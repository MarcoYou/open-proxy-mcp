# -*- coding: utf-8 -*-
"""law_lookup 코퍼스 확장(260902, 4법 → 10법) — 확장분이 실제로 찾아지고, 옛 4법 질의는
그대로이며, 확장이 만든 두 장치(법 우선순위·제목 앵커)가 오발화하지 않는다. network 0콜.

확장 커밋 셋은 「적기시정조치·명의신탁·준법감시인이 exact 로 1위」를 실측으로만 확인하고
코드로 잠그지 않았다. harness(N=242)는 4법 질의만 재므로 확장분 회귀는 아무도 못 봤다.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from open_proxy_mcp.services import law_lookup as L  # noqa: E402
from open_proxy_mcp.services.law_lookup import (  # noqa: E402
    _EXPANDED_LAWS,
    _OUT_OF_CORPUS_STATUTES,
    _expanded_law_hint,
    build_law_lookup_payload,
)

EXPANDED_SHORT = {"지배구조법", "상증세법", "금융지주회사법", "금산법", "은행법", "보험업법"}


def _top(p, n=1):
    return [(r["law"], r["article_no"]) for r in p["data"]["results"][:n]]


# ── 1. 코퍼스가 10법이고, 색인·manifest·검사 스크립트가 같은 목록을 본다 ───────
def test_corpus_index_carries_all_ten_laws():
    idx = json.loads((ROOT / "wiki/rules/laws/corpus/law_index.json").read_text(encoding="utf-8"))
    laws = {a["law_short"] for a in idx["articles"]}
    assert EXPANDED_SHORT <= laws, f"확장 6법이 색인에 없다: {EXPANDED_SHORT - laws}"
    assert {"상법", "자본시장법", "공정거래법", "외부감사법"} <= laws
    manifest = json.loads((ROOT / "wiki/rules/laws/corpus/_manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["files"]) == 20, "10법 × (법률+시행령) = 20파일"


def test_integrity_check_covers_every_target_law():
    """검사 스크립트가 4법을 하드코딩한 채 「OK: 8개 파일 온전」을 찍던 것(260902)의 회귀 가드.
    법 목록은 sync_law_corpus.TARGETS 하나여야 하고, 검사 결과는 그 수만큼이어야 한다."""
    from sync_law_corpus import TARGETS
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_law_corpus_integrity.py")],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    expected = len(TARGETS) * 2
    assert f"OK: {expected}/{expected}개 파일 온전" in r.stdout, r.stdout
    assert expected >= 20


def test_expanded_laws_are_not_listed_as_out_of_corpus():
    """코퍼스에 넣은 법을 「범위 밖」 표에서 안 걷어 내면 있는 것을 없다고 말한다."""
    for name in _OUT_OF_CORPUS_STATUTES:
        for short in EXPANDED_SHORT | {"상법", "자본시장법", "공정거래법", "외부감사법"}:
            assert short not in name, f"{name} 는 코퍼스에 있는 법인데 범위 밖 표에 남아 있다"
    assert {"지배구조법", "상증세법", "금융지주회사법", "금산법", "은행법", "보험업법"} <= _EXPANDED_LAWS


# ── 2. 확장 6법 질의가 그 법 조문으로 강하게 잡힌다 (확장 커밋의 실측 3건) ─────
@pytest.mark.parametrize("q,law,article", [
    ("적기시정조치 요건", "금산법", "제10조"),
    ("명의신탁 증여의제", "상증세법", "제34조의2"),
    ("준법감시인 임면", "지배구조법", "제25조"),
])
def test_expanded_law_queries_hit_their_article(q, law, article):
    p = build_law_lookup_payload(q)
    assert _top(p)[0] == (law, article), _top(p, 3)
    assert p["data"]["results"][0].get("full_text"), "1위인데 전문이 없다"


# ── 3. 옛 4법 질의는 확장 전과 같은 답 — 넓힌 대가를 옛 질의가 치르지 않는다 ──
@pytest.mark.parametrize("q,law,article", [
    ("집중투표 배제 조항 삭제하면 무슨 법 위반?", "상법", "제542조의7"),
    ("자본시장법 제147조", "자본시장법", "제147조"),
    ("상호출자 금지 조문", "공정거래법", "제21조"),
])
def test_core_law_queries_keep_their_top_hit(q, law, article):
    assert _top(build_law_lookup_payload(q))[0] == (law, article)


def test_core_query_without_domain_cue_does_not_lift_expanded_laws():
    """「감사위원」은 열 법에 다 나온다 — 영역 cue 가 없으면 4법이 먼저다."""
    p = build_law_lookup_payload("감사위원 분리선출 근거 조문")
    top3 = [law for law, _ in _top(p, 3)]
    assert not any(l in EXPANDED_SHORT for l in top3), top3


# ── 4. 법 우선순위 cue 의 오발화 — 글자가 겹치는 것과 가리키는 것은 다르다 ─────
@pytest.mark.parametrize("q", [
    "금융회사 아닌 회사의 사외이사 자격",
    "금융회사가 아닌 일반 상장사 사외이사 결격",
    "금융회사를 제외한 회사의 감사위원 선임",
])
def test_negated_cue_does_not_hint_the_expanded_law(q):
    assert "지배구조법" not in _expanded_law_hint(q), q


def test_negated_query_is_answered_from_core_laws():
    """260902 실측 — 힌트가 켜지면 지배구조법 §6(금융회사 사외이사 자격)이 1위였다."""
    p = build_law_lookup_payload("금융회사 아닌 회사의 사외이사 자격")
    law, _ = _top(p)[0]
    assert law not in EXPANDED_SHORT, _top(p, 3)


@pytest.mark.parametrize("q", [
    "임원배상책임보험 가입 안건",
    "임원 책임보험 가입 승인",
])
def test_liability_insurance_agenda_is_not_an_insurance_act_query(q):
    assert "보험업법" not in _expanded_law_hint(q), q


def test_real_domain_cues_still_hint():
    assert "보험업법" in _expanded_law_hint("보험회사 지급여력비율 기준")
    assert "지배구조법" in _expanded_law_hint("금융회사 사외이사 자격")
    assert "금산법" in _expanded_law_hint("적기시정조치 요건")
    assert _expanded_law_hint("집중투표 배제") == frozenset()


# ── 5. exact 여도 약한 꼬리에는 전문을 붙이지 않는다 ─────────────────────────
def test_exact_status_limits_full_text_to_strong_hits_plus_floor():
    """「적기시정조치 요건」: 강매칭 2건 뒤로 「시정조치」 글자만 겹친 공정거래법 6건이
    전문째 딸려 나와 21KB 였다. 강한 것은 다 주고, 약한 꼬리는 표로만."""
    p = build_law_lookup_payload("적기시정조치 요건")
    d = p["data"]
    assert p["status"] == "exact"
    res = d["results"]
    ft = [r for r in res if r.get("full_text")]
    assert 1 <= len(ft) <= max(3, sum(1 for r in res if r["score"] >= L.TAU_STRONG))
    assert all(r["score"] >= ft[-1]["score"] for r in ft), "전문은 위에서부터 이어져야 한다"
    assert ft[0]["law"] == "금산법"
    assert not any(r.get("full_text") for r in res if r["law"] == "공정거래법"), \
        "글자만 겹친 다른 법 조문에 전문이 붙었다"
    assert d["full_text_suppressed"] is False


def test_exact_status_never_drops_the_strong_hit_full_text():
    for q in ("자본시장법 제147조", "상법 제542조의8", "명의신탁 증여의제"):
        p = build_law_lookup_payload(q)
        assert p["data"]["results"][0].get("full_text"), q
