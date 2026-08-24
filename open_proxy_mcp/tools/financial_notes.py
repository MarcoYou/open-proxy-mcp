# -*- coding: utf-8 -*-
"""financial_notes tool — 금융사 재무제표 주석 표 원형 추출.

설계: wiki/decisions/260823_1720_decision_financial-notes-tool.md
"""
from __future__ import annotations

import json

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services import financial_notes as svc
from open_proxy_mcp.services.business_details import (
    _find_report_candidates, _find_report_for_bsns_year, _report_period_tag,
)
from open_proxy_mcp.services.company import resolve_company_query
from open_proxy_mcp.services.contracts import AnalysisStatus
from open_proxy_mcp.services.contracts import ToolEnvelope


def _against_totals(rec: dict, total_view: dict | None) -> str:
    """세부표 잎 합을 **원문 합계표**와 맞춰 본다. 260824 마스터 지시.

    🔴 **「안 맞으면 틀렸다」로 쓰지 않는다.** 합계표는 현재가치할인차금·손실충당금을
       반영한 수라 세부표(총장부금액) 잎 합과 원래 다를 수 있다. 그래서 **똑같은 값이
       있으면 ✅(정렬이 맞았다는 증거)** 로만 쓰고, 없으면 원문 값을 나란히 적어 준다.
       260824 NH 실측 — 예치금·미수금·미수수익 셋은 정확히 일치했고, 대출채권·기타채권은
       차감 항목만큼 달랐다(14,765,043 vs 14,735,015 = 28,267 + 1,761).
    """
    if not total_view:
        return ""
    g = rec["group"]
    mine = [c for c in total_view["columns"]
            if c["label"] == g or c["label"].startswith(g + " › ")]
    if not mine:
        return ""
    vals = [c["values"][0] for c in mine if c["values"] and c["values"][0].strip()]
    if not vals:
        return ""
    if rec["sum"] in vals:
        return f" → 원문 합계표에 같은 값 있음 ✅"
    return f" → 원문 합계표: {' · '.join(vals)} (차감 항목만큼 다를 수 있다)"


def _same_asset_pairs(tables: list[dict], views: list[dict | None], kind: str) -> list[str]:
    """같은 성격·같은 기준인데 **총합이 같은** 표 짝을 찾는다 — 더하면 2배인 것들."""
    idx = [i for i, x in enumerate(tables)
           if x["kind"] == kind and x.get("role") != "합계"]
    out: list[str] = []
    for a in range(len(idx)):
        for b in range(a + 1, len(idx)):
            i, j = idx[a], idx[b]
            if tables[i].get("table_basis") != tables[j].get("table_basis"):
                continue
            if svc.same_assets(views[i], views[j]):
                out.append(f"{a + 1}번↔{b + 1}번 표")
    return out


_CK_LIMIT = 12


