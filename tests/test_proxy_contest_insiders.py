"""임원·주요주주 특정증권등 소유상황보고(D002) 재개방 회귀 테스트 (260828).

D002 는 성능 때문에 통째로 꺼져 있었다. 전종목 스캔에서는 옳은 판단이었지만, 회사 한 곳을
볼 때는 **5% 문턱 아래에서 지배주주·특수관계인이 매집하는 움직임**을 통째로 버리는 것이었다.

여기서 지키는 것 넷:
  1. `_proxy_items`(list.json 키워드 스캔)의 상세유형은 그대로다 — 페이지컷 회귀 방지.
  2. 못 가져온 이유를 「없음」과 「실패」로 가른다.
  3. 상한에 걸리면 **경고한다** — 조용히 자르지 않는다.
  4. 공시에 안 적힌 값을 0으로 채우지 않는다.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from open_proxy_mcp.dart.client import DartClientError
from open_proxy_mcp.services import proxy_contest as svc
from open_proxy_mcp.services.proxy_contest import (
    _aggregate_insider_rows,
    _annotate_insider_reporters,
    _insider_float,
    _insider_holdings,
    _insider_int,
    _proxy_items,
)
from open_proxy_mcp.tools.proxy_contest import _render_insiders


def _row(name, date_, rcept, shares, change, rate="0.00", ofcps="상무", rgist="비등기임원", main="-"):
    return {
        "rcept_no": rcept, "rcept_dt": date_, "repror": name,
        "isu_exctv_rgist_at": rgist, "isu_exctv_ofcps": ofcps, "isu_main_shrholdr": main,
        "sp_stock_lmp_cnt": shares, "sp_stock_lmp_irds_cnt": change,
        "sp_stock_lmp_rate": rate, "sp_stock_lmp_irds_rate": "0.00",
    }


class _FakeClient:
    """elestock 만 흉내내는 최소 클라이언트."""

    def __init__(self, rows=None, error: DartClientError | None = None):
        self._rows = rows or []
        self._error = error
        self.calls = 0

    async def get_executive_holdings(self, corp_code: str) -> dict:
        self.calls += 1
        if self._error:
            raise self._error
        return {"list": self._rows}


@pytest.fixture
def fake_client(monkeypatch):
    holder = {}

    def _install(client):
        holder["c"] = client
        monkeypatch.setattr(svc, "get_dart_client", lambda: client)
        return client

    return _install


# ── 1. 페이지컷 회귀 방지 ──────────────────────────────────────────────────────

def test_proxy_items_detail_types_unchanged() -> None:
    """D002 를 열되 **list.json 키워드 스캔에는 넣지 않는다.**

    `_PROXY_KEYWORDS` 에 임원 보고 제목이 없어 D002 를 넣어도 matched 는 한 건도 안 늘고
    (실측 5사 전부 동일), 삼성전자만 API 콜이 3→11 로 늘며 「28페이지 중 10페이지만 확인」이라는
    가짜 truncation 경고가 붙는다. 그래서 여기는 손대지 않는다.
    """
    source = inspect.getsource(_proxy_items)
    assert 'pblntf_detail_ty=["D001", "D003", "D004"]' in source
    assert '"D002"' not in source


# ── 2. 빈칸을 0으로 채우지 않는다 ─────────────────────────────────────────────

@pytest.mark.parametrize("raw", ["-", "", "  ", "–", "해당사항없음"])
def test_blank_is_none_not_zero(raw) -> None:
    assert _insider_int(raw) is None
    assert _insider_float(raw) is None


def test_comma_numbers_parse() -> None:
    assert _insider_int("301,165") == 301165
    assert _insider_int("-3,650") == -3650
    assert _insider_float("25.42") == 25.42


def test_unparsed_change_is_counted_not_zero_filled() -> None:
    rows = [
        _row("김임원", "2026-08-01", "2026080100001", "1,000", "-"),
        _row("김임원", "2026-08-10", "2026081000001", "1,500", "500"),
    ]
    [agg] = _aggregate_insider_rows(rows, recent_since="20260601")
    assert agg["net_change_shares"] == 500          # 보유 1,000 → 1,500
    assert agg["reported_change_sum"] == 500        # 미기재 건은 증감 합계에서 빠진다
    assert agg["unparsed_change_count"] == 1        # 빠졌다는 사실을 같이 준다
    assert agg["report_count"] == 2


def test_initial_report_rows_are_not_summed_as_purchases() -> None:
    """DART 는 신규·재보고 행의 「증감」칸에 **보유 전량**을 적는다.

    실측(260828 금호석유화학 국민연금공단): 그대로 더하면 순증감 +13,710,029주가 나오는데
    실제 보유는 2,752,107주다. 순증감은 보유 수량의 차이로 내야 한다.
    """
    rows = [
        _row("국민연금공단", "2025-09-05", "n1", "2,841,202", "-12,104", main="10%이상주주"),
        _row("국민연금공단", "2026-01-20", "n2", "2,806,340", "2,806,340", main="10%이상주주"),  # 신규·재보고
        _row("국민연금공단", "2026-07-31", "n3", "2,752,107", "-15,407", main="10%이상주주"),
    ]
    [agg] = _aggregate_insider_rows(rows, recent_since="20260601")
    assert agg["net_change_shares"] == 2752107 - 2841202     # 보유 차이 = -89,095
    assert agg["net_change_basis"] == "levels"
    assert agg["reported_change_sum"] == -12104 - 15407      # 신규보고 행은 빠진다
    assert agg["initial_report_count"] == 1
    assert agg["direction"] == "decreasing"
    # 신규보고 전량이 「매집」으로 오인되지 않는다.
    assert agg["recent_window"]["accumulating"] is False


def test_single_report_reporter_says_change_is_unknown() -> None:
    """보고가 1건이면 「변화」를 말할 수 없다 — 0으로도, 전량 매수로도 적지 않는다."""
    rows = [_row("크루시블제이브이", "2026-01-06", "c1", "2,209,716", "2,209,716",
                 rate="10.59", ofcps="-", rgist="-", main="10%이상주주")]
    [agg] = _aggregate_insider_rows(rows, recent_since="20260601")
    assert agg["net_change_shares"] is None
    assert agg["net_change_basis"] == "initial_report_only"
    assert agg["initial_report_in_window"] is True
    assert agg["shares_last"] == 2209716
    assert agg["ownership_pct_last"] == 10.59


# ── 3. 보고자 단위 집계 — 누가·기간·순증감·최근 매집 ─────────────────────────

def test_aggregate_reports_who_period_net_change_and_recent_accumulation() -> None:
    rows = [
        _row("박지배", "2025-10-01", "a1", "100,000", "10,000", rate="1.00", main="사실상지배주주"),
        _row("박지배", "2026-07-01", "a2", "130,000", "30,000", rate="1.30", main="사실상지배주주"),
        _row("이임원", "2025-11-01", "b1", "500", "-200", rate="0.00"),
        _row("이임원", "2026-02-01", "b2", "300", "-200", rate="0.00"),
    ]
    out = _aggregate_insider_rows(rows, recent_since="20260601")
    by = {r["reporter"]: r for r in out}

    boss = by["박지배"]
    assert boss["first_date"] == "2025-10-01" and boss["last_date"] == "2026-07-01"
    assert boss["net_change_shares"] == 30000       # 보유 100,000 → 130,000
    assert boss["reported_change_sum"] == 40000     # 공시 증감칸 합계는 따로 준다
    assert boss["shares_last"] == 130000
    assert boss["ownership_pct_change_pp"] == 0.30
    assert boss["direction"] == "increasing"
    assert boss["major_shareholder_type"] == "사실상지배주주"
    assert boss["recent_window"]["accumulating"] is True
    assert boss["recent_window"]["reported_change_sum"] == 30000
    assert boss["recent_filings"][0]["rcept_no"] == "a2"   # 원문 접근 경로 (최신 먼저)

    exec_ = by["이임원"]
    assert exec_["direction"] == "decreasing"
    assert exec_["recent_window"]["accumulating"] is False  # 처분은 매집이 아니다

    # 주요주주 → 최근 매집 → 순증감 크기 순
    assert out[0]["reporter"] == "박지배"


def test_annotate_cross_reference_flags_only_facts() -> None:
    insiders = {"reporters": [{"reporter": "박지배"}, {"reporter": "이임원"}]}
    _annotate_insider_reporters(insiders, registry_names={"박지배"}, block_names=set())
    assert insiders["reporters"][0]["in_registry"] is True
    assert insiders["reporters"][0]["in_5pct_block"] is False
    assert insiders["reporters"][1]["in_registry"] is False


# ── 4. 못 가져온 이유를 가른다 ────────────────────────────────────────────────

def test_no_data_is_not_fetch_failure(fake_client) -> None:
    fake_client(_FakeClient(error=DartClientError("013", "조회된 데이타가 없습니다")))
    data, warnings = asyncio.run(_insider_holdings("00126380", "20250901", "20260828"))
    assert data["status_reason"] == "no_data"
    assert data["reporters"] == []
    assert any("[데이터 없음]" in w for w in warnings)
    assert not any("[호출 실패]" in w for w in warnings)


def test_fetch_failure_is_not_reported_as_absence(fake_client) -> None:
    fake_client(_FakeClient(error=DartClientError("020", "사용한도 초과")))
    data, warnings = asyncio.run(_insider_holdings("00126380", "20250901", "20260828"))
    assert data["status_reason"] == "fetch_failed"
    # 「없음」과 구별되도록 빈 리스트가 아니라 None 이다.
    assert data["reporters"] is None
    assert any("[호출 실패]" in w and "020" in w for w in warnings)


def test_window_filter_excludes_out_of_range_reports(fake_client) -> None:
    fake_client(_FakeClient(rows=[
        _row("김임원", "2020-01-01", "old", "100", "100"),
        _row("김임원", "2026-08-01", "new", "200", "100"),
    ]))
    data, _ = asyncio.run(_insider_holdings("x", "20250901", "20260828"))
    assert data["coverage"]["rows_all_history"] == 2
    assert data["coverage"]["rows_in_window"] == 1
    assert data["reporters"][0]["report_count"] == 1


# ── 5. 상한에 걸리면 경고한다 (조용히 자르지 않는다) ─────────────────────────

def test_row_limit_truncation_warns_with_numbers(fake_client) -> None:
    rows = [
        _row(f"임원{i:03d}", f"2026-08-{(i % 28) + 1:02d}", f"r{i}", "100", "10")
        for i in range(120)
    ]
    fake_client(_FakeClient(rows=rows))
    data, warnings = asyncio.run(_insider_holdings("x", "20250901", "20260828", rows_limit=50))
    cov = data["coverage"]
    assert cov["truncated"] is True
    assert cov["rows_in_window"] == 120 and cov["rows_analyzed"] == 50 and cov["rows_dropped"] == 70
    warn = next(w for w in warnings if "[상한 도달]" in w)
    assert "120" in warn and "50" in warn and "70" in warn
    assert "insider_rows_limit" in warn


def test_row_limit_is_clamped_to_max(fake_client) -> None:
    fake_client(_FakeClient(rows=[]))
    data, _ = asyncio.run(_insider_holdings("x", "20250901", "20260828", rows_limit=999999))
    assert data["coverage"]["rows_limit"] == svc._INSIDER_ROWS_LIMIT_MAX


# ── 6. 렌더러 — 화면에서도 세 상태가 구별된다 ────────────────────────────────

def test_render_distinguishes_absence_from_failure() -> None:
    absent = "\n".join(_render_insiders({"insider_holdings": {"status_reason": "no_data"}}, "summary"))
    failed = "\n".join(_render_insiders({"insider_holdings": {"status_reason": "fetch_failed"}}, "summary"))
    assert "[데이터 없음]" in absent
    assert "[호출 실패]" in failed and "없다」는 뜻이 아니다" in failed


def test_render_shows_truncation_and_missing_values() -> None:
    payload = {"insider_holdings": {
        "status_reason": "ok",
        "coverage": {"rows_in_window": 2707, "rows_analyzed": 500, "rows_dropped": 2207,
                     "truncated": True, "rows_limit": 500, "analyzed_from_date": "2026-07-21",
                     "rows_all_history": 3398},
        "reporter_count": 1,
        "reporters": [{
            "reporter": "박지배", "position": "회장", "registered_executive": "등기임원",
            "major_shareholder_type": "사실상지배주주", "report_count": 3,
            "first_date": "2026-07-21", "last_date": "2026-08-20",
            "shares_first": None, "shares_last": 130000,
            "net_change_shares": None, "net_change_basis": "unknown",
            "reported_change_sum": None, "initial_report_count": 0,
            "initial_report_in_window": False, "unparsed_change_count": 3,
            "ownership_pct_last": None, "in_registry": True, "in_5pct_block": False,
            "recent_window": {"days": 90, "since": "2026-05-30", "report_count": 3,
                              "net_change_shares": None, "reported_change_sum": None,
                              "accumulating": False},
            "recent_filings": [],
        }],
    }}
    text = "\n".join(_render_insiders(payload, "summary"))
    assert "상한 500건에 걸렸다" in text and "2,207건" in text
    assert "미기재" in text and "| 0 |" not in text     # 빈칸을 0으로 찍지 않는다
    assert "박지배" in text and "명부" in text


def test_render_notes_when_reporter_list_is_capped() -> None:
    reporters = [{
        "reporter": f"임원{i}", "position": None, "registered_executive": None,
        "major_shareholder_type": None, "report_count": 1,
        "first_date": "2026-08-01", "last_date": "2026-08-01",
        "shares_first": 0, "shares_last": 10,
        "net_change_shares": 10, "net_change_basis": "levels",
        "reported_change_sum": 10, "initial_report_count": 0,
        "initial_report_in_window": False, "unparsed_change_count": 0,
        "ownership_pct_last": 0.0, "in_registry": False, "in_5pct_block": False,
        "recent_window": {"days": 90, "since": "2026-05-30", "report_count": 1,
                          "net_change_shares": 10, "reported_change_sum": 10,
                          "accumulating": True},
        "recent_filings": [],
    } for i in range(30)]
    payload = {"insider_holdings": {
        "status_reason": "ok", "reporter_count": 30, "reporters": reporters,
        "coverage": {"rows_in_window": 30, "rows_analyzed": 30, "rows_dropped": 0,
                     "truncated": False, "rows_limit": 500, "rows_all_history": 30},
    }}
    text = "\n".join(_render_insiders(payload, "summary"))
    assert "30" in text and "8명만 표시" in text and "조용히 자른 것이 아니다" in text


def test_render_flags_when_two_bases_disagree() -> None:
    """보유 차이와 공시 증감칸 합계가 갈리면 **그 사실을 화면에 쓴다** — 한쪽만 조용히 고르지 않는다."""
    payload = {"insider_holdings": {
        "status_reason": "ok", "reporter_count": 1,
        "coverage": {"rows_in_window": 3, "rows_analyzed": 3, "rows_dropped": 0,
                     "truncated": False, "rows_limit": 500, "rows_all_history": 3},
        "reporters": [{
            "reporter": "국민연금공단", "position": None, "registered_executive": None,
            "major_shareholder_type": "10%이상주주", "report_count": 3,
            "first_date": "2025-09-05", "last_date": "2026-07-31",
            "shares_first": 2841202, "shares_last": 2752107,
            "net_change_shares": -89095, "net_change_basis": "levels",
            "reported_change_sum": -27511, "initial_report_count": 1,
            "initial_report_in_window": False, "unparsed_change_count": 0,
            "ownership_pct_last": 9.77, "in_registry": False, "in_5pct_block": True,
            "recent_window": {"days": 90, "since": "2026-05-30", "report_count": 2,
                              "net_change_shares": -15407, "reported_change_sum": -15407,
                              "accumulating": False},
            "recent_filings": [],
        }],
    }}
    text = "\n".join(_render_insiders(payload, "summary"))
    assert "2,841,202 → 2,752,107" in text
    assert "-89,095주" in text
    assert "신규보고 1건" in text and "증감합" in text


def test_render_says_single_report_change_is_unknown() -> None:
    payload = {"insider_holdings": {
        "status_reason": "ok", "reporter_count": 1,
        "coverage": {"rows_in_window": 1, "rows_analyzed": 1, "rows_dropped": 0,
                     "truncated": False, "rows_limit": 500, "rows_all_history": 1},
        "reporters": [{
            "reporter": "크루시블제이브이", "position": None, "registered_executive": None,
            "major_shareholder_type": "10%이상주주", "report_count": 1,
            "first_date": "2026-01-06", "last_date": "2026-01-06",
            "shares_first": 2209716, "shares_last": 2209716,
            "net_change_shares": None, "net_change_basis": "initial_report_only",
            "reported_change_sum": None, "initial_report_count": 1,
            "initial_report_in_window": True, "unparsed_change_count": 0,
            "ownership_pct_last": 10.59, "in_registry": False, "in_5pct_block": True,
            "recent_window": {"days": 90, "since": "2025-10-08", "report_count": 0,
                              "net_change_shares": None, "reported_change_sum": None,
                              "accumulating": False},
            "recent_filings": [],
        }],
    }}
    text = "\n".join(_render_insiders(payload, "summary"))
    assert "신규보고 (변화 산출 불가)" in text
    assert "10.59%" in text


def test_single_non_initial_report_uses_the_disclosed_change() -> None:
    """보고가 1건이라도 **신규보고가 아니면** 회사가 적은 증감을 쓴다 — 정보를 버리지 않는다."""
    rows = [_row("이임원", "2026-08-01", "s1", "500", "-200")]
    [agg] = _aggregate_insider_rows(rows, recent_since="20260601")
    assert agg["net_change_shares"] == -200
    assert agg["net_change_basis"] == "reported"
    assert agg["direction"] == "decreasing"
