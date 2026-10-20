#!/usr/bin/env python3
"""krx_weekly 스키마 — 닫힌 주 압축 표 `krx_wk` + 주 달력 `krx_wk_day`, 옛 이름 `krx_weekly` 는 같은 칸의 뷰로.
**서버는 DDL 을 실행하지 않는다 — 사람이 한 번 적용한다.** (이 저장소 `scripts/*_ddl.py` 관례: 스크립트 안 상수, 전부 멱등)
선행: `scripts/krx_daily_ddl.py`(일별 사실값 `krx_px`·달력 `krx_day`·번호표 `krx_ticker`).

261019 신설(재설계 v03 C4). 같은 사실을 두 번 저장하지 않는다 — 일별 보관 창(약 1년) 안의 닫힌 주는 일별 저장분의
주 닫는 날(`krx_day.wk_close`) 행 그 자체이고, 창보다 이른 닫힌 주(3년 + 종목별 연말 행)만 압축 표 `krx_wk` 에 둔다.

    python3 scripts/krx_weekly_ddl.py               # 새 DB(시험·로컬)용 SQL 출력: 표 2개 + 같은 이름 뷰. 아무것도 실행하지 않는다
    python3 scripts/krx_weekly_ddl.py --switch      # 운영 교체: krx_wk 채우기 → G2 관문(옛 표 vs 임시 뷰) → 옛 표를 krx_weekly_legacy 로 → 같은 이름 뷰 (한 트랜잭션)
    python3 scripts/krx_weekly_ddl.py --rollback    # 되돌리기: 뷰가 지금 보여 주는 행 그대로 옛 표(krx_weekly_legacy)를 krx_weekly 로 되살린다
    python3 scripts/krx_weekly_ddl.py --drop-legacy # 옛 표 지우기: 옛 표 행이 전부 뷰에 있을 때만(아니면 되돌린다). 지우기 전 retired Parquet 백업은 따로
    python3 scripts/krx_weekly_ddl.py --apply [--switch|--rollback|--drop-legacy]   # DATABASE_URL 의 DB 에 한 트랜잭션으로 적용

■ 표 `krx_wk` — 닫힌 주 사실값(한 행 = 종목 × 주 닫는 날). 칸: 상장주식수 int8 · 종가 int4 · 주 닫는 날 date · 종목 번호 int4 ·
    시장 1바이트("char" 'S'/'Q'). 시가총액은 저장하지 않는다(뷰가 종가 × 상장주식수 — 운영 455,505행 전부 저장값과 같음 확인).
    기본키 (tid, wk_dd) = 종목 시계열, BRIN(블록 범위 요약 색인)(wk_dd) = 날짜 단면(날짜순으로 넣는다). 외래키로 달력·번호표에 없는
    행이 생기지 않는다(뷰의 내부 조인이 행을 잃지 않게).
■ 표 `krx_wk_day` — krx_wk 의 주 달력: date·YYYYMMDD·그 날 행 수 n. 날짜 조건(`price_dd = …`·범위)은 이 작은 표에서 먼저 걸러진다.
■ 뷰 `krx_weekly` — 옛 표와 칸 이름·순서·형이 같다(price_dd text · ticker text · market text · close·mktcap·list_shrs bigint).
    = krx_wk(주 달력 조인) ∪ 일별 저장분의 주 닫는 날 행(`krx_day.wk_close`, 단 krx_wk_day 에 이미 있는 날은 뺀다 — 한 날은 한 곳에서만 나온다).
    한 주 = ISO 주 ∩ 달력 연도(`krx_retention.week_unit`) — 주 닫는 날 규칙은 `open_proxy_mcp/krx_daily.py` 의 `WK_CLOSE_SQL`.
■ 쓰는 쪽(`open_proxy_mcp/krx_daily.py`): 일별 보관 창 정리(`prune_window`)가 창 밖으로 나가는 주 닫는 날 행을 **지우기 전에** krx_wk 로
    옮긴다. 주간 닫기(`sync_weekly`)는 표에 쓰지 않는다(뷰가 달력에서 계산). 쓰는 쪽은 `krx_weekly` 가 뷰인지 표인지 보고 고른다 —
    이 DDL 을 적용하기 전에 코드가 먼저 배포돼도 지금과 똑같이 돈다.
■ 접근 제어 — 다른 표와 같은 꼴(RLS 켜기 + SELECT 정책). `anon`·`authenticated` 에는 권한을 주지 않고, `opm_ro` 만 SELECT.
■ 교체 때 krx_wk 에 넣는 행 = 옛 표 행 중 「일별 저장분의 콜드 아닌 주 닫는 날」이 아닌 것 전부. 일별 창 이전 주(3년 + 연말 행)에 더해
    **콜드 보관본에 들어간(`krx_day.cold`) 창 안 주 닫는 날**도 넣는다 — 그 날은 보관 창 정리가 언제든 지울 수 있는 날이라, 옮기지 않는 옛 코드가
    잠깐 돌아도(배포 순서가 바뀌거나 코드를 되돌려도) 주간 행이 사라지지 않게 미리 둔다. 그 날은 뷰가 krx_wk 쪽만 보여 준다(값은 G2 가 대조).
■ 교체 순서와 잠금(261019 검토): 옛 표는 처음부터 SHARE 잠금(읽기는 그대로, 쓰기만 기다림) — 무거운 G2 대조는 **이름을 바꾸기 전에**
    옛 표와 같은 정의의 임시 뷰(`pg_temp._krx_weekly_next`) 사이에서 한다. 읽는 쪽을 막는 ACCESS EXCLUSIVE 는 마지막 이름 바꾸기·뷰 만들기
    (밀리초)에만 걸린다. 마지막에 새 뷰와 임시 뷰의 정의(`pg_get_viewdef`)가 같은지 확인한다.
■ 🔴 메모리(261019 검토, 운영 덤프를 복원한 로컬 PG17 실측): 운영 DB 는 메모리가 작은 인스턴스다. 해시 집합 연산(HashSetOp)은 work_mem 을 넘어도 디스크로
    내리지 않고, 행을 글자로 바꾼 식은 행 수 추정이 200 이라 기본 work_mem 에서도 해시를 고른다 — 종전 G2(work_mem 64MB · 글자 대조 2개)는
    백엔드 하나가 개인 메모리 433MB, 되돌리기 218MB, 옛 표 지우기 101MB 를 썼다. 그래서 대조는 모두 `enable_hashagg = off`(정렬 — work_mem
    을 넘으면 임시 파일)·기본 work_mem·한 검사 한 문장으로 한다(교체 G2 9MB). 글자 대조는 칸 이름·순서·형 대조(`col_types`)로 바꿨다 —
    형이 같고 값이 같으면 글자 표현도 같다.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.krx_daily import REFRESH_LOCK_KEY  # noqa: E402

TABLES = """\
-- krx_wk 의 주 달력: 날짜 두 표기(date·YYYYMMDD)와 그 날 krx_wk 행 수.
CREATE TABLE IF NOT EXISTS krx_wk_day (
  wk_dd date    PRIMARY KEY,
  dd    text    NOT NULL UNIQUE,
  n     integer NOT NULL DEFAULT 0,
  CONSTRAINT krx_wk_day_dd_ck CHECK (dd = (extract(year FROM wk_dd)::int * 10000 + extract(month FROM wk_dd)::int * 100
                                           + extract(day FROM wk_dd)::int)::text)
);
-- 닫힌 주 사실값(한 행 = 종목 × 주 닫는 날). 칸 순서는 정렬 경계 기준(행 본체 21 B). 시총은 뷰가 계산한다.
CREATE TABLE IF NOT EXISTS krx_wk (
  list_shrs bigint,             -- 상장주식수 LIST_SHRS
  close     integer,            -- 종가 TDD_CLSPRC
  wk_dd     date    NOT NULL REFERENCES krx_wk_day (wk_dd),
  tid       integer NOT NULL REFERENCES krx_ticker (tid),
  market    "char"  NOT NULL CONSTRAINT krx_wk_market_ck CHECK (market IN ('S', 'Q')),   -- S=KS(코스피) Q=KQ(코스닥)
  PRIMARY KEY (tid, wk_dd)
);
CREATE INDEX IF NOT EXISTS krx_wk_dd_brin ON krx_wk USING brin (wk_dd) WITH (pages_per_range = 16, autosummarize = on);

