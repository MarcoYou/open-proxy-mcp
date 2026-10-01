"""월간 업종 수집: 합성 HTTP 응답 경계 검증. 외부 HTTP·DB·파일 쓰기 0회."""
from __future__ import annotations

import argparse
import asyncio
import copy
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from scripts import refresh_sector_class as S


ANCHOR = "20260925"
SENTINEL = {"info": {"TRD_DT": S.NO_DATA_TRADE_DATE}, "list": []}
TREE = [{"title": "WICS", "children": [
    {"key": f"G{s + 10}", "title": f"sector {s}", "children": [
        {"key": f"G{s * 2 + i + 1000}", "title": f"industry {s * 2 + i}"}
        for i in range(2)
    ]} for s in range(10)
]}]


def payload(day: str, group: int) -> dict:
    return {"info": {"TRD_DT": datetime.strptime(day, "%Y%m%d").date().isoformat()},
            "list": [{"CMP_CD": f"{group * 50 + n:06}"} for n in range(50)]}


@pytest.fixture
def upstream(monkeypatch):
    original = httpx.AsyncClient
    calls, throttled = [], []

    async def throttle(counter):
        throttled.append(counter)

    monkeypatch.setattr(S, "throttle_web_request", throttle)

    def install(respond=None, tree=None):
        def handler(request):
            calls.append(request)
            if request.url.path == "/API/Tree/Get":
                result = TREE if tree is None else tree
            else:
                day = request.url.params["dt"]
                group = int(request.url.params["sec_cd"][1:]) - 1000
                result = respond(day, group) if respond else payload(day, group)
            if isinstance(result, Exception):
                raise result
            if isinstance(result, httpx.Response):
                return result
            return httpx.Response(200, json=result)
        monkeypatch.setattr(S.httpx, "AsyncClient", lambda **kw: original(
            **kw, transport=httpx.MockTransport(handler)))
        return calls, throttled

    return install


def run(day=ANCHOR, lookback=0):
    return asyncio.run(S.fetch_snapshot(day, lookback_days=lookback))


def test_exact_snapshot_and_probe_reuse(upstream, capsys):
    calls, paced = upstream()
    snapshot = run()
    assert snapshot["asOf"] == "2026-09-25"
    assert snapshot["requestedDate"] == ANCHOR
    assert snapshot["tickerCount"] == 1000
    assert len(calls) == 21 == len(paced)  # one tree + twenty distinct groups, no duplicate probe
    assert "사유: 요청일 일치" in capsys.readouterr().out


def test_holiday_no_data_uses_latest_available_day(upstream, capsys):
    calls, _ = upstream(lambda day, group: SENTINEL if day in {ANCHOR, "20260924"} else payload(day, group))
    snapshot = run(lookback=14)
    assert (snapshot["requestedDate"], snapshot["asOf"]) == (ANCHOR, "2026-09-23")
    assert [str(r.url.params.get("dt")) for r in calls[1:4]] == [ANCHOR, "20260924", "20260923"]
    assert len(calls) == 23
    out = capsys.readouterr().out
    assert "명시적 무자료" in out and "소급 2일" in out


def test_weekend_search_skips_nontrading_days(upstream):
    calls, _ = upstream()
    assert run("20260927", 14)["asOf"] == "2026-09-25"
    assert {r.url.params["dt"] for r in calls[1:]} == {ANCHOR}


def test_exact_weekend_does_not_silently_fallback(upstream):
    calls, _ = upstream()
    with pytest.raises(RuntimeError, match="유효 거래일 없음"):
        run("20260927")
    assert len(calls) == 1  # no component requests for a known weekend


def test_inclusive_lookback_boundary(upstream):
    calls, _ = upstream(lambda day, group: payload(day, group) if day == "20260911" else SENTINEL)
    assert run(lookback=14)["asOf"] == "2026-09-11"
    assert len(calls) == 31  # tree + ten no-data probes + twenty selected groups


def test_exhaustion_is_bounded_and_fails_closed(upstream):
    calls, _ = upstream(lambda *_: SENTINEL)
    with pytest.raises(RuntimeError, match="최대 소급 14일"):
        run(lookback=14)
    days = [r.url.params["dt"] for r in calls[1:]]
    assert days[-1] == "20260911" and len(days) == 11
    assert len(calls) == 12


@pytest.mark.parametrize("day", ["20260230", "20261301", "2026092", "2026-09-25", "abcdefgh", "２０２６０９２５", None])
def test_invalid_request_date_fails_before_http(upstream, day):
    calls, _ = upstream()
    with pytest.raises(ValueError, match="실제 날짜"):
        run(day)
    assert not calls


