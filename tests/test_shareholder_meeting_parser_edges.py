import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from open_proxy_mcp.tools.parser import (
    parse_aoi_xml,
    parse_agenda_xml,
    parse_compensation_xml,
    parse_meeting_info_xml,
    parse_personnel_xml,
)


def test_meeting_type_prefers_notice_heading_over_later_reference():
    text = """
주주총회 소집공고

(제53기 정기)

상법 제365조 및 당사 정관 제21조에 의거 제53기 정기주주총회를 아래와 같이 개최하오니 참석하여 주시기 바랍니다.
1. 일 시: 2026년 3월 27일 오전 10시
2. 장 소: 서울특별시
3. 회의목적사항
1) 제1호 의안: 제53기 재무제표 승인의 건
5. 의결권 행사에 관한 사항
※ 참고사항
당사는 제 53기 임시주주총회부터 서면투표제도를 운영하지 않음을 안내 드립니다.
"""

    info = parse_meeting_info_xml(text)

    assert info["meeting_type"] == "정기"


def test_agenda_parser_accepts_period_after_agenda_keyword():
    text = """
주주총회 소집공고
(제12기 정기)
1. 일시 : 2026년 3월 26일 오전 9시
2. 장소: 본점
3. 회의 목적사항
[결의사항]
제1호 의안. 제12기 별도재무제표 및 연결재무제표 승인의 건
제2호 의안. 정관 일부 변경의 건
제3호 의안. 이사 선임의 건
제3-1호 의안. 사외이사 김남석 선임의 건
4. 경영참고사항 비치
"""

    agendas = parse_agenda_xml(text)

    assert [item["number"] for item in agendas] == ["제1호", "제2호", "제3호"]
    assert agendas[2]["children"][0]["number"] == "제3-1호"
    assert agendas[0]["title"] == "제12기 별도재무제표 및 연결재무제표 승인의 건"


def test_agenda_parser_note_does_not_hide_period_form_next_agenda():
    text = """
주주총회 소집공고
(제12기 정기)
1. 일시 : 2026년 3월 26일 오전 9시
2. 장소: 본점
3. 회의 목적사항
[결의사항]
제1호 의안. 제12기 재무제표 승인의 건 ※1주당 배당금: 900원
제2호 의안. 정관 일부 변경의 건
제3호 의안. 이사 선임의 건
제3-1호 의안. 사외이사 김남석 선임의 건
제4호 의안. 이사 보수한도 승인의 건
4. 경영참고사항 비치
"""

    agendas = parse_agenda_xml(text)

    assert [item["number"] for item in agendas] == ["제1호", "제2호", "제3호", "제4호"]
    assert agendas[2]["children"][0]["number"] == "제3-1호"


def test_agenda_parser_stops_title_at_candidate_table_header():
    text = """
주주총회 소집공고
(제44기 정기)
1. 소집일시 : 2026년 02월 27일 오후 03시
2. 소집장소 : 부산광역시
3. 회의목적사항
《의결사항(부의안건)》
제1호 의안 : 재무제표 승인의 건
제2호 의안 : 정관 변경의 건
제3호 의안 : 사외이사선임의 건
성 명 (생년월일) 사외이사후보자 여부 추천인 주된 직업 주요약력 회사와의 거래내역
김기웅 (1952.08.07) 사외이사 이사회 한양대학교 언론정보대학원 특임교수
제4호 의안 : 이사보수한도 승인의 건
"""

    agendas = parse_agenda_xml(text)

    assert agendas[2]["number"] == "제3호"
    assert agendas[2]["title"] == "사외이사선임의 건"


def test_agenda_parser_accepts_purpose_heading_without_meeting_prefix():
    text = """
주주총회 소집공고
(제53기 정기)
1. 일 시 : 2026년 3월 19일 오전 9시
2. 장 소 : 서울특별시
3. 보고사항 : 영업보고, 감사보고
4. 목적사항- 제 1호 의안 : (철회) - 제 2호 의안 : 제53기 재무제표 승인의 건- 제 3호 의안 : 사내이사 이부진 선임의 건- 제 4호 의안 : 감사위원회 위원이 되는 사외이사 김현웅 선임의 건 - 제 5호 의안 : 이사 보수한도 승인의 건
5. 상법 제542조의4에 의거 주주총회소집통지공고사항을 비치합니다.
"""

    agendas = parse_agenda_xml(text)

    assert [item["number"] for item in agendas] == ["제1호", "제2호", "제3호", "제4호", "제5호"]
    assert agendas[4]["title"] == "이사 보수한도 승인의 건"


