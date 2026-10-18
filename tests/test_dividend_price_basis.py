"""배당수익률의 주식 기준 — 10-19 리츠·인프라 배당수익률 분포 점검(p1b)에서 찾은 것.

① 결산기준일 뒤 액면분할·병합·무상증자(확정 라벨)면 사업보고서 DPS 는 옛 주식 기준이라 지금 주가로 나누면 계수만큼 어긋난다
   (운영 실측: INVENI 015360 1:5 분할 38.46% — 맞는 값 7.69%, 미원화학 134380 1:10 42.06% — 4.21%). `dividend.align_dps_to_price`.
② 그 밖의 큰 기준가 조정(라벨 없음 — 리츠 대규모 배당락, 코람코더원리츠 2026-08-28 10,930원 → 2,290원)은 DPS 를 바꾸지 않고 알린다.
③ 반기·분기 결산 DPS 편차(자산 처분 이익 배당 — NH프라임리츠 126 → 625 → 837원, 39.57%)·가장 늦은 결산기 무배당은 값을 두고 알린다.
④ 재무·주식 수·배당 사항이 모두 빈 회사(펀드 공시로 내는 인프라 투융자회사 — 맥쿼리인프라)는 빈 칸의 이유를 적는다.
"""
from __future__ import annotations

import asyncio
import calendar
from datetime import date

import pytest

from open_proxy_mcp.services import dividend as D
from open_proxy_mcp.services import price_multiple_data as S
from open_proxy_mcp.tools import dividend_disclosure as TD
from open_proxy_mcp.tools import price_multiple_data as TP

from tests.test_pmd_found_defects import _run_live

EV_SQL = "SELECT event_dd, adj_factor, event_type, label_confidence, prev_close, base_price FROM krx_adj_events"


def _items(*periods) -> list[dict]:
    """alotMatter 행(파싱 뒤) — periods = [(결산기준일, 보통주 DPS 문자열, 현금배당금총액(백만원) 문자열, 접수번호)]."""
    out = []
    for stlm, dps, total, rcept in periods:
        base = {"previous": "", "before_previous": "", "stlm_dt": stlm, "rcept_no": rcept, "is_special": False, "is_stock_dividend": False}
        out.append({**base, "category": "현금배당금총액(백만원)", "stock_type": "-", "current": total})
        out.append({**base, "category": "주당 현금배당금(원)", "stock_type": "보통주", "current": dps})
    return out


def _ev(dd, f, et="split", lab="confirmed", prev=None, base=None):
    return (dd, f, et, lab, prev, base)


# ═══ ① 분할·병합 뒤 DPS 주식 기준 ═════════════════════════════════════════════════════════════════════

def test_split_after_the_report_was_filed_scales_the_dps():
    """INVENI(015360) — FY2025 DPS 5,400원(사업보고서 2026-03 접수), 2026-04-09 1:5 분할 → 지금 주식 기준 1,080원."""
    meta = D.period_cash_meta(_items(("2025-12-31", "5,400", "23,971", "20260310000123")))
    a = D.align_dps_to_price([("20251231", 5400)], meta, [_ev("20260409", 0.2)], None)
    assert a["periods"] == [("20251231", 1080)]
    assert a["adjusted"] == [{"stlm_dt": "2025-12-31", "dps_reported_krw": 5400, "factor": 0.2, "dps_krw": 1080,
                              "basis": "filed_before_event", "events": [{"event_dd": "2026-04-09", "event_type": "split", "adj_factor": 0.2}]}]
    assert a["undetermined"] == [] and a["price_events"] == []


def test_merge_scales_the_dps_up_and_odd_ratios_keep_two_decimals():
    a = D.align_dps_to_price([("20251231", 100)], {"20251231": {"rcept_dt": "20260310"}}, [_ev("20260427", 5.0, "merge")], None)
    assert a["periods"] == [("20251231", 500)]
    b = D.align_dps_to_price([("20251231", 1000)], {"20251231": {"rcept_dt": "20260310"}}, [_ev("20260427", 1 / 3, "bonus")], None)
    assert b["periods"] == [("20251231", 333.33)]


