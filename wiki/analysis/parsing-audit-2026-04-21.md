---
type: analysis
title: 파싱 audit 매트릭스 (2026-04-21)
tags: [audit, parsing, health-check, data-tool]
related: [OpenProxy-MCP, parsing-audit, 파서-판정-등급]
date: 2026-04-21
---

# 파싱 audit 매트릭스 (2026-04-21)

v2 data tool 10종의 파싱 건강도를 20개 대표 기업 표본에 대해 전수 측정한 결과.

## 표본 (20 회사)

| 카테고리 | 기업 |
|---------|------|
| 대형 (5) | 삼성전자, SK하이닉스, 현대자동차, KB금융, NAVER |
| 분쟁/액티비즘 (5) | 고려아연, 한미사이언스, OCI, 한진칼, KT&G |
| 지주회사 (3) | SK, LG, CJ |
| M&A·재편 (3) | 두산에너빌리티, 이마트, 일동제약 |
| 중소형 (4) | 메리츠금융지주, 하이브, 에이피알, 셀트리온 |

## 측정 차원

각 tool의 `summary` scope를 호출하고 다음 기록:
- **status**: exact / partial / ambiguous / error / EXCEPTION
- **elapsed_sec**: 응답 시간 (초)
- **api_calls**: 해당 호출이 소진한 DART API 수
- **warnings**: 반환된 warning 수

## 결과 매트릭스

| tool | exact | partial | ambig | error | avg_s | avg_api |
|------|:-----:|:-------:|:-----:|:-----:|:-----:|:-------:|
| company | 20 | 0 | 0 | 0 | 5.50 | 0.0* |
| shareholder_meeting | 20 | 0 | 0 | 0 | 4.39 | 0.0* |
| ownership_structure | 17 | 3 | 0 | 0 | 3.82 | 0.0* |
| dividend | 20 | 0 | 0 | 0 | 6.38 | 0.0* |
| treasury_share | 18 | 2 | 0 | 0 | 2.16 | 0.0* |
| proxy_contest | 20 | 0 | 0 | 0 | 5.15 | 0.0* |
| value_up | 14 | 6 | 0 | 0 | 3.19 | 0.0* |
| corporate_restructuring | 5 | 15 | 0 | 0 | 1.24 | 4.0 |
| dilutive_issuance | 3 | 17 | 0 | 0 | 1.34 | 4.0 |
| related_party_transaction | 17 | 3 | 0 | 0 | 2.27 | 2.0 |

\* api_calls=0인 tool은 아직 `data.usage` 필드를 노출하지 않는 신버전 이전 tool. 후속 보강 대상.

## 해석

### 완벽 통과 (exact 100%)
- **company, shareholder_meeting, dividend, proxy_contest** — 20/20 exact
- 기본 정기공시 기반 tool들. 대형·중소형 모두 안정적.

### 높은 exact 비율 (85-90%)
- **ownership_structure (17/20)**: 3 partial — KT&G, 메리츠금융지주 등 사업보고서 기준연도 이슈 가능성
- **treasury_share (18/20)**: 2 partial — 자사주 이벤트 없는 회사에서 정상
- **related_party_transaction (17/20)**: 3 partial — 사건 없는 회사에서 정상

### Partial이 많은 tool
- **corporate_restructuring (5 exact / 15 partial)**: 대형 M&A 이벤트가 24개월 내 없는 회사가 대부분
- **dilutive_issuance (3 exact / 17 partial)**: 유상증자·CB·BW·감자 이벤트가 대형 우량기업에 드묾
- **value_up (14/6)**: 밸류업 계획 미제출 기업 6곳 partial

**→ 이는 파싱 실패가 아니라 "사건 없음"의 정확한 표현**. tool이 정상 작동.

### 에러 0건 ★

모든 tool이 모든 표본에서 예외·에러 없이 응답. 안정성 확인.

## 속도 분석

| 범위 | tool |
|------|------|
| 빠름 (< 2s) | corporate_restructuring, dilutive_issuance |
| 중간 (2-4s) | treasury_share, related_party_transaction, value_up, ownership_structure |
| 느림 (4-7s) | shareholder_meeting, proxy_contest, company, dividend |

- 느린 tool은 사업보고서 + 정기/수시 공시 여러 건 병행 호출 → 구조적 이유
- 가장 빠른 tool은 단일 API 시리즈 (DS005) 병렬 호출이라 오히려 빠름
- **에이피알 shareholder_meeting 16.88s 단일 이상치** 관찰 — 캐시 미스 가능성, 후속 확인

## 기존 자료와 비교 (Partial 비율 해석)

Partial은 두 가지 의미:
1. **"사건 없음" (정상)**: M&A·희석·밸류업 이벤트는 본질적으로 간헐적
2. **"데이터 불완전" (개선 대상)**: 사업보고서 기준연도 차이, 특정 API 응답 누락

현재 audit에서 대부분은 (1) 케이스로 판정. (2) 케이스 추정:
- `ownership_structure` 3건 partial (KT&G 등)
- `value_up` 일부 (예전 제출 안 된 기업)

추가 샘플 또는 해석 보강은 다음 audit에서 진행.

## 개선 우선순위

### 우선순위 1 (즉시)
- 모든 tool에 `data.usage` 필드 표준 적용 (현재 3개만) — 사용량 투명성
- 에이피알 shareholder_meeting 이상치 재현/원인 파악

### 우선순위 2 (단기)
- `ownership_structure` partial 3건 실사례 검증
- `value_up` partial vs "계획 미제출" 구분 명확화 (이미 `availability_status` 있지만 테스트 보강)

### 우선순위 3 (중기)
- 정량 필드 채움률(field completeness) audit 추가 — 예: shareholder_meeting의 `agenda_items`가 몇 % 채워지는지
- 사용자가 실제로 쓰는 scope 조합별 audit (summary 외 board/results/changes 등)

## 재실행 스크립트

`/tmp/parsing_audit.py` (로컬만, 커밋 안 함). 향후 정기 audit 시 재사용.

## 관련

[[OpenProxy-MCP]] [[파서-판정-등급]] [[MCP-개발-lessons-learned]]
