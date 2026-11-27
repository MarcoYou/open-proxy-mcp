"""Opt-in MCP image content. The original JSON is still available in structuredContent.result."""
from __future__ import annotations

import asyncio
import base64
import functools
import json
import logging

from mcp.types import CallToolResult, ImageContent, TextContent

# A chart request with the default scope selects the corresponding detail view.
_SCOPES = {
    "performance_analysis": ("summary", "series"),
    "dividend_data": ("firm", "firm"),
    "corp_gov_report": ("summary", "metrics"),
}


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
        if kwargs.get("scope", default) not in (default, detail):
            return f"이 도구의 차트는 `{detail}` 조회에서 제공합니다. 다른 조회는 format=md 또는 json을 사용하세요."
        raw = await fn(*args, **{**kwargs, "scope": detail, "format": "json"})
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            return raw  # Tool-level input and availability messages can be plain text.
        if payload.get("status") not in {"ok", "exact", "partial"}:
            return raw
        try:
            result = await asyncio.to_thread(_render, fn.__name__, payload)
        except Exception:
            logging.getLogger(__name__).exception("Could not render %s chart", fn.__name__)
            result = None
        if result is None:
            text = "차트를 표시할 수 없어 조회 원문을 제공합니다.\n\n" + raw
            return CallToolResult(content=[TextContent(type="text", text=text)], structured_content={"result": raw})
        png, summary = result
        for warning in payload.get("warnings", []):
            summary += f"\n\n⚠ {warning}"
        for notice in payload.get("notices", []):
            summary += f"\n\n📢 {notice.get('body', '')}"
        return CallToolResult(content=[
            ImageContent(type="image", mime_type="image/png", data=base64.b64encode(png).decode("ascii")),
            TextContent(type="text", text=summary),
        ], structured_content={"result": raw})

    return call