ALTER TABLE krx_wk_day ENABLE ROW LEVEL SECURITY;
ALTER TABLE krx_wk     ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS krx_wk_day_read ON krx_wk_day;
CREATE POLICY krx_wk_day_read ON krx_wk_day FOR SELECT USING (true);
DROP POLICY IF EXISTS krx_wk_read ON krx_wk;
CREATE POLICY krx_wk_read ON krx_wk FOR SELECT USING (true);
REVOKE ALL ON krx_wk_day, krx_wk FROM anon, authenticated;
GRANT SELECT ON krx_wk_day, krx_wk TO opm_ro;
-- 서버 전용 계정(opm_app, 「서버 DB 계정 분리」)이 이미 있으면 그 계정이 쓰는 만큼 미리 준다 — 보관 창 정리가 주 닫는 날을 옮긴다
-- (krx_wk INSERT · krx_wk_day INSERT·UPDATE(n)). RLS 를 따르는 계정이라 정책도 둔다. 없으면 아무것도 하지 않는다(계정을 만드는 쪽이 준다).
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'opm_app') THEN
    GRANT SELECT, INSERT ON krx_wk TO opm_app;
    REVOKE UPDATE ON krx_wk_day FROM opm_app;                -- 칸 단위로만(다시 돌려도 표 전체 UPDATE 가 남지 않게)
    GRANT SELECT, INSERT, UPDATE (n) ON krx_wk_day TO opm_app;
    DROP POLICY IF EXISTS krx_wk_app_insert ON krx_wk;
    CREATE POLICY krx_wk_app_insert ON krx_wk FOR INSERT TO opm_app WITH CHECK (true);
    DROP POLICY IF EXISTS krx_wk_day_app_insert ON krx_wk_day;
    CREATE POLICY krx_wk_day_app_insert ON krx_wk_day FOR INSERT TO opm_app WITH CHECK (true);
    DROP POLICY IF EXISTS krx_wk_day_app_update ON krx_wk_day;
    CREATE POLICY krx_wk_day_app_update ON krx_wk_day FOR UPDATE TO opm_app USING (true) WITH CHECK (true);
  END IF;
