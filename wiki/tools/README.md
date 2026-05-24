---
type: readme
title: tools/ — Tool 카탈로그 (16 tool 진입점)
updated: 2026-05-20
---

# tools/ — Tool 카탈로그

> OPM v2 의 16 public tool 진입점. 사용자가 가장 먼저 보는 페이지.
> 각 tool 1 페이지, 통일 schema (frontmatter + 한 줄 요약 + 사용법 + 입력 인자 + 출력 schema + Data sources + 파싱 전략 + 관련 공시/개념/결정/audit + 알려진 issue + 변경 이력).
> 도메인 개념 / 공시 본문 / 정책 결정 정보는 본 폴더에 중복 X. `rules/concepts/`, `rules/disclosures/`, `decisions/`, `architecture/audits/` 로 link만 한다.

Data tool별 상세 공시 매핑은 [[data_tool_disclosure_map]]을 본다.

## 빠른 진입표 (16 tool)

### Company (1)
| tool | 한 줄 |
|------|------|
| [[company]] | 기업 식별 + 최근 공시 인덱스 (모든 data tool 공통 입구) |

### Meeting (2 — 시점 분리, 2026-05-04)
| tool | 한 줄 |
|------|------|
| [[shareholder_meeting_notice]] | 주총 **소집공고** (사전 — DART API/XML, 0.5-1.5s, 5 scope: summary/board/compensation/aoi_change/prov_financials) |
| [[shareholder_meeting_results]] | 주총 **의결 결과** (사후 — 의결 결과, 찬반율, 단일) |

### Data — 지분·재무·거버넌스 (3)
| tool | 한 줄 |
|------|------|
| [[ownership_structure]] | 최대주주/특수관계인/5%/control_map (5 scope, treasury 제거) |
| [[financial_metrics]] | DART 재무 4 endpoint 통합: 51 지표 + 듀퐁/감사의견 (6 scope) |
| [[corp_gov_report]] | 기업지배구조보고서 15지표 + 연도별 추이 (5 scope) |

### Data — 환원·이벤트 (5)
| tool | 한 줄 |
|------|------|
| [[dividend]] | 배당 사실 + 분기별 breakdown (3 scope: summary/detail/history, CSR/TSR drop) |
| [[treasury_share]] | 자사주 9 source — 결정 5종 + 결과 4종 + 사이클 매칭 (2 scope: summary/annual) |
| [[value_up]] | 기업가치제고계획 commitment + 자사주 이행 cross-ref (4 scope) |
| [[corporate_restructuring]] | 합병/분할/주식교환·이전 4종 (DS005, 단일 통합) |
| [[dilutive_issuance]] | 유상증자/CB/BW/감자 4종 (희석률·refixing, 단일 통합) |

### Data — 분쟁·내부거래·근거 (3)
| tool | 한 줄 |
|------|------|
| [[proxy_contest]] | 위임장/소송/5%/vote_math (filer 3-way 분류) |
| [[related_party_transaction]] | 타법인주식 + 단일공급계약 (일감몰아주기) |
| [[evidence]] | rcept_no → 공시일/소스/뷰어 URL (API 0회) |

### Action (2 — 시점 분리)
| tool | 한 줄 |
|------|------|
| [[proxy_advise_before_meeting]] | 주총 **사전** 안건별 FOR/AGAINST/REVIEW/NO_DATA + facts/risk/citation/근거공고/후보 raw (단일 결정 호출, ralph G2 99.36%) |
| [[proxy_result_after_meeting]] | 주총 **사후** 결과 보고 (3 scope) |

> **2026-05-04~05-05 정리 변화**: screen_events drop, proxy_guideline → archive (internal로 만들었지만 호출 X 확인 후 archive), shareholder_meeting → notice + results 분리, proxy_advise scope 10→1 (specialized scope은 각 data tool 직접 호출).

## 16 페이지 통일 schema

```yaml
---
type: tool
title: <tool_name>
domain: discovery | data | policy_matrix | action
scope: [...]                 # 지원 scope list
data_source: [...]           # DART API / KIND / Naver / 정적 JSON
related_disclosures: [...]   # rules/disclosures/ link
related_concepts: [...]      # rules/concepts/ link
related_decisions: [...]     # decisions/ link
related_audits: [...]        # architecture/audits/ link
created: 2026-05-01
---
```

본문 섹션:
1. 한 줄 요약
2. 사용법 (자연어 예시 1-2건)
3. 입력 인자 (표)
4. 출력 schema (data dict)
5. Data sources (DART API / KIND / Naver / Upstage / 정적 JSON, 호출 횟수)
6. 파싱 전략 (3-tier fallback, 알려진 한계, regression 0 검증 audit 링크)
7. 관련 공시 (rules/disclosures/) — link only, 중복 X
8. 관련 개념 (rules/concepts/) — link only
9. 관련 결정 (decisions/) — link only
10. 관련 audit/fix (architecture/) — link only
11. 알려진 issue + TODO
12. 변경 이력

