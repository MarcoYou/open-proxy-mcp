# -*- coding: utf-8 -*-
"""지배자본 줄 고르기 한 규칙(`open_proxy_mcp/services/controlling_eq.py`) — 분기·연간 수집 · 서버 실시간 firm · 결산기 칸 (261020).

원문(DART 전체 재무제표 응답) 모양 그대로 옮긴 경우들이다. 회사가 지배자본 계정(`…EquityAttributableToOwnersOfParent`)을 엉뚱한 줄에 붙였거나,
이름 규칙이 자본이 아닌 줄을 고르던 것:
  · 「기타자본구성요소」 줄에 지배자본 계정 — 082660 2021 3분기
  · 이름은 지배자본인데 값이 자본금(자본금 계정 줄이 따로 없다) — 065370 2023 3분기
  · 자본금 + 결손금만 더한 소계 — 060230 FY2018 · 결손금 줄 값 — 203690 2024 반기
  · 이름 규칙이 「관계기업투자주식및공동지배투자주식」(자산)을 고름 — 016670 2025 반기 · 「지배지분손익」(결손금 값) — 082800 2019 1분기
그리고 맞는 줄은 그대로 두는 꼴(큰 음수 비지배 224060 FY2025 · 비지배 계정 없이 비지배를 둔 143240 · 부호를 바꿔 적은 비지배 145720).
연간 수집은 지배자본 계정 줄이 없으면 비지배를 품은 자본총계를 쓰던 것을 분기와 같은 규칙으로(112040 FY2023 · 000100 FY2025). DART 0콜."""
from __future__ import annotations

from datetime import date

from open_proxy_mcp.services import controlling_eq as CEQ
from open_proxy_mcp.services import period_cells as PC
from open_proxy_mcp.services.price_multiple_data import _ctrl_equity
from scripts import market_fund_quarterly as Q
from scripts import market_val_series as S
from tests.test_finstat_q_parser_gaps import _xbrl_zip, _ctx_365330, END, START

NOSTD = "-표준계정코드 미사용-"
ID = "ifrs-full_EquityAttributableToOwnersOfParent"


def row(sj, aid, nm, ordv, **amt):
    return {"sj_div": sj, "account_id": aid, "account_nm": nm, "ord": str(ordv), "currency": "KRW", "rcept_no": "20211115000000",
            **{k: ("" if v is None else str(v)) for k, v in amt.items()}}


def _082660_q3():
    """082660 2021 3분기(연결) — 「기타자본구성요소」 줄에 지배자본 계정. 자본총계 257.05억 · 비지배 0.15억 → 지배자본 256.90억."""
    return [row("BS", NOSTD, "자본금", 40, thstrm_amount=23790000000, frmtrm_amount=23790000000),
            row("BS", ID, "기타자본구성요소", 42, thstrm_amount=7555126480, frmtrm_amount=7538107760),
            row("BS", "ifrs-full_NoncontrollingInterests", "비지배지분", 44, thstrm_amount=15076000, frmtrm_amount=18420000),
            row("BS", "ifrs-full_Equity", "자본총계", 45, thstrm_amount=25705465130, frmtrm_amount=19001376960),
            row("BS", "ifrs-full_EquityAndLiabilities", "자본과부채총계", 46, thstrm_amount=57417384160)]


def _065370_q3():
    """065370 2023 3분기(연결) — 이름은 지배자본, 값은 자본금(36.92억 = 자본총계 323.72억 − 다른 구성요소 합 286.80억). 비지배 없음, 자본금 계정 줄 없음."""
    return [row("BS", NOSTD, "기타자본항목", 26, thstrm_amount=-5702000000),
            row("BS", NOSTD, "기타포괄손익누계액", 27, thstrm_amount=38000000),
            row("BS", NOSTD, "이익잉여금", 28, thstrm_amount=14794000000),
            row("BS", NOSTD, "자본잉여금", 29, thstrm_amount=19550000000),
            row("BS", "ifrs-full_Equity", "자본총계", 30, thstrm_amount=32372000000),
            row("BS", ID, "지배기업의 소유주에게 귀속되는 자본", 31, thstrm_amount=3692000000),
            row("BS", "ifrs-full_EquityAndLiabilities", "부채와자본총계", 32, thstrm_amount=49536000000)]


