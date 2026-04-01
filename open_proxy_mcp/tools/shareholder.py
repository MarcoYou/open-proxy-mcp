"""주주총회 소집공고 관련 MCP tools"""

import os
import json
import logging
import re
import glob
import tempfile
from datetime import datetime


def format_krw(raw_value: str, unit: str = "") -> str:
    """숫자 문자열 + 단위를 사람이 읽기 좋은 한국 원화 형태로 변환

    Args:
        raw_value: "3,049,040" 또는 "(477,528)" 등
        unit: "백만원", "천원", "원" 등

    Returns:
        "약 3.05조원", "약 4,775억원", "3,000만원" 등
        변환 실패 시 원본 반환
    """
    if not raw_value or raw_value.strip() in ('', '-'):
        return raw_value

    # 음수 감지
    is_negative = '(' in raw_value or '-' in raw_value.replace(',', '')
    # 숫자만 추출
    num_str = re.sub(r'[^\d]', '', raw_value)
    if not num_str:
        return raw_value

    value = int(num_str)

    # 단위 반영 → 원 단위로 변환
    unit_clean = re.sub(r'\s+', '', unit)
    if '백만' in unit_clean:
        value *= 1_000_000
    elif '천' in unit_clean:
        value *= 1_000
    # "원"이면 그대로

    if is_negative:
        value = -value

    return _format_won(value)


def _format_won(value: int) -> str:
    """원 단위 정수를 읽기 좋은 형태로"""
    abs_val = abs(value)
    sign = "-" if value < 0 else ""

    if abs_val >= 1_000_000_000_000:  # 조
        v = abs_val / 1_000_000_000_000
        if v >= 10:
            return f"약 {sign}{v:.1f}조원"
        return f"약 {sign}{v:.2f}조원"
    elif abs_val >= 100_000_000_000:  # 천억 이상
        v = abs_val / 100_000_000
        return f"약 {sign}{v:,.0f}억원"
    elif abs_val >= 100_000_000:  # 억
        v = abs_val / 100_000_000
        if v >= 10:
            return f"약 {sign}{v:.1f}억원"
        return f"약 {sign}{v:.2f}억원"
    elif abs_val >= 10_000_000:  # 천만
        v = abs_val / 10_000
        return f"{sign}{v:,.0f}만원"
    elif abs_val >= 10_000:  # 만
        v = abs_val / 10_000
        if v == int(v):
            return f"{sign}{int(v):,}만원"
        return f"{sign}{v:,.1f}만원"
    else:
        return f"{sign}{abs_val:,}원"
from open_proxy_mcp.dart.client import DartClient
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
    extract_structural_elements,
    get_agenda_contents,
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
    client = DartClient()
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

# ── 문서 캐시 (메모리 + 디스크) ──


_doc_cache: dict[str, dict] = {}
_MAX_CACHE = 30
_DISK_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "cache")


def _disk_cache_path(rcept_no: str) -> str:
    return os.path.join(_DISK_CACHE_DIR, f"{rcept_no}.json")


def _load_from_disk(rcept_no: str) -> dict | None:
    path = _disk_cache_path(rcept_no)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _save_to_disk(rcept_no: str, doc: dict):
    os.makedirs(_DISK_CACHE_DIR, exist_ok=True)
    path = _disk_cache_path(rcept_no)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)


async def _get_document_cached(rcept_no: str) -> dict:
    """get_document 결과를 캐싱하여 중복 API 호출 방지 (메모리 + 디스크)"""
    # 1. 메모리 캐시
    if rcept_no in _doc_cache:
        return _doc_cache[rcept_no]
    # 2. 디스크 캐시
    disk_doc = _load_from_disk(rcept_no)
    if disk_doc:
        if len(_doc_cache) >= _MAX_CACHE:
            _doc_cache.pop(next(iter(_doc_cache)))
        _doc_cache[rcept_no] = disk_doc
        return disk_doc
    # 3. API 호출
    client = DartClient()
    doc = await client.get_document(rcept_no)
    if len(_doc_cache) >= _MAX_CACHE:
        _doc_cache.pop(next(iter(_doc_cache)))
    _doc_cache[rcept_no] = doc
    _save_to_disk(rcept_no, doc)

    # 이미지 기반 공고 감지 — 소집공고 본문이 이미지에만 있는 경우
    images = doc.get("images", [])
    notice_images = [img for img in images if any(
        kw in img for kw in ["소집", "통지", "주총", "공고"]
    )]
    if notice_images:
        logger.warning(
            f"[IMAGE_NOTICE] 소집공고 본문이 이미지에 포함된 것으로 추정: "
            f"{rcept_no} | images={notice_images} — 텍스트 파싱 결과가 불완전할 수 있음"
        )

    return doc


# ── 마크다운 포매터 ──

def _format_agenda_tree(items: list[dict]) -> str:
    """안건 트리를 마크다운으로 포매팅"""
    if not items:
        return "안건을 파싱할 수 없습니다."

    # 루트 안건 수 (하위 제외)
    total = len(items)
    lines = [f"## 의안 목록 (총 {total}건)", ""]

    for item in items:
        lines.append(f"### {item['number']}: {item['title']}")
        meta = []
        if item.get("category"):
            meta.append(f"카테고리: {item['category']}")
        if item.get("source"):
            meta.append(f"출처: {item['source']}")
        if meta:
            lines.append(f"- {' | '.join(meta)}")
        if item.get("conditional"):
            lines.append(f"- ※ {item['conditional']}")

        # 하위 안건
        for child in item.get("children", []):
            lines.append(f"  - **{child['number']}**: {child['title']}")
            if child.get("source"):
                lines.append(f"    - 출처: {child['source']}")
            if child.get("conditional"):
                lines.append(f"    - ※ {child['conditional']}")
            # 3단계
            for gc in child.get("children", []):
                lines.append(f"    - **{gc['number']}**: {gc['title']}")
                if gc.get("conditional"):
                    lines.append(f"      - ※ {gc['conditional']}")

        lines.append("")

    return "\n".join(lines)


def _format_meeting_info(info: dict) -> str:
    """비안건 정보를 마크다운으로 포매팅"""
    lines = ["## 주주총회 개요", ""]

    # 유형
    type_str = ""
    if info.get("meeting_term"):
        type_str += info["meeting_term"] + " "
    if info.get("meeting_type"):
        type_str += info["meeting_type"] + "주주총회"
    if type_str:
        lines.append(f"- **유형**: {type_str}")

    if info.get("is_correction"):
        lines.append("- **정정공고**: 예")
        cs = info.get("correction_summary")
        if cs:
            if cs.get("date"):
                lines.append(f"- **정정일**: {cs['date']}")
            if cs.get("original_date"):
                lines.append(f"- **최초제출일**: {cs['original_date']}")
            if cs.get("items"):
                lines.append("- **정정 항목**:")
                for item in cs["items"]:
                    lines.append(f"  - {item['section']} — {item['reason']}")

    if info.get("datetime"):
        lines.append(f"- **일시**: {info['datetime']}")
    if info.get("location"):
        lines.append(f"- **장소**: {info['location']}")

    # 보고사항
    if info.get("report_items"):
        lines.append("")
        lines.append("## 보고사항")
        for item in info["report_items"]:
            lines.append(f"- {item}")

    # 전자투표
    if info.get("electronic_voting"):
        lines.append("")
        lines.append("## 전자투표")
        lines.append(info["electronic_voting"])

    # 의결권 행사
    if info.get("proxy_voting"):
        lines.append("")
        lines.append("## 의결권 행사 방법")
        lines.append(info["proxy_voting"])

    # 온라인 중계
    if info.get("online_broadcast"):
        lines.append("")
        lines.append("## 온라인 중계")
        lines.append(info["online_broadcast"])

    # 경영참고사항 비치
    if info.get("reference_materials"):
        lines.append("")
        lines.append("## 경영참고사항 비치")
        lines.append(info["reference_materials"])

    # 문서 목차
    if info.get("toc"):
        lines.append("")
        lines.append("## 문서 목차")
        for item in info["toc"]:
            lines.append(f"- {item}")

    return "\n".join(lines)


