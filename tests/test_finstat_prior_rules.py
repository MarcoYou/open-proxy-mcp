# -*- coding: utf-8 -*-
"""전기 칸 보충(`--prior-from-next`)의 거름 규칙 · 근거 존중 · 지배자본 파서의 세 결함 (261019).

10-19 실데이터 작업(재무 결측 재수집 D2)이 손으로 걸러낸 세 경우를 코드로 옮긴다 — 운영 칸 2,023개에 새 규칙을 다시 적용하면 2,021칸이 손 작업과
같은 결정이고, 다른 2칸(336570 2021 반기·355150 2022 1분기)은 출처 보고서 자본변동표에 「합병」 줄이 있는 합병 뒤 보고서라 새 규칙이 맞다(PR 본문):
  ① 전년 연간 태깅 — 다음 해 보고서의 전기 칸이 전년 회계연도 전체(482690 2025 1분기 원문)
  ② 상장 전 기간의 자본 — 공모 전 자본(377300 2021-11~2022-03 pbr_mrq 74~129)
  ③ 스팩 합병 전 기간 — 비교 칸은 영업회사 값(319400 합병 전 pbr_mrq 0.20~0.58)
그리고 근거(restate_why)가 있는 칸은 다시 채우지 않는다(종전엔 값 칸만 보고 손으로 비운 칸을 같은 잘못된 값으로 다시 채웠다). 파서 쪽은
자본변동표 옛 접두어(`ifrs_Equity`)·1원 반올림 차이·비지배지분부채, 그리고 「지배기업 소유주지분」 계정을 납입자본(자본금) 줄에 붙인 회사 태깅.
DART 0콜(가짜 클라이언트) · 통합 시험은 Postgres 17(`pg17`, 도커 없으면 건너뜀)."""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import date

import pytest

from scripts import market_fund_quarterly as Q
from tests.test_finstat_q_parser_gaps import _xbrl_zip, _ctx_365330, END, START

NOSTD = "-표준계정코드 미사용-"


def row(sj, aid, nm, ordv, cur="KRW", detail=None, rcept="20250513000569", **amt):
    r = {"sj_div": sj, "account_id": aid, "account_nm": nm, "ord": str(ordv), "currency": cur, "rcept_no": rcept,
         **{k: ("" if v is None else str(v)) for k, v in amt.items()}}
    if detail is not None:
        r["account_detail"] = detail
    return r


CTRL = "자본 [구성요소]|지배기업의 소유주에게 귀속되는 지분 [구성요소]"


# ── 지배자본 파서: 납입자본 줄 태깅 · 비지배지분부채 ─────────────────────────────

def test_capital_line_named_as_controlling_equity_is_skipped_for_the_named_row():
    """056360 2019 1분기(연결) 원문 모양 — 「자본금」 줄에 지배자본 계정, 진짜 지배자본은 표준 코드 없는 「지배기업지분」 줄."""
    rows = [row("BS", NOSTD, "지배기업지분", 29, thstrm_amount=98184205985),
            row("BS", "ifrs_EquityAttributableToOwnersOfParent", "자본금", 30, thstrm_amount=4898400000),
            row("BS", "ifrs_Equity", "자본총계", 35, thstrm_amount=98184205985)]
    assert Q.extract_controlling_eq(rows) == (98184205985.0, "BS_NM_CAPLINE")


def test_capital_line_falls_back_to_total_minus_minority():
    """065680 2024 사업보고서 — 「자본금」 줄에 지배자본 계정 · 자본총계 2,463.7억 · 비지배 −898만."""
    rows = [row("BS", "ifrs-full_Equity", "자본총계", 29, thstrm_amount=246373997170),
            row("BS", "ifrs-full_EquityAttributableToOwnersOfParent", "자본금", 30, thstrm_amount=4967110000),
            row("BS", "ifrs-full_NoncontrollingInterests", "비지배지분", 31, thstrm_amount=-8988087)]
    assert Q.extract_controlling_eq(rows) == (246373997170.0 + 8988087, "TOTAL_MINUS_MINORITY_CAPLINE")


