"""events(사용량 텔레메트리) 주간 드레인 — Supabase(500MB 무료티어) 압박 완화, DB→별도 private 저장소.

배경(260705): events가 6/29 사용 스파이크 이후 일 3천~7천건 유입, 이 페이스면 연 ~360MB 증가로
krx_weekly(연 22MB)보다 16배 빠르게 무료티어를 채움. 완결된 과거 주(KST 월~일)를 parquet 로 내보내고
DB에서 삭제.

**저장 위치(260705 변경)**: 유저 로그라 메인 레포(open-proxy-mcp)에도 wiki에도 안 두고, 별도 private
레포 `MarcoYou/open-proxy-storage`(usage/ 폴더)에 둔다. 로컬 클론 경로는 기기마다 다를 수 있어
`.env`의 `OPM_STORAGE_REPO`로 지정(미설정 시 `../open-proxy-storage` 기본값 — open-proxy-mcp와 형제
디렉토리로 클론했다고 가정). 이 스크립트는 parquet 만 그 폴더에 쓴다 — **git add/commit/push는 별도 수동**
(또는 향후 자동화 스크립트로 확장).

**저장 형식(260904 변경)**: CSV → **parquet** (`usage/events/{주시작yymmdd}-{주끝mmdd}.parquet`).
사용량이 일 15,000콜로 늘어 CSV 는 주당 ~30MB·연 1.5GB 가 git 에 쌓일 판이었다. parquet(zstd)는
같은 내용이 주당 ~1.5MB. 타입은 **Postgres 스키마에서 그대로 옮긴다** — CSV 로 한 번 내린 뒤
`read_csv(columns=<PG 타입>)` 로 읽어 쓰므로 추론 오류가 없다. 읽을 땐 DuckDB 로
`read_parquet('usage/events/*.parquet', union_by_name=true)` 한 줄이면 전 기간이 한 표다.
(옛 CSV 8개는 `opm_events_all_260622-260904.parquet` 통합본과 events/ 주별 파일로 옮겼다.)

**컬럼은 스키마에서 파생한다 (260810 — 이걸 안 해서 9컬럼이 지워질 뻔했다).**
종전엔 7개를 손으로 적어두고 **행 전체를 DELETE** 했다. 그 사이 컬럼이 260802·260804·260810
세 번 늘었는데(error_kind·response_bytes·doc_*·corp_codes·fetch_*·web_wait_ms) 드레인은 한 번도
안 따라와, 돌리는 순간 **9컬럼이 백업 없이 영구 소멸**하는 상태였다. 다행히 아직 안 돌았다.
이제 `information_schema` 에서 컬럼을 읽으므로 같은 드리프트가 구조적으로 불가능하고,
**지우기 전에 parquet 을 되읽어 행수·컬럼·고유 event_id 를 검증**한다(검증 실패 시 아무것도 안 지우고 중단).

**진행 중인 현재 주(미완결)는 절대 안 건드림** — 완결된 과거 주만 대상.
usage_tracker.py의 코호트 분석(예: "N일차 재방문율")은 raw events가 있어야 하므로, 드레인된 주의
분석은 `usage_tracker.drained_columns()` 가 `usage/events/*.parquet` 를 DB 와 합류시켜 본다(260817·260904).

기본 dry-run(내보내기만, 삭제 안 함). 실제 삭제는 --apply.
실행: python3 scripts/events_drain.py [--apply]
의존: duckdb — dev 그룹(`uv sync`). 서버 런타임 의존성 아님(Dockerfile 은 --no-dev). 이 모듈을
      import 만 하는 쪽(usage_tracker·drain_backlog_check 의 경로·시계 상수)은 duckdb 없이도 되게 지연 import.
"""
import csv
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
import psycopg
from psycopg import sql

KST = timezone(timedelta(hours=9))
STORAGE_REPO = Path(os.getenv("OPM_STORAGE_REPO") or (ROOT.parent / "open-proxy-storage"))
OUT_DIR = STORAGE_REPO / "usage" / "events"