@pytest.mark.parametrize("lookback", [-1, 15, True, 1.5, "14"])
def test_invalid_lookback_fails_before_http(upstream, lookback):
    calls, _ = upstream()
    with pytest.raises(ValueError, match="lookback-days"):
        run(lookback=lookback)
    assert not calls


@pytest.mark.parametrize("group_index", [0, 3])
@pytest.mark.parametrize("bad_day", ["2026-09-23", "2026-09-28", "0001-01-01", None, "invalid", "/Date(-1)/", "/Date(9999999999999999999999999)/"])
def test_stale_future_missing_invalid_or_mixed_dates_abort(upstream, group_index, bad_day):
    def response(day, group):
        result = payload(day, group)
        if group == group_index:
            result["info"]["TRD_DT"] = bad_day
        return result
    calls, _ = upstream(response)
    with pytest.raises((RuntimeError, ValueError)):
        run(lookback=14)
    assert len(calls) == group_index + 2
    assert {r.url.params["dt"] for r in calls[1:]} == {ANCHOR}


@pytest.mark.parametrize("bad", [
    {}, [], {"list": []}, {"info": None, "list": []},
    {"info": {"TRD_DT": S.NO_DATA_TRADE_DATE}},
    {"info": {"TRD_DT": S.NO_DATA_TRADE_DATE}, "list": None},
    {"info": {"TRD_DT": S.NO_DATA_TRADE_DATE}, "list": {}},
    {"info": {"TRD_DT": S.NO_DATA_TRADE_DATE}, "list": ""},
    {"info": {"TRD_DT": S.NO_DATA_TRADE_DATE}, "list": [{"CMP_CD": "000001"}]},
    {"info": {"TRD_DT": "2026-09-25"}, "list": []},
    {"info": {"TRD_DT": "2026-09-25"}, "list": [None]},
    {"info": {"TRD_DT": "2026-09-25"}, "list": [{"CMP_CD": "bad"}]},
])
def test_no_data_is_not_a_catch_all_for_schema_errors(upstream, bad):
    calls, _ = upstream(lambda *_: bad)
    with pytest.raises((RuntimeError, ValueError)):
        run(lookback=14)
    assert len(calls) == 2


def test_partial_no_data_aborts_instead_of_older_snapshot(upstream):
    calls, _ = upstream(lambda day, group: SENTINEL if group == 3 else payload(day, group))
    with pytest.raises(RuntimeError, match="일부 하위업종만 무자료"):
        run(lookback=14)
    assert len(calls) == 5


@pytest.mark.parametrize("failure", [
    httpx.Response(403), httpx.Response(429), httpx.Response(500),
    httpx.Response(302, headers={"Location": "/blocked"}),
    httpx.Response(200, text="not JSON"),
    httpx.ConnectError("synthetic-private-secret"), httpx.ReadTimeout("synthetic-private-secret"),
])
def test_upstream_errors_stop_without_date_retry(upstream, failure):
    calls, _ = upstream(lambda *_: failure)
    with pytest.raises(RuntimeError) as caught:
        run(lookback=14)
    assert "synthetic-private-secret" not in str(caught.value)
    assert len(calls) == 2


def test_duplicate_ticker_aborts(upstream):
    calls, _ = upstream(lambda day, group: payload(day, 0 if group == 1 else group))
    with pytest.raises(RuntimeError, match="분류 충돌"):
        run(lookback=14)
    assert len(calls) == 3


def test_insufficient_tickers_fail_without_fallback(upstream):
    def response(day, group):
        result = payload(day, group)
        result["list"].pop()
        return result
    calls, _ = upstream(response)
    with pytest.raises(RuntimeError, match="완전성 검사 실패"):
        run(lookback=14)
    assert len(calls) == 21


@pytest.mark.parametrize("kind", ["short", "duplicate_sector", "duplicate_industry", "missing_key", "invalid_key", "missing_title", "bad_children", "bad_node"])
def test_bad_tree_rejected_before_components(upstream, kind):
    tree = copy.deepcopy(TREE)
    sectors = tree[0]["children"]
    if kind == "short":
        sectors.pop()
    elif kind == "duplicate_sector":
        sectors[-1]["key"] = sectors[0]["key"]
    elif kind == "duplicate_industry":
        sectors[-1]["children"][0]["key"] = sectors[0]["children"][0]["key"]
    elif kind == "missing_key":
        del sectors[0]["key"]
    elif kind == "invalid_key":
        sectors[0]["key"] = "G10&dt=other"
    elif kind == "missing_title":
        sectors[0]["title"] = ""
    elif kind == "bad_children":
        sectors[0]["children"] = {}
    else:
        sectors[0] = None
    calls, _ = upstream(tree=tree)
    with pytest.raises(RuntimeError):
        run(lookback=14)
    assert len(calls) == 1


