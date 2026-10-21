#!/usr/bin/env python3
"""wise_sector 구간형 스키마 — 업종분류 구간 표 `wise_sector_iv` + 스냅샷 날짜 목록 `wise_sector_snap`, 옛 이름 `wise_sector` 는 같은 칸의 뷰로.
**서버는 DDL 을 실행하지 않는다 — 사람이 한 번 적용한다.** (이 저장소 `scripts/*_ddl.py` 관례: 스크립트 안 상수, 전부 멱등)
선행: `scripts/krx_daily_ddl.py`(종목 번호표 `krx_ticker`).

261021 신설(재설계 E1 — 업종 구간형). 업종분류는 분기·연 단위로 바뀌는 느린 값인데 옛 표는 스냅샷마다 전 종목을 한 벌씩 다시 적었다
(3벌 8,268행 중 같은 종목·같은 분류 반복이 대부분). 같은 사실을 두 번 저장하지 않는다 — 한 종목의 분류가 연속한 스냅샷에서 같으면
구간 한 행이다. 읽는 쪽(서버 업종 조인·집계 배치)은 같은 이름 뷰를 읽으므로 바뀌지 않는다.

    python3 scripts/wise_sector_iv_ddl.py               # 새 DB(시험·로컬)용 SQL 출력: 표 2개 + 같은 이름 뷰. 아무것도 실행하지 않는다
    python3 scripts/wise_sector_iv_ddl.py --switch      # 운영 교체: 구간 채우기 → G2 관문(옛 표 vs 임시 뷰) → 옛 표를 wise_sector_legacy 로 → 같은 이름 뷰 (한 트랜잭션)
    python3 scripts/wise_sector_iv_ddl.py --rollback    # 되돌리기: 뷰가 지금 보여 주는 행 그대로 옛 표(wise_sector_legacy)를 wise_sector 로 되살린다
    python3 scripts/wise_sector_iv_ddl.py --drop-legacy # 옛 표 지우기(2주 뒤): 옛 표 행이 전부 뷰에 있을 때만. 지우기 전 retired Parquet 백업은 따로
    python3 scripts/wise_sector_iv_ddl.py --apply [--switch|--rollback|--drop-legacy]   # DATABASE_URL 의 DB 에 한 트랜잭션으로 적용

■ 표 `wise_sector_snap` — 스냅샷 날짜 목록(한 행 = 업종분류 스냅샷 하나): date · YYYYMMDD · 그 날 뷰 행 수 n. 구간 경계는 이 날짜들로만
    잡힌다(외래키). 날짜 조건(`snap_dd = …`·「이하 가장 최근」)은 이 작은 표에서 먼저 걸러진다. 빈 스냅샷(n = 0)은 두지 않는다(G2 가 본다).
■ 표 `wise_sector_iv` — 업종분류 구간(한 행 = 종목 × 같은 분류가 이어진 스냅샷 구간). 칸: 종목 번호 tid int4(`krx_ticker` — 6자리 코드를
    행마다 적지 않는다) · valid_from date(구간 첫 스냅샷) · valid_to date(구간이 끝난 **다음** 스냅샷 — 그 날은 구간 밖, NULL = 최신 스냅샷까지) ·
    출처 src "char" · 대분류 코드·이름 · 하위업종 코드·이름 · 출처 근거 src_ref. 칸 순서는 정렬 경계 기준(고정 길이 먼저).
    **이름 칸을 행 안에 둔다** — 코드표를 따로 만들면 `kr_wics_snapshots`(공급처 분류 트리 원문)와 같은 뜻의 표가 하나 더 생긴다.
    출처 1바이트: W=wise(구성종목 관측) · K=wise_company(기업 정보 화면 관측) · C=carry(직전 분류 이음) · P=pref(우선주가 보통주 분류 이음).
    `src_ref` 는 출처별 근거 글자 그대로(carry=관측 스냅샷 날짜 · pref=보통주 코드 · wise_company=「조회일:산업」 · wise=NULL) — 근거가 달라지면 구간도 나뉜다.
    기본키 (tid, valid_from) = 종목 이력. 겹치는 구간·붙어 있는데 값이 같은 구간(덜 접힌 것)은 G2·쓰기 확인이 0 을 본다.
■ 뷰 `wise_sector` — 옛 표와 칸 이름·순서·형이 같다(snap_dd text · ticker text · sector_code·sector·industry_code·industry text · src text · src_ref text).
    = 스냅샷 × 그 날을 덮는 구간(valid_from ≤ 날 < valid_to) × 번호표. security_invoker — 읽는 계정의 밑 표 권한·정책을 따른다.
■ DB 범위 — 3년 창: 다른 시계열과 같은 원칙(DB = 일별 창 + 주간 3년, 그 앞은 opm-cold). 업종분류는 **3년 창에 걸친 구간만** DB 에 둔다 —
    창 하한(`krx_retention.krx_weekly_cutoff`, 10-21 기준 20231016) 당시 유효한 스냅샷부터. 지금 스냅샷은 20260821~ 셋이라 창 밖이 없다.
    창 밖 구간을 콜드로 옮기는 일은 첫 해당일(관측 시작 20260821 + 3년) 전에, 또는 2026-08 이전 이력 적재 때 그 적재가 창 안만 넣는 것으로 한다.
■ 쓰는 쪽(`scripts/refresh_sector_class.py`, 월간 배치 — postgres 계정): 뷰 모드면 트랜잭션 안에서 뷰의 지금 행을 임시 작업 표로 펴서
    종전과 같은 규칙(관측·기업 화면·이어 쓰기·우선주)으로 고친 뒤, 바뀐 구간만 지우고 넣는다(`FLUSH_*`). 넣은 뒤 「뷰 = 작업 표」를 확인하고
    다르면 전체를 되돌린다. 번호표에 없는 종목은 같은 사전 규칙(새 종목만 `WHERE NOT EXISTS`)으로 등록한다. 쓰는 쪽은 `wise_sector` 가
    뷰인지 표인지 보고 고른다 — 이 DDL 을 적용하기 전에 코드가 먼저 배포돼도 지금과 똑같이 돈다.
■ 접근 제어 — 다른 표와 같은 꼴(RLS 켜기 + SELECT 정책). `anon`·`authenticated` 에는 권한을 주지 않고, `opm_ro` 만 SELECT.
    서버 계정 `opm_app` 은 읽기만(업종 조인) — 뷰가 security_invoker 라 밑 표 SELECT 가 따로 필요하다. 있으면 여기서 SELECT 를 주고,
    정책·정리는 권한 행렬 스크립트(opm_app_role.py --apply)가 맡는다 — **교체 뒤 그 스크립트를 다시 --apply**.
■ 교체 순서와 잠금(krx_weekly 교체와 같은 꼴): 옛 표는 처음부터 SHARE 잠금(읽기는 그대로, 쓰기만 기다림) — G2 대조는 이름을 바꾸기 **전에**
    옛 표와 같은 정의의 임시 뷰(`pg_temp._wise_sector_next`) 사이에서 한다. ACCESS EXCLUSIVE 는 마지막 이름 바꾸기·뷰 만들기(밀리초)에만.
    대조는 정렬로(`enable_hashagg = off` — 운영 인스턴스는 메모리가 작고 해시 집합 연산은 work_mem 을 넘어도 디스크로 내리지 않는다).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: 출처 글자 ↔ 1바이트("char"). 뷰와 구간 만들기가 같은 대응을 쓴다. 모르는 출처는 NULL → NOT NULL 위반으로 멈춘다(지어내지 않는다).
SRC_CODES = (("wise", "W"), ("wise_company", "K"), ("carry", "C"), ("pref", "P"))
_SRC_TO_CHAR = "CASE {col} " + " ".join(f"WHEN '{t}' THEN '{c}'" for t, c in SRC_CODES) + " END::\"char\""
_CHAR_TO_SRC = "CASE {col} " + " ".join(f"WHEN '{c}' THEN '{t}'" for t, c in SRC_CODES) + " END::text"

TABLES = """\
-- 업종분류 스냅샷 날짜 목록: 날짜 두 표기(date·YYYYMMDD)와 그 날 뷰 행 수. 구간 경계는 이 날짜들로만 잡힌다.
CREATE TABLE IF NOT EXISTS wise_sector_snap (
  snap date    PRIMARY KEY,
  dd   text    NOT NULL UNIQUE,
  n    integer NOT NULL DEFAULT 0,
  CONSTRAINT wise_sector_snap_dd_ck CHECK (dd = (extract(year FROM snap)::int * 10000 + extract(month FROM snap)::int * 100
                                                 + extract(day FROM snap)::int)::text)
);
-- 업종분류 구간(한 행 = 종목 × 같은 분류가 이어진 스냅샷 구간 [valid_from, valid_to)). 칸 순서는 정렬 경계 기준(고정 길이 먼저).
CREATE TABLE IF NOT EXISTS wise_sector_iv (
  tid           integer NOT NULL REFERENCES krx_ticker (tid),
  valid_from    date    NOT NULL REFERENCES wise_sector_snap (snap),
  valid_to      date             REFERENCES wise_sector_snap (snap),        -- 구간이 끝난 다음 스냅샷(그 날은 밖). NULL = 최신까지
  src           "char"  NOT NULL CONSTRAINT wise_sector_iv_src_ck CHECK (src IN ('W', 'K', 'C', 'P')),
  sector_code   text    NOT NULL,     -- G25 등 대분류 10
  sector        text    NOT NULL,
  industry_code text    NOT NULL,     -- G2510 등 하위업종 28
  industry      text    NOT NULL,
  src_ref       text,                 -- carry=관측 스냅샷 날짜 · pref=보통주 코드 · wise_company=조회일:산업 · wise=NULL
  PRIMARY KEY (tid, valid_from),
  CONSTRAINT wise_sector_iv_range_ck CHECK (valid_to IS NULL OR valid_to > valid_from)
);
COMMENT ON TABLE wise_sector_iv IS '업종분류 구간(E1) — 뷰 wise_sector 의 밑 표. 쓰는 곳 scripts/refresh_sector_class.py(구간 차분), 정의 scripts/wise_sector_iv_ddl.py';
COMMENT ON COLUMN wise_sector_iv.src IS 'W=wise(구성종목 관측) · K=wise_company(기업 정보 화면 관측) · C=carry(직전 분류 이음) · P=pref(우선주가 보통주 분류 이음)';

