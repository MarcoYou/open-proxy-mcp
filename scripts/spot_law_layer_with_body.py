"""법령 layer 회귀 spot — D 패턴 body fallback 포함 (Ralph 7 iter 4).

기존 spot_law_layer.py는 _law_layer (title only)만 호출.
이 스크립트는 +D 패턴 _law_layer_body fallback 포함 — full proxy_advise 동등 catch.

회귀 검증:
- title hits: 기존 _law_layer만으로 잡은 hits (회귀 비교 기준)
- body hits: D 패턴 fallback으로 추가 catch한 hits (신규)
- 510 회사 (kospi_200 + kosdaq_150 + kosdaq_151-300 + dispute_30) 처리
"""
from __future__ import annotations
import argparse
import asyncio
import csv
import json
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.services.financial_metrics import build_financial_metrics_payload  # noqa: E402
from open_proxy_mcp.services.proxy_advise import (  # noqa: E402
    _is_charter_top, _law_layer, _law_layer_body,
)
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload  # noqa: E402


def _walk(items, parent="", out=None):
    if out is None:
        out = []
    for it in items or []:
        t = (it.get("title") or "").strip()
        if t:
            out.append({"title": t, "parent": parent, "n_children": len(it.get("children") or [])})
        _walk(it.get("children", []), parent=t, out=out)
    return out


async def _audit_one(ticker: str, name: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        t0 = time.time()
        try:
            fm = await asyncio.wait_for(
                build_financial_metrics_payload(name, scope="summary"),
                timeout=30.0,
            )
            fm_summary = ((fm or {}).get("data") or {}).get("summary") or {}
            ta = fm_summary.get("total_assets_krw")
            corp_asset = int(ta) if isinstance(ta, (int, float)) and ta > 0 else None

            sm_sum = await asyncio.wait_for(
                build_shareholder_meeting_payload(name, year=2026, scope="summary", meeting_type="annual"),
                timeout=60.0,
            )
            agendas = (sm_sum.get("data") or {}).get("agendas") or []
            flat = _walk(agendas)
            today_iso = date.today().isoformat()

            title_hits = []
            for entry in flat:
                hit = _law_layer(entry["title"], parent_title=entry["parent"],
                                 corp_total_asset_won=corp_asset, today_iso=today_iso)
                if hit:
                    title_hits.append({
                        "title": entry["title"], "rule_id": hit[2], "decision": hit[0],
                    })

            # D 패턴 fallback
            body_hits = []
            d_entries = []
            charter_top_no_children = [
                e for e in flat
                if e["parent"] == "" and _is_charter_top(e["title"]) and e["n_children"] == 0
            ]
            if charter_top_no_children:
                # amendments
                try:
                    sm_aoi = await asyncio.wait_for(
                        build_shareholder_meeting_payload(name, year=2026, scope="aoi_change", meeting_type="annual"),
                        timeout=60.0,
                    )
                    amendments = ((sm_aoi.get("data") or {}).get("aoi_change") or {}).get("amendments") or []
                except Exception:
                    amendments = []

                for e in charter_top_no_children:
                    if not amendments:
                        continue
                    d_entries.append(e["title"])
                    body_hit = _law_layer_body(
                        amendments, parent_title=e["title"],
                        corp_total_asset_won=corp_asset, today_iso=today_iso,
                    )
                    if body_hit:
                        body_hits.append({
                            "title": e["title"], "rule_id": body_hit[2], "decision": body_hit[0],
                        })

            return {
                "ticker": ticker, "name": name, "status": "ok",
                "duration_s": round(time.time() - t0, 2),
                "n_agendas": len(flat),
                "n_title_hits": len(title_hits),
                "title_hits": title_hits,
                "n_d_entries": len(d_entries),
                "d_entries": d_entries,
                "n_body_hits": len(body_hits),
                "body_hits": body_hits,
            }
        except asyncio.TimeoutError:
            return {"ticker": ticker, "name": name, "status": "timeout",
                    "duration_s": round(time.time() - t0, 2)}
        except Exception as exc:
            return {"ticker": ticker, "name": name, "status": "exception",
                    "error": str(exc)[:200], "duration_s": round(time.time() - t0, 2)}


async def _run(args):
    universe_map = {
        "kospi200": ROOT / "wiki/architecture/audits/data/260506_universe_kospi_200.csv",
        "kosdaq150": ROOT / "wiki/architecture/audits/data/260506_universe_kosdaq_150.csv",
        "kosdaq300": ROOT / "wiki/architecture/audits/data/260506_universe_kosdaq_300.csv",
        "dispute": ROOT / "wiki/architecture/audits/data/260510_law_layer_body/dispute_new_10.csv",
    }
    if args.universe in universe_map:
        path = universe_map[args.universe]
    elif Path(args.universe).is_file():
        path = Path(args.universe)
    else:
        print(f"unknown universe: {args.universe}")
        return

    with open(path) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        if args.skip:
            rows = rows[args.skip:]
        if args.limit:
            rows = rows[:args.limit]

    print(f"universe: {path.name} (n={len(rows)})")
    sem = asyncio.Semaphore(args.concurrency)
    results = []
    completed = 0
    for chunk_start in range(0, len(rows), 30):
        chunk = rows[chunk_start:chunk_start+30]
        chunk_results = await asyncio.gather(*[
            _audit_one(r["ticker"], r["company"], sem) for r in chunk
        ])
        results.extend(chunk_results)
        completed += len(chunk)
        n_title = sum(r.get("n_title_hits", 0) for r in results)
        n_body = sum(r.get("n_body_hits", 0) for r in results)
        print(f"  done {completed}/{len(rows)} — title hits={n_title} body hits={n_body}")
        if chunk_start + 30 < len(rows):
            await asyncio.sleep(2)  # batch 사이 sleep

    archive = ROOT / "wiki/architecture/audits/data/260510_agenda_hierarchy"
    archive.mkdir(parents=True, exist_ok=True)
    out = archive / f"iter4_spot_{args.universe}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    n_title = sum(r.get("n_title_hits", 0) for r in results)
    n_body = sum(r.get("n_body_hits", 0) for r in results)
    n_d = sum(r.get("n_d_entries", 0) for r in results)
    body_companies = [r for r in results if r.get("n_body_hits", 0) > 0]
    print(f"\n=== {args.universe} 결과 ===")
    print(f"  total companies: {len(results)}")
    print(f"  title hits: {n_title}")
    print(f"  D 패턴 진입: {n_d}")
    print(f"  body hits (신규): {n_body} (across {len(body_companies)} companies)")
    print(f"  saved: {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", required=True)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--skip", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=2)
    args = ap.parse_args()
    asyncio.run(_run(args))
