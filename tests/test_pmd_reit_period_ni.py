# -*- coding: utf-8 -*-
"""반기·분기 결산 회사(리츠)의 순이익 — 12개월 결산기 합(배당의 12개월 합과 같은 원리). network 0 · DB 0. (261019 p1d)

종전 실시간 firm 의 FY0 는 사업보고서 하나의 당기 = **한 결산기(3·6개월)** 순이익이라 PER(FY0) 이 2~4배로 부풀었다
(10-19 운영 SK리츠 FY0 = 2025.10~12 석 달 323.6억 → PER 49.39, 롯데리츠 = 2025.07~12 여섯 달 → 60.07).
반기 결산의 「반기 누적」 TTM 은 6개월 결산기 + 3개월 시점 보고서 − 3개월 시점 보고서라 12개월이 아니었다(롯데리츠 = 2025.10~2026.03 → 57.54).

값은 DART 원값(10-19 배치 키 직접 조회 — fnlttSinglAcntAll·alotMatter): 롯데리츠(1~6·7~12월, 별도) · SK리츠(분기, 연결 지배).
"""
from __future__ import annotations

import asyncio

import pytest

from open_proxy_mcp.services import price_multiple_data as S
from open_proxy_mcp.tools import price_multiple_data as TP
from tests.test_pmd_reit_fy0_year import _interim, _run


def _report(rcept: str, terms: tuple[int, int, int], ni: tuple[str, str, str], eps: tuple[str, str, str],
            eq: str = "1,127,725,590,182") -> list[dict]:
    """사업보고서 전체 재무제표 행(파싱 전 모양) — 당기·전기·전전기 칸(기수 이름 「제 N 기」)과 접수번호."""
    nm = {f: f"제 {t} 기" for f, t in zip(("thstrm_nm", "frmtrm_nm", "bfefrmtrm_nm"), terms)}
    def row(sj, aid, vals):
        r = {"rcept_no": rcept, "sj_div": sj, "account_id": aid, **nm}
        r.update(zip(("thstrm_amount", "frmtrm_amount", "bfefrmtrm_amount"), vals))
        return r
    return [row("BS", "ifrs-full_Assets", ("1,900,000,000,000",) * 3) | {"currency": "KRW"},
            row("BS", "ifrs-full_Liabilities", ("800,000,000,000",) * 3),
            row("BS", "ifrs-full_Equity", (eq,) * 3),
            row("BS", "ifrs-full_EquityAttributableToOwnersOfParent", (eq,) * 3),
            row("CIS", "ifrs-full_ProfitLossAttributableToOwnersOfParent", ni),
            row("CIS", "ifrs-full_BasicEarningsLossPerShare", eps)]


def _alot(*groups) -> list[dict]:
    """alotMatter 행 — groups = [(결산기준일, 보통주 DPS, 접수번호), …]."""
    return [{"category": "주당 현금배당금(원)", "stock_type": "보통주", "current": dps, "previous": "", "before_previous": "",
             "stlm_dt": stlm, "rcept_no": rcept, "is_special": False, "is_stock_dividend": False} for stlm, dps, rcept in groups]


# ── 롯데리츠(330590) — 반기 결산(1~6·7~12월) ─────────────────────────────────────────────────────────
LOTTE = {
    (2025, "11011"): _report("20260310002810", (14, 13, 12), ("19,627,460,882", "16,546,765,792", "10,613,567,528"), ("67", "57", "37"),
                             eq="1,139,498,580,374"),
    (2026, "11011"): _report("20260921000222", (15, 14, 13), ("23,770,182,540", "19,627,460,882", "16,546,765,792"), ("82", "67", "57")),
    (2026, "11012"): _interim("10,570,554,501", "1,114,525,962,143"),       # 반기보고서(2026.03) — 6개월 결산기의 3개월 시점
    (2025, "11012"): _interim("9,707,636,832", "1,130,000,000,000"),        # 반기보고서(2025.09)
}
LOTTE_ALOT = {2025: _alot(("2025-06-30", "117", "20250911000374"), ("2025-12-31", "123", "20260310002810")),
              2026: _alot(("2026-06-30", "139", "20260921000222"))}
