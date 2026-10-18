#!/usr/bin/env python3
"""지수 구성종목 원장 적재 — `krx_index_members` (코스피200·코스닥150). DART 0콜 · KRX 3~7콜 · 공개 목록 4~7요청.

■ 무엇을 푸는가
  「코스피200」 유니버스(`screener`·`trading_data(scope=universe)`·`forward_estimates_data(universe=…)`)가 원장이 없어
  KOSPI 시총상위 200 으로 대체돼 왔다. 지수 구성은 시총 순위가 아니라 업종 대표성·유동성으로 정하므로 둘은 다르다 —
  실측(20261016): 공식 201종목 중 34종목이 대체 목록에 없었고, 대체 목록의 33종목(리츠·인프라펀드 등)은 지수 밖이었다.

■ 원천과 확인 — **KRX 공식 합계와 맞는 구성만 쓴다**
  - KRX 는 지수 구성종목을 무료 API 로 주지 않는다: Open API 서비스 목록에 구성종목 항목이 없고, 정보데이터시스템의
    「지수구성종목」 화면·내려받기는 로그인을 요구하며(비로그인 응답 `LOGOUT`), 지수 사이트는 구성정보를 구매 요청으로 판다.
  - 그래서 구성종목 **목록**은 공개 증권 포털의 지수 편입종목 목록(`LIST_URL`)에서 받는다. 가격은 거기서 받지 않는다.
  - **확인**: 그 목록 종목들의 KRX 일별 매매정보(`stk_bydd_trd`·`ksq_bydd_trd`) 시가총액·거래량·거래대금 합이 같은 날 KRX 지수
    일별시세(`kospi_dd_trd`·`kosdaq_dd_trd`)의 그 지수 `MKTCAP`·`ACC_TRDVOL`·`ACC_TRDVAL` 과 **셋 다 정확히 같을 때만** 쓴다.
    종목 하나만 달라도 거래량 합이 어긋난다. 실측(20261016): 코스피200 201종목·코스닥150 150종목 모두 세 합의 차이 0.

■ 기준일 D — KRX 지수 일별시세가 있는 가장 늦은 날(오늘 KST 부터 거꾸로 최대 10일). KRX 는 전일 자료를 익일 아침에 낸다.
  목록은 「지금」 구성이라 구성이 바뀐 날(정기변경: 6·12월 선물옵션 만기 다음 날 · 수시변경: 상장폐지·합병·분할)에는 D 의 KRX
  합계와 어긋날 수 있다 → 그날은 쓰지 않고(경고) 다음 실행에서 D 가 따라오면 맞는다. 매일 돌리는 이유다.

■ 쓰기(한 트랜잭션, 지수마다)
  - 직전 묶음과 구성이 같으면 그 묶음의 checked_dd 만 D 로 올린다(이미 D 이상이면 아무것도 안 한다).
  - 다르면 snap_dd=D 로 새 묶음을 넣는다. D 가 직전 묶음의 snap_dd 이하인데 구성이 다르면 옛 날짜로 이력을 고쳐 쓰는 것이라 거절한다.

■ 종료코드 — 0: 썼거나 이미 최신 · 확인 불일치지만 직전 확인이 `GRACE_DAYS` 안(전환일로 본다, 경고만) /
  1: 목록·KRX 응답 이상 · 원장이 비었는데 확인 실패 · 확인 불일치가 `GRACE_DAYS` 를 넘김 / 2: 설정 누락(KRX 키·DB).

용례:
  python3 scripts/krx_index_members.py                 # 기준일 자동, 확인되면 적재
  python3 scripts/krx_index_members.py --dry-run       # 받아서 확인·계획만(쓰지 않는다)
  python3 scripts/krx_index_members.py --date 20261016 --index kospi200
필요 환경: KRX_API_KEY 또는 KRX_OPEN_API_KEY · DATABASE_URL(쓸 때). 스키마는 `scripts/krx_index_members_ddl.py`.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:  # Windows cp949 콘솔에서도 한글 출력 안전
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

KST = timezone(timedelta(hours=9))
LIST_URL = "https://m.stock.naver.com/api/index/{code}/enrollStocks"
#: 목록이 받는 가장 큰 페이지 크기(실측: 61 이상은 400). 짧은 페이지가 오면 끝이다.
PAGE_SIZE = 60
MAX_PAGES = 10
KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis"
TICKER_RE = re.compile(r"^[0-9A-Z]{6}$")
#: 확인 불일치를 「전환일」로 보아 경고만 하는 최대 달력 일수(직전 확인 → 기준일). 넘으면 실패로 알린다.
GRACE_DAYS = 4
#: KRX 지수 자료가 있는 날을 찾아 거꾸로 볼 최대 일수(연휴 포함).
LOOKBACK_DAYS = 10
#: 같은 프로세스·같은 날 두 실행이 겹쳐 쓰지 않게 — `pg_advisory_xact_lock` 키(ASCII "OPMIDXM1").
LOCK_KEY = int.from_bytes(b"OPMIDXM1", "big")


@dataclass(frozen=True)
class Source:
    code: str          # 표의 index_code = 유니버스 문법 이름
    list_code: str     # 공개 목록의 지수 코드
    idx_ep: str        # KRX 지수 일별시세 엔드포인트
    idx_name: str      # 그 응답의 IDX_NM
    stock_ep: str      # KRX 종목 일별 매매정보 엔드포인트(그 지수의 시장)
    min_n: int         # 받은 목록의 종목 수가 이 범위를 벗어나면 목록이 잘렸거나 다른 것이다
    max_n: int


SOURCES: tuple[Source, ...] = (
    Source("kospi200", "KPI200", "idx/kospi_dd_trd", "코스피 200", "sto/stk_bydd_trd", 195, 215),
    Source("kosdaq150", "KQI150", "idx/kosdaq_dd_trd", "코스닥 150", "sto/ksq_bydd_trd", 145, 165),
)


class SourceError(RuntimeError):
    """목록·KRX 응답이 기대한 모양이 아니다 — 확인할 수 없으니 쓰지 않는다."""


# ── 순수 함수(시험 대상) ──────────────────────────────────────────────────────

def _num(v: Any) -> int | None:
    s = str(v).replace(",", "").strip() if v is not None else ""
    if s in ("", "-"):
        return None
    try:
        return int(s)
    except ValueError:
        return None


def parse_list_page(payload: Any) -> list[str]:
    """목록 한 페이지 → 종목코드. 모양이 다르면 SourceError(빈 페이지는 [])."""
    if not isinstance(payload, list):
        raise SourceError(f"목록 응답이 배열이 아니다({type(payload).__name__})")
    out: list[str] = []
    for i, it in enumerate(payload):
        code = (it or {}).get("itemCode") if isinstance(it, dict) else None
        if not isinstance(code, str) or not TICKER_RE.match(code.strip()):
            raise SourceError(f"목록 {i}번째 행에 종목코드가 없다")
        if (it.get("stockEndType") or "stock") != "stock":
            raise SourceError(f"목록 {i}번째 행이 주식이 아니다({it.get('stockEndType')})")
        out.append(code.strip())
    return out


def check_members(tickers: list[str], src: Source) -> frozenset[str]:
    """중복·종목 수를 본다. 문제가 있으면 SourceError."""
    dup = sorted({t for t in tickers if tickers.count(t) > 1})
    if dup:
        raise SourceError(f"{src.code}: 같은 종목이 두 번 나왔다 {dup[:5]}")
    if not (src.min_n <= len(tickers) <= src.max_n):
        raise SourceError(f"{src.code}: 종목 수 {len(tickers)} — 기대 범위 {src.min_n}~{src.max_n} 밖(목록이 잘렸거나 다른 목록)")
    return frozenset(tickers)


def find_index_row(rows: Iterable[dict], name: str) -> dict | None:
    return next((r for r in rows if (r.get("IDX_NM") or "").strip() == name), None)


@dataclass
class Check:
    ok: bool
    missing: list[str] = field(default_factory=list)   # KRX 그 시장 일별 매매정보에 없는 목록 종목
    sums: tuple[int, int, int] = (0, 0, 0)              # 목록 종목의 (시총, 거래량, 거래대금) 합
    krx: tuple[int | None, int | None, int | None] = (None, None, None)

    def describe(self) -> str:
        names = ("시총", "거래량", "거래대금")
        parts = [f"{n} 합 {s:,} vs KRX {k:,}" + ("" if s == k else f" (차 {s - k:+,})")
                 for n, s, k in zip(names, self.sums, self.krx) if k is not None]
        if self.missing:
            parts.append(f"KRX 그 시장 자료에 없는 종목 {len(self.missing)}개 {self.missing[:5]}")
        return " · ".join(parts)


def verify(members: frozenset[str], stock_rows: Iterable[dict], idx_row: dict) -> Check:
    """목록 종목들의 KRX 시총·거래량·거래대금 합 == KRX 지수 합계(셋 다 정확히)."""
    by = {(r.get("ISU_CD") or "").strip(): r for r in stock_rows}
    missing = sorted(t for t in members if t not in by)
    sums = tuple(sum(_num(by[t].get(k)) or 0 for t in members if t in by)
                 for k in ("MKTCAP", "ACC_TRDVOL", "ACC_TRDVAL"))
    krx = tuple(_num(idx_row.get(k)) for k in ("MKTCAP", "ACC_TRDVOL", "ACC_TRDVAL"))
    ok = not missing and None not in krx and sums == krx
    return Check(ok=ok, missing=missing, sums=sums, krx=krx)  # type: ignore[arg-type]


@dataclass(frozen=True)
class Prev:
    snap_dd: str
    checked_dd: str
    tickers: frozenset[str]


@dataclass(frozen=True)
class Plan:
    action: str                       # insert · touch · noop · refuse
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()


def plan(prev: Prev | None, members: frozenset[str], d: str) -> Plan:
    if prev is None:
        return Plan("insert", added=tuple(sorted(members)))
    added = tuple(sorted(members - prev.tickers))
    removed = tuple(sorted(prev.tickers - members))
    if not added and not removed:
        return Plan("touch") if d > prev.checked_dd else Plan("noop")
    if d <= prev.snap_dd:
        return Plan("refuse", added, removed)
    return Plan("insert", added, removed)


def days_between(a: str, b: str) -> int:
    return (datetime.strptime(b, "%Y%m%d") - datetime.strptime(a, "%Y%m%d")).days


# ── DB ─────────────────────────────────────────────────────────────────────

PREV_SQL = ("SELECT snap_dd, min(checked_dd), array_agg(ticker) FROM krx_index_members "
            "WHERE index_code=%s AND snap_dd=(SELECT max(snap_dd) FROM krx_index_members WHERE index_code=%s) "
            "GROUP BY snap_dd")
INSERT_SQL = ("INSERT INTO krx_index_members (index_code, snap_dd, ticker, checked_dd) VALUES (%s, %s, %s, %s) "
              "ON CONFLICT (index_code, snap_dd, ticker) DO UPDATE SET checked_dd = GREATEST(krx_index_members.checked_dd, EXCLUDED.checked_dd)")
TOUCH_SQL = ("UPDATE krx_index_members SET checked_dd=%s "
             "WHERE index_code=%s AND snap_dd=%s AND checked_dd < %s")


def read_prev(conn, code: str) -> Prev | None:
    row = conn.execute(PREV_SQL, (code, code)).fetchone()
    if not row:
        return None
    return Prev(snap_dd=row[0], checked_dd=row[1], tickers=frozenset(row[2]))


def write(conn, code: str, p: Plan, members: frozenset[str], d: str, prev: Prev | None) -> int:
    """계획대로 쓴다 → 바뀐 행 수. 호출자가 트랜잭션을 연다."""
    if p.action == "insert":
        with conn.cursor() as cur:
            cur.executemany(INSERT_SQL, [(code, d, t, d) for t in sorted(members)])
        return len(members)
    if p.action == "touch" and prev is not None:
        return conn.execute(TOUCH_SQL, (d, code, prev.snap_dd, d)).rowcount
    return 0


# ── 네트워크 ───────────────────────────────────────────────────────────────

async def fetch_list(h, src: Source) -> frozenset[str]:
    """공개 목록을 페이지마다 받는다. 프로세스 공통 웹 속도제한(`throttle_web_request`)을 따른다."""
    from open_proxy_mcp.dart.client import throttle_web_request
    tickers: list[str] = []
    for page in range(1, MAX_PAGES + 1):
        await throttle_web_request("fetch_index_members")
        r = await h.get(LIST_URL.format(code=src.list_code), params={"page": page, "pageSize": PAGE_SIZE},
                        headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            raise SourceError(f"{src.code}: 목록 {page}쪽 응답 {r.status_code}")
        try:
            payload = r.json()
        except ValueError as exc:
            raise SourceError(f"{src.code}: 목록 {page}쪽이 JSON 이 아니다") from exc
        got = parse_list_page(payload)
        tickers += got
        if len(got) < PAGE_SIZE:
            break
    else:
        raise SourceError(f"{src.code}: 목록이 {MAX_PAGES}쪽을 넘는다 — 페이지 끝을 못 찾았다")
    return check_members(tickers, src)


async def krx_get(h, key: str, ep: str, d: str) -> list[dict]:
    from open_proxy_mcp.dart.krx_meter import bump
    bump()
    r = await h.get(f"{KRX_BASE}/{ep}", headers={"AUTH_KEY": key}, params={"basDd": d})
    if r.status_code != 200:
        raise SourceError(f"KRX {ep} 응답 {r.status_code} (basDd={d}) — 키·한도 확인")
    try:
        return next((v for v in r.json().values() if isinstance(v, list)), [])
    except ValueError as exc:
        raise SourceError(f"KRX {ep} 응답이 JSON 이 아니다 (basDd={d})") from exc


async def find_day(h, key: str, ep: str, today: date) -> tuple[str, list[dict]] | None:
    """KRX 지수 일별시세가 있는 가장 늦은 날과 그 행들(오늘부터 거꾸로)."""
    for back in range(0, LOOKBACK_DAYS + 1):
        d = (today - timedelta(days=back))
        if d.weekday() >= 5:
            continue
        dd = d.strftime("%Y%m%d")
        rows = await krx_get(h, key, ep, dd)
        if rows:
            return dd, rows
    return None


# ── 실행 ───────────────────────────────────────────────────────────────────

@dataclass
class Outcome:
    src: Source
    members: frozenset[str] | None = None
    check: Check | None = None
    error: str = ""


async def collect(key: str, d_arg: str | None, sources: list[Source]) -> tuple[str | None, list[Outcome]]:
    import httpx
    outcomes: list[Outcome] = []
    cache: dict[str, list[dict]] = {}
    async with httpx.AsyncClient(timeout=30) as h:
        if d_arg:
            d = d_arg
        else:
            found = await find_day(h, key, sources[0].idx_ep, datetime.now(KST).date())
            if found is None:
                return None, [Outcome(s, error=f"최근 {LOOKBACK_DAYS}일 안에 KRX 지수 자료가 없다") for s in sources]
            d, cache[sources[0].idx_ep] = found
        for src in sources:
            out = Outcome(src)
            outcomes.append(out)
            try:
                out.members = await fetch_list(h, src)
                if src.idx_ep not in cache:
                    cache[src.idx_ep] = await krx_get(h, key, src.idx_ep, d)
                idx_row = find_index_row(cache[src.idx_ep], src.idx_name)
                if idx_row is None:
                    raise SourceError(f"{src.code}: KRX {src.idx_ep} {d} 응답에 「{src.idx_name}」 행이 없다")
                if src.stock_ep not in cache:
                    cache[src.stock_ep] = await krx_get(h, key, src.stock_ep, d)
                if not cache[src.stock_ep]:
                    raise SourceError(f"{src.code}: KRX {src.stock_ep} {d} 응답이 비었다")
                out.check = verify(out.members, cache[src.stock_ep], idx_row)
            except SourceError as exc:
                out.error = str(exc)
            except Exception as exc:  # noqa: BLE001 — 전송 오류도 「확인 못 함」으로 모아 끝에서 실패로 알린다
                out.error = f"{src.code}: {type(exc).__name__}: {exc}"
    return d, outcomes


def settle(outcomes: list[Outcome], d: str | None, url: str | None, dry_run: bool) -> int:
    """확인된 것은 쓰고(드라이런이면 계획만), 나머지는 직전 확인과의 간격으로 경고/실패를 가른다."""
    import psycopg
    rc = 0
    conn = None
    if url:
        extra = {"options": "-c default_transaction_read_only=on"} if dry_run else {}
        conn = psycopg.connect(url, connect_timeout=20, **extra)
    try:
        for o in outcomes:
            code = o.src.code
            prev = None
            if conn is not None:
                with conn.transaction():
                    if not dry_run:
                        conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
                    prev = read_prev(conn, code)
                    if o.error or not (o.check and o.check.ok):
                        pass
                    else:
                        p = plan(prev, o.members or frozenset(), d or "")
                        n = 0 if dry_run else write(conn, code, p, o.members or frozenset(), d or "", prev)
                        diff = (f" · 편입 {len(p.added)} {list(p.added)[:10]} · 편출 {len(p.removed)} {list(p.removed)[:10]}"
                                if prev is not None and (p.added or p.removed) else "")
                        tag = "계획" if dry_run else "적용"
                        if p.action == "refuse":
                            print(f"::warning::{code} {d}: 구성이 직전 묶음({prev.snap_dd if prev else '-'})과 다른데 "
                                  f"기준일이 그 묶음보다 늦지 않다 — 옛 날짜로 이력을 고쳐 쓰지 않는다{diff}")
                        else:
                            print(f"{code} {d}: 확인됨({len(o.members or ())}종목 · {o.check.describe()}) → "
                                  f"{tag} {p.action}{'' if dry_run else f' {n}행'}{diff}")
                        continue
            elif o.check and o.check.ok:
                print(f"{code} {d}: 확인됨({len(o.members or ())}종목 · {o.check.describe()}) — DB 미설정이라 쓰지 않음")
                continue
            # 확인 못 함 — 응답 이상은 바로 실패, 합계 불일치는 직전 확인이 가까우면(전환일) 경고만
            if o.error:
                print(f"::error::{code}: {o.error}")
                rc = 1
                continue
            why = o.check.describe() if o.check else "확인 결과 없음"
            if prev is not None and d and days_between(prev.checked_dd, d) <= GRACE_DAYS:
                print(f"::warning::{code} {d}: 목록과 KRX 지수 합계가 맞지 않아 쓰지 않았다({why}) — 직전 확인 "
                      f"{prev.checked_dd}, 구성 전환일일 수 있다. 다음 실행에서 다시 맞춘다.")
            else:
                last = prev.checked_dd if prev else "없음"
                print(f"::error::{code} {d}: 목록과 KRX 지수 합계가 맞지 않는다({why}) — 직전 확인 {last}. "
                      "원장이 낡아 가고 있다 — 목록 원천·KRX 응답을 확인할 것.")
                rc = 1
    finally:
        if conn is not None:
            conn.close()
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--date", help="기준일 YYYYMMDD(비우면 KRX 지수 자료가 있는 가장 늦은 날)")
    ap.add_argument("--index", action="append", choices=[s.code for s in SOURCES],
                    help="이 지수만(여러 번 줄 수 있다). 비우면 전부")
    ap.add_argument("--dry-run", action="store_true", help="받아서 확인·계획만 — 쓰지 않는다(DB 는 읽기 전용으로 연다)")
    args = ap.parse_args(argv)
    if args.date and not re.fullmatch(r"\d{8}", args.date):
        ap.error("--date 는 YYYYMMDD")
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    key = os.getenv("KRX_API_KEY") or os.getenv("KRX_OPEN_API_KEY")
    if not key:
        print("::error::KRX_API_KEY / KRX_OPEN_API_KEY 가 없다 — 확인할 수 없어 아무것도 하지 않았다.")
        return 2
    url = os.getenv("DATABASE_URL")
    if not url and not args.dry_run:
        print("::error::DATABASE_URL 이 없다 — 쓰지 않았다(--dry-run 이면 확인만 할 수 있다).")
        return 2
    sources = [s for s in SOURCES if not args.index or s.code in args.index]
    d, outcomes = asyncio.run(collect(key, args.date, sources))
    print(f"기준일 {d or '-'} · 지수 {', '.join(s.code for s in sources)}")
    return settle(outcomes, d, url, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
