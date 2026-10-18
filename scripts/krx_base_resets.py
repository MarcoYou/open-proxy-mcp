"""KRX 기준가 리셋 전수 스윕 — 수정주가 조정계수의 원천 (거래소 실측).

원리: 정상 거래일은 `전일종가 == 오늘종가 - 전일대비`. 어긋나는 날 = 거래소가 기준가를
조정한 날(유증 권리락 · 주식배당락 · 무상락 · 분할/병합/감자 변경상장 재기준).
  조정계수 = 기준가 / 전일종가   (기준가 = 종가 - 전일대비)
날짜·크기 모두 거래소 공식값이라 발행가·배정비율 공식 계산이 불필요.
검증: 대한항공 유증락 0.8362 · 셀트리온 주식배당락(5%/2%) · 오리온홀딩스 인적분할 1/20.36 ·
      SKT 분할 1/5.80 — 전부 정확 탐지 (wiki/architecture/adjusted-price-timeseries.md).

KRX Open API 한도: 키당 1일(0~24시) 10,000콜 — 본 스크립트 상한 CALL_CAP(기본 9,000).
전수 스윕 ≈ 2,573거래일 × 2시장 ≈ 5,150콜. 재개 가능(일 단위 커밋, 중단 시 재실행).

휴장 판정(261019): 빈 응답 한 번으로 휴장을 적지 않는다. 코스피·코스닥이 **둘 다** 비고 그 날 뒤로 평일이 2일 이상 지났을 때만 후보로 두고,
다음 거래일의 기준가(종가 − 전일대비)가 그 앞 거래일 종가와 이어지는 것을 확인한 뒤 그 거래일과 같은 커밋에 적는다
(`open_proxy_mcp.krx_daily.closed_verdict` — 서버 갱신기와 같은 규칙). 종전에는 코스피 빈 응답 한 번(게시 전·일시 빈 응답)을
휴장으로 적었고, 장부가 「처리함」으로 보아 다시 메우지 않았다(20260702 실측: 그날 기준가 리셋 1건 누락·주식수 변동 8건 하루 늦게 기록).
일시 오류·한 시장만 온 날·아직 확정 못 한 날에서는 **멈춘다** — 다음 실행이 그 날부터 다시 묻는다(건너뛰고 다음 날을 처리하면 그 날은 영영
안 메워지고, 다음 날의 직전 종가가 하루 어긋난다).

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
from open_proxy_mcp.market_codes import KS as MKT_KS, KQ as MKT_KQ
from open_proxy_mcp.krx_daily import closed_verdict, weekdays_after
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


import httpx
import psycopg

URLS = [(MKT_KS, "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"),
        (MKT_KQ, "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd")]
THROTTLE_S = 0.35
_calls = 0
_fail_streak = 0

DDL = """
CREATE TABLE IF NOT EXISTS krx_reset_sweep_checkpoint (price_dd text PRIMARY KEY, n_stocks int);
-- 260823: krx_base_resets(측정) + krx_adj_factor_v3(라벨)을 한 표로 통합. 갱신이 2단계였던 탓에
--   뒤쪽(라벨)이 260705 부터 실행 불가였는데 앞쪽만 돌아 아무도 몰랐다. 한 표면 라벨 칸이
--   비어 있는 게 바로 보이고, 라벨 작업은 INSERT 가 아니라 그 빈 칸 UPDATE 가 된다.
CREATE TABLE IF NOT EXISTS krx_adj_events (
  ticker text NOT NULL, event_dd text NOT NULL, market text,
  event_close double precision, prev_close double precision, base_price double precision,
  adj_factor_raw double precision, adj_factor double precision,
  event_type text, rcept_no text, label_source text, label_confidence text, note text,
  PRIMARY KEY (ticker, event_dd));
