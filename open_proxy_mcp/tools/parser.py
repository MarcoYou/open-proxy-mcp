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
안건 상세는 'III > 2. 목적사항별 기재사항'에서 BeautifulSoup으로 파싱.
"""

import re
import logging
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import warnings

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

logger = logging.getLogger(__name__)

# lxml이 있으면 사용 (30% 빠름), 없으면 html.parser fallback
try:
    import lxml  # noqa: F401
    _BS4_PARSER = "lxml"
except ImportError:
    _BS4_PARSER = "html.parser"

# ── 정규식 ──

# 안건 번호 패턴: 제N호, 제N-M호, 제N-M-K호
# 공통 lookahead — 안건 경계 패턴
# 제N호, -제N호, N)제N호, (제N호, N-M호(제 없음), ※, 테이블 헤더, 정관변경 헤더
_AGENDA_BOUNDARY = (
    r'(?='
    r'\s*(?:'
    r'[□◎●○▶]?\s*제\s*\d+\s*(?:-\s*\d+)*\s*호'  # 제N호, □제N호
    r'|-\s*제\s*\d+\s*(?:-\s*\d+)*\s*호'           # -제N호
    r'|\d+\)\s*제\s*\d+'                            # N)제N호
    r'|\(제\s*\d+'                                   # (제N호
    r'|[·ㆍ]?\s*\d+-\d+호'                          # N-M호 (제 없음), ·N-M호
    r'|성명\s*(?:생년월일|출생)'                      # 후보자 테이블 헤더
    r'|후보자\s*(?:성명|선임직)'                      # 후보자 테이블 헤더
    r'|선임직\s*성명'                                 # 후보자 테이블 헤더
    r'|변경전\s*내용'                                 # 정관변경 비교 테이블
    r'|현행\s+개정'                                   # 정관변경 비교 테이블
    r'|구분\s+변경전'                                 # 정관변경 비교 테이블
    r'|※'
    r'|$'
    r'))'
)

# 표준 (콜론 있음): 제N호 의안: 제목 / 제N호 안건: 제목
AGENDA_RE = re.compile(
    r'제\s*(\d+)\s*(?:-\s*(\d+))?\s*(?:-\s*(\d+))?\s*호'
    r'\s*(?:의안|안건)?\s*[:：]\s*'
    r'(.+?)' + _AGENDA_BOUNDARY
)

# 콜론 없음: 제N호 의안 제목 (의안 키워드 필수, lookahead 더 엄격)
AGENDA_NO_COLON_RE = re.compile(
    r'제\s*(\d+)\s*(?:-\s*(\d+))?\s*(?:-\s*(\d+))?\s*호'
    r'\s*의안\s+'
    r'(.+?)' + _AGENDA_BOUNDARY
)

# 괄호형: (제N-M-K호) 제목 (콜론 없음)
AGENDA_PAREN_RE = re.compile(
    r'\(제\s*(\d+)\s*(?:-\s*(\d+))?\s*(?:-\s*(\d+))?\s*호\)'
    r'\s*'
    r'(.+?)' + _AGENDA_BOUNDARY
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

def parse_agenda_items(text: str, html: str = "") -> list[dict]:
    """'주주총회 소집공고' 섹션의 회의목적사항에서 안건 트리 추출

    html이 제공되면 bs4로 섹션 경계를 찾고, 없으면 기존 regex 방식 사용.

    Returns:
        [{"number": "제1호", "level1": 1, "level2": None, "level3": None,
          "title": "...", "source": "이사회안"|"주주제안"|None,
          "conditional": "..."|None, "children": [...]}]
    """
    zone = None

    # 1) bs4 기반 추출 시도
    if html:
        zone = _extract_agenda_zone_html(html)

    # 2) Fallback: 기존 plain text regex
    if not zone:
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

    # 세 패턴(표준 + 콜론없음 + 괄호형)의 매치를 위치 순으로 합침
    matches = []
    seen_positions = set()
    for m in AGENDA_RE.finditer(zone):
        matches.append((m.start(), m))
        seen_positions.add(m.start())
    for m in AGENDA_NO_COLON_RE.finditer(zone):
        if m.start() not in seen_positions:
            matches.append((m.start(), m))
            seen_positions.add(m.start())
    for m in AGENDA_PAREN_RE.finditer(zone):
        if m.start() not in seen_positions:
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


def validate_agenda_result(items: list[dict]) -> bool:
    """파싱 결과가 유효한지 검사. False면 LLM fallback 대상."""
    if not items:
        return False

    # 같은 number 중복 (정정공고 잔류 등)
    numbers = []
    def collect(tree):
        for item in tree:
            numbers.append(item["number"])
            collect(item.get("children", []))
    collect(items)
    if len(numbers) != len(set(numbers)):
        return False

    # 제목 200자 초과 (zone 텍스트 딸려옴)
    def check_title(tree):
        for item in tree:
            if len(item.get("title", "")) > 200:
                return False
            if not check_title(item.get("children", [])):
                return False
        return True
    if not check_title(items):
        return False

    return True


def _extract_agenda_zone_html(html: str) -> str | None:
    """HTML에서 bs4로 소집공고 섹션의 안건 영역 텍스트를 추출

    DART 문서 구조:
      <section-1>
        <title>주주총회 소집공고</title>
        <p>... 일시, 장소, 회의목적사항 ... 제1호 ... 제2호 ...</p>
        <p>... 전자투표, 의결권 ...</p>

    bs4로 <section-1> 범위를 정확히 잡아서 _extract_agenda_zone에 넘김.
    text 방식보다 섹션 경계가 정확하여 end_pattern 오발동 방지.
    """
    soup = BeautifulSoup(html, _BS4_PARSER)

    # '주주총회 소집공고' 섹션 찾기 — 마지막 매칭 선택 (정정 preamble 건너뜀)
    notice_section = None
    for el in soup.find_all('title'):
        title_text = el.get_text().strip()
        if '주주총회' in title_text and '소집' in title_text and '공고' in title_text:
            parent = el.parent
            section_text = parent.get_text()
            if re.search(r'일\s*시|장\s*소|회의\s*(?:의?\s*)?목적\s*사항|부의\s*(?:안건|사항)', section_text):
                notice_section = parent

    if not notice_section:
        return None

    section_text = notice_section.get_text()
    return _extract_agenda_zone(section_text)


def _extract_notice_section(text: str) -> str | None:
    """문서에서 '주주총회 소집공고' 본문 섹션만 추출

    실제 본문 헤더 판별: '주주총회 소집공고' 뒤에 '(제N기' 또는 기수/정기/임시 표현이 따라옴.
    인라인 언급('소집공고로 갈음', '소집공고 조직도' 등)은 제외.
    """
    # '주주총회 소집공고' 뒤에 일시/장소/회의목적사항이 나오는 것이 실제 본문 헤더
    # 유효 후보: 뒤에 일시/장소/회의목적사항이 따라오는 헤더
    # 여러 후보 중 마지막 것 선택 (정정 preamble 안 가짜 헤더는 앞쪽, 실제 본문은 뒤쪽)
    section_start = None
    for m in re.finditer(r'주주총회\s*소집\s*공고', text):
        after = text[m.end():m.end()+500]
        if re.search(r'일\s*시|장\s*소|회의\s*(?:의?\s*)?목적\s*사항|부의\s*(?:안건|사항)', after):
            section_start = m.start()

    if section_start is None:
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
        r'회의\s*(?:의?\s*)?(?:보고\s*)?목적\s*사항',
        r'결의\s*사항',
        r'부의\s*안건',
        r'부의\s*사항',
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
        # "N. 주주총회 소집통지/공고사항" 패턴
        r'\d+[\.\s]*주주총회\s*소집\s*(?:통지|공고)',
        # "N. 전자투표에 관한 사항" 패턴 (번호 뒤 마침표 없는 변형 포함)
        r'\d+[\.\s]*전자\s*투표\s*에\s*관한',
        # "N. 배당예정 내역" / "N. 이익배당 예정"
        r'\d+[\.\s]*(?:배당\s*예정|이익\s*배당)',
        # "N. 우선주의 의결권"
        r'\d+[\.\s]*우선주의?\s*의결권',
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

def parse_meeting_info(text: str, html: str = "") -> dict:
    """소집공고에서 비안건 정보를 추출

    html이 제공되면 bs4로 소집공고 섹션을 정확히 잡아서 파싱.
    없으면 기존 text regex 방식 사용.
    """
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

    # bs4로 소집공고 섹션 텍스트 추출 (범위가 정확)
    section_text = None
    if html:
        section_text = _extract_notice_section_html(html)
    if not section_text:
        section_text = text  # fallback: 전체 텍스트

    # 정정공고 여부 (전체 텍스트에서 확인)
    if re.search(r'정\s*정\s*신\s*고|기재\s*정정', text[:500]):
        info["is_correction"] = True

    # 정기/임시 구분
    if re.search(r'임시\s*주주총회', section_text):
        info["meeting_type"] = "임시"
    elif re.search(r'정기\s*주주총회|정기\)', section_text):
        info["meeting_type"] = "정기"

    # 기수 추출 (제N기)
    m = re.search(r'(제\s*\d+\s*기)', section_text)
    if m:
        info["meeting_term"] = re.sub(r'\s+', '', m.group(1))

    # 일시 추출
    m = re.search(r'\d+\.\s*일\s*시\s*[:：]?\s*(.+?)(?=\s*\d+\.\s*장\s*소|\n\s*\d+\.\s|\n|$)', section_text)
    if not m:
        m = re.search(r'일\s*시\s*[:：]\s*(.+?)(?=\n|$)', section_text)
    if m:
        info["datetime"] = m.group(1).strip()

    # 장소 추출
    m = re.search(r'\d+\.\s*장\s*소\s*[:：]?\s*(.+?)(?=\s*\d+\.\s*(?:회의|보고|전자|의결|경영)|\n\s*\d+\.\s|\n|$)', section_text)
    if not m:
        m = re.search(r'장\s*소\s*[:：]\s*(.+?)(?=\n|$)', section_text)
    if m:
        info["location"] = m.group(1).strip()

    # 보고사항 추출
    report_m = re.search(
        r'보고\s*(?:사항|안건)\s*[:：]?\s*(.+?)(?=\n\s*[나②][\.\s]|결의|부의|의결|\n\n)',
        section_text, re.DOTALL
    )
    if report_m:
        report_text = report_m.group(1)
        items = re.split(r'[,，]\s*|\n\s*-\s*', report_text)
        info["report_items"] = [_clean_report_item(i) for i in items
                                if _clean_report_item(i) and len(_clean_report_item(i)) > 2]

    # 전자투표 섹션
    info["electronic_voting"] = _extract_section(section_text, r'\d+\.\s*전자\s*투표', limit=1500)

    # 의결권 행사 방법
    info["proxy_voting"] = _extract_section(section_text, r'\d+\.\s*의결권\s*(?:행사|대리)', limit=1500)

    # 온라인 중계
    info["online_broadcast"] = _extract_section(section_text, r'\d+\.\s*온라인\s*중계', limit=1000)

    # 경영참고사항 비치
    info["reference_materials"] = _extract_section(section_text, r'경영참고사항의?\s*비치', limit=500)

    # 문서 목차 (전체 텍스트에서)
    info["toc"] = _extract_document_toc(text)

    return info


def _extract_notice_section_html(html: str) -> str | None:
    """HTML에서 bs4로 소집공고 섹션의 전체 텍스트를 추출

    _extract_agenda_zone_html과 달리, 안건 영역이 아닌 섹션 전체 반환.
    일시/장소/전자투표/의결권 등 비안건 정보가 이 범위 안에 있음.
    """
    soup = BeautifulSoup(html, _BS4_PARSER)

    notice_section = None
    for el in soup.find_all('title'):
        title_text = el.get_text().strip()
        if '주주총회' in title_text and '소집' in title_text and '공고' in title_text:
            parent = el.parent
            section_text = parent.get_text()
            if re.search(r'일\s*시|장\s*소|회의\s*(?:의?\s*)?목적\s*사항|부의\s*(?:안건|사항)', section_text):
                notice_section = parent

    if not notice_section:
        return None

    text = notice_section.get_text()
    # HTML get_text()의 연속 공백 정규화 (줄바꿈은 보존)
    text = re.sub(r'[^\S\n]+', ' ', text)
    return text


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
    title = re.sub(r'\s*[나다라마바사아]\s*$', '', title)  # 다음 안건의 가나다 접두사 잔류 제거
    title = re.sub(r'\s*[ㄴㅇ]\s*$', '', title)  # 단일 자음 잔류 제거 (ㄴ, ㅇ)
    title = re.sub(r'\s+o\s*$', '', title)  # 목록 마커 'o' 잔류 제거
    # 후행 "N)" 제거 — 단, 열린 괄호가 앞에 있으면(괄호 안이면) 제거하지 않음
    if re.search(r'\d+\)\s*$', title) and title.count('(') <= title.count(')') - 1:
        title = re.sub(r'\s*\d+\)\s*$', '', title)
    title = re.sub(r'\s*[\(\[]\s*$', '', title)  # 끝에 매달린 여는 괄호/대괄호 제거
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


# ── 안건 상세 파싱 (HTML 기반) ──

AGENDA_DETAIL_RE = re.compile(
    r'[■□●▶(（]?\s*제\s*(\d+)\s*(?:-\s*(\d+))?\s*(?:-\s*(\d+))?\s*호'
    r'\s*(?:의안|안건)?\s*[)）:：]?\s*(.+)',
    re.DOTALL,
)

SUBSECTION_RE = re.compile(
    r'^([가나다라마바사아자차카타파하])\.\s*(.+)'
)


def parse_agenda_details(html: str) -> list[dict]:
    """HTML에서 '목적사항별 기재사항' 섹션의 안건별 상세를 파싱

    DART 문서 XML 구조:
      <section-2>  (목적사항별 기재사항)
        <library>  (카테고리별 묶음)
          <section-3>
            <title> □ 카테고리명
            <p> ■ 제N호 : 제목
            <p> 가. 서브섹션
            <table> 테이블 데이터
            ...

    Returns:
        [{"number": "제1호", "title": "...", "category": "...",
          "sections": [{"heading": "가. ...", "blocks": [...]}]}]
    """
    soup = BeautifulSoup(html, _BS4_PARSER)

    # '목적사항별 기재사항' 섹션 찾기
    detail_section = None
    for el in soup.find_all('title'):
        if '목적사항별' in (el.get_text() or ''):
            detail_section = el.parent
            break

    if not detail_section:
        logger.warning("'목적사항별 기재사항' 섹션을 찾을 수 없음")
        return []

    # library 태그들에서 안건 파싱
    agendas = []
    for lib in detail_section.find_all('library'):
        parsed = _parse_library_block(lib)
        agendas.extend(parsed)

    return agendas


def _parse_library_block(lib) -> list[dict]:
    """하나의 <library> 블록에서 안건들을 추출

    하나의 library에 여러 안건이 있을 수 있음 (예: 제5호, 제6호가 같은 카테고리)
    """
    # section-3 이 있으면 그 안에서, 없으면 library 직접
    container = lib.find('section-3') or lib

    category = None
    title_el = container.find('title')
    if title_el:
        cat_text = title_el.get_text().strip()
        cat_text = re.sub(r'^[□■○●▶]\s*', '', cat_text)
        category = cat_text

    # 자식 요소들을 순회하면서 안건별로 분리
    agendas = []
    current_agenda = None
    current_section = None

    for child in container.children:
        if not hasattr(child, 'name'):
            # NavigableString — 의미 있는 텍스트면 처리
            text = child.strip()
            if text and current_section is not None:
                # ※ 조건부 의안 등
                if text.startswith('※'):
                    current_section["blocks"].append({"type": "note", "content": text})
                elif text:
                    current_section["blocks"].append({"type": "text", "content": text})
            continue

        if child.name == 'title':
            continue

        if child.name == 'pgbrk':
            continue

        text = child.get_text().strip()
        if not text:
            continue

        # <p> 태그 처리 — 내부에 여러 논리 요소가 합쳐질 수 있으므로
        # 줄 단위로 분리하여 각각 처리
        if child.name == 'p':
            lines = _split_p_lines(child)
            for line in lines:
                current_agenda, current_section = _process_text_line(
                    line, current_agenda, current_section, agendas, category
                )
            continue

        # 안건 시작 전 요소는 무시
        if current_section is None:
            continue

        # <table> — 테이블 변환
        if child.name == 'table':
            md_table = _table_to_markdown(child)
            if md_table:
                # 단일 셀 테이블은 _table_to_markdown이 plain text로 반환
                is_md_table = md_table.startswith('|')
                block_type = "table" if is_md_table else "text"
                current_section["blocks"].append({"type": block_type, "content": md_table})
            continue

        # 기타 블록 요소 (section-4 등 — 첨부 확인서, 간혹 다음 안건이 포함됨)
        if child.name and child.name.startswith('section'):
            sub_text = child.get_text().strip()
            if not sub_text:
                continue
            # section-4 안에 다음 안건(제N호)이 포함된 경우 — 내부 자식을 개별 파싱
            if re.search(r'제\s*\d+\s*호', sub_text):
                for sub_child in child.children:
                    if not hasattr(sub_child, 'name'):
                        continue
                    if sub_child.name == 'p':
                        lines = _split_p_lines(sub_child)
                        for line in lines:
                            current_agenda, current_section = _process_text_line(
                                line, current_agenda, current_section, agendas, category
                            )
                    elif sub_child.name == 'table' and current_section is not None:
                        md_table = _table_to_markdown(sub_child)
                        if md_table:
                            is_md_table = md_table.startswith('|')
                            block_type = "table" if is_md_table else "text"
                            current_section["blocks"].append({"type": block_type, "content": md_table})
            elif current_section is not None:
                current_section["blocks"].append({"type": "text", "content": sub_text})
            continue

    # 빈 섹션 정리
    for agenda in agendas:
        agenda["sections"] = [
            s for s in agenda["sections"]
            if s["blocks"] or s["heading"]
        ]

    return agendas


def _table_to_markdown(table_el) -> str:
    """<table> 요소를 마크다운 테이블로 변환

    단일 셀 테이블(텍스트 블록을 테이블로 감싼 경우)은 텍스트로 반환.
    """
    rows = table_el.find_all('tr')
    if not rows:
        return ""

    # 행/열 데이터 추출
    table_data = []
    for row in rows:
        cells = row.find_all(['td', 'th'])
        row_data = []
        for cell in cells:
            text = cell.get_text().strip()
            # 셀 내 줄바꿈을 공백으로
            text = re.sub(r'\s*\n\s*', ' ', text)
            # colspan 처리
            colspan = int(cell.get('colspan', 1) or 1)
            row_data.append(text)
            for _ in range(colspan - 1):
                row_data.append('')
        table_data.append(row_data)

    if not table_data:
        return ""

    # 단일 셀 테이블 → 텍스트 반환 (테이블로 감싼 텍스트 블록)
    if len(table_data) == 1 and len(table_data[0]) == 1:
        return table_data[0][0]

    # 열 수 통일
    max_cols = max(len(row) for row in table_data)
    for row in table_data:
        while len(row) < max_cols:
            row.append('')

    # 빈 열 제거
    non_empty_cols = []
    for col_idx in range(max_cols):
        if any(row[col_idx].strip() for row in table_data):
            non_empty_cols.append(col_idx)

    if not non_empty_cols:
        return ""

    table_data = [[row[i] for i in non_empty_cols] for row in table_data]
    max_cols = len(non_empty_cols)

    # 마크다운 테이블 생성
    # 파이프 내 | 이스케이프
    def escape_pipe(s):
        return s.replace('|', '\\|')

    lines = []
    # 헤더 (첫 행)
    header = table_data[0]
    lines.append('| ' + ' | '.join(escape_pipe(c) for c in header) + ' |')
    lines.append('| ' + ' | '.join('---' for _ in header) + ' |')

    # 데이터 행
    for row in table_data[1:]:
        lines.append('| ' + ' | '.join(escape_pipe(c) for c in row) + ' |')

    return '\n'.join(lines)


def _split_p_lines(p_el) -> list[str]:
    """<p> 요소 내부를 논리적 줄로 분리

    DART 문서에서 하나의 <p> 안에 여러 항목이 합쳐지는 경우 처리:
    - ■ 제N호 뒤에 - 제N-1호가 이어지는 경우
    - 여러 - 제N-M호가 한 <p>에 합쳐진 경우
    - 가. 서브섹션이 <p> 끝에 붙어있는 경우
    """
    # get_text의 separator로 줄바꿈 보존
    raw = p_el.get_text(separator='\n').strip()
    if not raw:
        return []

    # ■ 제N호 패턴 뒤의 줄바꿈을 공백으로 합침 (제목이 여러 줄에 걸치는 경우)
    raw = re.sub(
        r'([■□●▶]\s*제\s*\d+\s*(?:-\s*\d+)*\s*호\s*(?:의안)?\s*[:：]?)\s*\n\s*\n?\s*',
        r'\1 ', raw
    )

    lines = []
    for line in raw.split('\n'):
        line = line.strip()
        if not line:
            continue

        # "... - 제N호" 패턴이 중간에 있으면 분리
        # 예: "- 제2-6호 : 퇴직금규정- 제2-7호 : 자기주식"
        parts = re.split(r'(?=-\s*제\s*\d+)', line)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            # ■ 제N호 뒤에 - 제N-M호가 이어지는 경우 분리
            agenda_then_sub = re.match(
                r'([■□●▶]\s*제\s*\d+\s*(?:-\s*\d+)*\s*호\s*(?:의안)?\s*[:：]?\s*.+?)'
                r'\s*(-\s*제\s*\d+.+)',
                part
            )
            if agenda_then_sub:
                lines.append(agenda_then_sub.group(1).strip())
                # 나머지 부분 재귀적 분리
                remainder = agenda_then_sub.group(2).strip()
                for sub in re.split(r'(?=-\s*제\s*\d+)', remainder):
                    sub = sub.strip()
                    if sub:
                        lines.append(sub)
            else:
                # ※ 가 중간에 나오면 분리
                note_split = re.split(r'(?=※)', part)
                for ns in note_split:
                    ns = ns.strip()
                    if not ns:
                        continue
                    # 가. 나. 등이 끝에 붙어있으면 분리
                    sub_heading = re.search(r'([가나다라마바사아자차카타파하])\.\s+(.+)$', ns)
                    if sub_heading and not SUBSECTION_RE.match(ns):
                        before = ns[:sub_heading.start()].strip()
                        if before:
                            lines.append(before)
                        lines.append(sub_heading.group(0).strip())
                    else:
                        lines.append(ns)

    return lines


def _process_text_line(
    line: str,
    current_agenda: dict | None,
    current_section: dict | None,
    agendas: list[dict],
    category: str | None,
) -> tuple[dict | None, dict | None]:
    """한 줄의 텍스트를 처리하여 안건/섹션 상태를 업데이트"""

    # ■ 제N호 — 새 안건 시작
    agenda_match = AGENDA_DETAIL_RE.match(line)
    if agenda_match:
        l1 = int(agenda_match.group(1))
        l2 = int(agenda_match.group(2)) if agenda_match.group(2) else None
        l3 = int(agenda_match.group(3)) if agenda_match.group(3) else None
        number = _format_number(l1, l2, l3)
        title = agenda_match.group(4).strip()

        current_agenda = {
            "number": number,
            "title": title,
            "category": category,
            "sections": [],
        }
        agendas.append(current_agenda)
        current_section = {"heading": None, "blocks": []}
        current_agenda["sections"].append(current_section)
        return current_agenda, current_section

    if current_agenda is None:
        return current_agenda, current_section

    # 가. 나. 다. — 서브섹션
    sub_match = SUBSECTION_RE.match(line)
    if sub_match:
        current_section = {
            "heading": line,
            "blocks": [],
        }
        current_agenda["sections"].append(current_section)
        return current_agenda, current_section

    # ※ 노트
    if line.startswith('※'):
        current_section["blocks"].append({"type": "note", "content": line})
        return current_agenda, current_section

    # 하위 안건 목록 (- 제2-1호 : ...)
    if re.match(r'^-\s*제\s*\d+', line):
        current_section["blocks"].append({"type": "text", "content": line})
        return current_agenda, current_section

    # 일반 텍스트
    if line:
        current_section["blocks"].append({"type": "text", "content": line})

    return current_agenda, current_section


def validate_agenda_details(details: list[dict]) -> bool:
    """상세 파싱 결과 유효성 검사"""
    if not details:
        return False
    # 최소 1개 안건에 sections이 있어야
    return any(d.get("sections") for d in details)


# ── 재무제표 파싱 (HTML 기반) ──

# 재무제표 테이블 식별 키워드
_FS_BALANCE_SHEET = re.compile(r'재무상태표|대차대조표')
_FS_INCOME_STMT = re.compile(r'손익계산서|포괄손익')
_FS_CONSOLIDATED = re.compile(r'연결')
_FS_SEPARATE = re.compile(r'별도|개별')
_FS_UNIT = re.compile(r'\(단위\s*[:：]?\s*(.+?)\)')
_FS_PERIOD = re.compile(r'(제\s*\d+\s*\(?\s*(?:당|전)\s*\)?\s*기|(?:20)?\d{2,4}\s*년)')


def parse_financial_statements(html: str) -> dict:
    """HTML에서 재무제표(재무상태표, 손익계산서) 구조화 추출

    목적사항별 기재사항 > 재무제표 영역에서:
    - 연결/별도 구분
    - 재무상태표, 손익계산서 테이블 추출
    - 단위, 기간 라벨 메타데이터 포함

    Returns:
        {"consolidated": {"balance_sheet": {...}, "income_statement": {...}},
         "separate": {"balance_sheet": {...}, "income_statement": {...}}}
    """
    soup = BeautifulSoup(html, _BS4_PARSER)

    # 목적사항별 기재사항 섹션 찾기
    detail_section = None
    for el in soup.find_all('title'):
        if '목적사항별' in (el.get_text() or ''):
            detail_section = el.parent
            break

    if not detail_section:
        logger.warning("재무제표 파싱: 목적사항별 기재사항 섹션을 찾을 수 없음")
        return _empty_financial_result()

    # 재무제표 library 찾기 (보통 첫 번째 library)
    fs_container = None
    for lib in detail_section.find_all('library'):
        text = lib.get_text()[:200]
        if _FS_BALANCE_SHEET.search(text) or '재무제표' in text:
            fs_container = lib.find('section-3') or lib
            break

    if not fs_container:
        logger.warning("재무제표 파싱: 재무제표 library를 찾을 수 없음")
        return _empty_financial_result()

    # 데이터 테이블 수집 — 행 5개 이상, 첫 행에 '과목' 포함
    result = {
        "consolidated": {"balance_sheet": None, "income_statement": None},
        "separate": {"balance_sheet": None, "income_statement": None},
    }

    # 현재 컨텍스트 추적
    is_consolidated = True  # 기본값: 연결
    current_stmt_type = None  # 'balance_sheet' or 'income_statement'

    for child in fs_container.descendants:
        if not hasattr(child, 'name') or not child.name:
            continue

        text = child.get_text().strip()

        # <p> 헤딩으로 컨텍스트 갱신
        if child.name == 'p' and text:
            if _FS_SEPARATE.search(text):
                is_consolidated = False
            elif _FS_CONSOLIDATED.search(text) and '별도' not in text:
                is_consolidated = True

            if _FS_BALANCE_SHEET.search(text):
                current_stmt_type = 'balance_sheet'
            elif _FS_INCOME_STMT.search(text):
                current_stmt_type = 'income_statement'
            continue

        # 제목 테이블에서도 컨텍스트 갱신 (단일 셀 테이블)
        if child.name == 'table':
            rows = child.find_all('tr')
            if len(rows) <= 4:
                # 제목/메타 테이블 — 컨텍스트 갱신
                table_text = child.get_text()
                if _FS_SEPARATE.search(table_text):
                    is_consolidated = False
                if _FS_BALANCE_SHEET.search(table_text):
                    current_stmt_type = 'balance_sheet'
                elif _FS_INCOME_STMT.search(table_text):
                    current_stmt_type = 'income_statement'
                continue

            # 데이터 테이블 판별: 행 5개+, 첫 행에 '과목'
            first_cells = [c.get_text().strip() for c in rows[0].find_all(['td', 'th'])]
            if not any('과' in c and '목' in c for c in first_cells):
                continue
            if current_stmt_type is None:
                continue

            # 이미 채워진 슬롯이면 스킵 (중복 방지)
            scope = "consolidated" if is_consolidated else "separate"
            if result[scope][current_stmt_type] is not None:
                continue

            # 단위 추출 — 바로 앞 테이블에서
            unit = _extract_unit_from_siblings(child)

            # 헤더 colspan 반영한 실제 컬럼 수
            header_cells_raw = rows[0].find_all(['td', 'th'])
            expanded_header = []
            for c in header_cells_raw:
                val = c.get_text().strip()
                colspan = int(c.get('colspan', 1) or 1)
                expanded_header.append(val)
                for _ in range(colspan - 1):
                    expanded_header.append('')
            actual_cols = len(expanded_header)

            # 기간 라벨 추출
            period_labels = _extract_period_labels(expanded_header)

            # 행 데이터 추출
            data_rows = []
            for row in rows[1:]:  # 헤더 제외
                cells = row.find_all(['td', 'th'])
                expanded = []
                for c in cells:
                    val = c.get_text().strip().replace('\n', ' ')
                    colspan = int(c.get('colspan', 1) or 1)
                    expanded.append(val)
                    for _ in range(colspan - 1):
                        expanded.append('')
                # 컬럼 수 맞추기
                while len(expanded) < actual_cols:
                    expanded.append('')
                data_rows.append(expanded[:actual_cols])

            # 컬럼 메타데이터 — 실제 헤더 기반
            columns = _build_column_meta(expanded_header)

            # 정규화: 다양한 컬럼 패턴을 통일
            has_note = "note" in columns
            normalized = _normalize_financial_rows(columns, data_rows)

            if has_note:
                out_columns = ["account", "note", "current", "prior"]
            else:
                out_columns = ["account", "current", "prior"]
                # note 컬럼 제거
                normalized = [[r[0], r[2], r[3]] for r in normalized]

            result[scope][current_stmt_type] = {
                "unit": unit,
                "period_labels": period_labels,
                "columns": out_columns,
                "column_count": len(out_columns),
                "rows": normalized,
                "row_count": len(normalized),
            }

    # null 처리: 하나만 있으면 나머지에 scope 메타데이터 추가
    for scope in ["consolidated", "separate"]:
        for stmt in ["balance_sheet", "income_statement"]:
            entry = result[scope][stmt]
            if entry is not None:
                entry["scope"] = scope

    return result


def _empty_financial_result() -> dict:
    return {
        "consolidated": {"balance_sheet": None, "income_statement": None},
        "separate": {"balance_sheet": None, "income_statement": None},
    }


def _extract_unit_from_siblings(table_el) -> str:
    """테이블 바로 앞 형제 요소들에서 (단위: ...) 추출"""
    count = 0
    for sib in table_el.previous_siblings:
        if hasattr(sib, 'get_text'):
            text = sib.get_text()
            m = _FS_UNIT.search(text)
            if m:
                return m.group(1).strip()
        count += 1
        if count >= 5:
            break
    return ""


def _build_column_meta(header_cells: list[str]) -> list[str]:
    """헤더 셀로부터 컬럼 의미 추론"""
    columns = []
    for cell in header_cells:
        clean = re.sub(r'\s+', '', cell)
        if '과' in clean and '목' in clean:
            columns.append("account")
        elif '주석' in clean:
            columns.append("note")
        elif '당' in clean:
            columns.append("current")
        elif '전' in clean:
            columns.append("prior")
        elif not clean:
            # 빈 셀 — colspan 확장분, 앞 컬럼의 서브컬럼
            if columns and columns[-1] in ("current", "prior"):
                columns.append(f"{columns[-1]}_sub")
            else:
                columns.append("unknown")
        else:
            columns.append("unknown")
    return columns


def _normalize_financial_rows(columns: list[str], rows: list[list[str]]) -> list[list[str]]:
    """다양한 컬럼 패턴을 [account, note, current, prior] 4컬럼으로 정규화

    패턴 예시:
    - KT&G:    [account, note, current, prior] → 그대로
    - 삼성전자: [account, current, current_sub, prior, prior_sub] → 금액 병합
    - LG화학:  [account, note, current, current_sub, prior, prior_sub] → 금액 병합
    """
    if not columns or not rows:
        return rows

    # 이미 4컬럼이고 [account, note, current, prior]면 그대로
    if columns == ["account", "note", "current", "prior"]:
        return rows

    # 각 역할의 인덱스 찾기
    account_idx = None
    note_idx = None
    current_idxs = []
    prior_idxs = []

    for i, col in enumerate(columns):
        if col == "account" and account_idx is None:
            account_idx = i
        elif col == "note":
            note_idx = i
        elif col in ("current", "current_sub"):
            current_idxs.append(i)
        elif col in ("prior", "prior_sub"):
            prior_idxs.append(i)

    if account_idx is None:
        return rows

    normalized = []
    for row in rows:
        account = row[account_idx] if account_idx < len(row) else ""
        note = row[note_idx] if note_idx is not None and note_idx < len(row) else ""

        # current: 여러 컬럼 중 비어있지 않은 첫 번째 값
        current = ""
        for idx in current_idxs:
            if idx < len(row) and row[idx].strip():
                current = row[idx]
                break

        # prior: 여러 컬럼 중 비어있지 않은 첫 번째 값
        prior = ""
        for idx in prior_idxs:
            if idx < len(row) and row[idx].strip():
                prior = row[idx]
                break

        normalized.append([account, note, current, prior])

    return normalized


def _extract_period_labels(header_cells: list[str]) -> dict:
    """헤더 셀에서 당기/전기 라벨 추출"""
    labels = {"current": "", "prior": ""}
    for cell in header_cells:
        cell_clean = re.sub(r'\s+', '', cell)
        if '당' in cell_clean:
            labels["current"] = cell.strip()
        elif '전' in cell_clean:
            labels["prior"] = cell.strip()
        elif re.match(r'(?:20)?\d{2,4}년', cell_clean):
            # 연도 기반 — 큰 연도가 당기
            if not labels["current"]:
                labels["current"] = cell.strip()
            else:
                labels["prior"] = cell.strip()
    return labels
