"""주총 소집결의(`shareholder_meeting_notice(scope="resolution")`) — 결의 원문 표를 칸 그대로, 정정 이력까지.

실제 결의 원문 넷(`tests/dart_responses/dart/agm_resolution_*.html`)과 사슬 시험용 최소 표. 네트워크 0.
"""
import asyncio
from pathlib import Path

import pytest

from open_proxy_mcp.services import doc_tables as DT
from open_proxy_mcp.services import meeting_resolution as M
from open_proxy_mcp.services import screener as S
from open_proxy_mcp.services.amendment_table import parse_amendment_table
from open_proxy_mcp.tools._meeting_resolution_render import render_resolution

_FIX = Path(__file__).parent / "dart_responses" / "dart"


def _doc(name: str) -> str:
    return (_FIX / name).read_text("utf-8")


UNDETERMINED = "agm_resolution_undetermined_20260814901878.html"        # 휴마시스 임시 — 안건 미정
PROPOSAL = "agm_resolution_proposal_cumulative_20260721800927.html"     # 고려아연 임시 — 집중투표·주주제안
ORIGINAL = "agm_resolution_original_20260716800003.html"                # 대구백화점 임시 원본
CORRECTION = "agm_resolution_correction_20260811801007.html"            # 대구백화점 정정 — 대조표 + 본표


# ── 표 격자 ──────────────────────────────────────────────────────────

def test_table_grids_expand_rowspan_and_colspan():
    grid, = DT.table_grids(
        "<table><tr><td rowspan='2'>1. 일시</td><td>날짜</td><td colspan='2'>2026-09-30</td></tr>"
        "<tr><td>시간</td><td colspan='2'>오전 09시</td></tr></table>")
    assert grid == [["1. 일시", "날짜", "2026-09-30", "2026-09-30"],
                    ["1. 일시", "시간", "오전 09시", "오전 09시"]]
    assert DT.distinct(grid[1]) == ["1. 일시", "시간", "오전 09시"]


# ── 실제 원문 ────────────────────────────────────────────────────────

def test_undetermined_resolution_keeps_meeting_facts_and_says_agenda_is_pending():
    p = M.parse_resolution_document(_doc(UNDETERMINED))
    m = p["meeting"]
    assert m["datetime"] == "2026-09-30 오전 09시" and m["meeting_date"] == "2026-09-30"
    assert m["record_date_iso"] == "2026-08-31" and m["board_resolution_date_iso"] == "2026-08-14"
    assert m["meeting_type_text"] == "임시주주총회"
    assert m["outside_directors"] == {"present": "1", "absent": "-"}
    assert m["audit_attendance"] == "참석"          # 「사외이사가 아닌 감사위원」 줄이 사외이사 칸으로 새지 않는다
    assert "재공시" in m["notes"][0]
    assert p["agenda"] == {"status": "undetermined", "items": []}


def test_cumulative_voting_and_shareholder_proposals_are_marked_on_the_original_rows():
    p = M.parse_resolution_document(_doc(PROPOSAL))
    assert p["meeting"]["datetime"] == "2026-09-09 10 : 00"
    items = p["agenda"]["items"]
    assert [i["no"] for i in items] == ["1", "2", "3"]
    assert "shareholder_proposal" not in items[0] and "cumulative_voting" not in items[0]
    assert items[1]["cumulative_voting"] and items[1]["shareholder_proposal"]
    assert items[1]["sub_items"][0] == "2-1. 독립이사 이형규 선임의 건"
    # 3-2 호만 주주제안 — 비고 칸에 회사가 적은 낱말을 본다
    assert items[2]["shareholder_proposal"] and len(items[2]["sub_items"]) == 2
    assert [len(c["rows"]) for c in p["candidates"]] == [6, 2]
    assert p["candidates"][0]["columns"][:4] == ["성명", "출생년월", "임기", "신규선임여부"]


def test_correction_document_reads_the_main_table_not_the_comparison_table():
    html = _doc(CORRECTION)
    p = M.parse_resolution_document(html)
    assert p["has_main_table"]
    assert p["meeting"]["datetime"] == "2026-09-09 09 : 00"
    assert [i["title"] for i in p["agenda"]["items"]][0] == "제1호의안 : 정관 일부 변경의건"
    assert len(p["agenda"]["items"]) == 4 and len(p["candidates"]) == 3
    assert [t["columns"] for t in p["other_tables"]] == [["구분", "내용", "이유"]]   # 대조표 머리 줄이 새지 않는다
    a = parse_amendment_table(html)
    assert a["original_filed_date"] == "2026-07-16" and a["row_count"] == 3


