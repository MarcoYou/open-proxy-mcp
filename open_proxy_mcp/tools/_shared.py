"""Public tool rendering helpers."""

from __future__ import annotations

from typing import Any


def _format_evidence_line(ref: dict[str, Any]) -> str:
    """evidence_refs 리스트의 단일 항목을 markdown bullet으로 렌더링.

    형식: `- [공시일] 공시명 (섹션) — note · rcept_no · 뷰어 URL`
    """

    rcept_dt = ref.get("rcept_dt", "")
    report_nm = ref.get("report_nm", "")
    section = ref.get("section", "")
    note = ref.get("note", "")
    rcept_no = ref.get("rcept_no", "")
    viewer_url = ref.get("viewer_url", "")

    head_parts: list[str] = []
    if rcept_dt:
        head_parts.append(f"[{rcept_dt}]")
    if report_nm:
        head_parts.append(report_nm)
    elif section:
        head_parts.append(section)
    head = " ".join(head_parts) if head_parts else (section or rcept_no or "-")

    if report_nm and section:
        head = f"{head} ({section})"

    tail_parts: list[str] = []
    if note:
        tail_parts.append(note)
    if rcept_no:
        tail_parts.append(f"`{rcept_no}`")
    if viewer_url:
        tail_parts.append(viewer_url)
    tail = " · ".join(tail_parts)

    return f"- {head} — {tail}" if tail else f"- {head}"

def company_id_line(data: dict) -> str | None:
    """내부 회사 ID(`cmp_005930`)를 사람이 쓰는 종목코드로. 값이 없으면 줄을 내지 않는다.

    260728: 15개 도구 라이브 스캔에서 `- company_id: ``cmp_005930``` 가 10개 도구에 걸쳐
    나왔다. 사용자에게 `cmp_` 접두는 아무 뜻도 없고, 필요한 건 종목코드다.
    """
    cid = str(data.get("company_id") or "")
    code = data.get("stock_code") or (cid.split("_", 1)[1] if "_" in cid else "")
    return f"- 종목코드 {code}" if code else None


def krw_scaled(v: Any) -> str:
    """원 단위 금액을 값 크기에 맞는 단위로 찍는다 — **조 고정은 소형주를 지운다.**

    260828 T 재검토 지적: 관리종목 사유가 「시총 200억원 미만」인 종목에서 시총이
    `0.0조` 로 나왔다. 쟁점이 200억원인데 조 단위 반올림은 숫자를 없앤 것과 같다.
    JSON(`*_krw`)에는 원 단위 원본이 그대로 남는다 — 바뀌는 것은 md 표기뿐이다.
    """
    if v is None:
        return "-"
    a = abs(v)
    if a >= 1e13:          # 10조 이상 — 소수 1자리로 충분
        return f"{v/1e12:,.1f}조원"
    if a >= 1e12:          # 1조~10조 — 1자리면 500억이 반올림에 묻힌다
        return f"{v/1e12:,.2f}조원"
    if a >= 1e8:           # 1억~1조 — 억원 (소형주·거래대금 구간)
        return f"{v/1e8:,.0f}억원"
    return f"{v:,.0f}원"
