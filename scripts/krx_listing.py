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
창 이후 첫 관측은 주간 해상도라 실제 상장일보다 최대 6일 늦을 수 있다(배치가 매일 일별 최신일을 함께 보므로 보통은 그날이다). 연 단위로 쓰는 한
문제가 되지 않지만, 12월 마지막 주 상장은 해가 넘어갈 수 있다(실측 1건: 490470 —
원장 20251229 vs 주간 20260102). 일 단위 판단에는 쓰지 않는다.

■ 🔴 증분으로 굽는다 — 옛 구간은 다시 계산할 수 없다 (261017)
`krx_weekly` 는 최근 3년 + 종목별로 해마다 마지막 행(연말 행)만 남기도록 줄인다(`open_proxy_mcp/krx_retention.py`).
종전 로직은 매일 표 **전체**를 `GROUP BY ticker` 로 훑어(실측 3~20초) 값을 통째로 덮어썼다.
표를 자르면 그 계산은 옛 주를 못 보고 **연말 행 한 점씩만** 본다 — 실측(261017, 잘린 표 모의): 857종목의
`first_seen_dd` 가 진짜 첫 관측일에서 그 해 마지막 행으로 최대 357일(중앙값 133일) 밀린다(`last_seen_dd`·`market`·`before_window`
는 그대로 — 20151230 행도 종목별 연말 행으로 남는다).
옛 주는 지워졌으니 한 번 틀려 덮이면 되돌릴 원천이 없다.
그래서:
  · `first_seen_dd = LEAST(기존, 새 관측)` · `last_seen_dd = GREATEST(기존, 새 관측)` — 기존 값은 **줄이기만** 한다.
  · 관측창 시작은 `min(price_dd)` 가 아니라 상수 `KRX_WEEKLY_ORIGIN_DD`(20151230) 다.
  · 새 관측은 **최근 주만** 읽는다 — 직전 구축 주에서 8주 전부터. 전량 `GROUP BY` 없음.
  · 표가 비어 있는데 `krx_weekly` 가 이미 잘렸으면 **처음부터 만들 수 없다** — 거절한다(백업에서 복원).
    (잘렸는지는 표를 자르는 쪽과 같은 SQL 조각 `prune_where` 로 본다 — 지워질 행이 없으면 잘린 것이다.)

용례:
  python3 scripts/krx_listing.py            # 증분 갱신(최근 주만 읽어 합친다)
  python3 scripts/krx_listing.py --dry      # 저장 없이 산출만(세션을 읽기 전용으로 건다)
  python3 scripts/krx_listing.py --compare  # 읽기 전용 — 옛 전량 계산과 새 증분 결과가 같은지 대조(0건 차이여야 한다 · 자르기 전에만)
  python3 scripts/krx_listing.py --compare --stale-weeks 8   # 표가 8주 뒤처진 상태를 모의해 신규 상장·종료일 갱신까지 대조
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
import psycopg

from open_proxy_mcp.krx_retention import KRX_WEEKLY_ORIGIN_DD, krx_weekly_cutoff, prune_where

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
#: 실측 3,257종목 — 절반 아래로 떨어질 이유가 없다. (증분에서는 「최근 8주에 보인 종목 수」에 건다.)
MIN_TICKERS = 1_500

#: 직전 구축 주에서 이만큼 더 거슬러 읽는다 — 정정·지연 적재(금요일 데이터가 늦게 올라오는 경우)를 흡수한다.
OVERLAP_DAYS = 56

#: 관측창 시작 — 표의 `window_lo`·`before_window` 기준. `min(price_dd)` 를 읽지 않는다.
WINDOW_LO = KRX_WEEKLY_ORIGIN_DD

#: 한 종목 = (market, first_seen_dd, last_seen_dd). market 은 **마지막 관측** 것이다 —
#: 이전(코스닥→코스피)한 종목은 지금 있는 시장이 답이다.
Row = tuple[str | None, str, str]


class WindowError(RuntimeError):
    """증분으로 만들 수 없는 상태 — 호출부가 메시지를 그대로 보여 주고 실패로 끝낸다."""


