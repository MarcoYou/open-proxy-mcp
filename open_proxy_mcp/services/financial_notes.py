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
_COLSPAN = re.compile(r'COLSPAN\s*=\s*"?(\d+)"?', re.I)
_ROWSPAN = re.compile(r'ROWSPAN\s*=\s*"?(\d+)"?', re.I)
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


#: 문서 맨 앞의 **목차**가 차지하는 범위(문자 수). 목차에도 장 제목이 그대로 있어서
#: 그냥 첫 출현을 잡으면 문서 맨 앞으로 떨어진다.
_TOC_HEAD = 20_000


def notes_offset(html: str) -> int:
    """재무제표 주석 장이 시작하는 위치. 못 찾으면 0(문서 전체).

    🔴 **목차를 걷어내지 않으면 「사업의 내용」이 먼저 걸린다.** 260823 실측 —
    NH투자증권 반기보고서는 「III. 재무에 관한 사항」이 목차(8,574)와 본문(930,615)에
    각각 있고, 목차를 잡는 바람에 FVPL·FVOCI·상각후원가 세 필드가 전부
    **「II. 사업의 내용」의 자산 평균잔액·이자율 표**(317,046 부근·단위 억원)를 물었다.
    주석의 유형별 구성이 아니라 운용수익률 표라 헤어컷을 매길 수 없다.
    → 앞머리 목차 안의 출현은 버리고, 그 뒤 첫 출현을 쓴다.
    """
    floor = 0
    for mark in _NOTES_SECTION:
        i = html.rfind(mark, 0, _TOC_HEAD)
        if i >= 0:
            floor = max(floor, i + len(mark))
    for mark in _NOTES_SECTION:
        i = html.find(mark, floor)
        if i > 0:
            return i
    return 0


def _clean(s: str) -> str:
    return _WS.sub(" ", _TAG.sub(" ", s)).replace("&nbsp;", " ").strip()


def table_span(html: str, pos: int) -> tuple[int, int] | None:
    """`pos` 를 품은 `<TABLE>` 의 (시작, 끝) 위치. 경계가 비정상이면 None.

    시작 위치를 돌려주는 이유 — **단위 표기는 표 안이 아니라 표 바로 앞 문단에 있다.**
    260823 실측: KB손보 문서에 「단위: 백만원」이 259회 나오는데 `<TABLE>` 안에서 찾으면
    0건이다. 호출자가 표 앞을 읽으려면 시작 위치가 필요하다.
    """
    upper = html.upper()
    lo = max(0, pos - _WINDOW)
    # 제목/설명 문장에 앵커가 있고 표가 바로 뒤에 오는 형식이 흔하다.
    # 먼저 앵커를 포함하는 가장 가까운 앞 표를 찾고, 그 표가 이미 닫혔으면
    # 앵커 뒤의 첫 표를 후보로 삼는다. 어느 쪽도 임의의 표를 건너뛰어 선택하지 않는다.
    s = upper.rfind("<TABLE", lo, pos + 1)
    if s >= 0:
        e = upper.find("</TABLE>", s)
        if e >= pos and e - s <= _MAX_TABLE:
            return s, e + 8
    s = upper.find("<TABLE", pos, pos + _WINDOW)
    if s < 0:
        return None
    e = upper.find("</TABLE>", s)
    if e < 0 or e - s > _MAX_TABLE:
        return None
    return s, e + 8


def table_at(html: str, pos: int) -> str | None:
    """`pos` 를 품은 `<TABLE>` 를 잘라낸다. 경계가 비정상이면 None."""
    span = table_span(html, pos)
    return html[span[0]:span[1]] if span else None


