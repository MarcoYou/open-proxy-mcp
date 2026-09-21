"""공시 이벤트 원장 배치 — 전체시장 주요 공시를 날짜별로 쌓는다 (cron 대상, 260916 신설).

왜: `screener` 는 부를 때마다 DART 를 실시간 스캔하고 결과를 남기지 않는다(최근 3개월 상한).
「이번 주 반도체 업종 수주공시가 평소보다 많이 떴나」는 **기준선**(평소 몇 건인가)이 있어야
답할 수 있는데, 그 기준선을 만들 저장분이 없었다. 이 배치는 그 저장분 하나를 만든다.
업종별 흐름·수주 강도·리비전 대조 같은 조회 도구는 전부 이 표 위에 얹는다.

무엇을 쌓나: `dart_events` — 접수번호 1행. 분류(유형·세부·단계·정정 여부)는 screener 의
분류기를 **그대로** 쓴다(사본을 두면 두 곳이 갈린다). 업종은 업종분류(월 1회 스냅샷,
접수일 이전 최신), 시총은 `krx_weekly`(주간, 접수일 이전 최신)를 붙인다. 수주·자사주·배당·
증자·잠정실적은 기존 상세 파서로 금액·매출비율 등 핵심 숫자를 `detail`(jsonb)에 담는다.
`dart_events_scan` — 날짜×코드별로 DART 가 몇 건이라 했고 몇 페이지를 실제로 받았는지.
기준선은 모수가 정직해야 뜻이 있다. 빠진 날은 「0건」이 아니라 「안 본 날」이다.

무엇을 안 쌓나: 사용자 질의·조회 결과(규칙 10). 이 표는 시장 데이터 스냅샷이다.
정정본을 원본에 접지 않는다 — list.json 은 어느 접수번호의 정정인지 알려주지 않으므로 둘 다
남기고 `is_correction`·`dedup_key` 로 읽는 쪽이 판단한다(screener 의 「모르면 지우지 않는다」).

비용: 스캔은 날짜×코드당 list.json 1~수 콜(100건/페이지). 하루 5코드면 10콜 안쪽.
상세는 유형별 파서가 문서를 열어 건당 2~4콜. 하루 60~100건이면 200~400콜. 910/분 안쪽이지만
`--max-detail-calls` 로 상한을 둔다. 백필은 `--no-details` 로 스캔만 먼저 쌓는다.

실행:
  python3 scripts/disclosure_ledger.py                       # 최근 3일(KST) 재스캔 + 상세 (idempotent)
  python3 scripts/disclosure_ledger.py --since 20260901 --until 20260915 --no-details   # 백필(스캔만)
  python3 scripts/disclosure_ledger.py --dry                 # DB 에 쓰지 않고 요약만
  python3 scripts/disclosure_ledger.py --types order,earnings
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from open_proxy_mcp.services.screener import (  # noqa: E402
    TYPE_REGISTRY, _BY_CODE, _KST, _fetch_detail, _force_details, _scan_code_uncached,
    _stage_tag, classify,
)

#: 기본 유형 = opt-in(임원 소유상황·공개매수·권유) 을 뺀 전부. 노이즈 유형은 부를 때 명시한다.
DEFAULT_TYPES = [t["code"] for t in TYPE_REGISTRY if not t.get("opt_in")]
#: 상세 파서를 태우는 유형 — 숫자가 시그널의 재료가 되는 것들만. 나머지는 제목·단계로 충분하다.
DEFAULT_DETAIL_KINDS = ("order", "earnings", "treasury", "dilutive", "dividend")
#: 하루 배치가 상세에 쓸 DART 콜 상한. 넘으면 남은 건은 scan_only 로 두고 다음 날 채운다.
DEFAULT_MAX_DETAIL_CALLS = 600
#: 최근 며칠을 매번 다시 본다 — 정정·지연 접수를 잡는다. upsert 라 몇 번 봐도 같다.
DEFAULT_LOOKBACK_DAYS = 3
#: 한 날짜·한 코드가 넘길 수 있는 페이지 — 배치라 넉넉히. 100건/페이지 × 40 = 4,000건/일.
SCAN_MAX_PAGES = 40

DDL = """
CREATE TABLE IF NOT EXISTS dart_events (
  rcept_no      text PRIMARY KEY,
  rcept_dt      date NOT NULL,
  corp_code     text,
  corp_name     text,
  stock_code    text,
  corp_cls      text,
  kind          text NOT NULL,
  subtype       text,
  stage         text,
  is_correction boolean NOT NULL DEFAULT false,
  dedup_key     text,
  report_nm     text,
  flr_nm        text,
  sector        text,
  industry      text,
  sector_dd     text,
  mktcap_won    bigint,
  mktcap_dd     text,
  detail_status text,
  detail        jsonb,
  detail_note   text,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS dart_events_dt_kind ON dart_events (rcept_dt, kind);
CREATE INDEX IF NOT EXISTS dart_events_industry_dt ON dart_events (industry, rcept_dt);
CREATE INDEX IF NOT EXISTS dart_events_stock_dt ON dart_events (stock_code, rcept_dt);
CREATE TABLE IF NOT EXISTS dart_events_scan (
  scan_dd     date NOT NULL,
  code        text NOT NULL,
  total       integer,
  total_pages integer,
  fetched_pages integer,
  complete    boolean,
  error       text,
  scanned_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (scan_dd, code)
);
"""

UPSERT_EVENT = """
INSERT INTO dart_events (rcept_no, rcept_dt, corp_code, corp_name, stock_code, corp_cls, kind, subtype,
  stage, is_correction, dedup_key, report_nm, flr_nm, sector, industry, sector_dd, mktcap_won, mktcap_dd,
  detail_status, detail, detail_note, updated_at)
VALUES (%(rcept_no)s, %(rcept_dt)s, %(corp_code)s, %(corp_name)s, %(stock_code)s, %(corp_cls)s, %(kind)s,
  %(subtype)s, %(stage)s, %(is_correction)s, %(dedup_key)s, %(report_nm)s, %(flr_nm)s, %(sector)s,
  %(industry)s, %(sector_dd)s, %(mktcap_won)s, %(mktcap_dd)s, %(detail_status)s, %(detail)s, %(detail_note)s, now())
ON CONFLICT (rcept_no) DO UPDATE SET
  corp_name = EXCLUDED.corp_name, stock_code = EXCLUDED.stock_code, corp_cls = EXCLUDED.corp_cls,
  kind = EXCLUDED.kind, subtype = EXCLUDED.subtype, stage = EXCLUDED.stage,
  is_correction = EXCLUDED.is_correction, dedup_key = EXCLUDED.dedup_key, report_nm = EXCLUDED.report_nm,
  flr_nm = EXCLUDED.flr_nm,
  sector = COALESCE(EXCLUDED.sector, dart_events.sector),
  industry = COALESCE(EXCLUDED.industry, dart_events.industry),
  sector_dd = COALESCE(EXCLUDED.sector_dd, dart_events.sector_dd),
  mktcap_won = COALESCE(EXCLUDED.mktcap_won, dart_events.mktcap_won),
  mktcap_dd = COALESCE(EXCLUDED.mktcap_dd, dart_events.mktcap_dd),
  -- 상세는 **이미 파싱된 것을 실패로 되돌리지 않는다** — 그뿐이다. 260917 버그: 예전 SQL 은
  -- 「새 값이 parsed/partial 이 아니면 옛 값을 유지」였는데, 첫 시도에서 옛 값이 기본치
  -- scan_only(아직 안 본 상태)일 때도 이 규칙이 걸려 실제 결과(no_data 등)가 영영 안 남고
  -- scan_only 로 굳었다 — 해지 단계 공시 85건이 이렇게 「안 본 것」으로 잘못 보였다(실측).
  -- 옳은 규칙: 기존이 parsed/partial(진짜 결과)일 때만 지키고, 그 외(scan_only·이전 실패)는
  -- 새 결과로 갱신한다 — scan_only 는 결과가 아니라 「아직」이라는 뜻이라 지킬 값이 아니다.
  detail_status = CASE WHEN EXCLUDED.detail_status IN ('parsed','partial') THEN EXCLUDED.detail_status
                       WHEN dart_events.detail_status IN ('parsed','partial') THEN dart_events.detail_status
                       ELSE EXCLUDED.detail_status END,
  detail = CASE WHEN EXCLUDED.detail_status IN ('parsed','partial') THEN EXCLUDED.detail
                WHEN dart_events.detail_status IN ('parsed','partial') THEN dart_events.detail
                ELSE EXCLUDED.detail END,
  detail_note = CASE WHEN EXCLUDED.detail_status IN ('parsed','partial') THEN EXCLUDED.detail_note
                     WHEN dart_events.detail_status IN ('parsed','partial') THEN dart_events.detail_note
                     ELSE EXCLUDED.detail_note END,
  updated_at = now();
"""

UPSERT_SCAN = """
INSERT INTO dart_events_scan (scan_dd, code, total, total_pages, fetched_pages, complete, error, scanned_at)
VALUES (%(scan_dd)s, %(code)s, %(total)s, %(total_pages)s, %(fetched_pages)s, %(complete)s, %(error)s, now())
ON CONFLICT (scan_dd, code) DO UPDATE SET total = EXCLUDED.total, total_pages = EXCLUDED.total_pages,
  fetched_pages = EXCLUDED.fetched_pages, complete = EXCLUDED.complete, error = EXCLUDED.error,
  scanned_at = now();
"""


# ── 순수 함수 (테스트 대상) ─────────────────────────────────────────────

def yyyymmdd(d: date) -> str:
    return d.strftime("%Y%m%d")


def parse_dd(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def default_window(today: date, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> tuple[date, date]:
    """기본 창 = 오늘 포함 최근 N일. 정정·지연 접수를 잡으려고 며칠을 겹쳐 본다."""
    return today - timedelta(days=lookback_days - 1), today


def days_between(since: date, until: date) -> list[date]:
    return [since + timedelta(days=i) for i in range((until - since).days + 1)]


def row_from_item(item: dict, allowed: set[str]) -> dict | None:
    """list.json 한 행 → 원장 행. 분류 안 되거나 선택 유형 밖이면 None. screener 와 같은 분류기."""
    report_nm = item.get("report_nm", "") or ""
    kind, subtype, is_corr = classify(report_nm)
    if kind is None or kind not in allowed:
        return None
    rcept_dt = (item.get("rcept_dt") or "").strip()
    if len(rcept_dt) != 8 or not rcept_dt.isdigit():
        return None
    tdef = _BY_CODE[kind]
    corp_code = item.get("corp_code", "") or ""
    stock_code = (item.get("stock_code") or "").strip() or None
    dedup_key = f"{corp_code}:{kind}:{subtype}"
    if tdef.get("dedup_by_filer"):
        dedup_key += ":" + ((item.get("flr_nm") or "").strip() or item.get("rcept_no", ""))
    return {
        "rcept_no": item.get("rcept_no", ""),
        "rcept_dt": parse_dd(rcept_dt),
        "corp_code": corp_code,
        "corp_name": (item.get("corp_name") or "").strip(),
        "stock_code": stock_code,
        "corp_cls": (item.get("corp_cls") or "").strip() or None,
        "kind": kind,
        "subtype": subtype or None,
        "stage": _stage_tag(kind, report_nm, subtype),
        "is_correction": is_corr,
        "dedup_key": dedup_key,
        "report_nm": report_nm.strip(),
        "flr_nm": (item.get("flr_nm") or "").strip() or None,
        "sector": None, "industry": None, "sector_dd": None,
        "mktcap_won": None, "mktcap_dd": None,
        "detail_status": "scan_only", "detail": None, "detail_note": None,
        # 내부 — 상세 디스패치용(_fetch_detail 이 읽는 키). DB 에는 안 들어간다.
        "_detail_kind": tdef.get("detail_kind"),
        "_force_detail": _force_details(tdef, report_nm, is_corr),
        "filed_at": f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:8]}",
    }


def pick_snapshot(snaps: list[str], dd: str) -> str | None:
    """접수일 이전 가장 최신 스냅샷. 하나도 없으면 가장 오래된 것(초기 백필에서 그 앞 날짜는
    스냅샷이 없다 — 없다고 비워 두는 것보다 가장 가까운 것을 붙이고 `*_dd` 로 밝히는 편이 낫다)."""
    if not snaps:
        return None
    ordered = sorted(snaps)
    before = [s for s in ordered if s <= dd]
    return before[-1] if before else ordered[0]


def scan_codes_for(types: list[str]) -> list[str]:
    return sorted({_BY_CODE[t]["scan_code"] for t in types if t in _BY_CODE})


def db_row(row: dict) -> dict:
    """내부 키(_…, filed_at)를 뺀 DB 행. detail 은 jsonb 문자열로."""
    out = {k: v for k, v in row.items() if not k.startswith("_") and k != "filed_at"}
    out["detail"] = json.dumps(row["detail"], ensure_ascii=False) if row.get("detail") else None
    return out


# ── DB 보강 ────────────────────────────────────────────────────────────

def enrich(con, rows: list[dict]) -> None:
    """업종(업종분류 스냅샷)·시총(krx_weekly)을 접수일 이전 최신 스냅샷에서 붙인다. DB 만, DART 0콜."""
    tickers = sorted({r["stock_code"] for r in rows if r.get("stock_code")})
    if not tickers:
        return
    sec_snaps = [r[0] for r in con.execute("SELECT DISTINCT snap_dd FROM wise_sector").fetchall()]
    cap_snaps = [r[0] for r in con.execute(
        "SELECT DISTINCT price_dd FROM krx_weekly WHERE price_dd >= %s AND price_dd <= %s",
        (yyyymmdd(min(r["rcept_dt"] for r in rows) - timedelta(days=14)),
         yyyymmdd(max(r["rcept_dt"] for r in rows)))).fetchall()]
    if not cap_snaps:   # 창 안에 주간 시점이 없으면 그 이전 최신 하나
        prev = con.execute("SELECT max(price_dd) FROM krx_weekly WHERE price_dd <= %s",
                           (yyyymmdd(max(r["rcept_dt"] for r in rows)),)).fetchone()[0]
        cap_snaps = [prev] if prev else []
    sec_cache: dict[str, dict[str, tuple[str, str]]] = {}
    cap_cache: dict[str, dict[str, int]] = {}
    for r in rows:
        t = r.get("stock_code")
        if not t:
            continue
        dd = yyyymmdd(r["rcept_dt"])
        s_dd = pick_snapshot(sec_snaps, dd)
        if s_dd:
            if s_dd not in sec_cache:
                sec_cache[s_dd] = {a: (b, c) for a, b, c in con.execute(
                    "SELECT ticker, sector, industry FROM wise_sector WHERE snap_dd=%s AND ticker = ANY(%s)",
                    (s_dd, tickers)).fetchall()}
            hit = sec_cache[s_dd].get(t)
            if hit:
                r["sector"], r["industry"], r["sector_dd"] = hit[0], hit[1], s_dd
        c_dd = pick_snapshot(cap_snaps, dd)
        if c_dd:
            if c_dd not in cap_cache:
                cap_cache[c_dd] = {a: int(b) for a, b in con.execute(
                    "SELECT ticker, mktcap FROM krx_weekly WHERE price_dd=%s AND ticker = ANY(%s) AND mktcap IS NOT NULL",
                    (c_dd, tickers)).fetchall()}
            cap = cap_cache[c_dd].get(t)
            if cap is not None:
                r["mktcap_won"], r["mktcap_dd"] = cap, c_dd


def already_detailed(con, rcept_nos: list[str]) -> set[str]:
    if not rcept_nos:
        return set()
    return {r[0] for r in con.execute(
        "SELECT rcept_no FROM dart_events WHERE rcept_no = ANY(%s) AND detail_status IN ('parsed','partial')",
        (rcept_nos,)).fetchall()}


# ── 수집 ───────────────────────────────────────────────────────────────

async def scan_day(client, day: date, codes: list[str], allowed: set[str]) -> tuple[list[dict], list[dict]]:
    """하루치 전체시장 스캔 → (원장 행들, 코드별 커버리지). 코드는 서로 독립이라 함께 던진다."""
    dd = yyyymmdd(day)
    sem = asyncio.Semaphore(5)

    async def _one(code: str):
        async with sem:
            return code, await _scan_code_uncached(client, code, dd, dd, SCAN_MAX_PAGES)

    results = await asyncio.gather(*[_one(c) for c in codes], return_exceptions=True)
    rows: dict[str, dict] = {}
    coverage: list[dict] = []
    for code, res in zip(codes, results):
        if isinstance(res, BaseException):
            coverage.append({"scan_dd": day, "code": code, "total": None, "total_pages": None,
                             "fetched_pages": 0, "complete": False, "error": f"transport:{type(res).__name__}"})
            continue
        _code, r = res
        coverage.append({"scan_dd": day, "code": code, "total": r["total"], "total_pages": r["total_pages"],
                         "fetched_pages": r["received_pages"], "complete": r["complete"], "error": r["error"]})
        for it in r["items"]:
            row = row_from_item(it, allowed)
            if row and row["rcept_no"] and row["rcept_no"] not in rows:
                rows[row["rcept_no"]] = row
    return list(rows.values()), coverage


async def fetch_details(client, rows: list[dict], kinds: tuple[str, ...], max_calls: int,
                        skip: set[str]) -> dict:
    """상세 파서를 태운다. 정정·해지처럼 판단이 갈리는 건을 먼저, 그다음 시총 큰 순.

    콜 상한은 **클라이언트의 전역 콜 카운터**로 잰다. `_fetch_detail` 이 채우는 `running["calls"]`
    는 태스크마다 스냅샷 차이를 더하는 방식이라 동시에 돌면 남의 콜까지 겹쳐 세어 실제의 몇 배가
    된다(260916 실측: 전역 30콜을 83으로 셌다). 상한이 이르게 걸리는 쪽이라 위험하진 않지만
    「600콜 상한」이 600콜이어야 말이 된다."""
    targets = [r for r in rows if r.get("_detail_kind") in kinds and r["rcept_no"] not in skip]
    targets.sort(key=lambda r: (not r["_force_detail"], -(r.get("mktcap_won") or 0)))
    running = {"calls": 0}
    base = client.api_call_snapshot()
    done = skipped = 0
    sem = asyncio.Semaphore(4)

    async def _run(r: dict):
        nonlocal done, skipped
        async with sem:
            # ★ 상한 검사는 세마포어 **안**에서. 밖에서 하면 모든 태스크가 첫 await 전에 한꺼번에
            #   검사를 통과해(그때 카운터는 0) 상한이 무력해진다 — 260916 실측 150 상한에 303콜.
            if client.api_call_snapshot() - base >= max_calls:
                skipped += 1
                r["detail_note"] = "상세 콜 상한 도달 — 다음 실행에서 채운다"
                return
            res = await _fetch_detail(r, running)
        r["detail_status"] = res["detail_status"]
        r["detail"] = res.get("fields") or None
        if res.get("note"):
            r["detail_note"] = res["note"]
        done += 1

    await asyncio.gather(*[_run(r) for r in targets])
    return {"targets": len(targets), "done": done, "skipped": skipped,
            "dart_calls": client.api_call_snapshot() - base}


async def run(since: date, until: date, types: list[str], details: bool, detail_kinds: tuple[str, ...],
              max_detail_calls: int, dry: bool) -> int:
    from open_proxy_mcp.dart.client import get_dart_client
    import psycopg

    allowed = {t for t in types if t in _BY_CODE}
    unknown = sorted(set(types) - allowed)
    if unknown:
        print(f"모르는 유형 무시: {', '.join(unknown)} — 가능: {', '.join(_BY_CODE)}")
    codes = scan_codes_for(sorted(allowed))
    client = get_dart_client()
    con = None
    if not dry:
        con = psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=20)
        con.autocommit = True
        con.execute(DDL)
    print(f"원장 배치 {yyyymmdd(since)}~{yyyymmdd(until)} · 유형 {len(allowed)} · 스캔 코드 {codes} · "
          f"상세 {'on' if details else 'off'}{'' if not dry else ' · DRY(저장 안 함)'}")
    calls0 = client.api_call_snapshot()
    tot_rows = tot_new = 0
    for day in days_between(since, until):
        rows, coverage = await scan_day(client, day, codes, allowed)
        if con is not None:
            enrich(con, rows)
        stats = {}
        if details and rows:
            skip = already_detailed(con, [r["rcept_no"] for r in rows]) if con is not None else set()
            stats = await fetch_details(client, rows, detail_kinds, max_detail_calls, skip)
        if con is not None:
            with con.cursor() as cur:
                for r in rows:
                    cur.execute(UPSERT_EVENT, db_row(r))
                for c in coverage:
                    cur.execute(UPSERT_SCAN, c)
        by_kind: dict[str, int] = {}
        for r in rows:
            by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
        incomplete = [c["code"] for c in coverage if not c["complete"]]
        print(f"  {yyyymmdd(day)}: {len(rows):>4}건 " + " ".join(f"{k}={v}" for k, v in sorted(by_kind.items()))
              + (f" · 상세 {stats.get('done', 0)}/{stats.get('targets', 0)} (콜 {stats.get('dart_calls', 0)})" if stats else "")
              + (f" · ⚠ 불완전 코드 {incomplete}" if incomplete else ""))
        tot_rows += len(rows)
    print(f"합계 {tot_rows}건 · DART 콜 {client.api_call_snapshot() - calls0}"
          + ("" if dry else " · dart_events 에 upsert"))
    if con is not None:
        n_all, n_det = con.execute(
            "SELECT count(*), count(*) FILTER (WHERE detail_status IN ('parsed','partial')) FROM dart_events").fetchone()
        span = con.execute("SELECT min(rcept_dt), max(rcept_dt) FROM dart_events").fetchone()
        print(f"원장 현황: {n_all:,}행 (상세 {n_det:,}) · {span[0]} ~ {span[1]}")
        con.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    today = datetime.now(_KST).date()
    d0, d1 = default_window(today)
    ap.add_argument("--since", default=yyyymmdd(d0), help=f"시작일 YYYYMMDD (기본 {yyyymmdd(d0)} — 최근 {DEFAULT_LOOKBACK_DAYS}일)")
    ap.add_argument("--until", default=yyyymmdd(d1), help=f"종료일 YYYYMMDD (기본 오늘 {yyyymmdd(d1)})")
    ap.add_argument("--types", default=",".join(DEFAULT_TYPES),
                    help="쉼표 구분 유형 코드 (기본: opt-in 제외 전부)")
    ap.add_argument("--no-details", action="store_true", help="상세 파서 생략 — 백필용(스캔만)")
    ap.add_argument("--detail-kinds", default=",".join(DEFAULT_DETAIL_KINDS),
                    help="상세를 태울 유형 (기본 order,earnings,treasury,dilutive,dividend)")
    ap.add_argument("--max-detail-calls", type=int, default=DEFAULT_MAX_DETAIL_CALLS,
                    help="하루치 상세에 쓸 DART 콜 상한")
    ap.add_argument("--dry", action="store_true", help="DB 에 쓰지 않고 요약만 (DDL 도 안 만든다)")
    a = ap.parse_args(argv)
    since, until = parse_dd(a.since), parse_dd(a.until)
    if since > until:
        ap.error("--since 가 --until 보다 뒤다")
    if (until - since).days > 400:
        ap.error("한 번에 400일 넘게 돌리지 않는다 — 나눠서 백필할 것")
    types = [t.strip() for t in a.types.split(",") if t.strip()]
    kinds = tuple(k.strip() for k in a.detail_kinds.split(",") if k.strip())
    if not a.dry and not os.getenv("DATABASE_URL"):
        print("DATABASE_URL 이 없다 — --dry 로 요약만 볼 수 있다.", file=sys.stderr)
        return 1
    return asyncio.run(run(since, until, types, not a.no_details, kinds, a.max_detail_calls, a.dry))


if __name__ == "__main__":
    raise SystemExit(main())
