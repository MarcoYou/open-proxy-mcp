# -*- coding: utf-8 -*-
"""배수 도구 — DART 전체 재무제표 조회 **실패**를 「자료 없음」과 가른다(261021, fin audit L1). network 0 · DB 0.

종전 `_acntall` 은 `DartClientError` 를 전부 `continue` 로 삼켰다 — 013(조회된 데이터 없음 = 정말 그 보고서가 없다)과 020(키의 하루 한도)·010/011/012/800·
전송 오류(조회 자체가 실패)를 구분하지 않았다. 그래서 firm 의 재무 조회가 **실패**한 날에도 응답은 N/M 이나 한 분기 이른 TTM 으로 조용히 나갔다
(분기 고르기 루프는 빈 응답을 보고 다음(더 이른) 후보로 내려간다). 이제:
  · 013 이면 종전대로(CFS → OFS, 둘 다 없으면 빈 결과 = 자료 없음).
  · 그 밖의 오류는 답이 아니다 — 별도(OFS)로 넘어가지 않고(연결이 있는데 못 읽은 것을 별도로 받아 기준이 섞이는 것을 막는다) 요청의 실패 목록에 적는다.
  · firm 은 분기 고르기를 멈추고(이른 분기로 대체하지 않고) 「재무 조회 실패 — 값 미확인」 경고와 `data_quality.dart_fetch_failures` 를 단다.
응답 키는 그대로이고 실패가 있을 때만 경고·칸이 는다.
"""
from __future__ import annotations

import asyncio
from datetime import date

import httpx
import pytest

from open_proxy_mcp.dart.client import DartClientError
from open_proxy_mcp.services import price_multiple_data as S

TODAY = date(2026, 10, 19)


# ═══ `_acntall` 단독 ══════════════════════════════════════════════════════════════════════════════
class _Client:
    """`get_fnltt_singl_acnt_all(cc, year, rc, fs)` 대역 — script[(year, rc, fs)] 가 행 목록이면 답, 예외 클래스·인스턴스면 올린다, 없으면 013."""

    def __init__(self, script: dict):
        self.script, self.calls = script, []

    async def get_fnltt_singl_acnt_all(self, cc, year, rc, fs):
        self.calls.append((int(year), rc, fs))
        v = self.script.get((int(year), rc, fs), DartClientError("013", "조회된 데이타가 없습니다."))
        if isinstance(v, BaseException):
            raise v
        return {"list": v}


ROWS = [{"sj_div": "BS", "account_id": "ifrs-full_Equity", "thstrm_amount": "1"}]


def _acnt(client, year=2025, rc="11011", fs=None, with_list=True):
    async def go():
        fails: list = []
        if with_list:
            S._ACNT_FAILS.set(fails)
        out = await S._acntall(client, "00126380", year, rc, fs)
        return out, fails
    return asyncio.run(go())


def test_013_on_consolidated_falls_to_separate_and_is_not_a_failure():
    """연결이 013(연결 재무제표가 없는 회사 — 카카오뱅크·코스모신소재)이면 종전대로 별도를 본다. 실패로 적지 않는다."""
    c = _Client({(2025, "11011", "OFS"): ROWS})
    (rows, fs), fails = _acnt(c)
    assert (rows, fs) == (ROWS, "OFS") and fails == []
    assert c.calls == [(2025, "11011", "CFS"), (2025, "11011", "OFS")]


def test_013_on_both_is_no_data_not_a_failure():
    c = _Client({})
    (rows, fs), fails = _acnt(c)
    assert (rows, fs) == ([], None) and fails == [], "정말 자료가 없다 — 실패 목록은 비어 있어야 한다(N/M 의 까닭이 「없음」)"


@pytest.mark.parametrize("status", ["020", "010", "011", "012", "800", "TRANSPORT_ERROR"])
def test_any_other_dart_status_is_a_failure_and_does_not_fall_to_separate(status):
    """020(키의 하루 한도)처럼 013 이 아닌 상태는 「자료 없음」이 아니다 — 별도로 넘어가면 연결이 있는데 못 읽은 것을 별도로 받아 기준이 섞인다."""
    c = _Client({(2025, "11011", "CFS"): DartClientError(status, "x"), (2025, "11011", "OFS"): ROWS})
    (rows, fs), fails = _acnt(c)
    assert (rows, fs) == ([], None)
    assert c.calls == [(2025, "11011", "CFS")], "연결 조회가 실패했으면 별도를 부르지 않는다"
    assert fails == [{"year": 2025, "reprt_code": "11011", "fs": "CFS", "status": status}]


