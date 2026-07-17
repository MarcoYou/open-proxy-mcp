---
type: readme
title: wiki/decisions/ — OPM 정책 + 결정 + 토론
updated: 2026-06-01
---

# wiki/decisions/ — OPM 정책 + 결정 + 토론

> OPM 의사결정 추적. 정책 master + 시점별 결정 + 토론 transcript.

## 핵심 master 파일

| 파일 | 용도 |
|---|---|
| **`open-proxy-guideline.md`** | OPM 자체 의결권 정책 v1.2 (12 카테고리 + OPM 5 기준 + 8 운용사 + N연기금 통합). **유일 master** |
| `260429_0059_decision_voting-policy-consensus-matrix.md` | 8 운용사 합의 매트릭스 (79 토픽). 매트릭스 형태 보존 (master 보조) |
| [[valuation-methodology]] | 밸류에이션 tool 방법론 스펙 (지표×시계열 FY0/TTM/MRQ×측정방식 + 6인 패널 검토 + 린 v1) |
| [[ksic-sector-mapping]] | OPM 자체 업종 분류 — KSIC 중분류 기본 + 6예외 소분류 (실측 분포 근거) |
| [[260429_0059_debate_opm-guideline-7전문가]] | open-proxy-guideline 작성 토론 transcript (역사적 발전) |

## 사용 흐름

### 분석가 / LLM (사람)
→ `open-proxy-guideline.md` 읽기 (12 카테고리별 룰 + OPM 5 기준)

### 코드 (proxy_advise)
→ `open_proxy_mcp/data/asset_managers/policies/open_proxy_v1.json` 로드 (open-proxy-guideline의 머신리더블 버전)

### 정책 변경 시
→ `open_proxy_v1.json` 수정 (코드)
→ `open-proxy-guideline.md` 동기화 (인간 가독)

## 시점별 결정 (yymmdd_hhmm_decision_)

작은 기술적 결정 — 보존 (각자 명확한 scope):

| 파일 | 내용 |
|---|---|
| [[260429_0059_decision_voting-policy-consensus-matrix]] | 8 운용사 합의 매트릭스 |
| [[260429_0216_improvement_turnkey-11agent]] | 11 agent 통합 |
| [[260505_1700_decision_inside-director-performance-matrix]] | 사내이사 성과 매트릭스 2x3 |
| [[260505_1900_decision_compensation-retirement-split]] | 보수/퇴직금 분리 |
| [[260506_0030_decision_notice-scope-cleanup-prov-financials]] | shareholder_meeting_notice scope 정리 |
| [[260506_2330_decision_v1-dead-parsers-archive]] | v1 dead parser archive 결정 |
| [[260507_2330_decision_httpx-connection-pool]] | httpx connection pool |
| [[260508_0030_decision_classify-agenda-parent-shortcircuit]] | _classify_agenda parent 인지 |
| **[[260508_0200_decision_law-layer]]** | **법령 layer 도입 (Ralph 3 결과)** |
| [[260508_0700_decision_law-layer-precision]] | 법령 layer 정밀화 |
| [[260510_0900_decision_d-pattern-body-fallback]] | D 패턴 amendment body fallback |
| [[260510_1015_decision_subagenda-mapping]] | sub-agenda → amendment 1:1 매핑 |
| [[260510_1130_decision_director-faithfulness]] | 사외이사 겸직/충실성 fact 강화 |
| [[260510_1230_decision_career-parser-concat]] | careerDetails concat/boundary 처리 |
| [[260702_1520_decision_usage-is-error-tracking]] | 사용통계 is_error 기록 — 툴 내부 오류 기준 정의 |
| **[[260717_1220_decision_business-content-tool-roadmap]]** | **business_details tool — "II.사업의 내용" 자동추출 설계·실현가능성 로드맵 (156사 census, A+B 착수·D 보류, 스콥 정의)** |

## 정체성 문서 (시점 prefix 없음)

| 파일 | 용도 |
|---|---|
| [[open-proxy-guideline]] | OPM 자체 정책 master |
| [[tool-changelog]] | tool 변경 이력 |
| [[cross-domain-체이닝]] · [[free-paid-분리]] | tool 간 연결과 repo 운영 정책 |
| [[XML-vs-PDF]] · [[BeautifulSoup-파서-선택]] · [[LLM-fallback-설계]] | 파서/데이터 소스 결정 |
| [[pblntf-ty-필터링]] · [[DART-KIND-매핑-화이트리스트-2026-04]] | DART/KIND 검색 정책 |
| [[tool-추가-검증-정책]] · [[파서-성능-추이]] | tool 추가/성능 이력 |

## 관련 페이지

- [[open-proxy-guideline]] (master)
- `open_proxy_mcp/data/asset_managers/policies/open_proxy_v1.json` (코드 master)
- [[260508_0200_decision_law-layer]] (법령 layer 도입)
- [[law-layer-260508]] (lesson)
- [[rules/laws/README]] (법령 자료 입구)

## 신규 결정 추가 시

1. **시점별 결정**: `yymmdd_hhmm_decision_{title}.md`
2. **정책 변경**: `open-proxy-guideline.md` + `open_proxy_v1.json` 동시 update
3. **토론 transcript**: `yymmdd_hhmm_debate_{title}.md`
