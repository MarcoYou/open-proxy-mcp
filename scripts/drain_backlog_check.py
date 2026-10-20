#!/usr/bin/env python3
"""드레인 밀림 감시 — **잊히는 것이 이 절차의 유일한 고장 모드다.**

260817 실측: `events_drain.py` 는 260810 에 딱 한 번 돌고 잊혀, 완결 주 **7주 · 357,734건**이
밀린 채 무료티어 71% 까지 찼다. 수동 절차는 잊히고, **잊힌 절차는 없는 것과 같다.**

그래서 지우는 것을 자동화하는 대신 **밀린 것을 매주 알려 준다.**
왜 삭제까지 자동화하지 않나:
  · 산출 CSV 는 **private 레포**에 들어간다. public 레포의 CI 가 거기 쓰려면 교차 레포
    쓰기 토큰을 public 워크플로 시크릿으로 둬야 한다 — 이건 결정이어야지 부수효과여선 안 된다.
  · `--apply` 는 되돌릴 수 없는 삭제다. 백업 커밋이 **사람 손으로** 확인된 뒤에 도는 게 맞다.
감시는 **읽기만** 한다(SELECT + 용량 조회). 지우지도, 쓰지도 않는다.

260914 확장: 용량 % 하나로는 부족했다. events 를 다 비워도 `fwd`(서빙용 스냅샷)와
`fwd_hist`(리비전 이력)가 DB 의 대부분이라, 보존 정책(fwd 최신 4주 · fwd_hist 13주)이
지켜지는지를 **불변식**으로 같이 본다. 새 주를 먼저 올린 뒤 정리하므로 500MB 직전에서
알리면 늦다. 경고선은 90%다.
2610(재설계 5단계): `fwd`·`fwd_hist` 는 같은 이름·같은 칸의 **뷰**가 됐다(밑은 사실값 표, 벌당 ≈31MB → ≈9MB).
보존은 private 컨센서스 수집기의 주간 송출 `push_fwd_fact.py` 가 한 트랜잭션으로 맡는다 —
그 주 교체(같은 ISO 주 재송출 포함) → 5주차를 이력으로 → 14주차 삭제. 이 감시는 뷰를 그대로 읽는다
(`to_regclass` 는 뷰도 찾는다). 옛 `prune_fwd.py`·`push_fwd_hist.py` 는 은퇴했다.
261018(구간형 저장): 뷰가 게시 달력 `fwd_asof` 위로 옮겨 가면(`open_proxy_mcp/fwd_window.py` 의 「달력 모양」)
`fwd`·`fwd_hist` 는 같은 달력을 읽고 둘 다 창 `FWD_HOT_WEEKS`(기본 52주)를 담는다. 그때 이 감시는
① 달력의 ISO 주 수 ≤ 창+1(양 끝 포함) ② 같은 ISO 주 중복 0 ③ 창 밖으로 닫힌 판이 1주 넘게 남지 않았나
④ 창 첫날(읽는 쪽이 읽는 가장 이른 날 — 52w 리비전 기준일)이 온전한 한 벌인가(판 수 = 달력 n_fact, 시세 행 = n_stock)
를 본다. ④는 쓰는 쪽 창이 읽는 쪽 창(`FWD_HOT_WEEKS`)보다 짧거나 창 정리가 경계 날짜를 깎을 때 조용히 틀리는 것을 잡는다.
2026-10-21: 옛 주별 모양(최신 4주·13주 상한)의 분기를 걷어냈다 — 상한 기본값은 창+1주 하나다.

실행:  python3 scripts/drain_backlog_check.py [--max-weeks N] [--warn-pct P] [--tables]
                                              [--fwd-max-weeks N] [--hist-max-weeks N]
종료코드: 0 정상 · 1 조치 필요 또는 검사 오류(기존 실패 알림 유지)
상태: OK / ATTENTION_NEEDED(운영 조치 필요) / CHECK_ERROR(검사 미완료).
GitHub Actions에서는 작업 요약에도 상태를 남긴다. 원문 예외에는 접속 정보가 섞일 수 있어 출력하지 않는다.
--tables: 테이블별 용량·행수 상위 12개를 덧붙인다(옛 DB 용량 리포트 스크립트 흡수, 260902). 없으면 출력 동일.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

# 주 계산·시간대는 **드레인 본체의 것을 그대로 쓴다.** 사본을 두면 감시가 실제 대상과
# 다른 주를 세게 되고, 그때 감시는 있으나 마나가 된다.
from events_drain import KST, _kst, _to_ns, _week_start  # noqa: E402
from open_proxy_mcp import fwd_window  # noqa: E402 — 컨센서스 이력 창·저장 모양의 정의(서버·집계 배치와 같은 것)


#: 달력 모양 창 첫날 검사 — 쓰는 쪽 표·칸이 있을 때만(없으면 건너뛴다).
EDGE_READY_SQL = ("SELECT to_regclass('public.fwd_price') IS NOT NULL AND (SELECT count(*) FROM information_schema.columns "
                  "WHERE table_schema = 'public' AND table_name = 'fwd_asof' AND column_name IN ('n_fact', 'n_stock')) = 2")
EDGE_SQL = ("SELECT a.n_fact, a.n_stock, "
            "(SELECT count(*) FROM fwd_fact_ver f WHERE f.valid_from <= a.as_of AND a.as_of < f.valid_to), "
            "(SELECT count(*) FROM fwd_price p WHERE p.as_of = a.as_of) "
            "FROM fwd_asof a WHERE a.as_of = %s")


def _snapshot_week_stats(days) -> tuple[int, int]:
    """(ISO 주 수, 같은 주에 더 들어간 날짜 수). 주간 표에 날짜가 둘이면 보존 누수다."""
    weeks = {d.isocalendar()[:2] for d in days}
    return len(weeks), len(days) - len(weeks)


def _report_status(status: str, detail: str, findings: list[str] | None = None) -> None:
    """검사 결과와 검사 실패를 구분한다. detail 에 원문 예외·접속 정보는 넣지 않는다."""
    line = f"상태: {status} — {detail}"
    print(line)
    if os.getenv("GITHUB_ACTIONS") == "true" and status != "OK":
        level = "warning" if status == "ATTENTION_NEEDED" else "error"
        print(f"::{level} title=drain-backlog {status}::{detail}")
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as out:
                out.write(f"## 드레인 감시 결과\n\n{line}\n\n")
                if findings:
                    out.write("".join(f"- {finding}\n" for finding in findings) + "\n")
        except OSError:
            # 요약 파일 문제로 본래 검사 결과를 바꾸거나 경로·접속 정보를 출력하지 않는다.
            print("작업 요약 기록 실패 — 위의 검사 결과를 확인할 것.", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-weeks", type=int, default=0,
                    help="허용할 밀린 완결 주 수 (기본 0 — 한 주만 밀려도 바로 알린다)")
    ap.add_argument("--warn-pct", type=int, default=90,
                    help="무료티어 경고선 %% (기본 90 — 새 fwd 한 벌의 일시 공간까지 남긴다)")
    ap.add_argument("--fwd-max-weeks", type=int, default=None,
                    help="fwd 최대 ISO 주 수 (기본: 창 + 1주)")
    ap.add_argument("--hist-max-weeks", type=int, default=None,
                    help="fwd_hist 의 as_of 가 이 ISO 주 수를 넘으면 실패 (기본: 창 + 1주)")
    ap.add_argument("--tables", action="store_true",
                    help="테이블별 용량 breakdown(100KB 초과 상위 12개)도 출력 — 읽기만 한다")
    ap.add_argument("--json", action="store_true",
                    help="단일 JSON 상태(healthy/action_needed/execution_error)와 안전한 점검 본문")
    a = ap.parse_args()

    url = os.getenv("DATABASE_URL")
    if not url:
        if a.json:
            print(json.dumps({"version": 1, "status": "execution_error",
                              "report": "DATABASE_URL 이 없다 — 감시를 실행하지 못했다."}, ensure_ascii=False))
            return 1
        _report_status("CHECK_ERROR", "DATABASE_URL 이 없다 — 감시를 실행하지 못했다.")
        return 1

    try:
        if a.json:
            with contextlib.redirect_stdout(io.StringIO()) as report:
                result = _check(a, url)
            print(json.dumps({"version": 1, "status": "healthy" if result == 0 else "action_needed",
                              "report": report.getvalue()}, ensure_ascii=False))
            return result
        return _check(a, url)
    except Exception as exc:
        # psycopg 예외에는 URL·사용자명·호스트 등 접속 정보가 들어갈 수 있다.
        # 오류 종류만 남기고 traceback/원문은 로그와 작업 요약 모두에서 생략한다.
        detail = f"감시를 완료하지 못했다 ({type(exc).__name__}). DB 연결 설정·접근·조회 권한을 확인할 것."
        if a.json:
            print(json.dumps({"version": 1, "status": "execution_error", "report": detail}, ensure_ascii=False))
        else:
            _report_status("CHECK_ERROR", detail)
        return 1


def _check(a: argparse.Namespace, url: str) -> int:
    import psycopg

    con = psycopg.connect(url, connect_timeout=15)
    try:
        con.autocommit = True
        row = con.execute("SELECT min(ts_ns), max(ts_ns), count(*) FROM ops_tool_calls").fetchone()
        mn, mx, n = row
        now_week = _week_start(datetime.now(tz=KST))
        # events 가 없어도 DB 용량·스냅샷 보존 검사는 빠뜨리지 않는다.
        w, weeks = _week_start(_kst(mn)) if n else now_week, []
        while w < now_week:
            end = w + timedelta(days=7)
            c = con.execute(
                "SELECT count(*) FROM ops_tool_calls WHERE ts_ns >= %s AND ts_ns < %s",
                (_to_ns(w), _to_ns(end))).fetchone()[0]
            if c:
                weeks.append((w.date(), (end - timedelta(days=1)).date(), c))
            w = end

        size_mb = con.execute(
            "SELECT pg_database_size(current_database())/1024.0/1024").fetchone()[0]
        cur_week = con.execute(
            "SELECT count(*) FROM ops_tool_calls WHERE ts_ns >= %s", (_to_ns(now_week),)
        ).fetchone()[0]
        # 저장 모양 — 뷰가 게시 달력을 읽으면(구간형 저장 뒤) 두 뷰의 날짜는 달력 하나다. 달력이 싸고 정확하다
        # (뷰의 DISTINCT 는 이력 52주면 느리다). 창 밖으로 닫힌 판이 남았는지도 여기서 센다.
        stale_versions: tuple[int, object] = (0, None)
        window_edge = None          # (창 첫날, n_fact, n_stock, 그 날 판 수, 그 날 시세 행 수)
        cal_days = [r[0] for r in con.execute("SELECT as_of FROM fwd_asof ORDER BY 1").fetchall()]
        cal_weeks, cal_duplicate_days = _snapshot_week_stats(cal_days)
        fwd_days, fwd_weeks, fwd_duplicate_days = cal_days, cal_weeks, cal_duplicate_days
        hist_days, hist_weeks, hist_duplicate_days = cal_days, cal_weeks, cal_duplicate_days
        if cal_days and con.execute("SELECT to_regclass(%s) IS NOT NULL",
                                    ("public.fwd_fact_ver",)).fetchone()[0]:
            # 창 정리는 `valid_to ≤ 최신 − 창` 인 판을 지운다. 1주 늦음까지는 봐 준다(송출이 정리보다 먼저 돌 수 있다).
            grace = cal_days[-1] - timedelta(days=fwd_window.window_days() + 7)
            stale_versions = tuple(con.execute(
                "SELECT count(*), min(valid_to) FROM fwd_fact_ver WHERE valid_to <= %s", (grace,)).fetchone())
            # 창 첫날이 온전한 한 벌인가 — 읽는 쪽(서버·배치)은 그 날을 읽는다(52w 리비전 기준일). 쓰는 쪽 창이 더 짧거나
            # 창 경계 날짜의 판·시세를 지우고 달력 행만 남기면 그 날은 일부 종목만 보인다 — 조용히 틀린 답이 된다.
            # 일부만 지워지는 것은 가장 이른 날부터라 창 첫날 하나만 본다(판 수 = 달력 n_fact, 시세 행 = n_stock).
            first = next((d for d in cal_days if d >= fwd_window.window_start(cal_days[-1])), None)
            if first is not None and con.execute(EDGE_READY_SQL).fetchone()[0]:
                window_edge = (first, *con.execute(EDGE_SQL, (first,)).fetchone())
        # --tables 일 때만 한 번 더 읽는다(옛 DB 용량 리포트와 같은 SQL). 기본 경로는 그대로.
        tables = con.execute("""
          SELECT relname, pg_total_relation_size(relid) b, n_live_tup
          FROM pg_stat_user_tables JOIN pg_statio_user_tables USING(relid, relname)
          WHERE pg_total_relation_size(relid) > 100*1024
          ORDER BY 2 DESC LIMIT 12""").fetchall() if a.tables else None
    finally:
        con.close()

    pct = 100 * size_mb / 500        # Supabase 무료티어 500MB
    if n:
        print(f"events {n:,}행 · {_kst(mn).date()} ~ {_kst(mx).date()}")
    else:
        print("events 가 비어 있다 — 밀린 것 없음. 용량·스냅샷 보존 검사는 계속한다.")
    print(f"DB {size_mb:.0f}MB / 500MB ({pct:.0f}%)")
    if tables is not None:
        total_b = float(size_mb) * 1024 * 1024
        for name, b, live in tables:
            print(f"  {name:<24} {b/1024/1024:>7.1f} MB  ({b / total_b * 100:>4.1f}%)  {live:>10,}행")
    print(f"진행 중인 주({now_week.date()}~) {cur_week:,}행 — 드레인 대상 아님")
    window = fwd_window.hot_weeks()
    # 상한 — 주는 값이 있으면 그것, 없으면 창 + 1주(창의 양 끝 날짜가 둘 다 든다)
    fwd_max = a.fwd_max_weeks if a.fwd_max_weeks is not None else window + 1
    hist_max = a.hist_max_weeks if a.hist_max_weeks is not None else window + 1
    if fwd_days:
        print(f"fwd·fwd_hist 게시 달력 {len(fwd_days)}벌 · {fwd_weeks}주 ({fwd_days[0]} ~ {fwd_days[-1]})"
              f" — 창 {window}주(양 끝 포함 {fwd_max}주까지)")
    if stale_versions[0]:
        print(f"창 밖 닫힌 판 {stale_versions[0]:,}개 (가장 이른 끝 {stale_versions[1]})")
    if window_edge:
        print(f"창 첫날 {window_edge[0]} — 판 {window_edge[3]:,}/{window_edge[1]:,} · "
              f"시세 {window_edge[4]:,}/{window_edge[2]:,}")
    if weeks:
        print(f"\n밀린 완결 주 {len(weeks)}개 · {sum(c for *_, c in weeks):,}건")
        for s, e, c in weeks:
            print(f"  [{s}~{e}] {c:,}")
    else:
        print("\n밀린 완결 주 없음.")

    edge_broken = bool(window_edge and (window_edge[3] != window_edge[1] or window_edge[4] != window_edge[2]))
    bad = []
    if len(weeks) > a.max_weeks:
        bad.append(f"완결 주 {len(weeks)}개가 밀렸다(허용 {a.max_weeks})")
    # 두 뷰가 같은 달력을 읽으니 한 번만 센다.
    if fwd_weeks > min(fwd_max, hist_max):
        bad.append(f"게시 달력 {fwd_weeks}주가 창(허용 {min(fwd_max, hist_max)}주)보다 길다 — 창 밖 날짜는 뷰에서 "
                   "판 일부만 보인다. 창 정리(창 밖 닫힌 판을 보관본으로 내보낸 뒤 지우는 주간 작업)가 달력을 줄이지 못했다")
    if fwd_duplicate_days:
        bad.append(f"게시 달력 같은 ISO 주에 날짜가 {fwd_duplicate_days}개 더 있다 — 주 1벌 계약 위반"
                   "(리비전 기준일·선행 집계 날짜가 흔들린다)")
    if stale_versions[0]:
        bad.append(f"창 밖 닫힌 판 {stale_versions[0]:,}개가 1주 넘게 남았다(가장 이른 끝 {stale_versions[1]}) — "
                   "창 정리가 안 돌았다")
    if edge_broken:
        bad.append(f"창 첫날 {window_edge[0]} 이 한 벌이 아니다(판 {window_edge[3]:,}/{window_edge[1]:,} · "
                   f"시세 {window_edge[4]:,}/{window_edge[2]:,}) — 읽는 쪽은 그 날을 읽어(52w 리비전 기준일) 일부 종목만 "
                   "보고 답한다. 창 정리가 읽는 쪽 창 안의 판·시세를 지웠다")
    if pct >= a.warn_pct:
        bad.append(f"무료티어 {pct:.0f}% (경고선 {a.warn_pct}%)")
    if not bad:
        _report_status("OK", "검사를 완료했고 운영 경고선 이내다.")
        return 0

    _report_status("ATTENTION_NEEDED", "검사는 완료됐으나 운영 조치가 필요하다. 경고 항목을 확인할 것.", bad)
    print("\n⚠️  " + " · ".join(bad))
    if (fwd_weeks > min(fwd_max, hist_max) or fwd_duplicate_days or stale_versions[0]
                        or edge_broken):
        print("""
