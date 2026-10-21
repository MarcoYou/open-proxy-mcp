"""업종 구성·비중 유니버스(`services/sector_universe.py`) — DB 없이 되는 부분 (261021).

  P  문법 읽기: 업종 표지가 있으면 업종 조회, 시장·상위 N·단계를 뽑고, 순위표 문법(「코스피 시총 상위 100」)·종목 나열·지수는 건드리지 않는다.
  R  이름 맞추기: 정확 → 별칭 → 부분 일치, 여럿이면 추측하지 않고 후보와 함께 되묻는다. 대분류·중분류에 이름이 같고 구성도 같을 때만 중분류로 푼다.
  C  구성·비중: 합 100%, 누적, 상위 N 으로 잘라도 분모는 업종 전체, 시장을 고르면 분모도 그 시장, 우선주는 빼지 않고 표시, 미분류는 어디에도 안 넣고 밝힌다.
  E  끝에서 끝까지(DB 읽기만 바꿔 끼움): 응답 모양·문장·md 표.
Postgres 17 에서 시총 집계 표와 대조하는 시험은 `test_sector_universe_pg.py`.
"""
from __future__ import annotations

import asyncio

import pytest

from open_proxy_mcp.services import sector_universe as S
from open_proxy_mcp.services import trading as T
from open_proxy_mcp.services import universe as uni
from open_proxy_mcp.services.sector_class import MAJOR, MID

SEMI = ("G4530", "반도체와반도체장비")
SOFT = ("G4510", "소프트웨어와서비스")
IT = ("G45", "IT")
AUTO = ("G2510", "자동차와부품")
CONS = ("G25", "경기관련소비재")
ENERGY = ("G1010", "에너지")
ENERGY_MAJOR = ("G10", "에너지")
FOOD1 = ("G3010", "식품과기본식료품소매")
FOOD2 = ("G3020", "식품,음료,담배")
STAPLE = ("G30", "필수소비재")
TELE = ("G5010", "전기통신서비스")
ELEC = ("G4535", "전자와 전기제품")
COMM = ("G50", "커뮤니케이션서비스")
HEALTH_MAJOR = ("G35", "건강관리")
PHARMA = ("G3520", "제약과생물공학")
HEALTH_EQ = ("G3510", "건강관리장비와서비스")


def R(t, mkt, cap, major, mid, *, src="wise", src_ref=None, close=1000, snap="20261016", snap_min="20260821"):
    """DB 읽기 한 줄(`_ROW_KEYS`) — 분류가 (None, None) 이면 미분류."""
    return {"ticker": t, "market": mkt, "close": close, "mktcap": cap, "shrs": cap // close if close else None,
            "major_code": major[0] if major else None, "major": major[1] if major else None,
            "mid_code": mid[0] if mid else None, "mid": mid[1] if mid else None,
            "src": src if major else None, "src_ref": src_ref, "snap": snap, "snap_min": snap_min}


def rows_fixture():
    return [
        R("900010", "KS", 600, IT, SEMI), R("900015", "KS", 100, IT, SEMI, src="pref", src_ref="900010"),
        R("900020", "KS", 200, IT, SEMI), R("800010", "KQ", 50, IT, SEMI),
        R("900030", "KS", 70, IT, SOFT), R("800020", "KQ", 30, IT, SOFT),
        R("900040", "KS", 500, CONS, AUTO), R("800030", "KQ", 40, CONS, AUTO),
        R("900050", "KS", 300, ENERGY_MAJOR, ENERGY), R("800040", "KQ", 10, ENERGY_MAJOR, ENERGY),
        R("900060", "KS", 20, STAPLE, FOOD1), R("900070", "KS", 25, STAPLE, FOOD2),
        R("900080", "KS", 15, COMM, TELE), R("900090", "KS", 12, IT, ELEC),
        R("800099", "KQ", 7, None, None),                      # 미분류(신규 상장)
    ]


DAY = S.DayPick(dd="20261020")
NAMES = {"900010": "가나전자", "900020": "다라반도체", "800010": "마바테크"}


def q(text):
    return S.parse(text)


def compute(text, rows=None, day=DAY, names=NAMES):
    return S.compute(q(text), rows if rows is not None else rows_fixture(), day, names)


