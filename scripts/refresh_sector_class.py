#!/usr/bin/env python3
"""업종분류 수집 — 정적 맵(JSON) + Postgres 스냅샷 적재.

한국 종목의 업종분류(대분류 10 / 하위업종 28)를 분류 공급처의 공개 엔드포인트에서 수집한다.
분류만 담당하며 가격·시가총액은 수집하지 않는다(원시세 재배포 방지).

대시보드 프로젝트(Mirae_Asset_Securities)의 업종분류 수집 코드를
포팅. 완전성 검사·중복분류 거부·asOf 산출 규칙을 원본과 동일하게 유지한다.

엔드포인트 (분류 공급처 공개 API — 주소는 BASE_URL):
  - 분류 트리      GET /API/Tree/Get?id=4
  - 업종 구성종목  GET /Index/GetIndexComponets?ceil_yn=0&dt=YYYYMMDD&sec_cd=G4530
  `Componets` 철자는 사이트 원본 그대로다(오타 아님).

기준일: 자동 실행은 서울 기준 **직전 금요일 이하**에서 최대 14일 전까지 찾는다.
주말 또는 첫 하위업종의 명시적인 무자료 응답만 건너뛴다. --date/WICS_DATE는 기본적으로
그 날짜만 조회한다(--lookback-days로 명시적으로 소급 허용). requestedDate는 원래 요청일,
asOf는 모든 하위업종이 같은 날짜로 확인된 실제 기준일이다. 무자료 이외 오류는 즉시 중단한다.

무결성 규칙 (하나라도 걸리면 아무것도 쓰지 않고 중단):
  - 대분류 10 미만 / 하위업종 20 미만 / 종목 1,000 미만 → 불완전 응답으로 판단
  - 동일 종목이 둘 이상의 하위업종에 나타나면 실패 (임의로 최근 응답을 고르지 않는다)
  - 날짜 누락·오염·혼합·조회일 불일치 또는 일부 업종만 빈 응답이면 실패
  - 무자료 = 빈 list + 정확한 .NET 최소 날짜 sentinel. 음수 날짜를 유효일로 허용하지 않는다

DB: WICS_DATABASE_URL > DATABASE_URL 순으로 DSN을 찾는다.
    OPM의 DATABASE_URL은 Supabase를 가리키므로, 대시보드가 쓰는 Neon에 넣으려면
    WICS_DATABASE_URL을 따로 설정할 것. 둘 다 없으면 DB 단계를 건너뛴다(파일만 생성).

실행:
  python scripts/refresh_sector_class.py                      # 직전 금요일, 파일 + DB
  python scripts/refresh_sector_class.py --date 20260814      # 정확한 날짜 지정
  python scripts/refresh_sector_class.py --date 20260925 --lookback-days 14  # 이하 유효일
  python scripts/refresh_sector_class.py --no-db              # 파일만
  python scripts/refresh_sector_class.py --no-file            # DB만
  python scripts/refresh_sector_class.py --dry-run            # 수집·검증만, 쓰기 없음
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:  # Windows cp949 콘솔에서도 한글 출력 안전
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import httpx
from dotenv import load_dotenv

from open_proxy_mcp.dart.client import throttle_web_request

load_dotenv(ROOT / ".env")

BASE_URL = "https://www.wiseindex.com"
OUT_PATH = ROOT / "open_proxy_mcp" / "data" / "sector_class" / "class_map.json"
KST = timezone(timedelta(hours=9))

#: 불완전 응답 판정 임계값 — 포팅 원본과 동일.
MIN_SECTORS, MIN_INDUSTRIES, MIN_TICKERS = 10, 20, 1_000

TICKER_RE = re.compile(r"^[0-9A-Z]{6}$")
DOTNET_DATE_RE = re.compile(r"^/Date\(([0-9]+)(?P<offset>[+-][0-9]{4})?\)/$")
ISO_DATE_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
    r"(?:[T ][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]+)?)?"
    r"(?P<offset>Z|[+-][0-9]{2}:?[0-9]{2})?)?"
)
NO_DATA_TRADE_DATE = "/Date(-62135596800000)/"
MAX_LOOKBACK_DAYS = 14

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
--   표 이름은 OPM 규약의 출처 접두사를 따른다(DB 이름 변경은 보류).
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


def parse_requested_date(value: str) -> date:
    """조회일은 실제 달력의 YYYYMMDD여야 한다(형식만 맞는 2월 30일도 거부)."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{8}", value):
        raise ValueError("--date 는 실제 날짜 YYYYMMDD여야 한다")
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as exc:
        raise ValueError("--date 는 실제 날짜 YYYYMMDD여야 한다") from exc


