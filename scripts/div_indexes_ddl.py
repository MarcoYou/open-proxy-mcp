#!/usr/bin/env python3
"""div_declared 인덱스 둘 — 배당 도구가 호출마다 표 전체를 훑던 것을 없앤다. **서버는 DDL 을 실행하지 않는다 — 사람이 한 번 적용한다(배포 전후 순서는 상관없다 — 인덱스가 없어도 코드는 동작한다).**

261018 신설(Supabase 재설계 4단계 T3). `scripts/krx_daily_ddl.py` 와 같은 관례다: 기본은 SQL 을 화면에 낼 뿐이고, `--apply` 가 `DATABASE_URL` 의 DB 에
적용한다. **전부 멱등**이다(다시 돌려도 같은 결과).

    python3 scripts/div_indexes_ddl.py            # SQL 을 화면에 낸다. 아무것도 실행하지 않는다 — 편집기에 붙여 넣을 땐 문장을 하나씩
    python3 scripts/div_indexes_ddl.py --apply    # DATABASE_URL 의 DB 에 만든다 (자동 커밋 접속, 문장마다 따로 — 아래 「트랜잭션」)

■ 왜 — `div_declared`(확정 배당 원장, 38,183행·1,683쪽)에는 인덱스가 하나도 없다. 그래서
    · `dividend_data` 는 모든 호출을 `SELECT MIN(bsns_year), MAX(bsns_year)` 로 시작하고, 그 한 줄이 표 전체를 읽는다(평균 475ms, 차가운 캐시 1.5초).
    · 회사 시계열 질의 `WHERE corp_code=… AND reprt_code='11011' AND bsns_year BETWEEN …` 은 12행을 얻으려 38,171행을 버린다(데워진 캐시 12ms).
■ 만드는 것
    div_declared_corp_rep_yr  (corp_code, reprt_code, bsns_year)  회사 시계열 질의의 등치·등치·범위 조건 그대로
    div_declared_yr           (bsns_year)                         MIN/MAX(bsns_year) 가 인덱스 양 끝 한 칸씩만 읽는다. 스크리너의 `bsns_year = …` 조건에도 쓰인다
■ 만들지 않는 것 — `div_payment`: 서버의 세 질의(회사별 결의 목록·결정공시 횟수 집계·스크리너의 횟수 조건)가 이미 `div_payment_corp_fy (corp_code, fiscal_year)` 를 쓴다
    (2026-10-18 운영 EXPLAIN). 새로 만들 이유가 없다.
■ 트랜잭션 — `CREATE INDEX CONCURRENTLY` 는 쓰기를 막지 않고 만든다(그동안 도구는 계속 읽는다). 대신 **트랜잭션 안에서는 못 돈다.**
    여러 문장을 한 번에 보내면 하나의 암묵 트랜잭션이 되어 실패하므로, `--apply` 는 자동 커밋 접속으로 문장을 하나씩 보낸다.
    SQL 편집기에 붙여 넣을 때도 **한 문장씩** 실행한다.
■ 끊긴 시도 — 만드는 도중 끊기면 무효(INVALID) 인덱스가 남고, `IF NOT EXISTS` 는 이름만 보고 지나친다. `--apply` 는 무효 인덱스를 찾아 지우고 다시 만든다.
    손으로 할 때:  SELECT indexrelid::regclass, indisvalid FROM pg_index WHERE indexrelid::regclass::text LIKE 'div_declared_%';
                   DROP INDEX CONCURRENTLY IF EXISTS <이름>;
■ 크기 — 합계 약 1.4MB(운영 표를 복제한 로컬 Postgres 17 에 같은 문장을 돌려 잰 값: 1,112 kB + 280 kB). 쓰기는 적재 배치뿐이라 부담이 없다.
■ 되돌리기 —  DROP INDEX CONCURRENTLY IF EXISTS div_declared_corp_rep_yr;  DROP INDEX CONCURRENTLY IF EXISTS div_declared_yr;
■ 261018 7단계 — `scripts/div_keys_ddl.py` 가 기본키 `(corp_code, reprt_code, bsns_year, line_no)` 를 걸고 `div_declared_corp_rep_yr` 를 지운다(앞 세 칸이 같아
    기본키 인덱스가 같은 질의를 맡는다). 그 뒤로 이 스크립트는 기본키가 그 칸들로 시작하면 `div_declared_corp_rep_yr` 를 **다시 만들지 않는다**.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: (인덱스 이름, CREATE 문). **문장마다 따로 실행한다** — CONCURRENTLY 는 트랜잭션(여러 문장을 한 번에 보낸 것 포함) 안에서 못 돈다.
INDEXES: tuple[tuple[str, str], ...] = (
    ("div_declared_corp_rep_yr",
     "CREATE INDEX CONCURRENTLY IF NOT EXISTS div_declared_corp_rep_yr\n"
     "  ON div_declared (corp_code, reprt_code, bsns_year)"),
    ("div_declared_yr",
     "CREATE INDEX CONCURRENTLY IF NOT EXISTS div_declared_yr\n"
     "  ON div_declared (bsns_year)"),
)


#: 기본키가 이 칸들로 시작하면 그 기본키 인덱스가 같은 질의를 맡는다(`scripts/div_keys_ddl.py`, 7단계) — 같은 인덱스를 또 만들지 않는다.
COVERED_BY_PK: dict[str, tuple[str, ...]] = {"div_declared_corp_rep_yr": ("corp_code", "reprt_code", "bsns_year")}


def _pk_covering(con, cols: tuple[str, ...]) -> str | None:
    """div_declared 기본키가 `cols` 로 시작하면 그 이름, 아니면 None."""
    row = con.execute(
        "SELECT k.conname, array_agg(a.attname ORDER BY u.ord) FROM pg_constraint k "
        "CROSS JOIN LATERAL unnest(k.conkey) WITH ORDINALITY u(attnum, ord) "
        "JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = u.attnum "
        "WHERE k.conrelid = to_regclass('public.div_declared') AND k.contype = 'p' GROUP BY k.conname").fetchone()
    if row and tuple(row[1][:len(cols)]) == tuple(cols):
        return row[0]
    return None


def render() -> str:
    """사람이 SQL 편집기에 붙여 넣을 스크립트 — BEGIN/COMMIT 이 없다(CONCURRENTLY 는 트랜잭션 밖에서만 돈다). 문장을 하나씩 실행한다."""
    head = ("-- div_declared 인덱스 (CONCURRENTLY: 쓰기를 막지 않는다). 아래 문장을 **하나씩** 실행한다 — 한꺼번에 보내면 암묵 트랜잭션이 되어 실패한다.\n"
            "-- 중간에 끊겼다면 무효 인덱스가 남는다: SELECT indexrelid::regclass, indisvalid FROM pg_index "
            "WHERE indexrelid::regclass::text LIKE 'div_declared_%';  → DROP INDEX CONCURRENTLY IF EXISTS <이름>; 후 다시.\n"
            "-- 기본키 div_declared_key (corp_code, reprt_code, bsns_year, line_no) 가 이미 있으면(scripts/div_keys_ddl.py) 첫 문장은 건너뛴다 — 기본키가 같은 질의를 맡는다.\n")
    return head + "\n" + "\n\n".join(stmt + ";" for _, stmt in INDEXES) + "\n"


def _state(con, name: str) -> tuple[bool, bool, int] | None:
    """(유효, 준비됨, 바이트). 없으면 None."""
    row = con.execute(
        "SELECT i.indisvalid, i.indisready, pg_relation_size(c.oid) FROM pg_index i "
        "JOIN pg_class c ON c.oid = i.indexrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relname = %s AND n.nspname = 'public'", (name,)).fetchone()
    return None if row is None else (bool(row[0]), bool(row[1]), int(row[2]))


def apply(url: str, out=print) -> list[tuple[str, bool, int]]:
    """인덱스를 만들고 `[(이름, 유효, 바이트)]` 를 돌려준다. 무효가 하나라도 남으면 호출부가 실패로 다룬다.

    🔴 자동 커밋이다(`autocommit=True`) — 트랜잭션을 열면 CONCURRENTLY 가 실패한다. 그래서 `krx_daily_ddl.apply` 처럼
    한 트랜잭션으로 묶어 되돌리는 보호는 없다. 대신 문장이 둘뿐이고 각각 멱등이다."""
    import psycopg
    result: list[tuple[str, bool, int]] = []
    with psycopg.connect(url, autocommit=True, connect_timeout=20) as con:
        for name, stmt in INDEXES:
            pk = _pk_covering(con, COVERED_BY_PK[name]) if name in COVERED_BY_PK else None
            if pk:
                out(f"{name}: 기본키 {pk} 가 같은 칸을 앞에서 덮는다 — 만들지 않음")
                result.append((name, True, 0))
                continue
            st = _state(con, name)
            if st is not None and not (st[0] and st[1]):
                out(f"{name}: 이전 시도가 중간에 끊긴 무효 인덱스 — 지우고 다시 만듭니다")
                con.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            con.execute(stmt)
            st = _state(con, name)
            ok = bool(st and st[0] and st[1])
            result.append((name, ok, st[2] if st else 0))
            out(f"{name}: {'유효' if ok else '무효(!)'} · {(st[2] if st else 0) / 1024:,.0f} kB")
    return result


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
    result = apply(url)
    if not all(ok for _, ok, _ in result):
        print("무효 인덱스가 남았습니다 — 위 안내대로 지우고 다시 실행하세요.", file=sys.stderr)
        return 1
    print("div_declared 인덱스 적용 완료 (멱등 — 다시 실행해도 같은 결과).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
