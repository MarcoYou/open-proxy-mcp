"""전 시장 계산 벤치마크(「코스피 전체」「코스닥 전체」「전체」)에서 우선주를 뺀다 — Postgres 17 의 실제 표·뷰에서 증명한다 (261023).

  Q1  일별 집계 SQL(`BENCH_DAILY_SQL`)이 우선주 행을 합에 넣지 않는다: 보통주만의 Σcmp·주식수 / Σ(close−cmp)·주식수 와 같다.
      우선주 판정은 하나다 — 분류 구간이 있는 행은 출처 'P'(보통주 업종을 이은 행)만으로, 분류 구간이 없는 행은 종목코드 끝자리 규칙(끝자리 ≠ 0, `issuer_ticker` 와 같다)으로.
      회사 원장도 「앞 5자 + 0 이 번호표에 있다」도 보지 않는다(보통주 코드가 없는 종류주 코드도 뺀다).
  Q2  우선주를 넣은 픽스처의 벤치마크 수익률이 보통주만 넣은 픽스처의 값과 같다(수익률 = Σnum / Σden, 일별 계산 그대로).
  Q3  주간 경로 — `read_sector_map` 이 같은 판정(`SectorMap.is_pref`)을 준다(추가 질의 없이 코드 끝자리 규칙). 구간이 있고 출처가 'P' 인 종목, 구간이 없는 종류주 코드는 우선주,
      보통주·분류 없는 보통주·분류가 있는 종류주 코드(출처 'W')는 아니다.
  Q4  분류 없는 보통주(끝자리 0)는 미분류로 남아 합에 든다(합 보존) — 우선주 판정이 미분류 보통주를 삼키지 않는다.
  Q5  규칙 동등성 — 같은 픽스처(분류 있는 보통주·출처 'P'/'pref' 우선주·분류 없는 우선주·분류 없는 보통주·분류 있는 종류주 코드 'W')에서 업종 구성 `compute`·일별 SQL·주간 `SectorMap`
      세 경로가 빼는 우선주 집합이 같다.
이 시험은 고치기 전 main(006536ca)에서 실패한다(우선주가 합에 들고 `SectorMap.is_pref` 가 없다). Docker 가 없으면 건너뛴다.
"""
from __future__ import annotations

import importlib.util
import pathlib
import uuid
from datetime import date

import pytest

psycopg = pytest.importorskip("psycopg")

from test_krx_daily_pg import _ddl, put_daily  # noqa: E402

from open_proxy_mcp import db as dbm  # noqa: E402
from open_proxy_mcp.services import perf_data as D  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


#: A·B 보통주(코스피, 분류 있음) · PA = A 의 우선주(분류 출처 'P') · PB = B 의 종류주(분류 구간 없음 — 코드 끝자리 규칙) · PD = 보통주 코드(900040)가 번호표에 없는 종류주 코드(분류 구간 없음 — 끝자리 규칙만으로 뺀다)
#: PC = 분류 구간이 있고 출처가 'W' 인 종류주 코드(분류가 있는 행은 출처만 본다 — 운영에는 0행) · C 코스닥 보통주 · U 분류 없는 보통주.
A, B, PA, PB, PC, PD, C, U = "900010", "900020", "900015", "900025", "900035", "900045", "800010", "800020"
DAYS = ["20261005", "20261006", "20261007"]
CLOSE = {A: [10000, 10100, 10300], B: [20000, 20400, 20200], PA: [5000, 5300, 5600], PB: [4000, 3800, 4400], PC: [6000, 6100, 6300], PD: [2000, 2100, 2300],
         C: [3000, 3030, 3090], U: [1000, 1020, 1010]}
SHRS = {A: 1000, B: 500, PA: 200, PB: 300, PC: 250, PD: 700, C: 400, U: 100}
MKT = {A: "KS", B: "KS", PA: "KS", PB: "KS", PC: "KS", PD: "KS", C: "KQ", U: "KQ"}
PREV0 = {A: 9900, B: 19800, PA: 4900, PB: 4100, PC: 5900, PD: 1900, C: 2970, U: 990}
ALL = (A, B, PA, PB, PC, PD, C, U)
EXCLUDED = {PA, PB, PD}                       # 세 경로가 빼야 하는 우선주 집합


def _rows(drop=()):
    out = []
    for i, dd in enumerate(DAYS):
        for t in ALL:
            if t in drop:
                continue
            prev = CLOSE[t][i - 1] if i else PREV0[t]
            out.append((dd, t, MKT[t], CLOSE[t][i], SHRS[t], None, None, None, 100, 1000, CLOSE[t][i] - prev))
    return out