def test_event_between_year_end_and_filing_uses_the_dividend_share_count():
    """폰드그룹(472850) — 2026-01-02 무상증자(계수 0.5626)가 결산기준일과 접수일 사이. 배당총액 9,054백만 ÷ 280원 = 3,234만 주 =
    사업연도 말 주식 수 → 옛 주식 기준 → 곱한다. 거꾸로 배당받은 주식 수가 이벤트 뒤 주식 수면(MH머트리얼즈처럼 기준일이 분할 뒤) 그대로."""
    meta = D.period_cash_meta(_items(("2025-12-31", "280", "9,054", "20260320000456")))
    pre = D.align_dps_to_price([("20251231", 280)], meta, [_ev("20260102", 0.5626, "bonus")], 32_343_933)
    assert pre["periods"] == [("20251231", 157.53)] and pre["adjusted"][0]["basis"] == "dividend_shares_pre_event"
    meta2 = D.period_cash_meta(_items(("2025-12-31", "150", "5,267", "20260320000456")))       # 5,267백만 ÷ 150원 = 3,511만 주(분할 뒤)
    post = D.align_dps_to_price([("20251231", 150)], meta2, [_ev("20260102", 0.1)], 4_150_000)
    assert post["periods"] == [("20251231", 150)] and post["adjusted"] == [] and post["undetermined"] == []


def test_event_between_year_end_and_filing_without_share_counts_is_left_and_said():
    meta = D.period_cash_meta(_items(("2025-12-31", "280", "9,054", "20260320000456")))
    a = D.align_dps_to_price([("20251231", 280)], meta, [_ev("20260102", 0.5626, "bonus")], None)
    assert a["periods"] == [("20251231", 280)] and a["adjusted"] == []
    assert a["undetermined"] == [{"stlm_dt": "2025-12-31", "dps_krw": 280,
                                  "events": [{"event_dd": "2026-01-02", "event_type": "bonus", "adj_factor": 0.5626}]}]


def test_events_before_the_settlement_date_or_unconfirmed_do_not_scale_the_dps():
    meta = {"20251231": {"rcept_dt": "20260310"}}
    evs = [_ev("20250610", 0.2),                                   # 결산기준일 앞 — 사업보고서가 이미 새 주식 기준
           _ev("20260223", 0.2, "split", "unlabeled", 5000, 1000),  # 라벨 전 — 바꾸지 않고 알린다
           _ev("20260512", 0.983, None, "unlabeled", 2990, 2940)]   # 작은 조정 — 아무것도 안 한다
    a = D.align_dps_to_price([("20251231", 300)], meta, evs, None)
    assert a["periods"] == [("20251231", 300)] and a["adjusted"] == []
    assert a["price_events"] == [{"event_dd": "2026-02-23", "event_type": "split", "adj_factor": 0.2, "prev_close": 5000, "base_price": 1000}]


def test_no_events_changes_nothing():
    a = D.align_dps_to_price([("20251231", 1668)], {}, [], None)
    assert a == {"periods": [("20251231", 1668)], "adjusted": [], "undetermined": [], "price_events": []}


def test_december_company_split_flows_to_the_yield_and_the_explain(monkeypatch):
    def rows(sql, params):
        if sql.startswith(EV_SQL):
            assert params == ("330590", "20251231")
            return [_ev("20260409", 0.2)]
        return None
    p, _ = _run_live(monkeypatch, alot={2025: _items(("2025-12-31", "5,400", "23,971", "20260310000123"))}, induty="26110",
                     price=14040, db_rows=rows)
    d = p["data"]; i = d["inputs"]
    assert i["dps_krw"] == 1080 and d["multiples"]["dividend_yield_pct"] == round(1080 / 14040 * 100, 2)   # 종전 5,400 → 38.46%
    assert i["dps_share_adjustment"]["periods"][0]["dps_reported_krw"] == 5400 and "dps_basis" not in i
    assert any(w.startswith("결산기준일 2025-12-31 뒤 2026-04-09 액면분할(계수 0.2)") and "1,080원" in w for w in d["warnings"])
    md = TP._render_explain_firm(p)
    assert "1,080원 (사업보고서 5,400원 × 계수 0.2 — 2026-04-09 주식 분할·병합 뒤 지금 주식 기준)" in md


def test_no_event_rows_leave_the_response_as_before(monkeypatch):
    """대부분의 회사 — 이벤트가 없으면 DPS·경고·칸 모두 종전 그대로(DB 읽기만 한 번 는다)."""
    seen_ev = []

    def rows(sql, params):
        if sql.startswith(EV_SQL):
            seen_ev.append(params)
            return []
        return None
    p, _ = _run_live(monkeypatch, alot={2025: _items(("2025-12-31", "1,668", "5,000", "20260310000123"))}, induty="26110", db_rows=rows)
    i = p["data"]["inputs"]
    assert seen_ev == [("330590", "20251231")]
    assert i["dps_krw"] == 1668 and not {"dps_share_adjustment", "price_basis_events", "dps_basis"} & set(i)
    assert not any("계수" in w or "결산기 DPS" in w for w in p["data"]["warnings"])


