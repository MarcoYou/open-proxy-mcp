# -*- coding: utf-8 -*-
"""반기·분기 결산 리츠 — 결산기 총액이 **서로 같은** 해도 총액을 더한다 (261021, 공개 DATA 문서 DATA-DIV1).

배경: 원장이 결산기 줄마다 가장 늦은 결산기의 총액을 복사해 두던 시절(수집기 고침 전) 서비스는 「결산기 총액이 모두 같으면 복사 꼴」로 보고 합하지 않았다.
수집기를 고쳐 원장 총액이 결산기별 실제 값이 된 뒤에도 그 규칙이 남으면, 실제로 두 결산기 총액이 같은 해(주식 수가 그대로인 리츠가 두 결산기에 같은 DPS 를 준 해)가
반으로 센다 — 제이알글로벌리츠 FY2021 190+190원/각 314.64억, FY2024 195+195원/각 384.88억, FY2025 115+115원/각 226.98억(원문 DPS·총액 줄이 결산기마다 같다).
이제 복사 꼴 판정은 **총액이 모두 같고 DPS 가 서로 다를 때**만이다(롯데리츠 117 vs 123 이 같은 총액으로 복사돼 있던 꼴).

SQL(`_YEAR_SQL`: screen·market·sector)과 파이썬(`year_totals`: firm)이 같은 판정을 하는지도 같은 원장 줄로 맞춘다. 실제 Postgres 17 (Docker 가 없으면 SQL 쪽은 건너뜀).

  · E1 총액 같음 + DPS 같음(제이알글로벌리츠 FY2025 꼴)  → 더한다(`sum`)
  · E2 총액 같음 + DPS 다름(옛 복사 꼴, 롯데리츠 꼴)      → 합하지 않는다(`ledger_copy`) — 방어선은 남는다
  · E3 총액 다름(고친 원장)                                → 더한다(`sum`)
  · E4 한 결산기 총액이 비었다(이행기 무배당)              → 있는 것만 더한다
  · E5 결산기 하나                                         → `single`
"""
from __future__ import annotations

import pytest

from open_proxy_mcp.services import dividend_data as dd          # noqa: E402

COLS = ["corp_code", "name", "tickers", "bsns_year", "reprt_code", "reprt_label", "stock_knd_raw", "stock_kind", "dps_krw", "div_total_krw",
        "unit_raw", "payout_pct", "rcept_no", "stlm_dt", "se_raw", "status", "line_no"]


def _row(corp, name, ticker, fy, stlm, dps, total, status="확정", rcept="R", line=1):
    return (corp, name, ticker, fy, "11011", "사업보고서", "보통주식", "보통", dps, total, "원", None, rcept, stlm, "주당 현금배당금(원)", status, line)


def _rows():
    out = []
    # E1 — 제이알글로벌리츠 FY2025 꼴: 두 결산기 DPS·총액이 똑같다
    out += [_row("E1", "같은배당리츠", "000001", 2025, "2025-06-30", 115.0, 22_698_000_000.0, rcept="A1"),
            _row("E1", "같은배당리츠", "000001", 2025, "2025-12-31", 115.0, 22_698_000_000.0, rcept="A2")]
    # E2 — 옛 복사 꼴: 총액이 같고(가장 늦은 결산기 것) DPS 가 다르다
    out += [_row("E2", "복사꼴리츠", "000002", 2025, "2025-06-30", 117.0, 35_543_000_000.0, rcept="B1"),
            _row("E2", "복사꼴리츠", "000002", 2025, "2025-12-31", 123.0, 35_543_000_000.0, rcept="B2")]
    # E3 — 고친 원장: 롯데리츠 FY2025 (각 결산기 총액)
    out += [_row("E3", "고친원장리츠", "000003", 2025, "2025-06-30", 117.0, 33_809_000_000.0, rcept="C1"),
            _row("E3", "고친원장리츠", "000003", 2025, "2025-12-31", 123.0, 35_543_000_000.0, rcept="C2")]
    # E4 — 앞 결산기 총액 칸이 비었다(총액 한 줄만 있다) — 합하지 않을 이유가 없다(복사 꼴 판정에는 총액이 둘 이상 있어야 한다)
    out += [_row("E4", "이행기리츠", "000004", 2025, "2025-06-30", 50.0, None, status="확정", rcept="D1"),
            _row("E4", "이행기리츠", "000004", 2025, "2025-12-31", 60.0, 120_000_000.0, rcept="D2")]
    # E5 — 12월 결산
    out += [_row("E5", "일반사", "000005", 2025, "2025-12-31", 1444.0, 9.8e12, rcept="E1")]
    # E6 — 총액·DPS 가 모두 같은 분기 결산(네 결산기) + 한 결산기만 DPS 가 달라도 총액이 같으면 복사 꼴로 본다
    for stlm, dps in (("2025-03-31", 66.0), ("2025-06-30", 66.0), ("2025-09-30", 66.0), ("2025-12-31", 70.0)):
        out.append(_row("E6", "분기리츠", "000006", 2025, stlm, dps, 19_797_000_000.0, rcept="F" + stlm[5:7]))
    return out


