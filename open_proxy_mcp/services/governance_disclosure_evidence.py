"""Receipt-scoped governance facts. Keep conditions, corrections and execution distinct."""

from __future__ import annotations

import re
from datetime import date

from open_proxy_mcp.services.doc_tables import compact, distinct, table_grids

LABELS = {
    "proposal_not_tabled": (
        "이사회 부결로 주주제안 미상정",
        "미상정한 의안과 별도로 상정한 주주 추천 후보를 구분한다.",
    ),
    "audit_reappointment": ("현직 감사위원 사임 후 분리 재선임", "기존 임기와 주총 결과는 별도 공시로 대조한다."),
    "conversion_advanced": ("전환청구 개시일 앞당김", "같은 회사·회차의 전환청구와 새 주식 수를 대조한다."),
    "payment_postponed": ("제3자배정 납입일 연기", "변경 후 납입일과 납입 완료를 구분한다."),
    "treasury_exchange": (
        "자기주식이 교환대상인 사채",
        "최초 발행, 정정, 실제 교환, 간주 수량과 환입 예정을 구분한다.",
    ),
    "conversion_exercised": ("회차별 전환청구", "전환청구와 상장 완료는 각각 확인한다."),
    "allotment_paid": ("제3자배정 납입 결과", "실제 발행 주식과 납입일을 배정 결정과 대조한다."),
    "related_guarantee": ("계열사 보증 부담", "보증한도와 실제 발생한 손실을 구분한다."),
    "related_loan_extension": ("계열사 기존 대여금 연장", "연장한 금액과 신규 대여를 구분한다."),
    "listing_protection": ("자회사 상장과 주주보호방안", "현물배당 대상과 시점, 실제 이행 여부를 확인한다."),
    "listing_withdrawn": ("자회사 상장 철회", "철회된 상장 계획과 재추진을 구분한다."),
    "governance_vote": ("정관·보상 의안 표결 결과", "해당 주총의 의안번호와 원공고 내용을 대조한다."),
}

# Only route filings with relevant subtypes into the document-reading view.
EXTRA_SOURCES = {
    "treasury": {"처분"},
    "treasury_result": {"처분결과"},
    "capital_followup": {"권리행사", "발행결과"},
    "related_party": None,
    "guarantee_lending": {"채무보증"},
    "subsidiary_listing": None,
}


def field_rows(html):
    return [distinct(row) for grid in table_grids(html) for row in grid]


def _value(rows, label):
    for row in rows:
        for i, cell in enumerate(row[:-1]):
            if re.fullmatch(label, compact(cell)):
                return row[i + 1].strip()
    return ""


def _evidence(rows):
    return {"quote": " / ".join(" · ".join(str(v) for v in row if v) for row in rows)}


def meeting_text_signals(text):
    out = {}
    negative = re.search(
        r"(?:이사회에서?\s*)?부결(?:되어|됨에\s*따라).{0,38}?(?:주주총회|주총)\s*(?:안건|의안)(?:으로|에)?\s*상정(?:되지\s*(?:않|아니)|하지\s*(?:않|아니))[^.]{0,14}",
        text,
    )
    if negative:
        context = text[max(0, negative.start() - 740) : negative.start()]
        request = list(re.finditer(r"주주(?:제안|\s*\d[\d,]*\s*(?:명|인)|들이?\s*(?:제안|청구))", context))
        if request and re.search(r"제안|청구|요구", context[request[-1].start() :]):
            out["proposal_not_tabled"] = {"quote": context[request[-1].start() :] + negative[0]}
    audit = re.search(
        r"(?:현재|기존|현직).{0,45}?감사위원(?:회\s*위원)?.{0,32}?사임\s*후.{0,65}?분리\s*(?:선출|선임)\s*[(（]?\s*재선임[)）]?",
        text,
    )
    if audit:
        out["audit_reappointment"] = {"quote": audit[0]}
    bundle = re.search(
        r"(?:일부\s*조항만|(?:여러|각)\s*조항).{0,95}?하나의\s*(?:의안|안건)으로\s*(?:상정|표결)(?:합니다|한다|하였|했습니다)",
        text,
    )
    if bundle:
        out["bundled_amendments"] = {"quote": bundle[0]}
    return out


