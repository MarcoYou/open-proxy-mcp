# -*- coding: utf-8 -*-
"""사업의 내용 소절 구조 앵커 게이트 회귀 테스트 (biz_fields L-소절 스팬).

synthetic XML로 스팬 지도 생성·게이트 우선·폴백·이중검증 방어를 고정한다. network 0콜.
"""
from __future__ import annotations

from open_proxy_mcp.services.biz_fields import (
    _build_l_subsection_spans,
    build_region_index,
    extract_rnd,
    field_subsection_spans,
)


def _doc(decoy_first: bool = True) -> str:
    """소절 앵커 문서: III장(오섹션)에 미끼 헤딩, II장 소절6에 진짜 연구개발 소절."""
    decoy = ('<TITLE ATOC="Y">III. 재무에 관한 사항</TITLE>'
             '<P><SPAN USERMARK="ULE B">4. 연구개발 실적</SPAN></P>'
             '<P>미끼 구간 — 재무 장의 유사 제목</P>')
    body = ('<TITLE AASSOCNOTE="D-0-2-0-0">II. 사업의 내용</TITLE>'
            '<TITLE AASSOCNOTE="L-0-2-1-L1">1. 사업의 개요</TITLE><P>개요</P>'
            '<TITLE AASSOCNOTE="L-0-2-6-L1">6. 주요계약 및 연구개발활동</TITLE>'
            '<P><SPAN USERMARK="ULE B">나. 연구개발 실적</SPAN></P>'
            '<TABLE><TBODY><TR><TD>과제</TD><TE>신약 A</TE></TR>'
            '<TR><TD>연구개발비용 총계</TD><TE>1,234</TE></TR></TBODY></TABLE>'
            '<TITLE AASSOCNOTE="L-0-2-7-L1">7. 기타 참고사항</TITLE><P>기타</P>')
    parts = [decoy, body] if decoy_first else [body, decoy]
    return "<DOCUMENT>" + "".join(parts) + "</DOCUMENT>"


# ── 스팬 지도 생성 ──

def test_l_spans_built_with_title_verification() -> None:
    spans = _build_l_subsection_spans(_doc())
    assert ("L1", 6) in spans and ("L1", 1) in spans
    s, e = spans[("L1", 6)]
    assert "연구개발활동" in _doc()[s:e] and "기타 참고사항" not in _doc()[s:e]


def test_l_spans_dropped_on_title_mismatch() -> None:
    # 세대 간 의미 이동 방어: 코드는 있는데 제목이 기대 키워드와 다르면 그 소절은 버림
    xml = '<TITLE AASSOCNOTE="L-0-2-6-L1">6. 배당에 관한 사항</TITLE><P>x</P>'
    assert ("L1", 6) not in _build_l_subsection_spans(xml)


def test_l_spans_dropped_on_duplicate_code() -> None:
    xml = ('<TITLE AASSOCNOTE="L-0-2-6-L1">6. 주요계약 및 연구개발활동</TITLE><P>a</P>'
           '<TITLE AASSOCNOTE="L-0-2-6-L1">6. 주요계약 및 연구개발활동</TITLE><P>b</P>')
    assert _build_l_subsection_spans(xml) == {}


def test_field_spans_lookup() -> None:
    idx = build_region_index(_doc())
    assert field_subsection_spans("rnd", idx)          # L1-6 존재
    assert field_subsection_spans("segments", idx) == ()   # 매핑 없는 필드는 빈 튜플


# ── 게이트 동작: 소절 안 우선, 없으면 기존 전체 탐색 ──

def test_gate_prefers_in_subsection_heading_over_decoy() -> None:
    html = _doc(decoy_first=True)
    r = extract_rnd("", html, build_region_index(html))
    assert r["extraction_status"] == "SUCCESS"
    assert r["section_source"]["selection_method"] == "heading_l_gate"
    assert "신약 A" in r["markdown"] and "미끼 구간" not in r["markdown"]


def test_gate_falls_back_to_full_search_without_l_codes() -> None:
    # 구형 문서(L코드 없음): 기존 전체 탐색 그대로 — 진짜 소절이 어디 있든 찾는다
    html = _doc().replace(' AASSOCNOTE="L-0-2-6-L1"', "").replace(' AASSOCNOTE="L-0-2-1-L1"', "") \
                 .replace(' AASSOCNOTE="L-0-2-7-L1"', "")
    r = extract_rnd("", html, build_region_index(html))
    assert r["extraction_status"] == "SUCCESS"
    assert r["section_source"]["selection_method"] == "heading"
    assert "신약 A" in r["markdown"]
