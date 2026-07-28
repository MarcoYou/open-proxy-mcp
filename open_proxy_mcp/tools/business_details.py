"""business_details — DART "II. 사업의 내용" 사업부문 데이터 추출.

정형 파서 primary → 저신뢰 시 부문표 후보 raw 반환(호출측 LLM 추출) → N/A. 내부 LLM/pandas 없음.
설계: wiki/decisions/260717_1220_decision_business-content-tool-roadmap.md
"""
from __future__ import annotations

from open_proxy_mcp.services.business_details import build_business_details_payload
from open_proxy_mcp.services.contracts import as_pretty_json


def _fmt(v):
    if v is None:
        return "-"
    try:
        return f"{v:,.0f}"
    except (TypeError, ValueError):
        return str(v)


_AXIS_KO = {"by_segment": "부문별", "by_product": "제품별"}
# 「실패」라는 말은 쓰지 않는다 — 대부분은 오류가 아니라 '공시에 없거나 읽을 수 없는 형태'다.
_ABSENT_KO = {"NOT_APPLICABLE": "해당 없음", "NOT_COLLECTED": "공시에 미기재"}


def _absent(node: dict, what: str) -> list[str]:
    st = node.get("status")
    reason = (node.get("na_reason") or node.get("note") or "").strip()
    return [f"\n{what}: {_ABSENT_KO.get(st, '확인 불가')}" + (f" — {reason}" if reason else "")]


def _seg_lines(seg: dict, h: str) -> list[str]:
    """영업부문(K-IFRS 1108) — 정형 → 주석 원문 → 표 후보 → 부재."""
    L, st = [], seg.get("status")
    if st == "OK":
        L.append(f"\n{h} 사업부문별 매출·이익  (단위: {seg.get('unit','')}, 출처: 정형파싱)")
        L.append("| 부문 | 매출 | 영업이익 |")
        L.append("|---|--:|--:|")
        for s in seg.get("items", []):
            L.append(f"| {s.get('name','')} | {_fmt(s.get('revenue'))} | {_fmt(s.get('profit'))} |")
        L.append(f"\n_{seg.get('reconciliation','')}_  "
                 f"(지표: {seg.get('revenue_metric','')}/{seg.get('profit_metric','')})")
    elif st == "NEEDS_REVIEW":
        md = seg.get("segment_note_md")
        if md:
            L.append(f"\n{h} 사업부문 원문 ({seg.get('region','영업부문 주석')}) — 아래에서 직접 추출")
            L.append(f"> {seg.get('note','')}")
            L.append("\n" + md)
        else:
            L.append(f"\n{h} 사업부문 표 후보 (정형 저신뢰 → 아래 원문 표에서 직접 추출)")
            L.append(f"> {seg.get('note','')}")
            for i, c in enumerate(seg.get("candidates", []), 1):
                L.append(f"\n**[후보 {i}]** (score {c.get('score')}, {c.get('rows')}×{c.get('cols')})"
                         f"\n```\n{c.get('rendered','')[:2500]}\n```")
    elif st == "UNSUPPORTED_FORM":
        L.append(f"\n사업부문별 이익: 이 업종은 다른 tool에서 제공 — {seg.get('na_reason','')}")
    else:
        L.extend(_absent(seg, "사업부문별 이익"))
    return L


def _mix_lines(node: dict, h: str) -> list[str]:
    """II-2-가 제품별 매출구성 — 감사 대상이 아니므로 값을 확정해 주지 않고 원문 + 자가검산만."""
    if not node.get("markdown"):
        return _absent(node, "제품별 매출구성")
    L = []
    if node.get("status") == "NEEDS_REVIEW":
        L.append(f"\n> {node.get('note') or '자동 판정을 보류했습니다.'} 원문을 그대로 싣습니다.")
    sc = node.get("self_check") or {}
    if sc:
        bits = [f"단위 {sc.get('unit') or '미상(원문 확인)'}"]
        if sc.get("pct_sum") is not None:
            bits.append(f"비율합 {sc['pct_sum']}%")
        if sc.get("tie_out"):
            bits.append(sc["tie_out"])
        if sc.get("scope_note"):
            bits.append(sc["scope_note"])
        L.append(f"_자가검산: {' · '.join(bits)}_")
    L.append("\n" + node["markdown"])
    if node.get("markdown_truncated"):
        L.append(f"\n_(원문 {node.get('markdown_full_chars')}자 중 앞부분만 — 나머지는 DART 원문 참조)_")
    return L


