from datetime import date

from scripts.drain_backlog_check import _snapshot_week_stats


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
