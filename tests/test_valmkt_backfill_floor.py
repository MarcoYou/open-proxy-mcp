"""시장·업종 배수 백필의 보존 하한 가드 — 하한 이전 부분 행으로 저장된 월말을 덮지 않는다(261019).

DB krx_weekly 는 보존 하한(최신 주 3년 전) 이전을 종목별 연말 행만 남긴다. 종전 두 백필(`market_val_history_backfill.py`·
`sector_val_backfill.py`)은 월말을 `DISTINCT ON (월) max(price_dd)` 로 골라, 하한 이전 달마다 그 달에 상장폐지한 종목 몇 개의 마지막 행
날짜를 월말로 집었다 — 1~8종목짜리 집계가 저장된 월말 행을 덮거나 엉뚱한 날짜(20200110)에 새 행을 만들었다. 업종분류 월간 일정
(매월 1일)이 기본 `--since 20200101` 로 돈다(운영 10-19 실측: 2020-01~2023-09 의 41개 달 중 37개).

  · 서빙본 없이: 하한 이전은 연말(그해 가장 늦은 날짜 — 온전하다)만 다시 내고, 나머지 달은 건너뛰어 저장된 행을 그대로 둔다.
  · 서빙본(`--cold-sqlite`)이 있으면: 하한 이전 달도 그 달의 진짜 월말로 다시 낸다(서버 곡선과 같은 이음매).
  · 최소 커버리지: 배수를 낸 회사 시총이 버킷 시총의 절반 미만이면 배수와 분모 합을 비운다(화면 「자료없음」).
  · 시장 백필은 주간 스냅샷이 쓴 행(`cap_pref` 있음)을 덮지 않고, KSIC 동결(2026-08-23) 뒤 월말은 KSIC 행을 만들지 않는다.
Postgres 시험은 Docker 가 없으면 건너뛴다(`tests/conftest.py` 의 `pg17`).
"""
from __future__ import annotations

import asyncio
import sys

import pytest

from conftest import build_cold_serving
from scripts import month_end_prices as M


# ── 월말 고르기(순수 함수) ──────────────────────────────────────────────────
DB_DATES = ["20200110",                 # 1월에 상장폐지한 종목의 마지막 행(부분)
            "20200228",                 # 2월 말 날짜지만 그날 상장폐지한 종목 한 줄뿐(부분) — 날짜가 진짜 월말과 같아도 쓰면 안 된다
            "20201230",                 # 2020 연말 — 그해 말 살아 있던 모든 종목의 마지막 행(온전)
            "20210108", "20211230", "20230915",
            "20231020", "20231027", "20231103", "20231124", "20231229"]


def test_without_cold_only_year_ends_survive_below_the_floor():
    p = M.plan_month_ends(DB_DATES, "20231016")
    assert p.dates == ["20201230", "20211230", "20231027", "20231124", "20231229"]
    assert all(p.src[d] == "db" for d in p.dates)
    assert p.skipped == ["202001", "202002", "202101", "202309"]       # 건너뛴 달 — 저장된 행 그대로


def test_with_cold_months_below_the_floor_use_the_cold_month_end_never_a_partial_db_date():
    cold = ["20200103", "20200110", "20200117", "20200124", "20200131", "20200228", "20201230",
            "20230915", "20230922", "20230927", "20231006", "20231013"]
    p = M.plan_month_ends(DB_DATES, "20231016", cold_dates=cold, seam="20231016", gap=False)
    assert "20200110" not in p.dates and "20200131" in p.dates and p.src["20200131"] == "cold"
    assert p.src["20200228"] == "cold" and p.src["20201230"] == "cold"   # 같은 날짜면 서빙본(같은 값)으로
    assert [d for d in p.dates if d[:6] == "202309"] == ["20230927"]
    assert [d for d in p.dates if d[:6] == "202310"] == ["20231027"]     # 이음매에 걸친 달 — DB 촘촘한 구간의 마지막 주
    assert p.src["20231027"] == "db"
    assert p.skipped == ["202101"]                                       # 서빙본에도 없는 달(부분 DB 날짜뿐)


