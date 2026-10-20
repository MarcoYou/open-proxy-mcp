# -*- coding: utf-8 -*-
"""업종 비교(scope=sector + company)의 기업 행 — 3년 하한 이전 기준일은 콜드 서빙본의 같은 주 행으로 (261019, 콜드 서빙 뒤 발견).

운영 10-19(콜드 켬): 005930 `as_of=20210630` 에 `firm` + `as_of` 는 보관본을 이은 곡선의 20210625 점으로 답하는데, 같은 도구의 업종 비교는
기업 행을 빼고 「그 주에 시세가 없다(상장 전·거래정지·상장폐지)」라고 했다 — 그 날 시총을 DB(주간·일별)에서만 찾았고, 3년 하한 이전 주는
DB 에 연말 행만 남아 있다. 지키는 것
  ① DB 에 그 주 행이 없으면 서빙본의 같은 주 행(그 날 이하 가장 늦은 행, 없으면 그 날 뒤 가장 이른 행 — `_FIRM_CAP_SQL` 주간 갈래와 같은 순서)
  ② 그 시총으로 센 배수 = 같은 시총·같은 재무로 센 `_row_val`(곡선·업종 비교와 같은 정의)
  ③ DB 에 행이 있으면 서빙본을 보지 않는다(연말 행·3년 안 — 종전 그대로)
  ④ 못 찾았을 때 까닭: 서빙본까지 봤는데 없음·하한 이후 → 종전 「그 주에 시세가 없다」 / 하한 이전 + 꺼짐 → 보존 구간 밖 / 하한 이전 + 강등 → 일시
network 0 · DB 0 — 가짜 `_pg_rows`·재무 로더와 로컬 file:// 서빙본(`cold_serving` 고정물)."""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from open_proxy_mcp.services import price_multiple_data as S

SH = 5_969_782_550
TODAY = date(2026, 10, 21)          # 3년 하한(오늘의 달력 3년 전) = 20231021


def _c(dd, close, ticker="005930", market="KS"):
    """서빙본 한 행 — (ticker, price_dd, market, close, list_shrs)."""
    return (ticker, dd, market, close, SH)


COLD_ROWS = [_c("20210618", 80_000), _c("20210625", 81_000), _c("20210702", 80_500), _c("20211230", 78_300),
             _c("20231013", 68_000), _c("20210625", 9_000, ticker="000020")]
FIN = {2020: (26.09e12, 275.9e12)}
FINQ = {(2020, 1): (4.89e12, 260.0e12), (2021, 1): (7.09e12, 280.0e12)}


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setattr(S, "today_kst", lambda: TODAY)


def _db(cap_rows=(), fund=(("KS", "26410", "KRW", "ok", None),)):
    def rows(sql, params=()):
        s = " ".join(sql.split())
        if s.startswith("SELECT market, induty, currency, fetched, acc_mt FROM dart_fundamentals"):
            return list(fund)
        if "AS src" in s:
            return list(cap_rows)
        raise AssertionError(s)
    return rows


def _fin(monkeypatch):
    async def fy(*a, **k):
        return dict(FIN)

    async def fq(*a, **k):
        return dict(FINQ)
    monkeypatch.setattr(S, "_firm_fin_by_fy", fy)
    monkeypatch.setattr(S, "_firm_fin_by_q", fq)


# ══════════════════════════════════════════════════════════════════════
# ① 서빙본의 같은 주 행
# ══════════════════════════════════════════════════════════════════════
def test_cold_cap_at_picks_the_same_week_row(cold_serving):
    cold_serving(COLD_ROWS, upto="20231016", max_dd="20231015")
    assert asyncio.run(S._cold_cap_at("005930", "20210625")) == ("hit", 81_000.0 * SH, "KS"), "그 날 행"
    assert asyncio.run(S._cold_cap_at("005930", "20210627")) == ("hit", 81_000.0 * SH, "KS"), "일요일 → 같은 주 그 날 이하"
    assert asyncio.run(S._cold_cap_at("005930", "20210623")) == ("hit", 81_000.0 * SH, "KS"), "수요일 → 같은 주 그 날 뒤"
    assert asyncio.run(S._cold_cap_at("005930", "20210630")) == ("hit", 80_500.0 * SH, "KS"), "다음 주로 넘어가지 않는다"
    assert asyncio.run(S._cold_cap_at("000020", "20210625")) == ("hit", 9_000.0 * SH, "KS"), "종목이 섞이지 않는다"
    assert asyncio.run(S._cold_cap_at("005930", "20210611")) == ("miss", None, None), "그 주 행이 없다"
    assert asyncio.run(S._cold_cap_at("373220", "20210625")) == ("miss", None, None), "서빙본에 없는 종목(상장 전)"


def test_cold_cap_at_off_and_degraded(monkeypatch, cold_serving):
    assert asyncio.run(S._cold_cap_at("005930", "20210625")) == ("off", None, None), "기본 꺼짐 — 목록도 보지 않는다"
    cold_serving(COLD_ROWS, upto="20231016", max_dd="20231015", tamper_sha=True)
    assert asyncio.run(S._cold_cap_at("005930", "20210625")) == ("degraded", None, None), "검증 실패 → 강등"


