---
type: analysis
title: CSR (Cash Shareholder Return) — 한국식 주주환원율
tags: [dividend, treasury-share, shareholder-return, validation, 2026-04-29]
date: 2026-04-29
related: [dividend-tool-검증-예시, 주주환원, 자기주식취득결정, 자기주식소각결정, total-shareholder-return-2026-04-29]
---

# CSR — Cash Shareholder Return (한국식 주주환원율)

## 정의

```
CSR = (배당총액 + 자사주 매입 금액) / 지배주주 당기순이익 × 100
```

회사가 주주에게 돌려준 **현금** 관점.

- 분자 = 배당 + 자사주 **매입(acquire)** — 이사회 결의 시점 현금 유출
- 분모 = 연결 지배주주 당기순이익 (한국 표준)

자사주 **소각(retire)** 은 매입 후 회계 정리 단계로, 매입 시점에 이미 현금이 나간 상태이므로 **분자에 소각을 사용하면 이중 계산이 되거나 시점이 어긋난다.** T22(이전 빌드)는 분자에 소각 금액을 넣어 KT&G CSR을 119.23%로 잘못 보고했다. 본 빌드(T23)에서 정정.

## 변경 요약 (T22 정정)

### 1. dividend tool scope 분리
- 기존 `total_shareholder_return` (T22) → `cash_shareholder_return` (T23)으로 의미 정정 후 별도 scope로 정착
- 신규 `total_shareholder_return` 추가 — 글로벌 정의 (P_end - P_start + DPS) / P_start
- 두 scope 모두 `meta_signals` 동일 (선배당-후결의 + 감액배당)

### 2. CSR 분자 source 정정
- T22: `treasury_share.fetch_cancelation_summary` (소각/retire) — 잘못
- T23: `treasury_share.fetch_acquisition_summary` (매입/acquire) — 정정

### 3. 자사주 매입 데이터 보강
- 1차 source: DART 구조화 API `tsstkAqDecsn` (`aqpln_prc_ostk + aqpln_prc_estk`)
- 2차 폴백: `_parse_acquisition_body` 본문 파싱 (`open_proxy_mcp/services/treasury_share.py`)
- [기재정정] 공시 dedupe: `_dedupe_acquisition_rows` — (board_date, amount, shares) tuple 기준

## 검증 결과 (2026-04-27)

### KT&G 2024 (T22 vs T23 비교)

| 항목 | T22 (소각 사용, 잘못) | T23 (매입 사용, 정정) |
|---|---|---|
| 배당총액 | 5,884.48억원 | 5,884.48억원 |
| 자사주 (분자 소스) | 8,014.84억 (소각/retire) | 4,864.84억 (매입/acquire) |
| 환원 합계 | 1조 3,899.32억 | 1조 749.32억 |
| 지배주주 당기순이익 | 1조 1,657.27억 | 1조 1,657.27억 |
| **비율** | **119.23%** | **92.21%** |

T23 매입 결정 2건 (dedupe 후):
- 2024-08-08 결정: 보통주 361만주 / 3,371.74억원 (장내매수, 주주가치제고 및 주식소각)
- 2024-11-07 결정: 보통주 135만주 / 1,493.10억원 (장내매수, 주주가치제고 및 주식소각)

T22가 사용한 8,014.84억 소각 금액은 2024년 결의된 매입금액 4,864.84억보다 큼. 과거에 매입해서 보유 중이던 자사주를 2024년에 소각한 것이 합산됐기 때문. 시점·정의 모두 부정확.

### 삼성전자 (T22 vs T23 비교)

**2024 사업연도:**

| 항목 | T22 (소각, 잘못) | T23 (매입, 정정) |
|---|---|---|
| 배당총액 | 9조 8,107.67억 | 9조 8,107.67억 |
| 자사주 (분자 소스) | 0원 (2024 소각 0건) | 3조 50억 (2024-11-18 결정 1건) |
| 지배주주 당기순이익 | 33조 6,213.63억 | 33조 6,213.63억 |
| **비율** | **29.18%** | **38.10%** |

**2025 사업연도:**

| 항목 | T22 (소각, 잘못) | T23 (매입, 정정) |
|---|---|---|
| 배당총액 | 11조 1,079.06억 | 11조 1,079.06억 |
| 자사주 (분자 소스) | 3조 487억 (2/18 소각) | 6조 9,119.08억 (2/18, 7/8 매입 2건) |
| 지배주주 당기순이익 | 44조 2,609.56억 | 44조 2,609.56억 |
| **비율** | **31.98%** | **40.71%** |

T23 매입 결정 (2025):
- 2025-02-18 결정: 5,478만주 / 3조 원 (유가증권시장 장내 매수)
- 2025-07-08 결정: 6,472만주 / 3조 9,119.08억원 (유가증권시장 장내 매수)

## 회귀 0 검증

- summary scope: 기존 필드 (cash_dps, total_amount_mil, payout_ratio_dart, source) 모두 동일
- history scope: 변경 없음
- detail / policy_signals scope: 변경 없음
- meta_signals (선배당-후결의 + 감액배당): summary + CSR + TSR 3개 scope 동일하게 부착

## 안정성 가드

- 분모 0 / 음수 / unavailable 분리 (`ratio_status`: computed / denominator_zero_or_unknown / negative_net_income)
- 본문 파싱 실패 시 amount_krw / shares 0 폴백 + warnings 카운트
- [기재정정] dedupe 후 합산 (board_date+amount+shares 키)
- 회계 연도 필터: rcept_dt 연도 = target_year일 때만 합산
- 한국 표준 (연결 지배주주 당기순이익) 일관 사용

## 관련 공시

- [[자기주식취득결정]] - CSR 분자 source (acquire)
- [[자기주식소각결정]] - 회계 정리 단계 (분자에 사용 금지)
- [[배당기준일결정]] - 선배당-후결의 시그널
- [[감액배당결정]] - cross-link (자본준비금 감소)
- [[현금배당결정]] - 분자 dividend source

## 참고

- DIV_TOOL_RULE.md - dividend tool 규칙
- [[주주환원]] (concept)
- [[total-shareholder-return-2026-04-29]] - 글로벌 정의 TSR (별도 페이지)
