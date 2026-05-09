"""안건 호수 hierarchy 추출 정확도 진단 (Ralph 7 iter 1).

10 회사 raw vs parser 출력 비교.
- raw에서 "제N호" / "제N-M호" / "제N-M-K호" 직접 grep
- parse_agenda_xml 호출 후 number / level1-3 / title 출력
- 누락 / 오인식 cataloging
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.dart.client import get_dart_client  # noqa: E402
from open_proxy_mcp.services.shareholder_meeting import (  # noqa: E402
    _candidate_notices_in_meeting_window,
)
from open_proxy_mcp.tools.parser import parse_agenda_xml  # noqa: E402

from datetime import date, timedelta  # noqa: E402

TARGETS = [
    # 4 미매치 회사 (Ralph 6)
    ("247540", "에코프로비엠"),
    ("293490", "카카오게임즈"),
    ("041510", "에스엠"),
    ("138040", "메리츠금융지주"),
    # sub 명확 (LG화학)
    ("051910", "LG화학"),
    # KOSPI 5 추가
    ("005930", "삼성전자"),
    ("000660", "SK하이닉스"),
    ("005380", "현대차"),
    ("035420", "NAVER"),
    ("068270", "셀트리온"),
]

# raw에서 호수 직접 grep — 다양한 변형
RAW_HO_PATTERNS = [
    r'제\s*\d+\s*(?:-\s*\d+)*\s*호',  # 표준 "제N호" / "제N-M호" / "제N-M-K호"
    r'\(\s*제\s*\d+\s*(?:-\s*\d+)*\s*호\s*\)',  # 괄호형
]


async def _resolve_company(name: str) -> str | None:
    client = get_dart_client()
    try:
        match = await client.lookup_corp_code(name)
        if match:
            return match.get("corp_code")
    except Exception:
        pass
    return None


async def _diagnose_one(ticker: str, name: str) -> dict:
    out = {"ticker": ticker, "name": name}

    corp_code = await _resolve_company(name)
    if not corp_code:
        out["error"] = "corp_code not found"
        return out
    out["corp_code"] = corp_code

    today = date.today()
    notices, _ = await _candidate_notices_in_meeting_window(
        corp_code, "정기", today - timedelta(days=120), today + timedelta(days=120),
    )
    if not notices:
        out["error"] = "no notice"
        return out
    notice = notices[0]
    rcept_no = notice.get("rcept_no")
    out["rcept_no"] = rcept_no
    out["disclosure_date"] = notice.get("disclosure_date")

    client = get_dart_client()
    doc = await client.get_document_cached(rcept_no)
    text = doc.get("text", "") or ""
    html = doc.get("html", "") or ""

    # raw 호수 표기 grep (전체 doc — 안건 영역 외도 hit, 단순 표기 통계용)
    raw_hos: list[str] = []
    for pat in RAW_HO_PATTERNS:
        for m in re.finditer(pat, text):
            raw_hos.append(m.group(0).strip())
    raw_unique = sorted(set(raw_hos), key=lambda s: (
        int(re.search(r'\d+', s).group(0)) if re.search(r'\d+', s) else 999,
        s,
    ))
    out["raw_ho_unique"] = raw_unique
    out["raw_ho_total_occurrences"] = len(raw_hos)

    # parser 호출
    parsed = parse_agenda_xml(text, html)

    def _flatten(items, depth=0):
        rows = []
        for it in items:
            rows.append({
                "depth": depth,
                "number": it.get("number"),
                "level1": it.get("level1"),
                "level2": it.get("level2"),
                "level3": it.get("level3"),
                "title": (it.get("title") or "")[:80],
                "n_children": len(it.get("children") or []),
            })
            rows.extend(_flatten(it.get("children") or [], depth + 1))
        return rows

    parsed_flat = _flatten(parsed)
    out["parser_n_top"] = len(parsed)
    out["parser_n_total"] = len(parsed_flat)
    out["parser_items"] = parsed_flat
    out["parser_numbers"] = [r["number"] for r in parsed_flat]

    # 비교: raw에 있는 호수 중 parser 누락
    parser_numbers_set = set(out["parser_numbers"])
    raw_numbers_set = set(raw_unique)
    out["raw_only"] = sorted(raw_numbers_set - parser_numbers_set)
    out["parser_only"] = sorted(parser_numbers_set - raw_numbers_set)

    # 안건 영역 직접 추출 (raw 호수 표기 패턴 grep을 안건 영역 한정으로)
    # 회의목적사항 ~ 다음 섹션 사이만
    zone_match = re.search(
        r'(회의\s*(?:의\s*)?목적\s*사항|결의\s*사항|부의\s*안건|의결\s*사항)(.+?)'
        r'(?:전자\s*투표|의결권\s*행사|배당\s*예정|경영\s*참고|주주총회\s*소집|$)',
        text,
        re.DOTALL,
    )
    if zone_match:
        zone = zone_match.group(2)[:8000]
        zone_hos: list[str] = []
        for pat in RAW_HO_PATTERNS:
            for m in re.finditer(pat, zone):
                zone_hos.append(m.group(0).strip())
        zone_unique = sorted(set(zone_hos), key=lambda s: (
            int(re.search(r'\d+', s).group(0)) if re.search(r'\d+', s) else 999,
            s,
        ))
        out["zone_ho_unique"] = zone_unique
        out["zone_only"] = sorted(set(zone_unique) - parser_numbers_set)
    else:
        out["zone_ho_unique"] = []
        out["zone_only"] = []

    # raw doc 일부 저장 (debug용)
    out["text_len"] = len(text)
    out["html_len"] = len(html)

    return out


async def _run(args):
    archive = ROOT / "wiki/architecture/audits/data/260510_agenda_hierarchy"
    archive.mkdir(parents=True, exist_ok=True)

    sem = asyncio.Semaphore(2)  # rate limit 안전

    async def _wrapped(t, n):
        async with sem:
            print(f"  → {n} ({t}) ...", flush=True)
            return await _diagnose_one(t, n)

    results = await asyncio.gather(*[_wrapped(t, n) for t, n in TARGETS])

    # 요약 표
    print("\n=== 호수 hierarchy 진단 요약 ===")
    print(f"{'회사':<14} {'parser_top':>10} {'parser_total':>14} {'raw_only':>9} {'zone_only':>10}")
    for r in results:
        if "error" in r:
            print(f"{r['name']:<14} ERROR: {r['error']}")
            continue
        ro = len(r.get("raw_only") or [])
        zo = len(r.get("zone_only") or [])
        print(f"{r['name']:<14} {r['parser_n_top']:>10} {r['parser_n_total']:>14} {ro:>9} {zo:>10}")

    # 회사별 상세 dump
    out_path = archive / "raw_vs_parser_10.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nsaved: {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    args = ap.parse_args()
    asyncio.run(_run(args))
