import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from open_proxy_mcp.tools.parser import parse_agenda_xml, parse_meeting_info_xml


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
