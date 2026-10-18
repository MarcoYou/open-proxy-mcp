"""같은 값을 다시 쓰지 않는 배치 쓰기(재설계 v02 7단계) — 같은 값으로 UPDATE 해도 Postgres 는 행을 새로 써 죽은 행을 남긴다.

  · `scripts/adj_factor_v3.py` `UPDATE_LABELS` — 매일 미판정 전부(운영 885건)를 다시 판정해 같은 값으로 고쳐 쓰던 것.
  · `scripts/market_val_history_backfill.py` `UPSERT_MKT`·`UPSERT_SEC` — 2020년 이후 월말 전부를 같은 값으로 다시 쓰던 것.

계약: ① 같은 값이면 행이 다시 쓰이지 않는다(xmin 그대로·영향 행 0) ② 칸 하나라도 바뀌면(NULL↔값 포함) 그 행만 쓴다 ③ 새 키는 종전대로 들어간다.
Postgres 17 에서만 검증된다(`tests/conftest.py` 의 `pg17` — Docker 가 없으면 건너뜀).
"""
from __future__ import annotations

import pytest

psycopg = pytest.importorskip("psycopg")

from scripts import adj_factor_v3 as A                       # noqa: E402
from scripts import market_val_history_backfill as H        # noqa: E402


def _xmins(c, table, order):
    return [r[0] for r in c.execute(f"SELECT xmin::text FROM {table} ORDER BY {order}").fetchall()]


def test_label_update_rewrites_only_rows_whose_labels_changed(pg17):
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute("""CREATE TABLE krx_adj_events (ticker text, event_dd text, market text, adj_factor_raw double precision,
                     adj_factor double precision, event_type text, rcept_no text, label_source text, label_confidence text,
                     note text, PRIMARY KEY (ticker, event_dd))""")
        c.execute("INSERT INTO krx_adj_events (ticker, event_dd, adj_factor_raw, label_confidence) "
                  "SELECT lpad(g::text, 6, '0'), '20260105', 0.5, 'unlabeled' FROM generate_series(1, 4) g")
    # (isu, dd, f, raw, etype, src, evid, conf, note) — 스크립트가 만드는 모양 그대로
    rows = [(f"{g:06d}", "20260105", 0.5, 0.5, None, "reset", None, "unlabeled", None) for g in range(1, 5)]

    def run(rs):
        params = [(f, e, s, ev, cf, n, f, e, s, ev, cf, n, isu, dd) for (isu, dd, f, _r, e, s, ev, cf, n) in rs]
        with psycopg.connect(pg17) as c:
            with c.cursor() as cur:
                cur.executemany(A.UPDATE_LABELS, params)
                return cur.rowcount

    assert run(rows) == 4                                        # 처음: label_source·adj_factor 가 비어 있었다 → 4행
    with psycopg.connect(pg17) as c:
        before = _xmins(c, "krx_adj_events", "ticker")
    assert run(rows) == 0                                        # 같은 판정 → 아무 행도 다시 쓰지 않는다
    with psycopg.connect(pg17) as c:
        assert _xmins(c, "krx_adj_events", "ticker") == before
    rows[2] = rows[2][:4] + ("split", "dart", "20260105000123", "dart", "액면비율 1/5 스냅")   # 한 행만 판정이 바뀜
    assert run(rows) == 1
    with psycopg.connect(pg17) as c:
        after = _xmins(c, "krx_adj_events", "ticker")
        assert [a == b for a, b in zip(before, after)] == [True, True, False, True]
        assert c.execute("SELECT event_type, label_confidence FROM krx_adj_events WHERE ticker = '000003'").fetchone() == ("split", "dart")


def test_label_update_statement_keeps_the_key_last_and_never_deletes():
    sql = " ".join(A.UPDATE_LABELS.split())
    assert sql.startswith("UPDATE krx_adj_events SET")
    assert "IS DISTINCT FROM" in sql and sql.endswith("AND ticker = %s AND event_dd = %s")
    assert sql.count("%s") == 14


def _opm_val_market(c):
    c.execute("""CREATE TABLE opm_val_market (snap_dd text, market text, scheme text DEFAULT 'ksic', sector text,
                 label text, n integer, cap double precision, per_fy0 double precision, pbr_fy0 double precision,
                 per_ttm double precision, pbr_mrq double precision, ni_fy0 double precision, ni_ttm double precision,
                 PRIMARY KEY (snap_dd, market, scheme, sector))""")


def test_history_backfill_upserts_skip_identical_rows(pg17):
    with psycopg.connect(pg17, autocommit=True) as c:
        _opm_val_market(c)
    mk = [("20251230", "KS", 12.1, 1.1, 11.0, 1.0, 2.5e15, 2.0e14, None),
          ("20251230", "KQ", None, 2.0, 40.0, 1.9, 4.0e14, None, 1.0e13)]
    sec = [("20251230", "KS", "C26", "전자", 120, 9.0e14, 15.0, 1.5, 14.0, 1.4, 6.0e13, 6.4e13)]

    def run(m, s):
        with psycopg.connect(pg17) as c:
            with c.cursor() as cur:
                cur.executemany(H.UPSERT_MKT, m)
                a = cur.rowcount
                cur.executemany(H.UPSERT_SEC, s)
                return a, cur.rowcount

    assert run(mk, sec) == (2, 1)                                # 새 키는 들어간다
    with psycopg.connect(pg17) as c:
        before = _xmins(c, "opm_val_market", "market, sector")
    assert run(mk, sec) == (0, 0)                                # 같은 값 → 다시 쓰지 않는다
    with psycopg.connect(pg17) as c:
        assert _xmins(c, "opm_val_market", "market, sector") == before
    mk[1] = mk[1][:7] + (5.0e12,) + mk[1][8:]                    # NULL → 값 (KQ ni_fy0)
    sec[0] = sec[0][:4] + (121,) + sec[0][5:]                    # 종목 수만 바뀜
    assert run(mk, sec) == (1, 1)
    with psycopg.connect(pg17) as c:
        assert c.execute("SELECT ni_fy0 FROM opm_val_market WHERE market = 'KQ'").fetchone()[0] == 5.0e12
        assert c.execute("SELECT n FROM opm_val_market WHERE sector = 'C26'").fetchone()[0] == 121
        assert c.execute("SELECT scheme FROM opm_val_market WHERE sector = '_ALL' AND market = 'KS'").fetchone()[0] == "market"