def _060230_fy2018():
    """060230 FY2018(연결) — 「지배기업의소유주에게귀속되는자본」 −66.02억 = 자본금 253.61억 + 결손금 −319.64억(주식발행초과금 515.98억 등 빠짐). 비지배 0."""
    return [row("BS", ID, "지배기업의 소유주에게 귀속되는 자본", 28, thstrm_amount=-660204174),
            row("BS", "ifrs-full_IssuedCapital", "자본금", 29, thstrm_amount=25361000000),
            row("BS", "ifrs-full_RetainedEarnings", "이익잉여금(결손금)", 30, thstrm_amount=-31964000000),
            row("BS", NOSTD, "주식발행초과금", 31, thstrm_amount=51598000000),
            row("BS", "ifrs-full_NoncontrollingInterests", "비지배지분", 34, thstrm_amount=0),
            row("BS", "ifrs-full_Equity", "자본총계", 35, thstrm_amount=32448000000)]


# ── 지배자본 계정 줄을 믿지 않는 꼴 ────────────────────────────────────────────

def test_component_line_tagged_as_controlling_equity():
    v, case = Q.extract_controlling_eq(_082660_q3())
    assert (v, case) == (25705465130.0 - 15076000, "TOTAL_MINUS_MINORITY_COMPLINE")
    assert CEQ.controlling_eq(_082660_q3(), "frmtrm_amount") == (19001376960.0 - 18420000, "TOTAL_MINUS_MINORITY_COMPLINE")


def test_controlling_name_with_capital_value_is_off_total():
    assert Q.extract_controlling_eq(_065370_q3()) == (32372000000.0, "TOTAL_OFFTOTAL")


def test_partial_subtotal_is_off_total():
    assert Q.extract_controlling_eq(_060230_fy2018()) == (32448000000.0, "TOTAL_MINUS_MINORITY_OFFTOTAL")
    v, why = S.ctrl_eq(_060230_fy2018())
    assert v == 32448000000.0 and "자본총계 − 비지배와 크게 어긋난 줄" in why


def test_retained_earnings_value_under_controlling_name():
    """203690 2024 반기 — 지배자본 계정 줄 값 = 이익잉여금 줄(−284.32억) · 자본총계 303.70억 · 비지배 없음. 종전엔 완전자본잠식으로 읽었다."""
    rows = [row("BS", "ifrs-full_Equity", "자본총계", 49, thstrm_amount=30370000000),
            row("BS", "ifrs-full_IssuedCapitalOrdinaryShares", "I.납입자본", 58, thstrm_amount=39450000000),
            row("BS", "ifrs-full_RetainedEarnings", "III.이익잉여금", 61, thstrm_amount=-28432000000),
            row("BS", ID, "지배기업의소유주에게귀속되는자본", 63, thstrm_amount=-28432000000)]
    assert Q.extract_controlling_eq(rows) == (30370000000.0, "TOTAL_OFFTOTAL")


def test_capital_line_rule_is_unchanged():
    """261019 규칙 그대로 — 자본금 줄은 `_CAPLINE`(이름 줄이 있으면 그 줄)."""
    rows = [row("BS", NOSTD, "지배기업지분", 29, thstrm_amount=98184205985),
            row("BS", "ifrs_EquityAttributableToOwnersOfParent", "자본금", 30, thstrm_amount=4898400000),
            row("BS", "ifrs_Equity", "자본총계", 35, thstrm_amount=98184205985)]
    assert Q.extract_controlling_eq(rows) == (98184205985.0, "BS_NM_CAPLINE")
    assert CEQ.controlling_eq(rows[1:2]) == (None, "CTRL_IS_CAPITAL_LINE")
    assert CEQ.controlling_eq([rows[1]] + [row("BS", ID, "기타자본구성요소", 31, thstrm_amount=5)]) == (None, "CTRL_IS_CAPITAL_LINE")
    assert CEQ.controlling_eq([row("BS", ID, "기타자본구성요소", 31, thstrm_amount=5)]) == (None, "CTRL_LINE_REJECTED")


