from datetime import date, datetime, timedelta
import json
from decimal import Decimal
from unittest.mock import Mock

import psycopg
import pytest

from open_proxy_mcp import fwd_window
from scripts import drain_backlog_check as checker
from scripts.drain_backlog_check import _snapshot_week_stats


NOW = datetime(2025, 1, 15, 12, tzinfo=checker.KST)
THIS_WEEK = checker._week_start(NOW)
# Deliberately synthetic; no real connection information or usage data in fixtures.
TEST_DATABASE_URL = "postgresql://fixture-user:fixture-secret@db.invalid/fixture-db"


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz)


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return self.rows


class FakeConnection:
    """Read-only synthetic DB boundary; unknown SQL is always a test failure."""

    def __init__(self, *, events=None, size_mb=100, fail_on=None,
                 calendar=(), stale_versions=(0, None), edge=None):
        self.events = [checker._to_ns(NOW)] if events is None else events
        self.size_mb = Decimal(str(size_mb))
        # calendar = 게시 달력 날짜(`fwd_asof`, 구간형 저장 — 뷰가 이 달력을 읽는다).
        self.calendar = list(calendar)
        self.stale_versions = stale_versions
        # edge = 창 첫날 (n_fact, n_stock, 그 날 판 수, 그 날 시세 행 수). None 이면 쓰는 쪽 칸이 없는 DB(검사를 건너뛴다).
        self.edge = edge
        self.edge_day = None
        self.fail_on = fail_on
        self.calls = []
        self.autocommit = False
        self.closed = False

    def execute(self, query, params=None):
        query = " ".join(query.split())
        self.calls.append((query, params))
        assert query.startswith("SELECT "), "Checker must never mutate the DB"
        if self.fail_on and self.fail_on in query:
            raise psycopg.OperationalError(f"synthetic failure {TEST_DATABASE_URL}")
        if query == "SELECT min(ts_ns), max(ts_ns), count(*) FROM ops_tool_calls":
            return FakeResult([(min(self.events, default=None),
                                max(self.events, default=None), len(self.events))])
        if query == "SELECT count(*) FROM ops_tool_calls WHERE ts_ns >= %s AND ts_ns < %s":
            start, end = params
            return FakeResult([(sum(start <= ts < end for ts in self.events),)])
        if query == "SELECT count(*) FROM ops_tool_calls WHERE ts_ns >= %s":
            return FakeResult([(sum(ts >= params[0] for ts in self.events),)])
        if query == "SELECT pg_database_size(current_database())/1024.0/1024":
            return FakeResult([(self.size_mb,)])
        if query == "SELECT to_regclass(%s) IS NOT NULL" and params[0] == "public.fwd_fact_ver":
            return FakeResult([(True,)])
        if query == "SELECT as_of FROM fwd_asof ORDER BY 1":
            return FakeResult([(day,) for day in sorted(self.calendar)])
        if query == "SELECT count(*), min(valid_to) FROM fwd_fact_ver WHERE valid_to <= %s":
            self.grace = params[0]
            return FakeResult([self.stale_versions])
        if query == " ".join(checker.EDGE_READY_SQL.split()):
            return FakeResult([(self.edge is not None,)])
        if query == " ".join(checker.EDGE_SQL.split()) and self.edge is not None:
            self.edge_day = params[0]
            return FakeResult([self.edge])
        if "FROM pg_stat_user_tables JOIN pg_statio_user_tables" in query:
            return FakeResult([("synthetic_table", 1024 * 1024, 7)])
        raise AssertionError(f"Unexpected SQL: {query}")

    def close(self):
        self.closed = True


@pytest.fixture
def harness(monkeypatch, tmp_path):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(checker, "datetime", FrozenDateTime)
    monkeypatch.setattr("sys.argv", ["drain_backlog_check.py"])
    connect = Mock(side_effect=AssertionError("Live DB access is forbidden in tests"))
    monkeypatch.setattr(psycopg, "connect", connect)
    return connect, summary


