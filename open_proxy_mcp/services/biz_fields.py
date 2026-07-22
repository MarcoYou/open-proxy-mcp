"""II.사업의 내용 추가 필드(사업장·가동률·rnd·backlog·customers) — markdown-primary 추출.

설계(260718 census 286사 + 재무·공시·산업 QA패널 결론): **markdown-primary**.
- 신뢰경로 = 해당 소절 원문을 통째 **마크다운**으로 반환 → 호출측 강력 AI가 읽어 추출([[feedback_leverage_caller_ai]]).
- 정형 hint = 애매하지 않을 때만 아주 작게(비-authoritative). "이게 진짜 X표인가" 판정 게이트는 두지 않음
  (파서가 판정하면 조용한 오답 — 사업장 유형자산 함정·가동률 단위카오스는 호출측 AI가 원문 읽어 구분).
마크다운은 get_document HTML에서 만들고, hint는 반환된 마크다운 안에서만 산출한다.
"""
from __future__ import annotations

import html as html_lib
import re
from bisect import bisect_right
from dataclasses import dataclass

from open_proxy_mcp.services.segment_candidates import (
    _TABLE_RE,
    _is_roster,
    _md_has_data_rows,
    _render_html_region_md,
    _strip_tags,
)

# 소절 접두(번호/한글자/괄호): "2." "나." "(2)" "2)" 목차 제목 앞에 오는 마커
_SUBSEC_PREFIX = re.compile(r"(?:\d{1,2}|[가-하]|\(\s*\d{1,2}\s*\)|\d{1,2}\s*\))\s*[.)]?\s*$")

_P_BLOCK_RE = re.compile(r"<p\b(?P<attrs>[^>]*)>(?P<body>.*?)</p\s*>", re.DOTALL | re.IGNORECASE)
_SPAN_BLOCK_RE = re.compile(r"<span\b(?P<attrs>[^>]*)>(?P<body>.*?)</span\s*>", re.DOTALL | re.IGNORECASE)
_TITLE_BLOCK_RE = re.compile(r"<title\b(?P<attrs>[^>]*)>(?P<body>.*?)</title\s*>", re.DOTALL | re.IGNORECASE)
_BOLD_ATTR_RE = re.compile(r"usermark\s*=\s*['\"][^'\"]*\bB\b[^'\"]*['\"]", re.IGNORECASE)
_LEADING_BOLD_SPAN_RE = re.compile(
    r"^\s*<span\b(?P<attrs>[^>]*)>(?P<body>.*?)</span\s*>",
    re.DOTALL | re.IGNORECASE,
)
_TOC_LINE_RE = re.compile(r"[-.·]{3,}\s*\d+\s*$")
_TOP_LEVEL_SECTION_RE = re.compile(
    r"\d{1,2}\.\s*(?:사업의\s*개요|주요\s*제품\s*및\s*서비스|"
    r"원재료\s*및\s*생산설비|매출\s*및\s*수주상황|"
    r"위험관리\s*및\s*파생거래|주요계약\s*및\s*연구개발활동|기타\s*참고사항)"
)
_LONG_INLINE_HEADING_RE = re.compile(
    r"^\s*(?:[가-하]\.\s*|\(\s*\d{1,2}\s*\)\s*|\d{1,2}\)\s*|\(\s*[가-하]\s*\)\s*)"
)
_EMBEDDED_BIZ_HEADING_RE = re.compile(
    r"(?P<marker>(?:[가-하]\.\s*|\(\s*\d{1,2}\s*\)\s*|\d{1,2}\)\s*|"
    r"\(\s*[가-하]\s*\)\s*))"
    r"(?=(?:생산|설비|사업장|영업|가\s*동|연구개발|수주|판매|주요\s*(?:매출처|고객|거래처|수요처)))"
)
_EMBEDDED_BIZ_RAW_RE = re.compile(
    r"(?P<marker>(?:[가-하]\.\s*|\(\s*\d{1,2}\s*\)\s*|\d{1,2}\)\s*|"
    r"\(\s*[가-하]\s*\)\s*))"
    r"(?:\s|<[^>]+>)*(?=(?:생산|설비|사업장|영업|가\s*동|연구개발|수주|판매|"
    r"주요\s*(?:매출처|고객|거래처|수요처)))",
    re.IGNORECASE,
)
_ANY_HEADING_MARKER_RE = re.compile(
    r"(?:[IVXLC]+\.\s*|\d{1,2}(?:-\d{1,2})*\.\s*|[가-하]\.\s*|"
    r"\(\s*\d{1,2}\s*\)\s*|\d{1,2}\)\s*|\(\s*[가-하]\s*\)\s*)",
    re.IGNORECASE,
)
_HEADING_LEVELS = (
    (re.compile(r"^\s*[IVXLC]+\.\s*", re.IGNORECASE), 0),
    (re.compile(r"^\s*\d{1,2}(?:-\d{1,2})*\.\s*"), 1),
    (re.compile(r"^\s*[가-하]\.\s*"), 2),
    (re.compile(r"^\s*(?:\(\s*\d{1,2}\s*\)|\d{1,2}\)|\(\s*[가-하]\s*\))\s*"), 3),
)


@dataclass(frozen=True)
class _Heading:
    start: int
    end: int
    text: str
    level: int
    tag: str


@dataclass(frozen=True)
class BizRegionIndex:
    """DART XML/HTML heading coordinates, built once and shared by field extractors."""

    headings: tuple[_Heading, ...]


def _heading_level(text: str) -> int | None:
    for pattern, level in _HEADING_LEVELS:
        if pattern.search(text):
            return level
    return None


def _clean_heading_text(raw: str) -> str:
    return " ".join(html_lib.unescape(_strip_tags(raw)).replace("\xa0", " ").split())


def _is_concatenated_toc(text: str) -> bool:
    """Reject DART TOC paragraphs whose section titles were flattened into one line."""
    return len(_TOP_LEVEL_SECTION_RE.findall(text)) >= 3


