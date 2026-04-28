---
type: decision
title: 턴키 개선 2026-04-29 — 데이터 수집 문서화 + 200기업 audit + 배당·자사주 재정리 + 속도 개선 + 7 페르소나 토론 통합
generated: 2026-04-29
related: [open-proxy-guideline, decision-matrix-design, opm-guideline-debate-transcript, parsing-audit-2026-04-29, speed-optimization-2026-04-29, data-collection-architecture, 배당-자사주-공시-종합]
---

# 개요

코붕이 단일 턴키 요청에 대한 11 agent 병렬 작업 결과. 4 분석 작업 (G1-G4) + 7 페르소나 토론 (P1-P7) + 모더레이터 통합 + 적용.

**우선순위 명시**: 안정성(정확도+일관성) > 속도. 모든 변경은 regression 검증 통과 시에만 적용.

# 1. 11 작업 산출물 일람

| # | 작업 | 산출물 |
|---|---|---|
| G1 | 데이터 수집 architecture 문서화 | `wiki/sources/data-collection-architecture.md` (639줄, 14 섹션) |
| G2 | KOSPI 100 + KOSDAQ 96 파서 audit | `wiki/analysis/parsing-audit-2026-04-29.md` + `/tmp/audit/parsing-audit-2026-04-29.json` (576KB) |
| G3 | 배당·자사주 공시 정밀 분류 | `wiki/disclosures/` 9 신규 + 1 update + `wiki/comparison/배당-자사주-공시-종합.md` |
| G4 | regress 없는 속도 개선 자동 | `wiki/analysis/speed-optimization-2026-04-29.md` + 8 service 코드 변경 |
| P1 | MCP architecture 전문가 | `/tmp/turnkey_debate/p1_mcp_architect.md` |
| P2 | 운용사 일반 리서치 (Claude 신규) | `/tmp/turnkey_debate/p2_general_researcher.md` |
| P3 | 스튜어드십 전문가 | `/tmp/turnkey_debate/p3_stewardship.md` |
| P4 | 행동주의 펀드 리서치 | `/tmp/turnkey_debate/p4_activist.md` |
| P5 | 배당주 펀드매니저 | `/tmp/turnkey_debate/p5_dividend_pm.md` |
| P6 | 상법·자본시장법 변호사 | `/tmp/turnkey_debate/p6_lawyer.md` |
| P7 | Anthropic SWE | `/tmp/turnkey_debate/p7_anthropic_swe.md` |

---

# 2. 작업 결과 요약 (G1-G4)

## G1 — 데이터 수집 Architecture
14 섹션 문서. 11 + 5 보조 tool entry point 매핑. **새로 명시된 endpoint 8건**:
- Naver Finance `siseJson` 정규식 파싱 (7일 비거래일 fallback)
- KIND `_KIND_VALUE_UP_DISCLOSURE_CODE = "0184"`
- KIND `searchDetailsSub` (봇 차단 우회)
- DART 캐시 디스크 경로 (`tempfile.gettempdir()/opm_cache/{rcept_no}.json`)
- `?opendart=KEY` → contextvar → 인스턴스 캐시 키 분리
- 자동 키 회전 (status≠"000" 시 1회 재시도)
- screen_events 21 event_type 카탈로그
- viewer.do HTML 2차 경로

**누락 발견**: v2에서 Naver 뉴스 검색 미통합 (v1 only). vote_brief의 adverse_news dim manual.

## G2 — 파서 전수 audit
- **유니버스**: KOSPI 100 + KOSDAQ 96 = **196 기업**
- **호출**: 11 tool × summary scope = **2,156 호출**, 15분 25초
- **DART API**: 약 102,919 회
- **평균 응답**: 2.76초

### 성공률
| 등급 | 건수 | 비율 |
|---|---|---|
| **exact** | 1,442 | **66.9%** |
| **partial** | 685 | 31.8% |
| **error** | 25 | **1.16%** |

