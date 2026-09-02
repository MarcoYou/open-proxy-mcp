"""전 tool render smoke — MCPServer call_tool 경로로 build+render 전체 검증 + 산출물 이상 패턴 스캔.

payload audit은 데이터만 봤다 — LLM이 실제로 받는 건 render된 markdown.
런타임 카탈로그(build_mcp)의 **모든 tool**을 등록된 MCP 경로 그대로 호출해
  (1) 예외 없음 (2) 비어있지 않음 (3) 에러 패턴('Traceback' 등) 부재
를 확인하고, 렌더 텍스트를 post-pass 로 훑어 이상 패턴을 센다(260902 — 구 render 이상 스캔 스크립트 흡수):
  None 노출 · 0억(단위 미환산) · '- 원' · 인코딩 깨짐(�) · nan/undefined/null · 카테고리 None/other ·
  빈 셀 연속 · Python dict raw 노출 · 레이블 없는 값(bullet).

케이스: CASES 에 명시된 tool 은 그 인자로, 나머지는 입력 스키마의 required 만 보고 기본 회사(삼성전자)로
자동 생성 — 새 tool 이 생겨도 최소 1회는 호출된다. 자동 생성이 불가능한 required 인자가 있으면
호출하지 않고 「케이스 필요」로 알린다.

출력: --out (기본 ./render_smoke.json) 에 [{tool, company, text, args, ms, chars, status, anomalies}] —
scripts/diff_tool_output.py(before/after 대조)·scripts/scan_tool_output.py(내부식별자 누출)의 입력 형식.
종료코드: 1 = 예외·빈 응답·에러 마커 케이스 존재. 이상 패턴은 **보고만** 한다(휴리스틱이라 오탐 있음).

페이싱: tool 사이 0.8s, heavy(proxy_advise)는 1사만. DART 키 필요.
실행: uv run python scripts/render_smoke_all_tools.py [--out render_smoke.json] [--only dividend,value_up]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

DEFAULT_COMPANY = "삼성전자"

# tool → 호출 args 목록 (대표 2사 — 대형 + 중형/특수). 여기 없는 tool 은 _auto_case 가 스키마로 만든다.
CASES: dict[str, list[dict]] = {
    "company": [{"query": "삼성전자"}, {"query": "솔루엠"}],
    "shareholder_meeting_notice": [{"company": "삼성전자"}, {"company": "솔루엠"}],
    "shareholder_meeting_results": [{"company": "삼성전자"}, {"company": "고려아연"}],
    "ownership_structure": [{"company": "고려아연"}, {"company": "셀트리온"}],
    "dividend": [{"company": "삼성전자", "scope": "history"}, {"company": "KB금융"}],
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
    # 기본 회사로는 뜻이 없거나 required 가 company 가 아닌 tool — 명시 케이스.
    "director_news": [{"name": "이재용", "company": "삼성전자"}],
    "law_lookup": [{"query": "감사위원 분리선출"}],
    "order_contracts": [{"company": "HD현대중공업"}],   # 삼성전자는 단일판매·공급계약 공시가 드물다
    "proxy_guideline": [{}],
    "screener": [{}],
    "dividend_screener": [{}],
}

BAD_MARKERS = ("Traceback", "Exception", "NoneType", "KeyError")

# ── 렌더 텍스트 이상 패턴 (구 render 이상 스캔 스크립트에서 이관) ─────────────────────────────
ANOMALY = [
    (re.compile(r"(?<![A-Za-z'])None(?![A-Za-z'])"), "None 노출"),
    (re.compile(r"(?<![\d.])0억(?!\d)"), "0억(단위미환산?)"),       # 소수점(.0억) 제외
    (re.compile(r"[:|]\s*-\s*원"), "-원"),
    (re.compile(r"�"), "인코딩깨짐"),
    (re.compile(r"\b(nan|NaN|undefined|null)\b"), "nan/undefined/null"),
    (re.compile(r"카테고리[:：]\s*(None|other)\b"), "카테고리 None/other"),
    (re.compile(r"\|\s*\|\s*\|\s*\|"), "빈 셀 4연속"),
    (re.compile(r"\{['\"]"), "Python dict/obj raw 노출"),          # {'key': ...} 객체 노출
    # 레이블 없는 값 — bullet 다음에 한글 레이블 없이 숫자/금액/%만 (무슨 값인지 불명).
    # '- 450억원' / '- 12.3%' / '- 5건' (vs '- 보수한도: 450억원'). 날짜·코드·범위는 _SKIP_LINE/예외.
    (re.compile(r"^\s*[-*]\s+\*{0,2}-?[\d,]+(?:\.\d+)?\s*\*{0,2}\s*(?:억원?|조원?|백만원?|천원|원|%|건|주|명|배)\b"),
     "레이블 없는 값(bullet)"),
]
# 정상이라 제외할 라인 (접수번호 14자리·DART 뷰어 URL은 큰 숫자가 정상)
_SKIP_LINE = re.compile(r"rcept_no|rcpNo=|dart\.fss|company_id|corp_code|`\d{14}`")


def scan_anomalies(md: str) -> list[dict]:
    """렌더 텍스트 한 건의 이상 패턴 → [{anomaly, line}]. 라인 단위, _SKIP_LINE 은 건너뛴다."""
    hits = []
    for ln in md.splitlines():
        if _SKIP_LINE.search(ln):
            continue
        for pat, label in ANOMALY:
            if pat.search(ln):
                hits.append({"anomaly": label, "line": ln.strip()[:90]})
    return hits


def _auto_case(tool) -> dict | None:
    """CASES 에 없는 tool 의 기본 케이스 — required 가 company/query 뿐이면 기본 회사, 그 외 required 는 못 만든다."""
    schema = tool.input_schema or {}
    props = schema.get("properties", {})
    args: dict = {}
    for key in schema.get("required", []):
        if key in ("company", "query"):
            args[key] = DEFAULT_COMPANY
        else:
            return None
    if not args and "company" in props:
        args["company"] = DEFAULT_COMPANY
    return args


def _company_label(args: dict) -> str:
    """diff_tool_output 의 (tool, company) 키 — 회사가 없으면 인자 자체로 구분(evidence 두 케이스 등)."""
    return args.get("company") or args.get("query") or args.get("name") or (
        json.dumps(args, ensure_ascii=False, sort_keys=True) if args else "-")


async def main(out: Path, only: set[str] | None) -> int:
    from open_proxy_mcp.server import build_mcp   # 무거운 import 는 --help 뒤로

    mcp = build_mcp()
    tools = {t.name: t for t in await mcp.list_tools()}
    stale = sorted(set(CASES) - set(tools))
    cases: dict[str, list[dict]] = {}
    need_case: list[str] = []
    for name, tool in sorted(tools.items()):
        if name in CASES:
            cases[name] = CASES[name]
            continue
        auto = _auto_case(tool)
        if auto is None:
            need_case.append(name)
        else:
            cases[name] = [auto]
    print(f"등록 tool {len(tools)}개 / 명시 케이스 {len([n for n in cases if n in CASES])} / "
          f"자동 케이스 {len([n for n in cases if n not in CASES])} / 케이스 필요={need_case or '없음'} / "
          f"CASES 에만 있는 옛 tool={stale or '없음'}")

    rows: list[dict] = []
    for name, arg_list in cases.items():
        if only and name not in only:
            continue
        for args in arg_list:
            label = f"{name}({json.dumps(args, ensure_ascii=False)[:40]})"
            company = _company_label(args)
            t0 = time.perf_counter()
            try:
                result = await mcp.call_tool(name, args)
                # mcp 2.0: call_tool → CallToolResult(pydantic 모델).
                # **모델을 그대로 순회하면 (필드명, 값) 쌍이 나와** 내용이 비어도 글자수가
                # 잡히고 status 가 OK 로 뜬다 — 체크 셋 중 둘이 장식이 된다(260810 실측).
                # 도구 실패는 여기로 안 온다 — call_tool 이 ToolError 를 **던져서** 아래
                # except 의 EXC: 행으로 간다(tools/base.py). is_error 를 볼 자리가 없다.
                texts = [getattr(item, "text", str(item)) for item in result.content]
                text = "\n".join(texts)
                dt = (time.perf_counter() - t0) * 1000
                bad = [m for m in BAD_MARKERS if m in text]
                status = "OK" if text.strip() and not bad else f"BAD:{bad or 'empty'}"
                anomalies = scan_anomalies(text)
                rows.append({"tool": name, "company": company, "text": text, "args": args,
                             "ms": round(dt), "chars": len(text), "status": status, "anomalies": anomalies})
                mark = "✓" if status == "OK" else "⚠"
                extra = (status if status != "OK" else "") + (f" 이상 {len(anomalies)}건" if anomalies else "")
                print(f"  {mark} {label:60s} {dt:6.0f}ms {len(text):6d}자 {extra}")
            except Exception as exc:  # noqa: BLE001
                # diff/scan 스크립트가 크래시로 인식하는 마커([JSONRPC-ERROR])를 text 에 남긴다.
                rows.append({"tool": name, "company": company, "args": args,
                             "text": f"[JSONRPC-ERROR] {type(exc).__name__}: {str(exc)[:200]}",
                             "status": f"EXC:{type(exc).__name__}", "anomalies": []})
                print(f"  ✗ {label:60s} EXC {type(exc).__name__}: {str(exc)[:60]}")
            await asyncio.sleep(0.8)

    bad_rows = [r for r in rows if r["status"] != "OK"]
    by_anom = Counter(a["anomaly"] for r in rows for a in r["anomalies"])
    by_tool = Counter(r["tool"] for r in rows for _ in r["anomalies"])
    print(f"\n[render smoke] {len(rows)}케이스 — OK={len(rows) - len(bad_rows)} 문제={len(bad_rows)}")
    if by_anom:
        print(f"[이상 패턴] {sum(by_anom.values())}건 — 유형별 {dict(by_anom.most_common())}")
        print(f"  tool별 {dict(by_tool.most_common())}")
        seen: Counter = Counter()
        for r in rows:
            for a in r["anomalies"]:
                if seen[a["anomaly"]] < 3:   # 유형별 샘플 3건
                    seen[a["anomaly"]] += 1
                    print(f"    [{a['anomaly']}] {r['tool']}/{r['company']}: {a['line']}")
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {out} ({len(rows)}건; diff_tool_output.py / scan_tool_output.py 입력)")
    return 1 if bad_rows else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="전 tool render smoke + 이상 패턴 스캔 (DART 키 필요)")
    ap.add_argument("--out", type=Path, default=Path("render_smoke.json"),
                    help="결과 JSON 경로 (기본 ./render_smoke.json)")
    ap.add_argument("--only", type=str, default=None, help="콤마구분 tool 이름 — 이것만 호출")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.out, set(a.only.split(",")) if a.only else None)))