def _format_agenda_details(details: list[dict]) -> str:
    """안건 상세를 마크다운으로 포매팅"""
    lines = []
    for agenda in details:
        lines.append(f"## {agenda['number']}: {agenda['title']}")
        if agenda.get("category"):
            lines.append(f"*카테고리: {agenda['category']}*")
        lines.append("")

        for sec in agenda.get("sections", []):
            if sec.get("heading"):
                lines.append(f"### {sec['heading']}")
                lines.append("")

            for block in sec.get("blocks", []):
                content = block["content"]
                if block["type"] == "table":
                    lines.append(content)
                    lines.append("")
                elif block["type"] == "note":
                    lines.append(f"> {content}")
                    lines.append("")
                else:
                    lines.append(content)
                    lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


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
        """주주총회 소집공고를 검색합니다. rcept_no를 반환하며, 다른 agm_* tool에 필요합니다.

        종목코드(예: 033780) 또는 회사명(예: 케이티앤지)으로
        해당 기업의 주주총회 소집공고 목록을 반환합니다.

        Args:
            ticker: 종목코드 또는 회사명
            bgn_de: 검색 시작일 (YYYYMMDD). 미입력 시 올해 1월 1일
            end_de: 검색 종료일 (YYYYMMDD). 미입력 시 오늘
        """
        if not bgn_de:
            bgn_de = f"{datetime.now().year}0101"
        if not end_de:
            end_de = datetime.now().strftime("%Y%m%d")

        client = DartClient()
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
    async def agm_document(
        rcept_no: str,
        max_length: int = 10000,
    ) -> str:
        """주주총회 소집공고 전체 원문 텍스트를 반환합니다. 특정 안건만 보려면 agm_extract 사용.

        Args:
            rcept_no: 접수번호 (예: 20260225000123)
            max_length: 반환할 최대 글자 수 (기본 10000)
        """
        doc = await _get_document_cached(rcept_no)
        text = doc["text"]
        images = doc["images"]

        result_lines = []

        if images:
            result_lines.append(f"[첨부 이미지 {len(images)}개: {', '.join(images)}]")
            result_lines.append("")

        if len(text) > max_length:
            result_lines.append(text[:max_length])
            result_lines.append(f"\n... (전체 {len(text):,}자 중 {max_length:,}자 표시)")
        else:
            result_lines.append(text)

        return "\n".join(result_lines)

    @mcp.tool()
    async def agm_agenda_xml(
        rcept_no: str,
        use_llm: bool = False,
        max_fallback_length: int = 3000,
        format: str = "md",
    ) -> str:
        """주주총회 소집공고에서 의안(안건) 목록을 구조화하여 반환합니다.
        정상: 안건 1개+, 제목 2-150자, 제N호/제N-M호 형식.
        불완전하면 AI가 직접 보정 가능. 원문 확인은 agm_extract, 전체 문맥은 agm_items 사용.
        그래도 부족하면 agm_agenda_pdf -> agm_agenda_ocr -> AI가 원문 기반 재구성.
        agm_agenda_xml 결과의 안건 목록에 없는 안건 유형이면 빈 결과는 정상.

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
    async def agm_info(
        rcept_no: str,
    ) -> str:
        """주주총회 소집공고에서 의안을 제외한 회의 정보를 반환합니다.

        정기/임시 구분, 일시, 장소, 보고사항, 전자투표 안내,
        의결권 행사 방법, 온라인 중계 등 비안건 정보를 반환합니다.

        Args:
            rcept_no: 접수번호 (예: 20260225000123)
        """
        doc = await _get_document_cached(rcept_no)
        info = parse_meeting_info_xml(doc["text"], html=doc.get("html", ""))

        # 정정공고 메타데이터 추가
        correction = parse_corrections_xml(doc.get("html", ""))
        if correction:
            info["correction_summary"] = {
                "date": correction.get("date"),
                "original_date": correction.get("original_date"),
                "items": [
                    {"section": item["section"][:60], "reason": item["reason"][:80]}
                    for item in correction.get("items", [])
                ],
            }

        return _format_meeting_info(info)

    @mcp.tool()
    async def agm_items(
        rcept_no: str,
        agenda_no: str = "",
        use_llm: bool = False,
        max_fallback_length: int = 3000,
        format: str = "md",
    ) -> str:
        """안건별 상세 내용 (raw 블록). 특화 파서(financials, personnel 등)가 없는 안건의 상세를 볼 때 사용.
        테이블은 마크다운, 텍스트는 그대로 반환. 구조화된 추출이 필요하면 agm_extract 사용.

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
        """주주총회 소집공고에서 재무제표를 구조화하여 반환합니다.
        정상: BS에 자산(유동/비유동), 부채(유동/비유동), 자본 구조 + 자산=부채+자본. IS에 매출, 매출원가, 매출총이익, 판관비, 영업이익, 당기순이익, 주당이익. 단위(천원/백만원/억원) 반드시 포함.
        불완전하면 AI가 직접 보정 가능. 원문 확인은 agm_extract, 전체 문맥은 agm_items 사용.
        그래도 부족하면 agm_financials_pdf -> agm_financials_ocr -> AI가 원문 기반 재구성.
        agm_agenda_xml 결과에 재무제표 안건이 없으면 빈 결과는 정상.

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
        """정정공고의 정정 전/후 비교 + 정정 사유. 정정공고가 아닌 경우 빈 결과는 정상.

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
        """주주총회 소집공고에서 이사/감사 선임/해임 정보를 반환합니다.
        정상: 후보자 이름(한글 2-5자, 영문 병기 가능), 경력(기간+내용 분리, 각 100자 이내), 결격사유, 추천사유.
        불완전: 경력 100자+ 한 줄 병합 -> AI가 직접 분리 시도 가능. 원문 확인은 agm_extract, 전체 문맥은 agm_items 사용.
        그래도 부족하면 agm_personnel_pdf -> agm_personnel_ocr -> AI가 원문 기반 재구성.
        agm_agenda_xml 결과에 선임/해임 안건이 없으면 빈 결과는 정상.

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
        """주주총회 소집공고에서 정관변경 사항을 반환합니다.
        정상: amendments 1건+, 변경전/변경후 조문 텍스트 존재. ------생략 표기는 정상(변경 없는 부분 생략). <삭제>도 정상.
        불완전하면 AI가 직접 보정 가능. 원문 확인은 agm_extract, 전체 문맥은 agm_items 사용.
        그래도 부족하면 agm_aoi_change_pdf -> agm_aoi_change_ocr -> AI가 원문 기반 재구성.
        agm_agenda_xml 결과에 정관변경 안건이 없으면 빈 결과는 정상.

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
        """주주총회 소집공고에서 이사/감사 보수한도 승인 정보를 반환합니다.
        정상: 당기 limitAmount > 0, headcount(이사수/사외이사수), 전기 실지급액+한도액. 소진율(전기지급/전기한도) 자동 계산.
        불완전: limitAmount 없음 -> AI가 원문에서 추출 시도 가능. 원문 확인은 agm_extract, 전체 문맥은 agm_items 사용.
        그래도 부족하면 agm_compensation_pdf -> agm_compensation_ocr -> AI가 원문 기반 재구성.
        agm_agenda_xml 결과에 보수한도 안건이 없으면 빈 결과는 정상.

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
        """주주총회 소집공고에서 자기주식 보유/처분/소각 정보를 반환합니다.
        정상: items 1개+, type(hold_dispose/cancel), 주식수/취득방법/보유기간 정보.
        불완전하면 AI가 직접 보정 가능. 원문 확인은 agm_extract, 전체 문맥은 agm_items 사용.
        그래도 부족하면 agm_treasury_share_pdf -> agm_treasury_share_ocr -> AI가 원문 기반 재구성.
        agm_agenda_xml 결과에 자기주식 안건이 없으면 빈 결과는 정상.

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
        """주주총회 소집공고에서 자본준비금 감소/이익잉여금 전입 정보를 반환합니다.
        정상: amount(감소 금액) 추출됨, reducedCapital=true. 자본준비금 감소는 감액배당(비과세 배당)의 전제 조건이지 확정이 아님.
        불완전하면 AI가 직접 보정 가능. 원문 확인은 agm_extract, 전체 문맥은 agm_items 사용.
        그래도 부족하면 agm_capital_reserve_pdf -> agm_capital_reserve_ocr -> AI가 원문 기반 재구성.
        agm_agenda_xml 결과에 자본준비금 안건이 없으면 빈 결과는 정상.

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
        """주주총회 소집공고에서 임원 퇴직금 규정 개정 정보를 반환합니다.
        정상: 현행/개정안 비교 1건+, 조항별 변경전/변경후 텍스트.
        불완전하면 AI가 직접 보정 가능. 원문 확인은 agm_extract, 전체 문맥은 agm_items 사용.
        그래도 부족하면 agm_retirement_pay_pdf -> agm_retirement_pay_ocr -> AI가 원문 기반 재구성.
        agm_agenda_xml 결과에 퇴직금 안건이 없으면 빈 결과는 정상.

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
    async def agm(
        ticker: str,
        bgn_de: str = "",
        end_de: str = "",
    ) -> str:
        """주주총회 소집공고 스마트 오케스트레이터. rcept_no 없이 종목코드만으로 한 번에 요약. 상세 분석은 개별 tool 사용.

        종목코드 또는 회사명으로 최신 소집공고를 찾아서
        회의정보, 안건 트리, 재무 하이라이트(자산총계/매출/당기순이익/배당),
        자사주 현황, 정정 사항을 한 번에 반환합니다.

        Args:
            ticker: 종목코드 또는 회사명
            bgn_de: 검색 시작일 (YYYYMMDD). 미입력 시 올해 1월 1일
            end_de: 검색 종료일 (YYYYMMDD). 미입력 시 오늘
        """
        if not bgn_de:
            bgn_de = f"{datetime.now().year}0101"
        if not end_de:
            end_de = datetime.now().strftime("%Y%m%d")

        client = DartClient()
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

    # ── 범용 추출 tool ──

    @mcp.tool()
    async def agm_extract(
        rcept_no: str,
        agenda_no: str = "",
    ) -> str:
        """안건별 원문(마크다운) + 핵심 수치(금액/인명/날짜/법령/비율/테이블) 추출.
        파서 없는 안건(스톡옵션, 주주제안 등)이나, _xml 결과가 이상할 때 원문 확인용.
        전체 문맥이 필요하면 agm_items 사용.

        Args:
            rcept_no: 접수번호
            agenda_no: 안건 번호 (예: "제3호"). 빈 문자열이면 전체 안건.
        """
        doc = await _get_document_cached(rcept_no)
        html = doc.get("html", "")
        if not html:
            return "문서를 파싱할 수 없습니다. (HTML 없음)"

        contents = get_agenda_contents(html, agenda_no=agenda_no)
        extracted = extract_structural_elements(html, agenda_no=agenda_no)

        lines = []

        # mdContents
        if contents.get("mdContents"):
            lines.append("## 원문")
            lines.append(contents["mdContents"][:3000])
            if len(contents["mdContents"]) > 3000:
                lines.append(f"\n... ({len(contents['mdContents'])}자 중 3000자 표시)")
            lines.append("")

        # extracted
        lines.append("## 핵심 데이터 추출")
        if extracted["amounts"]:
            lines.append(f"**금액**: {', '.join(extracted['amounts'][:10])}")
        if extracted["names"]:
            lines.append(f"**인명**: {', '.join(extracted['names'][:10])}")
        if extracted["dates"]:
            lines.append(f"**날짜**: {', '.join(extracted['dates'][:5])}")
        if extracted["legalRefs"]:
            lines.append(f"**법령**: {', '.join(extracted['legalRefs'][:5])}")
        if extracted["percentages"]:
            lines.append(f"**비율**: {', '.join(extracted['percentages'][:5])}")
        if extracted["tables"]:
            lines.append(f"**테이블**: {len(extracted['tables'])}개")
            for t in extracted["tables"][:3]:
                lines.append(f"  헤더: {t['headers'][:5]}")
                for row in t["rows"][:2]:
                    lines.append(f"  {[c[:20] for c in row[:5]]}")

        if not any([extracted["amounts"], extracted["names"], extracted["tables"]]):
            lines.append("추출된 데이터가 없습니다.")

        return "\n".join(lines)

    # ── PDF fallback tools ──

    @mcp.tool()
    async def agm_guide() -> str:
        """상세 판정 기준과 성공 예시 JSON. 판단이 어려울 때 호출하세요."""
        guide = """# OpenProxy MCP 사용 가이드

## Tool 구조

기본 tool (XML 파싱, 빠름):
  agm_search → agm_agenda_xml, agm_info, agm_items
  agm_financials_xml, agm_personnel_xml, agm_aoi_change_xml, agm_compensation_xml
  agm_treasury_share_xml, agm_capital_reserve_xml, agm_retirement_pay_xml
  agm (종합 오케스트레이터)

PDF fallback tool (XML 실패 시, 느림 4초+):
  agm_agenda_pdf, agm_financials_pdf, agm_personnel_pdf
  agm_aoi_change_pdf, agm_compensation_pdf
  agm_treasury_share_pdf, agm_capital_reserve_pdf, agm_retirement_pay_pdf

OCR fallback tool (PDF도 실패 시, 가장 느림, UPSTAGE_API_KEY 필요):
  agm_agenda_ocr, agm_financials_ocr, agm_personnel_ocr
  agm_aoi_change_ocr, agm_compensation_ocr
  agm_treasury_share_ocr, agm_capital_reserve_ocr, agm_retirement_pay_ocr

## 결과 검증 + Fallback 흐름

1. 기본 _xml tool 호출 (예: agm_personnel_xml)
2. **결과 검증**: 아래 Case Definitions의 성공 예시와 비교
   - 구조가 예시와 일치하는가? (필드 존재, 값 형태)
   - 내용이 사람이 보기에 말이 되는가? (이름이 실제 사람 이름인지, 숫자가 합리적인지)
   - 경력이 깔끔하게 분리되어 있는가? (100자+ 한 줄이면 병합 의심)
3. **검증 통과** → 사용자에게 답변
   - 단, 포맷이 예시와 다르면 당신(AI)이 직접 보정하여 제공
   - 예: 계정명 공백 정리 ("자          산" → "자산"), 단위 변환 표시 등
4. **불완전하면** → 사용자에게 "파싱이 불완전합니다. PDF로 재시도할까요?" 안내
5. 사용자 동의 → _pdf tool 호출 (예: agm_personnel_pdf)
6. 여전히 실패 → "OCR로 한번 더 시도해볼까요?" 안내
7. 사용자 동의 → _ocr tool 호출 (예: agm_personnel_ocr)

**중요**: 파서가 SUCCESS를 반환해도 당신이 직접 결과를 읽고 검증하세요.
Case Definitions의 성공 예시가 "이렇게 생겨야 한다"의 기준입니다.

## 주의사항
- _pdf tool은 DART 웹에서 PDF를 다운로드하므로 시간이 걸립니다 (4초+)
- _ocr tool은 Upstage API를 호출하므로 UPSTAGE_API_KEY가 필요합니다
- 해당 안건 자체가 없는 경우 빈 결과는 정상입니다 (예: 보수한도 안건이 없는 기업)

---

"""
        # CASE_DEFINITION.md 내용 포함 (성공 예시 + 판정 기준)
        case_def_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "CASE_DEFINITION.md"
        )
        try:
            with open(case_def_path, "r") as f:
                case_def = f.read()
            return guide + case_def
        except FileNotFoundError:
            return guide + "\n(CASE_DEFINITION.md를 찾을 수 없습니다)"

    @mcp.tool()
    async def agm_personnel_pdf(
        rcept_no: str,
        format: str = "md",
    ) -> str:
        """이사/감사 선임 정보를 PDF에서 파싱합니다 (PDF, 4초+).
        agm_personnel_xml 실패 시 사용. 정상 기준은 _xml과 동일. 여전히 실패하면 agm_personnel_ocr로 재시도.

        Args:
            rcept_no: 접수번호
            format: "md" (기본) 또는 "json"
        """
        pdf_bytes = await _get_pdf_cached(rcept_no)
        md_text = _get_pdf_markdown_cached(rcept_no, pdf_bytes)
        if not md_text:
            return "PDF 파싱에 실패했습니다."

        result = parse_personnel_pdf(md_text)
        if not result.get("appointments"):
            return "PDF에서도 선임/해임 안건을 찾을 수 없습니다. agm_personnel_ocr로 재시도 가능."

        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)
        return _format_personnel(result)

    @mcp.tool()
    async def agm_personnel_ocr(
        rcept_no: str,
        format: str = "md",
    ) -> str:
        """이사/감사 선임 정보를 OCR로 추출합니다 (OCR, 가장 느림).
        agm_personnel_pdf도 실패 시 최후 수단. UPSTAGE_API_KEY가 .env에 없으면 에러.
        OCR도 실패하면 AI가 agm_extract/agm_items 원문 기반으로 직접 재구성.

        Args:
            rcept_no: 접수번호
            format: "md" (기본) 또는 "json"
        """
        pdf_bytes = await _get_pdf_cached(rcept_no)
        md_text = _get_pdf_markdown_cached(rcept_no, pdf_bytes)

        result = ocr_fallback_for_parser(
            pdf_bytes, md_text, "pers", parse_personnel_pdf, f"{rcept_no}.pdf"
        )
        if not result or not result.get("appointments"):
            return "OCR로도 선임/해임 정보를 추출할 수 없습니다."

        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)
        return _format_personnel(result)

    @mcp.tool()
    async def agm_financials_pdf(
        rcept_no: str,
        format: str = "md",
    ) -> str:
        """재무제표를 PDF에서 파싱합니다 (PDF, 4초+).
        agm_financials_xml 실패 시 사용. 정상 기준은 _xml과 동일. 여전히 실패하면 agm_financials_ocr로 재시도.

        Args:
            rcept_no: 접수번호
            format: "md" (기본) 또는 "json"
        """
        pdf_bytes = await _get_pdf_cached(rcept_no)
        md_text = _get_pdf_markdown_cached(rcept_no, pdf_bytes)
        if not md_text:
            return "PDF 파싱에 실패했습니다."

        result = parse_financials_pdf(md_text)
        has_data = any(
            result[scope][stmt] is not None
            for scope in ["consolidated", "separate"]
            for stmt in ["balance_sheet", "income_statement"]
        )
        if not has_data:
            return "PDF에서도 재무제표를 찾을 수 없습니다. agm_financials_ocr로 재시도 가능."

        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)
        return _format_financial_statements(result)

    @mcp.tool()
    async def agm_financials_ocr(
        rcept_no: str,
        format: str = "md",
    ) -> str:
        """재무제표를 OCR로 추출합니다 (OCR, 가장 느림).
        agm_financials_pdf도 실패 시 최후 수단. UPSTAGE_API_KEY가 .env에 없으면 에러.
        OCR도 실패하면 AI가 agm_extract/agm_items 원문 기반으로 직접 재구성.

        Args:
            rcept_no: 접수번호
            format: "md" (기본) 또는 "json"
        """
        pdf_bytes = await _get_pdf_cached(rcept_no)
        md_text = _get_pdf_markdown_cached(rcept_no, pdf_bytes)

        result = ocr_fallback_for_parser(
            pdf_bytes, md_text, "fin", parse_financials_pdf, f"{rcept_no}.pdf"
        )
        if not result:
            return "OCR로도 재무제표를 추출할 수 없습니다."

        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)
        return _format_financial_statements(result)

    @mcp.tool()
    async def agm_aoi_change_pdf(
        rcept_no: str,
        format: str = "md",
    ) -> str:
        """정관변경 사항을 PDF에서 파싱합니다 (PDF, 4초+).
        agm_aoi_change_xml 실패 시 사용. 정상 기준은 _xml과 동일. 여전히 실패하면 agm_aoi_change_ocr로 재시도.

        Args:
            rcept_no: 접수번호
            format: "md" (기본) 또는 "json"
        """
        pdf_bytes = await _get_pdf_cached(rcept_no)
        md_text = _get_pdf_markdown_cached(rcept_no, pdf_bytes)
        if not md_text:
            return "PDF 파싱에 실패했습니다."

        result = parse_aoi_pdf(md_text)
        if not result.get("amendments"):
            return "PDF에서도 정관변경 사항을 찾을 수 없습니다. agm_aoi_change_ocr로 재시도 가능."

        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)
        return _format_aoi_change(result)

    @mcp.tool()
    async def agm_aoi_change_ocr(
        rcept_no: str,
        format: str = "md",
    ) -> str:
        """정관변경 사항을 OCR로 추출합니다 (OCR, 가장 느림).
        agm_aoi_change_pdf도 실패 시 최후 수단. UPSTAGE_API_KEY가 .env에 없으면 에러.
        OCR도 실패하면 AI가 agm_extract/agm_items 원문 기반으로 직접 재구성.

        Args:
            rcept_no: 접수번호
            format: "md" (기본) 또는 "json"
        """
        pdf_bytes = await _get_pdf_cached(rcept_no)
        md_text = _get_pdf_markdown_cached(rcept_no, pdf_bytes)

        result = ocr_fallback_for_parser(
            pdf_bytes, md_text, "aoi", parse_aoi_pdf, f"{rcept_no}.pdf"
        )
        if not result or not result.get("amendments"):
            return "OCR로도 정관변경 사항을 추출할 수 없습니다."

        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)
        return _format_aoi_change(result)

    @mcp.tool()
    async def agm_compensation_pdf(
        rcept_no: str,
        format: str = "md",
    ) -> str:
        """보수한도를 PDF에서 파싱합니다 (PDF, 4초+).
        agm_compensation_xml 실패 시 사용. 정상 기준은 _xml과 동일. 여전히 실패하면 agm_compensation_ocr로 재시도.

        Args:
            rcept_no: 접수번호
            format: "md" (기본) 또는 "json"
        """
        pdf_bytes = await _get_pdf_cached(rcept_no)
        md_text = _get_pdf_markdown_cached(rcept_no, pdf_bytes)
        if not md_text:
            return "PDF 파싱에 실패했습니다."

        result = parse_compensation_pdf(md_text)
        if not result.get("items"):
            return "PDF에서도 보수한도 안건을 찾을 수 없습니다. agm_compensation_ocr로 재시도 가능."

        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)
        return _format_compensation(result)

    @mcp.tool()
    async def agm_compensation_ocr(
        rcept_no: str,
        format: str = "md",
    ) -> str:
        """보수한도를 OCR로 추출합니다 (OCR, 가장 느림).
        agm_compensation_pdf도 실패 시 최후 수단. UPSTAGE_API_KEY가 .env에 없으면 에러.
        OCR도 실패하면 AI가 agm_extract/agm_items 원문 기반으로 직접 재구성.

        Args:
            rcept_no: 접수번호
            format: "md" (기본) 또는 "json"
        """
        pdf_bytes = await _get_pdf_cached(rcept_no)
        md_text = _get_pdf_markdown_cached(rcept_no, pdf_bytes)

        result = ocr_fallback_for_parser(
            pdf_bytes, md_text, "comp", parse_compensation_pdf, f"{rcept_no}.pdf"
        )
        if not result or not result.get("items"):
            return "OCR로도 보수한도 정보를 추출할 수 없습니다."

        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)
        return _format_compensation(result)

    @mcp.tool()
    async def agm_treasury_share_pdf(
        rcept_no: str,
        format: str = "md",
    ) -> str:
        """자기주식 보유/처분/소각을 PDF에서 파싱합니다 (PDF, 4초+).
        agm_treasury_share_xml 실패 시 사용. 정상 기준은 _xml과 동일. 여전히 실패하면 agm_treasury_share_ocr로 재시도.

        Args:
            rcept_no: 접수번호
            format: "md" (기본) 또는 "json"
        """
        pdf_bytes = await _get_pdf_cached(rcept_no)
        md_text = _get_pdf_markdown_cached(rcept_no, pdf_bytes)
        if not md_text:
            return "PDF 파싱에 실패했습니다."

        result = parse_treasury_share_pdf(md_text)
        if not result.get("items"):
            return "PDF에서도 자기주식 안건을 찾을 수 없습니다. agm_treasury_share_ocr로 재시도 가능."

        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)
        return _format_treasury_share(result)

    @mcp.tool()
    async def agm_treasury_share_ocr(
        rcept_no: str,
        format: str = "md",
    ) -> str:
        """자기주식 보유/처분/소각을 OCR로 추출합니다 (OCR, 가장 느림).
        agm_treasury_share_pdf도 실패 시 최후 수단. UPSTAGE_API_KEY가 .env에 없으면 에러.
        OCR도 실패하면 AI가 agm_extract/agm_items 원문 기반으로 직접 재구성.

        Args:
            rcept_no: 접수번호
            format: "md" (기본) 또는 "json"
        """
        pdf_bytes = await _get_pdf_cached(rcept_no)
        md_text = _get_pdf_markdown_cached(rcept_no, pdf_bytes)

        result = ocr_fallback_for_parser(
            pdf_bytes, md_text, "treasury", parse_treasury_share_pdf, f"{rcept_no}.pdf"
        )
        if not result or not result.get("items"):
            return "OCR로도 자기주식 정보를 추출할 수 없습니다."

        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)
        return _format_treasury_share(result)

    @mcp.tool()
    async def agm_capital_reserve_pdf(
        rcept_no: str,
        format: str = "md",
    ) -> str:
        """자본준비금 감소/이익잉여금 전입을 PDF에서 파싱합니다 (PDF, 4초+).
        agm_capital_reserve_xml 실패 시 사용. 정상 기준은 _xml과 동일. 여전히 실패하면 agm_capital_reserve_ocr로 재시도.

        Args:
            rcept_no: 접수번호
            format: "md" (기본) 또는 "json"
        """
        pdf_bytes = await _get_pdf_cached(rcept_no)
        md_text = _get_pdf_markdown_cached(rcept_no, pdf_bytes)
        if not md_text:
            return "PDF 파싱에 실패했습니다."

        result = parse_capital_reserve_pdf(md_text)
        if not result.get("items"):
            return "PDF에서도 자본준비금 안건을 찾을 수 없습니다. agm_capital_reserve_ocr로 재시도 가능."

        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)
        return _format_capital_reserve(result)

    @mcp.tool()
    async def agm_capital_reserve_ocr(
        rcept_no: str,
        format: str = "md",
    ) -> str:
        """자본준비금 감소/이익잉여금 전입을 OCR로 추출합니다 (OCR, 가장 느림).
        agm_capital_reserve_pdf도 실패 시 최후 수단. UPSTAGE_API_KEY가 .env에 없으면 에러.
        OCR도 실패하면 AI가 agm_extract/agm_items 원문 기반으로 직접 재구성.

        Args:
            rcept_no: 접수번호
            format: "md" (기본) 또는 "json"
        """
        pdf_bytes = await _get_pdf_cached(rcept_no)
        md_text = _get_pdf_markdown_cached(rcept_no, pdf_bytes)

        result = ocr_fallback_for_parser(
            pdf_bytes, md_text, "capital", parse_capital_reserve_pdf, f"{rcept_no}.pdf"
        )
        if not result or not result.get("items"):
            return "OCR로도 자본준비금 정보를 추출할 수 없습니다."

        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)
        return _format_capital_reserve(result)

    @mcp.tool()
    async def agm_retirement_pay_pdf(
        rcept_no: str,
        format: str = "md",
    ) -> str:
        """퇴직금 규정 개정을 PDF에서 파싱합니다 (PDF, 4초+).
        agm_retirement_pay_xml 실패 시 사용. 정상 기준은 _xml과 동일. 여전히 실패하면 agm_retirement_pay_ocr로 재시도.

        Args:
            rcept_no: 접수번호
            format: "md" (기본) 또는 "json"
        """
        pdf_bytes = await _get_pdf_cached(rcept_no)
        md_text = _get_pdf_markdown_cached(rcept_no, pdf_bytes)
        if not md_text:
            return "PDF 파싱에 실패했습니다."

        result = parse_retirement_pay_pdf(md_text)
        if not result.get("amendments"):
            return "PDF에서도 퇴직금 규정을 찾을 수 없습니다. agm_retirement_pay_ocr로 재시도 가능."

        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)
        return _format_retirement_pay(result)

    @mcp.tool()
    async def agm_retirement_pay_ocr(
        rcept_no: str,
        format: str = "md",
    ) -> str:
        """퇴직금 규정 개정을 OCR로 추출합니다 (OCR, 가장 느림).
        agm_retirement_pay_pdf도 실패 시 최후 수단. UPSTAGE_API_KEY가 .env에 없으면 에러.
        OCR도 실패하면 AI가 agm_extract/agm_items 원문 기반으로 직접 재구성.

        Args:
            rcept_no: 접수번호
            format: "md" (기본) 또는 "json"
        """
        pdf_bytes = await _get_pdf_cached(rcept_no)
        md_text = _get_pdf_markdown_cached(rcept_no, pdf_bytes)

        result = ocr_fallback_for_parser(
            pdf_bytes, md_text, "retirement", parse_retirement_pay_pdf, f"{rcept_no}.pdf"
        )
        if not result or not result.get("amendments"):
            return "OCR로도 퇴직금 규정을 추출할 수 없습니다."

        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)
        return _format_retirement_pay(result)

    @mcp.tool()
    async def agm_agenda_pdf(
        rcept_no: str,
        format: str = "md",
    ) -> str:
        """안건 목록을 PDF에서 파싱합니다 (PDF, 4초+).
        agm_agenda_xml 실패 시 사용. 정상 기준은 _xml과 동일. 여전히 실패하면 agm_agenda_ocr로 재시도.

        Args:
            rcept_no: 접수번호
            format: "md" (기본) 또는 "json"
        """
        pdf_bytes = await _get_pdf_cached(rcept_no)
        md_text = _get_pdf_markdown_cached(rcept_no, pdf_bytes)
        if not md_text:
            return "PDF 파싱에 실패했습니다."

        result = parse_agenda_pdf(md_text)
        if not result:
            return "PDF에서도 안건을 찾을 수 없습니다. agm_agenda_ocr로 재시도 가능."

        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)
        return _format_agenda_tree(result)

    @mcp.tool()
    async def agm_agenda_ocr(
        rcept_no: str,
        format: str = "md",
    ) -> str:
        """안건 목록을 OCR로 추출합니다 (OCR, 가장 느림).
        agm_agenda_pdf도 실패 시 최후 수단. UPSTAGE_API_KEY가 .env에 없으면 에러.
        OCR도 실패하면 AI가 agm_extract/agm_items 원문 기반으로 직접 재구성.

        Args:
            rcept_no: 접수번호
            format: "md" (기본) 또는 "json"
        """
        pdf_bytes = await _get_pdf_cached(rcept_no)
        md_text = _get_pdf_markdown_cached(rcept_no, pdf_bytes)

        result = ocr_fallback_for_parser(
            pdf_bytes, md_text, "agenda", parse_agenda_pdf, f"{rcept_no}.pdf"
        )
        if not result:
            return "OCR로도 안건을 추출할 수 없습니다."

        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)
        return _format_agenda_tree(result)

    @mcp.tool()
    async def agm_result(
        ticker: str,
        bgn_de: str = "",
        end_de: str = "",
        format: str = "md",
    ) -> str:
        """정기주주총회 결과 — 안건별 투표 결과(가결/부결, 찬성률, 반대기권률).
        KIND 크롤링으로 다운로드 없이 파싱. 찬성률은 발행주식 기준과 행사주식 기준 둘 다 제공.
        주총이 아직 열리지 않았으면 결과가 없음.

        Args:
            ticker: 종목코드 또는 회사명
            bgn_de: 검색 시작일 YYYYMMDD (미입력 시 올해 1월 1일)
            end_de: 검색 종료일 YYYYMMDD (미입력 시 오늘)
            format: "md" (기본) 또는 "json"
        """
        client = DartClient()
        corp = await client.lookup_corp_code(ticker)
        if not corp:
            return f"'{ticker}'에 해당하는 기업을 찾을 수 없습니다."

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
            return f"공시 검색 실패: {e}"

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


