"""v2 prepare_vote_brief action service."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

from open_proxy_mcp.services.contracts import AnalysisStatus, ToolEnvelope
from open_proxy_mcp.services.corp_gov_report import build_corp_gov_report_payload
from open_proxy_mcp.services.ownership_structure import build_ownership_structure_payload
from open_proxy_mcp.services.proxy_contest import build_proxy_contest_payload
from open_proxy_mcp.services.proxy_guideline import (
    _CATEGORY_KO,
    classify_agenda,
    load_policy,
)
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload


def _merge_status(*statuses: str) -> str:
    """Action tool용 status 합산.

    NO_FILING은 정상(사건 없음)이므로 PARTIAL보다 높은 등급(EXACT 동급)으로 본다.
    구체적으로 모든 입력이 NO_FILING이면 NO_FILING, 일부라도 EXACT가 섞이면 EXACT.
    PARTIAL/CONFLICT/REQUIRES_REVIEW/ERROR는 기존과 동일하게 우선.
    """
    if any(status == AnalysisStatus.ERROR for status in statuses):
        return AnalysisStatus.ERROR
    if any(status == AnalysisStatus.REQUIRES_REVIEW for status in statuses):
        return AnalysisStatus.REQUIRES_REVIEW
    if any(status == AnalysisStatus.CONFLICT for status in statuses):
        return AnalysisStatus.CONFLICT
    if any(status == AnalysisStatus.PARTIAL for status in statuses):
        return AnalysisStatus.PARTIAL
    if any(status == AnalysisStatus.AMBIGUOUS for status in statuses):
        return AnalysisStatus.AMBIGUOUS
    if statuses and all(status == AnalysisStatus.NO_FILING for status in statuses):
        return AnalysisStatus.NO_FILING
    return AnalysisStatus.EXACT


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        cleaned = (value or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        ordered.append(cleaned)
    return ordered


def _dedupe_evidence(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    ordered: list[dict[str, Any]] = []
    for ref in refs:
        evidence_id = ref.get("evidence_id", "")
        if evidence_id in seen:
            continue
        seen.add(evidence_id)
        ordered.append(ref)
    return ordered


def _meeting_date_for_asof(meeting_summary_payload: dict[str, Any], end_date: str) -> str:
    selected = meeting_summary_payload.get("data", {}).get("selected_meeting", {}) or {}
    meeting_date = selected.get("meeting_date")
    if meeting_date:
        return meeting_date
    requested_window = meeting_summary_payload.get("data", {}).get("requested_window", {}) or {}
    if requested_window.get("end_date"):
        return requested_window["end_date"]
    if end_date:
        return end_date
    return date.today().isoformat()


def _result_highlights(result_payload: dict[str, Any]) -> dict[str, Any]:
    result_data = result_payload.get("data", {}).get("results", {}) or {}
    items = result_data.get("items", []) or []
    if not items:
        return {}

    passed = 0
    opposed: list[dict[str, Any]] = []
    for item in items:
        if (item.get("passed") or "").strip() in {"가결", "원안가결", "수정가결"}:
            passed += 1
        opposition = item.get("opposition_rate")
        try:
            if opposition is not None and float(opposition) >= 10:
                opposed.append({
                    "number": item.get("number", ""),
                    "agenda": item.get("agenda", ""),
                    "opposition_rate": float(opposition),
                })
        except (TypeError, ValueError):
            continue
    return {
        "agenda_count": len(items),
        "passed_count": passed,
        "high_opposition_items": opposed,
        "result_format": result_data.get("result_format", ""),
        "numerical_vote_table_available": result_data.get("numerical_vote_table_available"),
    }


def _result_quality_note(result_brief: dict[str, Any]) -> str:
    result_format = result_brief.get("result_format", "")
    numerical_available = result_brief.get("numerical_vote_table_available")
    if result_format == "table" and numerical_available:
        return "세부표형 결과공시라 안건별 찬성률과 추정참석률까지 확인 가능하다."
    if result_format == "summary":
        return "요약형 결과공시라 안건별 가결·부결은 확인되지만 찬성률/참석률 수치는 제공되지 않는다."
    return ""


def _vote_math_brief(vote_math_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not vote_math_payload:
        return {}
    data = vote_math_payload.get("data", {}).get("vote_math", {}) or {}
    attendance = data.get("attendance_estimate", {}) or {}
    capital = data.get("capital_structure", {}) or {}
    interpretation = data.get("interpretation", {}) or {}
    meeting_ref = data.get("meeting_reference", {}) or {}
    return {
        "status": vote_math_payload.get("status", ""),
        "meeting_reference": meeting_ref,
        "representative_pct": attendance.get("representative_pct"),
        "comparable_item_count": attendance.get("comparable_item_count", 0),
        "signal_level": interpretation.get("signal_level", ""),
        "contestable_turnout_pct": capital.get("contestable_turnout_pct"),
        "ex_related_turnout_pct": capital.get("ex_related_turnout_pct"),
        "notes": interpretation.get("notes", []) or [],
        "warnings": vote_math_payload.get("warnings", []) or [],
    }


def _board_candidates(board_payload: dict[str, Any]) -> list[dict[str, Any]]:
    appointments = board_payload.get("data", {}).get("board", {}).get("appointments", []) or []
    rows: list[dict[str, Any]] = []
    for appointment in appointments:
        for candidate in appointment.get("candidates", []):
            rows.append({
                "name": candidate.get("name", ""),
                "role_type": candidate.get("roleType", ""),
                "main_job": candidate.get("mainJob", ""),
                "recommender": candidate.get("recommender", ""),
                "major_relation": candidate.get("majorShareholderRelation", ""),
                "agenda_number": appointment.get("number", ""),
                "agenda_title": appointment.get("title", ""),
            })
    return rows


def _compensation_brief(comp_payload: dict[str, Any]) -> dict[str, Any]:
    summary = comp_payload.get("data", {}).get("compensation_summary", {}) or {}
    return {
        "total_items": summary.get("totalItems", 0),
        "current_total_limit": summary.get("currentTotalLimit"),
        "prior_total_paid": summary.get("priorTotalPaid"),
        "prior_total_limit": summary.get("priorTotalLimit"),
        "prior_utilization": summary.get("priorUtilization"),
    }


def _agenda_titles(agenda_payload: dict[str, Any]) -> list[str]:
    agendas = agenda_payload.get("data", {}).get("agendas", []) or []
    titles: list[str] = []

    def walk(nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            title = node.get("title", "")
            if title:
                titles.append(title)
            walk(node.get("children", []) or [])

    walk(agendas)
    return titles


def _cumulative_board_seats(board_payload: dict[str, Any]) -> int:
    appointments = board_payload.get("data", {}).get("board", {}).get("appointments", []) or []
    seats = 0
    for appointment in appointments:
        category = (appointment.get("category") or "").strip()
        if "감사위원" in category or "감사" == category:
            continue
        if "이사" in category:
            seats += max(len(appointment.get("candidates", []) or []), 1)
    return seats


def _cumulative_signal(agenda_payload: dict[str, Any], board_payload: dict[str, Any]) -> dict[str, Any]:
    agenda_titles = _agenda_titles(agenda_payload)
    seats = _cumulative_board_seats(board_payload)
    explicit_reference = any("집중투표" in title for title in agenda_titles)
    related_agendas = [
        title for title in agenda_titles
        if "집중투표" in title or ("이사" in title and "선임" in title)
    ]
    return {
        "relevant": seats >= 2 or explicit_reference,
        "explicit_reference": explicit_reference,
        "seats_to_elect": seats,
        "related_agendas": related_agendas[:10],
    }


def _turnout_reference_brief(vote_math_payload: dict[str, Any] | None) -> dict[str, Any]:
    brief = _vote_math_brief(vote_math_payload)
    if not brief:
        return {}
    return {
        "representative_pct": brief.get("representative_pct"),
        "meeting_reference": brief.get("meeting_reference", {}),
        "source_status": brief.get("status", ""),
        "notes": brief.get("notes", []) or [],
        "warnings": brief.get("warnings", []) or [],
    }


async def _cumulative_voting_strategy(
    company_query: str,
    *,
    meeting_summary: dict[str, Any],
    agenda_payload: dict[str, Any],
    board_payload: dict[str, Any],
    ownership_payload: dict[str, Any],
    current_vote_math_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    signal = _cumulative_signal(agenda_payload, board_payload)
    if not signal.get("relevant"):
        return {}

    seats_to_elect = signal.get("seats_to_elect", 0)
    if seats_to_elect < 2:
        return {
            "relevant": True,
            "status": AnalysisStatus.PARTIAL,
            "reason": "집중투표 관련 문구는 보이지만 실제 집중투표 대상 이사 수가 2명 이상으로 확인되지 않았다.",
            "seats_to_elect": seats_to_elect,
            "related_agendas": signal.get("related_agendas", []),
        }

    ownership_summary = ownership_payload.get("data", {}).get("summary", {}) or {}
    treasury_pct = float(ownership_summary.get("treasury_pct", 0) or 0)
    related_total_pct = float(ownership_summary.get("related_total_pct", 0) or 0)
    top_holder = ownership_summary.get("top_holder", {}) or {}
    control_map = ownership_payload.get("data", {}).get("control_map", {}) or {}
    active_external_blocks = control_map.get("active_non_overlap_blocks", []) or []
    active_overlap_blocks = control_map.get("active_overlap_blocks", []) or []

    voting_base_pct = round(max(100.0 - treasury_pct, 0.0), 2)
    full_turnout_one_seat_pct_of_voting_base = round(100.0 / (seats_to_elect + 1), 1)
    full_turnout_one_seat_pct_of_total_issued = round(voting_base_pct / (seats_to_elect + 1), 1)

    turnout_reference = _turnout_reference_brief(current_vote_math_payload)
    turnout_reference_type = ""

    if not turnout_reference.get("representative_pct"):
        selected_meeting = (meeting_summary.get("data", {}) or {}).get("selected_meeting", {}) or {}
        meeting_date_raw = selected_meeting.get("meeting_date")
        prior_end = ""
        if meeting_date_raw:
            try:
                prior_end = (date.fromisoformat(meeting_date_raw) - timedelta(days=1)).isoformat()
            except ValueError:
                prior_end = ""
        if prior_end:
            prior_vote_math = await build_proxy_contest_payload(
                company_query,
                scope="vote_math",
                start_date=(date.fromisoformat(prior_end) - timedelta(days=365 * 3)).isoformat(),
                end_date=prior_end,
                lookback_months=36,
            )
            turnout_reference = _turnout_reference_brief(prior_vote_math)
            turnout_reference_type = "previous_result"
    else:
        turnout_reference_type = "selected_meeting_result"

    expected_turnout_pct = turnout_reference.get("representative_pct")
    expected_one_seat_pct = None
    if expected_turnout_pct is not None:
        expected_one_seat_pct = round(float(expected_turnout_pct) / (seats_to_elect + 1), 1)

    largest_external_block_pct = max(
        [float(row.get("ownership_pct", 0) or 0) for row in active_external_blocks],
        default=0.0,
    )
    largest_overlap_block_pct = max(
        [float(row.get("ownership_pct", 0) or 0) for row in active_overlap_blocks],
        default=0.0,
    )

    notes = [
        "집중투표 1석선은 이론적으로 1/(N+1)이다.",
        "자사주는 의결권이 없어 전체 의결권 모수에서 차감했다.",
        "감사위원/분리선출은 집중투표 대상 이사 수에서 제외하는 보수적 기준을 사용했다.",
    ]
    status = AnalysisStatus.EXACT if expected_one_seat_pct is not None else AnalysisStatus.PARTIAL
    if not signal.get("explicit_reference"):
        status = AnalysisStatus.PARTIAL
        notes.append("공시에 집중투표가 명시되진 않았고 복수 이사 선임만 확인돼, 잠재적 전략 계산으로만 봐야 한다.")
    if turnout_reference_type == "previous_result":
        notes.append("예상 참석률은 이전 회차의 대표 추정참석률을 참고했다.")
    elif turnout_reference_type == "selected_meeting_result":
        notes.append("이미 결과가 나온 회차라 실제 대표 추정참석률을 참고치로 제시한다.")

    return {
        "relevant": True,
        "status": status,
        "explicit_reference": signal.get("explicit_reference", False),
        "seats_to_elect": seats_to_elect,
        "related_agendas": signal.get("related_agendas", []),
        "voting_base_pct_of_total_issued": voting_base_pct,
        "full_turnout_one_seat_pct_of_voting_base": full_turnout_one_seat_pct_of_voting_base,
        "full_turnout_one_seat_pct_of_total_issued": full_turnout_one_seat_pct_of_total_issued,
        "expected_turnout_pct_of_total_issued": expected_turnout_pct,
        "expected_one_seat_pct_of_total_issued": expected_one_seat_pct,
        "turnout_reference_type": turnout_reference_type,
        "turnout_reference": turnout_reference,
        "holder_context": {
            "top_holder_name": top_holder.get("name", ""),
            "top_holder_pct": top_holder.get("ownership_pct"),
            "related_total_pct": related_total_pct,
            "largest_external_active_block_pct": round(largest_external_block_pct, 2),
            "largest_overlap_active_block_pct": round(largest_overlap_block_pct, 2),
        },
        "gaps": {
            "largest_external_block_gap_to_one_seat": round(max((expected_one_seat_pct or full_turnout_one_seat_pct_of_total_issued) - largest_external_block_pct, 0.0), 2),
            "largest_overlap_block_gap_to_one_seat": round(max((expected_one_seat_pct or full_turnout_one_seat_pct_of_total_issued) - largest_overlap_block_pct, 0.0), 2),
        },
        "notes": notes,
    }


def _build_proxy_guideline_brief(
    vote_style: str,
    agenda_titles: list[str],
) -> dict[str, Any]:
    """안건 목록을 OPM 정책에 매핑하여 카테고리별 권고 생성 (정책 룰 + 카테고리만, 매트릭스 채점 X).

    auto_score 통합 버전은 _build_proxy_guideline_brief_with_auto_score 참조.
    """
    pol = load_policy(vote_style)
    if not pol:
        return {
            "vote_style": vote_style,
            "status": "policy_not_found",
            "available_policies": ["open_proxy", "mirae_asset", "samsung", "samsung_active",
                                   "truston", "kim", "align_partners", "baring"],
            "agenda_recommendations": [],
        }

    rules = pol.get("voting_rules", {})
    pol_meta = pol.get("policy_meta", {})

    recommendations = []
    seen_categories = []
    for idx, title in enumerate(agenda_titles[:30]):
        cat = classify_agenda(title)
        rule = rules.get(cat, {})
        against_top3 = [a.get("criterion", "")[:80] for a in rule.get("against", [])[:3]]
        for_top2 = [f.get("criterion", "")[:80] for f in rule.get("for", [])[:2]]

        recommendations.append({
            "agenda_no": str(idx + 1),
            "agenda_title": title[:120],
            "category": cat,
            "category_ko": _CATEGORY_KO.get(cat, cat),
            "policy_default": rule.get("default", "not_specified"),
            "policy_for_count": len(rule.get("for", [])),
            "policy_against_count": len(rule.get("against", [])),
            "policy_review_count": len(rule.get("review", [])),
            "key_against_criteria": against_top3,
            "key_for_criteria": for_top2,
        })
        if cat not in seen_categories:
            seen_categories.append(cat)

    return {
        "vote_style": vote_style,
        "status": "ok",
        "policy_meta": {
            "manager_name": pol_meta.get("manager_name", vote_style),
            "version": pol_meta.get("version", ""),
            "type": pol_meta.get("type", ""),
            "effective_date": pol_meta.get("effective_date", ""),
        },
        "agenda_recommendations": recommendations,
        "categories_in_agenda": seen_categories,
        "policy_general_principles": pol.get("general_principles", [])[:5],
        "evaluation_note": "매트릭스 자동 채점은 proxy_guideline scope=predict (auto_score=True)에서 호출.",
    }


async def _augment_recommendations_with_auto_score(
    recommendations: list[dict[str, Any]],
    *,
    company_query: str,
    meeting_date: str,
    notice_disclosure_date: str,
    agenda_titles_full: list[str],
    max_agendas: int = 5,
) -> list[dict[str, Any]]:
    """매트릭스 자동 채점 결과를 recommendations에 추가.

    상위 max_agendas건만 자동 채점 (data tool 호출 cost 보호).
    실패 시 graceful — 기존 recommendation 그대로 유지.
    """
    try:
        from open_proxy_mcp.services.proxy_guideline_scoring import (
            aggregate_score_to_decision,
            auto_score_matrix,
            evaluate_all_bingo_patterns,
        )
        from open_proxy_mcp.services.proxy_guideline import load_decision_matrices
    except Exception as exc:
        return recommendations

    matrices = load_decision_matrices().get("matrices", {})
    seen_cats: set[str] = set()
    augmented: list[dict[str, Any]] = []
    for rec in recommendations:
        cat = rec.get("category", "")
        # 카테고리당 1번만 채점 (cost 절감)
        if cat in seen_cats or cat == "other":
            augmented.append(rec)
            continue
        if len([a for a in augmented if a.get("auto_score")]) >= max_agendas:
            augmented.append(rec)
            continue
        seen_cats.add(cat)
        try:
            matrix_id = f"matrix_{cat}"
            matrix = matrices.get(matrix_id, {})
            if not matrix:
                augmented.append(rec)
                continue
            score_result = await auto_score_matrix(
                company_query,
                cat,
                agenda_titles=agenda_titles_full,
                meeting_date=meeting_date,
                notice_disclosure_date=notice_disclosure_date,
            )
            scores = score_result.get("scores", {}) or {}
            valid = {k: v for k, v in scores.items() if v is not None}
            bingo = evaluate_all_bingo_patterns(
                matrix, valid,
                agenda_date=meeting_date or None, agenda_category=cat,
            )
            decision_meta = aggregate_score_to_decision(scores, matrix, bingo)
            rec_aug = dict(rec)
            rec_aug["auto_score"] = {
                "decision": decision_meta.get("decision"),
                "decision_source": decision_meta.get("decision_source"),
                "raw_score": decision_meta.get("raw_score"),
                "max_score": decision_meta.get("max_score"),
                "red_count": decision_meta.get("red_count", 0),
                "yellow_count": decision_meta.get("yellow_count", 0),
                "green_count": decision_meta.get("green_count", 0),
                "unknown_count": decision_meta.get("unknown_count", 0),
                "triggered_pattern_ids": decision_meta.get("triggered_pattern_ids", []),
                "dimensions_scored": scores,
                "manual_dims": score_result.get("manual_dims", []),
                "data_calls": score_result.get("data_calls", {}),
            }
            augmented.append(rec_aug)
        except Exception as exc:
            rec_aug = dict(rec)
            rec_aug["auto_score_error"] = f"{type(exc).__name__}: {str(exc)[:100]}"
            augmented.append(rec_aug)
    return augmented


async def build_vote_brief_payload(
    company_query: str,
    *,
    meeting_type: str = "auto",
    year: int | None = None,
    start_date: str = "",
    end_date: str = "",
    lookback_months: int = 12,
    vote_style: str = "open_proxy",
    auto_score_matrix: bool = False,
) -> dict[str, Any]:
    meeting_summary = await build_shareholder_meeting_payload(
        company_query,
        meeting_type=meeting_type,
        scope="summary",
        year=year,
        start_date=start_date,
        end_date=end_date,
        lookback_months=lookback_months,
    )

    if meeting_summary.get("status") in {AnalysisStatus.ERROR, AnalysisStatus.AMBIGUOUS}:
        return ToolEnvelope(
            tool="prepare_vote_brief",
            status=meeting_summary.get("status", AnalysisStatus.ERROR),
            subject=meeting_summary.get("subject", company_query),
            warnings=meeting_summary.get("warnings", []),
            data=meeting_summary.get("data", {}),
            evidence_refs=meeting_summary.get("evidence_refs", []),
            next_actions=["company 또는 shareholder_meeting에서 먼저 회차를 확정"],
        ).to_dict()

    meeting_data = meeting_summary.get("data", {})
    ownership_as_of = _meeting_date_for_asof(meeting_summary, end_date)

    async def _safe_governance() -> dict[str, Any]:
        """corp_gov_report fetch (10초 timeout, 실패 시 graceful skip)."""
        try:
            return await asyncio.wait_for(
                build_corp_gov_report_payload(company_query, scope="summary"),
                timeout=10.0,
            )
        except (asyncio.TimeoutError, Exception) as exc:
            return {
                "tool": "corp_gov_report",
                "status": AnalysisStatus.PARTIAL,
                "data": {},
                "warnings": [f"거버넌스 보고서 조회 생략 (timeout/error): {type(exc).__name__}"],
                "evidence_refs": [],
            }

    agenda_payload, board_payload, compensation_payload, ownership_payload, governance_payload = await asyncio.gather(
        build_shareholder_meeting_payload(
            company_query,
            meeting_type=meeting_type,
            scope="agenda",
            year=year,
            start_date=start_date,
            end_date=end_date,
            lookback_months=lookback_months,
        ),
        build_shareholder_meeting_payload(
            company_query,
            meeting_type=meeting_type,
            scope="board",
            year=year,
            start_date=start_date,
            end_date=end_date,
            lookback_months=lookback_months,
        ),
        build_shareholder_meeting_payload(
            company_query,
            meeting_type=meeting_type,
            scope="compensation",
            year=year,
            start_date=start_date,
            end_date=end_date,
            lookback_months=lookback_months,
        ),
        build_ownership_structure_payload(
            company_query,
            scope="control_map",
            as_of_date=ownership_as_of,
            start_date=start_date,
            end_date=end_date,
        ),
        _safe_governance(),
    )

    result_payload = None
    vote_math_payload = None
    if meeting_data.get("result_status") == "available":
        result_payload = await build_shareholder_meeting_payload(
            company_query,
            meeting_type=meeting_type,
            scope="results",
            year=year,
            start_date=start_date,
            end_date=end_date,
            lookback_months=lookback_months,
        )
        result_brief_for_quality = _result_highlights(result_payload)
        if result_brief_for_quality.get("numerical_vote_table_available"):
            vote_math_payload = await build_proxy_contest_payload(
                company_query,
                scope="vote_math",
                year=year,
                start_date=start_date,
                end_date=end_date,
                lookback_months=lookback_months,
            )

    merged_status = _merge_status(
        meeting_summary.get("status"),
        agenda_payload.get("status"),
        board_payload.get("status"),
        compensation_payload.get("status"),
        ownership_payload.get("status"),
        governance_payload.get("status"),
        result_payload.get("status") if result_payload else AnalysisStatus.EXACT,
        vote_math_payload.get("status") if vote_math_payload else AnalysisStatus.EXACT,
    )

    cumulative_voting_strategy = await _cumulative_voting_strategy(
        company_query,
        meeting_summary=meeting_summary,
        agenda_payload=agenda_payload,
        board_payload=board_payload,
        ownership_payload=ownership_payload,
        current_vote_math_payload=vote_math_payload,
    )
    merged_status = _merge_status(
        merged_status,
        cumulative_voting_strategy.get("status", AnalysisStatus.EXACT) if cumulative_voting_strategy else AnalysisStatus.EXACT,
    )

    # 거버넌스 브리프 (corp_gov_report summary)
    gov_data = governance_payload.get("data", {}) or {}
    gov_meta = gov_data.get("report_meta", {}) or {}
    gov_metrics_summary = gov_data.get("metrics_summary", []) or []
    gov_non_compliant = [m for m in gov_metrics_summary if m.get("current") in ("X", "×", "미준수")]
    governance_brief = {
        "compliance_rate": gov_meta.get("compliance_rate"),
        "metrics_compliant": gov_meta.get("metrics_compliant", 0),
        "metrics_non_compliant": gov_meta.get("metrics_non_compliant", 0),
        "metrics_parsed_count": gov_meta.get("metrics_parsed_count", 0),
        "report_rcept_dt": gov_meta.get("rcept_dt", ""),
        "report_rcept_no": gov_meta.get("rcept_no", ""),
        "non_compliant_labels": [m.get("label", "") for m in gov_non_compliant][:10],
        "mandatory": gov_data.get("mandatory", False),
        "market": gov_data.get("market", ""),
    }

    control_map = ownership_payload.get("data", {}).get("control_map", {}) or {}
    summary = {
        "meeting_type": meeting_data.get("meeting_type", ""),
        "meeting_phase": meeting_data.get("meeting_phase", ""),
        "result_status": meeting_data.get("result_status", ""),
        "meeting_date": (meeting_data.get("selected_meeting") or {}).get("meeting_date"),
        "notice_rcept_no": (meeting_data.get("selected_meeting") or {}).get("notice_rcept_no"),
        "selection_basis": meeting_data.get("selection_basis", ""),
        "top_holder": ownership_payload.get("data", {}).get("summary", {}).get("top_holder", {}),
        "related_total_pct": ownership_payload.get("data", {}).get("summary", {}).get("related_total_pct", 0),
        "treasury_pct": ownership_payload.get("data", {}).get("summary", {}).get("treasury_pct", 0),
        "agenda_count": meeting_data.get("agenda_summary", {}).get("total_count", 0),
        "candidate_count": board_payload.get("data", {}).get("board_summary", {}).get("total_candidates", 0),
        "outside_director_count": board_payload.get("data", {}).get("board_summary", {}).get("outside_directors", 0),
        "compensation_items": compensation_payload.get("data", {}).get("compensation_summary", {}).get("totalItems", 0),
    }

    result_brief = _result_highlights(result_payload or {})
    result_quality_note = _result_quality_note(result_brief)
    vote_math_brief = _vote_math_brief(vote_math_payload)

    # 거버넌스 기반 추가 key_flags
    governance_flags: list[str] = []
    if governance_brief["compliance_rate"] is not None:
        rate = governance_brief["compliance_rate"]
        nc = governance_brief["metrics_non_compliant"]
        total = governance_brief["metrics_parsed_count"]
        if rate < 60:
            governance_flags.append(f"지배구조 핵심지표 준수율 {rate}%로 낮다 (미준수 {nc}/{total}).")
        elif rate < 80:
            governance_flags.append(f"지배구조 준수율 {rate}% — 보통 수준 (미준수 {nc}/{total}).")
        elif rate >= 95:
            governance_flags.append(f"지배구조 준수율 {rate}% — 우수.")
    if governance_brief["non_compliant_labels"]:
        structural = [lbl for lbl in governance_brief["non_compliant_labels"]
                      if any(k in lbl for k in ("집중투표", "사외이사가 이사회 의장", "독립적인 내부감사"))]
        if structural:
            governance_flags.append(f"구조적 거버넌스 약점 미준수 지표: {', '.join(s[:30] for s in structural[:3])}")

    key_flags = _dedupe_strings(
        [
            *meeting_summary.get("warnings", []),
            *agenda_payload.get("warnings", []),
            *board_payload.get("warnings", []),
            *compensation_payload.get("warnings", []),
            *ownership_payload.get("warnings", []),
            *governance_payload.get("warnings", []),
            *(result_payload.get("warnings", []) if result_payload else []),
            *(vote_math_payload.get("warnings", []) if vote_math_payload else []),
            *(control_map.get("observations", []) or []),
            *governance_flags,
            result_quality_note,
            "집중투표 사전 전략 계산을 포함했다." if cumulative_voting_strategy else "",
        ]
    )
    if meeting_data.get("correction_summary"):
        key_flags.insert(0, "정정공고가 반영된 회차다.")
    if meeting_data.get("meeting_phase") == "pre_meeting":
        key_flags.insert(0, "아직 회의 전이라 결과는 없다.")
    elif meeting_data.get("meeting_phase") == "post_meeting_pre_result":
        key_flags.insert(0, "회의는 끝났지만 결과공시는 아직 확인되지 않았다.")

    data = {
        "query": company_query,
        "company_id": meeting_data.get("company_id", ""),
        "canonical_name": meeting_data.get("canonical_name", ""),
        "requested_window": meeting_data.get("requested_window", {}),
        "quality": {
            "meeting_summary_status": meeting_summary.get("status", ""),
            "agenda_status": agenda_payload.get("status", ""),
            "board_status": board_payload.get("status", ""),
            "compensation_status": compensation_payload.get("status", ""),
            "ownership_status": ownership_payload.get("status", ""),
            "governance_status": governance_payload.get("status", ""),
            "result_status": result_payload.get("status", "") if result_payload else "",
            "vote_math_status": vote_math_payload.get("status", "") if vote_math_payload else "",
            "notice_parse_source": meeting_data.get("notice_parse_source", ""),
            "result_format": result_brief.get("result_format", ""),
            "numerical_vote_table_available": result_brief.get("numerical_vote_table_available"),
        },
        "meeting": {
            "requested_meeting_type": meeting_data.get("requested_meeting_type", ""),
            "selected_meeting": meeting_data.get("selected_meeting", {}),
            "alternative_meetings": meeting_data.get("alternative_meetings", []),
            "coverage": meeting_data.get("meeting_coverage_12m", {}),
            "meeting_info": meeting_data.get("meeting_info", {}),
            "summary": summary,
        },
        "ownership_context": {
            "summary": ownership_payload.get("data", {}).get("summary", {}),
            "control_map": {
                "flags": control_map.get("flags", {}),
                "observations": control_map.get("observations", []),
                "active_non_overlap_blocks": control_map.get("active_non_overlap_blocks", [])[:10],
                "active_overlap_blocks": control_map.get("active_overlap_blocks", [])[:10],
            },
        },
        "agenda_brief": {
            "titles": _agenda_titles(agenda_payload)[:20],
            "summary": meeting_data.get("agenda_summary", {}),
        },
        "board_brief": {
            "summary": board_payload.get("data", {}).get("board_summary", {}),
            "candidates": _board_candidates(board_payload)[:20],
        },
        "compensation_brief": _compensation_brief(compensation_payload),
        "governance_brief": governance_brief,
        "proxy_guideline_brief": _build_proxy_guideline_brief(
            vote_style=vote_style,
            agenda_titles=_agenda_titles(agenda_payload),
        ),
        "result_brief": result_brief,
        "vote_math_brief": vote_math_brief,
        "cumulative_voting_strategy": cumulative_voting_strategy,
        "key_flags": key_flags[:20],
    }

    # 자동 매트릭스 채점 통합 (옵션)
    if auto_score_matrix:
        meeting_date_for_score = (meeting_data.get("selected_meeting") or {}).get("meeting_date") or ""
        notice_disclosure_for_score = (meeting_data.get("selected_meeting") or {}).get("notice_disclosure_date") or ""
        try:
            augmented = await _augment_recommendations_with_auto_score(
                data["proxy_guideline_brief"].get("agenda_recommendations", []),
                company_query=company_query,
                meeting_date=meeting_date_for_score,
                notice_disclosure_date=notice_disclosure_for_score,
                agenda_titles_full=_agenda_titles(agenda_payload),
                max_agendas=5,
            )
            data["proxy_guideline_brief"]["agenda_recommendations"] = augmented
            data["proxy_guideline_brief"]["auto_score_enabled"] = True
            data["proxy_guideline_brief"]["disclaimer"] = (
                "자동 채점 결과는 참고용 — 최종 판단은 사용자가 검토 후 결정. "
                "데이터 부족 dim은 manual input으로 보정 가능."
            )
        except Exception as exc:
            data["proxy_guideline_brief"]["auto_score_error"] = (
                f"{type(exc).__name__}: {str(exc)[:200]}"
            )

    evidence_refs = _dedupe_evidence([
        *(meeting_summary.get("evidence_refs", []) or []),
        *(agenda_payload.get("evidence_refs", []) or []),
        *(board_payload.get("evidence_refs", []) or []),
        *(compensation_payload.get("evidence_refs", []) or []),
        *(ownership_payload.get("evidence_refs", []) or []),
        *(governance_payload.get("evidence_refs", []) or []),
        *((result_payload.get("evidence_refs", []) or []) if result_payload else []),
        *((vote_math_payload.get("evidence_refs", []) or []) if vote_math_payload else []),
    ])

    next_actions = [
        "evidence tool로 핵심 공시 원문을 다시 열어 확인",
        "shareholder_meeting results scope로 실제 의결 결과 재확인" if meeting_data.get("result_status") == "available" else "회의 이후 결과공시가 나오면 results scope로 다시 확인",
        "결과가 요약형이면 vote_math 대신 안건별 가결·부결 중심으로 해석",
        "ownership_structure control_map과 함께 보면 표 대결 구도가 더 선명하다.",
    ]

    return ToolEnvelope(
        tool="prepare_vote_brief",
        status=merged_status,
        subject=meeting_summary.get("subject", company_query),
        warnings=[],
        data=data,
        evidence_refs=evidence_refs,
        next_actions=next_actions,
    ).to_dict()