def _weekly_dates(count):
    return [(THIS_WEEK - timedelta(weeks=weeks)).date() for weeks in range(count)]


def test_snapshot_week_stats_counts_same_week_dates_as_duplicates():
    weeks, duplicates = _snapshot_week_stats([
        date(2026, 9, 12),
        date(2026, 9, 13),
        date(2026, 9, 19),
    ])

    assert weeks == 2
    assert duplicates == 1


def test_snapshot_week_stats_accepts_one_date_per_week():
    weeks, duplicates = _snapshot_week_stats([
        date(2026, 8, 28),
        date(2026, 9, 5),
        date(2026, 9, 12),
        date(2026, 9, 19),
    ])

    assert weeks == 4
    assert duplicates == 0


@pytest.mark.parametrize("kind", ["healthy", "action_needed", "execution_error", "missing_config"])
def test_json_status_distinguishes_complete_warning_from_failed_observation(harness, monkeypatch, capsys, kind):
    connect, _ = harness
    monkeypatch.setattr("sys.argv", ["drain_backlog_check.py", "--json"])
    connection = FakeConnection(size_mb=490 if kind == "action_needed" else 100)
    if kind == "execution_error":
        connect.side_effect = psycopg.OperationalError(f"synthetic failure {TEST_DATABASE_URL}")
    elif kind == "missing_config":
        monkeypatch.delenv("DATABASE_URL")
    else:
        connect.side_effect = None
        connect.return_value = connection
    result = checker.main()
    captured = capsys.readouterr()
    value = json.loads(captured.out)  # exactly one JSON; no human/Actions prefix
    expected = "execution_error" if kind == "missing_config" else kind
    assert value["status"] == expected
    assert value["version"] == 1
    assert result == (0 if kind == "healthy" else 1)
    assert "fixture-secret" not in captured.out + captured.err
    assert "db.invalid" not in captured.out + captured.err
    if kind == "missing_config":
        connect.assert_not_called()
    if kind in ("healthy", "action_needed"):
        assert connection.closed
        assert connection.calls
        assert "DB " in value["report"]


def test_missing_config_is_checker_error(harness, monkeypatch, capsys):
    connect, summary = harness
    monkeypatch.delenv("DATABASE_URL")

    assert checker.main() == 1

    connect.assert_not_called()
    output = capsys.readouterr()
    assert "상태: CHECK_ERROR" in output.out
    assert "DATABASE_URL 이 없다" in output.out
    assert "ATTENTION_NEEDED" not in output.out
    assert "CHECK_ERROR" in summary.read_text()


def test_connection_failure_is_redacted_checker_error(harness, capsys):
    connect, summary = harness
    connect.side_effect = psycopg.OperationalError(f"cannot connect to {TEST_DATABASE_URL}")

    assert checker.main() == 1

    output = capsys.readouterr()
    text = output.out + output.err + summary.read_text()
    assert "CHECK_ERROR" in text
    assert "OperationalError" in text
    assert "ATTENTION_NEEDED" not in text
    assert "Traceback" not in text
    for private_part in (TEST_DATABASE_URL, "fixture-user", "fixture-secret", "db.invalid"):
        assert private_part not in text
    connect.assert_called_once_with(TEST_DATABASE_URL, connect_timeout=15)


@pytest.mark.parametrize("fail_on", [
    "SELECT min", "pg_database_size", "to_regclass", "FROM fwd_asof",
    "FROM pg_stat_user_tables",
])
def test_query_failure_is_redacted_and_connection_closed(harness, monkeypatch, capsys, fail_on):
    connect, summary = harness
    con = FakeConnection(calendar=_weekly_dates(1), fail_on=fail_on)
    connect.side_effect = None
    connect.return_value = con
    monkeypatch.setattr("sys.argv", ["drain_backlog_check.py", "--tables"])

    assert checker.main() == 1

    assert con.closed
    output = capsys.readouterr()
    text = output.out + output.err + summary.read_text()
    assert "CHECK_ERROR" in text
    assert "ATTENTION_NEEDED" not in text
    assert "Traceback" not in text
    assert "fixture-secret" not in text
    assert "db.invalid" not in text


