# -*- coding: utf-8 -*-
"""반기·분기 결산 리츠 — 배당 원장(`dividend_data`) 의 회사·사업연도 DPS 는 **그 해 결산기 DPS 의 합**이다 (261021, 공개 DATA 문서 DATA-DIV1·DATA-FY3).

종전 `aggregate_history` 는 `DISTINCT ON (회사, 사업연도)` 로 총액이 큰 한 결산기만 읽었고, `screen` 은 **총액이 같은 행 가운데 임의의 한 줄**의 DPS 를 냈다
(옛 원장은 결산기 행마다 가장 늦은 결산기 총액을 복사해 둬 늘 동률이었다 — 롯데리츠 FY2025 = 2025-06-30 117원 · 2025-12-31 123원 → 240원).
실제 Postgres 17 에서 서비스 SQL 을 돌린다(Docker 가 없으면 건너뜀). 값은 운영 롯데리츠·SK리츠 원장 그대로, 나머지는 합성.

  · R1 롯데리츠(반기)  — FY2025 117 + 123 = 240, 총액 355.43억 이 두 결산기 행에 똑같이 복사(→ 합하지 않음, `ledger_copy`. 수집기를 고친 뒤 운영 원장은 결산기별 실제 총액이라
                          이 꼴은 방어선만 남았다 — 총액이 같고 DPS 가 다른 줄. 총액이 같고 DPS 도 같은 해는 더한다: test_dividend_data_equal_totals.py).
  · R2 합성 리츠(반기) — 결산기 총액이 서로 다르다(원장이 실제 결산기 총액으로 다시 쓰인 뒤의 꼴) → 더한다(`sum`).
  · R3 SK리츠(분기)   — FY2025 70 + 66 + 66 + 66 = 268, 총액 복사.
  · C1 12월 결산 일반 종목 — 결산기 하나: 값 그대로. C2 호텔신라 꼴 — 보통주 줄과 우선주 줄이 둘 다 「미구분」(종류 칸 `-`) : 원문 첫 줄 DPS 그대로.
  · NODIV 무배당. 섹터 표에는 같은 종목이 두 줄(조인 중복)이어도 한 번만 센다.
"""
from __future__ import annotations

import pytest

psycopg = pytest.importorskip("psycopg")

from open_proxy_mcp.services import dividend_data as dd          # noqa: E402
from open_proxy_mcp.tools import dividend_data as dd_tool       # noqa: E402

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


def _row(corp, name, ticker, fy, stlm, kind, dps, total, status, rcept, *, payout=None, raw="-", line=1):
    return (corp, name, ticker, fy, "11011", "사업보고서", raw, kind, dps, total, "원", payout, rcept, stlm, "주당 현금배당금(원)", status, line)


def _rows_for_fy2025():
    R1, R1T = ("R1", "롯데리츠", "330590"), 35_543_000_000.0
    R2, R3 = ("R2", "합성리츠", "000002"), ("R3", "SK리츠", "395400")
    out = []
    # R1 — 결산기 두 행, 각 행에 보통주 줄(확정) + 종류 칸 빈 무배당 줄. 총액은 두 행 모두 가장 늦은 결산기 것(355.43억)이 복사
    for stlm, dps, rc in (("2025-06-30", 117.0, "20250911000374"), ("2025-12-31", 123.0, "20260310002810")):
        out.append(_row(*R1, 2025, stlm, "보통", dps, R1T, "확정", rc, raw="보통주식", line=1))
        out.append(_row(*R1, 2025, stlm, "미구분", None, R1T, "무배당", rc, line=2))
    # R2 — 결산기 총액이 서로 다르다(실제 값으로 다시 쓰인 원장)
    for stlm, dps, tot, rc in (("2025-06-30", 50.0, 100_000_000.0, "20250911000001"), ("2025-12-31", 60.0, 120_000_000.0, "20260310000001")):
        out.append(_row(*R2, 2025, stlm, "보통", dps, tot, "확정", rc, raw="보통주식"))
    # R3 — 분기 결산, 총액 복사
    for stlm, dps, rc in (("2025-03-31", 70.0, "20250611000555"), ("2025-06-30", 66.0, "20250922000291"),
                          ("2025-09-30", 66.0, "20251210000536"), ("2025-12-31", 66.0, "20260311004347")):
        out.append(_row(*R3, 2025, stlm, "보통", dps, 19_797_000_000.0, "확정", rc, raw="보통주식"))
    # C1 — 12월 결산
    out.append(_row("C1", "삼성전자", "005930", 2025, "2025-12-31", "보통", 1444.0, 9.8e12, "확정", "20260310000100", payout=30.0, raw="보통주"))
    # C2 — 보통주 200 · 우선주 250 이 둘 다 「미구분」 (총액 동률)
    out.append(_row("C2", "호텔신라", "008770", 2025, "2025-12-31", "미구분", 200.0, 7.6e9, "확정", "20260310000200", payout=-2.69, line=1))
    out.append(_row("C2", "호텔신라", "008770", 2025, "2025-12-31", "미구분", 250.0, 7.6e9, "확정", "20260310000200", payout=-2.69, line=2))
    # NODIV — 무배당
    out.append(_row("N1", "무배당사", "000009", 2025, "2025-12-31", "보통", None, None, "무배당", "20260310000900", raw="보통주"))
    # 2024 — R1 두 결산기
    for stlm, dps, rc in (("2024-06-30", 103.0, "20240902000317"), ("2024-12-31", 112.0, "20250312000767")):
        out.append(_row(*R1, 2024, stlm, "보통", dps, 32_365_000_000.0, "확정", rc, raw="보통주식"))
    return out


