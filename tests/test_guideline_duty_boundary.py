"""Meeting-level duty timing on a service boundary; no new product parser.

The real-company regression reads the original cached DART document responses
and reconstructs all 15 FY2025 board meetings. Synthetic cases check arithmetic,
input compatibility, citation requirements and isolation, not judgment quality.
"""
from copy import deepcopy
import gzip
import json
from pathlib import Path
import re

from bs4 import BeautifulSoup
import pytest

from open_proxy_mcp.services.guideline_assessment import (
    GuidelineAssessment, accept_assessment, assessment_metrics, prepare_assessment_batch,
)
from test_guideline_attendance_contract import attendance_case


def assess(task, data):
    valid, errors = prepare_assessment_batch([data])
    if errors:
        return {"status": "rejected", "reason": "schema_rejected"}
    return accept_assessment(task, valid[data["task_id"]])


def with_boundary(value="not_on_duty"):
    task, data = attendance_case()
    row = data["attendance"]["meetings"][0]
    row["attendance"] = "unknown"
    row["duty_eligibility"] = {
        "value": value, "boundary": "service_start",
        "rationale": "합성 경계 계약 검사: 실제 재직 시작일의 첫 회의는 선임 전이다.",
        "evidence_refs": deepcopy(row["evidence_refs"]),
    }
    return task, data


def test_existing_inputs_keep_their_original_denominator_and_decision_metrics():
    task, data = attendance_case()
    accepted = assess(task, data)
    assert accepted["status"] == "accepted_unreviewed"
    calculation = accepted["attendance_calculation"]
    assert calculation["eligible_meetings"] == 3
    assert calculation["attendance_pct"] == pytest.approx(200 / 3)
    assert "unknown_duty_meetings" not in calculation


def test_cited_boundary_exclusion_keeps_the_meeting_and_real_service_date():
    task, data = with_boundary()
    before = deepcopy(data)
    accepted = assess(task, data)
    assert accepted["status"] == "accepted_unreviewed"
    calculation = accepted["attendance_calculation"]
    assert calculation["eligible_meetings"] == 2 and calculation["attended_meetings"] == 1
    assert calculation["excluded_boundary_meetings"] == 1
    assert calculation["attendance_pct"] == 50
    assert data == before
    assert len(accepted["assessment"]["attendance"]["meetings"]) == 3
    assert accepted["assessment"]["attendance"]["service_intervals"][0]["start"] == "2025-01-01"


def test_unknown_meeting_duty_never_becomes_a_confirmed_denominator():
    task, data = with_boundary("unknown")
    accepted = assess(task, data)
    assert accepted["status"] == "accepted_unreviewed"
    calculation = accepted["attendance_calculation"]
    assert calculation["status"] == "unresolved"
    assert calculation["unknown_duty_meetings"] == 1
    assert calculation["attendance_pct"] is None
    assert assessment_metrics(accepted)["attendance_pct"] is None


@pytest.mark.parametrize("mutation", [
    "middle_of_service", "wrong_boundary", "absent", "present", "empty_quote",
    "missing_rationale", "invented_quote", "voting_excluded", "already_outside_service",
])
def test_absence_recusal_and_nonboundary_dates_cannot_be_reclassified(mutation):
    task, data = with_boundary()
    row = data["attendance"]["meetings"][0]
    override = row["duty_eligibility"]
    if mutation == "middle_of_service":
        row["date"] = "2025-01-02"
    elif mutation == "wrong_boundary":
        override["boundary"] = "service_end"
    elif mutation in {"absent", "present"}:
        row["attendance"] = mutation
    elif mutation == "empty_quote":
        override["evidence_refs"] = []
    elif mutation == "missing_rationale":
        override["rationale"] = " "
    elif mutation == "invented_quote":
        override["evidence_refs"][0]["quote"] = "현재 원문에 존재하지 않는 직무 시작 시각에 대한 임의 문장입니다."
    elif mutation == "voting_excluded":
        override["boundary"] = "recusal_or_voting_excluded"
    else:
        data["attendance"]["service_intervals"][0]["start"] = "2025-02-01"
    assert assess(task, data)["status"] == "rejected"


def test_service_end_boundary_can_exclude_only_after_departure_meeting():
    task, data = with_boundary()
    row = data["attendance"]["meetings"][0]
    row["date"] = "2025-12-31"
    row["duty_eligibility"]["boundary"] = "service_end"
    row["duty_eligibility"]["rationale"] = "합성 경계 계약 검사: 실제 재직 종료일의 이 회의는 퇴임 후다."
    accepted = assess(task, data)
    assert accepted["status"] == "accepted_unreviewed"
    assert accepted["attendance_calculation"]["excluded_boundary_meetings"] == 1


