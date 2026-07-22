"""risk_events public tool."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.risk_events import build_risk_events_payload


def _render_error(payload: dict[str, Any]) -> str:
    lines = [f"# risk_events: {payload.get('subject', '')}", ""]
    for warning in payload.get("warnings", []):
        lines.append(f"- {warning}")
    return "\n".join(lines)


def _render_ambiguous(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    lines = [
        f"# risk_events: {data.get('query', '')}",
        "",
        "회사 식별이 애매해 자동 선택하지 않았다.",
        "",
        "| 회사명 | ticker | corp_code | company_id |",
        "|------|--------|-----------|------------|",
    ]
    for item in data.get("candidates", []):
        lines.append(
            f"| {item.get('corp_name', '')} | `{item.get('ticker', '')}` | `{item.get('corp_code', '')}` | `{item.get('company_id', '')}` |"
        )
    return "\n".join(lines)


def _link(rcept_no: str) -> str:
    url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}" if rcept_no else ""
    return f"[{rcept_no}]({url})" if url else f"`{rcept_no}`"


_CAT_ORDER = ("serious_accident", "embezzlement", "derivative_loss", "rehabilitation", "production_halt", "dissolution")
_CAT_LABEL = {
    "serious_accident": "중대재해",
    "embezzlement": "횡령·배임",
    "derivative_loss": "파생상품손실",
    "rehabilitation": "회생·부도",
    "production_halt": "생산중단·영업정지",
    "dissolution": "해산",
}


def _render(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    market = data.get("mode") == "market_scan"
    window = data.get("window", {})
    counts = data.get("event_count", {})
    usage = data.get("usage", {})
    cat = data.get("category", "all")
    cat_note = f" — category: {_CAT_LABEL.get(cat, cat)}" if cat != "all" else ""
    title = "시장 전체 리스크 이벤트 공시" if market else f"{data.get('canonical_name', payload.get('subject', ''))} 리스크 이벤트"
    lines = [f"# {title} (risk_events){cat_note}", ""]
    if not market:
        lines.append(f"- company_id: `{data.get('company_id', '')}`")
    cat_summary = " / ".join(
        f"{_CAT_LABEL[c]} {counts.get(c, 0)}" for c in _CAT_ORDER if counts.get(c, 0)
    ) or "0"
    lines += [
        f"- 조사 구간: `{window.get('start_date', '')}` ~ `{window.get('end_date', '')}`" + (" (시장 전체 스캔)" if market else ""),
        f"- 사건 수: 총 {counts.get('total', 0)}건 — {cat_summary} / 종속·자회사 {counts.get('subsidiary_reports', 0)} / 정정 {counts.get('corrections', 0)}",
        f"- status: `{payload.get('status', '')}`",
        "",
        "## 사용량",
        f"- DART API 호출: {usage.get('dart_api_calls', 0)}회 (분당 한도 {usage.get('dart_daily_limit_per_minute', 1000)}회)",
        f"- MCP tool 호출: {usage.get('mcp_tool_calls', 1)}회",
        "",
    ]
    if payload.get("warnings"):
        lines.append("## 유의사항")
        for warning in payload["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    events = data.get("events", [])
    if not events:
        lines.append("조사 구간 내 리스크 이벤트 공시 없음.")
        return "\n".join(lines)

    casualties = data.get("casualties")
    if casualties and casualties.get("parsed_rows"):
        lines.append(
            f"## 중대재해 사상자 집계 (사건 {casualties['parsed_rows']}건 기준 — 같은 사건 정정·이중 공시는 최신 공시로 대체)"
        )
        lines.append(f"- 사망 {casualties.get('deaths', 0)}명 / 부상 {casualties.get('injuries', 0)}명")
        lines.append("")

    has_details = any(row.get("details") for row in events)
    if not has_details:
        lines.append("> 📋 기본 모드는 list.json 메타만 수집. `include_details=True`로 원문 파싱 (중대재해: 사상자·장소·조치 / 횡령배임: 혐의자·금액·자기자본% / 파생손실: 손실액·% / 생산중단: 부문·매출비중).\n")

    if market and data.get("by_company"):
        lines.extend(["## 회사별 건수", "| 회사 | 건수 |", "|------|------|"])
        for nm, c in data["by_company"].items():
            lines.append(f"| {nm} | {c} |")
        lines.append("")

    header = (
        ["날짜", "회사", "카테고리", "단계", "제목", "종속·자회사", "정정", "원문"]
        if market else ["날짜", "카테고리", "단계", "제목", "종속·자회사", "정정", "원문"]
    )
    lines.extend([
        "## 공시 타임라인",
        "| " + " | ".join(header) + " |",
        "|" + "------|" * len(header),
    ])
    for ev in events:
        sub = "Y" if ev.get("subsidiary_report") else "-"
        corr = "Y" if ev.get("is_correction") else "-"
        cells = [ev.get("rcept_dt", "")]
        if market:
            cells.append(ev.get("corp_name", "") or ev.get("filer_name", ""))
        cells += [
            ev.get("category_label", ""),
            ev.get("stage", ""),
            ev.get("report_nm", "")[:45],
            sub, corr,
            _link(ev.get("rcept_no", "")),
        ]
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")

    for ev in events:
        d = ev.get("details")
        if not d:
            continue
        who = f"{ev.get('corp_name', '')} — " if market and ev.get("corp_name") else ""
        lines.append(f"\n### 상세 ({ev.get('rcept_dt')} — {who}{ev.get('report_nm', '')[:45]})")
        if d.get("subsidiary_name"):
            lines.append(f"- 대상 회사: **{d['subsidiary_name']}**")
        cat_key = ev.get("category", "")
        if cat_key == "serious_accident":
            if ev.get("stage") == "처벌확인":
                if d.get("confirmed_date"):
                    lines.append(f"- 확인일자: {d['confirmed_date']}")
                if d.get("summary_excerpt"):
                    lines.append(f"- 본문 발췌: {d['summary_excerpt'][:300]}")
                continue
            lines.append(f"- 사상자: **사망 {d.get('deaths', 0)}명 / 부상 {d.get('injuries', 0)}명**")
            if d.get("accident_date"):
                lines.append(f"- 발생일자: {d['accident_date']} (고용노동부 보고: {d.get('labor_ministry_report_date', '-') or '-'})")
            if d.get("location"):
                lines.append(f"- 발생 장소: {d['location']}")
            if d.get("description"):
                lines.append(f"- 재해 내용: {d['description']}")
            if d.get("response_plan"):
                lines.append(f"- 조치·향후대책: {d['response_plan']}")
        elif cat_key == "embezzlement":
            if d.get("suspect"):
                lines.append(f"- 혐의자: **{d['suspect']}**")
            if d.get("amount_won"):
                lines.append(f"- 혐의 금액: **{d['amount_won']}원** (자기자본 대비 {d.get('equity_ratio_pct', '-') or '-'}%)")
            if d.get("summary_excerpt"):
                lines.append(f"- 본문 발췌: {d['summary_excerpt'][:250]}")
        elif cat_key == "derivative_loss":
            if d.get("loss_amount_won"):
                lines.append(f"- 손실액: **{d['loss_amount_won']}원** (자기자본 대비 {d.get('equity_ratio_pct', '-') or '-'}%)")
            if d.get("summary_excerpt"):
                lines.append(f"- 본문 발췌: {d['summary_excerpt'][:250]}")
        elif cat_key == "production_halt":
            if d.get("halted_business"):
                lines.append(f"- 중단 부문: **{d['halted_business']}** (매출 대비 {d.get('revenue_ratio_pct', '-') or '-'}%)")
            if d.get("reason"):
                lines.append(f"- 사유: {d['reason'][:200]}")
            if d.get("summary_excerpt"):
                lines.append(f"- 본문 발췌: {d['summary_excerpt'][:200]}")
        elif cat_key == "rehabilitation":
            if d.get("court"):
                lines.append(f"- 관할법원: {d['court']}")
            if d.get("event_date"):
                lines.append(f"- 신청/결정일: {d['event_date']}")
            if d.get("amount_won"):
                lines.append(f"- 부도금액: **{d['amount_won']}원**")
            if d.get("summary_excerpt"):
                lines.append(f"- 본문 발췌: {d['summary_excerpt'][:250]}")
        else:
            if d.get("summary_excerpt"):
                lines.append(f"- 본문 발췌: {d['summary_excerpt'][:300]}")

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def risk_events(
        company: str = "",
        category: str = "",
        start_date: str = "",
        end_date: str = "",
        include_details: bool = False,
        details_limit: int = 5,
        format: str = "md",
    ) -> str:
        """desc: 기업 리스크 이벤트 공시 통합 — 중대재해(산재·사망사고) / 횡령·배임 / 생산중단·영업정지. 본사·종속/자회사 변형 포함. company 미지정(공백)이면 **시장 전체 최근 30일(최대 90일) 스캔** — "최근 사고·사건 터진 기업들". include_details=True면 카테고리별 원문 파싱(사상자/혐의자·혐의금액·자기자본%/중단부문·매출비중).
        when: 중대재해, 산업재해, 산재 사망사고, 중대재해처벌법, 횡령, 배임, 생산중단, 영업정지, ESG 안전(S), 기업 리스크 모니터링(회사 미지정 시장 스캔).
        rule: DART list.json I001(거래소 주요경영사항)+B001(주요사항보고서) 양 채널 + 키워드 — 중대재해는 305사 3.5년 차집합 0 검증, 본문 파싱은 연속 2개 90일 윈도우 359건 전수 audit. company 지정 시 24개월 / 미지정 시 30일(최대 90일). 주의: 중대재해 수시공시 신설 2025-10 — 이전 무공시 ≠ 무사고. 공시는 대형 원청·지주사 집중 — 비상장 자회사 사고는 상장 모회사가 공시.
        category: `serious_accident` 중대재해 / `embezzlement` 횡령·배임 / `production_halt` 생산중단·영업정지 — 미지정 시 3종 전체.
        include_details: True면 원문 파싱 추가 (DART 호출 N회 증가) + 중대재해 사상자 집계.
        details_limit: 원문 파싱 건수 (기본 5, 최대 10).
        ref: corp_gov_report (지배구조 맥락), proxy_contest (소송·분쟁), evidence (원문 확인)
        """
        payload = await build_risk_events_payload(
            company,
            category=category,
            start_date=start_date,
            end_date=end_date,
            include_details=include_details,
            details_limit=max(1, min(details_limit, 10)),
        )
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") == "ambiguous":
            return _render_ambiguous(payload)
        if payload.get("status") == "error":
            return _render_error(payload)
        return _render(payload)
