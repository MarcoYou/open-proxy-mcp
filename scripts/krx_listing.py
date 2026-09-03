#!/usr/bin/env python3
"""종목별 최초·최종 관측일 — `krx_listing`. KRX·DART 콜 0 (`krx_weekly` 파생).

■ 무엇을 푸는가 — 「배당을 안 했다」와 「그해엔 상장사가 아니었다」의 구분

배당 이력을 회사×연도로 세면 빈칸이 나온다. 그 빈칸이 **0회**(상장돼 있었는데 안 줬다)인지
**모름**(아직 상장 전이라 애초에 물을 수 없다)인지는 값 자체로는 알 수 없다.
0 으로 메우면 「무배당 기업」 목록에 상장 3년차 회사가 섞이고, 5년 연속 배당 같은 조건이
조용히 틀린 답을 낸다. 🔴 「없다」와 「모른다」를 가르려면 상장 시점이 필요하다.

■ 왜 표로 굽는가 — 요청마다 계산하면 280ms 다
`krx_weekly` 는 1,348,311행/181MB 이고 `ticker` 별 `min(price_dd)` 는 병렬 순차스캔이
붙어도 실측 **279.5ms**(전 종목) · **378.0ms**(배당사만) 다. 20ms 짜리 도구가 물 값이
아니다. 결과는 3,257행뿐이라 굽고 나면 한 번 읽는 데 10ms 다.
`krx_shares_ledger` 로도 같은 걸 만들 수 있나 보았는데(같은 3,257종목) 201ms 라 마찬가지고,
시작일이 2016-01-04 로 오히려 늦다. 굽는 쪽이 맞다.

■ 🔴 이 표의 날짜는 **상장일이 아니라 관측 시작일**이다
`krx_weekly` 는 2015-12-30 부터다. 그 첫 날에 이미 보이는 종목이 2,041개인데, 그건
그날 상장했다는 뜻이 아니라 **그 전부터 있었다**는 뜻이다. 삼성전자를 2015-12-30 상장으로
적으면 그게 바로 「모르는 것을 아는 것처럼 적는」 짓이다. 그래서 `before_window` 를 둔다 —
참이면 「창 이전부터 상장 · 정확한 시점은 이 표가 모른다」는 뜻이고, 배당 창(FY2018~)
전체를 덮으므로 판단에는 충분하다.
창 이후 첫 관측은 주간 해상도라 실제 상장일보다 최대 6일 늦을 수 있다. 연 단위로 쓰는 한
문제가 되지 않지만, 12월 마지막 주 상장은 해가 넘어갈 수 있다(실측 1건: 490470 —
원장 20251229 vs 주간 20260102). 일 단위 판단에는 쓰지 않는다.

용례:
  python3 scripts/krx_listing.py            # 다시 굽는다(전량 교체)
  python3 scripts/krx_listing.py --dry      # 저장 없이 산출만
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
import psycopg

DDL = """
CREATE TABLE IF NOT EXISTS krx_listing (
  ticker        text PRIMARY KEY,
  market        text,
  first_seen_dd text NOT NULL,
  last_seen_dd  text NOT NULL,
  -- 🔴 참이면 「창 시작 전부터 상장 · 정확한 시점 모름」. first_seen_dd 를 상장일로 읽지 말라는 표시.
  before_window boolean NOT NULL,
  window_lo     text NOT NULL,
  built_at      timestamptz NOT NULL DEFAULT now());
"""

#: 산출 결과가 이보다 적으면 상류(`krx_weekly`)가 깨진 것으로 보고 덮어쓰지 않는다.
#: 실측 3,257종목 — 절반 아래로 떨어질 이유가 없다.
MIN_TICKERS = 1_500


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="저장 없이 산출만")
    a = ap.parse_args()

    con = psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=20)
    for stmt in DDL.strip().split(";"):
        if stmt.strip():
            con.execute(stmt)
    con.commit()

    window_lo = con.execute("SELECT min(price_dd) FROM krx_weekly").fetchone()[0]
    if not window_lo:
        print("🔴 krx_weekly 가 비었다 — 굽지 않는다", file=sys.stderr)
        con.close()
        return 1

    # market 은 마지막 관측 것을 쓴다 — 이전(코스닥→코스피)한 종목은 지금 있는 시장이 답이다.
    rows = con.execute(
        """
        SELECT w.ticker, min(w.price_dd) AS first_dd, max(w.price_dd) AS last_dd,
               (array_agg(w.market ORDER BY w.price_dd DESC))[1] AS market
          FROM krx_weekly w
         GROUP BY w.ticker
        """
    ).fetchall()

    if len(rows) < MIN_TICKERS:
        print(f"🔴 {len(rows)}종목뿐이다(하한 {MIN_TICKERS}) — 상류 이상으로 보고 덮지 않는다",
              file=sys.stderr)
        con.close()
        return 1

    payload = [(isu, mk, first, last, first == window_lo, window_lo)
               for isu, first, last, mk in rows]
    n_edge = sum(1 for p in payload if p[4])

    print(f"=== krx_listing ===")
    print(f"  관측창          : {window_lo} ~ {max(p[3] for p in payload)}")
    print(f"  종목            : {len(payload):,}")
    print(f"  창 이전부터 상장: {n_edge:,}  (🔴 first_seen_dd 를 상장일로 읽으면 안 되는 것들)")
    print(f"  창 안에서 신규  : {len(payload) - n_edge:,}")
    if a.dry:
        print("\n--dry — 저장 생략")
        for p in payload[:5]:
            print(f"    {p[0]} {p[1]} {p[2]}~{p[3]} before_window={p[4]}")
        con.close()
        return 0

    with con.cursor() as cur:
        cur.executemany(
            """INSERT INTO krx_listing
                 (ticker, market, first_seen_dd, last_seen_dd, before_window, window_lo, built_at)
               VALUES (%s,%s,%s,%s,%s,%s, now())
               ON CONFLICT (ticker) DO UPDATE SET
                 market=EXCLUDED.market, first_seen_dd=EXCLUDED.first_seen_dd,
                 last_seen_dd=EXCLUDED.last_seen_dd, before_window=EXCLUDED.before_window,
                 window_lo=EXCLUDED.window_lo, built_at=now()""",
            payload)
        cur.execute("GRANT SELECT ON krx_listing TO opm_ro")
    con.commit()
    n = con.execute("SELECT count(*) FROM krx_listing").fetchone()[0]
    con.close()
    print(f"  적재 후         : {n:,}행")
    return 0


if __name__ == "__main__":
    sys.exit(main())
