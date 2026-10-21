"""performance_analysis 서비스(`services/performance_analysis.py`) — 인자 해석·기간 해상도·구간 열·조립·가드·경고 (261022).

DB 읽기(`perf_data`)와 유니버스 해석(`list_universe`)을 대역으로 바꿔 계산·응답 조립을 시험한다 — 실제 표와 맞붙는 질의는 `test_perf_data_pg.py` 가 증명한다.
손계산 기대값은 구현과 따로 계산한 것이다(주석에 풀이). 이 시험은 모듈이 없던 main(39e6a672)에서 ImportError 로 실패한다.
"""
from __future__ import annotations

import asyncio

import pytest

from open_proxy_mcp.services import perf_data as D
from open_proxy_mcp.services import perf_math as M
from open_proxy_mcp.services import performance_analysis as PA
from open_proxy_mcp.services import price_multiple_data as pmd
from open_proxy_mcp.services.universe import UniverseList

DAYS = ["20260105", "20260106", "20260107", "20260108", "20260109"]
SEMI = D.SectorKey("G45", "IT", "G4530", "반도체와반도체장비")
AUTO = D.SectorKey("G25", "경기관련소비재", "G2510", "자동차와부품")
SOFT = D.SectorKey("G45", "IT", "G4510", "소프트웨어와서비스")

#: 종목 → (시장, 상장주식수, [종가...]) — DAYS 순서. X: +10%, 0, +10%, 0 / Y: 0, +10%, 0, −5% / W(대상 밖 코스피): 완만한 상승 / Z: 코스닥.
STOCKS = {
    "900010": ("KS", 1, [1000, 1100, 1100, 1210, 1210]),
    "900020": ("KS", 1, [2000, 2000, 2200, 2200, 2090]),
    "900030": ("KS", 1, [500, 520, 540, 560, 580]),
    "800010": ("KQ", 1, [300, 303, 306, 300, 309]),
}
SECTOR_OF = {"900010": SEMI, "900020": AUTO, "900030": SEMI, "800010": SOFT}
X, Y, W, Z = "900010", "900020", "900030", "800010"


class World:
    """perf_data 읽기와 list_universe 를 갈아 끼운 작은 시장."""

    def __init__(self, monkeypatch):
        self.mp = monkeypatch
        self.days = list(DAYS)
        self.stocks = {t: (m, s, list(c)) for t, (m, s, c) in STOCKS.items()}
        self.events: dict[str, list[M.AdjEvent]] = {}
        self.dividends: list[D.Dividend] = []
        self.scope = [D.ScopeRow(2025, "KOSPI", True, 2020)]
        self.index = [(dd, 1000.0 + v) for dd, v in zip(DAYS, (0, 10, 20, 0, 10))]            # 코스피: 1000, 1010, 1020, 1000, 1010
        self.rate_obs: list | None = [("20251231", 3.65)]
        self.series = [D.IndexSeries(1, "P", "코스피", "z", True), D.IndexSeries(2, "P", "코스피 200", "z", True),
                       D.IndexSeries(3, "P", "전기전자", "s", True), D.IndexSeries(4, "Q", "코스닥", "z", True),
                       D.IndexSeries(5, "Q", "전기전자", "s", True), D.IndexSeries(6, "X", "KRX 반도체", "s", False)]
        self.snaps = ["20260101"]
        self.weekly = None                      # 주간 시험용 WeeklyRead
        self.universe_rows: dict[str, list[str]] = {}
        self.calls: list[str] = []
        self.last_end_tail = None
        mp = monkeypatch
        mp.setattr(D, "read_daily_days", lambda: list(self.days))
        mp.setattr(D, "read_tids", lambda tickers: {t: i + 1 for i, t in enumerate(sorted(tickers))})
        mp.setattr(D, "read_daily_px", self._daily_px)
        mp.setattr(D, "read_adj_events", lambda tickers, lo, hi: {t: [e for e in self.events.get(t, []) if lo < e.event_dd <= hi] for t in tickers if t in self.events})
        mp.setattr(D, "read_markets", lambda tids, dd: {t: self.stocks[t][0] for t in tids if t in self.stocks} if dd in self.days else {})   # 실제처럼 일별 창 밖 날짜엔 행이 없다
        mp.setattr(D, "read_stock_flags", lambda tickers: {})
        mp.setattr(D, "read_sector_map", self._sector_map)
        mp.setattr(D, "read_sector_snaps", lambda: list(self.snaps))
        mp.setattr(D, "read_index_series", lambda: list(self.series))
        mp.setattr(D, "read_index_closes", lambda sid, lo, hi: [(dd, v) for dd, v in self.index if lo <= dd <= hi])
        mp.setattr(D, "read_rf", lambda lo, hi: None if self.rate_obs is None else list(self.rate_obs))
        mp.setattr(D, "read_dividends", lambda tickers, lo, hi: [d for d in self.dividends if d.ticker in tickers and lo <= d.record_dd <= hi])
        mp.setattr(D, "read_div_scope", lambda: list(self.scope))
        mp.setattr(D, "read_bench_daily", self._bench_daily)
        mp.setattr(D, "read_market_counts", lambda dd: {"KS": 3, "KQ": 1})
        mp.setattr(D, "pick_week_close", lambda req: "20260109" if req >= "20260109" else None)
        mp.setattr(PA, "list_universe", self._list_universe)
        mp.setattr(PA, "_names", self._names)
        mp.setattr(pmd, "_weekly_bounds", self._bounds)
        mp.setattr(D, "read_weekly", self._read_weekly)

    # ── 대역들
    async def _names(self):
        return {X: "가나전자", Y: "다라차", W: "마바소재", Z: "사아소프트"}

    async def _bounds(self):
        return "20260109", "20231016"

    async def _read_weekly(self, tickers, lo, hi, *, latest, floor):
        self.calls.append(f"weekly {lo} {hi}")
        return self.weekly

    def _daily_px(self, tids, lo, hi):
        out = {}
        for t in tids:
            _m, shrs, closes = self.stocks[t]
            rows = []
            for i, (dd, c) in enumerate(zip(self.days, closes)):
                if c is None or not (lo <= dd <= hi):
                    continue
                prev = closes[i - 1] if i and closes[i - 1] is not None else c
                rows.append((dd, c, c - prev, shrs))
            out[t] = rows
        return out

    def _sector_map(self, tickers):
        ivs = {t: [(self.snaps[0], None, SECTOR_OF[t])] for t in tickers if t in SECTOR_OF}
        return D.SectorMap(self.snaps, ivs)

    def _bench_daily(self, markets, lo, hi):
        out = []
        for i in range(1, len(self.days)):
            dd, prev = self.days[i], self.days[i - 1]
            if not (lo <= dd <= hi) or prev < lo:
                continue
            groups: dict = {}
            for t, (m, s, cl) in self.stocks.items():
                if m not in markets or t not in SECTOR_OF or cl[i] is None or cl[i - 1] is None:
                    continue
                cmp = cl[i] - cl[i - 1]
                g = groups.setdefault(SECTOR_OF[t], [0.0, 0.0, 0])
                g[0] += cmp * s
                g[1] += (cl[i] - cmp) * s
                g[2] += 1
            out += [D.BenchGroup(dd, k, v[0], v[1], v[2], 0) for k, v in groups.items()]
        return out

    async def _list_universe(self, text, as_of=""):
        kind = "custom:" if "," in text or text.strip() in self.stocks else "rank:"
        tickers = self.universe_rows.get(text) or [t.strip() for t in text.split(",") if t.strip() in self.stocks]
        if not tickers:
            return UniverseList(spec="", label=text, resolved=False, notice=f"「{text}」에 해당하는 종목이 없습니다.")
        rows = [{"rank": i + 1, "ticker": t, "name": "-", "market": "KOSPI" if self.stocks[t][0] == "KS" else "KOSDAQ",
                 "mktcap_krw": next(c for c in self.stocks[t][2] if c is not None) * self.stocks[t][1]} for i, t in enumerate(tickers)]
        return UniverseList(spec=kind + text, label=text, resolved=True, as_of=self.days[-1], rows=rows)


