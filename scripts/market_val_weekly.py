"""주간 밸류에이션 스냅샷 배치 — DB-first 서빙의 갱신자 (cron 대상).

한 번 실행이 하는 일 (KRX 4콜 + DART 0콜 + ECOS 0콜):
  A. krx_weekly 갱신 — 최신 거래일 전종목 스냅샷, 같은 ISO주 수렴(valuation 공용 로직 재사용)
  B. opm_val_firm  — 종목별 PER/PBR 주간 스냅샷 (krx_weekly 시총 × dart_fundamentals 재무)
  C. opm_val_market — 시장 전체(KOSPI/KOSDAQ) 시총가중 aggregate (우선주 시총 보통주 귀속)
  D. opm_val_market — 시장 전체(scheme='market') 만. 섹터 집계는 WICS 로 이관(260823).

방법론 (260705 확정 — 보통주 기준):
  PER = Σ**보통주** 시총 ÷ Σ지배순이익(TTM) — KRX 지수 PER 관행. PBR = Σ보통주 시총 ÷ Σ지배자본(MRQ).
  우선주 시총은 배수에서 제외하되 cap_pref로 **별도 저장·노출**(분모의 이익·자본엔 우선주 몫이
  포함되므로 배수는 소폭 하향 편향 — 클래스별 이익·자본 분리는 공시 부재로 불가, 명시로 처리).
  종목별 PER = cap(보통주)÷ni_ttm (ni≤0 → N/M=NULL), PBR = cap÷eq_mrq (eq≤0 → NULL).
  섹터 분류 = KSIC 하이브리드(opm_sector_map.json). ※ WI26은 내부 분석 전용 — 제품/저장 탑재 금지.

통화(260705 실측 버그 수정): mkt_fundamentals의 비KRW 22사(USD/CNY/JPY)는 재무가 원통화 저장
  → ecos_fx_rate(Supabase 캐시, FY 기말환율)로 KRW 환산 후 합산/배수 산출. 캐시 미스 시 그 종목 제외+경고.

수렴 규칙(무료티어 보호): 모든 스냅샷 테이블은 같은 ISO주의 옛 snap_dd를 지우고 기록
  → 주중 매일 돌려도 주당 1스냅샷, 주 마지막 거래일로 굳음. (fx·krx_weekly와 동일 패턴)

실행: python3 scripts/market_val_weekly.py            # 전체 A→D
      python3 scripts/market_val_weekly.py --dry      # B~D 저장 생략(산출만) — A(krx_weekly 갱신)·DDL은 수행
      python3 scripts/market_val_weekly.py --backfill [--since 20151201] [--limit-weeks N]
          # krx_weekly 과거 주간 백필만(스냅샷 B~D 는 돌지 않는다). 옛 KRX 주간 적재 스크립트의 --backfill 흡수
          # (260902 · 원본은 open-proxy-storage archive/opm-scripts).
          # 완결된 ISO주마다 금→월 순으로 그 주 마지막 거래일을 찾아 전종목 적재. 주 단위 commit·기적재 주 skip
          # 이라 재개 가능. KRX 일 10,000콜 한도 — 콜 간 0.35s + 9,000콜에서 강제 중단.
          # (구 --update 는 A 단계 _ensure_krx_fresh 가 매일 담당하므로 없앴다.)

※ 스냅샷 저장의 단일 정본. 시장 aggregate 를 FX(비KRW 22사) 환산 없이 만들던 옛 경로
  (옛 aggregate --report/--snapshot·market_val_sector.py, 둘 다 삭제)는 저장에 쓰지 않는다 — QA 260705.
"""
import argparse, asyncio, json, os, ssl, sys, time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from open_proxy_mcp.market_codes import KS as MKT_KS, KQ as MKT_KQ
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
import httpx, psycopg

from open_proxy_mcp.dart.fx import fx_to_krw
from open_proxy_mcp.services.price_multiple_data import _ensure_krx_fresh, _iso_wk_range

FY = 2025
FY_END = f"{FY}1231"  # 비KRW 재무 환산 기준(회계기말 환율) — valuation tool과 동일 규칙

DDL_FIRM = """CREATE TABLE IF NOT EXISTS opm_val_firm(
  snap_dd text, ticker text, market text, sector text, cap double precision, cap_pref double precision,
  per_fy0 double precision, per_ttm double precision,
  pbr_fy0 double precision, pbr_mrq double precision,
  PRIMARY KEY(snap_dd, ticker))"""
