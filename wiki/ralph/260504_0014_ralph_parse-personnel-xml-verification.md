---
type: ralph
title: parse_personnel_xml 강화 + 전수조사 검증 (생년월일 / 전현직 / 재임기간 / 직책 다양 포맷 cover)
created: 2026-05-04 00:14
completion_promise: PARSE_PERSONNEL_VERIFIED
max_iterations: 30
---

## Invoke (복붙해서 실행)

```
/ralph-loop:ralph-loop wiki/ralph/260504_0014_ralph_parse-personnel-xml-verification.md 가이드 따라 parse_personnel_xml 8 필드 검증 + 강화. archive 우선. soft pattern 우선 hard pattern 다층 fallback. OCR 진단 only parser final. 전수조사 sample 500 회사 + 8 필드 success rate 95이상 + careerDetails 비어있는 비율 30이하 모두 충족 시 promise. --completion-promise PARSE_PERSONNEL_VERIFIED --max-iterations 20
```

> 모든 ralph 작업 invoke history는 [[invoke-history]] 참조.

## ⚠ Status (2026-05-04, iter 7 완료)

**career_period 89.0% 한계 — 95% 도달 불가능 확정**:
- 7 iter 진행 (iter4 role +11.3%p ✅, iter6 content year +0.3%p)
- 78 fail 후보 본문 정밀 검증: HTML 본문에도 year 없는 case 다수
- "(주)포스코엠텍 감사팀장(주)나눔테크 전무이사" 같은 단순 직책+회사명 — 시작 연도 본문에 X
- parser fix 효과 없음 — **본문 데이터 자체 한계**

**Promise 정직 X 유지** — 추가 iter 진행해도 95% 도달 불가능.

### 사용자 cancel 권고
```
/ralph-loop:cancel-ralph
```

### 또는 acceptance 옵션
- Option A: career_period target 95% → 90% relax (현재 89.0% 거의 도달)
- Option B: 별도 ralph (본문 다른 section 보완 — value_up / corp_gov_report 등 cross-source)
- Option C: 종료 (proxy_advise G2 99.36% 영향 없음)

archive: `wiki/architecture/audits/data/260504_parse_personnel_failure_archive/iter07_data_limit_confirmed.md`

# Ralph: parse_personnel_xml 강화 + 전수조사 검증

proxy_advise ralph (260503_0002) 결과 G2 99.36% 충족. 잔여 unique 3건 핵심 root cause는 **`parse_personnel_xml`의 careerDetails / role_type / audit role 추출 실패**.

특히:
- 서진시스템 정전환 (상근감사) → role_type=None, careerDetails=0
- 펩트론 이기영 → evaluations 0
- 심텍 김장래 / 고영 이종기 → role_type=None, careerDetails=0

DART 본문 구조 다양 (회사별 / 정정공고별) → 단일 parser 패턴 fail. **본 ralph는 8 필드 다양 포맷 cover + 전수조사 검증**.

## 가정 (Phase 3 동일)
- No current conversation context
- No web search
- MCP only
- as if it's the first question
- deterministic (temperature=0)

## 매 iteration 작업
1. **현황 확인**: 이전 검증 csv + 실패 archive
2. **다음 1 필드 또는 1 패턴**만 진행 (작게 쪼갬)
3. **fix 검증**: 50-200 회사 sample 재추출 후 success rate 측정
4. **commit** (의미 있는 변경마다)
5. 다음 iteration 1줄

---

## 8 필드 검증 항목

### F1. 이름 (name)
- 한글: 김동관 / Michael Coulter (마이클 쿨터) / 박○○
- 영문: Hobart L.Epstein / Thomas Park
- 한자/영문 혼용: 鄭傳鈉 (정전환)
- 회사 일부 이름에 회사명 포함: "한화생명 김00"
- **target**: success rate ≥99% (n=500 회사 × 평균 5 후보)

