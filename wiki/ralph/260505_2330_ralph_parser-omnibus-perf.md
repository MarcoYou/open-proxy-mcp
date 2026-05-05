---
type: ralph
title: 파서 omnibus 성능 + scope 정합 점검 (20 iter, light)
created: 2026-05-05 23:30
completion_promise: PARSER_OMNIBUS_VERIFIED
max_iterations: 20
ref:
  - wiki/lessons/scope-simplification.md
  - wiki/lessons/decision-tree-vs-matrix.md
  - feedback_data_action_tool_layers (data tool = parsing+computation, action tool = + decision evidence)
---

## Invoke (복붙)

```
/ralph-loop:ralph-loop wiki/ralph/260505_2330_ralph_parser-omnibus-perf.md 가이드 따라 모든 파서 성능 측정 강화 + scope 정합 점검 + 추가 또는 폐지 가능한 scope 발견. 시간 리소스 적게 (각 iter spot 위주, batch 최대 30 회사). 모든 active 파서 G1 95 퍼센트 이상 또는 데이터 한계 정직 기록 + scope reorg 명확 결정 시 promise. --completion-promise PARSER_OMNIBUS_VERIFIED --max-iterations 20
```

# Ralph: 파서 omnibus 성능 + scope 정합

## Context

shareholder_meeting_notice scope 정리 (full/agenda 폐지 + summary 강화 + aoi_change에 retirement 통합) 완료 후 후속.

이번 ralph 목적:
1. **모든 active 파서 G1 (파싱 성공률) 측정 + 강화** — 데이터 한계는 정직히 기록
2. **추가 가능한 scope 발견** — data tool 원칙 준수 (parsing + computation only, 판단 X)
3. **폐지 가능한 scope 발견** — raw 중복 / 사용 안 됨
4. **v1 dead parser 부활 또는 archive 결정**

**핵심 원칙 (data/action tool layer 분리, 코붕이 의견)**:
- Data tool 파서: parsing + computation (ratios/derived metrics) — 판단 X, raw + 계산만
- Action tool 파서/logic: + decision evidence (proxy_advise 등에서 정책 trigger + 분기 logic)
- 같은 parser를 두 layer가 공유 OK — 책임 분리 명확

## 가정

- No conversation context / no web search / MCP only / deterministic
- 분당 DART 1000회 hard rule
- v2 production (`OPEN_PROXY_TOOLSET=v2`) 기준
- **light**: 각 iter spot 위주 (5-10 회사), batch 최대 30 회사 (rate-safe)
- 20 iter / 의미 있는 변경마다 commit

## 매 iteration 작업
1. 현황: git status + 직전 spot
2. 다음 1 step만 (작게 쪼갬)
3. fix 검증: 회귀 spot
4. commit (의미 있는 변경마다)
5. 다음 iter 1줄

---

## 대상 파서 (audit 우선순위)

### Tier A — 자주 사용 (필수 G1 측정)

| Parser | 사용처 | 마지막 audit | G1 status |
|---|---|---|---|
| `parse_agenda_xml` | shareholder_meeting summary/agenda | 직접 audit X | 미측정 |
| `parse_agenda_details_xml` | shareholder_meeting + retirement chain | 직접 audit X | 미측정 |
| `parse_meeting_info_xml` | shareholder_meeting summary | 직접 audit X | 미측정 |
| `parse_personnel_xml` | director_evaluation + board scope | 260504 7 iter ralph | 89% (data limit 확정) |
| `parse_aoi_xml` | aoi_change scope | 직접 audit X | 미측정 |
| `parse_compensation_xml` | compensation scope + proxy_advise chain | 260505 precision ralph (간접) | 99%+ (proxy_advise 통합 측정) |
| `parse_retirement_pay_xml` | proxy_advise hybrid + (보강 후) aoi_change | 260505 precision ralph | 100% |
| `parse_corrections_xml` | summary correction_summary | 직접 audit X | 미측정 |
| `parse_provisional_financial_statement` (이전 `parse_fy_from_agm_doc`, file `provisional_financial_statement.py`) | proxy_advise (action) | 260504 framework G4 98.6% | 98.6% |

