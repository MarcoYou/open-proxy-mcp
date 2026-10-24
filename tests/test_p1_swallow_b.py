# -*- coding: utf-8 -*-
"""조회 실패를 「없음」으로 삼키던 곳 (재무 점검 P1 묶음 1 — B: 오류 분류 재사용). network 0 · DB 0.

공통 규칙 하나: **「없음」과 「조회 실패」를 `ownership_structure._dart_read_note` 하나로 가른다.**
  · JSON API(list.json·alotMatter·fnlttSinglAcnt·stockTotqySttus·company 등): 013 만 「없음」. 014·404 를 포함한 그 밖의 상태·전송 오류·예외 = 실패.
  · 원문(document.xml·viewer 절): 014·404 는 호출하는 쪽이 그 공시를 첨부정정([첨부정정]·[첨부추가])으로 알 때(`attachment=True`)만 「없음」.
    제목을 모르는 행(5% 대량보유 행 등)은 실패 — 접수 당일이면 원문이 아직 없을 수 있다.
  · 기준 시점 고정 실행의 STRICT_PIT_UNAVAILABLE: 실패. 「잠시 뒤 다시」 안내를 붙이지 않는다.
실패는 값을 지어내지 않고 비우거나 경고로 알리며 status 를 exact/ok 로 두지 않는다 — 도구의 기존 체계를 따른다
(asset_holdings=partial · ownership=parsing_failures→partial · proxy_contest=lookup_failed+parsing_failures · firm=경고 · screener=detail_status error).

입력은 합성이다(실제 회사 응답 원본을 이 저장소에 넣지 않는다) — 5% 대량보유 목록만 저장소의 기존 고정본(고려아연)을 쓴다.
실제 응답 재생 대조는 병합 전에 작업 폴더 밖에서 한다.
"""
from __future__ import annotations

import asyncio
import zipfile

import httpx
import pytest

from open_proxy_mcp.dart import client as dart_client
from open_proxy_mcp.dart.client import DartClientError
from open_proxy_mcp.services import asset_holdings as A
from open_proxy_mcp.services import ownership_structure as O
from open_proxy_mcp.services import price_multiple_data as S
from open_proxy_mcp.services import proxy_contest as PC
from open_proxy_mcp.services import screener as SC
from open_proxy_mcp.services.dart_safety import classify_degrade
from open_proxy_mcp.tools.asset_holdings import _render

from tests.test_ownership_pit_top_holder import KOREA_ZINC, _KZ, _Client as _OwnClient, _build as _own_build
from tests.test_pmd_found_defects import _run_live

_GUIDE_014_404 = "자료를 읽지 못했다 — 접수 당일이면 아직 없을 수 있다"
_GUIDE_STRICT = "기준 시점에 공개 여부를 확인할 수 없다"


def _run(coro):
    return asyncio.run(coro)


# ═══ ① 판정 함수 — 옛 규칙과 달라지는 것은 014·404(첨부정정을 모를 때)와 STRICT 뿐이다 ═══════════════════════════

@pytest.mark.parametrize("attachment", [False, True])
def test_013_is_none_either_way(attachment):
    failed, msg = O._dart_read_note(DartClientError("013", "x"), empty_text="없음", fail_text="조회 실패", attachment=attachment)
    assert (failed, msg) == (False, "없음")


@pytest.mark.parametrize("status", ["014", "404"])
def test_014_404_are_failures_unless_the_caller_knows_it_is_an_attachment_correction(status):
    exc = DartClientError(status, "x")
    failed, msg = O._dart_read_note(exc, empty_text="없음", fail_text="원문 조회 실패")
    assert failed is True and msg == f"원문 조회 실패 — {_GUIDE_014_404}"
    # 기본값 = 「모른다」 = 실패 쪽. 제목을 아는 호출만 True 를 준다.
    assert O._dart_read_note(exc, empty_text="없음", fail_text="x", attachment=False)[0] is True
    assert O._dart_read_note(exc, empty_text="없음", fail_text="x", attachment=True) == (False, "없음")


