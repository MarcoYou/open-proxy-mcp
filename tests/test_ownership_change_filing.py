"""최대주주등소유주식변동신고서(I004) — 칸 머리 이름으로 읽고, 인적사항은 싣지 않는다.

실제 원문 셋(`tests/dart_responses/dart/change_filing_*.html`, STX 2026-04~10 접수). 네트워크 0.
260419~261018 의 위치 파서는 이 서식에서 보고일자를 주식수로, 총괄현황 머리 둘째 줄을 주주로,
개인 주주의 「생년월일」 칸 값을 보유주식 자리로 냈다(운영 STX 2026-10-18).
"""
import asyncio
import json
from pathlib import Path

import pytest

from open_proxy_mcp.dart.client import DartClientError
from open_proxy_mcp.services import ownership_structure as O
from open_proxy_mcp.services.doc_tables import compact, table_grids
from open_proxy_mcp.tools import ownership_structure as T

_FIX = Path(__file__).parent / "dart_responses" / "dart"
_FILES = {
    "20260430801870": "change_filing_officer_buy_20260430801870.html",
    "20260921800460": "change_filing_largest_change_swap_20260921800460.html",
    "20261013800450": "change_filing_largest_change_rights_20261013800450.html",
}


def _html(rn: str) -> str:
    return (_FIX / _FILES[rn]).read_text("utf-8")


def _parse(rn: str) -> dict:
    return O._parse_change_filing(_html(rn), rn, rn[:8])


def _profile_values(html: str) -> set[str]:
    """원문의 생년월일·사업자등록번호 칸 값(인적사항 줄은 이름 칸 다음, 총괄현황은 둘째 칸)."""
    found = set()
    for grid in table_grids(html):
        for row in grid:
            for i, cell in enumerate(row[:-1]):
                if compact(cell) in ("생년월일", "사업자등록번호"):
                    v = compact(row[i + 1])
                    if v and "생년월일" not in v and "사업자" not in v:
                        found.add(v)
    return found


def test_overview_reads_dates_and_totals_by_header():
    rep, = _parse("20260430801870")["reports"]
    ov = rep["overview"]
    assert ov["before"] == {"shares": 11_995_657, "pct": 38.67, "basis": "합계", "filed_on": "2023-12-22"}
    assert ov["after"] == {"shares": 11_995_757, "pct": 38.67, "basis": "합계", "filed_on": "2026-04-30"}
    assert ov["delta"] == {"shares": 100, "pct": 0.0, "basis": "합계"}


def test_person_name_comes_from_the_table_not_the_section_heading():
    rep, = _parse("20260430801870")["reports"]
    person, = rep["individual_changes"]
    assert person["name"] == "홍라정"
    assert person["relation"] == "발행회사 임원" and person["holder_kind"] == "국내(개인)"
    assert person["changes"] == [{"date": "2026-04-30", "reason": "장외매수(+)", "stock_type": "보통주식",
                                  "before": 0, "delta": 100, "after": 100, "note": "0"}]
    assert set(person) == {"name", "relation", "holder_kind", "changes"}       # 허용 목록 칸뿐


def test_total_table_skips_the_second_header_row_and_profile_columns():
    rep, = _parse("20260430801870")["reports"]
    assert [h["name"] for h in rep["total_holders"]] == ["에이피씨머큐리유한회사", "홍라정"]
    corp, person = rep["total_holders"]
    assert (corp["total_shares"], corp["total_pct"]) == (11_995_657, 38.67)
    assert (person["common_shares"], person["total_shares"], person["relation"]) == (100, 100, "발행회사 임원")
    assert set(person) == {"name", "relation", "holder_kind", "common_shares", "common_pct", "total_shares", "total_pct"}
    assert rep["total"]["total_shares"] == 11_995_757


@pytest.mark.parametrize("rn", sorted(_FILES))
def test_no_birth_date_or_business_number_reaches_the_payload_or_markdown(rn):
    html = _html(rn)
    secrets = _profile_values(html)
    assert secrets, "원문에 인적사항 칸이 있어야 이 시험이 뜻이 있다"
    filing = _parse(rn)
    blob = json.dumps(filing, ensure_ascii=False) + "\n".join(T._change_filing_lines(filing))
    flat = blob.replace(",", "")
    assert not [s for s in secrets if s in blob or s.replace("-", "") in flat]
    assert filing["profile_omitted"] is True and filing["status"] == "parsed"


def test_largest_holder_change_form_has_before_and_after_groups():
    filing = _parse("20261013800450")
    before, after = filing["reports"]
    assert (before["group"], after["group"]) == ("변경전 최대주주등", "변경후 최대주주등")
    assert before["overview"]["after"]["shares"] == 489_030
    assert before["individual_changes"][0]["changes"][0]["reason"] == "주식병합"
    assert before["individual_changes"][0]["changes"][0]["delta"] == -49_881_128
    # 변경후 최대주주는 직전 보고가 없다 — `-` 칸을 0 으로 메우지 않는다
    assert after["overview"]["before"] == {"shares": None, "pct": None, "basis": "합계", "filed_on": None}
    assert [(h["name"], h["total_pct"]) for h in after["total_holders"]] == [("범한산업 주식회사", 94.89)]


