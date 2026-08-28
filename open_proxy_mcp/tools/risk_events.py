"""risk_events public tool."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.tools._shared import company_id_line
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


# 🔴 2026-08-27 에 늘어난 거래소 시장조치 6종이 여기 빠져 있어 머리 요약줄과
# category 라벨에서 통째로 사라져 있었다 — 목록엔 나오는데 세어지지 않았다.
_CAT_ORDER = (
    "serious_accident", "embezzlement", "derivative_loss", "rehabilitation",
    "production_halt", "dissolution",
    "trading_halt", "listing_review", "litigation", "capital_impairment",
    "inquiry_disclosure", "investment_judgment",
)
_CAT_LABEL = {
    "serious_accident": "중대재해",
    "embezzlement": "횡령·배임",
    "derivative_loss": "파생상품손실",
    "rehabilitation": "회생·부도",
    "production_halt": "생산중단·영업정지",
    "dissolution": "해산",
    "trading_halt": "매매거래정지",
    "listing_review": "상장적격성·관리종목",
    "litigation": "소송·제재",
    "capital_impairment": "자본잠식",
    "inquiry_disclosure": "조회공시·풍문해명",
    "investment_judgment": "투자판단 주요경영사항",
}


def _fields_new_categories(cat_key: str, d: dict[str, Any]) -> list[str]:
    """2026-08-27 에 연 거래소 시장조치 계열 여섯 — 여기가 비어 있어서
    「### 상세」 머리만 찍히고 알맹이가 한 줄도 안 나갔다."""
    out: list[str] = []
    if cat_key == "trading_halt":
        if d.get("subject"):
            out.append(f"- 대상 종목: **{d['subject']}**")
        if d.get("halt_state"):
            out.append(f"- 상태: **{d['halt_state']}**")
        if d.get("halt_type"):
            out.append(f"- 정지 유형: {d['halt_type']}")
        if d.get("reason"):
            out.append(f"- 정지·해제 사유: **{d['reason']}**")
        if d.get("period"):
            out.append(f"- {d.get('period_label', '기간')}: {d['period']}")
        if d.get("halted_at"):
            out.append(f"- 정지일시: {d['halted_at']}")
        if d.get("resumed_at"):
            out.append(f"- 해제일시: {d['resumed_at']}")
        if d.get("legal_basis"):
            out.append(f"- 근거 규정: {d['legal_basis']}")
        if d.get("note"):
            out.append(f"- 회사가 덧붙인 사항: {d['note']}")
    elif cat_key == "listing_review":
        if d.get("title"):
            out.append(f"- 안내 제목: **{d['title']}**")
        if d.get("reason"):
            out.append(f"- 사유(원문 낱말 기준): **{d['reason']}**")
        if d.get("deadline"):
            out.append(f"- 기한: **{d['deadline']}**")
        if d.get("legal_basis"):
            out.append(f"- 근거 규정: {d['legal_basis']}")
        for q in d.get("content") or []:
            out.append(f"- {q}")
    elif cat_key == "capital_impairment":
        if d.get("statement_basis"):
            out.append(f"- 재무제표 종류: {d['statement_basis']} (기준일 {d.get('period_end', '-') or '-'})")
        if d.get("impairment_rate_pct"):
            out.append(f"- 자본잠식률: **{d['impairment_rate_pct']}%** (전기 {d.get('prior_impairment_rate_pct', '-') or '-'})")
        if d.get("equity_won") is not None:
            out.append(f"- 자본총계 {d['equity_won']:,}원 / 자본금 {d.get('paid_in_capital_won', 0):,}원")
        if d.get("revenue_won") is not None:
            out.append(f"- 매출액 {d['revenue_won']:,}원 (증감 {d.get('revenue_change_pct', '-') or '-'}%)")
        if d.get("operating_income_won") is not None:
            out.append(f"- 영업이익 {d['operating_income_won']:,}원 (증감 {d.get('operating_income_change_pct', '-') or '-'}%)")
        if d.get("cause"):
            out.append(f"- 회사가 밝힌 원인: {d['cause']}")
        if d.get("unit_conflict"):
            out.append("- 금액 줄은 뺐습니다 — 공시의 단위 표기와 값이 서로 어긋나 어느 쪽이 맞는지 "
                       "우리가 고르지 않았습니다. 아래 원문에서 직접 확인하십시오.")
    elif cat_key == "litigation":
        if d.get("case_name"):
            out.append(f"- 사건: **{d['case_name']}** {d.get('case_no', '') or ''}")
        elif d.get("case_no"):
            out.append(f"- 사건번호: **{d['case_no']}**")
        if d.get("penalty_type"):
            out.append(f"- 제재 종류: **{d['penalty_type']}** (부과기관 {d.get('authority', '-') or '-'})")
        if d.get("plaintiff"):
            out.append(f"- 원고·신청인: {d['plaintiff']}")
        if d.get("defendant"):
            out.append(f"- 피고: {d['defendant']}")
        if d.get("amount_won") is not None:
            out.append(f"- 금액: **{d['amount_won']:,}원** (자기자본 대비 {d.get('equity_ratio_pct', '-') or '-'}%)")
        if d.get("payment_due"):
            out.append(f"- 납부기한: **{d['payment_due']}**")
        if d.get("court"):
            out.append(f"- 관할법원: {d['court']}")
        for label, key in (("청구내용", "claim"), ("판결·결정 내용", "ruling"),
                           ("부과사유", "reason"), ("향후 대책", "response_plan")):
            if d.get(key):
                out.append(f"- {label}: {d[key]}")
        if d.get("filed_date") or d.get("judged_date"):
            out.append(f"- 제기일 {d.get('filed_date', '-') or '-'} / 판결일 {d.get('judged_date', '-') or '-'}")
    elif cat_key == "inquiry_disclosure":
        if d.get("requested"):
            out.append(f"- 거래소 요구 내용: **{d['requested']}**")
        if d.get("answer_due"):
            out.append(f"- 공시 시한: **{d['answer_due']}**")
        if d.get("report_content"):
            out.append(f"- 풍문·보도 내용: {d['report_content']} ({d.get('media', '-') or '-'}, {d.get('reported_on', '-') or '-'})")
        for q in d.get("clarification") or []:
            out.append(f"- 회사 해명: {q}")
        if d.get("redisclosure_due"):
            out.append(f"- 재공시 예정일: **{d['redisclosure_due']}**")
    elif cat_key == "investment_judgment":
        if d.get("title"):
            out.append(f"- 제목: **{d['title']}**")
        if d.get("decided_on"):
            out.append(f"- 결의·확인일: {d['decided_on']}")
        if d.get("deal_size_won") is not None:
            out.append(f"- 규모: **{d['deal_size_won']:,}원** (연결 매출액의 {d.get('revenue_ratio_pct', '-') or '-'}%)")
        if d.get("upfront_won") is not None:
            out.append(f"- 계약금(착수금): {d['upfront_won']:,}원 — 총액이 아닙니다")
        if d.get("milestone_won") is not None:
            out.append(f"- 마일스톤: {d['milestone_won']:,}원")
        for q in d.get("content") or []:
            out.append(f"- {q}")
    return out


