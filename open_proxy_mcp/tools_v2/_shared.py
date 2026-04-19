"""tools_v2 공통 렌더링 헬퍼."""

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
