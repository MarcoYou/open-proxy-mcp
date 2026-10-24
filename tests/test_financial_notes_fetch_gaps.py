"""financial_notes — 받지 못한 절·보고서 검색이 「해당 주석 없음」·exact 로 나가던 것 (P1-#40·#42).

종전: 목차 노드로 절을 받다가 한 절의 조회가 실패하면(`except Exception: continue`) 그 절을 건너뛰고 나머지로 답을 냈다 —
받지 못한 절 몫의 필드는 「해당 주석 없음」(NOT_APPLICABLE)·status exact·경고 0 이었다. 재무상태표(분모) 절도 같아서 실패가
md 에 「분모 없음 — 계정을 못 찾았다」로 나갔다. `year` 를 준 경로는 보고서 종류별 검색 가운데 하나가 020·800 으로 실패해도
다른 종류가 하나라도 찾아지면 그 실패를 버려, 사업보고서 검색이 실패하고 분기만 찾아진 응답이 분기보고서를 exact 로 냈다.

규칙 하나: **예외로 받지 못한 것은 값을 지어내지 않는다** — 그 절 몫의 필드는 NOT_COLLECTED(채워진 필드면 기존 note 칸에 사실을 적음),
봉투는 partial, 경고에 무엇이 실패했고 다음에 무엇을 할지. 013(검색 결과 없음)은 실패가 아니라 답이다.

network 0 · 합성 입력(실제 DART 응답 아님) — 실제 `DartClient._fetch_viewer_section_html`(HTTP 오류 경로)에 가짜 전송을 물리고,
`DartClientError` 는 실제 객체를 던진다. 실제 DART 응답으로 한 정상 경로 대조는 이 저장소 밖(실행 기록)에서 했다.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from open_proxy_mcp.dart import client as dart_client
from open_proxy_mcp.dart.client import DartClient, DartClientError
from open_proxy_mcp.server import mcp
from open_proxy_mcp.services import business_details as BD
from open_proxy_mcp.services import financial_notes as svc
from open_proxy_mcp.services.company import CompanyResolution
from open_proxy_mcp.services.contracts import AnalysisStatus
from open_proxy_mcp.tools import financial_notes as T

CO = {"corp_code": "00126380", "corp_name": "테스트금융", "stock_code": "000000"}
REP = {"rcept_no": "20260310000001", "rcept_dt": "20260310", "report_nm": "사업보고서 (2025.12)", "corp_code": CO["corp_code"]}
REP2 = {"rcept_no": "20251114000002", "rcept_dt": "20251114", "report_nm": "분기보고서 (2025.09)", "corp_code": CO["corp_code"]}

#: (eleId → 절 제목, 부모) — 주석 항목 노드 셋(FVPL·사용제한·담보제공)과 재무상태표 절
_NODES = {
    "10": ("7. 당기손익-공정가치측정금융자산", "3. 연결재무제표 주석"),
    "11": ("8. 사용이 제한된 예금 (연결)", "3. 연결재무제표 주석"),
    "12": ("9. 담보제공자산 (연결)", "3. 연결재무제표 주석"),
    "2": ("2. 연결재무제표", "III. 재무에 관한 사항"),
}
_FVPL = ("<p>당기손익-공정가치측정금융자산</p><table><tr><td>구분</td><td>당기</td><td>전기</td></tr>"
         "<tr><td>지분상품</td><td>1,234</td><td>1,000</td></tr><tr><td>합계</td><td>1,234</td><td>1,000</td></tr></table>")
_RESTR = ("<p>사용이 제한된 예금</p><table><tr><td>구분</td><td>당기</td><td>전기</td></tr>"
          "<tr><td>단기금융상품</td><td>55</td><td>44</td></tr></table>")
_PLEDGED = ("<p>담보제공자산</p><table><tr><td>구분</td><td>당기</td><td>전기</td></tr>"
            "<tr><td>국공채</td><td>77</td><td>66</td></tr></table>")
_SHEET = ("<table>" + "".join(f"<tr><td>계정{i}</td><td>{i * 10}</td><td>{i * 9}</td></tr>" for i in range(12))
          + "<tr><td>예금</td><td>500</td><td>480</td></tr><tr><td>자산총계</td><td>1,000</td><td>900</td></tr></table>")
_BODY = {"10": _FVPL, "11": _RESTR, "12": _PLEDGED, "2": _SHEET}
_SECRET = "SECRETKEY0123456789"


def _run(coro):
    return asyncio.run(coro)


def _node(rcept_no: str, ele: str) -> dict:
    title, parent = _NODES[ele]
    return {"tocNo": ele, "text": title, "parent_text": parent, "lvl": 2, "rcpNo": rcept_no, "dcmNo": "1",
            "eleId": ele, "offset": "0", "length": "1", "dtd": "dart4.xsd"}


class World:
    """가짜 DART 세계 — `fail[(접수번호, eleId)]` 에 예외 객체(또는 HTTP 상태 정수)를 두면 그 절만 그렇게 실패한다."""

    def __init__(self, monkeypatch, year_mode: bool = False):
        monkeypatch.setenv("OPENDART_API_KEY", "test-key-not-real")
        self.fail: dict[tuple[str, str], object] = {}
        self.search: dict[str, object] = {}          # A001/A002/A003 → 예외 | 보고서 목록 (없으면 013)
        self.hits: list[tuple[str, str]] = []
        self.nodes = ["10", "11", "2"]               # 목차에 있는 절(eleId) — 담보제공자산(12)은 필요한 시험에서만 더한다
        self.cands: list[dict] = [REP]               # year_mode 가 아닐 때 `_candidates` 가 돌려줄 후보
        self.tool_args = {"company": CO["corp_name"], "fields": "FVPL,사용제한", "format": "json"}
        if year_mode:                                # 실제 `_candidates`·`_find_report_for_bsns_year` + 가짜 검색(`search`)
            self.tool_args.update(year="2025", fields="FVPL")

        def handler(request: httpx.Request) -> httpx.Response:
            rno, ele = request.url.params.get("rcpNo", ""), request.url.params.get("eleId", "")
            self.hits.append((rno, ele))
            mode = self.fail.get((rno, ele))
            if isinstance(mode, int):
                return httpx.Response(mode, request=request, text="err")
            return httpx.Response(200, request=request, text=_BODY[ele])

        client = DartClient()
        client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        real_section = DartClient._fetch_viewer_section_html

        async def section(this, node):
            mode = self.fail.get((node["rcpNo"], node["eleId"]))
            if isinstance(mode, BaseException):
                self.hits.append((node["rcpNo"], node["eleId"]))
                raise mode
            return await real_section(this, node)

        async def no_wait(this):
            return None

        async def main_html(this, rcept_no):
            return rcept_no                           # 목차 HTML 자리에 접수번호를 실어 아래 노드 생성기가 읽는다

        async def no_document(this, rcept_no):
            raise DartClientError("014", "원본 없음")  # 전체 문서 폴백은 막는다

        async def search_filings(this, **kw):
            mode = self.search.get(kw.get("pblntf_detail_ty"), "013")
            if isinstance(mode, BaseException):
                raise mode
            if mode == "013":
                raise DartClientError("013", "조회된 데이타가 없습니다.")
            return {"status": "000", "list": mode}

        async def resolve(q, *a, **k):
            return CompanyResolution(status=AnalysisStatus.EXACT, query=q, selected=dict(CO), candidates=[])

        async def fixed_candidates(c, corp_code, period, year, gaps=None):
            return list(self.cands)

        monkeypatch.setattr(DartClient, "_fetch_viewer_section_html", section)
        monkeypatch.setattr(DartClient, "_throttle_web", no_wait)
        monkeypatch.setattr(DartClient, "_fetch_viewer_main_html", main_html)
        monkeypatch.setattr(DartClient, "get_document_cached", no_document)
        monkeypatch.setattr(DartClient, "search_filings", search_filings)
        monkeypatch.setattr(BD, "_extract_node_tree", lambda rno: [_node(rno, e) for e in self.nodes])
        monkeypatch.setattr(T, "get_dart_client", lambda: client)
        monkeypatch.setattr(T, "resolve_company_query", resolve)
        if not year_mode:
            monkeypatch.setattr(T, "_candidates", fixed_candidates)

    def call(self, **extra) -> dict | str:
        args = {**self.tool_args, **extra}

        async def go():
            r = await mcp.call_tool("financial_notes", args)
            return "".join(getattr(c, "text", "") for c in (r if isinstance(r, list) else r.content))
        out = _run(go())
        return json.loads(out) if args.get("format") == "json" else out


@pytest.fixture
def world(monkeypatch):
    return World(monkeypatch)


def _http_error(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://dart.fss.or.kr/report/viewer.do")
    return httpx.HTTPStatusError(f"{code} {_SECRET}", request=req, response=httpx.Response(code, request=req))


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 대조군 — 실패가 없으면 응답이 그대로다(고치기 전에도 통과해야 한다)
# ═════════════════════════════════════════════════════════════════════════════════════════════════
def test_no_failure_keeps_exact_without_warnings_and_marks(world):
    body = world.call()
    assert body["status"] == "exact" and body["warnings"] == []
    assert {k: v["status"] for k, v in body["data"]["notes"].items()} == {"FVPL": svc.OK, "사용제한": svc.OK}
    assert [s["no"] for s in body["data"]["sections"]] == ["10", "11"]
    md = world.call(format="md")
    assert "⚠️ **" not in md.split("## FVPL")[0], "머리 아래에 실패 경고 줄이 없어야 한다"
    assert "미수집" not in md and "받지 못" not in md
    assert "재무상태표(연결) 잔액" in md                      # 분모는 정상 경로에서 그대로 붙는다


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# P1-#40 — 절 조회 실패
# ═════════════════════════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("injected,guide", [
    (503, "일시적 오류(5xx)"),
    (429, "요청이 몰려"),
    (DartClientError("800", "점검"), "시스템 점검"),
    (DartClientError("020", "한도"), "하루 요청 한도"),
    (DartClientError("014", "원본 없음"), "원본 파일이 DART 에 없습니다"),
    (httpx.ReadTimeout("t"), "응답이 지연"),
])
def test_a_section_that_could_not_be_read_is_not_collected_not_absent(world, injected, guide):
    world.fail[(REP["rcept_no"], "11")] = injected
    body = world.call()
    notes = body["data"]["notes"]
    assert notes["사용제한"]["status"] == svc.NOT_COLLECTED, notes["사용제한"]     # 종전: NOT_APPLICABLE(해당 주석 없음)
    assert notes["FVPL"]["status"] == svc.OK                                       # 받은 절 몫은 그대로
    assert body["status"] == "partial"                                             # 종전: exact
    assert len(body["warnings"]) == 1 and "사용이 제한된 예금" in body["warnings"][0] and guide in body["warnings"][0]
    assert REP["rcept_no"] in body["warnings"][0]                                  # 원문에서 직접 볼 자리
    assert "없다는 뜻이 아님" in notes["사용제한"]["note"]
    assert [s["no"] for s in body["data"]["sections"]] == ["10"]                   # 읽은 절 목록은 실제로 읽은 것만
    md = world.call(format="md")
    assert "> 원문 미수집 — " in md and "해당 주석 없음" not in md
    assert "⚠️" in md.split("## FVPL")[0]                                          # md 는 봉투가 없으니 경고를 머리 아래에 싣는다


def test_the_error_text_never_reaches_the_response(world):
    """예외 본문(URL·키 조각)은 싣지 않는다 — 안내문은 고정 문구."""
    world.fail[(REP["rcept_no"], "11")] = 503
    world.fail[(REP["rcept_no"], "10")] = _http_error(403)
    for out in (json.dumps(world.call(), ensure_ascii=False), world.call(format="md")):
        assert _SECRET not in out and "crtfc_key" not in out


def test_a_failed_section_of_another_field_marks_that_field(world):
    world.fail[(REP["rcept_no"], "10")] = 503
    body = world.call()
    notes = body["data"]["notes"]
    assert notes["FVPL"]["status"] == svc.NOT_COLLECTED and notes["사용제한"]["status"] == svc.OK
    assert body["status"] == "partial" and "당기손익-공정가치측정금융자산" in body["warnings"][0]


def test_a_field_gathered_from_two_sections_says_so_when_only_one_fails(world):
    """사용제한은 「사용이 제한된 예금」(11)과 「담보제공자산」(12) 두 절에서 모인다 — 한쪽만 실패해도 필드는 채워진 채로
    나가므로(표 하나), 빠진 사실을 필드 안내 칸에도 적는다(경고에만 있으면 필드를 읽는 쪽은 합이 전부인 줄 안다)."""
    world.tool_args["fields"] = "사용제한"
    world.nodes = ["10", "11", "12", "2"]
    ok = world.call()
    assert [s["no"] for s in ok["data"]["sections"]] == ["11", "12"] and ok["status"] == "exact"
    world.fail[(REP["rcept_no"], "12")] = 503
    body = world.call()
    res = body["data"]["notes"]["사용제한"]
    assert res["status"] == svc.OK and res["tables"], "받은 절의 표는 그대로 준다"
    assert "담보제공자산" in res.get("note", "") and "빠졌을 수" in res["note"], res
    assert body["status"] == "partial" and len(body["warnings"]) == 1
    assert "빠졌을 수" in world.call(format="md")                                   # md 도 필드 note 를 그대로 싣는다


def test_a_failed_balance_sheet_is_not_told_as_a_missing_denominator(world):
    """재무상태표(분모) 절을 못 받은 것을 「같은 이름의 계정을 못 찾았다 — 분모 없음」으로 말하면 거짓이다."""
    world.tool_args["fields"] = "사용제한"
    world.fail[(REP["rcept_no"], "2")] = 503
    body = world.call()
    assert body["status"] == "partial" and body["data"]["notes"]["사용제한"]["status"] == svc.OK
    assert len(body["warnings"]) == 1 and "재무상태표" in body["warnings"][0]
    md = world.call(format="md")
    assert "같은 이름의 계정을 못 찾았다" not in md
    assert "재무상태표 절을 받지 못해 분모를 확인하지 못했다" in md


def test_a_complete_failure_still_falls_back_to_the_whole_document(world):
    """절을 하나도 못 받은 경우는 지금처럼 전체 문서 폴백(그것도 실패하면 error) — 이 고침이 바꾸지 않는다."""
    world.fail[(REP["rcept_no"], "10")] = 503
    world.fail[(REP["rcept_no"], "11")] = 503
    body = world.call()
    assert body["status"] == "error" and "원문을 받지 못했습니다" in body["warnings"][0]


def test_only_the_adopted_report_failures_are_reported(world):
    """첫 후보가 절을 전부 못 받아 두 번째 후보를 썼다면, 알릴 실패는 두 번째 보고서의 것뿐이다(접수번호별)."""
    world.cands = [REP, REP2]
    world.fail[(REP["rcept_no"], "10")] = 503
    world.fail[(REP["rcept_no"], "11")] = 503
    world.fail[(REP2["rcept_no"], "11")] = 503
    body = world.call()
    assert body["data"]["report"]["rcept_no"] == REP2["rcept_no"]
    assert body["status"] == "partial" and len(body["warnings"]) == 1
    assert REP2["rcept_no"] in body["warnings"][0] and REP["rcept_no"] not in body["warnings"][0]


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# P1-#42 — 보고서 종류별 검색 실패
# ═════════════════════════════════════════════════════════════════════════════════════════════════
@pytest.fixture
def year_world(monkeypatch):
    """`year` 를 준 경로 — 실제 `_candidates`·`_find_report_for_bsns_year` 를 쓰고 검색만 가짜로 답한다."""
    # test_financial_notes.py 의 두 시험이 이 모듈의 두 이름을 되돌리지 않고 갈아 끼운 채 남긴다 — 실제 함수를 명시해 그 영향을 받지 않는다
    monkeypatch.setattr(T, "_find_report_for_bsns_year", BD._find_report_for_bsns_year)
    monkeypatch.setattr(T, "_find_report_candidates", BD._find_report_candidates)
    return World(monkeypatch, year_mode=True)


@pytest.mark.parametrize("status,guide", [("800", "시스템 점검"), ("020", "하루 요청 한도")])
def test_a_failed_search_of_one_report_kind_is_reported_even_when_another_kind_is_found(year_world, status, guide):
    year_world.search = {"A001": DartClientError(status, "x"), "A003": [REP2]}
    body = year_world.call()
    assert body["data"]["report"]["report_nm"] == "분기보고서 (2025.09)"          # 고른 보고서는 그대로(반환 목록 불변)
    assert body["status"] == "partial", body["status"]                           # 종전: exact
    assert len(body["warnings"]) == 1 and "사업보고서" in body["warnings"][0] and guide in body["warnings"][0]
    assert "최신" not in body["warnings"][0], "어느 종류가 최신인지 모르면 단언하지 않는다"
    assert body["data"]["notes"]["FVPL"]["status"] == svc.OK                      # 필드 값은 건드리지 않는다
    assert "사업보고서" in year_world.call(format="md").split("## FVPL")[0]


def test_a_search_with_no_filing_is_an_answer_not_a_failure(year_world):
    year_world.search = {"A003": [REP2]}                                          # A001·A002 는 013(없음)
    body = year_world.call()
    assert body["status"] == "exact" and body["warnings"] == []


def test_candidates_returns_the_same_list_and_records_the_failure_apart(monkeypatch):
    """반환 목록은 그대로(기존 시험 계약 「한 종류라도 찾으면 그것을 쓴다」) — 실패는 gaps 로 따로 알린다."""
    async def found_or_down(client, corp_code, bsns_year, reprt_code):
        if reprt_code == "11013":
            return [{"rcept_no": "Q1", "rcept_dt": "20190515", "report_nm": "분기보고서 (2019.03)"}]
        raise DartClientError("020", "x")
    monkeypatch.setattr(T, "_find_report_for_bsns_year", found_or_down)
    gaps: list[dict] = []
    assert [r["rcept_no"] for r in _run(T._candidates(None, "C", "quarter", "2019", gaps))] == ["Q1"]
    assert [g["kind"] for g in gaps] == ["search"] and "1분기" not in gaps[0]["warning"] and "3분기" in gaps[0]["warning"]


def test_a_year_less_call_is_untouched(world):
    """year 가 없으면 검색은 한 번뿐이라 일부 실패가 없다 — 응답은 그대로(오류는 공용 래퍼)."""
    body = world.call()
    assert body["status"] == "exact" and body["warnings"] == []
