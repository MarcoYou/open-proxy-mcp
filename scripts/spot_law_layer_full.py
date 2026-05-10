"""법령 layer 회귀 spot — Ralph 7 D 패턴 + Ralph 8 카카오게임즈 패턴 모두 포함.

- title hits: _law_layer (title 매칭)
- body hits: D 패턴 fallback (top + children 0 + amendments)
- sub hits: 카카오게임즈 패턴 fallback (sub + parent 정관변경 + clause/label 매핑)
- cross-match 회피: 매핑된 amendment idx 회사별 track
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
    _is_charter_top, _is_generic_sub,
    _law_layer, _law_layer_body, _law_layer_subagenda_mapped,
    _map_subagenda_to_amendment,
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
                build_financial_metrics_payload(name, scope="summary"), timeout=30.0)
            ta = (((fm or {}).get("data") or {}).get("summary") or {}).get("total_assets_krw")
            asset = int(ta) if isinstance(ta, (int, float)) and ta > 0 else None

            sm = await asyncio.wait_for(
                build_shareholder_meeting_payload(name, year=2026, scope="summary", meeting_type="annual"),
                timeout=60.0)
            agendas = (sm.get("data") or {}).get("agendas") or []
            flat = _walk(agendas)
            today_iso = date.today().isoformat()

            # amendments — 정관변경 안건 있을 때만 호출
            amendments = []
            need_aoi = any(
                (it["parent"] == "" and _is_charter_top(it["title"]))
                or (_is_charter_top(it["parent"]))
                for it in flat
            )
            if need_aoi:
                try:
                    sm_aoi = await asyncio.wait_for(
                        build_shareholder_meeting_payload(name, year=2026, scope="aoi_change", meeting_type="annual"),
                        timeout=60.0)
                    amendments = ((sm_aoi.get("data") or {}).get("aoi_change") or {}).get("amendments") or []
                except Exception:
                    amendments = []

            title_hits, body_hits, sub_hits = [], [], []
            used: set[int] = set()

            for it in flat:
                title, parent, nc = it["title"], it["parent"], it["n_children"]

                hit = _law_layer(title, parent_title=parent,
                                corp_total_asset_won=asset, today_iso=today_iso)
                if hit:
                    title_hits.append({"title": title, "rule_id": hit[2], "decision": hit[0]})
                    continue

                # D 패턴
                if (parent == "" and _is_charter_top(title) and nc == 0 and amendments):
                    hit = _law_layer_body(amendments, parent_title=title,
                                         corp_total_asset_won=asset, today_iso=today_iso)
                    if hit:
                        body_hits.append({"title": title, "rule_id": hit[2], "decision": hit[0]})
                        continue

                # 카카오게임즈 패턴
                if (parent and _is_charter_top(parent) and nc == 0
                    and amendments and not _is_generic_sub(title)):
                    midx = _map_subagenda_to_amendment(title, amendments, used)
                    if midx is not None:
                        hit = _law_layer_subagenda_mapped(
                            title, amendments[midx], parent_title=parent,
                            corp_total_asset_won=asset, today_iso=today_iso)
                        if hit:
                            sub_hits.append({
                                "title": title, "rule_id": hit[2], "decision": hit[0],
                                "mapped_idx": midx,
                            })
                            used.add(midx)

            return {
                "ticker": ticker, "name": name, "status": "ok",
                "duration_s": round(time.time() - t0, 2),
                "n_agendas": len(flat), "n_amendments": len(amendments),
                "n_title_hits": len(title_hits), "title_hits": title_hits,
                "n_body_hits": len(body_hits), "body_hits": body_hits,
                "n_sub_hits": len(sub_hits), "sub_hits": sub_hits,
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
    else:
        print(f"unknown universe")
        return

    with open(path) as f:
        rows = list(csv.DictReader(f))
        if args.skip:
            rows = rows[args.skip:]
        if args.limit:
            rows = rows[:args.limit]

    print(f"universe: {path.name} (n={len(rows)})")
    sem = asyncio.Semaphore(args.concurrency)
    results = []
    for chunk_start in range(0, len(rows), 30):
        chunk = rows[chunk_start:chunk_start+30]
        chunk_results = await asyncio.gather(*[
            _audit_one(r["ticker"], r["company"], sem) for r in chunk])
        results.extend(chunk_results)
        n_t = sum(r.get("n_title_hits", 0) for r in results)
        n_b = sum(r.get("n_body_hits", 0) for r in results)
        n_s = sum(r.get("n_sub_hits", 0) for r in results)
        print(f"  done {chunk_start+len(chunk)}/{len(rows)} — t={n_t} b={n_b} s={n_s}")
        if chunk_start + 30 < len(rows):
            await asyncio.sleep(2)

    archive = ROOT / "wiki/architecture/audits/data/260510_subagenda_mapping"
    archive.mkdir(parents=True, exist_ok=True)
    out = archive / f"iter4_spot_{args.universe}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    n_t = sum(r.get("n_title_hits", 0) for r in results)
    n_b = sum(r.get("n_body_hits", 0) for r in results)
    n_s = sum(r.get("n_sub_hits", 0) for r in results)
    sub_co = len([r for r in results if r.get("n_sub_hits", 0) > 0])
    print(f"\n=== {args.universe} ===")
    print(f"  total: {len(results)} | title {n_t} | body {n_b} | sub {n_s} ({sub_co} co)")
    print(f"  saved: {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", required=True)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--skip", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=2)
    args = ap.parse_args()
    asyncio.run(_run(args))
