"""정관·의안과 증권 발행 공시에서 재검토할 원문을 고른다."""
from __future__ import annotations

import re
from open_proxy_mcp.services.control_severance import retirement_signals
from open_proxy_mcp.services.governance_disclosure_evidence import (
    LABELS, audit_candidate, exchange_terms, filing_facts, issue_changes, meeting_text_signals,
)
from open_proxy_mcp.services.screener_allotment import exchange_allotment
from open_proxy_mcp.services.seat_wording import seat_wording

PATTERNS = {
    **LABELS,
    "equity_stake_change": ("자회사 증자 후 지분율 변동", "완료 조건과 지분율, 연결 제외 여부를 확인한다."),
    "debt_setoff": ("증자 납입금과 채권 상계", "채권자와 금액, 납입 완료 및 최대주주 지분 변동을 확인한다."),
    "subsidiary_liquidation": ("자회사 청산을 위한 증자", "채무 상환 재원과 청산 진행 단계를 확인한다."),
    "replaces_allotment": ("기존 자회사 증자계획 대체", "기존 발행사와 새 발행사, 배정 방식과 철회 공시를 대조한다."),
    "seat_wording": ("이사 정원·선임 인원 문구", "인원 명시·허용·상하한을 구분한다. 이사회 전체 정원, 이번 선거의 의석, 후보자 수, 실제 당선 인원과 조건부 의안의 표결 결과를 원문으로 대조한다."),
    "retirement_limit": ("임원 퇴직금 별도 한도·보수한도 제외", "총 보수한도 밖에 두는 조항인지 별도 한도 금액이 있는지 확인한다. 주총 승인·적용기간·개인별 또는 총액 기준·실제 지급액을 대조한다."),
    "control_severance": ("경영권 변동 시 임원 특별퇴직보상", "정관·퇴직금 규정의 지급 조건·대상·산식을 확인하고 상정, 가결, 실제 지급을 구분한다."),
    "conditional_ballot": ("선행 안건에 따른 상정·폐기", "선행 안건의 가부에 따라 어느 대안·선임석이 표결에서 빠지는지, 실제 주총 결과와 회사 사유를 대조한다."),
    "bundled_amendments": ("정관 변경 묶음 표결 후보", "서로 다른 주주권 사항을 각각 찬반 표시할 수 있었는지 소집공고의 세부 의안번호와 표결지를 확인한다."),
    "staggered_terms": ("이사 임기 분산", "이사별 임기를 달리 정할 수 있는 조항과 실제 후보별 임기를 대조한다. 보궐선임이나 단순 임기 연장과 구별한다."),
    "allotment_authority": ("제3자배정 권한 변경", "정관 구·신조항의 배정 한도·사유가 실제로 확대됐는지, 가결 및 시행일을 확인한다."),
    "third_party_allotment": ("제3자배정 발행", "발행 근거 조항, 배정 상대방·선정 이유, 납입과 신주 발행, 정정·철회를 확인한다."),
    "convertible_bond": ("전환사채 발행", "회차, 인수인, 전환가·하향 조정 하한, 매도청구권 행사자, 전환 가능 주식 수·비율 및 실제 전환 여부를 확인한다."),
    "exchangeable_bond": ("교환사채 발행", "회차, 인수인, 교환가·수량과 교환대상 발행사를 확인한다. 자기주식이면 처분 및 실제 교부를, 타사주식이면 그 회사 지분 변화를 대조한다."),
}

ISSUE_KINDS = {"CB발행": "convertible_bond", "교환사채": "exchangeable_bond", "유상증자": "third_party_allotment"}
_ISSUE_TITLES = {"convertible_bond": r"전환사채권?발행결정", "exchangeable_bond": r"교환사채권?발행결정",
                 "third_party_allotment": r"유상증자결정"}
_CONDITION = re.compile(r"(?:가결|부결|승인)(?:될\s*(?:경우|때)|되[면어]|됨에\s*따라|\s*시).{0,65}?(?:폐기|미상정|상정|철회)")
_TERMS = re.compile(r"시차\s*임기|(?:이사.{0,20})?임기.{0,22}(?:분산|교차|달리\s*(?:정|하)|엇갈리)|(?:매년|매\s*주총).{0,18}이사의?\s*일부.{0,10}(?:선임|개선)")
# Keep the term and the per-director discretion in the same clause.
_DIRECTOR_DISCRETION = re.compile(
    r"임기(?:는|를|의)?[^.。;①-⑳]{0,100}?이사\s*별로\s*(?:(?:그\s*)?임기를?\s*)?"
    r"(?:달리|다르게)\s*(?:정할|할)\s*수\s*있")
