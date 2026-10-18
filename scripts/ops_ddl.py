#!/usr/bin/env python3
"""운영 계측 표 3개 스키마 — 사용 기록(`ops_tool_calls`·`ops_corp_daily`)과 KRX 호출 장부(`ops_krx_calls`).
**서버는 이 DDL 을 실행하지 않는다 — 사람이 배포 전에 한 번 적용한다.**

261018 신설(Supabase 재설계 4단계 T7·T8). 종전엔 서버가 스스로 표를 맞췄다:
  · `usage._pg_connect` — 기록 워커가 접속할 때마다 CREATE/ALTER 22문장. `ALTER TABLE` 은 칸이 **이미 있어도**
    표 전체 잠금(ACCESS EXCLUSIVE)을 먼저 잡고, 그 잠금은 22문장 끝의 COMMIT 까지 간다. 배포·재시작마다 두 머신이 한 번씩.
  · `krx_meter._flush_locked` — 20콜마다 새 접속 + CREATE TABLE.
  둘 다 **스키마 생성 권한이 없는 역할에서는 표가 이미 있어도 거절된다**(`must be owner` / `permission denied for schema`).
  서버 DB 계정을 최소 권한으로 바꾸는 순간 사용 기록은 2초마다 재접속하며 한 행도 못 쓰게 됐을 것이다.
  이제 서버는 INSERT 만 한다(KRX 장부만 표가 **없을 때** 한 번 만든다 — 새 DB·로컬 개발 DB 용, `fx._db_put` 과 같은 꼴).

    python3 scripts/ops_ddl.py            # SQL 을 화면에 낸다. 아무것도 실행하지 않는다 — SQL 편집기에 붙여 넣을 수 있다
    python3 scripts/ops_ddl.py --no-tx    # BEGIN/COMMIT 없이 본문만 — 자체적으로 트랜잭션을 거는 도구(마이그레이션 적용기)용
    python3 scripts/ops_ddl.py --apply    # DATABASE_URL 의 DB 에 한 트랜잭션으로 적용한다 (실패하면 전부 되돌린다)

■ 칸 목록의 정본은 `open_proxy_mcp/usage.py` 의 `_EVENT_COLUMNS` 다. 칸을 더할 때는 둘을 함께 고치고 이 스크립트를 **배포 전에** 적용한다.
  어긋나면 시험(`tests/test_ops_ddl.py`)이 걸린다. 적용 전에 배포하면 기록 워커가 묶음을 버리지 않고 들고 재시도한다
  (큐 상한까지 — 그 뒤로 들어오는 것만 버린다). 적용하는 순간 밀린 것이 한 번에 들어간다.
■ 전부 **멱등**이다. `ops_tool_calls` 는 어느 옛 모양에서 와도 지금 모양이 된다 — 처음 네 칸으로 표를 만들고, 나머지는 `ADD COLUMN IF NOT EXISTS`
  를 **한 문장**에 모았다(종전 서버의 18문장 = 18번의 잠금 → 1번).
■ 잠금 — `lock_timeout` 5초. `ALTER TABLE` 이 오래 읽는 질의 뒤에서 표 전체 잠금을 기다리는 동안에는 그 뒤에 오는 기록·조회가 **전부**
  그 뒤에 줄을 선다. 기다리느니 실패하고 끝난다 — 실패하면 다시 돌리면 된다.
■ 접근 제어는 운영 그대로(261018 운영 실측): RLS 켜짐·정책 없음, `opm_ro` 는 SELECT 권한만, `anon`·`authenticated` 는 권한 없음.
  이 프로젝트의 기본 권한 설정(default privileges)이 새 표에 두 역할 SELECT 를 자동으로 주므로 새 DB 에서는 명시적으로 거둔다.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: 트랜잭션 본문 — BEGIN/COMMIT 은 `render()` 가 붙인다(`--apply` 는 psycopg 트랜잭션을 쓴다).
STATEMENTS = """\
SET LOCAL lock_timeout = '5s';

