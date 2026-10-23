#!/usr/bin/env python3
"""
출처 확인 스크립트 (검증 담당 보조 도구)

담당 결과 파일(.md)에 적힌 URL을 실제로 열어 보고, 특허번호와 링크가 맞는지,
인용문이 출처 페이지에 실제로 있는지를 기계적으로 먼저 걸러 준다.
검증 담당(verifier)은 이 결과를 읽고 "불충분" 판정을 빠르게 내린 뒤,
사람의 눈이 필요한 주장만 직접 연다.

표준 라이브러리만 쓴다. API 키 없음.
아이디어 출처: 199-biotechnologies/claude-deep-research-skill 의 verify_citations.py (MIT).
우리 결과 양식(한국어 표·인라인 URL)에 맞게 새로 썼다.

사용:
    python -I tools/출처확인.py runs/2026-10-23-bathroom/
    python -I tools/출처확인.py runs/.../10-기존해법.md --out runs/.../29-출처확인.md
    python -I tools/출처확인.py <경로> --strict      # 닫힌 출처가 하나라도 있으면 종료코드 1

판정:
    열림        HTTP 200대, 로그인·차단 흔적 없음
    닫힘        404/410/5xx, 연결 실패, 시간 초과
    차단 의심   200이지만 제목·본문 첫머리가 로그인·캡차·접근 거부 페이지로 보임
    중복        같은 글이 다른 URL로 두 번 이상 나옴 (독립 출처 수를 셀 때 1개로 센다)
"""

import argparse
import html
import re
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from urllib import error, request
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

# ----------------------------------------------------------------------------
# 설정
# ----------------------------------------------------------------------------

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
READ_LIMIT = 300_000  # 바이트. 제목과 본문 첫머리만 보면 된다
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "igshid", "ref", "ref_", "spm", "sc_", "th", "psc",
}
BLOCK_HINTS = [
    "로그인", "login", "sign in", "signin", "captcha", "robot", "are you a human",
    "access denied", "접근이 거부", "접근 거부", "forbidden", "unusual traffic",
    "비정상적인 접근", "보안 문자", "verify you are", "just a moment",
    "enable javascript", "page not found", "페이지를 찾을 수 없",
    "존재하지 않는", "삭제된 게시물", "삭제되었", "일시적으로 이용",
]

URL_RE = re.compile(r"https?://[^\s<>()\[\]\"'`|]+")
MD_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
QUOTE_RE = re.compile(r"[\"“]([^\"”]{8,200})[\"”]")
# 특허번호: KR/US/WO/EP/JP/CN + 숫자(+ 문자 코드), KIPRIS 출원번호(10-2020-0012345 또는 1020200012345), 등록번호 10-1234567
PATENT_RE = re.compile(
    r"\b(?:KR|US|WO|EP|JP|CN)\s?\d{4,}(?:\s?[A-Z]\d?)?\b|"
    r"\b(?:10|20|30)-\d{4}-\d{7}\b|\b(?:10|20)\d{11}\b|\b10-\d{7}\b"
)
PATENT_LINK_HOSTS = ("patents.google.com", "kipris.or.kr", "espacenet.com", "doi.org")


# ----------------------------------------------------------------------------
# URL 수집
# ----------------------------------------------------------------------------

def strip_trailing_punct(url: str) -> str:
    while url and url[-1] in ".,;:!?)]}>'\"”’":
        url = url[:-1]
    return url


def normalize(url: str) -> str:
    """추적 파라미터·조각·끝 슬래시를 떼어 '같은 글'을 알아보기 위한 키."""
    parts = urlsplit(url)
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS and not k.lower().startswith("utm_")
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, urlencode(query), ""))


