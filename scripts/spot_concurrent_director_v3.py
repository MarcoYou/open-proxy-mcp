"""사외이사 겸직 v3 — 본 회사 포함 정확 카운트 (Ralph 9 iter 2).

logic:
  사외이사_총_갯수 = careerDetails 중 "현재 + 사외이사" 키워드 카운트
  if 본 회사명 careerDetails 표기 X:
      사외이사_총_갯수 += 1  # 후보 본인 본 회사 보장
  if 사외이사_총_갯수 >= 2:
      concerns
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

_CURRENT_KW = ("현재", "현직", "재직")
_OUTSIDE_KW_RE = re.compile(r'(?:사외|독립)\s*이사')
_NORMALIZE_RE = re.compile(r'[\s㈜㈱()주식회사]')


def _normalize(s: str) -> str:
    return _NORMALIZE_RE.sub('', s or '').lower()


def count_total_outside_director_positions(career_details, own_company_name: str) -> dict:
    """후보 사외이사 직책 총 갯수 (본 회사 자동 포함).

    return: {total: int, in_career: int, own_in_career: bool, signals: list[str]}
    """
    own_clean = _normalize(own_company_name)
    in_career = 0
    own_in_career = False
    signals = []
    for cd in career_details or []:
        period = cd.get('period', '') or ''
        content = cd.get('content', '') or ''
        if not any(k in period for k in _CURRENT_KW):
            continue
        # 사외이사 / 독립이사 키워드 갯수 카운트 (한 entry에 여러 직책 가능)
        matches = _OUTSIDE_KW_RE.findall(content)
        if not matches:
            continue
        in_career += len(matches)
        signals.append(f"{period} | {content[:140]}")
        # 본 회사 표기 검출
        content_clean = _normalize(content)
        if own_clean and own_clean in content_clean:
            own_in_career = True
    total = in_career + (0 if own_in_career else 1)
    return {
        "total": total,
        "in_career": in_career,
        "own_in_career": own_in_career,
        "signals": signals,
    }


async def _audit_one(ticker: str, name: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        t0 = time.time()
        try:
            res = await asyncio.wait_for(resolve_company_query(name), timeout=30.0)
            if not res.selected:
                return {"ticker": ticker, "name": name, "status": "no_match"}
            own_name = res.selected.get('corp_name') or name
            appointments, _, _ = await asyncio.wait_for(
                fetch_appointments(res.selected['corp_code'], 2026, 'annual'), timeout=60.0)
        except Exception as exc:
            return {"ticker": ticker, "name": name, "status": "error", "error": str(exc)[:100]}

        candidates_data = []
        for ap in appointments:
            for c in (ap.get('candidates') or []):
                role = c.get('roleType') or ''
                if not any(k in role for k in ('사외', '독립')):
                    continue
                cd_count = count_total_outside_director_positions(
                    c.get('careerDetails') or [], own_name)
                candidates_data.append({
                    "name": c.get('name'),
                    "role": role,
                    "agenda_action": c.get('action') or '',
                    "total_outside": cd_count['total'],
                    "in_career": cd_count['in_career'],
                    "own_in_career": cd_count['own_in_career'],
                    "signals": cd_count['signals'][:3],
                })

        # 회사 단위 통계
        n_concerns = sum(1 for c in candidates_data if c['total_outside'] >= 2)
        n_strong = sum(1 for c in candidates_data if c['total_outside'] >= 3)

        return {
            "ticker": ticker, "name": name, "own_name": own_name, "status": "ok",
            "duration_s": round(time.time() - t0, 2),
            "n_outside_candidates": len(candidates_data),
            "n_concerns": n_concerns,
            "n_strong": n_strong,
            "candidates": candidates_data,
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
            this = list(csv.DictReader(f))
            if "kosdaq_300" in str(path):
                this = this[150:]
            rows.extend(this)
    seen = set()
    unique = []
    for r in rows:
        if r["ticker"] not in seen:
            seen.add(r["ticker"])
            unique.append(r)
    if args.limit:
        unique = unique[:args.limit]

    print(f"audit n={len(unique)}")
    sem = asyncio.Semaphore(args.concurrency)
    results = []
    for chunk_start in range(0, len(unique), 30):
        chunk = unique[chunk_start:chunk_start+30]
        chunk_results = await asyncio.gather(*[
            _audit_one(r["ticker"], r["company"], sem) for r in chunk])
        results.extend(chunk_results)
        ok = [r for r in results if r.get("status") == "ok"]
        n_total = sum(r.get("n_outside_candidates", 0) for r in ok)
        n_concerns = sum(r.get("n_concerns", 0) for r in ok)
        n_strong = sum(r.get("n_strong", 0) for r in ok)
        co_concerns = len([r for r in ok if r.get("n_concerns", 0) > 0])
        print(f"  done {chunk_start+len(chunk)}/{len(unique)} — outside={n_total}, concerns후보={n_concerns}, strong={n_strong}, 회사={co_concerns}")
        if chunk_start + 30 < len(unique):
            await asyncio.sleep(2)

    archive = ROOT / "wiki/architecture/audits/data/260510_director_faithfulness"
    out = archive / "iter2_concurrent_v3.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = [r for r in results if r.get("status") == "ok"]
    n_total = sum(r.get("n_outside_candidates", 0) for r in ok)
    n_concerns = sum(r.get("n_concerns", 0) for r in ok)
    n_strong = sum(r.get("n_strong", 0) for r in ok)
    co_concerns = len([r for r in ok if r.get("n_concerns", 0) > 0])
    co_strong = len([r for r in ok if r.get("n_strong", 0) > 0])
    print(f"\n=== logic v3 결과 ===")
    print(f"  사외이사 후보 총: {n_total}")
    print(f"  concerns 후보 (≥2): {n_concerns} ({100*n_concerns/n_total if n_total else 0:.1f}%)")
    print(f"  strong 후보 (≥3): {n_strong} ({100*n_strong/n_total if n_total else 0:.1f}%)")
    print(f"  concerns 회사: {co_concerns} / {len(ok)} ({100*co_concerns/len(ok) if ok else 0:.1f}%)")
    print(f"  strong 회사: {co_strong} / {len(ok)} ({100*co_strong/len(ok) if ok else 0:.1f}%)")
    print(f"  saved: {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=3)
    args = ap.parse_args()
    asyncio.run(_run(args))