def parse_table(tbl: str, context: str = "") -> dict[str, Any]:
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
            # 260823 T보고: 「열 이름 10개 · 값 9개」로 값이 한 칸씩 밀려 읽혔다.
            # 원인은 병합 셀이고, 병합 폭을 버리면 읽는 쪽이 복원할 수 없다. 실어 보낸다.
            mc = _COLSPAN.search(attrs)
            if mc and mc.group(1) != "1":
                cell["colspan"] = int(mc.group(1))
            mr = _ROWSPAN.search(attrs)
            if mr and mr.group(1) != "1":
                cell["rowspan"] = int(mr.group(1))
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
    # 병합 폭까지 세어 본 「논리 열 수」. 행마다 다르면 그 표는 그대로 읽으면 어긋난다.
    widths = [sum(c.get("colspan", 1) for c in r) for r in rows] or [0]
    return {
        "format": "xbrl_tagged" if tagged else "html_table",
        "rows": rows,
        "n_rows": len(rows),
        "n_numeric": n_num,
        "header": header,
        "unit": find_unit(tbl, context),
        # 표 바로 앞 문장. 「(1) 요약연결재무상태표」처럼 **이 표가 무엇인지**가 여기 있다.
        "caption": _clean(context)[-200:],
        "widths": widths,
        # 🔴 행마다 열 수가 다르다 = 값이 밀려 읽힐 수 있다. 읽는 쪽에 반드시 알린다.
        "ragged": len(set(widths)) > 1,
    }


#: 숫자칸 판별 — 「값이 있는 표」와 캡션·여백 표를 가른다. 음수 △·괄호 표기를 포함한다.
_NUMISH = re.compile(r"^\(?\s*[△▲-]?\s*[\d,]+\s*\)?$")
_UNIT = re.compile(r"단위\s*[:：]?\s*([가-힣]+원)")


def find_unit(tbl: str, context: str = "") -> str | None:
    """(단위: 백만원) — 회사마다 다르다. 원문 표기 그대로 돌려준다.

    🔴 **표 안에만 있는 것이 아니다.** 260823 실측 — KB손보·NH투자증권 반기보고서는
    「(단위: 백만원)」을 `<TABLE>` 바깥 바로 앞 문단에 둔다. 표 안만 뒤지면 둘 다 None 이
    되고, 그러면 26,356 이 263억인지 26억인지 알 수 없어 **숫자를 못 쓴다.**
    표 안을 먼저 보고, 없으면 `context`(표 직전 텍스트)에서 **가장 가까운** 표기를 쓴다.
    """
    m = _UNIT.search(_clean(tbl))
    if m:
        return m.group(1)
    if context:
        found = _UNIT.findall(_clean(context))
        if found:
            return found[-1]        # 표에 가장 가까운 것 = 마지막 것
    return None


#: 재무상태표·손익계산서 **본표**를 가려내는 표지. 이 단어들은 주석 표에는 안 나온다.
#: 🔴 260823 T보고: FVPL/FVOCI/상각후원가 세 필드가 **똑같은 표**를 돌려줬다. 원인은
#:    세 앵커가 전부 「계정과목 이름」이라 주석보다 앞에 있는 **연결재무상태표**에서 먼저
#:    걸린 것이다(KB 227,827·228,606·229,375 — 같은 52행 표). 유형별 구성이 아니라
#:    자산·부채 전문이 나오니 헤어컷을 매길 수 없다. 본표는 건너뛰고 계속 찾는다.
_STATEMENT_MARKS = ("자산총계", "부채총계", "자본총계", "부채와자본총계", "부채및자본총계")
#: 🔴 총계 행이 없는 본표도 있다. 260823 실측 — NH 「요약연결재무상태표」(36행)에는
#:    「자산총계」가 한 번도 안 나와 행 검사만으로는 안 걸렸고, 세 필드가 또 같은 표를 물었다.
#:    표가 무엇인지는 **표 바로 앞 문장**에 적혀 있다. 그쪽을 같이 본다.
_STATEMENT_CAPTIONS = ("요약재무정보", "요약연결재무정보", "재무상태표", "손익계산서",
                       "자본변동표", "현금흐름표")
#: 문서 앞머리의 목차. 「III. 재무에 관한 사항」이 목차에도 있어서 notes_offset 이
#: 8,574(NH) 같은 문서 맨 앞을 가리키는 일이 있다 — 그래서 본표 차단이 더 필요하다.


