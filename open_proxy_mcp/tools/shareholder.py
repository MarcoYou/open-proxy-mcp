"""주주총회 소집공고 관련 MCP tools"""

import json
import logging
import re
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
    parse_agenda_items, parse_meeting_info,
    validate_agenda_result, _extract_notice_section, _extract_agenda_zone,
    parse_agenda_details, validate_agenda_details,
    parse_financial_statements,
    parse_correction_details,
    parse_personnel,
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
    async def agm_agenda(
        rcept_no: str,
        use_llm: bool = False,
        max_fallback_length: int = 3000,
        format: str = "md",
    ) -> str:
        """주주총회 소집공고에서 의안(안건) 목록을 구조화하여 반환합니다.

        의안 번호, 제목, 상하위 관계(제2-1호는 제2호의 하위),
        카테고리, 조건부 의안 여부를 파싱하여 트리 형태로 반환합니다.

        Args:
            rcept_no: 접수번호 (예: 20260225000123)
            use_llm: True면 파싱 실패/의심 시 LLM fallback 사용 (기본: False)
            max_fallback_length: LLM fallback 시 원문 최대 글자 수 (기본 3000, 0이면 제한 없음)
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
            if max_fallback_length > 0:
                zone_clean = zone_clean[:max_fallback_length]
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
        info = parse_meeting_info(doc["text"], html=doc.get("html", ""))

        # 정정공고 메타데이터 추가
        correction = parse_correction_details(doc.get("html", ""))
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
        """주주총회 소집공고의 안건별 상세 내용을 반환합니다.

        '목적사항별 기재사항' 섹션에서 각 안건의 상세 내용을 파싱합니다.
        재무제표, 정관변경 비교표, 이사 후보 정보 등 테이블은
        마크다운 테이블로, 텍스트는 그대로 반환합니다.

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

        details = parse_agenda_details(html)

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
    async def agm_financials(
        rcept_no: str,
        use_llm: bool = False,
        max_fallback_length: int = 3000,
        format: str = "md",
    ) -> str:
        """주주총회 소집공고에서 재무제표를 구조화하여 반환합니다.

        agm_items(안건 상세)의 재무제표 블록을 기반으로
        재무상태표/손익계산서를 정규화합니다.
        안건 상세 파싱 실패 시 HTML 직접 파싱으로 fallback.

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
        details = parse_agenda_details(html)
        fs_found = False
        if details:
            for d in details:
                title = d.get("title", "")
                if any(kw in title for kw in ["재무제표", "재무상태표", "대차대조표"]):
                    fs_found = True
                    logger.info(f"재무제표 안건 확인: {d['number']} — {title[:40]}")
                    break

        # 재무제표 테이블 정규화 (HTML 직접 파싱)
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
        """주주총회 소집공고의 정정 사항을 반환합니다.

        정정공고인 경우 정정 전/후 비교, 정정 사유를 구조화하여 반환합니다.
        정정공고가 아닌 경우 정정 사항이 없다고 반환합니다.

        Args:
            rcept_no: 접수번호 (예: 20260225000123)
            format: 반환 형식. "md" (마크다운, 기본) 또는 "json"
        """
        doc = await _get_document_cached(rcept_no)
        html = doc.get("html", "")
        if not html:
            return "정정 사항을 확인할 수 없습니다. (HTML 없음)"

        result = parse_correction_details(html)
        if not result:
            return "정정공고가 아닙니다. (정정 사항 없음)"

        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)

        return _format_correction_details(result)

    @mcp.tool()
    async def agm_personnel(
        rcept_no: str,
        format: str = "md",
    ) -> str:
        """주주총회 소집공고에서 이사/감사 선임·해임 정보를 반환합니다.

        선임/해임 안건의 후보자 정보(성명, 생년월일, 직위, 추천인,
        주요경력, 결격사유 등)를 구조화하여 반환합니다.

        Args:
            rcept_no: 접수번호 (예: 20260225000123)
            format: 반환 형식. "md" (마크다운, 기본) 또는 "json"
        """
        doc = await _get_document_cached(rcept_no)
        html = doc.get("html", "")
        if not html:
            return "인사 정보를 파싱할 수 없습니다. (HTML 없음)"

        result = parse_personnel(html)

        if not result.get("appointments"):
            return "선임/해임 안건이 없습니다."

        if format == "json":
            return json.dumps(result, ensure_ascii=False, indent=2)

        return _format_personnel(result)

    @mcp.tool()
    async def agm_steward(
        ticker: str,
        bgn_de: str = "",
        end_de: str = "",
    ) -> str:
        """주주총회 소집공고 스마트 오케스트레이터.

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
        info = parse_meeting_info(text, html=html)

        # 2. 안건 트리
        agenda = parse_agenda_items(text, html=html)

        # 3. 정정 요약
        correction = parse_correction_details(html) if html else None
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
        fs = parse_financial_statements(html) if html else None
        fs_highlight = _build_financial_highlight(fs) if fs else None

        # 5. 인사 하이라이트
        personnel = parse_personnel(html) if html else None
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
            if c.get("birth_date"):
                lines.append(f"  - 생년월일: {c['birth_date']}")
            if c.get("position_type"):
                lines.append(f"  - 직위: {c['position_type']}")
            if c.get("recommender"):
                lines.append(f"  - 추천인: {c['recommender']}")
            if c.get("relationship_with_major_shareholder"):
                lines.append(f"  - 최대주주 관계: {c['relationship_with_major_shareholder']}")
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
