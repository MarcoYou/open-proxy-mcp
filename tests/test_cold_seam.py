# -*- coding: utf-8 -*-
"""콜드 서빙 이음매 — 3년 하한 이전 주간 곡선을 DB 밖 서빙본에서 읽어 DB 행과 잇는다(설계 v03 D2, 261019).

지키는 것
  ① 꺼짐(`OPM_COLD`≠1, 기본) — 서빙 목록도 보지 않고 응답이 종전 그대로다
  ② 이음매 S = min(B, coverage.max_dd 다음 날) — 콜드 date < S, DB date >= S, 같은 날짜 중복 0(DB 의 S 이전 행은 쓰지 않는다)
  ③ 강등(목록·파일 실패, 이음매 틈) — 종전 응답 + 일시 안내 한 줄(그 줄을 빼면 꺼짐 응답과 글자까지 같다)
  ④ since 가 S 이후면 콜드를 보지 않는다 · 일별 끝점 잇기·연말 밴드는 그대로
network 0 · DB 0 — 가짜 `_pg_rows` 와 로컬 file:// 서빙본(`cold_serving` 고정물)."""
from __future__ import annotations

import asyncio
import json

import pytest

from open_proxy_mcp import cold_store as C
from open_proxy_mcp import krx_retention as R
from open_proxy_mcp.krx_retention import COLD_DEGRADED_NOTE as NOTE
from open_proxy_mcp.services import price_multiple_data as S
from open_proxy_mcp.services import trading as T
from open_proxy_mcp.tools.price_multiple_data import _render_firm_history
from open_proxy_mcp.tools.trading import _render_firm

SH = 5_969_782_550


def _w(dd, close, shrs=SH, market="KS"):
    return (dd, market, close, close * shrs, shrs)


def _c(dd, close, ticker="005930", shrs=SH, market="KS"):
    """서빙본 한 행 — (ticker, price_dd, market, close, list_shrs)."""
    return (ticker, dd, market, close, shrs)


COLD_ROWS = [_c("20151230", 25_000), _c("20160108", 24_000), _c("20190405", 46_000), _c("20210625", 81_000),
             _c("20210702", 80_000), _c("20221229", 55_300), _c("20231006", 66_000), _c("20231013", 68_000),
             _c("20171229", 50_000, ticker="000020")]                 # 다른 종목 — 섞이지 않는다
DB_WEEKLY = [_w("20221229", 55_300),                                  # 연말 행(S 이전) — 곡선에 쓰지 않는다
             _w("20231020", 69_000), _w("20231027", 67_000), _w("20261009", 72_000), _w("20261016", 73_000)]


# ═════════════════════════════════════════════════════════════════════════════
# 0. 순수 규칙
# ═════════════════════════════════════════════════════════════════════════════
def test_serving_seam_is_min_of_cutoff_and_day_after_coverage():
    assert R.serving_seam("20231016", "20231015") == "20231016"          # 오늘: S = B
    assert R.serving_seam("20231023", "20231015") == "20231016"          # B 가 앞서면 coverage 다음 날
    assert R.serving_seam("20231016", "20240104") == "20231016"          # coverage 가 B 를 넘으면 B
    assert R.serving_seam("20240301", "20240228") == "20240229"          # 윤일
    with pytest.raises(ValueError):
        R.serving_seam("2023-10-16", "20231015")


@pytest.mark.parametrize("seam,floor,gap", [
    ("20231016", "20231016", False), ("20231023", "20231016", False),   # S ≥ floor — DB 가 S 이후를 다 갖는다
    ("20231014", "20231016", False),                                     # 토·일만 — 빠지는 거래 주 없음
    ("20231230", "20240101", False),                                     # 토·일 + 1월 1일 다음이 월
    ("20231009", "20231016", True), ("20231229", "20240102", True),      # 평일이 빈다 — 틈
])
def test_seam_gap(seam, floor, gap):
    assert R.seam_gap(seam, floor) is gap


# ═════════════════════════════════════════════════════════════════════════════
# 1. trading_data firm
# ═════════════════════════════════════════════════════════════════════════════
def _route(monkeypatch, weekly=DB_WEEKLY, tail=(), *, bounds=("20261016", "20231016"), adj=()):
    calls = []

    async def fake_resolve(q):
        return {"corp_name": "삼성전자", "stock_code": "005930"}, None

    async def fake_bounds():
        return bounds

    def fake_rows(sql, params=()):
        s = " ".join(sql.split())
        calls.append((s, params))
        if "FROM krx_weekly" in s:
            return [r for r in weekly if r[0] >= params[1]]
        if "FROM price_latest" in s:
            return list(tail)
        if "FROM krx_adj_events" in s:
            return [(d, f) for d, f in adj if params[1] <= d <= params[2]]
        raise AssertionError(f"모르는 SQL: {s}")

    monkeypatch.setattr(T, "_resolve_listed", fake_resolve)
    monkeypatch.setattr(T, "_weekly_bounds", fake_bounds)
    monkeypatch.setattr(T, "_pg_rows", fake_rows)
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    return calls


