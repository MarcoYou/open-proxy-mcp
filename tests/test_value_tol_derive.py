# -*- coding: utf-8 -*-
"""저장값 비교 허용 오차 한 곳(`value_tol`) — 재파생이 읽는 자릿수 차이로 17종목을 흔들지 않는다(261021 DATA-Q7).

운영 DB 새 연결의 기본 `extra_float_digits` 는 0(실수 15자리 읽기)이고 운영 배치가 그렇게 읽고 쓴다. 같은 칸을 정밀 읽기(3)로 다시 계산하면 외화 환산 값처럼
17자리를 쓰는 입력(900xxx·950xxx·008700 17종목)에서 합이 상대 2e-15 이하로 달라 — 정확 비교(`IS DISTINCT FROM`)는 그 종목을 「바뀐 것」으로 쓰고 다음 배치가 되돌렸다(매일 왕복).

DART 0콜. DB 는 Postgres 17(도커 없으면 건너뜀).
"""
from __future__ import annotations

import pytest

from open_proxy_mcp.services import value_tol as vt
from scripts import market_fund_quarterly as Q

psycopg = pytest.importorskip("psycopg")


# ═══ 순수 규칙 ═══════════════════════════════════════════════════════════════════════════════════
def test_tolerance_is_in_one_place_and_has_the_documented_size():
    assert (vt.REL_TOL, vt.ABS_TOL) == (1e-9, 0.01)
    assert vt.same(None, None) and not vt.same(None, 0.0) and not vt.same(1.0, None)           # 빈 칸은 빈 칸끼리만 같다
    big = 3_208_127_419_325.1234
    assert vt.same(big, big * (1 + 2e-15))                                                    # 끝자리 잡음(상대 2e-15)은 같다
    assert vt.same(big, big * (1 + 9e-10)) and not vt.same(big, big * (1 + 1.1e-9))           # 경계는 상대 1e-9
    assert vt.same(0.0, 0.009) and not vt.same(0.0, 0.011)                                    # 0 근처는 절대 0.01 — 합이 상쇄될 때의 잡음
    assert vt.same(-5.0e9, -5.0e9 * (1 + 5e-10)) and not vt.same(-5.0e9, 5.0e9)                # 부호가 다르면 다르다
    assert vt.differs(1.0e8, 1.0e8 + 1.0) and not vt.differs(1.0e8, 1.0e8)


@pytest.mark.parametrize("a, b", [
    (None, None), (None, 1.0), (1.0, None), (0.0, 0.0), (0.0, 0.005), (0.0, 0.02), (1.0e12, 1.0e12 * (1 + 1e-12)), (1.0e12, 1.0e12 * (1 + 1e-8)),
    (-3.5e11, -3.5e11 + 0.4), (-3.5e11, -3.5e11 + 4000.0), (123.0, 124.0), (1.0e-3, -1.0e-3),
])
def test_sql_predicate_agrees_with_the_python_rule(pg17, a, b):
    with psycopg.connect(pg17, autocommit=True) as c:
        got = c.execute("SELECT " + vt.differs_sql("%(a)s::float8", "%(b)s::float8"), {"a": a, "b": b}).fetchone()[0]
    assert got is vt.differs(a, b)


# ═══ 재파생 ═══════════════════════════════════════════════════════════════════════════════════════
FINSTAT_Q_DDL = """CREATE TABLE dart_finstat_q(
  ticker text, fy int, quarter int, reprt_code text, fs text,
  ni_cum double precision, eq double precision, fetched text, ni_case text, eq_case text,
  ni_cum_raw double precision, eq_raw double precision,
  ni_cum_restated double precision, eq_restated double precision, restate_why text,
  PRIMARY KEY(ticker, fy, quarter))"""

# 외화(엔) 결산 회사를 환산한 17자리 값 — 연간 + 당해 누적 − 전년 누적
FX = 9.1534827356812473
CELLS = {(2024, 4): (-31_415_926_535.0 * FX, 271_828_182_845.0 * FX), (2025, 1): (4_142_135_623.0 * FX, 281_828_182_845.0 * FX),
         (2025, 3): (17_320_508_075.0 * FX, 287_328_182_845.0 * FX), (2024, 3): (1_618_033_988.0 * FX, 268_828_182_845.0 * FX)}


def _rig(url, monkeypatch, *, ticker="900140"):
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(FINSTAT_Q_DDL)
        c.execute(Q.DDL_FUNDAMENTALS)
        c.execute("INSERT INTO dart_fundamentals (ticker, corp_code, fetched, acc_mt) VALUES (%s, 'c1', 'ok', 12), ('005930', 'c2', 'ok', 12)", (ticker,))
        for (fy, q), (ni, eq) in CELLS.items():
            for t in (ticker, "005930"):
                c.execute("INSERT INTO dart_finstat_q (ticker, fy, quarter, reprt_code, fs, ni_cum, eq, fetched) VALUES (%s,%s,%s,%s,'CFS',%s,%s,'ok')",
                          (t, fy, q, Q.Q_TO_RC[q], ni if t == ticker else round(ni), eq if t == ticker else round(eq)))
    monkeypatch.setenv("DATABASE_URL", url)


