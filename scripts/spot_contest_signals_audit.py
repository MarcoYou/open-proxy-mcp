"""proxy_contest 분쟁 신호 전수조사 (KOSPI 200 + KOSDAQ 100).

목적: 분쟁 신호 분포 측정 — 몇 회사가 어떤 신호(소송/위임장/5% 동학)를 보이나.
rate limit 안전: rolling window cap 900 내장 + concurrency 3 + batch sleep.
회사당 ~8-12 호출 → 300사 ≈ 3000-3600 호출.
"""
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

from open_proxy_mcp.services.proxy_contest import build_proxy_contest_payload  # noqa: E402


async def _audit_one(ticker: str, name: str, year: int, sem: asyncio.Semaphore) -> dict:
    async with sem:
        t0 = time.time()
        try:
            r = await asyncio.wait_for(
                build_proxy_contest_payload(name, scope="summary", year=year),
                timeout=120.0,
            )
        except Exception as exc:
            return {"ticker": ticker, "name": name, "status": "error", "error": str(exc)[:120]}

        if r.get("status") != "exact":
            return {"ticker": ticker, "name": name, "status": r.get("status")}

        data = r.get("data") or {}
        summ = data.get("summary") or {}
        ld = summ.get("litigation_dedup") or {}
        dyn = data.get("block_holder_dynamics") or []
        players = data.get("players") or {}
        ctx = data.get("control_context") or {}

        # 5% 동학 신호 집계
        purpose_shifts = [d for d in dyn if d.get("purpose_shift")]
        abrupt = [d for d in dyn if (d.get("accumulation") or {}).get("abrupt_change")]
        gainers = [d for d in dyn if (d.get("accumulation") or {}).get("direction") == "increasing"
                   and (d.get("accumulation") or {}).get("abrupt_change")]
        exiters = [d for d in dyn if (d.get("accumulation") or {}).get("direction") == "decreasing"
                   and (d.get("accumulation") or {}).get("abrupt_change")]

        return {
            "ticker": ticker, "name": name, "status": "exact",
            "duration_s": round(time.time() - t0, 2),
            "has_contest_signal": summ.get("has_contest_signal", False),
            "proxy_filing_count": summ.get("proxy_filing_count", 0),
            "shareholder_side_count": summ.get("shareholder_side_count", 0),
            "litigation_primary": ld.get("primary_count", 0),
            "litigation_raw": ld.get("raw_count", 0),
            "active_signal_count": summ.get("active_signal_count", 0),
            "n_block_reporters": len(dyn),
            "n_purpose_shift": len(purpose_shifts),
            "n_abrupt": len(abrupt),
            "n_abrupt_gain": len(gainers),
            "n_abrupt_exit": len(exiters),
            # 추가 필드 (260605 확장) — 방어/지배 context + 반복 패턴 추출
            "treasury_pct": round(summ.get("treasury_pct", 0) or 0, 2),
            "related_total_pct": round(summ.get("related_total_pct", 0) or 0, 2),
            "active_external_block_count": summ.get("active_external_block_count", 0),
            "active_overlap_block_count": summ.get("active_overlap_block_count", 0),
            "observations": ctx.get("observations", []),
            "company_side_filers": players.get("company_side_filers", []),
            "shareholder_side_filers": players.get("shareholder_side_filers", []),
            "retail_activism_filers": players.get("retail_activism_filers", []),
            "active_external_blocks": players.get("active_external_blocks", []),
            "abrupt_detail": [
                {"reporter": d["reporter"],
                 "change_pp": (d.get("accumulation") or {}).get("change_pp"),
                 "dir": (d.get("accumulation") or {}).get("direction"),
                 "purpose": d.get("current_purpose")}
                for d in abrupt[:8]
            ],
        }


