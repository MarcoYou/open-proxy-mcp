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


def test_roster_sources_go_from_freshest_to_oldest():
    """roster 사다리는 **신선한 것부터** 시도하고 없으면 내려간다.

    FY(N-1) 사업보고서가 1순위다. 소집공고(중앙값 3/13)보다 늦게 나오지만(3/23),
    「누가 언제 등기됐나」는 그때 이미 주총결과 공시로 공개된 사실이라 look-ahead 가 아니다
    — 감사 전이라 그때 알 수 없는 재무제표(`fin_year = target_year - 2`)와는 다르다.
    없으면 빈 응답이 오고 분기·반기(분기 종료 후 45일 제출)로 내려간다.
    FY(N-2)는 최후여야 한다 — 오래된 스냅샷일수록 그 뒤 승진분을 놓친다.
    """
    from open_proxy_mcp.services.director_evaluation import _ROSTER_SOURCES
    assert _ROSTER_SOURCES[0][:2] == (1, "11011"), "FY(N-1) 사업보고서가 1순위"
    backs = [b for b, *_ in _ROSTER_SOURCES]
    assert backs == sorted(backs), "오래된 사업연도가 앞에 오면 안 된다"
    assert _ROSTER_SOURCES[-1][0] == 2, "FY(N-2)는 최후 rung"
    # 각 rung 의 기준일 월이 그 보고서와 맞아야 개월 역산이 정확하다
    for _back, code, _label, ref_month in _ROSTER_SOURCES:
        assert ref_month == {"11011": 12, "11014": 9, "11012": 6, "11013": 3}[code]


def test_old_snapshot_must_not_declare_no_board_service():
    """FY(N-2) 스냅샷으로 「등기 재직 없음」을 단정하면 안 된다 — 그 뒤 승진해 등기됐을 수 있다.

    실측 등기 190행 중 28행(14.7%)이 직전 1년 내 시작이다.
    """
    from open_proxy_mcp.services.director_evaluation import apply_roster_board_tenure
    roster = {"홍길동": [{"birth": (1970, 3), "director_type": "미등기임원", "tenure": "6년"}]}
    cand = {"name": "홍길동", "birthDate": "1970-03-01"}
    old = {"appointment_type": {"type": "renewed", "board_earliest_start": 2011}}
    apply_roster_board_tenure(old, cand, roster, 2024, can_confirm_unregistered=False)
    assert old["appointment_type"]["board_earliest_start"] == 2011, "추정을 지우면 안 된다"
    assert "이후 변동 가능" in old["appointment_type"]["board_tenure_source"]["note"]
    fresh = {"appointment_type": {"type": "renewed", "board_earliest_start": 2011}}
    apply_roster_board_tenure(fresh, cand, roster, 2025, can_confirm_unregistered=True)
    assert fresh["appointment_type"]["board_earliest_start"] is None  # 최신이면 확정


def test_unknown_registration_wording_is_not_read_as_unregistered():
    """「등기」·「집행임원」처럼 해석 못 하는 표기를 「미등기 확정」으로 내면 안 된다."""
    from open_proxy_mcp.services.director_evaluation import apply_roster_board_tenure
    for wording in ("등기", "집행임원"):
        ev = {"appointment_type": {"type": "renewed", "board_earliest_start": 2015}}
        apply_roster_board_tenure(
            ev, {"name": "홍길동", "birthDate": "1970-03-01"},
            {"홍길동": [{"birth": (1970, 3), "director_type": wording, "tenure": "5년"}]}, 2025)
        note = ev["appointment_type"]["board_tenure_source"]["note"]
        assert "확정하지 못했" in note, (wording, note)
        assert ev["appointment_type"]["board_earliest_start"] == 2015


