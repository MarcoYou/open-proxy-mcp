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
from dataclasses import dataclass, field as dataclass_field

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
    # L-소절 구조 앵커 지도 {("L1"|"L2", n): (start, end)} — 사업의 내용 소절 경계.
    # L1=제조서비스업 서식 트랙 / L2=금융업 서식 트랙 (겸업사는 병존). 코드+제목 이중검증
    # 통과분만 담기며, 없으면 빈 dict(구형 문서 등) → 소비처는 기존 전체 탐색 그대로.
    l_spans: dict = dataclass_field(default_factory=dict)


# <TITLE AASSOCNOTE="..."> 구조 앵커. L-0-2-N-L1/L2 = II.사업의 내용 소절 코드.
_AASSOC_TITLE_SCAN_RE = re.compile(
    r'<TITLE\b[^>]*\bAASSOCNOTE="([A-Za-z0-9_-]+)"[^>]*>(.*?)</TITLE>',
    re.IGNORECASE | re.DOTALL,
)
_L_SUBSEC_CODE_RE = re.compile(r"^L-0-2-(\d{1,2})-L([12])$")
# 코드는 목차 '위치' 좌표라 서식 개정 시 의미가 이동할 수 있다 → 제목 키워드로 이중검증,
# 불일치 소절은 지도에서 제외(기존 텍스트 탐색으로 자연 폴백).
_L_SUBSEC_EXPECT = {
    ("L1", 1): re.compile(r"사업의\s*개요"),
    ("L1", 2): re.compile(r"제품|서비스"),
    ("L1", 3): re.compile(r"원재료|생산\s*설비"),
    ("L1", 4): re.compile(r"매출|수주"),
    ("L1", 5): re.compile(r"위험\s*관리|파생"),
    ("L1", 6): re.compile(r"연구\s*개발|계약"),
    ("L1", 7): re.compile(r"기타"),
    ("L2", 1): re.compile(r"사업의\s*개요"),
    ("L2", 2): re.compile(r"영업의?\s*현황"),
    ("L2", 3): re.compile(r"파생"),
    ("L2", 4): re.compile(r"영업\s*설비"),
    ("L2", 5): re.compile(r"재무\s*건전성|기타"),
}
# 필드 → 표준 소재 소절. 스팬 안에서 먼저 찾고, 없으면 기존 전체 탐색(단조 안전 폴백).
_FIELD_SUBSECTIONS = {
    "sites": (("L1", 3), ("L2", 4)),
    "utilization": (("L1", 3),),
    "raw_materials": (("L1", 3),),
    "backlog": (("L1", 4),),
    "customers": (("L1", 4), ("L1", 2)),
    "rnd": (("L1", 6),),
    "product_pricing": (("L1", 2), ("L1", 3)),
    # II-2-가 「주요 제품 등의 현황」 — 회계 부문(III 주석)이 아니라 공시서식 기재사항.
    # 실측 286건: II-2-가 83.2% vs III §32 제품별 7.3% — 제품 구성의 사실상 유일 경로다.
    "revenue_mix_form": (("L1", 2),),
    # II-6 「가. 주요계약」 — rnd 와 같은 소절인데 추출이 연구개발 하위표제로만 좁혀져
    # 절반이 잘려 나갔다(실측: 녹십자 라이센스인/아웃, 세방전지 기술도입, 대원화성 광개발 옵션).
    "key_contracts": (("L1", 6),),
    "financial_ops": (("L2", 2),),
    "financial_soundness": (("L2", 5),),
}


def _build_l_subsection_spans(html: str) -> dict:
    """AASSOCNOTE L-소절 코드로 사업의 내용 소절 경계 지도 생성.

    끝 경계 = 다음 AASSOCNOTE 앵커(L/D 무관). 방어: 같은 코드 중복 출현(미지 서식 변형) 또는
    제목 키워드 불일치(세대 간 의미 이동) 소절은 버린다 — 소비처가 기존 탐색으로 폴백.
    """
    hits = [(m.start(), m.group(1), _clean_heading_text(m.group(2)))
            for m in _AASSOC_TITLE_SCAN_RE.finditer(html)]
    code_counts: dict[str, int] = {}
    for _, code, _t in hits:
        code_counts[code] = code_counts.get(code, 0) + 1
    spans: dict = {}
    for i, (start, code, title) in enumerate(hits):
        m = _L_SUBSEC_CODE_RE.match(code)
        if not m or code_counts[code] != 1:
            continue
        key = (f"L{m.group(2)}", int(m.group(1)))
        expect = _L_SUBSEC_EXPECT.get(key)
        if expect is None or not expect.search(title):
            continue
        end = hits[i + 1][0] if i + 1 < len(hits) else len(html)
        spans[key] = (start, end)
    return spans


