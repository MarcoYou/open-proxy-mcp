# -*- coding: utf-8 -*-
"""asset_holdings 상장 보유지분 시가마크 — 261018 운영에서 하나도 평가되지 않던 결함의 회귀. network 0.

운영 실측(261018): 삼성물산 12/12·SK 12/12·LG 7/7 이 전부 미해결(n_marked 0), 경고 0.
원인: 종목코드 사전을 `<레포>/configs/master.db` 라는 **박아 둔 경로**에서 읽었는데 운영 이미지엔 그 파일이 없고
기업 마스터는 볼륨(`OPM_MASTER_DB_PATH`)에 있다 → 빈 사전 → 시세를 묻기도 전에 전부 미해결.
고친 뒤: 서버의 기업 마스터 해석기로 코드를 찾고, 종가는 시총과 같은 거래일의 일별 시세 저장분에서 한 번에 읽는다.
"""
from __future__ import annotations

import asyncio

import pytest

from open_proxy_mcp.services import asset_holdings as A


def _h(name, book, qty, ta=0):
    return {"inv_prm": name, "trmend_blce_acntbk_amount": f"{book:,}", "trmend_blce_qy": f"{qty:,}",
            "recent_bsns_year_fnnr_sttus_tot_assets": f"{ta:,}" if ta else "-"}


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
       "처분한회사상장 비상장회사비상장")

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
    _h("비상장회사", 9_999_000_000, 1),                            # 원문이 비상장 — 후보 아님
]


@pytest.fixture
def db_rows(monkeypatch):
    """`_db_closes` 를 가짜로 — 호출 인자를 남기고, 저장분에 있는 종목만 돌려준다."""
    calls = []
    store = {"005930": 215_500, "032830": 238_500, "034730": 456_500}   # 003600 은 없다(상장폐지)

    def fake(price_dd, codes):
        calls.append((price_dd, sorted(codes)))
        return (price_dd or "20261016"), {c: store[c] for c in codes if c in store}
    monkeypatch.setattr(A, "_db_closes", fake)
    return calls


def test_marks_from_daily_store_at_the_market_cap_date(db_rows):
    cli = FakeClient(TABLE)
    out = asyncio.run(A._mark_listed_stakes(cli, HOLD, DOC, "20261016"))
    # 시총 기준일로 한 번만 읽는다 — 종목마다 묻지 않는다
    assert db_rows == [("20261016", ["003600", "005930", "032830", "034730"])]
    assert cli.price_calls == []
    by = {m["name"]: m for m in out["marked"]}
    assert set(by) == {"삼성전자㈜", "삼성생명보험㈜", "에스케이㈜"}
    assert by["삼성전자㈜"]["code"] == "005930" and by["삼성전자㈜"]["px"] == 215_500
    assert by["삼성전자㈜"]["mkt_krw"] == 298_818_100 * 215_500 and by["삼성전자㈜"]["px_date"] == "20261016"
    # 동명 후보 둘 중 그날 시세가 있는 쪽(현 SK)으로 가린다
    assert by["에스케이㈜"]["code"] == "034730"
    assert out["price_date"] == "20261016" and out["price_source"] == "krx_daily"


def test_unresolved_carry_name_book_and_reason_and_counts_add_up(db_rows):
    out = asyncio.run(A._mark_listed_stakes(FakeClient(TABLE), HOLD, DOC, "20261016"))
    un = {u["name"]: u for u in out["unresolved"]}
    assert set(un) == {"삼성전자우", "해외법인"}
    assert "추정" in un["삼성전자우"]["reason"]                      # 추정 일치는 다른 종목 시세로 평가하지 않는다
    assert "국내 상장 종목코드" in un["해외법인"]["reason"]
    assert un["해외법인"]["book_krw"] == 32_243_000_000
    # 처분 완료·비상장은 후보가 아니고, 개수와 합계가 같은 집합을 센다(종전: 상한 밖 후보가 개수에만 들었다)
    assert out["n_listed"] == 5 == out["n_marked"] + out["n_unresolved"]
    assert out["listed_book_krw"] == sum(_b for _b in (35_828_290_000_000, 6_097_200_000_000, 100_000_000_000,
                                                        3_920_000_000, 32_243_000_000))
    # 미해결은 장부가로 남는다
    mkt = sum(m["mkt_krw"] for m in out["marked"]) + 3_920_000_000 + 32_243_000_000
    assert out["listed_mkt_krw"] == mkt and out["unrealized_gap_krw"] == mkt - out["listed_book_krw"]


