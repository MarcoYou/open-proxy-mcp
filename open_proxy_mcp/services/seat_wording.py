"""Read quantity and modality together, preserving the clause and its scope."""
from __future__ import annotations

import re

_ROLE = r"(?:기타비상무이사|사내이사|사외이사|독립이사|이사)"
_COUNT = r"(?:[일이삼사오육칠팔구십]+(?:인)?\s*\(\s*)?(?P<n>\d{1,3})\s*\)?\s*[인명]"
_AMOUNT = (_COUNT + r"(?:\s*(?:이상\s*[,~∼～-]?\s*|부터\s*|[~∼～-]\s*)(?:[일이삼사오육칠팔구십]+\s*\(\s*)?"
           r"(?P<hi>\d{1,3})\s*\)?\s*[인명]\s*(?:까지|이하|이내)?)?"
           r"(?:\s*(?P<bound>까지|이하|이내|이상|미만|초과))?")
_SELECT = r"(?:(?:선임|선출)(?:할\s*수\s*있[다음으며고]?|한다|한다는|하여야\s*한다|해야\s*한다|하는\s*(?:건|안)|의\s*건)?|뽑(?:을\s*수\s*있[다음으며고]?|는다|아야\s*한다))"
_MATCH = re.compile(
    rf"(?P<role>{_ROLE})(?P<subject>\s*(?:의\s*)?(?:정원|원수|총수|수(?=\s|[은는가를을이]|\d)|회)?\s*(?:의?\s*상한)?\s*(?:은|는|가|를|을|이)?\s*)"
    rf"(?:총\s*)?(?P<prefix>최대|최소)?\s*{_AMOUNT}(?:\s*(?:의\s*범위\s*(?:내|에서)|으로|로|을|를))?\s*"
    rf"(?P<verb>{_SELECT}|(?:구성|제한)(?:한다|할\s*수\s*있다)|정(?:한다|할\s*수\s*있다)|할\s*수\s*있다|한다|둔다|두(?:도록\s*한다|기로\s*한다)|둘\s*수\s*있다)?")
_LABEL = {"fixed": "특정 인원 명시", "maximum": "상한", "minimum": "하한", "range": "범위",
          "permitted": "선임 가능 문구 · 확정 인원 미확인"}
_REVERSED = re.compile(rf"(?<![\d(])(?P<prefix>최대|최소)?\s*{_AMOUNT}\s*(?:의\s*)?(?P<role>{_ROLE})(?:를|을|로)?\s*(?P<verb>{_SELECT})")
_SUFFIX = re.compile(rf"(?P<role>{_ROLE})\s*(?:를|을|의)?\s*(?P<verb>선임|선출)(?:의\s*건)?\s*[(（:]\s*"
                     rf"(?P<prefix>최대|최소)?\s*{_AMOUNT}\s*[)）]")
_QUALIFIER = re.compile(r"(?:\d\s*\)?\s*[인명]\s*(?:까지|이하|이내|이상|미만|초과|[~∼～-])|(?:최대|최소)\s*\d|(?:선임|선출)할\s*수|뽑을\s*수)")
_NUM = re.compile(r"(\d+)\s*\)?\s*(?:인|명)")


def _read(text: str):
    matches = sorted((m for pattern in (_MATCH, _REVERSED, _SUFFIX) for m in pattern.finditer(text)),
                     key=lambda m: (m.start(), -m.end()))
    end = 0
    for m in matches:
        if m.start() < end:
            continue
        verb, subject = m["verb"] or "", m.groupdict().get("subject") or ""
        prefix = re.search(r"(?:선임|선출)할\s*$", text[max(0, m.start()-12):m.start()])
        elect = prefix or re.search(r"선임|선출|뽑", verb)
        if not (elect or verb or re.search("정원|원수|총수|상한", subject)):
            continue
        if re.match(r"\s*(?:하지\s*않|하지\s*아니|할\s*수\s*없)", text[m.end():]):
            continue
        if re.search(r"위원회(?:는|의)?\s*$", text[max(0, m.start()-15):m.start()]):
            continue
        n = int(m["n"])
        bound = m["bound"] or m["prefix"] or ""
        if m["hi"]:
            kind = "range"
        elif bound in {"까지", "이하", "이내", "최대", "미만"} or "상한" in subject:
            kind = "maximum"
        elif bound in {"이상", "최소", "초과"}:
            kind = "minimum"
        elif re.search(r"수\s*있", verb):
            kind = "permitted"
        else:
            kind = "fixed"
        high = int(m["hi"]) if m["hi"] else n if kind in {"fixed", "maximum"} else None
        low = n if kind in {"fixed", "minimum", "range"} else None
        if bound == "미만":
            high = n - 1
        if bound == "초과":
            low = n + 1
        if high is not None and low is not None and low > high:
            continue
        end = m.end()
        yield {"scope": "election" if elect else "board", "role": m["role"], "kind": kind, "number": n,
               "minimum": low, "maximum": high, "quote": ((prefix[0] if prefix else "") + m[0]).strip()}


