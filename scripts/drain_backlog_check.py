#!/usr/bin/env python3
"""드레인 밀림 감시 — **잊히는 것이 이 절차의 유일한 고장 모드다.**

260817 실측: `events_drain.py` 는 260810 에 딱 한 번 돌고 잊혀, 완결 주 **7주 · 357,734건**이
밀린 채 무료티어 71% 까지 찼다. 수동 절차는 잊히고, **잊힌 절차는 없는 것과 같다.**

그래서 지우는 것을 자동화하는 대신 **밀린 것을 매주 알려 준다.**
왜 삭제까지 자동화하지 않나:
  · 산출 CSV 는 **private 레포**에 들어간다. public 레포의 CI 가 거기 쓰려면 교차 레포
    쓰기 토큰을 public 워크플로 시크릿으로 둬야 한다 — 이건 결정이어야지 부수효과여선 안 된다.
  · `--apply` 는 되돌릴 수 없는 삭제다. 백업 커밋이 **사람 손으로** 확인된 뒤에 도는 게 맞다.
감시는 **읽기만** 한다(SELECT + 용량 조회). 지우지도, 쓰지도 않는다.

실행:  python3 scripts/drain_backlog_check.py [--max-weeks N] [--warn-pct P]
종료코드: 0 정상 · 1 조치 필요(밀린 주 초과 또는 용량 경고)
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

# 주 계산·시간대는 **드레인 본체의 것을 그대로 쓴다.** 사본을 두면 감시가 실제 대상과
# 다른 주를 세게 되고, 그때 감시는 있으나 마나가 된다.
from events_drain import KST, _kst, _to_ns, _week_start  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-weeks", type=int, default=1,
                    help="이만큼 넘게 밀리면 실패 처리 (기본 1 — 한 주만 밀려도 바로 알린다)")
    ap.add_argument("--warn-pct", type=int, default=70, help="무료티어 경고선 %%")
    a = ap.parse_args()

    url = os.getenv("DATABASE_URL")
    if not url:
        print("DATABASE_URL 이 없다 — 감시할 대상이 없다.", file=sys.stderr)
        return 1

    import psycopg

    con = psycopg.connect(url, connect_timeout=15)
    con.autocommit = True
    try:
        row = con.execute("SELECT min(ts_ns), max(ts_ns), count(*) FROM ops_tool_calls").fetchone()
        mn, mx, n = row
        if not n:
            print("events 가 비어 있다 — 밀린 것 없음.")
            return 0

        now_week = _week_start(datetime.now(tz=KST))
        w, weeks = _week_start(_kst(mn)), []
        while w < now_week:
            end = w + timedelta(days=7)
            c = con.execute(
                "SELECT count(*) FROM ops_tool_calls WHERE ts_ns >= %s AND ts_ns < %s",
                (_to_ns(w), _to_ns(end))).fetchone()[0]
            if c:
                weeks.append((w.date(), (end - timedelta(days=1)).date(), c))
            w = end

        size_mb = con.execute(
            "SELECT pg_database_size(current_database())/1024.0/1024").fetchone()[0]
        cur_week = con.execute(
            "SELECT count(*) FROM ops_tool_calls WHERE ts_ns >= %s", (_to_ns(now_week),)
        ).fetchone()[0]
    finally:
        con.close()

    pct = 100 * size_mb / 500        # Supabase 무료티어 500MB
    print(f"events {n:,}행 · {_kst(mn).date()} ~ {_kst(mx).date()}")
    print(f"DB {size_mb:.0f}MB / 500MB ({pct:.0f}%)")
    print(f"진행 중인 주({now_week.date()}~) {cur_week:,}행 — 드레인 대상 아님")

    if weeks:
        print(f"\n밀린 완결 주 {len(weeks)}개 · {sum(c for *_, c in weeks):,}건")
        for s, e, c in weeks:
            print(f"  [{s}~{e}] {c:,}")
    else:
        print("\n밀린 완결 주 없음.")

    bad = []
    if len(weeks) > a.max_weeks:
        bad.append(f"완결 주 {len(weeks)}개가 밀렸다(허용 {a.max_weeks})")
    if pct >= a.warn_pct:
        bad.append(f"무료티어 {pct:.0f}% (경고선 {a.warn_pct}%)")
    if not bad:
        return 0

    print("\n⚠️  " + " · ".join(bad))
    print("""
조치 (private 레포 백업이 먼저다 — 지우는 쪽만 영속이고 남기는 쪽이 휘발이면 백업이 아니다):
  1) python3 scripts/events_drain.py                 # dry-run: CSV 만 쓴다
  2) open-proxy-storage 에서 usage/*.csv 커밋·푸시
  3) python3 scripts/events_drain.py --apply         # 검증 후 DELETE
  4) VACUUM FULL ops_tool_calls;                   # 여기까지 해야 용량이 실제로 돌아온다
자세히: private wiki-private/architecture/usage-telemetry-operations.md""")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