def test_capital_line_detected_by_issued_capital_value():
    """069960 2021 사업보고서 — 「납입자본」 줄(지배자본 계정) = 같은 재무상태표 자본금 계정 값. 자본총계는 표준 코드 없이 이름으로."""
    rows = [row("BS", "ifrs-full_EquityAttributableToOwnersOfParent", "납입자본", 51, thstrm_amount=117012205000),
            row("BS", "ifrs-full_IssuedCapital", "자본금", 52, thstrm_amount=117012205000),
            row("BS", "ifrs-full_NoncontrollingInterests", "비지배지분", 57, thstrm_amount=804696342000),
            row("BS", NOSTD, "자본총계", 58, thstrm_amount=5225628071000)]
    assert Q.extract_controlling_eq(rows) == (5225628071000.0 - 804696342000, "TOTAL_MINUS_MINORITY_CAPLINE")
    # 다른 이름이어도 값이 자본금 계정과 같으면 납입자본 줄이다
    renamed = [{**r, "account_nm": "지배주주지분"} if r["account_nm"] == "납입자본" else r for r in rows]
    assert Q.extract_controlling_eq(renamed)[1] == "TOTAL_MINUS_MINORITY_CAPLINE"
    # 납입자본 줄밖에 없으면 비운다
    assert Q.extract_controlling_eq(rows[:2]) == (None, "CTRL_IS_CAPITAL_LINE")


def test_normal_controlling_row_is_unchanged():
    rows = [row("BS", "ifrs-full_EquityAttributableToOwnersOfParent", "지배기업의 소유주에게 귀속되는 자본", 1, thstrm_amount=1020108293657),
            row("BS", "ifrs-full_IssuedCapital", "자본금", 2, thstrm_amount=212231945000),
            row("BS", "ifrs-full_Equity", "자본총계", 3, thstrm_amount=1190474364067)]
    assert Q.extract_controlling_eq(rows) == (1020108293657.0, "BS_ID")


def test_noncontrolling_liability_is_not_noncontrolling_equity():
    """100790 2024 1분기 — 「비지배지분부채」(부채)를 비지배 자본으로 읽어 지배자본이 −3,895억이었다. 비지배 자본 줄이 없으니 자본총계 = 지배."""
    rows = [row("BS", "ifrs-full_Equity", "자본총계", 40, thstrm_amount=356277678271),
            row("BS", NOSTD, "비지배지분부채", 25, thstrm_amount=745816623091),
            row("BS", "ifrs-full_IssuedCapital", "자본금", 41, thstrm_amount=55027350000)]
    assert Q.extract_controlling_eq(rows) == (356277678271.0, "TOTAL")


def test_xbrl_controlling_equity_equal_to_issued_capital_is_not_used():
    ctx = _ctx_365330()
    data = _xbrl_zip([("EquityAttributableToOwnersOfParent", "C_I", 4967110000, "KRW"), ("IssuedCapital", "C_I", 4967110000, "KRW"),
                      ("Equity", "C_I", 246373997170, "KRW"), ("NoncontrollingInterests", "C_I", -8988087, "KRW")], ctx)
    assert Q.xbrl_controlling(data, "CFS", END, START)["eq"] == (246382985257.0, "XBRL_TOTAL_MINUS_MINORITY_CAPLINE", "KRW")
    ok = _xbrl_zip([("EquityAttributableToOwnersOfParent", "C_I", 90467706469, "KRW"), ("IssuedCapital", "C_I", 5000000000, "KRW")], ctx)
    assert Q.xbrl_controlling(ok, "CFS", END, START)["eq"] == (90467706469.0, "XBRL_ID", "KRW")


# ── 자본변동표 전기 말 자본: 옛 접두어 · 반올림 ─────────────────────────────────

