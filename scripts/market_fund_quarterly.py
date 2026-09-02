"""분기 재무 시계열 저장소(dart_finstat_q) — firm/market/sector 밸류 밴드의 분기 granularity 원천.

설계(260705, 사용자 확정): "주간 가격 × **분기 재무** × 분기말 환율". 분기 재무가 있어야 과거
TTM PER(최근 4분기 지배순이익 합)·MRQ PBR(최근분기 지배자본)을 시계열로 산출 — 연간(dart_finstat_y)만
있어서 밴드 TTM이 N/A였던 한계를 해소.

키: (ticker, fy, quarter). quarter 1/2/3/4(=사업보고서). 저장:
  ni_cum = 지배순이익 **누적(YTD)** — TTM = FY(y-1) + ni_cum(y,q) − ni_cum(y-1,q).
  eq     = 지배자본 **기말 잔액**(BS, 기간무관).

수집 절약: **Q4(사업보고서)는 이미 있는 연간 데이터에서 seed(DART 0콜)** —
  · 과거 FY = dart_finstat_y(ni=연간누적=Q4누적, eq=FY말자본, restated 우선)
  · 최신 확정 FY(_latest_annual_fy) = dart_fundamentals(ni_fy, eq_fy) — ni_fy/eq_fy는 가변열
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
  python3 scripts/market_fund_quarterly.py --include-new [--fetch]
      # 신규 상장 보통주를 dart_fundamentals 에 등록(KRX 2~4콜 + DART 0콜). 옛 시장 aggregate 수집기(--fetch)의
      # 유일한 잔존 기능(260902 흡수·원본은 open-proxy-storage archive/opm-scripts). 재무 4열은 넣지 않는다 —
      # 이후 --fetch(분기)·market_val_series --fetch
      # (연간)·--derive 가 채운다. --fetch 와 같이 주면 등록 직후 그 종목의 분기 수집까지 이어진다.
"""
import argparse, asyncio, os, sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
import psycopg
from open_proxy_mcp.services.scale_guard import gid_exact, assess as scale_assess
from open_proxy_mcp.services.financial_metrics import normalize_amount
from open_proxy_mcp.dart.fx import statement_currency, fx_to_krw

_QEND = {1: "0331", 2: "0630", 3: "0930", 4: "1231"}

# 분기 수집연도: 2019~현재. 2018 분기는 2019 TTM에만 필요(→ 2020~ 추이엔 불필요)해 제외 —
# 2018은 seed_q4가 mkt_finstat_y에서 Q4(연간)만 seed. TTM은 2019 동분기가 있어 2020~부터 산출.
YEARS_DEFAULT = [2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026]
# (quarter, reprt_code) — Q1/반기/3Q만 DART 수집(Q4는 seed)
QFETCH = [(1, "11013"), (2, "11012"), (3, "11014")]
# 분기 공시 마감(그 이후 available): 1Q 5/15 · 반기 8/14 · 3Q 11/14. 아직 공시 전 분기는 수집 제외
# (미래 분기 [013] nodata 낭비 방지 + 현재연도 자동 대응). 실행 시점(date.today) 기준.
_Q_DEADLINE = {1: (5, 15), 2: (8, 14), 3: (11, 14)}


def _disclosed(fy: int, q: int, today: date | None = None) -> bool:
    mo, dy = _Q_DEADLINE[q]
    return (today or date.today()) >= date(fy, mo, dy)


def _latest_annual_fy(today: date | None = None) -> int:
    """그 시점 확정된 최신 사업연도(사업보고서 3월 공시 → 4월 이후 전년, 아니면 전전년). _pit_fy와 동일."""
    t = today or date.today()
    return t.year - 1 if t.month >= 4 else t.year - 2
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


def _first(rows, pred, val):
    """조건 pred 만족 행 중 ord 최선두 → val(r), 없으면 None. (id/taxonomy 무관 매칭용)"""
    cands = [r for r in rows if pred(r)]
    return val(min(cands, key=_ordv)) if cands else None


def _total_ni(rows):
    """당기순이익 총계(지배+비지배). 구·신 taxonomy(ifrs_ProfitLoss / ifrs-full_ProfitLoss) 모두."""
    for sj in ("IS", "CIS"):
        v = _first(rows, lambda r: r.get("sj_div") == sj and (r.get("account_id") or "").endswith("_ProfitLoss"), _cum_is)
        if v is not None:
            return v
    return None


