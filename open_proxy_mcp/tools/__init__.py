"""Public MCP tool facades."""

import asyncio
import contextlib
import functools
import importlib
import json
import pkgutil

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from open_proxy_mcp.dart.client import DartClientError
from open_proxy_mcp.services import notices as _notices
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


#: 종류주 코드를 받아 **그 종류주 자체의 값**을 주는 도구 — 회사 단위 고지(「발행 회사로 식별했습니다」)를 붙이면 틀린 말이 된다.
#: 이 도구는 자기 안내(「이 응답의 종가·시총·상장주식수는 이 종류주 자체의 값」)를 따로 싣는다(`services/trading._quoted_ticker`).
_OWN_SHARE_CLASS_NOTE = frozenset({"trading_data"})


def _declare_resolution_notes(tool_name: str, result):
    """해석 확정 관문이 요청 장부에 적은 고지 — 이름 **추정**(비활성 회사로 확정한 경우 포함)과 종류주(우선주 등) 코드를 발행 회사로
    읽었다는 것 — 가 응답에 빠졌으면 맨 앞에 붙인다. 도구 경계의 안전망(261018).

    고지는 해석 확정 관문이 요청 장부에 적고 `ToolEnvelope.to_dict()`·`declare_weak_resolution` 이 읽는데, 그 둘을 거치지 않고
    dict·md 를 직접 만드는 경로(`forward_estimates_data` · `dividend_data` · `price_multiple_data` 의 firm_history·과거 시점 firm·sector ·
    `trading_data` 의 universe)는 「005935」를 삼성전자 값으로 **고지 없이** 줬다(종전엔 그 코드가 `not_found` 였다). 추정도 같았다 —
    `dividend_data(company="현대미포")` 는 상장폐지된 에이치디현대미포의 배당 표를 「추정했다」·「비활성 회사」 한 줄 없이 냈다
    (다른 도구는 맨 앞에 밝힌다 — [[company]] 「이름이 정확히 맞지 않으면 응답이 그 사실을 밝힌다」). 모든 도구가 이 경계를 지나므로
    여기서 한 번 더 본다. 이미 실려 있으면(대부분의 도구) 응답을 건드리지 않는다 — 추정·종류주 코드가 없는 요청은 장부가 비어 아무것도 하지 않는다.
    종류주 고지만 `_OWN_SHARE_CLASS_NOTE` 도구에서 뺀다(그 종류주 자체의 값을 주므로). 추정 고지는 모든 도구에 같다."""
    if not isinstance(result, str):
        return result
    from open_proxy_mcp.services.contracts import _share_class_warnings, _weak_resolution_warnings, as_pretty_json

    # 순서는 `ToolEnvelope`(`_merge_declared`)와 같다 — 추정 먼저, 종류주 다음.
    lines = _weak_resolution_warnings()
    if tool_name not in _OWN_SHARE_CLASS_NOTE:
        lines += _share_class_warnings()
    if not lines:
        return result
    if result.lstrip().startswith("{"):
        # JSON 응답 — 문자열 안의 따옴표가 이스케이프되므로 직렬화된 모양으로 찾는다.
        missing = [ln for ln in lines if json.dumps(ln, ensure_ascii=False)[1:-1] not in result]
        if not missing:
            return result
        try:
            obj = json.loads(result)
        except ValueError:
            obj = None                                   # JSON 이 아니다 — 아래 md 경로
        else:
            if isinstance(obj, dict) and isinstance(obj.get("warnings", []), list):
                obj["warnings"] = missing + list(obj.get("warnings") or [])
                return as_pretty_json(obj)
            return result                                # 붙일 자리가 없는 JSON — 깨뜨리지 않는다
    missing = [ln for ln in lines if ln not in result]
    if not missing:
        return result
    block = "\n".join(f"> ⚠ {ln}" for ln in missing)
    head, _sep, rest = result.partition("\n")
    if head.startswith("#"):
        return f"{head}\n\n{block}\n{rest}" if rest else f"{head}\n\n{block}"
    return f"{block}\n\n{result}"