def test_prior_equity_reads_old_taxonomy_statement_of_changes():
    """001080 2019 3분기(연결) — 자본변동표 기말 행이 `ifrs_Equity`. 종전엔 NO_SCE_ROW 로 2018 3분기 자본이 비었다."""
    rows = [row("BS", "ifrs_EquityAttributableToOwnersOfParent", "지배기업의 소유주에게 귀속되는 자본", 1, thstrm_amount=230222503641),
            row("BS", "ifrs_IssuedCapital", "자본금", 2, thstrm_amount=4150000000),
            row("SCE", "ifrs_Equity", "기말자본", 9, detail="자본 [member]|지배기업의 소유주에게 귀속되는 지분 [member]",
                thstrm_amount=230222503641, frmtrm_q_amount=230989540368),
            row("SCE", "ifrs_Equity", "기말자본", 9, detail="연결재무제표 [member]", thstrm_amount=230222503641, frmtrm_q_amount=230989540368),
            row("SCE", "ifrs_Equity", "기말자본", 9, detail="자본 [member]|비지배지분 [member]", thstrm_amount=0, frmtrm_q_amount=0)]
    assert Q.prior_equity_from_sce(rows) == (230989540368.0, "SCE_PRIOR")


def test_prior_equity_tolerates_one_won_rounding_between_statements():
    """016610 2024 1분기 — 재무상태표 지배자본 …657원 · 자본변동표 지배 몫 …656원."""
    rows = [row("BS", "ifrs-full_EquityAttributableToOwnersOfParent", "지배", 1, thstrm_amount=1020108293657),
            row("SCE", "ifrs-full_Equity", "자본총계", 9, detail=CTRL, thstrm_amount=1020108293656, frmtrm_q_amount=920156487446),
            row("SCE", "ifrs-full_Equity", "자본총계", 9, detail=CTRL + "|이익잉여금 [구성요소]", thstrm_amount=490211665865,
                frmtrm_q_amount=490695151354)]
    assert Q.prior_equity_from_sce(rows) == (920156487446.0, "SCE_PRIOR")
    # 같은 보고서의 두 재무제표가 실제로 다른 값이면(476040 2026 1분기 — 5.75억 차) 여전히 쓰지 않는다
    off = [rows[0], {**rows[1], "thstrm_amount": "1019533111879"}]
    assert Q.prior_equity_from_sce(off) == (None, "NO_SCE_ROW")


# ── 거름 규칙(순수 함수) ─────────────────────────────────────────────────────

def test_fill_mode_respects_reasons():
    head = "전기 칸 보충(261019) — … 순이익 CIS_ID(전기 누적) · 자본 "
    assert Q.prior_fill_mode("nodata", None, None, None, None, None) == "full"
    assert Q.prior_fill_mode("ok", 1.0, None, None, None, None) is None
    assert Q.prior_fill_mode("nodata", None, None, None, None, "다른 정정") is None             # 근거만 있어도(값을 뺀 칸) 손대지 않는다
    assert Q.prior_fill_mode("nodata", None, None, 5.0, None, head + "NO_SCE_ROW(자본변동표 전기 말)") == "eq"
    assert Q.prior_fill_mode("nodata", None, None, 5.0, None, head + "NO_SCE_ROW(자본변동표 전기 말) · 261019 D2: 스팩 … 정정 칸에서 뺌(…)") is None
    assert Q.prior_fill_mode("nodata", None, None, 5.0, None, head + "NO_SCE_ROW(…) · 자본 다시 읽음(261019): 자본 NO_SCE_ROW") is None
    assert Q.prior_fill_mode("nodata", None, None, 5.0, None, head + "SCE_AMBIGUOUS(자본변동표 전기 말)") is None


def _sce_full_year_482690():
    """482690 2025 1분기(별도) 원문 — 자본변동표 전기 칸이 2024 연간(기말 114.79억 = 재무상태표 전기 칸)."""
    return [row("BS", "ifrs-full_Equity", "자본총계", 4, thstrm_amount=11517738517, frmtrm_amount=11479361522),
            row("CIS", "ifrs-full_ProfitLoss", "당기순이익", 9, thstrm_amount=38376995, thstrm_add_amount=38376995,
                frmtrm_add_amount=-50067458),
            row("SCE", "ifrs-full_Equity", "자본총계", 5, detail="별도재무제표 [member]", thstrm_amount=11517738517,
                frmtrm_q_amount=11479361522, frmtrm_amount=11479361522)]


