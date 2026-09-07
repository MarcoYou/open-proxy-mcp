# -*- coding: utf-8 -*-
"""asset_holdings 의 report·year — 종전엔 사업보고서 첫 후보 고정(첨부정정도 집음). network 0."""
from __future__ import annotations

import asyncio

import pytest

from open_proxy_mcp.services import asset_holdings as A


def _reps():
    return [{"rcept_no": "20260313001226", "report_nm": "[첨부정정]사업보고서 (2025.12)", "rcept_dt": "20260313"},
            {"rcept_no": "20260312001399", "report_nm": "사업보고서 (2025.12)", "rcept_dt": "20260312"}]


@pytest.fixture
def fakes(monkeypatch):
    calls = []
    async def cands(client, corp_code, period):
        calls.append(("cands", period)); return _reps() if period in ("annual", "latest") else [
            {"rcept_no": "20260814003992", "report_nm": "반기보고서 (2026.06)", "rcept_dt": "20260814"}]
    async def by_year(client, corp_code, bsns_year, reprt_code):
        calls.append(("year", bsns_year, reprt_code))
        nm = {"11011": "사업보고서", "11012": "반기보고서", "11013": "분기보고서", "11014": "분기보고서"}[reprt_code]
        mm = {"11011": "12", "11012": "06", "11013": "03", "11014": "09"}[reprt_code]
        return [{"rcept_no": f"{bsns_year}{reprt_code}00001", "report_nm": f"{nm} ({bsns_year}.{mm})", "rcept_dt": f"{bsns_year}{mm}15"}]
    monkeypatch.setattr(A._bd, "_find_report_candidates", cands)
    monkeypatch.setattr(A._bd, "_find_report_for_bsns_year", by_year)
    return calls


def test_default_annual_skips_attachment_corrections(fakes):
    rept, code, err = asyncio.run(A._pick_periodic_report(object(), "00126380"))
    assert rept["rcept_no"] == "20260312001399" and code == "11011" and err == ""


def test_half_and_year_map_to_the_right_lookup_and_code(fakes):
    rept, code, _ = asyncio.run(A._pick_periodic_report(object(), "00126380", "half"))
    assert rept["report_nm"].startswith("반기보고서") and code == "11012" and fakes[-1] == ("cands", "half")
    rept, code, _ = asyncio.run(A._pick_periodic_report(object(), "00126380", "q3", 2025))
    assert fakes[-1] == ("year", "2025", "11014") and code == "11014"
    rept, code, _ = asyncio.run(A._pick_periodic_report(object(), "00126380", "q1", 2025))
    assert code == "11013"


def test_bad_report_value_is_an_error_not_a_guess(fakes):
    rept, code, err = asyncio.run(A._pick_periodic_report(object(), "00126380", "monthly"))
    assert rept is None and "annual·half·quarter" in err


def test_reprt_code_from_report_name():
    assert A._reprt_code_of({"report_nm": "사업보고서 (2025.12)"}) == "11011"
    assert A._reprt_code_of({"report_nm": "[기재정정]반기보고서 (2026.06)"}) == "11012"
    assert A._reprt_code_of({"report_nm": "분기보고서 (2026.03)"}) == "11013"
    assert A._reprt_code_of({"report_nm": "분기보고서 (2025.09)"}) == "11014"