def _checksum_lines(view: dict | None, total_view: dict | None = None,
                    transposed: bool = True) -> list[str]:
    """열 묶음별 검산. **도구가 더한 값은 그렇다고 밝힌다.**

    이 도구는 값을 만들지 않는 것이 계약이라, 여기서 나오는 수에는 전부 출처를 붙인다.
    원문에 합계 열이 있으면 대조해 ✅/🔴 를 내고, 없으면 「도구가 더함」으로만 적는다.
    """
    if not view:
        return []
    # 보통 표(행=항목 · 열=시점) — 「합계」 행을 나머지 행과 맞춘다. 이게 진짜 검산이다.
    if not transposed:
        rc = svc.row_checksums(view)
        if not rc:
            return []
        # 🔴 **하나도 안 맞으면 줄줄이 늘어놓지 않는다.** 260824 실측 — 국민은행
        #    「계정별 장부금액 및 공정가치」는 자산·부채가 한 표에 있고 계층까지 섞여
        #    있어 네 열이 전부 어긋난다. 네 줄로 늘어놓으면 경고만 시끄러워지고
        #    **읽는 쪽이 다음부터 검산을 안 본다.** 사실만 한 줄로 적는다.
        if not any(r["ok"] for r in rc):
            return ["> ⚠️ **검산 못 함** — 원문 「{}」 행이 나머지 행의 합과 맞지 않는다"
                    "(예: {} vs {}). 계층이나 차감이 섞인 표이거나 자산·부채가 한 표에 "
                    "있는 경우다. **도구가 틀렸다는 뜻이 아니다** — 원문을 보고 판단할 것."
                    .format(rc[0]["row"], rc[0]["sum"], rc[0]["stated"]), ""]
        head = ["> 🧮 **검산** — 원문 「{}」 행을 나머지 행의 합과 맞춰 봤다.".format(rc[0]["row"])]
        for r in rc[:_CK_LIMIT]:
            # 🔴 안 맞는다고 반드시 도구가 틀린 것은 아니다 — 원문이 차감 항목을
            #    섞어 놓은 표도 있다(신한은행 「현금및예치금 − 사용제한 − 3개월초과」).
            #    「인용하지 말 것」으로 못 박으면 멀쩡한 표까지 버리게 된다.
            # 🔴 안 맞는다고 도구가 틀린 것은 아니다 — 원문이 차감 항목을 섞어 놓은 표도
            #    있다(신한 「현금및예치금 − 사용제한 − 3개월초과」). 「인용하지 말 것」으로
            #    못 박으면 멀쩡한 표까지 버리게 되므로 ⚠️ 로 사실만 적는다.
            mark = ("✅ 일치" if r["ok"] else
                    "⚠️ 안 맞는다 — 이 표는 단순 나열이 아닐 수 있다"
                    "(계층·차감이 섞인 표). 검산으로는 판단할 수 없으니 원문을 볼 것")
            head.append(f">  · {r['column']}: {r['n']}행 합 {r['sum']} / 원문 {r['stated']} → {mark}")
        head.append("")
        return head
    rows = svc.checksums(view, transposed=True)
    if not rows:
        return []
    multi = len(view["rows"]) > 1
    out = ["> 🧮 **검산** — 아래 합은 **도구가 위 표의 잎을 더한 값**이다(원문에 없는 수). "
           "원문에 합계 열이 있으면 대조해 표시했다."]
    for r in rows[:_CK_LIMIT]:
        where = f"{r['group']}" + (f" · {r['row']}" if multi and r["row"] else "")
        if "stated" in r:
            mark = "✅ 일치" if r.get("ok") else "🔴 **불일치**"
            out.append(f">  · {where} — 잎 {r['n']}열 합 {r['sum']} / 원문 합계 {r['stated']} → {mark}")
        else:
            line = f">  · {where} — 잎 {r['n']}열 합 **{r['sum']}** (도구가 더함)"
            out.append(line + _against_totals(r, total_view))
    if len(rows) > _CK_LIMIT:
        out.append(f">  · … 외 {len(rows) - _CK_LIMIT}건은 줄였다")
    out.append("")
    return out


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
        views = [svc.column_view(x) for x in res["tables"]]
        by_kind: dict[str, int] = {}
        for t in res["tables"]:
            # 🔴 원문의 짝(세부표 ↔ 합계표)은 「표가 여러 개」 경고 대상이 아니다.
            #    그건 우리가 일부러 함께 낸 것이고, 바로 아래에 🧾 로 따로 알린다.
            if t.get("role") in ("합계", "분모"):
                continue
            by_kind[t["kind"]] = by_kind.get(t["kind"], 0) + 1
        for k, n in by_kind.items():
            if n > 1:
                label = {"restricted": "사용제한", "pledged": "담보제공"}.get(k, k)
                bases = [t.get("table_basis") for t in res["tables"] if t["kind"] == k]
                periods = [t.get("period") for t in res["tables"] if t["kind"] == k]
                n_prev = sum(1 for x in periods if x == "전기")
                if n_prev:
                    L += ["", f"> 🔴 **{ {'restricted':'사용제한','pledged':'담보제공'}.get(k,k) }"
                              f" 표 {n}개 중 {n_prev}개가 **전기 전용 표**다** — 당기와 더하면 "
                              f"이중계상이다. 각 표의 `시점` 을 볼 것.", ""]
                # 🔴 **총합이 같은 두 표는 같은 자산을 다르게 자른 것이다.** 260824 T보고 —
                #    현대해상 담보제공은 범주별 요약과 유형별 세부가 짝인데(양쪽 총합
                #    2,310,215,724 천원으로 동일) 예전 문구가 「기준이 같으면 더해도 된다」로
                #    시작해 **정확히 2배**를 유도했다. 이제는 세어 보고 말한다.
                accts = [x.get("account") for x in res["tables"]
                         if x["kind"] == k and x.get("role") not in ("합계", "분모")]
                accts = [a for a in accts if a]
                same = _same_asset_pairs(res["tables"], views, k)
                if same:
                    hint = ("🔴 **두 표의 총합이 같다 — 같은 자산을 다르게 자른 것이다"
                            f"({' · '.join(same)}). 절대 더하지 말 것 — 정확히 2배가 된다.** "
                            "하나는 범주별 요약, 하나는 유형별 세부인 경우가 많다.")
                elif len(accts) == n and len(set(accts)) == n:
                    # 🔴 **판정할 수 있는 자리를 이용자에게 떠넘기지 않는다.** 260824 T
                    #    5회차 — 우리은행 사용제한 표 둘은 🏷 계정이 「현금및현금성자산」과
                    #    「예치금」으로 **이미 다르다.** 계정이 다르면 같은 자산일 수 없으니
                    #    더해도 된다. 「먼저 확인할 것」으로 내보내면 도구가 아는 것을
                    #    안 말하는 셈이다.
                    hint = ("✅ **표마다 계정이 다르다**({}) — 같은 자산이 아니므로 "
                            "**더해도 된다.** 다만 계정별로 뺄 분모가 각각 다르다는 것에 "
                            "주의할 것.".format(" · ".join(accts)))
                elif all(bases) and len(set(bases)) > 1:
                    hint = ("**연결과 별도가 섞여 있다**(각 표의 `기준` 참조) — "
                            "같은 기준끼리만 더할 것. 섞어 더하면 이중계상이다.")
                else:
                    hint = ("**더하기 전에 두 표가 같은 자산을 다르게 자른 것이 아닌지 "
                            "먼저 확인할 것.** 원문이 여러 주석에 나눠 실은 경우(현금및현금성자산 · "
                            "예치금)라면 더해도 되지만, 요약표와 세부표가 짝이면 더하면 2배가 된다. "
                            "각 표의 합을 맞춰 보고 판단할 것.")
                # 판정이 「더해도 된다」인데 🔴 로 시작하면 읽는 쪽이 위험 신호로 읽는다.
                lead = "ℹ️" if hint.startswith("✅") else "🔴"
                L += ["", f"> {lead} **{label} 표가 {n}개다** — {hint}", ""]
        # 🔴 **표를 다 편 다음에 훑는다.**(`views` 는 위에서 미리 만들었다) 합계표가
        #    세부표보다 뒤에 오므로, 훑으면서 펴면 세부표 차례에 대조할 상대가 없다.
        for idx, t in enumerate(res["tables"]):
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
            if t.get("other_field"):
                mark = f"🔴 **제목은 {t['other_field']} 표다** — 요청한 범주가 아니다"
            elif axis == "범주별":
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
                     + (f" · 🔴 **시점** {t['period']}만" if t.get("period") else "")
                     + f" · {mark}"
                     + (f" · **축** {axis}" if axis else " · **축** 판별 못함")
                     # 🔴 문서위치는 뺐다 — 목차 노드로 절만 받아오면 조각 기준 오프셋이라
                     #    「문서 어디쯤」이 아니다. 연결/별도는 이제 `기준` 이 직접 말한다.
                     + " · **형식** "
                     + ('XBRL 태그' if t['format'] == 'xbrl_tagged' else 'HTML 표')
                     + (f" · **단위** {t['unit']}" if t.get("unit")
                        else " · **단위** 원문에 표기 없음"))
            # 원문이 이 표에 붙인 문장. 한 줄만 보고 버릴지 쓸지 정할 수 있다.
            # 🔴 caption 이 아니라 title 이다 — caption 꼬리에는 **앞 표의 숫자 잔해**가
            #    붙어 오고, 그걸 이 표의 값으로 읽으면 틀린다(260823 실측).
            if t.get("title"):
                from open_proxy_mcp.services.financial_notes import looks_like_debris
                if looks_like_debris(t["title"]):
                    L.append("> 📄 원문 제목: **원문에 제목 문장이 없다** — 표 앞이 값이나 "
                             "설명문이다. 무슨 표인지는 열 이름으로 판단할 것.")
                else:
                    L.append(f"> 📄 원문 제목: {t['title'][:150]}")
            if t.get("account"):
                tot = t.get("account_total")
                has_den = any(o.get("role") == "분모"
                              and o.get("table_basis") == t.get("table_basis")
                              for o in res["tables"])
                if has_den:
                    L.append(f"> 🏷 이 금액이 붙어 있는 계정: **{t['account']}** · "
                             f"✅ **뺄 원본은 위 「분모표」다** — 같은 주석에 실린 구성내역이라 "
                             f"**단위와 기준이 이 표와 같다.** 재무상태표 잔액은 계정이 묶여 있어 "
                             f"쓰지 않는다.")
                elif tot and tot.get("spread"):
                    L.append(f"> 🏷 이 금액이 붙어 있는 계정: **{t['account']}** · "
                             f"🔴 **재무상태표에 그 이름의 계정이 따로 없다 — 여러 계정에 "
                             f"걸쳐 있다**({' · '.join(tot['spread'])} 등). 어느 것이 분모인지 "
                             f"도구가 정할 수 없다. **임의로 고르지 말 것.**")
                elif tot and tot.get("contains"):
                    # 🔴 은행·증권은 「현금및예치금」으로 현금과 묶여 있다(260824 T보고).
                    #    그대로 분모로 쓰면 현금까지 분모에 들어가 unencumbered 가 부풀려진다.
                    L.append(f"> 🏷 이 금액이 붙어 있는 계정: **{t['account']}** · "
                             f"🔴 **재무상태표에는 「{tot['matched']}」 안에 묶여 있다** "
                             f"({t.get('table_basis') or '?'} 잔액 "
                             f"**{' / '.join(tot['values'][:2])}** {tot.get('unit') or ''}). "
                             f"**{t['account']} 만의 잔액이 아니므로 그대로 분모로 쓰지 말 것** — "
                             f"쓰면 분모가 커져 unencumbered 가 과대계상된다. "
                             f"분해가 필요하면 주석에서 {t['account']} 잔액을 따로 찾을 것.")
                elif tot:
                    L.append(f"> 🏷 이 금액이 붙어 있는 계정: **{t['account']}** · "
                             f"재무상태표({t.get('table_basis') or '?'}) 잔액 "
                             f"**{' / '.join(tot['values'][:2])}** {tot.get('unit') or ''} — "
                             f"여기서 위 사용제한액을 뺀다."
                             + (f" (원문 계정명 「{tot['matched']}」)"
                                if tot.get("matched") and tot["matched"] != t["account"] else ""))
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
            if t.get("other_field"):
                L.append(f"> 🔴 **요청한 범주의 표가 아니다.** 원문 제목이 말하는 것은 "
                         f"**{t['other_field']}** 다. 앵커가 걸린 것은 표 **안에** 그 말이 "
                         f"열 이름으로 들어 있어서다(예: 평가손익표의 「상각후원가」 열). "
                         f"**축이 유형별이어도 범주가 다르면 다른 표다 — 인용하지 말 것.**")
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
            if t.get("role") == "분모":
                L.append("> 🧮 **분모표 — 위 사용제한액을 여기서 뺀다.** 원문이 사용제한 주석 "
                         "**바로 앞 항**에 실은 구성내역이다. **재무상태표 대신 이걸 쓴다** — "
                         "재무상태표는 은행·증권을 「현금및예치금」으로 묶어 실어 분모가 부풀고"
                         "(국민은행 실측 32,554,519 vs 주석 29,989,042 — 차이 2,565,477 이 현금), "
                         "**단위도 다를 수 있다**(메리츠: 재무상태표 원 · 주석 천원). "
                         "🔴 **사용제한 표가 계정별로 나뉘어 있으면 같은 계정 부분만 뺄 것** — "
                         "메리츠 31-1 은 현금및예치금과 당기손익-공정가치측정금융자산이 함께 있어 "
                         "합계를 통째로 빼면 과소계상이다.")
            if t.get("role") == "합계":
                L.append("> 🧾 **원문이 따로 실은 합계표다** — 바로 위 세부표의 짝이다. "
                         "세부표의 잎을 더한 값과 여기 값이 맞아야 정렬이 맞은 것이다. "
                         "**세부표와 이 표를 더하지 말 것 — 같은 자산이다.**")
            if t.get("shared_with"):
                L.append(f"> ℹ️ 이 표는 **{', '.join(t['shared_with'])} 와 같은 표**다 — "
                         f"회사가 한 표에 함께 공시했다. 중복 반환이 아니다.")
            if t.get("ragged"):
                L.append(f"> 🔴 **행마다 열 수가 다르다**(열 폭 {t['widths']}). 병합 셀 때문이며 "
                         f"그대로 읽으면 값이 밀린다. 병합 폭은 각 칸의 `colspan` 에 있다.")
            L.append("")

            # 🔴 **머리가 깊은 표는 물리 행 그대로 내보내면 읽는 쪽이 정렬을 복원해야 한다.**
            #    260824 NH투자증권 — 머리 8행 × 값 1행 × 27열 전치표에서 시험자도 렌더 UI도
            #    둘 다 열을 밀려 읽었다(예치금 소계를 12,731,887 로 냈다. 원문은 12,131,887).
            #    격자를 편 뒤 **값마다 열 경로를 붙여** 내보내면 복원할 것이 없다.
            #    칸을 옮겨 적을 뿐 합치거나 나누지 않는다 — 계약은 그대로다.
            view = views[idx]
            # 전치표 — 머리가 여러 단으로 쌓이고 열이 **항목**인 표. 여기서만 열을 더한다.
            transposed = bool(view and view["depth"] >= 3 and view["n_cols"] >= 6)
            if transposed:
                L.append(f"> 🧭 **격자로 폈다 — {view['n_cols']}열 × {t['n_rows']}행"
                         f"(머리 {view['depth']}행).** 병합 칸을 채워 열 이름과 값을 같은 열에 "
                         f"맞췄다. **원문 칸을 그대로 옮긴 것이다** — 합치거나 나누지 않았다.")
                if view["common"]:
                    L.append(f"> 🧷 값열 전부가 공유하는 머리: {' › '.join(view['common'])}")
                L.append("")
                L.append("| 열 이름(경로) | " + " | ".join(x or " " for x in view["rows"]) + " |")
                for c in view["columns"]:
                    L.append("| " + (c["label"] or " ") + " | "
                             + " | ".join(v or " " for v in c["values"]) + " |")
            else:
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

            # 🧮 검산 — 260824 마스터 지시. 잎을 더해 원문 합계와 맞춰 본다.
            #    맞으면 **열 정렬이 맞았다는 증거**이고, 틀리면 그 자리가 어긋난 것이다.
            total_view = next((views[j] for j, o in enumerate(res["tables"])
                               if o.get("role") == "합계" and j != idx), None)
            for line in _checksum_lines(view, total_view, transposed):
                L.append(line)
    if payload.get("basis") not in (None, "전체", "둘다", "all", "both"):
        L += ["", f"> ℹ️ **{payload.get('basis')} 기준만 받았다.** 다른 기준이 필요하면 "
                  f"`basis` 를 지정해 다시 부를 것 — 두 기준을 함께 받으면 시간이 두 배다.", ""]
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


