"""회사 명부(DART corpCode) 신선도 — 신규상장사를 「없다」고 답하지 않는다. 네트워크 0.

261018 실측: 운영 두 머신 모두 명부 기준일 20261008·119,601사였다. 만료가 sqlite 층(7일)에만 있고 프로세스가 뜰 때 한 번만
봤기 때문이다 — 10/15 상장 멜콘(179880)이 price_multiple_data·trading_data 에서 not_found. 새로 받은 명부(119,664사)에는
멜콘 행의 종목코드가 179880 으로 채워져 있었다.

이제 ① 하루 한 번(백그라운드) ② 일별 시세에 있는 보통주 코드가 명부에 없을 때 ③ 해석이 비었는데 명부가 1시간보다 묵었을 때
새로 받는다. 받기는 프로세스에 하나만 돌고, 실패하거나 반쪽이면 옛 명부를 그대로 쓴다.
"""
from __future__ import annotations

import asyncio
import io
import sqlite3
import zipfile
from datetime import datetime, timedelta

import pytest

from open_proxy_mcp import company_resolver as R
from open_proxy_mcp.dart import client as C
from open_proxy_mcp.services import corp_registry_refresher as REF


def _corp(code: str, name: str, stock: str = "", eng: str = "") -> dict:
    return {"corp_code": code, "corp_name": name, "corp_eng_name": eng or name,
            "stock_code": stock, "modify_date": "20261001"}


#: 운영 명부의 축소판 — 멜콘은 상장 전이라 종목코드가 비어 있다(실제 10/12 명부와 같은 모양).
OLD = [_corp("00126380", "삼성전자", "005930", "SAMSUNG ELECTRONICS"),
       _corp("00164779", "에스케이하이닉스", "000660", "SK hynix"),
       _corp("01965324", "삼성에피스홀딩스", "0126Z0"),
       _corp("01028933", "멜콘", "")]
#: 새로 받은 명부 — 멜콘에 종목코드가 붙었다(10/15 상장).
NEW = [*OLD[:3], {**_corp("01028933", "멜콘", "179880"), "modify_date": "20261015"}]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """모듈 상태를 비우고, 네트워크·운영 DB 로 새는 길을 막는다. 받기는 `downloads` 목록이 대신한다."""
    monkeypatch.setattr(C, "_MASTER_DB_PATH", tmp_path / "master.db")
    monkeypatch.setattr(C, "_corp_code_cache", None)
    monkeypatch.setattr(C, "_corp_code_lock", None)
    monkeypatch.setattr(C, "_corp_refresh_task", None)
    monkeypatch.setattr(C, "_corp_ticker_cache", None)
    monkeypatch.setattr(C, "_corp_registry_state", {
        "fetched_at": None, "source": None, "last_attempt_mono": None, "last_attempt_at": None,
        "last_reason": None, "last_outcome": None, "last_error": None, "last_added": None,
        "attempts": 0, "refreshed": 0, "failed": 0, "rejected": 0, "krx_baseline": None})
    monkeypatch.setattr(C, "_registry_meta", {})
    monkeypatch.setenv("OPM_CORP_REFRESHER", "1")
    monkeypatch.delenv("OPENDART_API_KEY", raising=False)      # 받기는 이 클라이언트로(서버 키 클라이언트를 만들지 않는다)
    market = {"ctx": R.MarketContext({})}

    async def fake_market():
        return market["ctx"]
    monkeypatch.setattr(R, "load_market_context", fake_market)

    async def no_filers(self):
        return frozenset()
    monkeypatch.setattr(C.DartClient, "periodic_filers", no_filers)

    state = {"downloads": [], "result": NEW, "delay": 0.0}

    async def fake_download(self):
        state["downloads"].append(1)
        if state["delay"]:
            await asyncio.sleep(state["delay"])
        res = state["result"]
        if isinstance(res, Exception):
            raise res
        return list(res)
    monkeypatch.setattr(C.DartClient, "_download_corp_codes", fake_download)
    state["market"] = market
    return state


def _install_old(age: timedelta, corps=OLD) -> None:
    C._install_corp_codes(list(corps), source="sqlite", fetched_at=datetime.now() - age)


def _client() -> C.DartClient:
    return C.DartClient(api_keys=["test-key"])


# ── 「명부가 낡아서 못 찾았을 수 있나」 판정 ─────────────────────────────