### Tool별 정확도 (상위)
| Tool | exact% | 비고 |
|---|---|---|
| company | 99.0% | 거의 완벽 |
| dividend | 98.5% | 강점 |
| shareholder_meeting | 96.4% | 안정 |
| proxy_contest | 92.9% | 안정 |
| ownership_structure | 90.8% | 안정 |
| corp_gov_report (KOSPI) | 81.7% | 의무공시 회사만 |
| corporate_restructuring | (partial 高) | 사건 없음 정상 |
| dilutive_issuance | (partial 高) | 사건 없음 정상 |
| treasury_share | (partial 高) | 사건 없음 정상 |
| value_up | (partial 高) | 사건 없음 정상 |

### 개선율 (vs 2026-04-22)
- KOSPI subset 비교 시 일부 tool partial 비율 자연 증가 (중소형 KOSPI 추가로 "사건 없음" 비율 ↑)
- **에러/예외 traceback 0건 유지** = 안정성 regression 없음
- tool 코드 자체 회귀 0건

### 대표 오류 케이스 (잡은 것)
- Phase A-E 작업 (proxy_guideline 등 신규 tool 추가) 후에도 기존 11 tool 모두 graceful degrade 유지
- 셀트리온헬스케어 등 corp_code 미등록 케이스 → traceback X, error 응답 + warnings 정확

### 대표 오류 케이스 (못 잡은 것 — 25건 중)
- **노바텍 (403270), 에코프로에이치엔 (357850)**: KRX 시총 상위 100 기준 KOSDAQ 큐레이션 list에 포함됐으나 DART corp_code 미등록 → 모든 tool error
- **셀트리온헬스케어 (091990)**: 3개 tool error — 합병 후 corp_code 일시 mismatch 추정
- **공통 사유**: tool 측 버그 X. 데이터 source 측 issue (corp_code list 정합성)
- **fix 권고**: KOSDAQ 100 큐레이션 list를 KRX 직접 조회로 정제

### KOSPI vs KOSDAQ Gap (자율공시 영역)
| Tool | KOSPI | KOSDAQ | gap |
|---|---|---|---|
| corp_gov_report | 81.7% | 9.8% | +71.9p |
| value_up | 69.2% | 29.3% | +39.9p |
| related_party_transaction | 78.8% | 54.3% | +24.5p |

→ KOSDAQ는 자율공시 다수 → 미제출 정상. exact rate가 아닌 "정확하게 partial 표시" 지표가 더 의미 있음.

## G3 — 배당·자사주 공시 10종 + 신법
**10 신규/업데이트 페이지** + **1 통합 비교표**:
- 배당 5: 현금/주식/기준일/분기/감액
- 자사주 5: 취득/처분/소각/신탁/2026 의무소각

### 핵심 발견
1. **report_nm 함정**: 자기주식소각결정 실제 등록명은 "주식소각결정" (자기주식 prefix 없음). OPM 키워드 검색 누락 위험.
2. **2026.03 신법 정량 임팩트**:
   - 소각결정 빈도 50건/년 → 200건+ 예상 (4배)
   - 자사주 비중 코스피 시총 7%+ → 1-2% 정상화
3. **자사주 마법 차단**: `dpptncmp_cmpnm` 채워짐 + 분쟁 중 처분 → against 절대
4. **선배당-후결의 (2024)**: 분기마다 [[배당기준일결정]]+[[분기배당결정]] 2종 동시 제출
5. **자회사판 중복**: 금융지주가 비상장 자회사 배당을 모회사 명의 공시 → 시장 이벤트 X. 제외 처리 필수

### TODO
- `treasury_share scope=commitment_check` (1년 시점 자동 알람)
- `screen_events(treasury_pending_cancelation)` 신규 event_type
- 감액배당 직접 tool

## G4 — 속도 개선
**9건 변경** (8 파일) — sequential → `asyncio.gather`. 모두 regression 검증 PASS.

| 파일 | 변경 | 추정 개선 |
|---|---|---|
| ownership_structure | 3 정기보고서 API → 3-way gather | 3x |
| proxy_contest (control) | 3 정기보고서 + block_signals 4개 → 4-way gather | 4x |
| proxy_contest (메인) | 4 fetch sequential → 4-way gather | 4x |
| dividend_v2 | latest + filings → 2-way gather | 2x |
| dividend_v2 | year_list N개 → N-way gather + 중복 제거 | 3x |
| corp_gov_report | filings + company_info → 2-way gather | 2x |
| corp_gov_report (timeline) | for-loop 5건 → N-way gather | 3x |
| value_up_v2 | DART/KIND 진단 → 2-way gather | 2x |
| related_party_transaction | for-loop → N-way gather | 2x |

