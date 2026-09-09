"""Public evidence acquisition for the limited guideline v2 pilot.

This adapter exposes source observations and review tasks. It never silently
turns a disclosed annual rate into a prior-term rate or a company statement
into an accepted independence assessment.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import date
from typing import Any
from bs4 import BeautifulSoup

from open_proxy_mcp.services.board_attendance import parse_board_attendance_observations
from open_proxy_mcp.dart.client import DartClientError


def _source_read_options(src: dict) -> dict:
    """Bounded navigation requests; these select text, never assert facts."""
    scope = src.get("source_scope", "company_context")
    terms = src.get("focus_terms", [])
    offset, chars = src.get("text_offset", 0), src.get("text_chars", 12000)
    if (not isinstance(scope, str) or scope not in {"candidate", "company_context", "agenda_context"}
        or not isinstance(terms, list) or len(terms) > 6
        or any(not isinstance(term, str) or not term.strip() or len(term) > 120 for term in terms)
        or type(offset) is not int or not 0 <= offset <= 2_000_000
        or type(chars) is not int or not 1000 <= chars <= 30000):
        raise ValueError("guideline_evidence_sources: invalid source reading options")
    return {"source_scope": scope, "focus_terms": list(dict.fromkeys(terms)),
            "text_offset": offset, "text_chars": chars}


def build_source_packet(item: dict, candidate_name: str = "") -> dict | None:
    """Keep company context even when a candidate name is absent.

    Offsets refer to whitespace-normalized document text. Disjoint excerpts stay
    separate so a citation cannot bridge an omitted passage. A next request can
    widen/reposition the same original document without a new fact parser.
    """
    if item.get("status") != "read":
        return None
    text = re.sub(r"\s+", " ", item.get("text") or "").strip()
    if not text:
        return None
    options = _source_read_options(item.get("read_options") or {
        "source_scope": item.get("purpose", "company_context")})
    offset, budget = options["text_offset"], options["text_chars"]
    terms = options["focus_terms"]
    if not terms and candidate_name and offset == 0:
        terms = [candidate_name]
    matches = []
    if terms and options["source_scope"] != "candidate" and not options["focus_terms"]:
        # Preserve a company overview as well as identity-adjacent context.
        matches.append((0, min(len(text), budget // 3)))
    found = []
    for term in terms:
        hits = list(re.finditer(re.escape(term), text))
        if hits:
            found.append(term)
        for match in hits[:6]:
            matches.append((max(0, match.start() - 500), min(len(text), match.end() + 3500)))
    if not found:
        matches = [(min(offset, len(text)), min(len(text), offset + budget))]
    spans = []
    for start, end in sorted(matches):
        if spans and start <= spans[-1][1]:
            spans[-1][1] = max(end, spans[-1][1])
        else:
            spans.append([start, end])
    selected = []
    for start, end in spans:
        end = min(end, start + budget)
        if end > start:
            selected.append([start, end])
            budget -= end - start
        if budget <= 0:
            break
    source_id = item.get("source_id") or f"filing:{item['rcept_no']}"
    next_offset = max((end for _, end in selected), default=min(offset, len(text)))
    base_request = ({"type": "dart", "rcept_no": item["rcept_no"]} if item.get("rcept_no")
                    else {"type": "kind", "url": item["source_url"]})
    return {"source_id": source_id, "source_url": item["source_url"],
            "publisher_type": "company_disclosure", "source_scope": options["source_scope"],
            "published": item.get("published") or (item.get("rcept_no") or "")[:8],
            "excerpts": [text[start:end] for start, end in selected],
            "excerpt_offsets": [{"start": start, "end": end} for start, end in selected],
            "offset_basis": "whitespace_normalized_document_text", "total_chars": len(text),
            "document_sha256": hashlib.sha256(json.dumps(text, ensure_ascii=False).encode()).hexdigest(),
            "partial": selected != [[0, len(text)]],
            "candidate_name_present": bool(candidate_name and candidate_name in text),
            "focus_terms_found": found, "focus_terms_not_found": [t for t in terms if t not in found],
            "read_next": {"source_request": {**base_request, "source_scope": options["source_scope"],
                                              "text_offset": next_offset, "text_chars": options["text_chars"]},
                          "has_more_after_window": next_offset < len(text),
                          "can_refocus": True},
            "hint": "회사·안건 맥락 원문. 후보명 부재는 무관함의 증거가 아니다. 당사자·사건·공개일·효력일·조건부 계획·정정/후속 공시를 대조하고 후보 책임은 별도 근거로 연결. 잘린 문맥은 focus_terms 또는 text_offset으로 다시 읽는다."}


def fiscal_attendance_period(text: str, as_of: str) -> dict:
    """Read the annual cover period, never silently label an older year latest."""
    date_pattern = r"(\d{4})\s*[년.-]\s*(\d{1,2})\s*[월.-]\s*(\d{1,2})\s*[일.]?"
    match = re.search(r"사업연도\s*" + date_pattern + r"\s*부터\s*" + date_pattern + r"\s*까지", text[:5000])
    if not match:
        return {"status": "unresolved", "reason": "사업보고서 표지의 사업연도 기간 확인 필요"}
    try:
        start, end = date(*map(int, match.group(1, 2, 3))), date(*map(int, match.group(4, 5, 6)))
        cutoff = date.fromisoformat(f"{as_of[:4]}-{as_of[4:6]}-{as_of[6:]}")
        next_end = end.replace(year=end.year+1, day=28 if end.month == 2 and end.day == 29 else end.day)
        if not start <= end < cutoff or next_end < cutoff or not 300 <= (end-start).days <= 370:
            return {"status": "unresolved", "reason": "오래되거나 비정형인 사업연도. 최신 완료 기간 별도 확인 필요",
                    "disclosed_start": start.isoformat(), "disclosed_end": end.isoformat()}
        return {"status": "resolved", "start": start.isoformat(), "end": end.isoformat(),
                "basis": "annual_cover_standard_fiscal_year", "quote": match.group(0)}
    except ValueError:
        return {"status": "unresolved", "reason": "사업연도 날짜가 유효하지 않음"}


def officer_filing_kind(title: str) -> str | None:
    """Discovery aliases only: preserve the original title and role in evidence."""
    title = re.sub(r"[\sㆍ·.,()\[\]〈〉]", "", title)
    if "대표이사변경" in title:
        return "representative_change"
    if any(role in title for role in ("독립이사", "사외이사", "감사위원", "감사")) and (
        any(event in title for event in ("중도퇴임", "해임")) or
        ("선임" in title and ("신고" in title or "변경" in title))
    ):
        return "officer_change"
    return None


async def discover_officer_filings(client, corp_code: str, as_of: str,
                                   exclude: list[str] | None = None) -> dict:
    """Bounded, explicitly incomplete index of pre-cutoff officer events.

    E (other) and I (exchange) are searched before title filtering. No event is
    itself an accepted tenure fact. Same-day filings need intraday verification.
    """
    start = f"{int(as_of[:4])-2}0101"
    scans, matches = [], {}
    excluded = set(exclude or [])
    for category in ("E", "I"):
        scan = {"pblntf_ty": category, "start": start, "end": as_of,
                "pages_read": 0, "status": "read", "truncated": False}
        try:
            for page in range(1, 3):
                payload = await asyncio.wait_for(client.search_filings(
                    bgn_de=start, end_de=as_of, corp_code=corp_code,
                    pblntf_ty=category, page_no=page, page_count=100,
                    last_reprt_at="N"), timeout=15)
                status = payload.get("status", "000")
                if status == "013":
                    break
                if status != "000":
                    raise ValueError("index unavailable")
                scan["pages_read"] = page
                scan["total_count"] = payload.get("total_count")
                total_pages = int(payload.get("total_page") or 1)
                scan["truncated"] = total_pages > page
                for row in payload.get("list", []):
                    rc = row.get("rcept_no", "")
                    published = row.get("rcept_dt", "")
                    kind = officer_filing_kind(row.get("report_nm", ""))
                    if (not kind or not re.fullmatch(r"\d{14}", rc)
                        or not re.fullmatch(r"\d{8}", published)
                        or published > as_of or rc[:8] > as_of or rc in excluded):
                        continue
                    matches[rc] = {"rcept_no": rc, "report_nm": row.get("report_nm"),
                                   "published": published, "filing_kind": kind,
                                   "same_day_unverified": published == as_of or rc[:8] == as_of}
                if page >= total_pages:
                    break
        except DartClientError as exc:
            scan["status"] = "no_rows" if exc.status == "013" else "fetch_failed"
        except Exception:
            scan["status"] = "fetch_failed"
        scans.append(scan)
    ordered = sorted(matches.values(), key=lambda x: (
        x["filing_kind"] != "officer_change", -int(x["rcept_no"])))
    eligible = [r for r in ordered if not r["same_day_unverified"]]
    selected = eligible[:5]
    filings = await collect_supplemental_filings(client, [r["rcept_no"] for r in selected], as_of)
    filings = [{**row, **item, "discovery": "officer_events"} for row, item in zip(selected, filings)]
    return {"status": "partial" if any(s["status"] == "fetch_failed" or s["truncated"] for s in scans)
            or len(eligible) > 5 else "searched", "scans": scans, "matches": ordered,
            "selected_count": len(selected), "filings": filings, "complete_history": False,
            "hint": "최근 2년 시작일부터 종류별 최대 2쪽·원문 5건. 검색 0건은 재직/관계 없음이 아님. 같은 날 공시는 선후관계 확인 전 자동 입력에서 제외."}


async def collect_supplemental_filings(client, rcepts: list[str], as_of: str) -> list[dict]:
    """Read explicitly supplied public DART filings, never arbitrary URLs."""
    if len(rcepts) > 5 or len(set(rcepts)) != len(rcepts):
        raise ValueError("guideline_evidence_rcepts: at most five unique filings")
    if any(not re.fullmatch(r"\d{14}", rc) for rc in rcepts):
        raise ValueError("guideline_evidence_rcepts: invalid receipt number")
    results = []
    for rc in rcepts:
        item = {"rcept_no": rc, "source_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rc}"}
        if rc[:8] > as_of:
            results.append({**item, "status": "after_as_of"})
            continue
        try:
            doc = await asyncio.wait_for(client.get_document_cached(rc), timeout=20)
            text = (doc or {}).get("text") or ""
            results.append({**item, "status": "read" if text else "format_unsupported", "text": text,
                            "acquisition": (doc or {}).get("source", "document_xml")})
        except Exception:
            results.append({**item, "status": "fetch_failed"})
    return results


async def collect_supplemental_sources(client, sources: list[dict], as_of: str) -> list[dict]:
    """Explicit public source adapters: DART XML or fixed KRX KIND HTML pages.

    KIND is for identified exchange-source documents, not an arbitrary web
    fetcher. DART and KIND receipt identifiers are not interchangeable.
    """
    if len(sources) > 5:
        raise ValueError("guideline_evidence_sources: maximum five sources")
    validated = []
    for src in sources:
        if not isinstance(src, dict):
            raise ValueError("guideline_evidence_sources: object required")
        options = _source_read_options(src)
        read_keys = {"source_scope", "focus_terms", "text_offset", "text_chars"}
        if src.get("type") == "dart" and set(src) - read_keys == {"type", "rcept_no"}:
            rc = src["rcept_no"]
            if not isinstance(rc, str) or not re.fullmatch(r"\d{14}", rc):
                raise ValueError("guideline_evidence_sources: invalid DART receipt")
            validated.append({**src, "read_options": options})
        elif src.get("type") == "kind" and set(src) - read_keys == {"type", "url"}:
            url = src["url"]
            match = re.fullmatch(r"https://kind\.krx\.co\.kr/external/(\d{4})/(\d{2})/(\d{2})/\d{6}/(\d{14})/(\d+)\.htm", url) if isinstance(url, str) else None
            if not match:
                raise ValueError("guideline_evidence_sources: fixed KIND external HTML URL required")
            try:
                published = date(*map(int, match.group(1, 2, 3))).strftime("%Y%m%d")
            except ValueError:
                raise ValueError("guideline_evidence_sources: invalid KIND publication date") from None
            validated.append({**src, "read_options": options, "published": published, "source_id": f"kind:{match[4]}:{match[5]}"})
        else:
            raise ValueError("guideline_evidence_sources: unsupported source schema")
    identities = [s.get("source_id") or s.get("rcept_no") for s in validated]
    if len(set(identities)) != len(identities):
        raise ValueError("guideline_evidence_sources: duplicate source")
    results = []
    for src in validated:
        if src["type"] == "dart":
            result = (await collect_supplemental_filings(client, [src["rcept_no"]], as_of))[0]
            results.append({**result, "source_id": f"filing:{src['rcept_no']}", "read_options": src["read_options"]})
            continue
        item = {"source_id": src["source_id"], "source_url": src["url"], "read_options": src["read_options"],
                "published": src["published"], "date_basis": "KIND fixed external publication path"}
        if src["published"] > as_of:
            results.append({**item, "status": "after_as_of"})
            continue
        try:
            # Same process web clock as DART/KIND; no redirects to other hosts.
            await client._throttle_kind()
            response = await client._http.get(src["url"], timeout=20, follow_redirects=False)
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError("unexpected response")
            # KIND older filings may declare EUC-KR in HTML rather than HTTP.
            soup = BeautifulSoup(response.content, "html.parser")
            for tag in soup(["script", "style"]):
                tag.decompose()
            content = soup.get_text(" ", strip=True)
            results.append({**item, "status": "read" if content else "format_unsupported", "text": content})
        except Exception:
            results.append({**item, "status": "fetch_failed"})
    return results


async def collect_guideline_evidence(client, annual_ref: dict | None, as_of: str) -> dict:
    ref = annual_ref or {}
    rc = ref.get("rcept_no") or ""
    base = {"as_of": as_of, "filing": ref, "observations": [], "document_read": False}
    if not rc:
        return {**base, "status": "source_unresolved",
                "reason": "기준일 이전 사업보고서 참조를 확보하지 못했습니다. 검색 실패와 미제출은 아직 구분되지 않았습니다."}
    published = ref.get("rcept_dt") or rc[:8]
    if not (len(published) == 8 and published.isdigit() and published <= as_of):
        return {**base, "status": "after_as_of", "reason": "공개 시점이 기준일 이전임을 확인하지 못해 사용하지 않습니다."}
    try:
        doc = await asyncio.wait_for(client.get_document_cached(rc), timeout=30)
    except Exception:
        return {**base, "status": "fetch_failed", "reason": "공시 원문 조회 실패. 미공시로 처리하지 않습니다."}
    doc = doc or {}
    try:
        parsed = parse_board_attendance_observations(doc["html"]) if doc.get("html") else {"status": "format_unsupported"}
    except Exception:
        parsed = {"status": "extraction_failed", "reason": "자동 추출 실패. 원문 직접 판독 가능."}
    period = fiscal_attendance_period(doc.get("text") or "", as_of)
    text = parsed.pop("section_text", "")
    raw = doc.get("text") or ""
    if not text:
        # Navigation aid, not another fact parser: give the LLM a readable window.
        match = re.search(r"이사회.{0,10}관한 사항", raw)
        text = raw[max(0, match.start()-300):] if match else raw
    return {**base, **parsed, "document_read": bool(text),
            "attendance_period": {k: v for k, v in period.items() if k != "quote"},
            "fiscal_period_quote": period.get("quote") or raw[:5000],
            "source_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rc}",
            "raw_text": text[:20000], "raw_text_total_chars": len(text),
            "raw_text_truncated": len(text) > 20000,
            "next_action": "공시 '이사회에 관한 사항'에서 회차별 출석 표·재임 기간·직무정지 주석을 확인하세요."}


def candidate_guideline_inputs(candidate: dict[str, Any], attendance: dict,
                               notice_rcept: str | None, as_of: str) -> dict:
    name = candidate.get("name")
    apt = candidate.get("appointment_type") or {}
    kind = apt.get("type")
    # Missing appointment type is not a new appointment. Existing classifications
    # remain explicitly identified as upstream inferences, subject to review.
    reelection = True if kind == "renewed" else False if kind == "new" else None
    observations = [o for o in attendance.get("observations", []) if o["name"] == name]
    faith = candidate.get("faithfulness") or {}
    raw = {k: faith.get(k) for k in ["career_raw", "career_content_raw", "main_job",
                                    "recommender", "recommendation_reason_raw", "duty_plan_raw"]}
    return {
        "metrics": {"attendance_pct": None, "is_reelection": reelection,
                    "attendance_exception_accepted": None,
                    "independence_concern_accepted": None, "coverage_complete": False},
        "evidence_status": {
            "attendance_pct": {
                "status": ("period_not_resolved" if observations else
                           "candidate_not_linked" if attendance.get("status") == "parsed" else
                           attendance.get("status", "not_collected")),
                "reason": "공시 구간별 값은 확보했지만 직전 임기·대상 동일성·분모를 아직 검증하지 않았습니다." if observations
                          else "후보에게 귀속할 이사회 출석률이 아직 확정되지 않았습니다. 원문 부재로 단정하지 않습니다.",
                "observations": observations, "candidate_match": "name_only_unaccepted",
                "rcept_no": (attendance.get("filing") or {}).get("rcept_no"),
            },
            "is_reelection": {"status": "upstream_classification" if reelection is not None else "unknown",
                              "classification": apt, "review_required": True},
            "attendance_exception_accepted": {"status": "assessment_pending",
                                              "reason": "불참·직무정지 사유를 해석하고 수용할 평가 절차가 필요합니다."},
            "independence_concern_accepted": {"status": "assessment_pending",
                                              "reason": "원문과 반증을 검토한 평가 수용 전입니다. 기존 독립성 등급을 수용값으로 복사하지 않습니다."},
            "coverage_complete": {"status": "incomplete",
                                  "reason": "출석·독립성의 근거 검증 및 평가 수용이 완료되지 않았습니다."},
        },
        "assessment_task": {
            "candidate_name": name, "as_of": as_of, "notice_rcept_no": notice_rcept,
            "source_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={notice_rcept}" if notice_rcept else None,
            "source_material": raw,
            "questions": ["후보의 재선임 여부와 실제 직무·재직기간이 일치하는가?",
                          "회사·제안주주와의 고용·거래·지원 관계는 무엇이며 중요한가?",
                          "관계 없음이라는 회사의 주장 외에 확인한 근거와 반증은 무엇인가?"],
            "required_output": ["claims", "evidence_refs", "counterevidence", "unresolved", "rationale"],
            "status": "awaiting_review", "accepted_by": None,
        },
    }