def collect(paths):
    """파일 목록 → [(파일, 줄번호, 줄 텍스트, url)]"""
    files = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files.extend(sorted(p.glob("*.md")))
        elif p.is_file():
            files.append(p)
    # 이 스크립트가 만든 결과 파일과 검증 결과는 다시 읽지 않는다 (URL이 잘려 있어 가짜 404가 난다)
    files = [f for f in files if "출처확인" not in f.name and not f.name.startswith("30-")]
    rows = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = f.read_text(encoding="cp949", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            seen_in_line = set()
            for m in URL_RE.finditer(line):
                url = strip_trailing_punct(m.group(0))
                if url in seen_in_line or "…" in url:
                    continue
                seen_in_line.add(url)
                rows.append((f, n, line, url))
    return files, rows


# ----------------------------------------------------------------------------
# 페이지 열기
# ----------------------------------------------------------------------------

def decode_body(raw: bytes, content_type: str) -> str:
    charset = None
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if m:
        charset = m.group(1)
    if not charset:
        m = re.search(rb"charset=[\"']?([\w-]+)", raw[:4096], re.I)
        if m:
            charset = m.group(1).decode("ascii", "ignore")
    for enc in [charset, "utf-8", "cp949", "euc-kr", "latin-1"]:
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def page_text(body: str) -> str:
    body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", body)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    body = html.unescape(body)
    return re.sub(r"\s+", " ", body).strip()


def to_ascii_url(url: str) -> str:
    """한글 등 비ASCII 경로·쿼리를 퍼센트 인코딩하고 호스트는 IDNA로 바꾼다."""
    parts = urlsplit(url)
    try:
        host = parts.netloc.encode("idna").decode("ascii")
    except Exception:
        host = parts.netloc
    path = quote(parts.path, safe="/%:@&=+$,;~!*'()")
    query = quote(parts.query, safe="=&%:@+$,;~!*'()/?")
    return urlunsplit((parts.scheme, host, path, query, ""))


def fetch(url: str, timeout: float):
    """→ dict(status, final_url, title, text, error)"""
    result = {"status": None, "final_url": url, "title": "", "text": "", "error": ""}
    req = request.Request(to_ascii_url(url), headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    })
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            result["status"] = resp.status
            result["final_url"] = resp.geturl()
            ctype = resp.headers.get("Content-Type", "")
            raw = resp.read(READ_LIMIT)
            if "html" in ctype or raw[:200].lstrip().lower().startswith((b"<!doctype", b"<html")):
                body = decode_body(raw, ctype)
                t = re.search(r"(?is)<title[^>]*>(.*?)</title>", body)
                result["title"] = html.unescape(re.sub(r"\s+", " ", t.group(1))).strip() if t else ""
                result["text"] = page_text(body)
            else:
                result["title"] = f"({ctype.split(';')[0] or '비HTML'})"
                result["text"] = ""
    except error.HTTPError as e:
        result["status"] = e.code
        result["error"] = f"HTTP {e.code}"
        try:
            raw = e.read(READ_LIMIT)
            body = decode_body(raw, e.headers.get("Content-Type", ""))
            t = re.search(r"(?is)<title[^>]*>(.*?)</title>", body)
            result["title"] = html.unescape(re.sub(r"\s+", " ", t.group(1))).strip() if t else ""
        except Exception:
            pass
    except error.URLError as e:
        reason = str(getattr(e, "reason", e))
        if "CERTIFICATE_VERIFY_FAILED" in reason or "SSL" in reason:
            result["error"] = "SSL 검증 실패 (사람이 브라우저로 열면 보일 수 있음)"
        else:
            result["error"] = f"연결 실패: {reason[:60]}"
    except socket.timeout:
        result["error"] = "시간 초과"
    except Exception as e:  # noqa: BLE001
        result["error"] = f"오류: {str(e)[:60]}"
    return result


def judge(res: dict) -> str:
    st = res["status"]
    if st is None:
        return "차단 의심" if res["error"].startswith("SSL") else "닫힘"
    if st >= 400:
        # 403/429는 봇 차단일 가능성이 높다. 사람이 열면 보일 수 있으니 따로 표시
        if st in (401, 403, 429, 503):
            return "차단 의심"
        return "닫힘"
    title = res["title"].lower()
    head = res["text"][:300].lower()
    if any(h in title for h in BLOCK_HINTS):
        return "차단 의심"
    if len(res["text"]) < 800 and any(h in head for h in BLOCK_HINTS):
        return "차단 의심"
    return "열림"


# ----------------------------------------------------------------------------
# 특허번호 · 인용문 대조
# ----------------------------------------------------------------------------

def compact(s: str) -> str:
    return re.sub(r"[\s\-/,.()\[\]]", "", s).upper()