def _detail_fields(ev: dict[str, Any], d: dict[str, Any]) -> list[str]:
    """카테고리별 값 줄. 원문은 여기서 찍지 않는다 — 뒤에 통째로 붙는다."""
    out: list[str] = []
    if not d:
        return out
    if d.get("subsidiary_name"):
        out.append(f"- 대상 회사: **{d['subsidiary_name']}**")
    cat_key = ev.get("category", "")
    if cat_key == "serious_accident":
        if ev.get("stage") == "처벌확인":
            if d.get("confirmed_date"):
                out.append(f"- 확인일자: {d['confirmed_date']}")
            return out
        out.append(f"- 사상자: **사망 {d.get('deaths', 0)}명 / 부상 {d.get('injuries', 0)}명**")
        if d.get("accident_date"):
            out.append(f"- 발생일자: {d['accident_date']} (고용노동부 보고: {d.get('labor_ministry_report_date', '-') or '-'})")
        if d.get("location"):
            out.append(f"- 발생 장소: {d['location']}")
        if d.get("description"):
            out.append(f"- 재해 내용: {d['description']}")
        if d.get("response_plan"):
            out.append(f"- 조치·향후대책: {d['response_plan']}")
    elif cat_key == "embezzlement":
        if d.get("suspect"):
            out.append(f"- 혐의자: **{d['suspect']}**")
        if d.get("amount_won"):
            out.append(f"- 혐의 금액: **{d['amount_won']}원** (자기자본 대비 {d.get('equity_ratio_pct', '-') or '-'}%)")
    elif cat_key == "derivative_loss":
        if d.get("loss_amount_won"):
            out.append(f"- 손실액: **{d['loss_amount_won']}원** (자기자본 대비 {d.get('equity_ratio_pct', '-') or '-'}%)")
    elif cat_key == "production_halt":
        if d.get("halted_business"):
            out.append(f"- 중단 부문: **{d['halted_business']}** (매출 대비 {d.get('revenue_ratio_pct', '-') or '-'}%)")
        if d.get("reason"):
            out.append(f"- 사유: {d['reason'][:200]}")
    elif cat_key == "rehabilitation":
        if d.get("court"):
            out.append(f"- 관할법원: {d['court']}")
        if d.get("event_date"):
            out.append(f"- 신청/결정일: {d['event_date']}")
        if d.get("amount_won"):
            out.append(f"- 부도금액: **{d['amount_won']}원**")
    else:
        out.extend(_fields_new_categories(cat_key, d))
    # 원문이 실리면 발췌는 같은 말을 두 번 쓰는 것이 된다.
    if d.get("summary_excerpt") and not d.get("source_text"):
        out.append(f"- 본문 발췌: {d['summary_excerpt'][:300]}")
    return out


