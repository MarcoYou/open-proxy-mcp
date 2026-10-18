"""공시 흐름 보기의 업종은 **읽을 때 조인**, 시총은 **원장에 저장된 값** — Postgres 17 에서 실제 SQL 로 검증 (261018, Supabase 재설계 6단계).

종전: 원장 배치가 넣을 때 복사해 둔 `dart_events.sector`·`industry`·`mktcap_won` 을 읽었다. 이제(`disclosure_flow._EVENTS_SQL`)
  · 업종 = 원본 업종분류 스냅샷(`wise_sector`)과 조인 — 접수일 이하 가장 최근 스냅샷(없으면 가장 이른 것). 스냅샷은 표 전체에서 고른다(원장 배치의
          `pick_snapshot` 과 같은 선택). 261019: 그 스냅샷에 종목이 없으면 그 종목의 가장 가까운 스냅샷(접수일 이하 가장 최근, 없으면 접수일 뒤 가장 이른 것) —
          신규 상장 종목이 상장 뒤 첫 스냅샷 전 공시에서 「업종 미상」이던 것. 어느 스냅샷에도 없는 종목만 비운다.
  · 시총 = 원장의 `mktcap_won` 그대로. 시세 표와 조인하지 않는다 — 일별 원본은 보관 창(261019~ 약 1년, 그 전 7거래일) 뒤 잘려 나중엔 공시 시점 값을 다시 만들 수 없다.
  · 업종 복사본 칸(`sector`·`industry`·`sector_dd`)이 **없는** 표(운영에서 지운 뒤)에서도 돌고, 칸이 **있어도 읽지 않는다**(엉뚱한 값을 넣어 두고 확인).
같은 파일에서 `price_multiple_data._firm_cap_at`(업종 비교의 그 날 시총 — 그 날 행, 없으면 같은 주 그 날 이하 가장 늦은 행, 그것도 없으면 같은 주 그 날 뒤
가장 이른 행)도 실제 SQL 로 확인한다.

로컬 Docker 의 `postgres:17` 이 필요하다(`tests/conftest.py` 의 `pg17`). 없으면 건너뛴다. network 0.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

psycopg = pytest.importorskip("psycopg")

from open_proxy_mcp.services import disclosure_flow as df  # noqa: E402
from open_proxy_mcp.services import price_multiple_data as S  # noqa: E402
from test_krx_daily_pg import KRX_WEEKLY_DDL, _ddl, put_daily  # noqa: E402

# 운영에서 업종 복사본 칸 셋을 지운 뒤의 모양(시총 칸 `mktcap_won`·`mktcap_dd` 는 남는다). 지우기 전 모양은 `with_sector_cols=True` 로 더한다.
EVENTS_DDL = """CREATE TABLE dart_events (
  rcept_no text PRIMARY KEY, rcept_dt date NOT NULL, corp_code text, corp_name text, stock_code text, corp_cls text,
  kind text NOT NULL, subtype text, stage text, is_correction boolean NOT NULL DEFAULT false, dedup_key text,
  report_nm text, flr_nm text, mktcap_won bigint, mktcap_dd text, detail_status text, detail jsonb, detail_note text,
  first_seen_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now())"""
SECTOR_COLS = "ALTER TABLE dart_events ADD COLUMN sector text, ADD COLUMN industry text, ADD COLUMN sector_dd text"
WISE_DDL = """CREATE TABLE wise_sector (snap_dd text NOT NULL, ticker text NOT NULL, sector_code text, sector text,
  industry_code text, industry text, PRIMARY KEY (snap_dd, ticker))"""

DAYS = ["20261007", "20261008", "20261012", "20261013", "20261014", "20261015", "20261016"]   # 10/9 한글날 휴장
A, B, C, E, F = "000001", "000002", "000003", "000005", "000006"
SHRS = 1_000_000


def _close(t: str, d: str) -> int:
    return {A: 10_000, B: 20_000, C: 30_000, E: 50_000}[t] + int(d[-2:]) * 10


def _drop_daily(c, ticker: str, days: tuple[str, ...]) -> None:
    """일별 저장분에서 그 종목의 그 날 행을 지운다 — 뷰(`krx_daily`) 대신 사실값 표에서(#124 구조). 달력의 행 수도 맞춘다."""
    c.execute("DELETE FROM krx_px WHERE tid = (SELECT tid FROM krx_ticker WHERE ticker=%s) AND to_char(trd_dd, 'YYYYMMDD') = ANY(%s)",
              (ticker, list(days)))
    c.execute("UPDATE krx_day d SET n = (SELECT count(*) FROM krx_px p WHERE p.trd_dd = d.trd_dd)")


def _stored_cap(no: str) -> int:
    """원장 배치가 저장해 둔 시총 — 시세 표 어디에도 없는 값으로 넣어, 조인이 아니라 저장 칸을 읽는지 가른다."""
    return 7_000_000_000 + int(no[1:])


def _load(url: str, *, with_sector_cols: bool = False, daily: bool = True) -> None:
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(KRX_WEEKLY_DDL)
        c.execute(EVENTS_DDL)
        c.execute(WISE_DDL)
        if with_sector_cols:
            c.execute(SECTOR_COLS)
    _ddl().apply(url)
    with psycopg.connect(url) as c, c.cursor() as cur:
        # 업종분류 스냅샷 둘 — B 는 새 스냅샷에서 빠졌다(공급처가 뺀 종목), C 는 새 스냅샷에서 업종이 바뀌었다
        cur.executemany("INSERT INTO wise_sector VALUES (%s,%s,%s,%s,%s,%s)", [
            ("20260821", A, "G45", "IT", "G4530", "반도체"), ("20260821", B, "G20", "산업재", "G2010", "자본재"),
            ("20260821", C, "G25", "경기소비재", "G2510", "자동차"),
            ("20261016", A, "G45", "IT", "G4530", "반도체"), ("20261016", C, "G20", "산업재", "G2010", "자본재"),
            ("20261016", F, "G35", "헬스케어", "G3510", "건강관리장비와서비스")])     # F = 신규 상장 — 새 스냅샷에만 있다
        # 주간 = 닫힌 주의 마지막 거래일 행 + 연말 행(20251230 — 연도 경계 주는 둘로 나뉜다)
        wk = ["20251230", "20260102", "20260731", "20260925", "20261002", "20261008", "20261016"]
        cur.executemany("INSERT INTO krx_weekly (price_dd, ticker, market, close, mktcap, list_shrs) VALUES (%s,%s,%s,%s,%s,%s)",
                        [(d, t, "KS", _close(t, d), _close(t, d) * SHRS, SHRS) for d in wk for t in (A, B, C, E)])
        if daily:
            # E 는 일별 응답에 없다(거래정지 등)
            # 261019(#124): 일별 저장분은 번호표·달력·사실값 표 + 같은 이름의 뷰다 — 시험 도우미로 넣는다(`INSERT INTO krx_daily` 는 뷰라 안 된다)
            put_daily(c, [(d, t, "KS", _close(t, d) + 1, SHRS) for d in DAYS for t in (A, B, C)])
        ev = [  # (접수번호, 접수일, 종목, 유형, 시장구분)
            ("r01", date(2026, 10, 14), A, "order", "Y"),   # 업종은 0821 스냅샷(1016 은 접수일 뒤)
            ("r02", date(2026, 10, 17), A, "order", "Y"),   # 토요일 → 1016 스냅샷
            ("r03", date(2026, 10, 6), A, "order", "Y"),
            ("r04", date(2026, 8, 1), A, "order", "Y"),     # 첫 스냅샷(0821)보다 이르다 → 가장 이른 스냅샷
            ("r05", date(2026, 10, 17), B, "order", "K"),   # 1016 스냅샷에 없다 → 그 종목의 접수일 이하 가장 최근 스냅샷(0821)
            ("r06", date(2026, 10, 17), C, "treasury", "Y"),  # 1016 스냅샷의 새 업종
            ("r07", date(2026, 10, 15), E, "order", "Y"),   # 업종분류에 없는 종목
            ("r08", date(2026, 10, 14), None, "order", "E"),  # 비상장 → 업종·시총 없음
            ("r09", date(2026, 10, 13), F, "order", "K"),   # 고른 스냅샷(0821)에 없는 신규 상장 → 그 종목의 접수일 뒤 가장 이른 스냅샷(1016)
            ("r10", date(2026, 8, 1), F, "order", "K"),     # 첫 스냅샷보다 이른 공시도 같다(가장 이른 스냅샷 0821 에 없다 → 1016)
        ]
        cur.executemany("INSERT INTO dart_events (rcept_no, rcept_dt, corp_code, corp_name, stock_code, corp_cls, kind, report_nm, "
                        "mktcap_won, mktcap_dd) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        [(no, dt, f"c{no}", f"회사{no}", t, cls, k, "단일판매ㆍ공급계약체결",
                          _stored_cap(no) if t else None, dt.strftime("%Y%m%d") if t else None) for no, dt, t, k, cls in ev])
        if with_sector_cols:   # 업종 복사본 칸에 엉뚱한 값 — 읽으면 시험이 깨진다
            cur.execute("UPDATE dart_events SET sector='복사본', industry='복사본', sector_dd='19000101'")
        c.commit()


