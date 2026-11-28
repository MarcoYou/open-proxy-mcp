"""Read an attachment's own amendment table and date-limit its DART family links."""
from __future__ import annotations

import re
from urllib.parse import parse_qs

from bs4 import BeautifulSoup

from open_proxy_mcp.services.dart_safety import DART_EXTERNAL_ERRORS
from open_proxy_mcp.dart.client import dart_halts_batch
from open_proxy_mcp.services.governance_patterns import _field
from open_proxy_mcp.services.amendment_table import parse_amendment_table

_VIEW = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
# KRX standalone HTML has a literal viewDoc call instead of a section tree.
_DIRECT = re.compile(r'viewDoc\(\s*"(\d{14})",\s*"(\d+)",\s*"(\d+)",\s*"(\d+)",\s*"(\d+)",\s*"(HTML)"')


def _entries(main, rc, cutoff):
    soup = BeautifulSoup(main, "html.parser")
    family, attachments = {}, {}
    for select in ("family", "att"):
        for option in soup.select(f"#{select} option[value]"):
            query = parse_qs(option["value"])
            label = " ".join(option.get_text().split())
            date = re.match(r"(\d{4})\.(\d\d)\.(\d\d)", label)
            if not date or "".join(date.groups()) > cutoff:
                continue
            if select == "family" and re.fullmatch(r"rcpNo=\d{14}", option["value"]):
                member = query["rcpNo"][0]
                if member[:8] <= cutoff:
                    family[member] = {"rcept_no": member, "title": label, "dart_url": _VIEW + member}
            if select == "att" and query.get("rcpNo") == [rc] and set(query) == {"rcpNo", "dcmNo"}:
                numbers = query["dcmNo"]
                if len(numbers) == 1 and re.fullmatch(r"\d{1,20}", numbers[0]):
                    attachments[numbers[0]] = {"title": label, "dcm_no": numbers[0],
                                              "dart_url": _VIEW + rc + "&dcmNo=" + numbers[0]}
    return family, attachments


def withdrawal_quote(doc):
    correction = parse_amendment_table(doc.get("html", ""))
    reason = correction.get("reason") or ""
    resolved = r"철회(?!\s*(?:하지|예정|여부|가능|검토|되지|할\s*수))"
    if m := re.search(r"(?:발행|증자|계획|결정).{0,20}?" + resolved, reason):
        return reason[max(0, m.start()-25):m.end()+45]
    for row in correction["rows"]:
        after = row["after"]
        if m := re.search(r"(?:전환사채권?\s*발행|유상증자)(?:\s*결정)?(?:을|를|의)?\s*" + resolved, after):
            return after[max(0, m.start()-35):m.end()+55]
    text = " ".join((doc.get("text") or "").split())
    match = re.search(r"(?:본건|당사는|당사는 이에|금번).{0,100}?(?:사채\s*발행|유상증자)(?:\s*결정)?[을를\s]*철회(?:하기로|합니다|하였)", text)
    return match[0] if match else ""


async def _latest(client, family, rc):
    if not family or (latest := max(family)) <= rc:
        return {}
    ref = family[latest].copy()
    try:
        doc = await client.get_document_cached(latest) or {}
    except DART_EXTERNAL_ERRORS as exc:
        if dart_halts_batch(exc):
            raise
        return {**ref, "status": "body_unread"}
    if quote := withdrawal_quote(doc):
        ref.update(status="withdrawn", quote=quote)
    else:
        ref["status"] = "later_version" if doc.get("text") else "body_unread"
        if "전환사채" in ref["title"]:
            for key, acode in (("series", "SEQ_NO"), ("price_won", "EXE_PRC"), ("potential_shares", "STK_CNT")):
                value = _field(doc.get("html", ""), acode)
                if re.fullmatch(r"[\d,]+", value):
                    ref[key] = value
    return ref


async def allotment_followup(client, rc, cutoff):
    main = await client._fetch_viewer_main_html(rc)
    family, _ = _entries(main, rc, cutoff)
    return await _latest(client, family, rc) if rc in family else {}


async def attachment_amendment(client, rc, cutoff):
    main = await client._fetch_viewer_main_html(rc)
    family, attachments = _entries(main, rc, cutoff)
    if not attachments:
        return {}
    context = {"attachments": list(attachments.values()), "status": "unread"}
    prior = [member for member in family if member <= rc]
    if prior:
        context["related_body"] = family[max(prior)]
    if later := await _latest(client, family, rc):
        context["later_body"] = later
    nodes = client._extract_viewer_nodes(main)
    if not nodes and (match := _DIRECT.search(main)):
        nodes = [dict(zip(("rcpNo", "dcmNo", "eleId", "offset", "length", "dtd"), match.groups()))]
    # Read only the current attachment's comparison section. The selected main
    # can silently point at a different receipt or a superseded attachment.
    node = next((n for n in nodes if n["rcpNo"] == rc and n["dcmNo"] in attachments
                 and (n["dtd"] == "HTML" or "정정" in re.sub(r"\s+", "", n.get("text", "")))), None)
    if not node:
        return context
    body = await client._fetch_viewer_section_html(node)
    a = parse_amendment_table(body)
    if not (a["rows"] or a.get("reason")):
        return context
    context.update(status="comparison_read", selected=attachments[node["dcmNo"]], reason=a["reason"],
                   changes=[{key: str(row[key])[:330] for key in ("item", "before", "after")} for row in a["rows"][:12]],
                   total_changes=len(a["rows"]))
    return context


def attachment_signals(context):
    if context.get("status") != "comparison_read":
        return []
    title = re.sub(r"\s+", "", context["selected"]["title"])
    rows = context["changes"]
    reason = context["reason"] or ""
    if "전환가액산정표" in title:
        pattern, label, check = "conversion_price_attachment", "전환가액 산정표 정정", "첨부 산정 금액과 후속 본문의 전환가·철회를 확인한다."
    elif any(re.search(r"부여(?:할)?(?:주식수|대상자수)", re.sub(r"\s+", "", row["item"])) for row in rows):
        pattern, label, check = "award_amendment", "보상 부여 대상·주식 수 정정", "부여 안건의 변경 전후 수량과 주총 결과를 확인한다."
    elif "이사회의사록" in title and "오기" in reason and any("이사회의사록" in row["item"] for row in rows):
        pattern, label, check = "minutes_comparison", "교체된 이사회의사록 원본 대조 필요", "정정표에는 파일명만 있다. 첨부 전후의 수량과 조건을 직접 대조한다."
    else:
        return []
    return [{"pattern": pattern, "label": label, "filing_state": "첨부정정", "check": check,
             "quote": reason if reason.strip("- ") else context["selected"]["title"], "dart_url": context["selected"]["dart_url"]}]
