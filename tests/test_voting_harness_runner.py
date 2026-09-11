"""Execution-contract tests, not live-model judgment-quality evidence."""
import asyncio
from copy import deepcopy
import hashlib
import json

import pytest

from open_proxy_mcp.harness import (
    CallbackModelAdapter, FinishAction, HarnessRequest, ReadSourcesAction,
    RunBudget, SubmitAction, VotingHarness,
)
from open_proxy_mcp.harness.transport import StreamableHTTPTransport, TransportError, _payload
from open_proxy_mcp.harness.runner import DiscoverSourcesAction


def request(**changes):
    return HarnessRequest(**{
        "company": "합성회사", "year": 2025, "meeting_type": "annual",
        "cutoff_at": "2025-03-18T18:00:00+09:00", "notice_rcept_no": "20250301000001",
        **changes,
    })


def assessment(task_id):
    judgment = {"rationale": "공개 원문 검토", "evidence_refs": [],
                "counterevidence": [], "unresolved": []}
    return {"task_id": task_id, "evaluator": "claimed-model",
            "appointment": {**judgment, "value": "new"},
            "independence": {**judgment, "value": "no_public_concern"}}


class FakeMCP:
    """A synthetic protocol peer. Acceptance below is deliberately not a grader."""
    def __init__(self, candidates=("a", "b"), *, reject=(), change=None):
        self.candidates = candidates
        self.reject = set(reject)
        self.calls = []
        self.change = change

    async def call_tool(self, name, arguments):
        assert name == "proxy_advise_before_meeting"
        self.calls.append(deepcopy(arguments))
        binding = arguments["guideline_harness"]
        manifest = {"notice:20250301000001": "a" * 64}
        reads = arguments.get("guideline_evidence_sources", [])
        for source in reads:
            manifest["filing:" + source.get("rcept_no", "kind")] = "b" * 64
        harness = {"run_id": "e" * 64, "policy_sha256": "c" * 64,
                   "source_manifest": manifest}
        if len(self.calls) > 1:
            if self.change == "source":
                manifest["notice:20250301000001"] = "d" * 64
            if self.change == "policy":
                harness["policy_sha256"] = "d" * 64
            if self.change == "run":
                harness["run_id"] = "different-run"
        harness["continuation"] = {
            "cutoff_at": binding["cutoff_at"], "notice_rcept_no": binding["notice_rcept_no"],
            "expected_run_id": harness["run_id"], "expected_policy_sha256": harness["policy_sha256"],
            "expected_sources": deepcopy(manifest),
        }
        if self.change == "cutoff":
            harness["continuation"]["cutoff_at"] = "2026-01-01T00:00:00+09:00"
        submitted = {item["task_id"] for item in arguments.get("guideline_assessments", [])}
        rows = []
        for candidate in self.candidates:
            key = candidate + ("-read" if reads else "")
            state = "pending" if key not in submitted else (
                "rejected" if candidate in self.reject else "accepted_unreviewed")
            rows.append({"agenda_title": candidate, "decision": "REVIEW", "guideline_trace": {
                "assessment_task": {"task_id": key, "candidate_name": candidate,
                                    "sources": [{"source_id": next(iter(manifest)), "excerpts": ["합성 원문"]}]},
                "llm_assessment": {"status": state},
            }})
        return {"status": "ok", "data": {"guideline_harness": harness,
                "agenda_decisions": rows, "guideline_application": {"research_plan": {"next_actions": []}}}}


async def always_submit(context):
    return SubmitAction(assessments=[assessment(context.tasks[0]["task_id"])])


def execute(peer=None, callback=always_submit, budget=None):
    return asyncio.run(VotingHarness(peer or FakeMCP(),
        CallbackModelAdapter("provider-a", "v1", callback), budget).run(request()))


def test_provider_swap_uses_same_request_and_contract_without_ballot_submission():
    peers = [FakeMCP(), FakeMCP()]

    class OtherProvider:
        name, version = "provider-b", "v2"

        async def assess(self, context):
            return {"action": "submit", "assessments": [assessment(context.tasks[0]["task_id"])]}

    first = execute(peers[0])
    second = asyncio.run(VotingHarness(peers[1], OtherProvider()).run(request()))
    assert first.status == second.status == "complete"
    assert first.counts["accepted"] == second.counts["accepted"] == 2
    assert peers[0].calls[0] == peers[1].calls[0]
    assert first.evaluator["identity_basis"] == "self_reported"
    assert first.evaluator["name"] != second.evaluator["name"]
    assert first.human_reviewed is False and first.ballots_submitted == 0
    for call in peers[0].calls:
        assert call["include_after_meeting"] is False
        assert call["vote_style"] == "opm_guideline_v2" and call["guideline_mode"] == "pilot"
        assert call["meeting_type"] == "annual" and call["year"] == 2025
        assert "as_of" not in call
        assert call["guideline_harness"]["cutoff_at"] == request().cutoff_at
    assert peers[0].calls[1]["guideline_harness"]["expected_sources"]
    assert peers[0].calls[-1]["guideline_assessments"][0]["evaluator"] == "provider-a/v1"


def test_model_exception_isolated_with_redacted_error_and_fair_peer_progress():
    seen = []

    async def callback(context):
        key = context.tasks[0]["task_id"]
        seen.append(key)
        if key == "a":
            raise RuntimeError("secret-token-do-not-echo")
        return await always_submit(context)

    result = execute(callback=callback, budget=RunBudget(max_attempts_per_task=2))
    assert result.status == "partial"
    assert seen[:2] == ["a", "b"]
    assert result.task_states == {"a": "retry_budget_exhausted", "b": "accepted_unreviewed"}
    assert "secret-token" not in repr(result)