def _parse_agm_result_table(soup) -> list[dict]:
    """주주총회결과 HTML에서 안건별 투표 결과 테이블 파싱

    헤더 패턴: 번호 | 결의구분 | 회의목적사항 | 가결여부 | 찬성률(발행) | 찬성률(행사) | 반대기권
    """
    tables = soup.find_all("table")
    for table in tables:
        rows = table.find_all("tr")
        if len(rows) < 3:
            continue

        # 헤더 확인 — "번호"와 "가결여부"가 있는 테이블
        header_text = " ".join(c.get_text(strip=True) for c in rows[0].find_all(["td", "th"]))
        if "번호" not in header_text or "가결여부" not in header_text:
            continue

        # 두 번째 행이 서브헤더 (찬성률, 반대기권)인 경우 스킵
        data_start = 1
        row1_text = " ".join(c.get_text(strip=True) for c in rows[1].find_all(["td", "th"]))
        if "찬성률" in row1_text:
            data_start = 2

        items = []
        for row in rows[data_start:]:
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            if len(cells) < 5:
                continue
            # 빈 행 스킵
            if not cells[0] or cells[0] == "-":
                continue

            try:
                iss = float(cells[4]) if len(cells) > 4 and cells[4] else 0
                vot = float(cells[5]) if len(cells) > 5 and cells[5] else 0
                attend = round(iss / vot * 100, 1) if vot > 0 else None
            except (ValueError, ZeroDivisionError):
                attend = None

            item = {
                "number": cells[0],
                "resolution_type": cells[1] if len(cells) > 1 else "",
                "agenda": cells[2] if len(cells) > 2 else "",
                "passed": cells[3] if len(cells) > 3 else "",
                "approval_rate_issued": cells[4] if len(cells) > 4 else "",
                "approval_rate_voted": cells[5] if len(cells) > 5 else "",
                "opposition_rate": cells[6] if len(cells) > 6 else "",
                "estimated_attendance": attend,
            }
            items.append(item)

        if items:
            return items

    return []


