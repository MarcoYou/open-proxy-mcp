---
type: readme
title: tools/ — 도구 카탈로그 (18개)
updated: 2026-06-20
---

# 도구(Tool) 카탈로그 — 18개

> OPM의 18개 도구 목록입니다. 도구마다 **답해주는 정보가 다릅니다**. AI 에이전트는 질문에 맞는
> 도구를 스스로 골라 호출합니다. 사용자는 "○○기업 분석해줘"처럼 자연어로 물어보면 됩니다.
>
> 👤 처음이라면 → **[[guide/README]]** (사람용 안내서) · 시스템 동작은 [[guide/architecture]]
> 각 도구의 입력·출력·데이터 출처는 도구 이름을 클릭하면 나옵니다.

## 18개 도구 한눈에 — "무엇을 알고 싶을 때 무엇을 쓰나"

### 🏢 기본 — 회사 찾기
| 도구 | 무엇을 답하나 |
|---|---|
| [company](company.md) | 회사 식별 + 최근 공시 목록 — **모든 분석의 출발점** |

### 🗳️ 주주총회 · 의결권
| 도구 | 무엇을 답하나 |
|---|---|
| [shareholder_meeting_notice](shareholder_meeting_notice.md) | 주총 **소집공고**(주총 전) — 안건·이사 후보·보수한도·정관 변경 |
| [shareholder_meeting_results](shareholder_meeting_results.md) | 주총 **결과**(주총 후) — 안건별 가결/부결·찬반율 |
| [proxy_advise_before_meeting](proxy_advise_before_meeting.md) | **의결권 보조** — 안건별 찬성/반대/검토 + 근거 (핵심 도구) |

### 💰 지분 · 재무 · 지배구조
| 도구 | 무엇을 답하나 |
|---|---|
| [ownership_structure](ownership_structure.md) | 지분 구조 — 최대주주·특수관계인·5% 대량보유·자사주 |
| [financial_metrics](financial_metrics.md) | 재무 지표 — 수익성·안정성·현금흐름·회계 리스크 |
| [valuation](valuation.md) | 상대가치 배수 — PER·PBR·배당수익률 (통화환산·스케일가드) |
| [corp_gov_report](corp_gov_report.md) | 기업지배구조보고서 — 15개 핵심지표 준수 여부 |

### 🎁 주주환원 · 자본
| 도구 | 무엇을 답하나 |
|---|---|
| [dividend](dividend.md) | 배당 — 배당금·총액·배당성향·추이 |
| [treasury_share](treasury_share.md) | 자기주식 — 취득·처분·소각·신탁 |
| [value_up](value_up.md) | 기업가치 제고(밸류업) 계획과 이행 현황 |
| [corporate_restructuring](corporate_restructuring.md) | 합병·분할·주식교환·이전 |
| [dilutive_issuance](dilutive_issuance.md) | 유상증자·전환사채(CB)·신주인수권부사채(BW)·감자 (지분 희석) |

### ⚔️ 분쟁 · 거래 · 리스크
| 도구 | 무엇을 답하나 |
|---|---|
| [proxy_contest](proxy_contest.md) | 경영권 분쟁 신호 — 위임장·소송·5% 경영참여 |
| [corporate_deals](corporate_deals.md) | 회사·지분 인수/매각 (계열사 출자·회수) |
| [order_contracts](order_contracts.md) | 수주·공급계약 (체결·해지, 매출 대비 규모) |
| [risk_events](risk_events.md) | 리스크 사건 — 중대재해·횡령배임·생산중단 |

### 🔗 근거
| 도구 | 무엇을 답하나 |
|---|---|
| [evidence](evidence.md) | 공시 원문 링크 (접수번호 → DART 열람 URL) |

> 📊 도구–공시 채널 매핑(시각 자료): [[tool_disclosure_map]] · [[data_tool_disclosure_map]]
> 📞 도구별 DART 콜 budget(기업당 최대 콜·유니버스 배치 안전 크기): [[tool_call_budget]]

---

