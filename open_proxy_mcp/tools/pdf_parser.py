"""PDF 마크다운 파서 — opendataloader 출력물에서 AGM 데이터 구조화

opendataloader가 DART PDF를 마크다운으로 변환한 결과를 파싱합니다.
XML 파서(parser.py)의 보조 소스로 사용:
  - XML 파서 실패 시 PDF 결과로 대체
  - XML SOFT_FAIL 시 PDF 결과로 보강

3단계 fallback:
  1. opendataloader 마크다운 → current 파서
  2. 실패 시 → 키워드로 페이지 특정 → Upstage OCR → 재파싱
  3. 여전히 실패 → LLM fallback (AGM_CASE_RULE 예시)

마크다운 구조:
  - 테이블: | col1 | col2 | ... | 형식 (구분선 |---|---|)
  - 헤딩: #, ##, ###, ...
  - 텍스트: 일반 줄, - 리스트
  - <br> 태그가 셀 내 줄바꿈으로 사용됨
"""

import os
import re
import io
import logging

logger = logging.getLogger(__name__)


# ── Upstage OCR fallback ──

# 파서 타입별 키워드 (페이지 특정용)
_PARSER_KEYWORDS = {
    "comp": ['보수총액', '최고한도', '보수한도'],
    "pers": ['후보자성명', '세부경력', '주된직업'],
    "fin": ['자산총계', '유동자산', '재무상태표'],
    "aoi": ['변경전', '변경후', '현행', '개정'],
    "treasury": ['자기주식', '자사주', '자본감소'],
    "capital": ['자본준비금', '이익잉여금', '전입'],
    "retirement": ['퇴직금', '퇴직위로금', '현행', '개정'],
}


def find_pages_by_keywords(paged_md: str, keywords: list[str]) -> list[int]:
    """페이지 구분자가 있는 마크다운에서 키워드 포함 페이지 번호 추출"""
    pages = paged_md.split("<!-- PAGE ")
    found = []
    for i, page in enumerate(pages[1:], 1):
        page_text = page.split(" -->")[1] if " -->" in page else page
        if any(kw in page_text for kw in keywords):
            found.append(i)
    return found


def extract_pdf_pages(pdf_bytes: bytes, page_list: list[int]) -> bytes:
    """PDF에서 특정 페이지만 추출하여 새 PDF 바이트 반환"""
    try:
        from PyPDF2 import PdfReader, PdfWriter
    except ImportError:
        logger.warning("PyPDF2 미설치 — 페이지 추출 불가")
        return pdf_bytes

    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    for p in page_list:
        if 0 < p <= len(reader.pages):
            writer.add_page(reader.pages[p - 1])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def upstage_ocr_parse(pdf_bytes: bytes, filename: str = "document.pdf") -> str | None:
    """Upstage Document Parse API로 PDF → 마크다운 변환

    ⚠️ 파일 크기 제한 있음 (약 50MB). 페이지 추출 후 호출 권장.
    """
    api_key = os.getenv("UPSTAGE_API_KEY")
    if not api_key:
        logger.warning("UPSTAGE_API_KEY 미설정 — OCR fallback 불가")
        return None

    try:
        import httpx
    except ImportError:
        logger.warning("httpx 미설치 — OCR fallback 불가")
        return None

    url = "https://api.upstage.ai/v1/document-ai/document-parse"
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        files = {"document": (filename, io.BytesIO(pdf_bytes), "application/pdf")}
        data = {"output_formats": '["markdown"]'}
        response = httpx.post(url, headers=headers, files=files, data=data, timeout=180)

        if response.status_code != 200:
            logger.warning(f"Upstage API 에러: {response.status_code}")
            return None

        result = response.json()
        content = result.get('content', '')
        if isinstance(content, dict):
            return content.get('markdown', '')
        return str(content) if content else None

    except Exception as e:
        logger.warning(f"Upstage OCR 실패: {e}")
        return None


def ocr_fallback_for_parser(
    pdf_bytes: bytes,
    paged_md: str,
    parser_type: str,
    parser_fn,
    filename: str = "document.pdf",
) -> dict | list | None:
    """opendataloader 파서 실패 시 Upstage OCR fallback

    Args:
        pdf_bytes: 원본 PDF 바이너리
        paged_md: opendataloader 페이지 구분자 포함 마크다운
        parser_type: "comp", "pers", "fin", "aoi", "agenda"
        parser_fn: 해당 파서 함수 (parse_compensation_pdf 등)
        filename: PDF 파일명 (로깅용)

    Returns:
        파서 결과 또는 None (OCR도 실패 시)
    """
    keywords = _PARSER_KEYWORDS.get(parser_type, [])
    if not keywords:
        return None

    # 1. 키워드로 페이지 특정
    target_pages = find_pages_by_keywords(paged_md, keywords)
    if not target_pages:
        logger.info(f"[OCR fallback] {filename}/{parser_type}: 키워드 페이지 없음")
        return None

    # 앞뒤 1페이지 포함 (컨텍스트)
    expanded = set()
    for p in target_pages[:5]:
        for pp in [p - 1, p, p + 1]:
            if pp > 0:
                expanded.add(pp)
    page_list = sorted(expanded)[:10]  # 최대 10페이지

    logger.info(f"[OCR fallback] {filename}/{parser_type}: 페이지 {page_list}")

    # 2. 페이지 추출 + Upstage OCR
    extracted_pdf = extract_pdf_pages(pdf_bytes, page_list)
    ocr_md = upstage_ocr_parse(extracted_pdf, filename)
    if not ocr_md:
        return None

    # 3. OCR 마크다운에 파서 재실행
    try:
        result = parser_fn(ocr_md)
        logger.info(f"[OCR fallback] {filename}/{parser_type}: 성공")
        return result
    except Exception as e:
        logger.warning(f"[OCR fallback] {filename}/{parser_type}: 재파싱 실패 — {e}")
        return None


# ── 유틸리티 ──

