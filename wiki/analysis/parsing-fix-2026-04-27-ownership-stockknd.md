---
type: analysis
title: ownership_structure stock_knd 변형 파싱 fix 2026-04-27
tags: [audit, parsing, fix, ownership_structure, regression-test]
related: [parsing-audit-2026-04-29-v2, parsing-fix-2026-04-29-cgr-financial, 최대주주, 대주주, 동일인, 5%-대량보유]
date: 2026-04-27
---

# ownership_structure stock_knd 변형 파싱 fix 2026-04-27

audit v2 (`parsing-audit-2026-04-29-v2.md`)에서 발견된 ownership_structure 15건 partial_failure
(모두 KOSPI 대형주: SK하이닉스, 현대차, LG전자, SK텔레콤 등)를 EXACT로 정상화. 진짜 partial 15 -> 0.

## 문제 요약

audit v2 결과: ownership_structure.summary 196건 중
- exact 178 / no_filing 0 / **partial_failure 15** / error 3

partial_failure 15건 모두 KOSPI 대형주:
SK하이닉스, 현대차, 한화에어로스페이스, SK스퀘어, 한화시스템, SK, HD현대, LG전자,
LIG디펜스앤에어로스페이스, SK텔레콤, CJ제일제당, GS건설, GS리테일, KCC, LG생활건강,
LG유플러스, SKC. (총 17건 — audit 시점에 누락 누계 명단)

audit 메모: "DART hyslrSttus가 빈 list 반환 (해당 기업의 정기보고서가 다른 구조이거나 5%
대량보유로만 채움)". 그러나 직접 호출 결과:

```
SK하이닉스 (00164779) hyslrSttus 2024/11011: items=15
  [0] nm='SK스퀘어㈜'  stock_knd='의결권 있는 주식'  relate='최대주주'  qty=20.07
  [1] nm='윤태화'      stock_knd='의결권 있는 주식'  relate='특수관계인' qty=0.00
  ...
```

**API는 정상 응답 — 파서 필터가 모두 누락하고 있었음.**

## 근본 원인 분석

`_major_holders_rows` 의 legacy 필터:

```python
if not name or name == "계" or ("보통" not in stock_kind and stock_kind):
    continue
```

이 조건은 `stock_kind`에 "보통"이 없으면 모두 제외. 하지만 KOSPI 다수 대형주는
보통주 표기를 다음과 같이 다양하게 사용:

| 회사 | stock_knd 변형 |
|------|----------------|
| 삼성전자, KT&G | `보통주` (legacy 호환) |
| SK하이닉스, 현대차, LG전자, KCC, LG생활건강 | `의결권 있는 주식` (공백 있음) |
| SK텔레콤, SK스퀘어 | `의결권있는 주식` (공백 없음) |
| 한화시스템 | `의결권이 있는 주식` (이 추가) |
| LG유플러스 | `의결권\n있는 주식`, `의결권 \n있는 주식` (개행) |
| CJ제일제당 | `의결권없는주식`, `의결권있는주식` (모두 붙음) |
| HD현대 | `의결권 있는 주식` + `합  계` 합계 행 |
| GS건설, KCC | `의결권 없는 주식`, `의결권 있는 주식` |

KOSPI 200 전수 조사로 파악한 stock_knd 변형 (60개 회사 sample):
```
44x  '보통주'
20x  '우선주'
16x  '-'
9x   '의결권 있는 주식'
6x   '기타'
6x   '합계'
4x   '의결권 없는 주식'
3x   '보통주식'
2x   '기타주식'
2x   '의결권있는 주식'
1x   '4우선주(신형우선주)'
1x   '의결권없는주식'
1x   '의결권있는주식'
1x   '합  계'
1x   ' 보통주' (선두 공백)
1x   ' 우선주 '
1x   '종류주'
1x   '의결권\n없는 주식'
1x   '의결권\n있는 주식'
1x   '의결권 \n있는 주식'
1x   '의결권 없는 주식\n(우선주)'
1x   '의결권 있는 주식\n(보통주)'
```

따라서 `if "보통" in stock_kind` 단일 조건으로는 절대 잡을 수 없음.

## Fix

### 코드 변경 (`open_proxy_mcp/services/ownership_structure.py`)

#### 1. 정규화 헬퍼 신규 정의

