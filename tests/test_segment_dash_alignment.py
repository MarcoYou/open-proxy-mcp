# -*- coding: utf-8 -*-
"""세그먼트 표 dash placeholder 정렬 + 총계검산(직접/조정누적) 회귀 테스트.

260723 한화 FY2024(rcept 20250331003821) 실측 케이스: 값행 중간 '-'(조선업 열)에서
수집이 끊겨 9부문 중 앞 3부문만 값이 잡히던 부분추출 → dash=0.0 자리표시자로 정렬 유지.
network 0콜 — 전부 synthetic 문자열.
"""
from __future__ import annotations

from open_proxy_mcp.services.business_details import (
    OK,
    SegmentProfit,
    _collect_metric_row,
    _segment_confident,
    parse_segment_table,
)


# ── _collect_metric_row: dash placeholder ──

def test_collect_row_mid_dash_kept_as_zero_when_dash_zero() -> None:
    region = "매출액\n\n100\n\n-\n\n50\n\n다음라벨"
    assert _collect_metric_row(region, "매출액", dash_zero=True) == [100.0, 0.0, 50.0]


def test_collect_row_default_breaks_at_dash() -> None:
    # rnd/backlog/customers 경로(_find_row_values)의 종전 의미 보존: dash는 '값 없음'이지 0원이 아님
    region = "매출액\n\n100\n\n-\n\n50\n\n다음라벨"
    assert _collect_metric_row(region, "매출액") == [100.0]


def test_collect_row_all_dash_returns_empty() -> None:
    # 전부-dash 행(값 부재)이 전부-0 유령행으로 둔갑하면 안 됨
    region = "매출액\n\n-\n\n-\n\n-\n\n다음라벨"
    assert _collect_metric_row(region, "매출액", dash_zero=True) == []


def test_collect_row_percent_stream_disables_dash_zero() -> None:
    # 금액/비중 교차표(스트림에 % 셀 존재): 비중 열의 dash는 % 마커가 없어 금액 0.0으로
    # 오인될 수 있다(260723 리뷰 재현 — 열이 밀린 오답이 검산 인증까지 획득). 텍스트만으로
    # dash의 소속 열을 판별할 수 없으므로 dash_zero 자동 비활성 → 종전 break(보수 후퇴).
    # 부분수집은 총계검산 불가로 게이트가 후보 강등한다.
    region = "매출액\n\n100\n\n9.7%\n\n-\n\n50\n\n다음라벨"
    assert _collect_metric_row(region, "매출액", dash_zero=True) == [100.0]


def test_collect_row_percent_dash_pair_no_misalignment() -> None:
    # 리뷰어 재현(260723): 부문2가 (금액 dash, 비중 dash) 쌍 → 종전 코드는 0.0을 2개 주입해
    # 이후 열 전체가 한 칸씩 밀렸다. 현행: % 감지로 dash_zero 비활성 → 첫 dash에서 break.
    region = "매출액\n\n100\n\n66.7%\n\n-\n\n-\n\n50\n\n33.3%\n\n150\n\n다음라벨"
    vals = _collect_metric_row(region, "매출액", dash_zero=True)
    assert vals == [100.0]  # 0.0 오주입·오정렬 없음


def test_collect_row_percent_after_stream_end_keeps_dash_zero() -> None:
    # 사전 스캔은 스트림 종료 지점까지만 — 다음 표의 %는 이 행의 dash_zero에 영향 없음
    region = "매출액\n\n100\n\n-\n\n50\n\n다음라벨\n\n9.7%"
    assert _collect_metric_row(region, "매출액", dash_zero=True) == [100.0, 0.0, 50.0]


def test_collect_row_negative_paren_mixed_with_dash() -> None:
    region = "매출액\n\n100\n\n-\n\n(20)\n\n130\n\n다음라벨"
    assert _collect_metric_row(region, "매출액", dash_zero=True) == [100.0, 0.0, -20.0, 130.0]


# ── _segment_confident: 총계검산 (직접 / 조정누적) ──