#: 새 관측 = 최근 주간 행 + 일별 시세 저장분 — 261018(재설계 5-1 쓰는 쪽). krx_weekly 는 닫힌 주만 담게 되어
#: 이번 주에 새로 상장·폐지한 종목은 주가 닫힐 때까지 krx_weekly 에 없다. 종전엔 매일 이번 주 행(그날 최신일)이 있어 관측이 그날
#: 바로 반영됐다 — 일별 저장분을 함께 읽어 그대로 둔다(첫·끝 관측은 LEAST/GREATEST 로 합쳐지므로 더 촘촘한 날짜가 이긴다).
#: 261019(재설계 v03): 일별 저장분이 약 1년이 되면서 호환 뷰(`krx_daily`)에 날짜 하한을 걸면 하한이 달력 쪽에 걸려 사실값 표 전체를
#: 훑는다 — 사실값 표(`krx_px`)의 날짜 칸에 직접 하한을 걸어 날짜 범위 요약 색인(BRIN)으로 같은 하한 이후만 읽는다(같은 행·같은 칸).
Q_RECENT = """
SELECT w.ticker, (array_agg(w.market ORDER BY w.price_dd DESC))[1] AS market,
       min(w.price_dd) AS first_dd, max(w.price_dd) AS last_dd
  FROM (SELECT price_dd, ticker, market FROM krx_weekly WHERE price_dd >= %(start)s
        UNION ALL
        SELECT to_char(p.trd_dd, 'YYYYMMDD'), k.ticker, CASE p.market WHEN 'S' THEN 'KS' WHEN 'Q' THEN 'KQ' END
          FROM krx_px p JOIN krx_ticker k ON k.tid = p.tid
         WHERE p.trd_dd >= to_date(%(start)s, 'YYYYMMDD')) w
 GROUP BY w.ticker
"""

#: 종전(전량) 계산 — `--compare` 의 기준값. 일상 경로에서는 부르지 않는다.
Q_FULL = """
SELECT w.ticker, (array_agg(w.market ORDER BY w.price_dd DESC))[1] AS market,
       min(w.price_dd) AS first_dd, max(w.price_dd) AS last_dd
  FROM krx_weekly w
 GROUP BY w.ticker
"""

#: `--stale-weeks` 모의용 — 그 날짜까지의 `krx_weekly` 만으로 종전 방식대로 구운 표(=그때 시점의 표).
Q_FULL_UPTO = """
SELECT w.ticker, (array_agg(w.market ORDER BY w.price_dd DESC))[1] AS market,
       min(w.price_dd) AS first_dd, max(w.price_dd) AS last_dd
  FROM krx_weekly w
 WHERE w.price_dd <= %s
 GROUP BY w.ticker
"""

#: 기존 값은 줄이기만 한다(LEAST/GREATEST). 호출부가 이미 합쳐서 넘기므로 정상 경로에서는 같은 값이지만,
#: 합치는 코드가 틀려도 옛 `first_seen_dd` 를 뒤로 미는 쓰기는 이 문장이 막는다.
UPSERT = """
INSERT INTO krx_listing
  (ticker, market, first_seen_dd, last_seen_dd, before_window, window_lo, built_at)
VALUES (%s,%s,%s,%s,%s,%s, now())
ON CONFLICT (ticker) DO UPDATE SET
  market        = EXCLUDED.market,
  first_seen_dd = LEAST(krx_listing.first_seen_dd, EXCLUDED.first_seen_dd),
  last_seen_dd  = GREATEST(krx_listing.last_seen_dd, EXCLUDED.last_seen_dd),
  before_window = (LEAST(krx_listing.first_seen_dd, EXCLUDED.first_seen_dd) = EXCLUDED.window_lo),
  window_lo     = EXCLUDED.window_lo,
  built_at      = now()
"""


# ── 순수 로직 (DB 없음 — 테스트 대상) ──────────────────────────────────────────

def _minus_days(dd: str, days: int) -> str:
    d = date(int(dd[:4]), int(dd[4:6]), int(dd[6:8])) - timedelta(days=days)
    return f"{d.year:04d}{d.month:02d}{d.day:02d}"


