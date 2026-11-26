"""Source-bound executive severance provisions and their remuneration limits."""
from __future__ import annotations

import re

_CONTROL = re.compile(r"적대적\s*(?:기업\s*)?(?:인수|M\s*&\s*A)|경영권(?:의)?\s*(?:변경|변동|이전|인수|교체)|"
                      r"지배권(?:의)?\s*(?:변경|변동|이전)|최대주주(?:의)?\s*(?:변경|변동)|"
                      r"인수\s*[·ㆍ및\s]*합병|M\s*&\s*A|change\s+of\s+control|황금\s*낙하산", re.I)
_SEVERANCE = re.compile(r"(?:퇴직|퇴임|해임)\s*(?:특별\s*)?(?:금|보상(?:금|액)?|위로금)|특별\s*(?:보상금|위로금)")
_ROLES = re.compile(r"대표이사|사내이사|사외이사|독립이사|등기이사|비등기임원|등기임원|이사(?!회|장)|임원|경영진")
_REMOVAL = re.compile(r"해임|면직|비자발적\s*퇴[직임]|의사에?\s*반.{0,15}(?:퇴[직임]|사임)|(?:요구|강요|압력).{0,15}사임")
_MONEY = r"[\d,]+(?:\.\d+)?\s*(?:억|백만|천만|만|천)?\s*원"
_RATE = r"(?:\d+(?:\.\d+)?|[가-힣]+\(\d+\))\s*배(?:수)?"
_FORMULA = re.compile(r"(?:최근\s*)?(?:\d+\s*개?년(?:간)?\s*)?(?:(?:월)?평균\s*)?(?:연봉|월보수|급여|퇴직금\s*누계액)"
                      r"(?:\s*[xX×*]\s*(?:재직|근속)\s*(?:연수|년수|기간)|(?:의|에)?\s*" + _RATE + r")")
_AMOUNT = re.compile(_MONEY + "|" + _RATE)
_SAME = re.compile(r"좌동|상동|현행과\s*(?:동일|같음)")
_DELETE = re.compile(r"^[\s<>〈〉()\[\].-]*(?:제\s*\d+\s*조(?:의\s*\d+)?(?:\s*\([^)]*\))?\s*)?(?:삭\s*제|폐\s*지)[\s<>〈〉()\[\].-]*$")
_NEGATIVE = re.compile(r"(?:지급|도입|채택|적용)\s*하지\s*(?:아니|않)|(?:지급|도입)(?:하지\s*않|할\s*수\s*없)|(?:규정|조항|제도)(?:은|는|이|가)?\s*없")
_CHANGE_ONLY = re.compile(r"(?:해임|퇴직|사임)\s*여부[와에]\s*(?:관계|상관)없이|(?:변경|변동|이전)\s*(?:만으로|즉시)|(?:변경|변동)(?:되면|\s*시)\s*.{0,30}?지급")
_INDEPENDENT = re.compile(r"(?:해임|퇴직|사임)(?:\s*여부[와에]\s*(?:관계|상관)없이|\s*(?:절차\s*)?없이)")
_LABEL = {"new": "신설안", "amended": "변경안", "removed": "삭제안", "unchanged": "현행 유지 표기",
          "mentioned": "공시 기재", "verify_change": "변경 내용 확인 필요", "increased": "인상안", "decreased": "인하안"}


def _provision(text: str) -> dict | None:
    """Evaluate one table cell or one narrative sentence, never a whole flattened filing."""
    text = " ".join(text.split())
    for trigger in _CONTROL.finditer(text):
        start = max(0, trigger.start() - 125)
        quote = text[start:trigger.end() + 670]
        if not (_SEVERANCE.search(quote) and _ROLES.search(quote) and re.search(r"지급|지불|가산", quote)):
            continue
        if not re.search(r"특별|황금\s*낙하산|보상[금액]?|위로금|별도|추가|가산|" + _RATE, quote):
            continue
        if _NEGATIVE.search(quote) or re.search(r"(?:제도|용어)(?:를?\s*말한다|의\s*정의)", quote):
            continue
        trigger_type = ("해임·퇴직 여부 무관" if _INDEPENDENT.search(quote)
                        else "경영권 변동과 해임·사임" if _REMOVAL.search(quote)
                        else "경영권 변동으로 지급" if _CHANGE_ONLY.search(quote) else "추가 지급 조건 확인")
        return {"quote": quote, "trigger": trigger_type, "roles_mentioned": list(dict.fromkeys(_ROLES.findall(quote))),
                "award_terms": list(dict.fromkeys(m[0] for m in _FORMULA.finditer(quote))) +
                               list(dict.fromkeys(m[0] for m in _AMOUNT.finditer(quote)))}
    return None


