# -*- coding: utf-8 -*-
"""안건 구간 코드(진단 축) + 재무제표 안건에 병합된 배당 발라내기.

문면은 전부 캐시 실측(소집공고 68건)에서 가져왔다 — 가공 예시로는 실제 표기 변형을 놓친다.
"""
import pytest

from open_proxy_mcp.services.shareholder_meeting_parser import (
    agenda_codes_in_notice, agenda_detail_sections, annotate_agenda_codes,
    declared_agenda_links, extract_dividend_from_title,
)


# ── 배당 발라내기: 실측 14가지 표기 ────────────────────────────────────
@pytest.mark.parametrize("title,amount,extra", [
    ("제38기 (2025.01.01 ~ 2025.12.31) 재무제표 승인의 건 (이익잉여금 처분계산서(안)포함, 주당 배당금 100원)",
     100, {}),
    ("제30기 재무제표 승인의 건(이익잉여금처분계산서 포함, 주당 배당금 410원)", 410, {}),
    ("제40기 별도 재무제표(이익잉여금처분계산서 포함)승인의 건 (현금배당 1주당 150원)",
     150, {"kind": "현금배당"}),
    ("제41기 재무제표 승인의 건 (1주당 예정 현금배당금 50원)", 50, {}),
    ("제43기 재무제표 (이익잉여금처분계산서 포함) 승인의 건 - 배당예정내용 : 1주당 1,375원 (시가배당률 : 5.2%)",
     1375, {"yield_pct": 5.2}),
])
def test_dividend_amount_extracted(title, amount, extra):
    got = extract_dividend_from_title(title)
    assert got and got["mentioned"] is True
    assert got.get("per_share_krw") == amount, got
    for k, v in extra.items():
        assert got.get(k) == v, (k, got)


def test_dividend_by_share_class_split():
    """보통주와 우선주 배당금이 다르면 갈라야 한다 — 실측 3건."""
    t = ("제103기 재무제표[이익잉여금처분계산서(안) 포함] 및 연결재무제표 승인의 건 "
         "- 1주당 배당금(안) : 보통주 600원, 우선주 610원")
    got = extract_dividend_from_title(t)
    assert got["by_class"] == {"보통주": 600, "우선주": 610}, got
    assert got["per_share_krw"] == 600, "대표값은 보통주"


def test_dividend_mentioned_without_amount():
    """금액이 제목에 없어도 '이 안건에 배당이 묶여 있다'는 사실은 남겨야 한다.
    재무제표 승인만 보고 배당 적정성을 건너뛰면 안 된다(실측 27/38건이 이 경우)."""
    t = "제44기(2025.01.01~2025.12.31) 재무제표 (이익잉여금처분계산서 포함) 및 연결재무제표 승인의 건"
    got = extract_dividend_from_title(t)
    assert got and got["mentioned"] is True
    assert got.get("per_share_krw") is None


def test_no_dividend_returns_none():
    assert extract_dividend_from_title("정관 일부 변경의 건") is None
    assert extract_dividend_from_title("이사 보수한도 승인의 건") is None


def test_none_declared_flagged():
    got = extract_dividend_from_title("제12기 재무제표 승인의 건 (당기 무배당)")
    assert got and got["none_declared"] is True


# ── 「처분계산서 제외」는 배당이 이 안건에 묶여 있지 않다는 뜻 ──────────────
@pytest.mark.parametrize("title", [
    # 실측 — 둘 다 배당을 별도 안건으로 따로 올린 회사다.
    "제52기 연결 및 별도 재무제표(이익잉여금처분계산서 제외) 승인의 건",
    "제49기 재무제표(이익잉여금처분계산서 제외)승인의 건 (2025년 1월 1일 ~ 2025년 12월 31일)",
    "제30기 재무제표 승인의 건(이익잉여금처분계산서(안) 미포함)",
])
def test_appropriation_excluded_is_not_merged_dividend(title):
    """제목이 「제외」라고 밝히면 배당 병합으로 보지 않는다.

    종전에는 '처분계산서'라는 글자만 보고 mentioned=True 를 냈다. 그 결과 재무제표 안건에
    배당 적정성 판정이 얹히고, 배당은 별도 안건에도 있어 **같은 배당이 두 번 판정**됐다.
    """
    assert extract_dividend_from_title(title) is None