def field_subsection_spans(field: str, index: "BizRegionIndex") -> tuple[tuple[int, int], ...]:
    """필드의 표준 소재 소절 스팬들(문서에 존재하는 것만)."""
    return tuple(index.l_spans[k] for k in _FIELD_SUBSECTIONS.get(field, ()) if k in index.l_spans)


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
    l_spans = _build_l_subsection_spans(html)
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
    return BizRegionIndex(tuple(deduped), l_spans)


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
                        content_re=None, need_rows: int = 1, exclude_chapter_re=None,
                        spans: tuple[tuple[int, int], ...] | None = None) -> list[dict]:
    candidates: list[tuple[int, _Heading, str]] = []
    for priority, keyword in enumerate(kw_patterns):
        for heading in index.headings:
            if spans and not any(a <= heading.start < b for a, b in spans):
                continue        # L-소절 게이트: 표준 소재 소절 안의 헤딩만
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
                                   exclude_chapter_re=None, field: str | None = None) -> list[dict]:
    if not html:
        return []
    index = region_index or build_region_index(html)
    # 1-pass: L-소절 게이트 — 필드의 표준 소재 소절 안에서만 탐색(다른 장·타 소절·embedded
    # 종속사 보고서의 유사 제목 오탐 차단). 소절 지도가 없거나(구형) 소절 안에서 못 찾으면
    # 2-pass 전체 탐색(기존 동작 그대로) → 단조 안전.
    spans = field_subsection_spans(field, index) if field else ()
    if spans:
        regions = _structural_regions(
            html, kw_patterns, index, content_re=content_re, need_rows=need_rows,
            exclude_chapter_re=exclude_chapter_re, spans=spans,
        )
        if regions:
            for region in regions:
                region["l_gated"] = True
            return regions
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


def _md_has_text_rows(md: str, need: int = 2) -> bool:
    """숫자 없는 표도 실질 내용이다 — 계약 상대방·아티스트 명단처럼 이름만 있는 표.

    `_md_has_data_rows`는 숫자행만 세므로, 「가. 주요 계약: 해당사항 없습니다」 뒤에 붙은
    「나. 주요 아티스트 전속계약」 표(회사명·그룹·아티스트)가 빈 표로 취급돼 구간 전체가
    해당없음으로 접혔다(하이브 실측 4,160자 유실).
    """
    n = 0
    for ln in md.splitlines():
        if not ln.startswith("|") or re.fullmatch(r"[|\s:-]+", ln):
            continue
        if sum(1 for c in ln.strip("|").split("|") if c.strip()) >= 2:
            n += 1
            if n >= need:
                return True
    return False


def _field(biz_text: str, html: str, head_patterns: list[str], na_re, content_re=None,
           max_chars: int = 20000, region_index: BizRegionIndex | None = None,
           exclude_chapter_re=None, field: str | None = None) -> dict:
    """markdown-primary: 소절 마크다운(content-gate 통과) 있으면 MARKDOWN, 없고 NA어휘면 N/A, 아니면 미검출."""
    regions = _render_biz_subsection_regions(
        html, head_patterns, max_chars=max_chars, content_re=content_re, region_index=region_index,
        exclude_chapter_re=exclude_chapter_re, field=field,
    )
    if exclude_chapter_re:
        regions = [region for region in regions
                   if not exclude_chapter_re.search(region["chapter"])]
    if regions:
        markdown = "\n\n———\n\n".join(region["markdown"] for region in regions)
        explicit_na = na_re.search(markdown) if na_re else None
        # 「해당사항 없습니다」가 구간 앞머리에 있어도, 뒤에 실제 표가 있으면 그건 원문이 이긴다.
        if explicit_na and not _md_has_data_rows(markdown, 1) and not _md_has_text_rows(markdown, 3):
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
                "selection_method": ("heading_l_gate" if all(r.get("l_gated") for r in regions)
                                     else "heading"),
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


