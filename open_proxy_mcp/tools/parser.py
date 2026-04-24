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
    r'[□◎●○▶·ㆍｏ]?\s*제\s*\d+\s*(?:-\s*\d+)*\s*호'  # 제N호, □제N호, ㆍ제N호, ｏ제N호 (전각o 강원랜드 등)
    r'|-\s*제\s*\d+\s*(?:-\s*\d+)*\s*호'           # -제N호
    r'|\d+\)\s*제\s*\d+'                            # N)제N호
    r'|\(제\s*\d+\s*(?:-\s*\d+)*\s*호\s*(?:의안|안건)\s*\)'  # (제N호 의안) — 괄호형 안건만, 조건부 (제N호 인가되는 경우) 제외
    r'|[·ㆍ]?\s*\d+-\d+호'                          # N-M호 (제 없음), ·N-M호
    r'|성\s*명\s*(?:생\s*년\s*월\s*일|출생)'            # 후보자 테이블 헤더 (공백 허용)
    r'|후보자\s*(?:성명|선임직)'                      # 후보자 테이블 헤더
    r'|선임직\s*성명'                                 # 후보자 테이블 헤더
    r'|변경전\s*내용'                                 # 정관변경 비교 테이블
    r'|현행\s+개정'                                   # 정관변경 비교 테이블
    r'|조문\s+현\s*행\s+변\s*경'                      # 정관변경 비교 테이블 (본느 패턴)
    r'|구분\s+변경전'                                 # 정관변경 비교 테이블
    r'|구\s*분\s+병합\s*전'                            # 주식병합 비교 테이블
    r'|가\.\s*의안의?\s*요지'                          # 안건 상세 시작
    r'|[▣■□]\s*의결권'                                  # 의결권 행사 안내
    r'|\d+\.\s*주주총회\s*소집'                           # 주총 소집 통지/공고
    r'|-\s*후보에\s*관한\s*사항'                          # 한전 등 공공기관 후보 섹션
    r'|의안\s+후보자\s+임기'                              # 한전 후보자 테이블 헤더
    r'|ｏ\s*(?:제\s*\d+|후보)'                            # 강원랜드 등 전각o + 제N호/후보 마커
    r'|※'
    r'|$'
    r'))'
)

