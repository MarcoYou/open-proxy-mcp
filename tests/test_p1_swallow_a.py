# -*- coding: utf-8 -*-
"""조회 실패를 「없음」으로 삼키던 것(P1 묶음 1 — A) — dividend_disclosure · treasury_share · financial_metrics · 공시 원장 배치.

실제 오류 종류(`DartClientError` 020 한도·800 점검·014 원문 없음·STRICT_PIT_UNAVAILABLE·전송 예외)를 주입해,
읽지 못한 것이 「없음」·exact 로 나가지 않고 status partial(공시 0건이면 error)·경고로 나가는지 본다. network 0 · DB 0.

판정·문구는 `ownership_structure._dart_read_note` 하나다 — 이 시험은 문구를 단정하지 않고 같은 함수를 불러 기대값을 만든다.
JSON API 는 013 만 「없음」, 원문(document.xml)은 첨부정정([첨부정정]·[첨부추가]) 제목의 014·404 만 「없음」.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

import pytest

from open_proxy_mcp.dart.client import DartClientError
from open_proxy_mcp.services import dividend as dv
from open_proxy_mcp.services import financial_metrics as FM
from open_proxy_mcp.services import ownership_structure as own
from open_proxy_mcp.services import treasury_share as ts
from open_proxy_mcp.services.company import CompanyResolution
from open_proxy_mcp.services.contracts import AnalysisStatus

_DECISION_TITLE = "현금ㆍ현물배당결정"
_CANCEL_TITLE = "주요사항보고서(자기주식소각결정)"
_FAIL_STATUSES = ["020", "800", "014"]          # JSON 에서는 013 만 「없음」이라 014 도 실패다


def _tail(exc: Exception, *, attachment: bool = False) -> str:
    """`_dart_read_note` 가 이 오류에 붙이는 안내 꼬리(「 — …」). 실패면 경고가 이 꼬리로 끝난다."""
    bad, text = own._dart_read_note(exc, empty_text="", fail_text="", attachment=attachment)
    assert bad
    return text


def _resolution(name: str = "합성") -> CompanyResolution:
    return CompanyResolution(
        status=AnalysisStatus.EXACT, query=name, candidates=[],
        selected={"corp_name": name, "stock_code": "000001", "corp_code": "00000001"})


# ── 1. dividend — #15 분기 alotMatter 조회 실패 ────────────────────────────────────────────────
class _AlotClient:
    """by_code: 보고서 코드 → 보통주 누적 DPS(int) | 예외. 없는 코드는 013."""

    def __init__(self, by_code: dict):
        self.by_code = by_code

    def api_call_snapshot(self):
        return 0

    async def get_dividend_info(self, corp_code, year, reprt_code):
        v = self.by_code.get(reprt_code)
        if isinstance(v, Exception):
            raise v
        if v is None:
            raise DartClientError("013", "조회된 데이터가 없습니다")
        return {"list": [
            {"se": "주당 현금배당금(원)", "stock_knd": "보통주", "thstrm": f"{v:,}", "stlm_dt": "2025-12-31"},
            {"se": "현금배당금총액(백만원)", "stock_knd": "-", "thstrm": "1,000", "stlm_dt": "2025-12-31"}]}


_CUM = {"11013": 361, "11012": 722, "11014": 1083, "11011": 1444}


@pytest.mark.parametrize("status", _FAIL_STATUSES)
def test_cumulative_table_does_not_call_an_unreadable_report_missing(monkeypatch, status):
    exc = DartClientError(status, "x")
    monkeypatch.setattr(dv, "get_dart_client", lambda: _AlotClient({**_CUM, "11012": exc}))
    failed: list[str] = []
    rows = asyncio.run(dv._quarterly_full_from_cumulative("00000001", 2025, failed))
    # 종전: 반기보고서 조회가 013 이든 020·800 이든 같은 행 「Q2+Q3 합산(반기보고서 없음)」 — 읽지 못한 것이 「없음」 라벨을 달았다.
    assert [r["quarter"] for r in rows] == ["Q1", "결산"]
    assert len(failed) == 1 and failed[0].endswith(_tail(exc))


def test_cumulative_table_keeps_the_missing_label_for_a_real_013(monkeypatch):
    monkeypatch.setattr(dv, "get_dart_client", lambda: _AlotClient({**_CUM, "11012": DartClientError("013", "x")}))
    failed: list[str] = []
    rows = asyncio.run(dv._quarterly_full_from_cumulative("00000001", 2025, failed))
    assert [r["quarter"] for r in rows] == ["Q1", "Q2+Q3 합산(반기보고서 없음)", "결산"]
    assert failed == []
    # failed 를 안 주는 옛 호출도 같은 표
    assert asyncio.run(dv._quarterly_full_from_cumulative("00000001", 2025)) == rows


@pytest.mark.parametrize("status", _FAIL_STATUSES)
def test_cumulative_dps_records_the_unreadable_report(monkeypatch, status):
    exc = DartClientError(status, "x")
    monkeypatch.setattr(dv, "get_dart_client", lambda: _AlotClient({**_CUM, "11011": exc}))
    failed: list[str] = []
    out = asyncio.run(dv._quarterly_dps_from_cumulative("00000001", 2025, failed))
    assert [q["quarter"] for q in out] == ["Q1", "Q2", "Q3"]            # 결산은 사업보고서 누계가 없어 차분 불가(종전과 같다)
    assert len(failed) == 1 and failed[0].endswith(_tail(exc))
    ok: list[str] = []
    monkeypatch.setattr(dv, "get_dart_client", lambda: _AlotClient({**_CUM, "11011": DartClientError("013", "x")}))
    asyncio.run(dv._quarterly_dps_from_cumulative("00000001", 2025, ok))
    assert ok == []


def test_interim_dividend_records_only_real_failures(monkeypatch):
    exc = DartClientError("800", "점검")
    monkeypatch.setattr(dv, "get_dart_client", lambda: _AlotClient({"11014": exc}))
    failed: list[str] = []
    assert asyncio.run(dv._interim_dividend_from_quarterly("00000001", 2025, failed)) is None
    assert len(failed) == 1 and failed[0].endswith(_tail(exc))
    monkeypatch.setattr(dv, "get_dart_client", lambda: _AlotClient({}))     # 셋 다 013 — 「중간배당 없음」은 답이다
    ok: list[str] = []
    assert asyncio.run(dv._interim_dividend_from_quarterly("00000001", 2025, ok)) is None
    assert ok == []


# ── 2. dividend — #16 결정공시 본문 조회 실패 ──────────────────────────────────────────────────
class _DocClient:
    def __init__(self, error: Exception | None = None, text: str = "본문"):
        self.error, self.text = error, text

    def api_call_snapshot(self):
        return 0

    async def get_document_cached(self, rcept_no):
        if self.error is not None:
            raise self.error
        return {"text": self.text}


def _decision_failed(monkeypatch, title: str, exc: Exception | None, text: str = "본문") -> list[str]:
    monkeypatch.setattr(dv, "get_dart_client", lambda: _DocClient(exc, text))
    monkeypatch.setattr(dv, "parse_dividend_decision", lambda t: None)
    failed: list[str] = []
    out = asyncio.run(dv._decision_details(
        [{"rcept_no": "20260130800001", "rcept_dt": "20260130", "report_nm": title}], failed))
    assert out == []
    return failed


@pytest.mark.parametrize("status", ["800", "020", "014", "404"])
def test_decision_body_failure_is_recorded(monkeypatch, status):
    exc = DartClientError(status, "x")
    failed = _decision_failed(monkeypatch, _DECISION_TITLE, exc)
    assert len(failed) == 1 and failed[0].endswith(_tail(exc))


@pytest.mark.parametrize("title", ["[첨부정정]" + _DECISION_TITLE, "[첨부추가]" + _DECISION_TITLE])
@pytest.mark.parametrize("status", ["014", "404"])
def test_attachment_amendment_without_a_body_is_not_a_failure(monkeypatch, title, status):
    # 본문 없이 첨부만 낸 정정 — 원 공시에 값이 있다. 종전처럼 조용히 빠진다(경고·partial 없음).
    assert _decision_failed(monkeypatch, title, DartClientError(status, "x")) == []


def test_attachment_title_does_not_excuse_a_real_failure(monkeypatch):
    exc = DartClientError("800", "점검")
    failed = _decision_failed(monkeypatch, "[첨부정정]" + _DECISION_TITLE, exc)
    assert len(failed) == 1 and failed[0].endswith(_tail(exc))


def test_strict_point_in_time_is_a_failure_without_a_retry_hint(monkeypatch):
    exc = DartClientError("STRICT_PIT_UNAVAILABLE", "기준 시점")
    failed = _decision_failed(monkeypatch, _DECISION_TITLE, exc)
    assert len(failed) == 1 and failed[0].endswith(_tail(exc))
    assert "잠시" not in failed[0]


def test_transport_exception_and_empty_body_are_failures(monkeypatch):
    assert len(_decision_failed(monkeypatch, _DECISION_TITLE, RuntimeError("boom"))) == 1
    assert len(_decision_failed(monkeypatch, _DECISION_TITLE, None, text="")) == 1      # 빈 본문
    assert _decision_failed(monkeypatch, _DECISION_TITLE, None, text="다른 서식") == []   # 비어 있지 않은 본문의 None 은 종전대로


# ── 3. dividend — 응답 봉투: 상태 칸과 본문 칸이 같은 판정 (C1) ─────────────────────────────────
def _dividend_payload(monkeypatch, *, scope="summary", year=2025, summary=None, decisions=None, client=None,
                      notices=None, annual_by_year=None, real_annual=False):
    """build_dividend_payload 를 DART 0콜로. decisions 는 결정공시 목록(list.json 항목) — 본문은 client 가 답한다."""
    cli = client or _DocClient(DartClientError("800", "점검"))

    async def resolve(_q):
        return _resolution()

    async def annual(_c, y):
        if annual_by_year is not None:
            return annual_by_year.get(y, ({}, None))
        return (summary or {}), None

    async def search(*_a, **_k):
        return list(decisions or []), list(notices or []), [], None

    async def capital(*_a, **_k):
        return False, []

    monkeypatch.setattr(dv, "today_kst", lambda: date(2026, 10, 23))
    monkeypatch.setattr(dv, "get_dart_client", lambda: cli)
    monkeypatch.setattr(dv, "resolve_company_query", resolve)
    if not real_annual:                     # real_annual: 사업보고서 요약을 client 의 alotMatter 응답에서 실제 파서로 만든다
        monkeypatch.setattr(dv, "_annual_summary", annual)
    monkeypatch.setattr(dv, "_search_dividend_filings", search)
    monkeypatch.setattr(dv, "_detect_capital_reserve_reduction", capital)
    monkeypatch.setattr(dv, "parse_dividend_decision", lambda t: None)
    return asyncio.run(dv.build_dividend_payload("합성", scope=scope, year=year, years=3))


_SUMMARY = {"period": "2025 사업보고서(기말)", "stlm_dt": "2025-12-31", "cash_dps": 1000, "total_dps": 1000, "source": "alotMatter"}
_FILING = {"rcept_no": "20260130800001", "rcept_dt": "20260130", "report_nm": _DECISION_TITLE}


def test_envelope_partial_when_a_decision_body_is_unreadable(monkeypatch):
    exc = DartClientError("800", "점검")
    p = _dividend_payload(monkeypatch, summary=_SUMMARY, decisions=[_FILING], client=_DocClient(exc))
    d = p["data"]
    assert p["status"] == "partial"                                          # 종전: exact
    assert (d["filing_status"], d["parsing_failures"], d["no_filing"]) == ("partial_failure", 1, False)
    assert any(w.endswith(_tail(exc)) for w in p["warnings"])


def test_envelope_error_not_no_filing_when_nothing_was_read_and_a_body_failed(monkeypatch):
    p = _dividend_payload(monkeypatch, summary={}, decisions=[_FILING])
    d = p["data"]
    assert p["status"] == "error"                                            # 공시 0건 + 읽지 못함 = 「조회 실패」, 「무배당 (정상)」이 아니다
    assert (d["filing_status"], d["no_filing"]) == ("fetch_failed", False)
    assert not any("무배당으로 본다" in w for w in p["warnings"])
    assert any("본문 조회 실패" in w for w in p["warnings"])


def test_envelope_unchanged_for_a_attachment_amendment_and_for_no_decisions(monkeypatch):
    amend = {**_FILING, "report_nm": "[첨부정정]" + _DECISION_TITLE}
    p = _dividend_payload(monkeypatch, summary=_SUMMARY, decisions=[amend], client=_DocClient(DartClientError("014", "x")))
    assert (p["status"], p["data"]["filing_status"], p["data"]["parsing_failures"]) == ("exact", "all_parsed", 0)
    q = _dividend_payload(monkeypatch, summary={}, decisions=[])
    assert (q["status"], q["data"]["filing_status"]) == ("no_filing", "no_filing")      # 진짜 없음은 종전 그대로


def test_envelope_history_quarterly_failure_reaches_both_status_and_body(monkeypatch):
    """history scope 의 누적차분 조회(상태 칸을 만든 뒤에 도는 곳)가 실패하면 status 와 data.filing_status·parsing_failures 가 같이 바뀐다."""
    exc = DartClientError("800", "점검")
    cli = _AlotClient({**_CUM, "11012": exc})
    cli.get_document_cached = _DocClient(DartClientError("800", "x")).get_document_cached
    p = _dividend_payload(monkeypatch, scope="history", year=2025, client=cli, summary=_SUMMARY)
    d = p["data"]
    assert p["status"] == "partial"
    assert (d["filing_status"], d["parsing_failures"]) == ("partial_failure", 1)
    assert [r["quarter"] for r in d["quarterly_full"]] == ["Q1", "결산"]
    assert any(w.endswith(_tail(exc)) for w in p["warnings"])


class _MultiYearClient(_AlotClient):
    """사업보고서(11011)는 당기 무배당·전기·전전기 1,000원, 3분기(11014)는 interim_error(없으면 013)."""

    def __init__(self, interim_error=None):
        super().__init__({"11014": interim_error} if interim_error else {})

    async def get_dividend_info(self, corp_code, year, reprt_code):
        if reprt_code != "11011":
            return await super().get_dividend_info(corp_code, year, reprt_code)
        return {"list": [
            {"se": "주당액면가액(원)", "stock_knd": "-", "thstrm": "500", "frmtrm": "500", "lwfr": "500", "stlm_dt": "2024-12-31"},
            {"se": "주당 현금배당금(원)", "stock_knd": "보통주", "thstrm": "0", "frmtrm": "1,000", "lwfr": "1,000", "stlm_dt": "2024-12-31"},
            {"se": "현금배당금총액(백만원)", "stock_knd": "-", "thstrm": "0", "frmtrm": "1,000", "lwfr": "1,000", "stlm_dt": "2024-12-31"}]}

    async def get_document_cached(self, rcept_no):
        raise DartClientError("800", "점검")


def _interim_pattern(monkeypatch, interim_error):
    notice = {"rcept_no": "20250301000001", "rcept_dt": "20250301", "report_nm": "배당기준일결정"}
    p = _dividend_payload(monkeypatch, scope="history", year=2024, client=_MultiYearClient(interim_error), notices=[notice],
                          annual_by_year=None, real_annual=True)
    row = next(r for r in p["data"]["history"] if r["year"] == 2024)
    return p, row


def test_a_failed_interim_lookup_is_not_turned_into_a_confirmed_no_dividend(monkeypatch):
    _, ok_row = _interim_pattern(monkeypatch, None)
    assert ok_row["pattern"] == "무배당 (확정 · 결산 현금배당 없음)"                  # 조회가 013 뿐이면 종전 그대로
    exc = DartClientError("800", "점검")
    p, row = _interim_pattern(monkeypatch, exc)
    assert not row["pattern"].startswith("무배당 (확정")                              # 종전: 중간배당 조회 실패가 「확정 무배당」이 됐다
    assert any(w.endswith(_tail(exc)) for w in p["warnings"])


# ── 4. treasury_share — #91 일부 조회 실패 · #94 소각결정 본문 · #90 연간 상류 ─────────────────
class _TsClient:
    def __init__(self, *, acquisition=None, disposal=None, doc_error=None, acq_error=None):
        self.acquisition, self.disposal = acquisition or [], disposal
        self.doc_error, self.acq_error = doc_error, acq_error

    def api_call_snapshot(self):
        return 0

    async def get_treasury_acquisition(self, *_a):
        if self.acq_error:
            raise self.acq_error
        return {"status": "000", "list": self.acquisition}

    async def get_treasury_disposal(self, *_a):
        if self.disposal is not None:
            raise self.disposal
        raise DartClientError("013", "x")

    async def get_treasury_trust_contract(self, *_a):
        raise DartClientError("013", "x")

    async def get_treasury_trust_termination(self, *_a):
        raise DartClientError("013", "x")

    async def get_document_cached(self, rcept_no):
        if self.doc_error is not None:
            raise self.doc_error
        return {"text": "본문"}


_ACQ = {"rcept_no": "20250115000001", "rcept_dt": "20250115", "aq_pp": "주가안정",
        "aqpln_stk_ostk": "1,000,000", "aqpln_prc_ostk": "50,000,000,000", "aq_dd": "2025-01-15"}


def _cancel(title: str = _CANCEL_TITLE) -> dict:
    return {"rcept_no": "20250220000009", "rcept_dt": "20250220", "report_nm": title, "corp_code": "00000001"}


def _treasury(monkeypatch, client, *, cancels=None, scope="summary", own_payload=None):
    async def resolve(q, *a, **k):
        return _resolution()

    async def title_scan(**_kw):
        return list(cancels or []), [], None

    monkeypatch.setattr(ts, "get_dart_client", lambda: client)
    monkeypatch.setattr(ts, "resolve_company_query", resolve)
    monkeypatch.setattr(ts, "fetch_filings_for_title_scan", title_scan)
    monkeypatch.setattr(ts, "_parse_cancelation_body", lambda text: {"shares": 10, "amount_krw": 1000})
    if own_payload is not None:
        async def fake_own(q, scope="summary", year=None, **k):
            return own_payload
        monkeypatch.setattr(own, "build_ownership_structure_payload", fake_own)
    return asyncio.run(ts.build_treasury_share_payload("합성", scope=scope, lookback_months=24))


@pytest.mark.parametrize("status", ["020", "800"])
def test_a_failed_decision_lookup_next_to_a_good_one_is_partial(monkeypatch, status):
    exc = DartClientError(status, "x")
    p = _treasury(monkeypatch, _TsClient(acquisition=[_ACQ], disposal=exc))
    d = p["data"]
    assert p["status"] == "partial"                                           # 종전: exact (처분 0건 = 「없음」과 같은 숫자)
    assert (d["filing_status"], d["parsing_failures"]) == ("partial_failure", 1)
    assert any("처분결정 조회 실패" in w for w in p["warnings"])


def test_a_clean_run_stays_exact_and_a_013_is_not_a_failure(monkeypatch):
    p = _treasury(monkeypatch, _TsClient(acquisition=[_ACQ], disposal=DartClientError("013", "x")))
    assert (p["status"], p["data"]["filing_status"], p["data"]["parsing_failures"]) == ("exact", "all_parsed", 0)


def test_nothing_read_and_a_lookup_failed_is_still_an_error(monkeypatch):
    p = _treasury(monkeypatch, _TsClient(acq_error=DartClientError("020", "한도")))
    assert (p["status"], p["data"]["filing_status"]) == ("error", "fetch_failed")


@pytest.mark.parametrize("status", ["800", "014"])
def test_unreadable_cancelation_body_is_partial_not_a_silent_zero(monkeypatch, status):
    exc = DartClientError(status, "x")
    p = _treasury(monkeypatch, _TsClient(acquisition=[_ACQ], doc_error=exc), cancels=[_cancel()])
    d = p["data"]
    assert p["status"] == "partial"
    assert (d["filing_status"], d["parsing_failures"]) == ("partial_failure", 1)
    assert any(w.endswith(_tail(exc)) for w in p["warnings"])
    assert d["summary"]["cancelation_amount_total_krw"] == 0                  # 합계 숫자는 건드리지 않는다 — status·경고로 알린다


def test_attachment_amendment_cancelation_without_a_body_stays_exact(monkeypatch):
    title = "[첨부정정]" + _CANCEL_TITLE
    p = _treasury(monkeypatch, _TsClient(acquisition=[_ACQ], doc_error=DartClientError("014", "x")), cancels=[_cancel(title)])
    assert (p["status"], p["data"]["parsing_failures"]) == ("exact", 0)
    assert any("본문 파싱 실패 1건" in w for w in p["warnings"])               # 종전 경고는 그대로(후퇴 없음)
    q = _treasury(monkeypatch, _TsClient(acquisition=[_ACQ], doc_error=DartClientError("800", "x")), cancels=[_cancel(title)])
    assert q["status"] == "partial"                                           # 첨부정정이어도 점검(800)은 실패다


def test_strict_point_in_time_cancelation_body_has_no_retry_hint(monkeypatch):
    exc = DartClientError("STRICT_PIT_UNAVAILABLE", "기준 시점")
    p = _treasury(monkeypatch, _TsClient(acquisition=[_ACQ], doc_error=exc), cancels=[_cancel()])
    assert p["status"] == "partial"
    assert any(w.endswith(_tail(exc)) and "잠시" not in w for w in p["warnings"])


_OWN_TREASURY = {"issued_shares": 100, "treasury_shares": 5}


def _own(status: str, warnings: list[str]) -> dict:
    return {"tool": "ownership_structure", "status": status, "warnings": warnings,
            "data": {"treasury": dict(_OWN_TREASURY)} if status != "error" else {"query": "합성"}}


def test_annual_scope_carries_an_upstream_error(monkeypatch):
    p = _treasury(monkeypatch, _TsClient(acquisition=[_ACQ]), scope="annual", own_payload=_own("error", ["상류 마커 실패"]))
    d = p["data"]
    assert d["annual"] == {}
    assert p["status"] == "partial"                                           # 종전: annual={} + exact
    assert (d["filing_status"], d["parsing_failures"]) == ("partial_failure", 1)
    assert "[ownership_structure] 상류 마커 실패" in p["warnings"]
    q = _treasury(monkeypatch, _TsClient(), scope="annual", own_payload=_own("error", ["상류 마커 실패"]))
    assert (q["status"], q["data"]["filing_status"]) == ("error", "fetch_failed")      # 이벤트 0건 + 상류 실패 = 「조회 실패」


def test_annual_scope_keeps_a_partial_snapshot_and_marks_it_partial(monkeypatch):
    p = _treasury(monkeypatch, _TsClient(), scope="annual", own_payload=_own("partial", ["상류 마커 부분"]))
    d = p["data"]
    assert d["annual"] == _OWN_TREASURY
    assert p["status"] == "partial"
    assert (d["filing_status"], d["no_filing"]) == ("partial_failure", False)
    assert "[ownership_structure] 상류 마커 부분" in p["warnings"]


def test_annual_scope_does_not_carry_upstream_warnings_when_upstream_is_exact(monkeypatch):
    p = _treasury(monkeypatch, _TsClient(), scope="annual", own_payload=_own("exact", ["지분 합계 100% 초과 안내"]))
    assert p["data"]["annual"] == _OWN_TREASURY
    assert not any("ownership_structure" in w or "100%" in w for w in p["warnings"])
    assert p["status"] == "no_filing"                                         # 이벤트 0건 — 종전과 같다


# ── 5. financial_metrics — #28 배당성향 조회 실패 · #29 일부 실패가 exact 로 ──────────────────
_FIXTURE = Path(__file__).parent / "dart_responses/dart/samsung_005930_00126380_2025_11011_acnt_all_cfs_full.json"
_FM_CO = {"corp_code": "00126380", "corp_name": "삼성전자", "stock_code": "005930"}


class _FmClient:
    def __init__(self, rows, fail_years=(), div_error=None):
        self.rows, self.fail_years, self.div_error = rows, set(fail_years), div_error

    def api_call_snapshot(self):
        return 0

    async def get_fnltt_singl_acnt_all(self, cc, y, rc, fs):
        if int(y) in self.fail_years:
            raise DartClientError("800", "점검")
        if int(y) == 2025 and rc == "11011" and fs == "CFS":
            return {"status": "000", "list": self.rows}
        raise DartClientError("013", "x")

    async def get_fnltt_singl_acnt(self, cc, y, rc, fs):
        if int(y) in self.fail_years:
            raise DartClientError("800", "점검")
        if int(y) == 2025 and rc == "11011":
            return {"status": "000", "list": [dict(r, fs_div="CFS") for r in self.rows if r.get("sj_div") in ("BS", "IS", "CIS")]}
        raise DartClientError("013", "x")

    async def get_dividend_info(self, cc, y, rc):
        raise self.div_error or DartClientError("013", "x")

    def __getattr__(self, name):
        async def _missing(*a, **k):
            raise DartClientError("013", "x")
        return _missing


def _fm_payload(monkeypatch, scope="summary", **kw):
    rows = json.load(open(_FIXTURE, encoding="utf-8"))["list"]
    cli = _FmClient(rows, **kw)
    cache_sets: list[str] = []

    async def resolve(q, *a, **k):
        return CompanyResolution(status=AnalysisStatus.EXACT, query=q, selected=dict(_FM_CO), candidates=[])

    async def info(cc):
        return {"acc_mt": "12", "induty_code": "264"}, None

    async def none(*a, **k):
        return None

    real_set = FM._fm_cache_set
    monkeypatch.setenv("OPENDART_API_KEY", "test-key-not-real")
    monkeypatch.setattr(FM, "_FM_CACHE", type(FM._FM_CACHE)(16 * 1024 * 1024, 300.0, "test-swallow-a"))
    monkeypatch.setattr(FM, "get_dart_client", lambda: cli)
    monkeypatch.setattr(dv, "get_dart_client", lambda: cli)
    monkeypatch.setattr(FM, "resolve_company_query", resolve)
    monkeypatch.setattr(FM, "_safe_company_info", info)
    monkeypatch.setattr(FM, "_periodic_filing_ref", none)
    monkeypatch.setattr(FM, "_fm_cache_set", lambda k, p: (cache_sets.append(p["status"]), real_set(k, p))[1])
    p = asyncio.run(FM.build_financial_metrics_payload("005930", scope=scope, year=2025, years=3))
    return p, cache_sets


@pytest.mark.parametrize("scope", ["summary", "yearly"])
def test_partial_fetch_failure_is_not_exact_and_not_cached(monkeypatch, scope):
    p, sets = _fm_payload(monkeypatch, scope, fail_years={2022, 2023, 2024})
    d = p["data"]
    assert p["status"] == "partial"                                           # 종전: exact
    assert d["filing_status"] == "partial_failure" and d["parsing_failures"] > 0
    assert any("fnlttSinglAcnt" in w and "800" in w for w in p["warnings"])
    assert sets == []                                                         # 5분 캐시에 굳히지 않는다


@pytest.mark.parametrize("scope", ["summary", "yearly"])
def test_clean_run_is_exact_and_cached(monkeypatch, scope):
    p, sets = _fm_payload(monkeypatch, scope)
    assert (p["status"], p["data"]["filing_status"], p["data"]["parsing_failures"]) == ("exact", "all_parsed", 0)
    assert sets == ["exact"]


def test_payout_lookup_failure_is_partial_with_a_warning_not_a_silent_dash(monkeypatch):
    p, sets = _fm_payload(monkeypatch, "summary", div_error=DartClientError("020", "한도"))
    assert p["data"]["summary"]["payout_ratio_pct"] is None
    assert p["status"] == "partial"
    assert any("배당성향 조회 실패" in w and "020" in w for w in p["warnings"])
    assert sets == []
    # 재시도 시각 안내를 붙이지 않는다(020 은 자정 이후라 틀린 말이 된다)
    assert not any("배당성향 조회 실패" in w and "잠시" in w for w in p["warnings"])


def test_payout_013_is_a_plain_dash(monkeypatch):
    p, sets = _fm_payload(monkeypatch, "summary")                              # 배당 항목이 013 — 무배당/미기재
    assert p["data"]["summary"]["payout_ratio_pct"] is None
    assert p["status"] == "exact" and not any("배당성향 조회 실패" in w for w in p["warnings"])


def test_accrual_payout_reports_the_error_and_leaves_reit_alone(monkeypatch):
    box = {"n": 0}
    ctx = FM._FETCH_FAILURES.set(box)
    try:
        monkeypatch.setattr(dv, "get_dart_client", lambda: _FmClient([], div_error=DartClientError("020", "한도")))
        failed: list[str] = []
        assert asyncio.run(FM._accrual_payout_pct("00126380", 2025, failed=failed)) is None
        assert box["n"] == 1 and failed == ["alotMatter 조회 실패: 020"]
        monkeypatch.setattr(dv, "get_dart_client", lambda: _FmClient([], div_error=DartClientError("013", "x")))
        failed2: list[str] = []
        assert asyncio.run(FM._accrual_payout_pct("00126380", 2025, failed=failed2)) is None
        assert box["n"] == 1 and failed2 == []                                 # 013 은 센다 않는다
        assert asyncio.run(FM._accrual_payout_pct("00126380", 2025, is_reit=True, failed=failed2)) is None
        assert box["n"] == 1 and failed2 == []                                 # REIT 의 의도적 None 은 실패가 아니다

        async def boom(*_a):
            raise RuntimeError("전송 오류")

        monkeypatch.setattr(dv, "_annual_summary", boom)
        failed3: list[str] = []
        assert asyncio.run(FM._accrual_payout_pct("00126380", 2025, failed=failed3)) is None
        assert box["n"] == 2 and failed3 == ["RuntimeError"]
    finally:
        FM._FETCH_FAILURES.reset(ctx)


def test_render_says_failed_calls_not_filings():
    from open_proxy_mcp.tools import financial_metrics as tool

    md = tool._render({"subject": "합성", "warnings": [], "data": {
        "scope": "summary", "canonical_name": "합성", "filing_status": "partial_failure", "parsing_failures": 3, "summary": {}}})
    assert "일부 공시를 읽지 못했습니다 (조회 3회 실패)" in md


# ── 6. 공시 원장 배치 — 조회 실패로 생긴 partial 은 채움이 아니다 ──────────────────────────────
def test_ledger_batch_does_not_treat_partial_as_already_detailed():
    from scripts import disclosure_ledger as DL

    class _Con:
        def execute(self, sql, params):
            self.sql, self.params = sql, params
            return self

        def fetchall(self):
            return [({"rcept_no": "20261016000001", "kind": "dividend"},)]

    con = _Con()
    assert DL.already_detailed(con, ["20261016000001", "20261016000002"]) == {"20261016000001"}
    assert "detail_status = 'parsed'" in con.sql and "'partial'" not in con.sql
    assert DL.already_detailed(_Con(), []) == set()