# 개발자 · AI용 상세

> 아래는 도구 설계·성능·데이터 출처 등 기술 상세입니다. 사람용 개요는 위 표와 [[guide/architecture]]를
> 보세요. 각 도구 1페이지는 통일된 형식(아래 schema)을 따릅니다.

## 각 도구 페이지 통일 schema

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

본문 섹션: 1. 한 줄 요약 · 2. 사용법(자연어 예시) · 3. 입력 인자 · 4. 출력 schema · 5. Data sources
(호출 횟수) · 6. 파싱 전략(3-tier fallback·한계·regression audit) · 7~10. 관련 공시/개념/결정/audit
link · 11. 알려진 issue·TODO · 12. 변경 이력. (도메인 개념·공시 본문·정책은 본 폴더에 중복하지 않고
`rules/`·`decisions/`·`architecture/`로 link만.)

> **2026-05 정리**: screen_events drop · proxy_guideline archive · shareholder_meeting → notice+results
> 분리 · proxy_advise scope 10→1(specialized scope은 각 data tool 직접 호출).

## 카테고리별 통계

| 도메인 | tool 수 | 호출 패턴 |
|--------|---------|---------|
| Company | 1 | corpCode/company/list 기반 식별 |
| Meeting | 2 | DART list/document 중심, 결과는 KIND fallback |
| Data | 11 | DART API 1-14회 병렬, 일부 KIND fallback |
| Evidence | 1 | rcept_no 문자열 기반 URL 생성 |
| Action | 2 | upstream data tool 병렬 호출 후 판단/요약 |

## 진단 필드

주요 data/action tool은 `data.timings_ms`를 노출한다. 공통 키는 `total`, `resolve_company`이고,
tool별로 `scope.summary`, `fetch_decisions`, `decision_details`, `load_report_document` 같은 stage 키가
추가된다. 최근 3개 회사 실측 기준 반복 병목은 `dividend.decision_details`,
`treasury_share.fetch_decisions`였으며 상세 근거는 `architecture/audits/260510_data_tools_perf_audit.md`
참조. `value_up`은 `classify_value_up_roles`, `role_backfill_search.dart`로 plan/status/result/meta 분리
비용을 노출한다.

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
| corporate_deals | ✅ list+키워드 | - | - | - | - |
| risk_events | ✅ list(I001+B001)+키워드 | - | - | - | - |
| proxy_contest | ✅ D/B/I + document | ✅ vote_math whitelist | - | - | - |
| evidence | - | - | - | - | - (문자열 가공) |
| proxy_advise_before_meeting | upstream data tools | upstream | - | - | 판단 규칙/records |

✅ = 1차 source / 🔧 = 보조

## 흡수된 archive 페이지 (정보 출처)

본 17 페이지가 흡수·대체한 archive/analysis/ 자료:
- `company-tool-검증-예시` → `company.md` / `shareholder_meeting-tool-검증-예시` → `notice`·`results`
- `ownership_structure`·`dividend`·`proxy_contest`·`value_up`·`evidence` 검증예시 → 각 동명 tool
- `corporate_restructuring-design`·`dilutive_issuance-design` → 각 tool / `related_party_transaction-design`
  → `corporate_deals` / `corp_gov_report-design` → `corp_gov_report`
- `cash-shareholder-return`·`total-shareholder-return` → archive 유지(CSR/TSR scope 제거)
- `release_v2-action-tool-검증-초안` → `proxy_advise_before_meeting` / `KIND-주총결과` → `results` fallback 이력

## 변경 이력
- 2026-05-01: 초기 tool 페이지 일괄 작성 + financial_metrics 신규
- 2026-05-18: 현재 16 public tool 체계로 정리(구 tool 명칭 제거)
- 2026-05-20: 도구–공시 매핑 [[data_tool_disclosure_map]] 추가
- 2026-05-31: financial_metrics 56 지표 확장 · value_up 역할 분리
- 2026-06-20: 카탈로그를 사람용("무엇을 답하나")으로 재정리 + 개발 상세 분리
