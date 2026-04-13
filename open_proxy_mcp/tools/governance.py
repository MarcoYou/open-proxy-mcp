"""거버넌스 종합 보고서 tool (governance_report)

AGM + OWN + DIV + PRX + VUP 5개 도메인을 한 번에 조회하는 최상위 체인 tool.
"""

import json
from open_proxy_mcp.tools.formatters import resolve_ticker
from open_proxy_mcp.tools.shareholder import register_tools as _reg_agm
from open_proxy_mcp.tools.ownership import register_tools as _reg_own
from open_proxy_mcp.tools.dividend import register_tools as _reg_div
from open_proxy_mcp.tools.proxy import register_tools as _reg_prx
from open_proxy_mcp.tools.value_up import register_tools as _reg_vup

# 의존 tool 함수를 모듈 레벨에서 한 번만 등록
_domain_tools: dict = {}

class _NullMCP:
    def tool(self):
        def d(fn):
            _domain_tools[fn.__name__] = fn
            return fn
        return d

_null = _NullMCP()
_reg_agm(_null)
_reg_own(_null)
_reg_div(_null)
_reg_prx(_null)
_reg_vup(_null)

_agm_post_analysis = _domain_tools["agm_post_analysis"]
_ownership_full_analysis = _domain_tools["ownership_full_analysis"]
_div_full_analysis = _domain_tools["div_full_analysis"]
_proxy_full_analysis = _domain_tools["proxy_full_analysis"]
_value_up_plan = _domain_tools["value_up_plan"]


def register_tools(mcp):

    @mcp.tool()
    async def governance_report(
        ticker: str,
        format: str = "md",
    ) -> str:
        """desc: 거버넌스/지배구조(corporate governance) 종합 보고서 -- AGM + OWN + DIV + PRX + VUP 5개 도메인 통합.
        when: [tier-4 Orchestrate] 거버넌스, 지배구조, corporate governance 전체를 한 번에 파악할 때. 주총 안건/투표결과 + 지분구조 + 배당 이력 + 경영권 분쟁 + 밸류업을 종합적으로 볼 때.
        rule: corp_identifier 실행 후 호출. 5개 도메인 asyncio.gather 병렬. 이 tool 하나로 충분하며 개별 domain tool 추가 호출 금지.
        ref: corp_identifier, agm_post_analysis, ownership_full_analysis, div_full_analysis, proxy_full_analysis, value_up_plan
        """
        ticker = await resolve_ticker(ticker)
        import asyncio

        _format = format
        agm_out, own_out, div_out, prx_out, vup_out = await asyncio.gather(
            _agm_post_analysis(ticker=ticker),
            _ownership_full_analysis(ticker=ticker, format=_format),
            _div_full_analysis(ticker=ticker, format=_format),
            _proxy_full_analysis(ticker=ticker, format=_format),
            _value_up_plan(ticker=ticker, format=_format),
        )

        if format == "json":
            def _parse(s):
                try:
                    return json.loads(s)
                except Exception:
                    return {"raw": s}

            return json.dumps({
                "ticker": ticker,
                "agm": _parse(agm_out),
                "own": _parse(own_out),
                "div": _parse(div_out),
                "prx": _parse(prx_out),
                "vup": _parse(vup_out),
            }, ensure_ascii=False, indent=2)

        # Markdown -- 섹션별 조합
        sep = "\n\n" + "─" * 60 + "\n\n"
        return (
            f"# {ticker} 거버넌스 종합 보고서\n\n"
            f"## 🏛 주주총회 (AGM)\n\n{agm_out}"
            f"{sep}"
            f"## 🏢 지분 구조 (OWN)\n\n{own_out}"
            f"{sep}"
            f"## 💰 배당 (DIV)\n\n{div_out}"
            f"{sep}"
            f"## ⚔️ 경영권 분쟁 (PRX)\n\n{prx_out}"
            f"{sep}"
            f"## 📈 기업가치제고 (VUP)\n\n{vup_out}"
        )
