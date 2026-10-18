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
    assert a["periods"] == [("20251231", 300)] and a["adjusted"] == [] and a["undetermined"] == []
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
    assert any("상장주식수 확인 못 함" in w for w in d["warnings"] if w.startswith("⚠️ 2026-08-28"))


def test_reit_dividend_reset_with_unchanged_share_count_says_so(monkeypatch):
    """같은 코람코더원리츠 — 상장주식수를 읽었고 그대로면(배당락) 주식 수 조정이 아니라는 근거를 칸과 안내에 적는다."""
    def rows(sql, params):
        if sql.startswith(EV_SQL):
            return [_ev("20260828", 0.209515, None, "unlabeled", 10930, 2290)]
        if sql.startswith(SHARES_SQL):
            return [(40_400_000, [40_400_000] * 7, 40_400_000)]
        return None
    p, _ = _run_live(monkeypatch, alot={2025: _items(("2025-05-31", "80", "3,232", "20250813000001"), ("2025-08-31", "72", "2,909", "20251113000001"),
                                                     ("2025-11-30", "94", "3,798", "20260213000001")),
                                        2026: _items(("2026-02-28", "96", "3,878", "20260521000001"), ("2026-05-31", "114", "4,642", "20260813000001"))},
                     price=2385, db_rows=rows)
    d = p["data"]
    assert d["inputs"]["dps_krw"] == 376 and d["inputs"]["price_basis_events"][0]["shares_ratio"] == 1.0
    assert any(w.startswith("⚠️ 2026-08-28 기준가 조정") and "상장주식수 × 1 — 주식 수 조정이 아니다" in w for w in d["warnings"])


# ═══ ③ 반기·분기 결산 DPS 편차 ═════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("chain,expect", [
    ([("20250531", 126), ("20251130", 625), ("20260531", 837)], {"max": {"stlm_dt": "2026-05-31", "dps_krw": 837},
                                                                 "min": {"stlm_dt": "2025-05-31", "dps_krw": 126}, "ratio": 6.6, "latest_zero": False}),
    ([("20250630", 117), ("20251231", 123), ("20260630", 139)], None),       # 롯데리츠 — 고르다
    ([("20250331", 96), ("20250930", 96), ("20260331", 46)], None),          # 마스턴프리미어리츠 — 줄었지만 3배 안
    ([("20250630", 120), ("20251231", 115), ("20260630", 0)], {"max": {"stlm_dt": "2025-12-31", "dps_krw": 115},
                                                               "min": {"stlm_dt": "2025-12-31", "dps_krw": 115}, "ratio": None, "latest_zero": True}),
    ([("20250630", 0), ("20251231", 0), ("20260630", 0)], None),            # 줄곧 무배당 — 편차가 아니다
    # 261019 p1c: 큰 배당이 창을 벗어나 앞 결산기 자리 — 12개월 합은 이미 평소 수준(이지스밸류플러스리츠 612 → 173·172원)
    ([("20240831", 612), ("20250228", 173), ("20250831", 172)], None),
    ([("20251130", 625), ("20260531", 837), ("20261130", 130)], {"max": {"stlm_dt": "2026-05-31", "dps_krw": 837},     # 아직 창 안
                                                                 "min": {"stlm_dt": "2026-11-30", "dps_krw": 130}, "ratio": 6.4, "latest_zero": False}),
    ([("20260531", 837), ("20261130", 130), ("20270531", 128)], None),     # 반년 더 — 창을 벗어났다
    ([("20250630", 120), ("20251231", 120), ("20260630", 30)], {"max": {"stlm_dt": "2025-12-31", "dps_krw": 120},     # 창 안 배당 축소
                                                                "min": {"stlm_dt": "2026-06-30", "dps_krw": 30}, "ratio": 4.0, "latest_zero": False}),
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
    # 무배당 회사(재무·주식 수는 있다)도 아니다 — 배당 사항만 빈 것은 흔하다
    p2, _ = _run_live(monkeypatch, alot=None, induty="26110")
    assert p2["data"]["multiples"]["per_fy0"] is not None and not any("펀드 공시" in w for w in p2["data"]["warnings"])


# ═══ dividend_disclosure — 현재가 기준 배당수익률도 같은 규칙 ═══════════════════════════════════════════

