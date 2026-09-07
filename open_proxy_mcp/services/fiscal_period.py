"""회사 결산월에 종속되지 않는 회계기간 메타데이터."""
from __future__ import annotations

from datetime import date, timedelta
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


def period_metadata(period: dict[str, str] | None, *, annual: bool = False,
                    fiscal_end_month: int | None = None) -> dict[str, Any]:
    """실적기간을 사업연도·분기·비교 기준으로 정규화한다.

    260907 정정: 종전엔 결산월을 **실적기간 시작월에서 추정**했다(`start.month - 1`). 그러면 모든 분기가 그 사업연도의
    1분기가 된다 — 삼성전자 2025.07~09 실적이 「2026 사업연도 1분기 · 6월 결산」으로 나갔다. 결산월은 회사 정보(DART
    `acc_mt`)에서 받고, 모르면 12월(상장사 대다수)로 두며, 추정하지 않는다. 사업연도는 프로젝트 관례대로 **끝나는 해**
    (`fiscal_year_from_end`), 분기는 **종료월** 기준(`fiscal_quarter_from_end`). 100일을 넘는 기간은 누적(반기·3분기 누적).
    """
    if not period:
        return {}
    try:
        start = date.fromisoformat(period["start"])
        end = date.fromisoformat(period["end"])
    except (KeyError, TypeError, ValueError):
        return {}
    duration_days = (end - start).days + 1
    # 45일 이하면 월간(현대차 월별 판매실적처럼 한 달짜리 공시) — 분기 라벨을 붙이면 4월 실적이 「2분기」로 읽힌다(260907)
    kind = "annual" if annual or duration_days >= 300 else ("month" if duration_days <= 45 else "quarter")
    if fiscal_end_month and 1 <= fiscal_end_month <= 12:
        fem, src = fiscal_end_month, "company"
    elif kind == "annual":
        fem, src = end.month, "period"        # 연간 기간의 끝 달은 곧 결산월 — 이건 추정이 아니라 정의다
    else:
        fem, src = 12, "default"              # 분기 기간에서는 결산월을 알 수 없다 — 추정하지 않는다
    return {
        "fiscal_year": fiscal_year_from_end(end.isoformat(), fem),
        "fiscal_year_end_month": fem,
        "fiscal_year_end_month_source": src,
        "period_kind": kind,
        "fiscal_quarter": fiscal_quarter_from_end(end.isoformat(), fem) if kind in ("quarter", "month") else None,
        "period_month": end.month if kind == "month" else None,
        "cumulative": kind == "quarter" and duration_days > 100,
        "comparison_basis": {"annual": "직전사업연도 대비", "month": "전년동월 대비"}.get(kind, "전년동기 대비"),
    }


def fiscal_year_span(
    fiscal_year: int | None, fiscal_end_month: int | None
) -> tuple[str, str] | None:
    """사업연도 번호 + 결산월 → 그 사업연도가 실제로 덮는 12개월(시작·종료 ISO).

    「사업연도 2025」는 결산월에 따라 전혀 다른 12개월이다 — 12월 결산이면
    2025-01-01~2025-12-31, 6월 결산이면 2024-07-01~2025-06-30. 라벨만 보고
    1년을 통째로 오독하는 자리라 라벨 옆에 이 구간을 붙인다(260828 U 지적 B-5).
    """
    if not fiscal_year or not fiscal_end_month or not 1 <= fiscal_end_month <= 12:
        return None
    end_year = fiscal_year
    # 결산월 말일 = 다음 달 1일 - 1일 (윤년·30/31일 분기 없이 안전)
    if fiscal_end_month == 12:
        end = date(end_year, 12, 31)
    else:
        end = date(end_year, fiscal_end_month + 1, 1) - timedelta(days=1)
    start_month = fiscal_end_month % 12 + 1
    start_year = end_year if fiscal_end_month == 12 else end_year - 1
    start = date(start_year, start_month, 1)
    return start.isoformat(), end.isoformat()


def fiscal_period_label(fiscal_year: int | None, fiscal_end_month: int | None) -> str | None:
    """`2024-07-01~2025-06-30 · 6월 결산` / 12월 결산이면 `2025-01-01~2025-12-31`.

    `provisional_earnings` 의 실적기간 표기와 형태를 맞춘다.
    """
    span = fiscal_year_span(fiscal_year, fiscal_end_month)
    if not span:
        return None
    start, end = span
    if fiscal_end_month == 12:
        return f"{start}~{end}"
    return f"{start}~{end} · {fiscal_end_month}월 결산"


_QUARTER_TEXT = re.compile(r"(20\d{2})\s*년\s*(?:제?\s*)?(?:(1|2|3|4)\s*분기|(상반기|반기|하반기|1분기 누적|3분기 누적))")


def period_from_quarter_text(text: str, fiscal_end_month: int | None = None) -> dict[str, str] | None:
    """「2025년 2분기」·「2025년 반기」 같은 문구에서 실적기간을 만든다 — 원문에 날짜 범위가 없는 잠정실적(260907).

    2025.07 삼성전자·LG전자 공시는 실적기간 표기가 없고 정정사유·행사명에 「2025년 2분기」만 있다. 결산월(기본 12월)로
    그 사업연도의 12개월을 잡고(`fiscal_year_span`) 분기 순번대로 3개월을 자른다. 반기·상반기는 1~2분기 누적,
    하반기는 3~4분기. 문구가 없거나 결산월을 모르는 채 사업연도 해석이 갈리는 자리면 None — 추정하지 않는다.
    """
    if not text:
        return None
    m = _QUARTER_TEXT.search(text)
    if not m:
        return None
    fy = int(m.group(1))
    fem = fiscal_end_month if fiscal_end_month and 1 <= fiscal_end_month <= 12 else 12
    span = fiscal_year_span(fy, fem)
    if not span:
        return None
    fy_start = date.fromisoformat(span[0])
    word = m.group(3)
    if m.group(2):
        q_from = q_to = int(m.group(2))
    elif word in ("상반기", "반기"):
        q_from, q_to = 1, 2
    elif word == "하반기":
        q_from, q_to = 3, 4
    elif word == "1분기 누적":
        q_from, q_to = 1, 1
    else:  # 3분기 누적
        q_from, q_to = 1, 3

    def _add_months(d: date, n: int) -> date:
        y, mo = divmod(d.month - 1 + n, 12)
        return date(d.year + y, mo + 1, 1)

    start = _add_months(fy_start, 3 * (q_from - 1))
    end = _add_months(fy_start, 3 * q_to) - timedelta(days=1)
    return {"start": start.isoformat(), "end": end.isoformat(), "source": "quarter_text"}

