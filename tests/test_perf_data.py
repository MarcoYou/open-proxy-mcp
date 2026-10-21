"""성과 분석 읽기 계층(`services/perf_data.py`) — DB 없는 부분: 지수 이름 풀이·업종 구간 규칙·정정 배당 접기·콜드 이음 (261022).

DB 와 맞붙는 질의는 `test_perf_data_pg.py`(Postgres 17)가 증명한다. 이 시험은 모듈이 없던 main(39e6a672)에서 ImportError 로 실패한다.
network 0 · DB 0 — 가짜 `read_weekly_px` 와 로컬 file:// 서빙본(`cold_serving` 고정물).
"""
from __future__ import annotations

import asyncio

import pytest

from open_proxy_mcp.services import perf_data as D

S = D.IndexSeries


def _series():
    return [S(1, "P", "코스피", "z", True), S(2, "P", "코스피 200", "z", True), S(3, "P", "전기전자", "s", True),
            S(4, "Q", "코스닥", "z", True), S(5, "Q", "코스닥 150", "z", True), S(6, "Q", "전기전자", "s", True),
            S(7, "X", "KRX 300", "z", True), S(8, "X", "KRX 반도체", "s", False)]


# ── 지수 이름 풀이 ────────────────────────────────────────────────────────────────

def test_index_name_with_two_markets_asks_instead_of_guessing():
    r = D.resolve_index("전기전자", _series())
    assert r.hit is None and "코스피 전기전자" in r.question and "코스닥 전기전자" in r.question      # (P,'전기전자')·(Q,'전기전자') 둘


def test_index_name_prefixed_with_a_market_picks_that_market():
    q = D.resolve_index("코스닥 전기전자", _series())
    assert q.hit.sid == 6 and "코스닥" in q.note
    p = D.resolve_index("KOSPI 전기전자", _series())                         # 영문 별칭
    assert p.hit.sid == 3


@pytest.mark.parametrize("text", ["코스피200", "코스피 200", "KOSPI200", "kospi 200"])
def test_kospi200_aliases_resolve_to_the_same_series(text):
    r = D.resolve_index(text, _series())
    assert r.hit.sid == 2 and not r.question


def test_unique_exact_name_has_no_note_and_unknown_gets_suggestions():
    assert D.resolve_index("코스닥", _series()).hit.sid == 4 and D.resolve_index("코스닥", _series()).note == ""
    miss = D.resolve_index("코스피 반도체", _series())
    assert miss.hit is None and "찾지 못했습니다" in miss.question
    near = D.resolve_index("KRX", _series())
    assert near.hit is None and "KRX 300" in near.question                    # 비슷한 이름 안내
    assert D.resolve_index("", _series()).question


def test_series_not_in_db_is_found_but_flagged():
    r = D.resolve_index("KRX 반도체", _series())
    assert r.hit.sid == 8 and r.hit.in_db is False                            # 값은 DB 에 없다 — 호출부가 안내한다


# ── 업종 구간 규칙 ────────────────────────────────────────────────────────────────
K = D.SectorKey


def test_sector_map_follows_snapshot_covering_the_day_and_retro_before_first():
    semi, auto = K("G45", "IT", "G4530", "반도체"), K("G25", "경기", "G2510", "자동차")
    ivs = {"B": [("20261001", "20261012", semi), ("20261012", None, auto)], "A": [("20261001", None, semi)]}
    m = D.SectorMap(["20261001", "20261012"], ivs)
    assert m.at("B", "20261008") == semi and m.at("B", "20261011") == semi          # 스냅샷 사이 날은 앞 스냅샷
    assert m.at("B", "20261012") == auto and m.at("B", "20261016") == auto           # 구간 끝(valid_to)은 밖, 다음 구간 첫 날은 안
    assert m.retro is False
    assert m.at("A", "20260930") == semi and m.retro is True                         # 첫 스냅샷 이전 → 첫 스냅샷 소급
    assert m.at("Z", "20261008") is None                                             # 구간이 없는 종목 = 미분류
    assert D.SectorMap([], {}).at("A", "20261008") is None


# ── 정정 배당 접기 ────────────────────────────────────────────────────────────────

