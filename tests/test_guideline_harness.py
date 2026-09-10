"""Run-continuation contracts, separate from real filing / judgment validation."""
import asyncio
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from open_proxy_mcp.services.guideline_harness import run_harness, get_harness_context
from open_proxy_mcp.services.meeting_pin import MeetingPin, get_meeting_pin
from open_proxy_mcp.dart.as_of import get_strict_as_of


REQUEST = {"cutoff_at": "2025-03-19T00:00:00+09:00", "notice_rcept_no": "20250218001524"}
ARGS = {"vote_style": "opm_guideline_v2", "guideline_mode": "pilot", "year": 2025,
        "meeting_type": "annual", "include_after_meeting": False}


def setup_boundary(monkeypatch):
    from open_proxy_mcp.services import company, meeting_pin
    pin = MeetingPin(corp_code="00126380", notice_rcept_no=REQUEST["notice_rcept_no"],
                     year=2025, meeting_type="annual", meeting_date=date(2025, 3, 19),
                     published="20250218", report_name="주주총회소집공고", filer_name="fixture",
                     meeting_datetime="2025-03-19", source_sha256="a" * 64)
    async def resolve(*args, **kwargs):
        assert get_strict_as_of()["effective_as_of"] == "20250318"
        return SimpleNamespace(selected={"corp_code": pin.corp_code}, status="ok")
    async def prepare(*args, **kwargs):
        assert kwargs["as_of"] == "20250318"
        return pin
    monkeypatch.setattr(company, "resolve_company_query", resolve)
    monkeypatch.setattr(meeting_pin, "prepare_meeting_pin", prepare)
    return pin


def result(source_hash="b" * 64, *, sources=True):
    return {"status": "ok", "data": {"agenda_decisions": [{
        "evidence_rcept_no": REQUEST["notice_rcept_no"], "decision": "REVIEW",
        "guideline_trace": {"assessment_task": {"sources": [
            {"source_id": "filing:20250311001085", "document_sha256": source_hash}
        ] if sources else []}}}]}}


def test_continuation_freezes_settings_and_suppresses_changed_source(monkeypatch):
    pin = setup_boundary(monkeypatch)
    calls = []
    source_hash = "b" * 64
    async def invoke(company, **kwargs):
        calls.append(kwargs)
        assert get_meeting_pin() == pin
        assert kwargs["as_of"] == "20250318"
        assert get_harness_context()["binding"]["cutoff_at"]
        return result(source_hash)
    first = asyncio.run(run_harness("fixture", REQUEST, ARGS, invoke))
    continuation = first["data"]["guideline_harness"]["continuation"]
    changed = asyncio.run(run_harness("fixture", continuation,
        {**ARGS, "guideline_workflow": {"stance": "conservative"}}, invoke))
    assert changed["data"]["guideline_harness"]["error_code"] == "policy_changed"
    assert len(calls) == 1  # Reject configuration drift before reading / evaluating.
    source_hash = "c" * 64
    changed = asyncio.run(run_harness("fixture", continuation, ARGS, invoke))
    assert changed["data"]["guideline_harness"]["error_code"] == "source_changed"
    assert changed["data"]["agenda_decisions"] == []
    assert get_meeting_pin() is get_strict_as_of() is get_harness_context() is None


def test_temporarily_missing_source_does_not_block_other_judgments(monkeypatch):
    setup_boundary(monkeypatch)
    available = True
    async def invoke(*args, **kwargs):
        return result(sources=available)
    first = asyncio.run(run_harness("fixture", REQUEST, ARGS, invoke))
    continuation = first["data"]["guideline_harness"]["continuation"]
    available = False
    second = asyncio.run(run_harness("fixture", continuation, ARGS, invoke))
    assert second["data"]["agenda_decisions"]
    trace = second["data"]["guideline_harness"]
    assert trace["unavailable_previous_sources"] == ["filing:20250311001085"]
    assert trace["continuation"]["expected_sources"]["filing:20250311001085"] == "b" * 64


def test_scopes_are_restored_after_unexpected_failure(monkeypatch):
    setup_boundary(monkeypatch)
    async def invoke(*args, **kwargs):
        raise RuntimeError("fixture failure")
    async def exercise():
        try:
            await run_harness("fixture", REQUEST, ARGS, invoke)
        except RuntimeError:
            pass
        assert get_meeting_pin() is get_strict_as_of() is get_harness_context() is None
    asyncio.run(exercise())


def test_invalid_time_or_mode_never_runs_analysis():
    async def invoke(*args, **kwargs):
        raise AssertionError("must not evaluate")
    for request, arguments in [({**REQUEST, "cutoff_at": "2025-03-19T00:00:00"}, ARGS),
                               (REQUEST, {**ARGS, "include_after_meeting": True}),
                               ({**REQUEST, "unexpected": "ignored?"}, ARGS)]:
        response = asyncio.run(run_harness("fixture", request, arguments, invoke))
        assert response["status"] == "error"
        assert get_strict_as_of() is None


