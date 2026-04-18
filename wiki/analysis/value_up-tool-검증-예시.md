---
type: analysis
title: value_up tool 검증 예시
tags: [release-v2, tool, validation, value-up]
date: 2026-04-18
related: [tool-추가-검증-템플릿, tool-추가-검증-정책, DART-KIND-매핑-화이트리스트-2026-04]
---

# value_up tool 검증 예시

## 목적

`value_up`은 기업가치 제고 계획과 이행 공시를 보는 탭이다.

## 제안 요약

- tool type: `data`
- 핵심 질문:
  - 이 회사가 밸류업 계획을 냈는가
  - 재공시/이행현황이 있었는가
  - 배당/자사주/ROE/저PBR 해소와 연결되는 메시지가 있는가
- 권장 scope:
  - `summary`
  - `plan`
  - `commitments`
  - `timeline`
  - `evidence`

## 소스 정책

| field | disclosure/source | primary source | secondary source | note |
|---|---|---|---|---|
| value-up filing list | 거래소공시(I) | DART `list.json` | 없음 | 키워드 필터 |
| plan text | value-up document | DART `document.xml` | KIND whitelist 가능 | 본문 추출 |
| timeline | multiple value-up filings | DART `list.json` | KIND 선택적 | 재공시/이행현황 추적 |

## 샘플 확인

| company | report_name | rcept_no | primary result | secondary result | 판정 | note |
|---|---|---|---|---|---|---|
| KB금융 | 기업가치제고계획(자율공시) | `20260327802428` | DART XML 확보 | KIND `20260327002428` 가능 | exact | whitelist 대상 |
| 하나금융지주 | [기재정정]기업가치제고계획(자율공시) | `20260331801627` | DART XML 확보 | KIND `20260331001627` 가능 | exact | 재공시 |
| 메리츠금융지주 | 기업가치제고계획(자율공시) | `20260211800942` | DART XML 확보 | KIND `20260211000942` 가능 | exact | 이행현황형 |

## requires_review 조건

- 밸류업 키워드는 잡히지만 실제 본문이 비정형인 경우
- 재공시/기재정정이 많아 timeline 연결이 흔들리는 경우
- KIND 제목 검증이 실패하는 경우

## release_v2 판정

- `go`
- 이유:
  - 공시군이 비교적 단순하고
  - 현재 샘플상 DART/KIND 모두 안정적이었다

## 실무 해석

`value_up`은 단독 도구로도 쓸 수 있지만, 실제로는 `dividend`나 `ownership_structure`와 같이 봐야 의미가 커진다.  
그래서 release_v2에서는 먼저 데이터 탭으로 열고, 나중에 action tool에서 engagement 논리와 연결하는 흐름이 맞다.
