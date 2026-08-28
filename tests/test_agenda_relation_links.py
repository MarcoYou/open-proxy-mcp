# -*- coding: utf-8 -*-
"""안건 사이의 관계 — **같은 자리를 다투는 안건에 같은 ✅ 를 내보내지 않는다.** network 0콜.

260828 실측 대림제지(017650) 2026 임시주총이 이 파일의 출발점이다. 주주제안 감사위원 해임
3건은 ⚠️ 인데 그 빈 자리를 채울 선임 4건이 전부 ✅ 찬성으로 나갔다 — 이사회측 후보와
주주측 후보가 같은 자리에서 맞붙었는데 「둘 다 찬성」은 실행할 수 없는 지시서다.

위양성 감시 종목은 금호석유화학(011780)이다 — 전원 이사회제안이라 **아무 관계도 붙으면 안 된다.**
"""
from __future__ import annotations

from open_proxy_mcp.services.proxy_advise import _apply_relation_links
from open_proxy_mcp.services.shareholder_meeting import (
    _is_removal_title,
    build_agenda_relation_links,
)


def _row(no, title, cat, pt, parent=""):
    return {"number": no, "title": title, "category": cat,
            "proposer_type": pt, "parent_number": parent}


# 대림제지 20260814003207 — 소집공고 부의안건 구조(발췌)
DAELIM_ROWS = [
    _row("제1호", "감사위원 해임의 건 [주주제안]", "shareholder_proposal", "shareholder_proposal"),
    _row("제2호", "감사위원 해임의 건 [주주제안]", "shareholder_proposal", "shareholder_proposal"),
    _row("제3호", "감사위원 해임의 건 [주주제안]", "shareholder_proposal", "shareholder_proposal"),
    _row("제4호", "독립이사 선임의 건(일반선출) [이사회제안]", "director_election", "company"),
    _row("제5호", "감사위원이 되는 독립이사 선임의 건(분리선출) [주주제안]",
         "audit_committee_election", "shareholder_proposal"),
    _row("제6호", "감사위원이 되는 독립이사 선임의 건(분리선출) [주주제안]",
         "audit_committee_election", "shareholder_proposal"),
    _row("제7호", "이사 중 감사위원회 위원 선임의 건 [이사회제안]",
         "audit_committee_election", "company"),
]
DAELIM_NOTICE = (
    "제1호 의안 : 감사위원 해임의 건 [주주제안] 이주철 사외이사 겸 감사위원 "
    "제4호 의안 : 독립이사 선임의 건(일반선출) [이사회제안] 이민규 독립이사 후보 회계사 "
    "제5호 의안 : 감사위원이 되는 독립이사 선임의 건(분리선출) [주주제안] 전우석 "
    "제6호 의안 : 감사위원이 되는 독립이사 선임의 건(분리선출) [주주제안] 한병우 "
    "제7호 의안 : 이사 중 감사위원회 위원 선임의 건 [이사회제안] 이민규 "
    "감사위원회 위원 후보(이사) 제4호 의안에서 선임된 독립이사 중 선임"
)

# 금호석유화학 — 전원 이사회제안. 위양성 감시용.
KUMHO_ROWS = [
    _row("제3호", "감사위원회 위원이 되는 사외이사 양정원 선임의 건", "director_election", "company"),
    _row("제4호", "사외이사 2명 선임의 건", "director_election", "company"),
    _row("제4-1호", "사외이사 김재희크리스틴 선임의 건", "director_election", "company", "제4호"),
    _row("제4-2호", "사외이사 박순애 선임의 건", "director_election", "company", "제4호"),
    _row("제5호", "이사 보수한도 승인의 건", "director_compensation", "company"),
]


def test_contested_seats_are_linked_both_ways():
    """대림제지 — 주주측 제5·6호와 이사회측 제7호는 같은 감사위원 자리를 다툰다."""
    links = build_agenda_relation_links(DAELIM_ROWS, DAELIM_NOTICE)
    for no, other in (("제5호", "제7호"), ("제6호", "제7호")):
        con = [l for l in links[no] if l["type"] == "contested"]
        assert con and con[0]["with"] == [other]
        assert "둘 다 찬성할 수 없습니다" in con[0]["note"]
    con7 = [l for l in links["제7호"] if l["type"] == "contested"]
    assert con7 and set(con7[0]["with"]) == {"제5호", "제6호"}


def test_removal_makes_the_election_dependent():
    """해임이 부결되면 그 자리 선임은 상정 자체가 무의미해질 수 있다 — 따로 판단하지 않는다."""
    links = build_agenda_relation_links(DAELIM_ROWS, DAELIM_NOTICE)
    dep = [l for l in links["제5호"] if l["type"] == "depends_on"]
    assert dep and set(dep[0]["with"]) == {"제1호", "제2호", "제3호"}
    assert any(l["type"] == "precedes" for l in links["제1호"])
    # 「독립이사 선임(일반선출)」은 감사위원 자리가 아니다 — 해임에 걸리지 않는다.
    assert not [l for l in links.get("제4호", []) if l["type"] == "depends_on"]


def test_body_cross_reference_is_caught():
    """조건부 상정 문구는 제목이 아니라 후보자 표 비고칸에 있다 — 원문을 읽어야 잡힌다."""
    links = build_agenda_relation_links(DAELIM_ROWS, DAELIM_NOTICE)
    cond = [l for l in links["제7호"] if l["type"] == "conditional_on"]
    assert cond and cond[0]["with"] == ["제4호"]
    assert "제4호 의안에서 선임된" in cond[0]["note"]


def test_no_contest_when_one_side_only():
    """🔴 위양성 감시 — 금호석유화학은 전원 이사회제안이라 아무 관계도 붙으면 안 된다."""
    assert build_agenda_relation_links(KUMHO_ROWS, "") == {}


