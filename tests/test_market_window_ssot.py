"""corp_code 없는 조회의 기간 상한 — **일수가 아니라 3역월**임을 실측으로 못 박는다. 네트워크 0.

배경(260909). 같은 DART 제약을 세 곳이 각자 다른 일수로 추측하고 있었다: client 85 ·
risk_events 90 · screener 92. 처음엔 92 를 「실측한 경계」로 굳혔는데, 그때 쓴 표본
(0609~0909)이 **92일이면서 동시에 정확히 3역월**이라 둘을 구분하지 못했다 — 교락이었다.

2월 시작으로 다시 재니 갈렸다. 아래 표는 `pblntf_detail_ty=I001` 로 직접 받은 status 다.
DART 의 거부 문구도 일수가 아니라 기간을 말한다: "corp_code가 없는 경우 검색기간은
3개월만 가능합니다".
"""
import datetime as dt
import inspect

from open_proxy_mcp.dart.client import (MARKET_WINDOW_MAX_MONTHS, market_window_end,
                                        market_window_start)
from open_proxy_mcp.services import risk_events, screener

D = dt.date

# (bgn, end, DART status 000 인가) — 260909 실측. 마지막 두 줄이 「전진 규칙」을 가른다.
MEASURED = [
    (D(2026, 2, 6),   D(2026, 5, 6),  True),   # 89일 = 3역월
    (D(2026, 2, 6),   D(2026, 5, 7),  False),  # 90일인데 거부 — 일수 규칙이 아니다
    (D(2026, 2, 20),  D(2026, 5, 20), True),   # 89일
    (D(2026, 2, 20),  D(2026, 5, 21), False),
    (D(2026, 6, 9),   D(2026, 9, 9),  True),   # 92일도 3역월이면 통과
    (D(2025, 12, 15), D(2026, 3, 15), True),   # 90일
    (D(2025, 12, 15), D(2026, 3, 16), False),
    (D(2025, 11, 30), D(2026, 2, 28), True),   # 말일 눌림(11/30 +3역월 = 2/28)
    (D(2025, 11, 30), D(2026, 3, 1),  False),
    (D(2026, 1, 31),  D(2026, 4, 30), True),
    (D(2026, 1, 30),  D(2026, 4, 30), True),
    (D(2026, 1, 29),  D(2026, 4, 30), False),  # 91일 거부 — `end − 3역월` 이었다면 통과했을 자리
    (D(2026, 2, 28),  D(2026, 5, 28), True),
    (D(2026, 2, 28),  D(2026, 5, 31), False),  # 92일 거부 — 규칙은 **전진**이다
    (D(2026, 2, 27),  D(2026, 5, 31), False),
]


def test_the_helpers_reproduce_every_measured_status():
    for bgn, end, allowed in MEASURED:
        assert (market_window_end(bgn) >= end) is allowed, f"{bgn}~{end}"
        assert (market_window_start(end) <= bgn) is allowed, f"{bgn}~{end}"


def test_the_allowed_span_is_not_a_fixed_number_of_days():
    """고정 일수를 쓰면 안 되는 이유 그 자체 — 허용 폭이 시작일마다 다르다."""
    spans = {(market_window_end(D(2026, 1, 1) + dt.timedelta(days=i))
              - (D(2026, 1, 1) + dt.timedelta(days=i))).days for i in range(365)}
    assert spans == {89, 90, 91, 92}
    # 92 로 고정하면 2월 시작이 통째로 막힌다.
    assert market_window_end(D(2026, 2, 6)) < D(2026, 2, 6) + dt.timedelta(days=92)


def test_market_window_start_is_the_tight_boundary():
    """하루라도 더 당기면 거부돼야 한다 — 넉넉히 자르면 사용자가 볼 수 있는 구간을 깎는다."""
    for i in range(0, 365, 7):
        end = D(2026, 1, 1) + dt.timedelta(days=i)
        bgn = market_window_start(end)
        assert market_window_end(bgn) >= end
        assert market_window_end(bgn - dt.timedelta(days=1)) < end


def test_every_market_scan_clamps_through_the_shared_helper():
    """네 번째 자리가 자기 상수를 들면 이 테스트가 잡는다 — SSOT 가 SSOT 로 남는 조건."""
    for mod in (risk_events, screener):
        assert mod.market_window_start is market_window_start
        src = inspect.getsource(mod)
        assert "_MARKET_SCAN_MAX_DAYS" not in src, f"{mod.__name__} 이 일수 상한을 되살렸다"


def test_the_cap_is_three_months():
    assert MARKET_WINDOW_MAX_MONTHS == 3
