"""주주총회 소집공고 파싱 — 안건/비안건 분리

문서 구조:
  주주총회소집공고          ← 간략 요약
  주주총회 소집공고         ← 상세 (일시, 장소, 회의목적사항=안건목차, 전자투표 등)
  I. 사외이사 등의 활동내역
  II. 최대주주등과의 거래내역
  III. 경영참고사항
    1. 사업의 개요
    2. 주주총회 목적사항별 기재사항  ← 안건별 상세 (재무제표, 정관변경 테이블 등)
  IV. 사업보고서 및 감사보고서 첨부
  ※ 참고사항

안건 트리는 '주주총회 소집공고' 섹션의 회의목적사항에서 추출.
안건 상세는 'III > 2. 목적사항별 기재사항'에 있으나 별도 tool로 분리 예정.
"""

import re
import logging

logger = logging.getLogger(__name__)

# ── 정규식 ──

# 안건 번호 패턴: 제N호, 제N-M호, 제N-M-K호
# lookahead: 다음 안건번호, ※, 번호), □, - 제, 줄바꿈
AGENDA_RE = re.compile(
    r'제\s*(\d+)\s*(?:-\s*(\d+))?\s*(?:-\s*(\d+))?\s*호'
    r'\s*(?:의안)?\s*[:：]\s*'
    r'(.+?)(?=\s*(?:□?\s*제\s*\d+\s*(?:-\s*\d+)*\s*호|-\s*제\s*\d+|\d+\)\s*제|※|\n|$))'
)

# 조건부 의안 ※
CONDITIONAL_RE = re.compile(
    r'※\s*(제\s*\d+(?:\s*-\s*\d+)*\s*호\s*(?:의안\s*)?(?:은|는)\s*.+?)(?=\s*(?:\d+\)\s*제|제\s*\d+|※|\n|$))'
)


# ── 안건 파싱 ──

def parse_agenda_items(text: str) -> list[dict]:
    """'주주총회 소집공고' 섹션의 회의목적사항에서 안건 트리 추출

    Returns:
        [{"number": "제1호", "level1": 1, "level2": None, "level3": None,
          "title": "...", "source": "이사회안"|"주주제안"|None,
          "conditional": "..."|None, "children": [...]}]
    """
    zone = _extract_agenda_zone(text)
    if not zone:
        logger.warning("안건 영역(회의목적사항/결의사항/부의안건)을 찾을 수 없음")
        return []

    conditionals = _extract_conditionals(zone)
    flat = []

    for m in AGENDA_RE.finditer(zone):
        l1 = int(m.group(1))
        l2 = int(m.group(2)) if m.group(2) else None
        l3 = int(m.group(3)) if m.group(3) else None
        title = _clean_title(m.group(4))

        source = _detect_source(title)
        if source:
            title = _remove_source_tag(title)

        number = _format_number(l1, l2, l3)

        flat.append({
            "number": number,
            "level1": l1,
            "level2": l2,
            "level3": l3,
            "title": title,
            "source": source,
            "conditional": conditionals.get(number),
            "children": [],
        })

    if not flat:
        logger.warning("의안 패턴 매치 없음")
        return []

    return _build_tree(flat)


def _extract_agenda_zone(text: str) -> str | None:
    """'주주총회 소집공고' 섹션에서 안건이 나열된 영역 추출

    시작: 회의목적사항 / 결의사항 / 부의안건 / 의결사항
    끝: 'N. 경영참고사항' / 'N. 전자투표' / 'N. 의결권' / 'I.' / 문서 끝
    """
    # 시작점
    start_patterns = [
        r'회의\s*(?:의?\s*)?목적\s*사항',
        r'결의\s*사항',
        r'부의\s*안건',
        r'의결\s*사항',
    ]
    start_pos = None
    for pat in start_patterns:
        m = re.search(pat, text)
        if m:
            if start_pos is None or m.start() < start_pos:
                start_pos = m.start()

    if start_pos is None:
        return None

    # 끝점: 안건 나열 이후의 다음 섹션
    end_patterns = [
        r'\n\d+\.\s*경영참고사항',
        r'\n\d+\.\s*전자\s*투표',
        r'\n\d+\.\s*전자\s*증권',
        r'\n\d+\.\s*의결권\s*행사',
        r'\n\d+\.\s*주주총회\s*참석',
        r'\n\d+\.\s*실질주주',
        r'\n\d+\.\s*기\s*타',
        r'\nI\.\s',
    ]
    end_pos = min(start_pos + 5000, len(text))  # 최대 5000자 제한 (안건 목차 영역)
    for pat in end_patterns:
        em = re.search(pat, text[start_pos:])
        if em and start_pos + em.start() < end_pos:
            end_pos = start_pos + em.start()

    return text[start_pos:end_pos]


