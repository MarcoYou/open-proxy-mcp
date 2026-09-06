#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""집중투표 안건 표본 — 부모(묶음)·자식(후보) 관계 판정 전수 점검 + before/after diff. DART 0콜.

왜 이 스크립트인가 (2026-09-04 실측 고려아연 2026-09-09 임시주총):
  제2호 「집중투표의 방법으로 이사 4인 선임의 건」 부모는 4석을 읽어 ✅ 인데, 자식 후보 넷은
  「몇 명을 뽑는지 읽지 못했습니다 → 경합·⚠️」였다. 부모가 읽은 정원이 자식에 상속되지 않았다.
  고친 뒤 **같은 모양이 시장 표본 전체에서 어떻게 움직였는지** 세지 않으면 한 회사만 고친 것이다.

입력은 DART 응답 경계의 캐시(`get_document_cached` 가 디스크에 남긴 `{rcept_no}.json(.gz)`)만 쓴다
(CLAUDE.md 규칙 15 — 중간 함수 결과는 기준이 아니다). 네트워크는 쓰지 않는다.

측정 단위는 **집중투표 선거 한 건(부모 안건)** 이다. 부모마다 —
  · 부모 제목의 정원(seats_parent) · 집중투표 여부 · 자식 수 · 자식 제안 주체 분포
  · 자식에 붙은 관계 유형(contested / same_election / 없음) · 자식 링크의 seats · seats_source
  · 모순 플래그: 부모는 정원을 읽었는데 자식 링크는 seats=None (parent_known_child_unknown)

사용법
  # ① 고치기 전 코드로 baseline
  git stash / checkout main …; python3 scripts/cumulative_parent_child_check.py --out before.jsonl
  # ② 고친 코드로 재수집
  python3 scripts/cumulative_parent_child_check.py --out after.jsonl
  # ③ diff — 부모/자식 판정 일치율 슬라이스 + 과대교정(맞던 것이 틀려짐) 검사
  python3 scripts/cumulative_parent_child_check.py --diff before.jsonl after.jsonl

캐시 위치는 `OPM_DOC_CACHE_DIR`(운영 볼륨 /data/opm_cache) 또는 `--cache-dir`. 로컬 기본은
`$TMPDIR/opm_cache`. 이 단계는 **관계 판정까지**만 본다 — 최종 FOR/REVIEW 는 후보 평가·재무 등
다른 도구 결과가 필요해 MCP 호출(pilot)로 확인한다(CLAUDE.md 「검증은 MCP 호출」).
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from open_proxy_mcp.services.proxy_advise import _seat_count  # noqa: E402
from open_proxy_mcp.services.shareholder_meeting import (  # noqa: E402
    _agenda_nodes,
    build_agenda_relation_links,
)

try:  # before/after 를 같은 스크립트로 재려면 고치기 전 코드에서도 돌아야 한다
    from open_proxy_mcp.services.shareholder_meeting import _is_cumulative_title  # noqa: E402
except ImportError:  # pragma: no cover — 260904 이전 코드
    def _is_cumulative_title(title: str) -> bool:
        return "집중투표" in (title or "") or "누적투표" in (title or "")
from open_proxy_mcp.services.shareholder_meeting_parser import parse_agenda_xml  # noqa: E402

_ELECTION = ("director_election", "audit_committee_election")


def _cache_dir(arg: str | None) -> Path:
    return Path(arg or os.environ.get("OPM_DOC_CACHE_DIR")
                or os.path.join(tempfile.gettempdir(), "opm_cache"))


def _iter_docs(cache_dir: Path) -> Iterable[tuple[str, dict[str, Any]]]:
    """디스크 캐시를 **그대로** 읽는다 — client 를 거치지 않는다(mtime 갱신·메모리 적재 부작용 없음)."""
    for path in sorted(cache_dir.iterdir()):
        name = path.name
        if name.endswith(".json.gz"):
            rcept_no = name[:-len(".json.gz")]
            opener = lambda p: gzip.open(p, "rt", encoding="utf-8")  # noqa: E731
        elif name.endswith(".json"):
            rcept_no = name[:-len(".json")]
            opener = lambda p: open(p, "r", encoding="utf-8")  # noqa: E731
        else:
            continue
        if not rcept_no.isdigit():
            continue
        try:
            with opener(path) as f:
                doc = json.load(f)
        except Exception:
            continue
        if isinstance(doc, dict) and doc.get("text"):
            yield rcept_no, doc


