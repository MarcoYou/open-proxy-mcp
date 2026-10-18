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

★ 섹터는 소급이다. 분류 공급처는 조회 시점 구성종목만 주고 우리는 2026-08 부터 모았다. 날짜마다
  「그 날짜 이하 최신 스냅샷, 없으면 가장 이른 것」을 쓰고 `sector_asof` 로 어느 관측을 적용했는지
  남긴다. 분류가 없는 종목(우선주·신규상장 등)은 버리지 않고 `_UNCLASSIFIED` 버킷에 남긴다 —
  조용히 빠지면 섹터 합이 시장 합보다 작은 이유를 아무도 모른다.

★ 평소에는 최신 주만 다시 적재한다. 외부 API 호출은 없어도 전 구간 DELETE·INSERT가 매주
  큰 WAL과 통계 누적을 만들기 때문이다. 새 업종분류 관측이 들어오면 그 관측일부터만 다시 계산한다.
  과거 날짜에는 날짜 이하 최신 스냅샷을 쓰므로 새 관측 이전 구간은 바뀌지 않는다. 원천 정정처럼
  전 구간을 다시 만들 필요가 있을 때만 `--full`을 쓴다.

★ UPSERT 만으로는 부족하다 — 어떤 버킷이 비면(그 날 그 업종 종목이 0) 새로 넣을 행이 없어
  **옛 행이 그대로 남는다.** 그러면 섹터 합이 시장 합보다 커진다. 그래서 한 트랜잭션 안에서
  대상 구간을 지우고 다시 넣는다. 파생 100%(krx_weekly × 분류 스냅샷)라 되살릴 원천이 항상
  있고, 같은 실행에서 만든 값으로 채우므로 260705 의 DELETE 사고와 형태가 다르다.

★ 🔴 보존 하한 이전은 다시 쓰지 않는다 (261017). `krx_weekly` 는 최근 3년 + 종목별로 해마다 마지막 행(연말 행)만
  남기도록 줄인다(`open_proxy_mcp/krx_retention.py`). 그러면 하한 이전 구간의 **원천은 종목별 연말 행 한 줄씩**뿐이다 —
  해의 마지막 주를 뺀 날짜는 그해 상장폐지·정지한 종목의 마지막 행만 남은 **부분 행**이라, 그 날짜로 다시 집계하면 시총 합이
  터무니없이 작아진다. 거기서부터 다시 집계하면 옛 주간 집계를 지우고 그 값으로 갈아 끼우게 된다 — 되살릴 원천이 없다.
  · 평소(최신 주·새 업종분류 관측)는 모두 하한 안쪽이라 달라지는 게 없다.
  · `--since`/`--full` 이 하한 이전을 가리키면 **거절**한다. 정말 필요하면 `--allow-before-cutoff`.
  · 그때도 하한 이전은 **원천이 온전한 날짜(해마다 마지막 주)만** 다시 집계하고 그 날짜의 집계만 지운다 — 나머지 옛 집계
    (원천이 부분 행뿐인 날짜 포함)는 건드리지 않는다.

★ 이번 주 최신 점은 일별 시세 저장분에서 온다 (261018, 재설계 5-1 쓰는 쪽). `krx_weekly` 는 닫힌 주만 담게 되어 주중에는
  지난 금요일이 최신이다. 원천을 「닫힌 주 + 일별 최신 거래일」(`krx_daily.WEEKLY_WITH_LATEST`)로 읽어 종전과 같은 모양
  (주마다 그 주 마지막 거래일, 이번 주는 최신 거래일)을 유지한다. 이번 주 임시 점은 그 주가 닫히면 다음 실행이 지우고 닫힌 날로 바꾼다.
  재계산 시작은 「원천 최신 주」와 「집계 표의 최신 점이 속한 주」 중 이른 쪽의 첫날이다 — 배치가 며칠 걸러도(또는 임시 점이 남아도)
  그 사이 주를 놓치지 않는다. 한 주 = ISO 주를 달력 연도로 자른 것(`krx_retention.week_unit`).

실행: python3 scripts/krx_cap_agg.py [--since 20231016 | --full] [--allow-before-cutoff]
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
import psycopg

from open_proxy_mcp.krx_daily import WEEKLY_WITH_LATEST
from open_proxy_mcp.krx_retention import krx_weekly_cutoff, week_unit

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
# 원천 = 닫힌 주(krx_weekly) + 일별 최신 거래일(이번 주 임시 점) — 261018.
Q_MARKET = """
SELECT price_dd, market, count(*), sum(mktcap)::bigint
FROM """ + WEEKLY_WITH_LATEST + """ w WHERE price_dd >= %s AND mktcap > 0{src}
GROUP BY 1,2
"""

