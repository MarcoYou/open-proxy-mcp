"""Parsers for DART shareholder-meeting result disclosures.

주총결과 공시는 서식이 둘이다.

- **표 서식** — 「주주총회 안건 세부내역」 표(번호·결의구분·회의목적사항·가결여부·찬성률·비고).
  `parse_agm_result_table`.
- **줄글 서식** — 「1. 결의사항」 아래에 `제1호 의안 : … → 원안대로 승인` 처럼 적는다.
  `parse_agm_result_summary`. 표가 비었을 때만 부른다.

두 서식 모두 결과 칸의 **원문 문구**를 지우지 않는다. `passed` 는 정규화 값이고,
줄글 서식의 원문 결과문은 `passed_raw` 에 그대로 남는다.
"""

import re


def _classify_approval_base(label: str) -> str:
    """찬성률 분모가 「의결권 있는 주식」인지 「발행주식총수」인지 가른다.

    DART 주주총회결과 표의 첫 찬성률 열 머리는 회사마다 다르게 적힌다.
    태광산업은 「의결권 있는 발행주식 총수 기준(1)」 — 자사주가 빠진 모수다.
    이 구분을 잃으면 자사주 비중이 큰 회사에서 참석률이 모수를 넘어선다(183.3% 사고).
    """
    text = re.sub(r"\s+", "", label or "")
    if not text:
        return "unknown"
    if "의결권" in text:
        return "voting"
    if "발행주식" in text:
        return "issued"
    return "unknown"


def _approval_base_label(rows, data_start: int) -> str:
    """찬성률(1) 열의 머리글 원문을 header/subheader에서 찾아 돌려준다."""
    for row in rows[:data_start]:
        cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
        for cell in cells:
            if "기준" in cell and ("의결권" in cell or "발행주식" in cell):
                return cell
    return ""


# 가결 행의 비고가 결과와 어긋나는 낱말. 원문에 글자 사이 공백이 끼어 온다(「무 효처리」).
# 「부결」은 가결여부 칸이 이미 말하고, 「사임」은 분리선출·임기 개시 안내 같은 정상 문구에
# 흔히 나와(「독립이사가 사임 후 …」, 「사임일 익일 임기 개시」) 넣지 않는다.
_REMARK_VOID = re.compile(r"무\s*효|철\s*회|취\s*소|미\s*취\s*임")


def _remark_contradicts_pass(passed: str, remark: str) -> bool:
    """가결(수정가결 포함) 행의 비고가 무효·철회·취소·미취임을 말하는가."""
    outcome = re.sub(r"\s+", "", passed or "")
    if "가결" not in outcome or "부결" in outcome:
        return False
    return bool(_REMARK_VOID.search(remark or ""))


def parse_agm_result_table(soup) -> list[dict]:
    """Parse the standard agenda-by-agenda voting result table."""
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 3:
            continue
        header = " ".join(c.get_text(strip=True) for c in rows[0].find_all(["td", "th"]))
        if "번호" not in header or "가결여부" not in header:
            continue

        data_start = 1
        subheader = " ".join(c.get_text(strip=True) for c in rows[1].find_all(["td", "th"]))
        if "찬성률" in subheader:
            data_start = 2

        base_label = _approval_base_label(rows, data_start)
        approval_base = _classify_approval_base(base_label)

        items = []
        for row in rows[data_start:]:
            cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
            cells = [re.sub(r"\s+", " ", c) for c in cells]
            if len(cells) < 5 or not cells[0] or cells[0] == "-":
                continue
            try:
                issued = float(cells[4]) if len(cells) > 4 and cells[4] else 0
                voted = float(cells[5]) if len(cells) > 5 and cells[5] else 0
                attendance = round(issued / voted * 100, 1) if voted > 0 else None
            except (ValueError, ZeroDivisionError):
                attendance = None
            passed = cells[3] if len(cells) > 3 else ""
            # 8번째 칸부터는 비고다. 「-」 은 빈 칸이다. 원문 그대로 싣는다.
            remark = " ".join(c for c in cells[7:] if c).strip()
            if remark in {"-", "–", "—"}:
                remark = ""
            items.append({
                "number": cells[0],
                "resolution_type": cells[1] if len(cells) > 1 else "",
                "agenda": cells[2] if len(cells) > 2 else "",
                "passed": passed,
                "passed_raw": passed,
                "approval_rate_issued": cells[4] if len(cells) > 4 else "",
                "approval_rate_voted": cells[5] if len(cells) > 5 else "",
                "opposition_rate": cells[6] if len(cells) > 6 else "",
                "estimated_attendance": attendance,
                # 참석률의 분모가 무엇인지 — 자사주 포함 발행총수인지, 의결권 있는 주식인지.
                "approval_base": approval_base,
                "approval_base_label": base_label,
                # 비고 칸 원문. 가결인데 비고가 무효·미취임을 말하면 결과 칸만으로는 틀리게 읽힌다
                # (가결 8건 중 1건이 「후보자 사퇴로 인한 미취임으로 무효처리」였던 사례).
                "remark": remark,
                "remark_contradicts_pass": _remark_contradicts_pass(passed, remark),
            })
        if items:
            return items
    return []