def test_personnel_parser_accepts_auditor_detail_title_without_election_suffix():
    html = """
<SECTION-2>
<TITLE>2. 주주총회 목적사항별 기재사항</TITLE>
<LIBRARY>
<SECTION-3>
<TITLE>□ 감사의 선임</TITLE>
<P><SPAN>제5호 의안: 감사 선임의 건(상근 감사 1인 선임)</SPAN></P>
<P><SPAN>제5-1호 의안: 감사 임성열</SPAN></P>
<P>가. 후보자의 성명ㆍ생년월일ㆍ추천인ㆍ최대주주와의 관계</P>
<TABLE>
<TR><TH>후보자성명</TH><TH>생년월일</TH><TH>최대주주와의 관계</TH><TH>추천인</TH></TR>
<TR><TD>임성열</TD><TD>1963.07.17</TD><TD>해당사항 없음</TD><TD>이사회</TD></TR>
</TABLE>
<P>나. 후보자의 주된직업ㆍ세부경력ㆍ당해법인과의 최근3년간 거래내역</P>
<TABLE>
<TR><TH ROWSPAN="2">후보자성명</TH><TH ROWSPAN="2">주된직업</TH><TH COLSPAN="2">세부경력</TH><TH ROWSPAN="2">당해법인과의최근3년간 거래내역</TH></TR>
<TR><TH>기간</TH><TH>내용</TH></TR>
<TR><TD ROWSPAN="2">임성열</TD><TD ROWSPAN="2">한화생명㈜ 비상임이사</TD><TD>2024~현재</TD><TD>한화생명㈜ 비상임이사</TD><TD ROWSPAN="2">해당없음</TD></TR>
<TR><TD>2023~현재</TD><TD>솔브레인홀딩스 감사</TD></TR>
</TABLE>
<P>다. 후보자에 대한 이사회의 추천 사유</P>
<TABLE><TR><TD>임성열 후보자는 금융 전문가입니다.</TD></TR></TABLE>
</SECTION-3>
</LIBRARY>
</SECTION-2>
"""

    personnel = parse_personnel_xml(html)

    appointments = personnel["appointments"]
    assert len(appointments) == 2
    assert appointments[0]["number"] == "제5호"
    assert appointments[0]["candidates"][0]["name"] == "임성열"
    assert appointments[1]["number"] == "제5-1호"
    assert appointments[1]["category"] == "감사"
    candidate = appointments[1]["candidates"][0]
    assert candidate["name"] == "임성열"
    assert candidate["roleType"] == "감사"
    assert candidate["birthDate"] == "1963.07.17"
    assert candidate["recommender"] == "이사회"
    assert candidate["mainJob"] == "한화생명㈜ 비상임이사"


def test_personnel_parser_does_not_treat_candidate_recommendation_rule_as_personnel():
    html = """
<SECTION-2>
<TITLE>2. 주주총회 목적사항별 기재사항</TITLE>
<LIBRARY>
<SECTION-3>
<TITLE>□ 정관의 변경</TITLE>
<P><SPAN>제3-1-7호 의안: 독립이사 후보 추천에 관한 규정 신설</SPAN></P>
<P>가. 의안의 요지</P>
<TABLE>
<TR><TH>현행</TH><TH>개정</TH><TH>비고</TH></TR>
<TR><TD>-</TD><TD>독립이사 후보 추천 규정을 신설한다.</TD><TD>신설</TD></TR>
</TABLE>
</SECTION-3>
</LIBRARY>
</SECTION-2>
"""

    personnel = parse_personnel_xml(html)

    assert personnel["appointments"] == []


def test_personnel_parser_extracts_spaced_korean_candidate_from_title():
    html = """
<SECTION-2>
<TITLE>2. 주주총회 목적사항별 기재사항</TITLE>
<LIBRARY>
<SECTION-3>
<TITLE>□ 이사의 선임</TITLE>
<P><SPAN>제4-1호 의안: 사내이사 선임의 건 (후보자 : 김 종 민)</SPAN></P>
</SECTION-3>
</LIBRARY>
</SECTION-2>
"""

    personnel = parse_personnel_xml(html)

    assert len(personnel["appointments"]) == 1
    candidate = personnel["appointments"][0]["candidates"][0]
    assert candidate["name"] == "김 종 민"
    assert candidate["roleType"] == "사내이사"