def _attach_notices(tool_name: str, result):
    """운영자 공지(`services/notices`)를 응답에 끼운다 — md 는 `# 제목` 바로 아래 `> 📢` 블록, JSON 은 최상위 dict 의 `notices` 칸(`warnings` 와 따로).

    공지가 없으면(`OPM_NOTICE_URL` 없음·아직 못 받음·만료·시작 전) 응답을 **그대로** 돌려준다. 받기는 배경에서 하고 여기서 기다리지 않는다.
    붙일 자리가 없는 JSON(dict 아님)은 깨뜨리지 않는다. 공지 쪽 어떤 오류도 도구 응답을 바꾸지 않는다."""
    try:
        return _notices.attach(tool_name, result)
    except Exception:  # noqa: BLE001 — 공지는 덤이다. 도구 응답을 코드버그로 만들지 않는다.
        return result


def _wrap_tool_errors(fn):
    """모든 tool 코루틴을 감싸는 중앙 안전망:
      ① DART 외부·부하 예외(DART_EXTERNAL_ERRORS) → 크래시 대신 graceful 응답(is_error=false).
         전수조사에서 공통 빈틈으로 확인돼 tool마다가 아니라 이 한 곳에서 처리.
         resolve_company_query 콜드경로·공용 search 헬퍼 등 미처 못 가드한 경로까지 전부 커버.
      ② 그 외 예외(진짜 코드버그) → `[ekind=xxx]` 태그를 붙여 re-raise → FastMCP isError=true.
         (태그를 본문에 싣는 이유: MCP streamable 전송이 요청/응답을 별도 태스크로 쪼개
         contextvar 역전파가 불안정 → 미들웨어가 본문에서 태그만 뽑아 기록, 원문 미저장.)
      ③ 이름 추정·종류주 코드 고지가 빠진 응답이면 맨 앞에 붙인다(`_declare_resolution_notes`).
      ④ 유효한 운영자 공지가 있으면 응답에 끼운다(`_attach_notices`) — 정상 응답과 ①의 graceful 응답에만. ②의 오류 본문은 바꾸지 않는다.
    """

    @functools.wraps(fn)
    async def inner(*args, **kwargs):
        try:
            name = getattr(fn, "__name__", "")
            return _attach_notices(name, _declare_resolution_notes(name, await fn(*args, **kwargs)))
        except DART_EXTERNAL_ERRORS as exc:  # 외부·부하 → graceful degrade
            name = getattr(fn, "__name__", "tool")
            return _attach_notices(name, degrade_response(name, kwargs.get("format", "md"), exc))
        except Exception as exc:  # noqa: BLE001 — 코드버그는 분류 후 그대로 re-raise
            raise ToolError(f"[ekind={classify_error(exc)}] {exc}") from exc

    inner.attaches_notices = True      # 시험이 「등록된 도구 전부가 이 경계를 지난다」를 확인하는 표지
    return inner


@contextlib.contextmanager
def tool_error_boundary(mcp):
    """`mcp.tool` 로 등록되는 도구를 공통 경계(`_wrap_tool_errors` — 예외 안전망·공지)로 감싼다. 확장 패키지가 거는 도구도 같은 경계를 지나게 쓴다."""
    orig_tool = mcp.tool

    def wrapping_tool(*d_args, **d_kwargs):
        real_deco = orig_tool(*d_args, **d_kwargs)

        def deco(fn):
            return real_deco(_wrap_tool_errors(fn))

        return deco

    mcp.tool = wrapping_tool
    try:
        yield
    finally:
        mcp.tool = orig_tool


def register_all_tools(mcp):
    """Register public tools with the shared exception boundary."""

    import open_proxy_mcp.tools as tools_pkg

    with tool_error_boundary(mcp):
        for _importer, modname, _ispkg in pkgutil.iter_modules(tools_pkg.__path__):
            if modname.startswith("_"):
                continue
            module = importlib.import_module(f"open_proxy_mcp.tools.{modname}")
            if hasattr(module, "register_tools"):
                module.register_tools(mcp)