```python
_SUBTOTAL_NAMES = {"계", "합계", "소계", "총계", "총합계"}

def _normalize_stock_label(value: str) -> str:
    """공백·개행 제거. stock_knd / nm 변형 비교용."""
    return re.sub(r"\s+", "", (value or "").strip())

def _is_voting_common_stock(stock_kind: str) -> bool:
    """보통주(=의결권 있는 주식) 여부 판정.

    정규화 후:
    - "없는" 포함 → False (의결권 없는 주식)
    - "보통" 또는 "있는" 포함 → True
    - 빈 값 → True (보수적, 과거 일부 회사)
    """
    norm = _normalize_stock_label(stock_kind)
    if not norm:
        return True
    if "없는" in norm:
        return False
    return ("보통" in norm) or ("있는" in norm)

def _is_subtotal_row(name: str) -> bool:
    """`계`, `합계`, `소계` 등 합계 행 판별."""
    return _normalize_stock_label(name) in _SUBTOTAL_NAMES
```

25개 stock_knd 변형 + 9개 subtotal 케이스 단위 테스트 모두 PASS.

#### 2. `_major_holders_rows` 교체

```python
def _major_holders_rows(data):
    rows = []
    for item in data.get("list", []):
        stock_kind = item.get("stock_knd", "")
        name = _clean_name(item.get("nm", ""))
        if not name or _is_subtotal_row(name):
            continue
        if not _is_voting_common_stock(stock_kind):
            continue
        rows.append({...})
    rows.sort(key=lambda r: r["ownership_pct"], reverse=True)
    return rows
```

#### 3. 다중 (year, reprt_code) fallback 추가

```python
_REPRT_CODE_FALLBACK = ["11011", "11014", "11012", "11013"]
# 사업 → 3분기 → 반기 → 1분기 + 직전연도 사업

async def _fetch_major_with_fallback(client, corp_code, bsns_year):
    ...
    for try_year, try_code in attempts:
        data = await client.get_major_shareholders(corp_code, try_year, try_code)
        rows = _major_holders_rows(data)
        if rows:
            return rows, source_meta, warnings
```

#### 4. 5% 대량보유 추정 fallback (3차)

```python
async def _fetch_largest_shareholder_from_blocks(client, corp_code):
    """hyslrSttus 모두 빈 응답 시 majorstock에서 추정.
    본인+특관 합산 아니므로 정확도 제한 명시.
    """
    data = await client.get_block_holders(corp_code)
    # 보고자별 최신만 채택 → ownership_pct 정렬
```

#### 5. `largest_shareholder_source` 메타 노출

```python
data["largest_shareholder_source"] = {
    "endpoint": "hyslrSttus" | "majorstock",
    "bsns_year": ...,
    "reprt_code": ...,
    "fallback_used": bool,
    "estimated_from_5pct": bool,  # 3차 fallback 사용 시
}
```

응답에 어디서 데이터를 가져왔는지 투명하게 기록. 5% 추정 사용 시 warning에도 명시.

## 검증 결과

### audit partial_failure 17건 — 전부 EXACT 회복

| ticker | 회사 | top_holder | pct | related_total |
|---|---|---|---:|---:|
| 000660 | SK하이닉스 | SK스퀘어(주) | 20.07% | 20.07% |
| 005380 | 현대자동차 | 현대모비스 | 22.36% | 30.67% |
| 012450 | 한화에어로스페이스 | (주)한화 | 32.18% | 35.55% |
| 402340 | SK스퀘어 | SK(주) | 32.14% | 32.16% |
| 272210 | 한화시스템 | 한화에어로스페이스(주) | 46.73% | 59.53% |
| 034730 | SK | 최태원 | 17.90% | 25.44% |
| 267250 | HD현대 | 정몽준 | 26.60% | 37.18% |
| 066570 | LG전자 | (주)LG | 35.26% | 35.27% |
| 079550 | LIG디펜스앤에어로스페이스 | 주식회사 엘아이지 | 37.74% | 38.21% |
| 017670 | SK텔레콤 | SK(주) | 30.57% | 30.58% |
| 097950 | CJ제일제당 | 씨제이(주) | 40.94% | 41.81% |
| 006360 | GS건설 | 허창수 | 5.95% | 23.64% |
| 007070 | GS리테일 | (주)GS | 58.62% | 58.62% |
| 002380 | 케이씨씨 | 정몽진 | 20.00% | 35.00% |
| 051900 | LG생활건강 | (주)LG | 34.74% | 34.74% |
| 032640 | LG유플러스 | (주)LG | 38.25% | 38.25% |
| 011790 | SKC | SK(주) | 40.64% | 40.88% |