_DAY = r"\d{4}\s*(?:[./-]|년)\s*\d{1,2}\s*(?:[./-]|월)\s*\d{1,2}일?"


def _iso(text, start=False):
    match = re.search((r"시작일\s*[:：]?\s*" if start else "") + "(" + _DAY + ")", text)
    if match:
        try:
            return date(*(int(x) for x in re.findall(r"\d+", match[1]))).isoformat()
        except ValueError:
            pass
    return ""


def issue_changes(html, kind, existing):
    out = {}
    for row in field_rows(html):
        if len(row) != 4:
            continue
        item, reason, old, new = row
        if kind == "convertible_bond" and "전환청구기간" in compact(item):
            before, after = _iso(old, True), _iso(new, True)
            key, label = "conversion_advanced", "전환 개시일"
            if not after or not before or after >= before:
                continue
        elif kind == "third_party_allotment" and re.fullmatch(r"(?:\d+\.)?납입일", compact(item)):
            before, after = _iso(old), _iso(new)
            key, label = "payment_postponed", "납입일"
            if not after or not before or after <= before:
                continue
        else:
            continue
        out[key] = {
            "before_date": before,
            "after_date": after,
            "series": existing.get("series"),
            "quote": " · ".join((label, reason, old, new)),
        }
    return out


def exchange_terms(html, terms):
    rows = field_rows(html)
    target = next(
        (
            row[-1]
            for row in rows
            if len(row) >= 3 and compact(row[-3]) == "교환대상" and compact(row[-2]) == "종류"
        ),
        "",
    )
    if not target:
        return
    terms.setdefault("target_stock", target)
    issuer = _value(rows, r"자회사인|종속회사인")
    if issuer:
        terms["issuing_subsidiary"] = issuer
        normalize = lambda s: re.sub(r"주식회사|㈜|\(주\)|\s+", "", s)
        name = re.fullmatch(r"(.+?)\s*(?:기명식\s*)?(?:보통|우선)주(?:식)?", target)
        if name and re.search(r"주식회사|㈜|\(주\)", name[1]):
            terms["target_basis"] = (
                "issuer_shares" if normalize(name[1]) == normalize(issuer) else "other_company_shares"
            )


def _treasury(text):
    # In amended forms the body follows the amendment comparison and repeats historical quantities.
    start = list(
        re.finditer(
            r"(?:자기주식처분결과보고서\s*(?:금융위원회|한국거래소|회\s*사\s*명)|1\.\s*발행인의\s*명칭\s*및\s*주소|1\.\s*처분예정주식\s*[(（]주[)）]\s*보통주식)",
            text,
        )
    )
    current = text[start[-1].start() :] if start else text
    if not re.search(
        r"(?:자기주식|자사주).{0,42}교환(?:대상|사채)|교환(?:대상|사채).{0,42}(?:자기주식|자사주)", current
    ):
        return {}
    purpose = re.search(r"처분\s*목적\s*([^\n]{2,140}?교환사채(?:의)?\s*발행)", current)
    quantity = list(
        re.finditer(r"[\d,]+\s*주(?:가|는|를)?\s*교환(?:청구된\s*것으로\s*간주|되었으며|되었습니다)", current)
    )
    if not purpose and not quantity:
        return {}
    match = quantity[-1] if quantity else purpose
    evidence = {"target_basis": "issuer_shares", "quote": current[max(0, match.start() - 42) : match.end() + 160]}
    if purpose and quantity:
        evidence["quote"] = purpose[0] + " / " + evidence["quote"]
    return {"treasury_exchange": evidence}


def _vote(rows):
    votes = []
    for row in rows:
        if (
            len(row) < 4
            or not re.fullmatch(r"(?:제\s*)?\d+(?:-\d+)?(?:\s*호)?", row[0])
            or row[3] not in {"가결", "부결", "폐기", "철회"}
        ):
            continue
        if re.search(r"정관|퇴직금|보수규정|분리선출", compact(row[2])):
            votes.append({"agenda": row[0], "subject": row[2], "outcome": row[3]})
    return (
        {
            "governance_vote": {
                "meeting_date": _iso(_value(rows, r"(?:\d+\.)?주주총회일자")),
                "votes": votes,
                "quote": " / ".join(f"{v['agenda']} {v['subject']} {v['outcome']}" for v in votes),
            }
        }
        if votes
        else {}
    )


