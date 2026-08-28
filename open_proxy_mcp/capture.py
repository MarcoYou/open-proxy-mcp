"""MCP `tools/call` 의 **요청과 응답 전문**을 파일로 남긴다 — 기본은 꺼져 있다.

## 왜 있나

OPM 은 파서가 아니라 에이전틱 제품이다. 사용자는 우리 응답을 **자기 AI 에 물려**
그 AI 가 만든 화면을 본다. 그래서 시험에서 봐야 하는 것이 셋이다 —
① 우리가 데이터를 가져왔나 ② 그것이 답변에 실제로 쓰였나
③ **우리가 못 준 것을 그 AI 가 자기 지식으로 메우지 않았나**.

②·③ 은 「AI 답변」과 「그때 도구가 실제로 준 응답」을 나란히 놓아야 잴 수 있는데,
클라이언트 세션 기록에는 답변만 남고 도구 응답은 남지 않는다(260828 실측). 대조할
왼쪽이 없으니 환각을 잡을 방법 자체가 없었다. 그 왼쪽을 여기서 만든다.

## 켜는 법

`OPM_CAPTURE_DIR` 환경변수가 있을 때만 돈다. 없으면 미들웨어는 첫 줄에서 통과시키므로
운영(fly)에서는 아무 일도 일어나지 않는다 — 배포에 그 변수를 넣지 않는다.
로컬 시험 서버는 `run_bpm.sh` 가 항상 켠다.

## 남기는 것

`$OPM_CAPTURE_DIR/calls-YYYYMMDD.jsonl` 에 **한 호출당 한 줄**, 이어쓰기.
채점기가 키 이름으로 읽으므로 키를 바꾸지 않는다.

    ts arguments tool response_text is_error duration_ms bytes

`response_text` 는 **자르지 않는다.** 이 파일의 목적이 그것이다 — 사용자 AI 가 실제로
받은 본문 전문과 화면에 나온 글자를 대조하는 것. 요약·절단은 대조를 무의미하게 만든다.

## 지키는 선

- **서빙을 막지 않는다.** 쓰기는 응답을 클라이언트로 내보낸 **뒤**에 하고, 어떤 예외도
  밖으로 내보내지 않는다. 실패는 프로세스당 한 번만 경고로 남긴다(매 호출 찍으면
  로그가 잠긴다).
- **키를 안 남긴다.** `ApiKeyMiddleware` 가 로그에 쓰는 것과 같은 마스킹을 요청 인자와
  응답 본문 양쪽에 건다. DART 뷰어 URL 에 `crtfc_key=` 가 실려 나올 수 있다.
"""

import json
import logging
import os
import re
import time
from datetime import datetime

logger = logging.getLogger(__name__)

#: 이 변수가 있을 때만 기록한다. 없으면 미들웨어는 통과 경로만 탄다(오버헤드 0).
CAPTURE_ENV = "OPM_CAPTURE_DIR"

#: URL 에 실린 키 값을 가린다. `server.RedactApiKey` 와 같은 모양이되, 기록물에는
#: 사용자 키(`opendart`)와 상류 키(`crtfc_key`) 둘 다 나올 수 있어 함께 본다.
_SECRET_IN_URL = re.compile(r"((?:opendart|crtfc_key)=)[^&\s\"'\\]+")

#: 요청 본문을 **인자를 꺼낼 만큼만** 읽는다. OPM 인자는 회사명·코드·연도라 이 창을
#: 넘길 일이 없다. 상한을 두는 이유는 1 GB VM 에 OOM 이력(260804)이 있어서다 —
#: 응답과 달리 요청은 통째로 담을 이유가 없다.
_MAX_ARG_BYTES = 64 * 1024

_warned = False


def capture_dir() -> str | None:
    """켜져 있으면 기록 디렉터리, 아니면 None. **호출마다** 환경을 읽는다 —
    프로세스 수명 내내 고정이지만, 시험이 켜고 끄며 확인할 수 있어야 한다."""
    return (os.environ.get(CAPTURE_ENV) or "").strip() or None


def _warn_once(exc: BaseException) -> None:
    """기록 실패는 **한 번만** 알린다. 매 호출 찍으면 진짜 신호가 묻힌다."""
    global _warned
    if not _warned:
        _warned = True
        logger.warning(
            "%s 기록 실패 — 이 프로세스에서는 더 알리지 않는다: %r", CAPTURE_ENV, exc
        )


def _mask(text: str) -> str:
    return _SECRET_IN_URL.sub(r"\1***", text)


def _extract_call(body: bytes):
    """JSON-RPC 요청 → `(tool명, arguments)`. `tools/call` 이 아니면 `(None, None)`.

    `initialize`·`tools/list` 같은 핸드셰이크는 여기서 걸러진다 — 잡음을 남기지 않는다.
    """
    try:
        d = json.loads(body)
    except Exception:
        return None, None
    if not isinstance(d, dict) or d.get("method") != "tools/call":
        return None, None
    params = d.get("params") or {}
    if not isinstance(params, dict):
        return None, None
    args = params.get("arguments")
    return params.get("name") or "", args if isinstance(args, dict) else {}


