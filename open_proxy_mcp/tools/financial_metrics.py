"""financial_metrics public tool — DART 재무 4 endpoint 통합."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.financial_metrics import build_financial_metrics_payload


def _num(v) -> str:
    """천단위 구분 — 문서 내 다른 숫자와 표기를 맞춘다(EPS 만 15410 으로 나오던 것)."""
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return "-"


def _format_krw_human(amount: int | float | None) -> str:
    """원 단위 raw → 사람 가독 (조/억/원). 단위에 '원'을 반드시 붙인다 —
    「334조」만 쓰면 무엇의 단위인지 문서 안에서 확정되지 않는다(260728 QA 지적)."""
    if amount is None:
        return "-"
    sign = "-" if amount < 0 else ""
    n = abs(int(amount))
    if n >= 1_000_000_000_000:
        # 1조 = 1,000,000,000,000
        cho = n / 1_000_000_000_000
        if cho >= 100:
            return f"{sign}{cho:,.0f}조원"
        return f"{sign}{cho:,.1f}조원"
    if n >= 100_000_000:
        eok = n / 100_000_000
        if eok >= 10:
            return f"{sign}{eok:,.0f}억원"
        return f"{sign}{eok:,.1f}억원"
    if n >= 10_000:
        man = n / 10_000
        return f"{sign}{man:,.0f}만원"
    return f"{sign}{n:,}원"


def _pct(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v:.2f}%"


def _ratio(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v:.2f}"


def _render_correction_note(data: dict[str, Any]) -> list[str]:
    """근거 보고서가 정정본이면 그렇다고 쓴다.

    조용히 쓰면 숫자가 정정 전인지 후인지 몰라 인용을 못 한다(260828 U 지적 A-3).
    무엇이 바뀐 정정인지까지는 파지 않는다 — 정정본이라는 사실과 정정일이면 막힘이 풀린다.
    """
    src = data.get("source_report") or {}
    if not src.get("is_correction"):
        return []
    raw = str(src.get("correction_dt") or src.get("rcept_dt") or "")
    when = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw) == 8 and raw.isdigit() else raw
    n = src.get("same_day_corrections") or 0
    tail = f" · 같은 날 함께 정정된 정기보고서 {n}건" if n else ""
    return [f"- ⚠ 근거 보고서는 **정정본**입니다 — {src.get('report_nm', '')}"
            f"{f' ({when} 정정)' if when else ''}{tail}. 아래 수치는 정정 후 기준이라 정정 전 값과 다를 수 있습니다."]


#: alert 코드는 그대로 두면 용어만 던지는 꼴이다 — 한 줄 뜻을 같이 낸다(260828 U 지적 D-9).
#: 줄 수는 그대로(코드 한 줄 = 뜻 한 줄).
_ALERT_KR = {
    "loss_conversion": "적자 전환 — 전기 흑자에서 당기 영업적자",
    "turnaround": "흑자 전환 — 전기 적자에서 당기 영업흑자",
    "continued_loss": "적자 지속 — 전기·당기 모두 영업적자",
    "operating_loss": "당기 영업적자",
    "revenue_decline": "매출 감소",
    "debt_surge": "부채비율 급증",
    "interest_coverage_low": "이자보상배율 1배 미만 — 영업이익으로 이자를 못 낸다",
    "capital_impairment_full": "완전 자본잠식 — 자본총계 ≤ 0 (코스닥 상장폐지 사유)",
    "capital_impairment_50plus": "자본잠식 50% 이상 (코스닥 관리종목 사유)",
    "capital_impairment_partial": "부분 자본잠식 — 조기 경고",
    "cfo_quality_red": "영업CF가 영업이익의 70% 미만 — 이익이 현금으로 덜 들어왔다",
    "negative_fcf": "자유현금흐름 음수 — 영업현금이 설비투자를 못 덮는다",
    "low_dividend_capacity_use": "FCF 대비 배당이 적다 — 배당 여력을 덜 쓰는 중",
    "nwc_surge": "운전자본 급증 — 재고·매출채권에 현금이 묶이는 중",
    "nwc_efficiency_low": "매출 대비 운전자본 부담이 크다",
    "roe_driven_by_leverage": "ROE가 마진이 아니라 레버리지(빚)로 올라간 구조",
    "roe_decline_margin_driven": "ROE 하락의 주원인이 마진 축소",
    "roe_decline_turnover_driven": "ROE 하락의 주원인이 자산회전 둔화",
    "accruals_red": "영업이익과 영업CF의 괴리가 30%+ — 이익이 현금으로 안 들어왔다. 운전자본인지 회계 신호인지는 요약의 「갈림길」 참조",
    "accruals_ratio_unreliable": "영업이익이 적자·0 근처라 괴리 **비율**을 믿을 수 없다 — 비율 말고 금액차로 볼 것",
    "receivables_surge": "매출채권/매출 비율이 전년보다 30%+ 상승 — 밀어내기 매출 점검",
    "inventory_surge": "재고/매출 비율이 전년보다 30%+ 상승 — 재고 누적 점검",
    "non_clean_audit_opinion": "감사의견이 적정이 아니다",
    "audit_opinion_change": "감사의견이 전기와 달라졌다",
    "dividend_halt": "배당 중단 — 전기 배당이 있었으나 당기 없음",
    "operating_loss_quarter": "당해 분기 영업적자",
    "net_loss_quarter": "당해 분기 순손실",
    "net_income_below_operating": "순이익이 영업이익보다 작다 — 영업외손실·금융비용 점검",
    "revenue_decline_qoq": "전분기 대비 매출 감소",
}


def _alert_line(code: str) -> str:
    gloss = _ALERT_KR.get(code)
    return f"- ⚠ `{code}` — {gloss}" if gloss else f"- ⚠ `{code}`"


def _render_error(payload: dict[str, Any]) -> str:
    lines = [f"# financial_metrics: {payload.get('subject', '')}", "", "재무 데이터를 확정하지 못했다."]
    for w in payload.get("warnings", []):
        lines.append(f"- {w}")
    return "\n".join(lines)


def _render_ambiguous(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    lines = [
        f"# financial_metrics: {data.get('query', payload.get('subject', ''))}",
        "",
        "회사 식별이 애매해 재무 데이터를 자동 선택하지 않았다.",
        "",
        "| 회사명 | ticker | corp_code |",
        "|------|--------|-----------|",
    ]
    for c in data.get("candidates", []):
        lines.append(f"| {c['corp_name']} | `{c['ticker']}` | `{c['corp_code']}` |")
    return "\n".join(lines)


_TURNOVER_BASIS_KR = {"ttm": "TTM(최근 4분기)", "annual": "연간", "period_matched": "분기 기간보정"}


def _accruals_fork(s: dict[str, Any]) -> str:
    """괴리가 크면 「무엇을 더 봐야 갈리는지」 한 줄. 단정하지 않는다 — 갈림길만 준다.

    NWC(매출채권+재고−매입채무) YoY 변동이 괴리 금액을 얼마나 덮는지로 방향을 가른다.
    원자재 회사의 재고 증가(운전자본)와 회계 신호를 같은 red 로 묶지 않기 위한 것(260828 U 지적 D-9).
    """
    gap_krw = s.get("accruals_gap_krw")
    nwc_chg = s.get("nwc_change_yoy_krw")
    tail = (f"재고/매출 {_pct(s.get('inv_to_revenue_pct'))}·DIO {_ratio(s.get('days_inventory_outstanding'))}일"
            f" / 매출채권/매출 {_pct(s.get('ar_to_revenue_pct'))}·DSO {_ratio(s.get('days_sales_outstanding'))}일")
    if not gap_krw or nwc_chg is None:
        return (f"재고·매출채권이 전년보다 같이 늘었으면 **운전자본**, 그대로인데 괴리만 크면 **회계 신호**. "
                f"지금 값 — {tail}")
    covered = nwc_chg / gap_krw * 100  # 부호가 같아야 「덮는다」
    if covered >= 60:
        head = (f"운전자본이 괴리의 {covered:.0f}%를 덮습니다 (NWC YoY {_format_krw_human(nwc_chg)}) — "
                f"재고·외상에 현금이 묶인 쪽. 회계 신호로 읽기 전에 여기부터 봅니다.")
    elif covered >= 20:
        head = (f"운전자본이 괴리의 {covered:.0f}%만 덮습니다 (NWC YoY {_format_krw_human(nwc_chg)}) — "
                f"나머지는 대손·평가손익 등 비현금 항목을 봐야 갈립니다.")
    else:
        head = (f"운전자본으로 설명되지 않습니다 (NWC YoY {_format_krw_human(nwc_chg)}, 괴리의 {covered:.0f}%) — "
                f"비현금 손익·이자·법인세 유출 또는 회계 인식을 봐야 갈립니다.")
    return f"{head} 지금 값 — {tail}"


def _render_accruals(s: dict[str, Any]) -> list[str]:
    """「영업이익 vs 영업CF 괴리」 — 뜻 + 금액차 + 비율 왜곡 경고 + 갈림길."""
    gap = s.get("accruals_gap_pct")
    amt = s.get("accruals_gap_krw")
    rel = s.get("accruals_gap_reliability")
    op = s.get("operating_profit_krw")
    if gap is None and amt is None:
        return ["- 영업이익 vs 영업CF 괴리: - (영업이익 또는 영업CF 미확보)"]
    if amt is None:
        amt_txt = ""
    elif amt > 0:
        amt_txt = f"  /  금액차(영업이익−영업CF) {_format_krw_human(amt)} — 현금이 이익에 그만큼 못 미친다"
    elif amt < 0:
        amt_txt = f"  /  금액차(영업이익−영업CF) {_format_krw_human(amt)} — 현금이 이익을 그만큼 웃돈다"
    else:
        amt_txt = "  /  금액차 0원"
    out = [f"- 영업이익 vs 영업CF 괴리: {_pct(gap)}{amt_txt}"]
    out.append("  - 뜻: 장부 영업이익만큼 실제 영업현금이 들어왔는지. 벌어질수록 이익이 아직 현금이 아니다.")
    if rel == "negative_op":
        out.append(f"  - ⚠ **비율은 읽지 마세요** — 분모인 영업이익이 적자({_format_krw_human(op)})라 부호가 뒤집히고 크기가 왜곡됩니다. 금액차로 판단하세요.")
    elif rel == "thin_op":
        out.append(f"  - ⚠ 비율 왜곡 — 영업이익({_format_krw_human(op)})이 매출 대비 매우 얇아 비율이 크게 튑니다. 금액차를 같이 보세요.")
    elif gap is not None and gap >= 30:
        out.append("  - 절대값 30%+ — 이익이 현금으로 안 들어온 쪽입니다.")
    elif gap is not None and gap <= -30:
        out.append("  - 절대값 30%+ 이지만 **역방향** — 현금이 이익보다 많습니다(감가상각·선수금 등). 분식 신호와는 반대쪽입니다.")
    else:
        out.append("  - 절대값 30% 이내 — 정상 범위입니다.")
    if (amt or 0) > 0 and (rel in ("negative_op", "thin_op") or (gap is not None and abs(gap) >= 30)):
        out.append(f"  - 갈림길: {_accruals_fork(s)}")
    return out


def _render_summary(data: dict[str, Any]) -> list[str]:
    s = data.get("summary", {}) or {}
    lines: list[str] = []
    # 기준(당기/누적/TTM) 항상 명시
    if s.get("basis_note"):
        lines.append(f"> **기준**: {s['basis_note']}")
        lines.append("")
    lines.append("## 핵심 지표")
    lines.append(f"- 매출액: {_format_krw_human(s.get('revenue_krw'))}  /  매출총이익: {_format_krw_human(s.get('gross_profit_krw'))}  /  영업이익: {_format_krw_human(s.get('operating_profit_krw'))}")
    # EBITDA는 D&A 추출 가능 회사(~24%)만 산출 — None이면 줄에서 생략 (결측 광고 방지)
    if s.get("ebitda_krw") is not None:
        lines.append(f"- 영업이익률: {_pct(s.get('operating_margin_pct'))}  /  EBITDA: {_format_krw_human(s.get('ebitda_krw'))}  ({_pct(s.get('ebitda_margin_pct'))})")
    else:
        lines.append(f"- 영업이익률: {_pct(s.get('operating_margin_pct'))}")
    lines.append(f"- 당기순이익(지배): {_format_krw_human(s.get('net_income_krw'))}  /  EPS: {_num(s.get('eps_krw'))}원  /  희석 EPS: {_num(s.get('diluted_eps_krw'))}원")
    lines.append(f"- ROE: {_pct(s.get('roe_pct'))}  /  ROA: {_pct(s.get('roa_pct'))}  /  ROIC: {_pct(s.get('roic_pct'))}")
    lines.append("")
    lines.append("## 듀퐁 3단 분해 (ROE)")
    lines.append(f"- 순이익률: {_pct(s.get('net_profit_margin_pct'))}")
    lines.append(f"- 총자산회전율: {_ratio(s.get('asset_turnover_ratio'))}회")
    lines.append(f"- 재무레버리지(평균자산/평균자본): {_ratio(s.get('equity_multiplier'))}배")
    lines.append(f"- DuPont ROE 검증: {_pct(s.get('roe_dupont_pct'))} (단순 ROE와 일치 여부 확인)")
    lines.append("")
    lines.append("## 안정성 / 부채")
    lines.append(f"- 자산총계: {_format_krw_human(s.get('total_assets_krw'))}  /  부채총계: {_format_krw_human(s.get('total_liabilities_krw'))}  /  자본총계(NAV): {_format_krw_human(s.get('total_equity_krw'))}")
    lines.append(f"- 부채비율(부채/자본): {_pct(s.get('debt_ratio_pct'))}  /  유동비율: {_pct(s.get('current_ratio_pct'))}")
    _dep = _pct(s.get("debt_dependency_pct"))
    if s.get("debt_dependency_status") == "n/a_financial":
        _dep = "n/a (금융업 — 예수부채 등 영업조달)"
    lines.append(f"- 이자보상배율(영업이익/이자비용): {_ratio(s.get('interest_coverage_ratio'))}배  /  차입금의존도: {_dep}")
    _conf = s.get("total_debt_confidence")
    _conf_tag = f" [{_conf}]" if _conf and _conf in ("REVIEW", "CONFLICT", "MED") else ""
    lines.append(f"- 총차입금: {_format_krw_human(s.get('total_debt_krw'))}{_conf_tag}  /  순현금(현금-차입): {_format_krw_human(s.get('net_cash_krw'))}")
    _st, _lt = s.get("short_term_debt_krw"), s.get("long_term_debt_krw")
    if _st is not None or _lt is not None:
        lines.append(f"  - 단기: {_format_krw_human(_st)}  /  장기: {_format_krw_human(_lt)}"
                     + (f"  /  전환사채류: {_format_krw_human(s.get('convertible_debt_krw'))}" if s.get("convertible_debt_krw") else ""))
    if s.get("lease_liabilities_krw"):
        lines.append(f"  - 리스부채(별도, IFRS16): {_format_krw_human(s.get('lease_liabilities_krw'))}  /  리스포함 총차입: {_format_krw_human(s.get('total_debt_incl_lease_krw'))}")
    if s.get("hybrid_capital_krw"):
        lines.append(f"  - 신종자본증권(자본 분류, 총차입 제외): {_format_krw_human(s.get('hybrid_capital_krw'))}")
    _bd = s.get("borrowing_detail") or {}
    if _bd.get("conflicts") or _bd.get("reviews"):
        _n = len(_bd.get("conflicts") or []) + len(_bd.get("reviews") or [])
        lines.append(f"  - ⚠ 차입 분류 사람검토 {_n}건(합산 제외) — 총차입금 신뢰도 {_conf}. warnings 참조.")
    cap_status = s.get("capital_impairment_status")
    cap_ratio = s.get("capital_impairment_ratio_pct")
    if cap_status:
        status_label = {
            "normal": "정상",
            "partial": "부분 자본잠식 (조기 경고)",
            "partial_50plus": "**자본잠식 50%+ (KOSDAQ 관리종목 사유)**",
            "full": "**완전 자본잠식 (KOSDAQ 상장폐지 사유)**",
        }.get(cap_status, cap_status)
        if cap_status == "normal":
            # 정상 기업의 잠식률은 거대 음수(예: 삼성전자 -48,514%)라 혼란만 줌 — 상태만 표기
            lines.append(f"- 자본잠식 상태: {status_label} (자본총계 > 자본금)  /  자본금: {_format_krw_human(s.get('capital_stock_krw'))}")
        else:
            lines.append(f"- 자본잠식 상태: {status_label}  /  잠식률: {_pct(cap_ratio)}  /  자본금: {_format_krw_human(s.get('capital_stock_krw'))}")
    lines.append("")
    lines.append("## 현금흐름 (코리아 디스카운트 핵심)")
    lines.append(f"- CFO(영업CF): {_format_krw_human(s.get('cfo_krw'))}  /  CapEx(유형자산취득): {_format_krw_human(s.get('capex_krw'))}")
    lines.append(f"- FCF(자유현금흐름): {_format_krw_human(s.get('fcf_krw'))}  ({_pct(s.get('fcf_margin_pct'))})")
    lines.append(f"- CFO/영업이익 (cash quality, <0.7=분식 신호): {_ratio(s.get('cfo_to_op_ratio'))}")
    lines.append(f"- CFO/순이익 (이익의 현금화): {_ratio(s.get('cfo_to_net_income_ratio'))}")
    if s.get("capex_to_da_ratio") is not None:
        lines.append(f"- CapEx/감가상각비 (>1=확장, <1=유지): {_ratio(s.get('capex_to_da_ratio'))}")
    lines.append(f"- 배당/FCF (배당 capacity 활용도): {_pct(s.get('dividend_to_fcf_pct'))}")
    lines.append("")
    lines.append("## 운전자본 (Working Capital)")
    lines.append(f"- 운전자본 (유동자산 - 유동부채): {_format_krw_human(s.get('working_capital_krw'))}")
    lines.append(f"- 순운전자본 NWC (매출채권+재고-매입채무): {_format_krw_human(s.get('nwc_krw'))}")
    lines.append(f"- NWC YoY 변동: {_format_krw_human(s.get('nwc_change_yoy_krw'))}")
    lines.append(f"- NWC/매출 (효율, 낮을수록 좋음): {_pct(s.get('nwc_to_revenue_pct'))}")
    _tb = _TURNOVER_BASIS_KR.get(s.get("turnover_basis"), s.get("turnover_basis") or "-")
    lines.append(f"- 회전일수 (분모 기준: {_tb}): DSO {_ratio(s.get('days_sales_outstanding'))}일 / DIO {_ratio(s.get('days_inventory_outstanding'))}일 / DPO {_ratio(s.get('days_payable_outstanding'))}일")
    lines.append(f"- 현금전환주기(DSO+DIO-DPO): {_ratio(s.get('cash_conversion_cycle_days'))}일")
    lines.append("")
    lines.append("## 회계 위험 지표 (분식 신호)")
    lines.extend(_render_accruals(s))
    lines.append(f"- 매출채권/매출 비율: {_pct(s.get('ar_to_revenue_pct'))} (push sales 신호)")
    lines.append(f"- 재고자산/매출 비율: {_pct(s.get('inv_to_revenue_pct'))} (재고 누적 신호)")
    lines.append("")
    lines.append("## 배당 / 유보")
    lines.append(f"- 배당지급액(CF, 현금 유출): {_format_krw_human(s.get('dividend_paid_krw'))}")
    lines.append(f"- 배당성향 (DART 현금배당성향, 귀속·연결): {_pct(s.get('payout_ratio_pct'))}")
    lines.append(f"- 이익잉여금(사내유보): {_format_krw_human(s.get('retained_earnings_krw'))}")
    lines.append("")
    lines.append("## NAV / 주식")
    lines.append(f"- NAV (순자산가치): {_format_krw_human(s.get('nav_krw'))}")
    # 반기/3분기 보고서면 누적(위)과 별도로 당기 분기(standalone) 손익·현금흐름 제공
    st = s.get("standalone")
    if st:
        lines.append("")
        lines.append("## 당기 분기(standalone, 3개월) — 위 누적과 별도")
        lines.append(f"- 매출: {_format_krw_human(st.get('revenue_krw'))}  /  영업이익: {_format_krw_human(st.get('operating_profit_krw'))}  /  순이익: {_format_krw_human(st.get('net_income_krw'))}  /  영업이익률: {_pct(st.get('operating_margin_pct'))}")
        lines.append(f"- CFO: {_format_krw_human(st.get('cfo_krw'))}  /  FCF: {_format_krw_human(st.get('fcf_krw'))}  /  CFO/영업이익: {_ratio(st.get('cfo_to_op_ratio'))}")
    return lines


def _render_yearly(data: dict[str, Any]) -> list[str]:
    rows = data.get("yearly", []) or []
    if not rows:
        return ["## 연간 추이", "_데이터 없음_"]
    lines = ["## 연간 추이 (3년)"]
    lines.append("")
    lines.append("| 연도 | 매출 | 영업이익 | 순이익 | OPM | ROE | 부채비율 | CFO | FCF |")
    lines.append("|------|------|----------|--------|-----|-----|----------|-----|-----|")
    for r in rows:
        lines.append(
            f"| {r.get('year')} | "
            f"{_format_krw_human(r.get('revenue_krw'))} | "
            f"{_format_krw_human(r.get('operating_profit_krw'))} | "
            f"{_format_krw_human(r.get('net_income_krw'))} | "
            f"{_pct(r.get('operating_margin_pct'))} | "
            f"{_pct(r.get('roe_pct'))} | "
            f"{_pct(r.get('debt_ratio_pct'))} | "
            f"{_format_krw_human(r.get('cfo_krw'))} | "
            f"{_format_krw_human(r.get('fcf_krw'))} |"
        )
    return lines


def _chg(v: Any) -> str:
    if v is None:
        return "-"
    return f"{v:+.1f}%"


def _render_quarterly(data: dict[str, Any]) -> list[str]:
    rows = data.get("quarterly", []) or []
    if not rows:
        return ["## 분기 추이", "_데이터 없음_"]
    lines = ["## 분기 추이 (최근 12분기, 전 행 standalone 3개월 기준)"]
    lines.append("")
    lines.append("| 사업연도-분기 | 매출 | QoQ | YoY | 영업이익 | QoQ | YoY | 순이익 | 영업이익률 |")
    lines.append("|-----------|------|-----|-----|----------|-----|-----|--------|------------|")
    has_cumulative_q4 = False
    for r in rows:
        qoq = r.get("qoq_pct") or {}
        yoy = r.get("yoy_pct") or {}
        mark = ""
        if r.get("basis") == "annual_cumulative":
            mark = " ⚠연간"
            has_cumulative_q4 = True
        lines.append(
            f"| {r.get('fiscal_year', r.get('year'))}-{r.get('fiscal_quarter', r.get('quarter'))}"
            f" [{r.get('period_end') or '기간종료 미상'} / 결산월 {r.get('fiscal_year_end_month') or '-'}월]{mark} | "
            f"{_format_krw_human(r.get('revenue_krw'))} | "
            f"{_chg(qoq.get('revenue'))} | {_chg(yoy.get('revenue'))} | "
            f"{_format_krw_human(r.get('operating_profit_krw'))} | "
            f"{_chg(qoq.get('operating_profit'))} | {_chg(yoy.get('operating_profit'))} | "
            f"{_format_krw_human(r.get('net_income_krw'))} | "
            f"{_pct(r.get('operating_margin_pct'))} |"
        )
    lines.append("")
    lines.append("> Q4는 사업보고서 연간치에서 3개 분기 누적을 차분한 standalone 값. QoQ/YoY는 전기가 적자·결측이면 `-`.")
    if has_cumulative_q4:
        lines.append("> ⚠연간 표시 행은 분기 보고서 결측으로 차분 불가 — 연간 누적치이므로 분기 비교에 쓰지 말 것.")
    status = data.get("quarterly_status") or {}
    missing = status.get("missing") or []
    if missing:
        lines.append("")
        for item in missing:
            lines.append(
                f"> 상태: {status.get('fiscal_year')}-{item.get('fiscal_quarter')} "
                f"({item.get('period_end')}) — **{item.get('status', '확인 필요')}**"
            )
    return lines


def _render_yoy(data: dict[str, Any]) -> list[str]:
    yoy = data.get("yoy", {}) or {}
    curr = yoy.get("current", {}) or {}
    prev = yoy.get("prior", {}) or {}
    alerts = yoy.get("alerts", []) or []
    audit = yoy.get("audit_opinion", {}) or {}

    lines = ["## 전년 대비 (YoY)"]
    lines.append("")
    lines.append("| 지표 | 당기 | 전기 |")
    lines.append("|------|------|------|")
    metric_pairs = [
        ("매출액", "revenue_krw", _format_krw_human),
        ("영업이익", "operating_profit_krw", _format_krw_human),
        ("순이익(지배)", "net_income_krw", _format_krw_human),
        ("영업이익률", "operating_margin_pct", _pct),
        ("ROE", "roe_pct", _pct),
        ("부채비율", "debt_ratio_pct", _pct),
        ("이자보상배율", "interest_coverage_ratio", _ratio),
        ("CFO", "cfo_krw", _format_krw_human),
        ("FCF", "fcf_krw", _format_krw_human),
        ("CFO/영업이익", "cfo_to_op_ratio", _ratio),
        ("NWC", "nwc_krw", _format_krw_human),
        ("NWC/매출", "nwc_to_revenue_pct", _pct),
        ("매출채권/매출", "ar_to_revenue_pct", _pct),
        ("재고/매출", "inv_to_revenue_pct", _pct),
        ("배당지급", "dividend_paid_krw", _format_krw_human),
    ]
    for label, key, fmt in metric_pairs:
        lines.append(f"| {label} | {fmt(curr.get(key))} | {fmt(prev.get(key))} |")

    lines.extend(["", "## Alerts (자동 감지)"])
    if alerts:
        for a in alerts:
            lines.append(_alert_line(a))
    else:
        lines.append("- 특이사항 없음")

    lines.extend(["", "## 감사의견 cross-check"])
    a_curr = audit.get("current") or {}
    a_prev = audit.get("prior") or {}
    if a_curr:
        lines.append(f"- 당기: {a_curr.get('adt_opinion', '-')} ({a_curr.get('adtor', '-')})")
    if a_prev:
        lines.append(f"- 전기: {a_prev.get('adt_opinion', '-')} ({a_prev.get('adtor', '-')})")
    return lines


def _render_qoq(data: dict[str, Any]) -> list[str]:
    qoq = data.get("qoq", {}) or {}
    curr = qoq.get("current") or {}
    prev = qoq.get("prior") or {}
    alerts = qoq.get("alerts", []) or []
    lines = ["## 전분기 대비 (QoQ)"]
    if not curr:
        return lines + ["_데이터 없음_"]
    lines.append(f"- 당기: {curr.get('year')}-{curr.get('quarter')}, 전기: {prev.get('year')}-{prev.get('quarter')}" if prev else f"- 당기: {curr.get('year')}-{curr.get('quarter')} (전분기 데이터 없음)")
    lines.append("- 양쪽 모두 standalone 3개월 기준 (Q4는 연간−3분기 누적 차분)")
    lines.append("")
    lines.append("| 지표 | 당기 | 전분기 | 증감(QoQ) |")
    lines.append("|------|------|--------|-----------|")
    qoq_pct = curr.get("qoq_pct") or {}
    chg_keys = {"revenue_krw": "revenue", "operating_profit_krw": "operating_profit", "net_income_krw": "net_income"}
    pp_keys = {"operating_margin_pct": "operating_margin_pp", "net_profit_margin_pct": "net_profit_margin_pp"}
    pairs = [
        ("매출액", "revenue_krw", _format_krw_human),
        ("영업이익", "operating_profit_krw", _format_krw_human),
        ("순이익", "net_income_krw", _format_krw_human),
        ("영업이익률", "operating_margin_pct", _pct),
    ]
    for label, key, fmt in pairs:
        if key in chg_keys:
            chg = _chg(qoq_pct.get(chg_keys[key]))  # 손익은 증감률(%)
        elif key in pp_keys:
            pp = qoq_pct.get(pp_keys[key])  # 마진은 %포인트
            chg = f"{pp:+.2f}%p" if pp is not None else "-"
        else:
            chg = "-"
        lines.append(f"| {label} | {fmt(curr.get(key))} | {fmt(prev.get(key)) if prev else '-'} | {chg} |")
    lines.extend(["", "## Alerts"])
    if alerts:
        for a in alerts:
            lines.append(_alert_line(a))
    else:
        lines.append("- 특이사항 없음")
    return lines


def _render_audit(data: dict[str, Any]) -> list[str]:
    audit = data.get("audit_opinion", {}) or {}
    summary = audit.get("summary", {}) or {}
    opinions = audit.get("opinions", []) or []
    lines = ["## 감사의견 추이"]
    if not opinions:
        return lines + ["_감사의견 공시 없음_"]
    lines.append(f"- 최신 의견: **{summary.get('latest_opinion') or '-'}**")
    lines.append(f"- 최신 감사인: {summary.get('latest_auditor') or '-'}")
    lines.append(f"- 감사의견 모두 적정: {'예' if summary.get('all_clean') else '아니오'}")
    lines.append(f"- 추적 사업연도 수: {summary.get('history_years')}")
    lines.append("")
    lines.append("| 결산일 | 감사인 | 의견 | 강조사항 | 핵심감사사항(KAM) |")
    lines.append("|--------|--------|------|----------|------------------|")
    for o in opinions:
        emphs = (o.get("emphs_matter") or "-")[:30]
        kam = (o.get("core_adt_matter") or "-").replace("\n", " / ")[:60]
        lines.append(
            f"| {o.get('stlm_dt', '-')} | {o.get('adtor', '-')} | "
            f"**{o.get('adt_opinion', '-')}** | {emphs} | {kam} |"
        )
    return lines


def _render(payload: dict[str, Any]) -> str:
    data = payload.get("data", {}) or {}
    scope = data.get("scope", "summary")
    _SCOPE_KO = {
        "summary": "요약", "yearly": "연간 추이", "quarterly": "분기 추이",
        "yoy": "전년 대비", "qoq": "전분기 대비", "audit_opinion": "감사의견", "detail": "상세",
    }
    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} 재무지표 — {_SCOPE_KO.get(scope, scope)}"]
    lines.append("")
    _tk = (data.get("identifiers") or {}).get("ticker") or ""
    if _tk:
        lines.append(f"- 종목코드 {_tk}")
    # 기준은 요청값(consolidated)이 아니라 **실제 사용된 fs_div** 다 — CFS 미작성 시 OFS 로 폴백된다.
    # None 을 「별도」로 단정하면 안 된다(260728 디버깅 에이전트 실측).
    _FSDIV_KO = {"CFS": "연결", "OFS": "별도"}
    _fsdiv = (data.get("summary") or {}).get("fs_div") or data.get("fs_div")
    _basis = _FSDIV_KO.get(_fsdiv, "기준 미상")
    # 「사업연도 2025」는 결산월에 따라 전혀 다른 12개월이다 — 6월 결산이면 2024-07-01~2025-06-30.
    # 라벨만 두면 1년을 통째로 오독한다(260828 U 지적 B-5). provisional_earnings 실적기간 표기와 맞춘다.
    _fp = (data.get("fiscal_period") or {}).get("label")
    lines.append(f"- 사업연도 {data.get('year')}" + (f" ({_fp})" if _fp else "") + f" · {_basis} 기준")
    lines.extend(_render_correction_note(data))
    # 일부 파싱 실패는 status 만 PARTIAL 로 바뀌고 warning 이 안 붙는다 — 화면에서 사라지면 안 된다.
    if (data.get("filing_status") or "") not in ("", "all_parsed"):
        _n = data.get("parsing_failures")
        lines.append(f"- ⚠ 일부 공시를 읽지 못했습니다"
                     + (f" ({_n}건)" if _n else "") + " — 수치가 불완전할 수 있습니다.")
    lines.append("")
    if payload.get("warnings"):
        lines.append("## 유의사항")
        for w in payload["warnings"]:
            lines.append(f"- {w}")
        lines.append("")

    if scope == "summary":
        lines.extend(_render_summary(data))
    elif scope == "yearly":
        lines.extend(_render_yearly(data))
    elif scope == "quarterly":
        lines.extend(_render_quarterly(data))
    elif scope == "yoy":
        lines.extend(_render_yoy(data))
    elif scope == "qoq":
        lines.extend(_render_qoq(data))
    elif scope == "audit_opinion":
        lines.extend(_render_audit(data))

    refs = payload.get("evidence_refs", []) or []
    if refs:
        lines.extend(["", "## Evidence"])
        for r in refs[:5]:
            url = r.get("viewer_url") or "-"
            lines.append(f"- {r.get('section', '-')}: [{r.get('rcept_no', '-')}]({url}) — {r.get('note', '')}")

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def financial_metrics(
        company: str,
        scope: str = "summary",
        year: int = 0,
        years: int = 3,
        consolidated: bool = True,
        format: str = "md",
    ) -> str:
        """desc: DART 재무 4 endpoint 통합 — 수익성/안정성/현금흐름/회계 risk. 한국 표준(연결, 지배주주 귀속). 듀퐁·FCF·NWC·accruals_gap·감사의견 자동 산출.
        when: 재무 펀더멘탈 + 회계 risk 진단 / 적자전환·턴어라운드·이자보상배율 alert / 사외이사 후보 재직 시점 회계 사건 cross-check.
        rule: source = fnlttSinglAcnt(BS+IS 30행, 요청 fs_div로 행 필터) + fnlttSinglIndx(보조 ROE) + fnlttSinglAcntAll(CF+213행) + accnutAdtorNmNdAdtOpinion(감사의견 3년). 금액 raw KRW int(_krw), %는 float(_pct), 비율 decimal(_ratio). 연결 default, 적자/0 분모 graceful. 금융사(은행·지주)는 매출액 계정이 없어 None — 영업이익·순이익 기준 해석. 분기 합≠연간이면 기중 분할·재작성 warning 자동 부착. 이자보상배율 분모 = IS 이자비용, 없으면 CF '이자의 지급' (금융비용 총액 사용 안 함). EBITDA는 CF에서 D&A가 추출된 회사만 산출 (조정 합계 공시 회사는 None). 사업연도 라벨에는 그 연도가 덮는 12개월과 결산월을 붙인다(6월 결산 오독 방지). 근거 정기보고서가 정정본이면 정정일과 함께 명시. accruals_gap은 비율(`_pct`)과 금액차(`accruals_gap_krw`)를 같이 내고, 영업이익이 적자·초박막이면 `accruals_gap_reliability`로 비율 왜곡을 표시(alert도 `accruals_red` 대신 `accruals_ratio_unreliable`).
        period: DART 기간 의미가 항목별로 다름 — 손익 thstrm=당기3개월/누적은 thstrm_add, 현금흐름=누적, 재무상태=잔액. summary가 분기보고서면 ① 손익은 누적(YTD) 기준 primary + 당기 분기(standalone)를 `standalone`에 별도 동봉(반기/3분기), ② 회전일수(DSO/DIO/CCC)는 TTM(최근 4분기) 분모로 산출(단일분기 연환산 왜곡 제거), ③ ROE/ROA/자산회전율은 연환산 안 함(분기값). 기준은 항상 `period_basis`/`turnover_basis`/`basis_note`로 명시. year 미지정 시 quarterly·qoq는 당해 연도(최신 분기 포함), summary·yearly·yoy는 직전 사업연도.
        scope: `summary` 핵심 지표 1년(분기보고서면 누적+standalone) / `yearly` N년 추이 / `quarterly` 12분기 standalone 손익 + QoQ·YoY(마진은 %p) 기본 동봉 (Q4는 연간−3분기 누적 차분 — 연간치 혼입 없음) / `yoy` 전년+alert / `qoq` 전분기 (standalone 기준) / `audit_opinion` 3년 추이
        ref: dividend, corp_gov_report, shareholder_meeting_notice, evidence
        """
        payload = await build_financial_metrics_payload(
            company,
            scope=scope,
            year=year or None,
            years=years,
            consolidated=consolidated,
        )
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") == "ambiguous":
            return _render_ambiguous(payload)
        if payload.get("status") == "error":
            return _render_error(payload)
        return _render(payload)
