"""D 패턴 amendments body fallback 단위 검증 (Ralph 7 iter 2).

5 회사 (4 미매치 + LG화학) 대상:
- aoi_change scope에서 amendments + agenda hierarchy 받기
- _law_layer (title only) hit 측정
- title 미매치 + D 패턴 (charter top + children 0 + amendments) 시 _law_layer_body fallback
- LG화학 regression 검증 (children > 0이라 fallback 진입 X)
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.services.financial_metrics import build_financial_metrics_payload  # noqa: E402
from open_proxy_mcp.services.proxy_advise import (  # noqa: E402
    _is_charter_top,
    _law_layer,
    _law_layer_body,
)
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload  # noqa: E402

TARGETS = [
    ("247540", "에코프로비엠"),
    ("293490", "카카오게임즈"),
    ("041510", "에스엠"),
    ("138040", "메리츠금융지주"),
    ("051910", "LG화학"),  # regression 검증
]


def _walk(items, parent="", out=None):
    if out is None:
        out = []
    for it in items or []:
        t = (it.get("title") or "").strip()
        if t:
            out.append({
                "title": t,
                "parent": parent,
                "n_children": len(it.get("children") or []),
            })
        _walk(it.get("children", []), parent=t, out=out)
    return out


async def _verify_one(name: str) -> dict:
    today_iso = date.today().isoformat()

    fm = await build_financial_metrics_payload(name, scope="summary")
    fm_summary = ((fm or {}).get("data") or {}).get("summary") or {}
    ta = fm_summary.get("total_assets_krw")
    corp_asset = int(ta) if isinstance(ta, (int, float)) and ta > 0 else None

    # agendas hierarchy
    sm_summary = await build_shareholder_meeting_payload(name, year=2026, scope="summary", meeting_type="annual")
    sm_data = sm_summary.get("data") or {}
    agendas = sm_data.get("agendas") or []

    # amendments
    sm_aoi = await build_shareholder_meeting_payload(name, year=2026, scope="aoi_change", meeting_type="annual")
    aoi_data = sm_aoi.get("data") or {}
    aoi = (aoi_data.get("aoi_change") or {})
    amendments = aoi.get("amendments") or []

    flat = _walk(agendas)

    title_hits = []
    fallback_hits = []
    fallback_entries = []  # D 패턴 진입 회사

    for entry in flat:
        title = entry["title"]
        parent = entry["parent"]
        n_children = entry["n_children"]

        title_hit = _law_layer(title, parent_title=parent,
                               corp_total_asset_won=corp_asset, today_iso=today_iso)
        if title_hit:
            title_hits.append({"title": title, "rule": title_hit[2], "decision": title_hit[0]})
            continue

        # D 패턴 fallback 진입 조건
        if (
            parent == ""
            and _is_charter_top(title)
            and n_children == 0
            and amendments
        ):
            fallback_entries.append(title)
            body_hit = _law_layer_body(
                amendments,
                parent_title=title,
                corp_total_asset_won=corp_asset,
                today_iso=today_iso,
            )
            if body_hit:
                fallback_hits.append({
                    "title": title,
                    "rule": body_hit[2],
                    "decision": body_hit[0],
                    "reason": body_hit[1][:100],
                })

    return {
        "name": name,
        "corp_asset_2tril": (corp_asset or 0) >= 2_000_000_000_000,
        "n_agendas": len(flat),
        "n_amendments": len(amendments),
        "title_hits_n": len(title_hits),
        "title_hits": title_hits,
        "fallback_entries_n": len(fallback_entries),
        "fallback_entries": fallback_entries,
        "fallback_hits_n": len(fallback_hits),
        "fallback_hits": fallback_hits,
    }


async def _run():
    archive = ROOT / "wiki/architecture/audits/data/260510_agenda_hierarchy"
    archive.mkdir(parents=True, exist_ok=True)

    sem = asyncio.Semaphore(2)

    async def _wrapped(t, n):
        async with sem:
            print(f"  → {n} ({t}) ...", flush=True)
            return await _verify_one(n)

    results = await asyncio.gather(*[_wrapped(t, n) for t, n in TARGETS])

    print("\n=== iter 2 — D 패턴 fallback 단위 검증 ===")
    print(f"{'회사':<14} {'agendas':>8} {'amends':>7} {'title_hits':>11} {'D_진입':>7} {'fb_hits':>8}")
    for r in results:
        print(f"{r['name']:<14} {r['n_agendas']:>8} {r['n_amendments']:>7} "
              f"{r['title_hits_n']:>11} {r['fallback_entries_n']:>7} {r['fallback_hits_n']:>8}")
    print()
    for r in results:
        if r["fallback_hits"]:
            print(f"  [fb-hit] {r['name']}:")
            for h in r["fallback_hits"]:
                print(f"    - {h['rule']} {h['decision']}: {h['title']} → {h['reason']}")

    out = archive / "iter2_body_fallback_verify.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nsaved: {out}")


if __name__ == "__main__":
    asyncio.run(_run())
