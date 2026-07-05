"""분기 재무 시계열 저장소(mkt_fund_q) — firm/market/sector 밸류 밴드의 분기 granularity 원천.

설계(260705, 사용자 확정): "주간 가격 × **분기 재무** × 분기말 환율". 분기 재무가 있어야 과거
TTM PER(최근 4분기 지배순이익 합)·MRQ PBR(최근분기 지배자본)을 시계열로 산출 — 연간(mkt_fund_hist)만
있어서 밴드 TTM이 N/A였던 한계를 해소.

키: (isu_cd, fy, quarter). quarter 1/2/3/4(=사업보고서). 저장:
  ni_cum = 지배순이익 **누적(YTD)** — TTM = FY(y-1) + ni_cum(y,q) − ni_cum(y-1,q).
  eq     = 지배자본 **기말 잔액**(BS, 기간무관).

수집 절약: **Q4(사업보고서)는 이미 있는 연간 데이터에서 seed(DART 0콜)** —
  · FY2018~2024 = mkt_fund_hist(ni=연간누적=Q4누적, eq=FY말자본, restated 우선)
  · FY2025      = mkt_fundamentals(ni_fy, eq_fy)
  나머지 Q1(11013)·반기(11012)·3Q(11014)만 DART 수집.

추출: financial_metrics._extract_cumulative_is 규칙 재사용 — 분기/반기 손익은 thstrm_add_amount(누적),
1Q·사업보고서는 thstrm(=누적). BS는 thstrm(잔액). 지배주주 귀속 account_id. 스케일가드(소프트센).

PIT는 **읽기 시점**에 표준 공시지연으로 매핑(저장 X): 1Q→5/15·반기→8/14·3Q→11/14·사업보고서→익년 4/1.

Rate limit(하드룰 준수): 동시성 1 · 0.45s sleep(≈133콜/분, 910 안전) · ReadError 즉시 중단(resume) ·
25건마다 flush · 최종 flush는 try로 감싸 blip에도 버퍼 손실 최소화.

실행:
  python3 scripts/market_fund_quarterly.py --seed              # Q4 seed(0콜)만
  python3 scripts/market_fund_quarterly.py --pilot 005930,000660,051910  # 소표본 전분기(검증용)
  python3 scripts/market_fund_quarterly.py --fetch             # 전 종목 Q1~Q3 백필(재개 가능, ~1.5h)
  python3 scripts/market_fund_quarterly.py --fetch --years 2020,2021,2022,2023,2024,2025
"""
import argparse, asyncio, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
import psycopg
from open_proxy_mcp.services.scale_guard import gid_exact, assess as scale_assess
from open_proxy_mcp.services.financial_metrics import normalize_amount

YEARS_DEFAULT = [2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025]
# (quarter, reprt_code) — Q1/반기/3Q만 DART 수집(Q4는 seed)
QFETCH = [(1, "11013"), (2, "11012"), (3, "11014")]
Q_TO_RC = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
# 콜 간 sleep(초) — env로 조정. DART client _throttle_api가 910/분을 별도 강제하므로 이건 예의용.
SLEEP = float(os.getenv("FUND_Q_SLEEP", "0.45"))

# 지배주주 귀속 추출(260705 QA 후 정정): account_id **substring**으로 net income 귀속만 —
# "ProfitLossAttributableToOwnersOfParent"는 총포괄 "ComprehensiveIncomeAttributable…"와 문자열이
# 구분되므로 substring이 안전(dart_* 접두 변형도 포착). account_id가 아예 없는(-표준계정코드 미사용-)
# 종목(LG그룹 등 다수)은 account_nm 폴백 — 단 **IS/BS만**(CIS 총포괄 제외, 값이 달라 오염). 총계
# (ifrs-full_ProfitLoss·Equity) 폴백은 **금지**(비지배 포함 총계를 지배로 오저장 — QA 실측 LG화학).
_NI_CTRL_ID = "ProfitLossAttributableToOwnersOfParent"
_EQ_CTRL_ID = "EquityAttributableToOwnersOfParent"


def _cum_is(r):
    """IS 누적: thstrm_add(당기누적) 우선, 없으면 thstrm(1Q·사업보고서는 이게 누적)."""
    v = normalize_amount(r.get("thstrm_add_amount"))
    return float(v) if v is not None else (
        float(normalize_amount(r.get("thstrm_amount"))) if normalize_amount(r.get("thstrm_amount")) is not None else None)


def _bal(r):
    v = normalize_amount(r.get("thstrm_amount"))
    return float(v) if v is not None else None


