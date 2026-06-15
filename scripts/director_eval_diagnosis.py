"""선임 안건 세부(후보자 정보) 파싱 전수 진단 — director_evaluation.

compensation/agenda 진단과 동일 방법론을 선임(이사/감사) 후보 파싱에 적용:
  - 후보 검출 (no_candidates / 개수)
  - 이름 파싱 (빈 이름)
  - 독립성 sub_factor 매핑 (success / soft-fail / fail) — 어느 factor가 약한가
  - 독립성 summary 분포 (independent / concerns / no_data)
  - 결격사유 status 분포
  - 선임유형 (new / renewed / ambiguous / None)
  - 추천사유/경력 raw 파싱 (빈 비율)

ground truth 없이 mapping fail/soft-fail·no_data를 프록시 지표로 빈도화 → 폴백 우선순위.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from collections import Counter
from pathlib import Path

import httpx

from open_proxy_mcp.dart.client import get_dart_client
from open_proxy_mcp.services.director_evaluation import build_director_evaluation_payload as de

UNIVERSE_FILE = os.environ.get("UNIVERSE_FILE", "/tmp/kospi_kosdaq_300.json")
OUT = Path(os.environ.get("AUDIT_OUT", "wiki/architecture/audits/data/260615_director_eval_diagnosis.json"))
BATCH = 30
SLEEP_COMPANY = 0.5
SLEEP_BATCH = 15.0


def _disq_status(e: dict) -> str:
    d = e.get("disqualification")
    if isinstance(d, dict):
        # 실제 구조: {"sub_factors": {age, eligibility}, "summary": "clean"}
        return str(d.get("summary") or d.get("status") or d.get("result") or "unknown")
    return str(d) if d else "none"


async def _diag(q: str) -> dict:
    p = await de(q, year=2026, meeting_type="annual")
    d = p.get("data") or {}
    evs = d.get("evaluations") or []
    n = len(evs)
    no_name = sum(1 for e in evs if not (e.get("name") or "").strip())
    indep_map: Counter = Counter()     # factor:mapping
    indep_summary: Counter = Counter()
    disq: Counter = Counter()
    appt: Counter = Counter()
    role: Counter = Counter()
    no_reco = 0
    r2_softfail = 0       # recent_2y_employee soft-fail 수
    r2_softfail_with_ev = 0  # 그중 evidence(경력 raw) 채워진 수
    for e in evs:
        ind = e.get("independence") or {}
        r2 = (ind.get("sub_factors") or {}).get("recent_2y_employee") or {}
        if r2.get("mapping") == "soft-fail":
            r2_softfail += 1
            if r2.get("evidence"):
                r2_softfail_with_ev += 1
        for fk, fv in (ind.get("sub_factors") or {}).items():
            indep_map[f"{fk}:{(fv or {}).get('mapping')}"] += 1
        indep_summary[str(ind.get("summary"))] += 1
        disq[_disq_status(e)] += 1
        appt[str((e.get("appointment_type") or {}).get("type"))] += 1
        role[str(e.get("role_type"))] += 1
        fa = e.get("faithfulness") or {}
        if not (fa.get("recommendation_reason_raw") or "").strip():
            no_reco += 1
    return {
        "company": d.get("canonical_name") or q, "query": q,
        "status": str(p.get("status")),
        "candidates": n,
        "no_candidates": n == 0,
        "no_name": no_name,
        "no_reco_reason": no_reco,
        "r2_softfail": r2_softfail,
        "r2_softfail_with_ev": r2_softfail_with_ev,
        "indep_map": dict(indep_map),
        "indep_summary": dict(indep_summary),
        "disq": dict(disq),
        "appt": dict(appt),
        "role": dict(role),
        "filing_status": str(d.get("filing_status")),
    }


async def main() -> None:
    universe = json.loads(Path(UNIVERSE_FILE).read_text())
    client = get_dart_client()
    calls0 = client.api_call_snapshot()
    t0 = time.time()
    rows = []
    print(f"[선임 후보 파싱 진단] {len(universe)}사")
    for i, q in enumerate(universe):
        try:
            rows.append(await _diag(q))
        except httpx.ReadError as exc:
            print(f"  [ABORT] ReadError at {q}: {exc}")
            break
        except Exception as exc:  # noqa: BLE001
            rows.append({"company": q, "query": q, "status": "EXC", "error": str(exc)[:60]})
        await asyncio.sleep(SLEEP_COMPANY)
        if (i + 1) % BATCH == 0:
            calls = client.api_call_snapshot() - calls0
            print(f"  {i+1}/{len(universe)} 누적콜={calls} {(time.time()-t0)/60:.1f}분")
            await asyncio.sleep(SLEEP_BATCH)

    ev = [r for r in rows if not r.get("error")]
    total_cand = sum(r.get("candidates", 0) for r in ev)
    # 회사 단위: 선임 안건 있는데 후보 0 (no_candidates 중 filing 있는 것)
    no_cand_cos = [r["company"] for r in ev if r.get("no_candidates")]
    agg = {k: Counter() for k in ("indep_map", "indep_summary", "disq", "appt", "role")}
    for r in ev:
        for k in agg:
            agg[k].update(r.get(k, {}))
    summary = {
        "companies": len(ev),
        "no_candidate_companies": len(no_cand_cos),
        "total_candidates": total_cand,
        "no_name_total": sum(r.get("no_name", 0) for r in ev),
        "no_reco_reason_total": sum(r.get("no_reco_reason", 0) for r in ev),
        "r2_softfail_total": sum(r.get("r2_softfail", 0) for r in ev),
        "r2_softfail_with_ev_total": sum(r.get("r2_softfail_with_ev", 0) for r in ev),
        "independence_mapping": dict(agg["indep_map"].most_common()),
        "independence_summary": dict(agg["indep_summary"].most_common()),
        "disqualification": dict(agg["disq"].most_common()),
        "appointment_type": dict(agg["appt"].most_common()),
        "role_type": dict(agg["role"].most_common()),
    }
    result = {
        "meta": {"date": "2026-06-15", "universe": f"{UNIVERSE_FILE} ({len(rows)}사)",
                 "total_dart_calls": client.api_call_snapshot() - calls0,
                 "elapsed_min": round((time.time() - t0) / 60, 1)},
        "summary": summary,
        "no_candidate_companies": no_cand_cos,
        "rows": rows,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n[완료] {summary['companies']}사 / 후보 {total_cand}")
    print(f"  후보0(선임 후보 미검출): {len(no_cand_cos)}사")
    print(f"  빈 이름: {summary['no_name_total']} / 추천사유 빈: {summary['no_reco_reason_total']}")
    print(f"  독립성 summary: {summary['independence_summary']}")
    print(f"  결격 status: {summary['disqualification']}")
    print(f"  선임유형: {summary['appointment_type']}")
    print(f"  역할: {summary['role_type']}")
    print(f"  독립성 sub_factor 매핑(soft-fail/fail 주목):")
    for k, v in summary["independence_mapping"].items():
        if "success" not in k:
            print(f"    {k}: {v}")
    print(f"  총 DART콜 {result['meta']['total_dart_calls']}, {result['meta']['elapsed_min']}분 → {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
