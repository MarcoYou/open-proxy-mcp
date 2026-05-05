---
type: ralph
title: 파서 omnibus 성능 + scope 정합 점검 (20 iter, light)
created: 2026-05-05 23:30
updated: 2026-05-06 00:30 (1번 2번 작업 완료 후)
completion_promise: PARSER_OMNIBUS_VERIFIED
max_iterations: 20
ref:
  - wiki/lessons/scope-simplification.md
  - wiki/lessons/decision-tree-vs-matrix.md
  - feedback_data_action_tool_layers (data tool = parsing+computation, action tool = + decision evidence)
---

## Invoke (복붙)

```
/ralph-loop:ralph-loop wiki/ralph/260505_2330_ralph_parser-omnibus-perf.md 가이드 따라 모든 파서 성능 측정 강화 + scope 정합 점검 + 추가 또는 폐지 가능한 scope 발견. KOSPI 200 KOSDAQ 100 합계 300 회사 sample 각 iter spot 위주 batch 최대 30 회사. 모든 active 파서 G1 95 퍼센트 이상 또는 데이터 한계 정직 기록 + scope reorg 명확 결정 + data action layer 정합 검증 시 promise. --completion-promise PARSER_OMNIBUS_VERIFIED --max-iterations 20
```

# Ralph: 파서 omnibus 성능 + scope 정합

## Context

선행 작업 완료 (260505_2200 precision + 1번/2번 정리):
- shareholder_meeting_notice scope: 6 → 5 (`summary`/`board`/`compensation`/`aoi_change`/`prov_financials`)
- summary 강화: agenda hierarchy + 1호 안건 메타 (회기/사업연도/배당)
- aoi_change에 retirement raw 통합 (data tool 원칙)
- **prov_financials scope 신설** — 잠정 재무제표 4 quadrant raw (parse_provisional_financial_statement)
- `provisional_financial_statement.py` 독립 모듈 (parser.py 의존성 제거)
- 보수/퇴직 분기 정밀화 (n=226, G1 99-100% / G3 100% / G4 NPS 정합 100%)

이번 ralph 목적:
1. **모든 active 파서 G1 측정 + 강화** — 데이터 한계는 정직히 기록
2. **추가 가능한 scope 발견** — data tool 원칙 (parsing + computation, 판단 X)
3. **폐지 가능한 scope 발견** — raw 중복 / 미사용
4. **v1 dead parser 처리 결정** — 부활 또는 archive
5. **data/action tool layer 정합 점검** — 모든 파서가 어느 layer인지 명확

## 핵심 원칙 (코붕이 2026-05-05)

**Data tool 파서**:
- Parsing (raw 추출) + Computation (ratios / derived metrics / 단위 환산)
- 판단 X — LLM/사용자가 raw 보고 자체 판단
- 예: `parse_personnel_xml` (후보 경력 raw), `parse_compensation_xml` (당기/전기 raw + 인원수), `parse_provisional_financial_statement` (4 quadrant 표), `_compute_metrics` (ROE/부채비율 계산)

**Action tool 파서/logic**:
- + Decision evidence layer (정책 trigger + pre-set logic)
- 예: `_decide_retirement_pay`가 `parse_retirement_pay_xml` 결과 보고 황금낙하산 / 사외이사 퇴직금 / 지급률 2배수+ 등 trigger 분기

같은 parser를 두 layer가 공유 OK — 책임 분리 명확. 새 parser/scope 신설 시 어느 layer인지 먼저 정의.

## 가정

- No conversation context / no web search / MCP only / deterministic
- 분당 DART 1000회 hard rule (rolling cap 900)
- v2 production (`OPEN_PROXY_TOOLSET=v2`) 기준
- **light**: 각 iter spot 위주 (5-10 회사), batch 최대 30 회사 (rate-safe)
- 20 iter / 의미 있는 변경마다 commit
- 데이터 한계 발견 시 archive에 정직히 기록 (lessons/ralph-threshold-realism)

---

## 대상 파서 (audit 우선순위)

### Tier A — Active (현 v2 production 사용)

