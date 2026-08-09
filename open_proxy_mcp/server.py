"""OpenProxy MCP 서버 — FastMCP 진입점"""

import argparse
import logging
import os
import re
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from open_proxy_mcp.prompts import register_all_prompts
from open_proxy_mcp.tools import register_all_tools


#: 사용자 DART 키는 쿼리스트링(`?opendart=`)으로 들어온다. uvicorn 액세스 로그는 요청 라인을
#: 통째로 찍으므로 그대로 두면 배포 로그에 유저 키가 평문으로 쌓인다(260806 실측 확인).
#: 로그 자체는 운영 진단에 쓰이므로 끄지 않고 값만 가린다.
_API_KEY_IN_URL = re.compile(r"((?:opendart|crtfc_key)=)[^&\s\"']+")


class RedactApiKey(logging.Filter):
    """로그 레코드에서 URL 안의 API 키 값을 가린다. 메시지·인자 양쪽을 본다."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        if isinstance(record.msg, str):
            record.msg = _API_KEY_IN_URL.sub(r"\1***", record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                _API_KEY_IN_URL.sub(r"\1***", a) if isinstance(a, str) else a
                for a in record.args
            )
        return True


def install_api_key_redaction() -> None:
    """액세스 로그를 내보내는 로거 전부에 마스킹 필터를 건다.

    uvicorn 은 로거를 자체 설정으로 다시 세우므로 `uvicorn.run` **직전**에 걸어야 한다.
    필터는 핸들러가 아니라 로거에 달아, 핸들러가 나중에 바뀌어도 살아남게 한다.
    """
    redactor = RedactApiKey()
    for name in ("uvicorn.access", "uvicorn.error", "uvicorn"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactApiKey) for f in logger.filters):
            logger.addFilter(redactor)


def build_mcp() -> FastMCP:
    """Build the single supported MCP tool surface."""
    # 이 이름이 클라이언트 커넥터 목록에 뜨고, MCP 양식(prompt)의 슬래시 명령
    # `/mcp__<서버이름>__<양식이름>` 가운데 자리에도 들어간다 — 짧을수록 부르기 쉽다.
    # fly 앱 이름(=URL `open-proxy-mcp.fly.dev`)과 레포명은 그대로 둔다.
    mcp = FastMCP(
        "openproxy",
        # 여기엔 **도구를 가로지르는 규칙만** 둔다. 도구 하나로 표현되는 것은 그 도구의
        # description 에 있어야 한다(설명 총 23,673자가 이미 컨텍스트에 있다).
        # 실측값(후보 수·회사명·종목코드)은 절대 넣지 않는다 — 등록부가 바뀌면 조용히 썩는다.
        instructions=(
            "Korean-listed company disclosure (DART) analysis. Natural-language questions "
            "work — you don't need tool names. Answer in the user's language.\n\n"
            "Resolve a company once with `company` and pass the returned name, ticker, and "
            "corp_code downstream; otherwise every tool re-resolves the name.\n\n"
            "Read `status` and `warnings` before answering — they carry resolution confidence, "
            "missing filings, and basis fallbacks. State only figures that trace to a value in "
            "the response; you may compute from them if you say so. A value you did not get is "
            "\"not found in the filings read\" — never fill it from prior knowledge, never turn "
            "\"not found\" into \"there is none\".\n\n"
            "Figures carry their own basis (unit, currency, consolidated/separate, period, "
            "confirmed/provisional/restated). Keep it attached, and never place figures from "
            "different tools or periods side by side without saying the bases differ."
        ),
    )
    register_all_tools(mcp)
    register_all_prompts(mcp)

    # 헬스 엔드포인트 — 인증 없이 200 을 내는 유일한 경로.
    # 260729 사고: mcp 2.0.0 이 fastmcp 를 제거해 서버가 부팅 즉시 죽었는데, 헬스체크가 없어
    # fly 는 「VM 이 켜졌다」만 보고 배포를 성공 처리했고 CI 도 초록이었다.
    # **여기 안에 붙여야 한다** — `main()` 이 `build_mcp()` 로 새 인스턴스를 만들므로
    # 모듈 레벨 `mcp` 에 붙인 라우트는 실제 서빙되는 앱에 없다(260729 2차 실측: /health 404).
    # 260804 사고: fly 머신이 OOM(exit_code=137)으로 죽었다. 캐시 예산을 항목 수로 잡아 둔 게
    # 원인이었는데, 예산 점유를 밖에서 볼 방법이 없어 「죽고 나서야」 알았다. 이제 캐시 점유율과
    # evict 횟수를 헬스에 실어 예산이 차오르는 걸 미리 본다.
    @mcp.custom_route("/health", methods=["GET"])
    async def _health(_request):
        from starlette.responses import JSONResponse
        from open_proxy_mcp.dart.client import cache_stats
        return JSONResponse({
            "status": "ok",
            "tools": len(await mcp.list_tools()),
            "cache": cache_stats(),
        })

    return mcp


mcp = build_mcp()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
    )
    parser.add_argument(
        "--sse",
        action="store_true",
        help="하위호환 옵션: --transport sse 와 동일",
    )
    args = parser.parse_args()
    if args.sse:
        args.transport = "sse"
    mcp = build_mcp()

    if args.transport in ("sse", "streamable-http"):
        mcp.settings.host = os.environ.get("FASTMCP_HOST", "0.0.0.0")
        mcp.settings.port = int(os.environ.get("FASTMCP_PORT", "8000"))
        allowed_hosts = [
            "open-proxy-mcp.fly.dev",
            "localhost:8000",
            "127.0.0.1:8000",
            "0.0.0.0:8000",
        ]
        extra_hosts = os.environ.get("FASTMCP_ALLOWED_HOSTS", "").strip()
        if extra_hosts:
            allowed_hosts.extend([h.strip() for h in extra_hosts.split(",") if h.strip()])
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
        )

    if args.transport == "streamable-http":
        # 무상태 HTTP: 각 요청이 독립(세션 in-memory 미보관) → fly 다중 머신에서 라우팅이
        # 갈려도 "Session not found" 없음. OPM tool은 무상태(요청마다 키·파라미터 자급)라
        # 세션 유지 불필요. 2머신 유지하면서 세션 어피니티 문제 해결. (2026-06)
        mcp.settings.stateless_http = True
        mcp.settings.json_response = True

        import uvicorn
        from starlette.middleware import Middleware
        from starlette.types import ASGIApp, Receive, Scope, Send
        from open_proxy_mcp.dart.client import set_request_api_key
        from open_proxy_mcp import usage

        def _extract_tool(body: bytes):
            """JSON-RPC 본문에서 호출 대상 추출 → (이름, tools/call 여부).
            tools/call이면 tool명, 아니면 method명."""
            try:
                import json
                d = json.loads(body)
                method = d.get("method", "")
                if method == "tools/call":
                    return d.get("params", {}).get("name") or "tools/call", True
                return method or None, False
            except Exception:
                return None, False

        _ERR_PATTERNS = (b'"isError":true', b'"isError": true',
                         b'"error":{"code"', b'"error": {"code"')
        import re as _re
        _EKIND_RE = _re.compile(rb"\[ekind=(\w+)\]")  # tool 래퍼가 붙인 error_kind 태그

        class ApiKeyMiddleware:
            """URL 쿼리 파라미터 ?opendart=키 → contextvar 세팅 + 사용 통계 기록."""

            def __init__(self, app: ASGIApp):
                self.app = app

            async def __call__(self, scope: Scope, receive: Receive, send: Send):
                if scope["type"] != "http":
                    await self.app(scope, receive, send)
                    return
                from urllib.parse import parse_qs
                qs = parse_qs(scope.get("query_string", b"").decode())
                opendart = qs.get("opendart", [None])[0]
                if opendart:
                    set_request_api_key(opendart)
                elif scope.get("path", "").startswith("/mcp"):
                    # 키 없는 서빙 요청 거절(260705) — fly secrets에 서버용 OPENDART 키(배치·DB
                    # 갱신 내부용)가 있으므로, 거절하지 않으면 env 폴백으로 서버 키가 조용히
                    # 소모된다. 서빙은 반드시 유저 키(?opendart=)로.
                    import json as _json
                    body = _json.dumps({"error": "opendart API key required",
                                        "hint": "connect with ?opendart=<your DART key>"}).encode()
                    await send({"type": "http.response.start", "status": 401,
                                "headers": [(b"content-type", b"application/json")]})
                    await send({"type": "http.response.body", "body": body})
                    return

                # 사용 통계 기록 (요청 1건 = 이벤트 1건). 기록은 비동기 큐라 지연 0.
                # 요청 본문(JSON-RPC)을 버퍼링해 tool명 추출 후 그대로 앱에 재생(replay).
                if opendart and scope.get("path", "").startswith("/mcp"):
                    import time as _t
                    start = _t.monotonic()
                    buffered = []
                    body = b""
                    more = True
                    while more:
                        msg = await receive()
                        buffered.append(msg)
                        if msg["type"] == "http.request":
                            body += msg.get("body", b"")
                            more = msg.get("more_body", False)
                        else:
                            more = False
                    tool, is_call = _extract_tool(body)

                    # 장부를 **여기서** 만든다. 하류(캐시·회사해석)는 이 dict 를 고치기만 하고,
                    # 우리는 같은 dict 를 들고 있으니 응답이 끝난 뒤 그대로 읽으면 된다.
                    # 하류가 값을 올려보내게 하면 안 된다 — ContextVar 는 위로 안 흐른다.
                    from open_proxy_mcp.dart.client import new_request_ledger
                    ledger = new_request_ledger()

                    idx = 0
                    async def replay():
                        nonlocal idx
                        if idx < len(buffered):
                            m = buffered[idx]; idx += 1; return m
                        return await receive()

                    # tools/call은 응답 본문(SSE/JSON)에서 isError를 스캔한 뒤 본문 종료 시 기록.
                    # (툴 내부 실패는 HTTP 200에 실려 오므로 status만으론 못 잡음)
                    # 그 외(핸드셰이크 등)는 기존대로 응답 시작 시 기록.
                    rec = {"status": 0, "latency": None, "err": False, "tail": b"",
                           "done": False, "ekind": None, "bytes": 0}

                    async def send_wrapper(message):
                        if message["type"] == "http.response.start":
                            rec["latency"] = int((_t.monotonic() - start) * 1000)
                            rec["status"] = message.get("status", 0)
                            if not is_call:
                                usage.record(opendart, rec["status"], tool, rec["latency"])
                        elif message["type"] == "http.response.body" and is_call and not rec["done"]:
                            chunk = message.get("body", b"") or b""
                            rec["bytes"] += len(chunk)     # 호출측이 무는 토큰 비용의 대리 지표
                            if chunk and (not rec["err"] or rec["ekind"] is None):
                                hay = rec["tail"] + chunk
                                if not rec["err"]:
                                    rec["err"] = any(p in hay for p in _ERR_PATTERNS)
                                if rec["ekind"] is None:
                                    m = _EKIND_RE.search(hay)
                                    if m:
                                        rec["ekind"] = m.group(1).decode()
                                rec["tail"] = hay[-64:]  # 태그(~16B)가 청크 경계에 안 잘리게
                            if not message.get("more_body", False):
                                rec["done"] = True
                                # 오류일 때만 error_kind 기록. 태그 없는 오류(인자검증·프로토콜·비래핑
                                # 경로)는 "untagged" sentinel → 배포前 NULL과 배포後 분류실패를 구분.
                                # 성공(not err)은 본문에 우연히 [ekind=]가 있어도 None으로 기록.
                                ekind = (rec["ekind"] or "untagged") if rec["err"] else None
                                # 장부를 읽는다 — 우리가 만들어 내려보낸 그 dict 다.
                                # 문서를 안 받은 요청은 셋 다 0이라 분모에서 자연히 빠진다.
                                usage.record(opendart, rec["status"], tool, rec["latency"],
                                             is_error=rec["err"], error_kind=ekind,
                                             response_bytes=rec["bytes"],
                                             doc_mem_hits=ledger["doc_mem_hits"],
                                             doc_disk_hits=ledger["doc_disk_hits"],
                                             doc_misses=ledger["doc_misses"],
                                             corp_codes=ledger["corp_codes"])
                        await send(message)
                    await self.app(scope, replay, send_wrapper)
                else:
                    await self.app(scope, receive, send)

        app = mcp.streamable_http_app()
        app.add_middleware(ApiKeyMiddleware)

        install_api_key_redaction()
        uvicorn.run(
            app,
            host=mcp.settings.host,
            port=mcp.settings.port,
        )
    else:
        mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
