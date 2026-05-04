"""ralph framework audit harness.

KOSPI200 + KOSDAQ top50 표본 측정 — proxy_advise framework dimension 노출률.

G1. Framework 4 dimension 노출률 (per candidate)
- 결격사유 / 독립성 / 전문성 / 과거 행적

G2. NO_DATA false-positive 비율 (per agenda)
- decision == "NO_DATA" 인 안건 중 실제로 facts 추출 가능했던 비율 (manual review or proxy 측정)

G3. 신임/연임 auto detect 정확도 (per candidate, future)
- appointment_type 필드 추가 후 측정

G4. 1번 안건 FY 본문 raw 추출 (per company, future)
- agenda[0] (재무제표 승인) 본문에서 FY raw 추출 성공 비율

usage:
    python scripts/ralph_framework_audit.py --universe kospi200 --sample 50 --year 2026
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


def _load_universe(name: str, sample: int) -> list[tuple[str, str]]:
    """(ticker, company_name) 리스트."""
    if name == "kospi200":
        path = ROOT / "wiki/architecture/audits/data/260503_universe_200.csv"
    elif name == "kosdaq50":
        path = ROOT / "wiki/architecture/audits/data/260504_proxy_advise_framework/kosdaq_top50.csv"
    else:
        raise ValueError(f"unknown universe: {name}")
    rows: list[tuple[str, str]] = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append((row["ticker"], row["company"]))
    return rows[:sample]


def _audit_payload(payload: dict) -> dict:
    """payload → metrics dict."""
    data = payload.get("data") or {}
    cands = data.get("candidates_evaluations") or []
    decisions = data.get("agenda_decisions") or []

    # G1 — per candidate dimension
    # G3 — appointment_type
    g1_records = []
    for c in cands:
        disq = (c.get("disqualification") or {}).get("summary")
        indep = (c.get("independence") or {}).get("summary")
        faith = c.get("faithfulness") or {}
        rec_reason = (faith.get("recommendation_reason_raw") or "").strip()
        main_job = (faith.get("main_job") or "").strip()
        careers = faith.get("career_company_groups") or []
        ah = faith.get("audit_history_check") or {}
        ah_red = ah.get("red_flags") or []
        apt = c.get("appointment_type") or {}
        apt_type = apt.get("type") if isinstance(apt, dict) else None

        d_disq = bool(disq)
        d_indep = bool(indep)
        d_expertise = bool(rec_reason or main_job)
        d_past_career = bool(careers or ah_red)

        g1_records.append({
            "name": c.get("name"),
            "role_type": c.get("role_type"),
            "agenda_action": c.get("agenda_action"),
            "appointment_type": apt_type,
            "d_disqualification": d_disq,
            "d_independence": d_indep,
            "d_expertise": d_expertise,
            "d_past_career": d_past_career,
            "all_4": d_disq and d_indep and d_expertise and d_past_career,
        })

    # G2 — NO_DATA breakdown + G4 — 1번 안건 FY raw
    g2_records = []
    g4_status: str | None = None  # 회사 단위 (1번 안건 또는 financial_statements 안건의 fy_raw_extraction_status)
    for ad in decisions:
        decision = ad.get("decision")
        facts = ad.get("facts") or {}
        risks = ad.get("risk_factors") or []
        is_no_data = decision == "NO_DATA"
        false_positive = is_no_data and (bool(facts) or bool(risks))
        g2_records.append({
            "agenda_title": (ad.get("agenda_title") or "")[:60],
            "agenda_category": ad.get("agenda_category"),
            "decision": decision,
            "fact_count": len(facts),
            "risk_count": len(risks),
            "no_data": is_no_data,
            "no_data_false_positive": false_positive,
        })
        # G4 — financial_statements 안건의 fy_raw_extraction_status
        if ad.get("agenda_category") == "financial_statements" and g4_status is None:
            g4_status = facts.get("fy_raw_extraction_status") or "no_data"

    # G3 — appointment_type (currently not auto-detected, all are 'unknown')
    # G4 — FY agenda raw (currently not parsed, all 'unknown')

    return {
        "candidates": g1_records,
        "agenda": g2_records,
        "g4_fy_status": g4_status,
    }


async def _run_one(ticker: str, name: str, year: int, semaphore: asyncio.Semaphore) -> dict:
    async with semaphore:
        t0 = time.time()
        try:
            payload = await asyncio.wait_for(
                build_proxy_advise_payload(name, year=year, vote_style="open_proxy", scope="decisions"),
                timeout=120.0,
            )
            audit = _audit_payload(payload)
            return {
                "ticker": ticker,
                "name": name,
                "status": payload.get("status"),
                "duration_s": round(time.time() - t0, 1),
                "candidates_count": len(audit["candidates"]),
                "agenda_count": len(audit["agenda"]),
                "audit": audit,
            }
        except Exception as exc:
            return {
                "ticker": ticker,
                "name": name,
                "status": "exception",
                "error": f"{type(exc).__name__}: {exc}",
                "duration_s": round(time.time() - t0, 1),
                "audit": {"candidates": [], "agenda": []},
            }


def _summarize(results: list[dict]) -> dict:
    total_companies = len(results)
    ok_companies = sum(1 for r in results if r.get("status") not in ("exception", "error"))
    cands_total = []
    agenda_total = []
    for r in results:
        cands_total.extend(r.get("audit", {}).get("candidates", []))
        agenda_total.extend(r.get("audit", {}).get("agenda", []))

    n_cands = len(cands_total)
    g1_metrics = {
        "n_candidates": n_cands,
        "d_disqualification": sum(1 for c in cands_total if c["d_disqualification"]),
        "d_independence": sum(1 for c in cands_total if c["d_independence"]),
        "d_expertise": sum(1 for c in cands_total if c["d_expertise"]),
        "d_past_career": sum(1 for c in cands_total if c["d_past_career"]),
        "all_4": sum(1 for c in cands_total if c["all_4"]),
    }
    if n_cands:
        g1_metrics["pct_disqualification"] = round(g1_metrics["d_disqualification"] / n_cands * 100, 1)
        g1_metrics["pct_independence"] = round(g1_metrics["d_independence"] / n_cands * 100, 1)
        g1_metrics["pct_expertise"] = round(g1_metrics["d_expertise"] / n_cands * 100, 1)
        g1_metrics["pct_past_career"] = round(g1_metrics["d_past_career"] / n_cands * 100, 1)
        g1_metrics["pct_all_4"] = round(g1_metrics["all_4"] / n_cands * 100, 1)

    n_agenda = len(agenda_total)
    n_no_data = sum(1 for a in agenda_total if a["no_data"])
    n_no_data_fp = sum(1 for a in agenda_total if a["no_data_false_positive"])
    g2_metrics = {
        "n_agenda": n_agenda,
        "n_no_data": n_no_data,
        "n_no_data_false_positive": n_no_data_fp,
        "pct_no_data": round(n_no_data / max(n_agenda, 1) * 100, 1),
        "pct_no_data_false_positive_of_no_data": round(n_no_data_fp / max(n_no_data, 1) * 100, 1) if n_no_data else 0,
    }

    # G4 — 회사 단위 fy_raw 추출률
    g4_companies_with_fs = [r for r in results if r.get("audit", {}).get("g4_fy_status") is not None]
    g4_success = sum(1 for r in g4_companies_with_fs if r["audit"]["g4_fy_status"] in ("success", "partial"))
    g4_metrics = {
        "n_companies_with_fs_agenda": len(g4_companies_with_fs),
        "n_fy_success_or_partial": g4_success,
        "pct_fy_extracted": round(g4_success / max(len(g4_companies_with_fs), 1) * 100, 1),
    }

    # G3 appointment_type breakdown
    n_apt_new = sum(1 for c in cands_total if c["appointment_type"] == "new")
    n_apt_renewed = sum(1 for c in cands_total if c["appointment_type"] == "renewed")
    n_apt_amb = sum(1 for c in cands_total if c["appointment_type"] in (None, "ambiguous"))
    # 사내이사 중 'new'로 잡힌 비율 (DART 부서명-only 케이스 — 잠재적 false-new)
    n_inside_total = sum(1 for c in cands_total if any(k in (c.get("role_type") or "") for k in ("사내", "executive")))
    n_inside_new = sum(1 for c in cands_total
                       if any(k in (c.get("role_type") or "") for k in ("사내", "executive"))
                       and c["appointment_type"] == "new")
    g3_metrics = {
        "n_candidates": n_cands,
        "n_new": n_apt_new,
        "n_renewed": n_apt_renewed,
        "n_ambiguous": n_apt_amb,
        "pct_classified": round((n_apt_new + n_apt_renewed) / max(n_cands, 1) * 100, 1),
        "n_inside_total": n_inside_total,
        "n_inside_new": n_inside_new,
        "pct_inside_new_warn": round(n_inside_new / max(n_inside_total, 1) * 100, 1),  # 사내이사인데 new 비율 — 부서명-only false-new 의심
    }

    return {
        "n_companies": total_companies,
        "n_ok": ok_companies,
        "pct_ok": round(ok_companies / max(total_companies, 1) * 100, 1),
        "g1": g1_metrics,
        "g2": g2_metrics,
        "g3": g3_metrics,
        "g4": g4_metrics,
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", default="kospi200")
    parser.add_argument("--sample", type=int, default=50)
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    universe = _load_universe(args.universe, args.sample)
    print(f"# audit: {len(universe)} companies, year={args.year}, concurrency={args.concurrency}", flush=True)

    clear_proxy_advise_cache()

    sem = asyncio.Semaphore(args.concurrency)
    tasks = [_run_one(t, n, args.year, sem) for t, n in universe]

    results = []
    for i, fut in enumerate(asyncio.as_completed(tasks), 1):
        r = await fut
        results.append(r)
        marker = "✓" if r.get("status") not in ("exception",) else "✗"
        print(f"  [{i:>3}/{len(universe)}] {marker} {r['ticker']} {r['name']} ({r.get('duration_s', '?')}s) cands={r.get('candidates_count', 0)} agenda={r.get('agenda_count', 0)}", flush=True)

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
