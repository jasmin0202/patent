"""
tools/완료확인.py 테스트. 임시 실행 폴더를 만들어 규칙 위반을 세는지 본다. 네트워크 없음.
"""

import importlib.util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("wanryo", ROOT / "tools" / "완료확인.py")
mod = importlib.util.module_from_spec(SPEC)
sys.modules["wanryo"] = mod
SPEC.loader.exec_module(mod)

REPORT_OK = """# 최종 보고서: X

## 신호등
| 항목 | 판정 | 이유 한 줄 |
|---|---|---|
| 특허 여지 | 노랑 | a |
| 시장 틈 | 노랑 | b |
| 왜 지금 | 노랑 | c |
| 원가·가격 | 노랑 | d |
| 디자인 차별 | 노랑 | e |

## 6. 검증해야 할 것 (가정표)
| 가정 | 갈래 | 중요도 | 지금 근거 | 우선순위 |
|---|---|---|---|---|
| 사람들이 산다 | 가치 | 상 | 약 | 1 |

## 9. 근거 목록
| 번호 | 주장 | 출처 | 등급 | 검증 판정 |
|---|---|---|---|---|
| 1 | 가격 1,900원 | https://a.com | 확인됨 | 뒷받침됨 |
| 2 | 특허 존재 | https://b.com | 추정 | 부분 |
"""


def make_run(d: Path, *, search1=8, search2=20, diggers=2, aside=0, report=REPORT_OK, missing=()):
    (d / "00-질문.md").write_text("# 질문\n", encoding="utf-8")
    (d / "01-영역.md").write_text("# 영역\n", encoding="utf-8")
    (d / "02-점수표.md").write_text("# 점수표\n", encoding="utf-8")
    (d / "01-불편-샤워.md").write_text(f"# 불편\n조사일, 웹검색 {search1}회\n", encoding="utf-8")
    c = d / "A-후보"
    c.mkdir()
    (c / "10-기존해법.md").write_text(f"# 기존 해법\n조사일 2026-10-23, 웹검색 {search2}회, Aside {aside}회\n", encoding="utf-8")
    (c / "11-왜지금.md").write_text("# 왜 지금\n웹검색 20회(상한 소진)\n", encoding="utf-8")
    (c / "20-디자인.md").write_text("# 디자인\n웹검색 0회\n", encoding="utf-8")
    (c / "21-구현원가.md").write_text("# 구현원가\n웹검색 20회(상한 소진), Aside 0회\n", encoding="utf-8")
    for i in range(diggers):
        (c / f"12-보조-{i}.md").write_text("# 보조\n사용 횟수(검색 3 / 페이지 2 / Aside 0)\n", encoding="utf-8")
    (c / "29-출처확인.md").write_text("# 출처 확인\n", encoding="utf-8")
    (c / "30-검증.md").write_text("# 검증\n", encoding="utf-8")
    (c / "40-최종보고서.md").write_text(report, encoding="utf-8")
    for m in missing:
        (c / m).unlink()
    return c


class Files(unittest.TestCase):
    def test_complete_run_has_no_problems(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            make_run(d)
            stage1, cands, problems = mod.check_files(d)
            self.assertTrue(stage1)
            self.assertEqual([c.name for c in cands], ["A-후보"])
            self.assertEqual(problems, [])

    def test_missing_design_and_report_are_listed(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            make_run(d, missing=("20-디자인.md", "40-최종보고서.md"))
            _, _, problems = mod.check_files(d)
            self.assertEqual(problems, ["없음: A-후보/20-디자인.md", "없음: A-후보/40-최종보고서.md"])


class Caps(unittest.TestCase):
    def test_within_caps(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            make_run(d)
            stage1, cands, _ = mod.check_files(d)
            rows, problems = mod.check_caps(d, stage1, cands)
            self.assertEqual(problems, [])
            self.assertTrue(all(v in ("OK", "미기재") for *_, v in rows))

    def test_stage1_search_over_cap(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            make_run(d, search1=11)
            stage1, cands, _ = mod.check_files(d)
            _, problems = mod.check_caps(d, stage1, cands)
            self.assertEqual(problems, ["상한 초과: 01-불편-샤워.md 웹검색 11회 > 10"])

    def test_digger_count_over_cap(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            make_run(d, diggers=6)
            stage1, cands, _ = mod.check_files(d)
            _, problems = mod.check_caps(d, stage1, cands)
            self.assertIn("상한 초과: A-후보 보조 6회 > 4", problems)

    def test_aside_sum_over_cap(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            make_run(d, aside=7)
            stage1, cands, _ = mod.check_files(d)
            _, problems = mod.check_caps(d, stage1, cands)
            self.assertIn("상한 초과: A-후보 Aside 7회 > 6", problems)

    def test_header_without_count_is_marked_not_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            make_run(d)
            (d / "01-불편-샤워.md").write_text("# 불편\n횟수를 안 적음\n", encoding="utf-8")
            stage1, cands, _ = mod.check_files(d)
            rows, problems = mod.check_caps(d, stage1, cands)
            self.assertEqual(problems, [])
            row = [r for r in rows if r[0] == "01-불편-샤워.md"][0]
            self.assertEqual(row[4], "미기재")


class Report(unittest.TestCase):
    def test_good_report_passes(self):
        with tempfile.TemporaryDirectory() as d:
            c = make_run(Path(d))
            stats, problems = mod.check_report(c / "40-최종보고서.md")
            self.assertEqual(problems, [])
            self.assertEqual(stats["근거행"], 2)
            self.assertEqual(stats["가정행"], 1)
            self.assertEqual(stats["신호등행"], 5)

    def test_row_without_grade_is_flagged(self):
        bad = REPORT_OK.replace("| 추정 | 부분 |", "| ? | 부분 |")
        with tempfile.TemporaryDirectory() as d:
            c = make_run(Path(d), report=bad)
            stats, problems = mod.check_report(c / "40-최종보고서.md")
            self.assertEqual(stats["등급없는행"], 1)
            self.assertIn("근거 목록 1행에 등급 없음", problems[0])

    def test_empty_assumption_table_is_flagged(self):
        bad = REPORT_OK.replace("| 사람들이 산다 | 가치 | 상 | 약 | 1 |\n", "")
        with tempfile.TemporaryDirectory() as d:
            c = make_run(Path(d), report=bad)
            _, problems = mod.check_report(c / "40-최종보고서.md")
            self.assertTrue(any("가정표" in p for p in problems))


class EndToEnd(unittest.TestCase):
    def run_main(self, run: Path, *extra):
        argv = ["완료확인.py", str(run), "--out", str(run / "51-완료확인.md"), *extra]
        with mock.patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                mod.main()
        return cm.exception.code, (run / "51-완료확인.md").read_text(encoding="utf-8")

    def test_clean_run_exit_0_and_report(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            make_run(d)
            code, text = self.run_main(d, "--strict")
            self.assertEqual(code, 0)
            self.assertIn("위반 0건", text)
            self.assertIn("빠진 파일 없음", text)

    def test_violations_give_exit_1_in_strict_mode(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            make_run(d, search1=12, missing=("30-검증.md",))
            code, text = self.run_main(d, "--strict")
            self.assertEqual(code, 1)
            self.assertIn("없음: A-후보/30-검증.md", text)
            self.assertIn("| 01-불편-샤워.md | 웹검색 | 12 | 10 | 초과 |", text)


if __name__ == "__main__":
    unittest.main()
