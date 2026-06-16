"""탑다운 스크리닝 실현가능성 — 이벤트 기반(주주제안).

질문: "2026 정기주총에서 주주제안 있었던 기업들"을 현재 툴로 실행하면?
방법: universe 500사를 shareholder_meeting(summary)로 순회 → agenda의 proposer_type=
shareholder_proposal 검출.
측정: 기업당 콜 수, 총 콜, 시간, 주주제안 검출 결과 → 이벤트 기반 탑다운 비용.

비교: 지표 기반(financial_metrics 14콜/사)보다 가벼운지. list.json 시장검색으로 후보를
좁힐 수 있는지(주총 공고는 거의 모든 상장사에 있어 좁히기 효과 제한적인지) 실증.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import httpx

from open_proxy_mcp.dart.client import get_dart_client
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload as sm

UNIVERSE_FILE = os.environ.get("UNIVERSE_FILE", "/tmp/topdown_universe.json")
OUT = Path(os.environ.get("AUDIT_OUT", "wiki/architecture/audits/data/260617_topdown_event_feasibility.json"))
BATCH = 25
SLEEP_COMPANY = 0.4
SLEEP_BATCH = 18.0
LIMIT = int(os.environ.get("LIMIT", "500"))


def _agendas(d: dict) -> list:
    return d.get("agendas") or d.get("agenda") or []


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
    rows, matched, no_agenda, errors = [], [], 0, 0
    print(f"[탑다운 이벤트 실현가능성] {len(companies)}사 — shareholder_meeting 순회 → 주주제안 검출")

    for i, q in enumerate(companies):
        try:
            c0 = client.api_call_snapshot()
            p = await sm(q, scope="summary", year=2026, meeting_type="annual")
            calls = client.api_call_snapshot() - c0
            d = p.get("data") or {}
            ags = _agendas(d)

            def _walk(nodes):
                out = []
                for n in nodes:
                    out.append(n)
                    out.extend(_walk(n.get("children") or []))
                return out

            all_ag = _walk(ags)
            proposals = [a for a in all_ag if a.get("proposer_type") == "shareholder_proposal"]
            row = {"company": q, "market": market.get(q, "?"), "calls": calls,
                   "status": str(p.get("status")), "agenda_count": len(all_ag),
                   "proposal_count": len(proposals)}
            if not ags:
                no_agenda += 1
            if proposals:
                matched.append({"company": q, "market": market.get(q, "?"),
                                "proposals": [a.get("title", "")[:40] for a in proposals[:3]]})
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
            print(f"  {i+1}/{len(companies)} 누적콜={tot} 경과={el/60:.1f}분 분당={rate:.0f}/910 주주제안={len(matched)}")
            await asyncio.sleep(SLEEP_BATCH)

    total_calls = client.api_call_snapshot() - calls0
    elapsed = time.time() - t0
    done = len([r for r in rows if r.get("status") != "EXC"])
    avg_calls = round(total_calls / max(done, 1), 1)
    result = {
        "meta": {"date": "2026-06-17", "query": "2026 정기주총 주주제안 검출",
                 "universe": f"{len(rows)}사 (코스피300+코스닥200)",
                 "total_dart_calls": total_calls, "elapsed_min": round(elapsed / 60, 1),
                 "avg_calls_per_company": avg_calls},
        "feasibility": {
            "companies_scanned": done,
            "total_calls_for_one_query": total_calls,
            "avg_calls_per_company": avg_calls,
            "no_agenda": no_agenda, "errors": errors,
            "matched_count": len(matched),
        },
        "matched": matched,
        "rows": rows,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n[완료] {done}사 스캔 / 주주제안 기업 {len(matched)}")
    print(f"  ★ 탑다운 1쿼리 비용: 총 {total_calls}콜 / {elapsed/60:.1f}분 / 평균 {avg_calls}콜·사")
    print(f"  no_agenda {no_agenda}, errors {errors}")
    print(f"  주주제안 기업: {[m['company'] for m in matched][:12]}")
    print(f"  → {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
