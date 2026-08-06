"""기업지배구조보고서 서식 레지스트리 — 번호 체계가 어긋나면 표를 잘못 집는다."""

from __future__ import annotations

import re

from open_proxy_mcp.services.corp_gov_form import (
    FORM_TABLES,
    KEY_LABELS,
    SECTION_CODES,
    form_table,
    parse_form_tables,
    section_path,
)

_SKELETON = {"000001", "000002", "000003", "000004"}


def _doc(number: str, aclass: str, header: str, body: str) -> str:
    return f"""
    <body><p class="table-name"><span>표 {number}: 시험용</span></p>
    <table-group aclass="{aclass}"><table class="fact-table"><tbody>
    {header}{body}
    </tbody></table></table-group></body>
    """


_ATTENDANCE = FORM_TABLES["7-2-1"]["aclass"]
_ATTENDANCE_HEADER = """
    <tr><th rowspan="3"></th><th colspan="0" rowspan="3">구분</th>
        <th colspan="0" rowspan="3">이사회 재직기간</th>
        <th colspan="2">출석률 (%)</th></tr>
    <tr><th colspan="0" rowspan="2">최근 3개년 평균</th><th colspan="1">최근 3개년</th></tr>
    <tr><th colspan="0" rowspan="2">당해연도</th></tr>
"""


def test_section_codes_split_into_chapter_principle_and_sub() -> None:
    """골격 4종 + 장 5 + 핵심원칙 10 + 세부원칙 28 = 47종."""
    assert len(SECTION_CODES) == 47
    chapters = {c for c in SECTION_CODES if section_path(c) == (c[0], "", "")}
    principles = {c for c in SECTION_CODES if section_path(c)[1] and not section_path(c)[2]}
    subs = {c for c in SECTION_CODES if section_path(c)[2]}
    assert len(chapters) == 5
    assert len(principles) == 10
    assert len(subs) == 28
    assert chapters | principles | subs | _SKELETON == set(SECTION_CODES)


def test_section_code_matches_the_title_it_heads() -> None:
    for code, title in SECTION_CODES.items():
        if code in _SKELETON:
            continue
        chapter, principle, sub = section_path(code)
        if sub:
            assert re.search(rf"세부원칙\s*{sub}\D", title), (code, title)
        elif principle:
            assert re.search(rf"핵심원칙\s*{principle}\D", title), (code, title)
        else:
            assert title.startswith(f"{chapter}."), (code, title)


def test_skeleton_codes_have_no_chapter_path() -> None:
    for code in _SKELETON:
        assert section_path(code) == ("", "", "")


def test_form_tables_carry_a_krx_concept_code_and_labels() -> None:
    assert len(FORM_TABLES) == 32
    for number, spec in FORM_TABLES.items():
        assert spec["aclass"].startswith("krx-cg_"), number
        assert spec["axis"] in ("row", "col"), number
        assert spec["labels"], number
        assert spec["title_ko"], number


def test_concept_codes_are_unique_per_table() -> None:
    codes = [spec["aclass"] for spec in FORM_TABLES.values()]
    assert len(set(codes)) == len(codes)


def test_committee_slots_beyond_the_third_are_not_form_tables() -> None:
    """서식은 위원회 표를 세 칸만 정의한다 — 번호로 위원회를 특정하면 안 된다."""
    assert form_table("8-2-1")["title_ko"] == "이사후보추천위원회 개최 내역"
    assert form_table("8-2-2")["title_ko"] == "리스크관리위원회 개최 내역"
    assert form_table("8-2-3")["title_ko"] == "내부거래위원회 개최 내역"
    assert form_table("8-2-4") is None
    assert form_table("8-2-5") is None


def test_key_indicator_table_is_registered_without_a_number() -> None:
    spec = form_table("#key-indicators")
    assert spec is not None
    assert "KeyIndicators" in spec["aclass"]


