"""proxy_advise_before_meeting — 주총 전 의결권 행사 메모 (운용사 보고서 스타일)."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.proxy_advise import build_proxy_advise_payload
from open_proxy_mcp.services.contracts import as_pretty_json


# 사용자에게 노출되는 internal code → 한국어 자연어 라벨
_INDEPENDENCE_LABELS = {
    "independent": "독립적 (모든 sub-factor 충족)",
    "weak_concerns": "약한 우려 (1개 sub-factor 위반)",
    "concerns": "우려 (다수 sub-factor 위반)",
    "long_tenure_concerns": "장기연임 우려 (5년 룰 위반)",
    "no_data": "데이터 부족",
    "-": "-",
}

_DISQUALIFICATION_LABELS = {
    "clean": "결격사유 없음",
    "red_flag": "결격사유 발견",
    "not_evaluated": "평가 미실시",
    "no_data": "데이터 부족",
    "-": "-",
}

_AUDIT_HISTORY_LABELS = {
    "not_checked": "미검증 (옵션 비활성)",
    "no_red_flags": "이력 clean",
    "red_flag": "과거 회사 회계 risk 발견",
    "-": "-",
}

_FIVE_YEAR_LABELS = {
    "first_term_or_short": "첫 임기 또는 단기 (5년 룰 통과)",
    "long_tenure_concerns": "장기연임 (5년+, 독립성 훼손)",
    "no_data": "데이터 부족",
    "-": "-",
}

_SUB_FACTOR_LABELS = {
    "major_shareholder_relation": "최대주주 관계",
    "recent_3y_transactions": "최근 3년 거래",
    "recent_2y_employee": "최근 2년 직원 이력",
    "five_year_rule": "5년 임기 룰",
}

# 독립성 sub_factor result → 한글 (⚠️=독립성 우려). 화면 후보 평가 근거 노출용.
_INDEP_RESULT_KO = {
    "independent": "관계없음", "related": "⚠️특수관계 있음",
    "no_transactions": "거래 없음", "transactions_exist": "⚠️거래 있음",
    "outsider": "외부인", "former_employee": "⚠️최근 2년 내 직원",
    "first_term_or_short": "첫 임기/단기", "long_tenure_concerns": "⚠️장기연임(5년+)",
}


def _indep_evidence_lines(c: dict[str, Any]) -> list[str]:
    """후보 독립성 sub_factor별 결과 + 근거(경력 raw/관계 raw) 구조화 — 사외이사/감사위원."""
    role = c.get("role_type", "") or ""
    ind = c.get("independence") or {}
    subs = ind.get("sub_factors") or {}
    if not subs or not any(k in role for k in ("사외", "감사", "독립")):
        return []
    out = [f"- 독립성 근거 (종합: {_ind_label(ind.get('summary', '-'))}):"]
    for key in ("major_shareholder_relation", "recent_3y_transactions", "recent_2y_employee", "five_year_rule"):
        sf = subs.get(key) or {}
        if not sf:
            continue
        res = _INDEP_RESULT_KO.get(sf.get("result"), sf.get("result") or "-")
        ev = sf.get("evidence") or sf.get("raw")
        ev_str = f" — 근거: {str(ev).strip()[:70]}" if ev and str(ev).strip() not in ("-", "없음") else ""
        out.append(f"  - {_SUB_FACTOR_LABELS.get(key, key)}: {res}{ev_str}")
    return out


def _ind_label(code: str) -> str:
    return _INDEPENDENCE_LABELS.get(code, code)


def _disq_label(code: str) -> str:
    return _DISQUALIFICATION_LABELS.get(code, code)


def _audit_label(code: str) -> str:
    return _AUDIT_HISTORY_LABELS.get(code, code)


def _five_y_label(code: str) -> str:
    return _FIVE_YEAR_LABELS.get(code, code)


def _public_vote_style_label(label: str | None) -> str:
    if label == "open_proxy":
        return "open_proxy"
    return "internal_policy_variant"


def _render_error(payload: dict[str, Any]) -> str:
    lines = [f"# advise_vote: {payload.get('subject', '')}", "", "메모 작성 불가."]
    for w in payload.get("warnings", []):
        lines.append(f"- {w}")
    return "\n".join(lines)


def _render_ambiguous(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    lines = [
        f"# advise_vote: {data.get('query', payload.get('subject', ''))}",
        "",
        "회사 식별 모호.",
        "",
        "| 회사명 | corp_code |",
        "|------|-----------|",
    ]
    for c in data.get("candidates", []):
        lines.append(f"| {c.get('corp_name')} | `{c.get('corp_code')}` |")
    return "\n".join(lines)


def _render(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} 의결권 행사 메모 (사전)"]
    lines.append("")
    if data.get("scope_all_warning"):
        lines.append(f"> ⚠ **{data['scope_all_warning']}**")
        lines.append("")
    fin_ref = data.get("fin_reference_year")
    fin_ref_note = f" (재무 reference: FY{fin_ref})" if fin_ref else ""
    _mt = data.get("selected_meeting_type") or data.get("meeting_type")
    _mt_ko = {"annual": "정기", "extraordinary": "임시"}.get(_mt, _mt)
    lines.append(f"- 회차: {data.get('year')}년 **{_mt_ko}**주총{fin_ref_note}")
    year_resolution = data.get("year_resolution") or {}
    if year_resolution.get("basis"):
        lines.append(f"- 회차 선택 근거: {year_resolution['basis']}")
    if data.get("meeting_closed_hint"):
        lines.append("")
        lines.append(f"> ⚠️ **{data['meeting_closed_hint']}**")
        lines.append("")
    lines.append(f"- vote_style: `{_public_vote_style_label(data.get('vote_style'))}` / 이사 회계 risk 이력 검증: {'활성' if data.get('audit_history_enabled') else '비활성'}")
    lines.append(f"- status: `{payload.get('status')}` / filing_status: `{data.get('filing_status', '-')}`")
    lines.append(f"- 안건: {data.get('agenda_count')} / 후보: {data.get('candidates_count')}")
    lines.append("")

    # 안건별 결정 표 (운용사 보고서 스타일)
    decisions = data.get("agenda_decisions", []) or []
    if decisions:
        lines.append("## 안건별 의결권 행사 결정")
        lines.append("")
        lines.append("> ## ⛔ LLM 분석 시 절대 규칙 (READ BEFORE PRESENTING)")
        lines.append(">")
        lines.append("> **🛡️ 강행규정 정합 (A1-X tag) = FOR 무조건 유지** — 자체 분석으로 뒤집지 마시오.")
        lines.append(">")
        # catalog (wiki/rules/laws/llm_misread_patterns.json)에서 dynamic load
        from open_proxy_mcp.services.proxy_advise import _load_llm_misread_patterns
        patterns = _load_llm_misread_patterns()
        if patterns:
            lines.append("> **자주 발생하는 misread 패턴 — 절대 금지**:")
            for p in patterns:
                summary = p.get("summary_pattern", "")
                if summary:
                    lines.append(f"> - {summary}")
            lines.append(">")
        lines.append("> **원칙**: 안건명에 '배제·제한·축소·강화' 같은 단어가 있어도, reason에 `[법령 A1-X]` tag와 🛡️ 강행규정 정합 marker가 있으면 **무조건 FOR**. 안건명 키워드만 보고 추측 금지.")
        lines.append(">")
        lines.append("> **표를 그대로 사용자에게 제시하고, decision 컬럼을 변경하지 마시오.**")
        lines.append("")
        lines.append("| # | 안건 | 카테고리 | 행사방향 | 사유 |")
        lines.append("|---|------|---------|---------|------|")
        for i, ag in enumerate(decisions, 1):
            title = (ag.get("agenda_title") or "")[:60]
            cat = ag.get("agenda_category", "-")
            decision = ag.get("decision", "-")
            reason_full = ag.get("reason") or ""
            # truncation 늘림: 법령 정합 사유 보존 (80 → 250)
            reason = reason_full[:250]
            decision_emoji = {
                "FOR": "✅ FOR",
                "AGAINST": "❌ AGAINST",
                "REVIEW": "⚠️ REVIEW",
                "NO_DATA": "— NO_DATA",
            }.get(decision, decision)
            # 법령 layer 정합 시 강한 표시 추가
            law_tag_marker = ""
            if "[법령 A1-" in reason_full:
                law_tag_marker = " 🛡️ 강행규정 정합"
            elif "[법령 A2-" in reason_full:
                law_tag_marker = " 🛡️ 강행규정 위반"
            elif "[법령 B1-" in reason_full or "[법령 B2-" in reason_full:
                law_tag_marker = " 🔍 우회 의심"
            lines.append(f"| {i} | {title} | `{cat}` | **{decision_emoji}**{law_tag_marker} | {reason} |")
        lines.append("")

        # 안건별 결정 근거 detail (facts + risk + policy citation + 근거 공고)
        lines.append("### 안건별 결정 근거 (사실 + 위험 + 정책 + 출처)")
        lines.append("")
        for i, ag in enumerate(decisions, 1):
            title = (ag.get("agenda_title") or "")[:80]
            facts = ag.get("facts") or {}
            risks = ag.get("risk_factors") or []
            citation = ag.get("policy_citation") or "-"
            policy_basis = ag.get("policy_basis") or "-"
            rcept_no = ag.get("evidence_rcept_no")
            full_reason = ag.get("reason") or ""
            lines.append(f"**{i}. {title}** — {ag.get('decision','-')}")
            # reason full (표는 250자 truncate, detail은 full — 정관 본문 raw 포함)
            if full_reason:
                lines.append(f"- 사유 (full): {full_reason}")
            if facts:
                # dict/list 값(candidate_review_profile 등)은 raw 노출 금지 — Python 객체가
                # markdown에 통째로 박혀 None·내부 숫자가 새어 나온다. 스칼라만 표시,
                # 구조값은 항목 수로 요약(상세는 별도 후보 평가 섹션에 노출됨).
                def _fmt_fact(v: Any) -> str:
                    if isinstance(v, dict):
                        return f"(상세 {len(v)}항목 — 후보 평가 섹션 참조)"
                    if isinstance(v, list):
                        return f"[{len(v)}건]"
                    return str(v)
                fact_str = ", ".join(f"{k}={_fmt_fact(v)}" for k, v in facts.items())
                lines.append(f"- 사실(facts): {fact_str}")
            else:
                lines.append("- 사실(facts): (해당 카테고리에 정량 fact 없음)")
            if risks:
                lines.append(f"- 위험 신호: {', '.join(risks)}")
            else:
                lines.append("- 위험 신호: 없음")
            lines.append(f"- 정책 인용: {citation}")
            lines.append(f"- 적용 정책: {policy_basis}")
            if rcept_no:
                viewer = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
                lines.append(f"- 근거 공고: [주주총회소집공고 {rcept_no}]({viewer})")
            lines.append("")

    # 후보 평가 (사외이사/감사위원 위주)
    cands = data.get("candidates_evaluations", []) or []
    if cands:
        lines.append("## 이사/감사 후보 평가")
        lines.append("")
        lines.append("> **판단 framework** — 신임: ① 과거 다른 회사에서의 행적 ② 결격사유 ③ 전문성 ④ 독립성·충실성. 연임: ① 재직 기간 ② 재직 중 회사 운영 성과 (이 회사 데이터 활용).")
        lines.append("")
        lines.append("| 후보 | 직책 | 선임유형 | 임기 | 독립성 | 결격사유 | 이사 회계 risk 이력 | 비고 |")
        lines.append("|------|------|---------|------|--------|---------|-------|------|")
        for c in cands:
            indep_code = c.get("independence", {}).get("summary", "-")
            disq_code = c.get("disqualification", {}).get("summary", "-")
            audit_code = c.get("faithfulness", {}).get("audit_history_check", {}).get("summary", "-")
            action = c.get("agenda_action", "-") or "-"
            five_y_code = ((c.get("independence") or {}).get("sub_factors") or {}).get("five_year_rule", {}).get("result", "-")
            # 비고: independence concerns 시 어떤 sub-factor 위반했는지 한국어로
            note = ""
            if indep_code in ("concerns", "weak_concerns"):
                ind_subs = c.get("independence", {}).get("sub_factors", {})
                concern_kr = [
                    _SUB_FACTOR_LABELS.get(k, k)
                    for k, v in ind_subs.items()
                    if v.get("result") not in ("independent", "no_transactions", "outsider", "first_term_or_short")
                ]
                if concern_kr:
                    note = f"위반: {', '.join(concern_kr)}"
            lines.append(
                f"| {c.get('name', '?')} | {c.get('role_type', '-')} | {action} | {_five_y_label(five_y_code)} | "
                f"{_ind_label(indep_code)} | {_disq_label(disq_code)} | {_audit_label(audit_code)} | {note} |"
            )
        lines.append("")

        # 후보별 detail — 전문성 / 경력 / 과거 회사 행적 raw (framework 적용용)
        lines.append("### 후보별 raw (전문성·경력·추천 사유)")
        lines.append("")
        for c in cands:
            name = c.get("name", "?")
            role = c.get("role_type", "-")
            faith = c.get("faithfulness", {}) or {}
            main_job = faith.get("main_job") or "-"
            rec_reason = (faith.get("recommendation_reason_raw") or "").strip()
            careers = faith.get("career_company_groups") or []
            ah = faith.get("audit_history_check") or {}
            ah_red = ah.get("red_flags") or []

            lines.append(f"**{name}** ({role})")
            lines.append(f"- 주요 직책: {main_job}")
            if rec_reason:
                lines.append(f"- 추천 사유 (raw): {rec_reason[:240]}{'…' if len(rec_reason) > 240 else ''}")
            if careers:
                lines.append("- 경력:")
                for grp in careers[:6]:
                    co = grp.get("company", "?")
                    items = grp.get("items") or []
                    items_str = " / ".join(items[:3])
                    lines.append(f"  - {co} — {items_str}")
            # 독립성 4 sub_factor 결과 + 근거(경력 raw 등) — 사외이사/감사위원
            lines.extend(_indep_evidence_lines(c))
            if ah_red:
                lines.append(f"- 과거 회사 회계 risk 이력 (raw): {len(ah_red)}건 발견 — 본문 raw 메모 검토")
            # 사내이사 재직 중 성과 (ralph 260505) — 사내이사 + renewed에만 부착됨
            perf = c.get("performance") or {}
            if perf.get("classification"):
                cls = perf.get("classification", "n/a")  # 영문 키 — 이모지 매핑용
                cls_ko = perf.get("classification_ko") or cls  # 한글 표시
                cls_emoji = {"good": "🟢", "moderate": "🟡", "weak": "🟠", "bad": "🔴"}.get(cls, "")
                lines.append(f"- **재직 중 성과**: {cls_emoji} **{cls_ko}** (총점 {perf.get('total_score')}/12, 재직 {perf.get('tenure_period', '-')})")
                m = perf.get("matrix", {}) or {}
                roe = m.get("roe", {}) or {}
                lev = m.get("leverage", {}) or {}
                csr = m.get("csr", {}) or {}
                # avg가 None '값'으로 존재하면 .get(key, 0) default가 안 먹는다 → or 0 필수 (솔루엠 crash)
                lines.append(f"  - ROE: 평균 {roe.get('avg') or 0:.1f}% ({roe.get('avg_label')}) / 추세 {roe.get('trend_pp_per_year') or 0:+.2f}%p/년 ({roe.get('trend_label')})")
                lines.append(f"  - 부채비율: 평균 {lev.get('avg') or 0:.0f}% ({lev.get('avg_label')}) / 누적변화 {lev.get('delta_pp_total') or 0:+.0f}%p ({lev.get('trend_label')})")
                csr_avg = csr.get('avg_pct')
                csr_trend = csr.get('trend_pp_per_year')
                lines.append(f"  - CSR 환원율: 평균 {csr_avg:.1f}%" if csr_avg is not None else "  - CSR 환원율: 데이터 부족" )
                lines[-1] += f" ({csr.get('avg_label')}) / 추세 {csr_trend:+.1f}%p/년 ({csr.get('trend_label')})" if csr_trend is not None else f" ({csr.get('avg_label')})"
                if perf.get("capital_impairment_status") == "full":
                    lines.append(f"  - ⚠ 자본잠식 (ROE/부채 자동 저조)")
                om = perf.get("operating_margin")  # 본업 수익성 fact (점수 미반영) — ROE 왜곡 보완
                if om and om.get("avg_pct") is not None:
                    core = "본업 흑자" if om.get("core_profitable") else "⚠️본업 적자"
                    tr = om.get("trend_pp_per_year")
                    lines.append(
                        f"  - 영업이익률(참고, 점수 미반영): 평균 {om['avg_pct']:.1f}% ({core})"
                        + (f" / 추세 {tr:+.1f}%p/년" if tr is not None else "")
                    )
                os_ = perf.get("order_signal")
                # external_count 기준 — 계열 일감만 있는 회사(external 0)에서 '외부 수주 0건 0억원'
                # 군더더기 노출 방지(order_count는 계열 포함이라 0건 표시됨).
                if os_ and (os_.get("external_count") or os_.get("terminated_count")):
                    def _won_s(n: int) -> str:
                        n = n or 0
                        if not n:
                            return "-"
                        raw = f"{n:,}원"
                        if n >= 1_0000_0000_0000:
                            return f"{n/1_0000_0000_0000:.1f}조원 ({raw})"
                        return f"{n/1_0000_0000:,.0f}억원 ({raw})"
                    parts = []
                    if os_.get("external_count"):
                        mx = os_.get("max_revenue_ratio_pct")
                        parts.append(
                            f"외부 수주 {os_.get('external_count')}건 {_won_s(os_.get('external_total_amount_won'))}"
                            + (f"(매출대비 최대 {mx}%)" if mx else "")
                        )
                    if os_.get("terminated_count"):  # 해지 = 부정 시그널
                        tmx = os_.get("max_terminated_revenue_ratio_pct")
                        parts.append(
                            f"⚠️해지 {os_.get('terminated_count')}건 {_won_s(os_.get('terminated_total_amount_won'))}"
                            + (f"(매출대비 최대 {tmx}%)" if tmx else "")
                        )
                    if parts:  # 외부 수주·해지 둘 다 없으면(계열만) line 생략
                        lines.append(f"  - 수주(참고, 점수 미반영): " + " · ".join(parts))
                # 담당부문 성과 (260723 Phase 1 — 참고, 점수 미반영) — 부문장 출신 사내이사만
                seg = perf.get("segment_signal")
                if seg and seg.get("series"):
                    sr = seg["series"]
                    unit = (sr[-1].get("unit") or "").strip()

                    def _seg_v(v) -> str:
                        return f"{v:,.0f}" if isinstance(v, (int, float)) else "-"

                    span = " → ".join(
                        f"FY{r['fy']} 매출 {_seg_v(r.get('revenue'))}·영업이익 {_seg_v(r.get('profit'))}"
                        for r in sr
                    )
                    excluded = seg.get("excluded_years") or []
                    excl_note = (
                        f" · FY{'/'.join(str(y) for y in excluded)}는 부문표 정형 추출 저신뢰로 제외"
                        if excluded else ""
                    )
                    lines.append(
                        f"  - **담당부문 성과(참고, 점수 미반영)**: {seg.get('segment')} — {span}"
                        + (f" (단위: {unit})" if unit else "")
                        + excl_note
                    )
                    lines.append(
                        f"    - 매핑 근거: \"{seg.get('matched_from')}\" · 전사 매트릭스와 별개 참고 정보"
                        f" — 부문 재편 시 연도 간 불연속 가능 (회사 공시 기준)"
                    )
            lines.append("")

        # 부문 참고 fallback (260723) — 자동 매핑 실패/정형 저신뢰 시 회사 단위 1회 첨부
        seg_ref = data.get("segment_reference")
        if seg_ref:
            if seg_ref.get("kind") == "structured_table":
                lines.append(f"### 📊 부문표 전체 (FY{seg_ref.get('fiscal_year')} — 참고, 점수 미반영)")
                lines.append(f"> {seg_ref.get('note')}")
                lines.append("")
                unit_s = (seg_ref.get("unit") or "").strip()
                lines.append(f"| 부문 | 매출{f' ({unit_s})' if unit_s else ''} | 영업이익{f' ({unit_s})' if unit_s else ''} |")
                lines.append("|------|------|------|")
                for it in seg_ref.get("items") or []:
                    _rv = it.get("revenue")
                    _pf = it.get("profit")
                    lines.append(
                        f"| {it.get('name', '?')} "
                        f"| {f'{_rv:,.0f}' if isinstance(_rv, (int, float)) else '-'} "
                        f"| {f'{_pf:,.0f}' if isinstance(_pf, (int, float)) else '-'} |"
                    )
                lines.append("")
            elif seg_ref.get("kind") == "note_markdown":
                lines.append(f"### 📄 영업부문 주석 원문 (FY{seg_ref.get('fiscal_year')} — LLM 직접 검토, 점수 미반영)")
                lines.append(f"> {seg_ref.get('note')}")
                if seg_ref.get("truncated"):
                    lines.append(
                        f"> ✂️ 분량 초과로 앞부분만 발췌 (전체 {seg_ref.get('full_length'):,}자 중 "
                        f"{seg_ref.get('context_chars'):,}자). 전체가 필요하면 ① business_details("
                        f"fields=\"segments\", bsns_year=\"{seg_ref.get('fiscal_year')}\", reprt_code=\"11011\")로 "
                        f"직접 조회(권장, 콜 절약) 또는 ② segment_context_chars를 늘려(최대 30000) 재호출."
                    )
                lines.append("")
                lines.append(seg_ref.get("markdown") or "")
                lines.append("")

        # 회계 risk 이력 발견 detail (회사명 / 시점 / risk 유형 raw 노출)
        audit_history_detail = []
        for c in cands:
            rfs = c.get("faithfulness", {}).get("audit_history_check", {}).get("red_flags", []) or []
            for rf in rfs:
                audit_history_detail.append((c.get("name", "?"), rf))
        if audit_history_detail:
            lines.append("### 이사 회계 risk 이력 검증 — 과거 회사 회계 risk overlap (raw)")
            lines.append("> 사외이사 충실의무 단정 X — 사용자 판단 위임. 본 시점에 후보가 그 회사에 재직 중이었음을 의미.")
            lines.append("")
            lines.append("| 후보 | 과거 회사 | 재직 기간 | risk 유형 | 시점 | detail |")
            lines.append("|------|----------|----------|----------|------|--------|")
            for cand_name, rf in audit_history_detail:
                co = rf.get("company", "?")
                tenure = f"{rf.get('tenure_start_year')} ~ {rf.get('tenure_end_year') or '현재'}"
                for r in rf.get("red_flags", []):
                    rtype = r.get("type")
                    yr = r.get("year") or f"{r.get('year_from','?')}→{r.get('year_to','?')}"
                    detail = ""
                    if rtype == "non_clean_audit_opinion":
                        detail = r.get("opinion", "")
                    elif rtype == "capital_impairment_full":
                        detail = f"잠식률 {r.get('ratio_pct')}%"
                    elif rtype == "loss_continued_worsening":
                        detail = f"순이익 {r.get('ni_from'):,} → {r.get('ni_to'):,}"
                    elif rtype == "leverage_surge_op_worsening":
                        detail = f"부채 +{r.get('debt_growth_pct')}% / 영업이익 {r.get('op_from'):,} → {r.get('op_to'):,}"
                    lines.append(f"| {cand_name} | {co} | {tenure} | `{rtype}` | {yr} | {detail} |")
            lines.append("")

    # 회사 펀더멘털 요약 (참고)
    fin = data.get("financial_summary") or {}
    if fin:
        lines.append("## 회사 펀더멘털 (참고)")
        lines.append(f"- 매출액: {fin.get('revenue_krw') or '-'} / 영업이익: {fin.get('operating_profit_krw') or '-'}")
        lines.append(f"- ROE: {fin.get('roe_pct') or '-'}% / 부채비율: {fin.get('debt_ratio_pct') or '-'}%")
        lines.append(f"- 자본잠식 상태: {fin.get('capital_impairment_status') or '-'}")
        lines.append("")

    # Evidence
    refs = payload.get("evidence_refs", []) or []
    if refs:
        lines.append("## Evidence (근거)")
        for r in refs[:5]:
            url = r.get("viewer_url") or "-"
            lines.append(f"- {r.get('section', '-')}: [{r.get('rcept_no', '-')}]({url}) — {r.get('note', '')}")
        lines.append("")

    # 추가 분석 영역 — 짧게. 사용자가 자연스럽게 후속 질문 유도 (도구는 Claude가 알아서 매칭)
    decisions_local = data.get("agenda_decisions", []) or []
    has_director = any(ad.get("agenda_category") in ("director_election", "audit_committee_election") for ad in decisions_local)
    topics: list[str] = ["배당", "지분 구조·행동주의", "가치제고 plan", "운용사별 정책 비교", "재무 detail"]
    if has_director:
        topics.append("후보 회계 risk 이력 (`check_audit_history=True`)")

    lines.append("---")
    lines.append(f"_더 보고 싶은 영역: {' · '.join(topics)} — 이어서 물어보시면 영역별로 더 자세히 분석합니다._")

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def proxy_advise_before_meeting(
        company: str,
        year: int = 0,
        meeting_type: str = "annual",
        vote_style: str = "open_proxy",
        check_audit_history: bool = False,
        segment_context_chars: int = 8000,
        format: str = "md",
    ) -> str:
        """desc: 주총 **소집 전** 안건별 의결권 권고. 1회 호출로: 안건별 FOR/AGAINST/REVIEW/NO_DATA + facts + risk_factors + policy_citation + 근거 공고 + 후보 평가 + 재무/거버넌스 summary.
        ⛔ CRITICAL: 응답의 decision 컬럼은 한국 상법 강행규정 (A1/A2 tag 🛡️) + 운용사 정책 (vote_style) + Open Proxy Guideline 통합 결과. 사용자에게 **그대로** 제시 — 안건명 키워드(배제·제한·축소·강화)만 보고 자체 판단으로 변경 금지. 자주 misread: '집중투표 배제 조항 삭제' = FOR(의무화 정합), '의결권 제한 강화' = FOR(합산 3% 룰).
        when: 소집공고 후 ~ 주총 직전. 의결권 행사 결정 + 내부 보고. 사후 결과는 `shareholder_meeting_results`.
        rule: 운용사 의결권 행사 보고서 스타일. hard-fail(형사 처벌/사적 관계/동명이인) 자동 검증 가능 항목만 표기. soft-fail(후보 약력/정관 본문) raw 노출 — LLM 판단.
        vote_style: `open_proxy` (default — OPM 자체 가이드라인). 다른 옵션은 internal cross-reference용
        check_audit_history: True 시 후보 과거 회사 × 회계 risk overlap cross-check (+30s)
        meeting_type: `annual`(default) / `extraordinary` / `auto`
        year: 미지정(0) 시 최신 소집공고(12개월 lookback) 기준 회차 자동 선택 — 응답의 회차 선택 근거·정기/임시 확인. 이미 종료된 회차면 임시주총 확인 힌트 동봉. 특정 연도 분석은 year 명시.
        segment_context_chars: 부문장 출신 사내이사의 담당부문 매핑 실패·정형 추출 저신뢰 시 첨부되는 부문표 원문 발췌 길이(기본 8000, 최대 30000). 응답에 '앞부분만 발췌' 표시가 뜨면 이 값을 늘려 재호출하거나 — 더 싸게는 business_details(fields="segments", bsns_year, reprt_code)로 전체를 직접 조회.
        ref: shareholder_meeting_notice, financial_metrics, corp_gov_report, ownership_structure, proxy_contest, value_up, shareholder_meeting_results
        """
        payload = await build_proxy_advise_payload(
            company,
            year=year or None,
            meeting_type=meeting_type,
            vote_style=vote_style,
            scope="decisions",  # 단일 scope — 모든 specialized scope 폐지 (각 tool 직접 호출 권장)
            check_audit_history=check_audit_history,
            segment_context_chars=segment_context_chars,
        )
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") == "ambiguous":
            return _render_ambiguous(payload)
        if payload.get("status") == "error":
            return _render_error(payload)
        return _render(payload)