@pytest.fixture
def world(monkeypatch):
    return World(monkeypatch)


def run(**kw):
    kw.setdefault("target", f"{X}, {Y}")
    kw.setdefault("since", "20260105")
    return asyncio.run(PA.build_performance_payload(**kw))


# ═══ 대상 문법 ═══════════════════════════════════════════════════════════════════

def test_parse_target_forms():
    one = PA.parse_target("반도체 업종")
    assert not one.error and len(one.components) == 1 and one.components[0].weight == 1.0 and not one.blend
    assert PA.parse_target("반도체 업종=100").components[0].spec == "반도체 업종" and PA.parse_target("업종=반도체").components[0].spec == "업종=반도체"
    lst = PA.parse_target("005930, 000660")
    assert lst.components[0].basket is None and lst.components[0].spec == "005930, 000660"
    bsk = PA.parse_target("005930:40, 000660:60")
    assert bsk.components[0].basket == {"005930": pytest.approx(.4), "000660": pytest.approx(.6)}
    bl = PA.parse_target("반도체 업종=60; 대분류 금융=40")
    assert bl.blend and [c.weight for c in bl.components] == [pytest.approx(.6), pytest.approx(.4)]
    assert bl.components[0].label == "반도체 업종 60%"
    mix = PA.parse_target("005930:50, 000660:50 = 30; 반도체 업종 = 70%")
    assert mix.blend and mix.components[0].basket and mix.components[1].spec == "반도체 업종"


@pytest.mark.parametrize("text,frag", [
    ("", "target 을 지정"),
    ("반도체 업종=60; 대분류 금융=39", "합이 100(±0.5)이 아닙니다(현재 99)"),          # 99 → 맞춰 채우지 않는다
    ("반도체 업종=60; 대분류 금융", "비중이 없습니다"),
    ("반도체 업종=60", "구성요소가 하나뿐인데 비중이 60"),
    ("005930:40, 000660:50", "바스켓 비중 합이 100"),
    ("005930:40, 삼성전자", "모든 종목에 비중을 적어야"),
    ("005930:50, 005930:50", "두 번 있습니다"),
    ("; ".join(f"업종{i}=10" for i in range(11)), "최대 10개"),
])
def test_parse_target_errors_ask_instead_of_filling(text, frag):
    assert frag in PA.parse_target(text).error


def test_blend_weight_sum_error_is_invalid_status(world):
    p = run(target="반도체 업종=60; 대분류 금융=39")
    assert p["status"] == "invalid" and "합이 100" in p["warnings"][0]


def test_market_wide_phrases():
    assert PA.market_wide("코스피 전체") == ["KS"] and PA.market_wide("코스닥 전체") == ["KQ"]
    assert PA.market_wide("전체") == ["KS", "KQ"] and PA.market_wide("KOSPI 전체") == ["KS"]
    assert PA.market_wide("대분류 IT") is None and PA.market_wide("코스피") is None


# ═══ 기간·해상도 ═════════════════════════════════════════════════════════════════
DD = [f"2026{m:02d}{d:02d}" for m, d in ((10, 1), (10, 2), (10, 5), (10, 6), (10, 7), (10, 8), (10, 12), (10, 13), (10, 14), (10, 15), (10, 16))]


def test_plan_period_defaults_and_resolutions():
    p = PA.plan_period(DD, "", "20261005", "")
    assert (p.end, p.base, p.freq, p.requested_end) == ("20261016", "20261005", "daily", "20261016")
    wk = PA.plan_period(DD, "", "20260930", "")                                   # 시작이 일별 창(20261001~) 앞 → 주간
    assert wk.freq == "weekly" and wk.base is None and wk.since_req == "20260930"
    sat = PA.plan_period(DD, "20261010", "20261003", "")                           # 끝: 토 → 앞 거래일(10-08), 시작: 토 → 앞 거래일(10-02)
    assert sat.end == "20261008" and sat.base == "20261002" and len(sat.notes) == 2
    future = PA.plan_period(DD, "20271231", "20261005", "")
    assert future.end == "20261016" and "최신 거래일" in future.notes[0]
    dflt = PA.plan_period(DD, "", "", "")                                          # since 비우면 끝의 1년 전 → 창 밖 → 주간
    assert dflt.requested_since == "20251016" and dflt.freq == "weekly"
    leap = PA.one_year_before("20240229")
    assert leap == "20230228"