def _election_title(title: str) -> str:
    # A board-size premise may precede the actual election in the same agenda title.
    return re.split(r"전제(?:로|하에)\s*(?:한|하여)?", title or "")[-1]


def _needs_context(text, entries):
    return bool(_QUALIFIER.search(text) or re.search(r"정원|이사(?:의)?\s*수\s*상한|후보(?:자)?\s*\d+\s*[인명]|"
                r"(?:선임|선출)(?:을)?\s*하지\s*(?:않|아니)|위원회.{0,12}구성", text)
                or any(e["scope"] == "board" or e["kind"] != "fixed" for e in entries))


def title_seats(title: str, *, upper_bound: bool = False) -> int | None:
    """Number for arithmetic. An explicit ceiling is usable only as an upper bound."""
    text = _election_title(title)
    entries = list(_read(text))
    if _needs_context(text, entries):
        if len(entries) == 1 and entries[0]["scope"] == "election":
            e = entries[0]
            if e["kind"] == "fixed" or (upper_bound and e["kind"] in {"maximum", "range"}):
                if len(_NUM.findall(text)) == (2 if e["kind"] == "range" else 1):
                    return e["maximum"] if 1 <= e["maximum"] <= 30 else None
        return None
    nums = [int(v) for v in _NUM.findall(text)]
    return sum(nums) if nums and all(1 <= n <= 30 for n in nums) and sum(nums) <= 30 else None


def nonfixed_title(title: str) -> bool:
    text = _election_title(title)
    return _needs_context(text, _read(text))


def seat_wording(text: str, clauses: list[dict], *, conditional: bool = False) -> dict | None:
    changes, mentions, covered = [], [], set()
    def compact(q):
        return re.sub(r"\s+", "", q)

    def signature(entries):
        return [(e["scope"], e["role"], e["kind"], e["minimum"], e["maximum"], e["number"]) for e in entries]

    for row in clauses:
        before, after = row.get("before", ""), row.get("after", "")
        old, new = list(_read(before)), list(_read(after))
        covered.update(compact(e["quote"]) for e in old + new)
        if signature(old) == signature(new) or (not new and not re.fullmatch(r"[\s()<>〈〉]*(?:삭제|폐지)[\s()<>〈〉]*", after)):
            continue
        changes.append({"clause": row.get("clause", ""), "before": old, "after": new,
                        "before_text": before[:1200], "after_text": after[:1200]})
    for e in _read(text):
        key = compact(e["quote"])
        if key in covered or (e["kind"] == "fixed" and not conditional):
            continue
        covered.add(key)
        mentions.append(e)
    if not (changes or mentions):
        return None
    basis = (changes[0]["after"] or changes[0]["before"]) if changes else mentions
    return {"quote": basis[0]["quote"], "seat_changes": changes, "seat_mentions": mentions}


def seat_lines(signal: dict) -> list[str]:
    def describe(e):
        scope = "선임 인원" if e["scope"] == "election" else "구성 인원"
        return f"{scope} · {_LABEL[e['kind']]} · 「{e['quote']}」"
    lines = []
    for row in signal.get("seat_changes", []):
        old = " / ".join(map(describe, row["before"])) or row["before_text"] or "기재 없음"
        new = " / ".join(map(describe, row["after"])) or row["after_text"] or "기재 없음"
        lines.append(f"  - {row['clause']} 변경 전 {old} → 변경안 {new}")
    lines.extend(f"  - {describe(e)}" for e in signal.get("seat_mentions", []))
    return lines
