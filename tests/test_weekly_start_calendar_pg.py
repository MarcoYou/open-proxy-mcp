"""주간 곡선 시작 날짜를 날짜 달력에서 (261021, 감사 B3) — Postgres 17 에서 증명한다.

배경: `krx_weekly` 를 뷰로 바꾼 뒤(v03 C4) `_WEEKLY_START_SQL` 의 `below`(하한 아래 날짜별 행 수)는 33.9만 행 압축 표 `krx_wk` 를 날짜로 묶어
첫 호출이 88~600 ms 걸렸다(운영 실측, `trading_data firm` 첫 호출 1.36초 중 대부분). 같은 답이 작은 달력(`krx_wk_day` 306행 +
`krx_day` 의 주 닫는 날 54행)에 이미 있다 — 날짜별 행 수 `n` 이 그 날 뷰의 행 수와 같다.

여기서 지키는 것
  ① 달력의 (날짜, n) = 뷰의 (날짜, count(*)) — 상장폐지 종목의 부분 날짜(1행짜리)·연말 행·콜드로 옮겨진 날 포함.
  ② 뷰 DB 에서 새 달력 질의(`_WEEKLY_START_VIEW_SQL`)와 종전 질의(`_WEEKLY_START_SQL`)가 **어느 하한에서도** 같은 (종류, 날짜, 행 수) 줄을 낸다
     (하한 아래 주 · 하한 뒤 첫 날 · 기준 주) — 그래서 곡선 시작도 같다. 운영 읽기 전용 대조(하한 654개, 하한 아래 189주, 불일치 0)와 같은 검사의 작은 판이다.
  ③ 표 DB(교체 전·되돌린 뒤)는 종전 질의를 그대로 쓴다 — 표 세계에는 주 달력이 없을 수 있다. 표 DB 와 뷰 DB 의 곡선 시작·월말 원천이 같다.
  ④ 뷰 DB 에서 실제로 **실행되는** 읽기가 달력(`krx_wk_day`·`krx_day`)뿐이다(`EXPLAIN ANALYZE` — 압축 표·일별 사실값·번호표를 훑지 않는다).
     종전 질의는 압축 표를 훑는다(이 시험이 변이로 잡는다).
  ⑤ 같은 꼴의 배치 읽기 — 월말 원천(`month_end_prices`)의 하한·날짜 목록, `krx_cap_agg`·`krx_listing` 의 최솟값·최댓값 — 도 달력에서 읽고 표 DB 와 같은 답을 낸다.

Docker 가 없으면 건너뛴다(`tests/conftest.py` 의 `pg17`).
"""
from __future__ import annotations

import inspect
import json
from datetime import date, timedelta

import pytest

psycopg = pytest.importorskip("psycopg")

# 두 세계(A = 끝까지 표, B = 교체 뒤 뷰) 픽스처와 기록 도우미를 뷰 시험에서 그대로 쓴다.
from test_krx_weekly_view_pg import _apply, _kind, _mark_cold, _q, _run, two  # noqa: E402,F401

from open_proxy_mcp import krx_daily  # noqa: E402
from open_proxy_mcp import krx_retention as R  # noqa: E402
from open_proxy_mcp.services import price_multiple_data as pmd  # noqa: E402


@pytest.fixture
def worlds(two, monkeypatch):
    """A = 표, B = 뷰. 교체 전 2주 → 교체 → 이어서 기록 → 콜드 표시 후 보관 창 정리가 주 닫는 날을 압축 표로 옮긴 뒤까지."""
    a, b = two
    _run(two, "20261005", "20261016", monkeypatch)
    _apply(b, "switch")
    assert (_kind(a), _kind(b)) == ("r", "v")
    _run(two, "20261019", "20261030", monkeypatch)
    _mark_cold(two, "20261016")
    _run(two, "20261102", "20261113", monkeypatch)           # 정리가 콜드 날을 krx_wk 로 옮기고 지운다
    assert _q(b, "SELECT count(*) FROM krx_wk_day")[0][0] > 7, "압축 표에도 주가 쌓였다"
    return a, b


def _rows_on(url):
    """`_weekly_start_rows` 의 `run(sql, params)` — 그 DB 에서 읽는다."""
    def run(sql, params=()):
        with psycopg.connect(url) as c:
            return c.execute(sql, params).fetchall()
    return run