def test_server_rejection_is_retried_without_dropping_accepted_peer():
    peer = FakeMCP(reject={"a"})
    result = execute(peer, budget=RunBudget(max_attempts_per_task=2))
    assert result.counts["rejected"] == 2
    assert result.counts["accepted"] == 1
    assert result.task_states["b"] == "accepted_unreviewed"
    assert [a["task_id"] for a in peer.calls[-1]["guideline_assessments"]] == ["b", "a"]


def test_repair_receives_own_submission_only_for_the_same_task():
    peer = FakeMCP(reject={'a'}); seen = []
    async def callback(context):
        key = context.tasks[0]['task_id']
        if key == 'b' or key not in seen:
            assert context.previous_submission is None
        else:
            assert context.previous_submission['task_id'] == key
            context.previous_submission['evaluator'] = 'local-mutation'
        seen.append(key)
        return await always_submit(context)
    execute(peer, callback, RunBudget(max_attempts_per_task=2))
    assert seen == ['a', 'b', 'a']
    assert all(a['evaluator'] != 'local-mutation' for call in peer.calls for a in call.get('guideline_assessments', []))


def test_structure_feedback_uses_only_known_local_codes():
    from open_proxy_mcp.harness.runner import _assessment_rejection_feedback
    p = {'data': {'guideline_application': {'structure_tasks': [{
        'task': {'task_id': 's'}, 'assessment': {'task_id': 's', 'rejected_items': [
            {'code': 'invalid_charter_event_role', 'item_id': 'private-value'},
            {'code': 'arbitrary-remote-message'}, {'code': ['malformed']}]}}]}}}
    feedback = _assessment_rejection_feedback(p, 's')
    assert 'target_event_id=null' in str(feedback)
    assert 'private-value' not in str(feedback) and 'arbitrary-remote' not in str(feedback)
    assert _assessment_rejection_feedback(p, 'another-task') == ('server_rejected_assessment',)


@pytest.mark.parametrize('code', ['model_budget_unavailable', 'model_auth_unavailable', 'model_not_available'])
def test_permanent_provider_failure_stops_retries_without_inventing_a_vote(code):
    from open_proxy_mcp.harness import ModelUnavailableError
    async def unavailable(context):
        if context.tasks[0]['task_id'] == 'a':
            return await always_submit(context)
        raise ModelUnavailableError(code)
    result = execute(callback=unavailable)
    assert result.status == 'partial' and result.stop_reason == code
    assert result.counts['model_calls'] == 2 and result.counts['accepted'] == 1
    assert result.counts['submitted'] == 1 and result.counts['model_errors'] == 1
    assert result.task_states['a'] == 'accepted_unreviewed'
    assert result.task_states['b'] == 'unassessed'
    assert result.payload is not None and result.ballots_submitted == 0


def test_structure_partial_result_is_repaired_using_own_submission_and_specific_feedback():
    from test_election_structure import setup_task, submission
    from test_charter_history import event
    from open_proxy_mcp.services.election_structure import accept_structure_assessment
    p, task = setup_task()
    class StructurePeer(FakeMCP):
        async def call_tool(self, name, arguments):
            result = await super().call_tool(name, arguments)
            submitted = (arguments.get('guideline_structure') or {}).get('assessments', [])
            result['data']['agenda_decisions'] = deepcopy(p['data']['agenda_decisions'])
            result['data']['guideline_application']['structure_tasks'] = [{
                'task': deepcopy(task),
                'assessment': accept_structure_assessment(task, submitted[0] if submitted else None)}]
            return result
    contexts = []
    async def callback(context):
        contexts.append(context)
        if not context.previous_submission:
            item = submission(task)
            item['out_of_scope_agenda_ids'] = [task['agendas'][1]['agenda_id']]
            item['facts'].append(event(task, 'bad-link', 'snapshot', target='comparison'))
        else:
            assert context.tasks[0]['previous_assessment']['judgments'][0]['recommendation'] == 'FOR'
            assert any('invalid_charter_event_role' in f for f in context.feedback)
            item = deepcopy(context.previous_submission)
            item['facts'][-1]['data']['target_event_id'] = None
        return SubmitAction(assessments=[item])
    peer = StructurePeer(candidates=())
    result = asyncio.run(VotingHarness(peer, CallbackModelAdapter('test', 'v1', callback)).run(request(structure=True)))
    assert result.status == 'complete' and len(contexts) == 2 and result.counts['submitted'] == 2
    assert peer.calls[1]['guideline_structure']['assessments'][0]['judgments'] == peer.calls[2]['guideline_structure']['assessments'][0]['judgments']