조치 (fwd·fwd_hist 게시 달력 — private 컨센서스 수집기, 원본은 그 머신의 로컬 DB 라 지워도 다시 낼 수 있다):
  같은 ISO 주 중복이면 토요일 체인의 주간 송출(push_fwd_fact.py)을 그 주 날짜로 다시 돌린다 — 송출이 그 주를 되돌리고 다시 쓴다.
  창이 길거나 창 밖 닫힌 판이 남았으면 창 정리 기록을 본다 — 보관본 내보내기와 대조가 끝난 판만 지우므로 다시 돌려도 같다.
  창 첫날이 한 벌이 아니면 쓰는 쪽과 읽는 쪽(서버·이 감시·선행 집계 배치의 FWD_HOT_WEEKS)의 창 길이가 같은지,
  창 정리가 창 경계 날짜(최신 − 창)의 판·시세를 남기는지 본다 — 창 정리는 창 밖 날짜의 달력 행도 함께 지워야 한다.""")
    if pct >= a.warn_pct and not n:
        print("\nevents 가 비어 있어 드레인으로 회수할 공간이 없다. --tables 로 테이블별 용량과 보존 정책을 확인할 것.")
    if len(weeks) > a.max_weeks or (pct >= a.warn_pct and n):
        # 261018: 4) 가 `VACUUM FULL` 이었다 — 표 전체를 잠그는 명령이라 정기 절차에서 뺐다(일요일 크론도 꺼져 있다).
        #   평범한 VACUUM 으로 빈 자리를 표시하면 다음 주 기록이 그 자리를 다시 쓴다. 자동 드레인도 그렇게 한다.
        print("""
조치 — 먼저 자동 드레인 결과를 본다(월 09:00, private open-proxy-storage/usage_auto_drain.py):
  결과 장부 open-proxy-storage/usage/auto-drain-last.json 의 status·error_type
손으로 할 때 (private 레포 백업이 먼저다 — 지우는 쪽만 영속이고 남기는 쪽이 휘발이면 백업이 아니다):
  1) python3 scripts/events_drain.py                 # dry-run: 주 파일 + 검증증(.manifest.json)을 쓴다(usage/events/)
  2) open-proxy-storage 에서 그 주 파일·검증증 커밋·푸시   # --apply 는 원격의 파일이 로컬과 같을 때만 지운다
  3) python3 scripts/events_drain.py --apply         # 원격 보존 확인 → 행을 잠그고 백업과 대조 → DELETE
  4) VACUUM (ANALYZE) ops_tool_calls;                # 빈 자리를 다음 주 기록이 다시 쓴다. VACUUM FULL(표 전체 잠금)은 쓰지 않는다
자세히: private wiki-private/architecture/usage-telemetry-operations.md""")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
