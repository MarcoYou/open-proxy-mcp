---
type: readme
title: tools/ — Tool 카탈로그 (18 tool 진입점)
updated: 2026-05-01
---

# tools/ — Tool 카탈로그

> OPM v2 의 18 public tool 진입점. 사용자가 가장 먼저 보는 페이지.
> 각 tool 1 페이지, 통일 schema (frontmatter + 한 줄 요약 + 사용법 + 입력 인자 + 출력 schema + Data sources + 파싱 전략 + 관련 공시/개념/결정/audit + 알려진 issue + 변경 이력).
> 도메인 개념 / 공시 본문 / 정책 결정 정보는 본 폴더에 중복 X. `rules/concepts/`, `rules/disclosures/`, `decisions/`, `architecture/audits/` 로 link만 한다.

## 빠른 진입표 (18 tool)

### Discovery (1)
| tool | 한 줄 |
|------|------|
| [[screen_events]] | 22종 이벤트 → N개 기업 역조회 (filing-centric) |

### Data — 회사·주총·지분·재무 (5)
| tool | 한 줄 |
|------|------|
| [[company]] | 기업 식별 + 최근 공시 인덱스 (모든 data tool 공통 입구) |
| [[shareholder_meeting]] | 정기/임시 주총 안건/이사/보수/정관/결과 (7 scope) |
| [[ownership_structure]] | 최대주주/특수관계인/5%/자사주/control_map (7 scope) |
| [[corp_gov_report]] | 기업지배구조보고서 15지표 + 연도별 추이 (5 scope) |
| [[financial_metrics]] | DART 재무 4 endpoint 통합: 수익성/안정성/현금흐름/회계risk + 듀퐁/감사의견 (6 scope) **NEW** |

### Data — 환원·재편 (4)
| tool | 한 줄 |
|------|------|
| [[dividend]] | 배당 사실 + CSR(한국식) + TSR(글로벌) (6 scope) |
| [[treasury_share]] | 자기주식 5종 이벤트 (취득/처분/소각/신탁/연간) (6 scope) |
| [[value_up]] | 기업가치제고계획 commitment + 자사주 이행 cross-ref (4 scope) |
| [[corporate_restructuring]] | 합병/분할/주식교환·이전 4종 (DS005, 4 scope) |

### Data — 분쟁·발행·내부거래·근거 (3)
| tool | 한 줄 |
|------|------|
| [[proxy_contest]] | 위임장/소송/5%/vote_math (filer 3-way 분류, 6 scope) |
| [[dilutive_issuance]] | 유상증자/CB/BW/감자 4종 (희석률, refixing, 5 scope) |
| [[related_party_transaction]] | 타법인주식 + 단일공급계약 (일감몰아주기, 3 scope) |
| [[evidence]] | rcept_no → 공시일/소스/뷰어 URL (API 0회) |

### Policy & Matrix (1)
| tool | 한 줄 |
|------|------|
| [[proxy_guideline]] | 7운용사 정책 + OPM Guideline + 12 매트릭스 자동 채점 + NPS (7 scope, 정적 데이터) |

### Action (3)
| tool | 한 줄 |
|------|------|
| [[prepare_vote_brief]] | 투표 메모 (AGM + OWN + CGR + PG + auto_score_matrix) |
| [[prepare_engagement_case]] | engagement 메모 (OWN + PRX + VUP, 사실·근거만) |
| [[build_campaign_brief]] | 캠페인 사실 브리프 (PRX + OWN + AGM, timeline/players) |

## 17 페이지 통일 schema

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

| 도메인 | tool 수 | 평균 line | 호출 패턴 |
|--------|---------|----------|---------|
| Discovery | 1 | 137 | DART list.json 1-40회 (event_type x 페이지) |
| Data | 12 | 148 | DART API 1-14회 (병렬), 일부 KIND/Naver 보강 |
| Policy & Matrix | 1 | 165 | 정적 JSON (DART 호출 0회), NPS 크롤링 옵션 |
| Action | 3 | 152 | upstream 5-10회 (병렬), auto_score_matrix는 추가 |

## 데이터 소스 매트릭스

| tool | DART API | KIND | Naver | Upstage | 정적 JSON |
|------|----------|------|-------|---------|----------|
| screen_events | ✅ list.json | - | - | - | - |
| company | ✅ corpCode/company/list | - | 🔧 보강 | - | - |
| shareholder_meeting | ✅ list/document | ✅ results whitelist | - | - | - |
| ownership_structure | ✅ 사업보고서/majorstock | ✅ changes scope | - | - | - |
| dividend | ✅ alotMatter | - | ✅ TSR price | - | - |
| financial_metrics | ✅ fnlttSinglAcnt + Indx + AcntAll + audit | - | - | - | - |
| treasury_share | ✅ DS005 5종 | - | - | - | - |
| value_up | ✅ list/document | ✅ 0184 fallback | - | - | - |
| corp_gov_report | ✅ list/원문 | - | - | - | - |
| corporate_restructuring | ✅ DS005 4종 병렬 | - | - | - | - |
| dilutive_issuance | ✅ DS005 4종 병렬 | - | - | - | - |
| related_party_transaction | ✅ list+키워드 | - | - | - | - |
| proxy_contest | ✅ D/B/I + document | ✅ vote_math whitelist | - | - | - |
| evidence | - | - | - | - | - (문자열 가공) |
| proxy_guideline | - | - | - | - | ✅ 운용사 데이터 |
| prepare_vote_brief | (upstream) | (upstream) | - | - | (upstream) |
| prepare_engagement_case | (upstream) | (upstream) | - | - | - |
| build_campaign_brief | (upstream) | (upstream) | - | - | - |

✅ = 1차 source / 🔧 = 보조

## 흡수된 archive 페이지 (정보 출처)

본 17 페이지가 흡수한 archive/analysis/ 자료:
- `archive/analysis/screen_events-design.md` → `screen_events.md`
- `archive/analysis/company-tool-검증-예시.md` → `company.md`
- `archive/analysis/shareholder_meeting-tool-검증-예시.md` → `shareholder_meeting.md`
- `archive/analysis/ownership_structure-tool-검증-예시.md` → `ownership_structure.md`
- `archive/analysis/dividend-tool-검증-예시.md` → `dividend.md`
- `archive/analysis/cash-shareholder-return-2026-04-29.md` → `dividend.md` (CSR scope)
- `archive/analysis/total-shareholder-return-2026-04-29.md` → `dividend.md` (TSR scope)
- `archive/analysis/proxy_contest-tool-검증-예시.md` → `proxy_contest.md`
- `archive/analysis/value_up-tool-검증-예시.md` → `value_up.md`
- `archive/analysis/corporate_restructuring-design.md` → `corporate_restructuring.md`
- `archive/analysis/dilutive_issuance-design.md` → `dilutive_issuance.md`
- `archive/analysis/related_party_transaction-design.md` → `related_party_transaction.md`
- `archive/analysis/corp_gov_report-design.md` → `corp_gov_report.md`
- `archive/analysis/evidence-tool-검증-예시.md` → `evidence.md`
- `archive/analysis/release_v2-action-tool-검증-초안.md` → `prepare_vote_brief.md` / `prepare_engagement_case.md` / `build_campaign_brief.md`
- `archive/analysis/KIND-주총결과.md` → `shareholder_meeting.md` (results scope)

## 변경 이력
- 2026-05-01: W2 작업 — 17 tool 페이지 일괄 작성, README catalog 업데이트
- 2026-05-01: financial_metrics tool Phase 1 신규 (DART 재무 4 endpoint), 17 → 18 tool
