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
#     (261019 다시 잼: 캐시 정정 원문 250건 전부 본문 머리가 **한 번뿐**이고 「정정사항」 표가 그 앞에 있다.
#      끌려 들어온 것은 표의 11번 행 — 정정전·정정후 비고 — 이었다. 이제 정정 공시는 본문만 읽으므로
#      (`_split_amendment`) 이 경계는 서식이 다른 원문을 위한 안전장치로 남는다.)
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


# 날짜 칸 — 하이픈(2026-02-24)만 보던 것을 점(2026.02.24)·빗금·「년 월」 표기까지 받는다. 결과는 늘 YYYY-MM-DD.
# 🔴 261019 — 정정사항 표가 점 날짜로 적힌 정정(엠앤씨솔루션 20260204801233 「10. 이사회결의일(결정일) 2025.02.04
#   2026.02.04」)에서 하이픈만 보던 정규식이 표를 건너뛰어 정정전 결의일을 잃었다. 본문 칸도 같은 규칙을 쓴다.
_DATE_SRC = r"(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})(?!\d)"


def _iso_date(match: re.Match | None, first_group: int = 1) -> str | None:
    """정규식 일치(연·월·일 세 묶음) → YYYY-MM-DD. 달·날이 말이 안 되면 None."""
    if not match:
        return None
    y, m, d = (int(match.group(first_group + i)) for i in range(3))
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return None
    return f"{y:04d}-{m:02d}-{d:02d}"


# 정정 공시 — 머리(정정일자 · 1. 정정관련 공시서류 · 2. 정정관련 공시서류제출일 · 3. 정정사유) 뒤에
#   「4. 정정사항」 표(정정항목 · 정정전 · 정정후)가 오고, 그 **뒤에** 정정 후 본문(「현금ㆍ현물배당 결정 1. 배당구분 …」)이 온다.
# 🔴 261019 — 칸마다 첫 일치를 잡던 파서가 표의 **정정전 값**을 읽었다. 표에 오른 칸(배당금총액·배당기준일·결의일…)만
#   정정전 값으로 나가고 나머지는 본문 값이라, 정정본이 정본이 되는 자리(중복 제거 뒤 남는 쪽)에 고치기 전 숫자가 섰다.
#   실측(맥미니 캐시 정정 원문 250건, 고치기 전 전문 vs 본문): 112건에서 핵심 칸이 갈렸다 — 배당금총액 74 · 결의일 15 ·
#   주총일 15 · 지급일 13 · 기준일 8 · 1주당 3 · 구분 2(비고까지 넣으면 174건). 원 공시 4,377건은 전 칸 같다.
#   동화약품 20260224801063 은 기준일 2025-03-25 → 2026-03-25 (「단순 착오」) 정정인데 2025-03-25 를 읽어
#   FY2025 결산배당이 FY2024 로 갔다. 이제 값은 본문에서만 읽고, 표는 원 공시를 찾는 열쇠(정정전 결의일 ·
#   배당구분 · 기준일)와 정정 항목 메타로만 쓴다.
# 본문 머리는 서식 이름부터 잡는다 — 표에도 「1. 배당구분」 행이 있을 수 있다(한온시스템 20210209800389: 분기배당 → 결산배당).
_BODY_HEAD_RE = re.compile(
    r"(?:현금\s*ㆍ?\s*현물\s*배당|주식\s*배당|분기\s*ㆍ?\s*중간\s*배당)\s*결정\s*1\.\s*배당구분"
)
_AMEND_TABLE_RE = re.compile(r"정정\s*사항(?=.{0,40}?정정\s*전.{0,20}?정정\s*후)")
_AMEND_REASON_RE = re.compile(r"정정\s*사유\s*:?\s*(.*?)\s*(?:\d{1,2}\s*\.\s*)?$")
_AMEND_FILED_RE = re.compile(r"정정\s*관련\s*공시\s*서류\s*제출\s*일\s*:?\s*" + _DATE_SRC)
_AMEND_BOARD_RE = re.compile(r"이사회\s*결의일\s*(?:\(\s*결정일\s*\))?\s*" + _DATE_SRC)
_AMEND_RECORD_RE = re.compile(r"배당\s*기준일\s*" + _DATE_SRC)
_AMEND_TYPE_RE = re.compile(r"배당\s*구분\s*(결산배당|중간배당|분기배당)")
# 표가 금액 행을 「숫자 → -」로 고쳤나(결정 취소 정정의 모양).
_AMEND_CLEARED_RE = re.compile(
    r"(?:1\s*주당\s*배당금\s*\(\s*원\s*\)\s*(?:보통\s*주식\s*)?|배당금\s*총액\s*\(\s*원\s*\)\s*)[\d,]+\s*[-－](?!\d)"
)
# 표의 행 이름 → 이 파서의 칸 이름. 11번(기타 투자판단) 행 뒤는 정정전·정정후 비고 글이라 거기서 멈춘다
#   (실측 250건 중 16건에서 그 글에 「4. 주주총회 예정일자는 …」 같은 번호 문장이 이어진다). 종류주식 묶음도 그 안의
#   「ㆍ배당금총액(원)」 같은 하위 줄을 행으로 세지 않게 거기서 멈춘다.
_AMEND_ITEMS = (
    ("dividend_type", r"배당\s*구분"),
    ("dividend_method", r"배당\s*(?:의\s*)?종류"),
    ("dps", r"1\s*주당\s*배당금"),
    ("yield", r"시가\s*배당\s*[율률]"),
    ("total_amount", r"배당[금급]?\s*총액"),
    ("record_date", r"배당\s*기준일"),
    ("payment_date", r"배당금?\s*지급\s*예정\s*일"),
    ("agm_required", r"주주\s*총회\s*개최\s*여부"),
    ("agm_date", r"주주\s*총회\s*예정\s*일"),
    ("board_date", r"이사회\s*결의일"),
    ("differential_dividend", r"차등\s*배당\s*여부"),
    ("attendance", r"사외\s*이사\s*참석|감사\s*\(\s*사외\s*이사가\s*아닌"),
    ("preferred_stocks", r"【\s*종류\s*주식에\s*대한\s*배당"),
    ("remarks", r"기타\s*투자\s*판[단관]"),
)
_AMEND_ITEM_RE = re.compile(
    r"(?:^|\s)(?:\d{1,2}\s*\.\s*)?(?:"
    + "|".join(f"(?P<{name}>{pattern})" for name, pattern in _AMEND_ITEMS)
    + ")"
)


