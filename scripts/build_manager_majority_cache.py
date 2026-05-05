"""Step 0.5 — 운용사 records aggregation cache (G3 검증용).

22 records → (company, agenda_title) → {manager: decision} dict 구축.
filter 카테고리: 보수한도 / 감사 보수한도 / 퇴직금.
"""
from __future__ import annotations
import json, glob
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path("/Users/marcoyou/Projects/open-proxy-mcp")
records_dir = ROOT / "open_proxy_mcp/data/asset_managers/records"


def vote_decision(v):
    f, a, ab = v.get("vote_for", 0), v.get("vote_against", 0), v.get("vote_abstain", 0)
    if f and not a and not ab: return "FOR"
    if a and not f: return "AGAINST"
    if ab and not f and not a: return "ABSTAIN"
    if f and a: return "SPLIT"
    return "OTHER"


def categorize(title: str) -> str | None:
    t = (title or "").strip()
    if "퇴직금" in t or "퇴임위로금" in t:
        return "retirement_pay"
    if ("감사" in t and "감사위원" not in t) and ("보수한도" in t or "보수액한도" in t or "보수의 한도" in t):
        return "audit_compensation"
    if "보수한도" in t or "보수액한도" in t or "보수의 한도" in t:
        return "director_compensation"
    return None


def main():
    files = sorted(glob.glob(str(records_dir / "*.json")))
    cache = defaultdict(lambda: defaultdict(dict))
    cnt = Counter()
    for fp in files:
        d = json.load(open(fp))
        mgr = d["manager_id"]
        for v in d.get("votes", []):
            cat = categorize(v.get("agenda_title", ""))
            if not cat:
                continue
            decision = vote_decision(v)
            key = (v.get("company"), v.get("agenda_title", "").strip())
            cache[cat][key][mgr] = decision
            cnt[(cat, decision)] += 1
    
    # Calc majority (4+ votes same direction)
    majority_index = {}
    for cat, agendas in cache.items():
        majority_index[cat] = {}
        for key, mgr_decisions in agendas.items():
            if len(mgr_decisions) < 4:
                continue
            decs = Counter(mgr_decisions.values())
            top, top_n = decs.most_common(1)[0]
            if top_n >= 4 and top in ("FOR", "AGAINST", "REVIEW"):
                majority_index[cat][f"{key[0]}|||{key[1]}"] = {
                    "majority": top, "votes": top_n, "total": len(mgr_decisions),
                    "all_votes": dict(mgr_decisions),
                }
    
    out = ROOT / "open_proxy_mcp/data/asset_managers/_majority_cache_compensation_retirement.json"
    out.write_text(json.dumps(majority_index, ensure_ascii=False, indent=2))
    
    print(f"# 22 records → cache built")
    print(f"# saved → {out}")
    for cat in ("director_compensation", "audit_compensation", "retirement_pay"):
        agendas = majority_index.get(cat, {})
        print(f"\n## {cat}: {len(agendas)} 4+ majority cases")
        majdist = Counter(v["majority"] for v in agendas.values())
        print(f"  distribution: {dict(majdist)}")


if __name__ == "__main__":
    main()