-- 사용 기록: 도구 호출 1건 = 1행. 칸 정본은 usage._EVENT_COLUMNS.
CREATE TABLE IF NOT EXISTS ops_tool_calls (
  event_id text PRIMARY KEY,        -- "{ts_ns}-{머신}-{순번}" — 다시 써도 중복이 걸러지는 열쇠
  ts_ns    bigint NOT NULL,
  key_hash text   NOT NULL,         -- SHA-256(DART 키). 평문은 보관하지 않는다
  status   int
);
ALTER TABLE ops_tool_calls
  ADD COLUMN IF NOT EXISTS tool           text,
  ADD COLUMN IF NOT EXISTS latency_ms     int,
  ADD COLUMN IF NOT EXISTS is_error       boolean,
  ADD COLUMN IF NOT EXISTS error_kind     text,
  ADD COLUMN IF NOT EXISTS response_bytes int,
  ADD COLUMN IF NOT EXISTS doc_mem_hits   int,
  ADD COLUMN IF NOT EXISTS doc_disk_hits  int,
  ADD COLUMN IF NOT EXISTS doc_misses     int,
  ADD COLUMN IF NOT EXISTS corp_codes     text,
  ADD COLUMN IF NOT EXISTS fetch_viewer   int,
  ADD COLUMN IF NOT EXISTS fetch_kind     int,
  ADD COLUMN IF NOT EXISTS web_wait_ms    int,
  ADD COLUMN IF NOT EXISTS weak_kinds     text,
  ADD COLUMN IF NOT EXISTS degraded       text,
  ADD COLUMN IF NOT EXISTS inflight       int,
  ADD COLUMN IF NOT EXISTS cpu_ms         int,
  ADD COLUMN IF NOT EXISTS lag_ms         int,
  ADD COLUMN IF NOT EXISTS scope          text,   -- 261018 T8: 호출 인자 scope (정규화한 값만 — usage._call_dims)
  ADD COLUMN IF NOT EXISTS as_of          date,   -- 261018 T8: 호출 인자 as_of (날짜로 읽히는 것만)
  DROP COLUMN IF EXISTS doc_cache_hit;            -- 260817 폐기 — 값이 한 번도 안 들어온 칸
CREATE INDEX IF NOT EXISTS idx_events_hash ON ops_tool_calls (key_hash);
CREATE INDEX IF NOT EXISTS idx_events_ts   ON ops_tool_calls (ts_ns);
CREATE INDEX IF NOT EXISTS idx_events_corp ON ops_tool_calls (corp_codes);   -- 운영에 손으로 만들어져 있던 것을 정본에 올림(261018)

-- 기업 조회 일별 집계: 사용자(key_hash)를 뗀 장기 계열. 드레인이 이벤트를 가져가도 남는다.
CREATE TABLE IF NOT EXISTS ops_corp_daily (
  log_dd    date NOT NULL,
  corp_code text NOT NULL,
  requests  int  NOT NULL DEFAULT 0,
  PRIMARY KEY (log_dd, corp_code)
);

-- KRX Open API 호출 장부: 키당 일 10,000 한도 대비(KST 자정 리셋). 머신·PC 별로 더한다.
CREATE TABLE IF NOT EXISTS ops_krx_calls (
  log_dd  date NOT NULL,
  machine text NOT NULL,
  calls   int  NOT NULL DEFAULT 0,
  PRIMARY KEY (log_dd, machine)
);

ALTER TABLE ops_tool_calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops_corp_daily ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops_krx_calls  ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON ops_tool_calls, ops_corp_daily, ops_krx_calls FROM anon, authenticated;
GRANT SELECT ON ops_tool_calls, ops_corp_daily, ops_krx_calls TO opm_ro;
"""


def render() -> str:
    """사람이 SQL 편집기에 붙여 넣을 전체 스크립트."""
    return "BEGIN;\n\n" + STATEMENTS + "\nCOMMIT;\n"


def apply(url: str) -> None:
    import psycopg
    with psycopg.connect(url, connect_timeout=20) as con:      # 한 트랜잭션 — 중간에 실패하면 전부 되돌아간다
        con.execute(STATEMENTS)
        con.commit()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="DATABASE_URL 의 DB 에 적용 (없으면 SQL 만 출력)")
    ap.add_argument("--no-tx", action="store_true", help="출력할 때 BEGIN/COMMIT 을 붙이지 않는다")
    args = ap.parse_args()
    if not args.apply:
        sys.stdout.write(STATEMENTS if args.no_tx else render())
        return 0
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL 이 없습니다 — 적용하지 않았습니다.", file=sys.stderr)
        return 2
    apply(url)
    print("운영 계측 표 스키마 적용 완료 (멱등 — 다시 실행해도 같은 결과).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
