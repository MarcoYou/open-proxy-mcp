"""지주사의 「최신 잠정실적」 자리에 자회사 실적이 앉지 않는지. network 0.

지주사는 자회사 실적을 같은 서식(영업(잠정)실적)에 「자회사의 주요경영사항」으로 낸다. 최신 1건만
고르면 하나금융지주의 최신 잠정실적이 **하나증권** 실적(영업이익 211억)이 되고, 지주 컨센서스와
맞대면 -98% 같은 거짓 쇼크가 나온다(2026-10-23 실측). 본체 공시를 먼저 고르고, 건너뛴 자회사
공시는 감추지 않고 목록으로 남긴다. 본체 공시가 없으면 자회사 공시를 내되 이름을 붙이고 맞대지 않는다.

하나증권 표는 하나금융지주 `20261023800558` 원문의 머리·표 칸 그대로(행 일부만).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from open_proxy_mcp.services import forward_estimates as fe
from open_proxy_mcp.services import provisional_earnings as pe

_TABLE = """<table>
<tr><td>1. 연결실적내용</td><td></td><td></td><td></td><td>단위 : 백만원, %</td></tr>
<tr><td>구분</td><td>구분</td><td>당기실적</td><td>전기실적</td><td>전기대비 증감율(%)</td><td>흑자적자전환여부</td>
<td>전년동기실적</td><td>전년동기대비 증감율(%)</td><td>흑자적자전환여부</td></tr>
<tr><td>구분</td><td>구분</td><td>(26.3Q)</td><td>(26.2Q)</td><td>증감율(%)</td><td>흑자적자전환여부</td><td>(25.3Q)</td><td>증감율(%)</td><td>흑자적자전환여부</td></tr>
<tr><td>매출액</td><td>당해실적</td><td>{rev}</td><td>11,715,844</td><td>-59.84</td><td>-</td><td>2,429,173</td><td>93.68</td><td>-</td></tr>
<tr><td>영업이익</td><td>당해실적</td><td>{op}</td><td>203,673</td><td>-89.65</td><td>-</td><td>65,435</td><td>-67.77</td><td>-</td></tr>
</table>"""

SUB_HTML = ("<html><body><p>연결재무제표 기준 영업(잠정)실적(공정공시)</p><p>자회사인 하나증권 의 주요경영사항신고</p>"
            "<table><tr><td>실적기간</td><td>당기실적</td><td>2026-07-01 ~ 2026-09-30</td></tr></table>"
            + _TABLE.format(rev="4,704,737", op="21,088") + "</body></html>")
OWN_HTML = ("<html><body><p>연결재무제표 기준 영업(잠정)실적(공정공시)</p>"
            "<table><tr><td>실적기간</td><td>당기실적</td><td>2026-07-01 ~ 2026-09-30</td></tr></table>"
            + _TABLE.format(rev="12,000,000", op="1,500,000") + "</body></html>")

SUB_TITLED = {"rcept_no": "20261023800558", "rcept_dt": "20261023",
              "report_nm": "연결재무제표기준영업(잠정)실적(공정공시)(자회사의 주요경영사항)              "}
SUB_UNTITLED = {"rcept_no": "20261023800999", "rcept_dt": "20261023",
                "report_nm": "연결재무제표기준영업(잠정)실적(공정공시)"}     # 제목 표시 없음 — 머리로만 안다
OWN = {"rcept_no": "20261022800100", "rcept_dt": "20261022",
       "report_nm": "연결재무제표기준영업(잠정)실적(공정공시)"}

_DOCS = {SUB_TITLED["rcept_no"]: SUB_HTML, SUB_UNTITLED["rcept_no"]: SUB_HTML, OWN["rcept_no"]: OWN_HTML}


class _Client:
    def __init__(self, items):
        self.items = items
        self.read: list[str] = []

    async def search_filings(self, **kw):
        return {"list": self.items if kw.get("pblntf_detail_ty") == "I002" else []}

    async def get_document_cached(self, rcept_no):
        self.read.append(rcept_no)
        return {"html": _DOCS[rcept_no]}

    async def get_company_info(self, corp_code):
        return {"acc_mt": "12"}


def _run(monkeypatch, items):
    client = _Client(items)
    gap_calls: list[dict] = []

    async def resolve(q):
        return SimpleNamespace(status="exact", selected={"corp_name": "하나금융지주", "corp_code": "00547583",
                                                         "stock_code": "086790"}, candidates=[])

    def gap(stock_code, period_end, filed, actual, consolidated):
        gap_calls.append(actual)
        return {"status": "ok", "as_of": "2026-10-16", "metrics": {}}

    monkeypatch.setattr(pe, "resolve_company_query", resolve)
    import open_proxy_mcp.dart.client as dc
    monkeypatch.setattr(dc, "get_dart_client", lambda: client)
    monkeypatch.setattr(fe, "quarter_consensus_gap", gap)
    out = asyncio.run(pe.build_provisional_earnings_payload("하나금융지주"))
    return out, client, gap_calls


def test_titled_subsidiary_filing_does_not_take_the_parents_place(monkeypatch) -> None:
    out, client, gap_calls = _run(monkeypatch, [SUB_TITLED, OWN])
    data = out["data"]
    assert data["report"]["rcept_no"] == OWN["rcept_no"]
    assert "subsidiary" not in data and "subsidiary_report" not in data
    assert data["headline"]["operating_profit"]["value_krw"] == 1_500_000 * 1e6
    assert [x["rcept_no"] for x in data["subsidiary_filings"]] == [SUB_TITLED["rcept_no"]]
    assert any("자회사 실적 공시 1건" in w for w in out["warnings"])
    assert client.read == [OWN["rcept_no"]]                 # 제목으로 알면 자회사 원문은 읽지도 않는다
    assert len(gap_calls) == 1                              # 본체 실적은 컨센서스와 맞댄다


def test_untitled_subsidiary_filing_is_caught_by_its_head(monkeypatch) -> None:
    out, client, _ = _run(monkeypatch, [SUB_UNTITLED, OWN])
    data = out["data"]
    assert data["report"]["rcept_no"] == OWN["rcept_no"]
    assert data["subsidiary_filings"] == [{"rcept_no": SUB_UNTITLED["rcept_no"], "rcept_dt": "20261023",
                                           "report_nm": SUB_UNTITLED["report_nm"], "subsidiary": "하나증권"}]
    assert client.read == [SUB_UNTITLED["rcept_no"], OWN["rcept_no"]]


def test_only_subsidiary_filing_is_labelled_and_never_compared(monkeypatch) -> None:
    out, _, gap_calls = _run(monkeypatch, [SUB_TITLED])
    data = out["data"]
    assert data["report"]["rcept_no"] == SUB_TITLED["rcept_no"]
    assert data["subsidiary"] == "하나증권" and data["subsidiary_report"] is True
    assert "consensus_gap" not in data and gap_calls == []
    assert any("자회사 하나증권 의 실적이다" in w for w in out["warnings"])


def test_title_marker_alone_marks_the_filing_even_if_head_has_no_name(monkeypatch) -> None:
    _DOCS["20261023800777"] = OWN_HTML                       # 머리에 자회사 문장이 없는 판
    titled_only = {**SUB_TITLED, "rcept_no": "20261023800777"}
    out, _, gap_calls = _run(monkeypatch, [titled_only])
    assert out["data"]["subsidiary_report"] is True and "subsidiary" not in out["data"]
    assert gap_calls == []
