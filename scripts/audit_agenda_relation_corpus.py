"""Audit agenda relation patterns in the local agenda relation corpus.

Reads only local JSON files produced by build_agenda_relation_corpus.py.

Usage:
    uv run python scripts/audit_agenda_relation_corpus.py
"""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CORPUS_DIR = ROOT / "wiki/architecture/audits/data/260524_agenda_relation_corpus"
OUT_DIR = ROOT / "wiki/architecture/audits/data/260524_agenda_relation_corpus/relation_audit"

PROCEDURAL_PATTERNS = (
    "선임할 이사의 수",
    "선임할 이사 수",
    "이사의 수 결정",
    "집중투표에 의하여 선임할",
    "집중투표에 의한 이사 선임",
)
CONDITIONAL_PATTERNS = (
    "승인 시",
    "승인시",
    "가결 시",
    "가결시",
    "부결 시",
    "부결시",
    "통과 시",
    "통과시",
    "조건부",
    "선행",
)
ALTERNATIVE_PATTERNS = (
    "대안",
    "택일",
    "둘 중",
    "상호배타",
    "5인 선임",
    "6인 선임",
)
SHAREHOLDER_PROPOSAL_PATTERNS = (
    "주주제안",
    "주주 제안",
)
CUMULATIVE_PATTERNS = (
    "집중투표",
    "누적투표",
)


def _load_manifest() -> dict[str, Any]:
    return json.loads((CORPUS_DIR / "manifest.json").read_text(encoding="utf-8"))


def _flatten_agendas(items: list[dict[str, Any]], parent: str = "", depth: int = 0) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items or []:
        row = {k: item.get(k) for k in (
            "agenda_id",
            "number",
            "title",
            "source",
            "proposer_type",
            "conditional",
            "agenda_relation_type",
            "agenda_relation_reasons",
        )}
        row["parent_title"] = parent
        row["depth"] = depth
        out.append(row)
        out.extend(_flatten_agendas(item.get("children") or [], item.get("title") or "", depth + 1))
    return out


def _has_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(p in text for p in patterns)


def _snippets(text: str, patterns: tuple[str, ...], window: int = 90, limit: int = 5) -> list[str]:
    hits: list[str] = []
    for pat in patterns:
        for match in re.finditer(re.escape(pat), text):
            start = max(0, match.start() - window)
            end = min(len(text), match.end() + window)
            snippet = re.sub(r"\s+", " ", text[start:end]).strip()
            if snippet and snippet not in hits:
                hits.append(snippet)
            if len(hits) >= limit:
                return hits
    return hits


