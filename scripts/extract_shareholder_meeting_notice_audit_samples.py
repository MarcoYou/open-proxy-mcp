"""Extract shareholder_meeting_notice audit samples from notice scan CSV.

현재 보유한 meeting notice scan 자산에서 감사용 표본 파일을 분리한다.

Usage:
    uv run python scripts/extract_shareholder_meeting_notice_audit_samples.py
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "wiki/architecture/audits/data/260504_meeting_notices_scan.csv"
OUT_DIR = ROOT / "wiki/architecture/audits/data/260517_parsing_success_rate_audit"


def _read_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with SOURCE.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rcept_dt = (row.get("rcept_dt") or "").strip()
            if len(rcept_dt) != 8 or not rcept_dt.isdigit():
                continue
            rows.append(row)
    return rows


def _write(name: str, rows: list[dict[str, str]]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    fieldnames = ["corp_code", "corp_name", "stock_code", "rcept_no", "rcept_dt", "report_nm", "is_correction", "is_extraordinary"]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})
    print(path.name, len(rows))


def main() -> None:
    rows = _read_rows()
    today = int(date.today().strftime("%Y%m%d"))

    annual_2026 = [
        row for row in rows
        if row["rcept_dt"].startswith("2026")
        and int(row["rcept_dt"]) <= today
        and str(row.get("is_extraordinary", "")).lower() == "false"
    ]
    extraordinary_since_20260331 = [
        row for row in rows
        if int(row["rcept_dt"]) >= 20260331
        and int(row["rcept_dt"]) <= today
        and str(row.get("is_extraordinary", "")).lower() == "true"
    ]

    annual_2026.sort(key=lambda r: (r["rcept_dt"], r["rcept_no"]))
    extraordinary_since_20260331.sort(key=lambda r: (r["rcept_dt"], r["rcept_no"]))

    _write("shareholder_meeting_notice_annual_2026.csv", annual_2026)
    _write("shareholder_meeting_notice_extraordinary_since_20260331.csv", extraordinary_since_20260331)


if __name__ == "__main__":
    main()