# 표준 (콜론 있음): 제N호 의안: 제목 / 제N호 (이사회안): 제목 / 제N호 (주주제안): 제목
# 조건부 prefix "(제N호 인가되는 경우)" 등이 제목 앞에 올 수 있으므로 괄호 블록을 포함
AGENDA_RE = re.compile(
    r'제\s*(\d+)\s*(?:-\s*(\d+))?\s*(?:-\s*(\d+))?\s*호'
    r'\s*(?:의안|안건)?\s*(?:\([^)]*\))?\s*[:：]\s*'
    r'((?:\([^)]*\)\s*)?[^\n]*?)' + _AGENDA_BOUNDARY
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

def parse_agenda_xml(text: str, html: str = "") -> list[dict]:
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

    # ※ 비고 문장에 있는 안건 번호 참조 위치 수집 (안건이 아닌 참조)
    # ※ 뒤에서 다음 안건 마커(□◎●제N호) 전까지만 note 범위로 잡음
    _note_spans: set[int] = set()
    for nm in re.finditer(
        r'※.+?(?=\s*[□◎●]\s*제|\s*(?<![가-힣])제\s*\d+\s*(?:-\s*\d+)*\s*호\s*(?:의안|안건)?\s*[:：]|$)',
        zone,
    ):
        note_start, note_end = nm.start(), nm.end()
        for ref in re.finditer(
            r'제\s*\d+\s*(?:-\s*\d+)*\s*호', zone[note_start:note_end]
        ):
            _note_spans.add(note_start + ref.start())

    flat = []
    for _, m in matches:
        # ※ 비고 안의 안건 번호 참조는 스킵
        if m.start() in _note_spans:
            continue

        l1 = int(m.group(1))
        l2 = int(m.group(2)) if m.group(2) else None
        l3 = int(m.group(3)) if m.group(3) else None
        raw_title = m.group(4)
        title = _clean_title(raw_title)

        source = _detect_source(title)
        if source:
            title = _remove_source_tag(title)

        # 소스 태그 제거 후 제목이 비거나 콜론으로 시작하면 재추출
        title = re.sub(r'^[:：]\s*', '', title).strip()
        if not title.strip():
            colon_match = re.search(r'[:：]\s*(.+)', raw_title)
            if colon_match:
                title = _clean_title(colon_match.group(1))
            else:
                title = _clean_title(raw_title)
            # 소스 태그가 남아있으면 다시 제거
            if source:
                title = _remove_source_tag(title)

        number = _format_number(l1, l2, l3)

        # 보고사항 필터링 (감사보고/영업보고/내부회계 등은 결의 안건이 아님)
        if _is_report_item(title):
            continue

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

    # 이중 파싱 방지: 같은 number가 중복되면 첫 번째 것만 유지
    # (소집공고 의안 목록이 먼저, 경영참고사항 상세가 뒤에 나오므로 첫 번째가 정확)
    seen_numbers: set[str] = set()
    deduped = []
    for item in flat:
        if item["number"] not in seen_numbers:
            seen_numbers.add(item["number"])
            deduped.append(item)
    flat = deduped

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

def parse_meeting_info_xml(text: str, html: str = "") -> dict:
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
    title = re.sub(r'\s{2,}', ' ', title)  # 연속 공백 정리
    title = re.sub(r'^[:：]\s*', '', title)  # 선행 콜론 제거
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


_REPORT_ITEMS_RE = re.compile(
    r'^(?:감사\s*보고|영업\s*보고|내부\s*회계|사업\s*보고|내부\s*통제)',
)


def _is_report_item(title: str) -> bool:
    """보고사항인지 판별 (감사보고, 영업보고, 내부회계 등 — 결의 안건 아님)"""
    return bool(_REPORT_ITEMS_RE.search(title.strip()))


def _detect_source(text: str) -> str | None:
    if re.search(r'주주\s*제안', text):
        return '주주제안'
    if re.search(r'이사회\s*안', text):
        return '이사회안'
    return None


def _remove_source_tag(title: str) -> str:
    # 괄호로 감싼 소스 태그만 제거: (주주제안), (이사회안), (주주 제안) 등
    title = re.sub(r'\s*\(\s*주주\s*제안[^)]*\)\s*', '', title)
    title = re.sub(r'\s*\(\s*이사회\s*안[^)]*\)\s*', '', title)
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


def parse_agenda_details_xml(html: str) -> list[dict]:
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

    # Fallback: 안건 마커(■제N호)가 없지만 서브섹션/테이블이 있는 경우
    # → 카테고리 제목을 안건으로 사용
    if not agendas and category:
        fallback_sections = []
        current_section = None
        for child in container.children:
            if not hasattr(child, 'name') or not child.name:
                continue
            if child.name in ('title', 'pgbrk'):
                continue
            text = child.get_text().strip()
            if not text:
                continue

            if child.name == 'p':
                lines = _split_p_lines(child)
                for line in lines:
                    sub_match = SUBSECTION_RE.match(line)
                    if sub_match:
                        current_section = {"heading": line, "blocks": []}
                        fallback_sections.append(current_section)
                    elif current_section is not None:
                        if line.startswith('※'):
                            current_section["blocks"].append({"type": "note", "content": line})
                        elif line:
                            current_section["blocks"].append({"type": "text", "content": line})
                    elif not current_section:
                        current_section = {"heading": None, "blocks": []}
                        fallback_sections.append(current_section)
                        current_section["blocks"].append({"type": "text", "content": line})

            elif child.name == 'table' and current_section is not None:
                md_table = _table_to_markdown(child)
                if md_table:
                    is_md_table = md_table.startswith('|')
                    block_type = "table" if is_md_table else "text"
                    current_section["blocks"].append({"type": block_type, "content": md_table})

        fallback_sections = [s for s in fallback_sections if s["blocks"] or s["heading"]]
        if fallback_sections:
            agendas.append({
                "number": "",
                "title": category,
                "category": category,
                "sections": fallback_sections,
            })

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
    # - 접두 기호(■□●▶)는 선택적 (SPAN 분리 시 없을 수 있음)
    # - 의안/안건 뒤 )） 포함 (삼성전자 "제4호 의안)" 패턴)
    raw = re.sub(
        r'([■□●▶]?\s*제\s*\d+\s*(?:-\s*\d+)*\s*호\s*(?:의안|안건)?\s*[)）:：]?)\s*\n\s*\n?\s*',
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

    # 가. 나. 다. — 서브섹션 (안건 마커 없이 바로 시작하는 경우 fallback)
    sub_match = SUBSECTION_RE.match(line)
    if current_agenda is None and sub_match and category:
        current_agenda = {
            "number": "",
            "title": category,
            "category": category,
            "sections": [],
        }
        agendas.append(current_agenda)

    if current_agenda is None:
        return current_agenda, current_section
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


# ── 인사(선임/해임) 파싱 ──

_PERSONNEL_KEYWORDS = ['선임', '해임', '중임', '연임', '재선임']
_CATEGORY_MAP = [
    ('감사위원', '감사위원회'),
    ('사외이사', '사외이사'),
    ('독립이사', '독립이사'),
    ('사내이사', '사내이사'),
    ('기타비상무', '기타비상무이사'),
    ('상임이사', '상임이사'),
    ('상근감사', '감사'),
    ('비상근감사', '감사'),
    ('감사', '감사'),
    ('이사', '이사'),
]


def _extract_career_from_html(html: str, candidate_name: str) -> list[dict] | None:
    """HTML에서 후보자의 경력을 bs4로 직접 파싱 (1단계)

    <table> 안 <td>의 <p> 태그로 기간/내용을 분리합니다.
    <p> 구분이 없거나 기간 패턴이 없으면 None 반환 → regex fallback으로.
    """
    soup = BeautifulSoup(html, _BS4_PARSER)
    for table in soup.find_all('table'):
        table_text = table.get_text()
        if candidate_name not in table_text:
            continue
        if '세부경력' not in table_text and '주된직업' not in table_text:
            continue

        all_rows = table.find_all('tr')
        for row_idx, tr in enumerate(all_rows):
            tds = tr.find_all(['td', 'th'])
            if not tds or candidate_name not in tds[0].get_text(strip=True):
                continue

            # rowspan 확인 — 이름 셀이 rowspan이면 다음 행들도 수집
            name_td = tds[0]
            rowspan = int(name_td.get('rowspan', 1))

            # 기간/내용 셀 위치 찾기 (이름 행에서)
            period_col = None
            content_col = None
            for i, td in enumerate(tds):
                td_text = td.get_text(strip=True)
                if re.search(r'\d{4}\s*(?:년|[-~.])', td_text):
                    period_col = i
                    content_col = i + 1
                    break

            if period_col is None:
                continue

            # rowspan > 1: 각 행에서 기간/내용 수집
            if rowspan > 1:
                result = []
                for r_offset in range(rowspan):
                    r_idx = row_idx + r_offset
                    if r_idx >= len(all_rows):
                        break
                    r_tds = all_rows[r_idx].find_all(['td', 'th'])
                    if r_offset == 0:
                        # 첫 행: 이름/주된직업 셀 포함
                        p_td = r_tds[period_col] if period_col < len(r_tds) else None
                        c_td = r_tds[content_col] if content_col and content_col < len(r_tds) else None
                    else:
                        # 이후 행: rowspan 셀이 빠져서 기간=첫 셀, 내용=두 번째 셀
                        p_td = r_tds[0] if len(r_tds) >= 1 else None
                        c_td = r_tds[1] if len(r_tds) >= 2 else None

                    if p_td:
                        period = p_td.get_text(strip=True)
                        content = c_td.get_text(strip=True) if c_td else ""
                        if period or content:
                            result.append({"period": period, "content": content})

                if result:
                    # 기간 전처리
                    for r in result:
                        parsed = _parse_period_raw(r["period"])
                        r["period"] = parsed[0] if parsed else r["period"]
                    return _clean_career_details(result, candidate_name)
                continue

            # rowspan=1: 기존 로직 (단일 행, <p> 분리)
            period_td = tds[period_col]
            content_td = tds[content_col] if content_col and content_col < len(tds) else None

            period_ps = [p.get_text(strip=True) for p in period_td.find_all('p') if p.get_text(strip=True)]
            content_ps = [p.get_text(strip=True) for p in content_td.find_all('p') if p.get_text(strip=True)] if content_td else []

            if not period_ps and not content_ps:
                # <p> 태그 없이 순수 텍스트인 경우 — 現/前 구분자로 content 먼저 분리, period는 연도 패턴으로 분리
                period_raw = period_td.get_text(strip=True) if period_td else ""
                content_raw = content_td.get_text(strip=True) if content_td else ""
                if period_raw and content_raw:
                    # content 분리 + 現/前 마커 보존
                    contents = []
                    is_current = []  # True=現(현재 진행중, 토큰 1개), False=前(종료, 토큰 2개)
                    if re.search(r'(?:現|前|현|전)(?:\)|\s)', content_raw):
                        parts = re.split(r'(?=(?:現|前|현|전)(?:\)|\s))', content_raw)
                        for x in parts:
                            if not x.strip():
                                continue
                            cur = bool(re.match(r'(?:現|현)', x))
                            is_current.append(cur)
                            cleaned = re.sub(r'^(?:現|前|현|전)(?:\)\s*|\s+)', '', x).strip()
                            if cleaned:
                                contents.append(cleaned)
                            else:
                                is_current.pop()
                    else:
                        contents = [content_raw]
                        is_current = [True]
                    # 연도 토큰 추출 + 現/前 기반 할당
                    if len(contents) >= 2:
                        year_tokens = re.findall(r'\d{4}(?:\.\d{1,2})?', period_raw)
                        periods = []
                        ti = 0
                        for ci in range(len(contents)):
                            if ci < len(is_current) and is_current[ci]:
                                # 現: 시작만 (1 토큰)
                                if ti < len(year_tokens):
                                    periods.append(f"{year_tokens[ti]} ~ 현재")
                                    ti += 1
                                else:
                                    periods.append("")
                            else:
                                # 前: 시작~끝 (2 토큰)
                                if ti + 1 < len(year_tokens):
                                    periods.append(f"{year_tokens[ti]} ~ {year_tokens[ti+1]}")
                                    ti += 2
                                elif ti < len(year_tokens):
                                    periods.append(f"{year_tokens[ti]} ~")
                                    ti += 1
                                else:
                                    periods.append("")
                        result = []
                        for i in range(len(contents)):
                            p = periods[i] if i < len(periods) else ""
                            result.append({"period": p, "content": contents[i]})
                        if result:
                            return _clean_career_details(result, candidate_name)
                return None

            if not period_ps:
                period_raw = period_td.get_text(strip=True)
                periods = _parse_period_raw(period_raw)
                if len(periods) == len(content_ps):
                    return _clean_career_details(
                        [{"period": p, "content": c} for p, c in zip(periods, content_ps)],
                        candidate_name,
                    )
                result = []
                for i, ct in enumerate(content_ps):
                    p = periods[i] if i < len(periods) else ""
                    result.append({"period": p, "content": ct})
                if result:
                    return _clean_career_details(result, candidate_name)
                return None

            period_ps = [re.sub(r'(\d{4})년\s*(\d{1,2})월', r'\1.\2', p).replace('년', '') for p in period_ps]
            result = []
            for i in range(max(len(period_ps), len(content_ps))):
                p = period_ps[i] if i < len(period_ps) else ""
                ct = content_ps[i] if i < len(content_ps) else ""
                if p or ct:
                    if not p and result:
                        result[-1]["content"] += ", " + ct
                    else:
                        result.append({"period": p, "content": ct})

            if result:
                return _clean_career_details(result, candidate_name)

    return None


def _parse_period_raw(period_raw: str) -> list[str]:
    """기간 원본 문자열에서 기간 리스트 추출

    4자리 연도 우선, 0개면 2자리 연도(YY~YY) 시도.
    """
    # 전처리
    # YYYY년 M월 → YYYY.MM
    period_raw = re.sub(r'(\d{4})년\s*(\d{1,2})월', r'\1.\2', period_raw)
    period_raw = re.sub(r'現', '현재', period_raw)
    period_raw = re.sub(r'[-~]\s*현(?!재)', '~현재', period_raw)
    # YYYY.MM~ 또는 YYYY~ (종료일 없이 끝남) → ~현재
    period_raw = re.sub(r'(\d{4}(?:\.\d{1,2})?)~(?=\d{4}[^.]|\s|$)', r'\1~현재 ', period_raw)
    period_raw = re.sub(r'(현재)(\d)', r'\1 \2', period_raw)
    period_raw = re.sub(r"[''`](\d{2})", lambda m: f"20{m.group(1)}" if int(m.group(1)) <= 30 else f"19{m.group(1)}", period_raw)
    # 붙어있는 연도 분리: YYYY.MMYYYY → YYYY.MM YYYY, YYYYYYYY → YYYY YYYY
    period_raw = re.sub(r'(\d{4}\.\d{1,2})(\d{4})', r'\1 \2', period_raw)
    period_raw = re.sub(r'(\d{4})(\d{4})', r'\1 \2', period_raw)
    period_raw = re.sub(r'(\d{4})(\d{4})', r'\1 \2', period_raw)

    # 1차: YYYY.MM 또는 YYYY 매치 (~와 - 모두 기간 구분자)
    # YYYY.MM~YYYY.MM, YYYY.MM~현재, YYYY~YYYY, YYYY~현재, 단독 YYYY.MM, 단독 YYYY
    _YMD = r'\d{4}(?:\.\d{1,2}(?:\.\d{1,2})?)?'  # YYYY or YYYY.MM or YYYY.MM.DD
    periods = re.findall(rf'{_YMD}\s*[-~]\s*(?:현재|{_YMD})|{_YMD}', period_raw)

    # 4자리 매치가 전부 비정상 연도면 무효화 → 2자리로 재시도
    if periods:
        all_invalid = True
        for p in periods:
            years = re.findall(r'\d{4}', p)
            if all(1950 <= int(y) <= 2030 for y in years):
                all_invalid = False
                break
        if all_invalid:
            periods = []

    # 4자리 유효 매치 0개 → 2자리 연도 시도 (고려아연 패턴: 22~현21~2219~21)
    if not periods:
        def _yy(yy: str) -> str:
            return f"20{yy}" if int(yy) <= 30 else f"19{yy}"
        # 2자리~현재
        pairs_2d = re.findall(r'(\d{2})[-~](현재|\d{2})', period_raw)
        if pairs_2d:
            for start, end in pairs_2d:
                if end == '현재':
                    periods.append(f"{_yy(start)}~현재")
                else:
                    periods.append(f"{_yy(start)}~{_yy(end)}")

    return periods


def _clean_career_details(details: list[dict], name: str = "") -> list[dict]:
    """경력 리스트 정리: 빈 content 제거, 역순 기간 검증, 합쳐진 content 분리"""
    cleaned = []
    for d in details:
        period = d.get("period", "").strip()
        content = d.get("content", "").strip()
        # 빈 content ("-" 또는 빈 문자열) 제거
        if not content or content == "-":
            continue
        # 역순 기간 검증
        years = re.findall(r'\d{4}', period)
        if len(years) == 2 and int(years[0]) > int(years[1]):
            logger.warning(f"[CAREER] 역순 기간: '{period}' — {name}")
            period = f"{years[1]} ~ {years[0]}"
            d["period"] = period
        # 비정상 연도
        if years and not all(1950 <= int(y) <= 2030 for y in years):
            logger.warning(f"[CAREER] 비정상 기간: '{period}' — {name}")
            d["period"] = ""
        # 합쳐진 content 분리 — 소프트 + 하드 패턴
        if len(content) > 80:
            # 소프트: 영문) + 한글/㈜ 경계
            parts = re.split(r'(?<=[a-zA-Z]\))(?=[가-힣㈜])', content)
            content = "\n".join(p.strip() for p in parts if p.strip())
            # 소프트: 한글 + ㈜ 경계
            content = re.sub(r'(?<=[가-힣])(?=㈜)', '\n', content)
            # 하드: 직책 뒤에 대기업 그룹명이 붙는 경우
            _CONGLOMERATES = r'한화|삼성|LG|SK|현대|롯데|CJ|두산|포스코|GS|LS|HD|네이버|카카오|신세계|이마트|코웨이|셀트리온|대한항공|아시아나'
            content = re.sub(
                rf'((?:대표)?이사|사장|부사장|상무|전무|본부장|팀장|실장|교수|위원장?|고문)(?=(?:{_CONGLOMERATES})[가-힣A-Za-z]*)',
                r'\1\n', content
            )
            d["content"] = content
        cleaned.append(d)
    return cleaned


def parse_personnel_xml(html: str) -> dict:
    """선임/해임 안건에서 후보자/대상자 정보를 정규화 추출

    Returns:
        {"appointments": [...], "summary": {...}}
    """
    details = parse_agenda_details_xml(html)
    if not details:
        return {"appointments": [], "summary": _empty_personnel_summary()}

    appointments = []

    for d in details:
        title = d.get("title", "")
        number = d.get("number", "")

        # 선임/해임 안건인지 확인
        if not any(kw in title for kw in _PERSONNEL_KEYWORDS):
            continue

        # 액션 분류
        action = "선임"
        if '해임' in title:
            action = "해임"
        elif '재선임' in title:
            action = "재선임"
        elif '중임' in title:
            action = "중임"
        elif '연임' in title:
            action = "연임"

        # 카테고리 분류
        category = "이사"
        for keyword, cat in _CATEGORY_MAP:
            if keyword in title:
                category = cat
                break

        # 후보자 정보 추출 — 가. 서브섹션의 테이블
        candidates = _extract_candidates(d, html)

        # 후보자 없으면 제목에서 이름 추출 시도
        if not candidates:
            name = _extract_name_from_title(title)
            if name:
                candidates = [{"name": name, "roleType": category}]

        appointment = {
            "number": number,
            "title": title,
            "action": action,
            "category": category,
            "candidates": candidates,
        }
        appointments.append(appointment)

    # 요약
    summary = _build_personnel_summary(appointments)

    return {"appointments": appointments, "summary": summary}


def _extract_candidates(agenda_detail: dict, html: str = "") -> list[dict]:
    """안건 상세의 가. 서브섹션 테이블에서 후보자 정보 추출"""
    candidates = []

    for sec in agenda_detail.get("sections", []):
        heading = sec.get("heading") or ""

        # 가. 후보자의 성명ㆍ생년월일... 테이블
        if heading.startswith("가.") or '성명' in heading:
            for block in sec.get("blocks", []):
                if block["type"] != "table":
                    continue
                rows = _parse_md_table(block["content"])
                if len(rows) < 2:
                    continue

                headers = rows[0]
                for row in rows[1:]:
                    if not row or not row[0].strip():
                        continue
                    # "총 ( N ) 명" 행 스킵
                    if '총' in row[0] and '명' in row[0]:
                        continue

                    candidate = {"name": row[0].strip()}

                    # 헤더 매핑
                    for ci, header in enumerate(headers):
                        if ci >= len(row):
                            break
                        h = re.sub(r'\s+', '', header)
                        val = row[ci].strip()
                        if '생년월일' in h:
                            candidate["birthDate"] = val
                        elif '사외이사' in h and '후보' in h:
                            candidate["roleType"] = val if val else None
                        elif '분리선출' in h:
                            candidate["separateElection"] = val
                        elif '최대주주' in h:
                            candidate["majorShareholderRelation"] = val
                        elif '추천인' in h:
                            candidate["recommender"] = val

                    candidates.append(candidate)

        # 나. 주된직업ㆍ세부경력 — 기존 후보자에 매칭
        if heading.startswith("나.") or '주된직업' in heading:
            # 주된직업/거래내역은 마크다운 테이블에서 추출 (단순 필드)
            for block in sec.get("blocks", []):
                if block["type"] != "table":
                    continue
                rows = _parse_md_table(block["content"])
                if len(rows) < 2:
                    continue
                headers = rows[0]
                for row in rows[1:]:
                    if not row or not row[0].strip():
                        continue
                    row0 = re.sub(r'\s+', '', row[0])
                    if row0 in ('기간', '내용', '총', ''):
                        continue
                    name = row[0].strip()
                    for c in candidates:
                        if c["name"] == name:
                            for ci, header in enumerate(headers):
                                if ci >= len(row):
                                    break
                                h = re.sub(r'\s+', '', header)
                                val = row[ci].strip()
                                if '주된직업' in h:
                                    c["mainJob"] = re.sub(r'^\(?\s*(?:現|現|현)\)?\s*', '', val).strip()
                                elif '거래내역' in h:
                                    c["recent3yTransactions"] = val if val and val != '없음' else None

            # 세부경력: bs4 직접 파싱 (1단계) → regex fallback (2단계)
            for c in candidates:
                name = c["name"]
                # 1단계: HTML <p> 태그에서 직접 분리
                html_career = _extract_career_from_html(html, name)
                if html_career:
                    c["careerDetails"] = html_career
                    c["careerCompanyGroups"] = _build_career_company_groups(html_career)
                    continue

                # 2단계: regex fallback — 마크다운 테이블에서 기간/내용 분리
                for block in sec.get("blocks", []):
                    if block["type"] != "table":
                        continue
                    rows = _parse_md_table(block["content"])
                    if len(rows) < 2:
                        continue
                    headers = rows[0]
                    career_idx = None
                    career_content_idx = None
                    for hi, h in enumerate(headers):
                        hc = re.sub(r'\s+', '', h)
                        if '세부경력' in hc:
                            career_idx = hi
                            if hi + 1 < len(headers) and not headers[hi + 1].strip():
                                career_content_idx = hi + 1

                    if career_idx is None:
                        continue

                    for row in rows[1:]:
                        if not row or not row[0].strip():
                            continue
                        row0 = re.sub(r'\s+', '', row[0])
                        if row0 in ('기간', '내용', '총', ''):
                            continue
                        if row[0].strip() != name:
                            continue

                        periods_raw = row[career_idx].strip() if career_idx < len(row) else ""
                        contents_raw = row[career_content_idx].strip() if career_content_idx is not None and career_content_idx < len(row) else ""

                        periods = _parse_period_raw(periods_raw)

                        # 내용 분리
                        if re.search(r'(?:現|前|현|전)(?:\)|\s)', contents_raw):
                            contents = re.split(r'(?=(?:現|前|현|전)(?:\)|\s))', contents_raw)
                            contents = [re.sub(r'^(?:現|前|현|전)(?:\)\s*|\s+)', '', x).strip() for x in contents if x.strip()]
                        elif re.search(r'-\s*[\(\(가-힣A-Z]', contents_raw):
                            contents = re.split(r'(?=-\s*[\(\(가-힣A-Z])', contents_raw)
                            contents = [re.sub(r'^-\s*', '', x).strip() for x in contents if x.strip()]
                        elif re.search(r'(?:\(주\)|\(재\)|\(사\)|법무법인)', contents_raw):
                            contents = re.split(r'(?=\(주\)|\(재\)|\(사\)|법무법인)', contents_raw)
                            contents = [x.strip() for x in contents if x.strip()]
                        else:
                            contents = [contents_raw.strip()] if contents_raw.strip() else []

                        career_details = []
                        if len(periods) > 1 and len(contents) <= 1:
                            full_period = f"{periods[0].split('~')[0].strip()} ~ {periods[-1].split('~')[-1].strip()}"
                            full_content = contents[0] if contents else contents_raw.strip()
                            career_details.append({"period": full_period, "content": full_content})
                        else:
                            for i in range(max(len(periods), len(contents))):
                                p = periods[i] if i < len(periods) else ""
                                ct = contents[i] if i < len(contents) else ""
                                if p or ct:
                                    career_details.append({"period": p, "content": ct})

                        if career_details:
                            career_details = _clean_career_details(career_details, name)
                        if career_details:
                            c["careerDetails"] = career_details
                            c["careerCompanyGroups"] = _build_career_company_groups(career_details)
                        elif periods_raw or contents_raw:
                            c["careerDetails"] = _clean_career_details(
                                [{"period": periods_raw, "content": contents_raw}], name
                            )
                        break

        # 다. 체납사실 — 기존 후보자에 매칭 (3개 필드 분리)
        if heading.startswith("다.") or '체납' in heading:
            for block in sec.get("blocks", []):
                if block["type"] != "table":
                    continue
                rows = _parse_md_table(block["content"])
                if len(rows) < 2:
                    continue
                headers = rows[0]
                for row in rows[1:]:
                    if not row or not row[0].strip():
                        continue
                    name = row[0].strip()
                    for c in candidates:
                        if c["name"] == name:
                            eligibility = {}
                            for ci, header in enumerate(headers):
                                if ci >= len(row):
                                    break
                                h = re.sub(r'\s+', '', header)
                                val = row[ci].strip() if row[ci].strip() else None
                                if '체납' in h:
                                    eligibility["taxDelinquency"] = val
                                elif '부실' in h:
                                    eligibility["insolventMgmt"] = val
                                elif '결격' in h:
                                    eligibility["legalDisqualification"] = val
                            c["eligibility"] = eligibility

        # 라. 직무수행계획 — 텍스트 블록
        if heading.startswith("라.") or '직무수행' in heading:
            texts = []
            for block in sec.get("blocks", []):
                if block["type"] == "text" and block["content"].strip():
                    content = block["content"].strip()
                    # 확인서 텍스트 제거
                    content = re.sub(r'확인서\s*\n*.*?\.(?:jpeg|jpg|png).*$', '', content, flags=re.DOTALL).strip()
                    if content:
                        texts.append(content)
            if texts and candidates:
                plan_text = "\n".join(texts)
                for c in candidates:
                    c["dutyPlan"] = plan_text

        # 마. 추천 사유 — 텍스트 블록
        if heading.startswith("마.") or '추천' in heading:
            texts = []
            for block in sec.get("blocks", []):
                if block["type"] == "text" and block["content"].strip():
                    content = block["content"].strip()
                    content = re.sub(r'확인서\s*\n*.*?\.(?:jpeg|jpg|png).*$', '', content, flags=re.DOTALL).strip()
                    if content:
                        texts.append(content)
            if texts and candidates:
                reason_text = "\n".join(texts)
                for c in candidates:
                    c["recommendationReason"] = reason_text

    return candidates


def _build_career_company_groups(career_details: list[dict]) -> list[dict]:
    """careerDetails를 회사명 기준으로 그룹핑

    content에서 회사/기관명과 직책을 분리하여 그룹화.
    """
    from collections import OrderedDict
    groups = OrderedDict()

    for cd in career_details:
        content = cd.get("content", "")
        period = cd.get("period", "")
        if not content:
            continue

        # 회사명/직책 분리 — 마지막 직책 키워드 앞까지가 회사명
        company, role = _split_company_role(content)

        if company not in groups:
            groups[company] = []
        item = f"{period} {role}".strip() if period else role
        if item:
            groups[company].append(item)

    return [{"company": k, "items": v} for k, v in groups.items()]


def _split_company_role(content: str) -> tuple[str, str]:
    """'LG전자 AE사업본부장, 사장' → ('LG전자', 'AE사업본부장, 사장')"""
    # 직책 키워드 패턴
    role_patterns = [
        r'대표이사', r'공동대표이사', r'사장', r'부사장', r'전무', r'상무',
        r'이사', r'감사', r'회장', r'부회장', r'사외이사', r'비상임이사',
        r'상근고문', r'교수', r'명예교수', r'초빙교수',
        r'변호사', r'대표변호사',
        r'본부장', r'부문장', r'담당장', r'사업부장', r'팀장', r'과장', r'실장',
        r'자문위원', r'위원', r'위원장',
    ]
    pattern = '|'.join(role_patterns)

    # 첫 번째 직책 키워드 위치 찾기
    m = re.search(pattern, content)
    if m:
        company = content[:m.start()].strip().rstrip(',').strip()
        role = content[m.start():].strip()
        if company:
            return company, role

    # 직책 키워드 못 찾으면 전체가 회사명+직책
    return content, ""


def _extract_name_from_title(title: str) -> str | None:
    """안건 제목에서 이름 추출: '사내이사 김용관 선임의 건' → '김용관'"""
    # 한글 이름 (2~4자)
    m = re.search(r'(?:이사|감사)\s+([가-힣]{2,4})\s+(?:선임|해임|재선임|중임|연임)', title)
    if m:
        return m.group(1)
    # 영문 이름
    m = re.search(r'(?:이사|감사)\s+([A-Za-z\s]+?)\s+(?:선임|해임)', title)
    if m:
        return m.group(1).strip()
    # 후보자: 형태
    m = re.search(r'후보자?\s*[:：]?\s*([가-힣]{2,4}|[A-Za-z\s]+)', title)
    if m:
        return m.group(1).strip()
    # 후보 형태
    m = re.search(r'후보\s+([가-힣]{2,4})', title)
    if m:
        return m.group(1)
    return None


def _parse_md_table(md_content: str) -> list[list[str]]:
    """마크다운 테이블을 행 리스트로 파싱"""
    rows = []
    for line in md_content.split('\n'):
        line = line.strip()
        if not line or line.startswith('| ---'):
            continue
        if line.startswith('|') and line.endswith('|'):
            cells = [c.strip() for c in line[1:-1].split('|')]
            rows.append(cells)
    return rows


def _build_personnel_summary(appointments: list[dict]) -> dict:
    """인사 안건 요약"""
    summary = {
        "total_appointments": len(appointments),
        "total_candidates": sum(len(a.get("candidates", [])) for a in appointments),
        "directors": 0,
        "outside_directors": 0,
        "auditors": 0,
        "audit_committee": 0,
        "dismissals": 0,
    }
    for a in appointments:
        cat = a.get("category", "")
        action = a.get("action", "")
        count = len(a.get("candidates", [])) or 1

        if action == "해임":
            summary["dismissals"] += count
        elif '감사위원' in cat:
            summary["audit_committee"] += count
        elif '감사' in cat:
            summary["auditors"] += count
        elif '사외' in cat or '독립' in cat:
            summary["outside_directors"] += count
        else:
            summary["directors"] += count

    return summary


def _empty_personnel_summary() -> dict:
    return {
        "total_appointments": 0,
        "total_candidates": 0,
        "directors": 0,
        "outside_directors": 0,
        "auditors": 0,
        "audit_committee": 0,
        "dismissals": 0,
    }


# ── 정관변경 파싱 ──

def parse_aoi_xml(html: str, sub_agendas: list[dict] | None = None) -> dict:
    """정관변경 안건에서 세부의안별 변경전/변경후/사유를 구조화 추출

    Args:
        html: 문서 HTML
        sub_agendas: agm_agenda에서 가져온 정관변경 세부의안 목록
                     [{"number": "제2-1호", "title": "집중투표제 배제 조항 삭제"}, ...]

    Returns:
        {"amendments": [...], "summary": {...}}
    """
    details = parse_agenda_details_xml(html)
    if not details:
        return {"amendments": [], "summary": _empty_aoi_summary()}

    amendments = []

    for d in details:
        title = d.get("title", "")
        category = d.get("category", "")
        if "정관" not in title and "정관" not in category:
            continue

        # 섹션 블록을 순서대로 순회 — text에서 세부의안 헤더 감지, table에서 내용 추출
        pending_sub_id = ""
        pending_label = ""

        for sec in d.get("sections", []):
            # 섹션 헤딩에서도 세부의안 감지 (가. 나. 아래에 제N-M호가 있는 경우)
            heading = sec.get("heading") or ""
            heading_m = re.search(r'제\s*(\d+-\d+)\s*호\s*(?:\([^)]*\))?\s*[：:]?\s*(.*)', heading)
            if heading_m:
                pending_sub_id = heading_m.group(1)
                pending_label = heading_m.group(2).strip()

            for block in sec.get("blocks", []):
                # text 블록에서 세부의안 헤더 감지
                if block["type"] == "text":
                    txt = block["content"].strip()
                    m = re.search(r'제\s*(\d+-\d+)\s*호\s*(?:\([^)]*\))?\s*[：:]?\s*(.*)', txt)
                    if m:
                        pending_sub_id = m.group(1)
                        pending_label = m.group(2).strip()
                        # 소스 태그 제거
                        pending_label = re.sub(r'^\(?\s*(?:이사회안|주주제안)[^)]*\)?\s*[：:]?\s*', '', pending_label).strip()
                    continue

                if block["type"] != "table":
                    continue

                rows = _parse_md_table(block["content"])
                if len(rows) < 2:
                    continue

                headers = rows[0]
                headers_clean = [re.sub(r'\s+', '', h) for h in headers]
                has_before = any('변경전' in h or '현행' in h for h in headers_clean)
                has_after = any('변경후' in h or '개정' in h for h in headers_clean)
                if not (has_before and has_after):
                    continue

                # 컬럼 인덱스 매핑
                id_idx = 0
                before_idx = next((i for i, h in enumerate(headers_clean) if '변경전' in h or '현행' in h), 1)
                after_idx = next((i for i, h in enumerate(headers_clean) if '변경후' in h or '개정' in h), 2)
                reason_idx = next((i for i, h in enumerate(headers_clean) if '목적' in h or '사유' in h), 3)

                # 이 테이블의 모든 행을 하나의 amendment로 묶음 (pending_sub_id 사용)
                table_amendments = []
                for row in rows[1:]:
                    if not row or not row[0].strip():
                        continue
                    col0 = row[id_idx].strip() if id_idx < len(row) else ""
                    if not col0 or col0 == '-':
                        continue

                    # 테이블 내부에서 세부의안 번호 있는지 확인
                    m = re.match(r'(?:제\s*)?(\d+-\d+)\s*호?\s*(?:의안)?\s*[：:]?\s*(.*)', col0)
                    if m:
                        sub_id = m.group(1)
                        label = m.group(2).strip()
                    else:
                        sub_id = ""
                        label = ""

                    before = row[before_idx].strip() if before_idx < len(row) else ""
                    after = row[after_idx].strip() if after_idx < len(row) else ""
                    reason = row[reason_idx].strip() if reason_idx < len(row) else ""

                    clause = ""
                    for txt in [before, after]:
                        clause_m = re.search(r'(제\d+(?:조의?\d*)?(?:\([^)]+\))?)', txt)
                        if clause_m:
                            clause = clause_m.group(1)
                            break

                    table_amendments.append({
                        "sub_id": sub_id, "label": label,
                        "clause": clause, "before": before, "after": after, "reason": reason,
                    })

                if not table_amendments:
                    continue

                # 테이블 내부에 세부의안 번호가 있으면 기존 로직 (KT&G/삼성 패턴)
                has_internal_ids = any(a["sub_id"] for a in table_amendments)

                if has_internal_ids:
                    last_sub_id = ""
                    for ta in table_amendments:
                        if ta["sub_id"]:
                            last_sub_id = ta["sub_id"]
                            amendments.append({
                                "subAgendaId": ta["sub_id"],
                                "label": ta["label"],
                                "clause": ta["clause"],
                                "before": ta["before"],
                                "after": ta["after"],
                                "reason": ta["reason"],
                            })
                        elif last_sub_id and amendments:
                            last = amendments[-1]
                            if "additionalClauses" not in last:
                                last["additionalClauses"] = []
                            last["additionalClauses"].append({
                                "clause": ta["clause"],
                                "before": ta["before"],
                                "after": ta["after"],
                                "reason": ta["reason"],
                            })
                else:
                    # 테이블 외부에서 감지한 pending_sub_id 사용 (LG화학 패턴)
                    first = table_amendments[0]
                    main_amendment = {
                        "subAgendaId": pending_sub_id,
                        "label": pending_label or first["clause"],
                        "clause": first["clause"],
                        "before": first["before"],
                        "after": first["after"],
                        "reason": first["reason"],
                    }
                    if len(table_amendments) > 1:
                        main_amendment["additionalClauses"] = [
                            {"clause": ta["clause"], "before": ta["before"], "after": ta["after"], "reason": ta["reason"]}
                            for ta in table_amendments[1:]
                        ]
                    amendments.append(main_amendment)
                    pending_sub_id = ""
                    pending_label = ""

    # 세부의안 매핑: subAgendaId가 없는 amendments에 agm_agenda 세부의안 번호 부여
    if sub_agendas and any(not a.get("subAgendaId") for a in amendments):
        _map_sub_agendas_to_amendments(amendments, sub_agendas)

    summary = {
        "totalAmendments": len(amendments),
        "categories": list(dict.fromkeys(a["label"] for a in amendments if a["label"])),
    }

    return {"amendments": amendments, "summary": summary}


def _map_sub_agendas_to_amendments(amendments: list[dict], sub_agendas: list[dict]) -> None:
    """agm_agenda 세부의안을 charterChanges amendments에 매핑

    전략:
    1. reason/label 키워드로 매칭 시도
    2. 매칭 못 하면 순서 기반 fallback
    3. 이미 subAgendaId 있으면 건드리지 않음
    """
    # 이미 전부 매핑돼 있으면 스킵
    if all(a.get("subAgendaId") for a in amendments):
        return

    subs = []
    for s in sub_agendas:
        num = s.get("number", "").replace("제", "").replace("호", "")
        title = s.get("title", "")
        subs.append({"id": num, "title": title, "used": False})

    # 1차: 키워드 매칭
    for a in amendments:
        if a.get("subAgendaId"):
            # 이미 있으면 used 표시
            for s in subs:
                if s["id"] == a["subAgendaId"]:
                    s["used"] = True
            continue

        reason = (a.get("reason", "") + " " + a.get("label", "")).lower()
        best_match = None
        best_score = 0

        for s in subs:
            if s["used"]:
                continue
            title_words = [w for w in s["title"].replace("ㆍ", "·").split() if len(w) > 1]
            score = sum(1 for w in title_words if w.lower() in reason)
            if score > best_score:
                best_score = score
                best_match = s

        if best_match and best_score >= 1:
            a["subAgendaId"] = best_match["id"]
            a["label"] = a["label"] or best_match["title"]
            best_match["used"] = True

    # 2차: 매칭 못 한 나머지 — 순서 기반
    unmapped_amendments = [a for a in amendments if not a.get("subAgendaId")]
    unused_subs = [s for s in subs if not s["used"]]

    for a, s in zip(unmapped_amendments, unused_subs):
        a["subAgendaId"] = s["id"]
        if not a["label"] or a["label"] in ("(신설)", a.get("clause", "")):
            a["label"] = s["title"]
        s["used"] = True


def _empty_aoi_summary() -> dict:
    return {"totalAmendments": 0, "categories": []}


# ── 정정공고 파싱 (HTML 기반) ──

def parse_corrections_xml(html: str) -> dict | None:
    """정정공고의 정정 사항을 파싱

    DART 정정공고 구조:
      <section-1>
        <title>정 정 신 고 (보고)</title>
        <table> 정정일
        <table> 1. 정정대상 공시서류
        <table> 2. 최초제출일
        <table> 3. 정정사항
        <table> [항목 | 정정사유 | 정정 전 | 정정 후]  ← 핵심

    Returns:
        {"is_correction": True, "date": "...", "target_document": "...",
         "original_date": "...", "items": [{"section": "...", "reason": "...",
         "before": "...", "after": "..."}]}
        또는 None (정정공고가 아닌 경우)
    """
    soup = BeautifulSoup(html, _BS4_PARSER)

    # 정정신고 섹션 찾기
    correction_section = None
    for el in soup.find_all('title'):
        t = re.sub(r'\s+', '', el.get_text())
        if '정정신고' in t or '기재정정' in t:
            correction_section = el.parent
            break

    if not correction_section:
        return None

    result = {
        "is_correction": True,
        "date": None,
        "target_document": None,
        "original_date": None,
        "items": [],
    }

    tables = correction_section.find_all('table')
    for table in tables:
        rows = table.find_all('tr')
        if not rows:
            continue
        first_cells = [re.sub(r'\s+', '', c.get_text()) for c in rows[0].find_all(['td', 'th'])]

        # 정정일 — 단일 셀, 날짜 패턴
        if len(rows) == 2 and len(first_cells) == 1:
            date_text = rows[1].get_text().strip()
            if re.search(r'\d{4}', date_text):
                result["date"] = date_text

        # 정정대상 공시서류
        if any('정정대상' in c for c in first_cells):
            cells = [c.get_text().strip() for c in rows[0].find_all(['td', 'th'])]
            if len(cells) >= 2:
                result["target_document"] = cells[-1]

        # 최초제출일
        if any('최초제출' in c for c in first_cells):
            cells = [c.get_text().strip() for c in rows[0].find_all(['td', 'th'])]
            if len(cells) >= 2:
                result["original_date"] = cells[-1]

        # 정정사항 테이블 — [항목 | 정정사유 | 정정 전 | 정정 후]
        if any('항' in c and '목' in c for c in first_cells) and any('정정' in c for c in first_cells):
            for row in rows[1:]:
                cells = row.find_all(['td', 'th'])
                if len(cells) < 4:
                    continue
                cell_texts = [c.get_text().strip() for c in cells]
                item = {
                    "section": cell_texts[0].replace('\n', ' '),
                    "reason": cell_texts[1].replace('\n', ' '),
                    "before": cell_texts[2].replace('\n', ' ')[:500],
                    "after": cell_texts[3].replace('\n', ' ')[:500],
                }
                result["items"].append(item)

    return result if result["items"] else None


# ── 재무제표 파싱 (HTML 기반) ──

# 재무제표 테이블 식별 키워드
_FS_BALANCE_SHEET = re.compile(r'재무상태표|대차대조표')
_FS_INCOME_STMT = re.compile(r'손익계산서|포괄손익')
_FS_CONSOLIDATED = re.compile(r'연결')
_FS_SEPARATE = re.compile(r'별도|개별')
_FS_UNIT = re.compile(r'\(단위\s*[:：]?\s*(.+?)\)')
_FS_PERIOD = re.compile(r'(제\s*\d+\s*\(?\s*(?:당|전)\s*\)?\s*기|(?:20)?\d{2,4}\s*년)')


def parse_financials_xml(html: str) -> dict:
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

    # 재무제표 library 찾기 — 카테고리 title 또는 본문에서 재무제표 키워드
    fs_container = None
    for lib in detail_section.find_all('library'):
        container = lib.find('section-3') or lib
        # 카테고리 title 확인 (□ 재무제표의 승인)
        title_el = container.find('title')
        if title_el:
            title_text = re.sub(r'\s+', '', title_el.get_text())
            if '재무제표' in title_text or '재무상태표' in title_text or '대차대조표' in title_text:
                fs_container = container
                break
        # title 없으면 본문 첫 500자에서 확인
        text = re.sub(r'\s+', '', lib.get_text()[:500])
        if _FS_BALANCE_SHEET.search(text) or '재무제표' in text:
            fs_container = container
            break

    # fallback: library 없이 section 직계 자식에 재무제표가 있는 경우
    # (한국금융지주 등 — □ 재무제표 보고 <p> + <table> 이 section-2에 직접 나열)
    if not fs_container:
        # detail_section 자체를 컨테이너로 사용
        section_text = re.sub(r'\s+', '', detail_section.get_text()[:1000])
        if '재무제표' in section_text or '재무상태표' in section_text:
            # table이 직접 있는지 확인
            direct_tables = [t for t in detail_section.find_all('table', recursive=False)]
            if direct_tables:
                fs_container = detail_section
                logger.info("재무제표 파싱: library 없이 section에서 직접 발견")

    if not fs_container:
        logger.warning("재무제표 파싱: 재무제표 library를 찾을 수 없음")
        return _empty_financial_result()

    # 데이터 테이블 수집 — 행 5개 이상, 첫 행에 '과목' 포함
    result = {
        "consolidated": {"balance_sheet": None, "income_statement": None},
        "separate": {"balance_sheet": None, "income_statement": None},
    }

    # 현재 컨텍스트 추적 — 문서에 "연결" 키워드가 있으면 연결부터, 없으면 별도
    fs_text = re.sub(r'\s+', '', fs_container.get_text()[:3000])
    has_consolidated = bool(_FS_CONSOLIDATED.search(fs_text))
    is_consolidated = has_consolidated  # "연결" 없으면 기본값 = 별도
    current_stmt_type = None  # 'balance_sheet' or 'income_statement'

    for child in fs_container.descendants:
        if not hasattr(child, 'name') or not child.name:
            continue

        text = child.get_text().strip()

        # <p> 헤딩으로 컨텍스트 갱신
        if child.name == 'p' and text:
            text_clean = re.sub(r'\s+', '', text)

            # 연결/별도 — "별도 및 연결" 처럼 둘 다 있으면 순서 기반 (먼저 나온 쪽)
            has_cons = bool(_FS_CONSOLIDATED.search(text_clean))
            has_sepa = bool(_FS_SEPARATE.search(text_clean))
            if has_cons and has_sepa:
                # 둘 다 있으면 텍스트에서 먼저 나오는 쪽으로
                cons_pos = _FS_CONSOLIDATED.search(text_clean).start()
                sepa_pos = _FS_SEPARATE.search(text_clean).start()
                is_consolidated = cons_pos < sepa_pos
            elif has_sepa:
                is_consolidated = False
            elif has_cons:
                is_consolidated = True

            # 재무제표 유형 — 현금흐름표/자본변동표는 None으로 설정하여 스킵
            if re.search(r'현금흐름', text_clean):
                current_stmt_type = None  # 스킵 대상
            elif re.search(r'자본변동', text_clean):
                current_stmt_type = None  # 스킵 대상
            elif re.search(r'이익잉여금처분|결손금처리', text_clean):
                current_stmt_type = None  # 스킵 대상
            elif _FS_BALANCE_SHEET.search(text_clean):
                current_stmt_type = 'balance_sheet'
            elif _FS_INCOME_STMT.search(text_clean):
                current_stmt_type = 'income_statement'
            continue

        # 제목 테이블에서도 컨텍스트 갱신 (단일 셀 테이블)
        if child.name == 'table':
            rows = child.find_all('tr')
            if len(rows) <= 4:
                # 제목/메타 테이블 — 컨텍스트 갱신
                table_text = child.get_text()
                table_text_clean = re.sub(r'\s+', '', table_text)

                if _FS_SEPARATE.search(table_text):
                    is_consolidated = False
                elif _FS_CONSOLIDATED.search(table_text):
                    is_consolidated = True

                if re.search(r'현금흐름|자본변동|이익잉여금처분|결손금처리', table_text_clean):
                    current_stmt_type = None  # 스킵 대상
                elif _FS_BALANCE_SHEET.search(table_text_clean):
                    current_stmt_type = 'balance_sheet'
                    # "연결" 없이 단독 "재무상태표" = 별도
                    if not _FS_CONSOLIDATED.search(table_text) and not _FS_SEPARATE.search(table_text):
                        scope_check = "consolidated" if is_consolidated else "separate"
                        if result[scope_check]["balance_sheet"] is not None:
                            is_consolidated = False
                elif _FS_INCOME_STMT.search(table_text_clean):
                    current_stmt_type = 'income_statement'
                    if not _FS_CONSOLIDATED.search(table_text) and not _FS_SEPARATE.search(table_text):
                        scope_check = "consolidated" if is_consolidated else "separate"
                        if result[scope_check]["income_statement"] is not None:
                            is_consolidated = False
                continue

            # 데이터 테이블 판별: 행 5개+, 첫 행에 '과목'/'구분' 또는 기간 라벨
            first_cells = [c.get_text().strip() for c in rows[0].find_all(['td', 'th'])]
            first_cells_clean = [re.sub(r'\s+', '', c) for c in first_cells]
            is_data_table = any(
                ('과' in c and '목' in c) or ('구' in c and '분' in c)
                for c in first_cells_clean
            )
            # 빈 첫 셀 + 기간 라벨(제N기, 당기, 전기) → 데이터 테이블
            if not is_data_table and len(first_cells_clean) >= 2:
                has_period = any(
                    re.match(r'제?\d+기', c) or c in ('당기', '전기', '당기말', '전기말')
                    for c in first_cells_clean
                )
                if has_period:
                    is_data_table = True
            if not is_data_table:
                continue

            # stmt_type이 None이면 내용 기반 추론
            if current_stmt_type is None:
                current_stmt_type = _infer_statement_type(child)
            if current_stmt_type is None:
                continue

            # 이미 채워진 슬롯이면 → 다음 stmt_type 또는 다음 scope
            scope = "consolidated" if is_consolidated else "separate"
            if result[scope][current_stmt_type] is not None:
                # 같은 scope에서 다음 statement type 시도
                other = "income_statement" if current_stmt_type == "balance_sheet" else "balance_sheet"
                if result[scope][other] is None:
                    inferred = _infer_statement_type(child)
                    if inferred and inferred == other:
                        current_stmt_type = other
                    else:
                        continue
                else:
                    # 이 scope 다 채워짐 → 다음 scope로
                    is_consolidated = not is_consolidated
                    scope = "consolidated" if is_consolidated else "separate"
                    current_stmt_type = _infer_statement_type(child)
                    if current_stmt_type is None or result[scope][current_stmt_type] is not None:
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
                # note 컬럼 제거 — normalized가 4컬럼이면 [0,2,3], 3컬럼이면 그대로
                if normalized and len(normalized[0]) == 4:
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

    # 이익잉여금처분계산서
    result["retained_earnings"] = _extract_retained_earnings(fs_container)

    # 자본변동표 — 연결/별도 각각
    result["consolidated"]["equity_changes"] = None
    result["separate"]["equity_changes"] = None
    _extract_equity_changes(fs_container, result)

    return result


def _infer_statement_type(table_el) -> str | None:
    """데이터 테이블의 내용을 보고 재무상태표/손익계산서 추론

    첫 5행의 과목명으로 판별:
    - 자산 + (유동자산 or 비유동자산) → balance_sheet
    - 매출 or 영업이익 → income_statement
    - 자본금 + 자본잉여금 → equity_changes (스킵 대상)
    """
    rows = table_el.find_all('tr')
    first_cells = []
    for row in rows[:7]:
        cell = row.find(['td', 'th'])
        if cell:
            first_cells.append(re.sub(r'\s+', '', cell.get_text()))

    sample = ' '.join(first_cells)

    # 자본변동표 제외: 첫 컬럼이 "과목"이고 나머지 헤더에 "자본금", "자본잉여금" 등
    header_row = rows[0] if rows else None
    if header_row:
        all_headers = [re.sub(r'\s+', '', c.get_text()) for c in header_row.find_all(['td', 'th'])]
        if any('자본금' in h for h in all_headers) and any('잉여금' in h for h in all_headers):
            return None  # 자본변동표 → 스킵

    # 현금흐름표 제외: "영업활동", "투자활동", "재무활동"
    if re.search(r'영업활동|투자활동|재무활동', sample):
        return None

    # 재무상태표: 첫 행들에 "자산" + "유동자산"/"비유동자산"
    if re.search(r'자산', sample) and re.search(r'유동자산|비유동자산|총계', sample):
        return 'balance_sheet'

    # 손익계산서: "매출" 또는 "영업이익"/"영업손익"
    if re.search(r'매출|영업이익|영업손익|영업수익|계속영업', sample):
        return 'income_statement'

    return None


def _extract_equity_changes(container, result: dict) -> None:
    """자본변동표 추출 — 연결/별도 각각

    자본변동표 테이블 식별: 헤더에 '자본금' + '잉여금' 포함, 행 10개+
    자사주 취득/소각 플래그도 추출.
    """
    is_consolidated = True
    found_scopes = set()

    for child in container.descendants:
        if not hasattr(child, 'name') or not child.name:
            continue

        # <p> 또는 제목 테이블에서 연결/별도 컨텍스트
        if child.name == 'p':
            text_clean = re.sub(r'\s+', '', child.get_text())
            if _FS_SEPARATE.search(text_clean):
                is_consolidated = False
            elif _FS_CONSOLIDATED.search(text_clean) and '별도' not in text_clean:
                is_consolidated = True
            # 자본변동표 제목 감지
            if re.search(r'자본변동', text_clean):
                pass  # 컨텍스트 유지
            continue

        if child.name != 'table':
            continue

        rows = child.find_all('tr')
        if len(rows) < 8:
            # 제목 테이블에서 컨텍스트 갱신
            table_text = re.sub(r'\s+', '', child.get_text())
            if _FS_SEPARATE.search(table_text):
                is_consolidated = False
            elif _FS_CONSOLIDATED.search(table_text):
                is_consolidated = True
            continue

        # 자본변동표 판별: 헤더에 '자본금' + '잉여금'
        header_cells = rows[0].find_all(['td', 'th'])
        headers = [re.sub(r'\s+', '', c.get_text()) for c in header_cells]
        if not (any('자본금' in h for h in headers) and any('잉여금' in h for h in headers)):
            continue

        scope = "consolidated" if is_consolidated else "separate"
        if scope in found_scopes:
            # 이미 이 scope에서 찾았으면 다음 scope로
            is_consolidated = not is_consolidated
            scope = "consolidated" if is_consolidated else "separate"
            if scope in found_scopes:
                continue
        found_scopes.add(scope)

        # 단위 추출
        unit = _extract_unit_from_siblings(child)

        # 컬럼 메타데이터
        columns = [h for h in headers]

        # 행 데이터 추출
        data_rows = []
        has_treasury_acquisition = False
        has_treasury_disposal = False

        for row in rows[1:]:
            cells = row.find_all(['td', 'th'])
            expanded = []
            for c in cells:
                val = c.get_text().strip().replace('\n', ' ')
                colspan = int(c.get('colspan', 1) or 1)
                expanded.append(val)
                for _ in range(colspan - 1):
                    expanded.append('')
            # 컬럼 수 맞추기
            while len(expanded) < len(columns):
                expanded.append('')
            data_rows.append(expanded[:len(columns)])

            # 자사주 플래그
            first = re.sub(r'\s+', '', expanded[0]) if expanded else ''
            if '자기주식' in first and '취득' in first:
                has_treasury_acquisition = True
            if '자기주식' in first and ('소각' in first or '처분' in first):
                has_treasury_disposal = True

        result[scope]["equity_changes"] = {
            "unit": unit,
            "columns": columns,
            "column_count": len(columns),
            "rows": data_rows,
            "row_count": len(data_rows),
            "has_treasury_acquisition": has_treasury_acquisition,
            "has_treasury_disposal": has_treasury_disposal,
            "scope": scope,
        }


def _extract_retained_earnings(container) -> dict | None:
    """이익잉여금처분계산서 전체 추출

    Returns:
        {"unit": "백만원", "disposal_date": "2026년 3월 26일",
         "items": [{"account": "배당금", "current": "477,528", "prior": "453,068"}, ...]}
    """
    # 이익잉여금처분계산서 테이블 찾기
    # 제목 테이블과 데이터 테이블이 분리된 경우가 있으므로,
    # "미처분이익잉여금" 키워드가 있는 테이블 또는 "이익잉여금처분" 키워드 + 행 5개 이상
    for table in container.find_all('table'):
        table_text = re.sub(r'\s+', '', table.get_text())

        rows = table.find_all('tr')

        # 미처분이익잉여금이 있으면 데이터 테이블 확실
        is_data = '미처분이익' in table_text or '미처분이익잉여금' in table_text
        # 이익잉여금처분 + 행 5개 이상 + 첫 행에 과목/구분/기간
        if not is_data and '이익잉여금처분' in table_text and len(rows) >= 5:
            first_cells = [re.sub(r'\s+', '', c.get_text()) for c in rows[0].find_all(['td', 'th'])]
            if any('과목' in c or '구분' in c or '당' in c or '전' in c for c in first_cells):
                is_data = True

        if not is_data:
            continue
        if len(rows) < 3:
            continue

        # 단위 추출
        unit = _extract_unit_from_siblings(table)
        # 테이블 내에서도 확인
        if not unit:
            for row in rows[:2]:
                row_text = row.get_text()
                m = _FS_UNIT.search(row_text)
                if m:
                    unit = m.group(1).strip()
                    break

        # 처분예정일/확정일
        disposal_date = None
        for row in rows[:3]:
            row_text = row.get_text()
            m = re.search(r'처분예정일\s*[:：]?\s*(\d{4}년\s*\d{1,2}월\s*\d{1,2}일)', row_text)
            if m:
                disposal_date = m.group(1)
                break

        # 전체 행 추출 (헤더/빈 행 제외)
        items = []
        for row in rows:
            cells = row.find_all(['td', 'th'])
            if not cells:
                continue
            first_cell = re.sub(r'\s+', ' ', cells[0].get_text().strip())
            if not first_cell:
                continue
            # 헤더 행 스킵 (구분/과목/단위)
            first_clean = re.sub(r'\s+', '', first_cell)
            if first_clean in ('구분', '과목') or '단위' in first_clean:
                continue
            # 처분예정일/확정일 행 스킵 (메타데이터로 이미 추출)
            if '처분예정일' in first_cell or '처분확정일' in first_cell:
                continue

            values = [c.get_text().strip().replace('\n', ' ') for c in cells]
            nums = [v for v in values[1:] if v and v != '-']
            current = nums[0] if len(nums) >= 1 else ""
            prior = nums[1] if len(nums) >= 2 else ""
            items.append({
                "account": first_cell,
                "current": current,
                "prior": prior,
            })

        if items:
            # 배당 실시 여부 판단
            div_items = [i for i in items if any(
                kw in i['account'] for kw in ['배당금', '현금배당', '중간배당']
            )]
            has_dividend = any(
                i['current'] and '해당사항' not in i['account']
                for i in div_items
            )

            return {
                "unit": unit,
                "disposal_date": disposal_date,
                "has_dividend": has_dividend,
                "items": items,
            }

    return None


def _empty_financial_result() -> dict:
    return {
        "consolidated": {"balance_sheet": None, "income_statement": None},
        "separate": {"balance_sheet": None, "income_statement": None},
        "retained_earnings": None,
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
        if ('과' in clean and '목' in clean) or ('구' in clean and '분' in clean):
            columns.append("account")
        elif '주석' in clean:
            columns.append("note")
        elif '당' in clean:
            columns.append("current")
        elif '전' in clean:
            columns.append("prior")
        elif re.match(r'제?\d+기', clean):
            # 제N기, 제N기말 — 기수 번호로 당기/전기 추론 (큰 번호 = 당기)
            columns.append("_period_by_num")
        elif not clean:
            # 빈 셀 — colspan 확장분, 앞 컬럼의 서브컬럼
            if columns and columns[-1] in ("current", "prior"):
                columns.append(f"{columns[-1]}_sub")
            else:
                columns.append("unknown")
        else:
            columns.append("unknown")

    # _period_by_num → current/prior 변환 (기수 번호 큰 게 당기)
    period_indices = [i for i, c in enumerate(columns) if c == "_period_by_num"]
    if len(period_indices) >= 2:
        # 헤더 셀에서 기수 번호 추출
        nums = []
        for idx in period_indices:
            m = re.search(r'(\d+)', re.sub(r'\s+', '', header_cells[idx]))
            nums.append(int(m.group(1)) if m else 0)
        # 큰 번호 = current
        if nums[0] >= nums[1]:
            columns[period_indices[0]] = "current"
            columns[period_indices[1]] = "prior"
        else:
            columns[period_indices[0]] = "prior"
            columns[period_indices[1]] = "current"
    elif len(period_indices) == 1:
        columns[period_indices[0]] = "current"

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
    period_candidates = []  # (기수번호, 라벨) — 당/전 없는 경우 기수로 추론

    for cell in header_cells:
        cell_clean = re.sub(r'\s+', '', cell)
        if '당' in cell_clean:
            labels["current"] = cell.strip()
        elif '전' in cell_clean:
            labels["prior"] = cell.strip()
        elif re.match(r'(?:20)?\d{2,4}년', cell_clean):
            if not labels["current"]:
                labels["current"] = cell.strip()
            else:
                labels["prior"] = cell.strip()
        elif re.match(r'제?\d+기', cell_clean):
            # 제N기, 제N기말 — 기수 번호 추출
            m = re.search(r'(\d+)', cell_clean)
            if m:
                period_candidates.append((int(m.group(1)), cell.strip()))

    # 당/전 라벨이 없으면 기수 번호로 추론
    if not labels["current"] and not labels["prior"] and len(period_candidates) >= 2:
        period_candidates.sort(key=lambda x: x[0], reverse=True)
        labels["current"] = period_candidates[0][1]
        labels["prior"] = period_candidates[1][1]

    return labels


# ── 보수한도 파싱 ──

_COMPENSATION_KEYWORDS = ['보수한도', '보수 한도', '보수의 한도', '보수액한도', '보수액 한도']


def parse_compensation_xml(html: str) -> dict:
    """보수한도 안건에서 당기/전기 보수 정보를 정규화 추출

    DART 표준 서식:
      가. 이사의 수ㆍ보수총액 내지 최고 한도액
        (당기) 이사의 수(사외이사수) / 보수총액 또는 최고한도액
        (전기) 이사의 수(사외이사수) / 실제 지급된 보수총액 / 최고한도액

    Returns:
        {"items": [...], "summary": {...}}
        각 item: {"number", "title", "target", "current", "prior", "notes"}
    """
    details = parse_agenda_details_xml(html)
    if not details:
        return {"items": [], "summary": _empty_compensation_summary()}

    items = []

    for d in details:
        title = d.get("title", "")
        if not any(kw in title for kw in _COMPENSATION_KEYWORDS):
            continue

        # 대상 분류: 이사 / 감사
        target = "이사"
        if '감사' in title and '감사위원' not in title:
            target = "감사"

        current = {}  # 당기
        prior = {}    # 전기
        notes = []
        extra_tables = []  # 보수 산정 기준, 지급 내역 등

        # 섹션 순회 — 당기/전기 테이블 추출
        phase = None  # "current" or "prior"
        for sec in d.get("sections", []):
            for block in sec.get("blocks", []):
                btype = block["type"]
                content = block["content"].strip()

                if btype == "text":
                    text_lower = content.replace(" ", "")
                    if "당기" in text_lower or "당 기" in content:
                        phase = "current"
                    elif "전기" in text_lower or "전 기" in content:
                        phase = "prior"
                    # 기수 패턴 (제N기)
                    elif re.match(r'제?\s*\d+\s*기', content):
                        if not current:
                            phase = "current"
                        elif not prior:
                            phase = "prior"
                    # 연도 패턴 (2026년) / (2025년) — 최신 연도가 당기
                    elif re.match(r'[\(（]?\s*\d{4}\s*년?\s*[\)）]?$', content.strip()):
                        if not current:
                            phase = "current"
                        elif not prior:
                            phase = "prior"

                elif btype == "note":
                    notes.append(content)

                elif btype == "table":
                    rows = _parse_md_table(content)
                    if len(rows) < 2:
                        continue

                    # 핵심 테이블 (이사의 수 / 보수총액) vs 부가 테이블 구분
                    first_cell = rows[0][0].replace(" ", "") if rows[0] else ""
                    is_core = any(kw in first_cell for kw in [
                        "이사의수", "이사수", "감사의수", "감사수", "보수총액",
                    ])

                    if is_core and not phase:
                        # phase 미감지 상태에서 핵심 테이블 등장 → 순서로 추론
                        phase = "current" if not current else "prior"

                    if is_core and phase:
                        parsed = _parse_compensation_table(rows)
                        if phase == "current":
                            current = parsed
                        elif phase == "prior":
                            prior = parsed
                    elif not is_core and rows[0] and len(rows[0]) >= 2:
                        # 보수 산정 기준, 지급 내역 등 부가 테이블
                        extra_tables.append({
                            "headers": rows[0],
                            "rows": rows[1:],
                        })

        item = {
            "number": d.get("number", ""),
            "title": title,
            "target": target,
            "current": current,
            "prior": prior,
            "notes": notes,
        }
        if extra_tables:
            item["extraTables"] = extra_tables
        items.append(item)

    summary = _build_compensation_summary(items)
    return {"items": items, "summary": summary}


def _parse_compensation_table(rows: list[list[str]]) -> dict:
    """보수한도 핵심 테이블 파싱 (key-value 2컬럼 구조)

    | 이사의 수 (사외이사수) | 8(5) |
    | 보수총액 또는 최고한도액 | 450억원 |
    """
    result = {}
    for row in rows:
        if len(row) < 2:
            continue
        key = row[0].replace(" ", "").strip()
        val = row[1].strip()

        if any(kw in key for kw in ["이사의수", "이사수", "감사의수", "감사수"]):
            # 공백 정리
            result["headcount"] = re.sub(r'\s+', '', val)
            # 사외이사수 추출
            m = re.search(r'(\d+)\s*[\(（]\s*(\d+)\s*[\)）]', val)
            if m:
                result["totalDirectors"] = int(m.group(1))
                result["outsideDirectors"] = int(m.group(2))
            else:
                m2 = re.search(r'(\d+)', val)
                if m2:
                    result["totalDirectors"] = int(m2.group(1))

        elif "최고한도" in key or "보수총액또는" in key or "한도액" in key:
            result["limit"] = val
            result["limitAmount"] = _parse_krw_amount(val)

        elif "실제지급" in key or "지급된보수" in key:
            result["actualPaid"] = val
            result["actualPaidAmount"] = _parse_krw_amount(val)

        elif "보수총액" in key:
            # "보수총액 또는 최고한도액" 과 구별
            if "최고" not in key and "한도" not in key:
                result["actualPaid"] = val
                result["actualPaidAmount"] = _parse_krw_amount(val)

    return result


def _parse_krw_amount(text: str) -> int | None:
    """금액 문자열을 원 단위 정수로 변환

    Examples:
        "450억원" → 45_000_000_000
        "6,000백만원" → 6_000_000_000
        "15,000백만원+30,000주" → 15_000_000_000  (주식 부분 무시)
        "100억원" → 10_000_000_000
    """
    if not text:
        return None

    # 주식 부분 제거
    text = re.split(r'[+＋]', text)[0].strip()
    # 콤마 제거
    text = text.replace(",", "")

    # 억원
    m = re.search(r'([\d.]+)\s*억\s*원?', text)
    if m:
        return int(float(m.group(1)) * 100_000_000)

    # 백만원
    m = re.search(r'([\d.]+)\s*백만\s*원?', text)
    if m:
        return int(float(m.group(1)) * 1_000_000)

    # 천원
    m = re.search(r'([\d.]+)\s*천\s*원?', text)
    if m:
        return int(float(m.group(1)) * 1_000)

    # 원 (단위 없이 숫자만)
    m = re.search(r'(\d+)\s*원?$', text)
    if m:
        return int(m.group(1))

    return None


def _build_compensation_summary(items: list[dict]) -> dict:
    """보수한도 요약"""
    total_limit = 0
    total_prior_paid = 0
    total_prior_limit = 0

    for item in items:
        cur = item.get("current", {})
        pri = item.get("prior", {})
        if cur.get("limitAmount"):
            total_limit += cur["limitAmount"]
        if pri.get("actualPaidAmount"):
            total_prior_paid += pri["actualPaidAmount"]
        if pri.get("limitAmount"):
            total_prior_limit += pri["limitAmount"]

    utilization = None
    if total_prior_limit > 0 and total_prior_paid > 0:
        utilization = round(total_prior_paid / total_prior_limit * 100, 1)

    return {
        "totalItems": len(items),
        "currentTotalLimit": total_limit if total_limit else None,
        "priorTotalPaid": total_prior_paid if total_prior_paid else None,
        "priorTotalLimit": total_prior_limit if total_prior_limit else None,
        "priorUtilization": utilization,
    }


def _empty_compensation_summary() -> dict:
    return {
        "totalItems": 0,
        "currentTotalLimit": None,
        "priorTotalPaid": None,
        "priorTotalLimit": None,
        "priorUtilization": None,
    }


# ── 자기주식 파싱 ──

_TREASURY_KEYWORDS = ['자기주식', '자사주', '자본의 감소', '자본금 감소']
_TREASURY_CANCEL_KW = ['소각', '자본금 감소', '자본감소', '자본의 감소']


def parse_treasury_share_xml(html: str) -> dict:
    """자기주식 보유/처분/소각 안건 파싱

    Returns:
        {"items": [...], "summary": {...}}
        각 item: {"type", "title", "purpose", "shares", "schedule", "tables", "notes"}
    """
    details = parse_agenda_details_xml(html)
    if not details:
        return {"items": [], "summary": {"totalItems": 0}}

    items = []

    for d in details:
        title = d.get("title", "")
        # 제목 매칭 또는 본문에 자기주식 키워드
        if not any(kw in title for kw in _TREASURY_KEYWORDS):
            continue
        # 정관변경/규정/상법 관련 세부의안 제외
        if any(kw in title for kw in ['정관', '규정', '상법', '지분 유동화']):
            continue
        # 자기주식 안건의 제목 패턴: "보유", "처분", "소각", "계획", "자본감소" 중 하나 포함
        if not any(kw in title for kw in ['보유', '처분', '소각', '계획', '감소']):
            continue

        # 유형 분류
        item_type = "cancel" if any(kw in title for kw in _TREASURY_CANCEL_KW) else "hold_dispose"

        purpose = ""
        shares_info = []
        schedule = []
        tables = []
        notes = []

        for sec in d.get("sections", []):
            heading = sec.get("heading") or ""

            for block in sec.get("blocks", []):
                btype = block["type"]
                content = block["content"].strip()

                if btype == "text":
                    # 목적 추출
                    if "목적" in heading and not purpose:
                        purpose = content[:200]
                    # 스케줄 추출
                    if any(kw in heading for kw in ["기간", "시기", "시점"]):
                        schedule.append(content[:200])
                elif btype == "table":
                    rows = _parse_md_table(content)
                    if rows and len(rows) >= 2:
                        tables.append({
                            "headers": rows[0],
                            "rows": rows[1:],
                        })
                        # 주식수 추출 시도
                        for row in rows[1:]:
                            for cell in row:
                                m = re.search(r'([\d,]+)\s*주', cell)
                                if m:
                                    shares_info.append(cell.strip())
                elif btype == "note":
                    notes.append(content)

            # heading에서 목적 추출
            if "목적" in heading and not purpose:
                purpose = heading[:200]

        item = {
            "number": d.get("number", ""),
            "title": title,
            "type": item_type,
            "purpose": purpose,
            "sharesInfo": shares_info[:5],
            "schedule": schedule,
            "tables": tables,
            "notes": notes,
        }
        items.append(item)

    return {"items": items, "summary": {"totalItems": len(items)}}


# ── 자본준비금 파싱 ──

def parse_capital_reserve_xml(html: str) -> dict:
    """자본준비금 감소/이익잉여금 전입 안건 파싱

    Returns:
        {"items": [...], "summary": {...}}
    """
    details = parse_agenda_details_xml(html)
    if not details:
        return {"items": [], "summary": {"totalItems": 0}}

    items = []

    for d in details:
        title = d.get("title", "")
        if "자본준비금" not in title:
            continue
        if any(kw in title for kw in ["재무제표", "재무상태표", "대차대조표"]):
            continue

        amount = None
        purpose = ""
        notes = []

        for sec in d.get("sections", []):
            for block in sec.get("blocks", []):
                content = block["content"].strip()

                if block["type"] == "text":
                    # 금액 추출 (N조원, N억원, N원)
                    if not amount:
                        m = re.search(r'([\d,.]+)\s*(조|억|백만|천)?\s*원', content)
                        if m:
                            amount = m.group(0).strip()
                    # 목적 추출
                    if not purpose and ("목적" in content or "이입" in content or "전입" in content):
                        purpose = content[:300]
                elif block["type"] == "note":
                    notes.append(content)

        item = {
            "number": d.get("number", ""),
            "title": title,
            "amount": amount,
            "purpose": purpose,
            "reducedCapital": True,
            "notes": notes,
        }
        items.append(item)

    return {
        "items": items,
        "summary": {
            "totalItems": len(items),
            "reducedCapital": len(items) > 0,
        },
    }


# ── 퇴직금 규정 파싱 ──

def parse_retirement_pay_xml(html: str) -> dict:
    """임원 퇴직금 규정 개정 안건 파싱 (변경전/변경후 테이블)

    정관변경(aoi)과 같은 패턴: 현행/개정안 비교 테이블
    Returns:
        {"amendments": [...], "summary": {...}}
    """
    details = parse_agenda_details_xml(html)
    if not details:
        return {"amendments": [], "summary": {"totalAmendments": 0}}

    amendments = []

    for d in details:
        title = d.get("title", "")
        if "퇴직금" not in title and "퇴직위로금" not in title:
            continue

        for sec in d.get("sections", []):
            for block in sec.get("blocks", []):
                if block["type"] != "table":
                    continue

                rows = _parse_md_table(block["content"])
                if len(rows) < 2:
                    continue

                headers = rows[0]
                headers_clean = [re.sub(r'\s+', '', h) for h in headers]

                # 현행/변경전 + 개정/변경후 컬럼 찾기
                before_idx = -1
                after_idx = -1
                reason_idx = -1

                for ci, h in enumerate(headers_clean):
                    if any(kw in h for kw in ['변경전', '현행']):
                        before_idx = ci
                    if any(kw in h for kw in ['변경후', '개정안', '개정', '변경(안)']):
                        after_idx = ci
                    if any(kw in h for kw in ['목적', '비고', '사유']):
                        reason_idx = ci

                if before_idx < 0 or after_idx < 0:
                    continue

                for row in rows[1:]:
                    if len(row) <= max(before_idx, after_idx):
                        continue
                    before = row[before_idx].strip()
                    after = row[after_idx].strip()
                    reason = row[reason_idx].strip() if reason_idx >= 0 and reason_idx < len(row) else ""

                    if not before and not after:
                        continue

                    # 조항 추출
                    clause = ""
                    for text in [before, after]:
                        m = re.search(r'제\s*\d+\s*조', text)
                        if m:
                            clause = m.group(0).strip()
                            break

                    amendments.append({
                        "clause": clause,
                        "before": before,
                        "after": after,
                        "reason": reason,
                    })

    return {"amendments": amendments, "summary": {"totalAmendments": len(amendments)}}


# ── 범용 구조 추출기 (Generic Structural Extractor) ──

def extract_structural_elements(html: str, agenda_no: str = "") -> dict:
    """안건 유형 상관없이 공통 구조 요소를 기계적으로 추출

    모든 안건에 적용 가능. 파서가 없는 안건의 핵심 데이터 포인트 추출.

    Args:
        html: 문서 HTML
        agenda_no: 특정 안건 번호 (빈 문자열이면 전체)

    Returns:
        {"tables": [...], "amounts": [...], "dates": [...],
         "names": [...], "legalRefs": [...], "percentages": [...]}
    """
    details = parse_agenda_details_xml(html)
    if not details:
        return _empty_extracted()

    target_details = details
    if agenda_no:
        target_details = [d for d in details if d.get("number", "") == agenda_no]
        if not target_details:
            target_details = details

    tables = []
    amounts = []
    dates = []
    names = []
    legal_refs = []
    percentages = []

    for d in target_details:
        for sec in d.get("sections", []):
            for block in sec.get("blocks", []):
                content = block.get("content", "")
                btype = block["type"]

                if btype == "table":
                    rows = _parse_md_table(content)
                    if rows and len(rows) >= 2:
                        tables.append({
                            "headers": rows[0],
                            "rows": rows[1:],
                        })

                # 금액 패턴
                for m in re.finditer(r'[\d,]+\.?\d*\s*(?:조|억|백만|천)?\s*원', content):
                    val = m.group(0).strip()
                    if val not in amounts:
                        amounts.append(val)

                # 날짜 패턴
                for m in re.finditer(r'\d{4}\s*[년.]\s*\d{1,2}\s*[월.]\s*\d{1,2}\s*일?', content):
                    val = m.group(0).strip()
                    if val not in dates:
                        dates.append(val)
                for m in re.finditer(r'\d{4}\.\d{1,2}\.\d{1,2}', content):
                    val = m.group(0).strip()
                    if val not in dates:
                        dates.append(val)

                # 인명 패턴 — 직위 바로 앞뒤의 2-4자 한글만
                # 일반 명사 제외 (의결권, 주주총회 등에서 오감지 방지)
                _NAME_EXCLUDE = {'주주총회', '이사회', '감사위원회', '대표이사', '사외이사',
                    '사내이사', '선임', '해임', '재무제표', '별도', '연결', '총수의', '명칭',
                    '회사의', '총회에서', '항의', '무제표는', '일자', '이상의', '최초로', '후보의'}
                for m in re.finditer(r'(?:대표이사|사장|부사장|전무|상무)\s+([가-힣]{2,4})\b', content):
                    val = m.group(1)
                    if val not in names and val not in _NAME_EXCLUDE and len(val) <= 4:
                        names.append(val)
                for m in re.finditer(r'([가-힣]{2,4})\s+(?:대표이사|사장|부사장|전무|상무)\b', content):
                    val = m.group(1)
                    if val not in names and val not in _NAME_EXCLUDE and len(val) <= 4:
                        names.append(val)
                for m in re.finditer(r'(?:후보자?|후보)\s*[：:)]\s*([가-힣]{2,4})', content):
                    val = m.group(1)
                    if val not in names and val not in _NAME_EXCLUDE:
                        names.append(val)

                # 법령 참조
                for m in re.finditer(r'(?:상법|자본시장법|금융회사의 지배구조에 관한 법률|공정거래법)\s*제?\s*\d+조(?:의\d+)?', content):
                    val = m.group(0).strip()
                    if val not in legal_refs:
                        legal_refs.append(val)

                # 비율
                for m in re.finditer(r'[\d.]+\s*%', content):
                    val = m.group(0).strip()
                    if val not in percentages:
                        percentages.append(val)

    return {
        "tables": tables[:10],
        "amounts": amounts[:20],
        "dates": dates[:10],
        "names": names[:20],
        "legalRefs": legal_refs[:10],
        "percentages": percentages[:10],
    }


def get_agenda_contents(html: str, agenda_no: str = "") -> dict:
    """안건의 rawContents(HTML) + mdContents(마크다운) 추출

    Args:
        html: 문서 전체 HTML
        agenda_no: 특정 안건 번호 (빈 문자열이면 전체)

    Returns:
        {"rawContents": "<html>...", "mdContents": "## 제1호..."}
    """
    details = parse_agenda_details_xml(html)
    if not details:
        return {"rawContents": "", "mdContents": ""}

    target_details = details
    if agenda_no:
        target_details = [d for d in details if d.get("number", "") == agenda_no]

    # mdContents: sections/blocks를 마크다운으로 변환
    md_lines = []
    for d in target_details:
        title = d.get("title", "")
        number = d.get("number", "")
        if number:
            md_lines.append(f"## {number}: {title}")
        else:
            md_lines.append(f"## {title}")
        md_lines.append("")

        for sec in d.get("sections", []):
            heading = sec.get("heading", "")
            if heading:
                md_lines.append(f"### {heading}")
                md_lines.append("")

            for block in sec.get("blocks", []):
                btype = block["type"]
                content = block["content"]
                if btype == "table":
                    md_lines.append(content)
                    md_lines.append("")
                elif btype == "note":
                    md_lines.append(f"> {content}")
                    md_lines.append("")
                else:
                    md_lines.append(content)
                    md_lines.append("")

    md_contents = "\n".join(md_lines)

    # rawContents: 해당 안건의 HTML 영역 추출
    # 간소화: 전체 HTML에서 해당 안건 제목 주변을 잡기엔 복잡하므로
    # mdContents 기반으로 역매핑은 어려움 → 전체 HTML 중 해당 구간 표시
    raw_contents = ""
    if target_details:
        # 첫 안건 제목으로 HTML에서 위치 찾기
        first_title = target_details[0].get("title", "")
        if first_title:
            soup = BeautifulSoup(html, _BS4_PARSER)
            for el in soup.find_all(string=re.compile(re.escape(first_title[:20]))):
                # 부모 section 찾기
                parent = el.parent
                for _ in range(5):
                    if parent and parent.name and 'section' in (parent.get('class', []) or [str(parent.name)]):
                        raw_contents = str(parent)
                        break
                    if parent:
                        parent = parent.parent
                if raw_contents:
                    break
            if not raw_contents:
                raw_contents = html[:500] + "..."  # fallback

    return {
        "rawContents": raw_contents,
        "mdContents": md_contents,
    }


def _empty_extracted() -> dict:
    return {
        "tables": [],
        "amounts": [],
        "dates": [],
        "names": [],
        "legalRefs": [],
        "percentages": [],
    }
