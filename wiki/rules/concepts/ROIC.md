---
type: concept
title: ROIC (투하자본이익률)
tags: [financial, profitability]
related: [ROE, ROA, 순현금]
---

# ROIC (투하자본이익률)

## 개념

Return on Invested Capital. **세후영업이익(NOPAT) ÷ 투하자본**. 본업에 투입한 자본의 수익성. 교과서 정의의 투하자본은
「자기자본 + 순차입금」(현금 차감)이지만, **OPM 은 자본총계 + 총차입금**(현금 미차감)을 쓴다 — 아래 참조.

## 의미

- 영업 외 손익·금융손익을 걷어내고 **본업 자체의 자본 효율**만 봄
- ROIC > 자본비용(WACC)이면 가치 창출, 그 반대면 가치 파괴
- [[ROE]]와 달리 자본 구조 왜곡이 적어 사업 경쟁력 비교에 유리

## OPM에서의 처리

`financial_metrics`(`services/financial_metrics.py`)의 단순 근사:

```
NOPAT     = 영업이익 × (1 − 0.22)        # 한국 평균 법인세율 22% 고정
투하자본  = 자본총계(total_equity) + 총차입금(total_debt)   # 순차입금이 아니라 총차입 — 현금 미차감
ROIC(%)   = NOPAT ÷ 투하자본 × 100        # 투하자본 ≤ 0 이면 None
```

- 현금이 많은 회사는 교과서 정의(순차입금)보다 **ROIC 가 낮게** 나온다(분모가 크다). 비교할 때 이 차이를
  밝힌다. `tools/financial_metrics` 의 「ROIC 근사」 항목과 같은 산식이다.
- 총차입금의 인식 범위는 [[순현금]]과 같은 `total_debt` 를 쓴다.