def test_personnel_parser_removes_gender_suffix_from_candidate_name():
    html = """
<SECTION-2>
<TITLE>2. 주주총회 목적사항별 기재사항</TITLE>
<LIBRARY>
<SECTION-3>
<TITLE>□ 이사의 선임</TITLE>
<P><SPAN>제5-1호 의안: 사내이사 후보자 장세욱</SPAN></P>
<P>가. 후보자의 성명ㆍ생년월일ㆍ추천인ㆍ최대주주와의 관계</P>
<TABLE>
<TR><TH>후보자성명</TH><TH>생년월일</TH><TH>추천인</TH></TR>
<TR><TD>장세욱(남성)</TD><TD>1962.12.01</TD><TD>이사회</TD></TR>
</TABLE>
</SECTION-3>
</LIBRARY>
</SECTION-2>
"""

    personnel = parse_personnel_xml(html)

    candidate = personnel["appointments"][0]["candidates"][0]
    assert candidate["name"] == "장세욱"


def test_personnel_parser_backfills_candidates_from_agenda_tree_child_titles():
    html = """
<SECTION-1>
<TITLE>주주총회 소집공고</TITLE>
<P>1. 일시 : 2026년 3월 27일 오전 9시</P>
<P>2. 장소 : 본점</P>
<P>3. 회의 목적사항</P>
<P>[결의사항]</P>
<P>제3호 의안 : 이사 선임의 건</P>
<P>제3-1호 의안 : 사내이사 선임의 건(후보 : 김정민)</P>
<P>제3-2호 의안 : 사내이사 선임의 건 (후보자 이원호)</P>
</SECTION-1>
<SECTION-2>
<TITLE>2. 주주총회 목적사항별 기재사항</TITLE>
<LIBRARY>
<SECTION-3>
<TITLE>□ 이사의 선임</TITLE>
<P><SPAN>제3호 의안: 이사 선임의 건</SPAN></P>
</SECTION-3>
</LIBRARY>
</SECTION-2>
"""

    personnel = parse_personnel_xml(html)

    by_number = {item["number"]: item for item in personnel["appointments"]}
    assert by_number["제3-1호"]["candidates"][0]["name"] == "김정민"
    assert by_number["제3-2호"]["candidates"][0]["name"] == "이원호"


def test_personnel_parser_extracts_candidate_after_role_candidate_keyword():
    html = """
<SECTION-1>
<TITLE>주주총회 소집공고</TITLE>
<P>3. 회의 목적사항</P>
<P>제3호 의안 : 이사 선임의 건</P>
<P>제3-1호 의안 : 사외이사 후보 전병선 선임의 건</P>
<P>제3-2호 의안 : 사외이사 후보 김 별 선임의 건</P>
</SECTION-1>
<SECTION-2>
<TITLE>2. 주주총회 목적사항별 기재사항</TITLE>
<LIBRARY>
<SECTION-3>
<TITLE>□ 이사의 선임</TITLE>
<P><SPAN>제3호 의안: 이사 선임의 건</SPAN></P>
</SECTION-3>
</LIBRARY>
</SECTION-2>
"""

    personnel = parse_personnel_xml(html)

    by_number = {item["number"]: item for item in personnel["appointments"]}
    assert by_number["제3-1호"]["candidates"][0]["name"] == "전병선"
    assert by_number["제3-2호"]["candidates"][0]["name"] == "김 별"


