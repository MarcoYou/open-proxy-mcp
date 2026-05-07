---
type: audit
title: 파서 전수조사 — 명명형 vs raw 보존형 분류 + 권장 방향
date: 2026-05-08
status: draft
related:
  - wiki/lessons/law-layer-precision-260508.md
  - wiki/architecture/3-tier-fallback.md
  - wiki/decisions/open-proxy-guideline.md
---

# 파서 전수조사 — 현상태 + 분류 + 권장 방향

## 배경

코붕이 통찰 (2026-05-08, Ralph 4 후): 법령 layer 룰 catalog 확장 (B1-1~B1-5, B2-* 미사용 13개)보다 **`parse_aoi_xml` 같은 raw 파싱 강화** + LLM이 raw 직접 판단하는 게 본질. 

질문: **어떤 데이터는 파서가 명명해서 추출해야 하고, 어떤 데이터는 raw key-value로 LLM에 넘기는 게 나은가?**

## 분류 framework

| 분류 | 정의 | 적합 케이스 | 부적합 케이스 |
|---|---|---|---|
| **A. 명명형 (deterministic)** | 사람이 결정한 key + enum/숫자/boolean 값 | 표 (보수, 재무, 결과 표) / 카테고리 enum (이사 종류) / 강행규정 충족 여부 | 자연어 본문 / 모호 표현 / case-by-case 판단 |
| **B. raw 보존형 (markdown/full text)** | 원문 그대로 + 메타만 명명 | 정관 변경 본문 / 사유 설명 / 후보 경력 narrative | 숫자 표 / 합산 / regression 자동 검증 |
| **C. 혼합형** | 카테고리/메타 명명 + 본문 raw 보존 | 안건 (title 명명 + 본문 raw) / 정관변경 (clause 명명 + before/after raw) | (혼합형 자체가 절충안, 부적합 카테고리 없음) |

### 분류 기준

| 기준 | A 명명형 | B raw 보존 | C 혼합 |
|---|---|---|---|
| 결정론 (deterministic) | ✓ | △ | ✓ (메타) + △ (본문) |
| 자연어 이해 필요? | × | ✓ (LLM) | △ |
| Catalog 유지 비용 | 높음 (룰 추가) | 낮음 | 중간 |
| 토큰 비용 | 낮음 | 높음 | 중간 |
| 새 패턴 자동 catch | × (룰 추가 필요) | ✓ (LLM 추론) | △ |
| regression 검증 용이 | ✓ | × | ✓ (메타) |

## 파서 전수조사

### tools/parser.py — 12 public 파서 (XML 본문)

| 파서 | 호출 tool | return shape 핵심 | 분류 | 평가 |
|---|---|---|---|---|
| `parse_agenda_xml` | shareholder_meeting_notice | `[{number, level1-3, title, source, conditional, children}]` | **C 혼합** | title raw + 메타 명명. 안건 hierarchy 자체는 명명 OK. 적정. |
| `parse_meeting_info_xml` | shareholder_meeting_notice | `{meeting_type, meeting_term, datetime_text, place, ...}` | **A 명명** | 메타 데이터 (일시/장소). 명명 적정. |
| `parse_agenda_details_xml` | personnel/aoi 파서 의존 | `[{number, title, action, category, blocks: [...]}]` | **C 혼합** | blocks는 raw text/table. action/category는 명명. 적정. |
| `parse_personnel_xml` | shareholder_meeting_notice (board) | `{appointments: [{action, category, candidates: [{name, roleType, careerDetails}]}], summary}` | **C 혼합 (강한 명명)** | careerDetails가 부족 catch. **재검토 대상** — careerDetails는 raw narrative가 더 LLM 친화일 수 있음. 5년 룰 long_tenure_concerns 작동 안 함. |
| **`parse_aoi_xml`** ★ | shareholder_meeting_notice (aoi_change) | `{amendments: [{sub_id, label, clause, before, after, reason}], summary}` | **C 혼합 (이상적)** | clause/label 명명 + before/after/reason **raw** ✓. **현재 모범 사례.** Ralph 4 KT&G 사례 검증 — LLM이 본문 직접 catch 가능. |
| `parse_corrections_xml` | shareholder_meeting_notice | `{original_date, items: [{section, reason, before, after}]}` | **C 혼합** | section 명명 + reason/before/after raw. 적정. |
| `parse_financials_xml` | shareholder_meeting_notice (prov_financials) | `{consolidated: {balance_sheet, income_statement}, separate: ...}` | **A 명명 (강함)** | 재무제표 표 — 숫자 + 항목 enum. 명명 필수. |
| `parse_compensation_xml` | shareholder_meeting_notice (compensation) | `{items, summary: {currentTotalLimit, priorTotalPaid, ...}}` | **A 명명** | 보수한도 숫자. 명명 적정. |
| `parse_treasury_share_xml` | treasury_share | `{items, summary: {totalItems}}` | **A 명명** | 자사주 보유/처분/소각 표. 명명 적정. |
| `parse_capital_reserve_xml` | (안건 raw) | `{items: [{number, title, amount, purpose, reducedCapital, notes}], summary}` | **C 혼합** | amount 명명 + purpose/notes raw. 적정. |
| `parse_retirement_pay_xml` | shareholder_meeting_notice (aoi_change retirement) | `{amendments: [{label, clause, before, after, reason}], summary}` | **C 혼합** | aoi와 동일 패턴. 적정. |
| `extract_structural_elements` | (범용 추출) | `{tables, amounts, dates, names, legalRefs, percentages}` | **B raw 보존** | 안건 유형 무관 mechanical 추출. 적정. |