def _format_agm_result(data: dict) -> str:
    """주주총회결과 마크다운 포맷"""
    lines = [f"# {data['corp_name']} 주주총회 결과\n"]
    lines.append(f"**공시일**: {data['rcept_dt']}\n")

    items = data.get("items", [])
    if not items:
        lines.append("투표 결과 없음")
        return "\n".join(lines)

    # 추정 참석률 — 보통결의 안건 중 최빈값
    from collections import Counter
    ordinary_att = []
    for item in items:
        if "보통" in item.get("resolution_type", "") and item.get("estimated_attendance"):
            ordinary_att.append(item["estimated_attendance"])
    if ordinary_att:
        most_common = Counter(ordinary_att).most_common(1)[0]
        lines.append(f"**추정 참석률**: {most_common[0]}% (보통결의 {most_common[1]}건 기준, 발행기준/행사기준 역산)")
        lines.append(f"*최대주주 제외 참석률은 own_major 지분율과 조합하여 추정 가능*\n")

    lines.append("| 번호 | 결의구분 | 안건 | 결과 | 찬성(발행기준) | 찬성(행사기준) | 반대/기권 | 추정참석률 |")
    lines.append("|------|---------|------|------|-------------|-------------|----------|----------|")

    for item in items:
        passed = item.get("passed", "")
        if "가결" in passed:
            passed_fmt = f"**{passed}**"
        elif "부결" in passed:
            passed_fmt = f"~~{passed}~~"
        else:
            passed_fmt = passed

        att = item.get("estimated_attendance")
        att_str = f"{att}%" if att else "-"

        lines.append(
            f"| {item.get('number', '')} "
            f"| {item.get('resolution_type', '')} "
            f"| {item.get('agenda', '')} "
            f"| {passed_fmt} "
            f"| {item.get('approval_rate_issued', '')}% "
            f"| {item.get('approval_rate_voted', '')}% "
            f"| {item.get('opposition_rate', '')}% "
            f"| {att_str} |"
        )

    return "\n".join(lines)