@pytest.mark.parametrize("kw,frag", [
    (dict(as_of="2026-13-01", since=""), "날짜가 아닙니다"),
    (dict(as_of="", since="abc"), "since"),
    (dict(as_of="", since="20261016"), "기간이 비었습니다"),
    (dict(as_of="", since="20260101", freq_req="daily"), "일별 보관 창"),              # 일별을 창 밖에서 요구 → invalid
])
def test_plan_period_errors(kw, frag):
    p = PA.plan_period(DD, kw["as_of"], kw["since"], kw.get("freq_req", ""))
    assert frag in p.error


def test_plan_period_old_end_needs_week_pick():
    p = PA.plan_period(DD, "20250103", "20241001", "")
    assert p.old_end and p.end == ""
    q = PA.plan_period(DD, "20250103", "20241001", "", "20250103")
    assert q.end == "20250103" and q.freq == "weekly" and not q.error
    assert PA.plan_period(DD, "20250103", "20241001", "", None).error and PA.plan_period(DD, "20250103", "20241001", "", False).error == "DB"


# ═══ 종목 구간 열 ═════════════════════════════════════════════════════════════════

def test_ticker_legs_daily_cmp_split_and_delisting():
    dates = ["20260105", "20260106", "20260107", "20260108"]
    rows = [("20260105", 50000, 0, 10), ("20260106", 10500, 500, 50), ("20260107", 10500, 0, 50)]     # 06일 5분할: 기준가 10,000, 종가 10,500 → +5%
    lg = PA.build_ticker_legs(rows, [], dates, "daily")
    assert lg.ret == pytest.approx([0.05, 0.0, 0.0]) and lg.last_idx == 2 and lg.priced_at_start
    assert lg.cap[0] == 500000 and lg.cap[1] == 525000 and lg.cap[3] == lg.cap[2]            # 시총은 분할에 불변이고 끊긴 뒤엔 직전 값
    assert lg.base[0] == 10000 and lg.base[2] is None
    none = PA.build_ticker_legs([("20260106", 10, 0, 1)], [], dates, "daily")
    assert none.priced_at_start is False and none.ret == [0.0, 0.0, 0.0]


def test_ticker_legs_cmp_null_fallback_tick_rounding_and_weekly_events():
    dates = ["20260105", "20260112", "20260119"]
    ev = [M.AdjEvent("20260114", 33333, 6667, "KS")]
    wk = PA.build_ticker_legs([("20260105", 33333, None, 1), ("20260112", 33000, None, 1), ("20260119", 7000, None, 1)], ev, dates, "weekly")
    assert wk.ret[0] == pytest.approx(33000 / 33333 - 1) and wk.ret[1] == pytest.approx(7000 / 33000 / (6667 / 33333) - 1)
    assert wk.events_used == 1
    dates2 = ["20260105", "20260106", "20260107"]
    old = PA.build_ticker_legs([("20260105", 2000, None, 1), ("20260106", 2100, None, 1), ("20260107", 2100, 0, 1)], [], dates2, "daily")
    assert old.cmp_missing == 1 and old.ret[0] == pytest.approx(0.05)
    tick = PA.build_ticker_legs([("20260105", 2082, 5, 1), ("20260106", 2100, 15, 1)], [M.AdjEvent("20260106", 2082, 2085, "KQ")], dates2[:2], "daily")
    assert tick.tick_skipped == 1 and tick.ret[0] == pytest.approx(2100 / 2082 - 1)           # 공표 기준가 2085 가 아니라 전일 종가 기준


def test_ticker_legs_daily_with_cmp_does_not_multiply_events_but_weekly_does():
    dates = ["20260105", "20260106"]
    ev = [M.AdjEvent("20260106", 50000, 10000, "KS")]
    d = PA.build_ticker_legs([("20260105", 50000, 0, 1), ("20260106", 10500, 500, 1)], ev, dates, "daily")
    assert d.ret == pytest.approx([0.05]) and d.events_used == 0                      # 공표 기준가(10,000)가 분할을 이미 반영 — 계수를 또 곱하지 않는다
    w = PA.build_ticker_legs([("20260105", 50000, None, 1), ("20260106", 10500, None, 1)], ev, dates, "weekly")
    assert w.ret == pytest.approx([0.05]) and w.events_used == 1
    nocmp = PA.build_ticker_legs([("20260105", 50000, None, 1), ("20260106", 10500, None, 1)], ev, dates, "daily")
    assert nocmp.ret == pytest.approx([0.05]) and nocmp.events_used == 1 and nocmp.cmp_missing == 1


def test_ticker_legs_dividend_in_the_ex_interval_only():
    dates = ["20260105", "20260106", "20260107"]
    rows = [("20260105", 10000, 0, 1), ("20260106", 9900, -100, 1), ("20260107", 9900, 0, 1)]
    lg = PA.build_ticker_legs(rows, [], dates, "daily", {0: 300.0})
    assert lg.ret[0] == pytest.approx((9900 + 300) / 10000 - 1) and lg.price_ret[0] == pytest.approx(-0.01) and lg.ret[1] == 0.0
    assert lg.dps_applied == 300.0


