# -*- coding: utf-8 -*-
"""DART 요청 단위 메모 — 한 도구 호출(요청 장부 하나) 안에서 같은 (엔드포인트, 인자)는 HTTP 한 번. network 0콜.

계기: 배수 도구(price_multiple_data firm) 한 번이 DART 14콜을 부르는데 그중 3콜(회사 개황 · 사업보고서 재무제표 ·
배당)이 같은 인자의 중복이었다. 값은 바뀌지 않고 HTTP 만 줄어야 한다 — 그래서 여기서 지키는 것은
① 같은 질의는 한 번, ② 동시여도 한 번, ③ 다른 질의는 각각, ④ 기준일(엄격 포함) 호출은 건드리지 않음,
⑤ 오류는 **요청 경계를 넘지 않고** 다시 하면 달라질 수 있는 오류는 기억하지 않음, ⑥ 받은 쪽이 고쳐도 남에게 안 번짐 이다.
공개 DATA 문서(wiki/decisions/data-collection.md) DART 절의 「요청 단위 메모」 항목을 시험으로 옮긴 것이다.
"""
from __future__ import annotations

import asyncio

import pytest

from open_proxy_mcp.dart import as_of
from open_proxy_mcp.dart.client import (
    DartClient,
    DartClientError,
    _ctx_ledger,
    new_request_ledger,
)


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._p


class _FakeHttp:
    """DART 대신 답하는 가짜. 호출을 (엔드포인트, 인자) 로 센다. `script` 는 호출 순서대로 응답/예외."""

    def __init__(self, script=None, delay: float = 0.0):
        self.calls: list[tuple[str, dict]] = []
        self.script = list(script or [])
        self.delay = delay

    async def get(self, url, params=None, timeout=None):
        self.calls.append((url.rsplit("/", 1)[-1], {k: v for k, v in params.items() if k != "crtfc_key"}))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, BaseException):
                raise item
            return _Resp(item)
        return _Resp({"status": "000", "list": [{"v": len(self.calls)}], "corp_name": "가나다"})


@pytest.fixture(autouse=True)
def _clean():
    _ctx_ledger.set(None)
    yield
    _ctx_ledger.set(None)


def _client(http: _FakeHttp) -> DartClient:
    c = DartClient(api_keys=["k-test"])

    async def _no_throttle():
        return None

    c._throttle_api = _no_throttle      # 분당 한도 대기는 이 시험의 관심사가 아니다
    c._http = http
    return c


def _run(coro_fn):
    return asyncio.run(coro_fn())


def test_same_request_twice_hits_http_once():
    http = _FakeHttp()
    c = _client(http)

    async def go():
        new_request_ledger()
        a = await c._request("fnlttSinglAcntAll.json", {"corp_code": "1", "bsns_year": "2025", "reprt_code": "11011"})
        b = await c._request("fnlttSinglAcntAll.json", {"reprt_code": "11011", "bsns_year": "2025", "corp_code": "1"})
        return a, b

    a, b = _run(go)
    assert len(http.calls) == 1, "인자 순서만 다른 같은 질의는 한 번"
    assert a == b
    assert c.api_call_snapshot() == 1, "dart_api_calls 는 실제 HTTP 수 — 메모 적중은 세지 않는다"


def test_concurrent_identical_requests_share_one_flight():
    http = _FakeHttp(delay=0.05)
    c = _client(http)

    async def go():
        new_request_ledger()
        return await asyncio.gather(*[c._request("company.json", {"corp_code": "9"}) for _ in range(5)])

    out = _run(go)
    assert len(http.calls) == 1
    assert all(o == out[0] for o in out)


def test_different_requests_are_each_called():
    http = _FakeHttp()
    c = _client(http)

    async def go():
        new_request_ledger()
        await c._request("company.json", {"corp_code": "1"})
        await c._request("company.json", {"corp_code": "2"})                     # 다른 회사
        await c._request("alotMatter.json", {"corp_code": "1", "bsns_year": "2025", "reprt_code": "11011"})
        await c._request("alotMatter.json", {"corp_code": "1", "bsns_year": "2024", "reprt_code": "11011"})  # 다른 해
        await c._request("alotMatter.json", {"corp_code": "1", "bsns_year": "2025", "reprt_code": "11012"})  # 다른 보고서

    _run(go)
    assert len(http.calls) == 5


def test_no_request_ledger_means_no_memo():
    """배치·스크립트에는 요청 경계가 없다 — 메모하지 않는다(종전 동작 그대로)."""
    http = _FakeHttp()
    c = _client(http)

    async def go():
        await c._request("company.json", {"corp_code": "1"})
        await c._request("company.json", {"corp_code": "1"})

    _run(go)
    assert len(http.calls) == 2