def test_strict_point_in_time_unavailable_is_a_failure_without_a_retry_hint():
    failed, msg = O._dart_read_note(DartClientError("STRICT_PIT_UNAVAILABLE", "x"), empty_text="없음", fail_text="원문 조회 실패")
    assert failed is True and _GUIDE_STRICT in msg
    assert "잠시" not in msg and "다시 시도" not in msg      # 기준 시점은 일시 오류가 아니다 — 다시 불러도 같다
    # attachment=True 라도 STRICT 는 「없음」이 아니다
    assert O._dart_read_note(DartClientError("STRICT_PIT_UNAVAILABLE", "x"), empty_text="없음", fail_text="x", attachment=True)[0] is True


@pytest.mark.parametrize("exc", [
    DartClientError("020", "x"), DartClientError("800", "x"), DartClientError("900", "x"), DartClientError("010", "x"),
    DartClientError("011", "x"), DartClientError("KIND_NO_DOC", "x"), DartClientError("TRANSPORT_ERROR", "x"),
    httpx.ReadTimeout("x"), zipfile.BadZipFile("x"), RuntimeError("x"),
])
def test_every_other_failure_keeps_the_old_verdict_and_wording(exc):
    for attachment in (False, True):
        failed, msg = O._dart_read_note(exc, empty_text="없음", fail_text="조회 실패", attachment=attachment)
        assert failed is True and msg == f"조회 실패 — {classify_degrade(exc)[1]}"


def test_public_description_no_longer_calls_014_404_a_plain_absence():
    """도구 설명이 응답과 어긋나지 않게 — 읽는 AI 가 「014·404 는 실패가 아니라 없음」으로 읽고 조회 실패를 없음으로 옮기지 않는다."""
    from open_proxy_mcp.tools import ownership_structure as T

    captured = []

    class _Mcp:
        def tool(self, *a, **k):
            return lambda fn: captured.append(fn) or fn

    T.register_tools(_Mcp())
    doc = captured[0].__doc__
    assert "013/014/404는 실패가 아니라" not in doc
    assert "013 만 「해당 자료 없음」" in doc


# ═══ ② asset_holdings summary ═══════════════════════════════════════════════════════════════════════════════

_CASH = {"sj_div": "BS", "account_id": "ifrs-full_CashAndCashEquivalents", "account_nm": "현금및현금성자산", "thstrm_amount": "100,000,000,000"}
FIN_OK = {"list": [_CASH]}
FIN_BANK = {"list": [_CASH, {"sj_div": "BS", "account_id": "dart_Deposits", "account_nm": "예수부채", "thstrm_amount": "5,000,000,000"}]}
E013 = DartClientError("013", "조회된 데이타가 없습니다.")


def _asset(monkeypatch, *, cfs=FIN_OK, ofs=None, info=None, otr=None, code="11011", mcap=None, scope="summary"):
    """각 조회의 답: 예외 인스턴스면 올리고, 아니면 그 dict. 반환 (payload, 재무 호출 목록 [(reprt_code, fs)])."""
    calls: list[tuple[str, str]] = []
    scripted = {"CFS": cfs, "OFS": ofs if ofs is not None else E013}

    def give(v):
        if isinstance(v, BaseException):
            raise v
        return v

    class Cli:
        async def get_fnltt_singl_acnt_all(self, cc, y, rc, fs):
            calls.append((rc, fs))
            return give(scripted[fs])

        async def get_company_info(self, cc):
            return give({"induty_code": "467"} if info is None else info)

        async def get_other_corp_investment(self, *a):
            return give({"list": []} if otr is None else otr)

    async def resolve(q):
        from types import SimpleNamespace
        return SimpleNamespace(status=A.AnalysisStatus.EXACT, selected={"corp_code": "00000001", "stock_code": "000001", "corp_name": q}, candidates=[])

    async def pick(client, cc, report, year):
        return {"rcept_no": "20260312000856", "report_nm": "사업보고서 (2025.12)"}, code, ""

    async def market_cap(isu):
        return (mcap, {"date": "20261016"}) if mcap else (None, {"reason": "test"})

    async def fake_mark(client, holdings, doc_text, price_dd=None, qty_dd=None):
        return {"marked": [], "unresolved": [], "price_date": None, "price_source": None, "qty_date": None, "qty_adjust_checked": True,
                "listed_book_krw": 0, "listed_mkt_krw": 0, "unrealized_gap_krw": 0, "n_listed": 0, "n_marked": 0, "n_unresolved": 0,
                "unlisted_book_krw": 0, "unlisted_majority_book_krw": 0, "n_unlisted": 0}

    async def getdoc(client, rcept_no):
        return {"note_html": "<p>주석</p>", "full_text": "주석"}

    monkeypatch.setattr(A._bd, "_fetch_getdoc", getdoc)
    monkeypatch.setattr(A, "get_dart_client", lambda: Cli())
    monkeypatch.setattr(A, "resolve_company_query", resolve)
    monkeypatch.setattr(A, "_pick_periodic_report", pick)
    monkeypatch.setattr(A, "_market_cap", market_cap)
    monkeypatch.setattr(A, "_mark_listed_stakes", fake_mark)
    return _run(A._build_asset_holdings_payload_impl("X", scope=scope)), calls


