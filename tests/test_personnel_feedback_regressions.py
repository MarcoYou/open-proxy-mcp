"""Synthetic DART-boundary regressions; no live API or local cache fallback.

Only the client is replaced. Company resolution, document parsing, roster
selection and candidate evaluation run production code with fixed expectations.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import socket

from bs4 import BeautifulSoup
import pytest
from starlette.testclient import TestClient

from open_proxy_mcp.dart.client import DartClientError
from open_proxy_mcp.services import company, director_board as board
from open_proxy_mcp.services import director_evaluation as evaluation
from open_proxy_mcp.services import shareholder_meeting as meeting
from open_proxy_mcp.services.shareholder_meeting_parser import parse_personnel_xml
from open_proxy_mcp.server import build_app
from open_proxy_mcp.tools._shareholder_meeting_render import render_board


CORP = "00000001"
RECEIPT = "20260301000001"


def _document(title="사내이사 홍길동 선임의 건", people=(("홍길동", "사내이사"),), *, role_column=True, term=None):
    rows = "".join(
        f"<TR><TD>{name}</TD><TD>1970.03.01</TD>"
        + (f"<TD>{role}</TD>" if role_column else "")
        + (f"<TD>{term}</TD>" if term is not None else "") + "</TR>"
        for name, role in people
    )
    careers = "".join(
        f"<TR><TD>{name}</TD><TD>이사</TD><TD>2019~현재</TD>"
        f"<TD>시험법인 사내이사</TD><TD>해당사항 없음</TD></TR>"
        for name, _ in people
    )
    html = f"""<SECTION-2><TITLE>2. 주주총회 목적사항별 기재사항</TITLE>
    <LIBRARY><SECTION-3><TITLE>□ 이사의 선임</TITLE>
    <P>제2호 의안: {title}</P>
    <P>가. 후보자의 성명ㆍ생년월일ㆍ추천인ㆍ최대주주와의 관계</P>
    <TABLE><TR><TH>후보자성명</TH><TH>생년월일</TH>{'<TH>직위</TH>' if role_column else ''}{'<TH>임기</TH>' if term is not None else ''}</TR>{rows}</TABLE>
    <P>나. 후보자의 주된직업ㆍ세부경력ㆍ해당법인과의 최근3년간 거래내역</P>
    <TABLE><TR><TH ROWSPAN="2">후보자성명</TH><TH ROWSPAN="2">주된직업</TH>
    <TH COLSPAN="2">세부경력</TH><TH ROWSPAN="2">해당법인과의최근3년간 거래내역</TH></TR>
    <TR><TH>기간</TH><TH>내용</TH></TR>{careers}</TABLE>
    </SECTION-3></LIBRARY></SECTION-2>"""
    return {"html": html, "text": BeautifulSoup(html, "html.parser").get_text(" "), "images": []}


def _row(name="홍길동", role="사내이사"):
    return {"nm": name, "birth_ym": "1970년 03월", "rgist_exctv_at": role,
            "hffc_pd": "2020.03.01~", "tenure_end_on": "2026년 03월 31일",
            "sexdstn": "남", "ofcps": "이사", "rcept_no": "20260301000002"}


def _response(rows):
    return {"status": "000", "list": rows}


class BoundaryClient:
    def __init__(self, documents, executives, outside):
        self.documents = deepcopy(documents)
        self.executives = deepcopy(executives)
        self.outside = deepcopy(outside)
        self.unexpected = []

    def _read(self, source, key):
        if key not in source:
            self.unexpected.append(key)
            raise AssertionError(f"Missing synthetic DART response: {key}")
        response = deepcopy(source[key])
        if isinstance(response, str):
            raise DartClientError(response, "synthetic DART status")
        return response

    def api_call_snapshot(self):
        return 0

    async def lookup_corp_code_all(self, query):
        assert query == CORP
        return [{"corp_code": CORP, "corp_name": "시험법인", "stock_code": "000001"}]

    async def search_filings(self, **kwargs):
        assert kwargs["corp_code"] == CORP
        return {"status": "000", "total_count": 1, "list": [
            {"corp_code": CORP, "rcept_no": RECEIPT, "rcept_dt": "20260301",
             "report_nm": "주주총회소집공고"}]}

    async def get_document_cached(self, receipt):
        return self._read(self.documents, receipt)

    async def get_executive_status(self, corp, year, report="11011"):
        assert corp == CORP
        return self._read(self.executives, (year, report))

    async def get_outside_director_changes(self, corp, year, report="11011"):
        assert corp == CORP
        return self._read(self.outside, (year, report))


@pytest.fixture
def boundary(monkeypatch):
    """Caught network/fixture errors must still fail the test at teardown."""
    attempts = []
    clients = []

    def deny(*args, **kwargs):
        attempts.append("network")
        raise AssertionError("Network is forbidden in personnel regressions")

    def guard_network_socket(original):
        def guarded(sock, *args, **kwargs):
            if sock.family in (socket.AF_INET, socket.AF_INET6):
                return deny()
            return original(sock, *args, **kwargs)
        return guarded

    # TestClient is an in-process ASGI transport. Its event loop needs AF_UNIX
    # socketpair wakeups; deny only actual IP traffic, including loopback/DNS.
    for method in ("connect", "connect_ex", "send", "sendall", "sendto"):
        monkeypatch.setattr(socket.socket, method, guard_network_socket(getattr(socket.socket, method)))
    for method in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr"):
        monkeypatch.setattr(socket, method, deny)

    def install(*, rows=None, document=None, executives=None, outside=None):
        client = BoundaryClient(
            {RECEIPT: document if document is not None else _document()},
            executives if executives is not None else {("2025", "11011"): _response(rows)},
            outside if outside is not None else {},
        )
        clients.append(client)
        for module in (company, board, evaluation, meeting):
            monkeypatch.setattr(module, "get_dart_client", lambda: client)
        return client

    yield install
    assert not attempts, "A caught exception must not hide an attempted external call"
    assert not [key for client in clients for key in client.unexpected], "Missing boundary fixtures"


def _evaluate():
    result = asyncio.run(evaluation.build_director_evaluation_payload(
        CORP, year=2026, meeting_type="auto", check_audit_history=False))
    people = result["data"]["evaluations"]
    assert len(people) == 1, result
    assert people[0]["name"] == "홍길동"
    return people[0]["appointment_type"]


@pytest.mark.parametrize("roles", [(None,), ("",), (" \n ",), ("미등기임원", "")])
def test_blank_registration_does_not_confirm_unregistered(boundary, roles):
    rows = [_row(role=role) for role in roles] + [_row("박이사")]
    client = boundary(rows=rows)
    original = deepcopy(client.executives)
    apt = _evaluate()
    assert apt["board_earliest_start"] == 2019
    source = apt["board_tenure_source"]
    assert "확정하지 못했" in source["note"]
    assert "재직 없음" not in source["note"]
    assert source["notice_estimate"] == 2019
    assert source["roster_row"]["재직기간"] == "2020.03.01~"
    assert client.executives == original


def test_explicit_unregistered_still_clears_notice_estimate(boundary):
    boundary(rows=[_row(role="미등기임원"), _row("박이사")])
    apt = _evaluate()
    assert apt["board_earliest_start"] is None
    assert "미등기임원으로만" in apt["board_tenure_source"]["note"]


@pytest.mark.parametrize("missing", ["013", {"status": "000", "list": []}])
def test_absent_current_roster_is_unknown_not_everyone_departed(boundary, missing):
    executives = {("2025", code): missing for code in ("11011", "11014", "11012", "11013")}
    executives[("2024", "11011")] = _response([_row()])
    boundary(executives=executives, outside={
        ("2025", "11011"): _response([{"apnt": "1"}]),
        ("2024", "11011"): "013",
    })
    result = asyncio.run(board.build_director_board_payload(CORP, scope="roster", year=2025, lookback_years=2))
    roster = result["data"]["roster"]
    assert roster["changes_vs_prev_year"] == []
    assert roster["executive_changes_vs_prev_year"] == []
    assert roster["diff_cross_check"] is None
    assert roster["comparison_status"] == "unknown"
    assert roster["roster_as_of"] is None
    assert result["status"] == "requires_review"
    assert any("비교 미산출" in w for w in result["warnings"])
    assert any("임원" in w and "원문" in w for w in result["warnings"])


@pytest.mark.parametrize("current_report", ["11011", "11014"])
def test_valid_current_roster_without_requested_comparison_is_exact(boundary, current_report):
    # 전년 응답을 넣지 않는다. 비교를 요청하지 않았는데 읽으면 경계 대역이 실패한다.
    boundary(executives={
        ("2025", "11011"): "013",
        ("2025", current_report): _response([_row()]),
    }, outside={("2025", "11011"): _response([{"apnt": "1"}])})
    result = asyncio.run(board.build_director_board_payload(CORP, scope="roster", year=2025, lookback_years=1))
    roster = result["data"]["roster"]
    assert result["status"] == "exact"
    assert roster["comparison_status"] == "not_requested"
    assert [r["name"] for r in roster["roster"]] == ["홍길동"]
    assert roster["roster_as_of"] == ("2025년 사업보고서" if current_report == "11011" else "2025년 3분기보고서")
    assert roster["changes_vs_prev_year"] == []
    assert roster["changes_since_last_annual"] == []
    assert roster["diff_cross_check"] is None
    assert not any("비교 미산출" in w or "2024년" in w for w in result["warnings"])


@pytest.mark.parametrize("missing", ["013", {"status": "000", "list": []}])
def test_requested_comparison_without_prior_is_explicit(boundary, missing):
    boundary(executives={
        ("2025", "11011"): _response([_row()]), ("2024", "11011"): missing,
    }, outside={
        ("2025", "11011"): _response([{"apnt": "1"}]), ("2024", "11011"): "013",
    })
    result = asyncio.run(board.build_director_board_payload(CORP, scope="roster", year=2025, lookback_years=2))
    roster = result["data"]["roster"]
    assert roster["comparison_status"] == "no_prior"
    assert result["status"] == "requires_review"
    assert [r["name"] for r in roster["roster"]] == ["홍길동"]
    assert roster["roster_as_of"] == "2025년 사업보고서"
    assert roster["headcount_board"] == 1
    assert roster["changes_vs_prev_year"] == []
    assert roster["diff_cross_check"] is None
    assert any("현재 명단은 확인했으나" in w and "2024년" in w and "비교 미산출" in w
               for w in result["warnings"])


def test_missing_current_roster_is_unknown_even_without_comparison(boundary):
    boundary(executives={
        ("2025", code): "013" for code in ("11011", "11014", "11012", "11013")
    }, outside={("2025", "11011"): "013"})
    result = asyncio.run(board.build_director_board_payload(CORP, scope="roster", year=2025, lookback_years=1))
    roster = result["data"]["roster"]
    assert roster["comparison_status"] == "unknown"
    assert result["status"] == "requires_review"
    assert roster["roster_as_of"] is None
    assert roster["changes_vs_prev_year"] == []
    assert any("임원현황을 확인하지 못해" in w for w in result["warnings"])


@pytest.mark.parametrize("current_report", ["11011", "11014"])
def test_independent_director_counts_and_changes_include_the_raw_role(boundary, current_report):
    executives = {("2024", "11011"): _response([_row("박이사")]),
                  ("2025", "11011"): "013",
                  ("2025", current_report): _response([_row("박이사"), _row(role="독립이사")])}
    boundary(executives=executives, outside={
        ("2025", "11011"): "013", ("2024", "11011"): "013"})
    result = asyncio.run(board.build_director_board_payload(CORP, scope="roster", year=2025, lookback_years=2))
    roster = result["data"]["roster"]
    assert roster["headcount_board"] == 2
    assert [(r["name"], r["director_type"]) for r in roster["changes_vs_prev_year"]] == [("홍길동", "독립이사")]
    assert roster["executive_changes_vs_prev_year"] == []
    assert result["status"] == "exact"
    if current_report == "11014":
        assert [(r["name"], r["director_type"]) for r in roster["changes_since_last_annual"]] == [("홍길동", "독립이사")]


def test_independent_director_tenure_survives_report_selection(boundary):
    boundary(executives={
        ("2025", "11011"): _response([_row(role="독립이사")]),
        ("2025", "11014"): "013", ("2025", "11012"): "013",
        ("2024", "11011"): "013",
    }, document=_document("독립이사 홍길동 선임의 건", (("홍길동", "독립이사"),)))
    apt = _evaluate()
    assert apt["board_earliest_start"] == 2020
    assert apt["board_tenure_source"]["director_type"] == "독립이사"
    assert apt["board_tenure_source"]["tenure_raw"] == "2020.03.01~"


def test_available_rosters_still_report_an_actual_departure(boundary):
    boundary(executives={
        ("2024", "11011"): _response([_row(), _row("박이사")]),
        ("2025", "11011"): _response([_row("박이사")]),
    }, outside={("2025", "11011"): "013", ("2024", "11011"): "013"})
    result = asyncio.run(board.build_director_board_payload(CORP, scope="roster", year=2025, lookback_years=2))
    changes = result["data"]["roster"]["changes_vs_prev_year"]
    assert len(changes) == 1
    assert changes[0]["name"] == "홍길동" and "이탈" in changes[0]["change"]
    assert result["status"] == "exact"


@pytest.mark.parametrize("auditor", ["감사", "상근감사", "비상근감사", "감사위원"])
@pytest.mark.parametrize("column_role", ["", "사외이사"])
def test_mixed_title_assigns_audit_role_to_the_named_person(boundary, auditor, column_role):
    title = f"사내이사 홍길동 선임 및 {auditor} 김철수 선임의 건"
    client = boundary(document=_document(title, (("홍길동", "사내이사"), ("김철수", column_role))))
    doc = asyncio.run(client.get_document_cached(RECEIPT))
    original = deepcopy(doc)
    people = {c["name"]: c for a in parse_personnel_xml(doc["html"])["appointments"] for c in a["candidates"]}
    assert set(people) == {"홍길동", "김철수"}
    assert people["홍길동"]["roleType"] == "사내이사"
    auditor_candidate = people["김철수"]
    assert auditor_candidate["declaredRole"] == auditor
    if column_role:
        assert auditor_candidate["roleType"] == column_role
        assert auditor_candidate["roleTypeConflict"]["declared_role"] == auditor
    else:
        assert auditor_candidate["roleType"] == auditor
    assert doc == original


def test_audit_committee_outside_seat_keeps_outside_title(boundary):
    client = boundary(document=_document("감사위원회 위원이 되는 독립이사 홍길동 선임의 건",
                                        (("홍길동", "독립이사"),)))
    doc = asyncio.run(client.get_document_cached(RECEIPT))
    people = [c for a in parse_personnel_xml(doc["html"])["appointments"] for c in a["candidates"]]
    assert len(people) == 1
    assert people[0]["declaredRole"] == "독립이사"
    assert "roleTypeConflict" not in people[0]


@pytest.mark.parametrize("auditor", ["비상근감사", "상근감사"])
@pytest.mark.parametrize("role_column", [False, True])
def test_named_auditor_subtype_refines_title_but_never_column(boundary, auditor, role_column):
    client = boundary(document=_document(f"{auditor} 홍길동 선임의 건",
                                        (("홍길동", "감사"),), role_column=role_column))
    doc = asyncio.run(client.get_document_cached(RECEIPT))
    people = [p for a in parse_personnel_xml(doc["html"])["appointments"] for p in a["candidates"]]
    assert len(people) == 1
    person = people[0]
    assert person["name"] == "홍길동"
    assert person["declaredRole"] == auditor and person["declaredRoleBasis"] == "named"
    assert person["roleType"] == ("감사" if role_column else auditor)
    assert person["roleTypeBasis"] == ("column" if role_column else "title_named")
    if not role_column:
        assert person["roleTypeBefore"] == "감사"
    assert "roleTypeConflict" not in person


@pytest.mark.parametrize("role,expected", [("비상근감사", "비상근감사"), ("비 상근 감사", "비상근감사"), ("상근감사", "상근감사"), ("감사위원", "감사위원"), ("비상근 감사위원", "감사위원")])
def test_auditor_column_preserves_full_role(boundary, role, expected):
    doc = _document("감사 홍길동 선임의 건", (("홍길동", role),))
    client = boundary(document=doc)
    people = [c for a in parse_personnel_xml(asyncio.run(client.get_document_cached(RECEIPT))["html"])["appointments"] for c in a["candidates"]]
    assert people[0]["roleType"] == expected


@pytest.mark.parametrize("raw,months", [("3년", 36), ("1년 6개월", 18), ("18개월", 18)])
def test_candidate_term_stays_with_its_table_row(boundary, raw, months):
    doc = _document(term=raw)
    client = boundary(document=doc)
    person = parse_personnel_xml(asyncio.run(client.get_document_cached(RECEIPT))["html"])["appointments"][0]["candidates"][0]
    assert person["termRaw"] == raw
    assert person["termDetails"]["duration_months"] == months
    assert person["termDetails"]["end"] is None
    assert person["termDetails"]["source"] == "candidate_table"


def test_no_term_is_invented_from_career_or_name(boundary):
    doc = _document()
    boundary(document=doc)
    person = parse_personnel_xml(doc["html"])["appointments"][0]["candidates"][0]
    assert "termRaw" not in person
    assert "termDetails" not in person


def test_term_is_forwarded_by_candidate_evaluation(boundary):
    boundary(document=_document(term="1년 6개월"), rows=[_row()])
    payload = asyncio.run(evaluation.build_director_evaluation_payload(
        CORP, year=2026, meeting_type="auto", check_audit_history=False))
    person = payload["data"]["evaluations"][0]
    assert person["term"] == "1년 6개월"
    assert person["term_details"]["duration_months"] == 18
    # 임원현황의 만료일은 후보표의 예정 임기에 섞지 않는다.
    assert person["term_details"]["end"] is None


@pytest.mark.parametrize("header,value,months", [("임기(년)", "3", 36), ("임기(개월)", "18", 18), ("임기", "3", None)])
def test_term_header_supplies_unit_only_when_explicit(boundary, header, value, months):
    doc = _document(term=value)
    doc["html"] = doc["html"].replace("<TH>임기</TH>", f"<TH>{header}</TH>")
    boundary(document=doc)
    person = parse_personnel_xml(doc["html"])["appointments"][0]["candidates"][0]
    assert person["termDetails"]["duration_months"] == months
    assert person["termDetails"]["requires_review"] is (months is None)


def test_same_name_different_agenda_does_not_share_term(boundary):
    first = _document(term="3년")["html"]
    second = _document(title="감사 홍길동 선임의 건", term="1년")["html"].replace("제2호", "제3호")
    boundary()
    # 문서의 목적사항 구간은 하나이고 그 안에 선임 절 둘이 있다.
    html = first.replace("</SECTION-2>", "") + "<LIBRARY>" + second.split("<LIBRARY>", 1)[1]
    people = [c for a in parse_personnel_xml(html)["appointments"] for c in a["candidates"]]
    assert [c["termDetails"]["duration_months"] for c in people] == [36, 12]


@pytest.mark.parametrize("role,expected", [("비상근감사(감사위원 아님)", "비상근감사"), ("비상근감사(사외이사 아님)", "비상근감사"), ("감사위원(비상근감사 아님)", "감사위원"), ("비상근감사(아님)", "비상근감사(아님)")])
def test_auditor_explanatory_negation_is_not_the_role(boundary, role, expected):
    doc = _document("감사 홍길동 선임의 건", (("홍길동", role),))
    boundary(document=doc)
    person = parse_personnel_xml(doc["html"])["appointments"][0]["candidates"][0]
    assert person["roleType"] == expected


@pytest.mark.parametrize("header,unit,months,review", [("임기(년)", "년", 36, False), ("임기(개월)", "개월", 3, False), ("임기(년)(주1)", "년", 36, True), ("임기(개월)[주1]", "개월", 3, True), ("임기※", None, None, True)])
def test_term_header_notes_and_units_survive_render(boundary, header, unit, months, review):
    doc = _document(term="3")
    doc["html"] = doc["html"].replace("<TH>임기</TH>", f"<TH>{header}</TH>")
    boundary(document=doc)
    board = parse_personnel_xml(doc["html"])
    person = board["appointments"][0]["candidates"][0]
    assert person["termRaw"] == "3"
    assert person["termDetails"]["duration_months"] == months
    assert person["termDetails"]["source_header"] == header
    assert person["termDetails"]["requires_review"] is review
    rendered = render_board({"data": {"board": board, "board_summary": board["summary"]}})
    assert f"공시상 임기(선임 예정): 3{unit or ''}" in rendered
    assert ("원문 확인 필요" in rendered) is review


@pytest.fixture
def mcp_client(boundary):
    # Fresh production app/session manager per case, as in the protocol contract.
    # Never replace tool wrappers, builders, parsers, or renderers.
    from open_proxy_mcp import usage
    from open_proxy_mcp.capture import capture_dir

    assert not usage._RECORDING, "Personnel tests must not write usage telemetry"
    assert capture_dir() is None, "Personnel tests must not capture requests to disk"
    with TestClient(build_app()) as client:
        yield client


def _tool_text(client, tool, arguments, output_format):
    response = client.post("/mcp?opendart=synthetic-test-key", headers={
        "Host": "localhost:8000", "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": tool, "arguments": {**arguments, "format": output_format},
    }})
    assert response.status_code == 200
    wire = response.json()
    assert "error" not in wire, wire
    result = wire["result"]
    assert result["isError"] is False, result
    content = result["content"]
    assert len(content) == 1 and content[0]["type"] == "text", content
    assert content[0]["text"].strip()
    return content[0]["text"]


def test_mcp_notice_board_preserves_source_when_term_is_missing(boundary, mcp_client):
    doc = _document("비상근감사 홍길동 선임의 건", (("홍길동", "비상근감사"),))
    doc["html"] = ("<P>주주총회 소집공고 (제1기 정기)</P>"
                   "<P>1. 일 시: 2026년 3월 27일 오전 10시</P>"
                   "<P>2. 장 소: 시험회의실</P><P>3. 회의목적사항</P>"
                   "<P>제2호 의안: 비상근감사 홍길동 선임의 건</P>" + doc["html"])
    doc["text"] = BeautifulSoup(doc["html"], "html.parser").get_text("\n")
    boundary(document=doc)
    arguments = {"company": CORP, "rcept_no": RECEIPT, "scope": "board", "year": 2026}
    rendered = _tool_text(mcp_client, "shareholder_meeting_notice", arguments, "md")
    assert f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={RECEIPT}" in rendered
    assert "주주총회 목적사항별 기재사항" in rendered
    assert "임기 정보 미확인" in rendered
    assert "후보자: **홍길동**" in rendered
    assert "직위: 비상근감사" in rendered


@pytest.mark.parametrize("receipt", [None, "", "invalid", "２０２６０３０１０００００１"])
def test_board_renderer_does_not_invent_filing_link(receipt):
    rendered = render_board({"data": {"notice": {"rcept_no": receipt}, "board": {"appointments": []}}})
    assert "https://dart.fss.or.kr/" not in rendered


@pytest.mark.parametrize("state", ["unknown", "no_prior", "not_requested", "compared"])
def test_mcp_roster_comparison_state_and_warnings_reach_render(boundary, mcp_client, state):
    current = _response([_row(role="독립이사")])
    executives = {("2025", "11011"): current}
    outside = {("2025", "11011"): "013"}
    lookback = 1 if state == "not_requested" else 2
    if lookback == 2:
        executives[("2024", "11011")] = "013" if state == "no_prior" else current
        outside[("2024", "11011")] = "013"
    if state == "unknown":
        executives.update({("2025", code): "013" for code in ("11011", "11014", "11012", "11013")})
    boundary(executives=executives, outside=outside)
    arguments = {"company": CORP, "scope": "roster", "year": 2025, "lookback_years": lookback}
    payload = json.loads(_tool_text(mcp_client, "director_board", arguments, "json"))
    assert payload["status"] == ("requires_review" if state in {"unknown", "no_prior"} else "exact")
    roster = payload["data"]["roster"]
    assert roster["comparison_status"] == state
    assert roster["changes_vs_prev_year"] == []
    assert roster["executive_changes_vs_prev_year"] == []
    assert roster["changes_since_last_annual"] == []
    assert roster["diff_cross_check"] is None
    assert [r["name"] for r in roster["roster"]] == ([] if state == "unknown" else ["홍길동"])
    rendered = _tool_text(mcp_client, "director_board", arguments, "md")
    assert "감지된 이탈:" not in rendered
    assert "이탈(사퇴·임기만료·해임 중 하나)" not in rendered
    if state in {"unknown", "no_prior"}:
        warning = next(w for w in payload["warnings"] if "비교 미산출" in w)
        assert warning in rendered
        assert "원문" in rendered
        if state == "unknown":
            assert "전원 이탈을 뜻하지 않습니다" in rendered
            assert roster["roster_as_of"] is None
    else:
        assert not payload["warnings"]
    if state != "unknown":
        assert "| 홍길동 |" in rendered and "독립이사" in rendered
        assert "2020.03.01~" in rendered and "2026년 03월 31일" in rendered


@pytest.mark.parametrize("auditor", ["감사", "상근감사", "비상근감사", "감사위원"])
@pytest.mark.parametrize("role_column", [False, True])
def test_mcp_notice_mixed_title_preserves_candidate_role_pairs(boundary, mcp_client, auditor, role_column):
    title = f"독립이사 홍길동 선임 및 {auditor} 김철수 선임의 건"
    doc = _document(title, (("홍길동", "독립이사"), ("김철수", "")), role_column=role_column)
    doc["html"] = (
        "<P>주주총회 소집공고 (제1기 정기)</P>"
        "<P>1. 일 시: 2026년 3월 27일 오전 10시</P>"
        "<P>2. 장 소: 시험회의실</P><P>3. 회의목적사항</P>"
        f"<P>제2호 의안: {title}</P>" + doc["html"]
    )
    doc["text"] = BeautifulSoup(doc["html"], "html.parser").get_text("\n")
    client = boundary(document=doc)
    original = deepcopy(client.documents)
    arguments = {"company": CORP, "rcept_no": RECEIPT, "scope": "board", "year": 2026}
    payload = json.loads(_tool_text(mcp_client, "shareholder_meeting_notice", arguments, "json"))
    assert payload["status"] == "exact"
    assert not payload["warnings"]
    people = [p for a in payload["data"]["board"]["appointments"] for p in a["candidates"]]
    audit_role = auditor
    assert [(p["name"], p["roleType"], p["declaredRole"]) for p in people] == [
        ("홍길동", "독립이사", "독립이사"), ("김철수", audit_role, auditor),
    ]
    rendered = _tool_text(mcp_client, "shareholder_meeting_notice", arguments, "md")
    assert "- 후보자: **홍길동**\n  - 직위: 독립이사" in rendered
    assert f"- 후보자: **김철수**\n  - 직위: {audit_role}" in rendered
    assert "총 후보자 수: 2명" in rendered
    assert client.documents == original


@pytest.mark.parametrize("raw,months,review", [("1년 6개월", 18, False), ("3년(주1)", 36, True), ("2029년 정기주총 종결 시까지", None, True)])
def test_mcp_notice_preserves_term_and_review(boundary, mcp_client, raw, months, review):
    doc = _document("비상근감사 홍길동 선임의 건", (("홍길동", "비상근감사"),), term=raw)
    doc["html"] = ("<P>주주총회 소집공고 (제1기 정기)</P>"
        "<P>1. 일 시: 2026년 3월 27일 오전 10시</P>"
        "<P>2. 장 소: 시험회의실</P><P>3. 회의목적사항</P>"
        "<P>제2호 의안: 비상근감사 홍길동 선임의 건</P>" + doc["html"])
    doc["text"] = BeautifulSoup(doc["html"], "html.parser").get_text("\n")
    client = boundary(document=doc)
    original = deepcopy(client.documents)
    arguments = {"company": CORP, "rcept_no": RECEIPT, "scope": "board", "year": 2026}
    payload = json.loads(_tool_text(mcp_client, "shareholder_meeting_notice", arguments, "json"))
    people = [p for a in payload["data"]["board"]["appointments"] for p in a["candidates"]]
    assert len(people) == 1
    assert people[0]["roleType"] == "비상근감사"
    assert people[0]["termRaw"] == raw
    assert people[0]["termDetails"]["duration_months"] == months
    assert people[0]["termDetails"]["requires_review"] is review
    rendered = _tool_text(mcp_client, "shareholder_meeting_notice", arguments, "md")
    assert f"공시상 임기(선임 예정): {raw}" in rendered
    assert ("원문 확인 필요" in rendered) is review
    assert "직위: 비상근감사" in rendered
    assert client.documents == original