END $$;
"""

#: 뷰 정의(이름 없이) — 같은 이름 뷰와 교체 G2 의 임시 뷰가 글자 그대로 같은 정의를 쓴다.
VIEW_SELECT = """\
SELECT c.dd AS price_dd, k.ticker,
       CASE w.market WHEN 'S' THEN 'KS' WHEN 'Q' THEN 'KQ' END::text AS market,
       w.close::bigint AS close, w.close::bigint * w.list_shrs AS mktcap, w.list_shrs
FROM krx_wk w
JOIN krx_ticker k ON k.tid = w.tid
JOIN krx_wk_day c ON c.wk_dd = w.wk_dd
UNION ALL
SELECT d.dd AS price_dd, k.ticker,
       CASE p.market WHEN 'S' THEN 'KS' WHEN 'Q' THEN 'KQ' END::text AS market,
       p.close::bigint AS close, p.close::bigint * p.list_shrs AS mktcap, p.list_shrs
FROM krx_px p
JOIN krx_ticker k ON k.tid = p.tid
JOIN krx_day d ON d.trd_dd = p.trd_dd
WHERE d.wk_close AND NOT EXISTS (SELECT 1 FROM krx_wk_day x WHERE x.wk_dd = d.trd_dd)"""

VIEW = """\
-- 옛 표 krx_weekly 와 같은 칸 이름·순서·형. 앞 갈래 = 창보다 이른 닫힌 주(krx_wk), 뒤 갈래 = 일별 저장분의 주 닫는 날 행.
-- 뒤 갈래는 krx_wk_day 에 이미 있는 날을 뺀다(한 날은 한 곳에서만 — 교체 때 미리 옮긴 콜드 날, 옮긴 뒤 아직 안 지운 날).
-- 두 갈래 모두 날짜 조건이 작은 달력(krx_wk_day·krx_day)에서 먼저 걸리고, 종목 조건은 번호표 → 기본키 (tid, 날짜)로 내려간다.
CREATE OR REPLACE VIEW krx_weekly WITH (security_invoker = true) AS
""" + VIEW_SELECT + """;
REVOKE ALL ON krx_weekly FROM anon, authenticated;
GRANT SELECT ON krx_weekly TO opm_ro;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'opm_app') THEN
    GRANT SELECT ON krx_weekly TO opm_app;
  END IF;
