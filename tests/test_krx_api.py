"""KRX 호출 한 곳(`krx_api`) — 네트워크 0 · DB 0. `httpx.AsyncClient`·호출 장부를 가짜로 바꿔 상태 판정·호출 수·줄인 행을 검사한다.

261021(DATA-PX8): 서버 안 KRX 호출 네 곳(배수 라이브·시세 확인·갱신기·DART 클라이언트)이 같은 호출을 따로 적고 실패를 빈 목록으로 삼키던 것을
`krx_api.fetch_day` 하나로 모았다. 여기서는 그 상태(`ok`/`empty`/`auth`/`limit`/`http_error`/`transport`)와, 그것을 읽는 쪽(배수 라이브·
배치 예비 갱신·DART 클라이언트)이 실패를 「자료 없음」으로 쓰지 않는 것을 본다.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
from datetime import date
from unittest.mock import AsyncMock

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp import krx_api, krx_daily  # noqa: E402
from open_proxy_mcp.services import price_multiple_data as pmd  # noqa: E402

KEY = "unit-test-krx-key-0123456789"


def _row(code, close="71,000", **kw):
    """KRX 응답 한 행 — 실제 응답과 같은 15칸(SECT_TP_NM·ISU_NM 같은 안 쓰는 칸 포함)."""
    r = {"BAS_DD": "20261016", "ISU_CD": code, "ISU_NM": "이름" * 8, "MKT_NM": "KOSPI", "SECT_TP_NM": "섹터" * 10,
         "TDD_CLSPRC": close, "CMPPREVDD_PRC": "1,000", "FLUC_RT": "1.43", "TDD_OPNPRC": "70,500", "TDD_HGPRC": "71,500",
         "TDD_LWPRC": "70,000", "ACC_TRDVOL": "12,345,678", "ACC_TRDVAL": "876,543,210,000", "MKTCAP": "420,294,092,462,000",
         "LIST_SHRS": "5,919,637,922"}
    r.update(kw)
    return r


def _rows(n, prefix="K", **kw):
    return [_row(f"{prefix}{i:05d}", **kw) for i in range(n)]


class Resp:
    def __init__(self, payload=None, status=200, bad_json=False):
        self.payload, self.status_code, self.bad_json = payload, status, bad_json

    def json(self):
        if self.bad_json:
            raise ValueError("not json")
        return self.payload


@pytest.fixture
def fake(monkeypatch):
    """KRX 호출을 세는 가짜. routes[endpoint] = Resp 또는 예외 — endpoint 는 stk_bydd_trd / ksq_bydd_trd. 장부(bump)도 센다."""
    import httpx
    from open_proxy_mcp.dart import krx_meter
    log, bumps, routes = [], [], {}

    class Client:
        def __init__(self, **kw):
            self.kw = kw

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, params=None, **kw):
            ep = url.rsplit("/", 1)[-1]
            log.append((ep, params["basDd"], dict(headers or {}), kw))
            r = routes[ep]
            if isinstance(r, Exception):
                raise r
            return r
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    monkeypatch.setattr(krx_meter, "bump", lambda n=1: bumps.append(n))
    monkeypatch.setenv("KRX_API_KEY", KEY)
    monkeypatch.delenv("KRX_OPEN_API_KEY", raising=False)
    pmd._KRX_CACHE.clear()
    yield log, bumps, routes
    pmd._KRX_CACHE.clear()


def _ok(n_k=3, n_q=4):
    return {"stk_bydd_trd": Resp({"OutBlock_1": _rows(n_k, "K")}), "ksq_bydd_trd": Resp({"OutBlock_1": _rows(n_q, "Q", MKT_NM="KOSDAQ")})}


def run(coro):
    return asyncio.run(coro)


# ── 상태 판정 ─────────────────────────────────────────────────────────────────

def test_both_markets_ok_costs_two_calls_and_two_ledger_entries(fake):
    log, bumps, routes = fake
    routes.update(_ok())
    kospi, kosdaq, calls, status = run(krx_api.fetch_day("20261016"))
    assert (len(kospi), len(kosdaq), calls, status) == (3, 4, 2, "ok")
    assert [x[0] for x in log] == ["stk_bydd_trd", "ksq_bydd_trd"] and len(bumps) == 2
    assert all(x[2] == {"AUTH_KEY": KEY} for x in log)                           # 키는 헤더로만


def test_empty_is_the_only_answer_that_means_no_data(fake):
    log, bumps, routes = fake
    routes.update({"stk_bydd_trd": Resp({"OutBlock_1": []}), "ksq_bydd_trd": Resp({"OutBlock_1": _rows(2, "Q")})})
    assert run(krx_api.fetch_day("20261016")) == ([], [], 1, "empty")           # 코스피가 비면 코스닥은 묻지 않는다
    assert len(log) == 1
    both = run(krx_api.fetch_day("20261016", both=True))                              # 두 시장이 다 필요한 호출은 코스피가 비어도 코스닥을 묻는다
    assert (both.kospi, len(both.kosdaq), both.calls, both.status) == ([], 2, 2, "ok")
    routes.update({"stk_bydd_trd": Resp({"OutBlock_1": []}), "ksq_bydd_trd": Resp({"OutBlock_1": []})})
    assert run(krx_api.fetch_day("20261016", both=True)) == ([], [], 2, "empty")  # 두 시장 모두 비면 비었다


@pytest.mark.parametrize("resp,status", [
    (Resp({}, status=401), "auth"), (Resp({}, status=403), "auth"), (Resp({}, status=429), "limit"),
    (Resp({}, status=500), "http_error"), (Resp({}, status=503), "http_error"), (Resp({}, status=404), "http_error"),
    (Resp(bad_json=True), "http_error"), (Resp(["x"]), "http_error"),
    (Resp({"respCode": "401", "respMsg": "Unauthorized Key"}), "auth"),
    (Resp({"respCode": "403"}), "auth"), (Resp({"respCode": "429"}), "limit"), (Resp({"respCode": "500"}), "http_error"),
    (Resp({"OutBlock_1": None}), "empty"),                                          # 목록도 오류 코드도 없는 200 은 종전처럼 빈 응답
])
def test_failures_are_named_and_carry_no_rows(fake, resp, status):
    log, bumps, routes = fake
    routes.update({"stk_bydd_trd": resp, "ksq_bydd_trd": Resp({"OutBlock_1": _rows(2, "Q")})})
    kospi, kosdaq, calls, got = run(krx_api.fetch_day("20261016"))
    assert got == status and kospi == [] and kosdaq == []
    assert calls == 1 and len(bumps) == 1                                           # 실패한 호출도 한 번 센다(KRX 는 요청을 받았다)


def test_transport_error_is_a_status_not_an_exception(fake):
    log, bumps, routes = fake
    routes.update({"stk_bydd_trd": OSError("timeout"), "ksq_bydd_trd": Resp({"OutBlock_1": []})})
    assert run(krx_api.fetch_day("20261016")) == ([], [], 1, "transport")


def test_a_failed_kospi_never_leads_to_a_kosdaq_call_unless_both_markets_are_wanted(fake):
    log, bumps, routes = fake
    for code, status in ((401, "auth"), (429, "limit"), (503, "http_error")):
        log.clear()
        routes.update({"stk_bydd_trd": Resp({}, status=code), "ksq_bydd_trd": Resp({"OutBlock_1": _rows(2, "Q")})})
        res = run(krx_api.fetch_day("20261016"))
        assert (res.calls, res.status, len(log)) == (1, status, 1)                  # 실패했는데 같은 키로 코스닥을 더 묻지 않는다
    log.clear()
    res = run(krx_api.fetch_day("20261016", both=True))                              # 두 시장이 다 필요하면 받은 행은 돌려준다(상태는 실패)
    assert (res.calls, res.status, len(res.kosdaq)) == (2, "http_error", 2)


def test_both_markets_are_requested_concurrently(monkeypatch):
    """두 시장이 다 필요한 호출(배수 라이브·시세 확인·휴장 판정)은 종전처럼 병렬이다 — 코스닥 요청이 코스피 응답을 기다리지 않는다."""
    import httpx
    from open_proxy_mcp.dart import krx_meter
    events = []

    class Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, params=None, **kw):
            ep = url.rsplit("/", 1)[-1]
            events.append(("start", ep))
            await asyncio.sleep(0.05)
            events.append(("end", ep))
            return Resp({"OutBlock_1": _rows(2, "K" if ep.startswith("stk") else "Q")})
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    monkeypatch.setattr(krx_meter, "bump", lambda n=1: None)
    monkeypatch.setenv("KRX_API_KEY", KEY)
    assert run(krx_api.fetch_day("20261016", both=True)).calls == 2
    assert [e[0] for e in events[:2]] == ["start", "start"]                           # 둘 다 시작한 뒤에야 끝난다
    events.clear()
    assert run(krx_api.fetch_day("20261016")).calls == 2                              # 기본은 코스피 먼저 — 코스닥은 그 응답을 본 뒤
    assert events[0] == ("start", "stk_bydd_trd") and events[1] == ("end", "stk_bydd_trd")


def test_a_failed_kosdaq_after_a_good_kospi_is_reported_as_the_failure_with_the_rows_kept(fake):
    log, bumps, routes = fake
    routes.update({"stk_bydd_trd": Resp({"OutBlock_1": _rows(5, "K")}), "ksq_bydd_trd": OSError("reset")})
    kospi, kosdaq, calls, status = run(krx_api.fetch_day("20261016"))
    assert (len(kospi), kosdaq, calls, status) == (5, [], 2, "transport")           # 호출자는 상태를 보고 반쪽을 쓰지 않는다


def test_no_key_is_auth_with_zero_calls_and_one_warning(fake, monkeypatch, caplog):
    log, bumps, routes = fake
    monkeypatch.delenv("KRX_API_KEY")
    krx_api._NO_KEY_WARNED["done"] = False
    with caplog.at_level("DEBUG", logger="open_proxy_mcp.krx_api"):
        assert run(krx_api.fetch_day("20261016")) == ([], [], 0, "auth")
        assert run(krx_api.fetch_day("20261016")) == ([], [], 0, "auth")
    assert log == [] and bumps == []
    assert sum(1 for r in caplog.records if r.levelname == "WARNING") == 1           # 프로세스당 한 번만 경고
    monkeypatch.setenv("KRX_OPEN_API_KEY", KEY)                                      # 다른 이름의 키도 읽는다
    assert krx_api.api_key() == KEY


def test_the_ticker_option_skips_kosdaq_when_the_stock_is_on_kospi(fake):
    log, bumps, routes = fake
    routes.update(_ok(3, 3))
    res = run(krx_api.fetch_day("20261016", ticker="K00001"))
    assert (res.calls, res.status, res.kosdaq) == (1, "ok", []) and len(log) == 1
    log.clear()
    res = run(krx_api.fetch_day("20261016", ticker="Q00001"))                       # 코스피에 없으면 코스닥까지
    assert res.calls == 2 and any(r["ISU_CD"] == "Q00001" for r in res.kosdaq)


def test_a_given_client_is_used_with_an_explicit_timeout(fake):
    log, bumps, routes = fake
    calls = []

    class Http:
        async def get(self, url, headers=None, params=None, **kw):
            calls.append(kw)
            return Resp({"OutBlock_1": _rows(2, "K")})
    res = run(krx_api.fetch_day("20261016", ticker="K00000", http=Http()))
    assert res.status == "ok" and calls == [{"timeout": krx_api.TIMEOUT_SEC}] and log == []   # 새 클라이언트를 만들지 않았다


def test_neither_the_key_nor_the_body_reaches_the_log(fake, caplog):
    log, bumps, routes = fake
    routes.update({"stk_bydd_trd": Resp({"respCode": "401", "respMsg": "SECRET-BODY-TEXT"}, status=401), "ksq_bydd_trd": Resp({})})
    with caplog.at_level("DEBUG"):
        run(krx_api.fetch_day("20261016"))
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "401" in text and KEY not in text and "SECRET-BODY-TEXT" not in text


# ── 줄인 행 ───────────────────────────────────────────────────────────────────

def test_rows_are_reduced_to_the_known_columns_and_tagged_by_endpoint(fake):
    log, bumps, routes = fake
    routes.update({"stk_bydd_trd": Resp({"OutBlock_1": [_row("005930")]}),
                   "ksq_bydd_trd": Resp({"OutBlock_1": [_row("247540", MKT_NM="KOSDAQ GLOBAL")]})})
    kospi, kosdaq, _, _ = run(krx_api.fetch_day("20261016"))
    assert set(kospi[0]) == set(krx_api.ROW_KEYS) and "ISU_NM" not in kospi[0] and "SECT_TP_NM" not in kospi[0]
    assert kospi[0]["_MKT"] == "KS" and kosdaq[0]["_MKT"] == "KQ"                    # 시장은 KRX 의 MKT_NM 이 아니라 호출한 endpoint 기준
    assert kosdaq[0]["MKT_NM"] == "KOSDAQ GLOBAL" and kospi[0]["TDD_CLSPRC"] == "71,000" and kospi[0]["BAS_DD"] == "20261016"
    # 일별 기록이 읽는 칸은 줄인 행에 모두 있다 — 줄여도 기록되는 값이 같다
    assert krx_daily.daily_records("20261016", kospi, kosdaq) == krx_daily.daily_records("20261016", [_row("005930")],
                                                                                         [_row("247540", MKT_NM="KOSDAQ GLOBAL")])


def test_reduced_row_columns_cover_every_reader():
    """줄인 행 칸의 합집합 — 일별 기록·시세 확인·배수·DART 클라이언트가 읽는 칸이 하나도 빠지지 않는다."""
    from open_proxy_mcp.services import trading
    need = {"ISU_CD", "BAS_DD", "TDD_CLSPRC", "TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "CMPPREVDD_PRC", "ACC_TRDVOL", "ACC_TRDVAL",
            "LIST_SHRS", "MKTCAP", "MKT_NM", "FLUC_RT"}
    assert need <= set(krx_api.KEEP_KEYS) and set(trading._QUOTE_KEEP_KEYS) == set(krx_api.ROW_KEYS)
    assert {src for src, _, _ in trading._QUOTE_FIELDS} <= set(krx_api.ROW_KEYS)


def test_index_and_split_by_market_tag():
    k = [krx_api.reduce_row(_row("005930"), "KS"), krx_api.reduce_row(_row("000660"), "KS")]
    q = [krx_api.reduce_row(_row("247540"), "KQ"), krx_api.reduce_row(_row("005930", close="1"), "KQ")]    # 같은 코드가 두 번 — 마지막이 이긴다
    day = krx_api.index_rows(k, q, [{"TDD_CLSPRC": "1"}])                                                # 종목코드 없는 행은 버린다
    assert sorted(day) == ["000660", "005930", "247540"] and day["005930"]["TDD_CLSPRC"] == "1"
    ks, kq = krx_api.split_markets(day)
    assert sorted(r["ISU_CD"] for r in ks) == ["000660"] and sorted(r["ISU_CD"] for r in kq) == ["005930", "247540"]
    assert krx_api.to_int("1,234") == 1234 and krx_api.to_int("-") is None and krx_api.to_int("") is None and krx_api.to_int("x") is None


# ── 배수 도구의 라이브 보조: 실패를 「자료 없음」으로 삼키지 않는다 ───────────────────────────

def test_live_day_carries_the_status_and_caches_only_complete_days(fake):
    log, bumps, routes = fake
    routes.update(_ok(3, 4))
    day = run(pmd._krx_market_live("20261016"))
    assert isinstance(day, pmd.LiveDay) and day.status == "ok" and len(day) == 7
    assert list(pmd._KRX_CACHE._entries) == ["20261016"]                              # 날짜마다 줄인 행 하나 — 「:split」 두 번째 항목이 없다
    n = len(log)
    assert run(pmd._krx_market_live("20261016")) is day and len(log) == n             # 두 번째는 0콜


@pytest.mark.parametrize("routes_,status", [
    ({"stk_bydd_trd": Resp({}, status=429), "ksq_bydd_trd": Resp({})}, "limit"),
    ({"stk_bydd_trd": OSError("x"), "ksq_bydd_trd": Resp({})}, "transport"),
    ({"stk_bydd_trd": Resp({"OutBlock_1": []}), "ksq_bydd_trd": Resp({"OutBlock_1": []})}, "empty"),
])
def test_failed_or_empty_days_are_not_cached(fake, routes_, status):
    log, bumps, routes = fake
    routes.update(routes_)
    day = run(pmd._krx_market_live("20261016"))
    assert day == {} and day.status == status and not pmd._KRX_CACHE._entries
    routes.update(_ok())                                                              # 복구 — 굳은 빈 값 없이 곧바로 받는다
    assert run(pmd._krx_market_live("20261016")).status == "ok"


def test_a_half_day_is_served_once_but_not_cached(fake):
    """종전: 한 시장이 비어 온 반쪽도 48시간 캐시에 담겨 그 시장 종목이 이틀 동안 없는 것으로 서빙됐다."""
    log, bumps, routes = fake
    routes.update({"stk_bydd_trd": Resp({"OutBlock_1": _rows(3, "K")}), "ksq_bydd_trd": Resp({"OutBlock_1": []})})
    day = run(pmd._krx_market_live("20261016"))
    assert len(day) == 3 and not pmd._KRX_CACHE._entries
    routes.update(_ok(3, 4))
    assert len(run(pmd._krx_market_live("20261016"))) == 7


def test_latest_snapshot_stops_at_a_failed_day_instead_of_serving_an_older_one(fake, monkeypatch):
    """종전: 최신 거래일 호출이 실패하면 빈 사전이라 「그 날은 자료 없음」으로 넘어가 며칠 전 날짜를 최신으로 내줬다."""
    log, bumps, routes = fake
    monkeypatch.setattr(pmd, "_price_dates", lambda today=None: ["20261016", "20261015"])
    routes.update({"stk_bydd_trd": Resp({}, status=429), "ksq_bydd_trd": Resp({})})
    assert run(pmd._fetch_live_snapshot()) == (None, {}) and {x[1] for x in log} == {"20261016"}   # 옛 날짜를 묻지도 않는다
    # 대조군 — 비어 온 날(휴장·게시 전)은 종전처럼 직전 거래일로 넘어간다
    log.clear()
    seen = {"n": 0}

    class Seq(dict):
        pass

    async def live(dd):
        seen["n"] += 1
        return pmd.LiveDay({} if dd == "20261016" else {"A": _row("A")}, "empty" if dd == "20261016" else "ok")
    monkeypatch.setattr(pmd, "_krx_market_live", live)
    dd, snap = run(pmd._fetch_live_snapshot())
    assert dd == "20261015" and "A" in snap and seen["n"] == 2


def test_market_for_does_not_fall_back_to_an_older_day_when_the_call_failed(fake, monkeypatch, caplog):
    log, bumps, routes = fake
    pmd._serving_dd_invalidate()
    monkeypatch.setattr(pmd, "_krx_daily_latest_dd", lambda: None)                  # 저장분을 못 읽는 서버 → 라이브로
    monkeypatch.setattr(pmd, "_price_dates", lambda today=None: ["20261016", "20261015"])
    routes.update({"stk_bydd_trd": Resp({}, status=401), "ksq_bydd_trd": Resp({})})
    with caplog.at_level("WARNING"):
        assert run(pmd._market_for("K00001")) == {}
    assert {x[1] for x in log} == {"20261016"} and any("호출 실패" in r.getMessage() for r in caplog.records)
    # 복구되면 곧바로 값을 낸다 — 위 실패가 캐시에 굳지 않았다
    routes.update(_ok(3, 3))
    row = run(pmd._market_for("K00001"))
    assert row["price"] == 71_000 and row["date"] == "20261016" and row["source"] == "krx_live"
    pmd._serving_dd_invalidate()


def test_live_split_is_derived_from_the_market_tags(fake):
    from open_proxy_mcp.services import trading
    log, bumps, routes = fake
    routes.update(_ok(3, 4))
    pmd._KRX_CACHE.clear()
    monkey_dates = ["20261016"]
    import unittest.mock as um
    with um.patch.object(pmd, "_price_dates", lambda today=None: monkey_dates):
        dd, snap, split = run(trading._live_split())
    assert dd == "20261016" and len(snap) == 7
    assert sorted(split) == ["KOSDAQ", "KOSPI"] and len(split["KOSPI"]) == 3 and len(split["KOSDAQ"]) == 4
    assert all(r["_MKT"] == "KS" for r in split["KOSPI"]) and all(r["_MKT"] == "KQ" for r in split["KOSDAQ"])


# ── 배치 예비 갱신(krx_daily.ensure_fresh) ──────────────────────────────────────────

@pytest.fixture
def fresh_env(monkeypatch):
    """DB 는 읽기 대역, 쓰기 함수는 기록만. 하루 1회 래치를 비운다."""
    krx_daily.FRESH_STATE.clear()
    writes, syncs = [], []
    monkeypatch.setattr(krx_daily, "upsert_day", lambda dd, k, q: writes.append((dd, len(k), len(q))) or krx_daily.KrxWrite(True, True, ()))
    monkeypatch.setattr(krx_daily, "weekly_sync", lambda: syncs.append(1) or True)
    yield writes, syncs
    krx_daily.FRESH_STATE.clear()


def _read(latest):
    return lambda sql, params=(): None if latest is False else ([(latest,)] if latest else [])


def test_ensure_fresh_makes_no_krx_call_when_the_store_is_current_and_only_closes_weeks(fresh_env):
    writes, syncs = fresh_env

    async def no_krx(*a, **k):
        raise AssertionError("저장분이 최신이면 KRX 를 부르지 않는다")
    got = run(krx_daily.ensure_fresh(today=date(2026, 10, 17), read_rows=_read("20261016"), fetch_day=no_krx))
    assert got == "20261016" and writes == [] and syncs == [1] and krx_daily.FRESH_STATE == {"day": "20261017", "latest_dd": "20261016"}
    assert run(krx_daily.ensure_fresh(today=date(2026, 10, 17), read_rows=_read("20261016"), fetch_day=no_krx)) == "20261016"   # 래치 — 두 번째는 DB 도 안 읽는다
    assert syncs == [1]


def test_ensure_fresh_writes_the_newest_day_krx_has_and_never_rolls_back(fresh_env):
    writes, syncs = fresh_env
    asked = []

    async def fetch(dd, both=False, **k):
        asked.append((dd, both))
        if dd >= "20261016":                                                         # 오늘(월)·금요일 — 게시 전이라 빈 응답
            return krx_api.DayFetch([], [], 1, "empty")
        return krx_api.DayFetch(_rows(3, "K"), _rows(3, "Q"), 2, "ok")
    got = run(krx_daily.ensure_fresh(today=date(2026, 10, 19), read_rows=_read("20261014"), fetch_day=fetch))   # 월요일, 저장분은 수요일까지
    assert asked == [("20261019", False), ("20261016", False), ("20261015", False)]       # 오늘부터 거슬러 올라가 처음 행이 있는 날
    assert writes == [("20261015", 3, 3)] and got == "20261015"
    krx_daily.FRESH_STATE.clear()
    # 저장된 날보다 앞선 날만 보이면(KRX 가 저장된 날을 일시 소실) 되돌려 쓰지 않는다
    writes.clear()

    async def stale(dd, both=False, **k):
        return krx_api.DayFetch(_rows(3, "K"), _rows(3, "Q"), 2, "ok") if dd == "20261014" else krx_api.DayFetch([], [], 1, "empty")
    assert run(krx_daily.ensure_fresh(today=date(2026, 10, 19), read_rows=_read("20261015"), fetch_day=stale)) == "20261015" and writes == []


@pytest.mark.parametrize("status", ["auth", "limit", "http_error", "transport"])
def test_ensure_fresh_does_not_read_a_krx_failure_as_nothing_published(fresh_env, status, caplog):
    """종전: 최신 거래일 확인이 실패하면 빈 스냅샷과 같아 하루 래치가 굳었다. 이제 경고를 남기고 저장된 최신일을 쓰되 래치는 굳히지 않는다."""
    writes, syncs = fresh_env
    asked = []

    async def fetch(dd, both=False, **k):
        asked.append(dd)
        return krx_api.DayFetch([], [], 1, status)
    with caplog.at_level("WARNING"):
        got = run(krx_daily.ensure_fresh(today=date(2026, 10, 19), read_rows=_read("20261014"), fetch_day=fetch))
    assert got == "20261014" and writes == [] and asked == ["20261019"]               # 실패한 날에서 멈춘다(더 옛날로 소급하지 않는다)
    assert krx_daily.FRESH_STATE == {}                                                 # 실패한 날의 래치를 굳히지 않는다 — 다음 호출이 다시 확인한다
    assert any(krx_api.STATUS_LABEL[status] in r.getMessage() for r in caplog.records)


def test_ensure_fresh_does_not_write_a_half_snapshot(fresh_env, caplog):
    writes, syncs = fresh_env

    async def fetch(dd, both=False, **k):
        return krx_api.DayFetch(_rows(3, "K"), [], 2, "ok")                         # 코스닥 0행이 정상 응답으로 온 반쪽
    with caplog.at_level("WARNING"):
        got = run(krx_daily.ensure_fresh(today=date(2026, 10, 19), read_rows=_read("20261014"), fetch_day=fetch))
    assert writes == [] and got == "20261014" and any("반쪽" in r.getMessage() for r in caplog.records)


def test_pmd_wrapper_passes_todays_date_and_its_own_db_reader(monkeypatch):
    seen = {}

    async def fake(*, today, read_rows, fetch_day=None):
        seen.update(today=today, read_rows=read_rows)
        return "20261016"
    monkeypatch.setattr(krx_daily, "ensure_fresh", fake)
    monkeypatch.setattr(pmd, "today_kst", lambda: date(2026, 10, 17))
    assert run(pmd._ensure_krx_fresh()) == "20261016"
    assert seen == {"today": date(2026, 10, 17), "read_rows": pmd._pg_rows}


# ── 옛 이름 재수출 · 달력 ──────────────────────────────────────────────────────────

def test_old_names_are_the_same_objects_as_the_new_homes():
    assert pmd.KrxWrite is krx_daily.KrxWrite and pmd._krx_db_upsert is krx_daily.upsert_day and pmd._krx_weekly_sync is krx_daily.weekly_sync
    assert pmd._KRX_STATE is krx_daily.FRESH_STATE and pmd._KRX_URL == krx_api.KOSPI_URL and pmd._KSQ_URL == krx_api.KOSDAQ_URL
    assert pmd._num is krx_api.to_int and krx_daily._num is krx_api.to_int
    assert pmd.serving_dd_invalidate is pmd._serving_dd_invalidate
    assert krx_daily.KrxWrite(True, False, ())._fields == ("daily_ok", "weekly_ok", "closed")


def test_price_dates_is_weekdays_only_newest_first_and_the_old_name_wraps_it(monkeypatch):
    assert krx_daily.price_dates(date(2026, 10, 19)) == ["20261019", "20261016", "20261015", "20261014", "20261013", "20261012", "20261009", "20261008"]
    assert krx_daily.price_dates(date(2026, 10, 18))[0] == "20261016"                  # 일요일 → 직전 금요일부터
    monkeypatch.setattr(pmd, "today_kst", lambda: date(2026, 10, 19))
    assert pmd._price_dates() == krx_daily.price_dates(date(2026, 10, 19)) and pmd._price_dates(date(2026, 10, 14))[0] == "20261014"


# ── DART 클라이언트의 종목 시세 ───────────────────────────────────────────────────────

def _client(responses):
    from open_proxy_mcp.dart.client import DartClient
    c = DartClient.__new__(DartClient)
    c._throttle_api = AsyncMock()
    c._http = type("Http", (), {"get": AsyncMock(side_effect=responses)})()
    return c


def test_client_reads_kosdaq_stocks_and_matches_the_code_exactly(fake, monkeypatch):
    log, bumps, routes = fake
    kospi = Resp({"OutBlock_1": [_row("005930", close="71,000"), _row("0059300", close="9")]})
    kosdaq = Resp({"OutBlock_1": [_row("247540", close="250,000", MKT_NM="KOSDAQ")]})
    c = _client([kospi, kosdaq, kospi, kosdaq])
    assert run(c._krx_stock_price("247540", "20261016")) == {"closing_price": 250000, "base_date": "20261016", "source": "krx"}
    assert c._http.get.await_count == 2                                               # 코스피에 없으니 코스닥까지
    # 코스피 종목은 1콜 — 코스닥을 부르지 않는다. 부분 일치(5930 이 005930 에 걸리는 것)는 더 이상 없다
    c = _client([kospi, kosdaq])
    assert run(c._krx_stock_price("005930", "20261016"))["closing_price"] == 71000 and c._http.get.await_count == 1
    c = _client([kospi, kosdaq])
    assert run(c._krx_stock_price("5930", "20261016")) is None
    assert bumps == [1] * 5                                                           # 호출 장부는 krx_api 가 세고 클라이언트가 따로 세지 않는다
    assert c._throttle_api.await_count == 0                                           # DART 분당 창을 KRX 호출이 쓰지 않는다


def test_client_returns_none_on_krx_failure_so_the_portal_can_take_over(fake, caplog):
    log, bumps, routes = fake
    c = _client([Resp({}, status=429), Resp({})])
    with caplog.at_level("WARNING"):
        assert run(c._krx_stock_price("005930", "20261016")) is None
    assert c._http.get.await_count == 1 and any("429" in r.getMessage() for r in caplog.records)
    krx_api._NO_KEY_WARNED["done"] = False


def test_client_without_a_key_does_not_call_or_warn(fake, monkeypatch, caplog):
    log, bumps, routes = fake
    monkeypatch.delenv("KRX_API_KEY")
    c = _client([])
    with caplog.at_level("WARNING"):
        assert run(c._krx_stock_price("005930", "20261016")) is None
    assert c._http.get.await_count == 0 and bumps == [] and not caplog.records


def test_client_strict_as_of_still_checks_the_reported_day(fake):
    from open_proxy_mcp.dart import as_of
    kospi = Resp({"OutBlock_1": [_row("005930", close="9", BAS_DD="20250326"), _row("005930", close="8", BAS_DD=None),
                                 _row("005930", close="7", BAS_DD="20250325")]})
    tokens = as_of.set_strict_as_of("2025-03-26T09:00:00+09:00")
    try:
        c = _client([kospi, kospi])
        assert run(c._krx_stock_price("005930", "20250325")) == {"closing_price": 7, "base_date": "20250325", "source": "krx"}
        assert {x["reason"] for x in as_of.strict_exclusions()} == {"publication_date_conflict", "publication_unknown"}
    finally:
        as_of.reset_strict_as_of(tokens)
