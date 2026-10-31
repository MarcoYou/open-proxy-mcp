"""Board attendance observations with section and period provenance.

These are disclosed period observations, not reconstructed prior-term rates.
Never average percentages from different periods without their denominators.
"""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from open_proxy_mcp.services.doc_tables import table_grids

_NAME_RATE = re.compile(
    r"(?<![가-힣])([가-힣]{2,5})\s*\(\s*(?:출석률\s*[:：]?\s*)?"
    r"(\d+(?:\.\d+)?)\s*%\s*\)"
)
_PERIOD = re.compile(
    r"기간\s*[:：]\s*(\d{4}\.\s*\d{1,2}\.\s*\d{1,2})\s*"
    r"[~∼～–-]\s*(\d{4}\.\s*\d{1,2}\.\s*\d{1,2})"
)


# ── 표 모양 넓히기 (2026-11) ──────────────────────────────────────────────
# `이름(NN%)` 한 꼴만 읽어 태광산업·삼성전기·한화솔루션·고려아연 사업보고서가 원문에 이사별 출석률이
# 뻔히 있는데도 format_unsupported 였다. 표 칸 단위로 두 꼴을 더 읽는다 — 둘 다 **한 칸 안**이거나
# **같은 열**로 이름과 값이 묶일 때만이다. 글을 줄 단위로 이어 붙여 짝짓지 않는다: 이름 줄 셋 뒤에 값
# 줄 셋이 오는 표를 글로 읽으면 둘째 이름에 첫째 값이 붙는다.
#   ① 한 칸: 「정평영 58.8%」·「정안식\n(사외이사)\n출석률: 100%」
#   ② 열 맞춤: 이름 줄 바로 아래 값 줄 — `정안식 | 정평영` / `100% | 58.8%`
_CELL_NAME_RATE = re.compile(
    r"([가-힣]{2,4})\s*(?:\([^()]{1,12}\)\s*)?\(?\s*(?:출\s*석\s*률\s*[:：]?\s*)?"
    r"(\d{1,3}(?:\.\d+)?)\s*%\s*\)?")
_RATE_CELL = re.compile(r"\(?\s*(?:출\s*석\s*률\s*[:：]?\s*)?(\d{1,3}(?:\.\d+)?)\s*%\s*\)?")
_NAME_CELL = re.compile(r"([가-힣]{2,4})(?:\s*\([^()]{1,12}\))?")
_NOT_NAMES = frozenset("""성명 이름 구분 출석률 참석률 사외이사 사내이사 독립이사 기타비상무이사 감사위원
    이사 감사 회차 개최일자 개최일 의안내용 의안 가결여부 찬반여부 찬성 반대 기권 불참 비고 합계 평균 전체
    소계 직위 직책 성명출석률 이사회 위원회 위원장 의장 대표이사 가결 부결 해당없음""".split())


def _cell_name(cell: str) -> str | None:
    first = (cell or "").split("\n")[0].strip()
    m = _NAME_CELL.fullmatch(first)
    if not m or m.group(1) in _NOT_NAMES:
        return None
    return m.group(1)


def _locate(text: str, quote: str, start: int) -> tuple[int, int] | None:
    """칸 글을 절 글에서 찾는다 — 칸 안 줄바꿈·공백 차이는 너그럽게."""
    tokens = quote.split()
    if not tokens:
        return None
    m = re.compile(r"\s*".join(re.escape(t) for t in tokens)).search(text, start)
    return (m.start(), m.end()) if m else None


def _table_observations(section, text: str, end: int) -> tuple[list[dict[str, Any]], list[tuple[int, int]]]:
    """이사회 절의 표에서 ①·② 꼴 → (관측, 표가 차지한 글 구간). 위원회 표제(`end`) 뒤 표는 보지 않는다."""
    observations: list[dict[str, Any]] = []
    covered: list[tuple[int, int]] = []
    cursor = 0
    for table in section.find_all(re.compile(r"^table$", re.I)):
        ttext = table.get_text("\n", strip=True)
        loc = _locate(text, ttext[:200], cursor) if ttext else None
        if loc is None:
            continue
        t_start = loc[0]
        if t_start >= end:
            break
        t_end = (_locate(text, ttext[-200:], t_start) or loc)[1]
        cursor = t_end
        context = text[max(0, t_start - 1500):t_end]
        if "출석률" not in context.replace(" ", ""):
            continue
        grids = table_grids(str(table))
        if not grids:
            continue
        grid = grids[0]
        found: list[dict[str, Any]] = []
        # ② 열 맞춤 — 이름 줄 바로 아래 값 줄. 값이 있는 열과 이름이 있는 열이 **정확히** 같아야 한다.
        for i in range(len(grid) - 1):
            names = {c: _cell_name(v) for c, v in enumerate(grid[i])}
            rates = {c: _RATE_CELL.fullmatch((v or "").strip()) for c, v in enumerate(grid[i + 1])}
            name_cols = {c for c, n in names.items() if n}
            rate_cols = {c for c, r in rates.items() if r}
            if not rate_cols or name_cols != rate_cols:
                continue
            seen: set[tuple[str, str]] = set()
            for c in sorted(rate_cols):
                key = (names[c], rates[c].group(1))
                if key in seen:          # 가로로 합친 칸이 펼쳐져 되풀이된 것
                    continue
                seen.add(key)
                found.append({"name": names[c], "value": rates[c].group(1),
                              "quote": f"{names[c]} | {grid[i + 1][c].strip()}",
                              "cell": {"name_row": i, "rate_row": i + 1, "column": c}})
        # ① 한 칸 — 칸 글 전체가 이름(+직위)+값이어야 한다.
        if not found:
            seen_cells: set[str] = set()
            for r, row in enumerate(grid):
                for c, v in enumerate(row):
                    flat = " ".join((v or "").split())
                    if flat in seen_cells:
                        continue
                    m = _CELL_NAME_RATE.fullmatch(flat)
                    if not m or m.group(1) in _NOT_NAMES:
                        continue
                    seen_cells.add(flat)
                    found.append({"name": m.group(1), "value": m.group(2), "quote": flat,
                                  "cell": {"row": r, "column": c}})
        if not found:
            continue
        covered.append((t_start, t_end))
        for f in found:
            value = float(f["value"])
            if not 0 <= value <= 100:
                continue
            span = _locate(text, f["quote"], t_start) if "cell" in f and "row" in f["cell"] else None
            observations.append({
                "name": f["name"], "attendance_pct": value,
                "quote": text[span[0]:span[1]] if span else f["quote"],
                "span": ({"start": span[0], "end": span[1], "basis": "section_text"} if span
                         else {"start": t_start, "end": t_end, "basis": "table_columns", **f["cell"]}),
                "_pos": t_start,
            })
    return observations, covered