_BASIS_ALL = ("전체", "둘다", "all", "both")


def _basis_wanted(basis: str) -> set[str] | None:
    """None 이면 전부. 기본은 **연결** — 받는 양과 호출 수가 절반이다."""
    b = (basis or "").strip()
    if b in _BASIS_ALL:
        return None
    if b in ("별도", "separate"):
        return {"별도"}
    return {"연결"}


async def _fetch_by_nodes(client, rcept_no: str, want: list[str], basis: str = "연결"):
    """목차 노드로 **필요한 절만** 받아온다. 못 하면 None → 호출자가 전체 문서로 폴백.

    🔴 260824 실측 — NH투자증권 사업보고서 19.5MB 를 통째로 읽으면 RSS 가 155MB 늘고
       1GB VM 에서 위험하다. 목차에는 주석 **항목마다** 좌표가 있다
       (「8. 사용이 제한된 예금 등 (연결)」 34KB). 필요한 것만 받는다.
    """
    from open_proxy_mcp.services.business_details import _extract_node_tree, _node_fetchable

    try:
        nodes = _extract_node_tree(await client._fetch_viewer_main_html(rcept_no))
    except Exception:                      # 뷰어가 막히면 전체 문서로 간다
        return None
    nodes = [n for n in nodes if _node_fetchable(n)]
    if not nodes:
        return None

    keep = _basis_wanted(basis)
    picked = [(b, n) for b, n in svc.pick_note_nodes(nodes, want)
              if keep is None or b in keep]
    if not picked:
        # 주석 항목 노드가 없는 서식 — 주석 **절**을 통으로 받는다(그래도 전체의 1/4~1/6)
        picked = [(b, n) for b, n in
                  svc.pick_chapter_nodes(nodes, ("연결재무제표 주석", "재무제표 주석"))
                  if keep is None or b in keep]
    if not picked:
        return None

    regions: list[tuple[str | None, str]] = []
    for basis, node in picked:
        try:
            regions.append((basis, await client._fetch_viewer_section_html(node)))
        except Exception:
            continue
    if not regions:
        return None

    # 분모 — 재무상태표는 「2. 연결재무제표」·「4. 재무제표」 절에 있다(각 수십~수백 KB).
    # 🔴 **사용제한을 안 물었으면 받지 않는다.** 뺄 계정이 없으면 분모도 쓸 데가 없고,
    #    viewer 는 호출마다 간격을 두므로 한 콜이 그대로 시간이다.
    sheets: dict[str | None, dict | None] = {}
    if "사용제한" not in want:
        return regions, sheets
    for b, node in svc.pick_chapter_nodes(nodes, ("연결재무제표", "재무제표")):
        if b in sheets or (keep is not None and b not in keep):
            continue
        try:
            sheets[b] = svc.balance_sheet_from(await client._fetch_viewer_section_html(node))
        except Exception:
            sheets[b] = None
    return regions, sheets