@pytest.fixture
def ledger(pg17, monkeypatch):
    def run(sql, params=()):
        with psycopg.connect(pg17, autocommit=True) as c:
            cur = c.execute(sql, params)
            return [tuple(r) for r in cur.fetchall()] if cur.description else None
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(DDL)
        with c.cursor() as cur:
            cur.executemany("INSERT INTO div_declared VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", _rows_for_fy2025())
            cur.executemany("INSERT INTO wise_sector VALUES (%s,%s,%s)", [
                ("20260930", "330590", "부동산"), ("20260930", "330590", "부동산"),       # 같은 종목이 두 줄 — 조인 중복
                ("20260930", "395400", "부동산"), ("20260930", "000002", "부동산"),
                ("20260930", "005930", "IT"), ("20260930", "008770", "경기소비재"),
            ])
            cur.execute("INSERT INTO div_payment_scope VALUES ('KOSPI', 2025, true)")
            cur.executemany("INSERT INTO div_payment VALUES (%s,%s,%s)", [
                ("R1", 2025, "2025-08-01"), ("R1", 2025, "2026-02-01"),
                ("R3", 2025, "2025-05-01"), ("R3", 2025, "2025-08-01"), ("R3", 2025, "2025-11-01"), ("R3", 2025, "2026-02-01"),
                ("C1", 2025, "2026-02-01")])
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): run(sql, params))
    return run


def _by(d):
    return {r["corp_code"]: r for r in d["rows"]}


def test_screen_dps_is_the_sum_of_the_years_settlement_periods(ledger):
    d = dd.screen(2025, limit=100)
    rows = _by(d)
    assert rows["R1"]["dps_krw"] == 240 and rows["R1"]["n_periods"] == 2, "롯데리츠 FY2025 = 117 + 123 (종전: 둘 중 임의의 한 결산기)"
    assert rows["R2"]["dps_krw"] == 110
    assert rows["R3"]["dps_krw"] == 268 and rows["R3"]["n_periods"] == 4, "SK리츠 70+66+66+66"
    assert rows["C1"]["dps_krw"] == 1444 and rows["C1"]["n_periods"] == 1, "12월 결산 일반 종목은 그대로"
    assert rows["C2"]["dps_krw"] == 200, "종류 칸이 빈 우선주 줄(250)이 보통주 줄(200)을 밀어내지 않는다 — 원문 첫 줄"
    assert "N1" not in rows, "무배당 제외"
    assert d["matched"] == 5 and d["n_universe"] == 6, "모집단 6사(무배당 포함) 중 5사"


def test_screen_min_dps_filters_on_the_sum_not_on_one_settlement_period(ledger):
    """🔴 종전: 한 결산기 DPS(117·123)로 걸러 min_dps=130 이면 롯데리츠가 빠졌다(연 240원)."""
    got = _by(dd.screen(2025, min_dps=130, limit=100))
    assert "R1" in got and got["R1"]["dps_krw"] == 240
    assert "R2" not in got, "합성 리츠 110원은 130 미만"
    assert set(got) == {"R1", "R3", "C1", "C2"}
    assert set(_by(dd.screen(2025, min_dps=250, limit=100))) == {"R3", "C1"}


def test_screen_total_is_summed_only_when_the_settlement_totals_differ(ledger):
    rows = _by(dd.screen(2025, limit=100))
    assert rows["R1"]["div_total_krw"] == 35_543_000_000 and rows["R1"]["total_basis"] == "ledger_copy", \
        "결산기 총액이 모두 같다 = 원장이 가장 늦은 결산기 총액을 복사 → 합하지 않고 하나(종전 값)"
    assert rows["R3"]["div_total_krw"] == 19_797_000_000 and rows["R3"]["total_basis"] == "ledger_copy"
    assert rows["R2"]["div_total_krw"] == 220_000_000 and rows["R2"]["total_basis"] == "sum"
    assert rows["C1"]["div_total_krw"] == 9.8e12 and rows["C1"]["total_basis"] == "single"


def test_screen_rcept_no_is_the_latest_settlement_report(ledger):
    assert _by(dd.screen(2025, limit=100))["R1"]["rcept_no"] == "20260310002810"


def test_screen_sector_join_duplicates_do_not_double_count(ledger):
    rows = _by(dd.screen(2025, sector="부동산", limit=100))
    assert rows["R1"]["dps_krw"] == 240, "섹터 표에 같은 종목이 두 줄이어도 결산기 DPS 가 두 번 더해지지 않는다"
    assert set(rows) == {"R1", "R2", "R3"}