# ═══ 끝에서 끝까지: 손계산 ═══════════════════════════════════════════════════════
#   X: 1000→1100→1100→1210→1210 (+10%, 0, +10%, 0) · Y: 2000→2000→2200→2200→2090 (0, +10%, 0, −5%) · 시총 가중 1/3 : 2/3 표류
#   구간 수익률 3.3333%, 6.4516%, 3.3333%, −3.2258% · 누적 +10% · MDD −3.2258%(고점 20260108 → 저점 20260109)
#   코스피 1000, 1010, 1020, 1000, 1010 → +1.00% · 초과 +9.00%p · rf 연 3.65% → 구간당 1e-4(ACT/365, 1일)
#   변동성 77.855%, 샤프 11.5555, 베타 −0.39425, 알파 9.3527, 상관 −0.14311, 추적오차 86.544%, 정보비율 9.3515
def test_summary_hand_values(world):
    p = run(rf="")
    assert p["status"] == "ok", p
    d = p["data"]
    assert d["period"] == {"start": "20260105", "end": "20260109", "requested_start": "20260105", "requested_end": "20260109",
                           "freq": "daily", "n_obs": 4, "calendar_days": 4, "annualization": 365.25}
    m = d["metrics"]
    assert m["return_cum_pct"] == pytest.approx(10.0, abs=1e-6) and m["return_ann_pct"] is None            # 4일 — 연율을 내지 않는다
    assert m["vol_ann_pct"] == pytest.approx(77.8554, abs=1e-3) and m["mdd_pct"] == pytest.approx(-3.2258, abs=1e-3)
    assert (m["mdd_from"], m["mdd_to"]) == ("20260108", "20260109")
    assert m["sharpe"] == pytest.approx(11.5555, abs=1e-3)
    r = d["relative"]
    assert r["benchmark"] == "코스피"
    assert r["benchmark_source"] == "official" and r["benchmark_series"] == {"src": "P", "name": "코스피", "kind": "z"}
    assert r["benchmark_return_cum_pct"] == pytest.approx(1.0, abs=1e-6) and r["excess_cum_pct"] == pytest.approx(9.0, abs=1e-6)
    assert r["beta"] == pytest.approx(-0.3942, abs=1e-3) and r["alpha_ann_pct"] == pytest.approx(935.269, abs=0.01)
    assert r["corr"] == pytest.approx(-0.1431, abs=1e-3) and r["tracking_error_pct"] == pytest.approx(86.5443, abs=1e-2)
    assert r["info_ratio"] == pytest.approx(9.3515, abs=1e-3) and r["n_obs_aligned"] == 4
    assert "비워 시작일 비중이 더 큰 시장(KOSPI)" in r["benchmark_reason"]
    assert d["rf"]["avg_pct"] == pytest.approx(3.65) and d["rf"]["asof"] == "20251231" and d["rf"]["convention"] == "ACT/365" and d["rf"]["missing"] == 0
    assert d["target"]["n_members"] == 2 and d["target"]["rebalance"] == "none" and d["target"]["weights_basis"] == "시총(시작일)"
    assert d["coverage"]["price_adjusted"] is True and d["coverage"]["cold"] == {"used": False, "seam": None, "reason": None}
    assert p["warning_codes"] == [] and p["data"]["warning_codes"] == []


def test_equal_weight_monthly_rebalance_and_blend_drift(world):
    eq = run(weights="동일")
    # 동일 가중 표류: (1.21 + 1.045)/2 − 1 = 12.75%
    assert eq["data"]["metrics"]["return_cum_pct"] == pytest.approx(12.75, abs=1e-6)
    w2 = run(target=f"{X}:50, {Y}:50")                                             # 바스켓 50:50 — 동일 가중과 같다
    assert w2["data"]["metrics"]["return_cum_pct"] == pytest.approx(12.75, abs=1e-6) and w2["data"]["target"]["weights_basis"] == "바스켓 비중"
    bl = run(target=f"{X}={60}; {Y}=40")
    # 블렌드: 구성요소 X(1.21)와 Y(1.045)를 60:40 표류 → 0.6×1.21 + 0.4×1.045 − 1 = 14.4%
    assert bl["data"]["metrics"]["return_cum_pct"] == pytest.approx(14.4, abs=1e-6)
    assert [c["weight_pct"] for c in bl["data"]["target"]["components"]] == [pytest.approx(60.0), pytest.approx(40.0)]


def test_scope_extras_and_contract_keys(world):
    base_keys = {"scope", "period", "target", "metrics", "relative", "coverage", "rf", "method", "sources", "warning_codes"}
    s = run()
    assert set(s["data"]) == base_keys
    c = run(scope="contribution", group_by="종목")
    assert set(c["data"]) == base_keys | {"contributions", "contributions_by"}
    rows = {r["ticker"]: r for r in c["data"]["contributions"]}
    assert set(rows[X]) == {"ticker", "name", "component", "sector", "weight_start_pct", "weight_end_pct", "return_pct", "contribution_pct"}
    assert sum(r["contribution_pct"] for r in rows.values()) == pytest.approx(10.0, abs=1e-6)          # Carino — 종목 기여 합 = 누적
    assert rows[X]["weight_start_pct"] == pytest.approx(33.3333, abs=1e-3) and rows[X]["return_pct"] == pytest.approx(21.0, abs=1e-6)
    assert rows[Y]["return_pct"] == pytest.approx(4.5, abs=1e-6) and rows[X]["name"] == "-" and rows[X]["sector"] == SEMI.mid
    by_sec = run(scope="contribution", group_by="대분류")
    assert {r["name"] for r in by_sec["data"]["contributions"]} == {"IT", "경기관련소비재"}
    assert sum(r["contribution_pct"] for r in by_sec["data"]["contributions"]) == pytest.approx(10.0, abs=1e-6)
    ser = run(scope="series")
    assert set(ser["data"]) == base_keys | {"series"} and ser["data"]["series"][0] == {"dd": "20260105", "cum_pct": 0.0, "bench_cum_pct": 0.0}
    assert ser["data"]["series"][-1]["cum_pct"] == pytest.approx(10.0, abs=1e-6) and ser["data"]["series"][-1]["bench_cum_pct"] == pytest.approx(1.0, abs=1e-6)
    assert len(ser["data"]["series"]) == 5
    comp = run(target=f"{X}={60}; {Y}=40", scope="contribution", group_by="구성요소")
    assert [r["ticker"] for r in comp["data"]["contributions"]] == ["C1", "C2"]
    assert sum(r["contribution_pct"] for r in comp["data"]["contributions"]) == pytest.approx(14.4, abs=1e-6)


