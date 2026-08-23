#!/usr/bin/env python3
"""시장·섹터 **시가총액** 집계 사전계산 — `krx_cap_agg`. DART·KRX 0콜.

260824 신설. `trading_data` 의 시장·섹터 시총 시계열이 읽는 표다.

★ `opm_val_market.cap` 을 재사용하면 안 된다 — 이름은 같은 `cap` 인데 **개념이 다르다**.
  저쪽은 배수의 분모(지배순이익·지배자본)를 가진 종목만 더한 값이라 시장 전체가 아니다.
  실측(20260821): KOSPI 저장분 5,497조 / 실제 5,713조, 822사 vs 942종목 — **3.8% 낮다**.
  배수의 분자로는 그게 맞다(분자·분모 모집단 일치). 하지만 「코스피 시총이 얼마냐」의 답은
  아니다. 그래서 같은 표에 스킴 하나 더 얹지 않고 **표를 나눈다** — 한 이름이 두 뜻을 가지면
  둘 중 하나는 반드시 틀리게 쓰인다.

여기 `cap` 은 **그 날 상장된 전 종목의 시총 합**이다(우선주 포함 — KRX 공표 시총과 같은 모집단).

★ 왜 사전계산인가. 실측 cold 3.16초 / warm 0.28초(1,342,779행 Seq Scan). 드물게 도는
  질의라 cold 가 현실값이고, 요청 경로에서 3초는 260823 의 502(느린 한 경로가 워커를 소진)와
  같은 형태다. 결과는 하루 한 번만 바뀌는 4만여 행이라 미리 두는 게 맞다.

★ 섹터는 소급이다. WICS 는 조회 시점 구성종목만 주고 우리는 2026-08 부터 모았다. 날짜마다
  「그 날짜 이하 최신 스냅샷, 없으면 가장 이른 것」을 쓰고 `sector_asof` 로 어느 관측을 적용했는지
  남긴다. 분류가 없는 종목(우선주·신규상장 등)은 버리지 않고 `_UNCLASSIFIED` 버킷에 남긴다 —
  조용히 빠지면 섹터 합이 시장 합보다 작은 이유를 아무도 모른다.

★ 왜 증분이 아니라 전량 재적재인가. ① 외부 API 0콜이고 11.5초다 — 「전체 재실행 금지」는
  DART 를 다시 때리는 파이프라인을 겨눈 규칙이고 여기엔 걸 것이 없다. ② WICS 관측이 하나
  늘면 **과거 전 구간의 소급 분류가 바뀐다.** 증분으로 최근만 고치면 과거가 옛 분류로 남아
  같은 표 안에 두 기준이 섞인다.

★ UPSERT 만으로는 부족하다 — 어떤 버킷이 비면(그 날 그 업종 종목이 0) 새로 넣을 행이 없어
  **옛 행이 그대로 남는다.** 그러면 섹터 합이 시장 합보다 커진다. 그래서 한 트랜잭션 안에서
  대상 구간을 지우고 다시 넣는다. 파생 100%(krx_weekly × wise_sector)라 되살릴 원천이 항상
  있고, 같은 실행에서 만든 값으로 채우므로 260705 의 DELETE 사고와 형태가 다르다.

실행: python3 scripts/krx_cap_agg.py [--since 20151230]
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
CREATE TABLE IF NOT EXISTS krx_cap_agg (
  price_dd    text    NOT NULL,
  market      text    NOT NULL,
  scheme      text    NOT NULL,
  bucket      text    NOT NULL,
  label       text,
  n           integer NOT NULL,
  cap         bigint  NOT NULL,
  sector_asof text,
  PRIMARY KEY (price_dd, market, scheme, bucket)
)
"""
IDX = ("CREATE INDEX IF NOT EXISTS idx_krx_cap_agg_scheme "
       "ON krx_cap_agg (scheme, market, price_dd)")

UPSERT = """
INSERT INTO krx_cap_agg (price_dd, market, scheme, bucket, label, n, cap, sector_asof)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (price_dd, market, scheme, bucket) DO UPDATE SET
  label=EXCLUDED.label, n=EXCLUDED.n, cap=EXCLUDED.cap, sector_asof=EXCLUDED.sector_asof
"""

# 시장 전체 — 분류와 무관하므로 스냅샷 시대(era)를 나눌 필요가 없다. 한 번에 훑는다.
Q_MARKET = """
SELECT price_dd, market, count(*), sum(mktcap)::bigint
FROM krx_weekly WHERE price_dd >= %s AND mktcap > 0
GROUP BY 1,2
"""

