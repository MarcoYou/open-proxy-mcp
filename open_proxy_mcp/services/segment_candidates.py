"""segment_profit 후보표 좁히기 — 정형 파서가 실패/저신뢰일 때 tool이 반환하는 raw fallback.

설계(260718 사용자 결정): 내부 LLM 호출 폐기. MCP tool은 이미 LLM(호출측 Claude)이 부르므로,
tool은 **기계적으로 수백 개 중첩표를 '진짜 부문표 후보' ~3-5개로 좁혀 raw로 반환**하고,
값 추출·표 선택은 호출측 Claude가 한다(156사 에이전트 추출로 이 방식 검증됨). anthropic/pandas/API키 불필요.
"""
from __future__ import annotations

import re
import warnings

try:
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
except Exception:
    pass

_METRIC = ("매출액", "영업수익", "영업이익", "영업손익", "총부문수익", "부문수익", "매출총이익",
           "당기순이익", "수익(매출액)", "부문이익")
_SEG_SIGNAL = ("영업부문", "사업부문", "부문별", "보고부문")
# 부문표가 아닌데 지표라벨을 가진 오답 표 힌트(관계·종속·지분율·유형자산 증감·내용연수)
_ANTI = ("관계기업", "종속기업", "지분율", "내용연수", "취득원가", "기초", "감가상각누계")

# get_document full html(7~22MB)을 통째로 bs4 파싱하면 느림 → 정규식으로 <table> 블록만 뽑아
# 문자열 프리필터(지표+부문신호+큰수) 통과 표만 bs4로 파싱. 전문서 스캔이라 구간슬라이스 오류 없음.
_TABLE_RE = re.compile(r"<table\b.*?</table>", re.DOTALL | re.IGNORECASE)
_BIGNUM_RE = re.compile(r"[\d,]{5,}")
# _SEG_SIGNAL(영업부문/사업부문…) 없이 '설비부문·액츄에이터부문'처럼 부문명만 담은 표(액트로류) 회수:
# 표 안에 일반어(영업/보고/사업/기타부문) 아닌 고유 부문명이 2개+면 부문표로 본다.
_SEGNAME_RE = re.compile(r"[가-힣A-Za-z]{2,10}부문")
_GENERIC_SEG = {"영업부문", "보고부문", "사업부문", "공통부문", "기타부문", "부문별", "해당부문",
                "각부문", "동부문", "본부문", "전부문", "단일부문", "각각부문", "전체부문"}


def _specific_seg_names(t: str) -> set:
    return {n for n in _SEGNAME_RE.findall(t) if n not in _GENERIC_SEG}


# 임원·주주 명부 표(성명+출생년월/직위/임기 등): '담당업무'에 'OO부문 총괄'이 들어가 부문표로 오인됨.
# 지표라벨 없이 부문명만으로 통과시키는 완화 경로에서 이런 명부를 배제한다(구조적 anti-pattern).
_ROSTER_RE = re.compile(r"출생년월|임기\s*만료|등기임원|주요\s*경력|최대주주와의|의결권\s*(있는|없는)\s*주식")


def _is_roster(t: str) -> bool:
    return ("성명" in t) and bool(_ROSTER_RE.search(t))


def _table_to_grid(tb) -> list[list[str]]:
    """bs4 <table> → colspan/rowspan 확장한 2D 텍스트 격자 (pandas 없이, venv-lean)."""
    grid: list[list[str]] = []
    rowspans: dict[int, tuple[int, str]] = {}
    for tr in tb.find_all("tr"):
        row: list[str] = []
        ci = 0
        cells = tr.find_all(["td", "th"])
        cidx = 0
        while cidx < len(cells) or (rowspans and ci in rowspans):
            if ci in rowspans:
                rem, txt = rowspans[ci]
                row.append(txt)
                rowspans[ci] = (rem - 1, txt)
                if rem - 1 <= 0:
                    del rowspans[ci]
                ci += 1
                continue
            if cidx >= len(cells):
                break
            td = cells[cidx]; cidx += 1
            txt = td.get_text(" ", strip=True)
            cs = int(td.get("colspan", 1) or 1)
            rs = int(td.get("rowspan", 1) or 1)
            for _ in range(cs):
                row.append(txt)
                if rs > 1:
                    rowspans[ci] = (rs - 1, txt)
                ci += 1
        if any(c.strip() for c in row):
            grid.append(row)
    return grid


