"""shareholder_meeting_notice / shareholder_meeting_results 공유 render helper.

Note: 이 모듈은 `_` prefix → tools_v2 auto-discovery 제외 (register_tools 호출 X).
"""

from __future__ import annotations

import re
from typing import Any


def _render_agenda_node(node: dict[str, Any], indent: int = 0) -> list[str]:
    """안건 트리 hierarchical render — number + title + 자식."""
    lines: list[str] = []
    prefix = "  " * indent
    number = node.get("number", "") or ""
    title = node.get("title", "") or ""
    source = f" [{node['source']}]" if node.get("source") else ""
    conditional = f" ({node['conditional']})" if node.get("conditional") else ""
    bullet = f"- **{number}** {title}" if number else f"- {title}"
    lines.append(f"{prefix}{bullet}{source}{conditional}")
    for child in (node.get("children") or []):
        lines.extend(_render_agenda_node(child, indent + 1))
    return lines


def _extract_fy_agenda_meta(agendas: list[dict[str, Any]]) -> dict[str, str]:
    """재무제표 승인 안건 title에서 회기 / 기간 / 배당 예정액 regex 추출.

    예: "제18기(2025.1.1~2025.12.31) 재무제표 승인의 건 (배당 예정액 보통주 1주당 500원)"
    회사마다 1호/2호 위치 다름 (정관변경이 1호인 회사도 있음) → 모든 root 안건 검사.
    """
    if not agendas:
        return {}
    title = ""
    for node in agendas:
        t = node.get("title") or ""
        if "재무제표" in t or "재무 상태표" in t or "재무상태표" in t:
            title = t
            break
    if not title:
        return {}
    out: dict[str, str] = {}
    # 회기 (예: 제18기, 제 18 기)
    m = re.search(r"제\s*(\d+)\s*기", title)
    if m:
        out["회기"] = f"제{m.group(1)}기"
    # 기간 (예: 2025.1.1~2025.12.31, 2025.01.01 ~ 2025.12.31, 2025.1.1.~2025.12.31. — trailing dot 포함)
    m = re.search(r"(\d{4}[.\-/]\s*\d{1,2}[.\-/]\s*\d{1,2}\.?)\s*[~∼-]\s*(\d{4}[.\-/]\s*\d{1,2}[.\-/]\s*\d{1,2}\.?)", title)
    if m:
        s1 = m.group(1).replace(' ', '').rstrip('.')
        s2 = m.group(2).replace(' ', '').rstrip('.')
        out["사업연도"] = f"{s1} ~ {s2}"
    # 배당 예정액 (예: 보통주 1주당 500원, 보통주 주당 500원)
    m = re.search(r"(보통주|우선주)?\s*(?:1\s*)?주\s*당\s*(\d{1,3}(?:,\d{3})*|\d+)\s*원", title)
    if m:
        share_type = m.group(1) or "보통주"
        out["배당 예정액"] = f"{share_type} 1주당 {m.group(2)}원"
    return out


_PHASE_LABELS = {
    "pre_meeting": "회의 전",
    "post_meeting_pre_result": "회의 종료 후 결과 미확인",
    "post_result": "결과공시 이후",
    "undetermined": "시점 미확정",
}

_RESULT_STATUS_LABELS = {
    "not_due_yet": "아직 결과가 나올 시점이 아님",
    "pending_or_missing": "결과 공시 대기 또는 미확인",
    "available": "결과 확인 가능",
    "requires_review": "결과 확인은 되나 검토 필요",
    "unknown": "상태 미확정",
}

_PRESENCE_FLAG_LABELS = {
    "annual_only": "정기주총만 확인",
    "extraordinary_only": "임시주총만 확인",
    "annual_and_extraordinary": "정기·임시 모두 확인",
    "none": "주총 회차 미확인",
}


def phase_label(value: str) -> str:
    return _PHASE_LABELS.get(value, value or "-")


def result_status_label(value: str) -> str:
    return _RESULT_STATUS_LABELS.get(value, value or "-")