def parse_board_attendance_observations(html: str) -> dict[str, Any]:
    """Read rate headers only inside the board section before committees.

    Preserve the section text so a client can verify every character span or
    read tables unsupported by this narrow extractor. Missing anchors mean an
    extraction limitation, not evidence that the company omitted disclosure.
    """
    soup = BeautifulSoup(html, "xml")
    title = next((t for t in soup.find_all(re.compile(r"^title$", re.I))
                  if re.fullmatch(r"\s*(?:\d+\s*\.\s*)?이사회에\s*관한\s*사항\s*",
                                  t.get_text())), None)
    if title is None:
        return {"status": "section_not_located", "observations": [],
                "reason": "이사회 절 경계를 확인하지 못했습니다. 원문에 자료가 없다는 뜻은 아닙니다."}
    section = title.find_parent(re.compile(r"^section-\d+$", re.I))
    if section is None:
        return {"status": "section_not_located", "observations": []}
    text = section.get_text("\n", strip=True)
    # Only standalone subsection headings delimit committees; agenda rows may
    # legitimately contain committee names while still describing board votes.
    end = len(text)
    for match in re.finditer(r"(?m)^\s*(?:[가-힣]\.|\d+\.|\(\d+\))\s*"
                             r"이사회\s*내(?:의)?\s*위원회[^\n]*$", text):
        end = match.start()
        break
    board = text[:end]
    periods = list(_PERIOD.finditer(board))
    observations = []
    table_obs, covered = _table_observations(section, text, end)
    for match in _NAME_RATE.finditer(board):
        # 표 모양으로 읽은 표 안의 글은 그 표의 짝짓기를 따른다(글 순서로 짝지으면 열이 어긋난다).
        if any(s <= match.start() < e for s, e in covered):
            continue
        value = float(match.group(2))
        if not 0 <= value <= 100:
            continue
        # Bare Name(100%) is meaningful only under a disclosed attendance header.
        before = board[max(0, match.start() - 1500):match.start()]
        if "출석률" not in match.group(0) and "출석률" not in before:
            continue
        period = next((p for p in reversed(periods) if p.end() <= match.start()), None)
        observations.append({
            "name": match.group(1), "attendance_pct": value,
            "period_raw": period.group(0) if period else None,
            "period_start": re.sub(r"\s", "", period.group(1)) if period else None,
            "period_end": re.sub(r"\s", "", period.group(2)) if period else None,
            "quote": match.group(0),
            "span": {"start": match.start(), "end": match.end(), "basis": "section_text"},
            "scope": "disclosed_period", "prior_term_complete": False,
        })
    for obs in table_obs:
        pos = obs.pop("_pos")
        period = next((p for p in reversed(periods) if p.end() <= pos), None)
        observations.append({
            "name": obs["name"], "attendance_pct": obs["attendance_pct"],
            "period_raw": period.group(0) if period else None,
            "period_start": re.sub(r"\s", "", period.group(1)) if period else None,
            "period_end": re.sub(r"\s", "", period.group(2)) if period else None,
            "quote": obs["quote"], "span": obs["span"],
            "scope": "disclosed_period", "prior_term_complete": False,
            "format": "table",
        })
    observations.sort(key=lambda o: o["span"]["start"])
    return {
        "status": "parsed" if observations else "format_unsupported",
        "section_title": title.get_text(strip=True),
        "section_text": text,
        "observations": observations,
        "reason": None if observations else "이사회 원문은 확보했으나 출석률 요약 형식을 읽지 못했습니다.",
    }


def summarize_attendance_observations(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group by disclosed name, retaining every period; do not infer identity."""
    names = sorted({o["name"] for o in observations})
    result = []
    for name in names:
        rows = [o for o in observations if o["name"] == name]
        values = {r["attendance_pct"] for r in rows}
        value = next(iter(values)) if len(values) == 1 else None
        result.append({"name": name, "attendance_pct": value,
                       "low": value < 75 if value is not None else None,
                       "period_observations": rows,
                       "aggregation": "same_disclosed_rates" if value is not None else "requires_denominators",
                       "prior_term_complete": False})
    return result
