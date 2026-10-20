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
    매입시점 BPS    = 지배자본 ÷ shares_total(DART stockTotqySttus, price_multiple_data.py의
                      _shares_outstanding 그대로 재사용)
    지배자본은 비지배지분을 뺀 값이다 — 지주사는 연결 자본총계 대부분이 자회사 비지배 몫이라 총계를 쓰면 BPS 가
    몇 배 과대해져 「싸게 샀다」가 거꾸로 나온다(한화 FY2025 말 4.09배). 기준 시점은 **매입 기간 말까지 공시돼 있던
    최신 재무상태표**이고(결과보고서 접수 해 말이 아니다) 어느 자본·어느 보고서·어느 날로 나눴는지는 사이클마다
    `bps_basis` 에 밝힌다.
배당은 이 BPS 계산에서 제외한다(배당은 자본만 줄고 주식수는 그대로라 BPS가 오히려 내려가는 반대
방향 효과 — 대화에서 확정, 섞으면 부정확). 대신 "주주환원 종합"에는 배당을 포함한다.

**CSR 분자 = 배당 + 자사주 "매입"(취득결정+신탁체결), "소각"이 아니다** (260914 변경, 마르코와
대화에서 확정). 소각금액은 몇 년 전에 사둔 재고 주식을 지금 없애는 것까지 섞여서 "지금 이
회사가 주주환원에 새 자금을 투입하고 있는가"를 왜곡한다(실측: 삼성화재·SK텔레콤은 소각결정은
있었지만 최근 24개월 신규 취득은 0건 — 전부 과거 보유분 처리였다). 매입(취득결정
acquisition_amount_total_krw + 신탁체결 trust_contract_amount_total_krw)은 그 기간에 실제로
지출된 현금 기준이라 왜곡이 없다. `director_performance.py`/`proxy_advise_before_meeting.py`의
CSR(의결권 추천에 쓰임)은 별개 계산식이고 이번 변경 대상이 아니다 — 이 tool 한정.

**CSR 분모·분자 기간을 배당과 동일 회계연도로 정합** (260914 후속 변경, 마르코와 대화에서 확정).
기존엔 배당(dividend.summary, 최근 확정 사업연도 1개년 스냅샷)과 매입(treasury_share,
lookback_years 기본 3년 누적)의 기간이 서로 달라 CSR이 최대 2배 가까이 부풀려졌다(실측: 신한지주
— 3년 매입 기준 94.5% vs 회사 자체 공시 "주주환원율 50.2%"; 1년으로 정합 후 49.2%로 거의 일치).
그래서 dividend.summary의 `stlm_dt`(결산기준일)로 배당이 커버하는 정확한 회계연도 구간을 구하고,
`treasury_share`를 그 구간(`start_date`~`end_date`)으로 **별도 재호출**해서 CSR 전용 매입액을 만든다.
BPS 손익 사이클 표(`capital_return_execution.buyback_cycles`)는 여전히 `lookback_years` 전체를 쓴다
(더 긴 역사가 유용하고, 그 표는 애초에 기간이 섞여도 왜곡되지 않는 개별 사이클 나열이라 문제 없음).
회계연도를 못 구하면(결산월 미상) 기존 lookback_years 매입액으로 폴백하고 `data_quality_flags`에
남긴다 — 조용히 안 맞는 숫자를 내지 않는다.