def test_scope_only_structure_completion_does_not_retry_or_count_as_an_accepted_judgment():
    from test_election_structure import setup_task
    from open_proxy_mcp.services.election_structure import accept_structure_assessment
    payload, task = setup_task()

    class StructurePeer(FakeMCP):
        async def call_tool(self, name, arguments):
            result = await super().call_tool(name, arguments)
            submitted = (arguments.get('guideline_structure') or {}).get('assessments', [])
            result['data']['agenda_decisions'] = deepcopy(payload['data']['agenda_decisions'])
            result['data']['guideline_application']['structure_tasks'] = [{
                'task': deepcopy(task),
                'assessment': accept_structure_assessment(task, submitted[0] if submitted else None)}]
            return result

    async def scope_only(context):
        current = context.tasks[0]
        return SubmitAction(assessments=[{
            'task_id': current['task_id'], 'evaluator': 'test-model',
            'facts': [], 'gaps': [], 'findings': [], 'judgments': [],
            'out_of_scope_agenda_ids': [a['agenda_id'] for a in current['agendas']]}])

    peer = StructurePeer(candidates=())
    result = asyncio.run(VotingHarness(peer, CallbackModelAdapter('test', 'v1', scope_only)).run(request(structure=True)))
    assert result.status == 'complete' and result.stop_reason == 'all_supported_tasks_processed'
    assert result.task_states == {task['task_id']: 'scope_complete'}
    assert result.counts['model_calls'] == result.counts['submitted'] == 1
    assert result.counts['scope_complete'] == 1 and result.counts['accepted'] == result.counts['unassessed'] == 0
    assert result.counts['rejected'] == 0 and len(peer.calls) == 2
    assert not any(event['code'] == 'assessment_accepted' for event in result.events)
    assert result.payload['data']['agenda_decisions'] == payload['data']['agenda_decisions']


def test_extra_source_invalidates_old_assessments_and_reassesses_all_new_tasks():
    peer = FakeMCP()
    seen = []

    async def callback(context):
        key = context.tasks[0]["task_id"]
        seen.append(key)
        if key == "b":
            return ReadSourcesAction(sources=[{"type": "dart", "rcept_no": "20250302000002",
                                               "text_offset": 1000, "text_chars": 30000}])
        return await always_submit(context)

    result = execute(peer, callback)
    assert result.status == "complete" and result.counts["accepted"] == 2
    assert result.counts["task_invalidations"] == 1
    assert seen == ["a", "b", "a-read", "b-read"]
    read_call = peer.calls[2]
    assert "guideline_assessments" not in read_call
    assert read_call["guideline_harness"]["expected_run_id"] == "e" * 64
    assert "filing:20250302000002" in peer.calls[3]["guideline_harness"]["expected_sources"]
    assert all(item["task_id"].endswith("-read") for item in peer.calls[-1]["guideline_assessments"])


@pytest.mark.parametrize("action", [
    {"action": "call_tool", "tool": "send_vote", "arguments": {}},
    {"action": "read_sources", "sources": [{"type": "kind", "url": "https://evil.example/secret"}]},
    {"action": "read_sources", "sources": [{"type": "dart", "rcept_no": "20250302000002",
                                             "include_after_meeting": True}]},
    {"action": "submit", "assessments": [assessment("someone-else")]},
])
def test_model_action_allowlist_cannot_change_scope_or_call_other_tools(action):
    peer = FakeMCP(candidates=("a",))

    async def callback(context):
        context.binding["continuation"]["cutoff_at"] = "2099-01-01T00:00:00Z"
        return action

    result = execute(peer, callback, RunBudget(max_attempts_per_task=1))
    assert result.status == "partial" and len(peer.calls) == 1
    assert peer.calls[0]["guideline_harness"]["cutoff_at"] == request().cutoff_at


@pytest.mark.parametrize("change,code", [
    ("source", "source_changed"), ("policy", "policy_changed"),
    ("run", "run_changed"), ("cutoff", "invalid_continuation"),
])
def test_changed_binding_stops_before_another_model_reads_it(change, code):
    result = execute(FakeMCP(change=change))
    assert result.status == "stopped" and result.stop_reason == code
    assert result.counts["model_calls"] == (0 if change == "cutoff" else 1)
    assert result.payload is None and result.counts["accepted"] == 0


def test_previously_frozen_packet_can_be_required_for_another_model():
    peer = FakeMCP(candidates=("a",))
    first = execute(peer)
    continuation = first.payload["data"]["guideline_harness"]["continuation"]
    second_peer = FakeMCP(candidates=("a",))
    frozen_request = request(**{key: continuation[key] for key in (
        "expected_run_id", "expected_policy_sha256", "expected_sources")})
    second = asyncio.run(VotingHarness(second_peer,
        CallbackModelAdapter("provider-b", "v2", always_submit)).run(frozen_request))
    assert second.status == "complete"
    assert second_peer.calls[0]["guideline_harness"] == continuation


def test_temporarily_unavailable_source_is_skipped_but_its_original_hash_remains_pinned():
    class PartialPeer(FakeMCP):
        async def call_tool(self, name, arguments):
            result = await super().call_tool(name, arguments)
            harness = result["data"]["guideline_harness"]
            old = arguments["guideline_harness"].get("expected_sources", {})
            if len(self.calls) == 1:
                harness["source_manifest"]["optional-source"] = "f" * 64
            harness["unavailable_previous_sources"] = sorted(set(old) - set(harness["source_manifest"]))
            harness["continuation"]["expected_sources"] = {**old, **harness["source_manifest"]}
            return result

    peer = PartialPeer()
    result = execute(peer)
    assert result.status == "complete" and result.counts["accepted"] == 2
    assert peer.calls[-1]["guideline_harness"]["expected_sources"]["optional-source"] == "f" * 64
    assert result.payload["data"]["guideline_harness"]["unavailable_previous_sources"] == ["optional-source"]


def test_missing_criterion_can_be_submitted_alongside_other_supported_judgments():
    async def callback(context):
        item = assessment(context.tasks[0]["task_id"])
        item["appointment"].update(value="unknown", unresolved=["공개 원문에 선임구분 미기재"],
                                   unresolved_kind="missing_information")
        return SubmitAction(assessments=[item])

    peer = FakeMCP(candidates=("a",))
    result = execute(peer, callback)
    assert result.counts["submitted"] == 1
    assert peer.calls[-1]["guideline_assessments"][0]["appointment"]["unresolved_kind"] == "missing_information"


