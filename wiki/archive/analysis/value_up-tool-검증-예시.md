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

## 샘플 확인 (2026-04-19 실행, scope=summary)

| company | status | latest.category | latest_plan | highlights | text_length | note |
|---|---|---|---|---|---|---|
| KB금융 | exact | meta_amendment (고배당표시) | plan (2025-04-24 원본) | 0 | 1584 | 원본 본문 텍스트 짧음 (PDF 첨부 중심). viewer_url로 직접 확인 |
| 하나금융지주 | exact | meta_amendment (고배당표시) | progress (2026-03-25 이행현황) | 5 | 2259 | meta → progress fallback 작동, commitment 5문장 추출 |
| LG에너지솔루션 | exact | progress | (자신이 progress) | 0 | 1926 | 이행현황 본문 있으나 `_COMMITMENT_KEYWORDS` 매칭 문장 없음 (키워드 튜닝 여지) |
| 메리츠금융지주 | exact | progress | (자신이 progress) | 2 | 2166 | 이행현황에서 commitment 2건 추출 |

### 카테고리 분류

| 카테고리 | 감지 조건 | 설명 |
|---|---|---|
| `meta_amendment` | report_name에 "고배당기업" / "고배당법인" 포함 | 조세특례제한법 고배당기업 표시 위한 형식 재공시. 실제 계획 본문은 원본에 있음. |
| `progress` | report_name에 "이행현황" 포함 | 전년도 이행현황 공시 (commitment 표현이 있을 수 있음) |
| `plan` | 그 외 | 원본 계획 또는 개정 계획 |

최신 공시가 `meta_amendment`이면 `latest_plan`으로 실제 계획 본문 공시를 별도 노출.

### dividend와의 역할 분리

- `dividend`: 실지급된/확정된 배당 **사실** (DPS, 총액, 배당성향)
- `value_up`: 주주환원 **정책·약속** (ROE 목표, 자사주 소각 계획, 중장기 배당성향 가이드 등)
- 두 tool을 교차 참조해 "약속한 수준"과 "실제 지급 수준"의 갭 파악

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