_NOMINEE_TERM = re.compile(r"(?:사내이사|사외이사|독립이사|기타비상무이사)\s+([가-힣]{2,5})\s*선임"
                           r"(?:의\s*건)?\s*[(（,·:\s]*임기\s*[:：]?\s*(\d{1,2})\s*년")
_ALLOTMENT = re.compile(r"(?<!\d)(?:제\s*)?3\s*자\s*(?:에게|배정)|주주\s*외의?\s*자")
_TOPICS = {
    "전자주총·전자투표": r"전자.{0,10}(?:주주총회|투표)|원격지",
    "이사 선임·해임": r"집중투표|이사의?\s*(?:수(?!당)|원수|임기|선임|해임)",
    "감사위원": r"감사위원", "신주 배정": r"신주인수권|제3자배정",
    "주총 의결 요건": r"결의요건|의결정족수|특별결의|초다수", "배당": r"배당",
}


def issue_kind(title: str) -> str:
    compact = re.sub(r"\s+", "", title)
    return next((kind for kind, pattern in _ISSUE_TITLES.items() if re.search(pattern, compact)), "")


def _excerpt(text: str, match: re.Match) -> str:
    return text[max(0, match.start() - 85):match.end() + 115].strip()


def _limit_direction(before: str, after: str) -> str:
    def limit(clause: str):
        values = re.findall(r"(?:발행주식\s*총수|발행주식총수)의?\s*(?:100분의\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*%)", clause)
        return float(values[0][0] or values[0][1]) if len(values) == 1 else None

    old, new = limit(before), limit(after)
    if old is None or new is None:
        return "unverified"
    return "increased" if new > old else "decreased" if new < old else "unchanged"


def _staggered_marker(text: str):
    for match in _TERMS.finditer(text):
        tail = text[match.end():match.end() + 45]
        if not re.match(r"할\s*수\s*없|할\s*수\s*있지\s*않|제?(?:를|는|의|를\s*명시적으로)?\s*(?:폐지|배제|미도입|(?:도입|채택|적용)하지\s*않)", tail):
            return match
    return next((m for m in _DIRECTOR_DISCRETION.finditer(text)
                 if not re.search(r"보수|보상|수당|급여", m[0])), None)


