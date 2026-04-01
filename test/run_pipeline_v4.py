"""Pipeline v4 JSON 생성 -- XML -> PDF -> OCR 자동 fallback + 신규 파서 4종 + voteResults

paid-open-proxy용 배치 파이프라인 v4:
  1. filing_tracker.json 기반 실행 (API 호출 0회)
  2. DART 캐시에서 XML 파싱 (기본)
  3. 품질 부족 시 PDF 다운로드 + opendataloader 파싱
  4. 여전히 부족 시 Upstage OCR
  5. pipeline_result JSON의 투표결과 합침
  6. 최선 결과를 v4 pipeline JSON으로 저장
"""

import asyncio
import glob
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from open_proxy_mcp.dart.client import get_dart_client
from open_proxy_mcp.tools.parser import (
    parse_agenda_xml,
    validate_agenda_result,
    parse_meeting_info_xml,
    parse_financials_xml,
    parse_personnel_xml,
    parse_aoi_xml,
    parse_compensation_xml,
    parse_treasury_share_xml,
    parse_capital_reserve_xml,
    parse_retirement_pay_xml,
)
from open_proxy_mcp.tools.pdf_parser import (
    parse_financials_pdf,
    parse_personnel_pdf,
    parse_aoi_pdf,
    parse_compensation_pdf,
    parse_treasury_share_pdf,
    parse_capital_reserve_pdf,
    parse_retirement_pay_pdf,
    parse_agenda_pdf,
    ocr_fallback_for_parser,
)

PIPELINE_DIR = "OpenProxy/frontend/src/data/pipeline"
TEMP_DIR = "OpenProxy/frontend/src/data/.pipeline_v4_temp"
RESULT_DIR = "OpenProxy/frontend/src/data/pipeline_result"
TRACKER_PATH = "data/filing_tracker.json"

PDF_CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache", "pdf")
PDF_MD_CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache", "pdf_parsed")


# ---------------------------------------------------------------------------
# filing_tracker 기반 기업 로드
# ---------------------------------------------------------------------------

def load_companies():
    """filing_tracker.json에서 기업 목록 + 최신 rcept_no 로드"""
    with open(TRACKER_PATH, encoding="utf-8") as f:
        tracker = json.load(f)
    companies = []
    for code, info in tracker.items():
        stock_code = info.get("stockCode", code)
        name = info.get("name", "")
        safe_name = re.sub(r'[^\w가-힣]', '', name)
        # 최신 rcept_no
        filings = info.get("filings", [])
        latest = next((f for f in filings if f.get("latest")), filings[0] if filings else None)
        if not latest:
            continue
        # 공고일: 최신 filing의 date
        notice_date = latest.get("date")
        # 정정 여부
        is_corrected = latest.get("type", "") == "정정"
        # 주총일, 종료 여부
        meeting_date = info.get("meetingDate")
        meeting_ended = info.get("meetingEnded", False)

        companies.append({
            "name": name,
            "stock_code": stock_code,
            "safe_name": safe_name,
            "rcept_no": latest["rceptNo"],
            "notice_date": notice_date,
            "meeting_date": meeting_date,
            "is_corrected": is_corrected,
            "meeting_ended": meeting_ended,
        })
    return companies


# ---------------------------------------------------------------------------
# pipeline_result (투표결과) 로드
# ---------------------------------------------------------------------------

def _load_vote_results(stock_code: str) -> dict | None:
    """pipeline_result에서 투표결과 로드"""
    pattern = os.path.join(RESULT_DIR, f"A{stock_code}_result_*.json")
    files = glob.glob(pattern)
    if not files:
        return None
    with open(files[0], "r", encoding="utf-8") as f:
        data = json.load(f)
    return {
        "items": data.get("vote_results", []),
        "attendance": data.get("attendance"),
        "sections": data.get("sections", []),
        "rcept_no": data.get("rcept_no"),
        "rcept_dt": data.get("rcept_dt"),
    }


# ---------------------------------------------------------------------------
# PDF 마크다운 캐시
# ---------------------------------------------------------------------------

