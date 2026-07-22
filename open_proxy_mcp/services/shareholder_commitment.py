"""shareholder_commitment — 밸류업/거버넌스 약속 vs 실제 이행 추적 (연중 지속 스튜어드십 Action Tool).

`proxy_advise_before_meeting`이 "이번 주총 안건에 어떻게 투표할까"라는 1회성 판단이라면, 이 tool은
"작년에 공표한 밸류업 계획(배당·자사주 소각)을 실제로 지켰나"를 주총과 무관하게 연중 추적한다.

4개 upstream을 재사용(새 파싱 로직 없음, 전부 기존 build_*_payload 그대로 호출):
  - value_up.build_value_up_payload(scope="commitments") — 밸류업 계획 원문 + 이미 있는
    treasury_cross_ref(소각 약속 vs 24개월 실제 이벤트 요약).
  - corp_gov_report.build_corp_gov_report_payload(scope="timeline") — 15개 지표 O/X 연도별 전환
    (transitions: improved/regressed).
  - dividend.build_dividend_payload(scope="summary"/"history") — 실제 배당 총액·성향·연도별 DPS.
  - treasury_share.build_treasury_share_payload(scope="summary") — 이번 세션에 원문 단위(백만원 등)
    미인식 버그를 고친 정확한 actual_amount_krw/cumulative_shares + 결정↔실행 사이클 매칭.

신규 로직은 딱 하나 — **자사주 소각의 장부가(BPS) 손익**:
    장부가손익(KRW) = (매입시점 BPS − 매입시점 가중평균 매입가) × 소각주식수
    가중평균 매입가 = actual_amount_krw ÷ cumulative_shares  (treasury_share, 이미 정확)
    매입시점 BPS    = controlling_equity(financial_metrics) ÷ shares_total(DART stockTotqySttus,
                      valuation.py의 _shares_outstanding 그대로 재사용)
배당은 이 BPS 계산에서 제외한다(배당은 자본만 줄고 주식수는 그대로라 BPS가 오히려 내려가는 반대
방향 효과 — 대화에서 확정, 섞으면 부정확). 대신 "주주환원 종합"에는 배당을 포함한다 — CSR 공식은
새로 안 만들고 `director_performance.py`의 기존 공식(총배당+총소각금액 ÷ 총순이익)을 그대로 재사용.

**sanity 필터**: treasury_share의 결정↔실행 사이클 매칭(`_link_cycles`)에 260707 세션에서 발견한
별개 오탐 버그가 남아있다(POSCO홀딩스·카카오·엘앤에프·포스코퓨처엠 확인 — 알려진 별개 이슈). 이 tool은
그 매칭을 무조건 신뢰하지 않고, `actual_amount_krw / decision.amount_krw` 비율이 0.3~3.0 밖이면 그
사이클을 계산에서 제외하고 `data_quality_flags`에 남긴다(조용히 틀린 값을 내지 않기 위함).
"""

from __future__ import annotations

import asyncio
from typing import Any

from open_proxy_mcp.dart.client import get_dart_client
from open_proxy_mcp.services.company import resolve_company_query
from open_proxy_mcp.services.contracts import (
    AnalysisStatus,
    ToolEnvelope,
    build_usage,
)
from open_proxy_mcp.services.value_up import build_value_up_payload
from open_proxy_mcp.services.corp_gov_report import build_corp_gov_report_payload
from open_proxy_mcp.services.dividend import build_dividend_payload
from open_proxy_mcp.services.treasury_share import build_treasury_share_payload
from open_proxy_mcp.services.valuation import _shares_outstanding, _pg_rows
from open_proxy_mcp.services.date_utils import resolve_date_window, format_yyyymmdd

_SANITY_LOW, _SANITY_HIGH = 0.3, 3.0