def _divd_payload(monkeypatch, events, price=14040, items=None, shares=None):
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
        s = D.build_dividend_summary(items or _items(("2025-12-31", "5,400", "23,971", "20260310000123")), "사업보고서(기말)")
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
    seen_sh = []

    async def shares_out(client, cc, year):
        seen_sh.append((cc, year))
        return {"total": shares, "common": shares}
    monkeypatch.setattr(S, "_shares_outstanding", shares_out)
    p = asyncio.run(D.build_dividend_payload("INVENI", scope="summary", year=2025))
    p["_seen_shares"] = seen_sh
    return p


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


def test_dividend_disclosure_reads_share_count_only_for_an_event_before_filing(monkeypatch):
    """폰드그룹꼴 — 결산기준일과 접수일 사이 무상증자. 이때만 주식 총수를 읽어 배당받은 주식 수와 견준다(옛 기준 → 곱한다)."""
    items = _items(("2025-12-31", "280", "9,054", "20260320000456"))
    p = _divd_payload(monkeypatch, [_ev("20260102", 0.5626, "bonus")], price=4550, items=items, shares=32_343_933)
    s = p["data"]["summary"]
    assert p["_seen_shares"] == [("00105101", 2025)]
    assert s["yield_current_dps_krw"] == 157.53 and s["yield_current_pct"] == round(157.53 / 4550 * 100, 2)
    # 주식 총수를 못 받으면 그대로 두고 경고
    p2 = _divd_payload(monkeypatch, [_ev("20260102", 0.5626, "bonus")], price=4550, items=items, shares=None)
    assert p2["data"]["summary"]["yield_current_pct"] == round(280 / 4550 * 100, 2)
    assert any(w.startswith("결산기준일 2025-12-31 뒤 주식 분할·병합(2026-01-02 계수 0.5626)") for w in p2["warnings"])
    # 접수일 뒤 이벤트만 있으면 주식 총수를 읽지 않는다
    p3 = _divd_payload(monkeypatch, [_ev("20260409", 0.2)])
    assert p3["_seen_shares"] == []


# ═══ 라벨 전 분할·병합 — 상장주식 수 배율로 확인 ═════════════════════════════════════════════════════════

def test_unlabeled_merge_with_matching_share_count_is_treated_as_a_merge():
    """영화금속(012280) 2026-08-28 5:1 병합(라벨 전, 계수 5.0) — 상장주식 4,952만 → 990만(배율 0.2). 계수 × 배율 = 1 이라 병합으로 보고 곱한다.
    같은 라벨 없는 조정이라도 주식 수가 그대로면(코람코더원리츠 배당락 0.21 × 1.0) 바꾸지 않고 알린다."""
    meta = {"20251231": {"rcept_dt": "20260310"}}
    a = D.align_dps_to_price([("20251231", 30)], meta, [_ev("20260828", 5.0, "merge", "unlabeled", 803, 4015)], None,
                             share_ratios={"20260828": 9_904_901 / 49_524_505})
    assert a["periods"] == [("20251231", 150)] and a["price_events"] == []
    assert a["adjusted"][0]["events"] == [{"event_dd": "2026-08-28", "event_type": "merge", "adj_factor": 5.0, "basis": "listed_shares_ratio",
                                           "shares_ratio": 0.2}]
    b = D.align_dps_to_price([("20251231", 30)], meta, [_ev("20260828", 0.209515, None, "unlabeled", 10930, 2290)], None,
                             share_ratios={"20260828": 1.0})
    assert b["periods"] == [("20251231", 30)] and b["adjusted"] == [] and len(b["price_events"]) == 1


SHARES_SQL = "SELECT (SELECT list_shrs FROM krx_weekly"


def test_unlabeled_merge_reads_listed_shares_in_the_firm_path(monkeypatch):
    """대교(019680) 2026-09-10 2:1 병합(unlabeled) — 이벤트 앞 마지막 주 8,047만 주, 그 뒤 4,023만 주. 읽는 기간은 이벤트 날부터 63일."""
    seen = []

    def rows(sql, params):
        if sql.startswith(EV_SQL):
            return [_ev("20260910", 2.0, "merge", "unlabeled", 1000, 2000)]
        if sql.startswith(SHARES_SQL):
            seen.append(params)
            return [(80_467_700, [40_233_850, 40_233_850, 40_233_850, 40_233_850, 40_233_850], 40_233_850)]
        return None
    p, _ = _run_live(monkeypatch, alot={2025: _items(("2025-12-31", "50", "4,000", "20260310000123"))}, induty="85503",
                     price=1874, db_rows=rows)
    i = p["data"]["inputs"]
    assert seen == [("330590", "20260910", "330590", "20260910", "20261112", "330590", "20261112")]
    assert i["dps_krw"] == 100 and p["data"]["multiples"]["dividend_yield_pct"] == round(100 / 1874 * 100, 2)
    assert "price_basis_events" not in i