# III. 재무 장이 시작하는 자리 — 문단 폴백이 주석 표를 긁지 않도록 경계를 둔다.
_FIN_CHAPTER_POS_RE = re.compile(r"<TITLE[^>]*>\s*(?:III|Ⅲ)\s*\.\s*재무", re.I)
_PROSE_TABLE_GAP = 900          # 문단 끝에서 이 안에 표가 오면 그 문단이 이끄는 표로 본다
_PROSE_TABLE_JOIN = 200         # 이미 표를 잡은 뒤 바짝 붙어 오는 표(단위 선언 등)는 이어 붙인다


def _prose_anchor_regions(html: str, head_patterns: list[str], content_re,
                          max_regions: int = 1) -> list[tuple[str, str]]:
    """소절 제목이 **문단 안에 녹아 있을 때**의 마지막 회수.

    회사가 「…추정됩니다.주요 원재료의 가격변동추이는 다음과 같습니다.」처럼 제목을 문장에
    넣어 버리면 HTML 헤딩 요소가 없어 `build_region_index` 에 안 잡힌다(260803 실측 19건 —
    II장엔 XBRL 구조 코드가 0개라 코드로 짚는 길도 없다).

    임베디드 헤딩 lookahead 를 넓히는 방법을 먼저 재봤으나 회수 4에 손실 2(그중 6,981자)라
    버렸다 — 색인을 건드리면 다른 필드의 구간 경계가 흔들린다. 이 경로는 **정규 경로가
    아무것도 못 찾았을 때만** 돌아서, 있는 값을 밀어낼 수 없다.
    """
    fin = _FIN_CHAPTER_POS_RE.search(html or "")
    limit = fin.start() if fin else len(html or "")
    out: list[tuple[str, str]] = []
    for pm in _P_BLOCK_RE.finditer(html or ""):
        if pm.start() >= limit:                       # III. 재무 주석은 이 필드의 자리가 아니다
            break
        text = _clean_heading_text(pm.group("body"))
        hit = next((m for pat in head_patterns for m in [re.search(pat, text)] if m), None)
        if not hit:
            continue
        # 헤딩이 없으니 **문장에서 잡은 그 문구**를 원문 위치로 준다 — 회사마다 표현이
        # 달라(「원재료의 가격 변동 추이」·「주요 원재료의 가격변동추이는」) 이게 없으면
        # 읽는 쪽이 원문에서 같은 자리를 못 찾는다.
        label = text[hit.start():hit.end() + 12].strip()[:60]
        # 그 문단이 이끄는 표까지 담는다. **잇달아 오는 표는 함께 담아야** 한다 —
        # DART 는 「(단위 : 원)」을 별도 표로 렌더해서, 첫 표만 집으면 단위 줄에서 끊긴다.
        end = pm.end()
        cursor, gap = pm.end(), _PROSE_TABLE_GAP
        while True:
            tm = _TABLE_RE.search(html, cursor)
            if not tm or tm.start() - cursor > gap:
                break
            end = cursor = tm.end()
            gap = _PROSE_TABLE_JOIN                    # 이후로는 바짝 붙은 표만 이어 붙인다
        rendered = _render_html_region_md(html, pm.start(), end)
        if not rendered or (content_re and not content_re.search(rendered)):
            continue
        if all(rendered != r for r, _ in out):
            out.append((rendered, label))
        if len(out) >= max_regions:
            break
    return out


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
# 회사가 설비를 **소재지가 아니라 장부가 표**로 공시하는 경우. `_C_SITE` 는 소재지·㎡·지역명을
# 요구해서 이런 표를 통째로 떨어뜨렸다(실측 82건 — 도시가스 공급배관, 유형자산 롤포워드 등).
# 다만 이 어휘로 `_C_SITE` 자체를 넓히면 자식 헤딩이 더 실한 부모 구간을 밀어내
# 기존 값이 3,204~10,749자 줄었다(occupied 겹침 배제 + 우선순위 탓). 그래서 **정규 경로가
# 값을 못 낸 뒤에만** 도는 마지막 단계로 둔다 — 구조상 있는 값을 밀어낼 수 없다.
_C_SITE_ASSET_TABLE = re.compile(r"기계장치|구축물|건설중인자산|장부(?:가액|금액)|공급배관|정압기")
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
               region_index=region_index, exclude_chapter_re=_FINANCIAL_CHAPTER_RE,
               field="utilization")
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
            exclude_chapter_re=_FINANCIAL_CHAPTER_RE, field="utilization",
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
               region_index=region_index, exclude_chapter_re=_FINANCIAL_CHAPTER_RE,
               field="sites")
    if r.get("extraction_status") == "NOT_COLLECTED":
        r = _field(
            biz_text, html, _SITE_PRODUCTION_HEAD, _SITE_NA,
            content_re=_C_SITE_LOCATION_STRONG, region_index=region_index,
            exclude_chapter_re=_FINANCIAL_CHAPTER_RE, field="sites",
        )
    if r.get("extraction_status") == "NOT_COLLECTED":
        r = _field(
            biz_text, html, _SITE_REFERENCE_HEAD, _SITE_NA,
            content_re=_C_SITE_REFERENCE, region_index=region_index,
            exclude_chapter_re=_FINANCIAL_CHAPTER_RE, field="sites",
        )
    if r.get("extraction_status") == "NOT_COLLECTED":
        r = _signal_paragraph_field(html, _SITE_PROSE_SIGNAL, "signal_paragraph") or r
    if r.get("status") != "MARKDOWN":
        # 「제조업체가 아니므로 생산설비는 없으나, 영업활동을 위한 자산의 내역은 아래와 같습니다」
        # 처럼 부재를 밝히고도 표를 싣는 회사가 많다(회수 82건 중 67건이 그 경우). 원문을
        # 그대로 돌려주므로 그 문장도 함께 실려, 읽는 쪽이 무엇인지 보고 판단할 수 있다.
        asset = _field(
            biz_text, html, _SITE_HEAD, _SITE_NA, content_re=_C_SITE_ASSET_TABLE,
            region_index=region_index, exclude_chapter_re=_FINANCIAL_CHAPTER_RE, field="sites",
        )
        if asset.get("status") == "MARKDOWN":
            r = asset
    return r

