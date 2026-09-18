# -*- coding: utf-8 -*-
"""price_multiple_data 의 선행(애널리스트 추정) PER·PBR — 시장·WICS 업종 표 (260918). network·DB 0.

지키는 것: ① 선행 칸은 트레일링 옆에, 추정 종목 수는 종목수 옆 괄호로 ② 합이 0 이하면 트레일링처럼
「적자 −N조」 ③ 추정 행이 없는 업종은 `-` 와 (0) — 0 으로 메우지 않는다 ④ KSIC 에는 선행을 안 붙이고
조회도 안 한다 ⑤ 선행 조회가 실패해도 트레일링 표는 그대로(fail-open) ⑥ 기준일 이전이면 선행 칸 없이
「선행 집계는 언제부터」를 적는다 ⑦ 추이는 주마다 마지막 스냅샷 하나.
검증은 MCP 호출로 — 렌더러를 건너뛰면 사용자가 보는 것과 다르다.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json

from open_proxy_mcp.server import mcp
from open_proxy_mcp.services import price_multiple_data as S

MARKET_ROWS = [  # snap_dd, market, per_fy0, per_ttm, pbr_fy0, pbr_mrq, cap, ni_ttm, eq, cap_pref, ni_fy0
    ("20260916", "KS", 12.0, 11.0, 1.3, 1.2, 3.0e15, 2.7e14, 2.5e15, 5e13, 2.5e14),
    ("20260916", "KQ", 40.0, 35.0, 2.6, 2.5, 5.0e14, 1.4e13, 2.0e14, 0.0, 1.2e13),
    ("20251226", "KS", 10.0, 9.5, 1.0, 0.9, 2.0e15, 2.1e14, 2.2e15, 4e13, 2.0e14),
    ("20251226", "KQ", 30.0, 28.0, 2.0, 1.9, 4.0e14, 1.4e13, 2.0e14, 0.0, 1.3e13),
]
SECTOR_ROWS = [  # snap_dd, market, sector, label, n, cap, per_ttm, pbr_mrq, per_fy0, pbr_fy0, ni_fy0, ni_ttm
    ("20260916", "KS", "G4530", "반도체와반도체장비", 20, 1.5e15, 9.0, 2.5, 9.5, 2.6, 1.6e14, 1.7e14),
    ("20260916", "KS", "G2010", "자본재", 138, 4.0e14, 20.0, 1.9, 21.0, 2.0, 1.9e13, 2.0e13),
    ("20260916", "KS", "G4535", "전자와 전기제품", 40, 2.0e14, None, 2.3, None, 2.4, -1.0e12, -5.0e11),
    ("20260916", "KS", "G2560", "교육서비스", 3, 1.0e12, 15.0, 1.0, 16.0, 1.1, 6e10, 7e10),
    ("20260909", "KS", "G2010", "자본재", 137, 3.9e14, 19.5, 1.85, 20.5, 1.95, 1.9e13, 2.0e13),
]


def F(as_of, market, bucket, label=None, n=10, per=10.0, ni=1e12, pbr=1.0, psr=0.5, per_pos=None,
      dy=None, n_dps=0, fy=2026):
    d = {"as_of": dt.date.fromisoformat(as_of), "market": market, "bucket": bucket, "label": label,
         "n_total": n, "cap_krw": 1e13, "n_per": n, "ni_krw": ni, "fwd_per": per,
         "n_per_pos": n, "fwd_per_pos": per if per_pos is None else per_pos,
         "n_pbr": n, "eq_krw": 1e13, "fwd_pbr": pbr, "n_psr": n, "fwd_psr": psr,
         "n_dps": n_dps, "fwd_div_yield_pct": dy, "fy_main": fy, "fy_min": fy, "fy_max": fy, "class_dd": "20260828"}
    return tuple(d[c] for c in S._FWD_COLS)


FWD = {
    "market": [
        F("2026-09-13", "KS", "_ALL", n=324, per=6.65, pbr=1.2, per_pos=6.45, dy=1.9, n_dps=270),
        F("2026-09-13", "KQ", "_ALL", n=326, per=None, ni=-5e11, pbr=2.7, per_pos=19.68),
        F("2026-09-13", "ALL", "_ALL", n=650, per=7.1, pbr=1.4),
        F("2026-09-12", "KS", "_ALL", n=324, per=6.60, pbr=1.19),
        F("2026-09-12", "KQ", "_ALL", n=326, per=22.5, pbr=2.6),
        F("2026-09-05", "KS", "_ALL", n=322, per=6.44, pbr=1.15),
        F("2026-09-05", "KQ", "_ALL", n=335, per=22.8, pbr=2.5),
    ],
    "wics_industry": [
        F("2026-09-13", "KS", "G4530", "반도체와반도체장비", n=11, per=4.87, pbr=2.76, dy=1.72, n_dps=10),
        F("2026-09-13", "KS", "G2010", "자본재", n=70, per=16.65, pbr=1.98, dy=1.56, n_dps=60),
        F("2026-09-13", "KS", "G4535", "전자와 전기제품", n=6, per=None, ni=-2e11, pbr=2.36, per_pos=24.88),
        F("2026-09-05", "KS", "G2010", "자본재", n=69, per=15.9, pbr=1.9),
    ],
}


def fake_db(fwd=FWD, *, fwd_fail=False, seen=None):
    def rows(sql, params=()):
        if seen is not None:
            seen.append(sql)
        if "opm_val_fwd" in sql:
            if fwd_fail:
                return None
            if "MIN(as_of)" in sql:
                return [(dt.date(2026, 8, 28),)]
            got = list(fwd.get(params[0], []))
            if "as_of <= %s" in sql:
                got = [r for r in got if r[0] <= dt.date.fromisoformat(params[-1])]
            if "ORDER BY as_of DESC" in sql:
                return sorted(got, key=lambda r: r[0], reverse=True)
            top = max((r[0] for r in got), default=None)
            return [r for r in got if r[0] == top]
        if "div_yield_hist" in sql:
            return []
        if "scheme='market'" in sql:
            return MARKET_ROWS
        if "FROM opm_val_market" in sql:  # 산업 표 — 기준일(있으면) 이하 가장 최근 스냅샷 하나
            ok = [r for r in SECTOR_ROWS if len(params) < 3 or r[0] <= params[2]]
            top = max((r[0] for r in ok), default=None)
            return [r for r in ok if r[0] == top]
        return []
    return rows


def call(args):
    async def go():
        r = await mcp.call_tool("price_multiple_data", args)
        return "".join(getattr(c, "text", "") for c in (r if isinstance(r, list) else r.content))
    return asyncio.run(go())


def test_market_puts_forward_next_to_trailing_with_its_own_date_and_population(monkeypatch):
    monkeypatch.setattr(S, "_pg_rows", fake_db())
    out = call({"scope": "market"})
    assert "| 시장 | PER(FY0) | PER(TTM) | PER(선행) | PBR(FY0) | PBR(MRQ) | PBR(선행) |" in out
    assert "| KOSPI | 12.00 | 11.00 | 6.65 | 1.30 | 1.20 | 1.20 |" in out
    # 코스닥 선행 합이 적자면 트레일링과 같은 표기 — 흑자만 더한 벤더식(19.68)을 대신 쓰지 않는다
    assert "| KOSDAQ | 40.00 | 35.00 | 적자 -0.50조 | 2.60 | 2.50 | 2.70 |" in out
    assert "추정 스냅샷 2026-09-13" in out and "추정 종목 KOSPI 324사 · KOSDAQ 326사" in out
    assert "트레일링과 같은 방식(적자 추정도 더한다)" in out and "대부분 2026년" in out
    # 추이 — 주마다 마지막 하나(9/12 는 9/13 과 같은 주라 빠진다)
    trend = out.split("## 선행 배수 추이")[1].split("\n> ")[0]
    assert "| 2026-09-13 | 6.65 / 1.20 | 적자 -0.50조 / 2.70 |" in trend
    assert "| 2026-09-05 | 6.44 / 1.15 | 22.80 / 2.50 |" in trend and "2026-09-12" not in trend
    # 선행 배당수익률은 선행 배수와 같은 표에서 온다
    assert "- / 1.90" in out and "선행 as_of 2026-09-13" in out
    j = json.loads(call({"scope": "market", "format": "json"}))["data"]
    ks = next(h for h in j["latest"] if h["market"] == "KS")
    assert ks["fwd_per"] == 6.65 and ks["fwd_per_pos"] == 6.45 and ks["fwd_n_total"] == 324 and ks["fwd_psr"] == 0.5
    assert j["fwd_ruler"]["as_of"] == "2026-09-13" and len(j["fwd_history"]) == 7


def test_market_before_forward_history_says_since_when_instead_of_blank(monkeypatch):
    monkeypatch.setattr(S, "_pg_rows", fake_db())
    out = call({"scope": "market", "as_of": "20251231"})
    assert "PER(선행)" not in out and "기준 20251226" in out
    assert "기준일 20251231 이하 선행 집계 없음 — 선행 집계는 2026-08-28 부터 쌓였다." in out


def test_industry_table_counts_estimates_and_keeps_missing_rows_blank(monkeypatch):
    monkeypatch.setattr(S, "_pg_rows", fake_db())
    out = call({"scope": "sector", "scheme": "wics_industry"})
    assert "| 섹터 | 종목수(추정) | PER(TTM) | PER(선행) | PBR(MRQ) | PBR(선행) | 배당수익률% 확정(배당주)/선행 | Σ시총 |" in out
    assert "| 자본재 | 138 (70) | 20.00 | 16.65 | 1.90 | 1.98 | - / 1.56 |" in out
    assert "| 전자와 전기제품 | 40 (6) | 적자 -0.50조 | 적자 -0.20조 | 2.30 | 2.36 |" in out
    assert "| 교육서비스 | 3 (0) | 15.00 | - | 1.00 | - | - / - |" in out
    assert "업종 분류 20260828" in out and "하위업종 표는 선행만 채워진다" in out
    # 과거 기준일이면 그 이하 추정 스냅샷을 쓴다
    j = json.loads(call({"scope": "sector", "scheme": "wics_industry", "as_of": "20260910", "format": "json"}))
    cap = next(s for s in j["data"]["sectors"] if s["sector"] == "G2010")
    assert cap["fwd_per"] == 15.9 and j["data"]["fwd_ruler"]["as_of"] == "2026-09-05"


def test_ksic_gets_no_forward_and_does_not_even_ask(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(S, "_pg_rows", fake_db(seen=seen))
    out = call({"scope": "sector", "scheme": "ksic"})
    assert "PER(선행)" not in out and "종목수(추정)" not in out and "🔭" not in out
    assert not any("opm_val_fwd" in q for q in seen)


def test_forward_failure_keeps_the_trailing_table(monkeypatch):
    monkeypatch.setattr(S, "_pg_rows", fake_db(fwd_fail=True))
    out = call({"scope": "market"})
    assert "| KOSPI | 12.00 | 11.00 | 1.30 | 1.20 |" in out and "PER(선행)" not in out
    assert out.count("선행 배수 조회 실패") == 1                    # 선행 각주 한 번만 — 배당 각주가 되풀이하지 않는다