def presence_flag_label(value: str) -> str:
    return _PRESENCE_FLAG_LABELS.get(value, value or "-")


def warning_block(payload: dict[str, Any]) -> list[str]:
    warnings = payload.get("warnings", [])
    if not warnings:
        return []
    lines = ["## 유의사항"]
    for warning in warnings:
        lines.append(f"- {warning}")
    lines.append("")
    return lines


def render_error(payload: dict[str, Any], tool_label: str) -> str:
    lines = [f"# {tool_label}: {payload.get('subject', '')}", "", "주총 공시를 확정하지 못했다."]
    for w in payload.get("warnings", []):
        lines.append(f"- {w}")
    return "\n".join(lines)


def render_ambiguous(payload: dict[str, Any], tool_label: str) -> str:
    data = payload.get("data", {})
    candidates = data.get("candidates", [])
    lines = [
        f"# {tool_label}: {data.get('query', payload.get('subject', ''))}",
        "",
        "회사 식별이 애매해 주총 공시를 자동 선택하지 않았다.",
        "",
        "| 회사명 | ticker | corp_code | company_id |",
        "|------|--------|-----------|------------|",
    ]
    for item in candidates:
        lines.append(
            f"| {item.get('corp_name', '')} | `{item.get('ticker', '')}` | `{item.get('corp_code', '')}` | `{item.get('company_id', '')}` |"
        )
    return "\n".join(lines)


