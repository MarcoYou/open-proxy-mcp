"""trading_data — 공식 지수 조회(scope=index) · 금리 조회(scope=rate) · 여러 종목 시세 순위표(scope=quote + universe) ·
코스피200/코스닥150 시총 비중 칸 (2026-10-22). network 0콜 · KRX 0콜 · DART 0콜 · DB 는 가짜 읽기.

계기(사용자 유형 시험): 「오늘 코스피 몇 포인트」에 답할 범위가 없었고, 「코스피 상위 100 등락률 순위·52주 신고가」는 scope=quote 가 company 를 요구해
「company 가 필요합니다」로 끝났으며, 국고채 금리는 읽는 길이 없었다. 지키는 것:
  ① 지수: 이름 부분 일치 · 여럿에 걸리면 추측하지 않고 되묻는다 · DB 에 종가가 없는 계열은 값을 만들지 않는다 · 코스피 20261021 종가 5,735.66
  ② 금리: 최신값·기간 추이, 기준금리·환율은 DB 에 없다고 밝힌다
  ③ 순위표: universe 만 주어도 표가 나온다 · 정렬 3종 · 52주 고가/저가/대비/신고가 · 조정 이벤트 종목은 산출 불가 · 300행 상한
  ④ 기존 단일 종목 quote 호출은 그대로 · 지수 구성종목 표의 시총 비중 합 100%
실제 Postgres 에서의 SQL 은 `test_trading_screen_pg.py`.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from open_proxy_mcp import db, index_members as im, krx_api
from open_proxy_mcp.services import screener, universe as uni
from open_proxy_mcp.services import trading_screen as ts
from open_proxy_mcp.services.trading import build_universe_payload
from open_proxy_mcp.tools import trading as tool


def _trading_data():
    """등록된 공개 도구 함수 — 사용자가 부르는 경로(인자 → 분기 → 렌더러)로 검증한다."""
    class _Mcp:
        def __init__(self):
            self.fns = {}

        def tool(self):
            def deco(f):
                self.fns[f.__name__] = f
                return f
            return deco
    m = _Mcp()
    tool.register_tools(m)
    return m.fns["trading_data"]


def _call(**kw):
    return asyncio.run(_trading_data()(**kw))


# ── 가짜 DB ───────────────────────────────────────────────────────────────────

#: 계열 사전 (sid, src, name, kind, in_db, first_dd, last_dd)
_SERIES = [
    (1, "P", "코스피", "z", True, "20151230", "20261021"),
    (2, "P", "코스피 200", "z", True, "20151230", "20261021"),
    (3, "P", "전기전자", "s", True, "20151230", "20261021"),
    (36, "P", "코스피 200 건설", "s", False, "20151230", "20261020"),
    (54, "Q", "코스닥", "z", True, "20151230", "20261021"),
    (55, "Q", "코스닥 150", "z", True, "20151230", "20261021"),
    (72, "Q", "전기전자", "s", True, "20151230", "20261021"),
    (97, "X", "KRX 반도체", "s", True, "20151230", "20261021"),
]
#: 보관된 종가 (sid → [(YYYYMMDD, close_x100)]) — 코스피·코스닥은 운영 저장분의 실제 값이다(2026-10-22 읽기 전용 조회).
_CLOSES = {
    1: [("20251020", 381469), ("20251021", 382384), ("20251229", 422056), ("20251230", 421417), ("20260918", 689423),
        ("20260921", 700772), ("20261014", 600881), ("20261020", 555274), ("20261021", 573566)],
    54: [("20251020", 87577), ("20251021", 87250), ("20251229", 93259), ("20251230", 92547), ("20260918", 82712),
         ("20260921", 83627), ("20261014", 82388), ("20261020", 78197), ("20261021", 79276)],
    97: [("20261020", 100000), ("20261021", 101000)],
}
_RATE = [("20250102", 2.9), ("20251021", 2.95), ("20251230", 3.05), ("20260918", 3.7), ("20260921", 3.71), ("20261014", 3.7),
         ("20261020", 3.692), ("20261021", 3.688)]


def _dash(dd: str) -> str:
    return dd if "-" in dd else f"{dd[:4]}-{dd[4:6]}-{dd[6:]}"


class FakeDb:
    """`db.pg_rows` 대역 — SQL 에 든 표 이름으로 갈라 가짜 행을 준다. 부른 SQL·인자를 `calls` 에 쌓는다."""

    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []
        self.series = list(_SERIES)
        self.closes = dict(_CLOSES)
        self.rate = list(_RATE)
        self.days = ["20251014", "20260930", "20261020", "20261021"]       # 보관된 거래일
        self.day_rows: dict[str, tuple] = {}
        self.win_rows: dict[str, tuple] = {}
        self.ev_rows: list[tuple] = []

    def __call__(self, sql, params=()):
        self.calls.append((sql, params))
        if "FROM krx_idx_series" in sql:
            return self.series
        if "FROM krx_idx k JOIN m" in sql:
            sids, hi_arg = params
            hi_arg = hi_arg.replace("-", "")
            out = []
            for sid in sids:
                rows = [r for r in self.closes.get(sid, []) if r[0] <= hi_arg]
                if not rows:
                    continue
                hi = rows[-1][0]
                out += [(sid, d, v) for d, v in rows if d <= hi]
            return out
        if "FROM ecos_rate" in sql:
            hi_arg = params[0].replace("-", "")
            rows = [r for r in self.rate if r[0] <= hi_arg]
            hi = rows[-1][0] if rows else None
            since = params[1].replace("-", "") if params[1] else None
            from datetime import date, timedelta
            lo = since or (_iso(hi) - timedelta(days=365)).strftime("%Y%m%d") if hi else None
            lo = min(lo, (_iso(hi) - timedelta(days=375)).strftime("%Y%m%d")) if hi else None
            return [(d, r) for d, r in rows if d >= lo]
        if "FROM krx_day" in sql:
            ok = [d for d in self.days if d <= params[0]]
            return [(self.days[0], self.days[-1], ok[-1] if ok else None)]
        if "FROM krx_daily_v WHERE trd_dd" in sql:
            dd, tickers = params
            return [self.day_rows[t] for t in tickers if t in self.day_rows]
        if "GROUP BY ticker" in sql:
            tickers, _lo, _hi = params
            return [self.win_rows[t] for t in tickers if t in self.win_rows]
        if "FROM krx_adj_events" in sql:
            tickers = set(params[0])
            return [r for r in self.ev_rows if r[0] in tickers]
        raise AssertionError(f"예상 밖 SQL: {sql[:80]}")


def _iso(dd: str):
    from datetime import date
    return date(int(dd[:4]), int(dd[4:6]), int(dd[6:]))


@pytest.fixture
def fdb(monkeypatch):
    f = FakeDb()
    monkeypatch.setattr(db, "pg_rows", f)

    def no_krx(*a, **k):
        raise AssertionError("KRX 를 불렀다 — 이 범위는 저장분만 읽는다(KRX 0콜)")
    monkeypatch.setattr(krx_api, "fetch_day", no_krx)
    return f


# ── ① scope=index ────────────────────────────────────────────────────────────

def test_index_kospi_latest_point_and_returns(fdb):
    """「오늘 코스피 몇 포인트」 — 2026-10-21 종가 5,735.66(저장분 close_x100 573566) · 전일 대비 · 기간 수익률과 그 기준 날짜."""
    p = json.loads(_call(company="코스피", scope="index", format="json"))
    assert p["status"] == "ok"
    d = p["data"]
    assert d["scope"] == "index" and d["as_of"] == "20261021" and d["n"] == 1
    r = d["indices"][0]
    assert r["name"] == "코스피" and r["market"] == "코스피" and r["close"] == 5735.66
    assert r["prev_dd"] == "20261020" and r["prev_close"] == 5552.74
    assert r["change"] == 182.92 and r["change_pct"] == 3.29
    pr = r["periods"]
    assert (pr["1w"]["base_dd"], pr["1w"]["return_pct"]) == ("20261014", -4.55)          # 10-21 − 7일 = 10-14
    assert (pr["1m"]["base_dd"], pr["1m"]["return_pct"]) == ("20260921", -18.15)         # 한 달 전 = 09-21
    assert (pr["ytd"]["base_dd"], pr["ytd"]["return_pct"]) == ("20251230", 36.1)         # 전년 12-31 이하 마지막 = 12-30
    assert (pr["1y"]["base_dd"], pr["1y"]["return_pct"]) == ("20251021", 50.0)
    assert "총수익" in d["method"] and d["price_index"] is True


def test_index_md_shows_point_and_dates(fdb):
    md = _call(company="코스닥", scope="index")
    assert "5,735.66" not in md and "792.76" in md and "기준 20261021" in md
    assert "| 1년 |" in md and "20251021" in md
    md2 = _call(company="코스피", scope="index")
    assert "5,735.66" in md2 and "+182.92포인트" in md2


def test_index_spacing_and_market_prefix_resolve_without_asking(fdb):
    fdb.closes[2] = [("20261020", 85878), ("20261021", 89104)]
    r = json.loads(_call(company="코스피200", scope="index", format="json"))
    assert r["status"] == "ok" and r["data"]["indices"][0]["name"] == "코스피 200"
    r = json.loads(_call(company="KOSPI 200", scope="index", format="json"))
    assert r["status"] == "ok" and r["data"]["indices"][0]["close"] == 891.04
    fdb.closes[72] = [("20261020", 1000), ("20261021", 1100)]
    r = json.loads(_call(company="코스닥 전기전자", scope="index", format="json"))        # 시장 이름이 앞에 붙은 업종 → 코스닥 계열
    assert r["status"] == "ok" and r["data"]["indices"][0]["market"] == "코스닥"


def test_index_same_name_in_two_markets_asks_instead_of_guessing(fdb):
    """같은 이름(전기전자)이 코스피·코스닥 양쪽에 있다 — 한쪽으로 고르지 않고 후보를 주며 되묻는다(invalid)."""
    r = json.loads(_call(company="전기전자", scope="index", format="json"))
    assert r["status"] == "invalid" and "data" not in r
    msg = " ".join(r["warnings"])
    assert "코스피 전기전자" in msg and "코스닥 전기전자" in msg and "하나로 정하지 않았습니다" in msg
    assert not any("FROM krx_idx k" in c[0] for c in fdb.calls)                  # 종가를 읽지도 않았다


def test_index_title_does_not_repeat_the_market_when_the_name_already_starts_with_it(fdb):
    """병합 뒤 확인(2026-10-23)이 찾은 결함: 지수 이름 「코스피」에 시장 표기 「코스피」를 또 붙여 「# 코스피 코스피 — …」가 나왔다."""
    md = _call(company="코스피", scope="index")
    assert md.splitlines()[0] == "# 코스피 — 5,735.66 포인트 (기준 20261021 종가)"
    assert "코스피 코스피" not in md
    p = json.loads(_call(company="코스피", scope="index", format="json"))
    assert p["subject"] == "코스피 지수"                                       # 대상 이름 칸도 겹치지 않는다
    r = p["data"]["indices"][0]
    assert r["name"] == "코스피" and r["market"] == "코스피"                    # json 의 name·market 값은 그대로


