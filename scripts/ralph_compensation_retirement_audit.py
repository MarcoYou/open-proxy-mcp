"""보수/퇴직 분기 ralph audit (260505).

KOSPI 50 + KOSDAQ 30 회사에서 보수/감사/퇴직 안건 추출 + 결정 분기 분포 + 운용사 majority 정합.

G1: 파싱 성공률 ≥99% (보수 + 퇴직)
G2: 이사/감사 분기 정확도 100%
G3: 운용사 4+ majority 정합도 ≥90%
G4: NPS 정책 정합 100%

DART rate limit: rolling cap 900/min. 30 회사 batch + sleep 30s.
"""

from __future__ import annotations
import argparse
import asyncio
import csv
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.services.proxy_advise import build_proxy_advise_payload, clear_proxy_advise_cache  # noqa: E402

CATEGORIES = ("director_compensation", "audit_compensation", "retirement_pay")

# articles_amendment 안에 hybrid 처리되는 case들 (reason prefix로 detect)
ARTICLES_HYBRID_REASONS = {
    "정관변경 (퇴직금)": "retirement_pay",
    "정관변경 (이사 보수한도)": "director_compensation",
    "정관변경 (감사 보수한도)": "audit_compensation",
}


def _classify_via_reason(decision_dict: dict) -> str | None:
    """decision의 effective category — articles_amendment hybrid 포함."""
    cat = decision_dict.get("agenda_category")
    if cat in CATEGORIES:
        return cat
    if cat == "articles_amendment":
        reason = decision_dict.get("reason", "") or ""
        for prefix, eff in ARTICLES_HYBRID_REASONS.items():
            if reason.startswith(prefix):
                return eff
    return None


def _load_universe(name: str, sample: int, offset: int = 0) -> list[tuple[str, str]]:
    if name == "kospi200":
        path = ROOT / "wiki/architecture/audits/data/260503_universe_200.csv"
    elif name == "kosdaq50":
        path = ROOT / "wiki/architecture/audits/data/260504_proxy_advise_framework/kosdaq_top50.csv"
    else:
        raise ValueError(name)
    rows = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append((row["ticker"], row["company"]))
    return rows[offset:offset + sample]


def _load_majority_cache() -> dict:
    path = ROOT / "open_proxy_mcp/data/asset_managers/_majority_cache_compensation_retirement.json"
    if not path.exists():
        return {}
    return json.load(path.open())


def _audit_one(company: str, payload: dict, majority_cache: dict) -> dict:
    data = payload.get("data") or {}
    decisions = data.get("agenda_decisions") or []

    relevant = []
    for d in decisions:
        eff_cat = _classify_via_reason(d)
        if not eff_cat:
            continue
        title = (d.get("agenda_title") or "").strip()
        decision = d.get("decision")
        cat = eff_cat
        original_cat = d.get("agenda_category")
        # 운용사 majority 매칭
        majority_info = None
        cat_cache = majority_cache.get(cat, {})
        # exact match key
        for key, info in cat_cache.items():
            if key.startswith(f"{company}|||") and key.split("|||", 1)[1].strip() == title:
                majority_info = info
                break
        relevant.append({
            "category": cat,
            "original_category": original_cat,
            "title": title,
            "decision": decision,
            "reason": (d.get("reason") or "")[:200],
            "majority": majority_info,
        })
    return relevant


async def _run_one(ticker: str, name: str, sem: asyncio.Semaphore, majority_cache: dict) -> dict:
    async with sem:
        t0 = time.time()
        try:
            payload = await asyncio.wait_for(
                build_proxy_advise_payload(name, year=2026, vote_style="open_proxy", scope="decisions"),
                timeout=180.0,
            )
            relevant = _audit_one(name, payload, majority_cache)
            return {
                "ticker": ticker, "name": name,
                "status": payload.get("status"),
                "duration_s": round(time.time() - t0, 1),
                "relevant_decisions": relevant,
            }
        except Exception as exc:
            return {
                "ticker": ticker, "name": name,
                "status": "exception",
                "error": f"{type(exc).__name__}: {exc}",
                "duration_s": round(time.time() - t0, 1),
                "relevant_decisions": [],
            }


def _summarize(results: list[dict]) -> dict:
    n_companies = len(results)
    n_ok = sum(1 for r in results if r["status"] not in ("exception",))

    # decisions per category
    per_cat = {c: [] for c in CATEGORIES}
    for r in results:
        for d in r.get("relevant_decisions", []):
            per_cat[d["category"]].append({**d, "_company": r["name"]})

    summary = {"n_companies": n_companies, "n_ok": n_ok, "categories": {}}
    for cat, items in per_cat.items():
        from collections import Counter
        dist = Counter(d["decision"] for d in items)
        # G3: 운용사 majority 정합
        n_with_majority = sum(1 for d in items if d.get("majority"))
        n_match = 0
        mismatches = []
        for d in items:
            mj = d.get("majority")
            if not mj:
                continue
            if d["decision"] == mj["majority"]:
                n_match += 1
            else:
                mismatches.append({
                    "company": d["_company"], "title": d["title"][:50],
                    "opm_decision": d["decision"], "majority": mj["majority"],
                    "all_votes": mj["all_votes"],
                })
        match_pct = (n_match / max(n_with_majority, 1) * 100) if n_with_majority else None
        summary["categories"][cat] = {
            "n_decisions": len(items),
            "distribution": dict(dist),
            "n_with_4plus_majority": n_with_majority,
            "n_match_majority": n_match,
            "match_pct": round(match_pct, 1) if match_pct is not None else None,
            "mismatches": mismatches[:10],
        }
    return summary


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", default="kospi200")
    parser.add_argument("--sample", type=int, default=30)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    universe = _load_universe(args.universe, args.sample, offset=args.offset)
    majority_cache = _load_majority_cache()
    print(f"# comp/retire audit: {args.universe} {len(universe)} companies, c={args.concurrency}", flush=True)
    clear_proxy_advise_cache()

    sem = asyncio.Semaphore(args.concurrency)
    tasks = [_run_one(t, n, sem, majority_cache) for t, n in universe]
    results = []
    for i, fut in enumerate(asyncio.as_completed(tasks), 1):
        r = await fut
        results.append(r)
        marker = "✓" if r["status"] not in ("exception",) else "✗"
        n_rel = len(r.get("relevant_decisions", []))
        print(f"  [{i:>3}/{len(universe)}] {marker} {r['ticker']} {r['name']} relevant={n_rel} ({r.get('duration_s', '?')}s)", flush=True)

    summary = _summarize(results)
    print("\n# SUMMARY")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            json.dump({"summary": summary, "results": results}, f, ensure_ascii=False, indent=2)
        print(f"\n# saved → {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