### F2. 생년월일 (birthDate)
- 다양 포맷:
  - "1970-05-04" / "1970.05.04" / "1970/05/04"
  - "1970년 5월 4일" / "1970년 5월" / "1970년"
  - "70.05.04" / "1970"
  - 잘못된 데이터 (예: "5378-05-04") → ignore
- iter22 fix: year 1900-current 범위 제한 → 100% 정확도 ✅
- **target**: 정확도 100% (이미 검증됨, 회귀 없음)

### F3. 전/현직 직장 (careerCompanyGroups / careerDetails)
- 한자 표기: "前 삼성전자 사장" / "現 한화 부사장"
- 한글 표기: "전 삼성전자 사장" / "현 한화 부사장"
- 영문 표기: "Former Samsung CEO"
- 표기 안 함 (직장명만): "삼성전자 사장 (2018-2024)"
- **target**: 전/현 분류 success rate ≥95%

### F4. 직책 (mainJob / role_type)
- 사외이사 / 사내이사 / 상근감사 / 비상근감사 / 감사위원
- 대표이사 / 사장 / 부사장 / 전무 / 상무 / 이사
- 영문: CEO / CFO / Director / Independent Director
- **target**: role_type 추출 success rate ≥95% (현재 대부분 None — 핵심 fix)

### F5. 재임기간 (period in careerDetails)
- 포맷 다양:
  - "2016년 3월 ~ 2024년 3월"
  - "2016.03 ~ 2024.03"
  - "2016/03 - 2024/03"
  - "2016-03 ~ 2024-03"
  - "2016 ~ 2024"
  - "2016 ~ 현재"
  - "2016.03 ~ "  (종료 미명시)
  - "現 2016 ~"  (한자 현)
- 정상 detect → 시작/종료 연도 추출
- **target**: period 정상 추출 success rate ≥95%

### F6. 재임기간 정렬 (오름차순 vs 내림차순)
- 오름차순: 2016 → 2018 → 2020 (과거부터 최근)
- 내림차순: 2020 → 2018 → 2016 (최근부터 과거)
- 회사별로 다름 — careerDetails order 자체가 일관 X
- 재임기간 합산 시 정렬 전 정규화 필요
- **target**: 정렬 무관 5년 룰 detect (시작-종료 차이만 봄)

### F7. 재임기간 포맷 (연 vs 월 vs 일)
- 연만: "2016 ~ 2024"
- 연+월: "2016년 3월 ~ 2024년 3월"
- 연+월+일: "2016-03-15 ~ 2024-03-15"
- 일 missing → 1일로 가정
- 월 missing → 1월로 가정
- **target**: 모든 포맷 자동 normalize

### F8. 전/현 한자 vs 한글
- "前 / 現" (한자)
- "전 / 현" (한글)
- "Former / Current" (영문)
- 정규식 OR 매칭으로 cover
- **target**: 한자/한글/영문 모두 detect

---

## 성공 기준 (모두 충족 시 promise)

### G1. 8 필드 추출 success rate
- name: ≥99%
- birthDate: 100% (회귀 없음)
- 전/현직 분류: ≥95%
- mainJob/role_type: ≥95% (현재 핵심 부족)
- period: ≥95%
- 정렬 무관 5년 detect: ≥95%
- 포맷 normalize: ≥95%
- 한자/한글/영문 cover: ≥95%

### G2. 전수조사 — 500 회사 sample
- universe: 11873 공고에서 정기 + 비정정 random 500
- 본문 fetch + parse_personnel_xml 호출 → success/fail 통계
- 각 후보별 8 필드 추출 success rate

### G3. careerDetails 비어 있는 비율
- 현재 (예상): 30-50% (parser miss)
- target: ≤10% (parser 강화 후)

### G4. proxy_advise G2 회귀 검증
- 152 회사 batch 재실행
- 4+ majority 99.36% 유지 또는 개선
- regression 0

### Promise 출력 조건
1. 산출물 .py commit (`tools/parser.py` parse_personnel_xml + helpers)
2. G1 8 필드 모두 target 충족
3. G2 500 회사 sample success
4. G3 careerDetails ≤10%
5. G4 proxy_advise 99.36% 유지
6. 실패 archive (회사명/필드/raw text/제안 fix)