def test_attribution_with_computed_benchmark_closes_the_books(world):
    p = run(scope="attribution", group_by="중분류")
    assert p["status"] == "ok", p
    a = p["data"]["attribution"]
    assert a["linking"] == "carino" and a["group_by"] == "중분류"
    assert p["data"]["relative"]["benchmark_source"] == "computed" and p["data"]["relative"]["benchmark"] == "코스피 전체"
    assert "비워 대상 시장(KOSPI)" in p["data"]["relative"]["benchmark_reason"]
    assert abs(a["residual_pct"]) < 1e-6
    assert a["allocation_pct"] + a["selection_pct"] + a["interaction_pct"] == pytest.approx(a["active_pct"], abs=3e-4)     # 각각 소수 넷째 자리 반올림
    assert a["active_pct"] == pytest.approx(p["data"]["relative"]["excess_cum_pct"], abs=1e-6)         # 같은 초과수익
    by = {g["code"]: g for g in a["by_group"]}
    assert set(by) == {"G4530", "G2510"}                                                              # X·W 반도체, Y 자동차
    assert by["G2510"]["w_p_pct"] > 0 and by["G4530"]["w_b_pct"] > 0
    assert a["classification"] == {"first_snapshot": "20260101", "retro": False, "taxonomy_break": False}
    big = run(scope="attribution", group_by="대분류")
    assert {g["code"] for g in big["data"]["attribution"]["by_group"]} == {"G45", "G25"}


def test_attribution_rejects_official_index_and_stock_grouping(world):
    p = run(scope="attribution", benchmark="코스피")
    assert p["status"] == "invalid" and "계산 벤치마크만 됩니다" in p["warnings"][0] and "구성 이력이 없어" in p["warnings"][0]
    q = run(scope="attribution", group_by="종목")
    assert q["status"] == "invalid" and "중분류·대분류만" in q["warnings"][0]
    ok = run(scope="attribution", benchmark="코스피 전체")
    assert ok["status"] == "ok" and ok["data"]["relative"]["benchmark"] == "코스피 전체"


def test_official_benchmark_names_and_ambiguity(world):
    amb = run(benchmark="전기전자")
    assert amb["status"] == "invalid" and "여러 시장" in amb["warnings"][0]
    a200 = run(benchmark="코스피200")
    assert a200["status"] == "ok" and a200["data"]["relative"]["benchmark_series"]["name"] == "코스피 200"
    notdb = run(benchmark="KRX 반도체")
    assert notdb["status"] == "invalid" and "DB 밖 보관본" in notdb["warnings"][0]
    wide = run(benchmark="코스닥 전체")                                                              # 계산 벤치마크(시장 전 종목)
    assert wide["status"] == "ok" and wide["data"]["relative"]["benchmark_source"] == "computed"


def test_benchmark_failure_blanks_only_relative_metrics(world, monkeypatch):
    monkeypatch.setattr(D, "read_index_series", lambda: None)
    p = run()
    assert p["status"] == "ok" and p["data"]["relative"] is None and "benchmark_unavailable" in p["warning_codes"]
    assert p["data"]["metrics"]["return_cum_pct"] == pytest.approx(10.0, abs=1e-6)


# ═══ 무위험 금리 ═════════════════════════════════════════════════════════════════

def test_rf_unavailable_nulls_only_rate_based_metrics_and_status_stays_ok(world):
    world.rate_obs = None                                                           # 읽기 실패(권한 오류 포함) — db_error 로 올리지 않는다
    p = run()
    assert p["status"] == "ok"
    m, r = p["data"]["metrics"], p["data"]["relative"]
    assert m["sharpe"] is None and m["sortino"] is None and m["downside_vol_ann_pct"] is None and r["alpha_ann_pct"] is None
    assert m["vol_ann_pct"] == pytest.approx(77.8554, abs=1e-3) and r["beta"] is not None and r["tracking_error_pct"] is not None
    assert "rf_unavailable" in p["warning_codes"] and p["data"]["rf"]["missing"] == 4
    fixed = run(rf="3.65")
    assert fixed["data"]["metrics"]["sharpe"] == pytest.approx(11.5555, abs=1e-3) and fixed["data"]["rf"]["source"] == "user"
    assert run(rf="abc")["status"] == "invalid"
    world.rate_obs = [("20260106", 3.0)]                                           # 첫 구간 시작(01-05) 이전 관측이 없다 → 그 구간만 비운다
    part = run()
    assert part["data"]["rf"]["missing"] == 1 and "rf_unavailable" in part["warning_codes"] and part["data"]["metrics"]["sharpe"] is not None


# ═══ 총수익(배당) ═════════════════════════════════════════════════════════════════

def test_total_return_adds_dividend_on_ex_date_and_marks_pending(world):
    world.dividends = [D.Dividend(X, "20260108", "20251215", 55.0, "R1"),                    # 기준일 1/8(목) → 배당락 1/7(수) — 그 구간 X 수익률 0 → +5%
                       D.Dividend(Y, "20260109", "20260120", 100.0, "R2")]                   # 결의일이 끝 기준일 뒤 → 아직 몰랐다(pending)
    price = run(basis="price")
    total = run(basis="total")
    assert total["status"] == "ok"
    # X 의 수익률 열: +10%, 0 + 55/1100(=5%), +10%, 0 → 누적 1.10 × 1.05 × 1.10 − 1 = 27.05%
    c = run(scope="contribution", group_by="종목", basis="total")
    xr = next(r for r in c["data"]["contributions"] if r["ticker"] == X)
    assert xr["return_pct"] == pytest.approx(27.05, abs=1e-6)
    assert total["data"]["metrics"]["return_cum_pct"] > price["data"]["metrics"]["return_cum_pct"] + 1.0
    dv = total["data"]["coverage"]["dividend"]
    assert dv["basis"] == "total" and dv["gross"] is True and dv["ex_date_basis"] == "daily_calendar"
    assert dv["covered_weight_pct"] == pytest.approx(100.0) and dv["preferred_price_only"] == 0 and dv["n_pending"] == 1 and dv["pending"][0]["ticker"] == Y
    assert {"gross_total_return", "dividend_pending", "benchmark_price_only"} <= set(total["warning_codes"])
    assert price["data"]["coverage"]["dividend"]["basis"] == "price" and "gross_total_return" not in price["warning_codes"]


