---
type: ralph
title: 파서 정밀화 — parse_personnel_xml careerDetails + parse_aoi_xml fallback
created: 2026-05-08 02:07
completion_promise: PARSER_PRECISION_VERIFIED
max_iterations: 7
ref:
  - wiki/architecture/audits/260508_parser_audit.md
  - wiki/lessons/law-layer-precision-260508.md
  - wiki/ralph/260508_0500_ralph_law-layer-precision.md
  - open_proxy_mcp/tools/parser.py
---

## Invoke

특수문자 사용 금지. 한글로 풀어쓰기.

```
/ralph-loop:ralph-loop wiki/ralph/260508_0207_ralph_parser-precision.md 가이드 따라 두 파서 정밀화. parse_personnel_xml careerDetails segmentation 보강 raw_content fallback. parse_aoi_xml amendments 누락 fallback clause label 추출 다양화. 회귀 280 회사 audit 영향 점검. 모두 충족 시 promise. --completion-promise PARSER_PRECISION_VERIFIED --max-iterations 7
```

# Ralph: 파서 정밀화

## Context

Parser audit (260508_parser_audit) 결과 40 파서 중 2개만 보강 필요:

1. **`parse_personnel_xml` careerDetails segmentation 약함**
   - 현재: `candidates[].careerDetails[].{period, content}`
   - 문제: content 안의 회사/직책 분리(`_split_company_role`)가 실패하면 unparsed → 5년 룰 long_tenure_concerns 작동 못 함
   - 영향 사례: 서진 / 펩트론 / 심텍 / 고영 등 audit 후보 careerDetails 비어있음

2. **`parse_aoi_xml` amendments 누락 fallback 부재**
   - 현재: clause/label 추출 패턴 한정
   - 문제: 일부 회사 (KOSPI 200 audit 회사 일부) amendments 비어 있음 — 정관 sub-agenda 매칭 실패 시 fallback 없음
   - 영향: aoi_change scope 정확도 저하 + Ralph 4 B1-8b 매칭 기회 손실

## 가정

- raw 보존이 본질 (자연어 본문 영역) — 파서는 명명 실패 시 raw 보장
- 구조 변경 X (return shape compatible) — 추가 key만 (`raw_content`)
- 회귀 안전 (기존 hits 유지)
- DART API 호출 절약 (기존 audit data 활용)

## 성공 기준

### G1. parse_personnel_xml careerDetails fallback 동작
- careerDetails[].content를 `{company, role, raw_content}`로 한 단계 더 segmentation
- 회사/직책 분리 실패 시 raw_content는 항상 보장 (raw text 그대로)
- 진단 케이스 (서진/펩트론/심텍/고영) 본문 raw 보존 확인

### G2. 5년 룰 long_tenure_concerns 작동
- careerDetails fallback 후 _classify_director_tenure logic 작동
- 진단 후보들 (5년+ 재임 패턴) detect

### G3. parse_aoi_xml amendments fallback 동작
- KOSPI 200 audit에서 amendments 비어있는 회사 list 추출
- clause/label 추출 패턴 다양화 (sub_id 누락 + label 누락 케이스 분리 fallback)
- 누락 회사 amendments 추출 가능 확인

### G4. 회귀 0%
- 280 회사 누적 audit 기존 hits 유지 (변경되더라도 개선 방향만)
- 새 hits 발견 시 정확성 검증 (false positive X)

## 작업 plan (7 iter)

### Phase 1 — parse_personnel_xml careerDetails 정밀화 (iter 1-3)

#### iter 1. careerDetails 누락 진단
- 서진/펩트론/심텍/고영 등 historical 후보 호출
- 현재 careerDetails 비어있는 케이스 raw HTML 분석
- _split_company_role 실패 패턴 식별

#### iter 2. raw_content fallback 추가
- `_clean_career_details` 보강: content 추가 segmentation 시도
- 실패 시 `raw_content` key에 원본 보존
- return shape: `{period, content, company?, role?, raw_content}` (company/role optional, raw_content 필수)
- 단위 테스트 (현재 정상 케이스 + 실패 케이스 both)

#### iter 3. 5년 룰 작동 검증
- 진단 후보 다시 호출하여 long_tenure_concerns trigger 확인
- _classify_director_tenure가 raw_content fallback 활용 가능한지 점검
- 필요시 _classify logic도 raw_content 받도록 보강

### Phase 2 — parse_aoi_xml fallback 강화 (iter 4-5)

#### iter 4. amendments 누락 진단
- 280 회사 audit에서 정관변경 안건 있는데 amendments=[] 회사 list 추출
- 누락 회사 raw HTML 분석
- clause/label 추출 실패 패턴 식별 (sub_agendas 매칭 실패 / 정규식 mismatch / table 구조 변형)

#### iter 5. fallback 패턴 추가
- clause 추출 다양화 (제X조 / 제 X 조 / 제X조 (제목) 등)
- label fallback (sub_agendas 매칭 실패 시 first non-empty line 사용)
- additionalClauses grouping logic 보강
- before/after raw text는 항상 보존 (이미 raw)

### Phase 3 — 회귀 + 문서화 (iter 6-7)

#### iter 6. 280 회사 회귀
- KOSPI 200 + KOSDAQ 100 + 분쟁 20 spot 재실행 (parser 변경 후)
- 기존 hits 유지 + 새 hits 정확성 검증
- B1-8b 등 미사용 룰 새로 hit하는지 확인 (KT&G 본문 매칭 효과)

#### iter 7. 문서화 + promise
- lesson 작성 (정밀화 발견)
- decision 작성 (parser 변경)
- log update
- promise 발행

## 총 DART 호출 추정

| Phase | iter | 호출 |
|---|---|---|
| Phase 1 (personnel) | 1-3 | ~50 (진단 후보 + 검증) |
| Phase 2 (aoi) | 4-5 | ~50 (누락 회사 진단) |
| Phase 3 (회귀) | 6 | ~600 (280 회사 spot) |
| Phase 3 (문서화) | 7 | 0 |
| **총합** | 7 iter | **~700 calls** |

→ 분당 cap 안전.

## 영향 범위

- `open_proxy_mcp/tools/parser.py` — `parse_personnel_xml` + `_clean_career_details` + `parse_aoi_xml` + `_map_sub_agendas_to_amendments` 보강
- `open_proxy_mcp/services/director_evaluation.py` — _classify_director_tenure logic raw_content 활용 (필요시)
- `wiki/architecture/audits/data/260508_parser_audit/` — 진단 데이터
- `wiki/lessons/parser-precision-260508.md` — lesson
- `wiki/decisions/260508_NNNN_decision_parser-precision.md` — decision

## 비목표

- 다른 38 파서 — Audit 결과 적정 (변경 X)
- 3-tier fallback 자체 구조 — 변경 X
- _law_layer 룰 catalog 정리 — 별도 ralph (raw 통합은 본 ralph에서 X)
- _decide_director_election 같은 분류기 logic 변경 — 별도 ralph

## archive 폴더

`wiki/architecture/audits/data/260508_parser_audit/`

---

## iteration log

### iter 1 — careerDetails 누락 진단
(작성 예정)

### iter 2 — raw_content fallback 추가
(작성 예정)

### iter 3 — 5년 룰 작동 검증
(작성 예정)

### iter 4 — aoi amendments 누락 진단
(작성 예정)

### iter 5 — aoi fallback 패턴 추가
(작성 예정)

### iter 6 — 280 회사 회귀
(작성 예정)

### iter 7 — 문서화 + promise
(작성 예정)
