"""v2 public tool 날짜 파라미터 유틸리티."""

from __future__ import annotations

from datetime import date, timedelta
import re


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
    end = parse_date_param(end_date) or default_end or date.today()
    start = parse_date_param(start_date)

    if start is None:
        days = lookback_days if lookback_days is not None else max(30, lookback_months * 30)
        start = end - timedelta(days=days)

    if start > end:
        start, end = end, start
        warnings.append("start_date가 end_date보다 뒤라 자동으로 순서를 바꿨다.")

    return start, end, warnings
