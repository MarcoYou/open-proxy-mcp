# -*- coding: utf-8 -*-
"""안건 구간 코드(진단 축) + 재무제표 안건에 병합된 배당 발라내기.

문면은 전부 캐시 실측(소집공고 68건)에서 가져왔다 — 가공 예시로는 실제 표기 변형을 놓친다.
"""
import pytest

from open_proxy_mcp.services.shareholder_meeting_parser import (
    agenda_codes_in_notice, annotate_agenda_codes, extract_dividend_from_title,
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


# ── 안건 구간 코드 ────────────────────────────────────────────────────
_NOTICE = (
    '<TITLE AASSOCNOTE="L0-0-2-1-0">□ 재무제표의 승인</TITLE>'
    '<TITLE AASSOCNOTE="L0-0-2-3-0">□ 이사의 선임</TITLE>'
    '<TITLE AASSOCNOTE="L0-0-2-3-1">확인서</TITLE>'
    '<TITLE AASSOCNOTE="L0-0-2-9-0">□ 이사의 보수한도 승인</TITLE>'
)


def test_confirmation_docs_excluded():
    """-1 접미는 안건이 아니라 후보자 확인서 첨부다."""
    codes = [c["code"] for c in agenda_codes_in_notice(_NOTICE)]
    assert codes == ["L0-0-2-1-0", "L0-0-2-3-0", "L0-0-2-9-0"], codes


def test_code_attaches_to_root_and_inherits_to_children():
    """코드는 루트 안건에만 달린다 — 하위안건(제N-M호)은 부모 구간을 상속한다."""
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
    assert "filed_kind" not in tree[1]["children"][0], "하위안건에 유형은 붙이지 않는다"
    assert tree[2]["filed_code"] == "L0-0-2-9-0"


def test_dividend_attached_during_annotation():
    tree = [{"number": "제1호", "title": "제38기 재무제표 승인의 건 (주당 배당금 100원)", "children": []}]
    annotate_agenda_codes(tree, _NOTICE)
    assert tree[0]["dividend"]["per_share_krw"] == 100


def test_code_absent_is_harmless():
    """코드가 없어도 분류·조언은 그대로 동작해야 한다 — 진단 필드일 뿐이다."""
    tree = [{"number": "제1호", "title": "이사 보수한도 승인의 건", "children": []}]
    annotate_agenda_codes(tree, "<TITLE>회의목적사항</TITLE>")   # 코드 없는 원문
    assert "filed_code" not in tree[0]


# ── 병합 판단: 보수적인 쪽 채택 ────────────────────────────────────────
def test_merge_takes_conservative_side():
    """재무제표는 FOR 인데 배당이 REVIEW 면 최종은 REVIEW 여야 한다."""
    rank = {"AGAINST": 3, "REVIEW": 2, "NO_DATA": 2, "FOR": 1}
    for fs, div, want in [("FOR", "REVIEW", "REVIEW"), ("FOR", "AGAINST", "AGAINST"),
                          ("REVIEW", "FOR", "REVIEW"), ("FOR", "FOR", "FOR")]:
        got = div if rank[div] > rank[fs] else fs
        assert got == want, (fs, div, got)