def _geo_lines(node: dict, h: str) -> list[str]:
    """지역별 수익 — 정형(검산 통과) 또는 원문 표. 종전엔 md 렌더가 아예 없어 안 보였다(260728)."""
    items = node.get("items") or []
    if items:
        L = [f"\n| 지역 | 매출 |", "|---|--:|"]
        L.extend(f"| {i.get('name','')} | {_fmt(i.get('revenue'))} |" for i in items)
        L.append(f"\n_단위 {node.get('unit','')} · {node.get('reconciliation','')} "
                 f"· 지표 {node.get('revenue_metric','')}_")
        if node.get("basis_caption"):
            L.append(f"_기준: {node['basis_caption']}_")
        return L
    if node.get("markdown"):
        return [f"\n> {node.get('note','정형 검산을 통과하지 못해 원문 표를 그대로 싣습니다.')}",
                "\n" + node["markdown"]]
    return _absent(node, "지역별 수익")


def _render(p: dict) -> str:
    status = p.get("status")
    d = p.get("data", {}) or {}
    subj = p.get("subject", "")
    if status in ("error", "ambiguous"):
        return f"**{subj}** — {'; '.join(p.get('warnings') or ['회사 식별 실패'])}"
    if status == "no_filing":
        return f"**{subj}** — {'; '.join(p.get('warnings') or ['정기보고서 없음'])}"
    L = []
    rep = d.get("report", {})
    L.append(f"## {subj} — 사업부문 상세  ({rep.get('report_nm','')}, {d.get('form_type','')})")

    _seg_head = _seg_lines(d.get("segments"), "###") if d.get("segments") else []
    L.extend(_seg_head)

    # 매출 분해 — 부문/제품/지역 세 축을 한 절에 두되 출처 라벨로 칸막이를 유지한다.
    rb = d.get("revenue_breakdown")
    if rb:
        # 제목의 축 목록은 _AXIS_KO 에서 파생 — 축을 더하거나 뺄 때 문자열이 어긋나지 않게.
        L.append(f"\n## 매출 분해 ({' · '.join(_AXIS_KO.values())})")
        L.append(f"> {rb.get('guidance','')}")
        _ko = lambda xs: ", ".join(_AXIS_KO.get(a, a) for a in xs)
        avail, review = rb.get("available") or [], rb.get("needs_review") or []
        L.append(f"> 값이 나온 축: **{_ko(avail) or '없음'}**"
                 + (f" · 원문만 있는 축(검토필요): **{_ko(review)}**" if review else ""))
        for axis, fn in (("by_segment", _seg_lines), ("by_product", _mix_lines)):
            node = rb.get(axis)
            if not node:
                continue
            L.append(f"\n### [{_AXIS_KO[axis]}]")
            L.append(f"_출처: {node.get('source','')}_")
            L.extend(fn(node, "####"))

    geo = d.get("geo_revenue")
    if geo:
        L.append("\n### 지역별 수익  (III 주석 · 전사 차원 공시)")
        L.extend(_geo_lines(geo, "####"))

    # 추가 필드(markdown-primary): 소절 원문 마크다운 → 읽어서 추출. hint는 참고용.
    _FIELD_LABEL = {"sites": "사업장·생산설비", "utilization": "생산실적·가동률",
                    "rnd": "연구개발", "backlog": "수주현황", "customers": "주요 고객·매출처",
                    "raw_materials": "원재료·투입원가", "product_pricing": "제품·서비스 가격 추이",
                    "financial_ops": "영업의 현황(금융)", "financial_soundness": "재무건전성(금융)",
                    "investment_property": "투자부동산(REIT/보험)",
                    # 회계 부문이 아님을 이름에서부터 구분한다 — '부문'·'제품별 수익' 어휘 금지
                    "revenue_mix_form": "매출구성(공시서식 II-2-가 · 회계 부문 아님)",
                    "key_contracts": "주요계약(라이선스·기술도입·장기공급)"}
    for key in ("sites", "utilization", "rnd", "backlog", "customers",
                "raw_materials", "product_pricing", "revenue_mix_form", "key_contracts",
                "financial_ops", "financial_soundness", "investment_property"):
        fd = d.get(key)
        if not fd:
            continue
        label = _FIELD_LABEL[key]
        if fd.get("status") == "MARKDOWN":
            hint = ""
            if fd.get("pct_hint"):
                hint = f" _(가동률 힌트 {', '.join(fd['pct_hint'])}% · 비교금지)_"
            elif fd.get("ratio_to_sales_pct_hint"):
                hint = f" _(연구개발비/매출 힌트 {fd['ratio_to_sales_pct_hint']}%)_"
            elif fd.get("note"):
                hint = f" _({fd['note']})_"
            L.append(f"\n### {label} (원문){hint}")
            L.append("> 아래 원문에서 값을 읽으세요. 단위·정의는 회사별 상이(비교 주의).")
            if key == "revenue_mix_form":
                # 이 표는 감사받은 주석이 아니다. 실측 33%가 연결매출과 안 맞고,
                # 비율 분모가 내부거래 포함 단순합계인 회사가 있다.
                L.append("> 제품별 매출 구분은 K-IFRS 기준과 다를 수 있습니다.")
            L.append("\n" + fd["markdown"])
        elif fd.get("status") == "NEEDS_REVIEW":
            # 「해당없음」이 아니다 — 절은 찾았고 값만 못 믿는 것이다. 원문을 버리면 안 된다.
            L.append(f"\n### {label} (원문 · 검토필요)")
            L.append(f"> {fd.get('note') or '자동 판정을 보류했습니다.'} 원문을 그대로 싣습니다.")
            if fd.get("markdown"):
                L.append("\n" + fd["markdown"])
        elif fd.get("extraction_status") == "NOT_COLLECTED":
            L.append(f"\n**{label}**: 확인하지 못함 — {fd.get('na_reason','해당 소절 미검출')}")
        else:
            reason = (fd.get("na_reason") or "").strip()
            L.append(f"\n**{label}**: 해당없음" + (f" — {reason}" if reason else ""))

    candidate = d.get("candidate_context")
    if candidate:
        if candidate.get("status") == "LOW_CONFIDENCE":
            L.append(f"\n### 저신뢰 보조 문맥 — {candidate.get('field','')}")
            L.append(f"> {candidate.get('warning','')}")
            L.append(f"> 앵커: {candidate.get('anchor','')} · 고정 문맥: {candidate.get('context_chars','')}자")
            L.append("\n" + candidate.get("markdown", ""))
        elif candidate.get("status") == "NOT_FOUND":
            L.append(f"\n**저신뢰 보조 문맥**: 찾지 못함 — {candidate.get('warning','')}")

    tm = d.get("timings_ms", {})
    if tm:
        L.append(f"\n_조회 {tm.get('total','?')}ms · 주석fetch={d.get('note_fetched')}_")
    if p.get("warnings"):
        L.append("\n⚠ " + " · ".join(p["warnings"]))
    return "\n".join(L)


