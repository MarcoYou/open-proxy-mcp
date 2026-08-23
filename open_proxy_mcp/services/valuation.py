"""밸류에이션 (lean v1) — DART(공시)+KRX(공식시세) 상대가치 배수.

설계 스펙·검증 근거: private wiki.
지표: PER(FY0+TTM) · PBR(MRQ, 미공시시 FY0) · 배당수익률(alotMatter 보통주 DPS).
가드: 섹터 N/A(금융사 EV/PSR/FCF 차단) · N/M(분모≤0) · 자본잠식→N/M+상폐/관리종목 경고.
시계열 기준: FY0=최근 사업연도, TTM=FY+1Q차분(flow), MRQ=최근 분기말 잔액(stock).
측정: 가격·시총=KRX(공식) / 순이익·EPS·자본=지배귀속 account_id / BPS=지배자본÷유통주식수.
드랍(v1.1): RIM·EV/EBITDA·PSR·FCF·5년밴드·PIT 시계열.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

import calendar

from open_proxy_mcp.dart.client import get_dart_client, DartClientError, LruByteCache, _env_mb
from open_proxy_mcp.dart.fx import fx_to_krw, statement_currency
from open_proxy_mcp.services.company import _company_id, resolve_company_query
from open_proxy_mcp.services.contracts import AnalysisStatus
from open_proxy_mcp.services.contracts import declare_weak_resolution
from open_proxy_mcp.services.financial_metrics import build_financial_metrics_payload
from open_proxy_mcp.services.dividend import _annual_summary
from open_proxy_mcp.services.scale_guard import gid_exact, assess as scale_assess, MARKET_MAX_NI_ANCHOR

_KRX_URL = "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"
_KSQ_URL = "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd"


def _num(v):
    try:
        return int(str(v).replace(",", "")) if v not in (None, "", "-") else None
    except Exception:
        return None


def _div(a, b):
    return (a / b) if (a not in (None,) and b not in (None, 0)) else None


def _price_dates() -> list[str]:
    """최근 12일 중 주말 제외 후보일 — KRX가 빈값 줄 토·일 콜 낭비 제거(API QA)."""
    from datetime import date, timedelta
    today = date.today()
    out = []
    for i in range(12):
        d = today - timedelta(days=i)
        if d.isoweekday() <= 5:  # 월~금 (공휴일은 KRX 빈 응답으로 자연 skip)
            out.append(d.strftime("%Y%m%d"))
    return out


# KRX 시세 = 기존 검증 자산 **krx_weekly**(주별 최종거래일 전종목, 2015-12~, 원장 대조 불일치 0,
# 수정주가 파이프라인과 공유)에서 서빙 — 별도 테이블 신설 금지(260705 krx_weekly_px 중복 생성 → 폐기).
# 라이브 KRX는 개인키 1개·일 10,000콜 한도(배치와 공유)라 유저마다 못 씀. FX 캐시와 동형:
#  · 매일 최신 거래일 스냅샷을 불러와 '그 ISO주' 슬롯에 수렴(같은 주 옛 bas_dd 삭제 후 insert, 트랜잭션)
#    → 서빙엔 전날 종가까지 표시, 주중 일별은 덮여 사라지고 주 마지막 거래일만 남음(연 ~52스냅샷 bounded)
#  · 이 daily-refresh가 krx_weekly의 주기 갱신자 역할도 겸함(수정주가 파이프라인 신선도 유지).
# price_date로 기준일 투명 노출(며칠 전 종가여도 날짜 명시 → 사용자 판단).
# 260821 OOM 수습: 무상한 dict → 바이트 상한 LRU. 날짜별 전종목(~2,766)+:split 중복 저장이
# 무제한 누적하던 것을 32MB(≈수일치)로 캡. 초과 시 오래된 날짜부터 evict.
_KRX_CACHE = LruByteCache(_env_mb("OPM_KRX_CACHE_MB", 32), 48 * 60 * 60, "krx")
_KRX_STATE: dict[str, str] = {}              # {"day": 오늘, "latest_dd": 서빙할 최신 bas_dd}


def _iso_wk_range(bas_dd: str) -> tuple[str, str]:
    """YYYYMMDD가 속한 ISO주의 (월요일, 일요일) YYYYMMDD — 같은 주 옛 스냅샷 수렴 삭제용."""
    from datetime import date, timedelta
    d = date(int(bas_dd[:4]), int(bas_dd[4:6]), int(bas_dd[6:8]))
    mon = d - timedelta(days=d.isoweekday() - 1)
    return mon.strftime("%Y%m%d"), (mon + timedelta(days=6)).strftime("%Y%m%d")


def _krx_db_latest_dd() -> str | None:
    url = os.getenv("DATABASE_URL")
    if not url:
        return None
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=8) as c:
            r = c.execute("SELECT MAX(bas_dd) FROM krx_weekly").fetchone()
            return r[0] if r and r[0] else None
    except Exception:
        return None


def _krx_db_get(bas_dd: str, isu_cd: str) -> dict:
    url = os.getenv("DATABASE_URL")
    if not url:
        return {}
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=8) as c:
            r = c.execute("SELECT close, mktcap, list_shrs FROM krx_weekly "
                          "WHERE bas_dd=%s AND isu_cd=%s", (bas_dd, isu_cd)).fetchone()
            if r and r[0]:
                return {"price": r[0], "date": bas_dd, "common_mktcap": r[1], "list_shrs": r[2]}
    except Exception:
        return {}
    return {}


def _krx_db_upsert(bas_dd: str, kospi: list, kosdaq: list) -> None:
    """전종목 스냅샷을 krx_weekly에 기록 + 같은 ISO주의 옛 bas_dd 삭제(수렴) — 한 트랜잭션.
    → 주중엔 '주 내 최신 거래일' 1개, 주 마감 후엔 '주별 최종거래일'로 굳음(기존 의미 보존).
    mkt는 endpoint 기준 태깅(KOSPI/KOSDAQ — 기존 값 형식과 일치). 컬럼명 명시(위치의존 금지)."""
    url = os.getenv("DATABASE_URL")
    if not url or not (kospi or kosdaq):
        return
    recs = []
    for mkt, rows in (("KOSPI", kospi), ("KOSDAQ", kosdaq)):
        for row in rows:
            isu = row.get("ISU_CD")
            if not isu:
                continue
            recs.append((bas_dd, isu, mkt, _num(row.get("TDD_CLSPRC")),
                         _num(row.get("MKTCAP")), _num(row.get("LIST_SHRS"))))
    if not recs:
        return
    wk_start, wk_end = _iso_wk_range(bas_dd)
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=20) as c:  # 단일 트랜잭션 — 삭제·삽입 원자성
            with c.cursor() as cur:
                cur.execute("DELETE FROM krx_weekly WHERE bas_dd >= %s AND bas_dd <= %s "
                            "AND bas_dd != %s", (wk_start, wk_end, bas_dd))
                cur.executemany(
                    "INSERT INTO krx_weekly(bas_dd, isu_cd, mkt, close, mktcap, list_shrs) "
                    "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT (bas_dd, isu_cd) DO UPDATE SET "
                    "mkt=EXCLUDED.mkt, close=EXCLUDED.close, mktcap=EXCLUDED.mktcap, "
                    "list_shrs=EXCLUDED.list_shrs", recs)
            c.commit()
    except Exception:
        pass


async def _krx_market_live(basDd: str) -> dict[str, dict]:
    """KRX 전종목(코스피+코스닥) 라이브 fetch → {단축코드: row}. 2시장 병렬 + basDd 인메모리 캐시.
    ⚠ 개인키 한도 때문에 서빙은 krx_daily(DB) 우선 — 이 경로는 하루 1회 스냅샷 확보·DB미스 fallback만."""
    _cached = _KRX_CACHE.get(basDd)
    if _cached is not None:
        return _cached
    key = os.getenv("KRX_API_KEY") or os.getenv("KRX_OPEN_API_KEY")
    if not key:
        return {}
    from open_proxy_mcp.dart.krx_meter import bump

    async def _one(h, url):
        try:
            bump()  # KRX 일별 사용량 장부
            r = await h.get(url, headers={"AUTH_KEY": key}, params={"basDd": basDd})
            return next((v for v in r.json().values() if isinstance(v, list)), [])
        except Exception:
            return []

    async with httpx.AsyncClient(timeout=30) as h:  # 코스피·코스닥 독립 → 병렬
        kospi, kosdaq = await asyncio.gather(_one(h, _KRX_URL), _one(h, _KSQ_URL))
    out: dict[str, dict] = {}
    for rows in (kospi, kosdaq):
        for row in rows:
            out[row.get("ISU_CD")] = row  # bydd_trd ISU_CD = 단축코드
    if out:
        _KRX_CACHE.put(basDd, out)
        _KRX_CACHE.put(basDd + ":split", {"KOSPI": kospi, "KOSDAQ": kosdaq})  # 시장별(krx_weekly 태깅용)
    return out


async def _fetch_live_snapshot() -> tuple[str | None, dict[str, dict]]:
    """최근 거래일 전종목 스냅샷(첫 데이터 있는 날). 주말·휴장이면 직전 거래일."""
    for d in _price_dates():
        snap = await _krx_market_live(d)
        if any(_num(r.get("TDD_CLSPRC")) for r in snap.values()):
            return d, snap
    return None, {}


async def _ensure_krx_fresh() -> str | None:
    """하루 1회(프로세스): 최신 거래일 스냅샷으로 krx_weekly 갱신(같은 ISO주 수렴). 반환 = 서빙할 bas_dd.
    매일 갱신(전날 종가까지 표시)하되 주중 일별은 덮여 사라지고 주 마지막 거래일만 영구 보존."""
    from datetime import date
    today = date.today().strftime("%Y%m%d")
    if _KRX_STATE.get("day") == today and _KRX_STATE.get("latest_dd"):
        return _KRX_STATE["latest_dd"] or None
    db_latest = await asyncio.to_thread(_krx_db_latest_dd)
    # DB가 이미 '직전 완료 영업일'(오늘 이전 최근 평일)까지 있으면 라이브 스캔 생략 — 콜드 프로세스
    # 마다 무조건 스캔하던 낭비 제거(API QA: 일요일 8콜 실측). KRX는 T+1 게시라 오늘 데이터는 없다.
    prev_bd = next((d for d in _price_dates() if d < today), None)
    if db_latest and prev_bd and db_latest >= prev_bd:
        _KRX_STATE.update(day=today, latest_dd=db_latest)
        return db_latest
    if db_latest is None or db_latest < today:   # 직전 영업일 미확보 → 라이브 확인·갱신
        dd, snap = await _fetch_live_snapshot()
        if snap and dd:
            # 전진(dd > db_latest)일 때만 기록 — KRX API가 이미 저장된 거래일 데이터를 일시 소실하면
            # (260703 실측: 금요일 데이터가 이틀째 0행) 스캔이 전일을 잡는데, != 조건이면 저장된
            # 금요일을 지우고 목요일로 롤백해버림(QA WARN-1). 과거로는 절대 되돌리지 않는다.
            if db_latest is None or dd > db_latest:
                split = _KRX_CACHE.get(dd + ":split") or {}
                kospi, kosdaq = split.get("KOSPI") or [], split.get("KOSDAQ") or []
                if kospi and kosdaq:             # 두 시장 모두 있을 때만 — 반쪽 스냅샷으로 덮기 금지(QA WARN-2)
                    await asyncio.to_thread(_krx_db_upsert, dd, kospi, kosdaq)
                    db_latest = dd
                else:
                    import logging
                    logging.getLogger(__name__).warning(
                        "KRX 스냅샷 반쪽(KOSPI %d/KOSDAQ %d) — krx_weekly 기록 스킵", len(kospi), len(kosdaq))
            else:
                db_latest = max(db_latest, dd)   # dd ≤ db_latest: 저장분이 이미 최신 — 그대로 서빙
    _KRX_STATE.update(day=today, latest_dd=db_latest or "")
    return db_latest


async def _market_for(stock_code: str) -> dict:
    """보통주 종가·시총·상장주식수 — krx_weekly(DB, 검증 자산) 우선, 라이브 KRX fallback.
    price_date로 기준일 노출. 우선주 총시총 합산은 v1.1 — v1은 배수에 시총 미사용, 보통주 시총만 정보성."""
    latest_dd = await _ensure_krx_fresh()
    if latest_dd:
        row = await asyncio.to_thread(_krx_db_get, latest_dd, stock_code)
        if row.get("price"):
            return row
    # DB 미스(최신 스냅샷에 아직 없는 신규상장·정지 해제 등) → 라이브 단발 fallback
    for d in _price_dates():
        snap = await _krx_market_live(d)
        base = snap.get(stock_code)
        if base and _num(base.get("TDD_CLSPRC")):
            return {"price": _num(base.get("TDD_CLSPRC")), "date": d,
                    "common_mktcap": _num(base.get("MKTCAP")), "list_shrs": _num(base.get("LIST_SHRS"))}
    return {}


async def _resolve_listed(query: str) -> tuple[dict | None, dict | None]:
    """공용 리졸버(resolve_company_query) 채택 — company 툴과 동일 진입 방식(260705).
    상장사 우선 + 동명 다수 시 ambiguous 후보표(silent 첫 후보 pick 제거).
    반환 (corp, early_payload): corp=식별 결과 / early=즉시 반환할 payload(ambiguous).
    ERROR(비상장만·무매칭)는 (None, None) — 호출부의 기존 세분화(unlisted 시총순 후보·
    우선주 힌트)가 더 구체적이라 그 경로로 폴백한다."""
    res = await resolve_company_query(query)
    if res.status == AnalysisStatus.AMBIGUOUS:
        return None, {
            "tool": "valuation", "status": "ambiguous", "subject": query,
            "data": {"query": query, "candidates": [
                {"corp_name": c.get("corp_name"), "stock_code": c.get("stock_code"),
                 "corp_code": c.get("corp_code")} for c in res.candidates[:10]]},
            "warnings": [f"'{query}' 동명 후보 여러 건 — 아래에서 골라 종목코드로 재시도."]}
    if res.status == AnalysisStatus.EXACT and res.selected:
        return res.selected, None
    return None, None


# ── 시장·산업·종목 히스토리 스코프 — 주간 스냅샷 테이블(DB-first, market_val_weekly.py가 갱신) ──
# mkt_val_history(시장) · mkt_val_history(KSIC 섹터) · firm_valuation_snapshot(종목별). PER/PBR·시총 시계열은
# 시총 기반이라 **수정주가 조정에 불변**(시총=주가×주식수, 분할·무상증자에 양쪽이 상쇄) — 조정 불필요.
# 주당 가격·EPS 시계열을 노출하게 되면 그때 krx_adj_events(기준가 리셋 실측) 적용 필수(wiki 수정주가).


def _pg_rows(sql: str, params: tuple = ()) -> list[tuple] | None:
    """None = DB 미설정/장애(no_data와 구분 — 오진 방지, QA), [] = 정상 조회·데이터 없음."""
    url = os.getenv("DATABASE_URL")
    if not url:
        return None
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=8) as c:
            return c.execute(sql, params).fetchall()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("스냅샷 DB 조회 실패: %s", e)
        return None


_DB_ERROR_PAYLOAD_WARN = "스냅샷 DB 연결 실패 — 일시 장애 가능, 잠시 후 재시도. (배치 미실행과 다름)"


async def build_market_val_payload(format: str = "md") -> dict[str, Any]:
    """시장 전체(KOSPI/KOSDAQ) 시총가중 밸류에이션 — 최신 + 주간 히스토리(mkt_val_history)."""
    rows = await asyncio.to_thread(_pg_rows,
        "SELECT snap_dd, mkt, per_fy0, per_ttm, pbr_fy0, pbr_mrq, cap, ni_ttm, eq, cap_pref "
        "FROM mkt_val_history WHERE sector='_ALL' ORDER BY snap_dd DESC, mkt")
    if rows is None:
        return {"tool": "valuation", "status": "db_error", "subject": "시장 밸류에이션",
                "warnings": [_DB_ERROR_PAYLOAD_WARN]}
    if not rows:
        return {"tool": "valuation", "status": "no_data", "subject": "시장 밸류에이션",
                "warnings": ["mkt_val_history 비어있음 — market_val_weekly 배치 미실행."]}
    hist = [{"snap_dd": r[0], "mkt": r[1],
             "per_fy0": r[2] and round(r[2], 2), "per_ttm": r[3] and round(r[3], 2),
             "pbr_fy0": r[4] and round(r[4], 2), "pbr_mrq": r[5] and round(r[5], 2),
             "cap_krw": r[6], "ni_ttm_krw": r[7], "eq_krw": r[8],
             "cap_pref_krw": r[9] if len(r) > 9 else None} for r in rows]
    latest_dd = hist[0]["snap_dd"]
    return {"tool": "valuation", "status": "ok", "subject": "시장 밸류에이션(KOSPI·KOSDAQ)",
            "data": {"scope": "market", "as_of": latest_dd,
                     "latest": [h for h in hist if h["snap_dd"] == latest_dd],
                     "history": hist,
                     "method": "**보통주 기준**(260705 확정): PER=Σ보통주 시총÷Σ지배순이익 · PBR=Σ보통주 "
                               "시총÷Σ지배자본(MRQ) — KRX 지수 PER 관행. 우선주 시총은 배수 제외, cap_pref_krw로 "
                               "별도 노출(분모 이익·자본엔 우선주 몫 포함 → 소폭 하향 편향, 클래스 분리는 공시 부재로 불가) · "
                               "trailing(과거 실적) 기준 — 컨센서스 선행 PER와 다름. "
                               "Σ지배순이익에 적자기업 포함(흑자기업만 쓰는 일부 벤더와 상이 — 적자 우세 "
                               "시장·섹터의 PER이 크게 높아짐, KOSDAQ 고PER의 주원인. PBR 병행 해석 권장). "
                               "※ 표기 PER 분모별 Σ시총은 해당 지표 보유 종목만 — cap_krw(전체 시총)"
                               "÷ni_ttm_krw 재계산과 다를 수 있음"},
            "warnings": [f"주간 스냅샷 기준(최신 {latest_dd}) — market_val_weekly가 갱신."]}


async def build_sector_val_payload(company: str = "", format: str = "md") -> dict[str, Any]:
    """산업(KSIC 하이브리드)별 시총가중 밸류에이션 — 최신 스냅샷 + 섹터 히스토리(mkt_val_history).
    company 지정 시 그 기업의 섹터를 함께 표시."""
    rows = await asyncio.to_thread(_pg_rows,
        "SELECT snap_dd, mkt, sector, label, n, cap, per_ttm, pbr_mrq, per_fy0, pbr_fy0 FROM mkt_val_history "
        "WHERE sector != '_ALL' AND snap_dd=(SELECT MAX(snap_dd) FROM mkt_val_history WHERE sector != '_ALL') "
        "ORDER BY mkt, cap DESC")
    if rows is None:
        return {"tool": "valuation", "status": "db_error", "subject": "산업별 밸류에이션",
                "warnings": [_DB_ERROR_PAYLOAD_WARN]}
    if not rows:
        return {"tool": "valuation", "status": "no_data", "subject": "산업별 밸류에이션",
                "warnings": ["mkt_val_history 비어있음 — market_val_weekly 배치 미실행."]}
    as_of = rows[0][0]
    sectors = [{"mkt": r[1], "sector": r[2], "label": r[3], "n": r[4], "cap_krw": r[5],
                "per_ttm": r[6] and round(r[6], 2), "pbr_mrq": r[7] and round(r[7], 2),
                "per_fy0": r[8] and round(r[8], 2), "pbr_fy0": r[9] and round(r[9], 2)}
               for r in rows]
    company_ctx = None
    warnings = [f"주간 스냅샷 기준(최신 {as_of}) · 분류=KSIC 하이브리드(opm_sector_map)."]
    if company.strip():
        corp, early = await _resolve_listed(company.strip())  # 공용 리졸버 — ambiguous 후보표
        if early:
            return early
        if not corp:
            corp = await get_dart_client().lookup_corp_code(company.strip())
        isu = (corp or {}).get("stock_code")
        if not isu:  # 회사 미해결/비상장 — 전체 표 덤프 대신 짧은 에러(실사용 QA P1: 1,600토큰 낭비 방지)
            return {"tool": "valuation", "status": "not_found", "subject": company,
                    "warnings": [f"'{company}' 상장사를 찾지 못함 — 정확한 회사명/종목코드로 재시도. "
                                 "전체 섹터 표는 company 없이 scope='sector'."]}
        else:
            fr = await asyncio.to_thread(_pg_rows,
                "SELECT v.sector, v.mkt, v.per_ttm, v.pbr_mrq, s.label, s.per_ttm, s.pbr_mrq "
                "FROM firm_valuation_snapshot v LEFT JOIN mkt_val_history s "
                "ON s.snap_dd=v.snap_dd AND s.mkt=v.mkt AND s.sector=v.sector "
                "WHERE v.isu_cd=%s AND v.snap_dd=%s", (isu, as_of)) or []
            if fr:
                sec, mkt, pt, pb, lbl, spt, spb = fr[0]
                if lbl is None:  # 소규모 섹터 → mkt_val_history엔 '_fold'로 접혀 raw 코드 JOIN 미스
                    fold = [s for s in sectors if s["mkt"] == mkt and s["sector"] == "_fold"]
                    if fold:  # 폴드 버킷과 비교(정직하게 표기) — literal None 렌더 방지(QA 109종목 실측)
                        lbl = f"{fold[0]['label']} (소규모 섹터 {sec} 포함)"
                        spt, spb = fold[0]["per_ttm"], fold[0]["pbr_mrq"]
                    else:
                        lbl = f"KSIC {sec} (섹터 집계 없음)"
                company_ctx = {"name": corp.get("corp_name"), "isu_cd": isu, "mkt": mkt,
                               "sector": sec, "sector_label": lbl,
                               "firm_per_ttm": pt and round(pt, 2), "firm_pbr_mrq": pb and round(pb, 2),
                               "sector_per_ttm": spt and round(spt, 2), "sector_pbr_mrq": spb and round(spb, 2)}
                # 소속 섹터의 과거 시계열(2020-01~, market_val_history_backfill.py가 채움) — 0콜, 이미 DB에 있음.
                # 소규모(_fold) 섹터면 fold 버킷 자체의 히스토리로 폴백(개별 sec 코드는 mkt_val_history에 없음).
                hist_sector = "_fold" if lbl and "(소규모 섹터" in lbl else sec
                hrows = await asyncio.to_thread(_pg_rows,
                    "SELECT snap_dd, per_fy0, per_ttm, pbr_fy0, pbr_mrq, cap FROM mkt_val_history "
                    "WHERE mkt=%s AND sector=%s ORDER BY snap_dd", (mkt, hist_sector)) or []
                if hrows:
                    company_ctx["sector_history"] = [
                        {"snap_dd": h[0], "per_fy0": h[1] and round(h[1], 2), "per_ttm": h[2] and round(h[2], 2),
                         "pbr_fy0": h[3] and round(h[3], 2), "pbr_mrq": h[4] and round(h[4], 2), "cap_krw": h[5]}
                        for h in hrows]
            else:
                warnings.append(f"'{company}' 종목 스냅샷 없음(비상장·미수집).")
    return {"tool": "valuation", "status": "ok", "subject": "산업별 밸류에이션",
            "data": {"scope": "sector", "as_of": as_of, "sectors": sectors,
                     "company": company_ctx},
            "warnings": warnings}


def _latest_annual_fy() -> int:
    """지금 시점 확정된 최신 사업연도 — 사업보고서 3월 공시(4월 이후 전년, 아니면 전전년).
    market_fund_quarterly._latest_annual_fy / market_val_series._latest_annual_fy와 동일 규칙.
    ⚠ mkt_fundamentals.ni_fy/eq_fy는 derive_fundamentals가 이 FY로 덮어쓰는 **가변열**이라,
    그 값을 담을 fin[] 키는 반드시 이 헬퍼로 파생해야 함(하드코딩 금지 — FY 넘어가면 조용히 오라벨)."""
    from datetime import date
    t = date.today()
    return t.year - 1 if t.month >= 4 else t.year - 2


def _pit_fy(bas_dd: str) -> int:
    """PIT 근사: 그 시점 최신 확정 FY. 사업보고서 3월 중순 공시 → 4월 이후면 전년 FY, 아니면 전전년
    (look-ahead 방지). market_val_series.py series()와 동일 규칙."""
    y, m = int(bas_dd[:4]), int(bas_dd[4:6])
    return y - 1 if m >= 4 else y - 2


async def _firm_fin_by_fy(isu_cd: str, currency: str = "KRW") -> dict[int, tuple]:
    """종목 FY별 (지배순이익, 지배자본) — KRW 환산 완료. mkt_finstat_y(FY2018~과거, 스케일오류
    정정치 ni_restated/eq_restated 우선) ∪ mkt_fundamentals(최신 확정 FY=_latest_annual_fy()).
    비KRW는 FY 기말환율 환산."""
    rows = await asyncio.to_thread(_pg_rows,
        "SELECT fy, ni, eq, ni_restated, eq_restated FROM mkt_finstat_y WHERE isu_cd=%s "
        "AND (fetched='ok' OR ni_restated IS NOT NULL OR eq_restated IS NOT NULL)", (isu_cd,)) or []
    fin: dict[int, tuple] = {}
    for fy, ni, eq, ni_r, eq_r in rows:
        ni_f = ni_r if ni_r is not None else ni
        eq_f = eq_r if eq_r is not None else eq
        fin[int(fy)] = (float(ni_f) if ni_f is not None else None,
                        float(eq_f) if eq_f is not None else None)
    cur = await asyncio.to_thread(_pg_rows,
        "SELECT ni_fy, eq_fy FROM mkt_fundamentals WHERE isu_cd=%s", (isu_cd,))
    if cur and cur[0][0] is not None:
        fin[_latest_annual_fy()] = (float(cur[0][0]), float(cur[0][1]) if cur[0][1] is not None else None)
    ccy = (currency or "KRW").upper()
    if ccy not in ("KRW", "NODATA", "?"):
        for fy in list(fin):
            fx = await fx_to_krw(ccy, f"{fy}1231")
            ni_f, eq_f = fin[fy]
            fin[fy] = ((ni_f * fx if ni_f is not None else None,
                        eq_f * fx if eq_f is not None else None) if fx else (None, None))
    return fin


def _pit_quarter(bas_dd: str) -> tuple[int, int]:
    """그 시점까지 공시된 최신 분기 → (fy, quarter). 공시지연(look-ahead 방지):
    사업보고서(Q4) 익년 3월 · 1Q 5/15 · 반기 8/14 · 3Q 11/14. Q4 경계는 _pit_fy(4월)와 일치."""
    y, m, d = int(bas_dd[:4]), int(bas_dd[4:6]), int(bas_dd[6:8])
    md = m * 100 + d
    if md >= 1114:
        return (y, 3)
    if md >= 814:
        return (y, 2)
    if md >= 515:
        return (y, 1)
    if m >= 4:            # 사업보고서(전년) — _pit_fy와 동일 경계
        return (y - 1, 4)
    return (y - 1, 3)     # 1~3월: 전년 3Q가 최신


async def _firm_fin_by_q(isu_cd: str, currency: str = "KRW") -> dict[tuple, tuple]:
    """종목 (fy,quarter)별 (지배순이익 누적, 지배자본 잔액) — KRW 환산. mkt_finstat_q.
    비KRW는 분기말 환율(Q1 0331·Q2 0630·Q3 0930·Q4 1231)로 환산 — 누적순익은 분기말 근사."""
    rows = await asyncio.to_thread(_pg_rows,
        "SELECT fy, quarter, ni_cum, eq FROM mkt_finstat_q WHERE isu_cd=%s", (isu_cd,)) or []
    finq: dict[tuple, tuple] = {}
    for fy, q, ni, eq in rows:
        finq[(int(fy), int(q))] = (float(ni) if ni is not None else None,
                                   float(eq) if eq is not None else None)
    ccy = (currency or "KRW").upper()
    if ccy not in ("KRW", "NODATA", "?"):
        qend = {1: "0331", 2: "0630", 3: "0930", 4: "1231"}
        fxcache: dict[str, float | None] = {}
        for (fy, q) in list(finq):
            key = f"{fy}{qend[q]}"
            if key not in fxcache:
                fxcache[key] = await fx_to_krw(ccy, key)
            fx = fxcache[key]
            ni_f, eq_f = finq[(fy, q)]
            finq[(fy, q)] = ((ni_f * fx if ni_f is not None else None,
                              eq_f * fx if eq_f is not None else None) if fx else (None, None))
    return finq


def _ttm_ni(fin: dict[int, tuple], finq: dict[tuple, tuple], fy: int, q: int) -> float | None:
    """TTM 지배순이익 = FY(fy-1) 연간 + 누적(fy,q) − 누적(fy-1,q). q==4면 사업보고서=연간(fy)."""
    if q == 4:
        return fin.get(fy, (None, None))[0]
    cum_now = finq.get((fy, q), (None, None))[0]
    cum_prev = finq.get((fy - 1, q), (None, None))[0]
    fy_prev = fin.get(fy - 1, (None, None))[0]
    if cum_now is None or cum_prev is None or fy_prev is None:
        return None
    return fy_prev + cum_now - cum_prev


def _mrq_eq(fin: dict[int, tuple], finq: dict[tuple, tuple], fy: int, q: int) -> float | None:
    """최근분기(MRQ) 지배자본 잔액. q==4면 FY말 자본(연간)."""
    if q == 4:
        return fin.get(fy, (None, None))[1]
    return finq.get((fy, q), (None, None))[1]


async def _annual_pit_band(isu_cd: str, currency: str = "KRW",
                           fin: dict[int, tuple] | None = None) -> list[dict]:
    """연말 PIT PER/PBR 밴드 — **이미 있는 데이터로 질의 시 계산**(백필 저장 X, compute-on-query).
    연말 보통주 시총(krx_weekly, isu_cd=보통주 코드) ÷ 그 시점 최신 확정 FY 재무(mkt_finstat_y).
    시총 기반이라 수정주가(분할·무상증자) 조정 불변. 비KRW는 그 FY 기말환율로 KRW 환산."""
    caps = await asyncio.to_thread(_pg_rows,
        "SELECT DISTINCT ON (substring(bas_dd,1,4)) substring(bas_dd,1,4), bas_dd, mktcap "
        "FROM krx_weekly WHERE isu_cd=%s AND substring(bas_dd,5,2)='12' "
        "ORDER BY substring(bas_dd,1,4), bas_dd DESC", (isu_cd,)) or []
    if fin is None:
        fin = await _firm_fin_by_fy(isu_cd, currency)
    band = []
    for yr_s, bas_dd, cap in caps:
        pit_fy = _pit_fy(bas_dd)
        if pit_fy not in fin or not cap:
            continue
        ni_k, eq_k = fin[pit_fy]
        cap = float(cap)
        band.append({
            "period": yr_s, "asof": bas_dd, "pit_fy": pit_fy, "cap_krw": round(cap),
            "per_fy0": round(cap / ni_k, 2) if ni_k and ni_k > 0 else None,
            "pbr_fy0": round(cap / eq_k, 2) if eq_k and eq_k > 0 else None,
            "source": f"연말(PIT FY{pit_fy})",
        })
    return band


async def _weekly_series(isu_cd: str, currency: str = "KRW",
                         fin: dict[int, tuple] | None = None,
                         finq: dict[tuple, tuple] | None = None) -> list[dict]:
    """차트용 주간 dense 시계열 — 주간 보통주 시총(krx_weekly) × 재무.
    FY0(연 계단, mkt_finstat_y) + TTM/MRQ(분기 계단, mkt_finstat_q). 분모는 공시시점 계단,
    주가는 주간이라 곡선은 촘촘. 시총 기반이라 수정주가 조정 불변. 반환은 asof 오름차순.
    TTM = FY(y-1) + 누적(y,q) − 누적(y-1,q) — 전년 동분기 필요해 사실상 2020~부터 산출."""
    px = await asyncio.to_thread(_pg_rows,
        "SELECT bas_dd, mktcap FROM krx_weekly WHERE isu_cd=%s ORDER BY bas_dd", (isu_cd,)) or []
    if fin is None:
        fin = await _firm_fin_by_fy(isu_cd, currency)
    if finq is None:
        finq = await _firm_fin_by_q(isu_cd, currency)
    series = []
    for bas_dd, cap in px:
        fy = _pit_fy(bas_dd)
        if fy not in fin or not cap:
            continue
        ni_k, eq_k = fin[fy]
        cap = float(cap)
        aq_fy, aq_q = _pit_quarter(bas_dd)
        ttm = _ttm_ni(fin, finq, aq_fy, aq_q)
        mrq = _mrq_eq(fin, finq, aq_fy, aq_q)
        series.append({
            "asof": bas_dd, "pit_fy": fy, "pit_q": f"{aq_fy}Q{aq_q}", "cap_krw": round(cap),
            "per_fy0": round(cap / ni_k, 3) if ni_k and ni_k > 0 else None,
            "pbr": round(cap / eq_k, 3) if eq_k and eq_k > 0 else None,
            "per_ttm": round(cap / ttm, 3) if ttm and ttm > 0 else None,
            "pbr_mrq": round(cap / mrq, 3) if mrq and mrq > 0 else None,
        })
    return series


def _month_end_summary(series: list[dict], months: int = 12) -> list[dict]:
    """주간 series → 월말(각 월 마지막 거래주) 다운샘플, 최근 `months`개월. series의 부분집합이라
    차트와 100% 일치. pit_q 변경 달에 분기공시 마커(▲) — 배수 변화가 가격/실적 어느 쪽인지 표시."""
    if not series:
        return []
    by_month: dict[str, dict] = {}
    for pt in series:              # asc 정렬 → 각 월 마지막 관측이 최종(=월말)
        by_month[pt["asof"][:6]] = pt
    out: list[dict] = []
    prev_q = None
    for ym in sorted(by_month)[-months:]:
        pt = dict(by_month[ym])
        pt["marker"] = f"▲{pt.get('pit_q')}" if prev_q is not None and pt.get("pit_q") != prev_q else ""
        prev_q = pt.get("pit_q")
        out.append(pt)
    return out


async def build_firm_history_payload(company: str, format: str = "md") -> dict[str, Any]:
    """종목별 밸류에이션 주간 히스토리(firm_valuation_snapshot) — PER/PBR/시총 시계열(수정주가 조정 불변)."""
    query = (company or "").strip()
    if not query:
        return {"tool": "valuation", "status": "invalid", "subject": company,
                "warnings": ["회사명 또는 종목코드(6자리)를 입력하세요."]}
    corp, early = await _resolve_listed(query)   # 공용 리졸버 — firm과 동일 진입
    if early:
        return early
    if not corp:
        corp = await get_dart_client().lookup_corp_code(query)
    if not corp or not corp.get("stock_code"):
        return {"tool": "valuation", "status": "not_found" if not corp else "unlisted",
                "subject": query, "warnings": [f"'{company}' 상장 종목을 찾지 못함."]}
    isu = corp["stock_code"]
    cur_row = await asyncio.to_thread(_pg_rows,
        "SELECT mkt, induty, currency FROM mkt_fundamentals WHERE isu_cd=%s", (isu,))
    currency = (cur_row[0][2] if cur_row and cur_row[0][2] else "KRW")
    # ① 연말 PIT 밴드 + ③ 차트용 주간 dense series + ④ 최근 12개월 월말 요약 — 이미 있는 데이터
    #    (krx_weekly × mkt_finstat_y연간 × mkt_finstat_q분기)로 질의 시 계산(저장 X). 재무 로더는
    #    한 번만 만들어 공유. series는 FY0(연)+TTM/MRQ(분기) 곡선, summary는 그 월말 다운샘플.
    fin = await _firm_fin_by_fy(isu, currency)
    finq = await _firm_fin_by_q(isu, currency)
    band = await _annual_pit_band(isu, currency, fin=fin)
    series = await _weekly_series(isu, currency, fin=fin, finq=finq)
    summary = _month_end_summary(series, 12)   # 텍스트 표: 최근 12개월 월말 + 분기공시 마커
    # ② 주간 스냅샷 — 최근/현재 촘촘한 포인트(앞으로 cron이 축적)
    rows = await asyncio.to_thread(_pg_rows,
        "SELECT snap_dd, mkt, sector, cap, per_fy0, per_ttm, pbr_fy0, pbr_mrq "
        "FROM firm_valuation_snapshot WHERE isu_cd=%s ORDER BY snap_dd", (isu,))
    if rows is None:
        return {"tool": "valuation", "status": "db_error", "subject": corp.get("corp_name", query),
                "warnings": [_DB_ERROR_PAYLOAD_WARN]}
    if not band and not rows:
        return {"tool": "valuation", "status": "no_data", "subject": corp.get("corp_name", query),
                "warnings": ["시계열 없음 — 과거 시세(krx_weekly)·재무(mkt_finstat_y)·주간 스냅샷 모두 미수집."]}
    hist = [{"period": b["period"], "asof": b["asof"], "cap_krw": b["cap_krw"],
             "per_fy0": b["per_fy0"], "per_ttm": None, "pbr": b["pbr_fy0"],
             "source": b["source"]} for b in band]
    hist += [{"period": r[0][:4] + "-" + r[0][4:6] + "-" + r[0][6:], "asof": r[0], "cap_krw": r[3],
              "per_fy0": r[4] and round(r[4], 2), "per_ttm": r[5] and round(r[5], 2),
              "pbr": (r[7] if r[7] is not None else r[6]) and round(r[7] if r[7] is not None else r[6], 2),
              "source": "주간"} for r in rows]
    hist.sort(key=lambda h: h["asof"])
    latest_dd = series[-1]["asof"] if series else (rows[-1][0] if rows else "?")
    warnings = [f"최신 {latest_dd} 기준. 정밀 배수(보통주 주가·배당·경고 포함)는 scope='firm' 사용."]
    # 수정주가 파이프라인 flag 대조 — 분할(스핀오프)·미해결 조정 종목은 시계열 해석 주의(QA 권고).
    flags = await asyncio.to_thread(_pg_rows,
        "SELECT flag, detail FROM krx_stock_flags WHERE isu_cd=%s", (isu,)) or []
    for fl, detail in flags:
        if fl == "spinoff_break":
            warnings.append("⚠ 인적분할 이력(spinoff_break) — 분할 시점 시총 점프 + 직후 배수는 "
                            "재무 반영 지연으로 왜곡 가능. 시계열 비교 주의.")
        elif fl == "unresolved_adjustment":
            warnings.append("⚠ 미해결 주가조정 이력(unresolved_adjustment) — 시계열 해석 주의.")
    mkt = rows[-1][1] if rows else (cur_row[0][0] if cur_row else None)
    sector = rows[-1][2] if rows else (cur_row[0][1] if cur_row else None)
    if not summary and not band:
        warnings.append("주간 곡선 미산출 — krx_weekly 시세 또는 재무(mkt_finstat_y/mkt_finstat_q) 미수집.")
    return {"tool": "valuation", "status": "ok", "subject": corp.get("corp_name", query),
            "data": {"scope": "firm_history", "isu_cd": isu, "mkt": mkt,
                     "sector": sector, "history": hist, "series": series, "summary": summary,
                     "method": "보통주 시총 ÷ 재무. FY0=직전 확정 FY(연간 mkt_finstat_y·PIT) · "
                               "TTM=최근4분기 지배순이익 FY(y-1)+누적(y,q)−누적(y-1,q)(분기 mkt_finstat_q) · "
                               "MRQ PBR=최근분기 지배자본. 차트=전구간 주간 곡선(series), 요약=최근 12개월 "
                               "월말(summary). 시총 기반이라 분할·무상증자 등 조정성 이벤트에 불변(유증·자사주 "
                               "소각·인적분할의 실제 시총 점프는 그대로 반영)."},
            "warnings": warnings}


async def _acntall(client, cc: str, year: int, rc: str, fs: str | None = None) -> tuple[list, str | None]:
    """fs 지정 시 그 기준만, 미지정 시 CFS(연결)→OFS(별도) 폴백 후 성공한 기준을 함께 반환.
    260704 실측: 카카오뱅크·코스모신소재는 FY2025 연결이 없고 별도만 있어(연결대상 없음) CFS만
    시도하면 전 지표가 N/M. TTM은 연간·분기를 같은 기준으로 맞춰야 정합(연결/별도 혼용 방지)."""
    for cand in ((fs,) if fs else ("CFS", "OFS")):
        try:
            d = await client.get_fnltt_singl_acnt_all(cc, str(year), rc, cand)
            rows = (d.get("list", d) if isinstance(d, dict) else d) or []
            if rows:
                return rows, cand
        except DartClientError:
            continue
    return [], None


def _gid(rows, account_id, sj, field="thstrm_amount"):
    """account_id 정확일치(exact match) — substring 금지(260704 실측 사고: 접두어 충돌로
    'ifrs-full_Liabilities'가 'ifrs-full_LiabilitiesIncludedIn...'에 오매칭될 수 있음)."""
    v = gid_exact(rows, f"ifrs-full_{account_id}", sj, field)
    return int(v) if v is not None else None


def _ctrl_equity(rows, field="thstrm_amount"):
    """지배자본 = EquityAttributableToOwnersOfParent. 없으면(비지배지분 없는 회사는 이 계정을
    아예 안 적음) 총자본 − 비지배지분으로 폴백 — 260704 실측: 카카오뱅크·케이씨텍·코스모신소재·
    JW중외제약이 지배자본 계정 부재로 PBR이 N/M이던 것을 이 폴백이 해소(NCI 없으면 총자본=지배자본)."""
    eq = _gid(rows, "EquityAttributableToOwnersOfParent", ("BS",), field)
    if eq is not None:
        return eq
    total = _gid(rows, "Equity", ("BS",), field)
    if total is None:
        return None
    nci = _gid(rows, "NoncontrollingInterests", ("BS",), field) or 0
    return total - nci


def _ctrl_ni(rows, field="thstrm_amount"):
    """지배순이익 = ProfitLossAttributableToOwnersOfParent. 없으면(비지배지분 없는 회사) 총순이익
    − 비지배귀속 순이익으로 폴백(대칭 로직) — 260704 실측: 카카오뱅크·케이씨텍·코스모신소재가
    지배순이익 계정 부재로 PER이 N/M이던 것을 해소."""
    ni = _gid(rows, "ProfitLossAttributableToOwnersOfParent", ("CIS", "IS"), field)
    if ni is not None:
        return ni
    total = _gid(rows, "ProfitLoss", ("CIS", "IS"), field)
    if total is None:
        return None
    nci = _gid(rows, "ProfitLossAttributableToNoncontrollingInterests", ("CIS", "IS"), field) or 0
    return total - nci


def _eps_disclosed(rows: list, *fields: str) -> float | None:
    """공시 기본주당이익 — fields 우선순위로 첫 유효값. 3단 매칭(100사 스윕 실측으로 확장):
    ① 표준 `ifrs-full_BasicEarningsLossPerShare`
    ② 계속영업+중단영업 분리 공시(삼바형) — 분모(가중평균) 동일하므로 합산 = 총 기본 EPS
    ③ 비표준 코드('-표준계정코드 미사용-', LG형) — nm 기반(보통주·기본·비중단·비우선주)
    분기 응답은 thstrm_add_amount(누적 EPS)가 실존([[per-pbr-data-points]]) → TTM 조립 재료.
    두산밥캣류 USD EPS는 소수점 문자열("2.95") — int 파싱 금지, float."""
    def _val(r):
        for f in fields:
            v = r.get(f)
            if v not in (None, "", "-"):
                try:
                    return float(str(v).replace(",", ""))
                except (TypeError, ValueError):
                    pass
        return None

    for sj in ("IS", "CIS"):  # 통상 IS 하단, 일부 회사 CIS
        rs = [r for r in rows if r.get("sj_div") == sj]
        for r in rs:  # ① 표준 총 기본 EPS
            if r.get("account_id") == "ifrs-full_BasicEarningsLossPerShare":
                v = _val(r)
                if v is not None:
                    return v
        cont = disc = None  # ② 계속+중단 분리(삼바형)
        for r in rs:
            if r.get("account_id") == "ifrs-full_BasicEarningsLossPerShareFromContinuingOperations":
                cont = _val(r) if cont is None else cont
            elif r.get("account_id") == "ifrs-full_BasicEarningsLossPerShareFromDiscontinuedOperations":
                disc = _val(r) if disc is None else disc
        if cont is not None:
            return cont + (disc or 0)
        for r in rs:  # ③ 비표준 코드(LG형) — nm 기반, 보수적 필터
            if not (r.get("account_id") or "").startswith("-표준"):
                continue
            nm = (r.get("account_nm") or "").replace(" ", "")
            if "주당" in nm and "기본" in nm and "우선주" not in nm and "중단" not in nm:
                v = _val(r)
                if v is not None:
                    return v
    return None


async def _eps_adj_factor(isu_cd: str, after_dd: str) -> float:
    """(after_dd, 오늘] 조정성 이벤트(액면분할·병합·무상증자·주식배당)의 누적 수정계수 —
    수정주가 파이프라인 krx_adj_events(기준가 리셋 실측 + 라벨) 재사용.
    260823: krx_base_resets(측정) + krx_adj_factor_v3(라벨) 두 표를 통합했다 — 갱신이 2단계라
    뒤쪽이 깨져도 앞쪽만 돌면 멀쩡해 보였고, 실제로 260705 부터 라벨 쪽이 실행 불가였다.
    EPS는 가격과 같은 방향으로 조정(주식수 n배 → EPS 1/n = ×factor). 유상증자·감자·미라벨(None)은
    보수적으로 제외 — 잔여는 sanity 경고가 방어."""
    rows = await asyncio.to_thread(_pg_rows,
        "SELECT adj_factor FROM krx_adj_events WHERE isu_cd=%s AND event_dd>%s "
        "AND event_type IN ('split','merge','bonus','stock_div') AND adj_factor IS NOT NULL "
        "AND label_confidence='confirmed'", (isu_cd, after_dd))
    f = 1.0
    for (x,) in rows or []:
        if x and x > 0:
            f *= x
    return f


async def _shares_ratio(isu_cd: str, after_dd: str) -> float | None:
    """(after_dd, 오늘] 상장주식수 변동배율 — krx_weekly 실측. 계수 누락 탐지용.

    260823: 수정계수 파이프라인이 **cron 없이 수동**이라(private wiki 「이벤트/수동」) 7주 밀려
    있었고, 그 사이 액면병합·분할 62종목의 계수가 통째로 비었다. 계수가 없으면
    `_eps_adj_factor` 가 조용히 1.0 을 돌려주고 공시 EPS 조각들이 **옛 분모와 새 분모로 섞인 채**
    조립된다(메이슨캐피탈 실측: TTM 지배순이익 -70억인데 EPS(TTM) +39원, PER 32.31 이 나갔다).
    """
    first = await asyncio.to_thread(_pg_rows,
        "SELECT list_shrs FROM krx_weekly WHERE isu_cd=%s AND bas_dd>%s AND list_shrs>0 "
        "ORDER BY bas_dd ASC LIMIT 1", (isu_cd, after_dd))
    last = await asyncio.to_thread(_pg_rows,
        "SELECT list_shrs FROM krx_weekly WHERE isu_cd=%s AND list_shrs>0 "
        "ORDER BY bas_dd DESC LIMIT 1", (isu_cd,))
    if not first or not last or not first[0][0] or not last[0][0]:
        return None
    return last[0][0] / first[0][0]


# 계수 f 와 주식수 배율 r 의 불변식: 조정성 이벤트는 주가·EPS 와 주식수가 상쇄하므로 f × r ≈ 1.
# 벗어나면 「주식수는 변했는데 계수가 없다」 = 조립 EPS 가 섞였다는 뜻.
# 밴드를 ±50% 로 넉넉히 잡는 이유 — 유상증자·감자는 계수 대상이 아니라 정상적으로 r 만 움직인다.
# 30% 유상증자(r=1.3)는 통과시키고, 10:1 병합(r=0.1)만 잡는다.
_ADJ_INVARIANT_LO, _ADJ_INVARIANT_HI = 0.67, 1.5


async def _shares_outstanding(client, cc: str, year: int) -> dict:
    """유통주식수: total(보통+우선 합계, BPS용) · common(보통주, EPS용). 자기주식 제외(distb)."""
    out = {"total": None, "common": None}
    try:
        st = await client.get_stock_total(cc, str(year), "11011")
    except DartClientError:
        return out
    for r in (st.get("list", st) if isinstance(st, dict) else st) or []:
        se = (r.get("se") or "").strip()
        if se == "합계":
            out["total"] = _num(r.get("distb_stock_co"))
        elif se == "보통주":
            out["common"] = _num(r.get("distb_stock_co"))
    return out


async def _build_valuation_payload_impl(company: str, format: str = "md") -> dict[str, Any]:
    client = get_dart_client()
    query = (company or "").strip()
    if not query:
        return {"tool": "valuation", "status": "invalid", "subject": company,
                "warnings": ["회사명 또는 종목코드(6자리)를 입력하세요."]}
    corp, early = await _resolve_listed(query)   # 공용 리졸버(company 툴 방식) — ambiguous 후보표
    if early:
        return early
    if not corp:  # ERROR(비상장만·무매칭) → 기존 세분화 경로(unlisted/not_found + 커스텀 안내)
        corp = await client.lookup_corp_code(query)
    if not corp:
        return {"tool": "valuation", "status": "not_found", "subject": company,
                "warnings": [f"'{company}' 조회 결과 없음 — 종목코드(6자리)나 정확한 회사명으로 재시도. "
                             "(우선주는 보통주 종목코드로 조회)"]}
    cc, stock_code = corp["corp_code"], corp.get("stock_code")
    name = corp.get("corp_name", company)
    # 비상장 = 주가 없음 → 시장배수(PER·PBR) 정의 불가. DART 마스터엔 비상장 법인(삼성·쿠팡 등)도
    # 있어 resolve되므로 여기서 조기 차단(전부 None 산출·크래시 방지). 상장 동명 후보는 안내.
    if not stock_code:
        alts = [c for c in await client.lookup_corp_code_all(query) if c.get("stock_code")][:5]
        if not alts:  # exact-match 단락으로 빈 경우('삼성'→비상장 법인만) → 부분매치 상장 후보 별도 조회
            corps = await client._load_corp_codes()  # 실사용 QA P2: "삼성전자를 찾으셨나요?" 오도 방지
            cand = [c for c in corps if c.get("stock_code") and query in c["corp_name"]]
            if cand:  # 시총순 정렬 — 사용자가 의도했을 가능성이 큰 대형사(삼성전자)부터
                latest = await asyncio.to_thread(_krx_db_latest_dd)
                caps = {r[0]: r[1] for r in (await asyncio.to_thread(
                    _pg_rows, "SELECT isu_cd, mktcap FROM krx_weekly WHERE bas_dd=%s "
                    "AND isu_cd = ANY(%s)", (latest, [c["stock_code"] for c in cand])) or [])} if latest else {}
                alts = sorted(cand, key=lambda c: -(caps.get(c["stock_code"]) or 0))[:5]
        alt_txt = ("  혹시 이 상장사를 찾으셨나요? " +
                   ", ".join(f"{c['corp_name']}({c['stock_code']})" for c in alts)) if alts else ""
        return {"tool": "valuation", "status": "unlisted", "subject": name,
                "warnings": [f"'{name}'은(는) 비상장 — 주가가 없어 시장배수(PER·PBR·배당수익률) 산출 불가. "
                             f"재무 펀더멘탈은 financial_metrics 사용.{alt_txt}"]}

    # ── 데이터 fetch 병렬화: 의존성 3단계 (P1 fy 무관 → P2 fy 의존 → P3 fs_used 의존) ──
    # P1: 재무요약(대형 ~7콜)·업종정보·시세 — 모두 cc/stock_code만 의존(fy 불필요) → 병렬.
    #     (이전엔 순차라 info·market이 fm 뒤에서 대기했음). stock_code는 위 unlisted 가드로 보장.
    fm, info, mk = await asyncio.gather(
        build_financial_metrics_payload(stock_code, scope="summary", year=0, consolidated=True),
        client.get_company_info(cc),
        _market_for(stock_code),
    )
    s = fm.get("data", {}).get("summary") or {}
    fy = fm.get("data", {}).get("year")
    if fy is None:  # 상장사여도 재무 미확정(신규상장·SPAC 등) → fy+1 크래시 방지, 명확한 상태 반환
        return {"tool": "valuation", "status": "no_financials", "subject": name,
                "warnings": [f"'{name}'({stock_code}) 재무 데이터를 확정하지 못함 — 밸류에이션 산출 불가."]}
    eps_fy = s.get("eps_krw"); revenue_fy = s.get("revenue_krw"); roe = s.get("roe_pct")
    cap_status = s.get("capital_impairment_status")
    # 금융사 판별: KSIC 업종코드(induty) 대분류 K = 64(은행·금융지주)·65(보험)·66(증권).
    # 260704 실측: 매출=None 휴리스틱은 인터넷은행(카카오뱅크 영업수익 3조 신고)을 놓쳐 오분류 →
    # induty를 1차 신호로, 매출=None을 2차 폴백으로. (EV/PSR/FCF·순차입 게이팅 = 범주 부적합 차단)
    induty = str(info.get("induty_code") or "")
    is_financial = induty[:2] in ("64", "65", "66") or revenue_fy is None

    # TTM(flow) = FY + 1Q(당해) − 1Q(전년); MRQ(stock) = 최근 분기 잔액
    q_cur, q_prev = fy + 1, fy  # 예: fy=2025 → 1Q2026, 1Q2025
    # P2: 연간 재무원장·유통주식수·배당 — fy만 의존 → 병렬.
    (fy_rows, fs_used), sh, (div_sum, _div_meta) = await asyncio.gather(
        _acntall(client, cc, fy, "11011"),
        _shares_outstanding(client, cc, fy),
        _annual_summary(cc, fy),
    )
    # P3: 분기 재무원장 — 연간에서 확정한 fs(연결/별도) 강제(TTM 혼용 방지) → 당해·전년 병렬.
    (qc_rows, _), (qp_rows, _) = await asyncio.gather(
        _acntall(client, cc, q_cur, "11013", fs_used),
        _acntall(client, cc, q_prev, "11013", fs_used),
    )

    # 통화 환산: 기능통화≠KRW(두산밥캣=USD 등)면 회계기말 환율로 KRW 환산 — KRW 주가/시총과
    # 통화 일치시켜야 배수가 유효(미환산 시 환율배수만큼 왜곡: 두산밥캣 PBR 1,238 오탐). wiki §9.
    stmt_cur = statement_currency(fy_rows)
    fx_rate = 1.0
    if stmt_cur != "KRW":
        acc_mt = str(info.get("acc_mt") or "12").zfill(2)
        last_day = calendar.monthrange(fy, int(acc_mt))[1]
        fx_rate = await fx_to_krw(stmt_cur, f"{fy}{acc_mt}{last_day:02d}") or 1.0

    def _fx(x):  # None 보존, 나머지는 KRW 환산(1.0이면 무변화)
        return round(x * fx_rate) if x is not None else None

    if fx_rate != 1.0:
        revenue_fy = _fx(revenue_fy)
        eps_fy = None  # fm의 eps_krw는 실제 USD/주 → 폐기, 아래서 공시 EPS×환율로 대체

    # ── 공시 EPS 조립 (260705, [[per-pbr-data-points]] 전수조사 귀결) ──
    # 가중평균주식수는 어느 endpoint에도 없음 → 주식수를 직접 만들지 않고 공시 EPS끼리 조립:
    #   TTM EPS = FY0 EPS + 당해 분기누적 EPS(thstrm_add_amount) − 전년동기누적 EPS
    # → FY0·TTM 모두 공시 가중평균·우선주 배분 기준 = 분모 비대칭(현대차 29% 괴리·방향 왜곡) 근본 해소.
    eps_fy_disc = _eps_disclosed(fy_rows, "thstrm_amount")
    eps_qc_disc = _eps_disclosed(qc_rows, "thstrm_add_amount", "thstrm_amount")
    eps_qp_disc = _eps_disclosed(qp_rows, "thstrm_add_amount", "thstrm_amount")
    # 수정계수 보정(krx_adj_events, 코스닥 스윕 리노공업 실증): 기중 액면분할·무상증자·주식배당
    # 이후의 보고서는 EPS를 새 분모로 내지만 **그 이전에 제출된 분기보고서 EPS는 옛 분모 그대로**
    # (실무상 소급 재발행 없음 — 리노 1:5 분할 실측: 전년1Q 1,933은 분할 전, 연간 2,002는 분할 후).
    # → 각 조각을 '그 보고서 결산기준일 이후 발생한 조정성 이벤트' 누적 계수로 현재 기준에 정렬.
    #   리노 검산: 2,002 + 532 − 1,933×0.2 = 2,147 (균일분모 2,148과 정합).
    eps_adj = None  # 보정 발동 시 {"current": f, "prior_q": f} — 근거 투명(explain에 표시)
    shares_unadjusted = None  # 계수 누락 탐지 시 {"shares_ratio": r, "factor": f} — PER 무효화
    if stock_code and any(x is not None for x in (eps_fy_disc, eps_qc_disc, eps_qp_disc)):
        f_cur = await _eps_adj_factor(stock_code, f"{fy + 1}0331")  # 연간·당해1Q 결산기준일 이후
        f_qp = await _eps_adj_factor(stock_code, f"{fy}0331")       # 전년1Q 결산기준일 이후
        if f_cur != 1.0 or f_qp != 1.0:
            eps_adj = {"current": round(f_cur, 6), "prior_q": round(f_qp, 6)}
        if eps_fy_disc is not None:
            eps_fy_disc *= f_cur
        if eps_qc_disc is not None:
            eps_qc_disc *= f_cur
        if eps_qp_disc is not None:
            eps_qp_disc *= f_qp
        # 계수 누락 탐지 — 조용히 틀린 값을 내느니 N/M 을 낸다
        r_cur = await _shares_ratio(stock_code, f"{fy + 1}0331")
        if r_cur and not (_ADJ_INVARIANT_LO <= f_cur * r_cur <= _ADJ_INVARIANT_HI):
            shares_unadjusted = {"shares_ratio": round(r_cur, 4), "factor": round(f_cur, 4)}
    eps_ttm_disc = (eps_fy_disc + eps_qc_disc - eps_qp_disc) \
        if None not in (eps_fy_disc, eps_qc_disc, eps_qp_disc) else None

    ni_fy = _fx(_ctrl_ni(fy_rows))
    ni_qc = _fx(_ctrl_ni(qc_rows))
    ni_qp = _fx(_ctrl_ni(qp_rows))
    ni_ttm = (ni_fy + ni_qc - ni_qp) if None not in (ni_fy, ni_qc, ni_qp) else None
    eq_mrq = _fx(_ctrl_equity(qc_rows))
    eq_fy = _fx(_ctrl_equity(fy_rows))
    ctrl_equity = eq_mrq if eq_mrq is not None else eq_fy  # MRQ 우선, 미공시시 FY0
    equity_basis = "MRQ" if eq_mrq is not None else "FY0"

    shares_total = sh.get("total")        # 합계(보통+우선) — BPS 분모 (sh = P2 병렬 fetch)
    shares_common = sh.get("common") or shares_total  # 보통주 — EPS 분모(스펙 P1)
    price = mk.get("price")               # mk = P1 병렬 fetch

    # ── 실시간 스케일 오류 가드 (소프트센 032680 사례, wiki §9) ──
    # hard 등급 = ②(항등식)·③(시장최댓값 배수). soft = ①(배수점프, 실측 오탐 97.5%)·④(시총비율).
    # ★개별 종목 조회에서는 값을 무효화(N/M)하지 않고 그대로 노출 + 강한 경고만 부착 — 이 tool의
    #  철학("배수·인풋·가정 모두 노출, 판단은 사용자")과 자본잠식 처리(값 유지+경고)에 일관.
    #  (기계가 합산하는 시장 aggregate = market_val_agg/series에서는 반대로 무효화 — 경고문이
    #   합산 연산에 무력하므로. 소비 맥락이 다르면 처리도 다르다.)
    # 스케일가드용 값도 KRW 환산(_fx) — market_max 앵커가 KRW(44조)이므로 통화 일치 필수.
    ni_fy_frmtrm = _fx(_ctrl_ni(fy_rows, "frmtrm_amount"))  # 지배순이익 부재사도 폴백 일관 적용
    assets_fy = _fx(_gid(fy_rows, "Assets", ("BS",)))
    liab_fy = _fx(_gid(fy_rows, "Liabilities", ("BS",)))
    # 항등식(자산=부채+자본)은 반드시 총자본(지배+비지배지분) 기준 — 지배자본(eq_fy)만 쓰면
    # 비지배지분만큼 항상 어긋남(실측 발견: 삼성전자 비지배지분 12조 → 2.12% 오탐).
    eq_total_fy = _fx(_gid(fy_rows, "Equity", ("BS",)))
    scale_verdict = scale_assess(
        thstrm=ni_fy, frmtrm=ni_fy_frmtrm, assets=assets_fy, liabilities=liab_fy,
        equity=eq_total_fy, mktcap=mk.get("common_mktcap"), market_max=MARKET_MAX_NI_ANCHOR,
    )

    # 주식수 sanity: DART 유통 > KRX 상장×3 = 파싱오류(LS에코 ×1e6) → 무효화 (우선주 감안 여유 ×3)
    list_shrs = mk.get("list_shrs")
    shares_bad = bool(list_shrs and shares_total and shares_total > list_shrs * 3)
    if shares_bad:
        shares_total = shares_common = None

    # DPS = alotMatter 보통주 결의 현금배당금 (이미 주당값 — 주식수 불필요). div_sum = P2 병렬 fetch.
    dps = (div_sum or {}).get("cash_dps") or None

    bps = round(_div(ctrl_equity, shares_total)) if (ctrl_equity and shares_total) else None
    # EPS(FY0): 공시값 우선 — fy_rows 직접(비KRW는 ×환율) → fm(eps_krw) → 지배순이익÷보통주 폴백.
    if eps_fy_disc is not None:
        eps_fy = _fx(eps_fy_disc) if fx_rate != 1.0 else round(eps_fy_disc)
    if eps_fy is None and ni_fy is not None and shares_common:
        eps_fy = round(_div(ni_fy, shares_common))
    # EPS(TTM): 공시 EPS 조립(FY0과 같은 기준) 우선 → 조각 결측 시 지배순이익÷보통주 폴백(비대칭).
    if eps_ttm_disc is not None:
        eps_ttm = _fx(eps_ttm_disc) if fx_rate != 1.0 else round(eps_ttm_disc)
        eps_ttm_basis = "disclosed_assembled"
    elif ni_ttm and shares_common:
        eps_ttm = round(_div(ni_ttm, shares_common))
        eps_ttm_basis = "ni_div_shares_fallback"
    else:
        eps_ttm, eps_ttm_basis = None, None

    # ── 가드: 자본잠식·적자·섹터 ──
    impaired_full = cap_status == "full"
    def nm(x, denom_ok):  # 분모≤0 or 완전자본잠식 → N/M
        return round(x, 2) if (x is not None and denom_ok and not impaired_full) else None
    # ── 배수는 **시총 기반**이다 (260823 전환) ─────────────────────────────────
    # 종전: PER = 주가 ÷ EPS · PBR = 주가 ÷ BPS — 둘 다 분모에 주식수가 들어간다.
    #   그래서 액면분할·병합이 나면 옛 주식수 기준 EPS 와 새 주가가 섞여 배수가 통째로 틀렸고
    #   (메이슨캐피탈 실측: 적자인데 PER 32.31), 이를 막으려면 수정계수 파이프라인이
    #   상시 최신이어야 했다. 실측 4.1%(116/2,797)가 계수 부재로 N/M 이 되고 있었다.
    #
    # 이제: PER = 보통주 시총 ÷ 지배순이익 · PBR = 보통주 시총 ÷ 지배자본.
    #   분자·분모에서 주식수가 상쇄되므로 **조정성 이벤트에 불변**이다 — 계수가 필요 없다.
    #   덤으로 scope=market/sector/firm_history 스냅샷과 **방법론이 같아진다**(종전에는
    #   같은 `per_ttm` 이름으로 서로 다른 정의가 나갔다).
    #
    # 대가 두 가지 — 출력에 명시한다:
    #   ① 가중평균이 아니다. 공시 EPS 는 기중 주식수 변동을 가중평균으로 반영한 회사 공식값인데,
    #      시총은 오늘 주식수만 본다. 연중 유상증자한 회사는 두 값이 벌어진다.
    #   ② 우선주 편향. 분자는 보통주 시총인데 분모(지배순이익·지배자본)에는 우선주 몫이 포함돼
    #      배수가 소폭 낮게 나온다. 클래스별 이익·자본 분리는 공시 부재로 불가.
    # EPS·BPS 는 인풋으로 계속 노출한다 — 회사 공식값이라 대조에 쓴다.
    cap = mk.get("common_mktcap")
    per_fy = nm(_div(cap, ni_fy), cap and ni_fy is not None and ni_fy > 0)
    per_ttm = nm(_div(cap, ni_ttm), cap and ni_ttm is not None and ni_ttm > 0)
    pbr = nm(_div(cap, ctrl_equity), cap and ctrl_equity is not None and ctrl_equity > 0)
    div_yield = round(_div(dps, price) * 100, 2) if (dps and price and not impaired_full) else None

    warnings = []
    if impaired_full:
        warnings.append("⚠️ 완전자본잠식(자본≤0) — 상장폐지 위험. PER·PBR N/M. risk_events 확인 요망.")
    elif cap_status == "partial_50plus":
        warnings.append("⚠️ 자본잠식 50%↑ — 관리종목 위험.")
    elif cap_status == "partial":
        warnings.append("자본잠식 진행 중.")
    if eps_fy is not None and eps_fy <= 0:   # None(파싱실패)을 '적자'로 오표기 금지 (패널 P1)
        warnings.append("적자(FY0 EPS≤0) — PER N/M.")
    if shares_unadjusted:
        r = shares_unadjusted["shares_ratio"]
        # 260823: 배수가 시총 기반으로 바뀌어 **PER·PBR 은 이 영향을 받지 않는다.**
        #   다만 아래 EPS 는 공시 조각 조립이라 여전히 섞여 있으므로 인풋으로만 읽어야 한다.
        warnings.append(
            f"상장주식수가 결산기준일 이후 {r}배로 바뀌었습니다(액면분할·병합 추정) — "
            "PER·PBR 은 시총 기반이라 영향 없지만, 아래 **EPS는 옛 분모와 새 분모가 섞여 있어** "
            "주당 비교에는 쓰지 마세요.")
    elif shares_bad:
        # 260823: 종전에는 이 문구가 계수 누락 케이스까지 덮어 **원인을 오진**했다
        # (액면병합인데 「DART 파싱오류」라고 안내). 위 분기가 그 경우를 먼저 가져간다.
        warnings.append("⚠️ 유통주식수 이상(상장주식수 초과) — DART 파싱오류 의심, PBR/EPS 무효화. 확인 요망.")
    # 극단 배수 plausibility (두산밥캣류 단위 오독 방어 — 값은 내되 경고)
    if (pbr and pbr > 100) or (per_fy and per_fy > 500) or (per_ttm and per_ttm > 500):
        warnings.append("⚠️ 배수 비정상 고값 — 재무 단위/스케일 오류 가능(예: 지배자본 과소). 원문 확인 요망.")
    if is_financial:
        warnings.append("금융·지주 업종 — EV/EBITDA·PSR·FCF·순차입은 범주 부적합으로 산출 제외(N/A). PBR·PER·배당·ROE 중심 해석. (금융·지주도 매출/영업수익은 있음 — 배수 부적합일 뿐)")
    if fx_rate != 1.0:
        warnings.append(f"기능통화 {stmt_cur} — 재무를 {fy}회계기말 환율 {fx_rate:,.1f}원/{stmt_cur}로 KRW 환산(순이익은 원칙상 평균환율, v1은 기말환율 근사 → 수% 오차). KRW 시총과 통화 정합.")
    elif stmt_cur != "KRW":
        warnings.append(f"⚠️ 기능통화 {stmt_cur}인데 환율 조회 실패 — 배수 통화 불일치 가능, 원문 확인 요망.")
    if scale_verdict and scale_verdict["tier"] == "hard":
        warnings.append(f"🚨 DART 재무 단위(스케일) 오류 강하게 의심({scale_verdict['hard_hit']}) — 아래 순이익·자본·배수는 **원문 그대로**이며 신뢰 불가. 반드시 원문 확인 후 사용. (예: 소프트센 032680 100만배 오류)")
    elif scale_verdict and scale_verdict["tier"] == "soft":
        warnings.append(f"재무 비율 이상치({scale_verdict['soft_hit']}) — 값은 정상일 수 있음(원샷 이익·자산매각·적자흑자 전환 등). 참고용 플래그.")
    # 조립 EPS sanity(재무 QA 260705, 이오플로우 실증): 기중 주식수 급변(대규모 유증·감자) 시
    # 서로 다른 가중평균 분모의 EPS를 가감하는 구조적 한계. 단 우선주 배분·가중평균의 '구조적' 괴리
    # (현대차 22% — 정상)는 FY0·TTM 양쪽에 동일하게 나타나므로, **괴리 비율의 변화**(FY0 대비 TTM)와
    # 부호 불일치만 경고 — 상시 발동 노이즈 방지.
    if eps_ttm_basis == "disclosed_assembled" and eps_ttm is not None and ni_ttm and shares_common:
        uni_ttm = _div(ni_ttm, shares_common)
        uni_fy = _div(ni_fy, shares_common) if ni_fy is not None else None
        sign_flip = eps_ttm * ni_ttm < 0
        shift = None
        if uni_ttm and uni_fy and eps_fy:
            r_ttm, r_fy = eps_ttm / uni_ttm, eps_fy / uni_fy
            if r_fy:
                shift = abs(r_ttm / r_fy - 1)
        if sign_flip or (shift is not None and shift > 0.15):
            warnings.append(
                "⚠️ TTM EPS 조립값의 정합 이상 — 기중 주식수 급변(대규모 유증·감자·전환) 시 서로 다른 "
                f"가중평균 분모의 공시 EPS를 가감하는 알려진 한계"
                f"({'순이익과 부호 불일치' if sign_flip else f'FY0 대비 괴리 변화 {shift*100:.0f}%'}). "
                "TTM 배수 해석 주의.")
    # EPS 비대칭 경고 — TTM이 폴백(지배NI÷보통주)일 때만: FY0(공시 가중평균)과 기준이 달라
    # 괴리 >10%면 방향 왜곡 가능(현대차 29% 실증). 공시 조립(disclosed_assembled)이면 대칭 — 경고 불필요.
    if eps_ttm_basis == "ni_div_shares_fallback" and eps_fy and ni_fy is not None and shares_common:
        eps_calc = _div(ni_fy, shares_common)
        if eps_calc and abs(eps_calc - eps_fy) / abs(eps_fy) > 0.10:
            warnings.append(
                f"⚠️ EPS(TTM)이 폴백 계산(지배순이익÷보통주) — 공시 EPS(FY0 {eps_fy:,}원)와 기준 괴리 "
                f"{abs(eps_calc-eps_fy)/abs(eps_fy)*100:.0f}%(가중평균·우선주 배분 차이). "
                "FY0·TTM PER의 증감 방향 비교 주의.")
    # 수정주가 파이프라인 flag — 분할·미해결 조정 종목은 배수 해석 주의(재무 QA: 삼바 분할 실증).
    if stock_code:
        for fl, _detail in (await asyncio.to_thread(
                _pg_rows, "SELECT flag, detail FROM krx_stock_flags WHERE isu_cd=%s", (stock_code,)) or []):
            if fl == "spinoff_break":
                warnings.append("⚠️ 인적분할 이력 — 분할 전후 재무·주식수 불연속으로 FY0 배수(특히 공시 "
                                "EPS의 가중평균 주식수)가 왜곡될 수 있음. TTM·MRQ 중심 해석 권장.")
            elif fl == "unresolved_adjustment":
                warnings.append("⚠️ 미해결 주가조정 이력 — 과거 가격 비교 시 주의.")
    if mk.get("date"):
        warnings.append(f"주가 기준일 {mk['date']} 종가 {price:,}원 (KRX).")

    payload = {
        "tool": "valuation", "status": "ok", "subject": name,
        "data": {
            "company_id": _company_id(corp),
            "identifiers": {"ticker": stock_code, "corp_code": cc},
            "sector_class": "financial" if is_financial else "general",
            "fiscal_year": fy, "price_krw": price, "price_date": mk.get("date"),
            "multiples": {
                "per_fy0": per_fy, "per_ttm": per_ttm,
                "pbr_mrq": pbr, "pbr_basis": equity_basis,
                # 260823: 주가÷EPS 에서 전환. 스냅샷(market/sector/firm_history)과 같은 정의.
                "multiples_basis": "common_mktcap_over_controlling_income",
                "dividend_yield_pct": div_yield,
            },
            "inputs": {
                "eps_fy0_krw": eps_fy, "eps_ttm_krw": eps_ttm, "eps_ttm_basis": eps_ttm_basis,
                "eps_adj_factors": eps_adj,  # 분할·무상증자 수정계수 보정(v3) — 미발동 시 None
                "shares_unadjusted": shares_unadjusted,  # 계수 누락 탐지 — 있으면 PER 무효(N/M)
                "bps_krw": bps, "roe_pct": roe,
                "net_income_fy0_krw": ni_fy, "net_income_ttm_krw": ni_ttm,
                "controlling_equity_krw": ctrl_equity,
                "shares_common": shares_common, "shares_total": shares_total,
                "dps_krw": dps, "revenue_fy0_krw": revenue_fy,
                "common_market_cap_krw": mk.get("common_mktcap"),
                "capital_impairment_status": cap_status,
                "functional_currency": stmt_cur,
                "fx_rate_to_krw": fx_rate if fx_rate != 1.0 else None,
            },
            "warnings": warnings,
            "data_quality": {
                "scale_tier": scale_verdict["tier"],          # hard=강한 오류의심 / soft=참고 / clean
                "scale_flags": scale_verdict["hard_hit"] + scale_verdict["soft_hit"],
                "values_masked": False,  # 개별조회는 값 무효화 안 함(집계 tool과 반대) — 판단은 사용자
            },
            "note": "lean v1 — RIM·EV/EBITDA·PSR·FCF·5년밴드·PIT·희석EPS는 v1.1. "
                    "EPS(FY0·TTM 모두)=DART 공시 기본주당이익 기준(TTM=공시 EPS 조립: FY0+분기누적−전년동기누적 "
                    "— 가중평균 주식수·우선주 배분 반영, 두 PER 직접비교 가능. 클래스별 EPS 미공시사(삼성전자 등)는 "
                    "보·우 합산 가중평균 = 네이버금융·FnGuide 관행과 동일). 공시 EPS 결측 시에만 "
                    "지배순이익÷보통주 폴백(경고 부착). "
                    "PBR 분모=합계 유통주식수(보통+우선, 자기주식 제외) — 보통주만 쓰는 일부 벤더와 다를 수 있음. "
                    "배수는 trailing(과거 실적) 기준 — 컨센서스 선행(fwd) PER와 상이.",
        },
    }
    if format == "md":
        payload["markdown"] = _render_md(payload)
    return payload


def _render_md(p: dict[str, Any]) -> str:
    d = p["data"]; m = d["multiples"]; i = d["inputs"]
    def g(x, suf="", fmt="{}"):
        return (fmt.format(x) + suf) if x is not None else "N/M"
    lines = [
        f"# {p['subject']} 밸류에이션 (lean v1 · {d['fiscal_year']} 재무 · 주가 {g(d['price_krw'],'원','{:,}')})",
        "",
        "## 배수",
        f"- PER {g(m['per_fy0'])}(FY0) / {g(m['per_ttm'])}(TTM) · PBR {g(m['pbr_mrq'])}({m['pbr_basis']}) · 배당수익률 {g(m['dividend_yield_pct'],'%')}",
        "",
        "## 인풋 (근거 투명)",
        f"- EPS {g(i['eps_fy0_krw'],'','{:,}')}(FY0)/{g(i['eps_ttm_krw'],'','{:,}')}(TTM) · BPS {g(i['bps_krw'],'','{:,}')} · ROE {g(i['roe_pct'],'%')} · DPS {g(i['dps_krw'],'','{:,}')}",
        f"- 지배순이익 {g(i['net_income_fy0_krw'],'','{:,}')}(FY0)/{g(i['net_income_ttm_krw'],'','{:,}')}(TTM) · 지배자본 {g(i['controlling_equity_krw'],'','{:,}')} · 유통주식 보통 {g(i['shares_common'],'','{:,}')}/합계 {g(i['shares_total'],'','{:,}')}",
        f"- 보통주 시총 {g(i['common_market_cap_krw'],'','{:,}')} (업종구분: {'금융·지주' if d['sector_class']=='financial' else '일반(비금융)'})",
    ]
    # 봉투(payload["warnings"])와 데이터(data["warnings"])를 **둘 다** 싣는다.
    # 「이 회사가 맞나」를 묻는 추정 경고는 declare_weak_resolution 이 봉투에 다는데,
    # 종전에는 데이터 쪽만 읽어 통째로 버렸다 — 「현대」를 물으면 28곳 중 하나를 고른 사실이
    # 아무 표시 없이 완결된 밸류에이션으로 나갔다. 봉투를 앞에 둔다(분석 전체의 전제라서).
    _seen: set[str] = set()
    _warns = [w for w in list(p.get("warnings") or []) + list(d.get("warnings") or [])
              if not (w in _seen or _seen.add(w))]
    if _warns:
        lines += ["", "## 주의"] + [f"- {w}" for w in _warns]
    # note(방법론 고지)를 md에도 렌더 — json에만 있으면 기본(md) 사용자가 핵심 고지를 못 봄(재무 QA HIGH)
    if d.get("note"):
        lines += ["", f"> {d['note']}"]
    lines += ["> 시장·섹터 대비 비교는 scope='market'/'sector' — 스냅샷 배수(총시총 기준)는 본 값과 다를 수 있음. "
              "수치 근거·계산 과정은 scope='explain'."]
    return "\n".join(lines)


async def build_valuation_payload(*args, **kwargs):
    """이름이 정확히 맞지 않아 추정으로 고른 기업을 응답에 밝힌다.

    이 서비스는 `ToolEnvelope` 를 쓰지 않고 dict 를 직접 만들어 return 이 여러 곳에
    흩어져 있다 — 진입점 하나만 감싸 두면 새 return 이 늘어도 전파가 끊기지 않는다.
    """
    payload = declare_weak_resolution(await _build_valuation_payload_impl(*args, **kwargs))
    # **순서 결함 방어.** impl 은 md 를 자기 안에서 먼저 찍고(`payload["markdown"] = _render_md(...)`),
    # 추정 고지는 그 바깥에서 붙는다. 그래서 md 사용자만 「이 회사가 맞나」를 못 봤다 —
    # json 사용자는 봉투에서 봤다. 경고를 붙인 뒤 firm md 만 다시 찍는다.
    if isinstance(payload, dict) and payload.get("markdown") and "multiples" in (payload.get("data") or {}):
        payload["markdown"] = _render_md(payload)
    return payload