ALTER TABLE wise_sector_snap ENABLE ROW LEVEL SECURITY;
ALTER TABLE wise_sector_iv   ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS wise_sector_snap_read ON wise_sector_snap;
CREATE POLICY wise_sector_snap_read ON wise_sector_snap FOR SELECT USING (true);
DROP POLICY IF EXISTS wise_sector_iv_read ON wise_sector_iv;
CREATE POLICY wise_sector_iv_read ON wise_sector_iv FOR SELECT USING (true);
REVOKE ALL ON wise_sector_snap, wise_sector_iv FROM anon, authenticated;
GRANT SELECT ON wise_sector_snap, wise_sector_iv TO opm_ro;
-- 서버 전용 계정(opm_app)이 이미 있으면 읽기만 준다(뷰가 security_invoker — 밑 표 SELECT 가 있어야 업종 조인이 읽힌다). 쓰기는 주지 않는다.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'opm_app') THEN
    GRANT SELECT ON wise_sector_snap, wise_sector_iv TO opm_app;
  END IF;
END $$;
"""

#: 뷰 정의(이름 없이) — 같은 이름 뷰와 교체 G2 의 임시 뷰가 글자 그대로 같은 정의를 쓴다.
VIEW_SELECT = """\
SELECT s.dd AS snap_dd, k.ticker, i.sector_code, i.sector, i.industry_code, i.industry,
       """ + _CHAR_TO_SRC.format(col="i.src") + """ AS src, i.src_ref