def test_historical_meeting_phase_uses_admitted_day():
    from open_proxy_mcp.dart.as_of import set_strict_as_of, reset_strict_as_of
    from open_proxy_mcp.services.shareholder_meeting import _meeting_phase
    token = set_strict_as_of(REQUEST["cutoff_at"])
    try:
        assert _meeting_phase({"datetime": "2025년 03월 19일"}, None, None) == (
            "pre_meeting", "not_due_yet")
    finally:
        reset_strict_as_of(token)


def test_law_layer_requires_publication_before_cutoff(monkeypatch):
    from open_proxy_mcp.dart.as_of import set_strict_as_of, reset_strict_as_of, strict_exclusions
    from open_proxy_mcp.services import proxy_advise
    monkeypatch.setattr(proxy_advise, "_load_law_provisions", lambda: {
        "known": {"promulgation_date": "2025-03-18"},
        "future": {"promulgation_date": "2025-03-20"}})
    token = set_strict_as_of(REQUEST["cutoff_at"])
    try:
        # Meeting can be after enactment; knowledge still stops at cutoff.
        assert proxy_advise._applies_to_match({"provision": "known"}, None, "2025-04-01")
        assert not proxy_advise._applies_to_match({"provision": "future"}, None, "2025-04-01")
        assert not proxy_advise._applies_to_match({}, None, "2025-04-01")
        assert {r["reason"] for r in strict_exclusions()} == {"after_cutoff", "publication_unverified"}
    finally:
        reset_strict_as_of(token)
    assert proxy_advise._applies_to_match({}, None, "2025-04-01")


def test_engine_replacement_rejects_continuation_before_analysis(monkeypatch):
    from open_proxy_mcp.services import guideline_harness

    setup_boundary(monkeypatch)
    bundle = "d" * 64
    monkeypatch.setattr(guideline_harness, "engine_bundle_sha256", lambda: bundle)
    calls = []

    async def invoke(*args, **kwargs):
        calls.append(kwargs)
        assert get_harness_context()["binding"]["engine_bundle_sha256"] == bundle
        return result()

    first = asyncio.run(run_harness("fixture", REQUEST, ARGS, invoke))
    trace = first["data"]["guideline_harness"]
    assert trace["engine_bundle_sha256"] == "d" * 64
    bundle = "e" * 64
    changed = asyncio.run(run_harness("fixture", trace["continuation"], ARGS, invoke))
    assert changed["status"] == "error"
    assert changed["data"]["guideline_harness"]["error_code"] == "policy_changed"
    assert changed["data"]["agenda_decisions"] == []
    assert len(calls) == 1
    # A deliberate new run gets distinct policy/run identities under the same v2 file.
    restarted = asyncio.run(run_harness("fixture", REQUEST, ARGS, invoke))
    assert restarted["data"]["guideline_harness"]["policy_sha256"] != trace["policy_sha256"]
    assert restarted["data"]["guideline_harness"]["run_id"] != trace["run_id"]


@pytest.mark.parametrize("member", ["services/proxy_advise.py", "data/laws/law_provisions.json",
                                     "data/guideline/guideline_thresholds.json"])
def test_engine_manifest_changes_with_runtime_code_or_bundled_rules(tmp_path, member):
    from open_proxy_mcp.services.guideline_harness import _public_engine_manifest, digest

    path = tmp_path / member
    path.parent.mkdir(parents=True)
    path.write_text("first public implementation")
    first = digest(_public_engine_manifest(tmp_path))
    path.write_text("changed public implementation")
    assert digest(_public_engine_manifest(tmp_path)) != first


def test_engine_manifest_never_reads_credentials_raw_results_or_symlink_targets(tmp_path, monkeypatch):
    from open_proxy_mcp.services.guideline_harness import _public_engine_manifest

    permitted = {"server.py", "services/rules.py", "data/laws/catalog.json"}
    excluded = {".env", "data/.credentials.json", "raw/document.py", "data/raw/document.json",
                "data/results/assessment.json", "data/cache/response.json", "output/model.py",
                ".private/secrets.py", "private_extension/extra.py", "data/keys.json",
                "data/keys/config.json", "data/api_keys.json", "data/userresults/assessment.json"}
    for member in permitted | excluded:
        path = tmp_path / member
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture")
    (tmp_path / "data" / "linked.json").symlink_to(tmp_path / ".env")
    (tmp_path / "services" / "linked-directory").symlink_to(tmp_path / ".private", target_is_directory=True)
    reads = []
    original = Path.read_bytes

    def read_bytes(path):
        relative = path.relative_to(tmp_path).as_posix()
        assert relative in permitted
        reads.append(relative)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    manifest = _public_engine_manifest(tmp_path)
    assert set(manifest) == set(reads) == permitted


def test_engine_fingerprint_is_process_cached_until_deployment_restart(monkeypatch):
    from open_proxy_mcp.services import guideline_harness

    calls = []
    bundle = {"services/rules.py": "a" * 64}

    def manifest(package):
        calls.append(package)
        return dict(bundle)

    guideline_harness.engine_bundle_sha256.cache_clear()
    monkeypatch.setattr(guideline_harness, "_public_engine_manifest", manifest)
    try:
        first = guideline_harness.engine_bundle_sha256()
        bundle["services/rules.py"] = "b" * 64
        assert guideline_harness.engine_bundle_sha256() == first
        assert len(calls) == 1
        guideline_harness.engine_bundle_sha256.cache_clear()  # Simulates a fresh deployment process.
        assert guideline_harness.engine_bundle_sha256() != first
    finally:
        guideline_harness.engine_bundle_sha256.cache_clear()