def test_tenure_that_implies_an_implausible_age_is_rejected():
    """「재직기간」에 입사 근속을 적는 회사가 있다 — 취임연령이 비상식적이면 시작연도를 버린다.

    실측 30사 등기 190행: 취임연령 30세 미만 11행(5.8%), 최악은 **19세 전무이사**.
    같은 회사 미등기 상무·전무도 「18년」·「19년」을 쓴다 = 근속연수다.
    날짜형도 안전하지 않다(넥센타이어 「1990.05.28~」 = 그해 24세).
    이걸 등기 기간으로 쓰면 비등기 시절을 개인에게 묻는 **원래 버그를 더 크게 재도입**한다.
    """
    from open_proxy_mcp.services.director_evaluation import apply_roster_board_tenure
    ev = {"appointment_type": {"type": "renewed", "board_earliest_start": None}}
    apply_roster_board_tenure(
        ev, {"name": "윤성임", "birthDate": "1971-01-01"},
        {"윤성임": [{"birth": (1971, 1), "director_type": "사내이사", "tenure": "35년"}]}, 2025)
    apt = ev["appointment_type"]
    assert apt["board_earliest_start"] is None, "19세 취임은 채택하면 안 된다"
    assert apt["board_tenure_source"]["rejected_start"] == 1990
    assert "19세" in apt["board_tenure_source"]["note"]


def test_earliest_of_multiple_board_rows_wins():
    """등기 행이 여럿이면 **가장 이른** 시작을 쓴다 — 성과 귀속은 등기 기간 전체다."""
    from open_proxy_mcp.services.director_evaluation import apply_roster_board_tenure
    ev = {"appointment_type": {"type": "renewed", "board_earliest_start": None}}
    apply_roster_board_tenure(
        ev, {"name": "홍길동", "birthDate": "1965-05-01"},
        {"홍길동": [{"birth": (1965, 5), "director_type": "사내이사", "tenure": "2020.03.20~"},
                   {"birth": (1965, 5), "director_type": "기타비상무이사", "tenure": "2012.03.15~"}]},
        2025)
    assert ev["appointment_type"]["board_earliest_start"] == 2012


def test_month_arithmetic_respects_the_report_cutoff_date():
    """개월 역산은 기준일 월을 따라야 한다 — 3분기(9/30)와 사업보고서(12/31)는 다르다."""
    from open_proxy_mcp.services.director_evaluation import _roster_board_start_year as f
    # 사업보고서 2025 (기준 2025-12): 46개월 전 = 2022-02
    assert f("46개월", 2025, 12)[0] == 2022
    assert f("10개월", 2025, 12)[0] == 2025          # 2025-02
    assert f("13개월", 2025, 12)[0] == 2024          # 2024-11
    # 3분기보고서 2025 (기준 2025-09): 같은 값이라도 한 해 앞선다
    assert f("46개월", 2025, 9)[0] == 2021           # 2021-11
    assert f("10개월", 2025, 9)[0] == 2024           # 2024-11
    assert f("9개월", 2025, 9)[0] == 2024            # 2024-12
    assert f("3개월", 2025, 9)[0] == 2025            # 2025-06
    # 날짜형(실측 58%)은 기준일과 무관하게 원문 그대로
    assert f("2019.01.01~", 2025, 9)[0] == 2019


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


def test_company_match_reads_the_raw_career_text():
    """이 회사 재직 매칭은 **경력 원문**으로 한다 — 쪼갠 회사명은 쓰지 않는다.

    260730 전수 비교(후보 2,552명): 쪼갠 그룹을 빼고 원문만 써도 type·earliest_start·
    board_earliest_start·outside_earliest_start·match_source 가 100% 동일했다.
    반대로 쪼개기는 「기타비상무이사」를 「기타비」+「상무이사」로, 「검사장」을 「사장」으로
    찢어 판정 어휘 자체를 훼손했다.
    회사명이 든 항목만 이 회사 재직으로 센다(다른 회사 기간이 섞이면 안 된다).
    """
    from open_proxy_mcp.services.director_evaluation import detect_appointment_type
    cand = {"name": "박기덕", "careerDetails": [
        {"period": "2024 ~ 현재", "content": "케이지그린텍㈜ 기타비상무이사고려아연㈜ 사장"},
        {"period": "2019 ~ 2021", "content": "무관회사㈜ 상무"}]}
    r = detect_appointment_type(cand, "고려아연", 2026)
    assert r["type"] == "renewed", r
    assert r["matched_entries"], "원문에 회사명이 있으면 매칭돼야 한다"
    assert r["earliest_start"] == 2024, r   # 무관회사 2019 는 세지 않는다