| Parser | 사용 layer | 사용처 | 마지막 audit | G1 status |
|---|---|---|---|---|
| `parse_agenda_xml` | data | shareholder_meeting summary (안건 트리) | 직접 audit X | 미측정 |
| `parse_agenda_details_xml` | data | shareholder_meeting + retirement chain | 직접 audit X | 미측정 |
| `parse_meeting_info_xml` | data | shareholder_meeting summary (회의 정보) | 직접 audit X | 미측정 |
| `parse_personnel_xml` | data + action | director_evaluation + board scope | 260504 7 iter | 89% (data limit 확정) |
| `parse_aoi_xml` | data | aoi_change scope (정관변경) | 직접 audit X | 미측정 |
| `parse_compensation_xml` | data + action | compensation scope + proxy_advise chain | 260505 precision (간접) | 99%+ |
| `parse_retirement_pay_xml` | data + action | aoi_change (raw) + proxy_advise (decision) | 260505 precision | 100% |
| `parse_corrections_xml` | data | summary correction_summary | 직접 audit X | 미측정 |
| `parse_provisional_financial_statement` | data + action | prov_financials scope + proxy_advise facts | 260505 1번 작업 (1 sample) | 미측정 (n=1) |

### Tier B — v1 dead (부활/archive 결정 필요)

| Parser | 현 상태 | 부활 가치 |
|---|---|---|
| `parse_treasury_share_xml` | tools/parser.py:3508 (v1 only) | 검증 필요 — treasury_share tool이 cover하면 archive |
| `parse_capital_reserve_xml` | tools/parser.py:3593 (v1 only) | 검증 필요 — articles_amendment에 통합되어 있는지 확인 |
| `parse_financials_xml` | tools/parser.py:2626 (v1 only) | **이미 부활** — services/provisional_financial_statement.py로 이동 (260505 1번). parser.py 본체 archive 검토 |

### Tier C — services 내 도메인 파서 (별도)

financial_metrics / treasury_share / dividend_v2 / ownership_structure / corp_gov_report 등의 도메인 services는 별도 ralph (이번 범위 X — 이미 충분히 audit됨 또는 별도 작업).

---

## 성공 기준 (모두 충족 시 promise)

### G1. 모든 Tier A 파서 G1 ≥95% (또는 데이터 한계 정직 기록)
KOSPI 200 + KOSDAQ 50 (light — 30 회사 batch + spot)에서 각 Tier A 파서가:
- "안건 detect됨" case 중 raw 추출 성공률 ≥95%
- 미달 시 archive에 fail 케이스 보존 + 데이터 한계 audit 작성

### G2. v1 dead parser 처리 결정 명확
- `parse_treasury_share_xml` → archive 또는 잔여 사용처 확인
- `parse_capital_reserve_xml` → archive 또는 articles_amendment 통합 검증
- `parse_financials_xml` (parser.py 본체) → archive (이미 services로 이동됨)

### G3. scope 추가/폐지 결정
이번 ralph 범위에서 발견한 scope 후보:
- 폐지: 미사용 scope 발견 시 (예: dividend `detail`이 `summary`와 raw 중복인지)
- 추가: 새 raw 노출 가치 발견 시 (data tool 원칙 준수)
각 결정에 근거 (사용 빈도 / data tool 원칙 정합 / raw 중복) 명시.

### G4. data/action tool layer 정합 검증
각 파서가 어느 layer인지 확인:
- 모든 data tool 파서: 판단 X (decision logic 포함하지 않음)
- 모든 action tool 사용 파서: data tool helper + 별도 decision layer 분리
- 위반 케이스 발견 시 fix

---

## 작업 plan (20 iter, light 단위)

### Phase 0 — Universe 확장 ✓ (사전 완료, dataguide 활용)

KOSDAQ 현재 top 50만 있음 → 샘플 부족. **dataguide xlsx (esgQuant 프로젝트)에서 시총 내림차순 ticker list 직접 추출** (코붕이 source). DART/KIND 호출 0.

생성된 universe (preprocessing 완료):
- `260506_universe_kosdaq_150.csv` (KOSDAQ top 150)
- `260506_universe_kosdaq_300.csv` (KOSDAQ top 300, 옵션)
- `260506_universe_kospi_200.csv` (KOSPI top 200)

source: `/Users/marcoyou/Projects/esgQuant/input/financial/2026-03-31/멀티인덱스_dataguide.xlsx` 멀티인덱스 시트, 시총 내림차순. KOSPI 810 + KOSDAQ 1816 회사 식별 가능.

