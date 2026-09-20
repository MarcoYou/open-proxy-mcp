"""후보표의 임기 한 칸을 보수적으로 분리한다. 재직·선임 결과를 추정하지 않는다."""
from __future__ import annotations

import calendar
import datetime as dt
import re


_DATE = r"[0-9]{4}(?:\s*년(?:\s*[0-9]{1,2}\s*월(?:\s*[0-9]{1,2}\s*일)?)?|(?:[./-][0-9]{1,2}){1,2}\.?)?"
_RANGE = re.compile(rf"(?P<start>{_DATE})\s*(?:[~～〜–—-]|부터)\s*(?P<end>{_DATE})\s*(?:까지)?")
_DURATION = re.compile(r"(?:(?P<years>[0-9]{1,3})\s*년\s*)?(?:(?P<months>[0-9]{1,3})\s*개월)?")


def _date(value: str) -> tuple[str, str, dt.date, dt.date]:
    """표기 정밀도는 그대로 두고 유효성·역순 검사에만 날짜 경계를 쓴다."""
    parts = [int(x) for x in re.findall(r"[0-9]+", value)]
    year = parts[0]
    month = parts[1] if len(parts) > 1 else 1
    day = parts[2] if len(parts) > 2 else 1
    lower = dt.date(year, month, day)
    precision = ("year", "month", "day")[len(parts) - 1]
    upper = lower if precision == "day" else dt.date(
        year, month if precision == "month" else 12,
        calendar.monthrange(year, month)[1] if precision == "month" else 31,
    )
    normalized = f"{year:04d}"
    if len(parts) > 1:
        normalized += f"-{month:02d}"
    if len(parts) > 2:
        normalized += f"-{day:02d}"
    return normalized, precision, lower, upper


def parse_appointment_term(raw: str | None, *, unit: str | None = None) -> dict:
    """임기 원문·개월 수·명시 날짜·미해결 조건. 날짜를 임의로 더하거나 채우지 않는다.

    unit은 후보표 헤더가 명시한 '년'·'개월'만 전달한다. 숫자만 적힌 셀의 단위를
    임의로 가정하지 않는다. 부분 판독은 원문 검토 표시를 유지한다.
    """
    if raw is not None and not isinstance(raw, str):
        raise TypeError("임기 원문은 문자열이어야 합니다")
    if unit not in (None, "년", "개월"):
        raise ValueError("임기 단위를 확인하세요")
    result = {
        "raw": raw, "duration_months": None, "start": None, "end": None,
        "start_precision": None, "end_precision": None, "end_condition": None,
        "parse_status": "missing", "requires_review": True, "warnings": [],
    }
    text = re.sub(r"\s+", " ", raw or "").strip()
    if text in ("", "-", "—", "미정", "미상", "해당없음", "해당사항 없음"):
        result["warnings"] = ["임기가 없거나 확정되지 않았습니다."]
        return result
    text = re.sub(r"^임기\s*[:：]?\s*", "", text)
    if unit and re.fullmatch(r"[0-9]{1,3}", text):
        text += unit

    match = _RANGE.fullmatch(text)
    if match:
        try:
            start, sp, start_min, _ = _date(match["start"])
            end, ep, _, end_max = _date(match["end"])
            if end_max < start_min:
                raise ValueError("역순")
        except ValueError:
            result.update(parse_status="invalid", warnings=["날짜가 존재하지 않거나 종료가 시작보다 빠릅니다."])
            return result
        result.update(start=start, end=end, start_precision=sp, end_precision=ep,
                      parse_status="parsed", requires_review=False)
        return result

    match = _DURATION.match(text)
    if match and match.end() and (match["years"] or match["months"]):
        # 숫자가 문장 안에 한 번 등장했다는 이유로 나머지 조건을 버리지 않는다.
        tail = text[match.end():].strip()
        months = int(match["years"] or 0) * 12 + int(match["months"] or 0)
        if months <= 0:
            result.update(parse_status="invalid", warnings=["임기 기간이 양수가 아닙니다."])
            return result
        if tail and re.search(r"[0-9]+\s*(?:년|개월)", tail):
            result.update(parse_status="unparsed", warnings=["복수 기간이나 선택 조건을 하나의 임기로 확정하지 않았습니다."])
            return result
        if tail and not (tail.startswith(("(", "[", "주", "※")) or "주주총회" in tail or "주총" in tail):
            result.update(parse_status="unparsed", warnings=["기간 뒤의 조건을 확정하지 못했습니다."])
            return result
        result.update(duration_months=months, parse_status="parsed", requires_review=False)
        if tail:
            result.update(parse_status="partial", requires_review=True,
                          end_condition=tail if "주총" in tail or "주주총회" in tail else None,
                          warnings=["조건·각주를 포함한 임기입니다. 원문을 확인하세요."])
        return result

    # 정기주총 종결·잔여 임기 등은 날짜가 아니라 종료 조건이다.
    if any(word in text for word in ("주주총회", "주총", "잔여", "잔임")):
        result.update(end_condition=text, parse_status="partial",
                      warnings=["조건부 종료를 달력상의 만료일로 환산하지 않았습니다."])
    else:
        result.update(parse_status="unparsed", warnings=["임기 표현을 확정하지 못했습니다. 원문을 확인하세요."])
    return result