# 260706 병합 A: opm_val_market 하나가 시장전체(sector='_ALL')+섹터별 행을 함께 담음.
# sector는 NOT NULL(PK 포함, PG는 PK에 NULL 불가+UNIQUE는 NULL≠NULL이라 중복 누적) — 센티넬 '_ALL'.
DDL_MKT = """CREATE TABLE IF NOT EXISTS opm_val_market(
  snap_dd text, market text, sector text NOT NULL DEFAULT '_ALL', label text, n int,
  per_fy0 double precision, per_ttm double precision,
  pbr_fy0 double precision, pbr_mrq double precision,
  cap double precision, cap_pref double precision, ni_ttm double precision, eq double precision,
  PRIMARY KEY(snap_dd, market, sector))"""
DDL_MIGRATE = (
    "ALTER TABLE opm_val_firm ADD COLUMN IF NOT EXISTS cap_pref double precision",
    "ALTER TABLE opm_val_market ADD COLUMN IF NOT EXISTS cap_pref double precision",
    # 섹터 밴드 통일(260705): 과거 연말은 FY0만 산출 가능 → sector 행에도 per_fy0/pbr_fy0 저장해
    # 현재(주간)·과거를 FY0 단일 기준으로 비교. 주간행은 per_ttm/pbr_mrq도 함께 채움.
    "ALTER TABLE opm_val_market ADD COLUMN IF NOT EXISTS per_fy0 double precision",
    "ALTER TABLE opm_val_market ADD COLUMN IF NOT EXISTS pbr_fy0 double precision",
    # 260829: per_fy0 가 비는 이유를 「적자」와 「자료없음」으로 가르기 위한 분모 보존.
    #   종전엔 둘 다 NULL 이라 사용자에게 N/M 하나로 뭉개져 나갔다(실측: 20260828 섹터 11칸이
    #   전부 적자였는데 결측으로 오해). ni_ttm 은 이미 있었고 FY0 쪽만 없었다.
    "ALTER TABLE opm_val_market ADD COLUMN IF NOT EXISTS ni_fy0 double precision",
)

# 섹터 버킷 — KSIC 하이브리드 규칙 (구 market_val_sector.py 에서 이관, 260902 삭제)
_smap = json.load(open(ROOT / "open_proxy_mcp/data/ksic/opm_sector_map.json"))
_ksic = json.load(open(ROOT / "open_proxy_mcp/data/ksic/ksic10_ko.json"))
_L3 = set(_smap["level3_prefixes"]); _OVR = _smap["display_overrides"]
_OVR_CODE = _smap.get("code_overrides", {}); _REASSIGN = _smap.get("prefix_reassign", {})
MINB = _smap["min_bucket_firms"]


def bucket(ind: str, isu: str | None = None) -> str:
    if isu and isu in _OVR_CODE:
        return _OVR_CODE[isu]
    for pref, dest in _REASSIGN.items():
        if ind.startswith(pref):
            return dest
    return ind[:3] if ind[:2] in _L3 else ind[:2]


def label(code: str) -> str:
    return _OVR.get(code) or f"{_ksic.get(code, '?')[:14]}({code})"


async def _krx_kinds(price_dd: str) -> dict[str, str]:
    """종목 유형(보통주/우선주) — isu_base_info 2콜. 우선주 시총 귀속용."""
    key = os.getenv("KRX_API_KEY") or os.getenv("KRX_OPEN_API_KEY")
    from open_proxy_mcp.dart.krx_meter import bump
    kinds: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=30) as h:
        for ep in ("stk_isu_base_info", "ksq_isu_base_info"):
            try:
                bump()
                r = await h.get(f"https://data-dbg.krx.co.kr/svc/apis/sto/{ep}",
                                headers={"AUTH_KEY": key}, params={"basDd": price_dd})
                for row in next(v for v in r.json().values() if isinstance(v, list)):
                    kinds[row["ISU_SRT_CD"]] = row.get("KIND_STKCERT_TP_NM", "")
            except Exception:
                pass
    return kinds


