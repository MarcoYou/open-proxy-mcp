#!/usr/bin/env python3
"""OPM 사용 통계 도구 — 요청별 이벤트를 누적하고 일/주 통계를 뽑는다.

저장 백엔드(통계 읽기): **DATABASE_URL 있으면 Postgres(Supabase)**, 없으면 로컬 sqlite.
앱(open_proxy_mcp/usage.py)이 요청 시점에 직접 기록한 events를 그대로 조회한다 → 무손실·합산 불필요.

키는 **SHA-256 해시로만** 저장(평문 DART 키 미보관). 시각 버킷(일/주)은 **KST(UTC+9)** 기준.

사용(읽기 — 백엔드 자동):
  python3 scripts/usage_tracker.py --stats        # 일/주/사용자/세션 상세 통계
  python3 scripts/usage_tracker.py --export DIR    # daily.csv·weekly.csv·users.csv·summary.json
  python3 scripts/usage_tracker.py --report       # 요약만
  python3 scripts/usage_tracker.py --paths [일수] # 원문 경로(주/viewer/KIND) + 폴백이 문 시간
  python3 scripts/usage_tracker.py --corps [일수] # 조회된 기업 순위(사용자와 분리된 집계)
  python3 scripts/usage_tracker.py --migrate-local # 로컬 sqlite events → Postgres 1회 이전(시드)

legacy(로컬 sqlite 수집 — Postgres 전환 전 과거 데이터 확보용):
  python3 scripts/usage_tracker.py --pull       # Fly 머신 볼륨에서 합산
  python3 scripts/usage_tracker.py              # 로그 API 증분 수집
  python3 scripts/usage_tracker.py --backfill    # 로그 API 7일 전체 재수집
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

APP = "open-proxy-mcp"
API = f"https://api.fly.io/api/v1/apps/{APP}/logs"
RETENTION_MARGIN_NS = 6 * 24 * 3600 * 10**9   # 첫 실행/오래 비웠을 때 시작점(6일 전 — 7일 보존 안쪽)
MAX_PAGES = 4000
SESSION_GAP_S = 30 * 60   # 30분 이상 끊기면 새 세션
KST = timezone(timedelta(hours=9))

# inbound MCP 요청 한 줄에서 (키, HTTP status) 추출
REQ_RE = re.compile(r'/mcp\?opendart=([0-9a-fA-F]{40})[^"]*"\s+(\d{3})')

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "usage" / "usage.db"

# DATABASE_URL 있으면 통계를 Postgres(Supabase)에서 읽음. 로컬 .env에서도 로드.
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass
DATABASE_URL = os.environ.get("DATABASE_URL")


def using_pg() -> bool:
    return bool(DATABASE_URL)


def _pg_conn():
    import psycopg
    return psycopg.connect(DATABASE_URL, connect_timeout=15)


def fetch_rows():
    """모든 (ts_ns, key_hash) 정렬 반환 (self 포함). 백엔드 자동 선택."""
    if using_pg():
        con = _pg_conn()
        rows = con.execute("SELECT ts_ns, key_hash FROM tool_call_events ORDER BY ts_ns").fetchall()
        con.close()
        return [(int(t), h) for t, h in rows]
    con = db()
    return con.execute("SELECT ts_ns, key_hash FROM events ORDER BY ts_ns").fetchall()


#: **프로토콜(핸드셰이크) 요청인가** — MCP 클라이언트가 *사람이 시키지 않아도* 보내는 것.
#: `initialize`·`ping`·`tools/list`·`notifications/*` 가 전체의 81.8% 이고, `ping` 27,609건은
#: **키 2개**에서 나온다(260810 실측). 이걸 실제 도구 호출과 한 표에 세우면 지표가 통째로
#: 오염된다 — 「평균 응답 1,522ms」는 near-0 인 핸드셰이크가 눌러 놓은 값이었다.
#: tool 이 None 인 것(본문 파싱 실패: 배치 요청·GET/DELETE)도 도구 호출이 아니므로 여기 넣는다.
#: **이 함수가 정의의 SSOT** — `startup_metrics.is_sub` 가 이걸 재사용한다(정의가 둘이면 갈라진다).
def is_protocol(tool) -> bool:
    return (not tool) or ("/" in tool) or tool in {"initialize", "ping"}


def fetch_tool_latency():
    """(tool, key_hash, latency_ms, is_error) 리스트. 옛 스키마(열 없음)면 is_error=None 패딩.
    PG(tool_call_events)와 sqlite(events)는 테이블명이 달라(260706 PG측 rename) 쿼리 분리."""
    if using_pg():
        sql = "SELECT tool, key_hash, latency_ms, is_error FROM tool_call_events"
        old = "SELECT tool, key_hash, latency_ms FROM tool_call_events"
        con = _pg_conn()
        try:
            try:
                rows = con.execute(sql).fetchall()
            except Exception:  # is_error 컬럼 미생성(구서버) — 롤백 후 구스키마로
                con.rollback()
                rows = [(*r, None) for r in con.execute(old).fetchall()]
        finally:
            con.close()
        return rows
    sql = "SELECT tool, key_hash, latency_ms, is_error FROM events"
    old = "SELECT tool, key_hash, latency_ms FROM events"
    try:
        return db().execute(sql).fetchall()
    except sqlite3.OperationalError:
        try:
            return [(*r, None) for r in db().execute(old).fetchall()]
        except sqlite3.OperationalError:
            return []


def migrate_local_to_pg():
    """로컬 sqlite events를 Postgres(tool_call_events)로 1회 이전(ON CONFLICT dedup). 과거 데이터 시드용."""
    if not using_pg():
        raise SystemExit("DATABASE_URL이 필요합니다 (.env 또는 환경변수).")
    src = db().execute("SELECT event_id, ts_ns, key_hash, status FROM events").fetchall()
    pg = _pg_conn()
    pg.execute("CREATE TABLE IF NOT EXISTS tool_call_events(event_id text PRIMARY KEY, "
               "ts_ns bigint NOT NULL, key_hash text NOT NULL, status int)")
    pg.cursor().executemany(
        "INSERT INTO tool_call_events(event_id, ts_ns, key_hash, status) VALUES(%s,%s,%s,%s) "
        "ON CONFLICT (event_id) DO NOTHING", src)
    pg.commit()
    total = pg.execute("SELECT COUNT(*) FROM tool_call_events").fetchone()[0]
    pg.close()
    print(f"로컬 {len(src)}건 → Postgres 이전 완료 (PG 총 {total}건)")

# 본인(운영자) 키 — 외부 사용자 통계에서 제외. 평문 미보관, SHA-256 해시로만.
#   6f02e8… = 운영자 opendart 키의 SHA-256 (평문 프리픽스는 주석에도 남기지 않음)
SELF_HASHES = {
    "6f02e8598b1bdcda660c970ca9c07c1ffba1d4d8ec193157991f7dc2a9173c30",
}


# ── 인프라 ────────────────────────────────────────────────────────────────
def fly_token() -> str:
    tok = os.environ.get("FLY_API_TOKEN")
    if tok:
        return tok.strip()
    fly = shutil.which("fly") or "/Users/marcoyou/.fly/bin/fly"
    out = subprocess.run([fly, "auth", "token"], capture_output=True, text=True, timeout=30).stdout
    for line in out.splitlines():
        line = line.strip()
        if line and "deprecat" not in line.lower():
            return line
    raise SystemExit("Fly 토큰을 얻지 못했습니다 (fly auth token / FLY_API_TOKEN 확인).")


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS events(
            event_id TEXT PRIMARY KEY,   -- Fly 로그 항목 고유 id → 중복 실행에도 dedup
            ts_ns    INTEGER NOT NULL,   -- epoch nanoseconds (UTC)
            key_hash TEXT NOT NULL,
            status   INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_events_hash ON events(key_hash);
        CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts_ns);
        CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
        """
    )
    return con