#: `year` 를 줬을 때 어떤 보고서를 찾을지. DART 표준 reprt_code —
#: 11011=사업 · 11012=반기 · 11013=1분기 · 11014=3분기.
_YEAR_CODES = {
    "annual": ("11011",), "사업": ("11011",),
    "half": ("11012",), "semiannual": ("11012",), "반기": ("11012",),
    "quarter": ("11013", "11014"), "분기": ("11013", "11014"),
    "1분기": ("11013",), "q1": ("11013",),
    "3분기": ("11014",), "q3": ("11014",),
    "quarterly": ("11012", "11013", "11014"),
    "latest": ("11011", "11012", "11013", "11014"),
}


async def _candidates(client, corp_code: str, period: str, year: str) -> list[dict]:
    """`year` 가 없으면 종류별 **최신** 한 건, 있으면 **그 사업연도**의 보고서.

    🔴 **260824 시험자 지적 — 과거 시점을 부를 길이 아예 없었다.** `period` 는 종류만
       고르고 그 종류의 최신 한 건을 쓴다. 표에 당기·전기가 함께 실려 **직전 기까지는**
       보이지만, 그 이상 과거는 못 봤다. 사업연도로 정확히 집는 조회기는
       `business_details` 에 이미 있었다(`_find_report_for_bsns_year`) — 그걸 쓴다.
       분기는 한 해에 두 번(1분기·3분기) 나오므로 `period="1분기"`/`"3분기"` 로
       콕 집을 수 있게 열어 둔다. 그냥 `quarter` 면 그 해의 **늦은 쪽**이 잡힌다.
    """
    y = (year or "").strip()
    if not y:
        return await _find_report_candidates(client, corp_code, period)
    codes = _YEAR_CODES.get((period or "latest").strip().lower()) or _YEAR_CODES["latest"]
    out: list[dict] = []
    seen: set[str] = set()
    for code in codes:
        # 🔴 **그 해에 그 보고서가 없으면 DART 는 013 을 던진다.** 260824 실측 —
        #    KB손해보험 `year=2019` 이 `DartClientError [013]` 로 그대로 터졌다.
        #    「없다」는 예외가 아니라 답이다 — 삼켜서 「찾지 못했습니다」로 내보낸다.
        try:
            found = await _find_report_for_bsns_year(client, corp_code, y, code)
        except DartClientError:
            continue
        for r in found:
            if r["rcept_no"] not in seen:
                seen.add(r["rcept_no"])
                out.append(r)
    out.sort(key=lambda r: r.get("rcept_dt", ""), reverse=True)
    return out