# ── 파이썬 쪽(firm) — DB 없이 ───────────────────────────────────────────────────────────────────────
def _annual(rows):
    keys = ("corp_code", "name", "tickers", "bsns_year", "reprt_code", "reprt_label", "stock_knd_raw", "stock_kind", "dps_krw", "div_total_krw",
            "unit_raw", "payout_pct", "rcept_no", "stlm_dt", "se_raw", "row_status", "line_no")
    return [dict(zip(keys, r)) for r in rows]


def _py_totals():
    ann = _annual(_rows())
    return {c: {t["bsns_year"]: t for t in dd.year_totals([a for a in ann if a["corp_code"] == c])} for c in {a["corp_code"] for a in ann}}


def test_python_year_totals_sum_when_totals_and_dps_are_both_equal():
    t = _py_totals()["E1"][2025]
    assert t["total_basis"] == "sum" and t["div_total_krw"] == 45_396_000_000 and t["dps_krw"] == 230


def test_python_year_totals_keep_the_copy_guard_when_totals_equal_but_dps_differ():
    for c, total in (("E2", 35_543_000_000), ("E6", 19_797_000_000)):
        t = _py_totals()[c][2025]
        assert t["total_basis"] == "ledger_copy" and t["div_total_krw"] == total, c
    assert _py_totals()["E2"][2025]["dps_krw"] == 240, "DPS 는 어느 쪽이든 결산기 합"


def test_python_year_totals_sum_distinct_and_partial_totals():
    t = _py_totals()
    assert t["E3"][2025]["total_basis"] == "sum" and t["E3"][2025]["div_total_krw"] == 69_352_000_000
    assert t["E4"][2025]["div_total_krw"] == 120_000_000 and t["E4"][2025]["total_basis"] == "sum"
    assert t["E5"][2025]["total_basis"] == "single"


# ── SQL 쪽(screen·market·sector) — 실제 Postgres ──────────────────────────────────────────────────────
psycopg = pytest.importorskip("psycopg")

DDL = """
CREATE TABLE div_declared(
  corp_code text, name text, tickers text, bsns_year int, reprt_code text, reprt_label text, stock_knd_raw text, stock_kind text,
  dps_krw double precision, div_total_krw double precision, unit_raw text, payout_pct double precision, rcept_no text, stlm_dt text,
  se_raw text, status text, line_no smallint);
CREATE TABLE div_quarterly(
  corp_code text, name text, tickers text, bsns_year int, reprt_code text, q_label text, stlm_dt text, rcept_no text,
  cum_div_total_krw double precision, quarterly_div_krw double precision, basis_from text, basis_to text, anomaly text, status text);
CREATE TABLE div_payment(corp_code text, fiscal_year int, board_date text);
CREATE TABLE wise_sector(snap_dd text, ticker text, sector text);
CREATE TABLE div_payment_scope(market text, fiscal_year int, is_complete boolean);
"""


@pytest.fixture
def ledger(pg17, monkeypatch):
    def run(sql, params=()):
        with psycopg.connect(pg17, autocommit=True) as c:
            cur = c.execute(sql, params)
            return [tuple(r) for r in cur.fetchall()] if cur.description else None
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(DDL)
        with c.cursor() as cur:
            cur.executemany("INSERT INTO div_declared VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", _rows())
            cur.executemany("INSERT INTO wise_sector VALUES (%s,%s,%s)", [("20260930", f"00000{i}", "부동산") for i in range(1, 7)])
            cur.execute("INSERT INTO div_payment_scope VALUES ('KOSPI', 2025, true)")
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): run(sql, params))
    return run


def test_screen_sql_and_python_agree_on_total_basis(ledger):
    sql = {r["corp_code"]: r for r in dd.screen(2025, limit=100)["rows"]}
    py = _py_totals()
    for c in ("E1", "E2", "E3", "E4", "E5", "E6"):
        assert sql[c]["total_basis"] == py[c][2025]["total_basis"], c
        assert sql[c]["div_total_krw"] == py[c][2025]["div_total_krw"], c
        assert sql[c]["dps_krw"] == py[c][2025]["dps_krw"], c


def test_screen_sums_equal_totals_when_dps_are_equal_too(ledger):
    r = {x["corp_code"]: x for x in dd.screen(2025, limit=100)["rows"]}
    assert r["E1"]["div_total_krw"] == 45_396_000_000 and r["E1"]["total_basis"] == "sum"
    assert r["E2"]["div_total_krw"] == 35_543_000_000 and r["E2"]["total_basis"] == "ledger_copy"


def test_market_and_sector_count_the_guard_only_for_the_copy_shapes(ledger):
    want = 45_396_000_000 + 35_543_000_000 + 69_352_000_000 + 120_000_000 + 9.8e12 + 19_797_000_000
    m = dd.aggregate_history("market", "코스피", 2025, 2025)["rows"][0]
    assert m["div_total_krw"] == pytest.approx(want)
    assert m["n_copy_guard"] == 2, "복사 꼴은 E2·E6 둘 — E1(총액·DPS 같음)은 더했다"
    s = dd.aggregate_history("sector", "부동산", 2025, 2025)["rows"][0]
    assert s["n_copy_guard"] == 2
