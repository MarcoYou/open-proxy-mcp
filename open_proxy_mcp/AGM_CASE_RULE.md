# Case Rule -- 파서별 성공/실패 판정 기준

## 파싱 성능 (KOSPI 200 샘플, 각 tier 독립 성능)

| 파서 | XML | PDF | OCR |
|------|-----|-----|-----|
| agenda | 99.5% | 98.0% | 100% |
| financials BS | 97.4% | 97.9% | 100% |
| financials IS | 100% | 95.7% | 100% |
| personnel | 98.9% | 97.9% | 100% |
| aoi (정관변경) | 97.8% | 99.0% | 100% |
| compensation | 98.4% | 99.5% | 100% |

## 판정 등급

| 등급 | 의미 | AI 행동 |
|------|------|---------|
| SUCCESS | 필수 필드 충족, 형태 정상 | 답변 (AI가 포맷 보정 가능) |
| SOFT_FAIL | 일부 누락 또는 형태 이상 | AI 보정 시도 → 실패 시 PDF fallback 제안 |
| HARD_FAIL | 핵심 데이터 없음 | PDF fallback 제안 |

---

## 1. agenda -- 안건 목록

모든 소집공고에 반드시 존재. 제N호/제N-M호 형식의 안건 트리.

### SUCCESS 예시 — 삼성전자

```json
[
  {
    "number": "제1호",
    "title": "정관 일부 변경의 건",
    "children": [
      {"number": "제1-1호", "title": "집중투표제 배제 조항 삭제", "children": []},
      {"number": "제1-2호", "title": "개정 상법 반영", "children": []}
    ]
  },
  {"number": "제2호", "title": "재무제표 승인의 건", "children": []},
  {"number": "제3호", "title": "사내이사 김용관 선임의 건", "children": []}
]
```

### 필수 필드
- `number`: "제N호" 또는 "제N-M호" 정규식 정상
- `title`: 2-150자, 안건 핵심 내용만 (테이블/조문 텍스트 혼입 안 됨)
- `children`: 배열 (세부의안 재귀)

### 판정

| 등급 | 기준 |
|------|------|
| SUCCESS | items >= 1, title 2-150자, number 정규식 정상, 중복 없음 |
| SOFT_FAIL | title 150-200자, title에 `\|` 포함, 안건 1개만 |
| HARD_FAIL | items 0개, title > 200자, 중복 number |

---

## 2. financials -- 재무제표

BS(재무상태표) + IS(손익계산서). 연결/별도 각각 존재 가능.

### SUCCESS 예시 — 삼성전자

```json
{
  "consolidated": {
    "balance_sheet": {
      "unit": "백만원",
      "columns": ["account", "current", "prior"],
      "rows": [
        ["자산", "", ""],
        ["Ⅰ. 유동자산", "247,684,612", "227,062,266"],
        ["1. 현금및현금성자산", "57,856,378", "53,705,579"]
      ]
    }
  }
}
```

### 필수 필드
- `unit`: 존재 필수 (숫자 해석에 필수)
- `rows`: 숫자는 쉼표 포함 문자열. 실제 값 = 숫자 x 단위
- 핵심 계정: 자산총계/부채총계/자본총계 (BS), 매출액/영업이익/당기순이익 (IS)

### 판정

| 등급 | 기준 |
|------|------|
| SUCCESS | BS rows >= 5 + IS rows >= 3, unit 존재, 핵심 계정 포함 |
| SOFT_FAIL | BS만 있고 IS 없음, rows 부족, 핵심 계정 누락 |
| HARD_FAIL | BS/IS 모두 None |

---

## 3. personnel -- 이사/감사 선임

후보자별: 성명, 생년월일, 직위, 경력, 결격사유, 추천사유, 직무수행계획.

### SUCCESS 예시 — 삼성전자 허은녕

