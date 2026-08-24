"""스로틀 직렬화 + 스캔 병렬화 — 260824.

배경: `screener` 응답의 **87%** 가 호출측 sleep 이었다(kospi200·details=ON 42.3초 중 36.7초).
실사용 p95 116초·최대 306초였고, 5분이면 클라이언트가 먼저 끊어 그 응답은 아무에게도 안 닿았다.

sleep 을 걷어내려면 먼저 **그것이 가리고 있던 결함**을 고쳐야 했다 —
`_throttle_scrape` 에 락이 없어 동시 호출이 같은 시각을 읽고 **함께 나갔다**
(실측: 동시 4건에서 간격 0.299초·0.151초, 하한 1.0초 위반). API 한도는 키마다라 넘겨도
그 사람만 막히지만 **웹 차단은 IP 기준이라 우리 서버가 막히면 전원이 막힌다.**

결과(실측): 스캔 14.9초→1.7초 · kospi200+details 42.3초→12.2초 · 출력은 **완전 동일**
(582건, 집합·순서 일치).
"""
from __future__ import annotations

import asyncio
import time

import pytest

from open_proxy_mcp.dart.client import _WEB_INTERVAL_RANGE  # noqa: F401


#: 실제 간격(1~2초)으로 돌리면 테스트가 수십 초가 된다. **코드 경로는 그대로 두고
#: 간격만 줄여** 직렬화 여부를 본다 — 가짜 시계로 `time.monotonic` 을 갈아끼우면
#: 그 패치가 이벤트루프 내부까지 흔들어 무엇을 재는지 알 수 없게 된다.
_FAST = (0.05, 0.10)


@pytest.fixture
def fast_web(monkeypatch):
    import open_proxy_mcp.dart.client as C
    monkeypatch.setattr(C, "_WEB_INTERVAL_RANGE", _FAST)
    return _FAST


@pytest.fixture(autouse=True)
def _dummy_key(monkeypatch):
    """CI 에는 키가 없다. 이 파일의 테스트는 **스로틀만** 보고 네트워크를 안 타므로
    더미 키로 클라이언트를 세운다 — 키가 없으면 생성자가 ValueError 를 낸다.
    (260824 배포 실패: 로컬 .env 에 키가 있어 못 보고 지나갔다. unit 은 키·네트워크 0 이 규칙이다.)"""
    monkeypatch.setenv("OPENDART_API_KEY", "test-dummy-key")
    import open_proxy_mcp.dart.client as C
    monkeypatch.setattr(C, "_client_pool", {}, raising=False)


def _client():
    from open_proxy_mcp.dart.client import get_dart_client
    cl = get_dart_client()
    cl._last_web_request = 0.0
    cl._api_call_timestamps.clear()
    cl._last_api_request = 0.0
    return cl


@pytest.mark.parametrize("conc", [2, 4, 8])
def test_web_throttle_serializes_under_concurrency(fast_web, conc):
    """★ 이 테스트가 락의 이유다. 락이 없으면 동시 N 건이 거의 동시에 나간다
    (락 제거 후 실측: 동시 4건 간격 0.299초·0.151초)."""
    cl = _client()
    fired: list[float] = []

    async def one():
        await cl._throttle_scrape("t")
        fired.append(time.monotonic())

    asyncio.run(_gather(one, conc))
    fired.sort()
    gaps = [fired[i] - fired[i - 1] for i in range(1, len(fired))]
    lo = fast_web[0]
    bad = [round(g, 4) for g in gaps if g < lo * 0.9]     # 스케줄링 오차 10% 허용
    assert not bad, f"하한 {lo}초 위반 {bad} — 웹 스로틀이 직렬화되지 않았다"


async def _gather(fn, n):
    await asyncio.gather(*[fn() for _ in range(n)])


def test_web_throttle_has_a_lock():
    """락을 지우면 위 테스트가 통과할 수도 있다(스케줄링 운). 존재 자체를 잠근다."""
    cl = _client()

    async def _check():
        api, web = cl._loop_locks()
        assert isinstance(web, asyncio.Lock) and isinstance(api, asyncio.Lock)
        assert api is not web, "API 와 웹은 서로 다른 한도라 락도 따로여야 한다"

    asyncio.run(_check())


def test_locks_survive_a_new_event_loop():
    """★ 락은 만든 루프에 묶인다. 싱글턴 클라이언트가 다른 루프에서 다시 쓰이면
    「다른 루프에 묶임」으로 죽는다 — 루프마다 다시 잡는지 확인한다."""
    cl = _client()
    seen = []

    async def _grab():
        api, web = cl._loop_locks()
        async with web:
            pass
        seen.append(id(web))

    asyncio.run(_grab())
    asyncio.run(_grab())          # 새 루프 — 여기서 종전엔 RuntimeError 였다
    assert len(seen) == 2 and seen[0] != seen[1], "루프가 바뀌면 락을 새로 잡아야 한다"