@pytest.mark.parametrize("value,expected", [
    ("/Date(1790089200000)/", "2026-09-23"),
    ("/Date(1790089200000+0900)/", "2026-09-23"),
    ("2026-09-22T15:00:00+00:00", "2026-09-23"),
    ("2026-09-23T00:00:00", "2026-09-23"),
    ("2026-09-23", "2026-09-23"),
])
def test_trade_date_is_normalized_in_seoul(value, expected):
    assert S.parse_trade_date(value) == expected


def test_previous_friday_is_seoul_based_and_strictly_previous():
    assert S.previous_friday_kst(datetime(2026, 10, 1, tzinfo=timezone.utc)) == ANCHOR
    assert S.previous_friday_kst(datetime(2026, 9, 24, 16, tzinfo=timezone.utc)) == "20260918"


def args(**overrides):
    values = dict(date=None, lookback_days=None, dry_run=False, no_file=False, no_db=False, out=None)
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.mark.parametrize("explicit,env,lookback,expected", [
    (None, None, None, (ANCHOR, 14)),
    (ANCHOR, None, None, (ANCHOR, 0)),
    (None, ANCHOR, None, (ANCHOR, 0)),
    ("20260923", ANCHOR, None, ("20260923", 0)),
    (ANCHOR, None, 7, (ANCHOR, 7)),
    (None, None, 0, (ANCHOR, 0)),
])
def test_cli_date_semantics(monkeypatch, explicit, env, lookback, expected):
    monkeypatch.delenv("WICS_DATE", raising=False)
    if env:
        monkeypatch.setenv("WICS_DATE", env)
    monkeypatch.setattr(S, "previous_friday_kst", lambda: ANCHOR)
    got = []
    async def fetch(day, *, lookback_days):
        got.append((day, lookback_days))
        raise RuntimeError("stop before writes")
    monkeypatch.setattr(S, "fetch_snapshot", fetch)
    assert asyncio.run(S.main(args(date=explicit, lookback_days=lookback))) == 1
    assert got == [expected]


@pytest.mark.parametrize("failure", ["sentinel", "partial", "date", "network"])
def test_failed_snapshot_never_writes_file_or_db(upstream, monkeypatch, tmp_path, failure):
    path = tmp_path / "map.json"
    path.write_text("original")
    def response(day, group):
        if failure == "sentinel" or failure == "partial" and group == 1:
            return SENTINEL
        if failure == "date" and group == 1:
            return payload("20260923", group)
        if failure == "network":
            return httpx.ReadTimeout("synthetic-secret")
        return payload(day, group)
    upstream(response)
    writes = []
    monkeypatch.setattr(S, "write_file", lambda *_: writes.append("file"))
    monkeypatch.setattr(S, "persist", lambda *_: writes.append("db"))
    assert asyncio.run(S.main(args(date=ANCHOR, lookback_days=14, out=str(path)))) == 1
    assert not writes and path.read_text() == "original"


def test_successful_dry_run_never_writes(upstream, monkeypatch):
    upstream()
    writes = []
    monkeypatch.setattr(S, "write_file", lambda *_: writes.append("file"))
    monkeypatch.setattr(S, "persist", lambda *_: writes.append("db"))
    assert asyncio.run(S.main(args(date=ANCHOR, dry_run=True))) == 0
    assert not writes


def test_keyless_throttle_uses_existing_process_clock(monkeypatch):
    from open_proxy_mcp.dart import client as C
    monkeypatch.delenv("OPENDART_API_KEY", raising=False)
    monkeypatch.setattr(C, "_web_clock", C._WebClock())
    monkeypatch.setattr(C, "_WEB_INTERVAL_RANGE", (0, 0))
    monkeypatch.setattr(C, "_web_block", {"last_at": None})
    asyncio.run(C.throttle_web_request("fetch_sector_class"))
    assert len(C._web_clock.stamps) == 1
    assert C._web_clock.last > 0


@pytest.mark.parametrize("value", [
    "/Date(1790089200000+9999)/", "/Date(1790089200000+1260)/",
    "/Date(1790089200000-2400)/", "2026-09-25T00:00:00+00:60",
    "2026-09-25T00:00:00+24:00", "2026-09-25T00:00:00-0090",
    "2026-09-25T00:00:00+09:00:30", "2026-09-25X00:00:00",
])
def test_malformed_offsets_and_iso_forms_are_rejected(value):
    with pytest.raises(ValueError, match="유효하지 않은 TRD_DT"):
        S.parse_trade_date(value)