def _build_financial_highlight(fs: dict) -> list[dict] | None:
    """재무제표에서 핵심 지표를 format_krw로 변환하여 추출"""
    highlights = []

    # 연결 우선, 없으면 별도
    scope = "consolidated" if fs["consolidated"]["balance_sheet"] else "separate"

    bs = fs[scope].get("balance_sheet")
    is_stmt = fs[scope].get("income_statement")
    unit = ""

    # 재무상태표 하이라이트
    if bs:
        unit = bs.get("unit", "")
        for row in bs.get("rows", []):
            acct = row[0].replace(" ", "")
            val_idx = 2 if len(row) > 3 else 1  # note 있으면 [2], 없으면 [1]
            val = row[val_idx] if val_idx < len(row) else ""
            if '자산총계' in acct and val:
                highlights.append({"label": "자산총계", "value": format_krw(val, unit)})
            elif '부채총계' in acct and val and not highlights_has(highlights, '부채총계'):
                highlights.append({"label": "부채총계", "value": format_krw(val, unit)})
            elif '자본총계' in acct and '부채' not in acct and val and not highlights_has(highlights, '자본총계'):
                highlights.append({"label": "자본총계", "value": format_krw(val, unit)})

    # 손익계산서 하이라이트
    if is_stmt:
        unit = is_stmt.get("unit", "")
        for row in is_stmt.get("rows", []):
            acct = row[0].replace(" ", "")
            val_idx = 2 if len(row) > 3 else 1
            val = row[val_idx] if val_idx < len(row) else ""
            if ('매출' in acct and '액' in acct or acct.startswith('매출')) and '원가' not in acct and '총' not in acct and val and not highlights_has(highlights, '매출'):
                highlights.append({"label": "매출", "value": format_krw(val, unit)})
            elif '영업이익' in acct and val and not highlights_has(highlights, '영업이익'):
                highlights.append({"label": "영업이익", "value": format_krw(val, unit)})
            elif '당기순이익' == acct and val:
                highlights.append({"label": "당기순이익", "value": format_krw(val, unit)})

    # 배당 하이라이트
    re_stmt = fs.get("retained_earnings")
    if re_stmt and re_stmt.get("has_dividend"):
        unit = re_stmt.get("unit", "")
        for item in re_stmt.get("items", []):
            if '배당금' in item["account"] or '현금배당' in item["account"]:
                if item["current"]:
                    # 처분계산서에서 배당은 음수로 표시되므로 절대값
                    raw = item["current"].replace("(", "").replace(")", "").replace("-", "")
                    highlights.append({
                        "label": "배당금",
                        "value": format_krw(raw, unit),
                    })
                # 주당배당금 추출
                m = re.search(r'(\d[\d,]*)\s*원\s*\(', item["account"])
                if m:
                    highlights.append({
                        "label": "주당배당금",
                        "value": f"{m.group(1)}원",
                    })
                break
    elif re_stmt and not re_stmt.get("has_dividend"):
        highlights.append({"label": "배당", "value": "미실시"})

    # 자사주 하이라이트
    for s in ["consolidated", "separate"]:
        eq = fs[s].get("equity_changes")
        if eq:
            flags = []
            if eq.get("has_treasury_acquisition"):
                flags.append("취득")
            if eq.get("has_treasury_disposal"):
                flags.append("소각/처분")
            if flags:
                highlights.append({"label": "자사주", "value": ", ".join(flags)})
            break

    return highlights if highlights else None