# ═══ ② 큰 기준가 조정(라벨 없음) ═══════════════════════════════════════════════════════════════════════

def test_reit_large_dividend_base_price_reset_is_said_without_changing_the_dps(monkeypatch):
    """코람코더원리츠(417310) — 분기 결산 72·94·96·114원 뒤 2026-08-28 기준가 조정(10,930원 → 2,290원, 8,974원 특별 배당락).
    배당수익률은 종전 값(376 ÷ 지금 주가)을 두고, 조정 뒤 주가로 그 앞 배당을 나눴다고 밝힌다."""
    def rows(sql, params):
        if sql.startswith(EV_SQL):
            assert params == ("330590", "20250531")      # 창 바로 앞 결산기 뒤부터
            return [_ev("20260828", 0.209515, None, "unlabeled", 10930, 2290)]
        return None
    p, _ = _run_live(monkeypatch, alot={2025: _items(("2025-05-31", "80", "3,232", "20250813000001"), ("2025-08-31", "72", "2,909", "20251113000001"),
                                                     ("2025-11-30", "94", "3,798", "20260213000001")),
                                        2026: _items(("2026-02-28", "96", "3,878", "20260521000001"), ("2026-05-31", "114", "4,642", "20260813000001"))},
                     price=2385, db_rows=rows)
    d = p["data"]; i = d["inputs"]
    assert i["dps_krw"] == 376 and d["multiples"]["dividend_yield_pct"] == round(376 / 2385 * 100, 2)
    assert i["price_basis_events"] == [{"event_dd": "2026-08-28", "event_type": None, "adj_factor": 0.209515, "prev_close": 10930, "base_price": 2290}]
    assert any(w.startswith("⚠️ 2026-08-28 기준가 조정(전일 종가 10,930원 → 기준가 2,290원, 계수 0.21, 종류 미확인)") for w in d["warnings"])
    assert "dps_share_adjustment" not in i and "uneven" not in i["dps_basis"]


# ═══ ③ 반기·분기 결산 DPS 편차 ═════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("chain,expect", [
    ([("20250531", 126), ("20251130", 625), ("20260531", 837)], {"max": {"stlm_dt": "2026-05-31", "dps_krw": 837},
                                                                 "min": {"stlm_dt": "2025-05-31", "dps_krw": 126}, "ratio": 6.6, "latest_zero": False}),
    ([("20250630", 117), ("20251231", 123), ("20260630", 139)], None),       # 롯데리츠 — 고르다
    ([("20250331", 96), ("20250930", 96), ("20260331", 46)], None),          # 마스턴프리미어리츠 — 줄었지만 3배 안
    ([("20250630", 120), ("20251231", 115), ("20260630", 0)], {"max": {"stlm_dt": "2025-06-30", "dps_krw": 120},
                                                               "min": {"stlm_dt": "2025-12-31", "dps_krw": 115}, "ratio": None, "latest_zero": True}),
    ([("20250630", 0), ("20251231", 0), ("20260630", 0)], None),            # 줄곧 무배당 — 편차가 아니다
])
def test_dps_unevenness(chain, expect):
    assert D.dps_unevenness(chain) == expect


def test_reit_asset_sale_dividends_keep_the_sum_and_say_it_is_uneven(monkeypatch):
    """NH프라임리츠(338100) — 2025-11 625원·2026-05 837원(이사회 결의·DART 원문 그대로, 자산 처분 이익). 12개월 합 1,462원은 두고 편차를 밝힌다."""
    p, _ = _run_live(monkeypatch, alot={2025: _items(("2025-05-31", "126", "2,351", "20250813001421"), ("2025-11-30", "625", "11,663", "20260211001059")),
                                        2026: _items(("2026-05-31", "837", "15,618", "20260812000759"))}, price=3695)
    d = p["data"]; i = d["inputs"]
    assert i["dps_krw"] == 1462 and d["multiples"]["dividend_yield_pct"] == 39.57
    assert i["dps_basis"]["uneven"]["ratio"] == 6.6
    w = [x for x in d["warnings"] if x.startswith("⚠️ 결산기 DPS 차이가 크다")]
    assert len(w) == 1 and "2026-05 837원가 2025-05 126원의 6.6배" in w[0] and "12개월 합(1,462원)" in w[0]


