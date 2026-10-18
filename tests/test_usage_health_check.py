from scripts.usage_health_check import evaluate


def test_zero_traffic_is_not_collection_failure():
    assert evaluate({"enabled": True, "basis": "tool_calls.v1", "pending": 0,
                     "oldest_pending_age_s": 0, "status": "ok"})[0] == "OK"


def test_missing_state_is_not_reported_as_zero_traffic():
    assert evaluate(None)[0] == "CHECK_ERROR"


def test_quarantined_rows_need_attention_even_when_nothing_is_pending():
    status, detail = evaluate({"enabled": True, "basis": "tool_calls.v1", "pending": 0,
                               "oldest_pending_age_s": 0, "quarantined": 2, "status": "degraded"})
    assert status == "ATTENTION_NEEDED" and "격리" in detail


def test_pending_write_failure_needs_attention():
    assert evaluate({"enabled": True, "basis": "tool_calls.v1", "pending": 30,
                     "oldest_pending_age_s": 360, "status": "degraded"})[0] == "ATTENTION_NEEDED"
