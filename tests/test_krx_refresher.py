"""KRX 시세 백그라운드 갱신기 — 네트워크 0 · 운영 DB 0. 입출력 함수를 가짜로 바꿔 끼워 판단 로직만 검사한다."""
from __future__ import annotations

import asyncio
import contextlib
import pathlib
import sys
from datetime import date, datetime

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp import krx_daily  # noqa: E402
from open_proxy_mcp.clock import KST  # noqa: E402
from open_proxy_mcp.services import krx_refresher as R  # noqa: E402


def kst(y, m, d, hh=8, mm=0, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=KST)


MON, TUE, FRI, SAT, SUN = (kst(2026, 10, 19), kst(2026, 10, 20), kst(2026, 10, 23), kst(2026, 10, 24), kst(2026, 10, 18))


def _rows(n, prefix="A"):
    return [{"ISU_CD": f"{prefix}{i:05d}", "TDD_CLSPRC": "1,000"} for i in range(n)]


@pytest.fixture(autouse=True)
def _clean():
    R._LOGGED_FRESH.clear()
    R._BUSY_STREAK["n"] = 0
    yield
    R._LOGGED_FRESH.clear()
    R._BUSY_STREAK["n"] = 0


# ── 달력 ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("now,expected", [
    (kst(2026, 10, 19, 7, 29, 59), False),     # 월 07:29:59 — 아직 창 밖
    (kst(2026, 10, 19, 7, 30, 0), True),       # 07:30:00 부터
    (kst(2026, 10, 21, 13, 0), True),
    (kst(2026, 10, 23, 19, 59, 59), True),     # 금 19:59:59 까지
    (kst(2026, 10, 23, 20, 0, 0), False),      # 20:00:00 부터 밖
    (kst(2026, 10, 24, 10, 0), False),         # 토
    (kst(2026, 10, 18, 10, 0), False),         # 일
])
def test_window_is_weekdays_0730_to_2000_kst(now, expected):
    assert R.in_window(now) is expected


@pytest.mark.parametrize("now,expected", [
    (MON, "20261016"),                         # 월요일 아침의 기대 거래일 = 직전 금요일
    (TUE, "20261019"),
    (FRI, "20261022"),
    (SAT, "20261023"),                         # 주말에도 「오늘 이전 마지막 평일」
    (SUN, "20261016"),
    (kst(2026, 10, 19, 23, 59), "20261016"),   # 같은 날 안에서는 시각과 무관
])
def test_expected_trading_day_is_the_last_weekday_before_today(now, expected):
    assert R.expected_dd(now) == expected


def test_expected_day_reuses_the_existing_price_calendar(monkeypatch):
    """새 달력을 만들지 않는다 — `_price_dates` 가 이 서버의 유일한 거래일 달력이다."""
    from open_proxy_mcp.services import price_multiple_data as pmd
    seen = []
    real = pmd._price_dates
    monkeypatch.setattr(pmd, "_price_dates", lambda today=None: seen.append(today) or real(today))
    assert R.expected_dd(TUE) == "20261019"
    assert seen == [date(2026, 10, 20)]
    # 기존 호출(인자 없음)은 그대로 오늘 기준
    assert real()[0] <= pmd.today_kst().strftime("%Y%m%d")


@pytest.mark.parametrize("now,secs", [
    (kst(2026, 10, 19, 8, 3, 30), 390.0),
    (kst(2026, 10, 19, 8, 10, 0), 600.0),      # 경계 위에 있으면 다음 경계(10분 뒤)
    (kst(2026, 10, 19, 8, 9, 59, ), 1.0),
])
def test_next_tick_aligns_to_ten_minute_boundaries(now, secs):
    assert R.seconds_to_next_tick(now) == pytest.approx(secs, abs=0.01)


# ── 켜짐·꺼짐 ──────────────────────────────────────────────────────────────

