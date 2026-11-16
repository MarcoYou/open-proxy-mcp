# -*- coding: utf-8 -*-
"""DART API 키 풀 — **사용자 키가 우리 키로 새지 않는가**. network 0콜.

260810 이전에는 사용자 요청의 키 뒤에 env 예비키가 붙었다. 사용자 키가 한도에 걸리면
`_rotate_key()` 가 조용히 **우리 키**로 넘어갔고, 그러면 두 가지가 동시에 벌어진다.
  ① 한 사용자의 과다 호출이 우리 키를 태운다. 우리 키가 막히면(실측 2~3시간) 그 키로 도는
     **다른 사용자와 배치가 전부 함께 멈춘다** — CLAUDE.md 의 "한도는 키마다다" 가 그 뜻이다.
  ② 사용자는 자기 키가 막힌 걸 모른 채 계속 쓴다. 알려야 할 신호를 우리가 덮는다.

이 파일이 지키는 것은 하나다 — **사용자 키로 들어온 요청은 그 키 하나만 쓴다.**
"""
from __future__ import annotations

import pytest

from open_proxy_mcp.dart.client import DartClient, _ctx_opendart_key

USER_KEY = "u" * 40
OUR_KEY = "o" * 40
SPARE_1, SPARE_2 = "1" * 40, "2" * 40


@pytest.fixture
def env_keys(monkeypatch):
    """env 에 우리 키 + 예비키 둘이 있는 상태를 만든다(로컬 .env 영향을 지운다)."""
    monkeypatch.setenv("OPENDART_API_KEY", OUR_KEY)
    monkeypatch.setenv("OPENDART_API_KEY_2", SPARE_1)
    monkeypatch.setenv("OPENDART_API_KEY_3", SPARE_2)
    for i in range(4, 10):
        monkeypatch.delenv(f"OPENDART_API_KEY_{i}", raising=False)


@pytest.fixture
def as_user():
    """요청 컨텍스트에 사용자 키가 실린 상태."""
    token = _ctx_opendart_key.set(USER_KEY)
    yield
    _ctx_opendart_key.reset(token)


def test_user_request_uses_only_the_users_key(env_keys, as_user):
    """**이 테스트가 전부다.** env 에 우리 키와 예비키가 있어도 풀에 들어오면 안 된다."""
    c = DartClient()
    assert c._api_keys == [USER_KEY], f"사용자 키 외의 것이 풀에 들어왔다: {len(c._api_keys)}개"
    assert OUR_KEY not in c._api_keys
    assert SPARE_1 not in c._api_keys and SPARE_2 not in c._api_keys


def test_user_request_cannot_rotate_onto_our_key(env_keys, as_user):
    """풀이 1개면 회전이 아예 안 일어난다 — 한도 에러는 사용자에게 그대로 간다."""
    c = DartClient()
    assert c._rotate_key() is False, "사용자 요청이 다른 키로 넘어갔다"
    assert c.api_key == USER_KEY


def test_scripts_without_a_user_key_still_use_env_keys(env_keys):
    """로컬 스크립트·배치·부팅 시 corpCode 적재는 사용자 키가 없다 — 여기선 env 키를 쓴다.
    이 경로까지 막으면 배치가 통째로 죽는다."""
    c = DartClient()
    assert c._api_keys == [OUR_KEY, SPARE_1, SPARE_2]
    assert c._rotate_key() is True and c.api_key == SPARE_1


def test_explicit_api_keys_argument_still_wins(env_keys, as_user):
    """명시 인자는 테스트·특수 배치용 탈출구다 — 컨텍스트보다 우선한다."""
    c = DartClient(api_keys=[SPARE_1, SPARE_2])
    assert c._api_keys == [SPARE_1, SPARE_2]


