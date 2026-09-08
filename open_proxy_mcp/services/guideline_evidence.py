"""Public evidence acquisition for the limited guideline v2 pilot.

This adapter exposes source observations and review tasks. It never silently
turns a disclosed annual rate into a prior-term rate or a company statement
into an accepted independence assessment.
"""
from __future__ import annotations

import asyncio
import re
from datetime import date
from typing import Any
from bs4 import BeautifulSoup

from open_proxy_mcp.services.board_attendance import parse_board_attendance_observations


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
            results.append({**item, "status": "read" if text else "format_unsupported", "text": text})
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
        if src.get("type") == "dart" and set(src) == {"type", "rcept_no"}:
            rc = src["rcept_no"]
            if not isinstance(rc, str) or not re.fullmatch(r"\d{14}", rc):
                raise ValueError("guideline_evidence_sources: invalid DART receipt")
            validated.append(src)
        elif src.get("type") == "kind" and set(src) == {"type", "url"}:
            url = src["url"]
            match = re.fullmatch(r"https://kind\.krx\.co\.kr/external/(\d{4})/(\d{2})/(\d{2})/\d{6}/(\d{14})/(\d+)\.htm", url) if isinstance(url, str) else None
            if not match:
                raise ValueError("guideline_evidence_sources: fixed KIND external HTML URL required")
            try:
                published = date(*map(int, match.group(1, 2, 3))).strftime("%Y%m%d")
            except ValueError:
                raise ValueError("guideline_evidence_sources: invalid KIND publication date") from None
            validated.append({**src, "published": published, "source_id": f"kind:{match[4]}:{match[5]}"})
        else:
            raise ValueError("guideline_evidence_sources: unsupported source schema")
    identities = [s.get("source_id") or s.get("rcept_no") for s in validated]
    if len(set(identities)) != len(identities):
        raise ValueError("guideline_evidence_sources: duplicate source")
    results = []
    for src in validated:
        if src["type"] == "dart":
            result = (await collect_supplemental_filings(client, [src["rcept_no"]], as_of))[0]
            results.append({**result, "source_id": f"filing:{src['rcept_no']}"})
            continue
        item = {"source_id": src["source_id"], "source_url": src["url"],
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
    if not (doc or {}).get("html"):
        return {**base, "status": "format_unsupported", "reason": "절 경계를 확인할 원문 XML을 확보하지 못했습니다."}
    parsed = parse_board_attendance_observations(doc["html"])
    text = parsed.pop("section_text", "")
    return {**base, **parsed, "document_read": True,
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
