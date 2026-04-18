"""포매터 + 파서 유틸리티 — shareholder.py/ownership.py에서 분리"""

import re
import json
from collections import Counter


# ── 기업 식별 자동 변환 ──

async def resolve_to_ticker(query: str) -> str:
    """어떤 입력이든 종목코드 6자리로 변환.

    지원 입력: 회사명("삼성전자"), 약칭("KT&G"), 영문명("LS ELECTRIC"), 종목코드("005930").
    종목코드 6자리가 이미 들어오면 그대로 반환.
    매칭 실패 시 원본 반환 (downstream tool에서 에러 처리).
    기업 식별이 불확실하면 corp_identifier tool을 먼저 호출할 것.
    """
    query = query.strip()
    if re.match(r'^\d{6}$', query):
        return query
    from open_proxy_mcp.dart.client import get_dart_client
    client = get_dart_client()
    result = await client.lookup_corp_code(query)
    if result and result.get("stock_code"):
        return result["stock_code"]
    return query


# backward compat alias
resolve_ticker = resolve_to_ticker


# ── HTML/CSS 제거 ──

def strip_css(text: str) -> str:
    """HTML + CSS 제거하고 텍스트만 추출 (DART xforms 문서용)"""
    text = re.sub(r'\.xforms[^}]*\}', '', text)
    text = re.sub(r'<[^>]+>', '\n', text)
    text = re.sub(r'\n\s*\n+', '\n', text)
    return text.strip()


# ── 숫자 파싱 유틸 ──

# 단위 → 원(KRW) 배수 (dart-fss 참고)
_KR_UNIT_MULTIPLIER: dict[str, int] = {
    "조원": 1_000_000_000_000,
    "천억원": 100_000_000_000,
    "백억원": 10_000_000_000,
    "십억원": 1_000_000_000,
    "억원": 100_000_000,
    "천만원": 10_000_000,
    "백만원": 1_000_000,
    "십만원": 100_000,
    "만원": 10_000,
    "천원": 1_000,
    "백원": 100,
    "원": 1,
    # 단축 표기
    "조": 1_000_000_000_000,
    "억": 100_000_000,
    "만": 10_000,
    "천": 1_000,
    "백만": 1_000_000,
}


def parse_kr_number(text: str, unit: str = "") -> float:
    """한국 재무제표 숫자 문자열 → float 변환.

    처리 케이스:
    - 괄호 음수: (1,234,567) → -1234567.0
    - 단위 반영: "1,234" + unit="백만원" → 1234000000.0
    - 단위 내장: "1,234억원" → 123400000000.0 (text 안에 단위 포함 시)
    - 쉼표/공백: "1, 234 " → 1234.0
    - 빈값/대시: "", "-", "N/A" → 0.0
    """
    if not text:
        return 0.0
    s = str(text).strip()
    if s in ("", "-", "N/A", "n/a", "－"):
        return 0.0

    # 음수 감지 (괄호만. △는 문맥에 따라 변동/감소 의미라 음수 처리 안 함)
    is_negative = s.startswith("(") or (s.startswith("-") and len(s) > 1)

    # text 안에 단위 내장된 경우 multiplier 추출
    text_multiplier = 1
    if not unit:
        for k, v in sorted(_KR_UNIT_MULTIPLIER.items(), key=lambda x: -len(x[0])):
            if k in s:
                text_multiplier = v
                break

    # 숫자만 추출 (소수점 포함)
    num_str = re.sub(r"[^\d.]", "", s)
    if not num_str:
        return 0.0

    try:
        value = float(num_str)
    except ValueError:
        return 0.0

    # 단위 배수 적용 (명시적 unit 우선, 없으면 text 내장 단위)
    unit_multiplier = 1
    if unit:
        unit_clean = re.sub(r"\s+", "", unit)
        for k, v in sorted(_KR_UNIT_MULTIPLIER.items(), key=lambda x: -len(x[0])):
            if k in unit_clean:
                unit_multiplier = v
                break
    else:
        unit_multiplier = text_multiplier

    value = value * unit_multiplier
    return -value if is_negative else value


def parse_kr_int(text: str, unit: str = "") -> int:
    """parse_kr_number의 int 버전"""
    return int(parse_kr_number(text, unit))


# ── 통화 포매터 (shareholder.py에서 이동) ──

def format_krw(raw_value: str, unit: str = "") -> str:
    """숫자 문자열 + 단위를 사람이 읽기 좋은 한국 원화 형태로 변환

    Args:
        raw_value: "3,049,040" 또는 "(477,528)" 등
        unit: "백만원", "천원", "원" 등

    Returns:
        "약 3.05조원", "약 4,775억원", "3,000만원" 등
        변환 실패 시 원본 반환
    """
    if not raw_value or raw_value.strip() in ('', '-'):
        return raw_value

    value = parse_kr_number(raw_value, unit)
    if value == 0.0 and re.sub(r'[^\d]', '', raw_value) == '':
        return raw_value

    return _format_won(int(value))


def _format_won(value: int) -> str:
    """원 단위 정수를 읽기 좋은 형태로"""
    abs_val = abs(value)
    sign = "-" if value < 0 else ""

    if abs_val >= 1_000_000_000_000:  # 조
        v = abs_val / 1_000_000_000_000
        if v >= 10:
            return f"약 {sign}{v:.1f}조원"
        return f"약 {sign}{v:.2f}조원"
    elif abs_val >= 100_000_000_000:  # 천억 이상
        v = abs_val / 100_000_000
        return f"약 {sign}{v:,.0f}억원"
    elif abs_val >= 100_000_000:  # 억
        v = abs_val / 100_000_000
        if v >= 10:
            return f"약 {sign}{v:.1f}억원"
        return f"약 {sign}{v:.2f}억원"
    elif abs_val >= 10_000_000:  # 천만
        v = abs_val / 10_000
        return f"{sign}{v:,.0f}만원"
    elif abs_val >= 10_000:  # 만
        v = abs_val / 10_000
        if v == int(v):
            return f"{sign}{int(v):,}만원"
        return f"{sign}{v:,.1f}만원"
    else:
        return f"{sign}{abs_val:,}원"


