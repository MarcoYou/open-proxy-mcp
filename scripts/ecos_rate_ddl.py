#!/usr/bin/env python3
"""ecos_rate 스키마 — 무위험 금리(국고채 1년 일별) 표·접근 제어. **서버는 DDL 을 실행하지 않는다 — 사람이 배포 전에 한 번 적용한다.**

`scripts/krx_index_members_ddl.py` 와 같은 모양(스크립트 안의 `CREATE … IF NOT EXISTS` 상수, 전부 멱등).

    python3 scripts/ecos_rate_ddl.py            # SQL 을 화면에 낸다. 아무것도 실행하지 않는다
    python3 scripts/ecos_rate_ddl.py --no-tx    # BEGIN/COMMIT 없이 본문만
    python3 scripts/ecos_rate_ddl.py --apply    # DATABASE_URL 의 DB 에 한 트랜잭션으로 적용한다

■ 표 `ecos_rate` — 한국은행 ECOS 의 시장금리 일별. 지금은 국고채 1년 한 계열만 담는다. 적재는 `scripts/ecos_rate_daily.py`(GitHub Actions 일별), 읽는 곳은
  성과 분석 도구 `performance_analysis` 하나(무위험 금리 — 연율 → 기간 수익률 변환은 읽는 쪽이 한다: ACT/365 단리, 구간 시작 이전 값).
    series  계열 한 글자 — 1=국고채 1년(817Y002/010190000). 계열을 더할 수 있게 열을 남겨 둔다(더할 때는 아래 CHECK 의 허용 코드도 함께 고친다).
            `"char"` 는 1바이트 고정이라 text 열쇠보다 작다.
    d       관측일(date). ECOS 가 값을 낸 날(거래일)마다 한 행. 읽는 법: `d <= 기준일` 인 가장 늦은 행.
    rate    연율 %(예 3.25). real(4바이트) — ECOS 값은 소수 둘~셋째 자리라 정밀도는 충분하다.
  기본키 (series, d). 한 계열의 기준일 이하 가장 늦은 값을 이 색인 하나로 찾는다.
■ `ecos_fx_rate`(외화 환산 캐시)에 계열로 넣지 않는 까닭: 그 표는 `open_proxy_mcp/dart/fx.py` 한 곳이 (base_ccy, fx_dd) 등호로 읽는 영구 캐시이고
  모양(`_FX_DDL`)을 그 코드가 정한다. 여기는 뜻(연율 %)·보관(3년 창 롤링)·쓰는 주체(배치)가 다르다.
■ 보관 — 목표는 DB 에 3년 창(+ 계열마다 창 앞 마지막 행 하나)만 두는 것이고, 그 앞 전 기간은 DB 밖 보관본(콜드 서빙)이 맡는다.
  **그러나 창 밖 행은 보관본이 발행·서빙된 뒤에야 지워진다** — 그 전에는 표가 3년 창보다 길다(거래일마다 한 줄씩, 연 약 245행).
  지우기는 `scripts/ecos_rate_daily.py --prune` 의 가드(보관본 coverage·발행 뒤 72시간)를 통과해야만 하고, 일별 실행은 저장소 비밀값
  `ECOS_RATE_COLD_MANIFEST` 가 있을 때만 이어서 돈다. 크기(로컬 PG17 24개월 모의, 출발 750행): 지우기 없음 1,254행 0.13 MiB · 지우기 있음 750행 0.125 MiB(3년 창만이면 0.10 MiB).
■ 접근 제어 — 다른 표와 같은 꼴(RLS 켜기 + SELECT 정책 `USING (true)`). 읽기 전용 역할(`opm_ro`)은 이 정책 + 명시적 `GRANT SELECT` 로 읽는다.
  `anon`·`authenticated` 에는 권한을 주지 않는다(기본 권한 설정이 새 표에 자동으로 주는 SELECT 를 명시적으로 거둔다).
  **서버 계정 `opm_app` 은 이 표를 읽기만 한다**(성과 분석 도구가 무위험 금리를 읽는다 — 261022). `opm_app` 은 BYPASSRLS 가 없어 위 정책으로 읽고, 운영의 기본 권한
  (ALTER DEFAULT PRIVILEGES)이 postgres 가 만든 새 표에 주는 권한은 쓰기까지일 수 있어 아래 DO 블록이 `REVOKE ALL` 로 걷은 뒤 **SELECT 만** 다시 준다.
  쓰기는 일별 배치 계정(소유자)만 한다. 🔴 서버 계정 권한 행렬(운영 쪽 스크립트)도 같은 모양(읽기만)이어야 한다 — 행렬이 이 표를 「권한 안 줌」으로 두면 다음 적용 때
  이 권한이 다시 거둬진다. 권한이 없으면 읽기가 권한 오류로 실패하는데 서버의 DB 읽기(`db.pg_rows`)는 그것도 None(= DB 장애)으로 돌려주므로, 읽는 쪽은 금리 읽기를
  따로 떼어 그 지표만 비운다.
■ 소유자 — 표를 만든 역할이 소유자다. 일별 배치의 `DATABASE_URL`(VACUUM·REINDEX 는 소유자만)과 **같은 역할로 이 스크립트를 적용할 것**
  (운영의 다른 배치 표 krx_index_members·krx_wk 등도 같은 postgres 소유).
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
CREATE TABLE IF NOT EXISTS ecos_rate (
  series "char"          NOT NULL,   -- 1=국고채1년 (계열을 더할 수 있게 열을 남긴다)
  d      date            NOT NULL,   -- 관측일
  rate   real            NOT NULL,   -- 연율 %
  PRIMARY KEY (series, d),
  CONSTRAINT ecos_rate_series_known CHECK (series IN ('1')),
  CONSTRAINT ecos_rate_rate_plausible CHECK (rate > -1 AND rate < 30)
);

COMMENT ON TABLE ecos_rate IS 'ECOS 무위험 금리 3년 창(연율 %). 계열 1=국고채 1년. 쓰기=scripts/ecos_rate_daily.py';

ALTER TABLE ecos_rate ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ecos_rate_read ON ecos_rate;
CREATE POLICY ecos_rate_read ON ecos_rate FOR SELECT USING (true);

REVOKE ALL ON ecos_rate FROM anon, authenticated;
GRANT SELECT ON ecos_rate TO opm_ro;

-- 서버 계정이 이미 있으면 기본 권한이 준 것을 걷고 읽기만 다시 준다(없는 DB 에서는 건너뛴다) — 서버는 이 표를 읽을 뿐 쓰지 않는다.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'opm_app') THEN
    REVOKE ALL ON ecos_rate FROM opm_app;
    GRANT SELECT ON ecos_rate TO opm_app;
  END IF;
END
$$;
"""


def render() -> str:
    """사람이 SQL 편집기에 붙여 넣을 전체 스크립트."""
    return "BEGIN;\n\n" + STATEMENTS + "\nCOMMIT;\n"


def apply(url: str) -> None:
    import psycopg
    with psycopg.connect(url, connect_timeout=20) as con:      # 한 트랜잭션 — 중간에 실패하면 전부 되돌아간다
        con.execute("SET LOCAL lock_timeout = '3s'")
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
    print("ecos_rate 스키마 적용 완료 (멱등 — 다시 실행해도 같은 결과).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
