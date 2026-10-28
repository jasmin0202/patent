"""
tools/출처확인.py 단위·통합 테스트. 네트워크를 쓰지 않는다.

실행:
    python -I -m unittest discover -s tests -v
"""

import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

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

    # --- 리뷰 지적 2: 뒷자리 7글자만 같아도 통과하던 문제 ---
    def test_same_tail_different_country_is_mismatch(self):
        line = "| KR101234567B1 | ... | https://patents.google.com/patent/US991234567B1/en |"
        out = mod.patent_checks(self.rows(line))
        self.assertIn("불일치", out[0][4])

    def test_same_tail_longer_number_is_mismatch(self):
        line = "| KR1234567 | ... | https://patents.google.com/patent/KR101234567B1/ko |"
        out = mod.patent_checks(self.rows(line))
        self.assertIn("불일치", out[0][4])

    def test_kind_code_may_be_omitted_in_link(self):
        line = "| US8234725B2 | Roth | https://patents.google.com/patent/US8234725 |"
        out = mod.patent_checks(self.rows(line))
        self.assertIn("포함", out[0][4])

    def test_kipris_number_must_be_whole_token(self):
        # 다른 출원번호 안에 부분 문자열로 들어 있어도 불일치
        line = "출원번호 2020260138772 (https://doi.org/10.8080/1020260138772)"
        out = mod.patent_checks(self.rows(line))
        self.assertIn("불일치", out[0][4])

    def test_accepts_three_tuples_from_collect(self):
        line = "US4944478A Sullivan (https://patents.google.com/patent/US4944478)"
        out = mod.patent_checks([(Path("10-기존해법.md"), 3, line)])
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
            files, rows, _ = mod.collect([d])
            self.assertEqual([f.name for f in files], ["10-기존해법.md"])
            self.assertEqual([r[3] for r in rows], ["https://a.com/1"])

    # --- 리뷰 지적 3: 상위 폴더를 주면 후보 폴더를 안 봤던 문제 ---
    def test_recurses_into_candidate_folders(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "01-불편-샤워.md").write_text("x https://a.com/1\n", encoding="utf-8")
            sub = d / "A-후보"
            sub.mkdir()
            (sub / "10-기존해법.md").write_text("y https://b.com/2\n", encoding="utf-8")
            (sub / "29-출처확인.md").write_text("z https://ignored.com/\n", encoding="utf-8")
            files, rows, _ = mod.collect([d])
            self.assertEqual(sorted(f.name for f in files), ["01-불편-샤워.md", "10-기존해법.md"])
            self.assertEqual(sorted(r[3] for r in rows), ["https://a.com/1", "https://b.com/2"])

    # --- 리뷰 지적 1: URL 없는 줄의 특허번호가 검사 대상에서 빠지던 문제 ---
    def test_patent_lines_include_lines_without_url(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "10-기존해법.md").write_text(
                "링크 없는 KR102345678B1 을 언급\n"
                "링크 있는 US4944478A (https://patents.google.com/patent/US4944478)\n"
                "특허 아님 https://a.com/1\n", encoding="utf-8")
            _, rows, patent_lines = mod.collect([d])
            self.assertEqual([n for _, n, _ in patent_lines], [1, 2])
            self.assertEqual(len(rows), 2)
            out = mod.patent_checks(patent_lines)
            verdicts = {num: v for _, _, num, _, v in out}
            self.assertIn("링크 없음", verdicts["KR102345678B1"])
            self.assertIn("포함", verdicts["US4944478A"])