→ **`<promise>PARSE_PERSONNEL_VERIFIED</promise>` 출력**

---

## 실패 사례 incremental archive

매 iteration 실패 case 만날 때 **원문 + 분석 archive**:
- 위치: `wiki/architecture/audits/data/260504_parse_personnel_failure_archive/`
- 파일명: `{ticker}_{rcept_no}_{field}_{fail_type}.md`
- 내용:
  1. 회사 / rcept_no / 후보 이름
  2. 실패 필드 (name/birthDate/role_type/career/period 등)
  3. 본문 raw text 발췌 (해당 후보 section 200-1000자)
  4. 현재 parser 시도한 정규식 / 매칭 실패 라인
  5. 정답 (수동 확인) — 무엇이 추출되어야 했나
  6. 제안 fix (정규식 보강 / 분기 추가 / fallback layer)

---

## Soft pattern 우선 / Hard pattern 다층 fallback

DART 본문 구조 다양:
- HTML table 구조 / div 구조 / p 구조
- 빈 cell / colspan / rowspan
- 한자/한글/영문 혼용
- 공백/줄바꿈 변형
- BS4 lxml vs html.parser 결과 차이

soft pattern (regex 다양 alternatives + normalized 비교 + substring + fuzzy) 우선.
hard pattern absolutely needed 시 다층 fallback (HTML structure → text → OCR study only).

---

## 진단 vs 산출물 분리

- 진단 단계: PDF 다운로드 + opendataloader-pdf + Upstage OCR 사용 OK (study)
- 최종 산출물: parser only (production runtime OCR 호출 X)
- image-only PDF는 status="image_notice_ocr_needed" 명시 (silent fallback X)

---

## 사전 정리 (Phase 3/proxy_advise 발견)

### Pre-finding 1: audit 후보 evaluations 0 multiple companies
- 펩트론 이기영, 셀트리온 등 → evaluations=0
- 본문에 "재선임" 키워드 명시 X case에서 parser 매칭 실패

### Pre-finding 2: role_type=None 다수
- 서진시스템 정전환, 심텍 김장래, 고영 이종기 → 본문 추출 실패
- 운용사 reason은 본문 careerDetails 정확 인용 → 본문에 정보 있음, parser 못 잡음

### Pre-finding 3: birth_date age 음수 bug
- 잘못된 birth_date format (예: "5378-05-04") → year 5378 → age=-3353
- iter22 fix: year 1900-current 범위 제한
- 본 ralph에서 회귀 검증

### Pre-finding 4: detect_meeting_type pattern (참조)
- 본문 첫 500자 normalize 후 "임시" 키워드 검색 → 100% 정확도
- careerDetails도 비슷한 normalized substring search 가능성

---

## 반복 단위 (작은 step)

좋은 1 iteration 단위 예시:
- "F1 name 추출 spot 50 회사 + 실패 case 분석"
- "F4 role_type fallback layer (사외이사/상근감사/감사위원 키워드 다양화)"
- "F5 period 정규식 확장 (다양 포맷 alternatives)"
- "F6 정렬 무관 5년 detect helper 작성 + 단위 테스트"
- "전수조사 500 회사 batch + 8 필드 success rate 측정"
- "잔여 실패 case archive + 다음 fix 후보 도출"

너무 큰 step (예: "F1+F4+F5+전수조사 한 번에") 금지.

---

## 명명
- 이 ralph: `wiki/ralph/260504_0014_ralph_parse-personnel-xml-verification.md`
- audit 페이지: `wiki/architecture/audits/260504_HHMM_audit_parse-personnel-{phase}.md`
- 실패 archive: `wiki/architecture/audits/data/260504_parse_personnel_failure_archive/`
- 검증 csv: `wiki/architecture/audits/data/260504_parse_personnel_{f1|f4|f5|...}.csv`
- 검증 script: `/tmp/run_parse_personnel_{phase}.py`