END $$;
"""

#: 갱신기(`krx_daily.REFRESH_LOCK_KEY`, 세션 잠금)와 겹치지 않게 트랜잭션 동안 쥔다 — 갱신기는 try 라 그동안 건너뛴다.
_LOCK = f"SELECT pg_advisory_xact_lock({REFRESH_LOCK_KEY});\n"

#: 교체 전 확인 — 이미 뷰면(두 번 돌림) 멈춘다. 일별 스키마(krx_px·krx_day·krx_ticker)가 먼저 있어야 한다.
_SWITCH_PRECHECK = """\
DO $$
BEGIN
  IF (SELECT relkind FROM pg_class WHERE oid = to_regclass('krx_weekly')) IS DISTINCT FROM 'r' THEN
    RAISE EXCEPTION 'krx_weekly 가 표가 아니다 — 이미 교체됐거나 없다(교체하지 않는다)';
  END IF;
  IF to_regclass('krx_px') IS NULL OR to_regclass('krx_day') IS NULL OR to_regclass('krx_ticker') IS NULL THEN
    RAISE EXCEPTION '일별 스키마(scripts/krx_daily_ddl.py)가 먼저 있어야 한다';
  END IF;
END $$;
-- 옛 표 쓰기만 막는다(읽기는 그대로) — 옮겨 담기부터 G2 까지 옛 표가 바뀌지 않게. 갱신기는 위 권고 잠금으로 이미 비켜 있고,
-- 이 잠금은 그 밖의 쓰기(배치의 예비 주간 닫기 등)를 커밋까지 기다리게 한다.
LOCK TABLE krx_weekly IN SHARE MODE;
"""

SWITCH_MIGRATE = """\
-- 다시 교체하는 경우(되돌린 뒤) 옛 내용을 버리고 지금 표에서 새로 만든다. 표 모드에서는 쓰는 쪽이 이 두 표를 쓰지 않는다.
TRUNCATE krx_wk, krx_wk_day;
-- 옮길 행: 옛 표 중 「일별 저장분의 콜드 아닌 주 닫는 날」이 아닌 것 전부(창 이전 주 + 콜드 날 — 위 설명).
DROP TABLE IF EXISTS _wk_mig;
CREATE TEMP TABLE _wk_mig ON COMMIT DROP AS
  SELECT w.price_dd, to_date(w.price_dd, 'YYYYMMDD') AS wk_dd, w.ticker, w.market, w.close, w.list_shrs
  FROM krx_weekly w
  WHERE NOT EXISTS (SELECT 1 FROM krx_day d WHERE d.dd = w.price_dd AND d.wk_close AND d.n > 0 AND NOT d.cold);
-- 옛 종목(일별 창에 없는 상장폐지 종목 등)에 번호를 매긴다. 새 종목만(번호 생성기를 태우지 않게).
INSERT INTO krx_ticker (ticker)
  SELECT DISTINCT m.ticker FROM _wk_mig m
  WHERE NOT EXISTS (SELECT 1 FROM krx_ticker k WHERE k.ticker = m.ticker)
  ORDER BY 1;
INSERT INTO krx_wk_day (wk_dd, dd, n)
  SELECT wk_dd, price_dd, count(*) FROM _wk_mig GROUP BY wk_dd, price_dd ORDER BY wk_dd;