def highlights_has(highlights: list, label: str) -> bool:
    return any(h["label"] == label for h in highlights)


# ── AGM 포매터 (shareholder.py에서 이동) ──

def _format_agenda_tree(items: list[dict]) -> str:
    """안건 트리를 마크다운으로 포매팅"""
    if not items:
        return "안건을 파싱할 수 없습니다."

    # 루트 안건 수 (하위 제외)
    total = len(items)
    lines = [f"## 의안 목록 (총 {total}건)", ""]

    for item in items:
        lines.append(f"### {item['number']}: {item['title']}")
        meta = []
        if item.get("category"):
            meta.append(f"카테고리: {item['category']}")
        if item.get("source"):
            meta.append(f"출처: {item['source']}")
        if meta:
            lines.append(f"- {' | '.join(meta)}")
        if item.get("conditional"):
            lines.append(f"- ※ {item['conditional']}")

        # 하위 안건
        for child in item.get("children", []):
            lines.append(f"  - **{child['number']}**: {child['title']}")
            if child.get("source"):
                lines.append(f"    - 출처: {child['source']}")
            if child.get("conditional"):
                lines.append(f"    - ※ {child['conditional']}")
            # 3단계
            for gc in child.get("children", []):
                lines.append(f"    - **{gc['number']}**: {gc['title']}")
                if gc.get("conditional"):
                    lines.append(f"      - ※ {gc['conditional']}")

        lines.append("")

    return "\n".join(lines)


def _format_meeting_info(info: dict) -> str:
    """비안건 정보를 마크다운으로 포매팅"""
    lines = ["## 주주총회 개요", ""]

    # 유형
    type_str = ""
    if info.get("meeting_term"):
        type_str += info["meeting_term"] + " "
    if info.get("meeting_type"):
        type_str += info["meeting_type"] + "주주총회"
    if type_str:
        lines.append(f"- **유형**: {type_str}")

    if info.get("is_correction"):
        lines.append("- **정정공고**: 예")
        cs = info.get("correction_summary")
        if cs:
            if cs.get("date"):
                lines.append(f"- **정정일**: {cs['date']}")
            if cs.get("original_date"):
                lines.append(f"- **최초제출일**: {cs['original_date']}")
            if cs.get("items"):
                lines.append("- **정정 항목**:")
                for item in cs["items"]:
                    lines.append(f"  - {item['section']} — {item['reason']}")

    if info.get("datetime"):
        lines.append(f"- **일시**: {info['datetime']}")
    if info.get("location"):
        lines.append(f"- **장소**: {info['location']}")

    # 보고사항
    if info.get("report_items"):
        lines.append("")
        lines.append("## 보고사항")
        for item in info["report_items"]:
            lines.append(f"- {item}")

    # 전자투표
    if info.get("electronic_voting"):
        lines.append("")
        lines.append("## 전자투표")
        lines.append(info["electronic_voting"])

    # 의결권 행사
    if info.get("proxy_voting"):
        lines.append("")
        lines.append("## 의결권 행사 방법")
        lines.append(info["proxy_voting"])

    # 온라인 중계
    if info.get("online_broadcast"):
        lines.append("")
        lines.append("## 온라인 중계")
        lines.append(info["online_broadcast"])

    # 경영참고사항 비치
    if info.get("reference_materials"):
        lines.append("")
        lines.append("## 경영참고사항 비치")
        lines.append(info["reference_materials"])

    # 문서 목차
    if info.get("toc"):
        lines.append("")
        lines.append("## 문서 목차")
        for item in info["toc"]:
            lines.append(f"- {item}")

    return "\n".join(lines)


def _format_agenda_details(details: list[dict]) -> str:
    """안건 상세를 마크다운으로 포매팅"""
    lines = []
    for agenda in details:
        lines.append(f"## {agenda['number']}: {agenda['title']}")
        if agenda.get("category"):
            lines.append(f"*카테고리: {agenda['category']}*")
        lines.append("")

        for sec in agenda.get("sections", []):
            if sec.get("heading"):
                lines.append(f"### {sec['heading']}")
                lines.append("")

            for block in sec.get("blocks", []):
                content = block["content"]
                if block["type"] == "table":
                    lines.append(content)
                    lines.append("")
                elif block["type"] == "note":
                    lines.append(f"> {content}")
                    lines.append("")
                else:
                    lines.append(content)
                    lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def _format_financial_statements(result: dict) -> str:
    """재무제표를 마크다운으로 포매팅"""
    lines = []
    stmt_names = {
        "balance_sheet": "재무상태표",
        "income_statement": "손익계산서",
    }
    scope_names = {
        "consolidated": "연결",
        "separate": "별도",
    }

    for scope in ["consolidated", "separate"]:
        for stmt in ["balance_sheet", "income_statement"]:
            entry = result[scope][stmt]
            if entry is None:
                continue

            title = f"{scope_names[scope]} {stmt_names[stmt]}"
            unit = entry.get("unit", "")
            periods = entry.get("period_labels", {})

            lines.append(f"## {title}")
            if unit:
                lines.append(f"*(단위: {unit})*")
            lines.append("")

            # 마크다운 테이블 — 주석 컬럼 유무에 따라 동적
            cols = entry.get("columns", [])
            has_note = "note" in cols
            if has_note:
                header = ["과목", "주석", periods.get("current", "당기"), periods.get("prior", "전기")]
            else:
                header = ["과목", periods.get("current", "당기"), periods.get("prior", "전기")]
            col_count = len(header)

            lines.append("| " + " | ".join(header) + " |")
            lines.append("| " + " | ".join("---" for _ in header) + " |")

            for row in entry.get("rows", []):
                padded = row[:col_count] if len(row) >= col_count else row + [""] * (col_count - len(row))
                escaped = [c.replace("|", "\\|") for c in padded]
                lines.append("| " + " | ".join(escaped) + " |")

            lines.append("")

    # 자본변동표
    for scope in ["consolidated", "separate"]:
        eq = result[scope].get("equity_changes")
        if eq is None:
            continue
        title = f"{scope_names[scope]} 자본변동표"
        flags = []
        if eq.get("has_treasury_acquisition"): flags.append("자사주 취득")
        if eq.get("has_treasury_disposal"): flags.append("자사주 소각/처분")
        flag_str = f" ({', '.join(flags)})" if flags else ""

        lines.append(f"## {title}{flag_str}")
        if eq.get("unit"):
            lines.append(f"*(단위: {eq['unit']})*")
        lines.append("")

        cols = eq.get("columns", [])
        lines.append("| " + " | ".join(c.replace("|", "\\|")[:15] for c in cols) + " |")
        lines.append("| " + " | ".join("---" for _ in cols) + " |")
        for row in eq.get("rows", []):
            padded = row[:len(cols)] if len(row) >= len(cols) else row + [""] * (len(cols) - len(row))
            escaped = [c.replace("|", "\\|")[:15] for c in padded]
            lines.append("| " + " | ".join(escaped) + " |")
        lines.append("")

    # 이익잉여금처분계산서
    re_stmt = result.get("retained_earnings")
    if re_stmt:
        has_div = re_stmt.get("has_dividend", False)
        lines.append(f"## 이익잉여금처분계산서 {'(배당 실시)' if has_div else '(배당 미실시)'}")
        if re_stmt.get("unit"):
            lines.append(f"*(단위: {re_stmt['unit']})*")
        if re_stmt.get("disposal_date"):
            lines.append(f"*처분예정일: {re_stmt['disposal_date']}*")
        lines.append("")

        lines.append("| 항목 | 당기 | 전기 |")
        lines.append("| --- | --- | --- |")
        for item in re_stmt.get("items", []):
            acct = item["account"].replace("|", "\\|")[:60]
            lines.append(f"| {acct} | {item['current']} | {item['prior']} |")
        lines.append("")

    return "\n".join(lines)


