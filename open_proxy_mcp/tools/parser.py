"""주주총회 소집공고 파싱 — 안건/비안건 분리

문서 구조:
  주주총회소집공고          ← 간략 요약
  주주총회 소집공고         ← 상세 (일시, 장소, 전자투표 등)
  I. 사외이사 등의 활동내역
  II. 최대주주등과의 거래내역
  III. 경영참고사항
    1. 사업의 개요
    2. 주주총회 목적사항별 기재사항  ← ★ 안건 상세
  IV. 사업보고서 및 감사보고서 첨부
  ※ 참고사항
"""

import re
import logging

logger = logging.getLogger(__name__)

# ── 안건 파싱 ──

# ■ 제N호 (의안)? : 제목
AGENDA_MAIN_RE = re.compile(
    r'■\s*제\s*(\d+)\s*호\s*(?:의안)?\s*[:：]\s*(.+?)(?=\n|$)'
)

# 하위 안건: 제N-M호 또는 제N-M-K호 (줄 내 또는 줄 시작)
AGENDA_SUB_RE = re.compile(
    r'제\s*(\d+)\s*-\s*(\d+)\s*(?:-\s*(\d+))?\s*호\s*(?:의안)?\s*[:：]\s*(.+?)(?=\s*(?:제\s*\d+\s*-|※|\n|$))'
)

# □ 카테고리명 (안건 카테고리 헤더)
CATEGORY_RE = re.compile(r'□\s*(.+?)(?:\n|$)')

# 조건부 의안 ※
CONDITIONAL_RE = re.compile(
    r'※\s*(제\s*\d+(?:\s*-\s*\d+)*\s*호\s*(?:의안\s*)?(?:은|는)\s*.+?)(?=\s*(?:제\s*\d+|※|\n|$))'
)

# 소집공고 첫 섹션의 안건 목차 패턴
SUMMARY_AGENDA_RE = re.compile(
    r'제\s*(\d+)\s*(?:-\s*(\d+))?\s*(?:-\s*(\d+))?\s*호\s*(?:의안)?\s*[:：]\s*(.+?)(?=\s*(?:제\s*\d+\s*(?:-\s*\d+)*\s*호|※|\d+\.\s|$))'
)


def parse_agenda_items(text: str) -> list[dict]:
    """소집공고의 '2. 주주총회 목적사항별 기재사항' 영역에서 안건 트리 파싱

    Returns:
        [{"number": "제1호", "level1": 1, "level2": None, "level3": None,
          "title": "...", "category": "재무제표의 승인",
          "source": "이사회안"|"주주제안"|None,
          "conditional": "..."|None,
          "content_summary": "..." (안건 상세 요약, 최대 500자),
          "children": [...]}]
    """
    # 1) '목적사항별 기재사항' 섹션 추출
    detail_zone = _extract_detail_zone(text)

    if not detail_zone:
        # 폴백: 소집공고 첫 섹션의 회의목적사항에서 목차만 추출
        logger.warning("'목적사항별 기재사항' 섹션 미발견 — 소집공고 목차에서 추출")
        return _parse_summary_agenda(text)

    # 2) ■ 마커로 메인 안건 추출
    main_matches = list(AGENDA_MAIN_RE.finditer(detail_zone))
    if not main_matches:
        logger.warning("■ 안건 마커 미발견 — 소집공고 목차 폴백")
        return _parse_summary_agenda(text)

    # 3) 조건부 의안 수집
    conditionals = _extract_conditionals(detail_zone)

    # 4) 각 ■ 안건 블록 파싱
    items = []
    for i, m in enumerate(main_matches):
        l1 = int(m.group(1))
        title = _clean_title(m.group(2))

        # 이 안건의 텍스트 블록: 현재 ■ ~ 다음 ■ 또는 끝
        block_start = m.end()
        block_end = main_matches[i + 1].start() if i + 1 < len(main_matches) else len(detail_zone)
        block = detail_zone[block_start:block_end]

        # □ 카테고리 (■ 앞에 나온 □)
        category = _find_category_before(detail_zone, m.start())

        # 소스 감지
        source = _detect_source(title + ' ' + block[:200])
        if source:
            title = _remove_source_tag(title)

        number = _format_number(l1, None, None)

        # 하위 안건 파싱
        children = _parse_sub_items(block, l1, conditionals)

        # 콘텐츠 요약 (하위 안건 제거 후 첫 500자)
        content_summary = _extract_content_summary(block)

        item = {
            "number": number,
            "level1": l1,
            "level2": None,
            "level3": None,
            "title": title,
            "category": category,
            "source": source,
            "conditional": conditionals.get(number),
            "content_summary": content_summary,
            "children": children,
        }
        items.append(item)

    return items


def _extract_detail_zone(text: str) -> str | None:
    """'2. 주주총회 목적사항별 기재사항' 영역 추출"""
    # 시작점
    m = re.search(r'목적사항별\s*기재사항', text)
    if not m:
        return None

    start = m.start()

    # 끝점: IV. 또는 '사업보고서' 또는 '※ 참고사항'
    end_patterns = [
        r'\nIV\.\s',
        r'\n※\s*참고사항',
        r'사업보고서\s*및\s*감사보고서',
    ]
    end = len(text)
    for pat in end_patterns:
        em = re.search(pat, text[start:])
        if em and start + em.start() < end:
            end = start + em.start()

    return text[start:end]


