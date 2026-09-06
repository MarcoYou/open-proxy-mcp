"""business_details — DART "II. 사업의 내용" 사업부문 데이터 추출.

정형 파서 primary → 저신뢰 시 부문표 후보 raw 반환(호출측 LLM 추출) → N/A. 내부 LLM/pandas 없음.
설계: wiki/decisions/260717_1220_decision_business-content-tool-roadmap.md
"""
from __future__ import annotations

import re

from open_proxy_mcp.services.business_details import build_business_details_payload
from open_proxy_mcp.services.contracts import as_pretty_json


def _fmt(v):
    if v is None:
        return "-"
    try:
        return f"{v:,.0f}"
    except (TypeError, ValueError):
        return str(v)


def _krw(v):
    """원 단위 금액을 사람이 읽는 단위로 — 지역별 표는 「백만원」 표 단위 그대로 쓰지만
    II 매출실적 합산값은 원 단위라 43,140,940,000,000 처럼 읽기 어렵다."""
    if v is None:
        return "-"
    try:
        a = abs(float(v))
    except (TypeError, ValueError):
        return str(v)
    if a >= 1e12:
        return f"{v/1e12:,.2f}조원"
    if a >= 1e8:
        return f"{v/1e8:,.0f}억원"
    return f"{v:,.0f}원"


# 원문 표가 쓰는 단위 → 원 환산 배수. 공시 표는 회사마다 단위가 다르다(천원·백만원이 대부분).
_UNIT_MULT = {"원": 1, "천원": 1e3, "만원": 1e4, "백만원": 1e6,
              "억원": 1e8, "십억원": 1e9, "조원": 1e12}


def _table_scale(values, unit: str):
    """표 **전체에 한 단위**를 골라 준다 — (라벨, 나눌 값, 소수자리).

    행마다 단위를 달리하면(한 줄은 조원, 다음 줄은 억원) 행끼리 눈으로 비교가 안 된다.
    그래서 공시 표처럼 최댓값 기준으로 단위 하나를 정해 전 행에 적용한다.
    단위를 모르면 환산하지 않는다 — 모르는 채로 곱하면 10³·10⁶ 배 틀린 값을 확신 있게 낸다.
    """
    mult = _UNIT_MULT.get((unit or "").strip())
    if not mult:
        return None, None, None
    nums = [abs(float(v)) * mult for v in values if isinstance(v, (int, float))]
    m = max(nums) if nums else 0
    if m >= 1e12:
        return "조원", 1e12 / mult, 2
    if m >= 1e8:
        return "억원", 1e8 / mult, 0
    if m >= 1e4:
        return "만원", 1e4 / mult, 0
    return "원", 1 / mult, 0


def _scaled(v, div, dec):
    if v is None or div is None:
        return _fmt(v)
    try:
        return f"{float(v)/div:,.{dec}f}"
    except (TypeError, ValueError):
        return _fmt(v)


_AXIS_KO = {"by_segment": "부문별", "by_product": "제품별",
            "by_region": "지역별", "by_trade": "수출/내수"}
# 폼 판별 결과는 내부 enum 이다 — 제목에 `dual`·`standard7` 을 그대로 찍으면 읽는 사람은 뜻을 모른다.
_FORM_KO = {"standard7": "일반(제조·서비스) 서식", "financial5": "금융업 서식",
            "reit": "부동산투자회사 서식", "dual": "제조·서비스 + 금융업 이중 서식"}
# 「실패」라는 말은 쓰지 않는다 — 대부분은 오류가 아니라 '공시에 없거나 읽을 수 없는 형태'다.
_ABSENT_KO = {"NOT_APPLICABLE": "해당 없음", "NOT_COLLECTED": "공시에 미기재",
              # 폼 게이트로 걸러진 것은 우리가 못 읽은 게 아니다 — 「확인 불가」로 내면 거짓말이 된다
              "UNSUPPORTED_FORM": "해당 없음"}


_CHAP_RE = re.compile(r"[IVXⅠ-Ⅹ]{1,4}\s*[.．]")