def parse_trade_date(value: str) -> str:
    """공급처 TRD_DT → 서울 기준 YYYY-MM-DD. 최소 날짜 sentinel은 유효일이 아니다."""
    if not isinstance(value, str) or value == NO_DATA_TRADE_DATE:
        raise ValueError("유효하지 않은 TRD_DT")
    try:
        dotnet = DOTNET_DATE_RE.fullmatch(value)
        if dotnet:
            _validate_utc_offset(dotnet.group("offset"))
            parsed = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
                milliseconds=int(dotnet.group(1))
            )
        else:
            iso = ISO_DATE_RE.fullmatch(value)
            if not iso:
                raise ValueError("날짜 형식 오류")
            _validate_utc_offset(iso.group("offset"))
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=KST)
        return parsed.astimezone(KST).date().isoformat()
    except (ValueError, OverflowError) as exc:
        # 상류 원문을 예외에 복사하지 않는다(HTML/접속 정보가 섞일 수 있다).
        raise ValueError("유효하지 않은 TRD_DT") from exc


def _validate_utc_offset(offset: str | None) -> None:
    if not offset or offset == "Z":
        return
    digits = offset[1:].replace(":", "")
    if int(digits[:2]) >= 24 or int(digits[2:]) >= 60:
        raise ValueError("날짜 UTC 오프셋 범위 오류")


def _component_rows(payload: object, candidate: date, industry_code: str) -> list | None:
    """None은 검증된 무자료만 뜻한다. 다른 비정상 응답은 소급하지 않고 중단한다."""
    if not isinstance(payload, dict) or not isinstance(payload.get("info"), dict):
        raise RuntimeError(f"{industry_code}: 구성종목 응답 구조 오류(info)")
    rows = payload.get("list")
    if not isinstance(rows, list):
        raise RuntimeError(f"{industry_code}: 구성종목 응답 구조 오류(list)")
    trd_dt = payload["info"].get("TRD_DT")
    if trd_dt == NO_DATA_TRADE_DATE and not rows:
        return None
    actual_date = parse_trade_date(trd_dt)
    if actual_date != candidate.isoformat():
        raise RuntimeError(
            f"{industry_code}: 응답 기준일 {actual_date} != 조회일 {candidate.isoformat()}"
        )
    if not rows or not all(isinstance(row, dict) for row in rows):
        raise RuntimeError(f"{industry_code}: 구성종목이 비었거나 행 구조가 잘못됐다")
    return rows


def find_node(nodes: list | None, title: str) -> dict | None:
    """트리에서 title이 일치하는 첫 노드를 깊이우선으로 찾는다."""
    if nodes is not None and not isinstance(nodes, list):
        raise RuntimeError("분류 트리 응답 구조 오류(children)")
    for node in nodes or []:
        if not isinstance(node, dict):
            raise RuntimeError("분류 트리 응답 구조 오류(node)")
        if node.get("title") == title:
            return node
        nested = find_node(node.get("children"), title)
        if nested:
            return nested
    return None


