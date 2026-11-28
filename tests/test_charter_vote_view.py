from open_proxy_mcp.services.charter_vote_view import charter_meetings


def test_vote_needs_same_date_number_and_unique_current_source():
    def notice(rc, day):
        return {"rcept_no": rc, "charter_document": {"meeting_date": day, "clauses": [
            {"agenda": "2", "subject": "정관 변경", "label": label, "before": "전", "after": "후", "reason": ""}
            for label in ["제6조", "제27조"]]}}

    def result(rc, day, agenda="2"):
        return {"rcept_no": rc, "charter_document": {"meeting_date": day, "votes": [
            {"agenda": agenda, "subject": "정관 일부 변경의 건", "outcome": "가결"}]}}

    day = "2026-08-13"
    proposed = notice("20260720000069", day)
    passed = result("20260813900626", day)
    def ballot(sources):
        return charter_meetings(sources)[0]["ballots"][0]
    group = ballot([proposed, passed])
    assert [c["label"] for c in group["clauses"]] == ["제6조", "제27조"]
    assert group["outcome"] == "가결" and group["result_receipt"] == passed["rcept_no"]
    for sources in [[proposed], [proposed, result("20260813900626", "2026-08-12")],
                    [proposed, result("20260813900626", day, "3")],
                    [proposed, passed, result("20260814900626", day, "2-1")],
                    [proposed, passed, result("20260814900626", day)],
                    [proposed, notice("20260728000001", day), passed]]:
        assert ballot(sources)["result_receipt"] == ""
        assert ballot(sources)["outcome"] == ""
