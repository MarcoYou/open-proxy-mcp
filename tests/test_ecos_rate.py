"""ECOS 무위험 금리 표(`ecos_rate`) · 일별 배치(`scripts/ecos_rate_daily.py`) · DDL(`scripts/ecos_rate_ddl.py`).

앞쪽은 네트워크·DB 0 — 원천 응답 모양(실측 261021 의 `StatisticSearch` 행 모양), 접기·창·계획·키 비노출.
뒤쪽(`pg17`)은 Postgres 17 에서 DDL 멱등·권한, 한 트랜잭션 쓰기, 같은 값 재실행 시 행을 건드리지 않음, 옛 값 조건, 정정 과다 거절, 창 밖 지우기 가드.
Docker 가 없으면 뒤쪽만 건너뛴다.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import pathlib
import sys
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


S = _load("ecos_rate_daily")
ddl = _load("ecos_rate_ddl")
KEY = "K3Y-NEVER-IN-OUTPUT-0123456789"
C, K, Y1, Y3, B = (S.BY_CODE[c] for c in ("C", "K", "1", "3", "B"))


def payload(s, pairs, *, total=None, **over):
    """실측과 같은 모양의 StatisticSearch 응답."""
    rows = [{"STAT_CODE": s.stat, "STAT_NAME": "x", "ITEM_CODE1": s.item, "ITEM_NAME1": s.name, "ITEM_CODE2": None,
             "UNIT_NAME": "연%", "WGT": None, "TIME": t, "DATA_VALUE": v, **over} for t, v in pairs]
    return {"StatisticSearch": {"list_total_count": len(rows) if total is None else total, "row": rows}}


def D(s):
    return S.parse_dd(s)


# ═══ DDL ═══════════════════════════════════════════════════════════════════════════════════════════
def test_ddl_text_shape():
    sql = ddl.render()
    assert sql.startswith("BEGIN;") and sql.rstrip().endswith("COMMIT;")
    assert 'series "char"' in sql and "rate   real" in sql and "PRIMARY KEY (series, d)" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql and "FOR SELECT USING (true)" in sql
    assert "REVOKE ALL ON ecos_rate FROM anon, authenticated" in sql and "GRANT SELECT ON ecos_rate TO opm_ro" in sql
    assert "ecos_fx_rate" not in ddl.STATEMENTS.replace("COMMENT", "")      # 환율 캐시 표를 건드리지 않는다
    codes = "".join(s.code for s in S.SERIES)
    assert all(f"'{c}'" in ddl.STATEMENTS for c in codes)                  # CHECK 의 허용 코드 = 배치의 계열


# ═══ 원천 응답 → 점 ══════════════════════════════════════════════════════════════════════════════════
def test_parse_payload_real_shape():
    pts = S.parse_payload(C, payload(C, [("20261016", "3.25"), ("20261019", "3.25"), ("20261020", "3.26")]))
    assert pts == [(D("20261016"), 3.25), (D("20261019"), 3.25), (D("20261020"), 3.26)]


def test_parse_payload_no_data_is_empty_but_other_errors_raise():
    assert S.parse_payload(B, {"RESULT": {"CODE": "INFO-200", "MESSAGE": "해당하는 데이터가 없습니다."}}) == []
    with pytest.raises(S.EcosError, match="INFO-100"):
        S.parse_payload(C, {"RESULT": {"CODE": "INFO-100", "MESSAGE": "인증키가 유효하지 않습니다."}})
    with pytest.raises(S.EcosError):
        S.parse_payload(C, ["x"])
    with pytest.raises(S.EcosError):
        S.parse_payload(C, {"unexpected": 1})


@pytest.mark.parametrize("bad, why", [
    (payload(C, [("20261020", "3.26")], total=5), "잘렸다"),
    (payload(C, [("20261020", "3.26")], ITEM_CODE1="010502001"), "코드"),
    (payload(C, [("20261020", "3.26")], STAT_CODE="817Y003"), "코드"),
    (payload(C, [("2026-10-20", "3.26")]), "TIME"),
    (payload(C, [("20261340", "3.26")]), "달력"),
    (payload(C, [("20261020", "abc")]), "숫자"),
    (payload(C, [("20261020", "31.0")]), "상식"),
    (payload(C, [("20261020", "-1.5")]), "상식"),
    (payload(C, [("20261020", "nan")]), "상식"),
    (payload(C, [("20261020", "3.2"), ("20261020", "3.3")]), "두 번"),
])
def test_parse_payload_rejects_malformed(bad, why):
    with pytest.raises(S.EcosError, match=why):
        S.parse_payload(C, bad)


def test_parse_payload_skips_blank_values():
    assert S.parse_payload(C, payload(C, [("20261019", ""), ("20261020", "3.26")])) == [(D("20261020"), 3.26)]
    assert S.parse_payload(C, payload(C, [("20261019", None)])) == []


def test_name_drift_warns_without_failing(capsys):
    S.fetch_series(_client({"C": payload(C, [("20261020", "3.26")], ITEM_NAME1="CD(91일물)")}), KEY, C, D("20261001"), D("20261021"))
    assert "::warning::C" in capsys.readouterr().out


# ═══ 접기 · 창 · 시작일 ═══════════════════════════════════════════════════════════════════════════════
def _cal(start, vals):
    d0 = D(start)
    return [(d0 + timedelta(days=i), v) for i, v in enumerate(vals)]


def test_fold_steps_keeps_only_change_days():
    cal = _cal("20260710", [2.5, 2.5, 2.5, 2.75, 2.75, 2.75, 2.75, 3.0])
    assert S.fold_steps(cal, None) == [(D("20260710"), 2.5), (D("20260713"), 2.75), (D("20260717"), 3.0)]
    assert S.fold_steps(cal, 2.5) == [(D("20260713"), 2.75), (D("20260717"), 3.0)]       # 앞 값을 알면 첫 날은 행이 아니다
    assert S.fold_steps(cal, 2.75)[0] == (D("20260710"), 2.5)                             # 앞 값과 다르면 첫 날이 변경일


def test_f32_compare_matches_real_storage():
    assert S.f32(3.26) == S.f32(3.2599999904632568)           # real 에 저장했다 읽은 값과 같은 값으로 본다
    assert S.f32(3.26) != S.f32(3.27)


def test_with_carry_in_keeps_last_point_before_window():
    pts = [(D("20230113"), 3.5), (D("20241011"), 3.25), (D("20241128"), 3.0)]
    assert S.with_carry_in(pts, D("20231007")) == pts                                       # 창 앞 마지막 점(20230113)은 남는다
    assert S.with_carry_in(pts, D("20241101")) == [(D("20241011"), 3.25), (D("20241128"), 3.0)]
    assert S.with_carry_in([(D("20260101"), 1.0)], D("20230101")) == [(D("20260101"), 1.0)]


def test_window_lo_is_weekly_cutoff_minus_pad():
    from open_proxy_mcp.krx_retention import krx_weekly_cutoff
    for today in (D("20261021"), D("20280229"), D("20260101")):
        assert S.window_lo(today) == D(krx_weekly_cutoff(S.fmt_dd(today))) - timedelta(days=S.WINDOW_PAD_DAYS)
    assert S.window_lo(D("20261021")) == D("20231007")
    # 주간 시세 창(최신 주의 3년 전)은 최신 주가 오늘보다 늦을 수 없으므로 항상 이 창 하한 이후다
    for lag in range(0, 12):
        assert D(krx_weekly_cutoff(S.fmt_dd(D("20261021") - timedelta(days=lag)))) >= S.window_lo(D("20261021"))


def test_fetch_start_rules():
    today, lo = D("20261021"), D("20231007")
    assert S.fetch_start(C, today, lo, None) == D("20230927")                    # 비었다: 창 하한 − 10일(carry-in)
    assert S.fetch_start(K, today, lo, None) == D("20230927")
    assert S.fetch_start(B, today, lo, None) == D("19990506")                    # 접는 계열은 첫 관측일부터
    assert S.fetch_start(C, today, lo, D("20261020")) == D("20260921")      # 최근 30일
    assert S.fetch_start(C, today, lo, D("20260801")) == D("20260729")           # 오래 쉬었다: 최신 행 − 3일부터 따라잡기
    assert S.fetch_start(C, today, lo, D("20230101")) == D("20230927")           # 창 앞까지 거슬러도 carry-in 몫(하한 − 10일)까지만
    assert S.fetch_start(C, today, lo, D("20261020"), since=D("20260101")) == D("20260101")
    assert S.fetch_start(K, today, lo, None, since=D("20200101")) == D("20211125")


# ═══ HTTP · 키 비노출 ═══════════════════════════════════════════════════════════════════════════════════
def _client(by_code, status=200, calls=None):
    """요청 경로의 통계표/항목으로 계열을 알아 응답하는 가짜 ECOS."""
    item_to_code = {s.item: s.code for s in S.SERIES}

    def handler(request):
        if calls is not None:
            calls.append(str(request.url))
        item = request.url.path.rstrip("/").split("/")[-1]
        body = by_code[item_to_code[item]]
        if isinstance(body, Exception):
            raise body
        return httpx.Response(status, json=body)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_path_has_window_and_item_and_one_call_per_series():
    calls = []
    cl = _client({"C": payload(C, [("20261020", "3.26")])}, calls=calls)
    S.fetch_series(cl, KEY, C, D("20260921"), D("20261021"))
    assert len(calls) == 1
    assert calls[0] == f"https://ecos.bok.or.kr/api/StatisticSearch/{KEY}/json/kr/1/100000/817Y002/D/20260921/20261021/010502000"


def test_retries_5xx_then_succeeds_and_gives_up_on_4xx():
    seq = iter([httpx.Response(503), httpx.Response(502), httpx.Response(200, json=payload(C, [("20261020", "3.26")]))])
    cl = httpx.Client(transport=httpx.MockTransport(lambda r: next(seq)))
    naps = []
    assert S.fetch_series(cl, KEY, C, D("20260921"), D("20261021"), sleep=naps.append) == [(D("20261020"), 3.26)]
    assert naps == [2.0, 4.0]
    n = {"c": 0}

    def forbidden(r):
        n["c"] += 1
        return httpx.Response(403)
    with pytest.raises(S.EcosError, match="HTTP 403"):
        S.fetch_series(httpx.Client(transport=httpx.MockTransport(forbidden)), KEY, C, D("20260921"), D("20261021"), sleep=lambda s: None)
    assert n["c"] == 1                                     # 4xx 는 다시 시도하지 않는다


def test_errors_never_carry_the_key_or_url():
    boom = httpx.ConnectError(f"cannot reach https://ecos.bok.or.kr/api/StatisticSearch/{KEY}/json")
    with pytest.raises(S.EcosError) as ei:
        S.fetch_series(_client({"C": boom}), KEY, C, D("20260921"), D("20261021"), sleep=lambda s: None)
    e = ei.value
    assert KEY not in str(e) and "ecos.bok.or.kr" not in str(e) and "ConnectError" in str(e)
    assert e.__cause__ is None and e.__suppress_context__          # 트레이스백에 원 예외(URL 포함)가 따라붙지 않는다
    with pytest.raises(S.EcosError) as ei:
        S.fetch_series(_client({"C": {"RESULT": {"CODE": "ERROR-500", "MESSAGE": f"{KEY}"}}}), KEY, C, D("20260921"), D("20261021"))
    assert KEY not in str(ei.value)                        # 서버 MESSAGE 는 싣지 않는다 — 코드만


def test_httpx_request_log_does_not_leak_key(caplog):
    root = logging.getLogger()
    old = {n: logging.getLogger(n).level for n in ("httpx", "httpcore")}
    try:
        for n in old:
            logging.getLogger(n).setLevel(logging.NOTSET)
        root.setLevel(logging.DEBUG)
        with caplog.at_level(logging.DEBUG):
            S._quiet_http_loggers()
            S.fetch_series(_client({"C": payload(C, [("20261020", "3.26")])}), KEY, C, D("20260921"), D("20261021"))
        assert KEY not in caplog.text
        # 대조: 낮추지 않으면 실제로 새는지(이 시험이 헛돌지 않는다는 증거)
        caplog.clear()
        for n in old:
            logging.getLogger(n).setLevel(logging.INFO)
        with caplog.at_level(logging.INFO):
            S.fetch_series(_client({"C": payload(C, [("20261020", "3.26")])}), KEY, C, D("20260921"), D("20261021"))
        assert KEY in caplog.text
    finally:
        for n, lvl in old.items():
            logging.getLogger(n).setLevel(lvl)


# ═══ 계획 ══════════════════════════════════════════════════════════════════════════════════════════════
def test_make_plan_counts_insert_update_same_orphan():
    pts = {"C": [(D("20261016"), 3.25), (D("20261019"), 3.26), (D("20261020"), 3.30)]}
    existing = {("C", D("20261016")): S.f32(3.25), ("C", D("20261019")): S.f32(3.25), ("C", D("20261014")): S.f32(3.2)}
    plan = S.make_plan(pts, existing, {"C": (D("20261001"), D("20261021"))})
    assert plan.insert == [("C", D("20261020"), 3.30)]
    assert [(c, d) for c, d, _, _ in plan.update] == [("C", D("20261019"))] and plan.same == 1
    assert plan.orphans == [("C", D("20261014"))]                       # 원천 구간 안인데 원천에 없는 행 — 지우지 않고 알린다
    # 구간 밖 행은 고아가 아니다
    plan = S.make_plan(pts, {**existing, ("C", D("20200101")): 1.0}, {"C": (D("20261001"), D("20261021"))})
    assert ("C", D("20200101")) not in plan.orphans


def test_revision_alert_threshold():
    ok = S.Plan(update=[("C", D("20261019"), 3.30, 3.25)])
    S.check_revisions(ok)
    big = S.Plan(update=[("C", D("20261019"), 3.90, 3.25)])
    with pytest.raises(S.EcosError, match="0.5"):
        S.check_revisions(big)


def test_freshness_checks():
    today = D("20261021")
    S.check_freshness({"C": [(D("20261020"), 3.2)], "B": []}, today)                  # 접는 계열은 비어도 된다(최근 변경 없음)
    S.check_freshness({"K": [(D("20261019"), 2.9)]}, today)                            # KOFR 은 하루 늦다
    with pytest.raises(S.EcosError, match="받은 값이 없다"):
        S.check_freshness({"C": []}, today)
    with pytest.raises(S.EcosError, match="늦다"):
        S.check_freshness({"3": [(D("20261001"), 3.0)]}, today)
    S.check_freshness({"C": [(D("20261013"), 3.2)]}, today)                            # 추석 연휴급 8일은 정상


def test_cold_wait_matches_server_manifest_max_age(monkeypatch):
    from open_proxy_mcp import cold_store
    monkeypatch.delenv("OPM_COLD_MANIFEST_MAX_AGE_SEC", raising=False)
    assert S.COLD_DELETE_WAIT_SEC >= cold_store._max_age_sec()


def _manifest(max_dd="20231006", min_dd="20151201", updated="2026-10-17T00:00:00+00:00"):
    entry = {"file": "ecos_rate_hist.5ec45eed2117af28.sqlite", "sha256": "a" * 64, "bytes": 1234, "rows": 10,
             "coverage": {"min_dd": min_dd, "max_dd": max_dd, "upto": max_dd}, "file_schema": 1}
    return json.dumps({"schema": 1, "kind": "opm-cold-serve", "updated_at": updated, "datasets": {S.COLD_DATASET: {
        "engine": "sqlite3", "view": "ecos_rate", "columns": ["series", "dd", "rate"], "key": ["series", "dd"],
        "date_col": "dd", "files": [entry], "coverage": {"min_dd": min_dd, "max_dd": max_dd}}}}).encode()


def test_cold_guard_from_manifest_reads_coverage_and_updated_at():
    mn, mx, pub = S.cold_guard_from_manifest(_manifest())
    assert (mn, mx) == (D("20151201"), D("20231006")) and pub == datetime(2026, 10, 17, tzinfo=timezone.utc)
    with pytest.raises(Exception):
        S.cold_guard_from_manifest(_manifest(), dataset="no_such")
    with pytest.raises(S.EcosError):
        S.cold_guard_from_manifest(json.dumps({**json.loads(_manifest()), "updated_at": None}).encode())


def test_parse_aware_requires_timezone():
    assert S.parse_aware("2026-10-17T00:00:00Z").tzinfo is not None
    with pytest.raises(S.EcosError):
        S.parse_aware("2026-10-17T00:00:00")


def test_main_rejects_unknown_series_and_missing_key(monkeypatch, capsys):
    monkeypatch.delenv("ECOS_API_KEY", raising=False)
    assert S.main(["--series", "X"]) == 2
    assert S.main(["--today", "20261021"]) == 2
    err = capsys.readouterr().err
    assert "알 수 없는 계열" in err and "ECOS_API_KEY" in err


def test_main_without_db_only_fetches_and_never_writes(monkeypatch, capsys):
    monkeypatch.setenv("ECOS_API_KEY", KEY)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ECOS_RATE_WRITE", "1")                    # DB 가 없으면 쓸 수 없다 → 설정 오류
    assert S.main(["--today", "20261021"]) == 2
    monkeypatch.delenv("ECOS_RATE_WRITE")
    fetched = S.Fetched({"C": [(D("20261020"), 3.26)]}, {"C": (D("20260921"), D("20261021"))}, 1)
    monkeypatch.setattr(S, "run_fetch", lambda *a, **k: fetched)
    assert S.main(["--today", "20261021", "--series", "C"]) == 0
    out = capsys.readouterr().out
    assert "쓰기 꺼짐" in out and "계획만 냈다" in out and KEY not in out


def test_write_switch_is_exactly_one(monkeypatch):
    for v, want in (("1", True), (" 1 ", True), ("0", False), ("true", False), ("", False)):
        assert S.write_enabled({"ECOS_RATE_WRITE": v}) is want
    assert S.write_enabled({}) is False


# ═══ Postgres 17 ══════════════════════════════════════════════════════════════════════════════════════════
psycopg = pytest.importorskip("psycopg")


@pytest.fixture
def db(pg17):
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(ddl.STATEMENTS)
        c.execute(ddl.STATEMENTS)                             # 멱등
    return pg17


@pytest.fixture
def con(db):
    with psycopg.connect(db, autocommit=True) as c:
        yield c


def _fetched(**kw):
    """kw: 코드 → 점 목록. 받은 구간은 (가장 이른 점 − 2일, 2026-10-21)."""
    pts = {c: list(v) for c, v in kw.items()}
    rng = {c: ((v[0][0] - timedelta(days=2)) if v else D("20261001"), D("20261021")) for c, v in pts.items()}
    return S.Fetched(pts, rng, len(pts))


def _rows(con):
    return con.execute("SELECT series::text, d, rate FROM ecos_rate ORDER BY 1, 2").fetchall()


def test_access_control_and_constraints(db):
    with psycopg.connect(db, autocommit=True) as c:
        assert c.execute("SELECT relrowsecurity FROM pg_class WHERE relname='ecos_rate'").fetchone()[0] is True
        for role in ("anon", "authenticated"):
            for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert c.execute("SELECT has_table_privilege(%s,'ecos_rate',%s)", (role, priv)).fetchone()[0] is False
        assert c.execute("SELECT has_table_privilege('opm_ro','ecos_rate','SELECT')").fetchone()[0] is True
        assert c.execute("SELECT has_table_privilege('opm_ro','ecos_rate','INSERT')").fetchone()[0] is False
        c.execute("INSERT INTO ecos_rate VALUES ('C','2026-10-20',3.26)")
        c.execute("SET ROLE opm_ro")                          # BYPASSRLS 없음 — 정책을 거쳐 읽힌다
        assert c.execute("SELECT count(*) FROM ecos_rate").fetchone()[0] == 1
        c.execute("RESET ROLE")
        for sql in ("INSERT INTO ecos_rate VALUES ('X','2026-10-20',3.0)",          # 모르는 계열
                    "INSERT INTO ecos_rate VALUES ('C','2026-10-20',3.5)",          # 기본키 중복
                    "INSERT INTO ecos_rate VALUES ('C','2026-10-21',99)",           # 상식 밖
                    "INSERT INTO ecos_rate VALUES ('C','2026-10-22',NULL)"):
            with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation, psycopg.errors.NotNullViolation)):
                c.execute(sql)


def test_series_roundtrips_as_one_char_text(con):
    S.settle(con, _fetched(C=[(D("20261020"), 3.26)]), D("20231007"), write=True)
    assert _rows(con) == [("C", D("20261020"), pytest.approx(3.26, abs=1e-6))]
    assert con.execute("SELECT pg_column_size(series) FROM ecos_rate").fetchone()[0] == 1


def test_first_write_inserts_everything_and_step_series_is_folded(con):
    lo = D("20231007")
    cal = _cal("20230110", [3.25] * 3 + [3.5] * 10)                        # 20230113 에 3.25 → 3.5, 창(20231007) 앞
    cal += _cal("20241010", [3.5, 3.25, 3.25])                              # 20241011 변경
    f = S.Fetched({"C": [(D("20230927"), 3.9), (D("20231010"), 4.0), (D("20261020"), 3.26)], "B": cal},
                  {"C": (D("20230927"), D("20261021")), "B": (D("19990506"), D("20261021"))}, 2)
    plan = S.settle(con, f, lo, write=True)
    rows = _rows(con)
    assert [(c, d) for c, d, _ in rows if c == "C"] == [("C", D("20230927")), ("C", D("20231010")), ("C", D("20261020"))]   # carry-in 포함
    assert [d for c, d, _ in rows if c == "B"] == [D("20230113"), D("20241011")]       # 접힌 점 셋 중 창 앞은 마지막(20230113)만
    assert plan.same == 0 and len(plan.insert) == len(rows) and not plan.update


def test_second_run_with_same_values_touches_no_row(con):
    f = _fetched(C=[(D("20261016"), 3.25), (D("20261019"), 3.25), (D("20261020"), 3.26)])
    S.settle(con, f, D("20231007"), write=True)
    before = con.execute("SELECT ctid::text, xmin::text FROM ecos_rate ORDER BY d").fetchall()
    plan = S.settle(con, f, D("20231007"), write=True)
    assert plan.writes == 0 and plan.same == 3
    assert con.execute("SELECT ctid::text, xmin::text FROM ecos_rate ORDER BY d").fetchall() == before     # 행 버전도 그대로(죽은 행 0)


def test_small_revision_updates_with_old_value_and_big_one_writes_nothing(con):
    S.settle(con, _fetched(C=[(D("20261016"), 3.25), (D("20261019"), 3.25)]), D("20231007"), write=True)
    plan = S.settle(con, _fetched(C=[(D("20261016"), 3.25), (D("20261019"), 3.27), (D("20261020"), 3.28)]), D("20231007"), write=True)
    assert [(c, d) for c, d, _, _ in plan.update] == [("C", D("20261019"))] and len(plan.insert) == 1
    assert [round(r[2], 2) for r in _rows(con)] == [3.25, 3.27, 3.28]
    snap = _rows(con)
    with pytest.raises(S.EcosError, match="0.5"):
        S.settle(con, _fetched(C=[(D("20261016"), 4.50), (D("20261021"), 3.3)]), D("20231007"), write=True)
    assert _rows(con) == snap                                                  # 새 행(20261021)도 안 들어갔다 — 전부 되돌아감


def test_old_value_condition_aborts_when_row_changed_after_the_read(con):
    S.settle(con, _fetched(C=[(D("20261016"), 3.25)]), D("20231007"), write=True)
    stale = S.Plan(update=[("C", D("20261016"), 3.30, 3.20)])                 # 읽었다고 가정한 옛 값 3.20 ≠ 실제 3.25
    with pytest.raises(S.ConflictError):
        with con.transaction():
            S.apply_plan(con, stale)
    assert [round(r[2], 2) for r in _rows(con)] == [3.25]


def test_dry_run_reads_but_never_writes(con):
    plan = S.settle(con, _fetched(C=[(D("20261020"), 3.26)]), D("20231007"), write=False)
    assert len(plan.insert) == 1 and _rows(con) == []


def test_settle_requires_autocommit_connection(db):
    with psycopg.connect(db) as c:                                              # autocommit 아님
        with pytest.raises(ValueError, match="autocommit"):
            S.settle(c, _fetched(C=[(D("20261020"), 3.26)]), D("20231007"), write=True)


def test_incremental_step_series_uses_db_value_as_prior(con):
    lo = D("20231007")
    S.settle(con, S.Fetched({"B": _cal("19990506", [1.5] * 10)}, {"B": (D("19990506"), D("19990515"))}, 1), D("19990101"), write=True)
    con.execute("DELETE FROM ecos_rate")
    S.settle(con, S.Fetched({"B": _cal("20230110", [3.5] * 5)}, {"B": (D("20230110"), D("20230114"))}, 1), lo, write=True)
    assert [d for _, d, _ in _rows(con)] == [D("20230110")]                     # 첫 행 = 첫 관측일(앞 값 모름)
    # 다음 날부터 최근 구간만 받는다 — DB 의 앞 값(3.5)과 같으면 행이 없고, 다르면 그 날이 변경일
    S.settle(con, S.Fetched({"B": _cal("20261019", [3.5, 3.5, 3.75, 3.75])}, {"B": (D("20261019"), D("20261022"))}, 1), lo, write=True)
    assert [(d, r) for _, d, r in _rows(con)] == [(D("20230110"), 3.5), (D("20261021"), 3.75)]


def _fill(con, n_days=40):
    """CD 와 국고채 3년을 2026-08-01 부터 n_days 일, 기준금리는 변경점 셋."""
    f = S.Fetched({"C": _cal("20260801", [3.0 + i / 1000 for i in range(n_days)]),
                   "3": _cal("20260801", [3.1 + i / 1000 for i in range(n_days)]),
                   "B": [(D("20260101"), 2.5), (D("20260401"), 2.5), (D("20260716"), 2.75)]},
                  {"C": (D("20260801"), D("20261021")), "3": (D("20260801"), D("20261021")),
                   "B": (D("20260101"), D("20261021"))}, 3)
    S.settle(con, f, D("20260101"), write=True)


def test_prune_guards_and_carry_in(con):
    _fill(con)
    total = con.execute("SELECT count(*) FROM ecos_rate").fetchone()[0]
    pub = datetime(2026, 10, 1, tzinfo=timezone.utc)
    now = datetime(2026, 10, 21, tzinfo=timezone.utc)
    lo = D("20260810")
    kw = dict(cold_min=D("20151201"), cold_max=D("20260809"), cold_published_at=pub, now=now)
    # ① 발행 직후(72시간 전) → 거절
    with pytest.raises(S.EcosError, match="72시간"):
        S.prune_rows(con, lo, **{**kw, "cold_published_at": now - timedelta(hours=71)}, write=True)
    # ② 보관본이 지울 끝(lo−1)을 못 덮음 → 거절
    with pytest.raises(S.EcosError, match="덮지 못한다"):
        S.prune_rows(con, lo, **{**kw, "cold_max": D("20260808")}, write=True)
    # ③ 보관본 시작이 표 가장 이른 날보다 늦음 → 거절
    with pytest.raises(S.EcosError, match="가장 이른 날"):
        S.prune_rows(con, lo, **{**kw, "cold_min": D("20260301")}, write=True)
    # ④ 지울 행이 표의 25% 넘음 → 거절
    with pytest.raises(S.EcosError, match="25%"):
        S.prune_rows(con, D("20261015"), **{**kw, "cold_max": D("20261014")}, write=True)
    assert con.execute("SELECT count(*) FROM ecos_rate").fetchone()[0] == total
    # 드라이: 세기만
    n, t = S.prune_rows(con, lo, **kw, write=False)
    assert (n, t) == (8 * 2 + 1, total)   # C·3 각 8행 + 기준금리 20260101 and con.execute("SELECT count(*) FROM ecos_rate").fetchone()[0] == total
    # 실제: 계열마다 창 앞 마지막 행(carry-in) 하나는 남는다
    n, _ = S.prune_rows(con, lo, **kw, write=True)
    left = _rows(con)
    assert n == 17
    assert [d for c, d, _ in left if c == "C"][0] == D("20260809") and [d for c, d, _ in left if c == "3"][0] == D("20260809")
    assert [d for c, d, _ in left if c == "B"] == [D("20260716")]               # 기준금리: 창 앞 마지막 변경일 하나만
    assert S.prune_rows(con, lo, **kw, write=True)[0] == 0                       # 멱등


def test_verify_flags_stale_or_missing_series(con):
    _fill(con)
    problems = S.verify(con, D("20260915"))
    assert any(p.startswith("K:") and "행이 없다" in p for p in problems) and any(p.startswith("1:") for p in problems)
    assert not any(p.startswith(("C:", "3:", "B:")) for p in problems)
    assert any(p.startswith("C:") and "늦다" in p for p in S.verify(con, D("20261021")))


def test_main_end_to_end_with_switch(db, monkeypatch, capsys):
    monkeypatch.setenv("ECOS_API_KEY", KEY)
    monkeypatch.setenv("DATABASE_URL", db)
    monkeypatch.delenv("ECOS_RATE_WRITE", raising=False)
    full = {
        "C": _cal("20260921", [3.20 + i / 1000 for i in range(30)]),
        "K": _cal("20260921", [2.90 + i / 1000 for i in range(29)]),
        "1": _cal("20260921", [2.80 + i / 1000 for i in range(30)]),
        "3": _cal("20260921", [3.00 + i / 1000 for i in range(30)]),
        "B": _cal("20260921", [3.0] * 31),
    }
    # ECOS 가 거래일만 준다고 가정하고 주말은 뺀다(B 는 달력 전부)
    full = {c: (v if c == "B" else [p for p in v if p[0].weekday() < 5]) for c, v in full.items()}
    monkeypatch.setattr(S, "run_fetch", lambda client, key, codes, today, lo, db_max, since, **k: S.Fetched(
        {c: full[c] for c in codes}, {c: (full[c][0][0], today) for c in codes}, len(codes)))

    # 스위치 꺼짐 + 표 있음 → DB 와 맞대 계획만, 아무것도 쓰지 않음
    assert S.main(["--today", "20261021"]) == 0
    assert "쓰지 않았다" in capsys.readouterr().out
    with psycopg.connect(db) as c:
        assert c.execute("SELECT count(*) FROM ecos_rate").fetchone()[0] == 0

    monkeypatch.setenv("ECOS_RATE_WRITE", "1")
    assert S.main(["--today", "20261021", "--dry-run"]) == 0                    # --dry-run 이 스위치를 이긴다
    with psycopg.connect(db) as c:
        assert c.execute("SELECT count(*) FROM ecos_rate").fetchone()[0] == 0
    capsys.readouterr()

    assert S.main(["--today", "20261021"]) == 0                                 # 켜짐 → 씀 (마지막 날이 어제 근처라 신선도 통과)
    out = capsys.readouterr().out
    assert "썼다" in out and KEY not in out
    with psycopg.connect(db) as c:
        n = c.execute("SELECT count(*) FROM ecos_rate").fetchone()[0]
        assert n == sum(len(v) for k, v in full.items() if k != "B") + 1        # B 는 변경점 없이 첫 점 하나
    assert S.main(["--today", "20261021"]) == 0                                 # 같은 값 재실행
    assert "새 0 · 정정 0" in capsys.readouterr().out


def test_main_before_ddl_with_switch_off_is_a_clean_noop(pg17, monkeypatch, capsys):
    monkeypatch.setenv("ECOS_API_KEY", KEY)
    monkeypatch.setenv("DATABASE_URL", pg17)
    monkeypatch.delenv("ECOS_RATE_WRITE", raising=False)
    monkeypatch.setattr(S, "run_fetch", lambda *a, **k: S.Fetched({"C": [(D("20261020"), 3.26)]}, {"C": (D("20260921"), D("20261021"))}, 1))
    assert S.main(["--today", "20261021", "--series", "C"]) == 0
    assert "표가 아직 없다" in capsys.readouterr().out
    monkeypatch.setenv("ECOS_RATE_WRITE", "1")
    assert S.main(["--today", "20261021", "--series", "C"]) == 1                # 스위치가 켜졌는데 표가 없으면 실패


def test_prune_cli_requires_guards(db, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", db)
    monkeypatch.delenv("ECOS_RATE_WRITE", raising=False)
    assert S.main(["--today", "20261021", "--prune"]) == 2
    assert "--manifest" in capsys.readouterr().err
    assert S.main(["--today", "20261021", "--prune", "--cold-coverage-max", "20231006",
                   "--cold-published-at", "2026-10-21T00:00:00"]) == 1           # 시간대 없는 시각은 거절
    assert "시간대" in capsys.readouterr().out


def _churn(con, days=600):
    """계열 다섯이 날마다 한 줄씩 들어가는 일별 배치를 흉내 — 색인 오른쪽 끝이 다섯 곳에서 쪼개진다."""
    d0 = D("20240102")
    for i in range(days):
        d = d0 + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        with con.cursor() as cur:
            cur.executemany('INSERT INTO ecos_rate VALUES (%s::"char", %s, %s::real)', [(s, d, 2.0 + (i % 150) / 100) for s in "C13K"])


def test_maintain_rebuilds_pk_index_smaller_and_keeps_data(con):
    _churn(con)
    rows_before = _rows(con)
    before, after = S.maintain(con)
    assert after["index"] < before["index"] * 0.8, (before, after)           # 쪼개져 비어 있던 쪽이 접힌다
    assert _rows(con) == rows_before and after["rows"] == before["rows"]
    pk = con.execute("SELECT conname FROM pg_constraint WHERE conrelid='ecos_rate'::regclass AND contype='p'").fetchone()[0]
    assert pk == S.PK_INDEX
    assert con.execute("SELECT count(*) FROM pg_index WHERE indrelid='ecos_rate'::regclass AND NOT indisvalid").fetchone()[0] == 0
    S.maintain(con)                                                           # 다시 해도 같다


def test_maintain_failure_drops_leftover_invalid_index(con):
    _churn(con, 60)
    con.execute("CREATE INDEX ecos_rate_pkey_ccnew ON ecos_rate (rate)")
    con.execute("UPDATE pg_index SET indisvalid = false WHERE indexrelid = 'ecos_rate_pkey_ccnew'::regclass")     # REINDEX CONCURRENTLY 가 죽으면 이렇게 남는다

    class Boom:
        def __init__(self, c):
            self._c = c
        def __getattr__(self, n):
            return getattr(self._c, n)
        def execute(self, sql, *a, **k):
            if str(sql).startswith("REINDEX"):
                raise psycopg.errors.QueryCanceled("lock timeout")
            return self._c.execute(sql, *a, **k)
    with pytest.raises(psycopg.errors.QueryCanceled):
        S.maintain(Boom(con))
    assert con.execute("SELECT to_regclass('ecos_rate_pkey_ccnew') IS NULL").fetchone()[0] is True
    assert con.execute("SELECT to_regclass('ecos_rate_pkey') IS NOT NULL").fetchone()[0] is True       # 기본키 색인은 그대로


def test_main_maintains_on_first_of_month_and_on_flag_only_when_writing(db, monkeypatch, capsys):
    monkeypatch.setenv("ECOS_API_KEY", KEY)
    monkeypatch.setenv("DATABASE_URL", db)
    pts = {"C": _cal("20261028", [3.2, 3.2, 3.2, 3.2])}
    monkeypatch.setattr(S, "run_fetch", lambda client, key, codes, today, lo, db_max, since, **k: S.Fetched(
        {c: pts[c] for c in codes}, {c: (pts[c][0][0], today) for c in codes}, len(codes)))
    monkeypatch.setenv("ECOS_RATE_WRITE", "1")
    assert S.main(["--series", "C", "--today", "20261102"]) == 0
    assert "유지보수" not in capsys.readouterr().out                            # 2일 — 안 한다
    assert S.main(["--series", "C", "--today", "20261101"]) == 0
    assert "유지보수: 표+색인" in capsys.readouterr().out                       # 1일 — 한다
    assert S.main(["--series", "C", "--today", "20261103", "--maintain"]) == 0
    assert "유지보수: 표+색인" in capsys.readouterr().out
    assert S.main(["--series", "C", "--today", "20261101", "--dry-run"]) == 0   # 쓰지 않는 날엔 유지보수도 없다
    assert "유지보수" not in capsys.readouterr().out


def test_prune_cli_with_manifest_deletes_only_outside_window_and_keeps_carry_in(db, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("DATABASE_URL", db)
    monkeypatch.setenv("ECOS_RATE_WRITE", "1")
    with psycopg.connect(db, autocommit=True) as c:
        with c.cursor() as cur:
            cur.executemany('INSERT INTO ecos_rate VALUES (%s::"char", %s, %s::real)',
                            [("C", D("20230901") + timedelta(days=i), 3.5) for i in range(6)]        # 창 앞 6행 (마지막 20230906 은 carry-in)
                            + [("C", D("20231009") + timedelta(days=i), 3.6) for i in range(30)])    # 창 안 30행
    pub = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    mf = tmp_path / "m.json"
    mf.write_bytes(_manifest(max_dd="20231006", updated=pub))
    assert S.main(["--today", "20261021", "--prune", "--manifest", "file://" + str(mf)]) == 0
    out = capsys.readouterr().out
    assert "창 밖 행 5 / 표 36 → 지웠다" in out and "유지보수: 표+색인" in out
    with psycopg.connect(db) as c:
        ds = [r[0] for r in c.execute("SELECT d FROM ecos_rate WHERE series='C' ORDER BY d")]
    assert ds[0] == D("20230906") and len(ds) == 31
    # 발행 직후 목록은 거절
    mf.write_bytes(_manifest(max_dd="20231006", updated=(datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()))
    assert S.main(["--today", "20261021", "--prune", "--manifest", "file://" + str(mf)]) == 1       # 지울 행이 0이어도 가드가 먼저다
    assert "72시간" in capsys.readouterr().out


def test_table_stays_small_at_window_size(con):
    """3년 창 규모(계열 4 × 약 748거래일 + 기준금리 변경점)에서 표+색인이 0.35 MiB 를 넘지 않는다 — 행당 약 85 B 가정의 회귀 시험."""
    d0 = D("20230101")
    days = [d0 + timedelta(days=i) for i in range(1100) if (d0 + timedelta(days=i)).weekday() < 5][:748]
    rows = [(s.code, d, 2.0 + (i % 200) / 100) for s in (C, K, Y1, Y3) for i, d in enumerate(days)]
    rows += [("B", D(x), 3.0) for x in ("20230113", "20241011", "20241128", "20250225", "20250529", "20260716", "20260827")]
    with con.cursor() as cur:
        cur.executemany('INSERT INTO ecos_rate VALUES (%s::"char", %s, %s::real)', rows)
    con.execute("VACUUM ecos_rate")
    size = con.execute("SELECT pg_total_relation_size('ecos_rate')").fetchone()[0]
    assert len(rows) == 748 * 4 + 7
    assert size < 0.35 * 1048576, size
