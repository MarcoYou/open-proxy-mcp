"""업종 구성·비중을 시총 집계 표와 대조한다 — Postgres 17 에서 두 세계(표 모드·뷰 모드)로 증명한다 (261021).

핵심 약속: **같은 날 같은 업종·시장이면 구성 종목의 시총 합·종목 수에 뺀 우선주 몫(`sector.preferred_*`)을 더한 값이 `krx_cap_agg`(`scope="sector"` 가 읽는 표, 우선주 포함 합)와 같다.**
(261023 부터 업종 구성은 우선주를 싣지 않는다 — 구성의 합은 보통주 합이고 집계와의 차이는 응답에 적힌 우선주 몫이다.) 합계 도구와 구성 도구가
다른 숫자를 내면 기여 분해가 합계와 어긋난다. 운영 읽기 전용 대조(2026-10-21: 날짜 15개 × 업종 38 × 시장 3 + 미분류, 불일치 0)의 작은 판이다.

세계
  table  `krx_weekly` 가 표(교체 전·되돌린 뒤) — 주간 날짜를 `krx_weekly` 에서 직접 읽는다.
  view   `krx_weekly` 가 뷰(교체 뒤) — 주간 날짜를 작은 달력(`krx_wk_day`·`krx_day`)에서 읽는다.

  D1  모든 집계 날짜 × 38 업종 × 시장(전체·코스피·코스닥)의 (종목 수, 시총 합)이 `krx_cap_agg` 와 같고 미분류도 같다(두 세계, 소급 날짜 포함).
  D2  분류 스냅샷은 그 날 이하 가장 최근 — 업종을 옮긴 종목은 스냅샷 날짜를 건너며 구성이 바뀌고, 첫 관측 이전 날짜는 소급(경고).
  D3  날짜 풀이: 주말 → 앞 거래일 · 보관된 최신일보다 뒤 → 최신일 · 일별 보관 창 밖 → 주간 시세의 앞 주 마지막 거래일 · 주간 구간보다 이름 → 안내.
  D4  시총이 없는 종목(상장주식수 없음)은 구성에 넣지 않는다 — 시총 집계와 같은 조건.
  D5  끝에서 끝까지: `list_universe`·`build_universe_payload` 가 같은 값을 낸다.
Docker 가 없으면 건너뛴다(`tests/conftest.py` 의 `pg17`).
"""
from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys

import pytest

psycopg = pytest.importorskip("psycopg")

from test_krx_daily_pg import KRX_WEEKLY_DDL, _ddl, _row  # noqa: E402

from open_proxy_mcp.db import pg_rows  # noqa: E402
from open_proxy_mcp.services import price_multiple_data as pmd  # noqa: E402
from open_proxy_mcp.services import sector_universe as S  # noqa: E402
from open_proxy_mcp.services import trading as T  # noqa: E402
from open_proxy_mcp.services import universe as uni  # noqa: E402
from open_proxy_mcp.services.sector_class import DB_SCHEME, MAJOR, MID  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent

SEMI = ("G4530", "반도체와반도체장비")
SOFT = ("G4510", "소프트웨어와서비스")
IT = ("G45", "IT")
AUTO = ("G2510", "자동차와부품")
CONS = ("G25", "경기관련소비재")

#: (코드, 시장, 상장주식수) — 900015 는 900010 의 우선주, 900099 는 상장주식수가 비어(900098 은 0) 시총이 없다, 800030 은 10/14 신규 상장(분류 스냅샷에 없음).
TICKERS = [("900010", "KS", 1000), ("900015", "KS", 200), ("900020", "KS", 500), ("900030", "KS", 300), ("900099", "KS", None),
           ("900098", "KS", 0),
           ("800010", "KQ", 400), ("800020", "KQ", 100), ("800030", "KQ", 100)]
NEW_LISTING_FROM = "20261014"
#: 일별 기록 — 10/9(금)은 휴장이라 10/8 이 그 주를 닫는다. 10/16(금)이 다음 주를 닫는다.
DAYS = ["20261005", "20261006", "20261007", "20261008", "20261012", "20261013", "20261014", "20261015", "20261016"]
#: 옛 닫힌 주(일별 보관 창보다 이르다) — 상장폐지된 종목 하나(D00000)가 섞여 있다.
OLD_WEEKS = ["20250926", "20251003", "20261001"]