def _minority_ni(rows):
    """비지배 당기순이익(총포괄 비지배는 ComprehensiveIncome id로 배제)."""
    for sj in ("IS", "CIS"):
        v = _first(rows, lambda r: r.get("sj_div") == sj and "ProfitLossAttributableToNoncontrolling" in (r.get("account_id") or ""), _cum_is)
        if v is not None:
            return v
    for sj in ("IS", "CIS"):
        v = _first(rows, lambda r: r.get("sj_div") == sj and "비지배" in _nm(r) and "ComprehensiveIncome" not in (r.get("account_id") or ""), _cum_is)
        if v is not None:
            return v
    return None


def extract_controlling_ni_cum(rows):
    """지배 당기순이익 누적 → (값, 케이스). 트리:
      NODATA · IS_ID/IS_NM · CIS_ID/CIS_NM — 명시 지배행 [LG화학·삼성·기아]
      TOTAL_MINUS_MINORITY — 명시 지배행 없으나 당기순이익 총계 − 비지배순이익 [유한양행형: 서브토탈 부재]
      TOTAL                — 지배·비지배 귀속 둘 다 부재 → 당기순이익 총계=지배 [001340·NAVER 무소수지분]
      MINORITY_NO_CTRL / NO_INCOME_ROW — 유도 불가(플래그) → None"""
    if not any(r.get("sj_div") in ("IS", "CIS") for r in rows):
        return None, "NODATA"
    for sj in ("IS", "CIS"):
        v, sub = _ctrl_in_sj(rows, sj, _cum_is)
        if v is not None:
            return v, f"{sj}_{sub}"
    tot, mino = _total_ni(rows), _minority_ni(rows)
    if tot is not None:
        return (tot - mino, "TOTAL_MINUS_MINORITY") if mino is not None else (tot, "TOTAL")
    return (None, "MINORITY_NO_CTRL") if mino is not None else (None, "NO_INCOME_ROW")


def _total_eq(rows):
    v = _first(rows, lambda r: r.get("sj_div") == "BS" and (r.get("account_id") or "").endswith("_Equity"), _bal)
    if v is not None:
        return v
    return _first(rows, lambda r: r.get("sj_div") == "BS" and _nm(r) == "자본총계", _bal)


def _minority_eq(rows):
    v = _first(rows, lambda r: r.get("sj_div") == "BS" and "NoncontrollingInterests" in (r.get("account_id") or ""), _bal)
    if v is not None:
        return v
    return _first(rows, lambda r: r.get("sj_div") == "BS" and "비지배" in _nm(r), _bal)


def extract_controlling_eq(rows):
    """지배자본 기말잔액 → (값, 케이스). 트리:
      NODATA · BS_ID/BS_NM — 명시 지배자본행
      TOTAL_MINUS_MINORITY — 명시 지배행 없으나 자본총계 − 비지배지분 [유한양행형: 서브토탈 부재]
      TOTAL                — 지배·비지배 자본 둘 다 부재 → 자본총계=지배 [카카오뱅크 별도·001340]
      MINORITY_NO_CTRL / NO_EQUITY_ROW — 유도 불가(플래그) → None"""
    if not any(r.get("sj_div") == "BS" for r in rows):
        return None, "NODATA"
    hit = [r for r in rows if r.get("sj_div") == "BS" and _EQ_CTRL_ID in (r.get("account_id") or "")]
    if hit:
        return _bal(min(hit, key=_ordv)), "BS_ID"
    cands = [r for r in rows if r.get("sj_div") == "BS" and "지배" in _nm(r) and "비지배" not in _nm(r)]
    if cands:
        return _bal(min(cands, key=_ordv)), "BS_NM"
    tot, mino = _total_eq(rows), _minority_eq(rows)
    if tot is not None:
        return (tot - mino, "TOTAL_MINUS_MINORITY") if mino is not None else (tot, "TOTAL")
    return (None, "MINORITY_NO_CTRL") if mino is not None else (None, "NO_EQUITY_ROW")


DDL = """CREATE TABLE IF NOT EXISTS dart_finstat_q(
  ticker text, fy int, quarter int, reprt_code text, fs text,
  ni_cum double precision, eq double precision, fetched text,
  ni_case text, eq_case text,
  PRIMARY KEY(ticker, fy, quarter))"""
DDL_MIGRATE = (
    "ALTER TABLE dart_finstat_q ADD COLUMN IF NOT EXISTS ni_case text",
    "ALTER TABLE dart_finstat_q ADD COLUMN IF NOT EXISTS eq_case text",
)


def _pg():
    return psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=15)