FROM wise_sector_snap s
JOIN wise_sector_iv i ON i.valid_from <= s.snap AND (i.valid_to IS NULL OR s.snap < i.valid_to)
JOIN krx_ticker k ON k.tid = i.tid"""

VIEW = """\
-- 옛 표 wise_sector 와 같은 칸 이름·순서·형. 스냅샷 × 그 날을 덮는 구간 × 번호표.
CREATE OR REPLACE VIEW wise_sector WITH (security_invoker = true) AS
""" + VIEW_SELECT + """;
COMMENT ON VIEW wise_sector IS '업종분류 스냅샷(종목마다 한 줄) — 구간 표 wise_sector_iv × 날짜 목록 wise_sector_snap 의 뷰(E1, scripts/wise_sector_iv_ddl.py)';
COMMENT ON COLUMN wise_sector.src IS '행 출처: wise=그 날 공급처 구성종목 응답(관측) · wise_company=구성종목에 없는 상장 보통주, 공급처 기업 정보 화면의 산업을 하위업종으로 올림(관측) · carry=관측에서 빠진 상장 종목, 직전 분류를 이음 · pref=우선주, 같은 회사 보통주 분류를 이음 (scripts/refresh_sector_class.py)';
COMMENT ON COLUMN wise_sector.src_ref IS 'carry=그 분류를 실제로 관측한 스냅샷 날짜(YYYYMMDD) · pref=보통주 코드 · wise_company=조회일(YYYYMMDD):기업 정보 화면의 산업 이름 · wise=NULL';
REVOKE ALL ON wise_sector FROM anon, authenticated;
GRANT SELECT ON wise_sector TO opm_ro;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'opm_app') THEN
    GRANT SELECT ON wise_sector TO opm_app;
  END IF;
END $$;
"""


def islands_select(src: str) -> str:
    """종목마다 연속한 스냅샷에서 값(출처·분류·근거)이 같은 행을 구간 하나로 접는 SELECT. `src` = 옛 모양 행(스냅샷 × 종목)이 있는 관계.

    「연속」은 전체 스냅샷 순서(src 에 나오는 날짜 전부)에서 바로 다음 스냅샷이다 — 중간 스냅샷에 종목이 없으면 구간이 끊긴다.
    valid_to = 구간 마지막 스냅샷의 **다음** 스냅샷(없으면 NULL). 섬 찾기 = 순번 − 같은 값 안의 순번."""
    return f"""\
WITH s AS (SELECT d.snap_dd, to_date(d.snap_dd, 'YYYYMMDD') AS snap,
                  row_number() OVER (ORDER BY d.snap_dd) AS rk,
                  lead(to_date(d.snap_dd, 'YYYYMMDD')) OVER (ORDER BY d.snap_dd) AS nxt
           FROM (SELECT DISTINCT snap_dd FROM {src}) d),
