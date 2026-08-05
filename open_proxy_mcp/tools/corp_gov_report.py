"""corp_gov_report public tool."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.tools._shared import company_id_line
from open_proxy_mcp.services.corp_gov_report import build_corp_gov_report_payload


#: 주총 의결 내용은 한 회사가 60행을 넘기도 한다 — md 는 잘라 싣고 전체는 json 으로 넘긴다.
_TABLE_ROW_LIMIT = 40


def _amt(v) -> str:
    """금액 + 단위. 값이 없으면 단위를 붙이지 않는다 — 「-백만원」은 음수로 읽힌다."""
    if v in (None, "", "-"):
        return "-"
    return f"{v}백만원"


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
        f"# {data.get('canonical_name', payload.get('subject', ''))} 기업지배구조보고서",
        "",
        f"- 시장: `{data.get('market', '')}` (의무대상: {'✓' if data.get('mandatory') else '✗ 자율공시'})",
        f"- scope: `{scope}`",
        f"- 최신 보고서: {meta.get('rcept_dt', '-')} / 공시대상기간 ~ {meta.get('reporting_period_end', '-')}",
        f"- 원문: {_link(meta.get('rcept_no', ''))}",
        f"- 총 {data.get('filings_found', 0)}건 이력",
        "",
        "## 사용량",
        f"- DART API 호출: {usage.get('dart_api_calls', 0)}회 (분당 한도 {usage.get('dart_daily_limit_per_minute', 1000)})",
        f"- MCP tool 호출: {usage.get('mcp_tool_calls', 1)}회",
        "",
    ]
    if payload.get("warnings"):
        lines.append("## 유의사항")
        _cid = company_id_line(data)
        if _cid:
            lines.append(_cid)
        for w in payload["warnings"]:
            lines.append(f"- {w}")
        lines.append("")

    # 금융회사 연차보고서는 거래소 서식 표가 애초에 없다 — 「읽지 못했습니다」로 내려가면
    # 파싱 실패로 읽힌다. 유의사항까지만 싣고 원문으로 보낸다.
    if data.get("report_format") == "financial_holding_annual":
        lines.extend([
            "## 서식",
            f"- {meta.get('format_note', '금융회사 지배구조·보수체계 연차보고서')}",
            "- 거래소 서식 기업지배구조보고서가 아니므로 핵심지표·세부원칙·서식 표는 제공하지 않습니다.",
            f"- 첨부 PDF: {_link(meta.get('rcept_no', ''))}",
        ])
        return "\n".join(lines)

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
        # 값이 없을 때 「-백만원」이 되면 음수로 읽힌다 — 없으면 단위도 붙이지 않는다(260728 QA 지적).
        f"- 매출 (연결): {_amt(overview.get('revenue_current'))}",
        f"- 영업이익 (연결): {_amt(overview.get('operating_income_current'))}",
        f"- 순이익 (연결): {_amt(overview.get('net_income_current'))}",
        f"- 자산총액 (연결): {_amt(overview.get('total_assets_current'))}",
        "",
        "## 지배구조 핵심지표 준수",
    ])
    rate = meta.get("compliance_rate")
    if rate is not None:
        lines.append(f"- **준수율: {rate}%**")
    # 파싱 0건이면 「0개 준수」가 아니라 「읽지 못함」이다 — 굵게 강조된 0이 미준수 기업으로
    # 읽힌다(260728 QA 지적: KB금융 사례).
    _parsed = meta.get("metrics_parsed_count", 0) or 0
    if _parsed:
        lines.append(
            f"- 15개 지표 중 **{meta.get('metrics_compliant', 0)}개 준수 / "
            f"{meta.get('metrics_non_compliant', 0)}개 미준수** (지표 {_parsed}건 확인)"
        )
    else:
        lines.append("- 핵심지표 준수 여부를 읽지 못했습니다 — 미준수라는 뜻이 아닙니다. 원문을 확인하세요.")
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
            lines.append(f"## 세부원칙 준수 응답 ({len(pl)}건)")
            for i, p in enumerate(pl, start=1):
                num = p.get("principle_number", "?")
                desc = p.get("principle_description", "") or p.get("principle_snippet", "")
                resp = p.get("response", "") or "-"
                lines.append(f"\n**{i}. (세부원칙 {num}) {desc[:200]}**")
                lines.append(f"→ {resp[:300]}")

    if scope == "tables":
        tables = data.get("tables", {}) or {}
        if not tables:
            lines.append("서식 표를 읽지 못했습니다 — 원문을 확인하세요.")
        for number in sorted(tables, key=lambda n: tuple(int(x) for x in n.split("-"))):
            table = tables[number]
            cols = table.get("columns", [])
            rows = table.get("rows", [])
            lines.append(f"\n## 표 {number}: {table.get('title', '')} ({len(rows)}행)")
            if not cols:
                continue
            lines.append("| " + " | ".join(c.replace("\n", " ") for c in cols) + " |")
            lines.append("|" + "|".join("---" for _ in cols) + "|")
            for row in rows[:_TABLE_ROW_LIMIT]:
                cells = [str(row.get(c, "") or "-").replace("\n", " ").replace("|", "\\|")[:120] for c in cols]
                lines.append("| " + " | ".join(cells) + " |")
            if len(rows) > _TABLE_ROW_LIMIT:
                lines.append(f"\n_{len(rows)}행 중 {_TABLE_ROW_LIMIT}행만 표시 — 전체는 format=json._")

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
        """desc: 기업지배구조보고서. 최대주주/지분율 + 15개 핵심지표 O/X + 세부원칙 응답 + 연도별 추이. **2026 제출분부터 KOSPI 전체 의무**, KOSDAQ 자율. 제출 시한 매년 5월말, 연중 정정 빈번.
        when: 거버넌스 종합 평가, 15개 지표 준수 현황, 연도별 변화 추적. B외국계 수준 배경자료.
        rule: DART list.json + 키워드 "기업지배구조보고서공시" + 원문 파싱. 기본 lookback 4년. 15개 표준 지표 라벨 prefix 매칭으로 O/X 당기·직전기 + 비고 추출.
        scope: `summary` 기업개요+준수율+15지표 / `metrics` 15지표 + 비고 상세 / `principles` 세부원칙 응답 / `filings` 제출 이력 / `timeline` 연도별 추이 + 지표 전환 / `tables` 서식 표 원본 10종 — 주총 운영(1-1-1 소집공고~주총 실제 일수·개최장소·감사 출석 · 1-2-1 집중일 회피·서면/전자투표 · 1-2-2 안건별 찬반 주식수) · 이사(4-2-1 선임·변동사유 · 4-3-1 후보 사전 정보제공기간 · 5-2-1 사외이사 겸직 · 7-1-1 이사회 개최·안건통지 간격 · 7-2-1 개별이사 3개년 출석률·찬성률) · 감사(9-1-1 내부감사기구 구성·재무전문가 · 10-2-1 외부감사인 소통내역)
        year: 사업연도 지정 (0이면 최신).
        ref: ownership_structure, shareholder_meeting_notice, proxy_contest, evidence
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