def _charter_signals(html: str, text: str) -> dict:
    signals = meeting_text_signals(text)
    conditions = [m for m in _CONDITION.finditer(text) if re.search(r"의안|안건", text[max(0, m.start()-100):m.end()+40])
                  and not re.search(r"이사회에서?\s*$", text[max(0, m.start()-12):m.start()])
                  and not any(m[0] in signals[key]["quote"] for key in ("bundled_amendments","proposal_not_tabled") if key in signals)]
    match = next((m for m in conditions if re.search("폐기|미상정", m[0])), conditions[0] if conditions else None)
    if match:
        signals["conditional_ballot"] = {"quote": _excerpt(text, match)}
    terms = {m[1]: (m[2], m[0]) for m in _NOMINEE_TERM.finditer(text)}
    if len({years for years, _ in terms.values()}) > 1:
        signals.setdefault("staggered_terms", {})["nominee_terms"] = [
            {"name": name, "years": int(years), "quote": quote} for name, (years, quote) in terms.items()]
    if match := re.search(r"정관\s*변경.{0,75}?(?:일괄\s*표결|일괄\s*투표|하나의\s*안건으로\s*표결)(?!하지\s*않)", text):
        signals["bundled_amendments"] = {"quote": _excerpt(text, match)}
    from open_proxy_mcp.services.shareholder_meeting_parser import parse_aoi_xml

    amendments = parse_aoi_xml(html)["amendments"] if html and "정관" in text else []
    clauses = [row for a in amendments for row in [a, *(a.get("additionalClauses") or [])]]
    if match := _staggered_marker(text):
        # The flattened notice also contains the superseded column of each amendment.
        old_only = any(match[0] in row.get("before", "") and not _staggered_marker(row.get("after", ""))
                       for row in clauses)
        if not old_only:
            signals.setdefault("staggered_terms", {})["quote"] = _excerpt(text, match)
    signals.update(retirement_signals(text, html, clauses))
    if seats := seat_wording(text, clauses, conditional="conditional_ballot" in signals):
        signals["seat_wording"] = seats
    ballot_topics = {}
    for amendment in amendments:
        ballot = amendment.get("subAgendaId")
        for row in [amendment, *(amendment.get("additionalClauses") or [])]:
            before, after = row.get("before", ""), row.get("after", "")
            if not after or re.sub(r"\s", "", before) == re.sub(r"\s", "", after):
                continue
            clause = row.get("clause", "")
            remaining = re.sub(r"제\s*\d+\s*조(?:의\s*\d+)?(?:\s*\([^)]*\))?", "", after)
            if re.fullmatch(r"[\s①-⑳~()\d.∼～-]*(?:좌동|상동|현행과\s*(?:동일|같음)|생략)[\s).]*", remaining):
                continue
            detail = {"clause": clause, "before": before[:1200], "after": after[:1200], "reason": row.get("reason", "")[:300]}
            reason_identifies_issue = (_ALLOTMENT.search(detail["reason"])
                                       and re.search(r"신주(?:를|의)?\s*발행", before + after))
            if _ALLOTMENT.search(before + after) or reason_identifies_issue:
                signals.setdefault("allotment_authority", {"clauses": []})["clauses"].append(
                    {**detail, "cap_direction": _limit_direction(before, after)})
            if term_marker := _staggered_marker(after) or _staggered_marker(row.get("reason", "")):
                term = signals.setdefault("staggered_terms", {})
                term["clause_change"] = detail
                term.setdefault("quote", term_marker[0])
            if not ballot or not clause:
                continue
            topics = [label for label, pattern in _TOPICS.items() if re.search(pattern, before + after)]
            ballot_topics.setdefault(ballot, {}).setdefault(clause, set()).update(topics)
    for ballot, clauses in ballot_topics.items():
        known = {c: sorted(topics) for c, topics in clauses.items() if topics}
        if len({tuple(topics) for topics in known.values()}) >= 2:
            signals.setdefault("bundled_amendments", {}).setdefault("ballots", []).append(
                {"agenda": ballot, "clauses_and_topics": known})
    return signals


_BOND_FIELDS = {"series": "SEQ_NO", "amount_won": "DNM_SUM", "method": "ISSU_MTH",
                "price_won": "EXE_PRC", "target_stock": "STK_KND", "potential_shares": "STK_CNT",
                "reported_share_pct": "STK_RT", "adjustment_floor_won": "MIN_PRC"}
_ALLOTMENT_FIELDS = {"method": "CI_MTH", "charter_basis": "ART_BAS", "new_common_shares": "CST_CNT",
                     "existing_common_shares": "BFR_CST_CNT", "price_won": "CST_ISS_VAL"}


def _exchange_target(html: str, target: str) -> str:
    """Compare named issuer and underlying; generic share classes cannot identify a company."""
    name = re.search(r"<COMPANY-NAME\b[^>]*>([^<]+)</COMPANY-NAME>", html, re.I)
    def normalize(value: str) -> str:
        return re.sub(r"주식회사|\(주\)|㈜|\s+", "", value)
    if re.fullmatch(r"(?:발행회사|당사|자사)의?\s*(?:기명식\s*)?(?:보통주식?|자기주식)|자기주식|자사주", target.strip()):
        return "issuer_shares"
    own_label = re.search(r"\s*[(（]\s*(?:자기\s*주식|자사주)\s*[)）]\s*$", target)
    stock = target[:own_label.start()].strip() if own_label else target.strip()
    company = re.fullmatch(r"(.+?)(?:\s*발행)?\s*(?:기명식\s*)?(?:보통주식?|우선주식?)", stock)
    if name and company:
        base = normalize(company[1])
        if base == normalize(name[1]):
            return "issuer_shares"
        if base and not own_label and re.search(r"주식회사|\(주\)|㈜", company[1]):
            return "other_company_shares"
    return "unverified"


