"""정정 공시의 「정정항목 · 정정전 · 정정후」 표를 **원문 그대로** 옮긴다.

정정 공시는 머리에 회사가 직접 쓴 비교표를 붙인다. 서식이 두 갈래다 —

    정정항목 | 정정전 | 정정후                    거래소 공시(수주·배당·주총소집·투자판단·잠정실적 …)
    항 목 | 정정사유 | 정 정 전 | 정 정 후       주요사항보고서·공고(자기주식·증자·CB·대량보유 …)

그 표를 칸 그대로 옮기면 「무엇이 무엇에서 무엇으로 바뀌었나」를 우리가 지어낼 자리가 없다.
도구들은 최신 정정본만 골라 읽어서(`last_reprt_at='Y'`) **바뀐 내용 자체가 응답에서 사라졌고**,
수주(`order_contracts._correction_diff`)·잠정실적·소집공고(`parse_corrections_xml`)만 자기 서식에
맞춘 비교를 따로 갖고 있었다. 이 모듈이 유형을 가리지 않는 공용 판이다.

규칙은 Agent D(공시알리미 `services/dart-push/amend_card.py`)에서 옮겨 왔다 — 정정 갈무리
956건 중 886건(93%)에서 이 표를 읽었다. **모델을 부르지 않는다.**

**평평한 글로는 두 열을 못 가른다** — `호주` 와 `호주, 뉴질랜드` 사이에 경계가 없다. 그래서 이 모듈은
`get_document_cached()` 의 `html`(태그째 원문)을 읽는다. 입력이 문서 하나라 **DART 콜은 0**이다 —
문서를 받는 것은 부르는 쪽이다.
"""

from __future__ import annotations

import html as _html
import re
from typing import Any

_TR = re.compile(r"(?is)<tr\b[^>]*>(.*?)</tr>")
# 🔴 **칸 태그가 넷이다.** 거래소 서식(XForms HTML)은 `td`, 주요사항보고서 XML 은 머리글이 `th` 로,
# 값 칸이 `te`(입력 칸)·`tu`(단위 칸)로 오기도 한다. 하나만 보면 그 서식의 표가 통째로 안 읽힌다.
_CELL = re.compile(r"(?is)<(t[dheu])\b([^>]*)>(.*?)</\1\s*>")
_BR = re.compile(r"(?is)<br\b[^>]*>")
_TAG = re.compile(r"<[^>]+>")
_ROWSPAN = re.compile(r"(?i)\browspan\s*=\s*[\"']?(\d+)")
_KDATE = re.compile(r"(\d{4})\s*[년.\-/]\s*(\d{1,2})\s*[월.\-/]\s*(\d{1,2})")

#: 카드·요약에서 싣는 줄 상한. 넘친 줄은 버리지 않고 몇 줄이 더 있는지 센다.
CARD_ROWS_MAX = 8

STATUS_NOTE = {
    "parsed": "",
    "no_changes": "정정 비교표는 있는데 바뀐 칸이 없다 — 값을 각주(주1·주2)나 본문 뒤쪽에 적었을 수 있다. 원문을 직접 볼 것.",
    "reason_only": "정정사유만 있고 비교표가 없다 — 첨부만 바꾼 정정([첨부정정])이거나 본문을 통째로 다시 낸 정정이다.",
    "table_absent": "정정 비교표를 찾지 못했다 — 서식이 다르거나 이미지일 수 있다. 원문 링크로 확인할 것.",
}


def _compact(s: str) -> str:
    """견줄 때만 쓴다 — 머리글이 `정 정 전` 처럼 글자 사이가 벌어져 온다."""
    return re.sub(r"\s+", "", s or "")


def _cell_text(frag: str) -> str:
    """표 한 칸 → 글. **`<br>` 은 줄바꿈으로 살린다** — 한 칸에 여러 값을 눌러 담는 회사가 있다.

    실체를 먼저 풀고 공백을 접는다. 거꾸로 하면 `&nbsp;` 가 접힌 **뒤에** `\\xa0` 로 풀려
    칸 앞머리에 남는다(Agent D 2026-08-28 — 앞에서부터 맞추는 검사가 통째로 어긋났다).
    """
    text = _html.unescape(_TAG.sub(" ", _BR.sub("\n", frag or "")))
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def _iso_date(s: str) -> str | None:
    m = _KDATE.search(s or "")
    if not m:
        return None
    return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))