def test_financial_statement_013_is_still_just_absence(monkeypatch):
    """회귀 방지(고치기 전에도 통과해야 한다) — 013 은 소규모기업·미제출의 「없음」이다."""
    p, calls = _asset(monkeypatch, cfs=E013, ofs=E013)
    assert p["status"] == "ok" and p["data"]["fs_div"] is None
    assert any("재무제표 계정 조회 불가(소규모기업" in w for w in p["warnings"])
    assert not any("조회 실패" in w for w in p["warnings"])
    assert calls == [("11011", "CFS"), ("11011", "OFS")]


@pytest.mark.parametrize("status", ["020", "800"])
def test_financial_statement_failure_is_partial_not_a_quiet_empty_ok(monkeypatch, status):
    p, _ = _asset(monkeypatch, cfs=DartClientError(status, "x"))
    assert p["status"] == "partial"
    assert any(w.startswith("재무제표 계정 조회 실패 — ") for w in p["warnings"]), p["warnings"]
    assert not any("소규모기업" in w for w in p["warnings"]), "실패에 「소규모기업·미제출」 사유를 붙이지 않는다"
    assert p["data"]["fs_div"] is None


def test_consolidated_failure_does_not_fall_to_separate_statements(monkeypatch):
    """연결(CFS) 조회가 013 이 아닌 이유로 실패했는데 별도(OFS)를 받아 오면 연결이 있는 회사를 별도 기준으로 읽는다."""
    p, calls = _asset(monkeypatch, cfs=DartClientError("800", "x"), ofs=FIN_OK)
    assert calls == [("11011", "CFS")]
    assert p["data"]["fs_div"] is None and p["status"] == "partial"


def test_consolidated_013_still_falls_to_separate_statements(monkeypatch):
    p, calls = _asset(monkeypatch, cfs=E013, ofs=FIN_OK)
    assert calls == [("11011", "CFS"), ("11011", "OFS")]
    assert p["data"]["fs_div"] == "OFS" and p["status"] == "ok"


def test_quarterly_empty_list_does_not_retry_with_the_opposite_quarter_code(monkeypatch):
    """분기 코드 반대 재시도(11014 → 11013)는 다른 분기 보고서를 이 보고서 이름표로 내보낼 수 있다 — 지운다.
    013 은 예외로 올라와 거기까지 닿지 않았고, 상태 000 인데 list 가 빈 경우에만 닿았다."""
    p, calls = _asset(monkeypatch, cfs={"list": []}, ofs={"list": []}, code="11014")
    assert calls == [("11014", "CFS"), ("11014", "OFS")]
    assert p["status"] == "ok" and p["data"]["fs_div"] is None


def test_company_info_failure_is_not_judged_as_non_financial_non_holding(monkeypatch):
    """회사개황(업종코드) 조회 실패를 「일반 금융·지주가 아님」으로 확정하면 지주사 잉여자산 배수가 거짓으로 나간다.
    BS 신호가 없으면 판정 불가(None)이고 두 배수는 비운다(절대액은 그대로)."""
    p, _ = _asset(monkeypatch, info=DartClientError("800", "x"), mcap=1_000_000_000_000)
    d = p["data"]
    assert p["status"] == "partial"
    assert d["is_financial"] is None and d["is_holding_company"] is None and d["induty_code"] is None
    assert d["nav"]["surplus_cov"] is None and d["nav"]["equity_nav_cov"] is None
    assert d["nav"]["surplus_krw"] == 100_000_000_000          # 절대액은 비금융 산식으로 두고 경고로 밝힌다
    assert any(w.startswith("회사개황 조회 실패 — ") for w in p["warnings"]), p["warnings"]
    assert any("업종코드를 몰라 금융·지주 판정과" in w for w in p["warnings"]), p["warnings"]
    md = _render(p)
    assert "## X — 보유 자산" in md and "금융·지주 판정을 못 해 배수 미제공" in md


