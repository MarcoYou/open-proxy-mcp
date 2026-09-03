"""shareholder_commitment — 밸류업/거버넌스 약속 vs 실제 이행 추적 (연중 스튜어드십 메모)."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.shareholder_commitment import build_shareholder_commitment_payload
from open_proxy_mcp.services.contracts import as_pretty_json


def _f(v, fmt="{:,}"):
    return fmt.format(v) if v is not None else "N/M"


def _render_status(payload: dict[str, Any]) -> str:
    status = payload.get("status", "error")
    lines = [f"# shareholder_commitment: {payload.get('subject', '')} — status=`{status}`", ""]
    for w in payload.get("warnings", []):
        lines.append(f"- {w}")
    cands = (payload.get("data") or {}).get("candidates") or []
    if cands:
        lines += ["", "| 회사명 | ticker | corp_code |", "|---|---|---|"]
        for c in cands:
            lines.append(f"| {c.get('corp_name')} | `{c.get('stock_code') or '-'}` | `{c.get('corp_code')}` |")
    return "\n".join(lines)


def _render(payload: dict[str, Any]) -> str:
    d = payload["data"]
    lines = [f"# {d['canonical_name']} 주주환원 약속 이행 점검 (최근 {d['lookback_years']}년)", ""]

    commitments = d.get("commitments") or {}
    tcr = commitments.get("treasury_cross_ref")
    if commitments.get("latest_plan"):
        lines.append("## 밸류업 계획 공표 여부")
        lines.append("- 밸류업 계획 공표 있음 (`value_up` tool로 상세 확인)")
        if tcr:
            lines.append(
                f"- 최근 24개월 자사주: 취득결정 {tcr.get('acquisition_count_24m', 0)}건"
                f"(소각목적 {tcr.get('acquisition_for_cancelation_count_24m', 0)}건) · "
                f"소각결정 {tcr.get('cancelation_decision_count_24m', 0)}건"
            )
        lines.append("")
    else:
        lines.append("## 밸류업 계획 공표 여부")
        lines.append("- 조회 구간 내 밸류업 계획 공표 없음 (`value_up` tool로 재확인 가능)")
        lines.append("")

    cycles = (d.get("capital_return_execution") or {}).get("buyback_cycles") or []
    lines.append("## 자사주 소각 — 장부가(BPS) 손익")
    if cycles:
        lines.append("")
        lines.append("| 매입기간 | 매입주식수 | 가중평균 매입가 | 매입시점 BPS | 프리미엄/디스카운트 | 장부가 손익 |")
        lines.append("|---|---|---|---|---|---|")
        for c in cycles:
            prem = c.get("premium_discount_pct")
            gain = c.get("book_value_gain_loss_krw")
            prem_str = f"{prem:+.1f}%" if prem is not None else "N/M"
            gain_str = f"{_f(gain)}원" if gain is not None else "N/M"
            lines.append(
                f"| {c.get('period', '-')} | {_f(c.get('shares_acquired'))}주 | "
                f"{_f(c.get('avg_acquisition_price_krw'))}원 | {_f(c.get('bps_at_acquisition_krw'))}원 | "
                f"{prem_str} | {gain_str} |"
            )
        lines.append("")
        lines.append("> 장부가 손익 = (매입시점 BPS − 매입가) × 매입주식수. 양수 = 장부가 기준 저가매입,")
        lines.append("> 음수 = 장부가 기준 고가매입(단, 내재가치가 장부가보다 높다면 여전히 좋은 결정일 수")
        lines.append("> 있음 — 이 tool은 장부가 기준 사실만 계산, 내재가치 판단은 하지 않음).")
    else:
        lines.append("- 조회 구간 내 '소각 목적' 자사주 매입 사이클 없음(또는 매칭 실패)")
    lines.append("")

    div_hist = (d.get("capital_return_execution") or {}).get("dividend_history") or []
    if div_hist:
        lines.append("## 배당 추이")
        lines.append("")
        lines.append("| 연도 | 주당배당(DPS) | 배당성향 | 배당수익률(결의시점) | 배당수익률(연말종가) | 패턴 |")
        lines.append("|---|---|---|---|---|---|")
        for h in div_hist:
            payout = h.get("payout_ratio")
            yld = h.get("yield_pct")
            yld_ye = h.get("yield_pct_yearend")
            lines.append(
                f"| {h.get('year')} | {_f(h.get('annual_dps'))}원 | "
                f"{f'{payout}%' if payout is not None else 'N/M'} | {f'{yld}%' if yld is not None else 'N/M'} | "
                f"{f'{yld_ye}%' if yld_ye is not None else 'N/M'} | {h.get('pattern', '-')} |"
            )
        lines.append("")
        lines.append("> 배당수익률(연말종가)은 krx_weekly 연말 종가로 직접 계산(DART 결의시점 시가배당률에")
        lines.append("> 결측 있는 옛 연도 보완용, 260707 추가) — 두 값은 기준일이 달라 다를 수 있음.")
        lines.append("")

    trans = (d.get("governance_trend") or {}).get("transitions") or []
    if trans:
        lines.append("## 지배구조 준수 변화 (15개 지표)")
        lines.append("")
        for t in trans:
            arrow = {"improved": "✅ 개선", "regressed": "⚠️ 후퇴", "changed": "변경"}.get(t.get("direction"), "-")
            lines.append(f"- {t.get('label')}: {t.get('from_val')} → {t.get('to_val')} ({arrow}, {t.get('to_dt')})")
        lines.append("")

    overall = d.get("overall") or {}
    lines.append("## 주주환원 종합")
    lines.append(f"- 배당 총액: {_f(overall.get('dividend_krw'))}원 (최근 확정 사업연도)")
    lines.append(f"- 자사주 소각금액: {_f(overall.get('buyback_cancelation_krw'))}원 (최근 {d['lookback_years']}년 누적)")
    csr = overall.get("cash_shareholder_return_pct")
    lines.append(f"- 환원율(CSR, 배당+소각÷순이익): {csr}%" if csr is not None else "- 환원율(CSR): 산출 불가(순이익 데이터 부족)")
    total_gain = overall.get("total_book_value_gain_loss_krw")
    if total_gain:
        lines.append(f"- 자사주소각 장부가 손익 합계: {_f(total_gain)}원")
    lines.append(f"> {overall.get('period_note', '')}")
    lines.append("")

    flags = d.get("data_quality_flags") or []
    if flags:
        lines.append("## ⚠ 데이터 품질 참고")
        for f in flags:
            lines.append(f"- {f}")
        lines.append("")

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def shareholder_commitment(
        company: str,
        lookback_years: int = 3,
        format: str = "md",
    ) -> str:
        """desc: 밸류업 계획·배당·자사주 소각 **약속 vs 실제 이행** 추적. proxy_advise_before_meeting이
        주총 시점 1회성 판단이라면, 이 tool은 주총과 무관하게 연중 스튜어드십 관여용 — "작년에 공표한
        계획을 실제로 지켰나"를 본다. 자사주 소각 사이클마다 매입시점 BPS 대비 실제 매입가를 비교해
        장부가(BPS) 기준 손익을 원화로 계산(내재가치 판단은 하지 않음, 장부가 사실만).
        when: 스튜어드십/기관투자자 engagement, 연례 보유종목 점검, "이 회사 약속 지켰나" 질문.
        rule: value_up(계획)+corp_gov_report(준수변화)+dividend_disclosure(실제배당)+treasury_share(실제소각,
        260707 원문단위버그 수정 완료)를 조합. 결정↔실행 매칭 오탐 의심 사이클은 sanity 필터로
        제외하고 data_quality_flags에 남김(알려진 treasury_share `_link_cycles` 별개 이슈 대응).
        lookback_years: 조회 기간(년), 기본 3
        ref: value_up, corp_gov_report, dividend_disclosure, treasury_share
        """
        payload = await build_shareholder_commitment_payload(company, lookback_years=lookback_years)
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") in ("ambiguous", "error"):
            return _render_status(payload)
        return _render(payload)
