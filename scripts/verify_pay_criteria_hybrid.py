"""pay_criteria 하이브리드 교차검증 실효성 + async 속도 검증(260713).

목적 2가지(사용자 요청):
  A. 하이브리드(정형 API hmvAuditIndvdlBySttus ↔ 파서 Σ컴포넌트) 독립 교차검증이 **실제로 먹히는가** —
     특히 파서 자기일치(in-doc 표)만으론 못 잡던 silent case(이름+직위 병합 등)를 API가 적발하는지.
  B. 병렬 fetch(document ∥ individual_pay)가 wall-clock을 늘리지 않는지 — 같은 회사에서 캐시를
     무효화해 serial vs parallel을 apples-to-apples로 측정.

레이트세이프(CLAUDE.md): 동시성 1 · 회사간 sleep 2s · 소수 표본(≈7사)이라 분당 콜 수십 회 수준.
로컬 직접 import 경로(production MCP엔 이 변경분 미반영이므로 ①MCP가 아니라 ②직접 import가 정답).
"""
from __future__ import annotations
import os, sys, json, asyncio, time, tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\Projects\open-proxy-mcp")
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")  # OPENDART_API_KEY 등 (repo 표준)
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.dart.client import get_dart_client
from open_proxy_mcp.services.company import resolve_company_query
from open_proxy_mcp.services.director_board import build_director_board_payload, _fetch_rows

OUT = Path(os.environ.get("OPM_VPAY_OUT") or (Path(tempfile.gettempdir()) / "opm_vpay_validation"))
OUT.mkdir(parents=True, exist_ok=True)

# 대상: silent case(삼성생명) + clean baseline + KPI(POSCO) + 대형다수(삼성전자) + 소수점(풍산) +
# 익명화(올릭스) + 금융단위(DB손보). 파서 자기일치는 통과하나 API와 어긋나는 건이 핵심.
TARGETS = [
    ("삼성생명", "032830"), ("KT&G", "033780"), ("POSCO홀딩스", "005490"),
    ("삼성전자", "005930"), ("풍산", "103140"), ("올릭스", "226950"),
    ("DB손해보험", "005830"),
]
YEAR = 2024
REPRT = "11011"


def _won(v):
    if v is None:
        return "-"
    return f"{v/1e8:.1f}억"


async def part_a():
    print("=" * 70)
    print("PART A — 하이브리드 교차검증 실효성 (파서 자기일치 vs 정형 API 독립대조)")
    print("=" * 70)
    rows_out = []
    for name, _tk in TARGETS:
        try:
            env = await build_director_board_payload(name, scope="pay_criteria", year=YEAR)
        except Exception as e:  # noqa: BLE001
            print(f"\n[{name}] ERROR: {e}")
            continue
        d = env.get("data") or {}
        pc = d.get("pay_criteria") or {}
        timing = d.get("timing") or {}
        st = pc.get("status")
        rec = pc.get("reconciliation") or {}
        arec = pc.get("api_reconciliation") or {}
        print(f"\n── {name} (status={st}, pay_criteria {timing.get('pay_criteria')}ms) ──")
        if st != "parsed":
            print(f"   {pc.get('note')}")
            rows_out.append({"company": name, "status": st})
            continue
        print(f"   파서 자기일치(in-doc): {rec.get('consistent')}/{rec.get('checkable')} "
              f"({rec.get('consistent_rate')}%)")
        print(f"   하이브리드(API 독립): {arec.get('consistent')}/{arec.get('checkable')} "
              f"({arec.get('consistent_rate')}%)  API공개 {arec.get('api_disclosed')}명")
        # 자기일치는 통과인데 API는 불일치인 인물 = 하이브리드가 새로 잡은 silent case
        newly_caught = []
        for p in (pc.get("individuals") or []):
            self_ok = p.get("total_consistent")
            api_ok = p.get("api_consistent")
            if api_ok is False:
                tag = "NEW(자기일치는 통과)" if self_ok is True else "(자기일치도 실패)"
                newly_caught.append(f"{p.get('name')} {tag}: 파서 {_won(p.get('total_krw'))} vs API {_won(p.get('api_total_krw'))}")
        for c in newly_caught:
            print(f"   ❗ API불일치: {c}")
        # API엔 5억+로 있는데 파서 개인목록에 대응 없음 = 이름 병합/누락(삼성생명류)
        for u in (arec.get("api_unmatched") or []):
            print(f"   ❗ API 5억+ 미매칭: {u.get('name')}({_won(u.get('api_total_krw'))}) — 파서에 대응 개인 없음(병합/누락 의심)")
        # 파서 5억+인데 API에 없음(미등기·직원이면 정상)
        pu = arec.get("parser_unmatched_ge5") or []
        if pu:
            for p in pu:
                print(f"   ℹ️ 파서 5억+ 미매칭: {p.get('name')}[{(p.get('group') or '')[:10]}] {_won(p.get('total_krw'))} (미등기·직원이면 정상)")
        rows_out.append({
            "company": name, "status": st,
            "self_recon": rec, "api_recon": {k: v for k, v in arec.items() if k not in ("api_unmatched", "parser_unmatched_ge5")},
            "api_unmatched": arec.get("api_unmatched"),
            "newly_caught": newly_caught,
            "timing_ms": timing,
        })
        await asyncio.sleep(2.0)
    (OUT / "hybrid_partA.json").write_text(json.dumps(rows_out, ensure_ascii=False, indent=2), encoding="utf-8")
    return rows_out


