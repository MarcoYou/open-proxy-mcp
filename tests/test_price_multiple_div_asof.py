# -*- coding: utf-8 -*-
"""price_multiple_data 시장·업종 표의 **과거 기준일(as_of) 확정 배당수익률** — 그 날까지 확정·공시된 사업연도만 (261020 p1j). network·DB 0.

고치기 전: 확정 배당 집계를 기준일과 상관없이 가장 늦은 사업연도로 읽었다. 운영(10-20)
`scope=market` 의 as_of 20201030·20230331·20250829·20260327 넷 다 FY2025(시총 20251230) 코스피 1.5567%·코스닥 0.6879% —
기준일보다 뒤의 사업연도·뒤의 시총이다(미래 정보). 업종 대분류(`scheme="대분류"`)도 같은 읽기를 썼다.

지키는 것: ① 과거 기준일은 그 날까지 확정·공시된 사업연도 — 12월 결산 FY 의 결산 배당·사업보고서는 다음 해 3월 말까지 나와
4월 1일부터 쓴다(같은 응답 PER·PBR 의 FY0 와 같은 시점 규칙 `report_calendar.pit_annual`) ② 기준은 응답이 답한 스냅샷 날짜다 —
요청일이 4월 1일이어도 스냅샷이 3월 28일이면 그 전 사업연도(표 머리 「기준 YYYYMMDD」 보다 뒤 정보를 싣지 않는다)
③ 그 사업연도 집계가 없으면 칸을 비우고 까닭을 적는다(최신 사업연도로 메우지 않는다) ④ 집계가 그 사업연도까지 아직 없으면 앞 해 값이고 그렇다고 적는다
⑤ 최신(as_of 없음) 응답은 종전 그대로 — 질의·값·자 모두 ⑥ 업종 대분류도 같은 규칙.
검증은 MCP 호출로 — 렌더러를 건너뛰면 사용자가 보는 것과 다르다.
"""
from __future__ import annotations

import asyncio
import json

from open_proxy_mcp.server import mcp
from open_proxy_mcp.services import price_multiple_data as S

# snap_dd, market, per_fy0, per_ttm, pbr_fy0, pbr_mrq, cap, ni_ttm, eq, cap_pref, ni_fy0 — 내림차순(운영 질의와 같은 순서)
_SNAPS = ("20261016", "20260327", "20250829", "20250404", "20250328", "20230331", "20201030")
MARKET_ROWS = [r for dd in _SNAPS for r in (
    (dd, "KQ", 40.0, 35.0, 2.6, 2.5, 5.0e14, 1.4e13, 2.0e14, 0.0, 1.2e13),
    (dd, "KS", 12.0, 11.0, 1.3, 1.2, 3.0e15, 2.7e14, 2.5e15, 5e13, 2.5e14))]

# 운영 값(10-20 읽기 전용) — (market, bucket, fy, all, payers, price_dd). 사업연도마다 값이 달라야 어느 해를 읽었는지 가린다.
_DIV_MARKET = [
    ("KS", 2021, 1.836312, 2.238113, "20211230"), ("KQ", 2021, 0.547608, 1.113976, "20211230"),
    ("KS", 2022, 2.270172, 2.866558, "20221229"), ("KQ", 2022, 0.812133, 1.639826, "20221229"),
    ("KS", 2023, 1.972253, 2.425762, "20231228"), ("KQ", 2023, 0.567582, 1.410046, "20231228"),
    ("KS", 2024, 2.39195, 2.88665, "20241230"), ("KQ", 2024, 0.751547, 1.740027, "20241230"),
    ("KS", 2025, 1.556699, 1.844814, "20251230"), ("KQ", 2025, 0.687944, 1.400665, "20251230"),
]
#: 업종 대분류 — 버킷은 업종 **이름**(그 표의 bucket 이 이름이다)
_DIV_SECTOR = [
    ("KS", "에너지", 2023, 3.1, 3.5, "20231228"), ("KS", "에너지", 2024, 4.2, 4.6, "20241230"),
    ("KS", "에너지", 2025, 5.3, 5.7, "20251230"),
]


