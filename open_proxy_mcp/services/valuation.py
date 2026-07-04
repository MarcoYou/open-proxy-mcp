"""밸류에이션 (lean v1) — DART(공시)+KRX(공식시세) 상대가치 배수.

스펙: wiki/decisions/valuation-methodology.md (6인 패널 검토 반영).
지표: PER(FY0+TTM) · PBR(MRQ, 미공시시 FY0) · 배당수익률(alotMatter 보통주 DPS).
가드: 섹터 N/A(금융사 EV/PSR/FCF 차단) · N/M(분모≤0) · 자본잠식→N/M+상폐/관리종목 경고.
시계열 기준: FY0=최근 사업연도, TTM=FY+1Q차분(flow), MRQ=최근 분기말 잔액(stock).
측정: 가격·시총=KRX(공식) / 순이익·EPS·자본=지배귀속 account_id / BPS=지배자본÷유통주식수.
드랍(v1.1): RIM·EV/EBITDA·PSR·FCF·5년밴드·PIT 시계열.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from open_proxy_mcp.dart.client import get_dart_client, DartClientError
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


async def _krx_market(basDd: str) -> dict[str, dict]:
    """KRX 전종목(코스피+코스닥) 일별매매 → {단축코드: row}. 하루 2콜(전종목)."""
    key = os.getenv("KRX_API_KEY") or os.getenv("KRX_OPEN_API_KEY")
    if not key:
        return {}
    out: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=30) as h:
        for url in (_KRX_URL, _KSQ_URL):
            try:
                from open_proxy_mcp.dart.krx_meter import bump
                bump()  # KRX 일별 사용량 장부
                r = await h.get(url, headers={"AUTH_KEY": key}, params={"basDd": basDd})
                rows = next((v for v in r.json().values() if isinstance(v, list)), [])
                for row in rows:
                    out[row.get("ISU_CD")] = row  # bydd_trd ISU_CD = 단축코드
            except Exception:
                pass
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


async def _acntall(client, cc: str, year: int, rc: str) -> list:
    try:
        d = await client.get_fnltt_singl_acnt_all(cc, str(year), rc, "CFS")
        return (d.get("list", d) if isinstance(d, dict) else d) or []
    except DartClientError:
        return []


def _gid(rows, account_id, sj, field="thstrm_amount"):
    """account_id 정확일치(exact match) — substring 금지(260704 실측 사고: 접두어 충돌로
    'ifrs-full_Liabilities'가 'ifrs-full_LiabilitiesIncludedIn...'에 오매칭될 수 있음)."""
    v = gid_exact(rows, f"ifrs-full_{account_id}", sj, field)
    return int(v) if v is not None else None


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
    corp = await client.lookup_corp_code(company)
    if not corp:
        return {"tool": "valuation", "status": "not_found", "subject": company}
    cc, stock_code = corp["corp_code"], corp.get("stock_code")
    name = corp.get("corp_name", company)

    fm = (await build_financial_metrics_payload(stock_code or company, scope="summary", year=0, consolidated=True))
    s = fm.get("data", {}).get("summary") or {}
    fy = fm.get("data", {}).get("year")
    eps_fy = s.get("eps_krw"); revenue_fy = s.get("revenue_krw"); roe = s.get("roe_pct")
    cap_status = s.get("capital_impairment_status")
    is_financial = revenue_fy is None  # 금융사(매출 계정 없음) → EV/PSR/FCF 게이팅

    # TTM(flow) = FY + 1Q(당해) − 1Q(전년); MRQ(stock) = 최근 분기 잔액
    q_cur, q_prev = fy + 1, fy  # 예: fy=2025 → 1Q2026, 1Q2025
    fy_rows = await _acntall(client, cc, fy, "11011")
    qc_rows = await _acntall(client, cc, q_cur, "11013")
    qp_rows = await _acntall(client, cc, q_prev, "11013")
    ni_fy = _gid(fy_rows, "ProfitLossAttributableToOwnersOfParent", ("CIS", "IS"))
    ni_qc = _gid(qc_rows, "ProfitLossAttributableToOwnersOfParent", ("CIS", "IS"))
    ni_qp = _gid(qp_rows, "ProfitLossAttributableToOwnersOfParent", ("CIS", "IS"))
    ni_ttm = (ni_fy + ni_qc - ni_qp) if None not in (ni_fy, ni_qc, ni_qp) else None
    eq_mrq = _gid(qc_rows, "EquityAttributableToOwnersOfParent", ("BS",))
    eq_fy = _gid(fy_rows, "EquityAttributableToOwnersOfParent", ("BS",))
    ctrl_equity = eq_mrq if eq_mrq is not None else eq_fy  # MRQ 우선, 미공시시 FY0
    equity_basis = "MRQ" if eq_mrq is not None else "FY0"

    sh = await _shares_outstanding(client, cc, fy)
    shares_total = sh.get("total")        # 합계(보통+우선) — BPS 분모
    shares_common = sh.get("common") or shares_total  # 보통주 — EPS 분모(스펙 P1)
    mk = await _market_for(stock_code) if stock_code else {}
    price = mk.get("price")

    # ── 실시간 스케일 오류 가드 (소프트센 032680 사례, wiki §9) ──
    # ①②는 "보고서 전체를 일괄 부풀린" 유형엔 무력함이 실사고 재검증으로 확인됨(당기·전기·재무제표
    # 전 항목이 같이 틀리면 내부비교는 정상처럼 보임) → ③④(외부기준: 자릿수·시총 대조)가 최종
    # 방어선으로 확인됐으나 4개 다 유지(①②는 "일부 항목만 실수" 유형엔 여전히 유효할 수 있음).
    ni_fy_frmtrm = _gid(fy_rows, "ProfitLossAttributableToOwnersOfParent", ("CIS", "IS"), "frmtrm_amount")
    assets_fy = _gid(fy_rows, "Assets", ("BS",))
    liab_fy = _gid(fy_rows, "Liabilities", ("BS",))
    # 항등식(자산=부채+자본)은 반드시 총자본(지배+비지배지분) 기준 — 지배자본(eq_fy)만 쓰면
    # 비지배지분만큼 항상 어긋남(실측 발견: 삼성전자 비지배지분 12조 → 2.12% 오탐).
    eq_total_fy = _gid(fy_rows, "Equity", ("BS",))
    scale_verdict = scale_assess(
        thstrm=ni_fy, frmtrm=ni_fy_frmtrm, assets=assets_fy, liabilities=liab_fy,
        equity=eq_total_fy, mktcap=mk.get("common_mktcap"), market_max=MARKET_MAX_NI_ANCHOR,
    )
    if scale_verdict["tier"] == "hard":
        ni_fy = ni_ttm = eq_mrq = eq_fy = ctrl_equity = None

    # 주식수 sanity: DART 유통 > KRX 상장×3 = 파싱오류(LS에코 ×1e6) → 무효화 (우선주 감안 여유 ×3)
    list_shrs = mk.get("list_shrs")
    shares_bad = bool(list_shrs and shares_total and shares_total > list_shrs * 3)
    if shares_bad:
        shares_total = shares_common = None

    # DPS = alotMatter 보통주 결의 현금배당금 (이미 주당값 — 주식수 불필요)
    div_sum, _ = await _annual_summary(cc, fy)
    dps = (div_sum or {}).get("cash_dps") or None

    bps = round(_div(ctrl_equity, shares_total)) if (ctrl_equity and shares_total) else None
    eps_ttm = round(_div(ni_ttm, shares_common)) if (ni_ttm and shares_common) else None

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
        warnings.append("금융사(매출 계정 없음) — EV/EBITDA·PSR·FCF·순차입 = N/A(범주 부적합). PBR·PER·배당·ROE 중심.")
    if scale_verdict and scale_verdict["tier"] == "hard":
        warnings.append(f"⚠️ DART 재무 단위(스케일) 오류 감지({scale_verdict['hard_hit']}) — 순이익·자본 N/M 처리. 원문 확인 요망.")
    elif scale_verdict and scale_verdict["tier"] == "soft":
        warnings.append(f"시총 대비 재무 비율 이상치({scale_verdict['soft_hit']}) — 값은 유지하되 확인 권장(원샷 이익/자산매각 등 가능).")
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
            },
            "warnings": warnings,
            "note": "lean v1 — RIM·EV/EBITDA·PSR·FCF·5년밴드·PIT는 v1.1.",
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
