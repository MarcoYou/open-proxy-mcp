"""법령 layer 회귀 spot — 자산 2조+ 30 회사.

shareholder_meeting_notice summary로 안건 list 추출 → _law_layer 적용 → hit 분포 통계.
proxy_advise 전체 호출(30s+) 대신 빠르게 (~3s/회사).
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
from open_proxy_mcp.services.proxy_advise import _law_layer  # noqa: E402
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload  # noqa: E402


def _flatten_agendas(items: list[dict], parent: str = "") -> list[dict]:
    out: list[dict] = []
    for it in items or []:
        title = (it.get("title") or "").strip()
        if title:
            out.append({"title": title, "parent_title": parent})
        out.extend(_flatten_agendas(it.get("children", []), parent=title))
    return out


async def _audit_one(ticker: str, name: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        t0 = time.time()
        try:
            # 자산 fetch (2조+ 검증용)
            fm = await asyncio.wait_for(
                build_financial_metrics_payload(name, scope="summary"),
                timeout=30.0,
            )
            fm_summary = ((fm or {}).get("data") or {}).get("summary") or {}
            total_assets = fm_summary.get("total_assets_krw")
            corp_asset = int(total_assets) if isinstance(total_assets, (int, float)) and total_assets > 0 else None

            # 안건 추출
            sm = await asyncio.wait_for(
                build_shareholder_meeting_payload(name, year=2026, scope="summary", meeting_type="annual"),
                timeout=60.0,
            )
            data = sm.get("data") or {}
            agendas_tree = data.get("agendas") or []
            flat = _flatten_agendas(agendas_tree)

            today_iso = date.today().isoformat()
            hits = []
            for entry in flat:
                hit = _law_layer(entry["title"], parent_title=entry["parent_title"],
                                 corp_total_asset_won=corp_asset, today_iso=today_iso)
                if hit:
                    hits.append({
                        "title": entry["title"],
                        "parent_title": entry["parent_title"],
                        "rule_id": hit[2],
                        "decision": hit[0],
                        "law_ref": hit[3],
                    })

            return {
                "ticker": ticker, "name": name,
                "status": sm.get("status"),
                "duration_s": round(time.time() - t0, 2),
                "corp_asset_won": corp_asset,
                "is_2tril_plus": (corp_asset or 0) >= 2_000_000_000_000,
                "n_agendas": len(flat),
                "n_law_hits": len(hits),
                "hits": hits,
            }
        except asyncio.TimeoutError:
            return {"ticker": ticker, "name": name, "status": "timeout",
                    "duration_s": round(time.time() - t0, 2)}
        except Exception as exc:
            return {"ticker": ticker, "name": name, "status": "exception",
                    "error": str(exc)[:200], "duration_s": round(time.time() - t0, 2)}


async def _run(args):
    # KOSPI 200 universe에서 상위 N (시총 기준 = 자산 2조+ 가능성 높음)
    universe_path = ROOT / "wiki/architecture/audits/data/260506_universe_kospi_200.csv"
    rows: list[tuple[str, str]] = []
    with universe_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append((r["ticker"], r["company"]))
    universe = rows[args.start:args.start + args.count]
    print(f"[law_layer spot] universe=kospi200 range={args.start}:{args.start + args.count} resolved={len(universe)}", flush=True)

    sem = asyncio.Semaphore(args.concurrency)
    t0 = time.time()
    results: list[dict] = []
    tasks = [_audit_one(t, n, sem) for t, n in universe]
    for fut in asyncio.as_completed(tasks):
        r = await fut
        results.append(r)
        marker = "✓" if r.get("status") == "exact" else f"✗{r.get('status')}"
        print(f"  [{len(results)}/{len(universe)}] {r.get('ticker')} {r.get('name')} {marker} "
              f"({r.get('duration_s', 0)}s, agendas={r.get('n_agendas', 0)}, hits={r.get('n_law_hits', 0)}, "
              f"2조+={r.get('is_2tril_plus', False)})", flush=True)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "meta": {
            "start": args.start,
            "count": args.count,
            "duration_s": round(time.time() - t0, 1),
            "generated_at": date.today().isoformat(),
        },
        "records": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] wrote {out_path}", flush=True)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--count", type=int, default=30)
    p.add_argument("--concurrency", type=int, default=2)
    p.add_argument("--output", required=True)
    return asyncio.run(_run(p.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
