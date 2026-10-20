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
        if "EXISTS" in sql:
            return [(True,)]
        return [tuple(r[c] for c in fe._GAP_COLS) for r in SAMSUNG]

    monkeypatch.setattr(fe, "pg_rows", fake)
    g = fe.quarter_consensus_gap("005930", "2026-09-30", "20261008", SAMSUNG_ACTUAL, True)
    assert g["status"] == "ok" and g["as_of"] == "2026-09-26"
    assert seen[0][1] == ("005930", dt.date(2026, 9, 30), 364)          # 창 안만(52주 + 기준일)
    assert "period_type='Q'" in seen[0][0]
    assert seen[1][1] == (dt.date(2026, 10, 8), 364)


# ── 지표 칸 ───────────────────────────────────────────────────────────────────
def test_metric_missing_on_either_side_says_why():
    rows = [_snap("2026-10-10", rev=None, op=30, ni=7)]      # 한진 10-10 꼴: 매출·순이익 칸이 비었다
    g = _gap({"revenue": 889.8, "operating_profit": 32.6, "net_income_controlling": None},
             filed="20261012", rows=rows)
    m = g["metrics"]
    assert g["status"] == "ok"
    assert "비어 있다" in m["revenue"]["why"] and "gap_pct" not in m["revenue"]
    assert m["operating_profit"]["gap_pct"] == 8.67
    assert "지배주주순이익" in m["net_income_controlling"]["why"]


def test_nothing_overlaps_is_not_comparable():
    rows = [_snap("2026-10-10", op=30)]
    g = _gap({"revenue": 100}, filed="20261012", rows=rows)
    assert g["status"] == "not_comparable" and "함께 있는 지표" in g["why"]


def test_loss_consensus_keeps_direction_and_warns():
    rows = [_snap("2026-10-10", op=-20)]
    g = _gap({"operating_profit": 10}, filed="20261012", rows=rows)
    c = g["metrics"]["operating_profit"]
    assert c["gap_pct"] == 150.0 and c["diff_krw"] == 30 and "방향만" in c["gap_caveat"]


def test_zero_consensus_has_diff_but_no_pct():
    rows = [_snap("2026-10-10", op=0)]
    c = _gap({"operating_profit": 10}, filed="20261012", rows=rows)["metrics"]["operating_profit"]
    assert c["diff_krw"] == 10 and "gap_pct" not in c


def test_stale_snapshot_gets_caveat():
    rows = [_snap("2026-09-12", rev=100)]
    g = _gap({"revenue": 100}, filed="20261008", rows=rows)
    assert g["lag_days"] == 26 and any("26일 전" in c for c in g["caveats"])


# ── ④ DB 전에 거르기 ──────────────────────────────────────────────────────────
def _parsed(**kw):
    base = {"kind": "financial", "period_kind": "quarter", "cumulative": False, "consolidated": True,
            "period": {"start": "2026-07-01", "end": "2026-09-30"},
            "headline": {"revenue": {"value_krw": 195 * JO}, "operating_profit": {"value_krw": 107.4 * JO}}}
    base.update(kw)
    return base


def test_gating_before_db():
    assert pe._gap_not_applicable(_parsed(), "005930") is None
    cases = {
        "월간": (_parsed(period_kind="month"), "005930", "not_comparable"),
        "연간": (_parsed(period_kind="annual"), "005930", "not_comparable"),
        "누적": (_parsed(cumulative=True), "005930", "not_comparable"),
        "비재무": (_parsed(kind="non_financial"), "005380", "not_comparable"),
        "종목코드 없음": (_parsed(), "", "out_of_coverage"),
        "기간 모름": (_parsed(period_kind=None, period=None), "005930", "unknown"),
    }
    for name, (parsed, code, status) in cases.items():
        got = pe._gap_not_applicable(parsed, code)
        assert got and got["status"] == status, name


def _capture(monkeypatch, ret=None):
    seen = {}

    def fake(stock_code, period_end, filed, actual, consolidated):
        seen.update(stock_code=stock_code, period_end=period_end, filed=filed, actual=actual,
                    consolidated=consolidated)
        return dict(ret or {"status": "ok", "as_of": "2026-09-26"})

    monkeypatch.setattr(fe, "quarter_consensus_gap", fake)
    return seen


def test_monthly_filing_never_queries(monkeypatch):
    seen = _capture(monkeypatch)
    g = asyncio.run(pe._consensus_gap(_parsed(period_kind="month"), "004170", "20261008"))
    assert g["status"] == "not_comparable" and seen == {}


def test_separate_net_income_stands_in_for_controlling(monkeypatch):
    seen = _capture(monkeypatch)
    head = {"revenue": {"value_krw": 100}, "net_income": {"value_krw": 9}}
    asyncio.run(pe._consensus_gap(_parsed(consolidated=False, headline=head), "000001", "20261015"))
    assert seen["actual"]["net_income_controlling"] == 9 and seen["consolidated"] is False


def test_consolidated_net_income_is_not_used_as_controlling(monkeypatch):
    seen = _capture(monkeypatch)
    head = {"revenue": {"value_krw": 100}, "net_income": {"value_krw": 9}}
    asyncio.run(pe._consensus_gap(_parsed(headline=head), "000001", "20261015"))
    assert seen["actual"]["net_income_controlling"] is None
    assert seen["period_end"] == "2026-09-30" and seen["filed"] == "20261015"


