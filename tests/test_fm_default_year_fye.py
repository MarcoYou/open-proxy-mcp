"""financial_metrics 의 기본 사업연도(year 미지정 summary·yearly·yoy·audit_opinion·accounts)는 결산월을 안다 (261019). network 0 · DB 0.

종전 `_default_recent_year()` 는 모든 회사에 12월 결산 규칙(4월 이후 전년)을 써서, 12월 결산이 아닌 회사는 이미 낸 최신 사업보고서를
건너뛰고 한 해 앞 것을 요약했다 — 10-19 운영 JTC(950170, 2월 결산) 요약이 2026.02 사업보고서(2026-05-28 제출)를 두고 FY2025(2025.02).
이제 `report_calendar.latest_annual`(분기·연간 재무 수집·배수 도구와 같은 규칙). 12월 결산·결산월 모름은 종전과 같다.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest

from open_proxy_mcp.dart import client as dart_client
from open_proxy_mcp.dart.client import DartClientError
from open_proxy_mcp.services import financial_metrics as FM
from open_proxy_mcp.services.company import CompanyResolution
from open_proxy_mcp.services.contracts import AnalysisStatus


def _old_rule(t: date) -> int:
    return t.year - 1 if t.month >= 4 else t.year - 2


def _at(monkeypatch, t: date):
    monkeypatch.setattr(FM, "today_kst", lambda: t)


def test_december_and_unknown_fiscal_month_are_unchanged_every_day(monkeypatch):
    t = date(2016, 1, 1)
    while t <= date(2031, 12, 31):
        _at(monkeypatch, t)
        want = _old_rule(t)
        assert FM._default_recent_year() == want
        assert FM._default_recent_year(12, "005930") == want
        assert FM._default_recent_year(None, "950170") == want        # 결산월 모름 → 12월
        assert FM._default_recent_year(12, "950160") == want           # 12월 결산 외국 상장사도 종전 그대로
        t += timedelta(days=1)


@pytest.mark.parametrize("today,fm,ticker,want", [
    # JTC — 2월 결산 외국 상장사(사업보고서 120일 → 결산월 다음 다섯째 달 1일 = 7/1)
    (date(2026, 10, 19), 2, "950170", 2026),
    (date(2026, 6, 30), 2, "950170", 2025),
    (date(2026, 7, 1), 2, "950170", 2026),
    # 3월 결산 국내(90일 → 다음 넷째 달 1일 = 7/1)
    (date(2026, 10, 19), 3, "012345", 2026),
    (date(2026, 6, 30), 3, "012345", 2025),
    # 6월 결산 외국 상장사(프레스티지바이오파마 등 — 11/1 부터 2026.06)
    (date(2026, 10, 19), 6, "950210", 2025),
    (date(2026, 11, 1), 6, "950210", 2026),
    # 6월 결산 국내(10/1 부터)
    (date(2026, 10, 19), 6, "012345", 2026),
])
def test_non_december_uses_its_fiscal_month(monkeypatch, today, fm, ticker, want):
    _at(monkeypatch, today)
    assert FM._default_recent_year(fm, ticker) == want


JTC = {"corp_code": "01041828", "corp_name": "JTC", "stock_code": "950170"}


async def _resolve(query, *args, **kwargs):
    return CompanyResolution(status=AnalysisStatus.EXACT, query=query, selected=dict(JTC), candidates=[])


@pytest.mark.parametrize("acc_mt,want", [("02", 2026), ("12", 2025), ("", 2025)])
def test_summary_without_year_asks_dart_for_the_fiscal_month_year(monkeypatch, acc_mt, want):
    """도구 경로 — 회사 개황의 결산월로 기본 연도를 고르고 그 해 보고서를 묻는다(개황을 못 받으면 12월 = 종전)."""
    _at(monkeypatch, date(2026, 10, 19))
    asked: list[str] = []
    monkeypatch.setenv("OPENDART_API_KEY", "test-key-not-real")

    async def no_data(self, endpoint, params):          # 그 밖의 DART 호출(지표·감사의견 등)은 모두 「자료 없음」
        raise DartClientError("013", "가짜 응답")
    monkeypatch.setattr(dart_client.DartClient, "_request", no_data)

    async def company_info(cc):
        return ({"acc_mt": acc_mt, "induty_code": "471"} if acc_mt else {}), (None if acc_mt else "개황 실패")

    async def acnt(cc, year, rc, fs):
        asked.append(str(year))
        return [], "no_filing"

    monkeypatch.setattr(FM, "resolve_company_query", _resolve)
    monkeypatch.setattr(FM, "_safe_company_info", company_info)
    monkeypatch.setattr(FM, "_safe_fetch_acnt", acnt)
    monkeypatch.setattr(FM, "_FM_CACHE", type(FM._FM_CACHE)(16 * 1024 * 1024, 300.0, "test-fm-year"))
    p = asyncio.run(FM.build_financial_metrics_payload("JTC", scope="summary"))
    assert p["data"]["year"] == want
    assert asked and asked[0] == str(want)


# ── 반기·분기 결산 리츠 — 기본 연도는 이미 제출된 가장 새 사업보고서 (261021) ─────────────────────────
#   롯데리츠(330590)는 결산월이 12 로 잡히지만 6월·12월에 사업보고서를 따로 낸다. 10-21 기본 연도는 2025(제14기 2025.07~12)인데
#   2026.06 사업보고서(제15기)가 9/21 에 이미 제출돼 있다 — 12월 결산이 아닌 회사가 최신 보고서를 건너뛰던 JTC(위)와 같은 꼴.

LOTTE_REIT = {"corp_code": "01363818", "corp_name": "롯데리츠", "stock_code": "330590"}


def _asked_years_for(monkeypatch, company, latest):
    _at(monkeypatch, date(2026, 10, 21))
    asked: list[str] = []
    monkeypatch.setenv("OPENDART_API_KEY", "test-key-not-real")

    async def no_data(self, endpoint, params):
        raise DartClientError("013", "가짜 응답")
    monkeypatch.setattr(dart_client.DartClient, "_request", no_data)

    async def resolve(query, *args, **kwargs):
        return CompanyResolution(status=AnalysisStatus.EXACT, query=query, selected=dict(company), candidates=[])

    async def company_info(cc):
        return {"acc_mt": "12", "induty_code": "681"}, None

    async def acnt(cc, year, rc, fs):
        asked.append(str(year))
        return [], "no_filing"

    async def latest_filed(corp_code, as_of):
        calls.append(as_of)
        if isinstance(latest, Exception):
            raise latest
        return latest

    calls: list[str] = []
    monkeypatch.setattr(FM, "resolve_company_query", resolve)
    monkeypatch.setattr(FM, "_safe_company_info", company_info)
    monkeypatch.setattr(FM, "_safe_fetch_acnt", acnt)
    monkeypatch.setattr(FM, "_lookup_induty_code", lambda *a, **k: "681")
    monkeypatch.setattr(FM, "latest_annual_report_before", latest_filed)
    monkeypatch.setattr(FM, "_FM_CACHE", type(FM._FM_CACHE)(16 * 1024 * 1024, 300.0, "test-fm-reit-year"))
    p = asyncio.run(FM.build_financial_metrics_payload(company["corp_name"], scope="summary"))
    return p, asked, calls


@pytest.mark.parametrize("latest,want", [
    ({"report_nm": "사업보고서 (2026.06)", "rcept_dt": "20260921"}, 2026),   # 6월 결산기 사업보고서가 이미 제출 — 12월 규칙의 2025 보다 새 것
    ({"report_nm": "[기재정정]사업보고서 (2026.06)", "rcept_dt": "20261005"}, 2026),
    ({"report_nm": "사업보고서 (2025.12)", "rcept_dt": "20260310"}, 2025),   # 12월 결산기 보고서가 가장 새 것 — 종전과 같다
    ({"report_nm": "사업보고서 (2024.12)", "rcept_dt": "20250310"}, 2025),   # 옛 보고서(정정 등)가 기본 연도를 끌어내리지 않는다
    (None, 2025),                                                          # 제출 목록에 없음 — 종전 그대로
    (RuntimeError("list down"), 2025),                                      # 목록 조회 실패 — 종전 그대로(fail-open)
])
def test_reit_summary_without_year_follows_the_latest_filed_annual_report(monkeypatch, latest, want):
    p, asked, calls = _asked_years_for(monkeypatch, LOTTE_REIT, latest)
    assert p["data"]["year"] == want and asked and asked[0] == str(want)
    assert calls == ["20261021"]                                           # 목록은 오늘까지 제출분 한 번


def test_non_reit_never_asks_for_the_filing_list(monkeypatch):
    """리츠가 아닌 회사의 기본 연도는 종전 그대로이고 DART 목록 조회를 늘리지 않는다."""
    p, asked, calls = _asked_years_for(monkeypatch, JTC, {"report_nm": "사업보고서 (2026.06)"})
    assert calls == [] and p["data"]["year"] == 2025 and asked[0] == "2025"