def test_identity_number_shapes_are_masked_and_proposal_reports_are_not_proposals():
    p = M.parse_agenda([["번호", "회의목적사항", "비고"],
                        ["1", "임시의장 윤창돈(741220-000000)선임의 건", "-"],
                        ["2", "정관 변경의 건", "주주제안 보고"]])
    assert p["items"][0]["title"] == "임시의장 윤창돈(******-*******)선임의 건"
    assert "shareholder_proposal" not in p["items"][1]


# ── 묶기 ─────────────────────────────────────────────────────────────

def _f(rn: str, nm: str) -> dict:
    return {"rcept_no": rn, "rcept_dt": rn[:8], "report_nm": nm}


def test_corrections_join_the_latest_meeting_of_the_same_kind_only():
    groups = M.group_filings([
        _f("20260301000001", "주주총회소집결의 (정기주주총회)"),
        _f("20260309000001", "주주총회소집결의 (임시주주총회)"),
        _f("20260317000001", "[기재정정]주주총회소집결의 (정기주주총회)"),   # 모다이노칩 — 임시에 붙지 않는다
        _f("20260320000001", "[기재정정]주주총회소집결의 (임시주주총회)"),
    ])
    assert [(g["meeting_type"], [f["rcept_no"][:8] for f in g["filings"]]) for g in groups] == [
        ("annual", ["20260301", "20260317"]), ("extraordinary", ["20260309", "20260320"])]


def test_a_correction_without_its_original_opens_an_orphan_group():
    groups = M.group_filings([_f("20260320000001", "[기재정정]주주총회소집결의 (임시주주총회)")])
    assert groups[0]["orphan"]


# ── 조회 (가짜 DART) ─────────────────────────────────────────────────

def _main(dt: str = "2026-09-30", rows: str = "<tr><td>1</td><td>정관 변경의 건</td><td>-</td></tr>") -> str:
    return ("<table><tr><td colspan='2'>1. 구분</td><td colspan='2'>임시주주총회</td></tr>"
            f"<tr><td colspan='2'>2. 일시</td><td>{dt}</td><td>10 : 00</td></tr>"
            "<tr><td colspan='2'>3. 장소</td><td colspan='2'>본사 회의실</td></tr>"
            "<tr><td colspan='2'>4. 의결권행사기준일</td><td colspan='2'>2026-08-31</td></tr>"
            "<tr><td colspan='2'>5. 이사회결의일(결정일)</td><td colspan='2'>2026-08-14</td></tr></table>"
            "<table><tr><td>번호</td><td>회의목적사항</td><td>비고</td></tr>" + rows + "</table>")


def _amend(original: str, rows: str) -> str:
    return ("<table><tr><td>1. 정정관련 공시서류</td><td colspan='2'>주주총회소집 결의</td></tr>"
            f"<tr><td>2. 정정관련 공시서류제출일</td><td colspan='2'>{original}</td></tr>"
            "<tr><td>3. 정정사유</td><td colspan='2'>일정 변경</td></tr>"
            "<tr><td colspan='3'>4. 정정사항</td></tr>"
            "<tr><td>정정항목</td><td>정정전</td><td>정정후</td></tr>" + rows + "</table>")


class _Client:
    def __init__(self, docs: dict[str, str]):
        self.docs, self.calls, self.opened = docs, 0, []

    def api_call_snapshot(self):
        return self.calls

    async def get_document_cached(self, rcept_no):
        self.calls += 1
        self.opened.append(rcept_no)
        return {"html": self.docs[rcept_no], "text": ""}


