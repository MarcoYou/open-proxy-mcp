"""Market-wide shareholder meeting agenda parser audit.

This runner intentionally separates live DART fetch from local XML reparsing:

1. Build KOSPI/KOSDAQ market-cap universes from Naver Finance.
2. Fetch each company's 2026 annual meeting notice once through the same
   selection path as the production shareholder_meeting service.
3. Save full document.xml HTML locally under cache/ for reproducible parser
   work without repeatedly hitting DART.
4. Reparse the saved XML multiple times and compare parser hashes.

Usage:
    uv run python scripts/audit_agenda_parser_marketwide.py --limit 5 --loops 2
    uv run python scripts/audit_agenda_parser_marketwide.py --kospi 500 --kosdaq 150 --loops 5
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.dart.client import DartClientError, get_dart_client  # noqa: E402
from open_proxy_mcp.services.company import resolve_company_query  # noqa: E402
from open_proxy_mcp.services.contracts import AnalysisStatus  # noqa: E402
from open_proxy_mcp.services.shareholder_meeting import (  # noqa: E402
    _agenda_nodes,
    _load_notice_bundle_with_fallback,
    _parse_notice_bundle,
    _safe_fiscal_month,
    _select_notice_candidate,
)
from open_proxy_mcp.tools.parser import (  # noqa: E402
    parse_agenda_details_xml,
    parse_aoi_xml,
    parse_retirement_pay_xml,
)


AUDIT_ID = "260525_agenda_parser_marketwide"
DEFAULT_OUT_DIR = ROOT / f"wiki/architecture/audits/data/{AUDIT_ID}"
DEFAULT_DOCS_DIR = ROOT / f"cache/audits/{AUDIT_ID}/documents"

ANCHORS = (
    "집중투표",
    "누적투표",
    "별개의 조",
    "이사 종류",
    "종류별",
    "감사위원",
    "분리선출",
    "보수한도",
    "퇴직금",
    "충실의무",
    "전자주주총회",
    "전자투표",
    "독립이사",
    "사외이사",
    "자기주식",
    "소수주주",
    "권고적 주주제안",
)

FUND_OR_VEHICLE_PREFIXES = (
    "KODEX",
    "TIGER",
    "ACE",
    "RISE",
    "SOL",
    "PLUS",
    "KOSEF",
    "HANARO",
    "TIMEFOLIO",
    "ARIRANG",
    "KBSTAR",
    "KIWOOM",
    "FOCUS",
    "마이티",
    "히어로즈",
    "UNICORN",
    "1Q",
)


@dataclass(frozen=True)
class UniverseRow:
    market: str
    rank: int
    ticker: str
    company: str
    market_cap: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _norm_id(number: str) -> str:
    return (number or "").replace("제", "").replace("호", "").strip()


def _json_hash(value: Any) -> str:
    blob = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


async def _fetch_naver_market(market: str, limit: int) -> list[UniverseRow]:
    """Fetch market-cap rows from Naver Finance.

    market: KOSPI -> sosok=0, KOSDAQ -> sosok=1.
    Preferred stocks are skipped because OPM company resolution and AGM notices
    are company/common-share centric.
    """

    sosok = "0" if market == "KOSPI" else "1"
    rows: list[UniverseRow] = []
    seen: set[str] = set()
    headers = {"User-Agent": "Mozilla/5.0 OpenProxyMCP audit"}
    candidate_target = limit + 200

    async with httpx.AsyncClient(timeout=15, headers=headers, follow_redirects=True) as client:
        page = 1
        while len(rows) < candidate_target and page <= 80:
            url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
            resp = await client.get(url)
            resp.raise_for_status()
            resp.encoding = "euc-kr"
            soup = BeautifulSoup(resp.text, "lxml")
            found = 0
            for tr in soup.select("table.type_2 tr"):
                link = tr.select_one("a.tltle")
                if not link:
                    continue
                found += 1
                name = _normalize_space(link.get_text(" "))
                href = link.get("href") or ""
                code = (parse_qs(urlparse(href).query).get("code") or [""])[0]
                if not re.fullmatch(r"\d{6}", code):
                    continue
                if code in seen:
                    continue
                # Naver market cap pages include ETFs/ETNs and preferred stocks;
                # exclude non-company vehicles to keep company-level AGM universe.
                if (
                    name.startswith(FUND_OR_VEHICLE_PREFIXES)
                    or "ETN" in name
                    or "스팩" in name
                    or "SPAC" in name.upper()
                    or "액티브" in name
                    or "인덱스" in name
                ):
                    continue
                if name.endswith(("우", "우B", "우선주")) or re.search(r"\d우$", name):
                    continue
                cols = [_normalize_space(td.get_text(" ")) for td in tr.select("td")]
                market_cap = cols[6] if len(cols) > 6 else ""
                seen.add(code)
                rows.append(UniverseRow(market=market, rank=len(rows) + 1, ticker=code, company=name, market_cap=market_cap))
                if len(rows) >= candidate_target:
                    break
            if found == 0:
                break
            page += 1
            await asyncio.sleep(0.2)
    return rows


async def _filter_company_universe(rows: list[UniverseRow], limit: int) -> list[UniverseRow]:
    """Keep rows that resolve to a listed DART company.

    This removes ETFs/ETNs/funds that still pass name heuristics on Naver's
    market-cap table. DART corpCode lookup is local-cached after initial master
    load and does not hit list/document APIs per ticker.
    """

    out: list[UniverseRow] = []
    for row in rows:
        try:
            resolution = await resolve_company_query(row.ticker)
        except Exception:
            continue
        if resolution.status != AnalysisStatus.EXACT or not resolution.selected:
            continue
        out.append(UniverseRow(
            market=row.market,
            rank=len(out) + 1,
            ticker=row.ticker,
            company=resolution.selected.get("corp_name") or row.company,
            market_cap=row.market_cap,
        ))
        if len(out) >= limit:
            break
    return out


def _write_universe(path: Path, rows: list[UniverseRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["market", "rank", "ticker", "company", "market_cap"])
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "market": row.market,
                "rank": row.rank,
                "ticker": row.ticker,
                "company": row.company,
                "market_cap": row.market_cap,
            })


def _load_universe(path: Path) -> list[UniverseRow]:
    with path.open(encoding="utf-8") as f:
        rows = []
        for row in csv.DictReader(f):
            rows.append(UniverseRow(
                market=row.get("market") or "",
                rank=int(row.get("rank") or len(rows) + 1),
                ticker=row.get("ticker") or "",
                company=row.get("company") or "",
                market_cap=row.get("market_cap") or "",
            ))
    return rows


def _flatten_raw_agendas(items: list[dict[str, Any]], parent_id: str = "", parent_title: str = "") -> list[dict[str, Any]]:
    flat: list[dict[str, Any]] = []
    for item in items or []:
        number = item.get("number") or ""
        agenda_id = _norm_id(number)
        entry = {
            "agenda_id": agenda_id,
            "number": number,
            "title": _normalize_space(item.get("title") or ""),
            "parent_id": parent_id,
            "parent_title": parent_title,
            "source": item.get("source"),
            "conditional": item.get("conditional"),
        }
        flat.append(entry)
        flat.extend(_flatten_raw_agendas(item.get("children") or [], agenda_id, entry["title"]))
    return flat


def _charter_subs(agenda: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for item in agenda or []:
        if "정관" in (item.get("title") or ""):
            return item.get("children") or []
    return []


def _parse_saved_doc(doc: dict[str, Any]) -> dict[str, Any]:
    html = doc.get("html") or ""
    text = doc.get("text") or ""
    rcept_no = doc.get("rcept_no") or ""
    parsed = _parse_notice_bundle(text, html, rcept_no=rcept_no, soup_cache={})
    agenda = parsed.get("agenda") or []
    aoi_change = parse_aoi_xml(html, sub_agendas=_charter_subs(agenda)) if html else {"amendments": [], "summary": {}}
    retirement = parse_retirement_pay_xml(html) if html else {"amendments": [], "summary": {}}
    details = parse_agenda_details_xml(html) if html else []
    return {
        "meeting_info": parsed.get("meeting_info") or {},
        "agenda_valid": parsed.get("agenda_valid"),
        "agendas_raw": agenda,
        "agendas": _agenda_nodes(agenda),
        "agenda_flat": _flatten_raw_agendas(agenda),
        "agenda_details": details,
        "board": parsed.get("board") or {},
        "compensation": parsed.get("compensation") or {},
        "aoi_change": aoi_change,
        "retirement_pay": retirement,
        "correction": parsed.get("correction"),
    }


def _snippet_windows(text: str, anchors: tuple[str, ...] = ANCHORS, width: int = 80) -> list[dict[str, str]]:
    compact = _normalize_space(text)
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for anchor in anchors:
        start = 0
        while True:
            idx = compact.find(anchor, start)
            if idx < 0:
                break
            lo = max(0, idx - width)
            hi = min(len(compact), idx + len(anchor) + width)
            snippet = compact[lo:hi]
            key = (anchor, snippet)
            if key not in seen:
                out.append({"anchor": anchor, "snippet": snippet})
                seen.add(key)
            start = idx + len(anchor)
            if sum(1 for item in out if item["anchor"] == anchor) >= 20:
                break
    return out


def _candidate_names(board: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for app in board.get("appointments") or []:
        for cand in app.get("candidates") or []:
            name = _normalize_space(cand.get("name") or "")
            if name:
                names.add(name)
                names.add(name.replace(" ", ""))
                names.add(name.lower())
                names.add(name.replace(" ", "").lower())
    return names


_AUDIT_TITLE_NAME_BLACKLIST = {
    "선임", "선임의", "연임", "연임의", "중임", "중임의", "재선임", "승인", "승인의",
    "명칭변경", "명칭", "변경", "후보자", "후보", "일신상의", "추천", "추천에", "추천의",
    "추천위원회", "감사위원회", "위원회", "위원", "위원이", "되는", "규정", "설치", "운영",
    "근거", "마련",
}


def _clean_audit_title_name(raw_name: str) -> str | None:
    name = _normalize_space(raw_name)
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
    name = re.sub(r"\s*(?:선임|해임|재선임|중임|연임|승인).*?$", "", name).strip()
    if not name:
        return None

    tokens = name.split()
    if tokens and tokens[0] in _AUDIT_TITLE_NAME_BLACKLIST:
        if "되는" in tokens and re.fullmatch(r"[가-힣]{2,5}", tokens[-1]):
            return tokens[-1]
        return None
    if len(tokens) >= 2 and tokens[0].endswith("국인") and re.fullmatch(r"[가-힣]{2,10}", tokens[-1]):
        return tokens[-1]
    if tokens and all(re.fullmatch(r"[가-힣]", token) for token in tokens[:5]):
        syllables = "".join(tokens[:5])
        if 2 <= len(syllables) <= 5:
            return syllables
    if tokens and re.fullmatch(r"[가-힣]{2,5}", tokens[0]):
        return tokens[0]
    if re.fullmatch(r"[A-Z][A-Za-z\.\-]*(?:\s+[A-Z][A-Za-z\.\-]*){0,5}", name):
        return name
    if re.fullmatch(r"[가-힣]{2,5}", name):
        return name
    return None


def _candidate_names_from_title(title: str) -> set[str]:
    names: set[str] = set()
    patterns = [
        r"후보(?:자)?\s*[:：]\s*([가-힣A-Za-z·\.\-\s]{2,40}?)(?=\)|,|/|$)",
        r"후보(?:자)?\s+([가-힣A-Za-z·\.\-\s]{2,40}?)(?=\s*(?:선임|해임|재선임|중임|연임|$|\)))",
        r"^([가-힣A-Za-z·\.\-\s]{2,40}?)\s+(?:감사위원|사외이사|독립이사|사내이사|기타비상무이사|감사|이사)\s+후보(?:자)?\s+(?:선임|해임|재선임|중임|연임)",
        r"(?:감사위원|사외이사|독립이사|사내이사|기타비상무이사|감사|이사)\s+([가-힣A-Za-z·\.\-\s]{2,40}?)\s+후보(?:자)?\s+(?:선임|해임|재선임|중임|연임)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, title):
            name = _clean_audit_title_name(match.group(1))
            if not name:
                continue
            names.add(name)
            if " " in name and re.fullmatch(r"[가-힣\s]{2,10}", name):
                names.add(name.replace(" ", ""))
    return names


_NON_PERSONNEL_TITLE_PHRASES = (
    "관련 변경", "기준 변경", "인원 변경", "구성 변경", "의무 추가",
    "권한 위임", "의결권 제한", "보수와 퇴직금", "규정 신설",
)


def _looks_like_personnel_agenda_title(title: str) -> bool:
    if any(phrase in title for phrase in _NON_PERSONNEL_TITLE_PHRASES):
        return False
    return ("선임" in title or "해임" in title) and ("이사" in title or "감사" in title)


def _comp_targets(compensation: dict[str, Any]) -> set[str]:
    targets: set[str] = set()
    for item in compensation.get("items") or []:
        target = _normalize_space(item.get("target") or item.get("title") or "")
        if target:
            targets.add(target)
    return targets


def _validate_parse(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    flat = parsed.get("agenda_flat") or []

    def add(severity: str, code: str, message: str, agenda_id: str = "", title: str = "", evidence: str = "") -> None:
        issues.append({
            "severity": severity,
            "code": code,
            "agenda_id": agenda_id,
            "title": title,
            "message": message,
            "evidence": evidence[:500],
        })

    if not parsed.get("agenda_valid"):
        add("P1", "agenda_low_confidence", "안건 파싱 신뢰도 낮음")
    if not flat:
        add("P0", "agenda_empty", "안건이 비어 있음")
        return issues

    seen: dict[str, str] = {}
    for item in flat:
        agenda_id = item.get("agenda_id") or ""
        title = item.get("title") or ""
        parent_id = item.get("parent_id") or ""
        if not title:
            add("P1", "title_empty", "안건 제목이 비어 있음", agenda_id=agenda_id)
        if len(title) > 180:
            add("P2", "title_too_long", "안건 제목이 비정상적으로 김", agenda_id=agenda_id, title=title)
        if agenda_id in seen:
            add("P1", "agenda_duplicate_id", "동일 안건 번호가 중복됨", agenda_id=agenda_id, title=title, evidence=seen[agenda_id])
        seen[agenda_id] = title
        if parent_id and not agenda_id.startswith(parent_id + "-"):
            add("P1", "parent_child_id_mismatch", "하위 안건 번호가 parent 번호와 맞지 않음", agenda_id=agenda_id, title=title, evidence=f"parent={parent_id}")

    candidate_names = _candidate_names(parsed.get("board") or {})
    titles = " ".join(item.get("title") or "" for item in flat)
    if any(_looks_like_personnel_agenda_title(item.get("title") or "") for item in flat):
        if not candidate_names:
            add("P1", "personnel_agenda_without_candidates", "이사/감사 선임 안건이 있지만 후보 파싱이 비어 있음")
    for item in flat:
        title = item.get("title") or ""
        if "폐기" in title or "철회" in title:
            continue
        for name in sorted(_candidate_names_from_title(title)):
            if candidate_names and name not in candidate_names and name.replace(" ", "") not in candidate_names and name.lower() not in candidate_names and name.replace(" ", "").lower() not in candidate_names:
                add("P1", "candidate_title_name_not_in_board_parse", "안건 제목 후보명이 board 후보 파싱에 없음", agenda_id=item.get("agenda_id") or "", title=title, evidence=name)

    compensation = parsed.get("compensation") or {}
    comp_targets = _comp_targets(compensation)
    compensation_approval_titles = [
        item.get("title") or ""
        for item in flat
        if "보수" in (item.get("title") or "")
        and "한도" in (item.get("title") or "")
        and not any(kw in (item.get("title") or "") for kw in ["규정 신설", "규정신설", "규정 개정", "정관"])
    ]
    if compensation_approval_titles and not compensation.get("items"):
        add("P1", "compensation_agenda_without_items", "보수한도 안건이 있지만 compensation item 파싱이 비어 있음")
    if any("이사" in t and "보수" in t and "한도" in t for t in [item.get("title") or "" for item in flat]):
        if comp_targets and not any("이사" in target for target in comp_targets):
            add("P2", "director_compensation_target_missing", "이사 보수한도 target을 찾지 못함", evidence=", ".join(sorted(comp_targets)))
    if any(
        "감사" in t and "감사위원" not in t and "보수" in t and "한도" in t
        for t in [item.get("title") or "" for item in flat]
    ):
        if comp_targets and not any("감사" in target for target in comp_targets):
            add("P2", "audit_compensation_target_missing", "감사 보수한도 target을 찾지 못함", evidence=", ".join(sorted(comp_targets)))

    has_charter = any("정관" in (item.get("title") or "") for item in flat)
    aoi_amendments = (parsed.get("aoi_change") or {}).get("amendments") or []
    if has_charter and not aoi_amendments:
        add("P2", "charter_agenda_without_aoi_amendments", "정관 변경 안건이 있지만 정관 변경 상세 파싱이 비어 있음")

    for item in flat:
        title = item.get("title") or ""
        if "충실의무" in title and "신주" not in title and "신주발행" not in title:
            add("INFO", "variant_fiduciary_duty_pure", "순수 이사 충실의무 표현", agenda_id=item.get("agenda_id") or "", title=title)
        if "충실의무" in title and ("신주" in title or "신주발행" in title):
            add("INFO", "variant_fiduciary_duty_issuance", "신주발행 충실의무 표현", agenda_id=item.get("agenda_id") or "", title=title)
        if "집중투표" in title and ("별개의 조" in title or "구분" in title or "종류" in title):
            add("INFO", "variant_cumulative_voting_grouping_title", "집중투표 조 분리 관련 표현", agenda_id=item.get("agenda_id") or "", title=title)
        if "퇴직금" in title:
            add("INFO", "variant_retirement_pay_title", "퇴직금 관련 안건", agenda_id=item.get("agenda_id") or "", title=title)
        if "독립이사" in title or "사외이사" in title:
            add("INFO", "variant_independent_director_title", "사외이사/독립이사 관련 표현", agenda_id=item.get("agenda_id") or "", title=title)

    return issues


def _digest_parse(parsed: dict[str, Any], issues: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "agenda": [
            {
                "agenda_id": item.get("agenda_id"),
                "title": item.get("title"),
                "parent_id": item.get("parent_id"),
                "conditional": item.get("conditional"),
            }
            for item in parsed.get("agenda_flat") or []
        ],
        "agenda_valid": parsed.get("agenda_valid"),
        "board_names": sorted(_candidate_names(parsed.get("board") or {})),
        "comp_targets": sorted(_comp_targets(parsed.get("compensation") or {})),
        "aoi_count": len((parsed.get("aoi_change") or {}).get("amendments") or []),
        "retirement_count": len((parsed.get("retirement_pay") or {}).get("amendments") or []),
        "issue_codes": sorted([issue["code"] for issue in issues if issue["severity"] != "INFO"]),
    }


async def _fetch_one(row: UniverseRow, docs_dir: Path, timeout_s: float) -> dict[str, Any]:
    started = time.perf_counter()
    client = get_dart_client()
    calls_start = client.api_call_snapshot()
    base = {
        "market": row.market,
        "rank": row.rank,
        "ticker": row.ticker,
        "company": row.company,
        "market_cap": row.market_cap,
    }
    try:
        resolution = await asyncio.wait_for(resolve_company_query(row.ticker), timeout=timeout_s)
        if resolution.status != AnalysisStatus.EXACT or not resolution.selected:
            return {**base, "status": "resolve_failed", "duration_s": round(time.perf_counter() - started, 2)}
        selected = resolution.selected
        fiscal_month = await asyncio.wait_for(_safe_fiscal_month(selected["corp_code"]), timeout=timeout_s)
        selected_candidate, _alternatives, _basis, candidate_error, _candidate_notices = await asyncio.wait_for(
            _select_notice_candidate(
                selected["corp_code"],
                2026,
                "annual",
                "aoi_change",
                fiscal_month=fiscal_month,
            ),
            timeout=timeout_s,
        )
        if not selected_candidate:
            return {
                **base,
                "status": "no_filing",
                "canonical_name": selected.get("corp_name", ""),
                "corp_code": selected.get("corp_code", ""),
                "fiscal_month": fiscal_month,
                "message": candidate_error or "annual notice not found",
                "api_calls": client.api_call_snapshot() - calls_start,
                "duration_s": round(time.perf_counter() - started, 2),
            }
        rcept_no = selected_candidate["notice"]["rcept_no"]
        parsed_notice, parse_warnings, source_used = await asyncio.wait_for(
            _load_notice_bundle_with_fallback(rcept_no, scope="aoi_change", soup_cache={}),
            timeout=timeout_s,
        )
        doc = {
            "rcept_no": rcept_no,
            "ticker": row.ticker,
            "company": row.company,
            "canonical_name": selected.get("corp_name", ""),
            "corp_code": selected.get("corp_code", ""),
            "market": row.market,
            "rank": row.rank,
            "notice": selected_candidate["notice"],
            "fiscal_month": fiscal_month,
            "source_used": source_used,
            "fetched_at": _now_iso(),
            "text": parsed_notice.get("text") or "",
            "html": parsed_notice.get("html") or "",
            "images": [],
        }
        docs_dir.mkdir(parents=True, exist_ok=True)
        doc_path = docs_dir / f"{row.market.lower()}_{row.rank:03d}_{row.ticker}_{rcept_no}.json"
        doc_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        parsed = _parse_saved_doc(doc)
        issues = _validate_parse(parsed)
        digest = _digest_parse(parsed, issues)
        variants = _snippet_windows((doc.get("text") or "")[:200000])
        return {
            **base,
            "status": "ok",
            "canonical_name": selected.get("corp_name", ""),
            "corp_code": selected.get("corp_code", ""),
            "fiscal_month": fiscal_month,
            "rcept_no": rcept_no,
            "doc_path": str(doc_path.relative_to(ROOT)),
            "notice_parse_source": source_used,
            "warnings": parse_warnings,
            "agenda_count": len(parsed.get("agenda_flat") or []),
            "board_candidate_count": len(digest["board_names"]),
            "compensation_item_count": len((parsed.get("compensation") or {}).get("items") or []),
            "aoi_amendments_count": digest["aoi_count"],
            "retirement_amendments_count": digest["retirement_count"],
            "parse_hash": _json_hash(digest),
            "issues": issues,
            "variants": variants,
            "api_calls": client.api_call_snapshot() - calls_start,
            "duration_s": round(time.perf_counter() - started, 2),
        }
    except asyncio.TimeoutError:
        return {**base, "status": "timeout", "api_calls": client.api_call_snapshot() - calls_start, "duration_s": round(time.perf_counter() - started, 2)}
    except DartClientError as exc:
        return {**base, "status": "dart_error", "error": f"{exc.status}: {exc}", "api_calls": client.api_call_snapshot() - calls_start, "duration_s": round(time.perf_counter() - started, 2)}
    except Exception as exc:  # noqa: BLE001 - audit runner should record and continue.
        return {**base, "status": "exception", "error": f"{type(exc).__name__}: {exc}", "api_calls": client.api_call_snapshot() - calls_start, "duration_s": round(time.perf_counter() - started, 2)}


async def _run_live(rows: list[UniverseRow], docs_dir: Path, out_path: Path, *, concurrency: int, timeout_s: float, batch_size: int, batch_sleep_s: float) -> list[dict[str, Any]]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done: dict[str, dict[str, Any]] = {}
    if out_path.exists():
        with out_path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rec = json.loads(line)
                    done[f"{rec.get('market')}:{rec.get('ticker')}"] = rec

    pending = [row for row in rows if f"{row.market}:{row.ticker}" not in done]
    sem = asyncio.Semaphore(concurrency)

    async def wrapped(row: UniverseRow) -> dict[str, Any]:
        async with sem:
            return await _fetch_one(row, docs_dir, timeout_s)

    results = list(done.values())
    with out_path.open("a", encoding="utf-8") as f:
        for offset in range(0, len(pending), batch_size):
            batch = pending[offset:offset + batch_size]
            tasks = [wrapped(row) for row in batch]
            for coro in asyncio.as_completed(tasks):
                rec = await coro
                results.append(rec)
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                marker = rec.get("status")
                issue_count = len([i for i in rec.get("issues") or [] if i.get("severity") != "INFO"])
                print(
                    f"[live {len(results)}/{len(rows)}] {rec.get('market')}#{rec.get('rank')} "
                    f"{rec.get('ticker')} {rec.get('company')} {marker} "
                    f"agendas={rec.get('agenda_count', 0)} issues={issue_count} "
                    f"api={rec.get('api_calls', 0)} t={rec.get('duration_s', 0)}s",
                    flush=True,
                )
            if offset + batch_size < len(pending):
                print(f"[rate] batch sleep {batch_sleep_s}s after {offset + len(batch)} pending records", flush=True)
                await asyncio.sleep(batch_sleep_s)
    return results


def _load_docs_from_live(live_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for rec in live_records:
        if rec.get("status") != "ok" or not rec.get("doc_path"):
            continue
        path = ROOT / rec["doc_path"]
        if path.exists():
            docs.append(json.loads(path.read_text(encoding="utf-8")))
    return docs


def _recompute_live_records_from_docs(live_path: Path) -> list[dict[str, Any]]:
    if not live_path.exists():
        raise SystemExit(f"missing live file for --recompute-from-docs: {live_path}")
    records: list[dict[str, Any]] = []
    original: list[dict[str, Any]] = []
    with live_path.open(encoding="utf-8") as f:
        original = [json.loads(line) for line in f if line.strip()]

    for rec in original:
        if rec.get("status") != "ok" or not rec.get("doc_path"):
            records.append(rec)
            continue
        path = ROOT / rec["doc_path"]
        if not path.exists():
            records.append({**rec, "status": "missing_local_doc"})
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        parsed = _parse_saved_doc(doc)
        issues = _validate_parse(parsed)
        digest = _digest_parse(parsed, issues)
        records.append({
            **rec,
            "agenda_count": len(parsed.get("agenda_flat") or []),
            "board_candidate_count": len(digest["board_names"]),
            "compensation_item_count": len((parsed.get("compensation") or {}).get("items") or []),
            "aoi_amendments_count": digest["aoi_count"],
            "retirement_amendments_count": digest["retirement_count"],
            "parse_hash": _json_hash(digest),
            "issues": issues,
            "variants": _snippet_windows((doc.get("text") or "")[:200000]),
        })

    backup = live_path.with_suffix(live_path.suffix + ".bak")
    if not backup.exists():
        backup.write_text("".join(json.dumps(rec, ensure_ascii=False) + "\n" for rec in original), encoding="utf-8")
    live_path.write_text("".join(json.dumps(rec, ensure_ascii=False) + "\n" for rec in records), encoding="utf-8")
    return records


def _run_reparse(docs: list[dict[str, Any]], out_path: Path, loop_name: str) -> list[dict[str, Any]]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    with out_path.open("w", encoding="utf-8") as f:
        for idx, doc in enumerate(docs, 1):
            parsed = _parse_saved_doc(doc)
            issues = _validate_parse(parsed)
            digest = _digest_parse(parsed, issues)
            rec = {
                "loop": loop_name,
                "market": doc.get("market"),
                "rank": doc.get("rank"),
                "ticker": doc.get("ticker"),
                "company": doc.get("company"),
                "canonical_name": doc.get("canonical_name"),
                "rcept_no": doc.get("rcept_no"),
                "agenda_count": len(parsed.get("agenda_flat") or []),
                "board_candidate_count": len(digest["board_names"]),
                "compensation_item_count": len((parsed.get("compensation") or {}).get("items") or []),
                "aoi_amendments_count": digest["aoi_count"],
                "retirement_amendments_count": digest["retirement_count"],
                "parse_hash": _json_hash(digest),
                "issues": issues,
                "variants": _snippet_windows((doc.get("text") or "")[:200000]),
            }
            records.append(rec)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if idx % 50 == 0 or idx == len(docs):
                print(f"[{loop_name}] reparsed {idx}/{len(docs)}", flush=True)
    return records


def _write_csvs(out_dir: Path, live_records: list[dict[str, Any]], reparse_records_by_loop: dict[str, list[dict[str, Any]]]) -> None:
    issues_path = out_dir / "issues.csv"
    with issues_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "loop", "market", "rank", "ticker", "company", "rcept_no",
            "severity", "code", "agenda_id", "title", "message", "evidence",
        ])
        writer.writeheader()
        for loop, records in [("loop_01_live", live_records), *reparse_records_by_loop.items()]:
            for rec in records:
                for issue in rec.get("issues") or []:
                    writer.writerow({
                        "loop": loop,
                        "market": rec.get("market"),
                        "rank": rec.get("rank"),
                        "ticker": rec.get("ticker"),
                        "company": rec.get("company"),
                        "rcept_no": rec.get("rcept_no"),
                        **{key: issue.get(key, "") for key in ["severity", "code", "agenda_id", "title", "message", "evidence"]},
                    })

    variants_path = out_dir / "variants.csv"
    seen: set[tuple[str, str, str]] = set()
    with variants_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["market", "rank", "ticker", "company", "rcept_no", "anchor", "snippet"])
        writer.writeheader()
        for rec in live_records:
            for variant in rec.get("variants") or []:
                key = (rec.get("ticker") or "", variant.get("anchor") or "", variant.get("snippet") or "")
                if key in seen:
                    continue
                seen.add(key)
                writer.writerow({
                    "market": rec.get("market"),
                    "rank": rec.get("rank"),
                    "ticker": rec.get("ticker"),
                    "company": rec.get("company"),
                    "rcept_no": rec.get("rcept_no"),
                    "anchor": variant.get("anchor"),
                    "snippet": variant.get("snippet"),
                })


def _summarize(out_dir: Path, rows: list[UniverseRow], live_records: list[dict[str, Any]], reparse_records_by_loop: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    issue_counts: dict[str, int] = {}
    for rec in live_records:
        status_counts[rec.get("status") or "unknown"] = status_counts.get(rec.get("status") or "unknown", 0) + 1
        for issue in rec.get("issues") or []:
            if issue.get("severity") == "INFO":
                continue
            issue_counts[issue["code"]] = issue_counts.get(issue["code"], 0) + 1

    live_hashes = {rec.get("rcept_no"): rec.get("parse_hash") for rec in live_records if rec.get("rcept_no") and rec.get("parse_hash")}
    reparse_diffs: dict[str, int] = {}
    for loop, records in reparse_records_by_loop.items():
        diffs = 0
        for rec in records:
            if live_hashes.get(rec.get("rcept_no")) != rec.get("parse_hash"):
                diffs += 1
        reparse_diffs[loop] = diffs

    summary = {
        "audit_id": AUDIT_ID,
        "generated_at": _now_iso(),
        "universe_count": len(rows),
        "status_counts": status_counts,
        "issue_counts": dict(sorted(issue_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "reparse_hash_diffs_vs_live": reparse_diffs,
        "output_files": {
            "issues_csv": str((out_dir / "issues.csv").relative_to(ROOT)),
            "variants_csv": str((out_dir / "variants.csv").relative_to(ROOT)),
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


async def _main(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    docs_dir = Path(args.docs_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)

    kospi_path = out_dir / "universe_kospi500.csv"
    kosdaq_path = out_dir / "universe_kosdaq150.csv"
    if args.rebuild_universe or not kospi_path.exists() or not kosdaq_path.exists():
        print("[universe] fetching Naver market-cap pages", flush=True)
        kospi_candidates, kosdaq_candidates = await asyncio.gather(
            _fetch_naver_market("KOSPI", args.kospi),
            _fetch_naver_market("KOSDAQ", args.kosdaq),
        )
        kospi_rows, kosdaq_rows = await asyncio.gather(
            _filter_company_universe(kospi_candidates, args.kospi),
            _filter_company_universe(kosdaq_candidates, args.kosdaq),
        )
        if len(kospi_rows) < args.kospi or len(kosdaq_rows) < args.kosdaq:
            raise SystemExit(
                f"company universe shortfall: KOSPI {len(kospi_rows)}/{args.kospi}, "
                f"KOSDAQ {len(kosdaq_rows)}/{args.kosdaq}"
            )
        _write_universe(kospi_path, kospi_rows)
        _write_universe(kosdaq_path, kosdaq_rows)
        print(f"[universe] wrote {kospi_path} n={len(kospi_rows)}", flush=True)
        print(f"[universe] wrote {kosdaq_path} n={len(kosdaq_rows)}", flush=True)
    if args.universe_only:
        return 0

    rows = _load_universe(kospi_path) + _load_universe(kosdaq_path)
    if args.limit:
        rows = rows[:args.limit]
    if args.start:
        rows = rows[args.start:]
    print(f"[audit] rows={len(rows)} concurrency={args.concurrency} batch={args.batch_size}", flush=True)

    live_path = out_dir / "loop_01_live.jsonl"
    if args.recompute_from_docs:
        print("[local] recomputing loop_01_live.jsonl from saved documents", flush=True)
        live_records = _recompute_live_records_from_docs(live_path)
    else:
        live_records = await _run_live(
            rows,
            docs_dir,
            live_path,
            concurrency=args.concurrency,
            timeout_s=args.timeout,
            batch_size=args.batch_size,
            batch_sleep_s=args.batch_sleep,
        )

    docs = _load_docs_from_live(live_records)
    reparse_records_by_loop: dict[str, list[dict[str, Any]]] = {}
    reparse_count = max(0, args.reparse_loops)
    for idx in range(1, reparse_count + 1):
        loop_name = f"loop_02_{idx}"
        reparse_records_by_loop[loop_name] = _run_reparse(docs, out_dir / f"{loop_name}_reparse.jsonl", loop_name)

    _write_csvs(out_dir, live_records, reparse_records_by_loop)
    summary = _summarize(out_dir, rows, live_records, reparse_records_by_loop)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kospi", type=int, default=500)
    parser.add_argument("--kosdaq", type=int, default=150)
    parser.add_argument("--limit", type=int, default=0, help="debug limit after combining universe")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--loops", type=int, default=5, help="kept for compatibility; live loop + reparse loops")
    parser.add_argument("--reparse-loops", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=30)
    parser.add_argument("--batch-sleep", type=float, default=3.0)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--rebuild-universe", action="store_true")
    parser.add_argument("--universe-only", action="store_true")
    parser.add_argument("--recompute-from-docs", action="store_true")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--docs-dir", default=str(DEFAULT_DOCS_DIR))
    args = parser.parse_args()
    if args.batch_size > 30:
        raise SystemExit("--batch-size must be <= 30 per OPM DART batch policy")
    if args.concurrency > 3:
        raise SystemExit("--concurrency must be <= 3 for this audit")
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