def test_group_without_individual_changes_keeps_its_totals():
    """희석으로 비율만 줄어든 묶음은 개인별 변동 표가 없다 — 다음 표를 변동 표로 잘못 붙이지 않는다."""
    before, after = _parse("20260921800460")["reports"]
    assert before["individual_changes"] == []
    assert (before["overview"]["before"]["pct"], before["overview"]["after"]["pct"]) == (38.67, 7.03)
    assert [h["name"] for h in before["total_holders"]] == ["에이피씨머큐리 유한회사", "홍라정"]
    assert after["individual_changes"][0]["name"] == "한국산업은행"
    assert after["total"]["total_shares"] == 50_370_158


def test_markdown_lines_for_the_change_form():
    filing = _parse("20261013800450") | {"report_nm": "최대주주등소유주식변동신고서(최대주주변경시)"}
    lines = T._change_filing_lines(filing)
    assert lines[0] == "\n### 20261013 (20261013800450) 최대주주등소유주식변동신고서(최대주주변경시)"
    assert "\n#### [변경전 최대주주등]" in lines and "\n#### [변경후 최대주주등]" in lines
    assert ("- 직전: 50,370,158주 (29.54%, 2026-09-21 보고) → 이번: 489,030주 (1.71%, 2026-10-13 보고)"
            " / 증감 -49,881,128주 (-27.83%p)") in lines
    assert "- 직전: 직전 보고 없음 → 이번: 27,200,000주 (94.89%, 2026-10-13 보고) / 증감 +27,200,000주 (+94.89%p)" in lines
    assert "| 범한산업 주식회사 | 본인 | 국내(법인) | 27,200,000 | 94.89% | 27,200,000 | 94.89% |" in lines
    assert not any("사업자등록번호 |" in ln or "| 생년월일 |" in ln for ln in lines)


def test_unrecognized_form_says_so_instead_of_zeros():
    html = "<p>1. 발행회사 정보</p><table><tr><td>회사명</td><td>가</td></tr></table>" \
           "<p>3. 다른 표</p><table><tr><td>갑</td><td>을</td></tr><tr><td>1</td><td>2</td></tr></table>"
    filing = O._parse_change_filing(html, "20260101800001", "20260101")
    assert filing["status"] == "unrecognized" and filing["reports"] == []
    assert filing["unread"] == [{"table": 2, "section": "3. 다른 표"}]
    lines = T._change_filing_lines(filing)
    assert lines[1].startswith("- **못 읽음** — 「보고의 개요」 표") and "20260101800001" in lines[1]


def test_unknown_table_inside_a_report_is_located_not_guessed():
    html = _html("20260430801870").replace("변경원인", "사유")     # 개인별 변동 표 머리를 못 알아보게
    filing = O._parse_change_filing(html, "20260430801870", "20260430")
    assert filing["status"] == "partial"
    assert filing["unread"] == [{"table": 4, "section": "4. 개인별 세부변동사항"}]
    assert filing["reports"][0]["individual_changes"] == []
    assert any(ln.startswith("- **못 읽음**: 원문 `20260430801870` 표 4 (「4. 개인별 세부변동사항」)")
               for ln in T._change_filing_lines(filing))


@pytest.mark.parametrize("text,as_float,want", [
    ("11,995,657", False, 11_995_657), ("-49,881,128", False, -49_881_128), ("(1,000)", False, -1000),
    ("38.67", True, 38.67), ("-", False, None), ("", False, None), ("781-87-00911", False, None),
    ("2026-09-21", False, None), ("해당없음", False, None),
])
def test_cell_num_never_turns_text_into_a_number(text, as_float, want):
    assert O._cell_num(text, as_float=as_float) == want


class _Client:
    def __init__(self, docs, kind=None):
        self.docs, self.kind, self.seen = docs, kind or {}, []

    async def search_filings(self, **kw):
        assert kw["pblntf_detail_ty"] == "I004" and kw["corp_code"] == "00126937"
        return {"list": [{"rcept_no": rn, "rcept_dt": rn[:8], "report_nm": "최대주주등소유주식변동신고서"}
                         for rn in ("20260430801870", "20260921800460")]}

    async def get_document_cached(self, rn):
        self.seen.append(("dart", rn))
        return {"html": self.docs.get(rn, "")}

    async def kind_fetch_document(self, acptno):
        self.seen.append(("kind", acptno))
        if acptno not in self.kind:
            raise DartClientError("013", "없음")
        return self.kind[acptno]


def test_fetch_reads_dart_first_and_falls_back_to_kind_with_the_same_parser():
    from datetime import date
    client = _Client({"20260430801870": _html("20260430801870")},
                     kind={"20260921000460": _html("20260921800460")})
    filings, warnings = asyncio.run(O._fetch_change_filings("00126937", date(2026, 1, 1), date(2026, 10, 18), client))
    assert [(f["rcept_no"], f["source"], f["status"]) for f in filings] == [
        ("20260430801870", "dart_api", "parsed"), ("20260921800460", "kind_scraping", "parsed")]
    assert ("kind", "20260921000460") in client.seen and warnings == []


def test_fetch_warns_with_the_table_location_when_partly_unread():
    from datetime import date
    broken = _html("20260430801870").replace("변경원인", "사유")
    client = _Client({"20260430801870": broken, "20260921800460": _html("20260921800460")})
    _filings, warnings = asyncio.run(O._fetch_change_filings("00126937", date(2026, 1, 1), date(2026, 10, 18), client))
    assert warnings == ["변동신고서 20260430801870 일부를 못 읽음 (표 4) — 원문 확인 (0 으로 메우지 않았다)"]