def build_region_index(html: str) -> BizRegionIndex:
    """Index short, styled subsection headings without parsing the multi-MB document DOM."""
    if not html:
        return BizRegionIndex(())
    found: list[_Heading] = []
    table_ranges = [(match.start(), match.end()) for match in _TABLE_RE.finditer(html)]
    table_starts = [start for start, _ in table_ranges]

    def inside_table(pos: int) -> bool:
        index = bisect_right(table_starts, pos) - 1
        return index >= 0 and pos < table_ranges[index][1]

    for tag, pattern in (("title", _TITLE_BLOCK_RE), ("p", _P_BLOCK_RE), ("span", _SPAN_BLOCK_RE)):
        for match in pattern.finditer(html):
            if inside_table(match.start()):
                continue
            body = match.group("body")
            attrs = match.group("attrs") or ""
            leading_span = _LEADING_BOLD_SPAN_RE.search(body) if tag == "p" else None
            if leading_span and _BOLD_ATTR_RE.search(leading_span.group("attrs") or ""):
                text = _clean_heading_text(leading_span.group("body"))
                is_bold = True
            else:
                text = _clean_heading_text(body)
                is_bold = tag == "title" or bool(_BOLD_ATTR_RE.search(attrs))
            level = _heading_level(text)
            if level is None and tag == "p":
                embedded = _EMBEDDED_BIZ_HEADING_RE.search(text[:600])
                if embedded:
                    text = text[embedded.start("marker"):]
                    level = _heading_level(text)
            if level is None or _TOC_LINE_RE.search(text) or _is_concatenated_toc(text):
                continue
            # Unstyled long prose beginning with a year/number is not a heading. DART headings
            # are normally bold. A few issuers put a marker-led heading and all its prose in one
            # <p>; accept only the non-date marker families and keep a compact source label.
            if not is_bold and len(text) > 100:
                if tag != "p" or not _LONG_INLINE_HEADING_RE.search(text):
                    continue
                text = text[:220]
            if len(text) > 220:
                continue
            heading_start = match.start()
            if tag == "p" and not is_bold:
                raw_embedded = _EMBEDDED_BIZ_RAW_RE.search(body[:4000])
                if raw_embedded:
                    heading_start = match.start("body") + raw_embedded.start("marker")
            found.append(_Heading(heading_start, match.end(), text, level, tag))

    # A <p><span usermark="... B">heading</span> prose...</p> produces two coordinates.
    # Keep the outer <p> so rendering includes both the heading and its inline prose.
    deduped: list[_Heading] = []
    for heading in sorted(found, key=lambda h: (h.start, -h.end)):
        heading_key = re.sub(r"\s+", "", heading.text)
        duplicate_at = next(
            (
                i
                for i, prev in enumerate(deduped)
                if prev.start <= heading.start <= prev.end
                and prev.level == heading.level
                and (
                    re.sub(r"\s+", "", prev.text) == heading_key
                    or (prev.tag == "p" and re.sub(r"\s+", "", prev.text).startswith(heading_key))
                )
            ),
            None,
        )
        if duplicate_at is None:
            deduped.append(heading)
        elif heading.tag == "p" and deduped[duplicate_at].tag != "p":
            deduped[duplicate_at] = heading
    deduped.sort(key=lambda h: h.start)
    return BizRegionIndex(tuple(deduped))


def _enclosing_section_end(html: str, pos: int) -> int | None:
    for tag in ("section-2", "section-1"):
        open_pos = max(html.rfind(f"<{tag}", 0, pos), html.rfind(f"<{tag.upper()}", 0, pos))
        close_before = max(html.rfind(f"</{tag}", 0, pos), html.rfind(f"</{tag.upper()}", 0, pos))
        if open_pos < 0 or close_before > open_pos:
            continue
        ends = [x for x in (html.find(f"</{tag}>", pos), html.find(f"</{tag.upper()}>", pos)) if x >= 0]
        if ends:
            return min(ends)
    return None


def _chapter_for(index: BizRegionIndex, heading: _Heading) -> str:
    chapter = "document"
    for candidate in index.headings:
        if candidate.start >= heading.start:
            break
        if candidate.level == 0:
            chapter = candidate.text
    return chapter


def _matched_heading_label(text: str, keyword_start: int) -> str:
    markers = []
    initial = _ANY_HEADING_MARKER_RE.match(text)
    if initial:
        markers.append(initial)
    markers.extend(
        marker
        for marker in _EMBEDDED_BIZ_HEADING_RE.finditer(text)
        if marker.start() <= keyword_start
    )
    selected = markers[-1] if markers else None
    start = selected.start() if selected else 0
    label = text[start:]
    current_marker_end = selected.end() - selected.start() if selected else 1
    next_marker = _ANY_HEADING_MARKER_RE.search(label, current_marker_end)
    if next_marker:
        label = label[:next_marker.start()]
    return label[:120].strip()


def _render_structural_region_md(html: str, start: int, end: int) -> str | None:
    fragment = html[start:end]
    if fragment.lstrip().startswith("<"):
        return _render_html_region_md(html, start, end)
    wrapped = f"<p>{fragment}</p>"
    return _render_html_region_md(wrapped, 0, len(wrapped))


def _is_near_duplicate_heading(heading: _Heading, peer: _Heading) -> bool:
    """Detect adjacent span/paragraph copies of one visual heading."""
    if heading.level != peer.level or peer.start - heading.start > 500:
        return False
    heading_key = re.sub(r"\s+", "", heading.text)
    peer_key = re.sub(r"\s+", "", peer.text)
    return heading_key.startswith(peer_key) or peer_key.startswith(heading_key)


def _recovery_end(index: BizRegionIndex, heading: _Heading, section_end: int) -> int:
    """Bound malformed-depth recovery at the next top-level business subsection."""
    # A DART TITLE is already scoped by its enclosing SECTION-2. Numbered paragraphs
    # inside that section often restart at `1.` and must not be mistaken for a sibling.
    if heading.tag == "title":
        return section_end
    for candidate in index.headings:
        if candidate.start <= heading.start:
            continue
        if candidate.start >= section_end:
            break
        if candidate.level <= 1:
            return candidate.start
    return section_end


def _structural_regions(html: str, kw_patterns: list[str], index: BizRegionIndex,
                        content_re=None, need_rows: int = 1, exclude_chapter_re=None) -> list[dict]:
    candidates: list[tuple[int, _Heading, str]] = []
    for priority, keyword in enumerate(kw_patterns):
        for heading in index.headings:
            match = re.search(keyword, heading.text)
            if match and not (
                exclude_chapter_re and exclude_chapter_re.search(_chapter_for(index, heading))
            ):
                candidates.append(
                    (priority, heading, _matched_heading_label(heading.text, match.start()))
                )

    regions: list[dict] = []
    occupied: list[tuple[int, int]] = []
    # Prefer the most specific matching child heading over a broad parent such as
    # "4. 매출 및 수주상황". Pattern order still decides between different aliases.
    for _, heading, heading_label in sorted(
        candidates, key=lambda item: (item[0], -item[1].level, item[1].start)
    ):
        section_end = _enclosing_section_end(html, heading.start)
        end = section_end or len(html)
        boundary = "section_end" if section_end else "document_end"
        for peer in index.headings:
            if peer.start <= heading.start:
                continue
            if section_end is not None and peer.start >= section_end:
                break
            if peer.level <= heading.level:
                if _is_near_duplicate_heading(heading, peer):
                    continue
                end = peer.start
                boundary = "peer_heading"
                break
        if end <= heading.start or any(heading.start < b and end > a for a, b in occupied):
            continue
        md = _render_structural_region_md(html, heading.start, end)
        # A broad DART TITLE must not satisfy the field gate by its title alone.
        content_source = (
            _strip_tags(html[heading.end:end]) if heading.tag == "title" else (md or "")
        )
        has_content = bool(md and (content_re is None or content_re.search(content_source)))
        plain = re.sub(r"[\s|:\-]", "", md or "")
        has_body = bool(md and (_md_has_data_rows(md, need_rows) or len(plain) >= 20))

        # Some issuers invert marker depth, for example `(3) 판매경로` -> `가. 판매품목`.
        # If the inferred peer leaves only a heading, retry only to the next top-level
        # subsection (or the enclosing SECTION-2 end). This remains structurally bounded.
        if not has_body and section_end is not None and end < section_end:
            recovery_end = _recovery_end(index, heading, section_end)
            recovered = _render_structural_region_md(html, heading.start, recovery_end)
            recovered_plain = re.sub(r"[\s|:\-]", "", recovered or "")
            recovered_content_source = (
                _strip_tags(html[heading.end:recovery_end])
                if heading.tag == "title"
                else (recovered or "")
            )
            recovered_has_content = bool(
                recovered and (
                    content_re is None or content_re.search(recovered_content_source)
                )
            )
            recovered_has_body = bool(
                recovered
                and (_md_has_data_rows(recovered, need_rows) or len(recovered_plain) >= 20)
            )
            if recovered_has_content and recovered_has_body:
                md = recovered
                plain = recovered_plain
                end = recovery_end
                boundary = (
                    "section_end_recovery"
                    if recovery_end == section_end
                    else "top_level_recovery"
                )
                has_content = True
                has_body = True

        if not has_content or not has_body:
            continue
        if any(heading.start < occupied_end and end > occupied_start
               for occupied_start, occupied_end in occupied):
            continue
        # A real structural heading may legitimately contain prose only. Numeric rows remain
        # the strongest signal, while meaningful prose is accepted after TOC headings were removed.
        regions.append({
            "markdown": md,
            "heading": heading_label,
            "chapter": _chapter_for(index, heading),
            "boundary": boundary,
            "start": heading.start,
            "end": end,
        })
        occupied.append((heading.start, end))
        if len(regions) >= 2:
            break
    return sorted(regions, key=lambda region: region["start"])