def _format_compensation(result: dict) -> str:
    """보수한도를 마크다운으로 포매팅"""
    lines = ["## 보수한도 승인", ""]
    s = result.get("summary", {})

    # 요약
    if s.get("currentTotalLimit"):
        lines.append(f"**당기 한도 총액**: {_format_won(s['currentTotalLimit'])}")
    if s.get("priorTotalPaid"):
        lines.append(f"**전기 실제 지급**: {_format_won(s['priorTotalPaid'])}")
    if s.get("priorTotalLimit"):
        lines.append(f"**전기 한도**: {_format_won(s['priorTotalLimit'])}")
    if s.get("priorUtilization") is not None:
        lines.append(f"**전기 한도 소진율**: {s['priorUtilization']}%")
    lines.append("")

    for item in result.get("items", []):
        lines.append(f"### {item['number']}: {item['title']}")
        lines.append("")

        cur = item.get("current", {})
        pri = item.get("prior", {})

        if cur:
            lines.append("**당기**")
            if cur.get("headcount"):
                lines.append(f"- 이사의 수 (사외이사수): {cur['headcount']}")
            if cur.get("limit"):
                lines.append(f"- 보수 최고한도액: {cur['limit']}")
            lines.append("")

        if pri:
            lines.append("**전기**")
            if pri.get("headcount"):
                lines.append(f"- 이사의 수 (사외이사수): {pri['headcount']}")
            if pri.get("actualPaid"):
                lines.append(f"- 실제 지급된 보수총액: {pri['actualPaid']}")
            if pri.get("limit"):
                lines.append(f"- 최고한도액: {pri['limit']}")
            lines.append("")

        for note in item.get("notes", []):
            lines.append(f"> {note}")
            lines.append("")

    return "\n".join(lines)