# 「3. 기타 투자판단과 관련한 중요사항」 구역. 다음 머리(관련공시·안건 세부내역)까지.
_ETC_SECTION = re.compile(
    r"기타\s*투자\s*판단[^※【\[]{0,14}?사항\s*(.*?)(?=※\s*관련\s*공시|[【\[]\s*주주총회\s*안건|$)"
)
# 결의가 효력을 잃었다는 문장만 옮긴다. 합병 해제 같은 **일반 조건문**
# (「철회되는 경우 … 효력은 발생하지 않습니다」)은 걸리지 않는다.
_ETC_VOID = re.compile(r"무\s*효\s*처\s*리|효\s*력\s*이\s*발생하지\s*아\s*니")


def parse_agm_result_void_notice(soup_or_text) -> str:
    """「기타 투자판단」 구역에서 결의 무효처리를 알리는 문장만 원문 그대로 돌려준다.

    없으면 빈 문자열. 판정하지 않고 원문 낱말만 옮긴다.
    """
    if soup_or_text is None:
        return ""
    raw = soup_or_text if isinstance(soup_or_text, str) else soup_or_text.get_text(" ", strip=True)
    text = " ".join((raw or "").split())
    match = _ETC_SECTION.search(text)
    if not match:
        return ""
    hits: list[str] = []
    for chunk in re.split(r"(?:^|\s)-\s*(?=\S)", match.group(1)):
        for sentence in re.split(r"(?<=[다요음임함]\.)\s+", chunk.strip()):
            sentence = sentence.strip()
            if sentence and _ETC_VOID.search(sentence) and sentence not in hits:
                hits.append(sentence)
    return " ".join(hits[:3])


# ── 줄글 서식 ──────────────────────────────────────────────────────────────

# 결과 낱말. 미결·미상정·무산·보류·철회도 결과다 — 빠지면 그 안건이 통째로 사라진다.
_VERDICT_WORD = r"(?:수정\s*)?(?:가결|부결|보류|철회|미결|미상정|무산)"
_VERDICT = r"(?:원안\s*대로\s*)?(?:수정\s*)?(?:승인\s*)?(?:가결|부결|보류|철회|미결|미상정|무산|승인)"

# 결과 표시 기호. 회사마다 다르다 — 하나라도 빠지면 그 회사 것만 통째로 안 읽힌다.
_MARK = re.compile(r"(?:-->|->|=>|⇒|→|▶|☞|➤)")
# 화살표가 없을 때만 본다(제목 안에도 괄호·붙임표가 흔하다): 끝의 「(부결)」, 「 - 원안대로 가결」.
_PAREN_TAIL = re.compile(r"[(\[]\s*(" + _VERDICT + r"[^)\]]{0,20})\s*[)\]]\s*$")
_DASH_TAIL = re.compile(r"\s[-–—]\s*(" + _VERDICT + r".{0,24})$")
# 제목 뒤에 공백·쌍점으로 붙은 결과. 「승인」 하나로 끝나는 것은 제목(「재무제표 승인」)과
# 구분할 수 없어 받지 않는다 — 「원안대로 승인」 이어야 한다.
_PLAIN_TAIL = re.compile(
    r"(?:^|[\s:：])((?:원안\s*대로\s*)?(?:승인\s*)?" + _VERDICT_WORD
    + r"(?:\s*(?:되었음|되었습니다|됨|함|하였음))?|원안\s*대로\s*승인(?:\s*(?:되었음|되었습니다|됨))?)\s*\.?\s*$"
)
# 결과만 적힌 줄(앞 줄 안건의 결과). 짧고 결과 낱말로 시작해야 한다.
_RESULT_ONLY_LINE = re.compile(
    r"^(?:결과\s*[:：]\s*)?(" + _VERDICT + r")(?:\s*(?:되었음|되었습니다|됨|함|하였음))?\s*\.?\s*$"
)

# 안건 머리. 줄 맨 앞에서만 잡는다 — 줄 가운데의 「제2호」 를 안건으로 읽지 않는다.
_ITEM_HEAD = re.compile(
    r"^(?:(?:[-•○ㆍ·*▷◦□■]|[가-힣]\.|\d+\)|\(\d+\))\s*)*"
    r"제\s*(\d+(?:\s*-\s*\d+)*)\s*호\s*(?:의\s*안)?\s*[:：.]?\s*(.*)$"
)