def _legacy_regions(html: str, kw_patterns: list[str], max_chars: int,
                    need_rows: int, content_re=None) -> list[dict]:
    """Old fixed-window path, retained only for documents without usable heading markup."""
    hits = []
    for keyword in kw_patterns:
        for match in re.finditer(keyword, html):
            pre = _strip_tags(html[max(0, match.start() - 26):match.start()])
            if not _SUBSEC_PREFIX.search(pre) or _is_roster(html[match.start():match.start() + 6000]):
                continue
            hits.append(match.start())
    regions, last_end = [], -1
    for start in sorted(set(hits)):
        if start < last_end:
            continue
        end = start + max_chars
        md = _render_html_region_md(html, max(0, start - 40), end)
        if not md or (need_rows > 0 and not _md_has_data_rows(md, need_rows)):
            continue
        if content_re is not None and not content_re.search(md):
            continue
        regions.append({"markdown": md, "heading": None, "chapter": "document",
                        "boundary": "fixed_window_fallback", "start": start, "end": end})
        last_end = end
        if len(regions) >= 2:
            break
    return regions


def _render_biz_subsection_regions(html: str, kw_patterns: list[str], max_chars: int = 22000,
                                   need_rows: int = 1, content_re=None,
                                   region_index: BizRegionIndex | None = None,
                                   exclude_chapter_re=None) -> list[dict]:
    if not html:
        return []
    index = region_index or build_region_index(html)
    regions = _structural_regions(
        html, kw_patterns, index, content_re=content_re, need_rows=need_rows,
        exclude_chapter_re=exclude_chapter_re,
    )
    # A populated outline means this document has usable structural markup. Falling back to a
    # fixed window after a field miss would reintroduce cross-reference and adjacent-section bleed.
    if regions or index.headings:
        return regions
    return _legacy_regions(html, kw_patterns, max_chars, need_rows, content_re=content_re)


def render_biz_subsection_markdown(html: str, kw_patterns: list[str], max_chars: int = 22000,
                                   need_rows: int = 1, content_re=None,
                                   region_index: BizRegionIndex | None = None) -> str | None:
    """II.사업의 내용의 특정 소절(kw_patterns 제목)을 통째 마크다운으로 렌더.

    소절 제목이 번호/한글자 접두를 가진 헤딩일 때만 앵커(프로즈 언급 오탐 방지). 명부(roster) 배제.
    content_re 주면 렌더된 구간이 그 필드 내용을 실제로 담을 때만 채택(부모/오섹션 오탐 차단) —
    이 content-gate 덕에 앵커를 넓게 잡아도 안전(넓은 앵커=놓침↓, gate=오탐↓).
    """
    regions = _render_biz_subsection_regions(
        html, kw_patterns, max_chars=max_chars, need_rows=need_rows,
        content_re=content_re, region_index=region_index,
    )
    return "\n\n———\n\n".join(region["markdown"] for region in regions) if regions else None


# ─────────────────────────── 가동률 (utilization) ───────────────────────────
# 소절 제목 변형(자간 삽입·율/률·결합제목·슬래시). 앵커는 '전체 헤딩'을 잡아야 접두(나./3))가 붙음.
# content-gate(_C_UTIL)가 오섹션을 거르므로 앵커를 넓게 잡아 놓침을 줄인다(census 54 놓침 대응).
_UTIL_HEAD = [
    r"생산능력\s*[/·,]\s*(?:생산)?실적\s*[/·,]\s*가\s*동\s*[율률]",       # 3) 생산능력/실적/가동률 (한미반도체)
    r"생산능력[\s,]*(?:및\s*)?생산실적[\s,및]*가\s*동\s*[율률]",           # 나. 생산능력, 생산실적 및 가동률 (한화솔루션)
    r"생산능력\s*(?:및|,)\s*가\s*동\s*[율률]",                            # 나. 생산능력 및 가동률 (한국전력공사)
    r"생산실적\s*(?:및|,|/)?\s*가\s*동\s*[율률]",                          # 나. 생산실적 및 가동률/가동율 (오리온)
    r"당해\s*사업연도의?\s*가\s*동\s*[율률]",
    r"당기\s*가\s*동\s*[율률]",
    r"설비\s*가\s*동\s*[율률]",
    r"가\s*동\s*[율률]",                                                   # (2) 가동률 단독 (쎄트렉아이) — prefix+content-gate로 안전
    r"생산\s*능력\s*(?:및|,)\s*(?:생산)?실적",                            # 3) 생산능력 및 실적 (한미약품 등 제약 — 가동시간表 동반, content-gate가 확인)
    r"평균\s*가\s*동\s*시간",                                             # (2) 평균 가동 시간 (한미약품 사업장별 가동일수·가동가능시간)
    # 단독 생산능력/실적은 제목 시작에서만 허용한다. 판매전략·산업전망 제목 안의
    # 일반적인 "생산능력 확대" 언급까지 앵커로 쓰면 오탐이 된다.
    r"^\s*(?:(?:[가-하]|\d{1,2})\s*[.)]\s*|\(\s*(?:\d{1,2}|[가-하])\s*\)\s*)?생산\s*능력",
    r"^\s*(?:(?:[가-하]|\d{1,2})\s*[.)]\s*|\(\s*(?:\d{1,2}|[가-하])\s*\)\s*)?생산\s*실적",
    r"생산\s*능력\s*(?:및\s*생산능력의?\s*)?산출\s*근거",                  # 현대차
    r"산출\s*근거",                                                         # 티엠씨: 산출근거 내 평균가동시간
    r"생산\s*설비\s*[,·]?\s*사업장의?\s*현황",                          # 아이에스동서: 상위절 안 생산실적·가동률
    r"생산\s*및\s*설비(?:에?\s*관한\s*사항|\s*\(|의?\s*현황)?",            # 2. 생산 및 설비(1)생산능력 (HL만도)
    r"생산\s*설비에?\s*관한\s*사항",                                    # HLB: 상위절 안 가동률 표
]
_UTIL_PRODUCTION_PARENT_HEAD = [
    r"^\s*(?:(?:[가-하]|\d{1,2})\s*[.)]\s*|\(\s*(?:\d{1,2}|[가-하])\s*\)\s*)?생산\s*설비",
]
# 값 hint는 %만 보수적으로(단위 없는 '1개'·'1?' 노이즈 배제). 시간/톤 등은 마크다운이 담당.
_UTIL_PCT = re.compile(r"(?:평균\s*|가중평균\s*|설비\s*)?가\s*동\s*[율률]"
                       r"[^\d\n]{0,12}?약?\s*([\d]{1,3}(?:\.\d+)?)\s*%")
