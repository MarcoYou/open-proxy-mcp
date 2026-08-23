"""과거 `ops_tool_calls.corp_codes` 를 사용자와 떼어 `ops_corp_daily` 로 옮기고 컬럼을 비운다.

**왜**: 같은 행에 `key_hash`·`ts_ns`·`corp_codes` 가 있으면 「이 사용자가 언제 어느 기업을
조사했는지」가 한 줄 쿼리로 나온다. 재무분석가·기관투자자에게 **무엇을 언제 조사했는가는
그 자체가 정보**다 — 회사 이름이 공개라는 사실과 무관하다. `key_hash` 는 익명이 아니라
**가명**이고(같은 사람인지는 안다), DART 키는 실명 등록에 묶여 있다.

그런데 우리가 원한 답(「어느 기업이 많이 조회되나」)은 **누가 봤는지를 몰라도 된다.**
그래서 사용자를 떼고 `(날짜, 기업)` 카운터만 남긴다 — 값어치는 그대로, 부채는 0.
260810 실측: 7,090행 · 1,041개 기업 · 상위 100건대.

**연결을 백업하지 않는다.** 백업하면 지운 의미가 없다. 옮겨진 집계(ops_corp_daily)가
남는 전부이고, 그게 이 작업의 목적이다.

기본 dry-run. 실제 반영은 --apply.
실행: python3 scripts/corp_codes_unlink.py [--apply]
"""
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
import psycopg

KST = timezone(timedelta(hours=9))


def main(apply: bool) -> None:
    con = psycopg.connect(os.environ["DATABASE_URL"])
    con.autocommit = False
    con.execute("CREATE TABLE IF NOT EXISTS ops_corp_daily("
                "log_dd date NOT NULL, corp_code text NOT NULL, "
                "requests int NOT NULL DEFAULT 0, PRIMARY KEY (log_dd, corp_code))")

    rows = con.execute(
        "SELECT ts_ns, corp_codes FROM ops_tool_calls WHERE corp_codes IS NOT NULL").fetchall()
    if not rows:
        print("옮길 행 없음 — 이미 끝났거나 데이터가 없다.")
        con.close()
        return

    agg = defaultdict(int)
    for ts, codes in rows:
        day = datetime.fromtimestamp(ts / 1e9, KST).date()
        for c in (codes or "").split(","):
            if c:
                agg[(day, c)] += 1

    print(f"대상 {len(rows):,}행 → (날짜,기업) 쌍 {len(agg):,}개 · "
          f"기업 {len({c for _, c in agg}):,}개 · mode={'APPLY' if apply else 'DRY-RUN'}")
    top = sorted(((c, n) for (_, c), n in agg.items()), key=lambda x: -x[1])[:5]
    merged = defaultdict(int)
    for (_, c), n in agg.items():
        merged[c] += n
    print("  상위 기업:", ", ".join(f"{c} {n}" for c, n in
                                 sorted(merged.items(), key=lambda x: -x[1])[:5]))

    if not apply:
        print("\n(dry-run — DB 변경 없음. 실제 반영은 --apply)")
        print("  --apply 하면: ops_corp_daily 에 집계 반영 후 ops_tool_calls.corp_codes 를 NULL 로 비움")
        print("  ⚠ 연결(누가 무엇을 봤나)은 **백업하지 않는다** — 그게 이 작업의 목적이다")
        con.close()
        return

    con.cursor().executemany(
        "INSERT INTO ops_corp_daily(log_dd, corp_code, requests) VALUES(%s,%s,%s) "
        "ON CONFLICT (day, corp_code) DO UPDATE SET "
        "requests = ops_corp_daily.requests + EXCLUDED.requests",
        [(d, c, n) for (d, c), n in agg.items()])

    # 집계가 들어간 것을 **확인한 뒤에** 비운다. 순서가 반대면 실패 시 값이 사라진다.
    got = con.execute("SELECT coalesce(sum(requests),0) FROM ops_corp_daily").fetchone()[0]
    want = sum(agg.values())
    if got < want:
        con.rollback()
        print(f"❌ 집계 검증 실패 (ops_corp_daily 합계 {got} < 옮긴 값 {want}) — 아무것도 안 지운다")
        con.close()
        return

    cleared = con.execute(
        "UPDATE ops_tool_calls SET corp_codes = NULL WHERE corp_codes IS NOT NULL").rowcount
    con.commit()
    print(f"\n✓ ops_corp_daily 반영 (합계 {got:,}) · 이벤트 행 {cleared:,}건에서 기업 연결 제거")
    left = con.execute(
        "SELECT count(*) FROM ops_tool_calls WHERE corp_codes IS NOT NULL").fetchone()[0]
    print(f"  잔존 확인: {left}행 → {'OK' if left == 0 else '실패'}")
    con.close()


if __name__ == "__main__":
    main("--apply" in sys.argv)
