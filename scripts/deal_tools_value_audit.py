"""dilutive_issuance / corporate_restructuring / corporate_deals 값 정확도 audit.

배경: 260517 baseline은 파싱 '성공률'(exact/no_filing)만 검증 — 필드 '값'은 미검증.
유니버스: baseline에서 해당 tool이 exact였던 회사만 (값이 있는 곳만 타격).
  dilutive 115 / restructuring 71 / deals 281→100 샘플.

값 검증 (필드명 휴리스틱):
  - 금액류(amount/price/금액/가액/prc/fta): 비어있지 않으면 숫자 파싱 가능 + > 0
  - 비율류(pct/ratio/rt/비율): 숫자 파싱 가능, pct_of_total은 (0, 100]
  - 날짜류(date/_dt/일자): YYYY-MM-DD 또는 YYYYMMDD
  - coverage: 핵심 값 필드 비어있는 행 비율 (deals 본문 regex 추출 실패 탐지)

페이싱: 순차, 회사 0.5s, 30사 배치 20s, ReadError 즉시 중단.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from collections import Counter
from pathlib import Path

import httpx

from open_proxy_mcp.dart.client import get_dart_client
from open_proxy_mcp.services.dilutive_issuance import build_dilutive_issuance_payload
from open_proxy_mcp.services.corporate_restructuring import build_corporate_restructuring_payload
from open_proxy_mcp.services.corporate_deals import build_corporate_deals_payload

OUT = Path("wiki/architecture/audits/data/260612_deal_tools_value_audit.json")
BATCH, SLEEP_COMPANY, SLEEP_BATCH = 30, 0.5, 20.0
DEALS_SAMPLE = 100

_AMOUNT_KEY = re.compile(r"amount|price|prc|fta|금액|가액", re.I)
_PCT_KEY = re.compile(r"pct|ratio|_rt$|비율", re.I)
_DATE_KEY = re.compile(r"date|_dt$|일자", re.I)
_NUM = re.compile(r"^-?[\d,]+(\.\d+)?$")
_DATE_FMT = re.compile(r"^\d{4}-\d{2}-\d{2}$|^\d{8}$")


def _validate_row(row: dict, path: str, issues: list[dict], company: str) -> tuple[int, int]:
    """이벤트 행의 값 필드 검증. returns (값필드 수, 채워진 수)."""
    fields = 0
    filled = 0
    for k, v in row.items():
        if isinstance(v, dict):
            f2, fl2 = _validate_row(v, f"{path}.{k}", issues, company)
            fields += f2
            filled += fl2
            continue
        if not isinstance(v, str):
            continue
        is_amount = bool(_AMOUNT_KEY.search(k))
        is_pct = bool(_PCT_KEY.search(k)) and not is_amount
        is_date = bool(_DATE_KEY.search(k)) and not is_amount and not is_pct
        if not (is_amount or is_pct or is_date):
            continue
        fields += 1
        if not v or v == "-":
            continue
        filled += 1
        if is_amount:
            if not _NUM.match(v.replace(" ", "")):
                issues.append({"company": company, "path": f"{path}.{k}", "kind": "AMOUNT_NOT_NUMERIC", "value": v[:60]})
            elif float(v.replace(",", "")) < 0:
                issues.append({"company": company, "path": f"{path}.{k}", "kind": "AMOUNT_NEGATIVE", "value": v[:60]})
        elif is_pct:
            cleaned = v.replace(",", "").replace("%", "").strip()
            if _NUM.match(cleaned):
                num = float(cleaned)
                if "pct_of_total" in k and not (0 < num <= 100):
                    issues.append({"company": company, "path": f"{path}.{k}", "kind": "PCT_RANGE", "value": v[:60]})
            # 비율은 "1:0.123" / 서술형도 합법 (mg_rt 등) — 숫자 아니어도 issue 아님
        elif is_date:
            if not _DATE_FMT.match(v.strip()):
                issues.append({"company": company, "path": f"{path}.{k}", "kind": "DATE_FORMAT", "value": v[:60]})
    return fields, filled


def _iter_event_rows(data: dict):
    for k, v in (data or {}).items():
        if isinstance(v, list) and v and isinstance(v[0], dict) and ("rcept_no" in v[0] or "rcept_dt" in v[0]):
            for row in v:
                yield k, row


async def _audit_tool(name: str, build, companies: list[str], client) -> dict:
    issues: list[dict] = []
    coverage = Counter()
    rows_total = 0
    errors = []
    t0 = time.time()
    print(f"[{name}] {len(companies)}사 시작")
    for i, q in enumerate(companies):
        try:
            p = await build(q, scope="summary")
            data = p.get("data") or {}
            for list_key, row in _iter_event_rows(data):
                rows_total += 1
                f, fl = _validate_row(row, list_key, issues, q)
                coverage["fields"] += f
                coverage["filled"] += fl
        except httpx.ReadError as exc:
            print(f"[ABORT] ReadError at {q} — 즉시 중단")
            errors.append({"company": q, "error": f"ReadError: {exc}"[:80]})
            break
        except Exception as exc:  # noqa: BLE001
            errors.append({"company": q, "error": f"{type(exc).__name__}: {exc}"[:80]})
        await asyncio.sleep(SLEEP_COMPANY)
        if (i + 1) % BATCH == 0:
            print(f"  {name} {i+1}/{len(companies)}  콜={client.api_call_snapshot()}  행={rows_total}  issue={len(issues)}")
            await asyncio.sleep(SLEEP_BATCH)
    fill_rate = coverage["filled"] / coverage["fields"] * 100 if coverage["fields"] else 0
    print(f"[{name}] 완료 {time.time()-t0:.0f}s — 행 {rows_total}, 값필드 채움률 {fill_rate:.1f}%, issue {len(issues)}, error {len(errors)}")
    return {
        "companies": len(companies), "event_rows": rows_total,
        "value_fields": coverage["fields"], "filled": coverage["filled"],
        "fill_rate_pct": round(fill_rate, 1),
        "issues": issues, "issue_kinds": dict(Counter(i["kind"] for i in issues)),
        "errors": errors,
    }


async def main() -> None:
    client = get_dart_client()
    plans = [
        ("dilutive_issuance", build_dilutive_issuance_payload, json.load(open("/tmp/exact_dilutive_issuance.json"))),
        ("corporate_restructuring", build_corporate_restructuring_payload, json.load(open("/tmp/exact_corporate_restructuring.json"))),
        ("corporate_deals", build_corporate_deals_payload, json.load(open("/tmp/exact_related_party_transaction.json"))[:DEALS_SAMPLE]),
    ]
    results = {}
    for name, build, companies in plans:
        results[name] = await _audit_tool(name, build, companies, client)
        await asyncio.sleep(30)  # tool 간 휴식
    OUT.write_text(json.dumps({
        "meta": {"script": "scripts/deal_tools_value_audit.py", "date": "2026-06-12",
                 "context": "260517은 파싱 성공률만 검증 — 값 필드 정확도(숫자/범위/형식/coverage) 첫 audit"},
        "results": results,
    }, ensure_ascii=False, indent=2))
    print(f"→ {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