def test_full_year_prior_column_is_caught_in_report_and_by_annual_values():
    rows = _sce_full_year_482690()
    p_eq, case = Q.prior_equity_from_sce(rows)
    assert (p_eq, case) == (11479361522.0, "SCE_PRIOR")
    assert Q.prior_fy_end_equity(rows) == 11479361522.0
    assert "재무상태표 전기 칸" in Q.full_year_prior(None, p_eq, fy_end_eq=Q.prior_fy_end_equity(rows))
    assert "연간 순이익" in Q.full_year_prior(-50067458.0, None, annual_ni=[None, -50067458.0])
    assert "회계연도 말 자본" in Q.full_year_prior(None, 11479361522.4, annual_eq=[11479361522.0])
    # 보통 보고서(전년 같은 분기 말 ≠ 직전 사업연도 말)는 걸리지 않는다
    assert Q.full_year_prior(1508708000000.0, 54531173000000.0, fy_end_eq=56692684000000.0,
                             annual_ni=[4631932000000.0], annual_eq=[56929804000000.0]) is None


def test_listing_rule_drops_only_equity():
    ni, eq, notes = Q.apply_prior_rules(-50067458.0, 110000000.0, end=date(2024, 3, 31), ann_end=date(2024, 12, 31),
                                        listed="20241122", merge=None, src_merged=False, full_year=None)
    assert (ni, eq) == (-50067458.0, None)
    assert len(notes) == 1 and "상장(20241122) 전" in notes[0] and "자본 110000000원" in notes[0] and Q._PRIOR_DROP_MARK in notes[0]
    # 상장 뒤 기간·관측창 전 상장(listed=None)은 그대로
    assert Q.apply_prior_rules(1.0, 2.0, end=date(2024, 12, 31), ann_end=date(2024, 12, 31), listed="20241122", merge=None,
                               src_merged=False, full_year=None) == (1.0, 2.0, [])
    assert Q.apply_prior_rules(1.0, 2.0, end=date(2019, 3, 31), ann_end=date(2019, 12, 31), listed=None, merge=None,
                               src_merged=False, full_year=None) == (1.0, 2.0, [])


def test_full_year_rule_drops_both_with_values_in_the_note():
    ni, eq, notes = Q.apply_prior_rules(-50067458.0, 11479361522.0, end=date(2024, 3, 31), ann_end=date(2024, 12, 31), listed=None,
                                        merge=None, src_merged=False, full_year="자본이 …")
    assert (ni, eq) == (None, None) and "순이익 -50067458원 · 자본 11479361522원" in notes[0] and Q._PRIOR_DROP_MARK in notes[0]


def test_spac_rule():
    kw = dict(end=date(2020, 3, 31), listed=None, merge="20210312", full_year=None)
    # 합병 뒤 출처 · 그해 사업보고서도 합병 전(스팩) → 둘 다 뺀다
    ni, eq, notes = Q.apply_prior_rules(-440000000.0, 5.0e9, ann_end=date(2020, 12, 31), src_merged=True, **kw)
    assert (ni, eq) == (None, None) and "합병 주 20210312" in notes[0]
    # 그해 사업보고서가 합병 뒤(영업회사) → 순이익은 둔다(1년 뒤 TTM 전년 동기)
    ni, eq, notes = Q.apply_prior_rules(-440000000.0, 5.0e9, ann_end=date(2021, 12, 31), src_merged=True, **kw)
    assert (ni, eq) == (-440000000.0, None) and "순이익은 둠" in notes[0]
    # 출처도 합병 전(스팩 자기 비교 칸) → 그대로
    assert Q.apply_prior_rules(7033620.0, 7.86e9, ann_end=date(2020, 12, 31), src_merged=False, **kw) == (7033620.0, 7.86e9, [])
    # 기간 말이 합병 뒤 → 해당 없음
    assert Q.apply_prior_rules(1.0, 2.0, end=date(2021, 6, 30), ann_end=date(2021, 12, 31), listed=None, merge="20210312",
                               src_merged=True, full_year=None) == (1.0, 2.0, [])


