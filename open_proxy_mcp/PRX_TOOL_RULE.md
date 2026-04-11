# PRX Tool Rule

## Tool 구조 (5 tools)

### 오케스트레이터
- `prx_fight(ticker, year)` — 프록시 파이트 감지 (prx_search + prx_direction 체이닝)

### 개별 Tool
- `prx_search(ticker, year)` — 위임장 권유 참고서류 검색 (rcept_no 목록 + 회사측/주주측 구분)
- `prx_detail(rcept_no)` — 권유자 상세 (보유주식, 권유기간, 대리인, 전자위임장 방법)
- `prx_direction(rcept_no)` — 안건별 의결권 행사방향 (찬성/반대/기권)
- `prx_manual()` — PRX_TOOL_RULE + PRX_CASE_RULE

## 검색 방법

OpenDART `list.json`에서 `pblntf_detail_ty` 파라미터 미지원.
→ corp_code + 날짜범위로 전체 검색 후 `report_nm`에서 "의결권대리행사" 키워드 필터.

```python
# 필터 조건
report_nm.contains("의결권대리행사") or report_nm.contains("위임장권유")
```

## 회사측 vs 주주측 구분

`flr_nm` (제출인 이름) 기준:
- `flr_nm == corp_name` (또는 포함) → 회사측 (is_company=True)
- 다르면 → 주주측 (is_company=False, 행동주의 펀드/기관투자자)

동일 주총에 복수 제출 → 날짜(주총일) 기준 grouping → prx_fight 감지.

## 안건별 행사방향 파싱

**위치**: Section II-1 "의결권 대리행사의 권유를 하는 취지" (자유서술)
**API 미제공**: 반드시 get_document(rcept_no)로 원문 파싱.

파싱 패턴:
```python
# 안건번호 + 행사방향
re.findall(r'제\s*(\d+(?:-\d+)?)\s*호[^.]*?(찬성|반대|기권)', text)
# 역방향
re.findall(r'(찬성|반대|기권)[^.]*?제\s*(\d+(?:-\d+)?)\s*호', text)
```

불명확한 경우 "불명" fallback. AI가 최종 판단.

## 문서 구조 트리

```
의결권대리행사권유참고서류
├── [커버] 권유자, 대상회사, 제출일
├── [요약표] 권유 개요, 전자위임장, 주총 목적사항 목록
│
├── I. 의결권 대리행사 권유에 관한 사항
│   ├── 1. 권유자 보유주식 현황 테이블
│   ├── 2. 의결권 수임인 + 권유업무 위탁법인
│   └── 3. 권유기간 + 피권유자 범위
│
├── II. 의결권 대리행사 권유의 취지
│   ├── 1. 권유 취지 (★ 안건별 행사방향 자유서술) ← prx_direction 파싱 위치
│   ├── 2. 위임 방법 (전자위임장/서면)
│   └── 3. 주주총회 직접 행사 (전자투표/서면투표)
│
└── III. 주주총회 목적사항별 기재사항
    ├── 회사측: 재무제표 전문 + 후보자 경력 + 정관변경 대조표
    └── 주주측: 제목만 (규정에 따라 생략 가능)
```

## API 호출 수

| Tool | 호출 수 |
|------|---------|
| prx_search | 1 (list.json) |
| prx_detail | 1 (get_document) |
| prx_direction | 1 (get_document) |
| prx_fight | 1 + 권유자 수 × 1 (search + direction per 권유자) |

## 데이터 소스

- **DART list.json**: rcept_no, flr_nm, rcept_dt, report_nm → prx_search
- **DART get_document**: 원문 텍스트 → prx_detail, prx_direction
