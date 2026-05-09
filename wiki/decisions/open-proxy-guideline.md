---
type: decision
title: Open Proxy Guideline v1.2 — OPM 자체 의결권 행사 정책
generated: 2026-04-28
updated: 2026-04-29
version: v1.2
related: [voting-policy-consensus-matrix, decision-matrix-design, opm-guideline-debate-transcript, 2026 신법]
related_audits: [260508_parser_audit]
---

# Open Proxy Guideline v1.2 — OPM 자체 의결권 행사 정책

## v1.2 (2026-04-29) — 외부 advisor 다운그레이드

외부 advisor 추천 퀄리티가 별로인 경우 + 글로벌 논란 있는 경우 있어, OPM은 외부 advisor를 벤치마크로 사용하지 않는다.

- B외국계은 "외국계 운용사가 외부 advisor 참조" 데이터 reference로만 보존 (벤치마크 X). policy_classification: `external_advisor_adoption` → `foreign_manager_external_reference`
- v1.1에서 추가했던 외부 advisor-only novel topics 정리:
  - 삭제: say_on_climate, discharge_of_director
  - 다운그레이드: climate_accountability (한국 운용사 입장만, tier_2 → tier_3_signal)
  - 유지: bundled_slate_against (A행동주의 동조), overboarded_directors (한국 상법 §382의2 + 시행령 §34 + 한투 정량)
- voting_rules 12 카테고리에서 `[외부 advisor direct]` 직접 인용 제거, source/law/evidence_refs를 한국 운용사·법령 중심으로 재정렬. B외국계은 `foreign_reference_note` 필드로 별도 보존
- _decision_matrices.json: 외부 advisor-only dim `climate_accountability_signal` 제거 (101 → 100 dim), bingo `climate_accountability_red` 제거 (77 → 76). `climate_disclosure`는 한국 KOSPI 자산 2조원+ TCFD 의무화 (자본시장법 §161의2, 2024) 기반으로 재정렬
- OPM은 한국 8 운용사 + N연기금 합의 + 한국 법령 + OPM 5 기준 중심으로 운영

## 0. v1 → v1.1 변경 요약 (2026-04-29)

### 0.1 추가 운용사 (5 → 7)

| 운용사 | 분류 | 영향 |
| --- | --- | --- |
| **B외국계 (b_foreign)** | external_advisor_adoption (외부 advisor 2026 v1.1 직접 채택) | 글로벌 표준 즉시 통합 — Climate Accountability·Say on Climate·Bundled Slate·Two-tier·Overboarded(>2)·Pre-emption 20% 정량 |
| **A행동주의 (a_activist)** | active_engagement_activist (행동주의 펀드, against 17.3%) | 적극 행사 원칙 정책 선언 — silent 금지·배당 과소 강화·성과 미연계 강화·외부 인수 자본증가 against |

### 0.2 v1.1 핵심 변경 5건

1. **외부 advisor Climate Accountability 채택** — 고배출 업종 (철강·석화·시멘트·정유) + Climate Action 100+ Focus 한정. matrix_director_election + matrix_financial_statements에 climate dim 신규 (5 운용사 중 미래/T행동주의 부분 합치)
2. **외부 advisor Bundled Slate 채택** — 묶음 선임 시 한 명이라도 거버넌스 결격 시 슬레이트 전체 against. 한국 5 운용사 silent 영역 차단. matrix_director_election에 bundled_slate_signal dim 신규
3. **외부 advisor Two-tier Governance 정량 통합** — 한국 상법 §542의8 ① + 외부 advisor 50%/25% 정합. concurrent_positions dim 강화 (>2 boards)
4. **A행동주의 적극 행사 원칙 정책 선언** — 합병/영업양수도/임원 임면/정관변경 등 중대 사항 silent 금지. matrix_shareholder_proposal에 active_engagement_signal dim 신규
5. **Discharge of Director 한국 미적용 명시** — 글로벌 reference만, default 적용 X

### 0.3 v1.1 통계

- voting_rules: 18건 새 criteria 추가 (12 카테고리 모두 b_foreign/a_activist 입장 강화)
- novel_topics: 8 → 13 (5건 신규 — climate_accountability, say_on_climate, discharge_of_director, overboarded_directors, bundled_slate_against)
- 매트릭스 dim: 96 → 101 (5건 신규)
- 매트릭스 빙고: 67 → 77 (10건 신규)
- cross_cutting: 4 → 7 (3건 신규)
- 7 운용사 합의 매트릭스: 79 topics (consensus 22 + majority 27 = 62%)

## 1. 개요

### 1.1 작성 배경

5개 한국 운용사(M레거시·삼성·삼성액티브·T행동주의·한국투자신탁) 정책 합의 매트릭스만으로는 부족하다. 이유:

- 운용사 평균 against rate 9.9% (5.8 - 16.0%)는 한국 재벌 친화 디폴트를 답습한다는 비판
- 5 운용사 모두 silent한 영역(Say-on-Pay, LID, MoM, 자사주 처분 등) 존재
- 2026 신법 7개 중 어느 것도 5 운용사 정책에 미반영
- 상법 §382의3 (2025 강화 — 회사+주주 양방향 충실의무) 적용 미흡

