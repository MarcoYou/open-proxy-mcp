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

    match = re.search(r"11\.\s*기타\s*투자판단과\s*관련한\s*중요사항\s*(.*?)(?:※|【|\Z)", clean)
    remarks = match.group(1).strip() if match else ""
    result["remarks"] = remarks
    result["has_special"] = bool(re.search(r"특별|추가.*배당|추가하여", remarks))
    if result["has_special"]:
        match = re.search(r"([\d,.]+)\s*조원을?\s*추가", remarks)
        if match:
            result["special_amount_description"] = f"{match.group(1)}조원 추가"

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


def build_dividend_summary(items: list[dict], report_label: str) -> dict:
    """Build a normalized annual summary from alotMatter rows."""
    cash_dps = cash_dps_preferred = stock_dps = special_dps = total_amount = 0
    payout_ratio = dividend_yield = preferred_yield = None
    net_income = 0
    settlement_date = ""
    for item in items:
        category = item.get("category", "")
        current = item.get("current", "")
        stock_type = item.get("stock_type", "")
        settlement_date = settlement_date or item.get("stlm_dt", "")
        if "주당 현금배당금" in category or ("주당" in category and "현금배당금" in category):
            value = safe_int(current)
            if item.get("is_special"):
                special_dps += value
            elif "우선주" in stock_type:
                cash_dps_preferred = value
            elif "보통주" in stock_type or value > 0:
                cash_dps = value
        if "주당 주식배당" in category:
            stock_dps = safe_int(current)
        if "현금배당금총액" in category:
            total_amount = safe_int(current)
        if "현금배당성향" in category and ("연결" in category or payout_ratio is None):
            value = safe_float(current)
            if value > 0:
                payout_ratio = value
        if "현금배당수익률" in category:
            value = safe_float(current)
            if value > 0:
                if "우선주" in stock_type:
                    preferred_yield = value
                else:
                    dividend_yield = value
        if "연결" in category and "당기순이익" in category:
            net_income = safe_int(current)

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
