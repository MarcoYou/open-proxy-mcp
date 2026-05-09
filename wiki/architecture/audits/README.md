---
type: readme
title: Audits 인덱스 (시간순)
updated: 2026-05-09
---

# Architecture Audits

OPM 검증 / 진단 결과 시간순 인덱스. 각 audit은 `yymmdd_hhmm_audit_{title}.md` 명명.

audit는 [트리 작은가지 (시점 작업)](../../WIKI_SCHEMA.md#0-트리-구조-식물학-metaphor). 관련 ralph + lesson과 양방향 link 필수.

## 2026-05

- [[260509_wiki_graph_audit]] — Wiki 그래프 분석 + 명명 패턴 표준화
- [[260508_parser_audit]] — 파서 전수조사 (Ralph 5 trigger)
- [[260508_0030_audit_classify-agenda-parent-shortcircuit]] — 안건 분류 parent short-circuit
- [[260507_2330_audit_classify-agenda-fix]] — 안건 분류 fix
- [[260506_2330_audit_parser-omnibus-perf]] — parser omnibus 성능
- [[260506_0030_audit_notice-scope-cleanup]] — notice scope 정리
- [[260505_1900_audit_compensation-retirement-split]] — 보수/퇴직금 분기
- [[260505_1611_audit_inside-director-performance-matrix]] — 사내이사 성과 매트릭스
- [[260505_0530_audit_treasury_execution_iter1-8]] — 자사주 결과보고서 iter 1-8
- [[260504_2200_audit_proxy_advise_framework_iter1-8]] — proxy_advise framework iter 1-8
- [[260504_0724_audit_parse_personnel_iter1-7]] — parse_personnel iter 1-7
- [[260504_0705_audit_proxy_advise_ralph_final]] — proxy_advise ralph final
- [[260504_0028_audit_proxy_advise_rename_regression]] — proxy_advise rename 회귀
- [[260503_2345_audit_ownership_baseline]] — ownership baseline
- [[260503_2330_audit_proxy_contest_baseline]] — proxy_contest baseline
- [[260503_2304_audit_recap_pattern]] — recap pattern
- [[260503_1847_audit_phase4_final]] — Phase 4 final
- [[260503_0500_audit_phase3_final]] — Phase 3 final
- [[260503_0130_audit_advise-200-virtual]] — advise 200 가상실험
- [[260502_2300_audit_advise-recap-vote]] — advise/recap vote
- [[260501_2030_audit_financial_metrics-200기업]] — financial_metrics 200기업
- [[260501_1820_audit_financial_metrics-6기업]] — financial_metrics 6기업

## 2026-04

- [[260429_0942_audit_arithmetic-21지표]] — 산술 21지표
- [[260429_0912_audit_parsing-200기업-v2-no_filing]] — parsing 200기업 v2
- [[260422_0005_audit_parsing-14scope-15기업]] — parsing 14scope 15기업
- [[260411_2023_audit_personnel-벤치마크-v1]] — personnel 벤치마크 v1

## 데이터 (raw 결과)

iter raw 결과는 [[data/README]] 참조 (ralph 진행 중 audit 데이터).

## 신규 audit 추가 시

1. `yymmdd_hhmm_audit_{title}.md` 명명
2. frontmatter 4축 (trigger ralph / related_tools / 후속 lesson / 영향 decision) 명시
3. 본 README index 업데이트
4. `python3 scripts/wiki_lint.py` 통과 확인
