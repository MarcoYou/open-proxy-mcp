"""회사 결산월에 종속되지 않는 회계기간 메타데이터."""
from __future__ import annotations

from datetime import date
import re
from typing import Any


def normalize_period_end(period_end: str | None) -> str | None:
    """DART의 `2025-06-30 현재`/`2025.06.30`을 ISO 날짜로 정규화한다."""
    if not period_end:
        return None
    match = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", str(period_end))
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
    except ValueError:
        return None


def fiscal_year_from_end(period_end: str | None, fiscal_end_month: int | None = None) -> int | None:
    """실적기간 종료일 기준 사업연도. 결산월을 모르면 종료연도를 반환한다."""
    if not period_end:
        return None
    normalized = normalize_period_end(period_end)
    if not normalized:
        return None
    end = date.fromisoformat(normalized)
    # 분기 종료가 결산월 뒤에 있으면 다음 해에 끝나는 사업연도다.
    if fiscal_end_month and end.month > fiscal_end_month:
        return end.year + 1
    return end.year


def fiscal_quarter_from_end(period_end: str | None, fiscal_end_month: int | None = None) -> int | None:
    """기간 종료일과 결산월로 사업연도 분기(1~4)를 계산한다."""
    if not period_end or not fiscal_end_month or not 1 <= fiscal_end_month <= 12:
        return None
    normalized = normalize_period_end(period_end)
    if not normalized:
        return None
    end = date.fromisoformat(normalized)
    offset_months = (end.month - fiscal_end_month - 1) % 12
    return (offset_months // 3) + 1


def period_metadata(period: dict[str, str] | None, *, annual: bool = False) -> dict[str, Any]:
    """실적기간을 사업연도·분기·비교 기준으로 정규화한다."""
    if not period:
        return {}
    try:
        start = date.fromisoformat(period["start"])
        end = date.fromisoformat(period["end"])
    except (KeyError, TypeError, ValueError):
        return {}
    fiscal_end_month = end.month if annual else (12 if start.month == 1 else start.month - 1)
    duration_days = (end - start).days + 1
    kind = "annual" if annual or duration_days >= 300 else "quarter"
    offset = (start.month - fiscal_end_month - 1) % 12
    fiscal_year = end.year if annual or end.month <= fiscal_end_month else end.year + 1
    return {
        "fiscal_year": fiscal_year,
        "fiscal_year_end_month": fiscal_end_month,
        "period_kind": kind,
        "fiscal_quarter": (offset // 3) + 1 if kind == "quarter" else None,
        "comparison_basis": "직전사업연도 대비" if kind == "annual" else "전년동기 대비",
    }
