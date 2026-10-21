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
  남긴다. 분류가 없는 종목(공급처의 구성종목 응답에도 기업 정보 화면에도 분류가 없는 종목 — 구성종목에 없는 상장 보통주는 분류
  저장분이 기업 정보 화면의 산업으로 채우고, 우선주는 같은 회사 보통주 분류를 잇는다, 261018)은 버리지 않고 `_UNCLASSIFIED` 버킷에 남긴다 —
  조용히 빠지면 섹터 합이 시장 합보다 작은 이유를 아무도 모른다.

★ 평소에는 최신 주만 다시 적재한다. 외부 API 호출은 없어도 전 구간 DELETE·INSERT가 매주
  큰 WAL과 통계 누적을 만들기 때문이다. 새 업종분류 관측이 들어오면 그 관측일부터만 다시 계산한다.
  과거 날짜에는 날짜 이하 최신 스냅샷을 쓰므로 새 관측 이전 구간은 바뀌지 않는다. 원천 정정처럼
  전 구간을 다시 만들 필요가 있을 때만 `--full`을 쓴다. 단 옛 스냅샷의 파생 행이 바뀌면(`refresh_sector_class.py
  --rederive`·옛 날짜 재수집, 261018) 평소 실행은 그것을 알아채지 못한다 — 그 스냅샷이 덮는 구간을 `--since` 로 다시 집계한다
  (가장 이른 스냅샷은 그 이전 구간에 소급되므로 보존 하한부터).

★ UPSERT 만으로는 부족하다 — 어떤 버킷이 비면(그 날 그 업종 종목이 0) 새로 넣을 행이 없어
  **옛 행이 그대로 남는다.** 그러면 섹터 합이 시장 합보다 커진다. 그래서 한 트랜잭션 안에서
  대상 구간을 지우고 다시 넣는다. 파생 100%(krx_weekly × 분류 스냅샷)라 되살릴 원천이 항상
  있고, 같은 실행에서 만든 값으로 채우므로 260705 의 DELETE 사고와 형태가 다르다.

★ 🔴 DB 에는 3년 창만 쓴다 (261021 — 종전 261017 「하한 이전은 다시 쓰지 않는다」를 넓혔다). 보존 하한 = `krx_weekly_cutoff(닫힌 주 최신)`.
  `krx_weekly` 는 최근 3년 + 종목별 연말 행만 남기고(`open_proxy_mcp/krx_retention.py`), 이 표도 3년 밖 행은 콜드 보관본으로 옮긴다
  (서버는 3년 이음매 앞을 서빙본 `krx_cap_agg_hist` 에서 읽는다 — `price_multiple_data._read_agg`). 그래서
  · 지우기·넣기는 언제나 **하한 이후**만이다(`_delete_stmt`). `--since`/`--full` 이 하한 이전을 가리키면 거절하고,
    `--allow-before-cutoff` 를 주면 하한으로 올려 진행한다(하한 이전은 DB 에 쓰지 않는다). 최초 적재도 하한부터다.
  · 하한 이전을 다시 내는 길은 콜드 서빙본(하한 이전 주간 시세 전 행 sqlite)뿐이다 — `--cold-sqlite PATH [--out 파일.parquet]`.
    결과는 DB 가 아니라 Parquet(칸·형 = 이 표)로 낸다. 다시 낸 시장 행이 대조 기준(DB 에 남은 하한 이전 시장 행, 없으면
    `--baseline-parquet` 지금 보관본)과 전부 같을 때만 쓰고(원천 확인), 업종 합 = 시장 합을 확인한다. 올리기·서빙본 발행은 저장소 밖.

