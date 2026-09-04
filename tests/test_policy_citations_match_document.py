# -*- coding: utf-8 -*-
"""정책 인용 라벨 ↔ 정책 문서 절·항목 자동 대조. network 0콜.

`proxy_advise_before_meeting` 의 「정책 인용」 줄은 `_POLICY_CITATIONS`(services/proxy_advise.py)
에서 나온다. 260903 이전에는 손으로 적은 요약 한 줄이었고 「§재무제표」처럼 문서에 없는 절을
가리켰다 — `proxy_guideline(section="이사 선임")` 로 원문을 열어도 그 문장이 없었다.

이제 각 인용은 문서 헤딩 번호(`### 2.4 이사 선임 (director_election)` 의 「2.4」)와 그 절의
`- **against**:` 목록 몇 번째 항목인지를 가리킨다. 이 테스트는 그 참조가 **문서와 실제로 맞는지**를
문서를 파싱해 확인한다. 문서 항목을 고치거나 순서를 바꾸면 여기가 먼저 깨진다 — 라벨을 같이
고치라는 뜻이다(문서·라벨·판정 함수 셋의 수기 동기화 중 문서↔라벨 한 변을 기계가 잡는다).
"""
from __future__ import annotations

import pathlib
import re

import pytest

from open_proxy_mcp.services.proxy_advise import (
    _CONTEXT_CITATIONS,
    _POLICY_CITATIONS,
    _context_citation,
    _policy_citation,
    _render_policy_citation,
)

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_GUIDELINE = _ROOT / "open_proxy_mcp" / "data" / "guideline" / "open-proxy-guideline.md"

_HEADING = re.compile(r"^(#{2,3})\s+([0-9]+(?:\.[0-9]+)?|0-A)\.?\s+(.*?)\s*$", re.M)
_KIND_LINE = re.compile(r"^- \*\*(for|against|review|default)\*\*[^:\n]*:\s*(.*)$")
_SUB_ITEM = re.compile(r"^  - (.*)$")