def _score(txt: str, ctx: str = "") -> int:
    s = 0
    s += sum(1 for m in _METRIC if m in txt)
    s += 2 * sum(1 for k in _SEG_SIGNAL if k in (txt + " " + ctx))   # 표 안 OR 표 앞 헤딩
    s += 2 * min(len(_specific_seg_names(txt)), 3)                    # 고유 부문명(설비부문 등) 가점
    s -= 3 * sum(1 for a in _ANTI if a in txt)
    s += min(len(re.findall(r"[\d,]{5,}", txt)) // 3, 4)   # 큰 숫자 다수 가점(상한)
    return s


def _table_to_markdown(tb) -> str:
    """bs4 <table> → GitHub 마크다운 표(colspan/rowspan 확장). 값 판단은 호출측 LLM이."""
    grid = _table_to_grid(tb)
    if not grid:
        return ""
    ncol = max(len(r) for r in grid)
    grid = [r + [""] * (ncol - len(r)) for r in grid]
    def esc(c):
        return c.replace("|", "\\|").replace("\n", " ").strip()
    out = ["| " + " | ".join(esc(c) for c in grid[0]) + " |",
           "|" + "|".join(["---"] * ncol) + "|"]
    for r in grid[1:]:
        out.append("| " + " | ".join(esc(c) for c in r) + " |")
    return "\n".join(out)


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s)


def _render_html_region_md(html: str, start: int, end: int) -> str | None:
    """html[start:end] 구간을 마크다운으로: <table>→md표, <p>/<span>→문단(문서 순서·중복제거)."""
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return None
    soup = BeautifulSoup(html[start:end], "lxml")
    parts = []
    for el in soup.find_all(["p", "span", "table"]):
        if el.name == "table":
            if el.find_parent("table"):
                continue
            md = _table_to_markdown(el)
            grid_txt = re.sub(r"[\s|:-]", "", md)
            if md and grid_txt:                       # 빈 표('(단위)'만) 스킵
                parts.append("\n" + md + "\n")
        else:
            if el.find("table") or el.find_parent("table"):
                continue
            txt = el.get_text(" ", strip=True)
            if txt and len(txt) > 1:
                parts.append(txt)
    out, prev = [], None
    for p in parts:
        if p != prev and not (prev and p in prev):    # 부모/자식 span 텍스트 중복 제거
            out.append(p)
        prev = p
    md = "\n".join(out).strip()
    return md or None


# 진짜 영업부문 주석 헤딩: 'N. 영업부문' / 'N. 부문정보' / 'N. 부문별 정보' (번호 붙은 소절제목).
# biz 본문의 '…사업부문을 분할' 같은 프로즈 언급과 구분하려 번호 헤딩만 앵커로 쓴다.
_NOTE_KW = ("영업부문", "부문정보", "부문 정보", "부문별 정보", "부문별정보", "부문별 보고", "부문에 대한")


def _md_has_data_rows(md: str, need: int = 2) -> bool:
    """마크다운에 실제 숫자 든 표 행이 need개 이상 있나(빈 헤더표만인 저품질 렌더 배제)."""
    return sum(1 for ln in md.splitlines() if ln.startswith("|") and _BIGNUM_RE.search(ln)) >= need


# 단일 영업부문 '선언' — 이게 있으면 다부문 데이터표 없이 지역/제품표만 있는 단일사라 마크다운 스킵.
# (NAVER 현대건설형처럼 선언 있어도 매출부문표 실재하면 후보경로에서 다시 잡힘)
_SINGLE_DECL_RE = re.compile(r"단일의?\s*(영업|보고)?\s*부문|단일\s*부문으로|하나의\s*(영업|보고)?\s*부문|"
                             r"단일\s*영업부문|영업부문은\s*단일")


def render_segment_note_markdown(html: str, max_chars: int = 55000) -> str | None:
    """영업부문 주석 구간을 통째로 마크다운으로 렌더(설명 문단 + 표 전부). 값 추출은 호출측 LLM.

    설계(260718 사용자): 하드케이스는 '어느 표인지' 점수매기지 말고 K-IFRS 영업부문 주석 원문을
    그대로 마크다운으로 넘겨 호출측 AI가 읽게 한다. 'N. 영업부문' 번호 헤딩을 앵커로 구간을 잡는다.
    설명 boilerplate가 길어 데이터표가 멀리 있을 수 있으므로 넉넉히 렌더하고, 숫자행 없으면 None.
    """
    if not html:
        return None
    for kw in _NOTE_KW:
        for m in re.finditer(kw, html):
            pre = _strip_tags(html[max(0, m.start() - 30):m.start()])
            if not re.search(r"\d{1,2}\.\s*$", pre):        # 번호 붙은 소절 제목만
                continue
            seg = html[m.start():m.start() + 45000]         # 뒤 45KB에 진짜 부문표 있나(boilerplate 감안)
            has_tbl = any(_BIGNUM_RE.search(x.group(0)) and not _is_roster(x.group(0))
                          and (any(k in x.group(0) for k in _METRIC) or len(_specific_seg_names(x.group(0))) >= 2)
                          for x in _TABLE_RE.finditer(seg))
            if not has_tbl:
                continue
            start = max(0, m.start() - 40)
            region_txt = _strip_tags(html[start:start + 6000])
            # 단일 선언 + 고유 부문명<2 → 단일사(지역/제품표뿐) → 마크다운 스킵(후보/NA로)
            if _SINGLE_DECL_RE.search(region_txt) and len(_specific_seg_names(html[start:start + max_chars])) < 2:
                continue
            md = _render_html_region_md(html, start, start + max_chars)
            if md and _md_has_data_rows(md):            # 저품질(빈 헤더표만) 렌더는 폴백에 양보
                return md
    return None


