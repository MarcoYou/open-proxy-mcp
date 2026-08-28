"""dilutive_issuance public tool."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.date_utils import format_iso_date
from open_proxy_mcp.tools._shared import company_id_line
from open_proxy_mcp.services.dilutive_issuance import build_dilutive_issuance_payload
from open_proxy_mcp.services.dilution_allottees import SECTION_CHARS_DEFAULT


def _render_error(payload: dict[str, Any]) -> str:
    lines = [f"# dilutive_issuance: {payload.get('subject', '')}", ""]
    for warning in payload.get("warnings", []):
        lines.append(f"- {warning}")
    return "\n".join(lines)


def _render_ambiguous(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    lines = [
        f"# dilutive_issuance: {data.get('query', '')}",
        "",
        "회사 식별이 애매해 자동 선택하지 않았다.",
        "",
        "| 회사명 | ticker | corp_code | company_id |",
        "|------|--------|-----------|------------|",
    ]
    for item in data.get("candidates", []):
        lines.append(
            f"| {item.get('corp_name', '')} | `{item.get('ticker', '')}` | `{item.get('corp_code', '')}` | `{item.get('company_id', '')}` |"
        )
    return "\n".join(lines)


def _link(rcept_no: str) -> str:
    url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}" if rcept_no else ""
    return f"[{rcept_no}]({url})" if url else f"`{rcept_no}`"


def _shares(value: Any) -> str:
    """`None` 은 **모른다**다. 0 으로 찍으면 「0주 증자」로 읽힌다."""
    return f"{value:,}주" if isinstance(value, int) else "미확인"


def _pct(value: Any) -> str:
    return f"{value:.2f}%" if isinstance(value, (int, float)) else "미확인"


def _render_rights_plan(plan: dict[str, Any], is_withdrawal: bool) -> list[str]:
    head = "철회 전 원안" if is_withdrawal else "정정 전 원안"
    lines = [f"- 📄 **{head}** (원본 공시 {_link(plan.get('source_rcept_no', ''))}, {plan.get('source_rcept_dt', '')})"]
    lines.append(
        f"  - 신주(보통): {_shares(plan.get('new_shares_common'))} / 기존: {_shares(plan.get('existing_shares_common'))}"
        f" → 희석률 근사 **{_pct(plan.get('dilution_pct_approx'))}**")
    price = plan.get("fixed_price_won") or plan.get("planned_price_won")
    if price:
        label = "확정발행가" if plan.get("fixed_price_won") else "예정발행가"
        lines.append(f"  - {label}: {price:,}원")
    if plan.get("planned_proceeds_won_derived"):
        lines.append(f"  - 자금조달 목적 합계: {plan['planned_proceeds_won_derived']:,}원 (원문 항목 합)")
    if plan.get("board_decision_date"):
        lines.append(f"  - 이사회결의일: {plan['board_decision_date']}")
    return lines


def _render_rights_card(row: dict[str, Any]) -> list[str]:
    fp = row.get("fund_purpose", {})
    lu = row.get("lock_up", {})
    title = "유상증자"
    if row.get("is_withdrawal"):
        title = "유상증자 (철회)"
    elif row.get("values_missing"):
        title = "유상증자 (정정 — 값 공란)"
    lines = [
        f"### {title} — {row.get('rcept_dt', '')} ({_link(row.get('rcept_no', ''))})",
        f"- 이사회결의일: {row.get('board_decision_date', '-') or '-'}",
        f"- 배정방식: **{row.get('issuance_method', '-') or '-'}**",
    ]
    if row.get("values_missing"):
        why = "철회되어" if row.get("is_withdrawal") else "정정되어"
        lines.append(
            f"- ⚠️ 신주수·발행가·자금목적이 **공시 응답에 없다**({why} 값이 비었다) — "
            f"**0 주 증자가 아니다.**")
        if row.get("original_filed_on"):
            lines.append(f"- 최초 제출일: {row['original_filed_on']}")
        plan = row.get("original_plan") or {}
        if plan.get("new_shares_common"):
            lines.extend(_render_rights_plan(plan, bool(row.get("is_withdrawal"))))
        if row.get("recovery_note"):
            lines.append(f"- {row['recovery_note']}")
        if row.get("withdrawal_reason"):
            lines.append(f"- 회사가 밝힌 철회 사유: {row['withdrawal_reason']}")
        lines.extend(_render_third_party_allotment(row.get("third_party_allotment") or {}))
        lines.append("")
        return lines
    lines.extend([
        f"- 신주(보통): {_shares(row.get('new_shares_common'))} / 기존: {_shares(row.get('existing_shares_common'))}",
        f"- 희석률 근사: **{_pct(row.get('dilution_pct_approx'))}** (기존대비 신주 비율)",
        f"- 액면가: {row.get('face_value_per_share', '-') or '-'}원",
        f"- 자금 목적: 시설 {fp.get('facility', '-') or '-'} / 운영 {fp.get('operating', '-') or '-'} / 채무상환 {fp.get('debt_repayment', '-') or '-'} / 기타법인주식 {fp.get('other_corp_share_acq', '-') or '-'}",
        f"- 보호예수: {lu.get('applicable', '-') or '-'} ({lu.get('begin_date', '-') or '-'} ~ {lu.get('end_date', '-') or '-'})",
    ])
    lines.extend(_render_third_party_allotment(row.get("third_party_allotment") or {}))
    lines.append("")
    return lines



def _render_excerpt(sec: dict[str, Any], indent: str = "  ") -> list[str]:
    """원문 대목을 **그대로** 싣는다. 표로 바꾸지 않는다 — 표는 아래에 더한다."""
    lines = [f"{indent}- 📄 **{sec.get('heading', '')}** — {sec.get('what_is_here', '')}"]
    body = (sec.get("excerpt") or "").strip()
    if not body:
        return lines
    lines.append(f"{indent}  ```")
    # DART 문서 text 는 표 칸마다 빈 줄이 끼어 온다 — **글자는 그대로 두고 빈 줄만** 접는다.
    blank = False
    for ln in body.split("\n"):
        if not ln.strip():
            blank = True
            continue
        if blank and lines[-1] != f"{indent}  ```":
            lines.append(indent)
        blank = False
        lines.append(f"{indent}  {ln.rstrip()}")
    lines.append(f"{indent}  ```")
    if sec.get("truncated"):
        lines.append(f"{indent}  - ⚠️ {sec.get('truncation_note', '')}")
    return lines


def _render_third_party_allotment(tpa: dict[str, Any]) -> list[str]:
    """제3자배정 대상자 — 「누가 받았나」. 정형 API 에 없어 원문에서 가져온 자리다."""
    if not tpa:
        return []
    lines = ["- 👤 **제3자배정 대상자 (원문)**"]
    if tpa.get("status") == "NOT_READ":
        lines.append(f"  - ⚠️ {tpa.get('note', '')}")
        for step in tpa.get("next_steps", []):
            lines.append(f"  - ↪︎ {step}")
        return lines
    lines.append(
        f"  - 출처: {tpa.get('source_report_nm', '')} {_link(tpa.get('source_rcept_no', ''))}"
        f" ({tpa.get('source_rcept_dt', '')}) · 창 `section_chars={tpa.get('section_chars_used', '')}`")

    rows = tpa.get("allottees") or []
    if rows:
        lines.append("")
        lines.append("  | 대상자 | 회사·최대주주와의 관계 | 배정주식수 | 비고 |")
        lines.append("  |---|---|---|---|")
        for r in rows:
            lines.append(
                f"  | {r.get('name', '')} | {r.get('relation_to_company_or_controller', '')} "
                f"| {r.get('allotted_shares_text', '')} | {r.get('note', '')} |")
        lines.append("")
    if tpa.get("allottee_parse_note"):
        lines.append(f"  - {tpa['allottee_parse_note']}")

    for sec in tpa.get("sections", []):
        lines.extend(_render_excerpt(sec, indent="  "))

    others = tpa.get("other_headings_in_document") or []
    if others:
        lines.append(f"  - 이 원문의 다른 대목(거기 없으면 여기일 수 있다): {', '.join(others[:12])}")
    for step in tpa.get("next_steps", []):
        lines.append(f"  - ↪︎ {step}")
    lines.append("")
    return lines


def _render_equity_channel(block: dict[str, Any]) -> list[str]:
    """발행공시(C001) — 인수인·자금사용 목적·실제 배정 결과가 있는 자리."""
    lines = [
        "## 발행공시 채널 — 증권신고서·투자설명서·증권발행실적보고서",
        f"- 채널: `{block.get('channel', '')}` · 조사 구간 내 {block.get('filing_count', 0)}건",
        f"- {block.get('note', '')}",
    ]
    filings = block.get("filings") or []
    if filings:
        lines.extend([
            "",
            "| 날짜 | 보고서 | 무엇이 여기 있나 | 원문 |",
            "|------|--------|-----------------|------|",
        ])
        for f in filings:
            lines.append(
                f"| {f.get('rcept_dt', '')} | {f.get('report_nm', '')} "
                f"| {f.get('what_is_here', '')} | {_link(f.get('rcept_no', ''))} |")
        lines.append("")
    latest = block.get("latest_issuance_result")
    if latest:
        lines.append(
            f"### 실제 배정 결과 — {latest.get('report_nm', '')} "
            f"({latest.get('rcept_dt', '')}, {_link(latest.get('rcept_no', ''))})")
        lines.append(f"- {latest.get('note', '')}")
        for sec in latest.get("sections", []):
            lines.extend(_render_excerpt(sec))
        for other in latest.get("other_results_not_read", []):
            lines.append(
                f"- 열지 않은 다른 실적보고서: {other.get('rcept_dt', '')} {_link(other.get('rcept_no', ''))}")
        lines.append("")
    return lines


def _render_cb_card(row: dict[str, Any]) -> list[str]:
    cv = row.get("conversion", {})
    fp = row.get("fund_purpose", {})
    return [
        f"### 전환사채 {row.get('bond_series', '')}회 — {row.get('rcept_dt', '')} ({_link(row.get('rcept_no', ''))})",
        f"- 이사회결의일: {row.get('board_decision_date', '-') or '-'}",
        f"- 종류: {row.get('bond_kind', '-') or '-'}",
        f"- 발행총액: **{row.get('total_issue_amount', '-') or '-'}원** / 방식: {row.get('issuance_method', '-') or '-'}",
        f"- 금리: 표면 {row.get('coupon_rate', '-') or '-'}% / YTM {row.get('yield_to_maturity', '-') or '-'}% / 만기 {row.get('maturity_date', '-') or '-'}",
        f"- 전환조건: 전환가 **{cv.get('price', '-') or '-'}원** / 전환비율 {cv.get('rate', '-') or '-'}% / 대상 {cv.get('target_stock_kind', '-') or '-'}",
        f"- 전환 시 발행주식: {cv.get('shares_if_converted', '-') or '-'}주 (**잠재 희석 {cv.get('pct_of_total_shares', '-') or '-'}%**)",
        f"- 전환청구기간: {cv.get('request_period_begin', '-') or '-'} ~ {cv.get('request_period_end', '-') or '-'}",
        f"- Refixing 하한: {cv.get('refixing_floor', '-') or '-'}",
        f"- 납입일: {row.get('payment_date', '-') or '-'} / 보증인: {row.get('guarantor', '-') or '-'} / 담보: {row.get('collateral', '-') or '-'}",
        f"- 자금 목적: 운영 {fp.get('operating', '-') or '-'} / 채무상환 {fp.get('debt_repayment', '-') or '-'} / 기타법인주식 {fp.get('other_corp_share_acq', '-') or '-'}",
        "",
    ]


def _render_eb_card(row: dict[str, Any]) -> list[str]:
    if row.get("detection_only"):
        return [
            f"### 교환사채 — {row.get('rcept_dt', '')} ({_link(row.get('rcept_no', ''))})",
            f"- ⚠️ {row.get('recovery_note', 'EB 공시 발견되었으나 구조화·원문 추출 불가 — 원문 확인 필요')}",
            "",
        ]
    ex = row.get("exchange", {})
    fp = row.get("fund_purpose", {})
    lines = [
        f"### 교환사채 {row.get('bond_series', '')}회 — {row.get('rcept_dt', '')} ({_link(row.get('rcept_no', ''))})",
        f"- 이사회결의일: {row.get('board_decision_date', '-') or '-'}",
        f"- 종류: {row.get('bond_kind', '-') or '-'}",
        f"- 발행총액: **{row.get('total_issue_amount', '-') or '-'}원** / 방식: {row.get('issuance_method', '-') or '-'}",
        f"- 금리: 표면 {row.get('coupon_rate', '-') or '-'}% / YTM {row.get('yield_to_maturity', '-') or '-'}% / 만기 {row.get('maturity_date', '-') or '-'}",
        f"- 교환조건: 교환가 **{ex.get('price', '-') or '-'}원** / 교환비율 {ex.get('rate', '-') or '-'}%",
        f"- 교환대상: **{ex.get('target', '-') or '-'}** {ex.get('target_share_count', '') or ''}{'주' if ex.get('target_share_count') else ''} (**발행총수 대비 {ex.get('pct_of_total_shares', '-') or '-'}%**)",
        f"- 교환청구기간: {ex.get('request_period_begin', '-') or '-'} ~ {ex.get('request_period_end', '-') or '-'}",
        f"- 인수자: {row.get('underwriter', '-') or '-'} / 납입일: {row.get('payment_date', '-') or '-'}",
        f"- 자금 목적: 운영 {fp.get('operating', '-') or '-'} / 채무상환 {fp.get('debt_repayment', '-') or '-'} / 기타법인주식 {fp.get('other_corp_share_acq', '-') or '-'}",
    ]
    if row.get("exchange", {}).get("target") and "자기주식" in str(row["exchange"]["target"]):
        lines.append("- ⚠️ 교환대상=자기주식 → 교환권 행사 시 의결권 부활(제3자 이전)로 **의결권 희석** 효과")
    if row.get("recovered_from_document"):
        lines.append(f"- 📄 {row.get('recovery_note', '')}")
    lines.append("")
    return lines


def _render_bw_card(row: dict[str, Any]) -> list[str]:
    w = row.get("warrant", {})
    fp = row.get("fund_purpose", {})
    return [
        f"### 신주인수권부사채 {row.get('bond_series', '')}회 — {row.get('rcept_dt', '')} ({_link(row.get('rcept_no', ''))})",
        f"- 이사회결의일: {row.get('board_decision_date', '-') or '-'}",
        f"- 종류: {row.get('bond_kind', '-') or '-'}",
        f"- 발행총액: **{row.get('total_issue_amount', '-') or '-'}원** / 방식: {row.get('issuance_method', '-') or '-'}",
        f"- 금리: 표면 {row.get('coupon_rate', '-') or '-'}% / YTM {row.get('yield_to_maturity', '-') or '-'}% / 만기 {row.get('maturity_date', '-') or '-'}",
        f"- 워런트: 행사가 **{w.get('exercise_price', '-') or '-'}원** / 비율 {w.get('exercise_rate', '-') or '-'}%",
        f"- 분리/비분리: {w.get('detachable', '-') or '-'} / 납입방법: {w.get('payment_method', '-') or '-'}",
        f"- 신주 대상: {w.get('new_stock_kind', '-') or '-'} {w.get('new_stock_count', '-') or '-'}주 (**잠재 희석 {w.get('pct_of_total_shares', '-') or '-'}%**)",
        f"- 행사기간: {w.get('exercise_period_begin', '-') or '-'} ~ {w.get('exercise_period_end', '-') or '-'}",
        f"- 납입일: {row.get('payment_date', '-') or '-'} / 보증인: {row.get('guarantor', '-') or '-'}",
        f"- 자금 목적: 운영 {fp.get('operating', '-') or '-'} / 기타법인주식 {fp.get('other_corp_share_acq', '-') or '-'}",
        "",
    ]


def _render_capital_reduction_card(row: dict[str, Any]) -> list[str]:
    sched = row.get("schedule", {})
    return [
        f"### 감자 — {row.get('rcept_dt', '')} ({_link(row.get('rcept_no', ''))})",
        f"- 이사회결의일: {row.get('board_decision_date', '-') or '-'}",
        f"- 감자비율: **{row.get('reduction_ratio_common', '-') or '-'}%** (보통주)",
        f"- 감소 주식수: {row.get('shares_reduced_common', '-') or '-'}주",
        f"- 자본금: {row.get('capital_before', '-') or '-'}원 → {row.get('capital_after', '-') or '-'}원",
        f"- 발행주식: {row.get('outstanding_before_common', '-') or '-'}주 → {row.get('outstanding_after_common', '-') or '-'}주",
        f"- 감자 방법: {row.get('method', '-') or '-'}",
        f"- 감자 사유: {row.get('reason', '-') or '-'}",
        f"- 기준일: {row.get('reduction_standard_date', '-') or '-'}",
        f"- 일정: 주총 {sched.get('shareholders_meeting', '-') or '-'} / 구주권 제출 {sched.get('old_share_submission_begin', '-') or '-'}~{sched.get('old_share_submission_end', '-') or '-'} / 매매정지 {sched.get('trading_suspension_begin', '-') or '-'}~{sched.get('trading_suspension_end', '-') or '-'} / 신주 상장 {sched.get('new_share_listing', '-') or '-'}",
        "",
    ]


_FOLLOWUP_CARD_TITLES = {
    "early_redemption": "자기사채 만기전취득",
    "issue_price_fixed": "유상증자 발행가액 확정",
    "subscription_result": "유상증자 청약결과",
}

_FOLLOWUP_FIELD_LABELS = (
    ("series", "회차"),
    ("bond_kind", "사채 종류"),
    ("issued_on", "발행일"),
    ("maturity_on", "만기일"),
    ("conversion_price_won", "주당 전환가액(원)"),
    ("face_total_won", "발행 권면총액(원)"),
    ("acquired_face_won", "취득 권면액(원)"),
    ("acquired_ratio_pct_derived", "발행총액 대비(%)"),
    ("acquisition_amount_won", "취득금액(원)"),
    ("remaining_face_won_after", "취득 후 잔액(원)"),
    ("acquisition_method", "취득방법"),
    ("funding_source", "취득자금 원천"),
    ("reason", "사유"),
    ("planned_handling", "향후 처리방법"),
    ("price_stage", "가액 구분"),
    ("final_price_won", "확정발행가(원)"),
    ("first_price_won", "1차 발행가(원)"),
    ("second_price_won", "2차 발행가(원)"),
    ("common_price_won", "보통주 발행가(원)"),
    ("planned_shares", "주식수(주)"),
    ("proceeds_won_derived", "조달금액(원)"),
    ("fixed_on", "확정일"),
    ("security_kind", "증권 종류"),
    ("subscriber", "청약대상자"),
    ("subscribed_on", "청약일"),
    ("subscribed_shares", "청약주식수(주)"),
    ("subscription_rate_pct", "청약률(%)"),
    ("forfeited_handling", "실권주 처리"),
)


def _render_followup_card(row: dict[str, Any]) -> list[str]:
    title = _FOLLOWUP_CARD_TITLES.get(row.get("type", ""), row.get("label", "후속 공시"))
    lines = [
        f"### {title} — {format_iso_date(row.get('rcept_dt', ''))} ({_link(row.get('rcept_no', ''))})",
        f"- 방향: **{row.get('direction', '-') or '-'}** / 공시명: {row.get('report_nm', '-') or '-'}",
    ]
    if row.get("parse_error"):
        lines.append(f"- ⚠️ 원문을 읽지 못했다 — {row['parse_error']}")
        lines.append("")
        return lines
    detail = row.get("details")
    if detail is None:
        lines.append("- ⚠️ 공시 목록에서만 확인했다 — 원문은 읽지 않았다(금액·회차 미확인).")
        lines.append("")
        return lines
    if detail.get("unparsed"):
        lines.append(f"- ⚠️ {detail.get('unparsed_note', '원문 서식이 맞지 않아 값을 읽지 못했다')}")
        if detail.get("summary_excerpt"):
            lines.append(f"- 원문 발췌: {detail['summary_excerpt']}")
        lines.append("")
        return lines
    for key, label in _FOLLOWUP_FIELD_LABELS:
        value = detail.get(key)
        if value is None or value == "":
            continue
        lines.append(f"- {label}: {value:,}" if isinstance(value, int) else f"- {label}: {value}")
    if detail.get("undersubscribed") is True:
        lines.append("- ⚠️ 청약률이 100%를 밑돌아 실권주가 났다 — 예정 주식수가 다 나가지 않았다.")
    lines.append("")
    return lines


def _render(payload: dict[str, Any]) -> str:
    """단일 통합 render — timeline + 4 type detail card 모두 노출."""
    data = payload.get("data", {})
    window = data.get("window", {})
    counts = data.get("event_count", {})
    usage = data.get("usage", {})
    lines = [
        f"# {data.get('canonical_name', payload.get('subject', ''))} 희석성 증권 발행",
        "",
        f"- 조사 구간: `{window.get('start_date', '')}` ~ `{window.get('end_date', '')}`",
        f"- 사건 수: 유상증자 {counts.get('rights_offering', 0)} / CB {counts.get('convertible_bond', 0)} / EB {counts.get('exchangeable_bond', 0)} / BW {counts.get('warrant_bond', 0)} / 감자 {counts.get('capital_reduction', 0)} / 발행 이후(되돌림·확정) {counts.get('followup', 0)}",
        "",
    ]
    if payload.get("warnings"):
        lines.append("## 유의사항")
        _cid = company_id_line(data)
        if _cid:
            lines.append(_cid)
        for warning in payload["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    timeline = data.get("events_timeline", [])
    if not timeline:
        if data.get("no_filing"):
            lines.append("## 공시 없음")
            lines.append("- 조사 구간 내 희석성 증권 발행 사건 없음 (정상 NO_FILING).")
        else:
            lines.append("조사 구간 내 희석성 증권 발행 사건 없음.")
        return "\n".join(lines)

    lines.extend([
        "## 사건 타임라인",
        "| 날짜 | 종류 | 방향 | 핵심 지표 | 원문 |",
        "|------|------|------|----------|------|",
    ])
    for ev in timeline:
        lines.append(
            f"| {ev.get('rcept_dt', '')} | {ev.get('event_label', '-')} | {ev.get('direction', '') or '-'} "
            f"| {ev.get('headline_metric', '-')} | {_link(ev.get('rcept_no', ''))} |"
        )
    lines.append("")

    # type별 detail card (있는 것만 노출)
    rights = data.get("rights_offering_events") or []
    if rights:
        lines.append("## 유상증자 결정 상세")
        for row in rights:
            lines.extend(_render_rights_card(row))

    cb = data.get("convertible_bond_events") or []
    if cb:
        lines.append("## 전환사채 발행결정 상세")
        for row in cb:
            lines.extend(_render_cb_card(row))

    eb = data.get("exchangeable_bond_events") or []
    if eb:
        lines.append("## 교환사채 발행결정 상세")
        for row in eb:
            lines.extend(_render_eb_card(row))

    bw = data.get("warrant_bond_events") or []
    if bw:
        lines.append("## 신주인수권부사채 발행결정 상세")
        for row in bw:
            lines.extend(_render_bw_card(row))

    cr = data.get("capital_reduction_events") or []
    if cr:
        lines.append("## 감자결정 상세")
        for row in cr:
            lines.extend(_render_capital_reduction_card(row))

    followup = data.get("followup_events") or []
    if followup:
        lines.append("## 발행 이후 — 되돌림·확정 상세")
        for row in followup:
            lines.extend(_render_followup_card(row))

    channel = data.get("equity_offering_channel") or {}
    if channel:
        lines.extend(_render_equity_channel(channel))

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def dilutive_issuance(
        company: str,
        start_date: str = "",
        end_date: str = "",
        section_chars: int = SECTION_CHARS_DEFAULT,
        format: str = "md",
    ) -> str:
        """desc: 희석성 증권 5종(유상증자/CB/EB/BW/감자) 결정 통합. 발행조건·잠재 희석률·**제3자배정 대상자 원문**·풋옵션·refixing + timeline + 발행공시(증권신고서·증권발행실적보고서) 목록.
        when: 행동주의 대응 자금조달, 경영권 방어 우호지분 형성, CB·BW 잠재 희석 평가, EB(자기주식 교환사채) 의결권 희석, **3자배정 대상자가 누구인지**. ownership_structure 교차 권장.
        rule: DART DS005 5 API 병렬 — piicDecsn/cvbdIsDecsn/exbdIsDecsn/bdwtIsDecsn/crDecsn. 기본 lookback 24개월.
          🔴 **배정 대상자 명단은 정형 API 에 없다** — 원문에만 있다. 제3자배정 유상증자 행에는 `third_party_allotment` 로 주요사항보고서 원문 대목(대상자별 선정경위·배정내역 / 대상자가 법인이면 그 최대출자자 / 제3자배정 근거·목적 / 조달자금 사용목적 / 기타 투자판단 참고사항)을 **그대로** 싣는다. `allottees` 6열 표는 **더한 것**이고 원문이 근거다 — 표가 비어 있어도 「대상자 미상」이 아니라 원문(`sections`)을 읽으라는 뜻이다. 최근 3건까지 싣고, 더 있으면 warnings 에 몇 건을 건너뛰었는지 적는다.
          `equity_offering_channel` 은 발행공시 C001(증권신고 — 지분증권) 목록이다. **인수인·자금의 사용목적·실제 배정 결과**는 주요사항보고서가 아니라 여기 있다. 목록은 전건, 본문은 건당 1.5만~2.6만자라 담지 않고 **가장 최근 증권발행실적보고서의 「유상증자 전후 주요주주 지분변동」 절만** 원문으로 싣는다. 나머지는 `viewer_url`·`evidence` tool 로.
          정정·철회로 구조화 응답이 비면 원본 공시 문서 파싱으로 교환가액·교환대상, 유상증자 원안(신주수·발행가·조달금액)을 복원 — 복원값은 original_plan 에만 들어가고 발행 물량 자리는 미확인으로 남는다(0으로 찍지 않는다). timeline 에는 자기사채 만기전취득·발행가액확정·청약결과가 direction 과 함께 선다.
        args:
          section_chars: 원문 대목 1개당 반환 상한(기본 4000, 500~40000). **대목이 잘렸으면(`truncated`/`truncation_note`) 올려서 다시 호출**하세요. 실측 표본에서 제3자배정 대상자 블록은 중앙값 219자·최대 2,982자라 기본값으로 대개 충분하고, 대상자가 여럿인 대형 출자전환이나 증권발행실적보고서 지분변동 표는 올려야 할 수 있습니다.
        ref: ownership_structure, treasury_share, corporate_restructuring, proxy_contest, evidence
        """
        payload = await build_dilutive_issuance_payload(
            company,
            scope="summary",  # service에서 항상 5종 모두 fetch
            start_date=start_date,
            end_date=end_date,
            section_chars=section_chars,
        )
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") == "ambiguous":
            return _render_ambiguous(payload)
        if payload.get("status") == "error":
            return _render_error(payload)
        return _render(payload)
