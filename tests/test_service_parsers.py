from bs4 import BeautifulSoup

from open_proxy_mcp.services.agm_result_parser import parse_agm_result_table
from open_proxy_mcp.services.dividend_parser import parse_dividend_decision, safe_int
from open_proxy_mcp.services.ownership_parser import (
    parse_holding_purpose,
    parse_holding_purpose_from_document,
)


def test_dividend_number_parser_preserves_embedded_units():
    assert safe_int("1,234백만원") == 1_234_000_000
    assert safe_int("(12억원)") == -1_200_000_000


def test_dividend_decision_parser_extracts_standard_fields():
    text = """
    1. 배당구분 결산배당 2. 배당종류 현금배당
    3. 1주당 배당금(원) 보통주식 1,444 종류주식 1,445
    4. 시가배당율(%) 보통주식 2.7 종류주식 2.8
    5. 배당금총액(원) 9,999,000 6. 배당기준일 2025-12-31
    7. 배당금지급 예정일자 2026-04-20 8. 주주총회 개최여부 개최
    9. 주주총회 예정일자 2026-03-20 10. 이사회결의일(결정일) 2026-02-10
    11. 기타 투자판단과 관련한 중요사항 -
    """
    parsed = parse_dividend_decision(text)
    assert parsed is not None
    assert parsed["dps_common"] == 1_444
    assert parsed["dps_preferred"] == 1_445
    assert parsed["record_date"] == "2025-12-31"


def test_ownership_purpose_parsers_preserve_api_and_document_rules():
    assert parse_holding_purpose("일반", "") == "경영참여"
    assert parse_holding_purpose("약식", "단순투자 목적") == "단순투자"
    assert parse_holding_purpose_from_document('<TU AUNIT="PUR_OWN">일반투자</TU>') == "일반투자"


def test_agm_result_table_parser_preserves_vote_rows():
    soup = BeautifulSoup(
        """
        <table>
          <tr><th>번호</th><th>결의구분</th><th>회의목적사항</th><th>가결여부</th><th>찬성률</th><th>찬성률</th><th>반대기권</th></tr>
          <tr><th></th><th></th><th></th><th></th><th>발행</th><th>행사</th><th></th></tr>
          <tr><td>제1호</td><td>보통</td><td>재무제표 승인</td><td>가결</td><td>80</td><td>90</td><td>10</td></tr>
        </table>
        """,
        "html.parser",
    )
    rows = parse_agm_result_table(soup)
    assert rows[0]["number"] == "제1호"
    assert rows[0]["estimated_attendance"] == 88.9


# ── 260724 안건 분류 L-코드 진단 반영 회귀 ──

def test_classify_capital_reduction_official_wording():
    """DART 공식 표기 '자본의 감소'가 'other'(→mainstream FOR 위험)로 새지 않는다."""
    from open_proxy_mcp.services.proxy_advise import _classify_agenda
    assert _classify_agenda("자본의 감소") == "capital_reduction"
    assert _classify_agenda("자본감소의 건") == "capital_reduction"
    assert _classify_agenda("무상감자 결정의 건") == "capital_reduction"
    assert _classify_agenda("자본금 감액의 건") == "capital_reduction"


def test_classify_reserve_reduction_stays_mainstream():
    """자본준비금 감액(회계 평탄화·배당가능이익 확보)은 종전대로 other — iter12 유지."""
    from open_proxy_mcp.services.proxy_advise import _classify_agenda
    assert _classify_agenda("자본준비금 감액의 건") == "other"
    assert _classify_agenda("이익준비금 감액의 건") == "other"


def test_classify_statutory_auditor_election_path_kept():
    """감사(상근) 선임은 감사위원과 동일 결정 경로 유지 (인용 라벨만 citation에서 구분)."""
    from open_proxy_mcp.services.proxy_advise import _classify_agenda, _is_statutory_auditor_agenda
    assert _classify_agenda("감사의 선임") == "audit_committee_election"
    assert _is_statutory_auditor_agenda("감사의 선임") is True
    assert _is_statutory_auditor_agenda("감사위원회 위원의 선임") is False