def test_leading_corp_name_keeps_the_whole_name():
    r"""회사명 추출은 원문 앞머리를 통째로 — 쪼갠 필드를 쓰던 때는 이름이 잘리거나 사라졌다.

    실측: `re.split(r"[,，\(]")` 가 「(주)카카오」에서 빈 문자열을 내 **17.1%** 가 조회에서
    통째로 빠졌고, 「대한변호사협회」→「대한」·「금융위원회」→「금융」으로 잘려 DART 회사검색
    유효 조회가 27.1% 뿐이었다.
    """
    from open_proxy_mcp.services.director_evaluation import _leading_corp_name as f
    assert f("(주)카카오 대표이사") == "(주)카카오"
    assert f("(주) 풀무원 사외이사") == "(주)풀무원"     # 법인표기 뒤 공백
    assert f("㈜광무 사내이사") == "㈜광무"
    assert f("대한변호사협회 부회장") == "대한변호사협회"
    assert f("현) ㈜애셔코퍼레이션 대표이사") == "㈜애셔코퍼레이션"   # 시점 마커 제거
    assert f("") == "" and f("- ") == ""
def test_interim_diff_reports_board_changes_only():
    """직전 사업보고서 이후 변동도 **이사회(등기)만** 싣는다.

    260709 QA: 대형사 상무 인사이동이 이사회 이탈로 오독됐다. 연간 diff 는 그때 갈랐는데,
    260730 에 기중 diff 를 더하면서 같은 실수를 되풀이할 뻔했다(LG화학 첫 구현에서
    상무·담당·명예회장만 잔뜩 나왔다). 집행임원은 건수만 요약한다.
    """
    from open_proxy_mcp.services.director_board import _diff_roster_rows, _BOARD_TYPES
    prev = [{"nm": "고윤주", "birth_ym": "1970년 03월", "ofcps": "전무", "rgist_exctv_at": "미등기"},
            {"nm": "유명희", "birth_ym": "1967년 08월", "ofcps": "이사", "rgist_exctv_at": "사외이사"}]
    curr = [{"nm": "김용관", "birth_ym": "1966년 01월", "ofcps": "사장", "rgist_exctv_at": "사내이사"}]
    changes = _diff_roster_rows(prev, curr, joined_label="신규", left_label="이탈")
    board = [c for c in changes if c["director_type"] in _BOARD_TYPES]
    assert {c["name"] for c in board} == {"김용관", "유명희"}
    assert all(c["name"] != "고윤주" for c in board), "미등기 전무는 이사회 변동이 아니다"


def test_two_pass_diff_survives_a_shared_birth_month():
    """이름이 다른 잔류자와 이탈자의 생년월이 같아도 이탈을 놓치지 않는다(QA 260709 회귀)."""
    from open_proxy_mcp.services.director_board import _diff_roster_rows
    prev = [{"nm": "윤치원", "birth_ym": "1959년 06월", "ofcps": "이사", "rgist_exctv_at": "사외이사"},
            {"nm": "심달훈", "birth_ym": "1959년 06월", "ofcps": "이사", "rgist_exctv_at": "사외이사"}]
    curr = [{"nm": "심달훈", "birth_ym": "1959년 06월", "ofcps": "이사", "rgist_exctv_at": "사외이사"}]
    changes = _diff_roster_rows(prev, curr, joined_label="신규", left_label="이탈")
    assert [c["name"] for c in changes] == ["윤치원"], changes
