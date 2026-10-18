#!/usr/bin/env python3
"""배당 원장 5표에 기본키 — 적재 배치가 「바뀐 행만」 맞출 때 기준으로 삼는 키이자 중복 방지. **서버는 DDL 을 실행하지 않는다 — 사람이 한 번 적용한다.**

261018 신설(Supabase 재설계 v02 7단계). `scripts/div_indexes_ddl.py` 와 같은 관례다: 기본은 SQL 을 화면에 낼 뿐이고, `--apply` 가 `DATABASE_URL` 의 DB 에
적용한다. `--check` 는 같은 DB 를 **읽기만** 해서 무엇을 하게 될지(이미 됐는지·막히는지)를 보인다. **전부 멱등**이다.

    python3 scripts/div_keys_ddl.py            # SQL 을 화면에 낸다. 아무것도 실행하지 않는다
    python3 scripts/div_keys_ddl.py --check    # DATABASE_URL 을 읽기 전용 세션으로 읽고 표마다 상태만 보인다
    python3 scripts/div_keys_ddl.py --apply    # 표마다 한 트랜잭션으로 적용 (잠금 대기 3초 — 못 잡으면 그 표만 건너뛰고 실패로 알린다)

■ 왜 — `div_declared`·`div_quarterly`·`div_payment`·`div_payment_scope`·`div_yield_hist` 다섯 표에 기본키가 없다(2026-10-18 운영).
    적재 배치는 사업연도 단위로 지우고 전부 다시 넣어 왔다 — 로컬 원본이 모든 연도를 가지므로 사실상 매번 표 전체를 다시 썼고,
    `div_declared` 는 한 번 다시 쓸 때마다 표 크기만큼 빈 공간이 남았다(운영 heap 13.2 MiB 중 약 6.6 MiB). 「바뀐 행만」 맞추려면 행을 가리킬 키가 있어야 한다.
■ 넷은 키가 이미 있다 — 같은 칸의 UNIQUE 인덱스(`<표>_key`)가 있고 운영에서 키 칸의 NULL 0건. 그 인덱스를 **그대로** 기본키로 올린다
    (`ADD CONSTRAINT … PRIMARY KEY USING INDEX`): 새 인덱스 0, 표 다시 쓰기 없음 — 키 칸에 NOT NULL 을 다는 확인 훑기만 한다.
      div_quarterly      (corp_code, bsns_year, reprt_code)
      div_payment        (rcept_no)
      div_payment_scope  (fiscal_year, market)
      div_yield_hist     (market, scheme, bucket, fiscal_year, denom_basis)
    제약 이름은 인덱스 이름과 같게 둔다(`<표>_key`). `_pkey` 로 바꾸면 인덱스 이름도 바뀌어, 인덱스를 `IF NOT EXISTS` 로 만드는 옛 적재 코드가 같은 인덱스를 하나 더 만든다.
■ `div_declared` 는 자연키가 없다. 원문(정기보고서 「배당에 관한 사항」)이 한 보고서 안에 **똑같은 줄을 여러 번** 싣는다 — 운영 4,039묶음·4,045행이
    모든 칸이 같다(대부분 무배당 줄). 원문 그대로 싣는 것이 이 표의 원칙이라 지우지 않는다. 그래서 「그 보고서 안 몇 번째 줄인가」 `line_no`(1부터)를 더하고
    키를 `(corp_code, reprt_code, bsns_year, line_no)` 로 한다. 앞 세 칸이 4단계 인덱스 `div_declared_corp_rep_yr` 와 같은 순서라 회사 시계열 질의는
    기본키 인덱스를 그대로 쓰고, 그 인덱스는 지운다(`div_indexes_ddl.py` 도 기본키가 덮고 있으면 다시 만들지 않는다).
    · 비어 있는 `line_no` 는 지금 저장 순서(ctid)로 1, 2, 3… 을 채운다. 2026-10-18 운영(읽기)·원문 다시 만들기 대조: 행 묶음은 38,183행 전부 같고,
      저장 순서가 원문 줄 순서와 엇갈린 곳은 30행(같은 보고서 안 두 줄 자리바꿈)뿐이다 — 다음 적재가 그 30행만 고친다. 어긋나도 결과는 원문과 같다.
    · 채우기는 전 행 UPDATE 라 죽은 행이 표 크기만큼 생긴다 → 7단계 `VACUUM FULL div_declared` 를 **이 뒤에** 한다.
    · 이 뒤로는 `line_no` 없이 넣는 옛 적재(전량 지우고 다시 넣기)가 NOT NULL 위반으로 **실패한다(의도)** — 키로 맞추는 적재로 바꾼 뒤에 돌린다.
■ 잠금 — 표마다 한 트랜잭션, `lock_timeout = 3s`. 넷은 순식간이다. `div_declared`(38,183행)는 채우기·인덱스 만들기 동안(수 초 안쪽) 그 표 읽기가 기다린다.
■ 되돌리기 — `ALTER TABLE <표> DROP CONSTRAINT <표>_key` 는 인덱스까지 지운다. 넷은 같은 UNIQUE 인덱스를 다시 만든다(`CREATE UNIQUE INDEX <표>_key ON <표> (…)`).
    div_declared: `ALTER TABLE div_declared DROP CONSTRAINT div_declared_key;` → `python3 scripts/div_indexes_ddl.py --apply` → `ALTER TABLE div_declared DROP COLUMN line_no;`
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LOCK_TIMEOUT = "3s"

#: (표, UNIQUE 인덱스 = 올릴 기본키 이름, 키 칸) — 칸 순서는 운영 인덱스 정의 그대로(2026-10-18 `pg_get_indexdef`).
PROMOTE: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("div_quarterly", "div_quarterly_key", ("corp_code", "bsns_year", "reprt_code")),
    ("div_payment", "div_payment_key", ("rcept_no",)),
    ("div_payment_scope", "div_payment_scope_key", ("fiscal_year", "market")),
    ("div_yield_hist", "div_yield_hist_key", ("market", "scheme", "bucket", "fiscal_year", "denom_basis")),
)

DECLARED = "div_declared"
DECLARED_PK = "div_declared_key"
DECLARED_KEY: tuple[str, ...] = ("corp_code", "reprt_code", "bsns_year", "line_no")
#: 기본키 앞 세 칸과 같은 칸·같은 순서라 기본키가 대신하는 4단계 인덱스.
SUPERSEDED_INDEX = "div_declared_corp_rep_yr"

_GROUP = "corp_code, reprt_code, bsns_year"
#: 비어 있는 줄 번호를 지금 저장 순서로 채운다 — 처음 한 번(전부 비어 있을 때)만 쓴다.
BACKFILL = (f"WITH s AS (SELECT ctid AS c, row_number() OVER (PARTITION BY {_GROUP} ORDER BY ctid) AS rn FROM {DECLARED})\n"
            f"UPDATE {DECLARED} d SET line_no = s.rn FROM s WHERE d.ctid = s.c AND d.line_no IS NULL")


def _promote_sql(table: str, index: str) -> str:
    return f"ALTER TABLE {table} ADD CONSTRAINT {index} PRIMARY KEY USING INDEX {index}"


def render() -> str:
    """사람이 SQL 편집기에 붙여 넣을 스크립트 — 표마다 BEGIN/COMMIT 한 덩어리."""
    parts = ["-- 배당 원장 5표 기본키. 덩어리(BEGIN~COMMIT)마다 따로 실행한다. 이미 기본키가 있는 표의 덩어리는 오류가 나므로 건너뛴다(--check 로 먼저 본다).\n"]
    for table, index, cols in PROMOTE:
        parts.append(f"-- {table}: 이미 있는 UNIQUE 인덱스 {index} ({', '.join(cols)}) 를 그대로 기본키로 (새 인덱스 없음)\n"
                     f"BEGIN;\nSET LOCAL lock_timeout = '{LOCK_TIMEOUT}';\n{_promote_sql(table, index)};\nCOMMIT;\n")
    parts.append(
        f"-- {DECLARED}: 보고서 안 줄 번호 line_no 를 더하고 (corp_code, reprt_code, bsns_year, line_no) 를 기본키로.\n"
        f"-- 🔴 UPDATE 는 line_no 가 **전부** 비어 있을 때(처음 한 번)만 돌린다. 일부만 비어 있으면 멈추고 원인을 본다(--check).\n"
        f"BEGIN;\nSET LOCAL lock_timeout = '{LOCK_TIMEOUT}';\n"
        f"ALTER TABLE {DECLARED} ADD COLUMN IF NOT EXISTS line_no smallint;\n"
        f"{BACKFILL};\n"
        f"ALTER TABLE {DECLARED} ADD CONSTRAINT {DECLARED_PK} PRIMARY KEY ({', '.join(DECLARED_KEY)});\n"
        f"DROP INDEX IF EXISTS {SUPERSEDED_INDEX};\nCOMMIT;\n"
        f"-- 이 뒤에 7단계 VACUUM FULL {DECLARED} (채우기가 남긴 죽은 행 회수).\n")
    return "\n".join(parts)


# ── 상태 읽기 (읽기만) ───────────────────────────────────────────────────────────────────────────────

def _exists(con, table: str) -> bool:
    return con.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",)).fetchone()[0]


def _pk(con, table: str) -> tuple[str, tuple[str, ...]] | None:
    """(기본키 이름, 칸 순서). 없으면 None."""
    row = con.execute(
        "SELECT k.conname, array_agg(a.attname ORDER BY u.ord) FROM pg_constraint k "
        "CROSS JOIN LATERAL unnest(k.conkey) WITH ORDINALITY u(attnum, ord) "
        "JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = u.attnum "
        "WHERE k.conrelid = to_regclass(%s) AND k.contype = 'p' GROUP BY k.conname",
        (f"public.{table}",)).fetchone()
    return None if row is None else (row[0], tuple(row[1]))


def _unique_index_cols(con, index: str) -> tuple[str, ...] | None:
    """유효한 UNIQUE·일반(식·부분 아님) 인덱스면 칸 순서, 아니면 None."""
    row = con.execute(
        "SELECT i.indisunique AND i.indisvalid AND i.indpred IS NULL AND i.indexprs IS NULL, "
        "array_agg(a.attname ORDER BY u.ord) FROM pg_index i "
        "CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY u(attnum, ord) "
        "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = u.attnum "
        "WHERE i.indexrelid = to_regclass(%s) GROUP BY i.indisunique, i.indisvalid, i.indpred IS NULL, i.indexprs IS NULL",
        (f"public.{index}",)).fetchone()
    return tuple(row[1]) if row and row[0] else None


def _nulls(con, table: str, cols: tuple[str, ...]) -> int:
    cond = " OR ".join(f"{c} IS NULL" for c in cols)
    return con.execute(f"SELECT count(*) FROM {table} WHERE {cond}").fetchone()[0]


def _dup_groups(con, table: str, cols: tuple[str, ...]) -> int:
    return con.execute(f"SELECT count(*) FROM (SELECT 1 FROM {table} GROUP BY {', '.join(cols)} HAVING count(*) > 1) g").fetchone()[0]


def _has_column(con, table: str, col: str) -> bool:
    return con.execute("SELECT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = to_regclass(%s) AND attname = %s "
                       "AND attnum > 0 AND NOT attisdropped)", (f"public.{table}", col)).fetchone()[0]


def plan_promote(con, table: str, index: str, cols: tuple[str, ...]) -> tuple[str, str]:
    """(상태, 설명). 상태: done · todo · blocked."""
    if not _exists(con, table):
        return "blocked", "표가 없다"
    pk = _pk(con, table)
    if pk is not None:
        return ("done", f"기본키 {pk[0]} {pk[1]} 이미 있음") if pk[1] == cols else \
               ("blocked", f"다른 칸의 기본키 {pk[0]} {pk[1]} 가 있다 — 손대지 않는다")
    have = _unique_index_cols(con, index)
    if have != cols:
        return "blocked", f"UNIQUE 인덱스 {index} {cols} 가 없거나 모양이 다르다(지금 {have}) — 적재 쪽 인덱스부터 확인"
    n = _nulls(con, table, cols)
    if n:
        return "blocked", f"키 칸 NULL {n}행 — 기본키로 못 올린다(먼저 그 행을 본다)"
    return "todo", f"UNIQUE 인덱스 {index} 를 기본키로 올림 (새 인덱스 없음)"


def plan_declared(con) -> tuple[str, str]:
    if not _exists(con, DECLARED):
        return "blocked", "표가 없다"
    pk = _pk(con, DECLARED)
    if pk is not None:
        if pk[1] != DECLARED_KEY:
            return "blocked", f"다른 칸의 기본키 {pk[0]} {pk[1]} 가 있다 — 손대지 않는다"
        left = _exists(con, SUPERSEDED_INDEX)
        return ("todo", f"기본키 있음 · 대신하는 인덱스 {SUPERSEDED_INDEX} 만 지움") if left else \
               ("done", f"기본키 {pk[0]} {pk[1]} 이미 있음")
    n = con.execute(f"SELECT count(*) FROM {DECLARED}").fetchone()[0]
    if not _has_column(con, DECLARED, "line_no"):
        return "todo", f"line_no 칸 추가 → {n:,}행 줄 번호 채움(저장 순서) → 기본키 → {SUPERSEDED_INDEX} 지움"
    empty = con.execute(f"SELECT count(*) FROM {DECLARED} WHERE line_no IS NULL").fetchone()[0]
    if 0 < empty < n:
        return "blocked", f"line_no 가 일부만 비어 있다({empty:,}/{n:,}) — 채우는 순서가 섞일 수 있어 멈춘다(누가 line_no 없이 넣었나 확인)"
    if empty == 0 and n:
        d = _dup_groups(con, DECLARED, DECLARED_KEY)
        if d:
            return "blocked", f"line_no 가 다 찼는데 키 중복 {d}묶음 — 기본키로 못 올린다"
        return "todo", f"줄 번호 이미 참({n:,}행) → 기본키 → {SUPERSEDED_INDEX} 지움"
    return "todo", f"줄 번호 채움 {n:,}행(저장 순서) → 기본키 → {SUPERSEDED_INDEX} 지움"


def check(con, out=print) -> list[tuple[str, str, str]]:
    result = []
    for table, index, cols in PROMOTE:
        st, why = plan_promote(con, table, index, cols)
        result.append((table, st, why))
    result.append((DECLARED, *plan_declared(con)))
    for table, st, why in result:
        out(f"{table:<18} {st:<7} {why}")
    return result


# ── 적용 ────────────────────────────────────────────────────────────────────────────────────────────

def _apply_declared(con, out) -> None:
    con.execute(f"ALTER TABLE {DECLARED} ADD COLUMN IF NOT EXISTS line_no smallint")
    n, empty = con.execute(f"SELECT count(*), count(*) FILTER (WHERE line_no IS NULL) FROM {DECLARED}").fetchone()
    if 0 < empty < n:
        raise RuntimeError(f"line_no 가 일부만 비어 있다({empty:,}/{n:,}) — 멈춘다")
    if empty:
        filled = con.execute(BACKFILL).rowcount
        out(f"{DECLARED}: line_no {filled:,}행 채움(저장 순서)")
    if _pk(con, DECLARED) is None:
        con.execute(f"ALTER TABLE {DECLARED} ADD CONSTRAINT {DECLARED_PK} PRIMARY KEY ({', '.join(DECLARED_KEY)})")
    con.execute(f"DROP INDEX IF EXISTS {SUPERSEDED_INDEX}")


def apply(url: str, out=print) -> list[tuple[str, bool, str]]:
    """표마다 한 트랜잭션. `[(표, 성공, 설명)]` — 한 표가 실패해도 나머지는 따로 한다(서로 묶이지 않는다)."""
    import psycopg
    result: list[tuple[str, bool, str]] = []
    with psycopg.connect(url, connect_timeout=20) as con:
        jobs = [(t, (lambda t=t, i=i, c=c: plan_promote(con, t, i, c)),
                 (lambda t=t, i=i: con.execute(_promote_sql(t, i)))) for t, i, c in PROMOTE]
        jobs.append((DECLARED, lambda: plan_declared(con), lambda: _apply_declared(con, out)))
        for table, plan, do in jobs:
            try:
                with con.transaction():
                    con.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
                    st, why = plan()
                    if st == "done":
                        result.append((table, True, why))
                    elif st == "blocked":
                        result.append((table, False, why))
                    else:
                        do()
                        pk = _pk(con, table)
                        result.append((table, True, f"기본키 {pk[0]} {pk[1]}" if pk else "?"))
            except psycopg.Error as exc:
                result.append((table, False, f"{type(exc).__name__} (SQLSTATE {exc.sqlstate}) — 이 표는 그대로"))
            except RuntimeError as exc:
                result.append((table, False, str(exc)))
            out(f"{table:<18} {'✓' if result[-1][1] else '✗'} {result[-1][2]}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true", help="DATABASE_URL 의 DB 에 적용 (없으면 SQL 만 출력)")
    g.add_argument("--check", action="store_true", help="DATABASE_URL 을 읽기 전용 세션으로 읽고 표마다 상태만 보인다")
    args = ap.parse_args()
    if not (args.apply or args.check):
        sys.stdout.write(render())
        return 0
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL 이 없습니다 — 아무것도 하지 않았습니다.", file=sys.stderr)
        return 2
    if args.check:
        import psycopg
        with psycopg.connect(url, autocommit=True, connect_timeout=20) as con:
            # 풀러 경유면 접속 옵션이 버려진다 — 접속 뒤 SET 하고 show 로 확인한다.
            con.execute("SET default_transaction_read_only = on")
            if con.execute("SHOW default_transaction_read_only").fetchone()[0] != "on":
                print("읽기 전용 세션을 만들지 못했습니다 — 멈춥니다.", file=sys.stderr)
                return 2
            res = check(con)
        return 0 if all(st != "blocked" for _, st, _ in res) else 1
    res = apply(url)
    if not all(ok for _, ok, _ in res):
        print("적용하지 못한 표가 있습니다 — 위 설명을 보고 다시 실행하세요(멱등).", file=sys.stderr)
        return 1
    print("배당 원장 기본키 적용 완료 (멱등 — 다시 실행해도 같은 결과). 다음: VACUUM FULL div_declared.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
