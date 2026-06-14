"""DART KSIC C21(의약품 제조업) 상장사 universe 추출.

master.db 상장사(stock_code 있음) 전체 × company.json → induty_code 21xxx 필터.
바이오 전수조사용 universe를 만든다. 콜 추적(분당/910) + 배치 페이싱.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path

import httpx

from open_proxy_mcp.dart.client import get_dart_client

OUT = Path("wiki/architecture/audits/data/bio_universe_c21.json")
BATCH = 100
SLEEP = 0.08


async def main() -> None:
    conn = sqlite3.connect("configs/master.db")
    listed = conn.execute(
        "SELECT corp_code, corp_name, stock_code FROM corp_codes WHERE stock_code != ''"
    ).fetchall()
    conn.close()
    # corp_code 기준 dedup (같은 회사 중복 방지)
    seen, rows = set(), []
    for cc, nm, sc in listed:
        if cc not in seen:
            seen.add(cc)
            rows.append((cc, nm, sc))
    print(f"[bio universe] 상장사 {len(rows)}개 induty 조회 시작 (batch {BATCH})")

    client = get_dart_client()
    bio: list[dict] = []
    calls0 = client.api_call_snapshot()
    t0 = time.time()
    for i, (cc, nm, sc) in enumerate(rows):
        try:
            d = await client._request("company.json", {"corp_code": cc})
            induty = (d.get("induty_code") or "").strip()
            if induty.startswith("21"):  # C21 의료용 물질 및 의약품 제조업
                bio.append({"corp_code": cc, "company": nm, "stock_code": sc, "induty_code": induty})
        except httpx.ReadError as exc:
            print(f"[ABORT] ReadError at {nm}: {exc}")
            break
        except Exception:
            pass
        await asyncio.sleep(SLEEP)
        if (i + 1) % BATCH == 0:
            calls = client.api_call_snapshot() - calls0
            elapsed = time.time() - t0
            rate = calls / (elapsed / 60) if elapsed else 0
            print(f"  {i+1}/{len(rows)}  누적콜={calls}  경과={elapsed/60:.1f}분  분당={rate:.0f}/910  bio={len(bio)}")

    OUT.write_text(json.dumps({
        "meta": {"date": "2026-06-14", "filter": "induty_code 21xxx (KSIC C21 의약품)",
                 "listed_scanned": len(rows), "total_calls": client.api_call_snapshot() - calls0},
        "count": len(bio), "companies": bio,
    }, ensure_ascii=False, indent=2))
    print(f"[완료] 바이오(C21) {len(bio)}개 / 상장사 {len(rows)} → {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