# ── P: 문법 ────────────────────────────────────────────────────────────────

def test_parse_reads_sector_phrases():
    a = q("업종 반도체")
    assert a.windows and a.windows[0] == "반도체" and a.level is None and a.market is None and a.top is None
    b = q("반도체 업종")
    assert b.windows[0] == "반도체"
    c = q("코스피 반도체 업종 상위 10")
    assert (c.market, c.top, c.windows[0]) == ("KS", 10, "반도체")
    d = q("중분류 반도체와반도체장비")
    assert (d.level, d.windows[0]) == (MID, "반도체와반도체장비")
    e = q("대분류 IT 코스닥 시총 상위 20개 종목")
    assert (e.level, e.market, e.top, e.windows[0]) == (MAJOR, "KQ", 20, "IT")
    f = q("반도체업종")                                         # 붙여 쓴 꼴
    assert f.windows[0] == "반도체"
    assert q("코스닥 IT 섹터").market == "KQ"
    assert q("하위업종 소프트웨어").level == MID
    assert q("코스피, 코스닥 반도체 업종").market is None       # 둘 다 적으면 양쪽


def test_parse_takes_the_name_next_to_the_marker_not_the_sentence_noise():
    """회사 이름 자리에 들어온 문장 — 이름 후보는 표지 바로 앞뒤 낱말 묶음이다."""
    t = q("반도체 업종 시총 상위 10개 종목의 영업이익 컨센서스가 1주 전 대비 어떻게 바뀌었나")
    assert t.top == 10 and "반도체" in t.windows


@pytest.mark.parametrize("text", [
    "코스피 시총 상위 100", "코스닥 상위 50", "코스피200", "삼성전자, SK하이닉스", "005930", "코스피 전체", "KRX300", "",
])
def test_non_sector_phrases_are_untouched(text):
    assert S.is_sector_phrase(text) is False and S.parse(text) is None


def test_ksic_is_flagged():
    assert q("KSIC 26 업종").ksic is True
    assert q("한국표준산업분류 반도체").ksic is True
    assert q("반도체 업종").ksic is False


def test_company_slot_sentence_with_sector_marker_keeps_the_sector_instead_of_dropping_it():
    """종전에는 「코스피 반도체 업종 상위 10」이 「코스피 시총 상위 10」으로 읽혀 업종이 조용히 사라졌다."""
    got = uni.universe_phrase("코스피 반도체 업종 상위 10")
    assert got is not None and "반도체 업종" in got[0]
    assert uni.universe_phrase("코스피 시총 상위 10") == ("코스피 시총 상위 10", None)
    assert uni.universe_phrase("삼성전자") is None
    assert uni.universe_phrase("삼성전자 업종") is None          # 업종 표지만 있고 목록을 바라는 말이 없다 — 회사 질문으로 둔다
    w = uni.universe_phrase("반도체 업종 상위 10 종목 4주 전 대비")
    assert w is not None and w[1] == "4w"


# ── R: 이름 맞추기 ──────────────────────────────────────────────────────────

def cat_and_members(rows=None):
    rows = rows or rows_fixture()
    cat = S._catalog(rows)
    return cat, (lambda e: frozenset(r["ticker"] for r in rows if S._is_member(r, e)))


def resolve(text, rows=None):
    cat, mem = cat_and_members(rows)
    return S.resolve(q(text), cat, mem)


def test_resolve_exact_alias_partial_and_code():
    e, how, ask = resolve("업종 반도체와반도체장비")
    assert e.code == "G4530" and not how and not ask
    e, how, _ = resolve("업종 반도체")
    assert e.code == "G4530" and "부분" in how
    e, how, _ = resolve("it 업종")                              # 대소문자 무시
    assert (e.level, e.code) == (MAJOR, "G45")
    e, _, _ = resolve("업종 G4530")                            # 업종 코드
    assert e.name == "반도체와반도체장비"
    e, how, _ = resolve("바이오 업종", rows=rows_fixture() + [R("900100", "KS", 9, HEALTH_MAJOR, PHARMA)])
    assert e.code == "G3520" and "별칭" in how
    e, _, _ = resolve("전자와전기제품 업종")                    # 공백 무시
    assert e.code == "G4535"