def test_no_hardcoded_master_db_path():
    """종전 결함의 뿌리 — 레포 안 파일 경로를 박아 열던 사전이 다시 생기지 않게."""
    assert not hasattr(A, "_MASTER") and not hasattr(A, "_listed_lookup")


def test_db_down_uses_shared_krx_snapshot_of_the_same_day(monkeypatch):
    monkeypatch.setattr(A, "_db_closes", lambda dd, codes: None)            # DB 장애
    seen = []

    async def snap(day):
        seen.append(day)
        return {"005930": {"TDD_CLSPRC": "215,500"}, "032830": {"TDD_CLSPRC": "238500"}}
    monkeypatch.setattr(A._val, "_krx_market_live", snap)
    cli = FakeClient(TABLE)
    out = asyncio.run(A._mark_listed_stakes(cli, HOLD, DOC, "20261016"))
    assert seen == ["20261016"] and cli.price_calls == []                    # 종목별 콜 0
    assert {m["code"] for m in out["marked"]} == {"005930", "032830"}
    assert out["price_source"] == "krx"
    sk = next(u for u in out["unresolved"] if u["name"] == "에스케이㈜")
    assert "20261016 거래일 시세 없음" in sk["reason"]


def test_db_and_krx_down_fall_back_per_stock_on_previous_trading_day(monkeypatch):
    """KRX 일별 시세는 다음 날 게시 — 오늘 날짜로 물으면 늘 빈 응답이었다(종전 결함). 직전 거래일로 묻는다."""
    monkeypatch.setattr(A, "_db_closes", lambda dd, codes: None)

    async def empty(day):
        return {}
    monkeypatch.setattr(A._val, "_krx_market_live", empty)
    monkeypatch.setattr(A, "_prev_trading_day", lambda: "20261016")
    monkeypatch.setattr(A, "_LIVE_PX_CAP", 2)
    cli = FakeClient(TABLE)
    out = asyncio.run(A._mark_listed_stakes(cli, HOLD, DOC, None))
    assert cli.price_calls == [("005930", "20261016"), ("032830", "20261016")]   # 장부가 큰 순, 상한 2
    sk = next(u for u in out["unresolved"] if u["name"] == "에스케이㈜")
    assert "상한" in sk["reason"]
    assert out["price_source"] == "portal" and out["n_marked"] == 2


def test_db_closes_reads_one_day_in_one_round_trip(monkeypatch):
    import open_proxy_mcp.db as db
    seen = []

    def rows(sql, params=()):
        seen.append((" ".join(sql.split()), params))
        return [("005930", 215500, "20261016")]
    monkeypatch.setattr(db, "pg_rows", rows)
    assert A._db_closes("20261016", ["005930", "000660"]) == ("20261016", {"005930": 215500})
    assert seen[-1] == ("SELECT ticker, close, trd_dd FROM krx_daily WHERE trd_dd = %s AND ticker = ANY(%s)",
                        ("20261016", ["005930", "000660"]))
    A._db_closes(None, ["005930"])
    assert "(SELECT max(trd_dd) FROM krx_daily)" in seen[-1][0] and seen[-1][1] == (["005930"],)
    monkeypatch.setattr(db, "pg_rows", lambda sql, params=(): None)           # DB 미설정·장애
    assert A._db_closes("20261016", ["005930"]) is None


