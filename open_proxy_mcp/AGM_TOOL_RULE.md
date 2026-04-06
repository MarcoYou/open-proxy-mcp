# Tool Rule

## Tool 구조 (40 tools)

### 오케스트레이터
- `agm(ticker)` — 종합 (info + agenda + fin + treasury + corrections)
- `own(ticker)` — 지분 종합 (major + total + treasury + block + latest)

### AGM Search & Meta
- `agm_search(ticker)` — 소집공고 검색 → rcept_no 리스트
- `agm_document(rcept_no)` — 공고 원문 텍스트
- `agm_info(rcept_no)` — 회의 정보 (일시/장소/투표방법)
- `agm_items(rcept_no, agenda_no)` — 안건 상세 블록 (원문)
- `agm_extract(rcept_no)` — 원문 + 핵심 데이터 추출
- `agm_corrections(rcept_no)` — 정정 전/후 비교
- `agm_manual()` — 이 문서 + AGM_CASE_RULE.md 반환

### 8 Parsers x 3 Tiers (24 tools)

| 파서 | _xml (fast) | _pdf (4s+) | _ocr (10s+, UPSTAGE_API_KEY) |
|------|-------------|------------|------------------------------|
| agenda (안건 트리) | agm_agenda_xml | agm_agenda_pdf | agm_agenda_ocr |
| financials (재무제표) | agm_financials_xml | agm_financials_pdf | agm_financials_ocr |
| personnel (선임) | agm_personnel_xml | agm_personnel_pdf | agm_personnel_ocr |
| aoi_change (정관변경) | agm_aoi_change_xml | agm_aoi_change_pdf | agm_aoi_change_ocr |
| compensation (보수한도) | agm_compensation_xml | agm_compensation_pdf | agm_compensation_ocr |
| treasury_share (자사주) | agm_treasury_share_xml | agm_treasury_share_pdf | agm_treasury_share_ocr |
| capital_reserve (자본준비금) | agm_capital_reserve_xml | agm_capital_reserve_pdf | agm_capital_reserve_ocr |
| retirement_pay (퇴직금) | agm_retirement_pay_xml | agm_retirement_pay_pdf | agm_retirement_pay_ocr |

### AGM 결과
- `agm_result(ticker)` — KIND 크롤링 → 투표결과 + 추정참석률

### Ownership (6 tools)
- `own_major(ticker, year)` — 최대주주 + 특관인
- `own_total(ticker, year)` — 총주식수 / 자사주 / 유통 / 소액주주
- `own_treasury(ticker, year)` — 자사주 취득방법별 잔액
- `own_treasury_tx(ticker)` — 자사주 이벤트 (취득/처분/신탁)
- `own_block(ticker)` — 5% 대량보유자 (보유목적 포함)
- `own_latest(ticker, year)` — 통합 스냅샷

## Fallback 흐름

```
1. agm_*_xml 호출 (빠름)
2. AI가 결과를 AGM_CASE_RULE 기준으로 검증
3. SUCCESS → 답변 (AI가 포맷 보정 가능: 공백 정리, 단위 변환 등)
4. SOFT_FAIL → AI가 자체 보정 시도 (구분자 분리, 누락 필드 추론 등)
   - 보정 성공 → 보정된 결과로 답변
   - 보정 불가 → 유저에게 PDF fallback 제안
5. 유저 동의 → agm_*_pdf 호출 (4s+)
6. 여전히 부족 → 유저에게 OCR fallback 제안
7. 유저 동의 → agm_*_ocr 호출 (UPSTAGE_API_KEY 필요)
```

**중요**: 파서가 SUCCESS를 반환해도 AI가 직접 결과를 읽고 검증할 것.
AGM_CASE_RULE의 성공 예시가 "이렇게 생겨야 한다"의 기준.

## 안건 유형별 파서 매핑

| 안건 유형 | XML 파서 | 일반적 제목 패턴 | 비고 |
|-----------|----------|-----------------|------|
| 재무제표 승인 | parse_financials_xml | "재무제표", "대차대조표", "손익계산서" | 보고사항일 수 있음 (결의 안건 아닌 경우) |
| 이사/감사 선임 | parse_personnel_xml | "선임", "해임" | 집중투표 시 별도 처리 필요 |
| 정관변경 | parse_aoi_xml | "정관" | 하위 안건(제2-1호 등)으로 분할 빈번 |
| 보수한도 | parse_compensation_xml | "보수", "한도" | 이사/감사 분리 가능 |
| 자기주식 | parse_treasury_share_xml | "자기주식", "보유", "처분", "소각" | XML 제목 매칭 한계, PDF fallback 빈번 |
| 자본준비금 | parse_capital_reserve_xml | "자본준비금", "이익잉여금 전입" | 감액배당 전제 조건 |
| 퇴직금 규정 | parse_retirement_pay_xml | "퇴직금", "퇴직급여" | 재무제표 주석과 혼동 주의 |

## 파싱 한계

- **자기주식**: 소집공고에 명시적 안건 없는 기업 많음. PDF fallback이 본문 전체 스캔으로 보완.
- **보수한도**: 이사/감사 별도 안건 가능. 금액 단위 다양 → limitAmount(원 단위)로 정규화.
- **퇴직금**: 재무제표 주석의 "퇴직급여" 테이블과 혼동 위험. 안건 제목 매칭으로 해결.
- **재무제표**: 보고사항일 수 있음 (투표 없음). 참석률 역산 시 제외 필요.
- **정관변경**: 하위 안건 분할 빈번. "------생략", "<삭제>" 표기는 정상.
- **이사 선임**: 경력 병합 시 PDF fallback. 감사위원 선임은 의결권 3% 제한.

## 집중투표제

| | 일반 투표 | 집중투표 |
|---|-----------|----------|
| 의결권 | 안건당 1주 1표 | N명 선출 시 1주 N표 |
| 결과 | 찬성/반대/기권 (%) | 득표율 + 순위 |
| 가결 기준 | 과반수 또는 2/3 | 득표 상위 N명 |
| DART 표기 | 발행기준/행사기준 찬성률 | "-" (찬성률 없음) |

## KIND 주총결과

### 데이터 소스
- KRX KIND (kind.krx.co.kr) 크롤링
- DART rcept_no → KIND acptno 변환: 8번째 이후 "80" → "00"

### 참석률 역산
```
전체 참석률 = 발행기준 찬성률 / 행사기준 찬성률
```
- 보통결의 안건 중 최빈값이 대표 참석률
- 감사위원 선임은 3% 의결권 제한으로 분모 다름 → 참석률 달라짐

## 판정 원칙

- 해당 안건이 소집공고에 존재하는 경우에만 성공/실패 판정
- 안건 자체가 없으면 빈 결과가 정상 (실패 아님)
- _pdf는 DART 웹 PDF 다운로드 (4s+), _ocr는 Upstage API (UPSTAGE_API_KEY 필요)

## 시간순서 규칙

```
소집결의 (이사회) → 소집공고 → 주총 당일 → 주총결과
```
- 공고 데이터를 결과에서 참조 → OK
- 결과 데이터를 공고에 넣기 → 금지 (시간 역전)
