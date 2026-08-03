"""asset_holdings — 회사 보유 자산(부동산·지분증권·현금성) + 시총 대비 청산가치(NAV) 스크리닝.

설계: 자산저평가주 전수조사 + 공시전문가↔밸류투자자 토론(260720). markdown-primary + 계정 API 구조화.
"""
from __future__ import annotations

from open_proxy_mcp.services.asset_holdings import build_asset_holdings_payload
from open_proxy_mcp.services.contracts import as_pretty_json


def _f(v) -> str:
    if not v:
        return "-"
    e = v / 1e8
    return f"{e / 1e4:,.2f}조" if abs(e) >= 1e4 else f"{e:,.0f}억"


def _render(p: dict) -> str:
    st = p.get("status")
    subj = p.get("subject", "")
    if st != "ok":
        return f"**{subj}** — {'; '.join(p.get('warnings') or ['조회 실패'])}"
    d = p.get("data", {})
    L = [f"## {subj} — 보유 자산  ({d.get('report_nm', '')}, {d.get('fs_div', '')})"]

    story = d.get("asset_story")
    if story:
        L.append(f"\n> 🏷 **{story}**")

    buckets = d.get("asset_buckets") or {}
    if buckets:
        L.append("\n### 무엇을 들고 있나 (목적별)")
        L.append("| 구분 | 규모 | 설명 |")
        L.append("|---|--:|---|")
        for label, info in buckets.items():
            L.append(f"| {label} | {_f(info['krw'])} | {info['desc']} |")

    assets = d.get("assets") or {}
    if assets:
        L.append("\n### 세부 계정 (감사 연결 BS, 단위 억원)")
        L.append("| 항목 | 장부금액 |")
        L.append("|---|--:|")
        for k, v in assets.items():
            L.append(f"| {k} | {_f(v)} |")

    if d.get("scope") == "summary":
        nav = d.get("nav") or {}
        mcap = d.get("market_cap_krw")
        m = d.get("listed_stakes") or {}
        L.append(f"\n### 시총 대비 (시총 {_f(mcap)})")
        if d.get("is_financial"):
            L.append("> ⚠ **금융업** — 투자자산이 본업(운용자산)이라 저평가 신호로 직접 해석 주의(금융 리그).")
        if d.get("is_reit"):
            L.append("> ⚠ **REIT 추정(사명 기준)** — 투자부동산이 본업이라 잉여자산에서 제외(저평가 신호 아님).")
        sc = nav.get("surplus_cov")
        nc = nav.get("equity_nav_cov")
        surplus_str = _f(nav.get("surplus_krw"))
        surplus_label = "**잉여자산**(현금성" + ("" if d.get("is_financial") else "·환금·투자부동산") + ")"
        if sc:
            L.append(f"- {surplus_label} {surplus_str} → 시총 대비 **{sc:.2f}배**"
                     + ("  ← 시총 초과!" if sc > 1 else ""))
        elif d.get("is_financial"):
            L.append(f"- {surplus_label} {surplus_str} (금융업이라 배수 미제공, 절대액만 참고)")
        else:
            L.append(f"- {surplus_label} {surplus_str} (시총 없어 배수 미산출)")
        if nc:
            L.append(f"- **지분 NAV**(지배·전략지분, 상장분 시가마크) {_f(nav.get('equity_nav_krw'))}"
                     f" → 시총 대비 **{nc:.2f}배**")
        mc = nav.get("mixed_combined_krw")
        if mc:
            mcv = nav.get("mixed_combined_cov")
            L.append(f"- ⚠ **결합계정(종속+관계/공동, 미분리)** {_f(mc)}"
                     + (f" → 시총 대비 {mcv:.2f}배" if mcv else "")
                     + " — 지배지분 섞여있어 위 지분NAV엔 미포함(지분법원가 기준, 참고용만)")
        if m.get("n_marked"):
            L.append(f"- 상장 보유지분 시가마크 {m['n_marked']}/{m['n_listed']}종 · "
                     f"장부 대비 미실현 **{_f(m['unrealized_gap_krw'])}**"
                     + (f" (미해결 {m['n_unresolved']}종은 장부가 유지)" if m.get("n_unresolved") else ""))
            for h in (m.get("marked") or [])[:5]:
                L.append(f"    - {h['name']}: 장부 {_f(h['book_krw'])} → 시가 {_f(h['mkt_krw'])}"
                         f" ({'+' if h['gap_krw'] >= 0 else ''}{_f(h['gap_krw'])})")
        hf = nav.get("haircut_flags") or []
        if hf:
            names = {"pledged": "담보제공 자산", "contingent": "우발부채·지급보증"}
            L.append(f"- ⚠ **NAV 차감 필요**: {', '.join(names.get(x, x) for x in hf)} 존재 "
                     f"→ `scope=\"detail\"`로 원문 확인 (담보 잡힌 자산·부외 부채는 자유청산 NAV에서 빼야 정확)")
        L.append("\n> 배수는 **부채 미차감 gross** — PBR과 병용하세요. 담보·우발 haircut 반영은 caller 판단.")

    if d.get("scope") == "detail":
        L.append("\n> III.재무 주석 원문 — 값·단위는 회사별 상이(직접 읽어 판단).")
        for key, title in (("real_estate", "토지·투자부동산 (원가 vs 공정가치)"),
                           ("equity_holdings", "지분증권 명세"),
                           ("pledged_assets", "담보제공 자산"), ("contingent", "우발부채·지급보증")):
            fd = d.get(key) or {}
            if fd.get("status") == "MARKDOWN":
                # 어느 기준의 표인지 **표보다 먼저** 밝힌다 — 연결·별도를 섞어 읽으면
                # NAV 자체가 달라진다.
                if fd.get("basis"):
                    L.append(f"_{fd['basis']} 재무제표 주석 기준_")
                if fd.get("basis_conflict"):
                    L.append(f"> {fd['basis_conflict']}")
                L.append(f"\n{fd['markdown']}")
                if fd.get("source_excerpt"):
                    L.append(f"_원문 위치: …{fd['source_excerpt']}…_")
            else:
                # 「해당없음」 한 갈래로 내보내면 원문에 없는 건지 우리가 못 읽은 건지 알 수 없다.
                head = {"extraction_failed": "찾지 못함 — 원문에 표가 있습니다",
                        "narrative_only": "표 없음 — 산문 서술만",
                        "cross_reference": "여기엔 없음 — 원문이 다른 절을 가리킵니다",
                        "not_disclosed": "해당 없음"}.get(fd.get("absence_kind"), "해당없음")
                L.append(f"\n**{title}**: {head} — "
                         f"{fd.get('absence_note') or fd.get('na_reason', '')}")
                if fd.get("absence_excerpt"):
                    L.append(f"_원문 위치: …{fd['absence_excerpt']}…_")

    if p.get("warnings"):
        L.append("\n⚠ " + " · ".join(p["warnings"]))
    return "\n".join(L)