### tools/pdf_parser.py — 9 PDF 파서

3-tier fallback의 2번째 단계. XML 파서 실패 시 PDF → markdown 변환 후 동일 schema로 재추출.

| 파서 | XML 대응 | 분류 |
|---|---|---|
| `parse_compensation_pdf` | parse_compensation_xml | A 명명 |
| `parse_personnel_pdf` | parse_personnel_xml | C 혼합 |
| `parse_financials_pdf` | parse_financials_xml | A 명명 |
| `parse_aoi_pdf` | parse_aoi_xml | C 혼합 |
| `parse_treasury_share_pdf` | parse_treasury_share_xml | A 명명 |
| `parse_capital_reserve_pdf` | parse_capital_reserve_xml | C 혼합 |
| `parse_retirement_pay_pdf` | parse_retirement_pay_xml | C 혼합 |
| `parse_agenda_pdf` | parse_agenda_xml | C 혼합 |
| `extract_pdf_pages` | (utility, page select) | (raw bytes) |

→ XML 파서와 동일 분류. PDF→md 변환은 raw 보존 친화적 (markdown raw가 그대로 전달됨).

### services/ — 도메인 파서 (10+개)

| 파서 | 호출 tool | return | 분류 | 평가 |
|---|---|---|---|---|
| `corp_gov_report._parse_compliance_rate` | corp_gov_report | float | A 명명 | 단일 숫자. 명명 적정. |
| `corp_gov_report._parse_company_summary` | corp_gov_report | `{name, ticker, market_cap, ...}` | A 명명 | 메타 데이터. 적정. |
| `corp_gov_report._parse_metrics` | corp_gov_report | `[{key, value, ...}]` | A 명명 | 100+ 정량 metric. 적정. |
| `corp_gov_report._parse_principles` | corp_gov_report | `[{principle, status, comment}]` | C 혼합 | comment raw + status enum. 적정. |
| `treasury_share._parse_acquisition_body` | treasury_share | `{quantity, price, period, ...}` | A 명명 | 자사주 취득 메타. 명명 적정. |
| `treasury_share._parse_disposal_result_body` | treasury_share | `{quantity, price, ...}` | A 명명 | 처분 결과. 적정. |
| `treasury_share._parse_cancelation_body` | treasury_share | `{quantity, ...}` | A 명명 | 소각 결과. 적정. |
| `treasury_share._parse_trust_*_body` | treasury_share | `{quantity, broker, ...}` | A 명명 | 신탁. 적정. |
| `provisional_financial_statement.parse_provisional_financial_statement` | shareholder_meeting_notice (prov_financials) | `{consolidated, separate}` | A 명명 | 재무제표 표. 적정. |
| `provisional_financial_statement.extract_metrics` | (보조) | `{revenue, op_profit, ...}` | A 명명 | 4 quadrant metric. 적정. |