def test_total_return_is_price_only_for_preferred_and_before_ledger_window(world):
    world.stocks["900015"] = ("KS", 1, [500, 505, 510, 515, 520])
    world.dividends = [D.Dividend("900015", "20260108", None, 30.0, "R3")]            # 우선주는 DPS 칸이 없어 원장에 행이 없다 — 읽는 종목에 넣지 않는다
    p = run(target=f"{X}, 900015", basis="total")
    dv = p["data"]["coverage"]["dividend"]
    assert dv["preferred_price_only"] == 1 and dv["covered_weight_pct"] < 100.0 and "dividend_coverage" in p["warning_codes"]
    world.scope = [D.ScopeRow(2026, "KOSPI", True, 2026)]                                # 원장이 2026 결의부터 → 2025 년 시작은 원장 밖
    q = run(target=f"{X}, {Y}", basis="total", since="20260105")
    assert q["data"]["coverage"]["dividend"]["covered_weight_pct"] == pytest.approx(100.0)       # 시작 연도 2026 ≥ window_lo 2026 → 안
    world.scope = [D.ScopeRow(2027, "KOSPI", True, 2027)]
    r = run(basis="total")
    assert r["data"]["coverage"]["dividend"]["covered_weight_pct"] == 0.0 and "dividend_coverage" in r["warning_codes"]


def test_dividend_ledger_read_failure_degrades_to_price_return(world, monkeypatch):
    monkeypatch.setattr(D, "read_dividends", lambda *a: None)
    p = run(basis="total")
    assert p["status"] == "ok" and "dividend_coverage" in p["warning_codes"]
    assert p["data"]["metrics"]["return_cum_pct"] == pytest.approx(10.0, abs=1e-6)


# ═══ 상장폐지·시작일 시세 없음 ════════════════════════════════════════════════════

def test_delisted_member_is_held_as_cash(world):
    world.stocks[Y] = ("KS", 1, [2000, 2000, 2200, None, None])                       # 20260108 부터 시세 없음 — 20260107 이 마지막 거래일
    p = run()
    assert p["status"] == "ok" and "delisted_cash" in p["warning_codes"]
    assert p["data"]["coverage"]["delisted_in_period"] == [{"ticker": Y, "last_dd": "20260107"}]
    # Y 는 20260107(+10%) 까지 오르고 그 뒤 0% — 누적 X 1.21 / Y 1.10 → 1/3×1.21 + 2/3×1.10 − 1 = 13.6667%
    assert p["data"]["metrics"]["return_cum_pct"] == pytest.approx(13.6667, abs=1e-3)


def test_member_without_start_price_is_dropped_and_weights_renormalised(world):
    world.stocks[Y] = ("KS", 1, [None, 2000, 2200, 2200, 2090])
    p = run()
    assert p["status"] == "ok" and p["data"]["target"]["n_members"] == 1
    assert p["data"]["coverage"]["not_priced_at_start"][0]["ticker"] == Y
    assert p["data"]["metrics"]["return_cum_pct"] == pytest.approx(21.0, abs=1e-6)          # X 만 — 1000 → 1210


# ═══ 가드 ═══════════════════════════════════════════════════════════════════════

def test_row_limit_and_position_limit_return_invalid(world, monkeypatch):
    names = [f"{900000 + i:06d}" for i in range(300)]
    for t in names:
        world.stocks[t] = ("KS", 1, [1000, 1001, 1002, 1003, 1004])
    world.universe_rows["코스피 시총 상위 300"] = names
    monkeypatch.setenv("OPM_PA_MAX_ROWS", "1000")                                       # 300종목 × 5행 = 1,500 > 1,000
    p = run(target="코스피 시총 상위 300")
    assert p["status"] == "invalid" and "읽는 상한" in p["warnings"][0] and "row_limit" in p["warning_codes"]
    monkeypatch.delenv("OPM_PA_MAX_ROWS")
    big = [f"{800000 + i:06d}" for i in range(501)]
    for t in big:
        world.stocks[t] = ("KQ", 1, [1000] * 5)
    world.universe_rows["코스닥 전체"] = big
    q = run(target="코스닥 전체")
    assert q["status"] == "invalid" and "상한(일별 500개)" in q["warnings"][0]


def test_rss_guard_rejects_before_heavy_reads(world, monkeypatch):
    from open_proxy_mcp import cold_store
    monkeypatch.setattr(cold_store, "_rss_mb", lambda: 5000.0)
    p = run()
    assert p["status"] == "invalid" and "rss_guard" in p["warning_codes"]


def test_db_failures_map_to_status(world, monkeypatch):
    monkeypatch.setattr(D, "read_daily_days", lambda: None)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert run()["status"] == "db_unconfigured"
    monkeypatch.setenv("DATABASE_URL", "configured-but-unreachable")
    assert run()["status"] == "db_error"


def test_unresolved_universe_is_not_found_and_question_is_invalid(world):
    p = run(target="없는 업종 문구")
    assert p["status"] == "not_found"
    world.mp.setattr(PA, "list_universe", lambda text, as_of="": _async(UniverseList(spec="", label=text, resolved=False, question="어느 업종인지 알 수 없습니다.")))
    q = run(target="반도체 업종")
    assert q["status"] == "invalid" and "어느 업종" in q["warnings"][0]


async def _async(v):
    return v


# ═══ 주간·콜드·업종 체계 단절 ═══════════════════════════════════════════════════
def _weekly(rows_by, **kw):
    return D.WeeklyRead(rows=rows_by, **kw)


WK_ROWS = [("20181228", 100, 1), ("20190104", 101, 1), ("20190111", 102, 1), ("20231020", 150, 1), ("20260109", 200, 1)]


