"""안건 유형별 파싱 품질 전수조사 — 타입화 parse_status 필요성 판단용.

보수한도(parse_status)·잠정재무제표(extraction_status)는 숫자+단위라 "그럴듯하게 틀림"
위험이 있어 타입화 status를 깔았다. 나머지 안건(이사선임·정관변경 등)은 텍스트라 위험이
다르다. 이 스크립트는 "안건 트리엔 있는데 상세 파싱이 빈"(silent-failure) 비율을 유형별로
측정해, 추가 타입화가 필요한 사각이 있는지 판단한다.

usage: uv run python scripts/agenda_typed_status_audit.py [N]   (N=표본 상한, 기본 320)
"""
import warnings as W
W.filterwarnings("ignore")
import asyncio, json, os, sys, io, time
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
for line in open(".env.local", encoding="utf-8"):
    if line.startswith("DART_API_KEY="):
        os.environ["OPENDART_API_KEY"] = line.split("=", 1)[1].strip()

from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload

LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 320
KOSPI_CAP, KOSDAQ_CAP = 200, 120

d = json.load(open("wiki/architecture/audits/data/260517_parsing_success_rate_audit/baseline_company_sample_450.json", encoding="utf-8"))
seen, kospi, kosdaq = set(), [], []
for r in d["records"]:
    if r["ticker"] in seen:
        continue
    seen.add(r["ticker"])
    if r["market"] == "KOSPI" and len(kospi) < KOSPI_CAP:
        kospi.append(r["company"])
    elif r["market"] == "KOSDAQ" and len(kosdaq) < KOSDAQ_CAP:
        kosdaq.append(r["company"])
UNIVERSE = (kospi + kosdaq)[:LIMIT]


def walk(nodes):
    for n in nodes:
        yield n
        yield from walk(n.get("children") or [])


async def audit(name):
    try:
        p = await build_shareholder_meeting_payload(name, scope="full")
        data = p.get("data", {})
        agendas = data.get("agendas") or []
        if not agendas:
            return {"name": name, "no_notice": True}
        cats = Counter(n.get("category") for n in walk(agendas) if n.get("category"))
        board = data.get("board", {}) or {}
        comp = data.get("compensation", {}) or {}
        aoi = data.get("aoi_change", {}) or {}
        prov = data.get("prov_financials", {}) or {}
        return {
            "name": name, "cats": cats,
            "board_ok": bool(board.get("appointments")) and any(a.get("candidates") for a in board.get("appointments", [])),
            "comp_ok": bool(comp.get("items")),
            "comp_status": comp.get("summary", {}).get("parse_status") or comp.get("parse_status"),
            "aoi_ok": bool(aoi.get("amendments")),
            "retire_ok": bool(aoi.get("retirement_amendments")),
            "prov_status": prov.get("metrics", {}).get("extraction_status") or prov.get("extraction_status"),
        }
    except Exception as e:
        return {"name": name, "err": repr(e)}


async def main():
    print(f"표본 {len(UNIVERSE)}사 (KOSPI {len([n for n in UNIVERSE if n in kospi])} + KOSDAQ {len([n for n in UNIVERSE if n in kosdaq])}), scope=full")
    res = []
    t0 = time.monotonic()
    for i in range(0, len(UNIVERSE), 16):
        res.extend(await asyncio.gather(*(audit(n) for n in UNIVERSE[i:i+16])))
        done = min(i + 16, len(UNIVERSE))
        if done % 64 == 0 or done == len(UNIVERSE):
            print(f"  ... {done}/{len(UNIVERSE)} ({time.monotonic()-t0:.0f}s)")

    err = [r for r in res if r.get("err")]
    no_notice = [r for r in res if r.get("no_notice")]
    ok = [r for r in res if "cats" in r]
    print(f"\n분석 {len(ok)}사 / 공고없음 {len(no_notice)} / 에러 {len(err)} {[r['name'] for r in err][:5]}")

    DETAIL_OF = {
        "director_election": "board_ok", "audit_committee_election": "board_ok",
        "director_compensation": "comp_ok", "audit_compensation": "comp_ok",
        "retirement_pay": "retire_ok", "articles_amendment": "aoi_ok",
    }
    print("\n=== 안건 유형별 silent-failure (트리엔 있는데 상세 빈) ===")
    summary_rows = {}
    for cat, key in DETAIL_OF.items():
        have = [r for r in ok if r["cats"].get(cat)]
        if not have:
            continue
        fail = [r for r in have if not r[key]]
        rate = len(fail) / len(have) * 100
        flag = "🔴" if rate >= 20 else ("🟡" if rate >= 5 else "✅")
        summary_rows[cat] = (len(have), len(fail), round(rate, 1))
        print(f"  {flag} {cat:26s} 보유 {len(have):3d}사 / 실패 {len(fail):3d}사 ({rate:.1f}%)  {[r['name'] for r in fail][:6]}")

    cs = Counter(r.get("comp_status") for r in ok)
    ps = Counter(r.get("prov_status") for r in ok)
    print(f"\n=== 보수한도 parse_status 분포 (summary 레벨) ===\n  {dict(cs)}")
    print(f"=== 잠정재무 extraction_status 분포 ===\n  {dict(ps)}")

    json.dump(
        {"universe": len(UNIVERSE), "analyzed": len(ok), "no_notice": len(no_notice),
         "errors": [r["name"] for r in err], "silent_failure": summary_rows,
         "comp_status": dict(cs), "prov_status": dict(ps)},
        open("wiki/architecture/audits/data/260615_agenda_typed_status_audit.json", "w", encoding="utf-8"),
        ensure_ascii=False, indent=2)
    print("\n저장: wiki/architecture/audits/data/260615_agenda_typed_status_audit.json")


asyncio.run(main())
