"""DART 020 은 **키의 하루 한도**다 — 「분당 제한」으로 안내하지 않는다 (261019).

종전 안내: 「DART 요청 한도를 넘었습니다(분당 제한). … 한 번에 1~2곳씩, 잠깐 간격을 두고 나눠서 조회하세요」 · kind=rate_limited ·
retry=true. 개발가이드의 020 은 「요청 제한을 초과하였습니다. 일반적으로는 20,000건 이상의 요청에 대하여…」 — 하루치이고 자정(KST)에
풀린다. 간격을 두고 나눠 불러도 같은 답이라, 종전 안내를 따르면 같은 오류만 되풀이한다(2026-10-19 운영: 06:58 020 뒤 07:00 1콜도 020).
이 시험은 main(304c002f)에서 실패한다 — 고치기 전 실패를 확인했다.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from open_proxy_mcp.clock import KST
from open_proxy_mcp.dart.client import DartClientError
from open_proxy_mcp.services import dart_safety as S


def test_020_is_the_keys_daily_limit_not_a_per_minute_limit():
    kind, msg = S.classify_degrade(DartClientError("020", "요청 제한을 초과하였습니다."))
    assert kind == "daily_limit", kind
    assert "하루" in msg and "자정" in msg and "20,000" in msg, msg
    assert "분당" not in msg, f"020 을 분당 제한으로 안내한다: {msg}"
    assert "나눠서" not in msg and "간격" not in msg, f"간격을 두라는 안내는 020 에 맞지 않는다: {msg}"
    assert "KST)" in msg and "다음 초기화" in msg, msg


def test_daily_limit_is_not_marked_retryable_and_says_when():
    body = json.loads(S.degrade_response("dividend_data", "json", DartClientError("020", "x")))
    data = body["data"]
    assert body["status"] == "error"
    assert data["error_kind"] == "daily_limit" and data["retry"] is False, data
    assert data["marker"] == "[degraded=daily_limit]", data
    when = datetime.fromisoformat(data["retry_after"])
    assert when.utcoffset() == timedelta(hours=9) and (when.hour, when.minute, when.second) == (0, 0, 0), when
    now = datetime.now(KST)
    assert timedelta(0) < when - now <= timedelta(days=1), (now, when)


def test_markdown_carries_marker_and_reset_time():
    md = S.degrade_response("dividend_data", "md", DartClientError("020", "x"))
    assert md.endswith("[degraded=daily_limit]"), md
    assert "다음 초기화" in md and "KST" in md, md


@pytest.mark.parametrize("now,expect", [
    (datetime(2026, 10, 19, 8, 40, tzinfo=KST), datetime(2026, 10, 20, 0, 0, tzinfo=KST)),
    # UTC 로 아직 18일이어도 KST 로 19일이면 20일 자정 — 서버(fly)는 UTC 로 돈다
    (datetime(2026, 10, 18, 23, 30, tzinfo=timezone.utc), datetime(2026, 10, 20, 0, 0, tzinfo=KST)),
    (datetime(2026, 10, 19, 23, 59, 59, tzinfo=KST), datetime(2026, 10, 20, 0, 0, tzinfo=KST)),
    (datetime(2026, 10, 20, 0, 0, 0, tzinfo=KST), datetime(2026, 10, 21, 0, 0, tzinfo=KST)),
    (datetime(2026, 12, 31, 12, 0, tzinfo=KST), datetime(2027, 1, 1, 0, 0, tzinfo=KST)),
])
def test_next_kst_midnight(now, expect):
    assert S.next_kst_midnight(now) == expect


def test_http_429_stays_a_short_rate_limit():
    """웹·KIND 경로의 HTTP 429 는 잠깐 뒤 풀리는 쪽이다 — 하루 한도와 섞지 않는다."""
    req = httpx.Request("GET", "https://dart.fss.or.kr/x")
    exc = httpx.HTTPStatusError("429", request=req, response=httpx.Response(429, request=req))
    body = json.loads(S.degrade_response("company", "json", exc))
    assert body["data"]["error_kind"] == "rate_limited" and body["data"]["retry"] is True, body
    assert "retry_after" not in body["data"], body


def test_other_kinds_get_no_retry_after():
    for status in ("011", "013", "800", "021"):
        data = json.loads(S.degrade_response("company", "json", DartClientError(status, "x")))["data"]
        assert "retry_after" not in data, (status, data)


def test_tool_read_note_uses_the_daily_wording():
    """도구가 직접 「조회 실패 — 안내」로 적는 경로(ownership_structure)도 같은 안내를 쓴다(코드표 한 곳)."""
    from open_proxy_mcp.services.ownership_structure import _dart_read_note
    failed, text = _dart_read_note(DartClientError("020", "x"), empty_text="없음", fail_text="변동신고서 DART 검색 실패")
    assert failed is True and "하루 요청 한도" in text and "분당" not in text, text


def test_no_per_minute_wording_left_for_020():
    """코드표 밖에 020 을 분당으로 설명하는 주석·문구가 남지 않았나(같은 오해가 다시 번지지 않게)."""
    from pathlib import Path
    root = Path(S.__file__).resolve().parents[1]
    for rel in ("services/dart_safety.py", "dart/client.py"):
        src = (root / rel).read_text(encoding="utf-8")
        assert "020 분당" not in src and "(분당 제한)" not in src, rel
        assert "키당 4만" not in src, f"{rel}: 하루 한도는 키당 20,000 이다"
