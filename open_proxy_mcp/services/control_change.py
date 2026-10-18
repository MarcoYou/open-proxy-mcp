"""최대주주변경 계열 거래소 공시(I001) — **최대주주가 바뀐 사실과 바뀌게 될 계약**을 원문 칸 그대로.

`최대주주등소유주식변동신고서`(I004, `ownership_structure(changes)` 가 이미 읽는다)는 최대주주가 **그대로인 채**
지분이 움직인 것이다. 이름이 닮았지만 다른 공시다(Agent D 2026-08-26). 이 모듈은 바뀌는 쪽 다섯 서식을 읽는다.

    change              최대주주변경                                   바뀐 사실           0.87건/일
    transfer_contract   최대주주 변경을 수반하는 주식양수도 계약 체결     바뀌게 될 계약      0.20건/일
    pledge_contract     … 주식 담보제공 계약 체결                       담보를 실행하면 바뀐다 0.30건/일
    pledge_release      … 주식 담보제공 계약 해제ㆍ취소 등              그 담보가 풀렸다
    transfer_release    … 주식 양수도 계약 해제ㆍ취소 등                그 계약이 풀렸다   (해제 둘 합 0.22건/일)

(건수는 코스피 2026-08 실측.) 다섯 서식 다 「이름 칸 | 값」 표라 **모델을 부르지 않는다.** 표를 합친 칸까지 펼친
격자로 읽어 줄마다 이름 경로(`1. 변경내용 › 변경전 › 소유비율(%)`)와 값을 얻고, 서식별 낱말로 칸을 고른다.
값은 원문 글자 그대로이고, 찾은 줄 원문(`rows`)과 보유 현황·담보 내역 표를 함께 싣는다.

🔴 **인적사항 표는 싣지 않는다** — 담보 서식 뒤쪽의 채무자 인적사항, 최대주주변경 서식의 새 최대주주 기본정보처럼
머리에 주소·생년월일 칸이 있는 표. 당사자가 개인일 수 있다. 빠졌다는 사실만 `profile_omitted` 로 남긴다.
"""

from __future__ import annotations

import asyncio
from typing import Any

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.amendment_table import parse_amendment_table
from open_proxy_mcp.services.doc_tables import compact, distinct, table_grids

#: 회사 한 곳에서 원문을 여는 사건 수 상한(최신부터) — 넘친 것은 목록만.
MAX_EVENTS = 8

KIND_LABEL = {
    "change": "최대주주 변경",
    "transfer_contract": "최대주주 변경 수반 주식양수도 계약 체결",
    "pledge_contract": "최대주주 변경 수반 주식담보제공 계약 체결",
    "pledge_release": "최대주주 변경 수반 주식담보 계약 해제·취소",
    "transfer_release": "최대주주 변경 수반 주식양수도 계약 해제·취소",
}


def classify(report_nm: str) -> str | None:
    """공시 제목 → 서식 이름. 소유주식 변동신고서(`(최대주주변경시)` 가 제목에 붙는다)는 아니다."""
    c = compact(report_nm).replace("ㆍ", "").replace("·", "")
    if "최대주주변경" not in c or "소유주식변동" in c:
        return None
    released = "해제" in c or "취소" in c or "해소" in c
    if "담보" in c:
        return "pledge_release" if released else "pledge_contract"
    if "양수도" in c:
        return "transfer_release" if released else "transfer_contract"
    return "change"


# ── 표 읽기 ───────────────────────────────────────────────────────────

def _label_rows(grid: list[list[str]]) -> list[tuple[str, list[str]]]:
    """격자 → `(이름 경로, 칸들)`. 이름 경로는 값 앞 칸들을 공백 없이 이은 것, 칸들은 원문 그대로."""
    out = []
    for row in grid:
        cells = distinct(row)
        out.append(("›".join(compact(c) for c in cells[:-1]) if len(cells) > 1 else compact(cells[0]), cells))
    return out


