"""ownership_structure summary 재설계(2026-06-10) 시장 전수 audit.

유니버스: 260517 baseline 450사 (KOSPI 300 + KOSDAQ 150).
검증 불변식 (회사당 summary 1회 = DART ~4콜):
  1) resolve: status == exact (ambiguous/error flag)
  2) 100% 분해 정합: issued > 0 (펀드형 예외 flag) / 기타 = issued - 명부 - 자사주 >= 0
  3) 교차일치: 명부 related_total_pct vs sum(shares)/issued*100 — 차이 > 1.5%p면
     분모 불일치(보통주 vs 합계) 의심 (셀트리온 버그류 탐지)
  4) 명부 0행 (fallback 추정 경로) / blocks pct 범위 (0, 100]
changes spot: 처음 30사만 scope=changes 1회 (I004 + 5% 변동 통합 동작).

페이싱: 순차, 회사당 sleep 0.5s, 30사 배치마다 20s 휴식, ReadError 즉시 중단.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx

from open_proxy_mcp.dart.client import get_dart_client
from open_proxy_mcp.services.ownership_structure import build_ownership_structure_payload as build

BASELINE = Path("wiki/architecture/audits/data/260517_parsing_success_rate_audit/baseline_company_sample_450.json")
OUT = Path("wiki/architecture/audits/data/260612_ownership_summary_market_audit.json")
BATCH = 30
SLEEP_COMPANY = 0.5
SLEEP_BATCH = 20.0
CHANGES_SPOT_N = 30


def _universe() -> list[dict]:
    recs = json.loads(BASELINE.read_text())["records"]
    seen, out = set(), []
    for r in recs:
        if r.get("tool") == "company" and r.get("company") and r["company"] not in seen:
            seen.add(r["company"])
            out.append({"company": r["company"], "market": r.get("market", "")})
    return out


def _check_summary(q: str, p: dict) -> dict:
    flags: list[str] = []
    row: dict = {"company": q, "status": str(p.get("status", ""))}
    d = p.get("data") or {}
    if row["status"] != "AnalysisStatus.EXACT" and p.get("status") != "exact":
        # ToolEnvelope.to_dict()는 status를 문자열로 직렬화
        if str(p.get("status")) not in ("exact", "AnalysisStatus.EXACT"):
            flags.append(f"status:{p.get('status')}")
    s = d.get("summary") or {}
    tr = d.get("treasury") or {}
    issued = tr.get("issued_shares", 0) or 0
    mh = d.get("major_holders") or []
    msh = sum(r.get("shares", 0) or 0 for r in mh)
    trsh = s.get("treasury_shares", 0) or 0
    row.update({"issued": issued, "registry_rows": len(mh), "blocks": len(d.get("blocks") or [])})
    if not issued:
        flags.append("ISSUED_0")
    else:
        other = issued - msh - trsh
        if other < 0:
            flags.append(f"NEG_OTHER:{other/issued*100:.2f}%")
        related_pct = s.get("related_total_pct", 0) or 0
        share_pct = msh / issued * 100
        if mh and abs(related_pct - share_pct) > 1.5:
            flags.append(f"PCT_MISMATCH:공시{related_pct:.2f}vs주식수{share_pct:.2f}")
    if not mh:
        flags.append("REGISTRY_EMPTY")
    for b in d.get("blocks") or []:
        pct = b.get("ownership_pct", 0) or 0
        if not (0 < pct <= 100):
            flags.append(f"BLOCK_PCT:{b.get('reporter')}={pct}")
            break
    row["flags"] = flags
    return row


async def main() -> None:
    client = get_dart_client()
    universe = _universe()
    print(f"[ownership audit] {len(universe)}사 시작 (batch {BATCH}, 회사 {SLEEP_COMPANY}s / 배치 {SLEEP_BATCH}s)")
    rows: list[dict] = []
    changes_rows: list[dict] = []
    t0 = time.time()
    for i, item in enumerate(universe):
        q = item["company"]
        try:
            p = await build(q, scope="summary")
            row = _check_summary(q, p)
            row["market"] = item["market"]
            rows.append(row)
            if i < CHANGES_SPOT_N:
                pc = await build(q, scope="changes")
                dd = pc.get("data") or {}
                changes_rows.append({
                    "company": q,
                    "change_filings": len(dd.get("change_filings") or []),
                    "block_changes": len(dd.get("block_changes") or []),
                    "status": str(pc.get("status")),
                })
        except httpx.ReadError as exc:
            print(f"[ABORT] ReadError at {q}: {exc} — 즉시 중단 (재시도 금지)")
            break
        except Exception as exc:  # noqa: BLE001
            rows.append({"company": q, "market": item["market"], "flags": [f"EXC:{type(exc).__name__}"], "error": str(exc)[:80]})
        await asyncio.sleep(SLEEP_COMPANY)
        if (i + 1) % BATCH == 0:
            calls = client.api_call_snapshot()
            elapsed = time.time() - t0
            flagged = sum(1 for r in rows if r.get("flags"))
            print(f"  {i+1}/{len(universe)}  누적콜={calls}  경과={elapsed/60:.1f}분  flag={flagged}")
            await asyncio.sleep(SLEEP_BATCH)

    flagged = [r for r in rows if r.get("flags")]
    from collections import Counter
    kinds = Counter(f.split(":")[0] for r in flagged for f in r["flags"])
    result = {
        "meta": {
            "script": "scripts/ownership_summary_market_audit.py",
            "date": "2026-06-12",
            "universe": f"baseline450 ({len(rows)}사 완료)",
            "context": "2026-06-10 summary 재설계(100% 분해·단독/특관·노이즈컷) 시장 전수 검증",
        },
        "summary": {
            "total": len(rows),
            "clean": len(rows) - len(flagged),
            "flagged": len(flagged),
            "flag_kinds": dict(kinds),
        },
        "flagged_rows": flagged,
        "changes_spot": changes_rows,
        "all_rows": rows,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"[완료] {len(rows)}사  clean={len(rows)-len(flagged)}  flagged={len(flagged)}  종류={dict(kinds)}")
    print(f"→ {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
