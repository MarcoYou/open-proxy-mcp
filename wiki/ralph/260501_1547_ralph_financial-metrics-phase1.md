---
type: ralph
title: financial_metrics tool Phase 1 — DART 재무 API 연동 신규 tool
created: 2026-05-01 15:47
completion_promise: FINANCIAL_METRICS_PHASE1_DONE
max_iterations: 20
---

# financial_metrics tool Phase 1 ralph

OPM에 재무지표 tool 신규 추가. 거버넌스 도구의 단일 최대 갭 (재무 0%) 메우는 작업. P4 행동주의 분석가 + P5 배당주 PM 모두 지적한 영역.

**Phase 2 (vote_brief 통합 / 매트릭스 dim 자동 채점 / Marco 시나리오)는 별도. 이번 ralph는 Phase 1만.**

---

## 매 iteration 작업

1. **현황 확인**: `git status`로 어디까지 됐는지 + 미흡한 부분 식별
2. **다음 1 step만 진행** (검증 가능 단위로 작게 쪼갬)
3. **sanity test** (실제 호출 + 결과 확인)
4. **self-critique**: 성공 기준 vs 현재 갭 / 가짜 데이터 risk / 안정성 측면 / 회귀 가능성
5. **commit** (의미 있는 변경마다, 커밋 메시지에 step 명시)
6. 다음 iteration 계획 1줄

---

## 성공 기준 (모두 충족 시 promise 출력)

### 코드
- [ ] **DART 재무 API client 4 endpoint** 추가 (`open_proxy_mcp/dart/client.py`):
  - [ ] `fnlttSinglAcnt` (단일회사 주요계정 — 재무상태/손익)
  - [ ] `fnlttSinglIndx` (주요 재무지표 — ROE/부채비율/EPS 등)
  - [ ] `fnlttSinglAcntAll` (전체 재무제표 — 현금흐름표 포함)
  - [ ] `accnutAdtorNmNdAdtOpinion` (회계감사인 + 감사의견 + 강조사항 + KAM)
  - [ ] rate limiter 통과 (1,000/min) + DART API key 2개 fallback (이미 구현)
  - [ ] 응답 status 코드 처리: 000 정상 / 013 자료 없음 → no_filing / 020 한도초과 → key 회전
- [ ] **`open_proxy_mcp/services/financial_metrics.py`** 신규 — 6 scope:
  - [ ] `summary` (1 사업연도 핵심 지표)
  - [ ] `yearly` (연도별 추이 Last 3 financial years)
  - [ ] `quarterly` (분기별 추이 4Q × 3년 = 12 분기)
  - [ ] `yoy` (전년 대비 + alerts)
  - [ ] `qoq` (전분기 대비 + alerts)
  - [ ] `audit_opinion` (감사의견 3년 추이 — 한정/부적정/의견거절 시점 식별)
- [ ] **`open_proxy_mcp/tools_v2/financial_metrics.py`** 신규 (render markdown/json + register_tools)
- [ ] FastMCP auto-discovery 등록 (server.py 변경 X — `register_all_tools_v2`가 자동)

### Schema 정합성 (한국 표준)
- [ ] `consolidated: bool = True` default — 한국 표준 = **연결 지배주주 귀속**
- [ ] **단위 처리 정책**:
  - 모든 금액: `_krw` suffix = **원 단위 raw int** (예: `revenue_krw: 302_000_000_000_000`)
  - 모든 %: `_pct` suffix = **% 형식 float** (예: `roe_pct: 11.5`, decimal 0.115 X)
  - 모든 비율: `_ratio` suffix = **decimal float** (예: `cfo_to_op_ratio: 0.85`)
  - 일수: `_days`, 갯수: `_count`
- [ ] **DART 응답 단위 정규화** (`normalize_amount` 헬퍼):
  - DART 표준 = 원 단위 raw → as-is
  - 응답에 "백만원" / "천원" 단위 메타 있으면 자동 곱셈 (×1,000,000 / ×1,000)
  - 괄호 음수 처리 (`(500)` → -500, T19 fix 패턴)
  - None/"-"/빈값 → None graceful
- [ ] **사람 가독 변환은 render 함수에서만** (service layer는 항상 raw KRW)
  - `format_krw_human(302_000_000_000_000)` → `"302.0조"`
  - `format_krw_human(5_884_000_000_000)` → `"5,884억"`