def patent_checks(rows):
    """줄 안의 특허번호와 그 줄의 링크가 맞는지. 링크 없는 번호도 잡는다."""
    out = []
    seen_lines = set()
    for f, n, line, _ in rows:
        key = (str(f), n)
        if key in seen_lines:
            continue
        seen_lines.add(key)
        links = [strip_trailing_punct(m.group(0)) for m in URL_RE.finditer(line)]
        # 링크 안에 든 번호는 세지 않는다 (본문 번호와 링크 번호를 대조하는 게 목적)
        line_wo_urls = URL_RE.sub(" ", line)
        nums = []
        for m in PATENT_RE.finditer(line_wo_urls):
            if m.group(0) not in nums:
                nums.append(m.group(0))
        if not nums:
            continue
        plinks = [u for u in links if any(h in u for h in PATENT_LINK_HOSTS)]
        for num in nums:
            c = compact(num)
            if not plinks:
                out.append((f, n, num, "", "링크 없음 → 규칙상 출력 불가, 삭제 대상"))
                continue
            matched = [u for u in plinks if c[-7:] in compact(u) or c in compact(u)]
            if matched:
                out.append((f, n, num, matched[0], "번호가 링크에 포함됨"))
            else:
                out.append((f, n, num, plinks[0], "번호와 링크 불일치 → 링크를 열어 출원인·번호 재확인"))
    return out


def quote_checks(rows, fetched):
    """같은 줄에 인용문과 URL이 함께 있으면, 열린 페이지 본문에 그 인용문이 있는지 본다."""
    out = []
    done = set()
    for f, n, line, url in rows:
        for qm in QUOTE_RE.finditer(line):
            quote = qm.group(1).strip()
            key = (str(f), n, quote, url)
            if key in done:
                continue
            done.add(key)
            res = fetched.get(url)
            if not res or judge(res) != "열림" or not res["text"]:
                out.append((f, n, quote, url, "확인 불가(페이지 못 열음)"))
                continue
            hay = compact(res["text"])
            needle = compact(quote)
            if not needle:
                continue
            if needle in hay:
                verdict = "발견"
            else:
                # 앞 12자 또는 뒤 12자만 맞아도 '부분'
                if needle[:12] in hay or needle[-12:] in hay:
                    verdict = "부분 발견(표현 다를 수 있음)"
                else:
                    verdict = "미발견 → 담당이 요약을 인용으로 썼을 가능성"
            out.append((f, n, quote, url, verdict))
    return out


# ----------------------------------------------------------------------------
# 보고서
# ----------------------------------------------------------------------------

def short(s: str, n: int) -> str:
    s = s.replace("|", "／").replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def build_report(files, rows, fetched, dup_groups, patents, quotes, elapsed):
    uniq = {}
    for f, n, line, url in rows:
        uniq.setdefault(url, []).append((f, n))
    counts = {"열림": 0, "닫힘": 0, "차단 의심": 0}
    for url in uniq:
        counts[judge(fetched[url])] += 1

    L = []
    L.append(f"# 출처 확인 (자동)")
    L.append("")
    L.append(f"확인일 {date.today().isoformat()} · 파일 {len(files)}개 · URL {len(uniq)}개 "
             f"(열림 {counts['열림']} / 차단 의심 {counts['차단 의심']} / 닫힘 {counts['닫힘']}) · "
             f"같은 글 중복 {len(dup_groups)}묶음 · {elapsed:.0f}초")
    L.append("")
    L.append("이 표는 기계가 열어 본 결과다. '열림'이 그 주장을 뒷받침한다는 뜻은 아니다. "
             "검증 담당은 '닫힘'과 '차단 의심'을 먼저 '불충분'으로 처리하고, '열림'인 출처만 내용을 대조한다.")
    L.append("")
    L.append("## 1. URL별 결과")
    L.append("")
    L.append("| 판정 | HTTP | 파일:줄 | URL | 페이지 제목 | 비고 |")
    L.append("|---|---|---|---|---|---|")
    order = {"닫힘": 0, "차단 의심": 1, "열림": 2}
    for url, locs in sorted(uniq.items(), key=lambda kv: (order[judge(fetched[kv[0]])], kv[0])):
        res = fetched[url]
        where = ", ".join(f"{f.name}:{n}" for f, n in locs[:3]) + (" …" if len(locs) > 3 else "")
        note = res["error"]
        if res["final_url"] and normalize(res["final_url"]) != normalize(url):
            note = (note + " " if note else "") + f"→ 리다이렉트: {short(res['final_url'], 60)}"
        L.append(f"| {judge(res)} | {res['status'] or '-'} | {short(where, 40)} | {short(url, 70)} | "
                 f"{short(res['title'], 50)} | {short(note, 60)} |")
    L.append("")

    L.append("## 2. 같은 글이 여러 URL로 적힌 경우 (독립 출처 수에서 1개로 센다)")
    L.append("")
    if dup_groups:
        for key, urls in dup_groups.items():
            L.append(f"- {short(key, 80)}")
            for u in urls:
                L.append(f"  - {u}")
    else:
        L.append("- 없음")
    L.append("")

    L.append("## 3. 특허번호 ↔ 링크 대조")
    L.append("")
    if patents:
        L.append("| 파일:줄 | 번호 | 링크 | 판정 |")
        L.append("|---|---|---|---|")
        for f, n, num, link, verdict in patents:
            L.append(f"| {f.name}:{n} | {num} | {short(link, 60) or '-'} | {verdict} |")
    else:
        L.append("- 특허번호 패턴을 찾지 못함")
    L.append("")

    L.append("## 4. 인용문 ↔ 출처 본문 대조")
    L.append("")
    if quotes:
        L.append("| 파일:줄 | 인용 | 출처 | 판정 |")
        L.append("|---|---|---|---|")
        for f, n, quote, url, verdict in quotes:
            L.append(f"| {f.name}:{n} | {short(quote, 50)} | {short(url, 50)} | {verdict} |")
    else:
        L.append("- URL과 같은 줄에 있는 인용문을 찾지 못함")
    L.append("")

    L.append("## 5. 검증 담당이 할 일")
    L.append("")
    L.append("- '닫힘' URL이 붙은 주장: 판정 **불충분**. 다른 출처가 없으면 등급을 내린다.")
    L.append("- '차단 의심' URL: 사람이 브라우저로 열면 보일 수 있다. 검증 담당이 WebFetch로 한 번 더 시도하고, 그래도 안 되면 '불충분'.")
    L.append("- '중복' 묶음: 담당이 독립 출처 수를 셀 때 따로 셌는지 확인하고 등급을 다시 계산한다.")
    L.append("- 특허 '불일치'·'링크 없음': 그 특허 행 전체를 '불충분'. 규칙상 링크 없는 번호는 보고서에 못 들어간다.")
    L.append("- 인용 '미발견': 출처를 직접 열어 뜻이 같은 문장이 있는지 본다. 없으면 인용을 요약으로 바꾸거나 뺀다.")
    return "\n".join(L) + "\n"


