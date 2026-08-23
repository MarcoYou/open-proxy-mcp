"""events(사용량 텔레메트리) 주간 드레인 — Supabase(500MB 무료티어) 압박 완화, DB→별도 private 저장소.

배경(260705): events가 6/29 사용 스파이크 이후 일 3천~7천건 유입, 이 페이스면 연 ~360MB 증가로
krx_weekly(연 22MB)보다 16배 빠르게 무료티어를 채움. 완결된 과거 주(KST 월~일)를 CSV로 내보내고
DB에서 삭제.

**저장 위치(260705 변경)**: 유저 로그라 메인 레포(open-proxy-mcp)에도 wiki에도 안 두고, 별도 private
레포 `MarcoYou/open-proxy-storage`(usage/ 폴더)에 둔다. 로컬 클론 경로는 기기마다 다를 수 있어
`.env`의 `OPM_STORAGE_REPO`로 지정(미설정 시 `../open-proxy-storage` 기본값 — open-proxy-mcp와 형제
디렉토리로 클론했다고 가정). 이 스크립트는 CSV만 그 폴더에 쓴다 — **git add/commit/push는 별도 수동**
(또는 향후 자동화 스크립트로 확장).

파일명: {주시작yymmdd}-{주끝mmdd}_user_log.csv (예: 260629-0705_user_log.csv)

**컬럼은 스키마에서 파생한다 (260810 — 이걸 안 해서 9컬럼이 지워질 뻔했다).**
종전엔 7개를 손으로 적어두고 **행 전체를 DELETE** 했다. 그 사이 컬럼이 260802·260804·260810
세 번 늘었는데(error_kind·response_bytes·doc_*·corp_codes·fetch_*·web_wait_ms) 드레인은 한 번도
안 따라와, 돌리는 순간 **9컬럼이 백업 없이 영구 소멸**하는 상태였다. 다행히 아직 안 돌았다.
이제 `information_schema` 에서 컬럼을 읽으므로 같은 드리프트가 구조적으로 불가능하고,
**지우기 전에 CSV 를 되읽어 행수·헤더를 검증**한다(검증 실패 시 아무것도 안 지우고 중단).

**진행 중인 현재 주(미완결)는 절대 안 건드림** — 완결된 과거 주만 대상.
usage_tracker.py의 코호트 분석(예: "N일차 재방문율")은 raw events가 있어야 하므로, 드레인된 주의
분석이 필요하면 해당 CSV를 다시 읽어들이는 방식으로(추후 확장, 지금은 export만).

기본 dry-run(내보내기만, 삭제 안 함). 실제 삭제는 --apply.
실행: python3 scripts/events_drain.py [--apply]
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
OUT_DIR = STORAGE_REPO / "usage"


def _kst(ts_ns: int) -> datetime:
    return datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).astimezone(KST)


def _week_start(dt: datetime) -> datetime:
    """그 날짜가 속한 ISO주(KST 월요일 00:00) 시작."""
    d = dt.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    return d - timedelta(days=d.isoweekday() - 1)


def _to_ns(dt: datetime) -> int:
    return int(dt.timestamp() * 1e9)


def _table_columns(con) -> list[str]:
    """테이블의 **실제** 컬럼을 순서대로 읽는다.

    하드코딩하면 컬럼이 늘 때 조용히 빠지고, 이 스크립트는 그 뒤에 행을 통째로 지운다 —
    즉 드리프트가 곧 **되돌릴 수 없는 데이터 손실**이 된다. 스키마에서 파생시키는 것이
    유일하게 안전한 방식이다."""
    return [r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'ops_tool_calls' ORDER BY ordinal_position").fetchall()]


def _verify_backup(fpath: Path, cols: list[str], n_rows: int) -> bool:
    """지우기 전에 **쓴 것을 되읽는다.** 「썼다」와 「제대로 썼다」는 다르다 —
    디스크가 차거나 쓰기가 반쯤 끊겨도 예외 없이 파일은 남는다."""
    try:
        with open(fpath, newline="", encoding="utf-8") as f:
            back = list(csv.reader(f))
    except OSError as e:
        print(f"  ❌ 백업을 되읽지 못했다: {e}")
        return False
    if not back or back[0] != cols:
        print(f"  ❌ 헤더 불일치 — 기대 {len(cols)}컬럼, 파일 {len(back[0]) if back else 0}컬럼")
        return False
    if len(back) - 1 != n_rows:
        print(f"  ❌ 행수 불일치 — DB {n_rows}건, 파일 {len(back) - 1}건")
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
    print(f"백업 컬럼 {len(cols)}개(스키마 파생): {', '.join(cols)}")
    if apply:
        # 지운 행은 CSV 에만 남는다. 지금 통계·덱은 **DB 만** 읽으므로, 드레인한 구간의
        # first_seen·코호트는 그 시점부터 보이지 않는다(장기 사용자가 「신규」로 재라벨된다).
        # 이건 컬럼 손실과 달리 복구는 되지만, 모르고 돌리면 지표가 조용히 틀어진다.
        print("⚠️  드레인한 주는 DB 에서 사라진다 — usage_tracker·트랙션덱은 DB 만 읽으므로\n"
              "    그 구간의 first_seen·코호트가 안 보이게 된다(CSV 로는 남는다).")
    print()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    w = first_week
    drained_total = 0
    while w < now_week:
        w_end = w + timedelta(days=7)
        rows = con.execute(
            sql.SQL("SELECT {} FROM ops_tool_calls "
                    "WHERE ts_ns >= %s AND ts_ns < %s ORDER BY ts_ns").format(
                sql.SQL(", ").join(map(sql.Identifier, cols))),
            (_to_ns(w), _to_ns(w_end))
        ).fetchall()
        if not rows:
            w = w_end; continue
        fname = f"{w.strftime('%y%m%d')}-{(w_end - timedelta(days=1)).strftime('%m%d')}_user_log.csv"
        fpath = OUT_DIR / fname
        with open(fpath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(cols)
            writer.writerows(rows)
        print(f"[{w.date()}~{(w_end - timedelta(days=1)).date()}] {len(rows)}건 "
              f"× {len(cols)}컬럼 → {fpath.name}")
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