def plan_window(latest_dd: str, built_last_seen: str | None,
                table_cut: bool | None = None) -> tuple[str, str]:
    """(읽기 시작일, 사유). 증분이 안 되는 상태면 `WindowError`.

    · 표가 비었다(`built_last_seen is None`) → 처음부터 만든다. 그런데 `krx_weekly` 가 이미 잘렸으면(`table_cut`)
      첫 관측일을 복원할 수 없다 — 잘린 표로 만든 `first_seen_dd` 는 그 종목의 첫 연말 행으로 밀린 거짓이다.
      `table_cut` 은 표가 비었을 때만 호출부가 `_table_is_cut` 으로 구해 넘긴다(모르면 None → 거절).
    · 아니면 직전 구축 주에서 `OVERLAP_DAYS` 전부터 읽는다. 그 시작이 보존 하한보다 오래면(구축이 3년 가까이
      멎었다) 사이의 주가 이미 지워져 새 상장 종목의 첫 주를 놓친다 — 거절한다.
    """
    cutoff = krx_weekly_cutoff(latest_dd)
    if built_last_seen is None:
        if table_cut is None or table_cut:
            raise WindowError(
                "krx_listing 이 비어 있는데 krx_weekly 는 이미 보존 하한 이전을 잘랐다 "
                f"(하한 {cutoff}) — 종목별 첫 관측일을 처음부터 다시 만들 수 없다. "
                "백업(Parquet)에서 krx_listing 을 복원하거나, 자르기 전 표로 만든다.")
        return WINDOW_LO, "표가 비어 처음부터(전 구간)"
    start = _minus_days(built_last_seen, OVERLAP_DAYS)
    if start < cutoff:
        raise WindowError(
            f"마지막 구축 주({built_last_seen})에서 {OVERLAP_DAYS // 7}주 전({start})이 보존 하한({cutoff})보다 "
            "오래다 — 그 사이 주가 이미 지워져 새 상장 종목의 첫 주를 놓친다. 손으로 확인한 뒤 표를 복원한다.")
    return start, f"직전 구축 주 {built_last_seen} 에서 {OVERLAP_DAYS // 7}주 전부터"


def merge_one(old: Row | None, obs: Row, window_lo: str = WINDOW_LO) -> tuple[str | None, str, str, bool]:
    """기존 행과 새 관측을 합친다 — (market, first_seen, last_seen, before_window).

    first=LEAST · last=GREATEST(기존 값은 줄이기만 한다). market 은 더 늦게 관측된 쪽.
    `before_window` 는 합친 first_seen 이 관측창 시작과 같을 때만 참이다.
    """
    if old is None:
        market, first, last = obs
    else:
        first = min(old[1], obs[1])
        last = max(old[2], obs[2])
        market = obs[0] if obs[2] >= old[2] else old[0]
    return market, first, last, first == window_lo


def merge_rows(existing: dict[str, Row], observed: dict[str, Row],
               window_lo: str = WINDOW_LO) -> tuple[dict[str, tuple], dict[str, int]]:
    """관측된 종목만 합쳐 **바뀐/새 행**을 돌려준다(+ 건수). 관측되지 않은 종목(상장폐지)은 그대로다."""
    changed: dict[str, tuple] = {}
    n_new = n_changed = n_same = 0
    for ticker, obs in observed.items():
        old = existing.get(ticker)
        merged = merge_one(old, obs, window_lo)
        if old is None:
            n_new += 1
            changed[ticker] = merged
        elif merged[:3] != tuple(old):
            n_changed += 1
            changed[ticker] = merged
        else:
            n_same += 1
    return changed, {"new": n_new, "changed": n_changed, "same": n_same}


def overlay(existing: dict[str, Row], changed: dict[str, tuple]) -> dict[str, tuple]:
    """적용 후 표 전체 — (market, first, last, before_window). 비교·요약용."""
    out = {t: (m, f, l, f == WINDOW_LO) for t, (m, f, l) in existing.items()}
    out.update(changed)
    return out


# ── DB 접근 ────────────────────────────────────────────────────────────────────

def _load_existing(con) -> dict[str, Row]:
    return {t: (m, f, l) for t, m, f, l in con.execute(
        "SELECT ticker, market, first_seen_dd, last_seen_dd FROM krx_listing").fetchall()}


def _observe(con, sql: str, params: tuple = ()) -> dict[str, Row]:
    return {t: (m, f, l) for t, m, f, l in con.execute(sql, params).fetchall()}


def _latest_dd(con) -> str | None:
    return con.execute("SELECT max(price_dd) FROM krx_weekly").fetchone()[0]


def _table_is_cut(con, latest_dd: str) -> bool:
    """`krx_weekly` 가 이미 잘렸나 — 정리 때 지워질 행이 하나도 없으면 True. 표가 비었을 때(처음부터 굽기 직전)만 부른다.

    종목별 연말 행 보존이라 날짜 목록만으로는 판단할 수 없어 표를 자르는 쪽과 **같은 SQL 조각**(`prune_where`)을 쓴다."""
    where, params = prune_where(latest_dd, alias="w")
    return bool(con.execute(
        f"SELECT NOT EXISTS (SELECT 1 FROM krx_weekly w WHERE {where})", params).fetchone()[0])