def _weeks(n, close, shares, start=(2019, 5, 10)):
    from datetime import timedelta
    d0 = date(*start)
    return [((d0 + timedelta(days=7 * i)).strftime("%Y%m%d"), close(i), shares(i)) for i in range(n)]


def test_spac_merge_week():
    spac = _weeks(120, lambda i: 2050 if i < 90 else 9000, lambda i: 6_000_000 if i < 95 else 30_000_000)
    assert Q.spac_merge_week(spac, "26410") == (spac[95][0], 6_000_000)
    assert Q.spac_merge_week(spac, "66199") is None                         # 지금도 금융업(합병 안 한 스팩)
    assert Q.spac_merge_week(spac[:90], "26410") is None                    # 주식 수가 안 바뀜(아직 합병 전)
    plain = _weeks(120, lambda i: 15000, lambda i: 6_000_000 if i < 95 else 30_000_000)
    assert Q.spac_merge_week(plain, "26410") is None                        # 처음부터 스팩 가격대가 아님
    # 첫 주만 튀어도(323280 2,930원) 8주 가운데 6주가 띠 안이면 스팩
    jump = [(spac[0][0], 2930, 6_000_000)] + spac[1:]
    assert Q.spac_merge_week(jump, "26410") == (spac[95][0], 6_000_000)


def test_source_after_merger():
    pre = [row("SCE", "ifrs-full_Equity", "기말자본", 1, thstrm_amount=6971220554)]
    merged = pre + [row("SCE", "dart_x", "합병으로 인한 변동", 2, thstrm_amount=16561032777)]
    s0 = 4_020_000                                                          # × 2,000원 = 80.4억
    assert Q.source_after_merger(date(2020, 3, 31), "20200424", pre, 6971220554.0, s0) is False     # 267320 — 스팩 자기 보고서(0.87배)
    assert Q.source_after_merger(date(2020, 3, 31), "20200424", merged, 6971220554.0, s0) is True   # 합병 줄
    assert Q.source_after_merger(date(2020, 3, 31), "20200424", pre, 31771327633.0, s0) is True     # 스팩 규모 아님(323280 꼴)
    assert Q.source_after_merger(date(2020, 3, 31), "20200424", pre, None, s0) is True              # 모르면 쓰지 않는다
    assert Q.source_after_merger(date(2020, 6, 30), "20200424", pre, 6971220554.0, s0) is True      # 기간 말이 합병 주 뒤


def test_ecos_key_missing_note(monkeypatch):
    from open_proxy_mcp.dart.fx import ecos_key_missing_note
    monkeypatch.delenv("ECOS_API_KEY", raising=False)
    assert "ECOS_API_KEY" in ecos_key_missing_note("분기 재무 수집")
    monkeypatch.setenv("ECOS_API_KEY", "offline-test")
    assert ecos_key_missing_note("분기 재무 수집") is None


# ── 수집 통합(Postgres 17) ───────────────────────────────────────────────────

psycopg = pytest.importorskip("psycopg")

from tests.test_finstat_scale_guard_prior import FINSTAT_Q_DDL, FINSTAT_Y_DDL, _Today, _FakeDart, _q, kb_2024_q1  # noqa: E402

LISTING_DDL = ("CREATE TABLE krx_listing(ticker text PRIMARY KEY, market text, first_seen_dd text, last_seen_dd text, "
               "before_window boolean, window_lo text, built_at timestamptz)")
WEEKLY_DDL = ("CREATE TABLE krx_weekly(price_dd text, ticker text, market text, close double precision, mktcap double precision, "
              "list_shrs double precision, PRIMARY KEY(ticker, price_dd))")


