"""v2 public tool 공통 계약."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
from typing import Any


class AnalysisStatus(str, Enum):
    """분석 결과 상태."""

    EXACT = "exact"
    AMBIGUOUS = "ambiguous"
    PARTIAL = "partial"
    CONFLICT = "conflict"
    REQUIRES_REVIEW = "requires_review"
    ERROR = "error"


class SourceType(str, Enum):
    """소스 계층."""

    DART_API = "dart_api"
    DART_XML = "dart_xml"
    DART_HTML = "dart_html"
    KIND_HTML = "kind_html"
    NAVER = "naver"
    INTERNAL = "internal"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


_DART_VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
_KIND_VIEWER_URL = "https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno={rcept_no}"


def _build_viewer_url(source_type: SourceType | str, rcept_no: str) -> str:
    if not rcept_no:
        return ""
    source_value = getattr(source_type, "value", source_type)
    if source_value == SourceType.KIND_HTML.value:
        return _KIND_VIEWER_URL.format(rcept_no=rcept_no)
    if source_value in {SourceType.DART_XML.value, SourceType.DART_HTML.value, SourceType.DART_API.value}:
        return _DART_VIEWER_URL.format(rcept_no=rcept_no)
    return ""


@dataclass(slots=True)
class EvidenceRef:
    """핵심 필드 근거.

    애널리스트가 "어느 공시를 언제 참조했는지"를 즉시 확인할 수 있도록
    rcept_no + rcept_dt + report_nm + viewer_url 중심 스키마.
    """

    evidence_id: str
    source_type: SourceType | str
    rcept_no: str = ""
    rcept_dt: str = ""
    report_nm: str = ""
    viewer_url: str = ""
    section: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        viewer_url = self.viewer_url or _build_viewer_url(self.source_type, self.rcept_no)
        return {
            "evidence_id": self.evidence_id,
            "source_type": getattr(self.source_type, "value", self.source_type),
            "rcept_no": self.rcept_no,
            "rcept_dt": self.rcept_dt,
            "report_nm": self.report_nm,
            "viewer_url": viewer_url,
            "section": self.section,
            "note": self.note,
        }


@dataclass(slots=True)
class ToolEnvelope:
    """v2 public tool 공통 응답."""

    tool: str
    status: AnalysisStatus | str
    subject: str = ""
    generated_at: str = field(default_factory=_utc_now_iso)
    warnings: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    evidence_refs: list[EvidenceRef | dict[str, Any]] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        evidence = [
            item.to_dict() if isinstance(item, EvidenceRef) else item
            for item in self.evidence_refs
        ]
        return {
            "tool": self.tool,
            "status": getattr(self.status, "value", self.status),
            "subject": self.subject,
            "generated_at": self.generated_at,
            "warnings": self.warnings,
            "data": self.data,
            "evidence_refs": evidence,
            "next_actions": self.next_actions,
        }


def as_pretty_json(payload: dict[str, Any]) -> str:
    """UTF-8 friendly JSON 직렬화."""

    return json.dumps(payload, ensure_ascii=False, indent=2)