def _wddl():
    spec = importlib.util.spec_from_file_location("krx_weekly_ddl", ROOT / "scripts" / "krx_weekly_ddl.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _close(dd: str, i: int) -> int:
    return (int(dd[-4:]) % 97 + 10 + i) * 100


def _day(dd: str):
    kospi, kosdaq = [], []
    for i, (t, m, shrs) in enumerate(TICKERS):
        if t == "800030" and dd < NEW_LISTING_FROM:
            continue
        r = _row(t, _close(dd, i), shrs=1 if shrs is None else shrs)
        if shrs is None:
            r["LIST_SHRS"], r["MKTCAP"] = None, None             # 상장주식수가 빈 행 — 시총이 없다
        (kospi if m == "KS" else kosdaq).append(r)
    return kospi, kosdaq


def _sector_rows(snap: str, moved: bool):
    semi, auto = ["900010", "900015", "900020", "800010", "900098"], ["900030", "800020"]
    if moved:                                                    # 10/12 스냅샷: 900020 이 자동차로 옮겨 가고 900099 가 소프트웨어로 들어온다
        semi.remove("900020")
        auto.append("900020")
    rows = []
    for t in semi:
        rows.append((snap, t, *IT[:1], IT[1], *SEMI[:1], SEMI[1], "pref" if t == "900015" else "wise", "900010" if t == "900015" else None))
    for t in auto:
        rows.append((snap, t, CONS[0], CONS[1], AUTO[0], AUTO[1], "wise", None))
    if moved:
        rows.append((snap, "900099", IT[0], IT[1], SOFT[0], SOFT[1], "wise", None))
    return rows


@pytest.fixture(params=["table", "view"])
def world(pg17, monkeypatch, request):
    import open_proxy_mcp.db as dbm
    from scripts import krx_cap_agg as C
    url = pg17
    _ddl().apply(url)
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(KRX_WEEKLY_DDL)
        c.execute("CREATE TABLE wise_sector (snap_dd text NOT NULL, ticker text NOT NULL, sector_code text NOT NULL, sector text NOT NULL, "
                  "industry_code text NOT NULL, industry text NOT NULL, src text NOT NULL DEFAULT 'wise', src_ref text, "
                  "PRIMARY KEY (snap_dd, ticker))")
        with c.cursor() as cur:
            for snap, moved in (("20261001", False), ("20261012", True)):
                cur.executemany("INSERT INTO wise_sector (snap_dd, ticker, sector_code, sector, industry_code, industry, src, src_ref) "
                                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", _sector_rows(snap, moved))
            for dd in OLD_WEEKS:                                  # 옛 닫힌 주 — 일별 저장분 이전 날짜
                rows = [(dd, t, m, _close(dd, i), _close(dd, i) * s, s) for i, (t, m, s) in enumerate(TICKERS)
                        if s is not None and t != "800030"]
                rows.append((dd, "D00000", "KQ", 500, 500 * 10, 10))   # 그 뒤 상장폐지된 종목 — 분류가 없다
                cur.executemany("INSERT INTO krx_weekly (price_dd, ticker, market, close, mktcap, list_shrs) VALUES (%s,%s,%s,%s,%s,%s)", rows)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(dbm, "_pool", None)
    monkeypatch.setattr(dbm, "_pool_disabled_until", float("inf"))
    for dd in DAYS:
        res = pmd._krx_db_upsert(dd, *_day(dd))
        assert res.daily_ok and res.weekly_ok, (dd, res)
    if request.param == "view":
        _wddl().apply(url, "switch")
    monkeypatch.setattr(sys, "argv", ["krx_cap_agg.py", "--full"])
    assert C.main() == 0

    async def bounds():
        return "20261016", "20250901"                            # (저장된 최신 주, 주간 곡선 하한)
    monkeypatch.setattr(pmd, "_weekly_bounds", bounds)

    async def names():
        return {"900010": "가나전자", "900020": "다라차", "900030": "마바소프트", "800010": "사아테크", "800020": "자차모터"}
    monkeypatch.setattr(uni, "_names_by_ticker", names)
    return url


def _agg_dates():
    return [r[0] for r in pg_rows("SELECT DISTINCT price_dd FROM krx_cap_agg WHERE scheme='market' ORDER BY 1")]


def _compute(text, dd):
    day = asyncio.run(S._pick_day(dd))
    assert not day.error and day.dd, (dd, day)
    return S.compute(S.parse(text), S._read_rows(day.dd, day.source), day, {}), day


# ── D1: 시총 집계와 같다 ────────────────────────────────────────────────────

def test_every_sector_and_market_matches_krx_cap_agg_on_every_aggregated_day(world):
    dates = _agg_dates()
    assert {"20250926", "20251003", "20261001", "20261008", "20261016"} <= set(dates), dates   # 소급·옛 주·일별 닫힌 주·이번 주 점
    checked = 0
    for dd in dates:
        day = asyncio.run(S._pick_day(dd))
        assert day.dd == dd and not day.error, (dd, day)
        rows = S._read_rows(day.dd, day.source)
        agg = {(m, s, b): (n, cap) for m, s, b, n, cap in
               pg_rows("SELECT market, scheme, bucket, n, cap FROM krx_cap_agg WHERE price_dd=%s", (dd,))}
        for e in S._catalog(rows):
            for mk in (None, "KS", "KQ"):
                res = S.compute(S.SectorQuery(raw="t", windows=(e.code,), level=e.level, market=mk), rows, day, {})
                if mk is None:
                    parts = [agg.get((m, DB_SCHEME[e.level], e.code), (0, 0)) for m in ("KS", "KQ")]
                    want = (sum(p[0] for p in parts), sum(p[1] for p in parts))
                else:
                    want = agg.get((mk, DB_SCHEME[e.level], e.code), (0, 0))
                got = ((res.sector["n"] + res.sector["preferred_n"], res.sector["cap_incl_preferred_krw"])      # 보통주 + 뺀 우선주 몫 = 집계(우선주 포함)
                       if res.rows or res.sector else (0, 0))
                assert got == want, (dd, e.level, e.code, mk, got, want)
                checked += 1
        for mk in ("KS", "KQ"):                                            # 미분류도 같다
            unc = [r for r in rows if r["market"] == mk and not r["major_code"]]
            assert (len(unc), sum(int(r["mktcap"]) for r in unc)) == agg.get((mk, "wics_sector", "_UNCLASSIFIED"), (0, 0)), (dd, mk)
    assert checked >= len(dates) * 3 * 3


def test_market_total_in_the_payload_is_the_cap_agg_market_total(world):
    res, day = _compute("코스피 반도체 업종", "20261016")
    ks = pg_rows("SELECT cap FROM krx_cap_agg WHERE price_dd='20261016' AND scheme='market' AND market='KS'")[0][0]
    assert res.sector["market_cap_krw"] + res.sector["market_preferred_cap_krw"] == ks          # 시장 몫의 분모는 우선주를 뺀 값 — 우선주 몫을 더하면 집계 값
    assert res.sector["market_preferred_cap_krw"] > 0


# ── D2: 분류 스냅샷 ─────────────────────────────────────────────────────────

def test_membership_follows_the_classification_snapshot_valid_on_that_day(world):
    before, _ = _compute("업종 반도체", "20261008")                         # 스냅샷 10/1 — 900020 은 반도체
    after, day = _compute("업종 반도체", "20261016")                         # 스냅샷 10/12 — 900020 은 자동차로 옮겨 갔다
    assert {r["ticker"] for r in before.rows} == {"900010", "900020", "800010"}                # 우선주 900015 는 싣지 않는다
    assert {r["ticker"] for r in after.rows} == {"900010", "800010"}
    assert before.sector["classification_asof"] == "20261001" and after.sector["classification_asof"] == "20261012"
    same_day, _ = _compute("업종 반도체", "20261012")                       # 스냅샷이 찍힌 바로 그 날 — 그 스냅샷이 유효하다(이하)
    assert same_day.sector["classification_asof"] == "20261012" and "900020" not in {r["ticker"] for r in same_day.rows}
    eve, _ = _compute("업종 반도체", "20261008")                            # 스냅샷 전날들은 이전 스냅샷
    assert eve.sector["classification_asof"] == "20261001"
    car, _ = _compute("업종 자동차", "20261016")
    assert "900020" in {r["ticker"] for r in car.rows}
    soft, _ = _compute("업종 소프트웨어", "20261016")                         # 10/12 에 들어온 900099 는 시총이 없어 구성에 없다
    assert soft.sector == {} or "900099" not in {r["ticker"] for r in soft.rows}


def test_day_before_the_first_snapshot_is_retro_and_says_so(world):
    res, day = _compute("업종 반도체", "20250926")
    assert day.source == "weekly" and res.sector["classification_retro"] is True
    assert any("소급" in w for w in res.warnings)
    assert res.sector["unclassified"]["n"] == 1 and res.sector["unclassified"]["top"][0]["ticker"] == "D00000"   # 상장폐지 종목은 분류가 없다


def test_new_listing_after_the_snapshot_is_unclassified_not_dropped_silently(world):
    res, _ = _compute("업종 반도체", "20261016")
    u = res.sector["unclassified"]
    assert u["n"] == 1 and u["top"][0]["ticker"] == "800030" and u["cap_krw"] > 0
    assert "800030" not in {r["ticker"] for r in res.rows}
    assert any("미분류 1종목" in w and "신규 상장" in w for w in res.warnings)
    early, _ = _compute("업종 반도체", "20261013")                         # 상장 전 — 미분류도 없다
    assert early.sector["unclassified"]["n"] == 0


# ── D3: 날짜 풀이 ───────────────────────────────────────────────────────────

def test_weekend_resolves_to_the_previous_trading_day(world):
    day = asyncio.run(S._pick_day("2026-10-11"))                             # 일요일 — 10/9 한글날 휴장·10/10 토요일 뒤
    assert (day.dd, day.source) == ("20261008", "daily") and "거래일이 아니어서" in day.note
    day = asyncio.run(S._pick_day("20261009"))                               # 한글날 휴장(달력에 행이 없다)
    assert day.dd == "20261008"
    ok = asyncio.run(S._pick_day("20261012"))                                # 거래일 그대로면 풀이 문장이 없다
    assert ok.dd == "20261012" and ok.note == ""


def test_later_than_the_latest_day_uses_the_latest(world):
    day = asyncio.run(S._pick_day("20271231"))
    assert day.dd == "20261016" and "최신 거래일" in day.note
    assert asyncio.run(S._pick_day("")).dd == "20261016"


def test_older_than_the_daily_window_reads_the_weekly_close_before_it(world):
    day = asyncio.run(S._pick_day("20251010"))                               # 20251003 주가 닫힌 뒤, 다음 닫힌 주(20261001)보다 앞
    assert (day.dd, day.source) == ("20251003", "weekly") and "주간 해상도" in day.note
    exact = asyncio.run(S._pick_day("20250926"))
    assert (exact.dd, exact.source) == ("20250926", "weekly")
    mid = asyncio.run(S._pick_day("20261002"))                               # 일별 창(10/5~) 밖, 10/1 주 마지막 거래일 이후
    assert (mid.dd, mid.source) == ("20261001", "weekly")


def test_older_than_the_weekly_floor_is_refused_with_a_pointer(world):
    day = asyncio.run(S._pick_day("20250801"))
    assert day.dd is None and "만들 수 없습니다" in day.error and "scope=\"sector\"" in day.error


def test_garbage_as_of_is_refused(world):
    day = asyncio.run(S._pick_day("어제"))
    assert day.dd is None and "날짜가 아닙니다" in day.error
    assert asyncio.run(S._pick_day("20261332")).dd is None


# ── D4: 시총이 없는 종목 ────────────────────────────────────────────────────

def test_ticker_without_market_cap_is_not_a_member(world):
    rows = S._read_rows("20261016", "daily")
    assert {"900099", "900098"}.isdisjoint({r["ticker"] for r in rows})      # 상장주식수가 없거나 0 이라 시총이 없다
    assert any(r["ticker"] == "900010" for r in rows)


# ── D5: 끝에서 끝까지 ───────────────────────────────────────────────────────

def test_list_universe_and_payload_end_to_end(world):
    ul = asyncio.run(uni.list_universe("코스피 반도체 업종", "20261008"))
    assert ul.resolved and ul.as_of == "20261008" and ul.sector["market"] == "KOSPI" and ul.granularity == "daily"
    assert [r["ticker"] for r in ul.rows] == ["900010", "900020"]
    assert [r["name"] for r in ul.rows] == ["가나전자", "다라차"]
    assert ul.sector["preferred_n"] == 1 and not any(r.get("preferred") for r in ul.rows)
    p = asyncio.run(T.build_universe_payload("중분류 반도체와반도체장비 상위 2", as_of="20250926"))
    assert p["status"] == "ok" and p["data"]["granularity"] == "weekly" and p["data"]["n"] == 2
    assert p["data"]["sector"]["n_shown"] == 2 and p["data"]["sector"]["n"] == 3
    tot = p["data"]["sector"]["cap_krw"]
    assert p["data"]["rows"][0]["weight_pct"] == round(p["data"]["rows"][0]["mktcap_krw"] / tot * 100, 4)
    big = asyncio.run(T.build_universe_payload("대분류 IT"))
    assert big["status"] == "ok" and abs(sum(r["weight_pct"] for r in big["data"]["rows"]) - 100) < 0.01
    assert {c["name"] for c in big["data"]["sector"]["children"]} == {"반도체와반도체장비"}   # 900099(소프트웨어)는 시총이 없다


def test_ranking_paths_are_unchanged_by_the_sector_branch(world):
    ul = asyncio.run(uni.list_universe("코스피 시총 상위 2"))
    # 순위표는 종전 그대로 우선주를 빼고 밝힌다(업종 구성도 261023 부터 뺀다 — 시세 순위표 경로는 그대로)
    assert ul.sector is None and [r["rank"] for r in ul.rows] == [1, 2] and ul.excluded_pref == 1
    assert "900015" not in {r["ticker"] for r in ul.rows} and all("weight_pct" not in r for r in ul.rows)