x AS (SELECT k.tid, s.snap, s.rk, s.nxt, {_SRC_TO_CHAR.format(col="w.src")} AS src,
             w.sector_code, w.sector, w.industry_code, w.industry, w.src_ref
      FROM {src} w JOIN s ON s.snap_dd = w.snap_dd JOIN krx_ticker k ON k.ticker = w.ticker),
g AS (SELECT x.*, x.rk - row_number() OVER (PARTITION BY x.tid, x.src, x.sector_code, x.sector, x.industry_code, x.industry, x.src_ref
                                            ORDER BY x.rk) AS grp
      FROM x)
SELECT tid, min(snap) AS valid_from, (array_agg(nxt ORDER BY rk DESC))[1] AS valid_to, src,
       sector_code, sector, industry_code, industry, src_ref
FROM g
GROUP BY tid, src, sector_code, sector, industry_code, industry, src_ref, grp"""


IV_COLS = "tid, valid_from, valid_to, src, sector_code, sector, industry_code, industry, src_ref"


def register_tickers_sql(src: str) -> str:
    """번호표에 없는 종목을 등록한다 — 사전 규칙 그대로(새 종목만 `WHERE NOT EXISTS`, 코드순 — 번호 생성기를 태우지 않게)."""
    return (f"INSERT INTO krx_ticker (ticker) SELECT DISTINCT w.ticker FROM {src} w "
            "WHERE NOT EXISTS (SELECT 1 FROM krx_ticker k WHERE k.ticker = w.ticker) ORDER BY 1 "
            "ON CONFLICT (ticker) DO NOTHING")


#: 구간 표 무결성 — 0 이어야 한다. (겹침: 한 종목의 다음 구간이 이 구간 끝보다 먼저 시작 · 덜 접힘: 붙어 있는 두 구간의 값이 같음 ·
#: 빈 스냅샷: 목록에 있는데 뷰 행 0 · 행 수 어긋남: 목록의 n ≠ 뷰 행 수)
IV_CHECKS = """\
INSERT INTO {t} SELECT 'iv_overlap', (SELECT count(*) FROM (
  SELECT valid_to, lead(valid_from) OVER (PARTITION BY tid ORDER BY valid_from) AS nf FROM wise_sector_iv) x
  WHERE x.nf IS NOT NULL AND (x.valid_to IS NULL OR x.valid_to > x.nf));
INSERT INTO {t} SELECT 'iv_unmerged', (SELECT count(*) FROM wise_sector_iv a JOIN wise_sector_iv b
  ON b.tid = a.tid AND b.valid_from = a.valid_to AND b.src = a.src AND b.sector_code = a.sector_code AND b.sector = a.sector
     AND b.industry_code = a.industry_code AND b.industry = a.industry AND b.src_ref IS NOT DISTINCT FROM a.src_ref);
INSERT INTO {t} SELECT 'snap_empty', (SELECT count(*) FROM wise_sector_snap s
  WHERE NOT EXISTS (SELECT 1 FROM wise_sector_iv i WHERE i.valid_from <= s.snap AND (i.valid_to IS NULL OR s.snap < i.valid_to)));
INSERT INTO {t} SELECT 'snap_n', (SELECT count(*) FROM wise_sector_snap s WHERE s.n <> (SELECT count(*) FROM wise_sector_iv i
  WHERE i.valid_from <= s.snap AND (i.valid_to IS NULL OR s.snap < i.valid_to)));
"""

# ── 운영 교체 ────────────────────────────────────────────────────────────────

#: 교체 전 확인 — 이미 뷰면(두 번 돌림) 멈춘다. 번호표(krx_ticker)가 먼저 있어야 한다.
_SWITCH_PRECHECK = """\
DO $$
BEGIN
  IF (SELECT relkind FROM pg_class WHERE oid = to_regclass('wise_sector')) IS DISTINCT FROM 'r' THEN
    RAISE EXCEPTION 'wise_sector 가 표가 아니다 — 이미 교체됐거나 없다(교체하지 않는다)';
  END IF;
  IF to_regclass('krx_ticker') IS NULL THEN
    RAISE EXCEPTION '종목 번호표 krx_ticker(scripts/krx_daily_ddl.py)가 먼저 있어야 한다';
  END IF;
  IF to_regclass('wise_sector_legacy') IS NOT NULL THEN
    RAISE EXCEPTION 'wise_sector_legacy 가 이미 있다 — 앞 교체의 옛 표를 먼저 정리(--drop-legacy)하거나 손으로 확인';
  END IF;
