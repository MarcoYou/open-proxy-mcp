"""v2 evidence facade 서비스."""

from __future__ import annotations

import re

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.contracts import AnalysisStatus, EvidenceRef, SourceType, ToolEnvelope


def _extract_rcept_no(evidence_id: str) -> str:
    match = re.search(r"(\d{14})", evidence_id or "")
    return match.group(1) if match else ""


def _build_snippet(text: str, keyword: str, width: int = 220) -> str:
    if not text:
        return ""
    clean = re.sub(r"\s+", " ", text).strip()
    if keyword:
        idx = clean.find(keyword)
        if idx >= 0:
            start = max(0, idx - width)
            end = min(len(clean), idx + len(keyword) + width)
            return clean[start:end]
    return clean[: min(len(clean), width * 5)]


async def build_evidence_payload(
    *,
    evidence_id: str = "",
    rcept_no: str = "",
    keyword: str = "",
) -> dict:
    target_rcept_no = rcept_no or _extract_rcept_no(evidence_id)
    if not target_rcept_no:
        return ToolEnvelope(
            tool="evidence",
            status=AnalysisStatus.REQUIRES_REVIEW,
            subject=evidence_id or "evidence",
            warnings=["rcept_no가 포함되지 않은 evidence_id는 아직 원문으로 직접 펼칠 수 없다. rcept_no를 함께 넣어야 한다."],
            data={"evidence_id": evidence_id, "rcept_no": "", "keyword": keyword},
        ).to_dict()

    client = get_dart_client()
    try:
        doc = await client.get_document_cached(target_rcept_no)
    except DartClientError as exc:
        return ToolEnvelope(
            tool="evidence",
            status=AnalysisStatus.ERROR,
            subject=target_rcept_no,
            warnings=[f"원문 조회 실패: {exc.status}"],
            data={"evidence_id": evidence_id, "rcept_no": target_rcept_no, "keyword": keyword},
        ).to_dict()

    snippet = _build_snippet(doc.get("text", ""), keyword)
    warnings: list[str] = []
    if keyword and keyword not in re.sub(r"\s+", " ", doc.get("text", "")):
        warnings.append(f"`{keyword}` 키워드를 원문에서 직접 찾지 못했다. 문서 앞부분 snippet을 반환한다.")

    return ToolEnvelope(
        tool="evidence",
        status=AnalysisStatus.EXACT,
        subject=target_rcept_no,
        warnings=warnings,
        data={
            "evidence_id": evidence_id or f"ev_manual_{target_rcept_no}",
            "rcept_no": target_rcept_no,
            "keyword": keyword,
            "snippet": snippet,
            "html_available": bool(doc.get("html")),
            "images": doc.get("images", []),
            "text_length": len(doc.get("text", "")),
        },
        evidence_refs=[
            EvidenceRef(
                evidence_id=evidence_id or f"ev_manual_{target_rcept_no}",
                source_type=SourceType.DART_XML,
                rcept_no=target_rcept_no,
                section="document_excerpt",
                snippet=snippet[:200],
                parser="keyword_snippet",
            )
        ],
    ).to_dict()
