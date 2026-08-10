# -*- coding: utf-8 -*-
"""좌석 예산 하드 블록 — **찬성 수가 선임 예정 인원을 넘으면 표를 내지 않는다.** network 0콜.

이 산출물은 의견이 아니라 **지시서**다(자본시장법 §152③ — 위임장 용지는 목적사항 항목별로
찬반을 명기한다). 항목 수를 넘는 찬성은 의견이 아니라 **산술 오류**다.

집중투표에서는 중립도 아니다. 상법 §382-2③④ 은 1주당 선임예정 이사 수만큼의 의결권을 주고
최다득표자부터 순차 선임하므로, **6석에 16명을 찬성하면 표가 흩어져 아무도 당선시키지 못한다**
— 찬성 남발이 반대와 같은 효과를 낸다. 260807 실측 고려아연이 그 모양이었다(5인안·6인안
두 시나리오가 각각 자식 후보로 쪼개졌는데 양쪽 전원에 찬성).

그리고 §542의7③(공포 2025-09-09 · 시행 2026-09-10)으로 자산 2조원 이상 상장사는 정관으로
집중투표를 배제할 수 없다 — 이 구조는 곧 예외가 아니라 대형사 표준이 된다.
"""
from __future__ import annotations

import pytest

from open_proxy_mcp.services.proxy_advise import _enforce_seat_budget, _seat_count


def _rows(n, category="director_election", decision="FOR"):
    return [{"agenda_title": f"후보{i}", "agenda_category": category,
             "decision": decision} for i in range(n)]


def _parents(mapping):
    return dict(mapping)


def test_seat_count_reads_the_number_and_ignores_nonsense():
    assert _seat_count("집중투표의 방법으로 이사 5인을 선임하는 건") == 5
    assert _seat_count("사외이사 3명 선임의 건") == 3
    assert _seat_count("이사 선임의 건") is None          # 못 읽으면 None
    assert _seat_count("이사 100인 선임") is None          # 연도·금액 오인 방지


def test_exclusive_scenarios_are_blocked_not_summed():
    """**고려아연 모양.** 5인안·6인안 중 하나만 표결되므로 상한은 max(5,6)=6 인데
    양쪽 전원에 찬성이 나가 11건이 됐다."""
    rows = _rows(11)
    parents = _parents({f"후보{i}": ("집중투표의 방법으로 이사 5인을 선임하는 건" if i < 5
                                    else "집중투표의 방법으로 이사 6인을 선임하는 건")
                        for i in range(11)})
    notes = _enforce_seat_budget(rows, parents)
    assert notes, "좌석 초과를 못 막았다 — 6석에 11표가 그대로 나간다"
    assert all(r["decision"] == "NO_VOTE" for r in rows), (
        "일부만 남기면 우리가 시나리오를 고른 셈이다 — 도구가 할 판단이 아니다")
    assert "6인" in notes[0] and "11" in notes[0]


def test_a_normal_election_is_untouched():
    rows = _rows(3)
    assert _enforce_seat_budget(rows, _parents({f"후보{i}": "이사 3인 선임의 건"
                                                for i in range(3)})) == []
    assert all(r["decision"] == "FOR" for r in rows)


def test_unknown_seat_count_blocks_nothing():
    """**모르면서 막으면 던져야 할 표를 지운다** — 틀린 표와 같은 크기의 사고다.
    좌석 수를 못 읽으면 이 검사는 아무것도 하지 않는다."""
    rows = _rows(9)
    assert _enforce_seat_budget(rows, _parents({f"후보{i}": "이사 선임의 건"
                                                for i in range(9)})) == []
    assert all(r["decision"] == "FOR" for r in rows)


def test_director_and_audit_committee_have_separate_budgets():
    """이사 선임과 감사위원 분리선임(상법 §542-12②)은 **별개 선거**다.
    표를 합치면 정상 주총을 구조 오류로 오판한다."""
    rows = _rows(3) + _rows(1, category="audit_committee_election")
    parents = {**{f"후보{i}": "이사 3인 선임의 건" for i in range(3)}}
    parents["후보0"] = "이사 3인 선임의 건"
    # audit 쪽 행은 이름이 겹치므로 제목으로 직접 좌석을 읽게 둔다
    rows[3]["agenda_title"] = "사외이사인 감사위원 1명 선임의 건"
    assert _enforce_seat_budget(rows, parents) == []
    assert all(r["decision"] == "FOR" for r in rows)


def test_only_for_votes_count_against_the_budget():
    """보류·반대는 표를 쓰지 않는다. 그것까지 세면 REVIEW 가 많은 주총이 막힌다."""
    rows = _rows(2) + _rows(6, decision="REVIEW")
    for i, r in enumerate(rows):
        r["agenda_title"] = f"후보{i}"
    parents = {f"후보{i}": "이사 3인 선임의 건" for i in range(8)}
    assert _enforce_seat_budget(rows, parents) == []


def test_guard_is_wired_into_the_pipeline():
    """함수만 있고 안 부르면 소용없다 — 호출부와 경고 전파를 함께 잠근다."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "open_proxy_mcp" / "services"
           / "proxy_advise.py").read_text(encoding="utf-8")
    assert "seat_budget_notes = _enforce_seat_budget(" in src, "가드를 파이프라인에서 안 부른다"
    assert "envelope_warnings.extend(seat_budget_notes)" in src, (
        "구조 오류가 경고로 안 올라간다 — 사용자는 「이 안건만 보류」로 오독한다")