def _build_tree(flat_items: list[dict]) -> list[dict]:
    """플랫 리스트를 부모-자식 트리로 구성

    제2-1호 → 제2호의 child
    제3-1-2호 → 제3-1호의 child
    """
    roots = {}      # l1 -> item
    mid_level = {}   # (l1, l2) -> item
    tree = []

    for item in flat_items:
        l1, l2, l3 = item["level1"], item["level2"], item["level3"]

        if l2 is None and l3 is None:
            roots[l1] = item
            tree.append(item)
        elif l3 is None:
            if l1 in roots:
                roots[l1]["children"].append(item)
            else:
                tree.append(item)
            mid_level[(l1, l2)] = item
        else:
            if (l1, l2) in mid_level:
                mid_level[(l1, l2)]["children"].append(item)
            elif l1 in roots:
                roots[l1]["children"].append(item)
            else:
                tree.append(item)

    return tree


# ── 비안건 파싱 ──

def parse_meeting_info(text: str) -> dict:
    """소집공고 텍스트에서 비안건 정보를 추출"""
    info = {
        "meeting_type": None,
        "meeting_term": None,
        "is_correction": False,
        "datetime": None,
        "location": None,
        "report_items": [],
        "electronic_voting": None,
        "proxy_voting": None,
        "online_broadcast": None,
        "reference_materials": None,
        "toc": [],
    }

    # 정정공고 여부
    if re.search(r'정\s*정\s*신\s*고|기재\s*정정', text[:500]):
        info["is_correction"] = True

    # 정기/임시 구분
    if re.search(r'임시\s*주주총회', text):
        info["meeting_type"] = "임시"
    elif re.search(r'정기\s*주주총회|정기\)', text):
        info["meeting_type"] = "정기"

    # 기수 추출 (제N기)
    m = re.search(r'(제\s*\d+\s*기)', text)
    if m:
        info["meeting_term"] = re.sub(r'\s+', '', m.group(1))

    # 일시 추출
    m = re.search(r'\d+\.\s*일\s*시\s*[:：]?\s*(.+?)(?=\s*\d+\.\s*장\s*소|\n\s*\d+\.\s|\n|$)', text)
    if not m:
        m = re.search(r'일\s*시\s*[:：]\s*(.+?)(?=\n|$)', text)
    if m:
        info["datetime"] = m.group(1).strip()

    # 장소 추출
    m = re.search(r'\d+\.\s*장\s*소\s*[:：]?\s*(.+?)(?=\s*\d+\.\s*(?:회의|보고|전자|의결|경영)|\n\s*\d+\.\s|\n|$)', text)
    if not m:
        m = re.search(r'장\s*소\s*[:：]\s*(.+?)(?=\n|$)', text)
    if m:
        info["location"] = m.group(1).strip()

    # 보고사항 추출
    report_m = re.search(
        r'보고\s*(?:사항|안건)\s*[:：]?\s*(.+?)(?=\n\s*[나②][\.\s]|결의|부의|의결|\n\n)',
        text, re.DOTALL
    )
    if report_m:
        report_text = report_m.group(1)
        items = re.split(r'[,，]\s*|\n\s*-\s*', report_text)
        info["report_items"] = [_clean_report_item(i) for i in items
                                if _clean_report_item(i) and len(_clean_report_item(i)) > 2]

    # 전자투표 섹션
    info["electronic_voting"] = _extract_section(text, r'\d+\.\s*전자\s*투표', limit=1500)

    # 의결권 행사 방법
    info["proxy_voting"] = _extract_section(text, r'\d+\.\s*의결권\s*(?:행사|대리)', limit=1500)

    # 온라인 중계
    info["online_broadcast"] = _extract_section(text, r'\d+\.\s*온라인\s*중계', limit=1000)

    # 경영참고사항 비치
    info["reference_materials"] = _extract_section(text, r'경영참고사항의?\s*비치', limit=500)

    # 문서 목차
    info["toc"] = _extract_document_toc(text)

    return info