def _build_financial_highlight(fs: dict) -> list[dict] | None:
    """재무제표에서 핵심 지표를 format_krw로 변환하여 추출"""
    highlights = []

    # 연결 우선, 없으면 별도
    scope = "consolidated" if fs["consolidated"]["balance_sheet"] else "separate"

    bs = fs[scope].get("balance_sheet")
    is_stmt = fs[scope].get("income_statement")
    unit = ""

    # 재무상태표 하이라이트
    if bs:
        unit = bs.get("unit", "")
        for row in bs.get("rows", []):
            acct = row[0].replace(" ", "")
            val_idx = 2 if len(row) > 3 else 1  # note 있으면 [2], 없으면 [1]
            val = row[val_idx] if val_idx < len(row) else ""
            if '자산총계' in acct and val:
                highlights.append({"label": "자산총계", "value": format_krw(val, unit)})
            elif '부채총계' in acct and val and not highlights_has(highlights, '부채총계'):
                highlights.append({"label": "부채총계", "value": format_krw(val, unit)})
            elif '자본총계' in acct and '부채' not in acct and val and not highlights_has(highlights, '자본총계'):
                highlights.append({"label": "자본총계", "value": format_krw(val, unit)})

    # 손익계산서 하이라이트
    if is_stmt:
        unit = is_stmt.get("unit", "")
        for row in is_stmt.get("rows", []):
            acct = row[0].replace(" ", "")
            val_idx = 2 if len(row) > 3 else 1
            val = row[val_idx] if val_idx < len(row) else ""
            if ('매출' in acct and '액' in acct or acct.startswith('매출')) and '원가' not in acct and '총' not in acct and val and not highlights_has(highlights, '매출'):
                highlights.append({"label": "매출", "value": format_krw(val, unit)})
            elif '영업이익' in acct and val and not highlights_has(highlights, '영업이익'):
                highlights.append({"label": "영업이익", "value": format_krw(val, unit)})
            elif '당기순이익' == acct and val:
                highlights.append({"label": "당기순이익", "value": format_krw(val, unit)})

    # 배당 하이라이트
    re_stmt = fs.get("retained_earnings")
    if re_stmt and re_stmt.get("has_dividend"):
        unit = re_stmt.get("unit", "")
        for item in re_stmt.get("items", []):
            if '배당금' in item["account"] or '현금배당' in item["account"]:
                if item["current"]:
                    # 처분계산서에서 배당은 음수로 표시되므로 절대값
                    raw = item["current"].replace("(", "").replace(")", "").replace("-", "")
                    highlights.append({
                        "label": "배당금",
                        "value": format_krw(raw, unit),
                    })
                # 주당배당금 추출
                m = re.search(r'(\d[\d,]*)\s*원\s*\(', item["account"])
                if m:
                    highlights.append({
                        "label": "주당배당금",
                        "value": f"{m.group(1)}원",
                    })
                break
    elif re_stmt and not re_stmt.get("has_dividend"):
        highlights.append({"label": "배당", "value": "미실시"})

    # 자사주 하이라이트
    for s in ["consolidated", "separate"]:
        eq = fs[s].get("equity_changes")
        if eq:
            flags = []
            if eq.get("has_treasury_acquisition"):
                flags.append("취득")
            if eq.get("has_treasury_disposal"):
                flags.append("소각/처분")
            if flags:
                highlights.append({"label": "자사주", "value": ", ".join(flags)})
            break

    return highlights if highlights else None


def _format_compensation(result: dict) -> str:
    """보수한도를 마크다운으로 포매팅"""
    lines = ["## 보수한도 승인", ""]
    s = result.get("summary", {})

    # 요약
    if s.get("currentTotalLimit"):
        lines.append(f"**당기 한도 총액**: {_format_won(s['currentTotalLimit'])}")
    if s.get("priorTotalPaid"):
        lines.append(f"**전기 실제 지급**: {_format_won(s['priorTotalPaid'])}")
    if s.get("priorTotalLimit"):
        lines.append(f"**전기 한도**: {_format_won(s['priorTotalLimit'])}")
    if s.get("priorUtilization") is not None:
        lines.append(f"**전기 한도 소진율**: {s['priorUtilization']}%")
    lines.append("")

    for item in result.get("items", []):
        lines.append(f"### {item['number']}: {item['title']}")
        lines.append("")

        cur = item.get("current", {})
        pri = item.get("prior", {})

        if cur:
            lines.append("**당기**")
            if cur.get("headcount"):
                lines.append(f"- 이사의 수 (사외이사수): {cur['headcount']}")
            if cur.get("limit"):
                lines.append(f"- 보수 최고한도액: {cur['limit']}")
            lines.append("")

        if pri:
            lines.append("**전기**")
            if pri.get("headcount"):
                lines.append(f"- 이사의 수 (사외이사수): {pri['headcount']}")
            if pri.get("actualPaid"):
                lines.append(f"- 실제 지급된 보수총액: {pri['actualPaid']}")
            if pri.get("limit"):
                lines.append(f"- 최고한도액: {pri['limit']}")
            lines.append("")

        for note in item.get("notes", []):
            lines.append(f"> {note}")
            lines.append("")

    return "\n".join(lines)


