---
type: decision
title: shareholder_meeting_notice scope 정리 + provisional_financial_statement 독립 + prov_financials scope 신설
date: 2026-05-06 00:30
status: adopted
related:
  - wiki/lessons/scope-simplification.md
  - feedback_data_action_tool_layers (data tool = parsing+computation, action tool = + decision evidence)
  - wiki/decisions/260505_1900_decision_compensation-retirement-split.md
---

# shareholder_meeting_notice scope 정리 + provisional_financial_statement

## 배경

코붕이 review (2026-05-06):
1. `shareholder_meeting_notice` scope 6개 중 `full` (병렬 wrapper) / `agenda` (summary와 중복 가능) 폐지 가능 여부 검토
2. 1호 안건 (재무제표 승인) raw 본문이 사용자 직접 호출에서 노출 안 되는 갭 발견
3. v1 dead `parse_financials_xml` 부활 + 이름 변경 + 독립 모듈 + 새 scope 노출 의견
4. retirement 별도 scope 신설 X (정관 안에 묶이는 경우 대부분 → aoi_change에 통합)
5. result_status / result_reference 사후 정보 — 사전 tool에 노출은 시점 분리 위반

## 결정

### 1. shareholder_meeting_notice scope: 6 → 5

| scope | 변경 |
|---|---|
| `summary` | **강화** — agenda hierarchy + 1호 안건 메타 (회기/사업연도/배당 예정액) 통합 |
| `board` | 유지 |
| `compensation` | 유지 |
| `aoi_change` | **보강** — 정관변경 raw + parse_retirement_pay_xml 결과 통합 |
| `prov_financials` (NEW) | 잠정 재무제표 4 quadrant raw + flat metrics |
| ~~`agenda`~~ | **폐지** (summary에 흡수, silent fallback) |
| ~~`full`~~ | **폐지** (거의 미사용, 종합 분석은 proxy_advise_before_meeting) |

폐지된 scope은 `_DEPRECATED_SCOPES` set에 두고 silent fallback to summary (caller 깨짐 방지).

### 2. result_status / result_reference 제거

사후 정보 — `shareholder_meeting_results` tool 참조. summary에서 "결과 시점" 표 삭제. service에서는 internal logic 유지 (`meeting_phase`만 노출).

### 3. provisional_financial_statement.py 독립 모듈

이전:
- `tools/parser.py:parse_financials_xml` (v1 only, BS4 4 quadrant 표 파싱)
- `services/agm_first_agenda_fy.py:parse_fy_from_agm_doc` (정규식 텍스트 파서, 부정확)

→ **통합** `services/provisional_financial_statement.py`:
- `parse_financials_xml` 본체 + 모든 의존 helper (regex constants / `_infer_statement_type` / `_build_column_meta` / `_normalize_financial_rows` / `_extract_period_labels` / `_extract_unit_from_siblings` / `_empty_financial_result`) 통째로 이동
- `parse_provisional_financial_statement(html)` — 4 quadrant 반환 (consolidated/separate × balance_sheet/income_statement)
- `extract_metrics(parsed)` — flat krw 추출 (proxy_advise facts evidence용)
- BS4/lxml 직접 import — `parser.py` 의존성 0
- 구 `agm_first_agenda_fy.py` archive (`wiki/archive/services/agm_first_agenda_fy_v1_regex.py`)

### 4. data/action tool layer 분리 정합

코붕이 원칙 (2026-05-05):
- Data tool 파서: parsing + computation (단위 환산, ratios) — 판단 X
- Action tool 파서: + decision evidence (정책 trigger + 분기 logic)
- 같은 parser를 두 layer가 공유 OK

`provisional_financial_statement` 사용:
- **Data tool** (`shareholder_meeting_notice` scope=`prov_financials`): 4 quadrant 표 raw + flat metrics 노출 (판단 X)
- **Action tool** (`proxy_advise_before_meeting`): `extract_metrics()` 호출 → `facts` evidence (financial_statements 안건 결정 logic용)

`parse_retirement_pay_xml` 사용 (260505 ralph 후속):
- **Data tool** (`shareholder_meeting_notice` scope=`aoi_change`): 정관변경 + 퇴직금 변경 raw 통합 노출 (판단 X)
- **Action tool** (`proxy_advise_before_meeting`): `_decide_retirement_pay` / `_decide_articles_amendment` 호출 → 황금낙하산 / 사외이사 퇴직금 / 지급률 2배수+ 등 trigger 분기

## 영향 범위

| 파일 | 변경 |
|---|---|
| `services/provisional_financial_statement.py` | NEW (독립 모듈, 의존 helper 통째로 이동) |
| `services/agm_first_agenda_fy.py` | DELETE → archive |
| `services/proxy_advise.py` | import 변경 + facts dict key (12 metrics + scope_used) |
| `services/shareholder_meeting.py` | scope 처리 (`include_prov_financials` + agenda 항상 포함 + retirement 통합) |
| `tools_v2/shareholder_meeting_notice.py` | _NOTICE_SCOPES 5개 + _DEPRECATED_SCOPES + render dispatch |
| `tools_v2/_shareholder_meeting_render.py` | render_summary 강화 + render_aoi 통합 + render_provisional_financials 신규 + render_full_notice/render_agenda 삭제 |

## 검증 (삼성전자 2026 AGM)

### prov_financials scope
- consolidated income 18 rows / balance 52 rows / separate income 13 / balance 41 rows
- 12 metric flat 정확 (매출 333.6조 / 영업이익 43.6조 / 순이익 45.2조 / 자산 566조 / 부채 130조 / 자본 436조)
- extraction_status: success / scope_used: consolidated

### summary 강화
- 안건 hierarchy 노출 (제1호 정관 + 1-1, 1-2, 1-3, 1-4 sub-agenda)
- 1호 안건 메타 (제57기) 추출
- 정정공시 detect (정정 여부: 예)
- 결과 시점 표 제거 ✓

### aoi_change 보강
- 정관변경 4건 + 퇴직금 변경 0건 (해당 안건 없음)
- 둘 다 동일 scope에 raw 통합

## 비목표

- 4 quadrant 외 추가 재무제표 (현금흐름표 / 자본변동표) — 사용 빈도 낮아 제외
- 1호 안건 외 다른 안건 본문 raw scope — 별도 작업 (전수 본문 노출은 무거움)
- v1 `tools/shareholder.py:agm_financials_xml` — production v2 미사용, parser.py에 본체 보존 (조만간 archive)