class EndToEnd(unittest.TestCase):
    """실제 파일 입력 → main() → 보고서 파일. 네트워크는 fetch()를 가짜로 바꿔 끊는다."""

    FAKE = {
        "https://open.example.com/post": res(title="욕실 선반 후기", text="본문 " * 50 + "하루도 안지나서 떨어져버렸네요 " + "본문 " * 200),
        "https://gone.example.com/x": res(status=404, error="HTTP 404"),
        "https://patents.google.com/patent/KR102345678B1/ko": res(title="KR102345678B1", text="청구항 " * 300),
    }

    def fake_fetch(self, url, timeout):
        return self.FAKE.get(url, res(status=None, error="연결 실패: 가짜"))

    def test_folder_with_candidate_subfolder_to_report(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "01-불편-샤워.md").write_text(
                '- "하루도 안지나서 떨어져버렸네요" (https://open.example.com/post)\n'
                "- 죽은 링크 https://gone.example.com/x\n", encoding="utf-8")
            sub = d / "A-후보"
            sub.mkdir()
            (sub / "10-기존해법.md").write_text(
                "| KR102345678B1 | 등록 | https://patents.google.com/patent/KR102345678B1/ko |\n"
                "| KR999999999B1 | 링크 없이 적힌 번호 |\n"
                "| KR101234567B1 | 다른 특허 링크 | https://patents.google.com/patent/US991234567B1/en |\n",
                encoding="utf-8")
            out = d / "29-출처확인.md"
            argv = ["출처확인.py", str(d), "--out", str(out), "--workers", "1"]
            with mock.patch.object(mod, "fetch", self.fake_fetch), \
                 mock.patch.object(sys, "argv", argv), \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    mod.main()
            self.assertEqual(cm.exception.code, 0)
            report = out.read_text(encoding="utf-8")
            # 파일 2개(하위 폴더 포함), URL 4개 (US991234567B1 링크는 가짜 fetch에서 연결 실패 → 닫힘)
            self.assertIn("파일 2개 · URL 4개", report)
            self.assertIn("열림 2 / 차단 의심 0 / 닫힘 2", report)
            # 특허: 일치 1, 링크 없음 1, 불일치 1 — 하위 폴더 파일에서 잡힘
            self.assertIn("| 10-기존해법.md:1 | KR102345678B1 |", report)
            self.assertIn("| 10-기존해법.md:2 | KR999999999B1 | - | 링크 없음", report)
            self.assertIn("| 10-기존해법.md:3 | KR101234567B1 |", report)
            self.assertIn("불일치", report)
            # 인용문 대조
            self.assertIn("| 발견 |", report)

    def test_strict_exit_code_when_a_source_is_closed(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "10-기존해법.md").write_text("x https://gone.example.com/x\n", encoding="utf-8")
            argv = ["출처확인.py", str(d), "--strict", "--out", str(d / "29-출처확인.md")]
            with mock.patch.object(mod, "fetch", self.fake_fetch), \
                 mock.patch.object(sys, "argv", argv), \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    mod.main()
            self.assertEqual(cm.exception.code, 1)

    def test_patent_only_file_without_any_url_still_reports(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "12-보조-KIPRIS.md").write_text("출원번호 1020260138772 (링크 없음)\n", encoding="utf-8")
            out = d / "29-출처확인.md"
            argv = ["출처확인.py", str(d), "--out", str(out)]
            with mock.patch.object(mod, "fetch", self.fake_fetch), \
                 mock.patch.object(sys, "argv", argv), \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    mod.main()
            self.assertEqual(cm.exception.code, 0)
            self.assertIn("1020260138772 | - | 링크 없음", out.read_text(encoding="utf-8"))

    def test_cli_reads_nested_patent_only_file(self):
        """별도 프로세스에서 실제 CLI와 UTF-8 파일 입출력을 확인한다."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            candidate = root / "A-후보" / "보조"
            candidate.mkdir(parents=True)
            source = candidate / "12-특허.md"
            source.write_text("# 특허 조사\nKR102345678B1 링크 없음\n", encoding="utf-8")
            # 이전 결과는 재귀 수집에서도 다시 검사하지 않아야 한다.
            (candidate / "29-출처확인.md").write_text(
                "KR999999999B1\n", encoding="utf-8")
            (candidate / "30-검증.md").write_text(
                "US999999999B1\n", encoding="utf-8")
            out = root / "29-출처확인.md"
            result = subprocess.run(
                [sys.executable, "-I", str(ROOT / "tools" / "출처확인.py"),
                 str(root), "--out", str(out)],
                cwd=d, capture_output=True, encoding="utf-8", timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = out.read_text(encoding="utf-8")
            self.assertIn("파일 1개 · URL 0개", report)
            self.assertIn("| 12-특허.md:2 | KR102345678B1 | - | 링크 없음", report)
            self.assertNotIn("KR999999999B1", report)
            self.assertNotIn("US999999999B1", report)

    def test_url_limit_does_not_skip_patent_checks(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / "10-기존해법.md"
            source.write_text(
                "후기 https://open.example.com/post\n"
                "KR101234567B1 https://patents.google.com/patent/US991234567B1/en\n"
                "KR999999999B1 링크 없음\n", encoding="utf-8")
            out = root / "29-출처확인.md"
            argv = ["출처확인.py", str(source), "--max", "1", "--out", str(out)]
            with mock.patch.object(mod, "fetch", side_effect=self.fake_fetch) as fetch, \
                 mock.patch.object(sys, "argv", argv), \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    mod.main()
            self.assertEqual(cm.exception.code, 0)
            fetch.assert_called_once_with("https://open.example.com/post", 15.0)
            report = out.read_text(encoding="utf-8")
            patent_rows = [line for line in report.splitlines()
                           if line.startswith("| 10-기존해법.md:")]
            self.assertEqual(len(patent_rows), 2)
            self.assertIn("KR101234567B1", patent_rows[0])
            self.assertIn("불일치", patent_rows[0])
            self.assertIn("KR999999999B1 | - | 링크 없음", patent_rows[1])
            self.assertIn("1개 URL은 확인하지 않았다", report)


if __name__ == "__main__":
    unittest.main()
