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


# ── 드레인된 과거 주 합류 (260817 · 260904 parquet) ────────────────────────────
#: `events_drain.py` 가 완결 주를 parquet 로 내보내고 DB 에서 지운다. **DB 만 읽으면 지운 만큼
#: 과거가 통째로 사라진다** — 260817 실측: 7주를 드레인하자 362,994행이 5,511행이 되면서
#: 오래 쓴 사용자가 전부 「신규」로 재라벨됐다. 백업은 있는데 되읽을 길이 없었다.
#: 드레인은 앞으로도 계속 돌아야 하므로(무료티어 압박 + 사용자-기업 연결의 수명 상한)
#: 이 합류 지점이 없으면 통계는 영구히 「진행 중인 주」만 보게 된다.
#:
#: **경로는 `events_drain` 의 것을 그대로 쓴다** — 여기 사본을 두면 이중장부가 되고,
#: 한쪽만 바뀌면 조용히 다른 폴더를 보게 된다(260817 SELF_HASHES 사본 사고와 같은 모양).
try:
    from events_drain import OUT_DIR as DRAINED_DIR
except ImportError:                      # `import scripts.usage_tracker` 로 들어온 경우
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from events_drain import OUT_DIR as DRAINED_DIR

#: `usage/events/*.parquet` 만 읽는다(260904 CSV→parquet — 주당 30MB 가 1.5MB). 한 폴더 위
#: `usage/` 에 있는 `opm_events_all_*.parquet`(통합 재생성본)·`*_purged.csv`(일부러 걷어낸 테스트
#: 오염)·`user_registry.csv` 는 글로브 밖이다. **통합본을 events/ 안에 두면 전 기간이 두 번
#: 세어진다** — 통합본은 산출물이고 정본은 주별 파일이다.
DRAINED_GLOB = "*.parquet"
#: parquet 은 PG 스키마 타입을 그대로 옮긴다(events_drain._PG2DUCK). 그래도 문자열로 들어온
#: 정수·불리언이 있으면 여기서 고친다 — 하류 계산이 조용히 갈리는 쪽보다 낫다.
_INT_COLS = {"ts_ns", "status", "latency_ms", "response_bytes", "doc_mem_hits",
             "doc_disk_hits", "doc_misses", "fetch_viewer", "fetch_kind", "web_wait_ms",
             "inflight", "cpu_ms", "lag_ms"}
_BOOL_COLS = {"is_error"}

#: 각 조회 함수의 SELECT 순서. **쿼리와 여기가 어긋나면 값이 조용히 다른 자리로 들어간다**
#: (260704 mkt_fund_hist 사고와 같은 실패 모드) — 테스트가 둘을 대조한다.
_TL_COLS = ("tool", "key_hash", "latency_ms", "is_error")
_ERR_COLS = ("ts_ns", "key_hash", "is_error")
_OUT_COLS = ("key_hash", "tool", "is_error", "error_kind", "weak_kinds")
#: 조용한 대체 — 「원래 답을 못 줘서 다른 것으로 답한」 경우. 오류율로는 안 보인다.
_DEG_COLS = ("ts_ns", "key_hash", "tool", "degraded")
_PATH_COLS = ("ts_ns", "tool", "doc_misses", "fetch_viewer", "fetch_kind", "web_wait_ms")
#: 느린 이유 — 「스스로 느린 것」과 「줄에 서 있던 것」을 가른다(260824 신설).
_SLOW_COLS = ("ts_ns", "tool", "latency_ms", "inflight", "cpu_ms", "lag_ms")


def event_table() -> str:
    """이벤트 표 이름 — **여기가 정본이다.** PG 는 `ops_tool_calls`(260706 rename),
    sqlite 는 `events`. 260828 사고: `startup_metrics.fetch_full` 이 옛 이름
    `tool_call_events` 를 사본으로 들고 있다가 rename 이후 회차에서 UndefinedTable 로
    죽었다 — 이름을 두 곳에 두면 한쪽만 고쳐진다(SELF_HASHES 와 같은 이중장부).
    """
    return "ops_tool_calls" if using_pg() else "events"


