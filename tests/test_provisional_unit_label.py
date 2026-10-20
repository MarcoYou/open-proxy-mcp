"""잠정실적 금액 단위 읽기 — 쉼표가 없는 표기도 읽고, 못 읽으면 백만원으로 가정하지 않는다. DATA-U1. network 0 · DB 0.

종전 정규식은 「단위 : 백만원, %」처럼 단위 뒤에 쉼표가 있어야 읽었고, 못 읽으면 조용히 백만원으로 환산했다. 쉼표 없는 「(단위 : 억원)」은
억원인데 백만원으로 읽혀 headline 금액이 **100배 작게** 나갔다. 이제 ① 쉼표 유무와 무관하게 읽고 ② 단위가 없으면 `unit_raw=None` ·
금액(`*_krw`)은 비우고 원문 숫자는 `*_raw` 로 남기며 경고를 싣는다(증감률 %는 단위와 무관해 그대로). 구조변경 서식도 같은 규칙이다.
"""
from __future__ import annotations

import asyncio

from open_proxy_mcp.services import provisional_earnings as pe
from open_proxy_mcp.services.provisional_earnings import UNIT_UNKNOWN_WARNING, parse_provisional_earnings

_NAME = "연결재무제표기준영업(잠정)실적(공정공시)"


def _html(unit_cell: str) -> str:
    return f"""<html><body>
<p>연결재무제표 기준 영업(잠정)실적(공정공시)</p><p>2026-07-01 ~ 2026-09-30</p>
<table>
<tr><td>1. 연결실적내용</td><td></td><td></td><td></td><td>{unit_cell}</td></tr>
<tr><td>구분</td><td>구분</td><td>당기실적</td><td>전기실적</td><td>전기대비 증감율(%)</td><td>흑자적자전환여부</td>
<td>전년동기실적</td><td>전년동기대비 증감율(%)</td><td>흑자적자전환여부</td></tr>
<tr><td>매출액</td><td>당해실적</td><td>4,399</td><td>4,200</td><td>4.7</td><td>-</td><td>4,100</td><td>7.3</td><td>-</td></tr>
<tr><td>영업이익</td><td>당해실적</td><td>48</td><td>60</td><td>-20.0</td><td>-</td><td>70</td><td>-31.4</td><td>-</td></tr>
</table></body></html>"""


def test_unit_with_comma_is_unchanged():
    p = parse_provisional_earnings(_html("단위 : 억원, %"), _NAME)
    assert p["unit_raw"] == "억원"
    assert p["headline"]["revenue"]["value_krw"] == 4399 * 1e8
    assert "unit_warning" not in p


def test_unit_without_comma_is_read():
    for cell in ("(단위 : 억원)", "단위:억원", "단위 : 억원 %"):
        p = parse_provisional_earnings(_html(cell), _NAME)
        assert p["unit_raw"] == "억원", cell
        # 옛 값: 쉼표가 없어 못 읽고 백만원으로 환산 → 4,399백만원 = 43.99억(100배 작게)
        assert p["headline"]["revenue"]["value_krw"] == 4399 * 1e8, cell
    p = parse_provisional_earnings(_html("(단위 : 백만원)"), _NAME)
    assert p["unit_raw"] == "백만원" and p["headline"]["operating_profit"]["value_krw"] == 48 * 1e6


def test_missing_unit_is_not_assumed_to_be_million_won():
    p = parse_provisional_earnings(_html(""), _NAME)
    assert p["unit_raw"] is None                          # 옛 값: "백만원"(가정)
    assert p["unit_warning"] == UNIT_UNKNOWN_WARNING
    assert p["kind"] == "financial"                       # 표는 읽었다 — 비재무형으로 오인하지 않는다
    rev = p["headline"]["revenue"]
    assert rev["value_krw"] is None and rev["value_raw"] == 4399
    assert rev["yoy_pct"] == 7.3                          # 증감률은 단위와 무관해 그대로
    assert p["table_markdown"]                            # 원문 표는 그대로 담는다


