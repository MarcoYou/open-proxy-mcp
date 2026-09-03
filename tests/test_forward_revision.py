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
    assert want == {"revision"} and bad == []
    assert "revision" in fe.parse_bundles("all")[0]
