#!/usr/bin/env python3
"""div_declared·div_quarterly 에 시장 칸(`market`) 추가 — 코스닥 확정 배당 원장(alotMatter)을 같은 표에 싣기 위한 DDL. **서버는 DDL 을 실행하지 않는다 — 사람이 한 번 적용한다.**

■ 왜 — 두 표는 코스피 828사만 담았고 `dividend_data` 의 시장·업종 집계·스크리닝·모집단(`n_universe`)이 시장 필터 없이 표 전체를 읽는다.
  코스닥 행을 그냥 올리면 「코스피 전체」 배당총액·모집단이 코스피+코스닥 합으로 바뀐다. 그래서 칸을 먼저 만들고, 코드가 `market = %s`(기본 KOSPI)로 읽는다.
■ 무엇 — `market TEXT NOT NULL DEFAULT 'KOSPI'` 두 칸. 상수 DEFAULT 라 표를 다시 쓰지 않고(PG11+) 기존 행은 전부 KOSPI 로 읽힌다. 기본키는 그대로다(회사 코드는 시장 사이에서 겹치지 않는다).
■ 순서 — 이 DDL 을 먼저 적용하고 코드를 배포한다. 코드가 먼저 나가면 `market` 칸이 없는 표를 읽어 배당 도구가 `db_error` 가 된다.
■ 잠금 — 한 트랜잭션, `lock_timeout = 3s`. 칸 추가는 순식간이다(카탈로그만 바뀐다).
■ 되돌리기 — `ALTER TABLE div_declared DROP COLUMN market; ALTER TABLE div_quarterly DROP COLUMN market;` (코드를 먼저 되돌린다)

  div_market_ddl.py            # SQL 만 출력
  div_market_ddl.py --apply    # DATABASE_URL 의 DB 에 적용 (멱등)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TABLES: tuple[str, ...] = ("div_declared", "div_quarterly")
STATEMENTS: tuple[str, ...] = tuple(
    f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS market TEXT NOT NULL DEFAULT 'KOSPI'" for t in TABLES)


def render() -> str:
    return "BEGIN;\nSET LOCAL lock_timeout = '3s';\n" + "".join(s + ";\n" for s in STATEMENTS) + "COMMIT;\n"


def apply(url: str, out=print) -> dict[str, list[tuple[str, int]]]:
    """칸을 만들고 표마다 `[(market, 행수)]` 를 돌려준다."""
    import psycopg
    result: dict[str, list[tuple[str, int]]] = {}
    with psycopg.connect(url, connect_timeout=20) as con:
        con.execute("SET LOCAL lock_timeout = '3s'")
        for stmt in STATEMENTS:
            con.execute(stmt)
        for t in TABLES:
            result[t] = [(m, int(n)) for m, n in con.execute(f"SELECT market, count(*) FROM {t} GROUP BY market ORDER BY market")]
            out(f"{t}: {', '.join(f'{m} {n:,}행' for m, n in result[t])}")
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
    apply(url)
    print("div_declared·div_quarterly market 칸 적용 완료 (멱등 — 다시 실행해도 같은 결과).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
