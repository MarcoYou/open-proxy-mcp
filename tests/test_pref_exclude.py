"""업종 구성·성과 분석 대상에서 우선주를 뺀다 (261023, DB 없이 되는 부분).

  A  `sector_universe.compute` — 결과 행에 우선주(분류 출처 src='pref', 분류가 없으면 코드 끝자리 규칙)가 없고, 비중·순위·누적·업종 시총·시장 몫의 분모는 보통주 합이다.
  B  경고·sector 블록 — 뺀 우선주 수와 시총을 밝히고, 보통주 합 + 우선주 몫 = 우선주 포함 합(시총 집계 `scope="sector"` 와 같은 정의의 수)이다.
  C  업종 안 종목이 우선주뿐이면 빈 표 대신 못 만든 사유(notice).
  D  응답 조립·md — 「우선주 포함 … 시총 집계와 같다」 문구가 사라지고 차이(우선주 몫)를 적는다.
  E  `performance_analysis` 의 업종 대상·블렌드 구성요소는 같은 `list_universe` 를 거치므로 우선주가 없고 구성요소 안 비중 합이 각각 1 이다.
  F  바뀌지 않는 것 — 종목 코드·나열로 직접 지정한 우선주는 그대로, 시세 순위표 경로는 그대로.
  G  판정 규칙은 하나 — 분류가 있는 행은 출처 src='pref' 만으로, 분류가 없는 행은 코드 끝자리 규칙(회사 원장이 비어 있어도 같다). 뺀 수는 `UniverseList.excluded_pref` 로 올라와
     같은 `list_universe` 를 쓰는 시세 순위표(`trading_screen`)가 업종 문구에서도 우선주를 뺀 사실을 밝힌다.
  H  대표 호출은 그대로 — 바스켓의 005935, 코스피200 원장 집합, 코스피 시총 상위 N 순위표. (종목 코드로 부르는 firm·quote 005935 는 `test_share_class_and_alnum_codes.py`
     의 `test_trading_firm_reads_the_share_class_own_curve`·`test_trading_quote_reads_the_share_class_row` 가 지킨다 — 이 변경은 그 경로를 건드리지 않는다.)
Postgres 17 에서 전 시장 계산 벤치마크(우선주 제외)를 대조하는 시험은 `test_pref_exclude_pg.py`. 이 시험들은 고치기 전 main(006536ca)에서 실패한다.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from open_proxy_mcp.services import performance_analysis as PA
from open_proxy_mcp.services import sector_universe as S
from open_proxy_mcp.services import trading as T
from open_proxy_mcp.services import universe as uni
from test_sector_universe import COMM, DAY, FOOD1, IT as IT_, NAMES, R, SEMI as SEMI_, STAPLE, rows_fixture


def fixture_with_unclassified_pref():
    """기본 픽스처(보통주 + 우선주 900015 = 900010 의 src='pref' 행) + 분류가 없는 우선주 900025(원장의 보통주 900020 의 종류주 — 코드 끝자리 규칙)."""
    return rows_fixture() + [R("900025", "KS", 40, None, None)]


def compute(text, rows=None, names=NAMES):
    return S.compute(S.parse(text), rows if rows is not None else rows_fixture(), DAY, names)


# ── A: 구성에서 우선주가 빠지고 분모가 보통주 합 ──────────────────────────────

def test_preferred_row_is_not_in_the_sector_table():
    r = compute("업종 반도체")
    assert "900015" not in {x["ticker"] for x in r.rows}
    assert [x["ticker"] for x in r.rows] == ["900010", "900020", "800010"]
    assert not any(x.get("preferred") for x in r.rows)


def test_weights_are_over_the_common_stock_total_and_sum_to_100_with_gapless_rank():
    r = compute("업종 반도체")
    assert [x["rank"] for x in r.rows] == [1, 2, 3]
    assert r.sector["cap_krw"] == 600 + 200 + 50 == sum(x["mktcap_krw"] for x in r.rows)           # 우선주 100 은 합에 없다
    assert r.rows[0]["weight_pct"] == round(600 / 850 * 100, 4)
    assert abs(sum(x["weight_pct"] for x in r.rows) - 100) < 0.01
    assert r.rows[-1]["cum_weight_pct"] == 100.0
    assert r.sector["n"] == 3 and r.sector["shown_weight_pct"] == 100.0


def test_top_n_keeps_the_common_stock_denominator():
    r = compute("업종 반도체 상위 2")
    assert [x["ticker"] for x in r.rows] == ["900010", "900020"]
    assert r.rows[0]["weight_pct"] == round(600 / 850 * 100, 4)
    assert r.sector["shown_weight_pct"] == round(800 / 850 * 100, 4)


def test_major_level_children_weights_are_over_the_common_total():
    r = compute("대분류 IT")
    assert "900015" not in {x["ticker"] for x in r.rows}
    ch = {c["code"]: c for c in r.sector["children"]}
    assert ch["G4530"]["cap_krw"] == 850 and ch["G4530"]["n"] == 3
    assert abs(sum(c["weight_pct"] for c in r.sector["children"]) - 100) < 0.01


def test_market_filter_excludes_the_preferred_of_that_market():
    ks = compute("코스피 반도체 업종")
    assert {x["ticker"] for x in ks.rows} == {"900010", "900020"} and ks.sector["cap_krw"] == 800
    assert abs(sum(x["weight_pct"] for x in ks.rows) - 100) < 0.01
    kq = compute("코스닥 반도체 업종")                                     # 코스닥에는 우선주가 없다 — 빼는 것도 없다
    assert [x["ticker"] for x in kq.rows] == ["800010"] and kq.sector["preferred_n"] == 0
    assert not any("업종 구성에서 뺐다" in w for w in kq.warnings)


def test_share_of_market_denominator_is_the_market_without_preferred():
    rows = fixture_with_unclassified_pref()
    r = compute("코스피 반도체 업종", rows)
    ks_common = sum(x["mktcap"] for x in rows if x["market"] == "KS" and x["ticker"] not in ("900015", "900025"))
    assert r.sector["market_cap_krw"] == ks_common
    assert r.sector["share_of_market_pct"] == round(800 / ks_common * 100, 4)


def test_unclassified_preferred_is_not_in_the_unclassified_table_and_not_in_the_market_total():
    r = compute("업종 반도체", fixture_with_unclassified_pref())
    u = r.sector["unclassified"]
    assert [x["ticker"] for x in u["top"]] == ["800090"] and u["n"] == 1 and u["cap_krw"] == 7


# ── B: 경고·sector 블록 ───────────────────────────────────────────────────────

def test_warning_says_how_many_preferred_and_their_cap_were_left_out():
    r = compute("업종 반도체")
    w = [x for x in r.warnings if "업종 구성에서 뺐다" in x]
    assert len(w) == 1 and "우선주 1종목(시총 100원)은 업종 구성에서 뺐다" in w[0] and "종목 코드" in w[0]
    assert not any("들어 있다" in x or "빼지 않는다" in x for x in r.warnings)


def test_sector_block_carries_the_preferred_share_and_the_inclusive_total():
    r = compute("업종 반도체")
    s = r.sector
    assert s["preferred_n"] == 1 and s["preferred_cap_krw"] == 100
    assert s["cap_krw"] + s["preferred_cap_krw"] == s["cap_incl_preferred_krw"] == 950           # 우선주 포함 합 = 시총 집계(scope="sector")의 정의
    assert compute("대분류 IT").sector["cap_incl_preferred_krw"] == 600 + 100 + 200 + 50 + 70 + 30 + 12


# ── C: 우선주뿐인 업종 ────────────────────────────────────────────────────────

def test_sector_with_only_preferred_gives_a_notice_instead_of_an_empty_table():
    tele = ("G5010", "전기통신서비스")
    rows = [r for r in rows_fixture() if r["mid_code"] != "G5010"] + [
        R("900085", "KS", 15, COMM, tele, src="pref", src_ref="900080")]
    res = compute("업종 전기통신서비스", rows)
    assert res.rows == [] and "우선주뿐" in res.notice and "1종목" in res.notice and "15원" in res.notice


# ── D: 응답 조립·md ───────────────────────────────────────────────────────────

@pytest.fixture
def fake_db(monkeypatch):
    async def pick(as_of):
        return S.DayPick(dd="20261020")
    monkeypatch.setattr(S, "_pick_day", pick)
    monkeypatch.setattr(S, "_read_rows", lambda dd, source: rows_fixture())

    async def names():
        return dict(NAMES)
    monkeypatch.setattr(uni, "_names_by_ticker", names)


def test_method_text_no_longer_claims_the_sum_equals_the_sector_aggregate(fake_db):
    p = asyncio.run(T.build_universe_payload("반도체 업종"))
    m = p["data"]["method"]
    assert "같은 날 같은 업종이면 같다" not in m and "우선주 포함 — 별도 종목" not in m
    assert "보통주" in m and "우선주 몫" in m and "scope=\"sector\"" in m
    assert p["data"]["sector"]["preferred_n"] == 1 and any("우선주 1종목(시총 100원)" in w for w in p["warnings"])


def test_md_header_names_the_excluded_preferred_not_included(fake_db):
    from open_proxy_mcp.tools import trading as tool
    md = tool._render_universe_sector(asyncio.run(T.build_universe_payload("반도체 업종")))
    assert "우선주 1종목 제외" in md
    assert "가나전자 우선주" not in md and "900015" not in md


# ── E: performance_analysis 대상·블렌드 ───────────────────────────────────────

def _ctx(since="20261016"):
    return types.SimpleNamespace(plan=types.SimpleNamespace(since_req=since), names=None)


def test_performance_target_sector_has_no_preferred_and_blend_weights_sum_to_one_per_component(monkeypatch):
    rows = rows_fixture() + [R("900065", "KS", 5, STAPLE, FOOD1, src="pref", src_ref="900060")]

    async def pick(as_of):
        return S.DayPick(dd="20261020")
    monkeypatch.setattr(S, "_pick_day", pick)
    monkeypatch.setattr(S, "_read_rows", lambda dd, source: rows)

    async def names():
        return dict(NAMES)
    monkeypatch.setattr(uni, "_names_by_ticker", names)
    parsed = PA.parse_target("반도체 업종=60; 대분류 필수소비재=40")
    assert parsed.error == "" and len(parsed.components) == 2
    caps = {r["ticker"]: float(r["mktcap"]) for r in rows}
    for comp in parsed.components:
        members, info = asyncio.run(PA._resolve_members(_ctx(), comp))
        tickers = {m["ticker"] for m in members}
        assert not (tickers & {"900015", "900065"}) and tickers
        w = PA._within_weights(members, "cap", comp, caps)
        assert sum(w.values()) == pytest.approx(1.0, abs=1e-12)
        assert info["kind"] == "sector"
    semi_members, _ = asyncio.run(PA._resolve_members(_ctx(), parsed.components[0]))
    assert [m["ticker"] for m in semi_members] == ["900010", "900020", "800010"]


# ── F: 바뀌지 않는 것 ─────────────────────────────────────────────────────────

def test_codes_named_directly_keep_their_preferred(monkeypatch):
    """종목 코드·이름 나열(custom:)로 직접 지정한 우선주는 그대로 — 고른 쪽이 정한 집합이다."""
    from open_proxy_mcp.services import screener

    class UF:
        label, notice, price_dd, resolved, question = "직접 지정", "", "20261020", True, ""
        allowed = frozenset({"900010", "900015"})
        members_dd = None
        pref_screen = frozenset()

    async def fake_resolve(spec):
        return UF()
    monkeypatch.setattr(screener, "resolve_universe", fake_resolve)
    monkeypatch.setattr(screener, "_nl_universe", lambda raw: "custom:900010,900015")
    monkeypatch.setattr(uni, "_krx_rows", lambda dd, codes: [("900010", "KS", 600, 1000, 1), ("900015", "KS", 100, 1000, 1)])

    async def names():
        return dict(NAMES)
    monkeypatch.setattr(uni, "_names_by_ticker", names)
    ul = asyncio.run(uni.list_universe("900010, 900015"))
    assert [r["ticker"] for r in ul.rows] == ["900010", "900015"] and ul.excluded_pref == 0


# ── G: 판정 규칙(분류 있음 = 출처만 · 분류 없음 = 코드 끝자리) · 뺀 수의 전달 ──────────────────

def test_unclassified_preferred_is_left_out_even_when_the_company_ledger_is_empty():
    """분류가 없는 종류주 코드(끝자리 ≠ 0)는 회사 원장(names)을 보지 않고 뺀다 — 보통주 코드가 원장에 없는 코드(900045)도 뺀다."""
    rows = fixture_with_unclassified_pref() + [R("900045", "KS", 33, None, None)]
    r = compute("업종 반도체", rows, names={})
    u = r.sector["unclassified"]
    assert [x["ticker"] for x in u["top"]] == ["800090"] and u["n"] == 1                    # 900025·900045(종류주 코드)는 미분류 표에도 없다
    ks = compute("코스피 반도체 업종", rows, names={})
    assert ks.sector["market_preferred_cap_krw"] == 100 + 40 + 33                            # 분류 있는 우선주 900015 + 분류 없는 900025·900045
    assert ks.sector["market_cap_krw"] == sum(x["mktcap"] for x in rows if x["market"] == "KS") - 173
    keep = lambda res: [(x["ticker"], x["weight_pct"]) for x in res.rows]                   # noqa: E731 — 이름 칸만 원장에 따라 다르다
    assert keep(compute("업종 반도체", rows, names={})) == keep(compute("업종 반도체", rows))    # 원장이 비어도 같은 결과


def test_unclassified_common_stock_code_ending_in_zero_stays():
    r = compute("업종 반도체", rows_fixture() + [R("700000", "KQ", 9, None, None)], names={})
    assert {x["ticker"] for x in r.sector["unclassified"]["top"]} == {"800090", "700000"}


def test_classified_row_is_judged_by_its_source_only():
    """분류가 있는 행은 출처 src='pref' 만 본다 — 끝자리가 0 이 아니어도 출처가 'wise' 면 싣는다(운영 읽기로 이런 행은 0행임을 확인한 규칙)."""
    rows = rows_fixture() + [R("900035", "KS", 30, IT_, SEMI_)]
    r = compute("업종 반도체", rows)
    assert "900035" in {x["ticker"] for x in r.rows} and "900015" not in {x["ticker"] for x in r.rows}


def test_excluded_pref_count_reaches_the_universe_list(fake_db):
    ul = asyncio.run(uni.list_universe("반도체 업종"))
    assert ul.excluded_pref == 1 and ul.sector["preferred_n"] == 1
    assert "900015" not in {r["ticker"] for r in ul.rows}


def test_quote_rank_with_a_sector_phrase_drops_the_preferred_and_says_so(monkeypatch, fake_db):
    """업종 문구를 시세 순위표(`scope=quote` + universe)에 주면 우선주가 빠지고 뺀 수를 경고로 밝힌다 — 같은 `list_universe` 를 쓰는 호출부."""
    from open_proxy_mcp import db
    from open_proxy_mcp.services import trading_screen as ts
    from test_trading_screen import FakeDb, _day
    f = FakeDb()
    monkeypatch.setattr(db, "pg_rows", f)
    for t, cap in (("900010", 600), ("900020", 200), ("800010", 50), ("900015", 100)):
        f.day_rows[t] = _day(t, 1000, 1010, 990, 1000, 1_000_000, cap, 5, 0.5)
        f.win_rows[t] = (t, 1100, 900, "20251022", 240)
    p = asyncio.run(ts.build_quote_rank_payload(universe="반도체 업종"))
    assert p["status"] == "ok"
    assert "900015" not in {r["ticker"] for r in p["data"]["rows"]}
    assert any("우선주 1종목은 업종 구성에서 뺐습니다" in w for w in p["warnings"])
    assert not any("순위에서 뺐습니다" in w for w in p["warnings"])


# ── H: 바뀌지 않는 대표 호출 ──────────────────────────────────────────────────

def test_basket_keeps_the_preferred_code_with_its_weight():
    """바스켓(「005935:40, 000660:60」)은 직접 지정한 구성이라 우선주를 그대로 둔다 — 업종 규칙이 닿지 않는다."""
    parsed = PA.parse_target("005935:40, 000660:60")
    assert parsed.error == ""
    ctx = types.SimpleNamespace(plan=types.SimpleNamespace(since_req="20261016"), names={"000660": "SK하이닉스"})
    members, info = asyncio.run(PA._resolve_members(ctx, parsed.components[0]))
    assert [(m["ticker"], m["w"]) for m in members] == [("005935", 0.4), ("000660", 0.6)] and info["kind"] == "basket"


def test_index_ledger_universe_keeps_the_preferred_rows_as_the_ledger_has_them(monkeypatch):
    """코스피200 같은 지수 구성종목 원장 집합은 우선주 판정으로 빼지 않는다(원장 그대로)."""
    from open_proxy_mcp.services import screener

    class UF:
        label, notice, price_dd, resolved, question = "KOSPI200", "", "20261020", True, ""
        allowed = frozenset({"900010", "900015"})
        members_dd = "20261020"
        pref_screen = frozenset()

    async def fake_resolve(spec):
        return UF()
    monkeypatch.setattr(screener, "resolve_universe", fake_resolve)
    monkeypatch.setattr(screener, "_nl_universe", lambda raw: "kospi200")
    monkeypatch.setattr(uni, "_krx_rows", lambda dd, codes: [("900010", "KS", 600, 1000, 1), ("900015", "KS", 100, 1000, 1)])

    async def names():
        return dict(NAMES)
    monkeypatch.setattr(uni, "_names_by_ticker", names)
    ul = asyncio.run(uni.list_universe("코스피200"))
    assert [r["ticker"] for r in ul.rows] == ["900010", "900015"] and ul.excluded_pref == 0 and ul.sector is None


def test_market_rank_universe_still_drops_the_preferred_by_the_ledger_rule(monkeypatch):
    """「코스피 시총 상위 N」은 종전 판정(`is_preferred`, 회사 원장을 본다)으로 우선주를 빼고 종전 문구로 밝힌다 — 업종 규칙을 쓰지 않는다."""
    from test_trading_universe import _stub
    _stub(monkeypatch)
    p = asyncio.run(T.build_universe_payload("코스피 시총 상위 3"))
    assert [r["ticker"] for r in p["data"]["rows"]] == ["005930", "000660", "005380"]
    assert any("우선주 1종목은 순위에서 뺐습니다" in w for w in p["warnings"])
    assert not any("업종 구성에서 뺐" in w for w in p["warnings"])