def _pick(rows, *words: str, exclude: tuple[str, ...] = ()) -> str | None:
    """이름 경로에 `words` 가 다 있고 `exclude` 가 없는 첫 줄의 값(마지막 칸)."""
    for path, cells in rows:
        if len(cells) < 2:
            continue
        if all(w in path for w in words) and not any(x in path for x in exclude):
            v = cells[-1]
            return None if compact(v) in ("", "-") else v
    return None


def _party(rows, word: str) -> dict[str, str] | None:
    """`- 양도인 | 이름 | 회사와의 관계 | 관계` 줄 → `{name, relation}`."""
    for _path, cells in rows:
        for i, c in enumerate(cells):
            if word in compact(c) and i + 1 < len(cells):
                out = {"name": cells[i + 1]}
                rel = next((cells[j + 1] for j in range(i + 1, len(cells) - 1)
                            if "회사와의관계" in compact(cells[j])), None)
                if rel and compact(rel) not in ("", "-"):
                    out["relation"] = rel
                return out
    return None


def _notes(rows) -> tuple[list[str], str | None]:
    """`기타 투자판단에 참고할 사항` 아래 한 칸 줄들과 `※관련공시` 값."""
    notes: list[str] = []
    related = None
    on = False
    for path, cells in rows:
        if "관련공시" in compact(cells[-2] if len(cells) >= 2 else "") or "관련공시" in path:
            v = cells[-1]
            related = None if compact(v) in ("", "-") or "관련공시" in compact(v) else v
            on = False
            continue
        if "기타투자판단" in path:
            on = True
            if len(cells) >= 2 and compact(cells[-1]) not in ("", "-") and "기타투자판단" not in compact(cells[-1]):
                notes.append(cells[-1])
            continue
        if on and len(cells) == 1 and compact(cells[0]) not in ("", "-"):
            notes.append(cells[0])
        elif len(cells) > 1:
            on = False
    return notes, related


_FIELDS: dict[str, list[tuple[str, tuple[str, ...], tuple[str, ...]]]] = {
    # (출력 칸, 이름 경로에 다 있어야 할 낱말, 있으면 안 되는 낱말)
    "change": [
        ("before_holder", ("변경전", "최대주주등"), ()), ("before_shares", ("변경전", "소유주식수"), ()),
        ("before_pct", ("변경전", "소유비율"), ()), ("after_holder", ("변경후", "최대주주등"), ()),
        ("after_shares", ("변경후", "소유주식수"), ()), ("after_pct", ("변경후", "소유비율"), ()),
        ("reason", ("변경사유",), ()), ("purpose", ("지분인수목적",), ()), ("funding", ("인수자금",), ()),
        ("board_plan", ("임원",), ()), ("changed_on", ("변경일자",), ()), ("confirmed_on", ("변경확인일자",), ()),
    ],
    "transfer_contract": [
        ("shares", ("양수도주식수",), ()), ("price_per_share", ("1주당가액",), ()),
        ("amount", ("양수도대금",), ("지급일정",)), ("payment_terms", ("지급일정",), ()),
        ("lockup", ("의무보유",), ()), ("expected_holder", ("변경예정최대주주",), ("예정일자", "예정소유")),
        ("expected_on", ("변경예정일자",), ()), ("expected_shares", ("예정소유주식수",), ()),
        ("expected_pct", ("예정소유비율",), ()), ("contract_on", ("계약일자",), ()),
    ],
    "pledge_contract": [
        ("pledgor", ("명칭",), ()), ("pledgor_shares_now", ("공시일현재", "주식수"), ()),
        ("pledgor_pct_now", ("공시일현재", "지분율"), ()), ("shares_if_executed", ("담보권전부실행시", "주식수"), ()),
        ("pct_if_executed", ("담보권전부실행시", "지분율"), ()), ("debt_total", ("채무", "금액총액"), ()),
        ("pledge_total", ("담보설정금액총액",), ()), ("cumulative_contracts", ("누적담보제공계약건수",), ()),
        ("cumulative_pledged_shares", ("누적담보제공주식총수",), ()), ("contract_on", ("설정계약체결일",), ()),
    ],
    "pledge_release": [
        ("creditor", ("채권자",), ()), ("pledgor", ("계약당사자", "담보제공자"), ()), ("debtor", ("채무자",), ()),
        ("reason", ("해소사유",), ()), ("released_shares", ("해소된담보제공주식수",), ()),
        ("released_amount", ("해소된담보설정금액",), ()), ("released_contracts", ("해소된계약건수",), ()),
        ("released_on", ("담보계약해소일",), ()),
        ("pledgor_shares", ("소유주식수",), ("담보",)), ("pledgor_pct", ("지분율",), ()),
    ],
    "transfer_release": [
        ("release_kind", ("계약해소종류",), ()), ("reason", ("계약해소사유",), ()),
        ("shares", ("양수도주식수",), ()), ("price_per_share", ("1주당가액",), ()),
        ("amount", ("양수도대금",), ("지급일정",)), ("contract_on", ("계약체결일",), ()),
        ("released_on", ("계약해소일",), ()),
    ],
}