def test_structure_change_form_has_the_same_rule():
    html = """<table>
  <tr><td>2. 결산기간</td><td>당해사업연도</td></tr>
  <tr><td>- 시작일</td><td>2025-07-01</td></tr>
  <tr><td>- 종료일</td><td>2026-06-30</td></tr>
  <tr><td>3. 매출액 또는 손익구조 변동내용{unit}</td><td>당해사업연도</td><td>직전사업연도</td><td>증감금액</td><td>증감비율(%)</td><td>흑자적자전환여부</td></tr>
  <tr><td>- 매출액</td><td>177,803,500</td><td>166,650,466</td><td>11,153,035</td><td>6.7</td><td>-</td></tr>
  <tr><td>- 영업이익</td><td>8,415,374</td><td>6,641,339</td><td>1,774,035</td><td>26.7</td><td>-</td></tr>
</table>"""
    name = "매출액또는손익구조30%(대규모법인은15%)이상변경"
    ok = parse_provisional_earnings(html.format(unit="(단위:천원)"), name)
    assert ok["unit_raw"] == "천원" and ok["headline"]["revenue"]["value_krw"] == 177_803_500_000
    none = parse_provisional_earnings(html.format(unit=""), name)
    assert none["unit_raw"] is None and none["unit_warning"] == UNIT_UNKNOWN_WARNING   # 옛 값: "천원"(가정)
    assert none["headline"]["revenue"]["value_krw"] is None and none["headline"]["revenue"]["value_raw"] == 177_803_500
    assert none["headline"]["revenue"]["yoy_pct"] == 6.7


def test_correction_form_without_unit_is_not_converted():
    html = """<table>
  <tr><td>실적기간</td><td>2026-04-01 ~ 2026-06-30</td></tr>
  <tr><td>구분</td><td>정정전</td><td>정정후</td></tr>
  <tr><td>매출액</td><td>170,000,000</td><td>171,500,000</td></tr>
</table>"""
    p = parse_provisional_earnings(html, "[기재정정] 영업(잠정)실적")
    assert p["unit_raw"] is None and p["headline"]["revenue"]["value_krw"] is None
    assert p["headline"]["revenue"]["value_raw"] == 171_500_000


def test_unknown_unit_is_not_compared_with_consensus():
    p = parse_provisional_earnings(_html(""), _NAME)
    skip = pe._gap_not_applicable({**p, "period_kind": "quarter"}, "005930")
    assert skip and skip["status"] == "not_comparable" and "단위" in skip["why"]


def test_service_payload_carries_the_unit_warning(monkeypatch):
    from open_proxy_mcp.services.company import CompanyResolution
    from open_proxy_mcp.services.contracts import AnalysisStatus

    class _Client:
        async def search_filings(self, **kw):
            if kw.get("pblntf_detail_ty") != "I002":
                return {"list": []}
            return {"list": [{"rcept_no": "20261016800456", "rcept_dt": "20261016", "report_nm": _NAME}]}

        async def get_document_cached(self, rcept_no):
            return {"html": _html("")}

        async def get_company_info(self, corp_code):
            return {"acc_mt": "12"}

    async def resolve(q, *a, **k):
        return CompanyResolution(status=AnalysisStatus.EXACT, query=q, candidates=[],
                                 selected={"corp_code": "00000001", "corp_name": "시험", "stock_code": "000001"})

    monkeypatch.setattr(pe, "resolve_company_query", resolve)
    monkeypatch.setattr("open_proxy_mcp.dart.client.get_dart_client", lambda: _Client())
    out = asyncio.run(pe.build_provisional_earnings_payload("시험"))
    assert out["status"] == "exact"
    assert UNIT_UNKNOWN_WARNING in out["warnings"]
    assert out["data"]["unit_raw"] is None
    assert "consensus_gap" not in out["data"]
