# -*- coding: utf-8 -*-
"""asset_holdings 상장 보유지분 시가마크·지분 NAV — 261018 운영 결함들의 회귀. network 0.

운영 실측(261018): 삼성물산 0/12·SK 0/13·LG 0/7 — 상장지분이 하나도 평가되지 않았고 경고도 없었다.
원인: 종목코드 사전을 `<레포>/configs/master.db` 라는 **박아 둔 경로**에서 읽었는데 운영 이미지엔 그 파일이 없고
기업 마스터는 볼륨(`OPM_MASTER_DB_PATH`)에 있다 → 빈 사전 → 시세를 묻기도 전에 전부 미해결.
고친 뒤: 서버의 기업 마스터 해석기로 코드를 찾고, 종가는 시총과 같은 거래일의 일별 시세 저장분에서 한 번에 읽는다.

시가마크가 살아나며 드러난 것(같은 날 검토 실측, 읽기 전용 DB):
  · 지분 NAV 산식이 별도 기준 보유 목록과 연결 재무상태표를 섞어 같은 지분을 두 번 셌다(삼성물산 3.25·LG 2.88·SK 2.46배).
    → 타법인출자현황 하나로 합산(상장 시가 + 비상장 장부가): 2.35·1.59·1.94배.
  · 기말 뒤 액면분할을 반영하지 않았다(LG 의 엘에스일렉트릭 1/5 분할 — 시가 1,094억, 실제 약 5,473억).
  · 합계 줄이 상장 후보로 들어가 합계 장부가가 한 번 더 더해졌다(한화 5.96조).
  · 자본 계정 「기타포괄손익누계액」이 FVOCI 지분(자산)으로 들어갔다(LG 5.19조·현대차 5.24조).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from open_proxy_mcp.services import asset_holdings as A

T = 10**12   # 조


def _h(name, book, qty, ta=0, rate="-"):
    return {"inv_prm": name, "trmend_blce_acntbk_amount": f"{book:,}", "trmend_blce_qy": f"{qty:,}",
            "recent_bsns_year_fnnr_sttus_tot_assets": f"{ta:,}" if ta else "-", "trmend_blce_qota_rt": rate}


class FakeClient:
    """`lookup_corp_code_all` 만 있는 가짜 — 해석기 결과 모양(stock_code·_resolution)을 그대로 흉내 낸다."""

    def __init__(self, table: dict[str, list[dict]]):
        self.table = table
        self.lookups: list[str] = []
        self.price_calls: list[tuple[str, str]] = []

    async def lookup_corp_code_all(self, name):
        self.lookups.append(name)
        return self.table.get(name, [])

    async def get_stock_price(self, code, base_date):
        self.price_calls.append((code, base_date))
        return {"closing_price": 1000, "base_date": base_date, "source": "portal"}


def _m(corp_name, code, inferred=False):
    return {"corp_name": corp_name, "stock_code": code, "_resolution": {"match_kind": "official", "inferred": inferred}}


DOC = ("삼성전자상장 삼성생명보험상장 에스케이상장 삼성전자우상장 해외법인상장 "
       "처분한회사상장 비상장회사비상장 합계상장")

TABLE = {
    "삼성전자㈜": [_m("삼성전자", "005930")],
    "삼성생명보험㈜": [_m("삼성생명", "032830")],                 # 공식명과 다른 표기 — 해석기가 잡는다
    "에스케이㈜": [_m("SK", "034730"), _m("SK", "003600")],       # 상장폐지 옛 법인 코드가 함께 나온다
    "삼성전자우": [_m("삼성전자", "005930", inferred=True)],       # 추정 일치 — 받지 않는다
    "해외법인": [],
}

HOLD = [
    _h("삼성전자㈜", 35_828_290_000_000, 298_818_100),
    _h("삼성생명보험㈜ ", 6_097_200_000_000, 38_688_000),                 # 원자료 꼬리 공백 — 응답엔 걷어 낸 이름
    _h("에스케이㈜", 100_000_000_000, 1_000_000),
    _h("삼성전자우", 3_920_000_000, 50_000),
    _h("해외법인", 32_243_000_000, 10_000),
    _h("처분한회사", 0, 0),                                       # 기중 전량 처분 — 후보에서 뺀다
    _h("비상장회사", 9_999_000_000, 1, rate="100"),                # 원문이 비상장 — 장부가로 지분 NAV 에 든다
    _h("합계", 99_000_000_000_000, 0),                            # 합계 줄 — 원문에 「합계 … 상장」 이 붙어 있어도 보유분이 아니다
]

STORE = {"005930": (215_500, 5_919_637_922), "032830": (238_500, 200_000_000),
         "034730": (456_500, 72_502_703)}   # 003600 은 없다(상장폐지)


@pytest.fixture
def db(monkeypatch):
    """`_db_quotes`·`_db_share_adjust` 를 가짜로 — 호출 인자를 남기고, 저장분에 있는 종목만 돌려준다."""
    calls = {"quotes": [], "adjust": []}
    state = {"factors": {}, "base": {}}

    def quotes(day, codes):
        calls["quotes"].append((day, sorted(codes)))
        return {c: STORE[c] for c in codes if c in STORE}

    def adjust(codes, qty_dd, upto):
        calls["adjust"].append((sorted(codes), qty_dd, upto))
        return state["factors"], state["base"]
    monkeypatch.setattr(A, "_db_quotes", quotes)
    monkeypatch.setattr(A, "_db_share_adjust", adjust)
    calls["state"] = state
    return calls


def test_marks_from_daily_store_at_the_market_cap_date(db):
    cli = FakeClient(TABLE)
    out = asyncio.run(A._mark_listed_stakes(cli, HOLD, DOC, "20261016", "20251231"))
    # 시총 기준일로 한 번만 읽는다 — 종목마다 묻지 않는다
    assert db["quotes"] == [("20261016", ["003600", "005930", "032830", "034730"])]
    assert db["adjust"] == [(["005930", "032830", "034730"], "20251231", "20261016")]   # 수정계수도 한 번에
    assert cli.price_calls == []
    by = {m["name"]: m for m in out["marked"]}
    assert set(by) == {"삼성전자㈜", "삼성생명보험㈜", "에스케이㈜"}
    assert by["삼성전자㈜"]["code"] == "005930" and by["삼성전자㈜"]["px"] == 215_500
    assert by["삼성전자㈜"]["mkt_krw"] == 298_818_100 * 215_500 and by["삼성전자㈜"]["px_date"] == "20261016"
    assert "qty_adj" not in by["삼성전자㈜"]                     # 기말 뒤 이벤트 없음 — 기말 수량 그대로
    # 동명 후보 둘 중 그날 시세가 있는 쪽(현 SK)으로 가린다
    assert by["에스케이㈜"]["code"] == "034730"
    # 출처 값은 시세 tool 과 같은 어휘(저장소 표 이름을 값으로 내지 않는다)
    assert out["price_date"] == "20261016" and out["price_source"] == "db"
    assert out["qty_date"] == "20251231" and out["qty_adjust_checked"] is True


def test_unresolved_carry_name_book_and_reason_and_counts_add_up(db):
    out = asyncio.run(A._mark_listed_stakes(FakeClient(TABLE), HOLD, DOC, "20261016", "20251231"))
    un = {u["name"]: u for u in out["unresolved"]}
    assert set(un) == {"삼성전자우", "해외법인"}                     # 합계 줄은 후보가 아니다
    assert "추정" in un["삼성전자우"]["reason"]                      # 추정 일치는 다른 종목 시세로 평가하지 않는다
    assert "국내 상장 종목코드" in un["해외법인"]["reason"]
    assert un["해외법인"]["book_krw"] == 32_243_000_000
    # 처분 완료·비상장·합계는 후보가 아니고, 개수와 합계가 같은 집합을 센다(종전: 상한 밖 후보가 개수에만 들었다)
    assert out["n_listed"] == 5 == out["n_marked"] + out["n_unresolved"]
    assert out["listed_book_krw"] == sum(_b for _b in (35_828_290_000_000, 6_097_200_000_000, 100_000_000_000,
                                                        3_920_000_000, 32_243_000_000))
    # 미해결은 장부가로 남는다
    mkt = sum(m["mkt_krw"] for m in out["marked"]) + 3_920_000_000 + 32_243_000_000
    assert out["listed_mkt_krw"] == mkt and out["unrealized_gap_krw"] == mkt - out["listed_book_krw"]
    # 비상장은 장부가 합으로 — 합계 줄(99조)은 어디에도 더하지 않는다
    assert out["unlisted_book_krw"] == 9_999_000_000 and out["n_unlisted"] == 1
    assert out["unlisted_majority_book_krw"] == 9_999_000_000     # 지분율 100% — 대개 종속기업


def test_total_row_is_never_a_holding(db):
    """실측 한화: 원문에 「합계 … 상장」 이 붙어 합계 줄(5.96조)이 「보유수량 없음」 미해결로 상장지분 장부가·시가 양쪽에 더해졌다."""
    hold = [_h("합계", 5_955_000_000_000, 0), _h(" 소계 ", 10**12, 0), _h("삼성전자㈜", 10**12, 1_000)]
    out = asyncio.run(A._mark_listed_stakes(FakeClient(TABLE), hold, "합계상장 소계상장 삼성전자상장", "20261016", "20251231"))
    assert out["n_listed"] == 1 and out["listed_book_krw"] == 10**12
    assert out["n_unlisted"] == 0 and out["unlisted_book_krw"] == 0


def test_split_after_year_end_scales_the_holding(db):
    """실측 LG 의 엘에스일렉트릭: 기말 690,167주 · 20260413 액면분할 1/5(확정 계수 0.2) · 분할 뒤 종가 158,600원.
    종전엔 기말 수량에 분할 뒤 종가를 곱해 시가 1,094억(「장부 3,175억 → 시가 1,094억」 가짜 평가손)이었다."""
    table = {"엘에스일렉트릭(주)": [_m("LS ELECTRIC", "010120")]}
    STORE["010120"] = (158_600, 150_000_000)
    db["state"]["factors"] = {"010120": 0.2}
    db["state"]["base"] = {"010120": 30_000_000}
    try:
        out = asyncio.run(A._mark_listed_stakes(FakeClient(table), [_h("엘에스일렉트릭(주)", 317_500_000_000, 690_167)],
                                                "엘에스일렉트릭상장", "20261016", "20251231"))
    finally:
        del STORE["010120"]
    m = out["marked"][0]
    assert m["qty"] == 690_167 and m["qty_adj"] == 3_450_835 and m["adj_factor"] == 0.2
    assert m["mkt_krw"] == 3_450_835 * 158_600                      # 약 5,473억
    assert m["gap_krw"] > 0


def test_share_count_change_without_factor_keeps_book_with_reason(db):
    """실측 시그네틱스: 20260826 액면병합 5:1(계수 라벨 미확정) — 상장주식수 85,728,319 → 17,145,663.
    확정 계수가 없으면 기말 수량에 병합 뒤 종가를 곱해 5배로 부풀린다. 평가하지 않고 사유와 함께 장부가로 둔다."""
    table = {"시그네틱스㈜": [_m("시그네틱스", "033170")]}
    STORE["033170"] = (3_140, 17_145_663)
    db["state"]["factors"] = {}
    db["state"]["base"] = {"033170": 85_728_319}
    try:
        out = asyncio.run(A._mark_listed_stakes(FakeClient(table), [_h("시그네틱스㈜", 100_000_000, 710_651)],
                                                "시그네틱스상장", "20261016", "20251231"))
    finally:
        del STORE["033170"]
    assert out["n_marked"] == 0
    u = out["unresolved"][0]
    assert "0.20배" in u["reason"] and "수정계수가 없다" in u["reason"]
    assert out["listed_mkt_krw"] == out["listed_book_krw"] == 100_000_000


def test_db_down_uses_shared_krx_snapshot_of_the_same_day(monkeypatch):
    monkeypatch.setattr(A, "_db_quotes", lambda dd, codes: None)            # DB 장애
    monkeypatch.setattr(A, "_db_share_adjust", lambda codes, q, u: None)
    seen = []

    async def snap(day):
        seen.append(day)
        return {"005930": {"TDD_CLSPRC": "215,500", "LIST_SHRS": "5,919,637,922"}, "032830": {"TDD_CLSPRC": "238500"}}
    monkeypatch.setattr(A._val, "_krx_market_live", snap)
    cli = FakeClient(TABLE)
    out = asyncio.run(A._mark_listed_stakes(cli, HOLD, DOC, "20261016", "20251231"))
    assert seen == ["20261016"] and cli.price_calls == []                    # 종목별 콜 0
    assert {m["code"] for m in out["marked"]} == {"005930", "032830"}
    assert out["price_source"] == "krx_live"
    sk = next(u for u in out["unresolved"] if u["name"] == "에스케이㈜")
    assert "20261016 거래일 시세 없음" in sk["reason"]
    # 수정계수를 못 읽었다 — 기말 수량 그대로 썼다는 것을 밝힌다
    assert out["qty_adjust_checked"] is False


def test_db_and_krx_down_fall_back_per_stock_on_previous_trading_day(monkeypatch):
    """KRX 일별 시세는 다음 날 게시 — 오늘 날짜로 물으면 늘 빈 응답이었다(종전 결함). 직전 거래일로 묻는다."""
    monkeypatch.setattr(A, "_db_quotes", lambda dd, codes: None)
    monkeypatch.setattr(A, "_db_share_adjust", lambda codes, q, u: None)

    async def no_dd():
        return None

    async def empty(day):
        return {}
    monkeypatch.setattr(A._val, "_serving_price_dd", no_dd)
    monkeypatch.setattr(A._val, "_krx_market_live", empty)
    monkeypatch.setattr(A, "_prev_trading_day", lambda: "20261016")
    monkeypatch.setattr(A, "_LIVE_PX_CAP", 2)
    cli = FakeClient(TABLE)
    out = asyncio.run(A._mark_listed_stakes(cli, HOLD, DOC, None, "20251231"))
    assert cli.price_calls == [("005930", "20261016"), ("032830", "20261016")]   # 장부가 큰 순, 상한 2
    sk = next(u for u in out["unresolved"] if u["name"] == "에스케이㈜")
    assert "상한" in sk["reason"]
    assert out["price_source"] == "portal" and out["n_marked"] == 2


def test_without_market_cap_date_uses_the_shared_serving_day(db, monkeypatch):
    """시총을 못 구했을 때 기준일을 이 파일에서 따로 정의하지 않는다 — 가격 배수 서비스의 서빙 기준일을 따른다."""
    async def serving():
        return "20261015"
    monkeypatch.setattr(A._val, "_serving_price_dd", serving)
    asyncio.run(A._mark_listed_stakes(FakeClient(TABLE), HOLD, DOC, None, "20251231"))
    assert db["quotes"][-1][0] == "20261015"


def test_db_reads_are_one_round_trip_each_and_share_the_adjust_rule(monkeypatch):
    import open_proxy_mcp.db as dbm
    seen = []

    def rows(sql, params=()):
        seen.append((" ".join(sql.split()), params))
        if "FROM krx_daily" in sql:
            return [("005930", 215500, 5919637922)]
        if "FROM krx_adj_events" in sql:
            return [("010120", 0.2), ("010120", 0.5)]
        return [("010120", 30_000_000)]
    monkeypatch.setattr(dbm, "pg_rows", rows)
    assert A._db_quotes("20261016", ["005930", "000660"]) == {"005930": (215500, 5919637922)}
    assert seen[-1] == ("SELECT ticker, close, list_shrs FROM krx_daily WHERE trd_dd = %s AND ticker = ANY(%s)",
                        ("20261016", ["005930", "000660"]))
    f, base = A._db_share_adjust(["010120"], "20251231", "20261016")
    assert f == {"010120": 0.1} and base == {"010120": 30_000_000}       # 누적곱
    ev_sql, ev_params = seen[-2]
    assert ev_params == (["010120"], "20251231", "20261016")
    assert ev_sql.endswith(" ".join(A._val.ADJ_EVENT_SQL.split()))      # 공시 EPS 정렬과 같은 이벤트 조건
    assert "price_dd <= %s" in seen[-1][0] and seen[-1][1] == (["010120"], "20251231")   # 기준일 **이전** 마지막 주
    monkeypatch.setattr(dbm, "pg_rows", lambda sql, params=(): None)           # DB 미설정·장애
    assert A._db_quotes("20261016", ["005930"]) is None
    assert A._db_share_adjust(["010120"], "20251231", "20261016") is None


def test_eps_adjust_factor_uses_the_same_event_rule(monkeypatch):
    """가격 배수 서비스의 공시 EPS 정렬도 같은 조건 상수를 쓴다(정의 한 곳)."""
    seen = []

    async def rows(sql, params=()):
        seen.append(sql)
        return [(0.2,)]
    monkeypatch.setattr(A._val, "_db_rows", rows)
    assert asyncio.run(A._val._eps_adj_factor("010120", "20251231")) == 0.2
    assert seen[-1].endswith(A._val.ADJ_EVENT_SQL)


def test_period_end_of_report_name():
    assert A._period_end("사업보고서 (2025.12)") == "20251231"
    assert A._period_end("반기보고서 (2026.06)") == "20260630"
    assert A._period_end("분기보고서 (2024.02)") == "20240229"
    assert A._period_end("사업보고서") is None and A._period_end(None) is None


def test_stake_codes_does_not_touch_the_request_ledger():
    """보유지분 이름 해석이 「이 요청이 조사한 기업」 장부·추정 경고에 섞이면 안 된다 —
    후보 목록 함수(`lookup_corp_code_all`)만 쓴다. 해석 실패는 응답을 깨지 않고 사유로 남는다."""
    class Boom:
        async def lookup_corp_code_all(self, name):
            raise RuntimeError("x")
    assert asyncio.run(A._stake_codes(Boom(), "삼성전자")) == ([], "종목코드 조회 실패")
    codes, why = asyncio.run(A._stake_codes(FakeClient(TABLE), "에스케이㈜"))
    assert codes == ["034730", "003600"] and why is None


def test_no_hardcoded_master_db_path():
    """종전 결함의 뿌리 — 레포 안 파일 경로를 박아 열던 사전이 다시 생기지 않게."""
    assert not hasattr(A, "_MASTER") and not hasattr(A, "_listed_lookup")


def test_equity_lines_are_not_asset_tiers():
    """실측 LG·현대차: 자본 계정 「기타포괄손익누계액」(5.19조·5.24조)이 FVOCI 지분 자산으로 더해졌다."""
    assert A._tier("기타포괄손익누계액") is None
    assert A._tier("기타포괄손익-공정가치측정금융자산평가손익") is None
    assert A._tier("이익잉여금") is None and A._tier("비지배지분") is None
    assert A._tier("비유동 기타포괄손익-공정가치 측정 금융자산") == "oci"      # 자산 줄은 그대로
    tiers, _ = A._bs_tiers([{"sj_div": "BS", "account_nm": "관계기업및공동기업투자", "thstrm_amount": "23675698000000"},
                            {"sj_div": "BS", "account_nm": "기타포괄손익누계액", "thstrm_amount": "5189175000000"}])
    assert tiers == {"assoc": 23_675_698_000_000}


# ── 지분 NAV 산식(빌드 수준) — 삼성물산·LG·SK·영풍 실측값(261018, 시총 기준일 20261016) ──

def _mark_out(listed_mkt, listed_book, unlisted, n_listed=1, n_unlisted=1, **kw):
    return {"marked": [], "unresolved": [], "price_date": "20261016", "price_source": "db", "qty_date": "20251231",
            "qty_adjust_checked": True, "listed_book_krw": listed_book, "listed_mkt_krw": listed_mkt,
            "unrealized_gap_krw": listed_mkt - listed_book, "n_listed": n_listed, "n_marked": n_listed,
            "n_unresolved": 0, "unlisted_book_krw": unlisted, "unlisted_majority_book_krw": 0,
            "n_unlisted": n_unlisted, **kw}


def _build(monkeypatch, *, bs, induty, mark, mcap, seen=None):
    async def resolve(q):
        return SimpleNamespace(status=A.AnalysisStatus.EXACT,
                               selected={"corp_code": "00000001", "stock_code": "000001", "corp_name": q}, candidates=[])

    async def pick(client, cc, report, year):
        return {"rcept_no": "20260312000856", "report_nm": "사업보고서 (2025.12)"}, "11011", ""

    async def getdoc(client, rno):
        return {"note_html": "", "full_text": ""}

    async def market_cap(isu):
        return mcap, {"shares": 1, "close": 1, "date": "20261016"}

    async def fake_mark(client, holdings, doc_text, price_dd=None, qty_dd=None):
        if seen is not None:
            seen.update(price_dd=price_dd, qty_dd=qty_dd)
        return mark

    class Cli:
        async def get_fnltt_singl_acnt_all(self, *a):
            return {"list": [{"sj_div": "BS", "account_nm": k, "thstrm_amount": str(v)} for k, v in bs.items()]}

        async def get_company_info(self, cc):
            return {"induty_code": induty}

        async def get_other_corp_investment(self, *a):
            return {"list": []}

    monkeypatch.setattr(A, "get_dart_client", lambda: Cli())
    monkeypatch.setattr(A, "resolve_company_query", resolve)
    monkeypatch.setattr(A, "_pick_periodic_report", pick)
    monkeypatch.setattr(A, "_safe_getdoc", getdoc)
    monkeypatch.setattr(A, "_market_cap", market_cap)
    monkeypatch.setattr(A, "_mark_listed_stakes", fake_mark)
    return asyncio.run(A._build_asset_holdings_payload_impl("X"))


def test_samsung_cnt_equity_nav_counts_each_stake_once(monkeypatch):
    """연결 FVOCI 44.82조 = 삼성전자·삼성생명·삼성SDS 의 기말 공정가치. 같은 종목이 시가(106.10조)로 또 들어가 3.25배였다."""
    out = _build(monkeypatch, induty="467", mcap=46_461_011_956_500,
                 bs={"현금및현금성자산": 5_456 * 10**9, "관계기업 및 공동기업 투자": 970 * 10**9,
                     "비유동 기타포괄손익-공정가치 측정 금융자산": 44_822 * 10**9},
                 mark=_mark_out(106_099 * 10**9, 47_056 * 10**9, 2_940 * 10**9))
    nav = out["data"]["nav"]
    assert nav["equity_nav_basis"] == "holdings"
    assert nav["equity_nav_krw"] == (106_099 + 2_940) * 10**9
    assert round(nav["equity_nav_cov"], 2) == 2.35                     # 종전 3.25
    assert nav["equity_bs_book_krw"] == (970 + 44_822) * 10**9        # 참고값 — 배수에 섞지 않는다


def test_lg_general_holdco_equity_nav_and_no_surplus_cov(monkeypatch):
    """LG(64992, 금융 계정 없음): 금융업 아님 → 지분 NAV 배수를 낸다. 연결 지분법 23.68조 − 별도 원가 6.87조를 「비상장 잔여」로
    더하던 이중 계산(2.88배)이 없어야 한다. 잉여자산 배수는 일반 지주회사라 내지 않는다(연결 현금 대부분이 자회사 몫)."""
    out = _build(monkeypatch, induty="64992", mcap=15_603_014_812_800,
                 bs={"현금및현금성자산": 1_511_450_000_000, "관계기업및공동기업투자": 23_675_698_000_000,
                     "기타포괄손익누계액": 5_189_175_000_000, "투자부동산": 1_082_869_000_000},
                 mark=_mark_out(23_446 * 10**9, 6_871 * 10**9, 1_341 * 10**9))
    d = out["data"]
    assert d["is_financial"] is False and d["is_holding_company"] is True
    assert not any("금융업" in w for w in out["warnings"])
    assert round(d["nav"]["equity_nav_cov"], 2) == 1.59                # 종전 2.88(분할 미반영 포함)
    assert d["nav"]["equity_bs_book_krw"] == 23_675_698_000_000        # 자본 계정(기타포괄손익누계액)은 섞이지 않는다
    assert d["nav"]["surplus_cov"] is None and d["nav"]["surplus_krw"] == 1_511_450_000_000 + 1_082_869_000_000
    assert any("일반 지주회사" in w and "잉여자산 배수" in w for w in out["warnings"])


def test_sk_general_holdco_equity_nav(monkeypatch):
    """SK: 상장 보유분이 연결 자회사라 연결 관계기업(37.79조)에 없는데 그 원가를 거기서 뺐다(2.46배). 연결 현금 28.66조로
    잉여자산 1.14배 「시총 초과」도 새로 나갈 뻔했다."""
    out = _build(monkeypatch, induty="64992", mcap=33_097_483_919_500,
                 bs={"현금및현금성자산": 28_663 * 10**9, "관계기업및공동기업투자": 37_786 * 10**9},
                 mark=_mark_out(57_965 * 10**9, 14_240 * 10**9, 6_119 * 10**9))
    nav = out["data"]["nav"]
    assert round(nav["equity_nav_cov"], 2) == 1.94 and nav["surplus_cov"] is None
    assert not any("자회사를 거쳐" in w for w in out["warnings"])     # 연결 장부가가 합산보다 작다


def test_financial_holdco_still_financial(monkeypatch):
    """신한지주 등 — 같은 64992 지만 예수부채가 연결되면 금융업(배수 미제공)이고 일반 지주회사가 아니다."""
    out = _build(monkeypatch, induty="64992", mcap=10**13,
                 bs={"현금및현금성자산": 10**12, "예수부채": 10**14},
                 mark=_mark_out(10**12, 10**12, 0))
    d = out["data"]
    assert d["is_financial"] is True and d["is_holding_company"] is False
    assert d["nav"]["equity_nav_cov"] is None and d["nav"]["surplus_cov"] is None
    assert any("금융업" in w for w in out["warnings"])


def test_indirect_holdings_warning_only_when_material(monkeypatch):
    """영풍: 고려아연을 100% 자회사를 거쳐 들고 있어 합산엔 그 자회사 장부가(0.67조)로만 잡힌다 — 연결 관계기업 3.24조와 차이를 밝힌다.
    서희건설처럼 차이가 시총의 10% 미만이면 말하지 않는다."""
    yp = _build(monkeypatch, induty="24213", mcap=619_520_482_950,
                bs={"관계기업투자": 3_236 * 10**9, "기타포괄손익-공정가치측정 금융자산": 94 * 10**9},
                mark=_mark_out(714 * 10**9, 83 * 10**9, 815 * 10**9))
    assert any("자회사를 거쳐 든 지분" in w and "3.33조" in w for w in yp["warnings"])
    sh = _build(monkeypatch, induty="4112", mcap=430 * 10**9,
                bs={"관계기업투자": 57 * 10**9}, mark=_mark_out(3 * 10**9, 4 * 10**9, 22 * 10**9))
    assert not any("자회사를 거쳐" in w for w in sh["warnings"])


def test_no_holdings_list_falls_back_to_balance_sheet_book(monkeypatch):
    out = _build(monkeypatch, induty="467", mcap=10**12,
                 bs={"관계기업투자": 2 * 10**11, "기타포괄손익-공정가치측정금융자산": 10**11},
                 mark=_mark_out(0, 0, 0, n_listed=0, n_unlisted=0))
    nav = out["data"]["nav"]
    assert nav["equity_nav_basis"] == "balance_sheet" and nav["equity_nav_krw"] == 3 * 10**11


def test_build_passes_the_market_cap_date_and_period_end_and_warns(monkeypatch):
    """시총을 먼저 구해 그 거래일을, 보고서 기간 말을 보유수량 기준일로 시가마크에 넘기고, 미해결이 있으면 경고로 밝힌다."""
    seen = {}
    mark = _mark_out(1, 1, 0, price_date="20261015", qty_adjust_checked=False,
                     unresolved=[{"name": "삼성이앤에이㈜", "book_krw": 1, "reason": "r"}])
    mark.update(n_marked=0, n_unresolved=1)
    out = _build(monkeypatch, induty="467", mcap=46_461_011_956_500,
                 bs={"현금및현금성자산": 100}, mark=mark, seen=seen)
    assert seen == {"price_dd": "20261016", "qty_dd": "20251231"}
    w = " ".join(out["warnings"])
    assert "1종 중 1종은 시가를 못 구해 장부가로 두었다(삼성이앤에이㈜)" in w
    assert "시가 기준일(20261015)과 시총 기준일(20261016)이 다르다" in w
    assert "20251231 기준인데 그 뒤의 액면분할·병합·무상증자를 확인하지 못했다" in w


# ── 렌더 ──

def test_renderer_says_when_nothing_could_be_marked():
    from open_proxy_mcp.tools.asset_holdings import _render
    p = {"status": "ok", "subject": "X", "data": {"scope": "summary", "listed_stakes": {
        "n_listed": 2, "n_marked": 0, "n_unresolved": 2, "listed_book_krw": 5 * 10**11,
        "marked": [], "unresolved": [{"name": "갑", "book_krw": 4 * 10**11, "reason": "사유A"},
                                     {"name": "을", "book_krw": 10**11, "reason": "사유B"}]}}}
    text = _render(p)
    assert "상장 보유지분 2종 — 시가를 못 구해 **전부 장부가 유지**" in text
    assert "미해결 갑" in text and "사유A" in text


def test_renderer_states_the_basis_holdco_and_split():
    from open_proxy_mcp.tools.asset_holdings import _render
    p = {"status": "ok", "subject": "LG", "data": {
        "scope": "summary", "is_holding_company": True, "market_cap_krw": 15_603 * 10**9,
        "nav": {"surplus_krw": 2_594 * 10**9, "surplus_cov": None, "equity_nav_krw": 24_787 * 10**9,
                "equity_nav_cov": 1.59, "equity_nav_basis": "holdings"},
        "listed_stakes": {"n_listed": 7, "n_marked": 7, "n_unresolved": 0, "price_date": "20261016",
                          "unrealized_gap_krw": 16_575 * 10**9, "unlisted_majority_book_krw": 564 * 10**9,
                          "marked": [{"name": "엘에스일렉트릭(주)", "book_krw": 3_175 * 10**8, "mkt_krw": 5_473 * 10**8,
                                      "gap_krw": 2_298 * 10**8, "qty": 690_167, "qty_adj": 3_450_835}]}}}
    text = _render(p)
    assert "일반 지주회사라 배수 미제공" in text and "시총 초과" not in text
    assert "보유지분 합산 — 상장 시가·비상장 장부가" in text and "1.59배" in text
    assert "지분율 50% 초과 비상장 장부가" in text
    assert "보유 690,167주 → 3,450,835주" in text