#: 표 제목에 붙는 말. 「무엇의 **내역**」·「무엇의 **공시**」가 그 표가 무엇인지 말한다.
#: 🔴 260823 실측 — 앵커 첫 출현은 정답이 아니다. 「당기손익-공정가치측정금융자산」은
#:    주석 안에서 **공정가치 수준별 내역·금리위험 익스포져·비연결구조화기업 위험** 표에도
#:    똑같이 나온다. KB손보 반기는 공정가치수준별(514,629)을 물었고 정답은 21행짜리
#:    「7. 당기손익-공정가치측정금융자산 … 내역」(562,267)이 47,000자 뒤에 있었다.
#:    NH 사업보고서도 「비연결구조화기업 위험」(1,393,631)을 물고 정답(1,449,639)을 지나쳤다.
#:    → **표 앞 문장에 「<앵커>…내역/공시」가 있는 표**를 고른다. 원문이 스스로 붙인 제목이라
#:    금지어 목록을 손으로 관리하는 것보다 안전하다.
_TITLE_MARKS = ("구성내역", "세부내역", "내역", "공시")
#: 앵커와 표지어 사이에 끼는 말의 길이. 「담보제공자산 보고기간말 현재 담보제공된 자산의 내역」
#: 처럼 사이가 벌어지는 경우가 있어 넉넉히 둔다.
_TITLE_GAP = 30
#: 앵커 출현을 몇 번까지 훑나. 정답이 47,000자 뒤에 있는 일이 있어 첫 건에서 멈추면 안 된다.
_MAX_SCAN = 80


def title_matches(caption: str, kw: str) -> bool:
    """표 바로 앞 문장이 **이 표가 무엇인지** 말하고 있나 — 「<앵커>…내역/공시」."""
    cap = _WS.sub(" ", caption)
    i = 0
    while True:
        i = cap.find(kw, i)
        if i < 0:
            return False
        tail = cap[i + len(kw): i + len(kw) + _TITLE_GAP]
        if any(m in tail for m in _TITLE_MARKS):
            return True
        i += len(kw)


def is_statement_table(parsed: dict[str, Any]) -> bool:
    """이 표가 재무제표 **본표**(또는 요약재무정보)인가. 주석 표라면 False."""
    cap = parsed.get("caption", "")
    if any(k in cap for k in _STATEMENT_CAPTIONS):
        return True
    hits = 0
    for row in parsed["rows"]:
        for cell in row:
            if cell["text"] in _STATEMENT_MARKS:
                hits += 1
                if hits >= 2:       # 두 개 이상 걸리면 본표로 본다(우연 일치 방지)
                    return True
    return False


def _table_for(html: str, p: int) -> tuple[str, dict[str, Any]] | None:
    """앵커 위치 `p` 가 가리키는 **값이 든 표** 하나. 캡션 표면 바로 뒤 표를 한 번 더 본다."""
    span = table_span(html, p)
    if not span:
        return None
    tstart, tend = span
    tbl = html[tstart:tend]
    parsed = parse_table(tbl, context=html[max(0, tstart - 2000):tstart])
    # 260823 실측: NH 「사용이 제한된」은 캡션 표(2행·숫자 0칸)를 먼저 물었다.
    # 행 수만 보면 캡션이 통과한다 — **값이 든 표인가**를 함께 묻는다.
    if parsed["n_rows"] >= 2 and parsed["n_numeric"] >= 2:
        return tbl, parsed
    upper = html.upper()
    next_open = upper.find("<TABLE", upper.find("</TABLE>", p) + 8)
    if next_open < 0 or next_open - p > _WINDOW:
        return None
    nspan = table_span(html, next_open)
    if not nspan:
        return None
    ntbl = html[nspan[0]:nspan[1]]
    nparsed = parse_table(ntbl, context=html[max(0, nspan[0] - 2000):nspan[0]])
    if nparsed["n_rows"] >= 2 and nparsed["n_numeric"] >= 2:
        return ntbl, nparsed
    return None