def test_model_timeout_leaves_one_task_unassessed_and_allows_peer_to_continue():
    async def callback(context):
        if context.tasks[0]["task_id"] == "a":
            await asyncio.Event().wait()
        return await always_submit(context)

    result = execute(callback=callback, budget=RunBudget(max_attempts_per_task=1, model_timeout_seconds=0.01))
    assert result.status == "partial" and result.counts["accepted"] == 1


def test_total_model_and_tool_budgets_keep_other_tasks_unassessed():
    result = execute(budget=RunBudget(max_model_rounds=1))
    assert result.status == "partial" and result.stop_reason == "model_budget_exhausted"
    assert result.counts["accepted"] == 1 and result.counts["unassessed"] == 1
    result = execute(budget=RunBudget(max_tool_calls=1))
    assert result.stop_reason == "tool_budget_exhausted" and result.counts["tool_calls"] == 1


def test_missing_information_finish_does_not_invent_an_assessment_or_block_peers():
    async def callback(context):
        if context.tasks[0]["task_id"] == "a":
            return FinishAction(reason="information_unavailable")
        return await always_submit(context)

    result = execute(callback=callback)
    assert result.task_states["a"] == "unassessed_information_unavailable"
    assert result.task_states["b"] == "accepted_unreviewed"
    assert result.counts["submitted"] == 1


@pytest.mark.parametrize("changes", [
    {"cutoff_at": "2025-03-18"}, {"cutoff_at": "2025-03-18T18:00:00"},
    {"meeting_type": "auto"}, {"notice_rcept_no": "bad"}, {"year": True},
    {"workflow": {"automation": "send_vote"}},
])
def test_request_requires_explicit_time_zone_and_meeting(changes):
    with pytest.raises(ValueError, match="^invalid_harness_request$"):
        request(**changes).arguments()


def test_equivalent_utc_cutoff_is_canonicalized_to_server_korean_time():
    assert request(cutoff_at="2025-03-18T09:00Z").arguments()["guideline_harness"]["cutoff_at"] == request().cutoff_at


def test_empty_supported_scope_is_not_fabricated_into_candidate_assessments():
    result = execute(FakeMCP(candidates=()))
    assert result.stop_reason == "no_supported_tasks" and result.counts["model_calls"] == 0


def test_sdk_transport_rejects_write_tools_before_opening_a_connection():
    transport = StreamableHTTPTransport("https://unused.invalid/mcp")
    with pytest.raises(TransportError, match="^tool_not_allowed$"):
        asyncio.run(transport.call_tool("send_vote", {}))


def test_sdk_json_text_payload_and_remote_error_redaction():
    class Result:
        isError = False
        structuredContent = {"result": '{"status":"ok","data":{}}'}
        content = []

    assert _payload(Result())["status"] == "ok"
    Result.isError = True
    with pytest.raises(TransportError, match="^mcp_tool_error$"):
        _payload(Result())


def test_actual_sdk2_structured_payload_and_error_fields():
    from mcp.types import CallToolResult

    result = CallToolResult(content=[], structured_content={"status": "ok", "data": {}})
    assert _payload(result)["status"] == "ok"
    result.is_error = True
    with pytest.raises(TransportError, match="^mcp_tool_error$"):
        _payload(result)


@pytest.mark.parametrize("mistake,reason", [
    ("citation", "인용을 현재 패킷의 원문에서 확인하지 못했습니다."),
    ("exception", "unknown 평가에 미확인 사항이 없습니다."),
])
def test_server_validation_reason_reaches_only_rejected_task_and_enables_correction(mistake, reason):
    from open_proxy_mcp.services.guideline_assessment import GuidelineAssessment, accept_assessment

    class ValidatingPeer(FakeMCP):
        async def call_tool(self, name, arguments):
            result = await super().call_tool(name, arguments)
            submitted = {item["task_id"]: item for item in arguments.get("guideline_assessments", [])}
            for row in result["data"]["agenda_decisions"]:
                trace = row["guideline_trace"]
                task = trace["assessment_task"]
                task["sources"][0]["excerpts"] = ["합성 후보의 선임 사실을 확인하는 테스트 원문입니다."]
                task["attendance_period"] = {"status": "resolved", "start": "2024-01-01", "end": "2024-12-31"}
                item = submitted.get(task["task_id"])
                trace["llm_assessment"] = accept_assessment(
                    task, GuidelineAssessment.model_validate(item) if item else None)
            return result

    seen = []

    async def correct_after_feedback(context):
        task = context.tasks[0]
        key = task["task_id"]
        seen.append((key, context.feedback))
        item = assessment(key)
        ref = {"source_id": task["sources"][0]["source_id"], "quote": task["sources"][0]["excerpts"][0]}
        item["appointment"]["evidence_refs"] = [ref]
        item["independence"]["evidence_refs"] = [ref]
        if key == "a":
            if mistake == "citation" and not context.feedback:
                item["appointment"]["evidence_refs"] = [{**ref, "quote": "현재 근거에 없는 잘못된 인용문입니다."}]
            if mistake == "exception":
                unknown = {"value": "unknown", "rationale": "원문에서 해당 정보를 확인하지 못함",
                           "evidence_refs": [], "counterevidence": [], "unresolved_kind": "missing_information"}
                item["attendance"] = {
                    **unknown, "unresolved": ["회의별 출석 미기재"],
                    "period_start": "2024-01-01", "period_end": "2024-12-31",
                    "all_board_meetings_covered": False, "service_intervals": [],
                    "legal_suspension_intervals": [], "meetings": [],
                    "exception": {**unknown, "unresolved": ["불참 예외 소명자료 없음"] if context.feedback else []},
                }
            if context.feedback:
                assert context.feedback == ("server_rejected_assessment", "assessment_validation_reason: " + reason)
        else:
            assert context.feedback == ()
        return SubmitAction(assessments=[item])

    result = execute(ValidatingPeer(), correct_after_feedback)
    assert [key for key, _ in seen] == ["a", "b", "a"]
    assert all(isinstance(feedback, tuple) and all(type(item) is str for item in feedback)
               for _, feedback in seen)
    assert result.status == "complete"
    assert result.counts["submitted"] == 3
    assert result.counts["rejected"] == 1 and result.counts["accepted"] == 2
    assert result.counts["model_errors"] == 0 and result.ballots_submitted == 0


