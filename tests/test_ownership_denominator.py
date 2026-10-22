"""ownership_structure 지분율 분해 표의 분모 — 어느 보고서 어느 날짜의 주식수인지, 최신 상장주식수와 다르면 두 값과 차이(B5).

운영 재호출: 삼성전자 100% 분해 표의 분모(발행주식총수 5,919,637,922)가 trading_data 의 상장주식수 5,846,278,608 과
1.2% 달랐는데(차이 73,359,314주) 표에는 분모가 어느 보고서·어느 날짜 값인지도, 상장주식수와 다르다는 말도 없었다.
정기보고서 발행주식총수는 결산기준일 값이고 상장주식수는 KRX 의 최신 거래일 값이라 그 사이 소각·증자가 있으면 갈린다.

network 0 · DB 0 — DART 는 가짜 클라이언트, 상장주식수 읽기는 갈아 끼운다.
"""

from __future__ import annotations

import asyncio

from open_proxy_mcp.services import ownership_structure as O
from open_proxy_mcp.services.company import CompanyResolution
from open_proxy_mcp.services.contracts import AnalysisStatus
from open_proxy_mcp.tools.ownership_structure import _render

SAMSUNG = {"corp_code": "00126380", "corp_name": "삼성전자", "stock_code": "005930"}

_COMMON = {
    "rcept_no": "20260310002820", "corp_cls": "Y", "se": "보통주", "istc_totqy": "5,919,637,922",
    "tesstk_co": "91,828,987", "distb_stock_co": "5,827,808,935", "stlm_dt": "2025-12-31",
}
_PREF = {
    "rcept_no": "20260310002820", "corp_cls": "Y", "se": "우선주", "istc_totqy": "815,974,664",
    "tesstk_co": "0", "distb_stock_co": "815,974,664", "stlm_dt": "2025-12-31",
}
_TOTAL = {
    "rcept_no": "20260310002820", "corp_cls": "Y", "se": "합계", "istc_totqy": "6,735,612,586",
    "tesstk_co": "100,000,000", "distb_stock_co": "6,635,612,586", "stlm_dt": "2025-12-31",
}


_NOTE = {
    "rcept_no": "20260310002820", "corp_cls": "Y", "se": "비고", "istc_totqy": "-",
    "tesstk_co": "-", "distb_stock_co": "-", "stlm_dt": "2025-12-31",
}


def _snapshot(rows=(_COMMON, _PREF, _TOTAL, _NOTE)) -> dict:
    return O._treasury_snapshot({"list": [dict(r) for r in rows]}, {"list": []}, bsns_year="2025")


def _listed(shares=5_846_278_608, dd="20261021") -> dict:
    return {"shares": shares, "price_dd": dd}


# ── 분모의 출처 ────────────────────────────────────────────────────────────────

def test_snapshot_records_which_report_and_date_the_denominator_is() -> None:
    snap = _snapshot()
    assert snap["issued_shares"] == 5_919_637_922
    basis = snap["basis"]
    assert basis["rcept_no"] == "20260310002820"
    assert basis["stlm_dt"] == "2025-12-31"
    assert basis["bsns_year"] == "2025"
    assert basis["report"] == "사업보고서"
    assert basis["stock_class"] == "보통주"
    assert basis["common_only"] is True


def test_subtotal_fallback_minus_preferred_is_still_common_only() -> None:
    """보통주 행이 없고 합계 행에서 우선주를 뺀 회사 — 보통주 기준이라고 밝힌다."""
    snap = _snapshot([_PREF, _TOTAL, _NOTE])        # 「비고」 주석 행은 주식 종류가 아니다
    assert snap["issued_shares"] == 6_735_612_586 - 815_974_664
    assert snap["basis"]["stock_class"] == "합계-우선주"
    assert snap["basis"]["common_only"] is True


def test_subtotal_fallback_with_unknown_class_is_not_common_only() -> None:
    """우선주도 보통주도 아닌 종류 행이 있으면 합계에서 뺀 값이 보통주만이라고 장담할 수 없다."""
    other = {**_PREF, "se": "종류주식", "istc_totqy": "1,000"}
    snap = _snapshot([other, _TOTAL])
    assert snap["basis"]["common_only"] is False


# ── 최신 상장주식수와의 비교 ───────────────────────────────────────────────────

