"""Parsing helpers for dividend API responses and decision disclosures."""

import re


DIVIDEND_KEYWORDS = (
    "현금ㆍ현물배당결정",
    "현금배당결정",
    "분기ㆍ중간배당결정",
    "주식배당결정",
    "현금ㆍ현물배당을위한주주명부폐쇄",
    "중간(분기)배당을위한주주명부폐쇄",
    "권리분기배당락",
    "권리중간배당락",
    "배당락",
)

_UNIT_MULTIPLIERS = {
    "조원": 1_000_000_000_000,
    "십억원": 1_000_000_000,
    "억원": 100_000_000,
    "천만원": 10_000_000,
    "백만원": 1_000_000,
    "십만원": 100_000,
    "만원": 10_000,
    "천원": 1_000,
    "백원": 100,
    "원": 1,
    "조": 1_000_000_000_000,
    "억": 100_000_000,
    "백만": 1_000_000,
    "만": 10_000,
    "천": 1_000,
}


# 11번 「기타 투자판단과 관련한 중요사항」 본문의 끝을 어디로 볼 것인가.
# 🔴 예전 경계는 아무 ※ 에서나 끊었다. 실측(공시원문 3,831건) 결과 두 가지가 어긋났다.
#   · 비고가 ※ 로 시작하는 서식에서는 본문이 통째로 사라졌다(15건).
#   · 정정공시는 정정 후 본문 뒤에 **정정 전 본문 전체**가 이어 붙는데, 옛 경계는 그것을
#     비고 안으로 끌고 들어왔다(60건). 그래서 배당결정 본문 머리글도 경계로 세운다.
# ※ 를 그냥 무시하는 것도 안 된다 — 「※ 관련공시」 꼬리 뒤에 다른 공시가 붙는다(20240403800157).
_REMARKS_RE = re.compile(
    r"11\.\s*기타\s*투자판단과\s*관련한\s*중요사항\s*(.*?)"
    r"(?:※\s*관련\s*공시"
    r"|【"
    r"|(?:현금\s*ㆍ?\s*현물배당|주식배당|분기\s*ㆍ?\s*중간배당)\s*결정\s*1\.\s*배당구분"
    r"|\Z)"
)

# 「특별」 한 글자나 「추가」로 잡으면 안 된다. 실측 3,831건에서 옛 규칙은 23건을 물었는데
# 그중 21건이 오탐이었다 — 정관상 우선주 액면배당률 1%p 「추가」 배당, 유상증자 「추가」 발행,
# 주식배당 「추가」 결정, 전환사채 「추가」 주식, 자기주식 「추가」 취득이 전부 걸렸다.
# 아래 규칙은 같은 코퍼스에서 2건(진성)만 물고, 비CASH 서식 표본 128건에서는 0건이다.
_SPECIAL_RE = re.compile(r"특별\s*(?:현금\s*|현물\s*)?배당|기념\s*배당")

# 남의 특별배당을 재원으로 쓴다는 문장은 이 회사의 특별배당이 아니다(20230403800799:
# 「한성피씨건설(주)가 … 실시한 특별배당 … 의 일부를 재원으로」).
# 🔴 근거가 이 1건뿐이다. 반례(당사를 「㈜○○」로 칭하며 특별배당을 알리는 서식)가 나오면 다시 재야 한다.
_SPECIAL_OTHERS_RE = re.compile(
    r"(?:\(주\)|㈜|주식회사)[^.]{0,40}특별\s*배당|특별\s*배당[^.]{0,60}재원"
)

# 옛 규칙은 「조원」만 봤다. 실제 문구는 「특별배당금 성격의 1,578원을 더하여」였고 아무것도 못 뽑았다.
_SPECIAL_AMOUNT_RE = re.compile(
    r"([\d][\d,]*(?:\.\d+)?)\s*(조원|억원|만원|원)\s*(?:을|를)?\s*(?:더하|추가|포함|합산)"
)


