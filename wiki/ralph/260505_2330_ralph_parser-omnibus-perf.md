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
/ralph-loop:ralph-loop wiki/ralph/260505_2330_ralph_parser-omnibus-perf.md 가이드 따라 모든 파서 성능 측정 강화 + scope 정합 점검 + 추가 또는 폐지 가능한 scope 발견. 시간 리소스 적게 각 iter spot 위주 batch 최대 30 회사. 모든 active 파서 G1 95 퍼센트 이상 또는 데이터 한계 정직 기록 + scope reorg 명확 결정 + data action layer 정합 검증 시 promise. --completion-promise PARSER_OMNIBUS_VERIFIED --max-iterations 20
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

### Phase 1 — Tier A 파서 audit (iter 1-7)

#### iter 1. `parse_agenda_xml` audit
- 30 회사 spot — 안건 트리 root_count + total_count 정확도
- 안건 number 패턴 다양성 (제N호 의안 / 제N-N호 / etc.) 검증
- summary scope에서 hierarchy 노출 정확도 spot

#### iter 2. `parse_agenda_details_xml` audit
- 30 회사 spot — 안건 detail 추출률 (안건 내 sections + tables)
- 표 head 패턴 다양성 (변경전/현행 등 — precision ralph에서 일부 강화 함)

#### iter 3. `parse_meeting_info_xml` + `parse_corrections_xml` audit
- 30 회사 spot — 회의 정보 (datetime/location/term) + 정정공시 detect

#### iter 4. `parse_aoi_xml` audit
- 30 회사 spot — 정관변경 변경전/후 추출률
- aoi_change scope에 통합된 retirement raw 정확도 spot
- 정관 안에 묶인 비-정관 안건 (퇴직금/보수) detect 정확도

#### iter 5. `parse_personnel_xml` 재검증
- 이전 7 iter ralph 후 89% (데이터 한계). 현재 v2 production에서 동일 수준 유지 확인
- 데이터 한계 archive 정직 기록 유지

#### iter 6. `parse_compensation_xml` audit
- 30 회사 spot — items + summary 채움 (utilization_rate / increase_rate / total_amount)
- 이사/감사 분리 정확도 (precision ralph 검증 결과 활용)

#### iter 7. `parse_provisional_financial_statement` audit
- 260505 1번 작업으로 신규 — 30 회사 spot 검증
- 4 quadrant 추출률 (consolidated/separate × balance/income)
- 단위 / 기간 라벨 / sub-column (current_sub/prior_sub) 패턴 정확도
- extract_metrics flat krw 정확도

### Phase 2 — Tier B v1 dead 결정 (iter 8-9)

#### iter 8. v1 dead parser 사용처 재확인 + archive
- `parse_treasury_share_xml` / `parse_capital_reserve_xml` 잔여 사용처 spot
- v2 services 어디에도 안 쓰면 archive
- v1 tools/shareholder.py에서만 쓰이면 v1 dead로 분류 (production X)

#### iter 9. `parse_financials_xml` parser.py 본체 archive
- services/provisional_financial_statement.py로 이미 이동됨
- v1 tools/shareholder.py가 import하는데 v1 dead → import 정리 또는 그대로

### Phase 3 — scope reorg 발견 (iter 10-15)

#### iter 10. dividend tool scope 검토
- summary / detail / history 3 scope 차이 확인
- `detail`이 `summary`와 raw 중복이면 폐지 검토

#### iter 11. ownership_structure scope 검토
- 5 scope 중 사용 빈도 낮은 것 발견
- raw 중복 check

#### iter 12. financial_metrics scope 검토
- 6 scope (summary / yearly / quarterly / yoy / qoq / audit_opinion) — yearly와 yoy raw 중복 여부

#### iter 13. proxy_contest scope 검토
- 6 scope 중 summary 통합 가능한 것 (사용자 빈도)

#### iter 14. treasury_share scope 검토
- 2 scope (summary / annual) — 이미 단순화. 추가 변경 X 가능성 높음

#### iter 15. corp_gov_report / value_up / 기타 scope 빠른 spot

### Phase 4 — fix + 검증 (iter 16-19)

#### iter 16-18. 발견된 fix 적용 (parser 강화 / scope 폐지 또는 신설)

#### iter 19. 회귀 spot — 변경된 모든 부분 재검증

### Phase 5 — 문서화 + promise (iter 20)

#### iter 20. wiki 정리 (decisions / log / tools 페이지 update) + promise 발행

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

## 가설 / 위험

- **위험 1 (light 제약)**: 20 iter / 작은 batch — 깊은 audit 어려움. 정직하게 spot 위주 + 발견된 issue archive 기록.
- **위험 2 (parse_personnel data limit)**: 이미 89%로 확정된 데이터 한계. 재시도 X — 정직 인정.
- **위험 3 (scope 폐지 후 caller 영향)**: 변경 시 회귀 spot 필수.
- **위험 4 (parser.py 본체 archive)**: v1 tools/shareholder.py import 깨짐. v1 dead라 무방. 단 import 시점 fail 위험 회피 위해 alias 또는 parser.py 잔존 결정 필요.

## archive 폴더

`wiki/architecture/audits/data/260505_parser_omnibus/`

---

## iteration log
(작성하면서 update)

### iter 1 — parse_agenda_xml audit
(작성 예정)

### iter 2 — parse_agenda_details_xml audit
(작성 예정)

### iter 3 — parse_meeting_info_xml + parse_corrections_xml audit
(작성 예정)

### iter 4 — parse_aoi_xml audit
(작성 예정)

### iter 5 — parse_personnel_xml 재검증
(작성 예정)

### iter 6 — parse_compensation_xml audit
(작성 예정)

### iter 7 — parse_provisional_financial_statement audit
(작성 예정)

### iter 8 — v1 dead parser 사용처 재확인 + archive
(작성 예정)

### iter 9 — parse_financials_xml parser.py 본체 archive
(작성 예정)

### iter 10 — dividend scope 검토
(작성 예정)

### iter 11 — ownership_structure scope 검토
(작성 예정)

### iter 12 — financial_metrics scope 검토
(작성 예정)

### iter 13 — proxy_contest scope 검토
(작성 예정)

### iter 14 — treasury_share scope 검토
(작성 예정)

### iter 15 — corp_gov_report / value_up / 기타 빠른 spot
(작성 예정)

### iter 16-18 — fix 적용
(작성 예정)

### iter 19 — 회귀 spot
(작성 예정)

### iter 20 — 문서화 + promise
(작성 예정)