@pytest.fixture
def fake(monkeypatch):
    state: dict = {"filings": [], "docs": {}, "notices": []}

    async def search(**kw):
        state["client"].calls += 1
        if kw.get("pblntf_detail_ty") == "I001":
            return list(state["filings"]), [], None
        return list(state["notices"]), [], None

    class _Res:
        status = "exact"
        selected = {"corp_code": "00000001", "corp_name": "시험회사", "stock_code": "000001"}
        candidates: list = []

    async def resolve(q):
        return _Res()

    def run(**kw):
        state["client"] = _Client(state["docs"])
        monkeypatch.setattr(M, "get_dart_client", lambda: state["client"])
        monkeypatch.setattr(M, "search_filings_by_report_name", search)
        monkeypatch.setattr(M, "resolve_company_query", resolve)
        return asyncio.run(M.build_meeting_resolution_payload("시험회사", start_date="20260101",
                                                              end_date="20261018", **kw))
    state["run"] = run
    return state


def test_real_original_and_correction_become_one_meeting_with_history(fake):
    fake["filings"] = [_f("20260716800003", "주주총회소집결의 (임시주주총회)"),
                       _f("20260811801007", "[기재정정]주주총회소집결의 (임시주주총회)")]
    fake["docs"] = {"20260716800003": _doc(ORIGINAL), "20260811801007": _doc(CORRECTION)}
    p = fake["run"]()
    d = p["data"]
    assert p["status"] == "exact" and d["meeting_type"] == "extraordinary" and d["scope"] == "resolution"
    assert [(v["label"], v.get("pointer_check")) for v in d["versions"]] == [("원본", None), ("정정 1차", "ok")]
    assert d["versions"][1]["amendment"]["reason"] == "임시주주총회 소집일자 변경 및 세부안건 확정"
    assert d["meeting"]["source_label"] == "정정 1차" and d["meeting"]["meeting_date"] == "2026-09-09"
    assert d["agenda"]["source_label"] == "정정 1차" and len(d["agenda"]["items"]) == 4
    assert d["notice"] == {"found": False}
    assert d["usage"]["dart_api_calls"] == 4             # 목록 1 + 원문 2 + 공고 목록 1
    md = render_resolution(p)
    assert "# 시험회사 임시주주총회 소집결의" in md and "## 결의 공시 이력 (2판)" in md
    assert "2. 일시: 2026-08-26 09:00 → 2026-09-09 09:00" in md


def test_a_later_comparison_only_version_that_clears_the_date_is_not_hidden(fake):
    """휴온스 2026-08-04 — 대조표만 있는 정정이 일시를 `-` 로 지웠다. 앞 판 본표의 일시가 그대로 보이면 안 된다."""
    fake["filings"] = [_f("20260518000001", "주주총회소집결의 (임시주주총회)"),
                       _f("20260804000001", "[기재정정]주주총회소집결의 (임시주주총회)")]
    fake["docs"] = {"20260518000001": _main("2026-08-21"),
                    "20260804000001": _amend("2026-05-18",
                                             "<tr><td>2. 일시</td><td>2026-08-21, 10:00</td><td>-</td></tr>")}
    p = fake["run"]()
    m = p["data"]["meeting"]
    assert m["source_label"] == "원본"
    later, = m["later_amendments"]
    assert (later["field"], later["after"], later["change"]) == ("datetime", "-", "deleted")
    assert any("고친 값이 우선" in w for w in p["warnings"])
    assert "⚠ 정정 1차" in render_resolution(p)


def test_a_correction_pointing_outside_the_group_is_flagged(fake):
    fake["filings"] = [_f("20260601000001", "주주총회소집결의 (임시주주총회)"),
                       _f("20260701000001", "[기재정정]주주총회소집결의 (임시주주총회)")]
    fake["docs"] = {"20260601000001": _main(),
                    "20260701000001": _amend("2026-05-02", "<tr><td>2. 일시</td><td>a</td><td>b</td></tr>")}
    p = fake["run"]()
    assert p["data"]["versions"][1]["pointer_check"] == "mismatch"
    assert any("2026-05-02" in w for w in p["warnings"])


def test_long_chains_open_only_the_newest_versions_and_list_the_rest(fake):
    n = M.MAX_VERSIONS + 2
    fake["filings"] = [_f("20260401000001", "주주총회소집결의 (임시주주총회)")] + [
        _f(f"202605{i:02d}000001", "[기재정정]주주총회소집결의 (임시주주총회)") for i in range(1, n)]
    fake["docs"] = {f["rcept_no"]: _amend("2026-04-01", "<tr><td>2. 일시</td><td>a</td><td>b</td></tr>") + _main()
                    for f in fake["filings"]}
    p = fake["run"]()
    d = p["data"]
    assert d["versions_total"] == n and len(d["versions"]) == M.MAX_VERSIONS
    assert len(d["versions_listed_only"]) == 2
    assert d["versions"][0]["label"] == "정정 2차"
    assert all(v.get("pointer_check") == "ok" for v in d["versions"])   # 원본은 목록에만 있어도 원공시로 맞는다
    assert any("최근 12판만" in w for w in p["warnings"])


