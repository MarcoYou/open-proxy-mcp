"""주주총회 소집공고 파싱 — 안건/비안건 분리

문서 구조:
  정 정 신 고 (보고)           ← 정정공고인 경우만
  주주총회소집공고              ← 간략 요약
  주주총회 소집공고             ← 상세 (일시, 장소, 회의목적사항=안건목차, 전자투표 등)
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
# 표준: 제N호 의안: 제목
AGENDA_RE = re.compile(
    r'제\s*(\d+)\s*(?:-\s*(\d+))?\s*(?:-\s*(\d+))?\s*호'
    r'\s*(?:의안)?\s*[:：]\s*'
    r'(.+?)(?=\s*(?:□?\s*제\s*\d+\s*(?:-\s*\d+)*\s*호|-\s*제\s*\d+\s*(?:-\s*\d+)*\s*호|\d+\)\s*제\s*\d+|\(제\s*\d+|※|$))'
)

# 괄호형: (제N-M-K호) 제목 (콜론 없음)
AGENDA_PAREN_RE = re.compile(
    r'\(제\s*(\d+)\s*(?:-\s*(\d+))?\s*(?:-\s*(\d+))?\s*호\)'
    r'\s*'
    r'(.+?)(?=\s*(?:\(제\s*\d+|□?\s*제\s*\d+\s*(?:-\s*\d+)*\s*호|-\s*제\s*\d+|※|$))'
)

# 조건부 의안 ※
CONDITIONAL_RE = re.compile(
    r'※\s*(제\s*\d+(?:\s*-\s*\d+)*\s*호\s*(?:의안\s*)?(?:은|는)\s*.+?)(?=\s*(?:\d+\)\s*제|제\s*\d+|※|\n|$))'
)

# '주주총회 소집공고' 섹션 끝 경계 — 다음 대섹션 시작
SECTION_END_PATTERNS = [
    r'I\s*\.\s*사외이사',
    r'Ⅰ\s*\.\s*사외이사',
    r'II\s*\.\s*최대주주',
    r'Ⅱ\s*\.\s*최대주주',
    r'III\s*\.\s*경영\s*참고',
    r'Ⅲ\s*\.\s*경영\s*참고',
    r'IV\s*\.\s*사업보고서',
    r'Ⅳ\s*\.\s*사업보고서',
    r'※\s*참고\s*사항',
]


# ── 안건 파싱 ──

def parse_agenda_items(text: str) -> list[dict]:
    """'주주총회 소집공고' 섹션의 회의목적사항에서 안건 트리 추출

    Returns:
        [{"number": "제1호", "level1": 1, "level2": None, "level3": None,
          "title": "...", "source": "이사회안"|"주주제안"|None,
          "conditional": "..."|None, "children": [...]}]
    """
    section = _extract_notice_section(text)
    if not section:
        logger.warning("'주주총회 소집공고' 섹션을 찾을 수 없음")
        return []

    zone = _extract_agenda_zone(section)
    if not zone:
        logger.warning("안건 영역(회의목적사항/결의사항/부의안건)을 찾을 수 없음")
        return []

    # 줄바꿈을 공백으로 치환 — 제목이 여러 줄에 걸치는 케이스 처리
    zone = re.sub(r'\n+', ' ', zone)

    conditionals = _extract_conditionals(zone)

    # 두 패턴(표준 + 괄호형)의 매치를 위치 순으로 합침
    matches = []
    for m in AGENDA_RE.finditer(zone):
        matches.append((m.start(), m))
    for m in AGENDA_PAREN_RE.finditer(zone):
        matches.append((m.start(), m))
    matches.sort(key=lambda x: x[0])

    flat = []
    for _, m in matches:
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


def _extract_notice_section(text: str) -> str | None:
    """문서에서 '주주총회 소집공고' 본문 섹션만 추출

    실제 본문 헤더 판별: '주주총회 소집공고' 뒤에 '(제N기' 또는 기수/정기/임시 표현이 따라옴.
    인라인 언급('소집공고로 갈음', '소집공고 조직도' 등)은 제외.
    """
    # 본문 헤더 후보: 뒤에 (제N기, 정기, 임시 등이 따라오는 것
    body_header = re.search(
        r'주주총회\s*소집\s*공고\s*\(?\s*(?:제\s*\d+\s*기|정기|임시)',
        text,
    )

    if body_header:
        section_start = body_header.start()
    else:
        # fallback: '주주총회 소집공고' 뒤에 일시/장소/회의목적사항이 나오는 것
        for m in re.finditer(r'주주총회\s*소집\s*공고', text):
            after = text[m.end():m.end()+500]
            if re.search(r'일\s*시|장\s*소|회의\s*(?:의?\s*)?목적\s*사항|부의\s*안건', after):
                section_start = m.start()
                break
        else:
            return None

    # 끝: 다음 대섹션
    section_end = len(text)
    for pat in SECTION_END_PATTERNS:
        em = re.search(pat, text[section_start:])
        if em and section_start + em.start() < section_end:
            section_end = section_start + em.start()

    return text[section_start:section_end]


def _extract_agenda_zone(section: str) -> str | None:
    """'주주총회 소집공고' 섹션 내에서 안건 나열 영역만 추출

    시작: 회의목적사항 / 결의사항 / 부의안건 / 의결사항
    끝: 섹션 끝 (이미 대섹션 경계로 잘려 있음) 또는 세부 끝점
    """
    start_patterns = [
        r'회의\s*(?:의?\s*)?목적\s*사항',
        r'결의\s*사항',
        r'부의\s*안건',
        r'의결\s*사항',
    ]
    start_pos = None
    for pat in start_patterns:
        m = re.search(pat, section)
        if m:
            if start_pos is None or m.start() < start_pos:
                start_pos = m.start()

    if start_pos is None:
        return None

    # 세부 끝점: 전자투표, 의결권 등 소섹션
    end_patterns = [
        r'\d+\.\s*경영\s*참고\s*사항',
        r'\d+\.\s*전자\s*투표',
        r'\d+\.\s*전자\s*증권',
        r'\d+\.\s*의결권\s*(?:행사|대리)',
        r'\d+\.\s*주주총회\s*참석',
        r'\d+\.\s*실질\s*주주',
        r'\d+\.\s*기\s*타\b',
        r'\d+\.\s*배당금\s*지급',
        r'\d+\.\s*제\d+기\s*(?:기말)?배당',
        r'[■□○●▶]\s*경영\s*참고\s*사항',
        r'[■□○●▶]\s*전자\s*투표',
        r'[■□○●▶]\s*의결권',
    ]
    end_pos = len(section)
    for pat in end_patterns:
        em = re.search(pat, section[start_pos:])
        if em and start_pos + em.start() < end_pos:
            end_pos = start_pos + em.start()

    return section[start_pos:end_pos]


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
    """제목 정리: 후행 기호, 번호, 특수문자 제거"""
    title = title.strip()
    title = re.sub(r'[□■○▶●①②③④⑤⑥⑦⑧⑨⑩]', '', title)  # 마커/기호/원문자 제거
    title = re.sub(r'[\s]*[ㆍ·\.\-]\s*$', '', title)
    # 후행 "N)" 제거 — 단, 열린 괄호가 앞에 있으면(괄호 안이면) 제거하지 않음
    if re.search(r'\d+\)\s*$', title) and title.count('(') <= title.count(')') - 1:
        title = re.sub(r'\s*\d+\)\s*$', '', title)
    title = re.sub(r'\s*\(\s*$', '', title)  # 끝에 매달린 여는 괄호 제거
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