def _parse_md_table_rows(lines: list[str], start: int) -> tuple[list[list[str]], int]:
    """마크다운 테이블을 행 리스트로 파싱. (rows, end_index) 반환.

    빈 줄을 만나면 테이블 종료. 연속된 | 행만 하나의 테이블로 인식.
    """
    rows = []
    i = start
    in_table = False
    while i < len(lines):
        line = lines[i].strip()

        # 빈 줄: 테이블 시작 전이면 스킵, 시작 후면 종료
        if not line:
            if in_table:
                i += 1
                break
            i += 1
            continue

        if line.startswith('|'):
            in_table = True
            if '---' in line and line.endswith('|'):
                i += 1
                continue
            # 멀티라인 합치기: |로 시작하지만 끝나지 않으면 다음 줄 합침
            full_line = line
            while not full_line.rstrip().endswith('|') and i + 1 < len(lines):
                i += 1
                next_l = lines[i].strip()
                if not next_l:
                    break
                full_line += ' ' + next_l
            if full_line.rstrip().endswith('|'):
                cells = [c.strip() for c in full_line[1:-1].split('|')]
                cells = [re.sub(r'<br\s*/?>', '\n', c).strip() for c in cells]
                rows.append(cells)
            i += 1
        elif in_table:
            # 테이블 행이 아닌 줄 → 종료
            break
        else:
            i += 1
    return rows, i


def _find_section(lines: list[str], keywords: list[str], start: int = 0) -> int:
    """키워드가 포함된 줄의 인덱스 반환. 없으면 -1."""
    for i in range(start, len(lines)):
        line = lines[i]
        if all(kw in line for kw in keywords):
            return i
    return -1


def _clean_br(text: str) -> str:
    """<br> 태그를 줄바꿈으로 변환."""
    return re.sub(r'<br\s*/?>', '\n', text).strip()


# ── 보수한도 파서 ──

def parse_compensation_pdf(md_text: str) -> dict:
    """PDF 마크다운에서 보수한도 정보 추출

    패턴:
      (당 기) 또는 당기 또는 제N기(YYYY년) 텍스트 다음에
      | 이사의 수 (사외이사수) | N(M) |
      | 보수총액 또는 최고한도액 | XXX억원 |

      (전 기) 또는 전기 텍스트 다음에
      | 이사의 수 (사외이사수) | N(M) |
      | 실제 지급된 보수총액 | XXX억원 |
      | 최고한도액 | XXX억원 |
    """
    lines = md_text.split('\n')
    items = []

    # 보수한도 본문 섹션 찾기
    # 전략 1: "가. 이사의 수" 패턴
    # 전략 2: 테이블에서 "보수총액 또는 최고한도액" 직접 찾기
    # 둘 다 시도하고, 실제 테이블 데이터가 있는 위치만 사용
    comp_starts = []

    # 전략 2를 기본으로 (더 직접적): 보수총액 테이블 위치 기반
    seen_positions = set()
    for i, line in enumerate(lines):
        if '보수총액또는최고한도액' in line.replace(' ', '') and '|' in line:
            if any(abs(i - p) < 20 for p in seen_positions):
                continue
            seen_positions.add(i)
            # 위로 올라가서 "이사의 수" 행 찾기 (같은 테이블의 시작)
            for j in range(max(0, i-5), i):
                if '이사의 수' in lines[j].replace(' ','') or '감사의 수' in lines[j].replace(' ',''):
                    comp_starts.append(j)
                    break
            else:
                comp_starts.append(max(0, i-3))

    if not comp_starts:
        # 전략 1 fallback: "가. 이사의 수" 패턴
        for i, line in enumerate(lines):
            if re.search(r'가\.\s*(?:이사|감사)의?\s*수', line.strip()):
                comp_starts.append(i)

    if not comp_starts:
        return {"items": [], "summary": _empty_compensation_summary()}

    # 각 보수한도 섹션에서 당기/전기 테이블 추출
    for sec_start in comp_starts:
        # 대상 분류 (이사/감사)
        target = "이사"
        sec_line = lines[sec_start].strip()
        if '감사' in sec_line and '감사위원' not in sec_line:
            target = "감사"

        # 당기/전기 phase 감지 + 테이블 파싱
        current = {}
        prior = {}
        notes = []
        phase = None
        comp_unit = ""  # 테이블 근처 단위 (단위:억원 등)
        title = f"{'감사' if target == '감사' else '이사'} 보수한도 승인"

        search_end = min(len(lines), sec_start + 50)
        i = sec_start
        while i < search_end:
            line = lines[i].strip()

            # 다음 섹션 시작이면 종료
            if i > sec_start + 5 and (
                line.startswith('##### □') or
                line.startswith('###### 제') or
                (line.startswith('가.') and '이사' in line and i > sec_start + 10)
            ):
                break

            # 단위 감지 (테이블 바깥: "(단위:억원)" 등)
            unit_m = re.search(r'단위\s*[：:]\s*(억원|백만원|천원|원)', line)
            if unit_m:
                comp_unit = unit_m.group(1)

            # phase 감지
            line_nospace = line.replace(' ', '')
            if re.search(r'당\s*기', line) or '(당기)' in line_nospace:
                phase = "current"
            elif re.search(r'전\s*기', line) or '(전기)' in line_nospace:
                phase = "prior"
            elif re.match(r'[\(（]?\s*\d{4}\s*년?\s*[\)）]?$', line):
                if not current:
                    phase = "current"
                elif not prior:
                    phase = "prior"
            elif re.match(r'(?:#*\s*)?제?\s*\d+\s*기', line):
                if not current:
                    phase = "current"
                elif not prior:
                    phase = "prior"

            # 테이블 감지
            if '|' in line and ('이사의 수' in line or '감사의 수' in line or '보수총액' in line or '최고한도' in line):
                rows, end_i = _parse_md_table_rows(lines, i)
                if rows:
                    parsed = _parse_comp_kv_table(rows)
                    # phase 자동 판정: "실제 지급" 키가 있으면 prior
                    if parsed.get('actualPaid') or parsed.get('actualPaidAmount'):
                        if not prior:
                            prior = parsed
                    elif not phase:
                        # phase 텍스트 없이 첫 테이블 → current
                        if not current:
                            current = parsed
                        elif not prior:
                            prior = parsed
                    elif phase == "current" and not current:
                        current = parsed
                    elif phase == "prior" and not prior:
                        prior = parsed
                    i = end_i
                    continue

            # 주석
            if line.startswith('※') or (line.startswith('*') and '이사' not in line):
                notes.append(line)

            i += 1

        # 단위 보정: limitAmount가 없고 comp_unit이 있으면 fallback_unit으로 재시도
        if comp_unit:
            for d in [current, prior]:
                if d.get('limit') and not d.get('limitAmount'):
                    d['limitAmount'] = _parse_krw(d['limit'], fallback_unit=comp_unit)
                if d.get('actualPaid') and not d.get('actualPaidAmount'):
                    d['actualPaidAmount'] = _parse_krw(d['actualPaid'], fallback_unit=comp_unit)

        if current or prior:
            item = {
                "number": "",
                "title": title or f"{'감사' if target == '감사' else '이사'} 보수한도 승인",
                "target": target,
                "current": current,
                "prior": prior,
                "notes": notes,
            }
            items.append(item)

    summary = _build_comp_summary(items)
    return {"items": items, "summary": summary}