def _split_amendment(clean: str) -> tuple[str | None, str, bool]:
    """(정정 머리+표, 값을 읽을 본문, 본문을 찾았나). 정정 공시가 아니면 (None, 전문, True).

    정정 공시인데 본문 머리를 못 찾으면 전문을 그대로 돌려준다(종전 동작) — 값이 정정전일 수 있다는 표시는
    `amendment.values_from` 이 나른다. 캐시 실측 250건에서는 0건이다.
    """
    table = _AMEND_TABLE_RE.search(clean)
    if not table:
        return None, clean, True
    head = _BODY_HEAD_RE.search(clean, table.end())
    if not head:
        return clean, clean, False
    return clean[: head.start()], clean[head.start():], True


def _amendment_meta(pre: str, body_found: bool) -> dict:
    """정정 머리·「정정사항」 표에서 원 공시를 찾는 열쇠와 정정 항목을 뽑는다 — **값은 여기서 안 읽는다.**

    · `board_date_before` — 표의 이사회결의일 정정전 값. 결의일을 고친 정정(SK 20260210: 2025-02-10 → 2026-02-10)도
      원 결의와 묶이게 한다. 표에 그 행이 없으면 None(= 결의일은 안 고쳤다, 본문 값이 곧 원 공시 값).
    · `record_date_before` · `dividend_type_before` — 같은 날 결의가 둘일 때 원 공시를 가르는 보조 열쇠.
    · `original_filed` — 「정정관련 공시서류제출일」(원 공시 접수일).
    · `items` — 표에 오른 행(이 파서의 칸 이름). 정보용이다.
    """
    table_at = _AMEND_TABLE_RE.search(pre)
    header = pre[: table_at.start()] if table_at else pre
    table = pre[table_at.start():] if table_at else ""
    reason = _AMEND_REASON_RE.search(header)
    items: list[str] = []
    for match in _AMEND_ITEM_RE.finditer(table):
        name = match.lastgroup
        if name and name not in items:
            items.append(name)
        if name in ("remarks", "preferred_stocks"):
            break
    dividend_type = _AMEND_TYPE_RE.search(table)
    return {
        "values_from": "정정후 본문" if body_found else "전문(본문 머리 못 찾음 — 정정전 값일 수 있다)",
        "reason": (reason.group(1).strip()[:200] or None) if reason else None,
        "original_filed": _iso_date(_AMEND_FILED_RE.search(header)),
        "board_date_before": _iso_date(_AMEND_BOARD_RE.search(table)),
        "record_date_before": _iso_date(_AMEND_RECORD_RE.search(table)),
        "dividend_type_before": dividend_type.group(1) if dividend_type else None,
        "items": items,
    }


