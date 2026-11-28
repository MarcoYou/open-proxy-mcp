"""Interactive MCP chart resources. The original JSON is still available in structuredContent.result."""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import re

from mcp.types import CallToolResult, TextContent

# A chart request with the default scope selects the corresponding detail view.
_SCOPES = {
    "performance_analysis": ("summary", "series"),
    "dividend_data": ("firm", "firm"),
    "corp_gov_report": ("summary", "metrics"),
    "shareholder_meeting_results": (None, None),
    "ownership_structure": ("summary", "blocks"),
    "financial_metrics": ("summary", "quarterly"),
    "treasury_share": ("summary", "summary"),
}


_FINANCIAL_SCOPES = {"quarterly_performance": "quarterly", "cash_flow": "yearly", "cash_debt": "yearly"}


CHART_URI = "ui://openproxy/charts-v3.html"
CHART_URIS = (CHART_URI, "ui://openproxy/charts-v2.html", "ui://openproxy/charts-v1.html")


def chart_tool_meta(name):
    if name in _SCOPES:
        return {"ui": {"resourceUri": CHART_URI}, "openai/outputTemplate": CHART_URI}
    return {}


def chart_apps():
    from importlib.resources import files
    from mcp.server.apps import Apps

    apps = Apps()
    html = files("open_proxy_mcp").joinpath("chart_app.html").read_text(encoding="utf-8")
    for uri in CHART_URIS:
        apps.add_html_resource(uri, html, name=uri.rsplit("/", 1)[1], title="OPM 공시 차트", prefers_border=True)
    return apps


def _render(name, payload):
    # Import the plotting backend off the request event loop, on first chart use.
    from open_proxy_mcp.services.chart_images import render_chart
    return render_chart(name, payload)


def with_chart_output(fn):
    if fn.__name__ not in _SCOPES:
        return fn
    default, detail = _SCOPES[fn.__name__]

    @functools.wraps(fn)
    async def call(*args, **kwargs):
        if kwargs.get("format") != "chart":
            return await fn(*args, **kwargs)
        scope = kwargs.get("scope", default)
        name = fn.__name__
        chart_scope = detail
        if name == "financial_metrics":
            name = kwargs.get("chart") or ("cash_flow" if scope == "yearly" else "quarterly_performance")
            if name not in _FINANCIAL_SCOPES:
                return "재무 차트는 chart=quarterly_performance, cash_flow, cash_debt 중에서 선택하세요."
            chart_scope = _FINANCIAL_SCOPES[name]
        receipt = kwargs.get("rcept_no") or ""
        if name == "ownership_structure":
            chart = kwargs.get("chart") or ("transfer_donut" if scope == "changes" or receipt else "blocks")
            if chart == "transfer_donut":
                name, chart_scope = chart, "changes"
                if receipt and not re.fullmatch(r"[0-9]{14}", receipt):
                    return "계약 공시의 접수번호 14자리를 입력하세요."
            elif chart != "blocks" or receipt:
                return "지분 차트는 blocks 또는 transfer_donut 중에서 선택하세요. 접수번호는 계약 도넛에서 사용합니다."
        if scope not in (default, chart_scope):
            return f"이 도구의 차트는 `{chart_scope}` 조회에서 제공합니다. 다른 조회는 format=md 또는 json을 사용하세요."
        query = {**kwargs, "format": "json"}
        if chart_scope is not None:
            query["scope"] = chart_scope
        if name == "transfer_donut" and receipt and not (query.get("start_date") or query.get("end_date")):
            query.update(start_date=receipt[:8], end_date=receipt[:8])
        raw = await fn(*args, **query)
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            return raw  # Tool-level input and availability messages can be plain text.
        if payload.get("status") not in {"ok", "exact", "partial"}:
            return raw
        try:
            render_input = {**payload, "chart_rcept_no": receipt} if name == "transfer_donut" else payload
            result = await asyncio.to_thread(_render, name, render_input)
        except Exception:
            logging.getLogger(__name__).exception("Could not render %s chart", fn.__name__)
            result = None
        if result is None:
            text = "차트를 표시할 수 없어 조회 원문을 제공합니다.\n\n" + raw
            return CallToolResult(content=[TextContent(type="text", text=text)], structured_content={"result": raw})
        vector, summary, chart = result
        for warning in payload.get("warnings", []):
            summary += f"\n\n⚠ {warning}"
        for notice in payload.get("notices", []):
            summary += f"\n\n> 📢 [공지] {notice.get('body', '')}"
        content = [TextContent(type="text", text=summary)]
        meta = None
        if vector is not None:
            meta = {"opm/chart": {**chart, "svg": vector, "notes": [*payload.get("warnings", []),
                    *(n.get("body", "") for n in payload.get("notices", []))]}}
        return CallToolResult(content=content, structured_content={"result": raw}, meta=meta)

    return call
