---
type: analysis
title: 인사 파서 audit 2026-04-29 — KOSPI 200 후보자 경력 정확도 79% → 95%
tags: [audit, parsing, personnel, shareholder_meeting, regression, candidates]
related: [parsing-audit-2026-04-29-v2, parsing-fix-2026-04-29-cgr-financial]
date: 2026-04-29
---

# 인사 파서 audit 2026-04-29 — 후보자/경력 정확도 95% 달성

`shareholder_meeting`의 personnel 파서 (이사·감사 후보자 + 경력) 전수 점검.
KOSPI 200 (199개사) 표본에 대해 HARD_FAIL 0건, SUCCESS 94.6% 달성.

## 환경

- 실행: 2026-04-29
- 유니버스: KOSPI 200 (199개사, 가장 최근 주총 소집공고)
- 입력: `parse_personnel_xml(html)` (파일: `open_proxy_mcp/tools/parser.py`)
- 판정 기준: `test/benchmark_personnel.py`의 `judge_candidate()`
  - HARD_FAIL: 이름이 30자 초과 또는 조문번호/안건번호 패턴 또는 경력 0개
  - SOFT_FAIL: content > 100자 (merged) 또는 period 누락
  - SUCCESS: 위 조건 모두 통과

## 결과 매트릭스

| 지표 | Before (2026-04-06) | After (2026-04-29) | 변화 |
|---|---:|---:|---:|
| 회사 (clean) | 168 / 199 (84.4%) | 199 / 199 (100%) | +31 |
| 전체 후보자 | 878 | 849 | -29\* |
| **SUCCESS** | **697 (79.4%)** | **803 (94.6%)** | **+15.2pp** |
| SOFT_FAIL | 103 | 46 | -57 |
| HARD_FAIL | 78 | 0 | -78 |

\* 전체 후보자 -29: 잘못된 후보자(조문 텍스트, 안건번호, footnote 등 78건)를 정상적으로 거부하면서
실제 후보자 풀이 정상화된 결과. 이름 검증 정규식이 도입되기 전에는 HARD_FAIL로 잡히던 가짜
후보자가 879명 풀에 포함돼 있었음.

## 회귀 0 검증

이전 SUCCESS 케이스 697건 모두 유지. 1건만 SOFT_FAIL로 약간 후퇴
(한국앤컴퍼니 KIM EUNICE KYONGHEE — 8개 period vs 10개 content 카운트 불일치, source 데이터 한계).

## Fix 패턴별 영향

### 1. 정관변경 안건 → 후보자 테이블 false-positive (HARD_FAIL 약 25명 제거)

**증상**: "전자주주총회 제도 도입의 건" / "감사위원 분리선임 인원 변경의 건" 등 정관변경 안건의
"가." 서브섹션 표 (변경전 내용 / 변경후 내용 / 변경의 목적)에서 row[0]이 정관 본문 텍스트
("제 31 조 (이사의 선임) ③ 2인이상의 이사를...") → 후보자 이름으로 잘못 추출.

**Fix**:
- `_is_personnel_title()` 신규: 제목에 '정관/한도/보수/감액/제도 도입' 등이 있으면 인사 안건으로 분류 안 함
- `_extract_candidates()`: `is_charter_amendment` 분기 추가 + `_is_candidate_table()` 헤더 검증 (변경전/변경후 키워드면 reject)

**영향**: DL, POSCO홀딩스, 키움증권, 포스코퓨처엠, 한국타이어앤테크놀로지 등 정관변경 케이스 전부 해결.

### 2. `| 의안 | 후보자성명 |` 비표준 컬럼 구조 (HARD_FAIL 30+명 제거)

**증상**: 신한지주, KB금융, BGF리테일, 세방전지 등 row[0]이 "제4-1호 의안" (의안 번호)이고
실제 후보자명은 row[1]에 위치. 기존 코드는 row[0]을 무조건 이름으로 사용.

