"""실제 tool 출력(render markdown) 이상 스캔 — 사용자가 보는 화면 기준 점검.

서비스 데이터 구조가 아니라 tool wrapper의 render 결과(md)에서 '이상하게 뜨는' 패턴을 잡는다:
  - None / null 노출
  - 0억 (단위 미환산 잔재)
  - '- 원' / 빈 셀 연속
  - 인코딩 깨짐(�)
  - nan / undefined
  - 카테고리 None/other
실제 호출 경로: build_*_payload → render_*(payload) (format=md).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import Counter
from pathlib import Path

import httpx

from open_proxy_mcp.dart.client import get_dart_client
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload as sm
from open_proxy_mcp.services.proxy_advise import build_proxy_advise_payload as pa
from open_proxy_mcp.tools_v2._shareholder_meeting_render import (
    render_summary, render_board, render_compensation, render_aoi,
)
from open_proxy_mcp.tools_v2.proxy_advise_before_meeting import _render as pa_render
# 확장: 다른 tool render 경로 (build_*_payload + _render(payload, scope))
from open_proxy_mcp.services.ownership_structure import build_ownership_structure_payload as b_own
from open_proxy_mcp.tools_v2.ownership_structure import _render as r_own
from open_proxy_mcp.services.corporate_deals import build_corporate_deals_payload as b_cd
from open_proxy_mcp.tools_v2.corporate_deals import _render as r_cd
from open_proxy_mcp.services.dividend_v2 import build_dividend_payload as b_div
from open_proxy_mcp.tools_v2.dividend import _render as r_div
from open_proxy_mcp.services.treasury_share import build_treasury_share_payload as b_tre
from open_proxy_mcp.tools_v2.treasury_share import _render as r_tre
from open_proxy_mcp.services.proxy_contest import build_proxy_contest_payload as b_pc
from open_proxy_mcp.tools_v2.proxy_contest import _render as r_pc
from open_proxy_mcp.services.value_up_v2 import build_value_up_payload as b_vu
from open_proxy_mcp.tools_v2.value_up import _render as r_vu
from open_proxy_mcp.services.corp_gov_report import build_corp_gov_report_payload as b_cg
from open_proxy_mcp.tools_v2.corp_gov_report import _render as r_cg
from open_proxy_mcp.services.risk_events import build_risk_events_payload as b_re
from open_proxy_mcp.tools_v2.risk_events import _render as r_re
from open_proxy_mcp.services.order_contracts import build_order_contracts_payload as b_oc
from open_proxy_mcp.tools_v2.order_contracts import _render as r_oc

# (label, build_coro(q)->payload, render(payload)->md)
JOBS = [
    ("ownership.summary", lambda q: b_own(q, scope="summary"), lambda p: r_own(p, "summary")),
    ("corporate_deals.summary", lambda q: b_cd(q, scope="summary"), lambda p: r_cd(p, "summary")),
    ("dividend.summary", lambda q: b_div(q, scope="summary"), lambda p: r_div(p, "summary")),
    ("treasury.summary", lambda q: b_tre(q, scope="summary"), lambda p: r_tre(p, "summary")),
    ("proxy_contest.summary", lambda q: b_pc(q, scope="summary"), lambda p: r_pc(p, "summary")),
    ("value_up.summary", lambda q: b_vu(q, scope="summary"), lambda p: r_vu(p, "summary")),
    ("corp_gov.summary", lambda q: b_cg(q, scope="summary"), lambda p: r_cg(p, "summary")),
    ("risk_events", lambda q: b_re(q), lambda p: r_re(p)),
    ("order_contracts", lambda q: b_oc(q), lambda p: r_oc(p)),
]

UNIVERSE_FILE = os.environ.get("UNIVERSE_FILE", "/tmp/kospi_kosdaq_300.json")
LIMIT = int(os.environ.get("LIMIT", "40"))
PA_LIMIT = int(os.environ.get("PA_LIMIT", "15"))
JOBS_LIMIT = int(os.environ.get("JOBS_LIMIT", "60"))
OUT = Path(os.environ.get("AUDIT_OUT", "wiki/architecture/audits/data/260615_render_anomaly_scan.json"))

ANOMALY = [
    (re.compile(r"(?<![A-Za-z'])None(?![A-Za-z'])"), "None 노출"),
    (re.compile(r"(?<![\d.])0억(?!\d)"), "0억(단위미환산?)"),       # 소수점(.0억) 제외
    (re.compile(r"[:|]\s*-\s*원"), "-원"),
    (re.compile(r"�"), "인코딩깨짐"),
    (re.compile(r"\b(nan|NaN|undefined|null)\b"), "nan/undefined/null"),
    (re.compile(r"카테고리[:：]\s*(None|other)\b"), "카테고리 None/other"),
    (re.compile(r"\|\s*\|\s*\|\s*\|"), "빈 셀 4연속"),
    (re.compile(r"\{['\"]"), "Python dict/obj raw 노출"),          # {'key': ...} 객체 노출
]

# 정상이라 제외할 라인 (접수번호 14자리·DART 뷰어 URL은 큰 숫자가 정상)
_SKIP_LINE = re.compile(r"rcept_no|rcpNo=|dart\.fss|company_id|corp_code|`\d{14}`")


def _scan(md: str, company: str, view: str) -> list[dict]:
    hits = []
    for ln in md.splitlines():
        if _SKIP_LINE.search(ln):
            continue
        for pat, label in ANOMALY:
            if pat.search(ln):
                hits.append({"company": company, "view": view, "anomaly": label, "line": ln.strip()[:90]})
    return hits


async def main() -> None:
    universe = json.loads(Path(UNIVERSE_FILE).read_text())
    client = get_dart_client()
    calls0 = client.api_call_snapshot()
    t0 = time.time()
    all_hits: list[dict] = []
    print(f"[render 이상 스캔] sm {LIMIT}사 + proxy_advise {PA_LIMIT}사")

    for i, q in enumerate(universe[:LIMIT]):
        try:
            for scope, render in (("summary", render_summary), ("board", render_board),
                                  ("compensation", render_compensation), ("aoi_change", render_aoi)):
                p = await sm(q, scope=scope, year=2026, meeting_type="annual")
                if p.get("status") in ("error", "ambiguous"):
                    continue
                md = render(p)
                all_hits.extend(_scan(md, q, f"sm.{scope}"))
        except httpx.ReadError as exc:
            print(f"  [ABORT] {q}: {exc}")
            break
        except Exception as exc:  # noqa: BLE001
            all_hits.append({"company": q, "view": "sm", "anomaly": "EXC", "line": f"{type(exc).__name__}: {str(exc)[:60]}"})
        await asyncio.sleep(0.4)
        if (i + 1) % 20 == 0:
            print(f"  sm {i+1}/{LIMIT} 누적콜={client.api_call_snapshot()-calls0} {(time.time()-t0)/60:.1f}분")

    for i, q in enumerate(universe[:PA_LIMIT]):
        try:
            p = await pa(q)
            if p.get("status") not in ("error", "ambiguous"):
                all_hits.extend(_scan(pa_render(p), q, "proxy_advise"))
        except httpx.ReadError as exc:
            print(f"  [ABORT pa] {q}: {exc}")
            break
        except Exception as exc:  # noqa: BLE001
            all_hits.append({"company": q, "view": "proxy_advise", "anomaly": "EXC", "line": f"{type(exc).__name__}: {str(exc)[:60]}"})
        await asyncio.sleep(0.5)

    # 확장 — 9개 tool render 스캔
    for i, q in enumerate(universe[:JOBS_LIMIT]):
        for label, build_fn, render_fn in JOBS:
            try:
                p = await build_fn(q)
                if isinstance(p, dict) and p.get("status") not in ("error", "ambiguous"):
                    all_hits.extend(_scan(render_fn(p), q, label))
            except httpx.ReadError as exc:
                print(f"  [ABORT {label}] {q}: {exc}")
                return
            except Exception as exc:  # noqa: BLE001
                all_hits.append({"company": q, "view": label, "anomaly": "EXC", "line": f"{type(exc).__name__}: {str(exc)[:60]}"})
            await asyncio.sleep(0.3)
        if (i + 1) % 20 == 0:
            print(f"  jobs {i+1}/{JOBS_LIMIT} 누적콜={client.api_call_snapshot()-calls0} {(time.time()-t0)/60:.1f}분")

    by_anom = Counter(h["anomaly"] for h in all_hits)
    by_view = Counter(h["view"] for h in all_hits)
    result = {
        "meta": {"date": "2026-06-15", "sm_companies": min(LIMIT, len(universe)),
                 "pa_companies": min(PA_LIMIT, len(universe)),
                 "total_dart_calls": client.api_call_snapshot() - calls0,
                 "elapsed_min": round((time.time() - t0) / 60, 1)},
        "anomaly_counts": dict(by_anom.most_common()),
        "view_counts": dict(by_view.most_common()),
        "hits": all_hits,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n[완료] 이상 {len(all_hits)}건")
    print(f"  유형별: {dict(by_anom.most_common())}")
    print(f"  view별: {dict(by_view.most_common())}")
    print("\n  샘플(유형별 3건):")
    seen: Counter = Counter()
    for h in all_hits:
        if seen[h["anomaly"]] < 3:
            seen[h["anomaly"]] += 1
            print(f"    [{h['anomaly']}] {h['company']}/{h['view']}: {h['line']}")
    print(f"\n  총 DART콜 {result['meta']['total_dart_calls']}, {result['meta']['elapsed_min']}분 → {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
