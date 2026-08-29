"""소집공고 경력 표를 「경력 한 줄 = 한 항목」으로 편다.

표본 10사·후보 26명(2026-08-29) 실측으로 만든 시험이다. 서식이 두 갈래고,
기간 표기가 제각각이며, 어떤 회사는 **구분자를 아예 안 쓴다** — 그때는 짝짓지 않는다.
"""
from __future__ import annotations

from open_proxy_mcp.services.career_normalize import (
    build_careers, norm_period, split_items, split_org_role,
)


def test_period_notations_seen_in_the_wild() -> None:
    cases = {
        "2013년~현재": ("2013-null", None, True),
        "2022년 9월~현재": ("2022-09", None, True),
        "'21 ~ 현재": ("2021-null", None, True),
        "2023.04~": ("2023-04", None, True),
        "1983-2016": ("1983-null", "2016-null", False),
        "2022 ~ 現": ("2022-null", None, True),
        "2015.03~2020.02": ("2015-03", "2020-02", False),
    }
    for raw, (st, en, op) in cases.items():
        p = norm_period(raw)
        assert (p["start"], p["end"], p["open_ended"]) == (st, en, op), raw


def test_single_year_is_a_point_not_a_range() -> None:
    """「2012」는 학위 취득처럼 한 시점이다 — 열린 구간으로 오해하지 않는다."""
    p = norm_period("2012")
    assert p["start"] == p["end"] == "2012-null"
    assert p["open_ended"] is False


def test_bullets_glued_to_previous_word_still_split() -> None:
    """실측 동양고속 — 「근무- ㈜동양고속」처럼 글머리표가 앞말에 붙어 온다."""
    items = split_items("- ㈜동양건설산업 근무- ㈜동양고속 총무부장- ㈜동양고속 사내이사")
    assert len(items) == 3
    assert items[-1].endswith("사내이사")


def test_era_marks_work_as_bullets() -> None:
    """실측 KISCO홀딩스 — 글머리표 없이 現/前 이 항목 머리 노릇을 한다."""
    items = split_items("現 법무법인 태평양前 주택도시보증공사 위원前 기획재정부 위원")
    assert len(items) == 3


def test_org_and_role_are_split() -> None:
    assert split_org_role("(주)무신사 사외이사 (보상위원장)") == ("(주)무신사", "사외이사")
    org, role = split_org_role("UCLA 경제학학사")
    assert role is None and org                     # 역할을 지어내지 않는다


def test_counts_match_then_align() -> None:
    out = build_careers(
        "1990~20062006~20152016~20172018~현재",
        "- ㈜동양건설산업 근무- ㈜동양고속 총무부장- ㈜동양고속 인사부장- ㈜동양고속 사내이사")
    assert out["aligned"] is True
    assert out["careers"][-1]["open_ended"] is True
    assert out["careers"][0]["start"] == "1990-null"


def test_counts_differ_then_do_not_invent_pairs() -> None:
    """구분자가 없어 항목을 못 끊는 표가 있다(실측 에이플러스에셋).

    이때 순서대로 짝지으면 **없는 사실이 생긴다.** 짝짓지 않고 기간을 따로 넘긴다.
    """
    out = build_careers("'13~'16, '20~'25'11~'21'90~'11",
                        "한국금융소비자학회 일반이사라이나생명보험 전무이사매일경제신문 기자")
    assert out["aligned"] is False
    assert out["periods"], "못 붙인 기간은 따로 실어야 한다"
    assert all(c["start"] is None for c in out["careers"])


def test_corporate_prefix_starts_an_item_but_suffix_does_not() -> None:
    """법인 표기는 앞에도 뒤에도 붙는다 — **뒤에 공백이 오는가**로 가른다.

    · 「…석사(주)다온네트웍스 사장」 → 「(주)」가 항목의 머리다 (실측 팜젠사이언스)
    · 「교보생명보험(주) 전무」    → 「(주)」는 이름의 꼬리다 (실측 에이플러스에셋).
      이걸 안 가르면 회사 이름이 두 동강 난다.
    """
    head = split_items("수원대 정보보호학 석사(주)다온네트웍스 사장(주)DSD삼호 총괄사업본부장")
    assert len(head) == 3

    tail = split_items("교보생명보험(주) 대외협력담당 전문위원")
    assert len(tail) == 1
    assert tail[0].startswith("교보생명보험(주)")


def test_era_mark_inside_parentheses_is_not_a_boundary() -> None:
    """「현중기술대학(現 현대중공업공과대학) 졸업」은 한 항목이다(실측 산일전기)."""
    assert len(split_items("현중기술대학(現 현대중공업공과대학) 졸업")) == 1


def test_boundaries_are_looked_at_together() -> None:
    """갈래를 배타적으로 두면 하나만 걸려도 나머지를 놓친다(실측 코오롱글로벌)."""
    items = split_items("前, 코오롱글로벌㈜ 인사/기획 담당임원\n前, ㈜코오롱 자산구조혁신단")
    assert len(items) == 2


def test_standalone_year_between_ranges_is_kept() -> None:
    """「20122018~20222022~2025」 — 앞의 2012 는 학위 취득 같은 시점이다. 빠뜨리지 않는다."""
    from open_proxy_mcp.services.career_normalize import split_periods
    spans, _ = split_periods("20122018~20222022~2025")
    assert len(spans) == 3
    assert spans[0]["start"] == "2012-null"


def test_company_list_is_not_split_at_each_corporate_mark() -> None:
    """「코오롱에코원㈜, 코오롱환경에너지㈜, 코오롱이엔지니어링㈜ 경영전략본부장」은 **한 항목**이다.

    ㈜ 뒤가 쉼표면 앞 이름의 꼬리고, 앞이 「, 」이면 나열의 이어짐이다.
    이 둘을 안 보면 회사 이름마다 잘려 10조각이 났다(실측 코오롱글로벌 이기원).
    """
    items = split_items(
        "前, 코오롱에코원㈜, 코오롱환경에너지㈜, 코오롱이엔지니어링㈜ 경영전략본부장")
    assert len(items) == 1

    two = split_items("前, 코오롱엘에스아이㈜, ㈜엠오디 대표이사")
    assert len(two) == 1, "「, ㈜」는 나열의 이어짐이다"


def test_kolon_row_aligns_end_to_end() -> None:
    out = build_careers(
        "2014~20182019~20202022~20232024~20252025~20252025~현재",
        "前, 코오롱글로벌㈜ 인사/기획 담당임원\n"
        "前, 코오롱에코원㈜, 코오롱환경에너지㈜, 코오롱이엔지니어링㈜ 경영전략본부장/대표이사 직무대행"
        "前, 코오롱글로벌㈜ 라비에벨사업 담당임원前, ㈜코오롱 자산구조혁신단"
        "前, 코오롱엘에스아이㈜, ㈜엠오디 대표이사現, 코오롱글로벌㈜ 공사지원본부장")
    assert out["aligned"] is True
    assert out["item_count"] == 6
    assert out["careers"][-1]["open_ended"] is True
