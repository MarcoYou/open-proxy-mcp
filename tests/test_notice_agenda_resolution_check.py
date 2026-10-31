"""소집공고 안건 출력 개선(2026-11) — 소집결의 안건표 대조(A)와 공고 파서 패턴 수리(B1~B6). 네트워크 0.

DART 에 닿을 수 없는 환경에서 만든 시험이다. 결의 쪽은 저장소에 있는 실제 결의 원문(고려아연 2026-07-21)을
쓰고, 나머지는 실패 사례에서 인용된 문장으로 만든 **합성 원문**이다 — 실제 서식이 더 다양할 수 있다.
"""
import asyncio
from datetime import date
from pathlib import Path

import pytest

from open_proxy_mcp.services import meeting_resolution as M
from open_proxy_mcp.services import shareholder_meeting as SM
from open_proxy_mcp.services.board_attendance import parse_board_attendance_observations
from open_proxy_mcp.services.shareholder_meeting_parser import (
    parse_agenda_xml,
    parse_meeting_info_xml,
    parse_personnel_xml,
)
from open_proxy_mcp.tools._shareholder_meeting_render import render_board, render_summary

_FIX = Path(__file__).parent / "dart_responses" / "dart"
KOREAZINC = "agm_resolution_proposal_cumulative_20260721800927.html"   # 고려아연 임시 — 비고 칸 세부 안건


def _notice(body: str) -> str:
    return ("주주총회 소집공고\n(제50기 정기)\n1. 일 시 : 2026년 3월 27일 오전 9시\n2. 장 소 : 본점\n"
            "3. 회의목적사항\n" + body + "\n4. 경영참고사항 비치\n")


def _walk(nodes):
    for n in nodes:
        yield n
        yield from _walk(n.get("children") or [])


def _by_no(nodes):
    return {n["number"]: n for n in _walk(nodes)}


def _resolution_html(rows: str, dt: str = "2026-03-27") -> str:
    return ("<table><tr><td colspan='2'>1. 구분</td><td colspan='2'>정기주주총회</td></tr>"
            f"<tr><td colspan='2'>2. 일시</td><td>{dt}</td><td>09 : 00</td></tr>"
            "<tr><td colspan='2'>4. 의결권행사기준일</td><td colspan='2'>2025-12-31</td></tr>"
            "<tr><td colspan='2'>5. 이사회결의일(결정일)</td><td colspan='2'>2026-02-20</td></tr></table>"
            "<table><tr><td>번호</td><td>회의목적사항</td><td>비고</td></tr>" + rows + "</table>")


# ── A. 소집결의 안건표를 뼈대로 ─────────────────────────────────────────

def test_folded_notice_items_get_the_resolution_sub_items_and_their_proposal_marks():
    """DB손해보험 2026 정기 모양 — 공고가 2·3 을 하나씩으로 접었다. 결의 비고 칸의 세부 안건이 이긴다."""
    res = M.parse_resolution_document((_FIX / KOREAZINC).read_text("utf-8"))
    notice = parse_agenda_xml(_notice(
        "제1호 의안 : 정관 일부 변경의 건\n"
        "제2호 의안 : 집중투표에 의한 이사 4인 선임의 건\n"
        "제3호 의안 : 감사위원회 위원이 되는 독립이사 선임의 건"))
    merged, check = M.merge_resolution_agenda(notice, res["agenda"]["items"])
    nodes = _by_no(SM._agenda_nodes(merged))
    assert [n for n in nodes] == ["제1호", "제2호", "제2-1호", "제2-2호", "제2-3호", "제2-4호",
                                  "제3호", "제3-1호", "제3-2호"]
    assert nodes["제2호"]["map_source"] == "both"
    assert nodes["제2-1호"]["map_source"] == "resolution_only"
    # 주주제안 표시는 줄마다 — 회사 후보(2-1·2-2)에 묻어가지 않는다
    assert nodes["제2-3호"]["proposer_type"] == "shareholder_proposal"
    assert nodes["제2-1호"]["proposer_type"] == "company"
    assert nodes["제3-2호"]["proposer_type"] == "shareholder_proposal"
    assert "일괄표결" in nodes["제3호"]["resolution_remark"]
    assert check["basis"] == "resolution"
    assert [i["number"] for i in check["only_in_resolution"]] == [
        "제2-1호", "제2-2호", "제2-3호", "제2-4호", "제3-1호", "제3-2호"]
    assert check["only_in_notice"] == [] and check["title_differs"] == []
    # 공고 원본 트리는 건드리지 않는다
    assert notice[1]["children"] == []