# ── krx_weekly 과거 백필 (옛 KRX 주간 적재 스크립트의 --backfill, 260902 흡수) ─────────────────────
# KRX Open API 이용 한도(openapi.krx.co.kr, 2026-07 확인): 키당 1일 10,000회 이하, 전일 데이터는 익일
# 오전 8시 갱신, 비상업적 목적 한정(원시세 재배포 금지 — 배수 산출 인풋으로만). DART 910/분과는 별개 채널.
KRX_ENDPOINTS = [
    (MKT_KS, "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"),
    (MKT_KQ, "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd"),
]
BACKFILL_THROTTLE_S = 0.35     # 콜 간 간격 (일 10,000 한도 대비 완충)
BACKFILL_MAX_CALLS = 9_000     # 하루 한도의 90%에서 강제 중단
BACKFILL_SINCE = "20151201"    # krx_weekly 는 2015-12 부터 — 기적재 주는 어차피 skip
DDL_KRX_WEEKLY = """
CREATE TABLE IF NOT EXISTS krx_weekly (
  price_dd  text NOT NULL, ticker text NOT NULL, market text,
  close bigint, mktcap bigint, list_shrs bigint,
  PRIMARY KEY (price_dd, ticker));
CREATE INDEX IF NOT EXISTS idx_krx_weekly_isu ON krx_weekly (ticker, price_dd);
"""
_backfill_calls = 0


def _num(v):
    try:
        return int(str(v).replace(",", "")) if v not in (None, "", "-") else None
    except Exception:
        return None


def _ssl_ctx():
    """Windows 신뢰저장소(truststore) 우선, 실패 시 기본. KRX_INSECURE=1 이면 검증 끔(로컬 프록시용)."""
    if os.getenv("KRX_INSECURE") == "1":
        return False
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        return True


async def _backfill_fetch(h: httpx.AsyncClient, url: str, key: str, price_dd: str) -> list:
    global _backfill_calls
    if _backfill_calls >= BACKFILL_MAX_CALLS:
        raise RuntimeError(f"BACKFILL_MAX_CALLS({BACKFILL_MAX_CALLS}) 도달 — 일 한도 보호를 위해 중단. 내일 재개.")
    from open_proxy_mcp.dart.krx_meter import bump
    _backfill_calls += 1
    bump()
    await asyncio.sleep(BACKFILL_THROTTLE_S)
    r = await h.get(url, headers={"AUTH_KEY": key}, params={"basDd": price_dd})
    r.raise_for_status()
    return next((v for v in r.json().values() if isinstance(v, list)), [])


def _week_candidates(since: date, until: date) -> list[list[str]]:
    """완결된 ISO주별 후보일 [금,목,수,화,월]. 진행 중인 이번 주는 제외(최근 주는 A 단계가 담당)."""
    weeks = []
    monday = since - timedelta(days=since.weekday())
    last_full_monday = until - timedelta(days=until.weekday() + 7)  # 지난주 월요일
    while monday <= last_full_monday:
        weeks.append([(monday + timedelta(days=d)).strftime("%Y%m%d") for d in (4, 3, 2, 1, 0)])
        monday += timedelta(days=7)
    return weeks


