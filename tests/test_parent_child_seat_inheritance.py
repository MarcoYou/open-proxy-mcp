# -*- coding: utf-8 -*-
"""부모(묶음)의 선거 구조가 자식(후보)에게 내려가는가. network 0콜.

2026-09-04 실측 고려아연(010130) 2026-09-09 임시주총이 출발점이다.
제2호 「집중투표의 방법으로 이사 4인 선임의 건」 부모는 「4석·후보 4명·결격 없음 → ✅」인데
자식 2-1~2-4 는 「몇 명을 뽑는지 읽지 못했습니다 → 경합·⚠️」였다. 제3호도 부모 ✅ · 자식
「둘 다 찬성할 수 없습니다」. **부모가 읽은 정원이 자식에 상속되지 않았다.** 그리고 제2호
후보 4명 중 2명은 독립이사후보추천위원회 추천인데 부모 칸은 「주주」 하나였고, 후보 표 비고는
확인 못 한 것을 「위반: 제안 측 소속 여부」로 적었다.

세 갈래를 한 파일에서 지킨다 —
  ① 관계 판정: 부모 제목의 정원·집중투표 여부가 자식 경합 판정의 입력이다
  ② 안전망: 부모 ✅ · 자식 전원 비-✅ 모순은 산출 전에 부모를 내린다
  ③ 표기: 부모 제안 칸의 후보 분포 · 「미확인」은 「위반」이 아니다
"""
from __future__ import annotations

from open_proxy_mcp.services.proxy_advise import (
    _apply_relation_links,
    _candidate_proposer_mix,
    _reconcile_parent_child_decisions,
)
from open_proxy_mcp.services.shareholder_meeting import (
    _election_seats_for_group,
    agenda_relation_link_label,
    build_agenda_relation_links,
)


def _row(no, title, cat, pt, parent="", parent_title=""):
    return {"number": no, "title": title, "category": cat, "proposer_type": pt,
            "parent_number": parent, "parent_title": parent_title}


# 고려아연 2026-09-09 임시주총 제2호 — 부모 제목이 정원(4)과 집중투표를 말한다.
KZ_PARENT = "집중투표의 방법으로 이사 4인 선임의 건"
KZ_ROWS = [
    _row("제2호", KZ_PARENT, "director_election", "shareholder_proposal"),
    _row("제2-1호", "사외이사 후보 갑 선임의 건", "director_election", "shareholder_proposal", "제2호"),
    _row("제2-2호", "사외이사 후보 을 선임의 건", "director_election", "shareholder_proposal", "제2호"),
    _row("제2-3호", "사외이사 후보 병 선임의 건", "director_election", "company", "제2호"),
    _row("제2-4호", "사외이사 후보 정 선임의 건", "director_election", "company", "제2호"),
]
# 공고 본문에는 「다득표 N개」 같은 문구가 없다 — 종전엔 이것만 찾다가 못 읽었다.
KZ_NOTICE = ("제2호 의안 : 집중투표의 방법으로 이사 4인 선임의 건 [주주제안] "
             "제2-1호 사외이사 후보 갑 제2-2호 사외이사 후보 을 "
             "제2-3호 사외이사 후보 병(독립이사후보추천위원회 추천) 제2-4호 사외이사 후보 정")


# ── ① 관계 판정 ──────────────────────────────────────────────────────────