```json
{
  "name": "허은녕",
  "roleType": "사외이사",
  "careerDetails": [
    {"period": "1996 ~ 현재", "content": "서울대학교 공과대학/공학전문대학원 교수"},
    {"period": "2013 ~ 2015", "content": "국민경제자문회의 민간위원"},
    {"period": "2016 ~ 2022", "content": "LX인터내셔널(전 LG상사) 사외이사"},
    {"period": "2017 ~ 2018", "content": "세계에너지경제학회 (IAEE) 부회장"},
    {"period": "2018 ~ 2022", "content": "한국혁신학회 회장"},
    {"period": "2020 ~ 현재", "content": "한국공학한림원 정회원(기술경영정책)"},
    {"period": "2022 ~ 현재", "content": "한국에너지법연구소 원장"}
  ]
}
```
→ 7건, 모두 content < 100자, period 정상.

### SOFT_FAIL 예시 — KCC 손준성 (경력 병합)

XML 파싱 결과 (병합):
```json
{
  "name": "손준성",
  "birthDate": "1974.03.21",
  "roleType": "사외이사",
  "mainJob": "변호사 손준성 법률사무소 변호사",
  "careerDetails": [{
    "period": "2016 ~ 2025",
    "content": "現 변호사 손준성 법률사무소 변호사前 대구고등검찰청 차장검사前 서울고등검찰청 송무부장검사前 대구고등검찰청 인권보호관前 대검찰청 수사정보담당관前 대검찰청 수사정보정책관前 춘천지방검찰청 원주지청장前 광주지방검찰청 형사제2부장검사前 서울중앙지방검찰청 형사제7부장검사前 대검찰청 정책기획과장前 서울서부지방검찰청 형사제5부장검사"
  }]
}
```
content 278자, 11개 경력이 한 줄로 병합. period는 전체 기간 하나뿐.

AI 자체 보정 (現/前 구분자로 분리):
```json
{
  "name": "손준성",
  "birthDate": "1974.03.21",
  "roleType": "사외이사",
  "mainJob": "변호사 손준성 법률사무소 변호사",
  "careerDetails": [
    {"period": "", "content": "변호사 손준성 법률사무소 변호사"},
    {"period": "", "content": "대구고등검찰청 차장검사"},
    {"period": "", "content": "서울고등검찰청 송무부장검사"},
    {"period": "", "content": "대구고등검찰청 인권보호관"},
    {"period": "", "content": "대검찰청 수사정보담당관"},
    {"period": "", "content": "대검찰청 수사정보정책관"},
    {"period": "", "content": "춘천지방검찰청 원주지청장"},
    {"period": "", "content": "광주지방검찰청 형사제2부장검사"},
    {"period": "", "content": "서울중앙지방검찰청 형사제7부장검사"},
    {"period": "", "content": "대검찰청 정책기획과장"},
    {"period": "", "content": "서울서부지방검찰청 형사제5부장검사"}
  ]
}
```
AI가 "現"/"前" 구분자로 11건 분리. 원본에 개별 기간이 없어 period는 빈 문자열.
분리 자체가 어렵거나 구분자가 불명확하면 유저에게 PDF fallback 제안.

### HARD_FAIL 예시 — 이름이 조문번호

```json
{"name": "제 31 조 (이사의 선임) 이사는 주주총회에서 선임한다..."}
```
→ 정관 텍스트가 이름으로 잡힌 케이스.

### 필수 필드
- `name`: 한글 2-5자 (영문 병기 시 10자 이내). 조문번호/안건번호가 아닐 것
- `careerDetails`: >= 1건, 각 `content` <= 100자 (한 줄에 하나의 직책)

### 판정

| 등급 | 기준 |
|------|------|
| SUCCESS | candidates >= 1, name 2-10자, careerDetails >= 1, content <= 100자 |
| SOFT_FAIL | careerDetails 빈 배열, content > 100자 (병합), birthDate 누락 |
| HARD_FAIL | candidates 0개, name이 조문/안건번호 |

---

## 4. aoi_change -- 정관변경

변경 조항별: 현행(변경전), 변경후, 변경 사유.