def test_share_basis_reports_both_values_and_the_difference() -> None:
    sb = O._share_basis(_snapshot(), _listed())
    assert sb["issued_shares"] == 5_919_637_922
    assert sb["listed"]["shares"] == 5_846_278_608
    assert sb["listed"]["price_dd"] == "20261021"
    assert sb["comparison"] == "differs"
    assert sb["diff_shares"] == 73_359_314
    assert sb["diff_pct"] == 1.25      # 상장주식수 대비


def test_share_basis_same_values_add_nothing_to_compare() -> None:
    sb = O._share_basis(_snapshot(), _listed(5_919_637_922))
    assert sb["comparison"] == "same"
    assert "diff_shares" not in sb


def test_share_basis_does_not_mix_classes() -> None:
    """분모가 보통주만이 아닐 수 있으면 보통주 상장주식수와 비교하지 않고 그 사실을 적는다."""
    other = {**_PREF, "se": "종류주식", "istc_totqy": "1,000"}
    sb = O._share_basis(_snapshot([other, _TOTAL]), _listed())
    assert sb["comparison"] == "mixed_class"
    assert "diff_shares" not in sb


def test_share_basis_without_listed_shares_says_so() -> None:
    sb = O._share_basis(_snapshot(), None)
    assert sb["comparison"] == "listed_unavailable"
    assert sb["issued_shares"] == 5_919_637_922


def test_share_basis_empty_when_no_denominator() -> None:
    assert O._share_basis(O._treasury_snapshot({"list": []}, {"list": []}, bsns_year="2025"), _listed()) is None


# ── md 표 ──────────────────────────────────────────────────────────────────────

def _payload(listed: dict | None) -> dict:
    snap = _snapshot()
    major = [{"name": "이재용", "relation": "본인", "shares": 97_414_196, "ownership_pct": 1.65}]
    return {
        "subject": "삼성전자", "status": "exact", "warnings": [],
        "data": {
            "canonical_name": "삼성전자", "company_id": "x", "year": "2025", "window": {},
            "summary": {"top_holder": major[0], "treasury_shares": snap["treasury_shares"]},
            "major_holders": major, "blocks": [], "treasury": snap,
            "share_basis": O._share_basis(snap, listed),
        },
    }


def test_md_states_the_denominator_and_its_basis() -> None:
    md = _render(_payload(_listed(5_919_637_922)), "summary")
    assert "분모" in md
    assert "5,919,637,922" in md
    assert "2025 사업보고서" in md and "20260310002820" in md and "2025-12-31" in md


def test_md_shows_both_counts_the_gap_and_a_listed_share_column() -> None:
    md = _render(_payload(_listed()), "summary")
    assert "5,919,637,922" in md and "5,846,278,608" in md and "73,359,314" in md
    assert "20261021" in md or "2026-10-21" in md
    head = next(ln for ln in md.splitlines() if ln.startswith("| 구분"))
    assert "최신 상장주식수 기준" in head
    # 명부 몫 97,414,196 ÷ 상장주식수 = 1.67%(보고서 분모 기준 1.65%)
    row = next(ln for ln in md.splitlines() if ln.startswith("| 최대주주+특수관계인"))
    assert "1.65%" in row and "1.67%" in row


def test_md_same_values_keep_the_three_column_table() -> None:
    md = _render(_payload(_listed(5_919_637_922)), "summary")
    head = next(ln for ln in md.splitlines() if ln.startswith("| 구분"))
    assert head == "| 구분 | 보유주식수 | 지분율 |"
    assert "최신 상장주식수 기준" not in md


def test_md_does_not_compare_when_denominator_may_hold_other_classes() -> None:
    other = {**_PREF, "se": "종류주식", "istc_totqy": "1,000"}
    p = _payload(_listed())
    p["data"]["treasury"] = _snapshot([other, _TOTAL])
    p["data"]["share_basis"] = O._share_basis(p["data"]["treasury"], _listed())
    md = _render(p, "summary")
    assert "보통주 외 주식" in md
    head = next(ln for ln in md.splitlines() if ln.startswith("| 구분"))
    assert head == "| 구분 | 보유주식수 | 지분율 |"


def test_md_without_share_basis_is_the_old_table() -> None:
    """share_basis 가 없는 payload(옛 모양)도 깨지지 않고 3칸 표 그대로."""
    p = _payload(_listed())
    del p["data"]["share_basis"]
    md = _render(p, "summary")
    head = next(ln for ln in md.splitlines() if ln.startswith("| 구분"))
    assert head == "| 구분 | 보유주식수 | 지분율 |"


