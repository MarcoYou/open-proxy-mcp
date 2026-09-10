"""Board attendance observations with section and period provenance.

These are disclosed period observations, not reconstructed prior-term rates.
Never average percentages from different periods without their denominators.
"""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

_NAME_RATE = re.compile(
    r"(?<![가-힣])([가-힣]{2,5})\s*\(\s*(?:출석률\s*[:：]?\s*)?"
    r"(\d+(?:\.\d+)?)\s*%\s*\)"
)
_PERIOD = re.compile(
    r"기간\s*[:：]\s*(\d{4}\.\s*\d{1,2}\.\s*\d{1,2})\s*"
    r"[~∼～–-]\s*(\d{4}\.\s*\d{1,2}\.\s*\d{1,2})"
)


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
    for match in _NAME_RATE.finditer(board):
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