def test_correction_filing_gets_caveat(monkeypatch):
    _capture(monkeypatch)
    g = asyncio.run(pe._consensus_gap(_parsed(correction=True), "005930", "20261020"))
    assert any("정정 공시" in c for c in g["caveats"])


# ── ⑤ 원문은 그대로 · 실패해도 잠정 실적은 나간다 ─────────────────────────────
_HTML = """<html><body>
<p>연결재무제표 기준 영업(잠정)실적(공정공시)</p><p>2026-07-01 ~ 2026-09-30</p>
<table>
<tr><td>1. 연결실적내용</td><td></td><td></td><td></td><td>단위 : 억원, %</td></tr>
<tr><td>구분</td><td>구분</td><td>당기실적</td><td>전기실적</td><td>전기대비 증감율(%)</td><td>흑자적자전환여부</td>
<td>전년동기실적</td><td>전년동기대비 증감율(%)</td><td>흑자적자전환여부</td></tr>
<tr><td>매출액</td><td>당해실적</td><td>4,399</td><td>4,200</td><td>4.7</td><td>-</td><td>4,100</td><td>7.3</td><td>-</td></tr>
<tr><td>영업이익</td><td>당해실적</td><td>48</td><td>60</td><td>-20.0</td><td>-</td><td>70</td><td>-31.4</td><td>-</td></tr>
</table></body></html>"""


class _Client:
    async def search_filings(self, **kw):
        if kw.get("pblntf_detail_ty") != "I002":
            return {"list": []}
        return {"list": [{"rcept_no": "20261016800456", "rcept_dt": "20261016",
                          "report_nm": "연결재무제표기준영업(잠정)실적(공정공시)"}]}

    async def get_document_cached(self, rcept_no):
        return {"html": _HTML}

    async def get_company_info(self, corp_code):
        return {"acc_mt": "12"}


def _builder(monkeypatch, gap):
    async def resolve(q):
        return SimpleNamespace(status="exact", selected={"corp_name": "국도화학", "corp_code": "00113067",
                                                         "stock_code": "007690"}, candidates=[])
    monkeypatch.setattr(pe, "resolve_company_query", resolve)
    import open_proxy_mcp.dart.client as dc
    monkeypatch.setattr(dc, "get_dart_client", lambda: _Client())
    monkeypatch.setattr(fe, "quarter_consensus_gap", gap)
    return asyncio.run(pe.build_provisional_earnings_payload("국도화학"))


def test_payload_keeps_headline_and_adds_gap(monkeypatch):
    seen = {}

    def gap(stock_code, period_end, filed, actual, consolidated):
        seen.update(actual=actual)
        return fe.compute_consensus_gap(actual, filed=filed, consolidated=consolidated, history_reaches=True,
                                        rows=[_snap("2026-10-10", rev=465_200_000_000, op=27_200_000_000)])
    p = _builder(monkeypatch, gap)
    d = p["data"]
    assert d["headline"]["revenue"]["value_krw"] == 4399 * 1e8          # 원문 값 그대로
    assert d["headline"]["operating_profit"]["yoy_pct"] == -31.4
    g = d["consensus_gap"]
    assert g["status"] == "ok" and g["as_of"] == "2026-10-10" and g["lag_days"] == 6
    assert g["metrics"]["operating_profit"]["gap_pct"] == -82.35
    assert seen["actual"]["revenue"] == 4399 * 1e8
    md = T._render(p)
    assert "**컨센서스 대비** (공시 6일 전 2026-10-10" in md and "vs 예상 272억 (-82.4%)" in md          # (48억−272억)÷272억 = −82.353% — 한 번만 반올림


def test_gap_crash_does_not_break_payload(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    p = _builder(monkeypatch, boom)
    assert p["status"] == "exact" and p["data"]["headline"]["revenue"]["value_krw"] == 4399 * 1e8
    assert "consensus_gap" not in p["data"]


def test_not_compared_leaves_only_parsed_values(monkeypatch):
    # 모르거나 없거나 맞댈 수 없으면 컨센서스 칸·줄 없이 파싱 값만(261018 결정).
    for status in ("out_of_coverage", "out_of_history", "unknown", "not_comparable"):
        p = _builder(monkeypatch, lambda *a, _s=status, **k: {"status": _s, "why": "이유", "as_of": "2026-10-10"})
        assert "consensus_gap" not in p["data"], status
        assert p["data"]["headline"]["revenue"]["value_krw"] == 4399 * 1e8
        md = T._render(p)
        assert "컨센서스" not in md, status


def test_render_non_ok_is_empty():
    for status in ("out_of_coverage", "out_of_history", "unknown", "not_comparable"):
        assert T._render_gap({"status": status, "why": "이유"}) == ""
    assert T._render_gap(None) == ""


def test_md_percent_rounds_once_from_raw_values():
    # 국도화학 2026.09 매출: (4,398.57억 − 4,652억) ÷ 4,652억 = −5.4478% → 「−5.4%」. 저장값 −5.45 를 다시 깎으면 −5.5.
    c = {"actual_krw": 439_857_000_000, "consensus_krw": 465_200_000_000,
         "diff_krw": 439_857_000_000 - 465_200_000_000, "gap_pct": -5.45}
    line = T._render_gap({"status": "ok", "as_of": "2026-10-10", "lag_days": 6, "period": "2026.09E",
                          "basis": "IFRS연결", "metrics": {"revenue": c}})
    assert "(-5.4%)" in line and "(-5.5%)" not in line
