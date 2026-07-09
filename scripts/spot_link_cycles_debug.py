"""_link_cycles 오탐 — 금액 파싱오류 ground truth 확인 (read-only).

발견: 오탐은 매칭(날짜)이 아니라 금액 파싱 문제. 카카오 결정 49,850원·포스코퓨처엠 실행 3.17천조.
두 오류 filing의 원시 필드(shares·amounts·report_name·agreement)를 덤프해 파싱 메커니즘 규명.
"""
from __future__ import annotations
import asyncio, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from open_proxy_mcp.services.treasury_share import build_treasury_share_payload  # noqa: E402

# (회사, 확인할 rcept_no들)
CASES = {
    "카카오": ["20221021000447", "20221013000468"],       # 실행 / 결정(49,850원)
    "포스코퓨처엠": ["20220825000198", "20220823000079"],  # 실행(3.17천조) / 결정
    "엘앤에프": ["20220602000155", "20220524000392"],       # 실행 / 결정(부분집행?)
}
_DUMP = ["event", "phase", "rcept_no", "rcept_dt", "report_name", "agreement_status",
         "planned_shares", "actual_shares", "actual_amount_krw", "amount_krw",
         "amount_common_krw", "amount_preferred_krw", "price_common_krw",
         "shortfall_reason", "counterparty", "body_parsed"]


async def go():
    for name, rcepts in CASES.items():
        print(f"\n{'='*72}\n{name}")
        p = await build_treasury_share_payload(name, scope="summary", lookback_months=60)
        evs = (p.get("data") or {}).get("events") or []
        for rc in rcepts:
            e = next((x for x in evs if x.get("rcept_no") == rc), None)
            if not e:
                print(f"  {rc}: (events에 없음)")
                continue
            print(f"  --- {rc} ---")
            for k in _DUMP:
                if k in e and e.get(k) not in (None, "", [], {}):
                    print(f"      {k} = {e.get(k)!r}")


asyncio.run(go())