# ── payload 배선 ───────────────────────────────────────────────────────────────

class _Client:
    def api_call_snapshot(self) -> int:
        return 0

    async def get_major_shareholders(self, corp_code, bsns_year, reprt_code="11011"):
        return {"list": [{"nm": "이재용", "relate": "본인", "stock_knd": "보통주",
                          "trmend_posesn_stock_co": "97,414,196", "trmend_posesn_stock_qota_rt": "1.65",
                          "stlm_dt": "2025-12-31"}]}

    async def get_stock_total(self, corp_code, bsns_year, reprt_code="11011"):
        return {"list": [dict(_COMMON), dict(_PREF), dict(_TOTAL)]}

    async def get_treasury_stock(self, corp_code, bsns_year, reprt_code="11011"):
        return {"list": []}

    async def get_block_holders(self, corp_code):
        return {"list": []}


def _build(monkeypatch, scope: str, listed, year: int | None = None) -> dict:
    async def resolve(query, *a, **k):
        return CompanyResolution(status=AnalysisStatus.EXACT, query=query, selected=dict(SAMSUNG), candidates=[])

    seen: list[str] = []

    async def fake_listed(stock_code):
        seen.append(stock_code)
        return listed

    monkeypatch.setattr(O, "get_dart_client", lambda: _Client())
    monkeypatch.setattr(O, "resolve_company_query", resolve)
    monkeypatch.setattr(O, "_latest_listed_shares", fake_listed)
    payload = asyncio.run(O.build_ownership_structure_payload("삼성전자", scope=scope, year=year))
    payload["_listed_calls"] = seen
    return payload


def test_summary_payload_carries_share_basis_with_listed_shares(monkeypatch) -> None:
    p = _build(monkeypatch, "summary", _listed())
    sb = p["data"]["share_basis"]
    assert sb["comparison"] == "differs" and sb["diff_shares"] == 73_359_314
    assert sb["rcept_no"] == "20260310002820" and sb["stlm_dt"] == "2025-12-31"
    assert p["_listed_calls"] == ["005930"]
    assert "5,846,278,608" in _render(p, "summary")


def test_other_scopes_do_not_read_listed_shares(monkeypatch) -> None:
    p = _build(monkeypatch, "major_holders", _listed())
    assert "share_basis" not in p["data"]
    assert p["_listed_calls"] == []


# ── 액면분할·병합(DATA-PX3)으로 주식 수 단위가 달라진 종목 ─────────────────────────

def _unit_payload(issued: int, major: int, treasury: int, listed_shares: int) -> dict:
    """보고서 분모 issued · 명부 major · 자사주 treasury · 최신 상장주식수 listed_shares 로 summary payload 를 만든다."""
    common = {**_COMMON, "istc_totqy": f"{issued:,}", "tesstk_co": f"{treasury:,}"}
    snap = O._treasury_snapshot({"list": [common]}, {"list": []}, bsns_year="2025")
    holders = [{"name": "최대주주", "relation": "본인", "shares": major, "ownership_pct": 0.0}]
    return {
        "subject": "시험", "status": "exact", "warnings": [],
        "data": {
            "canonical_name": "시험", "company_id": "x", "year": "2025", "window": {},
            "summary": {"top_holder": holders[0], "treasury_shares": treasury},
            "major_holders": holders, "blocks": [], "treasury": snap,
            "share_basis": O._share_basis(snap, _listed(listed_shares)),
        },
    }


def test_reverse_split_is_not_compared_against_post_merge_listed_shares() -> None:
    """5:1 병합(000040 꼴) — 보유주식수는 병합 전 수, 상장주식수는 병합 뒤 수라 나누면 152% 가 나온다."""
    p = _unit_payload(86_375_184, 30_000_000, 1_000_000, 19_675_036)
    assert p["data"]["share_basis"]["comparison"] == "unit_changed"
    md = _render(p, "summary")
    assert "152.48%" not in md
    assert "최신 상장주식수 기준" not in md
    head = next(ln for ln in md.splitlines() if ln.startswith("| 구분"))
    assert head == "| 구분 | 보유주식수 | 지분율 |"
    other = next(ln for ln in md.splitlines() if ln.startswith("| 기타"))
    assert other.rstrip().endswith("64.11% |")
    assert "86,375,184" in md and "19,675,036" in md          # 두 값과 차이는 그대로 적는다
    assert "분할·병합" in md and "계산하지 않았다" in md