def test_disable_flag_and_missing_prerequisites(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("KRX_API_KEY", "k")
    monkeypatch.delenv("KRX_OPEN_API_KEY", raising=False)
    monkeypatch.delenv(R.ENV_FLAG, raising=False)
    assert R.disabled_reason() is None                                    # 기본은 켜짐
    for off in ("0", "false", "OFF", "no"):
        monkeypatch.setenv(R.ENV_FLAG, off)
        assert R.ENV_FLAG in R.disabled_reason()
    monkeypatch.setenv(R.ENV_FLAG, "1")
    monkeypatch.delenv("DATABASE_URL")
    assert "DATABASE_URL" in R.disabled_reason()
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.delenv("KRX_API_KEY")
    assert "KRX" in R.disabled_reason()
    monkeypatch.setenv("KRX_OPEN_API_KEY", "k2")                           # 대체 키 이름도 인정
    assert R.disabled_reason() is None


def test_start_returns_none_when_disabled_and_creates_no_task(monkeypatch):
    monkeypatch.setenv(R.ENV_FLAG, "0")

    async def go():
        before = len(asyncio.all_tasks())
        assert R.start() is None
        assert len(asyncio.all_tasks()) == before
    asyncio.run(go())


def test_tests_run_with_the_refresher_off_by_default():
    """tests/conftest.py 가 기본으로 꺼 둔다 — 서버 앱을 띄우는 테스트가 갱신기를 띄우지 않는다."""
    import os
    assert os.environ.get(R.ENV_FLAG) == "0"


# ── 한 번의 확인(run_cycle) ────────────────────────────────────────────────

class Sim:
    """DB 상태·잠금·KRX 를 흉내 낸다. 두 머신이 같은 Sim 을 공유하면 같은 DB 를 보는 것과 같다."""

    def __init__(self, monkeypatch, stored="20261015", published=True, kospi=900, kosdaq=1800, write_ok=True, lock_held_by_other=False):
        self.stored, self.published, self.write_ok = stored, published, write_ok
        self.kospi_n, self.kosdaq_n = kospi, kosdaq
        self.locked = lock_held_by_other
        self.lock_attempts = self.fetches = self.writes = self.krx_calls = 0
        self.fetched_days, self.written = [], []
        monkeypatch.setattr(R, "_db_latest_dd", lambda: self.stored)
        monkeypatch.setattr(R, "_advisory_lock", self._lock)
        monkeypatch.setattr(R, "_fetch_day", self._fetch)
        monkeypatch.setattr(R, "_write", self._write)

    @contextlib.asynccontextmanager
    async def _lock(self):
        self.lock_attempts += 1
        if self.locked:
            yield False
            return
        self.locked = True
        try:
            yield True
        finally:
            self.locked = False

    async def _fetch(self, basDd):
        self.fetches += 1
        self.fetched_days.append(basDd)
        await asyncio.sleep(0.01)                       # 다른 머신이 끼어들 틈
        if not self.published:
            self.krx_calls += 1
            return [], [], 1
        self.krx_calls += 2
        return _rows(self.kospi_n, "K"), _rows(self.kosdaq_n, "Q"), 2

    async def _write(self, dd, kospi, kosdaq):
        self.writes += 1
        if self.write_ok:
            self.stored = dd
            self.written.append((dd, len(kospi), len(kosdaq)))
        return self.write_ok


def test_fresh_db_means_zero_krx_calls_and_no_lock(monkeypatch):
    sim = Sim(monkeypatch, stored="20261016")
    res = asyncio.run(R.run_cycle(MON))                 # 월요일 아침: 기대 = 금요일, 이미 저장됨
    assert res.outcome == "fresh" and res.krx_calls == 0
    assert sim.lock_attempts == 0 and sim.fetches == 0 and sim.writes == 0


def test_stored_day_newer_than_expected_is_also_fresh(monkeypatch):
    sim = Sim(monkeypatch, stored="20261019")
    assert asyncio.run(R.run_cycle(MON)).outcome == "fresh"
    assert sim.fetches == 0


def test_stale_db_fetches_the_expected_day_once_and_writes_it(monkeypatch):
    sim = Sim(monkeypatch, stored="20261015")
    res = asyncio.run(R.run_cycle(MON))
    assert (res.outcome, res.expected, res.krx_calls, res.rows) == ("refreshed", "20261016", 2, 2700)
    assert sim.fetched_days == ["20261016"] and sim.written == [("20261016", 900, 1800)]
    assert sim.locked is False                          # 일이 끝나면 잠금이 풀려 있다
    # 같은 날 다음 틱: 이미 저장돼 있으니 KRX 0콜 — 「저장되면 그날은 멈춘다」
    again = asyncio.run(R.run_cycle(kst(2026, 10, 19, 8, 10)))
    assert again.outcome == "fresh" and sim.fetches == 1


def test_only_the_lock_holder_proceeds(monkeypatch):
    sim = Sim(monkeypatch, stored="20261015", lock_held_by_other=True)
    res = asyncio.run(R.run_cycle(MON))
    assert res.outcome == "lock_busy" and sim.lock_attempts == 1
    assert sim.fetches == 0 and sim.writes == 0 and res.krx_calls == 0


def test_a_lock_stuck_for_three_ticks_is_bypassed_so_the_refresher_cannot_stay_dead(monkeypatch, caplog):
    """보유자가 죽었는데 풀러가 그 연결을 바로 정리하지 못하면 잠금이 남을 수 있다 — 30분(3틱) 연속 막히면 잠금 없이 진행한다(쓰기는 멱등)."""
    sim = Sim(monkeypatch, stored="20261015", lock_held_by_other=True)
    outs = []
    with caplog.at_level("WARNING"):
        for hh, mm in ((8, 0), (8, 10), (8, 20)):
            outs.append(asyncio.run(R.run_cycle(kst(2026, 10, 19, hh, mm))).outcome)
    assert outs == ["lock_busy", "lock_busy", "refreshed"]
    assert sim.fetches == 1 and sim.stored == "20261016"
    assert any("잠금 없이 진행" in r.message for r in caplog.records)
    assert R._BUSY_STREAK["n"] == R.LOCK_BUSY_STREAK_LIMIT                    # 우회한 틱까지 센다 — 다음 신선 확인에서 0 으로


def test_busy_streak_resets_once_the_db_is_fresh_or_the_lock_is_held(monkeypatch):
    sim = Sim(monkeypatch, stored="20261015", lock_held_by_other=True)
    asyncio.run(R.run_cycle(MON))
    assert R._BUSY_STREAK["n"] == 1
    sim.stored = "20261016"                                                    # 상대 머신이 끝냈다
    assert asyncio.run(R.run_cycle(kst(2026, 10, 19, 8, 10))).outcome == "fresh" and R._BUSY_STREAK["n"] == 0
    sim.stored, sim.locked = "20261015", False                                 # 다음 거래일, 이번엔 잠금을 잡는다
    asyncio.run(R.run_cycle(kst(2026, 10, 20, 8, 0)))
    assert R._BUSY_STREAK["n"] == 0


def test_recheck_after_taking_the_lock_skips_work_the_other_machine_just_did(monkeypatch):
    sim = Sim(monkeypatch, stored="20261015")
    reads = iter(["20261015", "20261016"])               # 잠금 전에는 낡았는데, 잠금을 잡고 다시 보니 상대가 끝냈다
    monkeypatch.setattr(R, "_db_latest_dd", lambda: next(reads))
    res = asyncio.run(R.run_cycle(MON))
    assert res.outcome == "fresh" and sim.lock_attempts == 1 and sim.fetches == 0 and sim.writes == 0


def test_two_machines_at_once_make_exactly_one_krx_fetch_and_one_write(monkeypatch):
    sim = Sim(monkeypatch, stored="20261015")

    async def both():
        return await asyncio.gather(R.run_cycle(MON), R.run_cycle(MON))
    a, b = asyncio.run(both())
    assert sorted([a.outcome, b.outcome]) in (["lock_busy", "refreshed"], ["fresh", "refreshed"])
    assert sim.fetches == 1 and sim.writes == 1 and sim.krx_calls == 2
    assert sim.stored == "20261016" and sim.locked is False


def test_not_yet_published_costs_one_call_writes_nothing_and_retries_next_tick(monkeypatch):
    sim = Sim(monkeypatch, stored="20261015", published=False)
    res = asyncio.run(R.run_cycle(MON))
    assert (res.outcome, res.krx_calls) == ("not_published", 1) and sim.writes == 0 and sim.stored == "20261015"
    sim.published = True                                  # 08:00 게시
    res2 = asyncio.run(R.run_cycle(kst(2026, 10, 19, 8, 10)))
    assert res2.outcome == "refreshed" and sim.stored == "20261016"


@pytest.mark.parametrize("kospi,kosdaq", [(900, 0), (0, 1800), (50, 1800), (900, 299)])
def test_half_snapshots_are_never_written(monkeypatch, kospi, kosdaq):
    sim = Sim(monkeypatch, stored="20261015", kospi=kospi, kosdaq=kosdaq)
    res = asyncio.run(R.run_cycle(MON))
    # 코스피가 비면 아직 안 게시된 것으로, 그 밖에는 반쪽 응답으로 본다 — 어느 쪽이든 쓰지 않는다.
    assert res.outcome in ("incomplete", "not_published") and sim.writes == 0 and sim.stored == "20261015"


def test_write_failure_is_reported_and_leaves_the_day_open_for_the_next_tick(monkeypatch):
    sim = Sim(monkeypatch, stored="20261015", write_ok=False)
    res = asyncio.run(R.run_cycle(MON))
    assert res.outcome == "write_failed" and sim.stored == "20261015" and sim.locked is False
    sim.write_ok = True
    assert asyncio.run(R.run_cycle(kst(2026, 10, 19, 8, 10))).outcome == "refreshed"


def test_unreadable_db_skips_the_tick_without_lock_or_krx(monkeypatch):
    sim = Sim(monkeypatch)
    monkeypatch.setattr(R, "_db_latest_dd", lambda: R._DB_DOWN)
    res = asyncio.run(R.run_cycle(MON))
    assert res.outcome == "db_error" and sim.lock_attempts == 0 and sim.fetches == 0


def test_empty_table_is_stale_and_gets_seeded(monkeypatch):
    sim = Sim(monkeypatch, stored=None)                    # 마이그레이션 직후: 표는 있는데 비었다
    assert asyncio.run(R.run_cycle(MON)).outcome == "refreshed" and sim.stored == "20261016"


def test_lock_connection_failure_is_a_db_error_not_a_crash(monkeypatch):
    Sim(monkeypatch)

    @contextlib.asynccontextmanager
    async def cannot_connect():
        yield None
    monkeypatch.setattr(R, "_advisory_lock", cannot_connect)
    assert asyncio.run(R.run_cycle(MON)).outcome == "db_error"


def test_a_weekday_holiday_just_keeps_checking_without_breaking(monkeypatch):
    """평일 휴장 다음 날: 기대 거래일(휴장일)을 KRX 가 빈 응답으로 준다 — 쓰지 않고 틱마다 1콜, 아무것도 깨지지 않는다."""
    sim = Sim(monkeypatch, stored="20261015", published=False)
    ticks = [kst(2026, 10, 19, 7, 30), kst(2026, 10, 19, 7, 40), kst(2026, 10, 19, 7, 50),
             kst(2026, 10, 19, 8, 0), kst(2026, 10, 19, 8, 10), kst(2026, 10, 19, 8, 20)]
    results = [asyncio.run(R.run_cycle(t)) for t in ticks]
    assert {r.outcome for r in results} == {"not_published"}
    assert sim.writes == 0 and sim.krx_calls == 6      # 틱당 1콜 — 상한 계산의 기초


# ── 잠금 입출력 ────────────────────────────────────────────────────────────

class LockConn:
    def __init__(self, grant=True):
        self.grant, self.sql, self.closed = grant, [], False

    class _R:
        def __init__(self, v):
            self.v = v

        def fetchone(self):
            return (self.v,)

    def execute(self, sql, params=()):
        self.sql.append((sql, params))
        return LockConn._R(self.grant)

    def close(self):
        self.closed = True


def test_advisory_lock_uses_the_fixed_key_and_unlocks_then_closes(monkeypatch):
    conn = LockConn(grant=True)
    monkeypatch.setattr(R, "_open_lock_conn", lambda: conn)

    async def go():
        async with R._advisory_lock() as held:
            assert held is True
    asyncio.run(go())
    assert conn.sql == [("SELECT pg_try_advisory_lock(%s)", (krx_daily.REFRESH_LOCK_KEY,)),
                        ("SELECT pg_advisory_unlock(%s)", (krx_daily.REFRESH_LOCK_KEY,))]
    assert conn.closed is True


def test_advisory_lock_not_granted_does_not_unlock_but_still_closes(monkeypatch):
    conn = LockConn(grant=False)
    monkeypatch.setattr(R, "_open_lock_conn", lambda: conn)

    async def go():
        async with R._advisory_lock() as held:
            assert held is False
    asyncio.run(go())
    assert [s for s, _ in conn.sql] == ["SELECT pg_try_advisory_lock(%s)"] and conn.closed


def test_lock_is_released_and_connection_closed_even_if_the_work_raises(monkeypatch):
    conn = LockConn(grant=True)
    monkeypatch.setattr(R, "_open_lock_conn", lambda: conn)

    async def go():
        async with R._advisory_lock():
            raise RuntimeError("KRX 장애")
    with pytest.raises(RuntimeError):
        asyncio.run(go())
    assert any("pg_advisory_unlock" in s for s, _ in conn.sql) and conn.closed


def test_unlock_failure_cannot_leak_the_lock_because_the_connection_is_closed(monkeypatch):
    class FlakyUnlock(LockConn):
        def execute(self, sql, params=()):
            if "unlock" in sql:
                raise OSError("connection lost")
            return super().execute(sql, params)
    conn = FlakyUnlock()
    monkeypatch.setattr(R, "_open_lock_conn", lambda: conn)

    async def go():
        async with R._advisory_lock() as held:
            return held
    assert asyncio.run(go()) is True and conn.closed is True


def test_lock_connect_failure_yields_none(monkeypatch):
    def boom():
        raise OSError("refused")
    monkeypatch.setattr(R, "_open_lock_conn", boom)

    async def go():
        async with R._advisory_lock() as held:
            return held
    assert asyncio.run(go()) is None


# ── DB 상태 읽기 ───────────────────────────────────────────────────────────

def test_db_latest_prefers_krx_daily_and_falls_back_to_weekly_when_the_table_is_missing(monkeypatch):
    seen = []

    def fake(sql, params=()):
        seen.append(sql)
        return {"SELECT max(trd_dd) FROM krx_daily": [("20261016",)]}.get(sql)
    monkeypatch.setattr("open_proxy_mcp.db.pg_rows", fake)
    assert R._db_latest_dd() == "20261016" and len(seen) == 1

    seen.clear()                                          # krx_daily 없음(마이그레이션 전 배포) → krx_weekly 로 대신: 갱신이 멎지 않는다
    monkeypatch.setattr("open_proxy_mcp.db.pg_rows",
                        lambda sql, params=(): seen.append(sql) or ({"SELECT max(price_dd) FROM krx_weekly": [("20261015",)]}.get(sql)))
    assert R._db_latest_dd() == "20261015" and len(seen) == 2

    monkeypatch.setattr("open_proxy_mcp.db.pg_rows", lambda sql, params=(): None)     # 둘 다 못 읽음
    assert R._db_latest_dd() is R._DB_DOWN
    monkeypatch.setattr("open_proxy_mcp.db.pg_rows", lambda sql, params=(): [(None,)])  # 표는 있으나 비었음
    assert R._db_latest_dd() is None


# ── KRX 호출 ───────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        return self.payload


def _fake_httpx(monkeypatch, routes):
    import httpx
    from open_proxy_mcp.dart import krx_meter
    log, bumps = [], []

    class Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, params=None):
            log.append((url.rsplit("/", 1)[-1], params["basDd"], bool(headers and headers.get("AUTH_KEY"))))
            r = routes[url.rsplit("/", 1)[-1]]
            if isinstance(r, Exception):
                raise r
            return r
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    monkeypatch.setattr(krx_meter, "bump", lambda n=1: bumps.append(n))
    monkeypatch.setenv("KRX_API_KEY", "test-key")
    return log, bumps