def render_summary(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    notice = data.get("notice", {})
    info = data.get("meeting_info", {})
    agenda_summary = data.get("agenda_summary", {})
    agendas_tree = data.get("agendas", []) or []
    correction = data.get("correction_summary")
    alternatives = data.get("alternative_meetings", [])
    coverage_12m = data.get("meeting_coverage_12m", {})
    requested_window = data.get("requested_window", {})

    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} 주주총회 소집공고"]
    lines.append("")
    lines.append(f"- company_id: `{data.get('company_id', '')}`")
    lines.append(f"- requested_meeting_type: `{data.get('requested_meeting_type', '')}`")
    lines.append(f"- selected_meeting_type: `{data.get('meeting_type', '')}`")
    lines.append(f"- meeting_phase: {phase_label(data.get('meeting_phase', ''))} (`{data.get('meeting_phase', '')}`)")
    # 260505 ralph: result_status 제거 (사후 정보, 시점 분리 위반)
    lines.append(f"- notice_parse_source: `{data.get('notice_parse_source', '')}`")
    lines.append(f"- status: `{payload.get('status', '')}`")
    if requested_window:
        lines.append(
            f"- requested_window: `{requested_window.get('start_date', '')}` ~ `{requested_window.get('end_date', '')}`"
        )
    lines.append("")
    lines.extend(warning_block(payload))

    if data.get("selection_basis"):
        lines.append("## 회차 선택")
        lines.append(f"- 선택 근거: {data.get('selection_basis')}")
        if alternatives:
            lines.append("- 대안 회차")
            for item in alternatives:
                lines.append(
                    f"  - {item.get('meeting_type')} / {item.get('meeting_phase')} / notice `{item.get('notice_rcept_no', '')}` / result `{item.get('result_rcept_no', '') or '-'}`"
                )
        lines.append("")

    if coverage_12m:
        lines.append("## 조회 구간 커버리지")
        lines.append(f"- 플래그: {presence_flag_label(coverage_12m.get('presence_flag', ''))} (`{coverage_12m.get('presence_flag', '')}`)")
        lines.append(f"- 조사 구간: {coverage_12m.get('window_start', '-')} ~ {coverage_12m.get('window_end', '-')}")
        lines.append(f"- 정기주총 공시 수: {coverage_12m.get('annual_count', 0)}")
        lines.append(f"- 임시주총 공시 수: {coverage_12m.get('extraordinary_count', 0)}")
        latest_annual = coverage_12m.get("latest_annual")
        latest_extraordinary = coverage_12m.get("latest_extraordinary")
        if latest_annual:
            lines.append(f"- 최근 정기주총: {latest_annual.get('meeting_date') or '-'} / notice `{latest_annual.get('notice_rcept_no', '')}`")
        if latest_extraordinary:
            lines.append(f"- 최근 임시주총: {latest_extraordinary.get('meeting_date') or '-'} / notice `{latest_extraordinary.get('notice_rcept_no', '')}`")
        lines.append("")

    lines.extend([
        "## 공시",
        "| 항목 | 값 |",
        "|------|----|",
        f"| 공시명 | {notice.get('report_name', '') or '-'} |",
        f"| 공시일 | {notice.get('disclosure_date', '') or '-'} |",
        f"| rcept_no | `{notice.get('rcept_no', '')}` |",
        f"| 정정 여부 | {'예' if notice.get('is_correction') else '아니오'} |",
        "",
        "## 회의 정보",
        "| 항목 | 값 |",
        "|------|----|",
        f"| 구분 | {info.get('meeting_type', '') or '-'} |",
        f"| 기수 | {info.get('meeting_term', '') or '-'} |",
        f"| 일시 | {info.get('datetime', '') or '-'} |",
        f"| 장소 | {info.get('location', '') or '-'} |",
        "",
        # 260505 ralph: 결과 시점 표 제거 (사후 정보, shareholder_meeting_results tool 참조)
        "## 안건",
        f"- 루트 안건 수: {agenda_summary.get('root_count', 0)} / 전체 안건 수: {agenda_summary.get('total_count', 0)}",
    ])

    # 260505 ralph: agenda hierarchy 통합 (이전 agenda scope 흡수)
    if agendas_tree:
        for node in agendas_tree:
            lines.extend(_render_agenda_node(node, indent=0))
    else:
        # fallback — agenda tree 없으면 flat titles
        titles = agenda_summary.get("titles") or []
        for title in titles:
            lines.append(f"  - {title}")

    # 1호 안건 메타 (회기 / 기간 / 배당 예정액) regex 추출 — 정기주총 표준
    fy_meta = _extract_fy_agenda_meta(agendas_tree)
    if fy_meta:
        lines.append("")
        lines.append("## 1호 안건 메타 (재무제표 승인)")
        for k, v in fy_meta.items():
            lines.append(f"- {k}: {v}")

    report_items = info.get("report_items") or []
    if report_items:
        lines.append("")
        lines.append("## 보고사항")
        for item in report_items:
            lines.append(f"- {item}")

    if correction:
        lines.append("")
        lines.append("## 정정 요약")
        lines.append(f"- 원공시일: {correction.get('original_date') or '-'}")
        lines.append(f"- 정정공시일: {correction.get('date') or '-'}")
        if correction.get("reason"):
            lines.append(f"- 정정 사유: {correction.get('reason')}")

    if data.get("raw_text_excerpt"):
        lines.append("")
        lines.append("## 원문 발췌 (DART 본문, 구조 파싱 실패 fallback)")
        lines.append(f"- 원문 총 길이: {data.get('raw_text_full_length', 0):,}자 (최대 6000자만 표시)")
        lines.append("```")
        lines.append(data["raw_text_excerpt"])
        lines.append("```")
    return "\n".join(lines)


