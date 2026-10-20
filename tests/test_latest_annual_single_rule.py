# -*- coding: utf-8 -*-
"""「지금 확정된 최신 사업연도」(사업보고서 3월 공시 → 4월 이후 전년, 아니면 전전년)의 규칙은 `report_calendar.latest_annual` 한 곳이다(261021 DATA-FY1).

종전엔 세 파일이 같은 식을 따로 가졌다 — `market_fund_quarterly._latest_annual_fy`·`market_val_series._latest_annual_fy`·`price_multiple_data._latest_annual_fy`
(주석이 「동일 규칙」이라고만 적었다). 하나가 고쳐지면 나머지가 조용히 어긋나는 꼴이다. 이제 셋 다 `report_calendar.latest_annual(today, 12)` 의 얇은 껍데기다.
또 스크립트의 `date.today()` 는 서버·배치가 UTC 라 KST 자정~9시에 어제를 주므로(4월 1일 0~9시에 3월 31일로 읽힘) `today_kst()` 로 바꿨다.

DART 0콜 · DB 0.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import sys
from datetime import date, timedelta

import pytest

from open_proxy_mcp.services import price_multiple_data as S
from open_proxy_mcp.services import report_calendar as rcal
from scripts import market_fund_quarterly as Q

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "scripts"))
    spec.loader.exec_module(mod)
    return mod


def _old_rule(t: date) -> int:
    """세 파일이 따로 가졌던 종전 식 그대로."""
    return t.year - 1 if t.month >= 4 else t.year - 2


def _days(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def test_the_three_wrappers_equal_the_one_rule_every_day_2018_to_2030(monkeypatch):
    V = _load("market_val_series")
    for d in _days(date(2018, 1, 1), date(2030, 12, 31)):
        want = _old_rule(d)
        assert rcal.latest_annual(d, 12) == want, d
        assert Q._latest_annual_fy(d) == want, d
        assert V._latest_annual_fy(d) == want, d
    # 서버 쪽은 오늘(`today_kst`)을 읽는다 — 월 경계 앞뒤와 연말연초를 전부
    for d in list(_days(date(2018, 3, 25), date(2018, 4, 7))) + list(_days(date(2029, 12, 28), date(2030, 1, 5))) + [date(2026, 10, 21)]:
        monkeypatch.setattr(S, "today_kst", lambda d=d: d)
        assert S._latest_annual_fy() == _old_rule(d), d


def test_a_non_december_company_has_its_own_latest_annual_but_the_wrappers_stay_december():
    """껍데기는 12월 결산 기준(수집 목록 YEARS·재무 요약 덮어쓰기 자리) — 결산월은 `latest_annual(today, fye)` 를 쓴다."""
    d = date(2026, 10, 21)
    assert rcal.latest_annual(d, 12) == 2025 and Q._latest_annual_fy(d) == 2025
    assert rcal.latest_annual(d, 3) == 2026 and rcal.latest_annual(d, 6) == 2026       # 3·6월 결산은 올해 사업보고서가 이미 나와 있다


def test_wrappers_follow_the_korean_calendar(monkeypatch):
    """UTC 로는 3월 31일이어도 한국은 4월 1일 — 날짜를 안 주면 `today_kst` 를 읽어 사업보고서가 나온 새 사업연도로 센다."""
    V = _load("market_val_series")
    monkeypatch.setattr(Q, "today_kst", lambda: date(2026, 4, 1))
    monkeypatch.setattr(V, "today_kst", lambda: date(2026, 4, 1))
    monkeypatch.setattr(S, "today_kst", lambda: date(2026, 4, 1))
    assert Q._latest_annual_fy() == V._latest_annual_fy() == S._latest_annual_fy() == 2025
    monkeypatch.setattr(Q, "today_kst", lambda: date(2026, 3, 31))
    assert Q._latest_annual_fy() == 2024


def test_the_rule_lives_in_one_place():
    """세 함수 본문에 식이 다시 생기지 않았다 · 다섯 스크립트에 `date.today()` 가 남지 않았다(KST)."""
    for rel in ("scripts/market_fund_quarterly.py", "scripts/market_val_series.py", "open_proxy_mcp/services/price_multiple_data.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        body = re.search(r"def _latest_annual_fy\([^)]*\)[^\n]*:\n(.*?)\n\n\n", src, re.S).group(1)
        code = "\n".join(ln for ln in body.splitlines() if not ln.strip().startswith(('"""', '261', '세 파일', '(세')) )
        assert "latest_annual(" in body, rel
        assert "t.month >= 4" not in code and "t.year - 2" not in code, f"{rel}: 식이 다시 생겼다"
    for rel in ("scripts/market_fund_quarterly.py", "scripts/market_val_series.py", "scripts/market_val_history_backfill.py",
                "scripts/sector_val_backfill.py", "scripts/fundamentals_ddl.py"):
        assert "date.today()" not in (ROOT / rel).read_text(encoding="utf-8").replace("(date.today)", ""), rel