def test_company_info_failure_keeps_a_financial_verdict_the_balance_sheet_proves(monkeypatch):
    p, _ = _asset(monkeypatch, cfs=FIN_BANK, info=DartClientError("800", "x"), mcap=1_000_000_000_000)
    d = p["data"]
    assert d["is_financial"] is True and d["is_holding_company"] is False and p["status"] == "partial"
    assert d["nav"]["surplus_cov"] is None            # 금융업은 원래 배수를 내지 않는다


def test_company_info_013_and_success_keep_the_old_verdicts(monkeypatch):
    for info in ({"induty_code": "467"}, E013):
        p, _ = _asset(monkeypatch, info=info, mcap=1_000_000_000_000)
        assert p["status"] == "ok" and p["data"]["is_financial"] is False and p["data"]["is_holding_company"] is False
        assert p["data"]["nav"]["surplus_cov"] == pytest.approx(0.1)


def test_holdings_list_failure_does_not_read_as_zero_holdings(monkeypatch):
    """타법인 출자현황 조회 실패 → 보유지분 0건이 아니라 값을 비운다(#217 의 주석 실패와 같은 길 — 사유 문구만 다르다)."""
    p, _ = _asset(monkeypatch, otr=DartClientError("800", "x"), mcap=1_000_000_000_000)
    d = p["data"]
    assert p["status"] == "partial"
    assert d["listed_stakes"]["unavailable"] is True and "출자현황" in d["listed_stakes"]["unavailable_reason"]
    assert d["nav"]["equity_nav_krw"] is None and d["nav"]["equity_nav_basis"] is None and d["nav"]["equity_nav_cov"] is None
    assert "출자현황" in d["nav"]["equity_nav_unavailable"]
    assert "잠시 뒤 다시 조회할 것" not in d["nav"]["equity_nav_unavailable"]      # 재시도 안내는 사유 문구(_dart_read_note) 것만 — 020 의 「자정 이후」와 겹치지 않게
    assert d["nav"]["haircut_flags"] == [] and d["haircuts"]["pledged"] is not None      # 주석은 읽었다 — 감액 표시는 비우지 않는다
    assert any(w.startswith("타법인 출자현황 조회 실패 — ") for w in p["warnings"])
    md = _render(p)
    assert "지분 NAV**: 산출 못 함" in md and "상장 보유지분 시가마크: 산출 못 함" in md


def test_holdings_list_013_is_still_an_empty_list(monkeypatch):
    p, _ = _asset(monkeypatch, otr=E013, mcap=1_000_000_000_000)
    assert p["status"] == "ok" and p["data"]["nav"]["equity_nav_basis"] == "balance_sheet"
    assert "unavailable" not in p["data"]["listed_stakes"]


def test_renderer_draws_a_partial_answer_instead_of_only_the_warning(monkeypatch):
    p, _ = _asset(monkeypatch, otr=DartClientError("800", "x"))
    md = _render(p)
    assert md.startswith("## X — 보유 자산") and "⚠ " in md and "타법인 출자현황 조회 실패" in md
    # 자료 없이 경고만 있는 실패 모양(invalid·not_found 등)은 종전처럼 경고 한 줄
    assert _render({"status": "not_found", "subject": "Y", "warnings": ["회사를 찾지 못했다"]}) == "**Y** — 회사를 찾지 못했다"


# ═══ ③ ownership 5% 보고 원문 — 읽지 못한 행 표식 ═══════════════════════════════════════════════════════════

class _FailingDocs(_OwnClient):
    def __init__(self, exc, **kw):
        super().__init__(**kw)
        self.exc = exc

    async def get_document_cached(self, rcept_no):
        self.documents.append(rcept_no)
        raise self.exc


def _own(monkeypatch, exc, **kw):
    return _own_build(monkeypatch, _FailingDocs(exc, blocks=_KZ), KOREA_ZINC, scope="blocks", **kw)


