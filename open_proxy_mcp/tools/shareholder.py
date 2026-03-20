"""주주총회 소집공고 관련 MCP tools"""

import logging
from datetime import datetime
from open_proxy_mcp.dart.client import DartClient
from open_proxy_mcp.tools.parser import (
    parse_agenda_items, parse_meeting_info,
    validate_agenda_result, _extract_notice_section, _extract_agenda_zone,
)
from open_proxy_mcp.llm.client import extract_agenda_with_llm

logger = logging.getLogger(__name__)

# ── 문서 캐시 (프로세스 레벨) ──

_doc_cache: dict[str, dict] = {}
_MAX_CACHE = 30


async def _get_document_cached(rcept_no: str) -> dict:
    """get_document 결과를 캐싱하여 중복 API 호출 방지"""
    if rcept_no in _doc_cache:
        return _doc_cache[rcept_no]
    client = DartClient()
    doc = await client.get_document(rcept_no)
    if len(_doc_cache) >= _MAX_CACHE:
        _doc_cache.pop(next(iter(_doc_cache)))
    _doc_cache[rcept_no] = doc
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


# ── Tool 등록 ──

def register_tools(mcp):
    """FastMCP 서버에 주주총회 관련 tool 등록"""

    @mcp.tool()
    async def search_shareholder_meetings(
        ticker: str,
        bgn_de: str = "",
        end_de: str = "",
    ) -> str:
        """주주총회 소집공고를 검색합니다.

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

        lines = [
            f"## {corp_info.get('corp_name', '')} ({corp_info.get('stock_code', '')}) 주주총회 소집공고",
            f"검색기간: {bgn_de} ~ {end_de}",
            f"총 {len(filings)}건",
            "",
        ]
        for item in filings:
            lines.append(f"- **{item['report_nm']}** | 접수일: {item['rcept_dt']} | 접수번호: {item['rcept_no']}")

        return "\n".join(lines)

    @mcp.tool()
    async def get_shareholder_meeting_document(
        rcept_no: str,
        max_length: int = 10000,
    ) -> str:
        """주주총회 소집공고 본문을 가져옵니다.

        search_shareholder_meetings에서 얻은 접수번호(rcept_no)로
        공시 본문 텍스트를 반환합니다.

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
    async def get_meeting_agenda(
        rcept_no: str,
    ) -> str:
        """주주총회 소집공고에서 의안(안건) 목록을 구조화하여 반환합니다.

        의안 번호, 제목, 상하위 관계(제2-1호는 제2호의 하위),
        카테고리, 조건부 의안 여부를 파싱하여 트리 형태로 반환합니다.

        Args:
            rcept_no: 접수번호 (예: 20260225000123)
        """
        doc = await _get_document_cached(rcept_no)
        text = doc["text"]
        agenda = parse_agenda_items(text)

        if not validate_agenda_result(agenda):
            import re
            section = _extract_notice_section(text)
            zone = _extract_agenda_zone(section) if section else None

            if not zone:
                # hard fail: 문서에서 안건 영역을 찾을 수 없음
                logger.error(f"[HARD FAIL] section/zone 추출 실패: {rcept_no}")
                return "안건을 파싱할 수 없습니다. (문서에서 안건 영역을 찾을 수 없음)"

            # soft fail: 정규식 의심 → LLM fallback
            logger.warning(f"[SOFT FAIL] 정규식 파싱 의심 — LLM fallback 시도: {rcept_no}")
            zone_clean = re.sub(r'\n+', ' ', zone)
            agenda = await extract_agenda_with_llm(zone_clean)

            if not validate_agenda_result(agenda):
                # soft fail → LLM도 실패 → hard fail로 전환
                logger.error(f"[HARD FAIL] LLM fallback도 실패: {rcept_no}")
                return "안건을 파싱할 수 없습니다. (정규식 + LLM 모두 실패)"

        return _format_agenda_tree(agenda)

    @mcp.tool()
    async def get_meeting_info(
        rcept_no: str,
    ) -> str:
        """주주총회 소집공고에서 의안을 제외한 회의 정보를 반환합니다.

        정기/임시 구분, 일시, 장소, 보고사항, 전자투표 안내,
        의결권 행사 방법, 온라인 중계 등 비안건 정보를 반환합니다.

        Args:
            rcept_no: 접수번호 (예: 20260225000123)
        """
        doc = await _get_document_cached(rcept_no)
        info = parse_meeting_info(doc["text"])
        return _format_meeting_info(info)