def _parse_comp_kv_table(rows: list[list[str]]) -> dict:
    """보수한도 key-value 테이블 파싱 (2컬럼)"""
    result = {}
    for row in rows:
        if len(row) < 2:
            continue
        key = row[0].replace(' ', '').strip()
        val = row[1].strip()

        if any(kw in key for kw in ['이사의수', '이사수', '감사의수', '감사수']):
            result['headcount'] = re.sub(r'\s+', '', val)
            m = re.search(r'(\d+)\s*[\(（]\s*(\d+)\s*[\)）]', val)
            if m:
                result['totalDirectors'] = int(m.group(1))
                result['outsideDirectors'] = int(m.group(2))
            else:
                m2 = re.search(r'(\d+)', val)
                if m2:
                    result['totalDirectors'] = int(m2.group(1))

        elif '최고한도' in key or '보수총액또는' in key or '한도액' in key:
            result['limit'] = val.split('\n')[0]  # 첫 줄만 (KB금융처럼 서술형일 수 있음)
            result['limitAmount'] = _parse_krw(val)

        elif '실제지급' in key or '지급된보수' in key:
            result['actualPaid'] = val
            result['actualPaidAmount'] = _parse_krw(val)

        elif '보수총액' in key and '최고' not in key and '한도' not in key:
            result['actualPaid'] = val
            result['actualPaidAmount'] = _parse_krw(val)

    return result


def _parse_krw(text: str, fallback_unit: str = "") -> int | None:
    """금액 문자열 → 원 단위 정수

    Args:
        text: 금액 문자열 (예: "450억원", "7,000(백만원)", "100")
        fallback_unit: 텍스트에 단위 없을 때 사용할 단위 (예: "억원")
    """
    if not text:
        return None
    text = re.split(r'[+＋]', text)[0].strip()
    text = text.replace(',', '').replace(' ', '')
    # 유럽식/DART식 천단위 구분자 (1.361.550) → 마침표 제거
    if re.search(r'\d\.\d{3}\.', text):
        text = re.sub(r'\.(?=\d{3})', '', text)

    # 괄호 안 단위 처리: "7,000(백만원)" → "7000백만원"
    text = re.sub(r'\((\s*(?:백만원|천원|억원|원)\s*)\)', r'\1', text)

    m = re.search(r'([\d.]+)\s*억\s*원?', text)
    if m:
        return int(float(m.group(1)) * 100_000_000)

    m = re.search(r'([\d.]+)\s*백만\s*원?', text)
    if m:
        return int(float(m.group(1)) * 1_000_000)

    m = re.search(r'([\d.]+)\s*천\s*원?', text)
    if m:
        return int(float(m.group(1)) * 1_000)

    m = re.search(r'(\d+)\s*원?$', text)
    if m:
        return int(m.group(1))

    # 단위 없는 숫자 + fallback_unit
    if fallback_unit:
        m = re.match(r'([\d.]+)', text)
        if m:
            val = m.group(1)
            return _parse_krw(val + fallback_unit)

    return None


def _build_comp_summary(items: list[dict]) -> dict:
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


# ── 인사(선임/해임) 파서 ──

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