def test_env_key_numbering_must_be_contiguous_from_2(monkeypatch):
    """**알고 있는 제약을 못으로 박아둔다.** 로더는 `_2` 부터 세다가 **처음 빈 번호에서 멈춘다.**
    번호에 구멍이 있으면 그 뒤는 조용히 안 읽힌다 — 키를 지웠다 넣을 때 실제로 밟는다.
    (fly 에 `OPENDART_API_KEY_1` 이 있는데 안 읽히는 것도 같은 이유다. 다만 예비키는
     사용자 요청에 안 쓰이므로 fly 쪽은 문제가 아니다.)"""
    monkeypatch.setenv("OPENDART_API_KEY", OUR_KEY)
    monkeypatch.delenv("OPENDART_API_KEY_2", raising=False)
    monkeypatch.setenv("OPENDART_API_KEY_3", SPARE_2)      # 구멍 뒤에 있는 키
    for i in (4, 5, 6, 7, 8, 9):
        monkeypatch.delenv(f"OPENDART_API_KEY_{i}", raising=False)

    c = DartClient()
    assert c._api_keys == [OUR_KEY], (
        "구멍 뒤 키까지 읽혔다 — 좋은 변화지만 이 테스트의 설명을 함께 고쳐야 한다")


def test_no_key_anywhere_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("OPENDART_API_KEY", raising=False)
    for i in range(2, 10):
        monkeypatch.delenv(f"OPENDART_API_KEY_{i}", raising=False)
    with pytest.raises(ValueError, match="OPENDART_API_KEY"):
        DartClient()


def test_binary_path_rotates_to_a_spare_key_on_quota_error(monkeypatch):
    """document.xml(바이너리) 경로도 020/021 이면 보조 키로 한 번 더 간다 (260906).

    그날 .env 에 보조 키 4개가 있었는데 JSON 경로만 순회하고 바이너리 경로는 020 으로 바로
    죽었다. 첫 키가 020 을 주고 보조 키가 ZIP 을 주면 ZIP 이 돌아와야 한다.
    """
    import asyncio
    from open_proxy_mcp.dart import client as dart_client

    monkeypatch.setenv("OPENDART_API_KEY", OUR_KEY)
    monkeypatch.setenv("OPENDART_API_KEY_2", SPARE_1)
    for i in (3, 4, 5, 6):
        monkeypatch.delenv(f"OPENDART_API_KEY_{i}", raising=False)
    c = dart_client.DartClient()

    seen = []

    class _Resp:
        def __init__(self, content):
            self.content = content
        def raise_for_status(self):
            pass

    class _Http:
        async def get(self, url, params=None, timeout=None):
            seen.append(params["crtfc_key"])
            if params["crtfc_key"] == OUR_KEY:
                return _Resp(b"<?xml version='1.0'?><result><status>020</status><message>limit</message></result>")
            return _Resp(b"PK\x03\x04zip-bytes")

    c._http = _Http()
    async def _no_throttle():
        return None
    c._throttle_api = _no_throttle
    out = asyncio.run(c._request_binary("document.xml", {"rcept_no": "20260310002820"}))
    assert out.startswith(b"PK")
    assert seen == [OUR_KEY, SPARE_1]


# ── 261116: 동시 요청이 공유 키 번호를 서로 돌려 살아 있는 키를 건너뛰던 문제 ──
# 운영 실측(2026-11-16 20:3x): 키 5개 중 2번만 한도가 남았는데, 재무 배치의 동시 조회가 모두 020 으로 끝났다.
# 요청마다 공유 번호(`_key_index`)를 +1 씩 돌리므로, 요청 A 가 2번으로 옮긴 직후 요청 B 가 3·4·5·1번으로 계속 돌려
# B 는 남은 키를 한 번도 써 보지 못하고, 공유 번호는 죽은 키에 멈춘다. 이 묶음이 지키는 것:
#   ① 동시 요청이라도 살아 있는 키가 하나라도 있으면 모두 성공한다.
#   ② 한도에 걸린 키는 그날 다시 쓰지 않는다 — 다음 요청은 살아 있는 키로 바로 간다.

DEAD = ["d" * 40, "e" * 40, "f" * 40, "g" * 40]
LIVE = "l" * 40


