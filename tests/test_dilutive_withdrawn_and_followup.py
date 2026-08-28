"""철회된 유상증자를 「신주 0주」로 찍지 않는지 · 만기전취득이 타임라인에 서는지.

2026-08-28 실사용 지적 둘 —
A-2 하이퍼코퍼레이션 2026-08-27 유상증자 철회가 「신주 0주 / 희석 0.00%」로 나왔다.
E-14 자기전환사채 만기전취득이 `company` 목록엔 있는데 희석 타임라인엔 없었다.
"""
from __future__ import annotations

from open_proxy_mcp.services.dilution_followup import (
    parse_early_redemption,
    parse_early_redemption_exchange,
)
from open_proxy_mcp.services.dilutive_issuance import (
    _event_direction,
    _followup_headline,
    _merge_ro_plan_into_row,
    _normalize_rights_offering,
    _parse_ro_correction_header,
    _parse_rights_offering_document,
    _ro_terms_blank,
    _summary_headline,
)
from open_proxy_mcp.tools.dilutive_issuance import _render_rights_card


# 철회본은 구조화 응답이 전 항목 `-` 로 온다 (2026-08-27 20260827000830 실제 응답).
WITHDRAWN_ITEM = {
    "rcept_no": "20260827000830",
    "corp_cls": "K",
    "corp_name": "하이퍼코퍼레이션",
    "nstk_ostk_cnt": "-",
    "nstk_estk_cnt": "-",
    "fv_ps": "-",
    "bfic_tisstk_ostk": "-",
    "fdpp_op": "-",
    "ic_mthn": "주주배정후 실권주 일반공모",
    "ssl_at": "N",
}

LIVE_ITEM = {
    "rcept_no": "20260227008569",
    "nstk_ostk_cnt": "8,556,759",
    "bfic_tisstk_ostk": "13,335,216",
    "fv_ps": "500",
    "ic_mthn": "제3자배정증자",
    "ssl_at": "N",
}

# 원본 공시 본문 (20260804000340 발췌 — 앞머리 정정 표까지 포함해 앵커를 시험한다)
RO_DOCUMENT = """주요사항보고서(유상증자결정)
정 정 신 고 (보고)
2026년 08월 04일
1. 정정대상 공시서류 :
주요사항보고서(유상증자결정)
2. 정정대상 공시서류의 최초제출일 :
2026년 05월 13일
3. 정정사항
8. 신주배정기준일
2026년 08월 06일
2026년 08월 31일
유상증자 결정
1. 신주의 종류와 수
보통주식 (주)
7,700,000
기타주식 (주)
-
2. 1주당 액면가액 (원)
1,000
3. 증자전 발행주식총수 (주)
보통주식 (주)
10,945,987
기타주식 (주)
-
4. 자금조달의 목적
시설자금 (원)
-
영업양수자금 (원)
-
운영자금 (원)
20,443,500,000
채무상환자금 (원)
-
타법인 증권취득자금 (원)
-
기타자금 (원)
-
5. 증자방식
주주배정후 실권주 일반공모
6. 신주 발행가액
확정발행가
보통주식 (원)
-
기타주식 (원)
-
예정발행가
보통주식 (원)
2,655
확정예정일
2026년 10월 16일
12. 납입일
2026년 10월 29일
16. 신주의 상장예정일
2026년 11월 11일
19. 이사회결의일(결정일)
2026년 05월 13일
"""

WITHDRAWAL_DOCUMENT = """주요사항보고서(유상증자결정)
정 정 신 고 (보고)
2026년 08월 27일
1. 정정대상 공시서류 :
주요사항보고서(유상증자결정)
2. 정정대상 공시서류의 최초제출일 :
2026년 05월 13일
3. 정정사항
유상증자 결정
유상증자 철회
1. 신주의 종류와 수
보통주식 (주)
-
24. 기타 투자판단에 참고할 사항
당사는 2026년 05월 13일 최초 이사회 결의를 통해 유상증자를 추진하였으나 금번 유상증자 철회를 결정하였습니다.
"""


def test_blank_rights_offering_is_not_zero():
    """`-` 는 0 이 아니다 — 신주수·희석률이 None 으로 남아야 한다."""
    row = _normalize_rights_offering(WITHDRAWN_ITEM)
    assert row["new_shares_common"] is None
    assert row["existing_shares_common"] is None
    assert row["dilution_pct_approx"] is None
    assert row["values_missing"] is True
    assert _ro_terms_blank(row) is True