# 구역 머리말. 「결의사항」 이 표준이고, 리츠·제약 쪽은 「기타 결의내용」, 「부의안건」 만 쓰기도 한다.
_SECTION_HEAD = re.compile(r"의결\s*사항|결의\s*사항|기타\s*결의\s*내용|결의\s*내용|부의\s*안건")
_SECTION_END = re.compile(
    r"^\d+\s*\.\s*(?:주주\s*총회\s*일\s*자|의결권\s*행사\s*기준일|기타\s*투자\s*판단)"
    r"|※\s*관련\s*공시"
)


def _normalize_vote_outcome(raw: str) -> str:
    text = re.sub(r"\s+", "", raw or "")
    if not text:
        return ""
    for word in ("미상정", "무산", "미결", "철회", "보류", "부결", "수정가결"):
        if word in text:
            return word
    if "가결" in text or "승인" in text:
        return "가결"
    return (raw or "").strip()


def _clean_verdict(raw: str) -> str:
    verdict = " ".join((raw or "").split())
    # 다음 항목 번호가 꼬리에 붙어 오는 문서가 있다(「원안대로 가결 2.」, 「… ②」).
    verdict = re.sub(r"\s+(?:\d+\s*\.|[①-⑳])\s*$", "", verdict)
    return verdict.strip(" -–—·※*")


def _split_title_verdict(text: str) -> tuple[str, str]:
    """안건 줄 나머지를 (제목, 결과 원문)으로 가른다. 결과가 없으면 결과는 빈 문자열."""
    text = " ".join((text or "").split())
    marks = list(_MARK.finditer(text))
    if marks:
        # 제목 안의 화살표(「A -> B 로 사명 변경」)에 속지 않도록 결과 낱말이 뒤따르는 마지막 기호를 쓴다.
        chosen = None
        for m in reversed(marks):
            if re.search(_VERDICT, text[m.end():]):
                chosen = m
                break
        chosen = chosen or marks[-1]
        return text[:chosen.start()].strip(" -–—·*:："), _clean_verdict(text[chosen.end():])
    for pattern in (_PAREN_TAIL, _DASH_TAIL, _PLAIN_TAIL):
        m = pattern.search(text)
        if m:
            title = text[:m.start()].strip(" -–—·*:：")
            if title:
                return title, _clean_verdict(m.group(1))
    return text.strip(" -–—·*:："), ""


def _result_only(line: str) -> str:
    """결과만 적힌 줄이면 결과 원문을, 아니면 빈 문자열을 돌려준다."""
    stripped = line.strip()
    mark = _MARK.match(stripped)
    if mark:
        rest = stripped[mark.end():].strip()
        return _clean_verdict(rest) if rest and len(rest) <= 40 else ""
    stripped = stripped.lstrip("-–—·*•○ ").strip()
    if len(stripped) > 40:
        return ""
    m = _RESULT_ONLY_LINE.match(stripped)
    return _clean_verdict(stripped) if m else ""


def _expand_vote_number_expr(expr: str) -> list[str]:
    expr = re.sub(r"\s+", " ", (expr or "").strip())
    if not expr:
        return []
    match = re.search(r"제(\d+)-(\d+)호\s*내지\s*제(?:\1-)?(\d+)호", expr)
    if match:
        major, start, end = match.group(1), int(match.group(2)), int(match.group(3))
        return [f"제{major}-{number}호" for number in range(start, end + 1)]
    match = re.search(r"제(\d+)호\s*내지\s*제(\d+)호", expr)
    if match:
        return [f"제{number}호" for number in range(int(match.group(1)), int(match.group(2)) + 1)]
    return re.findall(r"제\d+(?:-\d+)?호", expr)


def _parse_summary_outcome_targets(line: str) -> list[tuple[list[str], str, str]]:
    """「제1호 내지 제3호 원안대로 가결」 같은 일괄 결과 줄 → [(번호들, 정규화 결과, 원문 결과)]."""
    normalized = re.sub(r"\s+", " ", (line or "").strip())
    if not normalized:
        return []
    pairs = []
    pattern = re.compile(
        r"(제\d+(?:-\d+)?호(?:\s*(?:및|,|와|과)\s*제\d+(?:-\d+)?호)*|제\d+(?:-\d+)?호\s*내지\s*제(?:\d+-)?\d+호)"
        r"\s*(?:의\s*안)?\s*(?:은|는|이|가|모두)?\s*[:：]?\s*"
        r"(원안\s*대로\s*승인|원안\s*대로\s*가결|원안가결|수정\s*가결|가결|부결|미결|철회|보류|미상정|무산)"
    )
    for match in pattern.finditer(normalized):
        numbers = _expand_vote_number_expr(match.group(1))
        outcome = _normalize_vote_outcome(match.group(2))
        if numbers and outcome:
            pairs.append((numbers, outcome, match.group(2)))
    return pairs