_UTIL_NA = re.compile(
    r"가\s*동\s*[율률][^\n]{0,60}?"
    r"(?:기재하지\s*않았|산정할?\s*수\s*없|일률적으로\s*산출.{0,10}곤란|"
    r"해당\s*사항\s*(?:이)?\s*없|보안\s*관계상|정보\s*유출)"
)
_UTIL_PRODUCTION_EXPLICIT_NA = re.compile(
    r"생산\s*능력(?:\s*및\s*(?:생산\s*)?실적|\s*및\s*설비)?"
    r"(?:(?!\(\s*\d+\s*\))[\s\S]){0,100}?"
    r"(?:해당\s*사항\s*(?:이)?\s*없|기재할\s*사항(?:은)?\s*없|기재하지\s*않)|"
    r"생산\s*및\s*설비[\s\S]{0,100}?(?:해당\s*사항\s*(?:이)?\s*없|기재하지\s*않)|"
    r"생산(?:에?\s*관한\s*사항|\s*시설)[\s\S]{0,100}?기재하지\s*않|"
    r"별도의?\s*생산\s*시설[\s\S]{0,80}?필요하지\s*(?:않|는)|"
    r"생산[^\n]{0,80}?개념이\s*적용되지\s*않[^\n]{0,80}?기재를?\s*생략"
)


def extract_utilization(biz_text: str, html: str, region_index: BizRegionIndex | None = None) -> dict:
    """가동률 markdown-primary + %힌트(안전할 때만, 비교금지). 정의는 _field 아래에서 재사용."""
    return _util_impl(biz_text, html, region_index=region_index)


# ═══════════ markdown-primary 공통 필드 추출 (사업장·rnd·backlog·customers) ═══════════
# 설계: 파서가 '진짜 X표인가' 판정하지 않는다. 소절을 통째 마크다운으로 렌더→호출측 AI가 읽음.
# 실패 모드는 '섹션 있는데 md=0'(앵커 미스)뿐이라 헤딩 패턴을 census 앵커로 넓게 잡는다.

# content-gate 정규식: 렌더 구간이 실제 그 필드 내용을 담는지(부모/오섹션 오탐 차단)
_C_UTIL = re.compile(r"가\s*동\s*[율률]|가동\s*시간|생산\s*(?:능력|실적)")
_C_SITE = re.compile(r"소재지|주소|사업장|사업소|공장|영업소|점포|㎡|[가-힣]{2}(?:시|도)\b|"
                     r"본사|본점|경기|서울|인천|부산|대구|대전|광주|울산|충청|전라|경상|강원|제주|"
                     r"베트남|중국|미국")
_C_RND = re.compile(r"연구개발|핵심\s*기술")
_C_BL = re.compile(
    r"수주\s*(?:잔고|잔액|총액|액|상황|현황|계약|사항)|기납품|계약잔액|납기|발주처"
)
_C_CUST = re.compile(
    r"고객|매출처|판매처|거래처|판매\s*경로|수요처|납품|직판|직수출|"
    r"대리점(?:\s*판매)?|도매점|소매점|전문점|판매\s*조직|국내\s*및\s*해외\s*판매|"
    r"광고\s*대행사|계약\s*조건"
)
_FINANCIAL_CHAPTER_RE = re.compile(r"^III\.\s*재무")


def _field(biz_text: str, html: str, head_patterns: list[str], na_re, content_re=None,
           max_chars: int = 20000, region_index: BizRegionIndex | None = None,
           exclude_chapter_re=None) -> dict:
    """markdown-primary: 소절 마크다운(content-gate 통과) 있으면 MARKDOWN, 없고 NA어휘면 N/A, 아니면 미검출."""
    regions = _render_biz_subsection_regions(
        html, head_patterns, max_chars=max_chars, content_re=content_re, region_index=region_index,
        exclude_chapter_re=exclude_chapter_re,
    )
    if exclude_chapter_re:
        regions = [region for region in regions
                   if not exclude_chapter_re.search(region["chapter"])]
    if regions:
        markdown = "\n\n———\n\n".join(region["markdown"] for region in regions)
        explicit_na = na_re.search(markdown) if na_re else None
        if explicit_na and not _md_has_data_rows(markdown, 1):
            return {
                "status": "NOT_APPLICABLE",
                "extraction_status": "NOT_APPLICABLE",
                "na_reason": _strip_tags(explicit_na.group(0))[:60],
            }
        return {
            "status": "MARKDOWN",
            "extraction_status": "SUCCESS",
            "source": "heading",
            "section_source": {
                "matched_headings": [region["heading"] for region in regions if region.get("heading")],
                "chapters": list(dict.fromkeys(region["chapter"] for region in regions)),
                "selection_method": "heading",
                "boundary_methods": list(dict.fromkeys(region["boundary"] for region in regions)),
            },
            "markdown": markdown,
        }
    na = na_re.search(biz_text) if (na_re and biz_text) else None
    return {
        "status": "NOT_APPLICABLE",
        "extraction_status": "NOT_APPLICABLE" if na else "NOT_COLLECTED",
        "na_reason": (_strip_tags(na.group(0))[:60] if na else "해당 소절 미검출"),
    }


def _signal_paragraph_field(html: str, signal_re, source: str) -> dict | None:
    """헤딩 없는 강한 회사 고유 행동문만 해당 HTML 문단 단위로 보조 회수."""
    markdown: list[str] = []
    for match in _P_BLOCK_RE.finditer(html or ""):
        text = _clean_heading_text(match.group("body"))
        if not signal_re.search(text):
            continue
        rendered = _render_html_region_md(html, match.start(), match.end())
        if rendered and rendered not in markdown:
            markdown.append(rendered)
        if len(markdown) >= 2:
            break
    if not markdown:
        return None
    return {
        "status": "MARKDOWN",
        "extraction_status": "SUCCESS",
        "source": source,
        "section_source": {
            "matched_headings": [],
            "chapters": ["II. 사업의 내용"],
            "selection_method": source,
            "boundary_methods": ["paragraph"],
        },
        "markdown": "\n\n———\n\n".join(markdown),
    }