def test_index_krx_prefixed_name_is_not_repeated_either(fdb):
    """SRC_LABEL 세 표기(코스피·코스닥·KRX) 모두에 같은 규칙 — src=X 의 「KRX 300」이 「# KRX KRX 300 …」로 나오지 않는다."""
    fdb.series.append((98, "X", "KRX 300", "z", True, "20151230", "20261021"))
    fdb.closes[98] = [("20261020", 180000), ("20261021", 181234)]
    md = _call(company="KRX 300", scope="index")
    assert md.splitlines()[0] == "# KRX 300 — 1,812.34 포인트 (기준 20261021 종가)"
    p = json.loads(_call(company="KRX 300", scope="index", format="json"))
    assert p["subject"] == "KRX 300 지수" and "KRX KRX" not in json.dumps(p, ensure_ascii=False)
    assert p["data"]["indices"][0]["name"] == "KRX 300" and p["data"]["indices"][0]["market"] == "KRX"
    # 코스닥도 같다
    assert _call(company="코스닥", scope="index").splitlines()[0].startswith("# 코스닥 — 792.76 포인트")


def test_index_no_data_subject_does_not_repeat_the_market(fdb):
    """DB 에 종가가 없는 「코스피 200 건설」 — 대상 이름과 안내문이 「코스피 코스피 200 건설」로 겹치지 않는다."""
    r = json.loads(_call(company="코스피 200 건설", scope="index", format="json"))
    assert r["status"] == "no_data" and r["subject"] == "코스피 200 건설"
    assert "코스피 코스피" not in json.dumps(r, ensure_ascii=False)
    assert "「코스피 200 건설」은 공식 지수 사전에는 있지만" in " ".join(r["warnings"])


