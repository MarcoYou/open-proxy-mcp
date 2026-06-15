"""공동보유(coheld_with_registry) 통합 검증 — 솔루엠 타깃 + 분쟁 표본 회귀.

검증 항목:
1) 타깃(솔루엠): 얼라인 5% 블록이 coheld_with_registry=True, self_pct≈5.33(헤드라인 23.11과 분리),
   coheld_names에 명부 최대주주 포함. proxy_contest에서 얼라인 actor_side가 더 이상
   external_active_block이 아님 + observation 노출.
2) 회귀(분쟁 표본): control_map 정상 생성, coheld는 실제 공동보유에서만 발화(과발화 0),
   crash 0. 기존 actor_side 분포가 coheld 케이스 외엔 유지.

usage: uv run python scripts/coheld_integration_test.py
"""
import warnings as W
W.filterwarnings("ignore")
import asyncio, csv, json, os, sys, io, time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
for line in open(".env.local", encoding="utf-8"):
    if line.startswith("DART_API_KEY="):
        os.environ["OPENDART_API_KEY"] = line.split("=", 1)[1].strip()

from open_proxy_mcp.services.company import resolve_company_query
from open_proxy_mcp.services.ownership_structure import build_ownership_structure_payload
from open_proxy_mcp.services.proxy_contest import build_proxy_contest_payload


def _all_blocks(cm):
    return (cm.get("overlap_blocks", []) + cm.get("non_overlap_blocks", []))


async def target_solomon():
    print("=" * 70)
    print("[타깃] 솔루엠 — 얼라인 공동보유 분류 검증")
    print("=" * 70)
    p = await build_ownership_structure_payload("솔루엠", scope="control_map")
    cm = p.get("data", {}).get("control_map", {})
    print("\n-- control_map blocks --")
    for b in _all_blocks(cm):
        print(f"  {b.get('reporter')}: 헤드라인 {b.get('ownership_pct')}% / "
              f"self_pct={b.get('self_pct')} / coheld={b.get('coheld_with_registry')} / "
              f"coheld_names={b.get('coheld_names')} / purpose={b.get('purpose')}")
    print("\n-- observations --")
    for o in cm.get("observations", []):
        print(f"  · {o}")
    coheld = [b for b in _all_blocks(cm) if b.get("coheld_with_registry")]
    print(f"\n  coheld 블록 수: {len(coheld)}")

    print("\n-- proxy_contest signals (actor_side) --")
    pc = await build_proxy_contest_payload("솔루엠", scope="summary")
    sigs = pc.get("data", {}).get("signals", [])
    for s in sigs[:12]:
        print(f"  {s.get('reporter')}: {s.get('ownership_pct')}% / actor_side={s.get('actor_side')} / coheld={s.get('coheld_with_registry')}")
    return cm


async def regress_one(name):
    try:
        p = await build_ownership_structure_payload(name, scope="control_map")
        cm = p.get("data", {}).get("control_map", {})
        blocks = _all_blocks(cm)
        coheld = [b for b in blocks if b.get("coheld_with_registry")]
        sides = {}
        for b in blocks:
            from open_proxy_mcp.services.proxy_contest import _signal_actor_side
            sides[_signal_actor_side(b)] = sides.get(_signal_actor_side(b), 0) + 1
        return {"name": name, "n_blocks": len(blocks), "n_coheld": len(coheld),
                "coheld_detail": [(b.get("reporter"), b.get("self_pct"), b.get("coheld_names")) for b in coheld],
                "sides": sides}
    except Exception as e:
        return {"name": name, "err": f"{type(e).__name__}: {e}"}


async def main():
    t0 = time.monotonic()
    await target_solomon()

    print("\n" + "=" * 70)
    print("[회귀] 분쟁 표본 — control_map 생성 + coheld 발화 점검")
    print("=" * 70)
    names = []
    for f in ("260607_kospi_dispute_universe.csv", "260607_kosdaq_dispute_universe.csv"):
        for row in csv.DictReader(open(f"wiki/architecture/audits/data/{f}", encoding="utf-8")):
            names.append(row["company"])
    seen = set(); names = [x for x in names if not (x in seen or seen.add(x))][:60]
    print(f"표본 {len(names)}사\n")

    res = []
    for i in range(0, len(names), 12):
        res.extend(await asyncio.gather(*(regress_one(x) for x in names[i:i+12])))
        print(f"  ... {min(i+12,len(names))}/{len(names)} ({time.monotonic()-t0:.0f}s)")

    err = [r for r in res if r.get("err")]
    ok = [r for r in res if "n_blocks" in r]
    coheld_hits = [r for r in ok if r["n_coheld"] > 0]
    agg = {}
    for r in ok:
        for k, v in r["sides"].items():
            agg[k] = agg.get(k, 0) + v
    print(f"\n조회 {len(res)} / 정상 {len(ok)} / 에러 {len(err)}")
    print(f"actor_side 분포(전 표본 블록): {agg}")
    print(f"coheld 발화 회사 수: {len(coheld_hits)}")
    for r in coheld_hits:
        print(f"  · {r['name']}: {r['coheld_detail']}")
    if err:
        print("\n에러:", [(r["name"], r["err"][:50]) for r in err][:10])

    json.dump(res, open("wiki/architecture/audits/data/260615_coheld_integration_test.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n저장: 260615_coheld_integration_test.json  (총 {time.monotonic()-t0:.0f}s)")


asyncio.run(main())