def _ordv(r):
    try:
        return int(r.get("ord") or 0)
    except (TypeError, ValueError):
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# 경우의수 로직 트리 — 각 firm-quarter 응답이 어느 케이스로 지배 순이익·자본을 얻는지 명시 분류.
# 각 추출 함수는 (값, 케이스코드)를 반환. 케이스코드로 전 종목 분포를 집계해 exhaustive 검증.
#
# 재무제표 조합: IS(손익)·CIS(포괄손익) 중 하나 또는 둘 · CFS(연결)/OFS(별도) — fetch가 CFS→OFS.
# 지배/비지배 분해: 표준 account_id / 비표준(-표준계정코드 미사용- nm) / 분해 없음(무소수지분·별도).
# ─────────────────────────────────────────────────────────────────────────────
def _nm(r):
    return (r.get("account_nm") or "").replace(" ", "")


def _ctrl_in_sj(rows, sj, val):
    """한 재무제표(sj) 안 지배 귀속 행: ① id substring → ② nm '지배'(비지배·총포괄 제외) ord 최선두.
    반환 (값, 서브케이스 'ID'|'NM'|None). IS/CIS는 ord 시퀀스 별개 → sj 단위 비교."""
    hit = [r for r in rows if r.get("sj_div") == sj and _NI_CTRL_ID in (r.get("account_id") or "")]
    if hit:
        return val(min(hit, key=_ordv)), "ID"
    cands = [r for r in rows if r.get("sj_div") == sj and "지배" in _nm(r) and "비지배" not in _nm(r)
             and "ComprehensiveIncome" not in (r.get("account_id") or "")]
    return (val(min(cands, key=_ordv)), "NM") if cands else (None, None)


def _has_minority_ni(rows):
    return any(r.get("sj_div") in ("IS", "CIS") and "비지배" in _nm(r)
               and "ComprehensiveIncome" not in (r.get("account_id") or "") for r in rows)


def extract_controlling_ni_cum(rows):
    """지배 당기순이익 누적 → (값, 케이스). 트리:
      NODATA                — 손익 행 없음
      IS_ID / IS_NM         — IS 지배행(표준 id / 비표준 nm) = 순수 손익 순이익 귀속 [LG화학·삼성]
      CIS_ID / CIS_NM       — IS 없이 CIS 지배행(ord 최선두=총포괄보다 앞) [000180·기아·SK 결합제출]
      TOTAL                 — 지배·비지배 순이익 귀속 둘 다 부재(무소수지분/별도) → 당기순이익 총계=지배 [001340·NAVER]
      MINORITY_NO_CTRL      — 비지배는 있는데 지배 귀속 못 찾음(이상 케이스, 플래그) → None
      NO_INCOME_ROW         — 손익 행은 있으나 지배·총계 어느 것도 못 잡음(플래그) → None"""
    if not any(r.get("sj_div") in ("IS", "CIS") for r in rows):
        return None, "NODATA"
    for sj in ("IS", "CIS"):
        v, sub = _ctrl_in_sj(rows, sj, _cum_is)
        if v is not None:
            return v, f"{sj}_{sub}"
    if _has_minority_ni(rows):
        return None, "MINORITY_NO_CTRL"  # 비지배 존재 → 총계 폴백 금지(오염 방지)
    for sj in ("IS", "CIS"):
        hit = [r for r in rows if r.get("sj_div") == sj and (r.get("account_id") or "") == "ifrs-full_ProfitLoss"]
        if hit:
            return _cum_is(min(hit, key=_ordv)), "TOTAL"
    return None, "NO_INCOME_ROW"


def extract_controlling_eq(rows):
    """지배자본 기말잔액 → (값, 케이스). 트리:
      NODATA          — BS 없음
      BS_ID / BS_NM   — BS 지배자본(표준 id / 비표준 nm '지배')
      TOTAL           — 지배·비지배 자본 부재(무소수지분/별도 OFS) → 자본총계=지배 [카카오뱅크·001340]
      MINORITY_NO_CTRL — 비지배자본 있는데 지배 못 찾음(플래그) → None
      NO_EQUITY_ROW    — 자본총계 행도 없음(플래그) → None"""
    if not any(r.get("sj_div") == "BS" for r in rows):
        return None, "NODATA"
    hit = [r for r in rows if r.get("sj_div") == "BS" and _EQ_CTRL_ID in (r.get("account_id") or "")]
    if hit:
        return _bal(min(hit, key=_ordv)), "BS_ID"
    cands = [r for r in rows if r.get("sj_div") == "BS" and "지배" in _nm(r) and "비지배" not in _nm(r)]
    if cands:
        return _bal(min(cands, key=_ordv)), "BS_NM"
    if any(r.get("sj_div") == "BS" and "비지배" in _nm(r) for r in rows):
        return None, "MINORITY_NO_CTRL"
    tot = [r for r in rows if r.get("sj_div") == "BS" and (r.get("account_id") or "") == "ifrs-full_Equity"]
    if tot:
        return _bal(min(tot, key=_ordv)), "TOTAL"
    return None, "NO_EQUITY_ROW"


