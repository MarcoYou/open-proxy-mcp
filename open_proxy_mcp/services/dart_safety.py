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


# OpenDART 상태코드 → (kind, 사용자 안내). 원인이 다르면 안내도 다르다 —
# 특히 '사용 방식을 바꿔라'는 과호출(020·021·429)에만 붙이고, 지연·점검·결함엔 붙이지 않는다
# (안 몰았는데 "많이 했다"고 오탐 금지). 상태코드 의미는 OpenDART 공식 가이드 기준.
_DART_STATUS_GUIDE = {
    "020": ("rate_limited",
            "DART 요청 한도를 넘었습니다(분당 제한). 여러 회사를 한꺼번에 조회 중이라면 "
            "한 번에 1~2곳씩, 잠깐 간격을 두고 나눠서 조회하세요."),
    "021": ("too_many_targets",
            "한 번에 조회 가능한 회사 수를 초과했습니다. 대상을 나눠서 요청하세요."),
    "011": ("access_blocked",
            "DART 접근이 일시적으로 제한된 상태입니다(과호출 누적). 조회 빈도를 낮추고 잠시 뒤 다시 시도하세요."),
    "012": ("access_blocked",
            "DART 접근이 일시적으로 제한된 상태입니다. 조회 빈도를 낮추고 잠시 뒤 다시 시도하세요."),
    "800": ("maintenance",
            "DART가 시스템 점검 중입니다. 잠시 후 다시 시도하세요."),
    "013": ("no_data", "해당 조건으로 조회된 공시 데이터가 없습니다."),
    "404": ("not_found", "해당 회사·문서를 찾지 못했습니다. 회사명/식별자를 확인하세요."),
}
# '잠깐 뒤 재시도'가 의미 있는 kind (no_data·not_found은 재시도해도 소용없음)
_RETRYABLE = {"rate_limited", "too_many_targets", "access_blocked",
              "maintenance", "timeout", "bad_document", "upstream_5xx", "transient"}


def classify_degrade(exc: BaseException) -> tuple[str, str]:
    """degrade 대상 예외 → (kind, 사용자 안내 문구). 원인별로 행동 유도를 다르게 한다.
    이 결과는 MCP 응답으로 Claude가 읽어 사용자에게 전달·호출방식을 조정하는 데 쓰인다."""
    # 1) DART API 상태코드 (과호출 020·회사수 021·점검 800·접근제한 011/012 등) — 가장 정확
    status = getattr(exc, "status", None)
    if isinstance(status, str) and status in _DART_STATUS_GUIDE:
        return _DART_STATUS_GUIDE[status]
    # 2) HTTP 레벨 (웹 스크래핑·KIND 경로): 429 과호출 / 5xx 서버오류
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        code = exc.response.status_code
        if code == 429:
            return ("rate_limited",
                    "요청이 몰려 일시적으로 제한되었습니다. 한 번에 여러 회사를 조회 중이라면 "
                    "건수를 줄이고 잠깐 간격을 두세요.")
        if 500 <= code < 600:
            return ("upstream_5xx", "DART 서버가 일시적 오류(5xx)를 반환했습니다. 잠시 후 다시 시도하세요.")
    # 3) 타임아웃 — 사용자 요청 방식 문제가 아님(빈도 조절 안내 붙이지 않음)
    if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError, TimeoutError)):
        return ("timeout",
                "DART 응답이 지연되고 있습니다(요청 방식 문제가 아닙니다). 잠시 후 다시 시도하세요.")
    # 4) 불완전·잘린 문서
    if isinstance(exc, zipfile.BadZipFile):
        return ("bad_document",
                "DART가 불완전한 문서를 반환했습니다. 잠시 후 다시 시도하면 정상 조회되는 경우가 많습니다.")
    # 5) 그 외 전송오류·API 오류 — 일반 일시적 실패
    return ("transient", "DART 조회가 일시적으로 실패했습니다. 잠시 후 다시 시도하세요.")


def degrade_response(tool_name: str, fmt: str, exc: BaseException) -> str:
    """외부오류를 크래시 대신 정상 응답(문자열)으로. 원인별로 다른 안내를 실어
    사용자가 (필요할 때만) 호출 방식을 바꾸도록 유도한다. format="json"이면 최소 JSON,
    아니면 마크다운. 에러 메시지 원문은 싣지 않는다(개인정보 — 예외 클래스명만)."""
    reason = type(exc).__name__
    kind, msg = classify_degrade(exc)
    if (fmt or "md").lower() == "json":
        return json.dumps(
            {
                "tool": tool_name,
                "status": "error",
                "warnings": [msg],
                "data": {"error_class": reason, "error_kind": kind,
                         "retry": kind in _RETRYABLE},
            },
            ensure_ascii=False,
            indent=2,
        )
    return f"# {tool_name}\n\n{msg}"
