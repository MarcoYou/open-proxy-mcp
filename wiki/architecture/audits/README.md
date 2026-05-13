---
type: readme
title: Architecture Audits 정리본
updated: 2026-05-10
---

# Architecture Audits

이 폴더의 문제는 “audit가 없는 것”이 아니라 “읽는 순서와 기준이 없는 것”이다.  
앞으로는 이 문서를 기준으로:

- 먼저 읽을 **현재 기준 audit**
- 과거 과정을 남겨두는 **대체됨 / 이력**
- 본문보다 아래 레벨인 **원시 결과물**

을 분리한다.

## 먼저 읽기

지금 repo 상태를 빠르게 파악하려면 아래 문서만 먼저 읽으면 된다.

### 1. 현재 data tools 상태
- [[260510_parsing_audit_통합정리]]
- [[260510_financial_metrics_audit_통합정리]]
- [[260505_0530_audit_treasury_execution_iter1-8]]
- [[260508_parser_audit]]
- [[260510_data_tools_perf_audit]]

### 2. 현재 action / advise 상태
- [[260510_proxy_advise_audit_통합정리]]
- [[../proxy_advise_word_report_design]]

### 3. 현재 repo / wiki 관리 상태
- [[260509_wiki_graph_audit]]

위 문서들이 사실상 “현재 기준 audit 묶음”이다.

## 현재 기준 Audit

### Data tools

| 영역 | 현재 기준 문서 | 비고 |
|---|---|---|
| parsing 전수 상태 | [[260510_parsing_audit_통합정리]] | 계열 문서 통합 안내 포함 |
| financial_metrics | [[260510_financial_metrics_audit_통합정리]] | 6기업 → 200기업 흐름 통합 |
| treasury_share execution | [[260505_0530_audit_treasury_execution_iter1-8]] | 자사주 결과보고서 기준 |
| parser 종합 점검 | [[260508_parser_audit]] | 파서 전수/트리거 audit |
| data tools 성능 | [[260510_data_tools_perf_audit]] | 현재 성능 기준 문서 |

### Action tools

| 영역 | 현재 기준 문서 | 비고 |
|---|---|---|
| advise / proxy_advise 전체 흐름 | [[260510_proxy_advise_audit_통합정리]] | sanity → 실패 → 수렴 → framework 통합 |
| proxy_advise Word 문서 양식 | [[../proxy_advise_word_report_design]] | 샘플 기반 Word export 설계 |
| recap pattern | [[260503_2304_audit_recap_pattern]] | recap 전용 |
| proxy_contest baseline | [[260503_2330_audit_proxy_contest_baseline]] | “fix 불필요” 결정 기록 |
| ownership baseline | [[260503_2345_audit_ownership_baseline]] | “fix 불필요” 결정 기록 |

### 메타 / 유지보수

| 영역 | 현재 기준 문서 | 비고 |
|---|---|---|
| wiki 그래프 / 명명 정리 | [[260509_wiki_graph_audit]] | wiki 운영 기준 |
| 산술 정확성 | [[260429_0942_audit_arithmetic-21지표]] | 별도 정확성 audit |

## 대체됨 / 이력

아래 문서들은 삭제 대상은 아니지만, “지금 기준 문서”로 읽으면 안 된다.

### Parsing 계보
- [[260421_2308_audit_parsing-10tool-20기업]]
  - 초기 상태 점검
- [[260422_0005_audit_parsing-14scope-15기업]]
  - 위 문서의 확장판
- [[260429_0216_audit_parsing-200기업-v1]]
  - `partial` 안에 `no_filing`이 섞여 있던 구버전
- 현재 기준:
  - [[260429_0912_audit_parsing-200기업-v2-no_filing]]

### financial_metrics 계보
- `260501_1820_audit_financial_metrics-6기업.md`
  - 초기 소표본 sanity, 통합 후 원문 삭제
- 현재 기준:
  - [[260510_financial_metrics_audit_통합정리]]

### advise 계보
- `260503_0130_audit_advise-200-virtual.md`
  - 부분 진행 상태 보고, 통합 후 원문 삭제
- `260503_0500_audit_phase3_final.md`
  - 실패/미달 상태 기록, 통합 후 원문 삭제
- 현재 기준:
  - [[260510_proxy_advise_audit_통합정리]]

### proxy_advise 변화 과정
- [[260504_0028_audit_proxy_advise_rename_regression]]
  - rename 회귀 점검
- `260504_0705_audit_proxy_advise_ralph_final.md`
  - 중간 수렴 단계, 통합 후 원문 삭제
- [[260504_0724_audit_parse_personnel_iter1-7]]
  - parse_personnel iteration archive 성격
- 현재 기준:
  - [[260510_proxy_advise_audit_통합정리]]

### personnel 계보
- [[260411_2023_audit_personnel-벤치마크-v1]]
  - 매우 초기 benchmark
- [[260429_2053_audit_personnel-878명]]
  - 더 유의미한 대형 표본 audit

## 주제별 전문 Audit

현재 기준 묶음에는 넣지 않았지만, 특정 질문에 바로 연결되는 문서들이다.

- [[260429_2053_audit_personnel-878명]] — 후보자/경력 파서 정확도
- [[260502_2300_audit_advise-recap-vote]] — action tool 재편 sanity
- [[260503_2304_audit_recap_pattern]] — recap multi-upstream-pattern
- [[260503_2330_audit_proxy_contest_baseline]] — proxy_contest baseline
- [[260503_2345_audit_ownership_baseline]] — ownership baseline
- [[260429_0942_audit_arithmetic-21지표]] — 산술 정확성

## 원시 결과물 규칙

`audits/*.md`는 사람이 읽는 결론 문서다.  
`audits/data/**`는 원시 결과물이다.

원시 결과물부터 읽지 말고:
1. 이 README에서 현재 기준 audit 선택
2. 해당 `.md` 문서의 결론 / 최종 판단 확인
3. 필요할 때만 `data/...json|csv` 근거 파일로 내려간다

원시 결과물 인덱스는 [[data/README]].

## 신규 audit 추가 규칙

1. 새 audit가 기존 문서를 대체하면, 이 README의 `현재 기준 Audit` 또는 `대체됨 / 이력`을 같이 갱신한다.
2. 새 audit가 특정 실험/iter raw를 설명하는 수준이면 top-level `.md` 대신 `data/{topic}/` 원시 결과물과 기존 기준 문서 update를 우선 검토한다.
3. “현재 기준 문서”가 아닌 audit는 반드시 이 README에서 계보 또는 주제별 전문 audit로 위치를 명시한다.
4. 문서가 많아질수록 새 파일을 늘리기보다 기존 기준 문서 update가 가능한지 먼저 본다.

## 관련

- [[data/README]] — Audit 원시 결과물 인덱스
- [[../../../ralph/README]] — Ralph 인덱스