def _div_rows(scheme: str) -> list[tuple]:
    """(scheme, fy, row) — 운영 질의의 칸 순서: market, bucket, denom_basis, div_yield_pct, fiscal_year, price_dd, n_total, n_div, cov_cap_pct."""
    out = []
    if scheme == "market":
        for m, fy, a, p, dd in _DIV_MARKET:
            out += [(fy, (m, "ALL", "all", a, fy, dd, 800, 700, 88.0)), (fy, (m, "ALL", "payers", p, fy, dd, 560, 560, 88.0))]
    elif scheme == "wics_sector":
        for m, b, fy, a, p, dd in _DIV_SECTOR:
            out += [(fy, (m, b, "all", a, fy, dd, 30, 20, 90.0)), (fy, (m, b, "payers", p, fy, dd, 20, 20, 90.0))]
    return out


SECTOR_SNAPS = ("20261016", "20250829", "20250328", "20201030")


def fake_db(*, seen=None, div_fail=False):
    """운영 질의를 흉내 낸다. 확정 배당 집계: 「사업연도 = (그 scheme 의) 가장 늦은 해」, 상한 칸(셋째 인자)이 있으면 그 해 이하에서."""
    def rows(sql, params=()):
        if seen is not None:
            seen.append((" ".join(sql.split()), tuple(params)))
        if "div_yield_hist" in sql:
            if div_fail:
                return None
            got = _div_rows(params[0])
            if "MIN(fiscal_year)" in sql:
                return [(min((fy for fy, _ in got), default=None),)]
            cap = params[2] if len(params) > 2 else None
            fys = [fy for fy, _ in got if cap is None or fy <= cap]
            if not fys:
                return []
            return [r for fy, r in got if fy == max(fys)]
        if "opm_val_fwd" in sql:
            return [] if "MIN(as_of)" not in sql else [(None,)]
        if "scheme='market'" in sql:
            return MARKET_ROWS
        if sql.startswith("SELECT MAX(snap_dd)"):      # 업종 표 머리의 시장 전체 최신일
            cap = params[2] if len(params) > 2 else None
            return [(max(d for d in _SNAPS if cap is None or d <= cap),)]
        if sql.startswith("SELECT snap_dd, market, sector"):   # 업종 표 — 기준일(있으면) 이하 가장 최근 스냅샷 하나
            cap = params[2] if len(params) > 2 else None
            dd = max(d for d in SECTOR_SNAPS if cap is None or d <= cap)
            return [(dd, "KS", "G10", "에너지", 20, 1.0e14, 8.0, 0.9, 8.5, 0.95, 1.2e13, 1.25e13),
                    (dd, "KS", "G20", "산업재", 100, 4.0e14, 20.0, 1.9, 21.0, 2.0, 1.9e13, 2.0e13)]
        return []
    return rows


def call(args):
    async def go():
        r = await mcp.call_tool("price_multiple_data", args)
        return "".join(getattr(c, "text", "") for c in (r if isinstance(r, list) else r.content))
    return asyncio.run(go())


def mkt(args):
    j = json.loads(call({**args, "scope": "market", "format": "json"}))["data"]
    return j, {h["market"]: h for h in j["latest"]}


