"""pay_criteria 파서 유니버스 검증 — 레이트세이프 캐시 수집(260713).

director_board scope=pay_criteria(사업보고서 VIII-2 원문 파서)를 KOSPI200+KOSDAQ100+엣지10에
전체경로로 실행해, 파서 출력 + 원문 VIII-2 구간을 JSONL로 저장. 이후 DART-0콜 멀티에이전트가
원문 vs 파서출력을 적대적 대조하는 데 쓴다.

레이트리밋 하드룰(CLAUDE.md): 동시성 2 · 배치사이 sleep · ReadError 즉시중단 · 재개가능(done-set skip).
client 내부 throttle(cap 910/min)이 1차 방어, 이 스크립트가 2차.
"""
from __future__ import annotations
import os, sys, csv, json, asyncio, time, traceback, tempfile
from pathlib import Path

ROOT = Path(r"D:\Projects\open-proxy-mcp")
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")  # OPENDART_API_KEY 등 (repo 표준)
sys.path.insert(0, str(ROOT))

import httpx
from open_proxy_mcp.dart.client import get_dart_client
from open_proxy_mcp.services.director_board import build_director_board_payload
from open_proxy_mcp.services.executive_pay import _slice_section

DATA = ROOT / "wiki/architecture/audits/data"
OUT = Path(os.environ.get("OPM_VPAY_OUT") or (Path(tempfile.gettempdir()) / "opm_vpay_validation"))
OUT.mkdir(parents=True, exist_ok=True)
JSONL = OUT / "results.jsonl"

EDGE = [  # 비중복 엣지 10종(유니버스 밖·구조 특이): 인프라펀드/리츠=개인보수 없음, 외국계=단위/통화,
          # 우선주·순수지주=서식 변형. 금융지주(KB·신한 등)는 이미 KOSPI200 멤버라 자동 커버.
    ("088980", "맥쿼리인프라"), ("330590", "롯데리츠"), ("348950", "제이알글로벌리츠"),
    ("293940", "신한알파리츠"), ("357120", "코람코라이프인프라리츠"), ("365550", "ESR켄달스퀘어리츠"),
    ("950140", "잉글우드랩"), ("900140", "엘브이엠씨홀딩스"), ("000157", "두산2우B"),
    ("084690", "대상홀딩스"),
]

def load_universe():
    rows = []
    for f, src in (("260506_universe_kospi_200.csv", "kospi200"),
                   ("260506_universe_kosdaq_100.csv", "kosdaq100")):
        for r in csv.DictReader(open(DATA / f, encoding="utf-8")):
            rows.append((r["ticker"].strip(), r["company"].strip(), src))
    seen = {t for t, _, _ in rows}
    for t, n in EDGE:
        if t not in seen:
            rows.append((t, n, "edge"))
            seen.add(t)
    return rows

def done_set():
    if not JSONL.exists():
        return set()
    s = set()
    for line in open(JSONL, encoding="utf-8"):
        try:
            s.add(json.loads(line)["ticker"])
        except Exception:
            pass
    return s

async def process(ticker, name, src, client, abort):
    if abort["stop"]:
        return None
    t0 = time.perf_counter()
    try:
        env = await build_director_board_payload(ticker, scope="pay_criteria", format="json")
        pc = (env.get("data") or {}).get("pay_criteria") or {}
        status = pc.get("status")
        rcept_no = pc.get("rcept_no")
        raw_section = ""
        if rcept_no:
            # 원문 VIII-2 구간(디스크 캐시라 0 DART) — 에이전트 적대적 대조용
            try:
                doc = await client.get_document_cached(rcept_no)
                raw_section = _slice_section((doc or {}).get("text") or "")[:12000]
            except Exception:
                pass
        indivs = pc.get("individuals") or []
        rec = {
            "ticker": ticker, "company": name, "source": src,
            "status": status, "rcept_no": rcept_no,
            "resolved_name": (env.get("data") or {}).get("canonical_name"),
            "policy_rows": len(pc.get("pay_policy") or []),
            "policy_narrative": pc.get("policy_narrative"),
            "n_individuals": len(indivs),
            "n_amount_components": sum(1 for p in indivs for c in p.get("components", []) if c.get("amount_krw") is not None),
            "unknown_tables": pc.get("unknown_tables"),
            "aggregate_seen": pc.get("aggregate_seen"),
            "reconciliation": pc.get("reconciliation"),   # Σ컴포넌트 vs 공식총액 자기일치(무인 검증)
            "individuals": indivs,          # 전체(에이전트 검증용)
            "individual_totals": pc.get("individual_totals"),
            "pay_policy": pc.get("pay_policy"),
            "raw_section": raw_section,     # 원문(대조 ground truth)
            "warnings": env.get("warnings"),
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
        return rec
    except (httpx.ReadError, httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadTimeout) as e:
        abort["stop"] = True   # 하드룰: ReadError류는 즉시 중단(IP차단 신호일 수 있음)
        return {"ticker": ticker, "company": name, "source": src, "status": "NETWORK_ABORT",
                "error": f"{type(e).__name__}: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"ticker": ticker, "company": name, "source": src, "status": "ERROR",
                "error": f"{type(e).__name__}: {e}", "tb": traceback.format_exc()[-500:]}

async def main():
    universe = load_universe()
    done = done_set()
    todo = [u for u in universe if u[0] not in done]
    print(f"universe={len(universe)} done={len(done)} todo={len(todo)}", flush=True)
    client = get_dart_client()
    abort = {"stop": False}
    sem = asyncio.Semaphore(2)
    fh = open(JSONL, "a", encoding="utf-8")
    n = 0
    BATCH = 30
    for i in range(0, len(todo), BATCH):
        if abort["stop"]:
            print("ABORT — network error, stopping batch launch", flush=True)
            break
        batch = todo[i:i + BATCH]
        async def one(u):
            async with sem:
                return await process(u[0], u[1], u[2], client, abort)
        results = await asyncio.gather(*[one(u) for u in batch])
        for rec in results:
            if rec is None:
                continue
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
        fh.flush()
        parsed = sum(1 for r in results if r and r.get("status") == "parsed")
        print(f"batch {i//BATCH+1}: +{len(batch)} (parsed={parsed}) total_written={n}", flush=True)
        if not abort["stop"]:
            await asyncio.sleep(2)  # 배치사이 sleep(2차 방어)
    fh.close()
    print(f"DONE written={n} aborted={abort['stop']}", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