async def backfill(since: date, limit_weeks: int | None = None) -> None:
    """krx_weekly 과거 주간 백필 — 주 단위 idempotent(DELETE 후 COPY, commit). 기적재 주 skip → 재개 가능."""
    key = os.getenv("KRX_API_KEY") or os.getenv("KRX_OPEN_API_KEY")
    assert key, "KRX_API_KEY 없음 (.env)"
    con = psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=20)
    con.execute(DDL_KRX_WEEKLY)
    con.commit()
    done_days = {r[0] for r in con.execute("SELECT DISTINCT price_dd FROM krx_weekly").fetchall()}

    weeks = _week_candidates(since, date.today())
    todo = [c for c in weeks if not (set(c) & done_days)]   # 후보일 중 하나라도 있으면 그 주는 기적재
    if limit_weeks:
        todo = todo[:limit_weeks]
    print(f"대상 주: {len(weeks)} | 기적재 skip: {len(weeks) - len(todo)} | 실행: {len(todo)}", flush=True)

    t0 = time.time()
    saved_weeks = empty_weeks = 0
    async with httpx.AsyncClient(timeout=30, verify=_ssl_ctx()) as h:
        for i, cands in enumerate(todo):
            rows_by_mkt, used_day = None, None
            for d in cands:  # 금→월 순으로 그 주 마지막 거래일 탐색
                kospi = await _backfill_fetch(h, KRX_ENDPOINTS[0][1], key, d)
                if not kospi:
                    continue
                kosdaq = await _backfill_fetch(h, KRX_ENDPOINTS[1][1], key, d)
                rows_by_mkt, used_day = [(MKT_KS, kospi), (MKT_KQ, kosdaq)], d
                break
            if not rows_by_mkt:
                empty_weeks += 1  # 그 주 전체 휴장(설·추석 등) 또는 API 제공범위 밖
                continue
            recs = [
                (used_day, r.get("ISU_CD"), market, _num(r.get("TDD_CLSPRC")),
                 _num(r.get("MKTCAP")), _num(r.get("LIST_SHRS")))
                for market, rows in rows_by_mkt for r in rows if r.get("ISU_CD")
            ]
            with con.cursor() as cur:  # 컬럼명 명시 COPY — 위치 의존 금지
                cur.execute("DELETE FROM krx_weekly WHERE price_dd = %s", (used_day,))
                with cur.copy("COPY krx_weekly (price_dd, ticker, market, close, mktcap, list_shrs) FROM STDIN") as cp:
                    for rec in recs:
                        cp.write_row(rec)
            con.commit()
            saved_weeks += 1
            if saved_weeks % 25 == 0 or i == len(todo) - 1:
                print(f"  [{saved_weeks}/{len(todo)}] {used_day} {len(recs):,}행 | 콜 {_backfill_calls} | "
                      f"{time.time() - t0:.0f}s", flush=True)

    n = con.execute("SELECT count(*), count(DISTINCT price_dd), min(price_dd), max(price_dd) FROM krx_weekly").fetchone()
    print(f"\n완료: 저장 {saved_weeks}주 / 휴장·범위밖 {empty_weeks}주 / 총 콜 {_backfill_calls}", flush=True)
    print(f"테이블: {n[0]:,}행, {n[1]}개 주간포인트, {n[2]} ~ {n[3]}", flush=True)
    con.close()


def _wk_converge(cur, table: str, snap_dd: str, extra_where: str = "") -> None:
    """같은 ISO주의 다른 snap_dd 행 삭제 — 주당 1스냅샷 수렴."""
    s, e = _iso_wk_range(snap_dd)
    cur.execute(f"DELETE FROM {table} WHERE snap_dd >= %s AND snap_dd <= %s AND snap_dd != %s"
                + extra_where, (s, e, snap_dd))