def test_live_rights_offering_still_parses():
    row = _normalize_rights_offering(LIVE_ITEM)
    assert row["new_shares_common"] == 8556759
    assert row["dilution_pct_approx"] == 64.17
    assert row["values_missing"] is False
    assert _ro_terms_blank(row) is False


def test_blank_headline_says_missing_not_zero():
    row = _normalize_rights_offering(WITHDRAWN_ITEM)
    head = _summary_headline(row)
    assert "0주" not in head
    assert "0.00%" not in head
    assert "미확인" in head


def test_withdrawal_header_and_plan_recovery():
    header = _parse_ro_correction_header(WITHDRAWAL_DOCUMENT)
    assert header["original_filed_on"] == "2026-05-13"
    assert header["is_withdrawal"] == "Y"

    parsed = _parse_rights_offering_document(RO_DOCUMENT)
    assert parsed["new_shares_common"] == "7,700,000"
    assert parsed["existing_shares_common"] == "10,945,987"
    assert parsed["planned_price_won"] == "2,655"
    assert parsed["fixed_price_won"] == ""  # 확정발행가는 `-` 였다
    assert parsed["issuance_method"] == "주주배정후 실권주 일반공모"
    assert parsed["planned_proceeds_won_derived"] == 20443500000

    row = _normalize_rights_offering(WITHDRAWN_ITEM)
    row["is_withdrawal"] = True
    _merge_ro_plan_into_row(
        row, parsed, {"rcept_no": "20260804000340", "rcept_dt": "20260804"})
    # 복원값은 **원안 자리에만** 들어간다 — 발행 물량 자리는 비어 있어야 한다.
    assert row["new_shares_common"] is None
    assert row["original_plan"]["new_shares_common"] == 7700000
    assert row["original_plan"]["dilution_pct_approx"] == 70.35
    assert row["original_plan"]["source_rcept_no"] == "20260804000340"
    assert "철회" in row["recovery_note"]

    head = _summary_headline(row)
    assert "원안 신주 7,700,000주" in head
    assert "철회" in head


def test_correction_table_labels_do_not_leak_into_body():
    """앞머리 정정 표의 `8. 신주배정기준일` 값이 본문 값으로 새면 안 된다."""
    parsed = _parse_rights_offering_document(RO_DOCUMENT)
    assert parsed["face_value_per_share"] == "1,000"
    assert parsed["board_decision_date"] == "2026년 05월 13일"


def test_withdrawn_card_warns_instead_of_showing_zero():
    row = _normalize_rights_offering(WITHDRAWN_ITEM)
    row["is_withdrawal"] = True
    row["original_filed_on"] = "2026-05-13"
    card = "\n".join(_render_rights_card(row))
    assert "0 주 증자가 아니다" in card
    assert "0주" not in card.replace("0 주 증자가 아니다", "")
    assert "0.00%" not in card


def test_event_direction_marks_withdrawal_and_reduction():
    row = _normalize_rights_offering(WITHDRAWN_ITEM)
    row["is_withdrawal"] = True
    assert _event_direction(row) == "철회 — 발행되지 않음"
    assert _event_direction({"type": "capital_reduction"}) == "주식수 감소"
    assert _event_direction({"type": "convertible_bond"}) == "희석 확대(잠재)"


# ── 만기전취득 (E-14) ────────────────────────────────────────────

EXCHANGE_EARLY_REDEMPTION = (
    "하이퍼코퍼레이션/전환사채(해외전환사채포함)발행후만기전사채취득 "
    "전환사채(해외전환사채) 14 회차 1. 만기전 취득 사채에 관한 사항 "
    "사채의 종류 무기명식 이권부 무보증 사모 전환사채 발행일자 2024-09-23 "
    "발행방법 국내발행 (사모) 주당 전환가액(원) 6,864 만기일 2027-09-23 "
    "2. 사채 취득금액 (통화단위) 9,343,105,881 KRW : South-Korean Won - "
    "취득한 사채의 권면(전자등록)총액 (통화단위) 9,000,000,000 KRW : South-Korean Won - "
    "기준환율 - - 취득일자 2025-12-29 "
    "3. 취득후 사채의 권면(전자등록)총액 (통화단위) 0 KRW : South-Korean Won "
    "4. 만기전 취득사유 및 향후 처리방법 - 취득사유 : 사채권자와의 협의에 따른 만기전 사채 취득 "
    "- 향후 처리방법 : 소각 또는 재매각 등 처리방법 결정 "
    "5. 취득자금의 원천 자기자금 6. 사채의 취득방법 장내매수 7. 기타 투자판단에 참고할 사항 -"
)