### SUCCESS 예시 — 삼성전자 (집중투표제 배제 조항 삭제)

```json
{
  "amendments": [{
    "subAgendaId": "1-1",
    "label": "집중투표제 배제 조항 삭제",
    "clause": "제24조",
    "before": "제24조 (이사의 선임) ⑥ 2인 이상의 이사를 선임하는 경우에는 상법 제382조의2에서 규정하는 집중투표제를 적용하지 아니한다.",
    "after": "제24조 (이사의 선임) ⑥ <삭제>",
    "reason": "집중투표제 배제 조항 삭제 ※ 상법 시행시기 ('26.9.10자) 고려, 부칙에 경과규정 마련"
  }]
}
```

### 필수 필드
- `before`/`after`: 5자+ 실제 조문 텍스트. `<삭제>`, `------생략`은 정상.
- `clause`: `제N조` 형식

### 판정

| 등급 | 기준 |
|------|------|
| SUCCESS | amendments >= 1, before/after 5자+, clause에 `제N조` |
| SOFT_FAIL | clause 누락, reason 누락, subAgendaId 누락 |
| HARD_FAIL | amendments 0개, before/after 모두 빈값 |

---

## 5. compensation -- 보수한도

당기 한도, 전기 실지급, 전기 한도, 이사 수, 소진율.

### SUCCESS 예시 — 삼성전자

```json
{
  "currentLimit": 45000000000,
  "currentLimitDisplay": "450억원",
  "currentHeadcount": "8(5)",
  "directorCount": 8,
  "outsideDirectorCount": 5,
  "previousLimit": 36000000000,
  "previousActualPaid": 28700000000,
  "utilizationPct": 79.7,
  "target": "이사"
}
```
→ 모든 필드 충족. 소진율 79.7% 계산 가능.

### SOFT_FAIL 예시 — 한미사이언스 (전기 데이터 누락)

```json
{
  "currentLimit": 5000000000,
  "currentHeadcount": "10(3)",
  "previousLimit": null,
  "previousActualPaid": null,
  "utilizationPct": null
}
```
→ 당기 한도는 있지만 전기 비교 불가. 소진율 분석 불가능.

### 필수 필드
- `currentLimit`: 양수 정수 (원 단위)
- `currentHeadcount`: "N(M)" 형식 (이사수/사외이사수)
- `previousActualPaid` + `previousLimit`: 소진율 계산용

### 판정

| 등급 | 기준 |
|------|------|
| SUCCESS | currentLimit > 0, headcount 존재, previous에 금액 |
| SOFT_FAIL | currentLimit 있지만 previous 전부 null |
| HARD_FAIL | currentLimit 없음 (안건 있는데 파싱 실패) |

---

## 6. treasury_share -- 자사주

보유/처분/소각 수량, 취득방법, 기간.

### 필수 필드
- 수량 (shares): 양수 정수
- 목적/방법: 문자열

### 판정

| 등급 | 기준 |
|------|------|
| SUCCESS | 수량 >= 1, 목적/방법 존재 |
| SOFT_FAIL | 수량만 있고 목적/방법 누락 |
| HARD_FAIL | 데이터 없음 (안건 있는데 파싱 실패) |

---

## 7. capital_reserve -- 자본준비금

감액 금액, 전입 대상, 상태.

### 판정

| 등급 | 기준 |
|------|------|
| SUCCESS | 금액 존재, 전입 대상 명시 |
| SOFT_FAIL | 금액만 있고 전입 대상 누락 |
| HARD_FAIL | 데이터 없음 (안건 있는데 파싱 실패) |

---

## 8. retirement_pay -- 퇴직금 규정

변경전/변경후 규정 텍스트.

### 판정

| 등급 | 기준 |
|------|------|
| SUCCESS | before/after 텍스트 존재 |
| SOFT_FAIL | 한쪽만 존재 |
| HARD_FAIL | 데이터 없음 (안건 있는데 파싱 실패) |
