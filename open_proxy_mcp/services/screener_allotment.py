"""Read the decision and its named issuer in an exchange capital-increase form."""
from __future__ import annotations

import re

from open_proxy_mcp.services.doc_tables import compact, distinct, table_grids

# Only the selected method cell counts. Forms also print headings for unused methods.
_METHOD = re.compile(r"(?:제?3자배정|주주배정(?:후실권주일반공모)?|일반공모|주주우선공모)(?:방식|증자)?")
_NOTES = {
    "equity_stake_change": r"(?:지배회사.{0,40}?|당사의?\s*)지분율은?\s*(?:기존\s*)?[\d.]+\s*%.{0,50}?(?:변동|종속회사에서\s*제외)[^.]{0,30}",
    "debt_setoff": r"(?:대여금\s*채권.{0,120}?(?:상계|출자전환)|출자전환\s*\(채무상계\))",
    "subsidiary_liquidation": r"기존\s*차입금을?\s*상환하고.{0,30}?청산\s*절차를?\s*추진",
    "replaces_allotment": r"투자계획\s*변경에\s*따라.{0,160}?공시를\s*대체",
}


def exchange_allotment(html: str) -> dict:
    grids = table_grids(html)
    methods, issuer, notes, subscribers, terms = set(), "", [], [], {}
    for grid in grids:
        for i, raw in enumerate(grid):
            row = distinct(raw)
            if len(row) < 2:
                continue
            label, value = compact(row[0]), row[-1]
            if label in {"종속회사인", "자회사인"} and len(row) == 3 and "주요경영사항" in compact(value):
                issuer = row[1]
            elif re.fullmatch(r"(?:\d+[.)])?증자방식", label) and len(row) == 2:
                if _METHOD.fullmatch(compact(value)):
                    methods.add(compact(value))
            elif "기타투자판단" in label and "관련공시" not in compact(row[1]):
                notes.append(value)
            elif "제3자배정증자의목적" in map(compact, row) and i + 1 < len(grid):
                notes.append(grid[i + 1][-1])
            elif "신주의종류와수" in label and len(row) >= 3 and "보통주식" in compact(row[1]) and re.fullmatch(r"[\d,]+", value):
                terms["new_common_shares"] = value
            elif label == "제3자배정대상자":
                header = [compact(v) for v in raw]
                if "회사또는최대주주와의관계" not in header or "선정경위" not in header:
                    continue
                relation = header.index("회사또는최대주주와의관계")
                shares = next((n for n, v in enumerate(header) if v.startswith("배정주식수")), None)
                for cells in grid[i + 1:]:
                    if len(cells) != len(header) or not cells[0] or cells[0] in {"-", "합계"}:
                        break
                    item = {"name": cells[0], "reported_relationship": cells[relation]}
                    if shares is not None and re.fullmatch(r"[\d,]+", cells[shares]):
                        item["shares"] = cells[shares]
                    subscribers.append(item)
    if len(methods) != 1:
        return {}
    method = methods.pop()
    facts = {}
    for key, pattern in _NOTES.items():
        for note in notes:
            normalized = " ".join(note.split())
            match = re.search(pattern, normalized)
            if key == "equity_stake_change" and match and re.search(r"변동(?:이|은)?\s*(?:없|하지\s*않)", normalized[match.start():match.end()+10]):
                continue
            if match:
                facts[key] = normalized[max(0, match.start()-25):match.end()+32].strip()
                break
    return {"method": method, "issuer": issuer, "terms": terms, "subscribers": subscribers, "facts": facts}