def _derive(monkeypatch, digits, capsys):
    monkeypatch.setenv("PGOPTIONS", f"-c extra_float_digits={digits}")
    Q.derive_fundamentals()
    return capsys.readouterr().out


def _stored(url, t="900140"):
    with psycopg.connect(url) as c:
        c.execute("SET extra_float_digits = 3")                                               # 저장값은 항상 정밀(17자리)로 읽어 견준다
        return c.execute("SELECT ni_fy, ni_ttm, eq_fy, eq_mrq FROM dart_fundamentals WHERE ticker=%s", (t,)).fetchone()


def test_the_digits_read_do_not_flip_a_rederived_row(pg17, monkeypatch, capsys):
    _rig(pg17, monkeypatch)
    # 운영 배치처럼 15자리로 읽고 계산해 쓴다
    out0 = _derive(monkeypatch, 0, capsys)
    assert "값이 바뀐 2사 갱신" in out0
    at15 = _stored(pg17)
    # 같은 입력을 정밀(17자리)로 읽어 다시 계산하면 끝자리만 다른 값이 나온다 — 이 시험의 전제(고정 자료)
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute("SET extra_float_digits = 3")
        exact = {k: c.execute("SELECT ni_cum, eq FROM dart_finstat_q WHERE ticker='900140' AND fy=%s AND quarter=%s", k).fetchone() for k in CELLS}
        c.execute("SET extra_float_digits = 0")
        trunc = {k: c.execute("SELECT ni_cum, eq FROM dart_finstat_q WHERE ticker='900140' AND fy=%s AND quarter=%s", k).fetchone() for k in CELLS}
    ttm_exact = exact[(2024, 4)][0] + exact[(2025, 3)][0] - exact[(2024, 3)][0]
    ttm_trunc = trunc[(2024, 4)][0] + trunc[(2025, 3)][0] - trunc[(2024, 3)][0]
    assert ttm_exact != ttm_trunc and abs(ttm_exact - ttm_trunc) <= 1e-13 * abs(ttm_exact), "전제: 읽는 자릿수만으로 끝자리가 달라진다"
    # 정밀 읽기로 재파생: 허용 오차 안이라 아무것도 쓰지 않는다 — 저장값은 글자까지 그대로
    out3 = _derive(monkeypatch, 3, capsys)
    assert "값이 바뀐 0사 갱신" in out3, out3
    assert _stored(pg17) == at15
    # 다시 15자리 — 역시 0사
    assert "값이 바뀐 0사 갱신" in _derive(monkeypatch, 0, capsys)
    assert _stored(pg17) == at15


def test_a_real_change_is_still_written_with_all_four_cells(pg17, monkeypatch, capsys):
    _rig(pg17, monkeypatch)
    _derive(monkeypatch, 0, capsys)
    before = _stored(pg17)
    with psycopg.connect(pg17, autocommit=True) as c:                                           # 새 분기보고서가 나왔다(누적 순이익 +1억, 자본 +2억)
        c.execute("UPDATE dart_finstat_q SET ni_cum = ni_cum + 1e8, eq = eq + 2e8 WHERE ticker = '900140' AND fy = 2025 AND quarter = 3")
    out = _derive(monkeypatch, 3, capsys)
    assert "값이 바뀐 1사 갱신" in out
    after = _stored(pg17)
    assert after[1] == pytest.approx(before[1] + 1e8, rel=1e-12) and after[3] == pytest.approx(before[3] + 2e8, rel=1e-12)
    # 값이 빈 칸으로 바뀌면(정정으로 비움) 바뀐 것이다
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute("UPDATE dart_finstat_q SET ni_cum = NULL WHERE ticker = '900140' AND fy = 2025 AND quarter = 3")
    assert "값이 바뀐 1사 갱신" in _derive(monkeypatch, 3, capsys)
    assert _stored(pg17)[1] is None or _stored(pg17)[1] != after[1]


def test_whole_won_companies_are_unchanged_by_the_tolerance(pg17, monkeypatch, capsys):
    """원 단위 정수 값(국내 상장사 대다수)은 15자리로 정확히 읽히므로 어느 자릿수로 읽어도 같다 — 허용 오차가 바꾸는 것이 없다."""
    _rig(pg17, monkeypatch)
    _derive(monkeypatch, 0, capsys)
    a = _stored(pg17, "005930")
    assert _derive(monkeypatch, 3, capsys).count("값이 바뀐 0사") == 1 and _stored(pg17, "005930") == a