def _origin_lines(node: dict) -> list[str]:
    """이 표가 **회사별로 원문 어디**에서 나왔는지 한 줄로.

    회사마다 절 번호·제목이 다르므로(실측 101건에서 번호 26가지, 같은 회사도 연도가 바뀌면
    24→30 으로 밀림) 「III 주석」 같은 일반론으로는 원문을 못 찾는다. 그 회사의 그 절을
    적는다. 축마다 payload 모양이 달라(부문·지역=source_location / 제품=section_source)
    여기서 하나로 흡수한다 — 파서는 이미 알고 있었는데 렌더가 안 썼다.
    """
    loc = node.get("source_location") or {}
    if loc.get("note_section"):
        basis = f" · {loc['basis']} 기준" if loc.get("basis") else ""
        return [f"_원문 위치: {loc.get('chapter','')} → {loc['note_section']}{basis}_"]
    ss = node.get("section_source") or {}
    heads = [h for h in (ss.get("matched_headings") or []) if h]
    if heads:
        # 장 이름은 「II.」·「Ⅲ.」로 시작하는 것만 믿는다 — 표 안의 문장이 장 이름 자리에
        # 들어와 「C. 1주당 상환일까지…」 같은 게 그대로 나가는 일이 있다.
        chap = next((c for c in (ss.get("chapters") or [])
                     if c and _CHAP_RE.match(c.strip())), "II. 사업의 내용")
        return [f"_원문 위치: {chap} → {' · '.join(heads[:3])}_"]
    return []


def _absent(node: dict, what: str) -> list[str]:
    """값이 없을 때 **「원문에 없다」와 「우리가 못 찾았다」를 가른다**.

    `biz_fields` 는 `status` 를 늘 NOT_APPLICABLE 로 두고 진짜 구분은
    `extraction_status` 에 넣는다(NOT_APPLICABLE=원문이 「해당사항 없음」이라 밝힘 /
    NOT_COLLECTED=소절을 못 찾음). 렌더가 `status` 만 읽어 **둘 다 「해당 없음」**으로
    나갔다. 「없다」고 단정하면 읽는 쪽이 원문 확인을 포기한다.
    """
    st = node.get("status")
    reason = (node.get("na_reason") or node.get("note") or "").strip()
    kind = node.get("absence_kind")
    head = {"extraction_failed": "찾지 못함 — 원문에 표가 있습니다",
            "narrative_only": "표 없음 — 문장 서술만",
            "cross_reference": "여기엔 없음 — 원문이 다른 절을 가리킵니다",
            "not_disclosed": "해당 없음"}.get(kind)
    if not head:
        # 판정을 붙이지 않은 필드는 종전대로 — 소절을 못 찾은 것을 「해당 없음」이라 하지 않는다.
        head = ("확인하지 못함" if node.get("extraction_status") == "NOT_COLLECTED"
                else _ABSENT_KO.get(st, "확인 불가"))
    tail = node.get("absence_note") or reason
    return [f"\n{what}: {head}" + (f" — {tail}" if tail else "")]


def _seg_lines(seg: dict, h: str) -> list[str]:
    """영업부문(K-IFRS 1108) — 정형 → 주석 원문 → 표 후보 → 부재."""
    L, st = [], seg.get("status")
    if st == "OK":
        items = seg.get("items", [])
        u = (seg.get("unit") or "").strip()
        # 매출·이익을 함께 스케일한다 — 열마다 단위가 다르면 두 열을 눈으로 못 견준다.
        lab, div, dec = _table_scale([v for s in items for v in (s.get("revenue"), s.get("profit"))], u)
        L.append(f"\n{h} 사업부문별 매출·이익  (출처: 정형파싱)")
        L.append(f"| 부문 | 매출({lab or u or '단위 미상'}) | 영업이익({lab or u or '단위 미상'}) |")
        L.append("|---|--:|--:|")
        for s in items:
            L.append(f"| {s.get('name','')} | {_scaled(s.get('revenue'), div, dec)} "
                     f"| {_scaled(s.get('profit'), div, dec)} |")
        src = f" · 원문 표 단위 {u}" if lab and u and lab != u else ""
        L.append(f"\n_{seg.get('reconciliation','')}_  "
                 f"(지표: {seg.get('revenue_metric','')}/{seg.get('profit_metric','')}{src})")
        L.extend(_origin_lines(seg))
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
    L.extend(_origin_lines(node))
    L.append("\n" + node["markdown"])
    if node.get("truncation_note"):
        L.append(f"\n_{node['truncation_note']}_")
    return L