def test_multi_row_header_becomes_one_label_per_column() -> None:
    body = (
        '<tr><td>이인</td><td>사내이사</td><td>2022.01~2024.02</td><td>91</td><td></td></tr>'
    )
    parsed = parse_form_tables(_doc("7-2-1", _ATTENDANCE, _ATTENDANCE_HEADER, body), ["7-2-1"])
    table = parsed["7-2-1"]
    assert table["columns"] == [
        "이사",
        "구분",
        "이사회 재직기간",
        "출석률 (%) · 최근 3개년 평균",
        "출석률 (%) · 최근 3개년 · 당해연도",
    ]
    assert table["rows"][0]["출석률 (%) · 최근 3개년 평균"] == "91"
    # 빈 칸을 값으로 남긴다 — 지우면 뒤 연도의 값이 앞 연도 자리로 당겨진다.
    assert table["rows"][0]["출석률 (%) · 최근 3개년 · 당해연도"] == ""


def test_declared_rowspan_does_not_shift_the_row_below() -> None:
    """본문은 rowspan 이 걸려 있어도 아래 행이 같은 값을 다시 싣는다."""
    body = (
        '<tr><td rowspan="2">이인</td><td>사내이사</td><td>2022.01~</td><td>91</td><td>90</td></tr>'
        '<tr><td>이인</td><td>사외이사</td><td>2023.01~</td><td>80</td><td>70</td></tr>'
    )
    table = parse_form_tables(
        _doc("7-2-1", _ATTENDANCE, _ATTENDANCE_HEADER, body), ["7-2-1"]
    )["7-2-1"]
    assert [r["구분"] for r in table["rows"]] == ["사내이사", "사외이사"]
    assert [r["출석률 (%) · 최근 3개년 평균"] for r in table["rows"]] == ["91", "80"]


def test_row_whose_width_disagrees_with_the_header_is_not_emitted() -> None:
    body = '<tr><td>이인</td><td>사내이사</td><td>2022.01~</td></tr>'
    assert parse_form_tables(_doc("7-2-1", _ATTENDANCE, _ATTENDANCE_HEADER, body), ["7-2-1"]) == {}


def test_table_whose_concept_code_disagrees_with_the_number_is_skipped() -> None:
    body = '<tr><td>이인</td><td>사내이사</td><td>2022.01~</td><td>91</td><td>90</td></tr>'
    doc = _doc("7-2-1", FORM_TABLES["5-2-1"]["aclass"], _ATTENDANCE_HEADER, body)
    assert parse_form_tables(doc, ["7-2-1"]) == {}


_CANDIDATE = FORM_TABLES["4-3-1"]["aclass"]
_CANDIDATE_HEADER = (
    '<tr><th colspan="2"></th><th colspan="0">정보제공일(1)</th>'
    '<th colspan="0">주주총회일(2)</th></tr>'
)


def test_named_key_columns_survive_when_the_first_holds_a_meeting() -> None:
    body = '<tr><td>제53기 주주총회</td><td>김도균</td><td>2026-02-25</td><td>2026-03-26</td></tr>'
    table = parse_form_tables(_doc("4-3-1", _CANDIDATE, _CANDIDATE_HEADER, body), ["4-3-1"])["4-3-1"]
    assert table["key_labels_verified"] is True
    assert table["rows"][0]["주주총회"] == "제53기 주주총회"
    assert table["rows"][0]["후보"] == "김도균"


def test_key_columns_go_unnamed_when_the_company_puts_something_else_there() -> None:
    """서식이 이름을 안 단 칸이라 「주주총회: 최춘웅」 이 나가면 안 된다."""
    body = (
        '<tr><td>최춘웅</td><td>기타비상무이사</td><td>2026-02-25</td><td>2026-03-26</td></tr>'
        '<tr><td>조주현</td><td>사내이사</td><td>2026-02-25</td><td>2026-03-26</td></tr>'
    )
    table = parse_form_tables(_doc("4-3-1", _CANDIDATE, _CANDIDATE_HEADER, body), ["4-3-1"])["4-3-1"]
    assert table["key_labels_verified"] is False
    assert table["columns"][:2] == ["키1", "키2"]
    assert "주주총회" not in table["rows"][0]
    assert table["rows"][0]["키1"] == "최춘웅"
    assert table["rows"][0]["정보제공일(1)"] == "2026-02-25"


