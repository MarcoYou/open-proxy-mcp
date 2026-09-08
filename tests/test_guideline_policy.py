from copy import deepcopy

from open_proxy_mcp.services.guideline_policy import (
    evaluate_guideline_policy,
    load_guideline_policy,
)


def test_policy_source_is_versioned_and_loadable():
    policy = load_guideline_policy()
    assert policy["id"] == "opm-guideline-v2"
    assert policy["version"] == "0.1.0-pilot"
    assert policy["status"] == "synthetic_demonstrator"


def test_missing_assessment_stays_unresolved():
    policy = load_guideline_policy()
    trace = evaluate_guideline_policy(
        policy,
        {
            "attendance_pct": 70,
            "is_reelection": True,
            "attendance_exception_accepted": False,
            "independence_concern_accepted": None,
            "coverage_complete": False,
        },
    )
    assert {r["rule_id"]: r["state"] for r in trace["rule_results"]} == {
        "DEMO-ATT": "fired",
        "DEMO-IND": "unresolved",
    }
    assert trace["gate"] == "fail"
    assert trace["fired_effects"] == [{"rule_id": "DEMO-ATT", "effect": "oppose"}]


def test_exception_prevents_rule_from_firing():
    policy = load_guideline_policy()
    trace = evaluate_guideline_policy(
        policy,
        {
            "attendance_pct": 70,
            "is_reelection": True,
            "attendance_exception_accepted": True,
            "independence_concern_accepted": False,
            "coverage_complete": True,
        },
    )
    assert trace["fired_effects"] == []
    assert {r["rule_id"]: r["state"] for r in trace["rule_results"]} == {
        "DEMO-ATT": "excepted",
        "DEMO-IND": "not_triggered",
    }
    assert trace["gate"] == "pass"


def test_stricter_parameter_changes_only_the_intended_boundary_case():
    policy = deepcopy(load_guideline_policy())
    policy["parameters"]["attendance_min_pct"]["default"] = 80
    trace = evaluate_guideline_policy(
        policy,
        {
            "attendance_pct": 78,
            "is_reelection": True,
            "attendance_exception_accepted": False,
            "independence_concern_accepted": False,
            "coverage_complete": True,
        },
    )
    assert trace["fired_effects"] == [{"rule_id": "DEMO-ATT", "effect": "oppose"}]
    assert trace["gate"] == "pass"


def test_new_appointment_does_not_apply_prior_term_attendance_rule():
    policy = load_guideline_policy()
    trace = evaluate_guideline_policy(
        policy,
        {
            "attendance_pct": 50,
            "is_reelection": False,
            "attendance_exception_accepted": None,
            "independence_concern_accepted": False,
            "coverage_complete": True,
        },
    )
    states = {r["rule_id"]: r["state"] for r in trace["rule_results"]}
    assert states["DEMO-ATT"] == "not_triggered"
    assert trace["fired_effects"] == []