def _marked(payload):
    return [b for b in payload["data"]["blocks"] if b.get("doc_read_failed")]


@pytest.mark.parametrize("exc", [DartClientError("800", "x"), DartClientError("014", "x"), DartClientError("404", "x"),
                                 DartClientError("STRICT_PIT_UNAVAILABLE", "x"), RuntimeError("x")])
def test_unreadable_block_filing_marks_the_row_and_the_status_is_partial(monkeypatch, exc):
    """5% 보고 원문(보유 목적·합계표)을 못 읽으면 말없이 삼키지 않는다 — 읽지 못한 ≥5% 행에 표식, 상태는 partial.
    5% 대량보유 행에는 제목 칸이 없어 첨부정정인지 모른다 → 014·404 도 실패 쪽."""
    p = _own(monkeypatch, exc)
    marked = _marked(p)
    assert marked, "고려아연 고정본: 약식(국민연금)·일반(합계표) 행이 원문을 읽는다"
    assert p["status"] == "partial" and p["data"]["filing_status"] == "partial_failure"
    assert p["data"]["parsing_failures"] == len(marked)
    assert all(b["ownership_pct"] >= 5.0 for b in marked)


def test_the_marker_carries_the_reason_so_strict_runs_are_not_told_to_retry(monkeypatch):
    p = _own(monkeypatch, DartClientError("STRICT_PIT_UNAVAILABLE", "x"))
    assert all(_GUIDE_STRICT in b["doc_read_failed"] and "잠시" not in b["doc_read_failed"] for b in _marked(p))
    p = _own(monkeypatch, DartClientError("014", "x"))
    assert all(_GUIDE_014_404 in b["doc_read_failed"] for b in _marked(p))


def test_block_filing_013_is_absence_and_leaves_the_status_alone(monkeypatch):
    """원문 호출이 013 이면(코드에 그 경로는 없다 — 이론상) 이전과 같다: 표식 없음·상태 불변."""
    p = _own(monkeypatch, DartClientError("013", "x"))
    assert _marked(p) == [] and p["status"] == "exact" and p["data"]["parsing_failures"] == 0


def test_a_readable_filing_leaves_no_marker(monkeypatch):
    p = _own_build(monkeypatch, _OwnClient(blocks=_KZ), KOREA_ZINC, scope="blocks")
    assert _marked(p) == [] and p["status"] == "exact"


def test_the_verdict_does_not_lean_on_the_wording(monkeypatch):
    """표식·partial·proxy_contest 판정은 문구 접두어가 아니라 행 표식과 구조 칸으로 나른다 — 문구를 바꿔도 같은 결과."""
    monkeypatch.setattr(O, "_dart_read_note", lambda exc, **kw: (True, "임의의 다른 문구"))
    p = _own(monkeypatch, DartClientError("800", "x"))
    assert _marked(p) and p["status"] == "partial"
    assert {b["doc_read_failed"] for b in _marked(p)} == {"임의의 다른 문구"}


def test_a_failed_row_under_five_percent_does_not_change_the_status(monkeypatch):
    """5% 밑 행(이탈·청산 보고)은 ownership 신호와 status 에 닿지 않는다 — 표식은 붙되(행 자료) 세지 않는다."""
    data = {"list": [{"repror": "소액보유자", "rcept_dt": "20260601", "rcept_no": "20260601000100", "stkrt": "3.20", "stkqy": "100",
                      "report_tp": "약식", "report_resn": "단순추가취득/처분", "corp_code": "00102858", "corp_name": "고려아연"}]}
    p = _own_build(monkeypatch, _FailingDocs(DartClientError("800", "x"), blocks=data), KOREA_ZINC, scope="blocks")
    assert p["data"]["blocks"] == [] and p["status"] != "partial" and p["data"]["parsing_failures"] == 0
    monkeypatch.setattr(O, "get_dart_client", lambda: _FailingDocs(DartClientError("800", "x"), blocks=data))
    rows, _timeline, note = _run(O._latest_block_rows("00102858"))
    assert rows and rows[0].get("doc_read_failed") and note is None


