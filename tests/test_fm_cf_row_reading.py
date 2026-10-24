"""현금흐름표 세 줄(영업CF·유형자산 취득·배당 지급)은 계정 id(local-name)로 먼저 읽는다 — 이름 부분 일치는 그 뒤 폴백.

옛 코드는 이름 부분 일치 선착순이라: 이름이 패턴 밖인 영업CF 는 None(→ FCF 가 −CapEx, 부호 반대), 「기타유형자산의 취득」(0)이
「유형자산의 취득」보다 먼저 걸려 capex=0, 「신종자본증권 배당금의 지급」이 「배당금의 지급」보다 먼저 걸리고 「현금배당」「배당의 지급」은 못 읽었다.
FCF 는 영업CF·설비투자 둘 다 있을 때만 낸다(한쪽이 비면 비우고 사유를 기존 경고 목록에 한 줄). network 0 · DB 0.
행 모양(id·이름·순서)은 실제 DART 전체 계정 응답에서 가져왔다 — 금액만 줄였다.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import open_proxy_mcp.services.financial_metrics as fm  # noqa: E402

CFO = "ifrs-full_CashFlowsFromUsedInOperatingActivities"
OPS = "ifrs-full_CashFlowsFromUsedInOperations"
PPE = "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"
OTHER_PPE = "dart_PurchaseOfOtherPropertyPlantAndEquipment"
DIV = "ifrs-full_DividendsPaidClassifiedAsFinancingActivities"
HYBRID_DIV = "dart_DividendsPaidToHybridBond"
NO_CODE = "-표준계정코드 미사용-"


def _cf(aid: str, nm: str, cur: str, prev: str = "0") -> dict:
    return {"sj_div": "CF", "account_id": aid, "account_nm": nm, "thstrm_amount": cur, "frmtrm_amount": prev,
            "fs_div": "CFS", "ord": "1"}


def _read(rows: list[dict], key: str, period: str = "thstrm"):
    return fm._build_account_map_all(rows, period=period)[key]


# ── 영업CF ──
def test_cfo_is_read_by_account_id_when_the_name_is_outside_the_patterns():
    rows = [_cf(OPS, "영업에서 창출된 현금흐름", "300"), _cf(CFO, "영업활동으로부터의 현금흐름", "4020")]
    assert _read(rows, "cfo") == 4020


def test_cfo_old_taxonomy_prefix_is_read_too():
    rows = [_cf("ifrs_CashFlowsFromUsedInOperatingActivities", "영업활동순현금흐름", "4020")]
    assert _read(rows, "cfo") == 4020


def test_cfo_falls_back_to_the_name_when_there_is_no_account_id():
    rows = [_cf(NO_CODE, "영업활동현금흐름", "777")]
    assert _read(rows, "cfo") == 777


# ── 설비투자 ──
def test_capex_skips_the_other_ppe_row_that_comes_first():
    rows = [_cf(OTHER_PPE, "기타유형자산의 취득", "0", "38"), _cf(PPE, "유형자산의 취득", "1350", "2100")]
    assert _read(rows, "capex") == 1350
    assert _read(rows, "capex", "frmtrm") == 2100


def test_capex_without_account_id_still_reads_by_name():
    rows = [_cf(NO_CODE, "유형자산의취득", "-794")]
    assert _read(rows, "capex") == -794


# ── 배당 지급 ──
def test_dividends_skip_the_hybrid_bond_row_that_comes_first():
    rows = [_cf(HYBRID_DIV, "신종자본증권 배당금의 지급", "199"), _cf(DIV, "배당금의 지급", "1487")]
    assert _read(rows, "dividends_paid") == 1487


@pytest.mark.parametrize("name", ["현금배당", "배당의 지급", "배당금 지급"])
def test_dividends_are_read_whatever_the_row_is_called(name):
    assert _read([_cf(DIV, name, "434")], "dividends_paid") == 434


def test_dividends_none_when_there_is_no_dividend_row():
    assert _read([_cf(CFO, "영업활동현금흐름", "10")], "dividends_paid") is None


# ── FCF ──
def _metrics(detail: dict) -> dict:
    return fm._compute_metrics(
        bs_is={"net_income": 100, "total_equity": 600, "total_assets": 1000, "revenue": 500, "operating_profit": 50},
        bs_is_prev=None, detail=detail, detail_prev=None, indx_map=None)


def test_fcf_needs_both_cfo_and_capex():
    assert _metrics({"cfo": 120, "capex": -20})["fcf_krw"] == 100
    assert _metrics({"cfo": 120, "capex": 0})["fcf_krw"] == 120          # 설비투자 0 은 읽은 값이다
    only_cfo = _metrics({"cfo": 120, "capex": None})
    assert only_cfo["fcf_krw"] is None and only_cfo["cfo_krw"] == 120    # 키는 그대로, 값만 비움
    assert only_cfo["fcf_margin_pct"] is None and only_cfo["dividend_to_fcf_pct"] is None
    only_capex = _metrics({"cfo": None, "capex": -20})
    assert only_capex["fcf_krw"] is None and only_capex["capex_krw"] == -20
    assert _metrics({"cfo": None, "capex": None})["fcf_krw"] is None


# ── 사유 줄(기존 경고 목록, 새 키 없음) ──
_NO = ([], "no_filing")


def _acnt() -> list[dict]:
    base = {"fs_div": "CFS", "thstrm_dt": "2021.12.31", "ord": "1"}
    return [
        {**base, "sj_div": "BS", "account_nm": "자산총계", "thstrm_amount": "1000"},
        {**base, "sj_div": "BS", "account_nm": "자본총계", "thstrm_amount": "500"},
        {**base, "sj_div": "IS", "account_nm": "매출액", "thstrm_amount": "800"},
        {**base, "sj_div": "IS", "account_nm": "영업이익", "thstrm_amount": "80"},
        {**base, "sj_div": "IS", "account_nm": "당기순이익", "thstrm_amount": "60"},
    ]


def _acnt_all(cf: list[dict]) -> list[dict]:
    return [{"fs_div": "CFS", "sj_div": "CIS", "thstrm_dt": "2021.12.31", "ord": "1", "account_id": "ifrs-full_Revenue",
             "account_nm": "매출액", "thstrm_amount": "800", "thstrm_add_amount": "800"}] + cf


def _run(monkeypatch, cf_cur: list[dict], rc: str = "11011", cf_prior: list[dict] | None = None):
    async def fake_acnt(corp_code, year, reprt_code, fs_div):
        return (_acnt(), None) if (year == 2021 and reprt_code == rc) else _NO

    async def fake_acnt_all(corp_code, year, reprt_code, fs_div):
        if year == 2021 and reprt_code == rc:
            return _acnt_all(cf_cur), None
        if year == 2021 and cf_prior is not None:
            return _acnt_all(cf_prior), None
        return _NO

    async def fake_ref(*a, **k):
        return None

    monkeypatch.setattr(fm, "_safe_fetch_acnt", fake_acnt)
    monkeypatch.setattr(fm, "_safe_fetch_acnt_all", fake_acnt_all)
    monkeypatch.setattr(fm, "_periodic_filing_ref", fake_ref)
    monkeypatch.setattr(fm, "_accrual_payout_pct", fake_ref)
    return asyncio.run(fm._fetch_year_metrics("00000000", 2021, "CFS", induty_code="52999"))


def test_fcf_blank_adds_one_reason_line_and_no_new_key(monkeypatch):
    both = [_cf(CFO, "영업활동현금흐름", "400"), _cf(PPE, "유형자산의 취득", "100")]
    m_ok, w_ok, _ = _run(monkeypatch, both)
    assert m_ok["fcf_krw"] == 300
    assert not any("FCF" in w for w in w_ok)

    m, w, _ = _run(monkeypatch, [_cf(CFO, "영업활동현금흐름", "400")])
    assert m["cfo_krw"] == 400 and m["capex_krw"] is None and m["fcf_krw"] is None
    lines = [x for x in w if "FCF" in x]
    assert len(lines) == 1 and "2021" in lines[0] and "유형자산" in lines[0]
    assert set(m) == set(m_ok)                                            # 지표 묶음에 새 키 없음


def test_standalone_blank_fcf_gets_its_own_reason_line(monkeypatch):
    cf = [_cf(CFO, "영업활동현금흐름", "400")]
    m, w, _ = _run(monkeypatch, cf, rc="11012", cf_prior=[_cf(CFO, "영업활동현금흐름", "150")])
    assert m["standalone"]["cfo_krw"] == 250 and m["standalone"]["fcf_krw"] is None
    assert len([x for x in w if "FCF" in x]) == 2                        # 누적 한 줄 + 당기 분기 단독 한 줄
    assert any("단독" in x for x in w)
