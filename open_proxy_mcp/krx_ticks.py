# -*- coding: utf-8 -*-
"""KRX 주식 호가단위 — 기준가 리셋이 「전일 종가를 호가단위에 맞춘 것」뿐인지 가린다. 네트워크·DB 없음.

배경(261019 기능 검증): 수정계수 원장(`krx_adj_events`)은 거래소 기준가(종가 − 전일대비)가 전일 종가와 어긋난 날을 전부 적는다
(`scripts/krx_base_resets.py` — 어긋남 > max(종가×0.1%, 1원)). 그런데 기준가가 전일 종가를 **호가단위로 올리거나 내린 것**뿐인 날도
여기에 걸린다 — 예: 하나31호스팩(469900) 전일 종가 2,082원 → 기준가 2,085원(호가단위 5원, ×1.0014). 기업 행위가 아니라서 가격
시계열이 끊긴 것이 아닌데, `trading_data`(firm)는 이것까지 「기준가 조정 … 불연속」으로 셌다(운영 실측: 3년 창 안 |계수−1|<0.5%
378건 중 371건·81종목이 이 꼴, 대부분 기업인수목적회사, 최대 |계수−1| 0.4%). 나머지 7건(맥쿼리인프라·롯데리츠 등)은 호가단위보다
크게 움직인 진짜 조정이라 그대로 센다. |계수−1| 문턱(0.5%)으로 자르면 이 7건까지 숨는다 — 그래서 문턱이 아니라 호가단위로 가린다.

판정: |기준가 − 전일 종가| 가 그 가격대의 호가단위(전일 종가·기준가 둘 중 큰 쪽)보다 작으면 호가단위 맞춤이다. 기준가는 늘 호가단위
위에 있으므로, 이보다 작은 어긋남은 반올림·올림 말고는 생길 수 없다(그보다 작은 진짜 조정이 있어도 거래소 가격으로는 구분되지 않는다).

호가단위 표(주식, 상장주식 가격 범위별 — 유가증권·코스닥시장 업무규정 시행세칙):
  2023-01-25 부터 두 시장 같은 7단계: <2,000 1 · <5,000 5 · <20,000 10 · <50,000 50 · <200,000 100 · <500,000 500 · 그 이상 1,000
  그 전 코스피: <1,000 1 · <5,000 5 · <10,000 10 · <50,000 50 · <100,000 100 · <500,000 500 · 그 이상 1,000
  그 전 코스닥: <1,000 1 · <5,000 5 · <10,000 10 · <50,000 50 · 그 이상 100
"""
from __future__ import annotations

from open_proxy_mcp.market_codes import KQ

#: 두 시장 호가단위 통일 시행일(YYYYMMDD).
UNIFIED_FROM = "20230125"

_UNIFIED = ((2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100), (500_000, 500))
_KS_OLD = ((1_000, 1), (5_000, 5), (10_000, 10), (50_000, 50), (100_000, 100), (500_000, 500))
_KQ_OLD = ((1_000, 1), (5_000, 5), (10_000, 10), (50_000, 50))

#: 수정계수 원장의 라벨 — 호가단위 맞춤으로 판정한 리셋(`scripts/adj_factor_v3.py`). 시장이전(`excluded_market_transfer`)처럼
#: 계수·실측은 그대로 두고 「소비할 때 뺀다」는 표시만 붙인다.
TICK_ROUNDING_LABEL = "excluded_tick_rounding"
TICK_ROUNDING_TYPE = "tick_rounding"


def tick_size(price: float, market: str | None, dd: str) -> int:
    """그 날 그 시장에서 `price` 가격대의 호가단위(원)."""
    if dd >= UNIFIED_FROM:
        table, top = _UNIFIED, 1_000
    elif (market or "").upper() == KQ:
        table, top = _KQ_OLD, 100
    else:
        table, top = _KS_OLD, 1_000
    for limit, tick in table:
        if price < limit:
            return tick
    return top


def is_tick_rounding(prev_close: float | None, base_price: float | None, market: str | None, dd: str | None) -> bool:
    """기준가가 전일 종가를 호가단위에 맞춘 것뿐인가. 값이 없으면 False(판정하지 않는다 — 조정으로 둔다)."""
    if not prev_close or not base_price or not dd:
        return False
    try:
        pc, bp = float(prev_close), float(base_price)
    except (TypeError, ValueError):
        return False
    gap = abs(bp - pc)
    return 0 < gap < max(tick_size(pc, market, dd), tick_size(bp, market, dd))
