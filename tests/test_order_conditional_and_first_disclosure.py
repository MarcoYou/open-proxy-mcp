"""수주 본문 — 조건부 계약, 공시유보 해제 정정, 자릿수 구분이 깨진 금액, 「회사와의 관계」 칸 (2026-11-15).

원장의 2026-08-10 이후 수주 공시 원문 1,347건으로 전후를 쟀다: 조건부 계약 16건, 정정 764건 중 바뀐 것 7건
(금액 처음 공개 6 · 깨진 금액 1), 관계 칸 702건에서 뒤 칸 문구가 빠졌다. 아래는 그 공시들의 뼈대다.
"""
from open_proxy_mcp.services.order_contracts import _correction_diff, _parse_order, _signal_summary

# 스트라드비젼 20261012900298 — 확정 0원, 전액 조건부.
CONDITIONAL = ("단일판매ㆍ공급계약체결 1. 판매ㆍ공급계약 내용 전방 카메라 비전 인지 소프트웨어 개발 및 공급 2. 계약내역 "
               "조건부 계약여부 해당 확정 계약금액 0 조건부 계약금액 3,214,080,000 계약금액 총액(원) 3,214,080,000 "
               "최근 매출액(원) 18,109,229,713 매출액 대비(%) 17.7 3. 계약상대방 Aptiv Global Operations Limited "
               "- 최근 매출액(원) 29,269,090,200,000 - 주요사업 차량용 소프트웨어 솔루션 판매 - 회사와의 관계 최대주주의 계열사 "
               "- 회사와 최근 3년간 동종계약 이행여부 해당 4. 판매ㆍ공급지역 글로벌 5. 계약기간 시작일 2026-10-08 종료일 2028-04-30")
# 예스티 20261022900576 — 공시유보가 풀려 금액이 처음 적힌 정정. 정정 후 칸의 총액은 회사가 「16,600,000,」로 잘못 적었다.
HOLD_RELEASED = ("정정신고(보고) 정정일자 2026-10-22 3. 정정사유 공시 유보 기한 종료에 따른 정보 공개 4. 정정사항 정정항목 정정전 정정후 "
                 "2. 계약내역 확정 계약금액 : - 계약금액 총액(원) : - 매출액 대비(%) : - 확정 계약금액 : 16,600,000,000 "
                 "계약금액 총액(원) : 16,600,000, 매출액 대비(%) : 16.58 9. 공시유보 관련내용 유보기한 : 2026-10-23 "
                 "- 단일판매ㆍ공급계약체결 1. 판매ㆍ공급계약 내용 반도체 제조장비 2. 계약내역 조건부 계약여부 미해당 "
                 "확정 계약금액 16,600,000,000 조건부 계약금액 - 계약금액 총액(원) 16,600,000,000 최근 매출액(원) 100,095,115,191 "
                 "매출액 대비(%) 16.58 3. 계약상대방 한국알박 - 최근 매출액(원) 429,450,393,289 - 주요사업 반도체 장비 "
                 "- 회사와의 관계 없음 - 회사와 최근 3년간 동종계약 이행여부 해당 4. 판매ㆍ공급지역 중국")
# NC 20260812800835 — 표 꼴: 정정 전 「-」, 정정 후 금액.
HOLD_RELEASED_TABLE = "정정전 정정후 2. 계약내역-계약금액(원) - 90,706,000,000 2. 계약내역-매출액대비(%) - 3.93 5. 계약기간-시작일 - 2023-01-31"
# HJ중공업 20261023800728 — 정정 전 금액을 회사가 「85,419,259,16」으로 적었다(자릿수 구분이 깨짐).
BROKEN_DIGITS = ("정정전 정정후 2. 계약내역 - 계약금액(원) 85,419,259,16 89,530,076,904 2. 계약내역 - 매출액대비(%) 4.78 5.01 "
                 "- 단일판매ㆍ공급계약 체결 1. 판매ㆍ공급계약 구분 공사수주")
# 영진약품 20260812800977 — 라벨을 하이픈으로 이은 머리글. 진짜 감액 정정이다.
REAL_CHANGE = ("정정전 정정후 2. 계약내역 - 계약금액(원) - 매출액대비(%) 99,463,400,000 42.34 64,180,775,000 27.32 "
               "5. 계약기간 - 시작일 - 종료일 2024-09-10 2034-09-10")


def test_conditional_contract_is_marked_with_both_amounts():
    got = _parse_order(CONDITIONAL)
    assert got["is_conditional"] is True
    assert got["confirmed_amount_won"] == 0 and got["conditional_amount_won"] == 3_214_080_000
    assert got["contract_amount_won"] == 3_214_080_000        # 원문 총액은 그대로 둔다
    summary = _signal_summary([{**got, "is_external": True}], [], "w")
    assert summary["conditional_count"] == 1 and summary["conditional_amount_won"] == 3_214_080_000
    assert summary["external_total_amount_won"] == 3_214_080_000


def test_ordinary_contract_has_no_conditional_marks():
    got = _parse_order(HOLD_RELEASED)
    assert "is_conditional" not in got and "conditional_amount_won" not in got
    assert got["contract_amount_won"] == 16_600_000_000


def test_amount_first_disclosed_is_not_a_change():
    for text in (HOLD_RELEASED, HOLD_RELEASED_TABLE):
        diff = _correction_diff(text)
        assert diff == {"amount_first_disclosed": True}, diff     # 「166억 → 1,660만 원, −99.9%」를 붙이지 않는다


def test_broken_digit_grouping_gives_no_amount_change():
    diff = _correction_diff(BROKEN_DIGITS)
    assert "amount_change_pct" not in diff                        # 종전: +948.1%
    assert diff["revenue_ratio_before_pct"] == 4.78 and diff["revenue_ratio_after_pct"] == 5.01


def test_real_amount_change_is_still_read():
    diff = _correction_diff(REAL_CHANGE)
    assert diff["amount_before_won"] == 99_463_400_000 and diff["amount_after_won"] == 64_180_775_000
    assert diff["amount_change_pct"] == -35.5


def test_relationship_stops_before_the_next_line_of_the_same_item():
    assert _parse_order(HOLD_RELEASED)["relationship"] == "없음"
    got = _parse_order(CONDITIONAL)
    assert got["relationship"] == "최대주주의 계열사" and got["is_external"] is False
