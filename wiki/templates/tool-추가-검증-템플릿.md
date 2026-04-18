---
type: template
title: 신규 tool 제안 및 검증 템플릿
tags: [template, release-v2, tool, validation]
date: 2026-04-18
related: [tool-추가-검증-정책, DART-KIND-매핑-화이트리스트-2026-04]
---

# 신규 tool 제안 및 검증 템플릿

## 사용법

새 tool을 추가할 때 아래 순서로 쓴다.

1. `A. Tool Proposal` 작성
2. tool이 `data`면 `B. Data Tool Validation` 작성
3. tool이 `action`이면 `C. Action Tool Validation` 작성
4. KIND나 외부 HTML을 붙이면 `D. Whitelist Extension Check` 작성
5. 마지막으로 `E. Release Gate` 체크

---

## A. Tool Proposal

```md
# [tool_name] 제안서

## 1. 기본 정보
- tool name:
- tool type: data / action
- owner:
- target user:
- analyst use case:

## 2. 질문 정의
- 이 tool이 답하려는 핵심 질문:
- 사용자가 기대하는 최종 결과물:
- 기존 tool과의 차이:

## 3. 입력
- user-facing company input:
- optional filters:
- expected internal identifiers:

## 4. 공시 범위
- disclosure universe:
- target report names:
- pblntf_ty:
- time window rule:
- correction handling rule:

## 5. 소스 정책
- primary source:
- secondary source:
- uses KIND?: yes / no
- uses Naver?: yes / no
- requires whitelist check?: yes / no

## 6. 출력
- key output fields:
- evidence fields:
- status fields:
- requires_review triggers:
```

---

## B. Data Tool Validation

```md
## Data Tool Validation

### 1. 범위 확정
- 질문 범위가 명확한가:
- 읽는 공시 유형이 명확한가:
- 같은 회사에 동일 유형 공시가 여러 건일 때 선택 규칙이 있는가:

### 2. OpenDART 조사
- 공식 API 존재 여부:
- list.json 사용 여부:
- document.xml 사용 여부:
- 구조화 API가 XML보다 우선하는 필드:
- 공식 문서 링크:

### 3. 공시 매핑표
| field | disclosure type | pblntf_ty | primary source | secondary source | external mapping | note |
|---|---|---|---|---|---|---|
| | | | | | | |

### 4. 회사 식별
- 한글명 매칭:
- 영문명 매칭:
- 약칭 매칭:
- ambiguous 처리 규칙:
- 내부 식별자 체인:

### 5. 샘플 검증
| company | report_name | date | rcept_no | primary result | secondary result | final status | note |
|---|---|---|---|---|---|---|---|
| | | | | | | | |
| | | | | | | | |
| | | | | | | | |

### 6. evidence 설계
- field-level evidence 지원 여부:
- evidence 예시:
- source_type enum:
- confidence rule:

### 7. requires_review 조건
- source missing:
- source conflict:
- ambiguous company:
- malformed xml/html:
- other:
```

---

## C. Action Tool Validation

```md
## Action Tool Validation

### 1. 참조 data tool
- upstream data tools:
- upstream evidence sources:
- upstream status dependency:

### 2. 결과물 정의
- summary format:
- key findings format:
- next actions / watch items:
- evidence_refs exposure:

### 3. 판단 규칙
- 어떤 경우 결론을 확정하는가:
- 어떤 경우 partial/conflict/requires_review로 올리는가:
- 금지되는 문장/단정:

### 4. 시나리오 검증
| scenario | input company | input event | expected output | actual status | note |
|---|---|---|---|---|---|
| AGM routine vote | | | | | |
| EGM contest | | | | | |
| Dividend change | | | | | |
| 5% purpose change | | | | | |
| Litigation update | | | | | |

### 5. evidence chain
- key finding 1 -> source:
- key finding 2 -> source:
- key finding 3 -> source:
```

---

## D. Whitelist Extension Check

KIND 또는 다른 외부 HTML을 새로 붙일 때만 작성한다.

```md
## Whitelist Extension Check

### 1. 대상 공시
- disclosure type:
- current whitelist status: existing / new candidate
- mapping rule hypothesis:

### 2. 1차 샘플 3개
| company | dart report | dart rcept_no | mapped external id | title match | company match | date match | false match | result |
|---|---|---|---|---|---|---|---|---|
| | | | | | | | | |
| | | | | | | | | |
| | | | | | | | | |

### 3. 추가 샘플 10개
| company | dart rcept_no | mapped external id | title match | false match | result |
|---|---|---|---|---|---|
| | | | | | |

### 4. 판정
- whitelist approve?: yes / no
- reason:
- fallback if rejected:
```

---

## E. Release Gate

```md
## Release Gate

### 공통
- [ ] tool proposal 작성 완료
- [ ] 회사 식별 규칙 확인 완료
- [ ] 공시 범위 및 pblntf_ty 정의 완료
- [ ] status model 정의 완료
- [ ] evidence 구조 정의 완료

### data tool 전용
- [ ] 공시 매핑표 작성 완료
- [ ] OpenDART 공식 문서 조사 완료
- [ ] 샘플 3개 이상 검증 완료
- [ ] requires_review 조건 정의 완료

### action tool 전용
- [ ] upstream data tool 검증 완료
- [ ] 시나리오 5개 검증 완료
- [ ] evidence chain 확인 완료
- [ ] 과도한 단정 문구 점검 완료

### whitelist 전용
- [ ] 기존 화이트리스트 확인 완료
- [ ] 신규 후보면 3개 + 10개 샘플 검증 완료
- [ ] false match 0건 확인

### 최종
- [ ] wiki 문서 저장 완료
- [ ] index/log 업데이트 완료
- [ ] public tool 승격 승인
```

---

## 빠른 판단 기준

- `data tool`이면 먼저 `공시 매핑표`가 나와야 한다
- `action tool`이면 먼저 `근거 체인`이 나와야 한다
- `KIND`를 붙이면 먼저 `화이트리스트`부터 본다
- `false match`가 한 번이라도 나오면 public 승격을 멈춘다