def _find_category_before(text: str, pos: int) -> str | None:
    """■ 마커 앞에 있는 가장 가까운 □ 카테고리를 찾기"""
    # pos 이전 500자 내에서 마지막 □ 찾기
    search_start = max(0, pos - 500)
    chunk = text[search_start:pos]
    matches = list(CATEGORY_RE.finditer(chunk))
    if matches:
        cat = matches[-1].group(1).strip()
        # 너무 긴 건 카테고리가 아님
        if len(cat) < 30:
            return cat
    return None


def _parse_sub_items(block: str, parent_l1: int, conditionals: dict) -> list[dict]:
    """블록 내의 하위 안건 (제N-M호, 제N-M-K호) 파싱 — 중복 제거 포함"""
    children = []
    mid_level = {}  # l2 -> item, for 3-level nesting
    seen = set()  # (l2, l3) 조합으로 중복 방지

    for m in AGENDA_SUB_RE.finditer(block):
        l1 = int(m.group(1))
        if l1 != parent_l1:
            continue

        l2 = int(m.group(2))
        l3 = int(m.group(3)) if m.group(3) else None

        # 중복 제거: 같은 번호가 테이블 등에서 재등장하는 경우
        key = (l2, l3)
        if key in seen:
            continue
        seen.add(key)

        title = _clean_title(m.group(4))
        source = _detect_source(title)
        if source:
            title = _remove_source_tag(title)

        number = _format_number(l1, l2, l3)

        item = {
            "number": number,
            "level1": l1,
            "level2": l2,
            "level3": l3,
            "title": title,
            "category": None,
            "source": source,
            "conditional": conditionals.get(number),
            "content_summary": None,
            "children": [],
        }

        if l3 is None:
            children.append(item)
            mid_level[l2] = item
        else:
            if l2 in mid_level:
                mid_level[l2]["children"].append(item)
            else:
                children.append(item)

    return children


def _parse_summary_agenda(text: str) -> list[dict]:
    """폴백: 소집공고 첫 섹션의 회의목적사항에서 안건 목차 추출"""
    # 회의목적사항 ~ I. 사외이사 사이
    start_m = re.search(r'회의\s*(?:의?\s*)?목적\s*사항|결의\s*사항|부의\s*안건|의결\s*사항', text)
    if not start_m:
        return []

    end_m = re.search(r'\nI\.\s|경영참고사항|전자투표|의결권\s*행사', text[start_m.end():])
    zone_end = start_m.end() + end_m.start() if end_m else min(start_m.end() + 3000, len(text))
    zone = text[start_m.start():zone_end]

    conditionals = _extract_conditionals(zone)
    flat = []

    for m in SUMMARY_AGENDA_RE.finditer(zone):
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
            "category": None,
            "source": source,
            "conditional": conditionals.get(number),
            "content_summary": None,
            "children": [],
        })

    return _build_tree(flat)


def _build_tree(flat_items: list[dict]) -> list[dict]:
    """플랫 리스트를 부모-자식 트리로 구성"""
    roots = {}
    mid_level = {}
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
    """제목 정리: 후행 기호, 번호 제거"""
    title = title.strip()
    title = re.sub(r'[\s]*[ㆍ·\.\-]\s*$', '', title)
    title = re.sub(r'\s*\d+\)\s*$', '', title)
    title = title.strip()
    return title


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


def _extract_content_summary(block: str, max_len: int = 500) -> str | None:
    """안건 블록에서 하위안건/테이블 제외한 요약 추출"""
    # 첫 500자에서 유의미한 텍스트만
    text = block[:max_len * 2]
    # 숫자만 나열된 줄 제거 (재무제표 데이터)
    lines = text.split('\n')
    meaningful = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # 순수 숫자/기호 줄 스킵
        if re.match(r'^[\d,\s\.\-\(\)]+$', stripped):
            continue
        # 하위 안건 번호 줄 스킵
        if re.match(r'^\s*제\s*\d+\s*-', stripped):
            continue
        meaningful.append(stripped)
        if len(' '.join(meaningful)) > max_len:
            break

    summary = ' '.join(meaningful)
    if len(summary) > max_len:
        summary = summary[:max_len] + "..."
    return summary if summary else None


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

    # 일시 추출 — "N. 일시" 또는 "일 시 :"
    # 줄바꿈 없이 "1. 일시 ... 2. 장소 ..." 이어지는 경우 대응
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

    # 문서 목차 — 고정 구조
    info["toc"] = _extract_document_toc(text)

    return info


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
    # 다음 대섹션 시작점 찾기
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

    # 정정신고 여부
    if re.search(r'정\s*정\s*신\s*고', text[:500]):
        toc.append("정 정 신 고 (보고)")

    toc.append("주주총회소집공고")
    toc.append("주주총회 소집공고")

    # I, II, III, IV 로마숫자 섹션
    for m in re.finditer(r'\n((?:I{1,3}|IV)\.\s*.+?)(?:\n|$)', text):
        heading = m.group(1).strip()
        if len(heading) < 60:
            toc.append(heading)

    # ※ 참고사항
    if re.search(r'※\s*참고사항', text):
        toc.append("※ 참고사항")

    return toc
