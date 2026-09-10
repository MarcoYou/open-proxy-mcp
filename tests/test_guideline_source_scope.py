"""Candidate relevance routing, tested at source and task input boundaries.

Synthetic disclosures test the contract; these are not voting-quality cases.
"""
import asyncio
from copy import deepcopy

import pytest

from open_proxy_mcp.services.guideline_assessment import (
    GuidelineAssessment, accept_assessment, build_assessment_task,
)
from open_proxy_mcp.services.guideline_evidence import (
    MAX_EVIDENCE_DOCUMENTS, MAX_EVIDENCE_REQUESTS, MAX_WINDOWS_PER_DOCUMENT,
    build_source_packet,
    collect_supplemental_sources,
)
from open_proxy_mcp.services.guideline_policy import load_pilot_guideline_policy


RECEIPT = "20260301000002"
SOURCE_ID = f"filing:{RECEIPT}"
RAW_TEXT = "홍길동 후보의 과거 자문계약은 해당 공시에서 종료일을 명시하고 있다. " * 90


class DocumentClient:
    """Return source text at the DART document boundary, without fixture files."""

    def __init__(self):
        self.receipts = []

    async def get_document_cached(self, receipt):
        self.receipts.append(receipt)
        return {"text": RAW_TEXT, "source": "document_xml"}


def collect(**options):
    return asyncio.run(collect_supplemental_sources(
        DocumentClient(), [{"type": "dart", "rcept_no": RECEIPT, **options}], "20260302"))


def task(name, supplemental=None):
    return build_assessment_task(
        candidate={"name": name, "birth_date": "1970.01", "role_type": "사외이사"},
        corp_code="00000001", agenda_title=f"{name} 이사 선임",
        notice_rcept="20260301000001",
        notice_text="홍길동과 김철수는 이번 주주총회에 제안된 사외이사 후보이다.",
        as_of="20260302", policy=load_pilot_guideline_policy(), attendance={},
        supplemental=supplemental)


def has_supplemental(candidate_task):
    return any(source["source_id"] == SOURCE_ID for source in candidate_task["sources"])


def test_candidate_read_changes_only_selected_candidate_task_and_metadata():
    original_a, original_b = task("홍길동"), task("김철수")
    source = collect(candidate_names=["홍길동"])
    before = deepcopy(source)
    changed_a, unchanged_b = task("홍길동", source), task("김철수", source)

    assert changed_a["task_id"] != original_a["task_id"]
    assert has_supplemental(changed_a)
    assert changed_a["supplemental_collection"][0]["read_options"]["candidate_names"] == ["홍길동"]
    assert unchanged_b == original_b  # Includes task_id and all collection metadata.
    assert not has_supplemental(unchanged_b)
    assert source == before


@pytest.mark.parametrize("status", ["fetch_failed", "format_unsupported", "after_as_of"])
def test_unrelated_failed_collection_also_preserves_task_identity(status):
    source = collect(candidate_names=["홍길동"])
    source[0].update(status=status, text="")
    assert task("김철수", source) == task("김철수")
    affected = task("홍길동", source)
    assert affected["supplemental_collection"][0]["status"] == status
    assert affected["task_id"] != task("홍길동")["task_id"]


@pytest.mark.parametrize("options", [
    {}, {"candidate_names": []}, {"source_scope": "candidate"},
    {"source_scope": "candidate", "focus_terms": ["홍길동"]},
    {"source_scope": "company_context", "candidate_names": []},
])
def test_legacy_and_empty_selection_remain_common_even_with_candidate_focus(options):
    source = collect(**options)
    assert source[0]["read_options"]["candidate_names"] == []
    assert has_supplemental(task("홍길동", source))
    assert has_supplemental(task("김철수", source))
    assert task("김철수", source)["task_id"] != task("김철수")["task_id"]


