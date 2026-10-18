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
