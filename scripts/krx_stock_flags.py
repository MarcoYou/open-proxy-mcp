#!/usr/bin/env python3
"""시계열 해석 주의 딱지 생성 — `krx_stock_flags`. KRX·DART 콜 0 (전부 Supabase 조인).

`valuation` 이 두 곳에서 읽어 사용자에게 경고를 붙인다(scope=firm · firm_history).

  spinoff_break          인적분할/합병 이력 — 분할 전 구간과는 배수 비교가 무의미
  unresolved_adjustment  주식수가 줄었는데 거래소 기준가 조정이 없던 지점 — 과거 가격 비교 주의

260823 신설. 종전에는 **만드는 코드가 레포에 없었다.** 260702 에 한 번 만들어지고 박제돼,
그 뒤 분할·감자한 종목은 경고가 안 붙었다. 시계열이 끊겼는데 사용자는 모르고 비교한다.

■ 원본 규칙은 재현하지 못했다 — 그래서 **덮어쓰지 않는다**
  detail 을 열어 역산을 시도했다. 원천은 찾았다(unresolved 이벤트 125/125 가
  `krx_shares_ledger` 의 변동일. 리셋에 있는 건 41/125 뿐이라 「리셋 없음」이 조건).
  그런데 문턱이 안 맞는다 — 주식수비 r 은 중앙값 0.963 에 최대 1.96(증가!)까지 있고,
  가격비 p 는 `p<1` 이 65건인데 `p<0.9` 도 정확히 65건이라 0.9~1.0 구간이 비어 있다.
  조건이 하나가 아니라 여럿 얽혀 있고, 특히 **소각**(주식수 감소·가격 정상·리셋 없음)을
  걸러내는 장치가 있었을 것이다. 설계 문구의 「과잉 마스킹 방지」가 그 얘기로 보인다.

  무리하게 맞추려다 **기존 경고를 잃는 쪽이 더 나쁘다.** 그래서:
   · 기존 155건(unresolved 115 · spinoff 40)은 **그대로 둔다**
   · 신규 탐지분만 `rule:"cap_break_v1"` 로 표시해 **추가**한다
   · spinoff_break 는 데이터로 재현 불가(40종목 중 15는 리셋조차 없음 = 공시가 원천)이므로
     신규 탐지를 하지 않는다. DART 회사분할결정 스윕은 별건.

■ 신규 규칙 (cap_break_v1) — 시총 불연속
  주식수가 10%+ 줄었는데 ① 거래소 기준가 리셋이 없고 ② 가격이 비례해 움직이지도 않은 지점.
  깨끗한 조정 이벤트라면 주가×주식수가 보존되므로 p×r ≈ 1 이다. 15% 넘게 벗어나면
  설명되지 않은 불연속이다. 소각은 p≈1·r<1 로 p×r 이 r 만큼 벗어나므로 걸리는데,
  그건 실제로 시총이 준 것이라 「시계열 비교 주의」가 맞다.

■ 🔴 하한 이전 이벤트는 평가하지 않는다 (261017)
  이 탐지는 이벤트 전후의 `krx_weekly` 종가(직전 주·직후 주)로 p 를 낸다. `krx_weekly` 를 최근 3년 + 종목별로 해마다 마지막 행
  (연말 행)으로 줄이면(`open_proxy_mcp/krx_retention.py`) 하한 이전 이벤트의 전후 점이 **1년 간격 연말 행**으로 잡혀
  p 가 틀어진다 — 실측(261017, 잘린 표 모의): 오래된 이벤트 26건의 전후 간격 중앙값 364일, **새 가짜 경고 7종목**
  (경고는 사용자 응답에 `unresolved_adjustment` 로 붙는다). 이 탐지는 매일 돌아 모든 이벤트를 이미 한 번씩 평가했으므로
  하한(+14일 — 직전 점이 촘촘한 구간에 들 여유) 이전 이벤트는 **건너뛰어도 잃는 판정이 없다**(기존 딱지는 병합으로 그대로 남는다).

용례:
  python3 scripts/krx_stock_flags.py            # 갱신(기존 spinoff_break 보존)
  python3 scripts/krx_stock_flags.py --dry      # 저장 없이 산출만
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
import psycopg

from open_proxy_mcp.krx_daily import WEEKLY_LATEST_SQL, WEEKLY_WITH_LATEST
from open_proxy_mcp.krx_retention import krx_weekly_pair_floor

DDL = """
-- PK 는 (ticker) 하나 — **종목당 딱지 하나**가 기존 설계다(실측: 155행 = 115+40, 겹침 0).
CREATE TABLE IF NOT EXISTS krx_stock_flags (
  ticker text PRIMARY KEY, flag text NOT NULL,
  detail jsonb, updated text);
