"""proxy_advise_before_meeting — 주총 전 의결권 행사 메모 (운용사 보고서 스타일)."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.proxy_advise import build_proxy_advise_payload
from open_proxy_mcp.services.contracts import as_pretty_json


# 사용자에게 노출되는 internal code → 한국어 자연어 라벨
_INDEPENDENCE_LABELS = {
    "independent": "독립적 (세부 항목 모두 충족)",
    "weak_concerns": "약한 우려 (세부 항목 1개 위반)",
    "concerns": "우려 (세부 항목 다수 위반)",
    "long_tenure_concerns": "장기연임 우려 (5년 룰 위반)",
    "potential_long_tenure": "장기연임 가능성 (임기 확인 필요)",
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
    # 260813: `no_red_flags` 는 **유령 항목**이었다 — 코드 어디서도 만들지 않는 값을
    #   사전이 번역하고 있었고, 실제로 나오는 `clean`(director_evaluation.py:385·393·821)은
    #   사전에 없어 **영문 그대로 화면에 찍혔다**. 사전은 producer 를 읽고 만든다.
    "clean": "해당 이력 없음",
    "no_red_flags": "해당 이력 없음",   # 옛 이름 — 외부 저장분 호환용으로만 남긴다
    "red_flag": "과거 회사 회계 위험 발견",
    "-": "-",
}

#: 판정 enum → 화면 표기. **한 응답에서 판정을 두 이름으로 부르지 않기 위해** 모듈 상수다.
#: 260813: 표는 이 사전을 쓰는데 상세 절(:392)은 `FOR` 를 그대로 찍고 있었다 — 같은 판정이
#: 한 문서에서 「✅ 찬성」과 `FOR` 두 모습으로 나가면 읽는 쪽이 다른 것으로 읽는다.
#: payload 의 `decision` 필드는 FOR/AGAINST 그대로라 기계 소비자는 영향 없다(260729 지적).
_DECISION_KO = {
    "FOR": "✅ 찬성",
    "AGAINST": "❌ 반대",
    "REVIEW": "⚠️ 검토 필요",
    "NO_DATA": "— 판단 보류(자료 부족)",
    "NO_VOTE": "🚫 표결없음",          # 상법 §449조의2 보고사항 등 — 표결 자체가 없다
}

#: 회계 위험 이력의 **유형** 코드 → 한글. `_audit_history` 표의 「유형」 칸에
#: `non_clean_audit_opinion` 처럼 영문이 그대로 나가던 자리다.
#: director_evaluation.py:654·662·682·705 가 내는 값 **전부**(4종).
_AUDIT_RISK_TYPE_KO = {
    "non_clean_audit_opinion": "감사의견 비적정",
    "capital_impairment_full": "완전 자본잠식",
    "loss_continued_worsening": "적자 지속·악화",
    "leverage_surge_op_worsening": "부채 급증 + 영업이익 악화",
}

_FIVE_YEAR_LABELS = {
    "first_term_or_short": "첫 임기 또는 단기 (5년 룰 통과)",
    "long_tenure_concerns": "장기연임 (5년+, 독립성 훼손)",
    "potential_long_tenure": "장기연임 가능성 (임기 확인 필요)",
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
    # 260730: 정형(임원현황)이 「이 회사 상근 임원」이라고 말하는 경우 — 단정하지 않고 검토로
    "roster_says_fulltime_insider": "⚠️정형 데이터는 이 회사 상근 임원으로 기재 — 확인 필요",
    "first_term_or_short": "첫 임기/단기", "long_tenure_concerns": "⚠️장기연임(5년+)",
    # 25사 스윕에서 남아 있던 값 — 사전에 없으면 영문 코드가 그대로 화면에 나온다(260728)
    "potential_long_tenure": "장기연임 가능성(임기 확인 필요)",
    "no_match": "일치 항목 없음", "ambiguous": "판별 불가",
    # 결격 sub_factor — services/director_evaluation.py 가 result 로 뱉는다(354·383행).
    # 사전 감사(producer→사전 방향)에서 발견, 25사 스윕엔 해당 후보가 없어 안 잡혔다(260728).
    "minor": "⚠️미성년", "adult": "성년",
    "red_flag": "⚠️결격 신호 있음", "clean": "결격사유 없음",
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
        # 정형 데이터가 소집공고와 다르게 말하면 그 사실을 그대로 보여준다(단정하지 않는다).
        rx = sf.get("roster_cross_check")
        if rx:
            desc = " · ".join(x for x in (rx.get("director_type"), rx.get("position"),
                                          rx.get("full_time")) if x)
            out.append(f"    · {rx.get('source')} 기재: {desc}"
                       + (f" · 담당 {rx['duty'][:40]}" if rx.get("duty") else ""))
            out.append(f"    · {rx.get('note')}")
    return out


def _ind_label(code: str) -> str:
    return _INDEPENDENCE_LABELS.get(code, code)


def _disq_label(code: str) -> str:
    return _DISQUALIFICATION_LABELS.get(code, code)


def _audit_label(code: str) -> str:
    return _AUDIT_HISTORY_LABELS.get(code, code)


def _five_y_label(code: str) -> str:
    return _FIVE_YEAR_LABELS.get(code, code)


# facts 는 엔진 내부 필드명이다. 사람이 읽는 문서에 `fy_current_revenue_krw` 가 그대로 나오면
# 안 된다(260728 사용자 지적). 라벨 사전으로 옮기고, 못 옮긴 키는 아래 _humanize 가 최소한
# 영문 티를 걷어낸다. 새 fact 를 추가하면 여기에도 한 줄 추가할 것.
_FACT_LABEL: dict[str, str] = {
    # 재무제표 승인
    "audit_opinion": "감사의견", "capital_impairment_status": "자본잠식",
    "capital_impairment_ratio_pct": "자본잠식률(%)", "net_income_krw": "당기순이익(지배주주 귀속)",
    "net_income_yoy_pct": "순이익 증감률(%)", "accruals_gap_pct": "발생액 괴리(%)",
    "cfo_to_op_ratio": "영업현금흐름/영업이익", "interest_coverage_ratio": "이자보상배율",
    "fcf_krw": "잉여현금흐름", "dividend_to_fcf_pct": "배당/잉여현금흐름(%)",
    "fy_current_net_income_krw": "당기 순이익(총액)", "fy_prior_net_income_krw": "전기 순이익(총액)",
    "fy_current_revenue_krw": "당기 매출액", "fy_prior_revenue_krw": "전기 매출액",
    "fy_current_operating_profit_krw": "당기 영업이익",
    "fy_prior_operating_profit_krw": "전기 영업이익",
    "fy_current_total_assets_krw": "당기 자산총계",
    "fy_current_total_liabilities_krw": "당기 부채총계",
    "fy_current_total_equity_krw": "당기 자본총계",
    "fy_prior_net_income_krw_dart": "전기 순이익(지배주주 귀속)",
    "fy_raw_extraction_status": "본문 추출 상태", "fy_raw_scope": "본문 추출 범위",
    "fy_raw_skipped_currency": "본문 수치 미사용 사유(외화 표시)",
    "fy_raw_scope_mixed": "본문 수치 출처가 섞임(비율 계산 주의)",
    "fy_raw_rejected_accounts": "순이익 계정 불일치로 폐기",
    "fy_raw_cross_check": "본문↔확정 재무제표 검산",
    # 승인 대상 연도의 확정치 — 주총 시점에 사업보고서가 이미 나온 경우에만 붙는다.
    "fy_current_confirmed_year": "확정(A) 사업연도",
    "fy_current_revenue_krw_confirmed": "매출 (확정 A)",
    "fy_current_operating_profit_krw_confirmed": "영업이익 (확정 A)",
    "fy_current_net_income_krw_confirmed": "당기순이익 (확정 A, 지배주주 귀속)",
    "fy_current_total_equity_krw_confirmed": "자본총계 (확정 A)",
    "fy_provisional_vs_confirmed": "잠정(P) ↔ 확정(A) 대조",
    # 배당
    "payout_ratio_pct": "배당성향(%)", "payout_ratio_band": "배당성향 구간",
    # 보수한도
    "limit_krw": "이번 한도", "prior_limit_krw": "전기 한도", "prior_paid_krw": "전기 실지급",
    "increase_rate_pct": "한도 증가율(%)", "increase_rate_band": "증가율 구간",
    "director_count": "이사 수", "director_per_person_limit_krw": "1인당 한도",
    "audit_total_limit_krw": "감사 한도 총액", "audit_prior_limit_krw": "감사 전기 한도",
    "audit_prior_paid_krw": "감사 전기 실지급", "audit_count": "감사 수",
    "audit_per_person_krw": "감사 1인당", "audit_per_person_band": "감사 1인당 구간",
    "audit_increase_rate_pct": "감사 한도 증가율(%)", "audit_increase_rate_band": "감사 증가율 구간",
    "retirement_multiplier_evidence": "퇴직금 배수 근거",
    "retirement_target_expansion": "퇴직금 지급대상 확대",
    # 이사 선임
    "candidate_name": "후보자", "role_type": "직위", "appointment_type": "선임 유형",
    "tenure_status": "임기 상태", "this_company_since": "당사 재직 시작",
    "total_candidates": "후보 수", "disqualified_count": "결격 후보 수",
    "disqualification": "결격사유", "independence": "독립성",
    "concurrent_outside_positions": "겸직 수", "concurrent_summary": "겸직 요약",
    "candidate_summary": "후보 요약", "candidate_review_profile": "후보 상세",
    "audit_history_check": "회계 위험 이력 확인", "composition": "이사회 구성",
    # 정관변경·기타
    "amendments_count": "변경 조항 수", "amendments_sample": "변경 조항 예시",
    "agenda_action": "안건 성격", "cumulative_voting_threshold": "집중투표 기준",
    "treasury_pct": "자기주식 비율(%)", "treasury_pct_band": "자기주식 구간",
    "related_total_pct": "특수관계인 합계(%)", "active_signal_count": "행동주의 신호 수",
    "parsing_quality": "파싱 품질", "raw_text_fallback": "원문 폴백 사용",
    "law_detail": "조항 상세", "appointment_breakdown": "선임 유형 내역",
    "utilization_rate_pct": "한도 소진율(%)", "utilization_rate_band": "소진율 구간",
}
# 값이 enum 인 것들 — `not_checked` 같은 게 그대로 나가면 안 된다
_FACT_VALUE: dict[str, str] = {
    "normal": "없음", "partial": "부분", "full": "완전",
    "not_checked": "미확인", "checked": "확인함", "skipped": "생략",
    "first_term_or_short": "첫 임기 또는 단기", 
    "reappointment": "재선임", "new": "신규 선임", "inside": "사내이사", "outside": "사외이사",
    "audit_committee": "감사위원", "success": "성공", 
    "failed": "실패", "none": "없음", "unknown": "미상",
    "case_by_case": "사안별 판단", "mainstream": "일반 기준", "conservative": "보수적 기준",
    # 구간(band) — 엔진이 임계로 나눈 결과. 숫자 없이 영문 코드만 보이면 아무 뜻도 전달되지 않는다.
    "low_under_5": "5% 미만", "low_under_30": "30% 미만", "low_under_50m": "5천만원 미만",
    "mid_30_to_70": "30~70%", "high_80_to_150": "80~150%", "high_over_10": "10% 초과",
    "high_over_300m": "3억원 초과", "very_high_over_200": "200% 초과",
    "small_or_flat": "소폭 또는 동결", "large_increase": "큰 폭 인상",
    "very_large_increase": "매우 큰 폭 인상",
    "low_confidence": "신뢰도 낮음", "low_fallback_to_raw": "원문으로 대체",
    # 이사 후보 상태
    "renewed": "재선임", "independent": "독립적", "clean": "결격사유 없음",
    "concerns": "우려 있음", "weak": "부진", "strong": "우수",
    "single_position": "겸직 1곳",
    # 25사 라이브 스윕에서 남아 있던 것 — 코드 값 목록에서 전수로 뽑아 채웠다(260728)
    "borderline_150_to_200": "150~200%(경계)", "borderline_50m_to_100m": "5천만~1억원(경계)",
    "normal_70_to_100": "70~100%", "ordinary_under_80": "80% 미만",
    "notable_5_to_10": "5~10%", "sufficient_100m_to_300m": "1억~3억원",
    "moderate_increase": "완만한 인상",
    "long_tenure_concerns": "장기 재직 우려", "potential_long_tenure": "장기 재직 가능성",
    "weak_concerns": "약한 우려", "concerns_concurrent": "겸직 우려",
    "strong_concerns_concurrent": "겸직 우려 큼",
    "strong_review": "정밀 검토 필요", "strong_review_signal": "정밀 검토 신호",
    "low_attendance": "출석률 낮음", "mid_term_resigned": "임기 중 사임",
    "no_division_career": "담당부문 경력 없음", "no_match": "일치 항목 없음",
    "no_agenda": "안건 없음", "no_data": "자료 없음", "no_filing": "공시 없음",
    "potential_long_tenure": "장기연임 가능성", "ambiguous": "판별 불가", "over_100": "100% 초과",
}


def _fact_label(key: str) -> str:
    if key in _FACT_LABEL:
        return _FACT_LABEL[key]
    # 사전에 없는 키 — 최소한 영문 스네이크 티는 걷어낸다
    k = key.replace("_krw", "(원)").replace("_pct", "(%)").replace("_", " ")
    return k


def _fact_value(key: str, v) -> str:
    if isinstance(v, bool):
        return "예" if v else "아니오"
    if isinstance(v, dict):
        return f"{len(v)}항목 (아래 상세 참조)"
    if isinstance(v, list):
        return f"{len(v)}건"
    if v is None:
        return "-"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        # `_krw` 가 키 끝이 아닐 수 있다(fy_prior_net_income_krw_dart) — 포함 여부로 본다
        if "_krw" in key:
            return _won(v)
        if isinstance(v, float):
            return f"{v:,.2f}".rstrip("0").rstrip(".")     # 7.6487 → 7.65
        # 연도·개수에 천단위 쉼표를 찍으면 「2,018년」이 된다 — 큰 수만 구분자를 넣는다
        return f"{v:,}" if abs(v) >= 10_000 else str(v)
    sv = str(v)
    return _FACT_VALUE.get(sv, sv)


def _one_line(text: str, limit: int) -> str:
    """표 셀용 한 줄 요약 — 줄바꿈·파이프 제거 후 자른다. 전문은 근거 절이 보여준다."""
    head = (text or "").split("\n", 1)[0].replace("|", "／").strip()
    if len(head) <= limit:
        return head
    cut = head[:limit]
    # 괄호 안에서 끊기면 문장이 이상해진다 — 마지막 여는 괄호 앞에서 자른다
    for op, cl in (("(", ")"), ("（", "）")):
        if cut.count(op) > cut.count(cl):
            cut = cut[:cut.rfind(op)].rstrip(" ·-—,")
    return cut + "…"


def _won(v) -> str:
    """48916104000000 → 48조 9,161억. 사람이 읽는 자리에 원 단위 숫자를 그대로 두지 않는다."""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return "-"
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1_0000_0000_0000:
        jo, rest = divmod(n, 1_0000_0000_0000)
        eok = rest // 1_0000_0000
        return f"{sign}{jo}조" + (f" {eok:,}억" if eok else "") + "원"
    if n >= 1_0000_0000:
        return f"{sign}{n // 1_0000_0000:,}억원"
    if n >= 1_0000:
        return f"{sign}{n // 1_0000:,}만원"
    return f"{sign}{n:,}원"


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
    fin_ref_note = f" · 재무 분석 기준: {fin_ref}사업연도" if fin_ref else ""
    _mt = data.get("selected_meeting_type") or data.get("meeting_type")
    _mt_ko = {"annual": "정기", "extraordinary": "임시"}.get(_mt, _mt)
    # 값이 없으면 「None주총」이 찍힌다 — 모르면 종류를 말하지 않는다.
    _head = f"{_mt_ko}주총" if _mt_ko else "주주총회"
    lines.append(f"- 회차: {data.get('year')}년 **{_head}**{fin_ref_note}")
    year_resolution = data.get("year_resolution") or {}
    if year_resolution.get("basis"):
        lines.append(f"- 회차 선택 근거: {year_resolution['basis']}")
    if data.get("meeting_closed_hint"):
        lines.append("")
        lines.append(f"> {data['meeting_closed_hint']}")
        lines.append("")
    lines.append(f"- 안건 {data.get('agenda_count')}건 · 이사 후보 {data.get('candidates_count')}명")
    lines.append("")

    # 안건별 결정 표 (운용사 보고서 스타일)
    decisions = data.get("agenda_decisions", []) or []
    if decisions:
        lines.append("## 안건별 의결권 행사 결정")
        lines.append("")
        # LLM 지시(강행규정 정합을 뒤집지 말 것)는 tool docstring 에 둔다 — 모델은 호출 전에
        # 그걸 읽는다. 산출물에 섞으면 사람이 자기 앞으로 온 금지문을 읽게 된다(260728 사용자 지적).
        lines.append("> **✅ 찬성 / ❌ 반대**는 정책·법령에 비추어 판정이 선 것이고, "
                     "**⚠️ 검토 필요**는 판정을 보류한 것입니다 — 아래 「안건별 결정 근거」의 "
                     "사실·위험 신호를 읽고 직접 정하세요. 근거가 사실과 다르면 그 판정은 "
                     "쓰지 마시고 원문을 확인하시면 됩니다.")
        lines.append("")
        lines.append("| # | 안건 | 행사방향 | 사유 |")
        lines.append("|---|------|---------|------|")
        for i, ag in enumerate(decisions, 1):
            title = (ag.get("agenda_title") or "")[:60]
            decision = ag.get("decision", "-")
            reason_full = ag.get("reason") or ""
            # 표 셀에는 한 줄만 — 줄바꿈이 들어가면 그 지점에서 마크다운 표가 무너진다
            # (정관 원문·📋 조항 상세가 셀에 통째로 들어가 있었다). 전문은 아래 근거 절에 그대로 있다.
            reason = _one_line(reason_full, 160)
            decision_emoji = _DECISION_KO.get(decision, decision)
            # 법령 layer 정합 시 강한 표시 추가
            # 사유 문자열 파싱이 아니라 필드로 — 사유에서 내부 ID 를 뺐다(260728)
            _ll = ag.get("law_layer_id") or ""
            law_tag_marker = ""
            if _ll.startswith("A1-"):
                law_tag_marker = " 🛡️ 강행규정 정합"
            elif _ll.startswith("A2-"):
                law_tag_marker = " 🛡️ 강행규정 위반"
            elif _ll.startswith("B1-") or _ll.startswith("B2-"):
                law_tag_marker = " 🔍 우회 의심"
            lines.append(f"| {i} | {title} | **{decision_emoji}**{law_tag_marker} | {reason} |")
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
            # 260813: 표에는 「✅ 찬성」이라 써 놓고 여기선 `FOR` 를 다시 찍고 있었다.
            #   같은 판정을 한 응답에서 두 이름으로 부르면 읽는 쪽이 다른 것으로 읽는다.
            lines.append(f"**{i}. {title}** — {_DECISION_KO.get(ag.get('decision'), ag.get('decision') or '-')}")
            # reason full (표는 250자 truncate, detail은 full — 정관 본문 raw 포함)
            if full_reason:
                lines.append(f"- 사유: {full_reason}")
            if facts:
                # dict/list 값(candidate_review_profile 등)은 raw 노출 금지 — Python 객체가
                # markdown에 통째로 박혀 None·내부 숫자가 새어 나온다. 스칼라만 표시,
                # 구조값은 항목 수로 요약(상세는 별도 후보 평가 섹션에 노출됨).
                fact_str = " · ".join(f"{_fact_label(k)} {_fact_value(k, v)}"
                                      for k, v in facts.items())
                lines.append(f"- 사실: {fact_str}")
            else:
                # 「이 안건 유형에는 수치가 없다」는 **유형 단위 단정**이었다. 합병 안건이 그 문장을
                # 달고 나갔는데, 바로 윗줄 사유는 「합병비율의 산정근거와 외부평가기관 의견을
                # 확인하라」고 지시한다 — 있다고 말하면서 없다고 쓴 셈이고, 읽는 사람은 원문을 열
                # 이유를 잃는다(SK이노베이션 SK E&S 흡수합병 실측). **못 뽑았다고 말한다.**
                lines.append("- 사실: 이 안건에서 정량 수치를 추출하지 못했습니다 — 원문을 확인하세요")
            if risks:
                lines.append(f"- 위험 신호: {', '.join(risks)}")
            else:
                # 「없음」은 검사해서 없다는 뜻이다. 판정이 서지 않은 안건에서는 검사한 적이 없다.
                lines.append("- 위험 신호: 확인된 항목 없음"
                             if ag.get("decision") in ("FOR", "AGAINST")
                             else "- 위험 신호: 이 경로에서는 위험 신호를 판정하지 않았습니다")
            lines.append(f"- 정책 인용: {citation}")
            lines.append(f"- 적용 정책: {policy_basis}")
            if rcept_no:
                viewer = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
                lines.append(f"- 근거 공고: [주주총회소집공고 {rcept_no}]({viewer})")
            # 내부 코드번호는 비노출 (payload source_section.section_code로만 — 운영자용)
            src = ag.get("source_section")
            if src and src.get("section_title"):
                lines.append(
                    f"- 근거 위치: 소집공고 **§{src['section_title']}**"
                    f" — 뷰어 좌측 목차에서 해당 절을 열면 이 안건의 원문"
                )
            if ag.get("classification_note"):
                lines.append(f"- 분류 검증: {ag['classification_note']}")
            if ag.get("source_excerpt"):
                lines.append("- 해당 절 원문 발췌 (LLM 직접 검토):")
                lines.append("")
                lines.append(ag["source_excerpt"])
                lines.append("")
            lines.append("")

    # 후보 평가 (사외이사/감사위원 위주)
    cands = data.get("candidates_evaluations", []) or []
    if cands:
        lines.append("## 이사/감사 후보 평가")
        lines.append("")
        lines.append("> **판단 framework** — 신임: ① 과거 다른 회사에서의 행적 ② 결격사유 ③ 전문성 ④ 독립성·충실성. 연임: ① 재직 기간 ② 재직 중 회사 운영 성과 (이 회사 데이터 활용).")
        lines.append("")
        lines.append("| 후보 | 직책 | 선임유형 | 임기 | 독립성 | 결격사유 | 이사 회계 위험 이력 | 비고 |")
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
        lines.append("### 후보별 원문 (전문성·경력·추천 사유)")
        lines.append("")
        for c in cands:
            name = c.get("name", "?")
            role = c.get("role_type", "-")
            faith = c.get("faithfulness", {}) or {}
            main_job = faith.get("main_job") or "-"
            rec_reason = (faith.get("recommendation_reason_raw") or "").strip()
            careers = faith.get("career_raw") or []
            ah = faith.get("audit_history_check") or {}
            ah_red = ah.get("red_flags") or []

            lines.append(f"**{name}** ({role})")
            # 후보자 표와 안건 제목이 직위를 다르게 밝힌 경우 — 판정하지 않고 사실만 알린다.
            # 실측 12명(캐시 소집공고 1,399명 중). 직위가 갈리면 독립성 검증 적용 여부가 달라진다.
            rtc = c.get("role_type_conflict")
            if isinstance(rtc, dict):
                lines.append(
                    f"- ⚠️ 직위 표기 불일치: 후보자 표「{rtc.get('role_type')}」 vs "
                    f"안건 제목「{rtc.get('declared_role')}」 — {rtc.get('note')}")
            lines.append(f"- 주요 직책: {main_job}")
            # 정형 직위 — 주된직업(자유기재)과 별개로 이 회사 현재 직위를 정형으로 보여준다.
            # 실측 81.1%에서 주된직업에 없는 정보를 담고 있었다.
            # 출처 표기 규칙: **소집공고는 기본값이라 라벨을 안 붙이고, 다른 문서에서 온 것만
            # 밝힌다.** 안 밝히면 읽는 쪽이 전부 공고 내용이라고 읽는다(이 도구는 공고 분석이다).
            _apt0 = c.get("appointment_type") or {}
            _src0 = (_apt0.get("board_tenure_source") or {}).get("source") or "직전 정기보고서"
            if isinstance(_apt0, dict) and _apt0.get("roster_position"):
                lines.append(f"- 직위 ({_src0}): {_apt0['roster_position']}")
            # 임기 만료일 — 정형(임원현황) 값. 「이 사람 임기가 언제 끝나나」는 스튜어드십
            # 실무의 기본 정보인데 지금까지 받아만 오고 안 보여줬다(등기 행 96.3% 채움).
            _apt = c.get("appointment_type") or {}
            # 직전 보고서 기준 위원회 소속 — 감사위원 후보가 이미 그 위원회에 있었나
            if isinstance(_apt, dict) and _apt.get("roster_committees"):
                lines.append(f"- 위원회 ({_src0}): {' · '.join(_apt['roster_committees'])}")
            if isinstance(_apt, dict) and _apt.get("term_end_on"):
                _mark = " · 이번 회차 만료(재선임 대상)" if _apt.get("term_expiring_this_meeting") else ""
                lines.append(f"- 임기 만료 ({_src0}): {_apt['term_end_on']}{_mark}")
            if rec_reason:
                _shared = " (구간 공통 문면 — 이 후보 것이라고 확정하지 못함)" \
                    if faith.get("recommendation_reason_shared") else ""
                lines.append(
                    f"- 추천 사유{_shared}: {rec_reason[:240]}"
                    f"{'…' if len(rec_reason) > 240 else ''}")
            _cfr = faith.get("career_from_roster")
            if not careers and isinstance(_cfr, dict):
                lines.append(f"- 경력 ({_cfr.get('source')} 주요경력 — 소집공고에 세부경력 없음):")
                lines.append(f"  - {str(_cfr.get('main_career'))[:400]}")
                lines.append(f"  - ⓘ {_cfr.get('note')}")
            _unpaired = faith.get("career_period_unpaired")
            if careers and _unpaired:
                # 기간이 항목별로 안 갈렸다 — 짝을 지어 찍으면 없는 기간을 사실로 내보낸다.
                # 두 칸을 원문 그대로 싣고 대응은 읽는 쪽에 맡긴다(실측 4.8%).
                lines.append("- 경력 (소집공고 세부경력 원문 — 기간·내용 대응이 원문에 없어 "
                             "두 칸을 그대로 싣습니다):")
                lines.append(f"  - 기간: {_unpaired[:300]}")
                # 내용도 원문 셀을 쓴다 — 항목 분할이 회사명 중간을 자른다
                _craw = faith.get("career_content_raw") or " · ".join(
                    (it.get("content") or "").strip() for it in careers[:12])
                lines.append(f"  - 내용: {_craw[:700]}")
            elif careers:
                # 소집공고 표 그대로(기간 | 내용). 쪼개서 보여주지 않는다 —
                # 회사/직위 분리가 후보 17%에서 깨져 「…공학부 부」/「교수」처럼 단어를 찢고,
                # 분량도 원문의 2배였다(260729 실측 2,284명).
                lines.append("- 경력 (소집공고 세부경력 원문):")
                for item in careers[:8]:
                    period = item.get("period") or ""
                    content = item.get("content") or ""
                    lines.append(f"  - {period} | {content}" if period else f"  - {content}")
                if len(careers) > 8:
                    lines.append(f"  - … 외 {len(careers) - 8}건")
                # **폴백 체인** — 짝이 맞아 보여도 잘 쪼갰다는 보장은 없다.
                # 의심 신호가 있으면 원문 두 칸을 함께 싣고 읽는 쪽이 대조하게 한다
                # (실측 1,211명 중 13.0%: 기간 미대응 3.6% · 뭉침 9.1% · 절단 1.4%).
                _doubt = faith.get("career_split_doubt") or []
                _praw = faith.get("career_period_raw")
                _craw2 = faith.get("career_content_raw")
                if _doubt and (_praw or _craw2):
                    lines.append(f"  - ⓘ 항목 분리가 확실하지 않습니다({' · '.join(_doubt)})"
                                 " — 원문 두 칸을 함께 싣습니다:")
                    if _praw:
                        lines.append(f"    · 기간 원문: {_praw[:300]}")
                    if _craw2:
                        lines.append(f"    · 내용 원문: {_craw2[:700]}")
            # 독립성 4 sub_factor 결과 + 근거(경력 raw 등) — 사외이사/감사위원
            lines.extend(_indep_evidence_lines(c))
            if ah_red:
                lines.append(f"- 과거 회사 회계 위험 이력: {len(ah_red)}건 발견 — 본문 메모 원문 검토")
            # 사내이사 재직 중 성과 (ralph 260505) — 사내이사 + renewed에만 부착됨
            perf = c.get("performance") or {}
            if perf.get("classification") == "not_evaluated":
                # 평가를 안 한 것이지 「저조」가 아니다 — 점수·기간을 None 으로 찍지 않는다.
                lines.append(f"- **재직 중 성과**: 평가하지 않음 — "
                             f"{perf.get('rationale') or '등기이사 재직 이력을 확인하지 못했습니다.'}")
                # 판단 근거가 된 임원현황 행을 통째로 — 사유 문장만으로는 읽는 쪽이 검증할 수
                # 없다(대웅제약 박은경: 「재직기간 2010.1 ~現」이 취임연령 게이트에 걸렸는데
                # 그 표기를 볼 방법이 없었다). 확정된 경우엔 붙지 않는다.
                _rr = perf.get("roster_row") or {}
                if _rr:
                    lines.append("  - 판단 근거가 된 임원현황 원문 행:")
                    for _k, _v in _rr.items():
                        lines.append(f"    · {_k}: {_v}")
            elif perf.get("classification"):
                cls = perf.get("classification", "n/a")  # 영문 키 — 이모지 매핑용
                cls_ko = perf.get("classification_ko") or cls  # 한글 표시
                cls_emoji = {"good": "🟢", "moderate": "🟡", "weak": "🟠", "bad": "🔴"}.get(cls, "")
                # 재직 기간이 정형이 아니라 **추정**이면 분류 라벨에 그 사실을 병기한다.
                # 게이트가 정형 재직기간을 「근속 오기재」로 버린 뒤 소집공고 추정으로 되돌아간
                # 경우다(실측 3/113). 「양호」만 보면 확신 있게 읽히는데 근거는 그만큼 단단하지
                # 않다 — 정형 재직기간이 등기 기간인지 근속인지는 표기로 가릴 수 없고
                # (서식별 취임연령 이상률: 일자범위 12% ≈ N년 12% — 서식은 판별자가 아니다)
                # 확정하려면 법인등기부가 필요하다.
                _est = " (추정 기간 기준)" if "추정" in (perf.get("tenure_source") or "") else ""
                lines.append(f"- **재직 중 성과**: {cls_emoji} **{cls_ko}**{_est} "
                             f"(총점 {perf.get('total_score')}/12, 재직 {perf.get('tenure_period', '-')})")
                # 점수를 좌우하는 건 「재직 몇 년」이다 — 그 기간이 어디서 왔는지 밝히지 않으면
                # 읽는 쪽이 검증할 수 없다(계산은 되고 있었는데 렌더가 빠져 있었다).
                if perf.get("tenure_source"):
                    lines.append(f"  - 재직 기간 근거: {perf['tenure_source']}")
                if perf.get("tenure_note"):
                    lines.append(f"  - {perf['tenure_note']}")
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
                    # 연도 간 단위가 다르면 공통 단위 표기가 착시를 만든다 → 연도별 병기 (P1-6)
                    unit_ok = seg.get("unit_consistent", True)

                    def _seg_v(v) -> str:
                        return f"{v:,.0f}" if isinstance(v, (int, float)) else "-"

                    span = " → ".join(
                        f"FY{r['fy']} 매출 {_seg_v(r.get('revenue'))}·영업이익 {_seg_v(r.get('profit'))}"
                        + (f"({(r.get('unit') or '').strip()})" if not unit_ok and r.get("unit") else "")
                        for r in sr
                    )
                    excluded = seg.get("excluded_years") or []
                    # 제외 사유를 구분해 표기 (P1-2) — fetch 실패를 '회사 공시 저신뢰'로 오표기 금지
                    _reason_ko = {
                        "fetch_error": "조회 실패",
                        "not_applicable": "부문 공시 대상 아님",
                        "segment_absent_or_renamed": "해당 부문 미존재(재편·개명 가능)",
                        "low_confidence": "부문표 정형 추출 저신뢰",
                    }
                    _reasons = seg.get("excluded_reasons") or {}
                    if excluded:
                        _grouped: dict[str, list[str]] = {}
                        for _y in excluded:
                            _grouped.setdefault(_reasons.get(str(_y), "low_confidence"), []).append(str(_y))
                        excl_note = " · " + ", ".join(
                            f"FY{'/'.join(ys)}는 {_reason_ko.get(rk, rk)}로 제외"
                            for rk, ys in _grouped.items()
                        )
                    else:
                        excl_note = ""
                    lines.append(
                        f"  - **담당부문 성과(참고, 점수 미반영)**: {seg.get('segment')} — {span}"
                        + (f" (단위: {unit})" if unit and unit_ok else "")
                        + ("" if unit_ok else " ⚠️연도 간 공시 단위 상이 — 추이 비교 주의")
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
                lines.append(f"### 📄 영업부문 주석 원문 (FY{seg_ref.get('fiscal_year')} · 참고, 점수 미반영)")
                lines.append(f"> {seg_ref.get('note')}")
                if seg_ref.get("truncated"):
                    # 다른 도구의 호출 시그니처를 사람 문서에 적지 않는다 — 무엇이 필요한지만
                    # 말하면 호출측 AI 가 알아서 부른다(260728 이마트 실측).
                    lines.append(
                        f"> 원문 {seg_ref.get('full_length'):,}자 중 앞 {seg_ref.get('context_chars'):,}자입니다. "
                        f"부문 정보 전체가 필요하면 {seg_ref.get('fiscal_year')}년 사업부문 상세를 "
                        f"따로 조회하시면 됩니다."
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
            lines.append("### 이사 회계 위험 이력 — 과거 재직 회사에서 겹치는 회계 위험 (원문)")
            lines.append("> 사외이사 충실의무 단정 X — 사용자 판단 위임. 본 시점에 후보가 그 회사에 재직 중이었음을 의미.")
            lines.append("")
            lines.append("| 후보 | 과거 회사 | 재직 기간 | 위험 유형 | 시점 | 상세 |")
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
                    lines.append(f"| {cand_name} | {co} | {tenure} | {_AUDIT_RISK_TYPE_KO.get(rtype, rtype)} | {yr} | {detail} |")
            lines.append("")

    # 기업지배구조보고서 15개 핵심지표 중 미준수 — 준수율만 보면 어느 지표가 빠졌는지 모른다.
    # 그중엔 의결권 판단에 바로 닿는 것이 있다(이사회 의장 겸직·이사회 단일성·집중투표제).
    gnc = data.get("governance_non_compliant") or []
    if gnc:
        lines.append("## 기업지배구조보고서 미준수 지표 (15개 핵심지표 중)")
        for it in gnc:
            if not isinstance(it, dict):        # 옛 형태(라벨 문자열)도 그대로 받는다
                lines.append(f"- {it}")
                continue
            _pr = (it.get("prior") or "").strip().upper()
            _tail = " · 전년에도 미준수" if _pr in ("X", "×") else (
                f" · 전년 {it['prior']}" if it.get("prior") else "")
            lines.append(f"- {it.get('label')}{_tail}")
            # 회사가 적은 사유 — 준수/미준수보다 이쪽이 판단에 더 닿는다
            if it.get("note"):
                lines.append(f"  - 회사 설명(원문): {it['note']}")
            # 그 사유가 다른 절을 가리키기만 하면 가리킨 절을 데려온다(원문은 위에 그대로)
            if it.get("note_ref"):
                lines.append(f"    · 가리킨 세부원칙 원문: {it['note_ref']}")
        lines.append("")

    # 회사 펀더멘털 요약 (참고)
    fin = data.get("financial_summary") or {}
    if fin:
        lines.append("## 회사 재무 (참고)")
        lines.append(f"- 매출액 {_won(fin.get('revenue_krw'))} · 영업이익 {_won(fin.get('operating_profit_krw'))}")
        lines.append(f"- ROE {fin.get('roe_pct') or '-'}% · 부채비율 {fin.get('debt_ratio_pct') or '-'}%")
        _imp = {"normal": "자본잠식 없음", "partial": "부분 자본잠식", "full": "완전 자본잠식"}
        _st = fin.get("capital_impairment_status")
        lines.append(f"- {_imp.get(_st, _st or '-')}")
        lines.append("")

    # 읽은 공시 — 이 메모가 무엇을 보고 나왔는지. 예전에는 upstream 당 2건 + 표시 5건으로 두 번
    # 잘려서, 판정에 실제로 쓰인 지분·감사의견·배당 공시가 목록에 없었다.
    read = data.get("disclosures_read") or []
    if read:
        lines.append(f"## 읽은 공시 ({len(read)}건)")
        lines.append("| 공시 | 접수일 | 무엇에 썼나 | 비고 |")
        lines.append("|------|--------|------------|------|")
        for d in read:
            rcept = d.get("rcept_no", "-")
            url = d.get("viewer_url") or "-"
            name = (d.get("report_nm") or "-").replace("\n", " ").strip()
            dt = d.get("rcept_dt") or "-"
            used = " · ".join(d.get("used_for") or [])
            # 원문 note 에 줄바꿈·파이프가 섞이면 표가 무너진다.
            note = " / ".join(d.get("notes") or []).replace("\n", " ").replace("|", "／")
            lines.append(f"| [{name}]({url}) | {dt} | {used} | {note[:120]} |")
        lines.append("")
        lines.append(f"_접수번호로 직접 확인: {' · '.join(d.get('rcept_no', '') for d in read)}_")
        lines.append("")

    # 추가 분석 영역 — 짧게. 사용자가 자연스럽게 후속 질문 유도 (도구는 Claude가 알아서 매칭)
    decisions_local = data.get("agenda_decisions", []) or []
    has_director = any(ad.get("agenda_category") in ("director_election", "audit_committee_election") for ad in decisions_local)
    topics: list[str] = ["배당", "지분 구조·행동주의", "기업가치 제고 계획", "운용사별 정책 비교", "재무 상세"]
    if has_director:
        topics.append("이사 후보의 과거 회계 문제 이력")

    lines.append("---")
    lines.append(f"_더 보고 싶은 영역: {' · '.join(topics)} — 이어서 물어보시면 영역별로 더 자세히 분석합니다._")

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def proxy_advise_before_meeting(
        company: str,
        year: int = 0,
        meeting_type: str = "auto",
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
        meeting_type: `auto`(default — 정기/임시 중 지금 표를 던져야 하는 회차) / `annual` 정기만 / `extraordinary` 임시만. 임시주총을 보려고 따로 지정할 필요 없다.
        year: 미지정(0) 시 회의일이 과거 12개월~앞으로 90일 안인 회차를 자동 선택 — **아직 열리지 않은 예정 주총도 포함**되므로 다가오는 임시주총을 보려고 year를 따로 넣을 필요는 없다. 응답의 회차 선택 근거·정기/임시로 어느 회차인지 확인. 특정 과거 연도 분석에만 year 명시.
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
