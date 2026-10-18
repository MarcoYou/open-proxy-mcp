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


# ── 261018: 구성종목 ≠ 분류 대상 — 파생 행(이어 쓰기·우선주) ─────────────────────────────

CLS_A = ("G25", "경기관련소비재", "G2510", "자동차와부품")
CLS_B = ("G45", "IT", "G4530", "반도체와반도체장비")


def _rows_by_ticker(rows):
    return {r[0]: r for r in rows}


def test_carry_only_listed_tickers_dropped_from_constituents():
    """직전 관측에 있었고 지금도 상장인데 구성종목에서만 빠진 종목만 잇는다 — 상장폐지 종목은 되살리지 않는다."""
    prev = {"000040": (CLS_A, "wise", None), "000050": (CLS_A, "wise", None), "005930": (CLS_B, "wise", None)}
    observed = {"005930": CLS_B}
    out = _rows_by_ticker(S.derive_rows(observed, prev, "20260828", listed={"000040", "005930"}))
    assert out == {"000040": ("000040", *CLS_A, "carry", "20260828")}


def test_carry_chain_keeps_the_date_actually_observed():
    """이어 쓴 행을 다시 이을 때 출처 날짜는 실제로 관측한 날짜 그대로다(중간 스냅샷 날짜로 바꾸지 않는다)."""
    prev = {"000040": (CLS_A, "carry", "20260821")}
    out = S.derive_rows({}, prev, "20260828", listed={"000040"})
    assert out == [("000040", *CLS_A, "carry", "20260821")]


def test_observed_wins_over_carry_and_reclassification_is_not_overridden():
    """다시 관측된 종목은 관측 행이 이긴다 — 공급처가 다른 업종으로 옮겼으면 그 분류가 맞다."""
    prev = {"000040": (CLS_A, "wise", None)}
    assert S.derive_rows({"000040": CLS_B}, prev, "20260828", listed={"000040"}) == []


def test_preferred_inherits_common_class_including_carried_common():
    """우선주(끝자리 ≠ 0)는 같은 회사 보통주(앞 5자리 + 0)의 이 스냅샷 분류를 잇는다 — 보통주가 이어 쓰기여도."""
    prev = {"000040": (CLS_A, "wise", None)}
    observed = {"005930": CLS_B}
    listed = {"005930", "005935", "000040", "000045", "00104K", "123457"}
    out = _rows_by_ticker(S.derive_rows(observed, prev, "20260828", listed))
    assert out["005935"] == ("005935", *CLS_B, "pref", "005930")
    assert out["000045"] == ("000045", *CLS_A, "pref", "000040")
    assert "00104K" not in out and "123457" not in out  # 보통주 분류가 없으면 지어내지 않는다
    assert out["000040"][5] == "carry"


def test_preferred_rows_are_rederived_not_carried():
    """직전 스냅샷의 우선주 파생 행은 잇지 않는다 — 보통주가 이번에 다른 업종이면 우선주도 그 업종이다."""
    prev = {"005935": (CLS_A, "pref", "005930"), "005930": (CLS_A, "wise", None)}
    out = S.derive_rows({"005930": CLS_B}, prev, "20260828", listed={"005930", "005935"})
    assert out == [("005935", *CLS_B, "pref", "005930")]


def test_unobserved_common_is_never_invented():
    """공급처가 한 번도 분류를 준 적 없는 보통주는 파생 행을 만들지 않는다."""
    assert S.derive_rows({"005930": CLS_B}, {}, None, listed={"005930", "950160", "001570"}) == []


def test_rederive_flag_skips_fetch(monkeypatch):
    called = []

    async def fetch(*a, **k):
        called.append("fetch")
        raise AssertionError("수집하면 안 된다")
    monkeypatch.setattr(S, "fetch_snapshot", fetch)
    monkeypatch.setattr(S, "rederive_db", lambda dry_run=False: called.append(("rederive", dry_run)) or 0)
    assert asyncio.run(S.main(args(rederive=True, dry_run=True))) == 0
    assert called == [("rederive", True)]