def test_notice_only_items_are_kept_and_marked():
    """진도 2026-10-08 임시 모양 — 공고의 7-1 「감사 추가 선임 여부의 건」이 결의에 없다. 지우지 않는다."""
    rows = ("<tr><td>6</td><td>제6호 의안 : 이사 선임의 건</td><td>-</td></tr>"
            "<tr><td>7</td><td>제7호 의안 : 감사 선임의 건</td><td>7-2. 감사 갑 선임의 건</td></tr>")
    res = M.parse_resolution_document(_resolution_html(rows))
    notice = parse_agenda_xml(_notice(
        "제6호 의안 : 이사 선임의 건\n제6-1호 의안 : 사내이사 을 선임의 건\n"
        "제7호 의안 : 감사 선임의 건\n제7-1호 의안 : 감사 추가 선임 여부의 건\n제7-2호 의안 : 감사 갑 선임의 건"))
    merged, check = M.merge_resolution_agenda(notice, res["agenda"]["items"])
    nodes = _by_no(merged)
    assert nodes["제7-1호"]["map_source"] == "notice_only"
    assert check["only_in_notice"] == [{"number": "제7-1호", "title": "감사 추가 선임 여부의 건"}]
    # 결의가 세부 안건을 말하지 않은 부모(6) 아래 공고 세부 안건은 「공고에만」으로 세지 않는다
    assert nodes["제6-1호"]["map_source"] == "notice_sub_item"
    assert nodes["제7-2호"]["map_source"] == "both"


def test_title_difference_is_reported_not_overwritten():
    rows = "<tr><td>1</td><td>제1호 의안 : 자기주식 소각의 건</td><td>-</td></tr>"
    res = M.parse_resolution_document(_resolution_html(rows))
    notice = parse_agenda_xml(_notice("제1호 의안 : 이사 보수한도 승인의 건"))
    merged, check = M.merge_resolution_agenda(notice, res["agenda"]["items"])
    assert merged[0]["title"] == "이사 보수한도 승인의 건"          # 공고 제목은 그대로
    assert merged[0]["resolution_title"] == "자기주식 소각의 건"
    assert check["title_differs"][0]["number"] == "제1호"


class _Client:
    def __init__(self, docs):
        self.docs, self.opened = docs, []

    async def get_document_cached(self, rcept_no):
        self.opened.append(rcept_no)
        return {"html": self.docs[rcept_no], "text": ""}


def _f(rcept_no, report_nm):
    return {"rcept_no": rcept_no, "rcept_dt": rcept_no[:8], "report_nm": report_nm}


def _fake_dart(monkeypatch, filings, docs):
    client = _Client(docs)
    calls = []

    async def search(**kw):
        calls.append(kw)
        return list(filings), [], None

    monkeypatch.setattr(M, "search_filings_by_report_name", search)
    monkeypatch.setattr(M, "get_dart_client", lambda: client)
    return client, calls