def test_index_sector_keeps_the_market_label_to_tell_the_markets_apart(fdb):
    """시장 표기가 이름에 없는 업종 지수(전기전자)는 「코스피 전기전자」로 붙여 구분을 유지한다 — 고치기 전·후 모두 같다."""
    fdb.closes[3] = [("20261020", 100000), ("20261021", 101000)]
    md = _call(company="코스피 전기전자", scope="index")
    assert md.splitlines()[0] == "# 코스피 전기전자 — 1,010.00 포인트 (기준 20261021 종가)"
    p = json.loads(_call(company="코스피 전기전자", scope="index", format="json"))
    assert p["subject"] == "코스피 전기전자 지수"


def test_index_question_grammar_has_no_stray_hae(fdb):
    """되묻기 문구의 이유 조각 뒤에 「해」를 붙여 「있어해」·「여럿이라해」가 되던 결합 — 「있어서」·「여럿이라서」로 낸다."""
    same = " ".join(json.loads(_call(company="전기전자", scope="index", format="json"))["warnings"])
    assert "여러 시장에 있어서 하나로 정하지 않았습니다" in same and "있어해" not in same
    part = " ".join(json.loads(_call(company="200", scope="index", format="json"))["warnings"])
    assert "여럿이라서 하나로 정하지 않았습니다" in part and "여럿이라해" not in part


