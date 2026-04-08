"""배당 관련 MCP tools (div_*)

배당 지표 연산 규칙:
  - 배당성향(payout ratio) = 총배당금 / 지배주주 당기순이익 × 100
    * 반드시 '지배주주 귀속 당기순이익' 사용 (연결 기준)
    * 비지배지분 순이익 포함하면 배당성향이 과소 계산됨
    * 별도 재무제표의 당기순이익은 사용 금지 (연결 vs 별도 괴리)
  - 배당수익률(dividend yield) = 주당배당금(DPS) / 기준일 종가 × 100
    * 종가: KRX Open API 일별 시세 (배당 기준일 또는 직전 거래일)
    * 시가배당률이 DART에 있으면 그걸 우선 사용
  - 분기/반기 배당 시:
    * 각 분기별 DPS는 해당 분기분만 (누적 아님)
    * 연간 DPS = 1Q + 2Q + 3Q + 기말 합산
    * flow(개별 분기) + stock(연간 누적) 모두 표시
  - 특별배당: 정기배당과 별도. '특별' 키워드로 감지, 합산에 포함하되 별도 표시
  - 주식배당: 데이터 관리하되 현금배당 대비 중요도 낮음. 별도 행으로 표시
"""

import json
import os
import re
import logging
from datetime import datetime

from open_proxy_mcp.dart.client import DartClient, DartClientError, get_dart_client
from open_proxy_mcp.tools.errors import tool_error, tool_not_found, tool_empty

logger = logging.getLogger(__name__)

# 보고서 코드 → 라벨
_REPRT_LABELS = {
    "11013": "1분기",
    "11012": "반기",
    "11014": "3분기",
    "11011": "사업보고서(기말)",
}