def _db_event_ids() -> set:
    """DB 에 지금 있는 event_id. 드레인은 내보낸 뒤 지우므로 원래 안 겹치지만,
    중단·재실행이 있었다면 겹친다 — **겹침을 가정하지 않는 쪽이 위험하다**(이중 계상)."""
    tbl = "ops_tool_calls" if using_pg() else "events"
    try:
        if using_pg():
            con = _pg_conn()
            try:
                return {r[0] for r in con.execute(f"SELECT event_id FROM {tbl}").fetchall()}
            finally:
                con.close()
        return {r[0] for r in db().execute(f"SELECT event_id FROM {tbl}").fetchall()}
    except Exception as e:
        # 빈 집합을 조용히 돌려주면 dry-run 드레인 구간이 **두 번 세어진다**. 계속 가되 알린다.
        print(f"⚠️  DB event_id 를 못 읽었다({type(e).__name__}) — 드레인 백업과의 중복 제거 없이 합류한다.",
              file=sys.stderr)
        return set()


_drained_cache: dict | None = None


def drained_columns() -> dict:
    """드레인 parquet → {컬럼명: [값, ...]} (열 지향 — 투영이 공짜고 메모리가 싸다).

    DuckDB `read_parquet(..., union_by_name=true)` 로 읽는다 — 주마다 컬럼 수가 다르다
    (260817 이전 23개 · 이후 21개처럼). **이름으로 맞춰야** 늦게 생긴 열이 옛 주에서 NULL 로
    정렬되고, 위치로 맞추면 행이 밀린다(CSV 시절 `weak_kinds` 에서 실제로 깨졌다).
    문자열이 반복되는 열(key_hash 346개·tool 30여 개)은 **interning** 한다 —
    안 하면 48만 행이 수백 MB 가 된다.
    폴더·파일이 없으면 **조용히 넘어가지 않는다.** 그 침묵이 260817 사고의 원인이었다.
    """
    global _drained_cache
    if _drained_cache is not None:
        return _drained_cache

    files = sorted(DRAINED_DIR.glob(DRAINED_GLOB)) if DRAINED_DIR.is_dir() else []
    if not files:
        print(f"⚠️  드레인 백업이 없다({DRAINED_DIR}/{DRAINED_GLOB}) — DB 구간만 집계한다.\n"
              f"    OPM_STORAGE_REPO 가 맞는지 확인. 과거 지표는 실제보다 작게 나온다.",
              file=sys.stderr)
        _drained_cache = {}
        return _drained_cache
    import duckdb  # 서버 런타임 의존성 아님 — 통계 경로 전용(dev 그룹)

    have = _db_event_ids()
    paths = "[" + ", ".join("'" + f.as_posix().replace("'", "''") + "'" for f in files) + "]"
    duck = duckdb.connect()
    try:
        src = f"read_parquet({paths}, union_by_name=true)"
        # TIMESTAMPTZ(ts_kst) 를 파이썬 datetime 으로 바꾸려면 duckdb 가 pytz 를 요구한다 —
        # 통계는 전부 ts_ns(정수) 로 계산하므로 시각 열은 문자열로 받는다(의존성 하나 덜).
        sel = ", ".join(
            f'CAST("{n}" AS VARCHAR) AS "{n}"' if "TIMESTAMP" in t.upper() or "DATE" in t.upper() else f'"{n}"'
            for n, t, *_ in duck.execute(f"DESCRIBE SELECT * FROM {src}").fetchall())
        cur = duck.execute(f"SELECT {sel} FROM {src} ORDER BY ts_ns")
        names = [d[0] for d in cur.description]
        if "event_id" not in names:
            raise SystemExit(f"드레인 parquet 에 event_id 열이 없다 — 중복 제거를 할 수 없어 멈춘다: {names}")
        eid = names.index("event_id")
        cols: dict[str, list] = {k: [] for k in names}
        pool: dict = {}
        n = dup = 0
        while True:
            chunk = cur.fetchmany(50_000)
            if not chunk:
                break
            for r in chunk:
                if r[eid] in have:
                    dup += 1
                    continue
                for k, v in zip(names, r):
                    if v is None or v == "":
                        cols[k].append(None)
                    elif isinstance(v, str):
                        if k in _INT_COLS:
                            cols[k].append(int(v))
                        elif k in _BOOL_COLS:
                            cols[k].append(v in ("True", "true", "t", "1"))
                        else:
                            cols[k].append(pool.setdefault(v, v))    # interning
                    else:
                        cols[k].append(v)
                n += 1
    finally:
        duck.close()
    # 열 길이가 하나라도 다르면 그 뒤 모든 투영이 밀린다 — 조용히 틀리느니 여기서 멈춘다.
    bad = {k: len(v) for k, v in cols.items() if len(v) != n}
    if bad:
        raise SystemExit(f"드레인 백업의 열 길이가 어긋난다(기대 {n:,}): {bad}")
    print(f"  · 드레인 백업 {n:,}건 합류({len(files)}주 parquet)"
          f"{f' (DB 와 겹쳐 제외 {dup:,})' if dup else ''}", file=sys.stderr)
    _drained_cache = cols
    return cols


