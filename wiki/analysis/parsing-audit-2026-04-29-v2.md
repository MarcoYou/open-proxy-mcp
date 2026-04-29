---
type: analysis
title: 파서 전수 audit v2 2026-04-29 — no_filing 분리 + 진짜 partial 측정
tags: [audit, parsing, health-check, data-tool, regression, no_filing]
related: [parsing-audit-2026-04-29, parsing-audit-2026-04-22, parsing-audit-2026-04-21]
date: 2026-04-29
---

# 파서 전수 audit v2 2026-04-29 — no_filing 분리

11 data tool에 새 메타(`no_filing`/`filing_count`/`parsing_failures`)를 도입해
`AnalysisStatus.PARTIAL`이 묶어버리던 두 가지 현상을 분리:

1. **no_filing**: 조사 구간에 사건 자체가 없는 정상 케이스
2. **partial_failure**: 사건은 발견됐으나 일부 필드 파싱 실패 (진짜 partial)

이전 audit (2026-04-29 v1)에서 `partial 32%` 라는 모호한 결과가 사실은
대부분 (96%) `no_filing`(정상 사건 없음)이었고 진짜 partial은 1.5%에 불과함을 확인.

## 환경

- 실행: 2026-04-29
- 유니버스: KOSPI 100 + KOSDAQ 96 = 196 기업 (v1 audit과 동일)
- 호출: 11 tool x summary scope = 총 2,156 호출
- 평균 응답: 1.0-12.7s (tool별 동일)
- 병렬: 12 기업/배치, 배치 사이 1초 sleep

## 핵심 변경

### contracts.py
- `AnalysisStatus.NO_FILING` enum 신규 추가
- `build_filing_meta(filing_count, parsing_failures)` 헬퍼 추가 (5-tuple 메타 일관 생성)
- `status_from_filing_meta(meta)` 헬퍼 (분기 로직 일원화)

### 11 data tool 모두
data dict에 다음 5개 메타 필드 추가 (모든 응답 schema 그대로 + 메타만 추가, regression 0):

```json
{
  "no_filing": true | false,
  "filing_count": int,
  "parsed_count": int,
  "parsing_failures": int,
  "filing_status": "no_filing" | "all_parsed" | "partial_failure"
}
```

### Action tool _merge_status 갱신 (vote_brief, engagement_case, campaign_brief)
NO_FILING은 PARTIAL보다 높은 등급(EXACT 동급) — 모든 입력이 NO_FILING이면 NO_FILING,
일부라도 EXACT가 섞이면 EXACT. ERROR/REQUIRES_REVIEW/CONFLICT/PARTIAL/AMBIGUOUS 우선 순위는 유지.

## 4-class 결과 매트릭스

| tool.scope | exact | no_filing | partial_failure | error | exact% | no_filing% | partial% |
|---|---:|---:|---:|---:|---:|---:|---:|
| company.summary | 193 | 1 | 0 | 2 | 98.5% | 0.5% | 0.0% |
| corp_gov_report.summary | 94 | 82 | 18 | 2 | 48.0% | 41.8% | 9.2% |
| corporate_restructuring.summary | 29 | 165 | 0 | 2 | 14.8% | 84.2% | 0.0% |
| dilutive_issuance.summary | 52 | 142 | 0 | 2 | 26.5% | 72.4% | 0.0% |
| dividend.summary | 147 | 47 | 0 | 2 | 75.0% | 24.0% | 0.0% |
| ownership_structure.summary | 178 | 0 | 15 | 3 | 90.8% | 0.0% | 7.7% |
| proxy_contest.summary | 182 | 12 | 0 | 2 | 92.9% | 6.1% | 0.0% |
| related_party_transaction.summary | 132 | 62 | 0 | 2 | 67.3% | 31.6% | 0.0% |
| shareholder_meeting.summary | 187 | 0 | 0 | 5 | 95.4% | 0.0% | 0.0% |
| treasury_share.summary | 100 | 94 | 0 | 2 | 51.0% | 48.0% | 0.0% |
| value_up.summary | 99 | 94 | 0 | 3 | 50.5% | 48.0% | 0.0% |
| **합계 (2,156)** | **1,393** | **699** | **33** | **27** | **64.6%** | **32.4%** | **1.5%** |

## v1 vs v2 분리 결과

이전 v1 audit의 partial 32%가 어떻게 분리됐는지:

| tool | v1 partial | v2 no_filing | v2 partial_failure | no_filing 비중 |
|---|---:|---:|---:|---:|
| corp_gov_report | 100 | 82 | 18 | 82.0% |
| corporate_restructuring | 165 | 165 | 0 | 100.0% |
| dilutive_issuance | 142 | 142 | 0 | 100.0% |
| dividend | 1 → 47* | 47 | 0 | 100.0% |
| ownership_structure | 15 | 0 | 15 | **0.0%** |
| proxy_contest | 12 | 12 | 0 | 100.0% |
| related_party_transaction | 62 | 62 | 0 | 100.0% |
| treasury_share | 94 | 94 | 0 | 100.0% |
| value_up | 94 | 94 | 0 | 100.0% |