def _safe_int(val) -> int:
    """문자열 → 정수 변환 (쉼표, 공백, None 처리)"""
    if val is None:
        return 0
    s = str(val).replace(",", "").replace(" ", "").strip()
    if not s or s == "-":
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def _safe_float(val) -> float:
    """문자열 → 실수 변환"""
    if val is None:
        return 0.0
    s = str(val).replace(",", "").replace(" ", "").strip()
    if not s or s == "-":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_dividend_decision(text: str) -> dict | None:
    """현금ㆍ현물배당결정 공시 본문 파싱 (거래소 표준 서식)

    표준 필드:
      1. 배당구분 (결산배당/중간배당/분기배당)
      2. 배당종류 (현금배당/현물배당)
      3. 1주당 배당금(원) — 보통주식/종류주식
      4. 시가배당율(%) — 보통주식/종류주식
      5. 배당금총액(원)
      6. 배당기준일 (YYYY-MM-DD)
      7. 배당금지급 예정일자 (YYYY-MM-DD 또는 -)
      8. 주주총회 개최여부
      9. 주주총회 예정일자
     10. 이사회결의일(결정일)
     11. 기타 투자판단과 관련한 중요사항
    """
    if not text:
        return None

    # CSS 제거
    clean = re.sub(r'\.xforms[^}]+\}', '', text).strip()
    # 연속 공백/개행 → 단일 공백
    clean = re.sub(r'\s+', ' ', clean)

    result = {}

    # 1. 배당구분
    m = re.search(r'1\.\s*배당구분\s*(결산배당|중간배당|분기배당)', clean)
    result["dividend_type"] = m.group(1) if m else None

    # 2. 배당종류
    m = re.search(r'2\.\s*배당종류\s*(현금배당|현물배당|주식배당)', clean)
    result["dividend_method"] = m.group(1) if m else None

    # 3. 1주당 배당금
    m = re.search(r'3\.\s*1주당\s*배당금\s*\(원\)\s*보통주식\s*([\d,]+)', clean)
    result["dps_common"] = _safe_int(m.group(1)) if m else 0
    # 종류주식 DPS: "1주당 배당금" ~ "4. 시가배당율" 사이에서 추출
    dps_start = clean.find("1주당 배당금")
    dps_end = clean.find("4.", dps_start) if dps_start >= 0 else -1
    dps_segment = clean[dps_start:dps_end] if dps_start >= 0 and dps_end > dps_start else ""
    m = re.search(r'종류주식\s*([\d,]+)', dps_segment)
    result["dps_preferred"] = _safe_int(m.group(1)) if m else 0

    # 차등배당
    m = re.search(r'차등배당\s*여부\s*(해당|미해당)', clean)
    result["differential_dividend"] = m.group(1) == "해당" if m else False

    # 4. 시가배당율
    m = re.search(r'4\.\s*시가배당율\s*\(%\)\s*보통주식\s*([\d.]+)', clean)
    result["yield_common"] = _safe_float(m.group(1)) if m else 0.0
    m = re.search(r'4\.\s*시가배당율.*?종류주식\s*([\d.]+)', clean)
    result["yield_preferred"] = _safe_float(m.group(1)) if m else 0.0

    # 5. 배당금총액
    m = re.search(r'5\.\s*배당금총액\s*\(원\)\s*([\d,]+)', clean)
    result["total_amount"] = _safe_int(m.group(1)) if m else 0

    # 6. 배당기준일
    m = re.search(r'6\.\s*배당기준일\s*(\d{4}-\d{2}-\d{2})', clean)
    result["record_date"] = m.group(1) if m else None

    # 7. 배당금지급 예정일자
    m = re.search(r'7\.\s*배당금지급\s*예정일자\s*(\d{4}-\d{2}-\d{2})', clean)
    result["payment_date"] = m.group(1) if m else None

    # 8. 주주총회 개최여부
    m = re.search(r'8\.\s*주주총회\s*개최여부\s*(개최|미개최|미해당)', clean)
    result["agm_required"] = m.group(1) if m else None

    # 9. 주주총회 예정일자
    m = re.search(r'9\.\s*주주총회\s*예정일자\s*(\d{4}-\d{2}-\d{2})', clean)
    result["agm_date"] = m.group(1) if m else None

    # 10. 이사회결의일
    m = re.search(r'10\.\s*이사회결의일\s*\(결정일\)\s*(\d{4}-\d{2}-\d{2})', clean)
    result["board_date"] = m.group(1) if m else None

    # 11. 기타 — 특별배당 감지
    m = re.search(r'11\.\s*기타\s*투자판단과\s*관련한\s*중요사항\s*(.*?)(?:※|【|\Z)', clean)
    remarks = m.group(1).strip() if m else ""
    result["remarks"] = remarks

    # 특별배당 감지
    result["has_special"] = bool(re.search(r'특별|추가.*배당|추가하여', remarks))
    # 특별배당 금액 추출 시도
    if result["has_special"]:
        m = re.search(r'([\d,.]+)\s*조원을?\s*추가', remarks)
        if m:
            result["special_amount_description"] = f"{m.group(1)}조원 추가"

    # 종류주식 상세 (복수 우선주 지원)
    kind_match = re.search(r'【종류주식[^】]*】\s*(.*?)$', clean)
    preferred_stocks = []
    if kind_match:
        pref_text = kind_match.group(1)
        # 각 우선주 행: 종류주식명 종류주식구분 DPS 시가배당율 배당금총액
        rows = re.findall(
            r'(\S+)\s+(우선주|전환우선주|종류주식)\s+([\d,.]+)\s+([\d.]+|-)\s+([\d,.]+)',
            pref_text
        )
        for raw_name, ptype, dps, yld, total_amt in rows:
            # 이름 정규화: 괄호 제거, 종목코드 제거
            name_clean = re.sub(r'^\(|\)$', '', raw_name)  # 앞뒤 괄호
            name_clean = re.sub(r'^\d{6}\)?$', '', name_clean)  # 순수 종목코드
            if not name_clean:
                continue

            # 우선주 종류 판별
            # 2우B, 3우B = 신형우선주
            # N우(전환) = 전환우선주
            # {회사}우, 우선주, 1우선주 = 구형우선주 (단독이면 그냥 "우선주")
            if re.search(r'\d우B', name_clean):
                stock_class = "신형우선주"
            elif "전환" in ptype or "전환" in name_clean:
                stock_class = "전환우선주"
            elif re.match(r'제?\d차', name_clean):
                stock_class = "전환우선주"  # 키움증권 제3차/제4차
            elif re.search(r'2우선주|2우$', name_clean):
                stock_class = "신형우선주"
            else:
                stock_class = "우선주"  # 구형 또는 단독

            preferred_stocks.append({
                "name": name_clean,
                "raw_type": ptype,
                "stock_class": stock_class,
                "dps": _safe_int(dps),
                "yield_pct": _safe_float(yld),
                "total_amount": _safe_int(total_amt),
            })

    result["preferred_stocks"] = preferred_stocks
    # 하위 호환: 첫 번째 우선주를 preferred_detail로
    if preferred_stocks:
        result["preferred_detail"] = preferred_stocks[0]

    # 유효성 체크
    if not result.get("dps_common") and not result.get("total_amount"):
        return None

    return result