# ── 사업장 (business sites) — 위치판정은 호출측 AI (유형자산 함정은 AI가 원문 읽어 구분) ──
_SITE_HEAD = [
    r"생산설비\s*및\s*투자\s*현황(?:\s*등)?",              # 삼성전자·케이티앤지·대한전선
    r"생산\s*설비의?\s*현황(?:\s*등)?",
    r"(?:주요\s*)?(?:국내|해외)?\s*사업장의?\s*현황",
    r"생산\s*설비\s*(?:등\s*)?에?\s*관한\s*사항",
    r"생산\s*설비\s*등(?:의?\s*현황)?",
    r"생산\s*및\s*설비(?:에?\s*관한\s*사항|의?\s*현황(?:\s*등)?)?",
    r"생산\s*및\s*영업\s*시설",
    r"주요\s*사업설비",
    r"창고\s*보유\s*현황",
    r"생산과\s*영업에\s*중요한\s*(?:시설|물적)",
    r"영업용\s*설비\s*현황",                               # 유통(이마트·롯데쇼핑)
    r"영업\s*설비(?:의?\s*현황)?",                          # 편의점(BGF리테일: '가. 영업설비' + 점포·사업장명)
    r"영업장\s*(?:의?\s*)?현황",                            # 호텔·면세(호텔신라: '나. 영업장 현황(요약)')
    r"주요\s*설비의?\s*현황",                               # 건설(대우건설: '나. 주요 설비의 현황')
    r"설비의?\s*현황",                                      # 무접두 '가. 설비 현황'(넷마블: 사업장별 소재지表). _SUBSEC_PREFIX가 접두 보장
    r"물적\s*재산의?\s*(?:내용|현황)",                     # IT(NAVER 등)
]
_SITE_PRODUCTION_HEAD = [
    r"생산능력\s*(?:및|,)?\s*(?:생산)?실적(?:\s*및\s*가\s*동\s*[율률])?",
    r"생산실적\s*및\s*가\s*동\s*[율률]",
]
_C_SITE_LOCATION_STRONG = re.compile(
    r"소재지|주소|본사|본점|[가-힣A-Za-z0-9()]+\s*공장|"
    r"경기|서울|인천|부산|대구|대전|광주|울산|충청|전라|경상|강원|제주"
)
_SITE_REFERENCE_HEAD = [r"산업\s*표준"]
_C_SITE_REFERENCE = re.compile(r"송파\s*및\s*하남\s*사이트")
_SITE_NA = re.compile(
    r"기재하지\s*않았|해당\s*사항\s*(?:이)?\s*없|외주\s*(?:가공|생산)|위탁\s*생산|"
    r"\bOEM\b|인적자원을?\s*활용|별도의?\s*생산\s*(?:시설|설비)|"
    r"(?:원재료\s*및\s*)?생산설비에?\s*해당하는\s*사항이\s*없|"
    r"생산설비가\s*존재하지\s*않"
)
_SITE_PROSE_SIGNAL = re.compile(
    r"(?:본사|본점)[^.\n]{0,100}?(?:생산기지|\d+\s*개의?\s*사업장)|"
    r"해남야드[^.\n]{0,100}?공장|"
    r"송파\s*및\s*하남\s*사이트|"
    r"울산석유화학\s*단지[^.\n]{0,100}?생산시설"
)


# ── rnd 연구개발 (hint=매출액대비 % + 계금액; 회계처리/보조금 분해는 마크다운이 담당) ──
_RND_HEAD = [
    r"연구개발\s*실적",
    r"연구개발\s*비용",
    r"연구개발\s*활동(?:의?\s*개요)?",
    r"주요계약\s*및\s*연구개발활동",
    r"연구개발\s*담당\s*조직",
    r"핵심\s*기술\s*현황",
]
_RND_RATIO = re.compile(r"연구개발비\s*/?\s*(?:매출액|영업수익)\s*비율[^\d\n]{0,30}?([\d]{1,3}(?:\.\d+)?)\s*%")
_RND_NA = re.compile(
    r"연구개발\s*활동(?:[^\n]{0,30}|(?:\s|[-–—:\"'·]){0,24})"
    r"(?:해당\s*사항\s*(?:이)?\s*없|없습니다)"
)
_RND_PROSE_SIGNAL = re.compile(
    r"R\s*&\s*D\s*센터\s*운영|"
    r"(?:연구\s*개발|기술\s*개발)(?:을|를)?[^.\n]{0,80}?"
    r"(?:집중적으로\s*추진|신제품\s*출시|지속(?:할)?\s*예정)",
    re.IGNORECASE,
)


# ── backlog 수주 (value hint 없음 — flow표 오귀속 방지, QA BLOCKER. 마크다운만) ──
_BL_HEAD = [
    r"진행률\s*적용?\s*수주계약\s*현황",
    r"수주\s*계약\s*현황",
    r"수주\s*사항",
    r"수주\s*(?:상황|현황)",
    r"수주에?\s*관한\s*사항",
    r"매출\s*및\s*수주\s*상황",
]
_BL_NA = re.compile(
    r"(?<!소)수주(?!\s*주권)\s*(?:사항|상황|현황|에\s*관한\s*사항)?"
    r"\s*[-:.]?\s*(?:\n\s*)?(?:해당\s*사항\s*(?:이)?\s*없|없습니다)|"
    r"(?<!소)수주(?!\s*주권)(?!\s*액[^\n]{0,14}?(?:의\s*)?개념)[^\n]{0,24}?"
    r"(?:해당\s*사항\s*(?:이)?\s*없|없습니다)"
)
_BL_PROSE_SIGNAL = re.compile(
    r"(?:당사(?:가|는)?\s*)?(?:기술\s*이전을?\s*통해\s*)?수주(?:하|되|하게)[^.\n]{0,180}?"
    r"(?:\d{4}년까지\s*납품|납품(?:을)?\s*(?:완료할\s*)?(?:예정|계획))|"
    r"수주\s*계약을?\s*체결[^.\n]{0,80}?(?:제품을?\s*)?공급"
)
_BL_ACTUAL_SIGNAL = re.compile(
    r"수주\s*(?:잔고|잔액|총액)|계약잔액|기납품|발주처|"
    r"수주\s*계약을?\s*체결|\d{4}년까지\s*납품"
)


# ── customers 주요고객/매출처 (hint=집중률 % 안전할 때만; 이름은 마크다운) ──
_CUST_HEAD = [
    r"주요\s*매출처(?:\s*(?:및\s*매출\s*비중|현황))?",
    r"매출처별\s*비중",
    r"주요\s*판매처",
    r"주요\s*고객에\s*대한\s*(?:정보|공시)",
    r"주요\s*(?:거래처|수요처)",
    r"판매\s*경로(?:\s*(?:및|와)\s*(?:판매\s*)?방법)?",
    r"판매\s*방법(?:\s*및\s*(?:조건|판매\s*전략))?",
    r"판매\s*조직(?:\s*및\s*판매\s*전략)?",
]
_CUST_NA = re.compile(r"(?:주요\s*(?:매출처|고객)|판매\s*경로)[^\n]{0,30}?(해당\s*사항\s*(?:이)?\s*없|없습니다)")
_CUST_FIN_HEAD = [
    r"모집\s*형태별\s*영업\s*현황", r"보험종목별\s*모집\s*경로",
    r"신용카드업", r"자산관리", r"영업\s*개황",
]
_C_CUST_FIN = re.compile(
    r"설계사|방카슈랑스|가맹점\s*수|실질\s*회원|고객\s*자산|기관\s*고객|"
    r"주요\s*고객으로|모집\s*경로"
)
_CUST_BROAD_HEAD = [
    r"주요\s*제품\s*및\s*서비스", r"시장(?:의)?\s*특성", r"회사의?\s*현황",
    r"경쟁\s*상황(?:\s*,?\s*시장\s*점유율)?", r"렌탈\s*사업", r"투자\s*자산\s*상세",
    r"임대차\s*계약\s*체결\s*내역",
]
_C_CUST_STRONG = re.compile(
    r"주요\s*고객\s*정보|주요\s*(?:외부\s*)?(?:고객(?:사)?|매출처|판매처)|"
    r"단일\s*고객|주된\s*판매\s*경로|기업\s*고객[^.\n]{0,100}?(?:판매|대여)|"
    r"고객사[^.\n]{0,100}?(?:공급|납품|프로젝트)|(?:책임\s*)?임차인|"
    r"임대료\s*매출|\d+\s*여?\s*개?\s*국가[^.\n]{0,100}?대리점|직판|대리점\s*판매"
)
_C_CUST_ORDER = re.compile(r"발주처")
_CUST_PROSE_SIGNAL = re.compile(
    r"설계사[^.\n]{0,100}?방카슈랑스|"
    r"미국\s*내[^.\n]{0,100}?주요\s*고객으로|"
    r"\d[\d,]*\s*여?\s*고객사[^.\n]{0,100}?\d[\d,]*\s*여?\s*프로젝트|"
    r"당사는[^.\n]{0,180}?(?:삼성전자|SK하이닉스|현대자동차)[^.\n]{0,120}?프로젝트"
)


