"""Pipeline JSON 재생성 — 파서 결과로 keyData 업데이트 (기존 구조 유지)"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from open_proxy_mcp.dart.client import DartClient
from open_proxy_mcp.tools.parser import (
    parse_agenda_items,
    parse_meeting_info,
    parse_financial_statements,
    parse_personnel,
    parse_aoi,
)

PIPELINE_DIR = "OpenProxy/frontend/src/data/pipeline"

COMPANIES = [
    ("삼성전자", "005930", "삼성전자_MCP_v3.json"),
    ("NAVER", "035420", "NAVER_MCP_v3.json"),
    ("LG화학", "051910", "LG화학_MCP_v3.json"),
    ("케이티앤지", "033780", "케이티앤지_MCP_v3.json"),
    ("셀트리온", "068270", "셀트리온_주총공고_요약_v3.json"),
    ("SK", "034730", "SK_MCP_v3.json"),
    ("SK하이닉스", "000660", "SK하이닉스_MCP_v3.json"),
    ("고려아연", "010130", "고려아연_MCP_v3.json"),
    ("DB손해보험", "005830", "DB손해보험_MCP_v3.json"),
    ("코웨이", "021240", "코웨이_MCP_v3.json"),
]


def _match_agenda(agenda: dict, parsed_number: str) -> bool:
    """안건 번호 매치"""
    aid = agenda.get("agendaId", "")
    label = agenda.get("agendaLabel", "")
    return f"제{aid}호" == parsed_number or label == parsed_number


def _update_candidates(agenda: dict, personnel_result: dict):
    """personnel 파서 결과를 agenda.keyData.candidates에 반영"""
    kd = agenda.get("keyData", {})
    title = agenda.get("title", "")
    label = agenda.get("agendaLabel", "")

    # 선임/해임 안건인지 확인
    if not any(kw in title for kw in ("선임", "해임", "중임", "연임", "재선임")):
        return

    for appt in personnel_result.get("appointments", []):
        appt_title = appt.get("title", "")
        appt_number = appt.get("number", "")
        # 매칭: 번호 일치 or 제목 일치 or 제목 키워드 포함
        matched = False
        if appt_number and appt_number == label:
            matched = True
        elif appt_title == title:
            matched = True
        elif appt_title and appt_title in title:
            matched = True
        elif "이사" in title and "이사" in appt_title and "감사" not in title and "감사" not in appt_title:
            matched = True
        elif "감사위원" in title and "감사위원" in appt_title:
            matched = True
        elif "감사" in title and "감사" in appt_title and "위원" not in title:
            matched = True

        if matched:
            cands = appt.get("candidates", [])
            if cands:
                kd["candidates"] = cands
                return


def _update_charter_changes(agenda: dict, aoi_result: dict, parsed_items: list):
    """aoi 파서 결과를 agenda.keyData.charterChanges에 반영"""
    kd = agenda.get("keyData", {})
    title = agenda.get("title", "")
    if "정관" not in title:
        return
    amendments = aoi_result.get("amendments", [])
    if amendments:
        kd["charterChanges"] = amendments


def _snake_to_camel(d):
    """dict 키를 snake_case → camelCase로 재귀 변환"""
    if isinstance(d, dict):
        out = {}
        for k, v in d.items():
            parts = k.split("_")
            camel = parts[0] + "".join(p.capitalize() for p in parts[1:])
            out[camel] = _snake_to_camel(v)
        return out
    if isinstance(d, list):
        return [_snake_to_camel(item) for item in d]
    return d


def _update_financials(agenda: dict, fin_result: dict):
    """financial 파서 결과를 agenda.keyData.financialStatements에 반영 (camelCase 변환)"""
    kd = agenda.get("keyData", {})
    title = agenda.get("title", "")
    if "재무" not in title and "대차" not in title and "손익" not in title:
        return
    if fin_result.get("consolidated", {}).get("balance_sheet") or fin_result.get("separate", {}).get("balance_sheet"):
        kd["financialStatements"] = _snake_to_camel(fin_result)


async def process_company(client: DartClient, name: str, ticker: str, json_file: str):
    """기업 하나 처리"""
    json_path = os.path.join(PIPELINE_DIR, json_file)
    if not os.path.exists(json_path):
        print(f"  SKIP {name}: {json_file} 없음", flush=True)
        return

    # 최신 rcept_no 검색
    result = await client.search_filings_by_ticker(
        ticker=ticker, bgn_de="20260101", end_de="20260401", pblntf_ty="E"
    )
    filings = [f for f in result.get("list", []) if "소집" in f.get("report_nm", "")]
    if not filings:
        print(f"  SKIP {name}: 소집공고 없음", flush=True)
        return

    rcept_no = filings[0]["rcept_no"]
    await asyncio.sleep(2)

    # 문서 가져오기
    doc = await client.get_document(rcept_no)
    html = doc["html"]
    text = doc["text"]

    # 파서 실행
    parsed_items = parse_agenda_items(text, html)
    meeting_info = parse_meeting_info(text, html)
    fin = parse_financial_statements(html)
    pers = parse_personnel(html)
    aoi = parse_aoi(html, sub_agendas=[
        {"number": item["number"], "title": item["title"]}
        for item in parsed_items
        for child in [item] + item.get("children", [])
    ] if parsed_items else None)

    # 기존 JSON 로드
    with open(json_path, "r", encoding="utf-8") as f:
        existing = json.load(f)

    # meetingInfo 업데이트
    if meeting_info.get("datetime"):
        existing["meetingInfo"]["meetingDateTime"] = meeting_info.get("datetime", "")
        existing["meetingInfo"]["meetingLocation"] = meeting_info.get("location", "")
        existing["meetingInfo"]["fiscalTerm"] = meeting_info.get("meeting_term", "")

    # 각 agenda의 keyData 업데이트
    for agenda in existing.get("agendas", []):
        _update_candidates(agenda, pers)
        _update_charter_changes(agenda, aoi, parsed_items)
        _update_financials(agenda, fin)

    # 저장
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    pers_count = len(pers.get("appointments", []))
    aoi_count = len(aoi.get("amendments", []))
    fin_ok = bool(fin.get("consolidated", {}).get("balance_sheet") or fin.get("separate", {}).get("balance_sheet"))
    print(f"  OK {name} ({rcept_no}): fin={'Y' if fin_ok else 'N'} pers={pers_count} aoi={aoi_count}", flush=True)


async def main():
    client = DartClient()
    print("=== Pipeline JSON 재생성 ===", flush=True)
    for name, ticker, json_file in COMPANIES:
        try:
            await process_company(client, name, ticker, json_file)
        except Exception as e:
            print(f"  ERR {name}: {e}", flush=True)
        await asyncio.sleep(2)
    print("\n완료.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
