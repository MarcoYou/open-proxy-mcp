"""careerDetails concat 패턴 정량화 (Ralph 10 iter 1).

각 회사 사외이사 후보 careerDetails 수집:
- entry별 period 정규식 매치 갯수
- multi-period (≥2) entry 식별
- content 직책 키워드 갯수 측정 (split 가능성)
- 분포 catalog
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

# period 정규식 — "YYYY~YYYY" / "YYYY~현재" / "YYYY.MM~YYYY.MM" 등
_PERIOD_RE = re.compile(r'(?:19[5-9]\d|20[0-3]\d)(?:\.\d{1,2})?(?:\.\d{1,2})?\s*[~\-–—]\s*(?:(?:19[5-9]\d|20[0-3]\d)(?:\.\d{1,2})?(?:\.\d{1,2})?|현재|현)')

# 직책 키워드 — content에서 entry 분리 hint
_ROLE_KEYWORDS = [
    "사외이사", "사내이사", "독립이사", "감사위원", "감사",
    "사장", "부사장", "회장", "부회장",
    "본부장", "팀장", "실장", "부장", "과장", "팀원",
    "이사", "위원", "위원장", "고문", "자문",
    "교수", "조교수", "부교수", "전임강사", "강사", "연구원", "연구교수",
    "소장", "원장", "센터장", "원로",
    "대표", "대표이사", "CEO", "CFO", "CTO",
    "장",  # generic 후위 단독 매칭 (조사4국장 / 청장 등) — last resort
]


def _is_outside_director_role(role_type: str) -> bool:
    rt = role_type or ""
    return any(k in rt for k in ("사외", "독립"))


def _count_role_keywords(content: str) -> int:
    """content에서 직책 키워드 매칭 갯수."""
    if not content:
        return 0
    count = 0
    # 더 specific 키워드 우선 (단독 "장" 매칭은 마지막)
    for kw in _ROLE_KEYWORDS[:-1]:
        count += content.count(kw)
    # 단독 "장" 매칭은 더 정교 — '국장', '청장', '센터장', '본부장' 등 X자 + 장
    # specific 키워드 매칭 안 된 "장"만 추가 카운트
    return count


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
                if not _is_outside_director_role(role):
                    continue
                cds = c.get('careerDetails') or []
                if not cds:
                    candidates_data.append({
                        "name": c.get('name'), "role": role,
                        "n_entries": 0, "multi_period": 0, "split_possible": 0,
                        "examples": [],
                    })
                    continue

                multi_period_entries = []
                split_possible_entries = []
                for cd in cds:
                    period = cd.get('period', '') or ''
                    content = cd.get('content', '') or ''
                    period_matches = _PERIOD_RE.findall(period)
                    n_periods = len(period_matches)
                    n_roles = _count_role_keywords(content)
                    if n_periods >= 2:
                        multi_period_entries.append({
                            "period": period[:80], "content": content[:200],
                            "n_periods": n_periods, "n_roles": n_roles,
                            "split_match": n_periods == n_roles,
                        })
                        if n_periods == n_roles and n_periods >= 2:
                            split_possible_entries.append(period[:50])

                candidates_data.append({
                    "name": c.get('name'), "role": role,
                    "n_entries": len(cds),
                    "multi_period": len(multi_period_entries),
                    "split_possible": len(split_possible_entries),
                    "examples": multi_period_entries[:3],
                })

        return {
            "ticker": ticker, "name": name, "own_name": own_name, "status": "ok",
            "duration_s": round(time.time() - t0, 2),
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
        n_cands = sum(len(r.get("candidates") or []) for r in ok)
        n_multi = sum(c.get("multi_period", 0) for r in ok for c in (r.get("candidates") or []))
        n_split = sum(c.get("split_possible", 0) for r in ok for c in (r.get("candidates") or []))
        print(f"  done {chunk_start+len(chunk)}/{len(unique)} — cands={n_cands}, multi={n_multi}, split_ok={n_split}")
        if chunk_start + 30 < len(unique):
            await asyncio.sleep(2)

    archive = ROOT / "wiki/architecture/audits/data/260510_career_concat"
    archive.mkdir(parents=True, exist_ok=True)
    out = archive / "iter1_concat_audit.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = [r for r in results if r.get("status") == "ok"]
    all_cands = [c for r in ok for c in (r.get("candidates") or [])]
    n_total = len(all_cands)
    n_with_multi = len([c for c in all_cands if c.get("multi_period", 0) > 0])
    n_with_split = len([c for c in all_cands if c.get("split_possible", 0) > 0])
    multi_total = sum(c.get("multi_period", 0) for c in all_cands)
    split_total = sum(c.get("split_possible", 0) for c in all_cands)

    print(f"\n=== Ralph 10 iter 1 — concat 패턴 정량화 ===")
    print(f"  total companies: {len(ok)}")
    print(f"  사외이사 후보 총: {n_total}")
    print(f"  multi-period entry 있는 후보: {n_with_multi} ({100*n_with_multi/n_total if n_total else 0:.1f}%)")
    print(f"  split 가능 (period count == role count) 후보: {n_with_split} ({100*n_with_split/n_total if n_total else 0:.1f}%)")
    print(f"  multi-period entry 총: {multi_total}")
    print(f"  split 가능 entry 총: {split_total}")
    print(f"  → split 효과 잠재력: {split_total} entries 추가 catch 가능")
    print(f"\n  saved: {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=3)
    args = ap.parse_args()
    asyncio.run(_run(args))
