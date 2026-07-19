---
type: decision
title: capability 질문 응답 메커니즘 — tool vs resource vs 무대응 (4인 전문가 패널)
generated: 2026-07-21
related: [getting_started]
---

# capability 질문 응답 메커니즘 — tool vs resource vs 무대응

## 배경
"OPM으로 뭐 할 수 있어?" 같은 포괄적 질문에 서버가 잘 답하게 하려면 무엇을 추가해야 하는가.
FastMCP `instructions` 필드(서버 연결 시 1회 전달, 짧은 오리엔테이션용)를 이미 추가했지만, 상세
설명까지 담기엔 설계상 너무 짧다. 4명의 독립 전문가 에이전트(MCP 프로토콜 전문가·LLM tool-use
실무 전문가·멀티클라이언트(Claude/ChatGPT/Perplexity) 실무자·DX/유지보수 엔지니어)를 병렬로
투입해 검토했다.

## 검토한 옵션
- A. 신규 tool 신설(`getting_started` 등) — capability 질문 시 호출되도록 desc/when에 명시, 큐레이션된 콘텐츠 반환
- B. MCP resource로 노출
- C. 무대응 — 기존 instructions + per-tool docstring으로 충분하다고 보고 종료
- D. MCP prompts 등 다른 메커니즘

## 패널 결론 (4인 만장일치 — A, resource 배제)

**MCP 프로토콜 전문가**: MCP 스펙은 tools=model-controlled(모델이 자율 판단해 호출), resources=
application-controlled(호스트 앱이 컨텍스트 주입 여부 결정, 모델 자율 discovery 스펙 보장 없음),
prompts=user-controlled(유저가 먼저 선택)로 통제 주체가 다르다. "느슨한 질문에 모델이 알아서
반응"은 정의상 model-controlled 영역 — A(tool)가 스펙 취지에 정확히 부합. resource만으론 스펙이
보장하는 바가 없어(결국 tool 래퍼가 또 필요) B 단독 채택은 이 문제를 안 푼다.

**멀티클라이언트 실무자**: 실무적으로 더 결정적인 이유 — resource는 Claude/ChatGPT/Perplexity
3개 클라이언트 중 실질적으로 tool 호출만큼 신뢰성 있게 지원되지 않는다(브라우징 UI 자체가 없거나
사용자 수동 첨부 방식에 가까움). "모델이 알아서 반응"하려면 모델 자율 호출이 필요한데 이건 tool의
기본 동작이지 resource가 아니다. **B(resource) 채택 시 최소 1~2개 클라이언트에서 사실상 무용지물**.

**LLM tool-use 실무 전문가**: 이건 "정보 부재"가 아니라 "정보 curation" 문제라는 프레이밍이
맞다(25개 tool desc는 이미 모델 컨텍스트에 있음). tool 신설의 진짜 가치는 새 사실 추가가 아니라
①답변 일관성 보장 ②유지보수 지점 단일화. 단, self-referential/capability형 질문은 모델이
"이미 아는 얘기"로 판단해 tool 호출을 생략하는 **과소호출** 경향이 있어 100% 호출률은 기대하기
어려움 — instructions 필드에 중복 힌트를 넣는 게 비용 대비 효과적.

**DX/유지보수 엔지니어 — 결정적 반면교사 발견**: 레포에 이미 A안의 선례가 있었다. v1 toolset의
`open_proxy_mcp/tools/guide.py`(`tool_guide`)가 정확히 "하드코딩 markdown 가이드" 패턴인데, v2
재설계 이후 완전히 방치돼 현재 등록된 tool 이름(`asset_holdings`, `financial_metrics` 등)과 단
하나도 안 겹치는 채로 남아있었다(레포 확인: `fly.toml`의 `OPEN_PROXY_TOOLSET=v2`가 v1 패키지
자체를 프로덕션에서 아예 안 부르므로, 사실상 등록조차 안 됨). **하드코딩 콘텐츠는 반드시 썩는다는
실측 증거** — 따라서 A안을 채택하더라도 콘텐츠를 손으로 쓰면 안 되고, **런타임에 등록된 tool
목록에서 desc 필드를 그대로 추출해 조립**해야 구조적으로 드리프트가 불가능해진다.

## 최종 결정
**A 채택, 단 콘텐츠는 하드코딩 금지 — 런타임 introspection 기반으로 구현.** `getting_started` tool
신설, `mcp.list_tools()`로 매 호출 시 실제 등록 tool에서 `desc:` 필드를 추출해 카테고리별 markdown을
조립(`open_proxy_mcp/tools_v2/getting_started.py`). 카테고리 그루핑만 최소 `name→category` dict로
유지하되, 매핑 누락 tool은 "기타"로 떨어질 뿐 목록에서 사라지지 않도록 안전장치를 둠(구 tool_guide의
침묵 삭제 실패를 반복하지 않기 위함). resource·prompts는 이 문제의 대체재가 아니라고 결론.

## 트레이드오프
- 카테고리 매핑 자체는 여전히 손으로 유지해야 함(완전 자동화는 아님) — 다만 매핑 누락이 "삭제"가
  아니라 "기타 버킷"으로 완화돼 있어 최악의 실패 모드(완전 누락)는 구조적으로 막혀 있음.
- self-referential 질문에 대한 실제 tool 호출률은 3개 클라이언트에서 실측 검증되지 않음(패널의
  명시적 잔여 리스크) — 추후 실사용 로그로 확인 필요.

## 관련
- [[getting_started]] (구현 tool)
- `open_proxy_mcp/tools/guide.py` (v1 `tool_guide` — 반면교사, 현재 프로덕션 미등록 상태로 방치)
