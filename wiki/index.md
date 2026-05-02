---
type: index
title: OPM Wiki Index
updated: 2026-05-01
---

# OPM Wiki Index

OPM은 한국 상장사 거버넌스 분석 MCP. 이 인덱스에서 시작.

## Quick Start (사용자 진입점)

OPM tool 17개 카탈로그 -> **[[tools/README]]** (처음 방문 시 여기부터)

### 도메인별
- **Discovery (1)**: [[screen_events]]
- **Data (12)**: [[company]] · [[shareholder_meeting]] · [[ownership_structure]] · [[dividend]] · [[financial_metrics]] · [[treasury_share]] · [[proxy_contest]] · [[value_up]] · [[corporate_restructuring]] · [[dilutive_issuance]] · [[related_party_transaction]] · [[corp_gov_report]] · [[evidence]]
- **Policy & Matrix (1)**: [[proxy_guideline]]
- **Action (2)**: [[proxy_advise_before_meeting]] (10 scope, 다각도 심층) · [[proxy_result_after_meeting]] (2 scope, 결과 보고)

## 카테고리 구조

| 카테고리 | 목적 | 페이지 수 | 수정 가능 |
|---|---|---|---|
| **raw/** | 외부 source (운용사 정책 PDF/xlsx, 외부 reference) | 29 binary + 4 md | NO (절대 수정 금지) |
| **tools/** | 17 tool 진입점 (사용자 입장) | 17 + README | YES (tool 변경 시) |
| **architecture/** | OPM 시스템 설계 + audit + fix | 6 + audits 9+ fixes 3 | YES |
| **decisions/** | OPM 정책 + 판단 + debate | 14 | YES |
| **rules/** | 한국 자본시장 사실 (concepts/disclosures/laws) | 31 + 36 + 3 = 70 | YES (사실 update 시) |
| **archive/** | 흡수된 페이지 (역사 보존) | 48 | WARN (단순 보존) |

총 173 markdown + 29 binary.

## 명명 규칙 (2026-05-01~)

```
시점 있는 문서:  yymmdd_hhmm_{type}_{title}.md
정체성 문서:     {name}.md
```

| Type | Prefix | 예시 |
|---|---|---|
| audit / fix / decision / debate / changelog / improvement / release / log | YES | `260429_2030_audit_parsing-200기업.md` |
| tool / concept / disclosure / law | NO (정체성=이름) | `tools/shareholder_meeting.md` |

상세 schema와 워크플로우는 [[WIKI_SCHEMA]] 참조.

## 자주 쓰는 진입점

### 처음 사용자
- [[tools/README]] - 17 tool 카탈로그
- [[WIKI_SCHEMA]] - wiki 구조 + 명명 규칙

### OPM 정책 알고 싶음
- [[open-proxy-guideline]] - Open Proxy Guideline v1.3 (12 카테고리 + 16 novel topics)
- [[260429_0059_decision_voting-policy-consensus-matrix]] - 8 운용사 합의 매트릭스
- [[260429_0059_debate_opm-guideline-7전문가]] - 7 전문가 토론

### 시스템 동작 이해
- [[architecture/data-collection]] - 데이터 수집 architecture
- [[architecture/3-tier-fallback]] - XML -> PDF -> OCR
- [[architecture/matrix-system]] - 12 매트릭스 + 자동 채점
- [[architecture/proxy-voting-decision-tree]] - 의결권 판단 framework
- [[architecture/pipeline-architecture]] - 199 기업 v4 JSON 배치 파이프라인
- [[architecture/multi-upstream-pattern]] - asyncio.gather tool 표준 5 요소 (corpCode lock/retry/per-call timeout/semaphore/cache)
- [[architecture/lessons-learned]] - MCP 개발 7가지 교훈

### 한국 자본시장 용어 모름
- [[rules/concepts/]] - 31 개념 (배당성향 / 최대주주 / 동일인 / 집중투표 등)
- [[rules/disclosures/]] - 36 공시 유형 (현금배당결정 / 유상증자결정 / 자기주식취득결정 등)
- [[rules/laws/상법개정-타임라인-2026]] - 2025-2027 상법 개정 일정

### 최근 audit / fix
- [[260504_0028_audit_proxy_advise_rename_regression]] - proxy_advise rename + 9 scope 추가 — regression 0 + 100% 일관성
- [[260503_2304_audit_recap_pattern]] - recap_vote 패턴 적용 200×3 100% (multi-upstream-pattern 일반화 검증)
- [[260503_1847_audit_phase4_final]] - advise_vote 200×3 deterministic 100% + regression 0 (Phase 4)
- [[260429_0912_audit_parsing-200기업-v2-no_filing]] - 196 기업 × 11 tool audit
- [[260429_2053_audit_personnel-878명]] - personnel 파서 SUCCESS 79->95%
- [[260429_0942_audit_arithmetic-21지표]] - 산술 정확성 audit
- [[260427_1145_fix_ownership-stockknd]] - 보통주 변형 매칭 fix
- [[260429_0942_fix_corp_gov_report-financial-holding]] - 금융지주 분류 fix

---

## Tools (17 진입점) - `tools/`

전체 카탈로그 + 통계 + 흡수된 archive 매핑은 [[tools/README]].

### Discovery (1)
- [[screen_events]] - 22종 이벤트 -> N개 기업 역조회 (filing-centric)

### Data - 회사·주총·지분·재무 (5)
- [[company]] - 기업 식별 + 최근 공시 인덱스 (모든 data tool 공통 입구)
- [[shareholder_meeting]] - 정기/임시 주총 안건/이사/보수/정관/결과 (7 scope)
- [[ownership_structure]] - 최대주주/특수관계인/5%/자사주/control_map (7 scope)
- [[corp_gov_report]] - 기업지배구조보고서 15지표 + 연도별 추이 (5 scope)
- [[financial_metrics]] - DART 재무 4 endpoint 통합: 수익성/안정성/현금흐름/회계risk + 듀퐁/감사의견 (6 scope) **NEW**

### Data - 환원·재편 (4)
- [[dividend]] - 배당 사실 + CSR(한국식) + TSR(글로벌) (6 scope)
- [[treasury_share]] - 자기주식 5종 이벤트 (취득/처분/소각/신탁/연간) (6 scope)
- [[value_up]] - 기업가치제고계획 commitment + 자사주 이행 cross-ref (4 scope)
- [[corporate_restructuring]] - 합병/분할/주식교환·이전 4종 (DS005, 4 scope)

### Data - 분쟁·발행·내부거래·근거 (4)
- [[proxy_contest]] - 위임장/소송/5%/vote_math (filer 3-way 분류, 6 scope)
- [[dilutive_issuance]] - 유상증자/CB/BW/감자 4종 (희석률, refixing, 5 scope)
- [[related_party_transaction]] - 타법인주식 + 단일공급계약 (일감몰아주기, 3 scope)
- [[evidence]] - rcept_no -> 공시일/소스/뷰어 URL (API 0회)

### Policy & Matrix (1)
- [[proxy_guideline]] - 7운용사 정책 + OPM Guideline + 12 매트릭스 자동 채점 + NPS (7 scope, 정적 데이터)

### Action (2) — 시점 분리 재편 (2026-05-02)
- [[advise_vote_before_meeting]] - 주총 **전** 의결권 행사 메모 (안건별 FOR/AGAINST + 결정 사유 + 후보 평가 3축)
- [[recap_vote_after_meeting]] - 주총 **후** 결과 보고 (가결/부결/찬반율 + 후속 공시 + 위임장 결과)
- (구 prepare_vote_brief / build_campaign_brief 흡수, prepare_engagement_case는 _archive/)

---

## Architecture (6 + audits 7 + fixes 3)

### 시스템 설계 (6)
- [[architecture/data-collection]] - OPM 전수 데이터 수집 entry point + 파싱 방법 (DART/KIND/Naver/Upstage/정적 JSON, 14 섹션 639줄)
- [[architecture/3-tier-fallback]] - XML -> PDF -> OCR 3단계 파싱 전략
- [[architecture/matrix-system]] - 12 카테고리 매트릭스 (100 dim, 76 빙고 패턴) + 자동 채점 v1.3 (통합 페이지)
- [[architecture/proxy-voting-decision-tree]] - 3개 소스 통합 의결권 행사 판단 프레임워크
- [[architecture/pipeline-architecture]] - 199개 기업 v4 JSON 생성 배치 파이프라인
- [[architecture/lessons-learned]] - MCP 개발 7가지 핵심 교훈 (v1->v2 회고, 2026-04-19)

### audits/ (10 시점별)
- [[260411_2023_audit_personnel-벤치마크-v1]] - personnel XML 878명 전수 벤치마크 (SUCCESS 79.4%)
- [[260421_2308_audit_parsing-10tool-20기업]] - 10 data tool × 20 회사 파싱 건강도 audit
- [[260422_0005_audit_parsing-14scope-15기업]] - 확장 audit: 14 scope × 15 회사 + 필드 채움률 + corp_gov_report 포함
- [[260429_0216_audit_parsing-200기업-v1]] - 196 기업 (KOSPI 100 + KOSDAQ 96) × 11 tool 전수 audit (exact 66.9%)
- [[260429_0912_audit_parsing-200기업-v2-no_filing]] - audit v2: no_filing 분리 + 진짜 partial 측정 (4-class)
- [[260429_0942_audit_arithmetic-21지표]] - 산술 정확성 audit (21 지표)
- [[260429_2053_audit_personnel-878명]] - personnel 파서 SUCCESS 79->95%
- [[260501_1820_audit_financial_metrics-6기업]] - financial_metrics Phase 1 sanity (6/6 PASS, status=exact 100%)
- [[260501_2030_audit_financial_metrics-200기업]] - financial_metrics 전수 audit (KOSPI 100 + KOSDAQ 100, exact 96.9%, 자본잠식 2건 검출, 5분)
- [[260502_2300_audit_advise-recap-vote]] - action tool 재편 sanity (advise/recap 신규 + 18→17 회귀 0 + 매핑 3-tier 분류)

### fixes/ (3 시점별)
- [[260427_1145_fix_ownership-stockknd]] - ownership_structure 17건 partial -> 0 fix (stock_knd 변형 positive matching + 3-tier fallback, regression 0)
- [[260429_0216_fix_speed-optimization-9건]] - 9건 sequential -> asyncio.gather 적용 (proxy_contest 4x, ownership 3x, dividend 3x)
- [[260429_0942_fix_corp_gov_report-financial-holding]] - corp_gov_report 금융지주 18건 partial -> 0 fix (financial_form 감지)

---

## Decisions (14)

### 정책 + 매트릭스
- [[open-proxy-guideline]] - OPM 자체 의결권 행사 정책 v1.2 (12 카테고리 116 룰 + 11 novel topics + 2026 신법 7개 + §382의3 cross-cutting)
- [[260429_0059_decision_voting-policy-consensus-matrix]] - 7 운용사 의결권 정책 합의/이견 매트릭스 (79 토픽, 12 카테고리)
- [[260429_0059_debate_opm-guideline-7전문가]] - 7 전문가 토론 + v1.0 -> v1.1 -> v1.2 결정 transcript
- [[260429_0216_improvement_turnkey-11agent]] - 11 agent 병렬 작업 통합 (G1-G4 + 7 페르소나 + 모더레이터)

### Tool 정책 + 변경 이력
- [[tool-changelog]] - Tool 제거/통합/리네임 이력 (41->32->17개, 이유 포함)
- [[tool-추가-검증-정책]] - release_v2 신규 tool 추가 시 action/data별 검증 매뉴얼 + 화이트리스트 체크
- [[cross-domain-체이닝]] - AGM/OWN/DIV 도메인 간 tool 연결 맵 + 시나리오
- [[free-paid-분리]] - MCP(public) + Pipeline(private) 2-repo 구조

### 파서 + 데이터 소스 결정
- [[XML-vs-PDF]] - XML 1차 + PDF 보강이 최적, PDF-only는 역효과
- [[BeautifulSoup-파서-선택]] - lxml 채택 (30% 빠름, 결과 동일)
- [[LLM-fallback-설계]] - 정규식 -> zone 추출 -> LLM 하이브리드 전략
- [[pblntf-ty-필터링]] - DART 검색 시 pblntf_ty 필수 지정, 전체 순회 금지 (D/E/I 코드표)
- [[DART-KIND-매핑-화이트리스트-2026-04]] - KIND 병행 허용 공시 화이트리스트 + false match 사례
- [[파서-성능-추이]] - 2026-03-20부터 04-06까지 8개 파서 개선 이력

---

## Rules

### Concepts (31) - `rules/concepts/`
한국 자본시장 도메인 개념. tool 본문에서 link only.

#### 배당
- [[배당성향]] · [[배당수익률]] · [[시가배당률]] · [[분기배당]] · [[특별배당]] · [[감액배당]] · [[당기순이익]] · [[자본준비금]]

#### 지분 + 주체
- [[지분구조]] · [[최대주주]] · [[대주주]] · [[동일인]] · [[특수관계인]] · [[5%-대량보유]] · [[소액주주]] · [[자사주]]

#### 의결권 + 주총
- [[의결권]] · [[집중투표]] · [[감사위원-의결권-제한]] · [[참석률]] · [[정관변경]] · [[주주제안]] · [[보수한도]] · [[소진율]]

#### 분쟁 + 환원
- [[프록시-파이트]] · [[위임장-권유]] · [[경영권-방어]] · [[주주환원]]

#### 시스템 메타
- [[v4-스키마]] · [[시간순서-규칙]] · [[파서-판정-등급]]

### Disclosures (36) - `rules/disclosures/`
DART/KIND 공시 유형. 공시명 = 페이지명.

#### 주총 + 정기보고서
- [[주주총회소집공고]] · [[주주총회결과]] · [[사업보고서]] · [[반기보고서]] · [[분기보고서]]

#### 배당 (6)
- [[현금배당결정]] · [[주식배당결정]] · [[배당기준일결정]] · [[분기배당결정]] · [[감액배당결정]] · [[배당공시유형]]

#### 자사주 (6)
- [[자기주식결정]] · [[자기주식취득결정]] · [[자기주식처분결정]] · [[자기주식소각결정]] · [[자기주식신탁결정]] · [[자기주식의무소각-2026신법]]

#### 지분 + 위임장
- [[대량보유상황보고서]] · [[위임장권유참고서류]] · [[최대주주등소유주식변동신고서]] · [[최대주주변경]] · [[임원·주요주주특정증권등소유상황보고서]]

#### 분쟁
- [[소송등의제기]] · [[경영권분쟁소송]]

#### 발행 + 재편
- [[유상증자결정]] · [[전환사채발행결정]] · [[신주인수권부사채발행결정]] · [[감자결정]]
- [[회사합병결정]] · [[회사분할결정]] · [[회사분할합병결정]] · [[주식교환·이전결정]]

#### 거래 + 거버넌스
- [[타법인주식및출자증권거래]] · [[단일판매공급계약체결]] · [[기업지배구조보고서]] · [[기업가치제고계획]]

### Laws (3) - `rules/laws/`
- [[rules/laws/상법개정-타임라인-2026]] - 2025-2027 상법 개정 시행 일정
- [[rules/laws/주총방어-시나리오-4가지]] - 상법 개정 대응 방어 전술 4가지 (미래에셋증권)
- [[rules/laws/주총체크리스트-2026]] - 주총 체크리스트 9개 + 상법 개정 타임라인

---

## Archive (48)

흡수된 페이지 (역사 보존, 신규 사용자 안 봐도 OK).

### archive/analysis/ (18)
release_v2 검증 예시 + 설계 문서. 17 tools/* 페이지로 흡수.
[[release_v2-tool-아키텍처]] · [[release_v2-public-tool-검증-매트릭스]] · [[release_v2-action-tool-검증-초안]] · [[KIND-주총결과]] · [[cash-shareholder-return-2026-04-29]] · [[total-shareholder-return-2026-04-29]] 등

### archive/comparison/ (3)
- [[stkrt-vs-ctr_stkrt]] · [[회사측-vs-주주측-위임장]] · [[배당-자사주-공시-종합]]

### archive/decisions/ (2)
matrix-system.md 통합으로 흡수.
[[archive/decisions/decision-matrix-design]] · [[archive/decisions/matrix-auto-scoring-2026-04-29]]

### archive/entities/ (9)
DART/KIND/Upstage 등 외부 entity 페이지. CLAUDE.md path만 archive 보존.
[[archive/entities/DART-OpenAPI]] · [[archive/entities/KRX-KIND]] · [[archive/entities/네이버-금융]] · [[archive/entities/Upstage-OCR]] · [[archive/entities/OpenProxy-MCP]] · [[archive/entities/OpenProxy-AI]] · [[archive/entities/국민연금]] · [[archive/entities/FastMCP]] · [[archive/entities/opendataloader]]

### archive/sources/ (6)
구 RULE 파일 요약 + taxonomy.
[[agm-tool-rule]] · [[div-tool-rule]] · [[own-tool-rule]] · [[dart-kind-disclosure-taxonomy]] · [[devlog]] · [[주총방어전략-2026]]

### archive/templates/ (1)
- [[tool-추가-검증-템플릿]] - 신규 data/action tool 제안 템플릿

### archive root (8)
구 readme + case rule + 단일 disclosure 페이지.
- [opm-readme](archive/opm-readme.md) · [opa-readme](archive/opa-readme.md) · [benchmark](archive/benchmark-personnel-results.md)
- [agm-case-rule](archive/agm-case-rule.md) · [own-case-rule](archive/own-case-rule.md) · [div-case-rule](archive/div-case-rule.md)
- [임원주요주주](archive/임원주요주주특정증권등소유상황보고서.md) · [자기주식취득처분결정](archive/자기주식취득처분결정.md) · [정정공시](archive/정정공시.md)
