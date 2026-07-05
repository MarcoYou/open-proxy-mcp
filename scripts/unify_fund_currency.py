"""비KRW 공시사 재무를 **원화로 통일 저장** — 시총(krx_weekly, 항상 원화) 기준 **연도별 동적 통화감지**.

배경(260705): mkt_fund_hist/q는 각 연도 공시통화 그대로 저장(통화컬럼 없음). 두산밥캣(241560)처럼 연도별로
통화가 바뀌는 회사가 있어, mkt_fundamentals.currency(최신 통화 1개)를 전 연도에 곱하는 하위 FX가 옛 연도를
망침(4조×1185=4,826조). → 저장 단계에서 원화 통일하면 하위 FX 불필요.

방법:
  · 대상 = mkt_fundamentals.currency ∉ (KRW,nodata,?) 인 22사(USD/CNY/JPY).
  · 연도별 감지: 그 해 연말 시총(cap, 원화) ÷ eq 로 PBR을 계산 —
      as-is(원화 가정)가 정상범위면 원화 → 유지 / ×fx(외화 가정)가 정상이면 외화 → fx 곱해 원화화.
    (원화↔외화는 ~1000배 차라 판정 명확. cap 없으면 magnitude fallback: eq<1e10 → 외화.)
  · eq로 (firm,fy) 통화를 정하고 그 해의 ni·eq·restated·분기 전부에 동일 적용(통화는 연내 일관).
  · idempotent: 이미 원화면 as-is 정상 → 유지. fetch 후 derive 전 재실행 안전.
  · orig_currency 열에 원 통화 보존 + currency='KRW' 세팅 → 하위 리더(배포본 포함) FX 자동 스킵.

DART 0콜(krx_weekly·fx_rate 캐시). 기본 dry-run. 실제 반영은 --apply.
실행: python3 scripts/unify_fund_currency.py [--apply]
"""
import asyncio, math, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
import psycopg
from open_proxy_mcp.dart.fx import fx_to_krw

def _logdist(pbr):
    """PBR이 1(전형값)에서 로그거리 — 작을수록 정상. 원화 vs 라벨통화(167~1470배 차)를 가르는 척도."""
    return abs(math.log(pbr)) if pbr and pbr > 0 else 9e9


