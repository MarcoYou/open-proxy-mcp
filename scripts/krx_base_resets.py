"""KRX 기준가 리셋 전수 스윕 — 수정주가 조정계수의 원천 (거래소 실측).

원리: 정상 거래일은 `전일종가 == 오늘종가 - 전일대비`. 어긋나는 날 = 거래소가 기준가를
조정한 날(유증 권리락 · 주식배당락 · 무상락 · 분할/병합/감자 변경상장 재기준).
  조정계수 = 기준가 / 전일종가   (기준가 = 종가 - 전일대비)
날짜·크기 모두 거래소 공식값이라 발행가·배정비율 공식 계산이 불필요.
검증: 대한항공 유증락 0.8362 · 셀트리온 주식배당락(5%/2%) · 오리온홀딩스 인적분할 1/20.36 ·
      SKT 분할 1/5.80 — 전부 정확 탐지 (wiki/architecture/adjusted-price-timeseries.md).

KRX Open API 한도: 키당 1일(0~24시) 10,000콜 — 본 스크립트 상한 CALL_CAP(기본 9,000).
전수 스윕 ≈ 2,573거래일 × 2시장 ≈ 5,150콜. 재개 가능(일 단위 커밋, 중단 시 재실행).

실행:
  python scripts/krx_base_resets.py --sweep [--since 20160104] [--cap 9000]
  python scripts/krx_base_resets.py --update      # 최근 30일만 (주간 유지용)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import ssl
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


import httpx
import psycopg

URLS = [("KOSPI", "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"),
        ("KOSDAQ", "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd")]
THROTTLE_S = 0.35
_calls = 0
_fail_streak = 0

DDL = """
CREATE TABLE IF NOT EXISTS krx_base_resets (
  isu_cd text NOT NULL, reset_dd text NOT NULL,
  prev_close double precision, base_price double precision, close double precision,
  factor double precision, mkt text,
  PRIMARY KEY (isu_cd, reset_dd));
CREATE INDEX IF NOT EXISTS idx_base_resets_isu ON krx_base_resets (isu_cd, reset_dd);
CREATE TABLE IF NOT EXISTS krx_reset_sweep_checkpoint (bas_dd text PRIMARY KEY, n_stocks int);
CREATE TABLE IF NOT EXISTS krx_shares_ledger (
  isu_cd text NOT NULL, chg_dd text NOT NULL,
  prev_shrs bigint, new_shrs bigint, mkt text,
  PRIMARY KEY (isu_cd, chg_dd));