def filing_facts(title, html, text):
    title = compact(title)
    if "자기주식처분" in title:
        return _treasury(text)
    if "주주총회결과" in title:
        return _vote(field_rows(html))
    if not any(
        key in title for key in ("채무보증", "자금대여", "금전대여", "전환청구권행사", "증권발행결과", "상장")
    ):
        return {}
    rows = field_rows(html)
    if "채무보증" in title:
        role = _value(rows, r"-?회사와의관계")
        debtor = _value(rows, r"1\.채무자")
        amount = _value(rows, r"채무보증금액\(원\)")
        pct = _value(rows, r"자기자본대비\(%\)")
        if (
            debtor
            and re.fullmatch(r"계열회사|종속회사|자회사|관계회사|계열사", role)
            and re.fullmatch(r"[\d,.]+", pct)
            and amount
        ):
            return {
                "related_guarantee": {
                    "quote": f"{role} {debtor} · 보증한도 {amount}원 · 자기자본 대비 {pct}%",
                    "equity_pct": pct,
                    "guarantee_won": amount,
                }
            }
    elif "대여" in title:
        role = _value(rows, r"회사와의관계")
        purpose = _value(rows, r"(?:\d+\.)?거래의목적")
        if role in {"계열회사", "종속회사", "관계회사", "자회사"} and re.search(r"만기\s*연장", purpose):
            party = _value(rows, r"1\.거래상대방")
            amount = _value(rows, r"나\.거래금액")
            unit = re.search(r"단위\s*[:：]\s*((?:천|백만|억)?\s*원)", text)
            return {
                "related_loan_extension": _evidence(
                    [[role, party, purpose], ["거래금액", amount, unit[1] if unit else "단위 확인"]]
                )
            }
    elif "전환청구권행사" in title:
        entries = []
        for grid in table_grids(html):
            if not any("청구일자" in row and "전환가액" in row for row in grid):
                continue
            for row in grid:
                if len(row) == 7 and re.fullmatch(_DAY, row[0]) and re.fullmatch(r"\d+", row[1]):
                    entries.append(
                        dict(zip(("date", "series", "name", "amount", "price", "shares", "listing_date"), row))
                    )
        if entries:
            pct = _value(rows, r"-?발행주식총수대비\(%\)")
            return {
                "conversion_exercised": {
                    "exercises": entries,
                    "reported_share_pct": pct,
                    "quote": " / ".join(
                        f"{r['date']} 제{r['series']}회 {r['amount']} · 전환가액 {r['price']}원 · {r['shares']}주"
                        for r in entries
                    )
                    + (f" · 공시 전체 {pct}%" if pct else ""),
                }
            }
    elif "증권발행결과" in title:
        if _value(rows, r"(?:\d+\.)?발행방법").replace(" ", "") != "제3자배정유상증자":
            return {}
        shares, amount, paid = (
            _value(rows, k) for k in (r"실제발행주식수\(주\)", r"실제발행금액\(원\)", r"납입일")
        )
        if paid and amount and shares and re.fullmatch(r"[1-9][\d,]*", shares) and _iso(paid):
            return {
                "allotment_paid": {
                    **_evidence([["제3자배정 실제발행", shares + "주", amount + "원", "납입일 " + paid]]),
                    "resolution_date": _iso(_value(rows, r"발행결정최초이사회결의일")),
                    "shares": shares,
                }
            }
    elif "상장" in title:
        if "상장철회" in compact(_value(rows, r"(?:\d+\.)?정정사유")):
            return {"listing_withdrawn": {"quote": "정정사유 · " + _value(rows, r"(?:\d+\.)?정정사유")}}
        plan = _value(rows, r"(?:\d+\.)?주주보호방안주요내용")
        if re.search(r"현물\s*배당", plan):
            return {"listing_protection": {"quote": plan[:530]}}
    return {}


def _named_rows(html, name, required):
    for grid in table_grids(html):
        for i, row in enumerate(grid):
            header = [compact(c) for c in row]
            if not {"성명", *required} <= set(header):
                continue
            for values in grid[i + 1 :]:
                record = dict(zip(header, values))
                if record.get("성명") == name:
                    yield record
            break