**v1.1 추가**: B외국계 (외부 advisor 2026 직접) + A행동주의 (행동주의) 통합으로 글로벌 표준 + 행동주의 적극 행사 양 축 강화. 한국 5 운용사 silent 영역 (Climate Accountability, Bundled Slate, Two-tier 정량 등) 차단.

OPM은 **8 운용사 평균 X**, 7 전문가 토론 + 2026 신법 + 외부 advisor (글로벌) 표준 + 행동주의 적극 행사 + 정량 매트릭스를 결합한 차별화된 정책을 제시한다.

### 1.2 OPM 5 기준 (헌법)

1. **소수주주 보호 우선** — 합의 없을 때 소수주주 유리 쪽
2. **거버넌스 투명성** — 정보 부족 시 review (단 강행규정 위반은 against)
3. **장기 가치 관점** — 단기 주가 부양보다 구조 안정
4. **추적 가능성** — 모든 권고에 references (전문가 / 운용사 / 법령)
5. **법령 layer 우선 + 의무·우회 분기** (260508 추가) — 법 정합 = FOR / 법 위반 = AGAINST / 법 테두리 안 우회 의심 = REVIEW. 운용사 정책 stale해도 강행규정 자동 반영. 의무 정확 충족(FOR) ≠ 의무 미달(AGAINST) ≠ 의무 초과 + 우회 의심(REVIEW) 명확 분기. 참조: [[law-layer-260508]] / [[상법-2025-2026-종합]]

### 1.3 차별화 포인트

| 영역 | 5 운용사 | OPM v1 |
| --- | --- | --- |
| 2026 신법 7개 | 미반영 | 즉시 반영 (즉시 시행 ~ 2027.01) |
| 자사주 처분 | review (against rate 0%) | against (소각 commitment 없으면) |
| 물적분할 자회사 상장 | review | against (5명 전문가 합의) |
| Lead Independent Director | silent | for (자율 도입 회사) |
| Majority of Minority | silent | for (자기거래 합병) |
| Say-on-Pay | silent | tier_3 시그널링 |
| 강행규정 위반 | review로 빠짐 | against 절대 (A4+A7) |
| 정량 매트릭스 | 없음 | 12 카테고리 × 8 dim × 5+ 빙고 |

## 2. 12 카테고리 정책

### 2.1 재무제표 승인 (financial_statements)

- **default**: case_by_case
- **for**: 외부감사인 적정의견 + 적법 승인 + 비감사 자문 금지 안
- **against**:
  - 적정 외 의견 (한정·부적정·의견거절) — 5/5 + 7 전문가 (외감법 §10)
  - 회계오류 정정공시 직후 1년 내 재무제표 — A1 학자
- **review**: 재무제표 이사회 승인 정관 변경 (§449의2) — 강행규정 3 요건 미충족 시 against

**근거**: 외감법 §10·§21·상법 §449·§449의2·§382의3
**2026 신법**: 기업지배구조보고서 KOSPI 전체 의무 (2026)

### 2.2 현금배당 (cash_dividend)

- **for**:
  - 합리적 배당정책에 의한 배당 (5/5)
  - 중간/분기 배당, 선배당-후결의, 배당정책 공시
  - 주주환원율 50% 이상 (글로벌 평균)
- **against**:
  - 과소 배당 (동종업계 평균 50% 미만) — 한국 OECD 최하위 20%
  - 과다 배당 (영업현금흐름 음수 + 차입 의존)
- **review**: 이사회 결의 배당 — 배당정책 공시 시 review/for, 미공시 시 against

**근거**: 상법 §462·§462의3·§354·자본시장법 §159·§165의12

### 2.3 정관 변경 (articles_amendment)