def test_research_stays_inside_pinned_boundary_and_does_not_become_evidence(monkeypatch):
    from open_proxy_mcp.services import guideline_research
    pin = setup_boundary(monkeypatch)
    calls = []

    async def discover(client, corp_code, as_of, meeting_type, meeting_date, query):
        assert get_strict_as_of()['effective_as_of'] == '20250318'
        assert corp_code == pin.corp_code
        assert (as_of, meeting_type, meeting_date) == ('20250318', 'annual', '2025-03-19')
        calls.append(query.model_dump())
        return {'candidates': [{'rcept_no': '20250317000001', 'status': 'unread'}],
                'no_contents_read': True}

    async def invoke(company, **kwargs):
        assert 'guideline_research' not in kwargs
        return result()

    monkeypatch.setattr(guideline_research, 'discover_research_sources', discover)
    first = asyncio.run(run_harness('fixture', REQUEST, ARGS, invoke))
    second = asyncio.run(run_harness('fixture', first['data']['guideline_harness']['continuation'],
        {**ARGS, 'guideline_research': {'kind': 'meeting_resolution'}}, invoke))
    first_harness, second_harness = (p['data']['guideline_harness'] for p in (first, second))
    assert first_harness['source_manifest'] == second_harness['source_manifest']
    assert first_harness['run_id'] == second_harness['run_id']
    assert len(calls) == 1
    assert second['data']['guideline_application']['research_discovery']['no_contents_read']
    assert second['data']['guideline_application']['research_plan']['required_documents'] == []
    assert get_strict_as_of() is None


@pytest.mark.parametrize('query', [
    {'kind': 'arbitrary_web'}, {'kind': 'periodic_reports', 'company': 'another'},
    {'kind': 'meeting_resolution', 'page': 0},
])
def test_invalid_research_rejected_before_company_or_analysis(monkeypatch, query):
    async def invoke(*args, **kwargs):
        raise AssertionError('invalid query must not execute')
    response = asyncio.run(run_harness('fixture', REQUEST,
        {**ARGS, 'guideline_research': query}, invoke))
    assert response['status'] == 'error'
    assert response['data']['guideline_harness']['error_code'] == 'invalid_harness_request'
    assert get_strict_as_of() is None


def test_research_cannot_bypass_pinned_harness():
    from open_proxy_mcp.services.proxy_advise import build_proxy_advise_payload
    response = asyncio.run(build_proxy_advise_payload('fixture',
        guideline_research={'kind': 'meeting_resolution'}))
    assert response['status'] == 'error'
    assert response['data']['guideline_harness']['error_code'] == 'research_requires_harness'


def test_unknown_candidate_source_scope_is_reported_without_guessing(monkeypatch):
    setup_boundary(monkeypatch)
    async def invoke(*args, **kwargs):
        payload = result()
        task = payload['data']['agenda_decisions'][0]['guideline_trace']['assessment_task']
        task['candidate_name'] = '이영렬'
        payload['data']['guideline_application'] = {'supplemental_collection': [
            {'rcept_no': '20250311001085', 'read_options': {'candidate_names': ['이 영 렬']}},
            {'rcept_no': '20250312001085', 'read_options': {'candidate_names': ['이영']}}]}
        return payload
    response = asyncio.run(run_harness('fixture', REQUEST, ARGS, invoke))
    unused = response['data']['guideline_application']['unused_candidate_scopes']
    assert len(unused) == 1
    assert unused[0]['candidate_names'] == ['이영']
    assert response['data']['agenda_decisions']


def test_structure_is_opt_in_bound_and_returned_without_blocking_candidates(monkeypatch):
    setup_boundary(monkeypatch)
    async def invoke(*args, **kwargs):
        assert 'guideline_structure' not in kwargs
        from open_proxy_mcp.services.election_structure import capture_structure_sources
        capture_structure_sources(REQUEST['notice_rcept_no'], '정관 제1조 이사의 임기는 3년으로 한다.', [])
        payload = result(sources=False)
        payload['data']['agenda_decisions'][0].update(agenda_title='정관 변경', agenda_category='articles_amendment')
        return payload
    first = asyncio.run(run_harness('fixture', REQUEST, {**ARGS, 'guideline_structure': {}}, invoke))
    entry = first['data']['guideline_application']['structure_tasks'][0]
    assert entry['task']['sources'][0]['excerpts']
    assert entry['assessment']['status'] == 'pending'
    assert first['data']['agenda_decisions'][0]['decision'] == 'REVIEW'
    continuation = first['data']['guideline_harness']['continuation']
    changed = asyncio.run(run_harness('fixture', continuation, ARGS, invoke))
    assert changed['status'] == 'error'
