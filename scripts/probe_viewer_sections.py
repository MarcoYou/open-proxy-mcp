#!/usr/bin/env python3
"""DART 뷰어 목차·절 실측 — `opm://filing/{rcept_no}/toc`·`/section/{no}` 리소스 설계용 (260906).

무엇을 재나
  · 목차: main.do 한 번으로 노드가 몇 개(장/절/항 전 계층) 나오나, leaf(하위 없는 노드)가 몇 개인가.
  · 절: 절 하나의 HTML 바이트 · 정제 텍스트 글자 수 · 캐시에 넣으면 몇 바이트인가(`_cache_entry_bytes`,
    운영 LRU 가 실제로 세는 자) · `<table>` 수(뷰어는 대문자 태그라 대소문자 무시) · 왕복 시간.
  · 프로세스: 시작 전후 RSS. 캐시 예산(OPM_DOC_CACHE_MB=96)·VM 한도(1,024MB) 와 견준다.

260906 실측에서 배운 것 (11개 사업보고서, wiki/handoff/260906_filing-section-resources.md §2)
  · `_extract_viewer_nodes` 는 node1(장)만 잡는다 → 여기서는 `_extract_node_tree`(전 계층)를 쓴다.
  · viewer.do 는 부모 노드를 부르면 하위를 통째로 준다(부모 글자 수 = 자식 합). 그래서 leaf 표시가 필요하다.
  · 실패는 30초 ReadTimeout 이 드물게(1,358절 중 2) 나고 재시도에서 받힌다 — 예외 클래스와 경과 시간을 남긴다.

어떻게 쓰나 — 로컬 터미널에서(원격 샌드박스는 dart.fss.or.kr 이 막혀 있다). `.env` 의 OPENDART_API_KEY 가
있어야 한다(클라이언트 생성자 요구. 뷰어 호출 자체는 키를 쓰지 않는다)
  uv run python scripts/probe_viewer_sections.py 20260310002820                # 표본 절만 (직원·주석·사업의 내용)
  uv run python scripts/probe_viewer_sections.py 20260310002820 --all          # 전 노드 (노드 수 × 1~2초)
  uv run python scripts/probe_viewer_sections.py 20260310002820 --all --leaf   # leaf 만 (부모는 자식 합이라 생략)
  uv run python scripts/probe_viewer_sections.py 20260310002820 20250814002379 --all --json out.json

CLAUDE.md 규칙 7 — 웹 긁기는 **공유 시계 하나**(`_throttle_web`, 1~2초 랜덤)를 지난다. 동시 수를 그 시계에
맞추면 되고, 프로세스를 나누면 시계가 둘이 되므로 한 프로세스에서 순서대로 받는다. 일회성 계측이며 서빙 경로에 쓰지 않는다.
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
from open_proxy_mcp.services.filing_sections import mark_tree  # noqa: E402

SAMPLE_KEYWORDS = ("직원", "주석", "사업의 내용", "배당", "주주에 관한")


def _rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1024 if peak > 10 ** 7 else 1) / 1024, 1)   # linux KB · mac bytes


async def probe(rcept_no: str, all_sections: bool, leaf_only: bool) -> dict:
    client = DartClient()   # 생성자가 키를 요구한다(.env OPENDART_API_KEY). 뷰어 호출 자체는 키를 쓰지 않는다
    t0 = time.perf_counter()
    main_html = await client._fetch_viewer_main_html(rcept_no)
    t_main = time.perf_counter() - t0
    nodes = mark_tree(main_html)          # 전 계층 + leaf 표시 + 부모 경로 (리소스와 같은 함수)
    toc = [{"no": n["no"], "level": n["level"], "text": n["text"]} for n in nodes]
    out = {
        "rcept_no": rcept_no,
        "main_html_bytes": len(main_html.encode("utf-8")),
        "main_sec": round(t_main, 2),
        "n_nodes": len(nodes),
        "n_leaf": sum(1 for n in nodes if n["leaf"]),
        "toc_cache_bytes": _cache_entry_bytes(toc),
        "rss_start_mb": _rss_mb(),
        "sections": [],
    }
    if all_sections:
        targets = [n for n in nodes if n["leaf"]] if leaf_only else nodes
    else:
        targets = [n for n in nodes if n["leaf"] and any(k in n["text"] for k in SAMPLE_KEYWORDS)]
    for n in targets:
        t1 = time.perf_counter()
        row = {"no": n["no"], "level": n["level"], "leaf": n["leaf"], "text": n["text"],
               "toc_length": n["length"]}
        try:
            html = await client._fetch_viewer_section_html(n["node"])
        except Exception as exc:  # noqa: BLE001 - 계측: 실패도 한 줄로 남긴다 (클래스·경과·메시지)
            row.update({"error": f"{type(exc).__name__}: {str(exc)[:60]}",
                        "sec": round(time.perf_counter() - t1, 2)})
            out["sections"].append(row)
            continue
        text = client._html_to_text(html)
        row.update({
            "html_bytes": len(html.encode("utf-8")),
            "text_chars": len(text),
            "cache_bytes_html_and_text": _cache_entry_bytes({"html": html, "text": text}),
            "cache_bytes_text_only": _cache_entry_bytes({"text": text}),
            "sec": round(time.perf_counter() - t1, 2),
            "n_tables": html.lower().count("<table"),
            "rss_after_mb": _rss_mb(),
        })
        out["sections"].append(row)
    out["rss_end_mb"] = _rss_mb()
    return out


def _fmt(n: float) -> str:
    return f"{n:,.0f}"


def render(r: dict) -> str:
    L = [f"# {r['rcept_no']} — 목차 {r['n_nodes']}노드 (leaf {r['n_leaf']}) · main.do {_fmt(r['main_html_bytes'])}B "
         f"({r['main_sec']}s) · 목차 캐시 {_fmt(r['toc_cache_bytes'])}B", "",
         "| no | L | 절 | HTML B | 텍스트 자 | 캐시 B(html+text) | 캐시 B(text) | 표 | 초 |",
         "|---|---|---|---|---|---|---|---|---|"]
    tot_html = tot_text = tot_cache = tot_cache_text = 0
    leaf_chars = []
    for s in r["sections"]:
        mark = "" if s["leaf"] else "▸"
        if "error" in s:
            L.append(f"| {s['no']} | {s['level']} | {mark}{s['text']} | 실패: {s['error']} ({s['sec']}s) | | | | | |")
            continue
        tot_html += s["html_bytes"]; tot_text += s["text_chars"]
        tot_cache += s["cache_bytes_html_and_text"]; tot_cache_text += s["cache_bytes_text_only"]
        if s["leaf"]:
            leaf_chars.append(s["text_chars"])
        L.append(f"| {s['no']} | {s['level']} | {mark}{s['text']} | {_fmt(s['html_bytes'])} | {_fmt(s['text_chars'])} | "
                 f"{_fmt(s['cache_bytes_html_and_text'])} | {_fmt(s['cache_bytes_text_only'])} | "
                 f"{s['n_tables']} | {s['sec']} |")
    L += ["", f"합계(실측 노드만 · ▸=부모, 부모는 자식을 통째로 다시 준다): HTML {_fmt(tot_html)}B · 텍스트 {_fmt(tot_text)}자 · "
              f"캐시 {tot_cache/1024/1024:.2f}MB (html+text) / {tot_cache_text/1024/1024:.2f}MB (text)"]
    if leaf_chars:
        leaf_chars.sort()
        L.append(f"leaf {len(leaf_chars)}절: 중앙 {_fmt(leaf_chars[len(leaf_chars)//2])}자 · 최대 {_fmt(leaf_chars[-1])}자 · "
                 f"4만 자 초과 {sum(1 for c in leaf_chars if c > 40_000)}개 · "
                 f"leaf text 캐시 {sum(s['cache_bytes_text_only'] for s in r['sections'] if s.get('leaf') and 'error' not in s)/1024/1024:.2f}MB "
                 f"(OPM_DOC_CACHE_MB=96 의 {100*sum(s['cache_bytes_text_only'] for s in r['sections'] if s.get('leaf') and 'error' not in s)/1024/1024/96:.1f}%)")
    L.append(f"peak RSS: 시작 {r['rss_start_mb']}MB → 끝 {r['rss_end_mb']}MB")
    return "\n".join(L)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rcept_nos", nargs="+")
    ap.add_argument("--all", action="store_true", help="표본이 아니라 전 노드 (노드 수 × 1~2초)")
    ap.add_argument("--leaf", action="store_true", help="--all 에서 leaf 만 (부모 노드 생략)")
    ap.add_argument("--json", help="원자료 저장 경로")
    a = ap.parse_args()
    rss0 = _rss_mb()
    results = []
    for rno in a.rcept_nos:
        r = await probe(rno, a.all, a.leaf)
        results.append(r)
        print(render(r)); print()
    print(f"프로세스 peak RSS: 시작 {rss0}MB → 끝 {_rss_mb()}MB (VM 한도 1,024MB · 캐시 예산 96MB)")
    if a.json:
        Path(a.json).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"원자료 → {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
