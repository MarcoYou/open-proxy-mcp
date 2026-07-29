# -*- coding: utf-8 -*-
"""성과 귀속은 **등기이사 재직 기간**에만. network 0콜.

260729 사용자 지적(LG화학 김동춘): 툴이 「재직 2018~2026(9년)」으로 전사 ROE·부채비율을
개인에게 귀속해 REVIEW 를 냈다. 실제 소집공고 세부경력은
  2026~현재 CEO 겸 첨단소재사업본부장 사장   ← 등기는 여기부터
  2025~2025 첨단소재사업본부장 부사장        ← 비등기
  ...
  2018~2019 고기능소재사업부장 상무          ← 비등기
호출측 AI 가 이걸 알아채고 「수동 오버라이드」를 해야 했다.
"""
from __future__ import annotations

from open_proxy_mcp.services.director_evaluation import _is_board_role


def test_executive_titles_are_not_board_roles():
    """집행임원(비등기)은 성과 귀속 대상이 아니다."""
    for t in ("(주)LG화학 첨단소재사업본부장 부사장",
              "(주)LG화학 전자소재사업부장 전무",
              "(주)LG화학 반도체소재사업담당 상무",
              "A사 팀장", "B사 실장", "C사 공장장"):
        assert not _is_board_role(t), t


def test_board_titles_are_detected():
    for t in ("(주)LG화학 CEO 겸 첨단소재사업본부장 사장",
              "삼성전자 사외이사", "A사 대표이사", "B사 사내이사",
              "C사 상무이사", "D사 감사", "E사 이사회 의장"):
        assert _is_board_role(t), t


def test_audit_agenda_wording_is_not_a_board_role():
    """「감사보고」·「감사결과」는 안건 문구지 직위가 아니다 — 단독 「감사」와 구분한다."""
    for t in ("감사보고서 첨부", "감사결과 보고의 건", "감사의견 적정"):
        assert not _is_board_role(t), t


def test_career_groups_keep_the_raw_text():
    """회사/직위 분리가 「(주)LG화학 CEO 겸 첨단소재사업」/「본부장 사장」으로 잘라
    CEO 를 회사명 쪽으로 가져간다 — 원문을 함께 남겨야 등기 판별이 성립한다."""
    from open_proxy_mcp.services.shareholder_meeting_parser import _build_career_company_groups
    groups = _build_career_company_groups(
        [{"period": "2026~현재", "content": "(주)LG화학 CEO 겸 첨단소재사업본부장 사장"}])
    items = [i for g in groups for i in g["items"]]
    assert any(_is_board_role(i) for i in items), items


def test_not_evaluated_renders_without_none_values():
    """「평가 안 함」을 「저조」처럼 보이게 하거나 None 을 찍으면 안 된다."""
    from open_proxy_mcp.tools.proxy_advise_before_meeting import _render
    out = _render({"status": "ok", "subject": "테스트", "data": {"year": 2026,
        "agenda_count": 0, "candidates_count": 1,
        "candidates_evaluations": [{"name": "김동춘", "role_type": "inside",
            "performance": {"classification": "not_evaluated",
                            "rationale": "등기이사 재직이 2026년부터라 평가할 사업연도가 부족합니다.",
                            "tenure_period": None}}]}})
    assert "not_evaluated" not in out
    assert "None" not in out
    assert "평가하지 않음" in out and "사업연도가 부족" in out