def _geo_lines(node: dict, h: str) -> list[str]:
    """지역별 수익 — 정형(검산 통과) 또는 원문 표. 종전엔 md 렌더가 아예 없어 안 보였다(260728)."""
    items = node.get("items") or []
    L: list[str] = []
    if items:
        # 이 표가 연결인지 별도인지 **표보다 먼저** 밝힌다 — 종전엔 출처 라벨에 「연결 기준」이
        # 하드코딩돼 별도 절을 읽고도 연결이라 말했다(실측 95건 중 5건).
        if node.get("basis"):
            L.append(f"\n_{node['basis']} 재무제표 주석 기준_")
        if node.get("basis_conflict"):
            L.append(f"> {node['basis_conflict']}")
        # 해외비중을 **표보다 먼저** — 단위가 약분돼 단위 미상일 때도 맞는 유일한 지표다.
        if node.get("foreign_share_pct") is not None:
            L.append(f"\n**해외 매출 비중 {node['foreign_share_pct']}%**"
                     f"  ({node.get('share_basis','')})")
            if node.get("share_caveat"):
                L.append(f"> {node['share_caveat']}")
        # 단위는 표 **머리**에 붙이고, 값은 사람이 읽는 단위로 환산한다. 각주에 두면 숫자와
        # 떨어져, 바로 옆 by_trade 가 「3.15조원」으로 쓰는 같은 값이 여기선 「3,147,338」로
        # 보여 다른 값으로 읽힌다(260802 파일럿 실측: HD현대일렉트릭 — 두 축의 값이 실제로
        # 같았다). 정확한 원값은 payload(JSON)에 그대로 있으므로 md 는 가독성을 택한다.
        _u = (node.get("unit") or "").strip()
        _lab, _div, _dec = _table_scale([i.get("revenue") for i in items], _u)
        _uh = f"매출({_lab})" if _lab else (f"매출({_u})" if _u else "매출(단위 미상 — 원문 확인)")
        L += [f"\n| 지역 | {_uh} |", "|---|--:|"]
        L.extend(f"| {i.get('name','')} | {_scaled(i.get('revenue'), _div, _dec)} |" for i in items)
        _src = f" · 원문 표 단위 {_u}" if _lab and _u and _lab != _u else ""
        L.append(f"\n_{node.get('reconciliation','')} · 지표 {node.get('revenue_metric','')}{_src}_")
        # 비유동자산 지역별 — 수출형 vs 현지생산형 판별자
        if node.get("assets_by_region"):
            _av = list(node["assets_by_region"].values())
            _al, _ad, _adec = _table_scale(_av, _u)
            L.append(f"\n| 지역 | 비유동자산({_al or _u}) |" if (_al or _u) else "\n| 지역 | 비유동자산 |")
            L.append("|---|--:|")
            L.extend(f"| {k} | {_scaled(v, _ad, _adec)} |" for k, v in node["assets_by_region"].items())
            L.append(f"_{node.get('assets_note','')}_")
        L.extend(_origin_lines(node))
        # 지역 매출을 무슨 기준으로 나라에 배분했는지. 공시한 회사가 5%뿐이라(실측 96건 중
        # 5건) 「고객 소재지」로 못박을 수 없다 — 「사업장 소재지 기준」을 쓰는 회사도 있다.
        # 없으면 없다고 밝힌다. 이 값이 해외비중의 의미를 좌우한다.
        if node.get("attribution_basis"):
            L.append(f"_귀속기준: {node['attribution_basis']}_")
        else:
            L.append("_귀속기준 미공시 — 어느 나라 매출로 잡았는지는 회사가 밝히지 않았습니다_")
        if node.get("basis_caption"):
            L.append(f"_기준: {node['basis_caption']}_")
    elif node.get("markdown"):
        L += [f"\n> {node.get('note','정형 검산을 통과하지 못해 원문 표를 그대로 싣습니다.')}",
              "\n" + node["markdown"]]
    else:
        # 「회사가 공시를 안 했다」와 「우리가 못 읽었다」를 구분해 보여준다.
        kind = node.get("absence_kind")
        if kind:
            mark = {"no_segment_note": "공시 없음", "not_disclosed": "공시 없음",
                    # 「위치 다름」은 다른 데 있다는 뜻으로 읽힌다 — 실제로는 문서를 다 훑고도
                    # 표가 없는 것이고, 지역이 문장으로만 적힌 상태다.
                    "extraction_failed": "⚠ 추출 실패", "outside_segment_note": "표 없음"}
            # 모르는 kind 를 그대로 굵게 찍으면 내부 코드가 그대로 나간다(`get(k, k)` 금지).
            L.append(f"\n지역별 수익: **{mark.get(kind, '확인 불가')}** — {node.get('absence_detail','')}")
            if node.get("absence_sections"):
                L.append(f"_해당 절: {' · '.join(node['absence_sections'])}_")
            if node.get("absence_hint"):
                L.append(f"> 💡 {node['absence_hint']}")
        else:
            L += _absent(node, "지역별 수익")
    # 옛 호출(fields="geo_revenue")은 수출/내수를 여기 중첩된 채로 받는다 — 260802에 독립 축
    # (by_trade)이 됐지만 그쪽을 안 쓰는 호출을 깨지 않으려고 남긴다.
    e = node.get("ii_export_domestic")
    if e:
        L.append("")
        L.extend(_trade_lines(e, "####"))
    return L


