"""주주총회 **소집결의**(거래소 공시 I001) — 이사회가 주총을 열기로 한 단계의 일시·안건과 정정 이력.

주총 공시는 다섯 칸을 지난다 — 이사회 결의 → **소집결의 공시** → 소집공고(주총 2주 전, E006) →
개최 → 결과공시. `shareholder_meeting_notice` 는 소집공고부터 읽어서, 결의만 나온 임시주총은 2주 넘게
「주총 없음」으로 보였다. 결의는 정정도 잦다 — 정정 있는 주총 129/279(47%), 최장 21단
(Agent D `lab/260814-agm-resolution-table`, 2026-07-01~08-14 접수 전수).

**원문 표를 칸 그대로 옮긴다.** 결의 서식은 표 세 가지뿐이다(표 밖에는 제목 말고 글이 없다).

    본표      1. 구분 | 임시주주총회 · 2. 일시 | 2026-09-09 | 10 : 00 · 3. 장소 · 4. 의결권행사기준일 ·
              5. 이사회결의일 · 사외이사 참석(명)/불참(명) · 6. 기타 투자판단 … · ※관련공시
    안건표    번호 | 회의목적사항 | 비고        (세부 안건 `2-1. 독립이사 … 선임의 건` 은 비고 칸에)
    후보표    성명 | 출생년월 | 임기 | 신규선임여부 | 주요경력 …

번호·순서·합친 칸이 회사마다 달라 **이름 칸 낱말로** 값을 찾고, 찾은 줄의 원문(`meta_rows`)을 함께 준다.

정정 이력의 함정(같은 실험의 `TRAPS.md`):
  ① 정정본의 「정정관련 공시서류제출일」은 직전 판이 아니라 **원본**을 가리키는 일이 많다 — 포인터만
     따라가면 중간 판이 빠진다. 그래서 그 회사의 기간 목록(원본+정정)을 통째로 받아 묶는다.
  ② 정정본에는 **본표 없이 대조표만** 있는 판이 있다 — 값은 본표가 있는 가장 최근 판에서 읽고, 그보다
     새 판의 대조표가 그 칸을 고쳤으면 경고한다(휴온스 2026-08-04: 「1. 일시 → -」, 일시를 지웠다).
  ③ 정기·임시가 다르면 같은 주총으로 묶지 않는다(모다이노칩 2026-03).
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import date
from typing import Any

from open_proxy_mcp.clock import today_kst
from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.amendment_table import parse_amendment_table
from open_proxy_mcp.services.company import _company_id, company_not_found_warning, resolve_company_query
from open_proxy_mcp.services.contracts import (
    AnalysisStatus,
    EvidenceRef,
    SourceType,
    ToolEnvelope,
    build_usage,
)
from open_proxy_mcp.services.date_utils import resolve_date_window
from open_proxy_mcp.services.doc_tables import compact, distinct, table_grids
from open_proxy_mcp.services.filing_search import search_filings_by_report_name

TOOL = "shareholder_meeting"

#: 한 주총에서 원문을 여는 판 수 상한(최신부터). 코스나인 25회 같은 극단에서 콜이 폭주하지 않게 —
#: 넘친 판은 목록에만 남기고 개수를 알린다.
MAX_VERSIONS = 12

_CORRECTION = re.compile(r"^\s*\[[^\]]*(?:정정|추가)[^\]]*\]")
_ISO = re.compile(r"(\d{4})\s*[-.년/]\s*(\d{1,2})\s*[-.월/]\s*(\d{1,2})")
# 후보자 식별번호 꼴은 안건에서 지운다 — 회사가 `임시의장 윤창돈(741220-000000)선임의 건` 처럼 적어 오는
# 일이 있다(Agent D 2026-08-18, 158건에 1건). 앞자리가 생년월일이라도 응답에 있을 이유가 없다.
_RRN = re.compile(r"(?<!\d)\d{6}\s*-\s*[\d*]{6,7}(?!\d)")
_PROPOSAL = re.compile(r"주주\s*제안|소수\s*주주")
_PROPOSAL_REPORT = re.compile(r"주주\s*제안\s*(?:내용\s*)?보고")
_SUB_ITEM = re.compile(r"^\s*(?:제\s*)?\d+\s*-\s*\d+")
_UNDETERMINED = re.compile(r"추후\s*확정|확정\s*시|재공시|미정|미확정")
_KRX_LINK = re.compile(r"(?is)<a\b[^>]*href\s*=\s*[\"'][^\"']*acptno=(\d{14})[^\"']*[\"'][^>]*>(.*?)</a>")


def _mask(s: str) -> str:
    return _RRN.sub("******-*******", s or "")


def _blank(s: str) -> bool:
    return compact(s).strip("-－–—") == ""


def _iso(s: str) -> str | None:
    m = _ISO.search(s or "")
    return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3))) if m else None


def meeting_type_from_title(report_nm: str) -> str:
    """`주주총회소집결의 (임시주주총회)` → `extraordinary`. 제목에 없으면 빈 글."""
    c = compact(report_nm)
    if "임시주주총회" in c or "임시총회" in c:
        return "extraordinary"
    if "정기주주총회" in c or "정기총회" in c:
        return "annual"
    return ""


def _meeting_type_label(value: str) -> str:
    c = compact(value)
    if "임시" in c:
        return "extraordinary"
    if "정기" in c:
        return "annual"
    return ""


# ── 한 판 읽기 ─────────────────────────────────────────────────────────

def _is_amendment_grid(grid: list[list[str]]) -> bool:
    # 대조표 머리(`정정항목|정정전|정정후`)는 표 가운데에 온다 — 위에 정정관련 서류·제출일·사유 줄이 먼저 있다.
    return any(any("정정전" in compact(c) for c in row) and any("정정후" in compact(c) for c in row)
               for row in grid)


def _meta_grid(grids: list[list[list[str]]]) -> list[list[str]] | None:
    """본표 — `이사회결의일` 이나 `의결권행사기준일` 을 이름 칸에 가진 표. 대조표는 뺀다."""
    for grid in grids:
        if _is_amendment_grid(grid):
            continue
        labels = [compact(row[0]) for row in grid if row]
        if any("이사회결의일" in x or "의결권행사기준일" in x for x in labels):
            return grid
    return None


def _header_index(grid: list[list[str]], *words: str) -> int | None:
    for i, row in enumerate(grid[:3]):
        flat = [compact(c) for c in row]
        if all(any(w in c for c in flat) for w in words):
            return i
    return None


def parse_meeting_meta(grid: list[list[str]]) -> dict[str, Any]:
    """본표 → 주총 정보. 값은 원문 글자 그대로, 날짜만 따로 YYYY-MM-DD 를 덧붙인다."""
    out: dict[str, Any] = {}
    rows = [distinct(r) for r in grid]
    when: list[str] = []
    notes: list[str] = []
    in_notes = False
    for row in rows:
        label = compact(row[0])
        if len(row) == 1:
            if "기타" in label and "투자판단" in label:
                in_notes = True
            elif in_notes and not _blank(row[0]):
                notes.append(row[0])
            continue
        in_notes = False
        values = [v for v in row[1:] if compact(v) not in ("날짜", "시간")]
        value = " ".join(v for v in values if v).strip()
        if "구분" in label and "주주총회" in compact(value):
            out["meeting_type_text"] = value
        elif "일시" in label:
            when.extend(v for v in values if v)
        elif "장소" in label:
            out["venue"] = value
        elif "의결권행사기준일" in label or label.endswith("기준일"):
            out["record_date"] = value
        elif "이사회결의일" in label:
            out["board_resolution_date"] = value
        elif "감사" in label and "참석" in label and ("아닌" in label or label.lstrip("-").startswith("감사")):
            out["audit_attendance"] = value      # `-감사(사외이사가 아닌 감사위원) 참석여부` — 사외이사 줄보다 먼저 본다
        elif "사외이사" in label and "참석" in label and len(row) >= 3:
            key = "present" if "불참" not in compact(row[1]) else "absent"
            out.setdefault("outside_directors", {})[key] = row[-1]
        elif "관련공시" in label and not _blank(value):
            out["related_filings_text"] = value
    if when:
        out["datetime"] = " ".join(when)
        out["meeting_date"] = _iso(when[0])
    for key in ("record_date", "board_resolution_date"):
        if out.get(key):
            out[key + "_iso"] = _iso(out[key])
    if notes:
        out["notes"] = [_mask(n) for n in notes]
    out["meta_rows"] = [[_mask(c) for c in r] for r in rows]
    return out


def parse_agenda(grid: list[list[str]]) -> dict[str, Any]:
    """안건표 → `{status, items}`. 칸 글 그대로(식별번호 꼴만 가림) + 힌트(세부 안건·주주제안·집중투표)."""
    hi = _header_index(grid, "회의목적사항")
    if hi is None:
        return {"status": "absent", "items": []}
    head = [compact(c) for c in grid[hi]]
    col_title = next(i for i, c in enumerate(head) if "회의목적사항" in c)
    col_no = next((i for i, c in enumerate(head) if "번호" in c), None)
    col_note = next((i for i, c in enumerate(head) if "비고" in c), None)
    items: list[dict[str, Any]] = []
    for row in grid[hi + 1:]:
        if len(row) <= col_title:
            continue
        title = row[col_title]
        no = row[col_no] if col_no is not None and col_no < len(row) else ""
        note = row[col_note] if col_note is not None and col_note < len(row) else ""
        if _blank(title) and _blank(no) and _blank(note):
            continue
        item: dict[str, Any] = {"no": no, "title": _mask(title)}
        if not _blank(note):
            item["note"] = _mask(note)
            subs = [line for line in note.split("\n") if _SUB_ITEM.match(line)]
            if subs:
                item["sub_items"] = [_mask(s) for s in subs]
        both = f"{title}\n{note}"
        if _PROPOSAL.search(both) and not _PROPOSAL_REPORT.search(both):
            item["shareholder_proposal"] = True
        if "집중투표" in compact(title):
            item["cumulative_voting"] = True
        items.append(item)
    return {"status": "listed" if items else "undetermined", "items": items}


def _rows_as_dicts(grid: list[list[str]], hi: int) -> dict[str, Any]:
    cols = distinct(grid[hi])
    rows = []
    for row in grid[hi + 1:]:
        cells = distinct(row) if len(distinct(row)) == len(cols) else row[:len(cols)]
        if all(_blank(c) for c in cells):
            continue
        rows.append({cols[i]: _mask(cells[i]) for i in range(min(len(cols), len(cells)))})
    return {"columns": cols, "rows": rows}


def parse_resolution_document(html: str) -> dict[str, Any]:
    """소집결의 한 판(원문 태그째) → 본표·안건표·후보표·나머지 표·정정 비교표·관련공시 링크."""
    grids = table_grids(html)
    meta_grid = _meta_grid(grids)
    agenda_grid = next((g for g in grids
                        if not _is_amendment_grid(g) and _header_index(g, "회의목적사항") is not None), None)
    out: dict[str, Any] = {
        "has_main_table": meta_grid is not None,
        "meeting": parse_meeting_meta(meta_grid) if meta_grid else {},
        "agenda": parse_agenda(agenda_grid) if agenda_grid else {"status": "absent", "items": []},
        "candidates": [],
        "other_tables": [],
    }
    for g in grids:
        if g is meta_grid or g is agenda_grid or _is_amendment_grid(g):
            continue
        hi = _header_index(g, "성명")
        if hi is not None and any(("출생" in compact(c) or "생년" in compact(c)) for c in g[hi]):
            out["candidates"].append(_rows_as_dicts(g, hi))
        elif len(g) >= 2 and len(distinct(g[0])) >= 2:
            out["other_tables"].append(_rows_as_dicts(g, 0))
    links = [{"text": " ".join(re.sub(r"<[^>]+>", " ", t).split()), "krx_acptno": a}
             for a, t in _KRX_LINK.findall(html or "")]
    if links:
        out["related_links"] = links
    return out


# ── 판 묶기 ───────────────────────────────────────────────────────────

def is_correction(report_nm: str) -> bool:
    return bool(_CORRECTION.match(report_nm or ""))


def group_filings(filings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """접수 순으로 원본이 묶음을 열고, 정정은 같은 정기/임시의 가장 최근 묶음에 붙는다.

    제목만 보는 1차 묶기다(DART 0콜). 원공시 날짜는 원문을 연 뒤 `_check_pointers` 가 대조한다.
    """
    groups: list[dict[str, Any]] = []
    for f in sorted(filings, key=lambda r: (r.get("rcept_dt", ""), r.get("rcept_no", ""))):
        mtype = meeting_type_from_title(f.get("report_nm", ""))
        if not is_correction(f.get("report_nm", "")):
            groups.append({"meeting_type": mtype, "filings": [f], "orphan": False})
            continue
        target = next((g for g in reversed(groups)
                       if not mtype or not g["meeting_type"] or g["meeting_type"] == mtype), None)
        if target is None:
            groups.append({"meeting_type": mtype, "filings": [f], "orphan": True})
        else:
            target["filings"].append(f)
            target["meeting_type"] = target["meeting_type"] or mtype
    return groups


def _version_label(index: int, filing: dict[str, Any], orphan: bool) -> str:
    if index == 0 and not orphan:
        return "원본"
    n = index if not orphan else index + 1
    return f"정정 {n}차"


def _check_pointers(versions: list[dict[str, Any]], prior_dates: set[str] | None = None) -> list[str]:
    """정정본이 가리키는 원공시 날짜가 이 묶음의 앞 판에 있는가. 없으면 다른 주총의 정정일 수 있다.

    `prior_dates` — 열지 않고 목록에만 남긴 앞 판들의 접수일. 원본이 거기 있으면 맞는 것이다.
    """
    warnings: list[str] = []
    seen_dates: set[str] = set(prior_dates or ())
    for v in versions:
        amend = v.get("amendment") or {}
        pointed = amend.get("original_filed_date")
        if v["is_correction"] and pointed:
            pointed_compact = pointed.replace("-", "")
            if seen_dates and pointed_compact not in seen_dates:
                v["pointer_check"] = "mismatch"
                warnings.append(
                    f"{v['label']}({v['rcept_no']})이 가리키는 원공시({pointed})가 이 묶음에 없다 — "
                    "다른 주총의 정정이거나 원본이 조회 구간 밖이다.")
            else:
                v["pointer_check"] = "ok" if seen_dates else "original_outside_window"
        seen_dates.add(v["rcept_dt"])
    return warnings


_FIELD_WORDS = {"datetime": "일시", "venue": "장소", "record_date": "기준일",
                "board_resolution_date": "이사회결의일", "meeting_type_text": "구분"}


def _current_state(versions: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """본표·안건은 그것이 있는 가장 최근 판에서. 그 뒤 판의 대조표가 같은 칸을 고쳤으면 알린다."""
    warnings: list[str] = []
    meeting: dict[str, Any] = {}
    agenda: dict[str, Any] = {"status": "absent", "items": []}
    newest_first = list(reversed(versions))
    for i, v in enumerate(newest_first):
        if v["parsed"].get("has_main_table"):
            meeting = {**v["parsed"]["meeting"], "source_rcept_no": v["rcept_no"], "source_label": v["label"]}
            later = newest_first[:i]
            pending = []
            for lv in reversed(later):
                for row in (lv.get("amendment") or {}).get("rows", []):
                    item = compact(row.get("item", ""))
                    for field, word in _FIELD_WORDS.items():
                        if word in item:
                            pending.append({"field": field, "version": lv["label"], "rcept_no": lv["rcept_no"],
                                            "item": row["item"], "before": row["before"], "after": row["after"],
                                            "change": row["change"]})
            if pending:
                meeting["later_amendments"] = pending
                warnings.append(
                    f"본표는 {v['label']}({v['rcept_no']}) 기준이다 — 그 뒤 정정의 비교표가 "
                    + ", ".join(f"「{p['item']}」({p['before']} → {p['after']})" for p in pending[:3])
                    + " 를 고쳤다. 고친 값이 우선이다.")
            break
    for v in newest_first:
        a = v["parsed"].get("agenda") or {}
        if a.get("status") in ("listed", "undetermined"):
            agenda = {**a, "source_rcept_no": v["rcept_no"], "source_label": v["label"]}
            if v is not newest_first[0]:
                warnings.append(f"최신 판에 안건표가 없어 {v['label']}({v['rcept_no']})의 안건표를 읽었다.")
            break
    return meeting, agenda, warnings


# ── 조회 ─────────────────────────────────────────────────────────────

def _window(year: int | None, start_date: str, end_date: str, lookback_months: int) -> tuple[date, date, list[str]]:
    if year and not start_date and not end_date:
        return date(year, 1, 1), min(date(year, 12, 31), today_kst()), []
    return resolve_date_window(start_date=start_date, end_date=end_date, lookback_months=lookback_months)


async def _open_versions(filings: list[dict[str, Any]], orphan: bool) -> list[dict[str, Any]]:
    client = get_dart_client()
    docs = await asyncio.gather(*[client.get_document_cached(f["rcept_no"]) for f in filings],
                                return_exceptions=True)
    versions = []
    for i, (f, doc) in enumerate(zip(filings, docs)):
        v: dict[str, Any] = {
            "rcept_no": f.get("rcept_no", ""), "rcept_dt": f.get("rcept_dt", ""),
            "report_nm": " ".join((f.get("report_nm") or "").split()),
            "is_correction": is_correction(f.get("report_nm", "")),
            "label": "", "index": i,
        }
        if isinstance(doc, BaseException):
            v["parsed"] = {"has_main_table": False, "agenda": {"status": "absent", "items": []}}
            v["error"] = f"DART {getattr(doc, 'status', type(doc).__name__)}"
        else:
            html = (doc or {}).get("html") or ""
            v["parsed"] = parse_resolution_document(html)
            amend = parse_amendment_table(html)
            if v["is_correction"] or amend["tables"]:
                v["is_correction"] = True
                v["amendment"] = {k: amend[k] for k in ("status", "reason", "original_filed_date", "rows", "note")
                                  if amend.get(k) not in (None, "", [])}
        versions.append(v)
    return versions


def _envelope(status, subject, warnings, data, *, evidence=None, next_actions=None) -> dict[str, Any]:
    return ToolEnvelope(tool=TOOL, status=status, subject=subject, warnings=warnings, data=data,
                        evidence_refs=evidence or [], next_actions=next_actions or []).to_dict()


async def build_meeting_resolution_payload(
    company_query: str,
    *,
    meeting_type: str = "auto",
    year: int | None = None,
    start_date: str = "",
    end_date: str = "",
    lookback_months: int = 12,
    rcept_no: str = "",
) -> dict[str, Any]:
    """`shareholder_meeting_notice(scope="resolution")` 본체."""
    started = time.perf_counter()
    timings: dict[str, int] = {}
    client = get_dart_client()
    calls0 = client.api_call_snapshot()

    def _done(data: dict[str, Any]) -> dict[str, Any]:
        timings["total"] = int((time.perf_counter() - started) * 1000)
        return {**data, "scope": "resolution", "usage": build_usage(client.api_call_snapshot() - calls0),
                "timings_ms": timings}

    if rcept_no:
        versions = await _open_versions([{"rcept_no": rcept_no, "rcept_dt": rcept_no[:8],
                                          "report_nm": ""}], orphan=False)
        versions[0]["label"] = "지정한 판"
        meeting, agenda, warns = _current_state(versions)
        p = versions[0]["parsed"]
        data = _done({"query": company_query, "rcept_no": rcept_no,
                      "meeting_type": _meeting_type_label(meeting.get("meeting_type_text", "")),
                      "meeting": meeting, "agenda": agenda,
                      "candidates": p.get("candidates", []), "other_tables": p.get("other_tables", []),
                      "related_links": p.get("related_links", []),
                      "versions": [_public_version(v) for v in versions], "versions_total": 1})
        status = (AnalysisStatus.EXACT if meeting.get("meta_rows") and agenda["status"] != "absent"
                  else AnalysisStatus.PARTIAL)
        if versions[0].get("error"):
            status = AnalysisStatus.ERROR
        return _envelope(status, company_query, warns + ["정정 이력은 회사 이름으로 부르면 함께 준다(이 호출은 지정한 한 판만 읽었다)."],
                         data, evidence=[_evidence(versions[0])])

    resolution = await resolve_company_query(company_query)
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        return _envelope(AnalysisStatus.AMBIGUOUS, company_query, ["회사 식별이 애매해 소집결의를 고르지 않았다."],
                         _done({"query": company_query, "candidates": [
                             {"company_id": _company_id(c), "corp_name": c.get("corp_name", ""),
                              "ticker": c.get("stock_code", ""), "corp_code": c.get("corp_code", "")}
                             for c in resolution.candidates[:10]]}),
                         next_actions=["ticker 또는 corp_code로 다시 조회"])
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return _envelope(AnalysisStatus.ERROR, company_query, [company_not_found_warning(company_query)],
                         _done({"query": company_query}), next_actions=["company tool로 먼저 회사 식별 확인"])
    corp = resolution.selected
    start, end, warnings = _window(year, start_date, end_date, lookback_months)
    window = {"start_date": start.isoformat(), "end_date": end.isoformat()}

    t0 = time.perf_counter()
    filings, notices, error = await search_filings_by_report_name(
        corp_code=corp["corp_code"], bgn_de=start.strftime("%Y%m%d"), end_de=end.strftime("%Y%m%d"),
        pblntf_tys="", pblntf_detail_ty="I001", keywords=("주주총회소집결의",), strip_spaces=True,
        last_reprt_at="N")
    timings["search_filings"] = int((time.perf_counter() - t0) * 1000)
    base = {"query": company_query, "company_id": _company_id(corp), "corp_name": corp.get("corp_name", ""),
            "requested_window": window}
    if error and error != "013":
        return _envelope(AnalysisStatus.ERROR, corp.get("corp_name", company_query),
                         [f"DART 공시 검색 실패: {error}"], _done(base))
    groups = group_filings(filings or [])
    if meeting_type in ("annual", "extraordinary"):
        groups = [g for g in groups if g["meeting_type"] in (meeting_type, "")]
    if not groups:
        return _envelope(AnalysisStatus.NO_FILING, corp.get("corp_name", company_query),
                         [*notices, f"조사 구간({window['start_date']}~{window['end_date']}) 내 주주총회 소집결의 없음 (정상)"],
                         _done(base), next_actions=["start_date·year 로 구간을 넓히거나, 소집공고는 scope=\"summary\""])
    groups.sort(key=lambda g: (g["filings"][-1].get("rcept_dt", ""), g["filings"][-1].get("rcept_no", "")))
    chosen = groups[-1]
    others = [_group_brief(g) for g in reversed(groups[:-1])]

    filings_all = chosen["filings"]
    opened = filings_all[-MAX_VERSIONS:]
    t0 = time.perf_counter()
    versions = await _open_versions(opened, chosen["orphan"])
    timings["open_documents"] = int((time.perf_counter() - t0) * 1000)
    offset = len(filings_all) - len(opened)
    for v in versions:
        v["label"] = _version_label(v["index"] + offset, v, chosen["orphan"])
    if offset:
        warnings.append(f"이 주총의 결의 공시 {len(filings_all)}판 중 최근 {len(opened)}판만 열었다 — 앞 판은 목록만 싣는다.")
    if chosen["orphan"]:
        warnings.append("이 주총은 조회 구간 안에 원본 결의가 없다(정정만 있다) — start_date 를 앞당기면 원본까지 묶인다.")
    warnings += _check_pointers(versions, {f.get("rcept_dt", "") for f in filings_all[:offset]})
    meeting, agenda, state_warnings = _current_state(versions)
    warnings += state_warnings
    mtype = chosen["meeting_type"] or _meeting_type_label(meeting.get("meeting_type_text", ""))
    if agenda.get("status") == "undetermined":
        hint = next((n for n in meeting.get("notes", []) if _UNDETERMINED.search(n)), "")
        warnings.append("안건이 아직 정해지지 않았다" + (f" — 원문: 「{hint}」" if hint else " — 안건표가 `-` 뿐이다."))
    newest = next((v for v in reversed(versions) if v["parsed"].get("has_main_table")), versions[-1])

    t0 = time.perf_counter()
    notice = await _notice_after(corp["corp_code"], filings_all[0].get("rcept_dt", ""), end)
    timings["notice_lookup"] = int((time.perf_counter() - t0) * 1000)

    data = _done({
        **base,
        "requested_meeting_type": meeting_type,
        "meeting_type": mtype,
        "meeting": meeting,
        "agenda": agenda,
        "candidates": newest["parsed"].get("candidates", []),
        "other_tables": newest["parsed"].get("other_tables", []),
        "related_links": newest["parsed"].get("related_links", []),
        "versions": [_public_version(v) for v in versions],
        "versions_total": len(filings_all),
        "versions_listed_only": [{"rcept_no": f.get("rcept_no", ""), "rcept_dt": f.get("rcept_dt", ""),
                                  "report_nm": " ".join((f.get("report_nm") or "").split())}
                                 for f in filings_all[:offset]],
        "notice": notice,
        "other_meetings": others,
    })
    if meeting.get("meta_rows") and agenda.get("status") in ("listed", "undetermined") \
            and not any(v.get("error") for v in versions):
        status = AnalysisStatus.EXACT
    else:
        status = AnalysisStatus.PARTIAL
        if not meeting.get("meta_rows"):
            warnings.append("어느 판에서도 본표(일시·장소·기준일)를 못 읽었다 — 원문 링크로 확인할 것.")
    next_actions = []
    if notice.get("found"):
        next_actions.append(f"소집공고가 나왔다({notice['rcept_no']}) — 후보·보수한도·정관 상세는 scope=\"summary\"·\"board\"·\"compensation\"")
    else:
        next_actions.append("소집공고(주총 2주 전) 전 단계다 — 안건 상세·후보 이력은 공고가 나오면 scope=\"summary\"")
    return _envelope(status, corp.get("corp_name", company_query), [*notices, *warnings], data,
                     evidence=[_evidence(v) for v in versions], next_actions=next_actions)


async def _notice_after(corp_code: str, first_dt: str, end: date) -> dict[str, Any]:
    """같은 회사의 소집공고(E006)가 결의 뒤에 나왔나 — 목록 1콜. 실패하면 「모른다」."""
    if not first_dt:
        return {"found": None}
    try:
        notices, _n, error = await search_filings_by_report_name(
            corp_code=corp_code, bgn_de=first_dt, end_de=max(end, today_kst()).strftime("%Y%m%d"),
            pblntf_tys="", pblntf_detail_ty="E006", keywords=("소집",), last_reprt_at="Y", max_pages=1)
    except DartClientError:
        return {"found": None}
    if error and error != "013":
        return {"found": None}
    if not notices:
        return {"found": False}
    top = notices[0]
    return {"found": True, "rcept_no": top.get("rcept_no", ""), "rcept_dt": top.get("rcept_dt", ""),
            "report_nm": " ".join((top.get("report_nm") or "").split())}


def _group_brief(g: dict[str, Any]) -> dict[str, Any]:
    fs = g["filings"]
    return {"meeting_type": g["meeting_type"], "versions": len(fs),
            "first_rcept_no": fs[0].get("rcept_no", ""), "first_rcept_dt": fs[0].get("rcept_dt", ""),
            "last_rcept_no": fs[-1].get("rcept_no", ""), "last_rcept_dt": fs[-1].get("rcept_dt", "")}


def _public_version(v: dict[str, Any]) -> dict[str, Any]:
    out = {k: v[k] for k in ("rcept_no", "rcept_dt", "report_nm", "label", "is_correction") if k in v}
    out["has_main_table"] = bool(v["parsed"].get("has_main_table"))
    out["agenda_status"] = (v["parsed"].get("agenda") or {}).get("status", "absent")
    for k in ("amendment", "pointer_check", "error"):
        if v.get(k):
            out[k] = v[k]
    return out


def _evidence(v: dict[str, Any]) -> EvidenceRef:
    return EvidenceRef(evidence_id=f"ev_resolution_{v['rcept_no']}", source_type=SourceType.DART_XML,
                       rcept_no=v["rcept_no"], rcept_dt=v.get("rcept_dt", ""),
                       report_nm=v.get("report_nm") or "주주총회소집결의", section=v.get("label", ""))
