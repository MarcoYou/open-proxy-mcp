from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock

import psycopg
import pytest

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

    def __init__(self, *, events=None, size_mb=100, fwd=None, hist=None, fail_on=None):
        self.events = [checker._to_ns(NOW)] if events is None else events
        self.size_mb = Decimal(str(size_mb))
        self.snapshots = {"fwd": fwd, "fwd_hist": hist}
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
        if query == "SELECT to_regclass(%s) IS NOT NULL":
            return FakeResult([(self.snapshots[params[0].split(".")[-1]] is not None,)])
        for table, days in self.snapshots.items():
            if query == f"SELECT DISTINCT as_of FROM {table} ORDER BY 1":
                return FakeResult([(day,) for day in sorted(set(days))])
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
    "SELECT min", "pg_database_size", "to_regclass", "SELECT DISTINCT as_of",
    "FROM pg_stat_user_tables",
])
def test_query_failure_is_redacted_and_connection_closed(harness, monkeypatch, capsys, fail_on):
    connect, summary = harness
    con = FakeConnection(fwd=_weekly_dates(1), fail_on=fail_on)
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
    con = FakeConnection(events=[] if empty else None, fwd=_weekly_dates(4), hist=_weekly_dates(13))
    connect.side_effect = None
    connect.return_value = con

    assert checker.main() == 0

    assert con.closed
    assert con.autocommit is True
    queries = [sql for sql, _ in con.calls]
    assert any("pg_database_size" in sql for sql in queries)
    assert "SELECT DISTINCT as_of FROM fwd ORDER BY 1" in queries
    assert "SELECT DISTINCT as_of FROM fwd_hist ORDER BY 1" in queries
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


@pytest.mark.parametrize(("table", "count", "limit", "expected"), [
    ("fwd", 4, 4, 0), ("fwd", 5, 4, 1), ("fwd", 5, 5, 0),
    ("hist", 13, 13, 0), ("hist", 14, 13, 1), ("hist", 14, 14, 0),
])
def test_retention_boundary_with_empty_events(harness, monkeypatch, capsys, table, count, limit, expected):
    connect, summary = harness
    con = FakeConnection(events=[], **{table: _weekly_dates(count)})
    connect.side_effect = None
    connect.return_value = con
    monkeypatch.setattr("sys.argv", ["drain_backlog_check.py", f"--{table}-max-weeks", str(limit)])

    assert checker.main() == expected

    status = "ATTENTION_NEEDED" if expected else "OK"
    assert f"상태: {status}" in capsys.readouterr().out
    assert f"상태: {status}" in summary.read_text()


@pytest.mark.parametrize("table", ["fwd", "hist"])
def test_same_week_duplicates_warn_with_empty_events(harness, capsys, table):
    connect, summary = harness
    con = FakeConnection(events=[], **{table: [THIS_WEEK.date(), (THIS_WEEK + timedelta(days=1)).date()]})
    connect.side_effect = None
    connect.return_value = con

    assert checker.main() == 1

    output = capsys.readouterr().out
    assert "ATTENTION_NEEDED" in output
    assert "날짜가 1개 더 있다" in output
    assert "CHECK_ERROR" not in summary.read_text()


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
