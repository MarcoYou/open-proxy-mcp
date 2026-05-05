"""사내이사 performance 매트릭스 audit (ralph 260505_1611).

G1: classification 노출률 ≥99% (사내이사 + renewed 대상)
G2: 자본잠식/적자 special rule 정확도 100%
G3: bad/weak → AGAINST/REVIEW 분기 정확
G4: distribution 합리성 (good 20-40%, moderate 30-50%, weak 15-30%, bad 5-15%)

DART rate limit: rolling window cap 900/min (client.py 보호). 30 회사 batch + offset 단위 안전.

usage:
    python scripts/ralph_inside_director_performance_audit.py --universe kospi200 --sample 30 --offset 0
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


def _audit_one(payload: dict) -> dict:
    data = payload.get("data") or {}
    cands = data.get("candidates_evaluations") or []
    decisions = data.get("agenda_decisions") or []

    # 사내이사 + renewed 후보만 performance 평가 대상
    inside_renewed = [
        c for c in cands
        if "사내" in (c.get("role_type") or "")
        and (c.get("appointment_type") or {}).get("type") == "renewed"
    ]

    # G1: classification 노출
    perf_records = []
    for c in inside_renewed:
        perf = c.get("performance") or {}
        perf_records.append({
            "name": c.get("name"),
            "tenure": perf.get("tenure_period"),
            "classification": perf.get("classification"),
            "total_score": perf.get("total_score"),
            "has_matrix": bool(perf.get("matrix")),
            "has_rationale": bool(perf.get("rationale")),
            "capital_impairment_status": perf.get("capital_impairment_status"),
            "avg_net_income_krw": perf.get("avg_net_income_krw"),
        })

    # G3: 사내이사 결정 분기 (이사선임 안건의 reason에 'sender'/'성과' 키워드 등장 여부)
    director_decisions = [d for d in decisions if d.get("agenda_category") in ("director_election", "audit_committee_election")]
    decision_records = []
    for d in director_decisions:
        decision_records.append({
            "agenda_title": (d.get("agenda_title") or "")[:60],
            "decision": d.get("decision"),
            "reason": (d.get("reason") or "")[:120],
            "has_perf_in_reason": "성과" in (d.get("reason") or ""),
        })

    return {
        "n_candidates_total": len(cands),
        "n_inside_renewed": len(inside_renewed),
        "perf_records": perf_records,
        "n_director_decisions": len(director_decisions),
        "decision_records": decision_records,
    }


async def _run_one(ticker: str, name: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        t0 = time.time()
        try:
            payload = await asyncio.wait_for(
                build_proxy_advise_payload(name, year=2026, vote_style="open_proxy", scope="decisions"),
                timeout=180.0,
            )
            audit = _audit_one(payload)
            return {
                "ticker": ticker, "name": name,
                "status": payload.get("status"),
                "duration_s": round(time.time() - t0, 1),
                **audit,
            }
        except Exception as exc:
            return {
                "ticker": ticker, "name": name,
                "status": "exception",
                "error": f"{type(exc).__name__}: {exc}",
                "duration_s": round(time.time() - t0, 1),
                "n_candidates_total": 0, "n_inside_renewed": 0,
                "perf_records": [], "n_director_decisions": 0, "decision_records": [],
            }


def _summarize(results: list[dict]) -> dict:
    n_companies = len(results)
    n_ok = sum(1 for r in results if r["status"] not in ("exception",))
    n_inside_renewed = sum(r["n_inside_renewed"] for r in results)
    perf_records = []
    for r in results:
        perf_records.extend(r.get("perf_records", []))

    # G1 — classification 노출률
    n_with_classification = sum(1 for p in perf_records if p["classification"] not in (None, "n/a"))
    n_with_matrix = sum(1 for p in perf_records if p["has_matrix"])
    n_with_rationale = sum(1 for p in perf_records if p["has_rationale"])

    # G2 — special rule audit
    n_capital_impairment = sum(1 for p in perf_records if p.get("capital_impairment_status") == "full")
    n_loss = sum(1 for p in perf_records if (p.get("avg_net_income_krw") or 0) < 0)

    # G4 — distribution
    distribution = {"good": 0, "moderate": 0, "weak": 0, "bad": 0, "n/a": 0}
    for p in perf_records:
        cls = p["classification"] or "n/a"
        if cls in distribution:
            distribution[cls] += 1

    # G3 — 결정 분기 (사내이사 성과 reason에 등장 비율)
    decisions_with_perf = []
    for r in results:
        decisions_with_perf.extend(r.get("decision_records", []))
    n_decisions_with_perf_reason = sum(1 for d in decisions_with_perf if d["has_perf_in_reason"])

    g1_pct = round(n_with_classification / max(n_inside_renewed, 1) * 100, 2)

    return {
        "n_companies": n_companies,
        "n_ok": n_ok,
        "n_inside_renewed_total": n_inside_renewed,
        "g1_classification": {
            "n_with_classification": n_with_classification,
            "n_with_matrix": n_with_matrix,
            "n_with_rationale": n_with_rationale,
            "n_total": n_inside_renewed,
            "pct": g1_pct,
            "pass": g1_pct >= 99.0 if n_inside_renewed > 0 else None,
        },
        "g2_special_rule": {
            "n_capital_impairment": n_capital_impairment,
            "n_loss": n_loss,
            "note": "G2 정확도는 spot 검증 필요 (자본잠식 시 ROE/leverage avg=bad, 적자+환원 시 CSR=weak)",
        },
        "g3_decision_branch": {
            "n_director_decisions": len(decisions_with_perf),
            "n_with_perf_in_reason": n_decisions_with_perf_reason,
        },
        "g4_distribution": {
            **distribution,
            "good_pct": round(distribution["good"] / max(n_inside_renewed, 1) * 100, 1),
            "moderate_pct": round(distribution["moderate"] / max(n_inside_renewed, 1) * 100, 1),
            "weak_pct": round(distribution["weak"] / max(n_inside_renewed, 1) * 100, 1),
            "bad_pct": round(distribution["bad"] / max(n_inside_renewed, 1) * 100, 1),
        },
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", default="kospi200")
    parser.add_argument("--sample", type=int, default=30)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    universe = _load_universe(args.universe, args.sample, offset=args.offset)
    print(f"# inside-director performance audit: {len(universe)} companies, c={args.concurrency}", flush=True)
    clear_proxy_advise_cache()

    sem = asyncio.Semaphore(args.concurrency)
    tasks = [_run_one(t, n, sem) for t, n in universe]

    results = []
    for i, fut in enumerate(asyncio.as_completed(tasks), 1):
        r = await fut
        results.append(r)
        marker = "✓" if r["status"] not in ("exception",) else "✗"
        print(
            f"  [{i:>3}/{len(universe)}] {marker} {r['ticker']} {r['name']} "
            f"cand={r['n_candidates_total']} inside_renewed={r['n_inside_renewed']} ({r.get('duration_s', '?')}s)",
            flush=True,
        )

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