def test_exchange_form_early_redemption_parses():
    """거래소 서식은 라벨이 달라 금감원 서식 파서로는 안 읽힌다 — 폴백이 받아야 한다."""
    parsed = parse_early_redemption(EXCHANGE_EARLY_REDEMPTION)
    assert parsed["series"] == "14"
    assert parsed["acquired_face_won"] == 9000000000
    assert parsed["acquisition_amount_won"] == 9343105881
    assert parsed["decided_on"] == "2025-12-29"
    # 되사고 남은 잔액 0 은 의미 있는 값이다 — 지워지면 안 된다.
    assert parsed["remaining_face_won_after"] == 0
    assert parse_early_redemption_exchange(EXCHANGE_EARLY_REDEMPTION)["series"] == "14"


def test_unparsable_early_redemption_says_so():
    """서식 둘 다 안 맞으면 빈 dict 가 아니라 「못 읽었다」를 남긴다."""
    parsed = parse_early_redemption("전혀 다른 서식의 문서입니다. 숫자도 라벨도 없습니다.")
    assert parsed["unparsed"] is True
    assert parsed["summary_excerpt"]


def test_followup_headline_reports_acquisition_size():
    row = {
        "type": "early_redemption",
        "direction": "희석 축소",
        "details": {
            "series": "15",
            "acquired_face_won": 1500000000,
            "acquired_ratio_pct_derived": 7.5,
        },
    }
    head = _followup_headline(row)
    assert "15회차" in head
    assert "1,500,000,000원" in head
    assert "7.50%" in head


def test_followup_headline_admits_unread_document():
    assert "원문 미열람" in _followup_headline(
        {"type": "early_redemption", "report_nm": "자기전환사채만기전취득결정"})
    assert "읽지 못했다" in _followup_headline(
        {"type": "early_redemption", "parse_error": "HTTPError: 500"})


# 서식 둘 — 값이 다음 줄에 오는 것(위)과 라벨 뒤 `:` 같은 줄에 오는 것(아래, 고려아연형).
INLINE_LABEL_WITHDRAWAL = """주요사항보고서(유상증자결정)
고려아연(주)
정 정 신 고 (보고)
2024년 11월 14일
1. 정정대상 공시서류 : 주요사항보고서(유상증자결정)
2. 정정대상 공시서류의 최초제출일 : 2024년 10월 30일
3. 정정사항[정정2]
유상증자 철회
5. 증자방식
일반공모증자
"""

THIRD_PARTY_DOCUMENT = """유상증자 결정
1. 신주의 종류와 수
보통주식 (주)
3,732,650
2. 1주당 액면가액 (원)
5,000
3. 증자전 발행주식총수 (주)
보통주식 (주)
20,703,283
4. 자금조달의 목적
시설자금 (원)
135,052,488,000
채무상환자금 (원)
2,300,000,000,000
타법인 증권취득자금 (원)
65,823,012,000
5. 증자방식
일반공모증자
6. 신주 발행가액
보통주식 (원)
670,000
기타주식 (원)
-
"""


def test_inline_label_correction_header():
    """라벨과 값이 한 줄인 서식에서도 최초제출일·철회 여부를 읽어야 한다."""
    header = _parse_ro_correction_header(INLINE_LABEL_WITHDRAWAL)
    assert header["original_filed_on"] == "2024-10-30"
    assert header["is_withdrawal"] == "Y"


def test_third_party_layout_has_no_price_subrows():
    """제3자배정·일반공모 서식엔 확정/예정 구분이 없다 — 발행가액이 바로 붙는다."""
    parsed = _parse_rights_offering_document(THIRD_PARTY_DOCUMENT)
    assert parsed["new_shares_common"] == "3,732,650"
    assert parsed["fixed_price_won"] == "670,000"
    assert parsed["planned_proceeds_won_derived"] == 2500875500000