def test_reit_latest_period_without_dividend_is_said(monkeypatch):
    """제이알글로벌리츠(348950) — 2026-06 결산기 무배당. 12개월 합 115원(9.73%)은 그 앞 결산기 배당이다."""
    p, _ = _run_live(monkeypatch, alot={2025: _items(("2025-06-30", "120", "23,685", "20250901000001"), ("2025-12-31", "115", "22,698", "20260301000001")),
                                        2026: _items(("2026-06-30", "-", "-", "20260901000001"))}, price=1182)
    d = p["data"]
    assert d["inputs"]["dps_krw"] == 115 and d["inputs"]["dps_basis"]["uneven"]["latest_zero"] is True
    assert any(x.startswith("⚠️ 가장 늦은 결산기(2026-06) 무배당") for x in d["warnings"])


# ═══ ④ 재무·주식 수·배당 사항이 모두 빈 회사 ════════════════════════════════════════════════════════════

def test_fund_style_reporter_gets_a_reason_for_the_empty_multiples(monkeypatch):
    """맥쿼리인프라(088980) — DART 공시 목록에 사업보고서가 없다(펀드 공시). 종전엔 이유 없이 빈 칸만 나갔다."""
    p, _ = _run_live(monkeypatch, reports={}, alot=None, induty="64201", price=9670)

    async def no_shares(client, cc, year):
        return {"total": None, "common": None}
    monkeypatch.setattr(S, "_shares_outstanding", no_shares)
    p = asyncio.run(S.build_valuation_payload("330590", format="json"))
    d = p["data"]
    assert d["multiples"]["per_fy0"] is None and d["multiples"]["dividend_yield_pct"] is None
    assert any(w.startswith("DART 정기보고서의 재무제표·주식 총수·배당 사항이 모두 비어 있다") for w in d["warnings"])


def test_ordinary_company_does_not_get_the_fund_note(monkeypatch):
    p, _ = _run_live(monkeypatch, alot={2025: _items(("2025-12-31", "1,668", "5,000", "20260310000123"))}, induty="26110")
    assert not any("펀드 공시" in w for w in p["data"]["warnings"])


# ═══ dividend_disclosure — 현재가 기준 배당수익률도 같은 규칙 ═══════════════════════════════════════════

def _divd_payload(monkeypatch, events, price=14040):
    from open_proxy_mcp.services.company import CompanyResolution
    from open_proxy_mcp.services.contracts import AnalysisStatus

    class _Client:
        def api_call_snapshot(self):
            return 0

    async def resolve(_q):
        return CompanyResolution(status=AnalysisStatus.EXACT, query="INVENI", candidates=[],
                                 selected={"corp_name": "INVENI", "stock_code": "015360", "corp_code": "00105101"})

    async def annual(_corp, y):
        if y != 2025:
            return {}, None
        s = D.build_dividend_summary(_items(("2025-12-31", "5,400", "23,971", "20260310000123")), "사업보고서(기말)")
        s["source"] = "alotMatter"
        return s, None

    async def search(corp, start, end):
        return [], [], [], None

    async def details(_f):
        return []

    async def capital(*_a, **_k):
        return False, []

    async def market_for(code):
        return {"price": price, "date": "20261016"}

    async def db_rows(sql, params=()):
        return events if " ".join(sql.split()).startswith(EV_SQL) else []

    monkeypatch.setattr(D, "today_kst", lambda: date(2026, 10, 19))
    monkeypatch.setattr(D, "get_dart_client", lambda: _Client())
    monkeypatch.setattr(D, "resolve_company_query", resolve)
    monkeypatch.setattr(D, "_annual_summary", annual)
    monkeypatch.setattr(D, "_search_dividend_filings", search)
    monkeypatch.setattr(D, "_decision_details", details)
    monkeypatch.setattr(D, "_detect_capital_reserve_reduction", capital)
    monkeypatch.setattr(S, "_market_for", market_for)
    monkeypatch.setattr(S, "_db_rows", db_rows)
    return asyncio.run(D.build_dividend_payload("INVENI", scope="summary", year=2025))


def test_dividend_disclosure_current_yield_uses_the_current_share_basis(monkeypatch):
    p = _divd_payload(monkeypatch, [_ev("20260409", 0.2)])
    s = p["data"]["summary"]
    assert s["cash_dps"] == 5400 and s["yield_current_dps_krw"] == 1080 and s["yield_current_pct"] == round(1080 / 14040 * 100, 2)
    md = TD._render(p, "summary")
    assert "현재가 기준 배당수익률: 7.69% (DPS 5,400원 × 계수 0.2(2026-04-09 주식 분할·병합 뒤) = 1,080원 ÷ 종가 14,040원" in md


def test_dividend_disclosure_without_events_is_unchanged(monkeypatch):
    p = _divd_payload(monkeypatch, [])
    s = p["data"]["summary"]
    assert s["yield_current_pct"] == round(5400 / 14040 * 100, 2) and "yield_current_dps_krw" not in s
