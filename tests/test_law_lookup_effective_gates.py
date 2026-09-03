# -*- coding: utf-8 -*-
"""law_lookup — 조문별 시행 게이트(항 단위, SSOT `law_provisions.json`). network 0콜.

260903 실측(as_of=2026-09-03): §542의12·§542의7 을 조문번호로 조회하면 표 '시행' 열이 「현행」이고
§542의12②(분리선출 2명, 2026-09-10 시행)·§542의7③(정관 배제 금지, 2026-09-10 시행)에 아무 표지도
없었다. 원인 — flag 경로가 **bridge 첫 룰**의 provision 만 봤다. 조문번호 직접 조회는 bridge 가 없어
진입조차 못 했고, 있어도 두 번째 룰(다른 조항)은 버렸다. corpus 스냅샷은 개정 조문을 이미 담고 있어
(상법 법률 2026-03-06 시행본) 전문이 '현행'이어도 그 안의 어떤 항은 as_of 시점 아직 효력이 없다.

여기서 잠그는 것: ① SSOT 조항을 조문번호로 직접 맞춰 항 단위 표지가 붙는다 ② as_of 가 시행일을 넘으면
표지가 사라진다 ③ md 표·항 줄에 「시행예정 YYYY-MM-DD」·「유예 종료 YYYY-MM-DD」가 보인다
④ SSOT `paragraphs` 가 가리키는 항이 원문에 실재한다.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from open_proxy_mcp.services.law_lookup import (
    _apply_provision_gates,
    _provision_paragraphs,
    build_law_lookup_payload,
    load_index,
    provision_gates,
)
from open_proxy_mcp.tools.law_lookup import _render

ROOT = pathlib.Path(__file__).resolve().parent.parent
SSOT = ROOT / "open_proxy_mcp" / "data" / "laws" / "law_provisions.json"


def _top(q: str, as_of: str) -> dict:
    p = build_law_lookup_payload(q, law="상법", as_of=as_of, top_k=3, include_full_text=False)
    r = p["data"]["results"][0]
    assert r["law"] == "상법" and r["article_no"] == q, (q, r["article_no"])
    return r


def _md_row(q: str, as_of: str) -> tuple[str, str]:
    """(표 1행, 상세 블록) — 사용자가 보는 md 그대로."""
    p = build_law_lookup_payload(q, law="상법", as_of=as_of, top_k=3, include_full_text=False)
    md = _render(p)
    row = next(l for l in md.splitlines() if l.startswith("| 1 |"))
    return row, md


# ── 1. 세 조문 × as_of 전/후 — 사용자 지정 회귀 케이스 ─────────────────────────
@pytest.mark.parametrize("article,as_of,para,label", [
    ("제542조의12", "2026-09-03", 2, "시행예정 2026-09-10"),   # 분리선출 감사위원 2명 (2차)
    ("제542조의7", "2026-09-03", 3, "시행예정 2026-09-10"),    # 정관으로 집중투표 배제 금지 (2차)
    ("제382조의3", "2025-07-01", None, "시행예정 2025-07-22"),  # 이사 충실의무 (1차, 조문 전체)
])
def test_pending_paragraph_is_flagged_before_effective_date(article, as_of, para, label):
    r = _top(article, as_of)
    assert r["in_force"] is True or r["in_force"] is None, "SSOT 게이트가 in_force 를 단정하면 안 된다"
    assert any(label in f for f in r.get("flags", [])), r.get("flags")
    assert label in (r.get("gate_summary") or "")
    gates = [g for g in r["provision_gates"] if g["state"] == "pending"]
    assert gates and all(g["label"] == label for g in gates), gates
    if para is None:
        assert r["gate_status"] == "pending"
        assert all(g["paragraphs"] == [] for g in gates)
    else:
        assert r["gate_status"] == "partial_pending"
        assert [g["paragraphs"] for g in gates] == [[para]]
        # 항 옆 표지 — 그 항에만 붙고 다른 항에는 안 붙는다.
        flagged = {h["no"] for h in r["hang"] if h.get("gates")}
        assert flagged == {para}, flagged


@pytest.mark.parametrize("article,as_of", [
    ("제542조의12", "2026-09-10"),   # 시행일 당일부터 현행
    ("제542조의7", "2026-09-10"),
    ("제382조의3", "2026-09-03"),
])
def test_no_pending_flag_on_or_after_effective_date(article, as_of):
    r = _top(article, as_of)
    assert not any("시행예정" in f for f in r.get("flags", [])), r.get("flags")
    assert r.get("gate_status") is None and not r.get("gate_summary")
    assert not any(h.get("gates") for h in r["hang"])
    # SSOT 조항 자체는 여전히 붙어 있다(in_force 상태로) — 「근거는 남기되 표지는 뗀다」.
    assert r["provision_gates"] and all(g["state"] == "in_force" for g in r["provision_gates"])


def test_first_agm_trigger_note_rides_on_pending_flag():
    r = _top("제542조의7", "2026-09-03")
    f = next(f for f in r["flags"] if "③항 시행예정" in f)
    assert "최초 이사선임 주총" in f


def test_other_paragraphs_of_the_same_article_are_not_flagged():
    """§542의12④·⑦(합산 3%, 2026-07-23 시행)은 as_of=2026-09-03 에 이미 현행 — ②만 표지."""
    r = _top("제542조의12", "2026-09-03")
    pending = [g for g in r["provision_gates"] if g["state"] == "pending"]
    in_force = [g for g in r["provision_gates"] if g["state"] == "in_force"]
    assert [g["provision_id"] for g in pending] == ["§542의12_감사위원_분리선출_2차"]
    assert any(g["provision_id"] == "§542의12_감사위원_합산3%_1차" and g["paragraphs"] == [4, 7]
               for g in in_force)
    # 2026-07-01 이면 둘 다 미시행 — 각각 제 날짜로.
    r2 = _top("제542조의12", "2026-07-01")
    labels = {g["provision_id"]: g["label"] for g in r2["provision_gates"]}
    assert labels["§542의12_감사위원_합산3%_1차"] == "시행예정 2026-07-23"
    assert labels["§542의12_감사위원_분리선출_2차"] == "시행예정 2026-09-10"
    assert {h["no"] for h in r2["hang"] if h.get("gates")} == {2, 4, 7}


def test_grace_period_is_labelled_with_obligation_date():
    """§542의8①(독립이사 1/3): 2026-07-23 시행, 요건 구비는 2027-07-23 까지 — 그 사이는 「유예 종료」."""
    r = _top("제542조의8", "2026-09-03")
    assert r["gate_status"] == "grace"
    assert "①항 유예 종료 2027-07-23" in r["gate_summary"]
    assert any("유예 종료 2027-07-23" in f and "위반 아님" in f for f in r["flags"])
    assert {h["no"] for h in r["hang"] if h.get("gates")} == {1}
    r_after = _top("제542조의8", "2027-07-23")
    assert r_after.get("gate_status") is None


# ── 2. md 출력 — 표 '시행' 열 + 항 옆 ⏳ 표지 ────────────────────────────────
def test_md_shows_pending_badge_on_table_and_paragraph():
    row, md = _md_row("제542조의12", "2026-09-03")
    assert "현행 (②항 시행예정 2026-09-10)" in row, row
    assert "- **2항** ⏳ 시행예정 2026-09-10" in md
    assert "- **4항** ⏳" not in md
    row7, md7 = _md_row("제542조의7", "2026-09-03")
    assert "현행 (③항 시행예정 2026-09-10)" in row7, row7
    assert "- **3항** ⏳ 시행예정 2026-09-10" in md7
    assert "최초 이사선임 주총" in md7


def test_md_whole_article_pending_replaces_current_label():
    row, _ = _md_row("제382조의3", "2025-07-01")
    assert "| 시행예정 2025-07-22 |" in row, row
    row_after, md_after = _md_row("제382조의3", "2026-09-03")
    assert "| 현행 |" in row_after and "시행예정" not in md_after


def test_md_grace_badge():
    row, md = _md_row("제542조의8", "2026-09-03")
    assert "현행 (①항 유예 종료 2027-07-23)" in row, row
    assert "- **1항** ⏳ 유예 종료 2027-07-23" in md


# ── 3. 게이트가 새지 않는다 — 시행령·타법·캐시 공유 ───────────────────────────
def test_gates_never_touch_decree_or_other_laws():
    idx = load_index()
    for rec in idx["articles"]:
        if rec.get("law_short") != "상법" or (rec.get("law_tier") or 0) != 0:
            assert provision_gates(rec, "2020-01-01") == [], rec["id"]


def test_gate_annotation_does_not_mutate_shared_index_record():
    """hang 은 인덱스 캐시와 공유되는 리스트 — 복사하지 않고 새기면 다음 질의(다른 as_of)에 표지가 남는다."""
    rec = next(a for a in load_index()["articles"] if a["id"] == "상법:제542조의12")
    before = json.dumps(rec["hang"], ensure_ascii=False)
    item = {"hang": rec["hang"], "ho": rec.get("ho") or {}}
    _apply_provision_gates(item, provision_gates(rec, "2026-09-03"))
    assert any(h.get("gates") for h in item["hang"])
    assert json.dumps(rec["hang"], ensure_ascii=False) == before, "인덱스 레코드가 오염됐다"


def test_deleted_paragraph_keeps_its_own_mark():
    """§542의7④ 는 2차 개정으로 삭제 — 삭제 표지는 그대로, 게이트 표지가 덮어쓰지 않는다."""
    r = _top("제542조의7", "2026-09-03")
    h4 = next(h for h in r["hang"] if h["no"] == 4)
    assert h4["deleted"] and not h4.get("gates")


# ── 4. SSOT `paragraphs` 정합 — 가리키는 항이 원문에 실재한다 ────────────────
def test_ssot_paragraphs_exist_in_corpus():
    data = json.loads(SSOT.read_text(encoding="utf-8"))
    assert "paragraphs" in data["schema"]
    by_id = {a["id"]: a for a in load_index()["articles"]}
    seen = 0
    for prov in data["provisions"]:
        for art, paras in _provision_paragraphs(prov).items():
            rec = by_id.get(f"상법:{art}")
            assert rec, f"{prov['provision_id']}: {art} 가 corpus 에 없다"
            have = {h["no"] for h in rec.get("hang") or []}
            for n in paras:
                assert n in have, f"{prov['provision_id']}: {art} 제{n}항이 원문에 없다({sorted(have)})"
                seen += 1
    assert seen >= 5, "항 단위 조항이 너무 적다 — paragraphs 가 사라졌나"