def test_wrong_boundary_quote_does_not_reject_another_candidates_valid_submission():
    bad_task, bad = with_boundary()
    bad["attendance"]["meetings"][0]["duty_eligibility"]["evidence_refs"][0]["quote"] = "원문에 없는 재직 경계에 대한 조작된 문장입니다."
    good_task, good = attendance_case()
    good_task["task_id"] += "-other-candidate"
    good["task_id"] = good_task["task_id"]
    valid, errors = prepare_assessment_batch([bad, good])
    assert good["task_id"] in valid
    good_result = accept_assessment(good_task, valid[good["task_id"]])
    assert good_result["status"] == "accepted_unreviewed"
    if bad["task_id"] in valid:
        assert accept_assessment(bad_task, valid[bad["task_id"]])["status"] == "rejected"
    else:
        assert errors


@pytest.mark.parametrize("appointment,unresolved,expected", [
    ("new", [], "not_applicable"),
    ("renewed", [], "unresolved"),
    ("new", ["선임구분에 상충하는 정보 존재"], "unresolved"),
    ("unknown", ["선임구분 미확인"], "unresolved"),
])
def test_only_accepted_and_resolved_new_appointment_gets_inapplicable_attendance(appointment, unresolved, expected):
    task, data = attendance_case()
    data["attendance"] = None
    data["appointment"].update(value=appointment, unresolved=unresolved)
    accepted = assess(task, data)
    assert accepted["status"] == "accepted_unreviewed"
    assert accepted["assessment"]["attendance"] is None
    assert accepted["attendance_calculation"]["status"] == expected
    assert accepted["attendance_calculation"]["attendance_pct"] is None
    assert assessment_metrics(accepted)["attendance_pct"] is None


def test_rejected_new_appointment_does_not_get_inapplicable_attendance():
    task, data = attendance_case()
    data["attendance"] = None
    data["appointment"].update(value="new", evidence_refs=[])
    accepted = assess(task, data)
    assert accepted["status"] == "rejected"
    assert "attendance_calculation" not in accepted


def test_rendered_new_appointment_explains_inapplicability_without_fake_counts():
    from open_proxy_mcp.tools.proxy_advise_before_meeting import _render

    task, data = attendance_case()
    data["attendance"] = None
    data["appointment"]["value"] = "new"
    accepted = assess(task, data)
    rendered = _render({"status": "ok", "subject": "fixture", "data": {
        "year": 2026, "agenda_decisions": [{"agenda_title": "신규 후보 선임", "decision": "FOR",
            "guideline_trace": {"mode": "pilot", "llm_assessment": accepted}}]}})
    line = next(line for line in rendered.splitlines() if line.startswith("- 출석 평가:"))
    assert "적용 대상 아님" in line
    assert "None%" not in line and "미제출" not in line


def _cached_document(receipt):
    from open_proxy_mcp.dart.client import _DISK_CACHE_DIR
    for suffix in (".json.gz", ".json"):
        path = Path(_DISK_CACHE_DIR) / (receipt + suffix)
        if path.is_file():
            with (gzip.open(path, "rt", encoding="utf-8") if suffix.endswith(".gz") else path.open()) as stream:
                return json.load(stream)
    pytest.skip("Required public DART document-response cache is unavailable; no network fallback")


def _normalized(text):
    return re.sub(r"\s+", " ", text).strip()


def _meeting_rows(table):
    rows = {}
    for tr in table.find_all(lambda tag: tag.name and tag.name.lower() == "tr"):
        cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(
            lambda tag: tag.name and tag.name.lower() in {"td", "te", "th"}, recursive=False)]
        if cells and cells[0].isdigit() and re.fullmatch(r"2025\.\d{2}\.\d{2}", cells[1]):
            rows[int(cells[0])] = cells
    return rows


