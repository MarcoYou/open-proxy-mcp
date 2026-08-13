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


# ── 260813 회귀 — 정상 주총을 막던 세 원인 ────────────────────────────────────
# 캐시 574건 재생 실측: 게이트가 63건(10.9%)에서 발동했는데 대부분 정상 주총이었다.
# 아래 셋을 고쳐 3건(0.5%)으로 내렸다. 각 케이스는 실측 접수번호를 근거로 둔다.

def test_bundle_parent_row_is_not_counted_as_a_vote():
    """**묶음 부모는 후보가 아니라 제목이다** — 위임장에서 한 표를 차지하지 않는다.

    실측 20260225004640: 「이사 선임의 건(사내이사 1명)」 + 자식 후보 1명.
    부모까지 세면 좌석 1 대 찬성 2 가 되어 정상 선거가 구조 오류로 찍혔다.
    """
    parent = "이사 선임의 건(사내이사 1명)"
    rows = [{"agenda_title": parent, "agenda_category": "director_election", "decision": "FOR"},
            {"agenda_title": "사내이사 김선종 선임의 건",
             "agenda_category": "director_election", "decision": "FOR"}]
    notes = _enforce_seat_budget(rows, {"사내이사 김선종 선임의 건": parent})
    assert notes == [], "부모 행을 표로 세어 정상 선거를 막았다"
    assert all(r["decision"] == "FOR" for r in rows)


def test_multiple_role_counts_in_one_title_are_summed():
    """실측 20260220000945: 「사외이사 1명, 사내이사 1명, 기타비상무이사 1명」은 3석이다.
    첫 숫자만 읽으면 1석이 되어 찬성 3이 초과가 된다."""
    assert _seat_count("이사 선임의 건(사외이사 1명, 사내이사 1명, 기타비상무이사 1명)") == 3
    assert _seat_count("이사 선임의 건 (사내이사 2명, 사외이사 1명)") == 3


def test_separate_role_elections_are_summed_not_maxed():
    """**역할이 다르면 함께 뽑는 별개 선거**라 상한은 합이다(1+2=3).

    실측 20260220001028: 「사내이사 선임의 건(1명)」과 「사외이사 선임의 건(2명)」.
    전부 max 로 잡던 종전에는 상한 2 대 찬성 3 으로 막혔다.
    진짜 택일(집중투표 5인안/6인안)은 같은 역할이라 여전히 max 로 남는다 — 위 테스트가 지킨다.
    """
    p1, p2 = "사내이사 선임의 건( 1명)", "사외이사 선임의 건(2명)"
    rows = [{"agenda_title": t, "agenda_category": "director_election", "decision": "FOR"}
            for t in ("사내이사 김중기 선임의 건", "사외이사 김용희 선임의 건",
                      "사외이사 장현욱 선임의 건")]
    parents = {"사내이사 김중기 선임의 건": p1,
               "사외이사 김용희 선임의 건": p2, "사외이사 장현욱 선임의 건": p2}
    assert _enforce_seat_budget(rows, parents) == [], "별개 선거를 max 로 합쳐 정상 주총을 막았다"


def test_one_unreadable_election_disables_the_whole_check():
    """**부분 정보로 막지 않는다.** 한 선거의 정원을 못 읽으면 검사를 포기한다.

    실측 20260303001942: 「사내이사 선임의 건」(인원 미표기) 후보 2명 +
    「사외이사 1명 선임의 건」 후보 1명. 읽힌 1석만 상한에 넣으면 찬성 3이 초과가 된다 —
    못 읽은 선거의 후보를 세면서 그 좌석은 안 세는 비대칭이 원인이었다.
    """
    p1, p2 = "사내이사 선임의 건", "사외이사 1명 선임의 건"
    rows = [{"agenda_title": t, "agenda_category": "director_election", "decision": "FOR"}
            for t in ("김희철 사내이사 선임", "윤주영 사내이사 선임", "박찬국 사외이사 선임")]
    parents = {"김희철 사내이사 선임": p1, "윤주영 사내이사 선임": p1,
               "박찬국 사외이사 선임": p2}
    assert _enforce_seat_budget(rows, parents) == [], "정원을 모르는 선거를 부분 정보로 막았다"