def parse_personnel_pdf(md_text: str) -> dict:
    """PDF 마크다운에서 이사/감사 선임 후보자 정보 추출

    패턴:
      |후보자성명|주된직업|세부경력|세부경력|해당법인과의 최근3년간 거래내역|
      |---|---|---|---|---|
      |후보자성명|주된직업|기간|내용|해당법인과의 최근3년간 거래내역|
      |이름|직업|기간1<br>기간2|내용1<br>내용2|거래내역|
    """
    lines = md_text.split('\n')
    appointments = []

    # 1. 안건 제목에서 선임/해임 안건 찾기
    agenda_sections = []
    for i, line in enumerate(lines):
        if re.search(r'제\s*\d+(?:-\d+)*\s*호\s*(?:의안)?[)）:\s]', line):
            title = re.sub(r'^[#\s\-□●◆▶]+', '', line).strip()
            title = re.sub(r'^제\s*\d+(?:-\d+)*\s*호\s*(?:의안)?[)）:\s]*', '', title).strip()
            if any(kw in title for kw in _PERSONNEL_KEYWORDS):
                agenda_sections.append((i, title))

    # 2. 후보자 경력 테이블 찾기 (기간|내용 헤더)
    career_tables = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # 경력 테이블 헤더: |후보자성명|주된직업|기간|내용|
        if '후보자성명' in line and '주된직업' in line and '|' in line:
            # 이 테이블에 '기간' 컬럼이 있는지 (경력 테이블만 파싱)
            has_period = '기간' in line
            if not has_period and i + 2 < len(lines):
                has_period = '기간' in lines[i+1] or '기간' in lines[i+2]

            if has_period:
                parse_start = i + 1
                if i + 2 < len(lines) and '기간' in lines[i+1]:
                    parse_start = i + 2
                candidates = _parse_career_table(lines, parse_start)
                if candidates:
                    career_tables.append(candidates)
                    i = parse_start + len(candidates) * 3
                    continue
        i += 1

    # 3. 후보자 dedup (같은 이름이 여러 테이블에서 나오면 경력 많은 쪽 유지)
    raw_candidates = []
    for ct in career_tables:
        raw_candidates.extend(ct)

    all_candidates = []
    seen_names = {}
    for c in raw_candidates:
        name = c.get('name', '')
        career_count = len(c.get('careerDetails', []))
        if name in seen_names:
            # 기존보다 경력 많으면 교체
            if career_count > len(seen_names[name].get('careerDetails', [])):
                all_candidates = [x for x in all_candidates if x.get('name') != name]
                all_candidates.append(c)
                seen_names[name] = c
        else:
            all_candidates.append(c)
            seen_names[name] = c

    if agenda_sections:
        # 안건별로 후보자 배분 (제목에서 이름 매칭)
        used = set()
        for idx, (line_no, title) in enumerate(agenda_sections):
            action = "선임"
            if '해임' in title:
                action = "해임"
            elif '재선임' in title:
                action = "재선임"
            elif '중임' in title:
                action = "중임"

            category = "이사"
            for keyword, cat in _CATEGORY_MAP:
                if keyword in title:
                    category = cat
                    break

            # 제목에서 이름 추출 시도
            name_in_title = None
            m = re.search(r'(?:사외이사|사내이사|이사|감사위원|감사)\s+(\S{2,5})\s', title)
            if m:
                name_in_title = m.group(1)

            matched = []
            for ci, c in enumerate(all_candidates):
                if ci in used:
                    continue
                if name_in_title and c['name'] == name_in_title:
                    matched.append(c)
                    used.add(ci)
                    break

            # 이름 매칭 안 되면 아직 미배분 후보자 중 순서대로
            if not matched:
                for ci, c in enumerate(all_candidates):
                    if ci not in used:
                        matched.append(c)
                        used.add(ci)
                        # 안건당 후보자 수 불명확 — 다음 안건까지만
                        break

            appointments.append({
                "number": "",
                "title": title,
                "action": action,
                "category": category,
                "candidates": matched,
            })
    elif all_candidates:
        # 안건 구분 없이 후보자만 있는 경우
        appointments.append({
            "number": "",
            "title": "이사/감사 선임",
            "action": "선임",
            "category": "이사",
            "candidates": all_candidates,
        })

    summary = {
        "total_appointments": len(appointments),
        "total_candidates": len(all_candidates),
    }

    return {"appointments": appointments, "summary": summary}


def _parse_career_table(lines: list[str], start: int) -> list[dict]:
    """경력 테이블 데이터 행에서 후보자 정보 추출

    테이블 구조:
      |이름|주된직업|기간1<br>기간2|내용1<br>내용2|거래내역|
    기간/내용은 <br> 또는 줄바꿈으로 분리
    """
    candidates = []
    i = start

    while i < len(lines):
        line = lines[i].strip()

        # 테이블 끝
        if not line or (line.startswith('#') or line.startswith('- ') and '후보자' not in line):
            break

        # 구분선 스킵
        if '---' in line and '|' in line:
            i += 1
            # 구분선 다음에 이어지는 행 (동일 후보자의 추가 경력)
            continue

        if not line.startswith('|'):
            # 이전 후보자의 경력이 줄바꿈으로 이어지는 경우
            if candidates and line and not line.startswith('('):
                # 기간 패턴인지 확인
                if re.match(r"(?:'\d{2}|\d{4})", line):
                    # 이전 후보자의 기간에 추가
                    last = candidates[-1]
                    if last.get('_raw_periods'):
                        last['_raw_periods'] += '\n' + line
                elif candidates[-1].get('_raw_contents'):
                    candidates[-1]['_raw_contents'] += '\n' + line
            i += 1
            continue

        # |로 시작하지만 끝나지 않는 행 → 멀티라인 합치기
        full_line = line
        while not full_line.rstrip().endswith('|') and i + 1 < len(lines):
            i += 1
            next_l = lines[i].strip()
            if next_l.startswith('|') and '---' in next_l:
                break
            if not next_l:
                break
            full_line += '\n' + next_l

        if not full_line.rstrip().endswith('|'):
            i += 1
            continue

        cells = [c.strip() for c in full_line[1:-1].split('|')]
        if len(cells) < 4:
            i += 1
            continue

        # 헤더 행 스킵
        if cells[0] in ['후보자성명', '총'] or '총 (' in cells[0]:
            i += 1
            continue

        name = _clean_br(cells[0]).strip()
        if not name or len(name) < 2:
            # 이전 후보자의 추가 경력 행 (이름 셀이 비어있음)
            if candidates:
                last = candidates[-1]
                periods_raw = _clean_br(cells[2]) if len(cells) > 2 else ''
                contents_raw = _clean_br(cells[3]) if len(cells) > 3 else ''
                if periods_raw:
                    last['_raw_periods'] = (last.get('_raw_periods', '') + '\n' + periods_raw).strip()
                if contents_raw:
                    last['_raw_contents'] = (last.get('_raw_contents', '') + '\n' + contents_raw).strip()
            i += 1
            continue

        main_job = _clean_br(cells[1]) if len(cells) > 1 else ''
        periods_raw = _clean_br(cells[2]) if len(cells) > 2 else ''
        contents_raw = _clean_br(cells[3]) if len(cells) > 3 else ''
        transactions = _clean_br(cells[4]) if len(cells) > 4 else ''

        candidates.append({
            'name': name,
            'mainJob': main_job.replace('\n', ' '),
            '_raw_periods': periods_raw,
            '_raw_contents': contents_raw,
            'recent3yTransactions': transactions if transactions and transactions != '해당사항 없음' else None,
        })

        i += 1

    # 기간/내용 분리 → careerDetails 구축
    for c in candidates:
        c['careerDetails'] = _split_career_details(
            c.pop('_raw_periods', ''),
            c.pop('_raw_contents', '')
        )

    return candidates


def _split_career_details(periods_raw: str, contents_raw: str) -> list[dict]:
    """기간 문자열과 내용 문자열을 매칭하여 careerDetails 리스트 생성"""
    if not periods_raw and not contents_raw:
        return []

    # 줄바꿈으로 분리
    periods = [p.strip() for p in periods_raw.split('\n') if p.strip()]
    contents = [c.strip() for c in contents_raw.split('\n') if c.strip()]

    # 학력 헤더 제거
    periods = [p for p in periods if p not in ['<학력사항>', '<경력사항>']]
    contents = [c for c in contents if c not in ['<학력사항>', '<경력사항>']]

    details = []
    for idx in range(max(len(periods), len(contents))):
        period = periods[idx] if idx < len(periods) else ''
        content = contents[idx] if idx < len(contents) else ''
        if period or content:
            details.append({
                'period': period,
                'content': content,
            })

    return details