@pytest.fixture
def youngpoong_whole_year_case():
    """All annual board rows stay in the input; no meetings or dates are hidden."""
    annual_receipt, notice_receipt = "20260317000753", "20260310003081"
    annual, notice = _cached_document(annual_receipt), _cached_document(notice_receipt)
    annual_text, notice_text = _normalized(annual["text"]), _normalized(notice["text"])
    soup = BeautifulSoup(annual["html"], "xml")
    tables = soup.find_all(lambda tag: tag.name and tag.name.lower() == "table")
    old = min((table for table in tables if all(term in table.get_text(" ", strip=True)
               for term in ["회차", "박영민", "주주총회 의장 대행인 결정의 건"])),
              key=lambda table: len(table.get_text()))
    new = min((table for table in tables if all(term in table.get_text(" ", strip=True)
               for term in ["회차", "전영준", "이사회 의장 선임의 건"])),
              key=lambda table: len(table.get_text()))
    old_rows, new_rows = _meeting_rows(old), _meeting_rows(new)
    assert set(old_rows) == set(range(1, 11))
    assert set(new_rows) == set(range(11, 16))
    assert old_rows[10][1] == new_rows[11][1] == "2025.03.27"
    assert all(row[-1] == "참석" for row in new_rows.values())
    appointment_quote = re.search(
        r"2025년 3월 27일 개최한 제74기 정기주주총회에서.{1,200}?전영준을 신규 선임하였으며",
        annual_text).group()
    no_duty_quote = re.search(
        r"10 2025\.03\.27 주주총회 의장 대행인 결정의 건 찬성 찬성 찬성 미해당",
        notice_text).group()
    annual_source, notice_source = "annual:" + annual_receipt, "notice:" + notice_receipt
    appointment_refs = [{"source_id": annual_source, "quote": appointment_quote}]
    meetings = []
    for number, cells in sorted({**old_rows, **new_rows}.items()):
        quote = _normalized(" ".join(cells))
        assert quote in annual_text
        # Before the appointment there is no Jeon column; this is not an absence.
        meetings.append({"meeting_id": f"2025-{number}", "date": cells[1].replace(".", "-"),
                         "attendance": "present" if number >= 11 else "unknown",
                         "evidence_refs": [{"source_id": annual_source, "quote": quote}]})
    meetings[9]["duty_eligibility"] = {
        "value": "not_on_duty", "boundary": "service_start",
        "rationale": "실제 선임일 3월 27일 제10회는 선임 전 미해당이며, 같은 날 제11회부터 참석한다.",
        "evidence_refs": [*appointment_refs, {"source_id": notice_source, "quote": no_duty_quote}],
    }
    task = {"task_id": "youngpoong-cached-annual-duty-boundary", "as_of": "2026-03-24",
            "candidate_name": "전영준", "attendance_period": {
                "status": "resolved", "start": "2025-01-01", "end": "2025-12-31"},
            "sources": [{"source_id": annual_source, "excerpts": [annual_text]},
                        {"source_id": notice_source, "excerpts": [notice_text]}]}
    judgment = {"rationale": "원본에 실린 회의별 재직·참석 경계 산술 검증", "evidence_refs": appointment_refs,
                "counterevidence": [], "unresolved": []}
    unassessed = {**judgment, "value": "unknown", "unresolved": ["이 회귀는 출석 대상 산술만 검사"],
                  "unresolved_kind": "not_assessed"}
    data = {"task_id": task["task_id"], "evaluator": "source-boundary-regression",
            "appointment": deepcopy(unassessed), "independence": deepcopy(unassessed),
            "attendance": {**judgment, "value": "known", "period_start": "2025-01-01",
                "period_end": "2025-12-31", "all_board_meetings_covered": True,
                "service_intervals": [{"start": "2025-03-27", "end": "2025-12-31",
                    "rationale": "원문상 2025년 3월 27일 신규 선임", "evidence_refs": appointment_refs}],
                "legal_suspension_intervals": [], "meetings": meetings,
                "exception": {**judgment, "value": "unknown", "unresolved": ["소명은 이 산술 회귀 범위 밖"]}}}
    return task, data


def test_raw_fifteen_meetings_exclude_nine_prior_days_and_only_the_tenth_boundary_meeting(youngpoong_whole_year_case):
    task, data = youngpoong_whole_year_case
    legacy = deepcopy(data)
    legacy["attendance"]["meetings"][9].pop("duty_eligibility")
    old = assess(task, legacy)["attendance_calculation"]
    assert old["eligible_meetings"] == 6 and old["unknown_meetings"] == 1
    assert old["status"] == "unresolved" and old["attendance_pct"] is None
    accepted = assess(task, data)
    assert accepted["status"] == "accepted_unreviewed"
    calculation = accepted["attendance_calculation"]
    assert calculation["eligible_meetings"] == calculation["attended_meetings"] == 5
    assert calculation["excluded_meetings"] == 10 and calculation["excluded_boundary_meetings"] == 1
    assert calculation["attendance_pct"] == 100
    assert len(accepted["assessment"]["attendance"]["meetings"]) == 15
    assert accepted["assessment"]["attendance"]["service_intervals"][0]["start"] == "2025-03-27"
    assert accepted["human_reviewed"] is False