def _parse_document() -> dict[str, dict]:
    """절 번호 → {title, key, items{kind: [항목 텍스트…]}}.

    항목 규칙: `- **against**:` 줄 뒤의 `  - ` 들여쓰기 줄이 항목이고, 헤더 줄 콜론 뒤에
    본문이 바로 오면(`- **for**: 독립 평가 + …`) 그것이 1번 항목이다.
    """
    text = _GUIDELINE.read_text(encoding="utf-8")
    heads = list(_HEADING.finditer(text))
    out: dict[str, dict] = {}
    for i, m in enumerate(heads):
        number, rest = m.group(2), m.group(3)
        body = text[m.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        km = re.match(r"^(.*?)\s*\(([a-z_]+)\)\s*(?:—.*)?$", rest)
        title = (km.group(1) if km else re.sub(r"\s*—.*$", "", rest)).strip()
        key = km.group(2) if km else None
        items: dict[str, list[str]] = {}
        cur: str | None = None
        for line in body.splitlines():
            h = _KIND_LINE.match(line)
            if h:
                cur = h.group(1)
                items.setdefault(cur, [])
                if h.group(2).strip():
                    items[cur].append(h.group(2).strip())
                continue
            s = _SUB_ITEM.match(line)
            if s and cur:
                items[cur].append(s.group(1).strip())
                continue
            if line.strip() and not line.startswith(" "):
                cur = None
        out[number] = {"title": title, "key": key, "items": items}
    return out


@pytest.fixture(scope="module")
def doc() -> dict[str, dict]:
    d = _parse_document()
    # 파서 자체가 문서를 읽었는지 — 12 카테고리 절이 전부 있어야 한다
    assert {"2.1", "2.4", "2.12", "0-A", "7"} <= set(d), sorted(d)
    assert d["2.4"]["key"] == "director_election"
    assert len(d["2.4"]["items"]["against"]) >= 10, d["2.4"]["items"]
    return d


def _all_specs():
    return [(f"policy:{k}", v) for k, v in _POLICY_CITATIONS.items()] + \
           [(f"context:{k}", v) for k, v in _CONTEXT_CITATIONS.items()]


@pytest.mark.parametrize("name,spec", _all_specs(), ids=[n for n, _ in _all_specs()])
def test_section_and_title_match_the_document_heading(name, spec, doc):
    """절 번호가 문서에 있고 제목이 헤딩과 글자 그대로 같다 — 없는 절(§재무제표)을 못 만든다."""
    sec = spec.get("section")
    if sec is None:
        # 절 없음은 허용하되 라벨이 그렇게 말해야 한다
        assert "해당 절 없음" in _render_policy_citation(spec)
        assert not spec.get("items") and not spec.get("also"), f"{name}: 절이 없는데 항목 참조가 있다"
        return
    assert sec in doc, f"{name}: 문서에 §{sec} 가 없다 — 있는 절: {sorted(doc)}"
    assert doc[sec]["title"] == spec["title"], (
        f"{name}: §{sec} 제목이 문서와 다르다 — 문서 「{doc[sec]['title']}」 / 라벨 「{spec['title']}」")


@pytest.mark.parametrize("category,spec", sorted(_POLICY_CITATIONS.items()), ids=sorted(_POLICY_CITATIONS))
def test_engine_category_points_at_the_matching_document_key(category, spec, doc):
    """문서 헤딩의 (key) 가 엔진 카테고리와 같은 절이면 그 절을 가리켜야 한다.

    엔진만의 카테고리(감자·스톡옵션·주식분할·퇴직금·구조개편)는 파생 절을 가리키되, 그 절이
    이름이 같은 다른 카테고리의 절이면 안 된다(예: 퇴직금이 §2.4 를 가리키면 오류).
    """
    sec = spec.get("section")
    if sec is None:
        return
    doc_keys = {v["key"] for v in doc.values() if v["key"]}
    doc_key = doc[sec]["key"]
    if category in doc_keys:
        assert doc_key == category, f"{category} 는 문서에 자기 절이 있는데 §{sec}({doc_key}) 를 가리킨다"
    else:
        assert doc_key != category  # 자명 — 그러나 파생 절이 엔진 카테고리와 이름이 같으면 위 분기로 갔어야 한다


def _check_item(doc, sec, kind, no, quote, where):
    items = doc[sec]["items"]
    assert kind in items, f"{where}: §{sec} 에 **{kind}** 목록이 없다 — 있는 것: {sorted(items)}"
    assert 1 <= no <= len(items[kind]), (
        f"{where}: §{sec} {kind} 항목이 {len(items[kind])}개인데 {no}번을 가리킨다")
    assert quote in items[kind][no - 1], (
        f"{where}: §{sec} {kind} {no}번 항목에 「{quote}」가 없다 — 실제: 「{items[kind][no - 1]}」")


@pytest.mark.parametrize("name,spec", _all_specs(), ids=[n for n, _ in _all_specs()])
def test_every_item_reference_quotes_the_document_item(name, spec, doc):
    """(kind, 번호, 인용구) 가 그 절의 그 번호 항목 안에 실제로 있다 — 항목 순서가 바뀌면 걸린다."""
    sec = spec.get("section")
    for kind, no, quote in spec.get("items") or []:
        _check_item(doc, sec, kind, no, quote, name)
    for kind, no, quote in spec.get("unused") or []:
        _check_item(doc, sec, kind, no, quote, f"{name}(unused)")
    for other_sec, kind, no, quote in spec.get("also") or []:
        assert other_sec in doc, f"{name}: also 가 없는 절 §{other_sec} 를 가리킨다"
        _check_item(doc, other_sec, kind, no, quote, f"{name}(also)")


def test_rendered_label_carries_section_items_and_engine_verdict():
    """독자가 라벨만 보고 절 → 항목 → 엔진 판정을 이을 수 있어야 한다."""
    s = _policy_citation("director_election")
    assert s.startswith("OPM Guideline §2.4 이사 선임 — ")
    assert "against ①「사외이사 장기연임 5년+」" in s
    assert "④「이사회 출석률 75% 미만」는 엔진 미반영" in s
    assert "▸ 엔진:" in s
    assert "§0-A" in s                       # 정책↔엔진 간극의 공식 지도를 가리킨다
    assert _policy_citation("nonexistent_category") == _policy_citation("other")


def test_no_label_invents_a_section_name():
    """「OPM Guideline §」 뒤에는 문서 절 번호만 온다 — 옛 「§재무제표」식 가짜 절 금지."""
    for k in _POLICY_CITATIONS:
        for m in re.finditer(r"OPM Guideline §(\S+)", _policy_citation(k)):
            assert re.fullmatch(r"(\d+(\.\d+)?|0-A)", m.group(1)), f"{k}: §{m.group(1)}"
    for k in _CONTEXT_CITATIONS:
        for m in re.finditer(r"OPM Guideline §(\S+)", _context_citation(k)):
            assert re.fullmatch(r"(\d+(\.\d+)?|0-A)", m.group(1)), f"{k}: §{m.group(1)}"


def test_labels_carry_no_engine_identifiers():
    """산출물에 스네이크 식별자가 새지 않는다(test_tool_render_no_internal_ids 와 같은 기준)."""
    snake = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
    for k in _POLICY_CITATIONS:
        assert not snake.search(_policy_citation(k)), f"{k}: {_policy_citation(k)}"
    for k in _CONTEXT_CITATIONS:
        assert not snake.search(_context_citation(k)), k


def test_alignment_table_lists_attendance_tenure_and_overboarding(doc):
    """§0-A 정합표에 출석률·장기연임·겸직 행이 있다 (260903) — 엔진이 안 쓰는 정책 항목을 표가 숨기지 않는다."""
    text = _GUIDELINE.read_text(encoding="utf-8")
    start = text.index("## 0-A.")
    end = text.index("## 0.", start)
    table = text[start:end]
    for needle in ("출석률", "장기연임", "겸임"):
        assert needle in table, f"§0-A 정합표에 「{needle}」 행이 없다"