def governance_detail_lines(signal: dict) -> list[str]:
    if signal.get("prior_term"):
        term = signal["prior_term"]
        return [f"  - {signal['candidate_name']} · 종전 임기 {term['end']} · 주총 {signal['meeting_date']} · [기존 임기 공시]({term['dart_url']})"]
    if change := signal.get("clause_change"):
        return [f"  - {change['clause']} · 변경 전 {change['before']} / 변경안 {change['after']}"]
    if signal["pattern"] == "staggered_terms" and signal.get("quote"):
        return [f"  - 임기 원문 · {signal['quote']}"]
    terms = signal.get("terms", {})
    if basis := terms.get("target_basis"):
        label = {"issuer_shares": "발행회사 주식", "other_company_shares": "타사 주식", "unverified": "발행사 미확인"}
        return [f"  - 교환대상 구분 · {label[basis]} · {terms.get('target_stock') or '대상 주식명 미기재'}"]
    return []


def _field(html: str, code: str) -> str:
    from open_proxy_mcp.services.treasury_share import _extract_acode

    value = _extract_acode(html, code)
    if not value and code in {"CI_MTH", "ISSU_MTH"}:
        # DART dropdowns store their Korean display text in TU/AUNIT.
        match = re.search(rf'<TU\b[^>]*\bAUNIT="{code}"[^>]*>(.*?)</TU>', html, re.S)
        if match:
            value = re.sub(r"<[^>]+>", "", match[1]).strip()
    return value.strip() if value and value.strip() not in {"-", "—"} else ""


def allotment_method_unknown(title: str, doc: dict) -> bool:
    return issue_kind(title) == "third_party_allotment" and not (_field(doc.get("html") or "", "CI_MTH") or exchange_allotment(doc.get("html") or ""))


def _issue_signal(kind: str, html: str, text: str, allotment: dict) -> dict:
    from open_proxy_mcp.services.treasury_share import _extract_acode

    mapping = _ALLOTMENT_FIELDS if kind == "third_party_allotment" else _BOND_FIELDS
    terms = {}
    for label, code in mapping.items():
        value = _field(html, code)
        if value:
            terms[label] = " ".join(value.split())[:250]
    facts = {}
    if allotment:
        terms.update(method=allotment["method"], **allotment["terms"])
        if allotment["issuer"]:
            terms["issuing_subsidiary"] = allotment["issuer"]
        facts = {key: {"quote": quote, "terms": {"method": allotment["method"], "issuing_subsidiary": allotment["issuer"]}}
                 for key, quote in allotment["facts"].items()}
    if kind == "third_party_allotment" and not _ALLOTMENT.search(terms.get("method", "")):
        return facts
    name_field = "PART" if kind == "third_party_allotment" else "ISSU_NM"
    subscribers = []
    for row in re.findall(r"<TR\b[^>]*>[\s\S]*?</TR>", html, re.I):
        name = _extract_acode(row, name_field)
        if name and name.strip() not in {"-", "—"}:
            subscribers.append({"name": name[:160], "reported_relationship": _extract_acode(row, "RLT")})
    if not subscribers and allotment:
        subscribers = allotment["subscribers"]
    options = _extract_acode(html, "OPT_FCT") or ""
    call = re.search(r"매도\s*청구권|콜\s*옵션|call\s*option", options, re.I)
    if call:
        terms["call_option_excerpt"] = _excerpt(options, call)
        section = " ".join(options[call.start():].split())
        cap = re.search(r"취득\s*규모\s*[:：]\s*(?:최대\s*)?([0-9,]+)\s*원\s*[(（]\s*(?:콜\s*옵션|call\s*option)\s*([0-9.]+)\s*%", section, re.I)
        if cap:
            terms.update(call_limit_won=cap[1], call_limit_pct=cap[2])
        adjusted = re.search(r"전환가액\s*조정[^.。]{0,65}?후에는\s*최대\s*([0-9,]+)\s*주까지\s*취득", section)
        if adjusted:
            terms["call_adjusted_max_shares"] = adjusted[1]
    available = bool(terms)
    if kind == "exchangeable_bond":
        terms["target_basis"] = _exchange_target(html, terms.get("target_stock", ""))
        if not terms.get("target_stock") or terms["target_basis"] == "unverified":
            exchange_terms(html, terms)
        available = bool(terms.get("target_stock") or available)
    return {**facts, kind: {"terms": terms, "subscribers": subscribers[:15], "subscriber_count": len(subscribers),
                   "terms_available": available, "source_text_available": bool(text)}}