@pytest.mark.parametrize("empty", [False, True])
def test_normal_and_empty_events_still_check_guardrails(harness, capsys, empty):
    connect, summary = harness
    con = FakeConnection(events=[] if empty else None, calendar=_weekly_dates(13))
    connect.side_effect = None
    connect.return_value = con

    assert checker.main() == 0

    assert con.closed
    assert con.autocommit is True
    queries = [sql for sql, _ in con.calls]
    assert any("pg_database_size" in sql for sql in queries)
    assert "SELECT as_of FROM fwd_asof ORDER BY 1" in queries
    assert "상태: OK" in capsys.readouterr().out
    assert "상태: OK" in summary.read_text()


@pytest.mark.parametrize(("max_weeks", "expected"), [(0, 1), (1, 0)])
def test_backlog_threshold_and_current_week_exclusion(harness, monkeypatch, capsys, max_weeks, expected):
    connect, summary = harness
    con = FakeConnection(events=[checker._to_ns(NOW), checker._to_ns(THIS_WEEK - timedelta(days=1))])
    connect.side_effect = None
    connect.return_value = con
    monkeypatch.setattr("sys.argv", ["drain_backlog_check.py", "--max-weeks", str(max_weeks)])

    assert checker.main() == expected

    output = capsys.readouterr().out
    assert "밀린 완결 주 1개 · 1건" in output
    assert "1행 — 드레인 대상 아님" in output
    status = "ATTENTION_NEEDED" if expected else "OK"
    assert f"상태: {status}" in output
    assert f"상태: {status}" in summary.read_text()
    assert "CHECK_ERROR" not in output
    if expected:
        assert "완결 주 1개가 밀렸다" in summary.read_text()


@pytest.mark.parametrize(("size_mb", "warn_pct", "expected"), [
    (449, 90, 0), (450, 90, 1), (451, 90, 1), (450, 91, 0),
])
def test_capacity_boundary_with_empty_events(harness, monkeypatch, capsys, size_mb, warn_pct, expected):
    connect, summary = harness
    con = FakeConnection(events=[], size_mb=size_mb)
    connect.side_effect = None
    connect.return_value = con
    monkeypatch.setattr("sys.argv", ["drain_backlog_check.py", "--warn-pct", str(warn_pct)])

    assert checker.main() == expected

    output = capsys.readouterr().out
    status = "ATTENTION_NEEDED" if expected else "OK"
    assert f"상태: {status}" in output
    assert f"상태: {status}" in summary.read_text()
    if expected:
        assert "드레인으로 회수할 공간이 없다" in output
        assert "events_drain.py --apply" not in output


def test_tables_flag_preserves_read_only_breakdown(harness, monkeypatch, capsys):
    connect, _ = harness
    con = FakeConnection(events=[])
    connect.side_effect = None
    connect.return_value = con
    monkeypatch.setattr("sys.argv", ["drain_backlog_check.py", "--tables"])

    assert checker.main() == 0

    assert "synthetic_table" in capsys.readouterr().out
    assert con.closed


@pytest.mark.parametrize(("size_mb", "expected"), [(100, 0), (450, 1)])
def test_summary_write_failure_does_not_change_result(harness, monkeypatch, tmp_path, capsys, size_mb, expected):
    connect, _ = harness
    con = FakeConnection(size_mb=size_mb)
    connect.side_effect = None
    connect.return_value = con
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "missing" / "summary.md"))

    assert checker.main() == expected

    output = capsys.readouterr()
    assert "작업 요약 기록 실패" in output.err
    assert "CHECK_ERROR" not in output.out
    assert str(tmp_path) not in output.err