def build(con, *, dry: bool = False) -> int:
    """증분 갱신. dry 면 쓰지 않는다(DDL 도 안 건다)."""
    latest = _latest_dd(con)
    if not latest:
        print("🔴 krx_weekly 가 비었다 — 굽지 않는다", file=sys.stderr)
        return 1

    if not dry:
        for stmt in DDL.strip().split(";"):
            if stmt.strip():
                con.execute(stmt)
        con.commit()
    try:
        existing = _load_existing(con)
    except psycopg.errors.UndefinedTable:
        if not dry:
            raise
        existing = {}      # dry 인데 표가 아직 없다 — 빈 표로 본다
        con.rollback()

    built_last = max((r[2] for r in existing.values()), default=None) if existing else None
    cut = _table_is_cut(con, latest) if built_last is None else None
    try:
        start, why = plan_window(latest, built_last, cut)
    except WindowError as exc:
        print(f"🔴 {exc}", file=sys.stderr)
        return 1

    t0 = time.time()
    observed = _observe(con, Q_RECENT, {"start": start})
    if len(observed) < MIN_TICKERS:
        print(f"🔴 {start} 이후에 보인 종목이 {len(observed)}개뿐이다(하한 {MIN_TICKERS}) — 상류 이상으로 보고 덮지 않는다",
              file=sys.stderr)
        return 1

    changed, stats = merge_rows(existing, observed)
    after = overlay(existing, changed)
    n_edge = sum(1 for v in after.values() if v[3])

    print("=== krx_listing ===")
    print(f"  읽은 구간       : {start} ~ {latest}  ({why}) · {time.time() - t0:.1f}s")
    print(f"  관측창          : {WINDOW_LO} ~ {max(v[2] for v in after.values())}")
    print(f"  이번에 본 종목  : {len(observed):,}  (신규 {stats['new']:,} · 갱신 {stats['changed']:,} · 변동 없음 {stats['same']:,})")
    print(f"  종목(적용 후)   : {len(after):,}")
    print(f"  창 이전부터 상장: {n_edge:,}  (🔴 first_seen_dd 를 상장일로 읽으면 안 되는 것들)")
    print(f"  창 안에서 신규  : {len(after) - n_edge:,}")
    if dry:
        print("\n--dry — 저장 생략")
        for t, v in list(changed.items())[:5]:
            print(f"    {t} {v[0]} {v[1]}~{v[2]} before_window={v[3]}")
        return 0

    if changed:
        with con.cursor() as cur:
            cur.executemany(UPSERT, [(t, m, f, l, bw, WINDOW_LO) for t, (m, f, l, bw) in changed.items()])
            cur.execute("GRANT SELECT ON krx_listing TO opm_ro")
        con.commit()
    n = con.execute("SELECT count(*) FROM krx_listing").fetchone()[0]
    print(f"  적재 후         : {n:,}행" + ("" if changed else "  (바뀐 행 없음 — 쓰기 생략)"))
    return 0