def get_cursor(con: sqlite3.Connection) -> str:
    now_ns = time.time_ns()
    floor = now_ns - RETENTION_MARGIN_NS
    row = con.execute("SELECT v FROM meta WHERE k='cursor'").fetchone()
    if not row:
        return str(floor)
    return str(max(int(row[0]), floor))  # 7일보다 오래 비웠으면 보존한도 안쪽으로 클램프


# ── 수집 ──────────────────────────────────────────────────────────────────
def fetch_events(token: str, cursor: str):
    """cursor(ns) 이후 로그를 forward로 드레인하며 요청 이벤트 추출.
    일시 실패는 백오프 재시도, 그래도 안 되면 진행분과 마지막 정상 cursor 반환(다음 run이 이어받음)."""
    rows: list[tuple] = []
    pages = 0
    while pages < MAX_PAGES:
        url = f"{API}?next_token={cursor}"
        payload = None
        for attempt in range(8):
            try:
                req = urllib.request.Request(url, headers={"Authorization": f"FlyV1 {token}"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    payload = json.load(r)
                break
            except urllib.error.HTTPError as e:
                # Fly 로그 API는 빠른 연속 페이징 시 간헐적 401/429를 냄 → 백오프 후 토큰 갱신 재시도
                if e.code in (401, 403, 429) and attempt >= 1:
                    try:
                        token = fly_token()
                    except Exception:
                        pass
                if attempt == 7:
                    sys.stderr.write(f"[warn] page fetch 실패, 진행분 저장 후 중단: {e}\n")
                time.sleep(min(2 ** attempt, 30))
            except Exception as e:
                if attempt == 7:
                    sys.stderr.write(f"[warn] page fetch 실패, 진행분 저장 후 중단: {e}\n")
                time.sleep(min(2 ** attempt, 30))
        if payload is None:
            break
        data = payload.get("data", [])
        if not data:
            break
        for it in data:
            attr = it.get("attributes", {})
            m = REQ_RE.search(attr.get("message", ""))
            if not m:
                continue
            ev_id = it.get("id")
            ts_ns = _id_ns(ev_id) or _iso_ns(attr.get("timestamp", ""))
            if not ev_id or ts_ns is None:
                continue
            khash = hashlib.sha256(m.group(1).lower().encode()).hexdigest()
            rows.append((ev_id, ts_ns, khash, int(m.group(2))))
        nxt = payload.get("meta", {}).get("next_token")
        if not nxt or nxt == cursor:
            break
        cursor = nxt
        pages += 1
        time.sleep(0.15)
    return rows, cursor


def _id_ns(ev_id):
    if not ev_id:
        return None
    tail = ev_id.rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _iso_ns(ts):
    try:
        base = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        return int(base.timestamp() * 10**9)
    except Exception:
        return None


def pull_from_fly(con: sqlite3.Connection) -> int:
    """각 Fly 머신(볼륨 분리)에서 events를 긁어 로컬 DB에 합산. event_id PK로 dedup."""
    fly = shutil.which("fly") or "/Users/marcoyou/.fly/bin/fly"
    out = subprocess.run([fly, "machines", "list", "--json", "-a", APP],
                         capture_output=True, text=True, timeout=60)
    try:
        machines = json.loads(out.stdout)
    except Exception:
        sys.stderr.write(f"머신 목록 파싱 실패: {out.stdout[:200]} {out.stderr[:200]}\n")
        return 0
    ids = [m.get("id") for m in machines if m.get("id")]
    print(f"머신 {len(ids)}대: {', '.join(ids)}")
    total_new = 0
    for mid in ids:
        r = subprocess.run(
            [fly, "ssh", "console", "--machine", mid, "-a", APP, "--pty=false",
             "-C", "python -m open_proxy_mcp.usage dump"],
            capture_output=True, text=True, timeout=180,
        )
        rows = []
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line.startswith("["):
                continue
            try:
                ev = json.loads(line)
                rows.append((ev[0], int(ev[1]), ev[2], ev[3]))
            except Exception:
                continue
        new = upsert(con, rows)
        con.commit()
        total_new += new
        print(f"  {mid}: {len(rows)} 이벤트 수신 · 신규 {new}")
        if not rows and r.stderr:
            sys.stderr.write(f"  [{mid}] stderr: {r.stderr[:200]}\n")
    return total_new


def upsert(con: sqlite3.Connection, rows) -> int:
    before = con.total_changes
    con.executemany(
        "INSERT OR IGNORE INTO events(event_id, ts_ns, key_hash, status) VALUES(?,?,?,?)", rows
    )
    return con.total_changes - before


# ── 통계 ──────────────────────────────────────────────────────────────────
def _kst(ts_ns: int) -> datetime:
    return datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).astimezone(KST)


def daily_stats(rows):
    """{day(KST 'YYYY-MM-DD'): {"users", "requests", "new_users"}}.
    new_users = 그 날 로그에 '처음 등장'한 key 수(수집 시작 6/29 이후 기준 — 진짜 최초 사용이
    아니라 '우리 로그상 첫 관측'. 수집 첫날은 전원이 신규로 잡히는 게 정상)."""
    first_seen: dict[str, str] = {}
    by_day = defaultdict(lambda: [set(), 0])
    for ts, h in rows:
        d = _kst(ts).strftime("%Y-%m-%d")
        if h not in first_seen or d < first_seen[h]:
            first_seen[h] = d
        by_day[d][0].add(h)
        by_day[d][1] += 1
    new_by_day: dict[str, int] = defaultdict(int)
    for h, d in first_seen.items():
        new_by_day[d] += 1
    return {d: {"users": len(u), "requests": n, "new_users": new_by_day[d]}
            for d, (u, n) in sorted(by_day.items())}


def fetch_error_rows():
    """(ts_ns, key_hash, is_error) — 일별 오류율 버킷용. is_error 컬럼 없는 구스키마면 [] (오류율 생략)."""
    if using_pg():
        con = _pg_conn()
        try:
            try:
                rows = con.execute("SELECT ts_ns, key_hash, is_error FROM tool_call_events").fetchall()
            except Exception:  # is_error 미생성 구서버
                con.rollback()
                return []
        finally:
            con.close()
        return [(int(t), h, e) for t, h, e in rows]
    try:
        return [(int(t), h, e)
                for t, h, e in db().execute("SELECT ts_ns, key_hash, is_error FROM events").fetchall()]
    except Exception:
        return []


def classify_outcome(is_error, error_kind) -> tuple[str, str | None]:
    """한 행의 결말 → (갈래, 분류태그). DB 없이 시험할 수 있게 순수 함수로 뺐다.

    **판정불가와 미기록을 섞으면 안 된다** — 하나는 경보, 하나는 역사다.
      kind='unclassifiable' = 스캐너가 **응답을 못 읽었다**(늘면 응답 형식이 바뀐 것)
      kind=NULL             = `is_error` 컬럼이 생기기 전(260802) 행. 소급 불가.
    처음 짤 때 둘 다 「판정불가」로 세었더니 3,528건이 잡혀 경보처럼 보였는데,
    실제로는 **전부 구스키마였고 진짜 판정불가는 0건**이었다.

    `dart_` 접두는 상류(DART)가 못 준 것 — 우리 크래시와 대응이 다르다(전자는 사용자
    안내, 후자는 우리가 고칠 것). 013/404 는 실패가 아니라 **답**이라 정상 쪽에 두되
    빈도는 따로 센다.
    """
    if is_error is None:
        return ("판정불가", "unclassifiable") if error_kind == "unclassifiable" \
            else ("미기록(구스키마)", None)
    if is_error:
        k = error_kind or "untagged"
        return ("상류(DART)" if k.startswith("dart_") else "우리오류"), k
    if error_kind in ("no_data", "not_found"):
        return "자료없음", error_kind
    return "정상", None


def outcome_breakdown():
    """도구 호출의 **결말**을 네 갈래로 나눈다. 종전엔 `WHERE is_error=true` 하나만 봤다.

    그 하나로는 세 가지가 안 보였다 —
      · **상류 실패**: DART 가 못 준 것을 크래시 대신 안내로 낮춰 보내(degrade) 예전엔
        아예 성공으로 세어졌다. 260810 에 `dart_*` 표지를 달아 이제 잡힌다.
      · **자료 없음**: 013/404 는 실패가 아니라 **답**이다. 실패로 세면 오류율이 부풀고
        진짜 고장이 그 안에 묻힌다. 그래서 따로 센다.
      · **판정불가**: 응답을 못 읽어 `is_error=NULL` 로 남긴 것(260810 셋째 상태).
        `WHERE is_error=true` 는 이걸 영원히 못 본다 — 스캐너가 멀어도 오류율이 0으로 수렴한다.

    프로토콜(핸드셰이크)은 결말이랄 게 없어 분모에서 뺀다.
    반환 {'우리오류':n, '상류(DART)':n, '자료없음':n, '판정불가':n, '정상':n, 'kinds':{...}}
    """
    cols = "key_hash, tool, is_error, error_kind, weak_kinds"
    if using_pg():
        con = _pg_conn()
        try:
            rows = con.execute(f"SELECT {cols} FROM tool_call_events").fetchall()
        except Exception:      # weak_kinds 미생성 구서버 — 그 컬럼만 빼고 재시도
            con.rollback()
            try:
                rows = [(*r, None) for r in con.execute(
                    "SELECT key_hash, tool, is_error, error_kind FROM tool_call_events").fetchall()]
            except Exception:
                con.rollback(); return {}
        finally:
            con.close()
    else:
        try:
            rows = db().execute(f"SELECT {cols} FROM events").fetchall()
        except Exception:
            try:
                rows = [(*r, None) for r in db().execute(
                    "SELECT key_hash, tool, is_error, error_kind FROM events").fetchall()]
            except Exception:
                return {}
    out = defaultdict(int)
    kinds = defaultdict(int)
    weak = defaultdict(int)
    for h, tool, err, kind, weak_kinds in rows:
        if h in SELF_HASHES or is_protocol(tool):
            continue
        bucket, tag = classify_outcome(err, kind)
        out[bucket] += 1
        if tag:
            kinds[tag] += 1
        if weak_kinds:
            out["추정해석"] += 1
            for wk in weak_kinds.split(","):
                weak[wk or "unknown"] += 1
    out["kinds"] = dict(kinds)
    out["weak"] = dict(weak)
    return dict(out)


def print_outcomes():
    """`--report`·`--stats` 공용 — 결말 분해를 한 블록으로 찍는다."""
    o = outcome_breakdown()
    if not o:
        return
    # 갈래는 이 다섯뿐이다. 「추정해석」은 가로지르는 성질이라 분모에 넣으면 합이 100%를
    # 넘고, `kinds`/`weak` 는 dict 라 더하면 터진다(실제로 터뜨렸다).
    _BUCKETS = ("정상", "자료없음", "상류(DART)", "우리오류", "판정불가", "미기록(구스키마)")
    total = sum(o.get(k, 0) for k in _BUCKETS)
    if not total:
        return
    print(f"\n[도구 호출 결말] 총 {total:,}건 (핸드셰이크 제외)")
    # 다섯 갈래는 **0이어도 찍는다** — 0이 곧 신호다(상류 실패 0건 = DART 가 안 죽었다,
    # 판정불가 0건 = 스캐너가 응답을 읽고 있다). 안 찍으면 「없음」과 「안 봄」이 같아 보인다.
    for k in _BUCKETS:
        n = o.get(k, 0)
        if k == "미기록(구스키마)" and not n:
            continue                              # 이건 역사라 0이면 굳이 안 보여도 된다
        print(f"  {k:<14} {n:>8,}  {100 * n / total:>5.2f}%")
    kinds = {k: v for k, v in o.get("kinds", {}).items() if v}
    if kinds:
        parts = ", ".join(f"{k} {v}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])[:8])
        print(f"  └ 분류: {parts}")
    # 「추정해석」은 결말 갈래가 아니라 **가로지르는 성질**이다 — 정상 응답이면서
    # 동시에 「이름이 정확히 안 맞아 찍었다」일 수 있다. 그래서 위 백분율에 안 섞고 따로 낸다.
    if o.get("추정해석"):
        w = o.get("weak", {})
        parts = ", ".join(f"{k} {v}" for k, v in sorted(w.items(), key=lambda kv: -kv[1]))
        print(f"  · 회사명 추정해석 {o['추정해석']:,}건 ({100 * o['추정해석'] / total:.2f}%)"
              f" — {parts}")
        print("    (사용자에게는 warning 으로 이미 나간다. 원문은 저장하지 않고 방식만 센다)")
    if o.get("판정불가"):
        print("  ⚠ 판정불가 = 스캐너가 응답을 못 읽었다. 늘면 응답 형식이 바뀐 것이다")
    if o.get("미기록(구스키마)"):
        print("  · 미기록은 is_error 컬럼이 생기기 전(260802) 행 — 소급 불가, 경보 아님")


def daily_errors(err_rows):
    """{day: (errors, err_known)} — err_rows=(ts, h, is_error) 외부 사용자만. err_known=is_error 기록된 건수."""
    by_day = defaultdict(lambda: [0, 0])  # [errors, known]
    for ts, h, e in err_rows:
        if h in SELF_HASHES or e is None:
            continue
        d = _kst(ts).strftime("%Y-%m-%d")
        by_day[d][1] += 1
        if e:
            by_day[d][0] += 1
    return {d: (er, kn) for d, (er, kn) in by_day.items()}


def weekly_stats(rows):
    """{week('YYYY-Www'): (unique_users, requests)}"""
    by_w = defaultdict(lambda: [set(), 0])
    for ts, h in rows:
        iso = _kst(ts).isocalendar()
        w = f"{iso[0]}-W{iso[1]:02d}"
        by_w[w][0].add(h)
        by_w[w][1] += 1
    return {w: (len(u), n) for w, (u, n) in sorted(by_w.items())}


def per_user(rows):
    """사용자별: 요청수·활성일수·최초/최종·기간(일)·세션수·총사용시간(분)."""
    ev = defaultdict(list)
    for ts, h in rows:
        ev[h].append(ts)
    out = {}
    for h, times in ev.items():
        times.sort()
        days = {_kst(t).strftime("%Y-%m-%d") for t in times}
        # 세션 분할(30분 갭)
        sessions = []
        s_start = s_last = times[0]
        for t in times[1:]:
            if t - s_last > SESSION_GAP_S * 10**9:
                sessions.append((s_start, s_last))
                s_start = t
            s_last = t
        sessions.append((s_start, s_last))
        total_min = sum((e - s) / 1e9 / 60 for s, e in sessions)
        span_days = (times[-1] - times[0]) / 1e9 / 86400
        out[h] = {
            "requests": len(times),
            "active_days": len(days),
            "first": _kst(times[0]).strftime("%Y-%m-%d %H:%M"),
            "last": _kst(times[-1]).strftime("%Y-%m-%d %H:%M"),
            "span_days": round(span_days, 1),
            "sessions": len(sessions),
            "total_minutes": round(total_min, 1),
        }
    return dict(sorted(out.items(), key=lambda kv: kv[1]["requests"], reverse=True))


def _external(all_rows):
    return [(t, h) for (t, h) in all_rows if h not in SELF_HASHES]


def tool_stats(tl_rows):
    """[(tool, requests, unique_users, errors, err_known)] 요청수 내림차순 + latency(평균·p50·p95).

    **latency 는 실제 도구 호출만 잰다.** 260810 이전에는 `if tool:` 밖에서 모아 핸드셰이크
    (near-0, 전체의 81.8%)까지 평균에 들어갔다 — 「평균 응답 1,522ms」가 그렇게 나온 값이고,
    그 숫자로 인프라를 판단하면 안 된다.
    평균만 내지 않는 이유: 문서 파싱은 꼬리가 길어(사업보고서 수십MB) 평균이 중앙값을
    한참 웃돈다. p50/p95 를 같이 내야 「보통 얼마나 걸리나」와 「최악이 얼마나 되나」가 갈린다.
    """
    by_tool = defaultdict(lambda: [0, set(), 0, 0])
    lat = []
    for tool, h, latency, is_err in tl_rows:
        if h in SELF_HASHES or is_protocol(tool):
            continue
        if latency is not None:
            lat.append(latency)
        rec = by_tool[tool]
        rec[0] += 1
        rec[1].add(h)
        if is_err is not None:
            rec[3] += 1
            if is_err:
                rec[2] += 1
    ranked = sorted(((t, n, len(u), e, k) for t, (n, u, e, k) in by_tool.items()),
                    key=lambda x: x[1], reverse=True)
    lat.sort()

    def _p(q):
        return lat[min(len(lat) - 1, int(len(lat) * q))] if lat else None

    return ranked, (round(sum(lat) / len(lat)) if lat else None), _p(0.5), _p(0.95)


def report(all_rows):
    rows = _external(all_rows)
    users = {h for _, h in rows}
    total_all = len({h for _, h in all_rows})
    rng = ""
    if rows:
        rng = f" · 기간 {_kst(rows[0][0]):%Y-%m-%d} ~ {_kst(rows[-1][0]):%Y-%m-%d}"
    print(f"[source: {'Postgres(Supabase)' if using_pg() else 'local sqlite'}]")
    print(f"고유 사용자(외부): {len(users)}   [전체 {total_all} − 본인 {total_all - len(users)}]")
    print(f"총 요청(외부): {len(rows)}{rng}")
    # 요청 수만 보면 81.8% 가 핸드셰이크라 「사용량」이 아니라 「연결 횟수」가 된다.
    _r, _avg, _p50, _p95 = tool_stats(fetch_tool_latency())
    _sub = sum(n for _, n, *_ in _r)
    print(f"  그중 도구 호출: {_sub:,} ({100 * _sub / max(1, len(rows)):.1f}%) "
          f"— 나머지는 클라이언트가 자동으로 보내는 연결·점검 통신")
    if _avg is not None:
        print(f"  도구 호출 응답: 평균 {_avg} ms · p50 {_p50} · p95 {_p95}")
    print_outcomes()


def stats(all_rows):
    rows = _external(all_rows)
    if not rows:
        print("이벤트 없음 — 먼저 수집/기록을 실행하세요.")
        return
    daily = daily_stats(rows)
    weekly = weekly_stats(rows)
    users = per_user(rows)
    derr = daily_errors(fetch_error_rows())

    print("=" * 56)
    print("OPM 사용 통계 (외부 사용자, KST 기준)")
    print("=" * 56)
    report(all_rows)

    print("\n[일별]  날짜         단일사용자  신규  요청수  인당요청  오류율")
    for d, v in daily.items():
        u, n, nu = v["users"], v["requests"], v["new_users"]
        per = n / u if u else 0
        er, kn = derr.get(d, (0, 0))
        erate = f"{er/kn*100:.1f}%" if kn else "-"
        print(f"        {d}      {u:>6}  {nu:>4}  {n:>6}   {per:>6.1f}   {erate:>6}")

    print("\n[주별]  주          단일사용자  요청수")
    for w, (u, n) in weekly.items():
        print(f"        {w}    {u:>6}   {n:>6}")

    returning = sum(1 for v in users.values() if v["active_days"] >= 2)
    avg_min = sum(v["total_minutes"] for v in users.values()) / len(users)
    avg_req = sum(v["requests"] for v in users.values()) / len(users)
    print(f"\n[요약] 외부 사용자 {len(users)}명 · 재방문(2일+) {returning}명 "
          f"· 평균 {avg_req:.1f}요청/인 · 평균 사용 {avg_min:.1f}분/인")

    # 집중도: users는 requests 내림차순 정렬됨(per_user) — 누적 90% 도달까지 필요한 사용자 수
    total_req = sum(v["requests"] for v in users.values())
    cum = 0
    top_n_for_90 = 0
    for v in users.values():
        cum += v["requests"]
        top_n_for_90 += 1
        if cum >= total_req * 0.9:
            break
    print(f"[집중도] 상위 {top_n_for_90}명({top_n_for_90 / len(users) * 100:.1f}%)이 "
          f"전체 요청의 90%를 차지")

    # 결말 분해 — 종전 [오류종류]는 `WHERE is_error=true` 만 봐서 상류실패·자료없음·
    # 판정불가를 전부 놓쳤다. outcome_breakdown 주석 참조.
    print_outcomes()

    print("\n[사용자 Top 15]  요청  활성일  기간(일)  세션  총사용(분)   최초 ~ 최종")
    for h, v in list(users.items())[:15]:
        print(f"  {h[:10]}  {v['requests']:>5} {v['active_days']:>6} {v['span_days']:>8} "
              f"{v['sessions']:>5} {v['total_minutes']:>9}   {v['first']} ~ {v['last']}")

    ranked, avg_lat, p50, p95 = tool_stats(fetch_tool_latency())
    if avg_lat is not None:
        print(f"\n[성능] 도구 호출만 — 평균 {avg_lat} ms · p50 {p50} ms · p95 {p95} ms")
    if ranked:
        print("\n[기능(tool) Top 15]  요청  사용자  오류(측정분)")
        for t, n, u, e, k in ranked[:15]:
            err = f"{e}/{k} ({e/k*100:.0f}%)" if k else "-"
            print(f"  {t:<28} {n:>5} {u:>6}  {err}")


def export(all_rows, outdir: str):
    rows = _external(all_rows)
    d = Path(outdir)
    d.mkdir(parents=True, exist_ok=True)
    daily = daily_stats(rows)
    weekly = weekly_stats(rows)
    users = per_user(rows)
    derr = daily_errors(fetch_error_rows())

    with open(d / "daily.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "unique_users", "new_users", "requests", "req_per_user", "errors", "err_known"])
        for k, v in daily.items():
            er, kn = derr.get(k, (0, 0))
            per = round(v["requests"] / v["users"], 1) if v["users"] else 0
            w.writerow([k, v["users"], v["new_users"], v["requests"], per, er, kn])
    with open(d / "weekly.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["week", "unique_users", "requests"])
        for k, (u, n) in weekly.items(): w.writerow([k, u, n])
    with open(d / "users.csv", "w", newline="") as f:
        cols = ["requests", "active_days", "first", "last", "span_days", "sessions", "total_minutes"]
        w = csv.writer(f); w.writerow(["user_hash"] + cols)
        for h, v in users.items(): w.writerow([h] + [v[c] for c in cols])
    ranked, avg_lat, lat_p50, lat_p95 = tool_stats(fetch_tool_latency())
    with open(d / "tools.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["tool", "requests", "unique_users", "errors", "err_known"])
        for t, n, u, e, k in ranked: w.writerow([t, n, u, e, k])
    summary = {
        "unique_users_external": len({h for _, h in rows}),
        "total_requests_external": len(rows),
        "returning_users_2day": sum(1 for v in users.values() if v["active_days"] >= 2),
        "avg_minutes_per_user": round(sum(v["total_minutes"] for v in users.values()) / max(len(users), 1), 1),
        "avg_latency_ms": avg_lat,          # 도구 호출만 (핸드셰이크 제외, 260810)
        "latency_p50_ms": lat_p50, "latency_p95_ms": lat_p95,
        "top_tools": [{"tool": t, "requests": n, "users": u, "errors": e, "err_known": k}
                      for t, n, u, e, k in ranked[:20]],
        "daily": daily,
        "weekly": weekly,
    }
    with open(d / "summary.json", "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"내보냄 → {d}/ (daily.csv, weekly.csv, users.csv, tools.csv, summary.json)")


# ── 진입점 ────────────────────────────────────────────────────────────────
def collect(con):
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    token = fly_token()
    cursor = get_cursor(con)
    rows, new_cursor = fetch_events(token, cursor)
    new = upsert(con, rows)
    con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('cursor',?)", (new_cursor,))
    con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('last_run',?)", (stamp,))
    con.commit()
    print(f"[{stamp}] 이벤트 수거 {len(rows)} · 신규 {new}")



def paths_report(days: int = 7):
    """원문을 **어느 경로로** 받았나 + 폴백의 예의 간격이 실제로 얼마나 물었나.

    「DART 웹 2초 간격이 필요한가」를 감이 아니라 숫자로 정하려고 붙인 계기(260810)를 읽는다.
    답은 폴백 **비율**에서 갈린다 —
      · 드물다  → 2초는 아무 비용도 아니다. 건드릴 이유가 없다
      · 잦다    → 간격이 아니라 **주 경로(document.xml)가 자주 실패한다**는 뜻이다.
                  낮출 게 아니라 주 경로를 고쳐야 한다
    어느 쪽이든 「2초를 낮춘다」가 답이 되는 경우는 거의 없다. 그걸 확인하는 표다.
    """
    if not using_pg():
        raise SystemExit("--paths 는 Postgres 백엔드에서만 (DATABASE_URL 필요)")
    from open_proxy_mcp.dart.client import _WEB_INTERVAL_RANGE as _WEB_RANGE
    con = _pg_conn()
    have = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'tool_call_events'").fetchall()}
    missing = {"fetch_viewer", "fetch_kind", "web_wait_ms"} - have
    if missing:
        con.close()
        raise SystemExit(
            f"계기 컬럼이 아직 없다: {', '.join(sorted(missing))}\n"
            "  컬럼은 운영 머신의 usage 워커가 붙을 때 생긴다 — 배포 후 첫 요청이 지나면 만들어진다.")
    where = f"ts_ns > (extract(epoch from now()) - {days} * 86400) * 1e9"
    api, viewer, kind, wait_ms, reqs, fb_reqs = con.execute(
        "SELECT coalesce(sum(doc_misses),0), coalesce(sum(fetch_viewer),0), "
        "coalesce(sum(fetch_kind),0), coalesce(sum(web_wait_ms),0), count(*), "
        "count(*) FILTER (WHERE coalesce(fetch_viewer,0) + coalesce(fetch_kind,0) > 0) "
        f"FROM tool_call_events WHERE {where}").fetchone()
    total = api + viewer + kind
    print(f"\n=== 원문 경로 (최근 {days}일 · 요청 {reqs:,}건) ===")
    if not total:
        print("  원문을 받은 요청이 없다 — 기간을 늘리거나 계기 배포 시점을 확인할 것")
        con.close()
        return
    # 라벨에 숫자를 박지 않는다 — 간격이 바뀌면 표가 조용히 거짓말을 한다(260810 통일).
    for label, n in (("주 경로 document.xml", api), ("viewer 폴백 (DART 웹)", viewer),
                     ("KIND 폴백", kind)):
        print(f"  {label:<26} {n:>7,}건   {100 * n / total:>5.1f}%")
    print(f"\n  폴백을 탄 요청   {fb_reqs:,} / {reqs:,}  ({100 * fb_reqs / max(1, reqs):.2f}%)")
    lo, hi = _WEB_RANGE
    print(f"  간격({lo:g}~{hi:g}초 랜덤·시계 공유) 때문에 잔 시간 총 {wait_ms / 1000:,.1f}초"
          + (f" · 폴백 요청당 평균 {wait_ms / fb_reqs / 1000:.1f}초" if fb_reqs else ""))

    rows = con.execute(
        "SELECT tool, count(*), coalesce(sum(fetch_viewer),0), coalesce(sum(fetch_kind),0), "
        "coalesce(sum(web_wait_ms),0) FROM tool_call_events "
        f"WHERE {where} AND coalesce(fetch_viewer,0) + coalesce(fetch_kind,0) > 0 "
        "GROUP BY 1 ORDER BY 5 DESC LIMIT 10").fetchall()
    if rows:
        print("\n  폴백을 가장 많이 타는 tool")
        for tool, n, v, k, w in rows:
            print(f"    {str(tool):<26} 요청 {n:>5,}  viewer {v:>5,}  kind {k:>4,}  {w / 1000:>7.1f}초")
    con.close()


def corp_report(top: int = 15, days: int | None = None):
    """어느 기업이 많이 조회되나 — **사용자와 떼어낸** 집계(`corp_daily`)에서 읽는다.

    260810 이전에는 이벤트 행에 `corp_codes` 가 `key_hash`·`ts_ns` 와 나란히 있었다.
    셋이 붙으면 「이 사용자가 언제 어느 기업을 조사했는지」가 되고, 그건 조사 이력이다.
    원하는 답은 누가 봤는지를 몰라도 나오므로 쓰는 시점에 사용자를 뗐다.
    그래서 여기서는 **「몇 명이 봤나」를 낼 수 없다** — 의도한 한계다.
    """
    if not using_pg():
        raise SystemExit("--corps 는 Postgres 백엔드에서만 (DATABASE_URL 필요)")
    con = _pg_conn()
    where = f"WHERE day > current_date - {int(days)}" if days else ""
    rows = con.execute(
        f"SELECT corp_code, sum(requests) n FROM corp_daily {where} "
        f"GROUP BY 1 ORDER BY 2 DESC LIMIT {int(top)}").fetchall()
    total, corps, lo, hi = con.execute(
        f"SELECT coalesce(sum(requests),0), count(DISTINCT corp_code), min(day), max(day) "
        f"FROM corp_daily {where}").fetchone()
    con.close()
    if not total:
        print("집계 없음 — corp_daily 가 비어 있다.")
        return
    span = f"{lo} ~ {hi}" if lo else ""
    print(f"\n=== 조회된 기업 (총 {total:,}건 · {corps:,}개사 · {span}) ===")
    names = {}
    try:                                    # 코드→이름은 있으면 붙이고, 없으면 코드만 낸다
        from open_proxy_mcp.dart.client import _MASTER_DB_PATH
        import sqlite3 as _s
        if _MASTER_DB_PATH.exists():
            con2 = _s.connect(_MASTER_DB_PATH)
            names = dict(con2.execute("SELECT corp_code, corp_name FROM corp_codes").fetchall())
            con2.close()
    except Exception:
        pass
    for code, n in rows:
        print(f"  {names.get(code, code):<24} {n:>6,}  {100 * n / total:>5.1f}%")
    print("  (사용자와 분리된 집계 — 「몇 명이 봤나」는 의도적으로 낼 수 없다)")


def main():
    args = sys.argv[1:]
    # 읽기 명령 — 백엔드(Postgres/sqlite) 자동 선택
    if "--report" in args:
        report(fetch_rows())
        return
    if "--stats" in args:
        stats(fetch_rows())
        return
    if "--corps" in args:
        i = args.index("--corps")
        days = int(args[i + 1]) if i + 1 < len(args) and args[i + 1].isdigit() else None
        corp_report(days=days)
        return
    if "--paths" in args:
        i = args.index("--paths")
        days = int(args[i + 1]) if i + 1 < len(args) and args[i + 1].isdigit() else 7
        paths_report(days)
        return
    if "--export" in args:
        i = args.index("--export")
        outdir = args[i + 1] if i + 1 < len(args) else str(ROOT / "data" / "usage" / "export")
        export(fetch_rows(), outdir)
        return
    if "--migrate-local" in args:
        migrate_local_to_pg()
        return

    # 수집 명령 — legacy 로그 API/머신 pull은 로컬 sqlite 사용
    con = db()
    if "--pull" in args:
        new = pull_from_fly(con)
        con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('last_run',?)",
                    (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),))
        con.commit()
        print(f"합산 완료 · 신규 {new}")
        report(con.execute("SELECT ts_ns, key_hash FROM events ORDER BY ts_ns").fetchall())
    elif "--backfill" in args:
        con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('cursor',?)",
                    (str(time.time_ns() - RETENTION_MARGIN_NS),))
        con.commit()
        print("cursor를 6일 전으로 되돌림 — 다음 수집이 보존 window 전체를 재수집합니다.")
        collect(con)
        report(con.execute("SELECT ts_ns, key_hash FROM events ORDER BY ts_ns").fetchall())
    else:
        collect(con)
        report(con.execute("SELECT ts_ns, key_hash FROM events ORDER BY ts_ns").fetchall())


if __name__ == "__main__":
    main()