END $$;
-- 옛 표 쓰기만 막는다(읽기는 그대로) — 옮겨 담기부터 G2 까지 옛 표가 바뀌지 않게(월간 배치가 겹치면 커밋까지 기다리게 한다).
LOCK TABLE wise_sector IN SHARE MODE;
"""

SWITCH_MIGRATE = """\
-- 다시 교체하는 경우(되돌린 뒤) 옛 내용을 버리고 지금 표에서 새로 만든다. 표 모드에서는 쓰는 쪽이 이 두 표를 쓰지 않는다.
TRUNCATE wise_sector_iv, wise_sector_snap;
""" + register_tickers_sql("wise_sector") + """;
INSERT INTO wise_sector_snap (snap, dd, n)
  SELECT to_date(snap_dd, 'YYYYMMDD'), snap_dd, count(*) FROM wise_sector GROUP BY snap_dd ORDER BY snap_dd;
-- 종목 번호순 → 구간 시작순으로 넣는다(한 종목의 이력이 물리적으로 모인다).
INSERT INTO wise_sector_iv (""" + IV_COLS + """)
SELECT """ + IV_COLS + """ FROM (
""" + islands_select("wise_sector") + """
) n ORDER BY tid, valid_from;
"""

#: 교체 G2 관문 — 옛 표(아직 이름이 wise_sector)와 **같은 정의의 임시 뷰**가 전 행·전 칸 같아야 이름을 바꾼다. 하나라도 다르면 예외 → 전체 되돌림.
SWITCH_G2_SQL = """\
SET LOCAL statement_timeout = '600s';
SET LOCAL enable_hashagg = off;
DROP VIEW IF EXISTS pg_temp._wise_sector_next;
CREATE TEMP VIEW _wise_sector_next WITH (security_invoker = true) AS
""" + VIEW_SELECT + """;
CREATE TEMP TABLE _g2 (chk text PRIMARY KEY, diff bigint NOT NULL) ON COMMIT DROP;
INSERT INTO _g2 SELECT 'legacy_empty', (SELECT (count(*) = 0)::int FROM wise_sector);
INSERT INTO _g2 SELECT 'col_types', (SELECT count(*) FROM (
  (SELECT row_number() OVER (ORDER BY attnum) AS pos, attname, atttypid, atttypmod FROM pg_attribute
    WHERE attrelid = 'wise_sector'::regclass AND attnum > 0 AND NOT attisdropped
   EXCEPT
   SELECT row_number() OVER (ORDER BY attnum), attname, atttypid, atttypmod FROM pg_attribute
    WHERE attrelid = 'pg_temp._wise_sector_next'::regclass AND attnum > 0 AND NOT attisdropped)
  UNION ALL
  (SELECT row_number() OVER (ORDER BY attnum), attname, atttypid, atttypmod FROM pg_attribute
    WHERE attrelid = 'pg_temp._wise_sector_next'::regclass AND attnum > 0 AND NOT attisdropped
   EXCEPT
   SELECT row_number() OVER (ORDER BY attnum), attname, atttypid, atttypmod FROM pg_attribute
    WHERE attrelid = 'wise_sector'::regclass AND attnum > 0 AND NOT attisdropped)) x);
INSERT INTO _g2 SELECT 'l_minus_v', (SELECT count(*) FROM (SELECT * FROM wise_sector EXCEPT ALL SELECT * FROM pg_temp._wise_sector_next) x);
INSERT INTO _g2 SELECT 'v_minus_l', (SELECT count(*) FROM (SELECT * FROM pg_temp._wise_sector_next EXCEPT ALL SELECT * FROM wise_sector) x);
""" + IV_CHECKS.format(t="_g2") + """\
SELECT chk, diff FROM _g2 ORDER BY chk;
SELECT (SELECT count(*) FROM wise_sector) AS legacy_rows, (SELECT count(*) FROM wise_sector_iv) AS iv_rows,
       (SELECT count(*) FROM wise_sector_snap) AS snaps, (SELECT min(dd) FROM wise_sector_snap) AS snap_first,
       (SELECT max(dd) FROM wise_sector_snap) AS snap_last;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM _g2 WHERE diff <> 0) THEN
    RAISE EXCEPTION 'G2 관문 실패 — 교체를 되돌린다: %', (SELECT string_agg(chk || '=' || diff, ', ' ORDER BY chk) FROM _g2 WHERE diff <> 0);
  END IF;
  RAISE NOTICE 'G2 관문 통과 — 차이 0';
END $$;
"""

#: 이름 바꾸기 — G2 를 통과한 뒤에만. 여기서부터 커밋까지가 읽는 쪽을 막는 ACCESS EXCLUSIVE 구간이다(이름 바꾸기·뷰 만들기·정의 확인뿐).
#: 기본키 색인 이름을 바꾸면 제약 이름도 함께 바뀐다. opm_app 이름의 정책(권한 행렬이 만든 것)은 옛 표를 따라가고, 다음 --apply 가 지운다.
SWITCH_RENAME = """\
ALTER TABLE wise_sector RENAME TO wise_sector_legacy;
ALTER INDEX IF EXISTS wise_sector_pkey RENAME TO wise_sector_legacy_pkey;
ALTER INDEX IF EXISTS idx_wise_sector_ticker RENAME TO idx_wise_sector_legacy_ticker;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_policy WHERE polrelid = 'wise_sector_legacy'::regclass AND polname = 'wise_sector_read') THEN
    ALTER POLICY wise_sector_read ON wise_sector_legacy RENAME TO wise_sector_legacy_read;
  END IF;