def _format_aoi_change(result: dict) -> str:
    """정관변경을 마크다운으로 포매팅"""
    lines = ["## 정관변경 사항", ""]
    s = result.get("summary", {})
    lines.append(f"*총 {s.get('totalAmendments', 0)}건*")
    lines.append("")

    for a in result.get("amendments", []):
        sub_id = a.get("subAgendaId", "")
        label = a.get("label", "")
        header = f"### {sub_id}: {label}" if sub_id else f"### {label}"
        lines.append(header)
        if a.get("clause"):
            lines.append(f"*조항: {a['clause']}*")
        if a.get("reason"):
            lines.append(f"*사유: {a['reason']}*")
        lines.append("")
        if a.get("before"):
            lines.append(f"**변경전**")
            lines.append(f"> {a['before']}")
            lines.append("")
        if a.get("after"):
            lines.append(f"**변경후**")
            lines.append(f"> {a['after']}")
            lines.append("")

    return "\n".join(lines)


def _format_treasury_share(result: dict) -> str:
    """자기주식을 마크다운으로 포매팅"""
    lines = ["## 자기주식 현황", ""]
    s = result.get("summary", {})
    lines.append(f"*총 {s.get('totalItems', 0)}건*")
    lines.append("")

    for item in result.get("items", []):
        title_str = f"{item['number']}: {item['title']}" if item.get('number') else item['title']
        lines.append(f"### {title_str}")
        lines.append(f"*유형: {item.get('type', '')}*")
        lines.append("")

        if item.get("purpose"):
            lines.append(f"**목적**: {item['purpose']}")
            lines.append("")

        if item.get("sharesInfo"):
            lines.append("**주식수 정보**")
            for si in item["sharesInfo"]:
                lines.append(f"- {si}")
            lines.append("")

        if item.get("schedule"):
            lines.append("**스케줄**")
            for sc in item["schedule"]:
                lines.append(f"- {sc}")
            lines.append("")

        for tbl in item.get("tables", []):
            headers = tbl.get("headers", [])
            if headers:
                lines.append("| " + " | ".join(h.replace("|", "\\|") for h in headers) + " |")
                lines.append("| " + " | ".join("---" for _ in headers) + " |")
                for row in tbl.get("rows", []):
                    padded = row[:len(headers)] if len(row) >= len(headers) else row + [""] * (len(headers) - len(row))
                    lines.append("| " + " | ".join(c.replace("|", "\\|") for c in padded) + " |")
                lines.append("")

        for note in item.get("notes", []):
            lines.append(f"> {note}")
            lines.append("")

    return "\n".join(lines)


def _format_capital_reserve(result: dict) -> str:
    """자본준비금을 마크다운으로 포매팅"""
    lines = ["## 자본준비금 감소/이익잉여금 전입", ""]
    s = result.get("summary", {})
    lines.append(f"*총 {s.get('totalItems', 0)}건*")
    lines.append("")

    for item in result.get("items", []):
        title_str = f"{item['number']}: {item['title']}" if item.get('number') else item['title']
        lines.append(f"### {title_str}")
        lines.append("")

        if item.get("amount"):
            lines.append(f"**금액**: {item['amount']}")
            lines.append("")

        if item.get("purpose"):
            lines.append(f"**목적**: {item['purpose']}")
            lines.append("")

        for note in item.get("notes", []):
            lines.append(f"> {note}")
            lines.append("")

    return "\n".join(lines)


def _format_retirement_pay(result: dict) -> str:
    """퇴직금 규정을 마크다운으로 포매팅"""
    lines = ["## 퇴직금 규정 개정", ""]
    s = result.get("summary", {})
    lines.append(f"*총 {s.get('totalAmendments', 0)}건*")
    lines.append("")

    for a in result.get("amendments", []):
        clause = a.get("clause", "")
        header = f"### {clause}" if clause else "### (조항 미상)"
        lines.append(header)
        if a.get("reason"):
            lines.append(f"*사유: {a['reason']}*")
        lines.append("")
        if a.get("before"):
            lines.append(f"**현행**")
            lines.append(f"> {a['before']}")
            lines.append("")
        if a.get("after"):
            lines.append(f"**개정안**")
            lines.append(f"> {a['after']}")
            lines.append("")

    return "\n".join(lines)