def _columns(grid: list[list[str]]) -> tuple[list[str], int]:
    """머리 두 줄(`변경전 | 변경후` 아래 `주식수 | 지분율`)이면 합쳐 `변경전 주식수` 꼴로. 데이터 시작 줄도."""
    head = grid[0]
    if len(grid) > 1 and len(grid[1]) == len(head) and grid[1] != head and not any(
            any(ch.isdigit() for ch in c) for c in grid[1]):
        cols = [a if a == b else f"{a} {b}" for a, b in zip(head, grid[1])]
        return distinct(cols), 2
    return distinct(head), 1


def _table(grid: list[list[str]]) -> dict[str, Any]:
    cols, start = _columns(grid)
    rows = [distinct(r) for r in grid[start:] if any(compact(c) not in ("", "-") for c in r)]
    return {"columns": cols, "rows": rows}


def parse_control_document(html: str, kind: str) -> dict[str, Any]:
    """원문 한 판 → `{fields, notes, related, rows, holders?, pledges?, pledge_conditions?, comparison?}`."""
    grids = table_grids(html)
    main = next((g for g in grids if not any("정정전" in compact(c) for r in g for c in r)
                 and len(g) >= 4 and any(len(distinct(r)) >= 2 for r in g)), None)
    out: dict[str, Any] = {"fields": {}, "rows": []}
    if main is None:
        out["status"] = "no_main_table"
        return out
    rows = _label_rows(main)
    fields: dict[str, Any] = {}
    for name, words, exclude in _FIELDS.get(kind, []):
        v = _pick(rows, *words, exclude=exclude)
        if v is not None:
            fields[name] = v
    for name, word in (("transferor", "양도인"), ("transferee", "양수인")):
        p = _party(rows, word)
        if p:
            fields[name] = p
    out["fields"] = fields
    notes, related = _notes(rows)
    if notes:
        out["notes"] = notes
    if related:
        out["related"] = related
    # 해제 서식의 「해소 전후 비교」(`구분 | 해소 전 | 해소 후`) — 본표 안에 있다
    comp = [cells for _p, cells in rows if len(cells) == 3 and "구분" not in compact(cells[0])
            and any(w in compact(cells[0]) for w in ("계약건수", "주식총수", "금액총액"))]
    if comp:
        out["comparison"] = [{"item": c[0], "before": c[1], "after": c[2]} for c in comp]
    out["rows"] = [cells for _p, cells in rows]
    for g in grids:
        if g is main:
            continue
        head = compact(" ".join(g[0]))
        top = [compact(c) for r in g[:3] for c in r]
        if any("인적사항" in c or "주소" in c or "생년월일" in c for c in top):
            out["profile_omitted"] = True               # 주소·생년월일 — 싣지 않는다
            continue
        if "정정전" in head or any("정정전" in compact(c) for r in g for c in r):
            continue
        if "성명" in head and "관계" in head:
            out["holders"] = _table(g)
        elif "채권자" in head:
            out["pledges"] = _table(g)
        elif "담보권실행조건" in head:
            out["pledge_conditions"] = _table(g)
        else:
            out.setdefault("other_tables", []).append(_table(g))
    out["status"] = "parsed" if fields else "fields_not_found"
    return out