def _blocks_to_text(content) -> str:
    """content 블록들 → 사용자 AI 가 받는 텍스트.

    텍스트가 아닌 블록(이미지·리소스)은 **그 사실을 표시**한다. 조용히 빠뜨리면
    대조하는 쪽이 「도구가 안 줬다」와 「우리가 안 적었다」를 구분할 수 없다.
    블록이 하나면 그 텍스트 그대로다 — 대부분의 OPM tool 이 이 경우다.
    """
    parts = []
    for b in content or []:
        if isinstance(b, dict) and b.get("type") == "text":
            parts.append(b.get("text") or "")
        elif isinstance(b, dict):
            parts.append(f"[non-text content block: {b.get('type') or 'unknown'}]")
        else:
            parts.append("[non-text content block: unknown]")
    return "\n".join(parts)


def _parse_response(raw: bytes, content_type: str):
    """응답 wire 바이트 → `(response_text, is_error)`.

    전송 방식 둘 다 본다 — `json_response=True` 인 지금은 JSON 이지만, SSE 로 바뀌어도
    기록이 조용히 비지 않아야 한다. 어느 쪽으로도 못 읽으면 원문을 그대로 남긴다:
    **못 읽었다고 빈칸으로 두지 않는다**(그러면 「응답이 비었다」와 구분되지 않는다).
    """
    payloads = []
    if "text/event-stream" in (content_type or ""):
        for line in raw.split(b"\n"):
            if line.startswith(b"data:"):
                payloads.append(line[5:].strip())
    else:
        payloads.append(raw.strip())

    for chunk in payloads:
        if not chunk:
            continue
        try:
            d = json.loads(chunk)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        if "error" in d:                       # JSON-RPC 프로토콜 오류
            return json.dumps(d["error"], ensure_ascii=False), True
        result = d.get("result")
        if isinstance(result, dict):
            return (_blocks_to_text(result.get("content")),
                    bool(result.get("isError")))
    # 우리가 못 읽은 경우 — 원문을 그대로 넘겨 판단은 읽는 쪽이 한다
    return raw.decode("utf-8", "replace"), False


def write_record(tool: str, arguments, response_text: str, is_error: bool,
                 duration_ms: int, nbytes: int) -> str | None:
    """한 줄 이어쓴다. 성공하면 파일 경로, 꺼져 있거나 실패하면 None.

    예외를 밖으로 내지 않는다 — 기록은 서빙을 막을 자격이 없다.
    """
    d = capture_dir()
    if not d:
        return None
    try:
        os.makedirs(d, exist_ok=True)
        now = datetime.now().astimezone()
        path = os.path.join(d, f"calls-{now.strftime('%Y%m%d')}.jsonl")
        rec = {
            "ts": now.isoformat(timespec="seconds"),
            "tool": tool,
            "arguments": arguments,
            "response_text": response_text,
            "is_error": is_error,
            "duration_ms": duration_ms,
            "bytes": nbytes,          # 응답 wire 바이트(= 호출측이 무는 비용의 대리 지표)
        }
        line = _mask(json.dumps(rec, ensure_ascii=False)) + "\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
        return path
    except Exception as exc:          # 디렉터리 없음·권한 없음·디스크 참 — 전부 여기로
        _warn_once(exc)
        return None


class CaptureMiddleware:
    """`tools/call` 의 요청·응답 전문을 JSONL 로 남기는 ASGI 미들웨어.

    `ApiKeyMiddleware` **안쪽**에 둔다 — 키 게이트를 통과한 진짜 호출만 남기고,
    401 로 되돌아간 요청은 기록에 섞지 않는다.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # 꺼져 있으면 여기서 끝 — 감싸지도 않으므로 운영 지연은 문자 그대로 0 이다.
        if scope["type"] != "http" or not capture_dir():
            await self.app(scope, receive, send)
            return
        if scope.get("method") != "POST" or not scope.get("path", "").startswith("/mcp"):
            await self.app(scope, receive, send)
            return

        start = time.monotonic()
        buffered = []
        body = b""
        more = True
        while more:
            msg = await receive()
            buffered.append(msg)
            if msg["type"] == "http.request":
                body += msg.get("body", b"")
                more = msg.get("more_body", False)
                if len(body) >= _MAX_ARG_BYTES:
                    break     # 나머지는 replay 가 receive() 로 그대로 흘려보낸다
            else:
                more = False

        idx = 0

        async def replay():
            nonlocal idx
            if idx < len(buffered):
                m = buffered[idx]
                idx += 1
                return m
            return await receive()

        tool, arguments = _extract_call(body)
        if tool is None:              # 핸드셰이크·목록 조회 — 남기지 않는다
            await self.app(scope, replay, send)
            return

        chunks: list[bytes] = []
        state = {"ctype": "", "nbytes": 0, "done": False}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                for k, v in message.get("headers") or []:
                    if k.lower() == b"content-type":
                        state["ctype"] = v.decode("latin-1")
            elif message["type"] == "http.response.body" and not state["done"]:
                chunk = message.get("body", b"") or b""
                chunks.append(chunk)
                state["nbytes"] += len(chunk)
                if not message.get("more_body", False):
                    state["done"] = True
                    # **먼저 내보내고 그 다음에 적는다.** 클라이언트가 기다리는 시간에
                    # 파일 I/O 를 끼워 넣지 않기 위해서다.
                    await send(message)
                    duration = int((time.monotonic() - start) * 1000)
                    text, is_err = _parse_response(b"".join(chunks), state["ctype"])
                    write_record(tool, arguments, text, is_err, duration, state["nbytes"])
                    chunks.clear()
                    return
            await send(message)

        await self.app(scope, replay, send_wrapper)
