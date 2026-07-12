#!/usr/bin/env python3
"""OPM 사용 통계 도구 — 요청별 이벤트를 누적하고 일/주 통계를 뽑는다.

저장 백엔드(통계 읽기): **DATABASE_URL 있으면 Postgres(Supabase)**, 없으면 로컬 sqlite.
앱(open_proxy_mcp/usage.py)이 요청 시점에 직접 기록한 events를 그대로 조회한다 → 무손실·합산 불필요.

키는 **SHA-256 해시로만** 저장(평문 DART 키 미보관). 시각 버킷(일/주)은 **KST(UTC+9)** 기준.

사용(읽기 — 백엔드 자동):
  python3 scripts/usage_tracker.py --stats        # 일/주/사용자/세션 상세 통계
  python3 scripts/usage_tracker.py --export DIR    # daily.csv·weekly.csv·users.csv·summary.json
  python3 scripts/usage_tracker.py --report       # 요약만
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


def fetch_error_kinds():
    """{error_kind: count} — is_error=true 인 이벤트의 예외 분류 집계(외부 사용자만).
    error_kind 컬럼 자체가 없는 구스키마/구서버면 {} (컬럼 생기기 전 구간은 집계 생략).
    컬럼이 있으면: 태그(timeout/upstream/crash) + "untagged"(배포後 분류실패=커버리지갭) +
    NULL→"unknown"(배포前, 소급 분류 불가)."""
    counts = defaultdict(int)
    if using_pg():
        con = _pg_conn()
        try:
            try:
                rows = con.execute(
                    "SELECT key_hash, error_kind FROM tool_call_events WHERE is_error=true"
                ).fetchall()
            except Exception:  # error_kind 미생성 구서버
                con.rollback()
                return {}
        finally:
            con.close()
    else:
        try:
            rows = db().execute(
                "SELECT key_hash, error_kind FROM events WHERE is_error=1"
            ).fetchall()
        except Exception:
            return {}
    for h, kind in rows:
        if h in SELF_HASHES:
            continue
        counts[kind or "unknown"] += 1
    return dict(counts)


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
    """[(tool, requests, unique_users, errors, err_known)] 요청수 내림차순 + 평균 latency(ms).
    errors=is_error=True 건수, err_known=is_error가 기록된 건수(구버전 행은 NULL)."""
    by_tool = defaultdict(lambda: [0, set(), 0, 0])
    lat = []
    for tool, h, latency, is_err in tl_rows:
        if h in SELF_HASHES:
            continue
        if latency is not None:
            lat.append(latency)
        if tool:
            rec = by_tool[tool]
            rec[0] += 1
            rec[1].add(h)
            if is_err is not None:
                rec[3] += 1
                if is_err:
                    rec[2] += 1
    ranked = sorted(((t, n, len(u), e, k) for t, (n, u, e, k) in by_tool.items()),
                    key=lambda x: x[1], reverse=True)
    avg_lat = round(sum(lat) / len(lat)) if lat else None
    return ranked, avg_lat


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

    # 오류종류: is_error=true 를 예외 분류로 분해 (배포 이후 이벤트만 태그됨 — 이전은 unknown)
    ekinds = fetch_error_kinds()
    if ekinds:
        _KIND_LABEL = {"crash": "코드버그", "timeout": "시간초과",
                       "upstream": "DART/KIND장애",
                       "untagged": "분류실패(커버리지갭)", "unknown": "미분류(배포前)"}
        total_err = sum(ekinds.values())
        parts = ", ".join(
            f"{_KIND_LABEL.get(k, k)} {c}({c/total_err*100:.0f}%)"
            for k, c in sorted(ekinds.items(), key=lambda kv: -kv[1]))
        print(f"[오류종류] 총 {total_err}건 — {parts}")

    print("\n[사용자 Top 15]  요청  활성일  기간(일)  세션  총사용(분)   최초 ~ 최종")
    for h, v in list(users.items())[:15]:
        print(f"  {h[:10]}  {v['requests']:>5} {v['active_days']:>6} {v['span_days']:>8} "
              f"{v['sessions']:>5} {v['total_minutes']:>9}   {v['first']} ~ {v['last']}")

    ranked, avg_lat = tool_stats(fetch_tool_latency())
    if avg_lat is not None:
        print(f"\n[성능] 평균 응답 {avg_lat} ms")
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
    ranked, avg_lat = tool_stats(fetch_tool_latency())
    with open(d / "tools.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["tool", "requests", "unique_users", "errors", "err_known"])
        for t, n, u, e, k in ranked: w.writerow([t, n, u, e, k])
    summary = {
        "unique_users_external": len({h for _, h in rows}),
        "total_requests_external": len(rows),
        "returning_users_2day": sum(1 for v in users.values() if v["active_days"] >= 2),
        "avg_minutes_per_user": round(sum(v["total_minutes"] for v in users.values()) / max(len(users), 1), 1),
        "avg_latency_ms": avg_lat,
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


def main():
    args = sys.argv[1:]
    # 읽기 명령 — 백엔드(Postgres/sqlite) 자동 선택
    if "--report" in args:
        report(fetch_rows())
        return
    if "--stats" in args:
        stats(fetch_rows())
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