# 섹터 — LEFT JOIN 이라 미분류 종목이 살아남는다(coalesce 로 _UNCLASSIFIED).
Q_SECTOR = """
SELECT k.price_dd, k.market,
       COALESCE(w.{code}, '_UNCLASSIFIED'), COALESCE(w.{name}, '미분류'),
       count(*), sum(k.mktcap)::bigint
FROM """ + WEEKLY_WITH_LATEST + """ k
LEFT JOIN wise_sector w ON w.ticker = k.ticker AND w.snap_dd = %s
WHERE k.price_dd >= %s AND k.price_dd < %s AND k.mktcap > 0{src}
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


def _week_start(dd: str) -> str:
    """YYYYMMDD 가 속한 주의 첫날 — ISO 주를 달력 연도로 자른 것(`krx_retention.week_unit`, 261018)."""
    return week_unit(dd)[0]


def _choose_since(*, requested: str | None, full: bool, source_min: str,
                  source_max: str, latest_snap: str, latest_sector_asof: str | None,
                  agg_latest: str | None = None) -> tuple[str, str]:
    """재계산 시작일과 이유. 새 분류는 그 날짜 이후에만 영향을 준다.

    평소(최신 주 갱신)는 원천 최신 주와 집계 표 최신 점이 속한 주 중 **이른 쪽**부터 — 261018. 집계 표의 최신 점이 지난주의
    임시 점(그 주가 닫히기 전 일별 최신일)이면 그 주부터 다시 만들어 닫힌 날로 바꾸고, 배치가 며칠 걸렀으면 그 사이 주를 채운다."""
    if requested:
        return requested, "지정 구간"
    if full or latest_sector_asof is None:
        return source_min, "전 구간" if full else "최초 적재"
    if latest_snap > latest_sector_asof:
        return latest_snap, "새 업종분류 관측 반영"
    return _week_start(min(source_max, agg_latest) if agg_latest else source_max), "최신 주 갱신"


def _guard_since(*, since: str, reason: str, cutoff: str, allow: bool) -> tuple[bool, str]:
    """(진행 여부, 안내). 보존 하한 이전을 다시 쓰려는 요청을 막는다.

    하한 이전 구간의 원천은 종목별 연말 행 한 줄씩이라 거기서 다시 집계하면 되살릴 수 없는 옛 집계를 갈아 끼운다.
    「최초 적재」(집계 표가 비어 지울 것이 없다)는 그대로 통과시킨다.
    """
    if since >= cutoff:
        return True, ""
    if reason == "최초 적재":
        return True, (f"⚠ 최초 적재라 지울 집계가 없다 — 하한({cutoff}) 이전은 해마다 마지막 주 한 점씩만 집계된다.")
    if allow:
        return True, (f"⚠ --allow-before-cutoff — 하한({cutoff}) 이전 {since} 부터 다시 집계한다. 하한 이전은 원천이 온전한 "
                      "날짜(해마다 마지막 주)만 다시 집계하고 그 날짜의 집계만 지운다 — 나머지 옛 집계는 그대로 둔다.")
    return False, (f"🔴 {reason}({since})이 보존 하한({cutoff})보다 이르다 — krx_weekly 는 하한 이전을 종목별 연말 행 한 줄씩만 보존해 "
                   "그 구간은 다시 집계하면 되살릴 수 없는 옛 집계를 지우게 된다. 거절한다. "
                   "정말 필요하면 --allow-before-cutoff.")


def _src_filter(since: str, cutoff: str, whole_dates: list[str], col: str) -> tuple[str, tuple]:
    """원천 날짜 제한(SQL 조각, 파라미터). 하한 이상 구간은 촘촘하니 제한이 없다(종전 쿼리 그대로).

    하한 아래는 **원천이 온전한 날짜**(`whole_dates` = 해마다 마지막 주)만 읽는다 — 그 밖의 날짜 행은 그해 상장폐지한
    종목의 마지막 행뿐이라 그 날짜의 시총 합이 아니다. 그 값으로 집계하면 옛 집계를 터무니없이 작은 값으로 덮는다.
    """
    if since >= cutoff:
        return "", ()
    return f" AND ({col} >= %s OR {col} = ANY(%s))", (cutoff, whole_dates)


def _delete_stmt(since: str, cutoff: str, whole_dates: list[str]) -> tuple[str, tuple]:
    """다시 넣을 구간의 옛 집계를 지우는 문장. **되살릴 수 없는 집계는 지우지 않는다.**

    하한 이상 구간은 원천이 촘촘하니 `since` 이후를 전부 지운다(버킷이 비면 옛 행이 남지 않게 — 종전과 같다).
    하한 아래는 다시 집계하는 날짜(`whole_dates`, 원천이 온전한 날짜)의 집계만 지운다 — 그 날짜는 곧 다시 넣는다.
    """
    if since >= cutoff:
        return "DELETE FROM krx_cap_agg WHERE price_dd >= %s", (since,)
    return ("DELETE FROM krx_cap_agg WHERE price_dd >= %s AND (price_dd >= %s OR price_dd = ANY(%s))",
            (since, cutoff, whole_dates))


def main() -> int:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--since", help="이 날짜부터 집계 (YYYYMMDD)")
    mode.add_argument("--full", action="store_true", help="krx_weekly 전 구간을 다시 집계")
    ap.add_argument("--allow-before-cutoff", action="store_true",
                    help="보존 하한(최신 주 3년 전) 이전 구간 재집계를 허용 — 하한 이전은 원천이 온전한 날짜(해마다 마지막 주)만 다시 쓴다")
    a = ap.parse_args()

    con = psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=30)
    con.autocommit = True
    con.execute(DDL)
    con.execute(IDX)
    con.autocommit = False   # 이하 삭제+삽입은 한 트랜잭션

    snaps = [r[0] for r in con.execute("SELECT DISTINCT snap_dd FROM wise_sector ORDER BY snap_dd")]
    if not snaps:
        print("분류 스냅샷 표가 비어 있음 — refresh_sector_class.py 를 먼저 돌린다")
        return 1

    source_min, weekly_max = con.execute("SELECT min(price_dd), max(price_dd) FROM krx_weekly").fetchone()
    if not source_min:
        print("krx_weekly 비어 있음 — 먼저 시세를 적재한다")
        return 1
    # 원천 최신 = 닫힌 주와 일별 최신 거래일 중 늦은 쪽(이번 주 임시 점). 보존 하한은 표를 자르는 쪽과 같게 krx_weekly 최신에서 잰다.
    daily_max = con.execute("SELECT max(trd_dd) FROM krx_daily").fetchone()[0]
    source_max = max(weekly_max, daily_max or "")
    latest_sector_asof, agg_latest = con.execute(
        "SELECT max(sector_asof) FILTER (WHERE scheme <> 'market'), max(price_dd) FILTER (WHERE scheme = 'market') FROM krx_cap_agg"
    ).fetchone()
    since, reason = _choose_since(
        requested=a.since,
        full=a.full,
        source_min=source_min,
        source_max=source_max,
        latest_snap=snaps[-1],
        latest_sector_asof=latest_sector_asof,
        agg_latest=agg_latest,
    )
    cutoff = krx_weekly_cutoff(weekly_max)
    cy0 = cutoff[:4] + "0101"   # 하한이 속한 해의 1월 1일 — 이보다 이른 해만 「그 해 전역 마지막 주」가 온전한 날짜다
    ok, note = _guard_since(since=since, reason=reason, cutoff=cutoff, allow=a.allow_before_cutoff)
    if not ok:
        print(note, file=sys.stderr)
        con.close()
        return 2
    print(f"재계산 시작 {since} · {reason} (보존 하한 {cutoff} · 닫힌 주 최신 {weekly_max} · 일별 최신 {daily_max})", flush=True)
    if note:
        print(note, flush=True)

    # 하한 이전을 다시 쓰는 경우(허용 플래그·최초 적재): 원천이 온전한 날짜 = 해마다 마지막 주(하한이 속한 해 이전)만
    whole_dates = [r[0] for r in con.execute(
        "SELECT max(price_dd) FROM krx_weekly WHERE price_dd >= %s AND price_dd < %s GROUP BY left(price_dd, 4)",
        (since, cy0))] if since < cutoff else []
    mkt_src, mkt_src_params = _src_filter(since, cutoff, whole_dates, "price_dd")
    sec_src, sec_src_params = _src_filter(since, cutoff, whole_dates, "k.price_dd")

    rows: list[tuple] = []
    for dd, mkt, n, cap in con.execute(Q_MARKET.format(src=mkt_src), (since, *mkt_src_params)):
        rows.append((dd, mkt, "market", "_ALL", "전체", n, cap, None))
    print(f"시장 집계 {len(rows):,}행", flush=True)

    eras = _eras(snaps, since)
    for scheme, code, name in (("wics_sector", "sector_code", "sector"),
                               ("wics_industry", "industry_code", "industry")):
        before = len(rows)
        for snap, start, end in eras:
            q = Q_SECTOR.format(code=code, name=name, src=sec_src)
            for dd, mkt, bucket, label, n, cap in con.execute(q, (snap, start, end, *sec_src_params)):
                rows.append((dd, mkt, scheme, bucket, label, n, cap, snap))
        print(f"{scheme} 집계 {len(rows)-before:,}행 (스냅샷 시대 {len(eras)}개)", flush=True)

    # 지우고-넣기를 한 트랜잭션으로. 중간에 죽어도 옛 표가 그대로 남는다(빈 표가 되지 않는다).
    del_sql, del_params = _delete_stmt(since, cutoff, whole_dates)
    with con.cursor() as cur:
        cur.execute(del_sql, del_params)
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