def _classify_relation(row: dict[str, Any], document_text: str) -> tuple[str, list[str]]:
    title = row.get("title") or ""
    source = row.get("source") or ""
    conditional = row.get("conditional") or ""
    haystack = " ".join([title, source, conditional])
    reasons: list[str] = []

    if _has_any(haystack, PROCEDURAL_PATTERNS):
        reasons.append("procedural_title")
    if _has_any(haystack, CONDITIONAL_PATTERNS):
        reasons.append("conditional_title")
    if _has_any(haystack, ALTERNATIVE_PATTERNS):
        reasons.append("alternative_title")
    if _has_any(haystack, SHAREHOLDER_PROPOSAL_PATTERNS):
        reasons.append("shareholder_proposal_source")
    if _has_any(haystack, CUMULATIVE_PATTERNS):
        reasons.append("cumulative_voting_title")

    if reasons:
        if "procedural_title" in reasons:
            return "procedural", reasons
        if "conditional_title" in reasons:
            return "conditional", reasons
        if "alternative_title" in reasons:
            return "alternative", reasons
        if "shareholder_proposal_source" in reasons:
            return "shareholder_proposal", reasons
        return "cumulative_related", reasons

    # Document-level condition wording near the agenda id/title suggests the
    # parser should expose condition_text even if agenda.title is clean.
    if title and title in document_text:
        idx = document_text.find(title)
        area = document_text[max(0, idx - 250): min(len(document_text), idx + 900)]
        if _has_any(area, CONDITIONAL_PATTERNS):
            return "conditional_context", ["conditional_near_title"]
        if _has_any(area, ALTERNATIVE_PATTERNS):
            return "alternative_context", ["alternative_near_title"]

    return "normal", []


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest()
    rows_out: list[dict[str, Any]] = []
    company_summaries: list[dict[str, Any]] = []
    relation_counter: Counter[str] = Counter()
    bucket_counter: dict[str, Counter[str]] = defaultdict(Counter)

    for sample in manifest["samples"]:
        if sample.get("status") != "ok":
            continue
        doc = json.loads((ROOT / sample["path"]).read_text(encoding="utf-8"))
        parsed = doc.get("parsed") or {}
        agendas = _flatten_agendas(parsed.get("agendas") or [])
        text = doc.get("text") or ""
        company_counts: Counter[str] = Counter()
        company_rows: list[dict[str, Any]] = []
        for agenda in agendas:
            relation = agenda.get("agenda_relation_type") or ""
            reasons = agenda.get("agenda_relation_reasons") or []
            if not relation or relation == "normal":
                relation, reasons = _classify_relation(agenda, text)
            relation_counter[relation] += 1
            bucket_counter[sample["bucket"]][relation] += 1
            company_counts[relation] += 1
            if relation != "normal":
                snippets = []
                if relation in {"conditional_context", "alternative_context"}:
                    snippets = _snippets(text, CONDITIONAL_PATTERNS + ALTERNATIVE_PATTERNS, limit=3)
                row = {
                    "company": sample["company"],
                    "ticker": sample["ticker"],
                    "bucket": sample["bucket"],
                    "rcept_no": sample["rcept_no"],
                    **agenda,
                    "relation_type_candidate": relation,
                    "reasons": reasons,
                    "snippets": snippets,
                }
                rows_out.append(row)
                company_rows.append(row)
        company_summaries.append({
            "company": sample["company"],
            "ticker": sample["ticker"],
            "bucket": sample["bucket"],
            "rcept_no": sample["rcept_no"],
            "agenda_count": len(agendas),
            "relation_counts": dict(company_counts),
            "non_normal_count": sum(v for k, v in company_counts.items() if k != "normal"),
            "notable": company_rows[:8],
        })

    result = {
        "corpus": str(CORPUS_DIR.relative_to(ROOT)),
        "documents": len([s for s in manifest["samples"] if s.get("status") == "ok"]),
        "agenda_nodes": sum(sum(v for v in c["relation_counts"].values()) for c in company_summaries),
        "relation_distribution": dict(relation_counter),
        "bucket_relation_distribution": {k: dict(v) for k, v in bucket_counter.items()},
        "non_normal_agendas": rows_out,
        "companies": company_summaries,
    }
    json_path = OUT_DIR / "relation_scan.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    md_path = OUT_DIR / "relation_scan.md"
    md_path.write_text(_render_markdown(result), encoding="utf-8")

    print("json", json_path)
    print("md", md_path)
    print("documents", result["documents"])
    print("agenda_nodes", result["agenda_nodes"])
    print("relation_distribution", result["relation_distribution"])


def _render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "---",
        "type: audit",
        "title: Agenda relation corpus scan",
        "date: 2026-05-24",
        "---",
        "",
        "# Agenda Relation Corpus Scan",
        "",
        "Local-only scan over the 50-document agenda relation corpus.",
        "",
        "## Distribution",
        "",
        "| relation | count |",
        "|---|---:|",
    ]
    for key, value in sorted(result["relation_distribution"].items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {key} | {value} |")
    lines.extend([
        "",
        "## Bucket Distribution",
        "",
        "| bucket | relation | count |",
        "|---|---|---:|",
    ])
    for bucket, counter in sorted(result["bucket_relation_distribution"].items()):
        for key, value in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"| {bucket} | {key} | {value} |")
    lines.extend([
        "",
        "## Notable Non-Normal Agenda Candidates",
        "",
        "| company | bucket | relation | agenda | reason |",
        "|---|---|---|---|---|",
    ])
    for row in result["non_normal_agendas"][:80]:
        title = (row.get("title") or "").replace("|", "/")
        reason = ", ".join(row.get("reasons") or [])
        lines.append(
            f"| {row.get('company')} | {row.get('bucket')} | "
            f"{row.get('relation_type_candidate')} | {title} | {reason} |"
        )
    lines.extend([
        "",
        "## Companies With Relation Candidates",
        "",
        "| company | bucket | agendas | non-normal | relation_counts |",
        "|---|---|---:|---:|---|",
    ])
    for company in sorted(result["companies"], key=lambda r: (-r["non_normal_count"], r["company"])):
        if not company["non_normal_count"]:
            continue
        lines.append(
            f"| {company['company']} | {company['bucket']} | {company['agenda_count']} | "
            f"{company['non_normal_count']} | `{company['relation_counts']}` |"
        )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