def _sp(revs: list[float], excess: list[float] | None) -> SegmentProfit:
    names = ["화약제조업", "도소매업", "화학제조업"]
    sp = SegmentProfit(status=OK, source="body")
    sp.segments = [{"name": n, "revenue": v, "profit": None} for n, v in zip(names, revs)]
    if excess is not None:
        sp.adjustments = [{"revenue_excess": excess}]
    return sp


def test_confident_requires_total_column() -> None:
    # 총계열 부재 = 검산 불가 → confident 아님(부분추출이 통과하던 구멍 봉쇄)
    assert _segment_confident(_sp([100.0, 40.0, 60.0], None)) is False


def test_confident_direct_total_match() -> None:
    assert _segment_confident(_sp([100.0, 40.0, 60.0], [200.0])) is True


def test_confident_adjustment_then_total_cumulative_match() -> None:
    # 한화형: 부문합(220) + 연결조정(-20) = 합계(200) — 직접 매칭은 실패, 누적 매칭으로 성립
    assert _segment_confident(_sp([120.0, 40.0, 60.0], [-20.0, 200.0])) is True


def test_confident_no_arithmetic_identity_fails() -> None:
    assert _segment_confident(_sp([100.0, 40.0, 60.0], [-50.0, 300.0])) is False


# ── 260723 리뷰 회귀 고정: 오답-인증 채널 차단 (양수 흡수 금지) ──

def test_confident_rejects_positive_excess_absorption() -> None:
    # 리뷰어 재현: 헤더에서 부문명 1개 탈락 → 그 부문의 양수 매출(30)이 excess로 밀림.
    # 종전 누적검산은 30을 조정처럼 흡수해 130+30=160≈총계로 인증했다(오정렬 오답 인증).
    # 현행: 양수는 흡수 금지 — 총계 후보로만 비교 → 검산 실패 → 후보 강등.
    assert _segment_confident(_sp([100.0, 30.0], [30.0, 160.0])) is False


def test_confident_rejects_shifted_mapping_with_mixed_excess() -> None:
    # 리뷰어 재현 변형: 밀린 매핑 [10,20,31] + excess [40(놓친 부문), -6(조정), 95(합계)]
    # 종전: 61+40-6=95 성립 → 확신-오답. 현행: 40 흡수 금지 → 61-6=55≠95 → False.
    assert _segment_confident(_sp([10.0, 20.0, 31.0], [40.0, -6.0, 95.0])) is False


def test_confident_negative_absorption_capped_at_two() -> None:
    # 조정성(음수) 흡수는 최대 2회 — 그 이상 필요한 항등식은 불인정(과적합 방지)
    assert _segment_confident(_sp([100.0, 100.0, 100.0], [-10.0, -20.0, 270.0])) is True
    assert _segment_confident(_sp([100.0, 100.0, 100.0], [-10.0, -10.0, -10.0, 270.0])) is False


# ── parse_segment_table 통합: 한화형 미니 표 (부문3 + 중간 dash + 조정 + 합계) ──

def test_parse_table_mid_dash_restores_full_alignment() -> None:
    region = "\n".join([
        "사업부문별 재무정보", "",
        " (단위:백만원)", "",
        " 부문", "",
        " 화약제조업", "",
        " 도소매업", "",
        " 조선업", "",
        " 매출액", "",
        " 100", "",
        " -", "",       # 도소매업 열의 값 부재 placeholder
        " 50", "",
        " (20)", "",     # 연결조정
        " 130", "",      # 합계
        " 영업이익(손실)", "",
        " 10", "",
        " -", "",
        " 5", "",
        " (3)", "",
        " 12",
    ])
    sp = parse_segment_table("사업부문별 재무정보", region)
    assert sp.status == OK
    assert [s["name"] for s in sp.segments] == ["화약제조업", "도소매업", "조선업"]
    assert [s["revenue"] for s in sp.segments] == [100.0, 0.0, 50.0]
    assert [s["profit"] for s in sp.segments] == [10.0, 0.0, 5.0]
    # 조정열이 excess에 보존돼 누적검산 성립 → 결정적(OK+confident) 추출
    assert sp.adjustments[0]["revenue_excess"] == [-20.0, 130.0]
    assert _segment_confident(sp) is True