def _firm(monkeypatch, **kw):
    route = {k: kw.pop(k) for k in ("weekly", "tail", "bounds", "adj") if k in kw}
    calls = _route(monkeypatch, **route)
    return asyncio.run(T.build_firm_series_payload("삼성전자", format="json", **kw)), calls


def _spy_ready(monkeypatch):
    seen = []
    real = C.ready

    async def spy(*a, **k):
        seen.append(a)
        return await real(*a, **k)
    monkeypatch.setattr(C, "ready", spy)
    return seen


def test_off_never_looks_at_the_cold_store(monkeypatch, cold_serving):
    cold_serving(COLD_ROWS, upto="20231016", max_dd="20231015")
    monkeypatch.setenv("OPM_COLD", "0")
    seen = _spy_ready(monkeypatch)
    p, _ = _firm(monkeypatch)
    assert seen == []
    d = p["data"]
    assert [s["asof"] for s in d["series"]] == ["20231020", "20231027", "20261009", "20261016"]
    assert d["weekly_from"] == "20231016" and "cold_until" not in d and "sources" not in d
    assert any("주간 시세는 최근 3년까지 제공" in w for w in p["warnings"]) and NOTE not in json.dumps(p, ensure_ascii=False)


def test_cold_joins_three_years_back_without_duplicates(monkeypatch, cold_serving):
    cold_serving(COLD_ROWS, upto="20231016", max_dd="20231015")
    p, calls = _firm(monkeypatch)
    d = p["data"]
    dates = [s["asof"] for s in d["series"]]
    assert dates == ["20151230", "20160108", "20190405", "20210625", "20210702", "20221229", "20231006", "20231013",
                     "20231020", "20231027", "20261009", "20261016"]
    assert len(dates) == len(set(dates)) and dates == sorted(dates)
    assert d["from"] == "20151230" and d["weekly_from"] == "20151230" and d["cold_until"] == "20231016"
    assert d["sources"] == {"cold": 8, "db_weekly": 4, "db_daily_tail": 0} and d["points_weekly"] == 12
    # DB 질의는 종전과 같다(하한 = DB 주간 구간 시작) — 콜드가 DB 질의를 바꾸지 않는다
    assert [c for c in calls if "FROM krx_weekly" in c[0]][0][1] == ("005930", "20231016")
    assert not any("주간 시세는 최근 3년까지 제공" in w for w in p["warnings"]) and NOTE not in p["warnings"]
    assert "보관본(DB 밖 서빙본)" in d["method"] and "2015-12-30" in d["method"]
    # 값은 서빙본 그대로(종가·시총 = 종가 × 주식수)
    first = d["series"][0]
    assert (first["close_krw"], first["mktcap_krw"], first["list_shrs"]) == (25_000, 25_000 * SH, SH)
    md = _render_firm(p)
    assert "주간 곡선 12개(20151230~ — 20231016 이전은 보관본)" in md and "최근 3년" not in md


def test_db_rows_before_the_seam_are_not_used(monkeypatch, cold_serving):
    """롤링 전처럼 DB 하한이 S 보다 이를 때(floor < S) — DB 의 [floor, S) 행은 버리고 서빙본 행을 쓴다(같은 날짜 한 번)."""
    cold_serving(COLD_ROWS, upto="20231016", max_dd="20231015")
    weekly = [_w("20231013", 68_000)] + DB_WEEKLY
    p, _ = _firm(monkeypatch, weekly=weekly, bounds=("20261016", "20231009"))
    dates = [s["asof"] for s in p["data"]["series"]]
    assert dates.count("20231013") == 1 and dates.count("20221229") == 1
    assert p["data"]["sources"]["cold"] == 8


