# -*- coding: utf-8 -*-
"""실시간 firm(종목 배수 — DART 원문을 바로 읽는 경로)의 **보고서 사이 단위 가리기**(261020 p1f unit-restated). network 0 · DB 0.

회사가 단위를 잘못 적어 보고서가 **통째로** 10ⁿ 배인 경우 — 같은 보고서 안의 항등식(자산 = 부채 + 자본)·순이익 대 시총이 다 맞아 종전 가드가
반응하지 않았다. 같은 날짜의 같은 숫자(자본총계)를 낸 **다른 보고서**와 대야 드러난다.

  · 0091W0 신영스팩11호 꼴 — 2025 사업보고서 금액이 1,000배(자본총계 11조 9,883.9억 = 2026 1분기·반기보고서 전기 칸 119.88억의 정확히 1,000배).
    10-20 운영 firm: PBR 0.001 · 지배자본 11,988,390,000,000 · BPS 1,975,674원.
  · 484130 하나34호스팩 꼴 — 2025 1분기보고서가 100만 배(분기 수집이 전기 칸 대조로 ÷1,000,000 복구했던 칸). firm 의 전년 동기 분기가 이 보고서면
    TTM = 연간 + 당해 분기 − 100만 배 전년 동기 → −27.7조.

수치는 DART 전체 재무제표 API 원문(0091W0 2025 사업보고서 접수 20260310002858 · 2026 1분기 20260514001292, 484130 2024·2025 사업보고서·2026 반기) 그대로이고,
484130 2025 1분기보고서는 원문 값(분기 표에 저장된 복구 값) × 1,000,000 으로 만든 꼴이다.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from open_proxy_mcp.services import price_multiple_data as S
from open_proxy_mcp.services import scale_guard as G

TODAY = date(2026, 10, 19)


def _bs(aid: str, th: str | None, frm: str | None = None, ccy: str = "KRW") -> dict:
    r = {"sj_div": "BS", "account_id": f"ifrs-full_{aid}", "account_nm": aid, "thstrm_amount": th or "", "currency": ccy}
    if frm is not None:
        r["frmtrm_amount"] = frm
    return r


def _pl(ni: str, *, add: str | None = None, frm: str | None = None, eps: str | None = None, rcept: str = "") -> list[dict]:
    out = [{"sj_div": "CIS", "account_id": "ifrs-full_ProfitLoss", "account_nm": "당기순이익(손실)", "thstrm_amount": ni,
            "rcept_no": rcept}]
    if add is not None:
        out[0]["thstrm_add_amount"] = add
    if frm is not None:
        out[0]["frmtrm_amount"] = frm
    if eps is not None:
        out.append({"sj_div": "CIS", "account_id": "ifrs-full_BasicEarningsLossPerShare", "account_nm": "보통주기본주당이익(손실)",
                    "thstrm_amount": eps, "thstrm_add_amount": eps})
    return out


def _report(rcept: str, *, assets, liab, eq, ni, assets_frm=None, liab_frm=None, eq_frm=None, ni_add=None, ni_frm=None, eps=None,
            ccy: str = "KRW") -> list[dict]:
    rows = [_bs("Assets", assets, assets_frm, ccy), _bs("Liabilities", liab, liab_frm, ccy), _bs("Equity", eq, eq_frm, ccy)]
    rows += _pl(ni, add=ni_add, frm=ni_frm, eps=eps)
    for r in rows:
        r["rcept_no"] = rcept
    return rows


# ── 0091W0 신영스팩11호: 2025 사업보고서 금액 1,000배(원문), 2026 1분기(원문)·반기(DB 저장값과 같은 꼴) 맞음, 전년 같은 분기 보고서 없음(첫해) ──
SPAC_FY_X1000 = _report("20260310002858", assets="13,945,226,648,000", liab="1,956,836,648,000", eq="11,988,390,000,000",
                        ni="-17,858,891,000", eps="-11")
SPAC_2026Q1 = _report("20260514001292", assets="14,009,728,391", liab="1,978,706,899", eq="12,031,021,492",
                      assets_frm="13,945,226,648", liab_frm="1,956,836,648", eq_frm="11,988,390,000", ni="42,631,492", ni_add="42,631,492",
                      eps="7")
SPAC_2026H1 = _report("20260814000001", assets="14,051,373,000", liab="1,978,706,954", eq="12,072,666,046",
                      assets_frm="13,945,226,648", liab_frm="1,956,836,648", eq_frm="11,988,390,000", ni="41,644,554", ni_add="84,276,046",
                      eps="14")
SPAC_SHARES = 6_068_000
SPAC_PRICE = 2_060
SPAC_CAP = SPAC_SHARES * SPAC_PRICE           # 125.0억 — 운영 시총과 같은 크기


# ── 484130 하나34호스팩: 2025 사업보고서(원문)·2026 1분기(분기 표 값) 맞음, 2025 1분기보고서 100만 배 ──
H_FY2025 = _report("20260305001627", assets="10,070,371,574", liab="1,407,626,158", eq="8,662,745,416", assets_frm="9,841,878,611",
                   liab_frm="1,336,382,644", eq_frm="8,505,495,967", ni="159,609,313", ni_frm="-19,390,874")
H_2026Q1 = _report("20260515000002", assets="10,130,000,000", liab="1,446,105,624", eq="8,683,894,376", assets_frm="10,070,371,574",
                   liab_frm="1,407,626,158", eq_frm="8,662,745,416", ni="21,148,960", ni_add="21,148,960")
_M = 1_000_000
H_2025Q1_X1E6 = _report("20250515000003", assets=f"{9_870_000_000 * _M:,}", liab=f"{1_336_810_888 * _M:,}", eq=f"{8_533_189_112 * _M:,}",
                        assets_frm=f"{9_841_878_611 * _M:,}", liab_frm=f"{1_336_382_644 * _M:,}", eq_frm=f"{8_505_495_967 * _M:,}",
                        ni=f"{27_693_145 * _M:,}", ni_add=f"{27_693_145 * _M:,}")
H_SHARES = 8_700_000
H_PRICE = 1_060
H_CAP = H_SHARES * H_PRICE


def _run_live(mp, reports: dict, *, cap, shares, price, fy: int = 2025, revenue=None):
    """reports = {(사업연도, 보고서 코드): rows}. DART·DB 없이 실시간 firm 경로를 돈다 — 원장 읽기 차례를 `calls` 에 남긴다."""
    calls: list[tuple] = []

    async def resolve(q):
        return {"corp_name": "가나다스팩", "stock_code": "0091W0", "corp_code": "01945339"}, None

    async def fin_metrics(code, **kw):
        return {"data": {"summary": {"eps_krw": None, "revenue_krw": revenue, "roe_pct": None,
                                     "capital_impairment_status": "normal", "reprt_code": "11011"}, "year": fy}}

    async def market_for(code):
        return {"price": price, "date": "20261016" if price else None, "common_mktcap": cap, "list_shrs": shares,
                "source": "db" if price else None}

    async def acntall(client, cc, year, rc, fs=None):
        calls.append((year, rc))
        rows = reports.get((year, rc))
        return (rows, "OFS") if rows else ([], None)

    async def shares_out(client, cc, year):
        return {"total": shares, "common": shares}

    async def annual_summary(cc, year):
        return {}, None

    class Client:
        async def get_company_info(self, cc):
            return {"induty_code": "66199", "acc_mt": "12"}

    mp.setattr(S, "_pg_rows", lambda sql, params=(): [])
    mp.setattr(S, "today_kst", lambda: TODAY)
    mp.setattr(S, "_resolve_listed", resolve)
    mp.setattr(S, "get_dart_client", lambda: Client())
    mp.setattr(S, "build_financial_metrics_payload", fin_metrics)
    mp.setattr(S, "_market_for", market_for)
    mp.setattr(S, "_acntall", acntall)
    mp.setattr(S, "_shares_outstanding", shares_out)
    mp.setattr(S, "_annual_summary", annual_summary)
    return asyncio.run(S.build_valuation_payload("0091W0", format="json")), calls


# ═══ 순수 규칙 ═════════════════════════════════════════════════════════════════════════════════════════
def test_reconcile_finds_the_inflated_annual_report_by_majority_and_market_cap():
    obs = [("FY", ("FYE", 2025, "KRW"), 11_988_390_000_000, 2),
           ("Q1", ("FYE", 2025, "KRW"), 11_988_390_000, 2), ("Q1", ("Q", "Q1", "KRW"), 12_031_021_492, 3),
           ("H1", ("FYE", 2025, "KRW"), 11_988_390_000, 2), ("H1", ("Q", "H1", "KRW"), 12_072_666_046, 3)]
    r = G.reconcile_report_scales(obs, mktcap=SPAC_CAP)
    assert r["status"] == "fixed" and r["divisors"] == {"FY": 1000}
    assert r["method"] == "majority+mktcap"
    one = G.reconcile_report_scales(obs[:3], mktcap=SPAC_CAP)
    assert one["status"] == "fixed" and one["divisors"] == {"FY": 1000} and one["method"] == "mktcap", "보고서 둘(1 대 1)이면 시총(PBR)이 가린다"


def test_reconcile_finds_the_deflated_or_million_times_quarter():
    obs = [("FY", ("FYE", 2025, "KRW"), 8_662_745_416, 2), ("FY", ("FYE", 2024, "KRW"), 8_505_495_967, 0),
           ("QC", ("FYE", 2025, "KRW"), 8_662_745_416, 2), ("QC", ("Q", "QC", "KRW"), 8_683_894_376, 3),
           ("QP", ("FYE", 2024, "KRW"), 8_505_495_967 * _M, 0), ("QP", ("Q", "QP", "KRW"), 8_533_189_112 * _M, 1)]
    r = G.reconcile_report_scales(obs, mktcap=None)
    assert r["status"] == "fixed" and r["divisors"] == {"QP": 1_000_000} and r["method"] == "majority", "시총 없이도 2 대 1 다수결"
    # 반대로 분기 보고서가 1,000분의 1(천원을 원으로 적음)이면 곱한다
    low = [("FY", ("FYE", 2025, "KRW"), 8_662_745_416, 2), ("QC", ("FYE", 2025, "KRW"), 8_662_745, 2),
           ("QC", ("Q", "QC", "KRW"), 8_683_894, 3)]
    r2 = G.reconcile_report_scales(low, mktcap=H_CAP)
    assert r2["status"] == "fixed" and r2["divisors"] == {"QC": pytest.approx(0.001)}


def test_reconcile_leaves_restatements_currency_switches_and_lone_reports_alone():
    restated = [("FY", ("FYE", 2025, "KRW"), 1_070_000_000, 2), ("QC", ("FYE", 2025, "KRW"), 1_000_000_000, 2)]
    assert G.reconcile_report_scales(restated, mktcap=1e9)["status"] == "clean", "재작성(1.07배)은 단위 오류가 아니다"
    fx = [("FY", ("FYE", 2024, "USD"), 3_000_000_000, 0), ("QP", ("FYE", 2024, "KRW"), 3_300_000_000_000, 0)]
    assert G.reconcile_report_scales(fx, mktcap=5e12)["status"] == "clean", "통화가 다른 보고서끼리(1/1,100 ≈ 10⁻³)는 대지 않는다"
    sign = [("FY", ("FYE", 2025, "KRW"), -5_000_000_000_000, 2), ("QC", ("FYE", 2025, "KRW"), 5_000_000_000, 2)]
    assert G.reconcile_report_scales(sign, mktcap=1e10)["status"] == "clean", "부호가 다르면 같은 숫자가 아니다"
    assert G.reconcile_report_scales([("FY", ("FYE", 2025, "KRW"), 1e12, 2)], mktcap=1e9)["status"] == "clean"


def test_reconcile_does_not_guess_when_signals_are_missing_or_disagree():
    tie = [("FY", ("FYE", 2025, "KRW"), 11_988_390_000_000, 2), ("QC", ("FYE", 2025, "KRW"), 11_988_390_000, 2)]
    assert G.reconcile_report_scales(tie, mktcap=None)["status"] == "undecided", "1 대 1 · 시총 없음 — 고르지 않는다"
    # 같은 해 보고서 둘(사업보고서·전년 동기 분기)이 함께 1,000배이고 다음 해 분기만 맞는 경우 — 다수결은 틀린 쪽을 고른다. 시총이 엇갈려 막는다.
    both = [("FY", ("FYE", 2025, "KRW"), 8_662_745_416_000, 2), ("FY", ("FYE", 2024, "KRW"), 8_505_495_967_000, 0),
            ("QP", ("FYE", 2024, "KRW"), 8_505_495_967_000, 0),
            ("QC", ("FYE", 2025, "KRW"), 8_662_745_416, 2), ("QC", ("Q", "QC", "KRW"), 8_683_894_376, 3)]
    r = G.reconcile_report_scales(both, mktcap=H_CAP)
    assert r["status"] == "undecided" and r["method"] == "majority≠mktcap"


def test_rescale_rows_divides_amounts_but_not_per_share_lines():
    rows = SPAC_FY_X1000 + [{"sj_div": "BS", "account_id": "ifrs-full_Equity", "account_nm": "x", "thstrm_amount": "(5,000)"}]
    out = G.rescale_rows(rows, 1000)
    eq = next(r for r in out if r["account_id"] == "ifrs-full_Equity")
    assert eq["thstrm_amount"] == "11988390000"
    assert next(r for r in out if "PerShare" in r["account_id"])["thstrm_amount"] == "-11", "주당이익은 그대로"
    assert next(r for r in out if r["account_id"] == "ifrs-full_ProfitLoss")["thstrm_amount"] == "-17858891"
    assert out[-1]["thstrm_amount"] == "-5", "괄호 음수"
    assert rows[2]["thstrm_amount"] == "11,988,390,000,000", "원본 rows 는 바꾸지 않는다(사본)"
    assert G.rescale_rows(rows, 1) is rows


def test_assess_marks_cross_report_results():
    fixed = G.assess(thstrm=1.0, cross_report={"status": "fixed", "divisors": {"FY": 1000}, "method": "mktcap"})
    assert fixed["tier"] == "soft" and fixed["soft_hit"] == ["cross_report_scale_fixed"]
    und = G.assess(thstrm=1.0, cross_report={"status": "undecided", "method": None})
    assert und["tier"] == "hard" and und["hard_hit"] == ["cross_report_scale"]
    assert G.assess(thstrm=1.0, cross_report={"status": "clean"})["tier"] == "clean"


# ═══ 실시간 firm ═══════════════════════════════════════════════════════════════════════════════════════
def test_live_firm_divides_the_inflated_annual_report_0091W0(monkeypatch):
    reports = {(2025, "11011"): SPAC_FY_X1000, (2026, "11013"): SPAC_2026Q1, (2026, "11012"): SPAC_2026H1}
    p, calls = _run_live(monkeypatch, reports, cap=SPAC_CAP, shares=SPAC_SHARES, price=SPAC_PRICE)
    d = p["data"]
    inp, mul = d["inputs"], d["multiples"]
    assert inp["controlling_equity_krw"] == 11_988_390_000, "종전 11,988,390,000,000(1,000배)"
    assert inp["net_income_fy0_krw"] == -17_858_891
    assert inp["bps_krw"] == 1_976, "종전 1,975,674원"
    assert mul["pbr_mrq"] == pytest.approx(SPAC_CAP / 11_988_390_000, abs=0.01), "종전 0.001"
    assert inp["eps_fy0_krw"] == -11, "주당이익은 원문 그대로(단위 오류와 무관)"
    fix = d["data_quality"]["scale_fix"]
    assert [f["report"] for f in fix] == ["2025 사업보고서"] and fix[0]["divisor"] == 1000 and fix[0]["rcept_no"] == "20260310002858"
    assert fix[0]["method"] == "majority+mktcap"
    assert {e["compared_with"] for e in fix[0]["evidence"]} == {"2026 반기보고서", "2026 1분기보고서"}
    assert d["data_quality"]["scale_tier"] == "soft" and "cross_report_scale_fixed" in d["data_quality"]["scale_flags"]
    w = " ".join(d["warnings"])
    assert "단위 오류를 바로잡았다" in w and "÷1,000" in w and "20260310002858" in w
    assert "🚨" not in w, "바로잡았으면 hard 경고를 내지 않는다"
    assert calls == [(2025, "11011"), (2026, "11014"), (2026, "11012"), (2025, "11012"), (2026, "11013"), (2025, "11013")], \
        "원장 읽기는 종전과 같다(DART 콜 0 추가)"


def test_live_firm_with_only_one_later_report_uses_market_cap(monkeypatch):
    reports = {(2025, "11011"): SPAC_FY_X1000, (2026, "11013"): SPAC_2026Q1}
    p, _ = _run_live(monkeypatch, reports, cap=SPAC_CAP, shares=SPAC_SHARES, price=SPAC_PRICE)
    assert p["data"]["inputs"]["controlling_equity_krw"] == 11_988_390_000
    assert p["data"]["data_quality"]["scale_fix"][0]["method"] == "mktcap"


def test_live_firm_without_market_cap_and_one_to_one_warns_and_keeps_values(monkeypatch):
    reports = {(2025, "11011"): SPAC_FY_X1000, (2026, "11013"): SPAC_2026Q1}
    p, _ = _run_live(monkeypatch, reports, cap=None, shares=SPAC_SHARES, price=None)
    d = p["data"]
    assert d["inputs"]["controlling_equity_krw"] == 11_988_390_000_000, "못 가리면 원문 그대로"
    assert d["data_quality"]["scale_tier"] == "hard" and d["data_quality"]["scale_flags"] == ["cross_report_scale"]
    assert d["data_quality"]["scale_cross_report"]["status"] == "undecided"
    assert "scale_fix" not in d["data_quality"]
    w = " ".join(d["warnings"])
    assert "보고서 사이 단위 어긋남" in w and "11,988,390,000,000" in w and "11,988,390,000" in w
    assert "DART 재무 단위(스케일) 오류 강하게 의심" not in w, "같은 사실을 두 번 적지 않는다"


def test_live_firm_divides_the_million_times_prior_year_quarter_484130(monkeypatch):
    reports = {(2025, "11011"): H_FY2025, (2026, "11013"): H_2026Q1, (2025, "11013"): H_2025Q1_X1E6}
    p, _ = _run_live(monkeypatch, reports, cap=H_CAP, shares=H_SHARES, price=H_PRICE)
    d = p["data"]
    assert d["inputs"]["net_income_ttm_krw"] == 159_609_313 + 21_148_960 - 27_693_145, "종전 연간 + 당해 − 100만 배 전년 동기 → −27.7조"
    assert d["multiples"]["per_ttm"] == pytest.approx(H_CAP / (159_609_313 + 21_148_960 - 27_693_145), abs=0.01)
    assert d["inputs"]["controlling_equity_krw"] == 8_683_894_376, "자본(MRQ)은 맞는 당해 분기 그대로"
    fix = d["data_quality"]["scale_fix"]
    assert [(f["report"], f["divisor"]) for f in fix] == [("2025 1분기보고서", 1_000_000)]


def test_live_firm_unchanged_when_reports_agree(monkeypatch):
    reports = {(2025, "11011"): H_FY2025, (2026, "11013"): H_2026Q1,
               (2025, "11013"): _report("20250515000003", assets="9,870,000,000", liab="1,336,810,888", eq="8,533,189,112",
                                        assets_frm="9,841,878,611", liab_frm="1,336,382,644", eq_frm="8,505,495,967",
                                        ni="27,693,145", ni_add="27,693,145")}
    p, calls = _run_live(monkeypatch, reports, cap=H_CAP, shares=H_SHARES, price=H_PRICE)
    d = p["data"]
    assert d["data_quality"]["scale_tier"] == "clean" and d["data_quality"]["scale_flags"] == []
    assert "scale_fix" not in d["data_quality"] and "scale_cross_report" not in d["data_quality"]
    assert d["inputs"]["net_income_ttm_krw"] == 159_609_313 + 21_148_960 - 27_693_145
    assert not any("단위" in w for w in d["warnings"])
    assert calls == [(2025, "11011"), (2026, "11014"), (2026, "11012"), (2026, "11013"), (2025, "11013")]


def test_live_firm_ignores_a_broken_quarter_it_does_not_use(monkeypatch):
    """짝(전년 같은 분기)이 없어 값에 쓰지 않는 분기 보고서가 1,000배여도 응답은 종전 그대로 — 대조에만 쓰고 바로잡을 값이 없다."""
    q3_x1000 = _report("20261114000009", assets=f"{10_200_000_000 * 1000:,}", liab=f"{1_450_000_000 * 1000:,}", eq=f"{8_700_000_000 * 1000:,}",
                       assets_frm=f"{10_070_371_574 * 1000:,}", liab_frm=f"{1_407_626_158 * 1000:,}", eq_frm=f"{8_662_745_416 * 1000:,}",
                       ni="1", ni_add="40,000,000,000")
    reports = {(2025, "11011"): H_FY2025, (2026, "11014"): q3_x1000, (2026, "11013"): H_2026Q1,
               (2025, "11013"): _report("20250515000003", assets="9,870,000,000", liab="1,336,810,888", eq="8,533,189,112",
                                        assets_frm="9,841,878,611", liab_frm="1,336,382,644", eq_frm="8,505,495,967",
                                        ni="27,693,145", ni_add="27,693,145")}
    p, _ = _run_live(monkeypatch, reports, cap=H_CAP, shares=H_SHARES, price=H_PRICE)
    d = p["data"]
    assert d["data_quality"]["scale_tier"] == "clean" and "scale_fix" not in d["data_quality"]
    assert d["inputs"]["net_income_ttm_krw"] == 159_609_313 + 21_148_960 - 27_693_145
    assert not any("단위" in w for w in d["warnings"])
