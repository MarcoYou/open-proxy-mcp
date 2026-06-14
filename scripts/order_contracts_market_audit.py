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
import os
import time
from collections import Counter
from pathlib import Path

import httpx

from open_proxy_mcp.dart.client import get_dart_client
from open_proxy_mcp.services.order_contracts import build_order_contracts_payload as build

BASELINE = Path("wiki/architecture/audits/data/260517_parsing_success_rate_audit/baseline_company_sample_450.json")
# UNIVERSE_FILE(회사명 리스트 JSON) 지정 시 그걸 universe로 — 섹터별 전수조사(바이오 등) 재사용.
UNIVERSE_FILE = os.environ.get("UNIVERSE_FILE")
OUT = Path(os.environ.get("AUDIT_OUT", "wiki/architecture/audits/data/260613_order_contracts_market_audit.json"))
BATCH = 30
SLEEP_COMPANY = 0.4
SLEEP_BATCH = 20.0
MAX_DOCS = 15


def _universe() -> list[str]:
    if UNIVERSE_FILE:
        return json.loads(Path(UNIVERSE_FILE).read_text())  # 회사명 리스트
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

    # 해지(termination) 파싱 검증 — 계약명/상대방/해지금액/매출대비 + 단위 불변식
    terms = d.get("terminations") or []
    t_no_name = t_no_cp = t_no_amt = t_no_rev = t_unit_mismatch = 0
    for t in terms:
        if not t.get("contract_name"):
            t_no_name += 1
            flags.append("TERM_NO_NAME")
        if not t.get("counterparty"):
            t_no_cp += 1
            flags.append("TERM_NO_CP")
        tamt = t.get("terminated_amount_won")
        trev = t.get("recent_revenue_won")
        tratio = t.get("revenue_ratio_pct")
        if not tamt:
            t_no_amt += 1
            flags.append("TERM_NO_AMT")
        if tratio is None:
            t_no_rev += 1
        if tamt and trev and tratio and tratio > 0:
            implied = tamt / trev * 100
            if abs(implied - tratio) > max(tratio * 0.15, 1.0):
                t_unit_mismatch += 1
                flags.append(f"TERM_UNIT?:{(t.get('contract_name') or '')[:12]} amt/rev={implied:.1f} vs 공시{tratio}")

    # 판단용 진단 ① 해지-체결 매핑 가능성: 같은 window 내 (계약명+상대방) 매칭된 해지 수.
    #   계약기간 시작일이 본문에 있으면 더 긴 window 역조회로 매핑 가능성 추정.
    term_matched = sum(1 for t in terms if t.get("matched_order_rcept_no"))
    term_has_period = sum(1 for t in terms if t.get("period_start") or t.get("contract_period_start"))
    # 판단용 진단 ② 계열 판정 신뢰도: 체결의 '회사와의 관계' 명시(계열/외부)냐, 미기재('-')냐.
    rel_internal = rel_ext_named = rel_blank = 0
    for o in orders:
        rel = (o.get("relationship") or "").strip()
        if not rel or rel.lstrip().startswith("-"):
            rel_blank += 1
        elif not o.get("is_external"):
            rel_internal += 1
        else:
            rel_ext_named += 1

    return {
        "company": company,
        "status": str(payload.get("status")),
        "order_count": len(orders),
        "external_count": s.get("external_count", 0),
        "internal_count": s.get("internal_count", 0),
        "correction_count": s.get("correction_count", 0),
        "no_name": no_name,
        "no_amount": no_amt,
        "no_revenue_ratio": no_rev,
        "unit_mismatch": unit_mismatch,
        "max_revenue_ratio_pct": s.get("max_revenue_ratio_pct"),
        "termination_count": len(terms),
        "term_no_name": t_no_name,
        "term_no_counterparty": t_no_cp,
        "term_no_amount": t_no_amt,
        "term_no_revenue_ratio": t_no_rev,
        "term_unit_mismatch": t_unit_mismatch,
        "term_matched": term_matched,
        "term_has_period": term_has_period,
        "rel_internal": rel_internal,
        "rel_ext_named": rel_ext_named,
        "rel_blank": rel_blank,
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
    with_terms = [r for r in rows if r.get("termination_count")]
    total_terms = sum(r.get("termination_count", 0) for r in rows)
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
            "termination": {
                "companies_with_terminations": len(with_terms),
                "total_terminations": total_terms,
                "term_no_name": sum(r.get("term_no_name", 0) for r in rows),
                "term_no_counterparty": sum(r.get("term_no_counterparty", 0) for r in rows),
                "term_no_amount": sum(r.get("term_no_amount", 0) for r in rows),
                "term_no_revenue_ratio": sum(r.get("term_no_revenue_ratio", 0) for r in rows),
                "term_unit_mismatch": sum(r.get("term_unit_mismatch", 0) for r in rows),
            },
            # 판단 ① 해지-체결 매핑 가능성
            "mapping_feasibility": {
                "total_terminations": total_terms,
                "matched_in_window": sum(r.get("term_matched", 0) for r in rows),
                "has_period_start": sum(r.get("term_has_period", 0) for r in rows),
            },
            # 판단 ② 계열 판정 신뢰도 (체결 관계필드 명시 여부)
            "relation_disclosure": {
                "total_orders": sum(r.get("order_count", 0) for r in rows),
                "internal_named": sum(r.get("rel_internal", 0) for r in rows),
                "external_named": sum(r.get("rel_ext_named", 0) for r in rows),
                "blank_unspecified": sum(r.get("rel_blank", 0) for r in rows),
            },
            "flagged_companies": len(flagged),
        },
        "termination_rows": [r for r in rows if r.get("termination_count")],
        "flagged_rows": flagged,
        "all_rows": rows,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    sm = result["summary"]
    print(f"\n[완료] {sm['companies']}사 — 수주有 {sm['companies_with_orders']}, 유효계약 {sm['total_valid_orders']}, 정정 {sm['total_corrections']}")
    print(f"  파싱누락: {sm['parse_miss']}  flag회사 {sm['flagged_companies']}")
    print(f"  해지: {sm['termination']}")
    print(f"  [판단①] 매핑가능성: {sm['mapping_feasibility']}")
    print(f"  [판단②] 관계명시: {sm['relation_disclosure']}")
    print(f"  총 DART콜 {result['meta']['total_dart_calls']}, {result['meta']['elapsed_min']}분 → {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