def test_index_partial_match_with_several_hits_asks_with_candidates(fdb):
    r = json.loads(_call(company="200", scope="index", format="json"))
    assert r["status"] == "invalid"
    msg = " ".join(r["warnings"])
    assert "코스피 200" in msg and "코스피 200 건설" in msg
    assert "(DB 에 일별 종가 없음)" in msg                                             # 후보에 DB 유무를 밝힌다


def test_index_partial_single_hit_is_used_and_said(fdb):
    r = json.loads(_call(company="반도체", scope="index", format="json"))
    assert r["status"] == "ok" and r["data"]["indices"][0]["name"] == "KRX 반도체"
    assert any("부분 일치" in w for w in r["warnings"])


def test_index_series_without_db_close_says_so_and_makes_no_value(fdb):
    r = json.loads(_call(company="코스피 200 건설", scope="index", format="json"))
    assert r["status"] == "no_data" and "data" not in r
    assert "DB 에 일별 종가가 없습니다" in " ".join(r["warnings"])
    assert not any("FROM krx_idx k" in c[0] for c in fdb.calls)


def test_index_unknown_name_is_not_found(fdb):
    r = json.loads(_call(company="없는지수", scope="index", format="json"))
    assert r["status"] == "not_found" and "찾지 못했습니다" in r["warnings"][0]


def test_index_without_name_lists_the_representative_indices(fdb):
    fdb.closes[2] = [("20261020", 85878), ("20261021", 89104)]
    r = json.loads(_call(scope="index", format="json"))
    names = [x["name"] for x in r["data"]["indices"]]
    assert r["status"] == "ok" and names == ["코스피", "코스피 200", "코스닥"]        # kind z + in_db + 종가 있음 (코스닥 150 은 종가 행이 없어 빠짐)
    assert "대표 지수" in _call(scope="index")


def test_index_as_of_uses_the_latest_day_on_or_before(fdb):
    r = json.loads(_call(company="코스피", scope="index", as_of="20261015", format="json"))
    assert r["data"]["as_of"] == "20261014" and r["data"]["indices"][0]["close"] == 6008.81
    assert any("그 이하 가장 늦은 보관 거래일 20261014" in w for w in r["warnings"])
    bad = json.loads(_call(company="코스피", scope="index", as_of="2026-13-40", format="json"))
    assert bad["status"] == "invalid"


def test_index_db_down_and_unset_are_told_apart(monkeypatch):
    monkeypatch.setattr(db, "pg_rows", lambda sql, params=(): None)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert json.loads(_call(company="코스피", scope="index", format="json"))["status"] == "db_unconfigured"
    monkeypatch.setenv("DATABASE_URL", "configured-but-unreachable")
    assert json.loads(_call(company="코스피", scope="index", format="json"))["status"] == "db_error"


# ── ② scope=rate ─────────────────────────────────────────────────────────────

def test_rate_latest_and_changes(fdb):
    r = json.loads(_call(scope="rate", format="json"))
    assert r["status"] == "ok"
    d = r["data"]
    assert d["scope"] == "rate" and d["as_of"] == "20261021" and d["latest"] == {"asof": "20261021", "rate": 3.688}
    ch = {c["key"]: c for c in d["changes"]}
    assert ch["1w"]["base_dd"] == "20261014" and ch["1w"]["change_bp"] == -1.2          # 3.688 − 3.700
    assert ch["ytd"]["base_dd"] == "20251230" and ch["ytd"]["change_bp"] == 63.8        # 3.688 − 3.05
    assert d["range"]["from"] == "20251021" and d["range"]["max"]["rate"] == 3.71        # 기본 구간 = 최근 1년
    assert d["not_in_db"] == ["기준금리", "원/달러 환율"]
    md = _call(scope="rate")
    assert "국고채 1년 금리 — 연 3.688%" in md and "기준금리와 원/달러 환율은 이 서버 DB 에 없습니다" in md


def test_rate_since_widens_the_series(fdb):
    r = json.loads(_call(scope="rate", since="20250101", format="json"))
    assert r["data"]["range"]["from"] == "20250102" and r["data"]["range"]["min"]["rate"] == 2.9


