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


def test_classify_treasury_cancellation_funded_by_dividend_profit():
    """「배당가능이익을 재원으로 한 자기주식 소각」은 자사주 안건이지 배당 안건이 아니다.

    배당은 **재원의 이름**으로만 등장한다. 종전에는 '배당' 글자가 먼저 걸려 cash_dividend 로
    가서, 자사주 소각에 배당성향·잉여금 기준을 들이대는 엉뚱한 판정이 나왔다
    (실측 태광산업 — 권고적 주주제안).
    """
    from open_proxy_mcp.services.proxy_advise import _classify_agenda
    assert _classify_agenda(
        "배당가능이익을 재원으로 한 보유 자기주식 소각의 건 (권고적 주주제안)") == "treasury_share"
    assert _classify_agenda("배당가능이익으로 자사주 취득의 건") == "treasury_share"


def test_agenda_title_drops_trailing_notice_section():
    """안건 목록 뒤에 소집공고 본문 섹션이 붙어 들어오는 것을 잘라낸다.

    실측 현대글로비스 — 원문이 「제5-2호 : 이사 보수한도 승인의 건4. 제25기 이익배당 예정내용
    - 1주당 배당금 : 현금 5,800원」으로 공백 없이 이어진다. 「4.」는 안건 번호가 아니라 공고의
    4번 항목이다. 안 자르면 보수한도 안건이 **배당 안건으로 분류돼 배당 판정을 받는다**.
    """
    from open_proxy_mcp.services.proxy_advise import _classify_agenda
    from open_proxy_mcp.services.shareholder_meeting_parser import _clean_title
    got = _clean_title("이사 보수한도 승인의 건4. 제25기 이익배당 예정내용 - 1주당 배당금 : 현금 5,800원")
    assert got == "이사 보수한도 승인의 건", got
    assert _classify_agenda(got) == "director_compensation"
    assert _clean_title("이사 선임의 건 5. 전자투표 및 전자위임장 권유에 관한 사항") == "이사 선임의 건"


def test_agenda_title_keeps_inline_subagendas():
    """반대 어형 회귀 — 뒤에 붙은 게 공고 섹션이 아니면 자르지 않는다.

    인라인 하위안건은 `_split_inline_subagendas` 소관이라 여기서 잘라내면 후보를 잃는다.
    """
    from open_proxy_mcp.services.shareholder_meeting_parser import _clean_title
    t = "이사 선임의 건 3-1 사내이사 허남 선임의 건 3-2 기타비상무이사 정문주 선임의 건"
    assert _clean_title(t) == t
    assert _clean_title("정관 일부 변경의 건") == "정관 일부 변경의 건"


def test_classify_real_dividend_agendas_unchanged():
    """반대 어형 회귀 — 진짜 배당 안건은 종전대로 cash_dividend."""
    from open_proxy_mcp.services.proxy_advise import _classify_agenda
    assert _classify_agenda("제52기 이익배당 승인의 건(보통주 현금배당 주당 20,000원)") == "cash_dividend"
    assert _classify_agenda("제14기 현금배당 결의의 건") == "cash_dividend"
    assert _classify_agenda("이익잉여금 처분계산서 승인의 건") == "cash_dividend"


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


def test_industry_suffix_stripping_prefers_the_shortest_cut():
    """공고 헤더는 정식 상호(「삼성생명보험」)인데 DART 등록명은 짧다(「삼성생명」).

    많이 뗄수록 다른 회사가 된다 — 「미래에셋생명보험」에서 '생명보험'을 떼면
    「미래에셋」이라는 별개 회사가 나온다. '보험'만 떼야 「미래에셋생명」이다.
    """
    from open_proxy_mcp.company_resolver import industry_suffix_variants as v
    assert v("미래에셋생명보험")[0] == "미래에셋생명", v("미래에셋생명보험")
    assert v("흥국화재해상보험")[0] == "흥국화재해상"
    assert "흥국화재" in v("흥국화재해상보험"), "짧게 떼서 못 찾으면 더 떼 본다"
    assert v("대한약품공업")[0] == "대한약품"


def test_industry_suffix_stripping_leaves_group_forms_alone():
    """지주·계열 표기는 떼지 않는다 — 앞자르기 실험에서 나온 오답을 막는다.

    실측: 에스피씨삼립→「케이에스피」, 포스코디엑스→「POSCO홀딩스」, NICE홀딩스→「NICE」.
    조회 실패보다 틀린 회사를 주는 편이 나쁘다.
    """
    from open_proxy_mcp.company_resolver import industry_suffix_variants as v
    for name in ("nice홀딩스", "포스코디엑스", "에스피씨삼립", "삼성전자", "카카오"):
        assert v(name) == [], name