def extract_rnd(biz_text, html, region_index=None):
    r = _field(biz_text, html, _RND_HEAD, _RND_NA, content_re=_C_RND, max_chars=24000,
               region_index=region_index, exclude_chapter_re=_FINANCIAL_CHAPTER_RE,
               field="rnd")
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
               region_index=region_index, exclude_chapter_re=_FINANCIAL_CHAPTER_RE,
               field="backlog")
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
        field="customers",
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
            field="customers",
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
# II-2-가 「주요 제품 등의 현황」. '가격변동추이'(2-나)와 구분해야 한다 — 바로 옆 소절이다.
#   소절2는 문서가 스스로 선언한 구간이다. 그 안에서 표를 실제로 이고 있는 표제의 어휘는
#   회사마다 갈린다 — 「주요제품 (연결기준)」·「주요 제품, 서비스 등의 매출 현황」·「매출현황」·
#   「주요제품 소개」. 어휘를 좇지 말고 넓게 앵커한 뒤 content-gate(_C_REVENUE_MIX)로 거른다.
#   신풍제약 실측: 소절 표제와 표 표제가 형제 레벨이라 구간이 52자에서 끊겨 gate 탈락했다.
_NOT_PRICE_TREND = r"(?!.{0,14}(?:가격|단가|판매\s*가)\s*(?:변동|추이|현황))"
_REVENUE_MIX_HEAD = [
    r"주요\s*제품\s*등?의?\s*현황",
    r"주요\s*(?:제품|상품|서비스)\s*및\s*서비스",
    r"주요\s*제품\s*및\s*원재료",
    r"주요\s*(?:제품|상품|서비스|품목|매출)" + _NOT_PRICE_TREND,
    r"(?:제품|상품|서비스|사업\s*부문)\s*별?\s*매출\s*(?:현황|실적|구성|비중)",
    r"매출\s*(?:현황|실적|구성)" + _NOT_PRICE_TREND,
]
# 값이 실제로 매출 구성인지 — 금액·비율 신호. 「가. 주요공사 현황」(시공실적)·
# 「매입 현황」·기능 카탈로그를 걸러내려면 이 신호가 있어야 한다.
_C_REVENUE_MIX = re.compile(
    r"매\s*출\s*액|매출\s*비중|비\s*율|품\s*목|사\s*업\s*부\s*문|매출\s*유형|구체적\s*용도"
)
# II-6-가 「주요계약」 — 라이선스·기술도입·장기공급.
_KEY_CONTRACTS_HEAD = [
    r"주요\s*계약(?:\s*(?:등)?의?\s*(?:현황|내용))?",
    r"라이\s*[선센]\s*스\s*(?:아웃|인)",
    r"기술\s*(?:도입|이전|제휴)\s*계약",
    # 업종의 핵심 계약이 「가. 주요 계약: 해당사항 없습니다」 밑의 별도 표제에 실린다.
    # 하이브 실측: 「나. 주요 아티스트 전속계약」에 소속사·그룹·아티스트 전원이 있는데
    # 앞 표제의 '없습니다'만 읽고 해당없음으로 접혔다 — 재계약 리스크가 통째로 사라진다.
    r"전속\s*계약",
]
_C_KEY_CONTRACTS = re.compile(
    r"계약\s*(?:상대|체결|기간|금액|명)|상\s*대\s*처|License|라이[선센]스|기술도입|"
    r"대금\s*(?:수수|수금)|전속\s*계약|아\s*티\s*스\s*트"
)