def _format_personnel(result: dict) -> str:
    """인사 안건을 마크다운으로 포매팅"""
    lines = ["## 선임/해임 현황", ""]

    s = result.get("summary", {})
    parts = []
    if s.get("directors"): parts.append(f"이사 {s['directors']}명")
    if s.get("outside_directors"): parts.append(f"사외이사 {s['outside_directors']}명")
    if s.get("auditors"): parts.append(f"감사 {s['auditors']}명")
    if s.get("audit_committee"): parts.append(f"감사위원회 {s['audit_committee']}명")
    if s.get("dismissals"): parts.append(f"해임 {s['dismissals']}명")
    if parts:
        lines.append(f"*{', '.join(parts)}*")
        lines.append("")

    for a in result.get("appointments", []):
        lines.append(f"### {a['number']}: {a['title'][:50]}")
        lines.append(f"*{a['action']} | {a['category']}*")
        lines.append("")

        for c in a.get("candidates", []):
            lines.append(f"- **{c.get('name', '?')}**")
            if c.get("birthDate"):
                lines.append(f"  - 생년월일: {c['birthDate']}")
            if c.get("roleType"):
                lines.append(f"  - 직위: {c['roleType']}")
            if c.get("recommender"):
                lines.append(f"  - 추천인: {c['recommender']}")
            if c.get("majorShareholderRelation"):
                lines.append(f"  - 최대주주 관계: {c['majorShareholderRelation']}")
            if c.get("mainJob"):
                lines.append(f"  - 주요경력: {c['mainJob']}")
            if c.get("careerDetails"):
                lines.append(f"  - 세부경력:")
                for cd in c["careerDetails"]:
                    period = cd.get("period", "")
                    content = cd.get("content", "")
                    if period and content:
                        lines.append(f"    - {period}: {content}")
                    elif content:
                        lines.append(f"    - {content}")
            if c.get("recent3yTransactions"):
                lines.append(f"  - 법인 거래내역: {c['recent3yTransactions']}")
            if c.get("eligibility"):
                el = c["eligibility"]
                lines.append(f"  - 결격사유:")
                lines.append(f"    - 체납: {el.get('taxDelinquency', '해당사항 없음')}")
                lines.append(f"    - 부실기업: {el.get('insolventMgmt', '해당사항 없음')}")
                lines.append(f"    - 법령상: {el.get('legalDisqualification', '해당사항 없음')}")
            if c.get("dutyPlan"):
                lines.append(f"  - 직무수행계획: {c['dutyPlan']}")
            if c.get("recommendationReason"):
                lines.append(f"  - 추천사유: {c['recommendationReason']}")
        lines.append("")

    return "\n".join(lines)


def _format_correction_details(result: dict) -> str:
    """정정 사항을 마크다운으로 포매팅"""
    lines = ["## 정정 사항", ""]

    if result.get("date"):
        lines.append(f"- **정정일**: {result['date']}")
    if result.get("target_document"):
        lines.append(f"- **정정대상**: {result['target_document']}")
    if result.get("original_date"):
        lines.append(f"- **최초제출일**: {result['original_date']}")
    lines.append("")

    for i, item in enumerate(result.get("items", []), 1):
        lines.append(f"### 정정 {i}: {item['section'][:60]}")
        lines.append(f"**정정사유**: {item['reason']}")
        lines.append("")
        lines.append(f"**정정 전**:")
        lines.append(f"> {item['before'][:300]}")
        lines.append("")
        lines.append(f"**정정 후**:")
        lines.append(f"> {item['after'][:300]}")
        lines.append("")

    return "\n".join(lines)


def _format_agm_result(data: dict) -> str:
    """주주총회결과 마크다운 포맷"""
    lines = [f"# {data['corp_name']} 주주총회 결과\n"]
    lines.append(f"**공시일**: {data['rcept_dt']}\n")

    items = data.get("items", [])
    if not items:
        lines.append("투표 결과 없음")
        return "\n".join(lines)

    # 추정 참석률 — 보통결의 안건 중 최빈값
    ordinary_att = []
    for item in items:
        if "보통" in item.get("resolution_type", "") and item.get("estimated_attendance"):
            ordinary_att.append(item["estimated_attendance"])
    if ordinary_att:
        most_common = Counter(ordinary_att).most_common(1)[0]
        lines.append(f"**추정 참석률**: {most_common[0]}% (보통결의 {most_common[1]}건 기준, 발행기준/행사기준 역산)")
        lines.append(f"*최대주주 제외 참석률은 ownership_major 지분율과 조합하여 추정 가능*\n")

    lines.append("| 번호 | 결의구분 | 안건 | 결과 | 찬성(발행기준) | 찬성(행사기준) | 반대/기권 | 추정참석률 |")
    lines.append("|------|---------|------|------|-------------|-------------|----------|----------|")

    for item in items:
        passed = item.get("passed", "")
        if "가결" in passed:
            passed_fmt = f"**{passed}**"
        elif "부결" in passed:
            passed_fmt = f"~~{passed}~~"
        else:
            passed_fmt = passed

        att = item.get("estimated_attendance")
        att_str = f"{att}%" if att else "-"

        lines.append(
            f"| {item.get('number', '')} "
            f"| {item.get('resolution_type', '')} "
            f"| {item.get('agenda', '')} "
            f"| {passed_fmt} "
            f"| {item.get('approval_rate_issued', '')}% "
            f"| {item.get('approval_rate_voted', '')}% "
            f"| {item.get('opposition_rate', '')}% "
            f"| {att_str} |"
        )

    return "\n".join(lines)


def _parse_agm_result_table(soup) -> list[dict]:
    """주주총회결과 HTML에서 안건별 투표 결과 테이블 파싱

    헤더 패턴: 번호 | 결의구분 | 회의목적사항 | 가결여부 | 찬성률(발행) | 찬성률(행사) | 반대기권
    """
    tables = soup.find_all("table")
    for table in tables:
        rows = table.find_all("tr")
        if len(rows) < 3:
            continue

        # 헤더 확인 — "번호"와 "가결여부"가 있는 테이블
        header_text = " ".join(c.get_text(strip=True) for c in rows[0].find_all(["td", "th"]))
        if "번호" not in header_text or "가결여부" not in header_text:
            continue

        # 두 번째 행이 서브헤더 (찬성률, 반대기권)인 경우 스킵
        data_start = 1
        row1_text = " ".join(c.get_text(strip=True) for c in rows[1].find_all(["td", "th"]))
        if "찬성률" in row1_text:
            data_start = 2

        items = []
        for row in rows[data_start:]:
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            if len(cells) < 5:
                continue
            # 빈 행 스킵
            if not cells[0] or cells[0] == "-":
                continue

            try:
                iss = float(cells[4]) if len(cells) > 4 and cells[4] else 0
                vot = float(cells[5]) if len(cells) > 5 and cells[5] else 0
                attend = round(iss / vot * 100, 1) if vot > 0 else None
            except (ValueError, ZeroDivisionError):
                attend = None

            item = {
                "number": cells[0],
                "resolution_type": cells[1] if len(cells) > 1 else "",
                "agenda": cells[2] if len(cells) > 2 else "",
                "passed": cells[3] if len(cells) > 3 else "",
                "approval_rate_issued": cells[4] if len(cells) > 4 else "",
                "approval_rate_voted": cells[5] if len(cells) > 5 else "",
                "opposition_rate": cells[6] if len(cells) > 6 else "",
                "estimated_attendance": attend,
            }
            items.append(item)

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
    if "→" in normalized:
        normalized = normalized.split("→", 1)[1]
    return _normalize_vote_outcome(normalized)


