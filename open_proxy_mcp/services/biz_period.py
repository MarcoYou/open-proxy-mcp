"""business_details 매출 분해 표의 기간 표기 — 분기 단독 / 누계 / 연간 / 기간 미상.

왜: 같은 반기보고서 안에서 부문표(III 주석 K-IFRS 1108)는 「3개월」 열을, 제품별 표(II-2-가)는 「반기 누계」를
읽는데 응답에는 기간 표기가 없어, 읽는 쪽이 두 표를 같은 기간으로 맞춰 보거나(삼성전자 2026 반기: 부문 영업이익
합 89.49조는 2분기 단독, 제품별 매출 305조는 반기 누계) 부문 영업이익을 잠정실적 분기값과 견주는 근거가 없었다
(DATA-Q1 누적 대 3개월 대 잔액, DATA-Q6 기간이 다른 값끼리 비교하지 않는다).

기간을 읽는 차례(앞이 이긴다):
  1. 원문 표 머리 — 열 머리의 「3개월」「누적/누계」「N개월」(부문표), 표 머리줄의 같은 말(제품별 표)
  2. 원문 문장 — 분기보고서 제품별 구간 문장의 「N분기」
  3. 보고서 종류 — 사업보고서 = 연간, 반기보고서 = 반기 누계 (표에 기간 표기가 없을 때만, 근거를 밝힌다)
  4. 읽지 못하면 「기간 미상」 — 지어내지 않는다.
"""
from __future__ import annotations

import re

_CUM_RE = re.compile(r"누\s*[적계]")
_MONTHS_RE = re.compile(r"(\d{1,2})\s*개월")
_QUARTER_RE = re.compile(r"([1-4])\s*분기")
UNKNOWN = "기간 미상"


def canon_col_period(text: str | None) -> str | None:
    """열 머리 글자 → 「3개월」「6개월」「누계」 같은 짧은 표기. 기간 말이 없으면 None."""
    t = (text or "").strip()
    if not t:
        return None
    if _CUM_RE.search(t):
        return "누계"
    m = _MONTHS_RE.search(t)
    if m:
        return f"{int(m.group(1))}개월"
    return None


def label_of(col: str | None) -> str:
    """짧은 열 표기 → 읽는 사람용 기간 이름."""
    if not col:
        return UNKNOWN
    if col == "누계":
        return "누계"
    m = _MONTHS_RE.fullmatch(col)
    if m:
        n = int(m.group(1))
        if n == 3:
            return "분기 단독(3개월)"
        if n == 12:
            return "연간(12개월)"
        return f"누계({n}개월)"
    return col


def report_kind(report_nm: str | None) -> str | None:
    nm = report_nm or ""
    if "사업보고서" in nm:
        return "annual"
    if "반기보고서" in nm:
        return "half"
    if "분기보고서" in nm:
        return "quarter"
    return None


def _node(label: str, basis: str, report_nm: str | None, **extra) -> dict:
    out = {"label": label, "basis": basis}
    if report_nm:
        out["report"] = report_nm
    out.update({k: v for k, v in extra.items() if v})
    return out


def from_report(report_nm: str | None) -> tuple[str, str] | None:
    """보고서 종류만으로 정해지는 기간. 분기보고서는 1분기·3분기를 가를 수 없어 None."""
    k = report_kind(report_nm)
    if k == "annual":
        return "연간", "보고서 종류(원문 표에 기간 표기 없음)"
    if k == "half":
        return "반기 누계(6개월)", "보고서 종류(원문 표에 기간 표기 없음)"
    return None


def segment_period(period_label: str | None, period_columns: list[str] | None,
                   report_nm: str | None) -> dict:
    """부문표(by_segment)의 기간. period_label = 격자가 읽은 열의 머리, period_columns = 표에 있는 기간 열 전체."""
    cols = [c for c in (period_columns or []) if c]
    if period_label:
        extra: dict = {}
        if len(set(cols)) > 1:
            extra["columns_in_source"] = list(dict.fromkeys(cols))
            others = [c for c in dict.fromkeys(cols) if c != period_label]
            extra["note"] = (f"원문 표에는 {', '.join(dict.fromkeys(cols))} 열이 함께 있고 이 표의 값은 "
                             f"{period_label} 열입니다. {', '.join(others)} 열의 값은 이 표에 없습니다.")
        return _node(label_of(period_label), "원문 표 머리", report_nm, **extra)
    fr = from_report(report_nm)
    if fr:
        return _node(fr[0], fr[1], report_nm)
    return _node(UNKNOWN, "미상", report_nm,
                 note="원문 표 머리에서 기간을 읽지 못했습니다. 분기보고서는 1분기 3개월 또는 3분기 누계일 수 있어 "
                      "원문 표 머리를 확인하세요." if report_kind(report_nm) == "quarter" else
                      "원문 표 머리에서 기간을 읽지 못했습니다. 원문 표 머리를 확인하세요.")


def _head_rows(md: str) -> list[str]:
    """제품별 구간 마크다운에서 표 머리줄들 — 구분행(`|---|`) 바로 위 줄. 데이터 행은 들어오지 않는다."""
    out: list[str] = []
    lines = [ln.strip() for ln in md.splitlines()]
    for i, ln in enumerate(lines[:-1]):
        nxt = lines[i + 1]
        if ln.startswith("|") and nxt.startswith("|") and re.fullmatch(r"\|[\s:|\-]+", nxt):
            out.append(ln)
    return out


def read_mix_period(md: str | None, report_nm: str | None) -> dict:
    """제품별 표(II-2-가)의 기간. 표 머리줄 → 분기보고서 문장의 N분기 → 보고서 종류 → 미상."""
    text = md or ""
    heads = " ".join(_head_rows(text))
    col = canon_col_period(heads)
    if col:
        return _node(label_of(col), "원문 표 머리", report_nm)
    k = report_kind(report_nm)
    if k == "quarter":
        m = _QUARTER_RE.search(text)
        if m:
            q = int(m.group(1))
            return _node(f"{q}분기", "원문 문장", report_nm,
                         note="문장에 적힌 분기입니다. 누계인지 단독인지는 원문 표 머리에 없어 확인하세요.")
        return _node(UNKNOWN, "미상", report_nm,
                     note="분기보고서의 이 표는 1분기 3개월 또는 3분기 누계일 수 있고 원문에서 읽지 못했습니다.")
    fr = from_report(report_nm)
    if fr:
        return _node(fr[0], fr[1], report_nm)
    return _node(UNKNOWN, "미상", report_nm, note="보고서 종류와 표 머리 어디에서도 기간을 읽지 못했습니다.")


def attach_segment_period(segment: dict | None, sp, report_nm: str | None) -> None:
    """부문표 응답(OK 정형)에 `period` 를 붙인다. 정형이 아니면(원문 표 후보) 건드리지 않는다."""
    if not isinstance(segment, dict) or segment.get("status") != "OK":
        return
    segment["period"] = segment_period(getattr(sp, "period_label", "") or None,
                                       getattr(sp, "period_columns", None), report_nm)


def attach_mix_period(node: dict | None, report_nm: str | None) -> None:
    """제품별 표 응답(원문 마크다운이 있는 것)에 `period` 를 붙인다."""
    if not isinstance(node, dict) or not node.get("markdown"):
        return
    node["period"] = read_mix_period(node.get("markdown"), report_nm)
