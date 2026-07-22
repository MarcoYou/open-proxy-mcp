"""Public MCP tool facades."""

import asyncio
import functools
import importlib
import pkgutil

import httpx
from mcp.server.fastmcp.exceptions import ToolError

from open_proxy_mcp.dart.client import DartClientError
from open_proxy_mcp.services.dart_safety import DART_EXTERNAL_ERRORS, degrade_response


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
    """모든 tool 코루틴을 감싸는 중앙 안전망:
      ① DART 외부·부하 예외(DART_EXTERNAL_ERRORS) → 크래시 대신 graceful 응답(is_error=false).
         전수조사에서 공통 빈틈으로 확인돼 tool마다가 아니라 이 한 곳에서 처리.
         resolve_company_query 콜드경로·공용 search 헬퍼 등 미처 못 가드한 경로까지 전부 커버.
      ② 그 외 예외(진짜 코드버그) → `[ekind=xxx]` 태그를 붙여 re-raise → FastMCP isError=true.
         (태그를 본문에 싣는 이유: MCP streamable 전송이 요청/응답을 별도 태스크로 쪼개
         contextvar 역전파가 불안정 → 미들웨어가 본문에서 태그만 뽑아 기록, 원문 미저장.)
    """

    @functools.wraps(fn)
    async def inner(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except DART_EXTERNAL_ERRORS as exc:  # 외부·부하 → graceful degrade
            return degrade_response(getattr(fn, "__name__", "tool"),
                                    kwargs.get("format", "md"), exc)
        except Exception as exc:  # noqa: BLE001 — 코드버그는 분류 후 그대로 re-raise
            raise ToolError(f"[ekind={classify_error(exc)}] {exc}") from exc

    return inner


def register_all_tools(mcp):
    """Register public tools with the shared exception boundary."""

    import open_proxy_mcp.tools as tools_pkg

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
            module = importlib.import_module(f"open_proxy_mcp.tools.{modname}")
            if hasattr(module, "register_tools"):
                module.register_tools(mcp)
    finally:
        mcp.tool = orig_tool
