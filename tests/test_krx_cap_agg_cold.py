"""krx_cap_agg — 하한 이전 시장·업종 행을 콜드 서빙본으로 다시 집계(`--cold-sqlite`, 261019 · 261021 출력 = Parquet).

DB krx_weekly 는 하한 이전을 종목별 연말 행만 남기고, 집계 표도 3년 밖 행은 콜드 보관본으로 옮긴다(서버는 서빙본에서 읽는다).
콜드 서빙본은 하한 이전 전 행이라 그 구간을 지금 규칙으로 다시 낼 수 있다.
  · 규칙은 Q_SECTOR 와 같다 — mktcap > 0, 그 날짜 이하 최신 스냅샷(없으면 가장 이른 것), 분류 없음 = _UNCLASSIFIED/「미분류」.
  · 다시 낸 시장 행이 대조 기준(DB 에 남은 하한 이전 행, 없으면 --baseline-parquet)과 하나라도 다르면 거절 — 아무것도 쓰지 않는다.
  · 결과는 DB 에 쓰지 않는다 — `--out` Parquet(칸 = 표). 없으면 대조만.
Postgres 시험은 Docker 가 없으면 건너뛴다(`tests/conftest.py` 의 `pg17`).
"""
from __future__ import annotations

import sys

import pytest

from conftest import build_cold_serving
from scripts import krx_cap_agg as C


def test_cold_sector_rows_follow_the_sql_rule():
    snaps = ["20260821", "20261016"]
    cls = {"20260821": {"A": ("G10", "에너지", "G1010", "에너지"), "B": (None, None, None, None)},
           "20261016": {"A": ("G15", "소재", "G1510", "소재")}}
    weekly = [("20221229", "A", "KS", 100), ("20221229", "B", "KS", 50), ("20221229", "C", "KQ", 7),
              ("20221229", "Z", "KQ", 0),                 # 시총 0 은 뺀다(Q_MARKET·Q_SECTOR 의 mktcap > 0)
              ("20261016", "A", "KS", 120)]               # 스냅샷 날짜 이후 = 그 스냅샷
    mkt, sec = C.cold_sector_rows(weekly, snaps, cls)
    assert mkt == {("20221229", "KS"): [2, 150], ("20221229", "KQ"): [1, 7], ("20261016", "KS"): [1, 120]}
    assert sec[("20221229", "KS", "wics_sector", "G10")] == [1, 100, "에너지", "20260821"]        # 가장 이른 스냅샷을 소급
    assert sec[("20221229", "KS", "wics_sector", "_UNCLASSIFIED")] == [1, 50, "미분류", "20260821"]  # 칸이 비면 미분류
    assert sec[("20221229", "KQ", "wics_industry", "_UNCLASSIFIED")] == [1, 7, "미분류", "20260821"]  # 스냅샷에 없으면 미분류
    assert sec[("20261016", "KS", "wics_sector", "G15")] == [1, 120, "소재", "20261016"]
    for scheme in ("wics_sector", "wics_industry"):                                                 # 업종 합 = 시장 합
        for (dd, m), (n, cap) in mkt.items():
            parts = [v for k, v in sec.items() if k[:3] == (dd, m, scheme)]
            assert (sum(v[0] for v in parts), sum(v[1] for v in parts)) == (n, cap)


# ── Postgres 17: 대조·적용·거절 ─────────────────────────────────────────────
psycopg = pytest.importorskip("psycopg")

COLD = [("A", "20221229", "KS", 10, 10), ("B", "20221229", "KS", 5, 10), ("C", "20221229", "KQ", 3, 10),
        ("A", "20230106", "KS", 11, 10), ("B", "20230106", "KS", 6, 10), ("C", "20230106", "KQ", 4, 10)]


@pytest.fixture
def cdb(pg17, monkeypatch, tmp_path):
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute("CREATE TABLE krx_weekly (price_dd text, ticker text, market text, mktcap bigint)")
        c.execute("INSERT INTO krx_weekly VALUES ('20221229','A','KS',100), ('20231020','A','KS',130), ('20261016','A','KS',150)")
        c.execute("CREATE TABLE wise_sector (snap_dd text, ticker text, sector_code text, sector text, industry_code text, industry text)")
        # 보강 뒤 분류 — B 는 옛 집계에서 미분류였다(우선주·구성종목 밖 보통주 같은 경우)
        c.execute("INSERT INTO wise_sector VALUES ('20260821','A','G10','에너지','G1010','에너지'), "
                  "('20260821','B','G20','산업재','G2010','자본재'), ('20260821','C','G45','IT','G4530','반도체')")
        c.execute(C.DDL)
        old = []
        for dd, a, b, cc in (("20221229", 100, 50, 30), ("20230106", 110, 60, 40)):
            old += [(dd, "KS", "market", "_ALL", "전체", 2, a + b, None), (dd, "KQ", "market", "_ALL", "전체", 1, cc, None)]
            for scheme, code_a, code_c in (("wics_sector", "G10", "G45"), ("wics_industry", "G1010", "G4530")):
                old += [(dd, "KS", scheme, code_a, "x", 1, a, "20260821"), (dd, "KS", scheme, "_UNCLASSIFIED", "미분류", 1, b, "20260821"),
                        (dd, "KQ", scheme, code_c, "x", 1, cc, "20260821")]
        old += [("20231020", "KS", "market", "_ALL", "전체", 1, 130, None), ("20231020", "KS", "wics_sector", "G10", "에너지", 1, 130, "20260821")]
        with c.cursor() as cur:
            cur.executemany(C.UPSERT, old)
    built = build_cold_serving(tmp_path, COLD, upto="20231016")
    monkeypatch.setenv("DATABASE_URL", pg17)
    return pg17, str(built["sqlite_path"])