def test_parent_title_seats_reach_the_children():
    """🔴 부모 「4인 선임」이 정원이다 — 자식은 「몇 명을 뽑는지 못 읽었다」가 될 수 없다."""
    links = build_agenda_relation_links(KZ_ROWS, KZ_NOTICE)
    for no in ("제2-1호", "제2-2호", "제2-3호", "제2-4호"):
        ls = links[no]
        assert not [l for l in ls if l["type"] == "contested"], (
            f"{no}: 4석에 후보 4명은 경합이 아니다 — 찬성할 수 있는 표를 지운다")
        same = [l for l in ls if l["type"] == "same_election"]
        assert same, f"{no}: 제안 주체가 갈린 사실은 관계로 남아야 한다"
        link = same[0]
        assert link["seats"] == 4 and link["candidates"] == 4
        assert link["seats_source"] == "parent_title"
        assert link["cumulative"] is True
        assert "몇 명을 뽑는지" not in link["note"]
        assert "둘 다 찬성할 수 없습니다" not in link["note"]
        assert "전원 찬성이 가능" in link["note"]
        assert "집중투표" in link["note"]
    # 부모 행 자체에는 관계가 붙지 않는다(한쪽 제안뿐인 층).
    assert "제2호" not in links


def test_same_election_label_reads_the_structure():
    links = build_agenda_relation_links(KZ_ROWS, KZ_NOTICE)
    label = agenda_relation_link_label(links["제2-1호"][0])
    assert label == "🤝 제2-3호, 제2-4호와 같은 선거 — 집중투표 4석에 후보 4명, 전원 찬성 가능"


def test_two_seat_parent_with_two_candidates_is_not_a_choice():
    """고려아연 제3호 모양 — 부모 「2인 선임」에 후보 둘(주주 1·이사회 1)이면 둘 다 찬성할 수 있다.
    종전엔 `seats is None and n_cand == 2` 가 「둘 다 찬성할 수 없습니다」로 떨어졌다."""
    rows = [
        _row("제3호", "감사위원회 위원이 되는 사외이사 2인 선임의 건", "audit_committee_election", "company"),
        _row("제3-1호", "사외이사 후보 무 선임의 건", "audit_committee_election", "shareholder_proposal", "제3호"),
        _row("제3-2호", "사외이사 후보 기 선임의 건", "audit_committee_election", "company", "제3호"),
    ]
    links = build_agenda_relation_links(rows, "")
    for no in ("제3-1호", "제3-2호"):
        assert links[no][0]["type"] == "same_election"
        assert links[no][0]["seats"] == 2
        assert "둘 다 찬성할 수 없습니다" not in links[no][0]["note"]


def test_more_candidates_than_cumulative_seats_is_a_real_contest():
    """자리가 후보보다 적으면 여전히 경합이다 — 완화하다가 진짜 표 분산을 놓치면 안 된다.
    집중투표면 「N명까지 찬성」이 아니라 **표를 몰아야 한다**고 말한다."""
    rows = [_row("제2호", KZ_PARENT, "director_election", "shareholder_proposal")] + [
        _row(f"제2-{i}호", f"사외이사 후보 {i} 선임의 건", "director_election",
             "shareholder_proposal" if i <= 3 else "company", "제2호")
        for i in range(1, 7)
    ]
    links = build_agenda_relation_links(rows, "")
    con = [l for l in links["제2-1호"] if l["type"] == "contested"]
    assert con and con[0]["seats"] == 4 and con[0]["candidates"] == 6
    assert con[0]["cumulative"] is True
    assert "표가 흩어져" in con[0]["note"]
    assert "4명을 넘겨 찬성하면" in con[0]["note"]
    assert agenda_relation_link_label(con[0]).startswith("⚔️ ")
    assert "집중투표 4석" in agenda_relation_link_label(con[0])


def test_parent_title_travels_on_the_child_row_when_parent_row_is_absent():
    """호출측이 자식만 넘겨도 `parent_title` 이 실려 있으면 같은 결과다."""
    rows = [_row(r["number"], r["title"], r["category"], r["proposer_type"], r["parent_number"],
                 parent_title=KZ_PARENT) for r in KZ_ROWS[1:]]
    links = build_agenda_relation_links(rows, "")
    assert links["제2-3호"][0]["type"] == "same_election"
    assert links["제2-3호"][0]["seats_source"] == "parent_title"


