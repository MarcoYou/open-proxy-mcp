"""v2 serious_accident public tool."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.serious_accident import build_serious_accident_payload


def _render_error(payload: dict[str, Any]) -> str:
    lines = [f"# serious_accident: {payload.get('subject', '')}", ""]
    for warning in payload.get("warnings", []):
        lines.append(f"- {warning}")
    return "\n".join(lines)


def _render_ambiguous(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    lines = [
        f"# serious_accident: {data.get('query', '')}",
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


_EVENT_LABEL = {"occurrence": "발생", "punishment": "처벌확인", "unknown": "기타"}


def _render(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    market = data.get("mode") == "market_scan"
    window = data.get("window", {})
    counts = data.get("event_count", {})
    usage = data.get("usage", {})
    title = "시장 전체 중대재해 공시" if market else f"{data.get('canonical_name', payload.get('subject', ''))} 중대재해 공시"
    lines = [f"# {title} (serious_accident)", ""]
    if not market:
        lines.append(f"- company_id: `{data.get('company_id', '')}`")
    lines += [
        f"- 조사 구간: `{window.get('start_date', '')}` ~ `{window.get('end_date', '')}`" + (" (시장 전체 스캔)" if market else ""),
        f"- 사건 수: 총 {counts.get('total', 0)}건 — 발생 {counts.get('occurrence', 0)} / 처벌확인 {counts.get('punishment', 0)} / 종속·자회사 {counts.get('subsidiary_reports', 0)} / 정정 {counts.get('corrections', 0)}",
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
        lines.append("조사 구간 내 중대재해 공시 없음.")
        return "\n".join(lines)

    casualties = data.get("casualties")
    if casualties and casualties.get("parsed_rows"):
        lines.append(
            f"## 사상자 집계 (사건 {casualties['parsed_rows']}건 기준 — 같은 사건 정정은 최신 공시로 대체)"
        )
        lines.append(f"- 사망 {casualties.get('deaths', 0)}명 / 부상 {casualties.get('injuries', 0)}명")
        lines.append("")

    has_details = any(row.get("details") for row in events)
    if not has_details:
        lines.append("> 📋 기본 모드는 list.json 메타만 수집. `include_details=True`로 사망·부상자 수/발생일자·장소/조치사항 원문 파싱.\n")

    if market and data.get("by_company"):
        lines.extend(["## 회사별 건수", "| 회사 | 건수 |", "|------|------|"])
        for nm, c in data["by_company"].items():
            lines.append(f"| {nm} | {c} |")
        lines.append("")

    header = (
        ["날짜", "회사", "구분", "제목", "종속·자회사", "정정", "원문"]
        if market else ["날짜", "구분", "제목", "종속·자회사", "정정", "원문"]
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
            _EVENT_LABEL.get(ev.get("event_type", ""), "기타"),
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
        if ev.get("event_type") == "punishment":
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

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def serious_accident(
        company: str = "",
        start_date: str = "",
        end_date: str = "",
        include_details: bool = False,
        details_limit: int = 5,
        format: str = "md",
    ) -> str:
        """desc: 중대재해 공시 통합 — 중대재해발생(본사·종속/자회사) + 중대재해 관련 (형사)처벌사실확인. company 미지정(공백)이면 **시장 전체 최근 30일(최대 90일) 스캔** — "최근 중대재해 발생한 기업들". include_details=True면 사망·부상자 수/발생일자·장소/재해내용/조치·향후대책 원문 파싱.
        when: 중대재해, 산업재해, 산재 사망사고, 안전사고 이력, 중대재해처벌법 리스크, ESG 안전(S) 점검, 최근 중대재해 공시 모니터링(회사 미지정). 건설·조선·중공업 분석 시 체크포인트.
        rule: DART list.json pblntf_detail_ty=I001 + '중대재해' 키워드 — 변형 5종(종속회사/자회사/기재정정 포함) 모두 매칭, 305사(고위험 49 + KOSPI100·KOSDAQ100 + 건설 56) 3.5년 검증 — I 전체 대비 차집합 0·truncation 0. company 지정 시 기본 lookback 24개월 / 미지정 시 30일(최대 90일). 주의: 거래소 수시공시 항목 신설이 2025-10월이라 이전 무공시는 무사고 의미 아님. 공시는 대형 원청·지주사 집중 — 비상장 자회사 사고는 상장 모회사가 공시.
        include_details: True면 원문 파싱 추가 (DART 호출 N회 증가) + 사상자 집계.
        details_limit: 원문 파싱 건수 (기본 5, 최대 10).
        ref: corp_gov_report (지배구조 맥락), evidence (원문 확인)
        """
        payload = await build_serious_accident_payload(
            company,
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
