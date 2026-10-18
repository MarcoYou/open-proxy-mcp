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

저장 위치는 private(`open-proxy-storage/usage/`) — 사용 통계는 공개 대상이 아니다. 저장소 경로는
드레인 백업과 같은 곳(`OPM_STORAGE_REPO`, 없으면 이 저장소의 형제 `open-proxy-storage`)에서 정한다.
원문 API 키는 어디에도 저장하지 않는다(events 자체가 SHA-256 해시만 담는다).

**과거는 드레인 백업에서 읽는다.** `ops_tool_calls` 에는 진행 중인 주만 남으므로 DB 만 세면
그 주에 안 온 사용자가 빠지고 호출 수·활성일이 1주 치가 된다 — `history_rows` 참조.

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


_DAY_NS = 86_400 * 10**9


def summarize(rows) -> list[tuple]:
    """(ts_ns, key_hash, tool) 행 → 키별 (key_hash, 최초 ts, 최근 ts, 호출 수, 활성일 수, 도구 종류 수).

    종전 SQL(`GROUP BY key_hash` · 활성일 = `ts_ns / 86400e9` 정수 나눗셈 · 도구 = NULL 아닌 것의
    DISTINCT · 최초 관측 순)과 **같은 정의**를 파이썬으로 옮긴 것이다 — 드레인 백업은 DB 밖에 있어
    SQL 한 문장으로는 못 센다.
    """
    acc: dict[str, list] = {}
    for ts, key_hash, tool in rows:
        ts = int(ts)
        a = acc.get(key_hash)
        if a is None:
            a = acc[key_hash] = [ts, ts, 0, set(), set()]
        a[0] = min(a[0], ts)
        a[1] = max(a[1], ts)
        a[2] += 1
        a[3].add(ts // _DAY_NS)
        if tool is not None:
            a[4].add(tool)
    out = [(k, a[0], a[1], a[2], len(a[3]), len(a[4])) for k, a in acc.items()]
    out.sort(key=lambda r: (r[1], r[0]))
    return out


def history_rows(conn) -> list[tuple]:
    """(ts_ns, key_hash, tool) — **DB + 드레인 백업** 전 기간.

    `ops_tool_calls` 는 주간 드레인으로 진행 중인 주(약 7일)만 남는다. DB 만 세면 그 주에
    안 온 사용자가 등록부에서 **통째로 빠지고**, 남은 사용자의 호출 수·활성일·최근 관측이
    1주 치로 줄어든다. 백업은 `usage_tracker.merge_drained` 로 합친다 — 같은 event_id 는
    한 번만 세고, 개명된 도구 이름은 현재 이름으로 접힌다(통계와 한 정의).
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import usage_tracker

    db = [(int(t), h, tool) for t, h, tool in
          conn.execute("SELECT ts_ns, key_hash, tool FROM ops_tool_calls").fetchall()]
    return usage_tracker.merge_drained(db, ("ts_ns", "key_hash", "tool"))


def merge_registry(rows, existing: dict[str, dict[str, str]]) -> list[dict[str, object]]:
    """요약 행 + 기존 등록부 → 새 등록부(user_id 순).

    · 이미 있는 키는 `user_id`·`first_seen_kst` 를 그대로 물려받는다.
    · **이번 기록에 안 보이는 기존 키도 지우지 않는다** — 지우면 그 번호가 비고, 가장 큰 번호가
      빠지면 다음 실행이 같은 번호를 **다른 사람에게** 준다.
    · 기존보다 호출 수가 줄어든 키가 있으면 **쓰지 않고 멈춘다.** 기록이 쌓이기만 하는 장부에서
      수가 줄었다면 과거 일부를 못 읽은 것(백업 경로·클론 상태)이다 — 덮어쓰면 그 손실이 굳는다.
    """
    shrunk = [(k, r["calls"], c) for k, _, _, c, _, _ in rows
              if (r := existing.get(k)) and str(r.get("calls", "")).isdigit() and c < int(r["calls"])]
    if shrunk:
        k, before, now = shrunk[0]
        sys.exit(f"호출 수가 기존 등록부보다 줄어든 키가 {len(shrunk)}개다(예: {k[:10]}… {before} → {now}) — "
                 "과거 기록 일부를 못 읽었다. 드레인 백업 경로(OPM_STORAGE_REPO)·클론 상태를 확인. 쓰지 않음.")
    number = _next_number(existing)
    registry: list[dict[str, object]] = []
    seen = set()
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
        seen.add(key_hash)
    for key_hash, prior in existing.items():
        if key_hash not in seen:
            kept = {f: prior.get(f, "") for f in FIELDS}
            for f in ("active_days", "calls", "distinct_tools"):   # CSV 에서 온 값 — 아래 집계가 숫자로 비교한다
                if str(kept[f]).isdigit():
                    kept[f] = int(kept[f])
            registry.append(kept)
    registry.sort(key=lambda r: r["user_id"])
    return registry


def build(out: pathlib.Path) -> list[dict[str, object]]:
    import psycopg

    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL 이 없다 — `set -a; source .env` 후 다시 실행.")
    with psycopg.connect(url, connect_timeout=10) as conn:
        rows = summarize(history_rows(conn))
    registry = merge_registry(rows, _load_existing(out))
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(registry)
    return registry


def default_out() -> pathlib.Path:
    """드레인 백업(`usage/events/`)과 **같은 저장소**의 `usage/user_registry.csv`.

    경로는 `events_drain.STORAGE_REPO` 한 곳에서 정한다. 종전 기본값은 홈 아래 고정 경로라,
    저장소를 다른 곳에 클론한 기기에서는 기존 등록부를 못 찾고 **빈 등록부를 새로 만들어**
    번호를 처음부터 다시 매기게 된다(한 번 준 ID 는 바꾸지 않는다는 약속이 깨진다).
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from events_drain import STORAGE_REPO
    return STORAGE_REPO / "usage" / "user_registry.csv"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=pathlib.Path, default=None,
                        help="기본: 드레인 백업과 같은 저장소의 usage/user_registry.csv")
    args = parser.parse_args()
    args.out = args.out or default_out()
    before = len(_load_existing(args.out))
    registry = build(args.out)
    new = len(registry) - before
    print(f"사용자 {len(registry)}명 (신규 {new}명) → {args.out}")
    print(f"  1회성 {sum(1 for r in registry if r['calls'] == 1)}명 · "
          f"활성일 2일+ {sum(1 for r in registry if r['active_days'] >= 2)}명")


if __name__ == "__main__":
    main()