@pytest.mark.parametrize("name, selected", [
    ("홍길동", "  홍 길\t동  "),
    ("홍 길 동", "홍길동"),
    ("Hong Gil Dong", " hong\tGIL dong "),
    ("Ｈｏｎｇ Ｇｉｌ Ｄｏｎｇ", "Hong Gil Dong"),
])
def test_complete_names_match_across_whitespace_and_unicode_forms(name, selected):
    assert has_supplemental(task(name, collect(candidate_names=[selected])))


@pytest.mark.parametrize("name, selected", [
    ("홍길동", "홍"), ("홍길동", "길동"), ("홍길동", "홍길동준"),
    ("이영렬", "이"), ("Hong Gil Dong", "Hong"),
    ("홍길동(Hong Gil Dong)", "홍길동"),
])
def test_partial_name_or_alias_is_never_an_implicit_selection(name, selected):
    assert task(name, collect(candidate_names=[selected])) == task(name)


def test_unknown_name_is_explicit_and_never_falls_back_to_all_candidates():
    source = collect(candidate_names=["목록에 없는 후보"])
    packet = build_source_packet(source[0])
    assert packet["candidate_names"] == ["목록에 없는 후보"]
    assert "어느 후보에게도 자동 배정하지 않는다" in packet["hint"]
    for name in ("홍길동", "김철수"):
        assert task(name, source) == task(name)


def test_multiple_named_candidates_receive_source_without_automatic_widening():
    source = collect(candidate_names=["홍길동", "김철수"])
    assert has_supplemental(task("홍길동", source))
    assert has_supplemental(task("김철수", source))
    assert task("박영수", source) == task("박영수")


@pytest.mark.parametrize("names", [
    None, "홍길동", 1, True, {}, ("홍길동",), [None], [1], [True],
    [""], [" \n\t"], ["가" * 121], ["홍길동"] * 11,
])
def test_invalid_selection_rejected_before_fetch_with_no_submitted_value_echo(names):
    client = DocumentClient()
    with pytest.raises(ValueError, match="invalid source reading options") as error:
        asyncio.run(collect_supplemental_sources(client, [{
            "type": "dart", "rcept_no": RECEIPT, "candidate_names": names,
        }], "20260302"))
    assert str(error.value) == "guideline_evidence_sources: invalid source reading options"
    assert client.receipts == []


def test_candidate_count_and_name_length_boundaries_are_accepted():
    names = ["가" * 120, *[f"후보{index}" for index in range(9)]]
    source = collect(candidate_names=names)
    assert len(source[0]["read_options"]["candidate_names"]) == 10
    assert has_supplemental(task(names[0], source))


def test_next_window_preserves_candidate_selection_and_original_hash():
    source = collect(candidate_names=["홍길동"], text_chars=1000)
    first = build_source_packet(source[0], candidate_name="홍길동")
    request = first["read_next"]["source_request"]
    assert request["candidate_names"] == ["홍길동"]
    reread = asyncio.run(collect_supplemental_sources(DocumentClient(), [request], "20260302"))
    next_packet = build_source_packet(reread[0], candidate_name="홍길동")
    common = build_source_packet(collect(text_chars=1000)[0], candidate_name="홍길동")
    assert first["document_sha256"] == next_packet["document_sha256"] == common["document_sha256"]
    assert first["source_id"] == next_packet["source_id"] == common["source_id"]
    assert source[0]["text"] == reread[0]["text"] == RAW_TEXT
    assert task("김철수", reread) == task("김철수")


def test_kind_schema_accepts_named_selection_and_keeps_future_date_guard():
    class NoFetchClient:
        async def _throttle_kind(self):
            raise AssertionError("A future KIND source must not be fetched")

    result = asyncio.run(collect_supplemental_sources(NoFetchClient(), [{
        "type": "kind",
        "url": "https://kind.krx.co.kr/external/2026/03/03/000001/20260303000001/1.htm",
        "candidate_names": ["홍길동"],
    }], "20260302"))
    assert result[0]["status"] == "after_as_of"
    assert result[0]["read_options"]["candidate_names"] == ["홍길동"]
    assert task("김철수", result) == task("김철수")