def _build(admin, url_for, drop=()):
    dbname = "pref_" + uuid.uuid4().hex[:8]
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{dbname}"')
        for role, attrs in (("anon", "NOLOGIN"), ("authenticated", "NOLOGIN"), ("service_role", "NOLOGIN BYPASSRLS"), ("opm_ro", "NOLOGIN")):
            if not c.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                c.execute(f"CREATE ROLE {role} {attrs}")
    url = url_for(dbname)
    _ddl().apply(url)
    _script("krx_weekly_ddl").apply(url, "full")
    _script("wise_sector_iv_ddl").apply(url, "full")
    with psycopg.connect(url, autocommit=True) as c:
        put_daily(c, _rows(drop))
        tid = dict(c.execute("SELECT ticker, tid FROM krx_ticker").fetchall())
        c.execute("INSERT INTO wise_sector_snap (snap, dd, n) VALUES (%s, %s, 0)", (date(2026, 10, 1), "20261001"))
        it, semi, soft = ("G45", "IT"), ("G4530", "반도체와반도체장비"), ("G4510", "소프트웨어와서비스")

        def iv(ticker, k, src, src_ref=None):
            if ticker not in tid:                 # 보통주만 넣은 판(drop)에는 그 종목이 없다
                return
            c.execute("INSERT INTO wise_sector_iv (tid, valid_from, valid_to, src, sector_code, sector, industry_code, industry, src_ref) "
                      "VALUES (%s, %s, NULL, %s, %s, %s, %s, %s, %s)", (tid[ticker], date(2026, 10, 1), src, *it, *k, src_ref))
        iv(A, semi, "W")
        iv(B, semi, "W")
        iv(PA, semi, "P", A)                  # 우선주 — 보통주 분류를 이은 행
        iv(PC, semi, "W")                     # 분류가 있는 종류주 코드(출처 'W') — 분류가 있는 행은 출처만 본다
        iv(C, soft, "W")
        # PB·PD(종류주 코드, 분류 구간 없음)·U(분류 없는 보통주)는 구간이 없다
        c.execute("UPDATE wise_sector_snap s SET n = (SELECT count(*) FROM wise_sector w WHERE w.snap_dd = s.dd)")
    return url


@pytest.fixture(scope="module")
def world(_pg17_server):
    admin, url_for = _pg17_server
    return {"full": _build(admin, url_for), "common_only": _build(admin, url_for, drop=tuple(EXCLUDED))}


@pytest.fixture
def use_db(monkeypatch):
    def _use(url):
        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.setattr(dbm, "_pool", None)
        monkeypatch.setattr(dbm, "_pool_disabled_until", float("inf"))
    return _use


def _by_day(got):
    out: dict[str, list[float]] = {}
    for g in got:
        v = out.setdefault(g.dd, [0.0, 0.0])
        v[0] += g.num
        v[1] += g.den
    return out


def _want_by_day(tickers):
    out: dict[str, list[float]] = {}
    for i, dd in enumerate(DAYS[1:], start=1):
        for t in tickers:
            cmp = CLOSE[t][i] - CLOSE[t][i - 1]
            v = out.setdefault(dd, [0.0, 0.0])
            v[0] += cmp * SHRS[t]
            v[1] += (CLOSE[t][i] - cmp) * SHRS[t]
    return out


# ── Q1·Q2 일별 집계 ──────────────────────────────────────────────────────────────

def test_daily_benchmark_sql_leaves_out_preferred_by_source_and_by_code_rule(world, use_db):
    use_db(world["full"])
    got = D.read_bench_daily(["KS", "KQ"], "20261005", "20261007")
    want = _want_by_day([A, B, PC, C, U])                                       # PA(출처 'P')·PB·PD(구간 없는 종류주 코드)는 합에 없고 PC(출처 'W')는 든다
    have = _by_day(got)
    assert set(have) == set(want)
    for dd, (num, den) in want.items():
        assert have[dd][0] == pytest.approx(num) and have[dd][1] == pytest.approx(den), dd
    assert sum(g.n for g in got if g.dd == "20261006") == 5                      # A·B·PC·C·U