def _empty_compensation_summary() -> dict:
    return {
        "totalItems": 0,
        "currentTotalLimit": None,
        "priorTotalPaid": None,
        "priorTotalLimit": None,
        "priorUtilization": None,
    }


# ── 재무제표 파서 ──

def parse_financials_pdf(md_text: str) -> dict:
    """PDF 마크다운에서 재무상태표/손익계산서 추출

    패턴:
      (단위 : 백만원)
      |과 목|제 N (당) 기|제 N (당) 기|제 N-1 (전) 기|제 N-1 (전) 기|
      |---|---|---|---|---|
      |자 산| | | | |
      |Ⅰ. 유동자산| |247,684,612| |227,062,266|
      ...

    개선 필요:
      - [ ] 연결/별도 구분 (현재 첫 번째 테이블만 잡음)
      - [ ] 4컬럼(소계 포함) vs 2컬럼(계정+금액) 구조 대응
      - [ ] 손익계산서 별도 감지 (현재 BS 이후 연속 테이블로 잡을 수 있음)
      - [ ] periodLabels 추출 (제N기 → 당기/전기 라벨)
    """
    lines = md_text.split('\n')
    result = {
        "consolidated": {"balance_sheet": None, "income_statement": None},
        "separate": {"balance_sheet": None, "income_statement": None},
    }

    # 단위 + 재무 테이블 영역 찾기
    unit = None
    bs_start = None
    is_start = None

    for i, line in enumerate(lines):
        # 단위 감지
        m = re.search(r'단위\s*[：:]\s*(백만원|천원|억원|원)', line)
        if m:
            unit_candidate = m.group(1)
            # 다음 15줄 내에 BS 테이블 헤더/첫 행이 있는지
            # 거래내역 테이블이 아닌 재무제표 테이블만 잡기
            for j in range(i, min(len(lines), i+15)):
                jline = lines[j].strip()
                if '|' not in jline:
                    continue
                jline_ns = jline.replace(' ', '')
                # 거래내역 테이블 제외
                if re.search(r'거래종류|거래상대|거래금액|거래기간', jline_ns):
                    break
                # BS 헤더/첫 행: 자산, 과목, 유동자산
                if re.search(r'자산|자 산|과\s*목|유동자산', jline):
                    unit = unit_candidate
                    bs_start = j
                    break
            if bs_start:
                break

    if not bs_start or not unit:
        return result

    # BS 테이블 파싱
    bs_rows, bs_end = _parse_financial_table(lines, bs_start)
    if bs_rows:
        result["consolidated"]["balance_sheet"] = {
            "unit": unit,
            "columns": ["account", "current", "prior"],
            "rows": bs_rows,
        }

    # IS 찾기: 2가지 전략
    # 전략 1: "손익계산서" 헤딩을 명시적으로 찾기
    for i in range(bs_end, len(lines)):
        line = lines[i]
        line_nospace = line.replace(' ', '')
        if '손익계산서' in line_nospace and '포괄' not in line_nospace:
            for j in range(max(0, i-3), min(len(lines), i+8)):
                m2 = re.search(r'단위\s*[：:]\s*(백만원|천원|억원|원)', lines[j])
                if m2:
                    unit = m2.group(1)
                    break
            for j in range(i, min(len(lines), i+15)):
                if '|' in lines[j] and re.search(r'과|매출|매 출|영업|영 업|Ⅰ|순이자', lines[j]):
                    is_start = j
                    break
            if is_start:
                break

    # 전략 2: 헤딩 없이 BS 다음에 바로 IS 테이블이 오는 경우
    # BS 끝 이후 가까운 테이블에서 매출/영업/이자수익 계정이 있으면 IS로 판정
    if not is_start:
        for i in range(bs_end, min(len(lines), bs_end + 50)):
            line = lines[i].strip()
            if '|' in line and line.startswith('|'):
                # 테이블 행의 첫 셀에 IS 핵심 계정이 있는지
                cells = [c.strip().replace(' ', '') for c in line.split('|') if c.strip()]
                if cells:
                    acct = cells[0]
                    if re.search(r'매출액|매출|영업수익|순이자손익|이자수익|Ⅰ\.매출|Ⅰ\.순이자', acct):
                        # 단위: BS와 동일 사용
                        is_start = i
                        # 근처에 단위 있으면 업데이트
                        for j in range(max(0, i-5), i):
                            m3 = re.search(r'단위\s*[：:]\s*(백만원|천원|억원|원)', lines[j])
                            if m3:
                                unit = m3.group(1)
                        break

    if is_start:
        is_rows, _ = _parse_financial_table(lines, is_start)
        if is_rows:
            result["consolidated"]["income_statement"] = {
                "unit": unit,
                "columns": ["account", "current", "prior"],
                "rows": is_rows,
            }

    return result


