---
type: decision
title: LLM Fallback 설계
tags: [llm, fallback, architecture]
sources: [DEVLOG.md]
related: [3-tier-fallback, 파서-판정-등급]
---

# LLM Fallback 설계

## 개요

정규식 파서가 실패한 경우, LLM을 활용하여 안건을 구조화하는 하이브리드 fallback 전략. [[3-tier-fallback]]의 보조 메커니즘으로, [[파서-판정-등급]]에서 FAIL 판정된 결과를 보정.

## 흐름

```
parse_agenda_items(text) -- 정규식
  |
  v
validate_agenda_result()
  |
  SUCCESS -> format_agenda_tree() -> 응답
  |
  FAIL -> extract_notice_section() + extract_agenda_zone()
            |
            zone 없음 -> HARD FAIL
            zone 있음 -> LLM fallback (gpt-5.4-mini)
                           |
                           validate again
                           |
                           SUCCESS -> 응답
                           FAIL -> HARD FAIL
```

## 트리거 조건 (validate_agenda_result)

- 빈 리스트 (0건)
- 같은 number 중복 (정정공고 잔류)
- 제목 200자 초과 (zone 텍스트 딸려옴)

## 토큰 효율

- 정규식 성공 시: 0 토큰
- LLM fallback 시: zone 크기만큼 (500-1500자)
- 전체 문서를 보내지 않고 zone만 추출하여 비용 최소화

## free vs paid

- **free ([[OpenProxy-MCP]])**: use_llm=False 기본, 유저 AI 토큰으로 보정
- **paid ([[OpenProxy-AI]])**: 자동 체이닝, provider API 토큰 사용. [[free-paid-분리]] 설계에 따른 차이
