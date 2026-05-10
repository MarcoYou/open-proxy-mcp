---
type: tool
title: proxy_advise_before_meeting
domain: action
scope: [decisions]
data_source: [shareholder_meeting (notice), ownership_structure, corp_gov_report, financial_metrics, director_evaluation (internal), agm_first_agenda_fy (internal)]
related_disclosures: [주주총회소집공고, 사업보고서, 기업지배구조보고서]
related_concepts: [의결권, 사외이사, 감사위원, 보수한도, 정관변경, 집중투표, 자본잠식, 신임/연임 detect]
related_decisions: [open-proxy-guideline]
related_audits: [260510_proxy_advise_audit_통합정리, 260504_2200_audit_proxy_advise_framework_iter1-8, 260503_1847_audit_phase4_final, 260504_0028_audit_proxy_advise_rename_regression, 260504_0724_audit_parse_personnel_iter1-7]
created: 2026-05-04
updated: 2026-05-05
---

# proxy_advise_before_meeting

## 한 줄 요약
주총 **사전** 안건별 의결권 권고 + 명확한 결정 근거 한 번에. 1회 호출로 결정 + facts + risk + 정책 근거 + 후보 raw 모두.

## 단순화 (2026-05-05)
이전 10 scope 구조 → **scope param 폐지, 항상 decisions** (단일).
Specialized 정보 (agenda 트리, 후보 raw, 재무 51 지표 등)는 각 data tool 직접 호출 권장:
- 안건/이사후보 → [[shareholder_meeting_notice]]
- 재무 detail → [[financial_metrics]]
- 거버넌스 → [[corp_gov_report]]
- 지분/5%블록 → [[ownership_structure]] / [[proxy_contest]]
- 가치제고 → [[value_up]]

## 사용법

```python
proxy_advise_before_meeting(
    company="KT&G",
    year=2026,
    meeting_type="annual",
    vote_style="open_proxy",
    check_audit_history=False,
)
```

## 입력 인자

| 인자 | 타입 | 필수 | 설명 | 기본값 |
|---|---|---|---|---|
| company | str | yes | 회사명 / ticker / corp_code | - |
| year | int | no | 주총 연도 (사업연도 X) | 자동 (전년) |
| meeting_type | str | no | "annual" / "extraordinary" / "auto" | "annual" |
| vote_style | str | no | `open_proxy` (default). 다른 내부 policy variant는 cross-reference용 비공개 surface이며 사용자 출력에는 실명/식별자 노출 안 함 | "open_proxy" |
| check_audit_history | bool | no | 후보 과거 회사 회계 risk overlap cross-check (+30s) | False |
| format | str | no | "md" / "json" | "md" |

## 출력 schema (decisions)

각 안건별 (`agenda_decisions[]`):

| 필드 | 의미 |
|---|---|
| `decision` | FOR / AGAINST / REVIEW / **NO_DATA** |
| `reason` | 결정 사유 한 줄 |
| `facts` | 정량 fact dict (net_income / cap_status / 1번 안건 본문 FY raw / 후보 평가 등) |
| `risk_factors` | 위험 신호 list ("완전 자본잠식", "장기연임", "이사 회계 risk 이력" 등) |
| `policy_citation` | OPM Guideline 근거 ("§재무제표 — 적정 + 잠식 없음 시 FOR" 등) |
| `policy_basis` | 공개 정책 basis (`Open Proxy guideline` 또는 `Internal policy variant`) |
| `evidence_rcept_no` | 근거 공고 (DART viewer link) |
| `agenda_action` / `appointment_type` | 신임 (`new`) / 연임 (`renewed`) auto detect |

후보 평가 (`candidates_evaluations[]`):

| 필드 | 의미 |
|---|---|
| 독립성 / 결격사유 / 충실성 | 자동 판단 (Korean 자연 라벨: "독립적" / "약한 우려" / "우려" 등) |
| main_job | 현 직책 (전문성 hint) |
| recommendation_reason_raw | 추천사유 (회사 본문 raw) |
| career_company_groups | 경력 (회사·기간) |
| audit_history_check | 과거 회사 회계 risk overlap (옵션) |
| **performance** | **사내이사 연임 후보 한정** — 재직 중 회사 운영 성과 매트릭스 2x3 (ROE/부채비율/CSR × avg/trend), 6 cell 점수, classification good/moderate/weak/bad, rationale 한국어 (자세히는 [[260505_1700_decision_inside-director-performance-matrix]]) |

## 6 upstream chain (병렬)

