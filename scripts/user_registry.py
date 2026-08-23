#!/usr/bin/env python3
"""사용자 레지스트리 — `key_hash` 에 고정 ID 를 붙이고 최초·최근 관측을 남긴다.

`ops_tool_calls` 는 요청 단위라 「누가 언제부터 쓰는가」를 매번 다시 세야 한다.
이 스크립트는 그것을 한 줄 = 한 사용자로 굳힌다. **ID 는 한 번 준 것을 바꾸지 않는다** —
기존 CSV 를 읽어 이미 있는 `key_hash` 의 `user_id` 와 `first_seen` 을 그대로 물려주고,
새 `key_hash` 에만 다음 번호를 준다(중간 사용자가 사라져도 뒤 번호가 당겨지지 않는다).

**운영 전제(260806 결정): 키 하나 = 사람 한 명으로 센다.** 키 밖의 식별자가 없으므로
이 이상 좁힐 수 없고, 코호트·리텐션·활성 사용자 지표는 이 전제로 성립한다. 다만 아래 둘은
전제가 어긋나는 자리라 수치를 읽을 때 함께 봐야 한다.

1. **키 재발급은 「이탈 1명 + 신규 1명」으로 잡힌다.** DART 는 한 사람이 키를 여러 개
   발급받을 수 있고 OPM 자신도 `OPENDART_API_KEY_2`·`_3` 를 쓴다. 그래서 사용자 수는
   실제 인원의 **상한**이다("최대 N명").
2. **`first_seen` 은 가입일이 아니라 「우리 로그의 첫 관측」이다.** 수집은 2026-06-29 에
   시작했으므로 그날의 신규 61명은 대부분 그 전부터 쓰던 사람이다.

저장 위치는 private(`open-proxy-storage/usage/`) — 사용 통계는 공개 대상이 아니다.
원문 API 키는 어디에도 저장하지 않는다(events 자체가 SHA-256 해시만 담는다).

    python3 scripts/user_registry.py [--out <path>]
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import pathlib
import sys

KST = dt.timezone(dt.timedelta(hours=9))
DEFAULT_OUT = pathlib.Path.home() / "Projects/open-proxy-storage/usage/user_registry.csv"
FIELDS = [
    "user_id", "key_hash", "first_seen_kst", "last_seen_kst",
    "active_days", "calls", "distinct_tools",
]


def _kst(ts_ns: int) -> str:
    return dt.datetime.fromtimestamp(ts_ns / 1e9, KST).isoformat(timespec="seconds")


def _load_existing(path: pathlib.Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(newline="") as f:
        return {row["key_hash"]: row for row in csv.DictReader(f)}


def _next_number(existing: dict[str, dict[str, str]]) -> int:
    used = [int(r["user_id"][1:]) for r in existing.values() if r["user_id"][1:].isdigit()]
    return max(used, default=0) + 1


def build(out: pathlib.Path) -> list[dict[str, object]]:
    import psycopg

    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL 이 없다 — `set -a; source .env` 후 다시 실행.")
    with psycopg.connect(url, connect_timeout=10) as conn:
        rows = conn.execute(
            """
            SELECT key_hash, MIN(ts_ns), MAX(ts_ns), COUNT(*),
                   COUNT(DISTINCT (ts_ns / 86400000000000)),
                   COUNT(DISTINCT tool) FILTER (WHERE tool IS NOT NULL)
            FROM ops_tool_calls
            GROUP BY key_hash
            ORDER BY MIN(ts_ns), key_hash
            """
        ).fetchall()

    existing = _load_existing(out)
    number = _next_number(existing)
    registry: list[dict[str, object]] = []
    for key_hash, first, last, calls, active_days, tools in rows:
        prior = existing.get(key_hash)
        if prior:
            user_id, first_seen = prior["user_id"], prior["first_seen_kst"]
        else:
            user_id, first_seen = f"u{number:04d}", _kst(first)
            number += 1
        registry.append({
            "user_id": user_id, "key_hash": key_hash,
            "first_seen_kst": first_seen, "last_seen_kst": _kst(last),
            "active_days": active_days, "calls": calls, "distinct_tools": tools,
        })
    registry.sort(key=lambda r: r["user_id"])
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(registry)
    return registry


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    before = len(_load_existing(args.out))
    registry = build(args.out)
    new = len(registry) - before
    print(f"사용자 {len(registry)}명 (신규 {new}명) → {args.out}")
    print(f"  1회성 {sum(1 for r in registry if r['calls'] == 1)}명 · "
          f"활성일 2일+ {sum(1 for r in registry if r['active_days'] >= 2)}명")


if __name__ == "__main__":
    main()
