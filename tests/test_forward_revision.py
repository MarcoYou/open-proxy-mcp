"""forward_estimates.compute_revision — 리비전(4주·12주 전 대비) 계산 회귀 (DB/네트워크 0콜).

지키는 것: ① 기준일 = 목표일 이전 가장 가까운 스냅샷 ② 이력이 짧으면 가장 오래된 것 + partial
③ %는 (지금−기준)/|기준| — 적자→흑자는 상향 ④ 그때 없던 기간은 absent ⑤ summary 는 FY·영업이익만
⑥ 기준 0/None 은 칸을 뺀다 ⑦ 6일 안쪽만 있으면 baselines 가 빈다.
"""
import datetime as dt

from open_proxy_mcp.services import forward_estimates as fe


def _row(as_of, period, ptype="FY", **m):
    d = {"as_of": as_of, "period": period, "period_type": ptype,
         "rev_krw": None, "op_krw": None, "ni_ctrl_krw": None, "eps_krw": None, "dps_krw": None}
    d.update(m)
    return d


def _weekly(n_weeks: int, start=dt.date(2026, 6, 6)):
    return [start + dt.timedelta(weeks=i) for i in range(n_weeks)]


def test_empty_history():
    r = fe.compute_revision([])
    assert r["rows"] == [] and r["baselines"] == {} and r["snapshots"] == 0


def test_baseline_is_nearest_snapshot_at_or_before_target():
    days = _weekly(14)                       # 13주 + 1 → 12w 기준이 잡힌다
    hist = [_row(d, "2026.12E", op_krw=100 + i) for i, d in enumerate(days)]
    r = fe.compute_revision(hist)
    latest = days[-1]
    assert r["as_of_latest"] == latest.isoformat()
    b4 = dt.date.fromisoformat(r["baselines"]["4w"]["as_of"])
    b12 = dt.date.fromisoformat(r["baselines"]["12w"]["as_of"])
    assert (latest - b4).days >= 28 and (latest - b4).days < 35      # 28일 이전 중 가장 가까운 것
    assert (latest - b12).days >= 91 and (latest - b12).days < 98
    assert r["baselines"]["4w"]["partial"] is False
    assert r["baselines"]["12w"]["partial"] is False


def test_short_history_uses_oldest_and_flags_partial():
    days = _weekly(2)                        # 7일치뿐
    hist = [_row(days[0], "2026.12E", op_krw=100), _row(days[1], "2026.12E", op_krw=110)]
    r = fe.compute_revision(hist)
    assert r["baselines"]["4w"] == {"as_of": days[0].isoformat(), "days": 7, "partial": True}
    assert r["baselines"]["12w"]["partial"] is True
    assert r["rows"][0]["vs"]["4w"]["op_krw_pct"] == 10.0


def test_too_close_history_has_no_baseline():
    d0 = dt.date(2026, 9, 1)
    hist = [_row(d0, "2026.12E", op_krw=100), _row(d0 + dt.timedelta(days=2), "2026.12E", op_krw=105)]
    r = fe.compute_revision(hist)
    assert r["baselines"] == {} and r["rows"] and r["rows"][0]["vs"] == {}


def test_pct_uses_abs_base_so_loss_to_profit_is_up():
    days = _weekly(6)
    hist = [_row(days[0], "2026.12E", op_krw=-100), _row(days[-1], "2026.12E", op_krw=50)]
    r = fe.compute_revision(hist)
    assert r["rows"][0]["vs"]["4w"]["op_krw_pct"] == 150.0
    assert r["summary"]["4w"] == {"up": 1, "down": 0, "flat": 0, "n": 1, "basis": "op_krw"}


def test_zero_or_missing_base_drops_the_cell():
    days = _weekly(6)
    hist = [_row(days[0], "2026.12E", op_krw=0, eps_krw=None, rev_krw=200),
            _row(days[-1], "2026.12E", op_krw=10, eps_krw=5, rev_krw=220)]
    cell = fe.compute_revision(hist)["rows"][0]["vs"]["4w"]
    assert "op_krw_pct" not in cell and "eps_krw_pct" not in cell
    assert cell["rev_krw_pct"] == 10.0


