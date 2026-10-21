# -*- coding: utf-8 -*-
"""provisional_earnings `consensus_gap` — 잠정실적 vs **공시 직전** 분기 컨센서스 (DB·네트워크 0콜).

지키는 것:
① 기준일 = 공시일 **전**(`as_of < 공시일`)의 가장 최근 스냅샷 — 공시 뒤에 고친 추정은 쓰지 않는다(삼성전자 2026.09).
② 연결/별도가 다르면 계산하지 않는다(대한항공 2026.09 별도 잠정 vs 연결 컨센서스 매출 −31.5% 가짜 차이).
③ 「없다」가 아니라 「모른다」 — 커버리지 밖 · 이력 밖 · DB 장애를 서로 다른 상태로 가른다(서비스).
   도구는 ok 일 때만 칸을 싣는다 — 모르거나 맞댈 수 없으면 칸 없이 파싱 값만(261018 결정).
④ 월간·연간·누적·비재무형은 DB 를 묻기 전에 거른다(261018 실측 39건 중 월간 26건).
⑤ 원문 값(headline)은 그대로 — 비교 칸만 더한다. 칸 계산이 터져도 잠정 실적은 나간다.
"""
from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace

from open_proxy_mcp.services import forward_estimates as fe
from open_proxy_mcp.services import provisional_earnings as pe
from open_proxy_mcp.tools import provisional_earnings as T

JO = 10**12


def _snap(as_of, *, rev=None, op=None, ni=None, est=True, basis="IFRS연결", period="2026.09E"):
    return {"as_of": dt.date.fromisoformat(as_of), "period": period, "is_estimate": est, "basis": basis,
            "rev_krw": rev, "op_krw": op, "ni_ctrl_krw": ni}


# 삼성전자 2026.09E 실제 스냅샷 꼴(운영 DB 261018) — 10-08 잠정 공시 뒤 10-10 에 추정이 내려왔다.
SAMSUNG = [
    _snap("2026-09-19", rev=204_764_640_000_000, op=111_377_180_000_000, ni=94_430_500_000_000),
    _snap("2026-09-26", rev=204_570_360_000_000, op=110_573_640_000_000, ni=90_526_380_000_000),
    _snap("2026-10-10", rev=200_765_740_000_000, op=106_943_530_000_000, ni=89_783_620_000_000),
]
SAMSUNG_ACTUAL = {"revenue": 195.00 * JO, "operating_profit": 107.40 * JO, "net_income_controlling": None}


def _gap(actual=SAMSUNG_ACTUAL, *, filed="20261008", consolidated=True, rows=SAMSUNG, reach=True):
    return fe.compute_consensus_gap(actual, filed=filed, consolidated=consolidated, rows=rows,
                                    history_reaches=reach)


# ── ① 기준일 ─────────────────────────────────────────────────────────────────
def test_uses_latest_snapshot_before_filing_not_after():
    g = _gap()
    assert g["status"] == "ok"
    assert g["as_of"] == "2026-09-26" and g["lag_days"] == 12      # 10-10 은 공시 뒤라 버린다
    assert g["period"] == "2026.09E" and g["basis"] == "IFRS연결"
    rev, op = g["metrics"]["revenue"], g["metrics"]["operating_profit"]
    assert rev["gap_pct"] == -4.68 and op["gap_pct"] == -2.87
    assert op["consensus_krw"] == 110_573_640_000_000 and op["actual_krw"] == 107_400_000_000_000
    assert op["diff_krw"] == 107_400_000_000_000 - 110_573_640_000_000
    assert "fwd_hist" in g["source"] and "DART 공시가 아니다" in g["source"]


def test_snapshot_on_filing_day_is_not_before():
    g = _gap(filed="20261010")                 # 같은 날 스냅샷은 공시를 봤을 수 있다 — 엄격히 「전」만
    assert g["as_of"] == "2026-09-26" and g["lag_days"] == 14


def test_iso_filing_date_accepted():
    assert _gap(filed="2026-10-08")["as_of"] == "2026-09-26"


# ── ② 연결/별도 ───────────────────────────────────────────────────────────────
def test_basis_mismatch_is_not_computed():
    g = _gap(consolidated=False)
    assert g["status"] == "not_comparable"
    assert "metrics" not in g and "별도" in g["why"] and "IFRS연결" in g["why"]
    assert g["as_of"] == "2026-09-26"          # 어느 스냅샷을 봤는지는 남긴다


def test_separate_basis_both_sides_is_compared():
    rows = [_snap("2026-10-10", rev=100, op=10, basis="IFRS별도")]
    g = _gap({"revenue": 110, "operating_profit": 12}, filed="20261015", consolidated=False, rows=rows)
    assert g["status"] == "ok" and g["metrics"]["revenue"]["gap_pct"] == 10.0


def test_unknown_basis_is_compared_with_caveat():
    rows = [_snap("2026-10-10", rev=100, basis=None)]
    g = _gap({"revenue": 90}, filed="20261015", rows=rows)
    assert g["status"] == "ok" and any("연결/별도" in c for c in g["caveats"])


# ── ③ 모른다 ──────────────────────────────────────────────────────────────────
def test_no_snapshot_before_filing_and_history_does_not_reach():
    g = _gap(filed="20260801", reach=False)
    assert g["status"] == "out_of_history" and "모른다" in g["why"]
    assert "as_of" not in g


def test_history_reaches_but_quarter_absent_is_out_of_coverage():
    g = _gap(rows=[], reach=True)
    assert g["status"] == "out_of_coverage" and "커버리지 밖" in g["why"]


def test_only_post_filing_snapshot_is_out_of_coverage():
    g = _gap(rows=[SAMSUNG[-1]], reach=True)   # 공시 뒤에야 추정이 생긴 종목
    assert g["status"] == "out_of_coverage"


def test_pre_filing_row_already_actual_is_not_consensus():
    g = _gap(rows=[_snap("2026-09-26", rev=1, est=False, period="2026.09A")])
    assert g["status"] == "out_of_coverage" and "실적 행" in g["why"]


def test_db_failure_is_unknown_not_absent(monkeypatch):
    monkeypatch.setattr(fe, "pg_rows", lambda sql, params=(): None)
    g = fe.quarter_consensus_gap("005930", "2026-09-30", "20261008", SAMSUNG_ACTUAL, True)
    assert g["status"] == "unknown" and "장애" in g["why"]


def test_fetch_passes_dates_and_reads_reach(monkeypatch):
    seen = []

    def fake(sql, params=()):
        seen.append((sql, params))
        # 스냅샷 행(k=0)과 「이력이 닿나」(k=1, 마지막 칸 EXISTS)를 한 질의로 읽는다(UNION ALL)
        return [(0, *(r[c] for c in fe._GAP_COLS), None) for r in SAMSUNG] + [(1, *([None] * len(fe._GAP_COLS)), True)]

    monkeypatch.setattr(fe, "pg_rows", fake)
    g = fe.quarter_consensus_gap("005930", "2026-09-30", "20261008", SAMSUNG_ACTUAL, True)
    assert g["status"] == "ok" and g["as_of"] == "2026-09-26"
    assert len(seen) == 1                                                  # 질의 하나
    assert seen[0][1] == ("005930", dt.date(2026, 9, 30), 364, dt.date(2026, 10, 8), 364)   # 창 안만(52주 + 기준일) · 공시일
    assert "period_type='Q'" in seen[0][0] and "EXISTS" in seen[0][0]
