# -*- coding: utf-8 -*-
"""shareholder_commitment 자사주 소각 사이클 BPS — 지배자본 기준 · 매입 기간 말 기준일(261021 fin-commit).

BPS(= 자기자본 ÷ 합계 유통주식수)의 자기자본을 종전엔 재무 지표 도구의 `total_equity_krw`(자본총계, **비지배지분 포함**)로 썼다. 지주사는 연결 자본총계의
대부분이 자회사 비지배 몫이라 BPS 가 몇 배 과대해지고, 매입가가 BPS 보다 싸다는 「장부가 기준 이득」이 거꾸로 나온다. 또 기준 시점이 결과보고서 접수 해 말
(`year` 사업보고서)이라 그해 안에 이미 끝난 매입도 매입 뒤에야 공시된 재무상태표로 나눴다.

이제: ① 자기자본 = 지배자본(수집·배수·재무 지표 도구와 같은 공용 규칙 `services/controlling_eq`) ② 기준일 = 사이클의 매입 기간 말까지 공시돼 있던 최신 정기보고서
재무상태표(DART 전체 재무제표 1콜), 못 쓰면 종전 연말 자본(재무 지표 도구의 `controlling_equity_krw`, 그 칸이 없으면 자본총계 + 경고) ③ 사이클마다 `bps_basis`.

수치는 DART 전체 재무제표(fnlttSinglAcntAll)·주식의 총수(stockTotqySttus) 원문에서 자본 줄만 남긴 것이다(DART 0콜, 가짜 응답).
  · 한화 000880 — 2025 사업보고서(접수 20260331003871) 자본총계 484,353.27억 · 지배 118,402.06억 · 비지배 365,951.21억 → 4.09배.
    2025 3분기보고서(접수 20251128001868 — 20251113 원본의 기재정정본) 자본총계 460,664.5억 · 지배 109,533.45억 → 4.21배. 유통주식 합계 88,978,956(2025 사업보고서).
  · CJ 001040 — 사업보고서 170,170.52억 · 52,088.98억 → 3.27배. 3분기(접수 20251114001784) 174,670.40억 · 54,251.87억. 유통주식 33,530,936.
  · 삼성물산 028260 — 사업보고서 574,969.49억 · 499,118.92억 → 1.15배. 3분기(접수 20251114001740) 482,666.40억 · 413,825.13억. 유통주식 163,635,171.
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest

from open_proxy_mcp.dart.client import DartClientError
from open_proxy_mcp.services import shareholder_commitment as SC

# 사업보고서(FY2025 말) · 3분기말 — (자본총계, 지배자본), 전기말(2024 말) 쌍, 접수번호, 합계 유통주식수.
FIRMS = {
    "000880": {"cc": "00160588", "name": "한화", "shares": 88_978_956,
               "fy": (48_435_327_000_000, 11_840_206_000_000, "20260331003871"),
               "q3": (46_066_450_000_000, 10_953_345_000_000, "20251128001868"),
               "prev": (39_777_621_000_000, 10_759_984_000_000)},
    "001040": {"cc": "00148540", "name": "CJ", "shares": 33_530_936,
               "fy": (17_017_052_447_000, 5_208_897_884_000, "20260318001509"),
               "q3": (17_467_040_192_000, 5_425_186_600_000, "20251114001784"),
               "prev": (17_730_414_070_000, 5_452_300_896_000)},
    "028260": {"cc": "00149655", "name": "삼성물산", "shares": 163_635_171,
               "fy": (57_496_949_405_446, 49_911_892_454_122, "20260312000856"),
               "q3": (48_266_639_958_227, 41_382_512_552_304, "20251114001740"),
               "prev": (37_258_539_898_529, 31_068_664_773_795)},
}
BY_CC = {f["cc"]: t for t, f in FIRMS.items()}


def _bs_rows(total, ctrl, rcept, prev=None, *, reprt="11014", currency="KRW", with_ctrl_row=True, nci=True) -> list[dict]:
    """재무상태표 자본 줄만 — 자본총계 · 지배 계정 줄 · 비지배 줄(전기말은 `prev` 쌍이 있으면 같이)."""
    pt, pc = prev if prev else (None, None)
    rows = [{"rcept_no": rcept, "reprt_code": reprt, "sj_div": "BS", "account_id": "ifrs-full_Equity", "account_nm": "자본총계",
             "thstrm_amount": str(total), "frmtrm_amount": str(pt) if pt else "", "ord": "30", "currency": currency}]
    if with_ctrl_row:
        rows.append({"rcept_no": rcept, "reprt_code": reprt, "sj_div": "BS", "account_id": "ifrs-full_EquityAttributableToOwnersOfParent",
                     "account_nm": "지배기업 소유주지분", "thstrm_amount": str(ctrl), "frmtrm_amount": str(pc) if pc else "",
                     "ord": "31", "currency": currency})
    if nci:
        rows.append({"rcept_no": rcept, "reprt_code": reprt, "sj_div": "BS", "account_id": "ifrs-full_NoncontrollingInterests",
                     "account_nm": "비지배지분", "thstrm_amount": str(total - ctrl), "frmtrm_amount": str(pt - pc) if pt else "",
                     "ord": "32", "currency": currency})
    return rows


class FakeClient:
    """DART 가짜 — 전체 재무제표(`acnt_all[(cc, 연도, 코드)]` → 행, 없으면 013) · 주식의 총수(`shares[(cc, 연도)]`). 호출을 센다."""

    def __init__(self, acnt_all=None, shares=None):
        self.acnt_all, self.shares, self.calls = acnt_all or {}, shares or {}, []

    async def get_fnltt_singl_acnt_all(self, cc, year, rc, fs):
        self.calls.append(("all", cc, year, rc, fs))
        rows = self.acnt_all.get((cc, year, rc))
        if rows is None or fs != "CFS":
            raise DartClientError("013", "조회된 데이터가 없습니다.")
        return {"status": "000", "list": rows}

    async def get_stock_total(self, cc, year, rc="11011"):
        self.calls.append(("stock", cc, year, rc))
        v = self.shares.get((cc, year))
        if v is None:
            raise DartClientError("013", "조회된 데이터가 없습니다.")
        return {"status": "000", "list": [{"se": "합계", "distb_stock_co": f"{v:,}"}]}


def _firm_client(*, years=("2025",)) -> FakeClient:
    acnt, shares = {}, {}
    for t, f in FIRMS.items():
        tot, ctl, rc = f["q3"]
        acnt[(f["cc"], "2025", "11014")] = _bs_rows(tot, ctl, rc, f["prev"])
        for y in years:
            shares[(f["cc"], y)] = f["shares"]
    return FakeClient(acnt, shares)


def _fm_payload(total, ctrl, case="BS_ID", *, with_key=True):
    s = {"total_equity_krw": total, "reprt_code": "11011"}
    if with_key:
        s.update({"controlling_equity_krw": ctrl, "controlling_equity_case": case})
    return {"status": "exact", "data": {"summary": s}}


@pytest.fixture
def patch(monkeypatch):
    """fake DART 클라이언트 + 재무 지표 도구(연말 길) 가짜."""
    state = {"fm_calls": []}

    def install(client, fm=None):
        monkeypatch.setattr(SC, "get_dart_client", lambda: client)

        async def fake_fm(name, *, scope="summary", year=None, **k):
            state["fm_calls"].append((name, scope, year))
            if fm is None:
                raise RuntimeError("재무 지표 호출 안 돼야 함")
            return fm(name, year) if callable(fm) else fm

        import open_proxy_mcp.services.financial_metrics as FMmod
        monkeypatch.setattr(FMmod, "build_financial_metrics_payload", fake_fm)
        return state

    return install


def _bps(ticker, **kw):
    f = FIRMS[ticker]
    args = dict(ref_ymd="20251124", ref_kind="period_end", fiscal_month=12, ticker=ticker)
    args.update(kw)
    return asyncio.run(SC._bps_at_cycle(f["name"], f["cc"], 2025, **args))


# ── 1. 지배자본 + 매입 기간 말 기준일 (1순위 길) ────────────────────────────────────────────────

@pytest.mark.parametrize("ticker,ratio", [("000880", 4.206), ("001040", 3.22), ("028260", 1.166)])
def test_bps_divides_by_controlling_equity_at_latest_filed_balance_sheet(patch, ticker, ratio):
    """매입 기간 말(2025-11-24)까지 공시된 최신 보고서 = 3분기보고서(기한 11-14). BPS 는 그 보고서 지배자본 ÷ 유통주식, 종전 총계 BPS 는 `bps_if_total_equity_krw`."""
    client = _firm_client()
    patch(client)
    r = _bps(ticker)
    f, b = FIRMS[ticker], r["bps_basis"]
    tot, ctl, rcept = f["q3"]
    assert r["bps_krw"] == round(ctl / f["shares"])
    assert b["bps_if_total_equity_krw"] == round(tot / f["shares"])
    assert round(b["bps_if_total_equity_krw"] / r["bps_krw"], 2) == pytest.approx(ratio, abs=0.01)
    assert b["equity"] == "controlling" and b["equity_case"].startswith("BS_ID")
    bs = b["balance_sheet"]
    assert bs["source"] == "latest_filed_as_of_reference_date"
    assert (bs["fiscal_year"], bs["reprt_code"], bs["report"], bs["period_end"]) == (2025, "11014", "3분기보고서", "2025-09-30")
    assert bs["reference_date"] == "2025-11-24" and bs["reference_kind"] == "period_end"
    assert bs["rcept_no"] == rcept
    assert b["fallback_reason"] is None


def test_one_dart_call_for_the_balance_sheet_per_cycle_and_no_financial_metrics(patch):
    """사이클당 재무상태표 DART 1콜(전체 재무제표) + 주식수 1콜(종전과 같다). 재무 지표 도구(연말 길)는 안 부른다."""
    client = _firm_client()
    st = patch(client)
    _bps("000880")
    assert client.calls == [("all", "00160588", "2025", "11014", "CFS"), ("stock", "00160588", "2025", "11011")]
    assert st["fm_calls"] == []


def test_amended_filing_after_reference_date_is_disclosed(patch):
    """한화 3분기: 원본 20251113 은 기간 말(11-24) 전이지만 응답은 기재정정본(20251128) — 접수일이 기준일 뒤임을 밝힌다. 기준일 전 접수(CJ)는 False."""
    patch(_firm_client())
    assert _bps("000880")["bps_basis"]["balance_sheet"]["filed_after_reference_date"] is True
    assert _bps("001040")["bps_basis"]["balance_sheet"]["filed_after_reference_date"] is False


@pytest.mark.parametrize("ref,fiscal_month,expect_year,expect_rc", [
    ("20251124", 12, "2025", "11014"),   # 3분기 기한(11-14) 지남
    ("20251110", 12, "2025", "11012"),   # 3분기 기한 전 → 반기보고서 (일찍 낸 3분기는 안 씀)
    ("20250310", 12, "2024", "11014"),   # 사업보고서(4/1) 전 → 전년 3분기
    ("20250402", 12, "2024", "11011"),   # 사업보고서 기한 지남
    ("20250815", 3, "2025", "11013"),    # 3월 결산: 2025 1분기 = 2025.06 기간 말, 8/14 지남
])
def test_report_chosen_by_filing_calendar(patch, ref, fiscal_month, expect_year, expect_rc):
    f = FIRMS["001040"]
    client = FakeClient({(f["cc"], expect_year, expect_rc): _bs_rows(*f["q3"][:2], f["q3"][2], f["prev"], reprt=expect_rc)},
                        {(f["cc"], "2025"): f["shares"]})
    patch(client)
    r = _bps("001040", ref_ymd=ref, fiscal_month=fiscal_month)
    assert r["bps_basis"]["balance_sheet"]["source"] == "latest_filed_as_of_reference_date"
    assert client.calls[0] == ("all", f["cc"], expect_year, expect_rc, "CFS")


def test_reference_falls_back_to_result_report_date_when_period_end_unknown(patch):
    """매입 기간 말을 모르는 결과보고서는 접수일(rcept_dt)을 기준일로 — 어느 쪽인지 `reference_kind` 에 남는다."""
    patch(_firm_client())
    r = _bps("001040", ref_ymd="20251201", ref_kind="rcept_dt")
    assert r["bps_basis"]["balance_sheet"]["reference_kind"] == "rcept_dt"
    assert r["bps_basis"]["balance_sheet"]["reference_date"] == "2025-12-01"


# ── 2. 종전 연말 길 (분기 재무제표를 못 쓸 때) ──────────────────────────────────────────────────

@pytest.mark.parametrize("ticker,ratio", [("000880", 4.09), ("001040", 3.27), ("028260", 1.15)])
def test_annual_fallback_uses_controlling_equity_key_of_financial_metrics(patch, ticker, ratio):
    """분기 재무제표 013 → 사업연도 말(2025) 재무 지표 응답의 `controlling_equity_krw` 로 나눈다. 한화 ÷4.09 · CJ ÷3.27 · 삼성물산 ÷1.15."""
    f = FIRMS[ticker]
    tot, ctl, _ = f["fy"]
    client = FakeClient({}, {(f["cc"], "2025"): f["shares"]})
    st = patch(client, _fm_payload(tot, ctl))
    r = _bps(ticker)
    assert r["bps_krw"] == round(ctl / f["shares"])
    assert r["bps_basis"]["bps_if_total_equity_krw"] == round(tot / f["shares"])
    assert round(r["bps_basis"]["bps_if_total_equity_krw"] / r["bps_krw"], 2) == pytest.approx(ratio, abs=0.01)
    b = r["bps_basis"]
    assert b["equity"] == "controlling"
    assert b["balance_sheet"]["source"] == "annual_year_end" and b["balance_sheet"]["report"] == "사업보고서"
    assert "조회 실패" in b["fallback_reason"] or "없음" in b["fallback_reason"]
    assert st["fm_calls"] == [(f["name"], "summary", 2025)]


def test_annual_fallback_without_new_key_keeps_old_value_and_warns(patch):
    """재무 지표 응답에 `controlling_equity_krw` 칸이 아예 없으면(옛 응답) 종전 값(자본총계)으로 나누되 경고를 남긴다 — 조용히 총계로 나누지 않는다."""
    f = FIRMS["000880"]
    tot, ctl, _ = f["fy"]
    patch(FakeClient({}, {(f["cc"], "2025"): f["shares"]}), _fm_payload(tot, ctl, with_key=False))
    r = _bps("000880")
    assert r["bps_krw"] == round(tot / f["shares"])
    assert r["bps_basis"]["equity"] == "total_incl_minority" and r["bps_basis"]["controlling_equity_krw"] is None
    assert "지배자본 칸이 없어" in r["warning"]


def test_annual_fallback_when_controlling_not_derivable_is_marked_total(patch):
    """재무 지표 도구가 지배자본을 못 구해 자본총계로 물러난 응답(`TOTAL_FALLBACK`)은 지배자본이라고 하지 않는다."""
    f = FIRMS["001040"]
    tot, _, _ = f["fy"]
    patch(FakeClient({}, {(f["cc"], "2025"): f["shares"]}), _fm_payload(tot, tot, case="TOTAL_FALLBACK"))
    r = _bps("001040")
    assert r["bps_krw"] == round(tot / f["shares"])
    assert r["bps_basis"]["equity"] == "total_incl_minority"
    assert "지배자본을 못 구해" in r["warning"]


@pytest.mark.parametrize("why,patch_rows,needle", [
    ("통화", lambda f: _bs_rows(*f["q3"][:2], f["q3"][2], f["prev"], currency="USD"), "원화가 아님"),
    ("단위", lambda f: _bs_rows(f["q3"][0] * 1000, f["q3"][1] * 1000, f["q3"][2], f["prev"]), "단위 오류 의심"),
    ("지배자본 못 고름", lambda f: [{"rcept_no": "x", "sj_div": "BS", "account_id": "ifrs-full_Assets", "account_nm": "자산총계",
                                  "thstrm_amount": "1", "ord": "1", "currency": "KRW"}], "지배자본을 못 고름"),
])
def test_quarter_balance_sheet_not_trusted_falls_back_to_year_end(patch, why, patch_rows, needle):
    """보고 통화가 원화가 아니거나 · 같은 응답 안 전기말과 10³배 점프(단위 오류) · 자본 줄이 없으면 그 보고서를 안 쓰고 사업연도 말로 물러난다(사유가 `fallback_reason`)."""
    f = FIRMS["001040"]
    client = FakeClient({(f["cc"], "2025", "11014"): patch_rows(f)}, {(f["cc"], "2025"): f["shares"]})
    patch(client, _fm_payload(*f["fy"][:2]))
    r = _bps("001040")
    assert needle in r["bps_basis"]["fallback_reason"]
    assert r["bps_basis"]["balance_sheet"]["source"] == "annual_year_end"
    assert r["bps_krw"] == round(f["fy"][1] / f["shares"])


def test_no_reference_date_uses_year_end_and_says_so(patch):
    f = FIRMS["001040"]
    patch(FakeClient({}, {(f["cc"], "2025"): f["shares"]}), _fm_payload(*f["fy"][:2]))
    r = _bps("001040", ref_ymd=None, ref_kind="rcept_dt")
    assert "기준일을 못 정함" in r["bps_basis"]["fallback_reason"]


# ── 3. 주식수 ─────────────────────────────────────────────────────────────────────────────────

def test_shares_fall_back_to_latest_annual_report_filed_by_reference_date(patch):
    """그 해 사업보고서가 아직 없는 사이클(2025 보고서는 2026 에 접수)은 종전엔 BPS 없음 — 기준일에 공시돼 있던 최신 사업보고서(2024)의 주식수로 대신한다."""
    f = FIRMS["001040"]
    client = FakeClient({(f["cc"], "2025", "11014"): _bs_rows(*f["q3"][:2], f["q3"][2], f["prev"])},
                        {(f["cc"], "2024"): 33_600_000})
    patch(client)
    r = _bps("001040")
    assert r["bps_krw"] == round(f["q3"][1] / 33_600_000)
    assert r["bps_basis"]["shares_fiscal_year"] == 2024 and "대신함" in r["bps_basis"]["shares_basis"]
    assert [c[:3] for c in client.calls if c[0] == "stock"] == [("stock", f["cc"], "2025"), ("stock", f["cc"], "2024")]


def test_no_shares_means_no_bps_not_a_wrong_one(patch):
    f = FIRMS["001040"]
    patch(FakeClient({(f["cc"], "2025", "11014"): _bs_rows(*f["q3"][:2], f["q3"][2], f["prev"])}, {}))
    r = _bps("001040")
    assert r["bps_krw"] is None and r["bps_basis"]["shares_total"] is None


# ── 4. 비지배 없는 회사는 변화 0 ──────────────────────────────────────────────────────────────

def test_company_without_minority_has_no_change(patch):
    """비지배지분이 없는 회사 — 지배 줄도 비지배 줄도 없이 자본총계만 있어도(`TOTAL`) 지배자본 = 자본총계라 BPS 가 종전과 같다(변화 0)."""
    cc, total, shares = "00999999", 300_000_000_000, 10_000_000
    rows = _bs_rows(total, total, "20251114009999", (280_000_000_000, 280_000_000_000), with_ctrl_row=False, nci=False)
    patch(FakeClient({(cc, "2025", "11014"): rows}, {(cc, "2025"): shares}))
    r = asyncio.run(SC._bps_at_cycle("무비지배", cc, 2025, ref_ymd="20251124", ref_kind="period_end", fiscal_month=12, ticker="999999"))
    assert r["bps_krw"] == r["bps_basis"]["bps_if_total_equity_krw"] == round(total / shares) == 30_000
    assert r["bps_basis"]["equity_case"] == "TOTAL"


# ── 5. 사이클 결과 — 프리미엄/디스카운트 · 장부가 손익 · 문장 ────────────────────────────────────────

def _treasury(ticker, *, shares, price, period_end="2025-11-24"):
    """소각 목적 취득결정 하나 + 그 실행결과(가정한 매입가) — 사이클 하나."""
    amount = shares * price
    return {"events": [
        {"event": "acquisition_decision", "phase": "decision", "rcept_no": "D1", "for_cancelation": True, "amount_krw": amount},
        {"event": "acquisition_result", "rcept_no": "R1", "linked_decision_rcept_no": "D1", "actual_amount_krw": amount,
         "cumulative_shares": shares, "period_start": "2025-08-25", "period_end": period_end, "rcept_dt": "20251201"},
    ]}


def _cycle(patch, ticker, *, price, shares=1_000_000, **kw):
    patch(_firm_client())
    f = FIRMS[ticker]
    cycles, flags = asyncio.run(SC._capital_return_impact(
        f["name"], f["cc"], _treasury(ticker, shares=shares, price=price, **kw), fiscal_month=12, ticker=ticker))
    assert len(cycles) == 1
    return cycles[0], flags


def test_holding_company_cycle_flips_from_gain_to_loss(patch):
    """한화: 가정한 매입가 150,000원 × 100만 주. 종전 BPS(총계) 517,723원이면 「−71.03%, 이득 3,677.23억」이었으나 지배자본 BPS 123,100원이면 「+21.85%, 손실 269.00억」."""
    c, flags = _cycle(patch, "000880", price=150_000)
    assert c["bps_at_acquisition_krw"] == 123_100
    assert c["premium_discount_pct"] == round((150_000 / 123_100 - 1) * 100, 2) == 21.85
    assert c["book_value_gain_loss_krw"] == (123_100 - 150_000) * 1_000_000 == -26_900_000_000
    assert "손실" in c["note"] and "이득" not in c["note"]
    old_bps = c["bps_basis"]["bps_if_total_equity_krw"]
    assert old_bps == 517_723
    assert round((150_000 / old_bps - 1) * 100, 2) == -71.03
    assert round((old_bps - 150_000) * 1_000_000) == 367_723_000_000   # 종전: 3,677.23억 「장부가 기준 이득」
    assert "bps_basis" in c and c["bps_basis"]["equity"] == "controlling"
    assert flags == []


def test_cycle_for_company_without_big_minority_barely_moves(patch):
    """삼성물산(비지배 ÷1.17): 가정한 매입가 200,000원 — 이득에서 이득으로, 크기만 변한다(종전 BPS 294,965 → 252,895)."""
    c, _ = _cycle(patch, "028260", price=200_000)
    assert c["bps_at_acquisition_krw"] == 252_895
    assert c["book_value_gain_loss_krw"] == (252_895 - 200_000) * 1_000_000
    assert "이득" in c["note"]


def test_cycle_reference_is_purchase_period_end_not_report_date(patch):
    """결과보고서는 매입이 끝난 뒤(12-01)에 접수돼도 기준일은 매입 기간 말(11-10) — 11-10 에는 3분기 기한(11-14) 전이라 반기보고서(11012)를 쓴다."""
    f = FIRMS["001040"]
    q2 = (16_900_000_000_000, 5_100_000_000_000, "20250814000999")
    client = FakeClient({(f["cc"], "2025", "11012"): _bs_rows(*q2, f["prev"], reprt="11012")}, {(f["cc"], "2025"): f["shares"]})
    patch(client)
    cycles, _ = asyncio.run(SC._capital_return_impact(
        f["name"], f["cc"], _treasury("001040", shares=1_000_000, price=150_000, period_end="2025-11-10"), fiscal_month=12, ticker="001040"))
    bs = cycles[0]["bps_basis"]["balance_sheet"]
    assert (bs["reprt_code"], bs["reference_kind"], bs["reference_date"]) == ("11012", "period_end", "2025-11-10")
    assert cycles[0]["bps_at_acquisition_krw"] == round(q2[1] / f["shares"])


def test_cycle_fallback_leaves_a_quality_flag(patch):
    """분기 재무제표를 못 써 연말 길로 물러난 사이클은 `data_quality_flags` 에 사유를 남긴다(같은 사유를 사이클 접수번호와 함께)."""
    f = FIRMS["001040"]
    client = FakeClient({}, {(f["cc"], "2025"): f["shares"]})
    patch(client, _fm_payload(*f["fy"][:2]))
    cycles, flags = asyncio.run(SC._capital_return_impact(
        f["name"], f["cc"], _treasury("001040", shares=1_000_000, price=150_000), fiscal_month=12, ticker="001040"))
    assert cycles[0]["bps_at_acquisition_krw"] == round(f["fy"][1] / f["shares"])
    assert any("BPS 기준일 폴백" in x and "rcept=R1" in x for x in flags)


def test_cycle_total_equity_fallback_leaves_a_quality_flag(patch):
    f = FIRMS["001040"]
    patch(FakeClient({}, {(f["cc"], "2025"): f["shares"]}), _fm_payload(*f["fy"][:2], with_key=False))
    cycles, flags = asyncio.run(SC._capital_return_impact(
        f["name"], f["cc"], _treasury("001040", shares=1_000_000, price=150_000), fiscal_month=12, ticker="001040"))
    assert cycles[0]["bps_basis"]["equity"] == "total_incl_minority"
    assert any("지배자본 칸이 없어" in x for x in flags)


def test_negative_controlling_equity_gives_no_gain_loss_numbers(patch):
    """지배자본이 음수(자본잠식)면 BPS 도 음수 — 프리미엄·손익을 만들지 않고 사유를 남긴다(종전 총계 BPS 는 양수일 수 있다)."""
    cc, total, ctrl, shares = "00999998", 50_000_000_000, -10_000_000_000, 10_000_000
    rows = _bs_rows(total, ctrl, "20251114009998", (60_000_000_000, -5_000_000_000))
    patch(FakeClient({(cc, "2025", "11014"): rows}, {(cc, "2025"): shares}))
    cycles, flags = asyncio.run(SC._capital_return_impact("잠식", cc, _treasury("999998", shares=100_000, price=1_000), fiscal_month=12, ticker="999998"))
    c = cycles[0]
    assert c["bps_at_acquisition_krw"] == -1_000
    assert "premium_discount_pct" not in c and "book_value_gain_loss_krw" not in c
    assert "자본잠식" in c["note"] and any("BPS 0 이하" in x for x in flags)


def test_malformed_reference_date_falls_back_instead_of_crashing(patch):
    f = FIRMS["001040"]
    patch(FakeClient({}, {(f["cc"], "2025"): f["shares"]}), _fm_payload(*f["fy"][:2]))
    r = _bps("001040", ref_ymd="20251399")
    assert "기준일 형식 이상" in r["bps_basis"]["fallback_reason"] and r["bps_krw"] == round(f["fy"][1] / f["shares"])


# ── 6. 낡은 주석 ──────────────────────────────────────────────────────────────────────────────

def test_no_stale_valuation_py_module_name():
    """옛 모듈명 `valuation.py` 는 `price_multiple_data.py` 로 바뀐 지 오래다 — 이 파일의 주석이 없는 모듈을 가리키지 않는다."""
    src = Path(inspect.getsourcefile(SC)).read_text(encoding="utf-8")
    assert "valuation.py" not in src
    assert "price_multiple_data.py" in src
