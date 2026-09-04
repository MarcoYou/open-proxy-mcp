# -*- coding: utf-8 -*-
"""proxy_advise 산출물 사전 커버리지 — 엔진이 내는 영문 키·enum 값이 한글로 렌더되는가. network 0콜.

260904 라이브 실측(가비아·솔루엠·고려아연 6회)에서 산출물에 그대로 찍힌 영문 두 가지:
  「제목 기반 분류('cash_dividend')」 — `_CATEGORY_KO` 에 배당·자사주·주주제안·구조개편·퇴직금 키가 없었다.
  「파싱 품질 name_match_failed_see_raw」 — `_FACT_VALUE` 에 그 값이 없었다.
둘 다 **producer 가 내는 값을 사전이 못 덮은** 같은 모양의 결함이다. 사전을 손으로 채우는 테스트는
「관찰된 값」만 잡는다(260728 교훈) — producer 소스를 읽어 대조한다.
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import re
import textwrap

import open_proxy_mcp.services.proxy_advise as PA
from open_proxy_mcp.tools import proxy_advise_before_meeting as R

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SNAKE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


def _returned_literals(fn) -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    return {n.value.value for n in ast.walk(tree)
            if isinstance(n, ast.Return) and isinstance(n.value, ast.Constant)
            and isinstance(n.value.value, str)}


def test_category_dictionary_covers_every_engine_category():
    """분류기가 낼 수 있는 카테고리 + 정책 인용 키 전부에 한글 이름이 있어야 한다."""
    produced = _returned_literals(PA._classify_agenda) | set(PA._POLICY_CITATIONS)
    missing = sorted(k for k in produced if k not in PA._CATEGORY_KO)
    assert not missing, f"_CATEGORY_KO 에 없는 카테고리: {missing}"
    for k, v in PA._CATEGORY_KO.items():
        assert not _SNAKE.search(v), f"{k} → {v}"


def _facts_enum_literals() -> dict[str, set[str]]:
    """`facts["<key>"] = "<literal>"` 꼴 대입 전부 — 값이 영문 스네이크면 enum 이다."""
    src = (_ROOT / "open_proxy_mcp" / "services" / "proxy_advise.py").read_text(encoding="utf-8")
    out: dict[str, set[str]] = {}
    for m in re.finditer(r'facts\["([a-z_]+)"\]\s*=\s*"([a-z][a-z0-9_]*)"', src):
        out.setdefault(m.group(1), set()).add(m.group(2))
    return out


def test_fact_value_dictionary_covers_every_enum_literal_assigned_to_facts():
    found = _facts_enum_literals()
    assert found, "facts enum 대입을 하나도 못 읽었다 — 테스트가 무력화됐다"
    missing = sorted(f"{k}={v}" for k, vs in found.items() for v in vs
                     if "_" in v and R._fact_value(k, v) == v)
    assert not missing, f"영문 그대로 렌더되는 facts 값: {missing}"


def test_fact_label_dictionary_covers_every_facts_key():
    src = (_ROOT / "open_proxy_mcp" / "services" / "proxy_advise.py").read_text(encoding="utf-8")
    keys = sorted(set(re.findall(r'facts\["([a-z_0-9]+)"\]', src)))
    assert keys
    missing = [k for k in keys if k not in R._FACT_LABEL]
    assert not missing, f"_FACT_LABEL 에 없는 facts 키(스네이크가 공백 치환으로만 나간다): {missing}"


def test_candidate_table_no_longer_says_five_year_rule_violation():
    """260710 법률 정정: 5년은 OPM 소프트 경보이지 위반할 성문 규정이 없다 — 표 라벨에 남아 있던 잔재."""
    for d in (R._INDEPENDENCE_LABELS, R._FIVE_YEAR_LABELS, R._INDEP_RESULT_KO):
        for k, v in d.items():
            assert "룰 위반" not in v, f"{k} → {v}"


def test_repeated_policy_citation_is_rendered_once_in_full():
    """같은 인용이 자식 안건마다 반복돼 45안건 응답이 상한을 넘겼다(260904 고려아연) — 두 번째부터는 참조만."""
    cite = PA._policy_citation("director_election")
    row = lambda n: {"agenda_title": f"사외이사 {n} 선임의 건", "decision": "FOR", "reason": "r",
                     "facts": {}, "risk_factors": [], "policy_citation": cite, "policy_basis": "-"}
    text = R._render({"status": "ok", "subject": "테스트",
                      "data": {"year": 2026, "agenda_count": 3, "candidates_count": 0,
                               "agenda_decisions": [row(1), row(2), row(3)]}})
    assert text.count(cite) == 1
    assert text.count("1번 안건과 같은 인용") == 2
    assert "§2.4 이사 선임 — 1번 안건과 같은 인용" in text


def test_disqualification_phrase_distinguishes_missing_field_from_clean():
    assert "기재 없음" in PA._disq_phrase("unknown_no_field")
    assert PA._disq_phrase("clean") == "결격사유 없음"
    ev = {"role_type": "사외이사", "disqualification": {"summary": "unknown_no_field"},
          "independence": {"summary": "independent"}, "faithfulness": {}}
    decision, reason = PA._decide_director_election(ev)
    assert decision == "FOR" and "기재 없음" in reason and "해당 없음" not in reason


def test_inside_director_gets_no_independence_risk_but_outside_gets_concurrent_risk():
    """사실 「독립성 평가 비대상」인데 위험 신호 「독립성 우려」(가비아 안상희) · 사실 「겸직 우려」인데
    위험 신호 「없음」(고려아연 이형규) — 두 자기모순을 같은 자리에서 막는다."""
    inside = {"role_type": "기타비상무이사", "disqualification": {"summary": "clean"},
              "independence": {"summary": "concerns"}, "faithfulness": {}}
    risks = PA._extract_risks("director_election", inside, None, None, "기타비상무이사 선임의 건")
    assert not any("독립성 우려" in r for r in risks), risks
    outside = {"role_type": "사외이사", "disqualification": {"summary": "clean"},
               "independence": {"summary": "independent"},
               "faithfulness": {"concurrent_outside_directors": {"summary": "concerns_concurrent", "total": 2}}}
    risks = PA._extract_risks("director_election", outside, None, None, "사외이사 선임의 건")
    assert any("겸직 우려" in r and "2곳" in r for r in risks), risks
    outside_concern = dict(outside, independence={"summary": "concerns"})
    risks = PA._extract_risks("director_election", outside_concern, None, None, "사외이사 선임의 건")
    assert any("독립성 우려" in r for r in risks), risks
