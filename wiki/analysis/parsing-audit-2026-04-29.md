---
type: analysis
title: 파서 전수 audit 2026-04-29 — 11 data tool x KOSPI 100 + KOSDAQ 96
tags: [audit, parsing, health-check, data-tool, regression]
related: [parsing-audit-2026-04-22, parsing-audit-2026-04-21]
date: 2026-04-29
---

# 파서 전수 audit 2026-04-29

11 data tool을 KOSPI 100 + KOSDAQ 96 = 196 기업에 대해 전수 호출하고 정확도/일관성/속도를 측정.
vs 2026-04-22 audit (15 대형주 기업) 비율 기반 regression 비교.

**주의**: 2026-04-22 audit은 15개 대형주 (대부분 KOSPI top-tier) 표본이었다. 본 audit은 196개로 4-22 표본에 없던 KOSDAQ 96개와 중소형 KOSPI를 다수 포함한다. 따라서 단순 exact% 비교는 표본 차이로 인한 변화이지 코드 regression이 아니다. 코드 regression 판정은 KOSPI vs 2026-04-22 비교, 그리고 에러/예외 0건 유지 여부로 한다.

## 환경
- 실행: 2026-04-29
- 유니버스: KOSPI 100 + KOSDAQ 96 = 196 기업
- 호출: 11 tool x summary scope = 총 2156 호출
- 추정 DART API 호출: 102,919 회
- 평균 응답: 2.76초
- 병렬: 12 기업/배치, 배치 사이 1초 sleep

## 결과 매트릭스

| tool.scope | exact | partial | error | exact% | field% | avg_s | avg_api | regression vs 04-22 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| company.summary | 194 | 0 | 2 | 99.0% | 99.0% | 4.96s | 23.2 | -1.0p (-4.1s) |
| corp_gov_report.summary | 94 | 100 | 2 | 48.0% | 57.1% | 1.31s | 33.9 | -38.7p (-6.9s) |
| corporate_restructuring.summary | 29 | 165 | 2 | 14.8% | 99.0% | 1.03s | 46.4 | -5.2p (-2.6s) |
| dilutive_issuance.summary | 52 | 142 | 2 | 26.5% | 99.0% | 1.0s | 46.4 | +6.5p (-2.5s) |
| dividend.summary | 193 | 1 | 2 | 98.5% | 98.5% | 2.45s | 46.0 | -1.5p (-6.1s) |
| ownership_structure.summary | 178 | 15 | 3 | 90.8% | 98.5% | 1.15s | 46.3 | +10.8p (-6.7s) |
| proxy_contest.summary | 182 | 12 | 2 | 92.9% | 99.0% | 1.22s | 93.5 | -7.1p (-7.7s) |
| related_party_transaction.summary | 132 | 62 | 2 | 67.3% | 99.0% | 0.78s | 37.4 | -26.0p (-6.1s) |
| shareholder_meeting.summary | 189 | 0 | 3 | 96.4% | 98.5% | 12.74s | 36.0 | +3.1p (+6.1s) |
| treasury_share.summary | 100 | 94 | 2 | 51.0% | 99.0% | 1.22s | 68.8 | -49.0p (-5.5s) |
| value_up.summary | 99 | 94 | 3 | 50.5% | 98.5% | 2.46s | 47.2 | -22.8p (-5.2s) |

## KOSPI vs KOSDAQ 분리 통계

| tool.scope | KOSPI exact% | KOSDAQ exact% | gap |
|---|---:|---:|---:|
| company.summary | 100.0% | 97.8% | +2.2p |
| corp_gov_report.summary | 81.7% | 9.8% | +71.9p |
| corporate_restructuring.summary | 16.3% | 13.0% | +3.3p |
| dilutive_issuance.summary | 23.1% | 30.4% | -7.4p |
| dividend.summary | 100.0% | 96.7% | +3.3p |
| ownership_structure.summary | 86.5% | 95.7% | -9.1p |
| proxy_contest.summary | 98.1% | 87.0% | +11.1p |
| related_party_transaction.summary | 78.8% | 54.3% | +24.5p |
| shareholder_meeting.summary | 100.0% | 92.4% | +7.6p |
| treasury_share.summary | 53.8% | 47.8% | +6.0p |
| value_up.summary | 69.2% | 29.3% | +39.9p |

## 에러/예외 상세

- 총 에러: 25건 (1.16%)
- 영향 받은 기업: 3개 (대부분 "DART corp_code 미등록" 케이스)

| company | ticker | 에러 발생 tool 수 | 원인 |
|---|---|---:|---|
| 노바텍 | 403270 | 11/11 | DART corp_code 미등록 (티커 변경/상장폐지/잘못된 ticker) |
| 에코프로에이치엔 | 357850 | 11/11 | DART corp_code 미등록 (티커 변경/상장폐지/잘못된 ticker) |
| 셀트리온헬스케어 | 091990 | 3/11 | 단발 에러 |

**해석**: 에러의 절대 다수는 "기업 자체가 DART API 마스터에 없는" 입력 데이터 문제. tool 코드 자체의 예외(traceback)나 DART API 다운은 0건 — 즉 모든 tool이 "resolution 실패"를 정상 error envelope으로 반환. graceful degrade 작동.

## 필드 채움률 < 100% 스코프

