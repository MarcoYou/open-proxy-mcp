"""proxy_contest filer 3-way 분류 audit (260508 ralph 2 phase 3).

각 회사 proxy_contest scope=fight 호출 → 위임장 filer + 분류 결과 수집.
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.services.proxy_contest import (  # noqa: E402
    _is_company_side,
    _is_retail_activism_side,
    build_proxy_contest_payload,
)

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


async def _audit_one(ticker: str, name: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        t0 = time.time()
        try:
            payload = await asyncio.wait_for(
                build_proxy_contest_payload(name, scope="fight"),
                timeout=60.0,
            )
            data = payload.get("data") or {}
            fight = data.get("fight") or {}
            filings = fight.get("proxy_filings") or []
            classifications = []
            for f in filings:
                filer = f.get("filer_name") or ""
                computed_company = _is_company_side(filer, name)
                computed_retail = _is_retail_activism_side(filer)
                # 실제 service에서 부여한 side label
                actual_side = f.get("filer_side") or ""
                classifications.append({
                    "filer_name": filer,
                    "computed_is_company": computed_company,
                    "computed_is_retail": computed_retail,
                    "actual_side": actual_side,
                    "rcept_no": f.get("rcept_no") or "",
                    "report_name": f.get("report_nm") or "",
                })
            return {
                "ticker": ticker, "name": name, "status": payload.get("status"),
                "duration_s": round(time.time() - t0, 2),
                "classifications": classifications,
                "n_filings": len(classifications),
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


async def _run(args):
    universe = _load_universe(args.universe, args.start, args.count)
    print(f"[filer audit] universe={args.universe} range={args.start}:{args.start + args.count} resolved={len(universe)}", flush=True)
    sem = asyncio.Semaphore(args.concurrency)

    t0 = time.time()
    results: list[dict] = []
    tasks = [_audit_one(t, n, sem) for t, n in universe]
    for fut in asyncio.as_completed(tasks):
        r = await fut
        results.append(r)
        marker = "✓" if r.get("status") == "exact" else f"✗{r.get('status')}"
        n_filings = r.get("n_filings", 0)
        print(f"  [{len(results)}/{len(universe)}] {r.get('ticker')} {r.get('name')} {marker} "
              f"({r.get('duration_s', 0)}s, filings={n_filings})", flush=True)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "meta": {
            "universe": args.universe,
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
    p.add_argument("--universe", required=True, choices=list(UNIVERSE_PATHS))
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--count", type=int, default=30)
    p.add_argument("--concurrency", type=int, default=2)
    p.add_argument("--output", required=True)
    return asyncio.run(_run(p.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