def test_personnel_parser_augments_empty_existing_appointment_from_agenda_title():
    html = """
<SECTION-1>
<TITLE>주주총회 소집공고</TITLE>
<P>3. 회의 목적사항</P>
<P>제4호 의안 : 이사 선임의 건(사외이사 1명)</P>
<P>제4-1호 의안 : 이사후보(사외이사) 조강래</P>
<P>제5호 의안 : 감사위원회 위원이 되는 사외이사 선임의 건 - 감사위원회 위원이 되는 사외이사 후보 김갑순</P>
</SECTION-1>
<SECTION-2>
<TITLE>2. 주주총회 목적사항별 기재사항</TITLE>
<LIBRARY>
<SECTION-3>
<TITLE>□ 이사의 선임</TITLE>
<P><SPAN>제4호 의안: 이사 선임의 건(사외이사 1명)</SPAN></P>
<P><SPAN>제5호 의안: 감사위원회 위원이 되는 사외이사 선임의 건</SPAN></P>
</SECTION-3>
</LIBRARY>
</SECTION-2>
"""

    personnel = parse_personnel_xml(html)

    by_number = {item["number"]: item for item in personnel["appointments"]}
    assert by_number["제4-1호"]["candidates"][0]["name"] == "조강래"
    assert by_number["제5호"]["candidates"][0]["name"] == "김갑순"


def test_personnel_parser_ignores_charter_change_titles_with_appointment_words():
    html = """
<SECTION-2>
<TITLE>2. 주주총회 목적사항별 기재사항</TITLE>
<LIBRARY>
<SECTION-3>
<TITLE>□ 정관의 변경</TITLE>
<P><SPAN>제2-5호 의안: 감사위원 선임 관련 변경의 건</SPAN></P>
<P><SPAN>제2-1호 의안: 이사의 의무 추가 및 독립이사 선임의 건</SPAN></P>
<P><SPAN>제3-9호 의안: 감사위원 분리선임 인원 변경</SPAN></P>
<P><SPAN>제3-11호 의안: 감사위원 선ㆍ해임시 의결권 제한기준 변경</SPAN></P>
</SECTION-3>
</LIBRARY>
</SECTION-2>
"""

    personnel = parse_personnel_xml(html)

    assert personnel["appointments"] == []


def test_personnel_parser_extracts_role_name_with_new_appointment_suffix():
    html = """
<SECTION-1>
<TITLE>주주총회 소집공고</TITLE>
<P>3. 회의 목적사항</P>
<P>제4호 의안 : 이사 선임의 건</P>
<P>제4-1호 의안 : 사외이사 한종복 (신규선임)</P>
<P>제4-2호 의안 : 사외이사 문종국 (신규선임)</P>
</SECTION-1>
<SECTION-2>
<TITLE>2. 주주총회 목적사항별 기재사항</TITLE>
<LIBRARY>
<SECTION-3>
<TITLE>□ 이사의 선임</TITLE>
<P><SPAN>제4호 의안: 이사 선임의 건</SPAN></P>
</SECTION-3>
</LIBRARY>
</SECTION-2>
"""

    personnel = parse_personnel_xml(html)

    by_number = {item["number"]: item for item in personnel["appointments"]}
    assert by_number["제4-1호"]["candidates"][0]["name"] == "한종복"
    assert by_number["제4-2호"]["candidates"][0]["name"] == "문종국"


def test_personnel_parser_accepts_candidate_section_marker_as_text_block():
    html = """
<SECTION-2>
<TITLE>2. 주주총회 목적사항별 기재사항</TITLE>
<LIBRARY>
<SECTION-3>
<TITLE>□ 감사위원회 위원이 되는 이사의 선임</TITLE>
<P><SPAN>제4호 의안: 감사위원회 위원이 되는 이사 선임의 건 (사외이사 1명)</SPAN></P>
<P>가. 후보자의 성명ㆍ생년월일ㆍ추천인ㆍ최대주주와의 관계ㆍ사외이사후보자 등 여부</P>
<TABLE>
<TR><TH>후보자 성명</TH><TH>생년월일</TH><TH>사외이사후보자여부</TH><TH>감사위원회 위원인이사 분리선출 여부</TH><TH>최대주주와의 관계</TH><TH>추천인</TH></TR>
<TR><TD>최순화</TD><TD>1972.05.25.</TD><TD>사외이사</TD><TD>분리선출</TD><TD>-</TD><TD>사외이사후보추천위원회</TD></TR>
</TABLE>
</SECTION-3>
</LIBRARY>
</SECTION-2>
"""

    personnel = parse_personnel_xml(html)

    candidate = personnel["appointments"][0]["candidates"][0]
    assert candidate["name"] == "최순화"
    assert candidate["birthDate"] == "1972.05.25."


