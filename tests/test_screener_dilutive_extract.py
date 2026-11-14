"""증자·사채 카드의 값은 그 공시의 결정 행에서만 온다 (2026-11-15).

원장 표본 141건(사건이 있던 84건): 종전엔 규모·배정·희석이 0건 채워졌고, 64건은 다른 공시의 사건 이름이 붙었다.
"""
import asyncio

from open_proxy_mcp.dart.client import html_to_text
from open_proxy_mcp.services.screener import (_extract_dilutive, _fetch_detail, _is_subsidiary_matter, classify)

CB = {"type": "convertible_bond", "event_label": "전환사채발행결정", "rcept_no": "20260923000131",
      "total_issue_amount": "10,000,000,000", "issuance_method": "사모", "conversion": {"pct_of_total_shares": "8.12"}}
RIGHTS = {"type": "rights_offering", "event_label": "유상증자결정", "rcept_no": "20261027000191", "issuance_method": "제3자배정",
          "dilution_pct_approx": 5.5, "fund_purpose": {"facility": "-", "operating": "3,000,000,000", "debt_repayment": "2,000,000,000"}}
# 타임라인은 금액 칸이 없고, 다른 사건(자기사채 만기전취득)이 섞여 있다 — 종전엔 여기서 첫 행을 집었다.
TIMELINE = [{"type": "early_redemption", "event_label": "자기사채 만기전취득", "rcept_no": "20260831900139"},
            {"type": "convertible_bond", "event_label": "전환사채발행결정", "rcept_no": "20260923000131"}]


def _payload(**lists):
    return {"data": {"events_timeline": TIMELINE, **lists}}


def test_exact_receipt_reads_the_decision_row():
    got = _extract_dilutive(_payload(convertible_bond_events=[CB]), "20260923000131", {"subtype": "CB발행", "stage": "결정(예정)"})
    assert got["amount_won"] == 10_000_000_000 and got["allocation"] == "사모" and got["dilution_pct"] == 8.12
    assert got["matched_rcept_no"] is None


def test_original_filing_takes_the_only_later_decision_of_its_type_and_says_so():
    # DART 구조화 응답은 정정되면 최신 정정본의 접수번호만 남긴다 — 원 공시(9-21) 카드는 9-23 정정본의 값을 받고 그 접수번호를 밝힌다.
    got = _extract_dilutive(_payload(convertible_bond_events=[CB]), "20260921000401", {"subtype": "CB발행", "stage": "결정(예정)"})
    assert got["amount_won"] == 10_000_000_000 and got["matched_rcept_no"] == "20260923000131"


def test_rights_offering_amount_is_the_sum_of_fund_purposes():
    got = _extract_dilutive(_payload(rights_offering_events=[RIGHTS]), "20261027000191", {"subtype": "유상증자"})
    assert got["amount_won"] == 5_000_000_000 and got["dilution_pct"] == 5.5


def test_other_events_are_never_borrowed():
    hit = {"subtype": "유상증자", "stage": "결정(예정)"}
    assert _extract_dilutive(_payload(convertible_bond_events=[CB]), "20260921000401", hit) == {}       # 유형이 다르다
    assert _extract_dilutive(_payload(), "20260921000401", hit) == {}                                    # 타임라인뿐이다
    assert _extract_dilutive(_payload(rights_offering_events=[RIGHTS, {**RIGHTS, "rcept_no": "20261028000001"}]),
                             "20261026000393", hit) == {}                                                # 같은 유형이 둘
    assert _extract_dilutive(_payload(rights_offering_events=[RIGHTS]), "20261101000001", hit) == {}     # 이 공시보다 앞선 결정
    assert _extract_dilutive(_payload(rights_offering_events=[RIGHTS]), "20261026000393",
                             {"subtype": "유상증자", "stage": "철회/취소"}) == {}                          # 철회 공시


def test_subsidiary_guard_also_works_for_ledger_rows():
    # 원장 배치의 행은 subsidiary_matter 키가 없고 제목이 report_nm 에 있다.
    row = {"_detail_kind": "dividend", "rcept_no": "20261112900610", "filed_at": "2026-11-12", "corp_name": "휴온스글로벌",
           "report_nm": "현금ㆍ현물배당결정(자회사의 주요경영사항)              (분기배당)"}
    got = asyncio.run(_fetch_detail(row, {"calls": 0}))
    assert got["detail_status"] == "no_data" and got["fields"] == {} and "자회사" in got["note"]


def test_kosdaq_subsidiary_wording_and_combined_offering():
    assert _is_subsidiary_matter("유상증자결정(종속회사의주요경영사항)")
    assert _is_subsidiary_matter("주요사항보고서(유상증자결정)(자회사의 주요경영사항)")
    assert not _is_subsidiary_matter("주요사항보고서(유상증자결정)")
    assert classify("[기재정정]주요사항보고서(유무상증자결정)")[:2] == ("dilutive", "유무상증자")
    assert classify("주요사항보고서(무상증자결정)")[:2] == ("dilutive", "무상증자")
    assert classify("주요사항보고서(유상증자결정)")[:2] == ("dilutive", "유상증자")


def test_character_references_are_decoded_not_dropped():
    assert html_to_text("<p>KT&amp;G &lt;5% 미만&gt; 2020&#8211;2025&nbsp;끝&cr;</p>") == "KT&G <5% 미만> 2020–2025 끝"
    assert html_to_text("<td>A&#160;B&#x26;C</td>") == "A B&C"
