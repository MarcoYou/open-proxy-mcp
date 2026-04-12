"""주주총회 소집공고 관련 MCP tools"""

import os
import json
import logging
import re
import glob
import tempfile
from datetime import datetime


from open_proxy_mcp.dart.client import DartClient, DartClientError, get_dart_client
from open_proxy_mcp.tools.formatters import (
    format_krw, highlights_has,
    _format_agenda_tree, _format_meeting_info, _format_agenda_details,
    _format_financial_statements, _build_financial_highlight,
    _format_compensation, _format_aoi_change, _format_treasury_share,
    _format_capital_reserve, _format_retirement_pay, _format_personnel,
    _format_correction_details, _format_agm_result, _parse_agm_result_table,
)
from open_proxy_mcp.tools.errors import tool_error, tool_not_found, tool_empty
from open_proxy_mcp.tools.parser import (
    parse_agenda_xml, parse_meeting_info_xml,
    validate_agenda_result, _extract_notice_section, _extract_agenda_zone,
    parse_agenda_details_xml, validate_agenda_details,
    parse_financials_xml,
    parse_corrections_xml,
    parse_personnel_xml,
    parse_aoi_xml,
    parse_compensation_xml,
    parse_treasury_share_xml,
    parse_capital_reserve_xml,
    parse_retirement_pay_xml,
)
from open_proxy_mcp.llm.client import extract_agenda_with_llm
from open_proxy_mcp.tools.pdf_parser import (
    parse_compensation_pdf, parse_personnel_pdf,
    parse_financials_pdf, parse_aoi_pdf, parse_agenda_pdf,
    parse_treasury_share_pdf, parse_capital_reserve_pdf,
    parse_retirement_pay_pdf,
    ocr_fallback_for_parser,
)

logger = logging.getLogger(__name__)


# ── PDF 캐시 (디스크) ──

_PDF_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "cache", "pdf"
)
_PDF_MD_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "cache", "pdf_parsed"
)


async def _get_pdf_cached(rcept_no: str) -> bytes:
    """PDF 바이너리를 디스크 캐시에서 가져오거나 다운로드"""
    os.makedirs(_PDF_CACHE_DIR, exist_ok=True)
    path = os.path.join(_PDF_CACHE_DIR, f"{rcept_no}.pdf")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    client = get_dart_client()
    pdf_bytes = await client.get_document_pdf(rcept_no)
    with open(path, "wb") as f:
        f.write(pdf_bytes)
    return pdf_bytes


def _get_pdf_markdown_cached(rcept_no: str, pdf_bytes: bytes) -> str:
    """opendataloader 마크다운을 디스크 캐시에서 가져오거나 파싱"""
    os.makedirs(_PDF_MD_CACHE_DIR, exist_ok=True)
    md_path = os.path.join(_PDF_MD_CACHE_DIR, f"{rcept_no}.md")
    if os.path.exists(md_path):
        with open(md_path, "r") as f:
            return f.read()

    # opendataloader로 파싱
    import tempfile
    from opendataloader_pdf import convert

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    try:
        convert(
            input_path=[tmp_path],
            output_dir=_PDF_MD_CACHE_DIR,
            format="markdown",
            quiet=True,
            keep_line_breaks=True,
            table_method="cluster",
        )
        # opendataloader는 입력 파일명 기준으로 출력 — rename 필요
        generated = glob.glob(os.path.join(_PDF_MD_CACHE_DIR, "*.md"))
        for g in generated:
            if os.path.basename(g).startswith("tmp"):
                os.rename(g, md_path)
                break
    finally:
        os.unlink(tmp_path)

    if os.path.exists(md_path):
        with open(md_path, "r") as f:
            return f.read()
    return ""

async def _get_document_cached(rcept_no: str) -> dict:
    """get_document_cached를 DartClient 싱글턴에 위임"""
    return await get_dart_client().get_document_cached(rcept_no)


# ── JSON 빌더 ──

def _build_agenda_json(
    rcept_no: str,
    agenda_items: list[dict],
    meeting_info: dict,
    corp_info: dict | None = None,
    parse_method: str = "regex",
) -> dict:
    """파서 결과를 프론트엔드용 JSON 구조로 변환

    agendaId = rceptNo + agendaId 로 유일 식별 가능.
    향후 enrichment(classification, summary 등)는 agendaId로 join.
    """
    def _build_agenda_node(item: dict, parent_id: str | None = None) -> dict:
        aid = item["number"].replace("제", "").replace("호", "")
        depth = "root" if parent_id is None else ("sub" if "-" in aid and aid.count("-") == 1 else "subsub")
        node = {
            "agendaId": aid,
            "number": item["number"],
            "title": item["title"],
            "depth": depth,
            "parentId": parent_id,
            "source": item.get("source"),
            "conditional": item.get("conditional"),
            "children": [],
        }
        for child in item.get("children", []):
            node["children"].append(_build_agenda_node(child, parent_id=aid))
        return node

    agendas = [_build_agenda_node(item) for item in agenda_items]

    return {
        "schemaVersion": "v1",
        "rceptNo": rcept_no,
        "meetingInfo": {
            "corpName": corp_info.get("corp_name") if corp_info else None,
            "stockCode": corp_info.get("stock_code") if corp_info else None,
            "corpCode": corp_info.get("corp_code") if corp_info else None,
            "meetingType": meeting_info.get("meeting_type"),
            "fiscalTerm": meeting_info.get("meeting_term"),
            "isCorrection": meeting_info.get("is_correction", False),
            "datetime": meeting_info.get("datetime"),
            "location": meeting_info.get("location"),
            "reportItems": meeting_info.get("report_items", []),
        },
        "agendas": agendas,
        "parseMeta": {
            "method": parse_method,
            "valid": validate_agenda_result(agenda_items),
            "totalCount": sum(1 for _ in _flatten_agendas(agendas)),
            "rootCount": len(agendas),
        },
    }