def _expand_row(cells: list[tuple[str, str]], carries: dict[int, list], ncols: int) -> list[str] | None:
    """세로로 합친 칸(`rowspan`)을 아랫줄에 다시 채워 칸 수를 맞춘다. 못 맞추면 `None`(표 끝).

    🔴 주요사항보고서 정정표는 **사유 칸 하나가 여러 항목에 걸치는** 일이 있다 — 그 아랫줄은 칸이
    하나 모자라 들어온다. 칸 수만 세면 표가 거기서 끊긴 것으로 읽혀 나머지 항목을 통째로 잃는다.
    """
    row: list[str] = []
    pending = iter(cells)
    for col in range(ncols):
        carry = carries.get(col)
        if carry and carry[1] > 0:
            row.append(carry[0])
            carry[1] -= 1
            continue
        nxt = next(pending, None)
        if nxt is None:
            return None
        attrs, frag = nxt
        row.append(frag)
        m = _ROWSPAN.search(attrs)
        if m and int(m.group(1)) > 1:
            carries[col] = [frag, int(m.group(1)) - 1]
    if next(pending, None) is not None:
        return None
    return row


def _unmerge(item: str, before: str, after: str) -> list[tuple[str, str, str]]:
    """한 줄에 눌러 담은 항목을 **항목마다 한 줄로 편다.**

    셋이 같은 줄 수로 갈릴 때만 편다. 이름 하나에 값이 여러 줄인 칸(`당해실적: … / 누계실적: …`)은
    항목이 하나뿐이라 줄 수가 안 맞고, 그때는 칸을 통째로 둔다 — 억지로 짝지으면 없는 항목 이름을
    우리가 지어내게 된다(Agent D 2026-09-09 특수건설 `20260909900207`).
    """
    cols = [item.split("\n"), before.split("\n"), after.split("\n")]
    n = len(cols[0])
    if n > 1 and len(cols[1]) == n and len(cols[2]) == n:
        return list(zip(*cols))
    return [(item, before, after)]


# 값이 아니라 각주를 가리키기만 하는 칸 — 회사가 칸에 `주1) 정정 전` · `주2) 정정 후` 라고만 쓰고
# 실제 내용은 표 밖에 적는다(Agent D 2026-09-04 전환사채 정정). 옮겨도 아무것도 말하지 못한다.
_POINTER = re.compile(r"^(주\d*[).]?)?정정(전|후)$")
_BLANK_WORDS = frozenset({"해당사항없음", "해당없음"})
# 소제목 번호 — `1. 연결실적내용` 은 큰 묶음, `당기실적(26.2Q)`·`전기대비 증감율(%)` 은 그 안의 작은 묶음.
_TOP_HEADING = re.compile(r"^\s*\d+\s*[.)]")


def _is_blank(value: str) -> bool:
    """칸이 「값 없음」인가. 회사마다 비었다는 표시가 다르다.

    이 판정 하나가 세 곳을 정한다 —
      ① 전이 비고 후가 차면 ``added``, 전이 차고 후가 비면 ``deleted``(안건 철회·항목 삭제)
      ② 전·후가 **둘 다** 비면 그 줄은 바뀐 칸이 아니라 **소제목**이다(`1. 연결실적내용 | - | -`).
         아래 항목에 `section` 으로 붙는다 — 빼 버리면 금액 줄과 증감율 줄이 같은 이름
         (`- 매출액(당해실적)`)으로 섞인다(셀트리온 `20260727800078` 실측).
      ③ 그 밖은 둘 다 값이 있는 줄 — 같으면 ``unchanged``, 다르면 ``changed``.

    `value` 는 칸 글 그대로다(줄바꿈 `\\n` 포함, 앞뒤 공백 없음). 실제로 오는 꼴:
    빈칸 · `-` · `- -`(칸을 둘로 나눈 서식) · `－`(전각) · `해당사항 없음` · `해당없음`.
    `0` · `0원` 은 **값**이다(0 으로 정정한 것은 소식이다).
    """
    # `미정`·`추후 확정` 은 **값**으로 둔다 — `미정 → 2026-09-01` 은 「정해졌다」는 소식이라
    # added 가 아니라 changed 로 읽혀야 한다. 넓히는 것은 실측 꼴이 나왔을 때만.
    compact = _compact(value)
    return compact.strip("-－–—") == "" or compact in _BLANK_WORDS


def _classify_change(before: str, after: str) -> str:
    """정정 표 한 줄의 변화 이름 — ``changed`` · ``added`` · ``deleted`` · ``unchanged`` · ``pointer``."""
    if _POINTER.match(_compact(after)) or _POINTER.match(_compact(before)):
        return "pointer"
    b_blank, a_blank = _is_blank(before), _is_blank(after)
    if b_blank and a_blank:
        return "unchanged"
    if b_blank:
        return "added"
    if a_blank:
        return "deleted"
    return "unchanged" if _compact(before) == _compact(after) else "changed"


