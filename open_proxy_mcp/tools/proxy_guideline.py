"""proxy_guideline — 의결권 판단 기준 문서 조회.

왜 tool 로도 두나 (260813 실측):
  같은 문서를 `opm://guideline` resource 로도 걸어 두었지만, **Claude.ai 커넥터는
  resource 를 모델에게 노출하지 않는다** — 사용자 클라이언트가 "리소스·프롬프트가
  하나도 안 보인다"고 답했다. resource 만 두면 아무도 못 읽는다.

무엇에 쓰나:
  `proxy_advise_before_meeting` 의 판정 사유에 「OPM Guideline §2.4 이사 선임 — against
  ①「사외이사 장기연임 5년+」… ▸ 엔진: …」 같은 인용이 붙는다. §2.4 가 이 문서의 절 번호이고
  ①이 그 절 against 목록의 항목 번호다 — `section="2.4"` 로 열면 그 항목이 그 자리에 있다
  (tests/test_policy_citations_match_document.py 가 라벨↔문서를 자동 대조한다).
  「왜 이 안건이 찬성이냐」에 답할 때 이 문서를 읽고 인용하면 된다.

정직 표시:
  이 문서(정책)와 엔진(실제 판정)은 **의도적으로 다르다** — 문서 §0-A「정책 ↔ 엔진
  정합표」가 그 간극의 공식 지도다. 정책이 반대를 선언해도 법령 강행규정이 아니면
  엔진은 자동 반대 대신 검토로 두고 판단 재료를 애널리스트에게 넘긴다.
  이 문서만 읽고 「시스템이 자동 반대한다」고 읽으면 안 된다.

DART 호출 0.
"""

from __future__ import annotations

from importlib.resources import files

#: 260814: `wiki/decisions/` 를 경로로 찾아갔는데 배포 이미지에 wiki 가 안 들어가
#:   fly 에서 「문서를 찾지 못했습니다」가 나왔다. **패키지 데이터로 옮겨** 코드와 함께
#:   배포되게 하고, 작업 디렉터리·실행 방식에 의존하지 않는 importlib.resources 로 읽는다
#:   (운용사 정책 `data/asset_managers/` 가 이미 그 방식이다).

#: Claude.ai/Desktop 의 tool 결과 상한이 약 150,000자다. 문서는 약 26KB 라 여유가 있지만,
#: 문서가 커지면 여기서 잘린다 — 그때는 `section` 으로 좁혀 부르면 된다.
_MAX_CHARS = 120_000


def _sections(text: str) -> list[tuple[str, str]]:
    """`## `·`### ` 헤딩 기준으로 (제목, 본문) 목록. 앞머리는 첫 항목.

    **`###` 까지 보는 이유**: 안건 유형별 정책이 `### 2.1 재무제표 승인` 처럼 3단에 있다.
    `##` 만 보면 「2. 12 카테고리 정책」 하나로 뭉쳐 `section="재무제표"` 가 안 걸린다
    (260813 실측). 사용자가 찾는 단위는 안건 유형이다.
    """
    out: list[tuple[str, str]] = []
    cur_title = "(문서 머리)"
    cur: list[str] = []
    for line in text.splitlines():
        if line.startswith("## ") or line.startswith("### "):
            out.append((cur_title, "\n".join(cur).strip()))
            cur_title = line.lstrip("#").strip()
            cur = [line]
        else:
            cur.append(line)
    out.append((cur_title, "\n".join(cur).strip()))
    return [(t, b) for t, b in out if b]


def register_tools(mcp) -> None:
    @mcp.tool()
    async def proxy_guideline(section: str = "") -> str:
        """OPM 의결권 행사 정책(Open Proxy Guideline) 원문을 읽는다.

        proxy_advise_before_meeting 의 판정 사유에 인용되는 기준 문서다.
        「왜 이 안건이 찬성/반대냐」의 근거를 원문으로 확인할 때 쓴다.

        section: 비우면 목차 + 전문. 값을 주면 제목에 그 말이 들어간 절만
                 (예: "2.4" — 판정의 「정책 인용」이 가리키는 절 번호 / "재무제표", "이사 선임",
                 "정관", "0-A").
        주의: 정책과 엔진은 의도적으로 다르다 — 문서 §0-A 정합표를 함께 읽을 것.
        """
        path = files("open_proxy_mcp.data.guideline") / "open-proxy-guideline.md"
        if not path.is_file():
            return (
                "# proxy_guideline\n\n"
                "가이드라인 문서를 찾지 못했습니다.\n\n"
                f"- 기대 경로: `{path}`\n"
                "- 패키지 데이터가 빠진 빌드일 수 있습니다.\n"
                "- 판정별 요약 인용은 `proxy_advise_before_meeting` 응답의 "
                "「정책 인용」 줄에서 확인할 수 있습니다."
            )
        text = path.read_text(encoding="utf-8")
        secs = _sections(text)

        if section:
            key = section.replace(" ", "")
            hit = [b for t, b in secs if key in t.replace(" ", "")]
            if not hit:
                titles = "\n".join(f"- {t}" for t, _ in secs)
                return (f"# proxy_guideline\n\n'{section}' 에 해당하는 절이 없습니다.\n\n"
                        f"## 사용 가능한 절\n{titles}")
            body = "\n\n".join(hit)
        else:
            body = text

        if len(body) > _MAX_CHARS:
            body = body[:_MAX_CHARS] + "\n\n…(이후 생략 — `section` 으로 좁혀 조회하세요)"
        return body