DDL = """CREATE TABLE IF NOT EXISTS mkt_fund_q(
  isu_cd text, fy int, quarter int, reprt_code text, fs text,
  ni_cum double precision, eq double precision, fetched text,
  ni_case text, eq_case text,
  PRIMARY KEY(isu_cd, fy, quarter))"""
DDL_MIGRATE = (
    "ALTER TABLE mkt_fund_q ADD COLUMN IF NOT EXISTS ni_case text",
    "ALTER TABLE mkt_fund_q ADD COLUMN IF NOT EXISTS eq_case text",
)


def _pg():
    return psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=15)


def seed_q4() -> None:
    """Q4(사업보고서) 행을 이미 있는 연간 데이터에서 seed — DART 0콜."""
    con = _pg(); con.autocommit = True
    con.execute(DDL)
    for _m in DDL_MIGRATE:
        con.execute(_m)
    # FY2018~2024: mkt_fund_hist (restated 우선)
    n1 = con.execute("""
        INSERT INTO mkt_fund_q (isu_cd, fy, quarter, reprt_code, fs, ni_cum, eq, fetched)
        SELECT isu_cd, fy, 4, '11011', fs,
               COALESCE(ni_restated, ni), COALESCE(eq_restated, eq),
               CASE WHEN COALESCE(ni_restated,ni) IS NOT NULL OR COALESCE(eq_restated,eq) IS NOT NULL
                    THEN 'ok' ELSE 'nodata' END
        FROM mkt_fund_hist
        WHERE fetched='ok' OR ni_restated IS NOT NULL OR eq_restated IS NOT NULL
        ON CONFLICT (isu_cd, fy, quarter) DO UPDATE SET
          ni_cum=EXCLUDED.ni_cum, eq=EXCLUDED.eq, fetched=EXCLUDED.fetched
    """).rowcount
    # FY2025: mkt_fundamentals (ni_fy=FY2025 연간, eq_fy=FY말자본)
    n2 = con.execute("""
        INSERT INTO mkt_fund_q (isu_cd, fy, quarter, reprt_code, fs, ni_cum, eq, fetched)
        SELECT isu_cd, 2025, 4, '11011', fs, ni_fy, eq_fy,
               CASE WHEN ni_fy IS NOT NULL OR eq_fy IS NOT NULL THEN 'ok' ELSE 'nodata' END
        FROM mkt_fundamentals WHERE fetched='ok'
        ON CONFLICT (isu_cd, fy, quarter) DO UPDATE SET
          ni_cum=EXCLUDED.ni_cum, eq=EXCLUDED.eq, fetched=EXCLUDED.fetched
    """).rowcount
    print(f"Q4 seed: mkt_fund_hist {n1}행 + mkt_fundamentals(FY2025) {n2}행")
    con.close()


def _flush(buf) -> None:
    if not buf:
        return
    with _pg() as c:
        with c.cursor() as cur:
            cur.executemany("""INSERT INTO mkt_fund_q
                (isu_cd, fy, quarter, reprt_code, fs, ni_cum, eq, fetched, ni_case, eq_case)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (isu_cd, fy, quarter) DO UPDATE SET
                  reprt_code=EXCLUDED.reprt_code, fs=EXCLUDED.fs, ni_cum=EXCLUDED.ni_cum,
                  eq=EXCLUDED.eq, fetched=EXCLUDED.fetched, ni_case=EXCLUDED.ni_case,
                  eq_case=EXCLUDED.eq_case""", buf)
        c.commit()
    buf.clear()


_NET = ("ReadError", "ConnectError", "ConnectTimeout", "ReadTimeout", "Timeout", "Operational", "gaierror")


def _is_net(e) -> bool:
    return any(t in type(e).__name__ for t in _NET) or any(t in str(e) for t in _NET)