**sanity 필터**: treasury_share의 결정↔실행 사이클 매칭(`_link_cycles`)에 260707 세션에서 발견한
별개 오탐 버그가 남아있다(POSCO홀딩스·카카오·엘앤에프·포스코퓨처엠 확인 — 알려진 별개 이슈). 이 tool은
그 매칭을 무조건 신뢰하지 않고, `actual_amount_krw / decision.amount_krw` 비율이 0.3~3.0 밖이면 그
사이클을 계산에서 제외하고 `data_quality_flags`에 남긴다(조용히 틀린 값을 내지 않기 위함).
"""

from __future__ import annotations

import asyncio
from typing import Any

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services import controlling_eq as _ceq
from open_proxy_mcp.services import report_calendar as _rcal
from open_proxy_mcp.services.company import (COMPANY_LOOKUP_NEXT_ACTION,
                                             company_ambiguous_warning,
                                             company_not_found_warning,
                                             resolve_company_query)
from open_proxy_mcp.services.contracts import (
    AnalysisStatus,
    ToolEnvelope,
    build_usage,
)
from open_proxy_mcp.services.value_up import build_value_up_payload
from open_proxy_mcp.services.corp_gov_report import build_corp_gov_report_payload
from open_proxy_mcp.services.dividend import build_dividend_payload, _fiscal_period
from open_proxy_mcp.services.treasury_share import build_treasury_share_payload
from open_proxy_mcp.services.price_multiple_data import _shares_outstanding, _pg_rows
from open_proxy_mcp.services.date_utils import resolve_date_window, format_yyyymmdd
from open_proxy_mcp.services.scale_guard import check_magnitude_jump

_SANITY_LOW, _SANITY_HIGH = 0.3, 3.0


#: 분기·사업보고서 칸 번호(`report_calendar` 의 quarter 1~4) → DART 보고서 코드 · 이름.
_REPORT_CODE = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
_REPORT_LABEL = {1: "1분기보고서", 2: "반기보고서", 3: "3분기보고서", 4: "사업보고서"}


def _ymd(v: Any) -> str | None:
    """`2025-11-24`·`20251124` → `20251124`. 날짜가 아니면 None."""
    s = "".join(ch for ch in str(v or "") if ch.isdigit())
    return s[:8] if len(s) >= 8 else None


def _iso(ymd: str | None) -> str | None:
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}" if ymd and len(ymd) == 8 else None


async def _shares_total_krw(client: Any, corp_code: str, year: int) -> int | None:
    try:
        sh = await _shares_outstanding(client, corp_code, year)
    except Exception:
        return None
    return sh.get("total")


async def _equity_annual(canonical_name: str, year: int) -> dict[str, Any]:
    """종전 길 — 재무 지표 도구 summary 의 **그 사업연도 말** 자본.

    지배자본은 재무 지표 도구가 주는 `controlling_equity_krw`(수집·배수 도구와 같은 공용 규칙으로 고른 값)를 쓴다. 그 칸이 아예 없는
    옛 응답이거나 지배자본을 못 구해 자본총계로 물러난 응답(`TOTAL_FALLBACK`)이면 자본총계(비지배 포함)를 그대로 쓰되 그 사실을 `warning` 에 남긴다
    — 조용히 총계로 나누지 않는다."""
    from open_proxy_mcp.services.financial_metrics import build_financial_metrics_payload

    try:
        fm = await build_financial_metrics_payload(canonical_name, scope="summary", year=year)
    except Exception as exc:  # noqa: BLE001 — 상류 실패는 「없음」이 아니라 사유와 함께 올린다
        return {"equity_krw": None, "warning": f"재무 지표 조회 실패({type(exc).__name__})"}
    summary = ((fm or {}).get("data") or {}).get("summary") or {}
    total = summary.get("total_equity_krw")
    case = summary.get("controlling_equity_case")
    out: dict[str, Any] = {
        "total_equity_krw": total, "equity_case": case,
        "fiscal_year": year, "reprt_code": summary.get("reprt_code") or "11011",
    }
    if "controlling_equity_krw" in summary:
        ctrl = summary.get("controlling_equity_krw")
        if ctrl is not None and case != "TOTAL_FALLBACK":
            return {**out, "equity_krw": ctrl, "equity_basis": "controlling"}
        if total:
            return {**out, "equity_krw": total, "equity_basis": "total_incl_minority",
                    "warning": f"지배자본을 못 구해 자본총계(비지배 포함)로 나눔 — year={year}"}
        return {**out, "equity_krw": None, "warning": f"자기자본 칸 없음 — year={year}"}
    if total:
        return {**out, "equity_krw": total, "equity_basis": "total_incl_minority",
                "warning": f"재무 지표 응답에 지배자본 칸이 없어 자본총계(비지배 포함)로 나눔 — year={year}"}
    return {**out, "equity_krw": None, "warning": f"자기자본 칸 없음 — year={year}"}


async def _equity_as_of(
    client: Any, corp_code: str, ref_ymd: str, *, fiscal_month: int | None, ticker: str | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """기준일(매입 기간 말)까지 **공시돼 있던 최신 정기보고서**의 재무상태표에서 지배자본을 고른다 — DART 1콜(전체 재무제표, 연결).

    어느 보고서인가: `report_calendar.pit_cell` — 분기·반기는 기간 말 + 45일, 사업보고서는 결산 다음 넷째 달 1일(외국 상장사 60일·다섯째 달)
    까지 낸 것으로 본다. 법정 기한 전에 일찍 낸 다음 보고서는 쓰지 않는다(미래 정보를 안 쓰는 쪽). 결산월을 모르면 12월로 본다.
    그 보고서가 정정공시를 거쳤으면 응답은 **최신 정정본**이라 접수일이 기준일 뒤일 수 있다 — `filed_after_reference_date` 로 밝힌다.

    못 쓰면 (None, 사유)를 돌려 호출부가 종전 연말 길로 물러나게 한다: 연결 재무제표 없음·조회 실패 · 지배자본 못 고름 · 원화가 아닌 보고 통화 ·
    같은 응답 안 전기말과 100배 이상 10ⁿ 점프(단위가 틀린 보고서 — 재무 지표 도구의 보고서 사이 단위 가드가 보는 꼴)."""
    fye = _rcal.fiscal(fiscal_month, ticker)
    try:
        fy, q = _rcal.pit_cell(ref_ymd, fye)
    except (ValueError, TypeError):
        return None, f"기준일 형식 이상({ref_ymd})"
    rc = _REPORT_CODE[q]
    try:
        d = await client.get_fnltt_singl_acnt_all(corp_code, str(fy), rc, "CFS")
    except DartClientError as exc:
        return None, f"{fy} {_REPORT_LABEL[q]} 연결 재무제표 조회 실패(DART {getattr(exc, 'status', '?')})"
    except Exception as exc:  # noqa: BLE001 — 통신 오류(시간 초과·HTTP 5xx 등)도 도구 전체를 죽이지 않고 연말 길로 물러난다(종전 길도 예외를 삼켰다)
        return None, f"{fy} {_REPORT_LABEL[q]} 연결 재무제표 조회 실패({type(exc).__name__})"
    rows = (d.get("list", d) if isinstance(d, dict) else d) or []
    if not rows:
        return None, f"{fy} {_REPORT_LABEL[q]} 연결 재무제표 없음"
    cur = {(r.get("currency") or "").strip() for r in rows if r.get("sj_div") == "BS"} - {""}
    if cur and cur != {"KRW"}:
        return None, f"{fy} {_REPORT_LABEL[q]} 보고 통화가 원화가 아님({','.join(sorted(cur))})"
    ctrl, case = _ceq.controlling_eq(rows, "thstrm_amount")
    if ctrl is None:
        return None, f"{fy} {_REPORT_LABEL[q]} 지배자본을 못 고름({case})"
    prev, _pc = _ceq.controlling_eq(rows, "frmtrm_amount")
    jump = check_magnitude_jump(ctrl, prev)
    if jump.get("triggered"):
        return None, f"{fy} {_REPORT_LABEL[q]} 자본이 전기말의 10^{jump.get('power')}배 — 단위 오류 의심"
    total = _ceq.total_eq(rows, lambda r: _ceq.amount(r, "thstrm_amount"))
    rcept_no = str(next((r.get("rcept_no") for r in rows if r.get("rcept_no")), "") or "")
    rcept_dt = _ymd(rcept_no[:8]) if rcept_no else None
    return {
        "equity_krw": ctrl, "equity_basis": "controlling", "equity_case": case,
        "total_equity_krw": total, "fiscal_year": fy, "reprt_code": rc, "quarter": q,
        "period_end": _rcal.cell_end(fy, q, fye).isoformat(),
        "rcept_no": rcept_no or None, "rcept_dt": rcept_dt,
        "filed_after_reference_date": bool(rcept_dt and rcept_dt > ref_ymd),
    }, None


async def _bps_at_cycle(
    canonical_name: str, corp_code: str, year: int, *,
    ref_ymd: str | None, ref_kind: str, fiscal_month: int | None, ticker: str | None,
) -> dict[str, Any]:
    """사이클 하나의 BPS = 지배자본 ÷ 합계 유통주식수, 그리고 어느 기준으로 나눴는지(`bps_basis`).

    자본: ① 기준일(매입 기간 말, 없으면 결과보고서 접수일)까지 공시된 최신 정기보고서 재무상태표의 지배자본(`_equity_as_of`, DART 1콜)
    ② 그게 안 되면 종전처럼 `year` 사업연도 말 자본(`_equity_annual` — 재무 지표 도구의 `controlling_equity_krw`). ②로 물러나면 `fallback_reason` 에 사유.
    주식수: 종전 그대로 `year` 사업보고서 합계 유통주식수(분기보고서의 주식의 총수 칸은 비어 오는 회사가 있어 못 쓴다). 그 해 사업보고서가 아직 안
    나온 사이클은 기준일에 공시돼 있던 최신 사업보고서의 주식수로 물러난다(종전엔 BPS 없음).
    """
    client = get_dart_client()
    equity: dict[str, Any] | None = None
    fallback_reason: str | None = None
    if ref_ymd:
        equity, fallback_reason = await _equity_as_of(
            client, corp_code, ref_ymd, fiscal_month=fiscal_month, ticker=ticker)
    else:
        fallback_reason = "매입 기간 말·접수일을 몰라 기준일을 못 정함"
    source = "latest_filed_as_of_reference_date"
    if equity is None:
        source = "annual_year_end"
        equity = await _equity_annual(canonical_name, year)

    shares_year = year
    shares_total = await _shares_total_krw(client, corp_code, year)
    shares_note = f"{year} 사업보고서 합계 유통주식수"
    if not shares_total and ref_ymd:
        pit_year = _rcal.pit_annual(ref_ymd, _rcal.fiscal(fiscal_month, ticker))
        if pit_year != year:
            shares_total = await _shares_total_krw(client, corp_code, pit_year)
            if shares_total:
                shares_year = pit_year
                shares_note = f"{pit_year} 사업보고서 합계 유통주식수({year} 보고서가 아직 없어 기준일에 공시돼 있던 최신 보고서로 대신함)"

    equity_krw = equity.get("equity_krw")
    total_krw = equity.get("total_equity_krw")
    bps = round(equity_krw / shares_total) if equity_krw and shares_total else None
    bps_total = round(total_krw / shares_total) if total_krw and shares_total else None
    basis: dict[str, Any] = {
        "equity": equity.get("equity_basis"),
        "equity_case": equity.get("equity_case"),
        "controlling_equity_krw": equity_krw if equity.get("equity_basis") == "controlling" else None,
        "total_equity_krw": total_krw,
        "bps_if_total_equity_krw": bps_total,
        "shares_total": shares_total,
        "shares_basis": shares_note if shares_total else None,
        "shares_fiscal_year": shares_year if shares_total else None,
        "balance_sheet": {
            "source": source,
            "reference_date": _iso(ref_ymd),
            "reference_kind": ref_kind,
            "fiscal_year": equity.get("fiscal_year"),
            "report": _REPORT_LABEL.get(equity.get("quarter")) if equity.get("quarter") else "사업보고서",
            "reprt_code": equity.get("reprt_code"),
            "period_end": equity.get("period_end"),
            "rcept_no": equity.get("rcept_no"),
            "rcept_dt": _iso(equity.get("rcept_dt")),
            "filed_after_reference_date": equity.get("filed_after_reference_date"),
        },
        "fallback_reason": fallback_reason if source == "annual_year_end" else None,
    }
    return {
        "bps_krw": bps, "total_equity_krw": total_krw, "shares_total": shares_total,
        "bps_basis": basis, "warning": equity.get("warning"),
    }


def _acquisition_events(treasury_data: dict[str, Any]) -> list[dict[str, Any]]:
    events = treasury_data.get("events") or []
    return [e for e in events if e.get("event") == "acquisition_result"]


def _decision_for(events: list[dict[str, Any]], rcept_no: str) -> dict[str, Any] | None:
    for e in events:
        if e.get("rcept_no") == rcept_no and e.get("phase") == "decision":
            return e
    return None


async def _capital_return_impact(
    canonical_name: str, corp_code: str, treasury_data: dict[str, Any], *,
    fiscal_month: int | None = None, ticker: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """자사주소각 사이클별 장부가 손익 계산. sanity 필터에 걸리면 flags에 남기고 계산에서 제외.

    BPS 는 지배자본 기준이고 기준일은 사이클의 매입 기간 말이다(`_bps_at_cycle` — 어느 기준으로 나눴는지 사이클마다 `bps_basis`).
    `fiscal_month`(결산월)·`ticker` 는 어느 정기보고서가 그 날 공시돼 있었는지 정하는 공시 달력에 쓴다(없으면 12월 결산)."""
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
        ref_kind = "period_end"
        ref_ymd = _ymd(res.get("period_end"))
        if not ref_ymd:
            ref_kind, ref_ymd = "rcept_dt", _ymd(res.get("rcept_dt"))
        bps_info = await _bps_at_cycle(
            canonical_name, corp_code, year,
            ref_ymd=ref_ymd, ref_kind=ref_kind, fiscal_month=fiscal_month, ticker=ticker,
        )
        bps = bps_info.get("bps_krw")
        basis = bps_info.get("bps_basis") or {}
        for msg in (
            bps_info.get("warning"),
            (f"BPS 기준일 폴백: {basis['fallback_reason']} — {year} 사업연도 말 자본으로 나눔"
             if basis.get("fallback_reason") else None),
        ):
            if msg and f"{msg} (rcept={res.get('rcept_no')})" not in flags:
                flags.append(f"{msg} (rcept={res.get('rcept_no')})")
        cycle: dict[str, Any] = {
            "rcept_no": res.get("rcept_no"),
            "decision_rcept_no": linked,
            "period": f"{res.get('period_start', '-')} ~ {res.get('period_end', '-')}",
            "shares_acquired": shares,
            "avg_acquisition_price_krw": round(avg_price),
            "acquisition_amount_krw": actual,
            "bps_at_acquisition_krw": bps,
            "bps_basis": basis,
        }
        if bps and bps > 0:
            premium_discount_pct = round((avg_price / bps - 1) * 100, 2)
            book_value_gain_loss_krw = round((bps - avg_price) * shares)
            cycle["premium_discount_pct"] = premium_discount_pct
            cycle["book_value_gain_loss_krw"] = book_value_gain_loss_krw
            cycle["note"] = (
                "매입가가 BPS보다 쌈(장부가 기준 이득)" if book_value_gain_loss_krw > 0
                else "매입가가 BPS보다 비쌈(장부가 기준 손실, 내재가치 판단은 별도)"
            )
        elif bps:
            # 지배자본이 음수(자본잠식)면 BPS 가 음수다 — 매입가와의 비율·손익이 뜻을 잃으므로 내지 않는다(지배자본은 총계보다 음수가 되기 쉽다).
            cycle["note"] = "매입시점 지배자본 BPS 가 0 이하(자본잠식) — 장부가손익 산출 불가"
            flags.append(f"BPS 0 이하(자본잠식): rcept={res.get('rcept_no')} year={year} bps={bps:,}")
        else:
            cycle["note"] = "그 연도 BPS 확보 실패 — 장부가손익 산출 불가"
            flags.append(f"BPS 확보 실패: rcept={res.get('rcept_no')} year={year}")
        cycles.append(cycle)

    return cycles, flags


def _overall_shareholder_return(
    dividend_summary: dict[str, Any],
    treasury_summary: dict[str, Any],
    *,
    csr_treasury_summary: dict[str, Any] | None,
    fiscal_period: dict[str, str] | None,
) -> dict[str, Any]:
    """CSR(현금성주주환원율/주주환원율) = (배당총액 + 자사주 매입액) / 순이익 * 100.

    260914부터 분자를 소각금액(cancelation_amount_total_krw)에서 매입액(취득결정+신탁체결)으로
    변경 — 소각은 과거 보유분 처리까지 섞여 "이번 기간 실제 신규 지출"을 왜곡하기 때문(실측:
    삼성화재·SK텔레콤은 소각결정은 있었지만 최근 24개월 신규 취득 0건).

    260914 후속: 매입액은 `csr_treasury_summary`(배당과 동일 회계연도로 별도 조회한 treasury_share
    summary)에서 가져온다 — `treasury_summary`(lookback_years 누적, BPS 사이클 표용)를 그대로 쓰면
    배당(1개년)과 기간이 안 맞아 CSR이 최대 2배 가까이 부풀려진다(실측: 신한지주 3년 매입 기준
    94.5% vs 회사 자체 "주주환원율 50.2%" — 1년 정합 후 49.2%로 근접). `fiscal_period`를 못 구해
    `csr_treasury_summary`가 None이면 `treasury_summary`로 폴백하고 그 사실을 `period_note`에 남긴다
    (조용히 안 맞는 숫자를 내지 않기 위함 — 호출부에서 이 경우 data_quality_flags에도 남긴다).

    director_performance.py/proxy_advise_before_meeting.py의 CSR은 별개 계산식(소각 기준 유지) —
    이 tool 한정 변경."""
    dividend_krw = (dividend_summary.get("total_amount_mil") or 0) * 1_000_000
    net_income_krw = (dividend_summary.get("net_income_consolidated_mil") or 0) * 1_000_000

    if csr_treasury_summary is not None:
        acquisition_source = csr_treasury_summary
        period_note = (
            f"배당·자사주매입 모두 동일 회계연도({fiscal_period['start']}~{fiscal_period['end']}) 기준 — 정합됨."
            if fiscal_period else
            "배당·자사주매입 모두 동일 회계연도 기준 — 정합됨."
        )
    else:
        acquisition_source = treasury_summary
        period_note = (
            f"⚠ 회계연도를 못 구해 배당=최근 확정 사업연도 스냅샷(1개년), 자사주매입·소각=조회 "
            "lookback 기간 누적(여러 해)을 그대로 합산 — 기간 불일치로 CSR이 부풀려질 수 있음."
        )
    acquisition_krw = (
        (acquisition_source.get("acquisition_amount_total_krw") or 0)
        + (acquisition_source.get("trust_contract_amount_total_krw") or 0)
    )
    cancelation_krw = treasury_summary.get("cancelation_amount_total_krw") or 0  # 참고용, CSR 분자 아님(lookback_years 기준 그대로)
    total_return = dividend_krw + acquisition_krw
    csr_pct = round(total_return / net_income_krw * 100, 1) if net_income_krw > 0 else None
    return {
        "period_note": period_note,
        "csr_fiscal_period": fiscal_period,
        "csr_period_matched": csr_treasury_summary is not None,
        "dividend_krw": dividend_krw,
        "buyback_acquisition_krw": acquisition_krw,
        "buyback_cancelation_krw": cancelation_krw,
        "total_shareholder_return_krw": total_return,
        "net_income_krw": net_income_krw,
        "cash_shareholder_return_pct": csr_pct,
    }


_DECISION_EVENT_LABELS = {
    "acquisition_decision": "자사주 취득결정",
    "disposal_decision": "자사주 처분결정",
    "cancelation_decision": "자사주 소각결정",
    "trust_contract": "자사주 신탁계약체결",
    "trust_termination": "자사주 신탁해지",
}


def _latest_events_beyond_period(
    treasury_data: dict[str, Any], fiscal_period: dict[str, str] | None
) -> list[dict[str, Any]]:
    """CSR이 커버하는 회계연도(fiscal_period) **이후**에 새로 나온 자사주 결정 공시를 크기 무관하게
    전부 모은다 (260914 재변경, 마르코와 대화에서 확정).

    배경: CSR을 배당과 회계연도로 정합시켰더니, 그 연도가 끝난 뒤 나온 대형 공시(예: SK하이닉스
    2026-08-19 취득결정 40.00조원, 소각목적)가 FY2025 CSR엔 (정확히) 안 잡혀서 "최신 데이터를
    놓쳤다"는 오해를 샀다. 실제론 안 놓쳤음(24개월 요약·value_up 교차참조엔 있었음) — 다만 CSR
    본문 어디에도 눈에 띄게 안 보여서 실질적으로 놓친 것과 같았다. 그래서 CSR 비율 자체(분모=
    확정 순이익과 짝지어야 하는 값)는 안 건드리고, **회계연도 밖 최신 이벤트를 결과 최상단에
    무조건 노출**하는 별도 섹션을 추가한다 — 크기와 무관하게 전부, 최신순."""
    if not fiscal_period:
        return []
    cutoff = fiscal_period["end"].replace("-", "")  # "YYYY-MM-DD" -> "YYYYMMDD" (rcept_dt와 동일 형식)
    events = treasury_data.get("events") or []
    later = [
        e for e in events
        if e.get("event") in _DECISION_EVENT_LABELS and (e.get("rcept_dt") or "") > cutoff
    ]
    later.sort(key=lambda e: e.get("rcept_dt", ""), reverse=True)
    return [
        {
            "event": e.get("event"),
            "event_label": _DECISION_EVENT_LABELS.get(e.get("event"), e.get("event")),
            "rcept_dt": e.get("rcept_dt"),
            "rcept_no": e.get("rcept_no"),
            "amount_krw": e.get("amount_krw"),
            "for_cancelation": e.get("for_cancelation"),
        }
        for e in later
    ]


async def _fill_yearend_yield(ticker: str, div_history: list[dict[str, Any]]) -> None:
    """DART 자체 배당수익률(yield_dart, dividend.history의 yield_pct)은 결의 시점 시가 기준이라
    옛 연도일수록 결측이 많다(실측 확인 260707: 미래에셋증권·현대차·SKC 전부 2021·2022년 None,
    2023년부터만 값 있음). krx_weekly(연말종가, price_multiple_data.py의 _annual_pit_band와 동일 쿼리 패턴)
    로 DPS÷연말종가를 직접 계산해 공백을 메운다 — 원래 값(yield_pct)은 안 건드리고
    `yield_pct_yearend`로 별도 필드 추가(기준일이 다르므로 값이 다를 수 있음을 명시)."""
    rows = await asyncio.to_thread(_pg_rows,
        "SELECT DISTINCT ON (substring(price_dd,1,4)) substring(price_dd,1,4), close "
        "FROM krx_weekly WHERE ticker=%s AND substring(price_dd,5,2)='12' "
        "ORDER BY substring(price_dd,1,4), price_dd DESC", (ticker,))
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
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        return ToolEnvelope(
            tool="shareholder_commitment",
            status=AnalysisStatus.AMBIGUOUS,
            subject=company_query,
            warnings=[company_ambiguous_warning(company_query, resolution.candidates)],
            data={"query": company_query, "candidates": resolution.candidates},
            next_actions=[COMPANY_LOOKUP_NEXT_ACTION],
        ).to_dict()
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return ToolEnvelope(
            tool="shareholder_commitment",
            status=resolution.status,
            subject=company_query,
            warnings=[company_not_found_warning(company_query, listed_only=True)],
            data={"query": company_query, "candidates": resolution.candidates},
            next_actions=[COMPANY_LOOKUP_NEXT_ACTION],
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
    #: 「조회 실패」로 끝난 상류(예외 또는 status=error). 261018: 종전엔 상류가 전부 DART 오류로 실패해도 이 도구는
    #: exact 였다 — 상류 경고만 옮기고 상태값은 「성공」이었다. 일부 실패면 partial, 전부 실패면 error.
    failed_upstreams: list[str] = []
    upstream_names: list[str] = []

    def _data(res, name: str) -> dict[str, Any]:
        """upstream 예외뿐 아니라 **upstream 자체 warnings도 그대로 전파**한다(260707 발견 —
        value_up 호출 시 조회 구간을 안 넘겨 실제로는 있는 밸류업 계획을 '없음'으로 오판했는데,
        원인은 upstream이 낸 경고를 조용히 버렸기 때문. 이 패턴은 다른 upstream 조합에서도
        반복될 수 있는 일반적 위험이라, 예외/정상 무관하게 upstream warnings를 항상 끌어올린다)."""
        upstream_names.append(name)
        if isinstance(res, BaseException):
            warnings.append(f"{name} 조회 실패: {res}")
            failed_upstreams.append(name)
            return {}
        if res.get("status") == "error":
            failed_upstreams.append(name)
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

    ticker = selected.get("stock_code")
    div_history_list = div_history_data.get("history") or []
    if ticker and div_history_list:
        await _fill_yearend_yield(ticker, div_history_list)

    capital_return_cycles, quality_flags = await _capital_return_impact(
        canonical_name, corp_code, treasury_data,
        fiscal_month=div_summary_data.get("fiscal_year_end_month"), ticker=ticker,
    )

    # CSR 분모·분자 기간 정합: 배당(div_summary_data)이 커버하는 정확한 회계연도를 stlm_dt 기반
    # fiscal_year/fiscal_year_end_month(둘 다 dividend.py가 이미 계산해 top-level에 둔 값, 재사용—
    # 이 tool에서 새로 추론하지 않음)로 구하고, 그 구간으로 treasury_share를 별도 재조회한다.
    fiscal_year = div_summary_data.get("year")
    fiscal_end_month = div_summary_data.get("fiscal_year_end_month")
    fiscal_period = _fiscal_period(fiscal_year, fiscal_end_month) if fiscal_year and fiscal_end_month else None

    csr_treasury_summary: dict[str, Any] | None = None
    if fiscal_period:
        csr_start = fiscal_period["start"].replace("-", "")
        csr_end = fiscal_period["end"].replace("-", "")
        treasury_csr_result = await build_treasury_share_payload(
            company_query, scope="summary", start_date=csr_start, end_date=csr_end,
        )
        treasury_csr_data = _data(treasury_csr_result, "treasury_share(CSR 회계연도 정합)")
        csr_treasury_summary = treasury_csr_data.get("summary") or {}
    else:
        quality_flags.append(
            "CSR 기간 정합 실패: 결산월(stlm_dt)을 못 구해 배당(1개년)·자사주매입(lookback_years 누적) "
            "기간이 안 맞는 채로 합산했다 — CSR이 부풀려질 수 있음."
        )

    overall = _overall_shareholder_return(
        div_summary_data.get("summary") or {}, treasury_data.get("summary") or {},
        csr_treasury_summary=csr_treasury_summary, fiscal_period=fiscal_period,
    )

    # CSR 회계연도 밖 최신 이벤트 — treasury_task의 lookback_years 범위 안에서 fiscal_period 이후로
    # 새로 나온 공시는 크기 무관 전부 노출(260914 재변경). lookback_years가 짧으면(예: 1년) 이
    # fiscal_period 자체가 lookback 범위 밖일 수 있어 그런 경우 빈 리스트가 정상.
    latest_events_beyond_csr_period = _latest_events_beyond_period(treasury_data, fiscal_period)
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
        "latest_events_beyond_csr_period": latest_events_beyond_csr_period,
        "data_quality_flags": quality_flags,
        "usage": build_usage(get_dart_client().api_call_snapshot() - calls_start),
    }

    if not failed_upstreams:
        status = AnalysisStatus.EXACT
    elif len(set(failed_upstreams)) >= len(set(upstream_names)):
        status = AnalysisStatus.ERROR
    else:
        status = AnalysisStatus.PARTIAL
    return ToolEnvelope(
        tool="shareholder_commitment",
        status=status,
        subject=canonical_name,
        data=data,
        warnings=warnings,
    ).to_dict()