def test_block_list_failure_keeps_its_old_path(monkeypatch):
    """5% 목록 조회 자체의 실패(020)는 종전 문구·판정 그대로 — 이번에 바꾸지 않았다."""
    class _Cli:
        async def get_block_holders(self, cc):
            raise DartClientError("020", "x")
    monkeypatch.setattr(O, "get_dart_client", lambda: _Cli())
    rows, timeline, note = _run(O._latest_block_rows("x"))
    assert rows == [] and timeline == [] and note.startswith(PC._BLOCK_LOOKUP_FAILED)


# ═══ ④ proxy_contest — 원문을 못 읽었으면 신호 0건을 「정상」이라 하지 않는다 ═════════════════════════════════

def _control(monkeypatch, exc):
    from tests.test_dart_no_data_wording import MIPO, _ControlClient

    monkeypatch.setattr(O, "get_dart_client", lambda: _FailingDocs(exc, blocks=_KZ))
    monkeypatch.setattr(PC, "get_dart_client", lambda: _ControlClient({"2026": None}))
    return _run(PC._control_context(MIPO["corp_code"], MIPO["corp_name"], 2027))


@pytest.mark.parametrize("exc", [DartClientError("800", "x"), DartClientError("014", "x"), DartClientError("STRICT_PIT_UNAVAILABLE", "x")])
def test_control_context_treats_unreadable_block_filings_as_unknown(monkeypatch, exc):
    ctx, warnings = _control(monkeypatch, exc)
    assert ctx["lookup_failed"] is True and ctx["block_doc_failures"] >= 1
    assert not any(w.startswith(PC._BLOCK_LOOKUP_FAILED) for w in warnings), "목록 조회 실패 문구에 기대지 않는다"


def test_control_context_with_readable_or_absent_filings_is_unchanged(monkeypatch):
    ctx, _ = _control(monkeypatch, DartClientError("013", "x"))
    assert ctx["lookup_failed"] is False and ctx.get("block_doc_failures", 0) == 0


def test_proxy_contest_payload_is_partial_with_the_failure_count(monkeypatch):
    """신호가 일부 있어도(filing_count>0 이면 fetch_failed 가 무시된다) 원문 실패 건수가 parsing_failures 로 가서 partial 이다."""
    from tests.test_dart_no_data_wording import _fake_resolve

    monkeypatch.setenv("OPENDART_API_KEY", "test-key-not-real")

    async def request(self, endpoint, params):
        if endpoint.startswith("majorstock"):
            return _KZ
        if endpoint.startswith(("hyslrSttus", "stockTotqySttus", "tesstkAcqsDspsSttus")):
            return {"list": []}                    # 지분 맥락 조회는 성공(빈 명부)
        raise DartClientError("013", "x")          # 나머지 조회는 정상적인 「없음」

    async def binary(self, endpoint, params):
        raise DartClientError("800", "점검")        # 5% 보고 원문만 읽지 못한다

    monkeypatch.setattr(dart_client.DartClient, "_request", request)
    monkeypatch.setattr(dart_client.DartClient, "_request_binary", binary)
    monkeypatch.setattr(PC, "resolve_company_query", _fake_resolve)
    p = _run(PC.build_proxy_contest_payload("에이치디현대미포", start_date="20251001", end_date="20261031"))
    assert p["status"] == "partial"
    assert p["data"]["parsing_failures"] >= 1 and p["data"]["filing_status"] in ("partial_failure", "fetch_failed")
    assert not any("시그널 없음 (정상)" in w for w in p["warnings"])


# ═══ ⑤ price_multiple_data — 주식총수 조회 실패 ═══════════════════════════════════════════════════════════

class _TotalCli:
    def __init__(self, v):
        self.v = v

    async def get_stock_total(self, cc, year, rc):
        if isinstance(self.v, BaseException):
            raise self.v
        return self.v


_TOTAL = {"list": [{"se": "보통주", "distb_stock_co": "900"}, {"se": "합계", "distb_stock_co": "1,000"}]}


def test_shares_outstanding_ok_and_013_keep_the_old_two_key_dict():
    assert _run(S._shares_outstanding(_TotalCli(_TOTAL), "x", 2025)) == {"total": 1000, "common": 900}
    assert _run(S._shares_outstanding(_TotalCli(E013), "x", 2025)) == {"total": None, "common": None}


