# -*- coding: utf-8 -*-
"""운영자 공지 — 모든 도구 응답에 끼우는 `notices`(전용 도구 없음).

공지는 환경변수 `OPM_NOTICE_URL` 의 JSON 매니페스트를 배경에서 받아 메모리에 둔다. 이 시험은 가짜 매니페스트(임시 파일의
file:// 주소)와 가짜 도구 응답만 쓴다 — 외부 호출(DART·KRX·Supabase) 0. 시계는 `notices._now` 를 바꿔 끼워 고정한다.

지키는 것(고치기 전 코드에서는 모듈이 없어 전부 실패한다):
  · 공지가 없거나 받지 못했거나 만료·시작 전이면 **응답이 바이트 단위로 그대로**다(`OPM_NOTICE_URL` 없음 = 회귀 비교 same).
  · 받기는 응답 경로에서 기다리지 않는다. 성공 뒤 일시 실패는 마지막 성공본을 72시간까지 쓰고, 실패 뒤 5분은 다시 받지 않는다.
  · 유효 공지는 등록된 공개 도구 전부(`build_mcp().list_tools()` 이름 순회)에서 md 의 📢 블록·json 의 `notices` 칸으로 나간다.
  · once_per_key 는 요청 키의 sha256 으로만 기억한다(원래 키는 어디에도 남지 않는다).
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import hashlib
import json
import logging

import pytest
from starlette.testclient import TestClient

KST = _dt.timezone(_dt.timedelta(hours=9))
T0 = _dt.datetime(2026, 10, 23, 9, 0, tzinfo=KST).timestamp()
HOUR = 3600.0


def _notice(**over):
    base = {
        "id": "n-2026-10-23",
        "level": "info",
        "title": "점검 안내",
        "body": "오늘 밤 서버를 점검합니다.",
        "starts_at": "2026-10-23T00:00:00+09:00",
        "expires_at": "2026-10-30T00:00:00+09:00",
    }
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


class Env:
    """가짜 매니페스트 파일 + 고정 시계 + 배경 받기 비우기."""

    def __init__(self, mod, monkeypatch, tmp_path):
        self.mod, self.mp, self.path = mod, monkeypatch, tmp_path / "notices.json"
        self.t = T0
        self.fetches = 0
        monkeypatch.setattr(mod, "_now", lambda: self.t)
        real_fetch = mod._fetch_bytes_sync

        def counting(url):
            self.fetches += 1
            return real_fetch(url)

        monkeypatch.setattr(mod, "_fetch_bytes_sync", counting)

    @property
    def url(self) -> str:
        return "file://" + str(self.path)

    def use_url(self):
        self.mp.setenv("OPM_NOTICE_URL", self.url)

    def publish(self, notices=None, *, raw: str | None = None):
        self.path.write_text(raw if raw is not None else json.dumps({"notices": notices}, ensure_ascii=False), encoding="utf-8")

    def unpublish(self):
        self.path.unlink(missing_ok=True)

    def flush(self):
        """배경 받기 스레드가 끝날 때까지(일이 없으면 즉시)."""
        self.mod._executor().submit(lambda: None).result(10)

    def advance(self, sec: float):
        self.t += sec

    def warm(self, notices):
        """공지를 올리고 첫 받기를 끝내 둔다(첫 호출은 받기만 시작하므로)."""
        self.publish(notices)
        self.use_url()
        self.mod.active()
        self.flush()


@pytest.fixture()
def env(monkeypatch, tmp_path):
    from open_proxy_mcp.services import notices as mod

    monkeypatch.delenv("OPM_NOTICE_URL", raising=False)
    mod._reset_for_tests()
    e = Env(mod, monkeypatch, tmp_path)
    yield e
    mod._reset_for_tests()


@pytest.fixture()
def request_key():
    """요청 키 ContextVar 를 시험 안에서만 바꾼다."""
    from open_proxy_mcp.dart.client import _ctx_opendart_key

    tokens = []

    def _set(value):
        tokens.append(_ctx_opendart_key.set(value))

    yield _set
    for tok in reversed(tokens):
        _ctx_opendart_key.reset(tok)


def run_tool(name, body="# 제목\n\n본문", *, raises=None, **kwargs):
    """`_wrap_tool_errors` 가 감싼 가짜 도구를 부른다(진짜 도구 본문은 외부 호출을 하므로 쓰지 않는다)."""
    from open_proxy_mcp.tools import _wrap_tool_errors

    async def fn(**_kw):
        if raises is not None:
            raise raises
        return body

    fn.__name__ = name
    return asyncio.run(_wrap_tool_errors(fn)(**kwargs))


def _md_block(env, n=None):
    n = n or _notice()
    from open_proxy_mcp.server import _opm_version

    return f"> 📢 공지 (OPM {_opm_version()} · 10-23) {n['title']} — {n['body']}"


# ── 응답 불변 ────────────────────────────────────────────────────────────────
def test_no_url_leaves_responses_untouched_and_starts_nothing(env):
    md, js = "# t\n\n본문", json.dumps({"tool": "x", "warnings": []}, indent=2)
    assert run_tool("company", md) == md
    assert run_tool("company", js, format="json") == js
    assert env.mod.active() == []
    assert env.fetches == 0 and env.mod._EXEC is None        # 받기 스레드조차 만들지 않는다


def test_first_call_only_starts_the_background_fetch(env):
    env.publish([_notice()])
    env.use_url()
    md = "# 제목\n\n본문"
    assert run_tool("company", md) == md                      # 아직 받은 것이 없다 — 이번 응답에는 붙이지 않는다
    env.flush()
    out = run_tool("company", md)
    assert out == f"# 제목\n\n{_md_block(env)}\n\n본문"          # 다음 응답부터


def test_first_fetch_failure_and_malformed_manifest_leave_responses_alone(env):
    md = "# 제목\n\n본문"
    env.use_url()                                             # 파일이 없다 — 첫 받기 실패
    assert run_tool("company", md) == md
    env.flush()
    assert run_tool("company", md) == md
    env.advance(301)
    for raw in ("not json", json.dumps({"items": []}), json.dumps({"notices": "x"}), json.dumps([1, 2])):
        env.publish(raw=raw)
        env.advance(301)
        assert run_tool("company", md) == md
        env.flush()
        assert run_tool("company", md) == md, raw             # 형식 오류 = 한 번도 성공하지 못했으니 빈 목록


def test_expired_and_not_yet_started_notices_are_not_attached(env):
    md = "# 제목\n\n본문"
    env.warm([
        _notice(id="old", starts_at="2026-10-01T00:00:00+09:00", expires_at="2026-10-23T08:59:59+09:00"),
        _notice(id="future", starts_at="2026-10-23T09:00:01+09:00", expires_at="2026-10-30T00:00:00+09:00"),
    ])
    assert run_tool("company", md) == md
    env.advance(2)                                            # 시작 시각을 지나면 붙는다
    out = run_tool("company", md)
    assert "future" not in out and "점검 안내" in out


# ── 받기 상태 규칙 ───────────────────────────────────────────────────────────
def test_last_good_manifest_survives_transient_failures_for_72_hours_then_goes_empty(env):
    md = "# 제목\n\n본문"
    env.warm([_notice(expires_at="2026-11-30T00:00:00+09:00", show="every_call")])
    assert "점검 안내" in run_tool("company", md)
    env.unpublish()                                           # 이후 받기는 모두 실패
    for hours in (1, 10, 40, 71):
        env.t = T0 + hours * HOUR
        run_tool("company", md)
        env.flush()
        assert "점검 안내" in run_tool("company", md), hours  # 마지막 성공본 유지
    env.t = T0 + 72 * HOUR + 1
    run_tool("company", md)
    env.flush()
    assert run_tool("company", md) == md                      # 72시간을 넘기면 빈 목록
    env.publish([_notice(expires_at="2026-11-30T00:00:00+09:00", show="every_call")])   # 다시 받으면 돌아온다
    env.advance(301)
    run_tool("company", md)
    env.flush()
    assert "점검 안내" in run_tool("company", md)


def test_malformed_manifest_after_success_keeps_last_good(env):
    md = "# 제목\n\n본문"
    env.warm([_notice(show="every_call")])
    env.publish(raw="{broken")
    env.advance(601)
    run_tool("company", md)
    env.flush()
    assert "점검 안내" in run_tool("company", md)


def test_no_refetch_within_five_minutes_after_failure_and_ten_minute_ttl_after_success(env):
    md = "# 제목\n\n본문"
    env.use_url()
    run_tool("company", md)
    env.flush()
    assert env.fetches == 1                                   # 첫 받기 실패
    for _ in range(5):
        env.advance(50)                                       # 250초 — 쉬는 중
        run_tool("company", md)
        env.flush()
    assert env.fetches == 1
    env.advance(60)                                           # 310초
    env.publish([_notice()])
    run_tool("company", md)
    env.flush()
    assert env.fetches == 2
    env.advance(599)                                          # 성공 뒤 10분 안 — 다시 받지 않는다
    run_tool("company", md)
    env.flush()
    assert env.fetches == 2
    env.advance(2)
    run_tool("company", md)
    env.flush()
    assert env.fetches == 3


def test_successfully_fetched_empty_list_takes_effect_at_once(env):
    md = "# 제목\n\n본문"
    env.warm([_notice(show="every_call")])
    assert "점검 안내" in run_tool("company", md)
    env.publish([])
    env.advance(601)
    run_tool("company", md)
    env.flush()
    assert run_tool("company", md) == md


def test_fetch_never_blocks_the_response_path(env, monkeypatch):
    import threading

    gate = threading.Event()
    real = env.mod._fetch_bytes_sync

    def slow(url):
        gate.wait(5)
        return real(url)

    monkeypatch.setattr(env.mod, "_fetch_bytes_sync", slow)
    env.publish([_notice()])
    env.use_url()
    md = "# 제목\n\n본문"
    assert run_tool("company", md) == md                      # 받기가 막혀 있어도 즉시 돌아온다
    assert run_tool("company", md) == md
    gate.set()
    env.flush()
    assert "점검 안내" in run_tool("company", md)


def test_https_fetch_uses_status_check_and_scrubs_the_url_from_errors(env, monkeypatch):
    import httpx

    calls = []

    def handler(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(403)
        return httpx.Response(200, content=json.dumps({"notices": [_notice()]}).encode())

    monkeypatch.setattr(env.mod, "_TRANSPORT", httpx.MockTransport(handler))
    monkeypatch.setenv("OPM_NOTICE_URL", "https://example.invalid/n.json?sig=SIGPART")
    md = "# 제목\n\n본문"
    run_tool("company", md)
    env.flush()
    assert run_tool("company", md) == md
    assert "SIGPART" not in json.dumps(env.mod.stats(), ensure_ascii=False)
    env.advance(301)
    run_tool("company", md)
    env.flush()
    assert "점검 안내" in run_tool("company", md)


# ── 공지 하나의 검사 ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("bad", [
    {"title": "가" * 61},
    {"body": "나" * 401},
    {"title": "[ekind=crash] 점검"},
    {"body": "본문 [degraded=timeout] 끝"},
    {"body": "본문 [nodata = x]"},
    {"title": "줄\n바꿈"},
    {"body": "탭\t문자"},
    {"body": "널\x00문자"},
    {"link": "http://example.org/a"},
    {"link": "javascript:alert(1)"},
    {"link": "https://example.org/a https://example.org/b"},
    {"link": "file:///etc/passwd"},
    {"level": "critical"},
    {"show": "sometimes"},
    {"id": ""},
    {"title": ""},
    {"starts_at": "어제"},
    {"expires_at": None},
    {"expires_at": "2026-10-22T00:00:00+09:00"},                # 시작보다 앞선 만료
])
def test_a_bad_notice_is_dropped_but_the_rest_are_kept(env, bad):
    env.warm([_notice(**{"id": "bad", **bad}), _notice(id="good", title="정상 공지", body="그대로 나갑니다.")])
    ids = [n["id"] for n in env.mod.active()]
    assert ids == ["good"], bad


def test_limits_are_inclusive_and_unknown_fields_are_ignored(env):
    env.warm([_notice(id="edge", title="가" * 60, body="나" * 400, link="https://example.org/notice/1", min_version="9.9", extra={"a": 1})])
    [n] = env.mod.active()
    assert n["id"] == "edge" and len(n["title"]) == 60 and len(n["body"]) == 400 and n["link"] == "https://example.org/notice/1"


def test_defaults_important_means_every_call_and_duplicates_keep_the_first(env):
    env.warm([
        _notice(id="a"),
        _notice(id="a", title="두 번째"),
        _notice(id="b", level="important", show="once_per_key"),
        _notice(id="c", show="company_only", level="important"),
    ])
    shows = {n["id"]: (n["level"], n["show"]) for n in env.mod.active()}
    assert shows == {"a": ("info", "once_per_key"), "b": ("important", "every_call"), "c": ("important", "company_only")}
    assert [n["title"] for n in env.mod.active() if n["id"] == "a"] == ["점검 안내"]


# ── 끼우기 ───────────────────────────────────────────────────────────────────
def test_md_block_goes_right_under_the_title_and_before_resolution_warnings(env):
    env.warm([_notice(link="https://example.org/notice/1", show="every_call")])
    out = run_tool("trading_data", "# 제목\n\n> ⚠ 기존 경고\n\n본문")
    assert out == f"# 제목\n\n{_md_block(env)} https://example.org/notice/1\n\n> ⚠ 기존 경고\n\n본문"
    assert run_tool("trading_data", "제목 없는 응답") == f"{_md_block(env)} https://example.org/notice/1\n\n제목 없는 응답"
    tight = run_tool("trading_data", "# 제목\n본문 바로 아래")     # 빈 줄이 없어도 블록 인용이 본문으로 이어지지 않는다
    assert tight == f"# 제목\n\n{_md_block(env)} https://example.org/notice/1\n\n본문 바로 아래"


def test_json_gets_a_notices_field_beside_warnings(env):
    env.warm([_notice(link="https://example.org/notice/1", show="every_call")])
    src = {"tool": "x", "status": "ok", "warnings": ["데이터 경고"], "data": {"k": 1}}
    out = json.loads(run_tool("x", json.dumps(src, ensure_ascii=False, indent=2), format="json"))
    assert out["warnings"] == ["데이터 경고"]                    # 데이터 경고 칸은 그대로
    assert out["notices"] == [{"id": "n-2026-10-23", "level": "info", "title": "점검 안내",
                               "body": "오늘 밤 서버를 점검합니다.", "link": "https://example.org/notice/1"}]
    assert list(out)[:4] == ["tool", "status", "warnings", "notices"]
    assert out["data"] == {"k": 1}


def test_json_notice_without_a_link_has_no_link_key(env):
    env.warm([_notice(show="every_call")])
    out = json.loads(run_tool("x", json.dumps({"warnings": []}), format="json"))
    assert "link" not in out["notices"][0]


def test_list_json_is_not_broken(env):
    env.warm([_notice(show="every_call")])
    raw = "[1, 2, 3]"
    out = run_tool("x", raw, format="json")
    assert out == raw                                           # dict 가 아닌 JSON — 깨뜨리지 않는다
    assert run_tool("x", "\"문자열\"", format="json") == "\"문자열\""
    assert json.loads(run_tool("x", "{}", format="json"))["notices"][0]["id"] == "n-2026-10-23"   # 빈 dict 는 붙일 자리가 있다


def test_attaching_is_idempotent(env):
    env.warm([_notice(show="every_call")])
    once = run_tool("company", "# 제목\n\n본문")
    assert env.mod.attach("company", once) == once
    js = run_tool("company", json.dumps({"warnings": []}), format="json")
    assert env.mod.attach("company", js) == js


def test_degrade_path_carries_the_notice_but_the_bug_path_does_not(env):
    from open_proxy_mcp.dart.client import DartClientError
    from mcp.server.mcpserver.exceptions import ToolError

    env.warm([_notice(show="every_call")])
    md = run_tool("company", raises=DartClientError("800", "x"), format="md")
    assert md.startswith("# company\n\n" + _md_block(env))
    assert "[degraded=" in md or "[nodata=" in md           # 통계 표지는 그대로
    js = json.loads(run_tool("company", raises=DartClientError("020", "x"), format="json"))
    assert js["notices"][0]["id"] == "n-2026-10-23" and js["status"] == "error" and js["warnings"]
    with pytest.raises(ToolError) as ei:
        run_tool("company", raises=ValueError("boom"), format="md")
    assert str(ei.value) == "[ekind=crash] boom"                 # 코드 버그 경로의 본문은 그대로
    assert "점검" not in str(ei.value)


def test_attach_failure_never_breaks_a_tool_response(env, monkeypatch):
    env.warm([_notice(show="every_call")])

    def boom(*_a, **_k):
        raise RuntimeError("공지 쪽 사고")

    monkeypatch.setattr(env.mod, "_render_md", boom)
    monkeypatch.setattr(env.mod, "_render_json", boom)
    md = "# 제목\n\n본문"
    assert run_tool("company", md) == md


# ── 노출 판정 ────────────────────────────────────────────────────────────────
def test_once_per_key_remembers_only_the_hash_and_per_key(env, request_key, caplog):
    req_key = "Fake-Request-Key-For-Test"
    env.warm([_notice(show="once_per_key")])
    md = "# 제목\n\n본문"
    request_key(req_key)
    with caplog.at_level(logging.DEBUG):
        first = run_tool("company", md)
        second = run_tool("company", md)
        request_key(req_key.upper())                          # usage.record 와 같은 식 — 소문자로 맞춘 해시
        third = run_tool("company", md)
    assert "점검 안내" in first and second == md and third == md
    request_key("another-key-value-0123456789abcdef0123")
    assert "점검 안내" in run_tool("company", md)             # 다른 키에는 붙는다
    digest = hashlib.sha256(req_key.lower().encode()).hexdigest()
    seen = list(env.mod._seen)
    assert ("n-2026-10-23", digest) in seen
    blob = repr(seen) + repr(env.mod._state) + caplog.text + first + json.dumps(env.mod.stats())
    assert req_key not in blob and req_key.lower() not in blob and req_key.upper() not in blob


def test_requests_without_a_key_get_it_once_per_machine(env, request_key):
    env.warm([_notice(show="once_per_key")])
    request_key(None)
    md = "# 제목\n\n본문"
    assert "점검 안내" in run_tool("company", md)
    assert run_tool("company", md) == md
    request_key("")
    assert run_tool("company", md) == md                      # 빈 키도 키 없음과 같다


def test_once_per_key_is_not_spent_when_nothing_could_be_attached(env, request_key):
    env.warm([_notice(show="once_per_key")])
    request_key("k1-0123456789abcdef0123456789abcdef")
    assert run_tool("x", "[1]", format="json") == "[1]"       # 붙일 자리가 없었다 — 본 것으로 치지 않는다
    assert "점검 안내" in run_tool("company", "# 제목\n\n본문")


def test_seen_set_is_a_bounded_lru(env, request_key, monkeypatch):
    monkeypatch.setattr(env.mod, "_SEEN_MAX", 3)
    env.warm([_notice(show="once_per_key")])
    md = "# 제목\n\n본문"
    for i in range(5):
        request_key(f"key-{i}-0123456789abcdef0123456789")
        run_tool("company", md)
    assert len(env.mod._seen) == 3
    request_key("key-0-0123456789abcdef0123456789")           # 밀려난 키는 다시 받는다
    assert "점검 안내" in run_tool("company", md)
    assert env.mod._SEEN_MAX == 3 and len(env.mod._seen) == 3


def test_every_call_and_important_attach_every_time_company_only_only_to_company(env, request_key):
    request_key("kkk-0123456789abcdef0123456789abcdef")
    env.warm([
        _notice(id="ev", title="매번", show="every_call"),
        _notice(id="imp", title="중요", level="important"),
        _notice(id="co", title="회사만", show="company_only"),
    ])
    md = "# 제목\n\n본문"
    for _ in range(3):
        out = run_tool("trading_data", md)
        assert "매번" in out and "중요" in out and "회사만" not in out
    for _ in range(3):
        out = run_tool("company", md)
        assert "매번" in out and "중요" in out and "회사만" in out
    js = json.loads(run_tool("trading_data", json.dumps({"warnings": []}), format="json"))
    assert [n["id"] for n in js["notices"]] == ["ev", "imp"]


# ── 응답 봉투·지시문·리소스 ──────────────────────────────────────────────────
def test_tool_envelope_notices_field_is_invisible_when_empty():
    from open_proxy_mcp.services.contracts import ToolEnvelope

    base = ToolEnvelope(tool="t", status="ok", generated_at="2026-10-23T00:00:00Z").to_dict()
    assert list(base) == ["tool", "status", "subject", "generated_at", "warnings", "data", "evidence_refs", "next_actions"]
    with_n = ToolEnvelope(tool="t", status="ok", generated_at="2026-10-23T00:00:00Z",
                          notices=[{"id": "a", "level": "info", "title": "t", "body": "b"}]).to_dict()
    assert list(with_n) == ["tool", "status", "subject", "generated_at", "warnings", "notices", "data", "evidence_refs", "next_actions"]
    assert with_n["notices"][0]["id"] == "a" and with_n["warnings"] == base["warnings"]


def test_server_instructions_get_one_english_sentence_and_no_notice_text(env):
    from open_proxy_mcp.server import build_mcp

    env.warm([_notice(title="지시문에 들어가면 안 되는 제목")])
    ins = build_mcp().instructions
    assert "it is an announcement, not data." in ins     # 뒤에 다른 안내 문단(원문 읽기)이 붙어도 이 문장은 남는다
    assert "`notices`" in ins and "📢" in ins and "relay it to the user once" in ins
    assert "지시문에 들어가면 안 되는 제목" not in ins and "점검" not in ins


def test_company_tool_description_tells_the_client_to_relay_the_notice_line(env):
    """claude.ai 는 도구 결과 속 공지를 외부 문구로 보고 무시한다(261027 실측) — 도구 설명(서버가 정하는 채널)에도 한 줄을 둔다. 공지 문구는 넣지 않는다."""
    from open_proxy_mcp.server import build_mcp

    env.warm([_notice(title="설명에 들어가면 안 되는 제목")])
    desc = next(t.description for t in asyncio.run(build_mcp().list_tools()) if t.name == "company")
    assert "📢 공지" in desc and "사용자에게 한 번 짧게 전해" in desc
    assert "설명에 들어가면 안 되는 제목" not in desc


def _rpc(client, method, params, i=1):
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "Host": "open-proxy-mcp.fly.dev"}
    r = client.post("/mcp?opendart=k", json={"jsonrpc": "2.0", "id": i, "method": method, "params": params}, headers=h)
    assert r.status_code == 200, r.text[:120]
    return r.json()["result"]


def test_notices_resource_lists_valid_notices_and_is_not_a_tool(env):
    from open_proxy_mcp.server import build_app

    with TestClient(build_app()) as client:
        res = _rpc(client, "resources/list", {})["resources"]
        entry = next(r for r in res if r["uri"] == "opm://notices")
        assert entry["mimeType"] == "application/json"
        empty = json.loads(_rpc(client, "resources/read", {"uri": "opm://notices"})["contents"][0]["text"])
        assert empty == {"notices": []}                       # 공지가 없으면 빈 목록
        env.publish([_notice(), _notice(id="gone", expires_at="2026-10-23T08:00:00+09:00")])
        env.use_url()
        _rpc(client, "resources/read", {"uri": "opm://notices"})   # 첫 읽기는 받기만 시작한다
        env.flush()
        body = json.loads(_rpc(client, "resources/read", {"uri": "opm://notices"})["contents"][0]["text"])
        assert [n["id"] for n in body["notices"]] == ["n-2026-10-23"]
        assert body["notices"][0]["title"] == "점검 안내" and body["notices"][0]["show"] == "once_per_key"
        tools = {t["name"] for t in _rpc(client, "tools/list", {})["tools"]}
        assert tools.isdisjoint({"notice", "notices", "operator_notices"})   # 새 도구 0


# ── 등록된 공개 도구 전부 ────────────────────────────────────────────────────
def _registered_names():
    from open_proxy_mcp.server import build_mcp

    return sorted(t.name for t in asyncio.run(build_mcp().list_tools()))


def test_every_registered_tool_is_wrapped_by_the_notice_boundary():
    from open_proxy_mcp.server import build_mcp

    mcp = build_mcp()
    tools = mcp._tool_manager.list_tools()                    # 등록된 Tool 객체의 fn 은 경계가 감싼 함수다
    assert tools
    unwrapped = [t.name for t in tools if not getattr(t.fn, "attaches_notices", False)]
    assert unwrapped == []


def _fake_registry(monkeypatch):
    """진짜 `register_all_tools` 가 도구 이름마다 부르는 경계(`_wrap_tool_errors`)에 가짜 본문을 끼운다."""
    import open_proxy_mcp.tools as tools_pkg

    real_wrap = tools_pkg._wrap_tool_errors

    def fake_wrap(fn):
        name = fn.__name__

        async def body(**kw):
            if kw.get("format") == "json":
                return json.dumps({"tool": name, "status": "ok", "warnings": [], "data": {}}, indent=2)
            return f"# {name}\n\n본문"

        body.__name__ = name
        return real_wrap(body)

    class Rec:
        def __init__(self):
            self.fns = {}

        def tool(self, *a, **k):
            def deco(fn):
                self.fns[fn.__name__] = fn
                return fn
            return deco

        def resource(self, *a, **k):                           # 확장이 거는 리소스는 이 시험의 관심 밖
            return lambda fn: fn

    monkeypatch.setattr(tools_pkg, "_wrap_tool_errors", fake_wrap)
    rec = Rec()
    tools_pkg.register_all_tools(rec)
    from open_proxy_mcp.extensions import load_extensions

    load_extensions(rec)                                       # 확장이 거는 도구(설치돼 있으면)도 같은 경계를 지나야 한다
    return rec.fns


def test_all_registered_tools_show_the_notice_in_md_and_json(env, monkeypatch, request_key):
    request_key("every-tool-key-0123456789abcdef0123")
    env.warm([_notice(show="every_call")])
    fns = _fake_registry(monkeypatch)
    names = _registered_names()
    assert sorted(fns) == names and len(names) > 20
    for name in names:
        md = asyncio.run(fns[name]())
        assert md.startswith(f"# {name}\n\n> 📢 공지 (OPM "), name
        js = json.loads(asyncio.run(fns[name](format="json")))
        assert js["notices"][0]["id"] == "n-2026-10-23" and js["warnings"] == [], name


def test_all_registered_tools_are_byte_identical_without_a_notice_url(env, monkeypatch):
    fns = _fake_registry(monkeypatch)
    names = _registered_names()
    assert sorted(fns) == names
    for name in names:
        assert asyncio.run(fns[name]()) == f"# {name}\n\n본문", name
        assert asyncio.run(fns[name](format="json")) == json.dumps({"tool": name, "status": "ok", "warnings": [], "data": {}}, indent=2), name
    env.use_url()                                              # 주소는 있는데 받은 공지가 없을 때도 같다
    env.flush()
    for name in names:
        assert asyncio.run(fns[name]()) == f"# {name}\n\n본문", name


# ── 비밀값 ───────────────────────────────────────────────────────────────────
def test_output_carries_no_secret_shaped_text(env, request_key):
    import re

    key = "S3cretKey" + "0123456789abcdef" * 2
    request_key(key)
    env.warm([_notice(show="every_call", link="https://example.org/notice/1")])
    outs = [run_tool("company", "# 제목\n\n본문"), run_tool("company", json.dumps({"warnings": []}), format="json"), env.mod.resource_json()]
    digest = hashlib.sha256(key.lower().encode()).hexdigest()
    for out in outs:
        assert key not in out and digest not in out
        assert not re.search(r"[0-9a-f]{40,}", out)           # 해시·키 꼴의 긴 16진수 열이 없다


# ── 중요도별 노출 빈도·표 응답 (261025) ─────────────────────────────────────────
def test_show_defaults_follow_importance_rarely_daily_every_call(env):
    env.warm([_notice(id="i", level="info"), _notice(id="n", level="normal"), _notice(id="m", level="important")])
    shows = {n["id"]: n["show"] for n in env.mod.active()}
    assert shows == {"i": "once_per_key", "n": "daily", "m": "every_call"}


@pytest.mark.parametrize("show, gap, again", [("hourly", HOUR, True), ("hourly", 59 * 60, False),
                                              ("daily", 24 * HOUR, True), ("daily", 23 * HOUR, False),
                                              ("weekly", 7 * 24 * HOUR, True), ("weekly", 6 * 24 * HOUR, False),
                                              ("once_per_key", 30 * 24 * HOUR, False)])
def test_daily_and_weekly_come_back_per_key_after_the_interval(env, request_key, show, gap, again):
    env.warm([_notice(show=show, expires_at="2027-01-01T00:00:00+09:00")])
    md = "# 제목\n\n본문"
    request_key("cadence-key-0123456789abcdef0123456789")
    assert "점검 안내" in run_tool("company", md)
    assert run_tool("company", md) == md                      # 간격 안에서는 다시 붙지 않는다
    request_key("other-key-0123456789abcdef0123456789ab")
    assert "점검 안내" in run_tool("company", md)             # 키마다 따로 센다
    request_key("cadence-key-0123456789abcdef0123456789")
    env.advance(gap)
    env.mod._state["fetched"] = env.t                          # 매니페스트는 계속 신선하다고 둔다(빈도만 본다)
    assert ("점검 안내" in run_tool("company", md)) is again


def test_a_bare_list_from_a_table_endpoint_is_a_manifest_and_nulls_are_blank(env):
    row = _notice(level="normal") | {"body": None, "link": None, "show": None}
    env.publish(raw=json.dumps([row, {"id": "bad", "level": "loud"}], ensure_ascii=False))
    env.use_url()
    env.mod.active()
    env.flush()
    assert [(n["id"], n["show"], n["body"]) for n in env.mod.active()] == [("n-2026-10-23", "daily", "")]


def test_the_read_key_goes_in_the_apikey_header_and_nowhere_else(env, monkeypatch, caplog):
    import httpx

    seen = []

    def handler(request):
        seen.append(dict(request.headers))
        return httpx.Response(200, content=json.dumps([_notice(show="every_call")]).encode())

    monkeypatch.setattr(env.mod, "_TRANSPORT", httpx.MockTransport(handler))
    monkeypatch.setenv("OPM_NOTICE_URL", "https://example.invalid/rest/v1/notices?select=*")
    monkeypatch.setenv("OPM_NOTICE_APIKEY", "PUBLISHABLE-READ-KEY-VALUE")
    md = "# 제목\n\n본문"
    with caplog.at_level(logging.DEBUG):
        run_tool("company", md)
        env.flush()
        out = run_tool("company", md)
    assert "점검 안내" in out and seen[0]["apikey"] == "PUBLISHABLE-READ-KEY-VALUE"
    blob = json.dumps(env.mod.stats(), ensure_ascii=False) + repr(env.mod._state) + caplog.text + out + env.mod.resource_json()
    assert "PUBLISHABLE-READ-KEY-VALUE" not in blob
