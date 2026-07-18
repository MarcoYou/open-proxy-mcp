---
description: OPM tool 결과를 유니버스 배치로 검증 (파싱 정합성 2인 + 전문가 3인 + 개선점 도출)
---

`opm-investigate` 워크플로우를 실행하라.

- `Workflow` 도구를 `name: "opm-investigate"`, `args: "$ARGUMENTS"` 로 호출한다.
- `$ARGUMENTS` 가 비어 있으면, 먼저 사용자에게 **검증할 질문**(유니버스·대상 tool·제약조건, 예: "삼성전자 등 5사 2026 정기주총 안건 점검 — 정기만")을 묻고 그 답을 `args` 로 넘긴다.
- 실행 전, 선택된 tool의 기업당 콜 수 × 유니버스 크기가 DART 분당 한도(910)를 넘지 않는지 `wiki/tools/tool_call_budget.md` 로 가볍게 확인한다 (워크플로우 Scout 단계가 자체적으로도 점검함).
- 끝나면 정합성 플래그·전문가 해설·개선점 노트를 요약해 보고한다. 개선점이 코드 수정으로 이어질 만하면 `/opm-enhance` 로 넘길 수 있음을 안내한다.
