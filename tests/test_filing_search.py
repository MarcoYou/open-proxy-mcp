import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from open_proxy_mcp.services import filing_search


class ConcurrentPageClient:
    def __init__(self):
        self.started_pages: list[int] = []
        self.remaining_pages_started = asyncio.Event()

    async def search_filings(self, *, page_no, **_kwargs):
        if page_no == 1:
            return {
                "list": [{"report_nm": "page 1", "rcept_dt": "20260101", "rcept_no": "1"}],
                "total_count": 300,
            }
        self.started_pages.append(page_no)
        if len(self.started_pages) == 2:
            self.remaining_pages_started.set()
        await asyncio.wait_for(self.remaining_pages_started.wait(), timeout=0.2)
        return {
            "list": [{"report_nm": f"page {page_no}", "rcept_dt": f"2026010{page_no}", "rcept_no": str(page_no)}],
            "total_count": 300,
        }


def test_fetch_filings_for_title_scan_fetches_remaining_pages_concurrently(monkeypatch):
    client = ConcurrentPageClient()
    monkeypatch.setattr(filing_search, "get_dart_client", lambda: client)

    items, notices, error = asyncio.run(
        filing_search.fetch_filings_for_title_scan(
            corp_code="00126380",
            bgn_de="20250101",
            end_de="20251231",
            pblntf_tys="I",
            keyword_label="page concurrency",
        )
    )

    assert error is None
    assert notices == []
    assert set(client.started_pages) == {2, 3}
    assert [item["report_nm"] for item in items] == ["page 3", "page 2", "page 1"]
