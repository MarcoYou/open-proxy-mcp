# OWN Case Rule -- 지분 tool 성공/실패 판정 기준

## 판정 등급

| 등급 | 의미 | AI 행동 |
|------|------|---------|
| SUCCESS | 핵심 데이터 충족 | 테이블 형태로 정리하여 답변 |
| SOFT_FAIL | 일부 누락 | 있는 데이터로 답변 + 누락 안내 |
| HARD_FAIL | 핵심 데이터 없음 | 다른 tool 시도 또는 안내 |

---

## 1. own_major -- 최대주주 + 특수관계인

### SUCCESS 예시 — 삼성전자

```json
{
  "list": [
    {"nm": "삼성생명보험(주)", "relate": "최대주주 본인", "trmend_posesn_stock_qota_rt": "8.58", "stock_knd": "보통주"},
    {"nm": "삼성물산(주)", "relate": "계열회사", "trmend_posesn_stock_qota_rt": "5.05", "stock_knd": "보통주"},
    {"nm": "이재용", "relate": "특수관계인", "trmend_posesn_stock_qota_rt": "1.65", "stock_knd": "보통주"}
  ]
}
```

### 판정

| 등급 | 기준 |
|------|------|
| SUCCESS | list >= 1, 최대주주(본인) 존재, 지분율 양수 |
| SOFT_FAIL | 최대주주만 있고 특수관계인 없음 |
| HARD_FAIL | list 비어있음 (사업보고서 미제출 기업) |

---

## 2. own_total -- 주식총수

### SUCCESS 예시

```json
{
  "list": [
    {"se": "보통주", "istc_totqy": "5,969,782,550", "tesstk_co": "78,836,180", "distb_stock_co": "5,890,946,370"}
  ]
}
```

### 판정

| 등급 | 기준 |
|------|------|
| SUCCESS | 보통주 행 존재, istc_totqy > 0 |
| SOFT_FAIL | 우선주만 있고 보통주 없음 |
| HARD_FAIL | list 비어있음 |

---

## 3. own_treasury -- 자사주

### 판정

| 등급 | 기준 |
|------|------|
| SUCCESS | 기말 보유 수량 존재 |
| SOFT_FAIL | 수량 0 (자사주 없는 기업이면 정상) |
| HARD_FAIL | 데이터 없음 |

---

## 4. own_treasury_tx -- 자사주 이벤트

### 판정

| 등급 | 기준 |
|------|------|
| SUCCESS | 이벤트 >= 1 (취득/처분/신탁) |
| SOFT_FAIL | - |
| HARD_FAIL | 이벤트 0건 (최근 이벤트 없는 기업이면 정상) |

---

## 5. own_block -- 5% 대량보유

### SUCCESS 예시

```json
{
  "list": [
    {"repror": "국민연금공단", "stkrt": "11.52", "rcept_dt": "20260115", "report_resn": "주식등의 대량보유상황보고서"}
  ]
}
```

보유목적: 원문 파싱으로 추출 (경영권 참여/단순투자/일반투자)

### 판정

| 등급 | 기준 |
|------|------|
| SUCCESS | list >= 1, 보유목적 파싱 성공 |
| SOFT_FAIL | 보유목적이 "불명" |
| HARD_FAIL | list 비어있음 (5% 이상 보유자 없는 기업이면 정상) |

---

## 6. own_latest -- 통합 스냅샷

own_major + own_block + 임원 소유를 합산. 3개 API 호출.

### 판정

| 등급 | 기준 |
|------|------|
| SUCCESS | major + block 데이터 모두 존재 |
| SOFT_FAIL | 한쪽만 존재 |
| HARD_FAIL | 양쪽 모두 비어있음 |

---

## 공통 규칙

- 안건이 없어서 비어있는 것은 실패가 아님 (예: 5% 보유자 없는 기업)
- 보통주 기준으로 지분율 계산 (우선주 별도 표시)
- 사업보고서 기준일(stlm_dt)과 수시 공시일(rcept_dt)을 구분
- "계" 행은 합산에서 제외 (중복 카운팅 방지)
