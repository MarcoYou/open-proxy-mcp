"""DART 조회 예외 안전망 (중앙집중).

부하(대량 스캔) 시 DART 클라이언트가 던지는 외부·전송·타임아웃 예외를, tool 래퍼
(`tools_v2/_wrap_tool_errors`) 한 곳에서 graceful 응답으로 떨어뜨려 크래시
(FastMCP isError=true)를 방지한다. 260712 shareholder_meeting 사고(한 유저 140콜
버스트에서 크래시 스파이크) 근본수정 — 전수조사 결과 15개 tool이 같은 빈틈을 공유해,
tool마다 고치지 않고 모든 tool이 통과하는 래퍼에서 일괄 처리한다.

degrade 대상(외부·부하 원인 — "일시적 오류, 재시도" 로 안내):
  - DartClientError        : OpenDART API-level 오류
  - httpx.HTTPError         : 타임아웃(ReadTimeout)·429/5xx(HTTPStatusError)·전송오류
                              (ReadError·ConnectError·RemoteProtocolError) 전부의 base
  - asyncio.TimeoutError / TimeoutError : 내부 wait_for 시간초과(3.11+ 동일 alias)
  - zipfile.BadZipFile      : 잘린/비정상 문서 본문을 Zip으로 열 때

**코드버그(KeyError·ValueError·IndexError 등)는 이 집합에 없다** — 래퍼가 그대로 크래시로
노출해 error_kind=crash로 측정되게 한다(외부오류와 코드버그를 섞지 않음).
"""
from __future__ import annotations

import asyncio
import json
import zipfile

import httpx

from open_proxy_mcp.dart.client import DartClientError

# 래퍼가 graceful degrade할 외부 예외 집합.
DART_EXTERNAL_ERRORS = (
    DartClientError,
    httpx.HTTPError,        # TimeoutException·TransportError·HTTPStatusError 전부의 base
    asyncio.TimeoutError,
    TimeoutError,
    zipfile.BadZipFile,
)


def degrade_response(tool_name: str, fmt: str, exc: BaseException) -> str:
    """외부오류를 크래시 대신 정상 응답(문자열)으로. format="json"이면 최소 JSON,
    아니면 마크다운. 에러 메시지 원문은 싣지 않는다(개인정보 — 예외 클래스명만)."""
    reason = type(exc).__name__
    msg = f"DART 조회가 일시적으로 실패했습니다({reason}). 잠시 후 다시 시도해 주세요."
    if (fmt or "md").lower() == "json":
        return json.dumps(
            {
                "tool": tool_name,
                "status": "error",
                "warnings": [msg],
                "data": {"error_class": reason, "transient": True},
            },
            ensure_ascii=False,
            indent=2,
        )
    return f"# {tool_name}\n\n{msg}"