def test_period_absent_at_baseline_is_marked_not_zero():
    days = _weekly(6)
    hist = [_row(days[0], "2026.12E", op_krw=100),
            _row(days[-1], "2026.12E", op_krw=100), _row(days[-1], "2028.12E", op_krw=300)]
    rows = {r["period"]: r for r in fe.compute_revision(hist)["rows"]}
    assert rows["2028.12E"]["vs"]["4w"] == {"as_of": days[0].isoformat(), "absent": True}
    assert rows["2026.12E"]["vs"]["4w"]["op_krw_pct"] == 0.0


def test_summary_counts_only_fy_and_uses_flat_band():
    days = _weekly(6)
    hist = [_row(days[0], "2026.12E", op_krw=100), _row(days[-1], "2026.12E", op_krw=100.4),   # flat
            _row(days[0], "2027.12E", op_krw=100), _row(days[-1], "2027.12E", op_krw=90),      # down
            _row(days[0], "2026.09E", "Q", op_krw=10), _row(days[-1], "2026.09E", "Q", op_krw=20)]  # Q 제외
    s = fe.compute_revision(hist)["summary"]["4w"]
    assert s == {"up": 0, "down": 1, "flat": 1, "n": 2, "basis": "op_krw"}


def test_revision_is_a_known_bundle_and_in_all():
    want, bad = fe.parse_bundles("revision")
    assert want == {"revision", "core"} and bad == []   # revision 은 core 를 끌고 온다
    assert "revision" in fe.parse_bundles("all")[0]


def test_one_week_window_uses_previous_saturday():
    """1w(260916 추가): 목표일 7일 전 이전의 가장 가까운 스냅샷 = 직전 토요일. 6일 안쪽은 「전」이 아니다."""
    days = _weekly(4)
    hist = [_row(d, "2026.12E", op_krw=100 + 10 * i) for i, d in enumerate(days)]
    r = fe.compute_revision(hist)
    b1 = dt.date.fromisoformat(r["baselines"]["1w"]["as_of"])
    assert (days[-1] - b1).days == 7 and r["baselines"]["1w"]["partial"] is False
    assert r["rows"][0]["vs"]["1w"]["op_krw_pct"] == 8.33          # (130-120)/120
    assert list(r["baselines"]) == ["1w", "4w", "12w"]
    assert r["baselines"]["4w"]["partial"] and r["baselines"]["12w"]["partial"]   # 이력 3주 → 둘 다 가장 오래된 것과 비교


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# 261018 — DB 보관 52주(구간형 저장): 26w·52w 리비전은 이력이 앞 창보다 길 때만
# ═══════════════════════════════════════════════════════════════════════════════════════════════
def test_long_windows_appear_when_history_reaches_them():
    days = _weekly(53)                                   # 52주 + 1 → 52w 기준이 정확히 잡힌다
    hist = [_row(d, "2027.12E", op_krw=100 + i) for i, d in enumerate(days)]
    r = fe.compute_revision(hist)
    assert list(r["baselines"]) == ["1w", "4w", "12w", "26w", "52w"]
    assert r["baselines"]["26w"] == {"as_of": days[-27].isoformat(), "days": 182, "partial": False}
    assert r["baselines"]["52w"] == {"as_of": days[0].isoformat(), "days": 364, "partial": False}
    assert r["rows"][0]["vs"]["52w"]["op_krw_pct"] == 52.0                 # (152-100)/100
    assert r["windows_not_reached"] == [] and r["history_weeks"] == 52
    assert set(r["summary"]) == {"1w", "4w", "12w", "26w", "52w"}


def test_long_windows_are_left_out_not_duplicated_when_history_is_short():
    """이력 7주(지금 운영): 26w·52w 를 「가장 오래된 스냅샷과 부분 비교」로 내면 12w 와 같은 칸이 둘 더 생긴다 — 빼고 알린다."""
    days = _weekly(8)
    hist = [_row(d, "2026.12E", op_krw=100 + i) for i, d in enumerate(days)]
    r = fe.compute_revision(hist)
    assert list(r["baselines"]) == ["1w", "4w", "12w"]                     # 종전 응답과 같은 창
    assert r["baselines"]["12w"]["partial"] is True
    assert r["windows_not_reached"] == ["26w", "52w"]
    assert r["history_weeks"] == 7 and r["first_as_of"] == days[0].isoformat()


