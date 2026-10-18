"""`shareholder_meeting_notice(scope="resolution")` md — 이사회 소집결의 원문 표를 칸 글 그대로 그린다."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.tools._shareholder_meeting_render import render_error, warning_block

_TYPE_LABEL = {"annual": "정기주주총회", "extraordinary": "임시주주총회"}
_CELL_MAX = 120          # 표 칸 글자 상한 — 넘으면 `…`(잘랐다는 표시). 전체는 json
_CANDIDATE_CELL_MAX = 40
_AMEND_ROWS = 3


def _cell(s: Any, limit: int = _CELL_MAX) -> str:
    v = " / ".join(str(s or "").split("\n")).replace("|", "｜") or "-"
    return v if len(v) <= limit else v[:limit] + "…"


def _meeting_lines(m: dict[str, Any]) -> list[str]:
    rows = [("일시", m.get("datetime")), ("장소", m.get("venue")),
            ("의결권행사기준일", m.get("record_date")), ("이사회결의일", m.get("board_resolution_date"))]
    od = m.get("outside_directors") or {}
    if od:
        rows.append(("사외이사 참석", f"참석 {od.get('present', '-')} · 불참 {od.get('absent', '-')}"))
    if m.get("audit_attendance"):
        rows.append(("감사 참석", m["audit_attendance"]))
    for n in m.get("notes", []):
        rows.append(("기타 투자판단 참고", n))
    if m.get("related_filings_text"):
        rows.append(("관련공시", m["related_filings_text"]))
    out = ["| 항목 | 원문 |", "|---|---|"]
    out += [f"| {k} | {_cell(v)} |" for k, v in rows if v]
    for p in m.get("later_amendments", []):
        out.append(f"\n> ⚠ {p['version']}({p['rcept_no']}) 정정이 「{_cell(p['item'], 40)}」을 "
                   f"{_cell(p['before'], 40)} → {_cell(p['after'], 40)} 로 고쳤다 — 위 표보다 이 값이 우선이다.")
    return out


def _agenda_lines(a: dict[str, Any]) -> list[str]:
    status = a.get("status")
    if status == "undetermined":
        return ["안건 미정 — 안건표가 `-` 뿐이다. 확정되면 회사가 정정 공시로 다시 낸다."]
    if status != "listed":
        return ["안건표를 찾지 못했다 — 원문 링크로 확인할 것."]
    out = ["| 번호 | 회의목적사항 | 비고 | 표시 |", "|---|---|---|---|"]
    for i in a.get("items", []):
        marks = []
        if i.get("shareholder_proposal"):
            marks.append("주주제안")
        if i.get("cumulative_voting"):
            marks.append("집중투표")
        out.append(f"| {_cell(i.get('no'), 12)} | {_cell(i.get('title'))} | {_cell(i.get('note'))} | {' · '.join(marks)} |")
    return out


def _candidate_lines(tables: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for t in tables:
        cols = t.get("columns") or []
        if not cols or not t.get("rows"):
            continue
        out.append("| " + " | ".join(_cell(c, 20) for c in cols) + " |")
        out.append("|" + "---|" * len(cols))
        for r in t["rows"]:
            out.append("| " + " | ".join(_cell(r.get(c), _CANDIDATE_CELL_MAX) for c in cols) + " |")
        out.append("")
    return out


def _version_lines(d: dict[str, Any]) -> list[str]:
    versions = d.get("versions") or []
    out = ["| 판 | 접수일 | 접수번호 | 정정사유 | 바뀐 칸 |", "|---|---|---|---|---|"]
    for v in versions:
        a = v.get("amendment") or {}
        changed = len(a.get("rows", [])) if a.get("status") == "parsed" else ("-" if not v.get("is_correction") else "표 없음")
        flag = " ⚠원공시 불일치" if v.get("pointer_check") == "mismatch" else ""
        out.append(f"| {v.get('label', '')}{flag} | {v.get('rcept_dt', '')} | `{v.get('rcept_no', '')}` | "
                   f"{_cell(a.get('reason'), 60) if a.get('reason') else ''} | {changed} |")
    details = []
    for v in versions:
        rows = (v.get("amendment") or {}).get("rows") or []
        if not rows:
            continue
        details.append(f"- **{v['label']}** ({v.get('rcept_dt', '')})")
        for r in rows[:_AMEND_ROWS]:
            name = " · ".join(x for x in (r.get("section"), r.get("item")) if x)
            sign = {"added": " (추가)", "deleted": " (삭제)"}.get(r.get("change"), "")
            details.append(f"    - {_cell(name, 40)}: {_cell(r.get('before'), 60)} → {_cell(r.get('after'), 60)}{sign}")
        if len(rows) > _AMEND_ROWS:
            details.append(f"    - 그 밖에 {len(rows) - _AMEND_ROWS}칸 — 전체는 `format=\"json\"` 의 `versions[].amendment.rows`")
    if details:
        out += ["", *details]
    listed = d.get("versions_listed_only") or []
    if listed:
        out.append(f"\n앞 판 {len(listed)}개는 목록만 — " + ", ".join(f"`{x['rcept_no']}`" for x in listed[:6]))
    return out


def render_resolution(payload: dict[str, Any]) -> str:
    status = payload.get("status")
    d = payload.get("data") or {}
    if status in ("error", "no_filing") or not d.get("meeting") and not d.get("agenda"):
        return render_error(payload, "shareholder_meeting_notice(resolution)")
    m = d.get("meeting") or {}
    a = d.get("agenda") or {}
    mtype = _TYPE_LABEL.get(d.get("meeting_type") or "", "주주총회")
    lines = [
        f"# {payload.get('subject', '')} {mtype} 소집결의",
        "",
        "> 이사회가 주총을 열기로 한 단계(거래소 공시). 소집공고(주총 2주 전)보다 먼저 나오고 자주 정정된다. "
        "표는 원문 칸 글 그대로다.",
        "",
    ]
    lines += warning_block(payload)
    src = m.get("source_label")
    lines += [f"## 일시 · 장소{f' — {src} 기준' if src else ''}", "", *_meeting_lines(m), ""]
    n = len(a.get("items") or [])
    asrc = a.get("source_label")
    lines += [f"## 안건{f' ({n}건)' if n else ''}{f' — {asrc} 기준' if asrc else ''}", "", *_agenda_lines(a), ""]
    cands = _candidate_lines(d.get("candidates") or [])
    if cands:
        lines += ["## 이사·감사 후보 (원문 표)", "", *cands]
    if d.get("other_tables"):
        lines += [f"_그 밖의 표 {len(d['other_tables'])}개(정관 변경 세부내역 등)는 `format=\"json\"` 의 `other_tables`._", ""]
    total = d.get("versions_total") or len(d.get("versions") or [])
    lines += [f"## 결의 공시 이력 ({total}판)", "", *_version_lines(d), ""]
    others = d.get("other_meetings") or []
    if others:
        lines.append("## 같은 구간의 다른 주총 결의")
        for o in others[:5]:
            lines.append(f"- {_TYPE_LABEL.get(o.get('meeting_type') or '', '주주총회')} · 첫 결의 {o.get('first_rcept_dt', '')} "
                         f"`{o.get('first_rcept_no', '')}` · {o.get('versions', 1)}판")
        lines.append("")
    for act in payload.get("next_actions") or []:
        lines.append(f"- 다음: {act}")
    return "\n".join(lines).rstrip() + "\n"