def safe_float(value) -> float:
    text = str(value).strip() if value is not None else ""
    if text in ("", "-", "N/A", "n/a", "－"):
        return 0.0
    negative = text.startswith("(") or (text.startswith("-") and len(text) > 1)
    numeric = re.sub(r"[^\d.]", "", text)
    if not numeric:
        return 0.0
    try:
        result = float(numeric)
    except ValueError:
        return 0.0
    for unit, multiplier in sorted(_UNIT_MULTIPLIERS.items(), key=lambda item: -len(item[0])):
        if unit in text:
            result *= multiplier
            break
    return -result if negative else result


def safe_int(value) -> int:
    return int(safe_float(value))


def parse_dividend_decision(text: str) -> dict | None:
    """Parse the standard exchange disclosure for a dividend decision."""
    if not text:
        return None
    clean = re.sub(r"\.xforms[^}]+\}", "", text).strip()
    clean = re.sub(r"\s+", " ", clean)
    result = {}

    match = re.search(r"1\.\s*배당구분\s*(결산배당|중간배당|분기배당)", clean)
    result["dividend_type"] = match.group(1) if match else None
    match = re.search(r"2\.\s*배당종류\s*(현금배당|현물배당|주식배당)", clean)
    result["dividend_method"] = match.group(1) if match else None
    match = re.search(r"3\.\s*1주당\s*배당금\s*\(원\)\s*보통주식\s*([\d,]+)", clean)
    result["dps_common"] = safe_int(match.group(1)) if match else 0

    dps_start = clean.find("1주당 배당금")
    dps_end = clean.find("4.", dps_start) if dps_start >= 0 else -1
    dps_segment = clean[dps_start:dps_end] if dps_start >= 0 and dps_end > dps_start else ""
    match = re.search(r"종류주식\s*([\d,]+)", dps_segment)
    result["dps_preferred"] = safe_int(match.group(1)) if match else 0
    match = re.search(r"차등배당\s*여부\s*(해당|미해당)", clean)
    result["differential_dividend"] = match.group(1) == "해당" if match else False

    match = re.search(r"4\.\s*시가배당율\s*\(%\)\s*보통주식\s*([\d.]+)", clean)
    result["yield_common"] = safe_float(match.group(1)) if match else 0.0
    match = re.search(r"4\.\s*시가배당율.*?종류주식\s*([\d.]+)", clean)
    result["yield_preferred"] = safe_float(match.group(1)) if match else 0.0
    match = re.search(r"5\.\s*배당금총액\s*\(원\)\s*([\d,]+)", clean)
    result["total_amount"] = safe_int(match.group(1)) if match else 0

    date_fields = (
        ("record_date", r"6\.\s*배당기준일\s*(\d{4}-\d{2}-\d{2})"),
        ("payment_date", r"7\.\s*배당금지급\s*예정일자\s*(\d{4}-\d{2}-\d{2})"),
        ("agm_date", r"9\.\s*주주총회\s*예정일자\s*(\d{4}-\d{2}-\d{2})"),
        ("board_date", r"10\.\s*이사회결의일\s*\(결정일\)\s*(\d{4}-\d{2}-\d{2})"),
    )
    for field, pattern in date_fields:
        match = re.search(pattern, clean)
        result[field] = match.group(1) if match else None
    match = re.search(r"8\.\s*주주총회\s*개최여부\s*(개최|미개최|미해당)", clean)
    result["agm_required"] = match.group(1) if match else None

    match = _REMARKS_RE.search(clean)
    remarks = match.group(1).strip() if match else ""
    result["remarks"] = remarks
    result["has_special"] = bool(
        _SPECIAL_RE.search(remarks) and not _SPECIAL_OTHERS_RE.search(remarks)
    )
    if result["has_special"]:
        match = _SPECIAL_AMOUNT_RE.search(remarks)
        if match:
            amount, unit = match.group(1), match.group(2)
            result["special_amount_description"] = f"{amount}{unit} 추가"
            # 「원」으로 더한 금액만 주당 특별배당금으로 본다. 조원·억원은 총액 문장이다.
            if unit == "원":
                result["special_dps_krw"] = safe_int(amount)

    preferred_stocks = []
    kind_match = re.search(r"【종류주식[^】]*】\s*(.*?)$", clean)
    if kind_match:
        rows = re.findall(
            r"(\S+)\s+(우선주|전환우선주|종류주식)\s+([\d,.]+)\s+([\d.]+|-)\s+([\d,.]+)",
            kind_match.group(1),
        )
        for raw_name, preferred_type, dps, dividend_yield, total_amount in rows:
            name = re.sub(r"^\(|\)$", "", raw_name)
            name = re.sub(r"^\d{6}\)?$", "", name)
            if not name:
                continue
            if re.search(r"\d우B", name) or re.search(r"2우선주|2우$", name):
                stock_class = "신형우선주"
            elif "전환" in preferred_type or "전환" in name or re.match(r"제?\d차", name):
                stock_class = "전환우선주"
            else:
                stock_class = "우선주"
            preferred_stocks.append({
                "name": name,
                "raw_type": preferred_type,
                "stock_class": stock_class,
                "dps": safe_int(dps),
                "yield_pct": safe_float(dividend_yield),
                "total_amount": safe_int(total_amount),
            })
    result["preferred_stocks"] = preferred_stocks
    if preferred_stocks:
        result["preferred_detail"] = preferred_stocks[0]
    if not result.get("dps_common") and not result.get("total_amount"):
        return None
    return result


