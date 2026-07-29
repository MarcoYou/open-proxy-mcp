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
    for t in ("삼성전자 사외이사", "A사 대표이사", "B사 사내이사",
              "C사 상무이사", "D사 감사", "E사 이사회 의장"):
        assert _is_board_role(t), t


def test_ceo_and_foundation_chair_are_not_proof_of_board_seat():
    """상법 §317②8호가 정한 등기 지위에 「CEO」·「이사장」은 없다.

    「CEO」는 사규상 호칭이라 미등기 집행임원일 수 있고(§401조의2①3호가 그 존재를 전제),
    「이사장」은 학교법인·재단·공단의 대표라 상법상 회사 이사가 아니다.
    260729 실측: 소집공고 경력 7,617개 중 등기를 명시한 것은 15개(0.20%)뿐이고,
    이 텍스트 추정과 사업보고서 임원현황의 일치율은 43%(30사 77명)였다.
    확정은 `apply_roster_board_tenure`(정형 데이터)가 한다.
    """
    for t in ("(주)LG화학 CEO 겸 첨단소재사업본부장 사장",
              "학교법인 광운학원 이사장", "한국해양교통안전공단 이사장"):
        assert not _is_board_role(t), t


def test_audit_agenda_wording_is_not_a_board_role():
    """「감사보고」·「감사결과」는 안건 문구지 직위가 아니다 — 단독 「감사」와 구분한다."""
    for t in ("감사보고서 첨부", "감사결과 보고의 건", "감사의견 적정"):
        assert not _is_board_role(t), t


def test_audit_org_names_are_not_board_roles():
    """감사원·감사실·감사본부·감사팀은 조직명이다 — 등기 감사와 무관(실측 오탐 16%)."""
    for t in ("감사원 정책자문위원회 위원", "현대자동차 그룹감사실 감사2팀",
              "삼일회계법인 감사본부 Director"):
        assert not _is_board_role(t), t


def test_registered_tenure_comes_from_the_annual_report_roster():
    """등기 재직 시작은 정형 데이터(임원현황)로 확정한다 — 경력란 추정을 덮는다."""
    from open_proxy_mcp.services.director_evaluation import apply_roster_board_tenure
    ev = {"appointment_type": {"type": "renewed", "board_earliest_start": 2018}}
    roster = {"김동춘": [{"birth": (1968, 3), "director_type": "사내이사",
                        "tenure": "10개월", "major_shareholder_relation": ""}]}
    apply_roster_board_tenure(ev, {"name": "김동춘", "birthDate": "1968-03-01"}, roster, 2025)
    apt = ev["appointment_type"]
    assert apt["board_earliest_start"] == 2025, apt
    assert apt["board_tenure_source"]["director_type"] == "사내이사"
    assert apt["board_tenure_source"]["notice_estimate"] == 2018  # 덮기 전 값을 남긴다


def test_unregistered_only_roster_means_no_board_tenure():
    """임원현황에 미등기로만 있으면 등기 재직 없음이 **확정**된다 — 추정값을 지운다.

    hffc_pd 를 등기 구분 없이 쓰면 안 되는 이유이기도 하다: 미등기 행에도 연수가 찍힌다
    (260729 실측 640건 · 삼성중공업 미등기 부사장 「6년」).
    """
    from open_proxy_mcp.services.director_evaluation import apply_roster_board_tenure
    ev = {"appointment_type": {"type": "renewed", "board_earliest_start": 2011}}
    roster = {"홍길동": [{"birth": (None, None), "director_type": "미등기임원",
                        "tenure": "6년", "major_shareholder_relation": ""}]}
    apply_roster_board_tenure(ev, {"name": "홍길동", "birthDate": None}, roster, 2025)
    assert ev["appointment_type"]["board_earliest_start"] is None
    assert "미등기" in ev["appointment_type"]["board_tenure_source"]["note"]


def test_tenure_field_formats_seen_in_real_filings():
    """재직기간 서식이 회사마다 다르다 — 실측 분포(30사 861행)를 전부 읽어야 한다.

    날짜 58% · 단위 없는 숫자 20% · 「N년」 14% · 「N개월」 7%.
    처음 구현은 「N년/N개월」만 봐서 **58%를 조용히 버렸다**(LG화학 천경훈 '2023.03.28~').
    """
    from open_proxy_mcp.services.director_evaluation import _roster_board_start_year as f
    for raw, want in (("2019.01.01~", 2019),        # 날짜 — 가장 흔하고 가장 정확
                      ("2023년 3월 13일~", 2023),
                      ("2022년 05월\n~", 2022),      # 개행 포함
                      ("08.04.01 ~ 현재", 2008),     # 2자리 연도
                      ("14년", 2011), ("5년 1개월", 2020), ("2.1년", 2023),
                      ("4", 2021),                  # 단위 없는 숫자 = 재직 연수
                      ("46개월", 2022),              # 2025-12-31 기준 46개월 전 = 2022-02
                      ("10개월", 2025),
                      ("-", None), ("", None)):
        assert f(raw, 2025)[0] == want, (raw, f(raw, 2025))


def test_roster_absence_does_not_overwrite_the_notice_estimate():
    """임원현황에 없으면(신임·매칭 실패) 추정을 덮지 않는다 — 침묵 삭제 금지."""
    from open_proxy_mcp.services.director_evaluation import apply_roster_board_tenure
    ev = {"appointment_type": {"type": "renewed", "board_earliest_start": 2019}}
    apply_roster_board_tenure(ev, {"name": "없는사람", "birthDate": None},
                              {"다른사람": [{"director_type": "사내이사", "tenure": "3년"}]}, 2025)
    assert ev["appointment_type"]["board_earliest_start"] == 2019
    assert "board_tenure_source" not in ev["appointment_type"]


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