def test_notice_seats_still_apply_when_parent_says_nothing():
    """부모가 인원을 말하지 않으면 공고 본문의 다득표 규칙이 받는다(한국앤컴퍼니 제4호)."""
    seats, quote, source = _election_seats_for_group(
        "감사위원이 되는 사외이사 선임의 건",
        "결의요건을 충족한 후보가 2인 이상일 경우 찬성률이 높은 후보 순으로 2인의 감사위원이 되는 사외이사를 선임합니다")
    assert (seats, source) == (2, "notice") and quote
    assert _election_seats_for_group("", "") == (None, None, None)


def test_parent_seats_win_over_a_stray_notice_rule():
    """부모 제목이 정원을 말하면 공고 어딘가의 다른 안건 규칙보다 우선한다 — 공고 전체 검색은
    다른 선거의 규칙을 끌어올 수 있다."""
    seats, _, source = _election_seats_for_group(
        KZ_PARENT, "제5호, 제6호 의안에 대해서는 다득표 의안 1개 안건이 가결된 것으로 합니다.")
    assert (seats, source) == (4, "parent_title")


def test_same_election_link_counts_as_a_found_relation_but_does_not_block():
    """막지 않는 관계도 「관계를 찾았다」에 센다 — 안 그러면 정상 선거에 「관계를 판정하지
    못했습니다」가 붙는다. 판정은 그대로 둔다."""
    rows = [
        {"agenda_title": "사외이사 후보 갑 선임의 건", "decision": "FOR", "reason": "결격 없음",
         "agenda_relation_links": [{"type": "same_election", "with": ["제2-3호"], "seats": 4,
                                    "candidates": 4, "cumulative": True, "note": "n"}]},
    ]
    notes = _apply_relation_links(rows)
    assert notes and rows[0]["decision"] == "FOR"
    assert "relation_downgraded_from" not in rows[0]
    assert rows[0]["agenda_relation_label"].startswith("🤝 ")


# ── ② 안전망: 부모 ✅ · 자식 전원 비-✅ ───────────────────────────────────

def _decisions():
    parent = {"agenda_title": KZ_PARENT, "agenda_category": "director_election",
              "decision": "FOR", "reason": "묶음 안건 — 결격사유 없음, 후보 4명 · 집중투표 4석",
              "risk_factors": []}
    kids = [{"agenda_title": f"사외이사 후보 {n} 선임의 건", "agenda_id": f"2-{i}",
             "agenda_category": "director_election", "decision": "REVIEW",
             "reason": "이 안건은 다른 안건과 묶여 있어 혼자 판정할 수 없습니다.",
             "risk_factors": []} for i, n in enumerate(("갑", "을", "병", "정"), 1)]
    parents = {k["agenda_title"]: KZ_PARENT for k in kids}
    return parent, kids, parents


def test_parent_for_with_all_children_review_is_caught_before_output():
    parent, kids, parents = _decisions()
    notes = _reconcile_parent_child_decisions([parent, *kids], parents)
    assert notes and "4명 전원 비찬성" in notes[0]
    assert parent["decision"] == "REVIEW"
    assert parent["consistency_downgraded_from"] == "FOR"
    assert "보류 4" in parent["reason"]
    assert "자식을 보기 전 판정: FOR" in parent["reason"]
    assert "부모·자식 판정 불일치" in parent["risk_factors"]
    # 자식은 건드리지 않는다 — 자식이 본 사유(경합)를 지우는 일이다.
    assert all(k["decision"] == "REVIEW" for k in kids)


def test_one_child_for_keeps_the_parent():
    parent, kids, parents = _decisions()
    kids[0]["decision"] = "FOR"
    assert _reconcile_parent_child_decisions([parent, *kids], parents) == []
    assert parent["decision"] == "FOR"


