#!/usr/bin/env python3
"""
KIPRIS Plus 상세 서지 조회 (청구항 전문·출원인·법적 상태·공보 PDF 경로)

MCP 서버(SilverQ/kipris-mcp)의 kipris_bibliography 는 "요약 서지"(getBibliographySumryInfoSearch)만 불러
청구항·출원인이 비어 돌아온다. 우리가 신청한 상품 "특허·실용 공개·등록공보"에는 "상세 서지"
(getBibliographyDetailInfoSearch)가 들어 있고 거기에 청구항 전문이 있다. 이 스크립트가 그걸 부른다.

표준 라이브러리만 쓴다. 키는 환경변수 KIPRIS_API_KEY 에서만 읽고 어디에도 적지 않는다.

사용:
    python -I tools/kipris_청구항.py 1020250013141 1020240177669
    python -I tools/kipris_청구항.py --from runs/.../10-기존해법.md --out runs/.../12-보조-KIPRIS청구항.md
    python -I tools/kipris_청구항.py 1020250013141 --claims 3      # 청구항 3개까지만

--from 은 파일 안의 KIPRIS 출원번호(10/20으로 시작하는 13자리, 또는 10-2025-0013141 꼴)를 전부 모은다.
한 번에 60건까지. 호출 사이에 1초 쉰다(무료 한도 월 1,000회).
"""

import argparse
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from urllib import error, parse, request

ENDPOINT = "http://plus.kipris.or.kr/kipo-api/kipi/patUtiModInfoSearchSevice/getBibliographyDetailInfoSearch"
APPNO_RE = re.compile(r"\b(?:10|20)-?(?:\d{4})-?(?:\d{7})\b")
MAX_PER_RUN = 60


def normalize_appno(s: str) -> str:
    return re.sub(r"\D", "", s)