# ── 맞는 줄은 그대로 ────────────────────────────────────────────────────────

def test_large_negative_minority_keeps_controlling_line():
    """224060 FY2025 — 지배자본 1,186.94억 · 비지배 −1,079.42억 · 자본총계 107.52억(지배 + 비지배 = 총계 그대로)."""
    rows = [row("BS", "ifrs-full_Equity", "자본총계", 25, thstrm_amount=10752000000),
            row("BS", ID, "지배기업의소유주에게귀속되는자본", 26, thstrm_amount=118694000000),
            row("BS", "ifrs-full_NoncontrollingInterests", "비지배지분", 32, thstrm_amount=-107942000000)]
    assert Q.extract_controlling_eq(rows) == (118694000000.0, "BS_ID")
    assert S.ctrl_eq(rows) == (118694000000.0, None)
    assert _ctrl_equity(rows) == 118694000000


def test_small_identity_gaps_keep_controlling_line():
    """143240 — 비지배 계정 없이 자본총계와 1억 안팎 차이 · 145720 — 비지배(11.61억)를 부호 바꿔 적어 지배 + 비지배 ≠ 총계. 둘 다 계정 줄이 맞다."""
    a = [row("BS", ID, "지배기업소유주지분", 1, thstrm_amount=151733000000), row("BS", "ifrs-full_Equity", "자본총계", 2, thstrm_amount=151828000000)]
    b = [row("BS", "ifrs-full_Equity", "자본총계", 30, thstrm_amount=553506000000),
         row("BS", ID, "지배기업소유주지분", 31, thstrm_amount=554666000000),
         row("BS", "ifrs-full_NoncontrollingInterests", "비지배지분", 36, thstrm_amount=1161000000)]
    assert Q.extract_controlling_eq(a) == (151733000000.0, "BS_ID")
    assert Q.extract_controlling_eq(b) == (554666000000.0, "BS_ID")


# ── 이름 규칙(BS_NM) ───────────────────────────────────────────────────────

def test_name_rule_skips_joint_venture_investment_asset():
    """016670 2025 반기(별도) — 지배자본 계정 줄 없음. 「관계기업투자주식및공동지배투자주식」(자산 177.45억)이 이름 규칙에 먼저 걸렸다."""
    rows = [row("BS", "ifrs-full_InvestmentsInAssociates", "관계기업투자주식및공동지배투자주식", 20, thstrm_amount=17745000000),
            row("BS", NOSTD, "지배기업소유지분", 30, thstrm_amount=68552000000),
            row("BS", "ifrs-full_Equity", "자본총계", 31, thstrm_amount=68552000000)]
    assert Q.extract_controlling_eq(rows) == (68552000000.0, "BS_NM")


def test_name_rule_skips_profit_and_not_attributable_lines():
    """082800 2019 1분기 — 「지배지분손익」 −778.49억(결손금 줄 값) · 자본총계 677.67억. 「…귀속되지않는자본」(비지배)도 고르지 않는다."""
    rows = [row("BS", NOSTD, "이익잉여금(결손금)", 33, thstrm_amount=-77849000000),
            row("BS", NOSTD, "지배지분손익", 34, thstrm_amount=-77849000000),
            row("BS", "ifrs_Equity", "자본총계", 36, thstrm_amount=67767000000)]
    assert Q.extract_controlling_eq(rows) == (67767000000.0, "TOTAL")
    nci = [row("BS", NOSTD, "지배기업의 소유주에게 귀속되지 않는 자본", 10, thstrm_amount=5),
           row("BS", "ifrs-full_Equity", "자본총계", 11, thstrm_amount=100)]
    assert Q.extract_controlling_eq(nci) == (100.0, "TOTAL")