def parse_dividend_decision(text: str) -> dict | None:
    """Parse the standard exchange disclosure for a dividend decision.

    정정 공시면 값은 정정 후 본문에서 읽고, 앞의 「정정사항」 표는 `amendment` 메타로만 낸다(`_split_amendment`).
    """
    if not text:
        return None
    clean = re.sub(r"\.xforms[^}]+\}", "", text).strip()
    clean = re.sub(r"\s+", " ", clean)
    pre, clean, body_found = _split_amendment(clean)
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
        ("record_date", r"6\.\s*배당기준일\s*"),
        ("payment_date", r"7\.\s*배당금지급\s*예정일자\s*"),
        ("agm_date", r"9\.\s*주주총회\s*예정일자\s*"),
        ("board_date", r"10\.\s*이사회결의일\s*\(결정일\)\s*"),
    )
    for field, label in date_fields:
        result[field] = _iso_date(re.search(label + _DATE_SRC, clean))
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
        # 정정 후 본문에 금액이 없고 표가 금액을 「-」로 지웠으면 **결정 취소 정정**이다(웅진씽크빅 20200508800742:
        #   「배당기준일 명부확정 절차를 사전 진행하지 못해 … 배당금 지급 결정 취소」 — 1주당 20 → -, 총액 → -).
        #   종전엔 표의 정정전 값(20원)을 읽어 취소된 배당이 지급으로 남았다. 빈 결과(None)로 버리면 원 공시가
        #   대체되지 않으므로 취소 표시를 단 행으로 낸다. 그 밖의 빈 본문(서식이 다른 것)은 종전처럼 None.
        if pre is not None and body_found and _AMEND_CLEARED_RE.search(pre):
            result["amendment"] = {**_amendment_meta(pre, body_found), "cancelled": True}
            return result
        return None
    if pre is not None:
        result["amendment"] = _amendment_meta(pre, body_found)
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
            # 261019: 그 행이 나온 사업보고서 접수번호(앞 8자리 = 접수일) — 접수일 뒤의 주식 분할·병합은 그 DPS 에 반영됐을 수 없다
            #   (`dividend.align_dps_to_price`). 반기·분기 결산 회사는 한 응답에 결산기마다 다른 보고서 행이 섞여 온다.
            "rcept_no": item.get("rcept_no", ""),
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


def settlement_digits(item: dict) -> str:
    """alotMatter 행의 결산기준일 → `YYYYMMDD`. 없거나 날짜가 아니면 빈 문자열."""
    d = "".join(ch for ch in str(item.get("stlm_dt") or "") if ch.isdigit())[:8]
    return d if len(d) == 8 else ""


def latest_period_items(items: list[dict]) -> list[dict]:
    """결산기가 둘 이상 섞인 응답에서 **가장 늦은 결산기 행**만 — 결산기준일이 빈 행은 남긴다. 결산기가 하나면 그대로. (261021)

    반기·분기 결산 회사(리츠 등)는 사업연도 하나에 사업보고서가 결산기마다 따로 나와, 사업보고서 `alotMatter` 한 번에 결산기 여럿이
    `stlm_dt` 로 갈라져 온다(롯데리츠 FY2025 = 2025-06-30 행 · 2025-12-31 행). 종전 요약은 결산기준일을 **첫 행**에서, 주당값·총액을 **마지막 행**에서
    읽어 한 요약 안에서 기준일(2025-06-30)과 DPS(123원·2025-12-31 것)·총액이 서로 다른 결산기였다."""
    dates = {d for d in (settlement_digits(i) for i in items) if d}
    if len(dates) < 2:
        return items
    latest = max(dates)
    return [i for i in items if settlement_digits(i) in ("", latest)]


def build_dividend_summary(items: list[dict], report_label: str) -> dict:
    """Build a normalized annual summary from alotMatter rows.

    결산기가 둘 이상 섞인 응답(반기·분기 결산 리츠)은 **가장 늦은 결산기 행**에서 기준일·주당값·총액을 같이 읽는다
    (`latest_period_items`). 결산기별 값과 사업연도 합은 `dividend.settlement_periods` 가 `items`(원문 전체)에서 만든다."""
    stock_dps = special_dps = total_amount = 0
    payout_ratio = None
    net_income = 0
    settlement_date = ""
    dps_rows: list[tuple] = []
    yield_rows: list[tuple] = []
    all_items = items
    items = latest_period_items(items)
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
        "items": all_items,
    }