\* dividend는 v1이 alotMatter 비어 있어도 EXACT로 잡았던 케이스가 다수. v2에선 cash_dps=0 + 결정 공시 없음 = NO_FILING으로 정확 분리. KOSDAQ 무배당사 위주.

**핵심 발견**: 진짜 parsing failure는 두 tool에 집중:
- **corp_gov_report**: 18건 (모두 KOSPI 금융지주). `_EXCLUDE_REPORT_SUBSTR=("연차보고서",)` 제외 + 일부 보고서가 표 구조 다른 케이스
- **ownership_structure**: 15건 (모두 KOSPI 대형주). 정기보고서 hyslrSttus가 빈 list 반환하는 케이스 — SK하이닉스/현대차/LG전자 등

## KOSPI vs KOSDAQ 4-class 분리

| tool.scope | KOSPI(exact% no_fil% partf% err%) | KOSDAQ(exact% no_fil% partf% err%) |
|---|---:|---:|
| company.summary | 100.0% / 0.0% / 0.0% / 0.0% | 96.7% / 1.1% / 0.0% / 2.2% |
| corp_gov_report.summary | 81.7% / 1.0% / 17.3% / 0.0% | 9.8% / 88.0% / 0.0% / 2.2% |
| corporate_restructuring.summary | 16.3% / 83.7% / 0.0% / 0.0% | 13.0% / 84.8% / 0.0% / 2.2% |
| dilutive_issuance.summary | 23.1% / 76.9% / 0.0% / 0.0% | 30.4% / 67.4% / 0.0% / 2.2% |
| dividend.summary | 88.5% / 11.5% / 0.0% / 0.0% | 59.8% / 38.0% / 0.0% / 2.2% |
| ownership_structure.summary | 86.5% / 0.0% / 13.5% / 0.0% | 95.7% / 0.0% / 1.1% / 3.3% |
| proxy_contest.summary | 98.1% / 1.9% / 0.0% / 0.0% | 87.0% / 10.9% / 0.0% / 2.2% |
| related_party_transaction.summary | 78.8% / 21.2% / 0.0% / 0.0% | 54.3% / 43.5% / 0.0% / 2.2% |
| shareholder_meeting.summary | 98.1% / 0.0% / 0.0% / 1.9% | 92.4% / 0.0% / 0.0% / 3.3% |
| treasury_share.summary | 53.8% / 46.2% / 0.0% / 0.0% | 47.8% / 50.0% / 0.0% / 2.2% |
| value_up.summary | 69.2% / 30.8% / 0.0% / 0.0% | 29.3% / 67.4% / 0.0% / 3.3% |

**해석 변화**:
- v1에선 corp_gov_report가 "KOSPI 81.7% vs KOSDAQ 9.8% gap +71.9p"로 큰 격차로 보였지만,
  v2에선 KOSDAQ는 88%가 NO_FILING (자율공시 미제출 = 정상)으로 명확히 분리.
- value_up도 KOSDAQ 67.4% NO_FILING이 정상. KOSPI는 30.8% NO_FILING.

## 진짜 partial_failure 33건 상세

### corp_gov_report 18건 (모두 KOSPI 금융지주/은행/증권/보험)
KB금융, 삼성생명, 신한지주, 미래에셋증권, 하나금융지주, 우리금융지주, 삼성화재,
메리츠금융지주, 기업은행, 한국금융지주, 등 (모두 filing_count=3, parsing_failures=1).

원인: 의무공시는 했으나 metrics 표 파싱 실패. 금융지주 구조의 보고서는 일반 형식과 다름.
대부분 filing_count=3 → 1개 파싱 실패는 최신만 또는 일부 회차만.

**개선 우선순위**: 중. KOSPI 의무공시이므로 진짜 누락이지만, 원문 수동 확인은 evidence tool로 가능.

### ownership_structure 15건 (모두 KOSPI 대형주)
SK하이닉스, 현대차, 한화에어로스페이스, SK스퀘어, 한화시스템, SK, HD현대, LG전자,
LIG넥스원, SK텔레콤, 등.

원인: DART `hyslrSttus` API가 빈 list 반환 (해당 기업의 정기보고서가 다른 구조이거나 5%
대량보유로만 채움). filing_count=1-4, 5% 블록은 살아있음 → control_map은 복원 가능.

**개선 우선순위**: 중. 5% 블록만으로 분석은 가능. major_holders 확보를 위해 보조 fetch 추가 검토.

## 진짜 partial_failure < 5% 검증 ✓