def test_only_resolutions_filed_by_the_notice_date_and_same_meeting_are_used(monkeypatch):
    rows = "<tr><td>1</td><td>제1호 의안 : 재무제표 승인의 건</td><td>-</td></tr>"
    client, calls = _fake_dart(monkeypatch, [
        _f("20260220000001", "주주총회소집결의 (정기주주총회)"),
        _f("20260225000001", "주주총회소집결의 (임시주주총회)"),            # 종류가 다르다
        _f("20260320000001", "[기재정정]주주총회소집결의 (정기주주총회)"),   # 공고 뒤 — 쓰지 않는다
    ], {"20260220000001": _resolution_html(rows), "20260225000001": _resolution_html(rows),
        "20260320000001": _resolution_html(rows)})
    found = asyncio.run(M.find_resolution_for_notice(
        "00000001", meeting_type="annual", meeting_date=date(2026, 3, 27), as_of="20260310"))
    assert found["status"] == "found" and found["rcept_no"] == "20260220000001"
    assert found["meeting_date_checked"] is True
    assert client.opened == ["20260220000001"]                       # 원문 1건만
    assert calls[0]["pblntf_detail_ty"] == "I001" and calls[0]["end_de"] == "20260310"


def test_a_resolution_for_another_meeting_date_is_not_used(monkeypatch):
    rows = "<tr><td>1</td><td>제1호 의안 : 재무제표 승인의 건</td><td>-</td></tr>"
    _fake_dart(monkeypatch, [_f("20260220000001", "주주총회소집결의 (정기주주총회)")],
               {"20260220000001": _resolution_html(rows, dt="2026-03-30")})
    found = asyncio.run(M.find_resolution_for_notice(
        "00000001", meeting_type="annual", meeting_date=date(2026, 3, 27), as_of="20260310"))
    assert found["status"] == "date_mismatch"


def test_cross_check_falls_back_to_the_notice_with_a_warning(monkeypatch):
    _fake_dart(monkeypatch, [], {})
    notice = parse_agenda_xml(_notice("제1호 의안 : 재무제표 승인의 건"))
    tree, check, ref, warns = asyncio.run(SM._resolution_cross_check(
        notice, corp_code="00000001", meeting_type="annual", meeting_date=date(2026, 3, 27),
        notice_rcept_no="20260310000001"))
    assert tree is notice and ref is None
    assert check["basis"] == "notice" and check["reason"]
    assert "접혔거나 빠졌을 수 있다" in warns[0]
    # 공시번호 직접 지정(회사 코드 없음)은 찾지 않는다 — DART 콜 0
    tree, check, ref, warns = asyncio.run(SM._resolution_cross_check(
        notice, corp_code="", meeting_type="annual", meeting_date=None, notice_rcept_no="20260310000001"))
    assert check["basis"] == "notice" and "직접 지정" in check["reason"]


def test_cross_check_returns_the_merged_tree_and_the_summary_renders_the_check(monkeypatch):
    rows = ("<tr><td>1</td><td>제1호 의안 : 정관 변경의 건</td>"
            "<td>1-1. 집중투표 배제 조항 삭제의 건(주주제안)<br/>1-2. 사업목적 추가의 건</td></tr>")
    _fake_dart(monkeypatch, [_f("20260220000001", "주주총회소집결의 (정기주주총회)")],
               {"20260220000001": _resolution_html(rows)})
    notice = parse_agenda_xml(_notice("제1호 의안 : 정관 변경의 건"))
    tree, check, ref, warns = asyncio.run(SM._resolution_cross_check(
        notice, corp_code="00000001", meeting_type="annual", meeting_date=date(2026, 3, 27),
        notice_rcept_no="20260310000001"))
    assert ref["rcept_no"] == "20260220000001" and check["resolution"]["meeting_date_checked"]
    nodes = SM._agenda_nodes(tree)
    assert [c["number"] for c in nodes[0]["children"]] == ["제1-1호", "제1-2호"]
    assert nodes[0]["children"][0]["proposer_type"] == "shareholder_proposal"
    assert "결의에만 제1-1호, 제1-2호" in warns[0]
    md = render_summary({"status": "exact", "warnings": warns, "data": {
        "canonical_name": "시험회사", "agendas": nodes, "agenda_check": check,
        "agenda_summary": {"root_count": 1, "total_count": 3}}})
    assert "안건 뼈대: 소집결의 안건표 `20260220000001`" in md
    assert "결의 안건표에만 있음" in md


