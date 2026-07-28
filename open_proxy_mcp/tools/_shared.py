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
