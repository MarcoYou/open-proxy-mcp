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


def test_audit_compensation_never_crashes_on_partial_data():
    """분기 9/10 은 둘 다 None 일 때만 잡아, 하나만 None 이면 포맷이 터졌다.
    260728 부실기업 검증에서 이오플로우·한국유니온제약이 도구 전체 크래시로 나왔다.
    """
    from open_proxy_mcp.services.proxy_advise import _decide_audit_compensation
    for comp in (
        {"audit_total_limit_krw": 100_000_000, "audit_count": None},   # 1인당 산출 불가
        {"audit_prior_limit_krw": None, "audit_total_limit_krw": 500_000_000, "audit_count": 2},
        {}, {"audit_count": 0}, {"audit_total_limit_krw": 0, "audit_count": 1},
    ):
        for fin in ({}, {"net_income_krw": -1}, {"capital_impairment_status": "full"}, None):
            d, r = _decide_audit_compensation(comp, fin or {})
            assert d in ("FOR", "AGAINST", "REVIEW", "NO_DATA"), (comp, fin, d)
            assert "None" not in r, (comp, fin, r)


# ── 변이 테스트에서 「못 잡음」으로 드러난 구멍 (260729) ───────────────────────
# 소스 문자열만 검사하면 렌더 결과가 바뀌어도 통과한다. 실제 렌더를 돌려서 본다.

def _rendered_sample() -> str:
    from open_proxy_mcp.tools.proxy_advise_before_meeting import _render
    return _render({
        "status": "ok", "subject": "테스트",
        "data": {
            "year": 2026, "agenda_count": 2, "candidates_count": 0,
            "agenda_decisions": [
                {"agenda_title": "정관 변경의 건", "decision": "FOR",
                 # 표 셀에 줄바꿈이 들어가면 마크다운 표가 그 지점에서 무너진다
                 "reason": "정관변경 — 위험 신호 없음\n\n📄 정관 조문 원문:\n[제20조] 변경 전: …",
                 "facts": {}, "risk_factors": []},
                {"agenda_title": "이사 보수한도 승인의 건", "decision": "REVIEW",
                 "reason": "한도 인상 — 검토 필요", "facts": {"limit_krw": 7_000_000_000},
                 "risk_factors": []},
            ],
            "financial_summary": {"revenue_krw": 48_916_104_000_000,
                                  "operating_profit_krw": 916_798_000_000,
                                  "capital_impairment_status": "normal"},
        },
    })


def test_rendered_table_rows_have_a_consistent_column_count():
    """표 셀에 줄바꿈이 들어가면 그 행부터 표가 무너진다 — 렌더 결과로 확인한다."""
    rows = [ln for ln in _rendered_sample().splitlines() if ln.startswith("|")]
    assert rows, "표가 렌더되지 않았다 — 테스트가 무력화됐다"
    counts = {ln.count("|") for ln in rows}
    assert len(counts) == 1, f"열 수가 어긋난 행이 있다: {sorted(counts)}"


def test_rendered_output_has_no_scolding_and_no_warning_sign():
    out = _rendered_sample()
    assert "⚠" not in out.replace("⚠️ 검토 필요", "")     # 판정 마커만 예외
    for scold in ("하지 마세요", "하지 말 것", "만들지 마", "금지"):
        assert scold not in out, scold


def test_rendered_amounts_carry_the_won_unit():
    """「334조」만 쓰면 무엇의 단위인지 문서 안에서 확정되지 않는다."""
    import re
    out = _rendered_sample()
    # 「제20조」의 조를 물지 않게 — 천단위 구분자가 있는 금액만 본다(측정 도구 오탐 교정)
    for m in re.finditer(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?\s*(조|억)(?!원)", out):
        raise AssertionError(f"단위에 '원'이 없다: {out[max(0, m.start()-30):m.end()+10]!r}")
    assert "조원" in out or "억원" in out, "금액이 렌더되지 않았다 — 테스트가 무력화됐다"