def _ins_q(c, t, fy, q, why=None, ni_r=None, eq_r=None):
    c.execute("INSERT INTO dart_finstat_q (ticker, fy, quarter, reprt_code, fs, fetched, ni_case, eq_case, ni_cum_restated, eq_restated, "
              "restate_why) VALUES (%s, %s, %s, %s, 'OFS', 'nodata', 'NODATA', 'NODATA', %s, %s, %s)", (t, fy, q, Q.Q_TO_RC[q], ni_r, eq_r, why))


def _sce_next_q1(ni_prev, eq_cur, eq_prev, fy_end, old_prefix=False):
    eid = "ifrs_Equity" if old_prefix else "ifrs-full_Equity"
    return [row("BS", "ifrs-full_Equity", "자본총계", 4, thstrm_amount=eq_cur, frmtrm_amount=fy_end),
            row("CIS", "ifrs-full_ProfitLoss", "당기순이익", 9, thstrm_amount=1, thstrm_add_amount=1, frmtrm_add_amount=ni_prev),
            row("SCE", eid, "자본총계", 5, detail="별도재무제표 [member]", thstrm_amount=eq_cur, frmtrm_q_amount=eq_prev)]


@pytest.fixture
def rdb(pg17, monkeypatch, tmp_path):
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(FINSTAT_Q_DDL)
        c.execute(FINSTAT_Y_DDL)
        c.execute(Q.DDL_FUNDAMENTALS)
        c.execute("ALTER TABLE dart_fundamentals ADD COLUMN induty text")
        c.execute(LISTING_DDL)
        c.execute(WEEKLY_DDL)
        firms = {"FY": ("0000000F", "20151230", True), "IPO": ("0000000I", "20241122", False),
                 "OLD": ("0000000O", "20151230", True), "SP": ("0000000S", "20190510", False),
                 "MARK": ("0000000M", "20151230", True)}
        for t, (cc, first, bw) in firms.items():
            c.execute("INSERT INTO dart_fundamentals (ticker, corp_code, fetched, induty) VALUES (%s, %s, 'ok', '26410')", (t, cc))
            c.execute("INSERT INTO krx_listing (ticker, market, first_seen_dd, last_seen_dd, before_window, window_lo) "
                      "VALUES (%s, 'KQ', %s, '20261016', %s, '20151230')", (t, first, bw))
        # DB 주간 시세는 하한(20231016) 이후만 — 스팩(SP)의 2019 상장 주는 콜드 보관본에만 있다
        c.execute("INSERT INTO krx_weekly VALUES ('20261016', 'OLD', 'KQ', 1000, 1, 1)")
        # FY: 전년 연간 태깅(2025 1분기 보고서의 전기 칸 = 2024 연간), 연간 표에도 2024 순이익이 있다
        c.execute("INSERT INTO dart_finstat_y (ticker, fy, fs, ni, eq, fetched) VALUES ('FY', 2024, 'OFS', -50067458, 11479361522, 'ok')")
        for t, fy, q in (("FY", 2024, 1), ("IPO", 2024, 1), ("OLD", 2019, 3), ("SP", 2020, 1), ("MARK", 2023, 1)):
            _ins_q(c, t, fy, q)
        c.execute("UPDATE dart_finstat_q SET restate_why='전기 칸 보충(261019) — … · 261019 D2: 자본 1원은 … 정정 칸에서 뺌(…)' "
                  "WHERE ticker='MARK'")
        # OLD: 자본만 못 읽어(옛 접두어) 순이익만 채운 칸
        c.execute("UPDATE dart_finstat_q SET ni_cum_restated=700, restate_why='전기 칸 보충(261019) — DART … 다음 해 같은 분기 보고서"
                  "(2020 11014 CFS, 접수 1)의 전년 동기 값: 순이익 TOTAL(전기 누적) · 자본 NO_SCE_ROW(자본변동표 전기 말)' WHERE ticker='OLD'")
    fake = _FakeDart({("0000000F", "2025", "11013", "CFS"): _sce_full_year_482690(),
                      ("0000000I", "2025", "11013", "CFS"): _sce_next_q1(-3000, 9_000_000_000, 110_000_000, 8_000_000_000),
                      ("0000000O", "2020", "11014", "CFS"): _sce_next_q1(700, 230_222_503_641, 230_989_540_368, 229_000_000_000,
                                                                       old_prefix=True),
                      ("0000000S", "2021", "11013", "CFS"): _sce_next_q1(-440_000_000, 30_000_000_000, 5_000_000_000, 28_000_000_000),
                      ("0000000M", "2024", "11013", "CFS"): kb_2024_q1()})
    cold = tmp_path / "cold.sqlite"
    lite = sqlite3.connect(cold)
    lite.execute("CREATE TABLE krx_wk_hist (ticker TEXT, price_dd TEXT, market TEXT, close INTEGER, list_shrs INTEGER)")
    lite.execute("CREATE VIEW krx_weekly AS SELECT price_dd, ticker, market, close, close * list_shrs AS mktcap, list_shrs FROM krx_wk_hist")
    for dd, cl, sh in _weeks(240, lambda i: 2050 if i < 90 else 9000, lambda i: 6_000_000 if i < 95 else 30_000_000):   # ~20231013 까지
        if dd < "20231016":
            lite.execute("INSERT INTO krx_wk_hist VALUES ('SP', ?, 'KQ', ?, ?)", (dd, cl, sh))
    lite.commit(); lite.close()
    monkeypatch.setenv("DATABASE_URL", pg17)
    monkeypatch.setattr(Q, "SLEEP", 0.0)
    monkeypatch.setattr(Q, "date", _Today)
    monkeypatch.setattr("open_proxy_mcp.dart.client.get_dart_client", lambda: fake)
    return pg17, fake, str(cold)


