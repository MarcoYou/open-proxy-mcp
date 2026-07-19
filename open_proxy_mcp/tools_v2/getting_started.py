"""getting_started — OPM 전체 개요를 자동 생성(하드코딩 markdown 없음).

260721 설계 — MCP 프로토콜·LLM tool-use·멀티클라이언트·DX 4인 전문가 패널 검토:
- 포괄적 "OPM으로 뭐 할 수 있어?" 질문은 model-controlled 판단 영역이라 tool이 맞는 프리미티브
  (resource는 application-controlled라 Claude/ChatGPT/Perplexity 3사 동시 지원 보장 안 됨).
- 과거 v1 `tool_guide`(open_proxy_mcp/tools/guide.py)가 v2 재설계 후 완전히 단절된 채 방치되어
  현재 등록된 tool과 이름조차 안 겹치는 죽은 코드가 된 전례 — 하드코딩 markdown은 반드시 썩는다.
  → 이 tool은 **런타임에 실제 등록된 tool 목록에서 desc: 필드를 그대로 추출**해 조립한다.
  tool이 추가·제거돼도 이 페이지는 구조적으로 항상 최신이다.
- 카테고리 그루핑만 최소 name→category 매핑(아래)으로 유지 — 매핑에 없는 새 tool은 "기타"로
  떨어질 뿐 절대 누락되지 않는다(무매핑 시 침묵 삭제 방지, 실측: 위 tool_guide는 침묵 삭제였음).
"""
from __future__ import annotations

import re

_DESC_RE = re.compile(r"desc:\s*(.+?)(?:\n\s*when:|\Z)", re.S)

_CATEGORY: dict[str, str] = {
    "company": "기본 — 회사 찾기",
    "screener": "전체시장 스캔 · 디제스트",
    "shareholder_meeting_notice": "주주총회 · 의결권",
    "shareholder_meeting_results": "주주총회 · 의결권",
    "proxy_advise_before_meeting": "주주총회 · 의결권",
    "shareholder_commitment": "주주총회 · 의결권",
    "ownership_structure": "지분 · 재무 · 밸류에이션",
    "financial_metrics": "지분 · 재무 · 밸류에이션",
    "valuation": "지분 · 재무 · 밸류에이션",
    "business_details": "지분 · 재무 · 밸류에이션",
    "provisional_earnings": "지분 · 재무 · 밸류에이션",
    "asset_holdings": "지분 · 재무 · 밸류에이션",
    "corp_gov_report": "지분 · 재무 · 밸류에이션",
    "director_board": "지분 · 재무 · 밸류에이션",
    "dividend": "주주환원 · 자본",
    "treasury_share": "주주환원 · 자본",
    "value_up": "주주환원 · 자본",
    "corporate_restructuring": "주주환원 · 자본",
    "dilutive_issuance": "주주환원 · 자본",
    "proxy_contest": "분쟁 · 거래 · 리스크",
    "corporate_deals": "분쟁 · 거래 · 리스크",
    "order_contracts": "분쟁 · 거래 · 리스크",
    "risk_events": "분쟁 · 거래 · 리스크",
    "evidence": "근거 · 참조",
    "law_lookup": "근거 · 참조",
}
_CATEGORY_ORDER = [
    "기본 — 회사 찾기", "전체시장 스캔 · 디제스트", "주주총회 · 의결권",
    "지분 · 재무 · 밸류에이션", "주주환원 · 자본", "분쟁 · 거래 · 리스크",
    "근거 · 참조", "기타(신규 tool — 분류 미지정)",
]


def _extract_desc(description: str) -> str:
    """docstring의 'desc: ...' 구간만 추출(다음 필드 전까지), 개행·들여쓰기 정리."""
    m = _DESC_RE.search(description or "")
    text = m.group(1) if m else (description or "").split("\n", 1)[0]
    return re.sub(r"\s+", " ", text).strip()


async def _build_guide(mcp) -> str:
    tools = [t for t in await mcp.list_tools() if t.name != "getting_started"]
    by_cat: dict[str, list[tuple[str, str]]] = {}
    for t in tools:
        cat = _CATEGORY.get(t.name, "기타(신규 tool — 분류 미지정)")
        by_cat.setdefault(cat, []).append((t.name, _extract_desc(t.description)))

    lines = [
        "# OpenProxy MCP — 무엇을 할 수 있나",
        "",
        "한국 상장사 DART 공시 분석 서버입니다. tool 이름을 몰라도 자연어로 물어보면 알맞은 tool이 "
        "자동으로 선택됩니다. 회사를 특정해야 하는 질문이면 `company`부터 시작하세요.",
        "",
    ]
    for cat in _CATEGORY_ORDER:
        items = by_cat.get(cat)
        if not items:
            continue
        lines.append(f"## {cat}")
        for name, desc in items:
            lines.append(f"- **`{name}`** — {desc}")
        lines.append("")
    lines.append(
        f"_전체 {len(tools)}개 tool. 이 목록은 매 호출 시 실제 등록된 tool에서 자동 생성됩니다 "
        "(하드코딩 아님 — tool이 추가/변경돼도 항상 최신)._"
    )
    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def getting_started() -> str:
        """desc: OpenProxy MCP가 무엇을 할 수 있는지 카테고리별로 정리해 보여줍니다. 처음 연결했거나 전체 기능이 궁금할 때 이 tool로 답하세요.
        when: "OPM으로 뭐 할 수 있어?", "무슨 기능 있어?", "what can this do?", "what tools do you have?" 같은 포괄적 capability 질문. 특정 회사·데이터에 대한 질문이면 이 tool 없이 바로 해당 tool을 쓰세요.
        rule: DART 호출 없음(0콜, 항상 즉시 응답). 등록된 tool 목록에서 매번 새로 조립하므로 하드코딩 드리프트가 구조적으로 불가능.
        ref: company
        """
        return await _build_guide(mcp)
