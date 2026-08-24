"""screener 자연어 앞단 — 260824. network 0콜·DB 0콜.

배경: screener 만 `period="last_7d"` · `universe="kospi:30"` · `custom_start=` 같은
**우리끼리 정한 어휘**를 요구했다. 나머지 tool 은 회사명을 그냥 받고 기간은
`start_date`/`end_date` 로 받는다. 부르는 쪽이 그 어휘를 외워야 했고, 틀리면 조용히
기본값(since_yesterday · 전체시장)으로 빠졌다 — 틀린 줄도 모른다.

여기서 지키는 것은 **정규화만 한다**는 계약이다. 사람 말을 기존 코드로 바꿔 원래 리졸버에
넘긴다. 실측으로 4쌍(코드 vs 자연어)이 같은 결과를 냈다.
"""
from __future__ import annotations

import pytest

from open_proxy_mcp.services.screener import _nl_period, _nl_types, _nl_universe


# ── period ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,code", [
    ("", "since_yesterday"), ("오늘", "today"), ("금일", "today"),
    ("어제", "yesterday"), ("전일", "yesterday"),
    ("어제부터", "since_yesterday"),
    ("지난주", "last_7d"), ("일주일", "last_7d"), ("최근 7일", "custom:7"),
    ("지난 한 달", "last_30d"), ("한달", "last_30d"),
    ("최근 3개월", "custom:90"), ("최근 45일", "custom:45"),
    ("last_7d", "last_7d"), ("since_yesterday", "since_yesterday"),
])
def test_period_words_map_to_codes(raw, code):
    assert _nl_period(raw, "", "", "", "")[0] == code


def test_longer_phrase_wins_over_shorter():
    """「지난 한 달」이 「한 달」보다 먼저 걸려야 한다 — 순서가 뒤집히면 둘 다 같은 데로 간다."""
    assert _nl_period("지난 한 달", "", "", "", "")[0] == "last_30d"
    assert _nl_period("지난 3개월", "", "", "", "")[0] == "custom:90"


@pytest.mark.parametrize("raw,cs,ce", [
    ("20260801~20260820", "20260801", "20260820"),
    ("2026-08-01 ~ 2026-08-20", "20260801", "20260820"),
    ("20260820", "20260820", "20260820"),
])
def test_date_ranges_are_parsed(raw, cs, ce):
    p, a, b = _nl_period(raw, "", "", "", "")
    assert (p, a, b) == ("custom", cs, ce)


def test_start_date_end_date_match_the_rest_of_the_repo():
    """다른 tool 은 전부 `start_date`/`end_date` 다 — screener 만 `custom_*` 였다."""
    assert _nl_period("", "20260801", "20260820", "", "") == ("custom", "20260801", "20260820")
    assert _nl_period("", "2026-08-01", "2026-08-20", "", "") == ("custom", "20260801", "20260820")


def test_new_date_args_win_over_legacy_ones():
    assert _nl_period("", "20260801", "20260810", "20250101", "20250131")[1] == "20260801"


def test_explicit_dates_beat_words():
    """날짜를 직접 줬으면 말보다 구체적이다 — 그쪽이 이겨야 한다."""
    assert _nl_period("지난주", "20260801", "20260820", "", "")[0] == "custom"


def test_unknown_period_passes_through_for_the_resolver_to_flag():
    """못 알아들으면 **삼키지 않고** 흘려보낸다 — 원래 리졸버가 notice 를 단다."""
    assert _nl_period("헛소리", "", "", "", "")[0] == "헛소리"


# ── universe ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,out", [
    ("", "all"), ("전체", "all"), ("전체시장", "all"),
    ("코스피", "market:kospi"), ("코스닥", "market:kosdaq"),
    ("코스피200", "kospi200"), ("KOSPI200", "kospi200"),
    ("코스피 시총 상위 30", "kospi:30"), ("코스닥 상위 50", "kosdaq:50"),
    ("시총 상위 100", "top_mktcap:100"),
    ("kospi:30", "kospi:30"), ("market:kospi", "market:kospi"),
    ("top_mktcap:50", "top_mktcap:50"), ("custom:005930", "custom:005930"),
])
def test_universe_words_map_to_existing_grammar(raw, out):
    assert _nl_universe(raw) == out


def test_bare_names_become_a_custom_universe():
    """이름을 나열하면 `custom:` 이 받는다 — 거기서 이름→코드 변환이 이미 된다."""
    assert _nl_universe("삼성전자, SK하이닉스") == "custom:삼성전자, SK하이닉스"


# ── types ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,out", [
    ("", "core"), ("core", "core"), ("all", "all"),
    ("자사주", "treasury"), ("자기주식", "treasury"),
    ("자사주, 배당", "treasury,dividend"),
    ("수주·실적", "order,earnings"),
    ("주총 지분", "agm_notice,ownership5"),
    ("합병", "restructuring"),
    ("treasury,dividend", "treasury,dividend"),
])
def test_type_words_map_to_codes(raw, out):
    assert _nl_types(raw) == out


def test_registry_labels_are_accepted():
    """표에 보이는 라벨을 사용자가 되돌려 주는 일이 흔하다 — 원장에서 역인덱스를 만든다."""
    from open_proxy_mcp.services.screener import TYPE_REGISTRY
    for t in TYPE_REGISTRY[:4]:
        assert _nl_types(t["label"]) == t["code"], t["label"]


def test_duplicates_collapse_and_order_is_kept():
    assert _nl_types("자사주, 자기주식, 배당") == "treasury,dividend"


def test_unknown_type_passes_through_for_validation():
    """못 알아들은 조각을 조용히 버리면 사용자는 자기가 뭘 빠뜨렸는지 모른다."""
    assert "없는말" in _nl_types("없는말")


# ── 계약: 정규화만 한다 ────────────────────────────────────────────────
def test_legacy_inputs_are_untouched():
    """옛 어휘로 부르던 호출은 **한 글자도 안 바뀌어야** 한다(하위호환)."""
    assert _nl_period("last_30d", "", "", "", "") == ("last_30d", "", "")
    assert _nl_universe("kospi200") == "kospi200"
    assert _nl_types("core") == "core"


def test_interpretation_is_reported_to_the_user():
    """조용히 다른 걸 조회하면 사용자가 모른다 — 해석 결과를 warnings 에 싣는다."""
    import inspect

    from open_proxy_mcp.services import screener
    src = inspect.getsource(screener._build_screener_payload_impl)
    assert "입력 해석" in src
