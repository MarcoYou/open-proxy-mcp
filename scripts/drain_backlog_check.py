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

260914 확장: 용량 % 하나로는 부족했다. events 를 다 비워도 `fwd`(서빙용 스냅샷, 벌당 ≈37MB)와
`fwd_hist`(리비전 이력)가 DB 의 대부분이라, 보존 정책(fwd 최신 2주+pin · fwd_hist 13주)이
지켜지는지를 **불변식**으로 같이 본다. 새 fwd 한 벌을 먼저 올린 뒤 정리하므로 500MB 직전에서
알리면 늦다. 경고선은 90%다. fwd 정리는 private forward-collector 의
`prune_fwd.py --keep-weeks 2 --pin 2026-08-31`가, fwd_hist 는 `push_fwd_hist.py`의 롤링이 맡는다.

실행:  python3 scripts/drain_backlog_check.py [--max-weeks N] [--warn-pct P] [--tables]
                                              [--fwd-max-weeks N] [--hist-max-weeks N]
종료코드: 0 정상 · 1 조치 필요(밀린 주 초과 · fwd/fwd_hist 보존 주 초과 · 용량 경고)
--tables: 테이블별 용량·행수 상위 12개를 덧붙인다(옛 DB 용량 리포트 스크립트 흡수, 260902). 없으면 출력 동일.
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


def _snapshot_week_stats(days) -> tuple[int, int]:
    """(ISO 주 수, 같은 주에 더 들어간 날짜 수). 주간 표에 날짜가 둘이면 보존 누수다."""
    weeks = {d.isocalendar()[:2] for d in days}
    return len(weeks), len(days) - len(weeks)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-weeks", type=int, default=0,
                    help="허용할 밀린 완결 주 수 (기본 0 — 한 주만 밀려도 바로 알린다)")
    ap.add_argument("--warn-pct", type=int, default=90,
                    help="무료티어 경고선 %% (기본 90 — 새 fwd 한 벌의 일시 공간까지 남긴다)")
    ap.add_argument("--fwd-max-weeks", type=int, default=3,
                    help="fwd 최대 ISO 주 수 (최신 2주 + 별도 pin 1주)")
    ap.add_argument("--hist-max-weeks", type=int, default=13,
                    help="fwd_hist 의 as_of 가 이 ISO 주 수를 넘으면 실패 (13주 롤링)")
    ap.add_argument("--tables", action="store_true",
                    help="테이블별 용량 breakdown(100KB 초과 상위 12개)도 출력 — 읽기만 한다")
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
        # fwd·fwd_hist 는 as_of(date) 벌 단위다. 같은 주 재송출은 한 주로 센다 — prune_fwd 와 같은 단위.
        def _weeks(table: str) -> tuple[list, int, int]:
            if not con.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",)).fetchone()[0]:
                return [], 0, 0
            days = [r[0] for r in con.execute(f"SELECT DISTINCT as_of FROM {table} ORDER BY 1").fetchall()]
            weeks, duplicate_days = _snapshot_week_stats(days)
            return days, weeks, duplicate_days
        fwd_days, fwd_weeks, fwd_duplicate_days = _weeks("fwd")
        hist_days, hist_weeks, hist_duplicate_days = _weeks("fwd_hist")
        # --tables 일 때만 한 번 더 읽는다(옛 DB 용량 리포트와 같은 SQL). 기본 경로는 그대로.
        tables = con.execute("""
          SELECT relname, pg_total_relation_size(relid) b, n_live_tup
          FROM pg_stat_user_tables JOIN pg_statio_user_tables USING(relid, relname)
          WHERE pg_total_relation_size(relid) > 100*1024
          ORDER BY 2 DESC LIMIT 12""").fetchall() if a.tables else None
    finally:
        con.close()

    pct = 100 * size_mb / 500        # Supabase 무료티어 500MB
    print(f"events {n:,}행 · {_kst(mn).date()} ~ {_kst(mx).date()}")
    print(f"DB {size_mb:.0f}MB / 500MB ({pct:.0f}%)")
    if tables is not None:
        total_b = float(size_mb) * 1024 * 1024
        for name, b, live in tables:
            print(f"  {name:<24} {b/1024/1024:>7.1f} MB  ({b / total_b * 100:>4.1f}%)  {live:>10,}행")
    print(f"진행 중인 주({now_week.date()}~) {cur_week:,}행 — 드레인 대상 아님")
    if fwd_days:
        print(f"fwd {len(fwd_days)}벌 · {fwd_weeks}주 ({fwd_days[0]} ~ {fwd_days[-1]}) — 보존 한도 {a.fwd_max_weeks}주")
    if hist_days:
        print(f"fwd_hist {len(hist_days)}벌 · {hist_weeks}주 ({hist_days[0]} ~ {hist_days[-1]}) — 롤링 {a.hist_max_weeks}주")

    if weeks:
        print(f"\n밀린 완결 주 {len(weeks)}개 · {sum(c for *_, c in weeks):,}건")
        for s, e, c in weeks:
            print(f"  [{s}~{e}] {c:,}")
    else:
        print("\n밀린 완결 주 없음.")

    bad = []
    if len(weeks) > a.max_weeks:
        bad.append(f"완결 주 {len(weeks)}개가 밀렸다(허용 {a.max_weeks})")
    if fwd_weeks > a.fwd_max_weeks:
        bad.append(f"fwd 스냅샷 {fwd_weeks}주가 쌓였다(허용 {a.fwd_max_weeks}) — 토요일 체인의 prune_fwd.py 가 안 돌았다")
    if fwd_duplicate_days:
        bad.append(f"fwd 같은 ISO 주에 날짜가 {fwd_duplicate_days}개 더 있다 — 재송출 중복을 정리할 것")
    if hist_weeks > a.hist_max_weeks:
        bad.append(f"fwd_hist {hist_weeks}주 (롤링 {a.hist_max_weeks}) — push_fwd_hist.py 의 정리가 안 돌았다")
    if hist_duplicate_days:
        bad.append(f"fwd_hist 같은 ISO 주에 날짜가 {hist_duplicate_days}개 더 있다 — 주간 이력 정책 위반")
    if pct >= a.warn_pct:
        bad.append(f"무료티어 {pct:.0f}% (경고선 {a.warn_pct}%)")
    if not bad:
        return 0

    print("\n⚠️  " + " · ".join(bad))
    if (fwd_weeks > a.fwd_max_weeks or hist_weeks > a.hist_max_weeks
            or fwd_duplicate_days or hist_duplicate_days):
        print("""
조치 (fwd·fwd_hist — private open-proxy-storage/forward-collector, 원본은 그 머신의 DuckDB·jsonl 이라 내보내기 불필요):
  python3 prune_fwd.py --keep-weeks 2 --pin 2026-08-31 --dry-run  # 지울 날짜 확인 → 빼고 다시 실행
  python3 push_fwd_hist.py --keep-weeks 13        # 이력 롤링""")
    if len(weeks) > a.max_weeks or pct >= a.warn_pct:
        print("""
조치 (private 레포 백업이 먼저다 — 지우는 쪽만 영속이고 남기는 쪽이 휘발이면 백업이 아니다):
  1) python3 scripts/events_drain.py                 # dry-run: parquet 만 쓴다(usage/events/)
  2) open-proxy-storage 에서 usage/events/*.parquet 커밋·푸시
  3) python3 scripts/events_drain.py --apply         # 검증 후 DELETE
  4) VACUUM FULL ops_tool_calls;                     # 여기까지 해야 용량이 실제로 돌아온다
자세히: private wiki-private/architecture/usage-telemetry-operations.md""")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