★ 이번 주 최신 점은 일별 시세 저장분에서 온다 (261018, 재설계 5-1 쓰는 쪽). `krx_weekly` 는 닫힌 주만 담게 되어 주중에는
  지난 금요일이 최신이다. 원천을 「닫힌 주 + 일별 최신 거래일」(`krx_daily.WEEKLY_WITH_LATEST`)로 읽어 종전과 같은 모양
  (주마다 그 주 마지막 거래일, 이번 주는 최신 거래일)을 유지한다. 이번 주 임시 점은 그 주가 닫히면 다음 실행이 지우고 닫힌 날로 바꾼다.
  재계산 시작은 「원천 최신 주」와 「집계 표의 최신 점이 속한 주」 중 이른 쪽의 첫날이다 — 배치가 며칠 걸러도(또는 임시 점이 남아도)
  그 사이 주를 놓치지 않는다. 한 주 = ISO 주를 달력 연도로 자른 것(`krx_retention.week_unit`).

실행: python3 scripts/krx_cap_agg.py [--since 20231016 | --full] [--allow-before-cutoff]
      python3 scripts/krx_cap_agg.py --cold-sqlite <서빙본.sqlite> [--out <파일.parquet>] [--baseline-parquet <보관본.parquet>]
                                       # 하한 이전 시장·업종 행을 Parquet 로(261021 — DB 쓰기 없음, --out 없으면 대조만)
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

from open_proxy_mcp.krx_daily import LATEST_DAY_SQL, WEEKLY_WITH_LATEST, weekly_is_view
from open_proxy_mcp.krx_retention import WEEKLY_RANGE_SQL, krx_weekly_cutoff, week_unit

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


def _guard_since(*, since: str, reason: str, cutoff: str, allow: bool) -> tuple[bool, str, str]:
    """(진행 여부, 안내, 실제 시작일). DB 에는 하한(`cutoff`) 이후만 쓴다 — 시작일이 하한보다 이르면

    · 「최초 적재」(지울 집계가 없다)·`--allow-before-cutoff` 는 하한으로 올려 진행한다(하한 이전은 DB 에 쓰지 않는다 — 3년 창).
    · 그 밖(`--since`·`--full`)은 거절한다 — 하한 이전을 다시 내는 길은 `--cold-sqlite … --out`(Parquet) 이다.
    """
    if since >= cutoff:
        return True, "", since
    if reason == "최초 적재" or allow:
        why = "최초 적재" if reason == "최초 적재" else "--allow-before-cutoff"
        return True, (f"⚠ {why} — 시작 {since} 를 보존 하한({cutoff})으로 올린다. DB 에는 3년 창만 쓴다 — 하한 이전 집계는 "
                      "콜드 보관본 몫이다(다시 내려면 --cold-sqlite <서빙본> --out <파일.parquet>)."), cutoff
    return False, (f"🔴 {reason}({since})이 보존 하한({cutoff})보다 이르다 — DB 에는 3년 창(하한 이후)만 쓴다. 거절한다. "
                   "하한 이후만 다시 내려면 --allow-before-cutoff(하한으로 올린다), 하한 이전은 "
                   "--cold-sqlite <서빙본> --out <파일.parquet>(DB 쓰기 없음)."), since


def _delete_stmt(since: str, cutoff: str) -> tuple[str, tuple]:
    """다시 넣을 구간의 옛 집계를 지우는 문장 — **언제나 하한 이후만**(261021). 하한 이전 행(옮기기 전 남은 행)은 건드리지 않는다.

    시작일 이후를 전부 지운다(버킷이 비면 옛 행이 남지 않게 — 종전과 같다). 시작일이 하한보다 이르게 와도 하한에서 자른다."""
    return "DELETE FROM krx_cap_agg WHERE price_dd >= %s", (max(since, cutoff),)