@pytest.fixture
def db(pg17, monkeypatch):
    def pg_rows(sql, params=()):
        try:
            with psycopg.connect(pg17, autocommit=True) as c:
                return c.execute(sql, params).fetchall()
        except Exception:
            return None
    monkeypatch.setattr(df, "pg_rows", pg_rows)
    monkeypatch.setattr("open_proxy_mcp.db.pg_rows", pg_rows)
    return pg17


def _fetch(kinds=("order", "treasury"), tickers=None, corp_cls=None, since=date(2026, 7, 1), until=date(2026, 10, 18)):
    rows = df._fetch_events(list(kinds), since, until, tickers, corp_cls)
    assert rows is not None, "SQL 이 실패했다"
    return rows


def _expect() -> dict[str, tuple]:
    return {
        "r01": ("IT", "반도체", _stored_cap("r01")),
        "r02": ("IT", "반도체", _stored_cap("r02")),
        "r03": ("IT", "반도체", _stored_cap("r03")),
        "r04": ("IT", "반도체", _stored_cap("r04")),
        "r05": ("산업재", "자본재", _stored_cap("r05")),
        "r06": ("산업재", "자본재", _stored_cap("r06")),
        "r07": (None, None, _stored_cap("r07")),
        "r08": (None, None, None),
        "r09": ("헬스케어", "건강관리장비와서비스", _stored_cap("r09")),
        "r10": ("헬스케어", "건강관리장비와서비스", _stored_cap("r10")),
    }


