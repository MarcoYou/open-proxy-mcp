# -*- coding: utf-8 -*-
"""dividend_data — 질의 **하나만** 실패하는 부분 장애를 「없다」나 예외로 내지 않는다 (260903 점검).

`tests/test_dividend_raw_cells.py` 가 「결정공시 집계 전체 실패 → 모른다」를 잠갔다면,
여기서는 그 옆 구멍들이다 — 매칭 수 COUNT 만 실패하면 `len(rows) < None` 으로 도구가
죽었고, 연간 원장만 실패하면 「원장이 이 구간에 없다」, 이력열만 실패하면 전 회사가
「상장 전」(`-`)으로 읽혔다. network 0콜 · DB 0콜(전부 monkeypatch).
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

from open_proxy_mcp.services import dividend_data as dd
from open_proxy_mcp.tools import dividend_data as dd_tool

ROOT = pathlib.Path(__file__).resolve().parent.parent
_YEARS = [2020, 2021, 2022, 2023, 2024]
_ROW = {"corp_code": "00126380", "name": "삼성전자", "ticker": "005930", "dps_krw": 1444.0,
        "div_total_krw": 9_809_438_000_000.0, "payout_pct": 67.8, "rcept_no": "20250311000001"}


# ── screen ───────────────────────────────────────────────────────────────
def test_screen_render_survives_a_failed_match_count():
    """매칭 수 COUNT 만 실패 → 예외가 아니라 「세지 못했다」. 실은 수를 매칭 수로 읽히게 두지 않는다."""
    d = {"status": "ok", "rows": [_ROW], "matched": None, "n_universe": None,
         "returned": 1, "limit": 50}
    md = dd_tool._render_screen(2024, [], d, 50, _YEARS, {})
    assert "세지 못했다" in md and "모집단 조회 실패" in md
    assert "None사" not in md
    assert "조건에 걸린 회사 1사" not in md, "실은 수를 매칭 수로 찍었다"
    assert "조건에 맞는 회사가 없다" not in md


def test_screen_render_marks_a_failed_history_lookup_instead_of_pre_listing():
    """이력열 질의 실패(`hist=None`)는 `-`(상장 전)가 아니라 `?`(모른다)다."""
    d = {"status": "ok", "rows": [_ROW], "matched": 1, "n_universe": 700, "returned": 1, "limit": 50}
    md = dd_tool._render_screen(2024, [], d, 50, _YEARS, None)
    assert "| ? |" in md and "이력열을 읽지 못했다" in md
    ok = dd_tool._render_screen(2024, [], d, 50, _YEARS, {"00126380": [4, 4, 4, 4, 4]})
    assert "| 4·4·4·4·4 |" in ok and "읽지 못했다" not in ok


def test_screen_legend_says_dash_means_unknown_not_only_pre_listing():
    d = {"status": "ok", "rows": [_ROW], "matched": 1, "n_universe": 700, "returned": 1, "limit": 50}
    md = dd_tool._render_screen(2024, [], d, 50, _YEARS, {"00126380": [None, None, 2, 4, 4]})
    assert "상장 여부를 모른다" in md and "krx_listing" in md


def test_payment_history_returns_none_on_query_failure(monkeypatch):
    """질의 실패를 「전 회사 상장 전」 배열로 메우지 않는다."""
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): None)
    assert dd.payment_history([("00126380", "005930")], 2020, 2024) is None


def test_payment_history_distinguishes_zero_unknown_and_count(monkeypatch):
    rows = [
        ("A", 2020, None, 12, "20191230", False),   # 상장 중 · 결의 없음 → 0
        ("A", 2021, 2, 12, "20191230", False),      # 결의 2건
        ("B", 2020, None, 12, "20210315", False),   # FY2020 말일엔 상장 전 → null
        ("B", 2021, 1, 12, "20210315", False),
        ("C", 2020, None, 12, None, None),          # krx_listing 에 없음 → null
        ("C", 2021, None, 12, None, None),
    ]
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): rows)
    out = dd.payment_history([("A", "1"), ("B", "2"), ("C", None)], 2020, 2021)
    assert out == {"A": [0, 2], "B": [None, 1], "C": [None, None]}


# ── firm ─────────────────────────────────────────────────────────────────
def _firm_with(monkeypatch, annual, quarterly):
    def _rows(sql, params=()):
        return annual if "FROM div_declared" in sql else quarterly
    monkeypatch.setattr(dd, "_rows", _rows)
    return dd.firm_history("00126380", 2020, 2025)


def test_firm_history_flags_a_failed_annual_query_without_dropping_quarterly(monkeypatch):
    qtr = [(2024, "11012", "Q2", 2_450_000_000_000, 2_450_000_000_000, None, "확정", "20240814000001")]
    d = _firm_with(monkeypatch, None, qtr)
    assert d["status"] == "ok"
    assert d["annual_failed"] is True and d["quarterly_failed"] is False
    assert len(d["quarterly"]) == 1 and d["annual"] == []


def test_firm_history_both_failed_is_db_error(monkeypatch):
    assert _firm_with(monkeypatch, None, None) == {"status": "db_error"}


def test_firm_render_says_unknown_not_absent_when_only_the_annual_query_failed():
    pay = {"status": "ok", "complete_years": _YEARS, "rows": [], "decisions": []}
    d = {"status": "ok", "annual_failed": True, "quarterly_failed": False,
         "empty_kind_rows_folded": 0, "annual": [], "quarterly": []}
    md = dd_tool._render_firm("삼성전자", "005930", d, pay)
    assert "연간 원장을 읽지 못했다" in md
    assert "원장이 이 구간에 없다" not in md
    d2 = {**d, "annual_failed": False, "quarterly_failed": True}
    md2 = dd_tool._render_firm("삼성전자", "005930", d2, pay)
    assert "분기 원장을 읽지 못했다" in md2 and "원장 분기 확정 없음" not in md2


def test_firm_render_still_says_absent_when_the_query_succeeded_with_nothing():
    pay = {"status": "ok", "complete_years": _YEARS, "rows": [], "decisions": []}
    d = {"status": "ok", "annual_failed": False, "quarterly_failed": False,
         "empty_kind_rows_folded": 0, "annual": [], "quarterly": []}
    md = dd_tool._render_firm("테스트", "000000", d, pay)
    assert "원장이 이 구간에 없다" in md and "읽지 못했다" not in md


# ── dividend_disclosure 결정공시 합산 경로의 자(尺) ────────────────────────
def test_decisions_summary_cash_dps_already_contains_the_special_portion():
    """결정공시의 「1주당 배당금」은 정기·특별분이 합산된 한 숫자다 — `total_dps = cash_dps`,
    `special_dps` 는 그중 특별분(정보용). 원장 경로(`cash + special`)와 자가 다르다는 것을
    주석뿐 아니라 코드로 잠근다(삼성전자 FY2020: 1,932 중 1,578)."""
    from open_proxy_mcp.services.dividend import _decisions_summary_for_year
    s = _decisions_summary_for_year([{
        "rcept_dt": "2021-01-28", "rcept_no": "20210128800069", "record_date": "2020-12-31",
        "dividend_type": "결산배당", "dps_common": 1932, "dps_preferred": 1933,
        "total_amount": 13_124_000_000_000, "has_special": True, "special_dps_krw": 1578,
    }], 2020, 12)
    assert s["cash_dps"] == 1932 and s["total_dps"] == 1932
    assert s["special_dps"] == 1578 and s["special_dps"] <= s["cash_dps"]
    assert s["source"] == "decisions"


# ── 카탈로그 검사가 없는 도구명을 잡는다 ──────────────────────────────────
def test_tool_catalog_check_rejects_retired_tool_names_in_descriptions():
    """`ref:`·`when:` 이 `dividend`(260902 은퇴)를 가리키던 16곳의 회귀 가드 — 검사가
    통과해야 하고, 은퇴한 이름을 다시 넣으면 그 검사가 잡아야 한다."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import check_tool_catalog as c

    class _T:
        def __init__(self, name, description):
            self.name, self.description = name, description

    runtime = {"dividend_disclosure", "dividend_data", "evidence"}
    ok = _T("x", "desc: ..\n        when: 배당 상세는 `dividend_disclosure`.\n        ref: dividend_disclosure, evidence")
    assert c._description_problems([ok], runtime) == []
    bad = _T("y", "when: 배당 상세는 `dividend`.\n        ref: dividend, evidence (유형별 심층)")
    probs = c._description_problems([bad], runtime)
    assert any("은퇴한 도구명" in p and "dividend" in p for p in probs)
    assert any("런타임에 없는 도구 dividend" in p for p in probs)
    # 코드 목록은 도구명이 아니다 — screener 의 유형 코드 `dividend` 를 오탐하지 않는다.
    codes = _T("screener", "types: order,treasury,dividend,dilutive\n        ref: evidence")
    assert c._description_problems([codes], runtime) == []
    # 산문도 도구명이 아니다(`evidence`: 「모든 data/action tool」). 밑줄 든 오타는 잡는다.
    prose = _T("evidence", "ref: 모든 data/action tool")
    assert c._description_problems([prose], runtime) == []
    typo = _T("z", "ref: dividend_disclosur, evidence")
    assert any("dividend_disclosur" in p for p in c._description_problems([typo], runtime))

    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_tool_catalog.py")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