def test_past_date_uses_the_fiscal_year_disclosed_by_then_not_the_latest(monkeypatch):
    """운영에서 본 넷 — 기준일마다 그 날까지 확정·공시된 사업연도(시총도 그 해 12월 — 기준일 이전)."""
    monkeypatch.setattr(S, "_pg_rows", fake_db())
    for as_of, fy, ks_all, kq_all, dd in (("20230331", 2021, 1.8363, 0.5476, "20211230"),   # 3월 말 — FY2022 는 아직(4월 1일부터)
                                          ("20250829", 2024, 2.392, 0.7515, "20241230"),
                                          ("20260327", 2024, 2.392, 0.7515, "20241230")):   # FY2025 는 2026-04-01 부터
        j, by = mkt({"as_of": as_of})
        assert j["as_of"] == as_of
        assert by["KS"]["div_fiscal_year"] == fy and by["KS"]["div_yield_pct_all"] == ks_all, (as_of, by["KS"])
        assert by["KQ"]["div_yield_pct_all"] == kq_all and by["KS"]["div_price_dd"] == dd <= as_of
        r = j["div_yield_ruler"]
        assert r["actual_fiscal_year"] == fy and r["actual_price_dd"] == dd and r["actual_basis_dd"] == as_of
        # 261020(검토): 그 해 집계가 있으면 「앞 해 값」·「빈칸」 안내가 붙지 않는다 — `fy < pit` 가 `<=` 로 바뀌면 모든 과거 응답에 거짓 안내가 붙는다.
        assert r["actual_pit_fy"] == fy and "actual_note_older" not in r and "actual_note_empty" not in r
        out = call({"scope": "market", "as_of": as_of})
        assert f"확정 FY{fy}(시총 {dd})" in out and f"{as_of} 까지 확정·공시된 사업연도" in out
        assert "FY2025" not in out and "1.56 (1.84)" not in out and "까지 확정·공시된 사업연도는 FY" not in out   # ⚠️ 두 안내 모두 없음


def test_before_the_first_aggregated_year_leaves_the_cell_blank_and_says_why(monkeypatch):
    """20201030 — 그 날 확정된 사업연도는 FY2019 인데 집계는 FY2021 부터다. 최신(FY2025) 값으로 메우지 않는다."""
    monkeypatch.setattr(S, "_pg_rows", fake_db())
    j, by = mkt({"as_of": "20201030"})
    assert all("div_yield_pct_all" not in h and "div_fiscal_year" not in h for h in by.values())
    r = j["div_yield_ruler"]
    assert "actual_fiscal_year" not in r and r["actual_pit_fy"] == 2019
    assert "FY2019" in r["actual_note_empty"] and "FY2021 부터" in r["actual_note_empty"]
    out = call({"scope": "market", "as_of": "20201030"})
    assert "| KOSPI | 12.00 | 11.00 | 1.30 | 1.20 | - / - |" in out
    assert "⚠️ 기준일 20201030 까지 확정·공시된 사업연도는 FY2019" in out
    assert "1.56" not in out and "FY2025" not in out


def test_basis_is_the_snapshot_the_table_answers_with_not_the_requested_day(monkeypatch):
    """요청 20250401 → 표는 20250328 스냅샷이다(머리 「기준 20250328」). FY2024 는 4월 1일부터라 그 표에는 FY2023."""
    monkeypatch.setattr(S, "_pg_rows", fake_db())
    j, by = mkt({"as_of": "20250401"})
    assert j["as_of"] == "20250328" and by["KS"]["div_fiscal_year"] == 2023
    assert j["div_yield_ruler"]["actual_basis_dd"] == "20250328"
    j, by = mkt({"as_of": "20250404"})
    assert j["as_of"] == "20250404" and by["KS"]["div_fiscal_year"] == 2024


def test_a_year_not_aggregated_yet_falls_back_to_the_year_before_and_says_so(monkeypatch):
    """2027-04 이후인데 FY2026 집계가 아직 없으면 FY2025 — 기준일보다 앞선 정보라 쓰되, 그 해 값이 아니라고 적는다."""
    rows = [("20270409", "KS", 12.0, 11.0, 1.3, 1.2, 3.0e15, 2.7e14, 2.5e15, 5e13, 2.5e14)] + MARKET_ROWS
    base = fake_db()
    monkeypatch.setattr(S, "_pg_rows", lambda sql, params=(): rows if "scheme='market'" in sql else base(sql, params))
    j, by = mkt({"as_of": "20270409"})
    assert by["KS"]["div_fiscal_year"] == 2025 and j["div_yield_ruler"]["actual_pit_fy"] == 2026
    assert "FY2026" in j["div_yield_ruler"]["actual_note_older"] and "FY2025" in j["div_yield_ruler"]["actual_note_older"]
    # 261020(검토): 사용자가 보는 표 아래에도 그 안내가 나간다(렌더에서 빠지면 JSON 만 알고 표는 모른다).
    out = call({"scope": "market", "as_of": "20270409"})
    assert "⚠️ 기준일 20270409 까지 확정·공시된 사업연도는 FY2026 인데" in out and "FY2025 값이다." in out
    assert "확정 FY2025(시총 20251230) — 20270409 까지 확정·공시된 사업연도" in out


