# -*- coding: utf-8 -*-
"""financial_notes tool — 금융사 재무제표 주석 표 원형 추출.

설계: wiki/decisions/260823_1720_decision_financial-notes-tool.md
"""
from __future__ import annotations

import json

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services import financial_notes as svc
from open_proxy_mcp.services.business_details import (
    _find_report_candidates, _report_period_tag,
)
from open_proxy_mcp.services.company import resolve_company_query
from open_proxy_mcp.services.contracts import AnalysisStatus
from open_proxy_mcp.services.contracts import ToolEnvelope


def _render(payload: dict) -> str:
    """표를 **그대로** 마크다운으로. 합치거나 나누지 않는다."""
    L = [f"# {payload['company']} 재무제표 주석",
         "",
         f"- 보고서: {payload['report']['report_nm']} (`{payload['report']['rcept_no']}`)",
         f"- 조회 필드: {', '.join(payload['fields'])}",
         ""]
    for field, res in payload["notes"].items():
        L.append(f"## {field}")
        if res["status"] != svc.OK:
            L += ["", f"> {res['status']} — {res.get('note','')}", ""]
            continue
        if res.get("note"):
            L += ["", f"> {res['note']}", ""]

        # 🔴 사용제한은 한 표에 다 있지 않다(우리은행: 현금및현금성자산 + 예치금 두 군데).
        #    여러 표를 모아 내되, **연결과 별도가 섞여 있을 수 있으니** 합산 전에 알린다.
        by_kind: dict[str, int] = {}
        for t in res["tables"]:
            by_kind[t["kind"]] = by_kind.get(t["kind"], 0) + 1
        for k, n in by_kind.items():
            if n > 1:
                label = {"restricted": "사용제한", "pledged": "담보제공"}.get(k, k)
                bases = [t.get("table_basis") for t in res["tables"] if t["kind"] == k]
                if all(bases) and len(set(bases)) > 1:
                    hint = ("**연결과 별도가 섞여 있다**(각 표의 `기준` 참조) — "
                            "같은 기준끼리만 더할 것. 섞어 더하면 이중계상이다.")
                else:
                    hint = ("원문이 여러 주석에 나눠 실었다(예: 현금및현금성자산 · 예치금). "
                            "`기준` 이 같으면 더해도 되지만, 같은 계정을 두 번 세지 않는지 볼 것.")
                L += ["", f"> 🔴 **{label} 표가 {n}개다** — {hint}", ""]
        for t in res["tables"]:
            kind = {"restricted": "사용제한", "pledged": "담보제공"}.get(t["kind"], t["kind"])
            # 🔴 **제목 대조 성공을 명시한다.** 성공과 미대조가 똑같이 무표시면 읽는 쪽은
            #    「경고 없음 = 맞음」으로 읽고, 그러면 틀린다(260823 T보고).
            axis = t.get("axis")
            # 🔴 **✅ 와 「범주별」이 한 표에 같이 붙으면 모순이다.** 260823 시험자 지적 —
            #    제목은 맞아도 범주별이면 헤어컷을 못 매기니 ✅ 를 주면 안 된다.
            # 🔴 **경고를 셋으로 가른다.** 260823 시험자 실측 — 「범주별」 판정은 한 건도
            #    안 틀렸는데 「제목 대조 실패」는 위양성이 많았다. 그리고 **「축 유형별 +
            #    제목 실패」는 전부 맞는 표**였다. 다 🔴 로 묶으면 「일단 다 열어봐야 한다」가
            #    되어 경고가 없는 것과 같아진다.
            if axis == "범주별":
                mark = "🔴 **범주별** — 헤어컷을 매길 수 없다"
            elif t.get("title_matched"):
                mark = "✅ 제목 확인됨"
            elif axis == "유형별":
                mark = "⚠️ 이름은 못 맞췄지만 **축은 유형별**"
            else:
                mark = "🔴 제목 대조 실패"
            basis = t.get("table_basis")
            L.append(f"\n**앵커** `{t['anchor']}` · **성격** {kind}"
                     + (f" · **기준** {basis}" if basis else " · **기준** 판별 못함")
                     + f" · {mark}"
                     + (f" · **축** {axis}" if axis else " · **축** 판별 못함")
                     + f" · **문서위치** {t['pos']:,} · **형식** "
                     + ('XBRL 태그' if t['format'] == 'xbrl_tagged' else 'HTML 표')
                     + (f" · **단위** {t['unit']}" if t.get("unit")
                        else " · **단위** 원문에 표기 없음"))
            # 원문이 이 표에 붙인 문장. 한 줄만 보고 버릴지 쓸지 정할 수 있다.
            # 🔴 caption 이 아니라 title 이다 — caption 꼬리에는 **앞 표의 숫자 잔해**가
            #    붙어 오고, 그걸 이 표의 값으로 읽으면 틀린다(260823 실측).
            if t.get("title"):
                L.append(f"> 📄 원문 제목: {t['title'][:150]}")
            if t.get("account"):
                tot = t.get("account_total")
                if tot:
                    L.append(f"> 🏷 이 금액이 붙어 있는 계정: **{t['account']}** · "
                             f"재무상태표({t.get('table_basis') or '?'}) 잔액 "
                             f"**{' / '.join(tot['values'][:2])}** {tot.get('unit') or ''} — "
                             f"여기서 위 사용제한액을 뺀다.")
                else:
                    L.append(f"> 🏷 이 금액이 붙어 있는 계정: **{t['account']}** · "
                             f"🔴 **재무상태표에서 같은 이름의 계정을 못 찾았다 — 분모 없음.** "
                             f"이름이 정확히 맞지 않으면 붙이지 않는다(틀린 분모는 "
                             f"없는 것보다 나쁘다). 이 회사는 unencumbered 를 계산하지 말 것.")
            if t.get("also_kinds"):
                names = [{"restricted": "사용제한", "pledged": "담보제공"}.get(k, k)
                         for k in t["also_kinds"]]
                L.append(f"> 🔴 **이 표 하나에 {kind}과 {', '.join(names)}이 함께 있다** — "
                         f"원문이 「사용이 제한된 예치금 **및** 담보제공자산 등」처럼 한 주석에 "
                         f"묶어 공시했다. **성격별로 더하면 두 배가 된다.**")
            if axis == "범주별":
                L.append("> 🔴 **범주별 표다 — 유형별이 아니다.** 「어떤 자산을 어떤 측정범주로 "
                         "분류했나」이지 그 안이 국공채인지 회사채인지는 없다. "
                         "**헤어컷을 매길 수 없다.** 같은 문서 안에 유형별 표가 따로 있을 수 있다.")
            if t["format"] == "xbrl_tagged":
                L.append("> 값마다 IFRS 코드가 붙어 있다 — `[acode]` 로 항목을 확인한다. "
                         "**열 위치로 짐작하지 말 것.** 연결/별도는 `basis`.")
            if t.get("title_matched") is False:
                L.append("> 🔴 **표 제목을 대조하지 못했다** — 원문이 이 표에 "
                         f"「{t['anchor']}…내역/공시」라는 제목을 붙이지 않았다. "
                         "위험·공정가치수준별 같은 **다른 표일 수 있으니 그대로 인용하지 말 것.** "
                         "위 「원문 제목」 한 줄로 무슨 표인지 먼저 확인할 것.")
            if t.get("shared_with"):
                L.append(f"> ℹ️ 이 표는 **{', '.join(t['shared_with'])} 와 같은 표**다 — "
                         f"회사가 한 표에 함께 공시했다. 중복 반환이 아니다.")
            if t.get("ragged"):
                L.append(f"> 🔴 **행마다 열 수가 다르다**(열 폭 {t['widths']}). 병합 셀 때문이며 "
                         f"그대로 읽으면 값이 밀린다. 병합 폭은 각 칸의 `colspan` 에 있다.")
            L.append("")
            for row in t["rows"]:
                cells = []
                for c in row:
                    s = c["text"] or " "
                    if c.get("colspan"):
                        s += f" <{c['colspan']}칸>"
                    if c.get("basis"):
                        s += f" ({c['basis']})"
                    if c.get("acode"):
                        s += f" [{c['acode']}]"
                    cells.append(s)
                L.append("| " + " | ".join(cells) + " |")
            L.append("")
    L += ["---",
          "⚠️ **✅ 제목 확인됨은 「원문이 그 이름으로 제목을 붙였다」까지다.** 표의 내용이 "
          "요청한 것과 같다는 보증이 아니다 — **축**과 **원문 제목**을 함께 볼 것.",
          "⚠️ **단위를 확인하기 전에는 금액을 쓰지 말 것.** 회사마다 백만원/천원/원이 섞이고, "
          "**같은 회사 안에서도 보고서마다 다르다**(현대해상: 분기 원 · 반기 천원 · 사업 원). "
          "보고서를 바꿔가며 시계열을 이을 때는 단위를 매번 다시 볼 것 — 1,000배가 어긋난다. "
          "「원문에 표기 없음」이면 그 표의 숫자는 규모를 알 수 없는 값이다.",
          "⚠️ 표는 원문 그대로다. **열 이름의 기준 시점을 반드시 확인할 것** — 같은 표에 당기말과 "
          "전기말이 함께 있고, 회사에 따라 값이 크게 다르다(KB손보 사용제한 합계: 전기말 391,082 → "
          "당반기말 26,356).",
          "⚠️ 「사용제한」과 「담보제공」은 **구분해 내보낸다.** 산식에 같이 넣을지는 이용자가 정한다.",
          "⚠️ unencumbered cash 계산·헤어컷 적용은 이 tool 밖이다."]
    return "\n".join(L)


