#!/usr/bin/env python3
"""Fail when the runtime MCP tool surface and wiki catalog drift apart."""

import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 스캔에서 뺄 폴더. **레포의 문서가 아닌 것**은 읽지 않는다.
#: 260827: `.claude/worktrees/` 안의 옛 사본(다른 세션이 만든 git worktree)까지 읽고
#: 「runtime에 없는 tool 링크: valuation」으로 죽었다. CI 는 깨끗한 체크아웃이라 통과하고
#: 로컬만 실패하니, **사람이 고칠 곳이 없는 오탐**으로 시간을 쓰게 된다.
_SKIP_DIRS = {".git", ".claude", ".venv", "venv", "node_modules",
              "__pycache__", ".mypy_cache", ".pytest_cache", "build", "dist"}


def _repo_markdown():
    """레포에 실제로 속한 .md 만 훑는다(위 폴더는 통째로 건너뛴다)."""
    for path in ROOT.rglob("*.md"):
        if _SKIP_DIRS.isdisjoint(path.relative_to(ROOT).parts):
            yield path
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from open_proxy_mcp.server import build_mcp


CATALOG_DIR = ROOT / "wiki" / "tools"
SUPPORT_PAGES = {
    "README",
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

    # 루트 README도 사람이 보는 공개 카탈로그다. wiki/tools/README만 검사하면
    # tool 개명·신설 때 사용자 진입점이 조용히 낡는다.
    for path in (ROOT / "README.md", ROOT / "README_ENG.md"):
        text = path.read_text(encoding="utf-8")
        missing = sorted(t for t in runtime_tools if f"wiki/tools/{t}.md" not in text)
        if missing:
            problems.append(f"{path.name}에 tool 링크 없음: " + ", ".join(missing))
        count_patterns = (
            r"Tool 구조 \((\d+)개\)", r"Tool Structure \((\d+) tools\)",
            r"총 (\d+)개 tool", r"(\d+) tools in total",
        )
        claims = {int(m.group(1)) for rx in count_patterns if (m := re.search(rx, text))}
        if claims != {len(runtime_tools)}:
            problems.append(f"{path.name} tool 수 주장 불일치: {sorted(claims) or '없음'} vs {len(runtime_tools)}")

    # 구 tool 페이지 링크는 basename resolver가 살려주지 않는 일반 Markdown 링크다.
    stale_links: set[str] = set()
    for path in _repo_markdown():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for name in re.findall(r"\]\([^)]*wiki/tools/([a-z0-9_]+)\.md(?:#[^)]*)?\)", text):
            if name not in runtime_tools and name not in SUPPORT_PAGES:
                stale_links.add(name)
    if stale_links:
        problems.append("runtime에 없는 tool 링크: " + ", ".join(sorted(stale_links)))

    # 영문 릴리즈노트와 기능 문서는 한국어 정본과 짝을 이뤄야 한다.
    ko_release = (ROOT / "docs/RELEASE_NOTES.md").read_text(encoding="utf-8")
    en_release = (ROOT / "docs/RELEASE_NOTES_ENG.md").read_text(encoding="utf-8")
    ko_head = re.search(r"^## (.+)$", ko_release, re.MULTILINE)
    if ko_head and ko_head.group(1) not in en_release:
        problems.append("RELEASE_NOTES_ENG.md에 최신 섹션 없음: " + ko_head.group(1))
    ko_features = {p.stem for p in (ROOT / "docs/features").glob("*.md")}
    en_features = {p.stem for p in (ROOT / "docs/features/en").glob("*.md")}
    if ko_features != en_features:
        problems.append("한/영 feature 문서 비대칭: "
                       f"ko-only={sorted(ko_features - en_features)}, en-only={sorted(en_features - ko_features)}")

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
