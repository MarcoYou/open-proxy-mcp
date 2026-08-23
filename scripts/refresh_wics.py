#!/usr/bin/env python3
"""WiseIndex WICS 업종분류 수집 — 정적 맵(JSON) + Postgres 스냅샷 적재.

한국 종목의 업종분류(대분류 10 / 하위업종 28)를 WiseIndex 공개 엔드포인트에서 수집한다.
분류만 담당하며 가격·시가총액은 수집하지 않는다(원시세 재배포 방지).

Mirae_Asset_Securities의 `src/lib/data/kr-wics-live.ts` + `scripts/refresh-kr-wics-map.mjs`
포팅. 완전성 검사·중복분류 거부·asOf 산출 규칙을 원본과 동일하게 유지한다.

엔드포인트 (wiseindex.com):
  - WICS 트리      GET /API/Tree/Get?id=4
  - 업종 구성종목  GET /Index/GetIndexComponets?ceil_yn=0&dt=YYYYMMDD&sec_cd=G4530
  `Componets` 철자는 사이트 원본 그대로다(오타 아님).

기준일: 인자 없으면 서울 기준 **직전 금요일**. 실제 기준일(asOf)은 응답 info.TRD_DT를
서울시간으로 변환해 기록한다. 응답 날짜는 .NET JSON `/Date(밀리초)/` 형식일 수 있다.

무결성 규칙 (하나라도 걸리면 아무것도 쓰지 않고 중단):
  - 대분류 10 미만 / 하위업종 20 미만 / 종목 1,000 미만 → 불완전 응답으로 판단
  - 동일 종목이 둘 이상의 하위업종에 나타나면 실패 (임의로 최근 응답을 고르지 않는다)

DB: WICS_DATABASE_URL > DATABASE_URL 순으로 DSN을 찾는다.
    OPM의 DATABASE_URL은 Supabase를 가리키므로, 대시보드가 쓰는 Neon에 넣으려면
    WICS_DATABASE_URL을 따로 설정할 것. 둘 다 없으면 DB 단계를 건너뛴다(파일만 생성).

실행:
  python scripts/refresh_wics.py                      # 직전 금요일, 파일 + DB
  python scripts/refresh_wics.py --date 20260814      # 기준일 지정
  python scripts/refresh_wics.py --no-db              # 파일만
  python scripts/refresh_wics.py --no-file            # DB만
  python scripts/refresh_wics.py --dry-run            # 수집·검증만, 쓰기 없음
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:  # Windows cp949 콘솔에서도 한글 출력 안전
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import httpx
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

BASE_URL = "https://www.wiseindex.com"
OUT_PATH = ROOT / "open_proxy_mcp" / "data" / "wics" / "wics_map.json"
KST = timezone(timedelta(hours=9))

#: 불완전 응답 판정 임계값 — 원본(kr-wics-live.ts)과 동일.
MIN_SECTORS, MIN_INDUSTRIES, MIN_TICKERS = 10, 20, 1_000

TICKER_RE = re.compile(r"^[0-9A-Z]{6}$")
DOTNET_DATE_RE = re.compile(r"^/Date\((\d+)(?:[+-]\d+)?\)/$")

DDL = """
CREATE TABLE IF NOT EXISTS kr_wics_snapshots (
    as_of          text PRIMARY KEY,
    requested_date text NOT NULL,
    data           jsonb NOT NULL,
    sector_count   integer NOT NULL,
    industry_count integer NOT NULL,
    ticker_count   integer NOT NULL,
    refreshed_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kr_wics_snapshots_refreshed
    ON kr_wics_snapshots (refreshed_at DESC);

-- 260823(OPM): 위 blob 은 대시보드 모양이다. OPM 은 krx_weekly 와 **조인**해 섹터 시총을
--   집계하므로 종목당 한 행이 필요하다. jsonb 를 매 질의마다 펴는 건 비싸다.
--   이름은 OPM 규약대로 출처 접두사 — WiseIndex 가 원천이라 wise_.
CREATE TABLE IF NOT EXISTS wise_sector (
    snap_dd       text NOT NULL,      -- YYYYMMDD (asOf 를 _dd 규약으로)
    ticker        text NOT NULL,
    sector_code   text NOT NULL,      -- G25 등 대분류 10
    sector        text NOT NULL,
    industry_code text NOT NULL,      -- G2510 등 하위업종 28
    industry      text NOT NULL,
    PRIMARY KEY (snap_dd, ticker)
);
CREATE INDEX IF NOT EXISTS idx_wise_sector_ticker ON wise_sector (ticker, snap_dd);
"""

UPSERT = """
INSERT INTO kr_wics_snapshots
    (as_of, requested_date, data, sector_count, industry_count, ticker_count, refreshed_at)
VALUES (%s, %s, %s, %s, %s, %s, now())
ON CONFLICT (as_of) DO UPDATE SET
    requested_date = EXCLUDED.requested_date,
    data           = EXCLUDED.data,
    sector_count   = EXCLUDED.sector_count,
    industry_count = EXCLUDED.industry_count,
    ticker_count   = EXCLUDED.ticker_count,
    refreshed_at   = now()
"""


def previous_friday_kst(now: datetime | None = None) -> str:
    """서울 기준 직전 금요일 YYYYMMDD. 금요일에 돌리면 *지난주* 금요일을 준다."""
    today = (now or datetime.now(KST)).astimezone(KST).date()
    js_day = (today.weekday() + 1) % 7  # 파이썬 월=0 → JS 일=0 체계로 변환
    delta = (js_day + 2) % 7 or 7
    return (today - timedelta(days=delta)).strftime("%Y%m%d")


def parse_wise_date(value: str) -> str:
    """WiseIndex TRD_DT → 서울 기준 YYYY-MM-DD. .NET `/Date(ms)/`와 ISO 문자열 모두 처리."""
    dotnet = DOTNET_DATE_RE.match(str(value))
    if dotnet:
        parsed = datetime.fromtimestamp(int(dotnet.group(1)) / 1000, tz=timezone.utc)
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError as exc:
            raise ValueError(f"invalid WICS TRD_DT: {value}") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST).strftime("%Y-%m-%d")


def find_node(nodes: list | None, title: str) -> dict | None:
    """트리에서 title이 일치하는 첫 노드를 깊이우선으로 찾는다."""
    for node in nodes or []:
        if node.get("title") == title:
            return node
        nested = find_node(node.get("children"), title)
        if nested:
            return nested
    return None


async def _get_json(client: httpx.AsyncClient, path: str):
    response = await client.get(f"{BASE_URL}{path}")
    if response.status_code != 200:
        raise RuntimeError(f"{path}: HTTP {response.status_code}")
    return response.json()


async def fetch_snapshot(requested_date: str) -> dict:
    """WICS 트리 → 하위업종별 구성종목 수집. 무결성 위반 시 예외를 던진다."""
    if not re.fullmatch(r"\d{8}", requested_date):
        raise ValueError("--date 는 YYYYMMDD 형식이어야 한다")

    headers = {
        "Accept": "application/json",
        "Referer": f"{BASE_URL}/DataCenter/Index/G10",
        "User-Agent": "Mirae-Research-Terminal/1.0",
    }
    async with httpx.AsyncClient(timeout=30, headers=headers, follow_redirects=True) as client:
        tree = await _get_json(client, "/API/Tree/Get?id=4")
        wics = find_node(tree, "WICS")
        if not wics or not wics.get("children"):
            raise RuntimeError("WICS 트리를 찾지 못했다 — 사이트 응답 구조 변경 가능성")

        sectors = wics["children"]
        industries = [
            {
                "sectorCode": sector["key"],
                "sector": sector["title"],
                "industryCode": industry["key"],
                "industry": industry["title"],
            }
            for sector in sectors
            for industry in (sector.get("children") or [sector])
        ]

        entries: dict[str, dict] = {}
        actual_dates: set[str] = set()
        for group in industries:
            params = f"ceil_yn=0&dt={requested_date}&sec_cd={group['industryCode']}"
            payload = await _get_json(client, f"/Index/GetIndexComponets?{params}")
            trd_dt = (payload.get("info") or {}).get("TRD_DT")
            if trd_dt:
                actual_dates.add(parse_wise_date(trd_dt))
            for row in payload.get("list") or []:
                code = str(row.get("CMP_CD") or "").strip()
                if not TICKER_RE.match(code):
                    continue
                existing = entries.get(code)
                if existing and existing["industryCode"] != group["industryCode"]:
                    raise RuntimeError(
                        f"{code} 가 {existing['industryCode']} 와 {group['industryCode']} 양쪽에 있다 "
                        "— 분류 충돌이므로 중단한다"
                    )
                entries[code] = dict(group)

    if len(sectors) < MIN_SECTORS or len(industries) < MIN_INDUSTRIES or len(entries) < MIN_TICKERS:
        raise RuntimeError(
            "완전성 검사 실패 — "
            f"대분류 {len(sectors)}/{MIN_SECTORS}, "
            f"하위업종 {len(industries)}/{MIN_INDUSTRIES}, "
            f"종목 {len(entries)}/{MIN_TICKERS}"
        )

    fallback_as_of = f"{requested_date[:4]}-{requested_date[4:6]}-{requested_date[6:8]}"
    return {
        "requestedDate": requested_date,
        "asOf": max(actual_dates) if actual_dates else fallback_as_of,
        "sectorCount": len(sectors),
        "industryCount": len(industries),
        "tickerCount": len(entries),
        "data": dict(sorted(entries.items())),
    }


def write_file(snapshot: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "requestedDate": snapshot["requestedDate"],
            "asOf": snapshot["asOf"],
            "sectorCount": snapshot["sectorCount"],
            "industryCount": snapshot["industryCount"],
            "tickerCount": snapshot["tickerCount"],
            "refreshedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": "wiseindex",
        },
        "data": snapshot["data"],
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    print(f"파일: {out_path.relative_to(ROOT)} ({snapshot['tickerCount']:,}종목)", flush=True)


def persist(snapshot: dict) -> bool:
    """kr_wics_snapshots 업서트. DSN이 없으면 False를 돌려주고 조용히 건너뛴다."""
    dsn = os.getenv("WICS_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        print("DB: WICS_DATABASE_URL/DATABASE_URL 없음 — 적재 건너뜀", flush=True)
        return False

    import psycopg
    from psycopg.types.json import Jsonb

    with psycopg.connect(dsn, connect_timeout=20) as con:
        con.execute(DDL)
        con.execute(
            UPSERT,
            (
                snapshot["asOf"],
                snapshot["requestedDate"],
                Jsonb(snapshot["data"]),
                snapshot["sectorCount"],
                snapshot["industryCount"],
                snapshot["tickerCount"],
            ),
        )
        # 행 단위 전개 — OPM 이 실제로 쓰는 모양(krx_weekly 조인용)
        snap_dd = snapshot["asOf"].replace("-", "")
        with con.cursor() as cur:
            cur.executemany(
                "INSERT INTO wise_sector "
                "(snap_dd, ticker, sector_code, sector, industry_code, industry) "
                "VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (snap_dd, ticker) DO UPDATE SET "
                "sector_code=EXCLUDED.sector_code, sector=EXCLUDED.sector, "
                "industry_code=EXCLUDED.industry_code, industry=EXCLUDED.industry",
                [(snap_dd, t, v["sectorCode"], v["sector"], v["industryCode"], v["industry"])
                 for t, v in snapshot["data"].items()])
        con.commit()
        total = con.execute("SELECT count(*) FROM kr_wics_snapshots").fetchone()[0]
        rows, snaps = con.execute(
            "SELECT count(*), count(DISTINCT snap_dd) FROM wise_sector").fetchone()
    print(f"DB: kr_wics_snapshots 업서트 (as_of={snapshot['asOf']}, 누적 {total}개 스냅샷)", flush=True)
    print(f"DB: wise_sector {rows:,}행 / {snaps}개 시점", flush=True)
    return True


async def main(args: argparse.Namespace) -> int:
    requested_date = args.date or os.getenv("WICS_DATE") or previous_friday_kst()
    print(f"수집 기준일(dt): {requested_date}", flush=True)

    try:
        snapshot = await fetch_snapshot(requested_date)
    except Exception as exc:
        print(f"실패: {exc}", file=sys.stderr, flush=True)
        return 1

    print(
        f"수집 완료: 대분류 {snapshot['sectorCount']} / 하위업종 {snapshot['industryCount']} / "
        f"종목 {snapshot['tickerCount']:,} / 실제 기준일 {snapshot['asOf']}",
        flush=True,
    )

    if args.dry_run:
        print("dry-run — 파일·DB 모두 쓰지 않았다", flush=True)
        return 0
    if not args.no_file:
        write_file(snapshot, Path(args.out) if args.out else OUT_PATH)
    if not args.no_db:
        persist(snapshot)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="WiseIndex WICS 업종분류 수집")
    ap.add_argument("--date", help="기준일 YYYYMMDD (기본: 서울 기준 직전 금요일)")
    ap.add_argument("--out", help=f"정적 맵 출력 경로 (기본: {OUT_PATH.relative_to(ROOT)})")
    ap.add_argument("--no-file", action="store_true", help="정적 맵 파일을 쓰지 않는다")
    ap.add_argument("--no-db", action="store_true", help="Postgres 적재를 건너뛴다")
    ap.add_argument("--dry-run", action="store_true", help="수집·검증만 하고 아무것도 쓰지 않는다")
    sys.exit(asyncio.run(main(ap.parse_args())))
