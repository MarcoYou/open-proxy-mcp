"""v2 corp_gov_report public tool."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.corp_gov_report import build_corp_gov_report_payload


def _render_error(payload: dict[str, Any]) -> str:
    lines = [f"# corp_gov_report: {payload.get('subject', '')}", ""]
    for warning in payload.get("warnings", []):
        lines.append(f"- {warning}")
    return "\n".join(lines)


def _render_ambiguous(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    lines = [
        f"# corp_gov_report: {data.get('query', '')}",
        "",
        "회사 식별이 애매해 자동 선택하지 않았다.",
        "",
        "| 회사명 | ticker | corp_code |",
        "|------|--------|-----------|",
    ]
    for item in data.get("candidates", []):
        lines.append(
            f"| {item.get('corp_name', '')} | `{item.get('ticker', '')}` | `{item.get('corp_code', '')}` |"
        )
    return "\n".join(lines)


def _link(rcept_no: str) -> str:
    url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}" if rcept_no else ""
    return f"[{rcept_no}]({url})" if url else f"`{rcept_no}`"


def _render(payload: dict[str, Any], scope: str) -> str:
    data = payload.get("data", {})
    meta = data.get("report_meta", {}) or {}
    overview = data.get("company_overview", {}) or {}
    usage = data.get("usage", {})

    lines = [
        f"# {data.get('canonical_name', payload.get('subject', ''))} 기업지배구조보고서 (corp_gov_report)",
        "",
        f"- company_id: `{data.get('company_id', '')}`",
        f"- 시장: `{data.get('market', '')}` (의무대상: {'✓' if data.get('mandatory') else '✗ 자율공시'})",
        f"- scope: `{scope}`",
        f"- 최신 보고서: {meta.get('rcept_dt', '-')} / 공시대상기간 ~ {meta.get('reporting_period_end', '-')}",
        f"- 원문: {_link(meta.get('rcept_no', ''))}",
        f"- 총 {data.get('filings_count', 0)}건 이력",
        f"- status: `{payload.get('status', '')}`",
        "",
        "## 사용량",
        f"- DART API 호출: {usage.get('dart_api_calls', 0)}회 (분당 한도 {usage.get('dart_daily_limit_per_minute', 1000)})",
        f"- MCP tool 호출: {usage.get('mcp_tool_calls', 1)}회",
        "",
    ]
    if payload.get("warnings"):
        lines.append("## 유의사항")
        for w in payload["warnings"]:
            lines.append(f"- {w}")
        lines.append("")

    if scope == "filings":
        filings = data.get("filings", [])
        if not filings:
            lines.append("제출 이력 없음.")
            return "\n".join(lines)
        lines.extend([
            "## 제출 이력",
            "| 제출일 | 보고서명 | 원문 |",
            "|--------|----------|------|",
        ])
        for f in filings:
            lines.append(f"| {f.get('rcept_dt', '')} | {f.get('report_nm', '')[:40]} | {_link(f.get('rcept_no', ''))} |")
        return "\n".join(lines)

    # summary/metrics/principles 공통: 기업개요 + 준수율
    lines.extend([
        "## 기업개요",
        f"- 최대주주: **{overview.get('max_shareholder', '-') or '-'}** ({overview.get('max_shareholder_pct', '-') or '-'}%)",
        f"- 소액주주 지분율: {overview.get('minority_shareholder_pct', '-') or '-'}%",
        f"- 업종: {overview.get('industry', '-') or '-'}",
        f"- 주요 제품: {overview.get('main_products', '-') or '-'}",
        f"- 기업집단: {overview.get('corporate_group', '-') or '-'}",
        f"- 매출 (연결): {overview.get('revenue_current', '-') or '-'}백만원",
        f"- 영업이익 (연결): {overview.get('operating_income_current', '-') or '-'}백만원",
        f"- 순이익 (연결): {overview.get('net_income_current', '-') or '-'}백만원",
        f"- 자산총액 (연결): {overview.get('total_assets_current', '-') or '-'}백만원",
        "",
        "## 지배구조 핵심지표 준수",
    ])
    rate = meta.get("compliance_rate")
    if rate is not None:
        lines.append(f"- **준수율: {rate}%**")
    lines.append(
        f"- 15개 지표 중 **{meta.get('metrics_compliant', 0)}개 준수 / {meta.get('metrics_non_compliant', 0)}개 미준수** (파싱 {meta.get('metrics_parsed_count', 0)}건)"
    )
    lines.append("")

    if scope == "summary":
        ms = data.get("metrics_summary", [])
        if ms:
            lines.extend([
                "## 지표 요약",
                "| # | 지표 | 준수 |",
                "|---|------|------|",
            ])
            for i, m in enumerate(ms, start=1):
                cur = m.get("current", "-") or "-"
                mark = "✅" if cur in ("O", "○", "준수") else ("❌" if cur in ("X", "×", "미준수") else "—")
                lines.append(f"| {i} | {m.get('label', '')[:60]} | {mark} {cur} |")

    if scope == "metrics":
        mlist = data.get("metrics", [])
        if mlist:
            lines.extend([
                "## 15 지표 상세",
                "| # | 지표 | 당기 | 직전기 | 비고 |",
                "|---|------|------|--------|------|",
            ])
            for i, m in enumerate(mlist, start=1):
                cur = m.get("current", "-") or "-"
                prior = m.get("prior", "-") or "-"
                mark_cur = "✅" if cur in ("O", "○", "준수") else ("❌" if cur in ("X", "×", "미준수") else "—")
                mark_prior = "✅" if prior in ("O", "○", "준수") else ("❌" if prior in ("X", "×", "미준수") else "—")
                lines.append(
                    f"| {i} | {m.get('label', '')[:60]} | {mark_cur} {cur} | {mark_prior} {prior} | {m.get('note', '')[:80]} |"
                )

    if scope == "principles":
        pl = data.get("principles", [])
        if not pl:
            lines.append("세부원칙 응답 추출 실패.")
        else:
            lines.append("## 세부원칙 준수 응답 (최대 30)")
            for i, p in enumerate(pl, start=1):
                lines.append(f"\n**{i}. {p.get('principle_snippet', '')[:100]}**")
                lines.append(f"→ {p.get('response', '')[:200]}")

    if scope == "timeline":
        reports = sorted(data.get("timeline", []), key=lambda r: r.get("rcept_dt", ""), reverse=True)
        transitions = data.get("transitions", [])
        if not reports:
            lines.append("연도별 이력 없음.")
        else:
            lines.extend([
                "## 연도별 준수율 추이",
                "| 제출일 | 준수율 | 원문 | 정정? |",
                "|--------|--------|------|-------|",
            ])
            for r in reports:
                cr = r.get("compliance_rate")
                cr_str = f"{cr}%" if cr is not None else "-"
                corr = "✓" if r.get("is_correction") else "-"
                lines.append(f"| {r.get('rcept_dt', '')} | {cr_str} | {_link(r.get('rcept_no', ''))} | {corr} |")

            if transitions:
                lines.extend(["", "## 지표 전환 (연도간 변화)"])
                improved = [t for t in transitions if t.get("direction") == "improved"]
                regressed = [t for t in transitions if t.get("direction") == "regressed"]
                changed = [t for t in transitions if t.get("direction") == "changed"]
                if improved:
                    lines.append(f"\n### ✅ 개선 ({len(improved)})")
                    for t in improved:
                        lines.append(f"- **{t['label'][:60]}** | {t['from_dt']} `{t['from_val']}` → {t['to_dt']} `{t['to_val']}`")
                if regressed:
                    lines.append(f"\n### ❌ 후퇴 ({len(regressed)})")
                    for t in regressed:
                        lines.append(f"- **{t['label'][:60]}** | {t['from_dt']} `{t['from_val']}` → {t['to_dt']} `{t['to_val']}`")
                if changed:
                    lines.append(f"\n### — 기타 변동 ({len(changed)})")
                    for t in changed:
                        lines.append(f"- {t['label'][:60]} | {t['from_dt']} `{t['from_val']}` → {t['to_dt']} `{t['to_val']}`")
            else:
                lines.append("\n지표 전환 없음 (연도간 동일 유지)")

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def corp_gov_report(
        company: str,
        scope: str = "summary",
        year: int = 0,
        format: str = "md",
    ) -> str:
        """desc: 기업지배구조보고서(거버넌스 종합 평가) data tool. 최대주주/지분율/소액주주 + 15개 핵심지표 준수 여부(O/X) + 세부원칙 응답 + 제출 이력 + 연도별 추이 제공. **2026년 제출분부터 KOSPI 전체 의무** (이전: 2024=자산 5천억+, 2022=자산 1조+, 2019=자산 2조+), KOSDAQ은 자율공시. 제출 시한 매년 5월말, 연중 `[기재정정]` 재제출 빈번.
        when: 거버넌스 종합 평가, 15개 지표 준수 현황, **연도별 준수율 변화 추적**, ISS·Glass Lewis 수준 배경자료. prepare_vote_brief/engagement_case에 거시 컨텍스트 추가.
        rule: DART 전용 구조화 API 없음 — `list.json`(pblntf_ty=I) + 키워드 `"기업지배구조보고서공시"` (금융지주 "연차보고서" 등 다른 서식 제외) + 원문 다운로드·파싱. 기본 lookback 4년. 파싱 원리: 15개 표준 지표 라벨 prefix 매칭으로 위치 찾고 블록별로 O/X 2개(당기·직전) + 비고 텍스트 추출. 비고 없는 서식(삼성) / 일부만 비고(SK하이닉스) / 매건 비고(현대차) 모두 지원.
        scope: `summary`(기본, 기업개요 + 준수율 + 15지표 ✅/❌) / `metrics`(15 지표 당기·직전기 + 비고 상세) / `principles`(세부원칙별 응답 텍스트) / `filings`(제출 이력) / `timeline`(연도별 준수율 추이 + 지표 전환: improved / regressed / changed).
        year: 특정 사업연도 지정(예: 2023). 기본 0이면 최신 보고서.
        ref: ownership_structure (지배구조), shareholder_meeting (주총 운영), proxy_contest (분쟁 맥락), evidence (원문)
        """
        payload = await build_corp_gov_report_payload(
            company,
            scope=scope,
            year=year,
        )
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") == "ambiguous":
            return _render_ambiguous(payload)
        if payload.get("status") == "error":
            return _render_error(payload)
        return _render(payload, scope)