# ── B1. 해임은 선임이 아니다 ──────────────────────────────────────────

def test_dismissal_items_are_not_elections_and_targets_are_not_candidates():
    """씨씨에스 2026-09-23 임시 「독립이사 ○○○ 해임의 건」이 director_election 으로 나왔다."""
    agenda = parse_agenda_xml(_notice("제1호 의안 : 독립이사 홍길동 해임의 건\n"
                                      "제2호 의안 : 감사위원회 위원 김철수 해임의 건\n"
                                      "제3호 의안 : 독립이사 이영희 선임의 건\n"
                                      "제4호 의안 : 감사위원 선·해임 시 의결권 제한 조항 삽입의 건"))
    cats = [n["category"] for n in SM._agenda_nodes(agenda)]
    assert cats[0] == "director_dismissal" and cats[2] == "director_election"
    assert cats[1] not in SM._ELECTION_CATEGORIES
    assert cats[3] != "director_dismissal"           # 「선·해임 시」 정관 안건은 해임이 아니다
    # 해임 묶음 아래 이름뿐인 자식도 해임이다
    tree = SM._agenda_nodes(parse_agenda_xml(_notice(
        "제1호 의안 : 이사 해임의 건\n제1-1호 의안 : 사외이사 홍길동")))
    assert tree[0]["children"][0]["category"] == "director_dismissal"
    html = ("<SECTION-2><TITLE>2. 주주총회 목적사항별 기재사항</TITLE>"
            "<LIBRARY><SECTION-3><TITLE>□ 이사의 해임</TITLE>"
            "<P>제1호 의안: 독립이사 홍길동 해임의 건</P><P>가. 의안의 요지 : 해임</P></SECTION-3></LIBRARY>"
            "<LIBRARY><SECTION-3><TITLE>□ 이사의 선임</TITLE>"
            "<P>제3호 의안: 독립이사 이영희 선임의 건</P></SECTION-3></LIBRARY></SECTION-2>")
    p = parse_personnel_xml(html)
    dismissal = p["appointments"][0]
    assert dismissal["action"] == "해임" and dismissal["candidates"] == []
    assert [c["name"] for c in dismissal["dismissal_targets"]] == ["홍길동"]
    s = p["summary"]
    assert s["unique_candidates"] == 1 and s["total_candidates"] == 1 and s["dismissals"] == 1
    md = render_board({"status": "exact", "warnings": [], "data": {"board": p, "board_summary": s}})
    assert "해임 대상: **홍길동** (후보 아님)" in md


# ── B2. 범위 주석은 안건 제목이 아니다 ─────────────────────────────────

@pytest.mark.parametrize("body", [
    # 제1-1호 제목 뒤 괄호에 붙은 꼴 — 종전: 1-1 「주주제안 변경안」에서 잘림, 1-2 「제외)부터」
    "제1호 의안 : 정관 일부 변경의 건\n"
    "제1-1호 의안 : 주주제안 변경안 (제1-1호 의안(제1-2호 의안 제외)부터 제5호 의안은 모두 주주제안입니다)\n"
    "제1-2호 의안 : 회사제안 변경안\n제2호 의안 : 감사 갑 선임의 건\n제3호 의안 : 이사 을 선임의 건\n"
    "제4호 의안 : 자기주식 소각의 건\n제5호 의안 : 현금배당의 건",
    # 목록 앞에 문장으로 온 꼴 — 종전: 제1-2호가 맨 앞 루트로 「제외)부터」
    "제1-1호 의안(제1-2호 의안 제외)부터 제5호 의안은 모두 주주제안입니다.\n"
    "제1호 의안 : 정관 일부 변경의 건\n제1-1호 의안 : 주주제안 변경안\n제1-2호 의안 : 회사제안 변경안\n"
    "제2호 의안 : 감사 갑 선임의 건\n제3호 의안 : 이사 을 선임의 건\n제4호 의안 : 자기주식 소각의 건\n"
    "제5호 의안 : 현금배당의 건",
])
def test_range_sentence_is_not_a_title_and_marks_the_range_as_shareholder_proposals(body):
    nodes = _by_no(parse_agenda_xml(_notice(body)))
    assert list(nodes) == ["제1호", "제1-1호", "제1-2호", "제2호", "제3호", "제4호", "제5호"]
    assert nodes["제1-1호"]["title"] == "주주제안 변경안"
    assert nodes["제1-2호"]["title"] == "회사제안 변경안"
    assert all("부터" not in n["title"] for n in nodes.values())
    marked = {k for k, n in nodes.items() if n.get("source") == "주주제안"}
    assert marked == {"제1-1호", "제2호", "제3호", "제4호", "제5호"}       # 제1-2호·제1호 제외
    assert "제1-2호 의안 제외" in nodes["제2호"]["source_note"]
    assert not any(n.get("conditional") for n in nodes.values())      # 주석이 조건절로 새지 않는다