def test_forward_split_is_not_compared_against_post_split_listed_shares() -> None:
    """10:1 분할(001130 꼴) — 상장주식수가 10배라 지분율 칸이 10분의 1로 줄어 나온다."""
    p = _unit_payload(1_690_000, 600_000, 20_000, 16_900_000)
    sb = p["data"]["share_basis"]
    assert sb["comparison"] == "unit_changed"
    assert sb["diff_pct"] == -90.0
    md = _render(p, "summary")
    assert "최신 상장주식수 기준" not in md
    assert "분할·병합" in md


def test_small_gap_keeps_the_listed_share_column() -> None:
    """소각·증자 정도(문턱 20% 이내)는 종전대로 상장주식수 기준 칸을 붙인다."""
    p = _unit_payload(1_000, 500, 100, 950)
    assert p["data"]["share_basis"]["comparison"] == "differs"
    md = _render(p, "summary")
    head = next(ln for ln in md.splitlines() if ln.startswith("| 구분"))
    assert "최신 상장주식수 기준" in head
    assert "분할·병합" not in md


def test_listed_column_is_dropped_when_holdings_exceed_listed_shares() -> None:
    """차이는 문턱 안이어도 명부+자사주가 상장주식수를 넘으면 「기타」 칸이 음수 — 칸을 붙이지 않고 그 사실을 적는다."""
    p = _unit_payload(1_000, 800, 150, 900)         # 차이 +11.11%, 800+150 > 900
    assert p["data"]["share_basis"]["comparison"] == "differs"
    md = _render(p, "summary")
    head = next(ln for ln in md.splitlines() if ln.startswith("| 구분"))
    assert head == "| 구분 | 보유주식수 | 지분율 |"
    assert "넘어" in md and "계산하지 않았다" in md


# ── 과거 사업연도 조회는 오늘의 상장주식수와 비교하지 않는다 ─────────────────────────

def test_past_year_query_does_not_read_or_compare_latest_listed_shares(monkeypatch) -> None:
    p = _build(monkeypatch, "summary", _listed(), year=2015)
    sb = p["data"]["share_basis"]
    assert sb["comparison"] == "past_year"
    assert sb["listed"] is None and "diff_shares" not in sb
    assert sb["rcept_no"] == "20260310002820"                 # 분모 출처 줄은 그대로
    assert p["_listed_calls"] == []
    md = _render(p, "summary")
    assert "최신 상장주식수 기준" not in md
    assert "과거" in md and "비교하지 않았다" in md
    assert "5,846,278,608" not in md


def test_default_year_still_compares(monkeypatch) -> None:
    p = _build(monkeypatch, "summary", _listed())              # year 를 주지 않으면 직전 사업연도(기본)
    assert p["data"]["share_basis"]["comparison"] == "differs"
    assert p["_listed_calls"] == ["005930"]


# ── _latest_listed_shares 의 조회문 ─────────────────────────────────────────────

def _run_listed(monkeypatch, rows, code="005930"):
    import datetime as dt
    import open_proxy_mcp.db as db

    calls: list[tuple] = []

    def fake_pg_rows(sql, params=()):
        calls.append((sql, params))
        return rows

    monkeypatch.setattr(db, "pg_rows", fake_pg_rows)
    monkeypatch.setattr(O, "today_kst", lambda: dt.date(2026, 10, 23))
    return asyncio.run(O._latest_listed_shares(code)), calls


def test_latest_listed_shares_query_has_21_day_floor_and_positive_filter(monkeypatch) -> None:
    got, calls = _run_listed(monkeypatch, [(5_846_278_608, "20261021")])
    assert got == {"shares": 5_846_278_608, "price_dd": "20261021"}
    sql, params = calls[0]
    assert "price_latest" in sql and "list_shrs>0" in sql and "price_dd>=" in sql
    assert params == ("005930", "20261002")                  # 2026-10-23 − 21일


def test_latest_listed_shares_none_when_db_unset_or_no_recent_row(monkeypatch) -> None:
    assert _run_listed(monkeypatch, None)[0] is None          # pg_rows None = DB 미설정·장애
    assert _run_listed(monkeypatch, [])[0] is None            # 최근 행 없음(상장폐지·장기 거래정지)
    assert _run_listed(monkeypatch, [(0, "20261021")])[0] is None


def test_latest_listed_shares_without_code_does_not_query(monkeypatch) -> None:
    got, calls = _run_listed(monkeypatch, [(1, "20261021")], code="")
    assert got is None and calls == []
