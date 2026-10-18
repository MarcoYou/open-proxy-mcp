"""최대주주변경 계열(거래소 I001) — 변경 사실·양수도·담보 계약 체결·해제를 원문 칸 그대로.

실제 원문 여섯(`tests/dart_responses/dart/control_*.html`, 2026-08~10 접수). 네트워크 0.
"""
import asyncio
from pathlib import Path

import pytest

from open_proxy_mcp.services import control_change as C
from open_proxy_mcp.services import screener as S

_FIX = Path(__file__).parent / "dart_responses" / "dart"


def _ev(name: str, report_nm: str) -> dict:
    rn = name.rsplit("_", 1)[1].split(".")[0]
    return C.event_from_document({"rcept_no": rn, "rcept_dt": rn[:8], "report_nm": report_nm},
                                 (_FIX / name).read_text("utf-8"))


@pytest.mark.parametrize("title,kind", [
    ("최대주주변경", "change"),
    ("최대주주변경(자율공시)", "change"),
    ("[기재정정]최대주주 변경을 수반하는 주식양수도 계약 체결", "transfer_contract"),
    ("최대주주 변경을 수반하는 주식 담보제공 계약 체결", "pledge_contract"),
    ("최대주주 변경을 수반하는 주식 담보제공 계약 해제ㆍ취소 등", "pledge_release"),
    ("최대주주 변경을 수반하는 주식 양수도 계약 해제ㆍ취소 등", "transfer_release"),
    ("최대주주등소유주식변동신고서(최대주주변경시)", None),     # 변동신고서(I004) — 다른 공시다
    ("주주총회소집결의", None),
])
def test_titles_map_to_the_five_forms(title, kind):
    assert C.classify(title) == kind


def test_change_reads_before_and_after_with_reason_and_funding():
    ev = _ev("control_change_rights_issue_20260827900868.html", "최대주주변경")
    f = ev["fields"]
    assert (f["before_holder"], f["before_pct"], f["after_holder"], f["after_pct"]) == (
        "김희자 외 2명", "12.83", "(주)에스에이치에너지알이백", "16.96")
    assert f["reason"].startswith("유상증자(제3자배정) 납입 완료")
    assert f["changed_on"] == "2026-08-27"
    assert ev["related"] == "2026-08-19 유상증자결정(제3자배정)"
    assert ev["holders"]["columns"][2:4] == ["변경전 주식수(주)", "변경전 지분율(%)"]
    assert ev["profile_omitted"]                      # 새 최대주주 기본정보 표(주소) — 싣지 않는다
    assert not any("주소" in c for t in ev.get("other_tables", []) for c in t["columns"])


def test_affiliate_transfer_keeps_the_same_stake_and_says_why():
    f = _ev("control_change_affiliate_20261001800285.html", "최대주주변경")["fields"]
    assert f["before_pct"] == f["after_pct"] == "63.43"
    assert f["purpose"] == "계열회사간 지분 구조 재편"


def test_transfer_contract_reads_parties_price_and_expected_holder():
    ev = _ev("control_transfer_contract_20260915900712.html", "최대주주 변경을 수반하는 주식양수도 계약 체결")
    f = ev["fields"]
    assert f["transferor"] == {"name": "오성첨단소재(주)외 1인", "relation": "변경전 최대주주 및 특수관계인"}
    assert f["transferee"]["name"] == "파마앤바이오 신기술조합"
    assert (f["shares"], f["price_per_share"], f["amount"]) == ("2,343,750", "32,000", "75,000,000,000")
    assert (f["expected_holder"], f["expected_pct"], f["expected_on"]) == ("파마앤바이오 신기술조합", "27.84", "2026-11-19")
    assert f["contract_on"] == "2026-09-15"


def test_pledge_contract_reads_stake_if_executed_and_omits_the_debtor_profile():
    ev = _ev("control_pledge_contract_20260904900280.html", "최대주주 변경을 수반하는 주식 담보제공 계약 체결")
    f = ev["fields"]
    assert (f["pledgor"], f["pledgor_pct_now"], f["pct_if_executed"]) == ("주식회사 솔루엠코스메틱", "30.55", "0.00")
    assert f["pledge_total"] == "28,000,000,000" and f["contract_on"] == "2026-09-04"
    assert ev["pledges"]["rows"][0][1] == "지아이밸류업제일차 주식회사"
    assert ev["profile_omitted"] and "other_tables" not in ev


def test_pledge_release_reads_released_amounts_and_the_before_after_comparison():
    ev = _ev("control_pledge_release_20260901900199.html", "최대주주 변경을 수반하는 주식 담보제공 계약 해제ㆍ취소 등")
    f = ev["fields"]
    assert (f["pledgor"], f["reason"], f["released_shares"], f["released_on"]) == (
        "최영권", "주식근질권설정 해지", "5,106,880", "2025-11-14")
    assert ev["comparison"][0] == {"item": "담보제공 계약 건수(건)", "before": "2", "after": "0"}


def test_transfer_release_reads_kind_reason_and_dates():
    f = _ev("control_transfer_release_20260923900724.html", "최대주주 변경을 수반하는 주식 양수도 계약 해제ㆍ취소 등")["fields"]
    assert f["release_kind"] == "해제(해지)" and f["reason"].startswith("주식매매계약서 제8조")
    assert (f["contract_on"], f["released_on"], f["amount"]) == ("2026-07-17", "2026-09-23", "156,971,904,000")
    assert f["transferee"]["name"] == "디씨케이인베스트먼트 주식회사"