async def _run(args):
    universe_map = {
        "kospi200": ROOT / "wiki/architecture/audits/data/260506_universe_kospi_200.csv",
        "kosdaq100": ROOT / "wiki/architecture/audits/data/260506_universe_kosdaq_100.csv",
        "kosdaq300": ROOT / "wiki/architecture/audits/data/260506_universe_kosdaq_300.csv",
    }
    rows = []
    seen_tk = set()
    for key in args.universes.split(","):
        key = key.strip()
        # 등록 universe 또는 직접 csv 경로
        path = universe_map.get(key)
        if path is None and Path(key).is_file():
            path = Path(key)
        if path is None:
            print(f"unknown universe: {key}")
            continue
        with open(path) as f:
            for r in csv.DictReader(f):
                if r["ticker"] not in seen_tk:
                    seen_tk.add(r["ticker"])
                    rows.append(r)
    if args.limit:
        rows = rows[:args.limit]

    print(f"전수조사 n={len(rows)} (year={args.year}, concurrency={args.concurrency})")
    sem = asyncio.Semaphore(args.concurrency)
    results = []
    BATCH = 30
    for chunk_start in range(0, len(rows), BATCH):
        chunk = rows[chunk_start:chunk_start + BATCH]
        chunk_res = await asyncio.gather(*[
            _audit_one(r["ticker"], r["company"], args.year, sem) for r in chunk
        ])
        results.extend(chunk_res)
        ok = [r for r in results if r.get("status") == "exact"]
        n_signal = sum(1 for r in ok if r.get("has_contest_signal"))
        n_abrupt = sum(1 for r in ok if r.get("n_abrupt", 0) > 0)
        print(f"  done {chunk_start+len(chunk)}/{len(rows)} — 분쟁신호 {n_signal} / 급변보유 {n_abrupt}")
        if chunk_start + BATCH < len(rows):
            await asyncio.sleep(args.batch_sleep)

    archive = ROOT / "wiki/architecture/audits/data/260605_contest_signals_audit"
    archive.mkdir(parents=True, exist_ok=True)
    # universe에 csv 경로가 들어와도 안전한 파일명 (label 인자 우선, 없으면 basename)
    if args.label:
        tag = args.label
    else:
        tag = "_".join(Path(u.strip()).stem for u in args.universes.split(","))
    out = archive / f"audit_{tag}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    # 요약
    ok = [r for r in results if r.get("status") == "exact"]
    err = [r for r in results if r.get("status") != "exact"]
    n_signal = sum(1 for r in ok if r.get("has_contest_signal"))
    n_litig = sum(1 for r in ok if r.get("litigation_primary", 0) > 0)
    n_sh = sum(1 for r in ok if r.get("shareholder_side_count", 0) > 0)
    n_abrupt = sum(1 for r in ok if r.get("n_abrupt", 0) > 0)
    n_shift = sum(1 for r in ok if r.get("n_purpose_shift", 0) > 0)
    n_exit = sum(1 for r in ok if r.get("n_abrupt_exit", 0) > 0)
    print(f"\n=== 전수조사 결과 (n={len(ok)} ok / {len(err)} err) ===")
    print(f"  has_contest_signal:   {n_signal} ({100*n_signal/len(ok) if ok else 0:.1f}%)")
    print(f"  소송 원본 ≥1:          {n_litig}")
    print(f"  주주측 위임장 ≥1:       {n_sh}")
    print(f"  5% 급변보유 ≥1:        {n_abrupt}")
    print(f"   - exit(매각) 포함:     {n_exit}")
    print(f"  목적전환 ≥1:           {n_shift}")
    # 상위 분쟁 회사 (신호 많은 순)
    ranked = sorted(ok, key=lambda r: (
        r.get("has_contest_signal", False),
        r.get("shareholder_side_count", 0) + r.get("litigation_primary", 0) + r.get("n_abrupt", 0),
    ), reverse=True)
    print(f"\n  [상위 분쟁 신호 회사 15]")
    for r in ranked[:15]:
        if not (r.get("has_contest_signal") or r.get("n_abrupt", 0) > 0):
            break
        print(f"    {r['name']:<14} 위임{r.get('shareholder_side_count',0)} 소송{r.get('litigation_primary',0)} "
              f"급변{r.get('n_abrupt',0)}(exit{r.get('n_abrupt_exit',0)}) 전환{r.get('n_purpose_shift',0)}")
    # ── 패턴 분석 (260605 확장) ──
    from collections import Counter
    print(f"\n=== 패턴 분석 ===")

    # 1. 신호 조합 분포 (어떤 신호들이 같이 뜨나)
    combos = Counter()
    for r in ok:
        tags = []
        if r.get("shareholder_side_count", 0) > 0: tags.append("위임")
        if r.get("litigation_primary", 0) > 0: tags.append("소송")
        if r.get("active_signal_count", 0) > 0: tags.append("외부5%")
        if r.get("n_abrupt", 0) > 0: tags.append("급변")
        combos["+".join(tags) if tags else "신호없음"] += 1
    print(f"\n  [신호 조합 분포]")
    for combo, n in combos.most_common(12):
        print(f"    {combo:<24} {n}")

    # 2. 반복 등장 보고자 (급변 일으킨 주체 — 외국기관 차익실현 등)
    abrupt_reporters = Counter()
    for r in ok:
        for d in r.get("abrupt_detail", []):
            abrupt_reporters[d["reporter"]] += 1
    print(f"\n  [급변 일으킨 보고자 반복 빈도 (≥2회)]")
    for rep, n in abrupt_reporters.most_common(20):
        if n < 2: break
        print(f"    {rep:<28} {n}개 회사")

    # 3. 관찰 포인트 빈도 (control_context observations)
    obs_c = Counter()
    for r in ok:
        for o in r.get("observations", []):
            obs_c[o] += 1
    print(f"\n  [관찰 포인트 빈도]")
    for o, n in obs_c.most_common(10):
        print(f"    {n:>3}  {o[:60]}")

    # 4. 방어 수단 — 자사주/특관 높은 회사 분포
    high_treasury = [r for r in ok if r.get("treasury_pct", 0) >= 5]
    high_related = [r for r in ok if r.get("related_total_pct", 0) >= 30]
    print(f"\n  [지배/방어 구조]")
    print(f"    자사주 ≥5%:       {len(high_treasury)} 회사")
    print(f"    특관합계 ≥30%:    {len(high_related)} 회사")

    # 5. 급변 방향 분포 (매집 vs exit)
    total_gain = sum(r.get("n_abrupt_gain", 0) for r in ok)
    total_exit = sum(r.get("n_abrupt_exit", 0) for r in ok)
    print(f"\n  [급변 방향 (정보 — 분쟁 판정 X)]")
    print(f"    매집(증가): {total_gain}건 / exit(감소): {total_exit}건")

    print(f"\n  saved: {out}")
    if err:
        from collections import Counter as _C
        ec = _C(r.get("status") for r in err)
        print(f"\n  [비exact {len(err)}] {dict(ec)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--universes", default="kospi200,kosdaq100")
    ap.add_argument("--label", default="", help="출력 파일명 태그 (csv 경로 universe일 때 권장)")
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--batch-sleep", type=float, default=2.0)
    args = ap.parse_args()
    asyncio.run(_run(args))