def _flatten(nodes: list[dict[str, Any]], parent_number: str = "",
             parent_title: str = "") -> list[dict[str, Any]]:
    """proxy_advise._flatten_agenda_rows 와 같은 모양의 행 — 관계 판정 입력."""
    rows: list[dict[str, Any]] = []
    for n in nodes:
        rows.append({
            "number": n.get("number") or "", "title": n.get("title") or "",
            "category": n.get("category"), "proposer_type": n.get("proposer_type"),
            "parent_number": parent_number, "parent_title": parent_title,
            "agenda_relation_type": n.get("agenda_relation_type"),
            "agenda_relation_reasons": n.get("agenda_relation_reasons") or [],
            "conditional": n.get("conditional"),
        })
        rows.extend(_flatten(n.get("children") or [], n.get("number") or "", n.get("title") or ""))
    return rows


def _has_cumulative(nodes: list[dict[str, Any]]) -> bool:
    for n in nodes:
        if _is_cumulative_title(n.get("title") or ""):
            return True
        if "cumulative_voting_title" in (n.get("agenda_relation_reasons") or []):
            return True
        if _has_cumulative(n.get("children") or []):
            return True
    return False


def _records_for_doc(rcept_no: str, doc: dict[str, Any]) -> list[dict[str, Any]]:
    text = doc.get("text") or ""
    html = doc.get("html") or ""
    if "주주총회" not in text[:4000] and "소집" not in text[:4000]:
        return []
    try:
        items = parse_agenda_xml(text, html=html)
    except Exception as exc:  # 파서 예외는 표본에서 빼되 남긴다
        return [{"rcept_no": rcept_no, "error": f"parse:{type(exc).__name__}"}]
    if not items:
        return []
    nodes = _agenda_nodes(items)
    if not _has_cumulative(nodes):
        return []
    rows = _flatten(nodes)
    links = build_agenda_relation_links(rows, text)
    out: list[dict[str, Any]] = []
    for parent in rows:
        if parent["parent_number"] or parent.get("category") not in _ELECTION:
            continue
        kids = [r for r in rows if r["parent_number"] and r["parent_number"] == parent["number"]]
        if not kids:
            continue
        seats_parent = _seat_count(parent["title"])
        kid_types: Counter[str] = Counter()
        kid_seats: set[Any] = set()
        kid_sources: set[Any] = set()
        for k in kids:
            ls = [l for l in links.get(k["number"], []) if l.get("type") in ("contested", "same_election")]
            if not ls:
                kid_types["none"] += 1
                continue
            for l in ls:
                kid_types[l["type"]] += 1
                kid_seats.add(l.get("seats"))
                kid_sources.add(l.get("seats_source"))
        mix = Counter(k.get("proposer_type") or "unknown" for k in kids)
        linked = sum(v for t, v in kid_types.items() if t != "none")
        out.append({
            "rcept_no": rcept_no,
            "parent_number": parent["number"],
            "parent_title": parent["title"][:80],
            "cumulative_parent": _is_cumulative_title(parent["title"]),
            "seats_parent": seats_parent,
            "n_children": len(kids),
            "child_proposer_mix": dict(mix),
            "mixed_proposers": len(mix) > 1,
            "child_link_types": dict(kid_types),
            "child_link_seats": sorted((s if s is not None else -1) for s in kid_seats),
            "child_seats_source": sorted(str(s) for s in kid_sources),
            # 모순: 부모는 정원을 읽었는데 자식 링크는 seats 를 모른다
            "parent_known_child_unknown": bool(seats_parent) and linked > 0 and (None in kid_seats),
            # 자식 링크가 부모 정원과 다른 숫자를 들고 있다(공고 전체 검색이 다른 선거를 끌어옴)
            "parent_child_seat_mismatch": bool(seats_parent) and any(
                isinstance(s, int) and s != seats_parent for s in kid_seats),
        })
    return out