# 구조 앵커가 끝내 해당 필드를 회수하지 못했을 때만 쓰는 저신뢰 보조 문맥.
# 이 경로의 고정 창은 SUCCESS 판정·hint 산출에 절대 사용하지 않는다.
_CANDIDATE_FIELD_HEADS = {
    "sites": _SITE_HEAD + _SITE_PRODUCTION_HEAD + _SITE_REFERENCE_HEAD,
    "utilization": _UTIL_HEAD + _UTIL_PRODUCTION_PARENT_HEAD,
    "rnd": _RND_HEAD,
    "backlog": _BL_HEAD,
    "customers": _CUST_HEAD + _CUST_FIN_HEAD + _CUST_BROAD_HEAD + _BL_HEAD,
}


def render_candidate_context(field: str, html: str, context_chars: int,
                             region_index: BizRegionIndex | None = None) -> dict | None:
    """Return a fixed-window, low-confidence context candidate for one standard field.

    This is an explicit caller-requested recovery aid after strict extraction returns
    NOT_COLLECTED. It deliberately bypasses field content gates, so callers must not
    treat it as an authoritative field result.
    """
    patterns = _CANDIDATE_FIELD_HEADS.get(field)
    if not patterns or not html:
        return None
    index = region_index or build_region_index(html)
    candidates: list[tuple[int, _Heading, str]] = []
    for priority, pattern in enumerate(patterns):
        for heading in index.headings:
            match = re.search(pattern, heading.text)
            if not match or _FINANCIAL_CHAPTER_RE.search(_chapter_for(index, heading)):
                continue
            candidates.append(
                (priority, heading, _matched_heading_label(heading.text, match.start()))
            )
    if not candidates:
        return None
    _, heading, label = min(candidates, key=lambda item: (item[0], -item[1].level, item[1].start))
    end = min(len(html), heading.start + context_chars)
    markdown = _render_html_region_md(html, max(0, heading.start - 40), end)
    if not markdown:
        return None
    return {
        "status": "LOW_CONFIDENCE",
        "field": field,
        "anchor": label,
        "selection_method": "fixed_window_heading",
        "context_chars": context_chars,
        "warning": "인접 소절이 포함될 수 있는 보조 문맥입니다. 공식 추출 결과나 힌트로 사용하지 마세요.",
        "markdown": markdown,
    }


def _util_impl(biz_text, html, region_index=None):
    r = _field(biz_text, html, _UTIL_HEAD, _UTIL_NA, content_re=_C_UTIL, max_chars=18000,
               region_index=region_index, exclude_chapter_re=_FINANCIAL_CHAPTER_RE)
    production_na = _UTIL_PRODUCTION_EXPLICIT_NA.search(
        r.get("markdown", "") or (biz_text or "")
    )
    if production_na and (
        r.get("extraction_status") == "NOT_COLLECTED"
        or not _md_has_data_rows(r.get("markdown", ""), 1)
    ):
        r = {
            "status": "NOT_APPLICABLE",
            "extraction_status": "NOT_APPLICABLE",
            "na_reason": _strip_tags(production_na.group(0))[:60],
        }
    # 일부 DART 문서는 `2. 생산설비` 한 문단 안에 번호 없는 생산능력·실적을 모두 붙인다.
    # 이 넓은 부모 앵커는 구체적인 가동률/생산 헤딩이 전혀 없을 때만 보조 경로로 쓴다.
    if r.get("extraction_status") == "NOT_COLLECTED":
        r = _field(
            biz_text, html, _UTIL_PRODUCTION_PARENT_HEAD, _UTIL_NA,
            content_re=_C_UTIL, max_chars=18000, region_index=region_index,
            exclude_chapter_re=_FINANCIAL_CHAPTER_RE,
        )
    pv = [match.group(1) for match in _UTIL_PCT.finditer(r.get("markdown", ""))]
    if pv:
        r["pct_hint"] = pv[:6]
        r["comparable"] = False
        r["hints"] = [{
            "name": "utilization_pct",
            "values": pv[:6],
            "unit": "%",
            "authoritative": False,
            "comparable": False,
            "source": "returned_markdown",
        }]
    return r


def extract_sites(biz_text, html, region_index=None):
    r = _field(biz_text, html, _SITE_HEAD, _SITE_NA, content_re=_C_SITE,
               region_index=region_index, exclude_chapter_re=_FINANCIAL_CHAPTER_RE)
    if r.get("extraction_status") == "NOT_COLLECTED":
        r = _field(
            biz_text, html, _SITE_PRODUCTION_HEAD, _SITE_NA,
            content_re=_C_SITE_LOCATION_STRONG, region_index=region_index,
            exclude_chapter_re=_FINANCIAL_CHAPTER_RE,
        )
    if r.get("extraction_status") == "NOT_COLLECTED":
        r = _field(
            biz_text, html, _SITE_REFERENCE_HEAD, _SITE_NA,
            content_re=_C_SITE_REFERENCE, region_index=region_index,
            exclude_chapter_re=_FINANCIAL_CHAPTER_RE,
        )
    if r.get("extraction_status") == "NOT_COLLECTED":
        r = _signal_paragraph_field(html, _SITE_PROSE_SIGNAL, "signal_paragraph") or r
    return r

def extract_rnd(biz_text, html, region_index=None):
    r = _field(biz_text, html, _RND_HEAD, _RND_NA, content_re=_C_RND, max_chars=24000,
               region_index=region_index, exclude_chapter_re=_FINANCIAL_CHAPTER_RE)
    if r.get("extraction_status") == "NOT_COLLECTED":
        r = _signal_paragraph_field(html, _RND_PROSE_SIGNAL, "signal_paragraph") or r
    m = _RND_RATIO.search(r.get("markdown", ""))
    if m:
        r["ratio_to_sales_pct_hint"] = m.group(1)
        r["hints"] = [{
            "name": "rnd_ratio_to_sales_pct",
            "values": [m.group(1)],
            "unit": "%",
            "authoritative": False,
            "comparable": False,
            "source": "returned_markdown",
        }]
    return r

def extract_backlog(biz_text, html, region_index=None):
    r = _field(biz_text, html, _BL_HEAD, _BL_NA, content_re=_C_BL,
               region_index=region_index, exclude_chapter_re=_FINANCIAL_CHAPTER_RE)
    markdown = r.get("markdown", "")
    explicit_na = _BL_NA.search(markdown or (biz_text or ""))
    if explicit_na and not _BL_ACTUAL_SIGNAL.search(markdown):
        r = {
            "status": "NOT_APPLICABLE",
            "extraction_status": "NOT_APPLICABLE",
            "na_reason": _strip_tags(explicit_na.group(0))[:60],
        }
    if r.get("extraction_status") == "NOT_COLLECTED":
        r = _signal_paragraph_field(html, _BL_PROSE_SIGNAL, "signal_paragraph") or r
    return r

