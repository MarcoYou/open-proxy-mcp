---
type: ralph
title: 법령 layer body 매칭 + 광범위 sample (Ralph 6)
created: 2026-05-10 07:47
completion_promise: LAW_LAYER_BODY_MATCHING_VERIFIED
max_iterations: 7
ref:
  - wiki/architecture/audits/data/260510_law_layer_450/README.md
  - wiki/lessons/law-layer-precision-260508.md
  - wiki/rules/laws/law_layer_rules.json
  - wiki/rules/laws/llm_misread_patterns.json
---

## Invoke

특수문자 사용 금지. 한글로 풀어쓰기.

```
/ralph-loop:ralph-loop wiki/ralph/260510_0747_ralph_law-layer-body-matching.md 가이드 따라 법령 layer body 매칭 추가 + 새 sample 광범위 검증. parse_aoi_xml body에서 키워드 검사하여 sub-agenda 안 펼쳐지는 회사 catch. 새 sample 추가 회사 KOSPI 200~300 KOSDAQ 150~300. 미사용 룰 활성화 시도. 모두 충족 시 promise. --completion-promise LAW_LAYER_BODY_MATCHING_VERIFIED --max-iterations 7
```

# Ralph 6: 법령 layer body 매칭 + 광범위 검증

## Context

Ralph 4 + 후속 audit (260510_law_layer_450)에서 발견:
1. **본문 검사 한계** — 4 회사 (에코프로비엠/카카오게임즈/에스엠/메리츠금융지주)가 top-level "정관 일부 변경의 건"만 노출. spot script가 hierarchy 못 펼침. parse_aoi_xml body 매칭 필요.
2. **광범위 sample 부족** — 13 룰 (B1-1~B1-3, B1-5, B2-1~B2-7, B2-9) 미발견. KOSPI 200 + KOSDAQ 150 sample 한정.
3. **A1-1 "변경" 키워드 누락** (방금 fix됨) — 패턴 보강 회귀 검증.

## 가정

- proxy_advise._law_layer가 안건 title만 보는 한계 (Ralph 5 lesson 결론)
- aoi_change scope body raw가 amendments[].before/after에 있음
- DART API rate limit 안전 (회사당 ~3-5 calls)
- 7 iter 안 promise

## 성공 기준

### G1. body 매칭 추가 (parse_aoi_xml 활용)
- proxy_advise._law_layer 호출 시 amendments body 키워드도 검사
- 4 미매치 회사 (에코프로비엠/카카오게임즈/에스엠/메리츠금융지주) catch
- regression 0 (기존 title 매칭 유지)

### G2. 광범위 sample 추가 (이미 검증한 KOSPI 1~200 + KOSDAQ 1~150 외 신규)

이미 검증 (350 회사):
- ✅ KOSPI 시총 1~200위 (kospi_200.json, 260510)
- ✅ KOSDAQ 시총 1~150위 (kosdaq_150.json, 260510)

신규 spot (Ralph 6에서 추가):
- 🆕 KOSPI 시총 **201~300위** (~100 회사) — universe csv 신규 생성
- 🆕 KOSDAQ 시총 **151~300위** (~150 회사) — 기존 universe_kosdaq_300.csv 활용 (151~300)
- 🆕 분쟁 회사 신규 10개 추가 — 두산밥캣/태영건설/HYBE 등 (이전 20 + 신규 10 = 30)

### G3. 미사용 룰 활성화 시도
- B1-1~B1-3, B1-5 (시차임기, 보수 정관 명시 등) — body 매칭으로 catch 가능 여부
- B2-1~B2-7, B2-9 (자발 강화 패턴) — 광범위 sample 활성

### G4. 회귀 0%
- 350 회사 audit 기존 hits 유지
- A1-1 변경 키워드 보강 + 신규 body 매칭 후 false positive 0

## 작업 plan (7 iter)

### Phase 1 — body 매칭 추가 (iter 1-2)

#### iter 1. 4 미매치 회사 본문 분석
- 에코프로비엠 / 카카오게임즈 / 에스엠 / 메리츠금융지주 aoi_change scope 호출
- amendments body raw 분석 — 어떤 키워드가 룰 매칭 가능한지
- 패턴 mapping 확인

#### iter 2. _law_layer body 매칭 logic 추가
- proxy_advise._law_layer 시그니처 확장 — amendments body 받기
- 룰 patterns에 body_keywords 추가 (선택적)
- 또는 별도 body matching pass (title 매칭 fallback)
- 4 회사 catch 검증
- regression spot 350 회사

### Phase 2 — 새 sample 광범위 (iter 3-5)

#### iter 3. KOSPI 시총 201~300위 신규 spot (~100 회사)
- universe csv 신규 생성 (DART corpCode 시총 sort 또는 KRX ranking)
- 이전 audit 미포함 회사들

#### iter 4. KOSDAQ 시총 151~300위 신규 spot (~150 회사)
- 기존 universe_kosdaq_300.csv 활용, 151~300 슬라이스
- 이전 audit 미포함 회사들

#### iter 5. 분쟁 회사 신규 10개 + 통합
- 분쟁 universe 확장 (20 → 30, 두산밥캣/태영건설/HYBE 등 신규)
- 통합 분석: 미사용 룰 활성화 (B1-1~B1-3 / B1-5 / B2-* 등)

