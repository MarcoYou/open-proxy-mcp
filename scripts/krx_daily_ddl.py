#!/usr/bin/env python3
"""krx_daily 스키마 — 표·인덱스·뷰 2개·접근 제어. **서버는 DDL 을 실행하지 않는다 — 사람이 배포 전에 한 번 적용한다.**

261018 신설(Supabase 재설계 3단계). 이 저장소의 DDL 관례(스크립트 안의 `CREATE … IF NOT EXISTS` 상수)를 따르되,
서버 쓰기 경로가 표를 만들지 않으므로 별도 스크립트로 둔다. **전부 멱등**이다(다시 돌려도 같은 결과).

    python3 scripts/krx_daily_ddl.py            # SQL 을 화면에 낸다. 아무것도 실행하지 않는다 — SQL 편집기에 붙여 넣을 수 있다
    python3 scripts/krx_daily_ddl.py --apply    # DATABASE_URL 의 DB 에 한 트랜잭션으로 적용한다 (실패하면 전부 되돌린다)

■ 표 `krx_daily` — 사실값만. 시가총액·등락률은 저장하지 않고 뷰가 계산한다.
    trd_dd 는 `krx_weekly.price_dd` 와 같은 YYYYMMDD 문자열. 기본키 (trd_dd, ticker) + 색인 (ticker, trd_dd).
■ 뷰 `krx_daily_v` — 모든 행 + `mktcap = close × list_shrs` + 같은 종목의 **직전 보관 거래일** 대비 `prev_close`·`chg`·`chg_pct`.
    보관 창의 첫날(과 신규상장일)은 직전 행이 없어 NULL 이다.
■ 뷰 `price_latest` — 종목마다 가장 늦은 `trd_dd` 한 줄. `price_dd`(= trd_dd) 열을 함께 내 `krx_weekly` 를 읽던 쪽이 표 이름만 바꿔 쓸 수 있다.
■ 접근 제어 — 다른 표와 같은 꼴(RLS 켜기 + SELECT 정책 `USING (true)`)이다. 서버 역할(`postgres`)은 RLS 를 우회하고, 읽기 전용
    역할(`opm_ro`)은 이 정책으로 읽는다. 뷰는 `security_invoker` 라 호출자 권한으로 평가된다.
    🔴 `anon`·`authenticated` 에는 **권한을 주지 않는다** — 이 프로젝트의 기본 권한 설정(default privileges)이 새 표·뷰를 만들 때
    두 역할에 SELECT 를 자동으로 주므로, 그것을 명시적으로 거둔다(Data API 를 닫을 예정이므로 노출을 늘리지 않는다).
■ 보관은 쓰는 쪽이 한다: 가장 최근 서로 다른 거래일 7개만 남기고 지운다(`open_proxy_mcp/krx_daily.py`).
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
CREATE TABLE IF NOT EXISTS krx_daily (
  trd_dd    text   NOT NULL,   -- YYYYMMDD (krx_weekly.price_dd 와 같은 형식)
  ticker    text   NOT NULL,   -- 단축코드 (KRX ISU_CD)
  market    text,              -- KS / KQ
  open      bigint,
  high      bigint,
  low       bigint,
  close     bigint,
  volume    bigint,
  value     bigint,            -- 거래대금(원)
  list_shrs bigint,
  PRIMARY KEY (trd_dd, ticker)
);
CREATE INDEX IF NOT EXISTS idx_krx_daily_isu ON krx_daily (ticker, trd_dd);

CREATE OR REPLACE VIEW krx_daily_v WITH (security_invoker = true) AS
SELECT d.trd_dd, d.ticker, d.market, d.open, d.high, d.low, d.close, d.volume, d.value, d.list_shrs,
       d.close * d.list_shrs AS mktcap,
       lag(d.close) OVER w AS prev_close,
       d.close - lag(d.close) OVER w AS chg,
       round((d.close - lag(d.close) OVER w)::numeric * 100 / NULLIF(lag(d.close) OVER w, 0), 2) AS chg_pct
FROM krx_daily d
WINDOW w AS (PARTITION BY d.ticker ORDER BY d.trd_dd);

CREATE OR REPLACE VIEW price_latest WITH (security_invoker = true) AS
SELECT DISTINCT ON (v.ticker)
       v.trd_dd, v.trd_dd AS price_dd, v.ticker, v.market, v.open, v.high, v.low, v.close,
       v.volume, v.value, v.list_shrs, v.mktcap, v.prev_close, v.chg, v.chg_pct
FROM krx_daily_v v
ORDER BY v.ticker, v.trd_dd DESC;

ALTER TABLE krx_daily ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS krx_daily_read ON krx_daily;
CREATE POLICY krx_daily_read ON krx_daily FOR SELECT USING (true);

REVOKE ALL ON krx_daily, krx_daily_v, price_latest FROM anon, authenticated;
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
    args = ap.parse_args()
    if not args.apply:
        sys.stdout.write(render())
        return 0
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL 이 없습니다 — 적용하지 않았습니다.", file=sys.stderr)
        return 2
    apply(url)
    print("krx_daily 스키마 적용 완료 (멱등 — 다시 실행해도 같은 결과).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