async def fetch(years, pilot=None, conc=2) -> None:
    """동시성 conc(기본 2, CLAUDE.md 허용) 워커풀. DART client _throttle_api가 910/분 강제하므로
    안전. 큐에서 분기 작업을 꺼내 CFS→OFS + backoff 재시도. 지속 outage면 stop 세팅 → 워커 종료 →
    resume 가능. 버퍼는 lock 보호, flush는 to_thread로 이벤트루프 비차단."""
    from open_proxy_mcp.dart.client import get_dart_client, DartClientError
    con = _pg(); con.autocommit = True
    con.execute(DDL)
    for _m in DDL_MIGRATE:
        con.execute(_m)
    if pilot:
        firms = [r for r in con.execute(
            "SELECT isu_cd, corp_code FROM mkt_fundamentals WHERE isu_cd = ANY(%s) AND fetched='ok'", (pilot,))]
    else:
        firms = [r for r in con.execute(
            "SELECT isu_cd, corp_code FROM mkt_fundamentals WHERE fetched='ok' ORDER BY isu_cd")]
    done = {(r[0], r[1], r[2]) for r in con.execute(
        "SELECT isu_cd, fy, quarter FROM mkt_fund_q WHERE quarter != 4")}  # 분기 단위 resume
    con.close()
    todo = [(i, c, y, q, rc) for i, c in firms for y in years
            for q, rc in QFETCH if (i, y, q) not in done]
    print(f"대상 {len(firms)}사 × {len(years)}년 × 3분기 · 남은 {len(todo)}건 · 동시성 {conc}", flush=True)
    c = get_dart_client()
    buf: list = []; lock = asyncio.Lock(); stop = asyncio.Event()
    prog = {"n": 0}; total = len(todo)
    queue: asyncio.Queue = asyncio.Queue()
    for item in todo:
        queue.put_nowait(item)

    async def acnt(cc, yr, rc, fs):
        try:
            d = await c.get_fnltt_singl_acnt_all(cc, str(yr), rc, fs)
            return (d.get("list") or []) if isinstance(d, dict) else []
        except DartClientError as e:
            if "[013]" in str(e):
                return []
            raise

    async def fetch_rows(cc, yr, rc):
        for attempt in range(9):  # ~5분 outage까지 견딤
            try:
                fs = "CFS"; rows = await acnt(cc, yr, rc, fs); await asyncio.sleep(SLEEP)
                if not rows:
                    fs = "OFS"; rows = await acnt(cc, yr, rc, fs); await asyncio.sleep(SLEEP)
                return fs, rows
            except Exception as ne:
                if _is_net(ne) and attempt < 8:
                    await asyncio.sleep(min(60, 5 * 2 ** attempt)); continue
                raise

    async def maybe_flush(force=False):
        async with lock:
            if buf and (force or len(buf) >= 25):
                snap = buf[:]; buf.clear()
                await asyncio.to_thread(_flush, snap)

    async def worker():
        while not stop.is_set():
            try:
                isu, cc, yr, q, rc = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                fs, rows = await fetch_rows(cc, yr, rc)
            except Exception as e:
                if _is_net(e):
                    stop.set(); print(f"네트워크({type(e).__name__}) — 중단(재개 가능)", flush=True); return
                async with lock:
                    buf.append((isu, yr, q, rc, None, None, None, f"err:{str(e)[:30]}", "ERR", "ERR"))
            else:
                ni, ni_case = extract_controlling_ni_cum(rows)
                eq, eq_case = extract_controlling_eq(rows)
                assets = gid_exact(rows, "ifrs-full_Assets", ("BS",))
                liab = gid_exact(rows, "ifrs-full_Liabilities", ("BS",))
                eq_total = gid_exact(rows, "ifrs-full_Equity", ("BS",))
                v = scale_assess(thstrm=ni, assets=assets, liabilities=liab, equity=eq_total)
                if v["tier"] == "hard":
                    print(f"[가드] {isu} {yr}Q{q} 스케일오류({v['hard_hit']}) — 무효화", flush=True)
                    ni = eq = None; ni_case = eq_case = "SCALE_GUARD"
                st = "ok" if (ni is not None or eq is not None) else "nodata"
                async with lock:
                    buf.append((isu, yr, q, rc, fs, ni, eq, st, ni_case, eq_case))
            prog["n"] += 1
            if prog["n"] % 300 == 0:
                print(f"{prog['n']}/{total}", flush=True)
            await maybe_flush()

    await asyncio.gather(*[asyncio.create_task(worker()) for _ in range(conc)])
    try:
        await maybe_flush(force=True)
    except Exception as e:
        print(f"최종 flush 실패({type(e).__name__}) — 재개 시 복구", flush=True)
    print("fetch 종료", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true", help="Q4 seed(0콜)")
    ap.add_argument("--pilot", type=str, help="소표본 isu_cd 콤마구분(전분기 수집·검증)")
    ap.add_argument("--fetch", action="store_true", help="Q1~Q3 백필")
    ap.add_argument("--years", type=str, help="콤마구분 연도(기본 2018~2025)")
    ap.add_argument("--conc", type=int, default=int(os.getenv("FUND_Q_CONC", "2")),
                    help="동시성(기본 2, CLAUDE.md 허용 1~2)")
    a = ap.parse_args()
    yrs = [int(y) for y in a.years.split(",")] if a.years else YEARS_DEFAULT
    conc = max(1, min(2, a.conc))  # 하드 상한 2(910 한도·CLAUDE.md 준수)
    if a.seed:
        seed_q4()
    if a.pilot:
        seed_q4()
        asyncio.run(fetch(yrs, pilot=a.pilot.split(","), conc=conc))
    elif a.fetch:
        asyncio.run(fetch(yrs, conc=conc))