@pytest.mark.parametrize("ask", ["기준금리", "원달러 환율", "USD/KRW", "회사채", "국고채 3년", "CD금리"])
def test_rate_other_series_are_not_in_db_and_no_value_is_made(fdb, ask):
    r = json.loads(_call(company=ask, scope="rate", format="json"))
    assert r["status"] == "no_data" and "data" not in r
    assert "DB 에 없습니다" in r["warnings"][0] and "지어내지 않습니다" in r["warnings"][0]
    assert not any("ecos_rate" in c[0] for c in fdb.calls)


def test_rate_named_treasury_one_year_is_accepted(fdb):
    assert json.loads(_call(company="국고채 1년", scope="rate", format="json"))["status"] == "ok"


# ── ③ scope=quote + universe 순위표 ──────────────────────────────────────────

_NAMES = {"005930": "삼성전자", "000660": "SK하이닉스", "196170": "알테오젠", "001080": "만호제강", "469900": "하나31호스팩"}
_CAPS = [   # (ticker, market, mktcap, close, shrs) — 시총 큰 순
    ("005930", "KS", 500_000_000_000_000, 80_000, 6_250_000_000),
    ("000660", "KS", 300_000_000_000_000, 400_000, 750_000_000),
    ("196170", "KQ", 20_000_000_000_000, 100_000, 200_000_000),
    ("001080", "KS", 1_000_000_000_000, 20_000, 50_000_000),
    ("469900", "KQ", 100_000_000_000, 2_085, 48_000_000),
]


def _stub_universe(monkeypatch, caps=_CAPS, names=_NAMES, dd="20261021"):
    monkeypatch.setattr(screener, "_krx_latest_dd", lambda: dd)
    monkeypatch.setattr(screener, "_index_members_latest", lambda code: None)
    monkeypatch.setattr(screener, "_krx_top_mktcap",
                        lambda n, price_dd, market=None: {c[0] for c in sorted((c for c in caps if market is None or c[1] == market),
                                                                                key=lambda c: -c[2])[:n]})
    monkeypatch.setattr(screener, "_krx_market_codes", lambda market, dd: {c[0] for c in caps if c[1] == market})
    monkeypatch.setattr(uni, "_krx_rows", lambda price_dd, codes: [c for c in sorted(caps, key=lambda c: -c[2]) if codes is None or c[0] in codes])

    async def _names():
        return dict(names)
    monkeypatch.setattr(uni, "_names_by_ticker", _names)


def _day(t, close, high, low, vol, val, cap, chg, pct):
    return (t, close, high, low, vol, val, cap, chg, pct, 1)


def _fill_day(f: FakeDb):
    f.day_rows = {
        "005930": _day("005930", 80_000, 81_000, 79_000, 10_000_000, 800_000_000_000, 500_000_000_000_000, 960, 1.2),     # 신고가
        "000660": _day("000660", 400_000, 410_000, 395_000, 2_000_000, 800_000_000_000, 300_000_000_000_000, -6_000, -1.5),
        "196170": _day("196170", 100_000, 105_000, 98_000, 3_000_000, 300_000_000_000, 20_000_000_000_000, 5_000, 5.2),
        "001080": _day("001080", 20_000, 20_500, 19_800, 900_000, 18_000_000_000, 1_000_000_000_000, 0, 0.0),             # 5분할 뒤
        "469900": _day("469900", 2_085, 2_090, 2_080, 100_000, 200_000_000, 100_000_000_000, 8, 0.4),
    }
    # (ticker, 52주 고가, 52주 저가, 창 안 첫 보관일, 보관 행 수)
    f.win_rows = {
        "005930": ("005930", 81_000, 50_000, "20251022", 240),
        "000660": ("000660", 450_000, 200_000, "20251022", 240),
        "196170": ("196170", 130_000, 60_000, "20251022", 240),
        "001080": ("001080", 200_000, 18_000, "20251022", 240),       # 분할 전 가격이 섞여 고가가 현재가의 10배
        "469900": ("469900", 2_100, 2_040, "20251022", 240),
    }
    f.ev_rows = [
        ("001080", "20260309", 0.1, 200_000.0, 20_000.0, "KS"),                 # 5분할(×0.1) — 기업 행위
        ("469900", "20260410", 1.0014, 2_082.0, 2_085.0, "KQ"),                 # 기준가를 호가단위(5원)에 맞춘 것뿐 — 라벨이 아직 비어 코드로 가려야 한다
    ]


@pytest.fixture
def rank_env(fdb, monkeypatch):
    _stub_universe(monkeypatch)
    _fill_day(fdb)
    return fdb


def _rank(**kw):
    return asyncio.run(ts.build_quote_rank_payload(**kw))