# DART 표 셀은 자간을 벌려 렌더한다 — 「품 목」·「구 분」. 리터럴 `품목` 은 그걸 못 맞춰
# 실제 원재료 표(품 목 | 제54기 | …)를 통째로 떨어뜨렸다(260803 실측 8건, 손실 0).
_C_RAW_MATERIALS = re.compile(
    r"매입(?:액|처|비중|실적|유형)|구입(?:처|가격)|공급(?:사|처|업체)|구체적\s*용도|"
    r"원\s*(?:재료|자재)\s*가격|수입|품\s*목|평균\s*가|단\s*가"
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
                           na_re, region_index: BizRegionIndex | None = None,
                           field: str | None = None) -> dict:
    """Combine at most one bounded region per source component without cross-component N/A poisoning."""
    found: list[tuple[str, str, dict]] = []
    for key, label, heads, content_re in components:
        regions = _render_biz_subsection_regions(
            html, heads, need_rows=0, content_re=content_re, region_index=region_index,
            exclude_chapter_re=_FINANCIAL_CHAPTER_RE, field=field,
        )
        if regions:
            found.append((key, label, regions[0]))
    if not found:
        # 헤딩이 없는 형태(제목이 문장에 녹음)를 마지막으로 한 번 더 본다.
        for key, label, heads, content_re in components:
            prose = _prose_anchor_regions(html, heads, content_re)
            if prose:
                md, where = prose[0]
                found.append((key, label, {"markdown": md, "heading": where,
                                           "chapter": "II. 사업의 내용",
                                           "boundary": "prose_paragraph"}))
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
        _RAW_MATERIALS_NA, region_index, field="raw_materials",
    )