def test_dedupe_keeps_latest_receipt_per_ticker_and_record_date():
    a1 = D.Dividend("900010", "20251231", "20260210", 100.0, "20260210000001")
    a2 = D.Dividend("900010", "20251231", "20260224", 150.0, "20260224000007")        # 정정 — 가장 늦은 접수가 이긴다
    b = D.Dividend("900020", "20251231", None, 50.0, "20260211000002")
    c = D.Dividend("900010", "20260630", None, 40.0, "20260801000003")               # 같은 종목 다른 기준일 — 별개
    out = D.dedupe_dividends([a1, b, a2, c])
    assert [(d.ticker, d.record_dd, d.dps) for d in out] == [("900010", "20251231", 150.0), ("900010", "20260630", 40.0),
                                                              ("900020", "20251231", 50.0)]


# ── 주간 읽기: DB + 콜드 이음 ─────────────────────────────────────────────────────
SH = 5_969_782_550
COLD = [("005930", "20210625", "KS", 81_000, SH), ("005930", "20210702", "KS", 80_000, SH), ("005930", "20221229", "KS", 55_300, SH),
        ("005930", "20231006", "KS", 66_000, SH), ("005930", "20231013", "KS", 68_000, SH)]
DB = [("20221229", 55_300, SH),                                                    # DB 연말 행(S 이전) — 곡선에 쓰지 않는다
      ("20231020", 69_000, SH), ("20231027", 67_000, SH), ("20261016", 73_000, SH)]


@pytest.fixture
def fake_db(monkeypatch):
    calls = []

    def fake(tickers, lo, hi):
        calls.append((list(tickers), lo, hi))
        return {t: [r for r in DB if lo <= r[0] <= hi] for t in tickers}
    monkeypatch.setattr(D, "read_weekly_px", fake)
    return calls


def test_weekly_read_without_need_for_cold_is_db_only(fake_db):
    r = asyncio.run(D.read_weekly(["005930"], "20240105", "20261231", latest="20261016", floor="20231016"))
    assert r.cold_used is False and r.reason is None and r.seam is None
    assert [x[0] for x in r.rows["005930"]] == ["20261016"] and fake_db == [(["005930"], "20240105", "20261231")]


def test_weekly_read_cold_off_degrades_to_db_floor_and_says_why(fake_db):
    r = asyncio.run(D.read_weekly(["005930"], "20210601", "20261231", latest="20261016", floor="20231016"))     # OPM_COLD=0 (conftest)
    assert r.cold_used is False and r.reason == "off"
    assert [x[0] for x in r.rows["005930"]] == ["20231020", "20231027", "20261016"] and fake_db[0][1] == "20231016"     # DB 하한부터, 연말 행 제외


def test_weekly_read_joins_cold_before_the_seam_without_overlap(fake_db, cold_serving):
    cold_serving(COLD, upto="20231015")                                           # coverage 끝 20231015 → 이음매 S = min(B 20231016, C+1) = 20231016
    r = asyncio.run(D.read_weekly(["005930"], "20210601", "20261231", latest="20261016", floor="20231016"))
    assert r.cold_used is True and r.seam == "20231016" and r.reason is None and r.n_cold_rows == 5
    assert [x[0] for x in r.rows["005930"]] == ["20210625", "20210702", "20221229", "20231006", "20231013", "20231020", "20231027", "20261016"]
    assert fake_db[0][1] == "20231016"                                            # DB 는 이음매부터 읽는다 — 겹침 0
    assert r.rows["005930"][0] == ("20210625", 81_000, SH)


def test_weekly_read_cold_gap_degrades_whole_curve(fake_db, cold_serving):
    cold_serving(COLD, upto="20230630", max_dd="20230630")                        # 서빙본이 20230630 까지만 — 20230701~20231015 가 빈다(이음매 틈)
    r = asyncio.run(D.read_weekly(["005930"], "20210601", "20261231", latest="20261016", floor="20231016"))
    assert r.cold_used is False and r.reason and r.n_cold_rows == 0
    assert all(x[0] >= "20231016" for x in r.rows["005930"])                      # 부분 곡선을 내지 않는다(DATA-COLD2)


def test_weekly_read_db_failure_is_reported(monkeypatch):
    monkeypatch.setattr(D, "read_weekly_px", lambda t, lo, hi: None)
    r = asyncio.run(D.read_weekly(["005930"], "20240105", "20261231", latest="20261016", floor="20231016"))
    assert r.db_ok is False and r.rows == {}