def _industry_groups(sectors: list) -> list[dict]:
    """중복·누락된 트리가 완전성 검사 개수를 부풀리지 않게 검증한다."""
    if not isinstance(sectors, list):
        raise RuntimeError("분류 트리 응답 구조 오류(sectors)")
    groups = []
    sector_codes, industry_codes = set(), set()
    for sector in sectors:
        _validate_node(sector, sector_codes)
        children = sector.get("children")
        if children is not None and not isinstance(children, list):
            raise RuntimeError("분류 트리 응답 구조 오류(children)")
        for industry in children or [sector]:
            _validate_node(industry, industry_codes)
            groups.append({
                "sectorCode": sector["key"], "sector": sector["title"],
                "industryCode": industry["key"], "industry": industry["title"],
            })
    return groups


def _validate_node(node: object, seen: set) -> None:
    if not isinstance(node, dict) or any(
        not isinstance(node.get(field), str) or not node[field].strip()
        for field in ("key", "title")
    ):
        raise RuntimeError("분류 트리 응답 구조 오류(key/title)")
    if not re.fullmatch(r"G[0-9]+", node["key"]):
        raise RuntimeError("분류 트리 응답 구조 오류(key 형식)")
    if node["key"] in seen:
        raise RuntimeError("분류 트리의 중복 코드")
    seen.add(node["key"])


async def _get_json(client: httpx.AsyncClient, path: str):
    await throttle_web_request("fetch_sector_class")
    try:
        response = await client.get(f"{BASE_URL}{path}")
    except httpx.HTTPError as exc:
        raise RuntimeError(f"상류 전송 오류({type(exc).__name__}) — 날짜 소급 없이 중단") from exc
    if response.status_code != 200:
        raise RuntimeError(f"상류 HTTP {response.status_code} — 날짜 소급 없이 중단")
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError("상류 JSON 응답 오류 — 날짜 소급 없이 중단") from exc


