"""수정계수 v3 생성 — krx_base_resets(거래소 실측) × 공시 라벨.

원칙 (wiki/architecture/adjusted-price-timeseries.md §2.1, FnGuide 회신 260703):
- 계수 = 거래소 기준가 리셋 실측 그대로. 라벨은 event_type + evidence(rcept_no)만 부여.
- 라벨 미확인 → confidence='unlabeled' (계수는 유효 — 거래소 실측).
- 시장이전(KOSPI↔KOSDAQ): 벤더 미적용 → confidence='excluded_market_transfer'로 보존(소비 시 제외).
- 액면변경(split/merge): 액면가 비율로 스냅(측정치가 비율의 ±0.5% 이내면 정확한 유리수로 치환) — 벤더(Quanti) 방식 정렬.
콜: 0 (전부 Supabase 로컬 조인).
"""
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
import psycopg
from datetime import datetime, timedelta
from fractions import Fraction

DDL = """
CREATE TABLE IF NOT EXISTS krx_adj_factor_v3 (
  isu_cd text NOT NULL, effective_date text NOT NULL,
  factor double precision NOT NULL, raw_factor double precision NOT NULL,
  event_type text, source text NOT NULL, evidence text,
  confidence text NOT NULL, note text,
  PRIMARY KEY (isu_cd, effective_date));
"""

def d2dt(s): return datetime.strptime(s, "%Y%m%d")

# 액면가 비율 후보 (한국 액면가 {100,200,500,1000,2500,5000} 조합의 비율)
PARS = [100, 200, 500, 1000, 2500, 5000]
RATIOS = sorted({Fraction(a, b) for a in PARS for b in PARS if a != b}, key=float)

def snap_par_ratio(f):
    """측정 계수가 액면가 비율의 ±0.5% 이내면 (정확 비율, 표기) 반환."""
    for r in RATIOS:
        rv = float(r)
        if abs(f / rv - 1) <= 0.005:
            return rv, f"{r.numerator}/{r.denominator}"
    return None, None