def derive_fundamentals() -> None:
    """dart_fundamentals 재무 4열(ni_fy·ni_ttm·eq_fy·eq_mrq)을 dart_finstat_q(분기+Q4연간)에서 파생 —
    **DART 0콜**. SSOT = dart_finstat_q. 원통화 raw 유지(daily cron이 fx_rate로 KRW 환산). 최신 공시분기
    기준 TTM/MRQ라 분기 공시마다 자동 최신화(옛 시장 aggregate 수집기는 1Q 고정·done셋이라 갱신 불가였음 — 260902 삭제).
      ni_fy/eq_fy = 최신 완결 FY(Q4) · eq_mrq = 최신 분기 자본 · ni_ttm = FY(y-1)+누적(y,q)−누적(y-1,q)."""
    from collections import defaultdict
    con = _pg(); con.autocommit = True
    q: dict[str, dict] = defaultdict(dict)
    for isu, fy, quarter, ni, eq in con.execute(
            "SELECT ticker, fy, quarter, COALESCE(ni_cum_restated, ni_cum), COALESCE(eq_restated, eq) FROM dart_finstat_q"):
        q[isu][(int(fy), int(quarter))] = (ni, eq)
    updated = 0
    for isu, m in q.items():
        # ni·eq 앵커를 **독립**으로(Data-QA #2): 최신분기가 eq만 있고 ni=None이어도 ni_ttm이 불필요하게
        # None 되지 않게 — 각 지표의 최신 유효분기 기준. 값 있는 분기가 곧 available(저장=공시분).
        ni_present = [(fy, qq) for (fy, qq), (ni, eq) in m.items() if ni is not None]
        eq_present = [(fy, qq) for (fy, qq), (ni, eq) in m.items() if eq is not None]
        if not ni_present and not eq_present:
            continue
        if not any(qq != 4 for (fy, qq) in ni_present + eq_present):
            continue   # 분기(Q1-3) 미수집 = Q4 seed만 → 기존 dart_fundamentals 유지(백필 중 안전)
        fys4 = [fy for (fy, qq), (ni, eq) in m.items() if qq == 4 and (ni is not None or eq is not None)]
        fy_full = max(fys4) if fys4 else None                    # 최신 완결 사업연도
        ni_fy, eq_fy = m.get((fy_full, 4), (None, None)) if fy_full else (None, None)
        eq_mrq = m[max(eq_present)][1] if eq_present else None    # eq 있는 최신분기
        ni_ttm = None
        if ni_present:
            lfy, lq = max(ni_present)                             # ni 있는 최신분기 기준 TTM
            if lq == 4:
                ni_ttm = m.get((lfy, 4), (None, None))[0]
            else:
                cum_now = m.get((lfy, lq), (None, None))[0]
                cum_prev = m.get((lfy - 1, lq), (None, None))[0]
                fy_prev = m.get((lfy - 1, 4), (None, None))[0]
                ni_ttm = (fy_prev + cum_now - cum_prev) if None not in (cum_now, cum_prev, fy_prev) else None
        con.execute("UPDATE dart_fundamentals SET ni_fy=%s, ni_ttm=%s, eq_fy=%s, eq_mrq=%s WHERE ticker=%s",
                    (ni_fy, ni_ttm, eq_fy, eq_mrq, isu))
        updated += 1
    con.close()
    print(f"derive: dart_fundamentals {updated}사 재무 4열 파생 갱신(DART 0콜)", flush=True)


DDL_FUNDAMENTALS = """CREATE TABLE IF NOT EXISTS dart_fundamentals(
  ticker text PRIMARY KEY, corp_code text, market text, fs text,
  ni_fy double precision, ni_ttm double precision,
  eq_fy double precision, eq_mrq double precision, fetched text)"""


