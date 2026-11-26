import asyncio
import re

import pytest

from open_proxy_mcp.dart.client import DartClientError
from open_proxy_mcp.services import screener
from open_proxy_mcp.services.treasury_share import welfare_fund_disposal
from open_proxy_mcp.tools.screener import _render_welfare_fund


def document(xml):
    return {"html": xml, "text": re.sub(r"<[^>]+>", " ", xml)}


def decision(purpose, recipient=""):
    return document(f'<TE ACODE="SEL_PPS">{purpose}</TE><TE ACODE="DSPS_PARN">{recipient}</TE>')


def result(*recipients):
    rows = [f'<TR><TE ACODE="ACT_CNT">{qty}</TE><TE ACODE="OBJ_OTH">{name}</TE></TR>'
            for name, qty in recipients]
    return document("<TABLE>" + "".join(rows) + "</TABLE>")


@pytest.mark.parametrize("purpose,recipient,expected", [
    ("사내근로복지기금과 우리사주조합에 출연", "", "contribution_in_decision"),
    ("자기주식 현물 출연", "서로 공동 근로 복지 기금", "contribution_in_decision"),
    ("자사주 무상양도", "서로 공동근로복지기금", "contribution_in_decision"),
    ("사내근로복지기금에 현금 출연하기 위한 주식 처분", "", "needs_reading"),
    ("사내근로복지기금에 대한 출연금 마련", "", "needs_reading"),
    ("사내근로복지기금에 출연하지 않기로 결정", "", "needs_reading"),
    ("우리사주조합에 출연 및 사내근로복지기금에 유상매각", "", "needs_reading"),
    ("사내근로복지기금에 출연", "직원 개인", "needs_reading"),
    ("우리사주조합에 출연", "사내근로복지기금", "needs_reading"),
    ("우리사주조합에 자사주 출연", "우리사주조합", "no_mention"),
])
def test_decision_requires_share_contribution(purpose, recipient, expected):
    assert welfare_fund_disposal(decision(purpose, recipient), "결정(예정)")["status"] == expected


def test_same_report_separate_recipients_and_shared_or_missing_quantities():
    name = "링네트사내근로복지기금"
    ev = welfare_fund_disposal(result(("우리사주조합", "2,500"), (name, "600,000")), "결과(확정)")
    assert ev["status"] == "recipient_in_result"
    assert ev["result_rows"] == [{"recipient": name, "row_shares": 600000, "shared_recipient_row": False}]
    for qty in ("0", "—", ""):
        assert welfare_fund_disposal(result((name, qty)), "결과(확정)")["status"] == "needs_reading"
    assert welfare_fund_disposal(result((name, "별도 기재")), "결과(확정)")["result_rows"][0]["row_shares"] is None
    assert welfare_fund_disposal(result((name + " 및 우리사주조합", "602,500")), "결과(확정)")["status"] == "needs_reading"
    assert welfare_fund_disposal(document("<P>사내근로복지기금</P>"), "결과(확정)")["status"] == "needs_reading"
    assert welfare_fund_disposal({}, "결과(확정)")["status"] == "unassessed"


def test_market_pages_use_disposal_receipts_and_keep_corrections(monkeypatch):
    receipts = [f"2026030400000{i}" for i in range(6)]
    titles = ["자기주식취득결과보고서", "주요사항보고서(자기주식처분결정)",
              "자기주식처분결과보고서", "[기재정정]주요사항보고서(자기주식처분결정)",
              "자기주식처분결과보고서", "주요사항보고서(자기주식처분결정)"]
    docs = [None, decision("사내근로복지기금에 출연"), result(("사내근로복지기금", "17")),
            decision("임직원 상여"), result(("우리사주조합", "30")), decision("임직원 보상")]
    opened = []

    class Client:
        def api_call_snapshot(self):
            return len(opened)

        async def search_filings(self, *, pblntf_detail_ty, **kwargs):
            assert pblntf_detail_ty in {"B001", "E001"}
            items = [{"corp_code": "00000001", "corp_name": "회사", "stock_code": "000001",
                      "corp_cls": "Y", "rcept_no": receipt, "rcept_dt": "20260304", "report_nm": title}
                     for receipt, title in zip(receipts, titles)
                     if ("결과" in title) == (pblntf_detail_ty == "E001")]
            return {"list": items, "total_count": len(items)}

        async def get_document_cached(self, receipt):
            opened.append(receipt)
            return docs[receipts.index(receipt)]

    async def universe(_):
        return screener.UniverseFilter(label="전체시장", resolved=True)

    monkeypatch.setattr(screener, "get_dart_client", Client)
    monkeypatch.setattr(screener, "_scan_code", screener._scan_code_uncached)
    monkeypatch.setattr(screener, "resolve_universe", universe)
    args = {"start_date": "20260304", "end_date": "20260304"}
    first = asyncio.run(screener.build_welfare_fund_screen(**args, max_hits=2))
    assert first["matches"] == [] and first["no_mention_in_page"] == 2
    assert first["candidate_paging"]["matched"] == 5 and first["candidate_paging"]["next_offset"] == 2
    assert opened == receipts[4:][::-1]
    second = asyncio.run(screener.build_welfare_fund_screen(**args, offset=2))
    assert [r["rcept_no"] for r in second["matches"]] == [receipts[2], receipts[1]]
    assert second["followups"][0]["evidence"]["status"] == "amendment_to_check"
    assert second["candidate_paging"]["has_more"] is False and second["status"] == "ok"
    assert "17주" in _render_welfare_fund(second)

    async def quota(self, receipt):
        opened.append(receipt)
        raise DartClientError("020", "일일 한도")

    opened.clear()
    monkeypatch.setattr(Client, "get_document_cached", quota)
    halted = asyncio.run(screener.build_welfare_fund_screen(**args))
    assert len(opened) == 1 and halted["status"] == "partial"
    assert all(r["evidence"]["status"] == "unassessed" for r in halted["followups"])
    assert len(halted["followups"]) == 5

    search = Client.search_filings

    async def failed_list(self, *, pblntf_detail_ty, **kwargs):
        if pblntf_detail_ty == "B001":
            raise DartClientError("020", "일일 한도")
        return await search(self, pblntf_detail_ty=pblntf_detail_ty, **kwargs)

    opened.clear()
    monkeypatch.setattr(Client, "search_filings", failed_list)
    partial = asyncio.run(screener.build_welfare_fund_screen(**args))
    assert opened == [] and partial["status"] == "partial"
    assert len(partial["followups"]) == 2