def test_cold_cap_at_never_raises(monkeypatch, cold_serving):
    cold_serving(COLD_ROWS, upto="20231016", max_dd="20231015")

    async def boom(*a, **k):
        raise RuntimeError("목록 이상")
    monkeypatch.setattr(S._cold, "ready", boom)
    assert asyncio.run(S._cold_cap_at("005930", "20210625")) == ("degraded", None, None)


# ══════════════════════════════════════════════════════════════════════
# ② ③ 업종 비교 기업 행의 그 날 값
# ══════════════════════════════════════════════════════════════════════
def test_firm_val_at_counts_from_the_cold_row_when_the_db_has_none(monkeypatch, cold_serving):
    cold_serving(COLD_ROWS, upto="20231016", max_dd="20231015")
    _fin(monkeypatch)
    monkeypatch.setattr(S, "_pg_rows", _db(cap_rows=[]))
    sec, market, per_ttm, pbr_mrq, note = asyncio.run(S._firm_val_at("005930", "20210625"))
    want = S._row_val("20210625", 81_000.0 * SH, FIN, FINQ)
    assert (market, per_ttm, pbr_mrq, note) == ("KS", want["per_ttm"], want["pbr_mrq"], {})
    assert per_ttm == pytest.approx(81_000.0 * SH / (26.09e12 + 7.09e12 - 4.89e12))
    assert pbr_mrq == pytest.approx(81_000.0 * SH / 280.0e12)


def test_db_row_wins_and_the_cold_store_is_not_read(monkeypatch, cold_serving):
    cold_serving(COLD_ROWS, upto="20231016", max_dd="20231015")
    _fin(monkeypatch)
    monkeypatch.setattr(S, "_pg_rows", _db(cap_rows=[(78_300.0 * SH, "KS", 1)]))     # 연말 행(DB 에 남는다)

    async def no_cold(*a, **k):
        raise AssertionError("DB 에 행이 있으면 서빙본을 보지 않는다")
    monkeypatch.setattr(S, "_cold_cap_at", no_cold)
    _, _, per_ttm, _, _ = asyncio.run(S._firm_val_at("005930", "20211230"))
    assert per_ttm == S._row_val("20211230", 78_300.0 * SH, FIN, FINQ)["per_ttm"]


# ══════════════════════════════════════════════════════════════════════
# ④ 못 찾았을 때 까닭
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("cold,ticker,dd,why", [
    ("off", "005930", "20210625", "window"),          # 하한 이전 + 꺼짐 — 「상장 전·거래정지」가 아니다
    ("off", "005930", "20240105", "price"),           # 하한 이후 — 정말 그 주 시세가 없다(종전 문장)
    ("bad", "005930", "20210625", "cold_degraded"),   # 하한 이전 + 강등 — 일시
    ("bad", "005930", "20240105", "price"),
    ("on", "373220", "20210625", "price"),            # 서빙본까지 봤는데 없다 — 상장 전
])
def test_missing_reason(monkeypatch, cold_serving, cold, ticker, dd, why):
    if cold != "off":
        cold_serving(COLD_ROWS, upto="20231016", max_dd="20231015", tamper_sha=(cold == "bad"))
    _fin(monkeypatch)
    monkeypatch.setattr(S, "_pg_rows", _db(cap_rows=[]))
    assert asyncio.run(S._firm_val_at(ticker, dd)) == (None, None, None, None, {"missing": why})


@pytest.mark.parametrize("why,text", [
    ("window", "'삼성전자' 기업 행 없음 — 기준일이 주간 시세 보존 구간(최근 3년 + 해마다 연말 행) 밖이다 — 이 서버는 3년 이전 주간 시세 "
               "보관본을 쓰지 않는다. 연말 기준일(12월 마지막 거래일)이면 비교할 수 있다 — 기준일 20210625. 기업 배수·비교를 내지 않는다."),
    ("cold_degraded", "'삼성전자' 기업 행 없음 — 3년 이전 주간 시세 보관본을 지금 읽지 못했다(일시 — 잠시 뒤 다시 부르면 답한다) "
                      "— 기준일 20210625. 기업 배수·비교를 내지 않는다."),
    ("price", "'삼성전자' 기업 행 없음 — 그 주에 시세가 없다(상장 전·거래정지·상장폐지) — 기준일 20210625. 기업 배수·비교를 내지 않는다."),
])
def test_missing_reason_text(why, text):
    assert S._firm_row_missing("삼성전자", why, "20210625") == text


def test_weeks_outside_the_serving_coverage_are_not_read(monkeypatch, cold_serving):
    """3년 안에서 시세가 정말 없는 종목(거래정지 등)은 서빙본 범위 밖 주다 — 파일을 읽지 않고 「그 주에 시세가 없다」(종전)."""
    cold_serving(COLD_ROWS, upto="20231016", max_dd="20231015")
    reads = []
    real = S._cold.read

    async def spy(*a, **k):
        reads.append(a[3:])
        return await real(*a, **k)
    monkeypatch.setattr(S._cold, "read", spy)
    assert asyncio.run(S._cold_cap_at("005930", "20240105")) == ("miss", None, None)
    assert reads == []
    assert asyncio.run(S._cold_cap_at("005930", "20231013"))[0] == "hit" and reads == [("20231009", "20231016")]