### Phase 1 — Tier A 파서 통합 audit (iter 1-5, sample = KOSPI 200 + KOSDAQ 100)

**핵심**: 30 회사 spot batch로 8+ parser 모두 audit (master script). 코붕이 결정 sample = **KOSPI 200 + KOSDAQ 100 = 300 회사**.

universe:
- `260506_universe_kospi_200.csv` (KOSPI top 200)
- `260506_universe_kosdaq_100.csv` (KOSDAQ top 100)

#### iter 1. Master spot script 작성 + 첫 KOSPI batch (0-30)
- `scripts/spot_parser_omnibus.py` — 1 회사당 1 doc fetch + 8 parser 호출 (in-memory) + G1 metrics 일괄
- 첫 30 회사 batch (KOSPI 0-30) — ~90 DART calls
- 결과: `wiki/architecture/audits/data/260505_parser_omnibus/iter01_kospi_0-30.json`

#### iter 2. KOSPI batch chain 30-90 (2 batch × 30 + sleep)
- KOSPI 30-60 / 60-90 — 60 회사 / ~180 calls / sleep 30s 분산

#### iter 3. KOSPI batch chain 90-150 (2 batch × 30 + sleep)
- KOSPI 90-120 / 120-150 — 60 회사 / ~180 calls

#### iter 4. KOSPI 150-200 + KOSDAQ 0-30 batch chain
- KOSPI 150-180 / 180-200 (50 회사) + KOSDAQ 0-30 = 80 회사 / ~240 calls

#### iter 5. KOSDAQ 30-100 batch chain (3 batch × 30, 마지막 ~10)
- KOSDAQ 30-60 / 60-90 / 90-100 = 70 회사 / ~210 calls
- 누적 통합: KOSPI 200 + KOSDAQ 100 = 300 회사 완료

### Phase 1 결과 분석 (iter 6, DART X)
- 300 회사 통합 G1 — 8 parser 동시 측정
- KOSPI vs KOSDAQ 분포 차이 비교
- fail case archive
- parse_personnel_xml: 89% data limit 재확인 (재시도 X)

### Phase 2 — Tier B v1 dead 결정 (iter 7-8, **DART 호출 0**)

#### iter 7. v1 dead parser 사용처 재확인 (정적 grep — DART X)
- `parse_treasury_share_xml` / `parse_capital_reserve_xml` 잔여 사용처 grep
- v2 services 어디에도 안 쓰면 archive 결정
- v1 tools/shareholder.py에서만 쓰이면 v1 dead로 분류

#### iter 8. `parse_financials_xml` parser.py 본체 archive (정적 — DART X)
- services로 이미 이동됨 — parser.py 본체 + 의존 helper들 archive
- v1 tools/shareholder.py import 정리

### Phase 3 — scope reorg 발견 (iter 9-13, 대부분 정적 분석 + 소량 spot)

대부분 코드 정적 분석 (DART X). 필요한 경우만 5 회사 spot (각 ~15 calls).

#### iter 9. dividend + ownership_structure scope 검토 (정적)
- dividend summary / detail / history 3 scope 차이
- ownership_structure 5 scope raw 중복 check

#### iter 10. financial_metrics scope 검토 (정적 + 5 회사 spot if needed)
- 6 scope (summary / yearly / quarterly / yoy / qoq / audit_opinion) raw 중복

#### iter 11. proxy_contest + treasury_share + corp_gov_report + value_up scope 빠른 정적

#### iter 12. layer 정합 검증 (G4) — 정적 분석
- 모든 data tool 파서가 decision logic 포함하지 않는지 grep + review
- action tool 사용 파서가 data helper + 별도 decision layer로 분리되어 있는지 확인

#### iter 13. 수집된 fix list 정리 + 우선순위
- Phase 4 작업 목록 확정

### Phase 4 — fix + 검증 (iter 14-18)

#### iter 14-16. 발견된 fix 적용 (parser 강화 / scope 폐지 또는 신설)
- 대부분 코드 변경 + smoke test (5 회사 spot, ~15 calls)

#### iter 17-18. 회귀 spot (KOSPI 30 + KOSDAQ 30 = 60 회사)
- 변경된 모든 부분 재검증
- ~180 calls

### Phase 5 — 문서화 + promise (iter 19-20)

#### iter 19-20. wiki 정리 (decisions / log / tools 페이지 update) + promise 발행 (DART X)

