"""value_up 공시 분류기 audit (260508 ralph 2).

각 회사 value_up scope=timeline 호출 → 모든 filings의 report_nm + 분류 결과 수집.

DART 호출 ~3-5/회사 (search + KIND fallback if needed).
사용법:
  uv run python scripts/spot_classify_value_up.py \\
      --universe kospi200 --start 0 --count 30 \\
      --output wiki/architecture/audits/data/260508_classify_high_impact/iter01_kospi_0-30.json
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

from open_proxy_mcp.services.value_up_v2 import (  # noqa: E402
    _classify_value_up_item,
    _item_report_name,
    build_value_up_payload,
)

UNIVERSE_PATHS = {
    "kospi200": ROOT / "wiki/architecture/audits/data/260506_universe_kospi_200.csv",
    "kosdaq100": ROOT / "wiki/architecture/audits/data/260506_universe_kosdaq_100.csv",
}


def _expected_category(report_name: str) -> str:
    """ground truth rule (현재 _classify_value_up_item 와 동일)."""
    name = (report_name or "").replace(" ", "")
    if "고배당기업" in name or "고배당법인" in name:
        return "meta_amendment"
    if "이행현황" in name:
        return "progress"
    return "plan"


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
                build_value_up_payload(name, scope="timeline"),
                timeout=60.0,
            )
            data = payload.get("data") or {}
            items = data.get("items") or []
            classifications = []
            for it in items:
                rn = it.get("report_name") or it.get("report_nm") or ""
                cat = _classify_value_up_item(rn)
                expected = _expected_category(rn)
                classifications.append({
                    "report_name": rn,
                    "category": cat,
                    "expected": expected,
                    "match": cat == expected,
                    "rcept_no": it.get("rcept_no") or it.get("acptno") or "",
                    "source": it.get("source") or ("DART" if it.get("rcept_no") else "KIND"),
                })
            return {
                "ticker": ticker,
                "name": name,
                "status": payload.get("status"),
                "duration_s": round(time.time() - t0, 2),
                "classifications": classifications,
                "n_items": len(classifications),
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
    print(f"[value_up audit] universe={args.universe} range={args.start}:{args.start + args.count} resolved={len(universe)}", flush=True)
    sem = asyncio.Semaphore(args.concurrency)

    t0 = time.time()
    results: list[dict] = []
    tasks = [_audit_one(t, n, sem) for t, n in universe]
    for fut in asyncio.as_completed(tasks):
        r = await fut
        results.append(r)
        marker = "✓" if r.get("status") == "exact" else f"✗{r.get('status')}"
        n_items = r.get("n_items", 0)
        n_mismatch = sum(1 for c in (r.get("classifications") or []) if not c.get("match"))
        print(f"  [{len(results)}/{len(universe)}] {r.get('ticker')} {r.get('name')} {marker} "
              f"({r.get('duration_s', 0)}s, items={n_items}, mismatch={n_mismatch})", flush=True)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "meta": {
            "universe": args.universe,
            "start": args.start,
            "count": args.count,
            "concurrency": args.concurrency,
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