**Fix**:
- `_find_name_column()` 신규: 헤더에서 "후보자성명" 컬럼 동적 탐지
- 가/나/다 섹션 모두 `name_col`을 동적으로 사용

### 3. 안건번호 + 이름 concat (HARD_FAIL 5명 제거)

**증상**: 현대제철 row[0] = "제3-1호고흥석" — 의안번호와 이름이 같은 셀에 붙음.

**Fix**: `_normalize_candidate_name()` 신규: `^제?\s*\d+(?:\s*-\s*\d+)*\s*호` prefix 제거.

### 4. 보수 안건 테이블 contamination (HARD_FAIL 8명 제거)

**증상**: 한솔케미칼에서 "감사위원회 위원의 선임" 다음에 보수한도 표가 같은 detail에 들어가서
"보수총액 또는 최고한도액" / "실제 지급된 보수총액" / "주식의종류" 등이 후보자 이름으로 추출됨.

**Fix**: 헤더 검증에서 '보수총액', '최고한도', '실제지급' 키워드 있으면 후보자 테이블 아니라고 판정.

### 5. footnote 행 (카카오페이 "주2)" 등) 제거

**증상**: 가. 섹션의 후보자 표 아래 "| 주1) | 설명 |" 같은 footnote가 부착되어 row[0]="주2)" 등을
이름으로 추출.

**Fix**: `_is_valid_candidate_name()`에서 `re.fullmatch(r'주\s*\d+\s*\)', name)` 거부.

### 6. 부모-자식 안건 carry-over (HARD_FAIL 5명 제거)

**증상**: 한화솔루션, 셀트리온, 삼성물산, 롯데칠성 등에서 부모 안건(제3호 이사 선임)에 자식 안건들
(제3-1호 김동관, 제3-2호 남정운...)이 있고, **자식들의 후보자 테이블이 마지막 자식 안건에만**
모여 있음. 기존 코드는 각 자식 안건마다 별도 처리하므로 마지막 외 자식의 후보자는 careerDetails 0.

**Fix**: `parse_personnel_xml` 후처리에서 같은 이름으로 다른 appointment에 careerDetails가 있으면
back-fill (back-fill 우선순위: 첫 발견 데이터).

### 7. 철회 안건 스킵

한국타이어앤테크놀로지 "이은경" 등 (철회) 표시된 후보자는 careerDetails 없는 게 정상 → skip.

### 8. 이름 정규화 — 부가 텍스트 제거

KB금융 "최재홍(재선임)임기 1년" → "최재홍". `_normalize_candidate_name()`이 (재선임), 임기 N년 등 제거.

### 9. content 분리 패턴 확장 (SOFT_FAIL merged 50→18 감소)

**`_split_merged_content()` 신규**: 100자 초과 content를 회사명/직책 boundary로 분리.

추가된 패턴:
- 직책 뒤 영문 회사명 (Accenture, Bain 등)
- 영문 직책 (Manager, Director, CEO 등) 뒤 다음 회사
- 같은 conglomerate 반복 (LG전자 CEO 사장 LG전자 HS사업본부장 ...)
- (주) 뒤 직책 + 다음 회사 시작
- 정부 부처/위원회 뒤 다음 기관
- 학력 (학사/석사/박사/MBA) 뒤 다음 학교/회사

### 10. Period parsing 강화

- 4자리 연도+년 (`2025년`) → `2025` (불필요한 '년' 제거)
- 2자리 연도+년 (`'17년` 또는 `25년`) → `2017` / `2025` (롯데지주 패턴)
- 4자리 연도 시퀀스 (`201520182022`) 분리 인식 (한국항공우주 패턴)
- 트레일링 `~` (e.g., "2009~2022~") → `~현재` 자동 보강

### 11. content 정리 — 잔여 marker 제거