async def include_new_tickers() -> int:
    """KRX 최신 거래일 상장 **보통주** 가운데 dart_fundamentals 에 없는 종목을 등록한다 — 옛 시장 aggregate
    수집기(--fetch)가 하던 유일한 잔존 역할(260902 흡수). 이 표의 `fetched='ok'` 행이 분기(fetch)·연간
    (market_val_series)·주간 스냅샷(market_val_weekly) 세 수집기의 대상 집합이라, 여기 없는 신규 상장사는
    어디서도 안 잡힌다(구 스크립트는 done 셋 고정이라 갱신 불가였다).

    재무 4열은 쓰지 않는다(NULL) — SSOT 는 dart_finstat_q 이고 파생(--derive)이 채운다. 옛 방식(1Q 고정
    직접 수집)을 되살리지 않는다. corp_code 미해결이면 fetched='nocorp' 로 남겨 재시도 대상에서 뺀다.
    콜: KRX ≤4(시세 2 는 _ensure_krx_fresh 가 이미 DB 에 있으면 0, 종목유형 2) · DART 0(corpCode 캐시).
    """
    from open_proxy_mcp.services.price_multiple_data import _ensure_krx_fresh
    from market_val_weekly import _krx_kinds          # 같은 폴더 — 우선주/보통주 판별(KRX isu_base_info 2콜)
    from open_proxy_mcp.dart.client import get_dart_client
    snap_dd = await _ensure_krx_fresh()
    if not snap_dd:
        print("include-new: KRX 최신 거래일 확보 실패 — 건너뜀", flush=True)
        return 0
    kinds = await _krx_kinds(snap_dd)
    if not kinds:
        print("include-new: KRX 종목유형(kinds) 확보 실패 — 건너뜀(우선주를 보통주로 넣지 않기 위해)", flush=True)
        return 0
    con = _pg(); con.autocommit = True
    con.execute(DDL_FUNDAMENTALS)
    known = {r[0] for r in con.execute("SELECT ticker FROM dart_fundamentals")}
    listed = con.execute("SELECT ticker, market FROM krx_weekly WHERE price_dd=%s", (snap_dd,)).fetchall()
    new = [(t, m) for t, m in listed if t not in known and kinds.get(t) == "보통주"]
    print(f"include-new: {snap_dd} 상장 {len(listed)} · 기등록 {len(known)} · 신규 보통주 {len(new)}", flush=True)
    c = get_dart_client()
    n_ok = n_nocorp = 0
    for ticker, market in new:
        corp = await c.lookup_corp_code(ticker)
        if corp and corp.get("corp_code"):
            con.execute("INSERT INTO dart_fundamentals(ticker, corp_code, market, fetched) "
                        "VALUES(%s, %s, %s, 'ok') ON CONFLICT (ticker) DO NOTHING",
                        (ticker, corp["corp_code"], market))
            n_ok += 1
        else:
            con.execute("INSERT INTO dart_fundamentals(ticker, market, fetched) "
                        "VALUES(%s, %s, 'nocorp') ON CONFLICT (ticker) DO NOTHING", (ticker, market))
            n_nocorp += 1
    # 양쪽으로 센다(CLAUDE.md 13): 새 행 N건이 실제로 들어갔는지.
    after = con.execute("SELECT count(*) FROM dart_fundamentals").fetchone()[0]
    con.close()
    print(f"include-new: 등록 {n_ok}(ok) + {n_nocorp}(nocorp) → dart_fundamentals {len(known)} → {after}행", flush=True)
    return n_ok


def seed_q4() -> None:
    """Q4(사업보고서) 행을 이미 있는 연간 데이터에서 seed — DART 0콜."""
    con = _pg(); con.autocommit = True
    con.execute(DDL)
    for _m in DDL_MIGRATE:
        con.execute(_m)
    # FY2018~2024: dart_finstat_y (restated 우선)
    n1 = con.execute("""
        INSERT INTO dart_finstat_q (ticker, fy, quarter, reprt_code, fs, ni_cum, eq, fetched)
        SELECT ticker, fy, 4, '11011', fs,
               COALESCE(ni_restated, ni), COALESCE(eq_restated, eq),
               CASE WHEN COALESCE(ni_restated,ni) IS NOT NULL OR COALESCE(eq_restated,eq) IS NOT NULL
                    THEN 'ok' ELSE 'nodata' END
        FROM dart_finstat_y
        WHERE fetched='ok' OR ni_restated IS NOT NULL OR eq_restated IS NOT NULL
        ON CONFLICT (ticker, fy, quarter) DO UPDATE SET
          ni_cum=EXCLUDED.ni_cum, eq=EXCLUDED.eq, fetched=EXCLUDED.fetched
    """).rowcount
    # 최신 완결 FY가 아직 mkt_finstat_y에 없을 때만 mkt_fundamentals에서 **bootstrap**(INSERT-only).
    # ⚠ dart_fundamentals.ni_fy/eq_fy는 derive_fundamentals가 '최신 완결 FY'로 덮어쓰는 가변열 →
    # 과거 특정연도(하드코딩 2025)에 DO UPDATE로 쓰면 롤오버 시 그 값이 다음 FY로 바뀌어 mkt_finstat_q의
    # 과거 Q4가 오염되고 TTM이 조용히 틀어짐(Data-QA #1). 방지: 대상 FY 동적(_latest_annual_fy) +
    # hist에 없을 때만 + **DO NOTHING**(hist가 들어오면 n1이 authoritative, bootstrap은 절대 덮지 않음).
    lfy = _latest_annual_fy()
    in_hist = con.execute("SELECT 1 FROM dart_finstat_y WHERE fy=%s LIMIT 1", (lfy,)).fetchone()
    n2 = 0
    if not in_hist:
        n2 = con.execute("""
            INSERT INTO dart_finstat_q (ticker, fy, quarter, reprt_code, fs, ni_cum, eq, fetched)
            SELECT ticker, %s, 4, '11011', fs, ni_fy, eq_fy,
                   CASE WHEN ni_fy IS NOT NULL OR eq_fy IS NOT NULL THEN 'ok' ELSE 'nodata' END
            FROM dart_fundamentals WHERE fetched='ok'
            ON CONFLICT (ticker, fy, quarter) DO NOTHING
        """, (lfy,)).rowcount
    print(f"Q4 seed: dart_finstat_y {n1}행 + dart_fundamentals bootstrap(FY{lfy}, hist부재시만) {n2}행")
    con.close()