@pytest.mark.parametrize("status", ["020", "800", "014"])
def test_shares_outstanding_failure_is_recorded_not_collapsed_into_none(status):
    out = _run(S._shares_outstanding(_TotalCli(DartClientError(status, "x")), "x", 2025))
    assert out["total"] is None and out["common"] is None
    assert out["fetch_failed"].startswith("주식총수 조회 실패 — ")
    assert out["fetch_failed"] == O._dart_read_note(DartClientError(status, "x"), empty_text="", fail_text="주식총수 조회 실패")[1]


class _Shares:
    """`_run_live` 가 `_shares_outstanding` 을 넣는 자리를 가로챈다 — 주식총수 응답만 시험이 정한다."""

    def __init__(self, mp, replacement):
        self.mp, self.replacement = mp, replacement

    def setattr(self, obj, name, value):
        self.mp.setattr(obj, name, self.replacement if name == "_shares_outstanding" else value)


def _firm(monkeypatch, shares, **kw):
    async def sh(client, cc, year):
        return dict(shares)
    return _run_live(_Shares(monkeypatch, sh), **kw)[0]["data"]


_FUND_WARN = "펀드 공시"


def test_firm_says_the_share_count_lookup_failed_and_does_not_assert_a_fund(monkeypatch):
    d = _firm(monkeypatch, {"total": None, "common": None, "fetch_failed": "주식총수 조회 실패 — 점검 중"}, reports={}, alot=None, induty="64201")
    assert any(w.startswith("⚠️ 주식총수 조회 실패 — 점검 중") and "BPS" in w and "EPS" in w for w in d["warnings"]), d["warnings"]
    assert not any(_FUND_WARN in w for w in d["warnings"]), "조회 실패로 비었는데 「배당이 없다는 뜻이 아니다」 단정을 붙이지 않는다"


def test_firm_does_not_assert_a_fund_when_the_dividend_lookup_failed(monkeypatch):
    d = _firm(monkeypatch, {"total": None, "common": None}, reports={}, alot=None, induty="64201",
              alot_err={2025: "alotMatter 조회 실패: 020"})
    assert not any(_FUND_WARN in w for w in d["warnings"]), d["warnings"]


def test_firm_still_says_fund_when_all_three_are_really_absent(monkeypatch):
    """맥쿼리인프라형 — 세 조회가 모두 013(없음)으로 온 정상 펀드 공시 회사는 종전 경고 그대로."""
    d = _firm(monkeypatch, {"total": None, "common": None}, reports={}, alot=None, induty="64201")
    assert any(w.startswith("DART 정기보고서의 재무제표·주식 총수·배당 사항이 모두 비어 있다") for w in d["warnings"])
    assert not any("주식총수 조회 실패" in w for w in d["warnings"])


# ═══ ⑥ screener — 상세 빌더의 조회 실패(error)를 「무자료」로 바꾸지 않는다 ═══════════════════════════════════

def _detail(monkeypatch, payload):
    import open_proxy_mcp.services.dividend as D

    async def build(company, **kw):
        return payload

    class _Fake:
        def api_call_snapshot(self):
            return 0

    monkeypatch.setattr(D, "build_dividend_payload", build)
    monkeypatch.setattr(SC, "get_dart_client", lambda: _Fake())
    return _run(SC._fetch_detail({"_detail_kind": "dividend", "rcept_no": "20261021800001", "stock_code": "055550",
                                  "filed_at": "2026-10-21", "title": "x"}, {"calls": 0}))


def test_builder_error_stays_an_error_card(monkeypatch):
    res = _detail(monkeypatch, {"status": "error", "data": {}, "warnings": ["alotMatter 조회 실패 — DART 가 시스템 점검 중입니다."]})
    assert res["detail_status"] == "error" and res["fields"] == {}
    assert "조회 실패" in res["note"]
    from open_proxy_mcp.tools.screener import _DETAIL_BADGE
    assert _DETAIL_BADGE["error"] == " (조회실패)"


@pytest.mark.parametrize("status", ["ambiguous", "no_filing", "not_found"])
def test_nothing_found_is_still_no_data(monkeypatch, status):
    """공시 0건(no_filing)·회사 모호(ambiguous) 같은 「없음」 계열은 종전 배지 그대로."""
    res = _detail(monkeypatch, {"status": status, "data": {}, "warnings": []})
    assert res["detail_status"] == "no_data"