def test_amendment_mentioning_removal_is_not_a_removal():
    """「감사위원 선·해임 시 의결권 제한 강화의 건」은 정관변경이지 해임이 아니다.

    글자만 보면 걸린다 — 실측 한국앤컴퍼니 제2-5호·태광산업 제2-2호가 그렇게 걸려
    감사위원 선임 안건 전부에 「가결이 전제」가 붙었다.
    """
    assert _is_removal_title("감사위원 해임의 건 [주주제안]")
    assert not _is_removal_title("감사위원 선·해임 시 의결권 제한 강화의 건", "articles_amendment")
    assert not _is_removal_title("감사위원 선·해임 시 의결권 제한 조항 삽입의 건",
                                 "articles_amendment")


def test_footnote_enumeration_does_not_bind_an_unrelated_agenda():
    """공고 꼬리의 ※ 각주가 옆 안건의 조건절로 읽히면 안 된다(실측 태광산업 제7호).

    나열에 **자기 번호가 함께 있어야** 택일로 본다.
    """
    rows = [
        _row("제4-1호", "감사위원회 위원이 되는 사외이사 채이배 선임의 건(임기 3년)",
             "audit_committee_election", "shareholder_proposal", "제4호"),
        _row("제4-2호", "감사위원회 위원이 되는 사외이사 안효성 선임의 건 (임기 3년)",
             "audit_committee_election", "company", "제4호"),
        _row("제7호", "이사 보수한도 승인의 건", "director_compensation", "company"),
    ]
    notice = (
        "제4-1호 의안 : 감사위원회 위원이 되는 사외이사 채이배 선임의 건(임기 3년) "
        "제4-1호, 제4-2호 의안에 대해서는 보통결의 요건 충족 의안이 복수일 경우 "
        "다득표 의안이 가결된 것으로 합니다. "
        "제4-2호 의안 : 감사위원회 위원이 되는 사외이사 안효성 선임의 건 (임기 3년) "
        "제7호 의안 : 이사 보수한도 승인의 건"
        "※ 제4-1호, 제4-2호 의안에 대해서는 보통결의 요건 충족 의안이 복수일 경우 "
        "다득표 의안이 가결된 것으로 합니다."
    )
    links = build_agenda_relation_links(rows, notice)
    assert [l for l in links["제4-1호"] if l["type"] == "conditional_on"]
    assert not [l for l in links.get("제7호", []) if l["type"] == "conditional_on"]


def test_contested_row_never_ships_a_for():
    """관계가 걸린 안건은 혼자 판정하지 않는다 — 찬성은 거두고 진영과 선택지를 준다."""
    rows = [
        {"agenda_title": "감사위원이 되는 독립이사 선임의 건(분리선출)", "decision": "FOR",
         "reason": "묶음 안건 — 결격사유 없음", "agenda_relation_links": [
             {"type": "contested", "with": ["제7호"], "note": "제7호와 맞섭니다."}]},
        {"agenda_title": "주식분할 승인의 건", "decision": "FOR", "reason": "액면분할",
         "agenda_relation_links": []},
    ]
    notes = _apply_relation_links(rows)
    assert rows[0]["decision"] == "REVIEW"
    assert rows[0]["relation_downgraded_from"] == "FOR"
    assert "제7호와 경합" in rows[0]["agenda_relation_label"]
    assert rows[1]["decision"] == "FOR", "관계가 없는 안건까지 내리면 던져야 할 표를 지운다"
    assert notes


def test_relation_never_overrides_a_no():
    """반대는 관계와 무관하다 — 결격이 발견된 안건은 경합이어도 반대다."""
    rows = [{"agenda_title": "x", "decision": "AGAINST", "reason": "결격사유 발견",
             "agenda_relation_links": [
                 {"type": "contested", "with": ["제2호"], "note": "n"}]}]
    _apply_relation_links(rows)
    assert rows[0]["decision"] == "AGAINST"


def test_render_says_it_could_not_read_the_relations():
    """관계를 못 잡았으면 조용히 넘어가지 않는다 — 안건 목록과 갈 길을 남긴다."""
    from open_proxy_mcp.tools.proxy_advise_before_meeting import _render
    payload = {
        "subject": "테스트",
        "data": {
            "meeting_info": {},
            "agenda_decisions": [
                {"agenda_title": "감사위원 선임의 건", "decision": "REVIEW", "reason": "r",
                 "proposer_type": "shareholder_proposal"}],
            "agenda_relation_gap": {
                "note": "주주제안 선임 안건이 올라와 있는데 안건 사이의 관계를 판정하지 못했습니다",
                "agenda_list": ["1 감사위원 선임의 건 [주주제안]"],
                "where_to_look": ["소집공고 「회의목적사항 / 부의안건」 절"],
            },
        },
    }
    out = _render(payload)
    assert "판정하지 못했습니다" in out
    assert "감사위원 선임의 건 [주주제안]" in out
    assert "회의목적사항" in out


def test_proposer_column_is_rendered():
    """이사회제안 / 주주제안 — 이 한 칸만 있어도 판이 보인다."""
    from open_proxy_mcp.tools.proxy_advise_before_meeting import _render
    payload = {"subject": "테스트", "data": {"meeting_info": {}, "agenda_decisions": [
        {"agenda_title": "사외이사 A 선임의 건", "decision": "FOR", "reason": "r",
         "proposer_type": "company"},
        {"agenda_title": "사외이사 B 선임의 건", "decision": "REVIEW", "reason": "r",
         "proposer_type": "shareholder_proposal"},
    ]}}
    out = _render(payload)
    assert "| # | 안건 | 제안 | 관계 | 행사방향 | 사유 |" in out
    assert "| 이사회 |" in out and "| **주주** |" in out
