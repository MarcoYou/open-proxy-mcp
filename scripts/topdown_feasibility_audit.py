"""탑다운 스크리닝 실현가능성 전수조사 — 지표 기반(영업이익 증가율).

질문: "영업이익 30% 이상 오른 기업들"을 현재 bottom-up 툴로 실행하면?
방법: universe 500사를 financial_metrics(yoy)로 순회 → operating_profit_yoy_pct 필터.
측정: 기업당 콜 수, 총 콜, 시간, 필터 결과 → 탑다운 1쿼리의 실제 비용 실증.

rate limit: financial_metrics yoy ≈ 14콜/사 → 500사 ≈ 7000콜. batch 페이싱 필수.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import httpx

from open_proxy_mcp.dart.client import get_dart_client
from open_proxy_mcp.services.financial_metrics import build_financial_metrics_payload as bf

UNIVERSE_FILE = os.environ.get("UNIVERSE_FILE", "/tmp/topdown_universe.json")
OUT = Path(os.environ.get("AUDIT_OUT", "wiki/architecture/audits/data/260617_topdown_metric_feasibility.json"))
THRESHOLD = float(os.environ.get("THRESHOLD", "30"))  # 영업이익 증가율 % 임계
BATCH = 20
SLEEP_COMPANY = 0.3
SLEEP_BATCH = 20.0
LIMIT = int(os.environ.get("LIMIT", "500"))


def _find_key(obj, key):
    """payload 어디에 있든 key 값 재귀 추출."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_key(v, key)
            if r is not None:
                return r
    return None


async def main() -> None:
    uni = json.loads(Path(UNIVERSE_FILE).read_text())
    companies = (uni.get("all") if isinstance(uni, dict) else uni)[:LIMIT]
    market = {}
    if isinstance(uni, dict):
        for m in ("kospi", "kosdaq"):
            for c in uni.get(m, []):
                market[c] = m

    client = get_dart_client()
    calls0 = client.api_call_snapshot()
    t0 = time.time()
    rows, matched, no_data, errors = [], [], 0, 0
    print(f"[탑다운 지표 실현가능성] {len(companies)}사 — financial_metrics(yoy) 순회 → 영업이익 증가율 ≥{THRESHOLD}%")

    for i, q in enumerate(companies):
        try:
            c0 = client.api_call_snapshot()
            p = await bf(q, scope="yoy", year=2026)
            calls = client.api_call_snapshot() - c0
            d = p.get("data") or {}
            op_yoy = _find_key(d, "operating_profit_yoy_pct")
            row = {"company": q, "market": market.get(q, "?"), "calls": calls,
                   "status": str(p.get("status")), "op_yoy_pct": op_yoy}
            if op_yoy is None:
                no_data += 1
            elif op_yoy >= THRESHOLD:
                matched.append({"company": q, "market": market.get(q, "?"), "op_yoy_pct": op_yoy})
            rows.append(row)
        except httpx.ReadError as exc:
            print(f"  [ABORT] ReadError at {q}: {exc} — 즉시 중단")
            break
        except Exception as exc:  # noqa: BLE001
            errors += 1
            rows.append({"company": q, "status": "EXC", "error": str(exc)[:60]})
        await asyncio.sleep(SLEEP_COMPANY)
        if (i + 1) % BATCH == 0:
            tot = client.api_call_snapshot() - calls0
            el = time.time() - t0
            rate = tot / (el / 60) if el else 0
            print(f"  {i+1}/{len(companies)} 누적콜={tot} 경과={el/60:.1f}분 분당={rate:.0f}/910 매칭={len(matched)}")
            await asyncio.sleep(SLEEP_BATCH)

    total_calls = client.api_call_snapshot() - calls0
    elapsed = time.time() - t0
    done = len([r for r in rows if r.get("status") != "EXC"])
    avg_calls = round(total_calls / max(done, 1), 1)
    result = {
        "meta": {"date": "2026-06-17", "query": f"영업이익 증가율 ≥{THRESHOLD}%",
                 "universe": f"{len(rows)}사 (코스피300+코스닥200)",
                 "total_dart_calls": total_calls, "elapsed_min": round(elapsed / 60, 1),
                 "avg_calls_per_company": avg_calls},
        "feasibility": {
            "companies_scanned": done,
            "total_calls_for_one_query": total_calls,
            "avg_calls_per_company": avg_calls,
            "no_data": no_data, "errors": errors,
            "matched_count": len(matched),
        },
        "matched": sorted(matched, key=lambda x: -(x["op_yoy_pct"] or 0)),
        "rows": rows,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n[완료] {done}사 스캔 / 영업이익 ≥{THRESHOLD}% 기업 {len(matched)}")
    print(f"  ★ 탑다운 1쿼리 비용: 총 {total_calls}콜 / {elapsed/60:.1f}분 / 평균 {avg_calls}콜·사")
    print(f"  no_data {no_data}, errors {errors}")
    print(f"  상위 매칭: {[(m['company'], round(m['op_yoy_pct'])) for m in result['matched'][:8]]}")
    print(f"  → {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