# 섹터 — LEFT JOIN 이라 미분류 종목이 살아남는다(coalesce 로 _UNCLASSIFIED).
Q_SECTOR = """
SELECT k.price_dd, k.market,
       COALESCE(w.{code}, '_UNCLASSIFIED'), COALESCE(w.{name}, '미분류'),
       count(*), sum(k.mktcap)::bigint
FROM krx_weekly k
LEFT JOIN wise_sector w ON w.ticker = k.ticker AND w.snap_dd = %s
WHERE k.price_dd >= %s AND k.price_dd < %s AND k.mktcap > 0
GROUP BY 1,2,3,4
"""


def _eras(snaps: list[str], since: str) -> list[tuple[str, str, str]]:
    """(snap_dd, 적용시작, 적용끝-배타) — 가장 이른 스냅샷은 그 이전 구간까지 소급해 덮는다."""
    out = []
    for i, s in enumerate(snaps):
        start = since if i == 0 else max(s, since)
        end = snaps[i + 1] if i + 1 < len(snaps) else "99999999"
        if start < end:
            out.append((s, start, end))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="20151230", help="이 날짜부터 집계 (기본 = krx_weekly 전 구간)")
    a = ap.parse_args()

    con = psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=30)
    con.autocommit = True
    con.execute(DDL)
    con.execute(IDX)
    con.autocommit = False   # 이하 삭제+삽입은 한 트랜잭션

    snaps = [r[0] for r in con.execute("SELECT DISTINCT snap_dd FROM wise_sector ORDER BY snap_dd")]
    if not snaps:
        print("wise_sector 비어 있음 — refresh_wics.py 를 먼저 돌린다")
        return 1

    rows: list[tuple] = []
    for dd, mkt, n, cap in con.execute(Q_MARKET, (a.since,)):
        rows.append((dd, mkt, "market", "_ALL", "전체", n, cap, None))
    print(f"시장 집계 {len(rows):,}행", flush=True)

    eras = _eras(snaps, a.since)
    for scheme, code, name in (("wics_sector", "sector_code", "sector"),
                               ("wics_industry", "industry_code", "industry")):
        before = len(rows)
        for snap, start, end in eras:
            q = Q_SECTOR.format(code=code, name=name)
            for dd, mkt, bucket, label, n, cap in con.execute(q, (snap, start, end)):
                rows.append((dd, mkt, scheme, bucket, label, n, cap, snap))
        print(f"{scheme} 집계 {len(rows)-before:,}행 (스냅샷 시대 {len(eras)}개)", flush=True)

    # 지우고-넣기를 한 트랜잭션으로. 중간에 죽어도 옛 표가 그대로 남는다(빈 표가 되지 않는다).
    with con.cursor() as cur:
        cur.execute("DELETE FROM krx_cap_agg WHERE price_dd >= %s", (a.since,))
        deleted = cur.rowcount
        for i in range(0, len(rows), 5000):
            cur.executemany(UPSERT, rows[i:i + 5000])
    con.commit()
    con.autocommit = True

    print(f"\nkrx_cap_agg {len(rows):,}행 적재 (옛 {deleted:,}행 교체)")
    for r in con.execute("SELECT scheme, count(*), count(DISTINCT price_dd) "
                         "FROM krx_cap_agg GROUP BY 1 ORDER BY 1"):
        print(f"  {r[0]:16s} {r[1]:>7,}행 · {r[2]}시점")

    # 무결성: 섹터 합 == 시장 합 이어야 한다(_UNCLASSIFIED 를 포함했으므로 정확히 같아야 한다).
    #   어긋나면 조용히 빠진 종목이 있다는 뜻이다.
    bad = con.execute("""
        SELECT m.price_dd, m.market, s.scheme, m.cap, s.cap FROM
          (SELECT price_dd, market, cap FROM krx_cap_agg WHERE scheme='market') m
          JOIN (SELECT price_dd, market, scheme, sum(cap) cap FROM krx_cap_agg
                WHERE scheme<>'market' GROUP BY 1,2,3) s
            ON s.price_dd=m.price_dd AND s.market=m.market
        WHERE s.cap <> m.cap LIMIT 5""").fetchall()
    if bad:
        print(f"\n::error:: 섹터 합 ≠ 시장 합 — 표본 {bad}")
        return 1
    print("무결성 OK — 섹터 합 == 시장 합 (전 시점·전 스킴)")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
