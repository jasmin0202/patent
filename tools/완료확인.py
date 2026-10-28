#!/usr/bin/env python3
"""
실행 폴더 규칙 확인 (총괄 보조 도구)

runs/<날짜-주제>/ 폴더를 읽어 CLAUDE.md의 운영 규칙이 실제로 지켜졌는지 기계로 센다.
지침 준수에만 기대지 않도록, 사람이 보고서를 받기 전에 한 번 돌린다.

  1. 단계별 산출물이 다 있는가 (00-질문, 01-영역, 01-불편-*, 02-점수표, 후보 폴더의 10·11·20·21·29·30·40)
  2. 조사 횟수 상한을 넘겼는가 (담당 파일 머리글의 "웹검색 N회", "Aside N회", 후보당 12-보조 파일 수)
  3. 최종 보고서의 근거 목록·가정표가 채워졌고 각 행에 등급(확인됨/추정/미확인)이 붙었는가

표준 라이브러리만 쓴다.

사용:
    python -I tools/완료확인.py runs/2026-10-23-bathroom
    python -I tools/완료확인.py runs/2026-10-23-bathroom --strict   # 위반이 하나라도 있으면 종료코드 1
"""

import argparse
import re
import sys
from pathlib import Path

# CLAUDE.md "상한" 절의 숫자. 바꾸면 CLAUDE.md도 같이 바꾼다.
CAPS = {
    "stage1_search": 10,   # 1단계 영역당 웹검색
    "stage2_search": 20,   # 2단계 후보당 담당별 웹검색
    "digger_per_candidate": 4,  # 2단계 후보당 보조(source-digger) 호출
    "aside_per_candidate": 6,   # 2단계 후보당 Aside 작업
}

ROOT_REQUIRED = ["00-질문.md"]
STAGE1_REQUIRED = ["01-영역.md", "02-점수표.md"]
CANDIDATE_REQUIRED = ["10-기존해법.md", "11-왜지금.md", "20-디자인.md", "21-구현원가.md",
                      "29-출처확인.md", "30-검증.md", "40-최종보고서.md"]
# /discover 실행 (특허 지도 → 제품 대조 → 발상 → 검증 → 제안서)
DISCOVER_REQUIRED = ["50-특허지도.md", "51-제품대조.md", "60-제안.md",
                     "29-출처확인.md", "30-검증.md", "70-제안서.md"]
DISCOVER_PROPOSALS = 3
# 점수표의 세 등급 + 보고서에서 뺀 항목을 기록한 행("삭제"/"제외")도 등급이 붙은 것으로 본다
GRADE_RE = re.compile(r"확인됨|추정|미확인|삭제|제외")
CANDIDATE_DIR_RE = re.compile(r"^[A-Z]-")

# "웹검색 20회", "웹검색 20/20(상한)", "웹검색 최대 10회" 모두 첫 숫자를 사용량으로 읽는다
SEARCH_RE = re.compile(r"웹\s?검색\s*(?:최대\s*)?(\d+)\s*(?:회|/)")
# "KIPRIS 12회", "KIPRIS 호출 14/14", "KIPRIS 20/20"
KIPRIS_RE = re.compile(r"KIPRIS\s*(?:호출\s*)?(\d+)\s*(?:회|/)")
CAPS["kipris_practical"] = 10   # 실용 모드(기본) 기존 해법의 KIPRIS 호출
CAPS["kipris_patent"] = 20      # --특허 모드


def run_mode(run: Path) -> str:
    """00-질문.md 에 '모드: 특허' 또는 '--특허' 가 있으면 '특허', 아니면 '실용'."""
    q = run / "00-질문.md"
    if q.exists():
        t = head(q, 40)
        if re.search(r"모드\s*[:：]\s*특허|--특허", t):
            return "특허"
    return "실용"
ASIDE_RE = re.compile(r"Aside\s*(?:작업\s*)?(\d+)\s*회?")


def head(path: Path, n: int = 6) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="cp949", errors="replace")
    return "\n".join(text.splitlines()[:n])


def count_in_head(path: Path, rx: re.Pattern):
    """머리글에서 'N회'를 찾아 가장 큰 N을 돌려준다. 없으면 None."""
    nums = [int(m.group(1)) for m in rx.finditer(head(path))]
    return max(nums) if nums else None


