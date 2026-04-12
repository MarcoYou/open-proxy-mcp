"""거버넌스 종합 보고서 tool (governance_report)

AGM + OWN + DIV 3개 도메인을 한 번에 조회하는 최상위 체인 tool.
"""

import json
from open_proxy_mcp.tools.formatters import resolve_ticker
from open_proxy_mcp.tools.shareholder import register_tools as _reg_agm
from open_proxy_mcp.tools.ownership import register_tools as _reg_own
from open_proxy_mcp.tools.dividend import register_tools as _reg_div

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

_agm_post_analysis = _domain_tools["agm_post_analysis"]
_own_full_analysis = _domain_tools["own_full_analysis"]
_div_full_analysis = _domain_tools["div_full_analysis"]


def register_tools(mcp):

    @mcp.tool()
    async def governance_report(
        ticker: str,
        format: str = "md",
    ) -> str:
        """desc: 거버넌스 종합 보고서 — 주총(AGM) + 지분(OWN) + 배당(DIV) 3개 도메인 통합.
        when: [tier-4 Orchestrate] 특정 기업의 거버넌스 전체를 한 번에 파악할 때. 주총 안건/투표결과 + 지분구조 + 배당 이력을 종합적으로 볼 때.
        rule: corp_identifier 실행 후 호출. agm_post_analysis + own_full_analysis + div_full_analysis 체이닝. 이 tool 하나로 충분하며 개별 domain tool 추가 호출 금지.
        ref: corp_identifier, agm_post_analysis, own_full_analysis, div_full_analysis
        """
        ticker = await resolve_ticker(ticker)
        import asyncio

        _format = format
        agm_out, own_out, div_out = await asyncio.gather(
            _agm_post_analysis(ticker=ticker),
            _own_full_analysis(ticker=ticker, format=_format),
            _div_full_analysis(ticker=ticker, format=_format),
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
            }, ensure_ascii=False, indent=2)

        # Markdown — 섹션별 조합
        sep = "\n\n" + "─" * 60 + "\n\n"
        return (
            f"# {ticker} 거버넌스 종합 보고서\n\n"
            f"## 🏛 주주총회 (AGM)\n\n{agm_out}"
            f"{sep}"
            f"## 🏢 지분 구조 (OWN)\n\n{own_out}"
            f"{sep}"
            f"## 💰 배당 (DIV)\n\n{div_out}"
        )