def parse_dividend_items(data: dict) -> list[dict]:
    """Normalize an alotMatter API response into dividend rows."""
    results = []
    for item in data.get("list", []):
        category = item.get("se", "")
        results.append({
            "category": category,
            "stock_type": item.get("stock_knd", ""),
            "current": item.get("thstrm", ""),
            "previous": item.get("frmtrm", ""),
            "before_previous": item.get("lwfr", ""),
            "stlm_dt": item.get("stlm_dt", ""),
            "is_special": "특별" in category,
            "is_stock_dividend": "주식배당" in category or "주당 주식배당" in category,
        })
    return results


def share_class(stock_type) -> str:
    """alotMatter `stock_knd` 표기 → `common` / `preferred` / `class` / `unspecified`.

    🔴 「우선주」 글자가 없는 종류주식 표기가 실재한다 — 코스피 사업보고서 원장 실측(260906):
    「종류주식」107행·「종류주」73행·「기타주식」24행·「의결권 없는 주식」20행·「1종 종류주식」6행·
    「전환주」5행. 옛 규칙은 「우선주 아니면, 값이 있으면 보통주」라 그 행이 보통주 DPS 를
    **덮어썼다** — 한국금융지주 FY2024 보통 3,980 이 1종 종류주식 4,042 로, 두산 2,000 이
    종류주식 2,050 으로 나갔다(현재가 기준 배당수익률·`price_multiple_data` 배당수익률·history
    까지 같이 틀렸다). 보통주는 「보통」이 적힌 행이거나, 종류 칸을 비운(`-`) 회사의 그 행이다.
    """
    s = re.sub(r"\s+", "", str(stock_type or ""))
    if not s or s in {"-", "－", "해당없음", "미구분"}:
        return "unspecified"
    if "우선" in s:
        return "preferred"
    if "보통" in s or "보동" in s or "의결권있는" in s:
        return "common"
    return "class"