# ── 유틸리티 ──

def _extract_conditionals(text: str) -> dict[str, str]:
    """※ 조건부 의안 텍스트를 의안 번호별로 매핑"""
    result = {}
    for m in CONDITIONAL_RE.finditer(text):
        cond_text = m.group(1).strip()
        num_match = re.search(r'제\s*(\d+)(?:\s*-\s*(\d+))?(?:\s*-\s*(\d+))?\s*호', cond_text)
        if num_match:
            l1 = int(num_match.group(1))
            l2 = int(num_match.group(2)) if num_match.group(2) else None
            l3 = int(num_match.group(3)) if num_match.group(3) else None
            number = _format_number(l1, l2, l3)
            result[number] = cond_text
    return result


def _format_number(l1: int, l2: int | None, l3: int | None) -> str:
    if l3 is not None:
        return f"제{l1}-{l2}-{l3}호"
    elif l2 is not None:
        return f"제{l1}-{l2}호"
    else:
        return f"제{l1}호"


def _clean_title(title: str) -> str:
    """제목 정리: 후행 기호, 번호, □ 제거"""
    title = title.strip()
    title = re.sub(r'[□■]', '', title)  # □■ 마커 제거
    title = re.sub(r'[\s]*[ㆍ·\.\-]\s*$', '', title)
    title = re.sub(r'\s*\d+\)\s*$', '', title)
    return title.strip()


def _detect_source(text: str) -> str | None:
    if re.search(r'주주\s*제안', text):
        return '주주제안'
    if re.search(r'이사회\s*안', text):
        return '이사회안'
    return None


def _remove_source_tag(title: str) -> str:
    title = re.sub(r'\s*\(?\s*주주\s*제안[^)]*\)?\s*', '', title)
    title = re.sub(r'\s*\(?\s*이사회\s*안[^)]*\)?\s*', '', title)
    return title.strip()


def _clean_report_item(text: str) -> str:
    """보고사항 항목 정리"""
    text = text.strip()
    text = re.sub(r'^-\s*', '', text)
    text = re.sub(r'\s*[나다][\.\s]*$', '', text)
    return text.strip()


def _extract_section(text: str, heading_pattern: str, limit: int = 1000) -> str | None:
    """특정 키워드로 시작하는 섹션의 텍스트를 추출"""
    m = re.search(heading_pattern, text)
    if not m:
        return None

    start = m.start()
    remaining = text[m.end():]
    next_section = re.search(r'\n\d+\.\s+[가-힣]|\n[IVX]+\.\s', remaining)
    if next_section:
        end = m.end() + next_section.start()
    else:
        end = min(start + limit, len(text))

    section_text = text[start:end].strip()
    if len(section_text) > limit:
        section_text = section_text[:limit] + "..."
    return section_text


def _extract_document_toc(text: str) -> list[str]:
    """문서 전체 목차 추출"""
    toc = []

    if re.search(r'정\s*정\s*신\s*고', text[:500]):
        toc.append("정 정 신 고 (보고)")

    toc.append("주주총회소집공고")
    toc.append("주주총회 소집공고")

    for m in re.finditer(r'\n((?:I{1,3}|IV)\.\s*.+?)(?:\n|$)', text):
        heading = m.group(1).strip()
        if len(heading) < 60:
            toc.append(heading)

    if re.search(r'※\s*참고사항', text):
        toc.append("※ 참고사항")

    return toc
