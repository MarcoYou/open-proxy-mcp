# -*- coding: utf-8 -*-
"""proxy_advise 산출물에 기술 식별자가 새지 않는지. network 0콜.

260728 실측: LG화학 1건에 내부 식별자 95건(`fy_current_revenue_krw`·`[법령 A1-1]`·
`case_by_case`·⛔ LLM 지시 블록 700자). 25사 스윕에서 밴드 코드가 더 나왔다.
사람이 읽는 문서에 엔진 내부 이름이 나오면 안 된다 — AI 가 다듬어줄 수 없는 종류의 결함이다.
"""
from __future__ import annotations

import re

from open_proxy_mcp.tools.proxy_advise_before_meeting import (
    _FACT_LABEL,
    _FACT_VALUE,
    _fact_label,
    _fact_value,
    _one_line,
    _won,
)

_SNAKE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


def test_every_known_fact_key_has_a_korean_label():
    for key, label in _FACT_LABEL.items():
        assert not _SNAKE.search(label), f"{key} 라벨에 영문 식별자: {label}"
        assert re.search(r"[가-힣]", label), f"{key} 라벨에 한글이 없다: {label}"


def test_enum_values_render_in_korean():
    for raw, ko in _FACT_VALUE.items():
        assert not _SNAKE.search(ko), f"{raw} 값에 영문 식별자: {ko}"
    # 실측에서 새어 나왔던 것들이 전부 사전에 있어야 한다
    for raw in ("case_by_case", "not_checked", "first_term_or_short", "low_under_5",
                "small_or_flat", "mid_30_to_70", "single_position", "renewed",
                "independent", "clean", "potential_long_tenure", "concerns_concurrent",
                "normal_70_to_100", "no_match", "over_100"):
        assert raw in _FACT_VALUE, raw
        assert _fact_value("x", raw) != raw, raw


def test_unknown_key_still_loses_its_snake_case():
    # 사전에 없는 새 fact 가 들어와도 최소한 영문 스네이크 티는 걷힌다
    assert "_" not in _fact_label("some_new_metric_pct")
    assert _fact_label("some_new_metric_pct").endswith("(%)")


def test_won_amounts_are_human_readable():
    assert _won(48_916_104_000_000) == "48조 9,161억원"
    assert _won(-690_854_000_000) == "-6,908억원"
    assert _won(None) == "-"
    # 키 끝이 _krw 가 아니어도(fy_prior_net_income_krw_dart) 금액으로 읽는다
    assert "억원" in _fact_value("fy_prior_net_income_krw_dart", -690_854_000_000)


def test_year_like_numbers_keep_no_thousands_separator():
    # 「당사 재직 시작 2,018」 이 나오던 것
    assert _fact_value("this_company_since", 2018) == "2018"
    assert _fact_value("director_count", 7) == "7"
    assert _fact_value("some_big_count", 48_916) == "48,916"


def test_float_is_rounded_for_reading():
    assert _fact_value("cfo_to_op_ratio", 7.6487) == "7.65"


def test_table_cell_is_single_line_so_the_table_does_not_break():
    # 정관 원문·조항 상세가 셀 안에 줄바꿈째 들어가 표가 무너지던 것
    multi = "정관변경 — 위험 신호 없음\n\n📄 정관 조문 원문:\n[제20조] 변경 전: …"
    out = _one_line(multi, 160)
    assert "\n" not in out and out == "정관변경 — 위험 신호 없음"
    assert "|" not in _one_line("a | b", 160)


def test_cut_does_not_end_inside_an_open_paren():
    long = "집중투표 배제 조항 삭제 — 2026 상법 2차 개정 (자산 2조+ 집중투표 의무화, 정관 배제 불가)"
    out = _one_line(long, 40)
    assert out.count("(") == out.count(")")


def test_all_label_dicts_are_korean_and_cover_the_same_enums():
    """라벨 사전이 여러 개라 한 곳만 고치면 다른 표에서 영문이 샌다(potential_long_tenure 실측)."""
    from open_proxy_mcp.tools import proxy_advise_before_meeting as m
    dicts = {n: getattr(m, n) for n in dir(m)
             if n.endswith(("_LABELS", "_KO", "_LABEL", "_VALUE")) and isinstance(getattr(m, n), dict)}
    assert len(dicts) >= 4, dicts.keys()
    for name, d in dicts.items():
        for k, v in d.items():
            assert not _SNAKE.search(str(v)), f"{name}[{k}] 에 영문 식별자: {v}"
    # 임기 상태는 세 사전이 함께 쓴다 — 한 곳만 알면 다른 표에서 코드가 나온다
    tenure = [n for n, d in dicts.items() if "long_tenure_concerns" in d]
    for n in tenure:
        assert "potential_long_tenure" in dicts[n], f"{n} 에 potential_long_tenure 누락"