def register_tools(mcp):

    @mcp.tool()
    async def financial_notes(
        company: str,
        fields: str = "",
        period: str = "latest",
        format: str = "md",
    ) -> str:
        """desc: **은행·증권·보험**의 연결/별도 **재무제표 주석** 표를 원형 그대로 추출. ①사용제한 예치금·담보제공자산(→unencumbered cash) ②투자자산 유형별 구성 FVPL·FVOCI·상각후원가(→유형별 헤어컷·자산건전성).
        when: 금융사 유동성(부채상환능력)·자산건전성 평가. 전사 집계는 `financial_metrics`, 「II.사업의 내용」은 `business_details`.
        rule: **표를 합치거나 나누지 않는다** — 회사마다 표 형태가 다른 것 자체가 정보다. 모든 값에 열 이름(기준 시점)이 붙어 있으니 **당기말/전기말을 반드시 구분**할 것(KB손보 사용제한: 전기말 391,082 → 당반기말 26,356으로 1/15). **단위가 회사마다 다르고, 같은 회사 안에서도 보고서마다 다르다** — 현대해상은 분기 `원` · 반기 `천원` · 사업 `원` 이라 **분기와 반기를 그대로 이으면 1,000배 어긋난다.** 응답 머리의 `단위` 를 매번 확인할 것. 「사용제한」과 「담보제공」은 `kind` 로 구분해 내보내며 **합치지 않는다** — 담보 제공은 소유권이 남고 사용제한은 인출이 막힌 것이라 회계상 다르다. unencumbered cash 계산·헤어컷 적용은 이 tool 밖이다.
        fields: 쉼표구분 — `사용제한,FVPL,FVOCI,상각후원가` (미지정 시 전부). 문서 다운로드는 회사당 1회라 한 번에 부르는 편이 싸다.
        period: `latest`(기본, 사업·반기·분기 중 최신 제출분) / `annual`(사업) / `half`(반기) / `quarter`(분기) / `quarterly`(분기+반기 중 최신). **`quarterly` 는 반기를 함께 잡으므로 분기만 보려면 `quarter` 를 쓸 것.**
        """
        want = [f.strip() for f in fields.split(",") if f.strip()] or list(svc.FIELDS)
        bad = [f for f in want if f not in svc.ANCHORS]
        if bad:
            return f"알 수 없는 항목: {bad}. 가능한 값: {list(svc.FIELDS)}"

        resolution = await resolve_company_query(company)
        if resolution.status != AnalysisStatus.EXACT or not resolution.selected:
            env = ToolEnvelope(tool="financial_notes", status=resolution.status,
                               subject=company, warnings=["회사를 하나로 식별하지 못했습니다"],
                               data={"candidates": resolution.candidates})
            return json.dumps(env.to_dict(), ensure_ascii=False)
        corp = resolution.selected
        corp_code = corp["corp_code"]
        name = corp.get("corp_name", company)
        c = get_dart_client()

        cands = await _find_report_candidates(c, corp_code, period)
        if not cands:
            return json.dumps({"error": "정기보고서를 찾지 못했습니다", "company": company},
                              ensure_ascii=False)

        # 정정본은 원문(document.xml)이 없을 수 있다(DART 014) — **같은 기수 원본으로 폴백**한다.
        # 260823 실측: 삼성화재 [첨부정정]사업보고서가 014, 하루 전 원본은 정상 2,675만자.
        tag0 = _report_period_tag(cands[0])
        report = None
        html = ""
        tried = []
        for r in cands[:6]:
            if tag0 and _report_period_tag(r) != tag0:
                break                       # 다른 기수로 넘어가면 멈춘다(작년 데이터 금지)
            try:
                doc = await c.get_document_cached(r["rcept_no"])
                report, html = r, doc.get("html") or ""
                break
            except DartClientError as e:
                tried.append(f"{r['rcept_no']}({str(e)[:40]})")
        if not html:
            env = ToolEnvelope(tool="financial_notes", status="NO_DATA", subject=name,
                               warnings=[f"원문을 받지 못했습니다 — 시도: {tried}"],
                               data={"status": svc.NOT_COLLECTED})
            return json.dumps(env.to_dict(), ensure_ascii=False)

        notes = svc.extract(html, want)
        payload = {
            "company": name, "corp_code": corp_code,
            "report": {"report_nm": report.get("report_nm", "").strip(),
                       "rcept_no": report["rcept_no"], "rcept_dt": report.get("rcept_dt")},
            "fields": want, "notes": notes,
            "doc_chars": len(html),
        }
        if format == "json":
            env = ToolEnvelope(tool="financial_notes", status="OK", subject=name, data=payload)
            return json.dumps(env.to_dict(), ensure_ascii=False)
        return _render(payload)
