"""[실험] MCP resource — 공시 원문을 주소로 노출한다.

왜 필요한가 (260813 실측):
  소집공고 안건 파싱이 0건이면 `raw_text_fallback`(proxy_advise.py:4101)이 **안 붙는다**
  — 그 코드는 「안건이 있고 그 안건의 decision 이 NO_DATA」일 때만 돈다. 안건 자체가
  없으면 원문이 응답에 한 글자도 안 실린다(실측: 페니트리움바이오 20260220000017).
  응답의 DART 웹 링크는 AI 가 열 수 없는 그냥 글자다.

  그런데 파싱이 약할 때 원문을 읽은 AI 가 결정론 판정보다 훨씬 잘한다(같은 문서에서
  감사위원 겸직자 스톡옵션 부여를 잡아냄). 그 경로를 **주소로 열어 준다.**

DART 호출: `get_document_cached` 를 쓰므로 캐시에 있으면 0콜.
"""

from __future__ import annotations

import re

from mcp.server.mcpserver import MCPServer

#: 응답 상한. 소집공고는 최대 7MB 라 통째로 주면 컨텍스트가 터진다.
_MAX_CHARS = 120_000


def _clean(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text or "")).strip()


def register_all_resources(mcp: MCPServer) -> None:
    @mcp.resource(
        "opm://filing/{rcept_no}",
        name="DART 공시 원문",
        description=(
            "접수번호(rcept_no, 14자리)로 공시 원문 텍스트를 읽는다. "
            "구조화 파싱이 약하다고 표시된 경우(warnings 에 agenda_parse_low_confidence 등) "
            "이 원문을 직접 읽고 판단하라."
        ),
        mime_type="text/plain",
    )
    async def filing_text(rcept_no: str) -> str:
        if not re.fullmatch(r"\d{14}", rcept_no or ""):
            return "잘못된 접수번호입니다 — 14자리 숫자여야 합니다."
        from open_proxy_mcp.dart.client import get_dart_client

        doc = await get_dart_client().get_document_cached(rcept_no)
        text = _clean(doc.get("text") or "")
        if not text:
            return f"[{rcept_no}] 원문 텍스트를 가져오지 못했습니다."
        if len(text) > _MAX_CHARS:
            return text[:_MAX_CHARS] + f"\n\n…(이후 {len(text) - _MAX_CHARS:,}자 생략)"
        return text
