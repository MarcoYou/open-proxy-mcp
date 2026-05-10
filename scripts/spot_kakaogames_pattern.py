"""카카오게임즈 패턴 식별 spot — 510 회사 안건 트리 분석.

조건 (카카오게임즈와 같은 사례):
- 정관변경 top 안건 (parent="" + "정관" + "변경"/"개정")
- children > 0 (sub-agenda 있음)
- sub-agenda 중 title이 일반 표현 (정관/변경/개정 단어 없음)
- title hits 그 안건들에서 0건

→ Ralph 7 D 패턴 fallback 진입 X + sub-agenda 일반 표현 = 카카오게임즈 케이스.
"""
from __future__ import annotations
import asyncio
import csv
import json
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.services.proxy_advise import _is_charter_top, _law_layer  # noqa: E402
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload  # noqa: E402


def _walk(items, parent="", out=None):
    if out is None:
        out = []
    for it in items or []:
        t = (it.get("title") or "").strip()
        if t:
            out.append({"title": t, "parent": parent, "n_children": len(it.get("children") or [])})
        _walk(it.get("children", []), parent=t, out=out)
    return out


async def _check(ticker: str, name: str, sem: asyncio.Semaphore) -> dict | None:
    async with sem:
        today_iso = date.today().isoformat()
        try:
            sm = await asyncio.wait_for(
                build_shareholder_meeting_payload(name, year=2026, scope="summary", meeting_type="annual"),
                timeout=60.0,
            )
            agendas = (sm.get("data") or {}).get("agendas") or []
            flat = _walk(agendas)
        except Exception:
            return None

        # 정관변경 top + children > 0 안건 식별
        out = []
        for it in flat:
            if it["parent"] != "" or not _is_charter_top(it["title"]) or it["n_children"] == 0:
                continue
            # 자식 sub-agenda 모두
            subs = [c for c in flat if c["parent"] == it["title"]]
            # 일반 표현 sub (정관/변경/개정 단어 없음)
            generic_subs = []
            for c in subs:
                t = c["title"]
                if "정관" not in t and "변경" not in t and "개정" not in t:
                    generic_subs.append(c["title"])
            if not generic_subs:
                continue
            # 그 안건 + sub 모두 title 매칭 0인지 확인
            top_hit = _law_layer(it["title"], parent_title=it["parent"],
                                corp_total_asset_won=None, today_iso=today_iso)
            if top_hit:
                continue  # top이 catch되면 카카오게임즈 케이스 아님
            sub_hits = [_law_layer(c["title"], parent_title=c["parent"],
                                   corp_total_asset_won=None, today_iso=today_iso) for c in subs]
            if any(sub_hits):
                continue  # 어느 sub라도 catch되면 카카오게임즈 케이스 아님
            out.append({
                "top_title": it["title"],
                "sub_count": len(subs),
                "generic_sub_count": len(generic_subs),
                "generic_sub_titles": generic_subs[:5],
                "all_sub_titles": [c["title"] for c in subs][:5],
            })
        if out:
            return {"ticker": ticker, "name": name, "patterns": out}
        return None


async def _run():
    universe_csvs = [
        ROOT / "wiki/architecture/audits/data/260506_universe_kospi_200.csv",
        ROOT / "wiki/architecture/audits/data/260506_universe_kosdaq_150.csv",
        ROOT / "wiki/architecture/audits/data/260506_universe_kosdaq_300.csv",  # skip 150
        ROOT / "wiki/architecture/audits/data/260510_law_layer_body/dispute_new_10.csv",
    ]
    rows = []
    for path in universe_csvs:
        with open(path) as f:
            reader = csv.DictReader(f)
            this = list(reader)
            if "kosdaq_300" in str(path):
                this = this[150:]  # 151~300 슬라이스
            rows.extend(this)
    # dedup
    seen = set()
    unique_rows = []
    for r in rows:
        if r["ticker"] not in seen:
            seen.add(r["ticker"])
            unique_rows.append(r)

    print(f"total companies (dedup): {len(unique_rows)}")
    sem = asyncio.Semaphore(3)

    results = []
    completed = 0
    for chunk_start in range(0, len(unique_rows), 30):
        chunk = unique_rows[chunk_start:chunk_start+30]
        chunk_results = await asyncio.gather(*[_check(r["ticker"], r["company"], sem) for r in chunk])
        for r in chunk_results:
            if r:
                results.append(r)
        completed += len(chunk)
        print(f"  done {completed}/{len(unique_rows)} — kakaogames-pattern hits {len(results)}")
        if chunk_start + 30 < len(unique_rows):
            await asyncio.sleep(2)

    archive = ROOT / "wiki/architecture/audits/data/260510_agenda_hierarchy"
    out = archive / "iter5_kakaogames_pattern_510.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== 카카오게임즈 패턴 식별 결과 ===")
    print(f"total companies: {len(unique_rows)}")
    print(f"kakaogames-pattern 회사: {len(results)}")
    for r in results[:30]:
        print(f"  {r['name']:<14} top={r['patterns'][0]['top_title'][:40]!r}")
        for s in r['patterns'][0]['all_sub_titles'][:3]:
            print(f"    sub: {s[:60]}")
    print(f"\nsaved: {out}")


if __name__ == "__main__":
    asyncio.run(_run())