def test_sector_is_joined_and_market_cap_is_the_stored_value_without_the_sector_columns(db):
    _load(db)
    rows = _fetch()
    got = {r["rcept_no"]: (r["sector"], r["industry"], r["mktcap_won"]) for r in rows}
    assert got == _expect()
    assert list(rows[0]) == list(df._EVENT_COLS) + ["via_sub", "sub_dup"], "응답 키·순서는 그대로"
    assert all(isinstance(r["mktcap_won"], int) for r in rows if r["mktcap_won"] is not None), "원 단위 정수(bigint 칸 그대로)"
    keys = [(r["rcept_dt"], r["rcept_no"]) for r in rows]
    assert keys == sorted(keys), "접수일·접수번호 순 — 실행마다 같은 순서"


def test_sector_copy_columns_are_ignored_while_they_still_exist(db):
    """칸을 지우기 전(지금 운영)에도 같은 답 — 업종 복사본 칸의 값은 읽지 않는다."""
    _load(db, with_sector_cols=True)
    got = {r["rcept_no"]: (r["sector"], r["industry"], r["mktcap_won"]) for r in _fetch()}
    assert got == _expect()


def test_market_cap_does_not_depend_on_the_price_stores(db):
    """시총은 원장의 저장값 — 일별 시세 저장분이 비어도, 주간 표가 바뀌어도 같은 값(공시 시점 값이 시간이 지나도 그대로다)."""
    _load(db, daily=False)
    with psycopg.connect(db, autocommit=True) as c:
        c.execute("UPDATE krx_weekly SET mktcap = 1")
    got = {r["rcept_no"]: r["mktcap_won"] for r in _fetch()}
    assert got == {k: v[2] for k, v in _expect().items()}