def _flatten_agendas(agendas: list[dict]):
    """트리를 플랫하게 순회 (카운팅용)"""
    for a in agendas:
        yield a
        yield from _flatten_agendas(a.get("children", []))


# ── Tool 등록 ──

def register_tools(mcp):
    """FastMCP 서버에 주주총회 관련 tool 등록"""

    @mcp.tool()
    async def agm_search(
        ticker: str,
        bgn_de: str = "",
        end_de: str = "",
    ) -> str:
        """desc: 주주총회 소집공고 검색. rcept_no 리스트 반환.
        when: [tier-3 Search] 특정 기업의 주총 공고를 찾을 때. 다른 agm_* tool에 필요한 rcept_no를 여기서 획득. corp_identifier 실행 후 호출할 것.
        rule: ticker 또는 종목코드로 검색. 정정공고 포함, 최신 정정본에 ← 최신 표시.
        ref: corp_identifier, agm, agm_agenda_xml, agm_manual

        Args:
            ticker: 종목코드 또는 회사명
            bgn_de: 검색 시작일 (YYYYMMDD). 미입력 시 올해 1월 1일
            end_de: 검색 종료일 (YYYYMMDD). 미입력 시 오늘
        """
        if not bgn_de:
            bgn_de = f"{datetime.now().year}0101"
        if not end_de:
            end_de = datetime.now().strftime("%Y%m%d")

        client = get_dart_client()
        result = await client.search_filings_by_ticker(
            ticker=ticker,
            bgn_de=bgn_de,
            end_de=end_de,
            pblntf_ty="E",
        )

        corp_info = result.get("corp_info", {})
        filings = [
            item for item in result.get("list", [])
            if "소집" in item.get("report_nm", "")
        ]

        if not filings:
            return f"{corp_info.get('corp_name', ticker)}의 주주총회 소집공고가 없습니다. (검색기간: {bgn_de}~{end_de})"

        # 원본/정정 관계 태깅 — 날짜순 정렬
        filings.sort(key=lambda x: x.get("rcept_dt", ""))
        has_correction = any("정정" in f.get("report_nm", "") for f in filings)

        lines = [
            f"## {corp_info.get('corp_name', '')} ({corp_info.get('stock_code', '')}) 주주총회 소집공고",
            f"검색기간: {bgn_de} ~ {end_de}",
            f"총 {len(filings)}건",
            "",
        ]

        for i, item in enumerate(filings):
            report_nm = item["report_nm"]
            if "정정" in report_nm:
                tag = "[정정]"
            elif has_correction:
                tag = "[원본]"
            else:
                tag = ""

            line = f"- {tag} **{report_nm}** | 접수일: {item['rcept_dt']} | 접수번호: {item['rcept_no']}"
            if i == len(filings) - 1 and has_correction:
                line += " ← 최신"
            lines.append(line)

        return "\n".join(lines)

    @mcp.tool()
    async def agm_agenda_xml(
        rcept_no: str,
        use_llm: bool = False,
        max_fallback_length: int = 3000,
        format: str = "md",
    ) -> str:
        """desc: 의안(안건) 목록 구조화. 제N호/제N-M호 형식의 트리.
        when: [tier-5 Detail] agm_pre_analysis 실행 후 사용자가 안건 상세를 명시적으로 요청했을 때만 사용. agm_pre_analysis 없이 단독 호출하지 말 것.
        rule: XML 파싱. 불완전 시 agm_parse_fallback(parser="agenda", tier="pdf") fallback. 판정 기준은 agm_manual 참조.
        ref: agm_parse_fallback, agm_items, agm_manual

        Args:
            rcept_no: 접수번호 (예: 20260225000123)
            use_llm: True면 파싱 실패/의심 시 LLM fallback 사용 (기본: False)
            max_fallback_length: LLM fallback 시 원문 최대 글자 수 (기본 3000, 0이면 제한 없음)
            format: 반환 형식. "md" (마크다운, 기본) 또는 "json" (프론트엔드용)
        """
        doc = await _get_document_cached(rcept_no)
        text = doc["text"]
        html = doc.get("html", "")
        agenda = parse_agenda_xml(text, html=html)
        parse_method = "bs4+regex" if html else "regex"

        if not validate_agenda_result(agenda) and use_llm:
            import re
            section = _extract_notice_section(text)
            zone = _extract_agenda_zone(section) if section else None

            if not zone:
                logger.error(f"[HARD FAIL] section/zone 추출 실패: {rcept_no}")
                return "안건을 파싱할 수 없습니다. (문서에서 안건 영역을 찾을 수 없음)"

            logger.warning(f"[SOFT FAIL] 정규식 파싱 의심 — LLM fallback 시도: {rcept_no}")
            zone_clean = re.sub(r'\n+', ' ', zone)
            if max_fallback_length > 0:
                zone_clean = zone_clean[:max_fallback_length]
            agenda = await extract_agenda_with_llm(zone_clean)
            parse_method = "llm"

            if not validate_agenda_result(agenda):
                logger.error(f"[HARD FAIL] LLM fallback도 실패: {rcept_no}")
                return "안건을 파싱할 수 없습니다. (정규식 + LLM 모두 실패)"

        if format == "json":
            meeting_info = parse_meeting_info_xml(text)
            result = _build_agenda_json(rcept_no, agenda, meeting_info, parse_method=parse_method)
            return json.dumps(result, ensure_ascii=False, indent=2)

        return _format_agenda_tree(agenda)

    @mcp.tool()
    async def agm_items(
        rcept_no: str,
        agenda_no: str = "",
        use_llm: bool = False,
        max_fallback_length: int = 3000,
        format: str = "md",
    ) -> str:
        """desc: 안건별 상세 원문 블록 (마크다운). 특화 파서 없는 안건의 raw 내용.
        when: [tier-5 Detail] agm_pre_analysis 실행 후 특정 안건의 원문이 필요할 때만 사용.
        rule: 특화 파서(financials, personnel 등)가 있는 안건은 해당 파서 사용이 더 정확.
        ref: agm_financials_xml, agm_personnel_xml, agm_aoi_change_xml, agm_compensation_xml

        Args:
            rcept_no: 접수번호 (예: 20260225000123)
            agenda_no: 안건 번호 (예: "1", "2"). 미입력 시 전체 안건 반환.
                       "2" 입력 시 제2호 + 하위(제2-1호~) 전체 반환.
            use_llm: True면 파싱 실패 시 LLM fallback 사용 (기본: False)
            max_fallback_length: LLM fallback 시 원문 최대 글자 수 (기본 3000, 0이면 제한 없음)
            format: 반환 형식. "md" (마크다운, 기본) 또는 "json"
        """
        doc = await _get_document_cached(rcept_no)
        html = doc.get("html", "")
        text = doc["text"]

        if not html:
            return "안건 상세를 파싱할 수 없습니다. (HTML 없음)"

        details = parse_agenda_details_xml(html)

        if not validate_agenda_details(details):
            if use_llm:
                logger.warning(f"[SOFT FAIL] 안건 상세 파싱 실패 — LLM fallback: {rcept_no}")
                # 목적사항별 기재사항 영역 원문 추출
                fallback_text = ""
                match = re.search(r'목적사항별\s*기재사항', text)
                if match:
                    if max_fallback_length > 0:
                        fallback_text = text[match.start():match.start()+max_fallback_length]
                    else:
                        fallback_text = text[match.start():]
                    return f"[LLM fallback] 안건 상세 파싱에 실패하여 원문을 반환합니다:\n\n{fallback_text}"

                logger.error(f"[HARD FAIL] 목적사항별 기재사항 영역도 찾을 수 없음: {rcept_no}")
            return "안건 상세를 파싱할 수 없습니다. (목적사항별 기재사항 섹션을 찾을 수 없음)"

        # agenda_no 필터
        if agenda_no:
            filtered = [
                d for d in details
                if d["number"].startswith(f"제{agenda_no}호")
                or d["number"].startswith(f"제{agenda_no}-")
            ]
            if not filtered:
                return f"제{agenda_no}호 안건을 찾을 수 없습니다."
            details = filtered

        if format == "json":
            return json.dumps(details, ensure_ascii=False, indent=2)

        return _format_agenda_details(details)

    @mcp.tool()
    async def agm_financials_xml(
        rcept_no: str,
        use_llm: bool = False,
        max_fallback_length: int = 3000,
        format: str = "md",
    ) -> str:
        """desc: 재무제표 (BS/IS) 구조화. 연결/별도, 당기/전기 비교.
        when: [tier-5 Detail] agm_pre_analysis 실행 후 사용자가 재무제표 상세를 명시적으로 요청했을 때만 사용.
        rule: XML 파싱. 불완전 시 agm_parse_fallback(parser="financials", tier="pdf") fallback. 판정 기준은 agm_manual 참조.
        ref: agm_parse_fallback, agm_manual

        Args:
            rcept_no: 접수번호 (예: 20260225000123)
            use_llm: True면 파싱 실패 시 LLM fallback 사용 (기본: False)
            max_fallback_length: LLM fallback 시 원문 최대 글자 수 (기본 3000, 0이면 제한 없음)
            format: 반환 형식. "json" (기본, 구조화) 또는 "md" (마크다운 테이블)
        """
        doc = await _get_document_cached(rcept_no)
        html = doc.get("html", "")
        if not html:
            return "재무제표를 파싱할 수 없습니다. (HTML 없음)"

        # 체이닝: agm_items 로직으로 재무제표 안건 존재 여부 판단
        details = parse_agenda_details_xml(html)
        fs_found = False
        if details:
            for d in details:
                title = d.get("title", "")
                if any(kw in title for kw in ["재무제표", "재무상태표", "대차대조표"]):
                    fs_found = True
                    logger.info(f"재무제표 안건 확인: {d['number']} — {title[:40]}")
                    break

        # 재무제표 테이블 정규화 (HTML 직접 파싱)
        result = parse_financials_xml(html)

        # 빈 결과 체크 — 안건 트리에서 이유 파악
        has_data = any(
            result[scope][stmt] is not None
            for scope in ["consolidated", "separate"]
            for stmt in ["balance_sheet", "income_statement"]
        )
        if not has_data:
            text = doc["text"]
            agenda = parse_agenda_xml(text, html=html)
            info = parse_meeting_info_xml(text, html=html)

            # 재무제표 승인 안건 존재 여부
            fs_agenda = [a for a in agenda if any(
                kw in a.get("title", "") for kw in ["재무제표", "재무상태표", "대차대조표"]
            )]
            # 철회/보고 전환 여부 — 안건 제목 또는 문서 전체에서 감지
            fs_withdrawn = [a for a in agenda if any(
                kw in a.get("title", "") for kw in ["철회", "보고사항으로 변경", "보고안건"]
            )]
            # 이사회 승인 → 보고 전환 패턴 (정정공고에서 흔함)
            if not fs_withdrawn and re.search(
                r'이사회에서\s*승인|이사회\s*승인에\s*따라|보고사항\s*으로\s*변경',
                text[:3000]
            ):
                fs_withdrawn = True
            # 보고사항에 재무제표/감사 관련 항목
            report_items = info.get("report_items", [])
            fs_in_report = [r for r in report_items if any(
                kw in r for kw in ["재무", "감사", "결산", "승인보고"]
            )]

            if fs_withdrawn:
                return "재무제표 승인 안건이 철회되어 보고사항으로 전환되었습니다."
            elif not fs_agenda and fs_in_report:
                return f"재무제표 승인 안건이 없고, 보고사항으로만 존재합니다. (보고: {', '.join(fs_in_report)})"
            elif not fs_agenda:
                return "이번 주주총회에 재무제표 승인 안건이 없습니다."
            else:
                if use_llm:
                    logger.warning(f"[SOFT FAIL] 재무제표 파싱 실패 — LLM fallback 시도: {rcept_no}")
                    try:
                        from open_proxy_mcp.llm.client import extract_agenda_with_llm
                        # 재무제표 영역 텍스트를 LLM에 전달
                        fs_text = ""
                        for a in fs_agenda:
                            fs_text += f"{a.get('title', '')}\n"
                        # 본문에서 재무 관련 부분 추출
                        fs_match = re.search(r'재무상태표|대차대조표', text)
                        if fs_match:
                            if max_fallback_length > 0:
                                fs_text = text[fs_match.start():fs_match.start()+max_fallback_length]
                            else:
                                fs_text = text[fs_match.start():]
                        return f"[LLM fallback] 재무제표 파싱에 실패하여 원문을 반환합니다:\n\n{fs_text}"
                    except Exception as e:
                        logger.error(f"[HARD FAIL] LLM fallback도 실패: {rcept_no} — {e}")
                return "재무제표 승인 안건이 있으나 파싱에 실패했습니다. (비표준 문서 구조)"

        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)

        return _format_financial_statements(result)

    @mcp.tool()
    async def agm_corrections(
        rcept_no: str,
        format: str = "md",
    ) -> str:
        """desc: 정정공고의 전/후 비교 + 정정 사유.
        when: [tier-5 Detail] 정정 전후 차이를 볼 때. 정정공고가 아닌 경우 빈 결과는 정상.
        rule: 정정 사유가 중요 - 재무수치 변경인지 단순 오타인지 구분.
        ref: agm_search, agm_manual

        Args:
            rcept_no: 접수번호 (예: 20260225000123)
            format: 반환 형식. "md" (마크다운, 기본) 또는 "json"
        """
        doc = await _get_document_cached(rcept_no)
        html = doc.get("html", "")
        if not html:
            return "정정 사항을 확인할 수 없습니다. (HTML 없음)"

        result = parse_corrections_xml(html)
        if not result:
            return "정정공고가 아닙니다. (정정 사항 없음)"

        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)

        return _format_correction_details(result)

    @mcp.tool()
    async def agm_personnel_xml(
        rcept_no: str,
        format: str = "md",
    ) -> str:
        """desc: 이사/감사 선임/해임 정보. 후보자별 경력, 결격사유, 추천사유, 직무수행계획.
        when: [tier-5 Detail] agm_pre_analysis 실행 후 사용자가 이사/감사 후보자 경력 상세를 요청했을 때만 사용.
        rule: XML 파싱. 경력 병합(100자+) 시 agm_parse_fallback(parser="personnel", tier="pdf") fallback. 판정 기준은 agm_manual 참조.
        ref: agm_parse_fallback, agm_manual, agm_result, news_check

        Args:
            rcept_no: 접수번호 (예: 20260225000123)
            format: 반환 형식. "md" (마크다운, 기본) 또는 "json"
        """
        doc = await _get_document_cached(rcept_no)
        html = doc.get("html", "")
        if not html:
            return "인사 정보를 파싱할 수 없습니다. (HTML 없음)"

        result = parse_personnel_xml(html)

        if not result.get("appointments"):
            return "선임/해임 안건이 없습니다."

        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)

        return _format_personnel(result)

    @mcp.tool()
    async def agm_aoi_change_xml(
        rcept_no: str,
        format: str = "md",
    ) -> str:
        """desc: 정관변경 비교 (변경전/변경후/사유). 세부의안별 분리.
        when: [tier-5 Detail] agm_pre_analysis 실행 후 사용자가 정관변경 상세를 요청했을 때만 사용.
        rule: XML 파싱. 불완전 시 agm_parse_fallback(parser="aoi_change", tier="pdf") fallback. 판정 기준은 agm_manual 참조.
        ref: agm_parse_fallback, agm_manual

        Args:
            rcept_no: 접수번호 (예: 20260225000123)
            format: 반환 형식. "md" (마크다운, 기본) 또는 "json"
        """
        doc = await _get_document_cached(rcept_no)
        html = doc.get("html", "")
        text = doc["text"]
        if not html:
            return "정관변경 사항을 파싱할 수 없습니다. (HTML 없음)"

        # 세부의안 목록 확보 (agm_agenda 체이닝)
        agenda = parse_agenda_xml(text, html=html)
        charter_subs = []
        for item in agenda:
            if "정관" in item.get("title", ""):
                charter_subs = item.get("children", [])
                break

        result = parse_aoi_xml(html, sub_agendas=charter_subs if charter_subs else None)

        if not result.get("amendments"):
            return "정관변경 안건이 없습니다."

        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)

        return _format_aoi_change(result)

    @mcp.tool()
    async def agm_compensation_xml(
        rcept_no: str,
        format: str = "md",
    ) -> str:
        """desc: 이사/감사 보수한도. 당기 한도, 전기 실지급, 이사 수, 소진율.
        when: [tier-5 Detail] agm_pre_analysis 실행 후 사용자가 보수한도/소진율 상세를 요청했을 때만 사용.
        rule: XML 파싱. 불완전 시 agm_parse_fallback(parser="compensation", tier="pdf") fallback. 판정 기준은 agm_manual 참조.
        ref: agm_parse_fallback, agm_manual, div_detail

        Args:
            rcept_no: 접수번호 (예: 20260225000123)
            format: 반환 형식. "md" (마크다운, 기본) 또는 "json"
        """
        doc = await _get_document_cached(rcept_no)
        html = doc.get("html", "")
        if not html:
            return "보수한도 정보를 파싱할 수 없습니다. (HTML 없음)"

        result = parse_compensation_xml(html)

        if not result.get("items"):
            return "보수한도 승인 안건이 없습니다."

        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)

        return _format_compensation(result)

    @mcp.tool()
    async def agm_treasury_share_xml(
        rcept_no: str,
        format: str = "md",
    ) -> str:
        """desc: 자기주식 보유/처분/소각. 수량, 목적, 방법.
        when: [tier-5 Detail] agm_pre_analysis 실행 후 사용자가 자사주 안건 상세를 요청했을 때만 사용.
        rule: XML 파싱. 안건 제목 매칭 한계로 PDF fallback 빈번. 판정 기준은 agm_manual 참조.
        ref: agm_parse_fallback, own_treasury, agm_manual

        Args:
            rcept_no: 접수번호 (예: 20260225000123)
            format: 반환 형식. "md" (마크다운, 기본) 또는 "json"
        """
        doc = await _get_document_cached(rcept_no)
        html = doc.get("html", "")
        if not html:
            return "자기주식 정보를 파싱할 수 없습니다. (HTML 없음)"

        result = parse_treasury_share_xml(html)

        if not result.get("items"):
            return "자기주식 관련 안건이 없습니다."

        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)

        return _format_treasury_share(result)

    @mcp.tool()
    async def agm_capital_reserve_xml(
        rcept_no: str,
        format: str = "md",
    ) -> str:
        """desc: 자본준비금 감소/이익잉여금 전입. 감액배당 전제 조건.
        when: [tier-5 Detail] agm_pre_analysis 실행 후 사용자가 자본준비금 안건 상세를 요청했을 때만 사용.
        rule: XML 파싱. 불완전 시 agm_parse_fallback(parser="capital_reserve", tier="pdf") fallback. 판정 기준은 agm_manual 참조.
        ref: agm_parse_fallback, agm_manual

        Args:
            rcept_no: 접수번호 (예: 20260225000123)
            format: 반환 형식. "md" (마크다운, 기본) 또는 "json"
        """
        doc = await _get_document_cached(rcept_no)
        html = doc.get("html", "")
        if not html:
            return "자본준비금 정보를 파싱할 수 없습니다. (HTML 없음)"

        result = parse_capital_reserve_xml(html)

        if not result.get("items"):
            return "자본준비금 관련 안건이 없습니다."

        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)

        return _format_capital_reserve(result)

    @mcp.tool()
    async def agm_retirement_pay_xml(
        rcept_no: str,
        format: str = "md",
    ) -> str:
        """desc: 임원 퇴직금 규정 개정 (변경전/변경후).
        when: [tier-5 Detail] agm_pre_analysis 실행 후 사용자가 퇴직금 규정 상세를 요청했을 때만 사용.
        rule: XML 파싱. 불완전 시 agm_parse_fallback(parser="retirement_pay", tier="pdf") fallback. 재무제표 주석의 "퇴직급여"와 혼동 주의. 판정 기준은 agm_manual 참조.
        ref: agm_parse_fallback, agm_manual

        Args:
            rcept_no: 접수번호 (예: 20260225000123)
            format: 반환 형식. "md" (마크다운, 기본) 또는 "json"
        """
        doc = await _get_document_cached(rcept_no)
        html = doc.get("html", "")
        if not html:
            return "퇴직금 규정 정보를 파싱할 수 없습니다. (HTML 없음)"

        result = parse_retirement_pay_xml(html)

        if not result.get("amendments"):
            return "퇴직금 규정 관련 안건이 없습니다."

        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)

        return _format_retirement_pay(result)

    @mcp.tool()
    async def agm_pre_analysis(
        ticker: str,
        bgn_de: str = "",
        end_de: str = "",
    ) -> str:
        """desc: 주총 사전 분석 — 소집공고 기반 안건/재무/인사 요약. 투표결과 미포함.
        when: [tier-4 Orchestrate] 주총 전 또는 소집공고만 있을 때. 안건 트리 + 재무 하이라이트 + 후보자 요약.
        rule: 소집공고(DART) 기반. 투표결과 포함 분석은 agm_post_analysis 사용. 이 tool 하나로 충분하며 agm_*_xml 개별 tool은 사용자의 명시적 요청 없는 한 추가 호출 금지.
        ref: corp_identifier, agm_search, agm_agenda_xml, agm_financials_xml, agm_post_analysis

        Args:
            ticker: 종목코드 또는 회사명
            bgn_de: 검색 시작일 (YYYYMMDD). 미입력 시 올해 1월 1일
            end_de: 검색 종료일 (YYYYMMDD). 미입력 시 오늘
        """
        if not bgn_de:
            bgn_de = f"{datetime.now().year}0101"
        if not end_de:
            end_de = datetime.now().strftime("%Y%m%d")

        client = get_dart_client()
        result = await client.search_filings_by_ticker(
            ticker=ticker, bgn_de=bgn_de, end_de=end_de, pblntf_ty="E",
        )

        corp_info = result.get("corp_info", {})
        filings = [
            item for item in result.get("list", [])
            if "소집" in item.get("report_nm", "")
        ]

        if not filings:
            return f"{corp_info.get('corp_name', ticker)}의 주주총회 소집공고가 없습니다."

        # 최신 공시 선택
        filings.sort(key=lambda x: x.get("rcept_dt", ""))
        latest = filings[-1]
        rcept_no = latest["rcept_no"]

        doc = await _get_document_cached(rcept_no)
        text = doc["text"]
        html = doc.get("html", "")

        # 1. 회의 정보
        info = parse_meeting_info_xml(text, html=html)

        # 2. 안건 트리
        agenda = parse_agenda_xml(text, html=html)

        # 3. 정정 요약
        correction = parse_corrections_xml(html) if html else None
        if correction:
            info["correction_summary"] = {
                "date": correction.get("date"),
                "original_date": correction.get("original_date"),
                "items": [
                    {"section": i["section"][:60], "reason": i["reason"][:80]}
                    for i in correction.get("items", [])
                ],
            }

        # 4. 재무 하이라이트
        fs = parse_financials_xml(html) if html else None
        fs_highlight = _build_financial_highlight(fs) if fs else None

        # 5. 인사 하이라이트
        personnel = parse_personnel_xml(html) if html else None
        personnel_summary = personnel.get("summary") if personnel else None

        # 포매팅
        corp_name = corp_info.get('corp_name', ticker)
        lines = [
            f"# {corp_name} 주주총회 Steward Report",
            "",
        ]

        # 공시 이력
        if len(filings) > 1:
            lines.append(f"*공시 {len(filings)}건 중 최신 사용 (접수번호: {rcept_no})*")
            lines.append("")

        # 회의 정보
        lines.append(_format_meeting_info(info))
        lines.append("")

        # 안건 트리
        lines.append(_format_agenda_tree(agenda))
        lines.append("")

        # 재무 하이라이트
        if fs_highlight:
            lines.append("## 재무 하이라이트")
            lines.append("")
            for item in fs_highlight:
                lines.append(f"- **{item['label']}**: {item['value']}")
            lines.append("")

        # 인사 하이라이트
        if personnel_summary and personnel_summary.get("total_appointments", 0) > 0:
            lines.append("## 인사 현황")
            lines.append("")
            parts = []
            if personnel_summary.get("directors"): parts.append(f"이사 {personnel_summary['directors']}명")
            if personnel_summary.get("outside_directors"): parts.append(f"사외이사 {personnel_summary['outside_directors']}명")
            if personnel_summary.get("auditors"): parts.append(f"감사 {personnel_summary['auditors']}명")
            if personnel_summary.get("audit_committee"): parts.append(f"감사위원회 {personnel_summary['audit_committee']}명")
            if personnel_summary.get("dismissals"): parts.append(f"해임 {personnel_summary['dismissals']}명")
            if parts:
                lines.append(f"- **선임/해임**: {', '.join(parts)}")
            # 후보자 이름 나열
            names = []
            for a in personnel.get("appointments", []):
                for c in a.get("candidates", []):
                    names.append(f"{c.get('name','?')}({a['category']})")
            if names:
                lines.append(f"- **후보자**: {', '.join(names)}")
            lines.append("")

        return "\n".join(lines)

    @mcp.tool()
    async def agm_post_analysis(
        ticker: str,
        bgn_de: str = "",
        end_de: str = "",
    ) -> str:
        """desc: 주총 사후 분석 — 소집공고(안건/재무/인사) + 투표결과(가결/부결/참석률) 통합.
        when: [tier-4 Orchestrate] 주총 종료 후 전체 분석. 사전+사후 완전한 주총 그림이 필요할 때.
        rule: agm_pre_analysis + agm_result 체이닝. 주총 미종료 시 투표결과 없음 안내. 이 tool 하나로 소집공고+투표결과 분석이 완성됨. agm_*_xml 개별 tool 추가 호출 금지.
        ref: corp_identifier, agm_pre_analysis, agm_result, agm_search
        """
        pre = await agm_pre_analysis(ticker=ticker, bgn_de=bgn_de, end_de=end_de)
        result = await agm_result(ticker=ticker, bgn_de=bgn_de, end_de=end_de)
        return f"{pre}\n\n---\n\n## 투표 결과\n\n{result}"

    # ── PDF/OCR fallback (unified) ──

    PARSER_DISPATCH = {
        "personnel": {
            "pdf_parser": parse_personnel_pdf,
            "ocr_key": "pers",
            "formatter": _format_personnel,
            "empty_check": lambda r: not r.get("appointments"),
            "empty_msg": "선임/해임 안건",
        },
        "financials": {
            "pdf_parser": parse_financials_pdf,
            "ocr_key": "fin",
            "formatter": _format_financial_statements,
            "empty_check": lambda r: not any(
                r[s][t] is not None
                for s in ["consolidated", "separate"]
                for t in ["balance_sheet", "income_statement"]
            ),
            "empty_msg": "재무제표",
        },
        "aoi_change": {
            "pdf_parser": parse_aoi_pdf,
            "ocr_key": "aoi",
            "formatter": _format_aoi_change,
            "empty_check": lambda r: not r.get("amendments"),
            "empty_msg": "정관변경 사항",
        },
        "compensation": {
            "pdf_parser": parse_compensation_pdf,
            "ocr_key": "comp",
            "formatter": _format_compensation,
            "empty_check": lambda r: not r.get("items"),
            "empty_msg": "보수한도 안건",
        },
        "treasury_share": {
            "pdf_parser": parse_treasury_share_pdf,
            "ocr_key": "treasury",
            "formatter": _format_treasury_share,
            "empty_check": lambda r: not r.get("items"),
            "empty_msg": "자기주식 안건",
        },
        "capital_reserve": {
            "pdf_parser": parse_capital_reserve_pdf,
            "ocr_key": "capital",
            "formatter": _format_capital_reserve,
            "empty_check": lambda r: not r.get("items"),
            "empty_msg": "자본준비금 안건",
        },
        "retirement_pay": {
            "pdf_parser": parse_retirement_pay_pdf,
            "ocr_key": "retirement",
            "formatter": _format_retirement_pay,
            "empty_check": lambda r: not r.get("amendments"),
            "empty_msg": "퇴직금 규정",
        },
        "agenda": {
            "pdf_parser": parse_agenda_pdf,
            "ocr_key": "agenda",
            "formatter": _format_agenda_tree,
            "empty_check": lambda r: not r,
            "empty_msg": "안건",
        },
    }

    @mcp.tool()
    async def agm_parse_fallback(
        rcept_no: str,
        parser: str,
        tier: str = "pdf",
        format: str = "md",
    ) -> str:
        """desc: AGM 파서 PDF/OCR fallback. XML 파싱 불완전 시 대체 수단.
        when: [tier-5 Detail] agm_*_xml 결과가 불완전할 때만 사용. tier="pdf"(4s+) 또는 tier="ocr"(UPSTAGE_API_KEY 필요).
        rule: parser 파라미터로 파서 선택 (personnel/financials/aoi_change/compensation/treasury_share/capital_reserve/retirement_pay/agenda). AI가 자체 보정 실패 후 유저에게 제안.
        ref: agm_personnel_xml, agm_financials_xml, agm_manual

        Args:
            rcept_no: 접수번호
            parser: 파서 이름 (personnel, financials, aoi_change, compensation, treasury_share, capital_reserve, retirement_pay, agenda)
            tier: "pdf" (기본) 또는 "ocr"
            format: "md" (기본) 또는 "json"
        """
        config = PARSER_DISPATCH.get(parser)
        if not config:
            return f"알 수 없는 파서: {parser}. 가능한 값: {', '.join(PARSER_DISPATCH.keys())}"

        pdf_bytes = await _get_pdf_cached(rcept_no)
        md_text = _get_pdf_markdown_cached(rcept_no, pdf_bytes)

        if tier == "pdf":
            if not md_text:
                return "PDF 파싱에 실패했습니다."
            result = config["pdf_parser"](md_text)
            if config["empty_check"](result):
                return f"PDF에서도 {config['empty_msg']}을 찾을 수 없습니다. tier='ocr'로 재시도 가능."
        elif tier == "ocr":
            result = ocr_fallback_for_parser(
                pdf_bytes, md_text, config["ocr_key"],
                config["pdf_parser"], f"{rcept_no}.pdf",
            )
            if not result or config["empty_check"](result):
                return f"OCR로도 {config['empty_msg']}을 추출할 수 없습니다."
        else:
            return f"알 수 없는 tier: {tier}. 'pdf' 또는 'ocr' 사용."

        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)
        return config["formatter"](result)

    @mcp.tool()
    async def agm_result(
        ticker: str,
        bgn_de: str = "",
        end_de: str = "",
        format: str = "md",
    ) -> str:
        """desc: 주주총회 투표결과 -- 안건별 가결/부결, 찬성률(발행/행사 기준), 추정참석률.
        when: [tier-5 Detail] 주총 결과를 볼 때. agm_post_analysis가 이미 포함하므로 단독 호출은 투표결과만 필요할 때로 한정.
        rule: KIND 크롤링 기반. rcept_no "80"->"00" 변환으로 KIND viewer 접근. 주총 미종료 시 데이터 없음.
        ref: agm_search, agm_manual, own_block, own_major

        Args:
            ticker: 종목코드 또는 회사명
            bgn_de: 검색 시작일 YYYYMMDD (미입력 시 올해 1월 1일)
            end_de: 검색 종료일 YYYYMMDD (미입력 시 오늘)
            format: "md" (기본) 또는 "json"
        """
        client = get_dart_client()
        corp = await client.lookup_corp_code(ticker)
        if not corp:
            return tool_not_found("기업", ticker)

        corp_code = corp["corp_code"]
        corp_name = corp.get("corp_name", ticker)

        if not bgn_de:
            bgn_de = f"{datetime.now().year}0101"
        if not end_de:
            end_de = datetime.now().strftime("%Y%m%d")

        # 1. DART 공시검색 — 거래소 수시공시(I)에서 "주주총회결과" 찾기
        try:
            filings = await client.search_filings(
                bgn_de=bgn_de, end_de=end_de,
                corp_code=corp_code, pblntf_ty="I",
            )
        except DartClientError as e:
            return tool_error("공시 검색", e, ticker=ticker)

        result_filing = None
        for item in filings.get("list", []):
            if "주주총회결과" in item.get("report_nm", ""):
                result_filing = item
                break

        if not result_filing:
            return f"{corp_name}의 주주총회결과 공시를 찾을 수 없습니다 ({bgn_de}-{end_de})."

        rcept_no = result_filing["rcept_no"]
        rcept_dt = result_filing.get("rcept_dt", "")

        # 2. KIND 크롤링 — rcept_no의 "80" → "00"으로 acptno 변환
        acptno = rcept_no.replace("80", "00", 1) if "80" in rcept_no[8:12] else rcept_no
        try:
            html = await client.kind_fetch_document(acptno)
        except DartClientError:
            # fallback: 원본 rcept_no로 시도
            try:
                html = await client.kind_fetch_document(rcept_no)
            except DartClientError:
                return f"KIND에서 주주총회결과 본문을 가져올 수 없습니다 (rcept_no={rcept_no})."

        # 3. 투표 결과 테이블 파싱
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        vote_results = _parse_agm_result_table(soup)

        if not vote_results:
            return f"주주총회결과에서 투표 결과 테이블을 찾을 수 없습니다."

        result_data = {
            "corp_name": corp_name,
            "rcept_no": rcept_no,
            "rcept_dt": rcept_dt,
            "items": vote_results,
        }

        if format == "json":
            return json.dumps(result_data, ensure_ascii=False, indent=2)
        return _format_agm_result(result_data)