# ═══ 무상증자 신주 상장 지연 — 권리락 뒤 3~5주 (261019 p1c) ═════════════════════════════════════════════

@pytest.mark.parametrize("f,pre,posts,expect", [
    (1 / 3, 9_153_900, [9_153_900, 9_153_900, 9_153_900, 9_153_900, 27_461_700, 27_461_700], 3.0),   # 엠앤씨솔루션 — 5주 뒤 상장
    (0.555685, 16_543_115, [16_543_115, 16_543_115, 16_543_115, 29_777_607], 29_777_607 / 16_543_115),  # 가온전선
    (5.0, 49_524_505, [9_904_901, 9_904_901], 9_904_901 / 49_524_505),                                # 영화금속 병합 — 그날
    (0.680387, 18_617_382, [18_617_382, 18_617_382], 1.0),                                             # SK디앤디 — 주식 수 그대로
    (0.5, 1_000, [], None), (0.5, None, [2_000], None), (0.5, 0, [2_000], None),
])
def test_share_ratio_for_picks_the_count_closest_to_the_inverse_factor(f, pre, posts, expect):
    r = D.share_ratio_for(f, pre, posts)
    assert r == (pytest.approx(expect) if expect is not None else None)


def test_dart_labelled_bonus_issue_listed_weeks_later_scales_the_dps():
    """엠앤씨솔루션(484870) 2026-06-26 무상증자 1:2(라벨 「dart」 — 확정 아님, 계수 1/3). 신주는 07-29 에 상장 — 이벤트 뒤 첫 주만 보면
    주식 수 그대로라 「대규모 배당락」 안내만 하고 DPS 를 그대로 둬 배당수익률이 세 배(10-19 운영 14.49%)였다."""
    meta = {"20251231": {"rcept_dt": "20260317"}}
    ev = [_ev("20260626", 1 / 3, "bonus", "dart", 75600, 25200)]
    first_week_only = D.align_dps_to_price([("20251231", 2490)], meta, ev, None, share_ratios={"20260626": 1.0})
    assert first_week_only["adjusted"] == [] and len(first_week_only["price_events"]) == 1
    r = D.share_ratio_for(1 / 3, 9_153_900, [9_153_900] * 4 + [27_461_700] * 5)
    a = D.align_dps_to_price([("20251231", 2490)], meta, ev, None, share_ratios={"20260626": r})
    assert a["periods"] == [("20251231", 830)] and a["price_events"] == []
    assert a["adjusted"][0]["events"] == [{"event_dd": "2026-06-26", "event_type": "bonus", "adj_factor": 0.333333,
                                           "basis": "listed_shares_ratio", "shares_ratio": 3.0}]


def test_small_unlabeled_bonus_is_matched_but_rights_issue_and_tiny_resets_are_not():
    """주식 수 배율 확인은 큰 조정에만 하지 않는다 — 5% 무상증자(계수 0.953, 주식 수 × 1.047)도 맞춘다. 유상증자(계수 0.96, 주식 × 1.2)·
    작은 기준가 조정(계수 0.983, 주식 수 그대로)은 상쇄하지 않아 그대로이고, 범위 안이라 안내도 없다."""
    meta = {"20251231": {"rcept_dt": "20260310"}}
    evs = [_ev("20260604", 0.953167, None, "unlabeled", 187900, 179100), _ev("20260701", 0.96, None, "unlabeled"),
           _ev("20260801", 0.983, None, "unlabeled")]
    a = D.align_dps_to_price([("20251231", 750)], meta, evs, None,
                             share_ratios={"20260604": 232_553_706 / 222_122_341, "20260701": 1.2, "20260801": 1.0})
    assert a["periods"] == [("20251231", 714.88)] and a["price_events"] == [] and a["undetermined"] == []
    assert [e["event_dd"] for e in a["adjusted"][0]["events"]] == ["2026-06-04"]


