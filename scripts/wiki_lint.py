"""wiki link 방향 정책 lint.

wiki_schema Section 0.2 트리 link 방향 정책 + README 인덱스 동기화 검증:
- [1] 뿌리 → 줄기 → 큰가지: 단방향 (위→아래만)
- [2] 큰가지 ↔ 가지 ↔ 잎: 양방향 강제 / 잎 ↔ 잎: 자유
- [3] 폴더 README ← 직속 .md 전부 인덱스([[]] link) — 새 파일 추가하고 README 누락 시 실패
- [4] wiki_index.md 카운트 검증 — 폴더 주석 헤더 `(N) - `folder/`` 주장을 파일시스템 실측과 대조.
- [5] 경로 오링크 — `[[a/b/c]]`처럼 경로를 명시한 wikilink가 실제 위치와 다르면 검출.
- [9] 없는 문서 참조 — 문서형 참조가 가리키는 문서가 실제로 없으면 검출. 개념 링크는 면제.
- [10] 빈 스텁 — 링크만 걷어내고 라벨·불릿만 남은 자리.
- [8] 규칙 이중장부 방지 (260712 패널 결정) — 규칙 SSOT는 wiki_schema.md. wiki_index.md는
  인벤토리/라우팅만. 명명 규칙·카테고리 정의·frontmatter schema 서술이 index에 재등장하면 실패
  (lint 안 받는 두 번째 사본 = confident-wrong drift 원천 차단).

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
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
WIKI = ROOT / "wiki"
# raw = 외부 원본. corpus = legalize-kr 법령 원문(law_lookup, sync_law_corpus.py가 vendored·기계생성)
# — 둘 다 wiki 프로즈가 아니라 외부 원본이라 link/카운트/orphan 검사 대상에서 제외.
EXCLUDE_DIRS = {"raw", "corpus"}

WIKILINK = re.compile(r"\[\[([^\]\|]+)(?:\|[^\]]+)?\]\]")
# 인식 키: related, related_*, tools_audited (audit 페이지 관례)
LINK_KEYS = r"(?:related(?:_\w+)?|tools_audited)"
RELATED_BLOCK = re.compile(rf"^({LINK_KEYS}):\s*\n((?:\s*-\s*[^\n]+\n)+)", re.MULTILINE)
RELATED_INLINE = re.compile(rf"^({LINK_KEYS}):\s*\[([^\]]*)\]", re.MULTILINE)
REL_ENTRY = re.compile(r"-\s*([^\s\n]+)")
MD_LINK = re.compile(r"\]\(([^)]+\.md)\)")
ANY_MD_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+['\"][^)]*)?\)")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}


def _git_tracked_wiki() -> set[str] | None:
    """git-tracked wiki md 집합 (wiki/ 기준 상대경로). git 불가 시 None → 파일시스템 폴백.

    260709 CI 실패 원인: gitignore된 로컬 전용 파일(devlog.md·_local/)을 로컬 lint는 세고
    CI 체크아웃엔 없어 [4] 카운트가 어긋남. tracked 집합으로 세면 로컬==CI 항상 일치.
    """
    import subprocess
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "wiki/*.md", "wiki/**/*.md"],
                             capture_output=True, timeout=10)
        if out.returncode != 0:
            return None
        files = out.stdout.decode("utf-8", errors="ignore").split("\0")
        return {f[len("wiki/"):] for f in files if f.startswith("wiki/") and f.endswith(".md")}
    except Exception:  # noqa: BLE001 — git 없는 환경은 파일시스템 폴백
        return None


def collect_pages() -> list[tuple[str, Path]]:
    tracked = _git_tracked_wiki()
    pages = []
    for md in WIKI.rglob("*.md"):
        if any(p in EXCLUDE_DIRS for p in md.parts):
            continue
        # as_posix()로 forward-slash 정규화 — Windows(\)에서도 git ls-files(/)·폴더 카운트와 일치.
        rel_md = md.relative_to(WIKI).as_posix()
        if tracked is not None and rel_md not in tracked:
            continue  # gitignore된 로컬 전용 파일 — CI에 없으므로 세지 않음
        rel = md.relative_to(WIKI).with_suffix("").as_posix()
        pages.append((rel, md))
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
    if cat == "lessons":
        return "branch"
    if cat == "ralph":
        return "branch"
    return "root_nav"  # wiki_index, log, wiki_schema


# 단방향 검사: 줄기/뿌리에서 위로 link 금지
DOWNWARD_ONLY = {
    ("trunk", "main_branch"),
    ("trunk", "branch"),
}


# 양방향 강제 라인
BIDIRECTIONAL_PAIRS = [
    ("decisions/", "lessons/"),
    ("decisions/", "ralph/"),
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


# README 인덱스 drift: 폴더에 README가 있으면 그 폴더 직속 .md가 README에 링크돼야 함
README_DRIFT_EXCLUDE = ()  # 면제 폴더 없음


def check_readme_drift(pages, outgoing) -> list[str]:
    """폴더 README가 그 폴더 직속 비-README .md를 전부 인덱스(링크)하는지 검사.

    새 파일을 추가하고 README에 안 넣으면 여기서 잡힌다(폴더↔README 동기화 강제).
    """
    issues = []
    folder_files = defaultdict(list)
    readme_rels = {}
    for rel, _ in pages:
        parts = rel.split("/")
        folder = "/".join(parts[:-1])
        if parts[-1] == "README":
            readme_rels[folder] = rel
        else:
            folder_files[folder].append(rel)
    for folder, files in sorted(folder_files.items()):
        if not folder or any(folder == x or folder.startswith(x + "/") for x in README_DRIFT_EXCLUDE):
            continue
        readme = readme_rels.get(folder)
        if not readme:
            continue  # README 없는 폴더는 면제 (생성 정책은 별도)
        linked = outgoing.get(readme, set())
        for f in sorted(files):
            if f not in linked:
                issues.append(f"README 미인덱스: {folder}/README ← {f.split('/')[-1]}")
    return issues


# [4] wiki_index.md 카운트 검증 패턴
INDEX_MD = WIKI / "wiki_index.md"
# `### Concepts (44) - `rules/concepts/`` 류 — 폴더 주석이 붙은 헤더는 직속 파일 수와 대조
HEADER_FOLDER_COUNT = re.compile(r"^#{2,3} .*?\((\d+)\)[^\n]*?-\s*`([a-z_/]+)/`", re.MULTILINE)
TOTAL_CLAIM = re.compile(r"총 (\d+) markdown")
# tool 카테고리 라벨 — 같은 라벨이 다른 수로 두 번 나오면 자기모순
CATEGORY_LABEL = re.compile(r"(?:^#{2,3} |\*\*)(Company|Screening|Meeting|Data|Evidence|Action|Tools)\s*\((\d+)", re.MULTILINE)


def _direct_md_count(folder: str, pages) -> int:
    """폴더 직속 비-README 페이지 수 (-1 = 폴더 없음). pages 기반 — git-tracked만 센다(CI 정합)."""
    if not (WIKI / folder).is_dir():
        return -1
    n = 0
    for rel, _ in pages:
        parts = rel.rsplit("/", 1)
        if len(parts) == 2 and parts[0] == folder and parts[1] != "README":
            n += 1
    return n


def check_index_counts(pages) -> list[str]:
    """wiki_index.md의 수량 주장(헤더 카운트·총계·카테고리 라벨)을 실측과 대조."""
    if not INDEX_MD.exists():
        return []
    issues = []
    text = INDEX_MD.read_text(encoding="utf-8", errors="ignore")

    for m in HEADER_FOLDER_COUNT.finditer(text):
        claimed, folder = int(m.group(1)), m.group(2)
        actual = _direct_md_count(folder, pages)
        if actual >= 0 and claimed != actual:
            issues.append(f"index 카운트 불일치: `{folder}/` 주장 {claimed} vs 실측 {actual}")

    m = TOTAL_CLAIM.search(text)
    if m and int(m.group(1)) != len(pages):
        issues.append(f"index 총계 불일치: 주장 {m.group(1)} vs 실측 {len(pages)} (raw 제외 전체 md)")

    # 카테고리 라벨 자기모순 + 합계 vs Tools 총계
    by_label: dict[str, set[int]] = defaultdict(set)
    for m in CATEGORY_LABEL.finditer(text):
        by_label[m.group(1)].add(int(m.group(2)))
    for label, ns in sorted(by_label.items()):
        if len(ns) > 1:
            issues.append(f"index 자기모순 카운트: {label} {sorted(ns)} — 같은 라벨이 다른 수")
    cats = ("Company", "Screening", "Meeting", "Data", "Evidence", "Action")
    if all(len(by_label.get(c, set())) == 1 for c in cats) and len(by_label.get("Tools", set())) == 1:
        cat_sum = sum(next(iter(by_label[c])) for c in cats)
        tools_total = next(iter(by_label["Tools"]))
        if cat_sum != tools_total:
            issues.append(f"index 카테고리 합계 불일치: {'+'.join(cats)}={cat_sum} vs Tools({tools_total})")
    return issues


# [8] 규칙 이중장부 방지 — 규칙 SSOT는 wiki_schema.md, wiki_index.md는 인벤토리/라우팅만.
# 260712 패널 결정(Karpathy/Musk/Buffett/Stewardship): 규칙을 index에 복붙하면 lint 안 받는 두 번째
# 사본이 생겨 confident-wrong drift(260709 카운트 사고와 동종). 규칙 서술이 index에 재등장하면 CI 차단.
INDEX_RULE_LEAKS = [
    (re.compile(r"^#{2,4}\s*명명\s*규칙", re.MULTILINE), "명명 규칙 섹션 (SSOT=wiki_schema §2)"),
    (re.compile(r"yymmdd_hhmm_\{type\}"), "명명 패턴 리터럴 (SSOT=wiki_schema §2)"),
    (re.compile(r"\|\s*수정\s*가능\s*\|"), "카테고리 정의 표의 '수정 가능' 컬럼 (SSOT=wiki_schema §1)"),
    (re.compile(r"^#{2,4}\s*frontmatter\s+schema", re.MULTILINE | re.IGNORECASE), "frontmatter schema 표 (SSOT=wiki_schema §3)"),
]


def check_index_no_rules() -> list[str]:
    """wiki_index.md에 규칙 서술이 재등장하면 실패 — 규칙은 wiki_schema로 옮기고 링크만 남긴다."""
    if not INDEX_MD.exists():
        return []
    text = INDEX_MD.read_text(encoding="utf-8", errors="ignore")
    issues = []
    for rx, label in INDEX_RULE_LEAKS:
        if rx.search(text):
            issues.append(f"wiki_index.md에 규칙 서술 재등장: {label} — 규칙은 wiki_schema로 옮기고 링크만 남길 것")
    return issues


def check_path_links(pages) -> list[str]:
    """경로 명시 wikilink([[a/b/c]])의 명시 경로가 실제 파일 위치와 다르면 검출."""
    from posixpath import normpath

    by_rel = {rel for rel, _ in pages}
    by_basename = defaultdict(list)
    for rel in by_rel:
        by_basename[rel.split("/")[-1]].append(rel)
    issues = []
    for rel, path in pages:
        text = path.read_text(encoding="utf-8", errors="ignore")
        parent = "/".join(rel.split("/")[:-1])
        for link in WIKILINK.findall(text):
            t = link.split("#")[0].strip()
            if "/" not in t or t.endswith("/"):
                continue  # 경로 없는 링크·폴더 링크는 대상 아님
            tt = t[:-3] if t.endswith(".md") else t
            if tt.lstrip("./") in by_rel:
                continue  # wiki 루트 기준 경로 일치
            # 현재 파일 폴더 기준 상대경로(Obsidian 스타일 `audits/README`·`../x`)도 정당
            joined = normpath(f"{parent}/{tt}") if parent else normpath(tt)
            if joined in by_rel:
                continue
            cands = by_basename.get(tt.split("/")[-1], [])
            if cands:
                where = cands[0] if len(cands) == 1 else f"{len(cands)}곳 (예: {', '.join(cands[:3])} …)"
                issues.append(f"경로 오링크: {rel} → [[{t}]] (실제: {where})")
    return issues


# [9] 문서 참조가 실제로 있나 — 문서를 옮기거나 지우면 남는 죽은 참조·빈 자리를 잡는다.
#: 「가리키는 문서가 없다」는 [5](경로가 틀렸다)와 다른 결함이다. 260806 이관에서 문서 20개를
#: 옮겼을 때 죽은 참조 13곳과 링크 뜯긴 빈 자리 7곳이 남았는데 8축 전부 통과했다.
#: 개념 링크(`[[PBR]]`·`[[이사회]]`)는 앞으로 쓸 자리로 일부러 비워 둔 것이라 대상이 아니다 —
#: **문서형**(경로가 있거나 `yymmdd_` 로 시작하거나 문서 종류 접미가 붙은 것)만 본다.
DOCLIKE = re.compile(r"^\d{6}_|_(?:audit|fix|ralph|decision|improvement)|_(?:goal|spec|checklist)$")
FRONTMATTER_REF_KEYS = ("related_audits", "related_decisions", "related", "depends")
#: 링크를 걷어내고 라벨만 남은 자리. 다음 줄에 하위 항목·표·인용·이미지가 붙으면 정상 문법이다.
EMPTY_STUB = re.compile(r"(?m)^([ \t]*)[-*][ \t]+([^\n:]{1,40}):[ \t]*$")
EMPTY_BULLET = re.compile(r"(?m)^[ \t]*[-*][ \t]*$")


def _is_doclike(target: str) -> bool:
    base = target.split("/")[-1]
    if target.endswith("/") or "..." in target or " " in base:
        return False  # 폴더 참조·플레이스홀더·산문
    return "/" in target or bool(DOCLIKE.search(base))


def check_missing_doc_refs(pages) -> list[str]:
    """문서형 참조가 가리키는 문서가 실제로 있는지."""
    known = {rel.split("/")[-1] for rel, _ in pages}
    issues = []
    for rel, path in pages:
        text = path.read_text(encoding="utf-8", errors="ignore")
        targets = [m.split("#")[0].strip() for m in WIKILINK.findall(text)]
        head = re.match(r"(?s)^---\n(.*?)\n---", text)
        if head:
            for key in FRONTMATTER_REF_KEYS:
                found = re.search(rf"(?m)^{key}:\s*\[(.*?)\]", head.group(1), re.S)
                if found:
                    targets += [x.strip() for x in found.group(1).split(",") if x.strip()]
        for target in targets:
            if not target or target.split("/")[-1] in known or not _is_doclike(target):
                continue
            issues.append(f"없는 문서 참조: {rel} → {target}")
    return issues


def _git_tracked_markdown() -> list[Path]:
    """Return every tracked Markdown file, including README/docs outside wiki."""
    import subprocess
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z", "*.md"],
            capture_output=True, timeout=10, check=True,
        )
        return [p for item in out.stdout.decode().split("\0") if item and (p := ROOT / item).exists()]
    except (OSError, subprocess.SubprocessError):
        return list(ROOT.rglob("*.md"))


def check_repo_links() -> list[str]:
    """Check relative Markdown/image links across the repository."""
    issues: list[str] = []
    for path in _git_tracked_markdown():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for raw in ANY_MD_LINK.findall(text):
            target = unquote(raw.strip().split("#", 1)[0].split("?", 1)[0])
            if not target or target in {"name.md", "상대경로.md", "상대경로/page.md"} or target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            rel = path.relative_to(ROOT).as_posix()
            candidate = (path.parent / target).resolve() if not target.startswith("/") else Path(target)
            try:
                candidate.relative_to(ROOT)
            except ValueError:
                continue
            if candidate.exists():
                continue
            if candidate.suffix.lower() in IMAGE_EXTENSIONS or target.endswith(".md") or "/" in target:
                issues.append(f"없는 상대 링크: {rel} → {raw}")
    return sorted(set(issues))


def check_tool_updated_fields() -> list[str]:
    """Every tool page carries an auditable last-updated date."""
    issues = []
    date_re = re.compile(r"^updated:\s*20\d\d-\d\d-\d\d\s*$", re.MULTILINE)
    for path in sorted((WIKI / "tools").glob("*.md")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "type: tool" in text.split("---", 2)[0:2][-1] and not date_re.search(text):
            issues.append(f"tool updated 누락/형식 오류: {path.relative_to(ROOT)}")
    return issues


def check_empty_stubs(pages) -> list[str]:
    """링크만 뜯겨 라벨·불릿만 남은 자리. lint 대상이 사라져 어떤 축에도 안 걸린다."""
    issues = []
    for rel, path in pages:
        lines = path.read_text(encoding="utf-8", errors="ignore").split("\n")
        for i, line in enumerate(lines):
            stub = EMPTY_STUB.match(line)
            if stub:
                indent = stub.group(1)
                # 라벨과 알맹이 사이에 빈 줄이 끼기도 한다 — 다음 «내용 있는» 줄을 본다.
                nxt = next((x for x in lines[i + 1:i + 4] if x.strip()), "")
                # 더 깊이 들여쓰였거나 표·코드·인용·이미지면 알맹이가 있는 것
                if nxt and (nxt.startswith(indent + " ") or nxt.lstrip()[:1] in "|>!`"):
                    continue
                issues.append(f"빈 스텁: {rel}:{i + 1} → `{line.strip()}`")
            elif EMPTY_BULLET.match(line):
                issues.append(f"빈 불릿: {rel}:{i + 1}")
    return issues


_DATE_RE = re.compile(r"20\d\d-\d\d-\d\d")
# 조문 core 추출: "제542조의7제3항"·"§542의7③" → (542, 7). 표시(law_reference)와 원본(article)을
# 같은 정규형으로 맞춰 대조 — 근거에 조문번호가 실제로 들어있는지 검사([7d]).
_ARTICLE_RE = re.compile(r"(?:제(\d+)조의(\d+)|§(\d+)의(\d+))")


def _article_cores(text: str) -> set[tuple[str, str]]:
    cores = set()
    for m in _ARTICLE_RE.finditer(str(text)):
        n = m.group(1) or m.group(3)
        s = m.group(2) or m.group(4)
        if n and s:
            cores.add((n, s))
    return cores


def check_law_dates(pages) -> list[str]:
    """[7] 상법 시행일 3자 정합 (260709 — 시행일 SSOT 도입).

    시행일은 law_provisions.json(원본)에만 두고, 사람이 읽는 md 표와 엔진 룰은 여기서
    파생·검증되게 한다. 같은 날짜를 여러 곳에 손으로 적어 한 곳만 고치면 조용히 어긋나던
    사고(이번 세션 A2-5: 엔진이 자사주 신주배정금지 룰을 3월이 아닌 9월부터 발화) 재발 방지.

    ⓐ md '시행 타임라인' 표가 원본과 일치 (생성기 --check 재사용).
    ⓑ 엔진 law_reference에 적힌 날짜가 해당 provision의 {통과·공포·시행}에 실재.
    ⓒ 엔진 applies_after(발화 게이트)가 해당 provision의 gate_dates에 속함.
    ⓓ 엔진 law_reference(proxy_advise가 띄우는 근거)에 원본 조문번호가 실재 — 근거의 조문 정확도 가드.
    """
    import subprocess

    issues: list[str] = []
    # 260814: 런타임이 읽는 규칙 데이터는 패키지로 옮겼다(경로 의존 제거). lint 도 그쪽을 본다.
    _DATA = WIKI.parent / "open_proxy_mcp" / "data" / "laws"
    reg_path = _DATA / "law_provisions.json"
    eng_path = _DATA / "law_layer_rules.json"
    gen_path = ROOT / "scripts" / "gen_law_timeline.py"

    if not reg_path.exists():
        return [f"시행일 원본 없음: {reg_path.relative_to(ROOT)}"]

    registry = json.loads(reg_path.read_text(encoding="utf-8"))
    prov_dates: dict[str, set[str]] = {}   # provision -> {통과·공포·시행}
    prov_gate: dict[str, set[str]] = {}    # provision -> gate_dates
    prov_eff: dict[str, str] = {}          # provision -> 시행일
    prov_prom: dict[str, str] = {}         # provision -> 공포일
    prov_article: dict[str, set[tuple[str, str]]] = {}  # provision -> {조문 core (NNN,MM)}
    for p in registry.get("provisions", []):
        pid = p["provision_id"]
        prov_dates[pid] = {
            d for d in (p.get("effective_date"), p.get("promulgation_date"), p.get("passed_date")) if d
        }
        prov_gate[pid] = set(p.get("gate_dates", []))
        prov_eff[pid] = p.get("effective_date")
        prov_prom[pid] = p.get("promulgation_date")
        prov_article[pid] = _article_cores(p.get("article", ""))

    # date_basis 면제: 개정 시행일 개념이 없는 룰 (법률 검증 260709).
    #   interpretation      = 법무부 유권해석 (A1-10 등), 개정 시행일 없음
    #   monitoring_baseline = 우회 선제감시용 임의 baseline (B1-8b 2024-01-01), 시행일 아님
    EXEMPT_BASIS = {"interpretation", "monitoring_baseline"}

    # ⓐ 표 ↔ 원본 (생성기 재사용)
    if gen_path.exists():
        r = subprocess.run(
            [sys.executable, str(gen_path), "--check"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            issues.append("[7a] " + (r.stderr or r.stdout).strip().splitlines()[-1])

    # ⓑ·ⓒ 엔진 룰 ↔ 원본
    if eng_path.exists():
        engine = json.loads(eng_path.read_text(encoding="utf-8"))
        for rule in engine.get("rules", []):
            rid = rule.get("id", "?")
            prov = rule.get("provision")
            if prov and prov not in prov_dates:
                issues.append(f"[7] 엔진 룰 {rid}: provision '{prov}'가 원본에 없음(phantom)")
                continue
            # ⓑ law_reference 날짜
            for d in set(_DATE_RE.findall(str(rule.get("law_reference", "")))):
                if prov:
                    if d not in prov_dates[prov]:
                        issues.append(
                            f"[7b] 엔진 룰 {rid} law_reference의 {d}가 원본 '{prov}' 날짜"
                            f"{sorted(prov_dates[prov])}에 없음")
                else:
                    known = set().union(*prov_dates.values()) if prov_dates else set()
                    if d not in known:
                        issues.append(
                            f"[7b] 엔진 룰 {rid} law_reference의 {d}가 원본 어디에도 없음")
            # ⓒ applies_after(발화 게이트) — layer별로 조인다 (법률 검증 260709).
            #   A2(위반): 강행규정은 시행 전 위반 불성립 → 반드시 시행일.
            #   A1(정합): 정관 선제 반영 조기 보상 허용 → 공포일 또는 시행일 (통과일 불허).
            #   그 외(C 등): gate_dates 집합 안.
            #   provision 없이 applies_after만 있으면 → date_basis 면제 명시가 없는 한 실패
            #   (SSOT 미연결 = 원본 날짜 바뀌면 조용히 어긋남 = 막으려는 사고. QA F1 260709).
            aa = (rule.get("applies_to") or {}).get("applies_after")
            basis = rule.get("date_basis")
            if aa and basis not in EXEMPT_BASIS:
                if not prov:
                    issues.append(
                        f"[7c] 엔진 룰 {rid} applies_after={aa}인데 provision도 date_basis도 없음 "
                        f"— SSOT 미연결(원본 날짜 변경 시 조용히 어긋남). provision 부여 또는 "
                        f"date_basis({'/'.join(sorted(EXEMPT_BASIS))}) 명시 필요")
                elif rid.startswith("A2") and aa != prov_eff[prov]:
                    issues.append(
                        f"[7c] 엔진 룰 {rid}(A2 위반)의 applies_after={aa}가 원본 '{prov}' "
                        f"시행일({prov_eff[prov]})과 다름 — 강행규정 위반은 시행일부터만 성립")
                elif rid.startswith("A1") and aa not in {prov_prom[prov], prov_eff[prov]}:
                    issues.append(
                        f"[7c] 엔진 룰 {rid}(A1 정합)의 applies_after={aa}가 원본 '{prov}' "
                        f"공포일({prov_prom[prov]})·시행일({prov_eff[prov]}) 어느 것도 아님 "
                        f"(통과일은 불허)")
                elif not rid.startswith(("A1", "A2")) and aa not in prov_gate[prov]:
                    issues.append(
                        f"[7c] 엔진 룰 {rid} applies_after={aa}가 원본 '{prov}' gate_dates"
                        f"{sorted(prov_gate[prov])}에 없음")
            # ⓓ 근거 표시(law_reference)에 원본 조문번호가 실제로 들어있는지 (Q1 정확도 가드).
            #   proxy_advise가 띄우는 근거가 "N차 개정"만이고 조문이 빠지면 여기서 실패 →
            #   조문 정확도가 조용히 퇴화하는 것을 막는다.
            if prov and prov_article.get(prov):
                lr_cores = _article_cores(rule.get("law_reference", ""))
                missing = prov_article[prov] - lr_cores
                if missing:
                    want = "·".join(f"제{n}조의{s}" for n, s in sorted(missing))
                    issues.append(
                        f"[7d] 엔진 룰 {rid} law_reference에 원본 조문 {want}가 없음 "
                        f"— 근거 표시에 정확한 조문번호를 넣어라(provision '{prov}')")
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
    drift_issues = check_readme_drift(pages, outgoing)
    index_issues = check_index_counts(pages)
    path_issues = check_path_links(pages)
    missing_ref_issues = check_missing_doc_refs(pages)
    stub_issues = check_empty_stubs(pages)
    law_date_issues = check_law_dates(pages)
    index_rule_leaks = check_index_no_rules()
    repo_link_issues = check_repo_links()
    updated_issues = check_tool_updated_fields()

    if args.json:
        print(json.dumps({
            "total_pages": len(pages),
            "unidirectional_violations": uni_violations,
            "bidirectional_issues": bi_issues,
            "readme_drift": drift_issues,
            "index_count_issues": index_issues,
            "path_link_issues": path_issues,
            "law_date_issues": law_date_issues,
            "index_rule_leaks": index_rule_leaks,
            "repo_link_issues": repo_link_issues,
            "updated_issues": updated_issues,
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

        print(f"\n[3] README 인덱스 누락 (폴더 .md가 해당 README에 없음): {len(drift_issues)} 건")
        for v in drift_issues[:20]:
            print(f"  ⚠ {v}")
        if len(drift_issues) > 20:
            print(f"  ... +{len(drift_issues) - 20} 건")

        print(f"\n[4] wiki_index.md 카운트 불일치 (주장 vs 실측): {len(index_issues)} 건")
        for v in index_issues[:20]:
            print(f"  ✗ {v}")
        if len(index_issues) > 20:
            print(f"  ... +{len(index_issues) - 20} 건")

        print(f"\n[5] 경로 오링크 (명시 경로 ≠ 실제 위치): {len(path_issues)} 건")
        for v in path_issues[:20]:
            print(f"  ✗ {v}")
        if len(path_issues) > 20:
            print(f"  ... +{len(path_issues) - 20} 건")

        print(f"\n[7] 상법 시행일 3자 정합 (원본↔md표↔엔진): {len(law_date_issues)} 건")
        for v in law_date_issues[:20]:
            print(f"  ✗ {v}")
        if len(law_date_issues) > 20:
            print(f"  ... +{len(law_date_issues) - 20} 건")

        print(f"\n[8] 규칙 이중장부 (wiki_index에 규칙 서술 재등장 — SSOT=wiki_schema): {len(index_rule_leaks)} 건")
        for v in index_rule_leaks[:20]:
            print(f"  ✗ {v}")

        print(f"\n[9] 없는 문서 참조 (문서를 옮기거나 지운 뒤 남은 죽은 링크): {len(missing_ref_issues)} 건")
        for v in missing_ref_issues[:20]:
            print(f"  ✗ {v}")
        if len(missing_ref_issues) > 20:
            print(f"  ... +{len(missing_ref_issues) - 20} 건")

        print(f"\n[10] 빈 스텁 (링크만 뜯겨 라벨·불릿만 남은 자리): {len(stub_issues)} 건")
        for v in stub_issues[:20]:
            print(f"  ✗ {v}")
        if len(stub_issues) > 20:
            print(f"  ... +{len(stub_issues) - 20} 건")

        print(f"\n[11] 저장소 전체 상대 링크 (README/docs 포함): {len(repo_link_issues)} 건")
        for v in repo_link_issues[:40]:
            print(f"  ✗ {v}")
        if len(repo_link_issues) > 40:
            print(f"  ... +{len(repo_link_issues) - 40} 건")

        print(f"\n[12] tool updated 필드: {len(updated_issues)} 건")
        for v in updated_issues[:20]:
            print(f"  ✗ {v}")

        if not (uni_violations or bi_issues or drift_issues or index_issues or path_issues or law_date_issues or index_rule_leaks or missing_ref_issues or stub_issues or repo_link_issues or updated_issues):
            print("\n✓ 모든 정책 충족")

    if args.strict and (uni_violations or bi_issues or drift_issues or index_issues or path_issues or law_date_issues or index_rule_leaks or missing_ref_issues or stub_issues or repo_link_issues or updated_issues):
        sys.exit(1)


if __name__ == "__main__":
    main()