def test_resolve_asks_instead_of_guessing_when_several_match():
    e, _, ask = resolve("전기 업종")
    assert e is None and "전기통신서비스" in ask and "전자와 전기제품" in ask and "G5010" in ask
    e, _, ask = resolve("식품 업종")
    assert e is None and "식품과기본식료품소매" in ask and "식품,음료,담배" in ask


def test_resolve_same_name_in_both_levels_only_when_membership_is_equal():
    e, how, ask = resolve("에너지 업종")                        # 대분류 G10 에너지 == 중분류 G1010 에너지
    assert (e.level, e.code) == (MID, "G1010") and "같아" in how and not ask
    # 구성이 다르면 되묻는다
    rows = rows_fixture() + [R("900111", "KS", 5, ENERGY_MAJOR, ("G1020", "석유가스"))]
    e, how, ask = resolve("에너지 업종", rows)
    assert e is None and "대분류 에너지(G10)" in ask and "중분류 에너지(G1010)" in ask


def test_resolve_level_mismatch_and_not_found_list_what_exists():
    e, _, ask = resolve("대분류 반도체")
    assert e is None and "대분류에는 없고" in ask and "중분류" in ask and "반도체와반도체장비" in ask
    e, _, ask = resolve("업종 2차전지")
    assert e is None and "2차전지" in ask and "대분류(" in ask and "중분류(" in ask
    e, _, ask = resolve("업종")
    assert e is None and "업종 이름" in ask
    e, _, ask = S.resolve(q("업종 반도체"), [], lambda e: frozenset())
    assert e is None and "비어" in ask


def test_resolve_ignores_one_character_partial_matches():
    e, _, ask = resolve("업종 차")
    assert e is None and ask


# ── C: 구성·비중 ───────────────────────────────────────────────────────────

def test_weights_sum_to_100_with_running_total_and_rank():
    r = compute("업종 반도체")
    assert [x["ticker"] for x in r.rows] == ["900010", "900020", "900015", "800010"]
    assert [x["rank"] for x in r.rows] == [1, 2, 3, 4]
    assert sum(x["mktcap_krw"] for x in r.rows) == 950 == r.sector["cap_krw"]
    assert r.rows[0]["weight_pct"] == round(600 / 950 * 100, 4)
    assert abs(sum(x["weight_pct"] for x in r.rows) - 100) < 0.001
    assert r.rows[-1]["cum_weight_pct"] == 100.0
    assert r.rows[0]["name"] == "가나전자"
    assert r.sector["n"] == 4 and r.sector["shown_weight_pct"] == 100.0 and r.sector["level"] == MID


def test_preferred_is_kept_flagged_and_named_from_the_common_stock():
    r = compute("업종 반도체")
    pref = next(x for x in r.rows if x["ticker"] == "900015")
    assert pref["preferred"] is True and pref["issuer_ticker"] == "900010" and pref["name"] == "가나전자 우선주"
    assert "preferred" not in next(x for x in r.rows if x["ticker"] == "900010")
    assert r.sector["preferred_n"] == 1 and any("우선주 1종목이 들어 있다" in w for w in r.warnings)
    # 우선주를 빼지 않으므로 업종 시총 합은 우선주 포함 합이다(시총 집계의 정의)
    assert r.sector["cap_krw"] == 600 + 100 + 200 + 50


def test_top_n_cuts_rows_but_keeps_the_denominator():
    r = compute("업종 반도체 상위 2")
    assert [x["ticker"] for x in r.rows] == ["900010", "900020"]
    assert r.sector["cap_krw"] == 950 and r.sector["n"] == 4 and r.sector["n_shown"] == 2
    assert r.rows[0]["weight_pct"] == round(600 / 950 * 100, 4)          # 분모는 업종 전체
    assert r.sector["shown_weight_pct"] == round(800 / 950 * 100, 4) < 100
    assert any("상위 2종목만" in w and "100% 에 못 미친" in w for w in r.warnings)