def test_benchmark_return_with_preferred_rows_equals_the_return_of_common_stock_only(world, use_db):
    use_db(world["full"])
    full = D.read_bench_daily(["KS", "KQ"], "20261005", "20261007")
    use_db(world["common_only"])
    only = D.read_bench_daily(["KS", "KQ"], "20261005", "20261007")
    f, o = _by_day(full), _by_day(only)
    assert set(f) == set(o)
    for dd in f:
        assert f[dd][0] / f[dd][1] == pytest.approx(o[dd][0] / o[dd][1], rel=1e-12), dd         # 일별 수익률 Σnum/Σden
    ks = D.read_bench_daily(["KS"], "20261005", "20261007")
    assert _by_day(ks)["20261006"] == pytest.approx(_want_by_day([A, B, PC])["20261006"])         # 코스피 전체 = A·B·PC(출처 'W' 종류주 코드는 분류가 있어 든다)


def test_unclassified_common_stock_stays_in_the_sum(world, use_db):
    use_db(world["full"])
    got = D.read_bench_daily(["KQ"], "20261005", "20261007")
    assert {(g.dd, g.key.mid if g.key else None): g.n for g in got}[("20261006", None)] == 1      # U: 분류 구간 없는 보통주(끝자리 0) — 미분류로 남는다
    assert _by_day(got)["20261006"] == pytest.approx(_want_by_day([C, U])["20261006"])


# ── Q3 주간 경로의 판정 ──────────────────────────────────────────────────────────

def test_sector_map_judges_preferred_like_the_daily_sql(world, use_db):
    use_db(world["full"])
    sm = D.read_sector_map(list(ALL))
    assert [sm.is_pref(t, "20261006") for t in ALL] == [False, False, True, True, False, True, False, False]
    assert sm.is_pref(PA, "20260930") is True                                    # 첫 스냅샷 앞 날짜(소급)도 같다
    assert sm.at(PA, "20261006").mid == "반도체와반도체장비" and sm.at(U, "20261006") is None   # 업종 읽기는 그대로


# ── Q5 규칙 동등성: 업종 구성 compute · 일별 SQL · 주간 SectorMap 이 빼는 집합이 같다 ──────────────

def _compute_excluded_codes():
    """`sector_universe.compute` 가 같은 픽스처에서 뺀 종목 집합 — 시총을 2 의 거듭제곱으로 줘 시장 몫 계산의 `market_preferred_cap_krw` 에서 집합을 읽어 낸다."""
    from open_proxy_mcp.services import sector_universe as S
    from test_sector_universe import DAY, IT, R, SEMI
    cap = {t: 1 << i for i, t in enumerate(ALL)}
    rows = [
        R(A, "KS", cap[A], IT, SEMI), R(B, "KS", cap[B], IT, SEMI), R(PA, "KS", cap[PA], IT, SEMI, src="pref", src_ref=A),
        R(PB, "KS", cap[PB], None, None), R(PC, "KS", cap[PC], IT, SEMI), R(PD, "KS", cap[PD], None, None),
        R(C, "KQ", cap[C], IT, SEMI), R(U, "KQ", cap[U], None, None)]
    res = S.compute(S.parse("코스피 반도체 업종"), rows, DAY, {})              # 회사 원장(names)이 비어 있어도 같은 결과여야 한다
    left = res.sector["market_preferred_cap_krw"]
    return {t for t in ALL if left & cap[t]}, res


def test_all_three_paths_leave_out_the_same_preferred_set(world, use_db):
    from_compute, res = _compute_excluded_codes()
    assert from_compute == EXCLUDED
    assert {x["ticker"] for x in res.rows} == {A, B, PC}                          # 구성에는 보통주·출처 'W' 행만

    use_db(world["full"])
    ks = [g for g in D.read_bench_daily(["KS"], "20261005", "20261007") if g.dd == "20261006"]
    num, den = (sum(getattr(g, k) for g in ks) for k in ("num", "den"))
    ks_codes = [t for t in ALL if MKT[t] == "KS"]
    one = _want_by_day  # 종목별 (Σcmp·주식수, Σ(close−cmp)·주식수) — 합이 관측값과 같은 부분집합이 딱 하나여야 그 집합이 SQL 이 합에 넣은 종목이다
    hits = []
    for mask in range(1 << len(ks_codes)):
        sub = [t for i, t in enumerate(ks_codes) if mask >> i & 1]
        if sub and one(sub)["20261006"] == pytest.approx([num, den]):
            hits.append(set(sub))
    assert len(hits) == 1
    from_sql = set(ks_codes) - hits[0]

    sm = D.read_sector_map([A, B, PA, PB, PC, PD])
    from_weekly = {t for t in (A, B, PA, PB, PC, PD) if sm.is_pref(t, "20261006")}
    assert from_compute == from_sql == from_weekly == EXCLUDED
