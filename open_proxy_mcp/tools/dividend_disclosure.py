"""dividend public tool."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.tools._shared import krw_with_raw as _won
from open_proxy_mcp.tools._shared import company_id_line, raw_cell
from open_proxy_mcp.services.dividend import build_dividend_payload


# 추세 enum → 한글. producer 는 services/dividend.py `_policy_signals()` 하나뿐이다.
# 260729 커밋(ec98f31)이 조회(`_TREND_KO.get(...)`)만 넣고 사전을 빠뜨려 md 경로 전체가
# NameError 로 죽었다 — 렌더러를 한 번도 실행하지 않는 테스트만 있어 전부 통과했다.
_TREND_KO = {
    "increasing": "증가",
    "decreasing": "감소",
    "stable": "유지",
    # 「없음」이 아니라 「판단하지 않았다」 — 비교할 확정 연도가 없어 계산 자체를 못 한 상태다
    "insufficient_data": "판단 불가 (확정된 배당 이력 없음)",
}


def _render_error(payload: dict[str, Any]) -> str:
    lines = [f"# dividend: {payload.get('subject', '')}", "", "배당 데이터를 확정하지 못했다."]
    for warning in payload.get("warnings", []):
        lines.append(f"- {warning}")
    return "\n".join(lines)


def _render_ambiguous(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    lines = [
        f"# dividend: {data.get('query', payload.get('subject', ''))}",
        "",
        "회사 식별이 애매해 배당 데이터를 자동 선택하지 않았다.",
        "",
        "| 회사명 | ticker | corp_code | company_id |",
        "|------|--------|-----------|------------|",
    ]
    for item in data.get("candidates", []):
        lines.append(f"| {item['corp_name']} | `{item['ticker']}` | `{item['corp_code']}` | `{item['company_id']}` |")
    return "\n".join(lines)


def _fiscal_meta_line(summary: dict[str, Any], data: dict[str, Any]) -> str:
    """비12월 결산일 때만 사업연도 구간·결산월·FY 라벨 기준을 한 줄로 붙인다.
    12월 결산은 FY와 달력연도가 같아 군더더기다. provisional_earnings 의 표기를 따른다."""
    end_month = summary.get("fiscal_year_end_month") or data.get("fiscal_year_end_month")
    if not end_month or end_month == 12:
        return ""
    start, end = summary.get("period_start"), summary.get("period_end")
    span = f"사업연도 {start}~{end} · " if start and end else ""
    return f"_{span}{end_month}월 결산 · {data.get('fiscal_year_basis', '')}_"


def _decision_kind(item: dict[str, Any]) -> str:
    """「구분」 칸 — 배당구분에 정정 표시를 붙인다(261019).

    정정 공시는 값이 정정 후 본문에서 온다(`dividend_parser._split_amendment`). 금액을 「-」로 지운 정정은
    결정 취소라 지급이 아니다 — 합산·건수에서 빠지고 여기서만 보인다.
    """
    label = item.get("dividend_type") or "-"
    meta = item.get("amendment") or {}
    if meta.get("cancelled"):
        return f"{label} [정정·취소]"
    if meta:
        return f"{label} [정정]"
    return label


def _decision_remarks_lines(decisions: list[dict[str, Any]]) -> list[str]:
    """배당결정 원문 11번 「기타 투자판단과 관련한 중요사항」 — **결의마다 전문**을 낸다.

    🔴 파서는 이 칸을 이미 통째로 들고 있었다(`parse_dividend_decision()["remarks"]`).
    렌더러가 그걸 버리고 `has_special` 불리언만 내보내고 있었다 — 서식에 칸이 없는 사실
    (감액배당 재원·자기주식 제외 산정·주총 갈음·차등배당·「변동될 수 있음」 단서)이
    전부 이 칸에만 적히는데도 그랬다. 260903 마스터 지시로 원문을 그대로 낸다.
    표 셀이 아니라 인용 블록인 이유는 길이다 — 중앙값 245자·최대 1,512자.
    """
    rows = [(d, raw_cell(d.get("remarks"))) for d in decisions]
    if not any(text for _, text in rows):
        return []
    L = ["", "### 배당결정 비고 원문 (11. 기타 투자판단과 관련한 중요사항)", "",
         "> 요약하지 않은 공시 원문이다. 특별·기념배당, 감액배당 재원, 자기주식 제외 산정, "
         "주총 갈음, 차등배당, 감사·주총 과정의 변동 단서가 이 칸에만 적힌다. "
         "**읽고 판단하라** — 아래 파생 플래그는 힌트일 뿐이다.", ""]
    for d, text in rows:
        head = [d.get("rcept_dt") or "-"]
        if d.get("dividend_type"):
            head.append(str(d["dividend_type"]))
        if d.get("dps_common"):
            head.append(f"DPS {d['dps_common']:,}원")
        if d.get("differential_dividend"):
            head.append("차등배당 해당")
        if d.get("has_special"):
            head.append("특별배당 힌트")
        L.append(f"- **{' · '.join(head)}** `{d.get('rcept_no', '')}`")
        L.append(f"  > {text}" if text else
                 "  > _(비고 칸이 비어 있다 — 「특이사항 없음」이 아니라 원문에 적힌 것이 "
                 "없다는 뜻이다. `evidence` 로 원문을 확인하라.)_")
    return L


def _alot_items_lines(summary: dict[str, Any]) -> list[str]:
    """사업보고서 `alotMatter` **행 원문**. 항목명·주식종류·당기/전기/전전기를 손대지 않고 낸다.

    🔴 위 「연간 요약」은 이 행들을 키워드로 골라 만든 파생값이다(`build_dividend_summary`).
    「주당 현금배당금」·「현금배당성향」 같은 문구가 서식마다 달라 골라내다 빠뜨리는 항목이
    생기고(특별배당·주식배당·액면가·순이익 행), 고른 값도 종류주식 표기 50여 종에서 갈린다.
    그래서 **고른 값과 원문 행을 나란히** 낸다 — 어긋나면 원문이 정본이다.
    """
    items = summary.get("items") or []
    if not items:
        return []
    # 261021: 결산기가 둘 이상 섞인 응답(반기·분기 결산 리츠)은 같은 항목 줄이 결산기마다 반복된다 — 결산기준일 칸이 없으면 줄이 중복처럼 보이고,
    #   당기·전기·전전기가 **연속한 결산기**(연 단위가 아니다)라는 것도 읽히지 않는다. 결산기가 하나면 종전 표 그대로다.
    _multi = len({"".join(ch for ch in str(it.get("stlm_dt") or "") if ch.isdigit()) for it in items} - {""}) > 1
    L = ["", "### 사업보고서 배당 항목 원문 (`alotMatter`)", ""]
    if _multi:
        L += ["| 결산기준일 | 항목(원문) | 주식종류 | 당기 | 전기 | 전전기 |", "|---|---|---|---|---|---|"]
    else:
        L += ["| 항목(원문) | 주식종류 | 당기 | 전기 | 전전기 |", "|---|---|---|---|---|"]
    for it in items:
        L.append(
            ("" if not _multi else f"| {raw_cell(it.get('stlm_dt'), inline=True) or '-'} ")
            + f"| {raw_cell(it.get('category'), inline=True) or '-'} "
            f"| {raw_cell(it.get('stock_type'), inline=True) or '-'} "
            f"| {raw_cell(it.get('current'), inline=True) or '-'} "
            f"| {raw_cell(it.get('previous'), inline=True) or '-'} "
            f"| {raw_cell(it.get('before_previous'), inline=True) or '-'} |")
    if _multi:
        L += ["", "> 결산기가 여럿인 회사(반기·분기 결산)는 한 행의 당기·전기·전전기가 **연속한 결산기**다(연 단위가 아니다) — "
              "행의 결산기준일에서 결산 주기만큼씩 거슬러 읽는다."]
    L += ["", "> 위 「연간 요약」 숫자는 이 행들에서 골라낸 파생값이다. "
          "**어긋나면 이 표가 정본이다.** 단위·기준(연결/별도)도 항목명에 적힌 그대로다."]
    return L


def _quarterly_full_lines(qf: list[dict[str, Any]], qb: list[dict[str, Any]], year: int | None) -> list[str]:
    """최신연도 분기별(정기보고서 누적차분) 표. 보고서 행이 비어 차분이 두 분기를 덮은 칸(`merged`)은 한 분기로 적지 않고 합산이라고 표시하며,
    같은 분기들의 결정공시(아래 표)와 맞춰 보는 줄을 붙인다(B4, DATA-DIV8)."""
    # 우선주 칸은 우선주만 — 우선주 아닌 종류주식(「1종 종류주식」 …)이 있는 회사는 원문 종류 이름을 머리줄로 한 칸을 더한다(DATA-EQ4).
    class_labels = list(dict.fromkeys(x["dps_class_label"] for x in qf if x.get("dps_class_label") and x.get("dps_class")))
    has_class = bool(class_labels)
    head = "| 분기 | 보통주 DPS | 우선주 DPS | " + (f"{'·'.join(class_labels)} DPS | " if has_class else "") + "배당총액 |"
    lines = ["", "## 최신연도 분기별 (정기보고서 누적차분)", head, "|" + "---|" * (head.count("|") - 1)]
    for x in qf:
        pref = f"{x['dps_preferred']:,}원" if x.get("dps_preferred") else "-"
        klass = (f"{x['dps_class']:,}원" if x.get("dps_class") else "-") if has_class else None
        cells = [str(x["quarter"]), f"{x['dps_common']:,}원", pref] + ([klass] if has_class else []) + [_won(x["total_mil"] * 1_000_000)]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("> 분기/반기/사업보고서 누적값을 차분 — 결정공시 귀속 추측이 아니라 권위 출처. 무배당 분기는 0.")
    live = [r for r in qb if r.get("year") == year and not r.get("is_superseded") and not r.get("is_cancelled")]
    for x in qf:
        if not x.get("merged"):
            continue
        miss = "·".join(x.get("missing_reports") or []) or "보고서"
        lines.append(f"> 「{x['quarter']}」 칸은 {miss}의 누계가 없어 앞뒤 누계의 차이가 **{len(x.get('span') or [])}개 분기분**이다 — 한 분기 값이 아니다.")
        parts = []
        for q in x.get("span") or []:
            hit = [r for r in live if str(r.get("quarter", "")).split()[0] == q]
            if not hit:
                parts = []
                break
            parts.append((q, sum(int(r.get("dps_common_krw") or 0) for r in hit)))
        if parts:
            total = sum(v for _q, v in parts)
            verdict = "일치" if total == x["dps_common"] else f"차이 {x['dps_common'] - total:+,}원"
            lines.append(f"> 대응: 결정공시 표의 {' + '.join(f'{q} {v:,}원' for q, v in parts)} = {total:,}원 ({verdict}).")
    return lines


def _render(payload: dict[str, Any], scope: str) -> str:
    data = payload.get("data", {})
    summary = data.get("summary", {})
    window = data.get("window", {})
    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} 배당", ""]
    _cid = company_id_line(data)
    if _cid:
        lines.append(_cid)
    if window:
        lines.append(f"- 조사 구간: `{window.get('start_date', '')}` ~ `{window.get('end_date', '')}`")
    lines.append("")
    if payload.get("warnings"):
        lines.append("## 유의사항")
        for warning in payload["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    if summary:
        # 몇 년치인지 없으면 리포트에 인용할 때 위험하다(260728 QA 지적)
        _fy = summary.get("fiscal_year") or summary.get("year") or data.get("year")
        lines.append(f"## 연간 요약" + (f" (FY{_fy})" if _fy else ""))
        # 비12월 결산은 같은 FY 라벨이 회사 IR 문서와 다른 12개월을 가리킨다 —
        # 구간·결산월·라벨 기준을 붙여야 어느 해 이야기인지 확정된다 (U 지적 B-6).
        _meta = _fiscal_meta_line(summary, data)
        if _meta:
            lines.append(_meta)
        # 주당값은 종류별로, 총액·배당성향은 회사 전체(전 종류 합·연결) — 기준을 라벨에 적는다.
        #   그 밖 종류의 라벨은 원문 표기(우선주·1종 종류주식 …)를 그대로 쓴다.
        _pref_label = summary.get("cash_dps_preferred_label") or "우선주"
        lines.append(f"- 연간 DPS(보통주): {summary.get('cash_dps', 0):,}원")
        # 261021: 반기·분기 결산 회사 — 연간 DPS 는 그 해 결산기들의 합이다. 어느 결산기가 얼마인지 보여 준다.
        _sp = summary.get("settlement_periods") or []
        if len(_sp) > 1:
            _cad = {3: "분기", 6: "반기"}.get(summary.get("settlement_months"), "")
            _latest = summary.get("latest_period") or {}
            lines.append(
                f"  - {_cad + ' ' if _cad else ''}결산기 {len(_sp)}개의 합: "
                + " + ".join(f"{p.get('stlm_dt')} {p.get('dps_krw', 0):,}원" for p in _sp)
                + (f" _(사업보고서 한 통은 가장 늦은 결산기 {_latest.get('stlm_dt')} 분 {int(_latest.get('cash_dps') or 0):,}원)_"
                   if _latest.get("cash_dps") else ""))
        if summary.get("cash_dps_preferred"):
            lines.append(f"- 연간 DPS({_pref_label}): {summary.get('cash_dps_preferred', 0):,}원")
        lines.append(f"- 배당총액: {_won(summary.get('total_amount_mil', 0) * 1_000_000)} _(전 종류 합산 신고총액)_")
        # 261021: 결산기 합 요약(반기·분기 결산)의 성향·시가배당률은 공시 원문 한 칸이 아니다 — 결산기마다 따로 적힌 값이라 어떻게 만들었는지 라벨에 적는다.
        _period_sum = summary.get("dps_basis") == "sum_of_settlement_periods"
        if summary.get("payout_ratio_dart") is not None:
            if _period_sum:
                lines.append(f"- 배당성향: {summary.get('payout_ratio_dart')}% _(결산기 배당총액 합 ÷ 결산기 `(연결)당기순이익` 합 — "
                             "공시 원문 성향은 결산기마다 따로 적혀 더할 수 없어 **우리가 계산했다**)_")
            else:
                lines.append(f"- 배당성향: {summary.get('payout_ratio_dart')}% _(공시 원문 `(연결)현금배당성향`. 연결·전 종류 합산 기준이며 우리가 계산한 값이 아니다)_")
        if summary.get("yield_dart") is not None:
            _y = f"- 시가배당률(보통주): {summary.get('yield_dart')}%"
            if summary.get("yield_preferred_dart") is not None:
                _y += f" · ({_pref_label}) {summary.get('yield_preferred_dart')}%"
            lines.append(_y + (" (결산기마다 결의 당시 공시값의 합)" if _period_sum else " (결의 당시 공시값)"))
        if summary.get("yield_current_pct") is not None:
            # 261019(p1b): 결산기준일 뒤 분할·병합이면 DPS 를 지금 주식 기준으로 바꿔 나눴다 — 사업보고서 값과 계수를 함께 적는다.
            _adj = summary.get("yield_current_dps_adjustment") or {}
            _dps_txt = (f"DPS {summary.get('cash_dps', 0):,}원 × 계수 {_adj.get('factor'):g}"
                        f"({' · '.join(e.get('event_dd', '') for e in _adj.get('events') or [])} 주식 분할·병합 뒤) = "
                        f"{summary.get('yield_current_dps_krw'):,}원" if _adj and summary.get("yield_current_dps_krw") is not None
                        else f"DPS {summary.get('cash_dps', 0):,}원")
            lines.append(
                f"- 현재가 기준 배당수익률: {summary.get('yield_current_pct')}% "
                f"({_dps_txt} ÷ 종가 {summary.get('yield_current_price_krw', 0):,}원, "
                f"{summary.get('yield_current_price_date', '-')} 기준)")
        # 신호 메타 — 선배당-후결의, 감액배당.
        if summary.get("pre_dividend_post_resolution"):
            lines.append("- 선배당-후결의 (2024 신법): 채택 (배당기준일결정 별도 공시 확인)")
        elif "pre_dividend_post_resolution" in summary:
            lines.append("- 선배당-후결의 (2024 신법): 미채택 추정")
        if summary.get("capital_reserve_reduction"):
            lines.append("- 감액배당 cross-link: 자본준비금 감소 안건 주총 상정 (이익잉여금 전입 → 배당 재원)")

    if scope in {"summary", "detail"}:
        # 자리가 정해진 칸(날짜·구분·DPS·기준일)은 표로. 자유서술 칸(비고)은 아래 원문으로.
        # detail 은 서비스가 따로 50건을 담아 둔다(`data["detail"]["latest_decisions"]`).
        # 최상위 `latest_decisions` 는 두 스코프 공용이라 20건에서 잘려 있다.
        _detail = data.get("detail") or {}
        _decisions = (_detail.get("latest_decisions") if scope == "detail" else None) \
            or data.get("latest_decisions", [])
        _total = _detail.get("decision_count") or len(_decisions)
        _shown = _decisions[: (50 if scope == "detail" else 10)]
        lines.extend(["", "## 최근 배당결정", "| 공시일 | 구분 | DPS(보통) | 기준일 | 공시번호 |", "|--------|------|-----------|--------|----------|"])
        for item in _shown:
            lines.append(
                f"| {item.get('rcept_dt', '')} | {_decision_kind(item)} | {item.get('dps_common', 0):,}원 | "
                f"{item.get('record_date', '-') or '-'} | `{item.get('rcept_no', '')}` |"
            )
        if _total > len(_shown):
            tail = " 전부 보려면 `scope=\"detail\"`." if scope == "summary" else ""
            lines.append(f"> 이 구간 결정공시 {_total}건 중 최근 {len(_shown)}건.{tail}")
        lines.extend(_decision_remarks_lines(_shown))

    if scope == "detail":
        lines.extend(_alot_items_lines(summary))

    if scope == "summary":
        policy = data.get("policy_signals", {})
        lines.extend([
            "",
            "## 정책 신호 (원문에서 뽑은 파생 힌트)",
            "> 🔴 **정본이 아니다.** 아래 넷은 위 표·비고 원문에서 규칙으로 뽑은 요약이고, "
            "규칙은 서식 변형을 놓친다 — 특히 `특별배당 이력` 은 비고에 「특별배당」·「기념배당」이 "
            "박힌 경우만 참이다(코스피 전수 3,831건 중 2건). **아니오 = 없다가 아니다.** "
            "판단은 위 비고 원문을 읽고 하라.",
            f"- 추세: {_TREND_KO.get(policy.get('trend'), policy.get('trend') or '-')}",
            f"- 분기/중간배당 패턴: {'예' if policy.get('has_quarterly_pattern') else '아니오'}",
            f"- 특별배당 이력: {'예' if policy.get('has_special_dividend') else '아니오'}",
            f"- 최근 DPS 변화율: {str(policy.get('latest_change_pct')) + '%' if policy.get('latest_change_pct') is not None else '-'}",
        ])

    if scope == "history":
        _em = data.get("fiscal_year_end_month")
        _span_col = bool(_em) and _em != 12
        if _span_col:
            lines.extend(["", "## 최근 연도 추이", f"_{_em}월 결산 · {data.get('fiscal_year_basis', '')}_",
                          "| FY | 결산기간 | 연간 DPS(보통주) | 공시 수 | 배당성향 | 수익률(보통주) | 패턴 |",
                          "|----|----------|----------|--------|----------|--------|------|"])
        else:
            lines.extend(["", "## 최근 연도 추이", "| 연도 | 연간 DPS(보통주) | 공시 수 | 배당성향 | 수익률(보통주) | 패턴 |", "|------|----------|--------|----------|--------|------|"])
        for item in data.get("history", []):
            payout = f"{item['payout_ratio']}%" if item.get("payout_ratio") is not None else "-"
            yld = f"{item['yield_pct']}%" if item.get("yield_pct") is not None else "-"
            # 미결의 연도는 0원을 지급액처럼 보이게 두지 않는다.
            dps = "-" if item.get("pattern", "").startswith("미결의") else f"{item['annual_dps']:,}원"
            if _span_col:
                span = f"{item.get('period_start') or '?'}~{item.get('period_end') or '?'}"
                lines.append(f"| {item['year']} | {span} | {dps} | {item['decision_count']} | {payout} | {yld} | {item['pattern']} |")
            else:
                lines.append(f"| {item['year']} | {dps} | {item['decision_count']} | {payout} | {yld} | {item['pattern']} |")
        # 최신연도 분기별 — 정기보고서 누적차분(권위). 결정공시 버킷팅 오귀속·중복 없음, 무배당 분기 0 포함.
        qf = data.get("quarterly_full") or []
        if qf:
            lines.extend(_quarterly_full_lines(qf, data.get("quarterly_breakdown") or [], data.get("year")))
        # 결정공시별 breakdown — 기준일·rcept_no 추적용 (정정 이력 포함).
        qb = data.get("quarterly_breakdown") or []
        if qb:
            lines.extend(["", "## 분기별 결정공시 (공시별·추적용)", "| 연도 | 분기 | 보통주 DPS | 우선주 DPS | 기준일 | 공시번호 |", "|------|------|------------|------------|--------|------------------|"])
            for r in qb:
                amend = " [정정]" if r.get("is_amendment") else ""
                supersed = " (대체됨)" if r.get("is_superseded") else ""
                supersed += " (취소)" if r.get("is_cancelled") else ""
                lines.append(f"| {r['year']} | {r['quarter']}{amend}{supersed} | {r['dps_common_krw']:,}원 | {r['dps_preferred_krw']:,}원 | {r.get('record_date','-')} | `{r.get('rcept_no','-')}` |")
            lines.append("")
            lines.append("> 최신연도 정확값은 위 '누적차분' 표 참조. 이 표는 공시 추적용(기준일·공시번호·정정 이력).")

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def dividend_disclosure(
        company: str,
        scope: str = "summary",
        year: int = 0,
        years: int = 3,
        start_date: str = "",
        end_date: str = "",
        format: str = "md",
    ) -> str:
        """desc: 실지급·확정된 배당 **사실**. DPS, 총액, 배당성향, 시가배당률(결의 당시)+**현재가 기준 배당수익률**(최신 거래일 종가, 일별 시세 저장분), 분기별 추이. 미래 정책·약속 X.
        when: 실제 지급된 배당 확인. 분기배당 회사는 `history`로 분기별 breakdown. 미래 정책/약속은 `value_up`.
        rule: source 2단 — (1) 사업보고서 alotMatter(공식값) (2) 현금ㆍ현물배당결정 합산(alotMatter 빈 경우 fallback). 결산배당은 record_date 기준 fiscal year bucket (선배당-후결의 신법). 정정공시는 정정 후 본문 값을 쓰고(앞 「정정사항」 표는 원 공시를 찾는 열쇠·정정 항목 메타 `amendment` 로만), 고친 앞 공시는 기준일이 바뀌어도 is_superseded, 금액을 지운 정정은 결정 취소(합산 제외). 미래 약속 추가 금지. 자유서술 칸은 **원문 전문**을 낸다 — 결정공시 비고(11번 「기타 투자판단과 관련한 중요사항」)는 결의마다, `alotMatter` 항목 행은 `detail` 에서. 특별·기념배당·감액배당 재원·자기주식 제외 산정·주총 갈음·차등배당은 그 칸에만 적히니 **파생 플래그(`정책 신호`)를 믿지 말고 원문을 읽어라.**
        scope: `summary` 선배당-후결의+감액배당 메타+최근 결정 10건(비고 원문 포함) / `detail` 요약+최근 결정 50건(비고 원문 포함)+`alotMatter` 항목 행 원문 / `history` N년 추이+분기 breakdown+policy_signals
        ref: value_up, treasury_share, shareholder_meeting_notice, company, ownership_structure, evidence
        """
        payload = await build_dividend_payload(
            company,
            scope=scope,
            year=year or None,
            years=years,
            start_date=start_date,
            end_date=end_date,
        )
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") == "ambiguous":
            return _render_ambiguous(payload)
        if payload.get("status") == "error":
            return _render_error(payload)
        return _render(payload, scope)
