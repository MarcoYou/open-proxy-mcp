"""업종분류 저장분의 파생 행(구성종목에서만 빠진 상장 종목 · 우선주)을 Postgres 17 에서 증명한다 (261018).

배경: 공급처 응답은 업종 지수 구성종목이라, 20261016 수집에서 20260828 에 있던 상장 종목 32개가 빠졌다(분류는 공급처에 그대로).
`scripts/refresh_sector_class.py` 는 관측 행에 같은 스냅샷 안의 분류를 잇는 파생 행을 더하고 출처(`src`·`src_ref`)를 남긴다.

  D1  옛 모양 표(출처 칸 없음)에 칸을 더하고 기존 행은 관측(`wise`)이 된다.
  D2  스냅샷을 오래된 것부터 차례로 — 이어 쓰기는 실제 관측 날짜를 잇고, 상장폐지 종목은 되살리지 않는다.
  D3  다시 돌려도 같은 결과(멱등).
  D4  같은 날짜를 다시 수집하면 다시 나온 종목은 관측으로 돌아오고, 그 뒤 스냅샷의 파생 행도 다시 만든다.
  D5  상장 여부를 모르면(시세 저장분 부족) 파생 행을 만들지 않는다.
  D6  상장 여부를 모르게 된 옛 스냅샷(보존 하한 이전 — 주간 시세가 종목별 연말 행만 남음)은 이미 만든 파생 행을 지우지 않는다.
      지우면 그 뒤 스냅샷의 이어 쓰기 사슬이 끊겨 최신 스냅샷에서도 종목이 미분류로 떨어진다.
  D7  기업 정보 화면 관측(`wise_company`, 261018)은 관측이다 — 다시 만들기가 지우지 않고, 그 종목은 이어 쓰지 않으며, 우선주가 잇고,
      다음 스냅샷이 조회하지 못하면 이어 쓴다. 그 날 구성종목에 다시 나오면 구성종목 관측이 이긴다(화면 관측 쓰기도 구성종목 행을 덮지 않는다).
  D8  `--company-only` — 모든 스냅샷에 화면 관측을 더하되, 대응은 스냅샷마다 그 날 구성종목인 본보기로 확인한다. dry-run 은 쓰지 않는다.
Docker 가 없으면 건너뛴다(`tests/conftest.py` 의 `pg17`).
"""
from __future__ import annotations

import pytest

psycopg = pytest.importorskip("psycopg")

from scripts import refresh_sector_class as S  # noqa: E402

A = ("G25", "경기관련소비재", "G2510", "자동차와부품")
B = ("G45", "IT", "G4530", "반도체와반도체장비")
#: 상장 판단이 1,000종목 미만이면 파생 행을 만들지 않으므로, 시세 저장분에 채울 다른 종목들.
#: 끝자리를 0 이 아닌 코드로 둔다 — 보통주면 기업 화면 조회 후보(구성종목에 없는 상장 보통주)가 되어 시험이 1,000콜을 흉내 내야 한다.
FILLER = [f"9{n:04}5" for n in range(1000)]


def _old_shape(c):
    """출처 칸이 생기기 전의 운영 표 모양(+ 시세 저장분 최소 칸)."""
    c.execute("""CREATE TABLE wise_sector (snap_dd text NOT NULL, ticker text NOT NULL, sector_code text NOT NULL,
                 sector text NOT NULL, industry_code text NOT NULL, industry text NOT NULL, PRIMARY KEY (snap_dd, ticker))""")
    c.execute("CREATE TABLE krx_weekly (price_dd text NOT NULL, ticker text NOT NULL, PRIMARY KEY (price_dd, ticker))")
    c.execute("CREATE TABLE krx_daily (trd_dd text NOT NULL, ticker text NOT NULL, PRIMARY KEY (trd_dd, ticker))")
    # 261019: 일별 저장분의 날짜(최신일·「이하 가장 늦은 날」)는 달력에서 읽는다
    c.execute("CREATE TABLE krx_day (dd text PRIMARY KEY, n integer NOT NULL DEFAULT 0)")