def test_universe_filters_and_date_range_still_apply(db):
    _load(db)
    assert {r["rcept_no"] for r in _fetch(tickers=[B, C])} == {"r05", "r06"}
    assert {r["rcept_no"] for r in _fetch(corp_cls="K")} == {"r05", "r09", "r10"}
    assert {r["rcept_no"] for r in _fetch(kinds=("treasury",))} == {"r06"}
    assert {r["rcept_no"] for r in _fetch(since=date(2026, 10, 14), until=date(2026, 10, 15))} == {"r01", "r07", "r08"}
    # 원장 첫날보다 훨씬 이른 시작(날짜 고르기는 원장 첫날부터) · 공시가 없는 기간
    assert {r["rcept_no"] for r in _fetch(since=date(2000, 1, 1))} == set(_expect())
    assert _fetch(since=date(2026, 9, 1), until=date(2026, 9, 30)) == []


def test_ticker_missing_from_the_picked_snapshot_takes_its_nearest_snapshot(db):
    """261019(PR #110 이 남긴 것): 고른 스냅샷에 종목이 없으면 그 종목의 가장 가까운 스냅샷 — 접수일 이하가 먼저, 없으면 접수일 뒤 가장 이른 것.
    고른 스냅샷에 종목이 있으면 종전 그대로(그 종목의 다른 스냅샷을 보지 않는다 — C 는 새 업종)."""
    _load(db)
    with psycopg.connect(db, autocommit=True) as c:
        # F 가 나중에 다른 업종으로 한 번 더 관측돼도 접수일에 더 가까운 스냅샷을 쓴다
        c.execute("INSERT INTO wise_sector VALUES ('20261120', %s, 'G45', 'IT', 'G4530', '반도체')", (F,))
    got = {r["rcept_no"]: (r["sector"], r["industry"]) for r in _fetch(until=date(2026, 10, 18))}
    assert got["r09"] == ("헬스케어", "건강관리장비와서비스") and got["r10"] == ("헬스케어", "건강관리장비와서비스")
    assert got["r06"] == ("산업재", "자본재") and got["r05"] == ("산업재", "자본재")
    assert got["r07"] == (None, None), "어느 스냅샷에도 없는 종목은 비운다"


def test_empty_ledger_and_empty_sector_table(db):
    with psycopg.connect(db, autocommit=True) as c:
        c.execute(EVENTS_DDL)
        c.execute(WISE_DDL)
    assert _fetch() == []
    with psycopg.connect(db, autocommit=True) as c:
        c.execute("INSERT INTO dart_events (rcept_no, rcept_dt, stock_code, corp_cls, kind, mktcap_won) "
                  "VALUES ('z1', '2026-10-14', %s, 'Y', 'order', 5)", (A,))
    rows = _fetch()
    assert [(r["rcept_no"], r["sector"], r["mktcap_won"]) for r in rows] == [("z1", None, 5)], "업종분류 스냅샷이 없으면 업종만 비운다"


# ── 업종 비교의 그 날 시총(`_firm_cap_at`) ──────────────────────────────────────────────────

def _cap(t, d, *, daily=False):
    return float((_close(t, d) + (1 if daily else 0)) * SHRS)


def test_firm_cap_at_exact_day_weekly_then_daily(db):
    _load(db)
    assert asyncio.run(S._firm_cap_at(A, "20261016")) == (_cap(A, "20261016"), "KS"), "같은 날 둘 다 있으면 주간 행"
    assert asyncio.run(S._firm_cap_at(A, "20261014")) == (_cap(A, "20261014", daily=True), "KS"), "주간 행이 없으면 그 날 일별 행"