def test_conditional_range_sentence_still_reads_as_a_condition():
    nodes = _by_no(parse_agenda_xml(_notice(
        "제2호 의안 : 이사 수 결정의 건\n제3호 의안 : 이사 선임의 건\n"
        "제3-1호 의안 : 사내이사 갑 선임의 건\n제3-2호 의안 : 사내이사 을 선임의 건\n"
        "※ 제3-1호부터 제3-2호 의안은 제2호 의안이 가결될 경우 상정됩니다.")))
    assert "가결될 경우" in nodes["제3-1호"]["conditional"]
    assert not nodes["제3-1호"].get("source")


# ── B3. 구획 표제는 앞 제목의 꼬리가 아니다 ────────────────────────────

def test_section_heading_belongs_to_the_following_items():
    """대호에이엘 2026-08 임시 — 「감사 갑 선임의 건 ― 소수주주 제안 안건 ―」."""
    nodes = _by_no(parse_agenda_xml(_notice(
        "제1호 의안 : 정관 일부 변경의 건\n제2호 의안 : 감사 갑 선임의 건\n― 소수주주 제안 안건 ―\n"
        "제3호 의안 : 감사 을 선임의 건\n제4호 의안 : 이사 병 해임의 건")))
    assert nodes["제2호"]["title"] == "감사 갑 선임의 건"
    assert not nodes["제2호"].get("source")
    assert nodes["제3호"]["source"] == nodes["제4호"]["source"] == "주주제안"
    assert nodes["제3호"]["source_note"] == "― 소수주주 제안 안건 ―"


def test_tag_in_parentheses_is_still_the_items_own_tag():
    nodes = _by_no(parse_agenda_xml(_notice(
        "제1호 의안 : 감사 갑 선임의 건(주주제안)\n제2호 의안 : 이사 을 선임의 건")))
    assert nodes["제1호"]["source"] == "주주제안" and not nodes["제2호"].get("source")


# ── B4. 후보 목록 중복·사퇴 ─────────────────────────────────────────

def _cand_table(rows):
    head = ("<TR><TH>후보자성명</TH><TH>생년월일</TH><TH>사외이사후보자여부</TH>"
            "<TH>최대주주와의 관계</TH><TH>추천인</TH></TR>")
    body = "".join(f"<TR><TD>{n}</TD><TD>{b}</TD><TD>사외이사</TD><TD>-</TD><TD>이사회</TD></TR>"
                   for n, b in rows)
    return "<TABLE>" + head + body + "</TABLE>"


def _section(title, table, extra=""):
    return ("<LIBRARY><SECTION-3><TITLE>□ 이사의 선임</TITLE>"
            f"<P>{title}</P><P>가. 후보자의 성명ㆍ생년월일ㆍ추천인ㆍ최대주주와의 관계ㆍ사외이사후보자 등 여부</P>"
            f"{table}{extra}</SECTION-3></LIBRARY>")