- 시작/끝의 `-`, `o`, `*`, `•`, `(` 등 bullet/orphan paren 제거
- 단독 bullet ("o", "・") 행 제거

### 12. `(現)/(前)` 한글 false-positive 방지

기존 split 정규식 `(?:現|前|현|전)\s` 가 "집현전" 같은 단어 중간 한글 "현/전"에 잘못 매칭.

**Fix**: 한자 `現/前`만 단어 boundary 매칭 + 한글 `(현)/(전)` 닫는 괄호 형태만 매칭.

## 케이스별 결과 샘플

### Before/After: 신한지주 (제4호 이사 선임)

**Before** (HARD_FAIL 6명):
```
- name: '제4-1호 의안', careers: 0
- name: '제4-2호 의안', careers: 0
...
```

**After** (SUCCESS 6명):
```
- name: '진옥동', careers: 2
- name: '김조설', careers: 2
- name: '배훈', careers: 1
- name: '송성주', careers: 1
- name: '최영권', careers: 1
- name: '박종복', careers: 1
```

### Before/After: BGF리테일 민승배 (HARD_FAIL → SUCCESS)

**Before**: "민승배" no_career + "제3-1호" / "제3-2호" 가짜 이름 2건

**After**: "민승배" with 5 careers, "이윤성" with 3 careers

### Before/After: 한화솔루션 (carry-over)

**Before**: 김동관/남정운/이아영 (제3-1, 3-2, 3-3호) — careers 0
**After**: 모두 careers 5-7개 (제3-4호 후보자 테이블에서 back-fill)

## 잔여 SOFT_FAIL 46건 분석

- **merged (18건)**: content 분할 패턴이 미캐치한 long 사례
  (대웅제약 권순용, 두산 김혜성, 미래에셋증권 4명 등). 학력+학교+직책+기관 패턴 다양화.
- **silent no_period (24건)**: source 데이터에서 period count < content count
  (한샘 임재철, 코오롱인더 강민아, 호텔신라 이부진 등). source 한계.
- **no_period flagged (4건)**: 50% 초과 (이수스페셜티 이성훈, 한샘 송인준, 삼성화재 박성연 2건)

이들은 source XML에 period가 누락된 케이스로, 추가 LLM fallback이나 cross-source verification으로
처리할 수 있음 (향후 작업).

## 코드 변경 파일

- **수정**: `open_proxy_mcp/tools/parser.py`
  - 신규 헬퍼: `_is_candidate_table()`, `_find_name_column()`, `_is_valid_candidate_name()`,
    `_normalize_candidate_name()`, `_is_personnel_title()`, `_split_merged_content()`
  - 개선: `_extract_candidates()`, `_extract_career_from_html()`, `_clean_career_details()`,
    `_parse_period_raw()`, `parse_personnel_xml()` (back-fill 후처리 추가)
  - 신규 상수: `_TITLE_NAME_BLACKLIST` (분리/신규/재 등 가짜 이름 제외)

총 라인 변경: parser.py +250라인 (신규 헬퍼 + 패턴 확장).

## audit 스크립트

`/tmp/audit_personnel_v2.py` (로컬). 다음 정기 audit 시 재실행 가능.
실행 시간: ~30초 (199 회사 × 평균 150ms).

## 향후 작업

### 단기
- 잔여 silent no_period (24건) — source 한계 케이스 확인 + LLM fallback 검토
- merged 18건 — content 분할 패턴 추가 (학력 시퀀스, 영문 회사명 추가)

### 중기
- BNK금융지주, 기업은행 비표준 구조 — 별도 adapter 검토 (현재는 표준 구조로 처리됨, 회귀 없음)
- 회사명 그룹핑 (`_build_career_company_groups`) 정확도 추가 점검 (TO_DO 항목)

## 관련

[[parsing-audit-2026-04-29-v2]] [[parsing-fix-2026-04-29-cgr-financial]] [[OpenProxy-MCP]]
