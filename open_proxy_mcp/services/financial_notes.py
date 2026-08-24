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
_CELL = re.compile(r"<(T[DHE])([^>]*)>(.*?)</T[DHE]>", re.S | re.I)
#: 🔴 **따옴표 종류를 가리면 안 된다.** 260824 실측 — DART 뷰어가 내려주는 절 HTML 은
#:    속성을 **홑따옴표**로 쓴다(`colspan='26'`). 겹따옴표만 받던 예전 정규식은 뷰어
#:    경로에서 colspan/rowspan 을 **한 번도 못 잡았다.** 그래서 "병합 폭은 colspan 에
#:    있다"고 안내해 놓고 정작 그 값을 실어 보내지 않았고, 27열 표가 2열로 세어졌다.
#:    (document.xml 전체를 받는 경로는 겹따옴표라 여태 드러나지 않았다.)
_ACODE = re.compile(r"""ACODE\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)
_ACTX = re.compile(r"""ACONTEXT\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)
_COLSPAN = re.compile(r"""COLSPAN\s*=\s*["']?(\d+)["']?""", re.I)
_ROWSPAN = re.compile(r"""ROWSPAN\s*=\s*["']?(\d+)["']?""", re.I)
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
        # 🔴 **회사마다 말이 다르다.** 260824 T 5회차 — 메리츠증권 31-2 는
        #    「담보로 **제공한** 자산」이라 앵커 어디에도 안 걸려 「제목 대조 실패」
        #    위양성이 났다. 제목은 명백한데 말이 달랐을 뿐이다.
        #    [미해결] 현대해상 투자부동산 담보(306,940,647 천원)는 앵커
        #    「담보로 제공된 비금융자산」이 문서에 그대로 있는데도 못 싣는다 —
        #    그 표가 **값 한 칸짜리**(공시금액 / 306,940,647)라 `_table_for` 의
        #    「값이 든 표인가」 관문(n_numeric >= 2)에 걸린다. 관문을 낮추면 캡션 표가
        #    값표로 통과하므로 여기서 건드리지 않는다.
        ("담보로 제공한 자산", "pledged"),
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


def _table_for_start(html: str, pos: int) -> int | None:
    """`pos` 가 가리키는 표의 시작 위치. 분모를 찾으려면 표 경계가 필요하다."""
    span = table_span(html, pos)
    return span[0] if span else None


def table_at(html: str, pos: int) -> str | None:
    """`pos` 를 품은 `<TABLE>` 를 잘라낸다. 경계가 비정상이면 None."""
    span = table_span(html, pos)
    return html[span[0]:span[1]] if span else None