def _parse_financial_table(lines: list[str], start: int) -> tuple[list[list[str]], int]:
    """재무제표 테이블을 [계정명, 당기, 전기] 행으로 정규화

    컬럼 구조 다양:
      - 4컬럼: |과목|당기소계|당기합계|전기소계|전기합계| → 합계 컬럼 사용
      - 2컬럼: |과목|당기|전기|
      - 주석컬럼 포함: |과목|주석|당기|전기|
    """
    rows = []
    i = start
    col_count = 0

    while i < len(lines):
        line = lines[i].strip()

        if not line:
            if rows:
                # 빈 줄 뒤에 |로 시작하는 행이 있으면 테이블 계속 (멀티 테이블 구조)
                lookahead = min(len(lines), i + 3)
                has_more = any(lines[j].strip().startswith('|') for j in range(i+1, lookahead))
                if not has_more:
                    i += 1
                    break
            i += 1
            continue

        if '---' in line and '|' in line:
            i += 1
            continue

        if line.startswith('#'):
            break

        if not line.startswith('|'):
            if rows:
                break
            i += 1
            continue

        cells = [c.strip() for c in line[1:-1].split('|') if line.endswith('|')]
        if not cells:
            i += 1
            continue

        # 헤더 행 감지 (과 목, 제 N 기 등)
        if any(kw in cells[0] for kw in ['과', '항목', '구분']) and any('기' in c for c in cells[1:]):
            col_count = len(cells)
            i += 1
            continue

        # 데이터 행
        account = re.sub(r'<br\s*/?>', ' ', cells[0]).strip()
        if not account:
            i += 1
            continue

        # 숫자 셀 추출
        nums = []
        for c in cells[1:]:
            c_clean = re.sub(r'<br\s*/?>', '', c).strip()
            c_clean = c_clean.replace('=', '').strip()
            nums.append(c_clean)

        # 당기/전기 추출 (합계 컬럼 우선)
        current = ''
        prior = ''
        if len(nums) >= 4:
            # 4컬럼: [소계, 합계, 소계, 합계] — 합계(인덱스 1, 3) 사용
            current = nums[1] if nums[1] else nums[0]
            prior = nums[3] if nums[3] else nums[2]
        elif len(nums) >= 2:
            # 주석 컬럼 감지
            if nums[0] and not re.search(r'[\d,]', nums[0]) and len(nums) >= 3:
                # 첫 번째가 주석번호
                current = nums[1]
                prior = nums[2] if len(nums) > 2 else ''
            else:
                current = nums[0]
                prior = nums[1] if len(nums) > 1 else ''

        rows.append([account, current, prior])
        i += 1

    return rows, i


# ── 정관변경 파서 ──

def parse_aoi_pdf(md_text: str) -> dict:
    """PDF 마크다운에서 정관변경 비교 테이블 추출

    패턴:
      |변경전 내용|변경후 내용|변경의 목적|
      |---|---|---|
      |제21조 (총회의 소집) ...|제21조 (총회의 소집) ...|전자주주총회 도입|

    또는:
      |현행|변경(안)|비고|

    개선 필요:
      - [ ] 세부의안 번호 매핑 (제3-1호, 제3-2호)
      - [ ] 여러 테이블에 걸친 변경사항 병합
      - [ ] 셀 내 줄바꿈(<br>)으로 인한 긴 조문 처리
      - [ ] "(생 략)" / "(현행과 같음)" 패턴 처리
    """
    lines = md_text.split('\n')
    amendments = []

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # 변경전/변경후 테이블 헤더 감지
        is_header = False
        before_col = -1
        after_col = -1
        reason_col = -1

        if '|' in line:
            cells = [c.strip().replace(' ', '') for c in line.split('|') if c.strip()]
            for ci, cell in enumerate(cells):
                if '변경전' in cell or '현행' == cell:
                    before_col = ci
                    is_header = True
                if '변경후' in cell or '변경(안)' in cell.replace(' ', ''):
                    after_col = ci
                    is_header = True
                if '목적' in cell or '비고' in cell or '사유' in cell:
                    reason_col = ci

        if not is_header or before_col < 0 or after_col < 0:
            i += 1
            continue

        # 구분선 스킵
        i += 1
        if i < len(lines) and '---' in lines[i]:
            i += 1

        # 데이터 행 파싱 (멀티라인 셀 대응)
        while i < len(lines):
            row_line = lines[i].strip()

            # 빈 줄은 스킵 (테이블 내 빈 줄 허용)
            if not row_line:
                i += 1
                # 빈 줄 2개 연속이면 테이블 끝
                if i < len(lines) and not lines[i].strip():
                    # 다음이 또 헤더일 수 있으니 break하지 않고 continue
                    i += 1
                continue

            if not row_line.startswith('|'):
                # 멀티라인: 이전 행의 연속일 수 있음 — 그냥 스킵
                i += 1
                continue

            if '---' in row_line:
                i += 1
                continue

            # |로 시작하는 행 — |로 끝날 때까지 줄 합치기
            full_line = row_line
            while not full_line.rstrip().endswith('|') and i + 1 < len(lines):
                i += 1
                next_line = lines[i].strip()
                if next_line.startswith('|') and '---' in next_line:
                    break  # 다음 구분선
                full_line += ' ' + next_line

            cells = [c.strip() for c in full_line[1:-1].split('|')] if full_line.rstrip().endswith('|') else []
            if not cells or len(cells) <= max(before_col, after_col):
                i += 1
                continue

            before_text = _clean_br(cells[before_col]) if before_col < len(cells) else ''
            after_text = _clean_br(cells[after_col]) if after_col < len(cells) else ''
            reason = _clean_br(cells[reason_col]) if reason_col >= 0 and reason_col < len(cells) else ''

            # 빈 행이나 "해당없음" 스킵
            if before_text in ['-', ''] and after_text in ['-', ''] :
                i += 1
                continue
            if '해당없음' in before_text.replace(' ', '') and '해당없음' in after_text.replace(' ', ''):
                i += 1
                continue

            # 조항 추출
            clause = ''
            for text in [before_text, after_text]:
                m = re.search(r'제\s*\d+\s*조\s*(?:\([^)]*\))?', text)
                if m:
                    clause = m.group(0).strip()
                    break

            amendments.append({
                "subAgendaId": "",
                "label": reason or clause,
                "clause": clause,
                "before": before_text,
                "after": after_text,
                "reason": reason,
            })
            i += 1

        i += 1

    summary = {"totalAmendments": len(amendments)}
    return {"amendments": amendments, "summary": summary}


# ── 자기주식 파서 ──

