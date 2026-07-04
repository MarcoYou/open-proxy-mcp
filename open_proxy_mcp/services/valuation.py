"""밸류에이션 (lean v1) — DART(공시)+KRX(공식시세) 상대가치 배수.

스펙: wiki/decisions/valuation-methodology.md (6인 패널 검토 반영).
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

from open_proxy_mcp.dart.client import get_dart_client, DartClientError
from open_proxy_mcp.dart.fx import fx_to_krw, statement_currency
from open_proxy_mcp.services.company import _company_id
from open_proxy_mcp.services.financial_metrics import build_financial_metrics_payload
from open_proxy_mcp.services.dividend_v2 import _annual_summary
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
    from datetime import date, timedelta
    today = date.today()
    return [(today - timedelta(days=i)).strftime("%Y%m%d") for i in range(12)]


_KRX_CACHE: dict[str, dict[str, dict]] = {}  # basDd → 전종목 스냅샷 (프로세스 캐시)


async def _krx_market(basDd: str) -> dict[str, dict]:
    """KRX 전종목(코스피+코스닥) 일별매매 → {단축코드: row}. 2시장 병렬 + basDd별 캐시.
    같은 날 스냅샷은 모든 종목이 공유 — 서버가 여러 종목 조회 시 재fetch 방지(전종목 콜 절약)."""
    if basDd in _KRX_CACHE:
        return _KRX_CACHE[basDd]
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
    if out:  # 빈 응답(휴장일·미settle·오류)은 캐시 안 함 — 다음 조회에서 재시도 여지
        _KRX_CACHE[basDd] = out
    return out


async def _market_for(stock_code: str) -> dict:
    """보통주 종가·총시총(우선주 합산)·상장주식수. 최근 거래일 fallback."""
    for d in _price_dates():
        mkt = await _krx_market(d)
        base = mkt.get(stock_code)
        if not base:
            continue
        price = _num(base.get("TDD_CLSPRC"))
        if not price:  # 거래정지·기준일 데이터 없음 → 다음 후보일
            continue
        # 우선주 총시총 합산은 v1.1 (종목명 접두 매칭이 단명 발행사에서 오합산 — 패널 지적).
        # v1은 배수에 시총 미사용 → 보통주 시총만 정보성으로 노출.
        return {
            "price": price, "date": d,
            "common_mktcap": _num(base.get("MKTCAP")),
            "list_shrs": _num(base.get("LIST_SHRS")),
        }
    return {}


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


async def build_valuation_payload(company: str, format: str = "md") -> dict[str, Any]:
    client = get_dart_client()
    query = (company or "").strip()
    if not query:
        return {"tool": "valuation", "status": "invalid", "subject": company,
                "warnings": ["회사명 또는 종목코드(6자리)를 입력하세요."]}
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
        alt_txt = ("  상장 후보: " + ", ".join(f"{c['corp_name']}({c['stock_code']})" for c in alts)) if alts else ""
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
        eps_fy = None  # fm의 eps_krw는 실제 USD/주 → 폐기, 아래서 환산된 ni_fy로 재계산

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
    eps_ttm = round(_div(ni_ttm, shares_common)) if (ni_ttm and shares_common) else None
    # EPS(FY0) financial_metrics 우선, None이면(지배순이익 부재사) 자체계산 지배순이익÷보통주로 폴백.
    if eps_fy is None and ni_fy is not None and shares_common:
        eps_fy = round(_div(ni_fy, shares_common))

    # ── 가드: 자본잠식·적자·섹터 ──
    impaired_full = cap_status == "full"
    def nm(x, denom_ok):  # 분모≤0 or 완전자본잠식 → N/M
        return round(x, 2) if (x is not None and denom_ok and not impaired_full) else None
    per_fy = nm(_div(price, eps_fy), eps_fy is not None and eps_fy > 0)
    per_ttm = nm(_div(price, eps_ttm), eps_ttm is not None and eps_ttm > 0)
    pbr = nm(_div(price, bps), bps is not None and bps > 0)
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
    if shares_bad:
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
                "dividend_yield_pct": div_yield,
            },
            "inputs": {
                "eps_fy0_krw": eps_fy, "eps_ttm_krw": eps_ttm, "bps_krw": bps, "roe_pct": roe,
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
            "note": "lean v1 — RIM·EV/EBITDA·PSR·FCF·5년밴드·PIT는 v1.1. "
                    "EPS(FY0)=DART 공시 기본주당이익(가중평균 주식수·우선주 배분 반영), "
                    "EPS(TTM)=지배순이익÷보통주(시점) — 분모 기준이 달라 FY0·TTM PER 직접비교는 주의.",
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
        f"- 보통주 시총 {g(i['common_market_cap_krw'],'','{:,}')} (섹터: {d['sector_class']})",
    ]
    if d["warnings"]:
        lines += ["", "## 주의"] + [f"- {w}" for w in d["warnings"]]
    return "\n".join(lines)