async def run(dry: bool = False) -> None:
    # A. krx_weekly 갱신 (valuation 공용 — 최신 거래일 확보 + 같은 주 수렴, KRX 최대 2콜)
    snap_dd = await _ensure_krx_fresh()
    if not snap_dd:
        print("KRX 최신 거래일 확보 실패 — 중단"); return
    print(f"A. krx_weekly 최신 = {snap_dd}")

    con = psycopg.connect(os.environ["DATABASE_URL"])
    for ddl in (DDL_FIRM, DDL_MKT) + DDL_MIGRATE:
        con.execute(ddl)
    con.commit()

    # 시세: krx_weekly에서 (전 종목 — 우선주 포함)
    wk = {c: (m or 0) for c, m in con.execute(
        "SELECT ticker, mktcap FROM krx_weekly WHERE price_dd=%s", (snap_dd,))}
    kinds = await _krx_kinds(snap_dd)  # KRX 2콜
    if not kinds:  # 양 endpoint 실패 → caps 전부 빈값 → 수렴 DELETE가 기존 스냅샷을 빈 데이터로
        print("⚠ KRX 종목유형(kinds) 확보 실패 — 스냅샷 중단(기존 데이터 보존)")  # 덮는 사고 방지(QA)
        con.close(); return
    # 보통주 시총 = 배수 분자(260705 확정). 우선주 시총은 배수에서 제외, 보통주 코드로 매핑해
    # cap_pref로 별도 저장(정보 노출용). 종류주권·K/L 등 우선주류는 '보통주 아님'으로 전부 pref 취급.
    caps: dict[str, float] = {}
    prefs: dict[str, float] = {}
    unmapped = 0.0
    unk_kind = []  # kinds 미수록 코드(참고 로깅)
    for code, cap in wk.items():
        kind = kinds.get(code, "")
        if kind == "보통주":
            caps[code] = caps.get(code, 0) + cap
        elif "우선주" in kind or kind == "종류주권":
            base = code[:5] + "0"
            if base in wk:
                prefs[base] = prefs.get(base, 0) + cap
            else:
                unmapped += cap
        elif cap and code not in kinds:
            unk_kind.append(code)
    if unk_kind:
        print(f"  (kinds 미수록 {len(unk_kind)}건 — 귀속 제외: {unk_kind[:8]}{'…' if len(unk_kind) > 8 else ''})")

    # 재무: dart_fundamentals (+비KRW 환산 — ecos_fx_rate 캐시라 API 0콜)
    rows = con.execute("""SELECT ticker, market, induty, currency, ni_fy, ni_ttm, eq_fy, eq_mrq
        FROM dart_fundamentals WHERE fetched='ok'""").fetchall()
    fx_cache: dict[str, float | None] = {}
    fx_skipped = []
    firms = []
    for isu, market, ind, cur_ccy, nf, nt, ef, em in rows:
        ccy = (cur_ccy or "KRW").upper()
        if ccy not in ("KRW", "NODATA", "?"):
            if ccy not in fx_cache:
                fx_cache[ccy] = await fx_to_krw(ccy, FY_END)
            r = fx_cache[ccy]
            if not r:
                fx_skipped.append(isu); continue
            nf, nt, ef, em = (x * r if x is not None else None for x in (nf, nt, ef, em))
        firms.append((isu, market, ind or "", nf, nt, ef, em))
    # 260829(마스터 지시): **완전자본잠식(지배주주 자본 ≤ 0)은 모든 연산·출력에서 뺀다.**
    #   수집은 그대로 한다 — 빼는 것은 집계와 화면뿐이다. 자본이 0 이하인 회사의 PER·PBR 은
    #   뜻이 없고, 그 적자가 분모에 섞여 시장 배수를 흔든다(실측: 코스닥 20251031 PER
    #   298.3 → 230.2, −22.8%). 코스피는 −0.1% 수준으로 거의 안 움직인다.
    _n0 = len(firms)
    firms = [f for f in firms if not (((f[6] if f[6] is not None else f[5])) is not None
                                      and (f[6] if f[6] is not None else f[5]) <= 0)]
    if _n0 != len(firms):
        print(f"  자본잠식 제외 {_n0 - len(firms)}사 (지배주주 자본 ≤ 0)")
    if fx_skipped:
        print(f"  ⚠ FX 미확보로 제외: {fx_skipped}")

    # B. 종목별 스냅샷
    firm_recs = []
    for isu, market, ind, nf, nt, ef, em in firms:
        cap = caps.get(isu)
        if not cap:
            continue
        eq = em if em is not None else ef
        sec = bucket(ind, isu) if ind and ind not in ("none", "err") else None
        firm_recs.append((
            snap_dd, isu, market, sec, cap, prefs.get(isu),
            (cap / nf) if nf and nf > 0 else None,      # per_fy0 (적자 → N/M)
            (cap / nt) if nt and nt > 0 else None,      # per_ttm
            (cap / ef) if ef and ef > 0 else None,      # pbr_fy0
            (cap / eq) if eq and eq > 0 else None,      # pbr_mrq
        ))
    print(f"B. opm_val_firm {len(firm_recs)}종목 (시총 매칭 기준)")

    # C. 시장 aggregate (agg와 동일 산식 — 분자·분모 짝 맞춤)
    agg = defaultdict(lambda: dict(cap=0, ni_fy=0, ni_ttm=0, eq=0, eq_fy=0,
                                   cap_ttm=0, cap_eq=0, cap_eqf=0, n=0, cap_pref=0))
    for isu, market, ind, nf, nt, ef, em in firms:
        cap = caps.get(isu)
        if not cap:
            continue
        a = agg[market]; eq = em if em is not None else ef
        a["cap_pref"] += prefs.get(isu) or 0
        if nf is not None: a["ni_fy"] += nf; a["cap"] += cap; a["n"] += 1
        if nt is not None: a["ni_ttm"] += nt; a["cap_ttm"] += cap
        if eq is not None and eq > 0: a["eq"] += eq; a["cap_eq"] += cap
        if ef is not None and ef > 0: a["eq_fy"] += ef; a["cap_eqf"] += cap
    # 260706 병합: mkt_recs도 sector='_ALL'·label=None·n=종목수로 sec_recs와 같은 컬럼 세트에 맞춤.
    mkt_recs = []
    for market in (MKT_KS, MKT_KQ):
        a = agg[market]
        if not a["n"]:
            continue
        per_f = a["cap"] / a["ni_fy"] if a["ni_fy"] and a["ni_fy"] > 0 else None      # Σni≤0 → N/M
        per_t = a["cap_ttm"] / a["ni_ttm"] if a["ni_ttm"] and a["ni_ttm"] > 0 else None  # (음수 PER 금지, QA)
        pbr_f = a["cap_eqf"] / a["eq_fy"] if a["eq_fy"] else None
        pbr_m = a["cap_eq"] / a["eq"] if a["eq"] else None
        # 260823: _ALL(시장 전체)은 **섹터 분류와 무관한 값**이다 — 코스피 전체 PER 은 섹터를
        #   어떻게 나누든 같다. scheme='market' 으로 분류 축에서 떼어낸다.
        mkt_recs.append((snap_dd, market, "market", "_ALL", None, a["n"], per_f, per_t, pbr_f, pbr_m,
                         a["cap"], a["cap_pref"], a["ni_ttm"], a["eq"],
                         a["ni_fy"] if a["n"] else None))  # n>0 ⇔ ni_fy 를 실제로 더한 회사가 있다
        print(f"C. [{market}] {a['n']}사 PER {per_f:.2f}/{per_t:.2f} PBR {pbr_f:.2f}/{pbr_m:.2f}")

    # D. 섹터 aggregate (KSIC 하이브리드, 시장별 · MINB 미만은 fold)
    sec_agg = defaultdict(lambda: dict(n=0, ni=0, eq=0, capn=0, cape=0, cap=0, cap_pref=0,
                                       ni_fy=0, eq_fy=0, capnf=0, capef=0))
    for isu, market, ind, nf, nt, ef, em in firms:
        cap = caps.get(isu)
        if not cap or not ind or ind in ("none", "err"):
            continue
        a = sec_agg[(market, bucket(ind, isu))]
        a["n"] += 1; a["cap"] += cap; a["cap_pref"] += prefs.get(isu) or 0
        eq = em if em is not None else ef
        if nt is not None: a["ni"] += nt; a["capn"] += cap
        if eq and eq > 0: a["eq"] += eq; a["cape"] += cap
        # FY0(당해연도) — C절(시장 aggregate)과 동일 산식으로 섹터도 채움(260709). firm 단위 nf/ef는
        # 이미 로드돼 있어 신규 수집 0. 섹터행 per_fy0/pbr_fy0 결측(주간행 전용) 해소.
        if nf is not None: a["ni_fy"] += nf; a["capnf"] += cap
        if ef is not None and ef > 0: a["eq_fy"] += ef; a["capef"] += cap
    sec_recs = []
    for market in (MKT_KS, MKT_KQ):
        fold = dict(n=0, ni=0, eq=0, capn=0, cape=0, cap=0, cap_pref=0,
                    ni_fy=0, eq_fy=0, capnf=0, capef=0)
        for (m, sec), a in sec_agg.items():
            if m != market:
                continue
            if a["n"] < MINB:
                for k in fold: fold[k] += a[k]
                continue
            # 컬럼 순서(snap_dd,market,sector,label,n,per_fy0,per_ttm,pbr_fy0,pbr_mrq,cap,cap_pref,ni_ttm,eq).
            # per_fy0/pbr_fy0도 섹터 단위로 채움(260709) — C절 _ALL과 동일 산식, firm 합산이라 신규 수집
            # 없음. ni_ttm/eq도 함께 채워 _ALL 행과 대칭(과거엔 None이라 scope="sector" FY0 결측 원인).
            sec_recs.append((snap_dd, market, sec, label(sec), a["n"],
                             a["capnf"] / a["ni_fy"] if a["ni_fy"] and a["ni_fy"] > 0 else None,
                             a["capn"] / a["ni"] if a["ni"] and a["ni"] > 0 else None,
                             a["capef"] / a["eq_fy"] if a["eq_fy"] else None,
                             a["cape"] / a["eq"] if a["eq"] else None,
                             a["cap"], a["cap_pref"], a["ni"] or None, a["eq"] or None))
        if fold["n"]:
            sec_recs.append((snap_dd, market, "_fold", _smap["fold_label"], fold["n"],
                             fold["capnf"] / fold["ni_fy"] if fold["ni_fy"] and fold["ni_fy"] > 0 else None,
                             fold["capn"] / fold["ni"] if fold["ni"] and fold["ni"] > 0 else None,
                             fold["capef"] / fold["eq_fy"] if fold["eq_fy"] else None,
                             fold["cape"] / fold["eq"] if fold["eq"] else None,
                             fold["cap"], fold["cap_pref"], fold["ni"] or None, fold["eq"] or None))
    _sec_fy0 = sum(1 for r in sec_recs if r[5] is not None)  # r[5]=per_fy0
    print(f"D. opm_val_market(섹터행) {len(sec_recs)}버킷 · per_fy0 채움 {_sec_fy0}/{len(sec_recs)}")

    if dry:
        print("(--dry: 저장 생략)"); con.close(); return

    # 저장 — 전부 같은 ISO주 수렴 + 컬럼명 명시 INSERT (위치의존 금지).
    # 260706 병합: 시장전체(sector='_ALL')와 섹터별 행을 opm_val_market 하나에 함께 저장.
    # 260823: **KSIC 섹터 집계는 더 이상 저장하지 않는다.** 섹터 배수·시총의 기준 축은
    #   WICS(wics_val_backfill.py, 같은 배치의 앞 단계)로 옮겼다. KSIC 는 「기업이 어느
    #   업종인가」를 알려주는 용도로만 남는다 — opm_val_firm.sector 는 그대로 채운다.
    #   과거 KSIC 집계 11,790행은 지우지 않는다(재생성 가능하지만 이력이라 보존).
    all_mkt_recs = mkt_recs
    with con.cursor() as cur:
        _wk_converge(cur, "opm_val_firm", snap_dd)
        cur.executemany("""INSERT INTO opm_val_firm
            (snap_dd, ticker, market, sector, cap, cap_pref, per_fy0, per_ttm, pbr_fy0, pbr_mrq)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (snap_dd, ticker) DO UPDATE SET market=EXCLUDED.market, sector=EXCLUDED.sector,
            cap=EXCLUDED.cap, cap_pref=EXCLUDED.cap_pref, per_fy0=EXCLUDED.per_fy0,
            per_ttm=EXCLUDED.per_ttm, pbr_fy0=EXCLUDED.pbr_fy0, pbr_mrq=EXCLUDED.pbr_mrq""", firm_recs)
        _wk_converge(cur, "opm_val_market", snap_dd)
        cur.executemany("""INSERT INTO opm_val_market
            (snap_dd, market, scheme, sector, label, n, per_fy0, per_ttm, pbr_fy0, pbr_mrq, cap, cap_pref, ni_ttm, eq, ni_fy0)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (snap_dd, market, scheme, sector) DO UPDATE SET label=EXCLUDED.label, n=EXCLUDED.n,
            per_fy0=EXCLUDED.per_fy0, per_ttm=EXCLUDED.per_ttm, pbr_fy0=EXCLUDED.pbr_fy0,
            pbr_mrq=EXCLUDED.pbr_mrq, cap=EXCLUDED.cap, cap_pref=EXCLUDED.cap_pref,
            ni_ttm=EXCLUDED.ni_ttm, eq=EXCLUDED.eq, ni_fy0=EXCLUDED.ni_fy0""", all_mkt_recs)
    con.commit()
    if unmapped:
        print(f"(우선주 미매핑 시총 {unmapped/1e12:.1f}조 — 제외)")
    print(f"저장 완료: snap_dd={snap_dd} · firm {len(firm_recs)} · market {len(mkt_recs)} (KSIC 섹터 집계는 중단 — WICS 로 이관)")
    con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="B~D 저장 생략(산출만)")
    ap.add_argument("--backfill", action="store_true",
                    help="krx_weekly 과거 주간 백필만 수행(스냅샷 B~D 미실행). 재개 가능")
    ap.add_argument("--since", default=BACKFILL_SINCE, help="--backfill 시작일 YYYYMMDD (기본 20151201)")
    ap.add_argument("--limit-weeks", type=int, default=None, help="--backfill 에서 이번 실행에 적재할 최대 주 수")
    a = ap.parse_args()
    if a.backfill:
        _since = date(int(a.since[:4]), int(a.since[4:6]), int(a.since[6:8]))
        asyncio.run(backfill(_since, a.limit_weeks))
    else:
        asyncio.run(run(dry=a.dry))