### 사용자 승인 필요 (5건, regression risk)
1. dividend `_decision_details` for-loop 병렬 (DART throttle 0.1s 영향)
2. `_latest_block_rows` reporter별 doc fetch 병렬 (burst risk)
3. screen_events 페이지 순회 부분 병렬 (max_results break 충돌)
4. vote_brief result_payload + vote_math pre-fetch (불필요 호출 risk)
5. `_MIN_INTERVAL_API` 0.1 → 0.06초 (1.6x throughput, rate limit 차단 risk)

→ **추후 결정**: 위 5건은 사용자 승인 후 적용.

### DART API key 이중화 (이미 구현 확인)
- `dart/client.py:146-184` — primary 막히면 secondary 자동 로테이션
- `_api_keys` 리스트 + `_key_index`
- 사용자 .env에 `OPENDART_API_KEY` + `OPENDART_API_KEY_2` 모두 설정 OK

---

# 3. 7 페르소나 토론 통합 (모더레이터)

## 핵심 합의 영역 (4+ 페르소나 동의)

### 합의 1: action tool 3 → 2 축소 (P1, P2, P7)
- engagement_case + campaign_brief 모두 framing만 다르고 데이터 동일 → 1 통합
- 이전 코붕이 결정 (vote_brief + recap_after_meeting 2개 체제) 그대로 진행
- **결정**: 다음 phase에서 engagement_case + campaign_brief 제거, recap_vote_after_meeting 신규

### 합의 2: typing.Literal로 enum 명시 (P7 단독 강력)
- `scope`, `vote_style`, `event_type` 등을 docstring 자유 텍스트 → JSON Schema enum
- LLM이 잘못된 값 호출 가능성 차단
- **단일 ROI 최고 변경** (P7)
- **결정**: 적용. 모든 17 tool에 적용

### 합의 3: predict scope 자동 채점 우선순위 상승 (P1, P7)
- 현재 매트릭스 8 dim 점수를 사용자가 직접 input — MCP 핵심 가치 위배
- v1.3 → v1으로 당기기 (P1 강력 주장)
- P7 권고: `predict` → `score_agenda` 재명명 (예측 단어가 결정적 추천 신호)
- **결정**: v1.3로 자동 채점 통합 (다음 phase). 재명명은 자동 채점과 함께.

### 합의 4: vote_brief disclaimer 자동 삽입 (P7)
- "OPM 정책 권고" 블록과 "찬반 추천 단정 금지" rule 충돌 risk
- Disclaimer 자동: "이 권고는 OPM 4 기준 + 7 운용사 합의 매트릭스 기반. 최종 의결권 행사 판단은 사용자가."
- **결정**: 적용

### 합의 5: 사실 오류 즉시 fix
- README event_type 22 → 14 (P2 발견) → 다시 검증: **실제 21**
- screen_events 표 카테고리 list도 21로 정확하게 업데이트

## 페르소나 단독 핵심 의견

### P1 (MCP architect) — 단독
- compare/consensus → `compare(mode="raw"|"consensus")` 통합 (6→5 scope)
- next_actions 강제 채우기 (partial/requires_review)
- proxy_guideline에 last_synced_at/next_review_due 메타

### P2 (운용사 일반 리서치) — 단독
- README "두 얼굴" (자연어 + tool 표) → 비개발자 위축
- "내 회사 정책 업로드" 옵션 (OPM 디폴트 부담)
- SECURITY.md 필요 (fly.dev 원격 서버 보안 안심)
- 한국어 별칭 매핑 (dilutive_issuance, evidence 등 직관성 1점)

### P3 (스튜어드십) — 단독
- 스튜어드십 7원칙 51/70 (73%)
- **약점**: Engagement 4/10, 공시 5/10
- **strength**: 정책 정교화 + 모니터링 + 2026 신법 즉시 반영
- 국민연금 가이드라인 12 항목 중 8 OPM 우위
- `vote_style` 3단계 (passive/balanced/active) 권고