1. shareholder_meeting (summary + agenda + compensation)
2. ownership_structure (control_map)
3. corp_gov_report (summary)
4. financial_metrics (FY-2 reference, 안정 데이터)
5. director_evaluation (후보 평가)
6. agm_first_agenda_fy (1번 안건 본문 FY raw 추출)

**+ 사내이사 연임 후보 detect 시 추가 chain (회사 단위 1회)**:
7. dividend (history, 10년) — CSR avg/trend 계산
8. treasury_share (summary, 120개월) — 소각 events
9. financial_metrics (yearly) — ROE/부채비율 시계열

## 결정 logic

OPM 자체 함수들 + vote_style 정책 wire:
- `_decide_director_election` (사외/사내·결격·독립성·장기연임 + **사내이사 재직 성과 bad→AGAINST / weak→REVIEW**)
- `_decide_financial_statements` (감사의견·자본잠식)
- `_decide_director_compensation` (이사 보수한도 13 분기 — 자본잠식·소진율<30·적자/yoy<0+인상·50%+ 인상 등)
- `_decide_audit_compensation` (감사 보수한도 11 분기 — 참조 감사보수 규칙 + strict 내부 패턴: 1인당 평균 과소 + 인상률 과다)
- `_decide_retirement_pay` (퇴직금 12 분기 — 황금낙하산·사외이사 퇴직금·지급률 2배수+ 등)
- `_decide_articles_amendment` 안에서 정관변경에 묶인 퇴직금/보수한도 hybrid 처리
- `_decide_dividend` (배당성향·자본잠식·리츠 의무 90%)
- `_decide_articles_amendment` (집중투표 배제 등 위험 키워드)
- `_decide_treasury_share` (소각 vs 처분)
- `_apply_policy_default` (vote_style 정책 default가 case_by_case 아니면 OPM 결정 override)

## 검증

- ralph 27 iter G2 99.36% (vs 8 운용사 majority, 4+ vote case)
- ralph framework iter1~8 KOSPI 100 + KOSDAQ 50 (566 후보 / 1271 안건)
  - G1 4 dimension 노출률 100%
  - G2 NO_DATA false-positive 0%
  - G3 신임/연임 classified 99.5%, 사내 false-new 0%
  - G4 1번 안건 FY raw 추출 98.6%
- ralph 260505 사내이사 성과 매트릭스 (KOSPI 100 + KOSDAQ 50, n=128):
  - G1 classification 노출률 100% (≥99%)
  - G2 적자 16건 모두 special rule 작동, 자본잠식 0건
  - G3 bad→AGAINST, weak→REVIEW 분기 작동 (한화오션 김희철, HD현대중공업 금석호 등)
  - G4 distribution good 29.7 / mod 45.3 / weak 18.0 / bad 7.0 — 모든 target band 충족
- ralph 260505 보수/퇴직 분기 정밀화 (KOSPI 200 + KOSDAQ 50, n=226):
  - G1 파싱 성공률 director 99.2 / audit 100 / retirement 100
  - G2 trigger 정확도 100% — AGAINST 5건 (피에스케이/피에스케이홀딩스/GST 지급률 2배수+ / 카카오페이 사외이사 퇴직금 / 퓨쳐메디신 자본잠식+인상)
  - G3 운용사 4+ majority 정합 100% (director 11/11, audit 1/1)
  - G4 reference rule 정합 100% — 모든 AGAINST가 참조 보수/감사보수/퇴직금 규칙 + OPM Open Proxy v1.3 #6/#7/#8 trigger와 일치
  - 정관 안에 묶인 퇴직금/보수 hybrid 통합 (코붕이 의견)
  - financial_metrics summary에 prev_net_income/yoy 노출 → 흑자+yoy<0 trigger 활성화

## 미수집 (의도적 제외)

- 형사 처벌 / 사적 관계 / 동명이인 (hard-fail)
- 1주당 액면가 (treasury 공시에 없음)
- 1일 매수/매도 한도 (분석 가치 낮음)

## 변경 이력

- 2026-05-05: scope 10 → 1 (decisions만), specialized scope 폐지 (raw는 각 tool 직접 호출). proxy_guideline service archive (실 호출 X 확인).
- 2026-05-04: framework enrichment ralph (facts/risk/citation/근거공고/후보 raw + 신임·연임 auto detect + 1번안건 FY raw)
- 2026-05-04: rename (구 advise_vote_before_meeting) + 9 scope 추가
- 2026-05-02: 구 advise_vote_before_meeting

## ref

- 사후 결과: [[proxy_result_after_meeting]]
- 사전 안건 raw: [[shareholder_meeting_notice]]
- archive (옛 specialized scope service): `wiki/archive/services/policy_comparison.py` / `proxy_guideline.py`
