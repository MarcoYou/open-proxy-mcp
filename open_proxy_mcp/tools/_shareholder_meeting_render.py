"""shareholder_meeting_notice / shareholder_meeting_results 공유 render helper.

Note: 이 모듈은 `_` prefix → tools auto-discovery 제외 (register_tools 호출 X).
"""

from __future__ import annotations

import re
from typing import Any

from open_proxy_mcp.tools._shared import company_id_line


# 주총 종류 라벨 — 한 벌만 둔다(사전을 복제하면 한쪽만 고쳐져 다른 표에서 샌다).
_MEETING_TYPE_KO = {"annual": "정기주총", "extraordinary": "임시주총"}


def _render_agenda_node(node: dict[str, Any], indent: int = 0) -> list[str]:
    """안건 트리 hierarchical render — number + title + 자식."""
    lines: list[str] = []
    prefix = "  " * indent
    number = node.get("number", "") or ""
    title = node.get("title", "") or ""
    source = f" [{node['source']}]" if node.get("source") else ""
    conditional = f" ({node['conditional']})" if node.get("conditional") else ""
    # 상법 §449조의2 — 재무제표가 이사회 승인으로 갈음돼 표결하지 않는 안건.
    # 표시가 없으면 읽는 쪽이 일반 안건으로 오인해 찬반 의견을 만들어 낸다.
    _vote = {"report_only": " 🚫 **표결 대상 아님 — 보고사항**",
             "report_if_conditions_met": " ⚠️ 요건 충족 시 보고사항으로 전환 가능"}.get(
        node.get("resolution_status") or "", "")
    bullet = f"- **{number}** {title}" if number else f"- {title}"
    lines.append(f"{prefix}{bullet}{source}{conditional}{_vote}")
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
    _cid = company_id_line(data)
    if _cid:
        lines.append(_cid)
    _mt = data.get("meeting_type", "")
    lines.append(f"- 주총 종류: {_MEETING_TYPE_KO.get(_mt, _mt)}")
    lines.append(f"- 진행 단계: {phase_label(data.get('meeting_phase', ''))}")
    # 260505 ralph: result_status 제거 (사후 정보, 시점 분리 위반)
    if requested_window:
        # 「조회 구간」이라 쓰면 공시를 언제부터 언제까지 검색했나로 읽히는데, 실제로 이 구간에
        # 걸리는 값은 **회의일**이다. 접수일은 미래일 수 없으니 끝날짜가 오늘 이후로 보이면
        # 읽는 쪽이 곧장 오해한다 — 무엇을 재는 구간인지 라벨에 밝힌다.
        lines.append(
            f"- 회의일 기준 구간: {requested_window.get('start_date', '')}"
            f" ~ {requested_window.get('end_date', '')}"
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
                    f"  - {_MEETING_TYPE_KO.get(item.get('meeting_type'), item.get('meeting_type'))}"
                    f" / {phase_label(item.get('meeting_phase', ''))}"
                    f" / 소집공고 `{item.get('notice_rcept_no', '')}`"
                    f" / 결과 `{item.get('result_rcept_no', '') or '-'}`"
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
        f"| 공시번호 | `{notice.get('rcept_no', '')}` |",
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

    # 목적사항별 기재사항 구간 원문. 안건과 구간을 짝지어 주지 않는 대신 어느 구간도 버리지
    # 않는다 — 표 파싱이 실패하면 통째로 사라지던 내용(주주제안 후보 명단·자기주식 처분계획·
    # 퇴직금 규정 개정 상세)이 여기로 나온다. 재무제표 구간은 머리만 남긴다(수치는 API가 정본).
    sections = data.get("agenda_detail_sections") or []
    if sections:
        lines.append("")
        lines.append("## 목적사항별 기재사항 (원문)")
        lines.append("- 어느 안건의 상세인지는 구간 제목과 본문을 보고 판단한다.")
        for sec in sections:
            note = f" · 이하 생략(전체 {sec['chars']:,}자)" if sec.get("truncated") else ""
            lines.append("")
            lines.append(f"### {sec.get('heading') or sec['code']}{note}")
            lines.append("```")
            lines.append(sec.get("text") or "")
            lines.append("```")

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
        # 통째 반환은 실패가 아니라 설계된 경로다 — '실패·fallback'으로 표기하지 않는다.
        lines.append("## 원문 발췌 (DART 본문)")
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
    _mt2 = data.get("meeting_type", "")
    lines.append(f"- 주총 종류: {_MEETING_TYPE_KO.get(_mt2, _mt2)}")
    lines.append(f"- 진행 단계: {phase_label(data.get('meeting_phase', ''))}")
    lines.append(f"- 결과 공시: {result_status_label(data.get('result_status', ''))}")
    lines.append("")
    lines.extend(warning_block(payload))

    lines.append("## 요약")
    lines.append(f"- 총 인사 안건: {summary.get('total_appointments', 0)}건")
    _tc = summary.get("total_candidates", 0)
    _uc = summary.get("unique_candidates")
    if _uc is not None and _uc != _tc:
        # 같은 사람이 묶음 안건과 개별 안건에 겹쳐 나온 것이지 후보가 빠진 것이 아니다.
        lines.append(f"- 총 후보자 수: 고유 {_uc}명 (안건별 등장 {_tc}회 — "
                     f"같은 후보가 묶음 안건과 개별 안건에 겹쳐 나옵니다)")
    else:
        lines.append(f"- 총 후보자 수: {_tc}명")
    # 상법 §542의8(2026-07-23 시행)로 「사외이사」가 「독립이사」로 바뀌었다 — 시행 전후 공고가
    # 섞이므로 두 표기를 함께 쓴다. 후보 개인의 직위는 아래에 원문 표기 그대로 나온다.
    lines.append(f"- 사외이사(독립이사) 후보: {summary.get('outside_directors', 0)}명")
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
    _mt2 = data.get("meeting_type", "")
    lines.append(f"- 주총 종류: {_MEETING_TYPE_KO.get(_mt2, _mt2)}")
    lines.append(f"- 진행 단계: {phase_label(data.get('meeting_phase', ''))}")
    lines.append(f"- 결과 공시: {result_status_label(data.get('result_status', ''))}")
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

    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} 정관변경 + 퇴직금 변경 원문", ""]
    _mt2 = data.get("meeting_type", "")
    lines.append(f"- 주총 종류: {_MEETING_TYPE_KO.get(_mt2, _mt2)}")
    lines.append(f"- 진행 단계: {phase_label(data.get('meeting_phase', ''))}")
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
        lines.append("## 퇴직금 변경 원문")
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