def _obs(c, snap, rows):
    with c.cursor() as cur:
        cur.executemany("INSERT INTO wise_sector (snap_dd, ticker, sector_code, sector, industry_code, industry) "
                        "VALUES (%s,%s,%s,%s,%s,%s)", [(snap, t, *cls) for t, cls in rows.items()])


def _listed(c, table, dd, tickers):
    col = "price_dd" if table == "krx_weekly" else "trd_dd"
    with c.cursor() as cur:
        cur.executemany(f"INSERT INTO {table} ({col}, ticker) VALUES (%s,%s)", [(dd, t) for t in [*tickers, *FILLER]])
    if table == "krx_daily":
        c.execute("INSERT INTO krx_day (dd, n) SELECT %s, count(*) FROM krx_daily WHERE trd_dd = %s "
                  "ON CONFLICT (dd) DO UPDATE SET n = EXCLUDED.n", (dd, dd))


def _snap(c, snap):
    return {r[0]: r[1:] for r in c.execute(
        "SELECT ticker, industry_code, src, src_ref FROM wise_sector WHERE snap_dd=%s ORDER BY 1", (snap,))}


@pytest.fixture
def db(pg17):
    with psycopg.connect(pg17, autocommit=True) as c:
        _old_shape(c)
        # 0821: 000040·000050·000060 관측. 0828: 000050 이 구성종목에서 빠짐(상장 유지), 000060 은 상장폐지.
        # 1016: 000040 도 빠짐(상장 유지). 우선주 000045(보통주 000040)·005935(005930) 상장.
        _obs(c, "20260821", {"000040": A, "000050": A, "000060": A, "005930": B})
        _obs(c, "20260828", {"000040": A, "005930": B})
        _obs(c, "20261016", {"005930": B})
        _listed(c, "krx_weekly", "20260821", ["000040", "000050", "000060", "005930", "005935", "000045"])
        _listed(c, "krx_weekly", "20260828", ["000040", "000050", "005930", "005935", "000045"])
        _listed(c, "krx_daily", "20261016", ["000040", "000050", "005930", "005935", "000045"])
    return pg17


def _rederive(url):
    with psycopg.connect(url) as con:
        added = S.ensure_src_columns(con)
        infos = S.rederive_from(con)
        con.commit()
    return added, infos


def test_d1_d2_columns_added_and_chain_derived(db):
    added, infos = _rederive(db)
    assert added
    with psycopg.connect(db) as c:
        s21, s28, s16 = _snap(c, "20260821"), _snap(c, "20260828"), _snap(c, "20261016")
    # 기존 행은 관측. 0821 은 첫 스냅샷이라 이어 쓰기 없음, 우선주만.
    assert s21["000040"] == ("G2510", "wise", None)
    assert s21["000045"] == ("G2510", "pref", "000040") and s21["005935"] == ("G4530", "pref", "005930")
    # 0828: 구성종목에서만 빠진 000050 을 잇는다. 상장폐지 000060 은 잇지 않는다.
    assert s28["000050"] == ("G2510", "carry", "20260821")
    assert "000060" not in s28
    # 1016: 000040 은 0828 관측을, 000050 은 0821 관측을 잇는다(사슬이어도 실제 관측 날짜). 우선주는 이어 쓴 보통주를 잇는다.
    assert s16["000040"] == ("G2510", "carry", "20260828")
    assert s16["000050"] == ("G2510", "carry", "20260821")
    assert s16["000045"] == ("G2510", "pref", "000040")
    assert [i["carry"] for i in infos] == [0, 1, 2]


def test_d3_rederive_is_idempotent(db):
    _rederive(db)
    with psycopg.connect(db) as c:
        before = {s: _snap(c, s) for s in ("20260821", "20260828", "20261016")}
    added, _ = _rederive(db)
    assert not added  # 칸은 한 번만 더한다
    with psycopg.connect(db) as c:
        assert {s: _snap(c, s) for s in before} == before