"""


def _ssl_ctx():
    """Windows 신뢰저장소(truststore) 우선. KRX_INSECURE=1이면 검증 끔(로컬 프록시 환경 전용)."""
    if os.getenv("KRX_INSECURE") == "1":
        return False
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        return True


def _num(v):
    try:
        return float(str(v).replace(",", "")) if v not in (None, "", "-") else None
    except Exception:
        return None


async def _fetch(h, key, url, day, cap):
    global _calls, _fail_streak
    if _calls >= cap:
        raise RuntimeError(f"CALL_CAP({cap}) 도달 — 일 한도 보호 중단. 재실행 시 이어서.")
    _calls += 1
    try:  # 일별 사용량 장부 (Supabase krx_call_log — 두 PC 합산)
        from open_proxy_mcp.dart.krx_meter import bump
        bump()
    except Exception:
        pass
    await asyncio.sleep(THROTTLE_S)
    try:
        r = await h.get(url, headers={"AUTH_KEY": key}, params={"basDd": day})
        r.raise_for_status()
        _fail_streak = 0
        return next((v for v in r.json().values() if isinstance(v, list)), [])
    except Exception:
        _fail_streak += 1
        if _fail_streak >= 10:
            raise RuntimeError("연속 실패 10회 — KRX 한도/장애 의심, 중단(재개 가능)")
        return None


async def sweep(since: date, cap: int):
    key = os.getenv("KRX_API_KEY") or os.getenv("KRX_OPEN_API_KEY")
    assert key, "KRX_API_KEY 없음 (.env)"
    con = psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=20)
    for stmt in DDL.strip().split(";"):
        if stmt.strip():
            con.execute(stmt)
    con.commit()

    done = {r[0] for r in con.execute("SELECT bas_dd FROM krx_reset_sweep_checkpoint").fetchall()}
    # 재개 시 prev_close 시드: ① 마지막 처리일 재조회 ② 부재 종목은 krx_weekly 최근 종가
    prev_close: dict[str, float] = {}
    # 260823: 주식수 원장(krx_shares_ledger)을 같은 스냅샷에서 함께 적는다 — **KRX 콜 0 추가**.
    #   원장은 writer 가 없어 20260701 에 멎어 있었는데, `krx_stock_flags`(시계열 해석 주의 딱지)가
    #   전적으로 이 원장에서 파생된다(실측: unresolved_adjustment 이벤트 125/125 가 원장 날짜).
    #   원장이 멈추면 딱지도 멈추고, 새로 분할·감자한 종목은 경고 없이 나간다.
    #   여기 붙이는 이유 — 이 스윕이 이미 전종목 일별 스냅샷(LIST_SHRS 포함)을 받아온다.
    prev_shrs: dict[str, int] = {}
    last = max(done) if done else None
    async with httpx.AsyncClient(timeout=30, verify=_ssl_ctx()) as h:
        if last:
            for mkt, url in URLS:
                snap = await _fetch(h, key, url, last, cap) or []
                for x in snap:
                    c = _num(x.get("TDD_CLSPRC"))
                    if c:
                        prev_close[x["ISU_CD"]] = c
            for isu, c in con.execute("""SELECT DISTINCT ON (isu_cd) isu_cd, close FROM krx_weekly
                                         WHERE bas_dd <= %s ORDER BY isu_cd, bas_dd DESC""", (last,)).fetchall():
                prev_close.setdefault(isu, float(c) if c else None)
            # 주식수 원장 시드 — 원장의 마지막 값이 우선, 없으면 krx_weekly 최근 상장주식수
            for isu, n in con.execute("""SELECT DISTINCT ON (isu_cd) isu_cd, new_shrs FROM krx_shares_ledger
                                         WHERE chg_dd <= %s ORDER BY isu_cd, chg_dd DESC""", (last,)).fetchall():
                if n: prev_shrs[isu] = int(n)
            for isu, n in con.execute("""SELECT DISTINCT ON (isu_cd) isu_cd, list_shrs FROM krx_weekly
                                         WHERE bas_dd <= %s AND list_shrs > 0 ORDER BY isu_cd, bas_dd DESC""",
                                      (last,)).fetchall():
                prev_shrs.setdefault(isu, int(n))
            print(f"재개: 처리일 {len(done)} (마지막 {last}) | 시드 종목 {len(prev_close):,}", flush=True)

        days = []
        d = since if not last else date(int(last[:4]), int(last[4:6]), int(last[6:8])) + timedelta(days=1)
        today = date.today()
        while d < today:
            if d.weekday() < 5 and d.strftime("%Y%m%d") not in done:
                days.append(d.strftime("%Y%m%d"))
            d += timedelta(days=1)
        print(f"처리할 평일 {len(days)}일 (CALL_CAP {cap})", flush=True)

        t0 = time.time()
        n_resets = n_days = n_shrs = 0
        try:
            for day in days:
                snaps = []
                empty = False
                for mkt, url in URLS:
                    s = await _fetch(h, key, url, day, cap)
                    if s is None:
                        snaps = None
                        break
                    if mkt == "KOSPI" and not s:
                        empty = True  # 휴장 (시장 전체)
                        break
                    snaps.append((mkt, s))
                if snaps is None:
                    continue  # 일시 오류 — 미기록, 재실행 때 재시도
                if empty:
                    con.execute("INSERT INTO krx_reset_sweep_checkpoint VALUES (%s,0) ON CONFLICT DO NOTHING", (day,))
                    con.commit()
                    n_days += 1
                    continue
                rows, srows, n_st = [], [], 0
                for mkt, snap in snaps:
                    for x in snap:
                        isu = x.get("ISU_CD")
                        close = _num(x.get("TDD_CLSPRC"))
                        chg = _num(x.get("CMPPREVDD_PRC")) or 0.0
                        if not isu or not close:
                            continue
                        n_st += 1
                        base = close - chg
                        pc = prev_close.get(isu)
                        if pc and abs(base - pc) > max(pc * 0.001, 1.0):
                            rows.append((isu, day, pc, base, close, base / pc, mkt))
                        prev_close[isu] = close
                        # 주식수 원장 — 변동한 날만 적는다(원장 기존 규약과 동일).
                        # 첫 등장(prev 없음)은 prev_shrs=NULL 로 기록 — 신규상장 표시.
                        ns = _num(x.get("LIST_SHRS"))
                        if ns:
                            ps = prev_shrs.get(isu)
                            if ps is None or ps != int(ns):
                                srows.append((isu, day, ps, int(ns), mkt))
                            prev_shrs[isu] = int(ns)
                with con.cursor() as cur:
                    if rows:
                        cur.executemany("""INSERT INTO krx_base_resets VALUES (%s,%s,%s,%s,%s,%s,%s)
                                           ON CONFLICT (isu_cd, reset_dd) DO NOTHING""", rows)
                    if srows:
                        cur.executemany("""INSERT INTO krx_shares_ledger
                                           (isu_cd, chg_dd, prev_shrs, new_shrs, mkt)
                                           VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""", srows)
                    cur.execute("INSERT INTO krx_reset_sweep_checkpoint VALUES (%s,%s) ON CONFLICT DO NOTHING", (day, n_st))
                con.commit()
                n_resets += len(rows)
                n_shrs += len(srows)
                n_days += 1
                if n_days % 100 == 0:
                    print(f"  {n_days}일 | 리셋 {n_resets:,}건 | 콜 {_calls} | {time.time()-t0:.0f}s (마지막 {day})", flush=True)
        except RuntimeError as e:
            print(f"\n중단: {e}", flush=True)

    tot = con.execute("SELECT count(*), count(DISTINCT isu_cd) FROM krx_base_resets").fetchone()
    dd = con.execute("SELECT count(*), max(bas_dd) FROM krx_reset_sweep_checkpoint").fetchone()
    led = con.execute("SELECT count(*), max(chg_dd) FROM krx_shares_ledger").fetchone()
    print(f"\n리셋 테이블: {tot[0]:,}건 / {tot[1]:,}종목 | 처리일 {dd[0]:,} (최신 {dd[1]}) | 이번 콜 {_calls}", flush=True)
    print(f"주식수 원장: {led[0]:,}건 (최신 {led[1]}) | 이번 추가 {n_shrs:,}", flush=True)
    con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--update", action="store_true", help="최근 30일만 (주간 유지)")
    ap.add_argument("--since", default="20160104")
    ap.add_argument("--cap", type=int, default=9000)
    a = ap.parse_args()
    s = date(int(a.since[:4]), int(a.since[4:6]), int(a.since[6:8]))
    if a.update:
        s = date.today() - timedelta(days=30)
    asyncio.run(sweep(s, a.cap))
