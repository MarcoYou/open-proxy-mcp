---
type: analysis
title: Cross-domain Tool 체이닝
tags: [architecture, tool-chaining, agm, own, div]
related: [OpenProxy-MCP, agm-tool-rule, own-tool-rule, div-tool-rule]
---

# Cross-domain Tool 체이닝

## 개요

OPM의 3개 도메인(AGM/OWN/DIV)은 독립적으로도 작동하지만, **도메인을 넘나드는 분석**이 거버넌스의 핵심.
AI가 tool을 선택할 때 cross-domain ref를 통해 연관 tool을 자연스럽게 체이닝.

## 체이닝 맵

### AGM → OWN
| AGM tool | OWN tool | 시나리오 |
|----------|---------|---------|
| [[agm-tool-rule\|agm_result]] | [[own-tool-rule\|own_block]] | 프록시 파이트: 투표결과 + 5% 대량보유자 의결권 행사 |
| agm_result | own_major | [[참석률]] 역산 시 최대주주 지분 참조 |
| agm_treasury_share_xml | own_treasury | 주총 안건(자사주) vs 사업보고서 baseline 비교 |

### AGM → DIV
| AGM tool | DIV tool | 시나리오 |
|----------|---------|---------|
| agm_compensation_xml | div_detail | [[보수한도]] 소진율 + 배당 정책 연계 (임원 보상 vs 주주환원) |
| agm_personnel_xml | agm_result | 후보자(공고) vs 실제 선임(결과) 비교 |

### OWN → AGM/DIV
| OWN tool | 연결 tool | 시나리오 |
|----------|---------|---------|
| own_block | agm_result | 경영참여 목적 보유자 → 주총에서 어떻게 투표했는지 |
| own_treasury_tx | div_detail | [[자사주]] 소각 → [[배당성향]] 변화 연계 (총 주주환원) |

### DIV → AGM/OWN
| DIV tool | 연결 tool | 시나리오 |
|----------|---------|---------|
| div_detail | own_total | 배당총액 / 발행주식수 = DPS 검증 |
| div_detail | agm_financials_xml | [[배당성향]] 검증 시 지배주주 당기순이익 필요 |
| div_history | own_treasury_tx | 자사주 매입 + 배당 = 총 주주환원 규모 분석 |

## 사용 예시

### "삼성전자 주주환원 정책 분석해줘"
```
1. div_history("삼성전자") → 배당 추이 (3년)
2. own_treasury_tx("삼성전자") → 자사주 매입/소각 이벤트
3. → 배당 + 자사주 = 총 주주환원 규모 산출
```

### "고려아연 프록시 파이트 분석"
```
1. own_block("고려아연") → 5% 대량보유자 + 보유목적 (경영참여)
2. agm_result("고려아연") → 주총 투표결과 (집중투표 득표율)
3. own_major("고려아연") → 최대주주+특관인 합산 지분
```

### "현대차 보수한도 적정성"
```
1. agm_compensation_xml(rcept_no) → 보수한도 + 소진율
2. div_detail("현대차") → 배당성향 → 임원 보상 vs 주주환원 비교
3. agm_financials_xml(rcept_no) → 당기순이익 대비 보수 비율
```
