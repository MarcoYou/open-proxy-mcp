"""KRX 주간(주말 마지막 거래일) 전종목 시세 적재 — Supabase krx_weekly.

용도: 밸류에이션 밴드/시계열의 가격 축. append-only(불변), 날짜별 시장 스냅샷이라
      신규상장 자동 포함·상폐 자동 중단(종목 관리 불필요, 생존편향 없음).

KRX Open API 이용 한도 (openapi.krx.co.kr 서비스 이용방법, 2026-07 확인):
  - "하나의 키당 1일(매일 0시~24시) 10,000회 이하의 요청으로 제한, 초과 시 서비스 중지 가능"
  - 전일 데이터는 익일 오전 8시 갱신 (당일 종가는 당일 조회 불가)
  - 비상업적 목적 한정·제3자 정보 제공 금지 조항 있음 (원시세 재배포 금지, 배수 산출 인풋으로만 사용)
  - 인증키 12개월 미사용 시 삭제될 수 있음
→ 본 스크립트: 콜 간 THROTTLE_S 간격 + SAFETY_MAX_CALLS(9,000) 초과 시 중단.
  DART throttle(910/min)과는 완전 별개 채널.

실행:
  python scripts/krx_weekly_ingest.py --backfill [--since 20160101] [--limit-weeks N]
  python scripts/krx_weekly_ingest.py --update          # 최근 완결 주만 (주간 유지용)
재개 가능: 주 단위 commit, 이미 적재된 주는 skip.
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

KRX_ENDPOINTS = [
    ("KOSPI", "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"),
    ("KOSDAQ", "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd"),
]
THROTTLE_S = 0.35          # 콜 간 간격 (일 10,000 한도 대비 예의상 완충)
SAFETY_MAX_CALLS = 9_000   # 하루 한도(10,000)의 90%에서 강제 중단
DDL = """
CREATE TABLE IF NOT EXISTS krx_weekly (
  bas_dd    text   NOT NULL,
  isu_cd    text   NOT NULL,
  mkt       text,
  close     bigint,
  mktcap    bigint,
  list_shrs bigint,
  PRIMARY KEY (bas_dd, isu_cd)
);
CREATE INDEX IF NOT EXISTS idx_krx_weekly_isu ON krx_weekly (isu_cd, bas_dd);
"""

_calls = 0


def _num(v):
    try:
        return int(str(v).replace(",", "")) if v not in (None, "", "-") else None
    except Exception:
        return None


def _ssl_ctx():
    """Windows 신뢰저장소 사용(truststore). 실패 시 기본. KRX_INSECURE=1이면 검증 끔(로컬 프록시 환경용)."""
    if os.getenv("KRX_INSECURE") == "1":
        return False
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        return True


async def _fetch(h: httpx.AsyncClient, url: str, key: str, bas_dd: str) -> list:
    global _calls
    if _calls >= SAFETY_MAX_CALLS:
        raise RuntimeError(f"SAFETY_MAX_CALLS({SAFETY_MAX_CALLS}) 도달 — 일 한도 보호를 위해 중단. 내일 재개하세요.")
    _calls += 1
    await asyncio.sleep(THROTTLE_S)
    r = await h.get(url, headers={"AUTH_KEY": key}, params={"basDd": bas_dd})
    r.raise_for_status()
    return next((v for v in r.json().values() if isinstance(v, list)), [])


def _week_candidates(since: date, until: date) -> list[list[str]]:
    """완결된 ISO 주별 후보일 [금,목,수,화,월]. 진행 중인 이번 주는 제외(최근가는 라이브 담당)."""
    weeks = []
    monday = since - timedelta(days=since.weekday())
    last_full_monday = until - timedelta(days=until.weekday() + 7)  # 지난주 월요일
    while monday <= last_full_monday:
        cands = [(monday + timedelta(days=d)).strftime("%Y%m%d") for d in (4, 3, 2, 1, 0)]
        weeks.append(cands)
        monday += timedelta(days=7)
    return weeks


async def ingest(since: date, limit_weeks: int | None):
    key = os.getenv("KRX_API_KEY") or os.getenv("KRX_OPEN_API_KEY")
    assert key, "KRX_API_KEY 없음 (.env)"
    dsn = os.environ["DATABASE_URL"]

    con = psycopg.connect(dsn, connect_timeout=20)
    con.execute(DDL)
    con.commit()
    done_days = {r[0] for r in con.execute("SELECT DISTINCT bas_dd FROM krx_weekly").fetchall()}

    weeks = _week_candidates(since, date.today())
    # 이미 적재된 주(후보일 중 하나라도 DB에 있으면) skip
    todo = [c for c in weeks if not (set(c) & done_days)]
    if limit_weeks:
        todo = todo[:limit_weeks]
    print(f"대상 주: {len(weeks)} | 기적재 skip: {len(weeks) - len(todo)} | 실행: {len(todo)}", flush=True)

    t0 = time.time()
    saved_weeks = empty_weeks = 0
    async with httpx.AsyncClient(timeout=30, verify=_ssl_ctx()) as h:
        for i, cands in enumerate(todo):
            rows_by_mkt, used_day = None, None
            for d in cands:  # 금→월 순으로 그 주 마지막 거래일 탐색
                kospi = await _fetch(h, KRX_ENDPOINTS[0][1], key, d)
                if not kospi:
                    continue
                kosdaq = await _fetch(h, KRX_ENDPOINTS[1][1], key, d)
                rows_by_mkt, used_day = [("KOSPI", kospi), ("KOSDAQ", kosdaq)], d
                break
            if not rows_by_mkt:
                empty_weeks += 1  # 그 주 전체 휴장(설·추석 등) 또는 API 제공범위 밖
                continue

            recs = [
                (used_day, r.get("ISU_CD"), mkt, _num(r.get("TDD_CLSPRC")), _num(r.get("MKTCAP")), _num(r.get("LIST_SHRS")))
                for mkt, rows in rows_by_mkt for r in rows if r.get("ISU_CD")
            ]
            with con.cursor() as cur:  # 주 단위 idempotent: 삭제 후 COPY, commit
                cur.execute("DELETE FROM krx_weekly WHERE bas_dd = %s", (used_day,))
                with cur.copy("COPY krx_weekly (bas_dd, isu_cd, mkt, close, mktcap, list_shrs) FROM STDIN") as cp:
                    for rec in recs:
                        cp.write_row(rec)
            con.commit()
            saved_weeks += 1
            if saved_weeks % 25 == 0 or i == len(todo) - 1:
                el = time.time() - t0
                print(f"  [{saved_weeks}/{len(todo)}] {used_day} {len(recs):,}행 | 콜 {_calls} | {el:.0f}s", flush=True)

    n = con.execute("SELECT count(*), count(DISTINCT bas_dd), min(bas_dd), max(bas_dd) FROM krx_weekly").fetchone()
    print(f"\n완료: 저장 {saved_weeks}주 / 휴장·범위밖 {empty_weeks}주 / 총 콜 {_calls}", flush=True)
    print(f"테이블: {n[0]:,}행, {n[1]}개 주간포인트, {n[2]} ~ {n[3]}", flush=True)
    con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--update", action="store_true", help="최근 8주만 확인(주간 유지)")
    ap.add_argument("--since", default="20160101")
    ap.add_argument("--limit-weeks", type=int, default=None)
    a = ap.parse_args()
    since = date(int(a.since[:4]), int(a.since[4:6]), int(a.since[6:8]))
    if a.update:
        since = date.today() - timedelta(weeks=8)
    asyncio.run(ingest(since, a.limit_weeks))
