"""v2 shareholder_meeting public tool."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload


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


def _phase_label(value: str) -> str:
    return _PHASE_LABELS.get(value, value or "-")


def _result_status_label(value: str) -> str:
    return _RESULT_STATUS_LABELS.get(value, value or "-")


def _presence_flag_label(value: str) -> str:
    return _PRESENCE_FLAG_LABELS.get(value, value or "-")


def _warning_block(payload: dict[str, Any]) -> list[str]:
    warnings = payload.get("warnings", [])
    if not warnings:
        return []
    lines = ["## 유의사항"]
    for warning in warnings:
        lines.append(f"- {warning}")
    lines.append("")
    return lines


def _render_error(payload: dict[str, Any]) -> str:
    lines = [
        f"# shareholder_meeting: {payload.get('subject', '')}",
        "",
        "주총 공시를 확정하지 못했다.",
    ]
    for warning in payload.get("warnings", []):
        lines.append(f"- {warning}")
    return "\n".join(lines)


def _render_ambiguous(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    candidates = data.get("candidates", [])
    lines = [
        f"# shareholder_meeting: {data.get('query', payload.get('subject', ''))}",
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


def _render_summary(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    notice = data.get("notice", {})
    info = data.get("meeting_info", {})
    agenda_summary = data.get("agenda_summary", {})
    correction = data.get("correction_summary")
    result_reference = data.get("result_reference", {})
    alternatives = data.get("alternative_meetings", [])
    coverage_12m = data.get("meeting_coverage_12m", {})
    requested_window = data.get("requested_window", {})

    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} 주주총회"]
    lines.append("")
    lines.append(f"- company_id: `{data.get('company_id', '')}`")
    lines.append(f"- requested_meeting_type: `{data.get('requested_meeting_type', '')}`")
    lines.append(f"- selected_meeting_type: `{data.get('meeting_type', '')}`")
    lines.append(f"- meeting_phase: {_phase_label(data.get('meeting_phase', ''))} (`{data.get('meeting_phase', '')}`)")
    lines.append(f"- result_status: {_result_status_label(data.get('result_status', ''))} (`{data.get('result_status', '')}`)")
    lines.append(f"- notice_parse_source: `{data.get('notice_parse_source', '')}`")
    lines.append(f"- status: `{payload.get('status', '')}`")
    if requested_window:
        lines.append(
            f"- requested_window: `{requested_window.get('start_date', '')}` ~ `{requested_window.get('end_date', '')}`"
        )
    lines.append("")

    lines.extend(_warning_block(payload))

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
        lines.append(f"- 플래그: {_presence_flag_label(coverage_12m.get('presence_flag', ''))} (`{coverage_12m.get('presence_flag', '')}`)")
        lines.append(f"- 조사 구간: {coverage_12m.get('window_start', '-')} ~ {coverage_12m.get('window_end', '-')}")
        lines.append(f"- 정기주총 공시 수: {coverage_12m.get('annual_count', 0)}")
        lines.append(f"- 임시주총 공시 수: {coverage_12m.get('extraordinary_count', 0)}")
        latest_annual = coverage_12m.get("latest_annual")
        latest_extraordinary = coverage_12m.get("latest_extraordinary")
        if latest_annual:
            lines.append(
                f"- 최근 정기주총: {latest_annual.get('meeting_date') or '-'} / notice `{latest_annual.get('notice_rcept_no', '')}`"
            )
        if latest_extraordinary:
            lines.append(
                f"- 최근 임시주총: {latest_extraordinary.get('meeting_date') or '-'} / notice `{latest_extraordinary.get('notice_rcept_no', '')}`"
            )
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
        "## 결과 시점",
        "| 항목 | 값 |",
        "|------|----|",
        f"| 현재 단계 | {_phase_label(data.get('meeting_phase', ''))} |",
        f"| 결과 상태 | {_result_status_label(data.get('result_status', ''))} |",
        f"| 결과 공시일 | {result_reference.get('disclosure_date', '') or '-'} |",
        f"| 결과 rcept_no | `{result_reference.get('rcept_no', '')}` |" if result_reference else "| 결과 rcept_no | - |",
        "",
        "## 안건 요약",
        f"- 루트 안건 수: {agenda_summary.get('root_count', 0)}",
        f"- 전체 안건 수: {agenda_summary.get('total_count', 0)}",
    ])

    titles = agenda_summary.get("titles") or []
    if titles:
        lines.append("- 상위 안건")
        for title in titles:
            lines.append(f"  - {title}")

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

    lines.append("")
    lines.append("다음 단계:")
    lines.append("- `scope=\"agenda\"`로 전체 안건 트리 확인")
    lines.append("- `scope=\"board\"`로 이사/감사 후보 확인")
    lines.append("- `scope=\"compensation\"`로 보수한도 확인")
    lines.append("- `scope=\"aoi_change\"`로 정관변경 상세 (변경전/후/사유) 확인")
    if data.get("result_status") == "available":
        lines.append("- `scope=\"results\"`로 실제 의결 결과 확인")
    lines.append("- `scope=\"full\"`로 모든 scope 한 번에 보기")
    return "\n".join(lines)


def _render_agenda(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    agendas = data.get("agendas", [])
    notice = data.get("notice", {})

    def render_nodes(nodes: list[dict[str, Any]], indent: int = 0) -> list[str]:
        lines: list[str] = []
        prefix = "  " * indent
        for node in nodes:
            source = f" [{node['source']}]" if node.get("source") else ""
            conditional = f" ({node['conditional']})" if node.get("conditional") else ""
            lines.append(f"{prefix}- **{node.get('number', '')}** {node.get('title', '')}{source}{conditional}")
            lines.extend(render_nodes(node.get("children", []), indent + 1))
        return lines

    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} 주총 안건", ""]
    lines.append(f"- selected_meeting_type: `{data.get('meeting_type', '')}`")
    lines.append(f"- rcept_no: `{notice.get('rcept_no', '')}`")
    lines.append(f"- meeting_phase: {_phase_label(data.get('meeting_phase', ''))} (`{data.get('meeting_phase', '')}`)")
    lines.append(f"- status: `{payload.get('status', '')}`")
    lines.append("")
    lines.extend(_warning_block(payload))

    lines.append("## 안건 트리")
    lines.extend(render_nodes(agendas))
    if not agendas:
        lines.append("- 파싱된 안건이 없다")
    return "\n".join(lines)


def _render_board(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    board = data.get("board", {})
    summary = data.get("board_summary", {})
    appointments = board.get("appointments", [])

    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} 이사/감사 안건", ""]
    lines.append(f"- selected_meeting_type: `{data.get('meeting_type', '')}`")
    lines.append(f"- meeting_phase: {_phase_label(data.get('meeting_phase', ''))} (`{data.get('meeting_phase', '')}`)")
    lines.append(f"- result_status: {_result_status_label(data.get('result_status', ''))} (`{data.get('result_status', '')}`)")
    lines.append(f"- status: `{payload.get('status', '')}`")
    lines.append("")
    lines.extend(_warning_block(payload))

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


def _render_compensation(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    compensation = data.get("compensation", {})
    summary = data.get("compensation_summary", {})
    items = compensation.get("items", [])

    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} 보수한도 안건", ""]
    lines.append(f"- selected_meeting_type: `{data.get('meeting_type', '')}`")
    lines.append(f"- meeting_phase: {_phase_label(data.get('meeting_phase', ''))} (`{data.get('meeting_phase', '')}`)")
    lines.append(f"- result_status: {_result_status_label(data.get('result_status', ''))} (`{data.get('result_status', '')}`)")
    lines.append(f"- status: `{payload.get('status', '')}`")
    lines.append("")
    lines.extend(_warning_block(payload))

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


def _render_aoi(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    aoi = data.get("aoi_change", {}) or {}
    amendments = aoi.get("amendments", [])
    summary = aoi.get("summary", {}) or {}

    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} 정관변경 상세", ""]
    lines.append(f"- selected_meeting_type: `{data.get('meeting_type', '')}`")
    lines.append(f"- meeting_phase: {_phase_label(data.get('meeting_phase', ''))} (`{data.get('meeting_phase', '')}`)")
    lines.append(f"- status: `{payload.get('status', '')}`")
    lines.append("")
    lines.extend(_warning_block(payload))

    lines.append("## 요약")
    lines.append(f"- 정관변경 안건 수: {len(amendments)}건")
    if summary.get("category_count"):
        lines.append(f"- 카테고리 수: {summary.get('category_count')}")
    lines.append("")

    if not amendments:
        lines.append("확인된 정관변경 안건이 없다.")
        return "\n".join(lines)

    lines.append("## 세부의안")
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
    return "\n".join(lines)


def _render_full(payload: dict[str, Any]) -> str:
    sections = [
        _render_summary(payload),
        "",
        "---",
        "",
        _render_agenda(payload),
        "",
        "---",
        "",
        _render_board(payload),
        "",
        "---",
        "",
        _render_compensation(payload),
        "",
        "---",
        "",
        _render_aoi(payload),
    ]
    data = payload.get("data", {})
    if data.get("results"):
        sections.extend(["", "---", "", _render_results(payload)])
    return "\n".join(sections)


def _render_results(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    result_reference = data.get("result_reference", {})
    results = data.get("results", {})
    items = results.get("items", [])

    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} 주총 결과", ""]
    lines.append(f"- selected_meeting_type: `{data.get('meeting_type', '')}`")
    lines.append(f"- meeting_phase: {_phase_label(data.get('meeting_phase', ''))} (`{data.get('meeting_phase', '')}`)")
    lines.append(f"- result_status: {_result_status_label(data.get('result_status', ''))} (`{data.get('result_status', '')}`)")
    lines.append(f"- status: `{payload.get('status', '')}`")
    lines.append("")
    lines.extend(_warning_block(payload))

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


def register_tools(mcp):

    @mcp.tool()
    async def shareholder_meeting(
        company: str,
        meeting_type: str = "auto",
        scope: str = "summary",
        year: int = 0,
        start_date: str = "",
        end_date: str = "",
        lookback_months: int = 12,
        format: str = "md",
    ) -> str:
        """desc: 정기주총/임시주총 데이터 탭. 안건·이사 후보·보수한도·정관변경·결과를 scope별로 점진 로드한다.
        when: 주총 일정, 안건, 후보자, 보수한도, 정관변경, 실제 의결 결과가 필요할 때.
        rule: 회사 식별이 exact가 아니면 자동 선택 안 함. 정정공시 있으면 최신 정정본 자동 선택(_auto_rank_key). 소스는 DART 공시검색 + DART XML, 결과공시는 KIND whitelist(80→00 변환). PDF 다운로드 미사용.
        meeting_type: `auto`=정기/임시 최신 회차 비교 후 대표성 높은 쪽 선택 / `annual`=정기만 / `extraordinary`=임시만 (임시주총 명시적으로 찾을 때)
        scope: `summary`(기본, 정정공시 포함 메타) / `agenda`(안건 트리) / `board`(이사·감사 후보 경력) / `compensation`(보수한도) / `aoi_change`(정관변경 변경전/후/사유) / `results`(KIND 투표결과) / `full`(모든 scope 병렬)
        year: 미지정 시 현재 기준 최근 12개월 window. 과거 연도 조사 시 year 명시 권장
        ref: company, ownership_structure, proxy_contest, evidence
        """
        payload = await build_shareholder_meeting_payload(
            company,
            meeting_type=meeting_type,
            scope=scope,
            year=year or None,
            start_date=start_date,
            end_date=end_date,
            lookback_months=lookback_months,
        )
        if format == "json":
            return as_pretty_json(payload)
        status = payload.get("status")
        if status == "ambiguous":
            return _render_ambiguous(payload)
        if scope == "agenda":
            return _render_agenda(payload)
        if scope == "board":
            return _render_board(payload)
        if scope == "compensation":
            return _render_compensation(payload)
        if scope == "aoi_change":
            return _render_aoi(payload)
        if scope == "results":
            return _render_results(payload)
        if scope == "full":
            return _render_full(payload)
        if status in {"exact", "partial", "requires_review", "conflict"}:
            return _render_summary(payload)
        return _render_error(payload)
