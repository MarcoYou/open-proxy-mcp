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
import json
import os
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
import psycopg

from open_proxy_mcp.services.scale_guard import assess, propose_scale_fix

# 260829: 가드가 비운 행의 **원문**. 비우기만 하면 나중에 고칠 근거가 사라진다.
#   DART 재조회로 떠서 파일로 남겨 뒀다(단위 배수가 덧곱해진 상태 그대로).
SEED = ROOT / "scripts/data/scale_repair_seed.json"

MIGRATE = (
    # 연간표에는 있는데 분기표에는 없었다. 그래서 고치면 원본을 덮어쓸 수밖에 없었다.
    "ALTER TABLE dart_finstat_q ADD COLUMN IF NOT EXISTS ni_cum_raw double precision",
    "ALTER TABLE dart_finstat_q ADD COLUMN IF NOT EXISTS eq_raw double precision",
    "ALTER TABLE dart_finstat_q ADD COLUMN IF NOT EXISTS ni_cum_restated double precision",
    "ALTER TABLE dart_finstat_q ADD COLUMN IF NOT EXISTS eq_restated double precision",
    "ALTER TABLE dart_finstat_q ADD COLUMN IF NOT EXISTS restate_why text",
)


def self_ref_map(con) -> dict:
    ann = collections.defaultdict(list)
    for t, fy, eq, eqr in con.execute(
            "SELECT ticker, fy, eq, eq_restated FROM dart_finstat_y ORDER BY fy"):
        v = eqr if eqr is not None else eq
        if v:
            ann[t].append(abs(v))
    return {t: max(st.median(vs), vs[-1]) for t, vs in ann.items() if vs}


def _seed_and_restate(con, ref) -> None:
    """① 이미 비워져 원문이 없는 행에 씨앗을 넣고 ② 복구 가능한 것은 restated 칸을 채운다.

    복구는 두 신호가 **독립적으로 같은 10ⁿ 을 가리킬 때만** 한다 —
    원문 끝의 0 개수, 그리고 나눈 값이 그 회사 평소 규모에 드는지.
    값을 원본 칸에 쓰지 않는다. 왜 그렇게 고쳤는지를 restate_why 에 남긴다.
    """
    if SEED.exists():
        seed = json.loads(SEED.read_text())["rows"]
        with con.cursor() as cur:
            cur.executemany(
                "UPDATE dart_finstat_q SET ni_cum_raw=COALESCE(ni_cum_raw, %s), "
                "eq_raw=COALESCE(eq_raw, %s) WHERE ticker=%s AND fy=%s AND quarter=%s",
                [(r["ni_cum_raw"], r["eq_raw"], r["ticker"], r["fy"], r["quarter"]) for r in seed])
        print(f"씨앗 {len(seed)}행 — 원문 보존 칸 채움")

    rows = con.execute(
        "SELECT ticker, fy, quarter, ni_cum_raw, eq_raw FROM dart_finstat_q "
        "WHERE (ni_cum_raw IS NOT NULL OR eq_raw IS NOT NULL) "
        "AND ni_cum_restated IS NULL AND eq_restated IS NULL").fetchall()
    fixes, skipped = [], []
    for t, fy, q, ni_raw, eq_raw in rows:
        r = ref.get(t)
        pe = propose_scale_fix(eq_raw, r)
        if not pe.get("ok"):
            skipped.append((t, fy, q, "자본에서 배수를 특정 못 함"))
            continue
        n = pe["power"]
        # 순이익은 같은 보고서라 **같은 배수**를 쓴다 — 보고서가 통째로 틀리기 때문이다.
        why = (f"단위배수 덧곱 추정 ÷10^{n} — 원문 끝0 {pe['trailing_zeros']}개 · "
               f"나눈 자본이 회사 평소 규모의 {pe['ratio_to_ref']:.2f}배 (260829 소급복구)")
        fixes.append((ni_raw / 10 ** n if ni_raw is not None else None,
                      eq_raw / 10 ** n if eq_raw is not None else None, why, t, fy, q))
    if fixes:
        with con.cursor() as cur:
            cur.executemany(
                "UPDATE dart_finstat_q SET ni_cum_restated=%s, eq_restated=%s, restate_why=%s "
                "WHERE ticker=%s AND fy=%s AND quarter=%s", fixes)
        print(f"✓ 복구값 {len(fixes)}행 (restated 칸 · 원본은 그대로)")
        for ni, eq, why, t, fy, q in fixes:
            print(f"    {t} {fy}Q{q}: ni {ni if ni is None else f'{ni:.3e}'} "
                  f"· eq {eq if eq is None else f'{eq:.3e}'} — {why}")
    for t, fy, q, why in skipped:
        print(f"  ⚠ 복구 보류 {t} {fy}Q{q} — {why}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제로 비운다(기본은 dry-run)")
    a = ap.parse_args()

    con = psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=30)
    con.autocommit = True
    for ddl in MIGRATE:
        con.execute(ddl)
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
        # 원문은 raw 칸에 남기고 본 칸만 비운다 — 나중에 고칠 근거를 지우지 않는다(260829).
        cur.executemany(
            "UPDATE dart_finstat_q SET ni_cum_raw=COALESCE(ni_cum_raw, ni_cum), "
            "eq_raw=COALESCE(eq_raw, eq), ni_cum=NULL, eq=NULL, "
            "ni_case='SCALE_GUARD', eq_case='SCALE_GUARD', fetched='nodata' "
            "WHERE ticker=%s AND fy=%s AND quarter=%s",
            [(t, fy, q) for t, fy, q, *_ in hits])
    print(f"\n✓ {len(hits)}행 비움(원문은 ni_cum_raw·eq_raw 로 보존)")

    _seed_and_restate(con, ref)

    left = [r for r in con.execute(
        "SELECT ticker, fy, quarter, ni_cum, eq FROM dart_finstat_q "
        "WHERE ni_cum IS NOT NULL OR eq IS NOT NULL")
        if assess(thstrm=r[3], equity=r[4], self_ref=ref.get(r[0]))["tier"] == "hard"]
    print(f"확인 — 남은 hard {len(left)}행")
    return 0 if not left else 1


if __name__ == "__main__":
    sys.exit(main())