def render_board(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    board = data.get("board", {})
    summary = data.get("board_summary", {})
    appointments = board.get("appointments", [])

    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} 이사/감사 안건", ""]
    lines.append(f"- selected_meeting_type: `{data.get('meeting_type', '')}`")
    lines.append(f"- meeting_phase: {phase_label(data.get('meeting_phase', ''))} (`{data.get('meeting_phase', '')}`)")
    lines.append(f"- result_status: {result_status_label(data.get('result_status', ''))} (`{data.get('result_status', '')}`)")
    lines.append(f"- status: `{payload.get('status', '')}`")
    lines.append("")
    lines.extend(warning_block(payload))

    lines.append("## 요약")
    lines.append(f"- 총 인사 안건: {summary.get('total_appointments', 0)}건")
    lines.append(f"- 총 후보자 수: {summary.get('total_candidates', 0)}명")
    lines.append(f"- 사외이사 후보: {summary.get('outside_directors', 0)}명")
    lines.append(f"- 감사위원 후보: {summary.get('audit_committee', 0)}명")
    lines.append("")

    lines.append("## 후보자")
    if not appointments:
        lines.append("- 확인된 인사 안건이 없다")
        return "\n".join(lines)

    for item in appointments:
        lines.append(f"### {item.get('number', '')} {item.get('title', '')}")
        lines.append(f"- 구분: {item.get('action', '-') } / {item.get('category', '-')}")
        for candidate in item.get("candidates", []):
            lines.append(f"- 후보자: **{candidate.get('name', '-') }**")
            if candidate.get("roleType"):
                lines.append(f"  - 직위: {candidate.get('roleType')}")
            if candidate.get("mainJob"):
                lines.append(f"  - 주요경력: {candidate.get('mainJob')}")
            if candidate.get("recommender"):
                lines.append(f"  - 추천인: {candidate.get('recommender')}")
            if candidate.get("majorShareholderRelation"):
                lines.append(f"  - 최대주주 관계: {candidate.get('majorShareholderRelation')}")
    return "\n".join(lines)


def render_compensation(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    compensation = data.get("compensation", {})
    summary = data.get("compensation_summary", {})
    items = compensation.get("items", [])

    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} 보수한도 안건", ""]
    lines.append(f"- selected_meeting_type: `{data.get('meeting_type', '')}`")
    lines.append(f"- meeting_phase: {phase_label(data.get('meeting_phase', ''))} (`{data.get('meeting_phase', '')}`)")
    lines.append(f"- result_status: {result_status_label(data.get('result_status', ''))} (`{data.get('result_status', '')}`)")
    lines.append(f"- status: `{payload.get('status', '')}`")
    lines.append("")
    lines.extend(warning_block(payload))

    lines.append("## 요약")
    lines.append(f"- 안건 수: {summary.get('totalItems', 0)}건")
    if summary.get("currentTotalLimit") is not None:
        lines.append(f"- 당기 한도 총액: {summary.get('currentTotalLimit'):,}원")
    if summary.get("priorTotalPaid") is not None:
        lines.append(f"- 전기 실제 지급: {summary.get('priorTotalPaid'):,}원")
    if summary.get("priorTotalLimit") is not None:
        lines.append(f"- 전기 한도: {summary.get('priorTotalLimit'):,}원")
    if summary.get("priorUtilization") is not None:
        lines.append(f"- 전기 소진율: {summary.get('priorUtilization')}%")
    lines.append("")

    lines.append("## 세부 안건")
    if not items:
        lines.append("- 확인된 보수한도 안건이 없다")
        return "\n".join(lines)

    for item in items:
        current = item.get("current", {})
        prior = item.get("prior", {})
        lines.append(f"### {item.get('number', '')} {item.get('title', '')}")
        if current.get("limit"):
            lines.append(f"- 당기 한도: {current.get('limit')}")
        if prior.get("actualPaid"):
            lines.append(f"- 전기 실제 지급: {prior.get('actualPaid')}")
        if prior.get("limit"):
            lines.append(f"- 전기 한도: {prior.get('limit')}")
    return "\n".join(lines)