def test_memo_does_not_cross_request_boundaries():
    http = _FakeHttp()
    c = _client(http)

    async def go():
        new_request_ledger()
        await c._request("company.json", {"corp_code": "1"})
        new_request_ledger()                                  # 다음 요청 = 새 장부
        await c._request("company.json", {"corp_code": "1"})

    _run(go)
    assert len(http.calls) == 2


def test_strict_as_of_passes_through_unmemoized():
    http = _FakeHttp()
    c = _client(http)

    async def go():
        new_request_ledger()
        tokens = as_of.set_strict_as_of("2025-03-26T09:00:00+09:00")
        try:
            await c._request("fnlttSinglAcntAll.json", {"corp_code": "1", "bsns_year": "2024", "reprt_code": "11011"})
            await c._request("fnlttSinglAcntAll.json", {"corp_code": "1", "bsns_year": "2024", "reprt_code": "11011"})
        finally:
            as_of.reset_strict_as_of(tokens)

    _run(go)
    assert len(http.calls) == 2


def test_legacy_as_of_passes_through_unmemoized():
    """기준일이 걸리면 응답 행 거르기(`note_row_drop`)가 호출마다 남아야 한다."""
    http = _FakeHttp()
    c = _client(http)

    async def go():
        new_request_ledger()
        tokens = as_of.set_as_of("20250326")
        try:
            await c._request("majorstock.json", {"corp_code": "1"})
            await c._request("majorstock.json", {"corp_code": "1"})
        finally:
            as_of.reset_as_of(tokens)

    _run(go)
    assert len(http.calls) == 2


def test_transient_error_is_not_remembered_so_next_call_retries():
    http = _FakeHttp(script=[RuntimeError("boom")])
    c = _client(http)

    async def go():
        new_request_ledger()
        with pytest.raises(RuntimeError):
            await c._request("company.json", {"corp_code": "1"})
        return await c._request("company.json", {"corp_code": "1"})

    out = _run(go)
    assert len(http.calls) == 2, "전송 오류는 다시 하면 달라질 수 있다 — 기억하지 않는다"
    assert out["status"] == "000"


def test_dart_answer_error_is_remembered_within_request_but_not_across():
    nodata = {"status": "013", "message": "조회된 데이터가 없습니다."}
    http = _FakeHttp(script=[nodata, nodata])
    c = _client(http)

    async def go():
        new_request_ledger()
        for _ in range(2):
            with pytest.raises(DartClientError) as ei:
                await c._request("alotMatter.json", {"corp_code": "1", "bsns_year": "2025", "reprt_code": "11011"})
            assert ei.value.status == "013"
        n_first = len(http.calls)
        new_request_ledger()                                  # 다음 요청은 오류를 물려받지 않는다
        with pytest.raises(DartClientError):
            await c._request("alotMatter.json", {"corp_code": "1", "bsns_year": "2025", "reprt_code": "11011"})
        return n_first

    n_first = _run(go)
    assert n_first == 1
    assert len(http.calls) == 2


def test_concurrent_waiters_all_see_the_same_error_from_one_call():
    http = _FakeHttp(script=[{"status": "013", "message": "x"}], delay=0.03)
    c = _client(http)

    async def go():
        new_request_ledger()
        return await asyncio.gather(
            *[c._request("company.json", {"corp_code": "1"}) for _ in range(3)], return_exceptions=True)

    out = _run(go)
    assert len(http.calls) == 1
    assert all(isinstance(e, DartClientError) and e.status == "013" for e in out)


def test_callers_get_independent_copies():
    http = _FakeHttp()
    c = _client(http)

    async def go():
        new_request_ledger()
        a = await c._request("company.json", {"corp_code": "1"})
        a["list"].append({"v": "오염"})
        a["corp_name"] = "바뀜"
        b = await c._request("company.json", {"corp_code": "1"})
        return b

    b = _run(go)
    assert b["corp_name"] == "가나다" and b["list"] == [{"v": 1}]


def test_cancelling_one_waiter_does_not_cancel_the_shared_call():
    http = _FakeHttp(delay=0.05)
    c = _client(http)

    async def go():
        new_request_ledger()
        t1 = asyncio.ensure_future(c._request("company.json", {"corp_code": "1"}))
        t2 = asyncio.ensure_future(c._request("company.json", {"corp_code": "1"}))
        await asyncio.sleep(0.01)
        t1.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t1
        return await t2

    out = _run(go)
    assert out["status"] == "000"
    assert len(http.calls) == 1


def test_callers_params_dict_is_not_mutated_by_the_memoized_path():
    http = _FakeHttp()
    c = _client(http)
    params = {"corp_code": "1"}

    async def go():
        new_request_ledger()
        await c._request("company.json", params)

    _run(go)
    assert params == {"corp_code": "1"}