def render_provisional_financials(payload: dict[str, Any]) -> str:
    """잠정 재무제표 4 quadrant raw 노출 (data tool — 판단 X)."""
    data = payload.get("data", {})
    pfs = data.get("prov_financials", {}) or {}
    notice = data.get("notice", {})
    metrics = pfs.get("metrics", {}) or {}

    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} 잠정 재무제표 (1호 안건 본문)", ""]
    _mt2 = data.get("meeting_type", "")
    lines.append(f"- 주총 종류: {_MEETING_TYPE_KO.get(_mt2, _mt2)}")
    lines.append(f"- 공시번호 {notice.get('rcept_no', '')}")
    lines.append(f"- 출처: 주총 소집공고 1호 안건 본문 (사업보고서 제출 전 회사 자가 공시 — 잠정치)")
    lines.append("")
    lines.extend(warning_block(payload))

    # 정량 metric summary
    if metrics.get("extraction_status") in ("success", "partial"):
        lines.append("## 정량 metric (flat)")
        lines.append(f"- extraction_status: {metrics.get('extraction_status')} / scope: {metrics.get('scope_used')}")
        for k in ("fy_current_revenue_krw", "fy_prior_revenue_krw",
                  "fy_current_operating_profit_krw", "fy_prior_operating_profit_krw",
                  "fy_current_net_income_krw", "fy_prior_net_income_krw",
                  "fy_current_total_assets_krw", "fy_prior_total_assets_krw",
                  "fy_current_total_liabilities_krw", "fy_prior_total_liabilities_krw",
                  "fy_current_total_equity_krw", "fy_prior_total_equity_krw"):
            v = metrics.get(k)
            if v is not None:
                lines.append(f"- {k}: {v:,}")
        lines.append("")

    # 4 quadrant 표
    for scope_label, scope_key in (("연결", "consolidated"), ("별도", "separate")):
        scope_data = pfs.get(scope_key) or {}
        if not scope_data or (not scope_data.get("balance_sheet") and not scope_data.get("income_statement")):
            continue
        lines.append(f"## {scope_label} 재무제표")
        for stmt_label, stmt_key in (("재무상태표", "balance_sheet"), ("손익계산서", "income_statement")):
            stmt = scope_data.get(stmt_key)
            if not stmt or not stmt.get("rows"):
                continue
            unit = stmt.get("unit") or "-"
            period = stmt.get("period_labels") or {}
            current_label = period.get("current") or "당기"
            prior_label = period.get("prior") or "전기"
            lines.append(f"### {stmt_label} (단위: {unit})")
            lines.append(f"| 과목 | {current_label} | {prior_label} |")
            lines.append("|------|----------|----------|")
            for row in stmt["rows"]:
                # row = [account, note, current, prior] or [account, current, prior]
                if len(row) >= 4:
                    acc, _, cur, prior = row[0], row[1], row[2], row[3]
                else:
                    acc, cur, prior = row[0], row[1] if len(row) > 1 else "", row[2] if len(row) > 2 else ""
                lines.append(f"| {acc} | {cur or '-'} | {prior or '-'} |")
            lines.append("")
        lines.append("")

    if not pfs.get("consolidated", {}).get("income_statement") and not pfs.get("separate", {}).get("income_statement"):
        # 원인을 단정하지 않는다 — 실측 34건 중 23건은 원문에 그 절이 아예 없었다.
        head = {"extraction_failed": "잠정 재무제표: 찾지 못함 — 원문에 실려 있습니다",
                "not_disclosed": "잠정 재무제표: 해당 없음",
                "unverified": "잠정 재무제표: 확인하지 못함"}.get(
                    pfs.get("absence_kind"), "잠정 재무제표를 내지 못했습니다")
        note = pfs.get("absence_note")
        lines.append(f"{head}" + (f" — {note}" if note else " — 원문을 확인하세요."))

    return "\n".join(lines)


def render_results(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    result_reference = data.get("result_reference", {})
    results = data.get("results", {})
    items = results.get("items", [])

    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} 주총 결과", ""]
    _mt2 = data.get("meeting_type", "")
    lines.append(f"- 주총 종류: {_MEETING_TYPE_KO.get(_mt2, _mt2)}")
    lines.append(f"- 진행 단계: {phase_label(data.get('meeting_phase', ''))}")
    lines.append(f"- 결과 공시: {result_status_label(data.get('result_status', ''))}")
    lines.append("")
    lines.extend(warning_block(payload))

    lines.append("## 결과 공시")
    lines.append(f"- 공시번호 {result_reference.get('rcept_no', '')}" if result_reference
                 else "- 공시번호 -")
    if result_reference.get("kind_acptno"):
        lines.append(f"- 거래소 접수번호 {result_reference.get('kind_acptno')}")
    # numerical_vote_table_available 은 result_format=="table" 과 같은 사실이라 줄을 따로 내지
    # 않는다 — 두 줄로 나오면 독립된 두 정보처럼 읽힌다(260728 QA 지적).
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
