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


def _parse_dividend_items(data: dict) -> list[dict]:
    """alotMatter API 응답 → 정규화된 배당 항목 리스트"""
    items = data.get("list", [])
    if not items:
        return []

    results = []
    for item in items:
        se = item.get("se", "")  # 구분 (현금배당, 주식배당 등)
        stock_knd = item.get("stock_knd", "")  # 주식 종류

        parsed = {
            "category": se,
            "stock_type": stock_knd,
            "current": item.get("thstrm", ""),          # 당기
            "previous": item.get("frmtrm", ""),          # 전기
            "before_previous": item.get("lwfr", ""),     # 전전기
            "is_special": "특별" in se,
            "is_stock_dividend": "주식" in se,
        }
        results.append(parsed)

    return results


def _build_dividend_summary(items: list[dict], reprt_label: str) -> dict:
    """배당 항목 리스트 → 요약 구조체"""
    cash_dps = 0  # 현금 주당배당금
    stock_dps = 0  # 주식 주당배당금
    special_dps = 0  # 특별배당 주당배당금
    total_amount = 0  # 배당금 총액
    payout_ratio_dart = None  # DART 제공 배당성향
    yield_dart = None  # DART 제공 시가배당률

    for item in items:
        category = item.get("category", "")
        current = item.get("current", "")

        # 주당배당금 행
        if "주당" in category and "배당금" in category:
            val = _safe_int(current)
            if item.get("is_special"):
                special_dps += val
            elif item.get("is_stock_dividend"):
                stock_dps += val
            else:
                cash_dps += val

        # 배당금총액 행
        if "배당금총액" in category or "총액" in category:
            total_amount = _safe_int(current)

        # 배당성향 행
        if "배당성향" in category:
            val = _safe_float(current)
            if val > 0:
                payout_ratio_dart = val

        # 시가배당률 행
        if "시가배당률" in category or "배당수익률" in category:
            val = _safe_float(current)
            if val > 0:
                yield_dart = val

    return {
        "period": reprt_label,
        "cash_dps": cash_dps,
        "stock_dps": stock_dps,
        "special_dps": special_dps,
        "total_dps": cash_dps + special_dps,
        "total_amount": total_amount,
        "payout_ratio_dart": payout_ratio_dart,
        "yield_dart": yield_dart,
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
        """desc: 배당 상세 — 주당배당금, 배당총액, 배당성향, 시가배당률, 특별배당 여부.
        when: 특정 사업연도의 배당 내용을 볼 때.
        rule: reprt_code로 분기 선택 (11011=기말, 11012=반기, 11013=1Q, 11014=3Q). DART 제공 배당성향/시가배당률이 있으면 우선 사용. 없으면 div_history에서 계산.
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

        if format == "json":
            return json.dumps({"corp_name": corp_name, "bsns_year": bsns_year, **summary}, ensure_ascii=False, indent=2)

        # Markdown
        lines = [
            f"# {corp_name} 배당 ({bsns_year} {reprt_label})\n",
            f"| 항목 | 당기 | 전기 | 전전기 |",
            f"|------|------|------|--------|",
        ]
        for item in items:
            lines.append(
                f"| {item['category']} | {item['current']} | {item['previous']} | {item['before_previous']} |"
            )

        lines.append("")
        if summary["cash_dps"]:
            lines.append(f"**현금배당 DPS**: {summary['cash_dps']:,}원")
        if summary["special_dps"]:
            lines.append(f"**특별배당 DPS**: {summary['special_dps']:,}원")
        if summary["stock_dps"]:
            lines.append(f"**주식배당 DPS**: {summary['stock_dps']:,}원 (주)")
        if summary["total_amount"]:
            lines.append(f"**배당금 총액**: {summary['total_amount']:,}원")
        if summary["payout_ratio_dart"] is not None:
            lines.append(f"**배당성향 (DART)**: {summary['payout_ratio_dart']}%")
        if summary["yield_dart"] is not None:
            lines.append(f"**시가배당률 (DART)**: {summary['yield_dart']}%")

        return "\n".join(lines)

    @mcp.tool()
    async def div_history(
        ticker: str,
        years: int = 3,
        format: str = "md",
    ) -> str:
        """desc: 배당 이력 — 연도별 DPS, 배당성향, 배당수익률 추이. 배당성향은 지배주주 귀속 당기순이익 기준.
        when: 배당 추이를 볼 때. 배당성향/수익률을 직접 계산.
        rule: 배당성향 = 배당금총액 / 지배주주귀속 당기순이익 × 100 (별도 재무제표 순이익 사용 금지). 배당수익률 = DPS / 배당기준일 종가 × 100 (KRX API). 분기배당 시 연간 합산 DPS 산출. DART 제공 값이 있으면 우선 사용, 없으면 계산.
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

        yearly_data = []
        for year in range(start_year, current_year):
            year_str = str(year)

            # 기말(사업보고서) 배당 조회
            try:
                data = await client.get_dividend_info(corp_code, year_str, "11011")
                items = _parse_dividend_items(data)
                summary = _build_dividend_summary(items, "기말")
            except DartClientError:
                summary = {"cash_dps": 0, "total_dps": 0, "total_amount": 0,
                           "payout_ratio_dart": None, "yield_dart": None,
                           "special_dps": 0, "stock_dps": 0, "period": "기말", "items": []}

            # 분기배당 체크 (1Q, 반기, 3Q)
            quarterly_dps = []
            for rc, label in [("11013", "1Q"), ("11012", "반기"), ("11014", "3Q")]:
                try:
                    qdata = await client.get_dividend_info(corp_code, year_str, rc)
                    qitems = _parse_dividend_items(qdata)
                    qsummary = _build_dividend_summary(qitems, label)
                    if qsummary["cash_dps"] > 0:
                        quarterly_dps.append({"period": label, "dps": qsummary["cash_dps"]})
                except DartClientError:
                    pass

            # 연간 합산 DPS
            annual_dps = summary["cash_dps"]
            for q in quarterly_dps:
                annual_dps += q["dps"]

            # KRX 종가 조회 (배당기준일 = 결산일 기준, 12/31 또는 직전 거래일)
            closing_price = None
            calc_yield = None
            if stock_code and annual_dps > 0:
                # 결산일 기준 (12월 결산 기업 기준)
                for dd in [f"{year}1230", f"{year}1229", f"{year}1228", f"{year}1227"]:
                    price_data = await client.get_krx_stock_price(stock_code, dd)
                    if price_data and price_data.get("closing_price", 0) > 0:
                        closing_price = price_data["closing_price"]
                        calc_yield = round(annual_dps / closing_price * 100, 2)
                        break

            yearly_data.append({
                "year": year_str,
                "annual_dps": annual_dps,
                "final_dps": summary["cash_dps"],
                "special_dps": summary["special_dps"],
                "stock_dps": summary["stock_dps"],
                "quarterly": quarterly_dps,
                "total_amount": summary["total_amount"],
                "payout_ratio_dart": summary["payout_ratio_dart"],
                "yield_dart": summary["yield_dart"],
                "closing_price": closing_price,
                "calc_yield": calc_yield,
            })

        if format == "json":
            return json.dumps({"corp_name": corp_name, "history": yearly_data}, ensure_ascii=False, indent=2)

        # Markdown
        lines = [f"# {corp_name} 배당 이력 ({start_year}-{current_year - 1})\n"]

        # 요약 테이블
        lines.append("| 연도 | 연간 DPS | 기말 DPS | 분기 DPS | 특별 | 배당성향 | 배당수익률 |")
        lines.append("|------|----------|----------|----------|------|----------|------------|")

        for yd in yearly_data:
            q_str = "+".join(f"{q['period']} {q['dps']:,}" for q in yd["quarterly"]) if yd["quarterly"] else "-"
            special = f"{yd['special_dps']:,}" if yd["special_dps"] else "-"

            # 배당성향: DART 값 우선, 없으면 빈칸 (지배순이익 데이터 없으므로 계산 불가)
            pr = f"{yd['payout_ratio_dart']}%" if yd["payout_ratio_dart"] else "-"

            # 배당수익률: DART 값 우선, 없으면 KRX 계산값
            if yd["yield_dart"]:
                dy = f"{yd['yield_dart']}%"
            elif yd["calc_yield"]:
                dy = f"{yd['calc_yield']}% (계산)"
            else:
                dy = "-"

            lines.append(
                f"| {yd['year']} | {yd['annual_dps']:,}원 | {yd['final_dps']:,}원 | {q_str} | {special} | {pr} | {dy} |"
            )

        lines.append("")
        lines.append("*배당성향 = 배당금총액 / 지배주주귀속 당기순이익 × 100*")
        lines.append("*배당수익률 = 연간 DPS / 배당기준일 종가 × 100*")
        lines.append("*분기배당 기업은 연간 DPS = 기말 + 각 분기 합산*")

        return "\n".join(lines)

    @mcp.tool()
    async def div(
        ticker: str,
        format: str = "md",
    ) -> str:
        """desc: 배당 종합 — 최근 배당 상세 + 3년 추이.
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