def _parse_dividend_items(data: dict) -> list[dict]:
    """alotMatter API 응답 → 정규화된 배당 항목 리스트

    DART alotMatter 필드:
      se: 항목명 (주당액면가액, 당기순이익, 현금배당금총액, 배당성향, 배당수익률, 주당 현금배당금 등)
      stock_knd: 주식 종류 (보통주/우선주, 일부 항목에만 있음)
      thstrm: 당기, frmtrm: 전기, lwfr: 전전기
      stlm_dt: 결산일 (배당 기준일/지급일 아님)
    """
    items = data.get("list", [])
    if not items:
        return []

    results = []
    for item in items:
        se = item.get("se", "")
        stock_knd = item.get("stock_knd", "")

        parsed = {
            "category": se,
            "stock_type": stock_knd,
            "current": item.get("thstrm", ""),
            "previous": item.get("frmtrm", ""),
            "before_previous": item.get("lwfr", ""),
            "stlm_dt": item.get("stlm_dt", ""),
            "is_special": "특별" in se,
            "is_stock_dividend": "주식배당" in se or "주당 주식배당" in se,
        }
        results.append(parsed)

    return results


def _build_dividend_summary(items: list[dict], reprt_label: str) -> dict:
    """배당 항목 리스트 → 요약 구조체

    alotMatter 실제 항목명 기준:
      - "주당 현금배당금(원)" + stock_knd="보통주" → 현금 DPS
      - "현금배당금총액(백만원)" → 총액 (백만원 단위)
      - "(연결)현금배당성향(%)" → 배당성향 (연결 기준)
      - "현금배당수익률(%)" + stock_knd="보통주" → 배당수익률
      - "(연결)당기순이익(백만원)" → 연결 당기순이익
      - "주당 주식배당(주)" → 주식배당
    """
    cash_dps = 0
    cash_dps_pref = 0  # 우선주 DPS
    stock_dps = 0
    special_dps = 0
    total_amount = 0  # 백만원 단위
    payout_ratio_dart = None
    yield_dart = None
    yield_pref_dart = None
    net_income_consolidated = 0  # 연결 당기순이익 (백만원)
    stlm_dt = ""

    for item in items:
        cat = item.get("category", "")
        cur = item.get("current", "")
        sknd = item.get("stock_type", "")

        if not stlm_dt and item.get("stlm_dt"):
            stlm_dt = item["stlm_dt"]

        # 주당 현금배당금
        if "주당 현금배당금" in cat or ("주당" in cat and "현금배당금" in cat):
            val = _safe_int(cur)
            if item.get("is_special"):
                special_dps += val
            elif "우선주" in sknd:
                cash_dps_pref = val
            else:
                cash_dps = val

        # 주당 주식배당
        if "주당 주식배당" in cat:
            stock_dps = _safe_int(cur)

        # 현금배당금총액
        if "현금배당금총액" in cat:
            total_amount = _safe_int(cur)  # 백만원 단위

        # 배당성향 (연결 기준 우선)
        if "현금배당성향" in cat and "연결" in cat:
            val = _safe_float(cur)
            if val > 0:
                payout_ratio_dart = val
        elif "현금배당성향" in cat and payout_ratio_dart is None:
            val = _safe_float(cur)
            if val > 0:
                payout_ratio_dart = val

        # 현금배당수익률
        if "현금배당수익률" in cat:
            val = _safe_float(cur)
            if val > 0:
                if "우선주" in sknd:
                    yield_pref_dart = val
                else:
                    yield_dart = val

        # 연결 당기순이익
        if "연결" in cat and "당기순이익" in cat:
            net_income_consolidated = _safe_int(cur)

    return {
        "period": reprt_label,
        "stlm_dt": stlm_dt,
        "cash_dps": cash_dps,
        "cash_dps_preferred": cash_dps_pref,
        "stock_dps": stock_dps,
        "special_dps": special_dps,
        "total_dps": cash_dps + special_dps,
        "total_amount_mil": total_amount,
        "payout_ratio_dart": payout_ratio_dart,
        "yield_dart": yield_dart,
        "yield_preferred_dart": yield_pref_dart,
        "net_income_consolidated_mil": net_income_consolidated,
        "items": items,
    }


