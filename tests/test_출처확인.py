"""
tools/출처확인.py 단위 테스트. 네트워크를 쓰지 않는다.

실행:
    python -m unittest discover -s tests -v
"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("chulcheo", ROOT / "tools" / "출처확인.py")
mod = importlib.util.module_from_spec(SPEC)
sys.modules["chulcheo"] = mod
SPEC.loader.exec_module(mod)


def res(status=200, title="", text="", error="", final_url=""):
    return {"status": status, "final_url": final_url, "title": title, "text": text, "error": error}


class UrlNormalization(unittest.TestCase):
    def test_strip_trailing_punct(self):
        self.assertEqual(mod.strip_trailing_punct("https://a.com/x)."), "https://a.com/x")
        self.assertEqual(mod.strip_trailing_punct("https://a.com/x"), "https://a.com/x")

    def test_same_article_different_urls_share_a_key(self):
        a = mod.normalize("https://www.82cook.com/entiz/read.php?num=50435")
        b = mod.normalize("http://82cook.com/entiz/read.php?num=50435&utm_source=x#top")
        c = mod.normalize("https://m.82cook.com/entiz/read.php/?num=50435")
        self.assertEqual(a, b)
        self.assertEqual(a, c)

    def test_different_articles_do_not_collide(self):
        a = mod.normalize("https://82cook.com/entiz/read.php?num=1")
        b = mod.normalize("https://82cook.com/entiz/read.php?num=2")
        self.assertNotEqual(a, b)

    def test_korean_path_is_percent_encoded(self):
        out = mod.to_ascii_url("https://www.gqkorea.co.kr/2024/11/27/날파리-배수구")
        self.assertTrue(out.isascii())
        self.assertTrue(out.startswith("https://www.gqkorea.co.kr/2024/11/27/%EB%82%A0"))


class Judge(unittest.TestCase):
    def test_connection_failure_is_closed(self):
        self.assertEqual(mod.judge(res(status=None, error="연결 실패")), "닫힘")

    def test_ssl_failure_is_suspected_block_not_closed(self):
        self.assertEqual(mod.judge(res(status=None, error="SSL 검증 실패")), "차단 의심")

    def test_404_is_closed_but_403_is_suspected_block(self):
        self.assertEqual(mod.judge(res(status=404)), "닫힘")
        self.assertEqual(mod.judge(res(status=403)), "차단 의심")
        self.assertEqual(mod.judge(res(status=429)), "차단 의심")

    def test_login_page_title_is_suspected_block(self):
        self.assertEqual(mod.judge(res(title="로그인 - 네이버", text="x" * 5000)), "차단 의심")
        self.assertEqual(mod.judge(res(title="Just a moment...", text="")), "차단 의심")

    def test_normal_page_with_login_link_in_nav_is_open(self):
        text = "홈 로그인 회원가입 " + "본문 내용 " * 300
        self.assertEqual(mod.judge(res(title="욕실 코너 선반 뭘 쓰시나요", text=text)), "열림")


class PatentChecks(unittest.TestCase):
    def rows(self, line):
        return [(Path("10-기존해법.md"), 7, line, u) for u in mod.URL_RE.findall(line)] or \
               [(Path("10-기존해법.md"), 7, line, "")]

    def test_number_matching_link_passes(self):
        line = "| KR102345678B1 | 삼성 | 등록 | ... | https://patents.google.com/patent/KR102345678B1/ko |"
        out = mod.patent_checks(self.rows(line))
        self.assertEqual(len(out), 1)
        self.assertIn("포함", out[0][4])

    def test_number_without_link_is_flagged(self):
        line = "KR102345678B1 은 같은 구조다"
        out = mod.patent_checks(self.rows(line))
        self.assertEqual(len(out), 1)
        self.assertIn("링크 없음", out[0][4])

    def test_number_not_in_link_is_flagged(self):
        line = "| KR100000001B1 | ... | https://patents.google.com/patent/KR102345678B1/ko |"
        out = mod.patent_checks(self.rows(line))
        self.assertIn("불일치", out[0][4])

    def test_kipris_application_number_and_doi_link(self):
        line = "출원번호 1020260138772 (https://doi.org/10.8080/1020260138772)"
        out = mod.patent_checks(self.rows(line))
        self.assertEqual(len(out), 1)
        self.assertIn("포함", out[0][4])


class QuoteChecks(unittest.TestCase):
    URL = "https://example.com/post/1"

    def row(self, line):
        return [(Path("01-불편-샤워.md"), 3, line, self.URL)]

    def test_quote_found_in_page_text(self):
        line = '- 인용: "하루도 안지나서 혼자 떨어져버렸네요." (https://example.com/post/1)'
        fetched = {self.URL: res(text="리뷰: 하루도 안 지나서 혼자 떨어져 버렸네요. 다시는 안 삼")}
        out = mod.quote_checks(self.row(line), fetched)
        self.assertEqual(out[0][4], "발견")

    def test_quote_missing_is_flagged(self):
        line = '- 인용: "이 문장은 페이지에 없습니다 정말로" (https://example.com/post/1)'
        fetched = {self.URL: res(text="전혀 다른 내용의 글입니다 " * 20)}
        out = mod.quote_checks(self.row(line), fetched)
        self.assertTrue(out[0][4].startswith("미발견"))

    def test_unopened_page_is_unverifiable(self):
        line = '- 인용: "열리지 않는 페이지의 인용문입니다" (https://example.com/post/1)'
        fetched = {self.URL: res(status=404)}
        out = mod.quote_checks(self.row(line), fetched)
        self.assertTrue(out[0][4].startswith("확인 불가"))


class Collect(unittest.TestCase):
    def test_skips_own_output_and_truncated_urls(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "10-기존해법.md").write_text(
                "a https://a.com/1 b https://a.com/long/url… c\n", encoding="utf-8")
            (d / "29-출처확인.md").write_text("x https://ignored.com/\n", encoding="utf-8")
            (d / "30-검증.md").write_text("x https://ignored2.com/\n", encoding="utf-8")
            files, rows = mod.collect([d])
            self.assertEqual([f.name for f in files], ["10-기존해법.md"])
            self.assertEqual([r[3] for r in rows], ["https://a.com/1"])


if __name__ == "__main__":
    unittest.main()