def test_with_a_seam_gap_months_touching_the_gap_are_skipped():
    # 서빙본은 10-15 까지, DB 촘촘한 구간은 11-06 부터 → 10-16~11-03 거래일 틈. 10월 마지막 주(10-27)가 틈에 있어
    # 서빙본의 10-13 을 10월 월말로 쓰면 틀린다.
    db = ["20231013", "20231027", "20231110", "20231124"]               # 하한 아래 두 날짜는 부분 행
    cold = ["20230929", "20231006", "20231013"]
    p = M.plan_month_ends(db, "20231106", cold_dates=cold, seam="20231016", gap=True)
    assert p.dates == ["20230929", "20231124"]
    assert p.skipped == ["202310"]


def test_covered_is_half_of_the_bucket_cap():
    assert M.covered(50.0, 100.0) and not M.covered(49.9, 100.0)
    assert not M.covered(0.0, 0.0)                                       # 버킷에 센 회사가 없으면 내지 않는다
    assert M.covered(10.0, 100.0, min_cover=0.1)


# ── Postgres 17: 두 백필을 실제로 돌린다 ──────────────────────────────────
psycopg = pytest.importorskip("psycopg")

from scripts import market_val_history_backfill as H          # noqa: E402
from scripts import sector_val_backfill as S                  # noqa: E402

# 시총(종가 × 상장주식수): A 10,000 · B 1,000 (코스피) · C 5,000 (코스닥) · D 100(2020-01-10 상장폐지) · E 200(2020-02-28 상장폐지)
# D·E 는 운영의 상장폐지 종목처럼 재무·분류가 있다 — 그래야 종전 고르기가 그 마지막 행 날짜로 집계를 낸다(고치기 전 실패 재현).
PX = {"A": ("KS", 100), "B": ("KS", 10), "C": ("KQ", 50), "D": ("KS", 1), "E": ("KS", 2)}
LAST = {"D": "20200110", "E": "20200228"}
OLD = 99.0                                                       # 저장돼 있던 옛 값 — 덮이면 안 되는 표지


def _cold_rows():
    rows = []
    for dd in ("20200110", "20200124", "20200131", "20200228", "20201230", "20231013"):
        for t, (m, close) in PX.items():
            if dd > LAST.get(t, "99999999"):
                continue
            rows.append((t, dd, m, close, 100))
    return rows