def _quota_client(monkeypatch, keys):
    import asyncio
    from open_proxy_mcp.dart import client as dart_client

    for i in range(2, 10):
        monkeypatch.delenv(f"OPENDART_API_KEY_{i}", raising=False)
    c = dart_client.DartClient(api_keys=list(keys))
    seen: list[str] = []

    class _Resp:
        def __init__(self, data):
            self._data = data
        def raise_for_status(self):
            pass
        def json(self):
            return self._data

    class _Http:
        async def get(self, url, params=None, timeout=None):
            key = params["crtfc_key"]
            seen.append(key)
            await asyncio.sleep(0)  # 다른 요청이 끼어들 틈 — 실제 HTTP 대기와 같다
            if key == LIVE:
                return _Resp({"status": "000", "list": [{"ok": 1}]})
            return _Resp({"status": "020", "message": "사용한도를 초과하였습니다."})

    async def _no_throttle():
        return None
    c._http = _Http()
    c._throttle_api = _no_throttle
    return c, seen


def test_concurrent_requests_all_reach_the_one_live_key(monkeypatch):
    import asyncio
    keys = [DEAD[0], LIVE, DEAD[1], DEAD[2], DEAD[3]]
    c, _ = _quota_client(monkeypatch, keys)

    async def many():
        return await asyncio.gather(
            *(c._request_http("fnlttSinglAcnt.json", {"corp_code": f"{i:08d}"}) for i in range(6)),
            return_exceptions=True)

    out = asyncio.run(many())
    errors = [o for o in out if isinstance(o, Exception)]
    assert not errors, f"살아 있는 키가 있는데 {len(errors)}건이 실패했다: {errors[:1]}"


def test_quota_dead_key_is_not_retried_by_later_requests(monkeypatch):
    import asyncio
    keys = [DEAD[0], LIVE, DEAD[1]]
    c, seen = _quota_client(monkeypatch, keys)
    asyncio.run(c._request_http("fnlttSinglAcnt.json", {"corp_code": "00000001"}))
    seen.clear()
    asyncio.run(c._request_http("fnlttSinglAcnt.json", {"corp_code": "00000002"}))
    assert seen == [LIVE], f"한도에 걸린 키를 다시 불렀다: {seen}"


def test_all_keys_dead_still_raises_quota_error(monkeypatch):
    import asyncio
    from open_proxy_mcp.dart.client import DartClientError
    c, seen = _quota_client(monkeypatch, DEAD[:3])
    with pytest.raises(DartClientError) as e:
        asyncio.run(c._request_http("fnlttSinglAcnt.json", {"corp_code": "00000001"}))
    assert e.value.status == "020"
    assert sorted(seen) == sorted(DEAD[:3]), "키마다 한 번씩만 시도해야 한다"


def test_no_data_answer_is_not_turned_into_quota_error(monkeypatch):
    """013(자료 없음)은 키와 무관한 답이다 — 다른 키로 다시 부르지 않고 013 그대로 올린다.
    종전엔 한 번 돌렸고, 돌린 키가 한도에 걸린 키면 013 이 020 으로 바뀌어 올라갔다(2026-11-16 고영 분기 재무)."""
    import asyncio
    from open_proxy_mcp.dart.client import DartClientError
    keys = [LIVE, DEAD[0]]
    c, seen = _quota_client(monkeypatch, keys)

    class _NoData:
        def raise_for_status(self):
            pass
        def json(self):
            return {"status": "013", "message": "조회된 데이타가 없습니다."}

    class _Http:
        async def get(self, url, params=None, timeout=None):
            seen.append(params["crtfc_key"])
            return _NoData()
    c._http = _Http()
    with pytest.raises(DartClientError) as e:
        asyncio.run(c._request_http("fnlttSinglAcnt.json", {"corp_code": "00000001"}))
    assert e.value.status == "013"
    assert seen == [LIVE] and c.api_key == LIVE