@pytest.mark.parametrize("reason", [
    None, "", {"reason": "인용을 현재 패킷의 원문에서 확인하지 못했습니다."},
    "원문" * 200, "RuntimeError: synthetic-private-value",
    "https://invalid.example/?api_key=synthetic-private-value",
    "인용을 현재 패킷의 원문에서 확인하지 못했습니다. synthetic-private-value",
])
def test_rejection_feedback_omits_unrecognized_remote_reason_and_other_fields(reason):
    seen = []

    class UntrustedReasonPeer(FakeMCP):
        async def call_tool(self, name, arguments):
            result = await super().call_tool(name, arguments)
            trace = result["data"]["agenda_decisions"][0]["guideline_trace"]
            trace["llm_assessment"].update(task_id="a", reason=reason,
                message="synthetic-private-message", error="synthetic-private-exception",
                assessment={"rationale": "synthetic-private-assessment"})
            return result

    async def callback(context):
        seen.append(context.feedback)
        return await always_submit(context)

    result = execute(UntrustedReasonPeer(candidates=("a",), reject={"a"}), callback,
                     RunBudget(max_attempts_per_task=2))
    assert seen == [(), ("server_rejected_assessment",)]
    assert result.counts["rejected"] == 2


@pytest.mark.parametrize("state,result_task", [("pending", "a"), ("rejected", "b"), ("rejected", None)])
def test_rejection_reason_requires_rejected_status_and_matching_result_task(state, result_task):
    from open_proxy_mcp.harness.runner import _assessment_rejection_feedback

    trace = {"assessment_task": {"task_id": "a"}, "llm_assessment": {
        "status": state, "task_id": result_task,
        "reason": "인용을 현재 패킷의 원문에서 확인하지 못했습니다.",
    }}
    payload = {"data": {"agenda_decisions": [{"guideline_trace": trace}]}}
    assert _assessment_rejection_feedback(payload, "a") == ("server_rejected_assessment",)
    assert _assessment_rejection_feedback(payload, "b") == ("server_rejected_assessment",)


class ResearchPeer(FakeMCP):
    """Synthetic transport peer with per-candidate packet and persistent raw pins."""
    async def call_tool(self, name, arguments):
        result = await super().call_tool(name, arguments)
        data = result["data"]
        binding = data["guideline_harness"]
        prior = arguments["guideline_harness"].get("expected_sources", {})
        binding["continuation"]["expected_sources"] = {**prior, **binding["source_manifest"]}
        binding["unavailable_previous_sources"] = sorted(set(prior) - set(binding["source_manifest"]))
        submitted = {item["task_id"] for item in arguments.get("guideline_assessments", [])}
        reads = arguments.get("guideline_evidence_sources", [])
        for row, candidate in zip(data["agenda_decisions"], self.candidates):
            relevant = [source for source in reads
                        if not source.get("candidate_names") or candidate in source["candidate_names"]]
            key = candidate
            if relevant:
                key += ":" + hashlib.sha256(json.dumps(relevant, sort_keys=True).encode()).hexdigest()[:12]
            trace = row["guideline_trace"]
            trace["assessment_task"].update(task_id=key, agenda_title=candidate)
            trace["assessment_task"]["sources"] = [
                {"source_id": "notice:20250301000001", "excerpts": ["합성 원문"]},
                *[{"source_id": "filing:" + source["rcept_no"], "excerpts": ["추가 합성 원문"]}
                  for source in relevant],
            ]
            accepted = key in submitted
            trace["llm_assessment"] = {"task_id": key,
                "status": "accepted_unreviewed" if accepted else "pending"}
            row["decision"] = ("AGAINST" if relevant else "FOR") if accepted else "REVIEW"
            row["voting_workflow"] = {"status": "ready_for_auto" if accepted else "awaiting_assessment"}
        if "guideline_research" in arguments:
            data["guideline_application"]["research_discovery"] = {
                "status": "ok", "filings": [{"rcept_no": "20250302000002", "report_nm": "주주총회소집결의"}],
                "original_evidence_read": False,
            }
        return result


