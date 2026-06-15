"""정관변경(aoi) 파싱 — KOSPI 전수조사 (리츠 제외). 실패 패턴 분류 + 보강 가능성 판단.

production 경로(build_shareholder_meeting_payload, scope=full)로 진단 — tool이 선택한 공시
기준. 실패(정관 안건 있는데 amendments 0)만 그 선택 공시 html에서 패턴 분류:
parse_agenda_details_xml의 library/detail 구조 + section heading + 변경전/변경후 표 유무.

usage: uv run python scripts/aoi_kospi_census.py [N]
raw: wiki/architecture/audits/data/260615_aoi_kospi_census.json
"""
import warnings as W
W.filterwarnings("ignore")
import asyncio, csv, json, os, sys, io, re, time
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
for line in open(".env.local", encoding="utf-8"):
    if line.startswith("DART_API_KEY="):
        os.environ["OPENDART_API_KEY"] = line.split("=", 1)[1].strip()

from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload
from open_proxy_mcp.dart.client import get_dart_client
from open_proxy_mcp.tools.parser import parse_agenda_details_xml
from bs4 import BeautifulSoup

UNIV = "wiki/architecture/audits/data/260525_agenda_parser_marketwide/universe_kospi500.csv"
rows = list(csv.DictReader(open(UNIV, encoding="utf-8")))
# 리츠 제외
companies = [r["company"] for r in rows if "리츠" not in r["company"]]
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else len(companies)
companies = companies[:LIMIT]


def walk(ns):
    for n in ns:
        yield n
        yield from walk(n.get("children") or [])


async def classify_failure(rcept):
    """tool이 고른 공시 html에서 실패 패턴 분류."""
    if not rcept:
        return "rcept_없음"
    try:
        html = (await get_dart_client().get_document_cached(rcept)).get("html", "")
    except Exception as e:
        return f"html_fetch_실패({type(e).__name__})"
    if not html:
        return "html_빈값"
    soup = BeautifulSoup(html, "lxml")
    n_lib = len(soup.find_all("library"))
    has_mok = any("목적사항별" in (t.get_text() or "") for t in soup.find_all("title"))
    details = parse_agenda_details_xml(html)
    charter_detail = any("정관" in (d.get("title", "") + d.get("category", "")) for d in details)
    charter_section = any(
        "정관" in (s.get("heading") or "")
        for d in details for s in d.get("sections", [])
    )
    # 변경전 AND 변경후 표 유무 (전체 html flat)
    flat = re.sub(r"\s+", "", re.sub(r"<[^>]+>", " ", html))
    has_ba_table = ("변경전" in flat or "현행" in flat) and ("변경후" in flat or "개정" in flat)
    if not has_mok and not details:
        return "섹션부재(목적사항별 없음)"
    if charter_section and has_ba_table:
        return "필터갭_잔존(기업은행형 — fix 후 안 나와야)"
    if n_lib == 1 and details and not charter_section:
        return "단일library_섹션드롭(한국금융지주형)"
    if has_ba_table and not charter_section:
        return "표는있는데_정관섹션미인식"
    if not has_ba_table:
        return "변경전후표_자체없음(이미지/서술형 가능)"
    return "기타"


async def audit(name):
    try:
        p = await build_shareholder_meeting_payload(name, scope="full")
        d = p.get("data", {})
        ag = d.get("agendas") or []
        if not ag:
            return {"name": name, "no_notice": True}
        has_articles = any(n.get("category") == "articles_amendment" for n in walk(ag))
        aoi = d.get("aoi_change", {}) or {}
        n_amend = len(aoi.get("amendments") or [])
        out = {"name": name, "has_articles": has_articles, "n_amend": n_amend,
               "rcept": (d.get("selected_meeting") or {}).get("notice_rcept_no", "")}
        if has_articles and n_amend == 0:
            out["fail_pattern"] = await classify_failure(out["rcept"])
        return out
    except Exception as e:
        return {"name": name, "err": f"{type(e).__name__}: {e}"}


async def main():
    print(f"KOSPI {len(rows)} − 리츠 = {len(companies)}사 전수 (production 경로 scope=full)")
    client = get_dart_client()
    res, t0 = [], time.monotonic()
    c0 = client.api_call_snapshot()
    for i in range(0, len(companies), 16):
        res.extend(await asyncio.gather(*(audit(n) for n in companies[i:i+16])))
        done = min(i + 16, len(companies))
        if done % 80 == 0 or done == len(companies):
            print(f"  ... {done}/{len(companies)} ({time.monotonic()-t0:.0f}s, ~{client.api_call_snapshot()-c0}콜)")

    ok = [r for r in res if "has_articles" in r]
    err = [r for r in res if r.get("err")]
    no_notice = [r for r in res if r.get("no_notice")]
    has_art = [r for r in ok if r["has_articles"]]
    fails = [r for r in has_art if r["n_amend"] == 0]
    print(f"\n분석 {len(ok)} / 공고없음 {len(no_notice)} / 에러 {len(err)}")
    print(f"정관 안건 보유 {len(has_art)}사 / 그중 amendments=0 실패 {len(fails)}사 ({len(fails)/max(len(has_art),1)*100:.1f}%)")
    pat = Counter(r["fail_pattern"] for r in fails)
    print("\n=== 실패 패턴 분류 ===")
    for p2, c in pat.most_common():
        names = [r["name"] for r in fails if r["fail_pattern"] == p2][:8]
        print(f"  {c:3d}  {p2}  {names}")
    if err:
        print("\n에러 샘플:", [(r["name"], r["err"][:40]) for r in err][:8])

    json.dump({"universe_kospi": len(rows), "ex_reits": len(companies),
               "analyzed": len(ok), "has_articles": len(has_art), "fails": len(fails),
               "patterns": dict(pat),
               "fail_detail": [{"name": r["name"], "pattern": r["fail_pattern"], "rcept": r.get("rcept", "")} for r in fails],
               "errors": [{"name": r["name"], "err": r["err"]} for r in err]},
              open("wiki/architecture/audits/data/260615_aoi_kospi_census.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("\n저장: wiki/architecture/audits/data/260615_aoi_kospi_census.json")


asyncio.run(main())
