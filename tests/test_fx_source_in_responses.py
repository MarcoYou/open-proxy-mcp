"""도구 응답의 환율 출처 칸(261019) — 값마다 "ecos"(한국은행 매매기준율) / "yahoo"(야후 대체값)를 적고, 대체값이면 경고한다.

종전엔 출처 칸이 없었고 배수 도구의 explain 은 출처를 늘 「한국은행 ECOS」로 적었다 — 10-19 운영 JTC FY0 환율 9.1907 은 야후 종가였다
(ECOS 9.1338). network 0 · DB 0.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from open_proxy_mcp.dart import fx
from open_proxy_mcp.services import financial_metrics as fm
from open_proxy_mcp.tools import price_multiple_data as TP

from tests.test_pmd_found_defects import FX, USD_REPORTS, _run_live


# ── price_multiple_data firm ─────────────────────────────────────────────────────────────────────────────
def test_firm_marks_each_rate_with_its_source_and_warns_on_the_fallback_date(monkeypatch):
    p, _ = _run_live(monkeypatch, reports=USD_REPORTS, fx=FX, induty="29241", fx_src={"20251231": "yahoo"})
    rates = p["data"]["inputs"]["fx_rates_to_krw"]
    assert rates["fy0"]["source"] == "yahoo"
    assert rates["quarter"]["source"] == "ecos" and rates["prior_quarter"]["source"] == "ecos"
    w = [x for x in p["data"]["warnings"] if "야후 파이낸스 종가로 대신한" in x]
    assert len(w) == 1 and w[0].startswith("⚠️ 2025-12-31 환율:")
    md = TP._render_explain_firm(p)
    assert "FY0 야후 파이낸스 종가(대체값)" in md and "최근 분기 한국은행 ECOS 매매기준율" in md


def test_firm_all_ecos_has_no_fallback_warning_and_names_ecos_once(monkeypatch):
    p, _ = _run_live(monkeypatch, reports=USD_REPORTS, fx=FX, induty="29241")
    assert {e["source"] for e in p["data"]["inputs"]["fx_rates_to_krw"].values()} == {"ecos"}
    assert not any("야후" in x for x in p["data"]["warnings"])
    md = TP._render_explain_firm(p)
    assert "(원/USD, 한국은행 ECOS 매매기준율)" in md and "야후" not in md


def test_quarter_rate_borrowed_from_the_fiscal_year_carries_that_dates_source(monkeypatch):
    """분기 말 환율을 못 받아 사업연도 말 환율로 대신하면, 분기 칸의 출처도 그 날짜(사업연도 말)의 출처다."""
    p, _ = _run_live(monkeypatch, reports=USD_REPORTS, fx={"20251231": 1434.9}, induty="29241", fx_src={"20251231": "yahoo"})
    rates = p["data"]["inputs"]["fx_rates_to_krw"]
    assert rates["quarter"] == {"date": "20251231", "rate": 1434.9, "source": "yahoo"}
    assert sum("야후 파이낸스 종가로 대신한" in x for x in p["data"]["warnings"]) == 1   # 같은 날짜는 한 번만


# ── financial_metrics ────────────────────────────────────────────────────────────────────────────────────
_ROWS = [{"account_id": "ifrs-full_Assets", "currency": "JPY", "thstrm_amount": "1000"}]


def _normalize(monkeypatch, source):
    monkeypatch.setattr(fm, "fx_to_krw", AsyncMock(return_value=9.19069290161133))
    monkeypatch.setattr(fm, "fx_source", lambda c, d=None: source)
    metrics = {"total_assets_krw": 1000}
    warnings = asyncio.run(fm._normalize_currency(metrics, list(_ROWS), "2026-02-28", 2026))
    return metrics, warnings


def test_annual_metrics_carry_the_rate_source(monkeypatch):
    m, w = _normalize(monkeypatch, "ecos")
    assert m["fx_rate_to_krw"] == 9.19 and m["fx_source"] == "ecos"
    assert m["total_assets_krw"] == round(1000 * 9.19069290161133)
    assert not any("야후" in x for x in w)


def test_annual_metrics_warn_when_the_rate_is_a_fallback(monkeypatch):
    m, w = _normalize(monkeypatch, "yahoo")
    assert m["fx_source"] == "yahoo"
    assert any(x.startswith("⚠️ 20260228 환율:") and "야후" in x for x in w)


def test_quarterly_rows_carry_the_rate_source(monkeypatch):
    rows = [
        {"account_nm": name, "account_id": identifier, "thstrm_amount": value,
         "fs_div": "CFS", "sj_div": section, "currency": "USD", "thstrm_dt": "2024.03.31"}
        for name, identifier, value, section in [
            ("자산총계", "ifrs-full_Assets", "1000", "BS"),
            ("매출액", "ifrs-full_Revenue", "100", "IS"),
            ("영업이익", "dart_OperatingIncomeLoss", "10", "IS"),
            ("당기순이익", "ifrs-full_ProfitLoss", "8", "IS"),
        ]
    ]

    async def statement(corp_code, year, reprt_code, fs_div):
        return {"status": "000", "list": rows if year == "2024" and reprt_code == "11013" else []}

    client = type("Client", (), {})()
    client.get_fnltt_singl_acnt = AsyncMock(side_effect=statement)
    client.get_fnltt_singl_acnt_all = AsyncMock(return_value={"status": "000", "list": []})
    monkeypatch.setattr(fm, "get_dart_client", lambda: client)
    monkeypatch.setattr(fm, "fx_to_krw", AsyncMock(return_value=1346.8))
    monkeypatch.setattr(fm, "fx_source", lambda c, d=None: "yahoo")
    quarters, warnings = asyncio.run(fm._build_quarterly("00100000", 2024, "CFS", fiscal_month=12))
    assert quarters and all(q["fx_source"] == "yahoo" and q["fx_rate_to_krw"] == 1346.8 for q in quarters)
    assert any("야후 파이낸스 종가로 대신한" in w for w in warnings)


def test_fallback_warning_text_has_no_engine_words():
    """출력 어휘 규칙 — 사용자 문장에 엔진 내부 영어 낱말(fallback·cache 등)을 쓰지 않는다."""
    low = fx.FALLBACK_WARNING.lower()
    assert "fallback" not in low and "cache" not in low and "ecos" in low