def split_by_share_class(rows: list[tuple]) -> tuple:
    """`[(stock_knd 표기, 값)]` → `(보통주 값, 그 밖 종류 값, 그 밖 종류 라벨)`. 없으면 None.

    · 「보통」 표기 행이 있으면 그것이 보통주. 없으면 종류 칸을 비운(`-`) 행. 그것도 없고
      종류 표기 행뿐이면(「우량주」처럼 표기만 다른 단일 종류) 그 행이 보통주다.
    · 우선주·종류주식 행은 「그 밖」 버킷 — 여러 줄이면 마지막 줄이 남는다(종전과 같다).
      라벨은 원문 표기 그대로(`우선주` / `1종 종류주식` …) — 렌더가 「우선주」로 뭉뚱그리지 않게.
    · 0·빈 값은 버킷을 덮지 않는다 — 서식이 줄 수를 맞추려 남긴 빈 줄이다.
    사업보고서 기말 요약(`build_dividend_summary`)과 다년 컬럼 history(`dividend._annual_summary`)가
    **같은 규칙**을 쓴다 — 갈라 두면 요약과 추이가 서로 다른 보통주 값을 낸다.
    """
    common = other = None
    labels: list[str] = []
    saw_common = False
    positive = [(t, v) for t, v in rows if v is not None and v > 0]
    for stock_type, value in positive:
        cls = share_class(stock_type)
        if cls == "common":
            common, saw_common = value, True
        elif cls == "unspecified":
            if not saw_common:
                common = value
        else:
            other = value
            label = str(stock_type or "").strip()
            if label and label not in labels:
                labels.append(label)
    if common is None and other is not None and all(share_class(t) == "class" for t, _ in positive):
        common, other, labels = other, None, []
    return common, other, "·".join(labels)


def build_dividend_summary(items: list[dict], report_label: str) -> dict:
    """Build a normalized annual summary from alotMatter rows."""
    stock_dps = special_dps = total_amount = 0
    payout_ratio = None
    net_income = 0
    settlement_date = ""
    dps_rows: list[tuple] = []
    yield_rows: list[tuple] = []
    for item in items:
        category = item.get("category", "")
        current = item.get("current", "")
        stock_type = item.get("stock_type", "")
        settlement_date = settlement_date or item.get("stlm_dt", "")
        if "주당 현금배당금" in category or ("주당" in category and "현금배당금" in category):
            value = safe_int(current)
            if item.get("is_special"):
                special_dps += value
            else:
                dps_rows.append((stock_type, value))
        if "주당 주식배당" in category:
            stock_dps = safe_int(current)
        if "현금배당금총액" in category:
            total_amount = safe_int(current)
        if "현금배당성향" in category and ("연결" in category or payout_ratio is None):
            value = safe_float(current)
            if value > 0:
                payout_ratio = value
        if "현금배당수익률" in category:
            yield_rows.append((stock_type, safe_float(current)))
        if "연결" in category and "당기순이익" in category:
            net_income = safe_int(current)
    # 주당값은 종류별로 가른다 — 「우선주」 글자 없는 종류주식이 보통주를 덮지 않게(share_class 참조).
    _c, _o, preferred_label = split_by_share_class(dps_rows)
    cash_dps, cash_dps_preferred = _c or 0, _o or 0
    dividend_yield, preferred_yield, _ = split_by_share_class(yield_rows)

    par_current = par_previous = 0
    for item in items:
        if "액면가" in item.get("category", ""):
            par_current = safe_int(item.get("current", ""))
            par_previous = safe_int(item.get("previous", ""))
            break
    return {
        "period": report_label,
        "stlm_dt": settlement_date,
        "cash_dps": cash_dps,
        "cash_dps_preferred": cash_dps_preferred,
        "cash_dps_preferred_label": preferred_label,   # 원문 표기(우선주·1종 종류주식 …) — 렌더 라벨
        "stock_dps": stock_dps,
        "special_dps": special_dps,
        "total_dps": cash_dps + special_dps,
        "total_amount_mil": total_amount,
        "payout_ratio_dart": payout_ratio,
        "yield_dart": dividend_yield,
        "yield_preferred_dart": preferred_yield,
        "net_income_consolidated_mil": net_income,
        "par_value_current": par_current,
        "par_value_previous": par_previous,
        "items": items,
    }