def _cells(url):
    return {r[0]: r[1:] for r in _q(url, "SELECT ticker, ni_cum_restated, eq_restated, restate_why FROM dart_finstat_q ORDER BY 1")}


KEYS = {("FY", 2024, 1), ("IPO", 2024, 1), ("OLD", 2019, 3), ("SP", 2020, 1), ("MARK", 2023, 1)}


def test_prior_rules_end_to_end(rdb):
    url, fake, cold = rdb
    # 콜드 보관본이 없으면 스팩 판정을 못 하는 칸(상장 2019 — 하한 이전)은 부르지도 쓰지도 않는다
    res = asyncio.run(Q.fill_prior_from_next(KEYS))
    assert "--cold-sqlite" in res[("SP", 2020, 1)] and ("0000000S", "2021", "11013", "CFS") not in fake.calls
    assert res[("MARK", 2023, 1)].startswith("건너뜀(근거 있음") and ("0000000M", "2024", "11013", "CFS") not in fake.calls
    got = _cells(url)
    # ① 전년 연간 태깅 → 값은 비우고 근거만(뺀 값 원 단위)
    ni, eq, why = got["FY"]
    assert (ni, eq) == (None, None) and Q._PRIOR_DROP_MARK in why and "11479361522원" in why and why.startswith(Q._PRIOR_WHY_HEAD)
    # ② 상장 전 → 순이익만
    ni, eq, why = got["IPO"]
    assert (ni, eq) == (-3000.0, None) and "상장(20241122) 전" in why and "자본 110000000원" in why
    # 옛 접두어로 비었던 자본만 다시 읽는다 — 순이익·앞 근거는 그대로, 끝에 덧붙임
    ni, eq, why = got["OLD"]
    assert (ni, eq) == (700.0, 230989540368.0) and Q._PRIOR_EQ_REREAD in why and "자본 NO_SCE_ROW(자본변동표 전기 말)" in why
    assert got["MARK"][:2] == (None, None)
    # ③ 콜드 보관본을 주면 스팩 합병(2021-03 신주 상장) 전 칸 — 출처(2021 1분기)는 스팩 규모가 아니라 합병 뒤 → 둘 다 뺀다
    res = asyncio.run(Q.fill_prior_from_next({("SP", 2020, 1)}, cold_sqlite=cold))
    ni, eq, why = _cells(url)["SP"]
    assert (ni, eq) == (None, None) and "스팩 합병 전 기간" in why and Q._PRIOR_DROP_MARK in why
    # 다시 돌려도 근거가 있는 칸은 부르지 않는다(손으로·규칙으로 뺀 칸이 다시 채워지지 않는다)
    n = len(fake.calls)
    res = asyncio.run(Q.fill_prior_from_next(KEYS, cold_sqlite=cold))
    assert len(fake.calls) == n
    assert all(v.startswith("건너뜀(") for v in res.values()), res
    assert _cells(url) == {**got, "SP": (None, None, why)}