def _parse_agm_result_summary(soup) -> list[dict]:
    """주주총회결과 HTML의 요약형 결과공시 파싱.

    패턴 예시:
    - ○ 제1호 의안 : ... → 원안가결
    - 제2-1호 의안 : ...
      → 부결
    - 1) 제1호 의안: ... → 원안대로 승인
    """
    text = soup.get_text("\n", strip=True)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]

    started = False
    current: dict[str, str] | None = None
    items: list[dict] = []

    def flush_current() -> None:
        nonlocal current
        if current and current.get("passed"):
            items.append({
                "number": current.get("number", ""),
                "resolution_type": "",
                "agenda": current.get("agenda", ""),
                "passed": current.get("passed", ""),
                "approval_rate_issued": "",
                "approval_rate_voted": "",
                "opposition_rate": "",
                "estimated_attendance": None,
            })
        current = None

    for line in lines:
        if not started:
            if "의결사항" in line or "결의사항" in line:
                started = True
            continue

        if (
            re.match(r"^\d+\.\s*(주주총회 ?일자|의결권행사기준일|기타 투자판단)", line)
            or "※ 관련공시" in line
            or line.startswith("[")
            or line.startswith("〖")
        ):
            flush_current()
            break

        match = re.search(r"(?:○\s*)?(?:\d+\)\s*)?(제\d+(?:-\d+)?호)\s*의안\s*[:：]?\s*(.*)", line)
        if match:
            flush_current()
            number = match.group(1)
            remainder = match.group(2).strip()
            passed = _extract_vote_outcome(remainder) if "→" in remainder or "가결" in remainder or "부결" in remainder or "승인" in remainder else ""
            agenda = remainder
            if "→" in agenda:
                agenda = agenda.split("→", 1)[0].strip()
            current = {
                "number": number,
                "agenda": agenda.strip(" -"),
                "passed": passed,
            }
            continue

        if current:
            outcome = _extract_vote_outcome(line)
            if outcome:
                current["passed"] = outcome
                flush_current()

    flush_current()
    return items


# ── Ownership 포매터 (ownership.py에서 이동) ──

def _parse_holding_purpose(report_tp: str, report_resn: str) -> str:
    """majorstock API 응답에서 보유목적 추론

    report_tp: "일반" → 경영참여, "약식" → 단순/일반투자
    report_resn: 텍스트에서 "단순투자", "일반투자" 키워드 매칭
    """
    if report_tp == "일반":
        return "경영참여"

    resn = report_resn or ""
    if "단순투자" in resn:
        return "단순투자"
    if "일반투자" in resn:
        return "일반투자"

    # 약식이면 경영참여는 아님 — 기본값
    if report_tp == "약식":
        return "단순투자/일반투자"

    return "불명"


def _parse_holding_purpose_from_document(html: str) -> str:
    """DART document.xml 원문에서 보유목적 파싱

    패턴 1: <TU AUNIT="PUR_OWN" ...>단순투자</TU> (DART XML 태그)
    패턴 2: 보유목적</TD> 다음 <TD>값</TD> (테이블 행)
    패턴 3: 보유목적</h3> 이후 첫 <TD>에서 추출
    """
    # 패턴 1: DART XML — PUR_OWN 태그 (가장 정확)
    m = re.search(r'AUNIT="PUR_OWN"[^>]*>([^<]+)<', html)
    if m:
        return _normalize_purpose(m.group(1).strip())

    # 패턴 2: 요약정보 테이블
    m2 = re.search(r'보유목적\s*</T[DH]>\s*<T[UDH][^>]*>\s*(.+?)\s*</T[UDH]>', html, re.IGNORECASE | re.DOTALL)
    if m2:
        purpose = re.sub(r'<[^>]+>', '', m2.group(1)).strip()
        if purpose:
            return _normalize_purpose(purpose)

    # 패턴 3: 본문 섹션
    m3 = re.search(r'보유목적\s*</[hH]\d>\s*.*?<TD[^>]*>\s*(.+?)\s*</TD>', html, re.IGNORECASE | re.DOTALL)
    if m3:
        purpose = re.sub(r'<[^>]+>', '', m3.group(1)).strip()
        if purpose:
            return _normalize_purpose(purpose)

    return "불명"


def _normalize_purpose(raw: str) -> str:
    """보유목적 텍스트 정규화"""
    if "경영" in raw and "참여" in raw:
        return "경영참여"
    if "단순" in raw and "투자" in raw:
        return "단순투자"
    if "일반" in raw and "투자" in raw:
        return "일반투자"
    return raw


def _pct(val: str) -> str:
    """% 있으면 그대로, 없으면 붙임"""
    v = (val or "").strip()
    if not v or v == "-":
        return v
    return v if "%" in v else f"{v}%"


def _format_number(val: str) -> str:
    """숫자 문자열에 콤마 추가 (이미 있으면 그대로)"""
    if not val or val.strip() in ('', '-'):
        return val
    num_str = re.sub(r'[^\d]', '', val)
    if not num_str:
        return val
    return f"{int(num_str):,}"


def _format_major_shareholders(data: dict, changes: dict | None = None) -> str:
    """최대주주+특관인 마크다운 포맷"""
    items = data.get("list", [])
    if not items:
        return "최대주주 현황 데이터가 없습니다."

    lines = ["## 최대주주 및 특수관계인 현황\n"]

    # 기준일
    stlm_dt = items[0].get("stlm_dt", "")
    if stlm_dt:
        lines.append(f"**기준일**: {stlm_dt}\n")

    lines.append("| 성명 | 관계 | 주식종류 | 기말 주식수 | 기말 지분율 |")
    lines.append("|------|------|----------|-----------|-----------|")

    for item in items:
        name = item.get("nm", "")
        relate = item.get("relate", "")
        stock_knd = item.get("stock_knd", "")
        end_co = _format_number(item.get("trmend_posesn_stock_co", ""))
        end_rt = item.get("trmend_posesn_stock_qota_rt", "")
        lines.append(f"| {name} | {relate} | {stock_knd} | {end_co} | {_pct(end_rt)} |")

    # 변동이력
    if changes and changes.get("list"):
        lines.append("\n### 최대주주 변동이력\n")
        lines.append("| 변동일 | 최대주주 | 주식수 | 지분율 | 변동원인 |")
        lines.append("|--------|---------|--------|--------|---------|")
        for ch in changes["list"]:
            lines.append(
                f"| {ch.get('change_on', '')} | {ch.get('mxmm_shrholdr_nm', '')} "
                f"| {_format_number(ch.get('posesn_stock_co', ''))} "
                f"| {_pct(ch.get('qota_rt', ''))} | {ch.get('change_cause', '')} |"
            )

    return "\n".join(lines)


