"""Step 2 — retirement_pay NO_DATA case 본문 spot 측정.

NO_DATA case (parser miss) 회사 list 입력 → 본문 fetch → 표 raw + 인접 텍스트 출력.
패턴 분류 → parser 강화 input.
"""

from __future__ import annotations
import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path("/Users/marcoyou/Projects/open-proxy-mcp")
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.dart.client import DartClient
from open_proxy_mcp.tools.parser import parse_retirement_pay_xml, parse_agenda_xml


async def spot(ticker: str, name: str, year: int = 2026) -> dict:
    client = DartClient()
    out = {"ticker": ticker, "name": name, "year": year, "patterns": []}
    try:
        # 소집공고 fetch
        results = await client.search_filings_by_ticker(
            ticker=ticker, bgn_de=f"{year-1}1201", end_de=f"{year}0501", pblntf_ty="E"
        )
        agms = [f for f in (results or {}).get("list", []) if "주주총회소집공고" in (f.get("report_nm") or "")]
        if not agms:
            out["error"] = "no AGM notice"
            return out
        rcept_no = agms[0].get("rcept_no")
        out["rcept_no"] = rcept_no
        doc = await client.get_document(rcept_no)
        text = doc.get("text", "") or ""
        html = doc.get("html", "") or ""

        # 안건 list (parse_agenda 결과 — list of agenda dicts)
        try:
            agenda_list = parse_agenda_xml(text, html=html) if html else []
        except Exception:
            agenda_list = []
        agenda_titles = [a.get("title", "") for a in agenda_list if isinstance(a, dict)]
        retirement_titles = [t for t in agenda_titles if "퇴직금" in t or "퇴임위로금" in t]
        out["agenda_titles"] = agenda_titles
        out["retirement_titles"] = retirement_titles

        # parse_retirement_pay_xml 결과
        result = parse_retirement_pay_xml(html)
        out["amendments_count"] = len(result.get("amendments") or [])
        out["amendments_sample"] = (result.get("amendments") or [])[:3]

        # 본문 "퇴직금" 인접 raw 추출 (parser 못 잡은 case 진단용)
        markers = []
        for m in re.finditer(r"퇴직금|퇴임위로금", text[:200000]):
            i = m.start()
            ctx = text[max(0, i-200):i+800]
            markers.append({"pos": i, "context": ctx})
        out["text_keyword_hits"] = len(markers)
        out["text_samples"] = [m["context"] for m in markers[:3]]

        # HTML 표 raw — "퇴직금" 근처 <table>
        # 단순 grep
        html_keyword_count = html.count("퇴직금") + html.count("퇴임위로금")
        out["html_keyword_count"] = html_keyword_count

        # 표 머리 패턴 추정 — "변경전", "변경후", "현행", "개정" 빈도
        out["table_headers"] = {
            "변경전": html.count("변경전"),
            "변경후": html.count("변경후"),
            "현행": html.count("현행"),
            "개정": html.count("개정안"),
            "신설": html.count("신설"),
        }

    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--companies", required=True, help="comma-separated 'ticker:name,...'")
    parser.add_argument("--out", required=True)
    parser.add_argument("--year", type=int, default=2026)
    args = parser.parse_args()

    pairs = []
    for s in args.companies.split(","):
        s = s.strip()
        if ":" in s:
            t, n = s.split(":", 1)
            pairs.append((t.strip(), n.strip()))

    print(f"# spot {len(pairs)} companies")
    results = []
    for ticker, name in pairs:
        r = await spot(ticker, name, args.year)
        marker = "✗" if r.get("error") else ("✓" if r.get("amendments_count") else "○")
        print(f"  {marker} {ticker} {name}: amends={r.get('amendments_count', 0)} text_hits={r.get('text_keyword_hits', 0)} html_hits={r.get('html_keyword_count', 0)}")
        results.append(r)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"# saved → {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