def test_seam_moves_to_day_after_coverage_when_it_lags_the_cutoff(monkeypatch, cold_serving):
    """coverage 가 B 보다 늦게 따라올 때(C+1 < B) — S = C+1, 그 사이 주는 DB 에서."""
    cold_serving(COLD_ROWS, upto="20231009", max_dd="20231008")
    weekly = [_w("20231013", 68_000)] + DB_WEEKLY
    p, _ = _firm(monkeypatch, weekly=weekly, bounds=("20261023", "20231009"))   # B = 20231023
    d = p["data"]
    assert d["cold_until"] == "20231009"
    dates = [s["asof"] for s in d["series"]]
    assert dates[:8] == ["20151230", "20160108", "20190405", "20210625", "20210702", "20221229", "20231006", "20231013"]
    assert len(dates) == len(set(dates)) and d["sources"] == {"cold": 7, "db_weekly": 5, "db_daily_tail": 0}


def test_since_after_the_seam_skips_the_cold_store(monkeypatch, cold_serving):
    cold_serving(COLD_ROWS, upto="20231016", max_dd="20231015")
    seen = _spy_ready(monkeypatch)
    on, _ = _firm(monkeypatch, since="20240101")
    assert seen == []
    monkeypatch.setenv("OPM_COLD", "0")
    off, _ = _firm(monkeypatch, since="20240101")
    assert on == off


def test_since_before_the_seam_starts_at_since(monkeypatch, cold_serving):
    cold_serving(COLD_ROWS, upto="20231016", max_dd="20231015")
    p, _ = _firm(monkeypatch, since="2019-01-01")
    dates = [s["asof"] for s in p["data"]["series"]]
    assert dates[0] == "20190405" and "20160108" not in dates
    assert p["data"]["weekly_from"] == "20151230" and p["data"]["sources"]["cold"] == 6
    assert not any("요청한 since" in w for w in p["warnings"])


@pytest.mark.parametrize("breakage", ["no_url", "bad_manifest", "sha"])
def test_degraded_is_the_old_response_plus_one_line(monkeypatch, cold_serving, breakage):
    info = cold_serving(COLD_ROWS, upto="20231016", max_dd="20231015", tamper_sha=breakage == "sha")
    if breakage == "no_url":
        monkeypatch.setenv("OPM_COLD_MANIFEST_URL", "file:///nonexistent/serve/manifest.json")
    elif breakage == "bad_manifest":
        info["manifest_path"].write_text("{not json")
    deg, _ = _firm(monkeypatch)
    monkeypatch.setenv("OPM_COLD", "0")
    off, _ = _firm(monkeypatch)
    s_deg = json.dumps(deg, ensure_ascii=False)
    assert s_deg.count(NOTE) == 1
    assert s_deg.replace(" " + NOTE, "") == json.dumps(off, ensure_ascii=False)
    assert "cold_until" not in deg["data"]


def test_seam_gap_degrades(monkeypatch, cold_serving):
    """서빙본 끝(20231015)과 DB 주간 구간 시작(20231023) 사이에 평일이 빈다 — 빈 주가 섞인 곡선을 내지 않는다."""
    cold_serving(COLD_ROWS, upto="20231016", max_dd="20231015")
    p, _ = _firm(monkeypatch, weekly=[r for r in DB_WEEKLY if r[0] >= "20231027"], bounds=("20261023", "20231023"))
    assert "cold_until" not in p["data"] and any(NOTE in w for w in p["warnings"])
    assert C.stats()["degraded"].get("seam_gap") == 1


def test_daily_tail_still_joins_after_the_cold_part(monkeypatch, cold_serving):
    cold_serving(COLD_ROWS, upto="20231016", max_dd="20231015")
    p, _ = _firm(monkeypatch, tail=[_w("20261019", 74_000)])
    d = p["data"]
    assert d["series"][-1]["asof"] == "20261019" and d["sources"] == {"cold": 8, "db_weekly": 4, "db_daily_tail": 1}
    # 같은 주의 더 늦은 날이면 마지막 주간 점을 갈아 끼운다 — DB 주간 점이 하나 준다
    p2, _ = _firm(monkeypatch, tail=[_w("20261015", 74_000)],
                  weekly=[r for r in DB_WEEKLY if r[0] != "20261016"] + [_w("20261013", 73_500)])
    assert [s["asof"] for s in p2["data"]["series"]][-2:] == ["20261009", "20261015"]
    assert p2["data"]["sources"] == {"cold": 8, "db_weekly": 3, "db_daily_tail": 1}


def test_adjustment_events_span_the_whole_curve(monkeypatch, cold_serving):
    cold_serving(COLD_ROWS, upto="20231016", max_dd="20231015")
    p, calls = _firm(monkeypatch, adj=[("20180504", 0.02)])
    assert [c for c in calls if "krx_adj_events" in c[0]][0][1] == ("005930", "20151230", "20261016")
    assert p["data"]["adj_events"] == [{"event_dd": "20180504", "adj_factor": 0.02}]


