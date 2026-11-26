import pytest

from open_proxy_mcp.services.control_severance import retirement_signals
from open_proxy_mcp.services.governance_patterns import inspect_filing
from open_proxy_mcp.services.proxy_advise import _articles_body_risks, _cumulative_voting_threshold, _decide_retirement_pay
from open_proxy_mcp.services.screener import pattern_types
from open_proxy_mcp.services.seat_wording import seat_wording, title_seats
from open_proxy_mcp.services.shareholder_meeting import _election_seats_for_group


@pytest.mark.parametrize("words,kind,seats,ceiling", [
    ("이사 10명을 선임한다", "fixed", 10, 10),
    ("이사 10명을 선임할 수 있다", "permitted", None, None),
    ("이사 10명을 뽑을 수 있다", "permitted", None, None),
    ("이사 10명까지 선임할 수 있다", "maximum", None, 10),
    ("최대 10명의 이사를 선임한다", "maximum", None, 10),
    ("이사 선임의 건(10명 이내)", "maximum", None, 10),
    ("이사 3명 이상 10명 이하를 선임한다", "range", None, 10),
    ("이사 3명 이상을 선임한다", "minimum", None, None),
])
def test_election_size_and_ceiling(words, kind, seats, ceiling):
    signal = seat_wording(words, [], conditional=True)
    assert signal["seat_mentions"][0]["kind"] == kind
    assert title_seats(words) == seats and title_seats(words, upper_bound=True) == ceiling
    if seats is None:
        assert _cumulative_voting_threshold("집중투표 " + words) is None
        assert _election_seats_for_group(words, "다른 안건의 다득표 후보 2명") == (None, None, None)


def test_change_of_one_word_is_visible_without_turning_cap_into_election_result():
    old, new = "이사는 10명으로 한다", "이사는 10명 이내로 한다"
    row = dict(clause="제28조", before=old, after=new)
    signal = seat_wording(old + " " + new, [row])
    change, = signal["seat_changes"]
    assert [change[s][0]["kind"] for s in ("before", "after")] == ["fixed", "maximum"]
    assert not signal["seat_mentions"] and change["after"][0]["scope"] == "board"
    risks = _articles_body_risks(row)
    assert any(old in s and new in s for s in risks) and not any("상한 신설" in s for s in risks)
    assert seat_wording(old + " " + new, [{**row, "before": new}]) is None
    assert title_seats("이사 수 상한이 19인임을 전제로 한 집중투표에 의한 이사 8인 선임의 건") == 8
    assert not seat_wording("이사회 참석 이사 10명. 임기 3년. 보수한도는 10억원이다.", [])


def limits(before, after):
    amendments = [{"clause": "제8조", "before": before, "after": after}]
    return retirement_signals(before + " " + after, amendments=amendments)["retirement_limit"]["provisions"][0]


def test_retirement_limits_without_a_control_event():
    old = "제8조 임원 퇴직금의 별도 한도는 연간 10억원으로 한다."
    new = old.replace("10억원", "20억원")
    item = limits("(신설)", old)
    assert item["change"] == "new" and item["limit_amount_text"] == "10억원"
    assert limits(old, new)["change_notes"] == ["퇴직금 별도 한도 인상안"]
    assert limits(new, old)["change_notes"] == ["퇴직금 별도 한도 인하안"]
    assert limits(old, "(삭제)")["change"] == "removed"
    assert limits(old, "좌동")["change"] == "unchanged"
    assert not limits(old, new.replace("연간", "1인당"))["change_notes"]
    assert _decide_retirement_pay({"amendments": [{"before": old, "after": new}]})[0] == "REVIEW"


def test_exclusions_do_not_invent_a_separate_cap_or_payout():
    text = "연간보수총액을 승인할 때 임원퇴직금에 대하여는 한도적용에서 배제됩니다."
    signal, = inspect_filing("주주총회소집공고", {"text": text})
    item, = signal["provisions"]
    assert item["outside_pay_limit"] and not item["separate_limit"]
    assert item["change"] == "mentioned" and item["limit_amount_text"] is None
    for ordinary in ("이사의 퇴직금은 별도 임원퇴직금지급규정에 따른다.", "이사 퇴직금은 보수한도에서 제외하지 않는다.",
                     "이사에게 지급되는 퇴직금은 보수한도에 포함한다.", "근로자 퇴직금의 별도 한도는 10억원이다."):
        assert not inspect_filing("주주총회소집공고", {"text": ordinary})
    assert pattern_types("퇴직금") == {"control_severance", "retirement_limit"}
    assert pattern_types("이사 정원,별도 한도") == {"seat_wording", "retirement_limit"}


def test_retirement_limit_not_the_general_remuneration_amount():
    excl = "이사 보수한도는 20억 원으로 하며 임원 퇴직급여는 제외한다."
    item = retirement_signals(excl)["retirement_limit"]["provisions"][0]
    assert item["outside_pay_limit"] and item["limit_amount_text"] is None
    assert not retirement_signals("이사 퇴직금은 보수한도에 포함하되 성과급만 제외한다.")
    proposed = "제3호 안건 임원 퇴직금 별도한도 30억원 신설의 건"
    item = retirement_signals(proposed)["retirement_limit"]["provisions"][0]
    assert item["change"] == "new" and item["limit_amount_text"] == "30억원"