# ── 연간 수집: 지배자본 계정 줄이 없을 때 분기와 같은 규칙 ─────────────────────────

def test_annual_without_controlling_line_excludes_minority():
    """112040 FY2023(연결) — 지배자본 계정 줄 없음, 「지배기업 소유주지분」(표준 코드 없음) 2,487.83억 · 자본총계(계정) 4,007.78억 = 비지배 포함."""
    rows = [row("BS", NOSTD, "지배기업 소유주지분", 30, thstrm_amount=248783000000),
            row("BS", NOSTD, "비지배지분", 38, thstrm_amount=151995000000),
            row("BS", "ifrs-full_Equity", "자본총계", 39, thstrm_amount=400778000000)]
    v, why = S.ctrl_eq(rows)
    assert v == 248783000000.0 and "BS_NM" in why and "비지배 포함" in why
    assert Q.extract_controlling_eq(rows) == (248783000000.0, "BS_NM")          # 분기 수집과 같은 값
    # 000100 꼴 — 이름 줄도 없고 비지배 계정 줄만: 자본총계 − 비지배
    yh = [row("BS", "ifrs-full_Equity", "자본총계", 1, thstrm_amount=2362284000000),
          row("BS", "ifrs-full_NoncontrollingInterests", "비지배지분", 2, thstrm_amount=55441000000)]
    assert S.ctrl_eq(yh)[0] == 2362284000000.0 - 55441000000
    # 비지배가 없으면 종전과 같은 값(자본총계)이고 까닭도 적지 않는다
    assert S.ctrl_eq([row("BS", "ifrs-full_Equity", "자본총계", 1, thstrm_amount=5)]) == (5.0, None)
    assert S.ctrl_eq([row("IS", "ifrs-full_Revenue", "매출액", 1, thstrm_amount=5)]) == (None, None)


def test_annual_component_line_note():
    v, why = S.ctrl_eq(_082660_q3())
    assert v == 25705465130.0 - 15076000 and "자본 구성요소 줄" in why


def test_annual_dash_valued_controlling_line_falls_through():
    """지배자본 계정 줄 값이 「-」(빈 표기)면 계정 줄이 없는 것으로 — 분기 수집과 같은 값. 그 줄에서 멈추면 그 해가 빈다(main 은 자본총계로 넘어갔다)."""
    rows = [row("BS", ID, "지배기업의 소유주에게 귀속되는 자본", 1, thstrm_amount="-"),
            row("BS", "ifrs-full_NoncontrollingInterests", "비지배지분", 2, thstrm_amount=1000000),
            row("BS", "ifrs-full_Equity", "자본총계", 3, thstrm_amount=10000000)]
    v, why = S.ctrl_eq(rows)
    assert v == 9000000.0 and "TOTAL_MINUS_MINORITY" in why
    assert Q.extract_controlling_eq(rows) == (9000000.0, "TOTAL_MINUS_MINORITY")


# ── 서버 실시간 firm · 결산기 칸 ──────────────────────────────────────────────