- **합계**: 33건 / 2,156 호출 = **1.5%** ← 목표 5% 미만 달성
- 파싱 안정성이 매우 높음. 사용자가 보던 "partial 32%"는 실제로는 "사건 없음"이었음.

## error 27건 분석 (regression 0)

| company | ticker | tool 별 발생 | 원인 |
|---|---|---|---|
| 노바텍 | 403270 | 11/11 | DART corp_code 미등록 |
| 에코프로에이치엔 | 357850 | 11/11 | DART corp_code 미등록 |
| 셀트리온헬스케어 | 091990 | 1/11 | 단발 에러 (shareholder_meeting) |
| NH투자증권 | 005940 | 1/11 | 일시적 DART throttling (재실행 시 정상) |
| 한화 | 000880 | 1/11 | 일시적 DART throttling (재실행 시 정상) |
| 기타 | 다수 | 1-2건 | corp_gov_report에서 KOSDAQ 자율공시 fetch 실패 |

NH투자증권/한화는 재실행 시 정상 동작 확인 (concurrent throttling). regression 아님.

## Regression 0 검증

### 1. 응답 schema 호환성
모든 tool이 기존 응답 필드를 100% 유지 + `no_filing/filing_count/...` 메타만 추가.
기존 호출자(action tool, tools_v2 renderer)는 문제 없이 동작.

### 2. status enum 호환
`AnalysisStatus.NO_FILING`은 신규 enum. 기존 `EXACT/AMBIGUOUS/PARTIAL/CONFLICT/REQUIRES_REVIEW/ERROR`는 그대로 유지.

### 3. _merge_status 호환
NO_FILING이 EXACT 동급으로 처리됨. 기존 PARTIAL/AMBIGUOUS/CONFLICT/REQUIRES_REVIEW/ERROR 우선순위 그대로.
구체적: 모든 입력이 NO_FILING일 때만 NO_FILING 반환, 일부라도 EXACT가 섞이면 EXACT.

### 4. tools_v2 renderer
- treasury_share, value_up, corporate_restructuring, dilutive_issuance, related_party_transaction
  렌더러에 `## 공시 없음` 섹션 추가 (no_filing=True 시).
- 기존 출력 형식 유지.

### 5. action tool 동작 검증
- `vote_brief(005930)` → status=`partial` (기존과 동일, NO_FILING 신호 유입 시 영향 없음)
- `engagement_case(033780)` → status=`exact` (기존과 동일)

## 코드 변경 파일 (총 13개)

### 새/수정 — services
- `open_proxy_mcp/services/contracts.py` — AnalysisStatus.NO_FILING + 헬퍼 2개
- `open_proxy_mcp/services/shareholder_meeting.py`
- `open_proxy_mcp/services/ownership_structure.py`
- `open_proxy_mcp/services/dividend_v2.py`
- `open_proxy_mcp/services/treasury_share.py`
- `open_proxy_mcp/services/proxy_contest.py`
- `open_proxy_mcp/services/value_up_v2.py`
- `open_proxy_mcp/services/corporate_restructuring.py`
- `open_proxy_mcp/services/dilutive_issuance.py`
- `open_proxy_mcp/services/related_party_transaction.py`
- `open_proxy_mcp/services/corp_gov_report.py`
- `open_proxy_mcp/services/company.py`
- `open_proxy_mcp/services/vote_brief.py` — _merge_status NO_FILING 처리
- `open_proxy_mcp/services/engagement_case.py` — _merge_status NO_FILING 처리
- `open_proxy_mcp/services/campaign_brief.py` — _merge_status NO_FILING 처리

### 수정 — tools_v2 renderers
- `open_proxy_mcp/tools_v2/treasury_share.py`
- `open_proxy_mcp/tools_v2/value_up.py`
- `open_proxy_mcp/tools_v2/corporate_restructuring.py`
- `open_proxy_mcp/tools_v2/dilutive_issuance.py`
- `open_proxy_mcp/tools_v2/related_party_transaction.py`

### audit 스크립트
- `/tmp/audit/run_audit_v2.py` — 새 4-class 분류 사용

## 향후 작업

### 단기
- corp_gov_report: 금융지주 보고서 형식 fallback 파서 추가 → partial_failure 18건 감소 가능
- ownership_structure: hyslrSttus 빈 응답 시 5% 블록 + 자사주로 control_map 보강 → partial_failure 15건 처리

### 중기
- audit 자동화: GitHub Actions로 주 1회 회귀 검증
- 새 메타 활용한 frontend 대시보드: "사건 없음 (정상)" vs "데이터 누락" 구분 표시

## 관련

[[parsing-audit-2026-04-29]] [[parsing-audit-2026-04-22]] [[parsing-audit-2026-04-21]] [[OpenProxy-MCP]]