def _get_or_parse_pdf_markdown(rcept_no: str, pdf_bytes: bytes) -> str:
    """PDF 마크다운 캐시 확인 or opendataloader로 새로 파싱"""
    os.makedirs(PDF_MD_CACHE_DIR, exist_ok=True)
    md_path = os.path.join(PDF_MD_CACHE_DIR, f"{rcept_no}.md")

    if os.path.exists(md_path):
        with open(md_path, "r") as f:
            return f.read()

    from opendataloader_pdf import convert

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    try:
        convert(
            input_path=[tmp_path],
            output_dir=PDF_MD_CACHE_DIR,
            format="markdown",
            quiet=True,
            keep_line_breaks=True,
            table_method="cluster",
        )
        # opendataloader 출력 파일명 -> rcept_no.md로 rename
        generated = glob.glob(os.path.join(PDF_MD_CACHE_DIR, "tmp*.md"))
        for g in generated:
            os.rename(g, md_path)
            break
    finally:
        os.unlink(tmp_path)

    if os.path.exists(md_path):
        with open(md_path, "r") as f:
            return f.read()
    return ""


# ---------------------------------------------------------------------------
# PDF fallback 판정
# ---------------------------------------------------------------------------

def _needs_pdf_fallback(agenda, fin, pers, aoi, comp, treas) -> bool:
    """XML 결과가 PDF 보강이 필요한지 판정"""
    # HARD: 결과 없음
    if not fin.get("consolidated", {}).get("balance_sheet") and not fin.get("separate", {}).get("balance_sheet"):
        return True
    if not pers.get("appointments"):
        return True
    if not aoi.get("amendments"):
        return True
    if not comp.get("items"):
        return True
    if not treas.get("items"):
        return True
    # SOFT: 경력 병합
    for apt in pers.get("appointments", []):
        for c in apt.get("candidates", []):
            for cd in c.get("careerDetails", []):
                if len(cd.get("content", "")) > 100:
                    return True
    return False


def _needs_ocr_fallback(agenda, fin, pers, aoi) -> bool:
    """PDF 결과도 부족한지 판정"""
    if not fin.get("consolidated", {}).get("balance_sheet") and not fin.get("separate", {}).get("balance_sheet"):
        return True
    if not pers.get("appointments"):
        return True
    if not aoi.get("amendments"):
        return True
    return False


# ---------------------------------------------------------------------------
# 유틸 함수
# ---------------------------------------------------------------------------

def _snake_to_camel(d):
    """dict 키를 snake_case -> camelCase로 재귀 변환"""
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


def _match_agenda(agenda: dict, parsed_number: str) -> bool:
    """안건 번호 매치"""
    aid = agenda.get("agendaId", "")
    label = agenda.get("agendaLabel", "")
    return f"제{aid}호" == parsed_number or label == parsed_number


# ---------------------------------------------------------------------------
# keyData 업데이트 함수들
# ---------------------------------------------------------------------------

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

    # sub_agendas 재귀
    for sub in agenda.get("subAgendas", []):
        _update_candidates(sub, personnel_result)


def _update_charter_changes(agenda: dict, aoi_result: dict, parsed_items: list):
    """aoi 파서 결과를 agenda.keyData.charterChanges에 반영"""
    kd = agenda.get("keyData", {})
    title = agenda.get("title", "")
    if "정관" not in title:
        # sub_agendas 재귀
        for sub in agenda.get("subAgendas", []):
            _update_charter_changes(sub, aoi_result, parsed_items)
        return
    amendments = aoi_result.get("amendments", [])
    if amendments:
        kd["charterChanges"] = amendments

    for sub in agenda.get("subAgendas", []):
        _update_charter_changes(sub, aoi_result, parsed_items)


def _update_financials(agenda: dict, fin_result: dict):
    """financial 파서 결과를 agenda.keyData.financialStatements에 반영 (camelCase 변환)"""
    kd = agenda.get("keyData", {})
    title = agenda.get("title", "")
    if "재무" not in title and "대차" not in title and "손익" not in title:
        for sub in agenda.get("subAgendas", []):
            _update_financials(sub, fin_result)
        return
    if fin_result.get("consolidated", {}).get("balance_sheet") or fin_result.get("separate", {}).get("balance_sheet"):
        kd["financialStatements"] = _snake_to_camel(fin_result)

    for sub in agenda.get("subAgendas", []):
        _update_financials(sub, fin_result)


