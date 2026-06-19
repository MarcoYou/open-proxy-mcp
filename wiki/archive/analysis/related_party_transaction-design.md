---
type: analysis
title: related_party_transaction data tool 설계 + 전수조사
tags: [data-tool, related-party, insider-dealing, 일감몰아주기, dart]
related: [OpenProxy-MCP, 타법인주식및출자증권거래, 단일판매공급계약체결]
date: 2026-04-21
---

# related_party_transaction 설계

타법인주식 거래 + 단일판매·공급계약을 통합한 **내부거래 모니터링 tool**. 14 → 15번째 tool, Data Tools 10개째.

## 동기

일감몰아주기·특수관계인 거래는 DART에 **전용 구조화 API 없음**. list.json + 키워드 매칭으로만 수집 가능. 다만 두 도메인 공시가 함께 읽혀야 내부거래 패턴이 보여서 단일 tool로 묶음:

- **타법인주식 거래**: 그룹 내 출자 구조 변경. 자회사 지분 확대/축소, 관계회사 편입.
- **단일판매·공급계약**: 매출 기반 거래. 계열사간 반복 계약 = 일감몰아주기.

## 한계 (design decision)

구조화 API가 없어서 **list.json 메타만 수집**. 거래 상대방 · 금액 · 특수관계 여부는 원문 파싱 필요. 이 원문 파싱은 본 tool에서 제공하지 않고 `evidence` tool + DART 뷰어로 drill-down.

타 data tool과 달리 본 tool은 "timeline + 플래그"에 집중.

## scope

| scope | 내용 |
|-------|------|
| `summary` | 두 도메인 통합 timeline |
| `equity_deal` | 타법인주식 거래 (양수/양도/취득/처분) |
| `supply_contract` | 단일판매·공급계약 (체결/해지) |

## 키워드 매칭

```python
_EQUITY_DEAL_KEYWORDS = (
    "타법인주식및출자증권양수결정", "타법인주식및출자증권양도결정",
    "타법인주식및출자증권취득결정", "타법인주식및출자증권처분결정",
)
_SUPPLY_CONTRACT_KEYWORDS = (
    "단일판매ㆍ공급계약체결", "단일판매ㆍ공급계약해지",
    "단일판매·공급계약체결", "단일판매·공급계약해지",
)
```

pblntf_tys:
- 타법인주식: `("B", "I")` — B(주요사항보고서) + I(거래소공시) 양쪽 제출
- 단일공급계약: `("I",)` — 거래소공시만

## 노출 플래그 (list.json 응답에서 추출)

| 플래그 | 의미 | 거버넌스 시사점 |
|--------|------|----------------|
| `subsidiary_report` | report_nm에 "자회사의주요경영사항" 포함 | 모회사가 비상장 자회사 거래 대신 공시 = 그룹 내 연결 거래 가능성 |
| `autonomous_disclosure` | report_nm에 "자율공시" 포함 | 의무 미달이지만 투명성 의지 |
| `is_correction` | `[기재정정]` prefix | 초기 공시 품질 의심 |
| `direction` (equity_deal) | acquire / dispose | 취득: 지분 확대, 처분: 지분 축소 |
| `direction` (supply_contract) | conclude / terminate | 체결: 거래 시작, 해지: 거래 종료 |

## 전수조사 결과 (2026-04-21)

| 회사 | scope | 결과 | 비고 |
|------|-------|------|------|
| POSCO홀딩스 | summary | exact, 3건 | 모두 자회사 주요경영사항 — 지주회사 구조 신호 |
| 삼성전자 | summary | exact, 2건 | 단일공급계약 체결만 |
| SK하이닉스 | supply_contract | partial | 사건 없음 (반도체도 매출 5% 미만 계약이면 미공시) |
| 현대건설 | supply_contract | exact, 72건 | 건설업 특성 (체결 71 / 해지 1) |
| 일진홀딩스 | equity_deal | exact, 1건 | 처분 |
| 성호전자 | equity_deal | exact, 9건 | 모두 양수 — M&A 활발 |
| 두나무 | summary | error | 비상장 (정상) |

**5/5 통과** (사용 가능한 케이스 기준)

## 거버넌스 분석에서의 의의

1. **POSCO홀딩스 패턴**: 자회사 주요경영사항 공시 비중 → 지주회사 구조 투명성
2. **현대건설 패턴**: 건설업 특성상 단일계약 빈번. 그룹 계열사 비중이 체크포인트 (원문 파싱 필요)
3. **타법인주식 반복 취득**: 지배구조 점진 재편 신호
4. **자율공시 비중**: 의무 기준 미달 거래도 자발 공시 → 거버넌스 등급 ↑

## 한계와 후속 작업

1. **원문 파싱 미수행**: 거래 상대방/금액/특수관계 여부는 list.json에 없음. evidence tool로 개별 확인.
2. **특수관계 자동 판별 없음**: 계열사 matrix가 OPM에 저장 안 됨. 후속 개선 영역.
3. **단일공급계약 매출 의존도 계산**: 원문 파싱 + 재무 데이터 결합 필요. 향후 `financial_metrics` tool과 연계.

## next action

- screen_events에 `related_party_equity_deal`, `supply_contract` event_type 추가 가능
- 원문 파싱 보강으로 거래 상대방·금액 추출 (phase 2)
- 계열사 matrix 데이터 소스 확보 시 특수관계 자동 판별 추가
