"""KOSPI 시총 상위 50 기업 — 2026 주총 이사 보수한도 상향/하향 분류.

shareholder_meeting compensation scope의 summary(currentTotalLimit vs priorTotalLimit)로 판정.
페이싱: 회사 간 sleep + batch sleep. ReadError 즉시 중단.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import httpx

from open_proxy_mcp.dart.client import get_dart_client
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload as sm

# UNIVERSE_FILE(회사명 리스트 JSON) 지정 시 그걸 universe로 — 단위 미환산 전수조사 재사용.
_UNIVERSE_FILE = os.environ.get("UNIVERSE_FILE")
# 네이버 시총순(2026-06-15) ETF·우선주 제외 상위 50 기업
_DEFAULT_COMPANIES = [
    "삼성전자", "SK하이닉스", "SK스퀘어", "삼성전기", "현대차",
    "LG에너지솔루션", "삼성생명", "삼성물산", "HD현대중공업", "기아",
    "삼성바이오로직스", "두산에너빌리티", "KB금융", "한화에어로스페이스", "현대모비스",
    "신한지주", "삼성SDI", "SK", "HD현대일렉트릭", "NAVER",
    "셀트리온", "LG전자", "한화오션", "한미반도체", "LS ELECTRIC",
    "하나금융지주", "효성중공업", "두산", "POSCO홀딩스", "미래에셋증권",
    "삼성화재", "HD한국조선해양", "고려아연", "LG이노텍", "LG화학",
    "한국전력", "삼성중공업", "우리금융지주", "현대로템", "SK텔레콤",
    "KT&G", "HD현대", "HMM", "현대오토에버", "삼성에스디에스",
    "카카오", "메리츠금융지주", "한화시스템", "SK이노베이션", "포스코퓨처엠",
]
COMPANIES = json.loads(Path(_UNIVERSE_FILE).read_text()) if _UNIVERSE_FILE else _DEFAULT_COMPANIES
OUT = Path(os.environ.get("AUDIT_OUT", "wiki/architecture/audits/data/260615_top50_compensation.json"))
BATCH = 25
SLEEP_COMPANY = 0.5
SLEEP_BATCH = 15.0


def _won(n) -> str:
    if not n:
        return "-"
    if n >= 1_0000_0000_0000:
        return f"{n/1_0000_0000_0000:.1f}조"
    return f"{n/1_0000_0000:,.0f}억"


async def _check(q: str) -> dict:
    p = await sm(q, scope="compensation", year=2026, meeting_type="annual")
    d = p.get("data") or {}
    comp = (d.get("compensation") or {})
    s = comp.get("summary") or {}
    cur = s.get("currentTotalLimit")
    pri = s.get("priorTotalLimit")
    if cur and pri:
        if cur > pri:
            direction = "상향"
        elif cur < pri:
            direction = "하향"
        else:
            direction = "동결"
        pct = round((cur - pri) / pri * 100, 1)
    else:
        direction = "N/A"
        pct = None
    # 단위 미환산 의심 — 시총 상위는 보수한도 수십억+. 0 < limit < 1억이면 단위 미환산 강한 의심.
    unconverted = [v for v in (cur, pri) if v is not None and 0 < v < 100_000_000]
    return {
        "company": d.get("canonical_name") or q, "query": q,
        "status": str(p.get("status")),
        "direction": direction, "change_pct": pct,
        "current_limit": cur, "prior_limit": pri,
        "current_str": _won(cur), "prior_str": _won(pri),
        "prior_utilization": s.get("priorUtilization"),
        "unconverted_suspect": len(unconverted),  # 단위 미환산 의심 셀 수
    }


async def main() -> None:
    client = get_dart_client()
    calls0 = client.api_call_snapshot()
    t0 = time.time()
    rows = []
    print(f"[보수한도 audit] {len(COMPANIES)}사")
    for i, q in enumerate(COMPANIES):
        try:
            r = await _check(q)
            rows.append(r)
            arrow = {"상향": "▲", "하향": "▼", "동결": "=", "N/A": "?"}.get(r["direction"], "?")
            pct = f"({r['change_pct']:+.0f}%)" if r["change_pct"] is not None else ""
            print(f"  {arrow} {r['company']}: {r['direction']} {r['prior_str']}→{r['current_str']} {pct}")
        except httpx.ReadError as exc:
            print(f"  [ABORT] ReadError at {q}: {exc}")
            break
        except Exception as exc:  # noqa: BLE001
            print(f"  ? {q}: EXC {type(exc).__name__}: {str(exc)[:60]}")
            rows.append({"company": q, "query": q, "direction": "EXC", "error": str(exc)[:80]})
        await asyncio.sleep(SLEEP_COMPANY)
        if (i + 1) % BATCH == 0:
            calls = client.api_call_snapshot() - calls0
            print(f"  --- {i+1}/{len(COMPANIES)} 누적콜={calls} {(time.time()-t0)/60:.1f}분 ---")
            await asyncio.sleep(SLEEP_BATCH)

    up = [r for r in rows if r.get("direction") == "상향"]
    down = [r for r in rows if r.get("direction") == "하향"]
    flat = [r for r in rows if r.get("direction") == "동결"]
    na = [r for r in rows if r.get("direction") in ("N/A", "EXC")]
    unconv = [r for r in rows if r.get("unconverted_suspect")]
    result = {
        "meta": {"date": "2026-06-15", "universe": "KOSPI 시총상위 50(ETF·우선주 제외)",
                 "total_dart_calls": client.api_call_snapshot() - calls0,
                 "elapsed_min": round((time.time() - t0) / 60, 1)},
        "summary": {"상향": len(up), "하향": len(down), "동결": len(flat), "N/A": len(na),
                    "단위미환산의심": len(unconv)},
        "rows": rows,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n[완료] 상향 {len(up)} / 하향 {len(down)} / 동결 {len(flat)} / N/A {len(na)}")
    print(f"  🔴 단위 미환산 의심(0<한도<1억): {len(unconv)}건 — {[r['company'] for r in unconv]}")
    print(f"  ▲ 상향: {', '.join(r['company'] for r in up)}")
    print(f"  ▼ 하향: {', '.join(r['company'] for r in down)}")
    print(f"  = 동결: {', '.join(r['company'] for r in flat)}")
    print(f"  ? N/A: {', '.join(r['company'] for r in na)}")
    print(f"  총 DART콜 {result['meta']['total_dart_calls']}, {result['meta']['elapsed_min']}분 → {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
