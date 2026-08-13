# -*- coding: utf-8 -*-
"""정책 어휘 정합 — 문서 · 정책 JSON · 엔진이 **같은 이름**을 쓰는가. network 0콜.

세 곳이 카테고리 키를 각자 관리한다:
  문서       `open_proxy_mcp/data/guideline/open-proxy-guideline.md` 의 `### 2.N 제목 (key)`
  정책 JSON  `open_proxy_mcp/data/asset_managers/policies/*.json` 의 `voting_rules[key]`
  엔진       `_classify_agenda` 가 내는 category = `_POLICY_CITATIONS` 의 키

260814 실측: 문서와 정책은 **완전히 일치**하는데 엔진만 다른 어휘를 쓴다.
그래서 `_policy_default(policy, category)` 가 엔진 카테고리로 정책을 조회할 때
이름이 다른 것은 **영원히 안 걸린다** — 정책 데이터가 조용히 사문이 된다.
  b_foreign 정책에 `merger: for` · `spin_off: for` 가 설정돼 있지만
  엔진은 `merger_or_restructuring` 으로만 조회해 None 이 돌아온다.

이 테스트는 **현재 어긋난 상태를 고정**한다(ratchet). 새로 벌어지면 실패한다.
알려진 간극을 좁히는 것은 문서·엔진 양쪽을 건드리는 별도 작업이다.
"""
from __future__ import annotations

import glob
import json
import pathlib
import re

import pytest

from open_proxy_mcp.services.proxy_advise import _POLICY_CITATIONS

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_GUIDELINE = _ROOT / "open_proxy_mcp" / "data" / "guideline" / "open-proxy-guideline.md"
_POLICIES = _ROOT / "open_proxy_mcp" / "data" / "asset_managers" / "policies"

#: 엔진 카테고리인데 문서·정책에 같은 이름의 항목이 없는 것.
#: 정책 조회가 항상 case_by_case 로 떨어지므로 **엔진 판정이 그대로 남는다** — 안전한 방향이다.
_ENGINE_ONLY = {
    "audit_compensation", "capital_reduction", "merger_or_restructuring",
    "other", "retirement_pay", "stock_option_grant",
}
#: 문서·정책에 있는데 엔진이 그 이름으로 조회하지 않는 것 = **사문화된 정책 규칙**.
#: `merger`·`spin_off` 는 엔진에서 `merger_or_restructuring` 하나로 합쳐지고,
#: `capital_increase_decrease` 는 `capital_reduction`(감소만) 으로 좁혀지며,
#: `cb_bw` 는 엔진에 카테고리 자체가 없어 `other` 로 흘러간다.
_DOC_ONLY = {"capital_increase_decrease", "cb_bw", "merger", "spin_off"}


def _doc_keys() -> set[str]:
    text = _GUIDELINE.read_text(encoding="utf-8")
    return {m.group(1) for m in
            re.finditer(r"^###\s[0-9.]+\s[^(\n]+\(([a-z_]+)\)", text, re.M)}


def _policy_keys() -> set[str]:
    out: set[str] = set()
    for f in glob.glob(str(_POLICIES / "*.json")):
        try:
            out |= set((json.loads(pathlib.Path(f).read_text(encoding="utf-8"))
                        .get("voting_rules") or {}).keys())
        except Exception:
            continue
    return out


def test_document_and_policy_json_use_the_same_vocabulary():
    """이 둘은 지금 정확히 일치한다 — 벌어지면 정책 문서가 코드와 무관해진다."""
    doc, pol = _doc_keys(), _policy_keys()
    assert doc == pol, f"문서에만 {sorted(doc - pol)} · 정책에만 {sorted(pol - doc)}"


def test_engine_only_categories_are_the_known_set():
    """엔진에만 있는 카테고리가 늘면 정책이 그만큼 더 못 닿는다."""
    extra = set(_POLICY_CITATIONS) - _doc_keys() - _ENGINE_ONLY
    assert not extra, f"새 엔진 카테고리 {sorted(extra)} — 문서·정책에 대응 항목이 없다"


def test_dead_policy_rules_are_the_known_set():
    """**정책에 설정돼 있는데 엔진이 조회하지 않는 키** — 조용히 사문이 된다."""
    dead = _doc_keys() - set(_POLICY_CITATIONS) - _DOC_ONLY
    assert not dead, f"새로 사문화된 정책 키 {sorted(dead)} — 엔진이 이 이름으로 조회하지 않는다"


@pytest.mark.parametrize("key", sorted({"financial_statements", "cash_dividend",
                                        "director_election", "audit_committee_election",
                                        "director_compensation", "articles_amendment",
                                        "treasury_share", "shareholder_proposal"}))
def test_aligned_categories_exist_in_all_three(key):
    """셋이 맞는 8개 — 여기가 깨지면 정책 오버라이드가 통째로 멈춘다."""
    assert key in _doc_keys(), f"{key} 가 정책 문서에서 사라졌다"
    assert key in _policy_keys(), f"{key} 가 정책 JSON 에서 사라졌다"
    assert key in _POLICY_CITATIONS, f"{key} 가 엔진 인용에서 사라졌다"


def test_every_engine_category_has_a_citation():
    """인용이 없으면 응답의 「정책 인용」 줄이 other 로 떨어진다."""
    import open_proxy_mcp.services.proxy_advise as PA
    for key in _POLICY_CITATIONS:
        assert PA._policy_citation(key), key


# ── 260814: 「사문을 살리자」를 시도했다가 되돌린 기록 ────────────────────────
# 엔진 카테고리 → 정책 키 별칭(`merger_or_restructuring` → `merger`+`spin_off`)을 넣어
# 사문화된 정책 규칙을 되살려 봤다. **넣으면 안 된다.**
#
#   지금        합병·분할 → REVIEW   (정책이 안 걸려 엔진 판정이 남는다)
#   별칭 넣으면  합병·분할 → FOR      (b_foreign 의 `merger: for` 가 REVIEW 를 덮는다)
#
# 합병·분할은 되돌릴 수 없는 최상위 안건이고, 260727 에 바로 그 자동 FOR 를 막았다
# (실측: 에이치디현대미포 합병계약·롯데케미칼 분할계획서 둘 다 ✅ FOR 로 나갔다).
# 사문을 살리는 것이 그 구멍을 **다른 문으로 다시 여는** 셈이다.
#
# 뿌리는 REVIEW 가 두 뜻을 겸하는 것이다 —
#   ① 판단이 안 섰다(정책 기본값이 채워도 되는 자리)
#   ② 사람이 봐야 한다(채우면 안 되는 자리)
# 이 둘을 코드가 구분하지 못한다. 구분하기 전에는 별칭을 넣을 수 없다.

def test_dead_policy_rules_stay_dead_until_review_is_split():
    """별칭이 다시 들어오면 이 테스트가 막는다."""
    import inspect
    import open_proxy_mcp.services.proxy_advise as PA
    src = inspect.getsource(PA._policy_default)
    assert "_POLICY_KEY_ALIASES" not in src, (
        "정책 키 별칭이 들어왔다 — 합병·분할이 자동 찬성으로 돌아간다. "
        "REVIEW 의 두 뜻(판단 미정 / 사람 검토 필요)을 먼저 가른 뒤에 다시 시도할 것"
    )
    p = PA._load_vote_style_policy("b_foreign")
    assert PA._policy_default(p, "merger_or_restructuring") is None