END $$;
COMMENT ON TABLE wise_sector_legacy IS '옛 업종분류 스냅샷 표(E1 교체 전) — 되돌리기용, 2주 뒤 scripts/wise_sector_iv_ddl.py --drop-legacy. 서버는 읽지 않는다';
"""

#: 이름을 바꾼 뒤 — 새 같은 이름 뷰가 G2 를 통과한 임시 뷰와 **같은 정의**인지(행을 다시 읽지 않는 확인) → 임시 뷰를 지운다.
SWITCH_FINAL_SQL = """\
DO $$
BEGIN
  IF pg_get_viewdef('wise_sector'::regclass) IS DISTINCT FROM pg_get_viewdef('pg_temp._wise_sector_next'::regclass) THEN
    RAISE EXCEPTION '새 뷰 정의가 G2 를 통과한 정의와 다르다 — 교체를 되돌린다';
  END IF;
END $$;
DROP VIEW pg_temp._wise_sector_next;
"""

#: 되돌리기 — 뷰가 **지금** 보여 주는 행(교체 뒤 새로 쌓인 스냅샷 포함)을 그대로 옛 표로 되살린다. 구간 두 표는 남겨 둔다
#: (표 모드에서는 쓰는 쪽이 쓰지 않는다 — 다시 교체할 때 새로 채운다). 짝이 되는 코드 되돌리기는 필요 없다(쓰는 쪽이 모드를 본다).
ROLLBACK_BODY = """\
DO $$
BEGIN
  IF (SELECT relkind FROM pg_class WHERE oid = to_regclass('wise_sector')) IS DISTINCT FROM 'v' THEN
    RAISE EXCEPTION 'wise_sector 가 뷰가 아니다 — 되돌릴 것이 없다';
  END IF;
  IF to_regclass('wise_sector_legacy') IS NULL THEN
    RAISE EXCEPTION 'wise_sector_legacy 가 없다 — 옛 표를 이미 지웠다(retired 백업에서 복원)';
  END IF;
END $$;
-- 쓰는 쪽(월간 배치)이 구간 표를 고치는 중이면 기다린다(그 배치는 같은 잠금을 쥔다).
LOCK TABLE wise_sector_iv, wise_sector_snap IN SHARE ROW EXCLUSIVE MODE;
SET LOCAL statement_timeout = '600s';
SET LOCAL enable_hashagg = off;
DROP TABLE IF EXISTS _ws_now;
CREATE TEMP TABLE _ws_now ON COMMIT DROP AS SELECT * FROM wise_sector;
ANALYZE _ws_now;
DROP VIEW wise_sector;
ALTER TABLE wise_sector_legacy RENAME TO wise_sector;
ALTER INDEX IF EXISTS wise_sector_legacy_pkey RENAME TO wise_sector_pkey;
ALTER INDEX IF EXISTS idx_wise_sector_legacy_ticker RENAME TO idx_wise_sector_ticker;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_policy WHERE polrelid = 'wise_sector'::regclass AND polname = 'wise_sector_legacy_read') THEN
    ALTER POLICY wise_sector_legacy_read ON wise_sector RENAME TO wise_sector_read;
  END IF;
END $$;
COMMENT ON TABLE wise_sector IS NULL;
-- 뷰에 없는(또는 값이 다른) 옛 행을 지우고, 표에 없는(또는 값이 다른) 뷰 행을 넣는다(기본키 (snap_dd, ticker)).
DELETE FROM wise_sector t
  WHERE NOT EXISTS (SELECT 1 FROM _ws_now v WHERE v.snap_dd = t.snap_dd AND v.ticker = t.ticker
                    AND v.sector_code = t.sector_code AND v.sector = t.sector AND v.industry_code = t.industry_code
                    AND v.industry = t.industry AND v.src = t.src AND v.src_ref IS NOT DISTINCT FROM t.src_ref);
INSERT INTO wise_sector (snap_dd, ticker, sector_code, sector, industry_code, industry, src, src_ref)
  SELECT v.snap_dd, v.ticker, v.sector_code, v.sector, v.industry_code, v.industry, v.src, v.src_ref FROM _ws_now v
  WHERE NOT EXISTS (SELECT 1 FROM wise_sector t WHERE t.snap_dd = v.snap_dd AND t.ticker = v.ticker)
  ORDER BY v.snap_dd, v.ticker;
