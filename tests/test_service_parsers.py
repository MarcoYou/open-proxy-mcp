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
    assert "L0-0-2-19-0" not in md  # 내부 코드는 사용자 화면 비노출 (payload에만)


def test_reconcile_category_with_lcode_rules():
    """260724 L-코드 이중 대조 (QA·스튜어드십 리뷰 반영판): 승격 게이트 2중 + note 분리."""
    from open_proxy_mcp.services.proxy_advise import _reconcile_category_with_lcode as rec
    # other + 특정 코드 + 이름 정합 + 맵 신뢰 → 승격 + 분류 근거 note (silent 승격 금지)
    cat, note = rec("other", "L0-0-2-19-0", section_title="자본의 감소", map_trusted=True)
    assert cat == "capital_reduction" and note and "분류 근거" in note and "L0-0-2" not in note
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
    assert cat == "other" and note and "미등록" in note and "어휘" not in note and "L0-0-2" not in note


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


def test_reserve_reclass_is_not_a_dividend():
    """「자본준비금 감액 및 이익잉여금 전입」은 배당이 아니라 배당가능이익을 만드는 자본거래다.

    '이익잉여금' 단축경로가 이 안건을 배당으로 끌고 가면 근거가 「§배당 — 흑자 + 배당성향
    적정 시 FOR」로 붙는다. 결손을 메우는 회사에 '흑자' 기준을 인용하게 되는 셈이다.
    문면은 전부 캐시 실측(소집공고 287건에서 12건 중 11건이 이렇게 새고 있었다).
    """
    from open_proxy_mcp.services.proxy_advise import _classify_agenda
    for t in (
        "자본준비금의 이익잉여금 전입의 건",
        "자본준비금 감액 및 이익잉여금 전입의 건",
        "자본준비금 감액 및 이익잉여금 전환의 건",
        "자본준비금 감소 및 이익잉여금 전입의 건",
        "결손보전을 위한 자본준비금의 이익잉여금 전입의 건",
        "자본준비금 이익잉여금 전입 승인 건",
        "자본준비금의 이익잉여금 전입의 건(규모: 500억원)",
        "자본준비금, 임의적립금, 기타자본잉여금 감액 및 이익잉여금으로의 이입의 건",
    ):
        assert _classify_agenda(t) == "other", t


def test_reserve_rule_does_not_swallow_real_dividends():
    """반례 — 준비금을 언급해도 배당이 본질이면 배당으로 남아야 한다."""
    from open_proxy_mcp.services.proxy_advise import _classify_agenda
    assert _classify_agenda("자본준비금 감액에 따른 배당 확대의 건") == "cash_dividend"
    assert _classify_agenda("이익잉여금 처분계산서 승인의 건") == "cash_dividend"
    assert _classify_agenda("현금배당 승인의 건 (1주당 500원)") == "cash_dividend"
    # 반대 방향(적립)은 감액·전입이 아니라 이 규칙에 걸리지 않는다
    assert _classify_agenda("이익준비금 적립의 건") == "other"


def test_share_consolidation_keeps_review_but_not_called_capital_reduction():
    """주식(액면)병합은 자본금이 줄지 않는다 — REVIEW 라우팅은 유지하되 감자라 하지 않는다.

    실측 10건 중 4건은 공고문에서 명시적으로 감자가 아니라고 밝히고 있었다. 그렇다고
    'other'로 되돌리면 자동 찬성으로 새던 사고(260724)가 재발하므로 경로는 그대로 둔다.
    """
    from open_proxy_mcp.services.proxy_advise import _classify_agenda
    assert _classify_agenda("주식병합 승인의 건") == "capital_reduction"
    assert _classify_agenda("주식(액면) 병합 승인의 건") == "capital_reduction"
    assert _classify_agenda("자본금 감소(액면가 감액) 승인의 건") == "capital_reduction"


def test_corp_form_prefix_is_stripped_for_lookup():
    """DART 정식명은 「(주)광무」처럼 법인격이 앞에 붙는다 — 그 표기로도 찾아야 한다.

    실측(100사 라이브 스윕): suffix 만 떼던 탓에 우리 툴이 스스로 출력한 회사명으로
    재조회하면 14곳이 식별 실패했다.
    """
    from open_proxy_mcp.dart.client import _normalize_corp_name as norm
    assert norm("(주)광무") == "광무"
    assert norm("주식회사솔루엠") == "솔루엠"
    assert norm("㈜한화") == "한화"
    assert norm("주식회사 케이티스카이라이프") == "케이티스카이라이프"
    assert norm("미래에셋생명보험(주)") == "미래에셋생명보험"


def test_corp_form_prefix_does_not_eat_real_names():
    """반례 — '주'·'유'·'재'·'사'로 시작하는 정상 상호를 깎으면 안 된다."""
    from open_proxy_mcp.dart.client import _normalize_corp_name as norm
    for name in ("주성엔지니어링", "주연테크", "유한양행", "재영솔루텍", "사조오양"):
        assert norm(name) == name.lower(), name


def test_reverse_transliteration_matches_latin_registered_names():
    """DART 등록명은 「SKC」인데 공고 헤더는 「에스케이씨(주)」로 적는다 — 둘을 이어야 한다.

    실측 322개 중 48개가 조회 실패였고 대부분 이 유형이었다(→ 31개).
    """
    from open_proxy_mcp.company_resolver import latinized_variants, normalize_compact
    def v(n): return latinized_variants(normalize_compact(n))
    assert "skc" in v("에스케이씨(주)")
    assert "cj대한통운" in v("씨제이대한통운")
    assert "hlb" in v("에이치엘비㈜")
    assert "byc" in v("비와이씨")
    assert "hd한국조선해양" in v("에이치디한국조선해양")


def test_reverse_transliteration_emits_every_prefix_length():
    """「엔」은 알파벳 N 이자 「엔터테인먼트」의 첫 글자다 — 어디까지 letter 인지 정할 수 없어
    길이별 변형을 모두 만든다."""
    from open_proxy_mcp.company_resolver import latinized_variants, normalize_compact
    got = latinized_variants(normalize_compact("제이와이피엔터테인먼트"))
    assert "jyp엔터테인먼트" in got, got
    assert "jypn터테인먼트" in got, "다른 해석도 함께 남긴다"


def test_reverse_transliteration_leaves_ordinary_names_alone():
    """반례 — 우연히 알파벳 음차와 겹치는 정상 상호를 깎으면 안 된다.
    1글자는 우연 일치가 많아(이수페타시스의 '이'=E) 2글자부터만 만든다."""
    from open_proxy_mcp.company_resolver import latinized_variants, normalize_compact
    for name in ("이수페타시스", "오뚜기", "삼성전자", "이마트", "유한양행", "지누스", "비상장회사"):
        assert not latinized_variants(normalize_compact(name)), name