async def fetch_snapshot(requested_date: str, *, lookback_days: int = 0) -> dict:
    """요청일 이하의 완전한 한 날짜만 선택. 정확일 기본값, 명시적 무자료만 제한 소급."""
    anchor = parse_requested_date(requested_date)
    if type(lookback_days) is not int or not 0 <= lookback_days <= MAX_LOOKBACK_DAYS:
        raise ValueError(f"--lookback-days 는 0~{MAX_LOOKBACK_DAYS} 정수여야 한다")
    # 날짜 하한도 HTTP 전에 검증한다.
    oldest = anchor - timedelta(days=lookback_days)
    headers = {
        "Accept": "application/json",
        "Referer": f"{BASE_URL}/DataCenter/Index/G10",
        "User-Agent": "Mirae-Research-Terminal/1.0",
    }
    async with httpx.AsyncClient(timeout=30, headers=headers, follow_redirects=False) as client:
        tree = await _get_json(client, "/API/Tree/Get?id=4")
        if not isinstance(tree, list):
            raise RuntimeError("분류 트리 응답 구조 오류")
        root = find_node(tree, "WICS")
        if not root or not root.get("children"):
            raise RuntimeError("분류 트리 최상위 노드를 찾지 못했다 — 사이트 응답 구조 변경 가능성")
        sectors = root["children"]
        industries = _industry_groups(sectors)
        if len(sectors) < MIN_SECTORS or len(industries) < MIN_INDUSTRIES:
            raise RuntimeError("완전성 검사 실패 — 분류 트리의 대분류/하위업종 부족")

        for offset in range(lookback_days + 1):
            candidate = anchor - timedelta(days=offset)
            if candidate.weekday() >= 5:
                print(f"기준일 제외: {candidate.isoformat()} — 주말", flush=True)
                continue
            candidate_dd = candidate.strftime("%Y%m%d")
            entries: dict[str, dict] = {}
            for group_index, group in enumerate(industries):
                params = f"ceil_yn=0&dt={candidate_dd}&sec_cd={group['industryCode']}"
                payload = await _get_json(client, f"/Index/GetIndexComponets?{params}")
                rows = _component_rows(payload, candidate, group["industryCode"])
                if rows is None:
                    if group_index:
                        raise RuntimeError(
                            f"{group['industryCode']}: 일부 하위업종만 무자료 — 불완전 응답으로 중단"
                        )
                    # 첫 업종을 가용일 탐침으로 재사용한다. 실패한 날짜에서 전 업종을 재수집하지 않는다.
                    print(
                        f"기준일 제외: {candidate.isoformat()} — 첫 하위업종의 명시적 무자료 "
                        "(빈 list + 최소 날짜)", flush=True,
                    )
                    break
                valid_rows = 0
                for row in rows:
                    code = str(row.get("CMP_CD") or "").strip()
                    if not TICKER_RE.fullmatch(code):
                        raise RuntimeError(f"{group['industryCode']}: 유효하지 않은 종목코드")
                    valid_rows += 1
                    existing = entries.get(code)
                    if existing and existing["industryCode"] != group["industryCode"]:
                        raise RuntimeError(
                            f"{code} 가 {existing['industryCode']} 와 {group['industryCode']} 양쪽에 있다 "
                            "— 분류 충돌이므로 중단한다"
                        )
                    entries[code] = dict(group)
                if not valid_rows:
                    raise RuntimeError(f"{group['industryCode']}: 유효한 종목이 없는 불완전 응답")
            else:
                if len(entries) < MIN_TICKERS:
                    raise RuntimeError(
                        f"완전성 검사 실패 — 종목 {len(entries)}/{MIN_TICKERS}"
                    )
                reason = "요청일 일치" if offset == 0 else "주말/명시적 무자료만 건너뛴 최근 유효일"
                print(
                    f"기준일 선택: 요청 {requested_date} → 실제 {candidate.isoformat()} "
                    f"/ 소급 {offset}일 / 사유: {reason}", flush=True,
                )
                return {
                    "requestedDate": requested_date,
                    "asOf": candidate.isoformat(),
                    "sectorCount": len(sectors),
                    "industryCount": len(industries),
                    "tickerCount": len(entries),
                    "data": dict(sorted(entries.items())),
                }

    raise RuntimeError(
        f"유효 거래일 없음 — 요청 {requested_date}, 조회 범위 {oldest.isoformat()}~{anchor.isoformat()}, "
        f"최대 소급 {lookback_days}일. 파일·DB를 쓰지 않는다"
    )


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
            "source": "external",
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
    explicit_date = args.date or os.getenv("WICS_DATE")
    requested_date = explicit_date or previous_friday_kst()
    lookback_days = args.lookback_days
    if lookback_days is None:
        lookback_days = 0 if explicit_date else MAX_LOOKBACK_DAYS
    print(f"수집 요청일: {requested_date} / 최대 소급 {lookback_days}일", flush=True)

    try:
        snapshot = await fetch_snapshot(requested_date, lookback_days=lookback_days)
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
    ap = argparse.ArgumentParser(description="업종분류 수집")
    ap.add_argument("--date", help="정확한 기준일 YYYYMMDD (미지정: 직전 금요일 이하 유효일)")
    ap.add_argument("--lookback-days", type=int,
                    help="허용 소급 일수 0~14 (기본: 자동 날짜 14, --date/WICS_DATE 지정 시 0)")
    ap.add_argument("--out", help=f"정적 맵 출력 경로 (기본: {OUT_PATH.relative_to(ROOT)})")
    ap.add_argument("--no-file", action="store_true", help="정적 맵 파일을 쓰지 않는다")
    ap.add_argument("--no-db", action="store_true", help="Postgres 적재를 건너뛴다")
    ap.add_argument("--dry-run", action="store_true", help="수집·검증만 하고 아무것도 쓰지 않는다")
    sys.exit(asyncio.run(main(ap.parse_args())))