# ── II-2-가 매출구성 (공시서식 기재사항 — 회계 부문 아님) ──────────────────
# 이 표는 K-IFRS 1108 부문 정보가 아니다. 실측(회계 QA 52사·IR 62사):
#   · 합계가 연결과 안 맞음 33% (별도 기준인데 표시 없음 · 분모가 내부거래 포함 단순합계)
#   · 매출이 아예 아닌 표 (남광토건 시공실적 · 듀켐바이오 매입액 · 퓨쳐켐 경쟁사 점유율)
#   · 비율 합계가 100%가 아님 (태광산업 500% · 삼성전자 108.9% · 토니모리 98.67%)
# 그래서 정형값을 만들지 않고 **원문 마크다운 + 캡션**으로 넘긴다 — 읽는 쪽이 판단한다.
# 각주(※·(주n)·(*))는 이 표의 유일한 사용설명서라 자르지 않는다.
_REVENUE_MIX_NA = re.compile(
    r"주요\s*제품[\s\S]{0,120}?(?:해당\s*사항\s*(?:이)?\s*없|기재를?\s*생략)"
)
# 캡션이 이것들이면 매출 구성표가 아니다 — 값으로 내보내지 않는다.
_REVENUE_MIX_NOT_SALES = re.compile(
    r"주요\s*공사\s*현황|시공\s*실적|매입\s*(?:현황|에\s*관한)|생산\s*능력|가격\s*변동|"
    r"주요\s*기능|영업\s*시설|"
    # 은행·보험의 II-2는 매출구성이 아니라 취급 상품 카탈로그다(상품수·가입대상, 단위가 「개」).
    # 금융업 매출 구성은 financial_ops 가 따로 본다. 실측: KB금융지주 65,050자 통째 반환.
    r"상\s*품\s*수|가입\s*대상|주요상품의\s*내용"
)


# 원문은 넘기되 페이로드는 묶는다 — 잘랐다는 사실을 숨기지 않고 필드로 알린다.
# 상한은 하드코딩이 아니라 호출 파라미터다(`section_chars`): 정보가 모자라면 호출측 AI 가
# 올려서 다시 부를 수 있다. 기본 20,000 은 제조·서비스사 실측 상한(~15.6k 한전KPS)을 덮는다.
# 금융지주는 계열사마다 같은 항목을 실어 한 소절이 70k 를 넘는다(KB금융지주 재무건전성).
_BIZ_MD_CAP = 20_000


def _cap_markdown(r: dict, cap: int | None = None) -> dict:
    cap = _BIZ_MD_CAP if cap is None else int(cap)
    md = r.get("markdown") or ""
    if cap <= 0 or len(md) <= cap:
        return r
    return {**r, "markdown": md[:cap], "markdown_truncated": True,
            "markdown_full_chars": len(md),
            "truncation_note": (f"원문 {len(md):,}자 중 앞 {cap:,}자입니다. 뒤쪽이 필요하면 "
                                f"section_chars 를 올려 다시 조회하세요.")}


# ── 자가진단: 표가 스스로 밝힌 것만 검산한다 ────────────────────────────
# 값을 재계산해 '정답'을 내려는 게 아니다. 표에 적힌 합계행·비율·단위를 그대로 읽어
# 서로 안 맞으면 안 맞는다고 말한다. 애매하면 단정하지 않고 null 로 둔다.
_UNIT_RE = re.compile(r"단\s*위\s*[:：]\s*([^)\|\n]{1,24})")
_TOTAL_CELL_RE = re.compile(r"^(?:합\s*계|총\s*계|계|소\s*계|total)$", re.I)
_NUM_RE = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+\.\d+|-?\d{4,}")
_PCT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*%")


def _split_md_tables(md: str) -> list[list[str]]:
    """마크다운 구간을 표 단위로 쪼갠다 — 구분행(|---|)이 새 표의 시작 표시다.

    한 구간에 표가 여럿(당기/전기 별도 표, 부문별+품목별)인 경우가 절반이라, 통째로 합산하면
    항목합이 합계행의 2~3배가 되어 '불일치'만 쏟아진다. 표 하나씩 봐야 검산이 의미를 갖는다.
    """
    tables, cur, seen_sep = [], [], False
    for ln in md.splitlines():
        if not ln.startswith("|"):
            if cur:
                tables.append(cur)
            cur, seen_sep = [], False
            continue
        if re.fullmatch(r"[|\s:-]+", ln):
            if seen_sep and cur:          # 구분행이 또 나오면 다음 표가 시작된 것
                tables.append(cur)
                cur = []
            seen_sep = True
            continue
        cur.append(ln)
    if cur:
        tables.append(cur)
    return [t for t in tables if t]


