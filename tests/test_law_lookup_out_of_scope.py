"""law_lookup — 범위 밖 질의에 무관 조문 전문을 쏟지 않는다 (260828 U 실사용 지적 A-4).

재현이었던 것: `law_lookup("코스닥 관리종목 지정 시가총액 요건")` 이 status=ambiguous 와 함께
외부감사법 §14 · 자본시장법 §246 · §86 전문을 md 23,513자로 뱉었다. 인덱싱된 4법에 그 요건은
**없다** — 어휘만 겹친 것이다. U: "도구가 『그건 내가 모르는 영역』이라고 말하는 대신 관계없는
조문 전문을 길게 뱉었다. 화면을 한참 넘겼는데 건진 게 없다."

보수적이어야 한다 — **진짜 강하게 매치되는 조문은 그대로 준다.** 아래 두 축을 같이 고정한다.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.services.law_lookup import build_law_lookup_payload  # noqa: E402
from open_proxy_mcp.tools.law_lookup import _render, _render_status  # noqa: E402


def _md(p):
    d = p.get("data") or {}
    if p.get("status") in ("error", "requires_review") and not d.get("results"):
        return _render_status(p)
    return _render(p)


def _fb(p):
    return ((p.get("data") or {}).get("fallback") or {}).get("type")


# ── 1. 거래소 규정 영역 = 범위 밖. 조문을 아예 붙이지 않는다 ────────────────
@pytest.mark.parametrize("q", [
    "코스닥 관리종목 지정 시가총액 요건",
    "상장폐지 실질심사 사유",
    "불성실공시 벌점 부과 기준",
    "정리매매 기간",
])
def test_exchange_rule_queries_return_scope_notice_not_articles(q):
    p = build_law_lookup_payload(q)
    assert _fb(p) == "out_of_corpus_topic", q
    assert p["data"]["results"] == [], "범위 밖이라 말하면서 조문을 붙이면 화면이 정반대를 말한다"
    assert p["data"]["results_suppressed"] is True
    md = _md(p)
    assert len(md) < 2000, f"범위 밖 안내가 {len(md)}자 — 짧아야 한다"
    # 무엇이 있고 무엇이 없는지를 둘 다 말한다
    assert "상법" in md and "자본시장법" in md and "공정거래법" in md and "외부감사법" in md
    assert "거래소" in md


def test_the_exact_query_u_hit_no_longer_dumps_full_text():
    """U 가 실제로 친 질의. 종전 md 23,513자 · 조문 전문 10건."""
    p = build_law_lookup_payload("코스닥 관리종목 지정 시가총액 요건")
    assert not any(r.get("full_text") for r in p["data"]["results"])
    md = _md(p)
    # 법령명은 "이 도구의 범위" 문장에 정당하게 나온다 — 사라져야 할 것은 **그 조문들**이다.
    for art in ("제14조", "제246조", "제86조"):
        assert art not in md, f"{art} 가 아직 화면에 남아 있다"


# ── 2. 강한 매칭은 그대로 — 이걸 깨면 도구가 죽는다 ─────────────────────────
@pytest.mark.parametrize("q", [
    "자본시장법 제147조",
    "상법 제542조의8",
    "집중투표 배제 조항 삭제하면 무슨 법 위반?",
    "전자주주총회 도입 관련 법",
])
def test_strong_matches_still_get_full_text(q):
    p = build_law_lookup_payload(q)
    assert p["data"]["results"], q
    assert any(r.get("full_text") for r in p["data"]["results"]), \
        f"'{q}' 는 강한 매칭인데 전문이 빠졌다 — 트림이 과하다"
    assert p["data"]["full_text_suppressed"] is False


# ── 3. 약한 매칭 — 전문을 상위 몇 건으로 줄이되 버리지 않는다 ────────────────
@pytest.mark.parametrize("q", ["집중투표", "대량보유 보고", "자기주식 소각"])
def test_weak_keyword_matches_keep_a_few_full_texts(q):
    """도구가 스스로 권하는 예시 질의들이다. 통째로 막으면 주 용도가 죽는다."""
    p = build_law_lookup_payload(q)
    d = p["data"]
    n_ft = sum(1 for r in d["results"] if r.get("full_text"))
    assert 0 < n_ft <= 3, f"'{q}' 전문 {n_ft}건 — 상위 몇 건만 남아야 한다"
    assert len(d["results"]) > n_ft, "나머지는 표에만 남는다"
    assert d["full_text_limited_to"] == 3


# ── 4. 용어를 못 알아본 질의 — 말과 화면이 어긋나지 않는다 ──────────────────
def test_unrecognized_terms_do_not_come_with_article_bodies():
    """「법령 용어를 알아보지 못했어요」라고 하면서 조문 20건을 붙이던 것이 A-4 의 실체다."""
    p = build_law_lookup_payload("주식을 쪼개는 거")
    assert _fb(p) in ("too_vague", "too_generic")
    assert not any(r.get("full_text") for r in p["data"]["results"])
    assert all(not r.get("hang") for r in p["data"]["results"]), "항/호도 본문이다"
    assert p["data"]["full_text_suppressed"] is True
    assert len(_md(p)) < 3000


# ── 5. 총 후보 수는 정직하게 남긴다 ─────────────────────────────────────────
def test_total_candidates_is_not_faked_by_the_trim():
    """붙이지 않은 것이지 **없는 것이 아니다.** 그 사실이 payload 에 남아야 한다."""
    p = build_law_lookup_payload("코스닥 관리종목 지정 시가총액 요건")
    assert p["data"]["total_candidates"] > 0
    assert p["data"]["results"] == []