@pytest.fixture
def vdb(pg17, monkeypatch, tmp_path):
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute("CREATE TABLE krx_weekly (price_dd text, ticker text, market text, close bigint, mktcap bigint, list_shrs bigint, "
                  "PRIMARY KEY (price_dd, ticker))")
        rows = [("20200110", "D"), ("20200228", "E")]                          # 하한 아래: 상장폐지 종목의 마지막 행(부분 날짜)
        rows += [("20201230", t) for t in ("A", "B", "C")]                     # 2020 연말 행(온전)
        dense = ["20231020", "20231027", "20231124", "20260731", "20260828", "20261016"]
        rows += [(d, t) for d in dense for t in ("A", "B", "C")]
        c.cursor().executemany("INSERT INTO krx_weekly VALUES (%s,%s,%s,%s,%s,100)",
                      [(d, t, PX[t][0], PX[t][1], PX[t][1] * 100) for d, t in rows])
        c.execute("CREATE TABLE wise_sector (snap_dd text, ticker text, sector_code text, sector text, industry_code text, industry text)")
        c.execute("INSERT INTO wise_sector VALUES ('20260821','A','G45','IT','G4530','반도체'), ('20260821','B','G40','금융','G4010','은행'), "
                  "('20260821','C','G45','IT','G4530','반도체'), ('20260821','D','G45','IT','G4530','반도체'), "
                  "('20260821','E','G45','IT','G4530','반도체')")
        c.execute("CREATE TABLE dart_finstat_y (ticker text, fy int, fs text, ni double precision, eq double precision, fetched text, "
                  "ni_restated double precision, eq_restated double precision)")
        fin = {"A": (1000.0, 5000.0), "B": (100.0, 800.0), "C": (200.0, 2000.0), "D": (5.0, 50.0), "E": (10.0, 100.0)}
        c.cursor().executemany("INSERT INTO dart_finstat_y VALUES (%s,%s,'CFS',%s,%s,'ok',NULL,NULL)",
                      [(t, fy, ni, eq) for t, (ni, eq) in fin.items() for fy in range(2018, 2026)])
        c.execute("CREATE TABLE dart_finstat_q (ticker text, fy int, quarter int, ni_cum double precision, eq double precision, "
                  "ni_cum_restated double precision, eq_restated double precision)")
        # 2020-01 의 TTM 칸 = FY2018 + 누적(2019 3분기) − 누적(2018 3분기): B(시총 1,000)만 있다 → 코스피 시총의 9% → 비운다.
        # MRQ 칸 = 자본(2019 3분기): A·B·C 모두.
        c.cursor().executemany("INSERT INTO dart_finstat_q VALUES (%s,%s,%s,%s,%s,NULL,NULL)",
                      [("B", 2018, 3, 60.0, 780.0), ("B", 2019, 3, 70.0, 790.0),
                       ("A", 2019, 3, None, 4900.0), ("C", 2019, 3, None, 1900.0), ("E", 2019, 3, None, 95.0)])
        c.execute("CREATE TABLE dart_fundamentals (ticker text, market text, induty text, fetched text)")
        c.execute("INSERT INTO dart_fundamentals VALUES ('A','KS','26410','ok'), ('B','KS','64110','ok'), ('C','KQ','26410','ok'), "
                  "('D','KS','26410','ok'), ('E','KS','26410','ok')")
        c.execute("""CREATE TABLE opm_val_market (snap_dd text, market text, scheme text DEFAULT 'ksic', sector text, label text, n integer,
                     cap double precision, per_fy0 double precision, pbr_fy0 double precision, per_ttm double precision,
                     pbr_mrq double precision, ni_fy0 double precision, ni_ttm double precision, cap_pref double precision,
                     eq double precision, PRIMARY KEY (snap_dd, market, scheme, sector))""")
        old = []
        for dd in ("20200131", "20200228"):                                  # 하한 이전 월말 — 콜드 없이는 그대로 남아야 한다
            for m in ("KS", "KQ"):
                old.append((dd, m, "market", "_ALL", None, None, 1.0, OLD, OLD, OLD, OLD, 1.0, 1.0, None, None))
            old.append((dd, "KS", "wics_sector", "G45", "IT", 1, 1.0, OLD, OLD, OLD, OLD, 1.0, 1.0, None, None))
        # 주간 스냅샷이 쓴 시장 행(cap_pref 있음) — 과거 밴드 백필이 덮지 않는다
        old.append(("20260731", "KS", "market", "_ALL", None, 2, 1.0, OLD, OLD, OLD, OLD, 1.0, 1.0, 5.0, 7.0))
        c.cursor().executemany("INSERT INTO opm_val_market VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", old)
    built = build_cold_serving(tmp_path, _cold_rows(), upto="20231016")
    monkeypatch.setenv("DATABASE_URL", pg17)
    return pg17, str(built["sqlite_path"])


def _rows(url):
    with psycopg.connect(url) as c:
        cur = c.execute("SELECT snap_dd, market, scheme, sector, n, cap, per_fy0, pbr_fy0, per_ttm, pbr_mrq, ni_fy0, ni_ttm "
                        "FROM opm_val_market ORDER BY 1,2,3,4")
        return {tuple(r[:4]): dict(zip(("n", "cap", "per_fy0", "pbr_fy0", "per_ttm", "pbr_mrq", "ni_fy0", "ni_ttm"), r[4:]))
                for r in cur.fetchall()}


def _run_both(monkeypatch, *extra):
    monkeypatch.setattr(sys, "argv", ["market_val_history_backfill.py", *extra])
    asyncio.run(H.main())
    monkeypatch.setattr(sys, "argv", ["sector_val_backfill.py", *extra])
    assert S.main() == 0


