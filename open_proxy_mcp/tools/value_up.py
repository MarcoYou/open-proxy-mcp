"""value_up public tool."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.tools._shared import company_id_line
from open_proxy_mcp.services.value_up import build_value_up_payload




# 섹션 태그는 엔진 내부 이름이다 — services.value_up 의 한글 매핑을 재사용한다(260728).
def _tag_ko(tag: str) -> str:
    from open_proxy_mcp.services.value_up import SECTION_LABELS_KO
    # 출처: services/value_up.py `_classify_value_up_item()` — plan · progress ·
    # pre_announcement · meta_amendment. 「정정공시」는 DART 에서 기재정정을 뜻하므로 오역이다
    # (본문이 스스로 "형식 재공시"라고 설명한다) — 계획 수정으로 오독된다(260728 QA 지적).
    extra = {"plan": "본계획", "progress": "이행현황", "pre_announcement": "예고",
             "meta_amendment": "형식 재공시", "meta_reference": "메타/참조", "-": "-"}
    return SECTION_LABELS_KO.get(tag) or extra.get(tag) or (tag.replace("_", " ") if tag else "-")

def _render_error(payload: dict[str, Any]) -> str:
    lines = [f"# value_up: {payload.get('subject', '')}", "", "밸류업 공시를 확정하지 못했다."]
    for warning in payload.get("warnings", []):
        lines.append(f"- {warning}")
    return "\n".join(lines)


def _render_ambiguous(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    lines = [f"# value_up: {data.get('query', payload.get('subject', ''))}", "", "회사 식별이 애매해 밸류업 공시를 자동 선택하지 않았다.", "", "| 회사명 | ticker | corp_code | company_id |", "|------|--------|-----------|------------|"]
    for item in data.get("candidates", []):
        lines.append(f"| {item['corp_name']} | `{item['ticker']}` | `{item['corp_code']}` | `{item['company_id']}` |")
    return "\n".join(lines)


def _render(payload: dict[str, Any], scope: str) -> str:
    data = payload.get("data", {})
    latest = data.get("latest", {})
    window = data.get("window", {})
    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} 밸류업", ""]
    _cid = company_id_line(data)
    if _cid:
        lines.append(_cid)
    if window:
        lines.append(f"- 조사 구간: `{window.get('start_date', '')}` ~ `{window.get('end_date', '')}`")
    lines.append("")
    if payload.get("warnings"):
        lines.append("## 유의사항")
        for warning in payload["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    if data.get("no_filing"):
        lines.extend([
            "## 공시 없음",
            "- 조사 구간 내 기업가치제고(밸류업) 공시 없음 (정상 NO_FILING).",
            "",
        ])

    if latest:
        lines.append("## 최신 공시")
        lines.append(f"- 공시일: {latest.get('disclosure_date', '-')}")
        lines.append(f"- 공시명: {latest.get('report_name', '-')}")
        if latest.get("category"):
            lines.append(f"- 카테고리: {_tag_ko(latest.get('category') or '')}")
        if latest.get("plan_title"):
            lines.append(f"- 계획서 명칭: {latest.get('plan_title')}")
        if latest.get("rcept_no"):
            lines.append(f"- 공시번호 {latest.get('rcept_no', '')}")
        if latest.get("acptno"):
            lines.append(f"- KIND acptno: `{latest.get('acptno', '')}`")

    latest_plan = data.get("latest_plan")
    if latest_plan:
        lines.append("")
        lines.append("## 본계획")
        lines.append(f"- 공시일: {latest_plan.get('disclosure_date', '-')}")
        lines.append(f"- 공시명: {latest_plan.get('report_name', '-')}")
        lines.append(f"- 카테고리: {_tag_ko(latest_plan.get('category') or '-')}")
        if latest_plan.get("plan_title"):
            lines.append(f"- 계획서 명칭: {latest_plan.get('plan_title')}")
        if latest_plan.get("rcept_no"):
            lines.append(f"- 공시번호 {latest_plan.get('rcept_no', '')}")
        if latest_plan.get("note"):
            lines.append(f"- 비고: {latest_plan.get('note')}")
    latest_status = data.get("latest_status")
    if latest_status:
        lines.append("")
        lines.append("## 최신 이행현황")
        lines.append(f"- 공시일: {latest_status.get('disclosure_date', '-')}")
        lines.append(f"- 공시명: {latest_status.get('report_name', '-')}")
        lines.append(f"- 카테고리: {_tag_ko(latest_status.get('category') or '-')}")
        if latest_status.get("plan_title"):
            lines.append(f"- 계획서 명칭: {latest_status.get('plan_title')}")
        if latest_status.get("rcept_no"):
            lines.append(f"- 공시번호 {latest_status.get('rcept_no', '')}")
        if latest_status.get("note"):
            lines.append(f"- 비고: {latest_status.get('note')}")

    latest_result = data.get("latest_result")
    if latest_result:
        lines.append("")
        lines.append("## 이행결과")
        lines.append(f"- 공시일: {latest_result.get('disclosure_date', '-')}")
        lines.append(f"- 공시명: {latest_result.get('report_name', '-')}")
        if latest_result.get("plan_title"):
            lines.append(f"- 계획서 명칭: {latest_result.get('plan_title')}")
        for section in latest_result.get("implementation_sections", [])[:5]:
            lines.append(f"- **{_tag_ko(section.get('tag', ''))}** {section.get('text', '')}")

    meta_amendment = data.get("meta_amendment")
    if meta_amendment:
        lines.append("")
        lines.append("## 메타/재공시")
        lines.append(f"- 공시일: {meta_amendment.get('disclosure_date', '-')}")
        lines.append(f"- 공시명: {meta_amendment.get('report_name', '-')}")
        lines.append(f"- 비고: {meta_amendment.get('note', '')}")

    if not latest_plan and not latest_status:
        diagnostic = data.get("search_diagnostics", {}).get("diagnostic_window", {})
        sample_filings = diagnostic.get("sample_filings", [])
        if sample_filings:
            lines.extend(["## 진단 구간에서 확인된 관련 공시", "| 소스 | 날짜 | 공시명 | 식별자 |", "|------|------|--------|--------|"])
            for item in sample_filings:
                filing_id = item.get("rcept_no") or item.get("acptno", "")
                lines.append(
                    f"| {item.get('source', '')} | {item.get('disclosure_date', '')} | {item.get('report_name', '')} | `{filing_id}` |"
                )

    if scope in {"summary", "timeline"}:
        lines.extend(["", "## 공시 타임라인", "| 날짜 | 공시명 | 제출인 | 공시번호 |", "|------|--------|--------|----------|"])
        for item in data.get("items", []):
            filing_id = item.get("rcept_no") or item.get("acptno", "")
            lines.append(f"| {item.get('disclosure_date', '')} | {item.get('report_name', '')} | {item.get('filer_name', '')} | `{filing_id}` |")

    # 수치 목표 ↔ 최신 실적 대조. **핵심 문장(원문)보다 위에 두되 원문을 대체하지 않는다.**
    target_rows = data.get("numeric_targets") or []
    unparsed = data.get("numeric_targets_unparsed") or []
    if target_rows or unparsed:
        lines.extend(["", "## 수치 목표 vs 최신 실적"])
    if target_rows:
        lines.extend([
            "목표는 회사 공시 원문에서 뽑았고, 실적은 **다른 도구가 이미 내는 값을 그대로** 가져왔다"
            "(재무비율=`financial_metrics` · PER/PBR·배당수익률=`price_multiple_data`). 새로 계산하지 않았다.",
            "",
            "| 지표 | 목표(원문) | 최신 실적 | 달성 | 실적 기준 |",
            "|------|-----------|-----------|------|-----------|",
        ])
        _mark = {"달성": "✅ 달성", "미달": "❌ 미달",
                 "판정 보류": "⚠️ 판정 보류", "대조 못 함": "— 대조 못 함"}
        for row in target_rows:
            unit = row.get("unit", "")
            actual = row.get("actual")
            actual_txt = f"{actual:,.2f}{unit}" if isinstance(actual, (int, float)) else "—"
            verdict = _mark.get(row.get("verdict", ""), row.get("verdict", ""))
            note = row.get("verdict_note") or ""
            if note:
                verdict = f"{verdict} ({note})"
            lines.append(
                f"| {row.get('metric_label', '')} | {row.get('target_text', '')} | {actual_txt} | "
                f"{verdict} | {row.get('actual_basis', '')} |")
        lines.extend([
            "",
            "> 목표가 「중장기」로 적힌 경우 단년 미달이 곧 미이행은 아니다. 배당 목표는 회사가 본문에서 "
            "지급 시점을 따로 밝히는 일이 있으니 아래 **핵심 문장(원문)**을 반드시 함께 읽어라.",
        ])
        caveats = [(r.get("metric_label", ""), r["caveat"]) for r in target_rows if r.get("caveat")]
        if caveats:
            lines.extend(["", "### 이 대조에서 조심할 것"])
            seen_caveat: set[str] = set()
            for label, text in caveats:
                if text in seen_caveat:
                    continue
                seen_caveat.add(text)
                lines.append(f"- **{label}**: {text}")
        lines.extend(["", "### 목표별 원문 (공시 본문 그대로)"])
        for row in target_rows:
            lines.append(f"- **{row.get('metric_label', '')}**: {row.get('source_text', '')}")
    if unparsed:
        lines.extend(["", "### 대조 못 한 목표 (원문 그대로)",
                      "수치를 정형으로 읽지 못했다. **없다는 뜻이 아니다** — 원문을 직접 읽고 판단해라."])
        for item in unparsed:
            lines.append(f"- **{item.get('metric_label', '')}**: {item.get('source_text', '')}")

    if scope in {"summary", "plan", "commitments"}:
        lines.extend(["", "## 핵심 문장 (회사 공시 원문)"])
        for item in data.get("highlights", []):
            lines.append(f"- {item}")

    sections = data.get("implementation_sections") or []
    if sections and scope in {"summary", "plan", "commitments"}:
        lines.extend(["", "## 이행 태그"])
        for section in sections[:12]:
            lines.append(f"- **{_tag_ko(section.get('tag', ''))}** {section.get('text', '')}")

    embedded = data.get("embedded_results") or []
    if embedded:
        lines.extend(["", "## 재공시 내 업데이트 결과"])
        for section in embedded[:8]:
            lines.append(f"- **{_tag_ko(section.get('tag', ''))}** {section.get('text', '')}")

    if scope == "plan":
        lines.extend(["", "## 원문 발췌", "```", data.get("latest_excerpt", "")[:1800], "```"])

    cross = data.get("treasury_cross_ref")
    if cross:
        lines.extend(["", "## 자사주 이행 교차참조 (최근 24개월)"])
        lines.append(f"- 자기주식 소각결정 공시: {cross.get('cancelation_decision_count_24m', 0)}건")
        lines.append(f"- 취득결정 공시: {cross.get('acquisition_count_24m', 0)}건 (소각 목적 {cross.get('acquisition_for_cancelation_count_24m', 0)}건)")
        amt = cross.get("acquisition_for_cancelation_amount_krw_24m", 0)
        if amt:
            lines.append(f"- 소각 목적 취득 총액: {amt:,}원")
        lines.append(f"- 신탁계약 체결: {cross.get('trust_contract_count_24m', 0)}건")
        lines.append("- 자기주식 취득·소각 상세는 자기주식 도구(treasury_share)로 따로 조회하시면 됩니다.")

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def value_up(
        company: str,
        scope: str = "summary",
        year: int = 0,
        start_date: str = "",
        end_date: str = "",
        format: str = "md",
    ) -> str:
        """desc: 기업가치제고계획(밸류업) 공시 + commitment 문장. 주주환원 **정책·미래 약속**. 자사주 소각 이행 교차참조 포함.
        when: 밸류업 계획, ROE/PBR/배당성향 목표, 자사주 소각 계획 등 미래 약속. 실제 배당은 `dividend`, 자사주 사실은 `treasury_share`.
        rule: DART I 밸류업 키워드 → 없으면 KIND 0184 fallback. 공시 카테고리: plan/progress/meta_amendment(고배당기업 재공시). 최신이 meta_amendment면 실계획 본문을 latest_plan으로 별도. summary/commitments에 24개월 자사주 이벤트 treasury_cross_ref 포함.
        scope: `summary` / `plan` 원문 발췌 / `commitments` 핵심 약속 + **수치 목표↔실적 대조표** + 이행 교차참조 / `timeline` 공시 이력
        commitments: 「목표 대비 어디까지 왔나」는 여기서 본다. `numeric_targets` 는 회사 원문에서 뽑은
          수치 목표(`target_text` = 원문 조각)에 최신 실적을 붙인 것이다. 실적값은 이 도구가 새로
          계산하지 않고 `financial_metrics`(재무비율) · `price_multiple_data`(PER/PBR·배당수익률)가
          내는 값을 그대로 가져온다 — **`actual_basis`(사업연도·연결·확정/정정·주가 기준일)를 반드시
          함께 인용해라.** 재무비율과 PBR 은 시점이 다르므로 같은 기준인 척 나란히 쓰지 마라.
          `verdict` 는 `달성`/`미달`/`판정 보류`(목표 문구에 이상·이하 방향이 없다)/`대조 못 함`(실적 미확보)이다.
          `numeric_targets_unparsed` 는 **지표는 언급됐는데 수치를 못 읽은 자리**이며 원문 조각이 담겨 있다 —
          「목표가 없다」로 읽지 마라. 표에 없는 약속(자사주 소각·IR 확대 등 비수치 약속)은
          `highlights`(원문 문장)에 그대로 있으니 표만 보고 결론내지 마라.
        ref: dividend, treasury_share, ownership_structure, financial_metrics, price_multiple_data, company, evidence
        """
        payload = await build_value_up_payload(
            company,
            scope=scope,
            year=year or None,
            start_date=start_date,
            end_date=end_date,
        )
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") == "ambiguous":
            return _render_ambiguous(payload)
        if payload.get("status") == "error":
            return _render_error(payload)
        return _render(payload, scope)