def test_discovery_is_call_scoped_metadata_and_preserves_accepted_peer_and_retry_budget():
    peer = ResearchPeer()
    seen = []
    discovery_rounds = 0

    async def callback(context):
        nonlocal discovery_rounds
        candidate = context.tasks[0]["candidate_name"]
        seen.append(candidate)
        if candidate == "b" and discovery_rounds < 4:
            if discovery_rounds:
                assert len(context.discovery_results) == discovery_rounds
                assert context.discovery_results[-1]["result"]["original_evidence_read"] is False
            discovery_rounds += 1
            return DiscoverSourcesAction(query={"kind": "meeting_resolution", "page": discovery_rounds})
        if candidate == "b":
            assert len(context.discovery_results) == 4
            # The callback gets a copy; it cannot change later replay requests.
            context.discovery_results[0]["query"]["page"] = 999
        return await always_submit(context)

    result = execute(peer, callback, RunBudget(max_attempts_per_task=1))
    assert result.status == "complete" and result.counts["accepted"] == 2
    assert result.counts["discoveries"] == result.counts["research_actions"] == 4
    assert result.counts["source_reads"] == result.counts["task_invalidations"] == 0
    assert seen == ["a", "b", "b", "b", "b", "b"]
    search_calls = [call for call in peer.calls if "guideline_research" in call]
    assert [call["guideline_research"]["page"] for call in search_calls] == [1, 2, 3, 4]
    assert all([item["task_id"] for item in call["guideline_assessments"]] == ["a"] for call in search_calls)
    assert "guideline_research" not in peer.calls[-1]


def test_research_turns_do_not_starve_other_candidates():
    seen = []

    async def callback(context):
        candidate = context.tasks[0]["candidate_name"]
        seen.append(candidate)
        if candidate == "a" and seen.count("a") < 3:
            return DiscoverSourcesAction(query={"kind": "periodic_reports", "page": seen.count("a")})
        return await always_submit(context)

    result = execute(ResearchPeer(), callback, RunBudget(max_attempts_per_task=1))
    assert result.status == "complete"
    assert seen == ["a", "b", "a", "a"]


def test_read_only_reassesses_changed_candidate_packet_and_preserves_accepted_peer():
    peer = ResearchPeer()
    seen = []

    async def callback(context):
        key = context.tasks[0]["task_id"]
        seen.append(key)
        if key == "b":
            return ReadSourcesAction(sources=[{"type": "dart", "rcept_no": "20250302000002",
                                               "candidate_names": ["b"]}])
        return await always_submit(context)

    result = execute(peer, callback, RunBudget(max_attempts_per_task=1))
    assert result.status == "complete" and result.counts["accepted"] == 2
    assert len(seen) == 3 and seen[:2] == ["a", "b"] and seen[-1].startswith("b:")
    assert result.counts["task_invalidations"] == 0
    assert "guideline_assessments" not in peer.calls[2]
    assert [item["task_id"] for item in peer.calls[3]["guideline_assessments"]] == ["a"]
    assert peer.calls[2]["guideline_evidence_sources"][0]["candidate_names"] == ["b"]


def test_same_document_merge_preserves_other_candidates_reading_window_and_assessment():
    peer = ResearchPeer()
    seen = []

    async def callback(context):
        key = context.tasks[0]["task_id"]
        seen.append(key)
        if key in {"a", "b"}:
            return ReadSourcesAction(sources=[{
                "type": "dart", "rcept_no": "20250302000002", "candidate_names": [key],
                "text_offset": 0 if key == "a" else 1000,
            }])
        if context.tasks[0]["candidate_name"] == "b":
            assert len(context.source_history) == 2
            assert all(item["active"] and item["document_sha256"] == "b" * 64
                       for item in context.source_history)
        return await always_submit(context)

    result = execute(peer, callback, RunBudget(max_attempts_per_task=1))
    assert result.status == "complete" and result.counts["accepted"] == 2
    assert len([key for key in seen if key.startswith("a")]) == 2
    assert len([key for key in seen if key.startswith("b")]) == 2
    assert result.counts["task_invalidations"] == 0
    active = peer.calls[-1]["guideline_evidence_sources"]
    assert len(active) == 2 and len({source["rcept_no"] for source in active}) == 1
    assert [(source["candidate_names"], source["text_offset"]) for source in active] == [(["a"], 0), (["b"], 1000)]
    refresh = [item for item in result.events if item["code"] == "evidence_packet_refreshed"][-1]
    assert refresh["discarded_read_windows"] == 0 and refresh["discarded_source_ids"] == []
    assert refresh["active_documents"] == 1 and refresh["active_read_windows"] == 2


def test_mixed_valid_and_unmatched_candidate_windows_do_not_share_warning_status():
    class ScopedPeer(ResearchPeer):
        async def call_tool(self, name, arguments):
            result = await super().call_tool(name, arguments)
            unused = []
            for source in arguments.get("guideline_evidence_sources", []):
                if source["candidate_names"] == ["타인"]:
                    unused.append({"source_id": "filing:" + source["rcept_no"],
                        "candidate_names": source["candidate_names"], "source_request": deepcopy(source),
                        "reason": "untrusted synthetic-private-value"})
            result["data"]["guideline_application"]["unused_candidate_scopes"] = unused
            return result

    read = False

    async def callback(context):
        nonlocal read
        if not read:
            read = True
            return ReadSourcesAction(sources=[
                {"type": "dart", "rcept_no": "20250302000002", "candidate_names": ["타인"], "text_offset": 0},
                {"type": "dart", "rcept_no": "20250302000002", "candidate_names": ["a"], "text_offset": 1000},
            ])
        assert [item["target_status"] for item in context.source_history] == ["not_matched", "candidate_scope"]
        assert all(item["active"] for item in context.source_history)
        assert "synthetic-private-value" not in repr(context)
        return await always_submit(context)

    result = execute(ScopedPeer(candidates=("a",)), callback)
    assert result.status == "complete"
    refresh = next(item for item in result.events if item["code"] == "evidence_packet_refreshed")
    assert len(refresh["unmatched_target_read_requests"]) == 1
    assert refresh["unmatched_target_read_requests"][0]["candidate_names"] == ["타인"]