def test_agenda_parser_stops_title_at_business_report_attachment_after_agenda():
    text = """
주주총회 소집공고
(제29기 정기)
1. 일시 : 2026년 3월 27일 오전 9시
2. 장소 : 본점
3. 회의 목적사항
[결의사항]
제1호 의안 : 제29기 재무제표 승인의 건
제5호 의안 : 주식매수선택권의 부여
4. 사업보고서 및 감사보고서 첨부
당사는 상법 시행령에 따라 사업보고서 및 감사보고서를 첨부합니다.
"""

    agendas = parse_agenda_xml(text)

    assert agendas[-1]["number"] == "제5호"
    assert agendas[-1]["title"] == "주식매수선택권의 부여"


def test_agenda_parser_stops_title_at_compact_candidate_table_headers():
    text = """
주주총회 소집공고
(제57기 정기)
1. 일시 : 2026년 3월 27일 오전 9시
2. 장소 : 본점
3. 회의 목적사항
[결의사항]
제3-1호 의안 : 감사(상근) 유연갑 선임의 건
성명 생년월 주요약력 추천인 회사와의 최근3년간 거래내역 최대주주와의 관계
유연갑 1964.10 전북대학교 법학대학원 이사회 해당사항 없음
제4-1호 의안 : 사외이사 장병원 선임의 건
성명 주요 약력 추천인 최대주주와의관계 최근 3년회사와의거래내역
장병원 서울대학교 보건대학원 이사회 해당없음
"""

    agendas = parse_agenda_xml(text)

    assert agendas[0]["title"] == "감사(상근) 유연갑 선임의 건"
    assert agendas[1]["title"] == "사외이사 장병원 선임의 건"


def test_agenda_parser_stops_title_at_misc_section_after_stock_option_agenda():
    text = """
주주총회 소집공고
(제26기 정기)
1. 일시 : 2026년 3월 27일 오전 9시
2. 장소 : 본점
3. 회의 목적사항
[결의사항]
제6호 의안 : 주식매수선택권 부여 승인의 건
4. 기타사항
1) 부속정관 제10조 제1항에 의거하여 정기주주총회는 총회 소집공고에서 지정한 장소에서 개최합니다.
"""

    agendas = parse_agenda_xml(text)

    assert agendas[-1]["title"] == "주식매수선택권 부여 승인의 건"


def test_agenda_parser_accepts_spaced_agenda_marker():
    text = """
주주총회 소집공고
(제30기 정기)
1. 일시 : 2026년 3월 31일 오전 9시
2. 장소 : 본점
3. 회의 목적사항
가. 보고사항
나. 부의안건
제1호의 안 : 제30기 연결 및 별도 재무제표 승인의 건
제2호의 안 : 사내이사 장상욱 재선임의 건
제3호의 안 : 이사 보수 한도 승인의 건
제4호의 안 : 감사 보수 한도 승인의 건
4. 경영 참고사항 비치
"""

    agendas = parse_agenda_xml(text)

    assert [item["number"] for item in agendas] == ["제1호", "제2호", "제3호", "제4호"]
    assert agendas[1]["title"] == "사내이사 장상욱 재선임의 건"


def test_compensation_parser_accepts_total_compensation_limit_title():
    html = """
<SECTION-2>
<TITLE>2. 주주총회 목적사항별 기재사항</TITLE>
<LIBRARY>
<SECTION-3>
<TITLE>□ 이사의 보수한도 승인</TITLE>
<P><SPAN>제4호 의안: 2026년 이사 보수총액 한도 승인의 건</SPAN></P>
<P>가. 이사의 수ㆍ보수총액 내지 최고 한도액</P>
<P>(당 기)</P>
<TABLE>
<TR><TH>이사의 수 (사외이사수)</TH><TD>7 (4)</TD></TR>
<TR><TH>보수총액 또는 최고한도액</TH><TD>50.0 억원</TD></TR>
</TABLE>
<P>(전 기)</P>
<TABLE>
<TR><TH>이사의 수 (사외이사수)</TH><TD>7 (4)</TD></TR>
<TR><TH>실제 지급된 보수총액</TH><TD>31.0 억원</TD></TR>
<TR><TH>최고한도액</TH><TD>50.0 억원</TD></TR>
</TABLE>
</SECTION-3>
</LIBRARY>
</SECTION-2>
"""

    compensation = parse_compensation_xml(html)

    assert len(compensation["items"]) == 1
    item = compensation["items"][0]
    assert item["target"] == "이사"
    assert item["current"]["limitAmount"] == 5_000_000_000
    assert item["prior"]["actualPaidAmount"] == 3_100_000_000