def test_fetch_day_checks_kospi_first_and_skips_kosdaq_when_nothing_is_published(monkeypatch):
    log, bumps = _fake_httpx(monkeypatch, {"stk_bydd_trd": _Resp({"OutBlock_1": []}), "ksq_bydd_trd": _Resp({"OutBlock_1": _rows(5)})})
    assert asyncio.run(R._fetch_day("20261016")) == ([], [], 1)
    assert log == [("stk_bydd_trd", "20261016", True)] and len(bumps) == 1            # 1콜, 호출 장부에도 1


def test_fetch_day_gets_both_markets_in_two_calls(monkeypatch):
    log, bumps = _fake_httpx(monkeypatch, {"stk_bydd_trd": _Resp({"OutBlock_1": _rows(3, "K")}), "ksq_bydd_trd": _Resp({"OutBlock_1": _rows(4, "Q")})})
    kospi, kosdaq, calls = asyncio.run(R._fetch_day("20261016"))
    assert (len(kospi), len(kosdaq), calls) == (3, 4, 2) and [x[0] for x in log] == ["stk_bydd_trd", "ksq_bydd_trd"] and len(bumps) == 2


def test_fetch_day_treats_errors_as_empty_and_logs(monkeypatch, caplog):
    _fake_httpx(monkeypatch, {"stk_bydd_trd": _Resp({}, status=401), "ksq_bydd_trd": _Resp({})})
    with caplog.at_level("WARNING"):
        assert asyncio.run(R._fetch_day("20261016")) == ([], [], 1)
    assert any("401" in r.message for r in caplog.records)
    caplog.clear()
    _fake_httpx(monkeypatch, {"stk_bydd_trd": OSError("timeout"), "ksq_bydd_trd": _Resp({})})
    with caplog.at_level("WARNING"):
        assert asyncio.run(R._fetch_day("20261016")) == ([], [], 1)
    assert any("KRX 호출 실패" in r.message for r in caplog.records)