"""

_MIN_DROP = 0.90      # 주식수 감소 최소 폭 — 단주 조정을 거른다
_MIN_DEV = 0.15       # |p×r − 1| 이탈 폭 — 실측 33종목/37이벤트 (원본 115/125 와 별개 규칙)
_RULE = "cap_break_v1"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="저장 없이 산출만")
    a = ap.parse_args()

    con = psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=15)
    for stmt in DDL.strip().split(";"):
        if stmt.strip():
            con.execute(stmt)
    con.commit()

    # ── 신규 탐지: 주식수 10%+ 감소 · 리셋 없음 · 시총 불연속(|p×r−1| > 15%)
    #    리셋이 있으면 거래소가 가격을 조정했다는 뜻 = 설명된 이벤트라 대상이 아니다.
    #    p 는 krx_weekly 근사(주간 해상도) — 원장에 일별 종가가 없다. 그래서 문턱을 넉넉히 잡는다.
    # 261017: 평가 대상은 보존 하한(+14일) 이후 이벤트뿐 — 위 docstring 「하한 이전 이벤트는 평가하지 않는다」.
    latest = con.execute(WEEKLY_LATEST_SQL).fetchone()[0]          # 닫힌 주 최신(261019 v03 C4 — krx_weekly 가 뷰여도 최근 몇 주만 읽는다)
    if not latest:
        print("🔴 krx_weekly 가 비었다 — 전후 가격을 못 구해 평가하지 않는다", file=sys.stderr)
        con.close()
        return 1
    floor = krx_weekly_pair_floor(latest)
    n_old = con.execute(
        """SELECT count(*) FROM krx_shares_ledger l
             LEFT JOIN krx_adj_events b ON b.ticker = l.ticker AND b.event_dd = l.event_dd
            WHERE l.prev_shrs IS NOT NULL AND l.prev_shrs > 0 AND l.new_shrs IS NOT NULL
              AND l.new_shrs::numeric / l.prev_shrs < %s AND b.ticker IS NULL
              AND l.event_dd < %s""", (_MIN_DROP, floor)).fetchone()[0]
    # 261018(재설계 5-1 쓰는 쪽): 전후 종가의 원천은 「닫힌 주 + 일별 최신 거래일」(`WEEKLY_WITH_LATEST`) — krx_weekly 는 닫힌 주만
    #   담게 되어 이번 주 사건의 「직후 점」이 주가 닫힐 때까지 없다. 종전 krx_weekly(이번 주 행 = 그날 최신일)와 같은 모양으로 읽는다.
    rows = con.execute(
        """
        WITH e AS (
          SELECT l.ticker, l.event_dd,
                 l.new_shrs::numeric / l.prev_shrs AS r,
                 (SELECT w.close FROM """ + WEEKLY_WITH_LATEST + """ w WHERE w.ticker=l.ticker AND w.price_dd>=l.event_dd
                   ORDER BY w.price_dd LIMIT 1)::numeric
                 / NULLIF((SELECT w.close FROM """ + WEEKLY_WITH_LATEST + """ w WHERE w.ticker=l.ticker AND w.price_dd<l.event_dd
                   ORDER BY w.price_dd DESC LIMIT 1), 0) AS p
          FROM krx_shares_ledger l
          LEFT JOIN krx_adj_events b
            ON b.ticker = l.ticker AND b.event_dd = l.event_dd
          WHERE l.prev_shrs IS NOT NULL AND l.prev_shrs > 0 AND l.new_shrs IS NOT NULL
            AND l.new_shrs::numeric / l.prev_shrs < %s
            AND b.ticker IS NULL
            AND l.event_dd >= %s)
        SELECT ticker, event_dd, r, p FROM e
        WHERE p IS NOT NULL AND ABS(p * r - 1) > %s
        ORDER BY ticker, event_dd
        """,
        (_MIN_DROP, floor, _MIN_DEV),
    ).fetchall()

    per: dict[str, list[dict]] = {}
    for isu, dd, r, px in rows:
        per.setdefault(isu, []).append(
            {"dd": dd, "r": round(float(r), 4), "p": round(float(px), 4),
             "why": "시총 불연속·설명불가", "rule": _RULE})

    # ★ 이 테이블은 PK 가 (ticker) 하나다 = **종목당 딱지 하나**. spinoff_break 가 붙은 종목에
    #   unresolved 를 쓰면 분할 경고가 덮인다 — 분할 쪽이 더 강한 신호이므로 건너뛴다.
    spin = {r[0] for r in con.execute(
        "SELECT ticker FROM krx_stock_flags WHERE flag='spinoff_break'")}
    skipped = len(set(per) & spin)
    for isu in list(per):
        if isu in spin:
            del per[isu]

    # 기존 것과 병합 — 같은 (종목, 날짜)는 기존 판정을 남긴다(원본 규칙이 더 정교했을 수 있다)
    existing = {isu: (d or {}).get("events", []) for isu, d in con.execute(
        "SELECT ticker, detail FROM krx_stock_flags WHERE flag='unresolved_adjustment'").fetchall()}
    merged: dict[str, list[dict]] = {}
    added = 0
    for isu in set(existing) | set(per):
        old_ev = existing.get(isu, [])
        seen = {e.get("dd") for e in old_ev}
        fresh = [e for e in per.get(isu, []) if e["dd"] not in seen]
        added += len(fresh)
        merged[isu] = old_ev + fresh

    today = date.today().strftime("%Y%m%d")
    before = dict(con.execute(
        "SELECT flag, count(*) FROM krx_stock_flags GROUP BY flag").fetchall())
    print(f"=== krx_stock_flags 갱신 ({today}) ===")
    print(f"  이전            : {before}")
    print(f"  평가 제외       : {n_old}이벤트 (보존 하한+14일 {floor} 이전 — 전후 주간 점이 연말 행이라 믿을 수 없다·기존 판정은 보존)")
    print(f"  신규 규칙 산출  : {len(per)}종목 / {sum(len(v) for v in per.values())}이벤트 ({_RULE})")
    print(f"  기존에 없던 것  : {added}이벤트 · 병합 후 {len(merged)}종목")
    if skipped:
        print(f"  spinoff 우선 제외: {skipped}종목 (PK 가 종목당 1딱지라 덮으면 분할 경고가 사라진다)")

    if a.dry:
        print("\n--dry — 저장 생략")
        for isu in list(per)[:5]:
            print(f"    {isu}: {per[isu][:2]}")
        con.close()
        return 0

    with con.cursor() as cur:
        # spinoff_break 는 건드리지 않는다 — 데이터로 재현 불가라 지우면 복구 못 한다.
        cur.executemany(
            "INSERT INTO krx_stock_flags (ticker, flag, detail, updated) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (ticker) DO UPDATE SET flag=EXCLUDED.flag, "
            "detail=EXCLUDED.detail, updated=EXCLUDED.updated",
            [(isu, "unresolved_adjustment", json.dumps({"events": ev}, ensure_ascii=False), today)
             for isu, ev in merged.items()])
    con.commit()
    after = dict(con.execute(
        "SELECT flag, count(*) FROM krx_stock_flags GROUP BY flag").fetchall())
    print(f"  이후            : {after}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
