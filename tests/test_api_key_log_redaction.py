"""사용자 DART 키가 액세스 로그에 평문으로 남지 않아야 한다.

키는 쿼리스트링(`?opendart=`)으로 들어오고 uvicorn 액세스 로그는 요청 라인을 통째로
찍는다. 260806 실측에서 배포 로그에 유저 키가 그대로 남는 것을 확인했다.
"""

from __future__ import annotations

import logging

from open_proxy_mcp.server import RedactApiKey, install_api_key_redaction

_SECRET = "ZZSECRETKEY0123456789"


def _emit(record_args) -> str:
    logger = logging.getLogger(f"test.redact.{id(record_args)}")
    logger.setLevel(logging.INFO)
    captured: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record.getMessage())

    logger.addHandler(_Capture())
    logger.addFilter(RedactApiKey())
    msg, args = record_args
    logger.info(msg, *args)
    return captured[0]


def test_key_in_the_message_is_masked() -> None:
    out = _emit((f'GET /mcp?opendart={_SECRET} HTTP/1.1', ()))
    assert _SECRET not in out
    assert "opendart=***" in out


def test_key_in_the_args_is_masked() -> None:
    """uvicorn 액세스 로그는 경로를 %s 인자로 넘긴다 — 메시지만 봐서는 못 잡는다."""
    out = _emit(('%s - "%s %s HTTP/1.1" %d', ("1.2.3.4", "POST", f"/mcp?opendart={_SECRET}&x=1", 200)))
    assert _SECRET not in out
    assert "opendart=***" in out


def test_dart_upstream_key_name_is_masked_too() -> None:
    out = _emit((f"https://opendart.fss.or.kr/api/list.json?crtfc_key={_SECRET}&x=1", ()))
    assert _SECRET not in out
    assert "crtfc_key=***" in out


def test_a_key_prefix_does_not_survive() -> None:
    """prefix 도 남기지 않는다 — CLAUDE.md 키 비노출 규칙."""
    out = _emit((f"GET /mcp?opendart={_SECRET}", ()))
    assert _SECRET[:8] not in out


def test_unrelated_logs_are_untouched() -> None:
    out = _emit(('%s - "%s %s HTTP/1.1" %d', ("1.2.3.4", "GET", "/health", 200)))
    assert out == '1.2.3.4 - "GET /health HTTP/1.1" 200'


def test_installing_twice_does_not_stack_filters() -> None:
    install_api_key_redaction()
    install_api_key_redaction()
    access = logging.getLogger("uvicorn.access")
    assert sum(isinstance(f, RedactApiKey) for f in access.filters) == 1
