"""v2 evidence facade 서비스.

evidence tool은 순수 `인용 메타 가공기`다.
rcept_no 문자열만으로 즉시 유도 가능한 정보만 반환한다.

- 입력: rcept_no (또는 rcept_no가 포함된 evidence_id)
- 출력: rcept_dt (rcept_no 앞 8자리), source_type (rcept_no 9~10자리로 DART/KIND 구분), viewer_url

report_nm은 upstream data tool이 전달한 evidence_refs에 이미 포함되어 있어야 하며,
생 rcept_no만으로는 채우지 않는다 (DART list.json 광범위 검색은 정확도가 낮고 비용이 큼).
필요하면 viewer_url로 DART/KIND 뷰어에서 직접 확인.
"""

from __future__ import annotations

import re

from open_proxy_mcp.services.contracts import (
    AnalysisStatus,
    EvidenceRef,
    SourceType,
    ToolEnvelope,
    _build_viewer_url,
)


_RCEPT_NO_PATTERN = re.compile(r"(\d{14})")


def _extract_rcept_no(evidence_id: str) -> str:
    match = _RCEPT_NO_PATTERN.search(evidence_id or "")
    return match.group(1) if match else ""


def _rcept_dt_from_no(rcept_no: str) -> str:
    """rcept_no 앞 8자리에서 YYYY-MM-DD 추출."""

    if len(rcept_no) < 8 or not rcept_no[:8].isdigit():
        return ""
    return f"{rcept_no[:4]}-{rcept_no[4:6]}-{rcept_no[6:8]}"


def _source_type_from_rcept_no(rcept_no: str) -> SourceType:
    """rcept_no 9~10번째 자리로 DART(00) / KIND(80) 구분."""

    if len(rcept_no) >= 10 and rcept_no[8:10] == "80":
        return SourceType.KIND_HTML
    return SourceType.DART_XML


async def build_evidence_payload(
    *,
    evidence_id: str = "",
    rcept_no: str = "",
) -> dict:
    """evidence_id 또는 rcept_no로 공시 인용 정보 반환."""

    target_rcept_no = rcept_no or _extract_rcept_no(evidence_id)

    if not target_rcept_no or not re.fullmatch(r"\d{14}", target_rcept_no):
        return ToolEnvelope(
            tool="evidence",
            status=AnalysisStatus.REQUIRES_REVIEW,
            subject=evidence_id or rcept_no or "evidence",
            warnings=[
                "rcept_no는 14자리 숫자여야 한다. 올바른 rcept_no 또는 rcept_no가 포함된 evidence_id를 입력해야 한다.",
            ],
            data={
                "evidence_id": evidence_id,
                "rcept_no": target_rcept_no,
            },
        ).to_dict()

    rcept_dt = _rcept_dt_from_no(target_rcept_no)
    source_type = _source_type_from_rcept_no(target_rcept_no)
    viewer_url = _build_viewer_url(source_type, target_rcept_no)

    resolved_evidence_id = evidence_id or f"ev_manual_{target_rcept_no}"

    return ToolEnvelope(
        tool="evidence",
        status=AnalysisStatus.EXACT,
        subject=target_rcept_no,
        warnings=[],
        data={
            "evidence_id": resolved_evidence_id,
            "rcept_no": target_rcept_no,
            "rcept_dt": rcept_dt,
            "source_type": source_type.value,
            "viewer_url": viewer_url,
        },
        evidence_refs=[
            EvidenceRef(
                evidence_id=resolved_evidence_id,
                source_type=source_type,
                rcept_no=target_rcept_no,
                rcept_dt=rcept_dt,
                viewer_url=viewer_url,
                section="citation",
            )
        ],
        next_actions=[
            "공시명/본문 확인이 필요하면 viewer_url로 DART/KIND 뷰어에서 직접 열기",
        ],
    ).to_dict()