def test_same_document_independent_candidate_windows_keep_prior_candidate_task():
    client = DocumentClient()
    request_a = {"type": "dart", "rcept_no": RECEIPT, "candidate_names": ["홍길동"],
                 "text_offset": 100, "text_chars": 1000}
    request_b = {"type": "dart", "rcept_no": RECEIPT, "candidate_names": ["김철수"],
                 "text_offset": 1100, "text_chars": 1000}
    before_sources = asyncio.run(collect_supplemental_sources(client, [request_a], "20260302"))
    prior_a, prior_b = task("홍길동", before_sources), task("김철수", before_sources)
    client.receipts.clear()
    after_sources = asyncio.run(collect_supplemental_sources(client, [request_a, request_b], "20260302"))
    assert client.receipts == [RECEIPT]
    assert task("홍길동", after_sources) == prior_a
    assert task("김철수", after_sources)["task_id"] != prior_b["task_id"]
    assert after_sources[0]["text"] == after_sources[1]["text"] == RAW_TEXT
    packets = [build_source_packet(source) for source in after_sources]
    assert packets[0]["document_sha256"] == packets[1]["document_sha256"]
    assert packets[0]["excerpt_offsets"] != packets[1]["excerpt_offsets"]


def test_same_source_windows_remain_independently_citable_without_bridging_gap():
    class GapClient(DocumentClient):
        async def get_document_cached(self, receipt):
            return {"text": "A" * 1000 + "omitted" * 1000 + "B" * 1000}

    source = asyncio.run(collect_supplemental_sources(GapClient(), [
        {"type": "dart", "rcept_no": RECEIPT, "text_offset": offset, "text_chars": 1000,
         "candidate_names": ["홍길동"]} for offset in (0, 8000)
    ], "20260302"))
    current = task("홍길동", source)
    merged = next(row for row in current["sources"] if row["source_id"] == SOURCE_ID)
    assert merged["excerpts"] == ["A" * 1000, "B" * 1000]
    assert merged["excerpt_offsets"] == [{"start": 0, "end": 1000}, {"start": 8000, "end": 9000}]
    assert len(merged["read_windows"]) == 2
    assert [window["read_options"]["text_offset"] for window in merged["read_windows"]] == [0, 8000]
    assert [window["read_next"]["source_request"]["text_offset"] for window in merged["read_windows"]] == [1000, 9000]
    assert all(window["read_options"]["candidate_names"] == ["홍길동"] for window in merged["read_windows"])
    assert merged["partial"]

    def assessment(quote):
        judgment = {"rationale": "합성 원문 인용 연결 검증이며 실제 후보 판단이 아닙니다.",
                    "evidence_refs": [{"source_id": SOURCE_ID, "quote": quote}],
                    "counterevidence": [], "unresolved": []}
        return GuidelineAssessment.model_validate({"task_id": current["task_id"],
            "evaluator": "synthetic-contract-test", "appointment": {**judgment, "value": "new"},
            "independence": {**judgment, "value": "no_public_concern"}})

    assert accept_assessment(current, assessment("A" * 24))["status"] == "accepted_unreviewed"
    assert accept_assessment(current, assessment("B" * 24))["status"] == "accepted_unreviewed"
    assert accept_assessment(current, assessment("A" * 12 + "B" * 12))["status"] == "rejected"


def test_explicit_window_does_not_discard_automatic_window_of_same_document():
    explicit = collect(candidate_names=["홍길동"], text_offset=2000, text_chars=1000)[0]
    automatic = {**deepcopy(explicit), "discovery": "officer_events"}
    automatic.pop("read_options")
    current = task("홍길동", [explicit, automatic])
    merged = next(row for row in current["sources"] if row["source_id"] == SOURCE_ID)
    assert len(merged["read_windows"]) == 2
    assert merged["candidate_names"] == []  # One common window remains common.
    assert [window["read_options"]["candidate_names"] for window in merged["read_windows"]] == [["홍길동"], []]
    for source in (explicit, automatic):
        packet = build_source_packet(source, candidate_name="홍길동")
        assert all(excerpt in merged["excerpts"] for excerpt in packet["excerpts"])
    assert task("김철수", [explicit, automatic]) == task("김철수", [automatic])


