"""treasury_share 실행결과보고서 ACODE 단위(백만원/원) 불일치 전수 스캔 — read-only, DB 변경 없음.

배경(260706): 현대차 자기주식취득/처분결과보고서 실측에서, 원문 표가 "(단위: 백만원)"로
작성됐는데 _acode_int()가 그 배수를 감지 못해 actual_amount_krw가 약 100만분의 1로
축소되는 버그를 확인(services/treasury_share.py의 _extract_acode/_acode_int, 결정 단계의
구조화 API가 아니라 실행결과보고서 원문 HTML 파싱 경로에서만 발생). 이 스크립트는 그 버그가
KOSPI200 전체에 얼마나 퍼져있는지 기계적으로 스캔한다.

탐지 로직 (판단 없이 산수만, 260707 확장 — 백만원 하나만 보면 다른 단위 놓친다는 지적 반영):
  ① actual_amount_krw vs 매칭된 decision.amount_krw(또는 result 자체의 planned_amount_krw)
     비율이 한국 공시 표에 실제 쓰이는 단위배수(천원 1e3·만원 1e4·십만원 1e5·백만원 1e6·
     천만원 1e7·억원 1e8·십억원 1e9) 중 하나에 ±10% 이내로 가까우면 단위불일치 의심.
  ② agreement_status="일치"인데 위 비율이 크게 벗어나면 강한 신호(계획과 "일치"한다면서
     금액이 안 맞는 건 모순 — 어떤 배수든 상관없이 잡음).

DART 직접 호출(build_treasury_share_payload, MCP 미경유) — concurrency 2 + sleep 준수.
실행: python3 scripts/treasury_unit_sweep.py
"""
import asyncio, csv, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from open_proxy_mcp.services.treasury_share import build_treasury_share_payload

UNIVERSE_CSV = ROOT / "wiki/architecture/audits/data/260506_universe_kospi_200.csv"
CONCURRENCY = 2
SLEEP_BETWEEN = 0.6
LOOKBACK_MONTHS = 60

# 한국 공시 표에 실제로 쓰이는 단위 배수 전부 — 백만원(1e6)만 보면 다른 단위(천원·억원·십억원 등)를
# 놓친다(260707 사용자 지적). 로그거리 최소인 배수를 찾아 ±10% 이내면 그 단위로 판정.
_UNIT_MULTIPLIERS = {
    "천원": 1_000,
    "만원": 10_000,
    "십만원": 100_000,
    "백만원": 1_000_000,
    "천만원": 10_000_000,
    "억원": 100_000_000,
    "십억원": 1_000_000_000,
}
_TOLERANCE = 0.10  # ±10%


def _nearest_unit(ratio: float) -> tuple[str, int] | None:
    """ratio(=planned/actual)가 어느 단위배수에 가장 가까운지. ±10% 밖이면 None."""
    best = None
    for name, mult in _UNIT_MULTIPLIERS.items():
        dist = abs(ratio - mult) / mult
        if dist <= _TOLERANCE and (best is None or dist < best[2]):
            best = (name, mult, dist)
    return (best[0], best[1]) if best else None


def _load_universe() -> list[tuple[str, str]]:
    rows = []
    with open(UNIVERSE_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append((r["ticker"], r["company"]))
    return rows


def _decision_amount_for(events: list[dict], rcept_no: str) -> int | None:
    for e in events:
        if e.get("rcept_no") == rcept_no and e.get("phase") == "decision":
            return e.get("amount_krw")
    return None


def _scan_company(company: str, data: dict) -> list[dict]:
    flags = []
    events = data.get("events", [])
    for ev in events:
        if ev.get("phase") != "execution":
            continue
        actual = ev.get("actual_amount_krw")
        if not actual:
            continue
        planned = ev.get("planned_amount_krw")
        linked = ev.get("linked_decision_rcept_no")
        if not planned and linked:
            planned = _decision_amount_for(events, linked)
        if not planned:
            continue
        ratio = planned / actual if actual else None
        if ratio is None:
            continue
        unit_match = _nearest_unit(ratio)  # (단위명, 배수) 또는 None — 어떤 표준단위든 걸러냄
        agreement = ev.get("agreement_status")
        contradiction = agreement == "일치" and not (0.5 <= ratio <= 2.0)
        if unit_match or contradiction:
            flags.append({
                "company": company,
                "event": ev.get("event"),
                "rcept_no": ev.get("rcept_no"),
                "actual_amount_krw": actual,
                "planned_or_decision_amount_krw": planned,
                "ratio_planned_over_actual": round(ratio, 4),
                "agreement_status": agreement,
                "unit_match": unit_match[0] if unit_match else None,
                "contradiction": contradiction,
            })
    return flags


async def main() -> None:
    universe = _load_universe()
    print(f"대상 {len(universe)}사 (KOSPI200) · lookback {LOOKBACK_MONTHS}개월 · concurrency {CONCURRENCY}")

    sem = asyncio.Semaphore(CONCURRENCY)
    all_flags: list[dict] = []
    scanned = 0
    execution_event_count = 0
    errors: list[str] = []

    async def _one(ticker: str, name: str) -> None:
        nonlocal scanned, execution_event_count
        async with sem:
            try:
                payload = await build_treasury_share_payload(
                    name, scope="summary", lookback_months=LOOKBACK_MONTHS
                )
            except Exception as e:
                errors.append(f"{name}({ticker}): {type(e).__name__} {e}")
                await asyncio.sleep(SLEEP_BETWEEN)
                return
            data = payload.get("data") or {}
            events = data.get("events", [])
            execution_event_count += sum(1 for e in events if e.get("phase") == "execution")
            flags = _scan_company(name, data)
            all_flags.extend(flags)
            scanned += 1
            if scanned % 20 == 0:
                print(f"  {scanned}/{len(universe)} 스캔 · 누적 플래그 {len(all_flags)}건", flush=True)
            await asyncio.sleep(SLEEP_BETWEEN)

    t0 = time.time()
    await asyncio.gather(*(_one(t, n) for t, n in universe))
    print(f"\n완료: {scanned}사 스캔 ({time.time()-t0:.0f}s) · execution 이벤트 {execution_event_count}건"
          f" · 플래그 {len(all_flags)}건 · 오류 {len(errors)}건")

    if all_flags:
        print("\n=== 플래그 상세 ===")
        for f in all_flags:
            tag = []
            if f["unit_match"]:
                tag.append(f"단위의심:{f['unit_match']}")
            if f["contradiction"]:
                tag.append("일치모순")
            print(f"  [{'/'.join(tag)}] {f['company']} {f['event']} rcept={f['rcept_no']} "
                  f"actual={f['actual_amount_krw']:,} vs planned/decision={f['planned_or_decision_amount_krw']:,} "
                  f"(비율 {f['ratio_planned_over_actual']}) agreement={f['agreement_status']}")

    if errors:
        print(f"\n=== 오류 {len(errors)}건 (상위 10) ===")
        for e in errors[:10]:
            print(f"  {e}")


if __name__ == "__main__":
    asyncio.run(main())
