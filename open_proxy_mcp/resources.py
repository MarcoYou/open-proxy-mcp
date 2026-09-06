"""MCP resources — 기능 안내, 공시 원문, 의결권 정책을 주소로 제공한다.

왜 필요한가 (260813 실측):
  소집공고 안건 파싱이 0건이면 `raw_text_fallback`(proxy_advise.py `_build_proxy_advise_payload`)이 **안 붙는다**
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

from open_proxy_mcp.services.filing_sections import toc_uri

#: 응답 상한. 소집공고는 최대 7MB 라 통째로 주면 컨텍스트가 터진다.
_MAX_CHARS = 120_000


def _clean(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text or "")).strip()


def register_all_resources(mcp: MCPServer) -> None:
    @mcp.resource(
        "opm://tools_guide",
        name="tools_guide",
        title="OpenProxy 기능 안내",
        description=(
            "OpenProxy에서 어떤 기능을 쓸 수 있는지 안내한다. "
            "현재 제공하는 도구 전체와 각 도구가 답하는 내용을 확인할 때 읽는다."
        ),
        mime_type="text/markdown",
        annotations=Annotations(audience=["user", "assistant"], priority=0.6),
    )
    async def tools_guide() -> str:
        # 도구 이름·설명·개수는 등록된 목록에서 읽는다. 별도 안내문에 복사하면
        # 개명·추가 때 뒤처진다. 여러 줄에 걸친 첫 문단도 끝까지 보존한다.
        tools = sorted(await mcp.list_tools(), key=lambda t: (t.name != "company", t.name))
        lines = [
            "# OpenProxy 기능 안내",
            "",
            "회사명이나 종목코드와 함께 궁금한 내용을 자연어로 물어보세요. "
            "도구 이름을 외울 필요는 없습니다.",
            "",
            f"현재 제공하는 도구는 {len(tools)}개입니다. "
            "아래 안내는 이 서버에 등록된 도구 설명에서 가져옵니다.",
        ]
        for tool in tools:
            description = (tool.description or "설명이 등록되지 않았습니다.").strip()
            summary = re.split(r"\n\s*\n|\n\s*(?:when|rule|ref):", description, maxsplit=1)[0]
            summary = re.sub(r"^desc:\s*", "", summary)
            lines.extend(["", f"## {tool.name}", "", " ".join(summary.split())])
        return "\n".join(lines)

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

        # 260906: 사업보고서는 82만~165만 자라 이 상한(12만)에서 III 장(재무)이 잘린다. 뒤쪽 장이
        #   필요하면 목차에서 절을 골라 읽는 길을 머리에 적는다 — 그 길은 자르지 않는다.
        head = (f"[목차] {toc_uri(rcept_no)} — 이 전문은 {_MAX_CHARS:,}자에서 잘린다. "
                f"특정 장·절(직원 현황·계열회사·우발부채·주석 항목 등)은 목차에서 절 번호를 골라 "
                f"opm://filing/{rcept_no}/section/{{no}} 로 읽는 편이 빠르고 끝까지 닿는다.\n\n")
        doc = await get_dart_client().get_document_cached(rcept_no)
        text = _clean(doc.get("text") or "")
        if not text:
            return head + f"[{rcept_no}] 원문 텍스트를 가져오지 못했습니다."
        if len(text) > _MAX_CHARS:
            return head + text[:_MAX_CHARS] + f"\n\n…(이후 {len(text) - _MAX_CHARS:,}자 생략 — 절 단위는 {toc_uri(rcept_no)})"
        return head + text

    @mcp.resource(
        "opm://filing/{rcept_no}/toc",
        name="DART 공시 목차",
        description=(
            "접수번호(rcept_no, 14자리)로 공시의 목차(장·절·항)를 읽는다. 각 절에 읽기 주소 "
            "opm://filing/{rcept_no}/section/{no} 가 붙는다. 전문이 잘려서 뒤쪽 장이 필요하거나, "
            "특정 절(직원 현황·계열회사·우발부채·재무제표 주석 항목 등)만 필요할 때 먼저 읽는다."
        ),
        mime_type="text/markdown",
        annotations=Annotations(audience=["user", "assistant"], priority=0.8),
    )
    async def filing_toc(rcept_no: str) -> str:
        if not re.fullmatch(r"\d{14}", rcept_no or ""):
            return "잘못된 접수번호입니다 — 14자리 숫자여야 합니다."
        from open_proxy_mcp.dart.client import DartClientError, get_dart_client
        from open_proxy_mcp.services.filing_sections import get_toc, render_toc

        try:
            toc = await get_toc(get_dart_client(), rcept_no)
        except DartClientError as exc:
            return f"[{rcept_no}] 목차를 가져오지 못했습니다 — {exc}. 전문은 opm://filing/{rcept_no} 에서."
        return render_toc(rcept_no, toc)

    @mcp.resource(
        "opm://filing/{rcept_no}/section/{no}{?start}",
        name="DART 공시 절",
        description=(
            "공시의 절 하나를 읽는다 — 정제 텍스트와 마크다운 표(단위·기준일·각주 포함). "
            "no 는 opm://filing/{rcept_no}/toc 의 절 번호. 상위 항목 번호면 하위 절 목록을 준다. "
            "절당 40,000자 상한이며 넘치면 응답 끝의 ?start= 주소로 이어 읽는다."
        ),
        mime_type="text/markdown",
        annotations=Annotations(audience=["user", "assistant"], priority=0.8),
    )
    async def filing_section(rcept_no: str, no: str, start: str = "") -> str:
        if not re.fullmatch(r"\d{14}", rcept_no or ""):
            return "잘못된 접수번호입니다 — 14자리 숫자여야 합니다."
        if not re.fullmatch(r"\d{1,4}", no or ""):
            return f"절 번호는 목차 {toc_uri(rcept_no)} 의 숫자 번호여야 합니다."
        from open_proxy_mcp.dart.client import DartClientError, get_dart_client
        from open_proxy_mcp.services.filing_sections import get_section, render_section

        try:
            offset = int(start) if str(start).strip() else 0
        except ValueError:
            offset = 0
        try:
            sec = await get_section(get_dart_client(), rcept_no, no)
        except DartClientError as exc:
            return f"[{rcept_no}] 절 {no} 을 가져오지 못했습니다 — {exc}. 목차 {toc_uri(rcept_no)} 에서 다른 절을 고르거나 잠시 뒤 다시 읽으세요."
        except Exception as exc:  # noqa: BLE001 — 전송 오류(재시도 후)·파싱 오류: 어디로 갈지 알려 준다
            return (f"[{rcept_no}] 절 {no} 본문을 받지 못했습니다 ({type(exc).__name__}). "
                    f"잠시 뒤 다시 읽거나 목차 {toc_uri(rcept_no)} 에서 이웃 절을 고르세요.")
        return render_section(rcept_no, sec, offset)

    #: 의결권 판단 기준 문서. **판정 사유에 「OPM Guideline §2.4 이사 선임 — against ①…」로
    #: 인용되는 그 문서다.**
    #: 260813: 인용문은 `_POLICY_CITATIONS`(proxy_advise.py) 에 손으로 적어둔 요약이었고 문서와
    #:   연결돼 있지 않았다. 최소한 **문서 자체를 열람 가능하게** 해서 원문으로 답할 수 있게 했다.
    #: 260903: 인용문이 이 문서의 **절 번호·항목 번호**를 가리키고, 문서↔라벨은
    #:   `tests/test_policy_citations_match_document.py` 가 자동 대조한다. 라벨↔판정 함수는 여전히 수기.
    #: 파일이 없으면 그 사실을 그대로 말한다(조용히 빈 값을 주지 않는다 — 무표시 열화 금지).
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
        from importlib.resources import files

        # 260814: `wiki/decisions/` 를 경로로 찾아갔는데 배포 이미지에 wiki 가 안 들어가
        #   fly 에서 「문서를 찾지 못했습니다」가 나왔다. 패키지 데이터로 옮겨 코드와 함께
        #   배포되게 하고, 작업 디렉터리에 의존하지 않는 importlib.resources 로 읽는다.
        path = files("open_proxy_mcp.data.guideline") / "open-proxy-guideline.md"
        if not path.is_file():
            return (
                "가이드라인 문서를 찾지 못했습니다.\n\n"
                f"기대 경로: {path}\n"
                "패키지 데이터가 빠진 빌드일 수 있습니다.\n"
                "판정 사유에 실리는 요약 인용은 응답의 「정책 인용」 줄에서 확인할 수 있습니다."
            )
        return path.read_text(encoding="utf-8")
