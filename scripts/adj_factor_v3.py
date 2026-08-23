"""수정계수 라벨 부착 — `krx_adj_events` 의 빈 라벨 칸을 공시로 채운다.

260823 통합 전에는 `krx_base_resets`(측정)를 읽어 `krx_adj_factor_v3`(라벨)를 **새로 만들었다.**
갱신이 2단계라 뒤쪽이 깨져도 앞쪽만 돌면 멀쩡해 보였고, 실제로 260705 v2 드랍 때부터
실행 불가였는데 cron 이 없어 아무도 몰랐다. 이제 한 표의 UPDATE 라 그 상태가 불가능하다.

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
    # 260823 통합: 표가 하나가 되면서 이 스크립트는 **INSERT 가 아니라 라벨 칸 UPDATE** 가 됐다.
    #   종전에는 krx_base_resets(측정)와 krx_adj_factor_v3(라벨)이 별개라 갱신이 2단계였고,
    #   뒤쪽이 260705 부터 실행 불가였는데 앞쪽만 돌아 아무도 몰랐다(v2 드랍 때 「코드참조 0건」
    #   확인이 틀렸다). 게다가 `DELETE` 후 재생성 방식이라 실행하면 라벨이 통째로 날아갔다.
    #   이제 지울 것이 없다 — 비어 있는 칸만 채운다.
    resets = con.execute("""
        SELECT e.isu_cd, e.event_dd, e.adj_factor_raw, e.mkt,
               (SELECT w.mkt FROM krx_weekly w
                 WHERE w.isu_cd = e.isu_cd AND w.bas_dd < e.event_dd
                 ORDER BY w.bas_dd DESC LIMIT 1) AS prev_mkt
          FROM krx_adj_events e
         WHERE e.label_confidence IS NULL OR e.label_confidence = 'unlabeled'
         ORDER BY e.isu_cd, e.event_dd""").fetchall()
    total = con.execute("SELECT count(*) FROM krx_adj_events").fetchone()[0]
    print(f"조정 사건 {total:,}건 중 라벨 미판정 {len(resets):,}건 처리")
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
        cur.executemany("""
            UPDATE krx_adj_events SET
              adj_factor = %s, event_type = %s, label_source = %s,
              rcept_no = %s, label_confidence = %s, note = %s
            WHERE isu_cd = %s AND event_dd = %s""",
            [(f, etype, src, evid, conf, note, isu, dd)
             for (isu, dd, f, _raw, etype, src, evid, conf, note) in rows])
    con.commit()
    n = con.execute("SELECT count(*) FROM krx_adj_events WHERE label_confidence <> 'unlabeled'").fetchone()[0]
    print(f"라벨 부착 누계: {n:,}행")
    for k in sorted(stats): print(f"  {k}: {stats[k]}")
    con.close()

if __name__ == "__main__":
    main()