### tools/formatters.py — 포맷/결과 파서

| 파서 | 호출 tool | return | 분류 |
|---|---|---|---|
| `parse_kr_number` / `parse_kr_int` | (utility) | int / float | A 명명 (utility) |
| `_parse_agm_result_table` | shareholder_meeting_results | `[{agenda, for_count, against_count, ...}]` | A 명명 |
| `_parse_agm_result_summary` | shareholder_meeting_results | `[{agenda, outcome, targets}]` | A 명명 |
| `_parse_holding_purpose` | ownership_structure | enum | A 명명 |

### 보조 파서

| 파서 | 분류 |
|---|---|
| `tools/proxy.py:_parse_directions` | A 명명 (운용사 행사방향 enum) |
| `tools/news.py:_parse_pub_date` | A 명명 (날짜) |
| `tools/dividend.py:_parse_dividend_decision/items` | A 명명 |
| `services/date_utils.py:parse_date_param` | A 명명 (utility) |

## 통계

| 분류 | 파서 수 | 비율 |
|---|---|---|
| A 명명형 | ~25 | ~63% |
| B raw 보존 | 1 (extract_structural_elements) | ~3% |
| C 혼합 | ~14 | ~35% |

## 권장 방향

### 1. 유지 (A 명명형 그대로)

**숫자/표/enum 데이터** — 자동 검증/regression이 필요한 영역. 변경 X.
- 재무제표 (financials, prov_financials)
- 보수 (compensation)
- 자사주 (treasury_share)
- 거버넌스 metric (corp_gov_report metrics)
- 결과 표 (agm_result_table/summary)
- ownership 신호 (holding_purpose 등)
- 배당 (dividend)

→ 25개 파서 중 ~25개. 변경 없음.

### 2. 강화 + raw 부분 보강 (C 혼합형)

**자연어 본문 영역** — 메타는 명명 유지 + 본문 raw 보존 강화. LLM 친화 동시 결정론도 유지.

#### 2-A. `parse_aoi_xml` ⭐ (현재 모범)

- 현재: `{amendments: [{sub_id, label, clause, before, after, reason, additionalClauses}]}`
- 문제: clause/label/sub_id 추출 누락 케이스 (Ralph 4 KOSPI 200 audit에서 일부 회사 amendments 비어있음)
- 강화 방향:
  - clause/label 추출 누락 fallback (label 없으면 first non-empty line / clause 패턴 다양화)
  - sub_agendas 매칭 실패 시 hardcoded grouping 보강
  - **before/after/reason raw text 그대로 보존 유지** (LLM이 본문 직접 검토)
- 효과: B1-1, B1-3, B1-5, B1-8, B1-9, B2-1, B2-2, B2-7 미사용 룰 → LLM 자연어 판단으로 자동 활성화

#### 2-B. `parse_personnel_xml` (재검토 필요)

- 현재: `{appointments: [{candidates: [{careerDetails: [...]}]}]}` — careerDetails 강한 명명
- 문제: 서진/펩트론/심텍/고영 등 careerDetails 비어 있음 (parser miss)
- 강화 방향 (TO_DO에 이미 있음):
  - careerDetails 정규식 확대 (audit role 본문 패턴 다양화)
  - **fallback raw narrative 추가**: 명명 추출 실패 시 후보 본문 raw text 그대로 보존
- 효과: 5년 룰 long_tenure_concerns 작동 + LLM이 raw 본문에서 case-by-case 판단

#### 2-C. `parse_capital_reserve_xml` / `parse_retirement_pay_xml` / `parse_corrections_xml`

- 현재: 메타 명명 + before/after raw — aoi와 동일 패턴
- 강화 방향: 동일 (raw 누락 fallback + 메타 추출 다양화)

### 3. 코드 변경 필요 없음 — `_law_layer` 룰 catalog 슬림화 (별도)

Ralph 4 발견: 룰 catalog 미사용 13개는 "패턴 부족"이 아니라 **"LLM 영역인데 룰로 풀려고 한 미스매치"**. 