def render_biz_section_markdown(html: str, max_chars: int = 40000) -> str | None:
    """II.사업의 내용 섹션을 마크다운으로 렌더 — 영업부문 주석이 없을 때 폴백(삼일 등)."""
    if not html:
        return None
    im = re.search(r"II\.\s*사업의\s*내용", _strip_tags(html[:400000]))
    # 태그포함 원본에서 위치 다시 찾기(간단히 키워드로)
    i2 = html.find("사업의 내용")
    if i2 < 0:
        return None
    i3 = html.find("재무에 관한 사항", i2 + 100)
    end = i3 if i3 > i2 else i2 + max_chars
    return _render_html_region_md(html, i2, min(end, i2 + max_chars))


def find_segment_candidates(html: str, max_tables: int = 5, max_rows: int = 40) -> list[dict]:
    """부문표 후보를 점수순 상위 N개 반환. 각 {rendered(파이프격자 텍스트), score, rows, cols}.

    호출측 LLM이 이 중 진짜 부문표를 골라 값을 읽는다. 수백 표 → ~5개로 기계적 narrow.
    """
    if not html:
        return []
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return []
    # 1) 정규식으로 <table> 블록 추출 + 문자열 프리필터(전체 DOM 파싱 회피 = 22MB도 <250ms).
    #    부문신호는 '표 안' 또는 '표 앞 800자 헤딩'에서 찾음 — 부문표 상당수가 '사업부문별
    #    요약 재무현황' 같은 제목만 표 밖에 두고 표 셀엔 부문명(반도체/화학)만 담아서(11사 실측).
    blocks = []
    for m in _TABLE_RE.finditer(html):
        t = m.group(0)
        specific = _specific_seg_names(t)
        # 지표라벨(매출액 등) 있거나, 지표라벨 없어도 고유 부문명 2개+면 통과(아미노로직스: 표엔
        # 매출액 라벨 없이 당기/전기 열에 아미노산부문·원료의약품부문 수치만 있는 케이스).
        if not (any(x in t for x in _METRIC) or len(specific) >= 2):
            continue
        if not _BIGNUM_RE.search(t):
            continue
        if _is_roster(t):
            continue                                   # 임원·주주 명부(담당업무 'OO부문')는 부문표 아님
        pre = html[max(0, m.start() - 800):m.start()]
        if not (any(x in t for x in _SEG_SIGNAL) or any(x in pre for x in _SEG_SIGNAL)
                or len(specific) >= 2):
            continue
        blocks.append((t, pre))
    # 2) 통과한 소수 표만 bs4 파싱해 점수·격자화
    scored = []
    for block, pre in blocks:
        tb = BeautifulSoup(block, "lxml").find("table")
        if tb is None:
            continue
        txt = tb.get_text(" ", strip=True)
        seg_names = _specific_seg_names(txt)
        if not (any(m in txt for m in _METRIC) or len(seg_names) >= 2):
            continue
        if not re.search(r"[\d,]{5,}", txt):
            continue
        if not (any(k in txt for k in _SEG_SIGNAL) or any(k in pre for k in _SEG_SIGNAL)
                or len(seg_names) >= 2):
            continue
        sc = _score(txt, pre)
        if sc <= 0:
            continue
        grid = _table_to_grid(tb)
        if not grid or len(grid) > max_rows or max((len(r) for r in grid), default=0) > 40:
            continue
        rendered = "\n".join(" | ".join(c[:24] for c in r) for r in grid)
        scored.append({"rendered": rendered[:5000], "score": sc,
                       "rows": len(grid), "cols": max(len(r) for r in grid)})
    scored.sort(key=lambda x: x["score"], reverse=True)
    # 중복 제거(동일 rendered 앞부분)
    seen, out = set(), []
    for c in scored:
        k = c["rendered"][:120]
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
        if len(out) >= max_tables:
            break
    return out