def _trade_lines(node: dict, h: str = "###") -> list[str]:
    """수출/내수(by_trade) — II 매출실적표. 지역별(by_region)과 **기준이 다른** 지표라
    같은 표에 합치지 않고 칸을 나눠 싣는다(별도 수출 vs 연결 외국 수익)."""
    if not node:
        return []
    if not node.get("export_krw") and not node.get("domestic_krw"):
        return _absent(node, "수출/내수")
    L = [f"**II 매출실적표 (별도 기준)** — 수출 {_krw(node.get('export_krw'))} · "
         f"내수 {_krw(node.get('domestic_krw'))}"
         + (f" · 수출비중 {node['export_share_pct']}%"
            if node.get("export_share_pct") is not None else "")]
    if node.get("basis"):
        L.append(f"_{node['basis']}_")
    if node.get("caveat"):
        L.append(f"> {node['caveat']}")
    return L


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
    _form = d.get("form_type", "")
    from open_proxy_mcp.services.filing_sections import origin_hint
    L.append(f"## {subj} — 사업부문 상세  ({rep.get('report_nm','')}"
             + (f", {_FORM_KO[_form]}" if _form in _FORM_KO else "") + ")")
    L.append(f"_{origin_hint(rep.get('rcept_no',''))} — 표가 약하거나 「확인하지 못함」이면 그 절을 직접 읽는다_")

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
        for axis, fn in (("by_segment", _seg_lines), ("by_product", _mix_lines),
                         ("by_region", _geo_lines), ("by_trade", _trade_lines)):
            node = rb.get(axis)
            if not node:
                continue
            L.append(f"\n### [{_AXIS_KO[axis]}]")
            L.append(f"_출처: {node.get('source','')}_")
            L.extend(fn(node, "####"))

    # 옛 호출(fields="geo_revenue")만 평평 키로 받는다 — 묶음 요청이면 위 by_region 에 이미 있다.
    geo = d.get("geo_revenue")
    if geo and not rb:
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
            # 이 회사 원문의 **어느 소절**에서 가져왔는지. payload 는 들고 있는데 렌더가
            # 안 쓰면, 회사마다 소절 제목이 달라 읽는 쪽이 원문에서 같은 자리를 못 찾는다.
            L.extend(_origin_lines(fd))
            L.append("> 아래 원문에서 값을 읽으세요. 단위·정의는 회사별 상이(비교 주의).")
            if key == "revenue_mix_form":
                # 이 표는 감사받은 주석이 아니다. 실측 33%가 연결매출과 안 맞고,
                # 비율 분모가 내부거래 포함 단순합계인 회사가 있다.
                L.append("> 제품별 매출 구분은 K-IFRS 기준과 다를 수 있습니다.")
            L.append("\n" + fd["markdown"])
            if fd.get("truncation_note"):
                L.append(f"\n_{fd['truncation_note']}_")
        elif fd.get("status") == "NEEDS_REVIEW":
            # 「해당없음」이 아니다 — 절은 찾았고 값만 못 믿는 것이다. 원문을 버리면 안 된다.
            L.append(f"\n### {label} (원문 · 검토필요)")
            L.append(f"> {fd.get('note') or '자동 판정을 보류했습니다.'} 원문을 그대로 싣습니다.")
            if fd.get("markdown"):
                L.append("\n" + fd["markdown"])
        else:
            L.extend(_absent(fd, f"**{label}**"))

    candidate = d.get("candidate_context")
    if candidate:
        if candidate.get("status") == "LOW_CONFIDENCE":
            L.append(f"\n### 저신뢰 보조 문맥 — {candidate.get('field','')}")
            L.append(f"> {candidate.get('warning','')}")
            L.append(f"> 앵커: {candidate.get('anchor','')} · 고정 문맥: {candidate.get('context_chars','')}자")
            L.append("\n" + candidate.get("markdown", ""))
        elif candidate.get("status") == "NOT_FOUND":
            L.append(f"\n**저신뢰 보조 문맥**: 찾지 못함 — {candidate.get('warning','')}")
            L.append(f"_{origin_hint(rep.get('rcept_no',''), candidate.get('anchor') or None)}_")

    tm = d.get("timings_ms", {})
    if tm:
        L.append(f"\n_조회 {tm.get('total','?')}ms · 주석fetch={d.get('note_fetched')}_")
    if p.get("warnings"):
        # 여기 담기는 것은 대개 실패가 아니라 처리 메모다(어느 문서를 썼나 · 정형 대신 원문을 냈나).
        # 경고 표지를 달면 읽는 사람이 뭘 잘못한 것처럼 느낀다.
        L.append("\n_처리 메모: " + " · ".join(p["warnings"]) + "_")
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
        section_chars: int = 20000,
    ) -> str:
        """desc: DART 정기보고서 **"II. 사업의 내용"**에서 사업부문별 매출·영업이익, **사업장·생산설비, 생산실적·가동률, 연구개발, 수주현황, 주요 고객·매출처, 원재료·투입원가, 제품·서비스 가격 추이**를 추출. SOTP·부문 수익성·생산능력·수주·고객집중·마진 분석의 1차 소스.
        when: 회사의 사업부문·생산·수주·고객 구조가 필요할 때. 전사 재무는 `financial_metrics`, 밸류는 `price_multiple_data`. **금융/증권/보험/지주는 `financial_ops`·`financial_soundness`, REIT/보험은 `investment_property`** 로 커버(segments 대신). **여러 분기/연도 추이**가 필요하면 `bsns_year`+`reprt_code`를 지정해 과거 시점을 하나씩 반복 호출.
        rule: segments는 정형→저신뢰 시 원문 마크다운. 나머지 필드는 **해당 소절 원문을 마크다운으로 반환** — 그 표를 읽어 값 추출(단위·정의 회사별 상이, 비교 주의). `context_mode=candidate`는 strict가 `NOT_COLLECTED`일 때만 **저신뢰 고정 윈도우 문맥**을 별도 `candidate_context`로 반환하며, 공식 결과·hint로 사용하면 안 됨. 이 모드는 표준 필드 하나를 지정할 때만 사용. 금융/REIT 필드는 표준사에선 자동 N/A. 유형자산 장부가 표를 사업장으로 오독 금지. **응답 `report.report_nm`으로 어느 보고서인지 확인**(분기/반기/사업). `bsns_year`/`reprt_code`는 **반드시 둘 다** 지정(하나만 주면 에러) — 지정 시 `period`는 무시됨.
        period: `latest`(기본, 사업·반기·분기 중 **가장 최신 제출분**=최신 데이터) / `annual`(연간 사업보고서 고정) / `quarterly`(분기·반기 고정). II.사업의내용은 분기/반기도 완전구조라 동일 필드. `bsns_year`+`reprt_code` 지정 시 이 파라미터는 무시.
        fields: 쉼표구분 — 표준: `revenue_breakdown,sites,utilization,rnd,backlog,customers,raw_materials,product_pricing,key_contracts`. **`revenue_breakdown`이 매출 분해의 단일 진입점** — 안에 매출 축 **4개**가 출처 라벨과 함께 들어 있고 `available`/`needs_review`로 어느 축에 값이 있는지 알려준다: `by_segment`(III 주석 K-IFRS 1108 영업부문, 외부감사 대상, **매출+영업이익**) · `by_product`(II-2-가 공시서식 기재사항, 외부감사 아님, 매출만) · `by_region`(III 주석 K-IFRS 1108 ¶33 전사차원, **연결** 기준 고객 소재지, 매출만) · `by_trade`(II-4 매출실적표, **별도** 기준 수출/내수, 매출만). **네 축을 더하거나 곱하지 말 것**(같은 매출을 다르게 자른 것) — 특히 `by_region`(연결)과 `by_trade`(별도)는 기준이 달라 방향이 양쪽으로 갈린다(현대차 1.4배·대한제분 0.5배). **이익이 있는 축은 `by_segment` 뿐**이다 — K-IFRS 1108이 이익을 영업부문에만 요구하기 때문. 지역별 이익이 필요하면 부문명이 지역·현지법인인 회사(예: 「미국 사업본부」)의 `by_segment`를 본다. 단일 영업부문 회사도 `by_product`엔 제품 구성이 있다(HD현대일렉트릭: 전력기기 69.5%). 옛 이름 `segments`·`revenue_mix_form`·`geo_revenue`를 fields로 직접 주면 종전대로 평평하게 반환(별칭 — 옛 호출 호환용, 새 코드는 축 이름을 쓴다) / 금융·REIT: `financial_ops,financial_soundness,investment_property`. `raw_materials`는 원재료 구성·매입과 원재료 가격 추이를 별도 소절로 반환하고, `product_pricing`은 판매가격·ASP·가격변동 원인을 반환. **`self_check`(단위·비율합·합계행 대조)로 by_product 의 자기정합성을 먼저 볼 것.** `key_contracts`는 II-6-가 라이선스·기술도입·장기공급 계약(연구개발은 `rnd`). (미지정 시 회사에 맞는 표준·금융 필드만). **자산(토지·투자부동산·지분증권 원가vs공정가치)은 별도 tool `asset_holdings`.**
        bsns_year: 특정 과거 사업연도 조회(예: "2025"). `reprt_code`와 함께 지정해야 함 — **추이 조회용**(한 번에 여러 분기 반환 아님, 분기마다 반복 호출).
        reprt_code: DART 표준 보고서유형 — `11011`(사업/연간) `11012`(반기) `11013`(1분기) `11014`(3분기). `bsns_year`와 함께 지정.
        context_mode: `strict`(기본) / `candidate`. candidate는 strict `NOT_COLLECTED`일 때만 단일 표준 필드의 저신뢰 보조 문맥을 별도 반환.
        context_chars: candidate 고정 문맥 길이(기본 20000, 최대 60000). strict에서는 사용하지 않음.
        section_chars: 소절 원문 1개당 반환 상한(기본 20000, 2000~200000). **정보가 부족하면 올려서 다시 호출**하세요 — 응답에 `markdown_truncated`·`truncation_note`가 붙어 있으면 뒤쪽이 잘린 것입니다. 금융지주·보험은 계열사마다 같은 항목을 실어 한 소절이 크므로(실측: 재무건전성 70,710자) 계열사 전체가 필요하면 80000 이상을 권합니다. 크게 올리면 응답도 그만큼 커집니다.
        ref: financial_metrics, price_multiple_data, order_contracts, company
        """
        flist = [f.strip() for f in fields.split(",") if f.strip()] or None
        payload = await build_business_details_payload(
            company, period=period, fields=flist, bsns_year=bsns_year, reprt_code=reprt_code,
            context_mode=context_mode, context_chars=context_chars,
            section_chars=section_chars,
        )
        if format == "json":
            return as_pretty_json(payload)
        return _render(payload)
