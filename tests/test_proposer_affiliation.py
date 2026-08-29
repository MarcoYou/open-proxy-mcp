"""후보 독립성은 「회사와의 관계」만 보면 반쪽이다 — **어느 편에서 왔나**도 봐야 한다.

4개 sub-factor(최대주주 관계·거래·직원이력·연임)는 전부 회사 축이다. 그래서
위임장 대결에서 **제안한 쪽 사람**이 「독립적(세부 항목 모두 충족)」으로 통과했다
(2026-08-28 실측) — 대림제지 한병우는 **위임장 권유자 본인**, 태광산업 윤상녕은
추천사유 원문에 **「제안주주 소속 주주권행사팀장」**.

부적격이라 못 박지 않는다. 다만 **「독립적」이라고는 말하지 않는다** — 그 한 마디가
읽는 쪽의 검토를 멈춰 세운다.
"""

from __future__ import annotations

from open_proxy_mcp.services.director_evaluation import (
    _proposer_affiliation,
    evaluate_independence,
)

_CLEAN = {"majorShareholderRelation": "해당없음", "recent3yTransactions": "없음",
          "careerDetails": [], "roleType": "사외이사"}


def test_plain_candidate_has_no_signal() -> None:
    ev = evaluate_independence({**_CLEAN, "name": "홍길동"}, 2026)
    assert ev["sub_factors"]["proposer_affiliation"]["result"] == "no_signal"
    assert ev["summary"] == "independent"


def test_proposer_employee_is_surfaced() -> None:
    """태광 윤상녕 꼴 — 추천사유 원문에 제안주주 소속이 적혀 있다."""
    ev = evaluate_independence(
        {**_CLEAN, "name": "윤상녕",
         "recommendationReason": "제안주주 소속 주주권행사팀장"}, 2026)
    sub = ev["sub_factors"]["proposer_affiliation"]
    assert sub["result"] == "affiliated_with_proposer"
    assert "제안주주" in (sub["evidence"] or "")
    assert ev["summary"] == "proposer_side_concerns"
    assert ev["summary"] != "independent"


def test_proxy_solicitor_himself_is_surfaced() -> None:
    """대림 한병우 꼴 — 후보가 위임장 권유자 본인이다."""
    ev = evaluate_independence({**_CLEAN, "name": "한병우"}, 2026,
                               proxy_solicitors={"한병우"})
    sub = ev["sub_factors"]["proposer_affiliation"]
    assert sub["result"] == "proxy_solicitor_self"
    assert ev["summary"] == "proposer_side_concerns"


def test_career_text_also_counted() -> None:
    out = _proposer_affiliation({"name": "김아무개", "careerDetails": [
        {"content": "2024~현재 주주권행사팀 팀장"}]})
    assert out["result"] == "affiliated_with_proposer"


def test_evidence_is_not_invented() -> None:
    """신호가 없으면 근거도 비운다 — 「없음」을 근거처럼 적지 않는다."""
    out = _proposer_affiliation({"name": "홍길동"})
    assert out["result"] == "no_signal"
    assert out["evidence"] is None


def test_shareholder_proposal_only_counts_near_the_name() -> None:
    """묶음 추천사유 — 이사회제안 후보와 주주제안 후보가 한 문단에 섞여 온다.

    실측 대림제지: 「○ 독립이사 후보자 이민규 - 해당 후보자는 세무법인 … 판단되어
    사외이사 후보 추천위원회에서 추천하였습니다. ○ 독립이사 후보자 전우석, 한병우 -
    상기 후보자는 주주 제안…」. 통째로 매칭하면 이민규까지 걸린다.
    """
    shared = ("○ 독립이사 후보자 이민규 - 해당 후보자는 세무법인 스카이원 대표세무사로서 "
              "전문적인 식견과 다년간의 업무경험 및 보유역량을 바탕으로 회사의 경영활동에 대한 "
              "독립적인 업무수행 및 객관적 평가를 제공함으로써 효율적인 경영환경을 촉진할 수 있는 "
              "충분한 자질을 갖추고 있어 사외이사 후보 추천위원회에서 추천하였습니다. "
              "○ 독립이사 후보자 전우석, 한병우- 상기 후보자는 주주 제안에 의한 후보입니다.")
    han = _proposer_affiliation({"name": "한병우", "recommendationReasonShared": shared})
    assert han["result"] == "affiliated_with_proposer"

    lee = _proposer_affiliation({"name": "이민규", "recommendationReasonShared": shared})
    assert lee["result"] == "no_signal", "이사회제안 후보까지 걸리면 안 된다"