def register_tools(mcp):

    @mcp.tool()
    async def business_details(
        company: str,
        period: str = "latest",
        fields: str = "",
        format: str = "md",
        bsns_year: str = "",
        reprt_code: str = "",
        context_mode: str = "strict",
        context_chars: int = 20000,
    ) -> str:
        """desc: DART 정기보고서 **"II. 사업의 내용"**에서 사업부문별 매출·영업이익, **사업장·생산설비, 생산실적·가동률, 연구개발, 수주현황, 주요 고객·매출처, 원재료·투입원가, 제품·서비스 가격 추이**를 추출. SOTP·부문 수익성·생산능력·수주·고객집중·마진 분석의 1차 소스.
        when: 회사의 사업부문·생산·수주·고객 구조가 필요할 때. 전사 재무는 `financial_metrics`, 밸류는 `valuation`. **금융/증권/보험/지주는 `financial_ops`·`financial_soundness`, REIT/보험은 `investment_property`** 로 커버(segments 대신). **여러 분기/연도 추이**가 필요하면 `bsns_year`+`reprt_code`를 지정해 과거 시점을 하나씩 반복 호출.
        rule: segments는 정형→저신뢰 시 원문 마크다운. 나머지 필드는 **해당 소절 원문을 마크다운으로 반환** — 그 표를 읽어 값 추출(단위·정의 회사별 상이, 비교 주의). `context_mode=candidate`는 strict가 `NOT_COLLECTED`일 때만 **저신뢰 고정 윈도우 문맥**을 별도 `candidate_context`로 반환하며, 공식 결과·hint로 사용하면 안 됨. 이 모드는 표준 필드 하나를 지정할 때만 사용. 금융/REIT 필드는 표준사에선 자동 N/A. 유형자산 장부가 표를 사업장으로 오독 금지. **응답 `report.report_nm`으로 어느 보고서인지 확인**(분기/반기/사업). `bsns_year`/`reprt_code`는 **반드시 둘 다** 지정(하나만 주면 에러) — 지정 시 `period`는 무시됨.
        period: `latest`(기본, 사업·반기·분기 중 **가장 최신 제출분**=최신 데이터) / `annual`(연간 사업보고서 고정) / `quarterly`(분기·반기 고정). II.사업의내용은 분기/반기도 완전구조라 동일 필드. `bsns_year`+`reprt_code` 지정 시 이 파라미터는 무시.
        fields: 쉼표구분 — 표준: `revenue_breakdown,sites,utilization,rnd,backlog,customers,raw_materials,product_pricing,geo_revenue,key_contracts`. **`revenue_breakdown`이 매출 분해의 단일 진입점** — 안에 `by_segment`(III 주석 K-IFRS 1108 영업부문, 외부감사 대상, 매출+영업이익)와 `by_product`(II-2-가 공시서식 기재사항, 외부감사 아님, 매출만) 두 축이 출처 라벨과 함께 들어 있고 `available`/`needs_review`로 어느 축에 값이 있는지 알려준다. **두 축을 더하거나 곱하지 말 것**(같은 매출을 다르게 자른 것). 단일 영업부문 회사도 `by_product`엔 제품 구성이 있다(HD현대일렉트릭: 전력기기 69.5%). 옛 이름 `segments`·`revenue_mix_form`을 fields로 직접 주면 종전대로 평평하게 반환(별칭) / 금융·REIT: `financial_ops,financial_soundness,investment_property`. `raw_materials`는 원재료 구성·매입과 원재료 가격 추이를 별도 소절로 반환하고, `product_pricing`은 판매가격·ASP·가격변동 원인을 반환. **`self_check`(단위·비율합·합계행 대조)로 by_product 의 자기정합성을 먼저 볼 것.** `key_contracts`는 II-6-가 라이선스·기술도입·장기공급 계약(연구개발은 `rnd`). (미지정 시 회사에 맞는 표준·금융 필드만). **자산(토지·투자부동산·지분증권 원가vs공정가치)은 별도 tool `asset_holdings`.**
        bsns_year: 특정 과거 사업연도 조회(예: "2025"). `reprt_code`와 함께 지정해야 함 — **추이 조회용**(한 번에 여러 분기 반환 아님, 분기마다 반복 호출).
        reprt_code: DART 표준 보고서유형 — `11011`(사업/연간) `11012`(반기) `11013`(1분기) `11014`(3분기). `bsns_year`와 함께 지정.
        context_mode: `strict`(기본) / `candidate`. candidate는 strict `NOT_COLLECTED`일 때만 단일 표준 필드의 저신뢰 보조 문맥을 별도 반환.
        context_chars: candidate 고정 문맥 길이(기본 20000, 최대 60000). strict에서는 사용하지 않음.
        ref: financial_metrics, valuation, order_contracts, company
        """
        flist = [f.strip() for f in fields.split(",") if f.strip()] or None
        payload = await build_business_details_payload(
            company, period=period, fields=flist, bsns_year=bsns_year, reprt_code=reprt_code,
            context_mode=context_mode, context_chars=context_chars,
        )
        if format == "json":
            return as_pretty_json(payload)
        return _render(payload)
