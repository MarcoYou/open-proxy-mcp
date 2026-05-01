---
type: decision
title: 공시 검색 시 pblntf_ty 필터 필수 — 전체 순회 금지
tags: [decision, dart, search, performance]
date: 2026-04-11
---

# 공시 검색 시 pblntf_ty 필터 필수

## 결정

DART `list.json` 검색 시 `pblntf_ty`(공시 유형 코드)를 반드시 지정할 것.
**전체 공시를 페이지 순회한 후 키워드 필터링하는 방식 금지.**

## 이유

DART `list.json`은 기본적으로 `page_count=100` 한도가 있음.
활발한 기업(고려아연, 금융지주 등)은 연간 공시가 100건을 초과하므로
`pblntf_ty` 없이 전체 순회하면 원하는 공시가 2페이지 이후에 있어 누락됨.

**발견 케이스 (2026-04-11):**
- `prx_search`에서 고려아연 2025 위임장 공시 누락
  → `pblntf_ty='D'` 지정 후 정상 탐지
- `div_search`에서 `pblntf_ty` 없이 전체 순회
  → `pblntf_ty='I'` 지정 후 해결

## pblntf_ty 코드표

| 코드 | 분류 | 해당 공시 |
|------|------|-----------|
| `A` | 정기공시 | 사업보고서, 반기/분기보고서 |
| `B` | 주요사항보고 | 자기주식취득결정, 합병 등 |
| `C` | 발행공시 | 증권신고서 |
| `D` | 지분공시 | **5% 대량보유, 위임장권유참고서류, 공개매수** |
| `E` | 기타공시 | **주총 소집공고**, AGM 관련 |
| `I` | 거래소공시 | **현금배당결정, 배당락, 기준일결정** |

## 도메인별 적용

| Tool | pblntf_ty | 이유 |
|------|-----------|------|
| `agm_search` | `E` | 주총 소집공고 = 기타공시 |
| `prx_search` / `prx_fight` | `D` | 위임장/공개매수 = 지분공시 |
| `div_search` | `I` | 배당결정 = 거래소공시 |

## 키워드 상수 패턴

pblntf_ty로 범위를 먼저 좁힌 뒤 키워드로 추가 필터:

```python
_DIV_KEYWORDS = ("현금ㆍ현물배당결정", "현금배당결정", "분기ㆍ중간배당결정", ...)
_PROXY_KEYWORDS = ("의결권대리행사권유", "공개매수신고서", ...)

# 검색
result = await client.search_filings(corp_code=..., pblntf_ty="I", ...)
matches = [i for i in result["list"] if any(kw in i["report_nm"] for kw in _DIV_KEYWORDS)]
```

## 관련

[[대량보유상황보고서]] [[위임장권유참고서류]] [[현금배당결정]] [[배당공시유형]]