def test_render_agenda_source_section_line():
    """260724 provenance 1단계: source_section이 있으면 '근거 위치' 라인이 렌더된다."""
    from open_proxy_mcp.tools.proxy_advise_before_meeting import _render
    payload = {"status": "exact", "subject": "T", "data": {
        "canonical_name": "테스트", "year": 2026, "meeting_type": "annual",
        "candidates_evaluations": [],
        "agenda_decisions": [{
            "agenda_title": "자본의 감소", "agenda_category": "capital_reduction",
            "decision": "REVIEW", "reason": "자본 감소 — 원문 확인 필요", "facts": {},
            "risk_factors": [], "policy_citation": "-", "policy_basis": "-",
            "evidence_rcept_no": "20260101000001",
            "source_section": {"rcept_no": "20260101000001",
                               "section_code": "L0-0-2-19-0", "section_title": "자본의 감소"},
        }]}}
    md = _render(payload)
    assert "근거 위치: 소집공고 **§자본의 감소**" in md
    assert "L0-0-2-19-0" in md


def test_reconcile_category_with_lcode_rules():
    """260724 L-코드 이중 대조 (QA·스튜어드십 리뷰 반영판): 승격 게이트 2중 + note 분리."""
    from open_proxy_mcp.services.proxy_advise import _reconcile_category_with_lcode as rec
    # other + 특정 코드 + 이름 정합 + 맵 신뢰 → 승격 + 분류 근거 note (silent 승격 금지)
    cat, note = rec("other", "L0-0-2-19-0", section_title="자본의 감소", map_trusted=True)
    assert cat == "capital_reduction" and note and "분류 근거" in note
    # 이름 정합 실패(밀린 코드 — 섹션 제목이 코드와 다른 유형) → 승격 차단 + 수기 확인
    cat, note = rec("other", "L0-0-2-19-0", section_title="이사의 보수한도 승인", map_trusted=True)
    assert cat == "other" and note and "정합 확인 실패" in note
    # 맵 불신(같은 공고에 불일치 존재) → 승격 차단
    cat, note = rec("other", "L0-0-2-19-0", section_title="자본의 감소", map_trusted=False)
    assert cat == "other" and note and "정합 확인 실패" in note
    # 둘 다 특정인데 상충 → 텍스트 유지 + 한글 라벨 경고
    cat, note = rec("director_election", "L0-0-2-4-0", section_title="감사위원회 위원의 선임")
    assert cat == "director_election" and note and "불일치" in note
    # 일치 → 무note
    assert rec("director_election", "L0-0-2-3-0", section_title="이사의 선임") == ("director_election", None)
    # 기타(20-0) 비권위 / 코드 부재 → 무간섭
    assert rec("other", "L0-0-2-20-0", section_title="기타 주주총회의 목적사항") == ("other", None)
    assert rec("other", None) == ("other", None)
    # 미등재 코드 → 수기 확인 note (사용자 톤 — '어휘 수집' 개발 메모 미노출)
    cat, note = rec("other", "L0-0-2-7-0", section_title="알 수 없는 안건")
    assert cat == "other" and note and "미등록" in note and "어휘" not in note


def test_classify_new_auto_for_holes_closed():
    """260724 스튜어드십 리뷰: 스톡옵션·영업양도 자동FOR 구멍 봉쇄."""
    from open_proxy_mcp.services.proxy_advise import _classify_agenda
    assert _classify_agenda("주식매수선택권 부여의 건") == "stock_option_grant"
    assert _classify_agenda("임직원 스톡옵션 부여 승인의 건") == "stock_option_grant"
    assert _classify_agenda("영업양도의 건") == "merger_or_restructuring"
    assert _classify_agenda("영업양수의 건") == "merger_or_restructuring"


def test_classify_reverse_split_routed_to_capital_reduction():
    """260724 라이브 실사례(상상인증권 8/7 EGM): 주식(액면)병합이 'other'→FOR로 새던 것."""
    from open_proxy_mcp.services.proxy_advise import _classify_agenda
    assert _classify_agenda("주식(액면)병합 승인의 건") == "capital_reduction"
    assert _classify_agenda("액면병합의 건") == "capital_reduction"
    # 합병(merger)과 혼동 금지
    assert _classify_agenda("회사 합병 승인의 건") == "merger_or_restructuring"
