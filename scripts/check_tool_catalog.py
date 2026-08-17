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
    problems: list[str] = []
    if missing_docs:
        problems.append("Missing wiki tool pages: " + ", ".join(missing_docs))
    if stale_docs:
        problems.append("Wiki pages without runtime tools: " + ", ".join(stale_docs))

    # 페이지 존재만 봐서는 부족했다 (260817): proxy_guideline 은 페이지가 있는데도
    # README 의 「도구 한눈에」 표에 없어 사람이 읽는 목록에서만 빠져 있었고,
    # 「카테고리별 통계」는 합 21 로 굳어 런타임 26 과 5 만큼 어긋나 있었다.
    # 검사가 표를 안 보면 표는 반드시 뒤처진다 — 그래서 여기서 함께 본다.
    readme = (CATALOG_DIR / "README.md").read_text(encoding="utf-8")
    unlisted = sorted(t for t in runtime_tools if f"]({t}.md)" not in readme)
    if unlisted:
        problems.append("README 「도구 한눈에」 표에 없음: " + ", ".join(unlisted))

    domains: dict[str, str] = {}
    for name in sorted(documented_tools):
        head = (CATALOG_DIR / f"{name}.md").read_text(encoding="utf-8").split("---")[1:2]
        line = next((ln for ln in "".join(head).splitlines()
                     if ln.startswith("domain:")), "")
        domains[name] = line.split(":", 1)[1].split("#")[0].strip() if line else ""
    ALLOWED = {"data", "action", "reference"}
    bad = sorted(n for n, d in domains.items() if d not in ALLOWED)
    if bad:
        problems.append("domain 미기재/미허용: " + ", ".join(f"{n}({domains[n] or '없음'})" for n in bad))
    else:
        counts = {d: sum(1 for v in domains.values() if v == d) for d in sorted(ALLOWED)}
        for dom, n in counts.items():
            if f"| {dom} | {n} |" not in readme:
                problems.append(f"README 통계표가 실측과 다름: {dom} = {n}")

    if problems:
        for p in problems:
            print(p)
        return 1
    print(f"Tool catalog matches runtime ({len(runtime_tools)} tools) "
          f"— README 표·domain 통계 포함.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