def test_compensation_parser_falls_back_to_agenda_title_without_detail_table():
    html = """
<SECTION-1>
<TITLE>주주총회 소집공고</TITLE>
<P>3. 회의 목적사항</P>
<P>제3호 의안 : 이사 보수한도 승인의 건</P>
<P>제4호 의안 : 감사 보수한도 승인의 건</P>
</SECTION-1>
<SECTION-2>
<TITLE>2. 주주총회 목적사항별 기재사항</TITLE>
<LIBRARY>
<SECTION-3>
<TITLE>□ 기타</TITLE>
<P>상세 내용은 주주총회장에서 설명 예정입니다.</P>
</SECTION-3>
</LIBRARY>
</SECTION-2>
"""

    compensation = parse_compensation_xml(html)

    assert [(item["number"], item["target"]) for item in compensation["items"]] == [
        ("제3호", "이사"),
        ("제4호", "감사"),
    ]
    assert compensation["items"][0]["current"] == {}
    assert compensation["items"][0]["notes"] == ["agenda_title_fallback"]


def test_compensation_parser_does_not_create_item_for_compensation_rule_amendment():
    html = """
<SECTION-1>
<TITLE>주주총회 소집공고</TITLE>
<P>3. 회의 목적사항</P>
<P>제2-1호 의안 : 이사 보수한도 규정 신설</P>
<P>제2-2호 의안 : 감사 보수한도 규정 신설</P>
</SECTION-1>
"""

    compensation = parse_compensation_xml(html)

    assert compensation["items"] == []


def test_aoi_parser_accepts_charter_child_detail_without_charter_word():
    html = """
<SECTION-2>
<TITLE>2. 주주총회 목적사항별 기재사항</TITLE>
<LIBRARY>
<SECTION-3>
<TITLE>□ 정관의 변경</TITLE>
<P><SPAN>제2-1호 의안: 집중투표 관련 정관 변경의 건</SPAN></P>
<P>가. 의안의 요지</P>
<TABLE>
<TR><TH>현 행</TH><TH>변 경(안)</TH><TH>변경의 목적</TH></TR>
<TR><TD>제34조 (이사의 선임) ③ 집중투표제를 적용하지 않는다.</TD><TD>제34조 ③항 삭제</TD><TD>상법 개정 반영</TD></TR>
</TABLE>
</SECTION-3>
</LIBRARY>
</SECTION-2>
"""

    aoi = parse_aoi_xml(
        html,
        sub_agendas=[{"number": "제2-1호", "title": "집중투표 관련 정관 변경의 건"}],
    )

    assert len(aoi["amendments"]) == 1
    assert aoi["amendments"][0]["subAgendaId"] == "2-1"
    assert "집중투표" in aoi["amendments"][0]["before"]


def test_aoi_parser_accepts_revision_before_after_headers():
    html = """
<SECTION-2>
<TITLE>2. 주주총회 목적사항별 기재사항</TITLE>
<LIBRARY>
<SECTION-3>
<TITLE>□ 정관의 변경</TITLE>
<P><SPAN>제2-1호 의안: 정관 개정 승인의 건(집중투표제 배제 규정 삭제)</SPAN></P>
<P>가. 의안의 요지</P>
<TABLE>
<TR><TH>개정 전</TH><TH>개정 후</TH><TH>비고</TH></TR>
<TR><TD>제31조 ③ 집중투표제는 적용하지 아니한다.</TD><TD>제31조 ③ 삭제</TD><TD>집중투표제 의무화</TD></TR>
</TABLE>
</SECTION-3>
</LIBRARY>
</SECTION-2>
"""

    aoi = parse_aoi_xml(
        html,
        sub_agendas=[{"number": "제2-1호", "title": "정관 개정 승인의 건(집중투표제 배제 규정 삭제)"}],
    )

    assert len(aoi["amendments"]) == 1
    assert aoi["amendments"][0]["subAgendaId"] == "2-1"
