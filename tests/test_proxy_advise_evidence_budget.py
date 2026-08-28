# -*- coding: utf-8 -*-
"""원문 발췌를 줄이되 **줄였다고 쓴다**. network 0콜.

260828 실측 대림제지(017650): 응답 62,812자로 호출측에서 잘렸다. 뜯어보니 이름 매칭이 실패한
안건 4건이 **같은 소집공고 원문을 각각 12.8k 씩** 싣고 있었다 — 응답의 대부분이 같은 글의 사본.
조용히 자르는 것이 이 저장소에서 가장 싫어하는 동작이므로, 줄인 자리에는 얼마나 줄였고
어떻게 넓히는지가 남아야 한다.
"""

from __future__ import annotations

from open_proxy_mcp.services.proxy_advise import (
    _EVIDENCE_DEFAULT_CHARS,
    _EVIDENCE_MAX_CHARS,
    _raw_window,
)

_NOTICE = ("머리말 " * 200) + "제4호 의안 : 독립이사 선임의 건 " + ("본문 " * 5000)


def test_the_window_reports_where_it_sat_and_what_it_left_out() -> None:
    w = _raw_window(_NOTICE, "제4호 의안 : 독립이사 선임의 건",
                    before=1000, after=4000,
                    source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260814003207")
    assert w is not None
    assert w["end"] > w["start"]
    assert w["omitted"] > 0
    # 줄인 자리에 「얼마나 · 어떻게 넓히나 · 전문은 어디」 셋이 남는다
    assert "줄였습니다" in w["text"]
    assert "evidence_chars" in w["text"]
    assert "dsaf001" in w["text"]


def test_a_window_that_fits_says_nothing_about_trimming() -> None:
    short = "제1호 의안 : 재무제표 승인의 건"
    w = _raw_window(short, "제1호 의안 : 재무제표 승인의 건", before=1000, after=4000)
    assert w is not None and w["omitted"] == 0
    assert "줄였습니다" not in w["text"]


def test_the_window_is_bounded_by_evidence_chars() -> None:
    narrow = _raw_window(_NOTICE, "제4호 의안", before=200, after=800)
    wide = _raw_window(_NOTICE, "제4호 의안", before=1000, after=8000)
    assert narrow and wide
    assert (narrow["end"] - narrow["start"]) < (wide["end"] - wide["start"])


def test_the_defaults_are_a_handle_not_a_wall() -> None:
    """기본값은 사용자가 넓힐 수 있는 손잡이여야 한다(CLAUDE.md 「길과 방법을 터준다」)."""
    assert _EVIDENCE_DEFAULT_CHARS < _EVIDENCE_MAX_CHARS
    assert _EVIDENCE_MAX_CHARS >= 30000


def test_the_tool_exposes_the_handle_and_the_as_of() -> None:
    """docstring 은 호출하는 LLM 이 읽는 유일한 안내문이다 — 손잡이가 거기 없으면 없는 것이다."""
    import inspect

    from open_proxy_mcp.tools import proxy_advise_before_meeting as mod

    src = inspect.getsource(mod.register_tools)
    for token in ("as_of:", "include_after_meeting:", "evidence_chars:"):
        assert token in src, f"{token} 안내가 docstring 에 없다"
    assert "look-ahead" in src
