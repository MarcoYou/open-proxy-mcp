#!/usr/bin/env python3
"""이미 들어와 있는 분기재무에 새 스케일 가드를 **소급 적용**한다.

배경(260829) — 가드는 260704 에 생겼고 신규 fetch 만 막았다. 그전에 들어온 오염은 남았고,
게다가 옛 가드는 두 군데가 뚫려 있었다.
  · 자릿수 상한이 **순이익에만** 걸렸다 — 자본 112,400조가 그대로 통과했다.
  · 기준이 시장 최댓값(삼성전자)이라 **소형주의 1,000배 오류를 놓쳤다**(코스닥 8행 중 3행).
그 결과 코스닥 시장 PER 이 −2,291조/+5,283조 같은 값으로 34개 시점에서 뒤집혔다.

여기서 값을 **고치지 않는다. 비운다.** 올바른 값은 원본 공시를 다시 읽어야 알 수 있고,
추정해서 채우면 그게 또 다른 오염이다. 비우면 집계가 그 종목을 빼고 간다.

기본은 dry-run. 실제로 비우려면 --apply.
"""
from __future__ import annotations

import argparse
import collections
import os
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
import psycopg

from open_proxy_mcp.services.scale_guard import assess


def self_ref_map(con) -> dict:
    ann = collections.defaultdict(list)
    for t, fy, eq, eqr in con.execute(
            "SELECT ticker, fy, eq, eq_restated FROM dart_finstat_y ORDER BY fy"):
        v = eqr if eqr is not None else eq
        if v:
            ann[t].append(abs(v))
    return {t: max(st.median(vs), vs[-1]) for t, vs in ann.items() if vs}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제로 비운다(기본은 dry-run)")
    a = ap.parse_args()

    con = psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=30)
    con.autocommit = True
    ref = self_ref_map(con)
    mkt = {r[0]: r[1] for r in con.execute("SELECT ticker, market FROM dart_fundamentals")}

    rows = con.execute(
        "SELECT ticker, fy, quarter, ni_cum, eq FROM dart_finstat_q "
        "WHERE ni_cum IS NOT NULL OR eq IS NOT NULL").fetchall()
    hits = []
    for t, fy, q, ni, eq in rows:
        v = assess(thstrm=ni, equity=eq, self_ref=ref.get(t))
        if v["tier"] == "hard":
            hits.append((t, fy, q, ni, eq, v["hard_hit"]))

    print(f"검사 {len(rows):,}행 · hard {len(hits)}행\n")
    for t, fy, q, ni, eq, why in sorted(hits):
        r = ref.get(t) or 0
        print(f"  {t}({mkt.get(t)}) {fy}Q{q} ni {ni or 0:.3e} eq {eq or 0:.3e} "
              f"· 자 {r:.3e} · {','.join(why)}")

    if not hits:
        print("\n비울 것 없음.")
        return 0
    if not a.apply:
        print("\n(dry-run — 실제로 비우려면 --apply)")
        return 0

    with con.cursor() as cur:
        cur.executemany(
            "UPDATE dart_finstat_q SET ni_cum=NULL, eq=NULL, "
            "ni_case='SCALE_GUARD', eq_case='SCALE_GUARD', fetched='nodata' "
            "WHERE ticker=%s AND fy=%s AND quarter=%s",
            [(t, fy, q) for t, fy, q, *_ in hits])
    print(f"\n✓ {len(hits)}행 비움(ni_cum·eq → NULL, case=SCALE_GUARD)")

    left = [r for r in con.execute(
        "SELECT ticker, fy, quarter, ni_cum, eq FROM dart_finstat_q "
        "WHERE ni_cum IS NOT NULL OR eq IS NOT NULL")
        if assess(thstrm=r[3], equity=r[4], self_ref=ref.get(r[0]))["tier"] == "hard"]
    print(f"확인 — 남은 hard {len(left)}행")
    return 0 if not left else 1


if __name__ == "__main__":
    sys.exit(main())
