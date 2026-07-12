"""v2 public facade tools."""

import asyncio
import functools
import importlib
import pkgutil

import httpx
from mcp.server.fastmcp.exceptions import ToolError

from open_proxy_mcp.dart.client import DartClientError


def classify_error(exc: BaseException) -> str:
    """tool 예외를 error_kind로 분류 (예외 타입이 살아있는 지점에서만 정확 — FastMCP가
    감싸면 str(e)만 남고 타입이 사라짐). 빈 메시지 타임아웃도 타입으로 잡힌다.
      timeout  = DART/KIND 응답 지연(시간초과)
      upstream = DART/KIND API 장애(연결·전송·HTTP 오류)
      crash    = 그 외 OPM 내부 코드 버그 (KeyError·ValueError 등)
    """
    if isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException)):
        return "timeout"
    if isinstance(exc, (DartClientError, httpx.TransportError, httpx.HTTPStatusError)):
        return "upstream"
    return "crash"


def _wrap_tool_errors(fn):
    """tool 코루틴을 감싸 예외를 분류해 `[ekind=xxx]` 태그를 메시지 앞에 실어 re-raise.
    FastMCP가 이 ToolError를 그대로 isError 본문에 실어 보내고(태그 보존), ASGI 미들웨어가
    그 태그만 뽑아 기록한다(에러 메시지 원문은 저장하지 않음 — 개인정보 안전).
    contextvar 역전파 대신 본문 태그를 쓰는 이유: MCP streamable 전송이 요청/응답을 별도
    태스크로 쪼개 contextvar가 미들웨어까지 안전하게 전파되지 않음."""

    @functools.wraps(fn)
    async def inner(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — 분류 후 그대로 re-raise
            raise ToolError(f"[ekind={classify_error(exc)}] {exc}") from exc

    return inner


def register_all_tools_v2(mcp):
    """tools_v2/ 하위 public tool 등록. 등록 시점에 mcp.tool 데코레이터를 감싸
    모든 tool을 _wrap_tool_errors로 통과시킨다(20개 tool 개별 수정 불필요)."""

    import open_proxy_mcp.tools_v2 as tools_pkg

    orig_tool = mcp.tool

    def wrapping_tool(*d_args, **d_kwargs):
        real_deco = orig_tool(*d_args, **d_kwargs)

        def deco(fn):
            return real_deco(_wrap_tool_errors(fn))

        return deco

    mcp.tool = wrapping_tool
    try:
        for _importer, modname, _ispkg in pkgutil.iter_modules(tools_pkg.__path__):
            if modname.startswith("_"):
                continue
            module = importlib.import_module(f"open_proxy_mcp.tools_v2.{modname}")
            if hasattr(module, "register_tools"):
                module.register_tools(mcp)
    finally:
        mcp.tool = orig_tool