def test_parent_and_child_duplicates_and_withdrawn_candidates():
    """고려아연 2025 정기·태광산업 2026 정기 모양 — 제3호와 제3-x호에 같은 후보, 사퇴한 후보가 그대로."""
    html = ("<SECTION-2><TITLE>2. 주주총회 목적사항별 기재사항</TITLE>"
            + _section("제3호 의안: 사외이사 선임의 건",
                       _cand_table([("김갑동", "1960.01.01"), ("이을순", "1965.02.02"), ("오영", "1970.03.03")]))
            + _section("제3-1호 의안: 사외이사 김갑동 선임의 건", _cand_table([("김갑동", "")]))
            + _section("제3-2호 의안: 사외이사 이을순 선임의 건", _cand_table([("이을순", "1965.02.02")]))
            + _section("제3-3호 의안: 사외이사 오영 선임의 건", _cand_table([("오영", "1970.03.03")]),
                       "<P>※ 오영 후보자는 일신상의 사유로 자진 사퇴하였습니다.</P>")
            + "</SECTION-2>")
    p = parse_personnel_xml(html)
    by_no = {a["number"]: a for a in p["appointments"]}
    assert by_no["제3호"]["candidates"] == []
    assert by_no["제3호"]["candidates_in_children"] == [
        {"name": "김갑동", "number": "제3-1호"}, {"name": "이을순", "number": "제3-2호"}]
    assert by_no["제3-1호"]["candidates"][0]["birthDate"] == "1960.01.01"   # 묶음 쪽에만 있던 칸을 옮겨 둔다
    assert by_no["제3-3호"]["candidates"] == []
    assert [c["name"] for c in by_no["제3-3호"]["withdrawn_candidates"]] == ["오영"]
    assert [c["name"] for c in by_no["제3호"]["withdrawn_candidates"]] == ["오영"]
    s = p["summary"]
    assert s["total_candidates"] == 2 and s["unique_candidates"] == 2 and s["outside_directors"] == 2
    assert s["withdrawn_candidates"] == 1


def test_a_conditional_withdrawal_sentence_does_not_withdraw_anyone():
    html = ("<SECTION-2><TITLE>2. 주주총회 목적사항별 기재사항</TITLE>"
            + _section("제3-1호 의안: 사외이사 오영 선임의 건", _cand_table([("오영", "1970.03.03")]),
                       "<P>※ 오영 후보자가 사퇴하는 경우 본 안건은 상정하지 않습니다.</P>")
            + "</SECTION-2>")
    p = parse_personnel_xml(html)
    assert [c["name"] for c in p["appointments"][0]["candidates"]] == ["오영"]
    assert "withdrawn_candidates" not in p["appointments"][0]


# ── B5. 회의 일시 ──────────────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    ("주주총회 소집공고\n(제30기 정기)\n1. 일 시 : 2026년 3월 31일(화) 오전 10시\n2. 장 소 : 본점\n",
     "2026년 3월 31일(화) 오전 10시"),
    ("주주총회 소집공고\n(제30기 정기)\n가. 일 시 2026년 3월 31일(화) 오전 10시\n나. 장 소 경기도\n",
     "2026년 3월 31일(화) 오전 10시"),
    ("주주총회 소집공고\n(제30기 정기)\n일 시\n2026년 3월 31일(화) 오전 10시\n장 소\n본점 대회의실\n",
     "2026년 3월 31일(화) 오전 10시"),
    ("임시주주총회 소집공고\n1. 일시 및 장소\n가. 일 시 : 2025년 12월 23일 (화요일) 오전 9시 30분\n나. 장 소 : 본사\n",
     "2025년 12월 23일 (화요일) 오전 9시 30분"),
    ("임시주주총회 소집공고\n상법 제363조에 의거 2025년 12월 23일(화) 오전 9시에 당사 본사 회의실에서 "
     "임시주주총회를 개최하오니\n회의목적사항\n전자투표 기간 2025년 12월 13일 9시 부터\n",
     "2025년 12월 23일(화) 오전 9시"),
    ("주주총회 소집공고\n1) 일시 2026/03/31 10:00\n2) 장소 본점\n", "2026/03/31 10:00"),
])
def test_meeting_datetime_variants(text, expected):
    info = parse_meeting_info_xml(text)
    assert info["datetime"] == expected
    assert SM._parse_notice_meeting_date(info["datetime"]) is not None


