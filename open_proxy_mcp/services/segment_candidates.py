"""segment_profit 후보표 좁히기 — 정형 파서가 실패/저신뢰일 때 tool이 반환하는 raw fallback.

설계(260718 사용자 결정): 내부 LLM 호출 폐기. MCP tool은 이미 LLM(호출측 Claude)이 부르므로,
tool은 **기계적으로 수백 개 중첩표를 '진짜 부문표 후보' ~3-5개로 좁혀 raw로 반환**하고,
값 추출·표 선택은 호출측 Claude가 한다(156사 에이전트 추출로 이 방식 검증됨). anthropic/pandas/API키 불필요.
"""
from __future__ import annotations

import re

_METRIC = ("매출액", "영업수익", "영업이익", "영업손익", "총부문수익", "부문수익", "매출총이익",
           "당기순이익", "수익(매출액)", "부문이익")
_SEG_SIGNAL = ("영업부문", "사업부문", "부문별", "보고부문")
# 부문표가 아닌데 지표라벨을 가진 오답 표 힌트(관계·종속·지분율·유형자산 증감·내용연수)
_ANTI = ("관계기업", "종속기업", "지분율", "내용연수", "취득원가", "기초", "감가상각누계")


def _table_to_grid(tb) -> list[list[str]]:
    """bs4 <table> → colspan/rowspan 확장한 2D 텍스트 격자 (pandas 없이, venv-lean)."""
    grid: list[list[str]] = []
    rowspans: dict[int, tuple[int, str]] = {}
    for tr in tb.find_all("tr"):
        row: list[str] = []
        ci = 0
        cells = tr.find_all(["td", "th"])
        cidx = 0
        while cidx < len(cells) or (rowspans and ci in rowspans):
            if ci in rowspans:
                rem, txt = rowspans[ci]
                row.append(txt)
                rowspans[ci] = (rem - 1, txt)
                if rem - 1 <= 0:
                    del rowspans[ci]
                ci += 1
                continue
            if cidx >= len(cells):
                break
            td = cells[cidx]; cidx += 1
            txt = td.get_text(" ", strip=True)
            cs = int(td.get("colspan", 1) or 1)
            rs = int(td.get("rowspan", 1) or 1)
            for _ in range(cs):
                row.append(txt)
                if rs > 1:
                    rowspans[ci] = (rs - 1, txt)
                ci += 1
        if any(c.strip() for c in row):
            grid.append(row)
    return grid


def _score(txt: str) -> int:
    s = 0
    s += sum(1 for m in _METRIC if m in txt)
    s += 2 * sum(1 for k in _SEG_SIGNAL if k in txt)
    s -= 3 * sum(1 for a in _ANTI if a in txt)
    s += min(len(re.findall(r"[\d,]{5,}", txt)) // 3, 4)   # 큰 숫자 다수 가점(상한)
    return s


def find_segment_candidates(html: str, max_tables: int = 5, max_rows: int = 40) -> list[dict]:
    """부문표 후보를 점수순 상위 N개 반환. 각 {rendered(파이프격자 텍스트), score, rows, cols}.

    호출측 LLM이 이 중 진짜 부문표를 골라 값을 읽는다. 수백 표 → ~5개로 기계적 narrow.
    """
    if not html:
        return []
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return []
    soup = BeautifulSoup(html, "lxml")
    scored = []
    for tb in soup.find_all("table"):
        txt = tb.get_text(" ", strip=True)
        if not any(m in txt for m in _METRIC):
            continue
        if not re.search(r"[\d,]{5,}", txt):
            continue
        if not any(k in txt for k in _SEG_SIGNAL):
            continue
        sc = _score(txt)
        if sc <= 0:
            continue
        grid = _table_to_grid(tb)
        if not grid or len(grid) > max_rows or max((len(r) for r in grid), default=0) > 40:
            continue
        rendered = "\n".join(" | ".join(c[:24] for c in r) for r in grid)
        scored.append({"rendered": rendered[:5000], "score": sc,
                       "rows": len(grid), "cols": max(len(r) for r in grid)})
    scored.sort(key=lambda x: x["score"], reverse=True)
    # 중복 제거(동일 rendered 앞부분)
    seen, out = set(), []
    for c in scored:
        k = c["rendered"][:120]
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
        if len(out) >= max_tables:
            break
    return out