LOTTE_CAP = 1_178_993_046_720


def test_semiannual_reit_per_uses_twelve_months_of_settlement_periods(monkeypatch):
    p, seen = _run(monkeypatch, overview_acc_mt="06", db_acc_mt=12, reports=LOTTE, alot=LOTTE_ALOT)
    d, i, m = p["data"], p["data"]["inputs"], p["data"]["multiples"]
    assert d["fiscal_year"] == 2025 and i["dps_krw"] == 262                                  # 배당은 그대로(#135 · 연도 회귀 고침)
    fy0, ttm = 16_546_765_792 + 19_627_460_882, 19_627_460_882 + 23_770_182_540
    assert (i["net_income_fy0_krw"], i["net_income_ttm_krw"]) == (fy0, ttm)
    assert m["per_fy0"] == round(LOTTE_CAP / fy0, 2) == 32.59                                # 종전 60.07(7~12월 여섯 달)
    assert m["per_ttm"] == round(LOTTE_CAP / ttm, 2) == 27.17                                # 종전 57.54(2025.10~2026.03 여섯 달)
    assert (i["eps_fy0_krw"], i["eps_ttm_krw"], i["eps_ttm_basis"]) == (57 + 67, 67 + 82, "disclosed_period_sum")
    assert (i["ttm_quarter"], i["ttm_quarter_year"]) == ("최근 12개월 결산기", 2026)
    assert i["ni_basis"] == {"rule": "trailing_12m_periods", "period_months": 6,
                             "fy0": {"periods": [{"stlm_dt": "2025-06-30", "net_income_krw": 16_546_765_792},
                                                 {"stlm_dt": "2025-12-31", "net_income_krw": 19_627_460_882}], "net_income_krw": fy0},
                             "ttm": {"periods": [{"stlm_dt": "2025-12-31", "net_income_krw": 19_627_460_882},
                                                 {"stlm_dt": "2026-06-30", "net_income_krw": 23_770_182_540}], "net_income_krw": ttm}}
    assert (m["pbr_basis"], i["controlling_equity_krw"]) == ("MRQ", 1_114_525_962_143)      # 자본은 종전 그대로
    assert (2026, "11011") in seen["acnt"], "다음 사업연도 결산기가 배당 사항에 보이면 그 사업보고서 재무를 읽는다"
    w = [x for x in d["warnings"] if x.startswith("반기 결산 회사 — 지배순이익")]
    assert len(w) == 1 and "FY0 = 2025-06 + 2025-12 결산기 36,174,226,674원" in w[0] and "TTM = 2025-12 + 2026-06 결산기" in w[0]
    md = S._render_md(p)
    assert "- TTM 기준: 최근 12개월 결산기 2개 합(2025-12 + 2026-06)" in md and "당해 누적 − 전년 동기 누적" not in md
    ex = TP._render_explain_firm(p)
    assert "36,174,226,674원 (12개월 결산기 2개 합 — 2025-06 16,546,765,792원 + 2025-12 19,627,460,882원)" in ex
    assert "EPS(TTM) = **공시 EPS 결산기 합**(최근 12개월 결산기 2개" in ex


def test_semiannual_reit_before_the_next_report_uses_the_fiscal_year_window_for_both(monkeypatch):
    """다음 사업연도 결산기가 아직 없으면(8월 — 2026.06 사업보고서 9월 제출 전) TTM 도 FY0 창(2025-06 + 2025-12)이고 더 읽지 않는다."""
    reports = {k: v for k, v in LOTTE.items() if k != (2026, "11011")}
    p, seen = _run(monkeypatch, overview_acc_mt="06", db_acc_mt=12, reports=reports, alot={2024: _alot(("2024-12-31", "112", "r24")),
                                                                                             2025: LOTTE_ALOT[2025]})
    i = p["data"]["inputs"]
    assert i["net_income_fy0_krw"] == i["net_income_ttm_krw"] == 16_546_765_792 + 19_627_460_882
    assert i["ni_basis"]["fy0"] == i["ni_basis"]["ttm"] and (i["ttm_quarter"], i["ttm_quarter_year"]) == ("최근 12개월 결산기", 2025)
    assert (2026, "11011") not in seen["acnt"]