def parse_treasury_share_pdf(md_text: str) -> dict:
    """PDF 마크다운에서 자기주식/자사주/자본감소 테이블 추출

    패턴:
      - "자기주식" / "자사주" / "자본감소" 키워드 포함 섹션
      - 주식수, 취득/처분/소각 내역 테이블
    """
    lines = md_text.split('\n')
    items = []

    _TREASURY_KW = ['자기주식', '자사주', '자본감소']

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # 자기주식 관련 헤딩/키워드 섹션 찾기
        if not any(kw in line for kw in _TREASURY_KW):
            i += 1
            continue

        # 헤딩이면 제목 추출
        title = line.lstrip('#').strip() if line.startswith('#') else line.strip()

        # 유형 분류
        item_type = "cancel" if any(kw in title for kw in ['소각', '감소']) else "hold_dispose"

        tables = []
        shares_info = []
        notes = []
        purpose = ""

        # 이 섹션에서 테이블과 텍스트 수집 (최대 80줄)
        j = i + 1
        search_end = min(len(lines), j + 80)
        while j < search_end:
            jline = lines[j].strip()

            # 다음 주요 섹션 시작이면 종료
            if jline.startswith('#') and not any(kw in jline for kw in _TREASURY_KW):
                break

            # 테이블 감지
            if jline.startswith('|') and '|' in jline[1:]:
                rows, end_j = _parse_md_table_rows(lines, j)
                if rows and len(rows) >= 2:
                    tables.append({
                        "headers": rows[0],
                        "rows": rows[1:],
                    })
                    # 주식수 추출
                    for row in rows[1:]:
                        for cell in row:
                            m = re.search(r'([\d,]+)\s*주', cell)
                            if m:
                                shares_info.append(cell.strip())
                j = end_j
                continue

            # 목적 추출
            if '목적' in jline and not purpose:
                purpose = jline[:200]

            # 주석
            if jline.startswith('※') or (jline.startswith('*') and len(jline) > 2):
                notes.append(jline)

            j += 1

        if tables or shares_info:
            items.append({
                "number": "",
                "title": title[:100],
                "type": item_type,
                "purpose": purpose,
                "sharesInfo": shares_info[:5],
                "schedule": [],
                "tables": tables,
                "notes": notes,
            })

        i = max(i + 1, j)

    return {"items": items, "summary": {"totalItems": len(items)}}


# ── 자본준비금 파서 ──

def parse_capital_reserve_pdf(md_text: str) -> dict:
    """PDF 마크다운에서 자본준비금 감소/이익잉여금 전입 정보 추출

    패턴:
      - "자본준비금" 키워드 포함 섹션
      - 금액 (N조원, N억원 등) 추출
    """
    lines = md_text.split('\n')
    items = []

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if '자본준비금' not in line:
            i += 1
            continue

        # 재무제표 섹션이면 스킵
        if any(kw in line for kw in ['재무제표', '재무상태표', '대차대조표']):
            i += 1
            continue

        title = line.lstrip('#').strip() if line.startswith('#') else line.strip()

        amount = None
        purpose = ""
        notes = []

        # 섹션 내에서 금액/목적 수집
        j = i + 1
        search_end = min(len(lines), j + 60)
        while j < search_end:
            jline = lines[j].strip()

            if jline.startswith('#') and '자본준비금' not in jline:
                break

            # 금액 추출
            if not amount:
                m = re.search(r'([\d,.]+)\s*(조|억|백만|천)?\s*원', jline)
                if m:
                    amount = m.group(0).strip()

            # 목적 추출
            if not purpose and ('목적' in jline or '이입' in jline or '전입' in jline):
                purpose = jline[:300]

            # 주석
            if jline.startswith('※') or (jline.startswith('*') and len(jline) > 2):
                notes.append(jline)

            j += 1

        if amount or purpose:
            items.append({
                "number": "",
                "title": title[:100],
                "amount": amount,
                "purpose": purpose,
                "notes": notes,
            })

        i = max(i + 1, j)

    return {"items": items, "summary": {"totalItems": len(items)}}


# ── 퇴직금 규정 파서 ──

def parse_retirement_pay_pdf(md_text: str) -> dict:
    """PDF 마크다운에서 퇴직금 규정 현행/개정안 비교 테이블 추출

    정관변경(aoi)과 같은 패턴: 현행/변경전 vs 개정안/변경후 비교 테이블
    """
    lines = md_text.split('\n')
    amendments = []

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # 변경전/변경후 테이블 헤더 감지 (aoi 패턴 재사용)
        is_header = False
        before_col = -1
        after_col = -1
        reason_col = -1

        if '|' in line:
            cells = [c.strip().replace(' ', '') for c in line.split('|') if c.strip()]
            for ci, cell in enumerate(cells):
                if '변경전' in cell or '현행' == cell:
                    before_col = ci
                    is_header = True
                if '변경후' in cell or '변경(안)' in cell.replace(' ', '') or '개정안' in cell or '개정' == cell:
                    after_col = ci
                    is_header = True
                if '목적' in cell or '비고' in cell or '사유' in cell:
                    reason_col = ci

        # 퇴직금 컨텍스트 확인: 앞 20줄에 퇴직금 키워드가 있는지
        if is_header and before_col >= 0 and after_col >= 0:
            context_start = max(0, i - 20)
            context_text = ' '.join(lines[context_start:i])
            if '퇴직금' not in context_text and '퇴직위로금' not in context_text:
                # 컨텍스트에 퇴직금 없으면 스킵 (정관변경 등 다른 테이블일 수 있음)
                i += 1
                continue
        else:
            i += 1
            continue

        # 구분선 스킵
        i += 1
        if i < len(lines) and '---' in lines[i]:
            i += 1

        # 데이터 행 파싱
        while i < len(lines):
            row_line = lines[i].strip()

            if not row_line:
                i += 1
                if i < len(lines) and not lines[i].strip():
                    i += 1
                continue

            if not row_line.startswith('|'):
                i += 1
                continue

            if '---' in row_line:
                i += 1
                continue

            # 멀티라인 합치기
            full_line = row_line
            while not full_line.rstrip().endswith('|') and i + 1 < len(lines):
                i += 1
                next_line = lines[i].strip()
                if next_line.startswith('|') and '---' in next_line:
                    break
                full_line += ' ' + next_line

            cells = [c.strip() for c in full_line[1:-1].split('|')] if full_line.rstrip().endswith('|') else []
            if not cells or len(cells) <= max(before_col, after_col):
                i += 1
                continue

            before_text = _clean_br(cells[before_col]) if before_col < len(cells) else ''
            after_text = _clean_br(cells[after_col]) if after_col < len(cells) else ''
            reason = _clean_br(cells[reason_col]) if reason_col >= 0 and reason_col < len(cells) else ''

            if before_text in ['-', ''] and after_text in ['-', '']:
                i += 1
                continue

            # 조항 추출
            clause = ''
            for text in [before_text, after_text]:
                m = re.search(r'제\s*\d+\s*조\s*(?:\([^)]*\))?', text)
                if m:
                    clause = m.group(0).strip()
                    break

            amendments.append({
                "clause": clause,
                "before": before_text,
                "after": after_text,
                "reason": reason,
            })
            i += 1

        i += 1

    summary = {"totalAmendments": len(amendments)}
    return {"amendments": amendments, "summary": summary}


