# DIV Case Rule -- 배당 tool 성공/실패 판정 기준

## 판정 등급

| 등급 | 의미 | AI 행동 |
|------|------|---------|
| SUCCESS | 핵심 배당 데이터 충족 | 테이블로 정리하여 답변 |
| SOFT_FAIL | 일부 누락 (배당성향/수익률 계산 불가) | 있는 데이터로 답변 + 누락 안내 |
| HARD_FAIL | 배당 데이터 없음 | 배당 미실시 또는 미공시 안내 |

---

## 1. div_detail -- 배당 상세

### SUCCESS 예시

```json
{
  "corp_name": "삼성전자",
  "bsns_year": "2025",
  "period": "사업보고서(기말)",
  "cash_dps": 1444,
  "total_dps": 1444,
  "total_amount": 9739849000000,
  "payout_ratio_dart": 45.2,
  "yield_dart": 2.1
}
```

### 판정

| 등급 | 기준 |
|------|------|
| SUCCESS | cash_dps > 0, total_amount > 0 |
| SOFT_FAIL | cash_dps > 0이지만 payout_ratio/yield 없음 |
| HARD_FAIL | 데이터 없음 (배당 미실시 기업이면 정상) |

---

## 2. div_history -- 배당 이력

### SUCCESS 예시

```json
{
  "year": "2025",
  "annual_dps": 1444,
  "final_dps": 1444,
  "quarterly": [],
  "payout_ratio_dart": 45.2,
  "yield_dart": 2.1
}
```

### 분기배당 기업 예시

```json
{
  "year": "2025",
  "annual_dps": 3600,
  "final_dps": 1200,
  "quarterly": [
    {"period": "1Q", "dps": 600},
    {"period": "반기", "dps": 600},
    {"period": "3Q", "dps": 1200}
  ],
  "special_dps": 0
}
```
→ 연간 DPS = 600 + 600 + 1200 + 1200 = 3,600원

### 판정

| 등급 | 기준 |
|------|------|
| SUCCESS | 2년+ 데이터 있음, annual_dps > 0 |
| SOFT_FAIL | 1년만 있음, 또는 배당성향/수익률 계산 불가 |
| HARD_FAIL | 전 기간 데이터 없음 |

---

## 3. div_search -- 배당 공시 검색

### 판정

| 등급 | 기준 |
|------|------|
| SUCCESS | 배당 관련 공시 >= 1건 |
| HARD_FAIL | 0건 (div_history로 사업보고서 기반 조회 안내) |

---

## 연산 주의사항

### 배당성향 계산 시
- **지배주주 귀속 당기순이익** 사용 (연결재무제표)
- ❌ 별도 재무제표 당기순이익 사용 금지
- ❌ 비지배지분 포함 당기순이익 사용 금지
- 당기순이익이 적자면 배당성향 = "적자 배당" 표시 (음수% 아닌 문자열)

### 배당수익률 계산 시
- KRX 종가가 없으면 (API 미승인/비거래일) → DART 시가배당률 사용
- 둘 다 없으면 → 배당수익률 "-" 표시

### 특별배당 주의
- 특별배당은 일회성 → 배당 추이 분석 시 정기배당과 분리 해석
- "배당성향" 계산에는 포함하되, 주석으로 "특별배당 포함" 명시