---

## 총 DART 호출 추정 (KOSPI 200 + KOSDAQ 100 = 300 회사)

| Phase | iter | DART calls 추정 |
|---|---|---|
| Phase 0 (universe 사전 완료) | — | 0 (dataguide xlsx 사용) |
| Phase 1 (parser audit, 300 회사) | 1-6 | ~900 calls (300 × 3) |
| Phase 2 (v1 dead 정적) | 7-8 | 0 |
| Phase 3 (scope reorg, 대부분 정적) | 9-13 | ~75 calls (5 회사 × 5 spot) |
| Phase 4 (fix + 회귀 60 회사) | 14-18 | ~225 calls |
| Phase 5 (문서화) | 19-20 | 0 |
| **총합** | 20 iter | **~1200 calls** (DART) |

→ **분산 시간 (iter간 stop hook gap)** 으로 분당 cap 900 미달 ✓
→ Phase 1 단일 batch 30 회사 ~90 calls / 6-12분 분산 → cap 900 micro 단위도 안전

---

## 영향 범위

- `open_proxy_mcp/tools/parser.py` — Tier A 파서 강화 + Tier B archive 검토
- `open_proxy_mcp/services/*.py` — services 내 파서 영향 시
- `open_proxy_mcp/tools_v2/*.py` — scope param 변경 (폐지/신설)
- `wiki/lessons/` — 새 lesson 추가 (parser layer / scope 정리 결과)
- `wiki/decisions/` — 결정 문서
- `wiki/architecture/audits/data/260505_parser_omnibus/` — 검증 데이터

## 비목표 (이번 ralph X)

- 도메인 services 깊이 audit (financial_metrics / treasury_share / ownership_structure 등 — 별도 ralph)
- 새 tool 추가 (audit_fee_disclosure / esg_disclosure 등)
- 운용사 majority cache normalize
- KOSDAQ universe 확장
- proxy_advise (action tool) decision logic 변경

## Rate limit 설계 (DART 분당 1000회 hard rule)

**design 핵심**: parser audit은 같은 AGM notice doc에서 여러 parser 호출 가능 → **1 회사당 1 doc fetch + N parser** (in-memory). 호출 수 폭증 X.

### Per-iter budget
- batch: 최대 30 회사 / concurrency 2 / 회사당 ~3 DART call (corp_code → search_filings → get_document)
- 30 회사 × 3 calls = **~90 calls/batch**. concurrency 2면 ~6-12분 분산.
- spot iter: 5-10 회사 = ~30 calls. 매우 안전.
- **단일 iter 최대 90 calls** ≪ cap 900/min ✓

### Master spot script (Tier A 파서 통합 audit)
iter 1-7을 7 batch (= 7 × 90 calls)로 돌리는 대신, **1 master batch로 통합**:
- 30 회사 spot 1회 → 각 회사의 AGM notice doc 1회 fetch
- 같은 html을 7 parser (`parse_agenda_xml` / `parse_agenda_details_xml` / `parse_meeting_info_xml` / `parse_personnel_xml` / `parse_aoi_xml` / `parse_compensation_xml` / `parse_provisional_financial_statement` / `parse_corrections_xml` / `parse_retirement_pay_xml`)에 모두 호출
- 결과 dict로 모은 다음 G1 metrics 일괄 계산

**호출 수**: 30 회사 × 3 calls = **90 calls** (parser 추가는 in-memory, DART 호출 X). iter 1 → 7 통합 가능.

### Cross-iter 안전
- 각 iter는 stop hook gap (수 분 ~ 수십 분)으로 분리 — DART rolling window 자연 reset
- 누적 영향 X (process 분리)

### Sequential batch 원칙
- 다중 batch chain 시 30s sleep 명시
- `client.py`의 rolling cap 900 hard guard 자동 throttle 신뢰

### 위험: 동시 다중 ralph
- 다른 ralph 또는 사용자 호출과 **동시에 실행 시 cap 초과 위험**
- mitigation: 이 ralph 단독 실행 (다른 ralph 동시 시작 금지)
- 실수 시 client.py의 rolling cap 900이 hard throttle (block 회피)

## 가설 / 위험

