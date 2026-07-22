#!/usr/bin/env python3
"""Fail when public business_details field docs drift from the runtime contract."""

from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_SOURCE = ROOT / "open_proxy_mcp/services/business_details.py"
WIKI_TOOL_DOC = ROOT / "wiki/tools/business_details.md"
FEATURE_DOCS = (
    ROOT / "docs/features/business-details.md",
    ROOT / "docs/features/en/business-details.md",
)
README_LINKS = {
    ROOT / "README.md": "docs/features/business-details.md",
    ROOT / "README_ENG.md": "docs/features/en/business-details.md",
}
DOC_CONTRACT = re.compile(
    r"<!-- documentation-contract: business_details fields=([^>]+) -->"
)
WIKI_SCOPE = re.compile(r"^scope:\s*\[([^]]*)\]$", re.MULTILINE)


def _fields(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _runtime_fields() -> tuple[str, ...]:
    tree = ast.parse(RUNTIME_SOURCE.read_text(encoding="utf-8"), filename=str(RUNTIME_SOURCE))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == "BUSINESS_DETAILS_FIELDS" for target in node.targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, tuple) and all(isinstance(field, str) for field in value):
                return value
    raise ValueError("BUSINESS_DETAILS_FIELDS tuple not found in runtime source")


def main() -> int:
    expected = _runtime_fields()
    issues: list[str] = []

    wiki = WIKI_TOOL_DOC.read_text(encoding="utf-8")
    scope = WIKI_SCOPE.search(wiki)
    if not scope:
        issues.append(f"{WIKI_TOOL_DOC.relative_to(ROOT)}: missing frontmatter scope")
    elif _fields(scope.group(1)) != expected:
        issues.append(f"{WIKI_TOOL_DOC.relative_to(ROOT)}: scope != runtime fields")

    for path in FEATURE_DOCS:
        match = DOC_CONTRACT.search(path.read_text(encoding="utf-8"))
        if not match:
            issues.append(f"{path.relative_to(ROOT)}: missing business_details documentation contract")
        elif _fields(match.group(1)) != expected:
            issues.append(f"{path.relative_to(ROOT)}: documented fields != runtime fields")

    for path, target in README_LINKS.items():
        if f"]({target})" not in path.read_text(encoding="utf-8"):
            issues.append(f"{path.relative_to(ROOT)}: missing link to {target}")

    if issues:
        print("Documentation contract drift:", *[f"- {issue}" for issue in issues], sep="\n")
        return 1
    print(f"Documentation contract matches runtime ({len(expected)} business_details fields).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
