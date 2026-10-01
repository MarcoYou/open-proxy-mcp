"""public tool 날짜 파라미터 유틸리티.

「오늘」은 `today_kst`(open_proxy_mcp.clock) — DART 접수일은 한국 달력이고 서버는 UTC 다.
"""

from __future__ import annotations
from open_proxy_mcp.clock import today_kst

from datetime import date, timedelta
import re


_DATE_KEY_RE = re.compile(r"_date$")
_KO_DATE_RE = re.compile(r"^(\d{4})\s*[년.\-/]\s*(\d{1,2})\s*[월.\-/]\s*(\d{1,2})\s*일?$")


def normalize_row_dates(row: dict) -> None:
    """*_date 필드를 제자리에서 ISO로 바꾸고, 모르는 형식은 원문 그대로 둔다.

    중첩 dict만 순회한다. 날짜 유효성 검증이나 list 순회는 하지 않는다.
    """
    for k, v in row.items():
        if isinstance(v, dict):
            normalize_row_dates(v)
            continue
        if not isinstance(v, str) or not v or not _DATE_KEY_RE.search(k):
            continue
        s = v.strip()
        m = _KO_DATE_RE.match(s)
        if m:
            row[k] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        elif re.fullmatch(r"\d{8}", s):
            row[k] = f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def parse_date_param(value: str) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None

    digits = re.sub(r"[^\d]", "", raw)
    if len(digits) != 8:
        return None

    try:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None


def format_yyyymmdd(value: date) -> str:
    return value.strftime("%Y%m%d")


def format_iso_date(value: str) -> str:
    """YYYYMMDD 또는 YYYY.MM.DD 등 혼합 포맷을 YYYY-MM-DD로 정규화."""

    if not value:
        return ""
    digits = re.sub(r"[^\d]", "", value)
    if len(digits) < 8:
        return ""
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"


def resolve_date_window(
    *,
    start_date: str = "",
    end_date: str = "",
    default_end: date | None = None,
    lookback_months: int = 12,
    lookback_days: int | None = None,
) -> tuple[date, date, list[str]]:
    warnings: list[str] = []
    end = parse_date_param(end_date) or default_end or today_kst()
    start = parse_date_param(start_date)

    if start is None:
        days = lookback_days if lookback_days is not None else max(30, lookback_months * 30)
        start = end - timedelta(days=days)

    if start > end:
        start, end = end, start
        warnings.append("start_date가 end_date보다 뒤라 자동으로 순서를 바꿨다.")

    return start, end, warnings