def test_appropriation_included_still_merged():
    """반대 어형 회귀 — 「포함」은 종전대로 병합이어야 한다."""
    got = extract_dividend_from_title("제65기 재무제표 [이익잉여금처분계산서(안)포함] 승인의 건")
    assert got and got["mentioned"] is True


def test_excluded_but_amount_present_keeps_amount():
    """「제외」와 명시 금액이 동시에 있으면 금액을 믿는다 — 부정어로 실제 값을 지우지 않는다."""
    got = extract_dividend_from_title(
        "제49기 재무제표(이익잉여금처분계산서 제외) 승인의 건 - 1주당 배당금 500원")
    assert got and got.get("per_share_krw") == 500


# ── 안건 구간 코드 ────────────────────────────────────────────────────
# 구간 본문이 「제N호 의안 :」으로 어느 안건의 상세인지 스스로 밝히는 실측 서식.
_NOTICE = (
    '<TITLE AASSOCNOTE="L0-0-2-1-0">□ 재무제표의 승인</TITLE>'
    '<P>제1호 의안 : 제38기 재무제표 승인의 건</P>'
    '<TITLE AASSOCNOTE="L0-0-2-3-0">□ 이사의 선임</TITLE>'
    '<P>제2호 의안 : 이사 선임의 건 -제2-1호 사외이사 홍길동</P>'
    '<TITLE AASSOCNOTE="L0-0-2-3-1">확인서</TITLE><P>확인서_홍길동.jpg</P>'
    '<TITLE AASSOCNOTE="L0-0-2-9-0">□ 이사의 보수한도 승인</TITLE>'
    '<P>제3호 의안 : 이사 보수한도 승인의 건</P>'
)

# 구간이 번호를 밝히지 않는 서식 — 실측 287건 중 40%가 이 모양이다.
_NOTICE_SILENT = (
    '<TITLE AASSOCNOTE="L0-0-2-1-0">□ 재무제표의 승인</TITLE>'
    '<P>가. 해당 사업연도의 영업상황의 개요</P>'
    '<TITLE AASSOCNOTE="L0-0-2-2-0">□ 정관의 변경</TITLE>'
    '<P>가. 집중투표 배제를 위한 정관의 변경</P>'
)


def test_confirmation_docs_excluded():
    """-1 접미는 안건이 아니라 후보자 확인서 첨부다."""
    codes = [c["code"] for c in agenda_codes_in_notice(_NOTICE)]
    assert codes == ["L0-0-2-1-0", "L0-0-2-3-0", "L0-0-2-9-0"], codes


def test_code_attaches_from_document_declaration():
    """구간이 스스로 밝힌 안건번호로만 붙인다 — 하위안건은 부모 구간을 상속한다."""
    tree = [
        {"number": "제1호", "title": "제38기 재무제표 승인의 건 (주당 배당금 100원)", "children": []},
        {"number": "제2호", "title": "이사 선임의 건", "children": [
            {"number": "제2-1호", "title": "사외이사 홍길동 선임의 건", "children": []},
        ]},
        {"number": "제3호", "title": "이사 보수한도 승인의 건", "children": []},
    ]
    annotate_agenda_codes(tree, _NOTICE)
    assert tree[0]["filed_code"] == "L0-0-2-1-0"
    assert tree[1]["filed_code"] == "L0-0-2-3-0"
    assert tree[1]["children"][0]["filed_code"] == "L0-0-2-3-0", "하위안건은 부모 코드를 상속"
    assert tree[2]["filed_code"] == "L0-0-2-9-0"


def test_misclassification_does_not_shift_codes():
    """분류가 틀려도 코드는 밀리지 않아야 한다.

    옛 구현은 분류 결과로 코드를 소비해서, 제1호를 정관변경으로 오분류하면 정관용 코드를
    먹어버리고 제2호부터 전부 밀렸다(실측 오답 6.3%). 그러면 이 필드로 분류 정확도를
    되짚을 수 없다 — 재는 자가 재는 대상에 의존하기 때문이다.
    """
    tree = [
        # 실측 오분류 사례: 「제무제표」 오타 + 「상법 제449조의2」 언급 → 정관변경으로 오분류됨
        {"number": "제1호", "title": "제38기 제무제표 승인의 건 (단, 상법 제449조의2에 따라)",
         "children": []},
        {"number": "제2호", "title": "이사 선임의 건", "children": []},
        {"number": "제3호", "title": "이사 보수한도 승인의 건", "children": []},
    ]
    annotate_agenda_codes(tree, _NOTICE)
    assert tree[0]["filed_code"] == "L0-0-2-1-0", "오분류돼도 문서 선언대로"
    assert tree[1]["filed_code"] == "L0-0-2-3-0", "뒤 안건이 밀리지 않는다"
    assert tree[2]["filed_code"] == "L0-0-2-9-0"