CREATE TEMP TABLE _rb (chk text PRIMARY KEY, diff bigint NOT NULL) ON COMMIT DROP;
INSERT INTO _rb SELECT 't_minus_v', (SELECT count(*) FROM (SELECT * FROM wise_sector EXCEPT ALL SELECT * FROM _ws_now) x);
INSERT INTO _rb SELECT 'v_minus_t', (SELECT count(*) FROM (SELECT * FROM _ws_now EXCEPT ALL SELECT * FROM wise_sector) x);
SELECT chk, diff FROM _rb ORDER BY chk;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM _rb WHERE diff <> 0) THEN
    RAISE EXCEPTION '되돌리기 대조 실패 — 아무것도 바꾸지 않는다: %', (SELECT string_agg(chk || '=' || diff, ', ' ORDER BY chk) FROM _rb WHERE diff <> 0);
  END IF;
  RAISE NOTICE '되돌리기 대조 통과 — 표 = 되돌리기 직전 뷰';
END $$;
"""

#: 옛 표 지우기 — 옛 표의 행이 **전부** 지금 뷰에 같은 값으로 있을 때만(교체 뒤 옛 스냅샷을 다시 수집·다시 만들어 값이 바뀌었으면 멈춘다 —
#: 그 차이는 사람이 보고 정한다). 지우기 전 retired Parquet 백업(행 수·지문)은 따로 한다.
DROP_LEGACY_BODY = """\
SET LOCAL statement_timeout = '600s';
SET LOCAL enable_hashagg = off;
DO $$
DECLARE missing bigint;
BEGIN
  IF (SELECT relkind FROM pg_class WHERE oid = to_regclass('wise_sector')) IS DISTINCT FROM 'v' THEN
    RAISE EXCEPTION 'wise_sector 가 뷰가 아니다 — 옛 표를 지우지 않는다';
  END IF;
  SELECT count(*) INTO missing FROM (SELECT * FROM wise_sector_legacy EXCEPT ALL SELECT * FROM wise_sector) x;
  IF missing <> 0 THEN
    RAISE EXCEPTION '옛 표 행 % 개가 뷰에 없다 — 지우지 않는다', missing;
  END IF;
  RAISE NOTICE '옛 표 행이 전부 뷰에 있다 — 지운다';
END $$;
DROP TABLE wise_sector_legacy;
"""

# ── 쓰는 쪽(scripts/refresh_sector_class.py)이 뷰 모드에서 쓰는 문장 ──────────────────────────────────────

#: 작업 표 — 옛 표와 같은 칸·기본키. 트랜잭션이 끝나면 사라진다. 쓰는 쪽의 기존 규칙(INSERT … ON CONFLICT (snap_dd, ticker) 등)이 그대로 돈다.
WORK_TABLE = "_ws_work"
FLUSH_OPEN = f"""\
SET LOCAL lock_timeout = '60s';
LOCK TABLE wise_sector_iv, wise_sector_snap IN SHARE ROW EXCLUSIVE MODE;
DROP TABLE IF EXISTS pg_temp.{WORK_TABLE};
CREATE TEMP TABLE {WORK_TABLE} (
  snap_dd text NOT NULL, ticker text NOT NULL, sector_code text NOT NULL, sector text NOT NULL,
  industry_code text NOT NULL, industry text NOT NULL, src text NOT NULL DEFAULT 'wise', src_ref text,
  PRIMARY KEY (snap_dd, ticker)) ON COMMIT DROP;
INSERT INTO {WORK_TABLE} SELECT * FROM wise_sector;
"""

#: 바뀐 구간만 고친다: 새 구간 집합을 임시 표로 → 날짜 목록에 새 스냅샷 → 새 집합에 없는(값이 다른) 구간 지우기 → 없는 구간 넣기 →
#: 행이 없어진 스냅샷 지우기 → 행 수 n. 마지막에 「뷰 = 작업 표」(전 행·전 칸)와 구간 무결성을 확인하고 하나라도 어긋나면 예외(전체 되돌림).
FLUSH_APPLY = f"""\
SET LOCAL enable_hashagg = off;
DROP TABLE IF EXISTS pg_temp._ws_iv_new;
""" + register_tickers_sql(WORK_TABLE) + f""";
CREATE TEMP TABLE _ws_iv_new ON COMMIT DROP AS
{islands_select(WORK_TABLE)};
INSERT INTO wise_sector_snap (snap, dd)
  SELECT DISTINCT to_date(w.snap_dd, 'YYYYMMDD'), w.snap_dd FROM {WORK_TABLE} w
  WHERE NOT EXISTS (SELECT 1 FROM wise_sector_snap s WHERE s.dd = w.snap_dd) ORDER BY 1;