def test_weekly_with_cold_flags_taxonomy_break_cold_coverage_and_truncation(world):
    world.weekly = _weekly({X: list(WK_ROWS), Y: [(d, c * 2, s) for d, c, s in WK_ROWS]}, cold_used=True, seam="20231016", n_cold_rows=6, floor="20231016")
    p = run(target=f"{X}:50, {Y}:50", since="20190102", scope="contribution", group_by="중분류")
    assert p["status"] == "ok", p
    d = p["data"]
    assert d["period"]["freq"] == "weekly" and d["period"]["start"] == "20181228" and d["period"]["requested_start"] == "20190102"
    assert d["coverage"]["cold"]["used"] is True and d["coverage"]["cold"]["seam"] == "20231016"
    codes = set(p["warning_codes"])
    assert {"sector_taxonomy_break", "cold_coverage", "sector_retroactive", "survivorship"} <= codes
    assert {"benchmark_truncated", "rf_unavailable"} <= codes                          # 지수·금리는 3년 밖에 없다 → 겹치는 구간만
    assert d["relative"]["n_obs_aligned"] == 0 and d["relative"]["excess_cum_pct"] is None
    assert [r["name"] for r in d["contributions"]] and abs(sum(r["contribution_pct"] for r in d["contributions"]) - d["metrics"]["return_cum_pct"]) < 1e-3
    assert world.calls and world.calls[0].startswith("weekly 20181223")                # 시작일 10일 앞에서 읽어 기준 주를 찾는다


def test_weekly_cold_off_truncates_start_to_db_floor_without_partial_curve(world):
    rows = [("20231020", 150, 1), ("20231027", 151, 1), ("20260109", 200, 1)]
    world.weekly = _weekly({X: rows, Y: rows}, cold_used=False, reason="off", floor="20231016")
    p = run(since="20190102")
    assert p["status"] == "ok"
    assert p["data"]["period"]["start"] == "20231020" and p["data"]["period"]["requested_start"] == "20190102" and p["data"]["period"]["freq"] == "weekly"
    assert p["data"]["coverage"]["cold"] == {"used": False, "seam": None, "reason": "off"}
    assert "cold_coverage" in p["warning_codes"] and any("잘랐다" in w and "부분 곡선은 내지 않는다" in w for w in p["warnings"])


def test_weekly_end_tail_is_appended_when_end_is_mid_week(world):
    rows = [("20260102", 100, 1)]                                                       # 20260105 이전 주 닫는 날 하나, 끝(20260109)은 이번 주 중간·일별에서 끝점을 잇는다
    world.weekly = _weekly({X: rows, Y: rows}, floor="20231016")
    p = run(since="20260102", freq="weekly")
    assert p["status"] == "ok" and p["data"]["period"]["end"] == "20260109" and p["data"]["period"]["n_obs"] == 1 and p["data"]["period"]["freq"] == "weekly"


def test_weekly_computed_benchmark_before_db_floor_is_refused(world):
    rows = [("20181228", 100, 1), ("20231020", 150, 1), ("20260109", 200, 1)]
    world.weekly = _weekly({X: rows, Y: rows}, cold_used=True, seam="20231016", floor="20231016")
    p = run(since="20190102", benchmark="코스피 전체")
    assert p["status"] == "invalid" and "전 시장 계산 벤치마크" in p["warnings"][0] and "benchmark_truncated" in p["warning_codes"]


def test_old_end_date_is_resolved_with_week_close(world, monkeypatch):
    monkeypatch.setattr(D, "pick_week_close", lambda req: "20230106")
    rows = [("20221230", 100, 1), ("20230106", 101, 1)]
    world.weekly = _weekly({X: rows, Y: rows}, floor="20221201")
    p = run(since="20221230", as_of="2023-01-08", freq="weekly")
    assert p["status"] == "ok" and p["data"]["period"]["end"] == "20230106" and p["data"]["period"]["requested_end"] == "20230108"
    assert any("그 앞 주 마지막 거래일" in w for w in p["warnings"])


def test_argument_validation(world):
    for kw, frag in ((dict(scope="x"), "scope"), (dict(basis="net"), "basis"), (dict(rebalance="weekly"), "rebalance"),
                     (dict(weights="임의"), "weights"), (dict(freq="hourly"), "freq"), (dict(group_by="x"), "group_by")):
        p = run(**kw)
        assert p["status"] == "invalid" and frag in p["warnings"][0], (kw, p)
    assert run(rebalance="월간", group_by="종목", basis="가격", weights="동일")["status"] == "ok"
    assert run(since="2026-01-05", as_of="2026/01/09")["status"] == "ok"


def test_monthly_rebalance_without_month_boundary_equals_none(world):
    a, b = run(rebalance="monthly"), run(rebalance="none")
    assert a["data"]["metrics"] == b["data"]["metrics"]


def test_monthly_rebalance_resets_equal_weights_but_cap_weights_follow_the_market(world):
    # 2월→3월 경계가 있는 5개 관측: X 는 매 구간 +10%, Y 는 −10% (시총 1000 : 1000 에서 출발)
    world.days = ["20260130", "20260202", "20260203", "20260227", "20260302"]
    world.stocks[X] = ("KS", 1, [1000, 1100, 1210, 1331, 1464])
    world.stocks[Y] = ("KS", 1, [1000, 900, 810, 729, 656])
    world.index = [(d, 1000.0) for d in world.days]
    drift = run(weights="동일", since="20260130", rebalance="none")
    monthly = run(weights="동일", since="20260130", rebalance="monthly")
    # 종가 반올림(1464·656)이 있어 표류 값이 0.0601 에서 조금 벗어난다 — 손계산은 정수 종가로: X 1.464, Y 0.656 → (1.464+0.656)/2 − 1 = 6.0%
    assert drift["data"]["metrics"]["return_cum_pct"] == pytest.approx(6.0, abs=1e-6)
    # 3구간 뒤 V=(1.331+0.729)/2=1.03, 50:50 으로 되돌린 넷째 구간 수익률 = .5×(1464/1331−1) + .5×(656/729−1)
    last = 0.5 * (1464 / 1331 - 1) + 0.5 * (656 / 729 - 1)
    assert monthly["data"]["metrics"]["return_cum_pct"] == pytest.approx(((1.03) * (1 + last) - 1) * 100, abs=1e-4)
    assert monthly["data"]["target"]["rebalance"] == "monthly"
    cap = run(weights="시총", since="20260130", rebalance="monthly")           # 시총 가중은 그 날 시총으로 되돌려 표류와 같다(주식 수 변화 없음)
    nocap = run(weights="시총", since="20260130", rebalance="none")
    assert cap["data"]["metrics"]["return_cum_pct"] == pytest.approx(nocap["data"]["metrics"]["return_cum_pct"], abs=1e-9)
    quarterly = run(weights="동일", since="20260130", rebalance="quarterly")
    assert quarterly["data"]["metrics"]["return_cum_pct"] == pytest.approx(drift["data"]["metrics"]["return_cum_pct"], abs=1e-9)   # 2월→3월은 같은 분기 — 되돌리지 않는다


