"""분기 추이의 「미제출」은 DART 가 「없다」(013)고 답한 분기에만 — 조회 실패(020 하루 한도 등)는 「조회 실패(미확인)」. DATA-API1·DATA-U3.

종전엔 한 분기의 보고서 조회가 020(키의 하루 한도)·800(점검)·전송 오류로 실패해도 그 분기가 표에 없다는 이유만으로
quarterly_status 가 「미제출」로 적었다 — 실제로는 공시된 보고서를 읽지 못한 것이다. network 0 · DB 0.
"""
from __future__ import annotations

import asyncio
from datetime import date

from open_proxy_mcp.services import financial_metrics as FM
from open_proxy_mcp.services.company import CompanyResolution
from open_proxy_mcp.services.contracts import AnalysisStatus

CO = {"corp_code": "00000001", "corp_name": "시험회사", "stock_code": "000001"}
TODAY = date(2026, 10, 21)
_NO = ([], "no_filing")
_FAIL = ([], "fnlttSinglAcnt(11014, CFS) 실패: 020 사용한도를 초과하였습니다")


def _q(year: int, end: str, rev: str) -> list[dict]:
    base = {"fs_div": "CFS", "thstrm_dt": end, "ord": "1"}
    return [
        {**base, "sj_div": "BS", "account_nm": "자산총계", "thstrm_amount": "1000"},
        {**base, "sj_div": "BS", "account_nm": "자본총계", "thstrm_amount": "500"},
        {**base, "sj_div": "IS", "account_nm": "매출액", "thstrm_amount": rev, "thstrm_add_amount": rev},
        {**base, "sj_div": "IS", "account_nm": "영업이익", "thstrm_amount": "10", "thstrm_add_amount": "10"},
        {**base, "sj_div": "IS", "account_nm": "당기순이익(손실)", "thstrm_amount": "5", "thstrm_add_amount": "5"},
    ]


def _status_of(missing: list[dict], q: str) -> str | None:
    return next((m["status"] for m in missing if m["fiscal_quarter"] == q), None)


def _payload(monkeypatch, q3):
    """2026 Q1·Q2 는 받았고 Q3(11014)는 q3 로 답한다(013 이면 (빈, no_filing), 020 이면 (빈, 오류))."""
    monkeypatch.setenv("OPENDART_API_KEY", "test-key-not-real")
    monkeypatch.setattr(FM, "today_kst", lambda: TODAY)

    async def resolve(query, *a, **k):
        return CompanyResolution(status=AnalysisStatus.EXACT, query=query, selected=dict(CO), candidates=[])

    async def info(cc):
        return {"acc_mt": "12", "induty_code": "471"}, None

    async def acnt(cc, y, rc, fs):
        if (y, rc) == (2026, "11013"):
            return _q(2026, "2026.03.31", "100"), None
        if (y, rc) == (2026, "11012"):
            return _q(2026, "2026.06.30", "110"), None
        if (y, rc) == (2026, "11014"):
            return q3
        return _NO

    async def none(*a, **k):
        return None

    monkeypatch.setattr(FM, "resolve_company_query", resolve)
    monkeypatch.setattr(FM, "_safe_company_info", info)
    monkeypatch.setattr(FM, "_safe_fetch_acnt", acnt)
    monkeypatch.setattr(FM, "_periodic_filing_ref", none)
    monkeypatch.setattr(FM, "_FM_CACHE", type(FM._FM_CACHE)(16 * 1024 * 1024, 300.0, "test-fm-qfail"))
    return asyncio.run(FM.build_financial_metrics_payload("시험회사", scope="quarterly", year=2026))


def test_q3_fetch_failure_is_unconfirmed_not_unsubmitted(monkeypatch):
    p = _payload(monkeypatch, _FAIL)
    missing = p["data"]["quarterly_status"]["missing"]
    assert _status_of(missing, "Q3") == "조회 실패(미확인)"      # 옛 값: 「미제출」
    assert not any("Q3" in w and "미제출로 표시" in w for w in p["warnings"])
    assert any("2026-Q3" in w and "확인하지 못했다" in w for w in p["warnings"])
    assert next(m for m in missing if m["fiscal_quarter"] == "Q3")["reprt_code"] == "11014"


def test_q3_really_absent_is_still_unsubmitted(monkeypatch):
    p = _payload(monkeypatch, _NO)                                # 013 = DART 의 답: 보고서 없음
    missing = p["data"]["quarterly_status"]["missing"]
    assert _status_of(missing, "Q3") == "미제출"
    assert any("2026-Q3" in w and "미제출로 표시" in w for w in p["warnings"])


def test_status_function_marks_only_the_failed_quarter():
    st = FM._quarterly_status([{"fiscal_year": 2026, "fiscal_quarter": "Q1"}], 2026, 12, as_of=TODAY,
                              failed={(2026, "11012")})
    assert _status_of(st["missing"], "Q2") == "조회 실패(미확인)"
    assert _status_of(st["missing"], "Q3") == "미제출"            # 실패하지 않은 빈 분기는 그대로


def test_non_december_fiscal_year_is_conservative_about_dart_business_year():
    # 6월 결산: DART 사업연도와 회계연도가 한 해 어긋날 수 있어 앞뒤 한 해의 같은 보고서 실패도 의심한다.
    st = FM._quarterly_status([], 2026, 6, as_of=date(2026, 10, 1), failed={(2025, "11014")})
    assert _status_of(st["missing"], "Q3") == "조회 실패(미확인)"
    st2 = FM._quarterly_status([], 2026, 6, as_of=date(2026, 10, 1), failed={(2023, "11014")})
    assert _status_of(st2["missing"], "Q3") == "미제출"


def test_default_call_without_failed_is_unchanged():
    st = FM._quarterly_status([], 2026, 12, as_of=TODAY)
    assert _status_of(st["missing"], "Q3") == "미제출"
