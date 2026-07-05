---
description: OPM tool 코드를 4축(속도·API·정확성·regression) 검증하며 최대 3회 정제 + 문서 동기화
---

`opm-enhance` 워크플로우를 실행하라.

- `Workflow` 도구를 `name: "opm-enhance"`, `args: "$ARGUMENTS"` 로 호출한다.
- `$ARGUMENTS` 가 비어 있으면, 먼저 사용자에게 **개선 항목**(어떤 tool의 무엇을 어떻게 개선할지, 가능하면 `opm-investigate` 가 낸 개선점 노트)을 묻고 그 답을 `args` 로 넘긴다.
- 이 워크플로우는 worktree에서 실제 코드를 수정하고 4축 게이트를 통과해야 채택하며, 채택 시 Docs 단계가 `tool 문서·tool_call_budget` 갱신을 제안한다.
- **자동 커밋·푸시는 하지 않는다.** 끝나면 채택 후보 diff·4축 결과·문서 갱신안을 보고하고, 커밋 여부는 사용자에게 묻는다.