def test_screen_min_payments_still_counts_decisions(ledger):
    assert set(_by(dd.screen(2025, min_payments=2, limit=100))) == {"R1", "R3"}   # 2025 결의 2건 · 4건


def test_market_aggregate_reads_one_row_per_company_year(ledger):
    m = dd.aggregate_history("market", "코스피", 2024, 2025)
    r = {x["bsns_year"]: x for x in m["rows"]}
    assert r[2025]["n_universe"] == 6 and r[2025]["n_payers"] == 5
    # 총액: R1 355.43억(복사 → 하나) + R2 2.2억(합) + R3 197.97억(복사 → 하나) + C1 9.8조 + C2 76억
    assert r[2025]["div_total_krw"] == pytest.approx(35_543_000_000 + 220_000_000 + 19_797_000_000 + 9.8e12 + 7.6e9)
    assert r[2025]["n_copy_guard"] == 2, "총액을 합하지 않은 (회사, 사업연도) 수 — R1·R3"
    assert r[2024]["n_universe"] == 1 and r[2024]["div_total_krw"] == 32_365_000_000 and r[2024]["n_copy_guard"] == 1


def test_sector_aggregate_counts_a_company_once_despite_duplicate_sector_rows(ledger):
    s = dd.aggregate_history("sector", "부동산", 2025, 2025)
    row = s["rows"][0]
    assert row["n_universe"] == 3 and row["n_payers"] == 3
    assert row["div_total_krw"] == pytest.approx(35_543_000_000 + 220_000_000 + 19_797_000_000)


def test_firm_history_year_totals_sum_the_settlement_periods(ledger):
    f = dd.firm_history("R1", 2024, 2025)
    t = {x["bsns_year"]: x for x in f["year_totals"]}
    assert t[2025]["dps_krw"] == 240 and t[2025]["n_periods"] == 2 and t[2025]["total_basis"] == "ledger_copy"
    assert [(p["stlm_dt"], p["dps_krw"]) for p in t[2025]["periods"]] == [("2025-06-30", 117), ("2025-12-31", 123)]
    assert t[2024]["dps_krw"] == 215
    assert len(f["annual"]) == 4 and f["empty_kind_rows_folded"] == 2, "원장 줄은 그대로 낸다(결산기마다 한 줄 — 종류 칸 빈 줄만 종전대로 접는다)"
    one = dd.firm_history("C1", 2025, 2025)["year_totals"][0]
    assert one["dps_krw"] == 1444 and one["n_periods"] == 1 and one["total_basis"] == "single"


def test_firm_history_keeps_every_ledger_line_and_shows_the_settlement_date(ledger):
    f = dd.firm_history("R1", 2025, 2025)
    assert {a["stlm_dt"] for a in f["annual"]} == {"2025-06-30", "2025-12-31"}
    md = dd_tool._render_firm("롯데리츠", "330590", f, {"status": "ok", "complete_years": [2025], "rows": [], "decisions": []})
    assert "### 원장 — 사업연도 합 (결산기가 둘 이상인 해, 보통주)" in md
    assert "| 2025 | 2개 | 240원 | 2025-06-30 117원 + 2025-12-31 123원 |" in md
    assert "† 합하지 않음(복사 꼴)" in md and "가장 늦은 결산기의 총액을 복사" in md
    assert "| 사업연도 | 결산일 | 주식 종류 | DPS |" in md and "| 2025 | 2025-06-30 |" in md


def test_screen_render_marks_period_sums_and_copied_totals(ledger):
    d = dd.screen(2025, limit=100)
    md = dd_tool._render_screen(2025, [], d, 100, [2024], {})
    assert "240원 (2결산기 합)" in md and "268원 (4결산기 합)" in md
    assert "† 2사는 총액이 결산기 줄마다 같은데 DPS 는 달라 원장이 가장 늦은 결산기의 총액을 복사해 둔 꼴" in md
    assert "1,444원 (" not in md, "결산기 하나인 회사는 표기를 늘리지 않는다"


def test_market_render_notes_copied_totals(ledger):
    md = dd_tool._render_agg("market", "코스피", dd.aggregate_history("market", "코스피", 2024, 2025))
    assert "더하지 않고 그 하나를 썼다" in md


def test_screen_single_query_counts_the_whole_match_before_the_display_limit(ledger):
    """`COUNT(*) OVER ()` 는 LIMIT 앞에서 센다 — 실제 Postgres 에서 limit=2 여도 매칭 수는 5, 모집단은 6."""
    d = dd.screen(2025, limit=2)
    assert (d["matched"], d["n_universe"], d["returned"]) == (5, 6, 2)
    assert [r["corp_code"] for r in d["rows"]] == ["C1", "R1"], "총액 큰 순(C1 9.8조 → R1 355억)"
    none = dd.screen(2025, min_dps=10 ** 9)
    assert (none["matched"], none["n_universe"], none["rows"]) == (0, 6, []), "조건에 맞는 회사가 없어도 모집단은 낸다"