def test_quote_with_only_universe_gives_a_ranking_not_company_error(rank_env):
    """고치기 전: scope=quote 에 company 가 없으면 무조건 「company 가 필요합니다」 오류."""
    md = _call(scope="quote", universe="코스피 시총 상위 3")
    assert "company 가 필요합니다" not in md
    assert md.startswith("# KOSPI 시총상위 3 — 시세 순위표 (등락률 큰 순 · 기준 20261021 · 3종목)")
    r = json.loads(_call(scope="quote", universe="코스피 시총 상위 3", format="json"))
    assert r["status"] == "ok" and r["data"]["scope"] == "quote" and r["data"]["view"] == "universe"


def test_quote_without_company_and_universe_still_asks_for_company(fdb):
    r = json.loads(_call(scope="quote", format="json"))
    assert r["status"] == "invalid" and "company 가 필요합니다" in r["warnings"][0]


def test_default_sort_is_change_pct_desc_with_krx_published_value(rank_env):
    p = _rank(universe="코스피, 코스닥")
    rows = p["data"]["rows"]
    assert [r["ticker"] for r in rows] == ["196170", "005930", "469900", "001080", "000660"]
    assert [r["rank"] for r in rows] == [1, 2, 3, 4, 5]
    assert rows[0]["change_pct"] == 5.2 and rows[-1]["change_pct"] == -1.5                  # 공표값 그대로(다시 계산하지 않는다)
    assert p["data"]["sort"] == "등락률 큰 순" and p["data"]["breadth"] == {"up": 3, "down": 1, "flat": 1}
    assert p["data"]["as_of"] == "20261021"


@pytest.mark.parametrize("sort, first, label", [
    ("하락률", "000660", "등락률 작은 순(하락률)"),
    ("거래대금", "005930", "거래대금 큰 순"),        # 005930·000660 동률 8000억 → 입력(시총) 순서 유지
    ("거래량", "005930", "거래량 큰 순"),
    ("volume", "005930", "거래량 큰 순"),
])
def test_sort_options(rank_env, sort, first, label):
    p = _rank(universe="코스피, 코스닥", sort=sort)
    assert p["status"] == "ok" and p["data"]["rows"][0]["ticker"] == first and p["data"]["sort"] == label


def test_unknown_sort_is_invalid_with_choices(rank_env):
    p = _rank(universe="코스피, 코스닥", sort="PER")
    assert p["status"] == "invalid" and "거래대금" in p["warnings"][0]


def test_week52_high_low_and_new_high(rank_env):
    rows = {r["ticker"]: r for r in _rank(universe="코스피, 코스닥")["data"]["rows"]}
    a = rows["005930"]
    assert (a["high52_krw"], a["low52_krw"]) == (81_000, 50_000)
    assert a["from_high52_pct"] == -1.23 and a["from_low52_pct"] == 60.0                      # 80,000 ÷ 81,000 − 1 · 80,000 ÷ 50,000 − 1
    assert a["new_high52"] is True                                                           # 기준일 고가 81,000 = 52주 고가
    b = rows["000660"]
    assert b["from_high52_pct"] == -11.11 and b["new_high52"] is False                       # 고가 410,000 < 450,000
    p = _rank(universe="코스피, 코스닥")
    assert p["data"]["new_high52_n"] == 1 and p["data"]["new_high52_tickers"] == ["005930"]
    assert p["data"]["window52"] == {"from": "20251022", "to": "20261021", "days": 364, "stored_from": "20251014", "covers_full": True}


def test_split_stock_week52_is_blank_not_ten_times_the_price(rank_env):
    """고치기 전(조정 이벤트를 모르는 계산): 만호제강 52주 고가 200,000 = 현재가 20,000 의 10배, 대비 −90%, 저가는 분할 뒤 값."""
    p = _rank(universe="코스피, 코스닥")
    s = next(r for r in p["data"]["rows"] if r["ticker"] == "001080")
    assert s["week52_blocked"] is True
    assert s["high52_krw"] is None and s["low52_krw"] is None
    assert s["from_high52_pct"] is None and s["from_low52_pct"] is None and s["new_high52"] is None
    assert s["close_krw"] == 20_000 and s["change_pct"] == 0.0                              # 종가·등락률 칸은 그대로
    assert p["data"]["week52_blocked_n"] == 1
    assert any("조정 이벤트로 산출 불가" in w and "1개" in w for w in p["warnings"])
    md = tool._render_quote_rank(p)
    assert "산출 불가*" in md and "* 산출 불가 1종목" in md and "조정 이벤트로 산출 불가" in md