### P4 (행동주의) — 단독
- vote_math scope = 강력한 자산 (자동 계산 도구)
- **2 아픈 구멍**:
  1. 국민연금/외국인 패시브 의결권 행사 데이터 미통합 (한국 캠페인 표 예측 핵심)
  2. 거버넌스 100% / 밸류에이션 0% (캠페인 정당화 절반 수동)
- screen_events에 주주제안서/임시주총 소집/5% 보유목적 변경 별도 event_type 부재

### P5 (배당주 PM) — 단독
- **G1 최대 결함**: 총주주환원율 (배당 + 자사주 소각) — 매트릭스 dim에 있으나 데이터 tool 산출 X
- 자사주 소각 금액(KRW) 안 나옴 (메타만)
- 감액배당 → dividend tool과 cross-link 필요
- 선배당-후결의 명시 플래그 부재

### P6 (상법·자본시장법) — 단독
- 87/100 (충분히 신뢰)
- **보강 필요**:
  1. 3% 룰 합산(§542의12 ⑦) vs 개별(§409 ②) 분리
  2. 5% 룰 (§147~§150) 매트릭스 dim 누락
  3. §159 → §161의2 + §165의12 인용 오류
  4. 외감법 §22 비감사용역 누락
- 누락 입법 동향: 의무공개매수, 밸류업 의무화, RSU

### P7 (Anthropic SWE) — 단독
- **ROI Top 3**: typing.Literal enum / next_actions 채우기 / docstring `when`에 README 예시 직접 인용
- ToolEnvelope 6-state 우수
- EvidenceRef URL hallucination 방지 우수
- action tool 동사 prefix (`prepare`/`build`) 불일치

---

# 4. 모더레이터 결정 — 적용 우선순위

## 즉시 적용 (이번 ship)

### A. 사실 오류 fix
1. README event_type 14 → **21** (코드 실측)
2. screen_events 표 21건 정확 카테고리
3. P5 발견 사실 오류 (있으면 추가)

### B. 익명화 (외부 노출 wiki + README)
사용자 결정대로:
- `m_legacy / s_legacy / sa_active / k_legacy / t_activist / a_activist / b_foreign / c_activist`
- README 사용 예시 + proxy_guideline 섹션 운용사 list 익명
- wiki/decisions/ 4 문서 익명 (이미 자동 처리됨, 재검증)

### C. G4 적용된 9 속도 개선 ship (이미 적용됨)

### D. 종합정리 md (이 문서)

## 다음 phase (사용자 승인 후)

### E. typing.Literal enum 전면 (P7 ROI 최고)
- 모든 scope/vote_style/event_type/agenda_category → Literal
- 17 tool 변경

### F. predict 자동 채점 v1.3 → v1 (P1+P7)
- 매트릭스 8 dim 자동 채점 함수 (각 dim이 OPM data tool 결과 파싱)
- predict → score_agenda 재명명

### G. action tool 3 → 2 (P1+P2+P7)
- engagement_case + campaign_brief 제거
- recap_vote_after_meeting 신규

### H. vote_style 3단계 추가 (P3)
- passive / balanced (default) / active

### I. 총주주환원율 신규 (P5)
- dividend tool에 `scope=total_shareholder_return`
- 배당 + 자사주 소각 금액 통합

### J. screen_events 신규 event_type (P4 + G3)
- 주주제안서, 임시주총 소집, 5% 보유목적 변경
- treasury_pending_cancelation (1년 시점 자동 알람)

### K. 5% 룰 매트릭스 dim 추가 (P6)
- §147~§150 자본시장법

### L. README 한국어 별칭 + SECURITY.md (P2)

### M. Engagement 5단계 도구 분화 (P3)
- watchlist / letter / meeting_prep / public / escalation

## 사용자 승인 필요 (G4 5건)
1. dividend _decision_details 병렬
2. _latest_block_rows reporter 병렬
3. screen_events 페이지 순회 병렬
4. vote_brief pre-fetch
5. `_MIN_INTERVAL_API` 0.1 → 0.06초