def main():
    con = psycopg.connect(os.environ["DATABASE_URL"])
    for stmt in DDL.strip().split(";"):
        if stmt.strip(): con.execute(stmt)
    # 260823: 종전에는 `DELETE FROM krx_adj_factor_v3` + commit 으로 전체를 지우고 재생성했다.
    #   그 직후의 v2 조회가 **드랍된 테이블**(260705 정리)을 참조해 죽었기 때문에, 실행하면
    #   **테이블만 비우고 중단**됐다 — 3,509건이 통째로 날아간다. 실제로 그렇게 날렸고
    #   git 이력의 backups_260705/krx_adj_factor_v2.csv 로 복구했다.
    #   CLAUDE.md 「파이프라인: 전체 재실행 금지, 누락분만 처리」에도 어긋난다. 증분으로 바꾼다.
    have = {(r[0], r[1]) for r in
            con.execute("SELECT isu_cd, effective_date FROM krx_adj_factor_v3").fetchall()}

    # prev_mkt 를 리셋마다 개별 조회하면 3,600+ 왕복이라 커넥션이 끊긴다(260823 실측).
    # 상관 서브쿼리 하나로 DB 에서 끝낸다.
    resets = con.execute("""
        SELECT b.isu_cd, b.reset_dd, b.factor, b.mkt,
               (SELECT w.mkt FROM krx_weekly w
                 WHERE w.isu_cd = b.isu_cd AND w.bas_dd < b.reset_dd
                 ORDER BY w.bas_dd DESC LIMIT 1) AS prev_mkt
          FROM krx_base_resets b ORDER BY b.isu_cd, b.reset_dd""").fetchall()
    skipped = sum(1 for r in resets if (r[0], r[1]) in have)
    resets = [r for r in resets if (r[0], r[1]) not in have]
    print(f"리셋 {len(resets) + skipped}건 중 신규 {len(resets)}건 처리 (기존 {skipped}건 보존)")
    v2 = con.execute(
        "SELECT isu_cd, effective_date, factor, event_type, evidence FROM krx_adj_factor_v2").fetchall()
    dartev = con.execute(
        "SELECT isu_cd, kind, rcept_no, rcept_dt FROM dart_capital_events").fetchall()

    # 인덱스: v2 by isu_cd → [(date, ...)], dart by isu_cd
    from collections import defaultdict
    v2i = defaultdict(list); [v2i[r[0]].append(r) for r in v2]
    dvi = defaultdict(list); [dvi[r[0]].append(r) for r in dartev]

    rows, stats = [], defaultdict(int)
    for isu, dd, f, mkt, pm in resets:   # pm = 리셋일 직전 주간 mkt (위 서브쿼리에서 함께 온다)
        raw_f = f
        etype = src = evid = note = None
        conf = "unlabeled"

        # ① 시장이전 제외 (벤더 미적용)
        if pm and pm != mkt and abs(f - 1) < 0.35:
            # 시장 바뀜 + 계수가 1 근처(가격 연속) = 이전 이벤트일 가능성. 강한 리셋(분할 등)은 제외 안 함.
            rows.append((isu, dd, f, raw_f, "market_transfer", "reset", None,
                         "excluded_market_transfer", f"{pm}→{mkt}"))
            stats["excluded_market_transfer"] += 1
            continue

        # ② v2 라벨 승계. v2 factor는 '배율'(주식수 배수) = 가격계수의 역수.
        #    stock_div는 v2 날짜가 신주상장일(연말 배당락보다 ~3-4개월 뒤)이라 창을 (ed-150d ≤ dd ≤ ed)로.
        best = None
        for _, ed, vf, vt, ve in v2i.get(isu, []):
            gap = (d2dt(ed) - d2dt(dd)).days  # 양수 = 리셋이 v2 날짜보다 이전
            ok = abs(gap) <= 7 or (vt == "stock_div" and 0 <= gap <= 150)
            if ok and (best is None or abs(gap) < abs(best[0])):
                best = (gap, vt, ve, vf)
        if best:
            _, etype, evid, vf = best
            src, conf = "reset+v2", "confirmed"
            if vf and abs(f * vf - 1) > 0.02:  # 역수 정합: 가격계수 × 배율 ≈ 1
                note = f"v2배율({vf:.4f})의 역수와 2%+ 괴리 — 실측 우선"
        else:
            # ③ dart_capital_events (결정공시 rcept_dt ≤ 리셋일 ≤ +150일, 최근접)
            bestd = None
            for _, kind, rno, rdt in dvi.get(isu, []):
                gap = (d2dt(dd) - d2dt(rdt)).days
                if 0 <= gap <= 150 and (bestd is None or gap < bestd[0]):
                    bestd = (gap, kind, rno)
            if bestd:
                _, etype, evid = bestd
                src, conf = "reset+dart", "dart"
            else:
                src = "reset"

        # ④ 액면변경 스냅 (벤더=액면가 비율): split/merge 라벨 or 무라벨 + 비율 근접
        if etype in ("split", "merge") or (etype is None and (f < 0.6 or f > 1.7)):
            sv, sr = snap_par_ratio(f)
            if sv is not None:
                if etype is None:
                    etype = "split" if sv < 1 else "merge"
                    note = (note + " · " if note else "") + f"비율스냅으로 라벨 추정"
                f = sv
                note = (note + " · " if note else "") + f"액면비율 {sr} 스냅(실측 {raw_f:.6f})"
                stats["par_snapped"] += 1

        rows.append((isu, dd, f, raw_f, etype, src, evid, conf, note))
        stats[conf] += 1
        if etype: stats[f"type:{etype}"] += 1

    with con.cursor() as cur:
        cur.executemany(
            "INSERT INTO krx_adj_factor_v3 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", rows)
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM krx_adj_factor_v3").fetchone()[0]
    print(f"v3 생성: {n}행")
    for k in sorted(stats): print(f"  {k}: {stats[k]}")
    con.close()

if __name__ == "__main__":
    main()