def test_only_tables_with_named_key_columns_are_extracted() -> None:
    assert set(KEY_LABELS) <= set(FORM_TABLES)
    body = '<tr><td>보통주</td><td>1,000</td></tr>'
    doc = _doc("2-1-1-2", FORM_TABLES["2-1-1-2"]["aclass"], "<tr><th></th><th>발행주식수(주)</th></tr>", body)
    assert "2-1-1-2" not in KEY_LABELS
    assert parse_form_tables(doc) == {}


_MEETING = FORM_TABLES["1-1-1"]["aclass"]
_MEETING_ROWS = """
    <tr><th colspan="2"></th><td>제27기 정기주주총회</td><td>제26기 정기주주총회</td></tr>
    <tr><th colspan="2">소집공고일</th><td>2026-02-25</td><td>2025-02-25</td></tr>
    <tr><th rowspan="2">세부사항</th><th>이사회 구성원 출석 현황</th><td>7/7</td><td>6/7</td></tr>
    <tr><th>감사 출석 현황</th><td>3/3</td><td>2/3</td></tr>
"""


def test_row_axis_table_is_transposed_to_one_record_per_period() -> None:
    """항목이 행에 놓인 표는 기수 하나가 한 줄이 된다."""
    table = parse_form_tables(_doc("1-1-1", _MEETING, "", _MEETING_ROWS), ["1-1-1"])["1-1-1"]
    assert table["columns"][0] == "주주총회"
    assert [r["주주총회"] for r in table["rows"]] == ["제27기 정기주주총회", "제26기 정기주주총회"]
    assert table["rows"][0]["소집공고일"] == "2026-02-25"
    assert table["rows"][1]["소집공고일"] == "2025-02-25"


def test_row_axis_labels_join_a_merged_parent_with_its_child() -> None:
    """여기서는 rowspan 이 진짜 병합이라 부모 라벨이 아래 행에 없다 — 이어 붙여야 구분된다."""
    table = parse_form_tables(_doc("1-1-1", _MEETING, "", _MEETING_ROWS), ["1-1-1"])["1-1-1"]
    assert "세부사항 · 이사회 구성원 출석 현황" in table["columns"]
    assert "세부사항 · 감사 출석 현황" in table["columns"]
    assert table["rows"][0]["세부사항 · 감사 출석 현황"] == "3/3"


def test_row_axis_key_goes_unnamed_when_the_header_is_not_a_meeting() -> None:
    rows = """
    <tr><th colspan="2"></th><td>1</td><td>2</td></tr>
    <tr><th colspan="2">소집공고일</th><td>2026-02-25</td><td>2025-02-25</td></tr>
    """
    table = parse_form_tables(_doc("1-1-1", _MEETING, "", rows), ["1-1-1"])["1-1-1"]
    assert table["key_labels_verified"] is False
    assert table["columns"][0] == "키1"


def test_periods_with_no_meeting_still_count_as_the_meeting_axis() -> None:
    """신규 상장사는 전기·전전기에 주총이 없어 「미개최(전기)」로 적는다 — 그것도 이 축의 값이다."""
    rows = """
    <tr><th colspan="2"></th><td>제1기 정기주주총회</td><td>미개최(전기)</td><td>미개최(전전기)</td></tr>
    <tr><th colspan="2">전자투표 실시 여부</th><td>O</td><td>X</td><td>X</td></tr>
    """
    table = parse_form_tables(_doc("1-2-1", FORM_TABLES["1-2-1"]["aclass"], "", rows), ["1-2-1"])["1-2-1"]
    assert table["key_labels_verified"] is True
    assert [r["주주총회"] for r in table["rows"]] == ["제1기 정기주주총회", "미개최(전기)", "미개최(전전기)"]


def test_row_axis_table_with_a_ragged_row_is_not_emitted() -> None:
    rows = """
    <tr><th colspan="2"></th><td>제27기 정기주주총회</td><td>제26기 정기주주총회</td></tr>
    <tr><th colspan="2">소집공고일</th><td>2026-02-25</td></tr>
    """
    assert parse_form_tables(_doc("1-1-1", _MEETING, "", rows), ["1-1-1"]) == {}