def compare(con, stale_weeks: int = 0) -> int:
    """읽기 전용 대조 — 종전 전량 계산(F)과 새 증분 결과(M = 표 + 최근 주 합치기)가 같은가.

    다르면 1. 같으면 0. 현재 표(T)와 F 의 차이는 참고로만 센다(표가 낡은 만큼 — 증분이 메울 몫).
    `stale_weeks=N` 이면 **N주 전 시점의 표를 모의**로 만들어(그때까지의 krx_weekly 만으로 종전 방식대로 구운 것)
    증분 갱신이 신규 상장·종료일 변경까지 합쳐 오늘의 전량 결과와 같아지는지 함께 본다 —
    오늘 표가 이미 최신이면 합치기가 할 일이 없어 0건 차이가 당연하기 때문이다.
    """
    latest = _latest_dd(con)
    if not latest:
        print("🔴 krx_weekly 가 비었다", file=sys.stderr)
        return 1
    if _table_is_cut(con, latest):
        # 잘린 표의 전량 재집계는 옛 구간을 못 봐서(첫 관측일이 연말 행으로 밀린다) 기준이 될 수 없다 — 차이가 나도 정상이다.
        print("🔴 krx_weekly 가 이미 보존 하한 이전을 잘랐다 — 전량 재집계를 기준으로 삼을 수 없어 대조하지 않는다 "
              "(이 대조는 자르기 전에 하는 것이다).", file=sys.stderr)
        return 2
    existing = _load_existing(con)
    cutoff = krx_weekly_cutoff(latest)

    t0 = time.time()
    full = _observe(con, Q_FULL)
    t_full = time.time() - t0
    window_lo_full = con.execute("SELECT min(price_dd) FROM krx_weekly").fetchone()[0]
    full_rows = {t: (m, f, l, f == window_lo_full) for t, (m, f, l) in full.items()}

    def diff(a: dict[str, tuple], b: dict[str, tuple]) -> tuple[int, int, int]:
        n_diff = sum(1 for t in set(a) & set(b) if a[t] != b[t])
        return n_diff, len(set(a) - set(b)), len(set(b) - set(a))

    print("=== krx_listing 대조 (읽기 전용) ===")
    print(f"  종전 로직(전량 GROUP BY)   : {len(full):,}종목 · {t_full:.1f}s · 관측창 시작 min(price_dd)={window_lo_full}")
    print(f"  관측창 상수 == 표의 최솟값 : {WINDOW_LO} == {window_lo_full} → "
          f"{'일치' if WINDOW_LO == window_lo_full else '🔴 불일치'}")
    ok = WINDOW_LO == window_lo_full

    scenarios: list[tuple[str, dict[str, Row]]] = [("현재 표", existing)]
    if stale_weeks > 0:
        d_old = _minus_days(latest, 7 * stale_weeks)
        scenarios.append((f"{stale_weeks}주 전({d_old}) 시점 표(모의)", _observe(con, Q_FULL_UPTO, (d_old,))))

    for label, ex in scenarios:
        built_last = max((r[2] for r in ex.values()), default=None) if ex else None
        cut = None if built_last else _table_is_cut(con, latest)
        try:
            start, why = plan_window(latest, built_last, cut)
        except WindowError as exc:
            print(f"🔴 [{label}] {exc}", file=sys.stderr)
            return 1
        t0 = time.time()
        observed = _observe(con, Q_RECENT, {"start": start})
        n_rows = con.execute("SELECT count(*) FROM krx_weekly WHERE price_dd >= %s", (start,)).fetchone()[0]
        changed, stats = merge_rows(ex, observed)
        t_inc = time.time() - t0
        after = overlay(ex, changed)
        d_new = diff(after, full_rows)
        d_old = diff(overlay(ex, {}), full_rows)
        print(f"\n  ── 출발점: {label} ({len(ex):,}종목) ──")
        print(f"  새 로직(증분)              : {start} ~ {latest} 만 읽음({n_rows:,}행) · {t_inc:.2f}s · "
              f"신규 {stats['new']} · 갱신 {stats['changed']} · 변동 없음 {stats['same']}  [{why}]")
        print(f"  읽은 구간이 보존 대상인가  : 시작 {start} {'>=' if start >= cutoff else '<'} 하한 {cutoff}"
              + (" → 예 — 표를 잘라도 이 읽기는 그대로" if start >= cutoff else " → 🔴 아니오"))
        print(f"  [증분 적용 후 vs 전량 재집계] 값 불일치 {d_new[0]}건 · 적용 후에만 {d_new[1]} · 전량에만 {d_new[2]}")
        print(f"  [출발 표 그대로 vs 전량 재집계] 값 불일치 {d_old[0]}건 · 표에만 {d_old[1]} · 전량에만 {d_old[2]} (표가 낡은 만큼)")
        ok = ok and d_new == (0, 0, 0)
    print("\n  → " + ("0건 차이 — 증분 결과가 종전 전량 결과와 같다" if ok else "🔴 차이 있음"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry", action="store_true", help="저장 없이 산출만(세션을 읽기 전용으로 건다)")
    mode.add_argument("--compare", action="store_true",
                      help="읽기 전용 — 종전 전량 계산과 새 증분 결과가 같은지 대조(0건 차이여야 한다)")
    ap.add_argument("--stale-weeks", type=int, default=0,
                    help="--compare 와 함께: N주 전 시점의 표를 모의로 만들어 증분 갱신 결과까지 대조")
    a = ap.parse_args()

    con = psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=20)
    try:
        if a.dry or a.compare:
            con.execute("SET default_transaction_read_only = on")   # 실수로라도 쓰지 못하게
            con.execute("SET statement_timeout = '120s'")
            con.commit()
        return compare(con, a.stale_weeks) if a.compare else build(con, dry=a.dry)
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