def test_write_invalidates_the_serving_date_cache_on_success_only(monkeypatch):
    from open_proxy_mcp.services import price_multiple_data as pmd
    monkeypatch.setattr(pmd, "_krx_db_upsert", lambda dd, k, q: True)
    pmd._SERVING_DD.update(dd="20261015", at=1e18)
    assert asyncio.run(R._write("20261016", [1], [2])) is True and pmd._SERVING_DD["dd"] is None
    pmd._SERVING_DD.update(dd="20261015", at=1e18)
    monkeypatch.setattr(pmd, "_krx_db_upsert", lambda dd, k, q: False)
    assert asyncio.run(R._write("20261016", [1], [2])) is False and pmd._SERVING_DD["dd"] == "20261015"


# ── 루프와 앱 연결 ─────────────────────────────────────────────────────────

def test_loop_runs_once_at_startup_then_only_inside_the_window(monkeypatch):
    cycles, sleeps = [], iter([False, False, False, True])        # 시작 지연 · 토요일 틱 · 월요일 틱 · 종료
    # now_kst() 는 반복마다 두 번 불린다(다음 틱까지 잘 시간 계산 · 깬 뒤 창 판정)
    clock = iter([kst(2026, 10, 24, 8, 50), kst(2026, 10, 24, 9, 0),
                  kst(2026, 10, 26, 8, 0), kst(2026, 10, 26, 8, 10), kst(2026, 10, 26, 8, 10)])

    async def fake_sleep(stop, seconds):
        return next(sleeps)

    async def fake_cycle(now=None):
        cycles.append(now)
    monkeypatch.setattr(R, "_sleep_or_stop", fake_sleep)
    monkeypatch.setattr(R, "_safe_cycle", fake_cycle)
    monkeypatch.setattr(R, "now_kst", lambda: next(clock))
    asyncio.run(R._loop(asyncio.Event()))
    assert cycles == [None, kst(2026, 10, 26, 8, 10)]             # 기동 직후 1회(창과 무관) + 평일 창 안의 틱만. 토요일 틱은 건너뜀


