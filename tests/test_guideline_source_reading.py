"""Source navigation tests; synthetic text is not a governance ground truth."""
import asyncio
from copy import deepcopy

import pytest

from open_proxy_mcp.services.guideline_evidence import build_source_packet, collect_supplemental_sources
from open_proxy_mcp.services.guideline_assessment import build_assessment_task
from open_proxy_mcp.services.guideline_policy import load_pilot_guideline_policy


def source(text, **options):
    return {"status": "read", "rcept_no": "20260301000002",
            "source_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260301000002",
            "text": text, "read_options": options}


def test_company_event_without_candidate_name_stays_citable():
    text = "공개매수자의 지배주주와 대상 회사의 계열관계는 아래에서 설명한다."
    task = build_assessment_task(candidate={"name": "홍길동", "role_type": "사외이사"},
        corp_code="00000001", agenda_title="이사 선임", notice_rcept="20260301000001",
        notice_text="홍길동은 사외이사 후보로 제안되었다.", as_of="20260302",
        policy=load_pilot_guideline_policy(), attendance={}, supplemental=[source(text)])
    packet = next(s for s in task["sources"] if s["source_id"] == "filing:20260301000002")
    assert packet["excerpts"] == [text]
    assert packet["candidate_name_present"] is False
    assert packet["source_scope"] == "company_context"


def test_next_window_reaches_omitted_footnote_and_keeps_document_identity():
    raw = "이 문서는 아직 종결되지 않은 거래를 설명한다. " * 1000 + "주석: 해당 수량은 거래 종결 후 취득할 예정 수량이다."
    first = build_source_packet(source(raw, text_chars=1000))
    request = first["read_next"]["source_request"]
    assert request["text_offset"] == 1000
    assert first["partial"] and first["read_next"]["has_more_after_window"]
    focused = build_source_packet(source(raw, focus_terms=["주석:"], text_chars=5000))
    assert "거래 종결 후" in " ".join(focused["excerpts"])
    assert first["document_sha256"] == focused["document_sha256"]
    assert first["excerpt_offsets"] != focused["excerpt_offsets"]


def test_disjoint_windows_cannot_invent_contiguous_quote():
    raw = "가나다 회사 공시 머리 " + "x" * 9000 + "라마바 실제 별도 항목 " + "y" * 9000
    packet = build_source_packet(source(raw, focus_terms=["가나다", "라마바"], text_chars=12000))
    assert len(packet["excerpts"]) == 2
    assert packet["excerpt_offsets"][0]["end"] < packet["excerpt_offsets"][1]["start"]
    assert "가나다" in packet["excerpts"][0] and "라마바" in packet["excerpts"][1]


def test_missing_focus_returns_source_and_marks_search_not_found():
    packet = build_source_packet(source("존재하는 원문 내용만 반환한다.", focus_terms=["없는 이름"]))
    assert packet["excerpts"]
    assert packet["focus_terms_not_found"] == ["없는 이름"]


@pytest.mark.parametrize("options", [
    {"text_offset": True}, {"text_chars": 0}, {"text_offset": -1},
    {"focus_terms": [""]}, {"focus_terms": ["a"] * 7}, {"source_scope": {}},
])
def test_invalid_navigation_options_rejected(options):
    with pytest.raises(ValueError, match="reading options"):
        build_source_packet(source("원문", **options))


def test_offset_and_source_context_are_task_bound():
    kwargs = dict(candidate={"name": "홍길동", "role_type": "사외이사"}, corp_code="00000001",
        agenda_title="이사 선임", notice_rcept="20260301000001", notice_text="홍길동은 사외이사 후보이다.",
        as_of="20260302", policy=load_pilot_guideline_policy(), attendance={},
        supplemental=[source("긴 원문에 기재된 조건부 거래 " * 2000, text_chars=1000)])
    original = build_assessment_task(**kwargs)
    changed = deepcopy(kwargs)
    changed["supplemental"][0]["read_options"]["text_offset"] = 1000
    assert original["task_id"] != build_assessment_task(**changed)["task_id"]


def test_source_request_keeps_date_gate_before_document_read():
    class Client:
        async def get_document_cached(self, rc):
            raise AssertionError("Future filing must not be fetched")
    result = asyncio.run(collect_supplemental_sources(Client(), [
        {"type": "dart", "rcept_no": "20260910000001", "focus_terms": ["공개매수"]}], "20260909"))
    assert result[0]["status"] == "after_as_of"
    assert build_source_packet(result[0]) is None