def test_reverse_direction_is_left_alone():
    """부모 ⚠️ · 자식 ✅ 는 손대지 않는다 — 부모가 묶음 수준 신호(택일·조건부)를 보고 있다."""
    parent, kids, parents = _decisions()
    parent["decision"] = "REVIEW"
    for k in kids:
        k["decision"] = "FOR"
    assert _reconcile_parent_child_decisions([parent, *kids], parents) == []
    assert parent["decision"] == "REVIEW" and all(k["decision"] == "FOR" for k in kids)


def test_non_election_bundles_are_out_of_scope():
    """정관변경 묶음은 `_decide_articles_amendment(sibling_risks=…)` 가 따로 본다."""
    parent, kids, parents = _decisions()
    parent["agenda_category"] = "articles_amendment"
    assert _reconcile_parent_child_decisions([parent, *kids], parents) == []
    assert parent["decision"] == "FOR"


# ── ③ 표기 ─────────────────────────────────────────────────────────────

def test_parent_carries_the_candidate_proposer_mix():
    parent, kids, parents = _decisions()
    for k, pt in zip(kids, ("shareholder_proposal", "shareholder_proposal", "company", "company")):
        k["proposer_type"] = pt
    parent["proposer_type"] = "shareholder_proposal"
    _candidate_proposer_mix([parent, *kids], parents)
    assert parent["candidate_proposer_mix"] == {"shareholder_proposal": 2, "company": 2}
    assert parent["proposer_type"] == "shareholder_proposal", "마커가 말한 값은 바꾸지 않는다"
    # 전원 같은 쪽이면 붙이지 않는다.
    for k in kids:
        k["proposer_type"] = "company"
    parent.pop("candidate_proposer_mix")
    _candidate_proposer_mix([parent, *kids], parents)
    assert "candidate_proposer_mix" not in parent


def test_render_shows_the_mix_next_to_the_parent_proposer():
    from open_proxy_mcp.tools.proxy_advise_before_meeting import _render
    payload = {"subject": "테스트", "data": {"meeting_info": {}, "agenda_decisions": [
        {"agenda_title": KZ_PARENT, "decision": "FOR", "reason": "r",
         "proposer_type": "shareholder_proposal",
         "candidate_proposer_mix": {"shareholder_proposal": 2, "company": 2}},
    ]}}
    out = _render(payload)
    assert "| **주주** (후보: 주주 2·이사회 2) |" in out


def test_render_says_unconfirmed_not_violated_for_a_nominated_candidate():
    """🔴 「주주가 제안한 후보 — 소속 관계는 확인되지 않음」을 「위반」이라 적지 않는다."""
    from open_proxy_mcp.tools.proxy_advise_before_meeting import _render

    def _cand(name, aff_result, msr="independent"):
        return {"name": name, "role_type": "사외이사", "agenda_action": "신임",
                "independence": {"summary": "proposer_nominated" if aff_result == "proposed_by_shareholder" else "weak_concerns",
                                 "sub_factors": {
                                     "major_shareholder_relation": {"result": msr},
                                     "recent_3y_transactions": {"result": "no_transactions"},
                                     "recent_2y_employee": {"result": "outsider"},
                                     "five_year_rule": {"result": "first_term_or_short"},
                                     "proposer_affiliation": {"result": aff_result}}},
                "disqualification": {"summary": "clean"}, "faithfulness": {}}

    payload = {"subject": "테스트", "data": {"meeting_info": {}, "agenda_decisions": [],
               "candidates_evaluations": [
                   _cand("갑", "proposed_by_shareholder"),
                   _cand("을", "employed_by_proposer"),
                   _cand("병", "proposed_by_shareholder", msr="related"),
               ]}}
    out = _render(payload)
    rows = {ln.split("|")[1].strip(): ln for ln in out.splitlines() if ln.startswith("| ")}
    assert "미확인: 제안 측 소속 여부" in rows["갑"] and "위반" not in rows["갑"]
    assert "위반: 제안 측 소속 여부" in rows["을"]
    assert "위반: 최대주주 관계" in rows["병"]
    assert "미확인: 제안 측 소속 여부" in rows["병"]