def parse_amendment_table(html: str) -> dict[str, Any]:
    """정정 원문(태그째) → 정정 비교표.

    반환:
      - ``status``  ``parsed`` 바뀐 칸 ≥1 / ``no_changes`` 표는 있는데 바뀐 칸 0 /
        ``reason_only`` 표 없이 정정사유만 / ``table_absent`` 아무것도 못 찾음
      - ``reason`` 정정사유(표 위 칸, 없으면 줄별 사유의 첫 값) · ``target_document`` 정정 대상 서류 ·
        ``original_filed`` 최초 제출일 원문 글자 · ``original_filed_date`` 그 날짜(YYYY-MM-DD, 못 읽으면 None)
      - ``rows`` ``[{item, before, after, change, section?, reason?}]`` — **칸 글 그대로**(번호·단위·줄바꿈 포함).
        ``section`` 은 그 위의 소제목 줄(전·후가 다 빈 줄)이다 — 같은 항목 이름이 묶음마다 되풀이된다
      - ``row_count`` · ``skipped`` ``{unchanged, pointer}`` 뺀 줄 수 · ``tables`` 만난 비교표 수 · ``note`` 다음에 볼 곳
    """
    reason = target = filed = None
    rows: list[dict[str, Any]] = []
    skipped = {"unchanged": 0, "pointer": 0}
    tables = 0
    ncols = bi = ai = wi = 0             # 칸 수 · 정정전 · 정정후 · 줄별 사유 자리(0 = 없음)
    carries: dict[int, list] = {}
    top = sub = ""                       # 지금 걸려 있는 소제목(큰 묶음 · 작은 묶음)

    for tr in _TR.findall(html or ""):
        raw = [(m.group(2), m.group(3)) for m in _CELL.finditer(tr)]
        if ncols:
            frags = _expand_row(raw, carries, ncols)
            if frags is not None:
                cells = [_cell_text(f) for f in frags]
                row_reason = " ".join(cells[wi].split()) if wi else ""
                if wi and not reason and row_reason:
                    reason = row_reason
                for item, before, after in _unmerge(cells[0], cells[bi], cells[ai]):
                    if item and _is_blank(before) and _is_blank(after):
                        if _TOP_HEADING.match(item):
                            top, sub = item, ""
                        else:
                            sub = item
                        continue
                    if _TOP_HEADING.match(item):
                        top = sub = ""       # 번호 붙은 값 줄(`4. 기타 …`)은 새 큰 묶음 — 앞 소제목을 닫는다
                    kind = _classify_change(before, after)
                    if kind in skipped:
                        skipped[kind] += 1
                        continue
                    row = {"item": item, "before": before, "after": after, "change": kind}
                    section = " > ".join(s for s in (top, sub) if s)
                    if section:
                        row["section"] = section
                    if row_reason:
                        row["reason"] = row_reason
                    rows.append(row)
                continue
            ncols = 0                    # 칸 수가 안 맞는다 — 표가 끝났다. 이 줄은 아래에서 다시 본다
            carries = {}
            top = sub = ""

        cells = [_cell_text(f) for _, f in raw]
        flat = [_compact(c) for c in cells]
        if len(cells) == 2 and cells[1]:
            head = flat[0]
            if "정정사유" in head and reason is None:
                reason = " ".join(cells[1].split())
            elif "제출일" in head and filed is None:
                filed = " ".join(cells[1].split())
            elif ("정정대상" in head or "정정관련공시서류" in head) and target is None:
                target = " ".join(cells[1].split())
        # 머리글 줄 — `정정전` 바로 다음 칸이 `정정후` 인 자리. 칸 수를 세는 것보다 서식을 덜 탄다.
        for i in range(len(flat) - 1):
            if "정정전" in flat[i] and "정정후" in flat[i + 1]:
                ncols, bi, ai = len(cells), i, i + 1
                wi = next((j for j in range(1, i) if "정정사유" in flat[j]), 0)
                tables += 1
                break

    if rows:
        status = "parsed"
    elif tables:
        status = "no_changes"
    elif reason:
        status = "reason_only"
    else:
        status = "table_absent"
    return {
        "status": status,
        "reason": reason,
        "target_document": target,
        "original_filed": filed,
        "original_filed_date": _iso_date(filed) if filed else None,
        "rows": rows,
        "row_count": len(rows),
        "skipped": skipped,
        "tables": tables,
        "note": STATUS_NOTE[status],
    }


def card_summary(parsed: dict[str, Any], max_rows: int = CARD_ROWS_MAX) -> dict[str, Any]:
    """카드에 실을 몫. 줄은 `max_rows` 까지 싣고 **넘친 줄 수를 남긴다** — 조용히 자르지 않는다."""
    out = {k: v for k, v in parsed.items() if v not in (None, "", [], {}) and k != "skipped"}
    rows = parsed.get("rows") or []
    if len(rows) > max_rows:
        out["rows"] = rows[:max_rows]
        out["rows_truncated"] = len(rows) - max_rows
    return out