### Tier B — v1 dead (부활/archive 결정)

| Parser | 현 상태 | 부활 가치 |
|---|---|---|
| `parse_financials_xml` | v1 only, 1호 안건 재무제표 raw | **HIGH** — data tool로 부활 (이름 변경 + 보강). financial_metrics와 source 다름 (잠정치 vs 확정치) |
| `parse_treasury_share_xml` | v1 only, 자기주식 안건 본문 | LOW — treasury_share tool이 cover |
| `parse_capital_reserve_xml` | v1 only, 자본준비금 감액 | LOW — articles_amendment에 통합되어 있음 |

### Tier C — services 내 도메인 파서 (별도)

financial_metrics / treasury_share / dividend_v2 / ownership_structure / corp_gov_report 등의 도메인 services는 별도 ralph (이번 범위 X — 이미 충분히 audit됨 또는 별도 작업).

---

## 성공 기준 (모두 충족 시 promise)

### G1. 모든 Tier A 파서 G1 ≥95% (또는 데이터 한계 정직 기록)
KOSPI 200 + KOSDAQ 50 (n=249, lite — 30 회사 batch 위주 spot)에서 각 Tier A 파서가:
- "안건 detect됨" case 중 raw 추출 성공률 ≥95%
- 미달 시 archive에 fail 케이스 보존 + 데이터 한계 audit 작성 (lessons/ralph-threshold-realism 패턴)

### G2. v1 dead parser 처리 결정 명확
- `parse_financials_xml` → data tool로 부활 OR archive (코붕이 결정 + 검증)
- `parse_treasury_share_xml` / `parse_capital_reserve_xml` → archive 또는 잔여 사용처 확인

### G3. scope 추가/폐지 결정
이번 ralph 범위에서 발견한 scope 후보:
- 추가: `parse_financials_xml` 부활 시 새 scope (예: `fy_summary` 또는 financial_metrics 통합)
- 폐지: 미사용 scope 발견 시 (예: dividend의 `detail`이 `summary`와 중복인지)
각 결정에 근거 (사용 빈도 / data tool 원칙 정합 / raw 중복) 명시.

### G4. data/action tool layer 분리 정합
- Data tool 파서가 결정 logic 포함하지 않음 (raw + computation만)
- Action tool에 사용되는 파서는 decision evidence layer 명시
- 파서 신설/이름 변경 시 어느 layer에 들어가는지 명확

---

## 작업 plan (20 iter, light 단위)

### Phase 1 — Tier A 파서 audit (iter 1-7)

#### iter 1. `parse_agenda_xml` audit
- 30 회사 spot — 안건 트리 root_count + total_count 정확도
- 안건 number 패턴 다양성 (제N호 의안 / 제N-N호 / etc.) 검증
- fail 케이스 archive

#### iter 2. `parse_agenda_details_xml` audit
- 30 회사 spot — 안건 detail 추출률 (안건 내 sections + tables)
- 표 head 패턴 다양성 (변경전/현행 등 — precision ralph에서 일부 강화 함)

#### iter 3. `parse_meeting_info_xml` + `parse_corrections_xml` audit
- 30 회사 spot — 회의 정보 (datetime/location/term) + 정정공시 detect

#### iter 4. `parse_aoi_xml` audit
- 30 회사 spot — 정관변경 변경전/후 추출률
- 정관 안에 묶인 비-정관 안건 (퇴직금/보수) detect 정확도

#### iter 5. `parse_personnel_xml` 재검증
- 이전 7 iter ralph 후 89% (데이터 한계). 현재 v2 production에서 동일 수준 유지 확인.

#### iter 6. `parse_compensation_xml` audit
- 30 회사 spot — items + summary 채움 (utilization_rate / increase_rate / total_amount)
- 이사/감사 분리 정확도

#### iter 7. `parse_provisional_financial_statement` (이름 변경) audit
- 1호 안건 본문 정량 추출률 — proxy_advise framework iter1~8에서 98.6%
- 실패 case 분석 + 강화

### Phase 2 — Tier B v1 dead parser 결정 (iter 8-10)