def fetch_detail(appno: str, key: str, timeout: float = 30.0) -> str:
    url = ENDPOINT + "?" + parse.urlencode({"applicationNumber": appno, "ServiceKey": key})
    with request.urlopen(url, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def text(el, path: str) -> str:
    if el is None:
        return ""
    found = el.find(path)
    return (found.text or "").strip() if found is not None and found.text else ""


def parse_detail(xml_text: str) -> dict:
    """상세 서지 XML → dict. 없는 필드는 빈 문자열/빈 목록."""
    root = ET.fromstring(xml_text)
    out = {
        "resultCode": text(root, ".//header/resultCode"),
        "resultMsg": text(root, ".//header/resultMsg"),
        "successYN": text(root, ".//header/successYN"),
    }
    item = root.find(".//item")
    bib = item.find("biblioSummaryInfoArray/biblioSummaryInfo") if item is not None else None
    out.update({
        "출원번호": text(bib, "applicationNumber"),
        "출원일": text(bib, "applicationDate"),
        "발명명칭": text(bib, "inventionTitle"),
        "영문명칭": text(bib, "inventionTitleEng"),
        "공개번호": text(bib, "openNumber"),
        "공개일": text(bib, "openDate"),
        "등록번호": text(bib, "registerNumber"),
        "등록일": text(bib, "registerDate"),
        "등록상태": text(bib, "registerStatus"),
        "최종처분": text(bib, "finalDisposal"),
        "청구항수": text(bib, "claimCount"),
        "심사청구일": text(bib, "originalExaminationRequestDate"),
    })
    out["출원인"] = [text(a, "name") for a in (item.findall("applicantInfoArray/applicantInfo") if item is not None else [])]
    out["발명자"] = [text(a, "name") for a in (item.findall("inventorInfoArray/inventorInfo") if item is not None else [])]
    out["IPC"] = [text(a, "ipcNumber") for a in (item.findall("ipcInfoArray/ipcInfo") if item is not None else [])]
    out["요약"] = text(item, "abstractInfoArray/abstractInfo/astrtCont") if item is not None else ""
    out["청구항"] = [text(c, "claim") for c in (item.findall("claimInfoArray/claimInfo") if item is not None else [])]
    out["법적상태"] = [
        {"일자": text(s, "receiptDate"), "서류": text(s, "documentName"), "코드명": text(s, "commonCodeName")}
        for s in (item.findall("legalStatusInfoArray/legalStatusInfo") if item is not None else [])
    ]
    img = item.find("imagePathInfo") if item is not None else None
    out["공보PDF"] = text(img, "path")
    out["도면"] = text(img, "largePath")
    return out


def collect_from_file(path: Path):
    try:
        t = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        t = path.read_text(encoding="cp949", errors="replace")
    seen, nums = set(), []
    for m in APPNO_RE.finditer(t):
        n = normalize_appno(m.group(0))
        if len(n) == 13 and n not in seen:
            seen.add(n)
            nums.append(n)
    return nums


def render(d: dict, max_claims: int) -> str:
    L = []
    title = d["발명명칭"] or "(명칭 없음)"
    L.append(f"## {d['출원번호']} — {title}")
    L.append("")
    status = d["등록상태"] or d["최종처분"] or "미표기"
    L.append(f"- 출원인: {', '.join(x for x in d['출원인'] if x) or '미표기'} / 발명자: {', '.join(x for x in d['발명자'] if x) or '미표기'}")
    L.append(f"- 출원일 {d['출원일'] or '-'} · 공개 {d['공개번호'] or '-'}({d['공개일'] or '-'}) · 등록 {d['등록번호'] or '-'}({d['등록일'] or '-'}) · 상태 **{status}** · 청구항 {d['청구항수'] or len(d['청구항'])}개")
    L.append(f"- IPC: {', '.join(x for x in d['IPC'] if x) or '-'}")
    L.append(f"- 링크: https://doi.org/10.8080/{normalize_appno(d['출원번호'])}" + (f" · 공보 PDF: {d['공보PDF']}" if d["공보PDF"] else ""))
    if d["법적상태"]:
        last = d["법적상태"][-3:]
        L.append("- 최근 법적 상태: " + " / ".join(f"{s['일자']} {s['서류']}" for s in last if s["서류"]))
    if d["요약"]:
        L.append(f"- 요약: {d['요약'][:400]}{'…' if len(d['요약']) > 400 else ''}")
    L.append("")
    if d["청구항"]:
        L.append(f"### 청구항 (원문, {min(len(d['청구항']), max_claims)}/{len(d['청구항'])}개)")
        L.append("")
        for c in d["청구항"][:max_claims]:
            L.append(f"> {c}")
            L.append("")
    else:
        L.append("### 청구항: API 응답에 없음 (공개 전이거나 상품 범위 밖)")
        L.append("")
    return "\n".join(L)


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="KIPRIS Plus 상세 서지(청구항 전문·출원인·상태·PDF 경로) 조회")
    ap.add_argument("appnos", nargs="*", help="출원번호 (1020250013141 또는 10-2025-0013141)")
    ap.add_argument("--from", dest="from_file", help="이 .md 파일 안의 출원번호를 전부 모아 조회")
    ap.add_argument("--out", help="결과 .md 저장 경로 (없으면 표준 출력)")
    ap.add_argument("--claims", type=int, default=20, help="건당 출력할 청구항 수, 기본 20(전부에 가깝게)")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--sleep", type=float, default=1.0, help="호출 간격(초), 기본 1")
    args = ap.parse_args()

    key = os.environ.get("KIPRIS_API_KEY")
    if not key:
        print("환경변수 KIPRIS_API_KEY 가 없습니다. README의 KIPRIS 설치 절을 보세요.", file=sys.stderr)
        sys.exit(2)

    nums = [normalize_appno(a) for a in args.appnos]
    if args.from_file:
        nums += collect_from_file(Path(args.from_file))
    nums = [n for i, n in enumerate(nums) if n and n not in nums[:i]]
    if not nums:
        print("조회할 출원번호가 없습니다.", file=sys.stderr)
        sys.exit(2)
    if len(nums) > MAX_PER_RUN:
        print(f"한 번에 {MAX_PER_RUN}건까지. {len(nums) - MAX_PER_RUN}건은 건너뜁니다.", file=sys.stderr)
        nums = nums[:MAX_PER_RUN]

    L = [f"# KIPRIS 상세 서지 (청구항 원문) — 조회일 {date.today().isoformat()}, {len(nums)}건", "",
         "출처: KIPRIS Plus getBibliographyDetailInfoSearch (상품 \"특허·실용 공개·등록공보\"). "
         "청구항은 API가 돌려준 원문 그대로이며 법적 판단이 아니다. 상태는 조회일 기준.", ""]
    failed = 0
    for i, n in enumerate(nums):
        try:
            xml_text = fetch_detail(n, key, args.timeout)
            d = parse_detail(xml_text)
            if d["successYN"] != "Y" or not d["출원번호"]:
                failed += 1
                L.append(f"## {n} — 조회 실패 ({d['resultCode'] or '-'} {d['resultMsg'] or '응답에 서지 없음'})")
                L.append("")
            else:
                L.append(render(d, args.claims))
        except (error.URLError, error.HTTPError, ET.ParseError, OSError) as e:
            failed += 1
            L.append(f"## {n} — 조회 실패 ({str(e)[:80]})")
            L.append("")
        print(f"[{i + 1}/{len(nums)}] {n}", file=sys.stderr)
        if i < len(nums) - 1:
            time.sleep(args.sleep)
    L.append(f"조회 {len(nums)}건 중 실패 {failed}건.")
    report = "\n".join(L) + "\n"
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"저장: {args.out}", file=sys.stderr)
    else:
        print(report)
    sys.exit(1 if failed == len(nums) else 0)


if __name__ == "__main__":
    main()
