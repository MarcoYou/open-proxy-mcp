"""_classify_agenda 분류 정확도 audit (260507 ralph).

회사당 1 doc fetch (shareholder_meeting_notice summary) → agenda hierarchy flatten
→ 각 안건 title마다 _classify_agenda 적용 → parent 정보 함께 수집.

DART 호출 ~3/회사 (corp_code lookup 0 + search 1 + doc 1-2).

사용법:
  uv run python scripts/spot_classify_agenda.py \\
      --universe kospi200 --start 0 --count 30 \\
      --output wiki/architecture/audits/data/260507_classify_agenda/iter01_kospi_0-30.json
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time
import traceback
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.services.proxy_advise import _classify_agenda  # noqa: E402
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload  # noqa: E402

UNIVERSE_PATHS = {
    "kospi200": ROOT / "wiki/architecture/audits/data/260506_universe_kospi_200.csv",
    "kosdaq100": ROOT / "wiki/architecture/audits/data/260506_universe_kosdaq_100.csv",
}


def _load_universe(name: str, start: int, count: int) -> list[tuple[str, str]]:
    path = UNIVERSE_PATHS[name]
    rows: list[tuple[str, str]] = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append((row["ticker"], row["company"]))
    return rows[start:start + count]


def _flatten_agendas(items: list[dict], parent_title: str = "") -> list[dict]:
    """agenda hierarchy → flat list with parent info."""
    out: list[dict] = []
    for it in items or []:
        number = it.get("number") or ""
        title = (it.get("title") or "").strip()
        out.append({
            "number": number,
            "title": title,
            "parent_title": parent_title,
            "is_sub": bool(parent_title),
        })
        out.extend(_flatten_agendas(it.get("children", []), parent_title=title))
    return out


async def _audit_one(ticker: str, name: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        t0 = time.time()
        try:
            payload = await asyncio.wait_for(
                build_shareholder_meeting_payload(
                    name, year=2026, scope="summary", meeting_type="annual",
                ),
                timeout=60.0,
            )
            data = payload.get("data") or {}
            agendas_tree = data.get("agendas") or []
            flat = _flatten_agendas(agendas_tree)
            for entry in flat:
                entry["category"] = _classify_agenda(entry["title"])
                # parent 기반 expected (단순 rule): parent에 "정관" 있으면 articles_amendment
                parent = entry.get("parent_title", "") or ""
                if "정관" in parent and "정관" not in entry["title"]:
                    entry["parent_implies_articles"] = True
                    if entry["category"] != "articles_amendment":
                        entry["mismatch"] = True
                else:
                    entry["parent_implies_articles"] = False
            return {
                "ticker": ticker,
                "name": name,
                "status": payload.get("status"),
                "duration_s": round(time.time() - t0, 2),
                "agendas": flat,
            }
        except asyncio.TimeoutError:
            return {"ticker": ticker, "name": name, "status": "timeout",
                    "duration_s": round(time.time() - t0, 2)}
        except Exception as exc:
            return {
                "ticker": ticker, "name": name, "status": "exception",
                "error_type": type(exc).__name__,
                "error": str(exc)[:200],
                "traceback": traceback.format_exc(limit=3)[:500],
                "duration_s": round(time.time() - t0, 2),
            }


async def _run(args: argparse.Namespace) -> int:
    universe = _load_universe(args.universe, args.start, args.count)
    print(f"[classify_agenda audit] universe={args.universe} range={args.start}:{args.start + args.count} "
          f"resolved={len(universe)}", flush=True)
    sem = asyncio.Semaphore(args.concurrency)

    t0 = time.time()
    results: list[dict] = []
    tasks = [_audit_one(t, n, sem) for t, n in universe]
    for fut in asyncio.as_completed(tasks):
        r = await fut
        results.append(r)
        marker = "✓" if r.get("status") == "exact" else f"✗{r.get('status')}"
        n_agendas = len(r.get("agendas") or [])
        n_mismatch = sum(1 for a in (r.get("agendas") or []) if a.get("mismatch"))
        print(f"  [{len(results)}/{len(universe)}] {r.get('ticker')} {r.get('name')} {marker} "
              f"({r.get('duration_s', 0)}s, agendas={n_agendas}, mismatch={n_mismatch})",
              flush=True)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "universe": args.universe,
            "start": args.start,
            "count": args.count,
            "concurrency": args.concurrency,
            "duration_s": round(time.time() - t0, 1),
            "generated_at": date.today().isoformat(),
        },
        "records": results,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] wrote {out_path}", flush=True)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--universe", required=True, choices=list(UNIVERSE_PATHS))
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--count", type=int, default=30)
    p.add_argument("--concurrency", type=int, default=2)
    p.add_argument("--output", required=True)
    return asyncio.run(_run(p.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