@pytest.mark.parametrize("limit", ["documents", "document_windows", "all_windows"])
def test_active_source_limits_bound_documents_and_reading_windows_separately(limit):
    def window(document, offset=0):
        return {"type": "dart", "rcept_no": f"2025030200000{document}", "text_offset": offset}

    if limit == "documents":
        allowed = [[window(document) for document in range(1, 6)]]
        excess = window(6)
    elif limit == "document_windows":
        allowed = [[window(1, offset * 1000) for offset in range(5)], [window(1, 5000)]]
        excess = window(1, 6000)
    else:
        allowed = [[window(document, offset * 1000) for offset in range(5)] for document in range(1, 5)]
        excess = window(5)
    action_index = 0

    async def callback(context):
        nonlocal action_index
        action_index += 1
        if action_index <= len(allowed):
            return ReadSourcesAction(sources=allowed[action_index - 1])
        if action_index == len(allowed) + 1:
            return ReadSourcesAction(sources=[excess])
        assert "source_read_not_allowed" in context.feedback
        assert len(context.source_history) == sum(len(batch) for batch in allowed)
        return await always_submit(context)

    peer = ResearchPeer(candidates=("a",))
    result = execute(peer, callback, RunBudget(max_attempts_per_task=1))
    assert result.status == "complete" and result.counts["source_reads"] == len(allowed)
    assert len(peer.calls[-1]["guideline_evidence_sources"]) == sum(len(batch) for batch in allowed)


def test_replace_reports_discarded_windows_and_deduplicates_document_ids():
    step = 0

    async def callback(context):
        nonlocal step
        step += 1
        if step == 1:
            return ReadSourcesAction(sources=[
                {"type": "dart", "rcept_no": "20250302000002", "text_offset": offset}
                for offset in (0, 1000, 2000)])
        if step == 2:
            return ReadSourcesAction(mode="replace", sources=[{"type": "dart", "rcept_no": "20250303000003"}])
        assert len(context.source_history) == 4
        assert sum(item["active"] for item in context.source_history) == 1
        return await always_submit(context)

    result = execute(ResearchPeer(candidates=("a",)), callback)
    assert result.status == "complete"
    refresh = [item for item in result.events if item["code"] == "evidence_packet_refreshed"][-1]
    assert refresh["discarded_read_windows"] == 3
    assert refresh["discarded_source_ids"] == ["filing:20250302000002"]


def test_tool_budget_ending_before_reapplication_does_not_claim_final_payload_accepted():
    async def callback(context):
        if context.tasks[0]["candidate_name"] == "b":
            return ReadSourcesAction(sources=[{"type": "dart", "rcept_no": "20250302000002",
                                               "candidate_names": ["b"]}])
        return await always_submit(context)

    result = execute(ResearchPeer(), callback, RunBudget(max_tool_calls=3))
    assert result.status == "partial" and result.stop_reason == "tool_budget_exhausted"
    assert result.task_states["a"] == "unassessed_pending_reapplication"
    assert result.counts["accepted"] == 0
    assert all(row["guideline_trace"]["llm_assessment"]["status"] == "pending"
               for row in result.payload["data"]["agenda_decisions"])


def test_rejected_unchanged_reapplication_removes_old_completion_and_retries():
    class RejectReapplication(ResearchPeer):
        async def call_tool(self, name, arguments):
            result = await super().call_tool(name, arguments)
            if len(self.calls) == 4:
                trace = result["data"]["agenda_decisions"][0]["guideline_trace"]
                trace["llm_assessment"].update(status="rejected")
            return result

    seen = []

    async def callback(context):
        key = context.tasks[0]["task_id"]
        seen.append((key, context.feedback))
        if key == "b":
            return ReadSourcesAction(sources=[{"type": "dart", "rcept_no": "20250302000002",
                                               "candidate_names": ["b"]}])
        return await always_submit(context)

    result = execute(RejectReapplication(), callback)
    assert result.status == "complete" and result.counts["accepted"] == 2
    assert result.counts["rejected"] == 1
    retried = [feedback for key, feedback in seen if key == "a"]
    assert retried == [(), ("server_rejected_assessment",)]


def test_unmatched_candidate_scope_returns_fixed_feedback_and_safe_read_history():
    class UnmatchedPeer(ResearchPeer):
        async def call_tool(self, name, arguments):
            result = await super().call_tool(name, arguments)
            if any(source.get("candidate_names") == ["타인"]
                   for source in arguments.get("guideline_evidence_sources", [])):
                result["data"]["guideline_application"]["unused_candidate_scopes"] = [{
                    "source_id": "filing:20250302000002", "candidate_names": ["타인"],
                    "reason": "untrusted remote exception synthetic-private-value",
                }]
            return result

    step = 0

    async def callback(context):
        nonlocal step
        step += 1
        if step == 1:
            return ReadSourcesAction(sources=[{"type": "dart", "rcept_no": "20250302000002",
                                               "candidate_names": ["타인"]}])
        if step == 2:
            assert "source_target_not_matched" in context.feedback
            assert context.source_history[0]["target_status"] == "not_matched"
            assert "synthetic-private-value" not in repr(context)
            return ReadSourcesAction(sources=[{"type": "dart", "rcept_no": "20250302000002",
                                               "candidate_names": ["a"]}])
        assert context.source_history[-1]["target_status"] == "candidate_scope"
        return await always_submit(context)

    result = execute(UnmatchedPeer(candidates=("a",)), callback, RunBudget(max_attempts_per_task=1))
    assert result.status == "complete" and result.counts["source_reads"] == 2
    event = next(item for item in result.events if item["code"] == "evidence_packet_refreshed")
    assert event["unmatched_target_source_ids"] == ["filing:20250302000002"]