def _canonical_number(raw: str) -> str:
    return "제" + re.sub(r"\s+", "", raw) + "호"


def parse_agm_result_summary(soup) -> list[dict]:
    """Parse voting outcomes from summary-form (prose) meeting result disclosures.

    줄 단위로 읽는다. 안건 머리(`제N호 …`)는 줄 맨 앞에서만 잡고, 결과는 표시 기호(→ 등) 뒤,
    화살표가 없으면 끝의 괄호·붙임표·결과 낱말에서만 읽는다. 결과가 없는 머리줄(하위 안건을
    거느린 상위 안건 등)은 버린다 — 제목에 「승인」 이 들어 있다고 가결로 읽지 않는다.
    하위 안건은 `제N-M호` 꼴이 바로 앞 상위 안건 `제N호` 아래 올 때만이다.
    """
    lines = [re.sub(r"\s+", " ", line).strip() for line in soup.get_text("\n", strip=True).splitlines()]
    lines = [line for line in lines if line]

    entries: list[dict] = []          # 문서 순서
    by_number: dict[str, dict] = {}
    last_top: dict | None = None
    started = False

    def add_entry(number: str, rest: str) -> dict:
        nonlocal last_top
        title, verdict = _split_title_verdict(rest)
        entry = {"number": number, "agenda": title, "passed_raw": verdict,
                 "passed": _normalize_vote_outcome(verdict) if verdict else "",
                 "parent": None, "has_child": False}
        major = number[1:-1].split("-", 1)[0]
        if "-" in number and last_top is not None and last_top["number"] == f"제{major}호":
            entry["parent"] = last_top["number"]
            last_top["has_child"] = True
        elif "-" not in number:
            last_top = entry
        entries.append(entry)
        by_number[number] = entry
        return entry

    def handle(line: str) -> None:
        targeted = _parse_summary_outcome_targets(line)
        if targeted:
            # 일괄 결과 줄(「제1호 내지 제3호 원안대로 가결」, 「제2-1호 및 제2-2호 가결」).
            # 번호 바로 뒤에 결과 낱말이 와야 걸리므로 「제1호 의안 : 제목 → 가결」 은 여기 안 온다.
            for numbers, outcome, raw in targeted:
                for number in numbers:
                    entry = by_number.get(number) or add_entry(number, "")
                    if not entry["passed"]:
                        entry["passed"], entry["passed_raw"] = outcome, raw
            return
        head = _ITEM_HEAD.match(line)
        if head:
            add_entry(_canonical_number(head.group(1)), head.group(2))
            return
        if not entries:
            return
        last = entries[-1]
        verdict = _result_only(line)
        if verdict:
            if last["passed"]:
                return
            last["passed_raw"], last["passed"] = verdict, _normalize_vote_outcome(verdict)
            # 하위 안건이 결과 없이 나열되고 결과가 한 줄로 오면 아직 결과 없는 형제에게도 준다.
            if last["parent"]:
                for entry in entries:
                    if entry["parent"] == last["parent"] and not entry["passed"]:
                        entry["passed_raw"], entry["passed"] = last["passed_raw"], last["passed"]
            elif last["has_child"]:
                for entry in entries:
                    if entry["parent"] == last["number"] and not entry["passed"]:
                        entry["passed_raw"], entry["passed"] = last["passed_raw"], last["passed"]
            return
        # 결과가 아직 없는 안건의 괄호 꼬리(후보자 이름 등)는 제목에 잇는다.
        if line.startswith("(") and not last["passed"] and not re.search(_VERDICT, line):
            last["agenda"] = (last["agenda"] + " " + line).strip()

    for line in lines:
        if not started:
            head = _SECTION_HEAD.search(line)
            if not head:
                continue
            started = True
            rest = line[head.end():].strip(" :：")
            if rest and re.search(r"제\s*\d", rest):
                handle(rest)
            continue
        if _SECTION_END.search(line) or line.startswith(("[", "【", "〖")):
            break
        handle(line)

    items = []
    for entry in entries:
        if not entry["passed"]:
            continue                  # 결과 없는 머리줄
        if entry["has_child"] and any(
                e["parent"] == entry["number"] and e["passed"] for e in entries):
            continue                  # 하위 안건 결과로 대신한다
        items.append({
            "number": entry["number"],
            "resolution_type": "",
            "agenda": entry["agenda"],
            "passed": entry["passed"],
            "passed_raw": entry["passed_raw"],
            "approval_rate_issued": "",
            "approval_rate_voted": "",
            "opposition_rate": "",
            "estimated_attendance": None,
            "approval_base": "unknown",
            "approval_base_label": "",
            "remark": "",
            "remark_contradicts_pass": False,
        })
    return items