def _update_compensation(agenda: dict, comp_result: dict):
    """compensation 파서 결과를 agenda.keyData.compensation에 반영"""
    kd = agenda.get("keyData", {})
    title = agenda.get("title", "")
    if "보수" not in title and "한도" not in title:
        for sub in agenda.get("subAgendas", []):
            _update_compensation(sub, comp_result)
        return
    items = comp_result.get("items", [])
    if not items:
        for sub in agenda.get("subAgendas", []):
            _update_compensation(sub, comp_result)
        return
    item = items[0]
    cur = item.get("current", {})
    pri = item.get("prior", {})
    # 소진율 계산
    util = None
    if pri.get("actualPaidAmount") and pri.get("limitAmount"):
        try:
            util = round(pri["actualPaidAmount"] / pri["limitAmount"] * 100, 1)
        except (ZeroDivisionError, TypeError):
            pass
    kd["compensation"] = {
        "currentLimit": cur.get("limitAmount"),
        "currentLimitDisplay": cur.get("limit"),
        "currentHeadcount": cur.get("headcount"),
        "directorCount": cur.get("totalDirectors"),
        "outsideDirectorCount": cur.get("outsideDirectors"),
        "previousLimit": pri.get("limitAmount"),
        "previousLimitDisplay": pri.get("limit"),
        "previousActualPaid": pri.get("actualPaidAmount"),
        "previousActualPaidDisplay": pri.get("actualPaid"),
        "previousHeadcount": pri.get("headcount"),
        "utilizationPct": util,
        "target": item.get("target"),
    }

    for sub in agenda.get("subAgendas", []):
        _update_compensation(sub, comp_result)


def _update_treasury_stock(agenda: dict, treas_result: dict):
    """treasury_share 파서 결과를 agenda.keyData.treasuryStock에 반영"""
    kd = agenda.get("keyData", {})
    title = agenda.get("title", "")
    if not any(kw in title for kw in ["자기주식", "보유", "처분", "소각"]):
        for sub in agenda.get("subAgendas", []):
            _update_treasury_stock(sub, treas_result)
        return
    items = treas_result.get("items", [])
    if not items:
        for sub in agenda.get("subAgendas", []):
            _update_treasury_stock(sub, treas_result)
        return
    kd["treasuryStock"] = _snake_to_camel(items[0])

    for sub in agenda.get("subAgendas", []):
        _update_treasury_stock(sub, treas_result)


# ---------------------------------------------------------------------------
# 분석 상태 판정
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# v4 JSON 빌드
# ---------------------------------------------------------------------------

def _build_v4_json(name, ticker, rcept_no, parsed_items, meeting_info,
                   fin, pers, aoi, comp, treas, vote_results,
                   notice_date=None, meeting_date=None, is_corrected=False):
    """파서 결과로 v4 pipeline JSON 골격 생성"""
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

    # keyData 채우기 (sub_agendas 포함 -- 각 함수 내부에서 재귀)
    for agenda in agendas:
        _update_candidates(agenda, pers)
        _update_charter_changes(agenda, aoi, parsed_items)
        _update_financials(agenda, fin)
        _update_compensation(agenda, comp)
        _update_treasury_stock(agenda, treas)

    return {
        "schemaVersion": "v4",
        "meetingInfo": {
            "companyName": name,
            "stockCode": ticker,
            "fiscalTerm": meeting_info.get("meeting_term", ""),
            "meetingDateTime": meeting_info.get("datetime", ""),
            "meetingDate": meeting_date,
            "meetingLocation": meeting_info.get("location", ""),
            "noticeDate": notice_date,
            "isCorrected": is_corrected,
            "rceptNo": rcept_no,
        },
        "agendas": agendas,
        "voteResults": vote_results,
    }


# ---------------------------------------------------------------------------
# 기업 하나 처리
# ---------------------------------------------------------------------------