# ── 261018: 두 번째 관측 원천 — 공급처 기업 정보 화면 ─────────────────────────────────────────

CLS_C = ("G35", "건강관리", "G3520", "제약과생물공학")


def _page(ticker, wics="생물공학", shown=None):
    line = "" if wics is None else f'<dt class="line-left">WICS : {wics}</dt>'
    return (f'<html><dl><dt><span class="name">회사</span><b class="num">{shown or ticker}</b></dt>'
            f'<dt class="line-left">KOSDAQ : 코스닥 제약</dt>{line}</dl>' + " " * 5000 + "</html>")


def test_parse_company_page_reads_the_industry_line_of_that_ticker_only():
    assert S.parse_company_page(_page("950160"), "950160") == "생물공학"
    assert S.parse_company_page(_page("950160", wics=None), "950160") is None
    assert S.parse_company_page(_page("950160", wics=" "), "950160") is None
    with pytest.raises(S.CompanyPageError):
        S.parse_company_page(_page("950160", shown="005930"), "950160")  # 다른 종목 화면의 분류를 쓰지 않는다
    with pytest.raises(S.CompanyPageError):
        S.parse_company_page("<html>오류</html>", "950160")


@pytest.fixture
def screens(monkeypatch):
    """기업 화면 모의 상류. pages: {ticker: 산업 이름 | None(분류 줄 없음) | httpx.Response | Exception}."""
    calls = []

    async def throttle(counter):
        calls.append(("throttle", counter))

    monkeypatch.setattr(S, "throttle_web_request", throttle)

    def make(pages, **kw):
        def handler(request):
            t = request.url.params["cmp_cd"]
            calls.append(t)
            v = pages.get(t, "없음")
            if isinstance(v, Exception):
                raise v
            if isinstance(v, httpx.Response):
                return v
            return httpx.Response(200, text=_page(t, wics=v))
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers=S.COMPANY_HEADERS,
                                   follow_redirects=False)
        return S.CompanyLookup(client, today="20261018", **kw)
    return make, calls


CMAP = {"생물공학": {"industryCode": "G3520", "exemplars": ["068270", "196170", "000100"]},
        "자동차": {"industryCode": "G2510", "exemplars": ["005380"]}}
CONS = {"068270": CLS_C, "196170": CLS_C, "000100": CLS_C, "005380": CLS_A, "005930": CLS_B}


def _company(make, pages, cands, cmap=CMAP, cons=CONS, **kw):
    async def go():
        lookup = make(pages, **kw)
        async with lookup.client:
            return await S.company_rows(lookup, cands, cons, cmap)
    return asyncio.run(go())


def test_company_rows_lift_the_screen_industry_to_its_group_after_exemplar_check(screens):
    make, calls = screens
    pages = {"950160": "생물공학", "000040": "자동차", "068270": "생물공학", "005380": "자동차"}
    rows, stats = _company(make, pages, ["000040", "950160"])
    assert rows == [("000040", *CLS_A, "wise_company", "20261018:자동차"),
                    ("950160", *CLS_C, "wise_company", "20261018:생물공학")]
    # 쓰는 대응마다 본보기 한 종목을 화면으로 다시 본다(후보 2 + 본보기 2 = 4콜, 모두 공통 웹 속도제한).
    assert [c for c in calls if not isinstance(c, tuple)] == ["000040", "950160", "068270", "005380"]
    assert sum(1 for c in calls if c == ("throttle", "fetch_sector_company")) == 4
    assert stats["refused"] == {} and stats["unmapped"] == {}


def test_unmapped_or_missing_industry_is_never_invented(screens):
    make, _ = screens
    pages = {"950160": "우주항공", "950170": None, "068270": "생물공학"}
    rows, stats = _company(make, pages, ["950160", "950170"])
    assert rows == []
    assert stats["unmapped"] == {"950160": "우주항공"} and stats["no_class"] == ["950170"]


