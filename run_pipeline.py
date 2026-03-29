"""Pipeline JSON 재생성 — 파서 결과로 keyData 업데이트 (기존 구조 유지)"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from open_proxy_mcp.dart.client import DartClient
from open_proxy_mcp.tools.parser import (
    parse_agenda_xml,
    validate_agenda_result,
    parse_meeting_info_xml,
    parse_financials_xml,
    parse_personnel_xml,
    parse_aoi_xml,
)

PIPELINE_DIR = "OpenProxy/frontend/src/data/pipeline"

KOSPI200_RAW = """005930    삼성전자
000660    SK하이닉스
005380    현대차
373220    LG에너지솔루션
402340    SK스퀘어
207940    삼성바이오로직스
012450    한화에어로스페이스
034020    두산에너빌리티
000270    기아
105560    KB금융
329180    HD현대중공업
028260    삼성물산
032830    삼성생명
068270    셀트리온
055550    신한지주
042660    한화오션
012330    현대모비스
006800    미래에셋증권
035420    NAVER
267260    HD현대일렉트릭
009150    삼성전기
010130    고려아연
006400    삼성SDI
086790    하나금융지주
015760    한국전력
042700    한미반도체
005490    POSCO홀딩스
009540    HD한국조선해양
010120    LS ELECTRIC
034730    SK
298040    효성중공업
272210    한화시스템
316140    우리금융지주
010140    삼성중공업
000810    삼성화재
035720    카카오
051910    LG화학
267250    HD현대
011200    HMM
024110    기업은행
064350    현대로템
096770    SK이노베이션
066570    LG전자
138040    메리츠금융지주
033780    KT&G
000150    두산
000720    현대건설
003670    포스코퓨처엠
047810    한국항공우주
086280    현대글로비스
017670    SK텔레콤
030200    KT
079550    LIG넥스원
047050    포스코인터내셔널
003550    LG
352820    하이브
005830    DB손해보험
278470    에이피알
010950    S-Oil
005940    NH투자증권
071050    한국금융지주
018260    삼성에스디에스
323410    카카오뱅크
039490    키움증권
307950    현대오토에버
259960    크래프톤
003490    대한항공
007660    이수페타시스
006260    LS
016360    삼성증권
003230    삼양식품
090430    아모레퍼시픽
180640    한진칼
377300    카카오페이
000880    한화
009830    한화솔루션
443060    HD현대마린솔루션
000100    유한양행
326030    SK바이오팜
047040    대우건설
161390    한국타이어앤테크놀로지
028050    삼성E&A
029780    삼성카드
011070    LG이노텍
128940    한미약품
032640    LG유플러스
078930    GS
052690    한전기술
064400    LG씨엔에스
034220    LG디스플레이
001040    CJ
241560    두산밥캣
138930    BNK금융지주
454910    두산로보틱스
175330    JB금융지주
001440    대한전선
271560    오리온
021240    코웨이
022100    포스코DX
036570    엔씨소프트
004020    현대제철
450080    에코프로머티
002380    KCC
066970    엘앤에프
062040    산일전기
251270    넷마블
018880    한온시스템
088350    한화생명
011790    SKC
111770    영원무역
035250    강원랜드
082740    한화엔진
010060    OCI홀딩스
011170    롯데케미칼
051900    LG생활건강
014680    한솔케미칼
012750    에스원
011780    금호석유화학
036460    한국가스공사
004990    롯데지주
004170    신세계
302440    SK바이오사이언스
017800    현대엘리베이터
097950    CJ제일제당
112610    씨에스윈드
051600    한전KPS
023530    롯데쇼핑
009970    영원무역홀딩스
009420    한올바이오파마
006360    GS건설
001450    현대해상
139130    iM금융지주
005850    에스엘
000120    CJ대한통운
000240    한국앤컴퍼니
008930    한미사이언스
017960    한국카본
026960    동서
028670    팬오션
030000    제일기획
071970    HD현대마린엔진
103140    풍산
139480    이마트
192820    코스맥스
204320    HL만도
375500    DL이앤씨
383220    F&F
457190    이수스페셜티케미컬
007340    DN오토모티브
120110    코오롱인더
069960    현대백화점
011210    현대위아
001430    세아베스틸지주
081660    미스토홀딩스
004370    농심
282330    BGF리테일
002790    아모레퍼시픽홀딩스
006040    동원산업
006280    녹십자
001800    오리온홀딩스
073240    금호타이어
298020    효성티앤씨
361610    SK아이이테크놀로지
161890    한국콜마
007070    GS리테일
007310    오뚜기
034230    파라다이스
069620    대웅제약
008770    호텔신라
005300    롯데칠성
004000    롯데정밀화학
003240    태광산업
003090    대웅
000670    영풍
000080    하이트진로
000210    DL
300720    한일시멘트
192080    더블유게임즈
185750    종근당
071320    지역난방공사
285130    SK케미칼
014820    동원시스템즈
069260    TKG휴켐스
006650    대한유화
009240    한샘
005250    녹십자홀딩스
004490    세방전지
093370    후성
114090    GKL
003030    세아제강지주
137310    에스디바이오센서
002840    미원상사
001680    대상
298050    HS효성첨단소재
280360    롯데웰푸드
008730    율촌화학
268280    미원에스씨
002030    아세아
005420    코스모화학"""

# 기존 파일명 매핑 (이미 있는 파일은 보존)
_EXISTING_FILES = {
    "005930": "삼성전자_MCP_v3.json",
    "000660": "SK하이닉스_MCP_v3.json",
    "035420": "NAVER_MCP_v3.json",
    "051910": "LG화학_MCP_v3.json",
    "033780": "케이티앤지_MCP_v3.json",
    "068270": "셀트리온_주총공고_요약_v3.json",
    "034730": "SK_MCP_v3.json",
    "010130": "고려아연_MCP_v3.json",
    "005830": "DB손해보험_MCP_v3.json",
    "021240": "코웨이_MCP_v3.json",
}

def _parse_kospi200():
    companies = []
    for line in KOSPI200_RAW.strip().split('\n'):
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            ticker, name = parts
            json_file = _EXISTING_FILES.get(ticker, f"{name}_MCP_v3.json")
            companies.append((name, ticker, json_file))
    return companies

COMPANIES = _parse_kospi200()


def _match_agenda(agenda: dict, parsed_number: str) -> bool:
    """안건 번호 매치"""
    aid = agenda.get("agendaId", "")
    label = agenda.get("agendaLabel", "")
    return f"제{aid}호" == parsed_number or label == parsed_number


def _update_candidates(agenda: dict, personnel_result: dict):
    """personnel 파서 결과를 agenda.keyData.candidates에 반영"""
    import re as _re
    kd = agenda.get("keyData", {})
    title = agenda.get("title", "")
    label = agenda.get("agendaLabel", "")

    if not any(kw in title for kw in ("선임", "해임", "중임", "연임", "재선임")):
        return

    # 제목에서 후보자 이름 추출 (후보: 이름, 이름 선임의 건)
    title_names = set(_re.findall(r'(?:후보\s*[:：]?\s*|이사\s+|감사\s+)([가-힣]{2,4})', title))

    # 1차: 번호 정확 매치
    for appt in personnel_result.get("appointments", []):
        if appt.get("number") and appt["number"] == label:
            cands = appt.get("candidates", [])
            if cands:
                kd["candidates"] = cands
                return

    # 2차: 제목에 후보자 이름이 있으면 그 이름이 candidates에 포함된 appt 매칭
    if title_names:
        for appt in personnel_result.get("appointments", []):
            cands = appt.get("candidates", [])
            cand_names = {c.get("name", "") for c in cands}
            if title_names & cand_names:
                # 이름 일치하는 후보자만 필터링
                matched_cands = [c for c in cands if c.get("name") in title_names]
                if matched_cands:
                    kd["candidates"] = matched_cands
                    return

    # 3차: 카테고리 키워드 매칭 (이름 없는 일반 안건)
    for appt in personnel_result.get("appointments", []):
        appt_title = appt.get("title", "")
        matched = False
        if appt_title == title:
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


def _compute_analysis_status(parsed_items, fin, pers, aoi) -> str:
    """분석 상태 판정: 정상 / 검토 필요 / 실패"""
    if not parsed_items or not validate_agenda_result(parsed_items):
        return "실패"

    issues = 0

    # 재무 안건이 있는데 데이터가 없으면
    has_fin_agenda = any("재무" in item.get("title", "") or "대차" in item.get("title", "") for item in parsed_items)
    has_fin_data = bool(
        (fin.get("consolidated") or {}).get("balance_sheet", {}).get("rows")
        or (fin.get("separate") or {}).get("balance_sheet", {}).get("rows")
    )
    if has_fin_agenda and not has_fin_data:
        issues += 1

    # 선임 안건이 있는데 후보자가 없으면
    has_pers_agenda = any(
        kw in item.get("title", "") for item in parsed_items for kw in ("선임", "해임")
    )
    has_pers_data = any(
        len(a.get("candidates", [])) > 0 for a in pers.get("appointments", [])
    )
    if has_pers_agenda and not has_pers_data:
        issues += 1

    # 정관 안건이 있는데 amendments가 없으면
    has_aoi_agenda = any("정관" in item.get("title", "") for item in parsed_items)
    has_aoi_data = len(aoi.get("amendments", [])) > 0
    if has_aoi_agenda and not has_aoi_data:
        issues += 1

    # 경력 이슈 (content > 100자)
    for a in pers.get("appointments", []):
        for c in a.get("candidates", []):
            for cd in c.get("careerDetails", []):
                if len(cd.get("content", "")) > 100:
                    issues += 1
                    break

    return "정상" if issues == 0 else "검토 필요"


def _build_new_json(name, ticker, rcept_no, parsed_items, meeting_info, fin, pers, aoi):
    """파서 결과로 v3 pipeline JSON 골격 생성"""
    def _build_agenda(item, parent_id=None):
        aid = item["number"].replace("제", "").replace("호", "")
        title = item["title"]
        depth = "main" if parent_id is None else "sub"

        kd = {
            "financials": [],
            "charterChanges": [],
            "candidates": [],
            "compensation": None,
            "treasuryStock": None,
            "financialStatements": None,
        }

        node = {
            "agendaId": aid,
            "agendaLabel": item["number"],
            "title": title,
            "fullTitle": f"{item['number']}: {title}",
            "depth": depth,
            "parentAgendaId": parent_id,
            "classification": {
                "primaryCode": "",
                "primaryLabel": "",
                "secondaryCodes": [],
                "allCodes": [],
                "reviewRequired": False,
            },
            "conditional": {
                "isConditional": bool(item.get("conditional")),
                "conditionText": item.get("conditional"),
                "ifRejectedAction": None,
            },
            "governanceAnalysis": {
                "lawResponse": [],
                "defenseStrategyMatches": [],
                "selectionStructure": {
                    "totalDirectorsToElect": None,
                    "separateElection": None,
                    "staggeredBoardSignal": "없음",
                    "cumulativeVotingSignal": "없음",
                },
            },
            "summary": {"oneLine": title, "highlights": []},
            "keyData": kd,
            "checklist": [],
            "subAgendas": [],
        }

        for child in item.get("children", []):
            node["subAgendas"].append(_build_agenda(child, parent_id=aid))

        return node

    agendas = [_build_agenda(item) for item in parsed_items]

    # keyData 채우기
    for agenda in agendas:
        _update_candidates(agenda, pers)
        _update_charter_changes(agenda, aoi, parsed_items)
        _update_financials(agenda, fin)

    return {
        "schemaVersion": "v3",
        "meetingInfo": {
            "companyName": name,
            "stockCode": ticker,
            "fiscalTerm": meeting_info.get("meeting_term", ""),
            "meetingDateTime": meeting_info.get("datetime", ""),
            "meetingDate": None,
            "meetingLocation": meeting_info.get("location", ""),
            "noticeDate": None,
        },
        "agendas": agendas,
    }


async def process_company(client: DartClient, name: str, ticker: str, json_file: str):
    """기업 하나 처리 — 기존 JSON 있으면 업데이트, 없으면 새로 생성"""
    json_path = os.path.join(PIPELINE_DIR, json_file)
    is_new = not os.path.exists(json_path)

    # 최신 rcept_no 검색
    result = await client.search_filings_by_ticker(
        ticker=ticker, bgn_de="20260101", end_de="20260401", pblntf_ty="E"
    )
    filings = [f for f in result.get("list", []) if "소집" in f.get("report_nm", "")]
    if not filings:
        print(f"  SKIP {name}: 소집공고 없음", flush=True)
        return

    latest = filings[0]
    rcept_no = latest["rcept_no"]
    rcept_dt = latest.get("rcept_dt", "")  # YYYYMMDD
    is_correction = "정정" in latest.get("report_nm", "")
    notice_date = f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:8]}" if len(rcept_dt) == 8 else None

    # 기업 기본정보 (CEO, 결산월)
    corp_code = result.get("corp_info", {}).get("corp_code", "")
    ceo = "-"
    fiscal_month = "12월"
    if corp_code:
        try:
            company_info = await client.get_company_info(corp_code)
            ceo = company_info.get("ceo_nm", "-") or "-"
            fm = company_info.get("acc_mt", "12")
            fiscal_month = f"{fm}월" if fm else "12월"
        except Exception:
            pass

    await asyncio.sleep(1.5)

    # 문서 가져오기
    doc = await client.get_document(rcept_no)
    html = doc["html"]
    text = doc["text"]

    # 파서 실행
    parsed_items = parse_agenda_xml(text, html)
    meeting_info = parse_meeting_info_xml(text, html)
    fin = parse_financials_xml(html)
    pers = parse_personnel_xml(html)
    aoi = parse_aoi_xml(html, sub_agendas=[
        {"number": item["number"], "title": item["title"]}
        for item in parsed_items
        for child in [item] + item.get("children", [])
    ] if parsed_items else None)

    if is_new:
        data = _build_new_json(name, ticker, rcept_no, parsed_items, meeting_info, fin, pers, aoi)
    else:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["meetingInfo"]["stockCode"] = ticker
        data["meetingInfo"]["companyName"] = name
        if meeting_info.get("datetime"):
            data["meetingInfo"]["meetingDateTime"] = meeting_info.get("datetime", "")
            data["meetingInfo"]["meetingLocation"] = meeting_info.get("location", "")
            data["meetingInfo"]["fiscalTerm"] = meeting_info.get("meeting_term", "")
        for agenda in data.get("agendas", []):
            _update_candidates(agenda, pers)
            _update_charter_changes(agenda, aoi, parsed_items)
            _update_financials(agenda, fin)

    # 공고일/정정/CEO/결산월
    data["meetingInfo"]["rceptNo"] = rcept_no
    data["meetingInfo"]["noticeDate"] = notice_date
    data["meetingInfo"]["isCorrected"] = is_correction
    data["meetingInfo"]["ceo"] = ceo
    data["meetingInfo"]["fiscalMonth"] = fiscal_month

    # 분석 상태: 정상 / 검토 필요 / 실패
    data["analysisStatus"] = _compute_analysis_status(parsed_items, fin, pers, aoi)

    # 저장
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    pers_count = len(pers.get("appointments", []))
    aoi_count = len(aoi.get("amendments", []))
    fin_ok = bool(fin.get("consolidated", {}).get("balance_sheet") or fin.get("separate", {}).get("balance_sheet"))
    tag = "NEW" if is_new else "UPD"
    print(f"  {tag} {name} ({rcept_no}): fin={'Y' if fin_ok else 'N'} pers={pers_count} aoi={aoi_count}", flush=True)


async def main():
    client = DartClient()
    print(f"=== Pipeline JSON 생성/업데이트 ({len(COMPANIES)}개) ===", flush=True)
    for i, (name, ticker, json_file) in enumerate(COMPANIES):
        try:
            await process_company(client, name, ticker, json_file)
        except Exception as e:
            print(f"  ERR {name}: {e}", flush=True)
        if (i + 1) % 50 == 0:
            print(f"  --- {i+1}건 완료, 10초 대기 ---", flush=True)
            await asyncio.sleep(10)
    print(f"\n완료. ({len(COMPANIES)}개)", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