def test_excluded_labels_are_not_corporate_actions():
    """시장 이전·호가단위 맞춤(「excluded_…」 라벨)은 소비할 때 뺀다 — 크더라도 안내하지 않고 주식 수도 읽지 않는다."""
    meta = {"20251231": {"rcept_dt": "20260310"}}
    evs = [_ev("20260301", 0.7, "market_transfer", "excluded_market_transfer"), _ev("20260302", 1.0014, "tick_rounding", "excluded_tick_rounding")]
    a = D.align_dps_to_price([("20251231", 300)], meta, evs, None, share_ratios={"20260301": 1 / 0.7})
    assert a == {"periods": [("20251231", 300)], "adjusted": [], "undetermined": [], "price_events": []}


def test_bonus_listed_in_the_open_week_is_read_from_the_daily_store(monkeypatch):
    """krx_weekly 는 닫힌 주만 — 이번 주에 상장한 신주는 일별 저장분(`price_latest`)에만 있다. 그 값도 후보로 본다."""
    def rows(sql, params):
        if sql.startswith(EV_SQL):
            return [_ev("20260924", 0.5, None, "unlabeled", 4000, 2000)]
        if sql.startswith(SHARES_SQL):
            assert params[-1] == "20261126"
            return [(10_000_000, [10_000_000, 10_000_000, 10_000_000], 20_000_000)]
        return None
    p, _ = _run_live(monkeypatch, alot={2025: _items(("2025-12-31", "100", "1,000", "20260310000123"))}, induty="26110",
                     price=2000, db_rows=rows)
    i = p["data"]["inputs"]
    assert i["dps_krw"] == 50 and "price_basis_events" not in i
    assert i["dps_share_adjustment"]["periods"][0]["events"][0]["basis"] == "listed_shares_ratio"
    # 종류가 빈 조정(무상증자 — 라벨 원천이 멎었다)은 「None」이 아니라 근거와 함께 적는다
    assert any(w.startswith("결산기준일 2025-12-31 뒤 2026-09-24 종류 미확인 조정(계수 0.5, 상장주식수 × 2 로 확인)") for w in p["data"]["warnings"])


def test_firm_path_does_not_read_shares_for_confirmed_or_excluded_events(monkeypatch):
    seen = []

    def rows(sql, params):
        if sql.startswith(EV_SQL):
            return [_ev("20260409", 0.2), _ev("20260501", 0.7, "market_transfer", "excluded_market_transfer")]
        if sql.startswith(SHARES_SQL):
            seen.append(params)
        return None
    p, _ = _run_live(monkeypatch, alot={2025: _items(("2025-12-31", "5,400", "23,971", "20260310000123"))}, induty="26110",
                     price=14040, db_rows=rows)
    assert seen == [] and p["data"]["inputs"]["dps_krw"] == 1080 and "price_basis_events" not in p["data"]["inputs"]


def test_dividend_disclosure_reads_share_count_for_the_summary_year(monkeypatch):
    """3월 결산 — 배당 사항을 읽은 사업연도(2025)의 주식 총수를 읽는다(결산기준일의 해 2026 이 아니다)."""
    items = _items(("2026-03-31", "280", "9,054", "20260620000456"))
    p = _divd_payload(monkeypatch, [_ev("20260410", 0.5626, "bonus")], price=4550, items=items, shares=32_343_933)
    assert p["_seen_shares"] == [("00105101", 2025)]
    assert p["data"]["summary"]["yield_current_dps_krw"] == 157.53


def test_alignment_failure_keeps_the_multiples_and_the_old_dps(monkeypatch):
    """배당 fail-open — 맞춤 계산이 깨져도 PER·PBR 응답과 종전 DPS 는 나간다."""
    def rows(sql, params):
        return [_ev("20260409", 0.2)] if sql.startswith(EV_SQL) else None

    def boom(*a, **k):
        raise ZeroDivisionError("x")
    p, _ = _run_live(monkeypatch, alot={2025: _items(("2025-12-31", "5,400", "23,971", "20260310000123"))}, induty="26110",
                     price=14040, db_rows=rows)
    monkeypatch.setattr(S, "align_dps_to_price", boom)
    p = asyncio.run(S.build_valuation_payload("330590", format="json"))
    assert p["status"] == "ok" and p["data"]["multiples"]["per_fy0"] is not None and p["data"]["inputs"]["dps_krw"] == 5400
    monkeypatch.setattr(D, "align_dps_to_price", boom)
    q = _divd_payload(monkeypatch, [_ev("20260409", 0.2)])
    assert q["data"]["summary"]["yield_current_pct"] == round(5400 / 14040 * 100, 2)