def extract_customers(biz_text, html, region_index=None):
    r = _field(
        biz_text, html, _CUST_HEAD, _CUST_NA, content_re=_C_CUST,
        region_index=region_index, exclude_chapter_re=_FINANCIAL_CHAPTER_RE,
    )
    for heads, content_re in (
        (_CUST_FIN_HEAD, _C_CUST_FIN),
        (_CUST_BROAD_HEAD, _C_CUST_STRONG),
        (_BL_HEAD, _C_CUST_ORDER),
    ):
        if r.get("extraction_status") != "NOT_COLLECTED":
            break
        r = _field(
            biz_text, html, heads, _CUST_NA, content_re=content_re,
            region_index=region_index, exclude_chapter_re=_FINANCIAL_CHAPTER_RE,
        )
    if r.get("extraction_status") == "NOT_COLLECTED":
        r = _signal_paragraph_field(html, _CUST_PROSE_SIGNAL, "signal_paragraph") or r
    return r


# ── 원재료·제품가격: 사업부문별 N/A가 다른 실측 소절을 덮지 않도록 소절 단위 조합 ──
_RAW_MATERIALS_HEAD = [
    r"(?:주요|회사별)\s*원\s*(?:재료|자재)(?:\s*\([^)]{0,40}\))?"
    r"(?:\s*(?:등)?의?)?\s*(?:현황|매입\s*현황|에\s*관한\s*사항)",
    r"주요\s*원\s*(?:재료|자재)(?:\s*\([^)]{0,40}\))?$",
    r"원\s*(?:재료|자재)\s*매입\s*(?:현황|실적)",
]
_RAW_INPUT_PRICE_HEAD = [
    r"원\s*(?:재료|자재)\s*(?:등)?의?\s*가격\s*(?:변동\s*)?(?:추이|현황)",
]
_PRODUCT_PRICING_HEAD = [
    r"주요\s*(?:제품|상품|서비스)(?:\s*등)?\s*(?:의\s*)?가격\s*(?:변동\s*)?(?:추이|현황)",
]
_C_RAW_MATERIALS = re.compile(
    r"매입(?:액|처|비중|실적)|구입(?:처|가격)|공급(?:사|처|업체)|구체적\s*용도|"
    r"원\s*(?:재료|자재)\s*가격|수입|품목"
)
_C_RAW_INPUT_PRICE = re.compile(r"가격|단가|원\s*(?:재료|자재)|품목")
_C_PRODUCT_PRICING = re.compile(
    r"판매\s*가격|평균\s*판매|단가|가격\s*변동\s*원인|가격\s*(?:상승|하락|변동|수준|추이)"
)
_RAW_MATERIALS_NA = re.compile(
    r"원\s*(?:재료|자재)[\s\S]{0,160}?"
    r"(?:해당\s*사항\s*(?:이)?\s*없|기재(?:를)?\s*생략|기재하지\s*않|"
    r"발생하지\s*않|필요하지\s*않|존재하지\s*않)"
)
_PRODUCT_PRICING_NA = re.compile(
    r"(?:가격\s*(?:변동\s*)?(?:추이|현황))?[\s\S]{0,160}?"
    r"(?:기재(?:는|를)?\s*생략|기재하지\s*않|산출.{0,25}?(?:어렵|곤란|부적합))"
)
_PRODUCT_PRICE_VALUE = re.compile(
    r"\d[\d,.]*\s*(?:원|달러|US\$|\$|%|/W|/kg|/KG|천원|백만원)|"
    r"(?:202[0-9]|전년|당기)[^\n|]{0,30}\d"
)


def _compose_pricing_field(biz_text: str, html: str, components: list[tuple[str, str, list[str], re.Pattern]],
                           na_re, region_index: BizRegionIndex | None = None) -> dict:
    """Combine at most one bounded region per source component without cross-component N/A poisoning."""
    found: list[tuple[str, str, dict]] = []
    for key, label, heads, content_re in components:
        regions = _render_biz_subsection_regions(
            html, heads, need_rows=0, content_re=content_re, region_index=region_index,
            exclude_chapter_re=_FINANCIAL_CHAPTER_RE,
        )
        if regions:
            found.append((key, label, regions[0]))
    if not found:
        na = na_re.search(biz_text) if (na_re and biz_text) else None
        return {
            "status": "NOT_APPLICABLE",
            "extraction_status": "NOT_APPLICABLE" if na else "NOT_COLLECTED",
            "na_reason": _strip_tags(na.group(0))[:60] if na else "해당 소절 미검출",
        }

    # 가격을 명시적으로 생략한 소절만 있고 숫자/단위 가격 신호도 없을 때만 N/A다.
    usable = found
    if len(components) == 1 and na_re:
        usable = [item for item in found if not (
            na_re.search(item[2]["markdown"]) and not _PRODUCT_PRICE_VALUE.search(item[2]["markdown"])
        )]
        if not usable:
            na = na_re.search(found[0][2]["markdown"])
            return {
                "status": "NOT_APPLICABLE",
                "extraction_status": "NOT_APPLICABLE",
                "na_reason": _strip_tags(na.group(0))[:60],
            }

    return {
        "status": "MARKDOWN",
        "extraction_status": "SUCCESS",
        "source": "heading",
        "section_source": {
            "matched_headings": [item[2]["heading"] for item in usable if item[2].get("heading")],
            "chapters": list(dict.fromkeys(item[2]["chapter"] for item in usable)),
            "selection_method": "heading",
            "boundary_methods": list(dict.fromkeys(item[2]["boundary"] for item in usable)),
            "components": [item[0] for item in usable],
        },
        "markdown": "\n\n———\n\n".join(
            f"#### {label}\n\n{region['markdown']}" for _, label, region in usable
        ),
    }


def extract_raw_materials(biz_text, html, region_index=None):
    return _compose_pricing_field(
        biz_text, html,
        [
            ("materials", "원재료 구성·매입", _RAW_MATERIALS_HEAD, _C_RAW_MATERIALS),
            ("input_price", "원재료 가격 추이", _RAW_INPUT_PRICE_HEAD, _C_RAW_INPUT_PRICE),
        ],
        _RAW_MATERIALS_NA, region_index,
    )


def extract_product_pricing(biz_text, html, region_index=None):
    return _compose_pricing_field(
        biz_text, html,
        [("product_pricing", "제품·서비스 가격 추이", _PRODUCT_PRICING_HEAD, _C_PRODUCT_PRICING)],
        _PRODUCT_PRICING_NA, region_index,
    )


# ═══════════ D-트랙: 금융·REIT 필드 (헤딩앵커 + 내용시그니처 폴백) ═══════════
# 사용자 지적(260718): 키워드 헤딩만이면 특이 헤딩에 무용지물 → 헤딩 미스 시 '데이터 시그니처'로
# 표를 찾아 렌더(헤딩라벨보다 안정적). segments의 table-scan 방식을 필드 일반화.

def _find_by_signature(html, signature_re, window=18000):
    """헤딩 못 찾을 때 폴백: signature 든 <table>(없으면 프로즈) 위치를 찾아 그 앞부터 렌더."""
    if not html:
        return None
    from open_proxy_mcp.services.segment_candidates import _TABLE_RE
    for m in _TABLE_RE.finditer(html):
        if signature_re.search(m.group(0)) and not _is_roster(m.group(0)):
            md = _render_html_region_md(html, max(0, m.start() - 1500), m.start() + window)
            if md and _md_has_data_rows(md, 2) and len(md) > 300:   # 폴백은 stricter(빈/tiny 렌더 배제)
                return md
    tm = signature_re.search(html)
    if tm:
        md = _render_html_region_md(html, max(0, tm.start() - 1200), tm.start() + window)
        if md and len(md) > 300:
            return md
    return None