- **for**:
  - 사외이사 비중 확대, 의장-CEO 분리, 집중투표 배제 삭제, 전자/서면 투표
  - 주주 사전 정보 공개 강화 (4주 전 권고 — KRX 핵심지표 #1)
- **against**:
  - 주주의결권 축소 (강행 §366), 5영업일 미준수 (강행 §542의4)
  - 시차임기제, 초다수의결 신설, 황금낙하산 (퇴직금 연봉 3배 초과)
  - 주총 본질적 권한 이관 (강행 §361 — 합병·분할·정관변경·이사 선·해임)
  - 차등의결권 상장사 도입 (1주 1의결권 §369 ① 강행)
  - 이사회 규모 7명 미만 축소
- **review**: 회사명 변경 (코붕이 criteria — 모회사 종속 / 사업 정체성 / 무형가치)

**2026 신법**: 독립이사 1/3 (2026.07), 집중투표 의무 (2026.09), 전자주총 (2027.01)

### 2.4 이사 선임 (director_election)

- **for**:
  - 다양성 (성별·전문성 — 자본시장법 §165의20 ②)
  - LID 자율 도입, ESG·기후 위원회 위원
- **against** (7/11 5/5 합의 + 4 OPM 강화):
  - 사외이사 5년 룰 위반 (강행 §542의8)
  - 6년 초과 재직 (계열사 합산 9년 — 강행)
  - 과도 겸임 (사외이사 3개사 / 상장사 임원 2개사)
  - 이사회 출석률 75% 미만
  - 기업가치 훼손 / 주주권익 침해 이력
  - 특정주주 이익 추구 이력 — **§382의3 (2025) 결정적 적용**
  - 탄소중립법 미이행 책임 이사 재선임
  - 정당 사유 없는 임기 중 해임
  - 주주 다수 승인 주주제안 미시행 책임 이사
  - **총수일가/특수관계인 사내이사** — 외부 후보 우월성 입증 부족 시 (A3 OPM 강화)
- **review**:
  - 후보 수 > 선임 예정 수 (장기 가치 적합)
  - 책임 감면 (§400② 단서) — 4 요건 미제시 시 against

**2026 신법**: 독립이사 1/3 의무 (2026.07.23), §382의3 충실의무 강화 (2025.07.22)

### 2.5 감사위원·감사 선임 (audit_committee_election)

- **for**:
  - 감사위원 분리선출 (§542의12 ④) — 2026.09 신법 2명 확대
  - 자산 2조 미만 회사 자율 감사위원회 설치
- **against**:
  - 3% 룰 회피 목적 감사위원회 도입 (강행 §409 ②) — 5/5 만장일치
  - 5년 내 적정 외 의견 또는 중요 제재 후보
  - 비감사용역 보수 > 감사용역 보수 (또는 25% 초과 — 외부 advisor (글로벌))
  - 5년 내 임직원/특수관계인 (강행 §542의11)
- **review**: 재무·회계 전문성 부족 (§542의11 ② 위반 우려)

### 2.6 이사 보수 (director_compensation)

- **for**:
  - 적정 보수한도, 성과-보상 연계 + Clawback 5년
  - 보상위원회 (전원 사외이사), 성과연동 60% + 3년 이연
- **against**:
  - 성과 미연계 (5/5 코어 룰)
  - 적자/순이익 감소 + 보수한도 증액 (§382의3 위반)
  - 스톡옵션 repricing / 2년 vesting 단축 (강행 §340의2·§340의4)
  - 스톡옵션 2% 초과 또는 누적 희석 5%+ (T행동주의·한투 + A3 강화)
  - 사외이사 퇴직혜택, 황금낙하산
  - 보수한도 50%+ 인상 (M&A·IPO 일회성 사유 외 — A3 강화)
- **review**: 5억 초과 + 동종업계 P75 초과

**2026 신법**: 임원보수 공시 시 TSR·영업이익 병기 (2026.05) — Say-on-Pay 사실상 평가 도구화

### 2.7 자기주식 (treasury_share) — **2026 신법 전면 재설계**

- **for**:
  - 자기주식 취득 후 1년 내 의무소각 (2026.03 신법 준수)
  - 합리적 사유 + 소각 commitment
  - 처분 결과 주총 보고 + 처분 주총 승인 의무화
- **against**:
  - **자사주 처분 + 1년 내 소각 계획 없음** (자사주 마법 차단)
  - 합리적 사유 미제시 (단순 경영권 방어)
  - 합리적 사유 없는 새 사업/제3자 배정 처분
  - **분쟁 중 자사주 처분** (한진칼 KCGI 패턴) — A3 활동가
- **review**: 경영권 보호 목적 취득 (사실상 against)

**2026 신법**: 자기주식 1년 내 의무소각 (2026.03.06 시행, 기존 보유분 6개월 유예 후 1년 내) — **자사주 정책 전면 재설계**

### 2.8 합병/인수/영업양수도 (merger)

- **for**: 독립 평가 + 비율 공정 + 시너지 + 페어니스 의견 사전 공개
- **against**:
  - 주주가치 훼손 (5/5 합의)
  - 지배주주 이해상충 합병 + MoM 미적용 (삼성물산-제일모직 패턴)
  - 왕관보석/그린메일
  - 자진 상장폐지 + 소액주주 보호 미비 (한국타이어·MBK 패턴)
  - **MoM 미적용 자기거래 합병** — 5 운용사 silent, OPM 차별화
- **review**: stakeholder 영향, 주식매수청구권 전술 활용

### 2.9 회사분할/지주회사 (spin_off) — **2026.07 신법 결정적**

- **for**: 인적분할 + 비례 배정 + 가치 공정
- **against**:
  - **물적분할 + 자회사 상장** (2026.07 신법 + LG에너지솔루션 사례) — A1+A3+A5+A6+A7 5명 합의 (가장 강력한 신규 입장)
  - 분할 후 지배주주 비대칭 증가 (사익편취)
  - 분할 가치 산정 독립성 부재
  - **지주회사 설립** — 총수 지배력 강화 목적 (롯데·SK·LG·CJ 사례) — A3 활동가
- **review**:
  - 지주회사 설립 (총수 강화 목적 외)
  - 모회사 주주 우선 청약권 (Sweetener) 부여 시

**2026 신법**: 물적분할 후 자회사 중복상장 원칙금지 (2026.07) — 자산 5조원+ 대기업집단 92개 + 상장 모회사 30%+ 비상장 자회사 적용

### 2.10 자본 증가/감소 (capital_increase_decrease)

- **for**: 비례 분할/병합, 스톡옵션·상장폐지 회피 자본증가, 주주배정
- **against**:
  - 무상감자 (강행 §439 + 정당 사유 없음)
  - 발행예정주식 100%+ 증가 (백지수표 권한)
  - 외부 인수 무력화 의도 (대법 2008다50776)
  - 신주인수권 배제 (제3자 배정 20% 초과 — 강행 §418 ② + UK Pre-emption)
- **review**: 합리적 제3자 배정 (목적·가격·대상 검증)

### 2.11 전환사채/신주인수권부사채 (cb_bw)

- **for**: CB/BW 발행 주총 결의 정관 채택 (사모 CB/BW 남용 차단)
- **against**:
  - 우선인수권 50%/20% 초과 (T행동주의·한투 + A3 강화 30%/10%)
  - **사후 리픽싱 조항** (한국 핵심 폐단 — A1+A3)
  - **1년 내 콜옵션** 행사 (지배주주 우호세력 양도 통로)
  - 특정주주·제3자 배정 (인수자 독립성 + 시장가 90% + 보호예수 1년 미충족)
- **review**: 일반 제3자 배정 (목적·규모·이해상충 검토)

### 2.12 주주제안 (shareholder_proposal)

- **for**:
  - ESG/지속가능성 (TCFD·Net-zero·인권·다양성)
  - 경영권 분쟁 시 신주발행/자사주 매각 주총 승인 요구 (한진칼 KCGI 패턴)
  - 독립 사외이사 추가 선임, 감사위원 분리선출 신청
- **against**:
  - 주주가치 훼손 명백 (특정 주주 이해, 단기 차익)
  - 주주 다수 승인 미시행 이사 재선임 (Glass Lewis Responsiveness)
- **review**: 이사회안과 경합 시 (장기 가치 적합 안건)

## 3. 8 Novel Topics

### 3.1 Lead Independent Director (LID) — tier_2

- **default**: for (자율 도입 회사 우호 평가)
- **한국 상태**: 자율 도입 ~8% (KOSPI 5천억+). 풀무원·삼성SDI·삼성SDS 도입
- **근거**: A1+A5+A7. A2 시기상조 의견은 단계적 도입으로 수용
- **2026 신법 시너지**: 독립이사 1/3 의무 (2026.07) → LID 우회 효과 확보
- **구현**: 정관 명시 권고. 미지정 자체로 against 안 함

### 3.2 Majority of Minority (MoM) — tier_2

- **default**: for (자기거래 합병 시 강력 적용)
- **한국 상태**: 법령 없음. 5 운용사 모두 silent. 외부 advisor (글로벌) Korea Policy 명시
- **근거**: A1+A3+A5+A7. UK Takeover Code Rule 16, 한국 §397의2 정신 확대
- **구현**: 관계사 합병 안건에서 controlling shareholder 의결권 비율 + 소수주주 단독 의결 결과 분리. 50% 미만 동의 시 against trigger

### 3.3 Say-on-Pay — tier_3 (시그널링)

- **default**: for (자율 도입 회사)
- **한국 상태**: 법령 미비. 2025.11 금융위 금융회사지배구조법 개정 추진. 2026.05 임원보수 TSR 병기로 사실상 평가 도구화
- **근거**: A1+A5 적극, A2 시기상조 + A4 법령 미비 → 시그널링 단계
- **구현**: 임원보수 공시 시 TSR·영업이익 적정성 review 트리거. 직전 3년 TSR 음수 + 보수 한도 인상 시 against

### 3.4 물적분할 후 자회사 상장 against — tier_1

- **default**: against
- **한국 상태**: 2026.07 신법 원칙금지
- **근거**: A1+A3+A5+A6+A7 5명 합의 + A2 실무 강화 (가장 강력한 신규 입장)
- **사례**: LG화학-LG에너지솔루션 (모회사 시총 약 30조 증발)

### 3.5 자기주식 1년 내 의무소각 — tier_1

- **default**: for (준수 회사)
- **한국 상태**: 2026.03.06 신법 시행
- **근거**: A3 활동가 + A6 신법 + A7 §382의3
- **임팩트**: 한국 자사주 비중 코스피 시총 7%+ → 글로벌 1-2% 수준으로 정상화

### 3.6 통제주주 사내이사 75% 초다수 의결 — tier_3

- **default**: for (선언적)
- **근거**: A3 활동가 (이스라엘식 통제주주 거래 강화)
- **구현**: 총수일가 후보 선임 안건에서 매트릭스 fiduciary_duty_signal red 시 추가 가중치

### 3.7 특수관계자 거래 (일감몰아주기) 사전 공시 + 외부 검증 — tier_2

- **default**: review
- **근거**: A1+A3 (공정거래법 §47 사전 검증)
- **구현**: 일감몰아주기 비중 30% 초과 + 적정성 미공시 시 책임 이사 against

### 3.8 TCFD/Climate Vote 통합 — tier_2

- **default**: review (고배출 업종 한정 적용 — A2 권고)
- **한국 상태**: KOSPI 자산 2조원+ TCFD 의무화 (2025년)
- **근거**: A5 — BlackRock 2024, Climate Action 100+
- **구현**: 고배출 업종 (철강·석화·시멘트·정유) 한정. 일반 회사는 review

## 4. 2026 신법 7개 반영

| # | 신법 | 시행일 | 카테고리 | OPM 적용 |
| --- | --- | --- | --- | --- |
| 1 | 물적분할 후 자회사 중복상장 원칙금지 | 2026-07 | spin_off | against 절대 |
| 2 | 자기주식 1년 내 의무소각 | 2026-03-06 | treasury_share | 자사주 정책 전면 재설계 |
| 3 | 자산 2조+ 집중투표 의무 + 분리선출 2명 | 2026-09-10 | articles_amendment + audit_committee | 정관 배제 시도 against 절대 |
| 4 | 독립이사 1/3 의무 | 2026-07-23 | director_election + articles_amendment | 1/3 미달 시 against |
| 5 | 임원보수 공시 시 TSR·영업이익 병기 | 2026-05 | director_compensation | TSR 음수 + 인상 시 against |
| 6 | 전자주주총회 의무화 | 2027-01-01 | articles_amendment | 사전 채택 회사 우호 |
| 7 | 기업지배구조보고서 KOSPI 전체 842사 의무 | 2026 | all (cross-cutting) | 준수율 < 60% 시 review 트리거 |

## 5. 한국 특수 룰

- **§382의3 (2025) 충실의무 확장** — 모든 카테고리 적용 (회사+주주 양방향). A7 게임체인저
- **§409 ② + §542의12 ④ 감사위원 분리선출 + 3% 룰** — for 강력
- **§382의2 + §542의7 집중투표** — 정관 배제 against, 청구권 사용 권고
- **§366 소수주주 임시주총 소집청구권** — 강행규정, 정관 자치로 축소 시 against 절대
- **§418 ② 제3자 신주배정** (경영상 목적 강행) — 위반 시 against
- **§542의8 ② 사외이사 6년 룰 + 5년 룰** — 강행규정
- **§340의2/§340의4 스톡옵션** 강행규정 (2년 vesting, repricing 금지)
- **§341/§342 자기주식** — 2026.03 신법 1년 내 의무소각
- **§530의12 물적분할** — 2026.07 신법 중복상장 원칙금지
- **KRX 기업지배구조보고서 핵심지표 15원칙** 미준수 시 review 트리거
- **한국 코리아 디스카운트** (P/B 1배 미만 60% 기업) → 주주환원율 50% 권고

## 6. 단계적 도입 (Tier 1/2/3)

### Tier 1 (즉시)
- 5/5 운용사 합의 토픽 모두 채택 (28개 P1 코어 룰)
- 2026 신법 7개 반영
- 한국 특수 룰 적용
- 강행규정 위반 against 절대
- 정량 자동 체크리스트 (사외이사 5년/6년/9년·출석률 75%·스톡옵션 2%·CB/BW 50%/20% 등)

### Tier 2 (2-3년)
- Lead Independent Director 자율 도입 회사 for
- Majority of Minority (M&A 자기거래 안건) for
- 정량 자동 채점 (8 dim 매트릭스 + 빙고 패턴)
- ESG 통합 의결 (TCFD 미공시 + 고배출 업종 review)
- 특수관계자 거래 검증 (일감몰아주기 30% 초과)
- Clawback 5년 환수 표준 명시

### Tier 3 (장기 시그널링)
- Say-on-Pay 자문적 결의 권고 (한국 도입 전 시그널링)
- 차등의결권 against 명시 (상장사)
- 통제주주 사내이사 75% 초다수 의결 (이스라엘식)
- Net-zero 미부합 이사 재선임 against (2030+)
- 이사회 다양성 30% 목표 (2030+)

## 7. 의사결정 7단계

1. **절차적 정합성** — 회사법 절차 위반 (소집, 결의요건) → against 절대
2. **상법 강행규정 체크** — §366, §409②, §418②, §542의8 등 → against 절대
3. **자본시장법 공시 위반** → review (회사 추가 정보 기회) — A4 권고
4. **§382의3 (2025) 충실의무 평가** → 강력 against
5. **주주 동등 대우** — 자기거래, 합병비율, 자기주식 → against/review
6. **경영판단 원칙** — 정보충분성/이해상충부재/합리성 → for/review
7. **12 카테고리 매트릭스 자동 채점** → 빙고 패턴 매칭 → 최종 결정

## 8. 참조 문서

### 8.1 입력 자료
- 7 전문가 의견 (`/tmp/opm_debate/`)
  - A1: expert_governance_scholar
  - A2: expert_practitioner
  - A3: expert_minority_activist
  - A4: expert_lawyer (자본시장법)
  - A5: expert_global_esg
  - A6: research_law_amendments (2026 신법 7개)
  - A7: expert_corporate_lawyer (상법 §382의3 게임체인저)
- 5 운용사 합의 매트릭스 (`open_proxy_mcp/data/asset_managers/_consensus_matrix.json`)
- 5 운용사 정책 (`policies/{m_legacy, s_legacy, sa_legacy, t_activist, kim}_2025-04.json`)
- 5 운용사 의결권 행사 기록 (`records/`)

### 8.2 산출물
- **Open Proxy Guideline v1 JSON**: `open_proxy_mcp/data/asset_managers/policies/open_proxy_v1.json`
- **12 카테고리 의사결정 매트릭스 JSON**: `open_proxy_mcp/data/asset_managers/_decision_matrices.json`
- **매트릭스 시스템 문서**: `wiki/architecture/matrix-system.md` (구 `decision-matrix-design` + `matrix-auto-scoring` 통합)
- **토론 시뮬레이션 Transcript**: `wiki/decisions/260429_0059_debate_opm-guideline-7전문가.md`

### 8.3 관련 위키
- 합의 매트릭스: `wiki/decisions/260429_0059_decision_voting-policy-consensus-matrix.md`
- 12 카테고리 도메인 위키: `wiki/rules/concepts/`

## 9. 검증 체크리스트

### 9.1 v1.0 (2026-04-28)

- [x] 12 카테고리 모두 작성
- [x] novel_topics 8개
- [x] 2026 신법 7개 모두 반영
- [x] §382의3 (2025) 충실의무 모든 카테고리 적용
- [x] 모든 결정에 evidence_refs (전문가/운용사/법령)
- [x] 5 운용사 silent 영역 명시 (자사주 처분, MoM, LID, Say-on-Pay)
- [x] 강행규정 위반 against 절대 (자본시장법 공시 위반은 review로 분리)
- [x] 정량 매트릭스 12개 (8 dim 각, 5+ 빙고)

### 9.2 v1.1 (2026-04-29)

- [x] 7 운용사 매트릭스 재계산 (b_foreign + a_activist 추가)
- [x] novel_topics 13개 (5건 신규)
- [x] 외부 advisor 2026 직접 reference 12건 명시
- [x] 행동주의 적극 행사 원칙 7건 채택
- [x] 한국 5 운용사 silent 영역 9건 명시 채택
- [x] global_reference 필드로 한국 미적용 (Discharge) 마킹
- [x] 매트릭스 dim 5건 신규 + 9건 강화
- [x] 매트릭스 빙고 10건 신규
- [x] cross_cutting 3건 신규 (climate_accountability_signal, bundled_signal, active_engagement_principle)
- [x] v1.0 결정 (코붕이 + 7 전문가) 모두 보존 — 강화/보완만, 뒤집기 없음

## 10. v1.1 변경사항 상세 (2026-04-29)

### 10.1 B외국계 (외부 advisor 2026) 통합 영향

**b_foreign_2025-04.json** (`policy_classification: external_advisor_adoption`)는 자체 정책 없이 외부 advisor 2026 v1.1 Voting Guidelines (Effective 2026-02-01)를 그대로 적용. 이를 통해 **글로벌 표준 12건 직접 reference 통합**:

| 외부 advisor Topic | OPM v1.1 적용 |
| --- | --- |
| **Climate Accountability** | tier_2 — 고배출 업종 + Climate Action 100+ Focus 한정. matrix_director_election dim climate_accountability_signal 신규 |
| **Say on Climate** | tier_3 — 자율 도입 회사 우호. matrix_shareholder_proposal review |
| **Discharge of Director** | tier_3_signal — 한국 미적용 명시 (global_reference only) |
| **Bundled Director Slate** | tier_2 — 한국 5 silent 차단. matrix_director_election dim bundled_slate_signal 신규 |
| **Bundled Articles Amendment** | tier_2 — matrix_articles_amendment dim bundled_articles_signal 신규 |
| **Director Accountability Extension** | reference — 거버넌스 실패자 미해임 시 그가 재직 모든 이사회에서 against |
| **Two-tier Governance** | tier_1 — 자산 2조원+ 50% / 소형사 25% 정량 (한국 상법 §542의8 ① 정합) |
| **Overboarded Outside Directors (>2 boards)** | tier_1 — concurrent_positions dim 강화 (한국 상법과 동일) |
| **5-year Cooling-off** | tier_1 — outside_director_independence_5year (한국 상법 §542의8 ② 정합) |
| **Stock Option Dilution** | tier_1 — 성숙기업 5% / 성장기업 10% 정량 (matrix_director_compensation 강화) |
| **Pre-emption 20%** | tier_1 — preemptive_right dim 정량 (UK Pre-emption + 외부 advisor + 한국 상법 §418 ② 정합) |
| **CB/BW Dilution 20%** | tier_1 — dilution_rate dim 강화 (신주발행과 동일 기준) |

### 10.2 A행동주의 (행동주의) 통합 영향

**a_activist_2025-04.json** (`policy_classification: active_engagement_activist`)는 행동주의 펀드. 정책 일반원칙 제3조 ②에 "합병/영업양수도/임원 임면/정관변경 등 중대 사항에 대해 적극적으로 의결권 행사" 명시. against 17.3% (5 운용사 평균 9.4% 대비 1.8배). 이를 통해 **행동주의 입장 7건 채택**:

| A행동주의 Topic | OPM v1.1 적용 |
| --- | --- |
| **[일반원칙 제3조 ②] 적극 행사** | matrix_shareholder_proposal dim active_engagement_signal 신규. silent 금지 |
| **[Ⅰ-2] 배당 과소 against** | matrix_cash_dividend payout_ratio_vs_industry 강화 |
| **[Ⅱ-4] 성과 미연계 보상 against** | matrix_director_compensation performance_link 강화 (7/7 만장일치) |
| **[Ⅶ-3] 외부 인수 무력화 자본증가 against** | matrix_capital_increase_decrease anti_takeover_signal 강화 |
| **[Ⅱ-2 9] 사외이사 비중 축소 against** | matrix_articles_amendment shareholder_rights_impact 강화 |
| **[Ⅱ-2 12] 일괄투표 방식 against** | matrix_director_election bundled_slate_signal 신규 (외부 advisor와 합치) |
| **[Ⅱ-5, Ⅱ-7] 의결권 대리행사자 자격 주주 제한 against, 소수주주권 행사 어렵게 하는 안 against** | matrix_articles_amendment shareholder_rights_impact 강화 |

### 10.3 새 Novel Topics 5건 (총 8 → 13)

| # | Novel Topic | Default | Stage | Source |
| --- | --- | --- | --- | --- |
| 9 | **climate_accountability** | review | tier_2 | b_foreign (외부 advisor) + 미래/T행동주의 통합 |
| 10 | **say_on_climate** | review | tier_3 | b_foreign (외부 advisor) 단독 |
| 11 | **discharge_of_director** | global_reference_only | tier_3_signal | b_foreign (외부 advisor) — 한국 미적용 |
| 12 | **overboarded_directors** | against | tier_1 | b_foreign (외부 advisor) 정량 + 한국 상법 정합 |
| 13 | **bundled_slate_against** | against | tier_2 | b_foreign (외부 advisor) + a_activist 합의 — 한국 silent 차단 |

### 10.4 새 매트릭스 dim 5건 (총 96 → 101)

| Matrix | New Dim | Source |
| --- | --- | --- |
| matrix_director_election | bundled_slate_signal | 외부 advisor + a_activist |
| matrix_director_election | climate_accountability_signal | 외부 advisor Climate Accountability |
| matrix_financial_statements | climate_disclosure | 외부 advisor / TCFD |
| matrix_articles_amendment | bundled_articles_signal | 외부 advisor Bundled Articles |
| matrix_shareholder_proposal | active_engagement_signal | a_activist 일반원칙 제3조 ② |

### 10.5 강화 매트릭스 dim 9건

| Matrix | Strengthened Dim | Reason |
| --- | --- | --- |
| matrix_director_election | concurrent_positions | 외부 advisor >2 boards 정량 통합 |
| matrix_director_compensation | performance_link | 행동주의 + 외부 advisor — 7/7 만장일치 against trigger |
| matrix_articles_amendment | shareholder_rights_impact | 행동주의 (A행동주의) + 외부 advisor (B외국계) 통합 |
| matrix_cash_dividend | payout_ratio_vs_industry | 행동주의 (A행동주의) 배당 과소 강화 |
| matrix_capital_increase_decrease | preemptive_right | 외부 advisor 20% 정량 통합 |
| matrix_capital_increase_decrease | issuance_size | 외부 advisor 100% 초과 정당화 명시 |
| matrix_capital_increase_decrease | anti_takeover_signal | 행동주의 (A행동주의) 외부 인수 무력화 |
| matrix_cb_bw | dilution_rate | 외부 advisor 신주발행 동일 기준 |
| matrix_treasury_share | ownership_structure_signal | 행동주의 (A행동주의) 외부 인수 무력화 정신 |

### 10.6 7 운용사 합의 매트릭스 (v3) 통계

- **총 79 topics** (기존 72 + 신규 7)
- consensus 22 (28%), majority 27 (34%), divergence 7 (9%), minority 23 (29%)
- consensus + majority = 62% (강한 합의)
- v1.1 신규 토픽 7건:
  - climate_accountability_iss (B외국계 단독)
  - say_on_climate_iss (B외국계 단독)
  - discharge_of_director_iss (B외국계 단독, 한국 미적용)
  - bundled_slate_against_iss (B외국계 + A행동주의)
  - director_accountability_extension_iss (B외국계 단독)
  - two_tier_governance_iss (B외국계 단독)
  - active_engagement_principle_align (A행동주의 단독)

### 10.7 외부 advisor direct reference 마킹 규칙

OPM v1.1에서 B외국계 (외부 advisor) 입장은 voting_rules 항목에 `global_reference` 필드로 명시:

```json
{
  "criterion": "[외부 advisor direct] ...",
  "source": "b_foreign (외부 advisor 2026 ...)",
  "law": "외부 advisor 2026 / 한국 상법 ...",
  "global_reference": "외부 advisor 2026 Voting Guidelines",
  "evidence_refs": ["b_foreign.section..."]
}
```

한국 미적용 글로벌 개념 (Discharge of Director)은 `default: "global_reference_only"`로 마킹되어 자동 적용 X.

## 11. v1.3 변경 (2026-04-29) — c_activist (C행동주의) 통합

### 11.1 추가 운용사 (7 → 8)

| 운용사 | 분류 | against rate | 영향 |
| --- | --- | --- | --- |
| **c_activist (C행동주의)** | active_engagement_activist (행동주의 펀드, 자본시장 표준 가이드라인 + 본문 행동주의 적극 행사 원칙) | 27.5% (a_activist 17.3%, 5 운용사 평균 9.4% 대비 가장 강함) | 행동주의 3사 (T/A/C) 일치 영역 명확화 + c_activist 단독 17건 신규 토픽 (한국 silent 영역 추가 차단) |

### 11.2 v1.3 핵심 변경 5건

1. **8/8 만장일치 영역 8건 명시** — non_qualified_audit_opinion (against), outside_director_ratio_increase (for), cumulative_voting_exclusion_removal (for), supermajority_voting (against), golden_parachute (against), outside_director_independence_5year (against), concurrent_positions (against), company_value_damage_history (against). voting_rules에 v1_3_note로 마킹
2. **행동주의 3사 (T/A/C) 일치 24건 명시** — c_activist 통합으로 행동주의 강도 강화. shareholder_rights_restriction·anti_takeover_capital_increase·preemptive_right_exclusion·active_engagement_principle 등 active_engagement 영역 강화
3. **c_activist 단독 신규 17 토픽** — 새 novel_topics 5개 (executive_officer_system, internal_trade_committee, outside_director_independence_annual_disclosure, compensation_disclosure_below_500m, executive_hedging_prohibition) + 12개 기존 토픽 신규 추가 (treasury_share_burnout_for_value, treasury_share_disposal_for_new_investment, forfeited_shares_to_insider_against, ceo_3rd_term_supermajority, board_evaluation_disclosure, outside_director_stock_lock_until_retirement, stock_purchase_loan_against, lead_independent_director_intro, nominating_committee_majority_outside, cb_bw_for_distressed_restructuring, political_donation_blanket_ban_against, charity_donation_restriction_against)
4. **bundled_slate_against / lead_independent_director / overboarded_directors** novel_topics 강화 — c_activist 동조 명시 (행동주의 2사 합의)
5. **active_engagement_principle 강화** — a_activist 단독 → 2사 일치로 채택. silent 금지 정책 선언 강화

### 11.3 v1.3 통계

- voting_rules: 17건 신규 c_activist criteria + 14건 기존 source 보강 (v1_3_note 마킹)
- novel_topics: 11 → 16 (5건 신규)
- 8 운용사 합의 매트릭스: 96 topics (consensus 22 + majority 26 = 50%)
- 8/8 만장일치 8건
- 7/8 합의 14건
- 행동주의 3사 (T/A/C) 일치 24건
- c_activist 단독 신규 17건

### 11.4 8 운용사 합의 매트릭스 (v4) 통계

| 항목 | v3 (7 운용사) | v4 (8 운용사) | 변화 |
| --- | --- | --- | --- |
| 총 토픽 | 79 | 96 | +17 (c_activist 단독) |
| consensus | 22 | 22 | ±0 (count 7→8 강화 7건) |
| majority | 27 | 26 | -1 |
| divergence | 7 | 21 | +14 |
| minority | 23 | 27 | +4 |
| consensus rate | 27.8% | 22.9% | -4.9%p (8 운용사 확대 자연 감소) |
| majority+ rate | 62.0% | 50.0% | -12.0%p (silent 운용사 정량적 영향) |

**유의**: consensus rate 하락은 c_activist 단독 신규 토픽 17건이 minority로 추가된 것 때문. 7 운용사 합의 영역에서 c_activist가 동조하면 consensus count는 7→8로 강화 (이는 만장일치 영역 8건 발견에 반영).

### 11.5 v1.3 강화 영역 (v1_3_note 마킹)

- financial_statements: non_qualified_audit_opinion (8/8), non_audit_service_prohibition (3/8 — C행동주의 명시 채택)
- cash_dividend: appropriate_dividend_policy (7/8), excessive_or_insufficient_dividend (행동주의 3사 일치 강화)
- articles_amendment: outside_director_ratio_increase (8/8), cumulative_voting_exclusion_removal (8/8), supermajority_voting (8/8), golden_parachute (8/8), shareholder_rights_restriction (행동주의 3사 일치 강화)
- director_election: outside_director_independence_5year (8/8), concurrent_positions (8/8), company_value_damage_history (8/8), bundled_slate_against (행동주의 2사 강화), executive_officer_system_for (신규), ceo_3rd_term_supermajority (신규), board_evaluation_disclosure (신규)
- audit_committee_election: 5 토픽 변경 없음 (c_activist는 분리 선출 등 합의 영역 동조)
- director_compensation: deferred_compensation_60pct_3yr (4/8 강화), outside_director_stock_lock_until_retirement (신규), compensation_disclosure_below_5억 (신규), hedging_derivatives_prohibition (신규), stock_purchase_loan_against (신규)
- treasury_share: treasury_share_disposal_to_market (4/8 강화), treasury_share_burnout_for_value (신규), treasury_share_disposal_for_new_investment (신규)
- merger: 5 토픽 변경 없음 (c_activist는 review/long-term holistic 동조)
- spin_off: 2 토픽 변경 없음 (c_activist는 long-term value review 동조)
- capital_increase_decrease: anti_takeover_capital_increase (행동주의 3사 일치 강화), forfeited_shares_to_insider_against (신규)
- cb_bw: agm_resolution_for_cb_bw (4/8 강화), cb_bw_for_distressed_restructuring (신규)
- shareholder_proposal: active_engagement_principle (행동주의 2사 강화), non_implementation_director_reelection (3/8 강화), political_donation_blanket_ban_against (신규), charity_donation_restriction_against (신규)

### 11.6 v1.0/v1.1/v1.2 결정 보존 여부

- v1.0 (코붕이 + 7 전문가) 결정: 모두 보존
- v1.1 a_activist 행동주의 7건: 모두 보존, c_activist 동조로 강화
- v1.2 외부 advisor 다운그레이드: 모두 유지 (B외국계은 외국계 외부 advisor 참조 사례로 보존)
- v1.3 c_activist 통합: 강화/통합만, 뒤집기 X

### 11.7 c_activist 단독 토픽 채택 원칙

c_activist 단독 명시 영역은 한국 운용사 silent 영역에 한해 채택 (한국 법령·KCGS 모범규준·KRX 핵심지표 정합 우선). 행동주의 적극 행사 원칙 (silent 금지)을 a_activist + c_activist 2사 합의로 강화하되, OPM은 한국 8 운용사 + N연기금 + 한국 법령 + OPM 5 기준 중심을 유지한다.
