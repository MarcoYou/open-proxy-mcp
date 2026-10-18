#!/usr/bin/env python3
"""krx_index_members 스키마 — 지수 구성종목 원장 표·접근 제어. **서버는 DDL 을 실행하지 않는다 — 사람이 배포 전에 한 번 적용한다.**

261018 신설. `scripts/krx_daily_ddl.py` 와 같은 모양(스크립트 안의 `CREATE … IF NOT EXISTS` 상수, 전부 멱등).

    python3 scripts/krx_index_members_ddl.py            # SQL 을 화면에 낸다. 아무것도 실행하지 않는다
    python3 scripts/krx_index_members_ddl.py --no-tx    # BEGIN/COMMIT 없이 본문만
    python3 scripts/krx_index_members_ddl.py --apply    # DATABASE_URL 의 DB 에 한 트랜잭션으로 적용한다

■ 표 `krx_index_members` — 지수(코스피200·코스닥150) 구성종목. 적재는 `scripts/krx_index_members.py`, 읽기는 `open_proxy_mcp/index_members.py`.
    index_code  유니버스 문법 이름(kospi200 · kosdaq150)
    snap_dd     이 구성이 **처음 확인된** 거래일(YYYYMMDD). 구성이 바뀐 날에만 새 묶음이 생긴다
    ticker      단축코드
    checked_dd  이 구성이 **마지막으로 확인된** 거래일. 구성이 그대로면 적재가 이 칸만 올린다
  기본키 (index_code, snap_dd, ticker) — 읽는 쪽의 「가장 늦은 묶음」 질의가 이 색인 하나로 끝난다. 크기: 묶음 하나가
  약 350행(코스피200 + 코스닥150)이고 구성이 바뀔 때만 늘어난다(정기변경 연 2회 + 수시변경).
■ 접근 제어 — 다른 표와 같은 꼴(RLS 켜기 + SELECT 정책 `USING (true)`). 서버 역할은 RLS 를 우회하고, 읽기 전용 역할(`opm_ro`)은
    이 정책 + 명시적 `GRANT SELECT` 로 읽는다. `anon`·`authenticated` 에는 권한을 주지 않는다(기본 권한 설정이 새 표에 자동으로
    주는 SELECT 를 명시적으로 거둔다).
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
CREATE TABLE IF NOT EXISTS krx_index_members (
  index_code text NOT NULL,   -- kospi200 / kosdaq150
  snap_dd    text NOT NULL,   -- YYYYMMDD — 이 구성이 처음 확인된 거래일
  ticker     text NOT NULL,   -- 단축코드
  checked_dd text NOT NULL,   -- YYYYMMDD — 이 구성이 마지막으로 확인된 거래일
  PRIMARY KEY (index_code, snap_dd, ticker),
  CONSTRAINT krx_index_members_checked_after_snap CHECK (checked_dd >= snap_dd)
);

ALTER TABLE krx_index_members ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS krx_index_members_read ON krx_index_members;
CREATE POLICY krx_index_members_read ON krx_index_members FOR SELECT USING (true);

REVOKE ALL ON krx_index_members FROM anon, authenticated;
GRANT SELECT ON krx_index_members TO opm_ro;
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
    print("krx_index_members 스키마 적용 완료 (멱등 — 다시 실행해도 같은 결과).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