async def _bps_at_year(canonical_name: str, corp_code: str, year: int) -> dict[str, Any]:
    """그 연도 자기자본÷합계유통주식수 = BPS. financial_metrics의 `bps_krw` 필드는 실측 결과
    항상 None(미구현)이라 `total_equity_krw`(financial_metrics summary) + shares_total(DART
    stockTotqySttus, valuation.py의 `_shares_outstanding` 그대로 재사용)를 직접 조합한다.
    ⚠ `total_equity_krw`는 지배지분만이 아니라 총자본(비지배지분 포함)일 수 있음 — financial_metrics가
    이 scope에서 지배지분만 분리한 필드를 별도로 안 주기 때문(알려진 근사치, valuation.py처럼
    `_ctrl_equity` 정밀 분리를 하려면 fnlttSinglAcntAll 직접 재구현이 필요해 이번 스코프에서는
    보류 — 별도 과제)."""
    from open_proxy_mcp.services.financial_metrics import build_financial_metrics_payload

    client = get_dart_client()
    fm_task = build_financial_metrics_payload(canonical_name, scope="summary", year=year)
    try:
        sh = await _shares_outstanding(client, corp_code, year)
    except Exception:
        sh = {"total": None, "common": None}
    fm = None
    try:
        fm = await fm_task
    except Exception:
        fm = None
    total_equity = None
    if fm and isinstance(fm, dict):
        total_equity = ((fm.get("data") or {}).get("summary") or {}).get("total_equity_krw")
    shares_total = sh.get("total")
    bps = None
    if total_equity and shares_total:
        bps = round(total_equity / shares_total)
    return {"bps_krw": bps, "total_equity_krw": total_equity, "shares_total": shares_total}


def _acquisition_events(treasury_data: dict[str, Any]) -> list[dict[str, Any]]:
    events = treasury_data.get("events") or []
    return [e for e in events if e.get("event") == "acquisition_result"]


def _decision_for(events: list[dict[str, Any]], rcept_no: str) -> dict[str, Any] | None:
    for e in events:
        if e.get("rcept_no") == rcept_no and e.get("phase") == "decision":
            return e
    return None