- **위험 1 (light 제약)**: 20 iter / 작은 batch — 깊은 audit 어려움. 정직하게 spot 위주 + 발견된 issue archive 기록.
- **위험 2 (parse_personnel data limit)**: 이미 89%로 확정된 데이터 한계. 재시도 X — 정직 인정.
- **위험 3 (scope 폐지 후 caller 영향)**: 변경 시 회귀 spot 필수.
- **위험 4 (parser.py 본체 archive)**: v1 tools/shareholder.py import 깨짐. v1 dead라 무방. 단 import 시점 fail 위험 회피 위해 alias 또는 parser.py 잔존 결정 필요.
- **위험 5 (rate limit)**: 위 design 따라 단일 iter ≤90 calls. 동시 다중 ralph 금지. client.py rolling cap 900 hard throttle 신뢰.

## archive 폴더

`wiki/architecture/audits/data/260505_parser_omnibus/`

---

## iteration log
(작성하면서 update)

### iter 1 — Master spot script 작성 + KOSPI 0-30 batch ✓
- `scripts/spot_parser_omnibus.py` (1 doc fetch + 9 parser in-memory)
- 2 batch × 15 회사 (concurrency 2, sleep 1s) — KOSPI 0-15 + 15-30
- 30/30 OK / 총 ~60 DART calls / 분산 ~1분 (cap 900/min 안전)
- G1 (n=30 OK): meeting_info / agenda / agenda_details / corrections / personnel / aoi / compensation / retirement_pay 모두 ≥99%
- provisional_fs metric extraction G1 19/22 = 86.4% — 현대차/두산에너빌리티/셀트리온 sparse (rows 충분, extract_metrics 매핑 미스 — Phase 4 fix 후보)

### iter 2 — KOSPI 30-90 batch chain ✓
- 4 batch × 15 회사 (30-45 / 45-60 / 60-75 / 75-90, sleep 10s 각 batch 사이)
- 59/60 OK (1 no_corp 0126Z0 — 비표준 alphanumeric ticker `삼성에피스홀딩스`)
- 누적 KOSPI 0-90 = 89/90 OK / Tier A 모두 ≥99% G1
- PFS metric extraction: 33/41 = 80.5%
- Parser fix 후보 발견:
  - 금융지주/은행 doc 구조 차이 (기업은행/한국금융지주 — aoi_empty + comp_empty)
  - HD건설기계 — 정관변경 detect됐지만 amendments 0
  - PFS extract_metrics 매핑 미스 8 cases (Phase 4)

### iter 3 — KOSPI 90-150 batch chain ✓
- 4 batch × 15 회사 / 90-105 / 105-120 / 120-135 / 135-150
- 59/60 OK (1 search_error 신영증권 001720 = DART code 013 "조회 데이터 없음" — 2026 AGM 미공고, data limit honest)
- 누적 KOSPI 0-150 = 148/150 OK (98.7%)
- PFS metric extraction 평균: 70/81 = 86.4%
- 한솔케미칼 014680 — `[IMAGE_NOTICE]` 소집통지서 이미지만 첨부, html 본문 X (data limit, parser 정상)

### iter 4 — KOSPI 150-200 + KOSDAQ 0-30 ✓
- 5 batch (KOSPI 150-165 / 165-180 / 180-200 / KOSDAQ 0-15 / 15-30)
- 79/80 OK (1 no_corp 에임드바이오 0009K0 비표준 ticker)
- 누적 KOSPI 0-200 = 198/200 / KOSDAQ 0-30 = 29/30
- KOSDAQ batch PFS 25/25 = 100% — KOSDAQ doc 구조가 KOSPI보다 표준화 우수
- 호텔신라 008770 — 안건 영역(회의목적사항/결의사항/부의안건) 미발견 (doc 구조 차이, 1건)

### iter 5 — KOSDAQ 30-100 batch chain ✓
- 5 batch (30-45 / 45-60 / 60-75 / 75-90 / 90-100)
- 70/70 OK = 100%
- PFS extraction 49/52 = 94.2% (KOSDAQ 우수)
- Phase 1 종료: **297/300 회사 OK (99.0%)**
  - KOSPI 0-200 = 198/200 (no_corp 1 + search_error 신영증권 1)
  - KOSDAQ 0-100 = 99/100 (no_corp 1)

### iter 6 — Phase 1 통합 G1 + PFS sparse root cause (DART X) ✓
- `scripts/agg_parser_omnibus.py` (11 batch JSON 통합)
- `scripts/spot_pfs_debug.py` (1 회사 doc + parsed table raw 확인)