def _source_block(d: dict[str, Any]) -> list[str]:
    """공시 원문 그대로. **표로 대체하지 않고 표에 더한다.**"""
    body = d.get("source_text")
    if not body:
        return []
    total = d.get("source_text_chars") or len(body)
    if d.get("source_text_truncated"):
        head = (f"- 공시 원문 (전체 {total:,}자 중 앞 {len(body):,}자 — "
                "`source_chars` 를 올리면 더 실립니다)")
    else:
        head = f"- 공시 원문 (전문 {total:,}자)"
    return ["", head, "", "```", body, "```"]


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
        _cid = company_id_line(data)
        if _cid:
            lines.append(_cid)
    cat_summary = " / ".join(
        f"{_CAT_LABEL[c]} {counts.get(c, 0)}" for c in _CAT_ORDER if counts.get(c, 0)
    ) or "0"
    lines += [
        f"- 조사 구간: `{window.get('start_date', '')}` ~ `{window.get('end_date', '')}`" + (" (시장 전체 스캔)" if market else ""),
        f"- 사건 수: 총 {counts.get('total', 0)}건 — {cat_summary} / 종속·자회사 {counts.get('subsidiary_reports', 0)} / 정정 {counts.get('corrections', 0)}",
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
        lines.append("> 기본은 공시 목록만 봅니다. **관리종목·거래정지의 사유·유예기간·해소요건**, "
                     "중대재해 사상자·장소·조치, 횡령배임 혐의자·금액·자기자본 대비 비율, "
                     "파생손실액과 비율, 생산중단 부문·매출 비중은 원문을 읽어야 나옵니다 — "
                     "필요하면 말씀해 주세요(include_details 옵션). 원문 창은 `source_chars` 로 넓힙니다.\n")

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
        d = ev.get("details") or {}
        why_missing = ev.get("detail_note", "")
        if not d and not why_missing:
            continue
        who = f"{ev.get('corp_name', '')} — " if market and ev.get("corp_name") else ""
        lines.append(f"\n### 상세 ({ev.get('rcept_dt')} — {who}{ev.get('report_nm', '')[:45]})")
        lines.extend(_detail_fields(ev, d))
        # 🔴 **원문을 항상 같이 싣는다.** 잘라 낸 요약만 주면 「25매매거래일 지속 ·
        # 5거래일 남음」 같은 유예기간·해소요건이 통째로 사라진다.
        lines.extend(_source_block(d))
        if why_missing:
            lines.append(f"- 확인 못 한 이유: {why_missing}")
        if not d:
            lines.append(f"- 원문 뷰어: {_link(ev.get('rcept_no', ''))}")

    guide = data.get("details_guide") or {}
    if guide:
        lines.append("\n## 원문에서 더 볼 곳 (카테고리별)")
        for cat_key, g in guide.items():
            lines.append(f"\n**{g.get('label', cat_key)}**")
            if g.get("where"):
                lines.append(f"- 이 서식에서 사유·기한·요건이 있는 자리: {g['where']}")
            if g.get("alt"):
                lines.append(f"- 거기 없으면 볼 곳: {g['alt']}")
            if g.get("next"):
                lines.append(f"- 그래도 안 나오면: {g['next']}")
        win = data.get("source_window") or {}
        if win:
            lines.append(
                f"\n- 원문 창은 지금 {win.get('source_chars')}자입니다. 잘렸으면 `source_chars` 를 "
                f"최대 {win.get('max')}까지 올려 다시 부르면 더 실립니다. "
                "표 위 접수번호 링크는 거래소·DART 원문 뷰어입니다."
            )

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
        source_chars: int = 4000,
        format: str = "md",
    ) -> str:
        """desc: 기업 리스크 이벤트 공시 통합 — 중대재해(산재·사망사고) / 횡령·배임 / 생산중단·영업정지 / **매매거래정지 / 관리종목·상장적격성 / 소송·제재 / 자본잠식**. 본사·종속/자회사 변형 포함. company 미지정(공백)이면 **시장 전체 최근 30일(최대 90일) 스캔** — "최근 사고·사건 터진 기업들". include_details=True면 카테고리별 값 + **공시 원문 전문**을 같이 싣는다.
        when: 중대재해, 산업재해, 산재 사망사고, 중대재해처벌법, 횡령, 배임, 생산중단, 영업정지, ESG 안전(S), 기업 리스크 모니터링(회사 미지정 시장 스캔), **관리종목 지정·지정우려, 상장적격성 실질심사, 개선기간, 상장폐지 우려, 매매거래정지·해제, 정리매매**.
        rule: DART list.json I001(거래소 주요경영사항)+B001(주요사항보고서)+I003(거래소 시장조치·안내) 채널 + 키워드 — 중대재해는 305사 3.5년 차집합 0 검증, 본문 파싱은 연속 2개 90일 윈도우 359건 전수 audit. 관리종목·거래정지 계열은 I003 에만 있다(2026-08-27 전수 실측). company 지정 시 24개월 / 미지정 시 30일(최대 90일). 주의: 중대재해 수시공시 신설 2025-10 — 이전 무공시 ≠ 무사고. 공시는 대형 원청·지주사 집중 — 비상장 자회사 사고는 상장 모회사가 공시.
        category: `serious_accident` 중대재해 / `embezzlement` 횡령·배임 / `production_halt` 생산중단·영업정지 / `trading_halt` 매매거래정지 / `listing_review` 관리종목·상장적격성·개선기간·상장폐지 / `litigation` 소송·벌금·과징금 / `capital_impairment` 자본잠식 — 미지정 시 7종 전체. 명시 요청 시에만 도는 것: `inquiry_disclosure` 조회공시·풍문해명 / `investment_judgment` 투자판단 주요경영사항 / `derivative_loss` / `rehabilitation` / `dissolution`.
        include_details: True면 원문을 읽어 값 + **원문 전문**을 싣는다 (DART 호출 N회 증가) + 중대재해 사상자 집계. 관리종목 지정의 **사유·유예기간(경과일수/남은 일수)·해소요건**은 정형 칸이 아니라 안내문 줄글에 있어 이 옵션 없이는 나오지 않는다.
        details_limit: 원문을 읽을 공시 건수 (기본 5, 최대 10).
        source_chars: 공시 한 건당 실을 원문 글자수 (기본 4000, 최대 20000). 원문이 잘렸다고 표시되면 올려서 다시 부를 것 — 거래소 안내문은 200~750자라 기본값으로 대개 전문이 들어온다.
        ref: corp_gov_report (지배구조 맥락), proxy_contest (소송·분쟁), evidence (원문 확인), financial_metrics·price_multiple_data (관리종목 사유가 재무·시가총액일 때 얼마나 모자라는지)
        """
        payload = await build_risk_events_payload(
            company,
            category=category,
            start_date=start_date,
            end_date=end_date,
            include_details=include_details,
            details_limit=max(1, min(details_limit, 10)),
            source_chars=source_chars,
        )
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") == "ambiguous":
            return _render_ambiguous(payload)
        if payload.get("status") == "error":
            return _render_error(payload)
        return _render(payload)