# ── 회사 조회 · 스크리너 ─────────────────────────────────────────────

class _Client:
    def __init__(self, docs):
        self.docs, self.calls = docs, 0

    def api_call_snapshot(self):
        return self.calls

    async def get_document_cached(self, rn):
        self.calls += 1
        return {"html": self.docs[rn], "text": ""}


def test_fetch_control_events_keeps_only_the_five_forms_newest_first(monkeypatch):
    docs = {"20260901900199": (_FIX / "control_pledge_release_20260901900199.html").read_text("utf-8"),
            "20260904900280": (_FIX / "control_pledge_contract_20260904900280.html").read_text("utf-8")}

    async def search(**kw):
        assert kw["pblntf_detail_ty"] == "I001" and kw["last_reprt_at"] == "N"
        return [
            {"rcept_no": "20260901900199", "rcept_dt": "20260901",
             "report_nm": "최대주주 변경을 수반하는 주식 담보제공 계약 해제ㆍ취소 등"},
            {"rcept_no": "20260904900280", "rcept_dt": "20260904",
             "report_nm": "최대주주 변경을 수반하는 주식 담보제공 계약 체결"},
            {"rcept_no": "20260905000001", "rcept_dt": "20260905",
             "report_nm": "최대주주등소유주식변동신고서(최대주주변경시)"},
        ], [], None

    import open_proxy_mcp.services.filing_search as FS
    monkeypatch.setattr(FS, "search_filings_by_report_name", search)
    events, warnings, rest = asyncio.run(C.fetch_control_events("00000001", "20260101", "20261018", _Client(docs)))
    assert [e["kind"] for e in events] == ["pledge_contract", "pledge_release"]
    assert rest == 0 and warnings == []


def test_screener_opens_control_change_cards_by_receipt_number(monkeypatch):
    html = (_FIX / "control_transfer_contract_20260915900712.html").read_text("utf-8")
    client = _Client({"20260915900712": html})
    monkeypatch.setattr(C, "get_dart_client", lambda: client)
    monkeypatch.setattr(S, "get_dart_client", lambda: client)
    hit = {"_detail_kind": "control", "rcept_no": "20260915900712", "stock_code": "061250",
           "title": "최대주주 변경을 수반하는 주식양수도 계약 체결", "filed_at": "2026-09-15"}
    res = asyncio.run(S._fetch_detail(hit, {"calls": 0}))
    assert res["detail_status"] == "parsed"
    assert res["fields"]["holder_after"] == "파마앤바이오 신기술조합"
    assert res["fields"]["amount"] == "75,000,000,000"

    from open_proxy_mcp.tools import screener as T
    line = T._detail_line({"type": {"code": "control_change"}, "detail_fields": res["fields"]})
    assert line == "변경예정 파마앤바이오 신기술조합 27.84% · 대금 75,000,000,000원 · 2026-09-15"


def test_control_change_is_a_detail_type_now():
    t = S._BY_CODE["control_change"]
    assert t["detail_kind"] == "control" and "해제" in t["force_keywords"]


def test_ownership_markdown_renders_each_form():
    from open_proxy_mcp.tools import ownership_structure as O
    ev = _ev("control_change_rights_issue_20260827900868.html", "최대주주변경")
    lines = O._control_event_lines(ev)
    assert lines[1] == "- **김희자 외 2명 12.83%** → **(주)에스에이치에너지알이백 16.96%** (변경일 2026-08-27)"
    assert any("인적사항 표" in ln for ln in lines)
    ev = _ev("control_pledge_contract_20260904900280.html", "최대주주 변경을 수반하는 주식 담보제공 계약 체결")
    assert "담보권 전부 실행시 **0.00%**" in O._control_event_lines(ev)[1]
    err = O._control_event_lines({"status": "error", "error": "DART 014", "kind": "change",
                                  "kind_label": "최대주주 변경", "rcept_no": "x", "rcept_dt": "y"})
    assert "원문을 못 읽었다" in err[1]


def test_release_cards_show_the_release_date_not_the_original_contract_date():
    ev = _ev("control_transfer_release_20260923900724.html", "최대주주 변경을 수반하는 주식 양수도 계약 해제ㆍ취소 등")
    f = S._extract_control({"data": {"event": ev}}, ev["rcept_no"])
    assert f["on"] == "2026-09-23"                     # 해소일 — 원계약 체결일(2026-07-17)이 아니다
    ev = _ev("control_transfer_contract_20260915900712.html", "최대주주 변경을 수반하는 주식양수도 계약 체결")
    assert S._extract_control({"data": {"event": ev}}, ev["rcept_no"])["on"] == "2026-09-15"


def test_ownership_amendment_lines_keep_one_line_per_cell():
    from open_proxy_mcp.tools import ownership_structure as O
    ev = _ev("control_transfer_contract_20260915900712.html", "최대주주 변경을 수반하는 주식양수도 계약 체결")
    ev["amendment"] = {"reason": "예정일 변경", "rows": [
        {"item": "3. 변경예정 최대주주\n-변경 예정일자", "before": "2026-10-30", "after": "2026-12-30", "change": "changed"}]}
    lines = O._control_event_lines(ev)
    assert "  - 3. 변경예정 최대주주 / -변경 예정일자: 2026-10-30 → 2026-12-30" in lines
    assert not any("\n" in ln for ln in lines[1:])     # 첫 줄의 앞 줄바꿈은 절 머리 띄움(의도)