def test_26w_is_a_partial_comparison_once_history_passes_12w():
    """이력 20주: 12w 는 정확, 26w 는 가장 오래된 스냅샷(19주 전)과 부분 비교 — 12w 와 다른 날이라 싣는다. 52w 는 같은 날이라 뺀다."""
    days = _weekly(20)
    hist = [_row(d, "2026.12E", op_krw=100 + i) for i, d in enumerate(days)]
    r = fe.compute_revision(hist)
    assert r["baselines"]["12w"]["partial"] is False
    assert r["baselines"]["26w"] == {"as_of": days[0].isoformat(), "days": 133, "partial": True}
    assert "52w" not in r["baselines"] and r["windows_not_reached"] == ["52w"]


def test_screen_mode_keeps_a_partial_long_window():
    """유니버스 스크린은 고른 창 하나를 낸다 — 26w 를 골랐으면 이력이 짧아도 부분 비교(「이력 짧음」)로 싣는다."""
    days = _weekly(8)
    hist = [_row(d, "2026.12E", op_krw=100 + i) for i, d in enumerate(days)]
    r = fe.compute_revision(hist, long_needs_history=False)
    assert r["baselines"]["26w"] == {"as_of": days[0].isoformat(), "days": 49, "partial": True}
    assert r["windows_not_reached"] == []


# ── 저장 모양 — 달력 모양 한 벌. 질의는 창 조건을 붙이고, 최신 날짜는 게시 달력에서 ──────────────────────
def _probe(monkeypatch, latest=dt.date(2026, 10, 17)):
    calls = []

    def pg(sql, params=()):
        calls.append((sql, params))
        if sql == fe_fw.LATEST_SQL_CALENDAR:
            return [(latest,)]
        return []
    monkeypatch.setattr(fe, "pg_rows", pg)
    # 캐시를 통째로 갈아 끼운다 — 시험이 끝나면 되돌아가 다른 시험에 최신 날짜가 새지 않는다
    monkeypatch.setattr(fe, "_latest_cache", {"at": 0.0, "latest": None})
    return calls


from open_proxy_mcp import fwd_window as fe_fw  # noqa: E402


def test_history_query_carries_the_window(monkeypatch):
    calls = _probe(monkeypatch)
    fe._fetch_hist("005930")
    sql, params = calls[-1]
    assert sql == ("SELECT as_of, period, period_type, rev_krw, op_krw, ni_ctrl_krw, eps_krw, dps_krw FROM fwd_hist "
                   "WHERE stock_code=%s AND is_estimate AND as_of >= (SELECT max(as_of) FROM fwd_asof) - %s::int "
                   "ORDER BY as_of")
    assert params == ("005930", 364)
    assert fe._db_hist_weeks() == 52
    fe._fetch_hist_many(["005930"], "FY", fe._screen_span_days(28))
    assert calls[-1][1] == ("FY", ["005930"], 91)                         # 4w 스크린은 13주(관측 폭) 만 읽는다
    fe._fetch_hist_many(None, "ALL", fe._screen_span_days(364))
    assert calls[-1][1] == (364,)                                          # 52w 는 창 전체(창보다 넓게 읽지 않는다)


def test_window_setting_shrinks_the_history_query(monkeypatch):
    calls = _probe(monkeypatch)
    monkeypatch.setenv("FWD_HOT_WEEKS", "26")
    fe._fetch_hist("005930")
    assert calls[-1][1] == ("005930", 182)
    assert fe._db_hist_weeks() == 26
    monkeypatch.setenv("FWD_HOT_WEEKS", "abc")
    assert fe_fw.hot_weeks() == 52                                         # 잘못된 값이면 기본값


