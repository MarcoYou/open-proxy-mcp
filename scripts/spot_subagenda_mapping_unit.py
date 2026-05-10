"""카카오게임즈 패턴 fallback 단위 검증 (Ralph 8 iter 2-3).

5+ 회사 (카카오게임즈 + 한미사이언스 + 강원랜드 + 한라캐스트 + LG화학):
- agendas + amendments 받기
- 안건별 _law_layer + D 패턴 fallback + 카카오게임즈 패턴 fallback 시뮬레이션
- regression 검증 (LG화학)
- cross-match 회피 검증 (한 회사 여러 sub 매핑)
"""
from __future__ import annotations
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.services.proxy_advise import (  # noqa: E402
    _is_charter_top, _is_generic_sub,
    _law_layer, _law_layer_body, _law_layer_subagenda_mapped,
    _map_subagenda_to_amendment,
)
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload  # noqa: E402
from open_proxy_mcp.services.financial_metrics import build_financial_metrics_payload  # noqa: E402

TARGETS = ["카카오게임즈", "한미사이언스", "강원랜드", "한라캐스트", "차바이오텍",
           "유한양행", "LG화학"]


def _walk(items, parent="", out=None):
    if out is None:
        out = []
    for it in items or []:
        t = (it.get("title") or "").strip()
        if t:
            out.append({"title": t, "parent": parent, "n_children": len(it.get("children") or [])})
        _walk(it.get("children", []), parent=t, out=out)
    return out


async def _verify(name: str) -> dict:
    today_iso = date.today().isoformat()
    fm = await build_financial_metrics_payload(name, scope="summary")
    fm_summary = ((fm or {}).get("data") or {}).get("summary") or {}
    ta = fm_summary.get("total_assets_krw")
    asset = int(ta) if isinstance(ta, (int, float)) and ta > 0 else None

    sm_sum = await build_shareholder_meeting_payload(name, year=2026, scope="summary", meeting_type="annual")
    agendas = (sm_sum.get("data") or {}).get("agendas") or []
    flat = _walk(agendas)

    sm_aoi = await build_shareholder_meeting_payload(name, year=2026, scope="aoi_change", meeting_type="annual")
    amendments = (((sm_aoi.get("data") or {}).get("aoi_change") or {})).get("amendments") or []

    used_amendments: set[int] = set()
    title_hits = []
    body_hits = []
    sub_hits = []
    for it in flat:
        title = it["title"]
        parent = it["parent"]
        n_children = it["n_children"]

        # 0. title
        hit = _law_layer(title, parent_title=parent,
                        corp_total_asset_won=asset, today_iso=today_iso)
        if hit:
            title_hits.append({"title": title, "rule": hit[2]})
            continue

        # 0-b. D 패턴 fallback
        if (parent == "" and _is_charter_top(title)
            and n_children == 0 and amendments):
            hit = _law_layer_body(amendments, parent_title=title,
                                 corp_total_asset_won=asset, today_iso=today_iso)
            if hit:
                body_hits.append({"title": title, "rule": hit[2]})
                continue

        # 0-c. 카카오게임즈 패턴 fallback
        if (parent and _is_charter_top(parent)
            and n_children == 0 and amendments
            and not _is_generic_sub(title)):
            mapped_idx = _map_subagenda_to_amendment(title, amendments, used_amendments)
            if mapped_idx is not None:
                hit = _law_layer_subagenda_mapped(
                    title, amendments[mapped_idx],
                    parent_title=parent, corp_total_asset_won=asset, today_iso=today_iso,
                )
                if hit:
                    sub_hits.append({
                        "title": title, "rule": hit[2],
                        "mapped_amendment_idx": mapped_idx,
                        "mapped_label": amendments[mapped_idx].get("label", ""),
                    })
                    used_amendments.add(mapped_idx)

    return {
        "name": name, "asset_2tril": (asset or 0) >= 2_000_000_000_000,
        "n_agendas": len(flat), "n_amendments": len(amendments),
        "title_hits_n": len(title_hits), "title_hits": title_hits,
        "body_hits_n": len(body_hits), "body_hits": body_hits,
        "sub_hits_n": len(sub_hits), "sub_hits": sub_hits,
        "used_amendments": sorted(used_amendments),
    }


async def _run():
    sem = asyncio.Semaphore(2)

    async def _wrapped(n):
        async with sem:
            print(f"  → {n} ...", flush=True)
            return await _verify(n)

    results = await asyncio.gather(*[_wrapped(n) for n in TARGETS])

    print(f"\n=== Ralph 8 iter 2 단위 검증 ===")
    print(f"{'회사':<14} {'agendas':>8} {'ams':>4} {'title':>5} {'body':>5} {'sub':>4} {'cross-match':>12}")
    for r in results:
        cross = "OK"
        if r["sub_hits_n"] > len(set(h["mapped_amendment_idx"] for h in r["sub_hits"])):
            cross = "❌ DUPLICATE"
        print(f"{r['name']:<14} {r['n_agendas']:>8} {r['n_amendments']:>4} "
              f"{r['title_hits_n']:>5} {r['body_hits_n']:>5} {r['sub_hits_n']:>4} {cross:>12}")
    print()
    for r in results:
        if r["sub_hits"]:
            print(f"  [sub-hit] {r['name']}:")
            for h in r["sub_hits"]:
                print(f"    - {h['rule']}: {h['title']} → {h['mapped_label']}")

    archive = ROOT / "wiki/architecture/audits/data/260510_subagenda_mapping"
    out = archive / "iter2_unit_verify.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nsaved: {out}")


if __name__ == "__main__":
    asyncio.run(_run())