async def main(apply: bool) -> None:
    con = psycopg.connect(os.environ["DATABASE_URL"]); con.autocommit = True
    # orig_currency 보존열
    con.execute("ALTER TABLE mkt_fundamentals ADD COLUMN IF NOT EXISTS orig_currency text")
    # 대상 = 외화 라벨 OR 이미 통일된 이력(orig_currency). 라벨을 KRW로 바꿔도 orig_currency로 계속
    # 추적 → 재수집분도 매번 통일(going-forward). FX 통화는 COALESCE(orig_currency, currency).
    firms = con.execute(
        "SELECT isu_cd, upper(COALESCE(orig_currency, currency)) FROM mkt_fundamentals "
        "WHERE fetched='ok' AND (orig_currency IS NOT NULL OR "
        "(currency IS NOT NULL AND upper(currency) NOT IN ('KRW','NODATA','?')))"
    ).fetchall()
    print(f"대상 {len(firms)}사 (비KRW 라벨) · mode={'APPLY' if apply else 'DRY-RUN'}\n")

    fxcache: dict[tuple, float | None] = {}
    async def fx(ccy, fy):
        k = (ccy, fy)
        if k not in fxcache:
            fxcache[k] = await fx_to_krw(ccy, f"{fy}1231")
        return fxcache[k]

    tot_hist = tot_q = 0
    for isu, ccy in firms:
        ccy = ccy.upper()
        # 연말 시총(각 연도 마지막 거래주)
        capfy: dict[int, float] = {}
        for bas_dd, mktcap in con.execute(
                "SELECT bas_dd, mktcap FROM krx_weekly WHERE isu_cd=%s ORDER BY bas_dd", (isu,)):
            capfy[int(bas_dd[:4])] = float(mktcap or 0)   # 연내 최신(=연말) 덮어씀
        # 연도별 통화 결정 (eq 기준)
        hist = {int(fy): (ni, eq, nir, eqr) for fy, ni, eq, nir, eqr in con.execute(
            "SELECT fy, ni, eq, ni_restated, eq_restated FROM mkt_fund_hist WHERE isu_cd=%s", (isu,))}
        # 1차: 연도별 원시 판정(신뢰도=로그거리 차)
        raw: dict[int, tuple] = {}
        for fy in sorted(hist):
            ni, eq, nir, eqr = hist[fy]
            eqv = eqr if eqr is not None else eq
            cap = capfy.get(fy); f = await fx(ccy, fy)
            if not eqv:
                raw[fy] = ("skip", 0.0, eqv, cap, f); continue
            a = abs(eqv)
            # magnitude 1차: 이 22사(외화 라벨) 한정 — 외화 원단위 값은 절대 1e11 미만, KRW 상장 자본은
            # 통상 1e10 이상. 회색지대(1e10~1e11)만 cap-anchor(시총 기준 로그거리)로 판정.
            if a >= 1e11:
                raw[fy] = ("krw", 9.0, eqv, cap, f)
            elif a < 1e10:
                raw[fy] = ("foreign", 9.0, eqv, cap, f)
            elif cap and f:
                da, df = _logdist(cap / a), _logdist(cap / (a * f))
                raw[fy] = ("foreign" if df < da else "krw", abs(da - df), eqv, cap, f)
            else:
                raw[fy] = ("krw", 0.0, eqv, cap, f)   # 회색+cap없음: 보수적 krw(2차변환 방지, first-run엔 해당 없음)
        # 2차: 스무딩 — 고신뢰(≥1.5)는 자기 판정 유지, 저신뢰(cap 극소로 등거리)는 최근접 고신뢰 연도에서
        #      상속(currency는 시간연속). 두산밥캣(고신뢰 krw→foreign)은 그대로, 상시-외화사 저신뢰 초기연도는
        #      이웃 foreign 상속. 고신뢰 없으면(전부 애매) 원시판정 유지.
        hi = {fy: d for fy, (d, c, *_ ) in raw.items() if d in ("foreign", "krw") and c >= 1.5}
        decisions, rows_out = {}, []
        for fy, (d, c, eqv, cap, f) in raw.items():
            if d == "skip":
                dec = "skip"
            elif c >= 1.5 or not hi:
                dec = d
            else:
                dmin = min(abs(h - fy) for h in hi)
                cand = {hi[h] for h in hi if abs(h - fy) == dmin}
                dec = d if len(cand) > 1 else cand.pop()   # 등거리 상충 시 원시판정
            decisions[fy] = dec
            tag = f"×{f:.1f}" if dec == "foreign" and f else ""
            note = " (상속)" if dec != d and d != "skip" else ""
            eqs = f"{eqv:+.3e}" if eqv is not None else "None"
            rows_out.append(f"    fy{fy}: eq={eqs} cap={(cap or 0)/1e12:.1f}조 c={c:.1f} → {dec}{tag}{note}")
        conv = [fy for fy, d in decisions.items() if d.startswith("foreign")]
        print(f"[{isu} {ccy}] 환산대상 fy={conv or '없음'}")
        for r in rows_out: print(r)

        if apply:
            for fy in conv:
                f = await fx(ccy, fy)
                if not f: continue
                ni, eq, nir, eqr = hist[fy]
                vals = tuple(v * f if v is not None else None for v in (ni, eq, nir, eqr))
                con.execute("UPDATE mkt_fund_hist SET ni=%s, eq=%s, ni_restated=%s, eq_restated=%s "
                            "WHERE isu_cd=%s AND fy=%s", (*vals, isu, fy))
                tot_hist += 1
            # 분기: 그 fy의 통화결정을 그대로 적용
            for fy, q, ni, eq in con.execute(
                    "SELECT fy, quarter, ni_cum, eq FROM mkt_fund_q WHERE isu_cd=%s", (isu,)):
                if decisions.get(int(fy), "").startswith("foreign"):
                    f = await fx(ccy, int(fy))
                    if not f: continue
                    con.execute("UPDATE mkt_fund_q SET ni_cum=%s, eq=%s WHERE isu_cd=%s AND fy=%s AND quarter=%s",
                                (ni * f if ni is not None else None, eq * f if eq is not None else None, isu, fy, q))
                    tot_q += 1
            con.execute("UPDATE mkt_fundamentals SET orig_currency=COALESCE(orig_currency,%s), currency='KRW' WHERE isu_cd=%s", (ccy, isu))
        print()
    if apply:
        print(f"✓ APPLY: mkt_fund_hist {tot_hist}행 · mkt_fund_q {tot_q}행 환산 · 라벨 KRW화 + orig_currency 보존")
        print("  → 다음: derive_fundamentals 재실행(mkt_fundamentals 원화화) + 밴드 재실행")
    con.close()


if __name__ == "__main__":
    asyncio.run(main("--apply" in sys.argv))
