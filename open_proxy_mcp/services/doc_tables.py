"""DART 원문(태그째)의 표를 **칸 그대로** 읽는 공용 도우미.

`get_document_cached()` 의 `html` 을 받는다. 거래소 서식(XForms HTML)과 주요사항보고서 XML 둘 다 —
칸 태그가 넷(`td`·`th`·`te`·`tu`)이고, 세로·가로로 합친 칸(`rowspan`·`colspan`)이 흔하다.
`table_grids()` 는 합친 칸을 **펼쳐** 모든 줄이 같은 칸 수를 갖는 격자로 돌려준다 — 이름 칸 낱말로 값을
찾는 쪽이 서식마다 다른 칸 배치(`1. 일시 | 날짜 | 2026-09-30` · `2. 일시 | 2026-09-09 | 10 : 00`)를
가리지 않게 된다.
"""

from __future__ import annotations

import html as _html
import re

TR_RE = re.compile(r"(?is)<tr\b[^>]*>(.*?)</tr>")
# 🔴 **칸 태그가 넷이다.** 거래소 서식은 `td`, 주요사항보고서 XML 은 머리글이 `th` 로, 값 칸이
# `te`(입력 칸)·`tu`(단위 칸)로 오기도 한다. 하나만 보면 그 서식의 표가 통째로 안 읽힌다.
CELL_RE = re.compile(r"(?is)<(t[dheu])\b([^>]*)>(.*?)</\1\s*>")
TABLE_RE = re.compile(r"(?is)<table\b[^>]*>(.*?)</table>")
_BR = re.compile(r"(?is)<br\b[^>]*>")
_TAG = re.compile(r"<[^>]+>")
_SPAN = {"rowspan": re.compile(r"(?i)\browspan\s*=\s*[\"']?(\d+)"),
         "colspan": re.compile(r"(?i)\bcolspan\s*=\s*[\"']?(\d+)")}


def cell_text(frag: str) -> str:
    """표 한 칸 → 글. **`<br>` 은 줄바꿈으로 살린다** — 한 칸에 여러 값을 눌러 담는 회사가 있다.

    실체를 먼저 풀고 공백을 접는다. 거꾸로 하면 `&nbsp;` 가 접힌 **뒤에** `\\xa0` 로 풀려
    칸 앞머리에 남는다(Agent D 2026-08-28 — 앞에서부터 맞추는 검사가 통째로 어긋났다).
    """
    text = _html.unescape(_TAG.sub(" ", _BR.sub("\n", frag or "")))
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def compact(s: str) -> str:
    """견줄 때만 쓴다 — 머리글이 `정 정 전`·`회 의 목 적` 처럼 글자 사이가 벌어져 온다."""
    return re.sub(r"\s+", "", s or "")


def span(attrs: str, name: str) -> int:
    m = _SPAN[name].search(attrs or "")
    return max(1, int(m.group(1))) if m else 1


def table_grids(html: str) -> list[list[list[str]]]:
    """문서의 표마다 격자 하나. 합친 칸은 펼친 자리마다 같은 글을 되풀이한다.

    줄마다 칸 수가 다를 수 있다(표 끝의 한 칸짜리 안내 줄 등) — 격자는 직사각형을 강요하지 않는다.
    """
    grids: list[list[list[str]]] = []
    for body in TABLE_RE.findall(html or ""):
        grid: list[list[str]] = []
        pending: dict[int, list] = {}          # 열 → [글, 남은 줄 수] (위에서 내려온 rowspan)
        for tr in TR_RE.findall(body):
            row: list[str] = []
            col = 0
            for m in CELL_RE.finditer(tr):
                while col in pending:
                    text, left = pending[col]
                    row.append(text)
                    pending[col][1] -= 1
                    if pending[col][1] <= 0:
                        del pending[col]
                    col += 1
                attrs, text = m.group(2), cell_text(m.group(3))
                rs, cs = span(attrs, "rowspan"), span(attrs, "colspan")
                for _ in range(cs):
                    row.append(text)
                    if rs > 1:
                        pending[col] = [text, rs - 1]
                    col += 1
            while col in pending:                  # 줄 끝까지 내려온 칸
                text, _left = pending[col]
                row.append(text)
                pending[col][1] -= 1
                if pending[col][1] <= 0:
                    del pending[col]
                col += 1
            if row:
                grid.append(row)
        if grid:
            grids.append(grid)
    return grids


def distinct(row: list[str]) -> list[str]:
    """가로로 합친 칸이 되풀이된 것을 접는다 — `[장소, 장소, 주소, 주소]` → `[장소, 주소]`."""
    out: list[str] = []
    for c in row:
        if not out or out[-1] != c:
            out.append(c)
    return out