# Postgres data_type → DuckDB 타입. 스키마에서 파생하므로 컬럼이 늘어도 여기만 보면 된다.
_PG2DUCK = {
    "text": "VARCHAR", "character varying": "VARCHAR", "json": "VARCHAR", "jsonb": "VARCHAR",
    "bigint": "BIGINT", "integer": "INTEGER", "smallint": "SMALLINT",
    "boolean": "BOOLEAN", "double precision": "DOUBLE", "real": "FLOAT", "numeric": "DOUBLE",
    "date": "DATE", "timestamp without time zone": "TIMESTAMP", "timestamp with time zone": "TIMESTAMPTZ",
}


def _kst(ts_ns: int) -> datetime:
    return datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).astimezone(KST)


def _week_start(dt: datetime) -> datetime:
    """그 날짜가 속한 ISO주(KST 월요일 00:00) 시작."""
    d = dt.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    return d - timedelta(days=d.isoweekday() - 1)


def _to_ns(dt: datetime) -> int:
    return int(dt.timestamp() * 1e9)


def _table_columns(con) -> list[tuple[str, str]]:
    """테이블의 **실제** (컬럼, PG 타입)을 순서대로 읽는다.

    하드코딩하면 컬럼이 늘 때 조용히 빠지고, 이 스크립트는 그 뒤에 행을 통째로 지운다 —
    즉 드리프트가 곧 **되돌릴 수 없는 데이터 손실**이 된다. 스키마에서 파생시키는 것이
    유일하게 안전한 방식이다."""
    return [(r[0], r[1]) for r in con.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = 'ops_tool_calls' ORDER BY ordinal_position").fetchall()]


def _write_parquet(tmp_csv: Path, fpath: Path, cols: list[tuple[str, str]]) -> None:
    """CSV(전송용 임시) → parquet. 타입은 PG 스키마에서 명시해 넘긴다 — 추론에 맡기면
    빈칸이 많은 정수 컬럼이 DOUBLE 로 굳는다."""
    import duckdb  # 지연 — 상수만 쓰는 import 경로는 duckdb 가 없어도 된다
    duck = duckdb.connect()
    types = ", ".join(f"'{n}': '{_PG2DUCK.get(t, 'VARCHAR')}'" for n, t in cols)
    duck.execute(
        f"COPY (SELECT * FROM read_csv('{tmp_csv.as_posix()}', header=true, "
        f"columns={{{types}}}, nullstr='') ORDER BY ts_ns) "
        f"TO '{fpath.as_posix()}' (FORMAT parquet, COMPRESSION zstd)")
    duck.close()


def _verify_backup(fpath: Path, cols: list[tuple[str, str]], n_rows: int) -> bool:
    """지우기 전에 **쓴 것을 되읽는다.** 「썼다」와 「제대로 썼다」는 다르다 —
    디스크가 차거나 쓰기가 반쯤 끊겨도 예외 없이 파일은 남는다."""
    try:
        import duckdb  # 지연 import (위와 같은 이유)
        duck = duckdb.connect()
        names = [r[0] for r in duck.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{fpath.as_posix()}')").fetchall()]
        n_back, n_ids = duck.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT event_id) FROM read_parquet('{fpath.as_posix()}')").fetchone()
        duck.close()
    except Exception as e:  # duckdb.IOException 등 — 어떤 이유든 못 읽으면 지우지 않는다
        print(f"  ❌ 백업을 되읽지 못했다: {e}")
        return False
    want = [n for n, _t in cols]
    if names != want:
        print(f"  ❌ 컬럼 불일치 — 기대 {len(want)}개, 파일 {len(names)}개")
        return False
    if n_back != n_rows or n_ids != n_rows:
        print(f"  ❌ 행수 불일치 — DB {n_rows}건, 파일 {n_back}건(고유 event_id {n_ids})")
        return False
    return True


def main(apply: bool) -> None:
    if not STORAGE_REPO.is_dir():
        print(f"저장소 폴더 없음: {STORAGE_REPO}\n"
              f"  → git clone https://github.com/MarcoYou/open-proxy-storage.git 를 그 경로에 하거나\n"
              f"    .env에 OPM_STORAGE_REPO=<실제 클론 경로> 를 지정하세요. 중단.")
        return
    con = psycopg.connect(os.environ["DATABASE_URL"]); con.autocommit = True
    row = con.execute("SELECT MIN(ts_ns), MAX(ts_ns) FROM ops_tool_calls").fetchone()
    if not row or row[0] is None:
        print("events 테이블 비어있음 — 드레인 대상 없음"); con.close(); return
    mn, mx = row
    first_week = _week_start(_kst(mn))
    now_week = _week_start(datetime.now(tz=KST))  # 현재 진행 중인 주(미완결) — 절대 대상 아님

    cols = _table_columns(con)
    if not cols:
        print("컬럼 목록을 못 읽었다 — 백업이 온전한지 보장할 수 없으므로 중단."); con.close(); return

    print(f"events 범위: {_kst(mn)} ~ {_kst(mx)} · mode={'APPLY' if apply else 'DRY-RUN'}")
    print(f"백업 컬럼 {len(cols)}개(스키마 파생): {', '.join(n for n, _t in cols)}")
    if apply:
        # 지운 행은 CSV 에만 남는다. 지금 통계·덱은 **DB 만** 읽으므로, 드레인한 구간의
        # first_seen·코호트는 그 시점부터 보이지 않는다(장기 사용자가 「신규」로 재라벨된다).
        # 이건 컬럼 손실과 달리 복구는 되지만, 모르고 돌리면 지표가 조용히 틀어진다.
        print("⚠️  드레인한 주는 DB 에서 사라진다 — usage_tracker·트랙션덱은 DB 만 읽으므로\n"
              "    그 구간의 first_seen·코호트가 안 보이게 된다(parquet 로는 남는다).")
    print()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    w = first_week
    drained_total = 0
    while w < now_week:
        w_end = w + timedelta(days=7)
        rows = con.execute(
            sql.SQL("SELECT {} FROM ops_tool_calls "
                    "WHERE ts_ns >= %s AND ts_ns < %s ORDER BY ts_ns").format(
                sql.SQL(", ").join(sql.Identifier(n) for n, _t in cols)),
            (_to_ns(w), _to_ns(w_end))
        ).fetchall()
        if not rows:
            w = w_end; continue
        stem = f"{w.strftime('%y%m%d')}-{(w_end - timedelta(days=1)).strftime('%m%d')}"
        fpath = OUT_DIR / f"{stem}.parquet"
        tmp_csv = OUT_DIR / f".{stem}.tmp.csv"
        with open(tmp_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([n for n, _t in cols])
            writer.writerows(rows)
        try:
            _write_parquet(tmp_csv, fpath, cols)
        finally:
            tmp_csv.unlink(missing_ok=True)
        print(f"[{w.date()}~{(w_end - timedelta(days=1)).date()}] {len(rows)}건 "
              f"× {len(cols)}컬럼 → events/{fpath.name} ({fpath.stat().st_size / 1048576:.2f} MB)")
        if not _verify_backup(fpath, cols, len(rows)):
            print("  → 아무것도 지우지 않고 중단한다.")
            con.close()
            return
        if apply:
            ids = [r[0] for r in rows]
            deleted = con.execute("DELETE FROM ops_tool_calls WHERE event_id = ANY(%s)", (ids,)).rowcount
            print(f"  ✓ DB에서 {deleted}건 삭제")
        drained_total += len(rows)
        w = w_end

    remaining_current_week = con.execute(
        "SELECT COUNT(*) FROM ops_tool_calls WHERE ts_ns >= %s", (_to_ns(now_week),)).fetchone()[0]
    print(f"\n총 {'드레인' if apply else '드레인 대상'} {drained_total}건 · "
          f"진행 중 주({now_week.date()}~)는 안 건드림, {remaining_current_week}건 유지")
    if not apply:
        print("(dry-run — DB 변경 없음. 실제 삭제하려면 --apply)")
    con.close()


if __name__ == "__main__":
    main("--apply" in sys.argv)
