"""동적 treasury lookback 검증 — 여러 기업.

검증:
  1) earliest_start detect 성공률 (None이면 120 fallback)
  2) 동적 lookback 계산값 분포
  3) 정확도 보존 (핵심): 동적 CSR 소각합 == 고정 120 기준 재직기간 소각합
     (동적이 재직기간 내 소각을 자르면 mismatch — 자르면 안 됨)
  4) 시간

페이싱: 회사 간 SLEEP. proxy_advise(~20콜) + treasury(별도) = 회사당 ~25콜.
"""
from __future__ import annotations

import asyncio
import time

import httpx

from open_proxy_mcp.services.proxy_advise import build_proxy_advise_payload as pa
from open_proxy_mcp.services.treasury_share import build_treasury_share_payload as tre

# 재직기간 다양하게 — 장기 오너 / 금융지주(소각 많음) / 중견 / 신생
COMPANIES = [
    "삼성전자", "현대차", "SK", "LG전자", "한화",
    "KB금융", "신한지주", "메리츠금융지주", "POSCO홀딩스", "기아",
    "NAVER", "카카오", "셀트리온", "에코프로비엠", "크래프톤",
    "하이브", "삼성SDI", "LG화학", "현대모비스", "두산",
]
SLEEP = 3.0


async def _check(q: str) -> dict:
    p = await pa(q)
    d = p.get("data") or {}
    target = d.get("year")
    inside = [
        ev for ev in (d.get("candidates_evaluations") or [])
        if "사내" in (ev.get("role_type") or "")
        and ((ev.get("performance") or {}).get("matrix") or {}).get("csr")
    ]
    if not inside or target is None:
        return {"company": q, "note": "사내이사 연임 performance 없음", "mismatch": 0, "skip": True}

    earliests = [(ev.get("appointment_type") or {}).get("earliest_start") for ev in inside]
    detect_ok = bool(earliests) and all(earliests)
    if detect_ok:
        dyn_lb = max(36, min(120, (target - min(earliests) + 2) * 12))
    else:
        dyn_lb = 120

    # 고정 120 기준 연도별 소각
    t = await tre(q, scope="summary", lookback_months=120)
    cancels: dict[int, int] = {}
    for e in ((t.get("data") or {}).get("events") or []):
        if e.get("event") == "cancelation_decision":
            y = e.get("rcept_dt", "")[:4]
            if y.isdigit():
                cancels[int(y)] = cancels.get(int(y), 0) + (e.get("amount_krw") or 0)

    # 정확도: 각 사내이사 CSR 소각합(동적) == 120 기준 재직기간 소각합
    mismatch = 0
    detail = []
    for ev in inside:
        earliest = (ev.get("appointment_type") or {}).get("earliest_start") or (target - 5)
        tenure = set(range(earliest, target + 1))
        cancel_120_tenure = sum(v for y, v in cancels.items() if y in tenure)
        cancel_dyn = (((ev.get("performance") or {}).get("matrix") or {}).get("csr") or {}).get("total_cancelation_krw", 0)
        if cancel_120_tenure != cancel_dyn:
            mismatch += 1
            detail.append(f"{ev.get('name')}: 동적{cancel_dyn:,} vs 120기준{cancel_120_tenure:,}")
    return {
        "company": q, "target": target, "inside": len(inside),
        "detect_ok": detect_ok, "earliest_min": min(earliests) if detect_ok else None,
        "dyn_lookback_months": dyn_lb, "cancel_years_120": sorted(cancels.keys()),
        "mismatch": mismatch, "mismatch_detail": detail, "skip": False,
    }


async def main() -> None:
    print(f"[동적 lookback 검증] {len(COMPANIES)}사")
    rows = []
    t0 = time.time()
    for i, q in enumerate(COMPANIES):
        try:
            r = await _check(q)
            rows.append(r)
            if r.get("skip"):
                print(f"  {q}: {r['note']}")
            else:
                flag = "🔴MISMATCH" if r["mismatch"] else "✓"
                print(f"  {q}: 사내{r['inside']} detect={r['detect_ok']} "
                      f"earliest={r['earliest_min']} dyn_lb={r['dyn_lookback_months']}개월 "
                      f"소각연도{r['cancel_years_120']} {flag}")
                for dt in r["mismatch_detail"]:
                    print(f"      {dt}")
        except httpx.ReadError as exc:
            print(f"  [ABORT] ReadError at {q}: {exc}")
            break
        except Exception as exc:  # noqa: BLE001
            print(f"  {q}: EXC {type(exc).__name__}: {str(exc)[:80]}")
        await asyncio.sleep(SLEEP)

    evaluated = [r for r in rows if not r.get("skip")]
    total_mismatch = sum(r.get("mismatch", 0) for r in evaluated)
    detect_fail = [r["company"] for r in evaluated if not r["detect_ok"]]
    shortened = [r for r in evaluated if r.get("dyn_lookback_months", 120) < 120]
    print(f"\n[완료] 평가 {len(evaluated)}사 / {time.time()-t0:.0f}s")
    print(f"  정확도(핵심): mismatch {total_mismatch}건 — 0이면 동적이 재직기간 소각을 안 자름")
    print(f"  detect 실패(→120 fallback): {detect_fail or '없음'}")
    print(f"  lookback 단축된 회사(<120): {[(r['company'], r['dyn_lookback_months']) for r in shortened] or '없음'}")


if __name__ == "__main__":
    asyncio.run(main())