def test_d4_recollect_returns_observed_and_rederives_later(db, monkeypatch):
    _rederive(db)
    monkeypatch.setenv("DATABASE_URL", db)
    monkeypatch.delenv("WICS_DATABASE_URL", raising=False)
    # 0828 을 다시 수집했더니 000050 이 응답에 다시 있다(관측으로 돌아와야 한다).
    snapshot = {"requestedDate": "20260828", "asOf": "2026-08-28", "sectorCount": 10, "industryCount": 28,
                "tickerCount": 3, "data": {t: {"sectorCode": cls[0], "sector": cls[1], "industryCode": cls[2],
                                                "industry": cls[3]}
                                            for t, cls in {"000040": A, "000050": A, "005930": B}.items()}}
    assert S.persist(snapshot)
    with psycopg.connect(db) as c:
        s28, s16 = _snap(c, "20260828"), _snap(c, "20261016")
        blob = c.execute("SELECT ticker_count FROM kr_wics_snapshots WHERE as_of='2026-08-28'").fetchone()[0]
    assert s28["000050"] == ("G2510", "wise", None)
    # 그 뒤 스냅샷도 다시 만들어 이어 쓰기 출처가 새 관측(0828)을 가리킨다.
    assert s16["000050"] == ("G2510", "carry", "20260828")
    assert blob == 3  # 원문 스냅샷은 관측만


def test_d5_no_price_rows_means_no_derived_rows(pg17):
    with psycopg.connect(pg17, autocommit=True) as c:
        _old_shape(c)
        _obs(c, "20260821", {"000040": A})
        _obs(c, "20260828", {"005930": B})
        c.execute("INSERT INTO krx_weekly VALUES ('20260828', '000040'), ('20260828', '005935')")  # 1,000 미만
    _, infos = _rederive(pg17)
    assert [(i["carry"], i["pref"]) for i in infos] == [(0, 0), (0, 0)]
    with psycopg.connect(pg17) as c:
        assert c.execute("SELECT count(*) FROM wise_sector WHERE src <> 'wise'").fetchone()[0] == 0


def test_dry_run_rederive_writes_nothing(db, monkeypatch):
    _rederive(db)  # 칸이 있는 상태에서
    with psycopg.connect(db, autocommit=True) as c:
        c.execute("DELETE FROM wise_sector WHERE src <> 'wise'")
    monkeypatch.setenv("DATABASE_URL", db)
    monkeypatch.delenv("WICS_DATABASE_URL", raising=False)
    assert S.rederive_db(dry_run=True) == 0
    with psycopg.connect(db) as c:
        assert c.execute("SELECT count(*) FROM wise_sector WHERE src <> 'wise'").fetchone()[0] == 0
    assert S.rederive_db(dry_run=False) == 0
    with psycopg.connect(db) as c:
        assert c.execute("SELECT count(*) FROM wise_sector WHERE src <> 'wise'").fetchone()[0] > 0


