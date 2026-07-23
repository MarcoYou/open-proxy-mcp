# -*- coding: utf-8 -*-
"""코드 앵커 우선 + 섹션별 텍스트 폴백 구간 슬라이싱 회귀 테스트 (_slice_getdoc_sections).

synthetic XML로 경계·폴백·방어 동작을 고정한다. network 0콜.
"""
from __future__ import annotations

from open_proxy_mcp.services.business_details import _slice_by_aassoc, _slice_getdoc_sections


def _doc_xml(*, biz_body: str = "사업의 개요 본문", conn_note_body: str = "부문별 정보 연결주석",
             with_conn: bool = True, with_sep: bool = True) -> str:
    parts = [
        '<DOCUMENT><BODY>',
        '<TITLE ATOC="Y" AASSOCNOTE="D-0-1-0-0">I. 회사의 개요</TITLE><P>개요</P>',
        f'<TITLE ATOC="Y" AASSOCNOTE="D-0-2-0-0">II. 사업의 내용</TITLE><P>{biz_body}</P>',
        '<TITLE AASSOCNOTE="L-0-2-1-L1">1. 사업의 개요</TITLE><P>소절 — biz 경계로 쓰면 안 됨</P>',
        '<TITLE ATOC="Y" AASSOCNOTE="D-0-3-0-0">III. 재무에 관한 사항</TITLE><P>재무 요약</P>',
    ]
    if with_conn:
        parts += [f'<TITLE AASSOCNOTE="D-0-3-3-0">3. 연결재무제표 주석</TITLE><P>{conn_note_body}</P>',
                  '<TITLE AASSOCNOTE="D-0-3-4-0">4. 재무제표</TITLE><P>별도 재무제표</P>']
    if with_sep:
        parts += ['<TITLE AASSOCNOTE="D-0-3-5-0">5. 재무제표 주석</TITLE><P>별도주석 본문</P>',
                  '<TITLE AASSOCNOTE="D-0-3-6-0">6. 배당에 관한 사항</TITLE><P>배당</P>']
    parts.append('</BODY></DOCUMENT>')
    return "\n".join(parts)


# ── 코드 앵커 경로 ──

def test_aassoc_biz_between_d0200_and_d0300() -> None:
    biz, note, src = _slice_by_aassoc(_doc_xml())
    assert "II. 사업의 내용" in biz and "사업의 개요 본문" in biz
    assert "III. 재무에 관한" not in biz          # 끝 경계 = D-0-3-0-0
    assert "소절 — biz 경계로 쓰면 안 됨" in biz   # L-계열 소절 앵커는 경계 아님

def test_aassoc_note_prefers_conn_and_ends_at_next_anchor() -> None:
    _, note, src = _slice_by_aassoc(_doc_xml())
    assert src == "연결재무제표 주석"
    assert "부문별 정보 연결주석" in note
    assert "별도 재무제표" not in note            # 끝 경계 = 다음 D-앵커(D-0-3-4-0)

def test_aassoc_note_falls_to_separate_when_no_conn() -> None:
    _, note, src = _slice_by_aassoc(_doc_xml(with_conn=False))
    assert src == "재무제표 주석"
    assert "별도주석 본문" in note and "배당" not in note

def test_aassoc_duplicate_anchor_disables_that_section() -> None:
    # 미지의 서식 변형(코드 중복 출현) 방어 — 그 섹션은 코드 경로 포기 → 텍스트 폴백 몫
    xml = _doc_xml() + '<TITLE AASSOCNOTE="D-0-2-0-0">II. 사업의 내용(중복)</TITLE>'
    biz, note, src = _slice_by_aassoc(xml)
    assert biz == ""                              # biz만 포기
    assert src == "연결재무제표 주석"              # note는 영향 없음

def test_aassoc_no_next_anchor_after_note_gives_empty() -> None:
    xml = ('<DOCUMENT><TITLE AASSOCNOTE="D-0-2-0-0">II</TITLE>'
           '<TITLE AASSOCNOTE="D-0-3-0-0">III</TITLE>'
           '<TITLE AASSOCNOTE="D-0-3-3-0">연결주석</TITLE><P>본문끝</P></DOCUMENT>')
    _, note, src = _slice_by_aassoc(xml)
    assert note == "" and src == ""               # 끝 경계 불명 → 텍스트 폴백(60KB cap)에 위임


# ── 통합: 코드 우선 + 섹션별 텍스트 폴백 ──

_TEXT = """II. 사업의 내용
사업 텍스트 폴백 본문
III. 재무에 관한 사항
3. 연결재무제표 주석
텍스트 폴백 주석 본문
4. 재무제표
"""

def test_slice_prefers_code_anchor_over_text() -> None:
    biz, note, src = _slice_getdoc_sections(_TEXT, html=_doc_xml())
    assert "사업의 개요 본문" in biz and "사업 텍스트 폴백 본문" not in biz
    assert "부문별 정보 연결주석" in note

def test_slice_falls_back_to_text_without_html() -> None:
    # 구형 euc-kr HTML 등 코드 앵커 없는 문서 — 기존 텍스트 로직 그대로
    biz, note, src = _slice_getdoc_sections(_TEXT)
    assert "사업 텍스트 폴백 본문" in biz
    assert "텍스트 폴백 주석 본문" in note and src == "연결재무제표 주석"

def test_slice_partial_fallback_per_section() -> None:
    # 앵커가 biz만 있고 주석엔 없는 문서: biz=코드 경로, note=텍스트 폴백
    xml = ('<DOCUMENT><TITLE AASSOCNOTE="D-0-2-0-0">II. 사업의 내용</TITLE><P>코드 biz</P>'
           '<TITLE AASSOCNOTE="D-0-3-0-0">III. 재무에 관한 사항</TITLE></DOCUMENT>')
    biz, note, src = _slice_getdoc_sections(_TEXT, html=xml)
    assert "코드 biz" in biz
    assert "텍스트 폴백 주석 본문" in note and src == "연결재무제표 주석"