# ═══ 주간 계산 벤치마크·고정 구성 계산 벤치마크 ═════════════════════════════════════
def test_weekly_market_benchmark_is_cap_weighted_by_previous_adjusted_cap(world, monkeypatch):
    # 주간 격자 20251226 → 20260102 → 20260109(끝, 일별 저장분 끝점). 대상 X·Y, 코스피 전체는 X·Y·W.
    #   구간 1: Σ(종가−기준가)·주식수 = 100+200+100 = 400 / Σ 기준가·주식수 = 900+1800+400 = 3100 → +12.9032%
    #   구간 2: 210+90+80 = 380 / 1000+2000+500 = 3500 → +10.8571% · 누적 +25.1613%
    tgt = {X: [("20251226", 900, 1), ("20260102", 1000, 1)], Y: [("20251226", 1800, 1), ("20260102", 2000, 1)]}
    world.weekly = _weekly(tgt, floor="20231016")
    mk = {X: [("20251226", 900, 1), ("20260102", 1000, 1), ("20260109", 1210, 1)], Y: [("20251226", 1800, 1), ("20260102", 2000, 1), ("20260109", 2090, 1)],
          W: [("20251226", 400, 1), ("20260102", 500, 1), ("20260109", 580, 1)]}
    seen = {}

    def fake_mk(markets, lo, hi, tail_dd=None):
        seen["args"] = (list(markets), lo, hi, tail_dd)
        return mk
    monkeypatch.setattr(D, "read_weekly_market_px", fake_mk)
    p = run(since="20251226", benchmark="코스피 전체", freq="weekly")
    assert p["status"] == "ok", p
    r = p["data"]["relative"]
    assert r["benchmark_source"] == "computed" and r["benchmark_return_cum_pct"] == pytest.approx(25.1613, abs=1e-3)
    assert seen["args"] == (["KS"], "20251226", "20260109", "20260109")                    # 끝 기준일 끝점을 함께 요청한다
    assert p["data"]["period"]["freq"] == "weekly" and p["data"]["period"]["n_obs"] == 2
    a = run(since="20251226", scope="attribution", group_by="대분류", freq="weekly")
    assert a["status"] == "ok" and abs(a["data"]["attribution"]["residual_pct"]) < 1e-6
    assert a["data"]["attribution"]["active_pct"] == pytest.approx(a["data"]["relative"]["excess_cum_pct"], abs=1e-6)


def test_weekly_market_benchmark_row_limit_is_estimated_before_reading(world, monkeypatch):
    world.weekly = _weekly({X: [("20251226", 900, 1)], Y: [("20251226", 1800, 1)]}, floor="20231016")
    monkeypatch.setattr(D, "read_market_counts", lambda dd: {"KS": 950, "KQ": 1800})
    called = []
    monkeypatch.setattr(D, "read_weekly_market_px", lambda *a, **k: called.append(a) or {})
    monkeypatch.setenv("OPM_PA_MAX_ROWS", "1000")
    p = run(since="20251226", benchmark="전체", freq="weekly")
    assert p["status"] == "invalid" and "계산 벤치마크(전 시장 주간)" in p["warnings"][0] and not called


def test_fixed_membership_computed_benchmark_with_sector_groups(world):
    world.universe_rows["대분류 IT"] = [X, W, Z]                                           # 시작일 구성을 고정한 시총 가중 계산 벤치마크
    p = run(benchmark="대분류 IT")
    assert p["status"] == "ok", p
    r = p["data"]["relative"]
    assert r["benchmark_source"] == "computed" and r["benchmark_series"]["kind"] == "universe" and "시작일 구성을 고정한" in r["benchmark_reason"]
    # 시총 가중(시작일 시총 1000 : 500 : 300) 표류 → X 1.21, W 1.16, Z 1.03 → (1000×1.21 + 500×1.16 + 300×1.03)/1800 − 1
    want = (1000 * 1.21 + 500 * 1.16 + 300 * 1.03) / 1800 - 1
    assert r["benchmark_return_cum_pct"] == pytest.approx(want * 100, abs=1e-4)
    att = run(scope="attribution", benchmark="대분류 IT", group_by="대분류")
    assert att["status"] == "ok" and abs(att["data"]["attribution"]["residual_pct"]) < 1e-6
    assert att["data"]["attribution"]["active_pct"] == pytest.approx(att["data"]["relative"]["excess_cum_pct"], abs=1e-6)
    unknown = run(benchmark="없는 유니버스 문구")
    assert unknown["status"] == "invalid" and "공식 지수" in unknown["warnings"][0]


def test_weekly_default_benchmark_follows_the_members_market_not_the_daily_window(world):
    rows = [("20251226", 300, 1), ("20260102", 305, 1)]
    world.weekly = _weekly({Z: rows}, floor="20231016")
    world.index = [("20251226", 800.0), ("20260102", 808.0), ("20260109", 812.0)]
    p = run(target=f"{Z}:100", since="20251226", freq="weekly")
    assert p["status"] == "ok" and p["data"]["relative"]["benchmark"] == "코스닥"         # 기준일이 일별 창 밖이라 시장 표는 최신 거래일 기준
    assert p["data"]["relative"]["benchmark_series"]["src"] == "Q"
