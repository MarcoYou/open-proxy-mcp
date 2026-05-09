"""wiki link 방향 정책 lint.

WIKI_SCHEMA Section 0.2 트리 link 방향 정책 검증:
- 뿌리 → 줄기 → 큰가지: 단방향 (위→아래만)
- 큰가지 ↔ 가지 ↔ 잎: 양방향 강제
- 잎 ↔ 잎 / 잎 ↔ 낙엽: 자유

사용:
    python3 scripts/wiki_lint.py           # warning만 출력
    python3 scripts/wiki_lint.py --strict  # warning 있으면 exit 1 (CI / hook용)

/ship 통합:
    git diff 변경된 wiki/ 파일이 정책 위반시 ship 차단 가능.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WIKI = ROOT / "wiki"
EXCLUDE_DIRS = {"raw"}

WIKILINK = re.compile(r"\[\[([^\]\|]+)(?:\|[^\]]+)?\]\]")
# 인식 키: related, related_*, tools_audited (audit 페이지 관례)
LINK_KEYS = r"(?:related(?:_\w+)?|tools_audited)"
RELATED_BLOCK = re.compile(rf"^({LINK_KEYS}):\s*\n((?:\s*-\s*[^\n]+\n)+)", re.MULTILINE)
RELATED_INLINE = re.compile(rf"^({LINK_KEYS}):\s*\[([^\]]*)\]", re.MULTILINE)
REL_ENTRY = re.compile(r"-\s*([^\s\n]+)")
MD_LINK = re.compile(r"\]\(([^)]+\.md)\)")


def collect_pages() -> list[tuple[str, Path]]:
    pages = []
    for md in WIKI.rglob("*.md"):
        if any(p in EXCLUDE_DIRS for p in md.parts):
            continue
        rel = md.relative_to(WIKI).with_suffix("")
        pages.append((str(rel), md))
    return pages


def build_resolver(pages: list[tuple[str, Path]]):
    by_rel = {rel: rel for rel, _ in pages}
    by_basename = defaultdict(list)
    for rel, _ in pages:
        by_basename[rel.split("/")[-1]].append(rel)

    def resolve(target: str) -> str | None:
        target = target.strip().lstrip("/").rstrip("/")
        if target.endswith(".md"):
            target = target[:-3]
        if target in by_rel:
            return target
        base = target.split("/")[-1]
        candidates = by_basename.get(base, [])
        if len(candidates) == 1:
            return candidates[0]
        return None

    return resolve


def build_graph(pages):
    resolve = build_resolver(pages)
    outgoing = defaultdict(set)
    for rel, path in pages:
        text = path.read_text(encoding="utf-8", errors="ignore")

        for m in RELATED_BLOCK.finditer(text):
            for line in m.group(2).split("\n"):
                em = REL_ENTRY.search(line)
                if em:
                    r = resolve(em.group(1).strip())
                    if r and r != rel:
                        outgoing[rel].add(r)

        for m in RELATED_INLINE.finditer(text):
            items = m.group(2).strip()
            if not items:
                continue
            for entry in items.split(","):
                e = entry.strip()
                if not e:
                    continue
                r = resolve(e)
                if r and r != rel:
                    outgoing[rel].add(r)

        for link in WIKILINK.findall(text):
            r = resolve(link.split("|")[0].strip())
            if r and r != rel:
                outgoing[rel].add(r)

        for md_link in MD_LINK.findall(text):
            if "http" in md_link:
                continue
            t = md_link.lstrip("./").rstrip("/")
            if t.startswith("wiki/"):
                t = t[5:]
            if t.endswith(".md"):
                t = t[:-3]
            r = resolve(t)
            if r and r != rel:
                outgoing[rel].add(r)

    return outgoing


# 트리 layer 분류
def layer_of(rel: str) -> str:
    parts = rel.split("/")
    cat = parts[0]
    if cat == "rules":
        return "trunk"  # 줄기
    if cat == "tools":
        return "main_branch"
    if cat == "decisions":
        return "main_branch"
    if cat == "architecture":
        if len(parts) > 1 and parts[1] in ("audits", "fixes"):
            return "branch"
        return "main_branch"
    if cat == "lessons":
        return "branch"
    if cat == "ralph":
        return "branch"
    if cat == "archive":
        return "fallen_leaf"
    return "root_nav"  # index, log, WIKI_SCHEMA


# 단방향 검사: 줄기/뿌리에서 위로 link 금지
DOWNWARD_ONLY = {
    ("trunk", "main_branch"),
    ("trunk", "branch"),
    # archive (낙엽) → trunk OK (자유). archive → main_branch는 자유 (낙엽 ↔ 잎 자유)
}


# 양방향 강제 라인
BIDIRECTIONAL_PAIRS = [
    ("tools/", "architecture/audits/"),
    ("tools/", "architecture/fixes/"),
    ("decisions/", "lessons/"),
    ("decisions/", "ralph/"),
    ("decisions/", "architecture/audits/"),
    ("architecture/audits/", "ralph/"),
    ("architecture/audits/", "lessons/"),
    ("architecture/fixes/", "lessons/"),
]


def check_unidirectional(outgoing) -> list[str]:
    """줄기 → 큰가지/가지 link 위반 검출."""
    violations = []
    for src, targets in outgoing.items():
        src_layer = layer_of(src)
        if src_layer != "trunk":
            continue
        for tgt in targets:
            tgt_layer = layer_of(tgt)
            if tgt_layer in ("main_branch", "branch"):
                violations.append(f"줄기→가지 위반: {src} → {tgt}")
    return violations


def check_bidirectional(outgoing) -> list[str]:
    """큰가지 ↔ 가지 양방향 결손 검출."""
    issues = []
    for a_prefix, b_prefix in BIDIRECTIONAL_PAIRS:
        fwd = set()
        bwd = set()
        for src, targets in outgoing.items():
            if src.startswith(a_prefix):
                for t in targets:
                    if t.startswith(b_prefix):
                        fwd.add((src, t))
            if src.startswith(b_prefix):
                for t in targets:
                    if t.startswith(a_prefix):
                        bwd.add((t, src))
        only_fwd = fwd - bwd
        only_bwd = bwd - fwd
        for s, t in sorted(only_fwd):
            issues.append(f"양방향 결손 ({a_prefix} → {b_prefix}만): {s} → {t} (역방향 누락)")
        for s, t in sorted(only_bwd):
            issues.append(f"양방향 결손 ({b_prefix} → {a_prefix}만): {s} ← {t} (정방향 누락)")
    return issues


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--strict", action="store_true", help="위반 발견 시 exit 1")
    p.add_argument("--json", action="store_true", help="JSON 형식 출력")
    args = p.parse_args()

    pages = collect_pages()
    outgoing = build_graph(pages)

    uni_violations = check_unidirectional(outgoing)
    bi_issues = check_bidirectional(outgoing)

    if args.json:
        print(json.dumps({
            "total_pages": len(pages),
            "unidirectional_violations": uni_violations,
            "bidirectional_issues": bi_issues,
        }, ensure_ascii=False, indent=2))
    else:
        print(f"[wiki_lint] 총 페이지: {len(pages)}")
        print(f"\n[1] 단방향 위반 (줄기→가지/큰가지 금지): {len(uni_violations)} 건")
        for v in uni_violations[:20]:
            print(f"  ✗ {v}")
        if len(uni_violations) > 20:
            print(f"  ... +{len(uni_violations) - 20} 건")

        print(f"\n[2] 양방향 결손 (큰가지↔가지 한쪽만): {len(bi_issues)} 건")
        for v in bi_issues[:20]:
            print(f"  ⚠ {v}")
        if len(bi_issues) > 20:
            print(f"  ... +{len(bi_issues) - 20} 건")

        if not uni_violations and not bi_issues:
            print("\n✓ 모든 정책 충족")

    if args.strict and (uni_violations or bi_issues):
        sys.exit(1)


if __name__ == "__main__":
    main()