def _changes(before: str, after: str, old: dict, new: dict) -> list[str]:
    # Compare a multiplier only when the calculation basis and surrounding wording agree.
    notes = []
    rate = r"(\d+(?:\.\d+)?)\s*배"
    b, a = re.findall(rate, before), re.findall(rate, after)
    old_formula = _FORMULA.findall(before)
    new_formula = _FORMULA.findall(after)
    same_formula = (len(old_formula) == len(new_formula) == 1 and
                    re.sub(rate, "#", old_formula[0]) == re.sub(rate, "#", new_formula[0]) and
                    set(old["roles_mentioned"]) <= set(new["roles_mentioned"]))
    if len(a) == len(b) == 1 and (same_formula or re.sub(rate, "#", before).split() == re.sub(rate, "#", after).split()) and float(a[0]) > float(b[0]):
        notes.append("동일 산식의 지급 배수 인상")
    prev = set(old["roles_mentioned"])
    curr = set(new["roles_mentioned"])
    if prev < curr and not prev.intersection({"임원", "이사", "경영진"}):
        notes.append("추가 직위 기재 · " + ", ".join(sorted(curr - prev)))
    return notes


_RETIRE = r"(?:퇴직|퇴임)\s*(?:보상금|위로금|급여|소득|금)"
_PAY_CAP = r"(?:보수\s*(?:총액\s*)?(?:의\s*)?한도(?:액)?|연간\s*보수\s*총액)"
_EXCLUDE = r"(?:제외|배제)(?!\s*(?:하지\s*않|하지\s*아니|되지\s*않|되지\s*아니))|포함(?:되지|하지|시키지)\s*(?:않|아니)|산입하지\s*(?:않|아니)"
_NEAR = r"[^.。;①-⑳\n]{0,42}?"
_OUTSIDE = re.compile(rf"{_PAY_CAP}{_NEAR}{_RETIRE}{_NEAR}(?:{_EXCLUDE})|"
                      rf"{_RETIRE}{_NEAR}{_PAY_CAP}{_NEAR}(?:{_EXCLUDE})|"
                      rf"{_RETIRE}{_NEAR}{_PAY_CAP}(?:과|와)?\s*(?:별도(?:로|이다|로서)|별개)|"
                      rf"{_RETIRE}(?:은|는|을|를)?\s*(?:{_EXCLUDE})한\s*(?:임원|이사)?\s*{_PAY_CAP}|"
                      rf"{_RETIRE}(?:에\s*대하여는|은|는)?\s*한도\s*적용에서\s*(?:{_EXCLUDE})")
_SEPARATE = re.compile(rf"{_RETIRE}(?:의|에\s*대한|은|는)?\s*(?:지급\s*)?(?:별도(?:의)?\s*)?한도(?:액)?|"
                       rf"(?:별도(?:의)?\s*한도(?:액)?)(?:로|를)?\s*(?:정한\s*)?(?:임원|이사)?\s*{_RETIRE}")
_CAP_AMOUNT = re.compile(r"^\s*(?:액|은|는|을|를|를?\s*연간|로|최대|금|연간|총|[:：])?[\s:：]*(?:연간\s*)?(" + _MONEY + r")")


def _limit_provision(text: str) -> dict | None:
    text = " ".join(text.split())
    separate, outside = _SEPARATE.search(text), _OUTSIDE.search(text)
    if not (separate or outside):
        return None
    if outside and re.search(r"포함(?:하되|하고|하며|한다)|산입(?:하되|하고|하며|한다)|(?:성과급|스톡옵션|상여금)(?:만|은|을)\s*제외", outside[0]):
        outside = None
    if not (separate or outside):
        return None
    hit = separate or outside
    quote = text[max(0, hit.start()-105):hit.end()+95]
    if not (_ROLES.search(quote) or "감사" in quote):
        return None
    if re.search(r"(?:직원|종업원|근로자)(?:의|에게|에\s*대한)?\s*퇴직", quote) and not re.search(r"(?:임원|이사|감사)(?:의|에게)?\s*퇴직", quote):
        return None
    amount = _CAP_AMOUNT.match(text[separate.end():]) if separate else None
    # A payment regulation on its own says nothing about a separate ceiling.
    if separate and re.match(r"(?:을|를|은|는)?\s*(?:두지|설정하지|신설하지|적용하지)\s*(?:않|아니)", text[separate.end():]):
        return None
    if separate and not amount and "별도" not in separate[0] and not re.search(r"별도|신설", text[separate.end():separate.end()+30]):
        separate = None
    if not (separate or outside):
        return None
    limit_text = amount[1] if amount and separate else None
    if limit_text:
        transition = re.match(r"\s*(?:에서|→|->)\s*(" + _MONEY + ")", text[separate.end()+amount.end():])
        if transition:
            limit_text = transition[1]
    return {"quote": quote, "trigger": "퇴직금 별도 한도" if separate else "일반 보수한도에서 퇴직금 제외",
            "outside_pay_limit": bool(outside), "separate_limit": bool(separate),
            "limit_amount_text": limit_text}