-- 날짜순 → 번호순으로 넣는다(같은 날 행이 물리적으로 모여 BRIN 이 촘촘하다).
INSERT INTO krx_wk (list_shrs, close, wk_dd, tid, market)
  SELECT m.list_shrs, m.close, m.wk_dd, k.tid, CASE m.market WHEN 'KS' THEN 'S' WHEN 'KQ' THEN 'Q' END
  FROM _wk_mig m JOIN krx_ticker k ON k.ticker = m.ticker
  ORDER BY m.wk_dd, k.tid;
SELECT count(*) AS migrated_rows, count(DISTINCT price_dd) AS migrated_days,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM krx_day d WHERE d.dd = _wk_mig.price_dd)) AS cold_window_rows
FROM _wk_mig;
"""

#: 이름 바꾸기 — G2 를 통과한 뒤에만. 여기서부터 커밋까지가 읽는 쪽을 막는 ACCESS EXCLUSIVE 구간이다(이름 바꾸기·뷰 만들기·정의 확인뿐).
SWITCH_RENAME = """\
ALTER TABLE krx_weekly RENAME TO krx_weekly_legacy;
ALTER INDEX IF EXISTS krx_weekly_pkey RENAME TO krx_weekly_legacy_pkey;
ALTER INDEX IF EXISTS idx_krx_weekly_isu RENAME TO idx_krx_weekly_legacy_isu;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_policy WHERE polrelid = 'krx_weekly_legacy'::regclass AND polname = 'krx_weekly_read') THEN
    ALTER POLICY krx_weekly_read ON krx_weekly_legacy RENAME TO krx_weekly_legacy_read;
  END IF;
END $$;
"""

#: 교체 G2 관문 — 옛 표(아직 이름이 krx_weekly)와 **같은 정의의 임시 뷰**가 전 행·전 칸 같아야 이름을 바꾼다. 하나라도 다르면 예외 → 전체 되돌림.
#: 이름을 바꾸기 전에 대조하므로 읽는 쪽은 그동안 옛 표를 그대로 읽는다(옛 표는 SHARE 잠금 — 쓰기만 기다린다).
#: 🔴 메모리: 대조는 정렬로만(`enable_hashagg = off` — 해시 집합 연산은 work_mem 을 넘어도 디스크로 내리지 않는다), 기본 work_mem,
#: 한 검사 한 문장(앞 검사의 정렬 메모리가 다음 검사까지 남지 않게). 글자 대조 대신 칸 이름·순서·형 대조(`col_types`) — 형이 같고
#: 값이 같으면 글자 표현도 같다. 종전(work_mem 64MB·해시·글자 대조)은 로컬 실측 백엔드 개인 메모리 433MB, 지금 9MB.
SWITCH_G2_SQL = """\
SET LOCAL statement_timeout = '600s';
SET LOCAL enable_hashagg = off;
DROP VIEW IF EXISTS pg_temp._krx_weekly_next;
CREATE TEMP VIEW _krx_weekly_next WITH (security_invoker = true) AS
""" + VIEW_SELECT + """;
CREATE TEMP TABLE _g2 (chk text PRIMARY KEY, diff bigint NOT NULL) ON COMMIT DROP;
INSERT INTO _g2 SELECT 'legacy_empty', (SELECT (count(*) = 0)::int FROM krx_weekly);
INSERT INTO _g2 SELECT 'col_types', (SELECT count(*) FROM (
  (SELECT row_number() OVER (ORDER BY attnum) AS pos, attname, atttypid, atttypmod FROM pg_attribute
    WHERE attrelid = 'krx_weekly'::regclass AND attnum > 0 AND NOT attisdropped
   EXCEPT
   SELECT row_number() OVER (ORDER BY attnum), attname, atttypid, atttypmod FROM pg_attribute
    WHERE attrelid = 'pg_temp._krx_weekly_next'::regclass AND attnum > 0 AND NOT attisdropped)
  UNION ALL
  (SELECT row_number() OVER (ORDER BY attnum), attname, atttypid, atttypmod FROM pg_attribute
    WHERE attrelid = 'pg_temp._krx_weekly_next'::regclass AND attnum > 0 AND NOT attisdropped
   EXCEPT
   SELECT row_number() OVER (ORDER BY attnum), attname, atttypid, atttypmod FROM pg_attribute
    WHERE attrelid = 'krx_weekly'::regclass AND attnum > 0 AND NOT attisdropped)) x);
