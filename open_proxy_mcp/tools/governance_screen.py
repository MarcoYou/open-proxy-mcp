"""Bulk, source-bound governance reading and LLM triage."""
from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.governance_screen import build_governance_screen_payload

_TRIAGE = {"priority_review": "우선 검토", "review": "검토", "monitor": "관찰",
           "needs_evidence": "중요한 사실 충돌 확인 필요 (위험 확정 아님)",
           "no_adverse_signal_in_reviewed_scope": "평가한 범위에서 불리한 신호 미확인",
           "not_assessed": "평가 미완료"}
_CATEGORY = {"minority_shareholder_treatment": "소수주주 대우", "related_party_conflict": "이해상충",
             "board_accountability": "이사회 책임", "disclosure_reliability": "공시 신뢰성",
             "control_process": "경영권 관련 절차", "other": "기타"}
_FINDING = {"supported_risk": "근거 있는 우려", "no_adverse_signal": "불리한 신호 미확인", "unresolved": "미확인"}
_MATERIALITY = {"high": "높음", "moderate": "중간", "low": "낮음"}
_FACT = {"disclosed_fact": "공시된 사실", "party_claim": "당사자 주장", "court_ruling": "법원 판단",
         "conditional_plan": "조건부 계획", "unknown": "성격 미확인"}
_PROCEDURE = {"not_applicable": "해당 없음", "alleged": "혐의·주장", "filed": "신청·제기",
              "pending": "진행 중", "interim_order": "잠정적 결정", "final_ruling": "최종 판결",
              "appealed": "불복 절차", "settled": "합의·종결", "withdrawn": "취하·철회", "unknown": "미확인"}
_IMPACT = {"observed": "관찰된 영향", "prospective": "장래 영향", "unknown": "영향 미확인"}


def _details(label: str) -> str:
    return "<details><summary>" + label + "</summary>"


def render_governance_screen(payload: dict[str, Any]) -> str:
    lines = ["# 거버넌스 검토", "", payload["notice"], ""]
    if payload.get("warnings"):
        lines += ["## 회사 식별 확인", "", *payload["warnings"], ""]
    lines += [
             f"기준일: {payload['as_of']} · 대상: {len(payload['companies'])}개사", "",
             "| 회사 | 현재 상태 | 검토 순서 |", "|---|---|---|"]
    for row in payload["companies"]:
        name = (row.get("company") or {}).get("corp_name") or row["query"]
        status = {"assessed": "LLM 평가 수용", "assessment_pending": "LLM 판독 대기",
                  "company_failed": "원천 조회 실패", "company_unresolved": "회사 식별 필요"}.get(row["status"], row["status"])
        triage = _TRIAGE.get((row.get("triage") or {}).get("disposition"), "해당 회사만 재조회")
        lines.append(f"| {name.replace('|', '/')} | {status} | {triage} |")
    for row in payload["companies"]:
        name = (row.get("company") or {}).get("corp_name") or row["query"]
        lines += ["", f"## {name}", ""]
        if not row.get("assessment_task"):
            lines += ["이 회사의 식별 또는 원천 조회를 완료하지 못했습니다. 다른 회사 결과는 계속 사용할 수 있습니다."]
            continue
        task = row["assessment_task"]
        lines += ["최근 1년의 일부 공시 종류·쪽·원문만 읽은 범위입니다. 조회되지 않은 항목은 문제가 없다는 뜻이 아닙니다.",
                  "기준일 당일 공시와 주총 결과를 포함한 회사 검토입니다. 당일 공시의 장중 선후관계는 검증하지 않았습니다.",
                  f"원문 {len(task['sources'])}건 · 새 접수번호 후보 {len(row['delta']['items'])}건 · 검토 중요도 기준 {_MATERIALITY[task['review_materiality']]}"]
        if row["delta"]["items"]:
            lines += ["", "새로 확인할 공시:"]
            for item in row["delta"]["items"]:
                rc = item["rcept_no"]
                lines.append(f"- {item.get('published', '')} [{item.get('report_nm') or rc}](https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rc}) · {rc}")
        lines += ["", _details("다음 호출에 사용할 checkpoint"), "", "```json",
                  as_pretty_json(row["delta"]["checkpoint"]), "```", "", "</details>"]
        accepted = row.get("assessment") or {}
        if accepted.get("status") == "accepted_unreviewed":
            lines += ["", accepted["summary"]]
            for finding in accepted["findings"]:
                lines += ["", f"### {_CATEGORY[finding['category']]}: {_FINDING[finding['disposition']]} ({_MATERIALITY[finding['materiality']]})",
                          f"- 당사자: {finding['actor']} · 사건: {finding['event']}",
                          f"- 관찰: {finding['observation']}", f"- 판단: {finding['rationale']}",
                          f"- 근거 성격: {_FACT[finding['fact_status']]} · 절차: {_PROCEDURE[finding['procedural_state']]} · {_IMPACT[finding['impact_state']]}",
                          f"- 회사·후보와의 관련성: {finding['relevance']}"]
                if finding.get("event_date") or finding.get("case_or_round"):
                    lines.append(f"- 사건일: {finding.get('event_date') or '미상'} · 사건·회차: {finding.get('case_or_round') or '미상'}")
                for ref in finding["evidence_refs"]:
                    source = next(s for s in task["sources"] if s["source_id"] == ref["source_id"])
                    lines.append(f"- [공시 원문]({source['source_url']}): {ref['quote']}")
                for ref in finding["counterevidence"]:
                    source = next(s for s in task["sources"] if s["source_id"] == ref["source_id"])
                    lines.append(f"- [반대 근거·보완 공시]({source['source_url']}): {ref['quote']}")
                for gap in finding["gaps"]:
                    lines.append(f"- 미확인: {gap['question']} · 다음 확인: {gap['next_action']}")
            skips = [*accepted["skipped_checks"], *accepted["implicit_skipped_checks"]]
            if skips:
                lines += ["", "평가에서 건너뛴 항목:"] + [f"- {_CATEGORY[g['category']]}: {g['question']}" for g in skips]
        elif accepted.get("status") == "rejected":
            lines += ["", "제출 평가의 과업 식별 또는 원문 인용이 맞지 않아 수용하지 않았습니다. 다른 회사 평가에는 영향이 없습니다."]
        lines += ["", _details("LLM 판독 과업 · 원문 · 이어 읽기"), "",
                  "```json", as_pretty_json(task), "```", "", "</details>"]
    if payload["submission_errors"] or payload["unmatched_task_ids"]:
        lines += ["", "## 제출 확인", "", "일부 입력 또는 이전 과업 평가를 수용하지 않았습니다.",
                  "```json", as_pretty_json({"submission_errors": payload["submission_errors"],
                                             "unmatched_task_ids": payload["unmatched_task_ids"]}), "```"]
    lines += ["", _details("평가 제출 JSON 명세"), "", "```json",
              as_pretty_json(payload["assessment_schema"]), "```", "", "</details>", "",
              "누락은 해당 항목만 표시하고 진행합니다. 다음 조회는 사용자가 호출할 때 실행하며 예약 작업이나 실제 투표를 만들지 않습니다."]
    return "\n".join(lines)


