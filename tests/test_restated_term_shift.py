# -*- coding: utf-8 -*-
"""정정 칸이 한 결산기씩 밀리는 것을 막는다 — 결산월을 바꾼 회사의 전기·전전기 칸 자리(261021 DATA-FY2 F10).

종전 읽기는 사업보고서(표기 fy)의 전기 칸을 fy−1, 전전기 칸을 fy−2 의 정정 칸에 넣었다. DART 표기는 결산기 끝의 달력 연도라 결산월을 바꾼 회사에서는 맞지 않는다:
  · 154030(9월 → 12월, 2025 전환기 2025.10~12 석 달): 2025 사업보고서의 전기 칸 = 제22기(2025.09, +19.05억)·전전기 칸 = 제21기(2024.09, −6.34억).
    종전엔 FY2024 정정 칸에 19.05억, FY2023 정정 칸에 −6.34억이 들어갔다(한 결산기씩 밀림). 제22기는 표기 2025 칸을 전환기 보고서가 차지해 칸이 없고,
    제21기가 맞는 자리는 FY2024 다.
  · 222160(3월 → 12월, 2024 전환기 2024.04~12 아홉 달): 2024 사업보고서의 전전기 칸 = 제25기(2023.03) → FY2023 의 자리, 종전엔 FY2022 정정 칸에 1.22억.
이제 전기·전전기 칸이 실제로 어느 표기 칸의 자리인지 결산월로 가르고(`report_calendar.prior_columns`) 보고서 기수로 확인한다(`market_val_series.prior_candidates`).

DART 0콜(가짜 클라이언트). Postgres 17 은 도커 없으면 건너뜀(`tests/conftest.py` 의 `pg17`).
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from open_proxy_mcp.services import report_calendar as rcal
from scripts import market_fund_quarterly as Q
from scripts import market_val_series as S

psycopg = pytest.importorskip("psycopg")

B = 100_000_000      # 1억
F154 = rcal.Fiscal(12, False, (), 9, 2025)      # 154030 — 9월 → 12월, 첫 사업연도 2025
F222 = rcal.Fiscal(12, False, (), 3, 2024)      # 222160 — 3월 → 12월, 첫 사업연도 2024
F334 = rcal.Fiscal(6, False, (), 12, 2023)      # 334970 — 12월 → 6월, 첫 사업연도 2023


# ═══ 순수: 칸의 자리 ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("fye", [12, 3, 6, rcal.Fiscal(), rcal.Fiscal(9), rcal.Fiscal(2, True)])
def test_unchanged_companies_keep_the_old_assumption(fye):
    for fy in range(2018, 2032):
        assert rcal.prior_columns(fy, fye) == [(fy - 1, True, ""), (fy - 2, True, "")]


@pytest.mark.parametrize("fye, report_fy, want", [
    # 154030 — 9 → 12. 새 결산월이 늦어 옛 마지막 결산기(2025.09)와 전환기(2025.12)가 같은 표기 2025 를 다툰다
    (F154, 2025, [(2025, False, "collision"), (2024, True, "")]),
    (F154, 2026, [(2025, False, "transition"), (2025, False, "collision")]),
    (F154, 2027, [(2026, True, ""), (2025, False, "transition")]),
    # 222160 — 3 → 12 (2024 전환기 9개월)
    (F222, 2024, [(2024, False, "collision"), (2023, True, "")]),
    (F222, 2025, [(2024, False, "transition"), (2024, False, "collision")]),
    (F222, 2026, [(2025, True, ""), (2024, False, "transition")]),
    # 334970 — 12 → 6. 새 결산월이 이르면 옛 마지막 결산기(2022.12)가 앞 해라 표기가 겹치지 않는다
    (F334, 2023, [(2022, True, ""), (2021, True, "")]),
    (F334, 2024, [(2023, False, "transition"), (2022, True, "")]),
    (F334, 2025, [(2024, True, ""), (2023, False, "transition")]),
    (F334, 2026, [(2025, True, ""), (2024, True, "")]),
])
def test_prior_columns_for_companies_that_changed_the_fiscal_month(fye, report_fy, want):
    assert rcal.prior_columns(report_fy, fye) == want


# ═══ 순수: 응답 → 후보 ═══════════════════════════════════════════════════════════════════════════
def _rows(rc, terms, ni, eq):
    """사업보고서 응답 꼴 — 당기·전기·전전기 칸에 기수(제 N 기)를 붙인다. ni·eq = (당기, 전기, 전전기)."""
    def amt(v):
        return "" if v is None else str(v)
    nm = {"thstrm_nm": f"제 {terms[0]} 기", "frmtrm_nm": f"제 {terms[1]} 기", "bfefrmtrm_nm": f"제 {terms[2]} 기"} if terms else {}
    return [
        {"sj_div": "BS", "account_id": "ifrs-full_EquityAttributableToOwnersOfParent", "account_nm": "지배기업 소유주지분",
         "thstrm_amount": amt(eq[0]), "frmtrm_amount": amt(eq[1]), "bfefrmtrm_amount": amt(eq[2]), "currency": "KRW", "rcept_no": rc, **nm},
        {"sj_div": "BS", "account_id": "ifrs-full_Equity", "account_nm": "자본총계",
         "thstrm_amount": amt(eq[0]), "frmtrm_amount": amt(eq[1]), "bfefrmtrm_amount": amt(eq[2]), "currency": "KRW", "rcept_no": rc, **nm},
        {"sj_div": "IS", "account_id": "ifrs-full_ProfitLoss", "account_nm": "당기순이익",
         "thstrm_amount": amt(ni[0]), "frmtrm_amount": amt(ni[1]), "bfefrmtrm_amount": amt(ni[2]), "currency": "KRW", "rcept_no": rc, **nm},
        {"sj_div": "IS", "account_id": "ifrs-full_ProfitLossAttributableToOwnersOfParent", "account_nm": "지배기업 소유주지분",
         "thstrm_amount": amt(ni[0]), "frmtrm_amount": amt(ni[1]), "bfefrmtrm_amount": amt(ni[2]), "currency": "KRW", "rcept_no": rc, **nm},
    ]


# 154030 2025 사업보고서(2025.12 전환기) — DART 원값(10-20 원문): 당기 −13.66억(3개월)·전기 +19.05억(제22기 2025.09)·전전기 −6.34억(제21기 2024.09)
R154 = _rows("20260320000001", (23, 22, 21), (-1366478768, 1905122329, -633857412), (24100295993, 25525973594, 22718340798))
# 222160 2025 사업보고서(2025.12) — 당기 12개월·전기 = 2024 전환기(9개월)·전전기 = 제25기(2024.03)
R222_2025 = _rows("20260320001395", (27, 26, 25), (-15716936622, -3032458683, -7334661001), (4488669254, 20591397758, 23566329118))
# 222160 2024 사업보고서(2024.12 전환기) — 전기 = 제25기(2024.03)·전전기 = 제24기(2023.03)
R222_2024 = _rows("20250319000827", (26, 25, 24), (-3032458683, -7334661001, 121752316), (20591397758, 23566329118, 23844901048))


def test_the_old_assumption_shifts_every_changed_company_and_the_new_one_does_not():
    # 종전(결산월을 모르는 읽기): 전기 → 2024, 전전기 → 2023 — 둘 다 한 결산기씩 밀린 값
    assert S.prior_candidates(R154, 2025) == [(2024, 1905122329.0, 25525973594.0), (2023, -633857412.0, 22718340798.0)]
    # 고침: 제22기(2025.09)는 표기 2025 칸을 전환기 보고서가 차지해 자리가 없고, 제21기(2024.09 −6.34억)는 FY2024 의 자리다
    assert S.prior_candidates(R154, 2025, F154) == [(2024, -633857412.0, 22718340798.0)]
    # 222160 2024 전환기 보고서: 전기(제25기 2024.03, −73.3억)는 표기 2024 칸을 전환기가 차지해 자리가 없고, 전전기(제24기 2023.03, +1.22억)는 FY2023 의 자리다
    assert [c[0] for c in S.prior_candidates(R222_2024, 2024)] == [2023, 2022]                                  # 종전 읽기 — 밀린 표기(FY2022 에 1.22억)
    assert S.prior_candidates(R222_2024, 2024, F222) == [(2023, 121752316.0, 23844901048.0)]                    # 고침
    # 다음 해(2025) 보고서: 전기 = 전환기(연간 아님) · 전전기 = 제25기(2024.03, 표기 2024 는 전환기가 차지) → 쓸 칸이 없다
    assert S.prior_candidates(R222_2025, 2025, F222) == []
    assert [c[0] for c in S.prior_candidates(R222_2025, 2025)] == [2024, 2023]                                  # 종전 읽기 — FY2023 정정 칸에 −73.3억


def test_term_numbers_must_run_down_by_one():
    """보고서의 세 칸 기수가 하나씩 줄지 않으면 그 칸은 앞 칸의 바로 앞 결산기가 아니다 — 결산월 변경이 아직 저장되지 않았어도 밀린 칸을 막는다."""
    gap = _rows("20260320000009", (23, 22, 20), (10 * B, 9 * B, 8 * B), (100 * B, 90 * B, 80 * B))       # 22 → 20: 결산기 하나가 끼어 있다
    assert [c[0] for c in S.prior_candidates(gap, 2025)] == [2024]                  # 전전기 칸은 쓰지 않는다
    assert [c[0] for c in S.prior_candidates(_rows("20260320000009", (23, 22, 21), (10 * B, 9 * B, 8 * B), (100 * B, 90 * B, 80 * B)), 2025)] == [2024, 2023]
    assert [c[0] for c in S.prior_candidates(_rows("x", (23, 20, 19), (1 * B, 2 * B, 3 * B), (1 * B, 2 * B, 3 * B)), 2025)] == []   # 전기부터 어긋남
    ok = _rows("20260320000009", (23, 22, 21), (10 * B, 9 * B, 8 * B), (100 * B, 90 * B, 80 * B))
    # 이 실행에서 읽은 그 해 보고서의 당기 기수와 대조: FY2024 보고서가 제 21 기면 전기(제 22 기)와 다르다 → 밀림
    assert [c[0] for c in S.prior_candidates(ok, 2025, 12, {2024: 21})] == [2023]
    assert [c[0] for c in S.prior_candidates(ok, 2025, 12, {2024: 22, 2023: 21})] == [2024, 2023]
    # 기수가 응답에 없으면 확인하지 않는다(종전 식 — 기존 시험 응답 꼴)
    assert [c[0] for c in S.prior_candidates(_rows("x", None, (1 * B, 2 * B, 3 * B), (1 * B, 2 * B, 3 * B)), 2025)] == [2024, 2023]


# ═══ Postgres: 수집 · 백필 ═══════════════════════════════════════════════════════════════════════
FINSTAT_Y_DDL = """CREATE TABLE dart_finstat_y(
  ticker text, fy int, fs text, ni double precision, eq double precision, fetched text,
  ni_restated double precision, eq_restated double precision, PRIMARY KEY(ticker, fy))"""
FINSTAT_Q_DDL = """CREATE TABLE dart_finstat_q(
  ticker text, fy int, quarter int, reprt_code text, fs text,
  ni_cum double precision, eq double precision, fetched text,
  ni_case text, eq_case text,
  ni_cum_raw double precision, eq_raw double precision,
  ni_cum_restated double precision, eq_restated double precision, restate_why text,
  PRIMARY KEY(ticker, fy, quarter))"""


class _FakeDart:
    def __init__(self):
        self.avail: dict = {}
        self.calls: list[tuple] = []

    async def get_fnltt_singl_acnt_all(self, corp_code, bsns_year, reprt_code="11011", fs_div="CFS"):
        from open_proxy_mcp.dart.client import DartClientError
        self.calls.append((corp_code, str(bsns_year), reprt_code, fs_div))
        rows = self.avail.get((corp_code, str(bsns_year), reprt_code, fs_div))
        if rows is None:
            raise DartClientError("013", "조회된 데이타가 없습니다.")
        return {"status": "000", "list": rows}


def _q(url, sql, params=()):
    with psycopg.connect(url) as c:
        return c.execute(sql, params).fetchall()


@pytest.fixture
def cdb(pg17, monkeypatch):
    """T1 = 154030 꼴(9 → 12, 2025) · T2 = 222160 꼴(3 → 12, 2024) · T3 = 결산월을 안 바꾼 12월 결산(대조군)."""
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(FINSTAT_Y_DDL)
        c.execute(FINSTAT_Q_DDL)
        c.execute(Q.DDL_FUNDAMENTALS)
        c.execute("INSERT INTO dart_fundamentals (ticker, corp_code, fetched, acc_mt, acc_mt_prev, acc_mt_since) VALUES "
                  "('T1','00T1','ok',12,9,2025), ('T2','00T2','ok',12,3,2024), ('T3','00T3','ok',12,NULL,NULL)")
        ins = "INSERT INTO dart_finstat_y (ticker, fy, fs, ni, eq, fetched, ni_restated, eq_restated) VALUES (%s,%s,'CFS',%s,%s,'ok',%s,%s)"
        for fy, ni, eq in ((2022, -604854675, 24762673102), (2023, -2451445851, 22292045453), (2024, -633857412, 22718340798)):
            c.execute(ins, ("T1", fy, ni, eq, None, None))
        for fy, ni, eq in ((2022, 421198428, 23671597038), (2023, 121752316, 23844901048)):
            c.execute(ins, ("T2", fy, ni, eq, None, None))
        for fy, ni, eq in ((2023, 20 * B, 300 * B), (2024, 25 * B, 320 * B)):
            c.execute(ins, ("T3", fy, ni, eq, None, None))
    fake = _FakeDart()
    monkeypatch.setenv("DATABASE_URL", pg17)
    monkeypatch.setenv("SERIES_SLEEP", "0")
    monkeypatch.setattr("open_proxy_mcp.dart.client.get_dart_client", lambda: fake)
    monkeypatch.setattr(S, "today_kst", lambda: date(2026, 10, 19))
    monkeypatch.setattr(S, "_RCEPT_COL", False)
    monkeypatch.setattr(S, "_RESTATED_COL", False)
    return pg17, fake


def _y(url, t):
    return _q(url, "SELECT fy, ni, eq, ni_restated, eq_restated FROM dart_finstat_y WHERE ticker=%s ORDER BY fy", (t,))


def test_regular_fetch_marks_the_transition_year_and_writes_no_shifted_restated_cell(cdb, capsys):
    """154030 꼴: FY2025 전환기 사업보고서(−13.66억 3개월)를 받으면 순이익은 연간 칸에 넣지 않고(자본만) 전기·전전기 칸은 밀리지 않는다."""
    url, fake = cdb
    fake.avail[("00T1", "2025", "11011", "CFS")] = R154
    asyncio.run(S.fetch(["T1"]))
    rows = {r[0]: r for r in _y(url, "T1")}
    assert [rows[y] for y in (2022, 2023, 2024)] == [(2022, -604854675.0, 24762673102.0, None, None),
                                                     (2023, -2451445851.0, 22292045453.0, None, None),
                                                     (2024, -633857412.0, 22718340798.0, None, None)], "FY2023·FY2024 정정 칸이 밀린 값으로 채워지지 않는다"
    assert rows[2025] == (2025, None, 24100295993.0, None, None)          # 전환기: 순이익 없음 · 자본은 그 날 잔액
    assert "[전환기] T1 FY2025" in capsys.readouterr().out
    assert fake.calls.count(("00T1", "2025", "11011", "CFS")) == 1 and not any(c[1] == "2026" for c in fake.calls), "받는 상한은 이 날의 최신 사업보고서(2025)"


def test_regular_fetch_before_the_fix_would_have_written_the_shifted_cells(cdb):
    """같은 응답을 결산월 변경 칸 없이(종전 읽기) 받으면 FY2024 ← 19.05억(2025.09)·FY2023 ← −6.34억(2024.09) 이 들어가고 FY2025 에 −13.66억 석 달이 연간 칸에 들어간다."""
    url, fake = cdb
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("UPDATE dart_fundamentals SET acc_mt_prev=NULL, acc_mt_since=NULL WHERE ticker='T1'")
    fake.avail[("00T1", "2025", "11011", "CFS")] = R154
    asyncio.run(S.fetch(["T1"]))
    rows = {r[0]: r for r in _y(url, "T1")}
    assert rows[2023][3] == -633857412.0 and rows[2024][3] == 1905122329.0 and rows[2025][1] == -1366478768.0


def test_backfill_writes_the_prior_columns_to_their_real_cells(cdb, capsys):
    """222160 꼴 2024 전환기 사업보고서로 백필 — 전기(2024.03)는 칸 없음, 전전기(2023.03)는 FY2023 칸(원래 FY2022 에 들어갔다). 재작성(값이 다른 경우)만 쓴다."""
    url, fake = cdb
    # 전전기(2023.03) 값을 재작성 값으로 바꿔 둔다(원본 1.22억 → 1.30억) — 같은 값이면 쓸 가치가 없어 위치를 볼 수 없다
    rows = _rows("20250319000827", (26, 25, 24), (-3032458683, -7334661001, 130000000), (20591397758, 23566329118, 23844901048))
    fake.avail[("00T2", "2024", "11011", "CFS")] = rows
    asyncio.run(S.backfill_restated(["T2"], 2024))
    got = {r[0]: r[3:] for r in _y(url, "T2")}
    assert got[2023] == (130000000.0, 23844901048.0), "전전기(제 24 기)는 2023.03 결산기 = FY2023 의 자리"
    assert got[2022] == (None, None), "종전엔 FY2022 정정 칸에 1.22억이 밀려 들어갔다"
    assert 2024 not in got or got[2024] == (None, None)
    assert "전기 칸은 FY2024 칸의 자리가 아니다" in capsys.readouterr().out


def test_unchanged_company_is_written_exactly_as_before(cdb):
    """대조군(T3, 12월 결산): 전기 → FY−1 · 전전기 → FY−2 — 종전과 같다."""
    url, fake = cdb
    rows = _rows("20260515000003", (25, 24, 23), (30 * B, 26 * B, 21 * B), (330 * B, 322 * B, 301 * B))
    fake.avail[("00T3", "2025", "11011", "CFS")] = rows
    asyncio.run(S.fetch(["T3"]))
    got = {r[0]: r for r in _y(url, "T3")}
    assert got[2024][3:] == (26.0 * B, 322.0 * B) and got[2023][3:] == (21.0 * B, 301.0 * B)
    assert got[2025][1:3] == (30.0 * B, 330.0 * B)


def test_prior_from_next_skips_a_cell_across_the_change_of_fiscal_month(cdb, capsys):
    """분기 전기 칸 보충(`--prior-from-next`)은 (fy, q) 칸의 값을 다음 해 같은 분기 보고서의 전기 칸에서 가져온다. 결산월을 바꾼 해를 끼면 다음 해 칸의 기간 말이
    이 칸보다 정확히 1년 뒤가 아니라 전기 칸이 이 칸의 기간이 아니다 — 154030 (2024,1)=2024.12(옛 결산월) ↔ (2025,1)=2025.03(새 결산월). 건너뛰고 DART 를 부르지 않는다."""
    url, fake = cdb
    with psycopg.connect(url, autocommit=True) as c:
        for t in ("T1", "T3"):
            for fy in (2023, 2024):
                c.execute("INSERT INTO dart_finstat_q (ticker, fy, quarter, reprt_code, fs, ni_cum, eq, fetched, ni_case, eq_case) "
                          "VALUES (%s, %s, 1, '11013', 'OFS', NULL, NULL, 'nodata', 'NODATA', 'NODATA')", (t, fy))
    res = asyncio.run(Q.fill_prior_from_next({("T1", 2024, 1), ("T3", 2023, 1)}, dry=True))
    assert "결산월을 바꾼 해" in res[("T1", 2024, 1)] and not any(c[0] == "00T1" for c in fake.calls)
    assert "결산월을 바꾼 해" not in res[("T3", 2023, 1)]                          # 안 바꾼 12월 결산은 종전대로 다음 해 보고서를 찾으러 간다
    assert any(c[0] == "00T3" and c[1] == "2024" and c[2] == "11013" for c in fake.calls)
