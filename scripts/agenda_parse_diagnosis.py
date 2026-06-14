"""주총 안건(agenda) 파싱 전반 진단 — 검출률·제목·카테고리·안건종류 분포.

compensation 진단과 동일 방법론을 agenda scope 전체로 확장:
  - 안건 검출 (no_agenda / 개수)
  - 제목 파싱 품질 (빈 제목 / 비정상 길이)
  - 카테고리 분류 (category None 비율)
  - 번호 파싱 (number None)
  - 안건 종류 분포 (제목 키워드 기준) — 종류별 세부 파서 점검 우선순위 산정
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from collections import Counter
from pathlib import Path

import httpx

from open_proxy_mcp.dart.client import get_dart_client
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload as sm

UNIVERSE_FILE = os.environ.get("UNIVERSE_FILE", "/tmp/kospi_kosdaq_300.json")
OUT = Path(os.environ.get("AUDIT_OUT", "wiki/architecture/audits/data/260615_agenda_parse_diagnosis.json"))
BATCH = 30
SLEEP_COMPANY = 0.5
SLEEP_BATCH = 15.0


def _kind(title: str) -> str:
    t = title or ""
    if "선임" in t or "선출" in t:
        return "선임"
    if "보수한도" in t or ("보수" in t and "한도" in t):
        return "보수한도"
    if "정관" in t:
        return "정관변경"
    if "재무제표" in t or "재무상태표" in t or "이익잉여금처분" in t:
        return "재무제표"
    if "자기주식" in t or "자사주" in t:
        return "자기주식"
    if "배당" in t:
        return "배당"
    if "감액" in t or "감자" in t:
        return "자본감액"
    if "합병" in t or "분할" in t or "주식교환" in t or "양수도" in t or "영업양도" in t:
        return "지배구조"
    if "해임" in t:
        return "해임"
    if "퇴직" in t or "退職" in t:
        return "퇴직금"
    return "기타"


async def _diag(q: str) -> dict:
    p = await sm(q, scope="agenda", year=2026, meeting_type="annual")
    d = p.get("data") or {}
    ag = d.get("agenda") or d.get("agendas") or []
    n = len(ag)
    empty_title = sum(1 for a in ag if len((a.get("title") or "").strip()) < 5)
    cat_none = sum(1 for a in ag if not a.get("category"))
    no_number = sum(1 for a in ag if not a.get("number"))
    kinds = Counter(_kind(a.get("title") or "") for a in ag)
    return {
        "company": d.get("canonical_name") or q, "query": q,
        "status": str(p.get("status")),
        "agenda_count": n,
        "no_agenda": n == 0,
        "empty_title": empty_title,
        "category_none": cat_none,
        "no_number": no_number,
        "kinds": dict(kinds),
        "sample_titles": [(a.get("title") or "")[:40] for a in ag[:3]],
    }


async def main() -> None:
    universe = json.loads(Path(UNIVERSE_FILE).read_text())
    client = get_dart_client()
    calls0 = client.api_call_snapshot()
    t0 = time.time()
    rows = []
    print(f"[안건 파싱 진단] {len(universe)}사")
    for i, q in enumerate(universe):
        try:
            rows.append(await _diag(q))
        except httpx.ReadError as exc:
            print(f"  [ABORT] ReadError at {q}: {exc}")
            break
        except Exception as exc:  # noqa: BLE001
            rows.append({"company": q, "query": q, "status": "EXC", "error": str(exc)[:60]})
        await asyncio.sleep(SLEEP_COMPANY)
        if (i + 1) % BATCH == 0:
            calls = client.api_call_snapshot() - calls0
            print(f"  {i+1}/{len(universe)} 누적콜={calls} {(time.time()-t0)/60:.1f}분")
            await asyncio.sleep(SLEEP_BATCH)

    evaluated = [r for r in rows if not r.get("error")]
    total_agenda = sum(r.get("agenda_count", 0) for r in evaluated)
    no_agenda = [r for r in evaluated if r.get("no_agenda")]
    kind_total: Counter = Counter()
    for r in evaluated:
        kind_total.update(r.get("kinds", {}))
    summary = {
        "companies": len(evaluated),
        "no_agenda_companies": len(no_agenda),
        "total_agenda_items": total_agenda,
        "empty_title_total": sum(r.get("empty_title", 0) for r in evaluated),
        "category_none_total": sum(r.get("category_none", 0) for r in evaluated),
        "no_number_total": sum(r.get("no_number", 0) for r in evaluated),
        "kind_distribution": dict(kind_total.most_common()),
    }
    result = {
        "meta": {"date": "2026-06-15", "universe": f"{UNIVERSE_FILE} ({len(rows)}사)",
                 "total_dart_calls": client.api_call_snapshot() - calls0,
                 "elapsed_min": round((time.time() - t0) / 60, 1)},
        "summary": summary,
        "no_agenda_companies": [r["company"] for r in no_agenda],
        "rows": rows,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n[완료] {summary['companies']}사 / 총 안건 {total_agenda}")
    print(f"  no_agenda(안건0): {len(no_agenda)}사 — {[r['company'] for r in no_agenda][:10]}")
    print(f"  빈 제목: {summary['empty_title_total']} / 카테고리 None: {summary['category_none_total']} ({summary['category_none_total']/max(total_agenda,1)*100:.0f}%) / 번호 None: {summary['no_number_total']}")
    print(f"  안건 종류 분포: {summary['kind_distribution']}")
    print(f"  총 DART콜 {result['meta']['total_dart_calls']}, {result['meta']['elapsed_min']}분 → {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