def test_stale_snapshot_warning_against_the_latest_calendar_date(monkeypatch):
    _probe(monkeypatch, latest=dt.date(2026, 10, 17))
    assert fe._stale_snapshot_warning(dt.date(2026, 10, 17)) is None
    w = fe._stale_snapshot_warning(dt.date(2026, 4, 18))
    assert w and "최신 컨센서스 스냅샷(2026-10-17)에 없다" in w and "2026-04-18 스냅샷" in w and "182일 전" in w


def test_stale_snapshot_warning_is_silent_when_latest_is_unknown(monkeypatch):
    monkeypatch.setattr(fe, "pg_rows", lambda sql, params=(): None)      # DB 장애
    monkeypatch.setattr(fe, "_latest_cache", {"at": 0.0, "latest": None})
    assert fe._stale_snapshot_warning(dt.date(2026, 4, 18)) is None


def test_latest_calendar_date_is_cached_for_ten_minutes(monkeypatch):
    calls = _probe(monkeypatch, latest=dt.date(2026, 10, 17))
    clock = {"t": 1000.0}
    monkeypatch.setattr(fe.time, "monotonic", lambda: clock["t"])
    assert fe._hist_latest() == dt.date(2026, 10, 17)
    clock["t"] += 599
    assert fe._hist_latest() == dt.date(2026, 10, 17)
    assert len(calls) == 1                                                 # 10분 안에는 다시 묻지 않는다
    clock["t"] += 2
    fe._hist_latest()
    assert len(calls) == 2                                                 # 10분이 지나면 다시 묻는다


def test_source_note_says_what_the_db_actually_keeps(monkeypatch):
    _probe(monkeypatch)
    assert "최근 52주" in fe._hist_source_note() and "보관본" in fe._hist_source_note()
    assert "13주" not in fe._hist_source_note()


def test_long_window_note_only_promises_what_the_db_can_hold(monkeypatch):
    """「이력이 쌓이면 나온다」는 그 창이 DB 보관 안에 들 때만 참이다."""
    rev = {"rows": [{}], "windows_not_reached": ["26w", "52w"], "history_weeks": 7, "first_as_of": "2026-08-29"}
    _probe(monkeypatch)
    w = fe._long_windows_warning(rev)
    assert w.startswith("리비전 26w·52w: 이 종목 이력이 7주(2026-08-29~)뿐이라 싣지 않았다")
    assert w.endswith("이력이 그만큼 쌓이면 나온다(DB 에는 최근 52주를 둔다).")
    monkeypatch.setenv("FWD_HOT_WEEKS", "30")                               # 창을 줄이면 26w 만 닿는다
    w = fe._long_windows_warning(rev)
    assert "26w 는 이력이 그만큼 쌓이면 나오고, 52w 는 DB 에 최근 30주만 두어 나오지 않는다" in w
    assert fe._long_windows_warning(dict(rev, windows_not_reached=[])) is None
    assert fe._long_windows_warning(dict(rev, rows=[])) is None


def test_consensus_gap_queries_add_the_window_and_read_the_calendar_for_reach(monkeypatch):
    """잠정실적 컨센서스 대비(#111)도 리비전과 같은 계약 — 분기 스냅샷 질의에 창 조건, 「이력이 닿나」는 게시 달력을 같은 창으로."""
    calls = _probe(monkeypatch)
    fe._fetch_quarter_consensus("005930", "2026-09-30", "2026-10-08")
    win = fe_fw.window_cond()
    assert calls[-2:] == [
        ("SELECT as_of, period, is_estimate, basis, rev_krw, op_krw, ni_ctrl_krw FROM fwd_hist "
         f"WHERE stock_code=%s AND period_type='Q' AND period_end=%s AND {win} ORDER BY as_of",
         ("005930", dt.date(2026, 9, 30), 364)),
        (f"SELECT EXISTS (SELECT 1 FROM fwd_asof WHERE as_of < %s AND {win})", (dt.date(2026, 10, 8), 364))]
    gone = fe.compute_consensus_gap({"revenue": 1}, filed="2026-10-08", consolidated=True, rows=[], history_reaches=False)
    assert "최근 52주 창 밖" in gone["why"] and "DB 에는 최근 52주" in gone["source"]
