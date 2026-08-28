"""Parsers for DART shareholder-meeting result disclosures."""

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
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            if len(cells) < 5 or not cells[0] or cells[0] == "-":
                continue
            try:
                issued = float(cells[4]) if len(cells) > 4 and cells[4] else 0
                voted = float(cells[5]) if len(cells) > 5 and cells[5] else 0
                attendance = round(issued / voted * 100, 1) if voted > 0 else None
            except (ValueError, ZeroDivisionError):
                attendance = None
            items.append({
                "number": cells[0],
                "resolution_type": cells[1] if len(cells) > 1 else "",
                "agenda": cells[2] if len(cells) > 2 else "",
                "passed": cells[3] if len(cells) > 3 else "",
                "approval_rate_issued": cells[4] if len(cells) > 4 else "",
                "approval_rate_voted": cells[5] if len(cells) > 5 else "",
                "opposition_rate": cells[6] if len(cells) > 6 else "",
                "estimated_attendance": attendance,
                # 참석률의 분모가 무엇인지 — 자사주 포함 발행총수인지, 의결권 있는 주식인지.
                "approval_base": approval_base,
                "approval_base_label": base_label,
            })
        if items:
            return items
    return []


def _normalize_vote_outcome(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    if "부결" in text:
        return "부결"
    if "수정가결" in text:
        return "수정가결"
    if "원안가결" in text or "원안대로 가결" in text:
        return "가결"
    if "원안대로 승인" in text or "승인" in text or "가결" in text:
        return "가결"
    return text


def _extract_vote_outcome(text: str) -> str:
    normalized = (text or "").replace("-->", "→").replace("->", "→")
    if not any(token in normalized for token in ("→", "가결", "부결", "승인")):
        return ""
    if "→" in normalized:
        normalized = normalized.split("→", 1)[1]
    return _normalize_vote_outcome(normalized)


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


def _parse_summary_outcome_targets(line: str) -> list[tuple[list[str], str]]:
    normalized = re.sub(r"\s+", " ", (line or "").strip())
    if not normalized:
        return []
    pairs = []
    pattern = re.compile(
        r"(제\d+(?:-\d+)?호(?:\s*(?:및|,)\s*제\d+(?:-\d+)?호)*|제\d+(?:-\d+)?호\s*내지\s*제(?:\d+-)?\d+호)"
        r"\s*(원안대로 승인|원안대로 가결|원안가결|수정가결|가결|부결)"
    )
    for match in pattern.finditer(normalized):
        numbers = _expand_vote_number_expr(match.group(1))
        outcome = _normalize_vote_outcome(match.group(2))
        if numbers and outcome:
            pairs.append((numbers, outcome))
    return pairs


def parse_agm_result_summary(soup) -> list[dict]:
    """Parse voting outcomes from summary-form meeting result disclosures."""
    lines = [re.sub(r"\s+", " ", line).strip() for line in soup.get_text("\n", strip=True).splitlines()]
    lines = [line for line in lines if line]
    started = False
    current = None
    current_children = {}
    last_child_number = None
    items = []

    def flush_current() -> None:
        nonlocal current, current_children, last_child_number
        sources = current_children.values() if current_children else ([current] if current else [])
        for source in sources:
            if source and source.get("passed"):
                items.append({
                    "number": source.get("number", ""),
                    "resolution_type": "",
                    "agenda": source.get("agenda", ""),
                    "passed": source.get("passed", ""),
                    "approval_rate_issued": "",
                    "approval_rate_voted": "",
                    "opposition_rate": "",
                    "estimated_attendance": None,
                    "approval_base": "unknown",
                    "approval_base_label": "",
                })
        current = None
        current_children = {}
        last_child_number = None

    for line in lines:
        if not started:
            if "의결사항" in line or "결의사항" in line:
                started = True
            continue
        if (re.match(r"^\d+\.\s*(주주총회 ?일자|의결권행사기준일|기타 투자판단)", line)
                or "※ 관련공시" in line or line.startswith("[") or line.startswith("〖")):
            flush_current()
            break

        if current:
            targeted = _parse_summary_outcome_targets(line)
            if targeted:
                for numbers, outcome in targeted:
                    for number in numbers:
                        if number in current_children:
                            current_children[number]["passed"] = outcome
                        elif current.get("number") == number:
                            current["passed"] = outcome
                if (current_children and all(child.get("passed") for child in current_children.values())) or current.get("passed"):
                    flush_current()
                continue

        child = re.search(r"(?:[ㆍ•\-]\s*)?(제\d+(?:-\d+)?호)\s*[:：]?\s*(.*)", line)
        if child and current:
            number, remainder = child.group(1), child.group(2).strip()
            passed = _extract_vote_outcome(remainder) if any(t in remainder for t in ("→", "가결", "부결", "승인")) else ""
            agenda = remainder.split("→", 1)[0].strip() if "→" in remainder else remainder
            current_children[number] = {"number": number, "agenda": agenda.strip(" -"), "passed": passed}
            last_child_number = number
            continue

        match = re.search(r"(?:[-•○]\s*)?(?:\d+\)\s*)?(제\d+(?:-\d+)?호)\s*(?:의안)?\s*[:：]?\s*(.*)", line)
        if match:
            flush_current()
            number, remainder = match.group(1), match.group(2).strip()
            passed = _extract_vote_outcome(remainder) if any(t in remainder for t in ("→", "가결", "부결", "승인")) else ""
            agenda = remainder.split("→", 1)[0].strip() if "→" in remainder else remainder
            current = {"number": number, "agenda": agenda.strip(" -"), "passed": passed}
            continue

        if current:
            if last_child_number and line.startswith("(") and not any(t in line for t in ("가결", "부결", "승인")):
                current_children[last_child_number]["agenda"] = (
                    current_children[last_child_number]["agenda"] + " " + line
                ).strip()
                continue
            outcome = _extract_vote_outcome(line)
            if outcome:
                if current_children:
                    for child in current_children.values():
                        child["passed"] = outcome
                else:
                    current["passed"] = outcome
                flush_current()

    flush_current()
    return items