def _format_aoi_change(result: dict) -> str:
    """정관변경을 마크다운으로 포매팅"""
    lines = ["## 정관변경 사항", ""]
    s = result.get("summary", {})
    lines.append(f"*총 {s.get('totalAmendments', 0)}건*")
    lines.append("")

    for a in result.get("amendments", []):
        sub_id = a.get("subAgendaId", "")
        label = a.get("label", "")
        header = f"### {sub_id}: {label}" if sub_id else f"### {label}"
        lines.append(header)
        if a.get("clause"):
            lines.append(f"*조항: {a['clause']}*")
        if a.get("reason"):
            lines.append(f"*사유: {a['reason']}*")
        lines.append("")
        if a.get("before"):
            lines.append(f"**변경전**")
            lines.append(f"> {a['before']}")
            lines.append("")
        if a.get("after"):
            lines.append(f"**변경후**")
            lines.append(f"> {a['after']}")
            lines.append("")

    return "\n".join(lines)


def _format_treasury_share(result: dict) -> str:
    """자기주식을 마크다운으로 포매팅"""
    lines = ["## 자기주식 현황", ""]
    s = result.get("summary", {})
    lines.append(f"*총 {s.get('totalItems', 0)}건*")
    lines.append("")

    for item in result.get("items", []):
        title_str = f"{item['number']}: {item['title']}" if item.get('number') else item['title']
        lines.append(f"### {title_str}")
        lines.append(f"*유형: {item.get('type', '')}*")
        lines.append("")

        if item.get("purpose"):
            lines.append(f"**목적**: {item['purpose']}")
            lines.append("")

        if item.get("sharesInfo"):
            lines.append("**주식수 정보**")
            for si in item["sharesInfo"]:
                lines.append(f"- {si}")
            lines.append("")

        if item.get("schedule"):
            lines.append("**스케줄**")
            for sc in item["schedule"]:
                lines.append(f"- {sc}")
            lines.append("")

        for tbl in item.get("tables", []):
            headers = tbl.get("headers", [])
            if headers:
                lines.append("| " + " | ".join(h.replace("|", "\\|") for h in headers) + " |")
                lines.append("| " + " | ".join("---" for _ in headers) + " |")
                for row in tbl.get("rows", []):
                    padded = row[:len(headers)] if len(row) >= len(headers) else row + [""] * (len(headers) - len(row))
                    lines.append("| " + " | ".join(c.replace("|", "\\|") for c in padded) + " |")
                lines.append("")

        for note in item.get("notes", []):
            lines.append(f"> {note}")
            lines.append("")

    return "\n".join(lines)


def _format_capital_reserve(result: dict) -> str:
    """자본준비금을 마크다운으로 포매팅"""
    lines = ["## 자본준비금 감소/이익잉여금 전입", ""]
    s = result.get("summary", {})
    lines.append(f"*총 {s.get('totalItems', 0)}건*")
    lines.append("")

    for item in result.get("items", []):
        title_str = f"{item['number']}: {item['title']}" if item.get('number') else item['title']
        lines.append(f"### {title_str}")
        lines.append("")

        if item.get("amount"):
            lines.append(f"**금액**: {item['amount']}")
            lines.append("")

        if item.get("purpose"):
            lines.append(f"**목적**: {item['purpose']}")
            lines.append("")

        for note in item.get("notes", []):
            lines.append(f"> {note}")
            lines.append("")

    return "\n".join(lines)