def collect(cache_dir: Path, out_path: Path) -> None:
    n_docs = n_rec = 0
    with out_path.open("w", encoding="utf-8") as f:
        for rcept_no, doc in _iter_docs(cache_dir):
            n_docs += 1
            for rec in _records_for_doc(rcept_no, doc):
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_rec += 1
    print(f"docs scanned={n_docs} · cumulative election parents={n_rec} → {out_path}")
    summarize(out_path)


def _load(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if "error" in rec:
            continue
        out[(rec["rcept_no"], rec["parent_number"])] = rec
    return out


def summarize(path: Path) -> None:
    recs = list(_load(path).values())
    if not recs:
        print("(표본 없음)")
        return
    known = [r for r in recs if r["seats_parent"]]
    mixed = [r for r in recs if r["mixed_proposers"]]
    contradict = [r for r in recs if r["parent_known_child_unknown"]]
    mismatch = [r for r in recs if r["parent_child_seat_mismatch"]]
    print(f"parents={len(recs)} · 부모 정원 읽힘={len(known)} · 자식 제안주체 혼합={len(mixed)}")
    print(f"  모순(부모 정원 읽힘·자식 seats 미상)={len(contradict)} · 부모/자식 정원 불일치={len(mismatch)}")
    if mixed:
        agree = sum(1 for r in mixed if r["seats_parent"] and not r["parent_known_child_unknown"]
                    and not r["parent_child_seat_mismatch"])
        print(f"  혼합 슬라이스 부모/자식 정원 일치율 = {agree}/{len(mixed)} ({100 * agree / len(mixed):.1f}%)")
    types: Counter[str] = Counter()
    for r in recs:
        types.update(r["child_link_types"])
    print(f"  자식 관계 유형 분포 = {dict(types)}")


def diff(before: Path, after: Path) -> int:
    b, a = _load(before), _load(after)
    keys = sorted(set(b) | set(a))
    fixed = broken = changed = 0
    for k in keys:
        rb, ra = b.get(k), a.get(k)
        if rb is None or ra is None:
            print(f"  only-in-{'after' if rb is None else 'before'}: {k}")
            continue
        if rb["child_link_types"] == ra["child_link_types"] and rb["child_link_seats"] == ra["child_link_seats"]:
            continue
        changed += 1
        was_bad = rb["parent_known_child_unknown"] or rb["parent_child_seat_mismatch"]
        is_bad = ra["parent_known_child_unknown"] or ra["parent_child_seat_mismatch"]
        tag = "FIXED" if was_bad and not is_bad else "BROKEN" if not was_bad and is_bad else "CHANGED"
        fixed += tag == "FIXED"
        broken += tag == "BROKEN"
        print(f"  {tag} {k[0]} {k[1]} 「{ra['parent_title'][:40]}」 seats_parent={ra['seats_parent']} "
              f"types {rb['child_link_types']}→{ra['child_link_types']} "
              f"seats {rb['child_link_seats']}→{ra['child_link_seats']}")
    print(f"changed={changed} · fixed={fixed} · broken(과대교정)={broken} · total={len(keys)}")
    print("--- after 요약"); summarize(after)
    return 1 if broken else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--out", default=None, help="수집 결과 jsonl")
    ap.add_argument("--diff", nargs=2, metavar=("BEFORE", "AFTER"))
    ap.add_argument("--summary", metavar="JSONL", help="수집본 요약만")
    args = ap.parse_args()
    if args.diff:
        return diff(Path(args.diff[0]), Path(args.diff[1]))
    if args.summary:
        summarize(Path(args.summary))
        return 0
    cache = _cache_dir(args.cache_dir)
    if not cache.is_dir():
        print(f"캐시 디렉터리 없음: {cache} — OPM_DOC_CACHE_DIR 또는 --cache-dir 지정", file=sys.stderr)
        return 2
    collect(cache, Path(args.out or "cumulative_parent_child.jsonl"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
