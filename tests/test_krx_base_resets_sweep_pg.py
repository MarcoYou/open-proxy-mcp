"""기준가 리셋 스윕(`scripts/krx_base_resets.py sweep`)의 휴장 판정 — 빈 응답 한 번을 휴장으로 적지 않는다(261019).

20260702 실측: 코스피 빈 응답 한 번(게시 전·일시 빈 응답)을 휴장으로 적어, 그날 기준가 리셋 1건이 빠지고 주식수 변동 8건이 하루 늦게
적혔다. 장부가 「처리함」으로 보아 다시 메우지 않았다. 이제는 두 시장이 다 비고 평일 2일이 지났고, 다음 거래일 기준가가 그 앞 거래일
종가와 이어질 때만 휴장으로 적는다. 일시 오류·확정 못 한 날에서는 멈춰 다음 실행이 그 날부터 다시 묻는다.

로컬 Docker 의 `postgres:17` 이 필요하다(`tests/conftest.py` 의 `pg17`). KRX 는 가짜(네트워크 0).
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

psycopg = pytest.importorskip("psycopg")


def _mod():
    spec = importlib.util.spec_from_file_location("krx_base_resets", ROOT / "scripts" / "krx_base_resets.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


TICKERS = [f"{i:06d}" for i in range(1, 201)]


def _snap(closes: dict[str, int], cmp: dict[str, int] | None = None, shrs: int = 1_000) -> list[dict]:
    return [{"ISU_CD": t, "TDD_CLSPRC": f"{c:,}", "CMPPREVDD_PRC": f"{(cmp or {}).get(t, 0):,}", "LIST_SHRS": f"{shrs:,}"}
            for t, c in closes.items()]


class _Resp:
    def __init__(self, rows):
        self.rows = rows

    def raise_for_status(self):
        if isinstance(self.rows, Exception):
            raise self.rows

    def json(self):
        return {"OutBlock_1": self.rows}


def _setup(monkeypatch, url, today: str, krx: dict[tuple[str, str], object]):
    """krx = {(날짜, 'stk'): 그 날 전 종목 행 | 예외}. 앞 절반은 코스피(stk), 뒤 절반은 코스닥(ksq) 응답으로 나눠 준다(없는 날은 둘 다 빈 응답)."""
    m = _mod()
    calls = []

    class Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, params=None):
            ep = url.rsplit("/", 1)[-1][:3]
            calls.append((params["basDd"], ep))
            rows = krx.get((params["basDd"], "stk"), [])
            if isinstance(rows, Exception):
                return _Resp(rows)
            half = len(rows) // 2
            return _Resp(rows[:half] if ep == "stk" else rows[half:])
    monkeypatch.setattr(m.httpx, "AsyncClient", Client)
    monkeypatch.setattr(m, "THROTTLE_S", 0)
    from open_proxy_mcp.dart import krx_meter
    monkeypatch.setattr(krx_meter, "bump", lambda n=1: None)

    class D(_dt.date):
        @classmethod
        def today(cls):
            return _dt.date(int(today[:4]), int(today[4:6]), int(today[6:]))
    monkeypatch.setattr(m, "date", D)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("KRX_API_KEY", "test")
    m._calls = 0
    m._fail_streak = 0
    return m, calls


def _seed(url, last_done: str, closes: dict[str, int]):
    """마지막 처리일까지 장부를 채워 둔다(재개 시드: 그 날 코스피 응답 = closes)."""
    m = _mod()
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("CREATE TABLE krx_weekly (price_dd text, ticker text, market text, close bigint, mktcap bigint, list_shrs bigint)")
        for stmt in m.DDL.strip().split(";"):
            if stmt.strip():
                c.execute(stmt)
        c.execute("INSERT INTO krx_reset_sweep_checkpoint VALUES (%s, %s)", (last_done, len(closes)))


def _state(url):
    with psycopg.connect(url) as c:
        ck = dict(c.execute("SELECT price_dd, n_stocks FROM krx_reset_sweep_checkpoint").fetchall())
        ev = c.execute("SELECT event_dd, count(*) FROM krx_adj_events GROUP BY 1 ORDER BY 1").fetchall()
    return ck, ev


BASE = {t: 1_000 + i for i, t in enumerate(TICKERS)}


def test_a_holiday_is_recorded_only_after_the_next_trading_day_confirms_it(pg17, monkeypatch):
    _seed(pg17, "20261008", BASE)
    up = {t: c + 10 for t, c in BASE.items()}
    krx = {("20261008", "stk"): _snap(BASE),
           ("20261012", "stk"): _snap(up, {t: 10 for t in TICKERS})}        # 기준가 = 10/8 종가 → 10/9 는 휴장이다
    m, calls = _setup(monkeypatch, pg17, "20261013", krx)
    asyncio.run(m.sweep(_dt.date(2026, 10, 1), 100))
    ck, ev = _state(pg17)
    assert ck == {"20261008": 200, "20261009": 0, "20261012": 200}
    assert ev == []                                                        # 거짓 기준가 리셋이 없다
    assert ("20261009", "ksq") in calls                                    # 휴장 후보는 코스닥도 묻는다


def test_an_empty_answer_the_morning_after_is_not_written_and_the_sweep_stops(pg17, monkeypatch):
    """20260702 재현: 0703 아침 실행 — 0702 를 KRX 가 아직 비워 준다. 휴장으로 적지 않고 멈춘다(다음 실행이 다시 묻는다)."""
    _seed(pg17, "20260701", BASE)
    m, calls = _setup(monkeypatch, pg17, "20260703", {("20260701", "stk"): _snap(BASE)})
    asyncio.run(m.sweep(_dt.date(2026, 6, 1), 100))
    ck, _ = _state(pg17)
    assert ck == {"20260701": 200}
    # 다음 실행(0706): 0702·0703 이 게시됐다 — 0702 의 리셋 1건과 0703 이 제자리에 적힌다
    after = {**{t: c + 5 for t, c in BASE.items()}, "000001": 2_070}
    krx = {("20260701", "stk"): _snap(BASE),
           ("20260702", "stk"): _snap(after, {**{t: 5 for t in TICKERS}, "000001": -10}),       # 000001: 기준가 2,080 ≠ 1,000 → 리셋
           ("20260703", "stk"): _snap(after)}
    m, _ = _setup(monkeypatch, pg17, "20260706", krx)
    asyncio.run(m.sweep(_dt.date(2026, 6, 1), 100))
    ck, ev = _state(pg17)
    assert ck == {"20260701": 200, "20260702": 200, "20260703": 200}
    assert ev == [("20260702", 1)]


def test_an_empty_day_whose_next_base_price_does_not_continue_is_not_a_holiday(pg17, monkeypatch):
    """두 시장 빈 응답 + 평일 2일 경과여도, 다음 거래일 기준가가 앞 거래일 종가와 이어지지 않으면 거래일을 빠뜨린 것이다 —
    휴장으로 적지 않고, 다음 날의 직전 종가가 어긋난 채로 리셋을 적지도 않는다(종전에는 그 날 거의 모든 종목이 리셋으로 적혔을 것)."""
    _seed(pg17, "20260701", BASE)
    d3 = {t: c + 20 for t, c in BASE.items()}
    krx = {("20260701", "stk"): _snap(BASE),
           ("20260703", "stk"): _snap(d3, {t: 10 for t in TICKERS})}       # 기준가 = 1,010+i ≠ 0701 종가 → 0702 는 거래일이었다
    m, _ = _setup(monkeypatch, pg17, "20260708", krx)
    asyncio.run(m.sweep(_dt.date(2026, 6, 1), 100))
    ck, ev = _state(pg17)
    assert ck == {"20260701": 200} and ev == []


def test_a_transient_error_stops_instead_of_skipping_the_day(pg17, monkeypatch):
    """일시 오류인 날을 건너뛰고 다음 날을 처리하면 그 날은 영영 안 메워지고(재개는 마지막 처리일 다음부터) 다음 날의 직전 종가가 하루 어긋난다."""
    _seed(pg17, "20261013", BASE)
    krx = {("20261013", "stk"): _snap(BASE), ("20261014", "stk"): RuntimeError("502"),
           ("20261015", "stk"): _snap(BASE)}
    m, calls = _setup(monkeypatch, pg17, "20261019", krx)
    asyncio.run(m.sweep(_dt.date(2026, 10, 1), 100))
    ck, _ = _state(pg17)
    assert ck == {"20261013": 200} and ("20261015", "stk") not in calls


def test_resume_seeds_previous_closes_from_the_last_trading_day_not_a_trailing_holiday(pg17, monkeypatch):
    """마지막 처리일이 휴장(n=0)이면 그 날 재조회는 빈 응답이다 — 마지막 거래일을 다시 받아 직전 종가를 시드한다."""
    _seed(pg17, "20261008", BASE)
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute("INSERT INTO krx_reset_sweep_checkpoint VALUES ('20261009', 0)")
    krx = {("20261008", "stk"): _snap(BASE), ("20261012", "stk"): _snap(BASE)}
    m, calls = _setup(monkeypatch, pg17, "20261013", krx)
    asyncio.run(m.sweep(_dt.date(2026, 10, 1), 100))
    ck, ev = _state(pg17)
    assert ("20261008", "stk") in calls and ("20261009", "stk") not in calls
    assert ck["20261012"] == 200 and ev == []                              # 시드가 맞아 거짓 리셋이 없다