async def _rcept_for(client, corp_code, year):
    for y in (year, year - 1):
        rows = await _fetch_rows(client.get_executive_status(corp_code, str(y), REPRT))
        rc = next((r.get("rcept_no") for r in rows if r.get("rcept_no")), None)
        if rc:
            return rc, y
    return None, year


def _invalidate(client, rcept_no):
    """document 캐시(메모리+디스크) 무효화 → 다음 fetch가 진짜 network cold."""
    client.invalidate_document(rcept_no)
    p = os.path.join(tempfile.gettempdir(), "opm_cache", f"{rcept_no}.json")
    try:
        os.remove(p)
    except FileNotFoundError:
        pass


async def part_b():
    print("\n" + "=" * 70)
    print("PART B — async 속도: 병렬(document ∥ individual_pay) vs 순차, 같은 회사 cold 대조")
    print("=" * 70)
    client = get_dart_client()
    bench = []
    # cold 대조 2사(캐시 무효화로 매 측정 network cold 보장) + warm 1사(디스크캐시 상태 그대로).
    for name, _tk in [("POSCO홀딩스", "005490"), ("삼성전자", "005930")]:
        res = await resolve_company_query(name)
        corp = res.selected["corp_code"]
        rcept, uy = await _rcept_for(client, corp, YEAR)
        if not rcept:
            print(f"[{name}] rcept 확보 실패 — skip")
            continue

        # SERIAL (cold): document 먼저 fetch, 이어서 API. 둘 다 순차 대기.
        _invalidate(client, rcept)
        t0 = time.perf_counter()
        await client.get_document_cached(rcept)
        t_doc = (time.perf_counter() - t0) * 1000
        t1 = time.perf_counter()
        await _fetch_rows(client.get_individual_pay(corp, str(uy), REPRT))
        t_api = (time.perf_counter() - t1) * 1000
        t_serial = (time.perf_counter() - t0) * 1000

        await asyncio.sleep(2.0)

        # PARALLEL (cold): 같은 회사, 캐시 다시 무효화 후 gather.
        _invalidate(client, rcept)
        t2 = time.perf_counter()
        await asyncio.gather(
            client.get_document_cached(rcept),
            _fetch_rows(client.get_individual_pay(corp, str(uy), REPRT)),
        )
        t_parallel = (time.perf_counter() - t2) * 1000

        saved = t_serial - t_parallel
        print(f"\n── {name} (cold, rcept {rcept}) ──")
        print(f"   순차:   document {t_doc:.0f}ms + API {t_api:.0f}ms = {t_serial:.0f}ms")
        print(f"   병렬:   gather(document ∥ API)          = {t_parallel:.0f}ms")
        print(f"   절감:   {saved:.0f}ms  ({saved/t_serial*100:.0f}%)  ← API가 document 다운로드 그늘에 흡수")
        bench.append({"company": name, "t_doc_ms": round(t_doc), "t_api_ms": round(t_api),
                      "t_serial_ms": round(t_serial), "t_parallel_ms": round(t_parallel),
                      "saved_ms": round(saved)})
        await asyncio.sleep(2.0)
    (OUT / "hybrid_partB.json").write_text(json.dumps(bench, ensure_ascii=False, indent=2), encoding="utf-8")
    return bench


async def main():
    a = await part_a()
    b = await part_b()
    print("\n" + "=" * 70)
    print("요약")
    print("=" * 70)
    caught = [r for r in a if r.get("newly_caught") or r.get("api_unmatched")]
    print(f"하이브리드가 API로 신규 적발/미매칭 신호 낸 회사: {len(caught)}/{len([x for x in a if x.get('status')=='parsed'])} parsed")
    for r in caught:
        print(f"  - {r['company']}: newly={len(r.get('newly_caught') or [])}, api_unmatched={len(r.get('api_unmatched') or [])}")
    if b:
        avg_saved = sum(x["saved_ms"] for x in b) / len(b)
        print(f"async 평균 절감: {avg_saved:.0f}ms/회사 (병렬화로 API 호출이 wall-clock에 무영향)")


if __name__ == "__main__":
    asyncio.run(main())