def test_tick_rounding_event_does_not_block_week52(rank_env):
    """기준가를 호가단위에 맞춘 것뿐(2,082 → 2,085, 호가 5원)이면 기업 행위가 아니다 — 라벨이 비어 있어도 코드로 가려 52주 칸을 낸다."""
    rows = {r["ticker"]: r for r in _rank(universe="코스피, 코스닥")["data"]["rows"]}
    t = rows["469900"]
    assert t["week52_blocked"] is False and t["high52_krw"] == 2_100 and t["low52_krw"] == 2_040


def test_event_on_or_before_the_first_stored_day_does_not_block(rank_env):
    """이벤트 날 앞에 창 안 보관 행이 없으면 가격이 끊긴 구간이 창에 없다."""
    rank_env.ev_rows = [("001080", "20251022", 0.1, 200_000.0, 20_000.0, "KS")]            # 창 첫 보관일 = 20251022
    rows = {r["ticker"]: r for r in _rank(universe="코스피, 코스닥")["data"]["rows"]}
    assert rows["001080"]["week52_blocked"] is False and rows["001080"]["high52_krw"] == 200_000


def test_short_archive_is_said_with_the_real_start(rank_env):
    rank_env.days = ["20260105", "20261020", "20261021"]
    p = _rank(universe="코스피, 코스닥")
    w = p["data"]["window52"]
    assert w["covers_full"] is False and w["stored_from"] == "20260105"
    assert any("20260105 부터라 52주" in x for x in p["warnings"])
    rank_env.days = ["20251014", "20261020", "20261021"]
    assert not any("부터라 52주" in x for x in _rank(universe="코스피, 코스닥")["warnings"])  # 다 덮으면 말하지 않는다


def test_as_of_inside_window_reads_that_day_and_outside_is_invalid_without_krx(rank_env):
    p = _rank(universe="코스피, 코스닥", as_of="20260930")
    assert p["status"] == "ok" and p["data"]["as_of"] == "20260930"
    day_sql = [c for c in rank_env.calls if "FROM krx_daily_v WHERE trd_dd" in c[0]][-1]
    assert day_sql[1][0] == "20260930"
    assert any("종목 구성은 최신 거래일(20261021) 기준" in w for w in p["warnings"])
    old = _rank(universe="코스피, 코스닥", as_of="20240102")                               # krx_api.fetch_day 는 fdb 가 부르면 터뜨린다
    assert old["status"] == "invalid" and "보관 창(20251014~20261021) 밖" in old["warnings"][0]
    assert "KRX 라이브를 종목마다 부르지 않아" in old["warnings"][0]


def test_calendar_is_read_first_and_no_view_wide_minmax(rank_env):
    _rank(universe="코스피, 코스닥")
    sqls = [c[0] for c in rank_env.calls]
    assert any("FROM krx_day" in s for s in sqls)
    assert not any("min(trd_dd) FROM krx_daily_v" in s.replace("  ", " ") and "WHERE" not in s for s in sqls)


def test_ranking_caps_rows_and_says_it_was_cut(fdb, monkeypatch):
    big = [(f"{i:06d}", "KS", 10**12 * (1000 - i), 1000, 1) for i in range(1, 701)]
    _stub_universe(monkeypatch, caps=big, names={})
    fdb.day_rows = {c[0]: _day(c[0], 1000, 1010, 990, 100, 1000, c[2], 10, float(i % 41 - 20)) for i, c in enumerate(big)}
    fdb.win_rows = {c[0]: (c[0], 1100, 900, "20251022", 240) for c in big}
    p = _rank(universe="코스피 시총 상위 700")
    d = p["data"]
    assert d["n"] == 700 and len(d["rows"]) == 300 and d["truncated"] is True and d["omitted"] == 400
    assert [r["rank"] for r in d["rows"][:2]] == [1, 2] and d["rows"][149]["rank"] == 150 and d["rows"][150]["rank"] == 551 and d["rows"][-1]["rank"] == 700
    assert d["rows"][0]["change_pct"] == 20.0 and d["rows"][-1]["change_pct"] == -20.0       # 양 끝 = 상승 상위 · 하락 상위
    assert any("가운데 400종목 생략" in w for w in p["warnings"])
    md = tool._render_quote_rank(p)
    assert "가운데 400종목 생략" in md and md.count("\n| ") <= 303 + 2


def test_instrument_without_a_day_row_is_named_in_a_warning(rank_env):
    del rank_env.day_rows["469900"]
    p = _rank(universe="코스피, 코스닥")
    assert [r["ticker"] for r in p["data"]["rows"]] == ["196170", "005930", "001080", "000660"]
    assert any("1종목은 20261021 시세 행이 없어" in w for w in p["warnings"])