def test_clause_number_is_not_an_agenda_number():
    """「정관 제25조 제1항 제3호」의 '제3호'는 조항번호이지 안건번호가 아니다."""
    html = ('<TITLE AASSOCNOTE="L0-0-2-2-0">□ 정관의 변경</TITLE>'
            '<P>나. 정관 제25조 제1항, 제3호를 다음과 같이 변경</P>')
    assert declared_agenda_links(html) == {}


def test_dividend_attached_during_annotation():
    tree = [{"number": "제1호", "title": "제38기 재무제표 승인의 건 (주당 배당금 100원)", "children": []}]
    annotate_agenda_codes(tree, _NOTICE)
    assert tree[0]["dividend"]["per_share_krw"] == 100


def test_code_absent_is_harmless():
    """코드가 없어도 분류·조언은 그대로 동작해야 한다 — 진단 필드일 뿐이다."""
    tree = [{"number": "제1호", "title": "이사 보수한도 승인의 건", "children": []}]
    annotate_agenda_codes(tree, "<TITLE>회의목적사항</TITLE>")   # 코드 없는 원문
    assert "filed_code" not in tree[0]


def test_silent_document_falls_back_but_says_it_inferred():
    """문서가 안 밝히면 추론 층으로 잇되, 추론이라고 밝힌다.

    선언만 쓰면 루트 안건의 47.3%에서 멈춘다. 층을 얹어 98.2%까지 올리는 대신,
    추론한 것을 선언처럼 내보내지 않는다 — `filed_link`로 구분되고 `filed_kind`는 안 붙는다.
    """
    tree = [{"number": "제1호", "title": "제38기 재무제표 승인의 건", "children": []},
            {"number": "제2호", "title": "정관 일부 변경의 건", "children": []}]
    annotate_agenda_codes(tree, _NOTICE_SILENT)
    assert tree[0]["filed_code"] == "L0-0-2-1-0"
    assert tree[1]["filed_code"] == "L0-0-2-2-0"
    for n in tree:
        assert n["filed_link"] != "declared", "선언이 없었으니 declared 라고 하면 안 된다"
        assert "filed_kind" not in n, "유형은 선언된 안건에만 — 추론에 확정 딱지를 붙이지 않는다"


def test_inference_never_overrides_declaration():
    """선언이 있으면 추론 층은 개입하지 않는다 — 문서가 최종 권위다."""
    tree = [{"number": "제1호", "title": "제38기 재무제표 승인의 건", "children": []},
            {"number": "제2호", "title": "이사 선임의 건", "children": []},
            {"number": "제3호", "title": "이사 보수한도 승인의 건", "children": []}]
    annotate_agenda_codes(tree, _NOTICE)
    assert [n["filed_link"] for n in tree] == ["declared"] * 3


def test_candidate_name_links_when_declaration_absent():
    """구간 표의 후보자 이름이 안건 제목에 있으면 그 구간 — 분류기와 무관한 경로."""
    html = ('<TITLE AASSOCNOTE="L0-0-2-3-0">□ 이사의 선임</TITLE>'
            '<P>후보자성명 생년월일 추천인 김의형 1958.03 이사회</P>'
            '<TITLE AASSOCNOTE="L0-0-2-9-0">□ 이사의 보수한도 승인</TITLE>'
            '<P>가. 이사의 수ㆍ보수총액 8(3) 7,000,000,000</P>')
    tree = [{"number": "제2호", "title": "사외이사 김의형 선임의 건", "children": []}]
    annotate_agenda_codes(tree, html)
    assert tree[0]["filed_code"] == "L0-0-2-3-0"
    assert tree[0]["filed_link"] == "candidate_name"


# ── 구간 통째 반환 ────────────────────────────────────────────────────
def test_sections_returned_whole_without_pairing():
    """안건↔구간을 짝지어 주지 않는 대신 어느 구간도 버리지 않는다."""
    secs = agenda_detail_sections(_NOTICE)
    assert [s["code"] for s in secs] == ["L0-0-2-1-0", "L0-0-2-3-0", "L0-0-2-9-0"]
    assert "확인서" not in " ".join(s["text"] for s in secs), "확인서는 이미지 파일명뿐 — 기본 제외"
    assert "이사 선임의 건" in secs[1]["text"]
    assert secs[1]["heading"].startswith("□")
    assert all(s["truncated"] is False for s in secs)