# ── SK리츠(395400) — 분기 결산 ────────────────────────────────────────────────────────────────────
SK = {
    (2025, "11011"): _report("20260311004347", (19, 18, 17), ("32,360,769,209", "19,112,879,383", "30,116,559,104"),
                             ("109.07", "65.64", "108.66"), eq="1,939,593,502,956"),
    (2026, "11011"): _report("20260908000397", (21, 20, 19), ("37,228,609,126", "19,375,384,605", "32,360,769,209"),
                             ("123.68", "64.41", "109.07"), eq="1,961,439,139,427"),
}
SK_ALOT = {2025: _alot(("2025-03-31", "70", "20250611000555"), ("2025-06-30", "66", "20250922000291"),
                       ("2025-09-30", "66", "20251210000536"), ("2025-12-31", "66", "20260311004347")),
           2026: _alot(("2026-03-31", "68", "20260610000569"), ("2026-06-30", "68", "20260908000397"))}
SK_CAP = 1_598_403_562_200


def test_quarterly_reit_ttm_is_four_quarters_and_fy0_says_it_is_one(monkeypatch):
    """분기 결산은 사업보고서 하나에 결산기가 셋 — FY0(2025 사업연도 넷)는 맞출 수 없어 종전 값(2025-12 석 달)이고 그렇다고 적는다.
    TTM 은 2026.06 보고서의 셋(제19~21기) + 2025.12 보고서의 제18기 = 2025-09 ~ 2026-06 넷. 종전 TTM 은 빈칸(분기 보고서가 없다)."""
    p, seen = _run(monkeypatch, overview_acc_mt="03", db_acc_mt=12, reports=SK, alot=SK_ALOT, ticker="395400", cap=SK_CAP, price=5310)
    d, i, m = p["data"], p["data"]["inputs"], p["data"]["multiples"]
    ttm = 19_112_879_383 + 32_360_769_209 + 19_375_384_605 + 37_228_609_126
    assert i["net_income_fy0_krw"] == 32_360_769_209 and m["per_fy0"] == 49.39                 # 종전 그대로(기능 축소 없음)
    assert i["net_income_ttm_krw"] == ttm and m["per_ttm"] == round(SK_CAP / ttm, 2) == 14.79    # 종전 None
    assert i["eps_ttm_krw"] == round(65.64 + 109.07 + 64.41 + 123.68) and i["dps_krw"] == 268
    assert i["ni_basis"]["fy0"] == {"periods": [{"stlm_dt": "2025-12-31"}], "single_period": True}
    assert [x["stlm_dt"] for x in i["ni_basis"]["ttm"]["periods"]] == ["2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]
    w = [x for x in d["warnings"] if x.startswith("분기 결산 회사 — 지배순이익")]
    assert len(w) == 1 and "마지막 결산기(2025-12, 3개월) 값 그대로" in w[0]
    assert "(2025-12 결산기 하나 — 분기 결산이라" in TP._render_explain_firm(p)


def test_semiannual_without_the_previous_period_says_why_fy0_is_one_period(monkeypatch):
    """반기 결산인데 FY0 보고서의 전기 순이익이 비면(상장 첫 결산기 등) FY0 는 그 결산기 하나 — 까닭을 분기 결산과 다르게 적는다."""
    reports = dict(LOTTE)
    reports[(2025, "11011")] = _report("20260310002810", (14, 13, 12), ("19,627,460,882", "", ""), ("67", "", ""), eq="1,139,498,580,374")
    reports.pop((2026, "11011"))
    p, _ = _run(monkeypatch, overview_acc_mt="06", db_acc_mt=12, reports=reports,
                alot={2024: _alot(("2024-12-31", "112", "r24")), 2025: LOTTE_ALOT[2025]})
    i = p["data"]["inputs"]
    assert "ni_basis" not in i and i["net_income_fy0_krw"] == 19_627_460_882      # FY0·TTM 모두 못 맞추면 칸도 경고도 없다(종전 응답)
    w = S._period_ni_warning(None, {"periods": [("20251231", 1), ("20260630", 2)], "ni": 3}, "20251231", 6)
    assert "12개월 결산기를 다 찾지 못해" in w and "셋까지만" not in w
    i2 = {"ni_basis": {"period_months": 6, "fy0": {"periods": [{"stlm_dt": "2025-12-31"}], "single_period": True}}}
    assert TP._ni_span(i2, "fy0") == " (2025-12 결산기 하나 — 12개월 결산기를 다 찾지 못했다)"


def test_newer_report_values_win_for_overlapping_periods():
    """겹치는 결산기는 새 보고서 값(정정 반영) — 제19기는 2026.06 보고서 칸."""
    older = _report("a", (19, 18, 17), ("100", "10", "1"), ("1", "1", "1"))
    newer = _report("b", (21, 20, 19), ("400", "300", "200"), ("4", "3", "2"))
    t = S.trailing_period_ni([("20260630", newer), ("20251231", older)], 3)
    assert t["ni"] == 400 + 300 + 200 + 10 and [d for d, _ in t["periods"]] == ["20250930", "20251231", "20260331", "20260630"]
    assert t["eps"] == 4 + 3 + 2 + 1


def test_fiscal_year_window_also_takes_the_newer_report_comparatives():
    """FY0 창(2025-09 에서 거꾸로)도 겹치는 결산기는 새 보고서 칸 — 10-19 운영 신한알파리츠: 2025.09 사업보고서에는 지배·비지배 구분이 없어
    총순이익(113.1억)이 지배순이익 자리에 들어갔고, 2026.03 사업보고서 전기·전전기 칸은 지배 67.5억 · 97.8억이다(DART 원값)."""
    older = [r for r in _report("a", (15, 14, 13), ("11,311,947,161", "12,428,538,238", "4,652,415,132"), ("42", "71", "17"))
             if r["account_id"] != "ifrs-full_ProfitLossAttributableToOwnersOfParent"]
    older.append(dict(older[0], sj_div="CIS", account_id="ifrs-full_ProfitLoss", thstrm_amount="11,311,947,161",
                      frmtrm_amount="12,428,538,238", bfefrmtrm_amount="4,652,415,132"))
    newer = _report("b", (16, 15, 14), ("11,559,835,786", "6,751,988,258", "9,784,917,968"), ("82", "42", "71"))
    fy0 = S.trailing_period_ni([("20260331", newer), ("20250930", older)], 6, end="20250930")
    assert fy0["ni"] == 6_751_988_258 + 9_784_917_968 and [d for d, _ in fy0["periods"]] == ["20250331", "20250930"]
    alone = S.trailing_period_ni([("20250930", older)], 6, end="20250930")
    assert alone["ni"] == 11_311_947_161 + 12_428_538_238                                   # 새 보고서가 없으면 그 보고서 값
    assert S.trailing_period_ni([("20260331", newer)], 6, end="20250831") is None              # 결산기준일이 주기에 안 맞는다


@pytest.mark.parametrize("reports,months", [
    ([("20251231", _report("a", (19, 18, 17), ("1", "2", "3"), ("1", "1", "1")))], 3),          # 분기 결산 보고서 하나 — 결산기 셋
    ([("20251231", _report("a", (14, 12, 11), ("1", "2", "3"), ("1", "1", "1")))], 6),          # 기수가 하나씩 줄지 않는다(이행기 등)
    ([("20251231", _report("a", (14, 13, 12), ("1", "", "3"), ("1", "1", "1")))], 6),           # 전기 순이익 빈 칸
    ([("20260630", _report("b", (21, 20, 19), ("4", "3", "2"), ("1", "1", "1"))),
      ("20250930", _report("a", (19, 18, 17), ("1", "2", "3"), ("1", "1", "1")))], 3),          # 앞 보고서 결산기준일이 기수와 안 맞는다
    ([("20251231", _report("a", (14, 13, 12), ("1", "2", "3"), ("1", "1", "1")))], 12),         # 반기·분기 결산이 아니다
])
def test_trailing_period_ni_is_none_unless_the_twelve_months_are_whole(reports, months):
    assert S.trailing_period_ni(reports, months) is None


def test_unmatched_receipt_number_keeps_the_previous_values(monkeypatch):
    """재무제표 보고서를 배당 사항의 결산기에 잇지 못하면(접수번호가 다르다) 종전 값 그대로 — 결산기를 짐작하지 않는다."""
    reports = dict(LOTTE)
    reports[(2025, "11011")] = [dict(r, rcept_no="99999999000000") for r in LOTTE[(2025, "11011")]]
    p, seen = _run(monkeypatch, overview_acc_mt="06", db_acc_mt=12, reports=reports, alot=LOTTE_ALOT)
    i = p["data"]["inputs"]
    assert "ni_basis" not in i and i["net_income_fy0_krw"] == 19_627_460_882 and i["ttm_quarter"] == "반기 누적"
    assert (2026, "11011") not in seen["acnt"]


def test_next_report_read_failure_keeps_the_fiscal_year_window(monkeypatch):
    """다음 사업연도 사업보고서 재무 읽기가 비면 TTM 도 FY0 창(종전 조립 값을 쓰지 않는다)."""
    reports = {k: v for k, v in LOTTE.items() if k != (2026, "11011")}
    p, seen = _run(monkeypatch, overview_acc_mt="06", db_acc_mt=12, reports=reports, alot=LOTTE_ALOT)
    i = p["data"]["inputs"]
    assert (2026, "11011") in seen["acnt"]
    assert i["net_income_ttm_krw"] == i["net_income_fy0_krw"] == 16_546_765_792 + 19_627_460_882


def test_exception_in_the_extra_read_is_fail_open(monkeypatch):
    p0, _ = _run(monkeypatch, overview_acc_mt="06", db_acc_mt=12, reports=LOTTE, alot=LOTTE_ALOT)
    orig = S._acntall

    async def boom(client, cc, year, rc, fs=None):
        if (year, rc) == (2026, "11011"):
            raise RuntimeError("ReadTimeout")
        return await orig(client, cc, year, rc, fs)
    monkeypatch.setattr(S, "_acntall", boom)
    p = asyncio.run(S.build_valuation_payload("330590", format="json"))
    i = p["data"]["inputs"]
    assert p["status"] == "ok" and "ni_basis" not in i and i["net_income_fy0_krw"] == 19_627_460_882   # 종전 값
    assert i["dps_krw"] == 262 and p0["data"]["inputs"]["ni_basis"]


def test_december_company_is_unchanged(monkeypatch):
    """결산기가 하나인 회사는 이 길을 타지 않는다 — 읽기·응답 그대로."""
    reports = {(2025, "11011"): _report("r1", (57, 56, 55), ("100,000,000,000", "90,000,000,000", "80,000,000,000"), ("1,000", "900", "800"),
                                        eq="1,000,000,000,000"),
               (2026, "11012"): _interim("60,000,000,000", "1,050,000,000,000"),
               (2025, "11012"): _interim("40,000,000,000", "950,000,000,000")}
    p, seen = _run(monkeypatch, overview_acc_mt="12", db_acc_mt=None, reports=reports,
                   alot={2025: _alot(("2025-12-31", "1,668", "r1"))}, ticker="005930")
    i = p["data"]["inputs"]
    assert "ni_basis" not in i and i["net_income_ttm_krw"] == 120_000_000_000 and i["ttm_quarter"] == "반기 누적"
    assert i["eps_ttm_basis"] != "disclosed_period_sum" and (2026, "11011") not in seen["acnt"]
    assert not any("지배순이익·EPS·PER 은 12개월" in w for w in p["data"]["warnings"])


# ── 261019(p1d 적대 검토) — EPS 대체 경로와 방법 고지 ─────────────────────────────────────────────────

def _without_eps(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["account_id"] != "ifrs-full_BasicEarningsLossPerShare"]


def test_period_sum_eps_falls_back_to_ni_over_shares_when_a_report_has_no_eps(monkeypatch):
    """결산기 공시 EPS 가 비면 FY0·TTM EPS 모두 12개월 지배순이익 ÷ 보통주 — 한 결산기 EPS(종전 값)를 그대로 두지 않는다."""
    reports = dict(LOTTE)
    for k in ((2025, "11011"), (2026, "11011")):            # 겹치는 결산기는 새 보고서 칸을 쓰므로 둘 다 비운다
        reports[k] = _without_eps(LOTTE[k])
    p, _ = _run(monkeypatch, overview_acc_mt="06", db_acc_mt=12, reports=reports, alot=LOTTE_ALOT)
    i = p["data"]["inputs"]
    shares = 288_968_884
    fy0, ttm = 16_546_765_792 + 19_627_460_882, 19_627_460_882 + 23_770_182_540
    assert (i["net_income_fy0_krw"], i["net_income_ttm_krw"]) == (fy0, ttm)
    assert i["eps_fy0_krw"] == round(fy0 / shares) == 125                         # 한 결산기(2025-12) ÷ 보통주 = 68 이 아니다
    assert (i["eps_ttm_krw"], i["eps_ttm_basis"]) == (round(ttm / shares), "ni_div_shares_fallback")


def test_period_sum_eps_is_not_used_when_a_share_adjustment_factor_applies(monkeypatch):
    """수정계수 보정이 걸리면(결산기마다 주식 기준이 다를 수 있다) 공시 EPS 합 대신 12개월 지배순이익 ÷ 보통주."""
    async def factor(ticker, after_dd):
        return 0.5 if after_dd.startswith("2025") else 1.0
    monkeypatch.setattr(S, "_eps_adj_factor", factor)
    p, _ = _run(monkeypatch, overview_acc_mt="06", db_acc_mt=12, reports=LOTTE, alot=LOTTE_ALOT)
    i = p["data"]["inputs"]
    shares = 288_968_884
    assert i["eps_adj_factors"] == {"current": 1.0, "prior_q": 0.5}
    assert i["eps_fy0_krw"] == round((16_546_765_792 + 19_627_460_882) / shares)
    assert (i["eps_ttm_krw"], i["eps_ttm_basis"]) == (round((19_627_460_882 + 23_770_182_540) / shares), "ni_div_shares_fallback")


def test_method_note_and_explain_describe_the_period_sum(monkeypatch):
    """방법 고지(`note`)·explain EPS(FY0) 줄이 12개월 결산기 합을 말한다 — 종전 「FY0+분기누적−전년동기누적」 식이 「TTM 기준」 줄과 어긋났다.
    결산기가 하나인 회사는 문구 그대로."""
    p, _ = _run(monkeypatch, overview_acc_mt="06", db_acc_mt=12, reports=LOTTE, alot=LOTTE_ALOT)
    note = p["data"]["note"]
    assert "TTM=결산기마다 공시한 EPS 의 최근 12개월 합" in note and "분기누적−전년동기누적" not in note
    assert "TTM=결산기마다 공시한 EPS" in S._render_md(p)
    ex = TP._render_explain_firm(p)
    assert "EPS(FY0) = **공시 EPS 결산기 합**(FY0 12개월 결산기 2개 — 결산기마다 사업보고서) = **124원**" in ex
    q, _ = _run(monkeypatch, overview_acc_mt="03", db_acc_mt=12, reports=SK, alot=SK_ALOT, ticker="395400", cap=SK_CAP, price=5310)
    assert "TTM=결산기마다 공시한 EPS" in q["data"]["note"]                               # 분기 결산 — TTM 은 합, FY0 는 한 결산기
    assert "EPS(FY0) = 공시 기본주당이익(가중평균 주식수 반영) = **109원**" in TP._render_explain_firm(q)
    dec = {(2025, "11011"): _report("r1", (57, 56, 55), ("100,000,000,000", "90,000,000,000", "80,000,000,000"), ("1,000", "900", "800"),
                                    eq="1,000,000,000,000"),
           (2026, "11012"): _interim("60,000,000,000", "1,050,000,000,000"),
           (2025, "11012"): _interim("40,000,000,000", "950,000,000,000")}
    r, _ = _run(monkeypatch, overview_acc_mt="12", db_acc_mt=None, reports=dec, alot={2025: _alot(("2025-12-31", "1,668", "r1"))},
                ticker="005930")
    assert "TTM=공시 EPS 조립: FY0+분기누적−전년동기누적" in r["data"]["note"]