def test_no_resolution_in_the_window_is_a_normal_no_filing(fake):
    p = fake["run"]()
    assert p["status"] == "no_filing"
    assert any("scope=\"summary\"" in a for a in p["next_actions"])


def test_a_published_notice_is_pointed_to(fake):
    fake["filings"] = [_f("20260716800003", "주주총회소집결의 (임시주주총회)")]
    fake["docs"] = {"20260716800003": _doc(ORIGINAL)}
    fake["notices"] = [_f("20260825000111", "주주총회소집공고")]
    p = fake["run"]()
    assert p["data"]["notice"]["rcept_no"] == "20260825000111"
    assert "소집공고가 나왔다(20260825000111)" in p["next_actions"][0]


def test_receipt_number_path_reads_one_version(monkeypatch):
    client = _Client({"20260721800927": _doc(PROPOSAL)})
    monkeypatch.setattr(M, "get_dart_client", lambda: client)
    p = asyncio.run(M.build_meeting_resolution_payload("고려아연", rcept_no="20260721800927"))
    assert p["status"] == "exact" and p["data"]["meeting_type"] == "extraordinary"
    assert len(p["data"]["agenda"]["items"]) == 3
    assert any("정정 이력은 회사 이름으로" in w for w in p["warnings"])


# ── 스크리너 ─────────────────────────────────────────────────────────

def test_screener_routes_resolution_cards_to_the_resolution_reader(monkeypatch):
    seen = {}

    async def build(company, **kw):
        seen.update(kw)
        return {"status": "exact", "data": {"scope": "resolution", "meeting_type": "extraordinary",
                                            "meeting": {"meeting_date": "2026-09-09"},
                                            "agenda": {"status": "listed", "items": [{"title": f"안건{i}"} for i in range(14)]}}}

    import open_proxy_mcp.services.shareholder_meeting as SM
    monkeypatch.setattr(SM, "build_shareholder_meeting_payload", build)

    class C:
        def api_call_snapshot(self):
            return 0
    monkeypatch.setattr(S, "get_dart_client", lambda: C())
    hit = {"_detail_kind": "agm_notice", "subtype": "소집결의", "rcept_no": "20260721800927",
           "stock_code": "010130", "filed_at": "2026-07-21"}
    res = asyncio.run(S._fetch_detail(hit, {"calls": 0}))
    assert seen["scope"] == "resolution" and seen["rcept_no"] == "20260721800927"
    assert res["fields"]["meeting_date"] == "2026-09-09"
    assert res["fields"]["agenda_count"] == 14 and len(res["fields"]["agenda_titles"]) == 12

    from open_proxy_mcp.tools import screener as T
    line = T._detail_line({"type": {"code": "agm_notice"}, "detail_fields": res["fields"]})
    assert line.endswith("외 11건")                  # 잘린 12건이 아니라 원래 14건으로 센다


def test_screener_says_agenda_is_pending():
    from open_proxy_mcp.tools import screener as T
    line = T._detail_line({"type": {"code": "agm_notice"},
                           "detail_fields": {"agenda_status": "undetermined", "meeting_type": "extraordinary"}})
    assert line.startswith("안건 미정")


def test_screener_card_flattens_multiline_agenda_titles():
    """안건 칸의 원문 줄바꿈(동운아나텍 `사내이사 선임의 건\\n후보자 : …`)이 카드 한 줄을 깨지 않는다 — json 원본은 그대로."""
    payload = {"data": {"scope": "resolution", "meeting_type": "extraordinary", "meeting": {},
                        "agenda": {"status": "listed", "items": [{"title": "사내이사 선임의 건\n후보자 : 김규호(신규)"}]}}}
    f = S._extract_agm_notice(payload, "x")
    assert f["agenda_titles"] == ["사내이사 선임의 건 / 후보자 : 김규호(신규)"]
    assert payload["data"]["agenda"]["items"][0]["title"].count("\n") == 1
