#!/usr/bin/env python3
"""DART 뷰어 목차·절 실측 — `opm://filing/{rcept_no}/toc`·`/section/{no}` 리소스 설계용 (260906).

무엇을 재나
  · 목차: main.do 한 번으로 절이 몇 개 나오나, 그 HTML 이 몇 바이트인가.
  · 절: 절 하나의 HTML 바이트 · 정제 텍스트 글자 수 · 캐시에 넣으면 몇 바이트인가(`_cache_entry_bytes`,
    운영 LRU 가 실제로 세는 자) · 왕복 시간.
  · 프로세스: 시작 전후 RSS. 캐시 예산(OPM_DOC_CACHE_MB=96)·VM 한도(1,024MB) 와 견준다.

어떻게 쓰나 — 로컬 터미널에서(원격 샌드박스는 dart.fss.or.kr 이 막혀 있다). `.env` 의 OPENDART_API_KEY 가
있어야 한다(클라이언트 생성자 요구. 뷰어 호출 자체는 키를 쓰지 않는다)
  uv run python scripts/probe_viewer_sections.py 20260310002820                # 표본 절만 (직원·주석·사업의 내용)
  uv run python scripts/probe_viewer_sections.py 20260310002820 --all          # 전 절 (절 수 × 1~2초)
  uv run python scripts/probe_viewer_sections.py 20260310002820 20250814002379 --all --json out.json

CLAUDE.md 규칙 7(웹 스크래핑 1~2초 랜덤·배치 금지)을 지킨다 — 절을 **순서대로 하나씩**, 클라이언트의
`_throttle_web` 을 그대로 탄다. 일회성 계측이며 서빙 경로에 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import resource
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.dart.client import DartClient, _cache_entry_bytes  # noqa: E402

SAMPLE_KEYWORDS = ("직원", "주석", "사업의 내용", "배당", "주주에 관한")


def _rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1024 if peak > 10 ** 7 else 1) / 1024, 1)   # linux KB · mac bytes


async def probe(rcept_no: str, all_sections: bool) -> dict:
    client = DartClient()   # 생성자가 키를 요구한다(.env OPENDART_API_KEY). 뷰어 호출 자체는 키를 쓰지 않는다
    t0 = time.perf_counter()
    main_html = await client._fetch_viewer_main_html(rcept_no)
    t_main = time.perf_counter() - t0
    nodes = client._extract_viewer_nodes(main_html)
    toc = [{"no": n.get("tocNo", ""), "text": n.get("text", ""), "eleId": n.get("eleId", "")} for n in nodes]
    out = {
        "rcept_no": rcept_no,
        "main_html_bytes": len(main_html.encode("utf-8")),
        "main_sec": round(t_main, 2),
        "n_sections": len(nodes),
        "toc_cache_bytes": _cache_entry_bytes(toc),
        "sections": [],
    }
    targets = nodes if all_sections else [
        n for n in nodes if any(k in n.get("text", "") for k in SAMPLE_KEYWORDS)
    ]
    for n in targets:
        t1 = time.perf_counter()
        try:
            html = await client._fetch_viewer_section_html(n)
        except Exception as exc:  # noqa: BLE001 - 계측: 실패도 한 줄로 남긴다
            out["sections"].append({"no": n.get("tocNo"), "text": n.get("text"), "error": str(exc)[:80]})
            continue
        text = client._html_to_text(html)
        out["sections"].append({
            "no": n.get("tocNo", ""), "text": n.get("text", ""),
            "html_bytes": len(html.encode("utf-8")),
            "text_chars": len(text),
            "cache_bytes_html_and_text": _cache_entry_bytes({"html": html, "text": text}),
            "cache_bytes_text_only": _cache_entry_bytes({"text": text}),
            "sec": round(time.perf_counter() - t1, 2),
            "n_tables": html.count("<table"),
        })
    return out


def _fmt(n: float) -> str:
    return f"{n:,.0f}"


def render(r: dict) -> str:
    L = [f"# {r['rcept_no']} — 목차 {r['n_sections']}절 · main.do {_fmt(r['main_html_bytes'])}B "
         f"({r['main_sec']}s) · 목차 캐시 {_fmt(r['toc_cache_bytes'])}B", "",
         "| no | 절 | HTML B | 텍스트 자 | 캐시 B(html+text) | 캐시 B(text) | 표 | 초 |",
         "|---|---|---|---|---|---|---|---|"]
    tot_html = tot_text = tot_cache = 0
    for s in r["sections"]:
        if "error" in s:
            L.append(f"| {s['no']} | {s['text']} | 실패: {s['error']} | | | | | |")
            continue
        tot_html += s["html_bytes"]; tot_text += s["text_chars"]; tot_cache += s["cache_bytes_html_and_text"]
        L.append(f"| {s['no']} | {s['text']} | {_fmt(s['html_bytes'])} | {_fmt(s['text_chars'])} | "
                 f"{_fmt(s['cache_bytes_html_and_text'])} | {_fmt(s['cache_bytes_text_only'])} | "
                 f"{s['n_tables']} | {s['sec']} |")
    L += ["", f"합계(실측 절만): HTML {_fmt(tot_html)}B · 텍스트 {_fmt(tot_text)}자 · "
              f"캐시 {tot_cache/1024/1024:.2f}MB (html+text)"]
    if r["sections"] and len(r["sections"]) == r["n_sections"]:
        L.append(f"→ 이 문서 전 절을 캐시하면 {tot_cache/1024/1024:.2f}MB — "
                 f"OPM_DOC_CACHE_MB=96 의 {100*tot_cache/1024/1024/96:.1f}%")
    return "\n".join(L)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rcept_nos", nargs="+")
    ap.add_argument("--all", action="store_true", help="표본이 아니라 전 절 (절 수 × 1~2초)")
    ap.add_argument("--json", help="원자료 저장 경로")
    a = ap.parse_args()
    rss0 = _rss_mb()
    results = []
    for rno in a.rcept_nos:
        r = await probe(rno, a.all)
        results.append(r)
        print(render(r)); print()
    print(f"프로세스 peak RSS: 시작 {rss0}MB → 끝 {_rss_mb()}MB (VM 한도 1,024MB · 캐시 예산 96MB)")
    if a.json:
        Path(a.json).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"원자료 → {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