def test_market_filter_changes_membership_and_denominator():
    ks = compute("코스피 반도체 업종")
    assert {x["ticker"] for x in ks.rows} == {"900010", "900015", "900020"} and ks.sector["cap_krw"] == 900
    assert ks.sector["market"] == "KOSPI" and abs(sum(x["weight_pct"] for x in ks.rows) - 100) < 0.001
    kq = compute("코스닥 반도체 업종")
    assert [x["ticker"] for x in kq.rows] == ["800010"] and kq.rows[0]["weight_pct"] == 100.0
    # 시장 안의 업종 비중도 그 시장 시총이 분모
    assert ks.sector["market_cap_krw"] == sum(r["mktcap"] for r in rows_fixture() if r["market"] == "KS")


def test_unclassified_is_never_put_in_a_sector_and_is_reported():
    r = compute("업종 반도체")
    assert "800099" not in {x["ticker"] for x in r.rows}
    u = r.sector["unclassified"]
    assert u["n"] == 1 and u["cap_krw"] == 7 and u["top"][0]["ticker"] == "800099"
    assert any("미분류 1종목" in w and "신규 상장" in w for w in r.warnings)
    assert compute("코스피 반도체 업종").sector["unclassified"]["n"] == 0        # 코스피에는 미분류가 없다


def test_major_level_carries_mid_names_and_children_weights():
    r = compute("대분류 IT")
    assert {x["mid"] for x in r.rows} == {SEMI[1], SOFT[1], ELEC[1]}
    ch = {c["code"]: c for c in r.sector["children"]}
    assert ch["G4530"]["cap_krw"] == 950 and abs(sum(c["weight_pct"] for c in r.sector["children"]) - 100) < 0.001
    assert [c["code"] for c in r.sector["children"]][0] == "G4530"          # 시총 큰 순


def test_mid_level_rows_do_not_repeat_the_mid_name():
    assert all("mid" not in x for x in compute("업종 반도체").rows)


def test_retro_when_the_day_is_before_the_first_classification_snapshot():
    rows = [dict(r, snap="20260821", snap_min="20260821") for r in rows_fixture()]
    r = S.compute(q("업종 반도체"), rows, S.DayPick(dd="20260105"), NAMES)
    assert r.sector["classification_retro"] is True
    assert any("소급" in w and "생존 편향" in w for w in r.warnings)
    assert any("상장폐지" in w and "미분류 1종목" in w for w in r.warnings)       # 소급 구간의 미분류 사유는 신규 상장이 아니다
    ok = S.compute(q("업종 반도체"), rows, S.DayPick(dd="20261020"), NAMES)
    assert ok.sector["classification_retro"] is False and not any("소급" in w for w in ok.warnings)


def test_empty_market_slice_is_reported_not_faked():
    rows = [r for r in rows_fixture() if not (r["market"] == "KQ" and r["mid_code"] == "G4510")]
    r = S.compute(q("코스닥 소프트웨어 업종"), rows, DAY, NAMES)
    assert r.rows == [] and "소프트웨어와서비스" in r.notice and "KOSDAQ" in r.notice and "KOSPI" not in r.notice
    assert "다른 시장" in r.notice


def test_top_zero_is_rejected():
    assert "1 이상" in compute("업종 반도체 상위 0").question


def test_label_and_spec_describe_the_query():
    r = compute("코스피 반도체 업종 상위 3")
    assert r.label == "반도체와반도체장비(중분류) · KOSPI 시총상위 3" and r.spec == "sector:중분류:G4530:KS:top3"


# ── E: 끝에서 끝까지 ────────────────────────────────────────────────────────

@pytest.fixture
def fake_db(monkeypatch):
    calls = {}

    async def pick(as_of):
        calls["as_of"] = as_of
        return S.DayPick(dd="20261020", note="요청일 20261018 은 거래일이 아니어서(휴장·주말) 그 앞 거래일 20261016 로 풀었다." if as_of else "")
    monkeypatch.setattr(S, "_pick_day", pick)
    monkeypatch.setattr(S, "_read_rows", lambda dd, source: rows_fixture())

    async def names():
        return dict(NAMES)
    monkeypatch.setattr(uni, "_names_by_ticker", names)
    return calls