async def _capital_return_impact(
    canonical_name: str, corp_code: str, treasury_data: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    """자사주소각 사이클별 장부가 손익 계산. sanity 필터에 걸리면 flags에 남기고 계산에서 제외."""
    events = treasury_data.get("events") or []
    cycles: list[dict[str, Any]] = []
    flags: list[str] = []

    for res in _acquisition_events(treasury_data):
        actual = res.get("actual_amount_krw")
        shares = res.get("cumulative_shares")
        linked = res.get("linked_decision_rcept_no")
        decision = _decision_for(events, linked) if linked else None
        if not actual or not shares or not decision:
            continue
        if not decision.get("for_cancelation"):
            continue  # 소각 목적 아닌 매입(자사주 상여 등)은 이 계산 대상 아님
        decision_amount = decision.get("amount_krw")
        if not decision_amount:
            continue
        ratio = actual / decision_amount
        if not (_SANITY_LOW <= ratio <= _SANITY_HIGH):
            flags.append(
                f"사이클 제외(단위/매칭 이상 의심): rcept={res.get('rcept_no')} "
                f"actual={actual:,} vs decision={decision_amount:,} (비율 {ratio:.2f}) — "
                f"_link_cycles 매칭 오탐 가능성(알려진 별개 이슈), 장부가손익 계산에서 제외"
            )
            continue

        avg_price = actual / shares
        year = int((res.get("main_report_date") or res.get("rcept_dt") or "0000")[:4])
        if not year:
            continue
        bps_info = await _bps_at_year(canonical_name, corp_code, year)
        bps = bps_info.get("bps_krw")
        cycle: dict[str, Any] = {
            "rcept_no": res.get("rcept_no"),
            "decision_rcept_no": linked,
            "period": f"{res.get('period_start', '-')} ~ {res.get('period_end', '-')}",
            "shares_acquired": shares,
            "avg_acquisition_price_krw": round(avg_price),
            "acquisition_amount_krw": actual,
            "bps_at_acquisition_krw": bps,
        }
        if bps:
            premium_discount_pct = round((avg_price / bps - 1) * 100, 2)
            book_value_gain_loss_krw = round((bps - avg_price) * shares)
            cycle["premium_discount_pct"] = premium_discount_pct
            cycle["book_value_gain_loss_krw"] = book_value_gain_loss_krw
            cycle["note"] = (
                "매입가가 BPS보다 쌈(장부가 기준 이득)" if book_value_gain_loss_krw > 0
                else "매입가가 BPS보다 비쌈(장부가 기준 손실, 내재가치 판단은 별도)"
            )
        else:
            cycle["note"] = "그 연도 BPS 확보 실패 — 장부가손익 산출 불가"
            flags.append(f"BPS 확보 실패: rcept={res.get('rcept_no')} year={year}")
        cycles.append(cycle)

    return cycles, flags


def _overall_shareholder_return(
    dividend_summary: dict[str, Any], treasury_summary: dict[str, Any]
) -> dict[str, Any]:
    """CSR = (배당총액 + 소각금액) / 순이익 * 100 — director_performance.py의 기존 공식 그대로 재사용
    (새 산식 발명 안 함). 최근 확정 사업연도(dividend.summary) 스냅샷 기준 — 다년 합산이 아님을 명시."""
    dividend_krw = (dividend_summary.get("total_amount_mil") or 0) * 1_000_000
    net_income_krw = (dividend_summary.get("net_income_consolidated_mil") or 0) * 1_000_000
    cancelation_krw = treasury_summary.get("cancelation_amount_total_krw") or 0
    total_return = dividend_krw + cancelation_krw
    csr_pct = round(total_return / net_income_krw * 100, 1) if net_income_krw > 0 else None
    return {
        "period_note": "배당=최근 확정 사업연도 스냅샷, 자사주소각=조회 lookback 기간 누적 — 서로 다른 기간 기준이라 단순 참고용 합산",
        "dividend_krw": dividend_krw,
        "buyback_cancelation_krw": cancelation_krw,
        "total_shareholder_return_krw": total_return,
        "net_income_krw": net_income_krw,
        "cash_shareholder_return_pct": csr_pct,
    }


async def _fill_yearend_yield(isu_cd: str, div_history: list[dict[str, Any]]) -> None:
    """DART 자체 배당수익률(yield_dart, dividend.history의 yield_pct)은 결의 시점 시가 기준이라
    옛 연도일수록 결측이 많다(실측 확인 260707: 미래에셋증권·현대차·SKC 전부 2021·2022년 None,
    2023년부터만 값 있음). krx_weekly(연말종가, valuation.py의 _annual_pit_band와 동일 쿼리 패턴)
    로 DPS÷연말종가를 직접 계산해 공백을 메운다 — 원래 값(yield_pct)은 안 건드리고
    `yield_pct_yearend`로 별도 필드 추가(기준일이 다르므로 값이 다를 수 있음을 명시)."""
    rows = await asyncio.to_thread(_pg_rows,
        "SELECT DISTINCT ON (substring(bas_dd,1,4)) substring(bas_dd,1,4), close "
        "FROM krx_weekly WHERE isu_cd=%s AND substring(bas_dd,5,2)='12' "
        "ORDER BY substring(bas_dd,1,4), bas_dd DESC", (isu_cd,))
    if not rows:
        return
    close_by_year = {int(yr): float(close) for yr, close in rows if close}
    for h in div_history:
        dps = h.get("annual_dps")
        close = close_by_year.get(h.get("year"))
        if dps and close:
            h["yield_pct_yearend"] = round(dps / close * 100, 2)


async def build_shareholder_commitment_payload(
    company_query: str, *, lookback_years: int = 3, format: str = "md"
) -> dict[str, Any]:
    calls_start = get_dart_client().api_call_snapshot()
    resolution = await resolve_company_query(company_query)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return ToolEnvelope(
            tool="shareholder_commitment",
            status=resolution.status,
            subject=company_query,
            warnings=[f"'{company_query}' 상장사를 찾지 못함"],
            data={"query": company_query, "candidates": resolution.candidates},
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        return ToolEnvelope(
            tool="shareholder_commitment",
            status=AnalysisStatus.AMBIGUOUS,
            subject=company_query,
            data={"query": company_query, "candidates": resolution.candidates},
        ).to_dict()

    selected = resolution.selected
    corp_code = selected["corp_code"]
    canonical_name = selected.get("corp_name", company_query)

    # 260707 버그 수정: value_up 기본 조회 구간이 최근 12개월 rolling이라, lookback_years를
    # 명시적으로 안 넘기면 그보다 오래된 밸류업 계획(연 1회 공시가 흔함)을 "없음"으로 오판한다
    # (미래에셋증권 실측 확인 — 2024-08 최초공시·2025-06 이행현황이 있는데 기본 구간에 안 걸림).
    vu_start, vu_end, _ = resolve_date_window(lookback_months=lookback_years * 12)
    value_up_task = build_value_up_payload(
        company_query, scope="commitments",
        start_date=format_yyyymmdd(vu_start), end_date=format_yyyymmdd(vu_end),
    )
    gov_task = build_corp_gov_report_payload(company_query, scope="timeline")
    div_summary_task = build_dividend_payload(company_query, scope="summary")
    div_history_task = build_dividend_payload(company_query, scope="history", years=lookback_years)
    treasury_task = build_treasury_share_payload(
        company_query, scope="summary", lookback_months=lookback_years * 12
    )

    value_up, gov, div_summary, div_history, treasury = await asyncio.gather(
        value_up_task, gov_task, div_summary_task, div_history_task, treasury_task,
        return_exceptions=True,
    )

    warnings: list[str] = []

    def _data(res, name: str) -> dict[str, Any]:
        """upstream 예외뿐 아니라 **upstream 자체 warnings도 그대로 전파**한다(260707 발견 —
        value_up 호출 시 조회 구간을 안 넘겨 실제로는 있는 밸류업 계획을 '없음'으로 오판했는데,
        원인은 upstream이 낸 경고를 조용히 버렸기 때문. 이 패턴은 다른 upstream 조합에서도
        반복될 수 있는 일반적 위험이라, 예외/정상 무관하게 upstream warnings를 항상 끌어올린다)."""
        if isinstance(res, BaseException):
            warnings.append(f"{name} 조회 실패: {res}")
            return {}
        for w in (res.get("warnings") or []):
            warnings.append(f"[{name}] {w}")
        return res.get("data") or {}

    value_up_data = _data(value_up, "value_up")
    gov_data = _data(gov, "corp_gov_report")
    div_summary_data = _data(div_summary, "dividend(summary)")
    div_history_data = _data(div_history, "dividend(history)")
    treasury_data = _data(treasury, "treasury_share")

    # value_up의 자체 진단 신호 — "요청 구간엔 없지만 더 넓은 구간엔 있다"를 명시적으로 확인.
    # lookback_years를 넘겼는데도 이 상태면 그보다 더 오래된 계획이라는 뜻이라 사용자에게 투명하게 알림.
    if value_up_data.get("availability_status") == "exists_outside_requested_window":
        diag = (value_up_data.get("search_diagnostics") or {}).get("diagnostic_window") or {}
        warnings.append(
            f"밸류업 계획이 lookback_years={lookback_years}년보다 더 오래된 시점에 존재함"
            f"(진단 구간 {diag.get('start_date')}~{diag.get('end_date')}에 {diag.get('dart_filing_count', 0)}건 확인) "
            "— lookback_years를 늘려서 재조회 권장."
        )

    isu_cd = selected.get("stock_code")
    div_history_list = div_history_data.get("history") or []
    if isu_cd and div_history_list:
        await _fill_yearend_yield(isu_cd, div_history_list)

    capital_return_cycles, quality_flags = await _capital_return_impact(canonical_name, corp_code, treasury_data)
    overall = _overall_shareholder_return(
        div_summary_data.get("summary") or {}, treasury_data.get("summary") or {}
    )
    overall["total_book_value_gain_loss_krw"] = sum(
        c.get("book_value_gain_loss_krw", 0) for c in capital_return_cycles if c.get("book_value_gain_loss_krw")
    )

    data: dict[str, Any] = {
        "query": company_query,
        "canonical_name": canonical_name,
        "corp_code": corp_code,
        "lookback_years": lookback_years,
        "commitments": {
            "latest_plan": value_up_data.get("latest_plan"),
            "latest_status": value_up_data.get("latest_status"),
            "treasury_cross_ref": value_up_data.get("treasury_cross_ref"),
        },
        "capital_return_execution": {
            "buyback_cycles": capital_return_cycles,
            "dividend_history": div_history_data.get("history"),
        },
        "governance_trend": {
            "transitions": gov_data.get("transitions"),
        },
        "overall": overall,
        "data_quality_flags": quality_flags,
        "usage": build_usage(get_dart_client().api_call_snapshot() - calls_start),
    }

    return ToolEnvelope(
        tool="shareholder_commitment",
        status=AnalysisStatus.EXACT,
        subject=canonical_name,
        data=data,
        warnings=warnings,
    ).to_dict()
