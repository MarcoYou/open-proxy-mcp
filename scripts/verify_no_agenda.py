#!/usr/bin/env python3
"""pay_agenda='no_agenda' 21개사 재검증 — 실제로 보수한도 안건 텍스트가 있는데 파서가 놓친 건지
(false no_agenda) 진짜 이번 주총에 보수한도 안건이 없는 건지(true no_agenda) 원문 텍스트로 확인.

동시성 1~2 + sleep, ReadError 즉시 중단.
"""
import asyncio, sys, re
sys.path.insert(0, ".")
from open_proxy_mcp.dart.client import get_dart_client
from open_proxy_mcp.services.company import resolve_company_query

COMPANIES = [
    "기아","한국전력","효성중공업","HMM","한전기술","에코프로머티","강원랜드","롯데케미칼",
    "한국가스공사","한솔케미칼","현대지에프홀딩스","달바글로벌","에코프로","삼천당제약",
    "레인보우로보틱스","펄어비스","우리기술","HPSP","주성엔지니어링","인텔리안테크","에이프릴바이오",
]

AMOUNT_RE = re.compile(r"(\d[\d,]*)\s*(백만원|억원|원)")


async def check_one(client, name: str) -> dict:
    r = await resolve_company_query(name)
    if not r.selected:
        return {"company": name, "verdict": "resolve_failed"}
    cc = r.selected["corp_code"]
    res = await client.search_filings("20250101", "20260708", pblntf_detail_ty="E006",
                                       corp_code=cc, last_reprt_at="Y")
    items = res.get("list") or []
    if not items:
        return {"company": name, "verdict": "no_notice_filing"}
    rcept_no = items[0]["rcept_no"]
    report_nm = items[0].get("report_nm", "")
    doc = await client.get_document_cached(rcept_no)
    text = doc.get("text") or ""

    idx = text.find("보수한도")
    if idx < 0:
        # 다른 표현도 확인
        idx = text.find("보수 한도")
    has_keyword = idx >= 0
    nearby = text[max(0, idx-50):idx+250] if has_keyword else ""
    has_amount = bool(AMOUNT_RE.search(nearby)) if has_keyword else False
    is_correction = "정정" in report_nm

    if has_keyword and has_amount:
        verdict = "FALSE_NO_AGENDA (텍스트에 금액 있음 — 파서 누락 의심)"
    elif has_keyword:
        verdict = "AMBIGUOUS (보수한도 언급은 있으나 금액 패턴 미검출)"
    else:
        verdict = "TRUE_NO_AGENDA (보수한도 언급 자체 없음)"

    return {
        "company": name, "rcept_no": rcept_no, "report_nm": report_nm,
        "is_correction_filing": is_correction, "text_len": len(text),
        "has_keyword": has_keyword, "nearby": nearby.replace("\n", " ")[:200],
        "verdict": verdict,
    }


async def main():
    client = get_dart_client()
    results = []
    for name in COMPANIES:
        try:
            r = await check_one(client, name)
        except Exception as e:
            r = {"company": name, "verdict": f"error: {e}"}
        results.append(r)
        print(f"{r['company']}: {r['verdict']}" + (f" [정정신고={r.get('is_correction_filing')}]" if 'is_correction_filing' in r else ""))
        await asyncio.sleep(0.5)

    false_cases = [r for r in results if r.get("verdict","").startswith("FALSE")]
    print(f"\n=== 요약: {len(results)}개사 중 FALSE_NO_AGENDA(데이터 손실 의심) {len(false_cases)}건 ===")
    for r in false_cases:
        print(f"  {r['company']}: {r['nearby']}")


if __name__ == "__main__":
    asyncio.run(main())
