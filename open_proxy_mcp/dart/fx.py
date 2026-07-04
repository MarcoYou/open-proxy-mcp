"""환율 조회 — 기능통화가 KRW가 아닌 상장사(두산밥캣=USD 등) 재무를 KRW로 환산.

배경(260704): 두산밥캣(241560)은 기능통화 USD라 DART가 재무를 USD로 준다(currency='USD').
KRW 주가/시총 ÷ USD 자본으로 배수를 계산하면 환율(≈1,540)배 왜곡(PBR 1,238 = 오탐). → 통화
감지 후 KRW 환산 필요.

출처: 야후 파이낸스(비공식·무료). ⚠️ 비공식 엔드포인트라 배포(fly.io) IP 차단 시 깨질 수 있음 —
production 하드닝은 한국은행 ECOS(공식·무료·일별) 교체가 v1.1 과제. 소스 교체 쉽게 단일 함수로 격리.

정확도(v1): stock(자본)·flow(순이익) 모두 해당 회계기말 환율 하나로 환산(수 % 오차) — flow는
원칙상 평균환율이나 v1.1로. 핵심인 자릿수(환율배수) 왜곡은 이걸로 해소.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import httpx

_CACHE: dict[tuple[str, str | None], float | None] = {}
_YF = "https://query1.finance.yahoo.com/v8/finance/chart/{pair}"


async def fx_to_krw(currency: str | None, date: str | None = None) -> float | None:
    """currency(예 'USD') 1단위의 KRW 값. date='YYYYMMDD'면 그 날(가장 가까운 거래일) 종가,
    없으면 최신. KRW/빈값이면 1.0. 실패 시 None(호출부에서 미환산 처리)."""
    cur = (currency or "KRW").upper()
    if cur in ("KRW", ""):
        return 1.0
    key = (cur, date)
    if key in _CACHE:
        return _CACHE[key]
    pair = f"{cur}KRW=X"
    try:
        params: dict = {"interval": "1d"}
        if date:
            d = datetime.strptime(date, "%Y%m%d")
            # 기말이 주말·공휴일이면 직전 거래일이 필요 → ±10일 창을 받아 date 이하 최신 종가 선택
            params["period1"] = int((d - timedelta(days=10)).timestamp())
            params["period2"] = int((d + timedelta(days=2)).timestamp())
        else:
            params["range"] = "5d"
        async with httpx.AsyncClient(timeout=15) as h:
            r = await h.get(_YF.format(pair=pair), params=params,
                            headers={"User-Agent": "Mozilla/5.0"})
            res = r.json()["chart"]["result"][0]
            ts = res["timestamp"]
            closes = res["indicators"]["quote"][0]["close"]
        pairs = [(t, c) for t, c in zip(ts, closes) if c]
        if not pairs:
            _CACHE[key] = None
            return None
        if date:
            cutoff = datetime.strptime(date, "%Y%m%d").timestamp() + 86400
            le = [(t, c) for t, c in pairs if t <= cutoff]
            rate = (le or pairs)[-1][1]  # date 이하 최신, 없으면 가장 이른 값
        else:
            rate = pairs[-1][1]
        _CACHE[key] = float(rate)
        return float(rate)
    except Exception:
        _CACHE[key] = None
        return None


def statement_currency(rows: list) -> str:
    """재무제표 rows의 통화 감지 — 자산총계 등 핵심 계정의 currency 필드. 기본 KRW."""
    for r in rows:
        if r.get("account_id") == "ifrs-full_Assets" and r.get("currency"):
            return str(r["currency"]).upper()
    for r in rows:  # 폴백: 아무 currency나
        if r.get("currency"):
            return str(r["currency"]).upper()
    return "KRW"