def test_failure_on_forced_basis_is_recorded_with_that_basis():
    c = _Client({(2026, "11014", "OFS"): DartClientError("020", "한도")})
    (rows, fs), fails = _acnt(c, year=2026, rc="11014", fs="OFS")
    assert (rows, fs) == ([], None) and fails == [{"year": 2026, "reprt_code": "11014", "fs": "OFS", "status": "020"}]


@pytest.mark.parametrize("exc", [httpx.ReadTimeout("t"), httpx.ConnectError("c"), asyncio.TimeoutError()])
def test_transport_errors_are_failures_too(exc):
    c = _Client({(2025, "11011", "CFS"): exc})
    (rows, fs), fails = _acnt(c)
    assert (rows, fs) == ([], None) and len(fails) == 1 and fails[0]["fs"] == "CFS"
    assert fails[0]["status"] == type(exc).__name__


def test_without_a_request_list_nothing_is_recorded_and_the_result_is_the_old_empty_one():
    """요청 밖에서 부르면(목록이 없다) 종전처럼 빈 결과만 — 시험·옛 호출이 깨지지 않는다."""
    S._ACNT_FAILS.set(None)
    c = _Client({(2025, "11011", "CFS"): DartClientError("020", "x")})
    (rows, fs), fails = _acnt(c, with_list=False)
    assert (rows, fs) == ([], None) and fails == []


def test_failure_summary_folds_the_same_report_into_one_entry_in_first_failed_order():
    """같은 (사업연도, 보고서)의 실패는 하나로 — 연결·별도 두 번 실패해도 응답 `dart_fetch_failures`·경고에 한 줄. 순서는 처음 실패한 차례."""
    fails = [{"year": 2026, "reprt_code": "11012", "fs": "CFS", "status": "020"},
             {"year": 2025, "reprt_code": "11011", "fs": "CFS", "status": "800"},
             {"year": 2026, "reprt_code": "11012", "fs": "OFS", "status": "020"}]
    assert S._fetch_fail_summary(fails) == [
        {"year": 2026, "report": "반기보고서", "reprt_code": "11012", "status": "020"},
        {"year": 2025, "report": "사업보고서", "reprt_code": "11011", "status": "800"}]
    w = S._fetch_fail_warning(fails, ttm_stopped=False)
    assert w.count("2026 반기보고서") == 1 and "상태 020·요청 한도 초과" in w and "더 이른 분기로 대체하지 않았다" not in w
    assert S._fetch_fail_warning([], ttm_stopped=True) is None


# ═══ firm 경로 ═════════════════════════════════════════════════════════════════════════════════════
def _annual_rows(ni="100,000,000,000", eq="1,000,000,000,000"):
    return [
        {"sj_div": "BS", "account_id": "ifrs-full_Assets", "thstrm_amount": "5,000,000,000,000", "currency": "KRW"},
        {"sj_div": "BS", "account_id": "ifrs-full_Liabilities", "thstrm_amount": "3,000,000,000,000"},
        {"sj_div": "BS", "account_id": "ifrs-full_Equity", "thstrm_amount": "2,000,000,000,000"},
        {"sj_div": "BS", "account_id": "ifrs-full_EquityAttributableToOwnersOfParent", "thstrm_amount": eq},
        {"sj_div": "BS", "account_id": "ifrs-full_NoncontrollingInterests", "thstrm_amount": f"{2_000_000_000_000 - int(eq.replace(',', '')):,}"},
        {"sj_div": "IS", "account_id": "ifrs-full_ProfitLossAttributableToOwnersOfParent", "thstrm_amount": ni, "frmtrm_amount": ni},
    ]