def test_financial_statement_section_is_capped():
    """재무제표 구간은 원문 전체 분량의 85.3%를 차지하고, 수치는 API 정형 데이터가 정본이다."""
    big = ('<TITLE AASSOCNOTE="L0-0-2-1-0">□ 재무제표의 승인</TITLE>'
           '<P>가. 영업상황의 개요</P><P>' + ("자산총계 1,000 " * 4000) + '</P>')
    s = agenda_detail_sections(big)[0]
    assert s["truncated"] is True
    assert s["chars"] > 20000
    assert len(s["text"]) < 3000
    assert "영업상황의 개요" in s["text"], "머리는 남긴다"
    assert "생략" in s["text"], "잘렸다는 사실을 감추지 않는다"


# ── 병합 판단: 보수적인 쪽 채택 ────────────────────────────────────────
def test_merge_takes_conservative_side():
    """재무제표는 FOR 인데 배당이 REVIEW 면 최종은 REVIEW 여야 한다."""
    rank = {"AGAINST": 3, "REVIEW": 2, "NO_DATA": 2, "FOR": 1}
    for fs, div, want in [("FOR", "REVIEW", "REVIEW"), ("FOR", "AGAINST", "AGAINST"),
                          ("REVIEW", "FOR", "REVIEW"), ("FOR", "FOR", "FOR")]:
        got = div if rank[div] > rank[fs] else fs
        assert got == want, (fs, div, got)


# ── 상법 §449조의2: 재무제표가 표결 대상인가 보고사항인가 ─────────────────
from open_proxy_mcp.services.shareholder_meeting_parser import (      # noqa: E402
    annotate_board_approval, board_approval_special_case,
)


def _fs_tree():
    return [{"number": "제1호", "title": "제56기 재무제표 승인의 건", "children": []},
            {"number": "제2호", "title": "정관 일부 변경의 건", "children": []}]


@pytest.mark.parametrize("sentence", [
    # 캐시 실측 확정 문면 — 완료형·철회형
    "당사는 상법 449조2 및 회사 정관 제52조 제6항에 따라 외부감사인의 감사의견이 적정하고"
    " 감사의 동의가 있어 재무제표를 이사회 결의로 승인하였기에 보고 안건으로 시행합니다.",
    "제50기 재무제표는 외부감사인의 '적정' 감사의견과 감사의 동의로 상법 제449조 의2 및 당사"
    " 정관 제51조 4항에 따라 이사회에서 최종승인 되었습니다.",
    "상법 제449조의2 및 정관 제43조에 따른 요건이 충족되어 이를 이사회에서 승인하고"
    " 주주총회 보고사항으로 변경함",
    "제1호 의안 : 안건 철회(보고사항으로 변경) 상법 제449조의2에 따라 요건을 모두 충족하여",
])
def test_converted_marks_agenda_as_not_voted(sentence):
    tree = _fs_tree()
    annotate_board_approval(tree, sentence)
    assert tree[0]["resolution_status"] == "report_only"
    assert "resolution_status" not in tree[1], "재무제표 아닌 안건에는 붙지 않는다"


@pytest.mark.parametrize("sentence", [
    # 캐시 실측 조건부 문면 — 조건 어미가 문장 전체를 지배한다
    "제 45 기 재무제표 승인의 건은 상법 제449조의2 및 당사 정관 제42조에 의거 외부감사인이"
    " 적정의견을 표시하고 감사 전원이 동의할 경우 이사회에서 승인하고 주주총회에서는"
    " 보고로 갈음할 예정입니다.",
    "제1호 의안은 상법 제449조의2 및 당사 정관 제43조 4항에 의해 외부감사인의 적정의견 및"
    " 감사위원 전원이 동의할 시 이사회에서 승인하고 주주총회에는 보고로 갈음할 수 있음.",
    "제8호 의안은 상법 제449조의2에 의한 요건 충족시 이사회 승인 후 주주총회 보고사항으로 변경 예정",
    "당사는 상법 제449조의2 및 당사 정관 제43조에 따라 외부감사인의 감사의견이 적정이고"
    " 감사의 동의가 있는 경우 이사회에서 재무제표를 승인하고 주주총회에서 보고할 예정임.",
])
def test_conditional_stays_a_votable_agenda(sentence):
    """조건부는 공고 시점엔 여전히 표결 안건 — '이사회에서 승인하고'만 떼어 완료로 읽으면 오판이다."""
    tree = _fs_tree()
    annotate_board_approval(tree, sentence)
    assert tree[0]["resolution_status"] == "report_if_conditions_met"


