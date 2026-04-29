"""v2 public tool 공통 계약."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
from typing import Any


class AnalysisStatus(str, Enum):
    """분석 결과 상태.

    - EXACT: 사건 발견 + 모든 필드 정상 파싱
    - NO_FILING: 조회 구간에 사건 자체가 없는 정상 케이스 (PARTIAL과 분리)
    - PARTIAL: 사건은 발견됐으나 일부 필드 파싱 실패 (진짜 부분 실패)
    - AMBIGUOUS: 회사 식별 등 입력 모호
    - CONFLICT: 둘 이상의 소스 결과가 충돌
    - REQUIRES_REVIEW: 자동 판정 불가, 사람 검토 필요
    - ERROR: 호출 실패 / 데이터 자체 미존재
    """

    EXACT = "exact"
    AMBIGUOUS = "ambiguous"
    PARTIAL = "partial"
    NO_FILING = "no_filing"
    CONFLICT = "conflict"
    REQUIRES_REVIEW = "requires_review"
    ERROR = "error"


def build_filing_meta(
    *,
    filing_count: int,
    parsed_count: int | None = None,
    parsing_failures: int = 0,
) -> dict[str, Any]:
    """11 data tool 공통 filing 메타.

    - no_filing: 조사 구간 사건 0건 (정상)
    - filing_count: 발견된 공시/이벤트 수
    - parsed_count: 정상 파싱된 수 (None이면 filing_count - parsing_failures)
    - parsing_failures: 진짜 partial failure (필드 누락 등)
    - filing_status: "no_filing" | "all_parsed" | "partial_failure"
    """

    if parsed_count is None:
        parsed_count = max(filing_count - parsing_failures, 0)

    if filing_count <= 0:
        filing_status = "no_filing"
    elif parsing_failures > 0:
        filing_status = "partial_failure"
    else:
        filing_status = "all_parsed"

    return {
        "no_filing": filing_count <= 0,
        "filing_count": int(filing_count),
        "parsed_count": int(parsed_count),
        "parsing_failures": int(parsing_failures),
        "filing_status": filing_status,
    }


def status_from_filing_meta(meta: dict[str, Any]) -> "AnalysisStatus":
    """filing 메타에서 표준 status 도출 (data tool 공통)."""

    if meta.get("no_filing"):
        return AnalysisStatus.NO_FILING
    if int(meta.get("parsing_failures", 0)) > 0:
        return AnalysisStatus.PARTIAL
    return AnalysisStatus.EXACT


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


def build_usage(api_calls: int) -> dict[str, int]:
    """모든 v2 data tool이 data.usage로 노출하는 공통 블록."""
    return {
        "dart_api_calls": api_calls,
        "mcp_tool_calls": 1,
        "dart_daily_limit_per_minute": 1000,
    }


_DART_VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"


def _build_viewer_url(source_type: SourceType | str, rcept_no: str) -> str:
    """viewer_url은 DART만 사용.

    KIND 전용 원문 URL(disclsviewer.do?acptno=...)은 직접 접근 시 404가 나오기 때문에
    KIND_HTML 출처여도 DART 뷰어 URL을 반환한다. DART 뷰어는 rcept_no가 80(거래소
    수시공시) 포맷이어도 정상 동작한다.
    """
    if not rcept_no:
        return ""
    source_value = getattr(source_type, "value", source_type)
    if source_value in {
        SourceType.KIND_HTML.value,
        SourceType.DART_XML.value,
        SourceType.DART_HTML.value,
        SourceType.DART_API.value,
    }:
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