def test_payload_shape_and_wording(fake_db):
    p = asyncio.run(T.build_universe_payload("코스피 반도체 업종 상위 2", as_of="2026-10-18"))
    assert p["status"] == "ok" and fake_db["as_of"] == "2026-10-18"
    d = p["data"]
    assert d["scope"] == "universe" and d["as_of"] == "20261020" and d["requested_as_of"] == "2026-10-18"
    assert d["granularity"] == "daily" and d["n"] == 2 and d["sector"]["name"] == "반도체와반도체장비"
    assert d["rows"][0]["weight_pct"] > d["rows"][1]["weight_pct"]
    assert "분모" in d["method"] or "비중 = 종목 시총" in d["method"]
    assert p["warnings"][0].startswith("요청일 20261018")                    # 날짜를 푼 근거가 맨 앞


def test_payload_invalid_questions_pass_through(fake_db):
    p = asyncio.run(T.build_universe_payload("전기 업종"))
    assert p["status"] == "invalid" and "전기통신서비스" in p["warnings"][0]
    p = asyncio.run(T.build_universe_payload("KSIC 26 업종"))
    assert p["status"] == "invalid" and "KSIC" in p["warnings"][0] and "20260821" in p["warnings"][0]


def test_payload_empty_slice_is_no_data(fake_db, monkeypatch):
    monkeypatch.setattr(S, "_read_rows", lambda dd, source: [r for r in rows_fixture() if r["market"] == "KS"])
    p = asyncio.run(T.build_universe_payload("코스닥 반도체 업종"))
    assert p["status"] == "no_data"


def test_payload_db_down_is_db_error_not_empty(monkeypatch):
    async def pick(as_of):
        return S.DayPick(db_ok=False)
    monkeypatch.setattr(S, "_pick_day", pick)
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    p = asyncio.run(T.build_universe_payload("반도체 업종"))
    assert p["status"] == "db_error"


def test_as_of_is_called_out_when_it_cannot_apply_to_a_ranking(monkeypatch):
    async def fake_list(universe, as_of=""):
        return uni.UniverseList(spec="kospi:1", label="KOSPI 시총상위 1", resolved=True, as_of="20261016",
                                rows=[{"rank": 1, "ticker": "005930", "name": "삼성전자", "market": "KOSPI",
                                       "mktcap_krw": 1, "close_krw": 1, "list_shrs": 1}])
    monkeypatch.setattr(uni, "list_universe", fake_list)
    p = asyncio.run(T.build_universe_payload("코스피 시총 상위 1", as_of="20260101"))
    assert p["status"] == "ok" and "`as_of` 는 업종 구성" in p["warnings"][0]
    p = asyncio.run(T.build_universe_payload("코스피 시총 상위 1"))
    assert not any("as_of" in w for w in p["warnings"])


def test_md_table_has_weights_and_hides_nothing(fake_db):
    from open_proxy_mcp.tools import trading as tool
    p = asyncio.run(T.build_universe_payload("대분류 IT"))
    md = tool._render_universe_sector(p)
    assert md.startswith("# 대분류 IT — 구성 종목·시총 비중 (기준 20261020")
    assert "| 순위 | 종목 | 코드 | 시장 | 중분류 | 시가총액 | 비중 | 누적 | 종가 |" in md
    assert "## 중분류 구성" in md and "반도체와반도체장비" in md
    assert "가나전자 우선주" in md
    mid = asyncio.run(T.build_universe_payload("업종 반도체"))
    md_mid = tool._render_universe_sector(mid)
    assert "| 순위 | 종목 | 코드 | 시장 | 시가총액 | 비중 | 누적 | 종가 |" in md_mid and "## 중분류 구성" not in md_mid


def test_revision_screen_takes_the_same_sector_phrase_through_the_shared_universe_reader(fake_db, monkeypatch):
    """`forward_estimates_data` 의 리비전 스크린은 `list_universe` 를 같이 쓴다 — 업종 문구가 그대로 통한다(부수 효과, 계약 변화 없음: 종전엔 풀리지 않던 문구)."""
    from open_proxy_mcp.services import forward_estimates as fe
    seen = {}

    def pg(sql, params=()):
        seen["params"] = params
        return []
    monkeypatch.setattr(fe, "pg_rows", pg)
    p = asyncio.run(fe.build_revision_screen_payload("코스피 반도체 업종"))
    assert p["subject"].startswith("반도체와반도체장비(중분류) · KOSPI")
    assert seen["params"][1] == ["900010", "900020", "900015"]              # 업종 구성 종목 코드(우선주 포함 — 추정이 없으면 표에서 빠진다)

