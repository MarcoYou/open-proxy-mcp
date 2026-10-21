"""서버 갱신기의 KRX 공식 지수 종가 쓰기(`krx_idx`) — 해석 · 받기(엔드포인트 셋) · 갱신기 순서 · 보관 모양 정리 · 콜드 72시간 가드.

계약(P0 지수 종가, 마스터 10-21 「3년 모양」):
  ① 갱신기는 그날 시세(`krx_px`)를 기록한 뒤 **같은 권고 잠금 안에서** 지수 엔드포인트 셋을 부른다(하루 +3콜, 호출 장부 같이 셈).
  ② 지수 응답이 빈 것 하나로 휴장이라 읽지 않는다 — 시세 달력이 거래일로 적은 날만 부르고, 비면 「게시 전」으로 다음 틱에 다시(상한).
  ③ 사전에 없는 이름은 in_db=false 로 등록하고 경고한다 — 값은 넣지 않는다.
  ④ 정리: 창 밖 일별은 시세와 같은 날·같은 트랜잭션에서(주 닫는 날 = krx_wk_day 날짜만 남김), 3년 밖은 콜드 coverage.max_dd 이하이고
     그 판이 발행된 지 72시간(OPM_COLD_MANIFEST_MAX_AGE_SEC) 지난 뒤에만. 그 뒤 VACUUM (ANALYZE) krx_idx.
네트워크 0 · 운영 DB 0. Postgres 17 시험은 Docker 가 없으면 건너뛴다.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import pathlib
import sys
import time
from datetime import date, datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from test_krx_refresher import MON, Sim, _Resp, _fake_httpx, _rows, kst  # noqa: E402 — 시세 쪽 대역 재사용

from open_proxy_mcp import cold_store, krx_api, krx_daily, krx_idx  # noqa: E402
from open_proxy_mcp.services import krx_refresher as R  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv(krx_idx.ENV_FLAG, "1")                    # conftest 가 끈 것을 이 파일에서만 켠다
    for d in (R._LOGGED_FRESH, R._GAP_TRIES, R._IDX_TRIES, R._IDX_SETTLED):
        d.clear()
    R._BUSY_STREAK["n"] = R._COOLDOWN["n"] = 0
    yield
    for d in (R._LOGGED_FRESH, R._GAP_TRIES, R._IDX_TRIES, R._IDX_SETTLED):
        d.clear()
    R._BUSY_STREAK["n"] = R._COOLDOWN["n"] = 0


def _irow(name, close, dd="20261016", clss=None, src="P"):
    return {"BAS_DD": dd, "IDX_CLSS": clss or krx_idx.CLSS[src], "IDX_NM": name, "CLSPRC_IDX": close}


# ── 해석 ──────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,want", [("5,552.74", 555274), ("1000", 100000), ("0.01", 1), (" 12.3 ", 1230), ("", None), ("-", None), (None, None)])
def test_close_is_an_exact_times_100_integer(raw, want):
    assert krx_idx.close_x100(raw) == want


@pytest.mark.parametrize("raw", ["0", "-1.00", "12.345", "abc", "99999999.99"])
def test_bad_closes_are_refused_not_rounded(raw):
    with pytest.raises(ValueError):
        krx_idx.close_x100(raw)


def test_parse_keys_by_endpoint_and_trimmed_name_and_drops_what_it_cannot_trust():
    rows = {
        "P": [_irow(" 코스피 ", "2,500.10"), _irow("음식료·담배", "4,000.00"), _irow("코스피 (외국주포함)", ""),
              _irow("운송장비·부품", "1.234"), _irow("어제 날짜", "10.00", dd="20261015"), _irow("두 번", "1.00"), _irow("두 번", "2.00"),
              _irow("코스피 200", "")],
        "Q": [_irow("음식료·담배", "3,000.00", src="Q", clss="KOSDAQ")],
        "X": [_irow("K-샤프지수(1년)", "0.55", src="X"), _irow("KRX 300", "1,700.00", src="X", clss="KOSPI")],
    }
    p = krx_idx.parse_day("20261016", rows)
    # 같은 이름이 코스피·코스닥 양쪽에 있어도 열쇠(src, name)로 갈린다 · 이름 앞뒤 공백은 지운다
    assert p.closes == {("P", "코스피"): 250010, ("P", "음식료·담배"): 400000, ("Q", "음식료·담배"): 300000, ("X", "KRX 300"): 170000}
    text = " / ".join(p.problems)
    assert "운송장비·부품" in text and "어제 날짜" in text and "두 번" in text and "KRX 300 계열 분류" in text
    assert "외국주포함" not in text and "K-샤프" not in text                  # 제외 목록은 조용히 건너뛴다
    assert ("P", "코스피 200") not in p.closes                               # 종가 빈칸 — 값 없음(문제 아님)


def test_excluded_names_match_the_dictionary_build_and_sources_match_the_ddl():
    ddl_src = (ROOT / "scripts" / "krx_idx_ddl.py").read_text(encoding="utf-8")
    assert "CHECK (src IN ('P', 'Q', 'X'))" in ddl_src and krx_idx.SOURCES == ("P", "Q", "X")
    assert set(krx_api.IDX_ENDPOINTS.values()) == {"kospi_dd_trd", "kosdaq_dd_trd", "krx_dd_trd"}
    assert {why for why in krx_idx.EXCLUDED.values()} == {"no_close", "not_index_level"} and len(krx_idx.EXCLUDED) == 6
    assert krx_idx.LOOKBACK_DAYS < 60                                         # 서버 계정 INSERT 정책 창(최근 60일) 안


# ── 받기(엔드포인트 셋) ─────────────────────────────────────────────────────────────────────────────

def _idx_routes(p=None, q=None, x=None):
    def resp(v):
        if isinstance(v, (_Resp, Exception)):
            return v
        return _Resp({"OutBlock_1": v if v is not None else []})
    return {"kospi_dd_trd": resp(p), "kosdaq_dd_trd": resp(q), "krx_dd_trd": resp(x)}


def test_fetch_index_day_checks_kospi_first_and_costs_one_call_when_nothing_is_published(monkeypatch):
    log, bumps = _fake_httpx(monkeypatch, _idx_routes(p=[], q=[_irow("a", "1")], x=[_irow("b", "1")]))
    got = asyncio.run(krx_api.fetch_index_day("20261016"))
    assert (got.calls, got.status, len(bumps)) == (1, krx_api.EMPTY, 1)
    assert log == [("kospi_dd_trd", "20261016", True)]


def test_fetch_index_day_gets_all_three_endpoints_in_three_calls_and_keeps_four_fields(monkeypatch):
    p = [{**_irow("코스피", "2,500.10"), "OPNPRC_IDX": "1", "MKTCAP": "9"}]
    log, bumps = _fake_httpx(monkeypatch, _idx_routes(p=p, q=[_irow("코스닥", "800", src="Q")], x=[_irow("KRX 300", "1", src="X")]))
    got = asyncio.run(krx_api.fetch_index_day("20261016"))
    assert (got.calls, got.status, len(bumps)) == (3, krx_api.OK, 3)
    assert [x[0] for x in log] == ["kospi_dd_trd", "kosdaq_dd_trd", "krx_dd_trd"]
    assert got.rows["P"] == [{"BAS_DD": "20261016", "IDX_CLSS": "KOSPI", "IDX_NM": "코스피", "CLSPRC_IDX": "2,500.10"}]


def test_fetch_index_day_a_later_empty_endpoint_is_empty_not_ok(monkeypatch):
    _fake_httpx(monkeypatch, _idx_routes(p=[_irow("코스피", "1")], q=[], x=[_irow("x", "1")]))
    got = asyncio.run(krx_api.fetch_index_day("20261016"))
    assert (got.calls, got.status) == (2, krx_api.EMPTY)                    # 코스닥이 비면 KRX 계열은 묻지 않는다


@pytest.mark.parametrize("resp,status", [(_Resp({}, 401), krx_api.AUTH), (_Resp({}, 429), krx_api.LIMIT),
                                         (_Resp({}, 500), krx_api.HTTP_ERROR), (TimeoutError("t"), krx_api.TRANSPORT)])
def test_fetch_index_day_keeps_failures_apart_from_empty(monkeypatch, resp, status):
    _fake_httpx(monkeypatch, _idx_routes(p=[_irow("코스피", "1")], q=resp))
    got = asyncio.run(krx_api.fetch_index_day("20261016"))
    assert (got.calls, got.status) == (2, status)


def test_fetch_index_day_without_a_key_calls_nothing(monkeypatch):
    log, bumps = _fake_httpx(monkeypatch, _idx_routes(p=[_irow("코스피", "1")]))
    monkeypatch.delenv("KRX_API_KEY")
    monkeypatch.delenv("KRX_OPEN_API_KEY", raising=False)
    got = asyncio.run(krx_api.fetch_index_day("20261016"))
    assert (got.calls, got.status, log, bumps) == (0, krx_api.AUTH, [], [])


# ── 갱신기 순서(대역) ────────────────────────────────────────────────────────────────────────────

class IdxSim:
    """지수 쪽 DB·KRX 대역. pending = 지수가 빠진 거래일(최근 날부터). published = 지수가 게시된 날. order = 갱신기가 한 일의 순서."""

    def __init__(self, monkeypatch, sim, *, ready=True, pending=(), published=(), write_ok=True, err=None, cold="20231231"):
        self.sim, self.ready, self.pending, self.published = sim, ready, list(pending), set(published)
        self.write_ok, self.err, self.cold = write_ok, err, cold
        self.reads = self.fetches = 0
        self.order, self.written, self.vacuums, self.cold_seen = [], [], [], []
        monkeypatch.setattr(R, "_idx_pending", self._pending)
        monkeypatch.setattr(R, "_fetch_idx", self._fetch)
        monkeypatch.setattr(R, "_write_idx", self._write)
        monkeypatch.setattr(R, "_vacuum", lambda tables="krx_px, krx_day": self.vacuums.append(tables))

        async def cold_max():
            return self.cold
        monkeypatch.setattr(R, "_cold_max_dd", cold_max)
        real_write = sim._write

        async def px_write(dd, kospi, kosdaq):
            self.order.append(("px", dd, sim.locked))
            res = await real_write(dd, kospi, kosdaq)
            if res.daily_ok:
                self.pending.insert(0, dd)                    # 시세를 쓰면 그 날은 달력의 거래일 — 지수가 빠진 날이 된다
            return res
        monkeypatch.setattr(R, "_write", px_write)

    def _pending(self, today):
        self.reads += 1
        return krx_idx.Pending(self.ready, tuple(sorted(set(self.pending), reverse=True)) if self.ready else ())

    async def _fetch(self, dd):
        self.fetches += 1
        self.order.append(("idx", dd, self.sim.locked))
        if self.err:
            return krx_api.IdxFetch({"P": []}, 1, self.err)
        if dd not in self.published:
            return krx_api.IdxFetch({"P": []}, 1, krx_api.EMPTY)
        rows = {"P": [_irow("코스피", "2,500.00", dd)], "Q": [_irow("코스닥", "800.00", dd, src="Q")], "X": [_irow("KRX 300", "1,700.00", dd, src="X")]}
        return krx_api.IdxFetch(rows, 3, krx_api.OK)

    def _write(self, parsed, cold_max_dd):
        self.cold_seen.append(cold_max_dd)
        if not self.write_ok:
            return krx_idx.IdxWrite(False, reason="시험")
        self.written.append((parsed.dd, dict(parsed.closes)))
        self.pending.remove(parsed.dd)
        return krx_idx.IdxWrite(True, len(parsed.closes))


def test_index_is_fetched_after_the_price_write_inside_the_same_lock_and_costs_three_calls(monkeypatch):
    sim = Sim(monkeypatch, stored="20261015")
    ix = IdxSim(monkeypatch, sim, published={"20261016"})
    res = asyncio.run(R.run_cycle(MON))
    assert res.outcome == "refreshed" and res.krx_calls == 2
    assert (res.idx, res.idx_calls, res.idx_days) == ("written", 3, ("20261016",))
    assert ix.order == [("px", "20261016", True), ("idx", "20261016", True)]           # 시세 뒤 · 잠금을 쥔 채
    assert sim.lock_attempts == 1 and sim.locked is False
    assert ix.written == [("20261016", {("P", "코스피"): 250000, ("Q", "코스닥"): 80000, ("X", "KRX 300"): 170000})]
    assert ix.vacuums == ["krx_idx"] and ix.cold_seen == ["20231231"]


def test_an_empty_index_answer_is_not_published_never_a_holiday_and_is_retried_with_a_daily_cap(monkeypatch):
    sim = Sim(monkeypatch, stored="20261015")
    judged = []
    monkeypatch.setattr(R, "_judge_closed", lambda *a: judged.append(a) or "closed")
    ix = IdxSim(monkeypatch, sim, published=set())
    res = asyncio.run(R.run_cycle(MON))
    assert res.outcome == "refreshed" and (res.idx, res.idx_calls) == ("not_published", 1)
    assert judged == [] and sim.closed == set()                               # 지수 빈 응답으로 휴장을 적지 않는다
    # 시세는 최신 — 다음 틱들은 지수만 묻는다(잠금을 잡고 1콜). 하루 상한까지만.
    outs = []
    for i in range(1, R.IDX_TRIES_PER_DAY + 2):
        outs.append(asyncio.run(R.run_cycle(kst(2026, 10, 19, 8 + i // 6, 10 * (i % 6)))))
    assert [o.outcome for o in outs] == ["fresh"] * len(outs)
    assert ix.fetches == R.IDX_TRIES_PER_DAY
    assert [o.idx for o in outs] == ["not_published"] * (R.IDX_TRIES_PER_DAY - 1) + [None, None]
    reads = ix.reads
    asyncio.run(R.run_cycle(kst(2026, 10, 19, 11, 30)))                       # 상한에 닿은 뒤로는 그 기대 거래일에 DB 도 안 본다
    assert ix.reads == reads and ix.fetches == R.IDX_TRIES_PER_DAY
    ix.published.add("20261016")                                              # 다음 날(새 기대 거래일)에는 다시 센다
    sim.stored = "20261019"
    nxt = asyncio.run(R.run_cycle(kst(2026, 10, 20, 8, 0)))
    assert nxt.idx == "written" and ix.written[-1][0] == "20261016"


def test_a_late_index_is_fetched_alone_under_the_lock_and_then_the_day_is_settled(monkeypatch):
    sim = Sim(monkeypatch, stored="20261016")                                 # 시세는 이미 최신
    ix = IdxSim(monkeypatch, sim, pending=["20261016"], published={"20261016"})
    res = asyncio.run(R.run_cycle(MON))
    assert (res.outcome, res.krx_calls, res.idx, res.idx_calls) == ("fresh", 0, "written", 3)
    assert sim.lock_attempts == 1 and ix.order == [("idx", "20261016", True)]
    again = asyncio.run(R.run_cycle(kst(2026, 10, 19, 8, 10)))
    assert again.idx is None and sim.lock_attempts == 1
    assert ix.reads == 3                                                      # 잠금 앞·뒤 + 다음 틱 「더 없음」 확인 한 번
    asyncio.run(R.run_cycle(kst(2026, 10, 19, 8, 20)))
    assert ix.reads == 3                                                      # 그 뒤로는 DB 도 안 본다


def test_index_only_tick_respects_the_other_machines_lock(monkeypatch):
    sim = Sim(monkeypatch, stored="20261016", lock_held_by_other=True)
    ix = IdxSim(monkeypatch, sim, pending=["20261016"], published={"20261016"})
    res = asyncio.run(R.run_cycle(MON))
    assert res.idx == "lock_busy" and ix.fetches == 0


def test_switch_off_or_tables_not_ready_cost_no_krx_and_no_lock(monkeypatch, caplog):
    sim = Sim(monkeypatch, stored="20261016")
    ix = IdxSim(monkeypatch, sim, ready=False)
    with caplog.at_level(logging.INFO, logger="open_proxy_mcp.services.krx_refresher"):
        res = asyncio.run(R.run_cycle(MON))
    assert res.idx is None and sim.lock_attempts == 0 and ix.fetches == 0
    assert any("지수 종가 표·사전" in r.getMessage() for r in caplog.records)
    R._IDX_SETTLED.clear()
    monkeypatch.setenv(krx_idx.ENV_FLAG, "0")
    sim.stored = "20261015"
    ix.ready = True
    ix.published.add("20261016")
    res = asyncio.run(R.run_cycle(MON))
    assert res.outcome == "refreshed" and res.idx == "off" and ix.fetches == 0 and ix.reads == 1


def test_a_price_side_krx_failure_does_not_spend_index_calls(monkeypatch):
    sim = Sim(monkeypatch, stored="20261015", krx_error=krx_api.LIMIT)
    ix = IdxSim(monkeypatch, sim, pending=["20261015"], published={"20261015"})
    res = asyncio.run(R.run_cycle(MON))
    assert res.outcome == "krx_error" and res.idx is None and ix.fetches == 0


def test_index_krx_error_and_write_failure_stop_the_tick(monkeypatch):
    sim = Sim(monkeypatch, stored="20261016")
    ix = IdxSim(monkeypatch, sim, pending=["20261016", "20261015"], published={"20261016", "20261015"}, err=krx_api.AUTH)
    res = asyncio.run(R.run_cycle(MON))
    assert (res.idx, res.idx_calls, ix.fetches) == ("krx_error", 1, 1)
    ix.err = None
    ix.write_ok = False
    res = asyncio.run(R.run_cycle(kst(2026, 10, 19, 8, 10)))
    assert res.idx == "write_failed" and ix.fetches == 2 and ix.vacuums == []


def test_gap_days_are_capped_per_tick(monkeypatch):
    days = [f"202610{d:02d}" for d in (16, 15, 14, 13, 12)]
    sim = Sim(monkeypatch, stored="20261016")
    ix = IdxSim(monkeypatch, sim, pending=days, published=set(days))
    res = asyncio.run(R.run_cycle(MON))
    assert res.idx_days == tuple(days[:R.IDX_FILL_PER_TICK]) and res.idx_calls == 3 * R.IDX_FILL_PER_TICK


# ── 콜드 72시간 가드 ─────────────────────────────────────────────────────────────────────────────

def _cold_with_built_at(cold_serving, built_at, *, max_dd="20231013"):
    info = cold_serving([("K00000", "20231013", "KS", 1, 1)], upto="20231014", dataset=krx_idx.COLD_DATASET, max_dd=max_dd)
    m = json.loads(info["manifest_path"].read_text())
    if built_at is not None:
        m["datasets"][krx_idx.COLD_DATASET]["files"][0]["built_at"] = built_at
    info["manifest_path"].write_text(json.dumps(m))
    return info


def _iso(seconds_ago):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat(timespec="seconds")


@pytest.mark.parametrize("hours,want", [(72 + 1 / 60, "20231013"), (71, None), (0, None)])
def test_cold_coverage_is_usable_only_72_hours_after_publication(cold_serving, hours, want):
    _cold_with_built_at(cold_serving, _iso(hours * 3600))
    assert asyncio.run(cold_store.settled_max_dd(krx_idx.COLD_DATASET)) == want


@pytest.mark.parametrize("built_at", [None, "어제", "2026-10-01T00:00:00"])          # 없음 · 깨짐 · 시간대 없음
def test_cold_coverage_without_a_trustworthy_publication_time_is_never_used(cold_serving, built_at):
    _cold_with_built_at(cold_serving, built_at)
    assert asyncio.run(cold_store.settled_max_dd(krx_idx.COLD_DATASET)) is None


def test_cold_off_or_missing_dataset_means_no_old_pruning(cold_serving, monkeypatch):
    _cold_with_built_at(cold_serving, _iso(100 * 3600))
    assert asyncio.run(cold_store.settled_max_dd("no_such_dataset")) is None
    monkeypatch.setenv("OPM_COLD", "0")
    assert asyncio.run(cold_store.settled_max_dd(krx_idx.COLD_DATASET)) is None


def test_the_72_hour_rule_follows_the_manifest_max_age_setting(cold_serving, monkeypatch):
    _cold_with_built_at(cold_serving, _iso(3 * 3600))
    monkeypatch.setenv("OPM_COLD_MANIFEST_MAX_AGE_SEC", str(2 * 3600))
    assert asyncio.run(cold_store.settled_max_dd(krx_idx.COLD_DATASET)) == "20231013"


# ── Postgres 17: 쓰기·보관 모양 끝에서 끝까지(소유자 계정 — 권한은 test_krx_daily_least_privilege_pg) ────────────

psycopg = pytest.importorskip("psycopg")


def _load(path, name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def idx_schema(url):
    """일별(krx_px·krx_day·krx_ticker) + 주간 뷰 모양(krx_wk·krx_wk_day — 운영과 같은 뷰 모드) + 지수 표 둘."""
    _load("krx_daily_ddl.py", "krx_daily_ddl").apply(url)
    _load("krx_weekly_ddl.py", "krx_weekly_ddl").apply(url, "full")
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(_load("krx_idx_ddl.py", "krx_idx_ddl").STATEMENTS)


SERIES = [(1, "P", "코스피", "z", True, "kospi200"), (2, "Q", "코스닥", "z", True, None), (3, "X", "KRX 300", "z", True, None),
          (4, "P", "음식료·담배", "s", True, None), (5, "Q", "음식료·담배", "s", True, None), (6, "X", "KRX 헬스케어", "t", False, None)]


def seed_series(c):
    for sid, src, name, kind, in_db, code in SERIES:
        c.execute("INSERT INTO krx_idx_series (sid, src, name, kind, in_db, idx_code) VALUES (%s, %s, %s, %s, %s, %s)",
                  (sid, src, name, kind, in_db, code))
    c.execute("SELECT setval('krx_idx_series_sid_seq', 6)")


def idx_rows_for(dd, *, extra=()):
    base = int(dd[-3:])
    return {"P": [_irow("코스피", f"{2000 + base}.01", dd), _irow("음식료·담배", f"{3000 + base}.50", dd), *extra],
            "Q": [_irow("코스닥", f"{700 + base}.00", dd, src="Q"), _irow("음식료·담배", f"{300 + base}.25", dd, src="Q")],
            "X": [_irow("KRX 300", f"{1500 + base}.75", dd, src="X"), _irow("KRX 헬스케어", "999.99", dd, src="X")]}


def _px(dd, n=3):
    from test_krx_daily_pg import _row
    base = int(dd[-4:])
    return [_row(f"K{i:05d}", base * 10 + i, cmp=0) for i in range(n)], [_row(f"Q{i:05d}", base + i, cmp=0) for i in range(n)]


def _q(url, sql, params=()):
    with psycopg.connect(url) as c:
        return c.execute(sql, params).fetchall()


def write_idx(url, dd, rows=None, cold=None):
    with psycopg.connect(url) as c:
        with c.transaction():
            res = krx_idx.write_day(c, krx_idx.parse_day(dd, rows or idx_rows_for(dd)))
            if res.ok:
                res = krx_idx.IdxWrite(True, res.rows, res.new, res.absent, krx_idx.prune_old(c, cold))
    return res


def _weekdays(lo: date, hi: date):
    d = lo
    while d <= hi:
        if d.isoweekday() < 6:
            yield d.strftime("%Y%m%d")
        d += timedelta(days=1)


@pytest.fixture
def idb(pg17, monkeypatch):
    idx_schema(pg17)
    with psycopg.connect(pg17, autocommit=True) as c:
        seed_series(c)
    monkeypatch.setenv("DATABASE_URL", pg17)
    import open_proxy_mcp.db as dbm
    monkeypatch.setattr(dbm, "_pool", None)
    monkeypatch.setattr(dbm, "_pool_disabled_until", float("inf"))
    return pg17


def test_write_day_puts_only_db_series_registers_new_names_and_pushes_the_span(idb, caplog):
    extra = (_irow("코스피 신규지수", "1,234.56", "20261016"),)
    res = write_idx(idb, "20261016", idx_rows_for("20261016", extra=extra))
    assert res.ok and res.rows == 5 and res.new == ("P|코스피 신규지수",) and res.absent == ()
    assert _q(idb, "SELECT sid, trd_dd::text, close_x100 FROM krx_idx ORDER BY 1") == [
        (1, "2026-10-16", 201601), (2, "2026-10-16", 71600), (3, "2026-10-16", 151675), (4, "2026-10-16", 301650), (5, "2026-10-16", 31625)]
    # 새 이름 — in_db=false·종류 t·범위 비움, 번호는 시퀀스가 이어 매긴다. 콜드 전용 계열(KRX 헬스케어)은 값을 넣지 않는다
    assert _q(idb, "SELECT sid, src::text, name, kind::text, in_db, first_dd, last_dd FROM krx_idx_series WHERE sid > 5 ORDER BY 1") == [
        (6, "X", "KRX 헬스케어", "t", False, None, None), (7, "P", "코스피 신규지수", "t", False, None, None)]
    assert _q(idb, "SELECT min(first_dd)::text, max(last_dd)::text FROM krx_idx_series WHERE in_db") == [("2026-10-16", "2026-10-16")]
    # 같은 날을 다시 쓰면 그 날 행을 통째로 바꾼다 · 새 이름은 한 번만
    res2 = write_idx(idb, "20261016", idx_rows_for("20261016", extra=extra))
    assert res2.ok and res2.new == () and _q(idb, "SELECT count(*) FROM krx_idx") == [(5,)]


def test_a_half_answer_is_not_written_and_leaves_no_new_names(idb):
    rows = {"P": [_irow("코스피", "1.00", "20261016"), _irow("새 이름", "2.00", "20261016")], "Q": [], "X": []}
    res = write_idx(idb, "20261016", rows)
    assert not res.ok and "1/5" in res.reason
    assert _q(idb, "SELECT count(*) FROM krx_idx") == [(0,)] and _q(idb, "SELECT count(*) FROM krx_idx_series") == [(6,)]


def test_pending_is_trading_days_in_the_calendar_without_an_index_row(idb):
    with psycopg.connect(idb) as c:
        for dd in ("20261014", "20261015", "20261016"):
            krx_daily.write_daily(c, dd, *_px(dd))
        krx_daily.record_closed(c, "20261009")
        c.commit()
    write_idx(idb, "20261015")
    from open_proxy_mcp.db import pg_rows
    p = krx_idx.pending(pg_rows, "20261019")
    assert p == krx_idx.Pending(True, ("20261016", "20261014"))
    assert krx_idx.pending(pg_rows, "20261212") == krx_idx.Pending(True, ())          # 45일 밖은 보지 않는다
    with psycopg.connect(idb, autocommit=True) as c:
        c.execute("UPDATE krx_idx_series SET in_db = false")
    assert krx_idx.pending(pg_rows, "20261019") == krx_idx.Pending(False)            # DB 계열이 없으면(적재 전) 아무것도 안 한다


def test_pending_before_the_ddl_is_not_ready_without_a_query_error(pg17, monkeypatch, caplog):
    _load("krx_daily_ddl.py", "krx_daily_ddl").apply(pg17)
    monkeypatch.setenv("DATABASE_URL", pg17)
    import open_proxy_mcp.db as dbm
    monkeypatch.setattr(dbm, "_pool", None)
    monkeypatch.setattr(dbm, "_pool_disabled_until", float("inf"))
    from open_proxy_mcp.db import pg_rows
    with caplog.at_level(logging.WARNING):
        assert krx_idx.pending(pg_rows, "20261019") == krx_idx.Pending(False)
    assert not [r for r in caplog.records if "질의 오류" in r.getMessage()]
    # 시세 정리도 지수 표가 없으면 그대로 돈다
    with psycopg.connect(pg17) as c:
        assert krx_daily.write_daily(c, "20261016", *_px("20261016"))[0]
        c.commit()


def test_rolling_keeps_the_three_year_shape_daily_window_plus_weekly_closes(idb, monkeypatch):
    """보관 창을 2주로 줄여 6주를 굴린다 — 창 밖으로 나간 날 중 주 닫는 날(krx_wk_day)만 남고, 콜드에 안 들어간 날은 시세와 함께 미룬다."""
    monkeypatch.setattr(krx_daily, "KEEP_YEARS", 0)
    monkeypatch.setattr(krx_daily, "KEEP_EXTRA_DAYS", 14)
    days = list(_weekdays(date(2026, 9, 1), date(2026, 10, 16)))
    for dd in days:
        with psycopg.connect(idb) as c:
            c.execute("UPDATE krx_day SET cold = true WHERE dd < %s AND dd <> '20260915'", (dd,))   # 맥 보관본 흉내(9/15 만 빠짐)
            assert krx_daily.write_daily(c, dd, *_px(dd))[0]
            c.commit()
        assert write_idx(idb, dd).ok
    lo = krx_daily.window_lo(days[-1])
    cal = {r[0] for r in _q(idb, "SELECT dd FROM krx_day WHERE n > 0")}
    wk = {r[0] for r in _q(idb, "SELECT dd FROM krx_wk_day")}
    have = {r[0] for r in _q(idb, "SELECT DISTINCT to_char(trd_dd, 'YYYYMMDD') FROM krx_idx")}
    assert have == cal | wk                                                   # 지수 날짜 = 시세 달력 ∪ 주 달력, 정확히
    assert {d for d in have if d < lo} == wk | {"20260915"}                   # 창 밖은 주 닫는 날 + 콜드에 없어 시세도 남긴 날뿐
    assert "20260911" in wk and "20260910" not in have                        # 금요일(주 닫는 날)은 남고 목요일은 지워졌다
    per_day = _q(idb, "SELECT count(*) FROM krx_idx GROUP BY trd_dd")
    assert {n for (n,) in per_day} == {5}


def test_a_failed_index_prune_holds_the_price_prune_for_that_day(idb, monkeypatch, caplog):
    monkeypatch.setattr(krx_daily, "KEEP_YEARS", 0)
    monkeypatch.setattr(krx_daily, "KEEP_EXTRA_DAYS", 14)
    for dd in ("20260901", "20260902"):
        with psycopg.connect(idb) as c:
            krx_daily.write_daily(c, dd, *_px(dd))
            c.commit()
        write_idx(idb, dd)
    with psycopg.connect(idb, autocommit=True) as c:
        c.execute("UPDATE krx_day SET cold = true")
    monkeypatch.setattr(krx_idx, "PRUNE_DAILY_SQL", "DELETE FROM no_such_table")
    with caplog.at_level(logging.ERROR, logger="open_proxy_mcp.krx_idx"):
        with psycopg.connect(idb) as c:
            ok, n, pruned = krx_daily.write_daily(c, "20261016", *_px("20261016"))
            c.commit()
    # 9/1 은 지수 행을 못 지워 시세·달력도 남긴다. 9/2(그 주의 닫는 날)는 주 달력으로 옮겨져 지수 행은 원래 남는 날이라 시세 정리가 진행된다
    assert ok and pruned == 6                                                 # 9/2 의 시세 6행만
    assert _q(idb, "SELECT dd FROM krx_day WHERE dd IN ('20260901','20260902')") == [("20260901",)]
    assert _q(idb, "SELECT to_char(trd_dd, 'YYYYMMDD'), count(*) FROM krx_idx WHERE trd_dd < '2026-10-01' GROUP BY 1 ORDER BY 1") == [
        ("20260901", 5), ("20260902", 5)]
    assert any("시세 정리도 미룬다" in r.getMessage() for r in caplog.records)


def test_old_weekly_points_go_only_inside_settled_cold_coverage_and_only_weekly_points(idb):
    """3년 하한(마지막 주 닫는 날 − 3년) 이전의 주 달력 날 중 콜드 coverage 안만 지운다. coverage 가 없으면(발행 72시간 전·콜드 꺼짐) 아무것도."""
    with psycopg.connect(idb) as c:
        for dd in ("20261015", "20261016"):
            krx_daily.write_daily(c, dd, *_px(dd))
        c.commit()
    old = ["20221230", "20230929", "20231013", "20231020"]                    # 하한 = 20231016(20261016 의 3년 전)
    with psycopg.connect(idb, autocommit=True) as c:
        for dd in old:
            c.execute("INSERT INTO krx_wk_day (wk_dd, dd, n) VALUES (%s, %s, 0)", (date(int(dd[:4]), int(dd[4:6]), int(dd[6:])), dd))
            c.execute("INSERT INTO krx_idx (sid, trd_dd, close_x100) VALUES (1, %s, 100)", (dd,))
        c.execute("INSERT INTO krx_idx (sid, trd_dd, close_x100) VALUES (2, '2022-06-01', 100)")   # 주 달력에 없는 옛 행(고아)
    assert write_idx(idb, "20261016", cold=None).old == 0
    assert write_idx(idb, "20261016", cold="20230930").old == 2                 # 20221230·20230929 — coverage 끝까지만
    assert write_idx(idb, "20261016", cold="20261231").old == 1                 # 20231013 — 하한 밖. 20231020 은 3년 안이라 남는다
    left = [r[0] for r in _q(idb, "SELECT to_char(trd_dd, 'YYYYMMDD') FROM krx_idx WHERE trd_dd < '2024-01-01' ORDER BY 1")]
    assert left == ["20220601", "20231020"]


def test_refresher_end_to_end_on_postgres_with_fake_krx(idb, monkeypatch):
    """갱신기 한 틱 — 잠금 → 시세 기록 → 같은 잠금 안에서 지수 셋 → 기록 → VACUUM(ANALYZE) krx_idx. KRX 는 가짜(5콜), DB 는 PG17."""
    routes = {"stk_bydd_trd": _Resp({"OutBlock_1": [r for r in _rows(400, "K")]}),
              "ksq_bydd_trd": _Resp({"OutBlock_1": [r for r in _rows(400, "Q")]})}
    for k, v in idx_rows_for("20261016").items():
        routes[krx_api.IDX_ENDPOINTS[k]] = _Resp({"OutBlock_1": v})
    log, bumps = _fake_httpx(monkeypatch, routes)
    for r in routes["stk_bydd_trd"].payload["OutBlock_1"] + routes["ksq_bydd_trd"].payload["OutBlock_1"]:
        r.update({"BAS_DD": "20261016", "TDD_OPNPRC": "1,000", "TDD_HGPRC": "1,000", "TDD_LWPRC": "1,000", "CMPPREVDD_PRC": "0",
                  "ACC_TRDVOL": "1", "ACC_TRDVAL": "1", "LIST_SHRS": "10"})
    vac = []
    real_vac = R._vacuum
    monkeypatch.setattr(R, "_vacuum", lambda tables="krx_px, krx_day": (vac.append(tables), real_vac(tables)))
    with psycopg.connect(idb) as c:
        krx_daily.write_daily(c, "20261015", *_px("20261015"))
        c.commit()
    write_idx(idb, "20261015")
    res = asyncio.run(R.run_cycle(MON))
    assert (res.outcome, res.krx_calls, res.idx, res.idx_calls) == ("refreshed", 2, "written", 3)
    assert [x[0] for x in log] == ["stk_bydd_trd", "ksq_bydd_trd", "kospi_dd_trd", "kosdaq_dd_trd", "krx_dd_trd"] and len(bumps) == 5
    assert _q(idb, "SELECT count(*) FROM krx_idx WHERE trd_dd = '2026-10-16'") == [(5,)]
    assert vac == ["krx_px, krx_day", "krx_idx"]
    assert _q(idb, "SELECT last_analyze IS NOT NULL OR last_autoanalyze IS NOT NULL FROM pg_stat_user_tables WHERE relname='krx_idx'") == [(True,)]
    # 다음 틱 — 지수까지 다 있으니 KRX 0콜 · 잠금 없음
    again = asyncio.run(R.run_cycle(kst(2026, 10, 19, 8, 10)))
    assert (again.outcome, again.idx, len(log)) == ("fresh", None, 5)