INSERT INTO _g2 SELECT 'l_minus_v', (SELECT count(*) FROM (SELECT * FROM krx_weekly EXCEPT ALL SELECT * FROM pg_temp._krx_weekly_next) x);
INSERT INTO _g2 SELECT 'v_minus_l', (SELECT count(*) FROM (SELECT * FROM pg_temp._krx_weekly_next EXCEPT ALL SELECT * FROM krx_weekly) x);
-- 두 곳에 다 있는 날(교체 때 미리 옮긴 콜드 주 닫는 날): 일별 행 = krx_wk 행이어야 나중에 옮길 때 값이 같다.
INSERT INTO _g2 SELECT 'overlap_wk_minus_px', (SELECT count(*) FROM (
       SELECT w.tid, w.wk_dd, w.close, w.market, w.list_shrs FROM krx_wk w JOIN krx_day d ON d.trd_dd = w.wk_dd AND d.wk_close
       EXCEPT ALL
       SELECT p.tid, p.trd_dd, p.close, p.market, p.list_shrs FROM krx_px p JOIN krx_wk_day c ON c.wk_dd = p.trd_dd
       JOIN krx_day d ON d.trd_dd = p.trd_dd AND d.wk_close) x);
INSERT INTO _g2 SELECT 'overlap_px_minus_wk', (SELECT count(*) FROM (
       SELECT p.tid, p.trd_dd, p.close, p.market, p.list_shrs FROM krx_px p JOIN krx_wk_day c ON c.wk_dd = p.trd_dd
       JOIN krx_day d ON d.trd_dd = p.trd_dd AND d.wk_close
       EXCEPT ALL
       SELECT w.tid, w.wk_dd, w.close, w.market, w.list_shrs FROM krx_wk w JOIN krx_day d ON d.trd_dd = w.wk_dd AND d.wk_close) x);
INSERT INTO _g2 SELECT 'wk_day_n', (SELECT count(*) FROM krx_wk_day c WHERE c.n <> (SELECT count(*) FROM krx_wk w WHERE w.wk_dd = c.wk_dd));
INSERT INTO _g2 SELECT 'wk_close_not_in_legacy', (SELECT count(*) FROM krx_day d WHERE d.wk_close
       AND NOT EXISTS (SELECT 1 FROM krx_weekly l WHERE l.price_dd = d.dd));
SELECT chk, diff FROM _g2 ORDER BY chk;
SELECT (SELECT count(*) FROM krx_weekly) AS legacy_rows, (SELECT count(*) FROM krx_wk) AS wk_rows,
       (SELECT count(*) FROM krx_wk_day) AS wk_days, (SELECT min(dd) FROM krx_wk_day) AS wk_first, (SELECT max(dd) FROM krx_wk_day) AS wk_last,
       (SELECT count(*) FROM krx_day WHERE wk_close) AS daily_wk_close_days;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM _g2 WHERE diff <> 0) THEN
    RAISE EXCEPTION 'G2 관문 실패 — 교체를 되돌린다: %', (SELECT string_agg(chk || '=' || diff, ', ' ORDER BY chk) FROM _g2 WHERE diff <> 0);
  END IF;
  RAISE NOTICE 'G2 관문 통과 — 차이 0';
END $$;
"""

#: 이름을 바꾼 뒤 — 새 같은 이름 뷰가 G2 를 통과한 임시 뷰와 **같은 정의**인지(행을 다시 읽지 않는 확인) → 임시 뷰를 지운다.
SWITCH_FINAL_SQL = """\
DO $$
BEGIN
  IF pg_get_viewdef('krx_weekly'::regclass) IS DISTINCT FROM pg_get_viewdef('pg_temp._krx_weekly_next'::regclass) THEN
    RAISE EXCEPTION '새 뷰 정의가 G2 를 통과한 정의와 다르다 — 교체를 되돌린다';
  END IF;