def register_tools(mcp):

    @mcp.tool()
    async def financial_notes(
        company: str,
        fields: str = "",
        period: str = "latest",
        basis: str = "연결",
        year: str = "",
        format: str = "md",
    ) -> str:
        """desc: **은행·증권·보험**의 연결/별도 **재무제표 주석** 표를 원형 그대로 추출. ①사용제한 예치금·담보제공자산(→unencumbered cash) ②투자자산 유형별 구성 FVPL·FVOCI·상각후원가(→유형별 헤어컷·자산건전성).
        when: 금융사 유동성(부채상환능력)·자산건전성 평가. 전사 집계는 `financial_metrics`, 「II.사업의 내용」은 `business_details`.
        rule: **표를 합치거나 나누지 않는다** — 회사마다 표 형태가 다른 것 자체가 정보다. 모든 값에 열 이름(기준 시점)이 붙어 있으니 **당기말/전기말을 반드시 구분**할 것(KB손보 사용제한: 전기말 391,082 → 당반기말 26,356으로 1/15). **단위가 회사마다 다르고, 같은 회사 안에서도 보고서마다 다르다** — 현대해상은 분기 `원` · 반기 `천원` · 사업 `원` 이라 **분기와 반기를 그대로 이으면 1,000배 어긋난다.** 응답 머리의 `단위` 를 매번 확인할 것. 「사용제한」과 「담보제공」은 `kind` 로 구분해 내보내며 **합치지 않는다** — 담보 제공은 소유권이 남고 사용제한은 인출이 막힌 것이라 회계상 다르다. unencumbered cash 계산·헤어컷 적용은 이 tool 밖이다.
        fields: 쉼표구분 — `사용제한,FVPL,FVOCI,상각후원가` (미지정 시 전부). 문서 다운로드는 회사당 1회라 한 번에 부르는 편이 싸다.
        period: `latest`(기본, 사업·반기·분기 중 최신 제출분) / `annual`(사업) / `half`(반기) / `quarter`(분기) / `quarterly`(분기+반기 중 최신). **`quarterly` 는 반기를 함께 잡으므로 분기만 보려면 `quarter` 를 쓸 것.**
        basis: `연결`(기본) / `별도` / `전체`. **기본이 연결인 이유는 받는 양과 호출 수가 절반이기 때문이다** — 두 기준을 다 받으면 시간이 두 배다. 별도가 필요하면 명시적으로 부를 것.
        year: 사업연도 `YYYY`. **비우면 그 종류의 최신 보고서 한 건**이고, 주면 그 해의 보고서를 집는다 — 추이를 보려면 해를 바꿔가며 부를 것(2024 → 2025 → 2026). 표에 당기·전기가 함께 실리므로 **직전 기까지는 `year` 없이도 보인다.** 분기는 한 해에 둘(1분기·3분기)이라 `period="1분기"`/`"3분기"` 로 콕 집을 수 있고, 그냥 `quarter` 면 늦은 쪽이 잡힌다. 🔴 **해마다 단위가 달라질 수 있으니**(현대해상: 분기 원 · 반기 천원 · 사업 원) 이을 때 응답 머리의 `단위` 를 매번 볼 것.
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

        cands = await _candidates(c, corp_code, period, year)
        if not cands:
            miss = (f"{year} 사업연도의 {period} 보고서를 찾지 못했습니다"
                    if (year or "").strip() else "정기보고서를 찾지 못했습니다")
            return json.dumps({"error": miss, "company": company,
                               "year": year, "period": period}, ensure_ascii=False)

        # 🔴 **먼저 목차 노드로 필요한 절만 받아본다.** 전체 문서는 최후 수단이다.
        node_hit = None
        for r_ in cands[:2]:
            node_hit = await _fetch_by_nodes(c, r_["rcept_no"], want, basis)
            if node_hit:
                report = r_
                break
        if node_hit:
            regions, sheets = node_hit
            notes = svc.extract_regions(regions, want, sheets)
            payload = {
                "company": name, "corp_code": corp_code,
                "report": {"report_nm": report.get("report_nm", "").strip(),
                           "rcept_no": report["rcept_no"], "rcept_dt": report.get("rcept_dt")},
                "fields": want, "notes": notes,
                "doc_chars": sum(len(h) for _, h in regions),
                "fetch": "nodes", "basis": basis,
            }
            if format == "json":
                env = ToolEnvelope(tool="financial_notes", status="OK", subject=name, data=payload)
                return json.dumps(env.to_dict(), ensure_ascii=False)
            return _render(payload)

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