# ─────────────────────────────────────────────────────────────────────────────
# 하한 이전 시장·업종 집계를 콜드 서빙본으로 다시 내 Parquet 로 (261019 신설 · 261021 출력을 DB → Parquet, `--cold-sqlite`).
# DB krx_weekly 는 하한 이전을 종목별 연말 행만 남기고 이 표도 3년 밖 행을 콜드 보관본으로 옮긴다. 콜드 서빙본(서버가 3년 이전 주간 곡선을 잇는
# 같은 sqlite — `open_proxy_mcp.cold_store`, 뷰 `krx_weekly`)은 하한 이전 **전 행**이라 그 구간의 모든 주를 지금 분류 규칙으로 다시 낼 수 있다.
# 규칙은 Q_MARKET·Q_SECTOR 와 같다(mktcap > 0 · 그 날짜 이하 최신 스냅샷, 없으면 가장 이른 것 · 분류 없음 = _UNCLASSIFIED/「미분류」).
# 안전장치: 다시 낸 **시장 행이 대조 기준과 하나라도 다르면 거절**한다(원천이 저장 당시와 같다는 증거 — 날짜·종목 수·시총 합 전부).
#   대조 기준 = DB 에 남은 하한 이전 시장 행(옮기기 전), 없으면 `--baseline-parquet`(지금 보관본 — 옮긴 뒤). 둘 다 없으면 거절.
# 결과는 DB 에 쓰지 않는다 — `--out` Parquet(칸·형 = 이 표, 시장 행 포함 전 행)로 내고 업종 합 = 시장 합을 확인한다.
# 올리기·서빙본 발행(발행 전 검사)은 저장소 밖 운영 도구 몫이다.
# ─────────────────────────────────────────────────────────────────────────────
#: Parquet 칸 = DB 표 칸(이름·순서), 형은 duckdb 이름.
PARQUET_COLS = [("price_dd", "VARCHAR"), ("market", "VARCHAR"), ("scheme", "VARCHAR"), ("bucket", "VARCHAR"),
                ("label", "VARCHAR"), ("n", "INTEGER"), ("cap", "BIGINT"), ("sector_asof", "VARCHAR")]


def _snap_for(snaps: list[str], dd: str) -> str:
    """`_eras` 와 같은 규칙 — 가장 이른 스냅샷은 그 이전 날짜까지 소급하고, 그 뒤는 날짜 이하 최신."""
    use = snaps[0]
    for s in snaps:
        if s <= dd:
            use = s
    return use


def cold_sector_rows(weekly, snaps: list[str], cls: dict[str, dict[str, tuple]]):
    """weekly = [(price_dd, ticker, market, mktcap)], cls = {snap: {ticker: (sector_code, sector, industry_code, industry)}}
    → (시장 {(날짜, 시장): [종목 수, 시총]}, 업종 {(날짜, 시장, scheme, bucket): [종목 수, 시총, 이름, 쓴 스냅샷]})."""
    from collections import defaultdict
    mkt: dict[tuple, list] = defaultdict(lambda: [0, 0])
    sec: dict[tuple, list] = defaultdict(lambda: [0, 0, None, None])
    for dd, t, m, cap in weekly:
        if cap is None or cap <= 0:
            continue
        mkt[(dd, m)][0] += 1
        mkt[(dd, m)][1] += int(cap)
        snap = _snap_for(snaps, dd)
        k = cls.get(snap, {}).get(t)
        for scheme, ci, ni in (("wics_sector", 0, 1), ("wics_industry", 2, 3)):
            code = k[ci] if k and k[ci] is not None else "_UNCLASSIFIED"
            name = k[ni] if k and k[ni] is not None else "미분류"
            r = sec[(dd, m, scheme, code)]
            r[0] += 1
            r[1] += int(cap)
            r[2], r[3] = name, snap
    return dict(mkt), dict(sec)


def sums_match(mkt: dict, sec: dict) -> list[tuple]:
    """업종 합 ≠ 시장 합인 (날짜, 시장, scheme) — 비어 있어야 한다(미분류를 버리지 않으므로 항등)."""
    from collections import defaultdict
    tot: dict[tuple, list] = defaultdict(lambda: [0, 0])
    for (d, m, scheme, _b), v in sec.items():
        tot[(d, m, scheme)][0] += v[0]
        tot[(d, m, scheme)][1] += v[1]
    bad = [k for k, v in tot.items() if v != list(mkt.get(k[:2], (None, None)))]
    bad += [(d, m, scheme) for (d, m) in mkt for scheme in ("wics_sector", "wics_industry") if (d, m, scheme) not in tot]
    return sorted(bad)


