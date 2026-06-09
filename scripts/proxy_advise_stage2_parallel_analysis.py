"""proxy_advise 2단계(perf) 병렬화(방법 C) trade-off 전수 분석.

방법 C: director_eval을 1차와 병렬 유지하되 먼저 await → gate(inside_renewed) 판단 →
        perf를 director 완료 직후 발사(나머지 1차와 겹침). 1차 전체 완료를 기다리지 않음.

시간 모델 (renewed 회사):
  현재   = L1 + P              (1차 전체 후 perf 순차)
  방법 C = max(L1, D + P)      (perf가 director 완료 직후 시작, 나머지 1차와 겹침)
  이득   = (L1 + P) - max(L1, D + P)   ≥ 0  (수학적으로 항상 음수 아님)
  renewed 없는 회사: 현재=L1, 방법C=L1 (perf 미발사) → 이득 0, 손해 0

  L1 = 1차 8개 component 중 max (director 포함)
  D  = director_eval 시간,  P = perf(inside_director_performance) 시간

100개 director_eval로 renewed 비율 + D 분포(흔한/복잡 케이스), 대표 proxy_advise로 L1/P 실측.
"""
from __future__ import annotations

import asyncio
import json
import statistics
import time
from pathlib import Path

from open_proxy_mcp.dart.client import get_dart_client
from open_proxy_mcp.services.director_evaluation import build_director_evaluation_payload as de
from open_proxy_mcp.services.proxy_advise import build_proxy_advise_payload as pa

BASELINE = Path("wiki/architecture/audits/data/260517_parsing_success_rate_audit/baseline_company_sample_450.json")
OUT = Path("wiki/architecture/audits/data/proxy_advise_stage2_parallel_260610.json")
N_COMPANIES = 100
N_PA_RENEWED = 8   # 흔한 케이스(renewed) proxy_advise 정밀 측정 수
N_PA_NONREN = 4    # 복잡/엣지 케이스(renewed 없음) 수


def _kospi_companies(limit: int) -> list[str]:
    recs = json.loads(BASELINE.read_text())["records"]
    seen: set[str] = set()
    out: list[str] = []
    for r in recs:
        if r.get("market") == "KOSPI" and r.get("tool") == "company":
            nm = r.get("company")
            if nm and nm not in seen:
                seen.add(nm)
                out.append(nm)
    return out[:limit]


def _gate_renewed(payload: dict) -> int:
    evals = (payload.get("data") or {}).get("evaluations", []) or []
    ir = [
        e for e in evals
        if "사내" in (e.get("role_type") or "")
        and (e.get("appointment_type") or {}).get("type") == "renewed"
    ]
    return len(ir)


def _pa_components(tm: dict) -> tuple[float, float, float]:
    """timings_ms → (L1, D, P).  L1=1차 component max(director 포함), D=director, P=perf."""
    ups = {k: v for k, v in tm.items() if k.startswith("upstream.")}
    director = next((v for k, v in ups.items() if "director_evaluation" in k), 0.0)
    # 1차 = upstream.* 중 perf 단계(inside_director_performance) 제외
    l1_vals = [v for k, v in ups.items() if "performance" not in k]
    L1 = max(l1_vals) if l1_vals else 0.0
    P = tm.get("inside_director_performance_upstreams", 0.0)
    return float(L1), float(director), float(P)


async def main() -> None:
    c = get_dart_client()
    companies = _kospi_companies(N_COMPANIES)
    print(f"[stage2] KOSPI {len(companies)}개 director_eval 측정 시작")

    de_rows: list[dict] = []
    for i, q in enumerate(companies):
        try:
            s0 = c.api_call_snapshot()
            t0 = time.perf_counter()
            p = await de(q, year=2024, meeting_type="annual")
            D = (time.perf_counter() - t0) * 1000
            calls = c.api_call_snapshot() - s0
            n_ren = _gate_renewed(p)
            de_rows.append({"company": q, "D_ms": round(D), "renewed": n_ren > 0, "n_renewed": n_ren, "calls": calls, "status": p.get("status")})
        except Exception as exc:  # noqa: BLE001
            de_rows.append({"company": q, "error": f"{type(exc).__name__}: {exc}"[:80]})
        if (i + 1) % 20 == 0:
            print(f"  director_eval {i+1}/{len(companies)}")
        await asyncio.sleep(0.4)

    ok = [r for r in de_rows if "D_ms" in r]
    renewed = [r for r in ok if r["renewed"]]
    print(f"[stage2] renewed 비율 = {len(renewed)}/{len(ok)}  D 중앙={statistics.median([r['D_ms'] for r in ok]):.0f}ms")

    # 대표 proxy_advise: renewed 있는 8 + 없는 4
    pa_targets = [r["company"] for r in renewed][:N_PA_RENEWED] + [r["company"] for r in ok if not r["renewed"]][:N_PA_NONREN]
    pa_rows: list[dict] = []
    for q in pa_targets:
        try:
            t0 = time.perf_counter()
            p = await pa(q)
            tot = (time.perf_counter() - t0) * 1000
            tm = (p.get("data") or {}).get("timings_ms", {}) or {}
            L1, D, P = _pa_components(tm)
            current = L1 + P if P > 0 else L1
            method_c = max(L1, D + P) if P > 0 else L1
            pa_rows.append({
                "company": q, "total_ms": round(tot), "L1_ms": round(L1), "D_ms": round(D), "P_ms": round(P),
                "current_model_ms": round(current), "method_c_model_ms": round(method_c),
                "gain_ms": round(current - method_c), "regression": current - method_c < -1,
            })
        except Exception as exc:  # noqa: BLE001
            pa_rows.append({"company": q, "error": f"{type(exc).__name__}: {exc}"[:80]})
        await asyncio.sleep(1.0)

    gains = [r["gain_ms"] for r in pa_rows if "gain_ms" in r and r.get("P_ms", 0) > 0]
    regressions = [r for r in pa_rows if r.get("regression")]
    result = {
        "meta": {"script": "scripts/proxy_advise_stage2_parallel_analysis.py", "n_director_eval": len(ok), "date": "2026-06-10"},
        "director_eval": de_rows,
        "renewed_ratio": f"{len(renewed)}/{len(ok)}",
        "D_stats_ms": {"median": round(statistics.median([r["D_ms"] for r in ok])), "max": max(r["D_ms"] for r in ok), "p90": round(statistics.quantiles([r["D_ms"] for r in ok], n=10)[8])},
        "proxy_advise_detail": pa_rows,
        "gain_stats_ms": {"median": round(statistics.median(gains)) if gains else None, "max": max(gains) if gains else None, "min": min(gains) if gains else None},
        "regression_cases": regressions,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"[stage2] 완료. 이득 중앙={result['gain_stats_ms']['median']}ms 회귀케이스={len(regressions)}  → {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
