# -*- coding: utf-8 -*-
"""상호배타 시나리오를 **형제 구조로** 알아보는가. network 0콜.

「집중투표의 방법으로 이사 5인을 선임하는 건」이라는 제목 하나만 보면 그게 시나리오인지
그냥 선거인지 알 수 없다. **옆에 6인안이 같이 올라와 있어야** 비로소 택일 구조다.

260525 에는 이걸 제목 리터럴(`"5인 선임"`·`"6인 선임"`)로 때웠다 — 고려아연 한 회사만
맞히고 4인/7인이면 그대로 뚫리는 임시방편이었다. 260810 에 형제 비교로 바꿨다.

좁게 잡는 이유: **틀리게 묶으면 던져야 할 표를 지운다.** 그건 틀린 표를 내는 것과 같은
크기의 사고다(코드 곳곳의 경고와 같은 종류).
"""
from __future__ import annotations

from open_proxy_mcp.services.shareholder_meeting import _agenda_nodes


def _n(title, children=None, number=""):
    return {"number": number, "title": title, "children": children or []}


def _types(nodes):
    out = []
    for n in nodes:
        out.append((n["title"], n["agenda_relation_type"]))
        out.extend(_types(n["children"]))
    return out


def test_sibling_seat_scenarios_are_marked_and_inherited():
    """**고려아연 모양.** 5인안·6인안이 형제로 올라오면 택일이고, 후보 행까지 물려받아야
    한다 — 자식이 normal 로 남으면 거기서 자동 찬성이 나간다."""
    nodes = _agenda_nodes([
        _n("제3호 의안: 집중투표의 방법으로 이사 5인을 선임하는 건",
           [_n("사외이사 후보 김이태"), _n("사내이사 후보 박서준")], "제3호"),
        _n("제4호 의안: 집중투표의 방법으로 이사 6인을 선임하는 건",
           [_n("사외이사 후보 최민호")], "제4호"),
    ])
    assert all(t == "alternative" for _, t in _types(nodes)), _types(nodes)


def test_seat_numbers_other_than_five_and_six_also_work():
    """리터럴 시절에는 **4인/7인이면 그대로 뚫렸다.** 그게 이 교체의 이유다."""
    nodes = _agenda_nodes([
        _n("집중투표의 방법으로 이사 4인을 선임하는 건", [_n("후보 A")]),
        _n("집중투표의 방법으로 이사 7인을 선임하는 건", [_n("후보 B")]),
    ])
    assert all(t == "alternative" for _, t in _types(nodes)), _types(nodes)


def test_single_cumulative_election_is_not_a_scenario():
    """옆에 다른 안이 없으면 그냥 선거다. 묶으면 정상 주총의 표를 지운다."""
    nodes = _agenda_nodes([
        _n("제2호 의안: 집중투표의 방법으로 이사 3인을 선임하는 건", [_n("사내이사 후보 김철수")]),
    ])
    assert not any(t == "alternative" for _, t in _types(nodes)), _types(nodes)


def test_different_roles_are_complementary_not_exclusive():
    """「사내이사 2인」과 「사외이사 3인」은 **함께 뽑는** 안건이다. 택일이 아니다."""
    nodes = _agenda_nodes([
        _n("집중투표의 방법으로 사내이사 2인을 선임하는 건"),
        _n("집중투표의 방법으로 사외이사 3인을 선임하는 건"),
    ])
    assert not any(t == "alternative" for _, t in _types(nodes)), _types(nodes)


def test_same_seat_count_twice_is_not_a_scenario():
    """인원이 같으면 택일 구조가 아니다(분리선임 등)."""
    nodes = _agenda_nodes([
        _n("집중투표의 방법으로 이사 3인을 선임하는 건"),
        _n("집중투표의 방법으로 이사 3인을 선임하는 건"),
    ])
    assert not any(t == "alternative" for _, t in _types(nodes)), _types(nodes)


def test_hardcoded_seat_literals_are_gone():
    """`"5인 선임"`·`"6인 선임"` 은 고려아연 전용이었다. 되살아나면 그 회사만 맞히는
    상태로 돌아가고, 형제 판정이 있다는 사실이 가려진다."""
    from open_proxy_mcp.services.shareholder_meeting import _AGENDA_ALTERNATIVE_PATTERNS

    assert not any(p and p[0].isdigit() for p in _AGENDA_ALTERNATIVE_PATTERNS), (
        f"인원 리터럴이 남아 있다: {_AGENDA_ALTERNATIVE_PATTERNS}")


# ── 260813 회귀 — 일괄표결·다득표를 못 읽어 한 자리에 양쪽 찬성이 나갔다 ──────────
# 실측 고려아연 20260811000705 제3호:
#   ※ '제3-1호' 및 '제3-2호' 의안은 일괄표결 후 보통결의요건 충족 의안이 복수일 경우
#     다득표 의안이 가결된 것으로 함
# 한 자리를 겨루는 후보 둘에 양쪽 찬성은 중립이 아니라 **기권**이다 —
# 내 표가 두 후보의 격차를 1표도 벌리지 못한다.

def test_conditional_clause_survives_quotes_and_enumeration():
    """따옴표 + 「및」 나열 형태를 못 읽어 조건절이 한 건도 안 잡혔다."""
    from open_proxy_mcp.services.shareholder_meeting_parser import _extract_conditionals
    text = ("※ '제3-1호' 및 '제3-2호' 의안은 일괄표결 후 보통결의요건 충족 의안이 "
            "복수일 경우 다득표 의안이 가결된 것으로 함\n")
    got = _extract_conditionals(text)
    assert "제3-1호" in got, "따옴표가 붙으면 조건절을 못 읽었다"
    assert "제3-2호" in got, "「및」로 나열된 둘째 안건에 조건절이 안 붙었다"


def test_bundled_vote_highest_count_is_alternative():
    """일괄표결·다득표는 상호배타다 — 형제 전체가 검토로 내려가야 한다."""
    from open_proxy_mcp.services.shareholder_meeting import _agenda_relation
    cond = ("'제3-1호' 및 '제3-2호' 의안은 일괄표결 후 보통결의요건 충족 의안이 "
            "복수일 경우 다득표 의안이 가결된 것으로 함")
    rel, reasons = _agenda_relation("감사위원회 위원이 되는 독립이사 백인규 선임의 건", cond)
    assert rel == "alternative", f"상호배타로 안 잡혔다: {rel} {reasons}"
    for word in ("일괄표결", "다득표", "최다득표", "일괄 표결"):
        assert _agenda_relation(f"의안 {word} 관련의 건")[0] == "alternative", word