def test_d6_unknown_listing_keeps_existing_derived_rows_and_chain(db, monkeypatch):
    """보존 하한이 지나 옛 스냅샷 날짜의 주간 시세가 종목별 연말 행만 남으면(1,000 미만) 그 날의 상장 여부를 모른다.
    그때 다시 만들기(`--rederive`)·옛 날짜 재수집이 이미 있던 파생 행을 지우면 안 된다 — 0828 의 000050 이어 쓰기가 사라지면
    1016 도 000050 을 잇지 못해 최신 스냅샷에서 미분류로 떨어진다."""
    _rederive(db)
    with psycopg.connect(db) as c:
        before = {s: _snap(c, s) for s in ("20260821", "20260828", "20261016")}
    # 보존 하한 이전이 된 0821·0828 — 주간 시세가 그 날 마지막 행인 종목 몇 개만 남는다.
    with psycopg.connect(db, autocommit=True) as c:
        c.execute("DELETE FROM krx_weekly WHERE price_dd IN ('20260821', '20260828') AND ticker NOT IN ('000060')")
    _, infos = _rederive(db)
    with psycopg.connect(db) as c:
        after = {s: _snap(c, s) for s in before}
    assert after == before
    assert after["20261016"]["000050"] == ("G2510", "carry", "20260821")
    assert [i.get("kept", False) for i in infos] == [True, True, False]

    # 옛 날짜를 다시 수집해도 같다 — 다시 나온 종목만 관측으로 바뀌고 나머지 파생 행은 남는다.
    monkeypatch.setenv("DATABASE_URL", db)
    monkeypatch.delenv("WICS_DATABASE_URL", raising=False)
    snapshot = {"requestedDate": "20260828", "asOf": "2026-08-28", "sectorCount": 10, "industryCount": 28,
                "tickerCount": 2, "data": {t: {"sectorCode": cls[0], "sector": cls[1], "industryCode": cls[2],
                                                "industry": cls[3]}
                                            for t, cls in {"000040": A, "005930": B}.items()}}
    assert S.persist(snapshot)
    with psycopg.connect(db) as c:
        assert _snap(c, "20260828") == before["20260828"]
        assert _snap(c, "20261016") == before["20261016"]


C = ("G35", "건강관리", "G3520", "제약과생물공학")


def _snapshot(day, rows):
    return {"requestedDate": day.replace("-", ""), "asOf": day, "sectorCount": 10, "industryCount": 28,
            "tickerCount": len(rows), "data": {t: {"sectorCode": cls[0], "sector": cls[1], "industryCode": cls[2],
                                                    "industry": cls[3]} for t, cls in rows.items()}}


def test_d7_company_rows_are_observations(db, monkeypatch):
    with psycopg.connect(db, autocommit=True) as c:
        c.execute("INSERT INTO krx_daily VALUES ('20261016', '950160')")
        _listed(c, "krx_daily", "20261030", ["000040", "000045", "000050", "005930", "005935", "950160"])
    _rederive(db)
    monkeypatch.setenv("DATABASE_URL", db)
    monkeypatch.delenv("WICS_DATABASE_URL", raising=False)
    company = [("000040", *A, "wise_company", "20261018:자동차"), ("950160", *C, "wise_company", "20261018:생물공학")]
    assert S.persist(_snapshot("2026-10-16", {"005930": B}), company)
    with psycopg.connect(db) as c:
        s16 = _snap(c, "20261016")
        note = c.execute("SELECT col_description('wise_sector'::regclass, attnum) FROM pg_attribute "
                         "WHERE attrelid='wise_sector'::regclass AND attname='src'").fetchone()[0]
    assert s16["950160"] == ("G3520", "wise_company", "20261018:생물공학")
    assert s16["000040"] == ("G2510", "wise_company", "20261018:자동차")  # 이어 쓰기 대신 화면 관측
    assert s16["000045"] == ("G2510", "pref", "000040")                  # 우선주는 화면 관측 보통주를 잇는다
    assert s16["000050"] == ("G2510", "carry", "20260821")               # 화면을 못 본 종목은 종전대로 이어 쓰기
    assert "wise_company" in note
    # 다시 만들어도 화면 관측은 남는다.
    _rederive(db)
    with psycopg.connect(db) as c:
        assert _snap(c, "20261016") == s16
    # 다음 스냅샷이 화면을 못 보면 관측한 스냅샷 날짜로 이어 쓴다.
    assert S.persist(_snapshot("2026-10-30", {"005930": B}), None)
    with psycopg.connect(db) as c:
        s30 = _snap(c, "20261030")
    assert s30["950160"] == ("G3520", "carry", "20261016")
    assert s30["000040"] == ("G2510", "carry", "20261016")
    # 그 날 구성종목 응답에 다시 나오면 구성종목 관측이 이긴다(화면 관측을 덮는다).
    assert S.persist(_snapshot("2026-10-16", {"005930": B, "950160": B}), None)
    with psycopg.connect(db) as c:
        assert _snap(c, "20261016")["950160"] == ("G4530", "wise", None)
        assert _snap(c, "20261030")["950160"] == ("G4530", "carry", "20261016")


