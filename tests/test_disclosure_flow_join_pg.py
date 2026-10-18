"""공시 흐름 보기의 업종·시총을 **읽을 때 조인**한다 — Postgres 17 에서 실제 SQL 로 검증 (261018, Supabase 재설계 6단계).

종전: 원장 배치가 넣을 때 복사해 둔 `dart_events.sector`·`industry`·`mktcap_won` 을 읽었다. 이제 원본 표(`wise_sector`·`krx_daily_v`·`krx_weekly`)와
조인한다(`disclosure_flow._EVENTS_SQL`). 규칙(원장 배치가 붙이던 것과 같은 출처·같은 선택):
  · 업종 = 접수일 이하 가장 최근 업종분류 스냅샷(없으면 가장 이른 것). 스냅샷은 표 전체에서 고르고, 그 스냅샷에 종목이 없으면 비운다.
  · 시총 = 접수일 이하 최신 거래일. 일별 저장분 창(최근 7거래일) 안이면 그 거래일 일별 행, 창보다 이르면 접수일 이하 가장 최근 주간 행.
          창 안인데 그 종목 일별 행이 없으면 주간 행으로 대신한다. 일별 저장분이 비면 전부 주간.
  · 복사본 칸이 **없는** 표(칸을 지운 뒤)에서도 돌고, 칸이 **있어도 읽지 않는다**(엉뚱한 값을 넣어 두고 확인).
같은 파일에서 `price_multiple_data._firm_cap_at`(업종 비교의 그 날 시총)도 실제 SQL 로 확인한다.

로컬 Docker 의 `postgres:17` 이 필요하다(`tests/conftest.py` 의 `pg17`). 없으면 건너뛴다. network 0.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

psycopg = pytest.importorskip("psycopg")

from open_proxy_mcp.services import disclosure_flow as df  # noqa: E402
from open_proxy_mcp.services import price_multiple_data as S  # noqa: E402
from test_krx_daily_pg import KRX_WEEKLY_DDL, _ddl  # noqa: E402

# 운영의 원장에서 칸 셋을 지운 뒤 모양(복사본 칸 없음). 칸이 있는 모양은 `with_copy_cols=True` 로 더한다.
EVENTS_DDL = """CREATE TABLE dart_events (
  rcept_no text PRIMARY KEY, rcept_dt date NOT NULL, corp_code text, corp_name text, stock_code text, corp_cls text,
  kind text NOT NULL, subtype text, stage text, is_correction boolean NOT NULL DEFAULT false, dedup_key text,
  report_nm text, flr_nm text, detail_status text, detail jsonb, detail_note text,
  first_seen_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now())"""
COPY_COLS = ("ALTER TABLE dart_events ADD COLUMN sector text, ADD COLUMN industry text, ADD COLUMN sector_dd text, "
             "ADD COLUMN mktcap_won bigint, ADD COLUMN mktcap_dd text")
WISE_DDL = """CREATE TABLE wise_sector (snap_dd text NOT NULL, ticker text NOT NULL, sector_code text, sector text,
  industry_code text, industry text, PRIMARY KEY (snap_dd, ticker))"""

DAYS = ["20261007", "20261008", "20261012", "20261013", "20261014", "20261015", "20261016"]   # 10/9 한글날 휴장
A, B, C, E = "000001", "000002", "000003", "000005"
SHRS = 1_000_000


def _close(t: str, d: str) -> int:
    return {A: 10_000, B: 20_000, C: 30_000, E: 50_000}[t] + int(d[-2:]) * 10


def _load(url: str, *, with_copy_cols: bool = False, daily: bool = True) -> None:
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(KRX_WEEKLY_DDL)
        c.execute(EVENTS_DDL)
        c.execute(WISE_DDL)
        if with_copy_cols:
            c.execute(COPY_COLS)
    _ddl().apply(url)
    with psycopg.connect(url) as c, c.cursor() as cur:
        # 업종분류 스냅샷 둘 — B 는 새 스냅샷에서 빠졌다(공급처가 뺀 종목), C 는 새 스냅샷에서 업종이 바뀌었다
        cur.executemany("INSERT INTO wise_sector VALUES (%s,%s,%s,%s,%s,%s)", [
            ("20260821", A, "G45", "IT", "G4530", "반도체"), ("20260821", B, "G20", "산업재", "G2010", "자본재"),
            ("20260821", C, "G25", "경기소비재", "G2510", "자동차"),
            ("20261016", A, "G45", "IT", "G4530", "반도체"), ("20261016", C, "G20", "산업재", "G2010", "자본재")])
        # 주간 = 주 마지막 거래일 행. 10/16 은 이번 주 행(지금 쓰는 쪽은 매일 다시 쓴다)
        wk = ["20260731", "20260925", "20261002", "20261008", "20261016"]
        cur.executemany("INSERT INTO krx_weekly (price_dd, ticker, market, close, mktcap, list_shrs) VALUES (%s,%s,%s,%s,%s,%s)",
                        [(d, t, "KS", _close(t, d), _close(t, d) * SHRS, SHRS) for d in wk for t in (A, B, C, E)])
        if daily:
            # E 는 일별 응답에 없다(거래정지 등) — 창 안 공시는 주간 행으로 대신해야 한다
            cur.executemany("INSERT INTO krx_daily (trd_dd, ticker, market, close, list_shrs) VALUES (%s,%s,%s,%s,%s)",
                            [(d, t, "KS", _close(t, d) + 1, SHRS) for d in DAYS for t in (A, B, C)])
        ev = [  # (접수번호, 접수일, 종목, 유형, 시장구분)
            ("r01", date(2026, 10, 14), A, "order", "Y"),   # 창 안 수요일 → 그날 일별 시총, 업종은 0821 스냅샷(1016 은 뒤)
            ("r02", date(2026, 10, 17), A, "order", "Y"),   # 창 안 토요일 → 10/16 일별, 업종은 1016 스냅샷
            ("r03", date(2026, 10, 6), A, "order", "Y"),    # 창보다 이른 화요일 → 주간 10/2(금)
            ("r04", date(2026, 8, 1), A, "order", "Y"),     # 첫 스냅샷(0821)보다 이르다 → 가장 이른 스냅샷 · 주간 7/31
            ("r05", date(2026, 10, 17), B, "order", "K"),   # 1016 스냅샷에 없다 → 업종 비움(0821 로 내려가지 않는다)
            ("r06", date(2026, 10, 17), C, "treasury", "Y"),  # 1016 스냅샷의 새 업종
            ("r07", date(2026, 10, 15), E, "order", "Y"),   # 창 안인데 일별 행 없음 → 주간 10/8 로 대신
            ("r08", date(2026, 10, 14), None, "order", "E"),  # 비상장 → 업종·시총 없음
        ]
        cur.executemany("INSERT INTO dart_events (rcept_no, rcept_dt, corp_code, corp_name, stock_code, corp_cls, kind, report_nm) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        [(no, dt, f"c{no}", f"회사{no}", t, cls, k, "단일판매ㆍ공급계약체결") for no, dt, t, k, cls in ev])
        if with_copy_cols:   # 복사본 칸에 엉뚱한 값 — 읽으면 시험이 깨진다
            cur.execute("UPDATE dart_events SET sector='복사본', industry='복사본', mktcap_won=1, sector_dd='19000101', mktcap_dd='19000101'")
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


def _fetch(kinds=("order", "treasury"), tickers=None, corp_cls=None):
    rows = df._fetch_events(list(kinds), date(2026, 7, 1), date(2026, 10, 18), tickers, corp_cls)
    assert rows is not None, "SQL 이 실패했다"
    return rows


def _expect() -> dict[str, tuple]:
    d = lambda t, dd: (_close(t, dd) + 1) * SHRS       # noqa: E731 — 일별 행(주간보다 1원 높게 넣어 출처를 가른다)
    w = lambda t, dd: _close(t, dd) * SHRS             # noqa: E731
    return {
        "r01": ("IT", "반도체", d(A, "20261014")),
        "r02": ("IT", "반도체", d(A, "20261016")),
        "r03": ("IT", "반도체", w(A, "20261002")),
        "r04": ("IT", "반도체", w(A, "20260731")),
        "r05": (None, None, d(B, "20261016")),
        "r06": ("산업재", "자본재", d(C, "20261016")),
        "r07": (None, None, w(E, "20261008")),                # E 는 업종분류에도 없다
        "r08": (None, None, None),
    }


def test_join_follows_the_ledger_rules_without_the_copy_columns(db):
    _load(db)
    rows = _fetch()
    got = {r["rcept_no"]: (r["sector"], r["industry"], r["mktcap_won"]) for r in rows}
    assert got == _expect()
    assert list(rows[0]) == list(df._EVENT_COLS) + ["via_sub", "sub_dup"], "응답 키·순서는 그대로"
    assert all(isinstance(r["mktcap_won"], int) for r in rows if r["mktcap_won"] is not None), "원 단위 정수(종전 bigint 칸과 같은 형)"
    keys = [(r["rcept_dt"], r["rcept_no"]) for r in rows]
    assert keys == sorted(keys), "접수일·접수번호 순 — 조인 계획에 따라 흔들리지 않는다"


def test_copy_columns_are_ignored_when_they_still_exist(db):
    """칸을 지우기 전(지금 운영)에도 같은 답 — 복사본 칸의 값은 읽지 않는다."""
    _load(db, with_copy_cols=True)
    got = {r["rcept_no"]: (r["sector"], r["industry"], r["mktcap_won"]) for r in _fetch()}
    assert got == _expect()


def test_without_the_daily_store_every_event_uses_the_weekly_rows(db):
    _load(db, daily=False)
    got = {r["rcept_no"]: r["mktcap_won"] for r in _fetch()}
    assert got["r01"] == _close(A, "20261008") * SHRS, "창이 없으면 접수일 이하 가장 최근 주(10/8)"
    assert got["r02"] == _close(A, "20261016") * SHRS


def test_universe_filters_still_apply(db):
    _load(db)
    assert {r["rcept_no"] for r in _fetch(tickers=[B, C])} == {"r05", "r06"}
    assert {r["rcept_no"] for r in _fetch(corp_cls="K")} == {"r05"}
    assert {r["rcept_no"] for r in _fetch(kinds=("treasury",))} == {"r06"}


def test_firm_cap_at_prefers_the_weekly_row_then_the_daily_store(db):
    _load(db)
    assert asyncio.run(S._firm_cap_at(A, "20261016")) == (float(_close(A, "20261016") * SHRS), "KS"), "같은 날 둘 다 있으면 주간 행"
    assert asyncio.run(S._firm_cap_at(A, "20261014")) == (float((_close(A, "20261014") + 1) * SHRS), "KS"), "주간 행이 없으면 일별"
    assert asyncio.run(S._firm_cap_at(A, "20261010")) == (None, None), "둘 다 없는 날(토요일)"
    assert asyncio.run(S._firm_cap_at(E, "20261014")) == (None, None)
