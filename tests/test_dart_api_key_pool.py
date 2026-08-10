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