def test_exemplar_contradiction_refuses_the_mapping_for_this_run(screens):
    """본보기 화면은 같은 산업인데 저장 하위업종이 대응표와 다르면(분류 체계 개편) 그 대응을 쓰지 않는다."""
    make, _ = screens
    cons = {**CONS, "068270": CLS_B}
    rows, stats = _company(make, {"950160": "생물공학", "068270": "생물공학"}, ["950160"], cons=cons)
    assert rows == [] and "생물공학" in stats["refused"]


def test_reclassified_exemplar_falls_through_to_the_next_one(screens):
    make, calls = screens
    pages = {"950160": "생물공학", "068270": "제약", "196170": "생물공학"}
    rows, _ = _company(make, pages, ["950160"])
    assert rows == [("950160", *CLS_C, "wise_company", "20261018:생물공학")]
    assert [c for c in calls if not isinstance(c, tuple)] == ["950160", "068270", "196170"]


def test_exemplars_must_be_constituents_of_that_snapshot(screens):
    make, _ = screens
    cons = {k: v for k, v in CONS.items() if k not in ("068270", "196170", "000100")}
    rows, stats = _company(make, {"950160": "생물공학"}, ["950160"], cons=cons)
    assert rows == [] and stats["refused"]["생물공학"] == "본보기로 확인하지 못함"


def test_block_or_redirect_streak_stops_lookups_without_raising(screens):
    """오류 화면으로 가는 302·403·전송 오류가 잇달아 3번이면 화면 조회만 멈춘다 — 예외로 수집을 막지 않는다."""
    make, calls = screens
    pages = {"000010": httpx.Response(302, headers={"Location": "/common/ConsiderErrorPage.aspx"}),
             "000020": httpx.Response(403), "000030": httpx.ReadTimeout("x"), "000040": "자동차"}
    rows, stats = _company(make, pages, ["000010", "000020", "000030", "000040"])
    assert rows == [] and stats["stopped"] and stats["failed"] == ["000010", "000020", "000030", "000040"]
    assert "000040" not in calls


def test_no_company_page_answer_is_not_a_block(screens):
    """상장폐지 등으로 그 코드의 화면이 없다는 답(200 + 짧은 경고 스크립트, 261018 실측)은 차단이 아니다 —
    옛 스냅샷의 상장폐지 종목 셋이 잇달아 나와도 조회를 멈추지 않고, 그 종목만 분류 없음으로 둔다."""
    make, calls = screens
    gone = "<script>alert('올바른 종목이 아닙니다.');location.replace('../company/c1010001.aspx?cmp_cd=005930');</script>"
    pages = {t: httpx.Response(200, text=gone) for t in ("000010", "000020", "000030")}
    pages |= {"000040": "자동차", "005380": "자동차"}
    rows, stats = _company(make, pages, ["000010", "000020", "000030", "000040"])
    assert [r[0] for r in rows] == ["000040"] and stats["stopped"] is None
    assert stats["no_page"] == ["000010", "000020", "000030"] and stats["failed"] == [] and stats["no_class"] == []


def test_no_company_page_streak_stops_at_its_own_limit(screens, monkeypatch):
    make, calls = screens
    monkeypatch.setattr(S, "COMPANY_STOP_AFTER_NO_PAGE", 2)
    gone = "<script>alert('올바른 종목이 아닙니다.');</script>"
    pages = {"000010": httpx.Response(200, text=gone), "000020": httpx.Response(200, text=gone), "000040": "자동차"}
    rows, stats = _company(make, pages, ["000010", "000020", "000040"])
    assert rows == [] and stats["stopped"] and stats["failed"] == ["000040"]