- [ ] 단위 검증: KOSDAQ 1개 회사가 백만원 단위 보고하면 자동 변환 정확
- [ ] `summary` 핵심 필드 (수익성 + 안정성 + 효율 + 회계 risk):
  - **수익성**:
    - 매출액 (revenue_krw)
    - 매출총이익 (gross_profit_krw)
    - 영업이익 (operating_profit_krw)
    - 영업이익률 (operating_margin_pct)
    - **EBITDA** (ebitda_krw — 영업이익 + 감가상각비)
    - **EBITDA 마진** (ebitda_margin_pct)
    - 당기순이익 (net_income_krw — 연결 지배주주 귀속)
    - EPS (eps_krw)
    - ROE (roe_pct)
    - **ROA** (roa_pct — 순이익 / 평균 총자산, ROE의 짝, 레버리지 영향 제거)
    - **ROIC** (roic_pct — 투자자본수익률, 보수 적정성 평가 핵심)
  - **ROE 듀퐁 3단 분석** (ROE 변동 원인 분해 + 레버리지 risk cross-check):
    - **순이익률** (net_profit_margin_pct — 순이익 / 매출)
    - **총자산회전율** (asset_turnover_ratio — 매출 / 평균 총자산)
    - **재무레버리지** (equity_multiplier — 평균 총자산 / 평균 자기자본)
    - **ROE 검증** (= 위 3개 곱, 단순 ROE와 일치 확인)
    - **ROE 변동 분해** (yearly scope에서 — 어떤 요소가 ROE 변화 주도?)
  - **안정성 / 부채**:
    - 부채비율 (debt_ratio_pct)
    - 유동비율 (current_ratio_pct)
    - **이자보상배율** (interest_coverage_ratio — 영업이익 / 이자비용)
    - 차입금의존도 (debt_dependency_pct — 총차입금 / 자산총계)
    - **순현금** (net_cash_krw — 현금성자산 - 총차입금, 음수면 순부채. 코리아 디스카운트 cross 핵심)
  - **현금흐름** (코리아 디스카운트 + 분식 cross-check 핵심):
    - **CFO** (cfo_krw — 영업활동 현금흐름)
    - **CapEx** (capex_krw — 유형자산 취득, 투자활동 현금유출)
    - **FCF** (fcf_krw — CFO - CapEx, 진정한 cash 창출)
    - **FCF 마진** (fcf_margin_pct — FCF / 매출)
    - **CFO / 영업이익** (cfo_to_op_ratio — 현금 quality, <0.7 = 분식 신호)
    - **CapEx / 감가상각비** (capex_to_da_ratio — >1 확장 / <1 유지보수)
    - **현금배당 / FCF** (dividend_to_fcf_pct — 배당 capacity 활용도, **코리아 디스카운트 핵심**)
  - **운전자본** (Working Capital — CFO 압박 + 효율성):
    - **운전자본** (working_capital_krw — 유동자산 - 유동부채)
    - **순운전자본** (nwc_krw — 매출채권 + 재고자산 - 매입채무, 영업 묶인 자본)
    - **NWC 변동** (nwc_change_yoy_krw — 전년 대비, CFO 감소 요인)
    - **NWC / 매출** (nwc_to_revenue_pct — 효율성, 낮을수록 좋음)
    - **현금전환주기** (ccc_days — 매출채권회전일 + 재고회전일 - 매입채무회전일, optional Phase 2 확장)
  - **회계 risk 지표** (분식회계 신호 — Marco 시나리오):
    - **영업이익 vs 영업CF 괴리** (accruals_gap_pct — (영업이익 - 영업CF) / 영업이익)
    - **매출채권 / 매출 비율** (ar_to_revenue_pct — push sales 신호)
    - **재고자산 / 매출 비율** (inv_to_revenue_pct — 재고 누적 신호)
  - **배당 / 유보**:
    - 배당성향 (payout_ratio_pct — 배당총액 / 지배주주 귀속 순이익, 분모 0/음수 graceful)
    - **이익잉여금** (retained_earnings_krw — 사내유보)
  - **NAV / 주식**:
    - **NAV 총액** (nav_krw — 자기자본 = 자산총계 - 부채총계 = 순자산가치)
    - **BPS** (bps_krw — NAV / 발행주식수, 주당순자산)
    - **희석 EPS** (diluted_eps_krw — 잠재희석 포함 EPS, CB/BW 행사 가정)
  - **지배구조 cross-check**:
    - **종속회사 수** (subsidiary_count — 지배구조 복잡성 신호)