# ── Tool 등록 ──

def register_tools(mcp):
    """dividend MCP tools 등록"""

    @mcp.tool()
    async def div_search(
        ticker: str,
        bgn_de: str = "",
        end_de: str = "",
    ) -> str:
        """desc: 배당 관련 공시 검색 (현금배당 결정, 중간배당 등).
        when: 특정 기업의 배당 공시를 찾을 때. ticker로 검색.
        rule: 검색 결과에서 rcept_no를 얻어 div_detail에 전달.
        ref: div_detail, div_history, div_manual"""
        client = get_dart_client()
        corp = await client.lookup_corp_code(ticker)
        if not corp:
            return tool_not_found("기업", ticker)

        corp_code = corp["corp_code"]
        corp_name = corp.get("corp_name", ticker)

        if not bgn_de:
            bgn_de = f"{datetime.now().year - 1}0101"
        if not end_de:
            end_de = datetime.now().strftime("%Y%m%d")

        try:
            filings = await client.search_filings(
                corp_code=corp_code, bgn_de=bgn_de, end_de=end_de,
            )
        except DartClientError as e:
            return tool_error("배당 공시 검색", e, ticker=ticker)

        dividend_filings = []
        for item in filings.get("list", []):
            report_nm = item.get("report_nm", "")
            if any(kw in report_nm for kw in ["배당", "현금ㆍ현물배당", "중간배당"]):
                dividend_filings.append(item)

        if not dividend_filings:
            return f"{corp_name}의 배당 관련 공시를 찾을 수 없습니다 ({bgn_de}-{end_de}).\n*div_history로 사업보고서 기반 배당 이력을 조회할 수 있습니다.*"

        lines = [f"# {corp_name} 배당 공시 ({bgn_de}-{end_de})\n"]
        for item in dividend_filings[:10]:
            lines.append(
                f"- **{item.get('report_nm', '')}** ({item.get('rcept_dt', '')})\n"
                f"  rcept_no: `{item.get('rcept_no', '')}`"
            )
        return "\n".join(lines)

    @mcp.tool()
    async def div_detail(
        ticker: str,
        bsns_year: str = "",
        reprt_code: str = "11011",
        format: str = "md",
    ) -> str:
        """desc: 배당 상세 — 보통주/우선주 DPS, 배당총액, 배당성향, 시가배당률, 특별배당, 종류주식(우선주) 상세.
        when: 배당 내용을 볼 때. 우선주(2우B, 우선주) 배당도 이 tool로 조회. "삼성전자우" 질문 시 ticker="삼성전자"로 호출 후 preferred_stocks에서 확인.
        rule: 우선주는 보통주 공시 안에 포함. ticker는 보통주 기준으로 입력. reprt_code로 분기 선택 (11011=기말, 11012=반기, 11013=1Q, 11014=3Q). DART 제공 배당성향/시가배당률이 있으면 우선 사용.
        ref: div_history, div_manual"""
        client = get_dart_client()
        corp = await client.lookup_corp_code(ticker)
        if not corp:
            return tool_not_found("기업", ticker)

        corp_code = corp["corp_code"]
        corp_name = corp.get("corp_name", ticker)

        if not bsns_year:
            bsns_year = str(datetime.now().year - 1)

        try:
            data = await client.get_dividend_info(corp_code, bsns_year, reprt_code)
        except DartClientError as e:
            return tool_error("배당 조회", e, ticker=ticker)

        items = _parse_dividend_items(data)
        if not items:
            return f"{corp_name}의 {bsns_year}년 배당 정보가 없습니다 (reprt_code={reprt_code})."

        reprt_label = _REPRT_LABELS.get(reprt_code, reprt_code)
        summary = _build_dividend_summary(items, reprt_label)

        # 현금배당결정 공시에서 기준일/지급일/특별배당 파싱
        decisions = []
        try:
            filings = await client.search_filings(
                corp_code=corp_code, bgn_de=f"{bsns_year}0101",
                end_de=f"{int(bsns_year)+1}1231", pblntf_ty="I",
            )
            for item in filings.get("list", []):
                if "현금" in item.get("report_nm", "") and "배당결정" in item.get("report_nm", ""):
                    doc = await client.get_document_cached(item["rcept_no"])
                    parsed = _parse_dividend_decision(doc.get("text", ""))
                    if parsed:
                        parsed["rcept_no"] = item["rcept_no"]
                        parsed["rcept_dt"] = item.get("rcept_dt", "")
                        decisions.append(parsed)
        except (DartClientError, Exception):
            pass

        if format == "json":
            return json.dumps({
                "corp_name": corp_name, "bsns_year": bsns_year,
                **summary, "decisions": decisions,
            }, ensure_ascii=False, indent=2)

        # Markdown
        stlm = summary.get("stlm_dt", "")
        lines = [
            f"# {corp_name} 배당 ({bsns_year} {reprt_label})",
            f"*결산일: {stlm}*\n",
        ]

        # alotMatter 테이블 (연간 요약)
        lines.append("## 연간 요약 (사업보고서)")
        lines.append(f"| 항목 | 당기 | 전기 | 전전기 |")
        lines.append(f"|------|------|------|--------|")
        for item in items:
            lines.append(
                f"| {item['category']}{' (' + item['stock_type'] + ')' if item.get('stock_type') else ''} "
                f"| {item['current']} | {item['previous']} | {item['before_previous']} |"
            )

        lines.append("")
        if summary["cash_dps"]:
            lines.append(f"**연간 DPS (보통주)**: {summary['cash_dps']:,}원")
        if summary["cash_dps_preferred"]:
            lines.append(f"**연간 DPS (우선주)**: {summary['cash_dps_preferred']:,}원")
        if summary["total_amount_mil"]:
            lines.append(f"**배당금 총액**: {summary['total_amount_mil']:,}백만원")
        if summary["payout_ratio_dart"] is not None:
            lines.append(f"**배당성향 (연결)**: {summary['payout_ratio_dart']}%")
        if summary["yield_dart"] is not None:
            lines.append(f"**시가배당률 (보통주, DART 공식)**: {summary['yield_dart']}%")
        if summary.get("yield_preferred_dart") is not None:
            lines.append(f"**시가배당률 (우선주, DART 공식)**: {summary['yield_preferred_dart']}%")

        # 현금배당결정 공시 (회차별 상세)
        if decisions:
            lines.append("")
            lines.append("## 배당 결정 내역 (거래소 공시)")
            lines.append("")
            lines.append("| 배당구분 | DPS(보통) | DPS(우선) | 배당총액 | 기준일 | 지급예정일 | 결의일 | 시가배당률 |")
            lines.append("|---------|----------|----------|---------|--------|----------|--------|----------|")
            for d in decisions:
                pay_date = d.get("payment_date") or "-"
                lines.append(
                    f"| {d.get('dividend_type', '-')} "
                    f"| {d.get('dps_common', 0):,}원 "
                    f"| {d.get('dps_preferred', 0):,}원 "
                    f"| {d.get('total_amount', 0):,}원 "
                    f"| {d.get('record_date', '-')} "
                    f"| {pay_date} "
                    f"| {d.get('board_date', '-')} "
                    f"| {d.get('yield_common', 0)}% |"
                )

            # 우선주 상세 (복수 우선주 포함)
            has_pref = any(d.get("preferred_stocks") for d in decisions)
            if has_pref:
                lines.append("")
                lines.append("### 종류주식(우선주) 상세")
                lines.append("")
                lines.append("| 배당구분 | 종류주식명 | 분류 | DPS | 시가배당률 | 배당총액 |")
                lines.append("|---------|----------|------|-----|----------|---------|")
                for d in decisions:
                    for ps in d.get("preferred_stocks", []):
                        lines.append(
                            f"| {d.get('dividend_type', '-')} "
                            f"| {ps['name']} "
                            f"| {ps['stock_class']} "
                            f"| {ps['dps']:,}원 "
                            f"| {ps['yield_pct']}% "
                            f"| {ps['total_amount']:,}원 |"
                        )

            # 특별배당 표시
            for d in decisions:
                if d.get("has_special"):
                    desc = d.get("special_amount_description", "특별배당 포함")
                    lines.append(f"\n**특별배당**: {desc}")
                if d.get("remarks"):
                    # 핵심 비고만 (200자 이내)
                    remark = d["remarks"][:200]
                    if len(d["remarks"]) > 200:
                        remark += "..."
                    lines.append(f"*비고: {remark}*")

        return "\n".join(lines)

    @mcp.tool()
    async def div_history(
        ticker: str,
        years: int = 3,
        format: str = "md",
    ) -> str:
        """desc: 배당 이력 — 공시 건별 집계. 보통주/우선주 DPS, 배당구분(결산/분기/중간), 기준일, 지급일, 배당성향, 배당수익률, 종류주식 상세.
        when: 배당 추이/패턴을 볼 때. 분기배당 여부, 배당 시작/중단 시그널 감지.
        rule: 현금배당결정 공시(거래소)를 건별로 파싱하여 집계. 배당구분은 공시 자체에 명시(결산배당/분기배당/중간배당). alotMatter는 연간 요약(배당성향/수익률)으로만 사용.
        ref: div_detail, div_manual"""
        client = get_dart_client()
        corp = await client.lookup_corp_code(ticker)
        if not corp:
            return tool_not_found("기업", ticker)

        corp_code = corp["corp_code"]
        corp_name = corp.get("corp_name", ticker)
        stock_code = corp.get("stock_code", "")

        current_year = datetime.now().year
        start_year = current_year - years

        # 1. 현금배당결정 공시 연도별 수집 (페이지네이션 방지)
        all_decisions = []
        for search_year in range(start_year, current_year + 1):
            try:
                filings = await client.search_filings(
                    corp_code=corp_code,
                    bgn_de=f"{search_year}0101",
                    end_de=f"{search_year}1231",
                    pblntf_ty="I",
                )
                for item in filings.get("list", []):
                    nm = item.get("report_nm", "")
                    if "현금" in nm and "배당결정" in nm:
                        try:
                            doc = await client.get_document_cached(item["rcept_no"])
                            parsed = _parse_dividend_decision(doc.get("text", ""))
                            if parsed:
                                parsed["rcept_no"] = item["rcept_no"]
                                parsed["rcept_dt"] = item.get("rcept_dt", "")
                                all_decisions.append(parsed)
                        except Exception:
                            pass
            except DartClientError:
                pass

        # 2. 연도별 그룹핑 (배당기준일 기준)
        by_year = {}
        for d in all_decisions:
            rd = d.get("record_date", "")
            if rd:
                yr = rd[:4]
            else:
                yr = d.get("rcept_dt", "")[:4]
            if yr not in by_year:
                by_year[yr] = []
            by_year[yr].append(d)

        # 3. alotMatter 연간 요약 (배당성향/수익률)
        annual_summaries = {}
        for year in range(start_year, current_year):
            year_str = str(year)
            try:
                data = await client.get_dividend_info(corp_code, year_str, "11011")
                items = _parse_dividend_items(data)
                annual_summaries[year_str] = _build_dividend_summary(items, "기말")
            except DartClientError:
                pass

        # 4. 연도별 집계
        yearly_data = []
        for year in range(start_year, current_year):
            year_str = str(year)
            decisions = by_year.get(year_str, [])
            summary = annual_summaries.get(year_str, {})

            # 공시 건별 DPS 합산
            annual_dps = 0
            decision_details = []
            for d in sorted(decisions, key=lambda x: x.get("record_date", "")):
                dps = d.get("dps_common", 0)
                annual_dps += dps
                decision_details.append({
                    "type": d.get("dividend_type", ""),
                    "dps": dps,
                    "dps_preferred": d.get("dps_preferred", 0),
                    "preferred_stocks": d.get("preferred_stocks", []),
                    "record_date": d.get("record_date"),
                    "payment_date": d.get("payment_date"),
                    "board_date": d.get("board_date"),
                    "yield_pct": d.get("yield_common", 0),
                    "total_amount": d.get("total_amount", 0),
                    "has_special": d.get("has_special", False),
                    "special_desc": d.get("special_amount_description", ""),
                })

            # 배당 패턴 분류
            types = [d["type"] for d in decision_details]
            if "분기배당" in types:
                pattern = "분기배당"
            elif "중간배당" in types:
                pattern = "반기배당"
            elif decisions:
                pattern = "연간배당"
            else:
                pattern = "무배당"

            # 배당성향/수익률: alotMatter 우선
            payout = summary.get("payout_ratio_dart")
            yield_dart = summary.get("yield_dart")

            # KRX 종가 기반 배당수익률 (alotMatter 없을 때)
            calc_yield = None
            if stock_code and annual_dps > 0 and not yield_dart:
                price_data = await client.get_stock_price(stock_code, f"{year}1230")
                if price_data and price_data.get("closing_price", 0) > 0:
                    calc_yield = round(annual_dps / price_data["closing_price"] * 100, 2)

            yearly_data.append({
                "year": year_str,
                "pattern": pattern,
                "annual_dps": annual_dps,
                "decision_count": len(decisions),
                "decisions": decision_details,
                "payout_ratio_dart": payout,
                "yield_dart": yield_dart,
                "calc_yield": calc_yield,
            })

        if format == "json":
            return json.dumps({"corp_name": corp_name, "history": yearly_data}, ensure_ascii=False, indent=2)

        # Markdown
        lines = [f"# {corp_name} 배당 이력 ({start_year}-{current_year - 1})\n"]

        # 연도별 요약 테이블
        lines.append("| 연도 | 패턴 | 연간 DPS | 공시 수 | 배당성향 | 배당수익률 |")
        lines.append("|------|------|----------|--------|----------|------------|")

        for yd in yearly_data:
            pr = f"{yd['payout_ratio_dart']}%" if yd["payout_ratio_dart"] else "-"
            if yd["yield_dart"]:
                dy = f"{yd['yield_dart']}%"
            elif yd["calc_yield"]:
                dy = f"{yd['calc_yield']}% (KRX)"
            else:
                dy = "-"
            lines.append(
                f"| {yd['year']} | {yd['pattern']} | {yd['annual_dps']:,}원 | {yd['decision_count']}건 | {pr} | {dy} |"
            )

        # 건별 상세
        lines.append("\n## 건별 상세\n")
        lines.append("| 연도 | 구분 | DPS | 기준일 | 지급예정일 | 결의일 | 시가배당률 | 특별 |")
        lines.append("|------|------|-----|--------|----------|--------|----------|------|")

        for yd in yearly_data:
            for d in yd["decisions"]:
                pay = d.get("payment_date") or "-"
                special = d.get("special_desc") or ("O" if d.get("has_special") else "-")
                yld = f"{d['yield_pct']}%" if d.get("yield_pct") else "-"
                lines.append(
                    f"| {yd['year']} | {d['type']} | {d['dps']:,}원 | {d.get('record_date', '-')} | {pay} | {d.get('board_date', '-')} | {yld} | {special} |"
                )

        lines.append("")
        lines.append("*배당성향 = 배당금총액 / 지배주주귀속 당기순이익 × 100 (DART alotMatter)*")
        lines.append("*배당수익률 = DART 시가배당률 우선, 없으면 연간 DPS / KRX 종가*")

        return "\n".join(lines)

    @mcp.tool()
    async def div(
        ticker: str,
        format: str = "md",
    ) -> str:
        """desc: 배당 종합 — 최근 배당 상세 + 3년 추이. 보통주/우선주 모두 포함.
        when: 기업의 배당 정책/현황을 종합적으로 볼 때.
        rule: div_detail(최신) + div_history(3년)를 합쳐서 반환.
        ref: div_detail, div_history, div_manual"""
        # 최신 기말 배당
        detail = await div_detail(ticker=ticker, format=format)
        # 3년 이력
        history = await div_history(ticker=ticker, years=3, format=format)

        return f"{detail}\n\n---\n\n{history}"

    @mcp.tool()
    async def div_manual() -> str:
        """desc: 배당 tool 구조, 출력 형태 가이드, 연산 규칙, 판정 기준.
        when: 배당 분석 시 또는 연산 방법 확인이 필요할 때.
        ref: DIV_TOOL_RULE.md, DIV_CASE_RULE.md"""
        pkg_dir = os.path.dirname(os.path.dirname(__file__))
        parts = []
        for fname in ("DIV_TOOL_RULE.md", "DIV_CASE_RULE.md"):
            fpath = os.path.join(pkg_dir, fname)
            try:
                with open(fpath, "r") as f:
                    parts.append(f.read())
            except FileNotFoundError:
                parts.append(f"\n({fname}를 찾을 수 없습니다)")
        return "\n\n---\n\n".join(parts)
