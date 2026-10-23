"""
tools/kipris_청구항.py 테스트. 네트워크·API 키 없이 XML 파싱과 번호 수집만 본다.
"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("kipris_claims", ROOT / "tools" / "kipris_청구항.py")
mod = importlib.util.module_from_spec(SPEC)
sys.modules["kipris_claims"] = mod
SPEC.loader.exec_module(mod)

SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<response><header><requestMsgID/><responseTime>2026-10-24</responseTime><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg><successYN>Y</successYN></header>
<body><item>
<biblioSummaryInfoArray><biblioSummaryInfo>
<applicationNumber>1020250013141</applicationNumber><applicationDate>2025.02.03</applicationDate>
<inventionTitle>리모컨 기능이 구비된 전자 기기용 액세서리</inventionTitle><inventionTitleEng>ACCESSORY</inventionTitleEng>
<openNumber>1020260121711</openNumber><openDate>2026.08.11</openDate>
<registerNumber/><registerDate/><registerStatus>공개</registerStatus><finalDisposal/><claimCount>10</claimCount>
</biblioSummaryInfo></biblioSummaryInfoArray>
<applicantInfoArray><applicantInfo><name>더다움 주식회사</name></applicantInfo></applicantInfoArray>
<inventorInfoArray><inventorInfo><name>홍길동</name></inventorInfo></inventorInfoArray>
<ipcInfoArray><ipcInfo><ipcNumber>H04M 1/72409</ipcNumber></ipcInfo></ipcInfoArray>
<abstractInfoArray><abstractInfo><astrtCont>요약문</astrtCont></abstractInfo></abstractInfoArray>
<claimInfoArray>
<claimInfo><claim>1. 자성을 통해 전자 기기의 외면에 일면이 탈부착되는 자성 유닛; 및 버튼이 구비된 컨트롤 유닛.</claim></claimInfo>
<claimInfo><claim>2. 제1항에 있어서, 링형 자성 프레임.</claim></claimInfo>
</claimInfoArray>
<legalStatusInfoArray><legalStatusInfo><receiptDate>2025.02.03</receiptDate><documentName>출원서</documentName><commonCodeName>수리</commonCodeName></legalStatusInfo></legalStatusInfoArray>
<imagePathInfo><path>http://plus.kipris.or.kr/kiprisplusws/fileToss.jsp?x</path><largePath>http://plus.kipris.or.kr/img.jpg</largePath></imagePathInfo>
</item></body></response>"""


class Parse(unittest.TestCase):
    def test_claims_applicant_status_are_extracted(self):
        d = mod.parse_detail(SAMPLE)
        self.assertEqual(d["successYN"], "Y")
        self.assertEqual(d["출원번호"], "1020250013141")
        self.assertEqual(d["출원인"], ["더다움 주식회사"])
        self.assertEqual(d["등록상태"], "공개")
        self.assertEqual(len(d["청구항"]), 2)
        self.assertTrue(d["청구항"][0].startswith("1. 자성을 통해"))
        self.assertEqual(d["IPC"], ["H04M 1/72409"])
        self.assertTrue(d["공보PDF"].startswith("http://plus.kipris.or.kr/"))

    def test_render_includes_claim_and_doi_link(self):
        d = mod.parse_detail(SAMPLE)
        md = mod.render(d, max_claims=1)
        self.assertIn("## 1020250013141 — 리모컨 기능이 구비된 전자 기기용 액세서리", md)
        self.assertIn("https://doi.org/10.8080/1020250013141", md)
        self.assertIn("> 1. 자성을 통해", md)
        self.assertNotIn("> 2. 제1항", md)  # max_claims=1
        self.assertIn("1/2개", md)

    def test_missing_item_gives_empty_fields_not_crash(self):
        d = mod.parse_detail("<response><header><successYN>N</successYN><resultCode>99</resultCode><resultMsg>no key</resultMsg></header><body/></response>")
        self.assertEqual(d["successYN"], "N")
        self.assertEqual(d["출원번호"], "")
        self.assertEqual(d["청구항"], [])


class CollectNumbers(unittest.TestCase):
    def test_collects_dashed_and_plain_application_numbers_once(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "10-기존해법.md"
            p.write_text("더다움 KR 10-2025-0013141 ... 누막 1020240177669 ... 다시 1020250013141\n"
                         "등록번호 10-2988010 (13자리 아님)\n", encoding="utf-8")
            self.assertEqual(mod.collect_from_file(p), ["1020250013141", "1020240177669"])

    def test_normalize(self):
        self.assertEqual(mod.normalize_appno("10-2025-0013141"), "1020250013141")


if __name__ == "__main__":
    unittest.main()
