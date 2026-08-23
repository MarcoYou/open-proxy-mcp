"""환율 조회 — 기능통화가 KRW가 아닌 상장사(두산밥캣=USD 등) 재무를 KRW로 환산.

배경(260704): 두산밥캣(241560)은 기능통화 USD라 DART가 재무를 USD로 준다(currency='USD').
KRW 주가/시총 ÷ USD 자본으로 배수를 계산하면 환율(≈1,440)배 왜곡(PBR 1,238 = 오탐). → 통화
감지 후 KRW 환산 필요.

소스 우선순위: ① ECOS(한국은행 매매기준율, 공식·안정) → ② 야후 파이낸스(폴백, 비공식·무키).
캐시 3층: 인메모리(_MEM) → Supabase ecos_fx_rate(영구) → 소스 호출.
  - 저장 규칙: **과거(확정) 날짜 = 영구 저장**(회계기말=분기말은 값이 안 바뀜, 불변). "오늘/최신"은
    변동값이라 영구 저장 안 함 — _MEM을 조회기준일(오늘)로 키잉해 하루 지나면 자동 미스→재조회.
  - 밸류에이션은 항상 과거 분기말로 조회(재무=회계기말 기준)하므로 실제 저장 대상 = 분기말뿐(연 4개).

정확도(v1): stock(자본)·flow(순이익) 모두 회계기말 환율 하나로 환산(수 % 오차, 호출부 경고). flow
원칙상 평균환율은 v1.1.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta

import httpx

_MEM: dict[tuple[str, str], float | None] = {}
# 통화 → (ECOS 731Y001 item 코드, per-단위 divisor). 엔·동 등은 100단위로 고시되므로 divisor로
# 1단위 환산(예 JPY 927원/100엔 → 9.27원/엔). 매핑에 없는 통화만 야후 폴백. (실측 전수검증 260704:
# 국내상장 외국사 = USD/CNY/JPY. 야후는 CNY 과거범위 조회가 빈값·값도 부정확 → ECOS 정본 우선.)
_ECOS_ITEM = {
    "USD": ("0000001", 1), "CNY": ("0000053", 1), "JPY": ("0000002", 100),
    "EUR": ("0000003", 1), "HKD": ("0000015", 1), "GBP": ("0000012", 1),
    "CHF": ("0000014", 1), "AUD": ("0000017", 1), "CAD": ("0000013", 1),
    "SGD": ("0000024", 1), "VND": ("0000035", 100),
}
_FX_DDL = ("CREATE TABLE IF NOT EXISTS ecos_fx_rate("
           "base_ccy text, fx_dd text, rate double precision, PRIMARY KEY(base_ccy, fx_dd))")
_YF = "https://query1.finance.yahoo.com/v8/finance/chart/{pair}"


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def _settled(date: str) -> bool:
    """과거(확정) 날짜만 영구 캐시 대상 — 오늘/미래는 변동값이라 제외."""
    return date < _today()


def _db_get(ccy: str, date: str) -> float | None:
    url = os.getenv("DATABASE_URL")
    if not url:
        return None
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=8) as c:
            c.execute(_FX_DDL)
            r = c.execute("SELECT rate FROM ecos_fx_rate WHERE base_ccy=%s AND fx_dd=%s",
                          (ccy, date)).fetchone()
            return r[0] if r else None
    except Exception:
        return None


def _db_put(ccy: str, date: str, rate: float) -> None:
    url = os.getenv("DATABASE_URL")
    if not url:
        return
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=8) as c:
            c.execute(_FX_DDL)
            c.execute("INSERT INTO ecos_fx_rate(base_ccy, fx_dd, rate) VALUES(%s,%s,%s) "
                      "ON CONFLICT (base_ccy, dt) DO NOTHING", (ccy, date, rate))
            c.commit()
    except Exception:
        pass


async def _ecos(ccy: str, date: str) -> float | None:
    """한국은행 ECOS 매매기준율(731Y001). date 이하 최신(주말·공휴일이면 직전 거래일)."""
    key = os.getenv("ECOS_API_KEY")
    ent = _ECOS_ITEM.get(ccy)
    if not key or not ent:
        return None
    item, divisor = ent
    d = datetime.strptime(date, "%Y%m%d")
    s = (d - timedelta(days=10)).strftime("%Y%m%d")
    url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/100/"
           f"731Y001/D/{s}/{date}/{item}")
    try:
        async with httpx.AsyncClient(timeout=15) as h:
            j = (await h.get(url)).json()
        rows = j.get("StatisticSearch", {}).get("row") or []
        vals = [(x["TIME"], float(x["DATA_VALUE"])) for x in rows if x.get("DATA_VALUE")]
        if not vals:
            return None
        le = [v for v in vals if v[0] <= date]
        return (le or vals)[-1][1] / divisor  # 100단위 고시(엔·동) → 1단위 환산
    except Exception:
        return None


async def _yahoo(ccy: str, date: str | None) -> float | None:
    pair = f"{ccy}KRW=X"
    try:
        params: dict = {"interval": "1d"}
        if date:
            d = datetime.strptime(date, "%Y%m%d")
            params["period1"] = int((d - timedelta(days=10)).timestamp())
            params["period2"] = int((d + timedelta(days=2)).timestamp())
        else:
            params["range"] = "5d"
        async with httpx.AsyncClient(timeout=15) as h:
            r = await h.get(_YF.format(pair=pair), params=params,
                            headers={"User-Agent": "Mozilla/5.0"})
            res = r.json()["chart"]["result"][0]
            pairs = [(t, c) for t, c in zip(res["timestamp"],
                     res["indicators"]["quote"][0]["close"]) if c]
        if not pairs:
            return None
        if date:
            cutoff = datetime.strptime(date, "%Y%m%d").timestamp() + 86400
            le = [(t, c) for t, c in pairs if t <= cutoff]
            return float((le or pairs)[-1][1])
        return float(pairs[-1][1])
    except Exception:
        return None


async def fx_to_krw(currency: str | None, date: str | None = None) -> float | None:
    """currency(예 'USD') 1단위의 KRW 값. date='YYYYMMDD'면 그 날(가장 가까운 거래일) 종가/매매
    기준율, 없으면 오늘. KRW/빈값이면 1.0. 실패 시 None(호출부에서 미환산 처리)."""
    cur = (currency or "KRW").upper()
    if cur in ("KRW", ""):
        return 1.0
    q_date = date or _today()          # 조회 기준일 — None이면 오늘(변동값)
    memkey = (cur, q_date)
    if memkey in _MEM:
        return _MEM[memkey]

    rate: float | None = None
    if _settled(q_date):               # 과거 확정일 → 영구캐시 조회
        rate = await asyncio.to_thread(_db_get, cur, q_date)
    if rate is None:                   # 캐시 미스 → ECOS 1차, 야후 폴백
        rate = await _ecos(cur, q_date) or await _yahoo(cur, q_date)
        if rate is not None and _settled(q_date):
            await asyncio.to_thread(_db_put, cur, q_date, rate)
    _MEM[memkey] = rate
    return rate


def statement_currency(rows: list) -> str:
    """재무제표 rows의 통화 감지 — 자산총계 등 핵심 계정의 currency 필드. 기본 KRW."""
    for r in rows:
        if r.get("account_id") == "ifrs-full_Assets" and r.get("currency"):
            return str(r["currency"]).upper()
    for r in rows:
        if r.get("currency"):
            return str(r["currency"]).upper()
    return "KRW"