def _rows(url, where=""):
    with psycopg.connect(url) as c:
        return {tuple(r[:4]): tuple(r[4:]) for r in c.execute(
            "SELECT price_dd, market, scheme, bucket, n, cap, label, sector_asof FROM krx_cap_agg " + where + " ORDER BY 1,2,3,4")}


def _run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["krx_cap_agg.py", *argv])
    return C.main()


def _parquet(path):
    from scripts import hist_parquet
    return {tuple(r[:4]): tuple(r[4:]) for r in hist_parquet.read(
        path, ["price_dd", "market", "scheme", "bucket", "n", "cap", "label", "sector_asof"])}


def test_cold_rebuild_writes_parquet_and_never_the_db(cdb, monkeypatch, capsys, tmp_path):
    url, sqlite_path = cdb
    before = _rows(url)
    assert _run(monkeypatch, "--cold-sqlite", sqlite_path) == 0                     # 기본은 대조만
    assert "대조만" in capsys.readouterr().out
    out = tmp_path / "out" / "krx_cap_agg_hist.parquet"
    assert _run(monkeypatch, "--cold-sqlite", sqlite_path, "--out", str(out)) == 0
    assert _rows(url) == before                                                       # DB 는 그대로(261021 — DB 쓰기 없음)
    got = _parquet(out)
    assert {k[0] for k in got} == {"20221229", "20230106"}                            # 하한 이전만
    assert got[("20221229", "KS", "market", "_ALL")] == (2, 150, "전체", None)         # 시장 행도 담는다(대조 기준과 같은 값)
    assert got[("20221229", "KS", "wics_sector", "G20")] == (1, 50, "산업재", "20260821")  # 미분류였던 B 가 제 업종으로
    assert ("20221229", "KS", "wics_sector", "_UNCLASSIFIED") not in got
    assert got[("20230106", "KQ", "wics_industry", "G4530")] == (1, 40, "반도체", "20260821")
    for dd in ("20221229", "20230106"):                                                # 업종 합 = 시장 합
        for m in ("KS", "KQ"):
            mk = got[(dd, m, "market", "_ALL")]
            for scheme in ("wics_sector", "wics_industry"):
                parts = [v for k, v in got.items() if k[:3] == (dd, m, scheme)]
                assert (sum(v[0] for v in parts), sum(v[1] for v in parts)) == mk[:2]
    assert "DB 쓰기 없음" in capsys.readouterr().out


def test_cold_rebuild_refuses_when_market_rows_do_not_match(cdb, monkeypatch, tmp_path, capsys):
    url, _ = cdb
    other = build_cold_serving(tmp_path / "other", COLD[:-1], upto="20231016")         # 한 종목이 빠진 원천 — 시장 행이 다르다
    out = tmp_path / "x.parquet"
    assert _run(monkeypatch, "--cold-sqlite", str(other["sqlite_path"]), "--out", str(out)) == 2
    assert not out.exists()
    assert "거절" in capsys.readouterr().err


def test_cold_rebuild_after_the_move_uses_the_baseline_parquet(cdb, monkeypatch, tmp_path, capsys):
    """옮긴 뒤(DB 에 하한 이전 행이 없다) — 대조 기준은 지금 보관본 Parquet. 기준이 없으면 거절한다."""
    url, sqlite_path = cdb
    base = tmp_path / "base.parquet"
    assert _run(monkeypatch, "--cold-sqlite", sqlite_path, "--out", str(base)) == 0
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("DELETE FROM krx_cap_agg WHERE price_dd < '20231016'")
    capsys.readouterr()
    assert _run(monkeypatch, "--cold-sqlite", sqlite_path, "--out", str(tmp_path / "y.parquet")) == 2
    assert "대조 기준이 없다" in capsys.readouterr().err
    assert _run(monkeypatch, "--cold-sqlite", sqlite_path, "--baseline-parquet", str(base),
                "--out", str(tmp_path / "z.parquet")) == 0
    assert _parquet(tmp_path / "z.parquet") == _parquet(base)


def test_out_requires_cold_sqlite(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["krx_cap_agg.py", "--out", "x.parquet"])
    with pytest.raises(SystemExit):
        C.main()