def register_tools(mcp):

    @mcp.tool()
    async def asset_holdings(company: str, scope: str = "summary", format: str = "md") -> str:
        """desc: 회사가 **보유한 자산**(투자부동산·지분증권·현금성·관계기업 지분)을 감사 연결재무제표 계정에서 뽑고, **시총 대비 청산가치(NAV) 커버리지**로 자산저평가주를 스크리닝. "시총보다 보유 자산이 값진가"에 답합니다.
        when: 자산주·NAV·청산가치·지주사 할인·숨은 부동산/지분 분석. "이 회사 자산 뭐 있어? 자산 대비 싸?"(summary) · 원문 명세로 직접 확인(detail). 전사 밸류(PER/PBR)는 `valuation`, 재무비율은 `financial_metrics`.
        scope: **`summary`(기본)** — 자산을 목적버킷(현금성/환금성증권·재테크형/우호제휴지분/지배관계사지분/투자용부동산/본업자산)으로 분류해 "재테크형·부동산 자산주형·지주사 할인형·우호지분형" 한 줄 서사(`asset_story`) + 세부 계정 + **상장 보유지분 시가마크** + 담보·우발 haircut 플래그 + 시총 대비 배수(잉여자산/지분NAV, 금융업은 미제공) / **`detail`** — III.주석 원문 markdown(토지 원가vs공정가치·지분증권·담보·우발) — summary에서 haircut 플래그가 뜨거나 숫자 원문을 직접 확인하고 싶을 때.
        rule: 구조화(계정·타법인출자)는 숫자, 주석(토지gap·담보·우발)은 **원문 markdown 반환→caller가 읽어 판단**. 시가마크는 상장 보유지분만(비상장은 장부가). **배수는 부채 미차감 gross → PBR 병용**. 담보·우발은 NAV에서 차감해야 정확(detail로 확인). **금융업(KSIC 64/65/66)은 투자자산=본업**이라 금융 리그 라벨.
        ref: valuation, financial_metrics, business_details, treasury_share
        """
        payload = await build_asset_holdings_payload(company, scope=scope)
        if format == "json":
            return as_pretty_json(payload)
        return _render(payload)