def is_discover(run: Path) -> bool:
    return (run / "50-특허지도.md").exists() or (run / "70-제안서.md").exists()


def check_discover(run: Path):
    """/discover 실행: 산출물 유무, 담당별 상한, 제안서 구조. → (rows, problems, stats)"""
    problems = []
    for name in ROOT_REQUIRED + DISCOVER_REQUIRED:
        if not (run / name).exists():
            problems.append(f"없음: {name}")
    rows = []
    for name in ("50-특허지도.md", "51-제품대조.md"):
        f = run / name
        if not f.exists():
            continue
        n = count_in_head(f, SEARCH_RE)
        verdict = "미기재" if n is None else ("초과" if n > CAPS["stage2_search"] else "OK")
        rows.append((name, "웹검색", n, CAPS["stage2_search"], verdict))
        if verdict == "초과":
            problems.append(f"상한 초과: {name} 웹검색 {n}회 > {CAPS['stage2_search']}")
    f = run / "60-제안.md"
    if f.exists():
        n = count_in_head(f, SEARCH_RE)
        verdict = "미기재" if n is None else ("초과" if n > 0 else "OK")
        rows.append(("60-제안.md", "웹검색(발상은 0)", n, 0, verdict))
        if verdict == "초과":
            problems.append(f"상한 초과: 60-제안.md 발상 담당이 검색 {n}회 (0회여야 함)")
    diggers = sorted(run.glob("12-보조-*.md"))
    verdict = "초과" if len(diggers) > CAPS["digger_per_candidate"] else "OK"
    rows.append(("12-보조-*", "보조 호출", len(diggers), CAPS["digger_per_candidate"], verdict))
    if verdict == "초과":
        problems.append(f"상한 초과: 보조 {len(diggers)}회 > {CAPS['digger_per_candidate']}")
    stats = {"제안": 0, "미확인(발상)": 0, "근거행": 0, "등급없는행": 0}
    p = run / "70-제안서.md"
    if p.exists():
        text = p.read_text(encoding="utf-8")
        stats["제안"] = len(re.findall(r"(?m)^###\s*제안\s*\d", text))
        stats["미확인(발상)"] = text.count("미확인(발상)")
        evid = table_rows(section(text, "7."))
        stats["근거행"] = len(evid)
        stats["등급없는행"] = len([r for r in evid if not GRADE_RE.search(r)])
        if stats["제안"] != DISCOVER_PROPOSALS:
            problems.append(f"70-제안서: 제안이 {stats['제안']}개 (기준 {DISCOVER_PROPOSALS})")
        if stats["미확인(발상)"] == 0:
            problems.append("70-제안서: '미확인(발상)' 등급 표기가 없음")
        if not evid:
            problems.append("70-제안서: 근거 목록(7절) 표가 비어 있음")
        if stats["등급없는행"]:
            problems.append(f"70-제안서: 근거 목록 {stats['등급없는행']}행에 등급 없음")
    return rows, problems, stats


def check_files(run: Path):
    problems = []
    for name in ROOT_REQUIRED:
        if not (run / name).exists():
            problems.append(f"없음: {name}")
    # 1단계를 돌렸는지는 영역·점수표 파일로 판단한다. (2단계부터 시작한 실행도 보조로 01-불편-*.md 를 둘 수 있다)
    stage1 = (run / "01-영역.md").exists() or (run / "02-점수표.md").exists()
    if stage1:
        for name in STAGE1_REQUIRED:
            if not (run / name).exists():
                problems.append(f"없음: {name}")
    candidates = sorted(p for p in run.iterdir() if p.is_dir() and CANDIDATE_DIR_RE.match(p.name))
    # 후보 폴더가 없으면(구체적 아이디어로 2단계부터 시작한 실행) 실행 폴더 자체가 유일한 후보다
    if not candidates and any(run.glob("1[01]-*.md")):
        candidates = [run]
    for c in candidates:
        for name in CANDIDATE_REQUIRED:
            if not (c / name).exists():
                problems.append(f"없음: {c.name}/{name}")
    return stage1, candidates, problems


