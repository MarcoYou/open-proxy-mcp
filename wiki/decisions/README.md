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
| [[ksic-sector-mapping]] | OPM 자체 업종 분류 — KSIC 중분류 기본 + 6예외 소분류 (실측 분포 근거) |

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
| [[260505_1700_decision_inside-director-performance-matrix]] | 사내이사 성과 매트릭스 2x3 |
| [[260505_1900_decision_compensation-retirement-split]] | 보수/퇴직금 분리 |
| [[260506_0030_decision_notice-scope-cleanup-prov-financials]] | shareholder_meeting_notice scope 정리 |
| [[260507_2330_decision_httpx-connection-pool]] | httpx connection pool |
| [[260508_0030_decision_classify-agenda-parent-shortcircuit]] | _classify_agenda parent 인지 |
| **[[260508_0200_decision_law-layer]]** | **법령 layer 도입 (Ralph 3 결과)** |
| [[260508_0700_decision_law-layer-precision]] | 법령 layer 정밀화 |
| [[260510_0900_decision_d-pattern-body-fallback]] | D 패턴 amendment body fallback |
| [[260510_1015_decision_subagenda-mapping]] | sub-agenda → amendment 1:1 매핑 |
| [[260510_1130_decision_director-faithfulness]] | 사외이사 겸직/충실성 fact 강화 |
| [[260510_1230_decision_career-parser-concat]] | careerDetails concat/boundary 처리 |
| [[260702_1520_decision_usage-is-error-tracking]] | 사용통계 is_error 기록 — 툴 내부 오류 기준 정의 |
| **[[260717_1220_decision_business-content-tool-roadmap]]** | **business_details tool — "II.사업의 내용" 자동추출 스코프·계약(IN/OUT 폼·필드, strict/candidate 문맥 계약)** |
| **[[260823_1720_decision_financial-notes-tool]]** | **financial_notes tool — 금융사 주석 표 원형 추출 스코프·계약(TE/TD 런타임 판별·앵커·기준일 부착. census 41건)** |
| [[260721_1500_decision_asset-holdings-purpose-buckets]] | asset_holdings 보유자산 목적버킷 6분류(회계사 검토) — 재테크형/부동산 자산주형/지주사 할인형/우호지분형 서사 근거 |

## 정체성 문서 (시점 prefix 없음)

| 파일 | 용도 |
|---|---|
| [[open-proxy-guideline]] | OPM 자체 정책 master |
| [[XML-vs-PDF]] · [[BeautifulSoup-파서-선택]] · [[후보반환-설계]] | 파서/데이터 소스 결정 |
| [[pblntf-ty-필터링]] · [[DART-KIND-매핑-화이트리스트-2026-04]] | DART/KIND 검색 정책 |
| [[data-collection]] · [[multi-upstream-pattern]] | 데이터 수집·병렬 패턴 (PDF/OCR 폴백은 open-proxy-ai 영역 — OPM 은 [[XML-vs-PDF]]) |
| [[mcp-endpoints]] · [[environment-secrets]] | 인프라·구조 (코드 구조는 `docs/ARCHITECTURE.md`) |

## 시점 수정 (yymmdd_hhmm_fix_)

| 파일 | 내용 |
|---|---|
| (fix 분석문 4건 — ownership stockKnd · 속도 최적화 9건 · corp_gov_report 금융지주 · dilutive EB) | 회고 성격이라 storage `wiki-private/archive/opm-decisions/` 로 이관(260902). 살아 있는 규칙은 각 tool 페이지에 있다 |

## 여기 없는 것 (260806 이관)

| 옮긴 것 | 어디로 | 왜 |
|---|---|---|
| `260429_0216_improvement_turnkey-11agent` · `260506_2330_decision_v1-dead-parsers-archive` · `파서-성능-추이` | private storage `wiki-private/decisions/` | 등장하는 tool·파서·PDF/OCR tier 가 현행 코드에 없다(v1 유물) |
| `cross-domain-체이닝` · `260721_1600_decision_getting-started-tool-vs-resource` | 삭제 (260831 archive/ 정리) | 현행 코드와 어긋나는 v1 유물 |

## 관련 페이지

- [[open-proxy-guideline]] (master)
- `open_proxy_mcp/data/asset_managers/policies/open_proxy_v1.json` (코드 master)
- [[260508_0200_decision_law-layer]] (법령 layer 도입)
- [[rules/laws/README]] (법령 자료 입구)

## 신규 결정 추가 시

1. **시점별 결정**: `yymmdd_hhmm_decision_{title}.md`
2. **정책 변경**: `open-proxy-guideline.md` + `open_proxy_v1.json` 동시 update
3. **토론 transcript**: `yymmdd_hhmm_debate_{title}.md`