G1 결과 (n=297 OK):
| Parser | n | ok | pct | 비고 |
|---|---|---|---|---|
| meeting_info | 297 | 297 | **100%** | ✓ |
| agenda | 297 | 296 | **99.7%** | ✓ (호텔신라 1건 doc 구조) |
| agenda_details | 297 | 297 | **100%** | ✓ |
| corrections | 297 | 297 | **100%** | ✓ |
| personnel(director) | 234 | 233 | **99.6%** | ✓ |
| personnel(audit) | 221 | 220 | **99.5%** | ✓ |
| aoi | 252 | 248 | **98.4%** | ✓ |
| compensation | 279 | 275 | **98.6%** | ✓ |
| retirement(call) | 30 | 30 | **100%** | ✓ |
| pfs(call) | 215 | 215 | **100%** | ✓ |
| **pfs metrics ≥6** | 215 | 196 | **91.2%** | ✗ 95% 미달 |
| pfs metrics ≥4 | 215 | 198 | 92.1% | |

KOSPI 88.4% / KOSDAQ 96.1% — KOSDAQ doc 표준화 우수.

PFS 19 sparse 케이스 spot debug (현대차 005380 / 셀트리온 068270) 근본 원인:
1. **Disclosure data anomaly**: 잠정 재무제표 IS의 매출액/영업이익/당기순이익 summary 라인이 빈 값. sub-line (지배지분/비지배지분/금융수익 등)에만 값.
2. **Parser table classification 버그**: 셀트리온 separate.balance_sheet에 종속기업 목록 24개를 오분류.

Fix 방향 (Phase 4):
- (A) `_METRIC_KEYWORDS`에 `지배기업소유주지분` 추가 (분리 보고 기업 대응)
- (B) Table 본문 검증 강화 (account 컬럼에 영문/주석 다수면 FS 아님으로 reject)
- (C) 또는 honest data limit 인정 + 91.2% 기록

### iter 7 — v1 dead parser 사용처 정적 분석 + archive 결정 (DART X) ✓
- 3개 v1 dead parser 모두 v2 production 미사용 (grep evidence):
  - `parse_treasury_share_xml` — tools/shareholder.py만 호출
  - `parse_capital_reserve_xml` — tools/shareholder.py만 호출
  - `parse_financials_xml` — tools/shareholder.py만 호출 (services/provisional_financial_statement.py 가 본체 흡수 완료)
- production fly.toml `OPEN_PROXY_TOOLSET=v2` 확인
- 결정: **logical archive** (코드 본체 보존 + decision 기록), physical archive 보류
- 이유: parser.py 3974 라인, 다수 helper 가 active/dead parser 공유 — physical archive 시 regression 위험
- decision doc: [[260506_2330_decision_v1-dead-parsers-archive]]

### iter 8 — Phase 2 마무리 + Phase 3 사전 준비 (예정, DART X)
v1 mode 실제 retire 시점에 physical archive — 별도 결정 (이번 ralph 범위 X)
→ 다음 작업: Phase 3 scope reorg 검토 시작

### iter 8-9 — Phase 3 scope reorg 검토 시작 (정적, DART X) ✓ (부분)

**dividend (3 scope)**:
- `summary`: latest_decisions[:20] + policy_signals + meta_signals (선배당후결의 / 감액배당 signals)
- `detail`: latest_decisions[:50] + decision_count + alotMatter vs decisions DPS mismatch warning
- `history`: history(N years) + quarterly_breakdown + policy_signals
- 관찰: detail vs summary `latest_decisions` raw 일부 중복 (20건은 50건의 부분집합). 그러나 detail은 more decisions + warning 추가, summary는 meta_signals 보유 — **각자 다른 derived 정보** → **유지 권고** (raw 완전 중복 X)

**ownership_structure (5 scope)**:
- `summary` / `major_holders` / `blocks` / `control_map` / `changes`
- 이미 cleanup 완료 (treasury → treasury_share tool / timeline → blocks 통합)
- 추가 reorg 불필요 ✓

### iter 10-12 — Phase 3 잔여 tool scope + G4 layer 정합 (정적, DART X) ✓

