# -*- coding: utf-8 -*-
"""배수 도구 읽는 쪽 — 재설계로 줄었던 것 되살리기 (261019, Supabase 재설계 6단계 읽는 쪽 · 설계 v03 감사 2·3 · 시험 서버 대조 · #110·#114 가 남긴 것).

  ① 곡선 끝점(감사 3): krx_weekly 는 닫힌 주만 담는다 — firm_history·firm_at 곡선의 끝점을 일별 저장분의 그 종목 최신 한 줄(`price_latest`)로 잇는다
     (같은 주면 갈아 끼우고 늦은 주면 붙인다 — 시세 도구 `trading._with_daily_tail` 과 같은 규칙).
  ② 곡선 하한(감사 2): 하한은 DB 에 실제로 남은 주간 구간의 시작 — 최신 주가 닫힐 때마다 한 주씩 올라가는 3년 하한이 DB 에 남은 주간 행을 숨기지 않는다.
  ③ 업종 비교의 소속 섹터 과거 시계열(`sector_history`)은 표의 scheme 값으로 읽는다(업종분류 축에서 0행이던 것 — 시험 서버 대조에서 발견).
  ④ 업종 비교에서 기업 행이 없을 때 실제 까닭을 말한다(종전 「종목 스냅샷 없음(비상장·미수집)」 — 없앤 표를 가리켰다).
  ⑤ not_found 안내의 종류주 문장은 지금 동작대로(#114 부터 종류주 코드를 발행 회사로 받는다).
network 0 · DB 0 (전부 monkeypatch).
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest

from open_proxy_mcp.services import price_multiple_data as S
from open_proxy_mcp.services.sector_class import DB_SCHEME, MAJOR, MID

TODAY = date(2026, 10, 21)            # 수요일 — 이번 주(10/19~)는 아직 닫히지 않았다
CAP = 150.0e12
FIN_Y = [(2024, 19.8e12, 54.0e12, None, None)]
FUND = (20.0e12, 60.0e12)
FIN_Q = [(2025, 2, 9.0e12, 58.0e12), (2026, 2, 12.0e12, 66.0e12)]


def _weeks(n, start):
    d0 = date(int(start[:4]), int(start[4:6]), int(start[6:]))
    return [((d0 + timedelta(days=7 * i)).strftime("%Y%m%d"), CAP + i * 1e12) for i in range(n)]


# ═══════════════════════════════════════════════════════════════════════════════════════════
# ① 곡선 끝점 = 일별 최신 한 줄
# ═══════════════════════════════════════════════════════════════════════════════════════════
def test_join_daily_tail_follows_the_trading_series_rule():
    px = [("20261009", 1.0), ("20261016", 2.0)]
    assert S._join_daily_tail(px, [("20261020", 3.0)], "20231016") == [*px, ("20261020", 3.0)], "늦은 주 → 붙인다"
    assert S._join_daily_tail([("20261013", 1.0)], [("20261014", 3.0)], "20231016") == [("20261014", 3.0)], "같은 주 → 갈아 끼운다"
    assert S._join_daily_tail(px, [("20261016", 9.0)], "20231016") is px, "같은 날 → 그대로(닫힌 주의 행)"
    assert S._join_daily_tail(px, [("20261008", 9.0)], "20231016") is px, "더 이른 날(거래정지) → 그대로"
    assert S._join_daily_tail([], [("20261020", 3.0)], "20231016") == [("20261020", 3.0)], "이번 주 신규 상장 — 그 한 점"
    assert S._join_daily_tail([], [("20231013", 3.0)], "20231016") == [], "하한보다 이른 꼬리는 버린다"
    assert S._join_daily_tail(px, None, "20231016") is px and S._join_daily_tail(px, [], "20231016") is px
    # 연도 경계 주는 둘로 나뉜다(주를 닫는 쪽과 같은 정의) — 12/30 다음 1/2 는 다른 주라 붙인다
    assert S._join_daily_tail([("20251230", 1.0)], [("20260102", 2.0)], "20231016") == [("20251230", 1.0), ("20260102", 2.0)]


def _fh_rows(tail, *, latest="20261016", weekly=None, seen=None):
    if weekly is None:                                                      # 2026-03-20 ~ 2026-10-09 금요일들 + 최신 주(10/16)
        weekly = _weeks(30, "20260320") + [(latest, CAP + 99e12)]

    def rows(sql, params=()):
        s = " ".join(sql.split())
        if s.startswith("SELECT market, induty, currency, acc_mt, fetched FROM dart_fundamentals"):
            return [("KS", "26110", "KRW", None, "ok")]
        if s.startswith("SELECT MAX(price_dd) FROM krx_weekly"):
            return [(latest,)]
        if s.startswith("SELECT 'below'"):
            return [("first", weekly[0][0], 2600), ("ref", latest, 2700)]
        if s.startswith("SELECT price_dd, mktcap, 'w' FROM krx_weekly"):
            if seen is not None:
                seen.append(params)
            return [(d, c, "w") for d, c in weekly if d >= params[1]] + [(d, c, "d") for d, c in tail]
        if s == "SELECT ni_fy, eq_fy FROM dart_fundamentals WHERE ticker=%s":
            return [FUND]
        if "FROM dart_finstat_y" in s:
            return list(FIN_Y)
        if "FROM dart_finstat_q" in s:
            return list(FIN_Q)
        if "DISTINCT ON" in s or "FROM krx_stock_flags" in s:
            return []
        raise AssertionError("모르는 SQL: " + s)
    return rows


def _patch(mp, rows):
    async def resolve(q):
        return {"corp_name": "SK하이닉스", "stock_code": "000660", "corp_code": "00164779"}, None
    mp.setattr(S, "_pg_rows", rows)
    mp.setattr(S, "_resolve_listed", resolve)
    mp.setattr(S, "today_kst", lambda: TODAY)


def test_firm_history_curve_ends_at_the_latest_daily_row_mid_week(monkeypatch):
    """화~금: 주간 표의 최신 주는 지난 금요일(10/16)이고 일별 최신은 화요일(10/20). 곡선 끝·「최신 X 기준」·「주간」 항목·월말 요약이 10/20 이다
    (종전 쓰기는 이번 주 행을 매일 갈아 끼워 최신 거래일이 곡선 끝이었다). 같은 도구 scope=firm 과 기준일이 맞는다."""
    _patch(monkeypatch, _fh_rows([("20261020", CAP + 120e12)]))
    p = asyncio.run(S.build_firm_history_payload("000660", format="json"))
    d = p["data"]
    assert d["series"][-1]["asof"] == "20261020" and d["series"][-1]["cap_krw"] == round(CAP + 120e12)
    assert d["series"][-2]["asof"] == "20261016", "닫힌 주의 점은 그대로 — 새 주의 점을 붙였다"
    assert p["warnings"][0].startswith("최신 20261020 기준.")
    assert d["series_daily_end"] == "20261020" and "일별 시세 저장분" in d["method"]
    wk = [h for h in d["history"] if h["source"] == "주간"]
    assert len(wk) == 16 and wk[-1]["asof"] == "20261020", "「주간」 항목 = 이번 주 + 닫힌 15주(종전 스냅샷 표처럼)"
    assert d["summary"][-1]["asof"] == "20261020"


def test_firm_history_same_week_tail_replaces_and_closed_week_is_unchanged(monkeypatch):
    # 이번 주 화요일까지 거래한 뒤 거래정지 — 주간 표에는 아직 이번 주 행이 없고 일별 최신은 화요일(새 주라 붙인다)
    # 주간 표에 이미 같은 날이 있으면(금요일이 닫힌 뒤 — 토·일) 아무것도 바꾸지 않는다: 칸·문구가 종전 그대로
    _patch(monkeypatch, _fh_rows([("20261016", CAP + 99e12)]))
    p = asyncio.run(S.build_firm_history_payload("000660", format="json"))
    assert p["data"]["series"][-1]["asof"] == "20261016" and "series_daily_end" not in p["data"]
    assert "일별 시세 저장분" not in p["data"]["method"]
    # 꼬리가 마지막 주간 점과 같은 주(그 주가 덜 닫힌 종목 — 주간 행이 그 주 이른 날)면 갈아 끼운다
    weekly = _weeks(30, "20260320")[:-1] + [("20261014", CAP)]          # … 10/02 · 10/14(그 주 이른 날)
    _patch(monkeypatch, _fh_rows([("20261016", CAP + 5e12)], latest="20261014", weekly=weekly))
    s = asyncio.run(S.build_firm_history_payload("000660", format="json"))["data"]["series"]
    assert [x["asof"] for x in s[-2:]] == ["20261002", "20261016"]


def test_firm_at_this_week_answers_with_the_daily_end_point(monkeypatch):
    _patch(monkeypatch, _fh_rows([("20261020", CAP + 120e12)]))
    p = asyncio.run(S.build_firm_at_payload("000660", "20261021"))
    assert p["data"]["as_of"] == "20261020" and p["data"]["cap_krw"] == round(CAP + 120e12)
    assert p["warnings"][0].startswith("일별 최신 거래일 20261020(이번 주 — 주간 곡선의 끝점)")
    q = asyncio.run(S.build_firm_at_payload("000660", "20261018"))
    assert q["data"]["as_of"] == "20261016" and q["warnings"][0].startswith("주간 스냅샷(주 마지막 거래일 20261016)")


# ═══════════════════════════════════════════════════════════════════════════════════════════
# ② 곡선 하한 = DB 에 실제로 남은 주간 구간의 시작
# ═══════════════════════════════════════════════════════════════════════════════════════════
def test_weekly_run_start_extends_only_over_dense_weeks_below_the_cut():
    # 지금(10-18) 운영 모양: 하한 아래엔 연말 행(20221229 — 전 종목)과 상장폐지 종목의 마지막 행(몇 개씩)뿐 → 하한 그대로
    now = [("below", "20221229", 2558), ("below", "20230714", 8), ("below", "20231013", 1),
           ("first", "20231020", 2634), ("ref", "20261016", 2762)]
    assert S._weekly_run_start(now, "20231016") == "20231016"
    # 한 주 뒤(최신 20261023, 3년 하한 20231023) — 롤링 정리를 켜지 않아 20231020 주가 DB 에 그대로 있다 → 그 주 첫날까지
    nxt = [("below", "20221229", 2558), ("below", "20231013", 1), ("below", "20231020", 2634),
           ("first", "20231027", 2634), ("ref", "20261023", 2762)]
    assert S._weekly_run_start(nxt, "20231023") == "20231016"
    # 1년 뒤에도 같은 시작(주마다 이어진다) — 연말 행에서 멈춘다
    year = [("below", "20221229", 2558)] + [("below", d, 2650) for d, _ in _weeks(52, "20231020")] + \
           [("first", "20241025", 2700), ("ref", "20271022", 2800)]
    assert S._weekly_run_start(year, "20241022") == "20231016"
    # 설·추석으로 한 주가 통째로 비어도(14일) 이어진다 · 상장폐지 종목의 마지막 행(몇 개)은 촘촘한 주가 아니다
    gap = [("below", "20230922", 2600), ("below", "20231006", 2600), ("below", "20231013", 3),
           ("first", "20231020", 2634), ("ref", "20261016", 2762)]
    assert S._weekly_run_start(gap, "20231016") == "20230918"
    # 하한이 1월 초면 직전 해 마지막 주(전 종목의 그해 마지막 행 — 그 주의 마지막 거래일이기도 하다)까지 이어진다
    jan = [("below", "20221229", 2558), ("below", "20231228", 2640), ("first", "20240105", 2645), ("ref", "20270108", 2800)]
    assert S._weekly_run_start(jan, "20240103") == "20231225"
    # 하한 이후 첫 날짜가 상장폐지 종목 한 줄뿐이어도(그 주 화요일) 기준은 최신 주의 행 수다
    odd = [("below", "20231020", 2634), ("first", "20231024", 1), ("ref", "20261023", 2762)]
    assert S._weekly_run_start(odd, "20231023") == "20231016"
    assert S._weekly_run_start([], "20231016") == "20231016"


def test_weekly_run_start_needs_a_first_row_and_a_reference():
    assert S._weekly_run_start([("below", "20231020", 2634)], "20231023") == "20231023"
    assert S._weekly_run_start([("below", "20231020", 2634), ("first", "20231027", 2634)], "20231023") == "20231023"


def test_weekly_bounds_floor_is_the_stored_weekly_run_start(monkeypatch):
    calls = []

    def rows(sql, params=()):
        s = " ".join(sql.split())
        calls.append(s[:30])
        if s.startswith("SELECT MAX(price_dd) FROM krx_weekly"):
            return [("20261023",)]
        if s.startswith("SELECT 'below'"):
            assert params == ("20231023", "20231023")
            return [("below", "20221229", 2558), ("below", "20231020", 2634), ("first", "20231027", 2634), ("ref", "20261023", 2762)]
        raise AssertionError(s)
    monkeypatch.setattr(S, "_pg_rows", rows)
    assert asyncio.run(S._weekly_bounds()) == ("20261023", "20231016")
    assert asyncio.run(S._weekly_bounds()) == ("20261023", "20231016")
    assert sum(c.startswith("SELECT 'below'") for c in calls) == 1, "1시간 캐시 — 최신 주가 같으면 다시 읽지 않는다"
    # 장애(None)면 자르는 쪽의 하한 — 캐시하지 않는다
    S._WEEKLY_START_CACHE.clear()
    monkeypatch.setattr(S, "_pg_rows", lambda sql, params=(): [("20261023",)] if sql.startswith("SELECT MAX(price_dd)") else None)
    assert asyncio.run(S._weekly_bounds()) == ("20261023", "20231023") and not S._WEEKLY_START_CACHE


def test_firm_history_keeps_the_stored_week_below_the_moving_cutoff(monkeypatch):
    """10-24 부터: 최신 주 20261023 → 3년 하한 20231023. DB 에 남은 20231020 주가 곡선 첫 점·weekly_from 에 그대로 있다."""
    weekly = _weeks(158, "20231020")                      # 20231020 ~ 20261023
    seen: list = []
    rows = _fh_rows([], latest="20261023", weekly=weekly, seen=seen)

    def with_start(sql, params=()):
        if " ".join(sql.split()).startswith("SELECT 'below'"):
            return [("below", "20221229", 2558), ("below", "20231020", 2634), ("first", "20231027", 2634), ("ref", "20261023", 2762)]
        return rows(sql, params)
    _patch(monkeypatch, with_start)
    d = asyncio.run(S.build_firm_history_payload("000660", format="json"))["data"]
    assert d["weekly_from"] == "20231016" and seen[-1] == ("000660", "20231016", "000660"), "곡선은 DB 에 남은 20231020 주부터 읽는다"


# ═══════════════════════════════════════════════════════════════════════════════════════════
# ③④ 업종 비교 — sector_history 의 scheme 값 · 기업 행이 없는 까닭
# ═══════════════════════════════════════════════════════════════════════════════════════════
AS_OF = "20261016"
DB_VALUES = {"ksic", *DB_SCHEME.values()}            # 운영 표에 있는 scheme 값(10-18 실측: ksic·market·wics_industry·wics_sector)
HIST = [("20251226", 12.0, 11.0, 1.5, 1.4, 800e12, 1e12, 1e12), (AS_OF, 14.0, 13.0, 1.9, 1.8, 900e12, 1e12, 1e12)]


def _sector_db(seen, *, wise=True):
    def rows(sql, params=()):
        s = " ".join(sql.split())
        if s.startswith("SELECT snap_dd, market, sector, label, n, cap, per_ttm"):
            sec = "261" if params[0] == "ksic" else ("G45" if params[0] == "wics_sector" else "G4530")
            return [(AS_OF, "KS", sec, "반도체", 30, 900e12, 13.456, 1.876, 14.0, 1.9, 1e12, 1e12)]
        if "FROM wise_sector WHERE ticker=%s" in s:
            if wise is None:
                return None
            return [("G45" if "sector_code" in s else "G4530", "반도체", AS_OF)] if wise else []
        if "FROM opm_val_market WHERE market=%s AND sector=%s AND scheme=%s" in s:
            seen.append(params[2])
            return list(HIST) if params[2] in DB_VALUES else []          # 표에 실제로 있는 값일 때만 행
        raise AssertionError("가짜 DB 가 모르는 SQL: " + s)
    return rows


def _sector(mp, scheme, firm_at, **kw):
    seen: list = []

    async def resolve(q):
        return {"corp_name": "삼성전자", "stock_code": "005930", "corp_code": "00126380"}, None

    async def no_fwd(*a, **k):
        return {}, {}, None

    async def no_div(*a, **k):
        return {}, {}
    mp.setattr(S, "_pg_rows", _sector_db(seen, **kw))
    mp.setattr(S, "_resolve_listed", resolve)
    mp.setattr(S, "_firm_val_at", firm_at)
    mp.setattr(S, "_fwd_val_map", no_fwd)
    mp.setattr(S, "_div_yield_map", no_div)
    return asyncio.run(S.build_sector_val_payload(company="삼성전자", scheme=scheme, format="json")), seen


async def _firm_ok(isu, dd):
    return ("261", "KS", 8.4, 2.2, {})


@pytest.mark.parametrize("scheme", ["ksic", MAJOR, MID, "wics_sector", "wics_industry"])
def test_sector_history_reads_the_table_scheme_value(monkeypatch, scheme):
    """공개 인자는 「대분류」·「중분류」인데 `opm_val_market.scheme` 칸은 옛 값(wics_sector·wics_industry)이다 — 260921 이름 전환 때 이 질의만 변환이
    빠져 업종분류 축에서 시계열이 통째로 빠졌다(운영 005930 중분류 — 표에는 88행). 가짜 DB 는 표에 있는 값일 때만 행을 돌려준다."""
    p, seen = _sector(monkeypatch, scheme, _firm_ok)
    assert p["status"] == "ok"
    assert seen and seen[0] in DB_VALUES, f"표에 없는 scheme 값으로 읽었다: {seen}"
    hist = p["data"]["company"].get("sector_history")
    assert hist and [h["snap_dd"] for h in hist] == ["20251226", AS_OF]


def _missing(why, market=None):
    async def f(isu, dd):
        return (None, market, None, None, {"missing": why})
    return f


async def _db_down(isu, dd):
    return None


@pytest.mark.parametrize("firm_at,kw,scheme,expect", [
    (_missing("nofin"), {}, MID, "'삼성전자' 기업 행 없음 — 재무 요약이 아직 없다(신규 상장 등 — 수집 전). 기업 배수·비교를 내지 않는다."),
    (_missing("nocorp"), {}, "ksic", "'삼성전자' 기업 행 없음 — 재무 요약을 DART 회사와 잇지 못했다(회사 고유번호 매칭 없음). 기업 배수·비교를 내지 않는다."),
    (_missing("outlier_scale"), {}, MID, "'삼성전자' 기업 행 없음 — 재무 요약이 규모 이상치로 걸러졌다. 기업 배수·비교를 내지 않는다."),
    (_missing("price", "KS"), {}, MID, "'삼성전자' 기업 행 없음 — 그 주에 시세가 없다(상장 전·거래정지·상장폐지) — 기준일 20261016. 기업 배수·비교를 내지 않는다."),
    (_firm_ok, {"wise": False}, MID, "'삼성전자' 기업 행 없음 — 업종분류 스냅샷 어디에도 없는 종목이다(분류 공급처 구성종목 밖). KSIC 축(scheme='ksic')으로는 비교할 수 있다."),
    (_firm_ok, {"wise": None}, MAJOR, "'삼성전자' 기업 행을 읽지 못했다(DB 읽기 실패) — 잠시 뒤 다시 조회한다."),
    (_db_down, {}, MID, "'삼성전자' 기업 행을 읽지 못했다(DB 읽기 실패) — 잠시 뒤 다시 조회한다."),
])
def test_missing_firm_row_says_the_real_reason(monkeypatch, firm_at, kw, scheme, expect):
    p, _ = _sector(monkeypatch, scheme, firm_at, **kw)
    assert p["status"] == "ok" and p["data"]["company"] is None
    assert p["warnings"][-1] == expect
    assert not any("스냅샷 없음(비상장·미수집)" in w for w in p["warnings"])


def test_firm_val_at_returns_the_reason_instead_of_none(monkeypatch):
    def rows_for(fund_row, cap_row):
        def rows(sql, params=()):
            s = " ".join(sql.split())
            if s.startswith("SELECT market, induty, currency, fetched, acc_mt FROM dart_fundamentals"):
                return fund_row
            if "AS src" in s:
                return cap_row
            raise AssertionError(s)
        return rows
    monkeypatch.setattr(S, "_pg_rows", rows_for([], [(CAP, "KS", 1)]))
    assert asyncio.run(S._firm_val_at("0010S0", AS_OF)) == (None, None, None, None, {"missing": "nofin"})
    monkeypatch.setattr(S, "_pg_rows", rows_for([("KQ", None, "KRW", "nocorp", None)], [(CAP, "KQ", 1)]))
    assert asyncio.run(S._firm_val_at("0001A0", AS_OF))[4] == {"missing": "nocorp"}
    monkeypatch.setattr(S, "_pg_rows", rows_for([("KQ", "26110", "KRW", "ok", None)], []))
    assert asyncio.run(S._firm_val_at("486510", "20260821")) == (None, None, None, None, {"missing": "price"})
    monkeypatch.setattr(S, "_pg_rows", lambda sql, params=(): None)
    assert asyncio.run(S._firm_val_at("005930", AS_OF)) is None, "DB 장애는 None(행 없음 — 경고만 「읽지 못했다」)"


# ═══════════════════════════════════════════════════════════════════════════════════════════
# ⑤ not_found 안내 — 종류주 코드는 받는다(#114)
# ═══════════════════════════════════════════════════════════════════════════════════════════
def test_not_found_hint_matches_the_share_class_behaviour(monkeypatch):
    from open_proxy_mcp.services import forward_estimates as F

    class _Client:
        async def lookup_corp_code(self, q):
            return None

    async def unresolved(q):
        return None, None
    monkeypatch.setattr(S, "get_dart_client", lambda: _Client())
    monkeypatch.setattr(S, "_resolve_listed", unresolved)
    p = asyncio.run(S._build_valuation_payload_impl("없는회사이름"))
    assert p["status"] == "not_found"
    assert S.PREF_CODE_HINT in p["warnings"] and not any("보통주 종목코드로 조회한다" in w for w in p["warnings"])
    q = asyncio.run(F.build_forward_estimates_payload("없는회사이름"))     # 같은 해석기(`price_multiple_data._resolve_listed`)를 쓴다
    assert q["status"] == "not_found" and not any("보통주 종목코드로 조회한다" in w for w in q["warnings"])
    assert any("종류주는 그 종목코드(예: 005935)로 조회해도 된다" in w for w in q["warnings"])