- [ ] `yoy_signals` alerts (자동 detect):
  - **수익성**:
    - `loss_conversion`: 전년 흑자 → 당기 적자
    - `operating_loss`: 영업손실
    - `turnaround`: 전년 적자 → 당기 흑자 (SK하이닉스 패턴)
    - `continued_loss`: 2년 이상 적자
    - `revenue_decline`: 매출 30%+ 감소
  - **부채 / 유동성**:
    - `debt_surge`: 부채 30%+ 증가
    - `interest_coverage_low`: 이자보상배율 <2배 (부채 위험)
  - **현금흐름** (코리아 디스카운트 + cash quality):
    - `cfo_quality_red`: CFO / 영업이익 < 0.7 (분식 신호 강화)
    - `negative_fcf`: FCF 음수 (현금 부족 — 배당/자사주 capacity X)
    - `low_dividend_capacity_use`: 배당 / FCF < 20% (FCF 충분한데 배당 적음 — **코리아 디스카운트 신호**)
  - **운전자본**:
    - `nwc_surge`: NWC 전년 대비 30%+ 증가 (CFO 압박, 영업 운전 자본 묶임)
    - `nwc_efficiency_low`: NWC / 매출 25%+ (효율 낮음, 동종 평균 대비)
  - **듀퐁 분해** (ROE 구성):
    - `roe_driven_by_leverage`: 재무레버리지 비중 50%+ (ROE가 부채에 의존 — risk)
    - `roe_decline_margin_driven`: ROE 전년 대비 하락 + 순이익률 주요 요인 (수익성 악화)
    - `roe_decline_turnover_driven`: ROE 하락 + 자산회전율 주요 요인 (효율 악화)
  - **회계 risk** (분식 신호):
    - `accruals_red`: 영업이익 - 영업CF 괴리 30%+ (분식 신호)
    - `receivables_surge`: 매출채권 / 매출 비율 30%+ 급증
    - `inventory_surge`: 재고자산 / 매출 비율 30%+ 급증
  - **감사의견** (audit_opinion scope 결과 기반):
    - `non_clean_audit_opinion`: 적정 외 (한정/부적정/의견거절) 트리거
    - `audit_opinion_change`: 적정 → 한정/부적정 등급 하락
  - **배당**:
    - `dividend_halt`: 전년 배당 있었으나 당기 0
- [ ] `no_filing/filing_count/parsed_count/parsing_failures` 메타 (T14 패턴)
- [ ] `data.usage` 표준 (`build_usage` 헬퍼 사용)
- [ ] `evidence_refs[]` 필수 (rcept_no + report_nm + viewer_url + section)

### 안정성 (분모 0 / None / 음수 graceful)
- [ ] 적자 회사 ROE 계산: 분모 (자본총계) 0 또는 음수 시 → None + warning
- [ ] 배당성향: 적자 회사면 None + "분모 음수 — 산출 불가" warning
- [ ] DPS = 0인데 배당총액 있는 경우 처리
- [ ] DART 응답에 일부 필드 missing 시 partial 분류 + warning
- [ ] reprt_code (사업/반기/분기) 다양화 fallback (T18 패턴 — 사업 → 분기 → 반기)

### Sanity Test (6 회사 end-to-end + audit_opinion 검증)
- [ ] **삼성전자** (KOSPI 005930, 대형 흑자 안정) — 모든 scope 정상 + audit_opinion 5년 적정 검증
- [ ] **KT&G** (KOSPI 033780, 배당주) — 배당성향 25% 안팎, 이익잉여금 + ROIC 검증
- [ ] **롯데케미칼** (KOSPI 011170, 대형 적자) — `loss_conversion`/`operating_loss` alert + `interest_coverage_low` 검증
- [ ] **SK하이닉스** (KOSPI 000660, 적자→대형 흑자 turnaround) — `turnaround` alert + yoy 강력 변화 + 듀퐁 분해
- [ ] **삼천당제약** (KOSDAQ 000250, 2026.03 미국 1억달러 계약 부풀리기 의혹 + 깜깜이 공시) — `receivables_surge`/`accruals_red` 검증 (push sales 신호 자동 detect 기대) + KOSDAQ 자율공시 영역 + audit_opinion 추적
- [ ] **오스템임플란트** (KOSDAQ 048260, 2022 횡령 2,000억 + 5년 전 분식회계 history) — audit_opinion 5년 추이 (`audit_opinion_change` 또는 history red flag) + Marco 시나리오 적용 가능 검증 (분식·횡령 시점에 사외이사였던 후보 식별 시 red flag)
- [ ] 각 회사 응답 시간 측정 (목표 5초 이내)
- [ ] 각 회사 evidence_refs 1+ (DART rcept_no 정확)