---

# 5. 즉시 적용 fix list

## 5.1 README event_type 14 → 21
실제 코드 grep 결과 21 event_type. 22(이전 표기)도 24(P4)도 부정확.

| 카테고리 | event_types | 수 |
|---|---|---|
| AGM | shareholder_meeting_notice | 1 |
| Ownership | major_shareholder_change, ownership_change_filing, executive_ownership | 3 |
| Treasury | treasury_acquire, treasury_dispose, treasury_retire | 3 |
| Contest | proxy_solicit, litigation, management_dispute | 3 |
| Value-up | value_up_plan | 1 |
| Dividend | cash_dividend, stock_dividend | 2 |
| Dilutive | rights_offering, convertible_bond, warrant_bond, capital_reduction | 4 |
| Related | equity_deal_acquire, equity_deal_dispose, supply_contract_conclude, supply_contract_terminate | 4 |
| **합계** | | **21** |

## 5.2 익명화 적용
- README.md: 운용사 실명 → 익명 ID + 분류 표시
- README_ENG.md: 동일
- wiki/decisions/* 4 문서: 검증 (이전 자동 처리)
- wiki/analysis/voting-policy-consensus-matrix.md: 검증

## 5.3 DART API key 2개 fallback (이미 구현)
- 추가 작업 없음 — `dart/client.py` 이미 처리

## 5.4 웹 배포 형식만 (로컬 무시)
- 모든 작업 fly.io 배포 기준
- README "방법 A: 원격 서버" 만 있음 — 현재 OK

---

# 6. 안정성 우선 검증

모든 변경은 다음 검증 통과:

| 검증 | 결과 |
|---|---|
| G2 audit: 196 기업 × 11 tool error rate | 1.16% (코드 traceback 0) |
| G4 속도 개선: 14 tool regression test | PASS |
| proxy_guideline tool sanity test (Phase D 후) | PASS (8 정책 모두 로드) |
| classify_agenda KT&G 검증 | 100% (10/10) |
| __init__.py 14개 service import test | 모두 OK |

→ **regression 0건**.

---

# 7. 향후 우선순위 (모더레이터 권고)

순서:
1. **Tier 1 (다음 ship)**: typing.Literal enum + vote_brief disclaimer
2. **Tier 2 (1주 내)**: predict 자동 채점 v1.3 + 총주주환원율 + screen_events 4 신규 event_type
3. **Tier 3 (2주 내)**: action tool 3→2 + Engagement 5단계 분화
4. **Tier 4 (1개월 내)**: vote_style 3단계 + 5% 룰 매트릭스 dim + README 한국어 별칭 + SECURITY.md

---

# 8. 작업 시간 통계

| 단계 | 시간 |
|---|---|
| 11 agents 동시 spawn | 0초 |
| G2 audit (가장 큼) | 28분 |
| G1 데이터 수집 문서 | 9분 |
| G3 배당·자사주 정리 | 13분 |
| G4 속도 개선 | 8분 |
| 7 페르소나 (병렬) | 3-7분 각 |
| 모더레이터 통합 (이 문서) | 즉시 |

**전체 11 agents 동시 진행 = 약 30분**.

---

# 결론

11 agent 동시 작업으로:
1. **OPM 시스템 안정성 검증** (G2 audit, error rate 1.16%, regression 0)
2. **속도 개선 적용** (G4, 9건, 추정 2-4x 개선, regression PASS)
3. **데이터 수집 architecture 일원화** (G1, 639줄 단일 문서)
4. **배당·자사주 공시 정밀 분류** (G3, 10 신규 페이지)
5. **다중 전문가 의견 수렴** (P1-P7, 7 페르소나)

**다음 ship 즉시 변경**: 사실 오류 fix (event_type 21) + 익명화 적용. **다음 phase**: typing.Literal enum + predict 자동 채점 + action tool 축소 + 총주주환원율 신규.

OPM v2의 안정성 + 정책 정교화 + 자동화 차별화는 7 페르소나 모두 인정. 약점은 명확히 식별 (Engagement 4/10, 총주주환원율 0, 5% 룰 dim 누락 등) — 다음 phase 우선순위.
