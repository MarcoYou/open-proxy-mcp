---
type: analysis
title: release_v2 action tool 검증 초안
tags: [release-v2, tool, validation, action]
date: 2026-04-18
related: [tool-추가-검증-템플릿, tool-추가-검증-정책, release_v2-public-tool-검증-매트릭스]
---

# release_v2 action tool 검증 초안

## 목적

release_v2에서는 data tool을 먼저 공개하고, action tool은 `phase-2`로 두는 것이 안전하다.  
다만 지금부터 어떤 검증이 필요한지는 정리해둔다.

## 대상

| tool | 결과물 | upstream |
|---|---|---|
| `prepare_vote_brief` | 투표 메모 | `shareholder_meeting`, `ownership_structure`, `evidence` |
| `prepare_engagement_case` | engagement 메모 | `ownership_structure`, `proxy_contest`, `value_up`, `evidence` |
| `build_campaign_brief` | 캠페인 브리프 | `proxy_contest`, `ownership_structure`, `shareholder_meeting`, `evidence` |

## 공통 검증 원칙

- 새 사실을 만들지 않는다
- upstream evidence를 넘는 단정 문구를 쓰지 않는다
- `partial / conflict / requires_review`를 결과에도 그대로 올린다
- 핵심 finding마다 evidence ref가 있어야 한다

## 필요한 시나리오

1. 정기주총 routine vote
2. 임시주총 분쟁
3. 배당정책 변화 후 engagement
4. 5% 보유 목적 변화 반영
5. 소송/분쟁 업데이트 반영

## 현재 판정

- `prepare_vote_brief`: `phase-2`
- `prepare_engagement_case`: `phase-2`
- `build_campaign_brief`: `phase-2`

이유:

- data layer가 먼저 고정돼야 한다
- `evidence` schema가 먼저 안정화돼야 한다
- 실제 사용자 문구는 scenario test 없이는 쉽게 과장될 수 있다

## 실무 해석

action tool의 가치는 크다.  
하지만 지금 우선순위는 `빠르고 정확한 데이터 접근`이므로, release_v2에서는 data tool을 먼저 열고 action tool은 그 위에 얹는 것이 맞다.