def _limit_changes(before, after, old, new):
    notes = []
    if not old["separate_limit"] and new["separate_limit"]:
        notes.append("별도 한도 신설안")
    if not old["outside_pay_limit"] and new["outside_pay_limit"]:
        notes.append("일반 보수한도에서 제외하는 변경안")
    a, b = old["limit_amount_text"], new["limit_amount_text"]
    if a and b and before.replace(a, "#").split() == after.replace(b, "#").split():
        from decimal import Decimal
        units = {"": 1, "천": 1000, "만": 10000, "백만": 1000000, "천만": 10000000, "억": 100000000}
        def won(raw):
            num, unit = re.fullmatch(r"([\d,.]+)\s*(억|백만|천만|만|천)?\s*원", raw).groups()
            return Decimal(num.replace(",", "")) * units[unit or ""]
        if won(b) != won(a):
            notes.append("퇴직금 별도 한도 인상안" if won(b) > won(a) else "퇴직금 별도 한도 인하안")
    return notes


def retirement_limit_change(before: str, after: str) -> str | None:
    old, new = _limit_provision(before), _limit_provision(after)
    if new and not old:
        return new["trigger"] + " · 변경 전후와 승인 범위 확인"
    if new and old:
        return " · ".join(_limit_changes(before, after, old, new)) or None
    return None


def retirement_signals(text: str, html: str = "", amendments: list[dict] | None = None) -> dict:
    checks = []
    if _CONTROL.search(text) and _SEVERANCE.search(text):
        checks.append(("control_severance", _provision, _changes))
    if ("한도" in text or "보수총액" in text) and re.search(_RETIRE, text):
        checks.append(("retirement_limit", _limit_provision, _limit_changes))
    if not checks:
        return {}
    rows = list(amendments or [])
    if html and "<table" in html.lower() and ("변경" in text or "개정" in text or "현행" in text):
        from open_proxy_mcp.services.shareholder_meeting_parser import parse_retirement_pay_xml
        rows.extend(parse_retirement_pay_xml(html)["amendments"])
    return {key: signal for key, read, compare in checks
            if (signal := _read_changes(text, rows, read, compare))}


def _read_changes(text, rows, read, compare):
    provisions, seen = [], set()
    for row in rows:
        before, after = row.get("before", "").strip(), row.get("after", "").strip()
        key = tuple(re.sub(r"\s+", "", s) for s in (before, after))
        if key in seen:
            continue
        seen.add(key)
        old, new = read(before), read(after)
        if not (new or old):
            continue
        if key[0] == key[1] or (not new and _SAME.search(after)):
            change = "unchanged"
        elif not new:
            change = "removed" if _DELETE.fullmatch(after) else "verify_change"
        elif old:
            change = "amended"
        else:
            introduced = ("신설" in before + row.get("reason", "") or re.fullmatch(r"[-—]+", before) or
                          (before and _SEVERANCE.search(before) and not _CONTROL.search(before)))
            change = "new" if introduced else "verify_change"
        notes = compare(before, after, old, new) if change == "amended" else []
        provisions.append({**(new or old), "change": change, "change_label": _LABEL[change], "change_notes": notes,
                           "before": before[:1800], "after": after[:1800], "clause": row.get("clause") or ""})
    if not provisions:
        # A prose mention is not proof that a proposal was adopted or a benefit paid.
        for sentence in re.split(r"(?<=[다음함됨요])[.]\s+|\n+|(?=제\s*\d+\s*조[ (])", text):
            if provision := read(sentence):
                if provision["quote"] in seen:
                    continue
                seen.add(provision["quote"])
                change = "mentioned"
                if read is _limit_provision and provision["separate_limit"]:
                    proposal = re.search(r"(신설|인상|증액|인하|감액|삭제|폐지)\s*(?:의\s*건|안(?:건)?(?:[\s).]|$)|하고자|할\s*예정)", provision["quote"])
                    if proposal:
                        change = {"신설": "new", "인상": "increased", "증액": "increased", "인하": "decreased",
                                  "감액": "decreased", "삭제": "removed", "폐지": "removed"}[proposal[1]]
                provisions.append({**provision, "change": change, "change_label": _LABEL[change], "change_notes": []})
                if len(provisions) == 3:
                    break
    return {"quote": provisions[0]["quote"], "provisions": provisions} if provisions else None


def provision_lines(signal: dict) -> list[str]:
    lines = []
    for p in signal.get("provisions", []):
        notes = " · ".join([p["change_label"], p["trigger"], *p["change_notes"]])
        lines.append(f"  - {notes} · {p.get('clause') or '공시 문구'}")
        if p.get("before") or p.get("after"):
            lines.append(f"    - 변경 전 {p['before'] or '기재 없음'} / 변경안 {p['after'] or '기재 없음'}")
        else:
            lines.append(f"    - {p['quote']}")
    return lines