### Regression 0
- [ ] 기존 17 tool 중 1 회사 (예: 삼성전자) 호출 → schema 동일 (회귀 X)
- [ ] proxy_guideline scope 7개 sanity (정적 데이터 read는 영향 없어야 함)
- [ ] vote_brief 호출 정상 (financial_metrics 미통합 상태 — Phase 2 작업)

### Wiki + 문서
- [ ] `wiki/tools/financial_metrics.md` — 12 섹션 + Flow (mermaid sequenceDiagram) schema 통일
  - [ ] frontmatter `created: 2026-05-01`, `domain: data`
  - [ ] 입력 인자 / 출력 schema / data sources / 파싱 전략 / 알려진 issue
  - [ ] 관련 disclosures: `사업보고서`, `반기보고서`, `분기보고서`
  - [ ] 관련 concepts: `당기순이익`, `배당성향`, `자본준비금`, `소진율`
- [ ] `wiki/architecture/audits/{yymmdd_hhmm}_audit_financial_metrics-5기업.md` — sanity test 결과 (5 회사 응답 표 + alert 정확성)
- [ ] `wiki/index.md` update (Quick Start 17 → 18 tool)
- [ ] `wiki/tools/README.md` update (18 tool 카탈로그)
- [ ] `README.md` + `README_ENG.md` (17 tools → 18 tools, badge + Tool Structure + 도메인 표)

---

## 품질 강화 (안정성 > 속도)

- DART 응답 schema 변경 risk → `try/except` + warnings, no_filing fallback 정확
- DART API 호출 신중 — rate limit (1,000/min) 고려, 3 endpoint × 5 회사 = 15회 안팎이라 여유 있음
- 한국 표준 100% 준수:
  - 배당성향 = 배당총액 / **연결 지배주주 귀속 당기순이익** (별도 X, 비지배 X)
  - DART 공식 시가배당률 사용 (자체 계산 X)
  - 연결 default, separate 옵션
- 가짜 데이터 절대 X — DART 응답 실제 그대로 + 파싱 실패 명확 표시
- 명명 규칙 준수:
  - tool 페이지 = `financial_metrics.md` (정체성)
  - audit 페이지 = `260501_HHMM_audit_financial_metrics-5기업.md` (시점 prefix)

---

## 종료 조건

### ✅ 모든 성공 기준 충족
1. 위 모든 체크박스 ✅
2. `git status` clean (모두 commit + push)
3. fly.io 자동 배포 진행 확인 (push 직후)
4. 마지막 commit 메시지에 phase 1 완료 명시
5. **`<promise>FINANCIAL_METRICS_PHASE1_DONE</promise>` 출력 → ralph 종료**

### ⚠️ 막힘 발생 시
- DART API 응답 schema 미지원 영역 발견 (예: 특정 회사만 다른 구조)
- 한국 표준 정합 어려움 (예: KOSDAQ 자율공시 영역 처리 불명확)
- regression 발생 (다른 tool 영향)
- → **promise 출력 X**, 사용자에게 `## STATUS REPORT` 섹션으로 막힘 영역 + 결정 요청 (현재 iteration 끝)

---

## 반복 단위 (작은 step)

좋은 1 iteration 단위 예시:
- "DART client에 fnlttSinglAcnt 추가 + 1 회사 호출 검증"
- "summary scope 작성 + 삼성전자 sanity"
- "yoy_signals alerts 4개 추가 + 롯데케미칼 검증"
- "tool render markdown 작성"
- "wiki tools/financial_metrics.md 작성"

너무 큰 step (예: "전체 코드 + wiki 한 번에") 금지. 검증 가능한 단위로 쪼갬.

---

## 참고 — 기존 OPM 패턴

- `services/dividend_v2.py` — 6 scope tool 참고 (가장 비슷한 도메인)
- `services/contracts.py` — `AnalysisStatus`, `build_usage`, `EvidenceRef`, `ToolEnvelope`
- `dart/client.py` — DART API 호출 패턴 + rate limiter + key fallback
- `tools_v2/dividend.py` — render 패턴 (markdown + format=json)

dart-mcp (`https://github.com/2geonhyup/dart-mcp.git`) — endpoint URL + 파라미터 매핑 reference만, 흡수 X.

---

## 명명 + frontmatter 규칙

- tool 페이지: `tools/financial_metrics.md` (정체성)
- audit 페이지: `architecture/audits/{yymmdd_hhmm}_audit_financial_metrics-N기업.md`
- 이 ralph 파일: `wiki/ralph/260501_1547_ralph_financial-metrics-phase1.md` (이미 정확)
- 모든 신규 페이지 `created: 2026-05-01`