-- 261018: idx_adj_events_isu (ticker, event_dd) 생성 줄을 뺐다 — 기본키와 칸·순서가 같은 중복 인덱스였다
--   (운영 0.24 MiB, 조회는 기본키가 그대로 받는다). 운영에서 지운 뒤 이 줄이 남아 있으면 매일 되살아난다.
CREATE TABLE IF NOT EXISTS krx_shares_ledger (
  ticker text NOT NULL, event_dd text NOT NULL,
  prev_shrs bigint, new_shrs bigint, market text,
  PRIMARY KEY (ticker, event_dd));
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
    try:  # 일별 사용량 장부 (Supabase ops_krx_calls — 두 PC 합산)
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

    ck = dict(con.execute("SELECT price_dd, n_stocks FROM krx_reset_sweep_checkpoint").fetchall())
    done = set(ck)
    # 재개 시 prev_close 시드: ① 마지막 「거래일」 처리일 재조회 ② 부재 종목은 krx_weekly 최근 종가.
    #   261019: 마지막 처리일이 휴장(n_stocks=0)이면 그 날 재조회는 빈 응답이라 시드가 주간 종가(지난 금요일)로 밀렸다 —
    #   마지막 거래일을 다시 받는다(그 뒤로는 휴장뿐이라 직전 종가가 그대로다).
    prev_close: dict[str, float] = {}
    # 260823: 주식수 원장(krx_shares_ledger)을 같은 스냅샷에서 함께 적는다 — **KRX 콜 0 추가**.
    #   원장은 writer 가 없어 20260701 에 멎어 있었는데, `krx_stock_flags`(시계열 해석 주의 딱지)가
    #   전적으로 이 원장에서 파생된다(실측: unresolved_adjustment 이벤트 125/125 가 원장 날짜).
    #   원장이 멈추면 딱지도 멈추고, 새로 분할·감자한 종목은 경고 없이 나간다.
    #   여기 붙이는 이유 — 이 스윕이 이미 전종목 일별 스냅샷(LIST_SHRS 포함)을 받아온다.
    prev_shrs: dict[str, int] = {}
    last = max(done) if done else None
    last_trading = max((d for d, n in ck.items() if n), default=None)
    async with httpx.AsyncClient(timeout=30, verify=_ssl_ctx()) as h:
        if last:
            for market, url in URLS:
                snap = (await _fetch(h, key, url, last_trading, cap) or []) if last_trading else []
                for x in snap:
                    c = _num(x.get("TDD_CLSPRC"))
                    if c:
                        prev_close[x["ISU_CD"]] = c
            for isu, c in con.execute("""SELECT DISTINCT ON (ticker) ticker, close FROM krx_weekly
                                         WHERE price_dd <= %s ORDER BY ticker, price_dd DESC""", (last,)).fetchall():
                prev_close.setdefault(isu, float(c) if c else None)
            # 주식수 원장 시드 — 원장의 마지막 값이 우선, 없으면 krx_weekly 최근 상장주식수
            for isu, n in con.execute("""SELECT DISTINCT ON (ticker) ticker, new_shrs FROM krx_shares_ledger
                                         WHERE event_dd <= %s ORDER BY ticker, event_dd DESC""", (last,)).fetchall():
                if n: prev_shrs[isu] = int(n)
            for isu, n in con.execute("""SELECT DISTINCT ON (ticker) ticker, list_shrs FROM krx_weekly
                                         WHERE price_dd <= %s AND list_shrs > 0 ORDER BY ticker, price_dd DESC""",
                                      (last,)).fetchall():
                prev_shrs.setdefault(isu, int(n))
            print(f"재개: 처리일 {len(done)} (마지막 {last}, 마지막 거래일 {last_trading}) | 시드 종목 {len(prev_close):,}", flush=True)

        days = []
        d = since if not last else date(int(last[:4]), int(last[4:6]), int(last[6:8])) + timedelta(days=1)
        today = date.today()
        today_dd = today.strftime("%Y%m%d")
        while d < today:
            if d.weekday() < 5 and d.strftime("%Y%m%d") not in done:
                days.append(d.strftime("%Y%m%d"))
            d += timedelta(days=1)
        print(f"처리할 평일 {len(days)}일 (CALL_CAP {cap})", flush=True)

        t0 = time.time()
        n_resets = n_days = n_shrs = 0
        pending: list[str] = []      # 두 시장 모두 빈 응답 + 평일 2일 경과 — 다음 거래일 기준가 연속성으로 확정할 휴장 후보
        try:
            for day in days:
                snaps = []
                for market, url in URLS:
                    s = await _fetch(h, key, url, day, cap)
                    if s is None:
                        snaps = None
                        break
                    snaps.append((market, s))
                if snaps is None:
                    print(f"  {day} 일시 오류 — 여기서 멈춘다(다음 실행이 이 날부터 다시)", flush=True)
                    break
                sizes = [len(s) for _, s in snaps]
                if not any(sizes):
                    if weekdays_after(day, today_dd) < 2:
                        print(f"  {day} 두 시장 빈 응답 — 아직 게시 전일 수 있어 멈춘다(휴장으로 적지 않음)", flush=True)
                        break
                    pending.append(day)
                    continue
                if not all(sizes):
                    print(f"  {day} 한 시장만 응답(KOSPI {sizes[0]} / KOSDAQ {sizes[1]}) — 멈춘다", flush=True)
                    break
                rows, srows, n_st, n_cmp = [], [], 0, 0
                new_close: dict[str, float] = {}
                new_shrs: dict[str, int] = {}
                for market, snap in snaps:
                    for x in snap:
                        isu = x.get("ISU_CD")
                        close = _num(x.get("TDD_CLSPRC"))
                        chg = _num(x.get("CMPPREVDD_PRC")) or 0.0
                        if not isu or not close:
                            continue
                        n_st += 1
                        base = close - chg
                        pc = prev_close.get(isu)
                        if pc:
                            n_cmp += 1
                        if pc and abs(base - pc) > max(pc * 0.001, 1.0):
                            rows.append((isu, day, market, pc, base, close, base / pc, base / pc))
                        new_close[isu] = close
                        # 주식수 원장 — 변동한 날만 적는다(원장 기존 규약과 동일).
                        # 첫 등장(prev 없음)은 prev_shrs=NULL 로 기록 — 신규상장 표시.
                        ns = _num(x.get("LIST_SHRS"))
                        if ns:
                            ps = prev_shrs.get(isu)
                            if ps is None or ps != int(ns):
                                srows.append((isu, day, ps, int(ns), market))
                            new_shrs[isu] = int(ns)
                if pending:
                    # 휴장 후보 확정 — 이 거래일의 기준가가 후보 앞 거래일 종가와 이어지면(어긋남이 기준가 조정 몇 종목뿐) 휴장이다.
                    v = closed_verdict(True, True, weekdays_after(pending[-1], today_dd), (n_cmp, len(rows)))
                    if v != "closed":
                        print(f"  {','.join(pending)} 는 두 시장 빈 응답이지만 {day} 기준가가 이어지지 않는다"
                              f"(어긋남 {len(rows)}/{n_cmp}, 판정 {v}) — 휴장으로 적지 않고 멈춘다(다음 실행이 다시 묻는다)", flush=True)
                        break
                prev_close.update(new_close)
                prev_shrs.update(new_shrs)
                with con.cursor() as cur:
                    for p in pending:
                        cur.execute("INSERT INTO krx_reset_sweep_checkpoint VALUES (%s,0) ON CONFLICT DO NOTHING", (p,))
                    if rows:
                        # 라벨 칸은 비워 둔다 — adj_factor_v3.py 가 나중에 UPDATE 로 채운다.
                        # adj_factor 는 우선 실측과 같게 두고, 액면비율 스냅이 걸리면 그때 덮인다.
                        cur.executemany("""INSERT INTO krx_adj_events
                            (ticker, event_dd, market, prev_close, base_price, event_close,
                             adj_factor_raw, adj_factor, label_confidence)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'unlabeled')
                            ON CONFLICT (ticker, event_dd) DO NOTHING""", rows)
                    if srows:
                        cur.executemany("""INSERT INTO krx_shares_ledger
                                           (ticker, event_dd, prev_shrs, new_shrs, market)
                                           VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""", srows)
                    cur.execute("INSERT INTO krx_reset_sweep_checkpoint VALUES (%s,%s) ON CONFLICT DO NOTHING", (day, n_st))
                con.commit()
                n_days += 1 + len(pending)
                if pending:
                    print(f"  휴장 확정 {','.join(pending)} ({day} 기준가 연속 — 어긋남 {len(rows)}/{n_cmp})", flush=True)
                pending = []
                n_resets += len(rows)
                n_shrs += len(srows)
                if n_days % 100 == 0:
                    print(f"  {n_days}일 | 리셋 {n_resets:,}건 | 콜 {_calls} | {time.time()-t0:.0f}s (마지막 {day})", flush=True)
            if pending:
                print(f"  휴장 후보 {','.join(pending)} — 다음 거래일이 아직 없어 이번엔 적지 않는다(다음 실행이 확정)", flush=True)
        except RuntimeError as e:
            print(f"\n중단: {e}", flush=True)

    tot = con.execute("SELECT count(*), count(DISTINCT ticker) FROM krx_adj_events").fetchone()
    dd = con.execute("SELECT count(*), max(price_dd) FROM krx_reset_sweep_checkpoint").fetchone()
    led = con.execute("SELECT count(*), max(event_dd) FROM krx_shares_ledger").fetchone()
    print(f"\n조정 사건: {tot[0]:,}건 / {tot[1]:,}종목 | 처리일 {dd[0]:,} (최신 {dd[1]}) | 이번 콜 {_calls}", flush=True)
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