def test_local_output_without_github_env(harness, monkeypatch, capsys):
    connect, summary = harness
    connect.side_effect = None
    connect.return_value = FakeConnection(size_mb=450)
    monkeypatch.delenv("GITHUB_ACTIONS")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY")

    assert checker.main() == 1

    output = capsys.readouterr().out
    assert "상태: ATTENTION_NEEDED" in output
    assert "::warning" not in output
    assert not summary.exists()


def test_summary_is_appended_without_overwriting_existing_content(harness):
    connect, summary = harness
    connect.side_effect = None
    connect.return_value = FakeConnection()
    summary.write_text("Earlier step summary\n")

    assert checker.main() == 0

    text = summary.read_text()
    assert text.startswith("Earlier step summary\n")
    assert "상태: OK" in text


def test_github_annotation_distinguishes_attention_from_error(harness, capsys):
    connect, _ = harness
    connect.side_effect = None
    connect.return_value = FakeConnection(size_mb=450)
    assert checker.main() == 1
    output = capsys.readouterr().out
    assert "::warning title=drain-backlog ATTENTION_NEEDED::" in output
    assert "::error" not in output

    connect.side_effect = psycopg.OperationalError("synthetic failure")
    assert checker.main() == 1
    output = capsys.readouterr().out
    assert "::error title=drain-backlog CHECK_ERROR::" in output
    assert "::warning" not in output


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# 261018 — 구간형 저장 뒤(달력 모양): fwd·fwd_hist 가 같은 게시 달력을 읽고 창 FWD_HOT_WEEKS(기본 52주)를 담는다
# ═══════════════════════════════════════════════════════════════════════════════════════════════
def test_calendar_shape_allows_the_full_window(harness, capsys):
    """달력 모양: 4주·13주가 아니라 창 52주(+양 끝 1주)가 상한이다 — 52주를 담는 것이 정상이다."""
    connect, _ = harness
    con = FakeConnection(events=[], calendar=_weekly_dates(53))
    connect.side_effect = None
    connect.return_value = con

    assert checker.main() == 0

    output = capsys.readouterr().out
    assert "게시 달력 53벌 · 53주" in output and "창 52주(양 끝 포함 53주까지)" in output
    assert "상태: OK" in output
    queries = [sql for sql, _ in con.calls]
    assert "SELECT as_of FROM fwd_asof ORDER BY 1" in queries                  # 뷰 DISTINCT 대신 달력
    assert con.grace == _weekly_dates(1)[0] - timedelta(days=52 * 7 + 7)     # 창 + 1주 늦음까지 봐 준다


def test_calendar_shape_flags_a_calendar_longer_than_the_window(harness, capsys):
    connect, summary = harness
    con = FakeConnection(events=[], calendar=_weekly_dates(54))
    connect.side_effect = None
    connect.return_value = con

    assert checker.main() == 1

    output = capsys.readouterr().out
    assert "게시 달력 54주가 창(허용 53주)보다 길다" in output and "창 정리" in output
    assert "5주차를 이력으로" not in output                                   # 주별 모양의 조치를 안내하지 않는다
    assert "ATTENTION_NEEDED" in summary.read_text()


def test_calendar_shape_flags_closed_versions_left_outside_the_window(harness, capsys):
    connect, _ = harness
    con = FakeConnection(events=[], calendar=_weekly_dates(10), stale_versions=(1234, date(2024, 1, 6)))
    connect.side_effect = None
    connect.return_value = con

    assert checker.main() == 1

    output = capsys.readouterr().out
    assert "창 밖 닫힌 판 1,234개가 1주 넘게 남았다" in output


def test_calendar_shape_flags_same_week_duplicates(harness, capsys):
    connect, _ = harness
    con = FakeConnection(events=[], calendar=[THIS_WEEK.date(), (THIS_WEEK + timedelta(days=1)).date()])
    connect.side_effect = None
    connect.return_value = con

    assert checker.main() == 1

    output = capsys.readouterr().out
    assert "게시 달력 같은 ISO 주에 날짜가 1개 더 있다" in output and "주 1벌 계약" in output


