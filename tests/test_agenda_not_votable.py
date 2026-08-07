"""안건이 아닌 것에 표를 던지지 않는다.

폐기된 안건·조건절 조각·상호배타 시나리오 중복·유령 후보에 찬성이 나가면, 틀린 판단이 아니라
**실행할 수 없는 지시서**가 된다. 실측에서 고려아연은 최대 6석에 16표를 던졌고, 사퇴한 후보의
폐기 안건에 두 번 찬성했다.
"""

from __future__ import annotations

from open_proxy_mcp.services.shareholder_meeting import _agenda_nodes, _agenda_relation
from open_proxy_mcp.services.shareholder_meeting_parser import _is_valid_candidate_name

_WITHDRAWN = "사외이사 오영 선임의 건→ 오영 후보자 일신상의 사유로 자진 사퇴함에 따라 안건 폐기"


def test_a_withdrawn_agenda_is_not_up_for_a_vote() -> None:
    assert _agenda_relation(_WITHDRAWN)[0] == "withdrawn"
    assert _agenda_relation("이사 보수 한도 승인의 건은 자동 폐기")[0] == "withdrawn"


def test_withdrawal_outranks_the_conditional_clause_it_came_with() -> None:
    """「~은 자동 폐기 -본 안건 가결 되는 경우」 — 이미 내려간 안건에 조건부를 얹을 이유가 없다."""
    title = "양도제한조건부 주식 부여의 건은 자동 폐기 -본 안건 가결 되는 경우"
    assert _agenda_relation(title)[0] == "withdrawn"


def test_a_spaced_conditional_still_reads_as_conditional() -> None:
    """「가결 되는 경우」를 놓쳐 조건절이 미분류 자동 찬성으로 샜다(BNK금융지주)."""
    assert _agenda_relation("이사 보수 한도 승인의 건 -본 안건 부결 되는 경우")[0] == "conditional"


def test_children_of_a_mutually_exclusive_slate_inherit_it() -> None:
    """부모 24(5인)·33(6인)은 ⚠️ 로 잡혔는데 자식 16명이 전원 찬성이었다."""
    nodes = _agenda_nodes([{
        "number": "제3-2호", "title": "이사 5인 선임의 건",
        "children": [{"number": "제3-2-1호", "title": "사외이사 김철수 선임의 건", "children": []}],
    }])
    assert nodes[0]["agenda_relation_type"] == "alternative"
    child = nodes[0]["children"][0]
    assert child["agenda_relation_type"] == "alternative"
    assert "inherited_from_parent:alternative" in child["agenda_relation_reasons"]


def test_a_child_keeps_its_own_stronger_signal() -> None:
    """상속은 빈자리만 채운다 — 자식이 스스로 폐기라고 밝히면 그쪽이 맞다."""
    nodes = _agenda_nodes([{
        "number": "제3-2호", "title": "이사 5인 선임의 건",
        "children": [{"number": "제3-2-1호", "title": _WITHDRAWN, "children": []}],
    }])
    assert nodes[0]["children"][0]["agenda_relation_type"] == "withdrawn"


def test_a_normal_slate_does_not_become_alternative() -> None:
    nodes = _agenda_nodes([{
        "number": "제3호", "title": "이사 선임의 건",
        "children": [{"number": "제3-1호", "title": "사내이사 박영희 선임의 건", "children": []}],
    }])
    assert nodes[0]["children"][0]["agenda_relation_type"] == "normal"


def test_a_job_title_is_not_a_person() -> None:
    """코웨이 「사외이사 이사회 의장 선임의 건」이 후보 「이사회 의장」을 만들고 독립성까지 매겼다."""
    for ghost in ("이사회 의장", "감사위원회", "구성", "전원", "사외이사 후보자"):
        assert _is_valid_candidate_name(ghost) is False, ghost


def test_real_names_that_look_like_role_words_survive() -> None:
    """「구성」·「선임」은 이름에도 쓰인다 — 부분 일치로 막으면 실재 후보가 조용히 사라진다."""
    for name in ("박구성", "김선임", "홍길동", "안효성", "KIM EUNICE KYONGHEE"):
        assert _is_valid_candidate_name(name) is True, name