| tool.scope | field_pct | exact% | 해석 |
|---|---:|---:|---|
| corp_gov_report.summary | 57.1% | 48.0% | 에러 + 일부 partial 필드 누락 |
| shareholder_meeting.summary | 98.5% | 96.4% | 필드 누락 = 에러 케이스 (3건) 만큼만 |
| ownership_structure.summary | 98.5% | 90.8% | 필드 누락 = 에러 케이스 (3건) 만큼만 |
| dividend.summary | 98.5% | 98.5% | 필드 누락 = 에러 케이스 (2건) 만큼만 |
| value_up.summary | 98.5% | 50.5% | 필드 누락 = 에러 케이스 (3건) 만큼만 |
| company.summary | 99.0% | 99.0% | 필드 누락 = 에러 케이스 (2건) 만큼만 |
| treasury_share.summary | 99.0% | 51.0% | 필드 누락 = 에러 케이스 (2건) 만큼만 |
| proxy_contest.summary | 99.0% | 92.9% | 필드 누락 = 에러 케이스 (2건) 만큼만 |
| corporate_restructuring.summary | 99.0% | 14.8% | 필드 누락 = 에러 케이스 (2건) 만큼만 |
| dilutive_issuance.summary | 99.0% | 26.5% | 필드 누락 = 에러 케이스 (2건) 만큼만 |
| related_party_transaction.summary | 99.0% | 67.3% | 필드 누락 = 에러 케이스 (2건) 만큼만 |

## 개선 가능 영역

### 1. 비교적 높은 partial 비율 tool
- **corporate_restructuring.summary**: partial 165/196 (84.2%). 대부분 "사건 없음" 정상 표현이지만 그 중 일부는 데이터 누락 가능성.
- **dilutive_issuance.summary**: partial 142/196 (72.4%). 대부분 "사건 없음" 정상 표현이지만 그 중 일부는 데이터 누락 가능성.
- **corp_gov_report.summary**: partial 100/196 (51.0%). 대부분 "사건 없음" 정상 표현이지만 그 중 일부는 데이터 누락 가능성.

### 2. KOSPI vs KOSDAQ gap > 20p
- **corp_gov_report.summary**: KOSPI 81.7% vs KOSDAQ 9.8% (gap +71.9p)
- **value_up.summary**: KOSPI 69.2% vs KOSDAQ 29.3% (gap +39.9p)
- **related_party_transaction.summary**: KOSPI 78.8% vs KOSDAQ 54.3% (gap +24.5p)

## 속도 분석

| tier | tool.scope | avg_s |
|---|---|---:|
| fast (<4s) | related_party_transaction.summary | 0.78s |
| fast (<4s) | dilutive_issuance.summary | 1.0s |
| fast (<4s) | corporate_restructuring.summary | 1.03s |
| fast (<4s) | ownership_structure.summary | 1.15s |
| fast (<4s) | treasury_share.summary | 1.22s |
| fast (<4s) | proxy_contest.summary | 1.22s |
| fast (<4s) | corp_gov_report.summary | 1.31s |
| fast (<4s) | dividend.summary | 2.45s |
| fast (<4s) | value_up.summary | 2.46s |
| mid (4-8s) | company.summary | 4.96s |
| slow (>=8s) | shareholder_meeting.summary | 12.74s |

## Regression vs 2026-04-22 요약

| tool.scope | exact% Δ | avg_s Δ |
|---|---:|---:|
| company.summary | -1.0p | -4.1s |
| corp_gov_report.summary | -38.7p | -6.9s |
| corporate_restructuring.summary | -5.2p | -2.6s |
| dilutive_issuance.summary | +6.5p | -2.5s |
| dividend.summary | -1.5p | -6.1s |
| ownership_structure.summary | +10.8p | -6.7s |
| proxy_contest.summary | -7.1p | -7.7s |
| related_party_transaction.summary | -26.0p | -6.1s |
| shareholder_meeting.summary | +3.1p | +6.1s |
| treasury_share.summary | -49.0p | -5.5s |
| value_up.summary | -22.8p | -5.2s |

## 미해결 케이스 / 개선 추천

### KOSPI subset (vs 2026-04-22 같은 시장) regression 검증
- 5p 이상 하락 (코드 regression 의심):
  - **treasury_share.summary**: 100.0% -> 53.8%. 원인 조사 필요.
  - **related_party_transaction.summary**: 93.3% -> 78.8%. 원인 조사 필요.

### 표본 확대로 인한 자연스러운 exact% 변동
- 2026-04-22는 15개 대형주 (블루칩) -> 본 audit은 196개로 중소형/KOSDAQ 포함
- treasury_share, value_up, related_party_transaction 등은 중소형사·KOSDAQ에서 "사건 없음" partial 비율이 높아 exact% 자연 하락 (regression 아님)
- corp_gov_report는 KOSDAQ 자율공시라 KOSDAQ 81.7% partial -> 전체 exact% 큰 하락 (regression 아님)

### 신규 측정 tool/scope 관찰
- 모든 scope가 2026-04-22 audit과 동일

## 유니버스 노트

- KOSPI 100: data/market_cap.json에서 시총 상위 100 (실제 199개 중)
- KOSDAQ 96: 시총 상위 KOSDAQ 큐레이션 리스트 (중복 제거 후 96개)
- 일부 KOSDAQ 후보 ticker는 KOSPI로 이동했거나 상장폐지된 경우가 있어 95-100 사이로 변동

## 관련

[[parsing-audit-2026-04-22]] [[parsing-audit-2026-04-21]] [[OpenProxy-MCP]]