def _cuts():
    """하한 후보 — 시드(2025-08)부터 기록 끝(2026-11)을 지나 먼 미래·먼 과거까지, 평일 전부."""
    d, e = date(2025, 8, 1), date(2026, 12, 31)
    while d <= e:
        if d.isoweekday() < 6:
            yield d.strftime("%Y%m%d")
        d += timedelta(days=1)
    yield from ("20151230", "20991231")


# ── ① 달력 = 뷰 ───────────────────────────────────────────────────────────────────

def test_calendar_dates_and_counts_equal_the_view(worlds):
    a, b = worlds
    cal = _q(b, f"SELECT dd, n FROM ({R.WEEKLY_DAYS_SQL}) c ORDER BY 1")
    view = _q(b, "SELECT price_dd, count(*) FROM krx_weekly GROUP BY 1 ORDER BY 1")
    assert cal == view and len(cal) >= 9
    assert (cal[0][0], cal[0][1]) == ("20250829", 6), "상장폐지 종목이 낀 옛 주 — 6행"
    assert ("20250905", 6) in cal and len({d for d, _ in cal}) == len(cal), "한 날은 한 곳에서만"
    assert any(n == 5 for _, n in cal) and any(n == 6 for _, n in cal)
    # 압축 표로 옮겨진 날과 아직 일별 창에 있는 주 닫는 날이 모두 들어 있다
    moved = {r[0] for r in _q(b, "SELECT dd FROM krx_wk_day")}
    live = {r[0] for r in _q(b, "SELECT dd FROM krx_day WHERE wk_close AND n > 0")}
    assert moved & {d for d, _ in cal} and live & {d for d, _ in cal}
    # 가장 이른·늦은 날 한 줄도 같다 — 표 DB 는 종전 질의(표 위 min/max), 뷰 DB 는 달력
    assert _q(b, R.WEEKLY_RANGE_SQL) == _q(b, "SELECT min(price_dd), max(price_dd) FROM krx_weekly") \
        == _q(a, "SELECT min(price_dd), max(price_dd) FROM krx_weekly")


def test_a_calendar_day_without_rows_is_not_a_view_day(worlds):
    """주 달력에 날짜만 먼저 적히고 행이 아직 없는 때(`MOVE_WK_DAY_SQL` 이 `n = 0` 으로 넣은 뒤 `MOVE_WK_N_SQL` 이 채우기 전)에는 뷰에 그 날 행이 없다 —
    달력에서도 그 날을 내지 않는다(내면 하한 아래 주를 한 날 더 센다)."""
    a, b = worlds
    from test_krx_weekly_view_pg import _x
    _x(b, "INSERT INTO krx_wk_day (wk_dd, dd, n) VALUES ('2020-01-03', '20200103', 0)")
    assert _q(b, "SELECT count(*) FROM krx_weekly WHERE price_dd = '20200103'") == [(0,)]
    assert _q(b, f"SELECT count(*) FROM ({R.WEEKLY_DAYS_SQL}) c WHERE dd = '20200103'") == [(0,)]
    assert _q(b, f"SELECT dd, n FROM ({R.WEEKLY_DAYS_SQL}) c ORDER BY 1") == \
        _q(b, "SELECT price_dd, count(*) FROM krx_weekly GROUP BY 1 ORDER BY 1")
    assert _q(b, R.WEEKLY_RANGE_SQL) == _q(b, "SELECT min(price_dd), max(price_dd) FROM krx_weekly")


