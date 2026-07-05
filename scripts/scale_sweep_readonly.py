"""mkt_fund_hist/mkt_fund_q 전수 스케일오류 read-only sweep — scale_guard 재사용, DB 변경 없음.

배경(260705): 소프트센(032680) fy2022 오염(ni/eq ×100만)이 가드 신설(260704) 이전에 DB에 들어와
잔존. 가드는 신규 fetch만 막고 기존 데이터 소급 sweep이 없었음. 사용자 요청으로 잔존 오염 전수 파악.

mkt_fund_hist엔 자산/부채가 없어 항등식(②)은 못 씀. 사용 가능한 체크:
  ③ market_relative_cap: |ni| / MARKET_MAX_NI_ANCHOR(삼성전자) > 3배 → hard
  ③ digit_cap 백스톱: 16자리(1000조) 초과 → hard
  ① magnitude_jump(soft): 전년대비 10^n±20% 점프 — 참고용(오탐률 97.5%로 hard 승격 금지, scale_guard 문서 근거)
eq는 market_max 앵커가 없어(순이익 앵커라 자본에 안 맞음) digit_cap만 적용.

출력만 — UPDATE/DELETE 없음.
실행: python3 scripts/scale_sweep_readonly.py
"""
import os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
import psycopg
from open_proxy_mcp.services.scale_guard import assess, MARKET_MAX_NI_ANCHOR, check_digit_cap


def main() -> None:
    con = psycopg.connect(os.environ["DATABASE_URL"]); con.autocommit = True
    print(f"MARKET_MAX_NI_ANCHOR = {MARKET_MAX_NI_ANCHOR:.3e} (삼성전자 앵커)\n")

    print("=== mkt_fund_hist 전수 sweep ===")
    rows = con.execute(
        "SELECT isu_cd, fy, ni, eq, ni_restated, eq_restated FROM mkt_fund_hist ORDER BY isu_cd, fy"
    ).fetchall()
    by_isu: dict[str, list] = {}
    for isu, fy, ni, eq, nir, eqr in rows:
        by_isu.setdefault(isu, []).append((int(fy), nir if nir is not None else ni, eqr if eqr is not None else eq))

    hard_hist, soft_hist = [], []
    for isu, series in by_isu.items():
        series.sort()
        prev_ni = None
        for fy, ni, eq in series:
            v_ni = assess(thstrm=ni, frmtrm=prev_ni, market_max=MARKET_MAX_NI_ANCHOR)
            v_eq = check_digit_cap(eq)   # eq는 ni앵커 부적합 — 자릿수 백스톱만
            if v_ni["tier"] == "hard" or v_eq.get("triggered"):
                hard_hist.append((isu, fy, ni, eq, v_ni["hard_hit"], v_eq.get("triggered")))
            elif v_ni["tier"] == "soft":
                soft_hist.append((isu, fy, ni, eq, v_ni["soft_hit"]))
            prev_ni = ni

    print(f"[HARD] {len(hard_hist)}건 (물리적으로 불가능 — 정정/무효화 검토 대상)")
    for isu, fy, ni, eq, hit, eq_bad in hard_hist:
        print(f"  {isu} fy{fy}: ni={ni:.3e} eq={eq if eq is None else f'{eq:.3e}'} "
              f"ni_hit={hit} eq_digit_cap={eq_bad}")
    print(f"\n[SOFT] {len(soft_hist)}건 (전년대비 배수점프 — 참고용, 오탐 다수 예상)")
    for isu, fy, ni, eq, hit in soft_hist[:30]:
        print(f"  {isu} fy{fy}: ni={ni:.3e} hit={hit}")
    if len(soft_hist) > 30:
        print(f"  ... 외 {len(soft_hist) - 30}건 생략(전량은 --verbose 필요시 추가)")

    print("\n=== mkt_fund_q 전수 sweep (ni_cum·eq) ===")
    qrows = con.execute(
        "SELECT isu_cd, fy, quarter, ni_cum, eq FROM mkt_fund_q ORDER BY isu_cd, fy, quarter"
    ).fetchall()
    by_isu_q: dict[str, list] = {}
    for isu, fy, q, ni, eq in qrows:
        by_isu_q.setdefault(isu, []).append((int(fy), int(q), ni, eq))

    hard_q = []
    for isu, series in by_isu_q.items():
        series.sort()
        prev_ni = None
        for fy, q, ni, eq in series:
            v_ni = assess(thstrm=ni, frmtrm=prev_ni, market_max=MARKET_MAX_NI_ANCHOR)
            v_eq = check_digit_cap(eq)
            if v_ni["tier"] == "hard" or v_eq.get("triggered"):
                hard_q.append((isu, fy, q, ni, eq, v_ni["hard_hit"], v_eq.get("triggered")))
            prev_ni = ni
    print(f"[HARD] {len(hard_q)}건")
    for isu, fy, q, ni, eq, hit, eq_bad in hard_q:
        nis = f"{ni:.3e}" if ni is not None else "None"
        eqs = f"{eq:.3e}" if eq is not None else "None"
        print(f"  {isu} fy{fy}Q{q}: ni_cum={nis} eq={eqs} ni_hit={hit} eq_digit_cap={eq_bad}")

    print(f"\n=== 요약 ===")
    print(f"  mkt_fund_hist: hard {len(hard_hist)}건 · soft {len(soft_hist)}건 (전체 {len(rows)}행)")
    print(f"  mkt_fund_q:    hard {len(hard_q)}건 (전체 {len(qrows)}행)")
    print("  ※ read-only — DB 변경 없음")
    con.close()


if __name__ == "__main__":
    main()
