"""수정계수 라벨 부착 — `krx_adj_events` 의 빈 라벨 칸을 공시로 채운다.

260823 통합 전에는 `krx_base_resets`(측정)를 읽어 `krx_adj_factor_v3`(라벨)를 **새로 만들었다.**
갱신이 2단계라 뒤쪽이 깨져도 앞쪽만 돌면 멀쩡해 보였고, 실제로 260705 v2 드랍 때부터
실행 불가였는데 cron 이 없어 아무도 몰랐다. 이제 한 표의 UPDATE 라 그 상태가 불가능하다.

원칙 (wiki/architecture/adjusted-price-timeseries.md §2.1, 데이터 공급처 회신 260703):
- 계수 = 거래소 기준가 리셋 실측 그대로. 라벨은 event_type + evidence(rcept_no)만 부여.
- 라벨 미확인 → confidence='unlabeled' (계수는 유효 — 거래소 실측).
- 시장이전(KOSPI↔KOSDAQ): 벤더 미적용 → confidence='excluded_market_transfer'로 보존(소비 시 제외).
- 호가단위 맞춤(261019): 기준가가 전일 종가를 호가단위로 올리거나 내린 것뿐인 리셋(×1.0014 류, 대부분 기업인수목적회사) →
  event_type='tick_rounding'·confidence='excluded_tick_rounding' 으로 보존(소비 시 제외). 기업 행위가 아니다(`open_proxy_mcp/krx_ticks.py`).
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
from open_proxy_mcp.krx_daily import WEEKLY_LATEST_SQL, WEEKLY_WITH_LATEST
from open_proxy_mcp.krx_retention import krx_weekly_pair_floor
from open_proxy_mcp.krx_ticks import TICK_ROUNDING_LABEL, TICK_ROUNDING_TYPE, is_tick_rounding


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

#: 라벨 칸 UPDATE — 여섯 칸이 지금 값과 **다를 때만** 쓴다(같으면 행을 다시 쓰지 않아 죽은 행이 안 생긴다).
#: 자리표시자 순서: SET 6 · 비교 6 · 키 2 (키가 끝에 온다).
UPDATE_LABELS = """
    UPDATE krx_adj_events SET
      adj_factor = %s, event_type = %s, label_source = %s,
      rcept_no = %s, label_confidence = %s, note = %s
    WHERE (adj_factor, event_type, label_source, rcept_no, label_confidence, note)
          IS DISTINCT FROM (%s::double precision, %s::text, %s::text, %s::text, %s::text, %s::text)
      AND ticker = %s AND event_dd = %s"""


def main():
    con = psycopg.connect(os.environ["DATABASE_URL"])
    # 260823 통합: 표가 하나가 되면서 이 스크립트는 **INSERT 가 아니라 라벨 칸 UPDATE** 가 됐다.
    #   종전에는 krx_base_resets(측정)와 krx_adj_factor_v3(라벨)이 별개라 갱신이 2단계였고,
    #   뒤쪽이 260705 부터 실행 불가였는데 앞쪽만 돌아 아무도 몰랐다(v2 드랍 때 「코드참조 0건」
    #   확인이 틀렸다). 게다가 `DELETE` 후 재생성 방식이라 실행하면 라벨이 통째로 날아갔다.
    #   이제 지울 것이 없다 — 비어 있는 칸만 채운다.
    # 261017: 직전 주간 시장(prev_mkt)은 krx_weekly 에서 읽는다. krx_weekly 를 최근 3년 + 종목별 연말 행으로 줄이면
    #   (`open_proxy_mcp/krx_retention.py`) 하한 이전 이벤트의 「직전 주」가 최대 1년 전 연말 행으로 잡혀, 그 사이 시장을
    #   옮긴 종목이 `excluded_market_transfer` 로 **영구** 오라벨된다(실측 261017 모의: 068270 20181227 1건).
    #   이 스크립트는 매일 미판정 전부를 다시 보므로 하한(+14일) 이전 이벤트는 이미 한 번 판정을 받았다 — 건너뛰어도
    #   잃는 것이 없고(판정 결과는 krx_adj_events 에 남아 있다), 새 이벤트는 늘 최근이라 그대로 처리된다.
    latest = con.execute(WEEKLY_LATEST_SQL).fetchone()[0]          # 닫힌 주 최신(261019 v03 C4 — krx_weekly 가 뷰여도 최근 몇 주만 읽는다)
    floor = krx_weekly_pair_floor(latest) if latest else "00000000"   # 시세가 비면 prev_mkt 가 None 이라 시장이전 판정은 일어나지 않는다
    # 261018(재설계 5-1 쓰는 쪽): 직전 시장의 원천은 「닫힌 주 + 일별 최신 거래일」(`WEEKLY_WITH_LATEST`) — 종전 krx_weekly 와 같은 모양.
    #   krx_weekly 만 보면 이번 주 사건의 직전 점이 지난 금요일로 고정되지만, 종전엔 이번 주 최신일 행도 직전 점이 될 수 있었다.
    resets = con.execute("""
        SELECT e.ticker, e.event_dd, e.adj_factor_raw, e.market,
               (SELECT w.market FROM """ + WEEKLY_WITH_LATEST + """ w
                 WHERE w.ticker = e.ticker AND w.price_dd < e.event_dd
                 ORDER BY w.price_dd DESC LIMIT 1) AS prev_mkt,
               e.prev_close, e.base_price
          FROM krx_adj_events e
         WHERE (e.label_confidence IS NULL OR e.label_confidence = 'unlabeled')
           AND e.event_dd >= %s
         ORDER BY e.ticker, e.event_dd""", (floor,)).fetchall()
    n_old = con.execute(
        "SELECT count(*) FROM krx_adj_events WHERE (label_confidence IS NULL OR label_confidence = 'unlabeled') "
        "AND event_dd < %s", (floor,)).fetchone()[0]
    total = con.execute("SELECT count(*) FROM krx_adj_events").fetchone()[0]
    print(f"조정 사건 {total:,}건 중 라벨 미판정 {len(resets):,}건 처리"
          + (f" (보존 하한+14일 {floor} 이전 미판정 {n_old:,}건은 전후 주간 점이 연말 행이라 건너뜀)" if n_old else ""))

    from collections import defaultdict

    rows, stats = [], defaultdict(int)
    for isu, dd, f, mkt, pm, pc, bp in resets:   # pm = 리셋일 직전 주간 mkt (위 서브쿼리에서 함께 온다) · pc·bp = 전일 종가·기준가
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

        # ①-2 호가단위 맞춤 제외 (261019): 기준가가 전일 종가를 호가단위에 맞춘 것뿐 — 기업 행위가 아니다. 계수는 실측 그대로 두고
        #   라벨로 「소비할 때 뺀다」만 표시한다(시장이전과 같은 방식). 서빙(`trading_data` firm)도 같은 판정으로 불연속에서 뺀다.
        if is_tick_rounding(pc, bp, mkt, dd):
            rows.append((isu, dd, f, raw_f, TICK_ROUNDING_TYPE, "reset", None, TICK_ROUNDING_LABEL,
                         f"호가단위 맞춤 {pc:g}→{bp:g}"))
            stats[TICK_ROUNDING_LABEL] += 1
            continue

        # ② ③ 삭제(260823). v2(라벨 장부)·dart_capital_events(공시 근거) 두 표는 **소진됐다** —
        #   실측: 미판정 1,998건 중 v2 로 붙일 수 있는 것 0건, dart 로 0건. v2 는 20260630 에서
        #   멎었고 dart 는 20260702, 둘 다 writer 가 없어 더 자라지 않는다. 이미 붙인 라벨
        #   (reset+v2 1,599 · reset+dart 35)은 krx_adj_events 에 들어 있어 표를 지워도 남는다.
        #   백업은 open-proxy-storage/db_backups/20260823/.
        #   → 남은 라벨 경로는 ①(시장이전 제외)과 ④(액면비율 스냅) 둘이다.
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

    # 261018(재설계 v02 7단계): **값이 바뀐 행만** 고친다. 이 패스는 매일 미판정 전부(운영 885건)를 다시 판정하는데,
    #   대부분 결과가 어제와 같다. 같은 값으로 UPDATE 해도 Postgres 는 행을 새로 써 죽은 행을 남긴다 — 그래서 하루 885건씩
    #   죽은 행이 쌓였다(운영 누적 UPDATE 14.5만 회 / 3,760행 표). 같으면 건너뛰고, 판정이 실제로 바뀐 행만 쓴다.
    params = [(f, etype, src, evid, conf, note, f, etype, src, evid, conf, note, isu, dd)
              for (isu, dd, f, _raw, etype, src, evid, conf, note) in rows]
    with con.cursor() as cur:
        cur.executemany(UPDATE_LABELS, params)
        changed = cur.rowcount
    con.commit()
    print(f"라벨 칸이 실제로 바뀐 행: {max(changed or 0, 0):,}/{len(params):,}")
    n = con.execute("SELECT count(*) FROM krx_adj_events WHERE label_confidence <> 'unlabeled'").fetchone()[0]
    print(f"라벨 부착 누계: {n:,}행")
    for k in sorted(stats): print(f"  {k}: {stats[k]}")
    con.close()

if __name__ == "__main__":
    main()