#### iter 8. `parse_financials_xml` 부활 + 이름 변경
- 이름 옵션: `parse_agm_balance_income` 또는 `parse_agm_fs_tables` 등
- data tool로 위치 (판단 X)
- spot 5-10 회사 — 4 quadrant (consolidated/separate × balance_sheet/income_statement) 추출률

#### iter 9. v1 dead parser archive 또는 부활
- `parse_treasury_share_xml`, `parse_capital_reserve_xml` 사용처 재확인
- treasury_share tool / articles_amendment에 통합되어 있으면 archive

#### iter 10. 부활 parser → scope 또는 financial_metrics 통합 결정
- `parse_agm_fs_tables` (가칭) → shareholder_meeting_notice의 새 scope `fy_tables` 신설 OR financial_metrics에 새 source로 통합
- data tool 원칙 (판단 X) 준수

### Phase 3 — scope reorg 발견 (iter 11-15)

#### iter 11. dividend tool scope 검토
- summary / detail / history 3 scope 차이 확인
- `detail`이 `summary`와 raw 중복이면 폐지 검토

#### iter 12. ownership_structure scope 검토
- 5 scope 중 사용 빈도 낮은 것 발견
- raw 중복 check

#### iter 13. financial_metrics scope 검토
- 6 scope (summary / yearly / quarterly / yoy / qoq / audit_opinion) — yearly와 yoy raw 중복 여부

#### iter 14. proxy_contest scope 검토
- 6 scope 중 summary 통합 가능한 것 (사용자 빈도)

#### iter 15. corp_gov_report / value_up / 기타 scope 빠른 spot

### Phase 4 — fix + 검증 (iter 16-19)

#### iter 16-18. 발견된 fix 적용 (parser 강화 / scope 폐지 또는 신설)

#### iter 19. 회귀 spot — 변경된 모든 부분 재검증

### Phase 5 — 문서화 + promise (iter 20)

#### iter 20. wiki 정리 (decisions / log / tools 페이지 update) + promise 발행

---

## 영향 범위

- `open_proxy_mcp/tools/parser.py` — Tier A 파서 강화
- `open_proxy_mcp/services/agm_first_agenda_fy.py` → `services/provisional_financial_statement.py` 이름 변경 (함수: `parse_provisional_financial_statement(text)`)
- `open_proxy_mcp/services/financial_metrics.py` 또는 `open_proxy_mcp/services/agm_fs_tables.py` (NEW) — parse_financials_xml 부활 시
- `open_proxy_mcp/tools_v2/*.py` — scope param 변경 (폐지/신설)
- `wiki/lessons/` — 새 lesson 추가 (parser layer / scope 정리 결과)
- `wiki/architecture/audits/data/260505_parser_omnibus/` — 검증 데이터

## 비목표 (이번 ralph X)

- 도메인 services 깊이 audit (financial_metrics / treasury_share 등 — 별도 ralph)
- 새 tool 추가 (audit_fee_disclosure / esg_disclosure 등)
- 운용사 majority cache normalize
- KOSDAQ universe 확장

## 가설 / 위험

- **위험 1 (light 제약)**: 20 iter / 작은 batch — 깊은 audit 어려움. 정직하게 spot 위주 + 발견된 issue archive 기록.
- **위험 2 (parse_personnel data limit)**: 이미 89%로 확정된 데이터 한계. 재시도 X — 정직 인정.
- **위험 3 (scope 폐지 후 caller 영향)**: 변경 시 회귀 spot 필수.
- **위험 4 (parse_financials 부활 시 financial_metrics와 충돌)**: 둘 다 노출 시 사용자 헷갈림. source 차이 (잠정 vs 확정) 명시.

## archive 폴더

`wiki/architecture/audits/data/260505_parser_omnibus/`

---

## iteration log
(작성하면서 update)

### iter 1 — parse_agenda_xml audit
(작성 예정)

### iter 2 — parse_agenda_details_xml audit
(작성 예정)

(...iter 3-19 작성 예정)

### iter 20 — 문서화 + promise
(작성 예정)