def _format_stock_total(stock_data: dict, minority_data: dict | None = None) -> str:
    """주식총수+소액주주 마크다운 포맷"""
    items = stock_data.get("list", [])
    if not items:
        return "주식 총수 데이터가 없습니다."

    lines = ["## 주식의 총수 현황\n"]

    lines.append("| 구분 | 발행할 주식 총수 | 현재까지 발행 총수 | 감소 총수 | 발행주식 총수 | 자기주식 | 유통주식 |")
    lines.append("|------|----------------|-----------------|----------|------------|---------|---------|")

    for item in items:
        lines.append(
            f"| {item.get('se', '')} "
            f"| {_format_number(item.get('isu_stock_totqy', ''))} "
            f"| {_format_number(item.get('now_to_isu_stock_totqy', ''))} "
            f"| {_format_number(item.get('now_to_dcrs_stock_totqy', ''))} "
            f"| {_format_number(item.get('istc_totqy', ''))} "
            f"| {_format_number(item.get('tesstk_co', ''))} "
            f"| {_format_number(item.get('distb_stock_co', ''))} |"
        )

    # 소액주주
    if minority_data and minority_data.get("list"):
        lines.append("\n### 소액주주 현황\n")
        lines.append("| 구분 | 주주수 | 전체 주주수 | 주주 비율 | 보유 주식수 | 총 발행주식수 | 보유 비율 |")
        lines.append("|------|--------|-----------|----------|-----------|------------|----------|")
        for m in minority_data["list"]:
            lines.append(
                f"| {m.get('se', '')} "
                f"| {_format_number(m.get('shrholdr_co', ''))} "
                f"| {_format_number(m.get('shrholdr_tot_co', ''))} "
                f"| {m.get('shrholdr_rate', '')} "
                f"| {_format_number(m.get('hold_stock_co', ''))} "
                f"| {_format_number(m.get('stock_tot_co', ''))} "
                f"| {m.get('hold_stock_rate', '')} |"
            )

    return "\n".join(lines)


def _format_treasury_stock(data: dict) -> str:
    """자기주식 현황 마크다운 포맷"""
    items = data.get("list", [])
    if not items:
        return "자기주식 데이터가 없습니다."

    lines = ["## 자기주식 취득 및 처분 현황\n"]

    stlm_dt = items[0].get("stlm_dt", "")
    if stlm_dt:
        lines.append(f"**기준일**: {stlm_dt}\n")

    lines.append("| 취득방법 | 주식종류 | 기초 | 취득 | 처분 | 소각 | 기말 |")
    lines.append("|---------|---------|------|------|------|------|------|")

    for item in items:
        method = f"{item.get('acqs_mth1', '')} {item.get('acqs_mth2', '')} {item.get('acqs_mth3', '')}".strip()
        lines.append(
            f"| {method} | {item.get('stock_knd', '')} "
            f"| {_format_number(item.get('bsis_qy', ''))} "
            f"| {_format_number(item.get('change_qy_acqs', ''))} "
            f"| {_format_number(item.get('change_qy_dsps', ''))} "
            f"| {_format_number(item.get('change_qy_incnr', ''))} "
            f"| {_format_number(item.get('trmend_qy', ''))} |"
        )

    return "\n".join(lines)


def _format_treasury_tx(acq: dict, disp: dict, trust_in: dict, trust_out: dict) -> str:
    """자사주 취득/처분/신탁 이벤트 마크다운 포맷"""
    lines = ["## 자기주식 거래 이벤트\n"]
    has_data = False

    # 취득 결정
    acq_list = acq.get("list", [])
    if acq_list:
        has_data = True
        lines.append("### 취득 결정\n")
        lines.append("| 결정일 | 취득예정 주수 | 취득예정 금액 | 취득기간 | 목적 | 방법 |")
        lines.append("|--------|------------|------------|---------|------|------|")
        for a in acq_list:
            lines.append(
                f"| {a.get('aq_dd', '')} "
                f"| {a.get('aqpln_stk_ostk', '')} "
                f"| {a.get('aqpln_prc_ostk', '')} "
                f"| {a.get('aqexpd_bgd', '')}-{a.get('aqexpd_edd', '')} "
                f"| {a.get('aq_pp', '')} "
                f"| {a.get('aq_mth', '')} |"
            )

    # 처분 결정
    disp_list = disp.get("list", [])
    if disp_list:
        has_data = True
        lines.append("\n### 처분 결정\n")
        lines.append("| 결정일 | 처분예정 주수 | 처분예정 금액 | 처분기간 | 목적 |")
        lines.append("|--------|------------|------------|---------|------|")
        for d in disp_list:
            lines.append(
                f"| {d.get('dp_dd', '')} "
                f"| {d.get('dppln_stk_ostk', '')} "
                f"| {d.get('dppln_prc_ostk', '')} "
                f"| {d.get('dpprpd_bgd', '')}-{d.get('dpprpd_edd', '')} "
                f"| {d.get('dp_pp', '')} |"
            )

    # 신탁 체결
    trust_in_list = trust_in.get("list", [])
    if trust_in_list:
        has_data = True
        lines.append("\n### 신탁계약 체결\n")
        lines.append("| 이사회결의일 | 계약금액 | 계약기간 | 목적 |")
        lines.append("|-----------|---------|---------|------|")
        for t in trust_in_list:
            lines.append(
                f"| {t.get('bddd', '')} "
                f"| {t.get('ctr_prc', '')} "
                f"| {t.get('ctr_pd_bgd', '')}-{t.get('ctr_pd_edd', '')} "
                f"| {t.get('ctr_pp', '')} |"
            )

    # 신탁 해지
    trust_out_list = trust_out.get("list", [])
    if trust_out_list:
        has_data = True
        lines.append("\n### 신탁계약 해지\n")
        lines.append("| 이사회결의일 | 해지전 금액 | 해지후 금액 | 목적 |")
        lines.append("|-----------|-----------|-----------|------|")
        for t in trust_out_list:
            lines.append(
                f"| {t.get('bddd', '')} "
                f"| {t.get('ctr_prc_bfcc', '')} "
                f"| {t.get('ctr_prc_atcc', '')} "
                f"| {t.get('cc_pp', '')} |"
            )

    if not has_data:
        return "자기주식 거래 이벤트가 없습니다."

    return "\n".join(lines)