def test_loop_exits_immediately_if_stopped_during_the_startup_delay(monkeypatch):
    called = []

    async def fake_cycle(now=None):
        called.append(now)

    async def stop_now(stop, seconds):
        return True
    monkeypatch.setattr(R, "_sleep_or_stop", stop_now)
    monkeypatch.setattr(R, "_safe_cycle", fake_cycle)
    asyncio.run(R._loop(asyncio.Event()))
    assert called == []


def test_safe_cycle_never_raises_and_loop_survives(monkeypatch, caplog):
    async def boom(now=None):
        raise RuntimeError("예상 밖 실패")
    monkeypatch.setattr(R, "run_cycle", boom)
    with caplog.at_level("ERROR"):
        assert asyncio.run(R._safe_cycle()) is None
    assert any("확인 중 예외" in r.message for r in caplog.records)


def test_sleep_or_stop_wakes_on_stop_and_times_out_otherwise():
    async def go():
        stop = asyncio.Event()
        assert await R._sleep_or_stop(stop, 0.01) is False
        stop.set()
        assert await R._sleep_or_stop(stop, 5) is True
    asyncio.run(go())


def _bare_app(order):
    from starlette.applications import Starlette

    @contextlib.asynccontextmanager
    async def inner(app):
        order.append("inner_enter")
        yield {"from": "inner"}
        order.append("inner_exit")
    return Starlette(lifespan=inner)