def test_firm_cap_at_falls_back_within_the_same_week(db):
    """그 날 행이 정확히 없으면 같은 주(ISO 주를 달력 연도 안으로 자른 것) 안 그 날 이하 가장 늦은 행, 그것도 없으면 그 날 뒤 가장 이른 행 —
    각 단계 주간 먼저, 없으면 일별. 다른 주로는 넘어가지 않는다."""
    _load(db)
    assert asyncio.run(S._firm_cap_at(A, "20261009")) == (_cap(A, "20261008"), "KS"), "휴장일 → 같은 주 앞 거래일의 주간 행"
    assert asyncio.run(S._firm_cap_at(A, "20261011")) == (_cap(A, "20261008"), "KS"), "일요일"
    assert asyncio.run(S._firm_cap_at(A, "20261018")) == (_cap(A, "20261016"), "KS")
    # 그 날 이하 행이 없으면 같은 주 뒤 날짜: E 는 일별 행이 없고, 같은 주 주간 행(10/16)은 기준일(10/14) 뒤 — 지난주 행(10/8)은 다른 주라 쓰지 않는다
    assert asyncio.run(S._firm_cap_at(E, "20261014")) == (_cap(E, "20261016"), "KS")
    assert asyncio.run(S._firm_cap_at(E, "20261012")) == (_cap(E, "20261016"), "KS")
    assert asyncio.run(S._firm_cap_at(E, "20261005")) == (_cap(E, "20261008"), "KS")
    # 같은 주에 아무 행도 없으면 비운다(다음 주 월요일 — 지난주 행으로 내려가지 않는다)
    assert asyncio.run(S._firm_cap_at(E, "20261019")) == (None, None)
    # 연도 경계 주는 둘로 나뉜다 — 12/31(휴장)은 같은 해 연말 행, 1/1(휴장)은 같은 해(2026) 쪽 첫 거래일 행 · 지난해 연말 행은 쓰지 않는다
    assert asyncio.run(S._firm_cap_at(A, "20251231")) == (_cap(A, "20251230"), "KS")
    assert asyncio.run(S._firm_cap_at(A, "20260101")) == (_cap(A, "20260102"), "KS")
    # 주간 행이 없는 주 → 일별 저장분의 같은 주·그 날 이하 가장 늦은 행
    with psycopg.connect(db, autocommit=True) as c:
        c.execute("DELETE FROM krx_weekly WHERE price_dd = '20261016'")
    assert asyncio.run(S._firm_cap_at(B, "20261017")) == (_cap(B, "20261016", daily=True), "KS")
    assert asyncio.run(S._firm_cap_at(B, "20261018")) == (_cap(B, "20261016", daily=True), "KS")


def test_firm_cap_at_order_earlier_before_later_weekly_before_daily(db):
    """고르는 순서: 그 날(주간 → 일별) → 그 날 이하(주간 → 일별) → 그 날 뒤(주간 → 일별)."""
    _load(db)
    with psycopg.connect(db, autocommit=True) as c:
        # A: 10/12 일별 행이 빠졌다(그 날 행 없음, 같은 주 그 날 이하 행도 없음) · 10/14 일별 행도 빠졌다(10/13 일별 행은 있다)
        _drop_daily(c, A, ("20261012", "20261014"))
        # B: 같은 주 주간 행(10/16)이 없고 일별은 10/14 부터
        c.execute("DELETE FROM krx_weekly WHERE ticker=%s AND price_dd='20261016'", (B,))
        _drop_daily(c, B, ("20261012", "20261013"))
    # 그 날 이하 일별(10/13)이 그 날 뒤 주간(10/16)보다 먼저
    assert asyncio.run(S._firm_cap_at(A, "20261014")) == (_cap(A, "20261013", daily=True), "KS")
    # 그 날 이하가 없으면 그 날 뒤 — 주간(10/16)이 일별(10/13)보다 먼저
    assert asyncio.run(S._firm_cap_at(A, "20261012")) == (_cap(A, "20261016"), "KS")
    # 그 날 뒤 주간도 없으면 일별의 그 날 뒤 가장 이른 행(10/14)
    assert asyncio.run(S._firm_cap_at(B, "20261012")) == (_cap(B, "20261014", daily=True), "KS")