**모든 v2 production tool scope inventory**:
| Tool | Scopes | 결정 |
|---|---|---|
| company | (single) | 유지 |
| shareholder_meeting_notice | summary/board/compensation/aoi_change/prov_financials | 260506 정리 완료 |
| shareholder_meeting_results | (single) | 유지 |
| ownership_structure | summary/major_holders/blocks/control_map/changes | 이미 cleanup |
| dividend | summary/detail/history | 일부 raw 중복 OK (derived 차이) |
| financial_metrics | summary/yearly/quarterly/yoy/qoq/audit_opinion | 각자 다른 view (yoy/qoq computation) — 유지 |
| treasury_share | summary/annual | minimal — 유지 |
| corp_gov_report | summary/metrics/principles/filings/timeline | 각자 다른 view — 유지 |
| value_up | summary/plan/commitments/timeline | 각자 다른 angle — 유지 |
| corporate_restructuring | (single) | 유지 |
| dilutive_issuance | (single) | 유지 |
| proxy_contest | summary/fight/litigation/signals/timeline/vote_math | 각자 specific aspect — 유지 |
| related_party_transaction | (single) | 유지 |
| evidence | (string only) | 유지 |
| proxy_guideline | (single) | 유지 |
| proxy_advise_before_meeting | (single — 260504 폐지된 9 → 1) | 정리 완료 |
| proxy_result_after_meeting | 3 scope | 유지 |

→ **추가 폐지/신설 결정 없음** (이미 충분히 정리됨)

**G4 layer 정합 검증 결과**:
- `tools/parser.py` (Tier A 9 파서): decision 키워드 grep **0건** ✓ — 순수 parsing/computation
- `services/provisional_financial_statement.py` (data tool helper): **0건** ✓ — 순수 parser
- 14개 data tool services (dividend/ownership/financial_metrics/treasury_share/corp_gov_report/value_up/proxy_contest/shareholder_meeting/RPT/company/filing_search/dilutive_issuance/corporate_restructuring): 모두 **0건** ✓ (false positive 1개 — director_evaluation comment 내 예시 설명)
- `services/proxy_advise.py` (action tool): **8개 `_decide_*` 함수** ✓ — director_election / director_compensation / audit_compensation / retirement_pay / financial_statements / articles_amendment / treasury_share / dividend
- `services/director_evaluation.py` (action-tool internal helper): `evaluate_*` 함수들 — proxy_advise가 사용하는 candidate 평가 로직 (적절한 layer)

→ **G4 PASS**: data tool layer (parsing + computation) vs action tool layer (decision evidence) 정합 명확 ✓

### iter 8 후반 (Phase 4 fix) — PFS extract_metrics 강화 + 회귀 ✓
변경:
- `_METRIC_KEYWORDS.net_income_krw`에 `지배기업소유주지분` 등 4 변형 추가
- `_NON_FS_TABLE_HINTS` 추가 — 영문 사명 ≥6 줄 테이블 reject (종속회사 목록)
- `scope_used` 보고 버그 fix — 실제 추출 scope만 기록

19 sparse 재측정 (`scripts/spot_pfs_sparse_recheck.py` / 17.5s):
- 1/19 추가 PASS (심텍 KOSDAQ — filled 4→6)
- 18/19 여전히 sparse — **disclosure data limit** (잠정 재무제표 summary 라인 빈 값)

PFS G1: 91.2% → 91.6% (한계 도달).

### iter 13 — Phase 4 결정: honest data limit 인정 ✓
- Plan 명시 "G1 ≥95% **또는 데이터 한계 정직 기록**" → 18 sparse 케이스는 정직히 기록
- root cause: 일부 KOSPI 대형 기업의 잠정 재무제표 disclosure 본문에 summary 라인 (매출액/영업이익/당기순이익/자산총계/부채총계/자본총계) 자체가 비어있음
- 해당 케이스: 현대차 / 두산에너빌리티 / 셀트리온 / 현대로템 / 현대건설 / 기업은행 / LG / KT / 대한항공 / LG이노텍 / 유한양행 / 두산밥캣 / 두산로보틱스 / OCI홀딩스 / 현대위아 / HDC / LS마린솔루션 / 현대바이오

### iter 14-16 — fix 적용 + smoke test
(작성 예정)

### iter 17-18 — 회귀 spot (KOSPI 30 + KOSDAQ 30)
(작성 예정)

### iter 19-20 — 문서화 + promise
(작성 예정)
