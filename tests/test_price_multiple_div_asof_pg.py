"""시장·업종 표 과거 기준일 확정 배당수익률(261020 p1j)의 사업연도 고르기 질의를 Postgres 17 에서 검증한다.

가짜 DB 시험(`test_price_multiple_div_asof.py`)은 질의 모양을 흉내 낸다 — 여기서는 같은 코드를 실제 엔진에 돌려
「그 날까지 확정·공시된 사업연도(12월 결산 FY X → X+1년 4월 1일부터) 이하 가장 늦은 집계」가 맞게 나오는지,
최신(기준일 없음)은 종전처럼 가장 늦은 사업연도인지, 그 이하 집계가 없으면 빈 결과 + 까닭인지 본다.
로컬 Docker 의 `postgres:17` 이 필요하다(`tests/conftest.py` 의 `pg17`). 없으면 건너뛴다. network 0.
"""
from __future__ import annotations

import asyncio

import pytest

psycopg = pytest.importorskip("psycopg")

from open_proxy_mcp.services import price_multiple_data as S  # noqa: E402

#: 운영 표와 같은 칸(10-20 정보 스키마) — 읽는 쪽이 쓰는 칸만이 아니라 전부.
DDL = """
CREATE TABLE div_yield_hist (
  market text, scheme text, bucket text, label text, fiscal_year integer, denom_basis text, price_dd text,
  div_total_krw double precision, cap_krw double precision, div_yield_pct double precision,
  n_total bigint, n_div bigint, cov_n_pct double precision, cov_cap_pct double precision,
  n_shares_from_krx bigint, n_payers bigint, n_nodiv bigint, n_dps_missing bigint,
  n_universe bigint, cap_universe_krw double precision, src_as_of date, built_at timestamp,
  CONSTRAINT div_yield_hist_key PRIMARY KEY (market, scheme, bucket, fiscal_year, denom_basis))
"""
YEAR_END = {2021: "20211230", 2022: "20221229", 2023: "20231228", 2024: "20241230", 2025: "20251230"}


@pytest.fixture
def db(pg17, monkeypatch):
    rows = []
    for fy, dd in YEAR_END.items():
        for m in ("KS", "KQ", "ALL"):
            for basis, k in (("all", 1.0), ("covered", 1.1), ("payers", 1.3)):
                rows.append((m, "market", "ALL", m, fy, basis, dd, k * (fy - 2000) / 10 + (m == "KQ")))
        for basis, k in (("all", 1.0), ("payers", 1.3)):
            rows.append(("KS", "wics_sector", "에너지", "에너지", fy, basis, dd, k * (fy - 2000)))
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(DDL)
        with c.cursor() as cur:
            cur.executemany("INSERT INTO div_yield_hist (market, scheme, bucket, label, fiscal_year, denom_basis, price_dd, div_yield_pct, "
                            "n_total, n_div, cov_cap_pct) VALUES (%s,%s,%s,%s,%s,%s,%s,%s, 800, 700, 88.0)", rows)
    sqls: list[tuple[str, tuple]] = []

    def pg_rows(sql, params=()):
        sqls.append((" ".join(sql.split()), tuple(params)))
        try:
            with psycopg.connect(pg17, autocommit=True) as c:
                return c.execute(sql, params).fetchall()
        except Exception:
            return None
    monkeypatch.setattr("open_proxy_mcp.db.pg_rows", pg_rows)
    yield sqls


def _fy(scheme, as_of):
    act, ruler = asyncio.run(S._div_yield_map(scheme, as_of))
    return {k: v["fiscal_year"] for k, v in act.items()}, act, ruler


@pytest.mark.parametrize("as_of, fy", [
    (None, 2025),                    # 최신 — 종전 그대로 가장 늦은 사업연도
    ("20260401", 2025), ("20260331", 2024), ("20250829", 2024), ("20250331", 2023),
    ("20230331", 2021), ("20220401", 2021),
])
def test_engine_picks_the_fiscal_year_disclosed_by_the_basis_day(db, as_of, fy):
    got, act, ruler = _fy("market", as_of)
    assert set(got) == {("KS", "ALL"), ("KQ", "ALL"), ("ALL", "ALL")} and set(got.values()) == {fy}
    assert set(act[("KS", "ALL")]) >= {"all", "payers"} and "covered" not in act[("KS", "ALL")]   # 두 벌만
    assert ruler["actual_fiscal_year"] == fy and ruler["actual_price_dd"] == YEAR_END[fy]
    if as_of:
        assert YEAR_END[fy] < as_of and ruler["actual_basis_dd"] == as_of and ruler["actual_pit_fy"] == fy
        assert "actual_note_older" not in ruler and "actual_note_empty" not in ruler   # 그 해 집계가 있으면 안내 없음(261020 검토)
    else:
        assert ruler == {"actual_fiscal_year": 2025, "actual_price_dd": "20251230"}


def test_engine_before_the_first_year_returns_nothing_and_the_reason(db):
    got, act, ruler = _fy("market", "20220331")       # 그 날 확정 사업연도는 FY2020 — 집계는 FY2021 부터
    assert act == {} and ruler["actual_pit_fy"] == 2020 and "FY2021 부터" in ruler["actual_note_empty"]
    got, act, ruler = _fy("wics_sector", "20201030")
    assert act == {} and "FY2019" in ruler["actual_note_empty"]
    assert db[-1] == ("SELECT MIN(fiscal_year) FROM div_yield_hist WHERE scheme=%s", ("wics_sector",))


def test_engine_major_sector_axis(db):
    got, act, _ = _fy("wics_sector", "20250829")
    assert got == {("KS", "에너지"): 2024} and act[("KS", "에너지")]["all"] == 24.0
    got, _, _ = _fy("wics_sector", None)
    assert got == {("KS", "에너지"): 2025}