def test_latest_response_is_unchanged(monkeypatch):
    """as_of 없이 — 종전 질의(가장 늦은 사업연도, 상한 없음)·값·자 그대로. 새 자 칸이 생기지 않는다."""
    seen: list = []
    monkeypatch.setattr(S, "_pg_rows", fake_db(seen=seen))
    j, by = mkt({})
    assert j["as_of"] == "20261016" and by["KS"]["div_fiscal_year"] == 2025 and by["KS"]["div_yield_pct_all"] == 1.5567
    assert by["KQ"]["div_yield_pct_all"] == 0.6879 and by["KQ"]["div_yield_pct_payers"] == 1.4007
    assert j["div_yield_ruler"] == {"actual_fiscal_year": 2025, "actual_price_dd": "20251230"}
    div_q = [q for q in seen if "div_yield_hist" in q[0]]
    assert div_q == [("SELECT market, bucket, denom_basis, div_yield_pct, fiscal_year, price_dd, n_total, n_div, cov_cap_pct "
                      "FROM div_yield_hist WHERE scheme=%s AND fiscal_year=(SELECT MAX(fiscal_year) FROM div_yield_hist WHERE scheme=%s) "
                      "AND denom_basis IN ('all','payers')", ("market", "market"))]
    out = call({"scope": "market"})
    assert "확정 FY2025(시총 20251230). PER·PBR 의 주간 스냅샷과 기준일이 다르다." in out and "까지 확정·공시된" not in out


def test_major_sector_axis_follows_the_same_rule(monkeypatch):
    """업종 대분류도 같은 읽기를 쓴다 — 과거 기준일은 그 날 확정된 사업연도, 없으면 빈칸, 최신은 그대로."""
    monkeypatch.setattr(S, "_pg_rows", fake_db())

    def energy(args):
        j = json.loads(call({**args, "scope": "sector", "scheme": "대분류", "format": "json"}))["data"]
        return j, next(s for s in j["sectors"] if s["label"] == "에너지")
    j, e = energy({})
    assert e["div_fiscal_year"] == 2025 and e["div_yield_pct_all"] == 5.3 and "actual_basis_dd" not in j["div_yield_ruler"]
    j, e = energy({"as_of": "20250829"})
    assert j["as_of"] == "20250829" and e["div_fiscal_year"] == 2024 and e["div_yield_pct_all"] == 4.2
    assert j["div_yield_ruler"]["actual_basis_dd"] == "20250829"
    j, e = energy({"as_of": "20250401"})              # 이 축이 답한 스냅샷(20250328) 기준 — 요청일 기준이면 FY2024 가 섞인다
    assert j["as_of"] == "20250328" and e["div_fiscal_year"] == 2023 and e["div_yield_pct_all"] == 3.1
    j, e = energy({"as_of": "20201030"})
    assert "div_yield_pct_all" not in e and "FY2023 부터" in j["div_yield_ruler"]["actual_note_empty"]
    out = call({"scope": "sector", "scheme": "대분류", "as_of": "20201030"})
    assert "⚠️ 기준일 20201030 까지 확정·공시된 사업연도는 FY2019" in out and "5.30" not in out


def test_failure_still_says_lookup_failed_not_blank_by_rule(monkeypatch):
    """조회 실패(None)는 「그 시점 집계 없음」이 아니다 — 종전 실패 안내 그대로, 빈칸 까닭을 지어내지 않는다."""
    monkeypatch.setattr(S, "_pg_rows", fake_db(div_fail=True))
    j, by = mkt({"as_of": "20250829"})
    r = j["div_yield_ruler"]
    assert "조회 실패" in r["actual_error"] and "actual_note_empty" not in r and "div_yield_pct_all" not in by["KS"]