@pytest.mark.parametrize("other", [
    {"candidate_names": []},
    {"text_offset": 0, "text_chars": 12000, "source_scope": "company_context", "focus_terms": []},
])
def test_implicit_and_explicit_default_window_duplicates_are_rejected(other):
    client = DocumentClient()
    base = {"type": "dart", "rcept_no": RECEIPT}
    with pytest.raises(ValueError, match="duplicate source reading window"):
        asyncio.run(collect_supplemental_sources(client, [base, {**base, **other}], "20260302"))
    assert client.receipts == []


def test_normalized_candidate_and_focus_order_duplicates_are_rejected():
    client = DocumentClient()
    base = {"type": "dart", "rcept_no": RECEIPT}
    with pytest.raises(ValueError, match="duplicate source reading window"):
        asyncio.run(collect_supplemental_sources(client, [
            {**base, "candidate_names": ["홍길동", "Kim"], "focus_terms": ["자문", "종료"]},
            {**base, "candidate_names": [" KIM ", "홍 길 동"], "focus_terms": ["종료", "자문"]},
        ], "20260302"))
    assert client.receipts == []


def test_twenty_requests_share_five_document_acquisitions():
    client = DocumentClient()
    requests = [{"type": "dart", "rcept_no": f"2026030100000{index}", "text_offset": offset}
                for index in range(MAX_EVIDENCE_DOCUMENTS)
                for offset in range(MAX_EVIDENCE_REQUESTS // MAX_EVIDENCE_DOCUMENTS)]
    result = asyncio.run(collect_supplemental_sources(client, requests, "20260302"))
    assert len(result) == MAX_EVIDENCE_REQUESTS == 20
    assert len(client.receipts) == MAX_EVIDENCE_DOCUMENTS == 5


@pytest.mark.parametrize("kind, error", [
    ("requests", "maximum twenty reading requests"),
    ("documents", "maximum five documents"),
    ("windows", "maximum six windows per document"),
])
def test_multiwindow_limits_fail_before_any_document_fetch(kind, error):
    client = DocumentClient()
    if kind == "documents":
        requests = [{"type": "dart", "rcept_no": f"2026030100000{index}"}
                    for index in range(MAX_EVIDENCE_DOCUMENTS + 1)]
    else:
        count = (MAX_EVIDENCE_REQUESTS if kind == "requests" else MAX_WINDOWS_PER_DOCUMENT) + 1
        requests = [{"type": "dart", "rcept_no": RECEIPT, "text_offset": offset}
                    for offset in range(count)]
    with pytest.raises(ValueError, match=error):
        asyncio.run(collect_supplemental_sources(client, requests, "20260302"))
    assert client.receipts == []


def test_kind_multiwindow_uses_one_http_read():
    class Response:
        content = b"<html><body>" + b"source body " * 400 + b"</body></html>"
        status_code = 200

        def raise_for_status(self):
            pass

    class Client:
        def __init__(self):
            self._http = self
            self.reads = 0

        async def _throttle_kind(self):
            pass

        async def get(self, *_args, **_kwargs):
            self.reads += 1
            return Response()

    client = Client()
    url = "https://kind.krx.co.kr/external/2026/03/01/000001/20260301000001/1.htm"
    result = asyncio.run(collect_supplemental_sources(client, [
        {"type": "kind", "url": url, "text_offset": offset, "text_chars": 1000}
        for offset in (0, 1000)
    ], "20260302"))
    assert client.reads == 1
    assert result[0]["text"] == result[1]["text"]
    assert build_source_packet(result[0])["document_sha256"] == build_source_packet(result[1])["document_sha256"]
