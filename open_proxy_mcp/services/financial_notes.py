# -*- coding: utf-8 -*-
"""financial_notes — 금융사 재무제표 주석 표를 **원형 그대로** 뽑는다.

설계·근거: wiki/decisions/260823_1720_decision_financial-notes-tool.md
census 21사 41건(반기 21·사업 20) 실측 위에 세웠다.

무엇을 뽑나 (크레딧 애널리스트 요청)
  · 사용제한 — 사용이 제한된 예금·예치금, 담보제공자산 → unencumbered cash 산출용
  · FVPL / FVOCI / 상각후원가 — 투자자산 유형별 구성 → 유형별 헤어컷 적용용

왜 이렇게 짰나 (실측 근거)
  🔴 **형식이 회사가 아니라 표 단위로 갈린다.** 사용제한 기준 은행 표구조 10:2 ·
     보험 태그 9:3(정반대) · 증권 8:6. 같은 회사 안에서도 표마다 다르다
     (국민은행 FVPL=태그, FVOCI=표구조). → **업권으로 분기하면 절반이 틀린다.**
     판별은 그 `<TABLE>` 안에 `<TE ACODE>` 가 있나로 **런타임에** 한다.
  🔴 **기준 시점을 안 붙이면 그대로 틀린 분석이 된다.** KB손보 사용제한 합계가
     전기말 391,082 → 당반기말 26,356(1/15). 열 이름을 그대로 보존하는 이유다.
  🔴 **단위가 회사마다 다르다** — 백만원/천원/**원**(미래에셋생명).
  🔴 **띄어쓰기가 흔들린다** — 같은 KB손보 안에서 「사용제한 내용」·「사용제한내용」.

무엇을 하지 않나
  임의로 합치거나 나누지 않는다. unencumbered cash 계산·헤어컷 적용은 이 tool 밖이다.
  표를 있는 그대로 내는 데까지가 계약이다.
"""
from __future__ import annotations

import re
from typing import Any

# ── 결측 3분류 (business_details 규약을 따른다) ──
NOT_APPLICABLE = "NOT_APPLICABLE"        # 그 주석이 없는 회사 — 정상
NOT_COLLECTED = "NOT_COLLECTED"          # 문서를 못 받음(첨부정정 014 등)
EXTRACTION_FAILED = "EXTRACTION_FAILED"  # 표는 찾았는데 못 읽음 — 버그/엣지
OK = "OK"

_TABLE_OPEN = re.compile(r"<TABLE", re.I)
_TE = re.compile(r"<TE\s[^>]*ACODE=", re.I)
_ROW = re.compile(r"<TR[^>]*>(.*?)</TR>", re.S | re.I)
_CELL = re.compile(r"<T[DHE]([^>]*)>(.*?)</T[DHE]>", re.S | re.I)
_ACODE = re.compile(r'ACODE="([^"]*)"', re.I)
_ACTX = re.compile(r'ACONTEXT="([^"]*)"', re.I)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

#: 재무제표 주석이 사는 장. 앞부분(사업의 내용·주주 현황)을 훑을 이유가 없다.
#: 32MB 문자열을 통째로 정규식에 넣지 않으려는 것이 목적이다.
_NOTES_SECTION = ("III. 재무에 관한 사항", "Ⅲ. 재무에 관한 사항", "재무제표 주석")

#: 앵커 — census 41건 실측 적중順. 회사마다 제목이 다르므로 후보를 여럿 둔다.
#: 「사용이 제한된」 32 · 「사용이 제한되어」 4 · 「사용제한 예치금」 2 · 못찾음 3
#: 🔴 못 찾은 3건(현대해상·미래에셋생명)은 **「담보제공자산」**으로 공시한다.
#:    「사용이 제한」이 문서에 0회다. 같은 경제적 실질이지만 회계상 다르므로
#:    **`kind` 로 구분해 내보내고 합치지 않는다** — 산식에 넣을지는 애널리스트가 정한다.
ANCHORS: dict[str, list[tuple[str, str]]] = {
    "사용제한": [
        ("사용이 제한된", "restricted"), ("사용이 제한되어", "restricted"),
        ("사용제한 금융자산", "restricted"), ("사용제한 예치금", "restricted"),
        ("사용제한", "restricted"),
        ("담보제공자산", "pledged"), ("담보로 제공된 금융자산", "pledged"),
    ],
    "FVPL": [("당기손익-공정가치측정금융자산", "fvpl"), ("당기손익인식금융자산", "fvpl"),
             ("당기손익-공정가치 측정 금융자산", "fvpl")],
    "FVOCI": [("기타포괄손익-공정가치측정금융자산", "fvoci"),
              ("기타포괄손익-공정가치 측정 금융자산", "fvoci"), ("기타포괄손익-공정가치", "fvoci")],
    "상각후원가": [("상각후원가측정유가증권", "amortized"), ("상각후원가측정금융자산", "amortized"),
                ("상각후원가측정 대출채권", "amortized"), ("상각후원가", "amortized")],
}

FIELDS = tuple(ANCHORS)

#: 표 하나를 담을 창. 앵커 위치에서 이만큼만 잘라 표 경계를 찾는다.
_WINDOW = 60_000
#: 표가 이보다 크면 앵커가 엉뚱한 바깥 표를 잡은 것으로 본다(레이아웃용 중첩 표).
_MAX_TABLE = 400_000


def notes_offset(html: str) -> int:
    """재무제표 주석 장이 시작하는 위치. 못 찾으면 0(문서 전체)."""
    for mark in _NOTES_SECTION:
        i = html.find(mark)
        if i > 0:
            return i
    return 0