#: **tool 개명 대조표.** 이름을 바꾸면 같은 도구의 통계가 두 계열로 갈라진다 — 옛 이름 아래
#: 쌓인 호출이 어느 날 0 이 되고 새 이름이 0 에서 시작하니, 그래프만 보면 「죽었다 태어났다」로
#: 읽힌다(실측: `valuation` 586건). 여기서 옛 이름을 새 이름으로 접어 한 계열로 유지한다.
#: 지우지 말 것 — 지우는 순간 과거가 다시 갈라진다.
TOOL_ALIASES = {
    "valuation": "price_multiple_data",   # 260824 개명 (배수 ↔ 거래·규모 분리)
    "dividend": "dividend_disclosure",    # 260902 개명 (공시 원문 ↔ DB 시계열 분리)
    "dividend_history_data": "dividend_data",  # 260903 통합 (screener 와 합쳐 결정공시 기반으로 교체)
    "dividend_screener": "dividend_data",      # 260903 통합 — quarterly_only 판정이 틀린 답을 내고 있었다
}


def canon_tool(tool):
    """옛 tool 이름 → 현재 이름. 그 외는 그대로."""
    return TOOL_ALIASES.get(tool, tool)


def merge_drained(rows, cols: tuple):
    """DB 행 + 드레인 행. `cols` 는 **DB 쿼리가 고른 컬럼명을 그 순서대로** 준 것이다.

    CSV 에 없는 컬럼(옛 백업에 아직 안 생겼던 열)은 None 으로 채운다 — 컬럼이 늘어난
    시점보다 오래된 주는 그 값을 가진 적이 없으므로, 0 이 아니라 「없음」이 맞다.
    """
    d = drained_columns()
    if not d:
        out = list(rows)
    else:
        n = len(next(iter(d.values())))
        blank = [None] * n
        src = [d.get(c, blank) for c in cols]
        out = list(rows) + [tuple(col[i] for col in src) for i in range(n)]
    # 개명 접기 — DB 행과 드레인 행이 모두 여기를 지나므로 **한 곳**이면 충분하다.
    #   두 곳에 두면 한쪽만 고쳐진다(이 레포에서 다섯 번 겪은 형태).
    if "tool" in cols:
        i = cols.index("tool")
        out = [r if canon_tool(r[i]) == r[i] else (*r[:i], canon_tool(r[i]), *r[i + 1:])
               for r in out]
    return out