async def process_company(name: str, stock_code: str, safe_name: str, rcept_no: str,
                          notice_date: str = None, meeting_date: str = None,
                          is_corrected: bool = False, meeting_ended: bool = False):
    """기업 하나 처리 -- filing_tracker의 rcept_no 직접 사용 (API 호출 0회)"""
    client = get_dart_client()

    # 캐시 기반 문서 가져오기
    doc = await client.get_document_cached(rcept_no)
    html = doc["html"]
    text = doc["text"]

    # -- 1단계: XML 파서 --
    parsed_items = parse_agenda_xml(text, html)
    meeting_info = parse_meeting_info_xml(text, html)
    fin = parse_financials_xml(html)
    pers = parse_personnel_xml(html)
    aoi = parse_aoi_xml(html, sub_agendas=[
        {"number": item["number"], "title": item["title"]}
        for item in parsed_items
        for child in [item] + item.get("children", [])
    ] if parsed_items else None)

    # 신규 XML 파서 4종
    comp = parse_compensation_xml(html)
    treas = parse_treasury_share_xml(html)
    cap_res = parse_capital_reserve_xml(html)
    retire = parse_retirement_pay_xml(html)

    # -- 2단계: PDF fallback (XML 품질 부족 시) --
    needs_pdf = _needs_pdf_fallback(parsed_items, fin, pers, aoi, comp, treas)
    pdf_md = None
    pdf_bytes = None

    if needs_pdf:
        try:
            pdf_bytes = await client.get_document_pdf(rcept_no)
            pdf_md = _get_or_parse_pdf_markdown(rcept_no, pdf_bytes)
        except Exception as e:
            print(f"    PDF 다운로드 실패: {e}", flush=True)

    if pdf_md:
        # 파서별 PDF 보강
        if not fin.get("consolidated", {}).get("balance_sheet") and not fin.get("separate", {}).get("balance_sheet"):
            pdf_fin = parse_financials_pdf(pdf_md)
            if pdf_fin.get("consolidated", {}).get("balance_sheet") or pdf_fin.get("separate", {}).get("balance_sheet"):
                fin = pdf_fin
                print(f"    -> fin: PDF fallback 성공", flush=True)

        if not pers.get("appointments"):
            pdf_pers = parse_personnel_pdf(pdf_md)
            if pdf_pers.get("appointments"):
                pers = pdf_pers
                print(f"    -> pers: PDF fallback 성공", flush=True)
        else:
            # SOFT_FAIL: 경력 병합 체크
            has_merged = any(
                len(cd.get("content", "")) > 100
                for apt in pers.get("appointments", [])
                for c in apt.get("candidates", [])
                for cd in c.get("careerDetails", [])
            )
            if has_merged:
                pdf_pers = parse_personnel_pdf(pdf_md)
                if pdf_pers.get("appointments"):
                    pers = pdf_pers
                    print(f"    -> pers: PDF 경력 분리 보강", flush=True)

        if not aoi.get("amendments"):
            pdf_aoi = parse_aoi_pdf(pdf_md)
            if pdf_aoi.get("amendments"):
                aoi = pdf_aoi
                print(f"    -> aoi: PDF fallback 성공", flush=True)

        # 신규 파서 PDF fallback
        if not comp.get("items"):
            pdf_comp = parse_compensation_pdf(pdf_md)
            if pdf_comp.get("items"):
                comp = pdf_comp
                print(f"    -> comp: PDF fallback 성공", flush=True)

        if not treas.get("items"):
            pdf_treas = parse_treasury_share_pdf(pdf_md)
            if pdf_treas.get("items"):
                treas = pdf_treas
                print(f"    -> treas: PDF fallback 성공", flush=True)

    # -- 3단계: OCR fallback (PDF도 부족 시) --
    if pdf_bytes and pdf_md:
        needs_ocr = _needs_ocr_fallback(parsed_items, fin, pers, aoi)
        if needs_ocr:
            for parser_type, parser_fn, result_ref, check_key in [
                ("fin", parse_financials_pdf, "fin", "consolidated"),
                ("pers", parse_personnel_pdf, "pers", "appointments"),
                ("aoi", parse_aoi_pdf, "aoi", "amendments"),
            ]:
                current = locals()[result_ref]
                if parser_type == "fin" and (current.get("consolidated", {}).get("balance_sheet") or current.get("separate", {}).get("balance_sheet")):
                    continue
                if parser_type != "fin" and current.get(check_key):
                    continue

                ocr_result = ocr_fallback_for_parser(
                    pdf_bytes, pdf_md, parser_type, parser_fn, f"{name}.pdf"
                )
                if ocr_result:
                    if parser_type == "fin":
                        fin = ocr_result
                    elif parser_type == "pers":
                        pers = ocr_result
                    elif parser_type == "aoi":
                        aoi = ocr_result
                    print(f"    -> {parser_type}: OCR fallback 성공", flush=True)

    # -- 투표결과 로드 --
    vote_results = _load_vote_results(stock_code)

    # -- v4 JSON 빌드 --
    data = _build_v4_json(
        name, stock_code, rcept_no, parsed_items, meeting_info,
        fin, pers, aoi, comp, treas, vote_results,
        notice_date=notice_date, meeting_date=meeting_date, is_corrected=is_corrected,
    )

    # 분석 상태
    data["analysisStatus"] = _compute_analysis_status(parsed_items, fin, pers, aoi)

    # 파일 저장 (임시 디렉토리 → 완료 후 일괄 이동)
    os.makedirs(TEMP_DIR, exist_ok=True)
    json_file = f"A{stock_code}_v4_parsed_{safe_name}.json"
    json_path = os.path.join(TEMP_DIR, json_file)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # 결과 요약
    pers_count = len(pers.get("appointments", []))
    aoi_count = len(aoi.get("amendments", []))
    fin_ok = bool(fin.get("consolidated", {}).get("balance_sheet") or fin.get("separate", {}).get("balance_sheet"))
    comp_ok = bool(comp.get("items"))
    treas_ok = bool(treas.get("items"))
    vote_ok = vote_results is not None

    return {
        "fin": fin_ok,
        "pers": pers_count,
        "aoi": aoi_count,
        "comp": comp_ok,
        "treas": treas_ok,
        "vote": vote_ok,
    }


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

