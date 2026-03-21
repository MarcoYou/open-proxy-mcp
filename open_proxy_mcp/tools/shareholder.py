"""주주총회 소집공고 관련 MCP tools"""

import json
import logging
from datetime import datetime
from open_proxy_mcp.dart.client import DartClient
from open_proxy_mcp.tools.parser import (
    parse_agenda_items, parse_meeting_info,
    validate_agenda_result, _extract_notice_section, _extract_agenda_zone,
    parse_agenda_details, validate_agenda_details,
    parse_financial_statements,
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
        use_llm: bool = False,
        format: str = "md",
    ) -> str:
        """주주총회 소집공고에서 의안(안건) 목록을 구조화하여 반환합니다.

        의안 번호, 제목, 상하위 관계(제2-1호는 제2호의 하위),
        카테고리, 조건부 의안 여부를 파싱하여 트리 형태로 반환합니다.

        Args:
            rcept_no: 접수번호 (예: 20260225000123)
            use_llm: True면 파싱 실패/의심 시 LLM fallback 사용 (기본: False)
            format: 반환 형식. "md" (마크다운, 기본) 또는 "json" (프론트엔드용)
        """
        doc = await _get_document_cached(rcept_no)
        text = doc["text"]
        html = doc.get("html", "")
        agenda = parse_agenda_items(text, html=html)
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
            agenda = await extract_agenda_with_llm(zone_clean)
            parse_method = "llm"

            if not validate_agenda_result(agenda):
                logger.error(f"[HARD FAIL] LLM fallback도 실패: {rcept_no}")
                return "안건을 파싱할 수 없습니다. (정규식 + LLM 모두 실패)"

        if format == "json":
            meeting_info = parse_meeting_info(text)
            result = _build_agenda_json(rcept_no, agenda, meeting_info, parse_method=parse_method)
            return json.dumps(result, ensure_ascii=False, indent=2)

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
        info = parse_meeting_info(doc["text"], html=doc.get("html", ""))
        return _format_meeting_info(info)

    @mcp.tool()
    async def get_agenda_detail(
        rcept_no: str,
        agenda_no: str = "",
        format: str = "md",
    ) -> str:
        """주주총회 소집공고의 안건별 상세 내용을 반환합니다.

        '목적사항별 기재사항' 섹션에서 각 안건의 상세 내용을 파싱합니다.
        재무제표, 정관변경 비교표, 이사 후보 정보 등 테이블은
        마크다운 테이블로, 텍스트는 그대로 반환합니다.

        Args:
            rcept_no: 접수번호 (예: 20260225000123)
            agenda_no: 안건 번호 (예: "1", "2"). 미입력 시 전체 안건 반환.
                       "2" 입력 시 제2호 + 하위(제2-1호~) 전체 반환.
            format: 반환 형식. "md" (마크다운, 기본) 또는 "json"
        """
        doc = await _get_document_cached(rcept_no)
        details = parse_agenda_details(doc["html"])

        if not validate_agenda_details(details):
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
    async def get_financial_statements(
        rcept_no: str,
        format: str = "json",
    ) -> str:
        """주주총회 소집공고에서 재무제표를 구조화하여 반환합니다.

        재무상태표(대차대조표)와 손익계산서를 연결/별도 구분하여
        당기/전기 데이터를 구조화된 형태로 반환합니다.

        Args:
            rcept_no: 접수번호 (예: 20260225000123)
            format: 반환 형식. "json" (기본, 구조화) 또는 "md" (마크다운 테이블)
        """
        doc = await _get_document_cached(rcept_no)
        html = doc.get("html", "")
        if not html:
            return "재무제표를 파싱할 수 없습니다. (HTML 없음)"

        result = parse_financial_statements(html)

        # 빈 결과 체크 — 안건 트리에서 이유 파악
        has_data = any(
            result[scope][stmt] is not None
            for scope in ["consolidated", "separate"]
            for stmt in ["balance_sheet", "income_statement"]
        )
        if not has_data:
            text = doc["text"]
            agenda = parse_agenda_items(text, html=html)
            info = parse_meeting_info(text, html=html)

            # 재무제표 승인 안건 존재 여부
            fs_agenda = [a for a in agenda if any(
                kw in a.get("title", "") for kw in ["재무제표", "재무상태표", "대차대조표"]
            )]
            # 철회/보고 전환 여부
            fs_withdrawn = [a for a in agenda if any(
                kw in a.get("title", "") for kw in ["철회", "보고사항으로 변경", "보고안건"]
            )]
            # 보고사항에 재무제표/감사 관련 항목
            report_items = info.get("report_items", [])
            fs_in_report = [r for r in report_items if any(
                kw in r for kw in ["재무", "감사", "결산"]
            )]

            if fs_withdrawn:
                return "재무제표 승인 안건이 철회되어 보고사항으로 전환되었습니다."
            elif not fs_agenda and fs_in_report:
                return f"재무제표 승인 안건이 없고, 보고사항으로만 존재합니다. (보고: {', '.join(fs_in_report)})"
            elif not fs_agenda:
                return "이번 주주총회에 재무제표 승인 안건이 없습니다."
            else:
                return "재무제표 승인 안건이 있으나 파싱에 실패했습니다. (비표준 문서 구조)"

        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)

        return _format_financial_statements(result)


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

    return "\n".join(lines)
