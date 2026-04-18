---
type: analysis
title: release_v2 public tool 검증 매트릭스
tags: [release-v2, tool, validation, matrix]
date: 2026-04-18
related: [tool-추가-검증-정책, tool-추가-검증-템플릿, shareholder_meeting-tool-검증-예시]
---

# release_v2 public tool 검증 매트릭스

## 목적

release_v2 공개 표면 전체를 한 장에서 보는 요약표다.  
애널리스트 기준으로는 `어떤 질문을 바로 다룰 수 있는지`, 운영 기준으로는 `지금 공개 가능한지`를 보는 문서다.

## Data Tools

| tool | 핵심 질문 | 1차 소스 | 화이트리스트 | 현재 판정 | 비고 |
|---|---|---|---|---|---|
| `company` | 이 회사를 정확히 특정할 수 있는가 | DART corpCode/company | 없음 | `go` | ISIN/최근공시 인덱스 보강 필요 |
| `shareholder_meeting` | 이번 주총 안건과 결과는 무엇인가 | DART XML + 결과는 KIND | 결과 공시만 | `go (annual)` / `conditional (extraordinary)` | notice는 DART-only |
| `ownership_structure` | 지분 구조와 지배력 변화는 무엇인가 | DART API + 일부 XML 목적 파싱 | 없음 | `go` | control map 정규화 규칙 필요 |
| `dividend` | 배당 수준, 결정 공시, 추이는 무엇인가 | DART alotMatter + DART XML | 배당결정만 선택적 | `go` | 시가배당률 부재 시 가격 fallback 규칙 명시 필요 |
| `proxy_contest` | 분쟁, 위임장, 소송, 지분 시그널은 무엇인가 | DART D/B/I + 일부 KIND | 일부 소송/주총결과만 | `conditional` | scope별 공개 범위 분리 권장 |
| `value_up` | 밸류업 계획과 이행 신호는 무엇인가 | DART I + XML | 가능 | `go` | KIND는 제목 검증 후만 허용 |
| `evidence` | 이 판단의 원문 근거는 어디인가 | upstream evidence pointers | upstream 따라감 | `conditional` | evidence schema 고정 필요 |

## Action Tools

| tool | 핵심 결과물 | upstream | 현재 판정 | 비고 |
|---|---|---|---|---|
| `prepare_vote_brief` | 투표 메모 | `shareholder_meeting`, `ownership_structure`, `evidence` | `phase-2` | 시나리오 5개 검증 필요 |
| `prepare_engagement_case` | engagement 메모 | `ownership_structure`, `proxy_contest`, `value_up`, `evidence` | `phase-2` | 결론 문구 통제 필요 |
| `build_campaign_brief` | 캠페인 브리프 | `proxy_contest`, `ownership_structure`, `shareholder_meeting`, `evidence` | `phase-2` | vote math 검증 필요 |

## 해석

- 지금 당장 우선 공개할 축은 `data tool`이다.
- 그중에서도 `company`, `shareholder_meeting`, `ownership_structure`, `dividend`, `value_up`은 상대적으로 빠르게 안정화 가능하다.
- `proxy_contest`는 사용자 가치는 높지만 공시군이 넓고 분쟁/위임장/소송/지분변동이 섞여 있어 scope 분리가 필요하다.
- `evidence`는 가장 중요하지만, 실제 공개 전에 `evidence_id`, `item_id`, `source_type`, `snippet` 스키마를 먼저 고정해야 한다.

## 관련 예시 문서

- [[company-tool-검증-예시]]
- [[shareholder_meeting-tool-검증-예시]]
- [[ownership_structure-tool-검증-예시]]
- [[dividend-tool-검증-예시]]
- [[proxy_contest-tool-검증-예시]]
- [[value_up-tool-검증-예시]]
- [[evidence-tool-검증-예시]]
- [[release_v2-action-tool-검증-초안]]