def _check_one_table(rows: list[str]) -> dict:
    item_pcts, item_amts, total_amts = [], [], []
    for ln in rows:
        # 캡션행(단위·기준일)은 데이터가 아니다 — 「2025.12.31」이 금액으로 걷히면 합계가 흐려진다.
        if _UNIT_RE.search(ln) or "기준일" in ln:
            continue
        ln = re.sub(r"\d{4}\s*[.\-/년]\s*\d{1,2}\s*[.\-/월]?\s*\d{0,2}\s*일?", " ", ln)
        cells = [c.strip() for c in ln.strip("|").split("|")]
        is_total = any(_TOTAL_CELL_RE.fullmatch(re.sub(r"\s+", "", c)) for c in cells)
        pcts = [float(p) for p in _PCT_RE.findall(ln)]
        # 비율 괄호형 「2,835,195(69.5%)」 과 별도 열 「매출비중」 둘 다 같은 방식으로 걷힌다.
        nums = [float(n.replace(",", "")) for n in _NUM_RE.findall(_PCT_RE.sub(" ", ln))]
        amt = max(nums) if nums else None
        if is_total:
            if amt is not None:
                total_amts.append(amt)
        else:
            if pcts:
                item_pcts.append(pcts[-1])
            if amt is not None:
                item_amts.append(amt)
    out: dict = {"pct_sum": None, "pct_sum_is_100": None,
                 "declared_total": None, "item_sum": None, "tie_out": None}
    if item_pcts:
        s = round(sum(item_pcts), 1)
        out["pct_sum"], out["pct_sum_is_100"] = s, abs(s - 100.0) <= 1.0
    if total_amts and item_amts:
        declared, isum = max(total_amts), sum(item_amts)
        out["declared_total"], out["item_sum"] = declared, isum
        if declared > 0:
            gap = abs(declared - isum) / declared
            out["tie_out"] = ("항목합≈합계행 일치" if gap <= 0.02 else
                              f"항목합≠합계행 (차이 {gap * 100:.1f}%) — 라벨 없는 소계행이 "
                              f"섞여 있는 경우가 많습니다")
    elif item_amts:
        out["tie_out"] = "합계행 없음 — 항목합만 제시"
    return out


def _mix_self_check(md: str) -> dict:
    """표가 밝힌 단위·비율합·합계행을 읽어 자기정합성만 본다. 외부 대조는 호출측 몫.

    검산 대상은 '합계행을 가진 첫 표' — 없으면 첫 표. 구간에 표가 여럿이면 그 사실을 알린다.
    """
    tables = _split_md_tables(md)
    checks = [_check_one_table(t) for t in tables]
    chosen = next((c for c in checks if c.get("declared_total") is not None),
                  next((c for c in checks if c.get("item_sum") or c.get("pct_sum")), None))
    out: dict = dict(chosen or {"pct_sum": None, "pct_sum_is_100": None,
                                "declared_total": None, "item_sum": None, "tie_out": None})
    unit = _UNIT_RE.search(md)
    out["unit"] = re.sub(r"\s+", " ", unit.group(1)).strip(" ,") if unit else None
    out["tables_in_region"] = len(tables)
    if len(tables) > 1:
        out["scope_note"] = (f"구간에 표가 {len(tables)}개라 검산은 그중 한 표 기준입니다. "
                             "기간·연결/별도가 표마다 다를 수 있습니다.")
    return out


def extract_revenue_mix_form(biz_text, html, region_index=None, max_chars=None):
    r = _cap_markdown(_field(
        biz_text, html, _REVENUE_MIX_HEAD, _REVENUE_MIX_NA, content_re=_C_REVENUE_MIX,
        region_index=region_index, exclude_chapter_re=_FINANCIAL_CHAPTER_RE,
        field="revenue_mix_form"), max_chars)
    md = r.get("markdown") or ""
    if md and _REVENUE_MIX_NOT_SALES.search(md[:400]):
        # 캡션이 시공실적·매입·생산능력이면 매출표가 아니다. 값을 내지 말고 그렇게 말한다.
        return {**r, "status": "NEEDS_REVIEW",
                "note": "「주요 제품」 절이지만 캡션이 매출 구성표가 아닙니다.",
                "not_sales_caption": True}
    if md:
        # 상세 설명은 두지 않는다 — 축별 출처 라벨과 self_check 가 구체적으로 말한다.
        r["basis_note"] = ("II. 사업의 내용 > 2. 주요 제품 및 서비스 > 가. 주요 제품 등의 현황 "
                           "(기업공시서식 기재사항). 제품별 매출 구분은 K-IFRS 기준과 다를 수 있습니다.")
        r["self_check"] = _mix_self_check(md)
        r["self_check"]["guidance"] = (
            "unit 이 null 이면 단위가 원문에만 있습니다(천원/백만원 혼동은 1000배 차이). "
            "tie_out 이 불일치면 표가 여러 개이거나 연결/별도가 섞인 경우입니다. "
            "declared_total 은 표 기준이라 연결 손익계산서 매출과 다를 수 있습니다.")
    return r