def test_electronic_voting_period_is_not_the_meeting_date():
    info = parse_meeting_info_xml("주주총회 소집공고\n전자투표 행사기간 : 2026년 3월 21일 오전 9시 부터 "
                                  "2026년 3월 30일 오후 5시 까지\n")
    assert info["datetime"] is None


# ── B6. 이사회 출석률 표 모양 ───────────────────────────────────────

def _annual(body):
    return '<DOCUMENT><SECTION-2><TITLE>1. 이사회에 관한 사항</TITLE>' + body + '</SECTION-2></DOCUMENT>'


def test_attendance_in_a_name_row_over_a_rate_row_is_paired_by_column():
    """이름 줄 아래 값 줄 — 종전엔 글 순서로 짝지어 셋째 이름(정평영)에 첫째 값(100%)이 붙었다."""
    parsed = parse_board_attendance_observations(_annual(
        '<P>나. 이사회의 주요 활동내역</P><P>기간 : 2025.1.1 ~ 2025.12.31</P>'
        '<TABLE><TR><TH ROWSPAN="3">회차</TH><TH ROWSPAN="3">개최일자</TH><TH ROWSPAN="3">의안내용</TH>'
        '<TH COLSPAN="3">이사의 성명</TH></TR>'
        '<TR><TH>정안식</TH><TH>조진환</TH><TH>정평영</TH></TR>'
        '<TR><TH>(출석률: 100%)</TH><TH>(출석률: 94.1%)</TH><TH>(출석률: 58.8%)</TH></TR>'
        '<TR><TD>1</TD><TD>2025.01.20</TD><TD>안건</TD><TD>찬성</TD><TD>찬성</TD><TD>불참</TD></TR></TABLE>'
        '<P>다. 이사회 내 위원회</P><P>정평영(출석률:33%)</P>'))
    assert parsed["status"] == "parsed"
    assert [(o["name"], o["attendance_pct"]) for o in parsed["observations"]] == [
        ("정안식", 100.0), ("조진환", 94.1), ("정평영", 58.8)]
    assert parsed["observations"][2]["period_end"] == "2025.12.31"
    assert parsed["observations"][0]["span"]["basis"] == "table_columns"


def test_attendance_with_name_and_rate_in_one_cell():
    parsed = parse_board_attendance_observations(_annual(
        '<P>나. 이사회의 주요 활동내역 (출석률)</P>'
        '<TABLE><TR><TH>회차</TH><TH>개최일자</TH><TH>정안식<BR/>(사외이사)<BR/>출석률 100%</TH>'
        '<TH>정평영 58.8%</TH></TR><TR><TD>1</TD><TD>2025.01.20</TD><TD>찬성</TD><TD>찬성</TD></TR></TABLE>'))
    rows = parsed["observations"]
    assert [(o["name"], o["attendance_pct"]) for o in rows] == [("정안식", 100.0), ("정평영", 58.8)]
    for o in rows:
        span = o["span"]
        assert parsed["section_text"][span["start"]:span["end"]] == o["quote"]


def test_rates_without_an_attendance_header_are_not_read():
    """출석률이라는 말이 없는 표의 % 는 지분율일 수도 있다 — 읽지 않는다."""
    parsed = parse_board_attendance_observations(_annual(
        '<TABLE><TR><TH>정안식</TH><TH>정평영</TH></TR><TR><TD>10%</TD><TD>5.5%</TD></TR></TABLE>'))
    assert parsed["status"] == "format_unsupported"
