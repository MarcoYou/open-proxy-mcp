# -*- coding: utf-8 -*-
"""안건 분류 구멍 — `other` 로 새면 **자동 찬성**이 나간다. network 0콜.

`other` 는 「위험 키워드가 없으면 찬성」이다. 이 도구가 「지시서」가 아니라 **의견**이고
루틴 안건을 흘려보내는 게 설계이기 때문이다(제품 결정). 그래서 위험한 것은
**분류에서 새지 않는 것**과 **키워드에 걸리는 것** 둘로만 막힌다.

여기가 반복해서 뚫렸다:
  260724  자본감소·주식병합        「감자」 표기를 안 씀
  260727  합병계약·분할계획서       분기가 없었음
  260811  전환사채 발행            키워드가 「전환사채발행」(붙여쓰기)이라 안 걸림
  260814  후보자 이름만 온 자식      부모 상속 조건이 제목에도 직위를 요구
  260814  의장 불신임·주주제안·양도   키워드 목록에 없었음
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from open_proxy_mcp.services.proxy_advise import _classify_agenda

_SRC = (pathlib.Path(__file__).resolve().parent.parent
        / "open_proxy_mcp" / "services" / "proxy_advise.py").read_text(encoding="utf-8")


def _risk_keywords() -> list[str]:
    """**코드에서 읽는다.** 목록을 여기 복사하면 이중장부가 되고,
    코드가 바뀌어도 테스트는 옛 목록으로 계속 통과한다."""
    i = _SRC.index("risk_keywords = [")
    return ast.literal_eval(_SRC[i + len("risk_keywords = "): _SRC.index("]", i) + 1])


# ── 분류: 부모 상속 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("title,parent", [
    ("후보자 천시열 선임의 건", "이사 선임의 건"),
    ("오세정(중임)", "사외이사 선임의 건"),
    ("후보자(안의준)", "비상임이사(2인) 선임의 건"),
])
def test_name_only_child_inherits_director_election(title, parent):
    """자식이 이름뿐이면 부모가 유일한 단서다 — 260814 실측 6건이 other 로 샜다."""
    assert _classify_agenda(title, parent_title=parent) == "director_election"


def test_audit_committee_parent_wins_over_child_role():
    """「감사위원회 위원이 되는 사외이사 선임의 건」의 자식은 **감사위원**이다.
    3%룰·독립성으로 더 엄격한 경로라 자식 제목의 「사외이사」에 지면 안 된다."""
    p = "감사위원회 위원이 되는 사외이사 선임의 건"
    assert _classify_agenda("사외이사 정운섭", parent_title=p) == "audit_committee_election"


def test_mixed_parent_defers_to_child_role():
    """부모가 「이사 **및 감사** 선임·해임의 건」이면 부모만으로는 못 가른다."""
    p = "이사 및 감사 선임.해임의 건"
    assert _classify_agenda("사내이사 정집훈 선임의 건", parent_title=p) == "director_election"
    assert _classify_agenda("감사 김기병 선임의 건", parent_title=p) == "audit_committee_election"


def test_articles_parent_still_wins():
    """정관 자식은 종전대로 정관변경 — 260507 fix 를 깨지 않았는지."""
    assert _classify_agenda("사외이사 명칭 변경",
                            parent_title="정관 일부 변경의 건") == "articles_amendment"


# ── 위험 키워드: 중대 안건이 목록에 있는가 ────────────────────────────────────

@pytest.mark.parametrize("kw", ["불신임", "주주제안", "영업양도", "사업양도", "해임",
                                "전환사채", "제3자배정", "유상증자"])
def test_material_agenda_keywords_are_registered(kw):
    """상법상 중대 안건이 목록에서 빠지면 그날로 자동 찬성이 된다."""
    flat = [k.replace(" ", "") for k in _risk_keywords()]
    assert kw.replace(" ", "") in flat, f"「{kw}」가 위험 키워드에서 빠졌다 — 자동 찬성으로 샌다"


def test_keywords_are_matched_without_spaces():
    """공고 표기는 띄어쓰기가 제각각이다 — 260811 「전환사채 발행의 건」이
    키워드 `전환사채발행` 에 안 걸려 사문이었다."""
    src_i = _SRC.index("risk_keywords = [")
    window = _SRC[max(0, src_i - 1200): src_i + 2000]
    assert 't_flat = t.replace(" ", "")' in window
    assert 'kw.replace(" ", "") in t_flat' in window, "키워드를 공백 무시로 비교하지 않는다"


def test_blank_check_delegation_is_flagged():
    """백지 발행권한 위임 — 제목이 길고 표현이 회사마다 달라 단일 키워드로 안 잡힌다.
    희석 결정을 이사회에 통째로 넘기는 안건이라 별도 조건으로 막는다."""
    src_i = _SRC.index('elif "이사회" in t_flat and "위임" in t_flat')
    assert src_i > 0, "백지 발행권한 위임 분기가 사라졌다"
