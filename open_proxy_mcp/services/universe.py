"""유니버스(종목 집합) 순위표 — 「코스피 시총 상위 100」 같은 말을 종목 목록으로 (260916).

계기: 주간 루틴(영업이익 모멘텀)이 「코스피·코스닥 시총 상위 100」을 만들려고 `trading_data`
를 종목마다 150번 부르고도 코스닥 70~100위가 빠졌을 수 있다고 자백했다. 그런데 `screener`
안에는 `resolve_universe` 가 이미 그 집합을 주간 시세 저장분에서 한 질의로 만들고 있었다 —
공시 필터로만 쓰고 **목록 자체를 돌려주는 도구가 없었을 뿐**이다.

여기서는 그 집합에 시총·종가·시장(주간 시세 저장분)과 회사 이름(DART 회사 원장, 캐시)을
붙여 순위표로 만든다. DART 0콜 · DB 1콜. 순위는 **요청한 시장 안에서** 시총 내림차순이다.

우선주는 뺀다(260916 실측: 코스피 3위가 `005935` 삼성전자우로 나왔다). 회사 순위에서 우선주는
같은 회사의 두 번째 줄이라 한 자리를 버리는 셈이고, DART 회사 원장은 보통주 코드만 알아 이름도
못 붙인다. 상위 N 을 뽑을 땐 여유를 두고 뽑아 우선주를 뺀 뒤 N 으로 자른다. 이름·코드를 직접
나열한 경우(`custom:`)는 사용자가 고른 것이니 그대로 둔다.
"""
from __future__ import annotations

import asyncio
import inspect
import re
from dataclasses import dataclass, field
from typing import Any

from open_proxy_mcp.db import pg_rows
from open_proxy_mcp.market_codes import to_label

_RANK_SPEC = re.compile(r"^(kospi|kosdaq|top_mktcap):(\d+)$")


@dataclass(slots=True)
class UniverseList:
    spec: str                      # screener 문법으로 정규화된 스펙 (kospi:100 · custom:… )
    label: str                     # 사람이 읽는 이름 (KOSPI 시총상위 100)
    resolved: bool
    notice: str = ""               # 대체·부분 해결 안내 (resolve_universe 가 준 문장)
    as_of: str | None = None       # 주간 시세 저장분의 최신 price_dd
    rows: list[dict[str, Any]] = field(default_factory=list)
    db_ok: bool = True             # False = 저장분 조회 자체가 안 됨(미설정/장애)
    excluded_pref: int = 0         # 순위에서 뺀 우선주 수


def _krx_rows(price_dd: str, codes: list[str] | None) -> list[tuple] | None:
    """그 날짜의 (ticker, market, mktcap, close, list_shrs). codes=None 이면 전 종목."""
    sql = ("SELECT ticker, market, mktcap, close, list_shrs FROM krx_weekly "
           "WHERE price_dd=%s AND mktcap IS NOT NULL")
    params: list[Any] = [price_dd]
    if codes is not None:
        sql += " AND ticker = ANY(%s)"
        params.append(codes)
    sql += " ORDER BY mktcap DESC"
    return pg_rows(sql, tuple(params))


async def _names_by_ticker() -> dict[str, str]:
    """단축코드 → 회사명. DART 회사 원장(corpCode.xml, 메모리·sqlite 캐시)에서. 실패하면 빈 표 —
    이름이 없다고 순위표를 못 낼 이유는 없다."""
    try:
        from open_proxy_mcp.dart.client import get_dart_client
        client = get_dart_client()
        if inspect.isawaitable(client):
            client = await client
        corps = await client._load_corp_codes()
    except Exception:  # noqa: BLE001 — 원장 장애는 이름 결측으로만 나타난다
        return {}
    out: dict[str, str] = {}
    for c in corps or []:
        sc = (c.get("stock_code") or "").strip()
        if sc and sc not in out:
            out[sc] = c.get("corp_name") or ""
    return out


def is_preferred(ticker: str, names: dict[str, str]) -> bool:
    """우선주 판정: 원장에 없는 코드인데 끝자리를 0 으로 바꾼 보통주 코드는 원장에 있다
    (005935→005930 삼성전자, 005387→005380 현대차, 00680K→006800 미래에셋증권)."""
    t = (ticker or "").strip()
    if len(t) != 6 or t in names or t[5] == "0":
        return False
    return (t[:5] + "0") in names


def _padded(spec: str) -> tuple[str, int | None, str]:
    """순위 스펙이면 (여유를 둔 스펙, 원래 N, 접두) — 우선주를 뺀 뒤에도 N 이 차게."""
    if spec == "kospi200":
        return "kospi:240", 200, "kospi"
    m = _RANK_SPEC.match(spec)
    if not m:
        return spec, None, ""
    n = int(m.group(2))
    return f"{m.group(1)}:{n + max(20, n // 5)}", n, m.group(1)


async def list_universe(universe: str) -> UniverseList:
    """말(「코스닥 상위 50」·「코스피200」·「삼성전자, SK하이닉스」) → 시총 순위표."""
    from open_proxy_mcp.services.screener import _nl_universe, resolve_universe

    raw = (universe or "").strip()
    spec = _nl_universe(raw)
    query_spec, n, _prefix = _padded(spec)
    uf = await resolve_universe(query_spec)
    label, notice = uf.label, uf.notice
    if spec == "kospi200":
        label = "KOSPI200(→KOSPI 시총상위 200 대체)"
        notice = "KOSPI200 구성종목 원장이 없어 KOSPI 시총상위 200(보통주)으로 대체했다(코스닥 미포함)."
    elif n is not None:
        label = re.sub(r"\d+\s*$", str(n), label)
    if uf.price_dd is None:
        # 최신 날짜조차 못 읽었다 — 저장분이 없거나 DB 가 죽었다. 종목 집합 문제가 아니다.
        return UniverseList(spec=spec, label=label, resolved=False, notice=notice, db_ok=False)
    if not uf.resolved:
        return UniverseList(spec=spec, label=label, resolved=False, notice=notice,
                            as_of=uf.price_dd)
    codes = None if uf.allowed is None else sorted(uf.allowed)
    rows = await asyncio.to_thread(_krx_rows, uf.price_dd, codes)
    if rows is None:
        return UniverseList(spec=spec, label=label, resolved=True, notice=notice,
                            as_of=uf.price_dd, db_ok=False)
    names = await _names_by_ticker()
    keep_pref = spec.startswith("custom:")   # 직접 고른 코드는 우선주라도 뺄 이유가 없다
    out: list[dict[str, Any]] = []
    excluded = 0
    for t, m, cap, close, shrs in rows:
        if not keep_pref and is_preferred(t, names):
            excluded += 1
            continue
        out.append({"rank": len(out) + 1, "ticker": t, "name": names.get(t) or "-",
                    "market": to_label(m),
                    "mktcap_krw": int(cap) if cap is not None else None,
                    "close_krw": int(close) if close is not None else None,
                    "list_shrs": int(shrs) if shrs is not None else None})
    if n is not None:
        out = out[:n]
    return UniverseList(spec=spec, label=label, resolved=True, notice=notice,
                        as_of=uf.price_dd, rows=out, excluded_pref=excluded)