def event_from_document(filing: dict[str, Any], html: str) -> dict[str, Any]:
    kind = classify(filing.get("report_nm", "")) or "change"
    ev: dict[str, Any] = {
        "kind": kind, "kind_label": KIND_LABEL[kind],
        "rcept_no": filing.get("rcept_no", ""), "rcept_dt": filing.get("rcept_dt", ""),
        "report_nm": " ".join((filing.get("report_nm") or "").split()),
        **parse_control_document(html, kind),
    }
    amend = parse_amendment_table(html)
    if amend["tables"] or amend["status"] == "reason_only":
        ev["amendment"] = {k: amend[k] for k in ("status", "reason", "original_filed_date", "rows", "note")
                           if amend.get(k) not in (None, "", [])}
    return ev


async def fetch_control_events(
    corp_code: str, bgn_de: str, end_de: str, client=None,
) -> tuple[list[dict[str, Any]], list[str], int]:
    """회사·구간의 최대주주변경 계열 공시 → 사건 목록(최신 먼저). `(events, warnings, 목록에만 남긴 수)`."""
    from open_proxy_mcp.services.filing_search import search_filings_by_report_name
    client = client or get_dart_client()
    filings, notices, error = await search_filings_by_report_name(
        corp_code=corp_code, bgn_de=bgn_de, end_de=end_de, pblntf_tys="", pblntf_detail_ty="I001",
        keywords=("최대주주변경",), strip_spaces=True, last_reprt_at="N")
    warnings = list(notices)
    if error and error != "013":
        return [], [*warnings, f"최대주주변경 공시 조회 실패: {error}"], 0
    filings = [f for f in (filings or []) if classify(f.get("report_nm", ""))]
    filings.sort(key=lambda r: (r.get("rcept_dt", ""), r.get("rcept_no", "")), reverse=True)
    opened = filings[:MAX_EVENTS]
    docs = await asyncio.gather(*[client.get_document_cached(f["rcept_no"]) for f in opened],
                                return_exceptions=True)
    events = []
    for f, doc in zip(opened, docs):
        if isinstance(doc, BaseException):
            status = getattr(doc, "status", type(doc).__name__)
            events.append({"kind": classify(f["report_nm"]), "kind_label": KIND_LABEL[classify(f["report_nm"])],
                           "rcept_no": f["rcept_no"], "rcept_dt": f.get("rcept_dt", ""),
                           "report_nm": " ".join(f["report_nm"].split()), "status": "error",
                           "error": f"DART {status}"})
            continue
        events.append(event_from_document(f, (doc or {}).get("html") or ""))
    rest = len(filings) - len(opened)
    if rest:
        warnings.append(f"최대주주변경 계열 공시 {len(filings)}건 중 최근 {len(opened)}건만 원문을 열었다.")
    return events, warnings, rest


async def build_control_event_payload(company: str = "", *, rcept_no: str = "", **_ignored) -> dict[str, Any]:
    """스크리너 details 용 — 접수번호 한 건. `{status, data: {event}}`."""
    client = get_dart_client()
    try:
        doc = await client.get_document_cached(rcept_no)
    except DartClientError as exc:
        return {"status": "error", "data": {}, "warnings": [f"DART {exc.status}"]}
    filing = {"rcept_no": rcept_no, "rcept_dt": rcept_no[:8], "report_nm": _ignored.get("report_nm", "")}
    html = (doc or {}).get("html") or ""
    if not filing["report_nm"]:
        # 제목을 모르면 원문 머리(`<title>회사/서식/…`)에서
        import re
        t = re.search(r"(?is)<title>(.*?)</title>", html)
        filing["report_nm"] = (t.group(1).split("/")[1] if t and "/" in t.group(1) else "")
    ev = event_from_document(filing, html)
    return {"status": "exact" if ev.get("status") == "parsed" else "partial", "data": {"event": ev}}