def test_replace_full_pool_retains_read_options_raw_hashes_and_allows_revisit():
    peer = ResearchPeer(candidates=("a",))
    original = [{"type": "dart", "rcept_no": f"2025030200000{index}"} for index in range(1, 6)]
    replacement = {"type": "dart", "rcept_no": "20250303000006"}
    step = 0

    async def callback(context):
        nonlocal step
        step += 1
        if step == 1:
            return ReadSourcesAction(sources=original)
        if step == 2:
            assert len(context.source_history) == 5
            assert all(item["active"] and item["document_sha256"] == "b" * 64
                       for item in context.source_history)
            return ReadSourcesAction(mode="replace", sources=[replacement])
        if step == 3:
            assert len(context.source_history) == 6
            assert sum(item["active"] for item in context.source_history) == 1
            old = next(item for item in context.source_history if item["request"]["rcept_no"] == "20250302000001")
            assert old["document_sha256"] == "b" * 64 and old["active"] is False
            return ReadSourcesAction(sources=[old["request"]])
        assert len(context.source_history) == 6
        assert sum(item["active"] for item in context.source_history) == 2
        return await always_submit(context)

    result = execute(peer, callback, RunBudget(max_attempts_per_task=1))
    assert result.status == "complete" and result.counts["accepted"] == 1
    assert result.counts["source_reads"] == 3 and result.counts["source_replacements"] == 1
    assert len(peer.calls[2]["guideline_evidence_sources"]) == 1
    assert len(peer.calls[-1]["guideline_harness"]["expected_sources"]) == 7
    assert len(peer.calls[-1]["guideline_evidence_sources"]) == 2
    refreshed = [event for event in result.events if event["code"] == "evidence_packet_refreshed"]
    assert len(refreshed[1]["discarded_source_ids"]) == 5


def test_replaced_source_cannot_return_with_a_changed_raw_hash():
    class ChangedReturn(ResearchPeer):
        async def call_tool(self, name, arguments):
            result = await super().call_tool(name, arguments)
            if len(self.calls) == 4:
                binding = result["data"]["guideline_harness"]
                binding["source_manifest"]["filing:20250302000002"] = "d" * 64
                binding["continuation"]["expected_sources"]["filing:20250302000002"] = "d" * 64
            return result

    step = 0

    async def callback(context):
        nonlocal step
        step += 1
        receipt = "20250303000003" if step == 2 else "20250302000002"
        return ReadSourcesAction(mode="replace", sources=[{"type": "dart", "rcept_no": receipt}])

    result = execute(ChangedReturn(candidates=("a",)), callback, RunBudget(max_attempts_per_task=1))
    assert result.status == "stopped" and result.stop_reason == "source_changed"
    assert result.payload is None and result.counts["accepted"] == 0


def test_accepted_revision_records_actual_vote_change_separately_from_pending_packet():
    peer = ResearchPeer()
    reading = False

    async def callback(context):
        nonlocal reading
        if context.tasks[0]["candidate_name"] == "b" and not reading:
            reading = True
            return ReadSourcesAction(sources=[{"type": "dart", "rcept_no": "20250302000002",
                                               "candidate_names": ["a"]}])
        return await always_submit(context)

    result = execute(peer, callback)
    assert result.status == "complete" and result.counts["accepted"] == 2
    assert result.counts["task_invalidations"] == 1
    assert result.counts["recommendation_changes"] == 1 and result.counts["workflow_changes"] == 0
    revisions = [event for event in result.events if event["code"] == "assessment_accepted" and event["before"]]
    assert len(revisions) == 1
    revision = revisions[0]
    assert revision["candidate_name"] == "a"
    assert revision["before"]["decisions"][0]["recommendation"] == "FOR"
    assert revision["after"]["decisions"][0]["recommendation"] == "AGAINST"
    assert "filing:20250302000002" in revision["after"]["source_ids"]
    assert all(row["recommendation"] != "REVIEW" for row in revision["before"]["decisions"] + revision["after"]["decisions"])


def test_exhausted_research_budget_still_allows_assessment_and_global_loop_is_bounded():
    attempts = 0

    async def callback(context):
        nonlocal attempts
        attempts += 1
        if attempts <= 3:
            return DiscoverSourcesAction(query={"kind": "officer_changes"})
        assert context.remaining_research_actions == 0
        assert "research_budget_exhausted_use_available_evidence" in context.feedback
        return await always_submit(context)

    result = execute(ResearchPeer(candidates=("a",)), callback,
                     RunBudget(max_attempts_per_task=1, max_research_actions=2))
    assert result.status == "complete" and result.counts["discoveries"] == 2
    assert result.counts["model_calls"] == 4 and result.counts["tool_calls"] == 4


@pytest.mark.parametrize("query", [
    {"kind": "all_filings"}, {"kind": "meeting_resolution", "company": "다른회사"},
    {"kind": "meeting_resolution", "page": 21},
    {"kind": "meeting_resolution", "page_count": 101},
])
def test_discovery_query_cannot_expand_company_or_unbounded_search(query):
    peer = ResearchPeer(candidates=("a",))

    async def callback(context):
        return {"action": "discover_sources", "query": query}

    result = execute(peer, callback, RunBudget(max_attempts_per_task=1))
    assert result.status == "partial" and result.counts["discoveries"] == 0
    assert len(peer.calls) == 1