def test_screener_has_no_caller_side_sleeps():
    """속도는 클라이언트 스로틀 한 곳에서 잡는다 — 두 곳에서 하면 한쪽만 고쳐진다."""
    import inspect

    from open_proxy_mcp.services import screener
    src = inspect.getsource(screener)
    assert "_SCAN_PAGE_SLEEP" not in src, "스캔 sleep 이 남아 있다"
    assert "_DETAILS_SLEEP" not in src, "상세 sleep 이 남아 있다"
    assert screener._SCAN_CONCURRENCY >= 2 and screener._DETAILS_CONCURRENCY >= 2


def test_scan_pages_are_fetched_in_parallel_but_ordered():
    """★ 병렬화의 유일한 진짜 위험은 **순서**다. 공시 목록 순서가 뒤집히면
    dedup(정정=최신본만)이 흔들린다. 늦게 오는 페이지를 일부러 먼저 끝내 본다."""
    from open_proxy_mcp.services.screener import _scan_code

    calls: list[int] = []

    class _C:
        async def search_filings(self, *, page_no, **kw):
            calls.append(page_no)
            # 뒤 페이지일수록 **빨리** 끝나게 — 도착 순서를 일부러 뒤집는다
            await asyncio.sleep((10 - page_no) * 0.001)
            return {"total_count": 500,
                    "list": [{"rcept_no": f"p{page_no}-{i}"} for i in range(2)]}

    items, total, trunc, err = asyncio.run(_scan_code(_C(), "B001", "20260101", "20260131", 5))
    got = [it["rcept_no"] for it in items]
    assert got == [f"p{p}-{i}" for p in range(1, 6) for i in range(2)], f"순서가 깨졌다: {got}"
    assert total == 500 and err is None
    assert trunc is False, "총 500건=정확히 5페이지, max_pages=5 라 잘린 게 없다"
    assert sorted(calls) == [1, 2, 3, 4, 5]


def test_scan_partial_failure_keeps_other_pages():
    """한 페이지가 죽어도 나머지는 살린다 — 종전 `break` 는 뒤를 통째로 버렸다."""
    from open_proxy_mcp.dart.client import DartClientError
    from open_proxy_mcp.services.screener import _scan_code

    class _C:
        async def search_filings(self, *, page_no, **kw):
            if page_no == 3:
                raise DartClientError("020", "스캔 실패")
            return {"total_count": 400, "list": [{"rcept_no": f"p{page_no}"}]}

    items, total, trunc, err = asyncio.run(_scan_code(_C(), "B001", "20260101", "20260131", 4))
    assert err == "020"
    assert [it["rcept_no"] for it in items] == ["p1", "p2", "p4"], "성공한 페이지는 남아야 한다"


def test_scan_survives_transport_error_on_first_page():
    """★ 전송 오류는 `DartClientError` 로 오지 않는다 — `_request` 가 원래 예외(httpx)를
    그대로 올린다. 종전 `except DartClientError` 만으로는 못 잡아 스캔 전체가 죽었다
    (260824 실측: DNS 실패 시 httpx.ConnectError 가 build_screener_payload 밖으로 나갔다)."""
    from open_proxy_mcp.services.screener import _scan_code

    class _C:
        async def search_filings(self, **kw):
            raise ConnectionError("DNS 실패")

    items, total, trunc, err = asyncio.run(_scan_code(_C(), "B001", "20260101", "20260131", 5))
    assert items == [] and total == 0
    assert err and err.startswith("transport:"), f"전송오류를 분류해야 한다: {err}"


def test_one_dead_scan_code_does_not_kill_the_others():
    """코드 5개를 gather 로 던지므로 하나가 죽어도 나머지 결과를 받아야 한다.
    `return_exceptions` 없이 두면 첫 예외가 올라오면서 나머지 태스크가 고아가 된다."""
    import open_proxy_mcp.services.screener as S

    async def _fake(client, code, bgn, end, mx):
        if code == "D001":
            raise ConnectionError("죽음")
        return [{"rcept_no": f"{code}-1", "report_nm": "단일판매ㆍ공급계약 체결",
                 "corp_code": "x", "corp_name": "테스트", "stock_code": "005930",
                 "corp_cls": "Y", "flr_nm": "테스트", "rcept_dt": "20260820"}], 1, False, None

    orig = S._scan_code
    S._scan_code = _fake
    try:
        p = asyncio.run(S.build_screener_payload(
            types="order", period="custom", custom_start="20260818",
            custom_end="20260820", universe="all", details=False))
    finally:
        S._scan_code = orig
    assert p["status"] == "ok", "한 코드가 죽었다고 전체가 실패하면 안 된다"