def fetch_rows():
    """모든 (ts_ns, key_hash) 정렬 반환 (self 포함). 백엔드 자동 선택."""
    if using_pg():
        con = _pg_conn()
        rows = con.execute("SELECT ts_ns, key_hash FROM ops_tool_calls ORDER BY ts_ns").fetchall()
        con.close()
        rows = [(int(t), h) for t, h in rows]
    else:
        rows = list(db().execute("SELECT ts_ns, key_hash FROM events ORDER BY ts_ns"))
    rows = merge_drained(rows, ("ts_ns", "key_hash"))
    rows.sort(key=lambda r: r[0])     # 합류하면 순서가 깨진다. key= 명시(튜플 전체비교 금지)
    return rows


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
    PG(ops_tool_calls)와 sqlite(events)는 테이블명이 달라(260706 PG측 rename) 쿼리 분리."""
    if using_pg():
        sql = "SELECT tool, key_hash, latency_ms, is_error FROM ops_tool_calls"
        old = "SELECT tool, key_hash, latency_ms FROM ops_tool_calls"
        con = _pg_conn()
        try:
            try:
                rows = con.execute(sql).fetchall()
            except Exception:  # is_error 컬럼 미생성(구서버) — 롤백 후 구스키마로
                con.rollback()
                rows = [(*r, None) for r in con.execute(old).fetchall()]
        finally:
            con.close()
        return merge_drained(rows, _TL_COLS)
    sql = "SELECT tool, key_hash, latency_ms, is_error FROM events"
    old = "SELECT tool, key_hash, latency_ms FROM events"
    try:
        return merge_drained(db().execute(sql).fetchall(), _TL_COLS)
    except sqlite3.OperationalError:
        try:
            return merge_drained([(*r, None) for r in db().execute(old).fetchall()], _TL_COLS)
        except sqlite3.OperationalError:
            return merge_drained([], _TL_COLS)


def fetch_degradations():
    """(ts_ns, key_hash, tool, degraded) — 조용한 대체 기록.

    260824 신설. `screener` 유니버스 폴백이 **모든 kospi200 호출에서 100% 발화**하는데도
    아무 데도 안 쌓이던 것이 계기다. 오류율은 그동안 1% 대로 조용했다.
    구스키마(컬럼 없음)면 빈 목록 — 경보가 아니라 「그 전 기간」이라는 뜻이다.
    """
    sql = "SELECT ts_ns, key_hash, tool, degraded FROM {} WHERE degraded IS NOT NULL AND degraded <> ''"
    if using_pg():
        con = _pg_conn()
        try:
            rows = con.execute(sql.format("ops_tool_calls")).fetchall()
        except Exception:
            con.rollback(); rows = []
        finally:
            con.close()
    else:
        try:
            rows = db().execute(sql.format("events")).fetchall()
        except sqlite3.OperationalError:
            rows = []
    return merge_drained(rows, _DEG_COLS)


def degradation_stats(rows):
    """(종류별 건수, tool×종류, 날짜별) — 「언제부터 늘었나」가 핵심 질문이다."""
    kinds, per_tool, per_day, users = defaultdict(int), defaultdict(int), defaultdict(int), defaultdict(set)
    import datetime as _dt
    for ts, h, tool, deg in rows:
        # ★ **드레인 백업에는 이 컬럼이 없다** — `merge_drained` 가 None 으로 채운다
        #   (컬럼이 생기기 전 주는 그 값을 가진 적이 없으므로 0 이 아니라 「없음」이 맞다).
        #   DB 쪽 `WHERE degraded IS NOT NULL` 은 합류분에 안 걸리므로 여기서 다시 거른다.
        #   이걸 빠뜨리면 `str(None)` 이 "None" 이라는 **가짜 범주**가 되어 65,500건으로
        #   집계된다(260824 첫 실행에서 실제로 그랬다 — 새 지표가 첫날부터 조용히 틀렸다).
        if not deg or h in SELF_HASHES or is_protocol(tool):
            continue
        day = _dt.datetime.fromtimestamp(int(ts) / 1e9, KST).strftime("%m-%d") if ts else "?"
        for k in str(deg).split(","):
            k = k.strip()
            if not k or k == "None":
                continue
            kinds[k] += 1
            per_tool[(canon_tool(tool), k)] += 1
            per_day[(day, k)] += 1
            users[k].add(h)
    return kinds, per_tool, per_day, users


def print_degradations():
    """[조용한 대체] — **오류가 아닌 고장**을 낸다.

    260824 계기: `screener` 유니버스 폴백이 「전체시장으로 대체」를 모든 kospi200 호출에서
    발화하는데도 아무 데도 안 쌓였다. 그동안 오류율은 1% 대로 조용했고 경보도 없었다.
    실측으로 그 사용자는 우리가 깨뜨린 2시간 반 뒤부터 밤새 58건을 다시 눌렀다.

    **날짜별을 함께 내는 이유**: 총량보다 「언제부터 늘었나」가 답이다. 대체는 원래 조금씩
    일어난다(사용자가 이상한 기간을 넣는 등) — 배포 직후 튀는 것이 우리가 깨뜨린 신호다.
    """
    rows = fetch_degradations()
    if not rows:
        return          # 기록 전 기간이거나 대체가 없었다 — 경보 아님
    kinds, per_tool, per_day, users = degradation_stats(rows)
    if not kinds:
        return
    total = sum(kinds.values())
    print(f"\n[조용한 대체] 총 {total:,}건 — 오류가 아니라 **다른 답으로 바꿔 답한** 경우")
    for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]):
        print(f"  {k:20s} {n:>6,}건 · 사용자 {len(users[k])}명")
    tops = sorted(per_tool.items(), key=lambda kv: -kv[1])[:6]
    if tops:
        print("  ├ tool×종류: " + " · ".join(f"{t}/{k} {n:,}" for (t, k), n in tops))
    days = sorted({d for d, _ in per_day})[-10:]
    if len(days) > 1:
        line = " ".join(f"{d}:{sum(n for (dd, _), n in per_day.items() if dd == d)}" for d in days)
        print(f"  └ 최근 날짜별: {line}")
        print("     ※ 배포 직후 튀면 우리가 조용히 깨뜨린 것이다 — 오류율로는 안 보인다.")


def fetch_slow(min_ms: int = 10_000):
    """(ts_ns, tool, latency_ms, inflight, cpu_ms) — 느린 호출만.

    260824 신설. 그 전 기간은 두 컬럼이 없어 빈 목록이 나온다 — 경보가 아니라 「그때는
    안 쟀다」는 뜻이다.
    """
    sql = ("SELECT ts_ns, tool, latency_ms, inflight, cpu_ms, lag_ms FROM {} "
           "WHERE latency_ms IS NOT NULL AND latency_ms >= %d" % min_ms)
    if using_pg():
        con = _pg_conn()
        try:
            rows = con.execute(sql.format("ops_tool_calls")).fetchall()
        except Exception:
            con.rollback(); rows = []
        finally:
            con.close()
    else:
        try:
            rows = db().execute(sql.format("events")).fetchall()
        except sqlite3.OperationalError:
            rows = []
    return merge_drained(rows, _SLOW_COLS)


#: CPU 를 「쓰고 있었다」고 볼 경계 — 한 요청이 제 시간의 절반 넘게 코어를 태웠다면
#: 그 시간은 대기가 아니었다. 경계 근처는 어차피 섞이므로 비율만 본다.
_BUSY = 0.5
#: 루프가 「밀렸다」고 볼 경계. 표본기가 0.1초마다 깨므로 그보다 훨씬 커야 신호다.
#: 제 시간의 1/4 을 못 돌았으면 이 요청은 **차례를 기다린** 것이다.
_LAGGED = 0.25


def contention_stats(rows):
    """느린 호출을 네 갈래로 나눈다 — 줄 / 자신이 무겁다 / CPU 못 받음 / 네트워크 대기.

    **셋이 아니라 넷이다.** 260827 까지는 셋이었고 그게 틀렸다. `cpu` 가 낮은 요청을
    전부 「네트워크 대기」로 몰았는데, 그 안에 **CPU 차례를 못 받은 것**이 섞여 있었다.
    실측: `law_lookup` 은 `await` 도 HTTP 도 없는 순수 로컬 조회인데 15.1~16.4초가
    걸렸다(할 일은 3.8초어치 = 코어의 1/4). 기다릴 상대가 없는데 늦었는데도
    「대기(네트워크)」로 적혔다. 지표가 오답을 내고 있었다.

      cpu 높음 & lag 낮음              → 이 호출 자신이 무겁다 (원인)
      cpu 높음 & lag 높음 & inflight>1 → 줄에 서 있었다 (피해자 — 고칠 곳은 동시성)
      cpu 낮음 & lag 높음              → **CPU 를 못 받았다** (VM 스로틀 — 코드 문제 아님)
      cpu 낮음 & lag 낮음              → 기다렸다 (네트워크·스로틀)

    `lag` 이 없는 행(260827 이전)은 종전 규칙으로 읽되 **「CPU 못 받음」 후보를 네트워크로
    단정하지 않는다** — 「모름」으로 따로 센다. 없던 컬럼을 0 으로 읽으면 그 기간이 통째로
    한 범주에 쏠린다(`degraded` 가 첫날 None 을 65,500건짜리 범주로 만든 그 형태다).
    """
    out = defaultdict(lambda: defaultdict(int))
    seen = 0
    for ts, tool, lat, inflight, cpu, lag in rows:
        if lat is None or cpu is None:
            continue            # 계측 전(260824 이전) — 0 이 아니라 「없음」
        seen += 1
        busy = cpu >= lat * _BUSY
        if lag is None:
            kind = ("줄" if (inflight or 1) > 1 else "자신이 무겁다") if busy else "모름(lag 계측 전)"
        else:
            lagged = lag >= lat * _LAGGED
            if busy:
                kind = "줄" if (lagged and (inflight or 1) > 1) else "자신이 무겁다"
            else:
                kind = "CPU 못 받음" if lagged else "대기(네트워크)"
        out[tool][kind] += 1
    return out, seen


def print_contention(min_ms: int = 10_000):
    """[느린 이유] — 「왜 느렸나」를 tool 별로.

    이게 없던 260824 에 business_details 178초를 진단하느라 종료시각을 겹쳐 역산해야 했다.
    같은 캐시히트 호출이 한가할 땐 0.7초였다 — 178초는 그 호출이 한 일이 아니라 줄이었다.
    """
    rows = fetch_slow(min_ms)
    per_tool, seen = contention_stats(rows)
    if not seen:
        return          # 계측 전 기간 — 경보 아님
    print(f"\n[느린 이유] {min_ms // 1000}초 초과 {seen:,}건 — 「스스로 느린 것」과 "
          f"「줄에 서 있던 것」을 가른다")
    for tool, kinds in sorted(per_tool.items(), key=lambda kv: -sum(kv[1].values()))[:8]:
        tot = sum(kinds.values())
        parts = " · ".join(f"{k} {n:,}({n / tot * 100:.0f}%)"
                           for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]))
        print(f"  {tool:<28} {tot:>5,}건  {parts}")
    print("     ※ 「줄」이 많으면 고칠 곳은 동시성, 「CPU 못 받음」이 많으면 **머신 등급**,")
    print("        「자신이 무겁다」가 많으면 그 tool 의 계산량이다.")


def migrate_local_to_pg():
    """로컬 sqlite events를 Postgres(ops_tool_calls)로 1회 이전(ON CONFLICT dedup). 과거 데이터 시드용."""
    if not using_pg():
        raise SystemExit("DATABASE_URL이 필요합니다 (.env 또는 환경변수).")
    src = db().execute("SELECT event_id, ts_ns, key_hash, status FROM events").fetchall()
    pg = _pg_conn()
    pg.execute("CREATE TABLE IF NOT EXISTS ops_tool_calls(event_id text PRIMARY KEY, "
               "ts_ns bigint NOT NULL, key_hash text NOT NULL, status int)")
    pg.cursor().executemany(
        "INSERT INTO ops_tool_calls(event_id, ts_ns, key_hash, status) VALUES(%s,%s,%s,%s) "
        "ON CONFLICT (event_id) DO NOTHING", src)
    pg.commit()
    total = pg.execute("SELECT COUNT(*) FROM ops_tool_calls").fetchone()[0]
    pg.close()
    print(f"로컬 {len(src)}건 → Postgres 이전 완료 (PG 총 {total}건)")

# 본인(운영자) 키 — 외부 사용자 통계에서 제외. 평문 미보관, SHA-256 해시로만.
# **정본은 `open_proxy_mcp/usage.py`의 SELF_HASHES 하나뿐이다.** 여기에 사본을 두었더니
# 이중장부가 됐다 — 260817에 2번 키를 기록 게이트에만 넣었고, 통계는 이 사본을 읽어
# 1,034건이 그대로 집계됐다(덱 숫자가 안 움직여서야 발견). 키를 늘릴 땐 정본만 고친다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from open_proxy_mcp.usage import SELF_HASHES  # noqa: E402


# ── 인프라 ────────────────────────────────────────────────────────────────
def fly_token() -> str:
    tok = os.environ.get("FLY_API_TOKEN")
    if tok:
        return tok.strip()
    fly = shutil.which("fly") or "fly"   # PATH 에 맡긴다 — 개인 홈 경로를 박지 않는다
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
    fly = shutil.which("fly") or "fly"   # PATH 에 맡긴다 — 개인 홈 경로를 박지 않는다
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
                rows = con.execute("SELECT ts_ns, key_hash, is_error FROM ops_tool_calls").fetchall()
            except Exception:  # is_error 미생성 구서버
                con.rollback()
                rows = []
        finally:
            con.close()
        return merge_drained([(int(t), h, e) for t, h, e in rows], _ERR_COLS)
    try:
        return merge_drained([(int(t), h, e) for t, h, e in
                              db().execute("SELECT ts_ns, key_hash, is_error FROM events")], _ERR_COLS)
    except Exception:
        return merge_drained([], _ERR_COLS)


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
            rows = con.execute(f"SELECT {cols} FROM ops_tool_calls").fetchall()
        except Exception:      # weak_kinds 미생성 구서버 — 그 컬럼만 빼고 재시도
            con.rollback()
            try:
                rows = [(*r, None) for r in con.execute(
                    "SELECT key_hash, tool, is_error, error_kind FROM ops_tool_calls").fetchall()]
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
    rows = merge_drained(rows, _OUT_COLS)
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
    print_degradations()
    print_contention()

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
        "WHERE table_name = 'ops_tool_calls'").fetchall()}
    missing = {"fetch_viewer", "fetch_kind", "web_wait_ms"} - have
    if missing:
        con.close()
        raise SystemExit(
            f"계기 컬럼이 아직 없다: {', '.join(sorted(missing))}\n"
            "  컬럼은 운영 머신의 usage 워커가 붙을 때 생긴다 — 배포 후 첫 요청이 지나면 만들어진다.")
    # 집계를 SQL 이 아니라 파이썬에서 한다 — **드레인된 과거가 DB 에 없기 때문**이다(260817).
    # SQL 로 하면 창 안의 대부분이 조용히 빠지고, 표는 아무 경고 없이 「최근 7일」이라고 말한다.
    db_rows = con.execute(
        "SELECT ts_ns, tool, doc_misses, fetch_viewer, fetch_kind, web_wait_ms "
        "FROM ops_tool_calls").fetchall()
    cut = (time.time() - days * 86400) * 1e9
    rows = [r for r in merge_drained(db_rows, _PATH_COLS) if r[0] and int(r[0]) > cut]
    z = lambda v: int(v or 0)
    api = sum(z(r[2]) for r in rows)
    viewer = sum(z(r[3]) for r in rows)
    kind = sum(z(r[4]) for r in rows)
    wait_ms = sum(z(r[5]) for r in rows)
    reqs = len(rows)
    fb_reqs = sum(1 for r in rows if z(r[3]) + z(r[4]) > 0)
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

    by_tool: dict = defaultdict(lambda: [0, 0, 0, 0])   # [요청, viewer, kind, wait_ms]
    for r in rows:
        if z(r[3]) + z(r[4]) == 0:
            continue
        a = by_tool[r[1]]
        a[0] += 1; a[1] += z(r[3]); a[2] += z(r[4]); a[3] += z(r[5])
    top_tools = sorted(by_tool.items(), key=lambda kv: kv[1][3], reverse=True)[:10]
    if top_tools:
        print("\n  폴백을 가장 많이 타는 tool")
        for tool, (n, v, k, w) in top_tools:
            print(f"    {str(tool):<26} 요청 {n:>5,}  viewer {v:>5,}  kind {k:>4,}  {w / 1000:>7.1f}초")
    con.close()


def corp_report(top: int = 15, days: int | None = None):
    """어느 기업이 많이 조회되나 — **사용자와 떼어낸** 집계(`ops_corp_daily`)에서 읽는다.

    260810 이전에는 이벤트 행에 `corp_codes` 가 `key_hash`·`ts_ns` 와 나란히 있었다.
    셋이 붙으면 「이 사용자가 언제 어느 기업을 조사했는지」가 되고, 그건 조사 이력이다.
    원하는 답은 누가 봤는지를 몰라도 나오므로 쓰는 시점에 사용자를 뗐다.
    그래서 여기서는 **「몇 명이 봤나」를 낼 수 없다** — 의도한 한계다.
    """
    if not using_pg():
        raise SystemExit("--corps 는 Postgres 백엔드에서만 (DATABASE_URL 필요)")
    con = _pg_conn()
    where = f"WHERE log_dd > current_date - {int(days)}" if days else ""
    rows = con.execute(
        f"SELECT corp_code, sum(requests) n FROM ops_corp_daily {where} "
        f"GROUP BY 1 ORDER BY 2 DESC LIMIT {int(top)}").fetchall()
    total, corps, lo, hi = con.execute(
        f"SELECT coalesce(sum(requests),0), count(DISTINCT corp_code), min(log_dd), max(log_dd) "
        f"FROM ops_corp_daily {where}").fetchone()
    con.close()
    if not total:
        print("집계 없음 — ops_corp_daily 가 비어 있다.")
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