def test_stake_codes_does_not_touch_the_request_ledger():
    """보유지분 이름 해석이 「이 요청이 조사한 기업」 장부·추정 경고에 섞이면 안 된다 —
    후보 목록 함수(`lookup_corp_code_all`)만 쓴다. 해석 실패는 응답을 깨지 않고 사유로 남는다."""
    class Boom:
        async def lookup_corp_code_all(self, name):
            raise RuntimeError("x")
    assert asyncio.run(A._stake_codes(Boom(), "삼성전자")) == ([], "종목코드 조회 실패")
    codes, why = asyncio.run(A._stake_codes(FakeClient(TABLE), "에스케이㈜"))
    assert codes == ["034730", "003600"] and why is None


def test_renderer_says_when_nothing_could_be_marked():
    from open_proxy_mcp.tools.asset_holdings import _render
    p = {"status": "ok", "subject": "X", "data": {"scope": "summary", "listed_stakes": {
        "n_listed": 2, "n_marked": 0, "n_unresolved": 2, "listed_book_krw": 5 * 10**11,
        "marked": [], "unresolved": [{"name": "갑", "book_krw": 4 * 10**11, "reason": "사유A"},
                                     {"name": "을", "book_krw": 10**11, "reason": "사유B"}]}}}
    text = _render(p)
    assert "상장 보유지분 2종 — 시가를 못 구해 **전부 장부가 유지**" in text
    assert "미해결 갑" in text and "사유A" in text


def test_build_passes_the_market_cap_date_and_warns(monkeypatch):
    """시총을 먼저 구해 그 거래일을 시가마크에 넘기고, 미해결이 있으면 경고로 밝힌다."""
    from types import SimpleNamespace
    from open_proxy_mcp.services.contracts import AnalysisStatus

    async def resolve(q):
        return SimpleNamespace(status=AnalysisStatus.EXACT, selected={"corp_code": "00149655", "stock_code": "028260",
                                                                      "corp_name": "삼성물산"}, candidates=[])

    async def pick(client, cc, report, year):
        return {"rcept_no": "20260312000856", "report_nm": "사업보고서 (2025.12)"}, "11011", ""

    async def getdoc(client, rno):
        return {"note_html": "", "full_text": ""}

    async def mcap(isu):
        return 46_461_011_956_500, {"shares": 162167581, "close": 286500, "date": "20261016"}

    seen = {}

    async def mark(client, holdings, doc_text, price_dd=None):
        seen["price_dd"] = price_dd
        return {"marked": [], "unresolved": [{"name": "삼성이앤에이㈜", "book_krw": 1, "reason": "r"}],
                "price_date": "20261015", "price_source": "krx_daily", "listed_book_krw": 1, "listed_mkt_krw": 1,
                "unrealized_gap_krw": 0, "n_listed": 1, "n_marked": 0, "n_unresolved": 1}

    class Cli:
        async def get_fnltt_singl_acnt_all(self, *a):
            return {"list": [{"sj_div": "BS", "account_nm": "현금및현금성자산", "thstrm_amount": "100"}]}

        async def get_company_info(self, cc):
            return {"induty_code": "467"}

        async def get_other_corp_investment(self, *a):
            return {"list": []}

    monkeypatch.setattr(A, "get_dart_client", lambda: Cli())
    monkeypatch.setattr(A, "resolve_company_query", resolve)
    monkeypatch.setattr(A, "_pick_periodic_report", pick)
    monkeypatch.setattr(A, "_safe_getdoc", getdoc)
    monkeypatch.setattr(A, "_market_cap", mcap)
    monkeypatch.setattr(A, "_mark_listed_stakes", mark)
    out = asyncio.run(A._build_asset_holdings_payload_impl("삼성물산"))
    assert seen["price_dd"] == "20261016"
    w = " ".join(out["warnings"])
    assert "1종 중 1종은 시가를 못 구해 장부가로 두었다(삼성이앤에이㈜)" in w
    assert "시가 기준일(20261015)과 시총 기준일(20261016)이 다르다" in w