def check_caps(run: Path, stage1: bool, candidates):
    rows = []      # (파일, 항목, 값, 상한, 판정)
    problems = []
    if stage1:
        for f in sorted(run.glob("01-불편-*.md")):
            n = count_in_head(f, SEARCH_RE)
            verdict = "미기재" if n is None else ("초과" if n > CAPS["stage1_search"] else "OK")
            rows.append((f.name, "웹검색", n, CAPS["stage1_search"], verdict))
            if verdict == "초과":
                problems.append(f"상한 초과: {f.name} 웹검색 {n}회 > {CAPS['stage1_search']}")
    mode = run_mode(run)
    kipris_cap = CAPS["kipris_patent"] if mode == "특허" else CAPS["kipris_practical"]
    for c in candidates:
        for name in ("10-기존해법.md", "11-왜지금.md", "20-디자인.md", "21-구현원가.md"):
            f = c / name
            if not f.exists():
                continue
            n = count_in_head(f, SEARCH_RE)
            verdict = "미기재" if n is None else ("초과" if n > CAPS["stage2_search"] else "OK")
            rows.append((f"{c.name}/{name}", "웹검색", n, CAPS["stage2_search"], verdict))
            if verdict == "초과":
                problems.append(f"상한 초과: {c.name}/{name} 웹검색 {n}회 > {CAPS['stage2_search']}")
        f = c / "10-기존해법.md"
        if f.exists():
            n = count_in_head(f, KIPRIS_RE)
            verdict = "미기재" if n is None else ("초과" if n > kipris_cap else "OK")
            rows.append((f"{c.name}/10-기존해법.md", f"KIPRIS({mode} 모드)", n, kipris_cap, verdict))
            if verdict == "초과":
                problems.append(f"상한 초과: {c.name}/10-기존해법.md KIPRIS {n}회 > {kipris_cap} ({mode} 모드)")
        diggers = sorted(c.glob("12-보조-*.md"))
        verdict = "초과" if len(diggers) > CAPS["digger_per_candidate"] else "OK"
        rows.append((f"{c.name}/12-보조-*", "보조 호출", len(diggers), CAPS["digger_per_candidate"], verdict))
        if verdict == "초과":
            problems.append(f"상한 초과: {c.name} 보조 {len(diggers)}회 > {CAPS['digger_per_candidate']}")
        aside = 0
        for f in list(c.glob("1*.md")) + list(c.glob("2*.md")):
            n = count_in_head(f, ASIDE_RE)
            aside += n or 0
        verdict = "초과" if aside > CAPS["aside_per_candidate"] else "OK"
        rows.append((f"{c.name}/*", "Aside 작업(합)", aside, CAPS["aside_per_candidate"], verdict))
        if verdict == "초과":
            problems.append(f"상한 초과: {c.name} Aside {aside}회 > {CAPS['aside_per_candidate']}")
    return rows, problems


def table_rows(section_text: str):
    """마크다운 표의 데이터 행(머리글·구분선 제외)만 돌려준다."""
    out = []
    for line in section_text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        if re.match(r"^\|[\s\-:|]+\|$", s):
            continue
        out.append(s)
    return out[1:] if out else []  # 첫 행은 머리글


def section(text: str, heading_prefix: str) -> str:
    """'## 9.' 처럼 시작하는 절의 본문."""
    m = re.search(rf"(?ms)^##\s*{re.escape(heading_prefix)}.*?$(.*?)(?=^##\s|\Z)", text)
    return m.group(1) if m else ""


def check_report(path: Path):
    problems = []
    if not path.exists():
        return {"근거행": 0, "등급없는행": 0, "가정행": 0}, [f"없음: {path.parent.name}/{path.name}"]
    text = path.read_text(encoding="utf-8")
    evid = table_rows(section(text, "9."))
    no_grade = [r for r in evid if not GRADE_RE.search(r)]
    assumptions = table_rows(section(text, "6."))
    signal = table_rows(section(text, "신호등"))
    if not evid:
        problems.append(f"{path.parent.name}/40: 근거 목록(9절) 표가 비어 있음")
    if no_grade:
        problems.append(f"{path.parent.name}/40: 근거 목록 {len(no_grade)}행에 등급 없음")
    if not assumptions:
        problems.append(f"{path.parent.name}/40: 가정표(6절)가 비어 있음")
    if len(signal) < 5:
        problems.append(f"{path.parent.name}/40: 신호등 표가 5행 미만({len(signal)})")
    return {"근거행": len(evid), "등급없는행": len(no_grade), "가정행": len(assumptions), "신호등행": len(signal)}, problems


