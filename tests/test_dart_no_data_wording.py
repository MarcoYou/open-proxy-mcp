"""DART 013(조회된 데이터 없음)은 **답**이고, 그 밖의 DART 오류는 **실패**다 — 문구·상태값을 그 둘에 맞춘다(261018).

회귀 기준선(같은 입력을 운영 코드·직전 코드로 141건 호출)에서 드러난 것:
  · 합병 상장폐지사(에이치디현대미포)의 company 가 「최근 공시 인덱스 조회 실패: 013」, dividend_disclosure 가
    「alotMatter 조회 실패: 013」 — 실패가 아니라 「그 구간에 공시가 없다」·「그 해 사업보고서가 없다」는 답이다.
  · financial_notes 의 응답 status 만 대문자 「OK」(원문 미수집은 「NO_DATA」) — 다른 도구는 소문자 `exact` 등.
작업 중 같은 갈래에서 더 나온 것(DART 를 013·020 으로만 답하게 바꿔 141건을 다시 돌려 전수로 찾았다):
  · 013 을 실패로 적던 자리 — proxy_contest 지분 명부(1~3월엔 사업보고서 제출 전이라 거의 모든 회사), provisional_earnings
    (두 검색 중 하나만 013 이어도 status=error), risk_events 시장 스캔(한 채널만 비어도 전체 실패), 도구가 013 을 놓쳐
    공용 래퍼로 떨어질 때의 status=error, financial_metrics 의 「2026년 reprt_code=11013 no_filing」 문장·「⚠ 일부 공시를
    읽지 못했습니다」 줄.
  · 거꾸로 **실패를 「없음 (정상)」으로 적던 자리** — 020(한도)·800(점검)·전송 오류로 목록을 못 읽었는데 0건을
    「배당결정 공시 없음 … 무배당으로 본다 (정상)」·「… 공시 없음 (정상)」·status=no_filing 으로 냈다(배당·타법인주식·
    지배구조 재편·희석 증권·자사주·수주·지배구조보고서·리스크·분쟁 신호·재무지표·최근 공시). 이제 `fetch_failed` 로 적고
    status=error(받은 다른 자료가 있는 분쟁 신호·주주환원 집계는 partial).

network 0 · DB 0 — DART 는 `DartClient._request` 를 갈아 끼워 원하는 상태코드로만 답하게 하고, 회사 해석은 가짜로 넣는다.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from open_proxy_mcp.dart import client as dart_client
from open_proxy_mcp.dart.client import DartClientError
from open_proxy_mcp.services import contracts
from open_proxy_mcp.services.company import CompanyResolution
from open_proxy_mcp.services.contracts import AnalysisStatus
from open_proxy_mcp.services.dart_safety import degrade_response, is_no_data

MIPO = {"corp_code": "00164609", "corp_name": "에이치디현대미포", "stock_code": "010620"}


def _resolution(query="에이치디현대미포"):
    return CompanyResolution(status=AnalysisStatus.EXACT, query=query, selected=dict(MIPO), candidates=[])


async def _fake_resolve(query, *args, **kwargs):
    return _resolution(query)


@pytest.fixture
def dart_answers(monkeypatch):
    """DART JSON 호출 전부가 주어진 상태코드로 답하는 세계. 원문(ZIP)은 014. 반환: 상태를 정하는 함수."""
    state = {"status": "013"}
    monkeypatch.setenv("OPENDART_API_KEY", "test-key-not-real")

    async def fake_request(self, endpoint, params):
        raise DartClientError(state["status"], "가짜 응답")

    async def fake_binary(self, endpoint, params):
        raise DartClientError("014", "가짜 응답")

    monkeypatch.setattr(dart_client.DartClient, "_request", fake_request)
    monkeypatch.setattr(dart_client.DartClient, "_request_binary", fake_binary)

    def set_status(status: str) -> None:
        state["status"] = status
    return set_status


def _run(coro):
    return asyncio.run(coro)


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# ① 공용 판정 — 코드표는 dart_safety 한 곳
# ═════════════════════════════════════════════════════════════════════════════════════════════════
def test_is_no_data_is_013_only():
    assert is_no_data(DartClientError("013", "x")) and is_no_data("013") and is_no_data(" 013 ")
    for status in ("020", "021", "800", "900", "010", "011", "012", "014", "404", "TRANSPORT_ERROR", "", None):
        assert not is_no_data(DartClientError(status or "", "x") if status is not None else None), status
    assert not is_no_data(RuntimeError("013"))          # 상태 칸이 없는 예외는 아니다


def test_degrade_json_status_is_no_filing_for_013_and_error_otherwise():
    """도구가 013 을 놓쳐 공용 래퍼로 떨어져도 상태값이 「실패」가 아니다 — 표지(`[nodata=…]`)·안내문과 같은 말을 한다."""
    body = json.loads(degrade_response("asset_holdings", "json", DartClientError("013", "x")))
    assert body["status"] == "no_filing" and body["data"]["error_kind"] == "no_data"
    assert body["data"]["marker"] == "[nodata=no_data]" and body["data"]["retry"] is False
    for status in ("020", "800", "404", "014"):
        assert json.loads(degrade_response("asset_holdings", "json", DartClientError(status, "x")))["status"] == "error", status


def test_filing_meta_fetch_failed_is_neither_none_nor_success():
    meta = contracts.build_filing_meta(filing_count=0, fetch_failed=True)
    assert meta["no_filing"] is False and meta["filing_status"] == "fetch_failed"
    assert contracts.status_from_filing_meta(meta) == AnalysisStatus.ERROR
    # 찾은 게 있으면 실패 표시는 0건일 때만 뜻이 있다 — 기존 판정 그대로
    assert contracts.build_filing_meta(filing_count=2, fetch_failed=True)["filing_status"] == "all_parsed"
    assert contracts.status_from_filing_meta(contracts.build_filing_meta(filing_count=0)) == AnalysisStatus.NO_FILING


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# ② company — 최근 공시 인덱스
# ═════════════════════════════════════════════════════════════════════════════════════════════════
class _CompanyClient:
    def __init__(self, status):
        self.status = status

    def api_call_snapshot(self):
        return 0

    async def lookup_corp_code_all(self, query):
        return [{**MIPO, "corp_eng_name": "HD HYUNDAI MIPO CO.,LTD.", "modify_date": "20251201"}]

    async def get_company_info(self, corp_code):
        return {"corp_name": MIPO["corp_name"], "corp_cls": "E", "jurir_no": "1812110000526", "acc_mt": "12"}

    async def search_filings(self, **kwargs):
        raise DartClientError(self.status, "x")


def _company(monkeypatch, status, language="auto"):
    from open_proxy_mcp.services import company as C
    monkeypatch.setattr(C, "get_dart_client", lambda: _CompanyClient(status))
    return _run(C.build_company_payload("010620", start_date="20260901", end_date="20261016", language=language))


def test_company_013_is_an_information_note_not_a_failure(monkeypatch):
    from open_proxy_mcp.tools.company import _render_exact
    p = _company(monkeypatch, "013")
    assert p["status"] == "exact"
    assert not any("실패" in w or "013" in w for w in p["warnings"]), p["warnings"]
    assert "조회 구간(20260901~20261016)에 DART 공시가 없습니다 — 조회는 정상이며 그 기간 공시가 0건입니다." in p["warnings"]
    assert p["data"]["no_filing"] is True and p["data"]["filing_status"] == "no_filing"
    md = _render_exact(p)
    assert "최근 공시 없음" in md and "조회 실패" not in md
    en = _company(monkeypatch, "013", language="en")
    assert any(w.startswith("No DART filings in the search window (20260901~20261016)") for w in en["warnings"])


@pytest.mark.parametrize("status", ["020", "800", "TRANSPORT_ERROR"])
def test_company_other_errors_stay_failures_and_are_not_rendered_as_none(monkeypatch, status):
    from open_proxy_mcp.tools.company import _render_exact
    p = _company(monkeypatch, status)
    assert f"최근 공시 인덱스 조회 실패: {status}" in p["warnings"]
    assert p["data"]["no_filing"] is False and p["data"]["filing_status"] == "fetch_failed"
    md = _render_exact(p)
    assert "조회 실패 — 공시 유무 미확인(유의사항 참조)" in md and "최근 공시 없음" not in md


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# ③ 배당 사항(alotMatter) — 013 은 빈 표와 같다
# ═════════════════════════════════════════════════════════════════════════════════════════════════
def test_annual_summary_013_is_not_a_failure_reason(monkeypatch):
    from open_proxy_mcp.services import dividend as D

    def client_raising(status):
        class C:
            async def get_dividend_info(self, cc, year, rc):
                raise DartClientError(status, "x")
        return lambda: C()
    monkeypatch.setattr(D, "get_dart_client", client_raising("013"))
    assert _run(D._annual_summary(MIPO["corp_code"], 2025)) == ({}, None)
    monkeypatch.setattr(D, "get_dart_client", client_raising("020"))
    assert _run(D._annual_summary(MIPO["corp_code"], 2025)) == ({}, "alotMatter 조회 실패: 020")


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# ④ 도구 전 구간 — 013 세계와 020 세계
# ═════════════════════════════════════════════════════════════════════════════════════════════════
#: (서비스 모듈, 빌더 이름, 인자) — 회사 해석은 가짜, DART 는 `dart_answers` 가 정한 상태로만 답한다.
_TOOLS = [
    ("dividend", "build_dividend_payload", {"scope": "summary"}),
    ("corporate_deals", "build_corporate_deals_payload", {}),
    ("corporate_restructuring", "build_corporate_restructuring_payload", {}),
    ("dilutive_issuance", "build_dilutive_issuance_payload", {}),
    ("treasury_share", "build_treasury_share_payload", {"scope": "summary"}),
    ("order_contracts", "build_order_contracts_payload", {}),
    ("corp_gov_report", "build_corp_gov_report_payload", {"scope": "filings"}),
    ("risk_events", "build_risk_events_payload", {}),
]
_NONE_PHRASES = ("(정상)", "무배당으로 본다", "공시 없음", "없음.")


def _build(monkeypatch, module_name, builder, kwargs):
    import importlib
    mod = importlib.import_module(f"open_proxy_mcp.services.{module_name}")
    monkeypatch.setattr(mod, "resolve_company_query", _fake_resolve)
    fn = getattr(mod, builder)
    return _run(fn("에이치디현대미포", **kwargs))


@pytest.mark.parametrize("module_name,builder,kwargs", _TOOLS, ids=[t[0] for t in _TOOLS])
def test_lookup_failure_is_not_reported_as_none(monkeypatch, dart_answers, module_name, builder, kwargs):
    """020(한도)로 목록을 못 읽었으면 0건은 「없음 (정상)」이 아니라 「모른다」다 — status=error, 실패 사유는 경고에."""
    dart_answers("020")
    p = _build(monkeypatch, module_name, builder, kwargs)
    assert p["status"] == "error", (module_name, p["status"], p["warnings"])
    assert any("020" in w for w in p["warnings"]), p["warnings"]
    assert not any(ph in w for w in p["warnings"] for ph in _NONE_PHRASES), p["warnings"]
    data = p.get("data") or {}
    if "filing_status" in data:
        assert data["filing_status"] == "fetch_failed" and data["no_filing"] is False


@pytest.mark.parametrize("module_name,builder,kwargs", _TOOLS, ids=[t[0] for t in _TOOLS])
def test_no_data_is_reported_as_none_not_failure(monkeypatch, dart_answers, module_name, builder, kwargs):
    """013 만 받으면 「없음」이 답이다 — status=no_filing, 경고에 「실패」·「013」이 없다."""
    dart_answers("013")
    p = _build(monkeypatch, module_name, builder, kwargs)
    assert p["status"] in ("no_filing", "partial"), (module_name, p["status"], p["warnings"])
    assert not any("013" in w or "조회 실패" in w or "검색 실패" in w for w in p["warnings"]), p["warnings"]


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# ⑤ proxy_contest — 지분 명부 기준연도 사업보고서가 아직 없을 때
# ═════════════════════════════════════════════════════════════════════════════════════════════════
class _ControlClient:
    """연도별로 다르게 답한다 — {연도: 상태코드 또는 None(정상)}."""

    def __init__(self, by_year):
        self.by_year = by_year
        self.years_asked: list[str] = []

    async def _answer(self, year):
        self.years_asked.append(year)
        status = self.by_year.get(year, "013")
        if status:
            raise DartClientError(status, "x")
        return {"list": []}

    async def get_major_shareholders(self, cc, year, reprt_code="11011"):
        return await self._answer(year)

    async def get_stock_total(self, cc, year, reprt_code="11011"):
        return await self._answer(year)

    async def get_treasury_stock(self, cc, year, reprt_code="11011"):
        return await self._answer(year)


def _control(monkeypatch, by_year):
    from open_proxy_mcp.services import proxy_contest as PC

    async def no_blocks(corp_code):
        return [], [], None
    client = _ControlClient(by_year)
    monkeypatch.setattr(PC, "get_dart_client", lambda: client)
    monkeypatch.setattr(PC, "_latest_block_rows", no_blocks)
    ctx, warnings = _run(PC._control_context(MIPO["corp_code"], "에이치디현대미포", 2027))
    return ctx, warnings, client


def test_control_context_steps_back_a_year_when_the_annual_report_is_not_filed_yet(monkeypatch):
    """1~3월(주총 시즌)엔 「올해 − 1」 사업보고서가 아직 없어 013 — 종전엔 「지분 명부 API 조회 실패: 013」에 지분 맥락이 통째로 비었다."""
    ctx, warnings, client = _control(monkeypatch, {"2026": "013", "2025": None})
    assert ctx["year"] == "2025" and ctx["lookup_failed"] is False
    assert any("2026년 사업보고서가 DART 에 아직 없어" in w and "2025년 사업보고서 기준" in w for w in warnings), warnings
    assert not any("실패" in w for w in warnings), warnings


def test_control_context_both_years_absent_is_information(monkeypatch):
    ctx, warnings, _ = _control(monkeypatch, {"2026": "013", "2025": "013"})
    assert ctx["top_holder"] == {} and ctx["lookup_failed"] is False
    assert any("2026년과 그 전 해 사업보고서의 최대주주 명부가 DART 에 없습니다" in w for w in warnings), warnings
    assert not any("013" in w for w in warnings)


def test_control_context_real_failures_stay_failures(monkeypatch):
    ctx, warnings, client = _control(monkeypatch, {"2026": "020"})
    assert "지분 명부 API 조회 실패: 020" in warnings and ctx["lookup_failed"] is True
    assert client.years_asked.count("2025") == 0                 # 실패면 앞 해로 내려가지 않는다(「없다」가 아니라 「모른다」)
    ctx, warnings, _ = _control(monkeypatch, {"2026": "013", "2025": "020"})
    assert ctx["lookup_failed"] is True
    assert any("그 전 해 지분 명부 조회는 실패했습니다: 020" in w for w in warnings), warnings


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# ⑥ provisional_earnings · risk_events 시장 스캔 — 한쪽이 013 이어도 다른 쪽을 버리지 않는다
# ═════════════════════════════════════════════════════════════════════════════════════════════════
def test_provisional_candidates_keep_one_side_when_the_other_is_013():
    from open_proxy_mcp.services.provisional_earnings import _find_provisional_candidates
    item = {"rcept_no": "20261015800001", "rcept_dt": "20261015", "report_nm": "연결재무제표기준영업(잠정)실적(공정공시)"}

    class C:
        def __init__(self, i002, i001):
            self.ans = {"I002": i002, "I001": i001}

        async def search_filings(self, **kw):
            a = self.ans[kw["pblntf_detail_ty"]]
            if isinstance(a, str):
                raise DartClientError(a, "x")
            return {"list": a}
    assert _run(_find_provisional_candidates(C([item], "013"), "x", "20260418", "20261018")) == [item]
    assert _run(_find_provisional_candidates(C("013", "013"), "x", "20260418", "20261018")) == []
    with pytest.raises(DartClientError):
        _run(_find_provisional_candidates(C([item], "020"), "x", "20260418", "20261018"))


def test_provisional_earnings_without_filings_is_no_filing(monkeypatch, dart_answers):
    from open_proxy_mcp.services import provisional_earnings as PE
    monkeypatch.setattr(PE, "resolve_company_query", _fake_resolve)
    dart_answers("013")
    p = _run(PE.build_provisional_earnings_payload("에이치디현대미포", start_date="20260418", end_date="20261018"))
    assert p["status"] == "no_filing" and not any("실패" in w for w in p["warnings"]), p
    dart_answers("020")
    p = _run(PE.build_provisional_earnings_payload("에이치디현대미포", start_date="20260418", end_date="20261018"))
    assert p["status"] == "error" and any("020" in w for w in p["warnings"]), p


def test_market_risk_scan_keeps_other_channels_when_one_is_empty(monkeypatch):
    from open_proxy_mcp.services import risk_events as R
    hit = {"rcept_no": "20261016800100", "rcept_dt": "20261016", "report_nm": "매매거래정지및정지해제(중요내용공시)",
           "corp_name": "가나다", "stock_code": "000000", "flr_nm": "코스닥시장본부"}

    class C:
        async def search_filings(self, **kw):
            if kw.get("pblntf_detail_ty") == "I003":
                return {"list": [hit], "total_page": 1}
            raise DartClientError("013", "x")
    monkeypatch.setattr(R, "get_dart_client", lambda: C())
    p = _run(R._build_market_scan_payload(start_date="20261016", end_date="20261016"))
    assert not any("스캔 실패" in w for w in p["warnings"]), p["warnings"]
    assert [e["rcept_no"] for e in p["data"]["events"]] == [hit["rcept_no"]] and p["status"] == "exact"


@pytest.mark.parametrize("status,expect", [("013", "no_filing"), ("020", "error")])
def test_market_risk_scan_status(monkeypatch, status, expect):
    from open_proxy_mcp.services import risk_events as R

    class C:
        async def search_filings(self, **kw):
            raise DartClientError(status, "x")
    monkeypatch.setattr(R, "get_dart_client", lambda: C())
    p = _run(R._build_market_scan_payload(start_date="20261018", end_date="20261018"))     # 일요일
    assert p["status"] == expect
    assert any("시장 전체 리스크 공시 없음" in w for w in p["warnings"]) is (status == "013")
    assert any("시장 전체 스캔 실패: 020" in w for w in p["warnings"]) is (status == "020")


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# ⑦ financial_metrics — 「reprt_code=… no_filing」 문장과 실패를 「미존재 (정상)」으로 적던 것
# ═════════════════════════════════════════════════════════════════════════════════════════════════
def test_fallback_chain_all_013_says_not_filed_in_words(monkeypatch):
    from open_proxy_mcp.services import financial_metrics as FM

    async def acnt(cc, year, rc, fs):
        return [], "no_filing"
    monkeypatch.setattr(FM, "_safe_fetch_acnt", acnt)
    rows, rc, msg = _run(FM._fetch_acnt_with_fallback("x", 2026, "CFS"))
    assert rows == [] and msg == "2026년 정기보고서(사업·3분기·반기·1분기) 재무제표 미공시"
    assert "reprt_code" not in msg and "no_filing" not in msg

    async def acnt_fail(cc, year, rc, fs):
        return ([], "no_filing") if rc != "11012" else ([], "fnlttSinglAcnt(11012, CFS) 실패: 020 x")
    monkeypatch.setattr(FM, "_safe_fetch_acnt", acnt_fail)
    assert _run(FM._fetch_acnt_with_fallback("x", 2026, "CFS"))[2] == "fnlttSinglAcnt(11012, CFS) 실패: 020 x"


@pytest.mark.parametrize("status,expect", [("013", "no_filing"), ("020", "error")])
def test_financial_metrics_failure_is_not_absent(monkeypatch, dart_answers, status, expect):
    from open_proxy_mcp.services import financial_metrics as FM
    from open_proxy_mcp.tools.financial_metrics import _render
    monkeypatch.setattr(FM, "resolve_company_query", _fake_resolve)
    monkeypatch.setattr(FM, "_FM_CACHE", type(FM._FM_CACHE)(1, 1.0, "test-fm"))
    dart_answers(status)
    p = _run(FM.build_financial_metrics_payload("에이치디현대미포", scope="summary", year=2026))
    assert p["status"] == expect, p["warnings"]
    absent = any("재무 공시 미존재 (정상" in w for w in p["warnings"])
    assert absent is (status == "013")
    assert any("재무 조회가 DART 오류로 실패해" in w for w in p["warnings"]) is (status == "020")
    if status == "013":
        md = _render(p)
        assert "일부 공시를 읽지 못했습니다" not in md        # 공시가 없는 해는 「읽기 실패」가 아니다
        assert not any("reprt_code=" in w or "no_filing" in w for w in p["warnings"]), p["warnings"]
    else:
        assert FM._fm_cache_get(("에이치디현대미포", "summary", 2026, 3, True)) is None   # 실패 응답은 5분 캐시에 굳히지 않는다


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# ⑧ financial_notes — 응답 status 어휘와 필드 상태 표기
# ═════════════════════════════════════════════════════════════════════════════════════════════════
def test_financial_notes_envelope_status_vocabulary():
    from open_proxy_mcp.services import financial_notes as svc
    from open_proxy_mcp.tools.financial_notes import _envelope_status
    assert _envelope_status({"FVPL": {"status": svc.OK}, "사용제한": {"status": svc.NOT_APPLICABLE}}) == "exact"
    assert _envelope_status({"FVPL": {"status": svc.OK}, "FVOCI": {"status": svc.EXTRACTION_FAILED}}) == "partial"
    assert _envelope_status({"FVPL": {"status": svc.NOT_COLLECTED}}) == "partial"
    assert {s.value for s in AnalysisStatus} >= {"exact", "partial", "no_filing", "error"}


def test_financial_notes_md_does_not_print_engine_status_names():
    from open_proxy_mcp.services import financial_notes as svc
    from open_proxy_mcp.tools.financial_notes import _render
    md = _render({"company": "KB금융", "report": {"report_nm": "반기보고서 (2026.06)", "rcept_no": "20260814004200"},
                  "fields": ["사용제한"], "notes": {"사용제한": {"status": svc.NOT_APPLICABLE, "tables": [],
                                                             "note": "이 문서에서 해당 주석을 찾지 못했다"}}})
    assert "> 해당 주석 없음 — 이 문서에서 해당 주석을 찾지 못했다" in md and "NOT_APPLICABLE" not in md


def test_financial_notes_tool_statuses_are_lowercase_envelopes(monkeypatch, dart_answers):
    """성공은 `exact`, 보고서가 없으면 봉투째 `no_filing`(종전 `{"error": …}` — status 칸이 아예 없었다)."""
    from open_proxy_mcp.server import mcp
    from open_proxy_mcp.tools import financial_notes as T

    async def no_candidates(client, corp_code, period, year):
        return []
    monkeypatch.setattr(T, "resolve_company_query", _fake_resolve)
    monkeypatch.setattr(T, "_candidates", no_candidates)

    async def call(args):
        r = await mcp.call_tool("financial_notes", args)
        return "".join(getattr(c, "text", "") for c in (r if isinstance(r, list) else r.content))
    body = json.loads(_run(call({"company": "에이치디현대미포", "year": "2019", "format": "json"})))
    assert body["tool"] == "financial_notes" and body["status"] == "no_filing"
    assert body["warnings"] == ["2019 사업연도의 latest 보고서를 찾지 못했습니다"]

    report = {"rcept_no": "20260814004200", "rcept_dt": "20260814", "report_nm": "반기보고서 (2026.06)"}

    async def one_candidate(client, corp_code, period, year):
        return [report]

    async def nodes(client, rcept_no, want, basis):
        return [], {}, []
    monkeypatch.setattr(T, "_candidates", one_candidate)
    monkeypatch.setattr(T, "_fetch_by_nodes", nodes)
    monkeypatch.setattr(T.svc, "extract_regions", lambda regions, want, sheets: {f: {"status": T.svc.OK, "tables": []} for f in want})
    body = json.loads(_run(call({"company": "에이치디현대미포", "fields": "FVPL", "format": "json"})))
    assert body["status"] == "exact", body["status"]


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# ⑨ 주주환원 집계(shareholder_commitment) — 상류가 실패했는데 exact 이던 것
# ═════════════════════════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("failed,expect", [((), "exact"), (("dividend(summary)",), "partial"),
                                           (("value_up", "corp_gov_report", "dividend(summary)", "dividend(history)",
                                             "treasury_share"), "error")])
def test_shareholder_commitment_status_follows_upstream_failures(monkeypatch, dart_answers, failed, expect):
    from open_proxy_mcp.services import shareholder_commitment as SC
    monkeypatch.setattr(SC, "resolve_company_query", _fake_resolve)

    def upstream(name):
        async def build(*args, **kwargs):
            if name in failed:
                return {"status": "error", "warnings": [f"{name} 조회 실패: 020"], "data": {}}
            return {"status": "exact", "warnings": [], "data": {}}
        return build
    monkeypatch.setattr(SC, "build_value_up_payload", upstream("value_up"))
    monkeypatch.setattr(SC, "build_corp_gov_report_payload", upstream("corp_gov_report"))

    async def dividend(company, *, scope, **kwargs):
        return await upstream(f"dividend({scope})")()
    monkeypatch.setattr(SC, "build_dividend_payload", dividend)
    monkeypatch.setattr(SC, "build_treasury_share_payload", upstream("treasury_share"))

    async def no_impact(*args, **kwargs):
        return [], []
    monkeypatch.setattr(SC, "_capital_return_impact", no_impact)
    p = _run(SC.build_shareholder_commitment_payload("에이치디현대미포"))
    assert p["status"] == expect, (p["status"], p["warnings"])