def _format_retirement_pay(result: dict) -> str:
    """퇴직금 규정을 마크다운으로 포매팅"""
    lines = ["## 퇴직금 규정 개정", ""]
    s = result.get("summary", {})
    lines.append(f"*총 {s.get('totalAmendments', 0)}건*")
    lines.append("")

    for a in result.get("amendments", []):
        clause = a.get("clause", "")
        header = f"### {clause}" if clause else "### (조항 미상)"
        lines.append(header)
        if a.get("reason"):
            lines.append(f"*사유: {a['reason']}*")
        lines.append("")
        if a.get("before"):
            lines.append(f"**현행**")
            lines.append(f"> {a['before']}")
            lines.append("")
        if a.get("after"):
            lines.append(f"**개정안**")
            lines.append(f"> {a['after']}")
            lines.append("")

    return "\n".join(lines)


def _format_personnel(result: dict) -> str:
    """인사 안건을 마크다운으로 포매팅"""
    lines = ["## 선임/해임 현황", ""]

    s = result.get("summary", {})
    parts = []
    if s.get("directors"): parts.append(f"이사 {s['directors']}명")
    if s.get("outside_directors"): parts.append(f"사외이사 {s['outside_directors']}명")
    if s.get("auditors"): parts.append(f"감사 {s['auditors']}명")
    if s.get("audit_committee"): parts.append(f"감사위원회 {s['audit_committee']}명")
    if s.get("dismissals"): parts.append(f"해임 {s['dismissals']}명")
    if parts:
        lines.append(f"*{', '.join(parts)}*")
        lines.append("")

    for a in result.get("appointments", []):
        lines.append(f"### {a['number']}: {a['title'][:50]}")
        lines.append(f"*{a['action']} | {a['category']}*")
        lines.append("")

        for c in a.get("candidates", []):
            lines.append(f"- **{c.get('name', '?')}**")
            if c.get("birthDate"):
                lines.append(f"  - 생년월일: {c['birthDate']}")
            if c.get("roleType"):
                lines.append(f"  - 직위: {c['roleType']}")
            if c.get("recommender"):
                lines.append(f"  - 추천인: {c['recommender']}")
            if c.get("majorShareholderRelation"):
                lines.append(f"  - 최대주주 관계: {c['majorShareholderRelation']}")
            if c.get("mainJob"):
                lines.append(f"  - 주요경력: {c['mainJob']}")
            if c.get("careerDetails"):
                lines.append(f"  - 세부경력:")
                for cd in c["careerDetails"]:
                    period = cd.get("period", "")
                    content = cd.get("content", "")
                    if period and content:
                        lines.append(f"    - {period}: {content}")
                    elif content:
                        lines.append(f"    - {content}")
            if c.get("recent3yTransactions"):
                lines.append(f"  - 법인 거래내역: {c['recent3yTransactions']}")
            if c.get("eligibility"):
                el = c["eligibility"]
                lines.append(f"  - 결격사유:")
                lines.append(f"    - 체납: {el.get('taxDelinquency', '해당사항 없음')}")
                lines.append(f"    - 부실기업: {el.get('insolventMgmt', '해당사항 없음')}")
                lines.append(f"    - 법령상: {el.get('legalDisqualification', '해당사항 없음')}")
            if c.get("dutyPlan"):
                lines.append(f"  - 직무수행계획: {c['dutyPlan']}")
            if c.get("recommendationReason"):
                lines.append(f"  - 추천사유: {c['recommendationReason']}")
        lines.append("")

    return "\n".join(lines)


def highlights_has(highlights: list, label: str) -> bool:
    return any(h["label"] == label for h in highlights)


def _format_correction_details(result: dict) -> str:
    """정정 사항을 마크다운으로 포매팅"""
    lines = ["## 정정 사항", ""]

    if result.get("date"):
        lines.append(f"- **정정일**: {result['date']}")
    if result.get("target_document"):
        lines.append(f"- **정정대상**: {result['target_document']}")
    if result.get("original_date"):
        lines.append(f"- **최초제출일**: {result['original_date']}")
    lines.append("")

    for i, item in enumerate(result.get("items", []), 1):
        lines.append(f"### 정정 {i}: {item['section'][:60]}")
        lines.append(f"**정정사유**: {item['reason']}")
        lines.append("")
        lines.append(f"**정정 전**:")
        lines.append(f"> {item['before'][:300]}")
        lines.append("")
        lines.append(f"**정정 후**:")
        lines.append(f"> {item['after'][:300]}")
        lines.append("")

    return "\n".join(lines)


def _format_financial_statements(result: dict) -> str:
    """재무제표를 마크다운으로 포매팅"""
    lines = []
    stmt_names = {
        "balance_sheet": "재무상태표",
        "income_statement": "손익계산서",
    }
    scope_names = {
        "consolidated": "연결",
        "separate": "별도",
    }

    for scope in ["consolidated", "separate"]:
        for stmt in ["balance_sheet", "income_statement"]:
            entry = result[scope][stmt]
            if entry is None:
                continue

            title = f"{scope_names[scope]} {stmt_names[stmt]}"
            unit = entry.get("unit", "")
            periods = entry.get("period_labels", {})

            lines.append(f"## {title}")
            if unit:
                lines.append(f"*(단위: {unit})*")
            lines.append("")

            # 마크다운 테이블 — 주석 컬럼 유무에 따라 동적
            cols = entry.get("columns", [])
            has_note = "note" in cols
            if has_note:
                header = ["과목", "주석", periods.get("current", "당기"), periods.get("prior", "전기")]
            else:
                header = ["과목", periods.get("current", "당기"), periods.get("prior", "전기")]
            col_count = len(header)

            lines.append("| " + " | ".join(header) + " |")
            lines.append("| " + " | ".join("---" for _ in header) + " |")

            for row in entry.get("rows", []):
                padded = row[:col_count] if len(row) >= col_count else row + [""] * (col_count - len(row))
                escaped = [c.replace("|", "\\|") for c in padded]
                lines.append("| " + " | ".join(escaped) + " |")

            lines.append("")

    # 자본변동표
    for scope in ["consolidated", "separate"]:
        eq = result[scope].get("equity_changes")
        if eq is None:
            continue
        title = f"{scope_names[scope]} 자본변동표"
        flags = []
        if eq.get("has_treasury_acquisition"): flags.append("자사주 취득")
        if eq.get("has_treasury_disposal"): flags.append("자사주 소각/처분")
        flag_str = f" ({', '.join(flags)})" if flags else ""

        lines.append(f"## {title}{flag_str}")
        if eq.get("unit"):
            lines.append(f"*(단위: {eq['unit']})*")
        lines.append("")

        cols = eq.get("columns", [])
        lines.append("| " + " | ".join(c.replace("|", "\\|")[:15] for c in cols) + " |")
        lines.append("| " + " | ".join("---" for _ in cols) + " |")
        for row in eq.get("rows", []):
            padded = row[:len(cols)] if len(row) >= len(cols) else row + [""] * (len(cols) - len(row))
            escaped = [c.replace("|", "\\|")[:15] for c in padded]
            lines.append("| " + " | ".join(escaped) + " |")
        lines.append("")

    # 이익잉여금처분계산서
    re_stmt = result.get("retained_earnings")
    if re_stmt:
        has_div = re_stmt.get("has_dividend", False)
        lines.append(f"## 이익잉여금처분계산서 {'(배당 실시)' if has_div else '(배당 미실시)'}")
        if re_stmt.get("unit"):
            lines.append(f"*(단위: {re_stmt['unit']})*")
        if re_stmt.get("disposal_date"):
            lines.append(f"*처분예정일: {re_stmt['disposal_date']}*")
        lines.append("")

        lines.append("| 항목 | 당기 | 전기 |")
        lines.append("| --- | --- | --- |")
        for item in re_stmt.get("items", []):
            acct = item["account"].replace("|", "\\|")[:60]
            lines.append(f"| {acct} | {item['current']} | {item['prior']} |")
        lines.append("")

    return "\n".join(lines)