def test_firm_and_period_cells_reject_the_same_lines():
    """317240 2021 1분기 — 「자본금」 줄에 지배자본 계정(90.35억) · 자본총계는 표준 코드 없이 이름으로(200.67억). 종전 firm 은 90.35억을 PBR 분모로 썼다."""
    rows = [row("BS", ID, "자본금", 31, thstrm_amount=9035000000),
            row("BS", NOSTD, "자본총계", 35, thstrm_amount=20067000000),
            row("BS", "ifrs-full_EquityAndLiabilities", "부채및자본총계", 36, thstrm_amount=41292000000)]
    assert _ctrl_equity(rows) == 20067000000
    assert PC.ctrl_eq(rows) == 20067000000.0
    assert _ctrl_equity(_082660_q3()) == 25705465130 - 15076000
    assert _ctrl_equity(_065370_q3()) == 32372000000
    assert PC.ctrl_eq(_060230_fy2018()) == 32448000000.0
    # 맞는 줄은 그대로(계정 줄 값)
    ok = [row("BS", ID, "지배기업의 소유주에게 귀속되는 자본", 1, thstrm_amount=1020108293657),
          row("BS", "ifrs-full_IssuedCapital", "자본금", 2, thstrm_amount=212231945000),
          row("BS", "ifrs-full_Equity", "자본총계", 3, thstrm_amount=1190474364067)]
    assert _ctrl_equity(ok) == 1020108293657 and PC.ctrl_eq(ok) == 1020108293657.0


def test_firm_reads_nonstandard_total_line_like_collector():
    """082270 FY2025(연결) — 자본총계를 표준 코드 없이 「자 본 총 계」로만 적고 계정 코드는 부채및자본총계(`EquityAndLiabilities`)에만.
    지배자본 계정 줄·`ifrs-full_Equity` 가 없어 종전 firm·결산기 칸은 None(PBR 이 경고 없이 N/M) — 같은 원문을 읽는 수집은 572.29억(TOTAL)."""
    rows = [row("BS", NOSTD, "자 본 총 계", 26, thstrm_amount=57228866333, frmtrm_amount=29005833962),
            row("BS", NOSTD, "자본잉여금", 28, thstrm_amount=114082920018),
            row("BS", NOSTD, "자본금", 29, thstrm_amount=21340194500),
            row("BS", NOSTD, "이익잉여금(결손금)", 30, thstrm_amount=-78423241818),
            row("BS", NOSTD, "기타자본구성요소", 31, thstrm_amount=228993633),
            row("BS", "ifrs-full_EquityAndLiabilities", "부 채 및 자 본 총 계", 32, thstrm_amount=158874710036),
            row("BS", "ifrs-full_Liabilities", "부채총계", 44, thstrm_amount=101645843703)]
    assert Q.extract_controlling_eq(rows) == (57228866333.0, "TOTAL")
    assert _ctrl_equity(rows) == 57228866333
    assert _ctrl_equity(rows, "frmtrm_amount") == 29005833962
    assert PC.ctrl_eq(rows) == 57228866333.0
    # 재무상태표가 없으면 종전처럼 None
    assert _ctrl_equity([row("IS", "ifrs-full_Revenue", "매출액", 1, thstrm_amount=5)]) is None
    assert PC.ctrl_eq([row("IS", "ifrs-full_Revenue", "매출액", 1, thstrm_amount=5)]) is None


