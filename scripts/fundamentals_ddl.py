#!/usr/bin/env python3
"""재무 요약 표(`dart_fundamentals`)에 결산월 칸 `acc_mt` — 분기·연간 재무 칸을 실제 기간 말로 읽는 기준. **서버는 DDL 을 실행하지 않는다 — 사람이 한 번 적용한다.**

261018 신설(Supabase 재설계 v02 6단계). `scripts/div_keys_ddl.py` 와 같은 관례다: 기본은 SQL 을 화면에 낼 뿐이고, `--apply` 가 `DATABASE_URL` 의 DB 에
적용한다. `--check` 는 같은 DB 를 **읽기만** 해서 무엇을 하게 될지 보인다. **멱등**이다.

    python3 scripts/fundamentals_ddl.py            # SQL 을 화면에 낸다. 아무것도 실행하지 않는다
    python3 scripts/fundamentals_ddl.py --check    # DATABASE_URL 을 읽기 전용 세션으로 읽고 상태만 보인다
    python3 scripts/fundamentals_ddl.py --apply    # 한 트랜잭션으로 적용 (잠금 대기 3초 — 못 잡으면 아무것도 바꾸지 않고 실패로 알린다)

■ 왜 — DART 는 분기·연간 재무 칸의 사업연도를 **보고서 기준일이 속한 달력 연도**로 붙인다(`open_proxy_mcp/services/report_calendar.py`).
    12월 결산이 아니면 같은 사업연도 표기 안의 네 칸이 서로 다른 회계연도·달력 분기를 가리킨다(3월 결산: 1분기 = 6월 말 · 사업보고서 = 3월 말).
    읽는 쪽(밸류에이션 곡선·업종 비교·재무 요약 파생·수집 마감)이 칸을 실제 기간 말로 바꾸려면 회사 결산월이 있어야 한다.
■ 칸 — `acc_mt smallint`, 1~12 확인 제약(`dart_fundamentals_acc_mt_check`). 비어 있으면 읽는 쪽이 12월로 본다(종전과 같은 읽기) —
    그래서 칸을 만든 직후·채우기 전에도 응답은 그대로다. 채우기는 `python3 scripts/market_fund_quarterly.py --acc-mt --acc-mt-since 20180101`.
■ 순서 — **서버 배포 전에** 적용한다. 새 서버 코드는 재무 요약을 읽을 때 이 칸을 함께 고른다(칸이 없으면 그 읽기가 실패한다).
    배치 스크립트는 칸이 없으면 12월 결산으로 읽고 넘어간다.
■ 잠금 — `ADD COLUMN`(기본값 없음)은 표 다시 쓰기가 없고, 확인 제약은 표(약 2,650행)를 한 번 훑는다. 둘 다 순식간이다. `lock_timeout = 3s`.
■ 되돌리기 — `ALTER TABLE dart_fundamentals DROP COLUMN acc_mt;`(제약도 함께 지워진다). 그 전에 이 칸을 읽는 서버 코드를 먼저 되돌린다.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LOCK_TIMEOUT = "3s"
TABLE = "dart_fundamentals"
COLUMN = "acc_mt"
CHECK = "dart_fundamentals_acc_mt_check"          # PostgreSQL 이 CREATE TABLE … CHECK 에 붙이는 기본 이름과 같다(market_fund_quarterly.DDL_FUNDAMENTALS)

ADD_COLUMN = f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS {COLUMN} smallint"
ADD_CHECK = f"ALTER TABLE {TABLE} ADD CONSTRAINT {CHECK} CHECK ({COLUMN} BETWEEN 1 AND 12)"


def render() -> str:
    """사람이 SQL 편집기에 붙여 넣을 스크립트 — 한 덩어리."""
    return (f"-- {TABLE}.{COLUMN}: 회사 결산월(1~12). 비어 있으면 읽는 쪽이 12월로 본다.\n"
            f"BEGIN;\nSET LOCAL lock_timeout = '{LOCK_TIMEOUT}';\n{ADD_COLUMN};\n"
            f"DO $$ BEGIN\n"
            f"  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.{TABLE}'::regclass AND conname = '{CHECK}') THEN\n"
            f"    {ADD_CHECK};\n  END IF;\nEND $$;\nCOMMIT;\n"
            f"-- 채우기: python3 scripts/market_fund_quarterly.py --acc-mt --acc-mt-since 20180101\n")


def _has_column(con) -> bool:
    return con.execute("SELECT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = to_regclass(%s) AND attname = %s "
                       "AND attnum > 0 AND NOT attisdropped)", (f"public.{TABLE}", COLUMN)).fetchone()[0]


def _has_check(con) -> bool:
    return con.execute("SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = to_regclass(%s) AND conname = %s)",
                       (f"public.{TABLE}", CHECK)).fetchone()[0]


def check(con, out=print) -> tuple[str, str]:
    """(상태, 설명). 상태: done · todo · blocked."""
    if not con.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{TABLE}",)).fetchone()[0]:
        st = ("blocked", "표가 없다")
    elif _has_column(con) and _has_check(con):
        n, filled = con.execute(f"SELECT count(*), count({COLUMN}) FROM {TABLE}").fetchone()
        st = ("done", f"칸·확인 제약 있음 · 채운 행 {filled:,}/{n:,}")
    elif _has_column(con):
        bad = con.execute(f"SELECT count(*) FROM {TABLE} WHERE {COLUMN} NOT BETWEEN 1 AND 12").fetchone()[0]
        st = ("blocked", f"1~12 밖 값 {bad}행 — 확인 제약을 못 단다") if bad else ("todo", "칸 있음 · 확인 제약 추가")
    else:
        st = ("todo", "칸 추가(기본값 없음 — 표 다시 쓰기 없음) · 확인 제약 추가")
    out(f"{TABLE}.{COLUMN:<8} {st[0]:<7} {st[1]}")
    return st


def apply(url: str, out=print) -> tuple[bool, str]:
    """한 트랜잭션. 잠금 대기 3초를 넘기면 아무것도 바꾸지 않고 실패."""
    import psycopg
    try:
        with psycopg.connect(url, connect_timeout=20) as con:
            with con.transaction():
                con.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
                st, why = check(con, out=lambda *_: None)
                if st == "blocked":
                    return False, why
                if st == "todo":
                    con.execute(ADD_COLUMN)
                    if not _has_check(con):
                        con.execute(ADD_CHECK)
            ok = _has_column(con) and _has_check(con)
            msg = "칸·확인 제약 있음" if ok else "확인 실패 — 칸 또는 제약이 없다"
    except psycopg.Error as exc:
        return False, f"{type(exc).__name__} (SQLSTATE {exc.sqlstate}) — 바꾼 것 없음"
    out(f"{TABLE}.{COLUMN} {'✓' if ok else '✗'} {msg}")
    return ok, msg


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true", help="DATABASE_URL 의 DB 에 적용 (없으면 SQL 만 출력)")
    g.add_argument("--check", action="store_true", help="DATABASE_URL 을 읽기 전용 세션으로 읽고 상태만 보인다")
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
            st, _ = check(con)
        return 0 if st != "blocked" else 1
    ok, msg = apply(url)
    if not ok:
        print(f"적용하지 못했습니다: {msg} — 다시 실행하세요(멱등).", file=sys.stderr)
        return 1
    print("결산월 칸 적용 완료 (멱등). 다음: python3 scripts/market_fund_quarterly.py --acc-mt --acc-mt-since 20180101")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