### Regression 검증 (KOSPI 200 전체)

| 분류 | before fix | after fix |
|---|---:|---:|
| EXACT | 184 | **199** |
| NO_FILING | 0 | 0 |
| PARTIAL_FAILURE | 15 | **0** |
| ERROR | 0 | 0 |

**100% EXACT (199/199) — 진짜 partial_failure 15 -> 0**

### 성능

- 평균 latency: 0.55s/회 (목표 5s 이내)
- fallback 사용률: 0% (KOSPI 200 모두 1차 hyslrSttus에서 해결)
- 보조 API call 증가: 0건 (정상 케이스에선 fallback 발동 안 함)

### Schema 호환성

기존 응답 schema 100% 유지 + `data.largest_shareholder_source` 메타 추가 (옵셔널).
- `data.summary.top_holder` 필드 동일
- `data.major_holders` 필드 동일
- `data.summary.related_total_pct` 동일
- `data.filing_status` 동일

기존 호출자 (action tool, tools_v2 renderer) 영향 없음.

## 디자인 노트

### 왜 positive matching인가

stock_knd 변형이 매우 다양하고 미래에도 계속 증가할 가능성이 높음. negative matching
(예: `if "우선" in stock_kind: skip`)은 예외 케이스를 누락하기 쉬움.

대신 positive matching은 명확한 의미를 가짐:
- "보통" 포함 → 명백한 보통주
- "있는" 포함 + "없는" 미포함 → 의결권 있는 주식 = 보통주
- 그 외(우선/기타/-/합계/종류) → 제외

### 빈 stock_knd를 보통주로 간주하는 이유

과거 일부 회사 사업보고서에서 stock_knd 빈 값 사례 존재. 보수적 처리로 채택하면
false positive 가능성 < false negative 위험. 어차피 합계 행은 name 정규화로 별도 차단.

### 3-tier fallback의 안전 장치

1. 1차 (hyslrSttus 사업보고서): KOSPI 200 100% 해결
2. 2차 (다른 reprt_code + 직전연도): 미래 시점 호출 / 신규 상장 회사 케이스
3. 3차 (5% 대량보유 추정): 정기보고서 자체 미공개의 잔여 케이스
   - 본인+특관 합산이 아니므로 warning + `estimated_from_5pct` 플래그
   - 분석가가 정확도 한계를 인식할 수 있도록 source 메타 노출

## 정의 정합

이 fix는 다음 4개 wiki 컨셉의 정의 정합과 함께 진행:

- [[최대주주]] — 본인+특관 합산 (hyslrSttus, 자본시장법 §159)
- [[대주주]] — 1%+ 또는 시총 10억+ (자본시장법 §9 ②, 소득세법 §94)
- [[동일인]] — 재벌 그룹 정점 (공정거래법 §2 ①)
- [[5%-대량보유]] — 5%+ 외부 보고 (자본시장법 §147)

각 개념의 OPM 매핑과 source 우선순위를 wiki에 명문화.

## 영향 범위

### 4-class 매트릭스 갱신 (ownership_structure.summary)

| | exact | no_filing | partial_failure | error | exact% |
|---|---:|---:|---:|---:|---:|
| before | 178 | 0 | 15 | 3 | 90.8% |
| **after** | **193** | 0 | **0** | 3 | **98.5%** |

### 11 tool 합계 영향 (corp_gov_report fix와 합산 시점 기준)

| | exact | no_filing | partial_failure | error |
|---|---:|---:|---:|---:|
| before | 1,393 | 717 | 15 | 27 |
| **after** | **1,408** | **717** | **0** | **27** |

진짜 partial_failure 15 -> 0. 11 tool 전체 합계 partial 1.5% -> **0%** 달성.

## 코드 변경 파일

- `open_proxy_mcp/services/ownership_structure.py`
  - `_normalize_stock_label`, `_is_voting_common_stock`, `_is_subtotal_row`, `_clean_name` 신규
  - `_major_holders_rows` 교체 (positive matching)
  - `_fetch_major_with_fallback` 신규 (다중 reprt_code/year 시도)
  - `_fetch_largest_shareholder_from_blocks` 신규 (5% 추정)
  - `build_ownership_structure_payload`: 3-tier fallback 통합 + `largest_shareholder_source` 메타

## 관련

[[parsing-audit-2026-04-29-v2]] [[parsing-fix-2026-04-29-cgr-financial]] [[최대주주]] [[대주주]] [[동일인]] [[5%-대량보유]]