def test_firm_does_not_count_nonstandard_minority_as_controlling():
    """자본총계는 계정 코드(`ifrs-full_Equity`)로, 지배·비지배는 표준 코드 없는 이름 줄로만 적은 보고서 — firm·결산기 칸이 따로 「자본총계 계정 −
    비지배 계정」을 세면 비지배 계정이 없어 비지배를 품은 자본총계가 지배자본이 됐다. 수집(이름 줄 → 자본총계 − 비지배)과 같은 값이어야 한다."""
    # 077360 2024 3분기(연결) — 지배기업소유주지분 3,067.77억 · 비지배지분 1,300.45억 · 자본총계 4,368.22억(종전 firm 은 4,368.22억)
    a = [row("BS", NOSTD, "지분법적용 투자지분", 21, thstrm_amount=185464806188),
         row("BS", NOSTD, "비지배지분", 29, thstrm_amount=130045132756, frmtrm_amount=132197075187),
         row("BS", NOSTD, "지배기업소유주지분", 30, thstrm_amount=306776704076, frmtrm_amount=289392159384),
         row("BS", NOSTD, "자본금", 33, thstrm_amount=9087400400, frmtrm_amount=9087400400),
         row("BS", NOSTD, "기타자본구성요소", 34, thstrm_amount=-65357478562, frmtrm_amount=-65357478562),
         row("BS", "ifrs-full_Equity", "자본총계", 36, thstrm_amount=436821836832, frmtrm_amount=421589234571),
         row("BS", "ifrs-full_EquityAndLiabilities", "자본과부채총계", 37, thstrm_amount=777008327790)]
    assert Q.extract_controlling_eq(a) == (306776704076.0, "BS_NM") and S.ctrl_eq(a)[0] == 306776704076.0
    assert _ctrl_equity(a) == 306776704076 and _ctrl_equity(a, "frmtrm_amount") == 289392159384
    assert PC.ctrl_eq(a) == 306776704076.0 and PC.ctrl_eq(a, "frmtrm_amount") == 289392159384.0
    # 307280 2023 3분기(연결) — 지배 줄 없음, 비지배는 이름 줄(「비지배지분.」)뿐 · 전기 칸 비지배는 음수
    b = [row("BS", "ifrs-full_Equity", "자본총계", 29, thstrm_amount=30990165758, frmtrm_amount=26825582923),
         row("BS", NOSTD, "기타자본항목", 31, thstrm_amount=-2224257483, frmtrm_amount=-2202022483),
         row("BS", NOSTD, "비지배지분.", 32, thstrm_amount=223760406, frmtrm_amount=-41735763),
         row("BS", "ifrs-full_IssuedCapital", "자본금", 33, thstrm_amount=3650521000, frmtrm_amount=3645440800),
         row("BS", "ifrs-full_EquityAndLiabilities", "자본과부채총계", 36, thstrm_amount=41185962040)]
    assert Q.extract_controlling_eq(b) == (30766405352.0, "TOTAL_MINUS_MINORITY")
    assert _ctrl_equity(b) == 30766405352 and _ctrl_equity(b, "frmtrm_amount") == 26867318686
    assert PC.ctrl_eq(b) == 30766405352.0 and PC.ctrl_eq(b, "frmtrm_amount") == 26867318686.0
    # 비지배를 계정 코드로 적은 보고서는 종전과 같다(자본총계 − 비지배 계정)
    c = [row("BS", "ifrs-full_Equity", "자본총계", 1, thstrm_amount=1000), row("BS", "ifrs-full_NoncontrollingInterests", "비지배지분", 2, thstrm_amount=100)]
    assert _ctrl_equity(c) == 900 and PC.ctrl_eq(c) == 900.0


# ── XBRL 원본 대체 경로 ──────────────────────────────────────────────────────

def test_xbrl_controlling_element_far_from_total_is_not_used():
    ctx = _ctx_365330()
    bad = _xbrl_zip([("EquityAttributableToOwnersOfParent", "C_I", 3692000000, "KRW"), ("Equity", "C_I", 32372000000, "KRW")], ctx)
    assert Q.xbrl_controlling(bad, "CFS", END, START)["eq"] == (32372000000.0, "XBRL_TOTAL_OFFTOTAL", "KRW")
    ok = _xbrl_zip([("EquityAttributableToOwnersOfParent", "C_I", 90467706469, "KRW"), ("Equity", "C_I", 90832000000, "KRW"),
                    ("NoncontrollingInterests", "C_I", 364293531, "KRW")], ctx)
    assert Q.xbrl_controlling(ok, "CFS", END, START)["eq"] == (90467706469.0, "XBRL_ID", "KRW")
    neg = _xbrl_zip([("EquityAttributableToOwnersOfParent", "C_I", 118694000000, "KRW"), ("Equity", "C_I", 10752000000, "KRW"),
                     ("NoncontrollingInterests", "C_I", -107942000000, "KRW")], ctx)
    assert Q.xbrl_controlling(neg, "CFS", END, START)["eq"] == (118694000000.0, "XBRL_ID", "KRW")
    assert END == date(2026, 6, 30)
