"""OpenProxy MCP 서버 — MCPServer 진입점"""

import argparse
import logging
import os
import re
from mcp.server.mcpserver import MCPServer
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


def _opm_version() -> str:
    """**설치된 배포판**의 버전(pyproject 파일이 아니라 메타데이터). 못 읽으면 빈 문자열."""
    try:
        from importlib.metadata import version
        return version("open-proxy-mcp")
    except Exception:
        return ""


def build_mcp() -> MCPServer:
    """Build the single supported MCP tool surface."""
    # 이 이름이 클라이언트 커넥터 목록에 뜨고, MCP 양식(prompt)의 슬래시 명령
    # `/mcp__<서버이름>__<양식이름>` 가운데 자리에도 들어간다 — 짧을수록 부르기 쉽다.
    # fly 앱 이름(=URL `open-proxy-mcp.fly.dev`)과 레포명은 그대로 둔다.
    mcp = MCPServer(
        "openproxy",
        # 2.0 은 SDK 버전을 자동으로 안 채운다(기본값 ""). 빈 값보다는 **OPM 자신의 버전**이
        # 유용하다 — 클라이언트가 「어느 OPM 이 답했나」를 알 수 있다. 종전 1.x 는 여기에
        # SDK 버전(1.26.0 등)을 넣었는데, 그건 우리 릴리스와 무관한 값이었다.
        version=_opm_version(),
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


# ── 서빙 설정과 미들웨어 ──────────────────────────────────────────────────────
# 이 아래는 전부 `main()` 안에 있었다. 그래서 **테스트가 프로덕션이 실제로 서빙하는 객체에
# 닿을 수 없었다** — 260729 2차 사고(모듈 레벨 인스턴스에 라우트를 붙였는데 서빙되는 건
# `main()` 이 만든 다른 인스턴스라 /health 가 404)와 같은 결함이다. 밖으로 꺼내
# `build_app()` 하나가 서빙 결정을 전부 들게 하고, `main()` 은 그걸 uvicorn 에 넘기기만 한다.


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


#: 응답 본문에서 「이 호출이 실패했나」를 읽는 패턴. **테스트는 이 상수를 import 해서
#: 실제 wire 바이트와 대조한다** — 테스트가 리터럴을 복사해 가지면, 서버가 눈이 먼 뒤에도
#: 테스트는 영원히 통과한다(매칭이 0건인 것과 「오류가 없었다」는 구분되지 않는다).
_ERR_PATTERNS = (b'"isError":true', b'"isError": true',
                 b'"error":{"code"', b'"error": {"code"')
#: **값이 아니라 필드가 보였나**를 따로 본다. 패턴이 하나도 안 맞는 것은 두 가지 뜻이다 —
#: 「오류가 없었다」와 「우리가 못 읽었다」. 종전에는 둘 다 is_error=False 로 적혀서,
#: 필드명이 바뀌면 **에러율이 영원히 0** 이 되고 아무 신호도 안 났다(260810 실측: 2.0 의
#: 파이썬 필드가 is_error 로 바뀐 걸 보고 wire 도 바뀐 줄 알고 스캐너를 고칠 뻔했다).
#: 이제 셋째 상태를 남긴다 — is_error=None + error_kind="unclassifiable".
#: 그 수가 늘면 에러율이 0으로 수렴하는 대신 「모르겠다」가 쌓여 눈에 띈다.
_ERR_FIELD = (b'"isError"', b'"is_error"', b'"error"')
_EKIND_RE = re.compile(rb"\[ekind=(\w+)\]")  # tool 래퍼가 붙인 error_kind 태그
#: **degrade 표지** — 상류(DART) 실패를 크래시 대신 정상 응답으로 낮춰 보낸 것(dart_safety).
#: 그 응답은 설계상 `# tool\n\n안내문` 이라 `isError` 가 없어, 이 표지가 없으면 스캐너가
#: **성공과 구분하지 못한다.** 260810 실측: 306,670행 중 오류로 적힌 것이 28건뿐이었는데
#: 진짜 오류가 28건이어서가 아니라 DART 실패가 전부 성공으로 세어졌기 때문이었다.
#: 오늘 넣은 3상태(unclassifiable)로도 못 잡힌다 — 스캐너가 눈이 먼 게 아니라 응답이
#: **진짜로 성공 모양**이라서다. 구멍이 스캐너보다 위에 있었다.
_DEGRADED_RE = re.compile(rb"\[degraded=(\w+)\]")   # 답을 못 줬다 → 실패로 센다
_NODATA_RE = re.compile(rb"\[nodata=(\w+)\]")       # 「자료 없음」은 답이다 → 성공, 다만 표시

#: 요청 본문에서 **도구 이름을 꺼낼 만큼만** 읽는다. JSON-RPC 는 method·params.name 이 앞에
#: 오므로 정상 요청은 수백 바이트면 충분하다(OPM 인자는 회사명·코드·연도다).
#: 종전에는 끝까지 다 모았고, 미들웨어가 라우터 **밖**에 있어 SDK 의 4 MiB 상한보다 먼저
#: 돌기 때문에 **32 MiB 를 통째로 메모리에 담은 뒤에야 413** 이 났다(실측, 1.29·2.0 동일).
#: 1 GB VM 에 OOM 이력(260804)이 있어 상한 없는 누적은 그대로 둘 수 없다.
#: 여기서 멈춰도 replay 가 나머지를 receive() 로 흘려보내므로 하류는 온전한 본문을 받는다.
_MAX_SNIFF_BYTES = 64 * 1024


class ApiKeyMiddleware:
    """URL 쿼리 파라미터 ?opendart=키 → contextvar 세팅 + 사용 통계 기록."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        from open_proxy_mcp import usage
        from open_proxy_mcp.dart.client import set_request_api_key

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from urllib.parse import parse_qs
        qs = parse_qs(scope.get("query_string", b"").decode())
        # 공백만 든 키는 **없는 것으로 친다.** 파이썬에서 " " 는 참이라 종전에는 게이트를
        # 통과했고(실측 `?opendart=%20` → 200), 하류의 `키 or os.getenv(...)` 폴백도 참이라
        # 서버 키로 넘어가지도 않은 채 **공백이 그대로 DART 키로 쓰였다**. 사용자는 401 힌트
        # 대신 원인 모를 상류 인증 실패를 받고, 통계엔 유령 사용자 해시가 잡히며,
        # 키로 캐싱되는 클라이언트 인스턴스가 하나씩 늘어난다.
        opendart = (qs.get("opendart", [None])[0] or "").strip() or None
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
                    if len(body) >= _MAX_SNIFF_BYTES:
                        break     # 나머지는 replay 가 receive() 로 그대로 흘려보낸다
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
                   "done": False, "ekind": None, "bytes": 0, "field_seen": False,
                   "degraded": None, "nodata": None}

            async def send_wrapper(message):
                if message["type"] == "http.response.start":
                    rec["latency"] = int((_t.monotonic() - start) * 1000)
                    rec["status"] = message.get("status", 0)
                    if not is_call:
                        usage.record(opendart, rec["status"], tool, rec["latency"])
                elif message["type"] == "http.response.body" and is_call and not rec["done"]:
                    chunk = message.get("body", b"") or b""
                    rec["bytes"] += len(chunk)     # 호출측이 무는 토큰 비용의 대리 지표
                    if chunk and (not rec["err"] or rec["ekind"] is None or not rec["field_seen"]):
                        hay = rec["tail"] + chunk
                        if not rec["err"]:
                            rec["err"] = any(p in hay for p in _ERR_PATTERNS)
                        if not rec["field_seen"]:
                            # 값이 아니라 **필드가 있었나**. 없으면 우리가 못 읽은 것이다.
                            rec["field_seen"] = any(p in hay for p in _ERR_FIELD)
                        if rec["ekind"] is None:
                            m = _EKIND_RE.search(hay)
                            if m:
                                rec["ekind"] = m.group(1).decode()
                        if rec["degraded"] is None:
                            m = _DEGRADED_RE.search(hay)
                            if m:
                                rec["degraded"] = m.group(1).decode()
                        if rec["nodata"] is None:
                            m = _NODATA_RE.search(hay)
                            if m:
                                rec["nodata"] = m.group(1).decode()
                        rec["tail"] = hay[-64:]  # 태그(~16B)가 청크 경계에 안 잘리게
                    if not message.get("more_body", False):
                        rec["done"] = True
                        # 세 상태로 적는다 — 「실패」·「성공」·**「모르겠다」**.
                        #   실패    err=True                → is_error=True. 태그 없는 오류
                        #           (인자검증·프로토콜·비래핑)는 "untagged" sentinel
                        #   성공    필드는 봤는데 값이 false → is_error=False
                        #   모르겠다 필드 자체를 못 봤다      → is_error=None + "unclassifiable"
                        # 셋째가 핵심이다. 종전엔 이것도 False 로 적혀서 **스캐너가 눈이 멀면
                        # 에러율이 조용히 0** 이 됐다. 이제 「모르겠다」가 쌓여 눈에 띈다
                        # (nullable 이라 WHERE is_error=true 집계의 분모에서도 빠진다).
                        #   상류실패 degrade 표지 → is_error=True + `dart_` 접두
                        #           (우리 크래시와 구분한다 — 대응이 다르다). 접두가 필요한 건
                        #           이름이 겹치기 때문이다 — `timeout` 은 우리 크래시 분류에도
                        #           degrade 분류에도 있다. 줄임말(`up_`)을 안 쓰는 이유는
                        #           **리포트에서 이 값을 읽는 사람이 물어보지 않아야** 해서다.
                        #   자료없음 nodata 표지        → is_error=False + kind 만 남김
                        if rec["degraded"]:
                            is_err, ekind = True, f"dart_{rec['degraded']}"
                        elif rec["err"]:
                            is_err, ekind = True, (rec["ekind"] or "untagged")
                        elif rec["nodata"]:
                            is_err, ekind = False, rec["nodata"]
                        elif rec["field_seen"]:
                            is_err, ekind = False, None
                        else:
                            is_err, ekind = None, "unclassifiable"
                        # 장부를 읽는다 — 우리가 만들어 내려보낸 그 dict 다.
                        # 문서를 안 받은 요청은 셋 다 0이라 분모에서 자연히 빠진다.
                        usage.record(opendart, rec["status"], tool, rec["latency"],
                                     is_error=is_err, error_kind=ekind,
                                     response_bytes=rec["bytes"],
                                     doc_mem_hits=ledger["doc_mem_hits"],
                                     doc_disk_hits=ledger["doc_disk_hits"],
                                     doc_misses=ledger["doc_misses"],
                                     corp_codes=ledger["corp_codes"],
                                     fetch_viewer=ledger["fetch_viewer"],
                                     fetch_kind=ledger["fetch_kind"],
                                     web_wait_ms=ledger["web_wait_ms"])
                await send(message)
            await self.app(scope, replay, send_wrapper)
        else:
            await self.app(scope, receive, send)


def allowed_hosts() -> list[str]:
    """DNS 리바인딩 방어의 허용 목록. `FASTMCP_ALLOWED_HOSTS` 로 덧붙일 수 있다
    (로컬에서 8000 이 아닌 포트로 띄울 때 필요)."""
    hosts = [
        "open-proxy-mcp.fly.dev",
        "localhost:8000",
        "127.0.0.1:8000",
        "0.0.0.0:8000",
    ]
    extra = os.environ.get("FASTMCP_ALLOWED_HOSTS", "").strip()
    if extra:
        hosts.extend([h.strip() for h in extra.split(",") if h.strip()])
    return hosts


def bind_host() -> str:
    return os.environ.get("FASTMCP_HOST", "0.0.0.0")


def bind_port() -> int:
    return int(os.environ.get("FASTMCP_PORT", "8000"))


def transport_security() -> TransportSecuritySettings:
    """호스트 보호를 **명시적으로** 만든다.

    mcp 2.0 은 이 값을 안 넘기면 host 가 localhost 계열일 때만 보호를 켠다
    (`lowlevel/server.py`). OPM 의 bind host 는 0.0.0.0 이라 그 조건에 안 걸려
    **보호가 조용히 꺼진다** — 사용자는 멀쩡히 쓰고 방어만 사라지며 아무 에러도 안 난다.
    그래서 기본값에 맡기지 않고 항상 만들어 넘긴다.
    """
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts(),
    )


def build_app(server=None):
    """**프로덕션이 실제로 서빙하는 ASGI 앱.** 테스트는 이 함수를 부른다.

    무상태 HTTP: 각 요청이 독립(세션 in-memory 미보관) → fly 다중 머신에서 라우팅이
    갈려도 "Session not found" 없음. OPM tool은 무상태(요청마다 키·파라미터 자급)라
    세션 유지 불필요. 2머신 유지하면서 세션 어피니티 문제 해결. (2026-06)
    """
    server = server or build_mcp()
    # 2.0 에서 이 **넷**은 `settings` 를 떠나 여기 인자가 됐고, **기본값이 전부 우리와 반대**다
    # (다섯 번째인 port 는 앱이 아니라 uvicorn.run()/run() 이 받는다).
    # 대입은 다섯 다 ValueError 로 시끄럽게 터지므로 안전하다 — 위험한 건 **인자를 빠뜨리는 것**
    # 이고, 그건 다섯 다 조용하다. 그중 transport_security 만 결과가 보안이다:
    # 없으면 보호가 꺼진 채로 정상 서빙된다(사용자도 지표도 눈치 못 챈다).
    app = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=transport_security(),
        host=bind_host(),
        # 기본값과 같은 값이지만 **명시한다** — 위 넷에 적용한 「SDK 기본값을 믿지 않는다」가
        # 여기에도 그대로 걸린다. 1.29 도 같은 4 MiB 였으므로 이관에 따른 변화는 없다(실측).
        # 주의: 이 상한은 우리를 못 지킨다 — ApiKeyMiddleware 가 라우터 **밖**에 있어
        # 본문을 통째로 버퍼링한 뒤에야 413 이 난다(32 MiB 로 실측, 1.29·2.0 동일).
        # 그 구멍은 이 이관과 무관한 기존 구조이고 따로 다뤄야 한다.
        max_request_body_size=4 * 1024 * 1024,
    )
    app.add_middleware(ApiKeyMiddleware)
    return app


def main():
    parser = argparse.ArgumentParser()
    # 전송 방식은 하나뿐이다. 종전에는 stdio·sse 도 받았고 **기본값이 stdio** 였다 —
    # 금지된 것이 기본값이라, 인자를 빼먹으면 조용히 그리로 떴다.
    #   stdio : 세션이 뜰 때 그 시점 코드를 메모리에 붙들어, 고쳐도 옛 결과를 낸다(260802).
    #   sse   : 연결을 붙들어 fly 2머신에서 "Session not found" 가 난다. streamable-http 가
    #           그 문제를 풀려고 나온 후속 방식이다. 게다가 SDK 가 자기 앱을 따로 만들어
    #           우리 ApiKeyMiddleware(키 게이트·통계·로그 마스킹)가 안 붙었다.
    # 인자 자체는 남긴다 — Dockerfile·launch.json 이 명시해서 넘긴다.
    parser.add_argument(
        "--transport",
        choices=["streamable-http"],
        default="streamable-http",
    )
    args = parser.parse_args()
    server = build_mcp()

    import uvicorn
    app = build_app(server)              # 서빙 결정은 전부 build_app 안에 있다
    install_api_key_redaction()
    uvicorn.run(app, host=bind_host(), port=bind_port())


if __name__ == "__main__":
    main()