def test_memo_is_bounded(monkeypatch):
    import open_proxy_mcp.dart.client as cl
    monkeypatch.setattr(cl, "_REQUEST_MEMO_MAX", 3)
    http = _FakeHttp()
    c = _client(http)

    async def go():
        led = new_request_ledger()
        for i in range(6):
            await c._request("company.json", {"corp_code": str(i)})
        return led

    led = _run(go)
    assert len(http.calls) == 6                    # 상한을 넘어도 호출은 그대로 된다
    assert len(led["dart_memo"]) == 3


def test_key_rotation_on_020_is_unchanged_under_memo():
    """020 처리는 `_request_http` 안에 그대로 — 첫 키가 한도면 다음 키로 한 번 더 부르고, 그 결과가 메모된다."""
    http = _FakeHttp(script=[{"status": "020", "message": "한도"}, {"status": "000", "list": [], "corp_name": "ok"}])
    c = DartClient(api_keys=["k1", "k2"])

    async def _nt():
        return None

    c._throttle_api = _nt
    c._http = http

    async def go():
        new_request_ledger()
        a = await c._request("company.json", {"corp_code": "1"})
        b = await c._request("company.json", {"corp_code": "1"})
        return a, b

    a, b = _run(go)
    assert a == b and a["corp_name"] == "ok"
    assert len(http.calls) == 2                    # 020 → 다음 키: 실제 HTTP 두 번(메모 두 번째 호출은 0)


def test_memo_byte_budget_stops_remembering_big_responses(monkeypatch):
    """요청 하나가 큰 응답을 몇백 건 쥐고 있지 않게 — 예산을 넘는 항목은 기억하지 않고 호출은 그대로 한다."""
    import open_proxy_mcp.dart.client as cl
    monkeypatch.setattr(cl, "_REQUEST_MEMO_MAX_BYTES", 600)
    big = {"status": "000", "list": [{"x": "가" * 400}]}
    http = _FakeHttp(script=[big, big, big, big])
    c = _client(http)

    async def go():
        led = new_request_ledger()
        await c._request("a.json", {"k": "1"})            # ~430바이트 — 기억
        await c._request("a.json", {"k": "1"})            # 적중
        await c._request("a.json", {"k": "2"})            # 합이 예산을 넘는다 — 기억하지 않음
        await c._request("a.json", {"k": "2"})            # 그래서 다시 부른다
        return led

    led = _run(go)
    assert len(http.calls) == 3
    assert 0 < led["dart_memo"].nbytes <= 600


def test_memo_is_per_client_so_a_key_scoped_error_does_not_leak_to_another_key():
    """020(키의 하루 한도)은 키마다 다르다 — 한 요청 안에서 다른 키 풀의 클라이언트가 같은 질의를 해도 서로의 답을 물려받지 않는다."""
    quota = {"status": "020", "message": "한도"}
    http_a = _FakeHttp(script=[quota])
    http_b = _FakeHttp()
    a, b = _client(http_a), _client(http_b)

    async def go():
        new_request_ledger()
        with pytest.raises(DartClientError):
            await a._request("company.json", {"corp_code": "1"})
        return await b._request("company.json", {"corp_code": "1"})

    out = _run(go)
    assert out["status"] == "000"
    assert len(http_a.calls) == 1 and len(http_b.calls) == 1


def test_a_ledger_left_over_from_another_context_cannot_serve_a_new_client():
    """앞선 일이 요청 장부를 흘려도(문맥 변수는 복사돼 흐른다) 새 클라이언트는 그 메모에 적중하지 않는다."""
    http1, http2 = _FakeHttp(), _FakeHttp()

    async def go():
        new_request_ledger()
        await _client(http1)._request("company.json", {"corp_code": "1"})
        await _client(http2)._request("company.json", {"corp_code": "1"})

    _run(go)
    assert len(http1.calls) == 1 and len(http2.calls) == 1


@pytest.mark.parametrize("status", ["800", "900"])
def test_transient_dart_status_is_not_remembered_so_a_retry_in_the_same_request_calls_again(status):
    """800 점검·900 알 수 없는 오류는 숫자 상태지만 다시 하면 달라질 수 있다 — 같은 요청 안의 재시도(proxy_advise 상류 3회 등)가
    기억된 오류만 다시 받으면 재시도가 무력해진다. 다음 호출은 HTTP 를 다시 한다."""
    http = _FakeHttp(script=[{"status": status, "message": "x"}])
    c = _client(http)

    async def go():
        new_request_ledger()
        with pytest.raises(DartClientError) as ei:
            await c._request("company.json", {"corp_code": "1"})
        assert ei.value.status == status
        return await c._request("company.json", {"corp_code": "1"})

    out = _run(go)
    assert out["status"] == "000"
    assert len(http.calls) == 2