def _cum_rows(ni_add, eq, ccy="KRW"):
    return [{"sj_div": "BS", "account_id": "ifrs-full_EquityAttributableToOwnersOfParent", "thstrm_amount": eq, "currency": ccy},
            {"sj_div": "IS", "account_id": "ifrs-full_ProfitLossAttributableToOwnersOfParent", "thstrm_amount": "1", "thstrm_add_amount": ni_add}]


#: 12월 결산 — 사업보고서 2025, 2026 반기(당해)·2025 반기(전년 동기). 3분기(2026 11014)는 아직 없다(013).
REPORTS = {
    (2025, "11011", "CFS"): _annual_rows(),
    (2026, "11012", "CFS"): _cum_rows("60,000,000,000", "1,050,000,000,000"),
    (2025, "11012", "CFS"): _cum_rows("40,000,000,000", "950,000,000,000"),
}


def _run(mp, script, *, acc_mt="12", induty="26110", db=None):
    client = _Client(script)
    seen = {"sql": []}

    async def resolve(q):
        return {"corp_name": "가나다", "stock_code": "123450", "corp_code": "01363818"}, None

    async def fin_metrics(code, **kw):
        return {"data": {"summary": {"eps_krw": None, "revenue_krw": 600_000_000_000, "roe_pct": 5.0,
                                     "capital_impairment_status": "normal", "reprt_code": "11011"}, "year": 2025}}

    async def market_for(code):
        return {"price": 4080, "date": "20261016", "common_mktcap": 1_000_000_000_000, "list_shrs": 245_000_000, "source": "db"}

    async def shares_out(c, cc, year):
        return {"total": 245_000_000, "common": 245_000_000}

    async def annual_summary(cc, year):
        return {}, None

    class Dart(_Client):
        async def get_company_info(self, cc):
            return {"induty_code": induty, "acc_mt": acc_mt}

    dart = Dart(script)

    def rows(sql, params=()):
        s = " ".join(sql.split())
        seen["sql"].append(s)
        return (db(s) if db else None) or []
    mp.setattr(S, "_pg_rows", rows)
    mp.setattr(S, "today_kst", lambda: TODAY)
    mp.setattr(S, "_resolve_listed", resolve)
    mp.setattr(S, "get_dart_client", lambda: dart)
    mp.setattr(S, "build_financial_metrics_payload", fin_metrics)
    mp.setattr(S, "_market_for", market_for)
    mp.setattr(S, "_shares_outstanding", shares_out)
    mp.setattr(S, "_annual_summary", annual_summary)
    p = asyncio.run(S.build_valuation_payload("123450", format="json"))
    return p, dart.calls


def test_no_failure_keeps_the_response_exactly_as_before(monkeypatch):
    """013 만 있는 평소 응답: 실패 경고도 `dart_fetch_failures` 칸도 없다. 3분기(013)에서 반기로 내려가 TTM 을 낸다(종전 그대로)."""
    p, calls = _run(monkeypatch, dict(REPORTS))
    d = p["data"]
    assert d["inputs"]["ttm_quarter"] == "반기 누적" and d["inputs"]["net_income_ttm_krw"] == 120_000_000_000
    assert d["multiples"]["per_ttm"] == round(1_000_000_000_000 / 120_000_000_000, 2)
    assert "dart_fetch_failures" not in d["data_quality"]
    assert not any("재무 조회 실패" in w for w in d["warnings"])
    assert (2026, "11014", "CFS") in calls, "3분기부터 내려오며 찾는다 — 013 은 다음 후보로 내려가는 정상 흐름"


def test_a_failed_latest_quarter_stops_the_descent_instead_of_hiding_behind_an_earlier_one(monkeypatch):
    """3분기 조회가 020 으로 **실패**하면 반기로 내려가 한 분기 낡은 TTM 을 최신인 양 내지 않는다 — TTM 은 값 미확인이고 까닭을 적는다."""
    script = dict(REPORTS)
    script[(2026, "11014", "CFS")] = DartClientError("020", "요청 한도 초과")
    p, calls = _run(monkeypatch, script)
    d = p["data"]
    assert [c for c in calls if c[1] in ("11012", "11013")] == [], "실패 뒤에는 더 이른 분기(반기·1분기)를 부르지 않는다"
    assert d["multiples"]["per_ttm"] is None and d["inputs"]["net_income_ttm_krw"] is None
    assert d["multiples"]["per_fy0"] is not None, "연간(FY0)은 읽었으니 그대로 낸다"
    w = [x for x in d["warnings"] if "재무 조회 실패" in x]
    assert len(w) == 1 and "2026 3분기보고서(상태 020·요청 한도 초과)" in w[0] and "013" in w[0] and "더 이른 분기로 대체하지 않았다" in w[0]
    assert d["data_quality"]["dart_fetch_failures"] == [{"year": 2026, "report": "3분기보고서", "reprt_code": "11014", "status": "020"}]


