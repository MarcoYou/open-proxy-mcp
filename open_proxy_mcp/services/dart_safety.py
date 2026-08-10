"""DART 조회 예외 안전망 (중앙집중).

부하(대량 스캔) 시 DART 클라이언트가 던지는 외부·전송·타임아웃 예외를, tool 래퍼
(`tools/_wrap_tool_errors`) 한 곳에서 graceful 응답으로 떨어뜨려 크래시
(FastMCP isError=true)를 방지한다. 260712 shareholder_meeting 사고(한 유저 140콜
버스트에서 크래시 스파이크) 근본수정 — 전수조사 결과 여러 tool이 같은 빈틈을 공유해,
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


# OpenDART 상태코드 → (kind, 사용자 안내). **공식 개발가이드 표 전체를 덮는다**
# (opendart.fss.or.kr/guide, 260810 대조). 종전에는 7개만 덮어 나머지가 전부
# "일시적으로 실패했습니다. 잠시 후 다시 시도하세요"로 떨어졌다 — 키가 만료된 사람이
# **영원히 재시도**하게 되는 안내다. 게다가 011·012 는 설명 자체가 틀렸다:
#   011 = 「사용할 수 없는 키」인데 「과호출 누적」이라고 안내했고,
#   012 = 「접근할 수 없는 IP」인데 「조회 빈도를 낮추라」고 안내했다.
# 둘 다 빈도와 무관한데 사용자에게 빈도를 줄이라고 시키고 있었다.
#
# 원인이 다르면 안내도 다르다 — '사용 방식을 바꿔라'는 과호출(020·021·429)에만 붙이고
# 지연·점검·결함엔 붙이지 않는다(안 몰았는데 "많이 했다"고 오탐 금지).
_DART_STATUS_GUIDE = {
    # ── 키 문제: 재시도해도 **절대** 안 된다. 사용자가 키를 고쳐야 한다 ──
    "010": ("bad_key",
            "DART API 키가 등록되지 않은 키입니다. opendart.fss.or.kr 에서 발급받은 "
            "키가 맞는지 확인하고 다시 연결하세요."),
    "011": ("bad_key",
            "이 DART API 키는 사용할 수 없는 상태입니다. opendart.fss.or.kr 에서 키 상태를 "
            "확인하거나 새로 발급받으세요."),
    "901": ("bad_key",
            "DART API 키가 개인정보 보유기간 만료로 정지됐습니다. opendart.fss.or.kr 에서 "
            "재발급받아 다시 연결하세요."),
    "012": ("blocked_ip",
            "이 서버의 IP 에서 DART 에 접근할 수 없습니다. 사용자가 조치할 수 있는 문제가 "
            "아니므로 운영자에게 알려 주세요."),
    # ── 호출 방식: 사용자가 조정하면 된다 ──
    "020": ("rate_limited",
            "DART 요청 한도를 넘었습니다(분당 제한). 여러 회사를 한꺼번에 조회 중이라면 "
            "한 번에 1~2곳씩, 잠깐 간격을 두고 나눠서 조회하세요."),
    "021": ("too_many_targets",
            "한 번에 조회 가능한 회사 수(최대 100)를 초과했습니다. 대상을 나눠서 요청하세요."),
    # ── 결과가 없는 것: 실패가 아니라 **답**이다 ──
    "013": ("no_data", "해당 조건으로 조회된 공시 데이터가 없습니다."),
    "014": ("no_document", "해당 공시의 원본 파일이 DART 에 없습니다."),
    "404": ("not_found", "해당 회사·문서를 찾지 못했습니다. 회사명/식별자를 확인하세요."),
    # ── 요청이 거부됨: 우리 쪽 문제일 가능성이 높다(사용자가 할 일이 없다) ──
    "100": ("bad_request", "DART 가 요청 값을 거부했습니다(부적절한 필드 값)."),
    "101": ("bad_request", "DART 가 요청을 거부했습니다(부적절한 접근)."),
    # ── 상류 상태 ──
    "800": ("maintenance", "DART 가 시스템 점검 중입니다. 잠시 후 다시 시도하세요."),
    "900": ("transient", "DART 에서 알 수 없는 오류가 발생했습니다. 잠시 후 다시 시도하세요."),
}

#: **「자료가 없다」는 실패가 아니라 답이다.** 실패로 세면 오류율이 부풀고 진짜 고장이
#: 그 안에 묻힌다. 이 셋만 성공 쪽에 두고 나머지는 전부 실패로 센다(degrade_marker).
_NOT_A_FAILURE = {"no_data", "no_document", "not_found"}

#: '잠깐 뒤 재시도'가 의미 있는 kind. **실패 여부와는 다른 축이다** —
#: `bad_key` 는 명백한 실패지만 재시도는 무의미하고, `no_data` 는 실패가 아니면서
#: 역시 재시도가 무의미하다. 260810 이전엔 이 하나로 둘 다 판정해서, 코드표를 넓히자
#: 「키가 틀렸다」가 조용히 성공으로 잡히는 문제가 생길 뻔했다.
_RETRYABLE = {"rate_limited", "too_many_targets", "maintenance",
              "timeout", "bad_document", "upstream_5xx", "transient"}


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


def degrade_marker(kind: str) -> str:
    """degrade 응답에 실을 **분류 표지**. 미들웨어가 본문 바이트에서 이것만 뽑아 통계에 적는다.

    260810: 이 표지가 없어서 **상류 실패가 통계에 전부 「성공」으로 잡히고 있었다.**
    degrade 는 설계상 정상 응답 모양(`# tool\n\n안내문`)이라 `isError` 가 없고, 그래서
    응답을 훑는 스캐너 입장에서 성공과 구분이 안 된다 — 306,670행 중 오류로 적힌 것이
    28건뿐이었던 이유다(진짜 오류가 28건인 게 아니라 DART 실패가 전부 성공으로 세어졌다).
    크래시 경로는 이미 `[ekind=...]` 를 싣고 있었다(tools/__init__). 같은 방식을 여기에도 준다.

    표지를 가르는 기준은 **`_NOT_A_FAILURE`** — 「이건 실패가 아니라 답인가」다.
    260810 초안은 `_RETRYABLE`(재시도가 의미 있나)로 갈랐는데, 코드표를 공식 표로 넓히자
    그게 깨졌다: `bad_key` 는 **명백한 실패인데 재시도는 무의미**해서, 그 기준으로는
    「키가 틀렸다」가 조용히 성공으로 잡힌다. 두 축은 다르다.
      · `[degraded=…]` 상류·부하 문제로 **답을 못 줬다** → 통계에서 실패로 센다
      · `[nodata=…]`  조회 결과가 없거나(013) 회사를 못 찾은 것(404) — **이건 답이다**.
                      실패로 세면 안 되지만, 얼마나 자주 나오는지는 알아야 하므로 표시는 남긴다
    """
    return f"[nodata={kind}]" if kind in _NOT_A_FAILURE else f"[degraded={kind}]"


def degrade_response(tool_name: str, fmt: str, exc: BaseException) -> str:
    """외부오류를 크래시 대신 정상 응답(문자열)으로. 원인별로 다른 안내를 실어
    사용자가 (필요할 때만) 호출 방식을 바꾸도록 유도한다. format="json"이면 최소 JSON,
    아니면 마크다운. 에러 메시지 원문은 싣지 않는다(개인정보 — 예외 클래스명만).

    안내문과 별개로 `degrade_marker()` 표지를 **두 포맷 모두**에 싣는다 — 안 실으면
    이 응답이 통계에서 성공으로 잡힌다(위 함수 주석)."""
    reason = type(exc).__name__
    kind, msg = classify_degrade(exc)
    marker = degrade_marker(kind)
    if (fmt or "md").lower() == "json":
        return json.dumps(
            {
                "tool": tool_name,
                "status": "error",
                "warnings": [msg],
                "data": {"error_class": reason, "error_kind": kind,
                         "retry": kind in _RETRYABLE, "marker": marker},
            },
            ensure_ascii=False,
            indent=2,
        )
    return f"# {tool_name}\n\n{msg}\n\n{marker}"