def test_miss_kind_counts_only_failures_a_fresh_registry_could_fix():
    assert C.corp_registry_miss_kind("179880", [], OLD) == "ticker"            # 보통주 코드, 명부에 없음
    assert C.corp_registry_miss_kind(" 179880 ", [], OLD) == "ticker"
    assert C.corp_registry_miss_kind("005935", [], OLD) is None                # 우선주 — 원래 DART 명부에 없다
    assert C.corp_registry_miss_kind("0126z0", [], OLD) is None                # 명부엔 있다 — 해석 문제지 낡은 게 아니다
    assert C.corp_registry_miss_kind("005930", [{"stock_code": "005930"}], OLD) is None
    assert C.corp_registry_miss_kind("멜콘", [], OLD) == "name"
    assert C.corp_registry_miss_kind("01028933", [], OLD) == "corp_code"
    assert C.corp_registry_miss_kind("삼성", [{"stock_code": "005930"}], OLD) is None   # 약한 추정이라도 결과가 있으면 실패 아님
    assert C.corp_registry_miss_kind("", [], OLD) is None


# ── corpCode ZIP 해석 ───────────────────────────────────────────────────

def _zip(xml: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("CORPCODE.xml", xml.encode("utf-8"))
    return buf.getvalue()


def test_chunked_parser_reads_every_row_like_the_whole_document_parser(monkeypatch):
    rows = "".join(
        f"<list><corp_code>{i:08d}</corp_code><corp_name>회사{i}</corp_name><corp_eng_name>Co {i}</corp_eng_name>"
        f"<stock_code>{'%06d' % i if i % 3 == 0 else ' '}</stock_code><modify_date>20261015</modify_date></list>"
        for i in range(2_000))
    xml = f'<?xml version="1.0" encoding="UTF-8"?><result>{rows}</result>'
    monkeypatch.setattr(C, "_CORP_XML_CHUNK", 1024)              # 조각 경계가 태그 한가운데에 여러 번 걸리게
    got = C._parse_corp_zip(_zip(xml))
    assert len(got) == 2_000
    assert got[3] == {"corp_code": "00000003", "corp_name": "회사3", "corp_eng_name": "Co 3",
                      "stock_code": "000003", "modify_date": "20261015"}
    assert got[1]["stock_code"] == ""                          # 공백 종목코드는 비운다(종전 strip 그대로)


# ── ③ 해석 실패 → 한 번 받고 다시 찾기 ──────────────────────────────────────

def test_new_listing_ticker_resolves_after_one_refresh(env):
    _install_old(timedelta(days=7))
    found = asyncio.run(_client().lookup_corp_code_all("179880"))
    assert found and found[0]["corp_name"] == "멜콘" and found[0]["stock_code"] == "179880"
    assert len(env["downloads"]) == 1
    st = C.corp_refresh_stats()
    assert st["last_outcome"] == "refreshed" and st["last_reason"] == "miss:ticker"
    assert st["last_added_tickers"] == ["179880"]
    assert C._corp_registry_state["source"] == "refresh"


def test_new_listing_name_resolves_after_one_refresh(env):
    _install_old(timedelta(hours=2))
    found = asyncio.run(_client().lookup_corp_code_all("멜콘"))
    assert found and found[0]["stock_code"] == "179880"
    assert len(env["downloads"]) == 1


def test_refreshed_registry_is_saved_for_the_next_process(env):
    _install_old(timedelta(days=2))
    asyncio.run(_client().lookup_corp_code_all("179880"))
    loaded = C.DartClient._master_db_load(require_english=False)
    assert loaded and any(c["stock_code"] == "179880" for c in loaded)
    assert C._master_db_updated_at() is not None


def test_a_just_fetched_registry_is_not_fetched_again(env):
    _install_old(timedelta(minutes=10))
    assert asyncio.run(_client().lookup_corp_code_all("179880")) == []
    assert env["downloads"] == []


def test_unknown_origin_registry_never_triggers_a_download(env):
    """출처를 모르는 명부(시험이 직접 꽂은 것 등)는 나이를 모르니 받지 않는다."""
    C._install_corp_codes(list(OLD), source="test", fetched_at=None)
    asyncio.run(_client().lookup_corp_code_all("179880"))
    assert env["downloads"] == []


def test_switch_off_disables_miss_refresh(env, monkeypatch):
    monkeypatch.setenv("OPM_CORP_REFRESHER", "0")
    _install_old(timedelta(days=7))
    assert asyncio.run(_client().lookup_corp_code_all("179880")) == []
    assert env["downloads"] == []


def test_miss_refresh_is_capped_per_hour_even_when_it_fails(env):
    """DART 가 계속 실패해도 매 요청이 다시 받지 않는다 — 시간당 한 번."""
    _install_old(timedelta(days=7))
    env["result"] = C.DartClientError("020", "한도 초과")

    async def go():
        cli = _client()
        for _ in range(5):
            assert await cli.lookup_corp_code_all("179880") == []
    asyncio.run(go())
    assert len(env["downloads"]) == 1
    st = C.corp_refresh_stats()
    assert st["last_outcome"] == "failed" and st["failed"] == 1
    assert st["last_error"] == "DartClientError[020]"           # 예외 문자열(요청 URL·키가 섞일 수 있다)은 싣지 않는다
    assert C._corp_code_cache is not None and len(C._corp_code_cache) == len(OLD)   # 옛 명부 유지


def test_concurrent_misses_share_one_download(env):
    _install_old(timedelta(days=7))
    env["delay"] = 0.05

    async def go():
        cli = _client()
        return await asyncio.gather(*(cli.lookup_corp_code_all(q) for q in ("179880", "멜콘", "179880", "01028933")))
    results = asyncio.run(go())
    assert len(env["downloads"]) == 1
    assert all(r and r[0]["corp_code"] == "01028933" for r in results)


def test_slow_download_does_not_hold_the_request(env, monkeypatch):
    """기다림 상한을 넘기면 지금 명부로 답하고, 받기는 뒤에서 마저 끝난다."""
    _install_old(timedelta(days=7))
    env["delay"] = 0.3
    monkeypatch.setattr(C, "_CORP_MISS_WAIT_SEC", 0.05)

    async def go():
        cli = _client()
        first = await cli.lookup_corp_code_all("179880")
        await asyncio.sleep(0.5)                                # 뒤에서 끝난 받기
        return first, await cli.lookup_corp_code_all("179880")
    first, later = asyncio.run(go())
    assert first == []
    assert later and later[0]["stock_code"] == "179880"
    assert len(env["downloads"]) == 1


def test_a_shrunken_download_is_rejected_and_the_old_registry_kept(env):
    """상장사가 3% 넘게 줄어든 응답은 반쪽으로 보고 버린다 — 하루 동안 수백 곳을 「없다」고 하지 않게."""
    many = [_corp(f"{i:08d}", f"회사{i}", f"{i:05d}0") for i in range(1, 101)]
    _install_old(timedelta(days=2), corps=many)
    env["result"] = many[:90]
    ok = asyncio.run(_client().refresh_corp_codes(reason="test"))
    assert ok is False
    assert len(C._corp_code_cache) == 100
    assert C.corp_refresh_stats()["last_outcome"] == "rejected"


def test_a_shrunken_registry_is_accepted_once_the_current_one_is_days_old(env):
    """며칠째 같은 이유로 버리고 있으면 받는다 — DART 쪽 정리였다면 영영 굳는 편이 더 나쁘다."""
    many = [_corp(f"{i:08d}", f"회사{i}", f"{i:05d}0") for i in range(1, 101)]
    _install_old(timedelta(days=3, hours=1), corps=many)
    env["result"] = many[:90]
    assert asyncio.run(_client().refresh_corp_codes(reason="test")) is True
    assert len(C._corp_code_cache) == 90


# ── 백그라운드 확인 한 번(①·②) ──────────────────────────────────────────

def test_tick_refreshes_once_a_day(env):
    _install_old(timedelta(hours=23, minutes=5))
    assert asyncio.run(_client().corp_registry_tick()) == "refreshed"
    assert len(env["downloads"]) == 1
    assert asyncio.run(_client().corp_registry_tick()) == "fresh"     # 방금 받았다 — 방금 저장한 파일을 「남이 받은 것」으로 읽지도 않는다
    assert len(env["downloads"]) == 1


def test_tick_refreshes_when_daily_prices_list_a_ticker_the_registry_lacks(env):
    _install_old(timedelta(hours=3))
    env["market"]["ctx"] = R.MarketContext(
        {"005930": 1, "000660": 1, "179880": 1, "005935": 1},
        frozenset({"005930", "000660", "179880", "005935"}), "20261016", "krx_daily")
    assert asyncio.run(_client().corp_registry_tick()) == "refreshed"
    assert C.corp_refresh_stats()["last_reason"] == "krx_new_listing"
    assert C._corp_registry_state["krx_baseline"] == frozenset()     # 우선주(005935)는 세지 않는다


def test_tick_does_not_refetch_hourly_for_a_code_dart_has_not_listed_yet(env):
    """DART 가 늦게 싣는 코드 때문에 매시간 받지 않는다 — 같은 코드는 다음 정기 갱신까지 기다린다."""
    _install_old(timedelta(hours=3))
    env["result"] = OLD                                         # 받아도 아직 없다
    env["market"]["ctx"] = R.MarketContext({"179880": 1}, frozenset({"005930", "179880"}), "20261016", "krx_daily")
    cli = _client()
    assert asyncio.run(cli.corp_registry_tick()) == "refreshed"
    C._corp_registry_state["last_attempt_mono"] = None          # 시간당 상한이 풀려도
    assert asyncio.run(cli.corp_registry_tick()) == "fresh"
    assert len(env["downloads"]) == 1


def test_tick_without_market_context_only_does_the_daily_check(env):
    _install_old(timedelta(hours=3))
    assert asyncio.run(_client().corp_registry_tick()) == "fresh"
    assert env["downloads"] == []


def test_tick_prewarms_and_picks_up_a_newer_file_written_by_another_process(env):
    C.DartClient._master_db_save(NEW)                           # 다른 프로세스가 방금 받아 둔 파일
    _install_old(timedelta(hours=5))
    C._corp_registry_state["last_attempt_mono"] = None
    assert asyncio.run(_client().corp_registry_tick()) == "reloaded"
    assert env["downloads"] == []
    assert any(c["stock_code"] == "179880" for c in C._corp_code_cache)


def test_first_load_records_when_the_sqlite_registry_was_fetched(env):
    C.DartClient._master_db_save(OLD)
    with sqlite3.connect(C._MASTER_DB_PATH) as conn:
        conn.execute("UPDATE _meta SET value=? WHERE key='last_updated'", ((datetime.now() - timedelta(days=3)).isoformat(),))
    asyncio.run(_client()._load_corp_codes())
    age = C._corp_registry_age_sec()
    assert age is not None and 3 * 86400 - 60 < age < 3 * 86400 + 60
    stats = C.registry_stats()["corp_master"]
    assert stats["source"] == "sqlite"
    assert 71.9 <= stats["fetched_age_hours"] <= 72.1          # 메모리에 올린 시각(age_hours)과 따로 보인다
    assert stats["age_hours"] < 1
    assert stats["refresh"]["policy"]["daily_hours"] == C._CORP_REFRESH_AGE_HOURS


# ── 갱신기 켜고 끄기 ────────────────────────────────────────────────────

def test_refresher_switches(monkeypatch):
    monkeypatch.setenv(REF.ENV_FLAG, "0")
    assert REF.disabled_reason() == "OPM_CORP_REFRESHER=0"
    monkeypatch.setenv(REF.ENV_FLAG, "1")
    monkeypatch.delenv("OPENDART_API_KEY", raising=False)
    assert "OPENDART_API_KEY" in REF.disabled_reason()
    monkeypatch.setenv("OPENDART_API_KEY", "k")
    assert REF.disabled_reason() is None


def test_refresher_loop_runs_ticks_and_stops(monkeypatch):
    monkeypatch.setenv(REF.ENV_FLAG, "1")
    monkeypatch.setenv("OPENDART_API_KEY", "k")
    ran: list[str] = []

    async def fake_run_once():
        ran.append("tick")
        return "fresh"
    monkeypatch.setattr(REF, "run_once", fake_run_once)

    async def go():
        stop = asyncio.Event()
        task = asyncio.get_running_loop().create_task(REF._loop(stop, startup_delay=0.0, interval=0.01))
        await asyncio.sleep(0.1)
        await REF.stop(REF.Handle(task, stop))
        assert task.done()
        await REF.stop(None)
    asyncio.run(go())
    assert len(ran) >= 2


def test_refresher_tick_failure_never_kills_the_loop(monkeypatch):
    class _Boom:
        async def corp_registry_tick(self):
            raise RuntimeError("DB 없음")
    monkeypatch.setattr(C, "get_dart_client", lambda: _Boom())
    assert asyncio.run(REF.run_once()) is None


def test_served_app_starts_the_registry_refresher(monkeypatch):
    """260729 교훈 — 서빙되는 객체에 붙었나를 본다(lifespan 이 갱신기를 띄우고 멈춘다)."""
    from starlette.testclient import TestClient
    from open_proxy_mcp.server import build_app
    order: list[str] = []
    monkeypatch.setattr(REF, "start", lambda: order.append("start") or "H")

    async def fake_stop(h):
        order.append(f"stop:{h}")
    monkeypatch.setattr(REF, "stop", fake_stop)
    with TestClient(build_app()):
        assert "start" in order
    assert order[-1] == "stop:H"