# ── 안건 목록 파서 ──

def parse_agenda_pdf(md_text: str) -> list[dict]:
    """PDF 마크다운에서 안건 트리 추출

    패턴 (소집공고 본문의 회의목적사항):
      □ 제1호: 정관 일부 변경의 건
        - 제1-1호: 집중투표제 배제 조항 삭제
        - 제1-2호: 개정 상법 반영
      □ 제2호: 재무제표 승인의 건
      ○ 제3호 의안: 사내이사 김용관 선임의 건

    개선 필요:
      - [ ] 보고사항 vs 결의사항 구분
      - [ ] 조건부 안건 감지 (제2-7호 인가되는 경우)
      - [ ] 정정공고에서 정정 전/후 안건 변경 처리
      - [ ] 목차의 안건 vs 본문의 안건 중복 제거
    """
    lines = md_text.split('\n')
    items = []
    seen_numbers = set()

    # 회의목적사항 영역 정확히 찾기
    # "N. 회의목적사항" 또는 "회의 목적사항" 패턴 (N은 보통 3 또는 4)
    # 정정공고 테이블/목차 안의 "목적사항" 텍스트는 제외해야 함
    agenda_start = -1
    for i, line in enumerate(lines):
        line_strip = line.strip()
        # "4. 회의목적사항", "3. 회의 목적사항", "4. 회의의 목적사항"
        if re.match(r'^-?\s*[34]\.\s*회의의?\s*목적\s*사항', line_strip):
            agenda_start = i
            break

    if agenda_start < 0:
        # fallback: "주주총회 소집공고" 헤딩 이후에서 찾기
        notice_start = 0
        for i, line in enumerate(lines):
            line_nospace = line.replace(' ', '')
            if re.match(r'^#+\s*주주총회\s*소집\s*공고', line_nospace):
                notice_start = i
        for i in range(notice_start, min(len(lines), notice_start + 300)):
            if re.search(r'회의.*목적|목적사항|결의사항|부의안건', lines[i]):
                agenda_start = i
                break
        if agenda_start < 0:
            agenda_start = notice_start

    # 안건 번호 패턴
    agenda_pattern = re.compile(
        r'(?:□|○|●|◆|▶|■|\-\s*[○●]?\s*)?'  # 앞 장식
        r'(?:\d+\)\s*)?'                  # 번호 (1), 2)
        r'(?:\(\d+\)\s*)?'               # 괄호 번호 (1), (2)
        r'(?:[①②③④⑤⑥⑦⑧⑨⑩]\s*)?'     # 원문자 번호
        r'(?:[가나다라마바사아자차카타파하]\.\s*)?'  # 한글 번호 가. 나.
        r'(?:·\s*)?'                      # · prefix (세부의안)
        r'(?:\(?\s*)?'                    # 여는 괄호 (제1호 의안)
        r'제\s*(\d+(?:-\d+)*)\s*호'      # 안건 번호
        r'\s*(?:의안)?[)）:\s]*'          # 구분자 + 닫는 괄호
        r'(.+)'                           # 제목
    )

    # 검색 범위: 목적사항부터 100줄 (안건 목록은 보통 한 페이지)
    for i in range(agenda_start, min(len(lines), agenda_start + 100)):
        raw_line = lines[i].strip()
        # 테이블 행 처리
        if raw_line.startswith('|') and raw_line.count('|') >= 2:
            # 안건 테이블인 경우만 허용 (|제N호 의안:|제목| 형태)
            cells = [c.strip() for c in raw_line.split('|') if c.strip()]
            if cells and re.match(r'제\s*\d+(?:-\d+)*\s*호', cells[0]):
                # 테이블 셀에서 안건 추출
                number_m = re.match(r'제\s*(\d+(?:-\d+)*)\s*호', cells[0])
                if number_m and len(cells) >= 2:
                    title = cells[1].strip()
                    title = re.sub(r'<br\s*/?>', ' ', title).strip()
                    if title and len(title) >= 2 and len(title) <= 200:
                        full_number = f"제{number_m.group(1)}호"
                        if full_number not in seen_numbers:
                            seen_numbers.add(full_number)
                            parts = number_m.group(1).split('-')
                            item = {"number": full_number, "title": title, "children": []}
                            if len(parts) > 1:
                                parent_num = f"제{parts[0]}호"
                                parent = next((it for it in items if it['number'] == parent_num), None)
                                if parent:
                                    parent['children'].append(item)
                                else:
                                    items.append(item)
                            else:
                                items.append(item)
            continue
        line = re.sub(r'^[-\s*□○◆▶]+', '', raw_line)

        m = agenda_pattern.match(line)
        if not m:
            continue

        number_str = m.group(1)
        title = m.group(2).strip()
        # 제목 끝의 잡음 제거
        title = re.sub(r'\s*\.{3,}.*$', '', title)  # ... 이후 제거
        title = re.sub(r'\s*\|.*$', '', title)       # | 이후 제거
        title = title.strip()

        if not title or len(title) < 2 or len(title) > 200:
            continue

        full_number = f"제{number_str}호"
        if full_number in seen_numbers:
            continue
        seen_numbers.add(full_number)

        # depth 판정
        parts = number_str.split('-')
        is_sub = len(parts) > 1

        item = {
            "number": full_number,
            "title": title,
            "children": [],
        }

        if is_sub:
            # 부모 찾아서 children에 추가
            parent_num = f"제{parts[0]}호"
            parent = next((it for it in items if it['number'] == parent_num), None)
            if parent:
                parent['children'].append(item)
            else:
                items.append(item)
        else:
            items.append(item)

    return items