def test_a_day_moved_to_the_compressed_table_but_not_yet_deleted_counts_once(worlds):
    """보관 창 정리는 주 닫는 날을 압축 표로 **먼저 옮기고** 일별에서 지운다 — 옮긴 뒤 아직 안 지운 날은 두 달력(`krx_wk_day`·`krx_day`)에 함께 있다.
    뷰는 그 날을 한 곳에서만 내므로(뒤 갈래의 NOT EXISTS) 달력도 한 번만 세어야 한다(두 번 세면 하한 아래 촘촘한 주 판정이 어긋난다)."""
    a, b = worlds
    from test_krx_weekly_view_pg import _x
    day = _q(b, "SELECT trd_dd FROM krx_day WHERE wk_close AND n > 0 ORDER BY trd_dd DESC LIMIT 1")[0][0]
    _x(b, krx_daily.MOVE_WK_DAY_SQL, ([day],))
    _x(b, krx_daily.MOVE_WK_SQL, ([day],))
    _x(b, krx_daily.MOVE_WK_N_SQL, ([day],))
    assert _q(b, "SELECT count(*) FROM krx_day WHERE trd_dd = %s AND wk_close", (day,)) == [(1,)], "일별에도 아직 있다"
    assert _q(b, "SELECT count(*) FROM krx_wk_day WHERE wk_dd = %s", (day,)) == [(1,)], "압축 표 달력에도 있다"
    cal = _q(b, f"SELECT dd, n FROM ({R.WEEKLY_DAYS_SQL}) c ORDER BY 1")
    assert cal == _q(b, "SELECT price_dd, count(*) FROM krx_weekly GROUP BY 1 ORDER BY 1")
    assert len({d for d, _ in cal}) == len(cal), "한 날은 한 번"


# ── ② 새 질의 = 종전 질의 (뷰 DB 위에서, 모든 하한) ─────────────────────────────────────

def test_view_query_equals_the_old_query_for_every_cut(worlds):
    a, b = worlds
    run = _rows_on(b)
    n = 0
    for cut in _cuts():
        p = pmd._weekly_start_params(cut)
        old, new = run(pmd._WEEKLY_START_SQL, p), run(pmd._WEEKLY_START_VIEW_SQL, p)
        assert sorted(old) == sorted(new), cut
        assert pmd._weekly_run_start(old, cut) == pmd._weekly_run_start(new, cut), cut
        n += 1
    assert n > 300
    # 하한 아래 주 · 첫 날 · 기준 주가 모두 실제로 나오는 하한이 있다(빈 비교가 아니다)
    rows = run(pmd._WEEKLY_START_VIEW_SQL, pmd._weekly_start_params("20261001"))
    kinds = {k for k, _, _ in rows}
    assert kinds == {"below", "first", "ref"} and sum(1 for k, _, _ in rows if k == "below") >= 4


def test_dense_weeks_below_the_cut_still_extend_the_start(worlds):
    """종전 규칙(하한 아래에 촘촘한 주가 이어져 있으면 거기까지 내린다)이 달력 질의에서도 그대로다 — 주 5행 중 절반 이상인 날이 이어질 때."""
    a, b = worlds
    cut = "20261104"                                    # 이 아래 10-23·10-30 주 닫는 날(5행)이 있다
    new = _rows_on(b)(pmd._WEEKLY_START_VIEW_SQL, pmd._weekly_start_params(cut))
    old = _rows_on(b)(pmd._WEEKLY_START_SQL, pmd._weekly_start_params(cut))
    start = pmd._weekly_run_start(new, cut)
    assert start == pmd._weekly_run_start(old, cut) and start < cut


# ── ③ 표 DB 는 종전 질의, 두 세계의 곡선 시작이 같다 ──────────────────────────────────────

def test_table_world_keeps_the_old_query_and_both_worlds_agree(worlds, monkeypatch):
    a, b = worlds
    seen: list[str] = []

    def spy(url):
        inner = _rows_on(url)

        def run(sql, params=()):
            seen.append(sql)
            return inner(sql, params)
        return run

    for cut in ("20261001", "20261104", "20250901", "20991231"):
        ra = pmd._weekly_start_rows(spy(a), cut)
        rb = pmd._weekly_start_rows(spy(b), cut)
        assert sorted(ra) == sorted(rb), cut
        assert pmd._weekly_run_start(ra, cut) == pmd._weekly_run_start(rb, cut), cut
    assert pmd._WEEKLY_START_SQL in seen and pmd._WEEKLY_START_VIEW_SQL in seen
    # 서버 함수도 같은 답 — DB 읽기를 각 세계로 바꿔 끼운다
    for cut in ("20261001", "20261104"):
        out = []
        for url in (a, b):
            monkeypatch.setattr(pmd, "_pg_rows", _rows_on(url))
            out.append(pmd._krx_weekly_start(cut))
        assert out[0] == out[1], cut