def _format_block_holders(data: dict, purposes: dict[str, str] | None = None) -> str:
    """5% 대량보유 마크다운 포맷

    Args:
        data: majorstock API 응답
        purposes: {rcept_no: 보유목적} 매핑 (원문 파싱 결과)
    """
    items = data.get("list", [])
    if not items:
        return "5% 대량보유 보고 이력이 없습니다."

    purposes = purposes or {}

    lines = ["## 5% 대량보유 상황보고\n"]
    lines.append("*보유비율은 보고자+특별관계자 합산 기준*\n")
    lines.append("| 접수일 | 보고자 | 보유주식수 | 보유비율 | 증감 | 보유목적 | 보고사유 |")
    lines.append("|--------|--------|----------|---------|------|---------|---------|")

    for item in items:
        rcept_no = item.get("rcept_no", "")
        purpose = purposes.get(rcept_no) or _parse_holding_purpose(
            item.get("report_tp", ""), item.get("report_resn", "")
        )
        lines.append(
            f"| {item.get('rcept_dt', '')} "
            f"| {item.get('repror', '')} "
            f"| {_format_number(item.get('stkqy', ''))} "
            f"| {_pct(item.get('stkrt', ''))} "
            f"| {item.get('stkrt_irds', '')}%p "
            f"| **{purpose}** "
            f"| {item.get('report_resn', '')[:50]} |"
        )

    # 보유목적 변경 감지
    reporters: dict[str, list] = {}
    for item in items:
        name = item.get("repror", "")
        if name:
            rcept_no = item.get("rcept_no", "")
            purpose = purposes.get(rcept_no) or _parse_holding_purpose(
                item.get("report_tp", ""), item.get("report_resn", "")
            )
            reporters.setdefault(name, []).append({
                "date": item.get("rcept_dt", ""),
                "purpose": purpose,
            })

    purpose_changes = []
    for name, history in reporters.items():
        sorted_hist = sorted(history, key=lambda x: x["date"])
        for i in range(1, len(sorted_hist)):
            prev = sorted_hist[i - 1]["purpose"]
            curr = sorted_hist[i]["purpose"]
            if prev != curr and "불명" not in prev and "불명" not in curr:
                purpose_changes.append(
                    f"- **{name}**: {prev} → {curr} ({sorted_hist[i]['date']})"
                )

    if purpose_changes:
        lines.append("\n### 보유목적 변경 이력\n")
        lines.extend(purpose_changes)

    return "\n".join(lines)


def _format_latest_snapshot(
    major_data: dict,
    block_data: dict,
    exec_data: dict,
    purposes: dict[str, str] | None = None,
) -> str:
    """전 주주 최신 스냅샷 마크다운 포맷"""
    lines = ["## 주주 최신 스냅샷\n"]
    purposes = purposes or {}

    # 1. 최대주주+특관인 (사업보고서 기준)
    major_items = major_data.get("list", [])
    if major_items:
        stlm_dt = major_items[0].get("stlm_dt", "")
        lines.append(f"### 최대주주+특수관계인 (사업보고서 {stlm_dt})\n")
        # 보통주만, 상위 5명
        common = [i for i in major_items if "보통" in i.get("stock_knd", "보통")]
        for item in common[:5]:
            name = item.get("nm", "")
            rt = item.get("trmend_posesn_stock_qota_rt", "")
            lines.append(f"- {name}: {_pct(rt)}")
        if len(common) > 5:
            lines.append(f"- ... 외 {len(common) - 5}명")

    # 2. 5% 대량보유 (수시, 보고자별 최신)
    block_items = block_data.get("list", [])
    if block_items:
        lines.append("\n### 5% 대량보유 (최신 보고 기준)\n")
        # 보고자별 최신 1건
        latest_by_reporter: dict[str, dict] = {}
        for item in block_items:
            name = item.get("repror", "")
            dt = item.get("rcept_dt", "")
            if name not in latest_by_reporter or dt > latest_by_reporter[name].get("rcept_dt", ""):
                latest_by_reporter[name] = item

        for name, item in sorted(latest_by_reporter.items(), key=lambda x: float(x[1].get("stkrt", 0) or 0), reverse=True):
            rcept_no = item.get("rcept_no", "")
            purpose = purposes.get(rcept_no) or _parse_holding_purpose(
                item.get("report_tp", ""), item.get("report_resn", "")
            )
            lines.append(
                f"- {name}: {item.get('stkrt', '')}% "
                f"({item.get('rcept_dt', '')}, {purpose})"
            )

    # 3. 임원 (최신 상위 5건)
    exec_items = exec_data.get("list", [])
    if exec_items:
        lines.append(f"\n### 임원/주요주주 소유 (최근 보고, 총 {len(exec_items)}건)\n")
        # 날짜순 정렬, 최신 5건
        sorted_exec = sorted(exec_items, key=lambda x: x.get("rcept_dt", ""), reverse=True)
        for item in sorted_exec[:5]:
            lines.append(
                f"- {item.get('repror', '')} ({item.get('isu_exctv_ofcps', '')}): "
                f"{item.get('sp_stock_lmp_cnt', '')}주 "
                f"({_pct(item.get('sp_stock_lmp_rate', ''))}) "
                f"[{item.get('rcept_dt', '')}]"
            )

    return "\n".join(lines)