def grid_of(rows: list[list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
    """colspan·rowspan 을 채워 **진짜 직사각 행렬**로 편다.

    🔴 **260824 NH투자증권 실측 — 이걸 안 하면 27열짜리 온전한 표가 「행마다 열 수가
       다르다」로 읽힌다.** 예전에는 `sum(colspan)` 만 셌는데, `rowspan` 으로 위 행이
       아래 행의 열을 먹고 있으면 아래 행은 물리 칸이 모자란다. 그래서 머리 5행이
       27이 아니라 10으로 세어졌고, 멀쩡한 표에 🔴 오경보가 나갔다.
       **격자를 펴야 열 이름과 값이 같은 열 번호로 만난다** — 그게 이 함수의 전부다.

    이어받은 칸은 `spanned=True` 사본으로 표시한다(원본 셀 dict 는 건드리지 않는다).
    """
    out: list[list[dict[str, Any]]] = []
    carry: dict[int, tuple[int, dict[str, Any]]] = {}   # 시작열 → (남은 행수, 셀)
    for row in rows:
        line: list[dict[str, Any]] = []
        col = 0
        fresh: dict[int, tuple[int, dict[str, Any]]] = {}

        def _drain(col: int) -> int:
            while col in carry:
                _, up = carry[col]
                for _i in range(up.get("colspan", 1)):
                    line.append(dict(up, spanned=True))
                col += up.get("colspan", 1)
            return col

        for cell in row:
            col = _drain(col)
            span = cell.get("colspan", 1)
            line.append(cell)
            for _i in range(span - 1):
                line.append(dict(cell, spanned=True))
            if cell.get("rowspan", 1) > 1:
                fresh[col] = (cell["rowspan"] - 1, cell)
            col += span
        _drain(col)
        out.append(line)
        carry = {c: (n - 1, up) for c, (n, up) in carry.items() if n - 1 > 0}
        carry.update(fresh)
    return out


def parse_table(tbl: str, context: str = "") -> dict[str, Any]:
    """표 하나 → 행렬 + 형식 판정. **셀을 합치거나 나누지 않는다.**

    태그 경로면 값마다 `acode`/`acontext` 를 함께 실어 보낸다 — 연결/별도와 분류축이
    거기 들어 있어 사람이 열 위치로 짐작할 필요가 없다.
    """
    tagged = bool(_TE.search(tbl))
    rows: list[list[dict[str, Any]]] = []
    for raw in _ROW.findall(tbl):
        cells = []
        for tag, attrs, body in _CELL.findall(raw):
            cell: dict[str, Any] = {"text": _clean(body)}
            # 머리칸/값칸 구분. 열 경로를 세우려면 어디까지가 머리인지 알아야 한다.
            if tag.upper() == "TH":
                cell["th"] = True
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
                    cell["acode"] = m.group(1) or m.group(2)
                m2 = _ACTX.search(attrs)
                if m2:
                    ctx = m2.group(1) or m2.group(2)
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
    # 🔴 열 폭은 **격자를 편 뒤에** 센다. colspan 만 더하면 rowspan 이 먹은 열을 빠뜨려
    #    멀쩡한 직사각형 표가 「행마다 열 수가 다르다」로 나온다(260824 NH 실측).
    grid = grid_of(rows)
    widths = [len(r) for r in grid] or [0]
    return {
        "format": "xbrl_tagged" if tagged else "html_table",
        "rows": rows,
        "grid": grid,
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
#: 🔴 **「원」 단위를 못 잡고 있었다.** `[가-힣]+원` 은 「원」 앞에 글자가 **하나 이상** 있어야
#:    맞으므로 「(단위 : 원)」 이 통째로 빠졌다. 260823 census — 현대해상 사업보고서 5표와
#:    미래에셋생명 4표가 「단위 표기 없음」으로 나간 이유가 이것이다. 원 단위 회사를 백만원
#:    회사와 나란히 놓으면 10⁶ 이 어긋난다.
_UNIT = re.compile(r"단위\s*[:：]?\s*([가-힣]{0,3}원)")


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
_TITLE_MARKS = ("구성내역", "세부내역", "내역", "공시", "내용", "장부금액")

#: 🔴 **「다음과 같습니다」는 좁게만 받는다.** 260824 T 5회차 — 메리츠증권 31-2 제목은
#:    「…담보로 제공한 자산**은 다음과 같습니다**」로 끝나 「내역」도 「공시」도 없다.
#:    그래서 제목이 명백한데 🔴 위양성이 났다. 그런데 이 말을 넓게 받으면 **앵커를 스쳐
#:    지나가기만 한 문장까지 제목으로 인정된다** — 국민은행 FVOCI 가 「기타포괄손익-공정가치
#:    측정 항목으로 지정한 지분상품으로부터 인식한 **배당금수익**은 다음과 같습니다」로
#:    걸려 배당금 표가 뽑혔다(실측). **앵커가 그 문장의 주어일 때만** 인정한다 —
#:    앵커 바로 뒤에 조사만 붙는 경우다.
_TITLE_MARKS_TIGHT = ("다음과 같",)
_TITLE_GAP_TIGHT = 8
#: 앵커와 표지어 사이에 끼는 말의 길이. 「18. 담보제공자산 및 담보로 제공받은 자산(1) 당기말과
#: 전기말 현재 담보로 제공한 자산의 내역」처럼 45자까지 벌어진다(신한은행).
_TITLE_GAP = 60
#: 앵커 출현을 몇 번까지 훑나. 정답이 47,000자 뒤에 있는 일이 있어 첫 건에서 멈추면 안 된다.
_MAX_SCAN = 80
#: 🔴 **사용제한이 한 표에 다 있지 않다.** 260823 마스터 제보 — 우리은행은 「현금및현금성자산
#:    (2) 사용이 제한된 현금및현금성자산의 내용」과 「상각후원가측정대출채권및기타금융자산
#:    (3) 사용이 제한된 예치금의 내용」 **두 군데로 나뉜다.** 첫 표만 담으면 절반이 빠지고,
#:    unencumbered cash 를 그만큼 크게 잡는다. 사용제한 필드만 여러 표를 모은다.
_MULTI_TABLE_LIMIT = 3

#: 🔴 **제목이 붙어 있어도 우리가 찾는 표가 아닌 것들.** 260823 census 41건 재검증에서
#:    걸러낸 자리다 — 국민은행 상각후원가는 316행짜리 「특수관계자와의 주요 채권ㆍ채무」를,
#:    우리은행 FVPL 은 「신용위험의 최대노출액」을, 하나은행 FVPL 은 「공정가치체계」를,
#:    메리츠증권 상각후원가는 「영업부문별 재무정보」를 물었다. 전부 계정과목 이름이 그 안에
#:    등장하기 때문이다. 유형별 구성·사용제한 판단에는 쓸 수 없으니 제목이 맞아도 물리지 않는다.
_OFF_TOPIC = ("특수관계자", "신용위험", "기대신용손실", "최대노출", "부문별", "집중",
              "익스포져", "공정가치체계", "수준별", "민감도", "위험 집중",
              "등급별", "서열체계", "가치평가기법")


def is_totalish_title(title: str) -> bool:
    """원문이 「…, 합계」·「… 총계」로 따로 실은 표인가. 세부표의 짝이다."""
    head = (title or "").split("(단위")[0]
    return any(k in head for k in ("합계", "총계"))


def is_off_topic(caption: str) -> bool:
    """표 앞 문장이 **다른 주제**를 말하고 있나(위험·특수관계자·부문별 …)."""
    return any(k in caption for k in _OFF_TOPIC)



#: 🔴 **방향이 반대인 표.** 260823 T보고 — 신한은행 담보제공으로 나온 60행 표가
#:    「특수관계자로부터 **제공받고** 있는 담보」였다. 제공자 열에 종속기업·주요경영진이
#:    들어 있다. 은행 자산이 묶인 것이 아니라 **받은 담보**라, 유동성 판단에서는 부호가
#:    거꾸로다. 제목 대조는 통과하므로(둘 다 「담보」다) 별도로 막아야 한다.
_WRONG_DIRECTION = ("제공받", "수취한 담보", "특수관계자")
#: 제목 문장만 본다. caption 은 표 앞 2,000자의 꼬리라 **앞 표의 문장이 섞여 들어온다** —
#: 넓게 보면 맞는 표까지 함께 버린다.
_DIRECTION_TAIL = 140



#: 🔴 **같은 필드에 축이 다른 표가 둘 있다.** 260823 T보고 —
#:    「범주별」은 **어떤 자산을 어떤 측정범주로 분류했나**(행=예치금·대출채권·유가증권,
#:    열=당기손익·기타포괄손익·상각후원가)이고, 「유형별」은 **그 안이 무엇인가**
#:    (국공채·금융채·회사채·수익증권)이다. **헤어컷은 유형별로만 매길 수 있다** —
#:    범주별은 「FVPL 유가증권 31.5조」 한 칸이라 국채인지 회사채인지 알 수 없다.
#:    국민은행 상각후원가가 범주별 표를 경고 없이 물었던 자리다.
_CATEGORY_AXIS = ("당기손익", "기타포괄손익", "상각후원가", "위험회피")
_TYPE_AXIS = ("국공채", "국채", "공채", "회사채", "금융채", "특수채", "수익증권",
              "지분증권", "채무증권", "출자금", "자산담보부", "기업어음", "사모사채",
              "외화유가증권", "주식")


def axis_of(parsed: dict[str, Any]) -> str | None:
    """이 표의 축이 「범주별」인가 「유형별」인가. 못 가리면 None.

    🔴 **머리글 몇 줄만 보면 XBRL 표를 놓친다.** 260823 실측 — NH투자증권·미래에셋증권·
       키움증권의 FVOCI·상각후원가는 유형별인데 「판별 못함」으로 나왔다. 태그 표는 머리글이
       4~6단으로 겹쳐 있어 앞 몇 행에 유형 이름이 안 들어온다.
       → **숫자가 아닌 칸(이름 칸)을 표 전체에서 모은다.**
    """
    labels = list(parsed.get("header") or [])
    for row in parsed["rows"]:
        for cell in row:
            text = cell["text"]
            if text and not _NUMISH.match(text):
                labels.append(text)
    blob = " ".join(labels)
    cat = sum(1 for k in _CATEGORY_AXIS if k in blob)
    typ = sum(1 for k in _TYPE_AXIS if k in blob)
    if typ >= 3 and typ > cat:
        return "유형별"
    if cat >= 3 and cat > typ:
        return "범주별"
    return None



#: 표 앞 문장에서 **이 표의 제목만** 잘라낸다. caption 은 표 앞 2,000자의 꼬리라
#: 🔴 **앞 표의 숫자 잔해가 그대로 붙어 온다.** 260823 실측 — KB손보 사용제한 caption 이
#:    「…합계 91,701 450,935 (3) 보고기간말 현재 사용이 제한되어 있는 예치금 내역은…」인데
#:    앞의 91,701 은 **예치금 총액 표**의 합계다(사용제한은 26,356). 시험자도 나도 이걸
#:    「빠진 사용제한 표」로 읽었다. 잔해는 매칭에만 쓰고 **사람에게는 제목만 보인다.**
#: 마커가 없을 때 제목으로 인정하는 꼬리 길이
_TITLE_TAIL = 120
_TITLE_START = re.compile(r"(?:\(\d+\)|\d+\s*[-.]\s*\d*\s*\.?)\s*(?=[가-힣])")


def title_only(caption: str) -> str:
    """caption → 이 표의 제목 문장. 못 가르면 caption 그대로."""
    cap = _WS.sub(" ", caption).strip()
    best = None
    for m in _TITLE_START.finditer(cap):
        if len(cap) - m.start() >= 12:
            best = m.start()
    if best is not None:
        return cap[best:]
    # 🔴 **마커를 못 찾아도 caption 전체를 제목으로 보면 안 된다.** 260823 실측 —
    #    부산은행 FVOCI 는 표 앞 문단이 신용등급 정의라 제목 문장이 아예 없는데,
    #    잔해 안에 섞인 앵커로 ✅ 를 받았다. 제목은 표 **바로 앞**에 오므로 꼬리만 본다.
    return cap[-_TITLE_TAIL:]


#: 「N. <계정명>」 — 주석의 **표제**다. 같은 앵커가 걸리는 표가 여럿일 때 이게 가른다.
#: 260823 실측 — KB손보 상각후원가는 「2) …상각후원가로 측정하는 금융상품의 장부금액과
#: 공정가치」(507,290)를 물었는데, 정답은 67,000자 뒤의 「**9. 상각후원가측정유가증권**
#: 보고기간말 현재 상각후원가측정유가증권의 내역」(574,337)이다. 둘 다 제목 대조는 통과한다.
_HEADING_TAIL = re.compile(r"\d+\s*[.\-]\s*$")
#: 제목이 맞아도 **우리가 찾는 내역이 아닌** 표. 원문이 붙인 말로만 판별한다.
_WEAK_TITLE = ("장부금액과 공정가치", "증감내역", "변동내역", "대손충당금", "평가 및 처분",
               "손익", "등급별", "서열체계", "신용위험", "최대노출", "범주별", "수준별",
               "총장부가액")


def is_note_heading(caption: str, kws: list[str] | tuple[str, ...]) -> bool:
    """표 앞 문장이 「N. <계정명>」으로 시작하는 **주석 표제**인가."""
    cap = _WS.sub(" ", caption)
    for kw in kws:
        i = 0
        while True:
            i = cap.find(kw, i)
            if i < 0:
                break
            if _HEADING_TAIL.search(cap[max(0, i - 8):i]):
                return True
            i += len(kw)
    return False


#: 필드마다 「이 범주를 가리키는 말」. 제목이 **다른 필드의 범주**를 말하고 있으면
#: 그 표는 요청한 범주의 표가 아니다.
_FIELD_MARKS = {
    "FVPL": ("당기손익-공정가치", "당기손익인식"),
    "FVOCI": ("기타포괄손익-공정가치",),
    "상각후원가": ("상각후원가",),
}


def title_says_other_field(title: str, field: str) -> str | None:
    """제목이 **다른 범주**를 말하고 있으면 그 범주 이름을 돌려준다.

    🔴 260824 T보고 — 메리츠증권 `상각후원가` 요청에 「8-4. …**기타포괄손익-공정가치측정
       금융자산평가손익**의 내역」 표가 나왔다. 축이 유형별이라 ⚠️ 로 통과했는데,
       축이 맞아도 **범주가 다르면 다른 표다.** 앵커가 걸린 것은 표 안에 「상각후원가」
       라는 **열 이름**이 있어서다. 제목이 이 필드의 범주를 말하지 않고 다른 범주를
       말하면 🔴 로 되돌린다.
    """
    head = (title or "").split("(단위")[0]
    if not head:
        return None
    mine = _FIELD_MARKS.get(field, ())
    if any(m in head for m in mine):
        return None
    for other, marks in _FIELD_MARKS.items():
        if other == field:
            continue
        for m in marks:
            if m in head:
                return other
    return None


def title_weakness(caption: str) -> int:
    """제목에 「장부금액과 공정가치」·「증감내역」처럼 **내역이 아님**을 알리는 말이 몇 개인가."""
    tail = title_only(caption)
    return sum(1 for k in _WEAK_TITLE if k in tail)


#: 사용제한 금액이 **재무상태표 어느 계정에서 나온 것인가.** 이게 없으면 뺄셈을 시작할 수
#: 없다 — 260823 시험자 지적: 「사용제한 26,356 을 현금및현금성자산 1,507,988 에서 빼야
#: 하는지 예치금에서 빼야 하는지 도구가 말해주지 않는다」. 원문 제목에 대개 적혀 있다.
_ACCOUNTS = ("현금및현금성자산", "현금및예치금", "예치금", "현금성자산",
             "대출채권", "유가증권", "기타금융자산", "금융자산", "예금")


def account_of(caption: str) -> str | None:
    """이 표의 금액이 붙어 있는 재무상태표 계정. 제목에서 읽는다."""
    tail = title_only(caption)
    for acc in _ACCOUNTS:
        if acc in tail:
            return acc
    return None



#: 별도재무제표 구간의 시작 표지. DART 정기보고서 「III. 재무에 관한 사항」은
#: 「2. 연결재무제표 → 3. 연결재무제표 주석 → **4. 재무제표** → 5. 재무제표 주석」 순이다.
#: 🔴 **HTML 표에는 연결/별도 표시가 없다.** XBRL 은 값마다 ACONTEXT 에 박혀 있는데
#:    표구조 경로에는 아무것도 없어, 같은 제목의 표가 두 번 나오면 어느 쪽인지 알 수 없었다.
#:    합산하면 이중계상이 난다 — 260823 시험자 지적. 위치로 가른다.
#:    실측 경계: KB손보 1,378,097 · 국민은행 1,755,152 · 신한은행 1,847,471 ·
#:    우리은행 1,885,740. 네 건 모두 연결 표는 앞, 별도 표는 뒤에 정확히 떨어진다.
_SEPARATE_MARKS = ("4. 재무제표", "4.재무제표")


def separate_offset(html: str, notes_start: int = 0) -> int | None:
    """별도재무제표가 시작하는 위치. 없으면 None(연결만 실린 문서)."""
    for mark in _SEPARATE_MARKS:
        i = html.find(mark, notes_start)
        if i > 0:
            return i
    return None


def basis_at(pos: int, sep: int | None) -> str | None:
    """이 표가 연결인가 별도인가. 경계를 못 찾았으면 None."""
    if sep is None:
        return None
    return "별도" if pos >= sep else "연결"



#: 🔴 **뺄 계정만 알려주고 총액을 안 주면 뺄셈이 안 된다.** 260823 시험자 실측 —
#:    「현금및현금성자산 36,152,124 − 사용제한 20,772,820」을 하려는데 앞의 36,152,124 를
#:    도구가 주지 않는다. 5차에는 제목 꼬리 잔해에서 우연히 얻었는데 꼬리를 정리하면서
#:    사라졌다. 원문은 대개 **바로 앞 항**에 그 계정의 구성내역을 싣는다
#:    (우리은행 (1)→(2) · KB손보 (2)→(3) · 국민은행 (1)→(2)). 그 표의 합계를 붙인다.
_TOTAL_ROW = ("합계", "합 계", "계", "소계")


def account_total(html: str, table_start: int, account: str) -> list[str] | None:
    """사용제한 표 **바로 앞** 표가 같은 계정의 구성내역이면 그 합계 행을 돌려준다."""
    upper = html.upper()
    end = upper.rfind("</TABLE>", 0, table_start)
    if end < 0:
        return None
    start = upper.rfind("<TABLE", 0, end)
    if start < 0 or table_start - end > 4_000:
        return None                      # 너무 멀면 다른 이야기다
    prev = parse_table(html[start:end + 8], context=html[max(0, start - 2000):start])
    title = title_only(prev["caption"])
    if account not in title or not any(m in title for m in ("구성내역", "내역")):
        return None
    for row in reversed(prev["rows"]):
        label = row[0]["text"] if row else ""
        if any(label.startswith(m) for m in _TOTAL_ROW):
            return [c["text"] for c in row[1:] if _NUMISH.match(c["text"])] or None
    return None



# ── 구조 앵커로 구간을 자른다 (business_details 의 _slice_by_aassoc 방식) ──
# 🔴 **키워드로 위치를 찾으면 계속 막힌다.** 260823 마스터 지시 — 지점을 잘 잡아
#    구간을 통으로 넘기는 편이 낫다. DART 문서에는 목차 좌표가 태그로 박혀 있다.
#      D-0-3-2-0  2. 연결재무제표      ← 본표(분모의 출처)
#      D-0-3-3-0  3. 연결재무제표 주석  ← 연결 주석
#      D-0-3-4-0  4. 재무제표          ← 별도 본표
#      D-0-3-5-0  5. 재무제표 주석      ← 별도 주석
#      D-0-3-6-0  6. 배당에 관한 사항   ← 주석 끝 경계
#    실측(KB손보) D-0-3-4-0 @1,378,007 — 문자열로 찾던 1,378,097 과 사실상 같지만
#    **목차·본문 혼동이 구조적으로 없다.** 코드는 서식 개정 시 의미가 이동할 수 있어
#    business_details 와 같이 **제목 키워드로 이중검증**한다.
_AASSOC_RE = re.compile(r'<TITLE\b[^>]*\bAASSOCNOTE="(D-0-[0-9-]+)"[^>]*>(.*?)</TITLE>',
                        re.IGNORECASE | re.DOTALL)
SEC_CONN_FS = "D-0-3-2-0"
SEC_CONN_NOTE = "D-0-3-3-0"
SEC_SEP_FS = "D-0-3-4-0"
SEC_SEP_NOTE = "D-0-3-5-0"
_SEC_EXPECT = {
    SEC_CONN_FS: re.compile(r"연결\s*재무제표"),
    SEC_CONN_NOTE: re.compile(r"연결\s*재무제표\s*주석"),
    SEC_SEP_FS: re.compile(r"재무제표"),
    SEC_SEP_NOTE: re.compile(r"재무제표\s*주석"),
}


def sections(html: str) -> dict[str, tuple[int, int]]:
    """구조 앵커 → {코드: (시작, 끝)}. 앵커가 없거나 제목이 안 맞으면 그 구간은 없다."""
    pos: dict[str, int] = {}
    bad: set[str] = set()
    starts: list[int] = []
    for m in _AASSOC_RE.finditer(html):
        code = m.group(1)
        starts.append(m.start())
        if code in pos:                      # 중복 출현 — 믿을 수 없다
            bad.add(code)
            continue
        pos[code] = m.start()
        expect = _SEC_EXPECT.get(code)
        if expect is not None:
            title = _WS.sub(" ", _TAG.sub("", m.group(2))).strip()
            if not expect.search(title):
                bad.add(code)
    starts.sort()
    out: dict[str, tuple[int, int]] = {}
    for code, start in pos.items():
        if code in bad or code not in _SEC_EXPECT:
            continue
        after = [x for x in starts if x > start]
        if after:
            out[code] = (start, after[0])
    return out


def note_regions(html: str) -> list[tuple[str, int, int]]:
    """주석 구간 목록 — [(기준, 시작, 끝)]. 구조 앵커가 없으면 빈 목록."""
    sec = sections(html)
    out = []
    if SEC_CONN_NOTE in sec:
        out.append(("연결", *sec[SEC_CONN_NOTE]))
    if SEC_SEP_NOTE in sec:
        out.append(("별도", *sec[SEC_SEP_NOTE]))
    return out



#: 🔴 **뺄셈의 분모는 주석이 아니라 재무상태표에서 가져온다.** 260823 시험자 제안 —
#:    「바로 앞 표」 규칙은 절반만 맞았다(우리은행은 (1)현금 구성 → (2)사용제한 현금 →
#:    (3)사용제한 예치금 이라 (3)의 분모가 바로 앞이 아니다). 재무상태표는 계정명과
#:    금액이 1:1 이고 중복이 없으며, **연결/별도가 구간 단계에서 이미 갈려 있다.**
#:    실측 — 우리은행 연결 재무상태표 「현금및현금성자산 36,152,124」가 그대로 분모다.
#:    🔴 **이름이 정확히 안 맞으면 붙이지 않는다.** KB손보 🏷은 「예치금」인데 재무상태표에
#:    그 계정이 없다. 틀린 분모는 없는 것보다 나쁘다 — 「못 찾음」을 명시한다.
_BS_MARKS = ("자산", "자산총계", "부채", "자본")


def balance_sheet_from(seg: str) -> dict[str, Any] | None:
    """**재무제표 절 조각**에서 재무상태표를 읽는다(노드로 받아온 경우)."""
    upper = seg.upper()
    i = 0
    while True:
        start = upper.find("<TABLE", i)
        if start < 0:
            return None
        end = upper.find("</TABLE>", start)
        if end < 0:
            return None
        i = end + 8
        parsed = parse_table(seg[start:end + 8], context=seg[max(0, start - 1500):start])
        if parsed["n_numeric"] < 10:
            continue
        labels = [row[0]["text"] for row in parsed["rows"] if row]
        if not any(m in labels for m in _BS_MARKS):
            continue
        accounts: dict[str, list[str]] = {}
        for row in parsed["rows"]:
            if len(row) < 2:
                continue
            nm = row[0]["text"]
            vals = [c["text"] for c in row[1:] if _NUMISH.match(c["text"])]
            if nm and vals:
                accounts.setdefault(nm, vals)
        return {"accounts": accounts, "unit": parsed["unit"]}


def balance_sheet(html: str, basis: str | None) -> dict[str, Any] | None:
    """그 기준(연결/별도)의 **재무상태표** → {계정명: [값…]} + 단위. 못 찾으면 None."""
    sec = sections(html)
    code = SEC_SEP_FS if basis == "별도" else SEC_CONN_FS
    if code not in sec:
        return None
    r0, r1 = sec[code]
    seg = html[r0:r1]
    upper = seg.upper()
    i = 0
    while True:
        start = upper.find("<TABLE", i)
        if start < 0:
            return None
        end = upper.find("</TABLE>", start)
        if end < 0:
            return None
        i = end + 8
        parsed = parse_table(seg[start:end + 8], context=seg[max(0, start - 1500):start])
        if parsed["n_numeric"] < 10:
            continue
        labels = [row[0]["text"] for row in parsed["rows"] if row]
        if not any(m in labels for m in _BS_MARKS):
            continue                       # 재무상태표가 아니다(손익·현금흐름 등)
        accounts: dict[str, list[str]] = {}
        for row in parsed["rows"]:
            if len(row) < 2:
                continue
            name = row[0]["text"]
            vals = [c["text"] for c in row[1:] if _NUMISH.match(c["text"])]
            if name and vals:
                accounts.setdefault(name, vals)
        return {"accounts": accounts, "unit": parsed["unit"]}


#: 계정명 꼬리에 붙는 주석 참조. `현금및현금성자산 (주35,37)` 처럼 붙어 온다.
_NOTE_REF = re.compile(r"\s*[\(（]\s*주[\s\d,，.·]*[\)）]\s*$")


def norm_account(name: str) -> str:
    """계정명에서 주석 참조 꼬리를 뗀다. 260824 실측 — NH투자증권 재무상태표는
    `현금및현금성자산 (주35,37)` 로 실려 있어 완전일치가 **꼬리 때문에만** 깨졌다."""
    return _NOTE_REF.sub("", (name or "").strip()).strip()


def lookup_account(bs: dict[str, Any] | None, account: str) -> dict[str, Any] | None:
    """재무상태표에서 계정을 찾는다. **틀린 분모를 만들지 않는 것이 첫째다.**

    260824 T보고 — 6사 중 5사가 「분모 없음」으로 나갔다. 원인이 셋으로 갈린다.
      ① 주석 참조 꼬리 때문에만 안 맞음(NH `현금및현금성자산 (주35,37)`) → **정규화하면 맞는다.**
      ② 은행·증권은 「**현금및예치금**」으로 현금과 **묶여** 있다(국민은행·메리츠증권).
         → 예치금만의 잔액이 아니다. **그대로 분모로 쓰면 unencumbered 가 과대계상된다.**
            값을 주되 `contains=True` 로 「묶여 있다」고 밝히고 쓰지 말라고 한다.
      ③ 보험사는 그 계정 자체가 없다(KB손보·현대해상) → 없는 것이 맞다.
    ①과 ②를 ③과 같은 「분모 없음」으로 뭉개면 읽는 쪽이 취할 행동이 달라진다.
    """
    if not bs or not account:
        return None
    want = norm_account(account)
    index = {norm_account(k): (k, v) for k, v in bs["accounts"].items()}
    hit = index.get(want)
    if hit:
        return {"values": hit[1], "unit": bs["unit"], "matched": hit[0]}
    # 묶여 있는 계정 — 이름이 통째로 들어 있고 그 계정이 더 긴 경우만
    wider = [(k, v) for k, (orig, v) in index.items()
             if want and want in k and k != want and len(k) > len(want)]
    if len(wider) == 1:
        k, v = wider[0]
        return {"values": v, "unit": bs["unit"], "matched": k, "contains": True}
    if len(wider) > 1:
        # 「금융자산」처럼 여러 계정에 걸치는 이름 — 「없다」와는 다른 상황이다
        return {"values": [], "unit": bs["unit"],
                "spread": [k for k, _ in wider[:4]]}
    return None



#: 🔴 **같은 주석 안에 당기표와 전기표가 따로 실리는 회사가 있다.** 260823 실측 —
#:    우리은행 별도 「사용이 제한된 현금및현금성자산」이 제193(당)기 20,466,725 와
#:    제192(전)기 23,293,045 로 **표가 둘**이다. 표시가 없으면 더해서 이중계상이 난다.
_PERIOD_RE = re.compile(r"제\s*\d+\s*[\(（]?\s*([당전])\s*[\)）]?\s*기")
_PERIOD_WORDS = (("당", ("당반기말", "당분기말", "당기말", "당반기", "당분기")),
                 ("전", ("전반기말", "전분기말", "전기말", "전반기")))


def period_of(parsed: dict[str, Any]) -> str | None:
    """이 표가 **당기**인가 **전기**인가. 둘 다/못 가리면 None(= 한 표에 함께 있다)."""
    labels = " ".join(c["text"] for row in parsed["rows"][:4] for c in row)
    labels += " " + " ".join(parsed.get("header") or [])
    marks = set(_PERIOD_RE.findall(labels))
    for tag, words in _PERIOD_WORDS:
        if any(w in labels for w in words):
            marks.add(tag)
    if marks == {"당"}:
        return "당기"
    if marks == {"전"}:
        return "전기"
    return None


#: 제목 폴백이 앞 표 잔해를 물었는지. 260823 실측 — 우리은행 별도 전기표의 제목 자리에
#: 앞 표가 통째로 들어왔다. 표지는 **문장이 끝난 뒤에도 글이 이어지는 것**이다
#: (「…다음과 같습니다.」 뒤에 표 머리글과 값이 붙는다). 숫자 개수로는 안 잡힌다.
_SENT_END = re.compile(r"(?:다음과 같습니다|같습니다|합니다)\s*[.。]?")
_DIGIT_GROUP = re.compile(r"[\d,]{3,}")


def looks_like_debris(title: str) -> bool:
    ends = [m.end() for m in _SENT_END.finditer(title)]
    if ends and len(title) - ends[0] > 30:
        return True
    return len(_DIGIT_GROUP.findall(title)) >= 6



# ── 목차 노드로 필요한 절만 받아온다 ──
# 🔴 **문서를 통째로 받을 이유가 없다.** 260824 마스터 지시 + 실측.
#    NH투자증권 사업보고서 19.5MB → 「8. 사용이 제한된 예금 등 (연결)」 노드는 34KB.
#    `business_details` 가 이미 쓰는 viewer 좌표(rcpNo·dcmNo·eleId·offset·length)를 그대로 쓴다.
#    회사에 따라 갈린다 — 주석 항목마다 lvl3 노드가 있는 곳(NH·현대해상)과
#    주석이 lvl2 하나로 통째인 곳(KB손보 935KB·우리은행 1.0MB)이 있다. 둘 다 전체보다 훨씬 싸다.
_NOTE_PARENT = ("연결재무제표 주석", "재무제표 주석")
_FS_NODE = {"연결": "연결재무제표", "별도": "재무제표"}


def _node_basis(node: dict) -> str:
    """이 노드가 연결인가 별도인가 — 부모 절 이름으로 가른다."""
    parent = (node.get("parent_text") or "").strip()
    text = (node.get("text") or "").strip()
    if "연결재무제표 주석" in parent or "연결재무제표" == text.split(". ")[-1]:
        return "연결"
    return "별도"


def pick_note_nodes(nodes: list[dict], fields: list[str]) -> list[tuple[str, dict]]:
    """필드에 맞는 **주석 항목 노드**를 고른다. 없으면 빈 목록(→ 절 전체로 폴백)."""
    out: list[tuple[str, dict]] = []
    for node in nodes:
        parent = (node.get("parent_text") or "").strip()
        if not any(p in parent for p in _NOTE_PARENT):
            continue
        title = (node.get("text") or "").strip()
        for field in fields:
            if any(kw in title for kw, _ in ANCHORS[field]):
                out.append((_node_basis(node), node))
                break
    return out


def pick_chapter_nodes(nodes: list[dict], names: tuple[str, ...]) -> list[tuple[str, dict]]:
    """「3. 연결재무제표 주석」·「5. 재무제표 주석」 같은 **절 노드**를 고른다."""
    out: list[tuple[str, dict]] = []
    for node in nodes:
        title = (node.get("text") or "").strip()
        if any(title.endswith(n) for n in names):
            basis = "연결" if title.replace(" ", "").find("연결") >= 0 else "별도"
            out.append((basis, node))
    return out


def is_wrong_direction(caption: str) -> bool:
    """이 표가 **받은 담보**·특수관계자 표인가. 우리가 찾는 것은 회사가 **제공한** 담보다."""
    tail = _WS.sub(" ", caption)[-_DIRECTION_TAIL:]
    return any(k in tail for k in _WRONG_DIRECTION)


def title_matches(caption: str, kws: tuple[str, ...] | list[str] | str) -> bool:
    """표 바로 앞 문장이 **이 표가 무엇인지** 말하고 있나 — 「<앵커>…내역/공시」.

    🔴 **제목에 쓰인 이름이 앵커와 다를 수 있다.** 삼성증권은 「상각후원가측정유가증권」으로
    걸리는데 제목은 「상각후원가측정금융자산의 내역」이고, 삼성화재는 「담보로 제공된 금융자산」
    으로 걸리는데 제목은 「담보제공자산 … 담보로 제공된 자산에 대한 공시」다.
    → 그 성격(kind)의 **모든 앵커**로 대조한다. 하나만 보면 맞는 표를 놓친다.
    """
    if isinstance(kws, str):
        kws = [kws]
    # 🔴 **caption 전체로 대조하면 앞 표 잔해가 오탐을 만든다.** 260823 실측 —
    #    부산은행 FVOCI 는 앞 표 잔해(「Grade 1 1등급~5등급 AAA, AA…」)에 앵커가 섞여
    #    들어와 「신용위험 등급별」 표가 ✅ 를 받았다. 5차엔 🔴 로 걸러지던 자리다.
    #    **제목에서만 대조한다.**
    cap = title_only(caption)
    for kw in kws:
        i = 0
        while True:
            i = cap.find(kw, i)
            if i < 0:
                break
            tail = cap[i + len(kw): i + len(kw) + _TITLE_GAP]
            if any(m in tail for m in _TITLE_MARKS):
                return True
            near = cap[i + len(kw): i + len(kw) + _TITLE_GAP_TIGHT]
            if any(m in near for m in _TITLE_MARKS_TIGHT):
                return True
            i += len(kw)
    return False


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


#: 표 앞 문장을 자를 때 「앞 표의 숫자 잔해」를 끊는 데 쓴다.
_NUM_RUN = re.compile(r"[\d,]{3,}")


def caption_before(html: str, tstart: int, window: int = 2000) -> str:
    """표 바로 앞의 **제목 문장만** 돌려준다. 앞 표의 숫자는 끊는다.

    🔴 **260824 NH투자증권 실측 — 제목이 통째로 숫자 잔해로 나갔다.** DART 뷰어는
       제목을 `class='nb'` 짜리 **작은 표**로 얹는다(「상각후원가측정금융자산의 내역,
       합계」 / 「당반기말」 / 「(단위 : 백만원)」). 그런데 앞 2000자를 그대로 쓰면
       그 앞 값표의 숫자가 먼저 들어차서 제목이 밀려난다. 그러면 제목 대조가 실패하고
       「원문에 제목 문장이 없다」로 나간다 — **있는데 못 읽은 것이다.**
       그래서 창 안에서 **값이 든 표가 끝난 지점**까지 잘라내고 그 뒤만 본다.
    """
    seg = html[max(0, tstart - window):tstart]
    cut = 0
    for m in re.finditer(r"</TABLE\s*>", seg, re.I):
        # 이 </TABLE> 로 닫히는 표가 값표였나 — 직전 <TABLE 부터 숫자 뭉치를 센다
        open_at = seg.upper().rfind("<TABLE", 0, m.start())
        chunk = seg[open_at:m.start()] if open_at >= 0 else seg[:m.start()]
        # 🔴 태그를 벗기고 센다 — `<COL width='600'>` 의 600 을 값으로 세면 제목표까지 잘린다.
        if len(_NUM_RUN.findall(_TAG.sub(" ", chunk))) >= 3:
            cut = m.end()
    return seg[cut:]


def _table_for(html: str, p: int) -> tuple[str, dict[str, Any]] | None:
    """앵커 위치 `p` 가 가리키는 **값이 든 표** 하나. 캡션 표면 바로 뒤 표를 한 번 더 본다."""
    span = table_span(html, p)
    if not span:
        return None
    tstart, tend = span
    tbl = html[tstart:tend]
    parsed = parse_table(tbl, context=caption_before(html, tstart))
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
    nparsed = parse_table(ntbl, context=caption_before(html, nspan[0]))
    if nparsed["n_rows"] >= 2 and nparsed["n_numeric"] >= 2:
        return ntbl, nparsed
    return None


def extract(html: str, fields: list[str] | None = None) -> dict[str, Any]:
    """문서 HTML 전체 → 요청한 표들. 구간을 스스로 잘라 `extract_regions` 로 넘긴다.

    문서를 통째로 받은 경우의 경로다. 목차 노드로 **필요한 절만** 받아온 경우는
    호출자가 `extract_regions` 를 직접 부른다(그쪽이 훨씬 싸다).
    """
    regions = note_regions(html)
    if not regions:
        off = notes_offset(html)
        sep = separate_offset(html, off)
        regions = ([("연결", off, sep), ("별도", sep, len(html))] if sep
                   else [(None, off, len(html))])
    parts = [(basis, html[r0:r1]) for basis, r0, r1 in regions]
    sheets = {basis: balance_sheet(html, basis) for basis, _, _ in regions}
    return extract_regions(parts, fields, sheets)


# ── 분모표: 「예치금의 구성내역」 ──────────────────────────────────────────
#
# 🔴 **260824 T 5회차 — 6사 중 5사가 unencumbered 를 못 냈고 병목이 하나였다.**
#    뺄 금액(사용제한)은 나오는데 **뺄 원본(예치금 잔액)** 이 없었다. 재무상태표는
#    은행·증권을 「현금및예치금」으로 묶어 실어서 그대로 쓰면 현금까지 분모에 들어간다.
#    그런데 **그 분해는 사용제한 주석 바로 앞 항에 거의 항상 있다.** 실측 —
#      KB손보  (2) 예치금의 구성내역 91,701      → (3) 사용제한 26,356    = 65,345
#      국민은행 (1) 예치금의 구성내역 29,989,042 → (2) 사용제한 25,282,482 = 4,706,560
#      신한은행 (1) 현금 및 예치금의 종류별 내역 38,213,613 → (2) 사용제한 18,499,662
#      메리츠   6. 현금및예치금 세부 내역 10,080,062,941천원 → 31-1 사용제한
#    국민은행 실측이 재무상태표를 쓰면 안 되는 이유를 그대로 보여준다 —
#    재무상태표 현금및예치금 32,554,519 vs 주석 예치금 29,989,042, 차이 2,565,477 이 현금.
#    그대로 썼으면 분모가 2.57조 부풀었다.
#
# 🔴 **덤으로 단위 문제가 사라진다.** 메리츠 주석표는 **천원**이고 재무상태표는 **원**이다.
#    같은 응답 안에서 분자는 천원·분모는 원이라 그냥 빼면 1,000배 어긋났다(T 5회차 최대 함정).
#    분모를 같은 주석에서 가져오면 단위가 저절로 맞는다.

#: 분모표로 쓸 수 있는 제목 — 「무엇으로 이루어져 있나」를 말하는 표만.
_DEN_GOOD = ("구성내역", "구성 내역", "세부 내역", "세부내역", "종류별 내역", "종류별내역", "내역")
#: 제목에 예치금이 있어도 분모가 아닌 표들. 이걸 안 빼면 신용위험표가 분모로 붙는다.
_DEN_BAD = ("신용위험", "신용건전성", "대손충당금", "변동내역", "현금흐름", "만기",
            "위험", "공정가치", "사용이 제한", "사용제한", "담보")


def is_deposit_breakdown(title: str) -> bool:
    """이 표가 「예치금이 무엇으로 이루어져 있나」인가 — 사용제한을 뺄 **원본**이다."""
    head = (title or "").split("(단위")[0]
    if not head:
        return False
    if not any(k in head for k in ("예치금", "현금및예치금", "현금 및 예치금")):
        return False
    if any(k in head for k in _DEN_BAD):
        return False
    return any(k in head for k in _DEN_GOOD)


def find_denominator(html: str, before: int) -> dict[str, Any] | None:
    """`before` 앞쪽에서 가장 가까운 분모표. 원문이 짝으로 실은 바로 앞 항이다."""
    best = None
    upper = html.upper()
    i = 0
    while True:
        start = upper.find("<TABLE", i)
        if start < 0 or start >= before:
            break
        span = table_span(html, start)
        if not span:
            i = start + 6
            continue
        i = span[1]
        parsed = parse_table(html[span[0]:span[1]], context=caption_before(html, span[0]))
        if parsed["n_numeric"] < 3 or is_statement_table(parsed):
            continue
        title = title_only(parsed["caption"])
        if not is_deposit_breakdown(title):
            continue
        best = {"anchor": "예치금 구성", "kind": "denominator", "pos": span[0],
                "title": title, "axis": axis_of(parsed), "role": "분모",
                "period": period_of(parsed), "account": None,
                "heading": False, "weak": 0, "title_matched": True,
                "body": "|".join(c["text"] for r in parsed["rows"] for c in r),
                **parsed}
    return best

def extract_regions(
        regions: list[tuple[str | None, str]],
        fields: list[str] | None = None,
        sheets: dict[str | None, dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """**구간별 HTML 조각** → 요청한 표들.

    🔴 **문서를 통째로 받지 않는다.** 260824 실측 — NH투자증권 사업보고서는 19.5MB 라
       통째로 읽으면 RSS 가 155MB 늘고 프로덕션 1GB VM 에서 위험하다. DART 목차에는
       주석 **항목마다** 좌표가 있어(「8. 사용이 제한된 예금 등 (연결)」 34KB) 필요한
       절만 받으면 된다. 이 함수는 그렇게 받은 조각들을 그대로 처리한다.

    🔴 **구조 앵커로 구간을 자르고 그 안에서만 찾는다.** 260823 마스터 지시 —
       키워드로 위치를 잡으면 계속 막힌다. `<TITLE AASSOCNOTE="D-0-3-3-0">`(연결 주석)과
       `D-0-3-5-0`(별도 주석)이 문서에 박혀 있고, 6사 실측에서 전부 정확히 갈렸다.
       구간을 쓰면 ①목차·본문 혼동이 없고 ②연결/별도가 위치 계산 없이 확정되며
       ③재무제표 본표가 애초에 안 들어온다.
       앵커가 없는 문서(구형 서식)는 예전 방식으로 폴백한다.
    """
    want = [f for f in (fields or FIELDS) if f in ANCHORS]
    bs_cache: dict[str | None, dict[str, Any] | None] = dict(sheets or {})
    out: dict[str, Any] = {}
    for field in want:
        found: list[dict[str, Any]] = []
        skipped_statement = 0
        skipped_direction = 0
        emitted_bodies: dict[str, dict[str, Any]] = {}
        per_kind: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
        for basis, html in regions:
            for kind in dict.fromkeys(kd for _, kd in ANCHORS[field]):
                kin_kws = [k for k, kd in ANCHORS[field] if kd == kind]
                hits: list[dict[str, Any]] = []
                fallback: dict[str, Any] | None = None
                seen_tables: set[str] = set()
                for kw in kin_kws:
                    if len(hits) >= _MULTI_TABLE_LIMIT:
                        break
                    start, scanned = 0, 0
                    while scanned < _MAX_SCAN and len(hits) < _MULTI_TABLE_LIMIT:
                        pos = html.find(kw, start)
                        if pos < 0:
                            break
                        start = pos + len(kw)
                        scanned += 1
                        hit = _table_for(html, pos)
                        if not hit:
                            continue
                        _, parsed = hit
                        if is_statement_table(parsed):
                            skipped_statement += 1
                            continue
                        # 연결·별도가 같은 제목을 쓰므로 **본문**으로 중복을 판별한다.
                        body = "|".join(c["text"] for r_ in parsed["rows"] for c in r_)
                        if body in seen_tables:
                            continue
                        seen_tables.add(body)
                        if is_wrong_direction(parsed["caption"]):
                            skipped_direction += 1
                            continue
                        if body in emitted_bodies:
                            # 🔴 **한 표가 두 성격에 걸렸다고 말하려면 제목이 실제로 둘 다
                            #    말해야 한다.** 260824 T 5회차 — 메리츠증권 31-1 은 순수
                            #    사용제한 예치금표인데, **주석 제목**(「31. 사용이 제한된
                            #    예치금 및 담보제공자산」)에 담보 앵커가 들어 있어 표까지
                            #    담보로 걸렸고 「성격별로 더하면 두 배」 경고가 나갔다.
                            #    담보는 31-2 로 따로 있어 두 배가 되지 않는다. 3회차 연속
                            #    지적된 위양성이다. **제목 대조를 통과한 때만 표시한다.**
                            if (title_matches(parsed["caption"], kin_kws)
                                    and not is_off_topic(title_only(parsed["caption"]))):
                                emitted_bodies[body].setdefault("also_kinds", []).append(kind)
                            continue
                        entry = {"anchor": kw, "kind": kind, "pos": pos,
                                 "axis": axis_of(parsed),
                                 "title": title_only(parsed["caption"]),
                                 "period": period_of(parsed),
                                 "account": (account_of(parsed["caption"])
                                             if kind in ("restricted", "pledged") else None),
                                 "table_basis": basis,
                                 "heading": is_note_heading(parsed["caption"], kin_kws),
                                 "weak": title_weakness(parsed["caption"]),
                                 "body": body, **parsed}
                        if entry["account"]:
                            entry["account_total"] = lookup_account(
                                bs_cache.get(basis), entry["account"])
                        # 🔴 제목이 **다른 범주**를 말하면 축이 맞아도 다른 표다(260824 T보고).
                        entry["other_field"] = title_says_other_field(entry["title"], field)
                        if (title_matches(parsed["caption"], kin_kws)
                                and not is_off_topic(entry["title"])):
                            entry["title_matched"] = entry["weak"] == 0
                            hits.append(entry)
                            continue
                        if fallback is None and not is_off_topic(entry["title"]):
                            fallback = entry
                bucket = per_kind.setdefault((kind, basis), [])
                bucket.extend(hits)
                # 🔴 등록은 **훑는 중에** 해야 한다. 트림 뒤로 미루면 다음 성격이 같은 표를
                #    다시 물어 메리츠증권 2배 중복이 되살아난다(시험이 잡았다).
                for h in hits:
                    emitted_bodies.setdefault(h["body"], h)
                if not bucket and fallback is not None:
                    fallback["title_matched"] = False
                    bucket.append(fallback)
        # 🔴 **자르는 단위는 구간이 아니라 (성격, 기준) 이다.** 260824 실측 — 목차 노드로
        #    받으면 한 기준이 절 여러 개로 쪼개져 들어오는데, 구간마다 잘랐더니 NH FVPL 이
        #    2표에서 8표로 늘었다. 문서를 통째로 받던 때와 답이 달라진다.
        for (_kind, _basis), bucket in per_kind.items():
            if field != "사용제한" and len(bucket) > 1:
                bucket.sort(key=lambda e: (
                    0 if e.get("heading") else 1,
                    e.get("weak", 0),
                    0 if e.get("axis") == "유형별" else (2 if e.get("axis") == "범주별" else 1),
                    e.get("pos", 0),
                ))
                # 🔴 **원문이 따로 실은 「…, 합계」 표는 버리지 않는다.** 260824 실측 —
                #    NH 「7. 상각후원가측정금융자산」은 세부표(27열)와 합계표(21열)가 짝이다.
                #    합계표에는 손실충당금·현재가치할인차금이 들어 있어 세부표 잎을 더한
                #    값과 대조가 된다(예치금 12,131,887 정확히 일치). 성격·기준당 1표만
                #    남기던 규칙이 **검산 재료를 통째로 버리고 있었다.**
                total_tbl = next((e for e in bucket[1:]
                                  if is_totalish_title(e.get("title", ""))
                                  and e.get("period") != "전기"), None)
                bucket = bucket[:1]
                if total_tbl is not None:
                    total_tbl["role"] = "합계"
                    bucket.append(total_tbl)
            elif len(bucket) > _MULTI_TABLE_LIMIT:
                bucket = bucket[:_MULTI_TABLE_LIMIT]
            found.extend(bucket)
        # 🔴 **뺄 금액만 주고 뺄 원본을 안 주면 계산이 안 된다.** 260824 T 5회차 —
        #    6사 중 5사가 여기서 막혔다. 사용제한 표 바로 앞 항의 「예치금 구성내역」을
        #    같은 주석에서 찾아 짝으로 싣는다. 재무상태표에서 가져오지 않는 이유 둘 —
        #    ①은행·증권은 「현금및예치금」으로 현금과 묶여 있어 분모가 부푼다
        #    ②재무상태표와 주석의 **단위가 다를 수 있다**(메리츠: 원 vs 천원).
        if field == "사용제한" and found:
            seen_den: set[str] = set()
            dens = []
            for basis, html in regions:
                first = min((e["pos"] for e in found
                             if e.get("table_basis") == basis), default=None)
                if first is None:
                    continue
                den = find_denominator(html, first)
                if den and den["body"] not in seen_den:
                    seen_den.add(den["body"])
                    den["table_basis"] = basis
                    dens.append(den)
            found.extend(dens)
        _order = {"연결": 0, "별도": 1}
        found.sort(key=lambda e: (_order.get(e.get("table_basis"), 2), e["pos"]))
        if found:
            out[field] = {"status": OK, "tables": found}
            notes = []
            if skipped_statement:
                notes.append(f"재무제표 본표 {skipped_statement}건을 건너뛰었다")
            if skipped_direction:
                notes.append(f"**받은 담보·특수관계자 표 {skipped_direction}건을 버렸다** — "
                             f"회사가 제공한 담보가 아니라 부호가 거꾸로다")
            if notes:
                out[field]["note"] = " / ".join(notes)
        else:
            out[field] = {"status": NOT_APPLICABLE, "tables": [],
                          "note": ("이 문서에서 해당 주석을 찾지 못했다 — 회사가 다른 이름으로 "
                                   "공시하거나 그 주석이 없다")}
    _mark_shared_tables(out)
    return out


def _mark_cross_field(out: dict[str, Any]) -> None:
    for field, res in out.items():
        tables = res.get("tables") or []
        # 이 필드가 제대로 유형별을 물었으면 안내할 것이 없다
        if any(t.get("axis") == "유형별" and t.get("title_matched") for t in tables):
            continue
        kws = [k for k, _ in ANCHORS.get(field, [])]
        for other, ores in out.items():
            if other == field:
                continue
            for t in ores.get("tables") or []:
                if t.get("axis") != "유형별":
                    continue
                if any(kw in t.get("body", "") for kw in kws):
                    res["see_field"] = other
                    break
            if res.get("see_field"):
                break


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


# ── 격자 읽기: 열 경로와 검산 ────────────────────────────────────────────────
#
# 🔴 **왜 필요한가 (260824 NH투자증권).** 「7. 상각후원가측정금융자산」은 머리 8행 ×
#    값 1행짜리 **전치표**다. 열 이름이 4단으로 쌓여 있고 값은 한 줄에 26개가 늘어선다.
#    표를 물리 행 그대로 내보내면 읽는 쪽은 27열 정렬을 손으로 복원해야 하는데,
#    실제로 시험자도 렌더 UI 도 **둘 다 틀렸다**(예치금 소계를 12,731,887 로 냈다.
#    원문 「…, 합계」 표의 값은 12,131,887 이다).
#    격자를 편 다음 **값마다 열 경로를 붙여** 내보내면 복원할 것이 없어진다.

_TOTALISH = ("합계", "총계", "소계", "계")

#: 값이 아니라 **행 이름**을 이고 있는 머리말. 이런 열은 이름칸이다.
_STUB_LABELS = ("구분", "항목", "과목", "내역", "계정", "계정과목", "분류", "종류", "자산")

#: 음수 표기 — 회계는 △·▲·괄호를 쓴다. 셋 다 마이너스다.
_NEG = ("△", "▲", "-")


def to_number(text: str) -> float | None:
    """표의 칸 하나 → 수. 셀 수 없으면 None. **검산에만 쓴다.**"""
    t = (text or "").strip().replace(",", "").replace("　", "")
    if not t:
        return None
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg, t = True, t[1:-1].strip()
    for mark in _NEG:
        if t.startswith(mark):
            neg, t = True, t[len(mark):].strip()
            break
    if not t or not t.replace(".", "", 1).isdigit():
        return None
    v = float(t)
    return -v if neg else v


def _fmt(v: float) -> str:
    n = round(v)
    s = f"{abs(n):,}"
    return f"({s})" if n < 0 else s


def is_totalish(label: str) -> bool:
    t = (label or "").replace(" ", "")
    return any(t == k or t.endswith(k) for k in _TOTALISH)


def header_depth(grid: list[list[dict[str, Any]]]) -> int:
    """머리가 몇 행인가. `<TH>` 가 있으면 그것을 믿고, 없으면 숫자가 없는 앞머리로 본다."""
    if any(c.get("th") for r in grid for c in r):
        n = 0
        for r in grid:
            if not all(c.get("th") for c in r):
                break
            n += 1
        return n
    n = 0
    for r in grid:
        if any(_NUMISH.match(c["text"]) for c in r):
            break
        n += 1
    return min(n, len(grid) - 1) if len(grid) > 1 else 0


def column_view(parsed: dict[str, Any]) -> dict[str, Any] | None:
    """격자 → **열 경로 × 값**. 값을 만들지 않는다 — 같은 칸을 다르게 놓을 뿐이다.

    돌려주는 것
      `common`  모든 값열이 똑같이 이고 있는 머리 (한 번만 쓴다)
      `columns` [{path: [...], leaf: str, group: str, values: [str, ...]}]
      `rows`    본문 행 이름 (values 의 순서와 같다)
    """
    grid = parsed.get("grid") or []
    if not grid or parsed.get("ragged"):
        return None
    n_cols = len(grid[0])
    depth = header_depth(grid)
    body = grid[depth:]
    if depth < 1 or not body or n_cols < 2:
        return None

    # 앞쪽 「이름칸」 — 머리가 통째로 빈 열(NH 전치표)이거나, 「구분」처럼 **이름표 구실을
    # 하는 말**이 붙고 본문이 숫자가 아닌 열(KB손보)이다.
    # 🔴 **「본문이 숫자가 아니면 이름칸」으로 잡으면 안 된다.** 260824 실측 — NH 합계표는
    #    값이 비어 있는 열이 앞에 둘 있는데(이연부대손익·현재가치할인차금) 그걸 이름칸으로
    #    먹어버려 예치금 묶음이 잎 2개짜리가 됐고, 측정 축 판별이 깨져 뜻 없는 합
    #    12,124,877(= 12,131,887 + (7,010)) 이 검산으로 나갔다. **이름이 붙어 있으면 값열이다.**
    stub = 0
    while stub < n_cols - 1:
        path = [grid[r][stub]["text"] for r in range(depth) if grid[r][stub]["text"]]
        if not path:
            stub += 1
            continue
        # 🔴 띄어쓰기가 흔들린다 — 우리은행은 「구 분」·「구  분」 이다. 붙여서 견준다.
        if (path[-1].replace(" ", "") in _STUB_LABELS
                and all(not _NUMISH.match(r[stub]["text"]) for r in body)):
            stub += 1
            continue
        break
    if stub >= n_cols:
        return None

    cols = []
    for c in range(stub, n_cols):
        path: list[str] = []
        for r in range(depth):
            t = grid[r][c]["text"]
            if t and (not path or path[-1] != t):
                path.append(t)
        cols.append({"path": path, "col": c})
    if not cols:
        return None

    # 모든 값열이 공유하는 머리는 한 번만 쓴다 (「금융자산의 범주」처럼 26열 전부에 붙는 것)
    common = [p for p in cols[0]["path"]
              if all(p in cc["path"] for cc in cols)]
    for cc in cols:
        rest = [p for p in cc["path"] if p not in common]
        cc["leaf"] = rest[-1] if rest else (cc["path"][-1] if cc["path"] else "")
        cc["group"] = " › ".join(rest[:-1])
        cc["label"] = " › ".join(rest) or cc["leaf"]

    names = []
    for r in body:
        parts: list[str] = []
        for i in range(stub):
            x = r[i]["text"]
            # 병합 칸을 폈으니 같은 글자가 이어 나온다 — 「합계 합계」가 되지 않게 한 번만.
            if x and (not parts or parts[-1] != x):
                parts.append(x)
        names.append(" ".join(parts))
    for cc in cols:
        cc["values"] = [r[cc["col"]]["text"] for r in body]
    return {"common": common, "columns": cols, "rows": names,
            "n_cols": n_cols, "depth": depth}


def checksums(view: dict[str, Any] | None, transposed: bool = True) -> list[dict[str, Any]]:
    """열 묶음마다 잎을 더해 본다. **원문에 합계가 있으면 대조하고, 없으면 더한 값을 적는다.**

    🔴 이 도구는 값을 만들지 않는 것이 계약이다. 그래서 여기서 나오는 수는 전부
       `source` 로 출처를 밝힌다 — `원문` 인지 `도구가 더함` 인지.
       260824 마스터 지시로 넣었다. 정렬이 어긋나면 합이 원문 합계와 안 맞으므로,
       **검산은 정렬이 맞았다는 증거이기도 하다.**
    """
    # 🔴 **보통 표에서 열을 더하면 안 된다.** 260824 실측 — KB손해보험 사용제한표는
    #    열이 「당반기말 · 전기말」이라 더하면 44+44=88 처럼 **시점이 다른 값을 합친
    #    무의미한 수**가 나온다. 열을 더해도 되는 것은 열이 **항목**인 전치표뿐이다.
    #    보통 표의 검산은 `row_checksums` — 「합계」 **행**을 나머지 행과 맞춘다.
    if not view or not transposed:
        return []
    out: list[dict[str, Any]] = []
    groups: dict[str, list[dict[str, Any]]] = {}
    for cc in view["columns"]:
        groups.setdefault(cc["group"], []).append(cc)

    # 🔴 **잎이 항목이 아니라 「측정 축」이면 더하면 안 된다.** 260824 실측 — NH 합계표는
    #    묶음마다 잎이 (이연부대손익 · 현재가치할인차금 · 손실충당금 반영 전 장부금액 ·
    #    손상차손누계액) 로 **똑같다.** 이건 항목이 넷인 게 아니라 **같은 항목의 측정값이
    #    넷**이라는 뜻이고, 더하면 뜻 없는 수가 나온다(12,124,877 같은).
    #    판별은 간단하다 — **다른 묶음과 잎 이름이 같으면 축이다.**
    shapes: dict[tuple[str, ...], int] = {}
    for gname, members in groups.items():
        shapes[tuple(m["leaf"] for m in members)] = shapes.get(
            tuple(m["leaf"] for m in members), 0) + 1

    for gname, members in groups.items():
        if len(groups) > 1 and shapes.get(tuple(m["leaf"] for m in members), 0) > 1:
            continue                       # 측정 축 — 더하지 않는다
        leaves = [m for m in members if not is_totalish(m["leaf"])]
        totals = [m for m in members if is_totalish(m["leaf"])]
        if len(leaves) < 2:
            continue
        for i, rowname in enumerate(view["rows"]):
            vals = [to_number(m["values"][i]) for m in leaves]
            got = [v for v in vals if v is not None]
            if len(got) < 2:
                continue
            s = sum(got)
            rec = {"group": gname or "(전체)", "row": rowname, "n": len(got),
                   "sum": _fmt(s)}
            if totals:
                tv = to_number(totals[0]["values"][i])
                if tv is not None:
                    rec["stated"] = _fmt(tv)
                    rec["ok"] = abs(tv - s) < 0.5
            out.append(rec)
    return out


def row_checksums(view: dict[str, Any] | None) -> list[dict[str, Any]]:
    """본문에 「합계」 **행**이 있으면 나머지 행을 더해 맞춰 본다.

    보통 표(행=항목 · 열=시점)의 올바른 검산은 이쪽이다. 260824 실측 —
    KB손해보험 사용제한: 44 + 14,963 + 11,349 = 26,356 (원문 합계와 일치),
    담보제공: 1,754,354 + 92,383 + 3,723 = 1,850,460 (일치).
    **합계 행이 여럿이면(합계와 소계가 같이 있으면) 손대지 않는다** — 무엇을 무엇과
    맞춰야 하는지 표마다 다르고, 틀린 검산은 검산이 없는 것보다 나쁘다.
    """
    if not view:
        return []
    names = view["rows"]
    tot = [i for i, n in enumerate(names) if is_totalish(n)]
    if not tot or len(names) < 3:
        return []
    # 🔴 **소계가 섞여 있어도 검산은 된다.** 260824 실측 — 국민은행·신한은행 예치금
    #    구성내역은 원화 소계 · 외화 소계 · 합계 셋이다. 예전에는 「합계 행이 하나일
    #    때만」이라 이런 표를 통째로 건너뛰었다. **마지막 합계 행을 총계로 보고,
    #    소계 행은 더하는 쪽에서 뺀다** — 안 그러면 두 번 세어 반드시 어긋난다.
    ti = tot[-1]
    others = [i for i in range(len(names)) if i not in set(tot)]
    if len(others) < 2:
        return []
    # 🔴 **계층 표에서 부모 행을 같이 더하면 두 번 센다.** 260824 실측 — 국민은행
    #    「계정별 장부금액 및 공정가치」는 대분류(당기손익-공정가치 측정 금융자산
    #    26,697,998) 밑에 채무증권·지분증권·대출채권·기타가 달려 있다. 그대로 더하면
    #    22행 합 1,276,111,656 대 원문 585,258,157 — **정확히 두 배 가까이 어긋나고
    #    🔴 위양성이 난다.** 값으로 부모를 찾아 뺀다(뒤따르는 연속 행의 합과 같은 행).
    others = [i for i in others if i not in _parent_rows(view, others)]
    if len(others) < 2:
        return []
    out: list[dict[str, Any]] = []
    for cc in view["columns"]:
        stated = to_number(cc["values"][ti])
        if stated is None:
            continue
        got = [v for v in (to_number(cc["values"][i]) for i in others) if v is not None]
        if len(got) < 2:
            continue
        s = sum(got)
        out.append({"column": cc["label"] or cc["leaf"], "n": len(got), "sum": _fmt(s),
                    "stated": _fmt(stated), "ok": abs(stated - s) < 0.5,
                    "row": names[ti]})
    return out


def numeric_total(view: dict[str, Any] | None, drop_totals: bool = True) -> float | None:
    """표 안 값의 총합. 표끼리 「같은 자산인가」를 묻는 데만 쓴다 — 내보내는 수가 아니다.

    합계 행·열을 빼는 쪽과 넣는 쪽을 둘 다 쓴다. 어느 쪽이 맞는지는 표마다 다르기
    때문이다 — 현대해상 범주별 요약표는 **열 이름 자체가 「금융자산의 종류 합계」**라
    빼고 나면 값이 하나만 남는다(그러면 총합이 안 나온다).
    """
    if not view:
        return None
    tot = 0.0
    n = 0
    for i, name in enumerate(view["rows"]):
        if drop_totals and is_totalish(name):
            continue
        for cc in view["columns"]:
            if drop_totals and is_totalish(cc["leaf"]):
                continue
            v = to_number(cc["values"][i])
            if v is not None:
                tot += v
                n += 1
    return tot if n >= 1 else None


def totals_of(view: dict[str, Any] | None) -> set[float]:
    """그 표가 「전체 얼마짜리 표인가」의 후보들. 합계를 빼고/넣고 둘 다."""
    out = {numeric_total(view, True), numeric_total(view, False)}
    return {v for v in out if v is not None and v != 0}


def same_assets(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    """두 표가 **같은 자산을 다르게 자른 것**인가 — 총합이 같으면 그렇다.

    🔴 260824 T보고 — 현대해상 담보제공은 표가 둘인데 하나는 **범주별 요약**,
       하나는 **유형별 세부**다. 둘 다 연결 기준이라 「기준이 같으면 더해도 된다」는
       예전 안내를 따르면 **정확히 2배**가 된다. 실측 총합이 양쪽 다
       2,310,215,724 천원으로 같다 — 그러면 같은 자산이다.
    """
    ta, tb = totals_of(a), totals_of(b)
    for x in ta:
        for y in tb:
            if abs(x - y) <= max(1.0, abs(x) * 1e-9):
                return True
    return False


def _parent_rows(view: dict[str, Any], rows: list[int]) -> set[int]:
    """값으로 「대분류 행」을 찾는다 — 바로 뒤 연속 행들의 합과 같은 행.

    제목만 봐서는 대분류인지 알 수 없다(들여쓰기가 HTML 에 안 남는 경우가 많다).
    숫자가 말해 준다. ⚠️ **우연히 맞아떨어지는 행도 부모로 잡힌다** — 그래서 이 판정은
    검산을 **돌려 보기 위한 보정**에만 쓰고, 결과가 안 맞을 때 「도구가 틀렸다」고
    말하지 않는다(⚠️ 로만 적는다). 기준 열은 숫자가 가장 많이 든 열 하나만 쓴다 — 열마다 따로
    판정하면 같은 행이 어떤 열에서는 부모, 어떤 열에서는 잎이 되어 뒤죽박죽이 된다.
    """
    best, best_n = None, 0
    for cc in view["columns"]:
        vals = [to_number(cc["values"][i]) for i in rows]
        n = sum(1 for v in vals if v is not None)
        if n > best_n:
            best, best_n = vals, n
    if not best or best_n < 4:
        return set()
    parents: set[int] = set()
    for a in range(len(best)):
        top = best[a]
        if top is None or top <= 0:
            continue
        acc = 0.0
        for b in range(a + 1, len(best)):
            v = best[b]
            if v is None or v < 0:
                break
            acc += v
            if b > a and abs(acc - top) < 0.5:
                parents.add(rows[a])
                break
            if acc > top + 0.5:
                break
    return parents