def test_prior_rules_dry_writes_nothing(rdb):
    url, fake, cold = rdb
    before = _q(url, "SELECT * FROM dart_finstat_q ORDER BY 1, 2, 3")
    res = asyncio.run(Q.fill_prior_from_next(KEYS, dry=True, cold_sqlite=cold))
    assert res[("FY", 2024, 1)].startswith("근거만 적음(값은 거름)")
    assert _q(url, "SELECT * FROM dart_finstat_q ORDER BY 1, 2, 3") == before


# ── 연간 수집: 납입자본 줄 규칙 · --refetch ────────────────────────────────────

def test_annual_refetch_fixes_capital_line_equity(pg17, monkeypatch):
    from scripts import market_val_series as S
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(FINSTAT_Y_DDL)
        c.execute(Q.DDL_FUNDAMENTALS)
        c.execute("INSERT INTO dart_fundamentals (ticker, corp_code, fetched) VALUES ('065680', '00390903', 'ok'), ('069960', '00428251', 'ok')")
        c.execute("INSERT INTO dart_finstat_y (ticker, fy, fs, ni, eq, fetched, ni_restated, eq_restated) VALUES "
                  "('065680', 2024, 'CFS', 19885390796, 4967110000, 'ok', NULL, NULL), "
                  "('069960', 2021, 'CFS', 189377183000, 117012205000, 'ok', NULL, NULL)")
    fake = _FakeDart({("00390903", "2024", "11011", "CFS"): [
        row("BS", "ifrs-full_Equity", "자본총계", 29, thstrm_amount=246373997170),
        row("BS", "ifrs-full_EquityAttributableToOwnersOfParent", "자본금", 30, thstrm_amount=4967110000),
        row("BS", "ifrs-full_NoncontrollingInterests", "비지배지분", 31, thstrm_amount=-8988087),
        row("CIS", "ifrs-full_ProfitLossAttributableToOwnersOfParent", "지배", 5, thstrm_amount=19885390796)]})
    monkeypatch.setenv("DATABASE_URL", pg17)
    monkeypatch.setenv("SERIES_SLEEP", "0")
    monkeypatch.setattr("open_proxy_mcp.dart.client.get_dart_client", lambda: fake)
    asyncio.run(S.fetch(only={("065680", 2024), ("069960", 2021)}))
    assert _q(pg17, "SELECT ticker, fy, ni, eq, fetched FROM dart_finstat_y ORDER BY 1") == [
        ("065680", 2024, 19885390796.0, 246382985257.0, "ok"),          # 자본금 줄 대신 자본총계 − 비지배
        ("069960", 2021, 189377183000.0, 117012205000.0, "ok")]         # 받지 못한(013 → nodata) 행은 있던 ok 를 덮지 않는다
    # 같은 규칙은 다음 해 보고서의 정정치에도
    v, why = S.ctrl_eq([row("BS", "ifrs-full_Equity", "자본총계", 29, frmtrm_amount=223924851066),
                        row("BS", "ifrs-full_EquityAttributableToOwnersOfParent", "자본금", 30, frmtrm_amount=4967110000),
                        row("BS", "ifrs-full_NoncontrollingInterests", "비지배지분", 31, frmtrm_amount=-78415589)], "frmtrm_amount")
    assert v == 223924851066.0 + 78415589 and "납입자본 줄" in why