def test_ranking_db_states(monkeypatch):
    monkeypatch.setattr(screener, "_krx_latest_dd", lambda: None)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert _rank(universe="코스피 시총 상위 10")["status"] == "db_unconfigured"
    monkeypatch.setenv("DATABASE_URL", "configured-but-unreachable")
    assert _rank(universe="코스피 시총 상위 10")["status"] == "db_error"


def test_single_company_quote_response_is_unchanged(fdb, monkeypatch):
    """company 를 준 기존 호출은 순위표 분기를 타지 않는다 — universe 가 같이 와도 종전대로 그 종목 시세(회귀 same)."""
    seen = []

    async def fake_quote(company, format="md", as_of=""):
        seen.append((company, as_of))
        return {"tool": "trading_data", "status": "ok", "subject": "삼성전자(005930) 20261021 시세",
                "data": {"scope": "quote", "ticker": "005930", "as_of": "20261021", "market": "KOSPI",
                         "quote": {"close_krw": 80000, "change_krw": 960, "change_pct": 1.2, "open_krw": 79500, "high_krw": 81000,
                                   "low_krw": 79000, "volume": 1, "value_krw": 1, "mktcap_krw": 1, "list_shrs": 1},
                         "price_adjusted": False, "source": "db", "method": "m"},
                "warnings": []}
    monkeypatch.setattr(tool, "build_quote_payload", fake_quote)

    def boom(*a, **k):
        raise AssertionError("순위표 분기를 탔다")
    monkeypatch.setattr(tool, "build_quote_rank_payload", boom)
    md = _call(company="삼성전자", scope="quote", universe="코스피 시총 상위 3", as_of="20261021", sort="거래대금")
    assert seen == [("삼성전자", "20261021")] and md.startswith("# 삼성전자(005930) 20261021 시세")
    assert "| 종가 | 80,000원 |" in md


def test_unknown_scope_message_lists_index_and_rate(fdb):
    r = json.loads(_call(scope="nope", format="json"))
    assert r["status"] == "invalid" and "`firm / quote / market / sector / universe / index / rate` 중 선택" in r["warnings"][0]


# ── ④ 지수 구성종목 표의 시총 비중 ────────────────────────────────────────────

def test_index_members_table_gets_weight_column_summing_to_100(monkeypatch):
    """「코스피200」 구성종목 표에 시총 비중(%) 칸 — 합 100%, 공식(유동비율 반영) 비중이 아니라는 문구."""
    members = frozenset({"005930", "000660", "196170"})
    monkeypatch.setattr(screener, "_krx_latest_dd", lambda: "20261016")
    monkeypatch.setattr(screener, "_index_members_latest",
                        lambda code: im.Members("kospi200", "20261016", "20261016", members) if code == "kospi200" else None)
    caps = [("005930", "KS", 500_000_000_000_000, 80_000, 1), ("000660", "KS", 300_000_000_000_000, 400_000, 1),
            ("196170", "KS", 200_000_000_000_000, 100_000, 1), ("005380", "KS", 60_000_000_000_000, 280_000, 1)]
    monkeypatch.setattr(uni, "_krx_rows", lambda price_dd, codes: [c for c in sorted(caps, key=lambda c: -c[2]) if codes is None or c[0] in codes])

    async def names():
        return dict(_NAMES)
    monkeypatch.setattr(uni, "_names_by_ticker", names)
    p = asyncio.run(build_universe_payload("코스피200"))
    d = p["data"]
    assert [r["ticker"] for r in d["rows"]] == ["005930", "000660", "196170"]
    assert [r["weight_pct"] for r in d["rows"]] == [50.0, 30.0, 20.0]
    assert abs(sum(r["weight_pct"] for r in d["rows"]) - 100) <= 0.01
    assert d["weight_total_krw"] == 1_000_000_000_000_000
    assert "유동비율" in d["weight_basis"] and "공식 지수 비중이 아니다" in d["weight_basis"]
    md = tool._render_universe(p)
    assert "| 시총 비중 |" in md and "| 50.00% |" in md and md.count("공식 지수 비중이 아니다") == 1


def test_market_rank_table_has_no_weight_column(monkeypatch):
    """지수가 아닌 시총 순위표(원장 대체 포함)에는 비중 칸을 붙이지 않는다 — 응답이 종전 그대로."""
    _stub_universe(monkeypatch)
    p = asyncio.run(build_universe_payload("코스피 시총 상위 2"))
    assert "weight_pct" not in p["data"]["rows"][0] and "weight_basis" not in p["data"]
    assert "시총 비중" not in tool._render_universe(p)
    fb = asyncio.run(build_universe_payload("코스피200"))                            # 원장 대역 None → 시총 상위 정원으로 대체
    assert fb["status"] == "ok" and "weight_pct" not in fb["data"]["rows"][0]