def _field2(biz_text, html, head_patterns, content_re, signature_re, na_re, max_chars=18000,
            region_index=None):
    """헤딩앵커(content-gate) → 실패 시 내용시그니처 폴백 → N/A. source로 어느 경로인지 표기."""
    regions = _render_biz_subsection_regions(
        html, head_patterns, max_chars=max_chars, content_re=content_re, region_index=region_index,
    )
    if regions:
        markdown = "\n\n———\n\n".join(region["markdown"] for region in regions)
        explicit_na = na_re.search(markdown) if na_re else None
        if explicit_na and not _md_has_data_rows(markdown, 1):
            return {"status": "NOT_APPLICABLE", "extraction_status": "NOT_APPLICABLE",
                    "na_reason": _strip_tags(explicit_na.group(0))[:60]}
        return {
            "status": "MARKDOWN",
            "extraction_status": "SUCCESS",
            "source": "heading",
            "section_source": {
                "matched_headings": [region["heading"] for region in regions if region.get("heading")],
                "chapters": list(dict.fromkeys(region["chapter"] for region in regions)),
                "selection_method": "heading",
                "boundary_methods": list(dict.fromkeys(region["boundary"] for region in regions)),
            },
            "markdown": markdown,
        }
    md = _find_by_signature(html, signature_re, max_chars)
    if md:
        return {"status": "MARKDOWN", "extraction_status": "SUCCESS",
                "source": "signature", "section_source": {"selection_method": "signature",
                "boundary_methods": ["fixed_window_fallback"]}, "markdown": md}
    na = na_re.search(biz_text) if (na_re and biz_text) else None
    return {"status": "NOT_APPLICABLE",
            "extraction_status": "NOT_APPLICABLE" if na else "NOT_COLLECTED",
            "na_reason": (_strip_tags(na.group(0))[:60] if na else "해당 소절 미검출")}


# 금융 영업현황(영업부문별 재무정보=금융판 segments·영업개황·영업실적)
_FOPS_HEAD = [r"영업의?\s*현황", r"영업\s*개황", r"영업부문별\s*(?:재무정보|비중|현황)",
              r"영업의?\s*종류"]
# KSIC 게이트가 비금융을 이미 막으므로(오케스트레이터), content-gate는 서브타입 용어를 넓게 잡아
# '영업의 현황 섹션인지'만 확인(은행 순이자·증권 영업순수익·운용사 관리보수·보험 보험영업 다 커버).
_C_FOPS = re.compile(r"순이자|보험영업|영업순수익|운용보수|관리보수|성과보수|투자조합|운용조합|수탁고|"
                     r"수입보험료|영업부문별|예대율|지급여력|위탁매매|집합투자|영업수익|영업이익|약정")
_SIG_FOPS = re.compile(r"순이자손익|영업부문별\s*재무|은행\s*부문|보험영업손익|영업순수익|관리보수.{0,10}성과보수")
# 금융 재무건전성(지급여력·RBC·BIS·자본적정성)
_FSND_HEAD = [r"재무\s*건전성", r"지급\s*여력", r"자본\s*적정성"]
# KSIC 게이트가 비금융 배제하므로 '재무건전성 섹션인지'만 확인(서브타입 지표 넓게 + VC 자기자본류).
_C_FSND = re.compile(r"지급여력|K-ICS|BIS|RBC|순자본|영업용순자본|고정이하|연체|책임준비금|"
                     r"위험가중|건전성|자기자본|재무구조")
_SIG_FSND = re.compile(r"지급여력비율|K-ICS|RBC\s*비율|BIS\s*비율|고정이하여신|영업용순자본비율")
# REIT 투자부동산(투자부동산 내역·투자자산 개요)
# REIT마다 서식 상이(SK리츠=투자부동산 내역 / 롯데리츠=임대조건+프로즈). KSIC 68게이트라 넓게.
_IPROP_HEAD = [r"투자\s*부동산의?\s*(?:내역|현황)", r"투자\s*자산\s*개요", r"투자\s*대상\s*(?:자산|부동산)",
               r"부동산\s*(?:보유|투자)\s*현황", r"임대\s*조건", r"임대\s*현황", r"보유\s*부동산",
               r"주요\s*(?:자산|부동산)\s*현황"]
_C_IPROP = re.compile(r"임대율|공실|임대면적|임대\s*형태|투자부동산|임대료|임차인|책임임대차|연면적|임대\s*조건")
# 시그니처: REIT 특화(단순 '투자부동산' 계정언급 오발 방지 — 임대료·임차인·공실·책임임대차 동반)
_SIG_IPROP = re.compile(r"임대율|공실률|임대\s*형태|투자부동산의?\s*내역|책임임대차|"
                        r"임차인.{0,40}임대료|임대료.{0,20}배당")
_IPROP_NA = re.compile(r"투자부동산[^\n]{0,20}?(해당\s*사항\s*(?:이)?\s*없|없습니다)")
# 지주형 REIT(명목회사)가 표준(제조)폼에 부동산을 프로즈로 싣는 케이스(제이알글로벌·해외리츠 등):
# 부동산이 '2.주요 제품 및 서비스 → 영업개황'에 서술형(임대료·WALE·임차율)로 들어가 전용헤딩·표
# 시그니처가 다 놓친다. 전용경로 실패 시에만 표준폼 헤딩을 시도하되, content-gate를 강화
# (임대료/임대차/임차 + 부동산/투자대상/임대 동반)해 비REIT 오섹션(보험 영업개황 등)을 차단.
_IPROP_HEAD_STD = [r"주요\s*제품\s*및\s*서비스", r"영업\s*개황", r"회사의?\s*현황"]
_C_IPROP_PROSE = re.compile(r"(?:임대료|임대차|임차)[^\n]{0,300}"
                            r"(?:부동산|임차|임대|투자대상|잔여임대|WALE|공실|연면적|기초자산)")


def extract_financial_ops(biz_text, html, region_index=None):
    return _field2(biz_text, html, _FOPS_HEAD, _C_FOPS, _SIG_FOPS, None, region_index=region_index)

def extract_financial_soundness(biz_text, html, region_index=None):
    return _field2(biz_text, html, _FSND_HEAD, _C_FSND, _SIG_FSND, None, region_index=region_index)

def extract_investment_property(biz_text, html, region_index=None):
    r = _field2(biz_text, html, _IPROP_HEAD, _C_IPROP, _SIG_IPROP, _IPROP_NA,
                region_index=region_index)
    if r.get("status") == "MARKDOWN":
        return r
    # 지주형 REIT 표준폼 프로즈 폴백(전용헤딩·시그니처 실패 시에만 — 작동하는 REIT엔 영향 없음)
    regions = _render_biz_subsection_regions(
        html, _IPROP_HEAD_STD, content_re=_C_IPROP_PROSE, region_index=region_index,
    )
    md = "\n\n———\n\n".join(region["markdown"] for region in regions) if regions else None
    if md:
        return {"status": "MARKDOWN", "extraction_status": "SUCCESS",
                "source": "reit_prose", "section_source": {"selection_method": "heading",
                "matched_headings": [region["heading"] for region in regions if region.get("heading")],
                "boundary_methods": list(dict.fromkeys(region["boundary"] for region in regions))},
                "markdown": md}
    na = _IPROP_NA.search(biz_text) if biz_text else None
    return {"status": "NOT_APPLICABLE", "extraction_status": "NOT_APPLICABLE" if na else "NOT_COLLECTED",
            "na_reason": (_strip_tags(na.group(0))[:60] if na else "해당 소절 미검출")}