def inspect_filing(title: str, doc: dict, *, allotment: dict | None = None) -> list[dict]:
    """One receipt at a time. Positive matches are reading handles, not adverse conclusions."""
    text = " ".join((doc.get("text") or "").split())
    html = doc.get("html") or ""
    if not title and html:
        header = re.search(r"<DOCUMENT-NAME\b[^>]*>([^<]+)</DOCUMENT-NAME>", html, re.I)
        if header:
            title = header[1]
            if re.search(r"<TITLE\b[^>]*>\s*정\s*정\s*신\s*고", html[:20000], re.I):
                title = "[정정]" + title
    kind = issue_kind(title)
    state = "결과 공시" if "결과" in title else "결정·상정 공시"
    if "철회" in title or (kind and re.search(r"(?:사채\s*발행|유상증자)(?:\s*결정)?[을의를\s]*철회(?:를?\s*결정|하기로|합니다|하였)", text[:4000])):
        return [{"pattern": kind, "label": PATTERNS[kind][0], "check": "철회 공시와 원안의 회차·범위를 확인한다.",
                 "filing_state": "철회 공시"}] if kind in {"convertible_bond", "exchangeable_bond"} else []
    correction_reasons = []
    if "정정" in title:
        state = "정정 요구 · 효력 확인" if "정정명령" in title or "정정요구" in title else "정정 공시 · 정정 범위 확인"
        # The amendment comparison table may still describe superseded ballots.
        if not kind:
            body = re.search(r"<TITLE\b[^>]*>\s*주주총회\s*소집공고\s*</TITLE>|"
                             r"<COVER-TITLE\b[^>]*>\s*증\s*권\s*신\s*고\s*서\s*</COVER-TITLE>", html, re.I)
            if body:
                from open_proxy_mcp.dart.client import html_to_text
                from open_proxy_mcp.services.amendment_table import parse_amendment_table
                correction = parse_amendment_table(html[:body.start()])
                correction_reasons = list(dict.fromkeys(filter(None, [
                    correction["reason"], *(row.get("reason") for row in correction["rows"])])))
                html = html[body.start():]
                text = " ".join(html_to_text(html).split())
    if kind:
        if allotment is None and kind == "third_party_allotment" and not _field(html, "CI_MTH"):
            allotment = exchange_allotment(html)
        signals = _issue_signal(kind, html, text, allotment or {})
        if "정정" in title and kind in signals:
            signals.update(issue_changes(html, kind, signals[kind].get("terms", {})))
    elif "주주총회" in title.replace(" ", ""):
        signals = _charter_signals(html, text)
    elif not title or any(word in title for word in ("증권신고서", "정관", "투자설명서", "사업보고서")):
        signals = retirement_signals(text, html, read_amendments=False)
        if "정관" in title:
            if seats := seat_wording(text, []):
                signals["seat_wording"] = seats
            if match := _staggered_marker(text):
                signals["staggered_terms"] = {"quote": _excerpt(text, match)}
        if "정정" not in title:
            state = "공시 기재 · 적용 시점 확인"
    else:
        signals = {}
    if "audit_reappointment" in signals:
        audit_candidate(signals["audit_reappointment"], html)
    signals.update(filing_facts(title, html, text))
    # The issuer may explain the scope of the pay limit only in the reason for a correction.
    # Read that cell separately so a superseded value cannot become a current cap.
    for reason in correction_reasons:
        evidence = retirement_signals(reason).get("retirement_limit")
        if not evidence:
            continue
        signal = signals.setdefault("retirement_limit", {"quote": evidence["quote"], "provisions": []})
        known = {p["quote"] for p in signal["provisions"]}
        signal["provisions"].extend({**p, "clause": "정정사유"} for p in evidence["provisions"] if p["quote"] not in known)
    return [{"pattern": key, "label": PATTERNS[key][0], "check": PATTERNS[key][1], "filing_state": state, **evidence}
            for key, evidence in signals.items()]