def test_only_cold_rows_for_a_stock_delisted_before_the_seam(monkeypatch, cold_serving):
    cold_serving([_c("20171229", 50_000, ticker="005930"), _c("20180105", 51_000, ticker="005930")],
                 upto="20231016", max_dd="20231015")
    p, _ = _firm(monkeypatch, weekly=[])
    assert p["status"] == "ok" and [s["asof"] for s in p["data"]["series"]] == ["20171229", "20180105"]
    assert p["data"]["market"] == "KS"
    md = _render_firm(p)
    assert "주간 곡선 2개(20171229~ — 20231016 이전은 보관본)" in md


def test_stock_listed_after_the_seam_does_not_claim_archive_rows(monkeypatch, cold_serving):
    cold_serving([r for r in COLD_ROWS if r[0] != "005930"], upto="20231016", max_dd="20231015")   # 이 종목 행이 서빙본에 없다
    p, _ = _firm(monkeypatch, weekly=[_w("20251128", 300_000), _w("20251205", 310_000)])
    d = p["data"]
    assert d["sources"]["cold"] == 0 and d["from"] == "20251128"
    assert "보관본(DB 밖 서빙본)" not in d["method"]                 # 이은 것이 없으면 출처 문장도 없다
    md = _render_firm(p)
    assert "주간 곡선 2개(20251128~)" in md and "이전은 보관본" not in md
    assert not any("최근 3년까지" in w for w in p["warnings"])         # 보관본을 봤고 그 전 시세가 없다 — 3년 안내는 필요 없다


def test_no_rows_anywhere_is_no_data_without_the_three_year_note(monkeypatch, cold_serving):
    cold_serving([_c("20171229", 50_000), _c("20180105", 51_000)], upto="20231016", max_dd="20231015")
    empty, _ = _firm(monkeypatch, weekly=[], since="20200101")
    assert empty["status"] == "no_data" and not any("최근 3년까지" in w for w in empty["warnings"])


# ═════════════════════════════════════════════════════════════════════════════
# 2. price_multiple_data firm_history · firm as_of(과거) · 연말 밴드
# ═════════════════════════════════════════════════════════════════════════════
FIN = {fy: (1.0e13, 1.0e14) for fy in range(2018, 2026)}           # FY2018~ — 곡선은 2019-04 부터


def _pmd(monkeypatch, weekly=DB_WEEKLY, tail=(), bounds=("20261016", "20231016")):
    async def fake_resolve(q):
        return {"corp_name": "삼성전자", "stock_code": "005930", "corp_code": "00126380"}, None

    async def fake_fin_y(isu, cur="KRW", fye=12):
        return dict(FIN)

    async def fake_fin_q(isu, cur="KRW", fye=12):
        return {}

    async def fake_bounds():
        return bounds

    def fake_rows(sql, params=()):
        s = " ".join(sql.split())
        if "FROM dart_fundamentals" in s:
            return [("KS", "C26", "KRW", None, "ok")]
        if s.startswith("SELECT DISTINCT ON (substring(price_dd,1,4))"):     # 연말 밴드 — DB 연말 행(전체 기간)
            return [("2019", "20191230", 55_800 * SH), ("2022", "20221229", 55_300 * SH)]
        if s.startswith("SELECT price_dd, mktcap, 'w' FROM krx_weekly"):
            return ([(r[0], r[3], "w") for r in weekly if r[0] >= params[1]]
                    + [(r[0], r[3], "d") for r in tail])
        if "FROM krx_stock_flags" in s:
            return []
        raise AssertionError(f"모르는 SQL: {s}")

    monkeypatch.setattr(S, "_resolve_listed", fake_resolve)
    monkeypatch.setattr(S, "_firm_fin_by_fy", fake_fin_y)
    monkeypatch.setattr(S, "_firm_fin_by_q", fake_fin_q)
    monkeypatch.setattr(S, "_weekly_bounds", fake_bounds)
    monkeypatch.setattr(S, "_pg_rows", fake_rows)


def test_firm_history_off_is_unchanged(monkeypatch, cold_serving):
    cold_serving(COLD_ROWS, upto="20231016", max_dd="20231015")
    monkeypatch.setenv("OPM_COLD", "0")
    seen = _spy_ready(monkeypatch)
    _pmd(monkeypatch)
    p = asyncio.run(S.build_firm_history_payload("삼성전자", format="json"))
    assert seen == []
    d = p["data"]
    assert [s["asof"] for s in d["series"]] == ["20231020", "20231027", "20261009", "20261016"]
    assert d["weekly_from"] == "20231016" and "cold_until" not in d
    assert "차트=최근 3년 주간 곡선" in d["method"] and "주간 시세는 최근 3년까지 제공" in p["warnings"][1]