# ── II-6-가 주요계약 (rnd 와 같은 소절인데 종전엔 연구개발만 나갔다) ──────────
_KEY_CONTRACTS_NA = re.compile(
    r"주요\s*계약[\s\S]{0,120}?(?:해당\s*사항\s*(?:이)?\s*없|없습니다|체결(?:중인)?\s*[^\n]{0,20}없)"
)


def extract_key_contracts(biz_text, html, region_index=None, max_chars=None):
    return _cap_markdown(_field(
        biz_text, html, _KEY_CONTRACTS_HEAD, _KEY_CONTRACTS_NA,
        content_re=_C_KEY_CONTRACTS, region_index=region_index,
        exclude_chapter_re=_FINANCIAL_CHAPTER_RE, field="key_contracts"), max_chars)


def extract_product_pricing(biz_text, html, region_index=None):
    return _compose_pricing_field(
        biz_text, html,
        [("product_pricing", "제품·서비스 가격 추이", _PRODUCT_PRICING_HEAD, _C_PRODUCT_PRICING)],
        _PRODUCT_PRICING_NA, region_index, field="product_pricing",
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
            region_index=None, field=None):
    """헤딩앵커(content-gate) → 실패 시 내용시그니처 폴백 → N/A. source로 어느 경로인지 표기."""
    regions = _render_biz_subsection_regions(
        html, head_patterns, max_chars=max_chars, content_re=content_re, region_index=region_index,
        field=field,
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
                "selection_method": ("heading_l_gate" if all(r.get("l_gated") for r in regions)
                                     else "heading"),
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


def extract_financial_ops(biz_text, html, region_index=None, max_chars=None):
    return _cap_markdown(_field2(biz_text, html, _FOPS_HEAD, _C_FOPS, _SIG_FOPS, None,
                                 region_index=region_index, field="financial_ops"), max_chars)

def extract_financial_soundness(biz_text, html, region_index=None, max_chars=None):
    return _cap_markdown(_field2(biz_text, html, _FSND_HEAD, _C_FSND, _SIG_FSND, None,
                                 region_index=region_index, field="financial_soundness"), max_chars)

def extract_investment_property(biz_text, html, region_index=None, max_chars=None):
    r = _field2(biz_text, html, _IPROP_HEAD, _C_IPROP, _SIG_IPROP, _IPROP_NA,
                region_index=region_index)
    if r.get("status") == "MARKDOWN":
        return _cap_markdown(r, max_chars)
    # 지주형 REIT 표준폼 프로즈 폴백(전용헤딩·시그니처 실패 시에만 — 작동하는 REIT엔 영향 없음)
    regions = _render_biz_subsection_regions(
        html, _IPROP_HEAD_STD, content_re=_C_IPROP_PROSE, region_index=region_index,
    )
    md = "\n\n———\n\n".join(region["markdown"] for region in regions) if regions else None
    if md:
        return _cap_markdown({"status": "MARKDOWN", "extraction_status": "SUCCESS",
                "source": "reit_prose", "section_source": {"selection_method": "heading",
                "matched_headings": [region["heading"] for region in regions if region.get("heading")],
                "boundary_methods": list(dict.fromkeys(region["boundary"] for region in regions))},
                "markdown": md}, max_chars)
    na = _IPROP_NA.search(biz_text) if biz_text else None
    return {"status": "NOT_APPLICABLE", "extraction_status": "NOT_APPLICABLE" if na else "NOT_COLLECTED",
            "na_reason": (_strip_tags(na.group(0))[:60] if na else "해당 소절 미검출")}