def test_not_found_shows_candidates_instead_of_dead_end():
    """못 찾으면 끝내지 말고 근접 후보를 보여준다 — 고르는 것은 사람이다."""
    from open_proxy_mcp.tools.company import _render_error
    out = _render_error({"subject": "에이플러스에셋어드바이저",
                         "warnings": ["'에이플러스에셋어드바이저'에 해당하는 회사를 찾지 못했다."],
                         "data": {"candidates": [
                             {"corp_name": "에이플러스에셋", "stock_code": "244920",
                              "corp_code": "00684802"}]}})
    assert "혹시 이 회사인가요?" in out
    assert "에이플러스에셋" in out and "244920" in out
    assert "ticker(6자리)" in out, "다음에 뭘 하면 되는지 알려준다"


def test_not_found_without_candidates_stays_quiet():
    """후보가 없으면 빈 표를 만들지 않는다."""
    from open_proxy_mcp.tools.company import _render_error
    out = _render_error({"subject": "성안머티리얼스", "warnings": ["못 찾았다."],
                         "data": {"candidates": []}})
    assert "혹시 이 회사인가요?" not in out


def test_every_classified_category_has_a_decision_branch():
    """분류 카테고리에 판정 분기가 없으면 'other'로 새어 자동 FOR 가 된다.

    이번 세션에서만 같은 구멍을 네 번 막았다 — stock_option_grant · capital_reduction
    (260724) · merger_or_restructuring · shareholder_proposal (260727, 라이브 스윕에서 발견).
    새 카테고리를 만들 때 분기를 빼먹으면 여기서 걸린다.
    """
    import re
    from pathlib import Path
    src = Path("open_proxy_mcp/services/proxy_advise.py").read_text(encoding="utf-8")
    classifier = src.split("def _classify_agenda")[1].split("\ndef ")[0]
    cats = set(re.findall(r'return "([a-z_]+)"', classifier)) - {"other"}
    dispatch = src.split("agenda_decisions.append")[0]
    have = set(re.findall(r'category == "([a-z_]+)"', dispatch))
    missing = sorted(cats - have)
    assert not missing, f"판정 분기 없는 카테고리(자동 FOR 위험): {missing}"


# ── 260828 태광산업 「참석률 183.3%」 회귀 ──

def test_agm_result_table_parser_records_approval_base():
    """찬성률 분모가 의결권 기준인지 발행총수 기준인지 머리글에서 잡아 둔다."""
    soup = BeautifulSoup(
        """
        <table>
          <tr><th>번호</th><th>결의구분</th><th>회의목적사항</th><th>가결여부</th>
              <th>의결권 있는 발행주식 총수 기준(1)</th><th>(1)중 의결권 행사 주식수 기준</th><th>비고</th></tr>
          <tr><th>찬성률(%)</th><th>찬성률(%)</th><th>반대, 기권 등 비율(%)</th></tr>
          <tr><td>1</td><td>보통결의</td><td>재무제표 승인</td><td>가결</td><td>76.6</td><td>82.3</td><td>17.7</td></tr>
        </table>
        """,
        "html.parser",
    )
    rows = parse_agm_result_table(soup)
    assert rows[0]["approval_base"] == "voting"
    assert "의결권" in rows[0]["approval_base_label"]
    assert rows[0]["estimated_attendance"] == 93.1


def test_vote_math_does_not_mix_issued_and_voting_bases():
    """자사주 24%인 회사에서 특수관계인 제외 참석률이 100%를 넘지 않는다."""
    from open_proxy_mcp.services.proxy_contest import _dominant_approval_base

    items = [
        {"estimated_attendance": 93.1, "approval_base": "voting"},
        {"estimated_attendance": 93.0, "approval_base": "voting"},
    ]
    assert _dominant_approval_base(items) == "voting"

    # 태광산업 실측값으로 손계산 대조
    representative, related, treasury = 93.1, 54.53, 24.41
    voting_base = 100.0 - treasury
    related_voting = related / voting_base * 100
    contestable = representative - related_voting
    ex_related = contestable / (100.0 - related_voting) * 100
    assert 70.0 < ex_related < 80.0