def cold_rebuild(con, sqlite_path: str, cutoff: str, snaps: list[str], out: str | None, baseline: str | None) -> int:
    """하한(`cutoff`) 이전 시장·업종 행을 콜드 서빙본으로 다시 집계해 `out` Parquet 로. `out` 이 없으면 대조만. 반환 = 종료 코드. DB 쓰기 없음."""
    import hashlib
    import sqlite3
    from urllib.parse import quote
    from scripts import hist_parquet
    sha = hashlib.sha256(Path(sqlite_path).read_bytes()).hexdigest()
    lite = sqlite3.connect(f"file:{quote(str(sqlite_path))}?mode=ro&immutable=1", uri=True)
    try:
        weekly = lite.execute("SELECT price_dd, ticker, market, mktcap FROM krx_weekly WHERE price_dd < ?", (cutoff,)).fetchall()
    finally:
        lite.close()
    cls: dict[str, dict[str, tuple]] = {}
    for snap, t, sc, sn, ic, inn in con.execute(
            "SELECT snap_dd, ticker, sector_code, sector, industry_code, industry FROM wise_sector"):
        cls.setdefault(snap, {})[t] = (sc, sn, ic, inn)
    con.rollback()                                     # 읽기만 했다 — 열린 트랜잭션을 닫는다
    mkt, sec = cold_sector_rows(weekly, snaps, cls)
    stored = [(d, m, s, b, n, cap) for d, m, s, b, n, cap in con.execute(
        "SELECT price_dd, market, scheme, bucket, n, cap FROM krx_cap_agg WHERE price_dd < %s", (cutoff,))]
    con.rollback()
    where = f"DB 에 남은 하한 이전 행 {len(stored):,}"
    if not stored and baseline:
        stored = [r for r in hist_parquet.read(baseline, ["price_dd", "market", "scheme", "bucket", "n", "cap"]) if r[0] < cutoff]
        where = f"보관본 Parquet {Path(baseline).name} 의 하한 이전 행 {len(stored):,}"
    stored_m = {(d, m): [n, cap] for d, m, s, _b, n, cap in stored if s == "market"}
    stored_s = {(d, m, s, b): (n, cap) for d, m, s, b, n, cap in stored if s != "market"}
    dates = sorted({d for d, _ in mkt})
    print(f"콜드 서빙본 sha256 {sha[:16]} · 하한 {cutoff} 이전 {len(weekly):,}행 · 날짜 {len(dates)}개"
          + (f"({dates[0]}~{dates[-1]})" if dates else "") + f" · 업종분류 스냅샷 {snaps} · 대조 기준: {where}", flush=True)
    if not stored_m:
        print("🔴 거절 — 시장 행 대조 기준이 없다(DB 에 하한 이전 시장 행이 없으면 --baseline-parquet 로 지금 보관본을 준다). "
              "아무것도 쓰지 않았다.", file=sys.stderr)
        return 2
    bad_m = sorted(k for k in set(mkt) | set(stored_m) if mkt.get(k) != stored_m.get(k))
    print(f"시장 행: 다시 {len(mkt):,} · 기준 {len(stored_m):,} · 다른 행 {len(bad_m)}", flush=True)
    if bad_m or not mkt:
        for k in bad_m[:5]:
            print(f"   {k} 다시 {mkt.get(k)} 기준 {stored_m.get(k)}")
        print("🔴 거절 — 다시 낸 시장 행이 기준과 다르다(원천이 저장 당시와 같다는 확인 실패). 아무것도 쓰지 않았다.", file=sys.stderr)
        return 2
    bad = sums_match(mkt, sec)
    if bad:
        print(f"::error:: 업종 합 ≠ 시장 합 — {bad[:5]} · 아무것도 쓰지 않았다", file=sys.stderr)
        return 1
    changed = [k for k in set(sec) | set(stored_s) if tuple(sec.get(k, (None, None))[:2]) != tuple(stored_s.get(k, (None, None)))]
    unc = lambda rows: sum(v[1] for k, v in rows.items() if k[2] == "wics_sector" and k[3] == "_UNCLASSIFIED")  # noqa: E731
    print(f"업종 행: 다시 {len(sec):,} · 기준 {len(stored_s):,} · 다른 키 {len(changed):,} · 날짜 {len({k[0] for k in changed})}개 · "
          f"미분류 시총 합(대분류, 전 날짜) {unc(stored_s):,} → {unc(sec):,} · 업종 합 = 시장 합 확인", flush=True)
    if not out:
        print("대조만 — Parquet 로 내려면 --out <파일.parquet> (DB 에는 쓰지 않는다)")
        return 0
    rows = sorted([(d, m, "market", "_ALL", "전체", v[0], v[1], None) for (d, m), v in mkt.items()]
                  + [(d, m, s, b, v[2], v[0], v[1], v[3]) for (d, m, s, b), v in sec.items()])
    info = hist_parquet.write(out, PARQUET_COLS, rows)
    print(f"Parquet: {Path(out).name} · {info['rows']:,}행 · {info['bytes'] / 1048576:.2f} MiB · sha256 {info['sha256'][:16]} "
          f"(하한 {cutoff} 이전 · 시장 {len(mkt):,} + 업종 {len(sec):,}) — DB 쓰기 없음. 올리기·서빙본 발행은 운영 도구 몫.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--since", help="이 날짜부터 집계 (YYYYMMDD)")
    mode.add_argument("--full", action="store_true", help="krx_weekly 전 구간을 다시 집계")
    mode.add_argument("--cold-sqlite", metavar="PATH",
                      help="하한 이전 시장·업종 행을 콜드 서빙본(sqlite, 뷰 krx_weekly)으로 다시 집계 — 기본은 대조만, "
                           "--out 이면 Parquet 로 낸다(DB 쓰기 없음, 261021)")
    ap.add_argument("--allow-before-cutoff", action="store_true",
                    help="시작일이 보존 하한(최신 주 3년 전)보다 이르면 하한으로 올려 진행 — DB 에는 3년 창만 쓴다(261021)")
    ap.add_argument("--out", metavar="PARQUET", help="--cold-sqlite 결과를 쓸 Parquet 경로(없으면 대조만)")
    ap.add_argument("--baseline-parquet", metavar="PARQUET",
                    help="--cold-sqlite 의 시장 행 대조 기준 — DB 에 하한 이전 시장 행이 없을 때(옮긴 뒤) 지금 보관본 Parquet")
    a = ap.parse_args()
    if (a.out or a.baseline_parquet) and not a.cold_sqlite:
        ap.error("--out·--baseline-parquet 는 --cold-sqlite 와 함께만 쓴다")

    con = psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=30)
    con.autocommit = True
    con.execute(DDL)
    con.execute(IDX)
    con.autocommit = False   # 이하 삭제+삽입은 한 트랜잭션

    snaps = [r[0] for r in con.execute("SELECT DISTINCT snap_dd FROM wise_sector ORDER BY snap_dd")]
    if not snaps:
        print("분류 스냅샷 표가 비어 있음 — refresh_sector_class.py 를 먼저 돌린다")
        return 1

    # 261021(감사 B3): 뷰 모드면 가장 이른·늦은 날짜를 33.9만 행 뷰 대신 날짜 달력에서 읽는다(같은 답 — `krx_retention.WEEKLY_DAYS_SQL`). 표 모드는 종전 그대로.
    source_min, weekly_max = con.execute(
        WEEKLY_RANGE_SQL if weekly_is_view(con) else "SELECT min(price_dd), max(price_dd) FROM krx_weekly").fetchone()
    if not source_min:
        print("krx_weekly 비어 있음 — 먼저 시세를 적재한다")
        return 1
    if a.cold_sqlite:
        rc = cold_rebuild(con, a.cold_sqlite, krx_weekly_cutoff(weekly_max), snaps, a.out, a.baseline_parquet)
        con.close()
        return rc
    # 원천 최신 = 닫힌 주와 일별 최신 거래일 중 늦은 쪽(이번 주 임시 점). 보존 하한은 표를 자르는 쪽과 같게 krx_weekly 최신에서 잰다.
    daily_max = con.execute(LATEST_DAY_SQL).fetchone()[0]          # 261019: 일별 최신일 = 달력(krx_day)
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
    ok, note, since = _guard_since(since=since, reason=reason, cutoff=cutoff, allow=a.allow_before_cutoff)
    if not ok:
        print(note, file=sys.stderr)
        con.close()
        return 2
    print(f"재계산 시작 {since} · {reason} (보존 하한 {cutoff} · 닫힌 주 최신 {weekly_max} · 일별 최신 {daily_max})", flush=True)
    if note:
        print(note, flush=True)

    # 261021: 여기서부터는 언제나 하한 이후(`_guard_since` 가 시작일을 하한 이상으로 맞췄다) — 원천 날짜 제한이 필요 없다.
    rows: list[tuple] = []
    for dd, mkt, n, cap in con.execute(Q_MARKET.format(src=""), (since,)):
        rows.append((dd, mkt, "market", "_ALL", "전체", n, cap, None))
    print(f"시장 집계 {len(rows):,}행", flush=True)

    eras = _eras(snaps, since)
    for scheme, code, name in (("wics_sector", "sector_code", "sector"),
                               ("wics_industry", "industry_code", "industry")):
        before = len(rows)
        for snap, start, end in eras:
            q = Q_SECTOR.format(code=code, name=name, src="")
            for dd, mkt, bucket, label, n, cap in con.execute(q, (snap, start, end)):
                rows.append((dd, mkt, scheme, bucket, label, n, cap, snap))
        print(f"{scheme} 집계 {len(rows)-before:,}행 (스냅샷 시대 {len(eras)}개)", flush=True)

    # 지우고-넣기를 한 트랜잭션으로. 중간에 죽어도 옛 표가 그대로 남는다(빈 표가 되지 않는다).
    del_sql, del_params = _delete_stmt(since, cutoff)
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
    # 261021: 검사 범위는 **DB 3년 창(하한 이후)** 이다 — 이 배치가 쓰는 곳이 거기뿐이다. 3년 밖(콜드 보관본) 쪽 같은 검사는
    #   `--cold-sqlite … --out` 이 Parquet 를 내기 전에, 그리고 서빙본 발행기의 발행 전 검사가 맡는다.
    bad = con.execute("""
        SELECT m.price_dd, m.market, s.scheme, m.cap, s.cap FROM
          (SELECT price_dd, market, cap FROM krx_cap_agg WHERE scheme='market' AND price_dd >= %s) m
          JOIN (SELECT price_dd, market, scheme, sum(cap) cap FROM krx_cap_agg
                WHERE scheme<>'market' AND price_dd >= %s GROUP BY 1,2,3) s
            ON s.price_dd=m.price_dd AND s.market=m.market
        WHERE s.cap <> m.cap LIMIT 5""", (cutoff, cutoff)).fetchall()
    if bad:
        print(f"\n::error:: 섹터 합 ≠ 시장 합(DB 3년 창, {cutoff}~) — 표본 {bad}")
        return 1
    print(f"무결성 OK — 섹터 합 == 시장 합 (DB 3년 창 {cutoff}~ 전 시점·전 스킴 · 3년 밖은 보관본 발행 전 검사)")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
