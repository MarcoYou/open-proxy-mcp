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
    assert sub["result"] == "employed_by_proposer"
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
    assert out["result"] == "employed_by_proposer"


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
    # 이름 근처에 「주주 제안」만 있다 — **지명된 것**이지 그 주주에 소속됐다는 근거는 없다.
    assert han["result"] == "proposed_by_shareholder"

    lee = _proposer_affiliation({"name": "이민규", "recommendationReasonShared": shared})
    assert lee["result"] == "no_signal", "이사회제안 후보까지 걸리면 안 된다"


def test_employed_by_proposer_is_not_the_same_label_as_merely_nominated() -> None:
    """🔴 한 라벨로 묶으면 독립성이 전혀 다른 둘이 같아 보인다 (2026-08-29 U 5차).

    태광산업 실측 — 채이배는 제안주주 밖의 회계사이고, 윤상녕은 그 주주의 현직
    주주권행사팀장이다. 예전엔 둘 다 「제안 측 인사」 한 라벨을 받아, 읽는 쪽이
    원문을 직접 읽어서 갈라야 했다.
    """
    employed = evaluate_independence(
        {**_CLEAN, "name": "윤상녕",
         "recommendationReason": "제안주주 소속의 주주권행사팀장으로 재직 중"}, 2026)
    nominated = evaluate_independence(
        {**_CLEAN, "name": "채이배",
         "recommendationReasonShared": "○ 사외이사 후보자 채이배 - 주주제안에 의한 후보입니다."},
        2026)

    assert employed["sub_factors"]["proposer_affiliation"]["result"] == "employed_by_proposer"
    assert nominated["sub_factors"]["proposer_affiliation"]["result"] == "proposed_by_shareholder"
    # 요약도 갈려야 한다 — 여기가 같으면 화면에서 다시 한 덩어리가 된다.
    assert employed["summary"] == "proposer_side_concerns"
    assert nominated["summary"] == "proposer_nominated"
    assert employed["summary"] != nominated["summary"]
    # 둘 다 「독립적」이라고는 말하지 않는다.
    assert "independent" not in (employed["summary"], nominated["summary"])


def test_proposer_speaking_is_not_proposer_employment() -> None:
    """🔴 「제안주주」가 나왔다고 그 주주 소속이 아니다 (2026-08-29 태광산업 실측).

    채이배 추천사유는 「제안주주는 채이배 후보자의 전문성을 고려할 때…」로 **제안주주가
    말하는 문장**이고, 윤상녕은 「제안주주 소속의 주주권행사팀장」으로 **소속**이다.
    한 단어 매칭이던 예전 코드는 둘을 똑같이 「제안주주 소속」으로 찍었다.
    """
    chae = _proposer_affiliation({
        "name": "채이배",
        "recommendationReason": (
            "채이배 후보자는 공인회계사로 좋은기업지배구조연구소 연구위원 경력을 가졌으며 "
            "제안주주는 채이배 후보자의 이러한 전문성과 경력을 고려할 때 기업의 올바른 "
            "지배구조에 대한 확고한 의식을 보유하고 있다고 판단됩니다."),
    })
    yoon = _proposer_affiliation({
        "name": "윤상녕",
        "recommendationReason": (
            "윤상녕 후보자는 변호사로 제안주주 소속의 주주권행사팀장 및 운용역으로 "
            "재직 중이며 귀사의 지배구조에 대한 이해도를 가지고 있습니다."),
    })
    assert yoon["result"] == "employed_by_proposer"
    assert chae["result"] == "proposed_by_shareholder", "제안주주가 말한 것을 소속으로 읽으면 안 된다"
    assert chae["result"] != yoon["result"]


def test_partial_period_pairing_is_flagged_as_doubt() -> None:
    """🔴 일부 줄에만 기간이 붙은 것은 정상이 아니다 (2026-08-30 U 6차 실측).

    한국앤컴퍼니 이행희 — 기간 셀 구간 3개에 경력 7줄. 앞 3줄에만 순서대로 붙고
    나머지는 비었다. 화면엔 「1988~2024 대표이사」와 「2022~現 사업부장」이 나란히
    서서 현재 소속을 읽을 수 없었다.
    """
    from open_proxy_mcp.services.director_evaluation import career_split_doubt

    doubt = career_split_doubt({"careerDetails": [
        {"period": "2010~2014", "content": "다국적기업 최고경영자 협회 회장"},
        {"period": "1988~2024", "content": "한국코닝(주) 대표이사 (사장)"},
        {"period": "2022 ~ 現", "content": "한국코닝(주) 자동차환경 사업부장"},
        {"period": "", "content": "(주)포스코인터내셔널 사외이사"},
        {"period": "", "content": "(주)무신사 사외이사"},
    ]})
    assert any("짝은 순서 추측" in d for d in doubt)
    # 전부 붙어 있으면 의심하지 않는다 — 정상까지 흔들면 신호가 죽는다.
    assert not any("짝은 순서 추측" in d for d in career_split_doubt({"careerDetails": [
        {"period": "2019~2022", "content": "해군본부 법무관"},
        {"period": "2022~현재", "content": "트러스톤자산운용 주주권행사팀장"},
    ]}))
