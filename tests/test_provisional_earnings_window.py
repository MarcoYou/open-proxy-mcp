# -*- coding: utf-8 -*-
"""provisional_earnings 의 months·start_date·end_date — 서비스엔 있었지만 도구에 안 노출돼 「작년 3분기 잠정실적」을 못 집었다(260907). network 0."""
from __future__ import annotations

import asyncio

from open_proxy_mcp.server import mcp
from open_proxy_mcp.tools import provisional_earnings as T


def _call(**a):
    async def go():
        r = await mcp.call_tool("provisional_earnings", a)
        return "".join(getattr(c, "text", "") for c in (r if isinstance(r, list) else r.content))
    return asyncio.run(go())


def test_window_args_pass_through_to_the_service(monkeypatch):
    seen = {}
    async def fake(company, *, months=6, start_date=None, end_date=None, format="md"):
        seen.update(company=company, months=months, start_date=start_date, end_date=end_date)
        return {"status": "no_filing", "subject": company, "warnings": ["x"], "data": {}}
    monkeypatch.setattr(T, "build_provisional_earnings_payload", fake)
    _call(company="삼성전자", start_date="20251001", end_date="20251115")
    assert seen == {"company": "삼성전자", "months": 6, "start_date": "20251001", "end_date": "20251115"}
    _call(company="삼성전자", months=18)
    assert seen["months"] == 18 and seen["start_date"] is None
    assert "YYYYMMDD" in _call(company="삼성전자", start_date="2025-10-01")
