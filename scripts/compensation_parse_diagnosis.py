"""보수한도 파싱 실패/애매 유형 전수 진단 — 플래그/raw 폴백 설계용.

각 회사를 parse_status로 분류 + raw limit 수집:
  ok                 방향 판정 가능 (정상)
  agenda_not_found   보수한도 안건 자체 미검출 (items=0)
  amount_parse_fail  raw limit은 있으나 금액 환산 실패 (셀 오염 등)
  table_not_detected items 있으나 핵심 표 미검출 (raw limit도 None)
  prior_missing      당기 OK, 전기 누락 → 방향 불가
  current_missing    전기 OK, 당기 누락
  unit_unknown       금액 0<x<1억 (단위 표기 없는 공시 의심)

유형별 빈도 + 샘플(raw)로 어디에 폴백(플래그/raw 반환)이 필요한지 판단.
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
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload as sm

UNIVERSE_FILE = os.environ.get("UNIVERSE_FILE", "/tmp/kospi_kosdaq_300.json")
OUT = Path(os.environ.get("AUDIT_OUT", "wiki/architecture/audits/data/260615_compensation_parse_diagnosis.json"))
BATCH = 30
SLEEP_COMPANY = 0.5
SLEEP_BATCH = 15.0
_1EOK = 100_000_000


def _classify(items: list, cur, pri) -> str:
    raw_cur = (items[0].get("current") or {}).get("limit") if items else None
    raw_pri = (items[0].get("prior") or {}).get("limit") if items else None
    if not items:
        return "agenda_not_found"
    if cur is None and pri is None:
        return "amount_parse_fail" if (raw_cur or raw_pri) else "table_not_detected"
    if cur is not None and pri is None:
        return "prior_missing"
    if cur is None and pri is not None:
        return "current_missing"
    if (cur and 0 < cur < _1EOK) or (pri and 0 < pri < _1EOK):
        return "unit_unknown"
    return "ok"


async def _diagnose(q: str) -> dict:
    p = await sm(q, scope="compensation", year=2026, meeting_type="annual")
    d = p.get("data") or {}
    comp = d.get("compensation") or {}
    items = comp.get("items") or []
    s = comp.get("summary") or {}
    cur = s.get("currentTotalLimit")
    pri = s.get("priorTotalLimit")
    raw_cur = (items[0].get("current") or {}).get("limit") if items else None
    raw_pri = (items[0].get("prior") or {}).get("limit") if items else None
    return {
        "company": d.get("canonical_name") or q, "query": q,
        "status_payload": str(p.get("status")),
        "parse_status": _classify(items, cur, pri),
        "items": len(items), "cur": cur, "pri": pri,
        "raw_cur": (raw_cur or "")[:40], "raw_pri": (raw_pri or "")[:40],
    }


async def main() -> None:
    universe = json.loads(Path(UNIVERSE_FILE).read_text())
    client = get_dart_client()
    calls0 = client.api_call_snapshot()
    t0 = time.time()
    rows = []
    print(f"[보수한도 파싱 진단] {len(universe)}사")
    for i, q in enumerate(universe):
        try:
            rows.append(await _diagnose(q))
        except httpx.ReadError as exc:
            print(f"  [ABORT] ReadError at {q}: {exc}")
            break
        except Exception as exc:  # noqa: BLE001
            rows.append({"company": q, "query": q, "parse_status": "EXC", "error": str(exc)[:60]})
        await asyncio.sleep(SLEEP_COMPANY)
        if (i + 1) % BATCH == 0:
            calls = client.api_call_snapshot() - calls0
            print(f"  {i+1}/{len(universe)} 누적콜={calls} {(time.time()-t0)/60:.1f}분")
            await asyncio.sleep(SLEEP_BATCH)

    counts = Counter(r.get("parse_status") for r in rows)
    samples = {}
    for st in counts:
        if st == "ok":
            continue
        samples[st] = [
            {"company": r["company"], "cur": r.get("cur"), "pri": r.get("pri"),
             "raw_cur": r.get("raw_cur"), "raw_pri": r.get("raw_pri")}
            for r in rows if r.get("parse_status") == st
        ][:8]
    result = {
        "meta": {"date": "2026-06-15", "universe": f"{UNIVERSE_FILE} ({len(rows)}사)",
                 "total_dart_calls": client.api_call_snapshot() - calls0,
                 "elapsed_min": round((time.time() - t0) / 60, 1)},
        "status_counts": dict(counts),
        "failure_samples": samples,
        "rows": rows,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    total = len(rows)
    print(f"\n[완료] {total}사 — 유형별 빈도:")
    for st, c in counts.most_common():
        print(f"  {st}: {c} ({c/total*100:.0f}%)")
    print("\n실패 유형 샘플(raw):")
    for st, smp in samples.items():
        print(f"  [{st}]")
        for s in smp[:5]:
            print(f"    {s['company']}: cur={s['cur']} pri={s['pri']} raw_cur={s['raw_cur']!r} raw_pri={s['raw_pri']!r}")
    print(f"\n  총 DART콜 {result['meta']['total_dart_calls']}, {result['meta']['elapsed_min']}분 → {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