def test_withdrawn_title_variants_are_recognised():
    """철회된 안건은 제목만 남고 표기가 갈린다 — 놓치면 표결 없는 안건에 찬성이 나간다."""
    sent = ("당사는 상법 제449조의2 및 당사 정관 제43조 규정에 의거하여 외부감사인의 적정의견 및"
            " 감사위원 전원의 동의로 재무제표를 이사회에서 승인하였습니다.")
    for title in ("안건 철회(보고사항으로 변경)", "의안 철회 [보고사항으로 변경]",
                  "보고사항으로 변경", "보고사항으로 전환", "보고사항으로 진행"):
        tree = [{"number": "제1호", "title": title, "children": []}]
        annotate_board_approval(tree, sent)
        assert tree[0]["resolution_status"] == "report_only", title


def test_renumbered_correction_does_not_mistag_other_agenda():
    """정정공고에서 번호가 재배치되면 문면의 '제1호'가 다른 안건을 가리킨다.

    실측: 써니전자·한진중공업홀딩스의 정관변경 안건에 보고사항 표시가 잘못 붙었다.
    번호가 아니라 안건의 정체로 게이트를 건다.
    """
    sent = ("기존 결의사항 제1호 의안인 제60기 재무제표 승인의 건을 상법 제449조의2 및 당사 정관"
            " 제43조에 따라 요건을 충족하여 이사회에서 승인하고 주주총회 보고사항으로 변경하였습니다.")
    tree = [{"number": "제1호", "title": "정관 일부 변경의 건", "children": []},
            {"number": "제2호", "title": "이사 선임의 건", "children": []}]
    annotate_board_approval(tree, sent)
    assert all("resolution_status" not in n for n in tree)


def test_law_absent_is_a_no_op():
    tree = _fs_tree()
    annotate_board_approval(tree, "제1호 의안 : 제56기 재무제표 승인의 건")
    assert all("resolution_status" not in n for n in tree)
    assert board_approval_special_case("정관 제449조 규정에 따라") == {}


def test_clause_number_449_without_the_special_case():
    """상법 제449조(재무제표 승인 일반)는 특칙이 아니다 — 조의2 만 잡는다."""
    assert board_approval_special_case(
        "상법 제449조에 따라 주주총회에서 재무제표를 승인합니다.") == {}


def test_financial_statements_as_modifier_is_not_the_approval_agenda():
    """'재무제표'가 수식어로 쓰인 별개 안건을 보고사항으로 표시하면 조언이 통째로 죽는다.

    실측(케이씨씨): 「연결재무제표를 기준으로 한 주주환원정책 재수립의 건(권고적 주주제안)」이
    재무제표 승인 안건으로 오인돼 '표결없음' 처리됐다 — 주주제안 안건인데 의견이 안 나간다.
    """
    sent = ("제8호 의안 : 안건 철회(보고사항으로 변경) 상법 제449조의2에 따라 요건을 모두 충족하여"
            " 이사회 결의로 재무제표를 승인하였습니다.")
    tree = [
        {"number": "제8호", "title": "안건 철회(보고사항으로 변경)", "children": []},
        {"number": "제10-3호",
         "title": "연결재무제표를 기준으로 한 주주환원정책 재수립의 건(권고적 주주제안)",
         "children": []},
    ]
    annotate_board_approval(tree, sent)
    assert tree[0]["resolution_status"] == "report_only", "철회된 재무제표 안건은 맞다"
    assert "resolution_status" not in tree[1], "주주제안 안건에는 붙으면 안 된다"


# ── 공고가 밝힌 직위 vs 후보자 표 파싱 ──────────────────────────────────
from open_proxy_mcp.services.shareholder_meeting_parser import annotate_declared_role  # noqa: E402