END $$;
DROP VIEW pg_temp._krx_weekly_next;
"""

#: 되돌리기 — 뷰가 **지금** 보여 주는 행(교체 뒤 새로 닫힌 주·창 밖으로 옮긴 주 포함)을 그대로 옛 표로 되살린다.
#: 옛 표 이름을 되찾은 뒤 다른 행만 고친다(지울 것 지우고, 없는 것 넣고) — 결과 = 뷰의 행 집합(관문이 대조). krx_wk 두 표는 남겨 둔다
#: (표 모드에서는 쓰는 쪽이 쓰지 않는다 — 다시 교체할 때 새로 채운다). 짝이 되는 코드 되돌리기는 필요 없다(쓰는 쪽이 모드를 본다).
ROLLBACK_BODY = """\
DO $$
BEGIN
  IF (SELECT relkind FROM pg_class WHERE oid = to_regclass('krx_weekly')) IS DISTINCT FROM 'v' THEN
    RAISE EXCEPTION 'krx_weekly 가 뷰가 아니다 — 되돌릴 것이 없다';
  END IF;
  IF to_regclass('krx_weekly_legacy') IS NULL THEN
    RAISE EXCEPTION 'krx_weekly_legacy 가 없다 — 옛 표를 이미 지웠다(retired 백업에서 복원)';
  END IF;
END $$;
SET LOCAL statement_timeout = '600s';
SET LOCAL enable_hashagg = off;          -- 🔴 메모리: 대조를 정렬로(교체 G2 와 같은 까닭). 종전 work_mem 64MB 는 로컬 실측 218MB
DROP TABLE IF EXISTS _wk_now;
CREATE TEMP TABLE _wk_now ON COMMIT DROP AS SELECT * FROM krx_weekly;
ANALYZE _wk_now;                         -- 임시 표는 자동 통계가 없다 — 행 수 추정이 틀려 메모리 계획이 어긋나지 않게
DROP VIEW krx_weekly;
ALTER TABLE krx_weekly_legacy RENAME TO krx_weekly;
ALTER INDEX IF EXISTS krx_weekly_legacy_pkey RENAME TO krx_weekly_pkey;
ALTER INDEX IF EXISTS idx_krx_weekly_legacy_isu RENAME TO idx_krx_weekly_isu;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_policy WHERE polrelid = 'krx_weekly'::regclass AND polname = 'krx_weekly_legacy_read') THEN
    ALTER POLICY krx_weekly_legacy_read ON krx_weekly RENAME TO krx_weekly_read;
  END IF;
END $$;
-- 뷰에 없는(또는 값이 다른) 옛 행을 지우고, 표에 없는(또는 값이 다른) 뷰 행을 넣는다.
DELETE FROM krx_weekly t
  WHERE NOT EXISTS (SELECT 1 FROM _wk_now v WHERE v.price_dd = t.price_dd AND v.ticker = t.ticker
                    AND v.market IS NOT DISTINCT FROM t.market AND v.close IS NOT DISTINCT FROM t.close
                    AND v.mktcap IS NOT DISTINCT FROM t.mktcap AND v.list_shrs IS NOT DISTINCT FROM t.list_shrs);
INSERT INTO krx_weekly (price_dd, ticker, market, close, mktcap, list_shrs)
  SELECT v.price_dd, v.ticker, v.market, v.close, v.mktcap, v.list_shrs FROM _wk_now v
  WHERE NOT EXISTS (SELECT 1 FROM krx_weekly t WHERE t.price_dd = v.price_dd AND t.ticker = v.ticker)
  ORDER BY v.price_dd, v.ticker;