DELETE FROM wise_sector_iv i WHERE NOT EXISTS (
  SELECT 1 FROM _ws_iv_new n WHERE n.tid = i.tid AND n.valid_from = i.valid_from AND n.valid_to IS NOT DISTINCT FROM i.valid_to
     AND n.src = i.src AND n.sector_code = i.sector_code AND n.sector = i.sector AND n.industry_code = i.industry_code
     AND n.industry = i.industry AND n.src_ref IS NOT DISTINCT FROM i.src_ref);
INSERT INTO wise_sector_iv ({IV_COLS})
  SELECT {IV_COLS} FROM _ws_iv_new n
  WHERE NOT EXISTS (SELECT 1 FROM wise_sector_iv i WHERE i.tid = n.tid AND i.valid_from = n.valid_from)
  ORDER BY tid, valid_from;
DELETE FROM wise_sector_snap s WHERE NOT EXISTS (SELECT 1 FROM {WORK_TABLE} w WHERE w.snap_dd = s.dd);
UPDATE wise_sector_snap s SET n = c.n FROM (SELECT snap_dd, count(*) AS n FROM {WORK_TABLE} GROUP BY snap_dd) c
  WHERE c.snap_dd = s.dd AND s.n IS DISTINCT FROM c.n;
"""

#: 쓰기 뒤 확인(읽기만) — (이름, 차이) 행들. 쓰는 쪽이 하나라도 0 이 아니면 예외로 되돌린다.
FLUSH_CHECK = f"""\
DROP TABLE IF EXISTS pg_temp._ws_chk;
CREATE TEMP TABLE _ws_chk (chk text PRIMARY KEY, diff bigint NOT NULL) ON COMMIT DROP;
INSERT INTO _ws_chk SELECT 'work_minus_view', (SELECT count(*) FROM (SELECT * FROM {WORK_TABLE} EXCEPT ALL SELECT * FROM wise_sector) x);
INSERT INTO _ws_chk SELECT 'view_minus_work', (SELECT count(*) FROM (SELECT * FROM wise_sector EXCEPT ALL SELECT * FROM {WORK_TABLE}) x);
""" + IV_CHECKS.format(t="_ws_chk")
FLUSH_CHECK_READ = "SELECT chk, diff FROM _ws_chk ORDER BY chk"


def body(mode: str) -> str:
    """모드별 트랜잭션 본문 — full(새 DB: 표 + 뷰) · switch(운영 교체) · rollback(되돌리기) · drop-legacy(옛 표 지우기)."""
    if mode == "full":
        return TABLES + VIEW
    if mode == "switch":
        return _SWITCH_PRECHECK + TABLES + SWITCH_MIGRATE + SWITCH_G2_SQL + SWITCH_RENAME + VIEW + SWITCH_FINAL_SQL
    if mode == "rollback":
        return ROLLBACK_BODY
    if mode == "drop-legacy":
        return DROP_LEGACY_BODY
    raise ValueError(mode)


def render(mode: str = "full") -> str:
    """사람이 SQL 편집기·psql 에 붙여 넣을 전체 스크립트."""
    return "BEGIN;\nSET LOCAL lock_timeout = '3s';\n\n" + body(mode) + "\nCOMMIT;\n"


def relkind(con, name: str = "wise_sector"):
    """'r'(표) · 'v'(뷰) · None(없음). 현재 search_path 기준."""
    r = con.execute("SELECT c.relkind::text FROM pg_class c WHERE c.oid = to_regclass(%s)", (name,)).fetchone()
    return r[0] if r else None


def apply(url: str, mode: str = "full") -> None:
    import psycopg
    with psycopg.connect(url, connect_timeout=20) as con:      # 한 트랜잭션 — 중간에 실패하면 전부 되돌아간다
        con.execute("SET LOCAL lock_timeout = '3s'")
        if mode == "full" and relkind(con) == "r":
            raise SystemExit("wise_sector 가 아직 표다 — 운영은 --switch(교체)를 쓴다")
        con.execute(body(mode))
        con.commit()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="DATABASE_URL 의 DB 에 적용 (없으면 SQL 만 출력)")
    ap.add_argument("--no-tx", action="store_true", help="출력할 때 BEGIN/COMMIT 을 붙이지 않는다")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--switch", action="store_true", help="운영 교체(G2 관문 → 옛 표 wise_sector_legacy, 같은 이름 뷰)")
    g.add_argument("--rollback", action="store_true", help="되돌리기(지금 뷰의 행 그대로 표로)")
    g.add_argument("--drop-legacy", action="store_true", help="옛 표 지우기(옛 행이 전부 뷰에 있을 때만)")
    a = ap.parse_args()
    mode = ("switch" if a.switch else "rollback" if a.rollback else "drop-legacy" if a.drop_legacy else "full")
    if not a.apply:
        sys.stdout.write(body(mode) if a.no_tx else render(mode))
        return 0
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL 이 없습니다 — 적용하지 않았습니다.", file=sys.stderr)
        return 2
    apply(url, mode)
    print(f"wise_sector 구간형 스키마 {mode} 적용 완료.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
