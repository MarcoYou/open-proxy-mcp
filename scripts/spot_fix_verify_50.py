"""묶음 후보 detail + raw 첨부 fix 광범위 검증 (50 회사)."""
from __future__ import annotations
import argparse
import asyncio
import csv
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.services.proxy_advise import build_proxy_advise_payload  # noqa: E402


async def _check(ticker: str, name: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        t0 = time.time()
        try:
            result = await asyncio.wait_for(
                build_proxy_advise_payload(name, year=2026, scope='decisions', meeting_type='annual'),
                timeout=120.0,
            )
        except Exception as exc:
            return {"ticker": ticker, "name": name, "status": "error", "error": str(exc)[:100]}

        decisions = (result.get('data') or {}).get('agenda_decisions') or []

        bundle_count = 0
        bundle_total_candidates = 0
        raw_full = 0
        raw_mapped = 0
        raw_anchor = 0
        full_total_chars = 0
        for d in decisions:
            facts = d.get('facts') or {}
            cs = facts.get('candidate_summary') or []
            if cs:
                bundle_count += 1
                bundle_total_candidates += len(cs)
            reason = d.get('reason') or ''
            if '📄' in reason:
                if '같은 회사의 다른 정관변경' in reason:
                    raw_anchor += 1
                elif 'sub-agenda 매핑된' in reason:
                    raw_mapped += 1
                else:
                    raw_full += 1
                    idx = reason.find('📄')
                    full_total_chars += len(reason) - idx

        return {
            "ticker": ticker, "name": name, "status": "ok",
            "duration_s": round(time.time() - t0, 2),
            "n_agendas": len(decisions),
            "bundle_count": bundle_count,
            "bundle_total_candidates": bundle_total_candidates,
            "raw_full": raw_full,
            "raw_mapped": raw_mapped,
            "raw_anchor": raw_anchor,
            "full_total_chars": full_total_chars,
        }


async def _run(args):
    universe_csv = ROOT / "wiki/architecture/audits/data/260506_universe_kospi_200.csv"
    with open(universe_csv) as f:
        rows = list(csv.DictReader(f))[:args.limit]

    print(f"audit n={len(rows)}")
    sem = asyncio.Semaphore(args.concurrency)
    results = []
    for chunk_start in range(0, len(rows), 20):
        chunk = rows[chunk_start:chunk_start+20]
        chunk_results = await asyncio.gather(*[
            _check(r["ticker"], r["company"], sem) for r in chunk])
        results.extend(chunk_results)
        ok = [r for r in results if r.get("status") == "ok"]
        print(f"  done {chunk_start+len(chunk)}/{len(rows)} — bundle 안건 {sum(r['bundle_count'] for r in ok)}, raw_full {sum(r['raw_full'] for r in ok)}, mapped {sum(r['raw_mapped'] for r in ok)}, anchor {sum(r['raw_anchor'] for r in ok)}")
        if chunk_start + 20 < len(rows):
            await asyncio.sleep(2)

    archive = ROOT / "wiki/architecture/audits/data/260510_fix_verify"
    archive.mkdir(parents=True, exist_ok=True)
    out = archive / f"verify_50.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = [r for r in results if r.get("status") == "ok"]
    err = [r for r in results if r.get("status") != "ok"]
    bundle_total = sum(r['bundle_count'] for r in ok)
    bundle_cands = sum(r['bundle_total_candidates'] for r in ok)
    raw_f = sum(r['raw_full'] for r in ok)
    raw_m = sum(r['raw_mapped'] for r in ok)
    raw_a = sum(r['raw_anchor'] for r in ok)
    chars = sum(r['full_total_chars'] for r in ok)
    co_with_bundle = len([r for r in ok if r['bundle_count'] > 0])
    co_with_raw = len([r for r in ok if (r['raw_full'] + r['raw_mapped'] + r['raw_anchor']) > 0])
    co_with_mapped = len([r for r in ok if r['raw_mapped'] > 0])

    print(f"\n=== Fix verify 50 회사 ===")
    print(f"  total: {len(results)} ({len(ok)} ok, {len(err)} error)")
    print(f"\n  [묶음 후보 detail]")
    print(f"    묶음 안건 있는 회사: {co_with_bundle} / {len(ok)}")
    print(f"    총 묶음 안건: {bundle_total}")
    print(f"    총 후보 detail 노출: {bundle_cands}명")
    print(f"\n  [raw 첨부 logic]")
    print(f"    raw 첨부 회사: {co_with_raw} / {len(ok)}")
    print(f"    매핑된 sub raw 회사: {co_with_mapped}")
    print(f"    full (회사 1번 모든 amendments): {raw_f}")
    print(f"    mapped (sub 매핑 amendment 1개): {raw_m}")
    print(f"    anchor (중복 회피): {raw_a}")
    print(f"    full 총 토큰: {chars:,}자")
    if raw_a > 0:
        # 만약 anchor 없이 모두 full이었다면 토큰 곱하기 추정
        # 평균 full 길이 × (full + anchor) = anchor 없이 첨부 시 토큰
        avg = chars / raw_f if raw_f else 0
        without_anchor = chars + (raw_a * avg)
        saved = without_anchor - chars - (raw_a * 50)  # anchor 50자 추정
        print(f"    토큰 절약 (anchor 효과): {saved:,.0f}자 (~{100*saved/without_anchor if without_anchor else 0:.0f}%)")

    if err:
        print(f"\n  [에러 회사]")
        for r in err[:5]:
            print(f"    {r['name']}: {r.get('error', r.get('status'))}")
    print(f"\n  saved: {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--concurrency", type=int, default=2)
    args = ap.parse_args()
    asyncio.run(_run(args))