def test_without_cold_partial_dates_never_overwrite_or_create_rows(vdb, monkeypatch, capsys):
    url, _ = vdb
    before = _rows(url)
    _run_both(monkeypatch)
    after = _rows(url)
    assert not [k for k in after if k[0] == "20200110"]                          # 부분 날짜(D 한 종목)에 새 행 없음
    for k, v in before.items():
        if k[0] in ("20200131", "20200228"):
            assert after[k] == v                                                   # 하한 이전 월말 = 옛 값 그대로(20200228 = E 한 종목 날짜)
    ks = after[("20201230", "KS", "market", "_ALL")]                               # 연말은 DB 연말 행(온전)으로 다시 낸다
    assert ks["per_fy0"] == pytest.approx(11000 / 1100)
    assert after[("20231027", "KQ", "wics_sector", "G45")]["per_fy0"] == pytest.approx(5000 / 200)
    assert "건너뜀" in capsys.readouterr().out


def test_with_cold_months_below_the_floor_are_recomputed_at_the_true_month_end(vdb, monkeypatch):
    url, sqlite_path = vdb
    _run_both(monkeypatch, "--cold-sqlite", sqlite_path)
    after = _rows(url)
    assert not [k for k in after if k[0] in ("20200110", "20200124")]              # 월말이 아닌 날짜 · 부분 날짜에 행 없음
    ks = after[("20200131", "KS", "market", "_ALL")]
    assert ks["per_fy0"] == pytest.approx(11200 / 1110)                            # 서빙본 시총 A 10,000 + B 1,000 + E 200
    assert ks["pbr_mrq"] == pytest.approx(11200 / (4900 + 790 + 95))
    # 최소 커버리지: 2020-01 TTM 은 B(코스피 시총 9%)만 셀 수 있다 → PER 과 분모 합을 함께 비운다(화면 「자료없음」)
    assert ks["per_ttm"] is None and ks["ni_ttm"] is None
    kq = after[("20200131", "KQ", "market", "_ALL")]
    assert kq["per_fy0"] == pytest.approx(5000 / 200) and kq["per_ttm"] is None
    it = after[("20200228", "KS", "wics_sector", "G45")]                           # 2월 말 — 상장폐지 전날까지 산 E 포함, D 는 없다
    assert it["per_fy0"] == pytest.approx(10200 / 1010) and it["n"] == 2
    assert after[("20200228", "KS", "wics_sector", "G40")]["pbr_mrq"] == pytest.approx(1000 / 790)


def test_history_backfill_leaves_weekly_snapshot_rows_and_frozen_ksic_alone(vdb, monkeypatch):
    url, _ = vdb
    _run_both(monkeypatch)
    after = _rows(url)
    assert after[("20260731", "KS", "market", "_ALL")]["per_fy0"] == OLD          # 주간 스냅샷 행(cap_pref) — 덮지 않는다
    assert after[("20260731", "KQ", "market", "_ALL")]["per_fy0"] == pytest.approx(25.0)   # 스냅샷이 없던 키는 종전처럼 넣는다
    assert [k for k in after if k[0] == "20260731" and k[2] == "ksic"]           # 동결 전 월말 — KSIC 행 있음
    assert not [k for k in after if k[0] == "20260828" and k[2] == "ksic"]       # 동결(2026-08-23) 뒤 월말 — KSIC 행 없음
    assert [k for k in after if k[0] == "20260828" and k[2] == "market"]


def test_sector_backfill_rewrites_only_changed_rows(vdb, monkeypatch):
    url, _ = vdb
    monkeypatch.setattr(sys, "argv", ["sector_val_backfill.py"])
    assert S.main() == 0
    with psycopg.connect(url) as c:
        before = c.execute("SELECT snap_dd, market, scheme, sector, xmin::text FROM opm_val_market WHERE scheme LIKE 'wics%' "
                           "ORDER BY 1,2,3,4").fetchall()
    assert S.main() == 0                                                          # 같은 입력 → 같은 값 → 다시 쓰지 않는다
    with psycopg.connect(url) as c:
        assert c.execute("SELECT snap_dd, market, scheme, sector, xmin::text FROM opm_val_market WHERE scheme LIKE 'wics%' "
                         "ORDER BY 1,2,3,4").fetchall() == before