# ----------------------------------------------------------------------------
# 메인
# ----------------------------------------------------------------------------

def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="담당 결과 파일의 출처 URL을 실제로 열어 확인한다.")
    ap.add_argument("paths", nargs="+", help=".md 파일 또는 폴더 (폴더면 안의 *.md 전부)")
    ap.add_argument("--out", help="결과를 저장할 .md 경로 (없으면 표준 출력)")
    ap.add_argument("--timeout", type=float, default=15.0, help="URL당 제한 시간(초), 기본 15")
    ap.add_argument("--workers", type=int, default=6, help="동시에 여는 수, 기본 6")
    ap.add_argument("--max", type=int, default=200, help="확인할 URL 상한, 기본 200")
    ap.add_argument("--strict", action="store_true", help="닫힘이 하나라도 있으면 종료코드 1")
    args = ap.parse_args()

    files, rows = collect(args.paths)
    if not rows:
        print("URL을 찾지 못했습니다.", file=sys.stderr)
        sys.exit(2)

    urls = []
    for _, _, _, u in rows:
        if u not in urls:
            urls.append(u)
    skipped = 0
    if len(urls) > args.max:
        skipped = len(urls) - args.max
        urls = urls[: args.max]

    # 같은 글 중복
    groups = {}
    for u in urls:
        groups.setdefault(normalize(u), []).append(u)
    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}

    print(f"URL {len(urls)}개 확인 중 (파일 {len(files)}개)…", file=sys.stderr)
    t0 = time.time()
    fetched = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for u, res in zip(urls, ex.map(lambda x: fetch(x, args.timeout), urls)):
            fetched[u] = res
    elapsed = time.time() - t0

    rows = [r for r in rows if r[3] in fetched]
    patents = patent_checks(rows)
    quotes = quote_checks(rows, fetched)
    report = build_report(files, rows, fetched, dup_groups, patents, quotes, elapsed)
    if skipped:
        report += f"\n> 상한({args.max})을 넘어 {skipped}개 URL은 확인하지 않았다.\n"

    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"저장: {args.out}", file=sys.stderr)
    else:
        print(report)

    closed = sum(1 for u in urls if judge(fetched[u]) == "닫힘")
    sys.exit(1 if (args.strict and closed) else 0)


if __name__ == "__main__":
    main()
