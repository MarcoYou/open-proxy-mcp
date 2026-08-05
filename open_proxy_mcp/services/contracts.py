"""public tool 공통 계약."""

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
    """모든 data tool이 data.usage로 노출하는 공통 블록."""
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


def declare_weak_resolution(payload: dict[str, Any]) -> dict[str, Any]:
    """`ToolEnvelope` 를 쓰지 않고 dict 를 직접 만드는 payload 에 같은 문구를 붙인다.

    서비스 안에 return 이 스무 곳 넘게 흩어져 있어도 **공개 진입 함수 하나만** 감싸면
    전부 덮인다 — 새 return 이 늘어도 전파가 끊기지 않는다.
    """
    if not isinstance(payload, dict):
        return payload
    payload["warnings"] = _merge_declared(list(payload.get("warnings") or []))
    return payload


def _merge_declared(existing: list[str]) -> list[str]:
    """추정 문구를 맨 앞에 두되, 이미 실려 있으면 다시 싣지 않는다.

    tool 이 안쪽 tool 의 응답을 감싸면(`director_board` 는 `[notice] ` 를 붙여 옮긴다)
    같은 문장이 두 번 나온다 — 접두어가 붙은 사본도 같은 것으로 본다.
    """
    declared = _weak_resolution_warnings()
    if not declared:
        return existing
    kept = [w for w in existing if not any(w.endswith(line) for line in declared)]
    return declared + kept


def _weak_resolution_warnings() -> list[str]:
    """이름이 정확히 맞지 않아 추정으로 고른 기업을 응답 맨 앞에 밝힌다.

    해석기는 `confidence` 를 이미 만들지만 `company` tool 만 그것을 보여 주고 나머지
    전부가 버리고 있었다. 「지에스」가 「지에스이」로 조용히 바뀌어도 사용자는 알 수 없었다.
    적는 곳은 해석 확정 관문 하나, 읽는 곳은 여기 하나다.
    """
    try:
        from open_proxy_mcp.dart.client import weak_resolutions
    except Exception:  # pragma: no cover - import 경로가 없는 환경
        return []
    lines: list[str] = []
    for weak in weak_resolutions():
        others = weak.get("candidates", 1) - 1
        tail = f" (다른 후보 {others}곳)" if others > 0 else ""
        line = (
            f"「{weak['query']}」를 **{weak['corp_name']}**(으)로 추정했습니다 — 이름이 정확히 "
            f"일치하지 않습니다{tail}. 다른 회사를 뜻했다면 종목코드나 정식명으로 다시 물어보세요."
        )
        # 한 요청이 같은 회사를 여러 이름으로 해석하기도 한다(tool 이 내부에서 재조회).
        # 같은 문장을 두 번 싣지 않는다.
        if line not in lines:
            lines.append(line)
    return lines


@dataclass(slots=True)
class ToolEnvelope:
    """public tool 공통 응답."""

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
            "warnings": _merge_declared(self.warnings),
            "data": self.data,
            "evidence_refs": evidence,
            "next_actions": self.next_actions,
        }


def as_pretty_json(payload: dict[str, Any]) -> str:
    """UTF-8 friendly JSON 직렬화."""

    return json.dumps(payload, ensure_ascii=False, indent=2)

