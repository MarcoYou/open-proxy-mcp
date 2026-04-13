"""기업가치제고계획 MCP tool (value_up_plan)

거버넌스 분석 체인의 독립 도메인.
기업가치제고계획(밸류업) 공시를 DART에서 검색·파싱.
"""

import json
from datetime import datetime

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.tools.formatters import resolve_ticker, strip_css
from open_proxy_mcp.tools.errors import tool_error, tool_not_found, tool_empty


_VALUATION_KEYWORDS = ("기업가치제고", "기업가치 제고", "밸류업")


def register_tools(mcp):

    @mcp.tool()
    async def value_up_plan(
        ticker: str,
        year: str = "",
        format: str = "md",
    ) -> str:
        """desc: 기업가치제고계획(밸류업) 공시 검색 및 원문 파싱.
        when: [tier-4 Analysis] 기업가치제고, 밸류업, value-up 계획을 확인할 때. 배당·지분 분석과 함께 거버넌스 전체 그림 파악에 활용.
        rule: DART pblntf_ty=I(거래소공시)에서 밸류업 키워드 필터. 원문 최신 2건 파싱.
        ref: corp_identifier, governance_report, div_full_analysis, ownership_full_analysis
        """
        ticker = await resolve_ticker(ticker)
        client = get_dart_client()
        corp = await client.lookup_corp_code(ticker)
        if not corp:
            return tool_not_found("기업", ticker)

        corp_code = corp["corp_code"]
        corp_name = corp["corp_name"]
        now = datetime.now()
        bsns_year = year or str(now.year)
        bgn_de = f"{int(bsns_year) - 1}0101"
        end_de = f"{bsns_year}1231"

        try:
            result = await client.search_filings(
                bgn_de=bgn_de,
                end_de=end_de,
                corp_code=corp_code,
                pblntf_ty="I",
                page_count=100,
            )
        except DartClientError as e:
            return tool_error("기업가치제고 공시 검색", e, ticker=ticker)

        items = result.get("list", [])
        valuation_items = [
            item for item in items
            if any(kw in (item.get("report_nm") or "").replace(" ", "")
                   for kw in _VALUATION_KEYWORDS)
        ]

        if not valuation_items:
            return tool_empty("기업가치제고 공시", f"{ticker} {bsns_year}년")

        valuation_items.sort(key=lambda x: x.get("rcept_dt", ""), reverse=True)

        # 최신 2건 원문 파싱
        detail_items = [
            i for i in valuation_items
            if "[기재정정]" not in (i.get("report_nm") or "")
        ][:2]
        details = {}
        for item in detail_items:
            try:
                doc = await client.get_document(item["rcept_no"])
                text = strip_css(doc.get("text", "") or "")
                details[item["rcept_no"]] = text[:5000]
            except Exception:
                pass

        if format == "json":
            return json.dumps({
                "corp_name": corp_name,
                "period": f"{bgn_de[:4]}-{end_de[:4]}",
                "count": len(valuation_items),
                "items": [{
                    "rcept_no": i.get("rcept_no", ""),
                    "rcept_dt": i.get("rcept_dt", ""),
                    "report_nm": (i.get("report_nm") or "").strip(),
                    "flr_nm": i.get("flr_nm", ""),
                    "detail": details.get(i.get("rcept_no"), ""),
                } for i in valuation_items],
            }, ensure_ascii=False, indent=2)

        lines = [
            f"# {corp_name} 기업가치제고계획",
            f"기간: {bgn_de[:4]}-{end_de[:4]} | 총 {len(valuation_items)}건",
            "",
            "| 날짜 | 공시명 | 제출인 | rcept_no |",
            "|------|--------|--------|----------|",
        ]
        for item in valuation_items:
            dt = item.get("rcept_dt", "")
            dt_fmt = f"{dt[:4]}.{dt[4:6]}.{dt[6:8]}" if len(dt) == 8 else dt
            rn = (item.get("report_nm") or "").strip()
            lines.append(
                f"| {dt_fmt} | {rn} | {item.get('flr_nm', '')} | `{item.get('rcept_no', '')}` |"
            )

        if details:
            lines.append("")
            lines.append("## 기업가치제고계획 원문")
            for rcept_no, text in details.items():
                lines.append(f"\n### `{rcept_no}`")
                lines.append("```")
                lines.append(text[:4000])
                lines.append("```")

        return "\n".join(lines)
