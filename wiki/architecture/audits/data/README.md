---
type: readme
title: Audit 원시 결과물 인덱스
updated: 2026-05-24
---

# Audit 원시 결과물

이 폴더는 **결론 문서가 아니라 근거 파일 보관소**다.

- `audits/*.md`
  - 사람이 먼저 읽는 결론 문서
- `audits/data/**`
  - `json`, `csv`, `iter` 로그 같은 원시 결과물

먼저 [[../README]]에서 현재 기준 audit를 찾고, 그 다음 필요한 경우에만 여기로 내려오면 된다.

## 주요 원시 결과물 묶음

### 2026-05-24 — core tool timing audit
- `260524_tool_timing_audit.json`
- 기준 문서: [[../260510_data_tools_perf_audit]]
- 포함: `LG화학`, `삼성전자`, `KT&G` × 8개 핵심 tool stage timing. 반복 병목은 `dividend.decision_details`, `treasury_share.fetch_decisions`.

### 2026-05-17 — parsing success-rate audit
- `260517_parsing_success_rate_audit/`
- 기준 문서: [[../260517_parsing_success_rate_audit]]
- 포함: `KOSPI 300 + KOSDAQ 150` baseline, 비중복 100개 recheck, `shareholder_meeting_notice` 정기/임시 공시 표본, `value_up`/`shareholder_meeting_results` 보강 검증

### 2026-05-10 — data tools 성능 audit
- `260510_perf_data_tools_audit/`
- 기준 문서: [[../260510_data_tools_perf_audit]]

### 2026-05-10 — 법령 / agenda / career / faithfulness 계열
- `260510_agenda_hierarchy/`
- `260510_career_concat/`
- `260510_director_faithfulness/`
- `260510_fix_verify/`
- `260510_law_layer_450/`
- `260510_law_layer_body/`
- `260510_subagenda_mapping/`

### 2026-05-08 — 법령 layer audit data
- `260508_law_layer/`
- `260508_parser_audit/`

### 2026-05-05 — 보수 / 퇴직금 / 성과 / 자사주 계열
- `260505_compensation_retirement/`
- `260505_compensation_retirement_extend/`
- `260505_compensation_retirement_precision/`
- `260505_inside_director_performance/`
- `260505_parser_omnibus/`
- `260505_treasury_execution/`

### 2026-05-04 — proxy_advise / parse_personnel iter archive
- `260504_proxy_advise_framework/`
- `260504_proxy_advise_failure_archive/`
- `260504_parse_personnel_failure_archive/`

## 데이터 보존 정책

- ralph 진행 중 audit data는 `data/{YYMMDD_topic}/iter*.{md|json|csv}` 형식
- 작업 완료 후 raw data는 보존한다
  - 회귀 검증
  - 향후 lesson 인용
  - 재측정 비교
- 디렉토리 명명은 `시점 + 토픽`을 기본으로 한다
  - 예: `260510_perf_data_tools_audit`

## 읽는 규칙

1. 먼저 [[../README]]에서 현재 기준 audit를 찾는다.
2. 그 문서의 결론 / 최종 판단을 먼저 본다.
3. 그 다음에만 여기서 근거 `json`/`csv`를 확인한다.

## 관련

- [[../README]] — Audit 정리본
- [[../../../ralph/README]] — Ralph plans 인덱스