def test_attach_to_app_starts_after_the_inner_lifespan_and_stops_before_it_exits(monkeypatch):
    from starlette.testclient import TestClient
    order = []
    monkeypatch.setattr(R, "start", lambda: order.append("start") or "HANDLE")

    async def fake_stop(h):
        order.append(f"stop:{h}")
    monkeypatch.setattr(R, "stop", fake_stop)
    app = _bare_app(order)
    R.attach_to_app(app)
    with TestClient(app):
        assert order == ["inner_enter", "start"]
    assert order == ["inner_enter", "start", "stop:HANDLE", "inner_exit"]


def test_app_still_boots_when_the_refresher_cannot_start(monkeypatch):
    from starlette.testclient import TestClient
    order = []

    def broken():
        raise RuntimeError("시작 실패")
    monkeypatch.setattr(R, "start", broken)
    app = _bare_app(order)
    R.attach_to_app(app)
    with TestClient(app):
        assert order == ["inner_enter"]
    assert order == ["inner_enter", "inner_exit"]


def test_real_start_and_stop_manage_a_task(monkeypatch):
    monkeypatch.setenv(R.ENV_FLAG, "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("KRX_API_KEY", "k")
    ran = []

    async def fake_loop(stop, startup_delay=0):
        ran.append("started")
        await stop.wait()
        ran.append("stopped")
    monkeypatch.setattr(R, "_loop", fake_loop)

    async def go():
        h = R.start()
        assert h is not None
        await asyncio.sleep(0)
        await R.stop(h)
        assert h.task.done()
        await R.stop(None)                                  # None 도 안전
    asyncio.run(go())
    assert ran[0] == "started"


def test_build_app_wires_the_refresher_into_the_server_lifespan(monkeypatch):
    """`build_app()` 이 만든 실제 서빙 앱이 시작될 때 갱신기를 띄우고, 꺼질 때 멈춘다."""
    from starlette.testclient import TestClient
    from open_proxy_mcp.server import build_app
    events = []
    monkeypatch.setattr(R, "start", lambda: events.append("start") or "H")

    async def fake_stop(h):
        events.append("stop")
    monkeypatch.setattr(R, "stop", fake_stop)
    with TestClient(build_app()):
        assert events == ["start"]
    assert events == ["start", "stop"]


def test_attach_to_app_leaves_a_non_starlette_app_alone(caplog):
    class Odd:
        pass
    app = Odd()
    with caplog.at_level("WARNING"):
        R.attach_to_app(app)                                  # 예외 없이 지나간다
    assert not hasattr(app, "router") and any("lifespan" in r.message for r in caplog.records)