def test_block_page_with_200_counts_as_bad(screens):
    make, _ = screens
    blocked = httpx.Response(200, text="<html>비정상적인 접근입니다</html>")
    rows, stats = _company(make, {"000010": blocked, "000040": "자동차", "005380": "자동차"}, ["000010", "000040"])
    assert stats["failed"] == ["000010"] and [r[0] for r in rows] == ["000040"]


def test_call_cap_keeps_a_reserve_for_exemplar_checks(screens):
    make, calls = screens
    cands = [f"{n:05}0" for n in range(1, 10)]
    pages = {t: "자동차" for t in cands} | {"005380": "자동차"}
    rows, stats = _company(make, pages, cands, max_calls=6)  # 확인 몫 6//3 = 2 를 남긴다
    assert len([c for c in calls if not isinstance(c, tuple)]) == 5
    assert len(rows) == 4 and len(stats["failed"]) == 5


def test_company_candidates_are_listed_commons_missing_from_constituents():
    listed = {"005930", "005935", "000040", "950160", "0126Z0", "bad"}
    assert S.company_candidates(listed, {"005930"}) == ["000040", "0126Z0", "950160"]


def test_company_observation_is_carried_and_inherited_like_constituents():
    """기업 화면 관측 행은 관측이다 — 다음 스냅샷이 조회에 실패하면 이어 쓰고(관측한 스냅샷 날짜), 우선주가 잇는다."""
    prev = {"950160": (CLS_C, "wise_company", "20261018:생물공학")}
    assert S.derive_rows({}, prev, "20261016", listed={"950160"}) == [
        ("950160", *CLS_C, "carry", "20261016")]
    out = _rows_by_ticker(S.derive_rows({"000040": CLS_A}, {}, None, listed={"000040", "000045"}))
    assert out == {"000045": ("000045", *CLS_A, "pref", "000040")}


def test_company_only_flag_skips_constituent_fetch(monkeypatch):
    called = []

    async def fetch(*a, **k):
        raise AssertionError("수집하면 안 된다")

    async def company_only(snap, dry_run=False):
        called.append((snap, dry_run))
        return 0
    monkeypatch.setattr(S, "fetch_snapshot", fetch)
    monkeypatch.setattr(S, "company_only_db", company_only)
    assert asyncio.run(S.main(args(company_only=True, snap="all", dry_run=True))) == 0
    assert called == [("all", True)]


@pytest.mark.parametrize("no_company", [False, True])
def test_monthly_run_passes_company_rows_to_the_same_transaction(upstream, monkeypatch, no_company):
    upstream()
    seen = []

    async def lookup(snapshot):
        seen.append("lookup")
        return [("950160", *CLS_C, "wise_company", "20261018:생물공학")]
    monkeypatch.setattr(S, "lookup_company_for_snapshot", lookup)
    monkeypatch.setattr(S, "persist", lambda snap, company=None: seen.append(company) or True)
    assert asyncio.run(S.main(args(date=ANCHOR, no_file=True, no_company=no_company))) == 0
    assert seen == ([None] if no_company else ["lookup", [("950160", *CLS_C, "wise_company", "20261018:생물공학")]])


def test_committed_company_map_is_well_formed_and_matches_observed_groups():
    """대응표는 전수 측정으로 만든 것이다 — 모든 하위업종 코드는 관측 정적 맵에 있는 코드이고, 두 하위업종에 걸친 이름은 표에 없다."""
    import json
    cmap = S.load_company_map()
    data = json.loads(S.COMPANY_MAP_PATH.read_text(encoding="utf-8"))
    codes = {v["industryCode"] for v in json.loads(S.OUT_PATH.read_text(encoding="utf-8"))["data"].values()}
    assert cmap and {e["industryCode"] for e in cmap.values()} <= codes
    assert not set(cmap) & set(data.get("ambiguous", {}))
    assert data["meta"]["pairs"] >= 1000 and all(len(e["exemplars"]) >= 1 for e in cmap.values())
