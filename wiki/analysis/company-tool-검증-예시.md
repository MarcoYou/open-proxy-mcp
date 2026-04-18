---
type: analysis
title: company tool 검증 예시
tags: [release-v2, tool, validation, company]
date: 2026-04-18
related: [tool-추가-검증-템플릿, tool-추가-검증-정책, DART-OpenAPI, 네이버-금융]
---

# company tool 검증 예시

## 목적

`company`는 모든 public tool의 공통 입구다.  
애널리스트 입장에서는 “회사 이름으로 시작해서 내부 식별자를 확정하는 도구”다.

## 제안 요약

- tool type: `data`
- 핵심 질문:
  - 이 회사를 정확히 특정했는가
  - ticker / corp_code / 시장 / 업종 / 영문명은 무엇인가
- 기대 결과물:
  - `company_id`
  - `ticker`
  - `corp_code`
  - `ISIN`
  - `market`
  - `sector`
  - `recent_filings`

## 소스 정책

| field | primary source | secondary source | note |
|---|---|---|---|
| corp_code / stock_code | DART `corpCode.xml` | 없음 | 회사 식별의 시작점 |
| company profile | DART `company.json` | 없음 | 영문명, 법인번호, 업종코드 |
| sector name | NAVER profile | 없음 | 보조 분류 |
| recent filings | DART `list.json` | 없음 | release_v2 추가 과제 |

## 샘플 확인

| query | corp_code | stock_code | corp_name | 판정 | note |
|---|---|---|---|---|---|
| 삼성전자 | `00126380` | `005930` | 삼성전자 | exact | 현재 corp_identifier 경로 존재 |
| 케이티앤지 / KT&G | `00244455` | `033780` | 케이티앤지 | exact | 약칭 추가 검증 필요 |
| 고려아연 | `00102858` | `010130` | 고려아연 | exact | 이후 AGM/ownership 샘플과 연결 가능 |

## requires_review 조건

- 동명기업/유사명으로 복수 corp_code가 나오는 경우
- stock_code 없는 비상장/비표준 명칭 입력
- 영문명/약칭 매칭이 불안정한 경우

## release_v2 판정

- `go`
- 이유:
  - 현재 `corp_identifier`가 이미 DART + NAVER 체인을 갖고 있다
  - release_v2에서는 이름만 `company`로 바꾸고, `recent_filings`와 `ISIN` 보강을 추가하면 된다

## 실무 해석

`company`는 분석 결과물이 아니라 `식별 카드`다.  
이 도구가 흔들리면 이후 모든 tool이 흔들리므로, `ambiguous` 처리와 `recent_filings` 인덱스가 핵심이다.