def test_declared_role_records_what_the_notice_said():
    """제목이 밝힌 직위를 남긴다 — 파싱을 덮지는 않는다."""
    tree = [{"number": "제1호", "title": "이사 선임의 건", "children": [
        {"number": "제1-1호", "title": "사내이사 선임의 건 (후보자: 문경민)", "children": []},
        {"number": "제1-2호", "title": "사외이사 선임의 건 (후보자: 유균)", "children": []},
        {"number": "제1-3호", "title": "기타비상무이사 후보 : 조민영", "children": []},
    ]}]
    annotate_declared_role(tree)
    assert "declared_role" not in tree[0], "직위가 없는 묶음 안건에는 붙지 않는다"
    kids = tree[0]["children"]
    assert [k["declared_role"] for k in kids] == ["사내이사", "사외이사", "기타비상무이사"]


def test_declared_role_absent_when_title_is_silent():
    tree = [{"number": "제2호", "title": "감사위원회 위원 선임의 건 (후보자: 유균)", "children": []}]
    annotate_declared_role(tree)
    assert "declared_role" not in tree[0]


# ── 상법 1차 개정: 사외이사 → 독립이사 (§542의8, 시행 2026-07-23) ──────────
def test_independent_director_is_the_same_role_as_outside_director():
    """명칭만 바뀐 같은 직위다 — 시행 전후 공고가 섞이므로 둘 다 사외이사로 받는다."""
    tree = [{"number": "제1호", "title": "이사 선임의 건", "children": [
        {"number": "제1-1호", "title": "독립이사 선임의 건 (후보자: 김철수)", "children": []},
        {"number": "제1-2호", "title": "사외이사 선임의 건 (후보자: 이영희)", "children": []},
        {"number": "제1-3호", "title": "사내이사 선임의 건 (후보자: 박민수)", "children": []},
    ]}]
    annotate_declared_role(tree)
    assert [k["declared_role"] for k in tree[0]["children"]] == \
        ["사외이사", "사외이사", "사내이사"], "독립이사도 사외이사로 취급"


def test_independent_director_normalises_like_outside_director():
    """표의 직위 칸이 「독립이사」여도 사외이사와 동일하게 정규화된다.

    실측 검증(판타지오 소집공고 전체를 개정 후 표기로 치환): 사외이사 2명이 독립이사로
    그대로 인식되고 후보 수도 보존됐다. 여기서는 그 규칙만 좁게 고정한다.
    """
    from open_proxy_mcp.services.director_evaluation import _is_outside_director_role
    for role in ("독립이사", "독립이사 후보자(재선임)", "사외이사"):
        assert _is_outside_director_role(role), role
    assert not _is_outside_director_role("사내이사")


def test_declared_role_only_on_election_agendas():
    """직위가 제목에 나와도 선임 안건이 아니면 그 사람의 직위를 밝힌 게 아니다(실측 164건)."""
    tree = [
        {"number": "제2-3호", "title": "독립이사로의 명칭 변경의 건", "children": []},
        {"number": "제5-3호", "title": "사외이사들의 보수 한도액을 1억원으로 정하는 건", "children": []},
        {"number": "제3-1호", "title": "사외이사 이원조 선임의 건", "children": []},
    ]
    annotate_declared_role(tree)
    assert "declared_role" not in tree[0], "정관 명칭변경 안건"
    assert "declared_role" not in tree[1], "보수한도 안건"
    assert tree[2]["declared_role"] == "사외이사"


def test_candidate_block_renders_with_and_without_shared_marker():
    """후보 평가 렌더가 공유 문면 표시와 함께 깨지지 않아야 한다.

    라이브에서 잡힌 크래시(`name 'ev' is not defined`)를 고정한다 — 렌더러는
    단위 테스트가 없어 변수명 오타가 배포 직전까지 살아남았다.
    """
    from open_proxy_mcp.tools.proxy_advise_before_meeting import _render
    def _payload(shared):
        return {"status": "exact", "subject": "T", "data": {
            "canonical_name": "테스트", "year": 2026, "meeting_type": "annual",
            "agenda_decisions": [], "candidates_evaluations": [{
                "name": "허은철", "role_type": "사내이사",
                "faithfulness": {"main_job": "대표이사",
                                 "recommendation_reason_raw": "후보자는 개발, 생산, 품질관리 분야에서",
                                 "recommendation_reason_shared": shared},
            }]}}
    with_mark = _render(_payload(True))
    without = _render(_payload(False))
    assert "확정하지 못함" in with_mark
    assert "확정하지 못함" not in without
    assert "후보자는 개발, 생산, 품질관리 분야에서" in with_mark, "문면 자체는 그대로 넘긴다"