## 카테고리별 통계

| 도메인 | tool 수 | 호출 패턴 |
|--------|---------|---------|
| Company | 1 | corpCode/company/list 기반 식별 |
| Meeting | 2 | DART list/document 중심, 결과는 KIND fallback |
| Data | 10 | DART API 1-14회 병렬, 일부 KIND fallback |
| Evidence | 1 | rcept_no 문자열 기반 URL 생성 |
| Action | 2 | upstream data tool 병렬 호출 후 판단/요약 |

## 진단 필드

성능 병목 추적을 위해 주요 data/action tool은 `data.timings_ms`를 노출한다.
공통 키는 `total`, `resolve_company`이고, tool별로 `scope.summary`, `fetch_decisions`,
`decision_details`, `load_report_document` 같은 stage 키가 추가된다.
최근 3개 회사 실측 기준 반복 병목은 `dividend.decision_details`,
`treasury_share.fetch_decisions`였으며 상세 근거는 `wiki/architecture/audits/260510_data_tools_perf_audit.md` 참조.

## 데이터 소스 매트릭스

| tool | DART API | KIND | Naver | Upstage | 정적 JSON |
|------|----------|------|-------|---------|----------|
| company | ✅ corpCode/company/list | - | 🔧 보강 | - | - |
| shareholder_meeting_notice | ✅ list/document | - | - | - | - |
| shareholder_meeting_results | ✅ list/document | 🔧 fallback | - | - | - |
| ownership_structure | ✅ 사업보고서/majorstock | ✅ changes scope | - | - | - |
| dividend | ✅ alotMatter | - | - | - | - |
| financial_metrics | ✅ fnlttSinglAcnt + Indx + AcntAll + audit | - | - | - | - |
| treasury_share | ✅ DS005 5종 | - | - | - | - |
| value_up | ✅ list/document | ✅ 0184 fallback | - | - | - |
| corp_gov_report | ✅ list/원문 | - | - | - | - |
| corporate_restructuring | ✅ DS005 4종 병렬 | - | - | - | - |
| dilutive_issuance | ✅ DS005 4종 병렬 | - | - | - | - |
| related_party_transaction | ✅ list+키워드 | - | - | - | - |
| proxy_contest | ✅ D/B/I + document | ✅ vote_math whitelist | - | - | - |
| evidence | - | - | - | - | - (문자열 가공) |
| proxy_advise_before_meeting | upstream data tools | upstream | - | - | 판단 규칙/records |
| proxy_result_after_meeting | upstream data tools | results fallback | - | - | records |

✅ = 1차 source / 🔧 = 보조

## 흡수된 archive 페이지 (정보 출처)

본 16 페이지가 흡수하거나 대체한 archive/analysis/ 자료:
- `archive/analysis/screen_events-design.md` → archive 유지, public tool에서는 제거
- `archive/analysis/company-tool-검증-예시.md` → `company.md`
- `archive/analysis/shareholder_meeting-tool-검증-예시.md` → `shareholder_meeting_notice.md` / `shareholder_meeting_results.md`
- `archive/analysis/ownership_structure-tool-검증-예시.md` → `ownership_structure.md`
- `archive/analysis/dividend-tool-검증-예시.md` → `dividend.md`
- `archive/analysis/cash-shareholder-return-2026-04-29.md` → archive 유지, CSR scope 제거
- `archive/analysis/total-shareholder-return-2026-04-29.md` → archive 유지, TSR scope 제거
- `archive/analysis/proxy_contest-tool-검증-예시.md` → `proxy_contest.md`
- `archive/analysis/value_up-tool-검증-예시.md` → `value_up.md`
- `archive/analysis/corporate_restructuring-design.md` → `corporate_restructuring.md`
- `archive/analysis/dilutive_issuance-design.md` → `dilutive_issuance.md`
- `archive/analysis/related_party_transaction-design.md` → `related_party_transaction.md`
- `archive/analysis/corp_gov_report-design.md` → `corp_gov_report.md`
- `archive/analysis/evidence-tool-검증-예시.md` → `evidence.md`
- `archive/analysis/release_v2-action-tool-검증-초안.md` → `proxy_advise_before_meeting.md` / `proxy_result_after_meeting.md`
- `archive/analysis/KIND-주총결과.md` → `shareholder_meeting_results.md` fallback 이력

## 변경 이력
- 2026-05-01: W2 작업 — 초기 tool 페이지 일괄 작성, README catalog 업데이트
- 2026-05-01: financial_metrics tool Phase 1 신규 (DART 재무 4 endpoint), 17 → 18 tool
- 2026-05-18: README 기준을 현재 16 public tool 체계로 정리. `screen_events`, `proxy_guideline`, 구 `shareholder_meeting`, 구 action tool 명칭 제거.
- 2026-05-20: data tool별 상세 공시 매핑 문서 [[data_tool_disclosure_map]] 추가.