→ B1/B2 모호 룰 (B1-1 시차임기 / B1-3 보수 정관 / B2-1 임기 유연화 등)은 **삭제 또는 "raw 검토" hint로 격하**. proxy_advise 응답에 amendments raw + careerDetails raw 포함하여 LLM 직접 판단.

(이건 파서 audit 외부, 별도 ralph)

## Ralph 5 실측 결과 (2026-05-08 후속 검증)

본 audit의 "보강 필요 두 파서" 결론을 Ralph 5 (260508_0207_ralph_parser-precision)에서 직접 검증한 결과:

### parse_personnel_xml careerDetails 누락
- 분쟁 14 회사 + KOSPI 30 회사 = **44 회사 / 225 후보** 광범위 sample
- careerDetails 비어있음: **0건 (0.0%)**
- careerCompanyGroups 비어있음: **0건**
- → TO_DO의 "서진/펩트론/심텍/고영 careerDetails 누락" 가정은 stale 정보. 현재 시점에선 사실 아님.

### parse_aoi_xml amendments 누락
- KOSPI 200 전체 audit
- 정관변경 안건 OK: **178**
- 정관변경 안건 + amendments=[] (누락): **3 (1.66%)** — 기업은행 / 한국금융지주 / HD현대건설기계
- 누락 3건 raw 분석: 모두 **source 본문에 정관변경 detail 없음** (별첨 PDF). parse_aoi_xml 한계가 아닌 source 한계 → PDF fallback 영역.

### 결론 update

| 파서 | 본 audit 결론 (초안) | Ralph 5 실측 결론 |
|---|---|---|
| parse_personnel_xml | "careerDetails fallback 추가 필요" | **현재 정밀도 충분, 보강 불필요** |
| parse_aoi_xml | "amendments fallback 추가 필요" | **1.66% 누락은 source 한계, parser 보강 불필요** |

→ 본 audit 1차 권장 (두 파서 보강)은 **실측 후 무효화**. 40 파서 모두 적정.

## 다음 ralph 후보 (우선순위) — Ralph 5 후 update

Ralph 5에서 두 파서 보강 ralph 후보 1, 2 실측 후 무효화 (현재 정밀도 충분). 새 우선순위:

| 우선순위 | ralph | 영향 |
|---|---|---|
| 🟡 1 | `_law_layer` 룰 슬림화 + amendments raw 통합 | proxy_advise 응답에 본문 raw 노출 → LLM 판단 영역 명시화 (B1/B2 모호 룰 → "raw 검토" hint) |
| 🟢 2 | parse_aoi_xml PDF fallback (3-tier 2단계) 검증 | 1.66% 누락 (기업은행 등 별첨 PDF 케이스) catch 가능 여부 |
| 🟢 3 | corp_gov_report _parse_principles raw narrative 보강 | comment raw 활용 ↑ |
| 🟢 4 | _classify_director_tenure logic 개선 (5년 룰) | careerDetails 정상 추출되지만 5년 룰 자체 logic 검토 필요 |

### 비목표

- A 명명형 25개 파서 — 변경 없음
- 3-tier fallback 자체 구조 (XML→PDF→OCR) — 변경 없음
- _classify_*, _decide_* 분류기 — 별도 ralph

## 핵심 통찰

1. **`parse_aoi_xml`가 이상적 모델**: 메타는 명명, 본문은 raw — LLM 친화 + regression 친화 동시 만족.
2. **`parse_personnel_xml`은 과도하게 명명형**: careerDetails 명명 추출 실패 시 fallback 없음. raw narrative 추가 시 두 모드 다 가능.
3. **명명형 25개는 적정**: 숫자/표는 결정론 필수. raw 변경 X.
4. **룰 catalog 확장 ≠ 정확도 향상**: Ralph 4 미사용 13개 룰은 raw 보존 + LLM 판단이 더 효과적.

## archive

- 파서 cataloging 시점: 2026-05-08
- 분석 대상: tools_v2 17개 → services 21개 → tools/parser.py 12 public + tools/pdf_parser.py 9 + 보조 파서 ~10개
- 총 ~40 파서 분류 완료
