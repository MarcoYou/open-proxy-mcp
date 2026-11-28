"""Join a notice's charter comparison to a published vote for the same meeting."""
from __future__ import annotations

import re

from open_proxy_mcp.services.governance_disclosure_evidence import filing_facts
from open_proxy_mcp.services.shareholder_meeting import _parse_notice_meeting_date
from open_proxy_mcp.services.shareholder_meeting_parser import parse_agenda_xml, parse_aoi_xml, parse_meeting_info_xml


def _number(value):
    match = re.fullmatch(r"(?:제)?(\d+(?:-\d+)*)(?:호)?(?:의안)?", re.sub(r"\s+", "", str(value or "")))
    return match[1] if match else ""


def charter_document(title, doc):
    html, text = doc.get("html") or "", doc.get("text") or ""
    compact = re.sub(r"\s+", "", title)
    if "주주총회결과" in compact:
        return filing_facts(title, html, text).get("governance_vote")
    if "주주총회소집공고" not in compact or not html:
        return None
    amendments = parse_aoi_xml(html)["amendments"]
    if not amendments:
        return None
    agenda_titles = {}
    for item in parse_agenda_xml(text, html):
        if "정관" in item.get("title", ""):
            for node in [item, *item.get("children", [])]:
                number = _number(node.get("number") or node.get("id"))
                if number:
                    agenda_titles[number] = node.get("title", "")
    clauses = []
    for amend in amendments:
        number = _number(amend.get("subAgendaId"))
        for row in [amend, *amend.get("additionalClauses", [])]:
            before, after = row.get("before", ""), row.get("after", "")
            if not row.get("clause") or not after or before.strip() == after.strip():
                continue
            supplement = re.match(r"부\s*칙\s*(제\s*\d+\s*조(?:\s*\([^)]*\))?)", after)
            label = "부칙 " + supplement[1] if supplement else row["clause"]
            clauses.append(dict(label=label, before=before, after=after, reason=row.get("reason", ""),
                                agenda=number, subject=agenda_titles.get(number, "")))
    if clauses:
        day = _parse_notice_meeting_date(parse_meeting_info_xml(text, html).get("datetime", ""))
        return {"meeting_date": day.isoformat() if day else "", "clauses": clauses}


def charter_meetings(filings):
    docs = {item["rcept_no"]: item["charter_document"] for item in filings if item.get("charter_document")}
    meetings = []
    notice_dates = {rc: doc["meeting_date"] for rc, doc in docs.items() if "clauses" in doc}
    for receipt, doc in docs.items():
        if "clauses" not in doc:
            continue
        day = doc["meeting_date"]
        results = [(rc, other["votes"]) for rc, other in docs.items()
                   if day and other.get("meeting_date") == day and "votes" in other and rc >= receipt]
        superseded = any(later > receipt and when == day for later, when in notice_dates.items())
        ballots = {}
        for clause in doc["clauses"]:
            number = clause["agenda"]
            if number not in ballots:
                matches = [(rc, v) for rc, votes in results for v in votes
                           if number and _number(v["agenda"]) == number and "정관" in v["subject"]]
                children = any(_number(v["agenda"]).startswith(number + "-") for _, votes in results for v in votes)
                match = matches[0] if len(matches) == 1 and not children and not superseded else None
                ballots[number] = {"number": number, "subject": match[1]["subject"] if match else clause["subject"],
                                   "outcome": match[1]["outcome"] if match else "", "result_receipt": match[0] if match else "",
                                   "clauses": []}
            ballots[number]["clauses"].append({key: clause[key] for key in ("label", "before", "after", "reason")})
        meetings.append({"meeting_date": day, "notice_receipt": receipt, "ballots": list(ballots.values())})
    return meetings