def register_tools(mcp):
    @mcp.tool()
    async def governance_screen(
        companies: list[str], as_of: str = "", governance_assessments: list[dict[str, Any]] | None = None,
        evidence_sources: list[dict[str, Any]] | None = None, review_materiality: str = "moderate",
        since: str = "", known_receipts: list[str] | None = None, format: str = "md",
    ) -> str:
        """desc: 명시한 최대 30개사의 공시 원문을 모아 호출 LLM이 거버넌스 우려를 검토하고 중요도별 검토 순서를 반환. 사람 미검토 파일럿.
        when: 여러 기업의 소수주주 대우·이해상충·이사회 책임·공시 신뢰성·경영권 관련 절차를 공시 근거로 살피거나 새 공시를 증분 조회할 때.
        rule: 먼저 assessment_task 원문을 읽고 같은 인자+governance_assessments로 재호출. 공개매수·행동주의·소송 자체는 부정 신호가 아니며 당사자 주장과 판결·계약과 결제를 구분. 누락은 해당 항목만 건너뜀. 실제 투표 없음.
        evidence_sources: [{company: 입력 회사명, sources: [{type: dart, rcept_no: 14자리, focus_terms?: [...], text_offset?: 0, text_chars?: 12000}]}]. 회사당 최대 5건. 현재 source 요청을 유지하고 필요한 원문 창을 추가/교체하면 task_id도 변경.
        since: YYYYMMDD. known_receipts와 함께 새 접수번호 목록을 좁힐 뿐 평가 원문 범위는 유지. 호출자가 checkpoint를 보존하며 예약 작업은 만들지 않음.
        ref: proxy_advise_before_meeting, proxy_contest, evidence, company
        """
        if format not in {"md", "json"}:
            raise ValueError("format must be md or json")
        payload = await build_governance_screen_payload(
            companies, as_of=as_of, governance_assessments=governance_assessments,
            evidence_sources=evidence_sources, review_materiality=review_materiality,
            since=since, known_receipts=known_receipts)
        return as_pretty_json(payload) if format == "json" else render_governance_screen(payload)
