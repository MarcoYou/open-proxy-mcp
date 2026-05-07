"""classify_agenda audit 통합 분석 (300 회사).

DART 호출 없음 - 기존 audit JSON만 읽음.
"""

from __future__ import annotations
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUDIT_DIR = ROOT / "wiki/architecture/audits/data/260507_classify_agenda"


def main():
    records: list[dict] = []
    for p in sorted(AUDIT_DIR.glob("iter0*_*.json")):
        if "smoke" in p.name:
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        for r in data["records"]:
            r["_source_file"] = p.name
            records.append(r)
    print(f"loaded {len(records)} records from {len(list(AUDIT_DIR.glob('iter0*_*.json')))} batches")

    ok = [r for r in records if r.get("status") == "exact"]
    print(f"OK: {len(ok)} / total: {len(records)}")
    print()

    # 모든 agenda flat
    all_agendas: list[dict] = []
    for r in ok:
        for a in r.get("agendas") or []:
            a["_company"] = r["name"]
            a["_ticker"] = r["ticker"]
            all_agendas.append(a)

    print(f"전체 agenda 수: {len(all_agendas)}")
    print()

    # 카테고리 분포
    cat_dist = Counter(a.get("category") for a in all_agendas)
    print("=== 전체 카테고리 분포 ===")
    for cat, n in cat_dist.most_common():
        print(f"  {cat:30} {n:>5}")
    print()

    # mismatch (parent에 정관 있는데 articles_amendment 아님)
    mismatch = [a for a in all_agendas if a.get("mismatch")]
    print(f"=== Mismatch: {len(mismatch)}건 (parent에 정관 있는데 articles_amendment 아님) ===")
    cat_mismatch = Counter(a.get("category") for a in mismatch)
    for cat, n in cat_mismatch.most_common():
        print(f"  {cat:30} {n:>5}")
    print()

    # mismatch sample (각 카테고리별)
    by_cat = defaultdict(list)
    for a in mismatch:
        by_cat[a.get("category")].append(a)

    for cat in sorted(by_cat.keys()):
        print(f"-- {cat} sample --")
        for a in by_cat[cat][:5]:
            print(f"  {a.get('_ticker')} {a.get('_company'):15} parent='{a.get('parent_title')[:35]}' / title='{a.get('title')[:55]}'")
        print()

    # G1: 분류 오류 비율
    n_total = len(all_agendas)
    n_mismatch = len(mismatch)
    print(f"=== G1: 분류 오류 비율 ===")
    print(f"  mismatch / total = {n_mismatch} / {n_total} = {n_mismatch * 100 / n_total:.2f}%")

    # G2: 정관변경 sub-안건 정확도
    sub_under_정관 = [a for a in all_agendas if "정관" in (a.get("parent_title") or "") and "정관" not in (a.get("title") or "")]
    correct = [a for a in sub_under_정관 if a.get("category") == "articles_amendment"]
    print()
    print(f"=== G2: 정관변경 sub-안건 정확도 ===")
    print(f"  정관 sub-안건: {len(sub_under_정관)}, 올바른 articles_amendment: {len(correct)}")
    print(f"  정확도: {len(correct) * 100 / len(sub_under_정관):.2f}% (목표 100%)")

    out_path = AUDIT_DIR / "phase1_aggregate.json"
    out_path.write_text(json.dumps({
        "n_records": len(records),
        "n_ok": len(ok),
        "n_total_agendas": n_total,
        "n_mismatch": n_mismatch,
        "mismatch_pct": round(n_mismatch * 100 / n_total, 2),
        "category_distribution": dict(cat_dist),
        "mismatch_by_category": dict(cat_mismatch),
        "mismatch_sample_per_category": {
            cat: [{"ticker": a["_ticker"], "company": a["_company"], "parent": a.get("parent_title"), "title": a.get("title")} for a in entries[:10]]
            for cat, entries in by_cat.items()
        },
        "sub_under_정관": len(sub_under_정관),
        "correctly_classified": len(correct),
        "g2_accuracy_pct": round(len(correct) * 100 / len(sub_under_정관), 2) if sub_under_정관 else 0,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[wrote] {out_path}")


if __name__ == "__main__":
    main()