def extract(html: str, fields: list[str] | None = None) -> dict[str, Any]:
    """문서 HTML → 요청한 표들. 문서는 호출자가 넘긴다(캐시 재사용).

    🔴 **`사용제한` 은 kind 별로 각각 하나씩 모은다.** 전에는 앵커 하나가 걸리면 멈춰서,
       「사용제한」과 「담보제공」을 구분해 내보낸다는 계약을 지킬 수가 없었다
       (KB손보 문서에 「담보제공자산」이 6회, NH 에 「담보로 제공된」이 18회 있는데
       둘 다 restricted 만 나갔다). 이제 kind 별로 첫 표를 각각 담는다.
    """
    want = [f for f in (fields or FIELDS) if f in ANCHORS]
    off = notes_offset(html)
    out: dict[str, Any] = {}
    for field in want:
        found: list[dict[str, Any]] = []
        seen_kinds: set[str] = set()
        skipped_statement = 0
        for kw, kind in ANCHORS[field]:
            if kind in seen_kinds:
                continue                      # 이 성격은 이미 확보했다
            best: dict[str, Any] | None = None      # 제목이 맞는 표
            fallback: dict[str, Any] | None = None  # 제목 대조 실패 — 최후 수단
            start, scanned = off, 0
            seen_tables: set[str] = set()
            while scanned < _MAX_SCAN:
                p = html.find(kw, start)
                if p < 0:
                    break
                start = p + len(kw)
                scanned += 1
                hit = _table_for(html, p)
                if not hit:
                    continue
                tbl, parsed = hit
                if is_statement_table(parsed):
                    skipped_statement += 1     # 본표다 — 다음 출현으로 계속 간다
                    continue
                if parsed["caption"] in seen_tables:
                    continue
                seen_tables.add(parsed["caption"])
                entry = {"anchor": kw, "kind": kind, "pos": p, **parsed}
                if title_matches(parsed["caption"], kw):
                    best = entry
                    break                      # 원문이 제목을 붙여준 표 — 더 볼 것 없다
                if fallback is None:
                    fallback = entry           # 제목이 없는 문서를 위해 잡아만 둔다
            pick = best or fallback
            if pick is not None:
                # 🔴 제목 대조가 안 됐으면 **그렇다고 말한다.** 위험·수준별 표일 수 있어
                #    읽는 쪽이 그대로 인용하면 안 된다.
                pick["title_matched"] = best is not None
                found.append(pick)
                seen_kinds.add(kind)
        if found:
            out[field] = {"status": OK, "tables": found}
            if skipped_statement:
                out[field]["note"] = (f"재무제표 본표 {skipped_statement}건을 건너뛰고 "
                                      f"주석 표를 찾았다")
        else:
            out[field] = {"status": NOT_APPLICABLE, "tables": [],
                          "note": ("이 문서에서 해당 주석을 찾지 못했다 — 회사가 다른 이름으로 "
                                   "공시하거나 그 주석이 없다"
                                   + (f" (재무제표 본표 {skipped_statement}건은 건너뛰었다)"
                                      if skipped_statement else ""))}
    _mark_shared_tables(out)
    return out


def _mark_shared_tables(out: dict[str, Any]) -> None:
    """서로 다른 필드가 **같은 표**를 물었으면 그렇다고 적는다.

    회사가 FVPL·FVOCI를 한 표에 같이 공시하면 두 필드가 같은 표를 돌려주는 것이 맞다.
    다만 그걸 말해주지 않으면 **읽는 쪽은 도구가 고장난 것과 구별할 수 없다** —
    260823 T보고("세 필드가 완전히 동일한 표를 3번 반환")가 정확히 그 상황이었다.
    """
    where: dict[int, list[str]] = {}
    for field, res in out.items():
        for t in res.get("tables", []):
            where.setdefault(t["pos"], []).append(field)
    for pos, fields in where.items():
        if len(fields) < 2:
            continue
        for field, res in out.items():
            for t in res.get("tables", []):
                if t["pos"] == pos:
                    t["shared_with"] = [f for f in fields if f != field]