def test_firm_history_curve_starts_at_the_first_point_in_time_financials(monkeypatch, cold_serving):
    cold_serving(COLD_ROWS, upto="20231016", max_dd="20231015")
    _pmd(monkeypatch)
    p = asyncio.run(S.build_firm_history_payload("삼성전자", format="json"))
    d = p["data"]
    dates = [s["asof"] for s in d["series"]]
    # FY2018 사업보고서가 공시된 2019-04 부터 점이 있다(그 전 서빙본 행은 재무가 없어 점을 만들지 않는다)
    assert dates == ["20190405", "20210625", "20210702", "20221229", "20231006", "20231013",
                     "20231020", "20231027", "20261009", "20261016"]
    assert d["weekly_from"] == "20151230" and d["cold_until"] == "20231016"
    assert "cold_until(20231016) 이전은 보관본" in d["method"] and "차트=최근 3년" not in d["method"]
    assert "보관본(DB 밖 서빙본)에서 이었습니다" in p["warnings"][1] and NOTE not in p["warnings"]
    # 연말 밴드(history 의 연말 항목)는 DB 연말 행 그대로 — 콜드가 바꾸지 않는다
    monkeypatch.setenv("OPM_COLD", "0")
    off = asyncio.run(S.build_firm_history_payload("삼성전자", format="json"))
    band = [h for h in d["history"] if h["source"].startswith("연말")]
    assert band == [h for h in off["data"]["history"] if h["source"].startswith("연말")] and len(band) == 2
    assert d["history"] == off["data"]["history"]           # 「주간」 최근 16주 항목도 같다
    md = _render_firm_history(p)
    assert "차트용 주간 곡선(20151230~ — 20231016 이전은 보관본) 10개" in md


def test_firm_history_degraded_keeps_the_old_answer(monkeypatch, cold_serving):
    cold_serving(COLD_ROWS, upto="20231016", max_dd="20231015")
    monkeypatch.setenv("OPM_COLD_MANIFEST_URL", "file:///nonexistent/serve/manifest.json")
    _pmd(monkeypatch)
    deg = asyncio.run(S.build_firm_history_payload("삼성전자", format="json"))
    monkeypatch.setenv("OPM_COLD", "0")
    off = asyncio.run(S.build_firm_history_payload("삼성전자", format="json"))
    assert deg["warnings"][-1] == NOTE and deg["warnings"][:-1] == off["warnings"]
    assert deg["data"] == off["data"]


def test_firm_at_answers_in_the_past_with_the_cold_curve(monkeypatch, cold_serving):
    cold_serving(COLD_ROWS, upto="20231016", max_dd="20231015")
    _pmd(monkeypatch)
    p = asyncio.run(S.build_firm_at_payload("삼성전자", "20210630"))
    assert p["status"] == "ok" and p["data"]["as_of"] == "20210625" and p["data"]["pit_fy"] == 2020
    assert p["data"]["cap_krw"] == 81_000 * SH
    # 주간 시세 시작 전 — 범위 밖(3년 안내가 아니라 시작일)
    q = asyncio.run(S.build_firm_at_payload("삼성전자", "20140101"))
    assert q["status"] == "no_data" and "주간 시세 시작(2015-12-30) 이전" in q["warnings"][0]
    assert not any("최근 3년" in w for w in q["warnings"])
    # 시세는 있지만 재무 공시 전 — 첫 점 안내 + 까닭
    r = asyncio.run(S.build_firm_at_payload("삼성전자", "20170101"))
    assert r["status"] == "no_data" and "곡선의 첫 점은 20190405" in r["warnings"][0] and "재무" in r["warnings"][0]


def test_firm_at_degraded_says_it_is_temporary(monkeypatch, cold_serving):
    cold_serving(COLD_ROWS, upto="20231016", max_dd="20231015")
    monkeypatch.setenv("OPM_COLD_MANIFEST_URL", "file:///nonexistent/serve/manifest.json")
    _pmd(monkeypatch)
    q = asyncio.run(S.build_firm_at_payload("삼성전자", "20210630"))
    assert q["status"] == "no_data" and "주간 시세는 최근 3년까지 제공" in q["warnings"][0] and NOTE in q["warnings"]