def test_a_failed_prior_year_pair_also_stops_the_descent(monkeypatch):
    """당해 3분기는 읽었는데 전년 동기가 실패 — 짝을 못 뺀다. 반기로 내려가지 않는다."""
    script = dict(REPORTS)
    script[(2026, "11014", "CFS")] = _cum_rows("90,000,000,000", "1,080,000,000,000")
    script[(2025, "11014", "CFS")] = DartClientError("020", "한도")
    p, calls = _run(monkeypatch, script)
    d = p["data"]
    assert [c for c in calls if c[1] in ("11012", "11013")] == []
    assert d["multiples"]["per_ttm"] is None
    assert d["data_quality"]["dart_fetch_failures"] == [{"year": 2025, "report": "3분기보고서", "reprt_code": "11014", "status": "020"}]


def test_a_failed_annual_is_said_and_never_read_as_a_missing_report(monkeypatch):
    """사업보고서 조회 실패: FY0 가 비는 까닭을 「자료 없음」이 아니라 「조회 실패」로 적는다. 연결 실패 뒤 별도(OFS)를 부르지 않는다."""
    script = dict(REPORTS)
    script[(2025, "11011", "CFS")] = DartClientError("020", "한도")
    script[(2025, "11011", "OFS")] = _annual_rows(ni="1,000,000,000")           # 불렸다면 연결 대신 별도를 읽었을 값
    p, calls = _run(monkeypatch, script)
    d = p["data"]
    assert (2025, "11011", "OFS") not in calls
    assert d["multiples"]["per_fy0"] is None and d["inputs"]["net_income_fy0_krw"] is None
    assert any("2025 사업보고서(상태 020·요청 한도 초과)" in w for w in d["warnings"])
    assert {"year": 2025, "report": "사업보고서", "reprt_code": "11011", "status": "020"} in d["data_quality"]["dart_fetch_failures"]


def test_non_december_filer_does_not_read_a_failed_quarter_as_no_quarter_yet(monkeypatch):
    """12월 결산이 아닌 회사는 사업보고서 뒤 분기 칸이 아직 없으면 TTM = 그 사업보고서 연간 값이다(`ttm_is_annual`). 분기 조회가 **실패**한 것은
    「분기가 아직 없다」가 아니다 — 연간 값으로 바꾸지 않고 값 미확인으로 둔다."""
    base = {(2025, "11011", "CFS"): _annual_rows()}

    def db(s):      # 결산월 칸(재무 요약 표 `acc_mt`) = 3 → 3월 결산으로 읽는다
        return [(3,)] if s.startswith("SELECT acc_mt FROM dart_fundamentals") else None
    ok, _ = _run(monkeypatch, dict(base), acc_mt="03", db=db)
    assert ok["data"]["inputs"]["ttm_quarter"] == "사업보고서(연간)", "분기 보고서가 정말 없으면(013) TTM = 연간 — 종전 그대로"
    failed = dict(base)
    for y in (2024, 2025, 2026):
        for rc in ("11012", "11013", "11014"):
            failed[(y, rc, "CFS")] = DartClientError("020", "한도")
    bad, calls = _run(monkeypatch, failed, acc_mt="03", db=db)
    d = bad["data"]
    assert d["inputs"]["ttm_quarter"] != "사업보고서(연간)" and d["inputs"]["net_income_ttm_krw"] is None
    assert any("재무 조회 실패" in w for w in d["warnings"])
    assert len([c for c in calls if c[1] in ("11014", "11012", "11013")]) == 1, "첫 실패에서 멈춘다 — 한도가 찬 키로 남은 후보를 연달아 부르지 않는다"
