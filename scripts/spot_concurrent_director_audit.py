"""사외이사 careerDetails 겸직 데이터 가용성 audit (510 회사 광범위).

각 회사:
- 사외이사 후보 careerDetails 수집
- 겸직 신호 키워드 detect:
  · period에 "현재" / "현" 포함
  · content에 "사외이사" / "독립이사" 키워드
- 회사 / 후보별 빈도 측정 → DART 데이터로 겸직 자동 카운트 실현 가능성 평가
"""
from __future__ import annotations
import argparse
import asyncio
import csv
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.services.company import resolve_company_query  # noqa: E402
from open_proxy_mcp.services.director_evaluation import fetch_appointments  # noqa: E402


def _is_current(period: str) -> bool:
    """period에 현재 마커 포함 여부."""
    if not period:
        return False
    return any(k in period for k in ("현재", "현직", "재직"))


def _detect_outside_director(content: str) -> bool:
    """content에 사외이사/독립이사 키워드."""
    if not content:
        return False
    return any(k in content for k in ("사외이사", "독립이사", "사외 이사", "독립 이사"))


async def _audit_one(ticker: str, name: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        t0 = time.time()
        try:
            res = await asyncio.wait_for(resolve_company_query(name), timeout=30.0)
            if not res.selected:
                return {"ticker": ticker, "name": name, "status": "no_match"}
            appointments, rcept_no, _ = await asyncio.wait_for(
                fetch_appointments(res.selected['corp_code'], 2026, 'annual'),
                timeout=60.0,
            )
        except Exception as exc:
            return {"ticker": ticker, "name": name, "status": "error",
                    "error": str(exc)[:100]}

        n_outside = 0
        n_with_career = 0
        n_current_marker = 0
        n_outside_keyword = 0
        concurrent_signals = []
        for ap in appointments:
            for c in (ap.get('candidates') or []):
                role = c.get('roleType') or ''
                if not any(k in role for k in ('사외', '독립')):
                    continue
                n_outside += 1
                cds = c.get('careerDetails') or []
                if cds:
                    n_with_career += 1
                has_current = False
                has_outside = False
                concurrent_text = []
                for cd in cds:
                    period = cd.get('period', '') or ''
                    content = cd.get('content', '') or ''
                    is_cur = _is_current(period)
                    is_out = _detect_outside_director(content)
                    if is_cur:
                        has_current = True
                    if is_out:
                        has_outside = True
                    if is_cur and is_out:
                        concurrent_text.append(f"{period} | {content[:80]}")
                if has_current:
                    n_current_marker += 1
                if has_outside:
                    n_outside_keyword += 1
                if concurrent_text:
                    concurrent_signals.append({
                        "name": c.get('name'),
                        "signals": concurrent_text[:3],
                    })

        return {
            "ticker": ticker, "name": name, "status": "ok",
            "duration_s": round(time.time() - t0, 2),
            "n_outside_candidates": n_outside,
            "n_with_career": n_with_career,
            "n_current_marker": n_current_marker,
            "n_outside_keyword": n_outside_keyword,
            "concurrent_signals": concurrent_signals,
        }


async def _run(args):
    universe_csvs = [
        ROOT / "wiki/architecture/audits/data/260506_universe_kospi_200.csv",
        ROOT / "wiki/architecture/audits/data/260506_universe_kosdaq_150.csv",
        ROOT / "wiki/architecture/audits/data/260506_universe_kosdaq_300.csv",
    ]
    rows = []
    for path in universe_csvs:
        with open(path) as f:
            reader = csv.DictReader(f)
            this = list(reader)
            if "kosdaq_300" in str(path):
                this = this[150:]
            rows.extend(this)
    seen = set()
    unique_rows = []
    for r in rows:
        if r["ticker"] not in seen:
            seen.add(r["ticker"])
            unique_rows.append(r)
    if args.limit:
        unique_rows = unique_rows[:args.limit]

    print(f"audit n={len(unique_rows)}")
    sem = asyncio.Semaphore(args.concurrency)
    results = []
    for chunk_start in range(0, len(unique_rows), 30):
        chunk = unique_rows[chunk_start:chunk_start+30]
        chunk_results = await asyncio.gather(*[
            _audit_one(r["ticker"], r["company"], sem) for r in chunk])
        results.extend(chunk_results)
        ok = [r for r in results if r.get("status") == "ok"]
        n_outside = sum(r.get("n_outside_candidates", 0) for r in ok)
        n_concurrent = sum(len(r.get("concurrent_signals") or []) for r in ok)
        n_co_with = len([r for r in ok if r.get("concurrent_signals")])
        print(f"  done {chunk_start+len(chunk)}/{len(unique_rows)} — outside={n_outside}, concurrent후보={n_concurrent}, 회사={n_co_with}")
        if chunk_start + 30 < len(unique_rows):
            await asyncio.sleep(2)

    archive = ROOT / "wiki/architecture/audits/data/260510_director_faithfulness"
    archive.mkdir(parents=True, exist_ok=True)
    out = archive / "iter1_concurrent_audit.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = [r for r in results if r.get("status") == "ok"]
    n_outside = sum(r.get("n_outside_candidates", 0) for r in ok)
    n_with_career = sum(r.get("n_with_career", 0) for r in ok)
    n_current = sum(r.get("n_current_marker", 0) for r in ok)
    n_outside_kw = sum(r.get("n_outside_keyword", 0) for r in ok)
    n_concurrent_signals = sum(len(r.get("concurrent_signals") or []) for r in ok)
    n_co_with = len([r for r in ok if r.get("concurrent_signals")])
    print(f"\n=== 510 회사 audit 결과 ===")
    print(f"  total companies: {len(ok)}")
    print(f"  사외이사 후보 총: {n_outside}")
    print(f"  careerDetails 있는 후보: {n_with_career} ({100*n_with_career/n_outside if n_outside else 0:.1f}%)")
    print(f"  '현재' 마커 있는 후보: {n_current}")
    print(f"  '사외이사' 키워드 있는 후보: {n_outside_kw}")
    print(f"  ★ 겸직 신호 (현재 + 사외이사 동시) 후보: {n_concurrent_signals}")
    print(f"  ★ 겸직 신호 있는 회사: {n_co_with} / {len(ok)} ({100*n_co_with/len(ok) if ok else 0:.1f}%)")
    print(f"\n  saved: {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=3)
    args = ap.parse_args()
    asyncio.run(_run(args))