CREATE TEMP TABLE _rb (chk text PRIMARY KEY, diff bigint NOT NULL) ON COMMIT DROP;
INSERT INTO _rb SELECT 't_minus_v', (SELECT count(*) FROM (SELECT * FROM krx_weekly EXCEPT ALL SELECT * FROM _wk_now) x);
INSERT INTO _rb SELECT 'v_minus_t', (SELECT count(*) FROM (SELECT * FROM _wk_now EXCEPT ALL SELECT * FROM krx_weekly) x);
SELECT chk, diff FROM _rb ORDER BY chk;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM _rb WHERE diff <> 0) THEN
    RAISE EXCEPTION '되돌리기 대조 실패 — 아무것도 바꾸지 않는다: %', (SELECT string_agg(chk || '=' || diff, ', ' ORDER BY chk) FROM _rb WHERE diff <> 0);
  END IF;
  RAISE NOTICE '되돌리기 대조 통과 — 표 = 되돌리기 직전 뷰';
END $$;
"""

#: 옛 표 지우기 — 옛 표의 행이 **전부** 지금 뷰에 같은 값으로 있을 때만. 지우기 전 retired Parquet 백업(행 수·지문)은 따로 한다(v03 P6).
DROP_LEGACY_BODY = """\
SET LOCAL statement_timeout = '600s';
SET LOCAL enable_hashagg = off;          -- 🔴 메모리: 대조를 정렬로(교체 G2 와 같은 까닭). 종전 work_mem 64MB 는 로컬 실측 101MB
DO $$
DECLARE missing bigint;
BEGIN
  IF (SELECT relkind FROM pg_class WHERE oid = to_regclass('krx_weekly')) IS DISTINCT FROM 'v' THEN
    RAISE EXCEPTION 'krx_weekly 가 뷰가 아니다 — 옛 표를 지우지 않는다';
  END IF;
  SELECT count(*) INTO missing FROM (SELECT * FROM krx_weekly_legacy EXCEPT ALL SELECT * FROM krx_weekly) x;
  IF missing <> 0 THEN
    RAISE EXCEPTION '옛 표 행 % 개가 뷰에 없다 — 지우지 않는다', missing;
  END IF;
  RAISE NOTICE '옛 표 행이 전부 뷰에 있다 — 지운다';
END $$;
DROP TABLE krx_weekly_legacy;
"""


def body(mode: str) -> str:
    """모드별 트랜잭션 본문 — full(새 DB: 표 + 뷰) · switch(운영 교체) · rollback(되돌리기) · drop-legacy(옛 표 지우기)."""
    if mode == "full":
        return TABLES + VIEW
    if mode == "switch":
        return _LOCK + _SWITCH_PRECHECK + TABLES + SWITCH_MIGRATE + SWITCH_G2_SQL + SWITCH_RENAME + VIEW + SWITCH_FINAL_SQL
    if mode == "rollback":
        return _LOCK + ROLLBACK_BODY
    if mode == "drop-legacy":
        return DROP_LEGACY_BODY
    raise ValueError(mode)


def render(mode: str = "full") -> str:
    """사람이 SQL 편집기·psql 에 붙여 넣을 전체 스크립트."""
    return "BEGIN;\nSET LOCAL lock_timeout = '3s';\n\n" + body(mode) + "\nCOMMIT;\n"


def _relkind(con, name: str):
    r = con.execute("SELECT c.relkind FROM pg_class c WHERE c.oid = to_regclass(%s)", (name,)).fetchone()
    return r[0] if r else None


def apply(url: str, mode: str = "full") -> None:
    import psycopg
    with psycopg.connect(url, connect_timeout=20) as con:      # 한 트랜잭션 — 중간에 실패하면 전부 되돌아간다
        con.execute("SET LOCAL lock_timeout = '3s'")
        kind = _relkind(con, "krx_weekly")
        if mode == "full" and kind == "r":
            raise SystemExit("krx_weekly 가 아직 표다 — 운영은 --switch(교체)를 쓴다")
        con.execute(body(mode))
        con.commit()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="DATABASE_URL 의 DB 에 적용 (없으면 SQL 만 출력)")
    ap.add_argument("--no-tx", action="store_true", help="출력할 때 BEGIN/COMMIT 을 붙이지 않는다")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--switch", action="store_true", help="운영 교체(G2 관문 → 옛 표 krx_weekly_legacy, 같은 이름 뷰)")
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
    print(f"krx_weekly 스키마 {mode} 적용 완료.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
