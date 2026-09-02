"""16 tool render smoke — MCPServer call_tool 경로로 build+render 전체 검증.

payload audit은 데이터만 봤다 — LLM이 실제로 받는 건 render된 markdown.
각 tool을 등록된 MCP 경로 그대로 호출해 (1) 예외 없음 (2) 비어있지 않음
(3) 에러 패턴('Traceback'/'❌') 부재를 확인한다.

페이싱: tool 사이 0.8s, heavy(proxy_advise)는 1사만.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

from mcp.server.mcpserver import MCPServer

from open_proxy_mcp.tools import register_all_tools

# tool → 호출 args 목록 (대표 2사 — 대형 + 중형/특수)
CASES: dict[str, list[dict]] = {
    "company": [{"query": "삼성전자"}, {"query": "솔루엠"}],
    "shareholder_meeting_notice": [{"company": "삼성전자"}, {"company": "솔루엠"}],
    "shareholder_meeting_results": [{"company": "삼성전자"}, {"company": "고려아연"}],
    "ownership_structure": [{"company": "고려아연"}, {"company": "셀트리온"}],
    "dividend_disclosure": [{"company": "삼성전자", "scope": "history"}, {"company": "KB금융"}],
    "financial_metrics": [{"company": "삼성전자"}, {"company": "솔루엠"}],
    "treasury_share": [{"company": "삼성전자"}, {"company": "미래에셋증권"}],
    "proxy_contest": [{"company": "고려아연"}, {"company": "솔루엠"}],
    "value_up": [{"company": "KB금융"}, {"company": "KT&G"}],
    "corporate_restructuring": [{"company": "두산에너빌리티"}, {"company": "삼성물산"}],
    "dilutive_issuance": [{"company": "카카오"}, {"company": "에코프로비엠"}],
    "corporate_deals": [{"company": "SK스퀘어"}, {"company": "삼성물산"}],
    "risk_events": [{"company": "한화"}, {}],  # 2번째 = 시장 스캔 모드
    "corp_gov_report": [{"company": "삼성전자"}, {"company": "KT&G"}],
    "evidence": [{"rcept_no": "20240220001962"}, {"evidence_id": "ev_block_20260520000110"}],
    "proxy_advise_before_meeting": [{"company": "솔루엠"}],  # heavy — 1사
}

BAD_MARKERS = ("Traceback", "Exception", "NoneType", "KeyError")


async def main() -> None:
    mcp = MCPServer("smoke")
    register_all_tools(mcp)
    tools = {t.name for t in await mcp.list_tools()}
    missing = set(CASES) - tools
    extra = tools - set(CASES)
    print(f"등록 tool {len(tools)}개 / 케이스 매핑 누락={missing or '없음'} / 미커버={extra or '없음'}")

    rows = []
    for name, arg_list in CASES.items():
        if name not in tools:
            continue
        for args in arg_list:
            label = f"{name}({json.dumps(args, ensure_ascii=False)[:40]})"
            t0 = time.perf_counter()
            try:
                result = await mcp.call_tool(name, args)
                # mcp 2.0: call_tool → CallToolResult(pydantic 모델).
                # **모델을 그대로 순회하면 (필드명, 값) 쌍이 나와** 내용이 비어도 글자수가
                # 잡히고 status 가 OK 로 뜬다 — 체크 셋 중 둘이 장식이 된다(260810 실측).
                # mcp 2.0: call_tool → CallToolResult(pydantic 모델).
                # **모델을 그대로 순회하면 (필드명, 값) 쌍이 나와** 내용이 비어도 글자수가
                # 잡히고 status 가 OK 로 뜬다 — 체크 셋 중 둘이 장식이 된다(260810 실측).
                # 도구 실패는 여기로 안 온다 — call_tool 이 ToolError 를 **던져서** 아래
                # except 의 EXC: 행으로 간다(tools/base.py). is_error 를 볼 자리가 없다.
                texts = [getattr(item, "text", str(item)) for item in result.content]
                out = "\n".join(texts)
                dt = (time.perf_counter() - t0) * 1000
                bad = [m for m in BAD_MARKERS if m in out]
                status = "OK" if out.strip() and not bad else f"BAD:{bad or 'empty'}"
                rows.append({"case": label, "ms": round(dt), "chars": len(out), "status": status})
                print(f"  {'✓' if status=='OK' else '⚠'} {label:60s} {dt:6.0f}ms {len(out):6d}자 {status if status!='OK' else ''}")
            except Exception as exc:  # noqa: BLE001
                rows.append({"case": label, "status": f"EXC:{type(exc).__name__}", "error": str(exc)[:100]})
                print(f"  ✗ {label:60s} EXC {type(exc).__name__}: {str(exc)[:60]}")
            await asyncio.sleep(0.8)

    bad_rows = [r for r in rows if r["status"] != "OK"]
    print(f"\n[render smoke] {len(rows)}케이스 — OK={len(rows)-len(bad_rows)} 문제={len(bad_rows)}")
    json.dump(rows, open("wiki/architecture/audits/data/260613_render_smoke.json", "w"), ensure_ascii=False, indent=1)
    sys.exit(1 if bad_rows else 0)


if __name__ == "__main__":
    asyncio.run(main())
