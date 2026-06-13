"""order_contracts 시장 전수 audit — baseline 450사.

검증:
  1) 파싱 누락률 — 계약명/금액/매출대비% None 비율
  2) 단위 정합 불변식 — 계약금액 ÷ 최근매출 × 100 ≈ 매출대비% (단위 오염이면 깨짐)
  3) 정정 dedup — 정정 건수, dedup 전후 차이
  4) 이상치 flag — 매출대비 >1000%, 금액 음수 등

페이싱·콜 추적 (재사용 패턴): 배치 BATCH사마다 client.api_call_snapshot() 누적 출력 +
SLEEP_BATCH 휴식. 회사 간 SLEEP_COMPANY. ReadError 즉시 중단. 최종 누적 콜·rate 출력.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from pathlib import Path

import httpx

from open_proxy_mcp.dart.client import get_dart_client
from open_proxy_mcp.services.order_contracts import build_order_contracts_payload as build

BASELINE = Path("wiki/architecture/audits/data/260517_parsing_success_rate_audit/baseline_company_sample_450.json")
OUT = Path("wiki/architecture/audits/data/260613_order_contracts_market_audit.json")
BATCH = 30
SLEEP_COMPANY = 0.4
SLEEP_BATCH = 20.0
MAX_DOCS = 15


def _universe() -> list[str]:
    recs = json.loads(BASELINE.read_text())["records"]
    seen, out = set(), []
    for r in recs:
        if r.get("tool") == "company" and r.get("company") and r["company"] not in seen:
            seen.add(r["company"])
            out.append(r["company"])
    return out


def _check(company: str, payload: dict) -> dict:
    d = payload.get("data") or {}
    orders = d.get("orders") or []
    s = d.get("signal_summary") or {}
    flags: list[str] = []
    no_name = no_amt = no_rev = unit_mismatch = 0
    for o in orders:
        if not o.get("contract_name"):
            no_name += 1
        amt = o.get("contract_amount_won")
        rev = o.get("recent_revenue_won")
        ratio = o.get("revenue_ratio_pct")
        if not amt:
            no_amt += 1
        if ratio is None:
            no_rev += 1
        # 단위 정합 불변식: amt/rev*100 ≈ ratio
        if amt and rev and ratio and ratio > 0:
            implied = amt / rev * 100
            if abs(implied - ratio) > max(ratio * 0.15, 1.0):
                unit_mismatch += 1
                flags.append(f"UNIT?:{o.get('contract_name','')[:12]} amt/rev={implied:.1f} vs 공시{ratio}")
        if ratio is not None and ratio > 1000:
            flags.append(f"RATIO>1000:{ratio}")
        if amt is not None and amt < 0:
            flags.append(f"NEG_AMT:{amt}")
    return {
        "company": company,
        "status": str(payload.get("status")),
        "order_count": len(orders),
        "external_count": s.get("external_count", 0),
        "correction_count": s.get("correction_count", 0),
        "no_name": no_name,
        "no_amount": no_amt,
        "no_revenue_ratio": no_rev,
        "unit_mismatch": unit_mismatch,
        "max_revenue_ratio_pct": s.get("max_revenue_ratio_pct"),
        "flags": flags,
    }


async def main() -> None:
    client = get_dart_client()
    universe = _universe()
    print(f"[order audit] {len(universe)}사 시작 (batch {BATCH} + {SLEEP_BATCH}s, max_docs {MAX_DOCS})")
    rows: list[dict] = []
    t0 = time.time()
    calls0 = client.api_call_snapshot()
    for i, q in enumerate(universe):
        try:
            p = await build(q, max_documents=MAX_DOCS)
            rows.append(_check(q, p))
        except httpx.ReadError as exc:
            print(f"[ABORT] ReadError at {q}: {exc} — 즉시 중단")
            break
        except Exception as exc:  # noqa: BLE001
            rows.append({"company": q, "status": "EXC", "flags": [f"EXC:{type(exc).__name__}"], "error": str(exc)[:80]})
        await asyncio.sleep(SLEEP_COMPANY)
        if (i + 1) % BATCH == 0:
            calls = client.api_call_snapshot() - calls0
            elapsed = time.time() - t0
            rate = calls / (elapsed / 60) if elapsed else 0
            withorders = sum(1 for r in rows if r.get("order_count"))
            print(f"  {i+1}/{len(universe)}  누적콜={calls}  경과={elapsed/60:.1f}분  분당={rate:.0f}/910  수주有={withorders}  flag={sum(1 for r in rows if r.get('flags'))}")
            await asyncio.sleep(SLEEP_BATCH)

    with_orders = [r for r in rows if r.get("order_count")]
    total_orders = sum(r.get("order_count", 0) for r in rows)
    flagged = [r for r in rows if r.get("flags")]
    result = {
        "meta": {
            "script": "scripts/order_contracts_market_audit.py", "date": "2026-06-13",
            "universe": f"baseline450 ({len(rows)}사 완료)",
            "total_dart_calls": client.api_call_snapshot() - calls0,
            "elapsed_min": round((time.time() - t0) / 60, 1),
        },
        "summary": {
            "companies": len(rows),
            "companies_with_orders": len(with_orders),
            "total_valid_orders": total_orders,
            "total_corrections": sum(r.get("correction_count", 0) for r in rows),
            "parse_miss": {
                "no_name": sum(r.get("no_name", 0) for r in rows),
                "no_amount": sum(r.get("no_amount", 0) for r in rows),
                "no_revenue_ratio": sum(r.get("no_revenue_ratio", 0) for r in rows),
                "unit_mismatch": sum(r.get("unit_mismatch", 0) for r in rows),
            },
            "flagged_companies": len(flagged),
        },
        "flagged_rows": flagged,
        "all_rows": rows,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    sm = result["summary"]
    print(f"\n[완료] {sm['companies']}사 — 수주有 {sm['companies_with_orders']}, 유효계약 {sm['total_valid_orders']}, 정정 {sm['total_corrections']}")
    print(f"  파싱누락: {sm['parse_miss']}  flag회사 {sm['flagged_companies']}")
    print(f"  총 DART콜 {result['meta']['total_dart_calls']}, {result['meta']['elapsed_min']}분 → {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