def _clean(s: str) -> str:
    return _WS.sub(" ", _TAG.sub(" ", s)).replace("&nbsp;", " ").strip()


def table_at(html: str, pos: int) -> str | None:
    """`pos` 를 품은 `<TABLE>` 를 잘라낸다. 경계가 비정상이면 None."""
    upper = html.upper()
    lo = max(0, pos - _WINDOW)
    # 제목/설명 문장에 앵커가 있고 표가 바로 뒤에 오는 형식이 흔하다.
    # 먼저 앵커를 포함하는 가장 가까운 앞 표를 찾고, 그 표가 이미 닫혔으면
    # 앵커 뒤의 첫 표를 후보로 삼는다. 어느 쪽도 임의의 표를 건너뛰어 선택하지 않는다.
    s = upper.rfind("<TABLE", lo, pos + 1)
    if s >= 0:
        e = upper.find("</TABLE>", s)
        if e >= pos and e - s <= _MAX_TABLE:
            return html[s:e + 8]
    s = upper.find("<TABLE", pos, pos + _WINDOW)
    if s < 0:
        return None
    e = upper.find("</TABLE>", s)
    if e < 0 or e - s > _MAX_TABLE:
        return None
    return html[s:e + 8]


def parse_table(tbl: str) -> dict[str, Any]:
    """표 하나 → 행렬 + 형식 판정. **셀을 합치거나 나누지 않는다.**

    태그 경로면 값마다 `acode`/`acontext` 를 함께 실어 보낸다 — 연결/별도와 분류축이
    거기 들어 있어 사람이 열 위치로 짐작할 필요가 없다.
    """
    tagged = bool(_TE.search(tbl))
    rows: list[list[dict[str, Any]]] = []
    for raw in _ROW.findall(tbl):
        cells = []
        for attrs, body in _CELL.findall(raw):
            cell: dict[str, Any] = {"text": _clean(body)}
            if tagged:
                m = _ACODE.search(attrs)
                if m:
                    cell["acode"] = m.group(1)
                m2 = _ACTX.search(attrs)
                if m2:
                    ctx = m2.group(1)
                    cell["acontext"] = ctx
                    # 연결/별도는 문맥에 박혀 있다 — 열 위치로 짐작하지 않는다
                    if "ConsolidatedMember" in ctx:
                        cell["basis"] = "연결"
                    elif "SeparateMember" in ctx:
                        cell["basis"] = "별도"
            cells.append(cell)
        if cells:
            rows.append(cells)
    header = next(([c["text"] for c in r] for r in rows if len(r) >= 2 and any(c["text"] for c in r)), [])
    n_num = sum(1 for r in rows for c in r if _NUMISH.match(c["text"]))
    return {
        "format": "xbrl_tagged" if tagged else "html_table",
        "rows": rows,
        "n_rows": len(rows),
        "n_numeric": n_num,
        "header": header,
        "unit": find_unit(tbl),
    }


#: 숫자칸 판별 — 「값이 있는 표」와 캡션·여백 표를 가른다. 음수 △·괄호 표기를 포함한다.
_NUMISH = re.compile(r"^\(?\s*[△▲-]?\s*[\d,]+\s*\)?$")
_UNIT = re.compile(r"단위\s*[:：]?\s*([가-힣]+원)")


def find_unit(tbl: str) -> str | None:
    """(단위: 백만원) — 회사마다 다르다. 원문 표기 그대로 돌려준다."""
    m = _UNIT.search(_clean(tbl))
    return m.group(1) if m else None


def extract(html: str, fields: list[str] | None = None) -> dict[str, Any]:
    """문서 HTML → 요청한 표들. 문서는 호출자가 넘긴다(캐시 재사용)."""
    want = [f for f in (fields or FIELDS) if f in ANCHORS]
    off = notes_offset(html)
    out: dict[str, Any] = {}
    for field in want:
        found = []
        for kw, kind in ANCHORS[field]:
            start = off
            while True:
                p = html.find(kw, start)
                if p < 0:
                    break
                tbl = table_at(html, p)
                start = p + len(kw)
                if not tbl:
                    continue
                parsed = parse_table(tbl)
                # 260823 실측: NH 「사용이 제한된」은 캡션 표(2행·숫자 0칸)를 먼저 물었다.
                # 행 수만 보면 캡션이 통과한다 — **값이 든 표인가**를 함께 묻는다.
                if parsed["n_rows"] < 2 or parsed["n_numeric"] < 2:
                    # 목차/캡션용 표가 앵커와 먼저 만나는 문서가 있다.
                    # 같은 앵커 뒤의 다음 표가 실제 값 표인지 한 번만 확인한다.
                    next_open = html.upper().find("<TABLE", html.upper().find("</TABLE>", p) + 8)
                    if next_open >= 0 and next_open - p <= _WINDOW:
                        next_tbl = table_at(html, next_open)
                        if next_tbl:
                            next_parsed = parse_table(next_tbl)
                            if next_parsed["n_rows"] >= 2 and next_parsed["n_numeric"] >= 2:
                                tbl, parsed = next_tbl, next_parsed
                            else:
                                continue
                        else:
                            continue
                    else:
                        continue
                found.append({"anchor": kw, "kind": kind, "pos": p, **parsed})
                break                          # 앵커 하나당 첫 표만
            if found:
                break                          # 앵커 후보는 순서대로, 걸리면 멈춘다
        out[field] = ({"status": OK, "tables": found} if found
                      else {"status": NOT_APPLICABLE, "tables": [],
                            "note": "이 문서에서 해당 주석을 찾지 못했다 — 회사가 다른 이름으로 "
                                    "공시하거나 그 주석이 없다"})
    return out