def render_aoi(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    aoi = data.get("aoi_change", {}) or {}
    amendments = aoi.get("amendments", [])
    retire_amendments = aoi.get("retirement_amendments", []) or []
    summary = aoi.get("summary", {}) or {}

    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} 정관변경 + 퇴직금 변경 raw", ""]
    lines.append(f"- selected_meeting_type: `{data.get('meeting_type', '')}`")
    lines.append(f"- meeting_phase: {phase_label(data.get('meeting_phase', ''))} (`{data.get('meeting_phase', '')}`)")
    lines.append(f"- status: `{payload.get('status', '')}`")
    lines.append("")
    lines.extend(warning_block(payload))

    lines.append("## 요약")
    lines.append(f"- 정관변경 안건: {len(amendments)}건 / 퇴직금 변경: {len(retire_amendments)}건")
    if summary.get("category_count"):
        lines.append(f"- 카테고리 수: {summary.get('category_count')}")
    lines.append("")

    if not amendments and not retire_amendments:
        lines.append("확인된 정관변경 / 퇴직금 변경 안건이 없다.")
        return "\n".join(lines)

    if amendments:
        lines.append("## 정관변경 세부의안")
        for item in amendments:
            sub_id = item.get("subAgendaId") or ""
            label = item.get("label") or item.get("clause", "")
            header = f"제{sub_id}호 {label}".strip() if sub_id else label or "-"
            lines.append(f"### {header}")
            before = (item.get("before") or "").strip()
            after = (item.get("after") or "").strip()
            reason = (item.get("reason") or "").strip()
            if before:
                lines.append("**변경 전**")
                lines.append(f"> {before}")
            if after:
                lines.append("**변경 후**")
                lines.append(f"> {after}")
            if reason:
                lines.append(f"**사유**: {reason}")
            lines.append("")

    # 260505 ralph: 퇴직금 변경 raw 통합 (data tool 원칙 — raw 노출만, 판단 X)
    if retire_amendments:
        lines.append("## 퇴직금 변경 raw")
        for i, a in enumerate(retire_amendments, 1):
            clause = (a.get("clause") or "").strip() or f"항목 {i}"
            lines.append(f"### {clause}")
            before = (a.get("before") or "").strip()
            after = (a.get("after") or "").strip()
            reason = (a.get("reason") or "").strip()
            if before:
                lines.append("**변경 전**")
                lines.append(f"> {before[:500]}")
            if after:
                lines.append("**변경 후**")
                lines.append(f"> {after[:500]}")
            if reason:
                lines.append(f"**사유**: {reason}")
            lines.append("")

    return "\n".join(lines)


def render_results(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    result_reference = data.get("result_reference", {})
    results = data.get("results", {})
    items = results.get("items", [])

    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} 주총 결과", ""]
    lines.append(f"- selected_meeting_type: `{data.get('meeting_type', '')}`")
    lines.append(f"- meeting_phase: {phase_label(data.get('meeting_phase', ''))} (`{data.get('meeting_phase', '')}`)")
    lines.append(f"- result_status: {result_status_label(data.get('result_status', ''))} (`{data.get('result_status', '')}`)")
    lines.append(f"- status: `{payload.get('status', '')}`")
    lines.append("")
    lines.extend(warning_block(payload))

    lines.append("## 결과 공시")
    lines.append(f"- result rcept_no: `{result_reference.get('rcept_no', '')}`" if result_reference else "- result rcept_no: -")
    if result_reference.get("kind_acptno"):
        lines.append(f"- KIND acptno: `{result_reference.get('kind_acptno')}`")
    if results.get("result_format"):
        lines.append(f"- result_format: `{results.get('result_format')}`")
    if results.get("numerical_vote_table_available") is not None:
        lines.append(f"- numerical_vote_table_available: `{results.get('numerical_vote_table_available')}`")
    lines.append("")

    lines.append("## 의결 결과")
    if not items:
        lines.append("- 현재 확보된 의결 결과가 없다")
        return "\n".join(lines)

    for item in items:
        lines.append(f"### {item.get('number', '')} {item.get('agenda', '')}")
        lines.append(f"- 결의 유형: {item.get('resolution_type', '-')}")
        lines.append(f"- 결과: {item.get('passed', '-')}")
        if item.get("approval_rate_issued"):
            lines.append(f"- 발행주식수 기준 찬성률: {item.get('approval_rate_issued')}%")
        if item.get("approval_rate_voted"):
            lines.append(f"- 출석주식수 기준 찬성률: {item.get('approval_rate_voted')}%")
        if item.get("opposition_rate"):
            lines.append(f"- 반대율: {item.get('opposition_rate')}%")
    return "\n".join(lines)