def test_calendar_window_follows_the_setting(harness, monkeypatch, capsys):
    """창은 설정값(`FWD_HOT_WEEKS`) — 예산 규칙으로 26주로 줄이면 감시도 27주를 상한으로 본다."""
    connect, _ = harness
    monkeypatch.setenv("FWD_HOT_WEEKS", "26")
    con = FakeConnection(events=[], calendar=_weekly_dates(28))
    connect.side_effect = None
    connect.return_value = con

    assert checker.main() == 1

    assert "게시 달력 28주가 창(허용 27주)보다 길다" in capsys.readouterr().out


def test_workflow_no_longer_pins_the_legacy_limits():
    """주간 감시 워크플로가 4·13 을 박아 두면 교체 뒤 52주를 담는 정상 상태가 매주 실패한다 — 모양별 기본값을 쓴다."""
    from pathlib import Path
    wf = (Path(__file__).resolve().parents[1] / ".github/workflows/drain-backlog-weekly.yml").read_text(encoding="utf-8")
    run = [ln for ln in wf.splitlines() if "drain_backlog_check.py" in ln and "run:" in ln]
    assert run and all("--fwd-max-weeks" not in ln and "--hist-max-weeks" not in ln for ln in run)


def test_calendar_shape_checks_that_the_first_day_of_the_window_is_whole(harness, monkeypatch, capsys):
    """창 첫날(읽는 쪽이 읽는 가장 이른 날 — 52w 리비전 기준일)이 한 벌이면 정상이고, 그 날을 고른다."""
    connect, _ = harness
    monkeypatch.setenv("FWD_HOT_WEEKS", "4")
    cal = _weekly_dates(5)                                    # 창 4주 + 양 끝 = 5벌
    con = FakeConnection(events=[], calendar=cal, edge=(30708, 2764, 30708, 2764))
    connect.side_effect = None
    connect.return_value = con

    assert checker.main() == 0

    output = capsys.readouterr().out
    assert con.edge_day == min(cal) == max(cal) - timedelta(days=28)
    assert f"창 첫날 {min(cal)} — 판 30,708/30,708 · 시세 2,764/2,764" in output and "상태: OK" in output


@pytest.mark.parametrize("edge", [(30708, 2764, 29001, 2764), (30708, 2764, 30708, 2700)])
def test_calendar_shape_flags_a_partial_first_day_of_the_window(harness, monkeypatch, capsys, edge):
    """쓰는 쪽 창이 더 짧거나 창 정리가 경계 날짜의 판·시세를 깎으면 그 날은 일부 종목만 보인다 — 조용히 틀린 답이라 알린다.
    달력 길이·창 밖 닫힌 판 검사로는 안 잡힌다(달력은 창 안이고, 지워진 판은 이미 없다)."""
    connect, _ = harness
    con = FakeConnection(events=[], calendar=_weekly_dates(53), edge=edge)
    connect.side_effect = None
    connect.return_value = con

    assert checker.main() == 1

    output = capsys.readouterr().out
    assert con.edge_day == min(_weekly_dates(53))
    assert "이 한 벌이 아니다" in output and "52w 리비전 기준일" in output
    assert "FWD_HOT_WEEKS" in output and "창 경계 날짜" in output             # 조치: 두 쪽 창 길이·경계 날짜


def test_calendar_shape_skips_the_first_day_check_without_the_writer_columns(harness, capsys):
    """게시 달력에 n_fact·n_stock(또는 시세 표)이 없는 DB 는 검사를 건너뛴다 — 없는 칸을 질의해 검사가 죽지 않게."""
    connect, _ = harness
    con = FakeConnection(events=[], calendar=_weekly_dates(10))
    connect.side_effect = None
    connect.return_value = con

    assert checker.main() == 0

    assert con.edge_day is None and "창 첫날" not in capsys.readouterr().out