def _flush(buf) -> None:
    if not buf:
        return
    with _pg() as c:
        with c.cursor() as cur:
            cur.executemany("""INSERT INTO dart_finstat_q
                (ticker, fy, quarter, reprt_code, fs, ni_cum, eq, fetched, ni_case, eq_case)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (ticker, fy, quarter) DO UPDATE SET
                  reprt_code=EXCLUDED.reprt_code, fs=EXCLUDED.fs, ni_cum=EXCLUDED.ni_cum,
                  eq=EXCLUDED.eq, fetched=EXCLUDED.fetched, ni_case=EXCLUDED.ni_case,
                  eq_case=EXCLUDED.eq_case""", buf)
        c.commit()
    buf.clear()


_NET = ("ReadError", "ConnectError", "ConnectTimeout", "ReadTimeout", "Timeout", "Operational", "gaierror")


def _is_net(e) -> bool:
    return any(t in type(e).__name__ for t in _NET) or any(t in str(e) for t in _NET)


def _self_ref_map(con) -> dict:
    """종목 → 자기 규모의 자(尺). 연간 자본(정정치 우선)의 중앙값과 최근값 중 큰 쪽.

    260829 신설. 종전 가드는 시장 최댓값(삼성전자)을 앵커로 써서 「삼성보다 큰가」를 물었다 —
    소형주의 1,000배 오류는 통과시키고 대형주의 정상 실적은 오탐했다. 회사마다 자를 따로 든다.
    """
    import collections
    import statistics as _st
    ann = collections.defaultdict(list)
    for t, fy, eq, eqr in con.execute(
            "SELECT ticker, fy, eq, eq_restated FROM dart_finstat_y ORDER BY fy"):
        v = eqr if eqr is not None else eq
        if v:
            ann[t].append(abs(v))
    return {t: max(_st.median(vs), vs[-1]) for t, vs in ann.items() if vs}


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
            "SELECT ticker, corp_code FROM dart_fundamentals WHERE ticker = ANY(%s) AND fetched='ok'", (pilot,))]
    else:
        firms = [r for r in con.execute(
            "SELECT ticker, corp_code FROM dart_fundamentals WHERE fetched='ok' ORDER BY ticker")]
    # 분기 단위 resume — **ok/nodata만 완료로 간주**. err(일일한도[020]·기타 실패)는 done에서 제외 →
    # 재실행 시 자동 재수집(DART 일일한도 20k/키 리셋 후 남은분 채움). DO UPDATE로 err행 덮어씀.
    done = {(r[0], r[1], r[2]) for r in con.execute(
        "SELECT ticker, fy, quarter FROM dart_finstat_q WHERE quarter != 4 "
        "AND (fetched IS NULL OR fetched NOT LIKE 'err:%')")}
    # 260829: 가드에 넘길 「그 회사 자신의 자」 — 연간 자본의 중앙값과 가장 최근 연간 자본 중 큰 쪽.
    #   중앙값만 쓰면 실제로 커진 회사(증자)를 오탐한다. 실측 74,918행에서 오탐 0·목표 8행 전부 적중.
    self_ref = _self_ref_map(con)
    # 260829(마스터 착안): 그 보고서의 「전기」 칸과 대조할 **우리가 아는 그 기간의 값**.
    #   분기보고서의 전기 = 직전 사업연도말이므로 연간표에서 (종목, fy-1) 로 찾는다.
    prev_eq = {(r[0], int(r[1])): (r[3] if r[3] is not None else r[2])
               for r in con.execute("SELECT ticker, fy, eq, eq_restated FROM dart_finstat_y")
               if (r[3] if r[3] is not None else r[2])}
    con.close()
    todo = [(i, c, y, q, rc) for i, c in firms for y in years
            for q, rc in QFETCH if (i, y, q) not in done and _disclosed(y, q)]
    yspan = sorted({y for _, _, y, _, _ in todo})
    print(f"대상 {len(firms)}사 · 공시완료 분기만 · 남은 {len(todo)}건(연도 {yspan}) · 동시성 {conc}", flush=True)
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
                # 전기 칸(직전 사업연도말) — 같은 기간의 같은 숫자라 우리 보유값과 일치해야 한다.
                eq_frm = gid_exact(rows, "ifrs-full_Equity", ("BS",), field="frmtrm_amount")
                # 통화 근본해법(260706): 그 분기 응답 자체에서 통화 감지 후 KRW 환산(저장은 항상 KRW) —
                # market_val_series.py 연간 fetch와 동일 패턴(statement_currency+fx_to_krw) 재사용.
                stmt_cur = statement_currency(rows)
                fx_err = None
                if stmt_cur != "KRW":
                    ecos_fx_rate = await fx_to_krw(stmt_cur, f"{yr}{_QEND[q]}")
                    if ecos_fx_rate is None:
                        fx_err = f"err:fx_{stmt_cur}"
                    else:
                        def _fx(x): return x * ecos_fx_rate if x is not None else None
                        ni, eq, assets, liab, eq_total = (_fx(v) for v in (ni, eq, assets, liab, eq_total))
                if fx_err:
                    async with lock:
                        buf.append((isu, yr, q, rc, None, None, None, fx_err, "ERR", "ERR"))
                else:
                    v = scale_assess(thstrm=ni, assets=assets, liabilities=liab, equity=eq_total,
                                     self_ref=self_ref.get(isu),
                                     frmtrm_equity=eq_frm, known_prev_equity=prev_eq.get((isu, yr - 1)))
                    if v["tier"] == "hard":
                        pm = v["diagnostics"].get("prior_period_mismatch") or {}
                        if pm.get("triggered"):
                            # 배수를 알면 무효화 대신 **고쳐서** 담는다 — 보고서가 통째로 틀리므로
                            # 전기 칸에서 나온 배수가 당기 값에도 그대로 적용된다(260829 실측 7/7).
                            d = pm["fix_divisor"]
                            print(f"[가드] {isu} {yr}Q{q} 단위오류 ÷{d:,} — 전기칸 대조로 복구", flush=True)
                            ni = ni / d if ni is not None else None
                            eq = eq / d if eq is not None else None
                            ni_case = eq_case = f"SCALE_FIXED_DIV{d}"
                        else:
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
    ap.add_argument("--pilot", type=str, help="소표본 ticker 콤마구분(전분기 수집·검증)")
    ap.add_argument("--fetch", action="store_true", help="Q1~Q3 백필")
    ap.add_argument("--derive", action="store_true", help="dart_fundamentals 재무 4열 파생(0콜)")
    ap.add_argument("--include-new", action="store_true",
                    help="KRX 신규 상장 보통주를 dart_fundamentals 에 등록(KRX ≤4콜·DART 0콜) — --fetch 앞에 수행")
    ap.add_argument("--years", type=str, help="콤마구분 연도(기본 2019~2026)")
    ap.add_argument("--conc", type=int, default=int(os.getenv("FUND_Q_CONC", "2")),
                    help="동시성(기본 2, CLAUDE.md 허용 1~2)")
    a = ap.parse_args()
    yrs = [int(y) for y in a.years.split(",")] if a.years else YEARS_DEFAULT
    conc = max(1, min(2, a.conc))  # 하드 상한 2(910 한도·CLAUDE.md 준수)
    if a.include_new:
        asyncio.run(include_new_tickers())   # fetch 가 firms 를 DB 에서 다시 읽으므로 같은 실행에서 이어진다
    if a.seed:
        seed_q4()
    if a.derive:
        derive_fundamentals()
    if a.pilot:
        seed_q4()
        asyncio.run(fetch(yrs, pilot=a.pilot.split(","), conc=conc))
    elif a.fetch:
        asyncio.run(fetch(yrs, conc=conc))
