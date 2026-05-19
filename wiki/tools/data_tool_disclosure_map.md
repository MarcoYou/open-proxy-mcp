---
type: tool_index
title: data-tool-disclosure-map
updated: 2026-05-20
related_tools:
  - corp_gov_report
  - corporate_restructuring
  - dilutive_issuance
  - dividend
  - financial_metrics
  - ownership_structure
  - proxy_contest
  - related_party_transaction
  - treasury_share
  - value_up
---

# Data Tool Disclosure Map

이 문서는 README의 `Data Tools` 요약을 보완하는 상세 공시 매핑입니다.

## 알파벳순 요약

| tool | 주요 공시 유형 | 비고 |
|---|---|---|
| `corp_gov_report` | 기업지배구조보고서 | 지배구조 핵심지표, 원문 표 파싱 |
| `corporate_restructuring` | 회사합병결정, 회사분할결정, 회사분할합병결정, 주식교환·이전결정 | DART 주요사항보고서 DS005 4종 |
| `dilutive_issuance` | 유상증자결정, 전환사채발행결정, 신주인수권부사채발행결정, 감자결정 | 희석성 발행과 자본감소 이벤트 |
| `dividend` | 현금배당결정, 주식배당결정, 배당기준일결정, 분기배당결정, 사업보고서 | 배당 결정 공시와 사업보고서 배당 요약을 함께 사용 |
| `financial_metrics` | 사업보고서, 반기보고서, 분기보고서 | DART 재무제표 endpoint와 감사의견 endpoint |
| `ownership_structure` | 대량보유상황보고서, 임원·주요주주특정증권등소유상황보고서, 사업보고서, 최대주주등소유주식변동신고서 | 최대주주, 5% 보유, 임원·주요주주, control map |
| `proxy_contest` | 위임장권유참고서류, 소송등의제기, 경영권분쟁소송, 대량보유상황보고서, 주주총회결과 | 위임장, 소송, 5% 활성 신호, vote math |
| `related_party_transaction` | 타법인주식및출자증권거래, 단일판매공급계약체결 | 내부거래와 일감몰아주기 모니터링 |
| `treasury_share` | 자기주식취득결정, 자기주식처분결정, 자기주식소각결정, 자기주식신탁결정, 사업보고서 | 결정, 결과보고서, 신탁, 연간 잔고·소각 |
| `value_up` | 기업가치제고계획, 자기주식취득결정, 자기주식소각결정 | 기업가치제고계획과 자사주 이행 cross-reference |

## 관련 문서

- [[README]]
- [[corp_gov_report]]
- [[corporate_restructuring]]
- [[dilutive_issuance]]
- [[dividend]]
- [[financial_metrics]]
- [[ownership_structure]]
- [[proxy_contest]]
- [[related_party_transaction]]
- [[treasury_share]]
- [[value_up]]