def build_report(run: Path, stage1, candidates, file_problems, cap_rows, cap_problems, report_stats, report_problems):
    L = [f"# 완료 확인 (자동): {run.name}", ""]
    L.append(f"1단계 산출물 {'있음' if stage1 else '없음(2단계부터 시작)'} · 후보 폴더 {len(candidates)}개 · "
             f"위반 {len(file_problems) + len(cap_problems) + len(report_problems)}건")
    L.append("")
    L.append("## 1. 산출물")
    L += [f"- {p}" for p in file_problems] or ["- 빠진 파일 없음"]
    L.append("")
    L.append("## 2. 조사 횟수 상한 (담당 파일 머리글 기준)")
    L.append("")
    L.append("| 파일 | 항목 | 값 | 상한 | 판정 |")
    L.append("|---|---|---|---|---|")
    for f, item, val, cap, verdict in cap_rows:
        L.append(f"| {f} | {item} | {'-' if val is None else val} | {cap} | {verdict} |")
    L.append("")
    L.append("'미기재'는 머리글에 횟수가 없다는 뜻이다. 담당 양식대로 '사용 횟수'를 적게 한다.")
    L.append("")
    L.append("## 3. 최종 보고서")
    L.append("")
    for c, stats in report_stats.items():
        L.append(f"- {c}: 근거 목록 {stats['근거행']}행(등급 없음 {stats['등급없는행']}), "
                 f"가정표 {stats['가정행']}행, 신호등 {stats.get('신호등행', 0)}행")
    L += [f"- 위반: {p}" for p in report_problems]
    L.append("")
    return "\n".join(L) + "\n"


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="실행 폴더가 CLAUDE.md 규칙을 지켰는지 기계로 센다.")
    ap.add_argument("run", help="runs/<날짜-주제> 폴더")
    ap.add_argument("--out", help="결과를 저장할 .md 경로 (없으면 표준 출력)")
    ap.add_argument("--strict", action="store_true", help="위반이 하나라도 있으면 종료코드 1")
    args = ap.parse_args()

    run = Path(args.run)
    if not run.is_dir():
        print(f"폴더가 아님: {run}", file=sys.stderr)
        sys.exit(2)
    if is_discover(run):
        rows, problems, stats = check_discover(run)
        L = [f"# 완료 확인 (자동, /discover): {run.name}", "",
             f"위반 {len(problems)}건 · 제안 {stats['제안']}개 · '미확인(발상)' 표기 {stats['미확인(발상)']}회 · "
             f"근거 목록 {stats['근거행']}행(등급 없음 {stats['등급없는행']})", "",
             "## 1. 산출물·상한·제안서"]
        L += [f"- {p}" for p in problems] or ["- 위반 없음"]
        L += ["", "| 파일 | 항목 | 값 | 상한 | 판정 |", "|---|---|---|---|---|"]
        for f, item, val, cap, verdict in rows:
            L.append(f"| {f} | {item} | {'-' if val is None else val} | {cap} | {verdict} |")
        report = "\n".join(L) + "\n"
        if args.out:
            Path(args.out).write_text(report, encoding="utf-8")
            print(f"저장: {args.out}", file=sys.stderr)
        else:
            print(report)
        sys.exit(1 if (args.strict and problems) else 0)
    stage1, candidates, file_problems = check_files(run)
    cap_rows, cap_problems = check_caps(run, stage1, candidates)
    report_stats, report_problems = {}, []
    for c in candidates:
        stats, probs = check_report(c / "40-최종보고서.md")
        report_stats[c.name] = stats
        report_problems += probs
    report = build_report(run, stage1, candidates, file_problems, cap_rows, cap_problems, report_stats, report_problems)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"저장: {args.out}", file=sys.stderr)
    else:
        print(report)
    violations = len(file_problems) + len(cap_problems) + len(report_problems)
    sys.exit(1 if (args.strict and violations) else 0)


if __name__ == "__main__":
    main()
