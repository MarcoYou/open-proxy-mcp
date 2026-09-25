"""뉴스 검색의 키 짝 ↔ 주소·헤더 묶음 (network 0, 가짜 키만).

260926: 개발자센터 키 이름(`NAVER_SEARCH_API_*`)을 읽어 HUB 주소·헤더로 보내고 있었다.
두 키는 서로의 방식에서 401 이라 뉴스 검색은 **늘 조용히 []** 였다. 이 테스트는 키 짝마다
주소와 헤더가 함께 가는지 본다 — 한쪽만 바뀌면 여기서 걸려야 한다.
"""

import asyncio

import pytest

from open_proxy_mcp.dart.client import DartClient

HUB_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"
DEV_URL = "https://openapi.naver.com/v1/search/news.json"
ITEMS = [{"title": "t", "originallink": "o", "link": "l", "description": "d", "pubDate": "p"}]
ENV_NAMES = (
    "NAVER_API_HUB_CLIENT_ID",
    "NAVER_API_HUB_CLIENT_SECRET",
    "NAVER_SEARCH_API_CLIENT_ID",
    "NAVER_SEARCH_API_CLIENT_SECRET",
)


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload


class _Http:
    def __init__(self, status_code=200):
        self.status_code = status_code
        self.calls = []

    async def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        return _Resp(self.status_code, {"items": ITEMS} if self.status_code == 200 else {"errorCode": "200"})


def _client(status_code=200):
    client = DartClient.__new__(DartClient)
    client._http = _Http(status_code)

    async def _no_throttle():
        return None

    client._throttle_api = _no_throttle
    return client


@pytest.fixture
def naver_env(monkeypatch):
    # client import 시 load_dotenv() 가 실제 .env 를 읽으므로 네 이름을 먼저 비운다.
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    def _set(**values):
        for name, value in values.items():
            monkeypatch.setenv(name, value)

    return _set


def test_hub_keys_go_to_hub_endpoint_with_hub_headers(naver_env):
    naver_env(NAVER_API_HUB_CLIENT_ID="fake-hub-id", NAVER_API_HUB_CLIENT_SECRET="fake-hub-secret")
    client = _client()
    assert asyncio.run(client.news_search("반도체", display=5)) == ITEMS
    (call,) = client._http.calls
    assert call["url"] == HUB_URL
    assert call["headers"] == {"X-NCP-APIGW-API-KEY-ID": "fake-hub-id", "X-NCP-APIGW-API-KEY": "fake-hub-secret"}
    assert call["params"] == {"query": "반도체", "display": 5, "sort": "date"}


def test_developer_center_keys_alone_go_to_developer_endpoint(naver_env):
    naver_env(NAVER_SEARCH_API_CLIENT_ID="fake-dev-id", NAVER_SEARCH_API_CLIENT_SECRET="fake-dev-secret")
    client = _client()
    assert asyncio.run(client.news_search("반도체")) == ITEMS
    (call,) = client._http.calls
    assert call["url"] == DEV_URL
    assert call["headers"] == {"X-Naver-Client-Id": "fake-dev-id", "X-Naver-Client-Secret": "fake-dev-secret"}


def test_hub_keys_win_when_both_pairs_are_set(naver_env):
    naver_env(
        NAVER_API_HUB_CLIENT_ID="fake-hub-id",
        NAVER_API_HUB_CLIENT_SECRET="fake-hub-secret",
        NAVER_SEARCH_API_CLIENT_ID="fake-dev-id",
        NAVER_SEARCH_API_CLIENT_SECRET="fake-dev-secret",
    )
    client = _client()
    asyncio.run(client.news_search("반도체"))
    (call,) = client._http.calls
    assert call["url"] == HUB_URL
    assert set(call["headers"]) == {"X-NCP-APIGW-API-KEY-ID", "X-NCP-APIGW-API-KEY"}


def test_half_pair_is_not_used_and_no_request_is_sent(naver_env):
    # HUB 는 id 만, 개발자센터는 secret 만 — 어느 짝도 완성이 아니면 부르지 않는다.
    naver_env(NAVER_API_HUB_CLIENT_ID="fake-hub-id", NAVER_SEARCH_API_CLIENT_SECRET="fake-dev-secret")
    client = _client()
    assert asyncio.run(client.news_search("반도체")) == []
    assert client._http.calls == []


def test_auth_failure_returns_empty_list(naver_env):
    naver_env(NAVER_API_HUB_CLIENT_ID="fake-hub-id", NAVER_API_HUB_CLIENT_SECRET="fake-hub-secret")
    client = _client(status_code=401)
    assert asyncio.run(client.news_search("반도체")) == []
    assert len(client._http.calls) == 1
