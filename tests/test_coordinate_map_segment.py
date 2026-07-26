# -*- coding: utf-8 -*-
"""구간 매핑 로더 + 부문 주석 2차 경로 — 메커니즘 단위 테스트.

실제 운영 매핑은 이 레포에 두지 않는다. 여기서는 **가짜 식별자**로 로더·폴백·거부 게이트가
설계대로 동작하는지만 확인한다. 네트워크 0콜.
"""
from __future__ import annotations

import json

import pytest

from open_proxy_mcp.services import business_details as bd
from open_proxy_mcp.services import coordinate_map

FAKE = "{TEST}FAKE_SEG_C"
FAKE_S = "{TEST}FAKE_SEG_S"


@pytest.fixture
def mapping(tmp_path, monkeypatch):
    def _write(concepts: dict) -> None:
        p = tmp_path / "coordinate_map.json"
        p.write_text(json.dumps({"version": "test", "concepts": concepts}, ensure_ascii=False),
                     encoding="utf-8")
        monkeypatch.setenv("OPM_COORDINATE_MAP_PATH", str(p))
        coordinate_map._CACHE.update({"path": None, "mtime": None, "data": None})
    return _write


def _doc(title: str, rows: str, aclass: str = FAKE) -> str:
    return (f'<TABLE-GROUP ACLASS="{aclass}"><TITLE>{title}</TITLE>'
            f'<TABLE><TBODY>{rows}</TBODY></TABLE></TABLE-GROUP>')


ROWS_OK = (
    "<TR><TH>구 분</TH><TH>가전부문</TH><TH>반도체부문</TH><TH>합 계</TH></TR>"
    "<TR><TD>매출액</TD><TE>1,000</TE><TE>2,000</TE><TE>3,000</TE></TR>"
    "<TR><TD>영업이익</TD><TE>100</TE><TE>200</TE><TE>300</TE></TR>"
)


def test_map_absent_is_reported_not_silent(monkeypatch):
    """매핑이 없으면 loaded=False 로 표면화한다 — 조용히 넘어가면 안 된다."""
    monkeypatch.setenv("OPM_COORDINATE_MAP_PATH", "/nonexistent/coordinate_map.json")
    coordinate_map._CACHE.update({"path": None, "mtime": None, "data": None})
    st = coordinate_map.status()
    assert st["loaded"] is False and st["concepts"] == 0 and "error" in st
    t, r, why = bd.find_segment_note_region_by_code(_doc("3. 영업부문", ROWS_OK))
    assert (t, r) == (None, None) and why == "map_not_loaded"


def test_broken_map_does_not_raise(tmp_path, monkeypatch):
    p = tmp_path / "coordinate_map.json"
    p.write_text("{ this is not json", encoding="utf-8")
    monkeypatch.setenv("OPM_COORDINATE_MAP_PATH", str(p))
    coordinate_map._CACHE.update({"path": None, "mtime": None, "data": None})
    assert coordinate_map.load()["loaded"] is False


def test_code_path_finds_region(mapping):
    mapping({bd._SEG_CONCEPT: {"consolidated": FAKE, "title_must_contain": ["부문"]}})
    title, region, why = bd.find_segment_note_region_by_code(_doc("3. 영업부문", ROWS_OK))
    assert title == "3. 영업부문" and why == "code:consolidated"
    assert "가전부문" in region


def test_title_validation_rejects_wrong_block(mapping):
    """식별자가 부문 표가 아닌 블록에 붙은 작성사가 실재한다 — 제목 검증으로 거부해야 한다."""
    mapping({bd._SEG_CONCEPT: {"consolidated": FAKE, "title_must_contain": ["부문"]}})
    t, r, why = bd.find_segment_note_region_by_code(_doc("20-2. 주요 고객", ROWS_OK))
    assert (t, r) == (None, None) and why.startswith("title_mismatch")


def test_separate_used_when_consolidated_absent(mapping):
    mapping({bd._SEG_CONCEPT: {"consolidated": FAKE, "separate": FAKE_S,
                               "title_must_contain": ["부문"]}})
    _, _, why = bd.find_segment_note_region_by_code(_doc("4. 영업부문", ROWS_OK, aclass=FAKE_S))
    assert why == "code:separate"


@pytest.mark.parametrize("segments,title,expect", [
    ([{"name": "가전부문"}, {"name": "반도체부문"}], "3. 영업부문", ""),
    ([{"name": "대한민국"}, {"name": "외국"}, {"name": "아시아"}], "4. 부문별 정보", "geographic_only"),
    ([{"name": "(단위 : 천원)"}, {"name": "가전"}], "26. 영업부문", "name_pattern"),
    ([{"name": "주요거래선 (A)"}, {"name": "주요거래선 (B)"}], "36. 영업부문", "name_pattern"),
    ([{"name": "영업부문"}, {"name": "가전"}], "26. 영업부문", "name_is_title"),
    ([{"name": "단일부문"}], "3. 영업부문", "single_segment_unreliable"),
    ([], "3. 영업부문", "no_segments"),
])
def test_acceptance_gate(segments, title, expect):
    """새 경로 전용 게이트 — 실측 오탐 4종(지역표·단위캡션·거래선·제목조각)을 각각 거부한다."""
    sp = bd.SegmentProfit(segments=segments)
    assert bd._code_path_acceptable(sp, title) == expect


def test_code_path_not_consulted_when_text_anchor_succeeds(monkeypatch):
    """단조 안전 — 텍스트 경로가 성공하면 식별자 경로는 아예 호출되지 않는다.

    파서 내부 규칙에 의존하지 않도록 텍스트 경로 성공을 주입하고, 식별자 경로가 호출되면
    실패시킨다(순서 자체를 검증).
    """
    monkeypatch.setattr(bd, "find_segment_note_region", lambda _t: ("3. 부문별 정보", "REGION"))
    monkeypatch.setattr(bd, "parse_segment_table",
                        lambda a, r, ns="": bd.SegmentProfit(
                            status=bd.OK, anchor=a, segments=[{"name": "가전부문"},
                                                              {"name": "반도체부문"}]))

    def _boom(_h):
        raise AssertionError("텍스트 경로가 성공했는데 식별자 경로가 호출됐다")

    monkeypatch.setattr(bd, "find_segment_note_region_by_code", _boom)
    sp = bd.extract_segment_profit("", "note text", "연결재무제표 주석",
                                   note_html=_doc("3. 영업부문", ROWS_OK))
    assert sp.status == bd.OK and sp.selection_method == "text_anchor"


def test_code_path_used_when_text_anchor_finds_nothing(mapping, monkeypatch):
    """텍스트 경로가 구간을 못 잡으면 식별자 경로로 넘어가고 그 사실이 기록된다."""
    mapping({bd._SEG_CONCEPT: {"consolidated": FAKE, "title_must_contain": ["부문"]}})
    monkeypatch.setattr(bd, "find_segment_note_region", lambda _t: (None, None))
    monkeypatch.setattr(bd, "parse_segment_table",
                        lambda a, r, ns="": bd.SegmentProfit(
                            status=bd.OK, anchor=a, segments=[{"name": "가전부문"},
                                                              {"name": "반도체부문"}]))
    sp = bd.extract_segment_profit("", "", "연결재무제표 주석",
                                   note_html=_doc("3. 영업부문", ROWS_OK))
    assert sp.status == bd.OK and sp.selection_method == "code:consolidated"