async def main():
    companies = load_companies()
    total = len(companies)
    print(f"=== Pipeline v4 JSON 생성 ({total}개, filing_tracker 기반) ===", flush=True)

    # 파서별 성공 카운트
    stats = {"fin": 0, "pers": 0, "aoi": 0, "comp": 0, "treas": 0, "vote": 0, "ok": 0, "err": 0}

    for i, company in enumerate(companies):
        name = company["name"]
        stock_code = company["stock_code"]
        safe_name = company["safe_name"]
        rcept_no = company["rcept_no"]

        try:
            result = await process_company(
                name, stock_code, safe_name, rcept_no,
                notice_date=company.get("notice_date"),
                meeting_date=company.get("meeting_date"),
                is_corrected=company.get("is_corrected", False),
                meeting_ended=company.get("meeting_ended", False),
            )
            stats["ok"] += 1
            if result["fin"]:
                stats["fin"] += 1
            if result["pers"] > 0:
                stats["pers"] += 1
            if result["aoi"] > 0:
                stats["aoi"] += 1
            if result["comp"]:
                stats["comp"] += 1
            if result["treas"]:
                stats["treas"] += 1
            if result["vote"]:
                stats["vote"] += 1
            print(
                f"  [{i+1}/{total}] {name} ({rcept_no}): "
                f"fin={'Y' if result['fin'] else 'N'} "
                f"pers={result['pers']} "
                f"aoi={result['aoi']} "
                f"comp={'Y' if result['comp'] else 'N'} "
                f"treas={'Y' if result['treas'] else 'N'} "
                f"vote={'Y' if result['vote'] else 'N'}",
                flush=True,
            )
        except Exception as e:
            stats["err"] += 1
            print(f"  [{i+1}/{total}] ERR {name}: {e}", flush=True)

        # 10건마다 진행률 + 파서별 성공 카운트
        if (i + 1) % 10 == 0:
            pct = (i + 1) / total * 100
            print(
                f"  --- {i+1}/{total} ({pct:.0f}%) | "
                f"ok={stats['ok']} err={stats['err']} | "
                f"fin={stats['fin']} pers={stats['pers']} aoi={stats['aoi']} "
                f"comp={stats['comp']} treas={stats['treas']} vote={stats['vote']} ---",
                flush=True,
            )

    # 임시 → 본 디렉토리 일괄 이동
    import shutil
    os.makedirs(PIPELINE_DIR, exist_ok=True)
    # 기존 v4 파일 제거
    for f in glob.glob(os.path.join(PIPELINE_DIR, "A*_v4_parsed_*.json")):
        os.remove(f)
    # temp에서 이동
    count = 0
    for f in glob.glob(os.path.join(TEMP_DIR, "A*_v4_parsed_*.json")):
        shutil.move(f, os.path.join(PIPELINE_DIR, os.path.basename(f)))
        count += 1
    # temp 삭제
    shutil.rmtree(TEMP_DIR, ignore_errors=True)
    print(f"\n  {count}개 파일을 pipeline/ 으로 일괄 이동 완료", flush=True)

    # 최종 요약
    print(f"\n=== 완료 ({total}개) ===", flush=True)
    print(
        f"  성공: {stats['ok']} | 실패: {stats['err']}\n"
        f"  fin: {stats['fin']} | pers: {stats['pers']} | aoi: {stats['aoi']}\n"
        f"  comp: {stats['comp']} | treas: {stats['treas']} | vote: {stats['vote']}",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