def audit_candidate(signal, html):
    name = re.search(
        r"(?:감사위원(?:회\s*위원)?\s*[(（]\s*([가-힣]{2,5})\s*[)）]|감사위원(?:회\s*위원)?\s+([가-힣]{2,5}))(?:이|가)?\s*사임",
        signal["quote"],
    )
    if not name:
        return
    name = name[1] or name[2]
    birth = {
        re.sub(r"\D", "", row["출생년월"])
        for row in _named_rows(html, name, {"출생년월", "신규선임여부"})
        if "재선임" in row["신규선임여부"]
    }
    if len(birth) == 1 and re.fullmatch(r"\d{6}", next(iter(birth))):
        signal.update(candidate_name=name, birth_month=birth.pop())
    for row in field_rows(html):
        if len(row) >= 3 and compact(row[0]) == "1.일시" and row[1] == "날짜" and (day := _iso(row[2])):
            signal["meeting_date"] = day
            break


def prior_audit_term(html, signal):
    # Do not associate a namesake or a differently appointed officer with this nominee.
    matches = set()
    for row in _named_rows(html, signal["candidate_name"], {"출생년월", "임기만료일", "담당업무"}):
        if re.sub(r"\D", "", row["출생년월"]) == signal["birth_month"] and re.search(
            r"감사위원(?:회\s*위원)?", row["담당업무"]
        ):
            if end := _iso(row["임기만료일"]):
                matches.add(end)
    return matches.pop() if len(matches) == 1 else None


_VOTE_SUBJECTS = {
    "seat_wording": r"이사의?수|이사총수|이사정원",
    "control_severance": r"특별퇴직|퇴직금|퇴직위로금",
    "retirement_limit": r"퇴직금|보수규정|보수한도",
    "allotment_authority": r"신주인수권|신주발행|사채발행",
}


def confirmed_followup(signal, original, later_signals):
    """Match by series, issuance resolution date and quantity, or meeting day and ballot."""
    kind = signal["pattern"]
    if kind == "conversion_advanced":
        for later in later_signals:
            if later["pattern"] == "conversion_exercised":
                entries = [
                    r
                    for r in later["exercises"]
                    if r["series"] == signal.get("series") and r["date"] >= signal["after_date"]
                ]
                if entries:
                    return " / ".join(
                        f"제{r['series']}회 · {r['date']} {r['amount']} · {r['shares']}주 전환청구"
                        for r in entries
                    )
    elif kind == "third_party_allotment":
        terms = signal["terms"]
        orig_date = _iso(_value(field_rows(original["html"]), r"(?:\d+\.)?이사회결의일(?:\(결정일\))?"))
        for later in later_signals:
            if (
                later["pattern"] == "allotment_paid"
                and orig_date
                and orig_date == later["resolution_date"]
                and terms.get("new_common_shares", "").replace(",", "") == later["shares"].replace(",", "")
            ):
                return later["quote"]
    elif kind in _VOTE_SUBJECTS:
        from open_proxy_mcp.services.shareholder_meeting_parser import _meeting_datetime_fallback

        notice = " ".join(original["text"].split())
        meeting_day = _iso(_meeting_datetime_fallback(notice) or "")
        for later in later_signals:
            if later["pattern"] != "governance_vote" or not meeting_day or meeting_day != later["meeting_date"]:
                continue
            for v in later["votes"]:
                number = re.search(r"\d+(?:-\d+)?", v["agenda"])[0]
                # Keep a generic charter ballot only if the notice identifies ONE charter ballot.
                subject = compact(v["subject"])
                ballots = re.findall(
                    r"제\s*(\d+(?:-\d+)?)\s*호\s*의안\s*[:：]?\s*정관\s*(?:일부\s*)?(?:변경|개정)", notice
                )
                target = re.search(_VOTE_SUBJECTS[kind], subject)
                generic = subject in {"정관일부변경의건", "정관변경의건"} and set(ballots) == {number}
                if (target or generic) and re.search(r"제\s*" + re.escape(number) + r"\s*호\s*의안", notice):
                    return f"{meeting_day} · {v['agenda']} {v['subject']} · {v['outcome']}"
    return ""