def test_unreadable_kind_is_an_outage_not_the_old_query(monkeypatch):
    """모양을 못 읽으면(None) 장애다 — 종전 질의로 돌아가 엉뚱한 세계의 답을 내지 않는다(호출자가 3년 하한으로 대신한다)."""
    monkeypatch.setattr(pmd, "_pg_rows", lambda sql, params=(): None)
    assert pmd._krx_weekly_start("20261001") is None
    calls = []
    monkeypatch.setattr(pmd, "_pg_rows", lambda sql, params=(): calls.append(sql) or ([("v",)] if "relkind" in sql else None))
    assert pmd._krx_weekly_start("20261001") is None and calls[-1] == pmd._WEEKLY_START_VIEW_SQL


# ── ④ 뷰 DB 에서 실제로 읽는 표 ──────────────────────────────────────────────────────

def _executed_relations(url, sql, params):
    """EXPLAIN ANALYZE 에서 한 번이라도 실행된 노드의 표 이름 — 실행되지 않은 갈래(`Actual Loops` 0)는 뺀다."""
    with psycopg.connect(url) as c:
        raw = c.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + sql, params).fetchone()[0]
    plan = raw if isinstance(raw, list) else json.loads(raw)
    names: set[str] = set()

    def walk(node):
        if node.get("Relation Name") and node.get("Actual Loops", 0) > 0:
            names.add(node["Relation Name"])
        for ch in node.get("Plans", []):
            walk(ch)
    walk(plan[0]["Plan"])
    return names


def test_view_query_only_executes_reads_on_the_calendars(worlds):
    a, b = worlds
    p = pmd._weekly_start_params("20261104")
    new = _executed_relations(b, pmd._WEEKLY_START_VIEW_SQL, p)
    assert new and new <= {"krx_wk_day", "krx_day"}, f"달력만 읽어야 한다: {new}"
    old = _executed_relations(b, pmd._WEEKLY_START_SQL, p)
    assert {"krx_wk", "krx_px"} <= old, "종전 질의는 압축 표·일별 사실값을 훑는다 — 이 확인이 변이를 잡는다"
    # 모양 판별 읽기는 시스템 카탈로그 한 줄이다
    assert _q(b, krx_daily.WEEKLY_KIND_SQL) == [("v",)] and _q(a, krx_daily.WEEKLY_KIND_SQL) == [("r",)]


# ── ⑤ 같은 꼴의 배치 읽기 ───────────────────────────────────────────────────────────

def test_month_end_prices_reads_the_calendar_in_a_view_and_agrees_with_the_table(worlds):
    from scripts import month_end_prices as M
    a, b = worlds
    res = {}
    for name, url in (("a", a), ("b", b)):
        sql_seen: list[str] = []

        class Spy:
            def __init__(self, con):
                self.con = con

            def execute(self, sql, params=None):
                sql_seen.append(" ".join(sql.split()))
                return self.con.execute(sql, params) if params is not None else self.con.execute(sql)
        with psycopg.connect(url) as con:
            m = M.MonthEndPrices(Spy(con), "20250801")
            res[name] = (m.cutoff, m.floor, sorted(m.dense), m.plan.dates, m.plan.skipped, sql_seen)
    assert res["a"][:5] == res["b"][:5], "표 세계와 뷰 세계의 월말 원천(하한·촘촘한 구간·계획)이 같다"
    latest = " ".join(krx_daily.WEEKLY_LATEST_SQL.split())             # 기준 주 질의는 달력이 비었을 때만 도는 뷰 갈래를 글자로 품는다(실행되지 않는다 — ④)
    assert not any("FROM krx_weekly" in s.replace(latest, "") for s in res["b"][5]), "뷰 세계는 뷰를 훑지 않는다 — 달력만"
    assert any("FROM krx_weekly" in s for s in res["a"][5]), "표 세계는 종전 질의"


def test_batch_scripts_use_the_calendar_range_in_view_mode():
    """`krx_cap_agg`(원천 최솟값·최댓값)와 `krx_listing`(관측창 최솟값 대조)이 뷰 모드에서 뷰 위 MIN/MAX 대신 달력 범위를 쓴다."""
    from scripts import krx_cap_agg, krx_listing
    for mod, fn in ((krx_cap_agg, krx_cap_agg.main), (krx_listing, krx_listing.compare)):
        src = inspect.getsource(fn)
        assert "WEEKLY_RANGE_SQL if weekly_is_view(con)" in src, mod.__name__
        assert 'else "SELECT min(price_dd), max(price_dd) FROM krx_weekly"' in src, "표 모드는 종전 질의"