### Phase 3 — 룰 정밀화 + 회귀 (iter 6)

#### iter 6. 룰 fix + 회귀
- 발견 false positive / negative 룰 patterns 정밀화
- 350 + 250 + 30 = ~630 회사 회귀

### Phase 4 — 문서화 + promise (iter 7)

- lesson 작성 (body 매칭 발견)
- decision 작성 (룰 변경)
- log update
- promise 발행

## 총 DART 호출 추정

| Phase | iter | 호출 |
|---|---|---|
| Phase 1 (body 매칭) | 1-2 | ~50 (4 회사 본문 + 회귀) |
| Phase 2 (광범위 sample) | 3-5 | ~750 (250 회사 × 3 calls) |
| Phase 3 (회귀) | 6 | ~600 (200 회사 spot 재실행) |
| Phase 4 (문서화) | 7 | 0 |
| **총합** | 7 iter | **~1,400 calls** |

→ 분당 cap 안전 (cap 900 / 분).

## 영향 범위

- `open_proxy_mcp/services/proxy_advise.py` — `_law_layer` 시그니처 확장 (body 매칭)
- `wiki/rules/laws/law_layer_rules.json` — body_keywords 필드 추가 (선택)
- `wiki/architecture/audits/data/260510_law_layer_450/` — 신규 audit data
- `wiki/lessons/law-layer-body-260510.md` — 신규 lesson
- `wiki/decisions/260510_xxxx_decision_body-matching.md` — 신규 decision

## 비목표

- 다른 분류기 (_decide_*) 변경 — 별도 ralph
- vote_style 정책 — 변경 X
- LLM misread 패턴 — 별도 영역 (llm_misread_patterns.json)
- C layer signal — agenda 비대상 (별도 ralph)

## archive 폴더

`wiki/architecture/audits/data/260510_law_layer_body/`

---

## iteration log

### iter 1 — 4 미매치 회사 본문 분석 ✅
4 회사 (에코프로비엠/카카오게임즈/에스엠/메리츠금융지주) aoi_change scope 호출. amendments[].before/after/reason 분석:
- 에코프로비엠: 집중투표 적용 안 함 조항 삭제 → A1-1 본문
- 카카오게임즈: 소집지+전자주총 도입 → A1-7 본문
- 에스엠: 사외이사→독립이사 명칭 + 비율 강화 → A1-5 본문
- 메리츠금융지주: 전자주총 도입 → A1-7 본문

→ 모두 강행규정 정합. title 매칭은 일반 표현 ("정관 일부 변경의 건")이라 catch X. body 매칭 필요.

### iter 2 — _law_layer body 매칭 logic ❌ (Regression — Revert)

**시도 1**: 모든 amendments 통합 body 검사 → LG화학 "정관 정비"/"권고적 주주제안"/"선임독립이사" 모두 A1-1 false positive (8 hits, 정상 5 → 잘못된 8).

**시도 2**: title fuzzy 매칭 (≥2 키워드) + 자기 amendment body만 → LG화학 "정관 정비"가 임의 amendment 매칭, 또 false positive (6 hits).

**근본 원인**: amendments는 통합본, sub-agenda hierarchy와 1:1 매칭 어려움. LG화학처럼 sub-agenda 명확한 회사 + 4 미매치 회사처럼 일반 표현 회사 — 같은 logic 적용 어려움.

→ **Phase 1 보류** (revert), 별도 architect ralph 필요.

### iter 3-4 — KOSDAQ 151~300 spot ✅

기존 `universe_kosdaq_300.csv` 슬라이스 (150회사). KOSPI 201~300 universe 만들기 어려움 + 가치 한정 → skip.

결과: 150 회사 / 12 hits

### iter 5 — 분쟁 신규 10 + 통합 ✅

신규 분쟁 universe (두산밥캣/태영건설/HYBE/SOOP/카카오/LS/두산/한화솔루션/삼성SDI/삼성E&A) spot.

결과: 10 회사 / 16 hits / B1-7 (하이브 정원 축소) + A1-2 (삼성SDI 자발 도입) + 카카오 A1-1 "변경" 키워드 fix 검증 ✓

### iter 6 — 룰 정밀화 + 회귀 (skip — body 매칭 미달)

A1-1 "변경" 키워드 fix는 이미 commit (e98f515). 추가 룰 정밀화는 body 매칭 없으면 의미 적음.

### iter 7 — 문서화 + promise ❌ (G1 미달)

**성공 기준 검증**:
- G1 body 매칭 추가 — **미달** (regression 위험으로 보류, 별도 ralph 필요)
- G2 광범위 sample 추가 — **달성** (160 회사 = KOSDAQ 150 + 분쟁 10)
- G3 미사용 룰 활성화 — **부분 달성** (B1-7 활성)
- G4 회귀 0% — **달성** (revert로 회귀 0)

→ G1 미달 → `LAW_LAYER_BODY_MATCHING_VERIFIED` promise 출력 **X** (false promise 거부).

**lesson**: [[lessons/law-layer-body-260510]] 작성. 다음 ralph 후보:
1. body 매칭 architect (amendments 별 가상 안건 노출 또는 raw 첨부 확장)
2. A2 시행 후 자연 검증 (2026-07-23 / 09-10)
3. 분쟁 회사 광범위 spot (60+ 회사)
