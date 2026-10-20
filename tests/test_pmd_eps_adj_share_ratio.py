# -*- coding: utf-8 -*-
"""공시 EPS 정렬·기말 보유수량 맞춤도 **확정 라벨이 없는 큰 조정**을 상장주식 수 배율로 잡는다(261021, fin audit H4 — 함정 「라벨 없는 분할·병합」).

`_eps_adj_factor`(공시 EPS 를 오늘 주식 기준으로)와 `asset_holdings._db_share_adjust`(기말 보유수량을 오늘 주식 기준으로)는 확정 라벨의 분할·병합·무상증자·
주식배당(`ADJ_EVENT_SQL`)만 계수를 곱했다. 라벨 원천(v2·DART 공시 장부)이 2026-06·07 에 멎어 그 뒤 분할·병합·무상증자는 모두 「unlabeled」(무상증자는 종류도
비어 있다)이거나 「dart」 라벨이다 — 운영 읽기 전용 실측(2025-01 이후): 확정 라벨이 아닌 조정 463건 가운데 상장주식 수가 계수의 역수만큼 바뀐 것 164건·158종목
(병합 106 · 분할 9 · 무상증자 6 · 감자 31 · 종류 없음 12). 배당수익률(`dividend.align_dps_to_price`)은 이미 같은 판정으로 맞춘다 — 같은 판정을 쓴다:
이벤트 앞 마지막 주 ↔ 이벤트 뒤 63일 안 상장주식 수 배율 r, f × r = 1 ± 2%, |r − 1| ≥ 3%, 「excluded_…」 라벨 제외.

  · 순수 SQL(Postgres): `ADJ_EVENT_SQL` 과 `UNLABELED_ADJ_SQL` 은 서로 겹치지 않고, 합치면 「excluded_…」가 아닌 계수 있는 이벤트 전부다.
  · `_unlabeled_share_events`: 병합·「dart」 무상증자·종류 없음(NULL)은 잡고, 대규모 배당락(주식 수 그대로)·63일 밖 상장·조회 밖 종목은 안 잡는다. 읽기 예외는 종전 계수(fail-open).
  · firm: 병합 뒤 EPS 가 새 주식 기준으로 정렬되고(TTM EPS 6,300 → 31,500원), 「상장주식수가 … 배로 바뀌었습니다」 경고가 사라지며, PER·PBR 은 시총 기반이라 그대로다.
  · asset_holdings: 기말 뒤 5:1 병합(라벨 없음)이 「확정된 수정계수가 없다」로 시가 평가에서 빠지지 않는다.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from open_proxy_mcp.services import asset_holdings as A
from open_proxy_mcp.services import price_multiple_data as S

psycopg = pytest.importorskip("psycopg")


# ═══ 순수 SQL — Postgres ═══════════════════════════════════════════════════════════════════════════
def _exec(url):
    def run(sql, params=()):
        with psycopg.connect(url) as c:
            return c.execute(sql, params or None).fetchall()
    return run


# (ticker, event_dd, adj_factor, event_type, label_confidence)
EVENTS = [
    ("U1", "20260728", 5.0, "merge", "unlabeled"),            # 5:1 병합 — 주식 수 1/5
    ("U2", "20260630", 0.5, "bonus", "dart"),                 # 무상증자 — 「dart」 라벨(확정이 아니다), 주식 수 2배
    ("U3", "20260828", 0.21, None, "unlabeled"),              # 리츠 대규모 배당락 — 기준가만 내려가고 주식 수 그대로
    ("U4", "20260413", 0.2, "split", "confirmed"),            # 확정 라벨 분할 — ADJ_EVENT_SQL 쪽
    ("U5", "20260901", 1.01, "tick_rounding", "excluded_tick_rounding"),
    ("U6", "20260805", 0.2, None, None),                      # 종류·라벨 모두 NULL — COALESCE 가 아니면 행이 빠진다. 주식 수 5배
    ("U7", "20260701", 0.5, None, "unlabeled"),               # 무상증자 신주가 40일 뒤 상장(63일 안)
    ("U8", "20260701", 0.5, None, "unlabeled"),               # 신주 상장이 70일 뒤(63일 밖) — 이벤트 때는 안 늘었다
    ("U9", "20250105", 5.0, "merge", "unlabeled"),            # 조회 기준일 이전
    ("U10", "20260811", 3.0, "reduction", "confirmed"),       # 확정이지만 감자 — 주식 수가 1/3 이면 잡는다(배당 맞춤과 같다)
    ("OTHER", "20260728", 5.0, "merge", "unlabeled"),         # 요청한 종목이 아니다
]
# 종목 → (이벤트 앞 마지막 주 주식 수, [(이벤트 뒤 주, 주식 수)])
SHARES = {
    "U1": (1000, [("20260731", 200)]), "U2": (1000, [("20260807", 2000)]), "U3": (1000, [("20260904", 1000)]),
    "U6": (1000, [("20260814", 5000)]), "U7": (1000, [("20260725", 1000), ("20260810", 2000)]),
    "U8": (1000, [("20260725", 1000), ("20260910", 2000)]), "U9": (1000, [("20250110", 200)]),
    "U10": (900, [("20260821", 300)]), "OTHER": (1000, [("20260731", 200)]),
}


@pytest.fixture
def adb(pg17, monkeypatch):
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute("CREATE TABLE krx_adj_events (ticker text, event_dd text, adj_factor double precision, event_type text, label_confidence text)")
        c.cursor().executemany("INSERT INTO krx_adj_events VALUES (%s,%s,%s,%s,%s)", EVENTS)
        c.execute("CREATE TABLE krx_weekly (price_dd text, ticker text, list_shrs bigint, PRIMARY KEY (price_dd, ticker))")
        c.execute("CREATE TABLE price_latest (ticker text PRIMARY KEY, price_dd text, list_shrs bigint)")
        rows = []
        for t, (pre, post) in SHARES.items():
            rows.append(("20260101", t, pre))
            rows += [(d, t, n) for d, n in post]
        c.cursor().executemany("INSERT INTO krx_weekly VALUES (%s,%s,%s)", rows)
    monkeypatch.setattr(S, "_pg_rows", _exec(pg17))
    return pg17


def test_the_two_event_conditions_partition_the_non_excluded_events(adb):
    run = _exec(adb)
    conf = {(r[0], r[1]) for r in run("SELECT ticker, event_dd FROM krx_adj_events WHERE " + S.ADJ_EVENT_SQL)}
    unl = {(r[0], r[1]) for r in run("SELECT ticker, event_dd FROM krx_adj_events WHERE " + S.UNLABELED_ADJ_SQL)}
    allx = {(r[0], r[1]) for r in run("SELECT ticker, event_dd FROM krx_adj_events WHERE adj_factor IS NOT NULL "
                                       "AND COALESCE(label_confidence,'') NOT LIKE 'excluded%'")}
    assert conf == {("U4", "20260413")}
    assert not (conf & unl), "확정 라벨 분할·병합·무상증자·주식배당과 겹치면 계수를 두 번 곱한다"
    assert conf | unl == allx, "NULL 종류·NULL 라벨 이벤트(U6)가 COALESCE 없이 빠지면 여기서 어긋난다"
    assert ("U5", "20260901") not in unl and ("U6", "20260805") in unl


def test_unlabeled_share_events_finds_only_what_listed_shares_confirm(adb):
    got = asyncio.run(S._unlabeled_share_events(["U1", "U2", "U3", "U4", "U5", "U6", "U7", "U8", "U9", "U10"], "20260101"))
    assert set(got) == {"U1", "U2", "U6", "U7", "U10"}
    assert got["U1"] == [{"event_dd": "20260728", "adj_factor": 5.0, "shares_ratio": 0.2}]
    assert got["U2"][0]["adj_factor"] == 0.5 and got["U2"][0]["shares_ratio"] == 2.0, "「dart」 라벨 무상증자 — 확정 라벨이 아니라도 주식 수가 2배다"
    assert got["U6"][0]["shares_ratio"] == 5.0
    assert got["U7"][0]["shares_ratio"] == 2.0, "신주는 권리락 뒤 3~5주에 상장한다 — 이벤트 뒤 첫 주만 보면 놓친다(63일)"
    assert got["U10"][0]["adj_factor"] == 3.0, "확정 라벨 감자도 주식 수가 계수의 역수만큼 줄었으면 주식 수 조정이다(배당 맞춤과 같다)"
    assert "U3" not in got, "대규모 배당락은 주식 수가 안 바뀌었다 — 곱하지 않는다(기준가 조정 ≠ 분할)"
    assert "U8" not in got and "U9" not in got and "U4" not in got and "U5" not in got and "OTHER" not in got


def test_events_at_or_before_the_base_date_and_after_the_upper_bound_are_left_out(adb):
    got = asyncio.run(S._unlabeled_share_events(["U1", "U2", "U6"], "20260728"))       # 기준일 당일 이벤트는 「뒤」가 아니다(> 비교)
    assert set(got) == {"U6"}
    up = asyncio.run(S._unlabeled_share_events(["U1", "U2", "U6"], "20260101", "20260731"))
    assert set(up) == {"U1", "U2"}, "상한 이후(20260805)는 뺀다 — 기말 보유수량 맞춤은 시세 거래일까지만"


def test_adj_events_factor_multiplies_only_events_after_the_date():
    ev = [{"event_dd": "20260630", "adj_factor": 0.5, "shares_ratio": 2.0}, {"event_dd": "20260728", "adj_factor": 5.0, "shares_ratio": 0.2}]
    assert S._adj_events_factor(ev, "20260101") == 2.5 and S._adj_events_factor(ev, "20260630") == 5.0
    assert S._adj_events_factor(ev, "20260728") == 1.0 and S._adj_events_factor(None, "20260101") == 1.0


def test_a_read_failure_or_exception_falls_back_to_the_old_factors(monkeypatch):
    async def none(sql, params=()):
        return None
    monkeypatch.setattr(S, "_db_rows", none)
    assert asyncio.run(S._unlabeled_share_events(["U1"], "20260101")) == {}

    async def boom(sql, params=()):
        raise RuntimeError("읽기 예외")
    monkeypatch.setattr(S, "_db_rows", boom)
    assert asyncio.run(S._unlabeled_share_events(["U1"], "20260101")) == {}, "배당과 같은 fail-open — 확인이 깨져도 응답은 나간다"


# ═══ firm — 병합 뒤 공시 EPS 정렬 ═══════════════════════════════════════════════════════════════════
TODAY = date(2026, 10, 18)
CORP = {"corp_name": "가나다", "stock_code": "123450", "corp_code": "01363818"}


def _fy_rows():
    return [
        {"sj_div": "BS", "account_id": "ifrs-full_Assets", "thstrm_amount": "5,000,000,000,000", "currency": "KRW"},
        {"sj_div": "BS", "account_id": "ifrs-full_Liabilities", "thstrm_amount": "3,000,000,000,000"},
        {"sj_div": "BS", "account_id": "ifrs-full_Equity", "thstrm_amount": "2,000,000,000,000"},
        {"sj_div": "BS", "account_id": "ifrs-full_EquityAttributableToOwnersOfParent", "thstrm_amount": "1,900,000,000,000"},
        {"sj_div": "BS", "account_id": "ifrs-full_NoncontrollingInterests", "thstrm_amount": "100,000,000,000"},
        {"sj_div": "IS", "account_id": "ifrs-full_ProfitLossAttributableToOwnersOfParent", "thstrm_amount": "590,000,000,000", "frmtrm_amount": "500,000,000,000"},
        {"sj_div": "IS", "account_id": "ifrs-full_BasicEarningsLossPerShare", "thstrm_amount": "5,900"},
    ]


def _q_rows(ni_add, eps_add, eq):
    return [{"sj_div": "BS", "account_id": "ifrs-full_EquityAttributableToOwnersOfParent", "thstrm_amount": eq},
            {"sj_div": "IS", "account_id": "ifrs-full_ProfitLossAttributableToOwnersOfParent", "thstrm_amount": "9", "thstrm_add_amount": ni_add},
            {"sj_div": "IS", "account_id": "ifrs-full_BasicEarningsLossPerShare", "thstrm_amount": "1,300", "thstrm_add_amount": eps_add}]


def _run_firm(monkeypatch, *, unlabeled, shares_first=5_000_000_000, shares_latest=1_000_000_000, dart_shares=1_000_000_000):
    """unlabeled = 라벨 없는 조정 후보 읽기 결과(행) — 후보마다 주식 수 읽기 `(앞 마지막 주, [뒤 주…], 최신)` 는 `unl_shares`."""
    state = {"unl_rows": unlabeled["rows"], "unl_shares": unlabeled["shares"]}

    def rows(sql, params=()):
        s = " ".join(sql.split())
        if "FROM krx_stock_flags" in s:
            return []
        if "FROM krx_adj_events" in s:
            return state["unl_rows"] if "ORDER BY ticker, event_dd" in s else []        # 확정 라벨 계수 읽기는 비어 있다
        if s.startswith("SELECT (SELECT list_shrs FROM krx_weekly"):
            return [state["unl_shares"]]
        if "SELECT list_shrs FROM krx_weekly" in s and "ASC LIMIT 1" in s:
            return [(shares_first,)]
        if s == "SELECT list_shrs FROM price_latest WHERE ticker=%s AND list_shrs>0":
            return [(shares_latest,)]
        return []

    async def resolve(q):
        return dict(CORP), None

    async def fin_metrics(code, **kw):
        return {"data": {"summary": {"eps_krw": 5900, "revenue_krw": 3 * 10**12, "roe_pct": 10.5, "capital_impairment_status": None}, "year": 2025}}

    async def market_for(code):
        return {"price": 70000, "date": "20261016", "common_mktcap": 4 * 10**12, "list_shrs": 1_000_000_000}

    async def acntall(client, cc, year, rc, fs=None):
        if year == 2025 and rc == "11011":
            return _fy_rows(), "CFS"
        if rc == "11012" and year == 2026:
            return _q_rows("180,000,000,000", "3,100", "1,950,000,000,000"), "CFS"
        if rc == "11012" and year == 2025:
            return _q_rows("100,000,000,000", "2,700", "1,850,000,000,000"), "CFS"
        return [], None

    class Client:
        async def get_company_info(self, cc):
            return {"induty_code": "264", "acc_mt": "12"}

    async def shares_out(client, cc, year):
        return {"total": dart_shares, "common": dart_shares}

    async def annual_summary(cc, fy):
        return {}, None
    mp = monkeypatch.setattr
    mp(S, "_pg_rows", rows); mp(S, "today_kst", lambda: TODAY); mp(S, "_resolve_listed", resolve)
    mp(S, "get_dart_client", lambda: Client()); mp(S, "build_financial_metrics_payload", fin_metrics); mp(S, "_market_for", market_for)
    mp(S, "_acntall", acntall); mp(S, "_shares_outstanding", shares_out); mp(S, "_annual_summary", annual_summary)
    return asyncio.run(S.build_valuation_payload("123450", format="json"))


MERGE_5_1 = {"rows": [("123450", "20260728", 5.0, "merge", "unlabeled")], "shares": (5_000_000_000, [1_000_000_000], 1_000_000_000)}


def test_an_unlabeled_merge_after_the_report_dates_is_applied_to_the_disclosed_eps(monkeypatch):
    """5:1 병합(2026-07-28, 라벨 없음) — 공시 EPS 는 FY2025(5,900) · 2026 반기 누적(3,100) · 2025 반기 누적(2,700) 모두 병합 전 주식 기준.
    종전: 정렬 없음 → TTM EPS 6,300(옛 주식 기준, 지금 주가는 새 주식 기준) + 「상장주식수가 0.2배로 바뀌었습니다」 경고. 이제 ×5 → 31,500."""
    new = _run_firm(monkeypatch, unlabeled=MERGE_5_1)["data"]
    assert new["inputs"]["eps_ttm_krw"] == 31_500 and new["inputs"]["eps_fy0_krw"] == 29_500
    assert new["inputs"]["eps_adj_factors"] == {"current": 5.0, "prior_q": 5.0,
                                                 "unlabeled_events": [{"event_dd": "2026-07-28", "adj_factor": 5.0, "shares_ratio": 0.2}]}
    assert new["inputs"]["shares_unadjusted"] is None
    assert not any("상장주식수가 결산기준일 이후" in w for w in new["warnings"])
    assert any(w.startswith("EPS 정렬에 확정 라벨이 없는 기준가 조정 1건을 반영했다") and "2026-07-28 계수 5(상장주식 수 ×0.2)" in w for w in new["warnings"])


def test_without_the_change_the_same_merge_left_eps_in_the_old_share_base(monkeypatch):
    """대조 — 후보 읽기를 막으면(종전 동작) EPS 는 병합 전 주식 기준 그대로이고 주식 수 불일치 경고가 뜬다."""
    async def none(tickers, after, upto=None):
        return {}
    monkeypatch.setattr(S, "_unlabeled_share_events", none)
    old = _run_firm(monkeypatch, unlabeled=MERGE_5_1)["data"]
    assert old["inputs"]["eps_ttm_krw"] == 6_300 and old["inputs"]["eps_adj_factors"] is None
    assert old["inputs"]["shares_unadjusted"] == {"shares_ratio": 0.2, "factor": 1.0}
    assert any("상장주식수가 결산기준일 이후 0.2배로 바뀌었습니다" in w for w in old["warnings"])


def test_multiples_do_not_move_because_they_are_market_cap_based(monkeypatch):
    new = _run_firm(monkeypatch, unlabeled=MERGE_5_1)["data"]["multiples"]

    async def none(tickers, after, upto=None):
        return {}
    monkeypatch.setattr(S, "_unlabeled_share_events", none)
    old = _run_firm(monkeypatch, unlabeled=MERGE_5_1)["data"]["multiples"]
    assert new == old and new["per_ttm"] is not None and new["pbr_mrq"] is not None, "PER·PBR 은 시총 ÷ 지배순이익·지배자본이라 주식 수 조정과 무관하다"


def test_old_share_count_in_the_annual_report_is_explained_not_called_a_parsing_error(monkeypatch):
    """실측 000040(2026-07-28 5:1 병합): DART 유통주식수는 사업보고서(사업연도 말) 기준이라 옛 주식 수 — 지금 상장주식수의 4.4배다. 계수가 비어 있던 때는 위의
    「상장주식수가 … 배로 바뀌었습니다」 경고가 이 자리를 가져갔다. 계수가 잡히면 그 경고가 사라지므로, 아래 「DART 파싱오류 의심」이 같은 종목을 오진하지 않게 한다."""
    d = _run_firm(monkeypatch, unlabeled=MERGE_5_1, dart_shares=4_400_000_000)["data"]
    assert not any("파싱오류 의심" in w for w in d["warnings"])
    assert any("사업연도 말 뒤 병합·감자로 상장주식수가 줄어든 것으로 보인다" in w for w in d["warnings"])
    assert d["inputs"]["bps_krw"] is None, "주당 지표(BPS)는 옛 주식 수라 비운다(종전과 같다)"
    # 계수가 없는 같은 모양(DART 주식수가 상장주식수 3배 초과, 계수 없음)은 종전 문구 그대로 — 진짜 파싱오류 의심
    plain = _run_firm(monkeypatch, unlabeled={"rows": [], "shares": (None, [], None)}, shares_first=1_000_000_000, dart_shares=4_400_000_000)["data"]
    assert any("DART 파싱오류 의심" in w for w in plain["warnings"])


def test_a_big_ex_dividend_with_unchanged_shares_is_not_a_share_event(monkeypatch):
    ex_div = {"rows": [("123450", "20260828", 0.21, None, "unlabeled")], "shares": (1_000_000_000, [1_000_000_000], 1_000_000_000)}
    d = _run_firm(monkeypatch, unlabeled=ex_div, shares_first=1_000_000_000)["data"]
    assert d["inputs"]["eps_adj_factors"] is None and d["inputs"]["eps_ttm_krw"] == 6_300
    assert not any("확정 라벨이 없는 기준가 조정" in w for w in d["warnings"])


def test_no_candidates_keeps_the_response_exactly_as_before(monkeypatch):
    d = _run_firm(monkeypatch, unlabeled={"rows": [], "shares": (None, [], None)}, shares_first=1_000_000_000)["data"]
    assert d["inputs"]["eps_adj_factors"] is None and d["inputs"]["eps_ttm_krw"] == 6_300
    assert "unlabeled_events" not in (d["inputs"]["eps_adj_factors"] or {})


def test_an_event_between_the_prior_and_current_quarter_ends_only_aligns_the_prior_piece(monkeypatch):
    """2025-09 병합(전년 동기 2025-06 뒤 · 당해 2026-06 앞) — 전년 동기 누적(2,700)만 새 주식 기준으로. 당해·연간은 병합 뒤 보고서라 이미 새 기준."""
    mid = {"rows": [("123450", "20250915", 5.0, "merge", "unlabeled")], "shares": (5_000_000_000, [1_000_000_000], 1_000_000_000)}
    d = _run_firm(monkeypatch, unlabeled=mid, shares_first=1_000_000_000)["data"]
    assert d["inputs"]["eps_adj_factors"]["current"] == 1.0 and d["inputs"]["eps_adj_factors"]["prior_q"] == 5.0
    assert d["inputs"]["eps_ttm_krw"] == 5_900 + 3_100 - 2_700 * 5


# ═══ asset_holdings — 기말 보유수량 ══════════════════════════════════════════════════════════════════
def _h(name, book, qty):
    return {"inv_prm": name, "trmend_blce_acntbk_amount": f"{book:,}", "trmend_blce_qy": f"{qty:,}",
            "recent_bsns_year_fnnr_sttus_tot_assets": "-", "trmend_blce_qota_rt": "-"}


class _Client:
    def __init__(self):
        self.price_calls = []

    async def lookup_corp_code_all(self, name):
        return [{"corp_name": "시그네틱스", "stock_code": "033170", "_resolution": {"match_kind": "official", "inferred": False}}]

    async def get_stock_price(self, code, base_date):
        self.price_calls.append((code, base_date))
        return None


def _mark(monkeypatch, *, unl):
    monkeypatch.setattr(A, "_db_quotes", lambda day, codes: {"033170": (3_140, 17_145_663)})
    seen = []

    def adjust(codes, qty_dd, upto):
        seen.append((codes, qty_dd, upto))
        return {}, {"033170": 85_728_319}                 # 확정 계수 없음 · 기말 상장주식수 85,728,319 → 지금 17,145,663 (5:1 병합)
    monkeypatch.setattr(A, "_db_share_adjust", adjust)

    async def unlabeled(tickers, after, upto=None):
        seen.append(("unl", tickers, after, upto))
        return unl
    monkeypatch.setattr(A._val, "_unlabeled_share_events", unlabeled)
    out = asyncio.run(A._mark_listed_stakes(_Client(), [_h("시그네틱스㈜", 100_000_000, 710_651)], "시그네틱스상장", "20261016", "20251231"))
    return out, seen


def test_a_label_less_merge_no_longer_drops_the_stake_from_the_market_valuation(monkeypatch):
    """실측 시그네틱스 033170: 20260826 액면병합 5:1(계수 라벨 없음) — 종전: 「확정된 수정계수가 없다」 → 장부가로 둠. 이제 상장주식 수 배율로 확인된
    병합이라 기말 수량 ÷ 5 = 142,130주 × 3,140원."""
    ev = {"033170": [{"event_dd": "20260826", "adj_factor": 5.0, "shares_ratio": 0.2}]}
    out, seen = _mark(monkeypatch, unl=ev)
    assert ("unl", ["033170"], "20251231", "20261016") in seen
    assert out["n_marked"] == 1 and out["n_unresolved"] == 0
    m = out["marked"][0]
    assert m["qty"] == 710_651 and m["adj_factor"] == 5.0 and m["qty_adj"] == round(710_651 / 5)
    assert m["mkt_krw"] == round(710_651 / 5) * 3_140 and m["adj_basis"] == "listed_shares_ratio"


def test_without_a_confirmed_share_event_the_old_unresolved_reason_stays(monkeypatch):
    out, _ = _mark(monkeypatch, unl={})
    assert out["n_marked"] == 0 and "확정된 수정계수가 없다" in out["unresolved"][0]["reason"]
    assert out["listed_mkt_krw"] == out["listed_book_krw"] == 100_000_000, "미해결은 장부가 유지(종전 그대로)"