def test_d7_company_rows_never_overwrite_constituent_rows(db):
    """화면 관측은 그 날 구성종목 관측(`wise`)을 덮지 않는다 — `--company-only` 가 후보를 정한 뒤 화면을 도는 사이에 같은 날짜를
    다시 수집해 그 종목이 구성종목으로 들어온 경우 등. 이어 쓰기 행은 화면 관측이 대신한다."""
    _rederive(db)
    with psycopg.connect(db) as con:
        S.write_company_rows(con, "20261016", [("005930", *C, "wise_company", "20261018:생물공학"),
                                               ("000040", *C, "wise_company", "20261018:생물공학")])
        con.commit()
        s16 = _snap(con, "20261016")
    assert s16["005930"] == ("G4530", "wise", None)
    assert s16["000040"] == ("G3520", "wise_company", "20261018:생물공학")


def test_d8_company_only_backfills_every_snapshot_with_per_snapshot_checks(db, monkeypatch):
    import asyncio

    import httpx
    with psycopg.connect(db, autocommit=True) as c:
        for table, dd in (("krx_weekly", "20260821"), ("krx_weekly", "20260828"), ("krx_daily", "20261016")):
            col = "price_dd" if table == "krx_weekly" else "trd_dd"
            c.execute(f"INSERT INTO {table} ({col}, ticker) VALUES (%s, '950160')", (dd,))
    _rederive(db)
    monkeypatch.setenv("DATABASE_URL", db)
    monkeypatch.delenv("WICS_DATABASE_URL", raising=False)
    pages = {"950160": "반도체", "005930": "반도체", "000040": "자동차", "000050": "자동차"}
    hits = []

    def handler(request):
        t = request.url.params["cmp_cd"]
        hits.append(t)
        return httpx.Response(200, text=f'<b class="num">{t}</b><dt class="line-left">WICS : {pages[t]}</dt>' + " " * 5000)

    async def no_wait(counter):
        return None
    monkeypatch.setattr(S, "throttle_web_request", no_wait)
    monkeypatch.setattr(S, "_company_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(S, "load_company_map", lambda: {
        "반도체": {"industryCode": "G4530", "exemplars": ["005930"]},
        "자동차": {"industryCode": "G2510", "exemplars": ["000040"]}})
    with psycopg.connect(db) as c:
        before = {s: _snap(c, s) for s in ("20260821", "20260828", "20261016")}

    assert asyncio.run(S.company_only_db("all", dry_run=True)) == 0
    with psycopg.connect(db) as c:
        assert {s: _snap(c, s) for s in before} == before
    hits.clear()
    assert asyncio.run(S.company_only_db("all")) == 0
    with psycopg.connect(db) as c:
        s21, s28, s16 = (_snap(c, s) for s in ("20260821", "20260828", "20261016"))
    for s in (s21, s28, s16):
        assert s["950160"][:2] == ("G4530", "wise_company")
    # 0828: 「자동차」 대응은 본보기 000040 이 그 날 구성종목이라 확인된다 → 000050 은 이어 쓰기 대신 화면 관측.
    assert s28["000050"][:2] == ("G2510", "wise_company")
    # 1016: 본보기 000040 이 그 날 구성종목이 아니라 「자동차」 대응을 확인하지 못한다 → 000040·000050 은 종전대로 이어 쓰기.
    assert s16["000040"] == ("G2510", "carry", "20260828") and s16["000050"][1] == "carry"
    assert s16["000045"] == ("G2510", "pref", "000040")
    # 한 종목은 한 실행에서 한 번만 조회한다(스냅샷 셋이어도).
    assert sorted(hits) == sorted(set(hits))
