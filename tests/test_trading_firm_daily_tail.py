"""trading_data(scope=firm) — 시계열 끝점을 일별 시세 저장분의 최신 한 줄로 (261018, Supabase 재설계 5-1 읽는 쪽).

주간 시세 저장분은 지금 매 거래일 이번 주 행을 지우고 다시 쓰지만, 다음 단계에서 **닫힌 주만** 쓰게 된다. 그때도 최신값이
낡지 않도록 끝점을 일별 저장분(`price_latest`)에서 읽는다. 지키는 것:
  ① 두 날짜가 같으면(지금) 응답이 한 글자도 바뀌지 않는다
  ② 일별 최신이 더 늦은 ISO 주면(몇 주 뒤든) 한 점 추가, 같은 ISO 주의 더 늦은 날이면 마지막 점 교체
  ③ 주간 행이 없고 일별만 있으면(이번 주 신규상장) 그 한 점으로 답한다
  ④ 끝점 읽기가 실패하면 조용히 주간만 — 요청 범위(since) 밖의 끝점은 버린다
  ⑤ 진짜 Postgres 에서: 지금 세계(주간에 이번 주 행 있음)와 다음 세계(닫힌 주만)의 응답이 같다

앞부분은 network 0 · DB 0(가짜 `_pg_rows`), 뒷부분(⑤)은 로컬 Docker `postgres:17`(`tests/conftest.py` 의 `pg17`)이 필요하다 — 없으면 그 시험만 건너뛴다.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from open_proxy_mcp.services import trading as T
from open_proxy_mcp.tools.trading import _render_firm

SH = 5_969_782_550


def _w(dd, close, shrs=SH, market="KS"):
    """주간·일별 질의가 돌려주는 한 줄 — (price_dd, market, close, mktcap, list_shrs)."""
    return (dd, market, close, close * shrs, shrs)


#: 2026-09-25(금) · 10-02(금) · 10-09(금) — 닫힌 주 셋. 10-12(월)~10-16(금)이 이번 ISO 주(42주).
WEEKLY = [_w("20260925", 70_000), _w("20261002", 71_000), _w("20261009", 72_000)]

#: 끝점을 잇기 전(종전 코드)의 출처 설명 — 끝점을 잇지 않은 응답은 이 문구가 한 글자도 바뀌지 않아야 한다.
OLD_METHOD = ("KRX 정보데이터시스템 → 주간 시세 저장분(주 마지막 거래일 보존, 매일 갱신). "
              "mktcap=상장주식수×종가(우선주는 별도 종목). "
              "OHLC·거래량·거래대금은 저장하지 않습니다 — `scope=quote` 참조.")


def _route(monkeypatch, weekly, tail, adj=(), barrier=None):
    """SQL 로 갈라 답하는 가짜 `_pg_rows`. 모르는 SQL 이면 실패한다. 주간 질의는 진짜처럼 `price_dd>=하한` 으로 거른다."""
    calls = []

    async def fake_resolve(q):
        return {"corp_name": "삼성전자", "stock_code": "005930"}, None

    async def fake_bounds():
        return "20261009", "20231009"

    def fake_rows(sql, params=()):
        s = " ".join(sql.split())
        calls.append((s, params))
        if "FROM krx_weekly" in s:
            if barrier is not None:
                barrier.wait()
            return None if weekly is None else [r for r in weekly if r[0] >= params[1]]
        if "FROM price_latest" in s:
            if barrier is not None:
                barrier.wait()
            return None if tail is None else list(tail)
        if "FROM krx_adj_events" in s:
            return [(d, f) for d, f in adj if params[1] <= d <= params[2]]
        raise AssertionError(f"모르는 SQL: {s}")

    monkeypatch.setattr(T, "_resolve_listed", fake_resolve)
    monkeypatch.setattr(T, "_weekly_bounds", fake_bounds)
    monkeypatch.setattr(T, "_pg_rows", fake_rows)
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")      # 주간 읽기 실패가 KRX 라이브로 새지 않게(db_error 로)
    return calls


def _payload(monkeypatch, weekly, tail, **kw):
    adj = kw.pop("adj", ())
    calls = _route(monkeypatch, weekly, tail, adj=adj)
    return asyncio.run(T.build_firm_series_payload("삼성전자", **kw)), calls


# ── ① 지금 — 두 날짜가 같으면 응답 불변 ─────────────────────────────────────
def test_same_day_tail_changes_nothing(monkeypatch):
    base, _ = _payload(monkeypatch, WEEKLY, [], format="json")
    same, calls = _payload(monkeypatch, WEEKLY, [WEEKLY[-1]], format="json")
    assert same == base                                                 # payload 전체(값·키·경고·method)가 같다
    assert _render_firm(same) == _render_firm(base)
    assert same["data"]["as_of"] == "20261009" and same["data"]["points_weekly"] == 3
    assert same["data"]["method"] == OLD_METHOD                         # 출처 설명도 종전 그대로(끝점을 잇지 않았으므로)
    assert any("FROM price_latest WHERE ticker=%s" in s and p == ("005930",) for s, p in calls)


def test_tail_failure_or_absence_falls_back_to_weekly_silently(monkeypatch):
    base, _ = _payload(monkeypatch, WEEKLY, [])
    failed, _ = _payload(monkeypatch, WEEKLY, None)                     # 끝점 읽기 실패(None)
    assert failed == base and failed["status"] == "ok"


def test_weekly_failure_keeps_the_old_error_path_even_with_a_tail(monkeypatch):
    p, _ = _payload(monkeypatch, None, [_w("20261013", 73_000)])
    assert p["status"] == "db_error"                                    # 새 폴백을 만들지 않는다


# ── ② 일별 최신이 더 늦을 때 ────────────────────────────────────────────────
def test_tail_in_the_next_iso_week_is_appended(monkeypatch):
    tail = _w("20261013", 73_000)
    p, calls = _payload(monkeypatch, WEEKLY, [tail], adj=[("20261012", 0.5)])
    d = p["data"]
    assert [s["asof"] for s in d["series"]] == ["20260925", "20261002", "20261009", "20261013"]
    assert d["as_of"] == "20261013" and d["points_weekly"] == 4 and d["points"] == 4
    assert d["latest"] == {"asof": "20261013", "close_krw": 73_000, "mktcap_krw": 73_000 * SH, "list_shrs": SH}
    assert d["from"] == "20260925" and d["market"] == "KS"
    # 조정 이벤트 구간은 새 끝점까지 — 주간 마지막 점(10-09) 이후의 이벤트도 경고에 잡힌다
    adj_call = next(pr for s, pr in calls if "FROM krx_adj_events" in s)
    assert adj_call == ("005930", "20260925", "20261013")
    assert d["adj_events"] == [{"event_dd": "20261012", "adj_factor": 0.5}]
    assert any("기준가 조정 1회" in w for w in p["warnings"])
    assert d["method"] == T._FIRM_METHOD_TAIL                           # 끝점을 이었을 때만 출처 설명이 그 사실을 적는다


def test_later_day_in_the_same_iso_week_replaces_the_last_point(monkeypatch):
    weekly = WEEKLY[:-1] + [_w("20261008", 72_000)]                     # 목요일까지만 주간에 있다
    p, _ = _payload(monkeypatch, weekly, [_w("20261009", 72_500)])     # 같은 41주의 금요일
    d = p["data"]
    assert [s["asof"] for s in d["series"]] == ["20260925", "20261002", "20261009"]
    assert d["points_weekly"] == 3 and d["latest"]["close_krw"] == 72_500


def test_monthly_resolution_takes_the_tail_as_the_month_end(monkeypatch):
    p, _ = _payload(monkeypatch, WEEKLY, [_w("20261013", 73_000)], freq="monthly")
    d = p["data"]
    assert [s["asof"] for s in d["series"]] == ["20260925", "20261013"]
    assert d["points_weekly"] == 4 and d["points"] == 2


# ── ③ 주간 행이 없고 일별만 ────────────────────────────────────────────────
def test_new_listing_this_week_is_a_single_point(monkeypatch):
    p, _ = _payload(monkeypatch, [], [_w("20261014", 15_000, shrs=1_000_000, market="KQ")])
    assert p["status"] == "ok"
    d = p["data"]
    assert d["as_of"] == d["from"] == "20261014" and d["points_weekly"] == 1 and d["market"] == "KQ"
    assert d["series"] == [{"asof": "20261014", "close_krw": 15_000, "mktcap_krw": 15_000_000_000, "list_shrs": 1_000_000}]
    assert "15,000원" in _render_firm(p)


def test_no_weekly_and_no_tail_is_still_no_data(monkeypatch):
    p, _ = _payload(monkeypatch, [], [])
    assert p["status"] == "no_data"


# ── ④ 요청 범위 밖의 끝점은 버린다 ─────────────────────────────────────────
def test_tail_before_since_is_ignored(monkeypatch):
    """이 스코프의 범위 인자는 시작(since)뿐이다 — 끝점이 그보다 이르면 쓰지 않는다."""
    p, _ = _payload(monkeypatch, WEEKLY, [_w("20261013", 73_000)], since="20261014")
    assert p["status"] == "no_data"                                     # 주간도 하한에 걸러져 비고, 끝점(10-13)도 범위 밖
    p, _ = _payload(monkeypatch, WEEKLY, [_w("20261013", 73_000)], since="2026-10-13")
    assert p["status"] == "ok" and [s["asof"] for s in p["data"]["series"]] == ["20261013"]


def test_tail_older_than_the_last_weekly_point_is_ignored(monkeypatch):
    base, _ = _payload(monkeypatch, WEEKLY, [])
    stale, _ = _payload(monkeypatch, WEEKLY, [_w("20261002", 1)])
    assert stale == base


def test_helper_contract():
    lo = "20231009"
    assert T._with_daily_tail(None, [_w("20261013", 1)], lo) is None
    assert T._with_daily_tail([], None, lo) == []
    assert T._with_daily_tail(WEEKLY, [], lo) is WEEKLY
    assert T._with_daily_tail([], [_w("20261013", 1)], lo) == [_w("20261013", 1)]
    assert T._with_daily_tail(WEEKLY, [_w("20261009", 1)], lo) is WEEKLY       # 같은 날 — 주간 값을 그대로 둔다
    # 다음 해 첫 주(ISO 연도가 바뀌는 경계) — 2026-12-31(목, 53주) 다음 2027-01-04(월, 1주)는 다른 주
    yr = [_w("20261231", 1)]
    assert T._with_daily_tail(yr, [_w("20270104", 2)], lo) == [_w("20261231", 1), _w("20270104", 2)]
    # 2027-01-01(금)은 2026-12-31 과 같은 ISO 주(2026년 53주) — 교체
    assert T._with_daily_tail(yr, [_w("20270101", 2)], lo) == [_w("20270101", 2)]
    # 바로 다음 주가 아니어도(주간 쓰기가 몇 주 밀린 경우) 더 늦은 주면 뒤에 붙인다 — 「다음 주」만이 아니다
    assert T._with_daily_tail(WEEKLY, [_w("20261027", 3)], lo) == [*WEEKLY, _w("20261027", 3)]


def test_weekly_and_tail_reads_overlap_in_one_round_trip(monkeypatch):
    """두 읽기는 서로 입력이 필요 없다 — 차례로 기다리면 왕복이 하나 는다. 둘이 동시에 떠 있어야 장벽을 넘는다."""
    from open_proxy_mcp.services import price_multiple_data as S
    monkeypatch.setattr(S, "_PG_FANOUT", 3)                 # 롤백 손잡이(`OPM_PG_FANOUT=1`)가 셸에 있어도 기본 동작을 본다
    barrier = threading.Barrier(2, timeout=10)
    _route(monkeypatch, WEEKLY, [_w("20261013", 73_000)], barrier=barrier)
    p = asyncio.run(T.build_firm_series_payload("삼성전자"))
    assert p["status"] == "ok" and p["data"]["as_of"] == "20261013"


# ── universe 문구 — 기준일은 보관된 최신 거래일 ─────────────────────────────
def _fake_universe(monkeypatch, **kw):
    from open_proxy_mcp.services import universe as uni
    ul = uni.UniverseList(spec="kospi:1", label="KOSPI 시총상위 1", resolved=True, as_of="20261016",
                          rows=[{"rank": 1, "ticker": "005930", "name": "삼성전자", "market": "KOSPI",
                                 "mktcap_krw": 80_000 * SH, "close_krw": 80_000}])
    for k, v in kw.items():
        setattr(ul, k, v)

    async def fake_list(raw):
        return ul
    monkeypatch.setattr(uni, "list_universe", fake_list)


def test_universe_warning_says_latest_stored_trading_day(monkeypatch):
    _fake_universe(monkeypatch)
    p = asyncio.run(T.build_universe_payload("코스피 시총 상위 1"))
    assert p["status"] == "ok"
    w = p["warnings"][0]
    assert "보관된 최신 거래일 기준(20261016)" in w and "오늘 장중 시총이 아닙니다" in w
    assert "주 마지막 거래일" not in w and "주간 스냅샷" not in w


def test_universe_db_missing_message_is_neutral(monkeypatch):
    _fake_universe(monkeypatch, db_ok=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    p = asyncio.run(T.build_universe_payload("코스피 시총 상위 1"))
    assert p["status"] == "db_unconfigured"
    joined = " ".join(p["warnings"])
    assert "시세 저장분에서만" in joined and "주간 시세 저장분" not in joined


def test_firm_method_says_daily_end_point_only_when_it_was_used(monkeypatch):
    """출처 설명은 사실과 맞아야 한다 — 끝점을 이었을 때만 「마지막 점은 일별 시세 저장분」, 아니면 종전 문구 그대로."""
    assert T._FIRM_METHOD_WEEKLY == OLD_METHOD
    assert "마지막 점은 주간 저장분보다 늦은 일별 시세 저장분의 그 종목 최신 거래일" in T._FIRM_METHOD_TAIL
    for tail, want in (([], OLD_METHOD), (None, OLD_METHOD), ([WEEKLY[-1]], OLD_METHOD),     # 없음·실패·같은 날
                       ([_w("20261002", 1)], OLD_METHOD),                                    # 더 이른 날(버림)
                       ([_w("20261013", 73_000)], T._FIRM_METHOD_TAIL)):                     # 이었음
        p, _ = _payload(monkeypatch, WEEKLY, tail)
        assert p["data"]["method"] == want, tail
        assert _render_firm(p).count(f"> {want}") == 1                                     # md 인용 줄도 같은 문구
    p, _ = _payload(monkeypatch, [], [_w("20261014", 15_000)])                              # 신규상장 한 점도 이은 것이다
    assert p["data"]["method"] == T._FIRM_METHOD_TAIL


def test_weekly_and_tail_queries_select_the_same_columns():
    """두 결과를 한 목록으로 잇고 위치로 읽는다 — 한쪽 SELECT 칸 목록만 바뀌면 칸이 조용히 어긋난다."""
    import re

    def cols(sql):
        return re.match(r"SELECT (.+?) FROM ", sql).group(1)
    assert cols(T._FIRM_WEEKLY_SQL) == cols(T._FIRM_TAIL_SQL) == T._FIRM_COLS
    assert "FROM krx_weekly" in T._FIRM_WEEKLY_SQL and "FROM price_latest" in T._FIRM_TAIL_SQL


# ═══════════════════════════════════════════════════════════════════════════════
# ⑤ 진짜 Postgres 17 — 지금 세계와 다음 세계(주간에 닫힌 주만)의 응답이 같다
# ═══════════════════════════════════════════════════════════════════════════════
ADJ_DDL = """CREATE TABLE IF NOT EXISTS krx_adj_events (
  ticker text NOT NULL, event_dd text NOT NULL, market text, adj_factor double precision,
  PRIMARY KEY (ticker, event_dd));"""

#: 6 거래일: 40주 목·금, 41주 목·금, 42주(이번 주) 월·화. 247540 은 41주 목요일까지만 거래하고 상장폐지(주 중간).
DAYS = ("20261001", "20261002", "20261008", "20261009", "20261012", "20261013")
_TAIL = T._with_daily_tail


@pytest.fixture
def firm_pg(pg17, monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    from test_krx_daily_pg import KRX_WEEKLY_DDL, _ddl

    _ddl().apply(pg17)
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(KRX_WEEKLY_DDL)
        c.execute(ADJ_DDL)

    def pg_rows(sql, params=()):
        try:
            with psycopg.connect(pg17, autocommit=True) as c:
                return c.execute(sql, params).fetchall()
        except Exception:
            return None
    # 서버의 모든 읽기(`_pg_rows` → `open_proxy_mcp.db.pg_rows`)를 이 컨테이너로 — 공용 풀은 만들지 않는다
    monkeypatch.setattr("open_proxy_mcp.db.pg_rows", pg_rows)
    monkeypatch.setenv("DATABASE_URL", pg17)          # 쓰는 쪽(`_krx_db_upsert`)이 이 주소로 쓴다

    async def fake_resolve(q):
        code = {"삼성전자": "005930", "폐지사": "247540", "신규상장": "0088M0"}[q]
        return {"corp_name": q, "stock_code": code}, None
    monkeypatch.setattr(T, "_resolve_listed", fake_resolve)
    return pg17, psycopg


def _write_current_world():
    """쓰는 쪽을 지금 그대로 돌린다 — 주간은 같은 ISO 주의 옛 날짜를 지우고, 일별은 6일을 다 갖는다."""
    from open_proxy_mcp.services import price_multiple_data as pmd
    from test_krx_daily_pg import _row
    for i, dd in enumerate(DAYS):
        kospi = [_row("005930", 70_000 + 500 * i, shrs=SH)]
        kosdaq = [_row("247540", 250_000 - 1_000 * i)] if dd <= "20261008" else []
        assert pmd._krx_db_upsert(dd, kospi, kosdaq) == (True, True)


def _firm(name="삼성전자"):
    return asyncio.run(T.build_firm_series_payload(name, format="json"))


def _comparable(p):
    """보존 하한(`weekly_from`)과 그 안내, 출처 설명(`method`)만 뺀다 — 하한은 주간 표의 최신 주를 따르는 것이 맞고(표를 자르는 쪽과
    같은 정의라 이번 작업에서 바꾸지 않는다), 출처 설명은 끝점을 실제로 이었는지를 사실대로 적으므로 두 세계에서 다르다(따로 단언한다)."""
    d = {k: v for k, v in p["data"].items() if k not in ("weekly_from", "method")}
    return {**p, "data": d, "warnings": [w for w in p["warnings"] if "주간 시세는 최근 3년까지" not in w]}


def test_pg_current_world_and_next_world_answer_the_same(firm_pg, monkeypatch):
    url, psycopg = firm_pg
    _write_current_world()
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("INSERT INTO krx_adj_events (ticker, event_dd, market, adj_factor) VALUES (%s, %s, %s, %s)",
                  ("005930", "20261013", "KS", 0.5))
        weekly = [r[0] for r in c.execute("SELECT DISTINCT price_dd FROM krx_weekly ORDER BY 1").fetchall()]
    assert weekly == ["20261002", "20261009", "20261013"]                # 지금: 닫힌 주 마지막 날 + 이번 주 최신 하루

    now = _firm()
    assert now["status"] == "ok" and now["data"]["as_of"] == "20261013"
    assert [s["asof"] for s in now["data"]["series"]] == ["20261002", "20261009", "20261013"]
    assert now["data"]["latest"] == {"asof": "20261013", "close_krw": 72_500, "mktcap_krw": 72_500 * SH, "list_shrs": SH}

    # 끝점을 빼고 읽어도 지금은 같은 응답이다 — 주간 표에도 이번 주 행이 있으므로(옮긴 읽기가 지금 값을 바꾸지 않는다)
    monkeypatch.setattr(T, "_with_daily_tail", lambda rows, tail, lo: rows)
    assert _firm() == now
    monkeypatch.setattr(T, "_with_daily_tail", _TAIL)

    # 다음 세계: 쓰는 쪽이 닫힌 주만 남긴다(이번 주 행 없음). 일별은 그대로.
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("DELETE FROM krx_weekly WHERE price_dd >= %s", ("20261012",))
    nxt = _firm()
    assert _comparable(nxt) == _comparable(now)                         # 값·키·경고·조정 이벤트까지 같다
    assert now["data"]["method"] == T._FIRM_METHOD_WEEKLY               # 지금: 주간 마지막 점 = 일별 최신 → 종전 문구
    assert nxt["data"]["method"] == T._FIRM_METHOD_TAIL                 # 다음 세계: 끝점을 일별에서 이었다고 적는다
    assert nxt["data"]["adj_events"] == [{"event_dd": "20261013", "adj_factor": 0.5}]

    # 대조: 끝점이 없으면 다음 세계의 응답은 지난주 금요일에 멈춘다(이 시험이 헛돌지 않는다)
    monkeypatch.setattr(T, "_with_daily_tail", lambda rows, tail, lo: rows)
    stale = _firm()
    assert stale["data"]["as_of"] == "20261009" and stale["data"]["adj_events"] == []


def test_pg_mid_week_delisting_gets_its_last_trading_day_back(firm_pg):
    """쓰는 쪽이 같은 ISO 주의 옛 날짜를 지우므로, 주 중간(목)에 폐지된 종목은 주간에서 그 목요일이 사라진다(금요일 행에 없으니).
    일별 보관 창 안에서는 끝점이 그 마지막 거래일을 되살린다 — 운영 실측(261018) 2종목이 이 모양이다."""
    url, psycopg = firm_pg
    _write_current_world()
    with psycopg.connect(url, autocommit=True) as c:
        weekly = [r[0] for r in c.execute("SELECT price_dd FROM krx_weekly WHERE ticker=%s ORDER BY 1", ("247540",)).fetchall()]
        latest = c.execute("SELECT price_dd, close FROM price_latest WHERE ticker=%s", ("247540",)).fetchone()
    assert weekly == ["20261002"] and latest == ("20261008", 248_000)
    d = _firm("폐지사")["data"]
    assert [s["asof"] for s in d["series"]] == ["20261002", "20261008"]
    assert d["latest"]["close_krw"] == 248_000 and d["market"] == "KQ"
    assert d["method"] == T._FIRM_METHOD_TAIL


def test_pg_new_listing_with_no_weekly_row_yet(firm_pg):
    import json

    from open_proxy_mcp import krx_daily
    from test_krx_daily_pg import _row
    url, psycopg = firm_pg
    _write_current_world()
    with psycopg.connect(url) as c:                                     # 이번 주 화요일에 상장 — 주간에는 아직 없다
        assert krx_daily.write_daily(c, "20261013", [_row("005930", 72_500, shrs=SH)],
                                     [_row("0088M0", 12_000, shrs=2_000_000)])[0]
        c.commit()
    p = _firm("신규상장")
    json.dumps(p, ensure_ascii=False)                                   # 뷰의 곱셈(시총)이 Decimal 로 새지 않는다
    assert p["status"] == "ok"
    assert p["data"]["series"] == [{"asof": "20261013", "close_krw": 12_000, "mktcap_krw": 24_000_000_000,
                                    "list_shrs": 2_000_000}]
