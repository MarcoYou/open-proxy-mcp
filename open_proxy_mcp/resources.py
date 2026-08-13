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
from mcp.types import Annotations

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
        # 스펙(server/resources)은 resource 를 **application-driven** 으로 정의한다 —
        # 호스트가 ①사용자 선택 UI ②검색 ③자동 포함 중 무엇을 할지 정한다.
        # 우리가 원하는 건 ③(파싱이 약할 때 모델이 원문을 집어감)이고, 그 판단의 근거로
        # 클라이언트가 읽는 것이 annotations 다.
        #   audience  사람도 붙여 볼 수 있고(원문 첨부) 모델도 읽어야 하므로 둘 다.
        #   priority  1.0 은 「사실상 필수」다. 이건 항상 넣을 자료가 아니라 **필요할 때
        #             권위 있는 원본**이므로 높되 1.0 은 아니다.
        annotations=Annotations(audience=["user", "assistant"], priority=0.8),
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

    #: 의결권 판단 기준 문서. **판정 사유에 「OPM Guideline §재무제표 …」로 인용되는 그 문서다.**
    #: 260813: 인용문은 `_POLICY_CITATIONS`(proxy_advise.py) 에 손으로 적어둔 14줄이고,
    #:   이 문서와 코드가 연결돼 있지 않다 — 셋(문서·라벨·판정 함수)이 수기 동기화 상태다.
    #:   최소한 **문서 자체를 열람 가능하게** 해서, 「왜 찬성이냐」에 원문으로 답할 수 있게 한다.
    #: ⚠ 배포 주의: Dockerfile 이 `wiki/rules/laws/` 만 복사하므로 fly 이미지에 이 파일이 없다.
    #:   없으면 그 사실을 그대로 말한다(조용히 빈 값을 주지 않는다 — 무표시 열화 금지).
    @mcp.resource(
        "opm://guideline",
        name="Open Proxy Guideline",
        description=(
            "OPM 의결권 행사 정책 원문. proxy_advise_before_meeting 의 판정 사유에 "
            "「OPM Guideline §…」로 인용되는 기준 문서다. 특정 판정의 근거를 확인하거나 "
            "정책 전문이 필요할 때 읽는다."
        ),
        mime_type="text/markdown",
        annotations=Annotations(audience=["user", "assistant"], priority=0.7),
    )
    async def guideline() -> str:
        from pathlib import Path

        # 레포 루트 기준. 패키지 위치에서 두 단계 위가 루트다.
        path = Path(__file__).resolve().parent.parent / "wiki" / "decisions" / "open-proxy-guideline.md"
        if not path.exists():
            return (
                "가이드라인 문서를 찾지 못했습니다.\n\n"
                f"기대 경로: {path}\n"
                "배포 이미지에 `wiki/decisions/open-proxy-guideline.md` 가 포함되지 않았을 수 있습니다"
                "(Dockerfile 이 현재 `wiki/rules/laws/` 만 복사합니다).\n"
                "판정 사유에 실리는 요약 인용은 응답의 「정책 인용」 줄에서 확인할 수 있습니다."
            )
        return path.read_text(encoding="utf-8")
