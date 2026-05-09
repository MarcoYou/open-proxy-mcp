---
type: readme
title: Audit Raw Data 인덱스
updated: 2026-05-09
---

# Audit Raw Data

ralph 진행 중 audit raw 결과 보존. lesson에서 참조 + 회귀 검증 시 활용.

## 2026-05-08 — 법령 layer audit data (Ralph 4)

- [[260508_law_layer/iter05_kospi_top30]] — KOSPI top 30 spot
- [[260508_law_layer/iter08_kospi_30-60]] — KOSPI 30-60
- [[260508_law_layer/iter08_kospi_100-130]] — KOSPI 100-130
- [[260508_law_layer/iter08_kospi_130-200]] — KOSPI 130-200 (Ralph 4 iter 3)
- [[260508_law_layer/iter08_kosdaq_0-100]] — KOSDAQ 0-100 (Ralph 4 iter 4)
- [[260508_law_layer/iter08_dispute_companies]] — 분쟁 회사 20 (Ralph 4 iter 5)
- [[260508_law_layer/dispute_universe]] — 분쟁 회사 universe csv

## 2026-05-04 — proxy_advise + parse_personnel iter archive

ralph 27 iter 진행 중 failure case + 진단 raw. 작업 완료 후 archive (lesson에 흡수됨).

### parse_personnel_xml iter 4-7
- [[260504_parse_personnel_failure_archive/iter04_status_role_fixed_period_remains]]
- [[260504_parse_personnel_failure_archive/iter05_final_status]]
- [[260504_parse_personnel_failure_archive/iter07_data_limit_confirmed]]

### proxy_advise iter 1-27
- [[260504_proxy_advise_failure_archive/iter01_g2_review_vs_for_pattern]]
- [[260504_proxy_advise_failure_archive/iter02_g2_vote_style_no_effect]]
- [[260504_proxy_advise_failure_archive/iter03_g2_director_eval_fetch_fix]]
- [[260504_proxy_advise_failure_archive/iter04_g2_director_grouping_logic]]
- [[260504_proxy_advise_failure_archive/iter27_g2_remaining_3_cases]]

## 데이터 보존 정책

- ralph 진행 중 audit data는 `data/{YYMMDD_topic}/iter*.{md|json|csv}` 형식
- 작업 완료 후 raw data는 보존 (회귀 검증 + 향후 lesson 인용)
- 디렉토리 명명: 시점 + 토픽 (예: 260508_law_layer)

## 관련

- [[../README]] — Audits 인덱스 (top-level)
- [[../../../ralph/README]] — Ralph plans 인덱스
