#!/usr/bin/env python3
"""Fail when the runtime MCP tool surface and wiki catalog drift apart."""

import asyncio
from pathlib import Path

from open_proxy_mcp.server import build_mcp


ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = ROOT / "wiki" / "tools"
SUPPORT_PAGES = {
    "README",
    "data_tool_disclosure_map",
    "tool_call_budget",
    "tool_disclosure_map",
}


def main() -> int:
    runtime_tools = {tool.name for tool in asyncio.run(build_mcp().list_tools())}
    documented_tools = {
        path.stem
        for path in CATALOG_DIR.glob("*.md")
        if path.stem not in SUPPORT_PAGES
    }
    missing_docs = sorted(runtime_tools - documented_tools)
    stale_docs = sorted(documented_tools - runtime_tools)
    if missing_docs or stale_docs:
        if missing_docs:
            print("Missing wiki tool pages:", ", ".join(missing_docs))
        if stale_docs:
            print("Wiki pages without runtime tools:", ", ".join(stale_docs))
        return 1
    print(f"Tool catalog matches runtime ({len(runtime_tools)} tools).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
