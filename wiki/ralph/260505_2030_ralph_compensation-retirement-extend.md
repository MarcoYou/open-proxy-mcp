---
type: ralph
title: 보수한도 / 퇴직금 분기 확장 검증 + parser 강화 (이전 ralph 후속)
created: 2026-05-05 20:30
completion_promise: COMPENSATION_RETIREMENT_EXTENDED_VERIFIED
max_iterations: 8
ref:
  - wiki/ralph/260505_1750_ralph_compensation-retirement-split.md
  - wiki/decisions/260505_1900_decision_compensation-retirement-split.md
---

## Invoke (복붙)

```
/ralph-loop:ralph-loop wiki/ralph/260505_2030_ralph_compensation-retirement-extend.md 가이드 따라 보수한도 퇴직금 분기를 KOSPI 200 KOSDAQ 50 안한 기업 169개로 확장 검증 + 퇴직금 parser NO_DATA 케이스 강화. 파싱 성공률 99 퍼센트 이상 + 운용사 majority 정합도 90 퍼센트 이상 + AGAINST REVIEW 트리거 분포 합리성 + NPS 정책 정합 100 퍼센트 모두 충족 시 promise. --completion-promise COMPENSATION_RETIREMENT_EXTENDED_VERIFIED --max-iterations 8
```

# Ralph: 보수한도 / 퇴직금 분기 확장 검증 + parser 강화

## Context

이전 ralph (260505_1750)에서 보수/퇴직 분기 + hybrid wire 완료. 단 KOSPI 50 + KOSDAQ 30 (n=80) 표본 부족 + 퇴직금 parser NO_DATA 3건 (G1 40%) 발생 → promise 미발행.

이번 ralph는:
1. 안한 회사로 확장 (KOSPI 50-200 = 149 + KOSDAQ 30-50 = 20, 총 169)
2. 확장 batch에서 새 NO_DATA / AGAINST / REVIEW case 수집
3. parser 강화 (이전 ralph NO_DATA 3건 + 신규 NO_DATA case)
4. threshold calibrate (lessons/distribution-calibrated-thresholds 패턴)
5. 전체 통합 (KOSPI 200 + KOSDAQ 50, n=249) 최종 검증 → promise

## 이전 ralph 결과 요약 (출발점)

KOSPI 50 + KOSDAQ 30 hybrid (n=80, 76 ok):

| 카테고리 | n | 분포 | G1 | G3 |
|---|---|---|---|---|
| director_compensation | 39 | FOR 39 | 100% ✓ | 100% (7/7) ✓ |
| audit_compensation | 11 | FOR 11 | 100% ✓ | majority 0 |
| retirement_pay | 5 | REVIEW 2 / NO_DATA 3 | 40% ❌ | majority 0 |

**미해결**:
- 퇴직금 parser NO_DATA: 에스티팜 / 원익IPS / 에코프로비엠 (3건)
- 트리거 sample 부족: 모든 결정 FOR (단 retirement REVIEW 2건만) — AGAINST/REVIEW 분기 정확도 검증 불가
- audit_compensation 4+ majority case 0 — G3 미측정
- 1인당 평균 threshold (잠정 5천만/1억) 실제 분포로 calibrate 안 됨

## 가정

- No conversation context / no web search / MCP only / deterministic
- 분당 DART 1000회 hard rule (rolling cap 900)
- v2 production (fly.toml `OPEN_PROXY_TOOLSET=v2`) 기준
- 이전 ralph push 코드 (`3456385`) 기준 + 추가 fix

## 매 iteration 작업
1. 현황: git status + 직전 검증 csv
2. 다음 1 step만 (작게 쪼갬)
3. fix 검증: 회귀 spot
4. commit (의미 있는 변경마다)
5. 다음 iter 1줄

---

## 성공 기준 (모두 충족 시 promise)

### G1. 퇴직금 + 보수한도 파싱 성공률 ≥99%
KOSPI 200 + KOSDAQ 50 전체 (n=249)에서:
- 보수한도 (이사+감사): NO_DATA 비율 ≤1%
- 퇴직금: NO_DATA 비율 ≤1% (현재 60% — parser 강화 필요 핵심)

### G2. 분기 정확도 — 트리거 sample 확보
AGAINST/REVIEW 분기가 실제로 작동하는지 검증. KOSPI 200 + KOSDAQ 50에서:
- AGAINST trigger ≥3건 발생 (자본잠식 / 적자+인상 / 황금낙하산 / 사외이사 퇴직금 / 지급률 2배수 등)
- REVIEW trigger ≥10건 발생 (50%+ 인상 / 1인당 평균 경계 / amendments raw 등)
- 각 trigger 발생 시 분기 logic 정확 작동 spot 검증

### G3. 운용사 majority 정합도 ≥90%
4+ majority case 기준. 이번 sample에서 majority case 더 나옴 (KOSDAQ 추가 + KOSPI 후반):
- 하이브 director_compensation 3대1 AGAINST (이전 cache에서 확인)
- 에코프로 retirement_pay 3대0 AGAINST (이전 cache에서 확인)
- 위 outlier에서 OPM 결정 정합 검증

### G4. NPS 정책 정합 100%
NPS [별표 1] IV-33/34/35 trigger 발생 case에서 OPM 결정이 NPS 정책과 일치:
- 이사 보수한도 한도 과다 (IV-33②) → AGAINST
- 감사 보수한도 1인당 평균 과소 (IV-34) → AGAINST
- 퇴직금 황금낙하산 / 경영권 변동 (IV-35①) → AGAINST

---

## 작업 plan

### Step 1. 확장 batch 실행 (KOSPI 50-200 + KOSDAQ 30-50)

149 + 20 = **169 회사**. 30 회사 batch + offset + sleep 30s + 다음 batch.

```
batch 1: KOSPI offset=50 sample=30  → iter01_kospi_50-80.json
batch 2: KOSPI offset=80 sample=30  → iter01_kospi_80-110.json
batch 3: KOSPI offset=110 sample=30 → iter01_kospi_110-140.json
batch 4: KOSPI offset=140 sample=30 → iter01_kospi_140-170.json
batch 5: KOSPI offset=170 sample=30 → iter01_kospi_170-200.json (실제 ~29 회사)
batch 6: KOSDAQ offset=30 sample=20 → iter01_kosdaq_30-50.json
```

총 6 batch × ~10-15분/batch = 60-90분.

DART rate limit 안전:
- concurrency 2 / batch
- sleep 30s 사이
- 1 batch 끝나야 다음 시작 (sequential)
- rolling cap 900/min 보호

### Step 2. NO_DATA 케이스 수집 + parser miss 패턴 분석

확장 batch 결과에서 retirement_pay NO_DATA case 수집 → 본문 fetch + 직접 spot.

이전 NO_DATA 3건 (에스티팜 / 원익IPS / 에코프로비엠) + 신규 detect.

분석 포인트:
- 안건 title 형식 ("임원 퇴직금 지급규정 개정의 건" / "정관 일부 변경" 묶음)
- 본문 변경전/후 테이블 형식 — 표준 vs 비표준
- ACODE marker 존재 여부 (lessons/acode-semantic-markers 참고 — 표준 서식이면 99%+ 가능)
- 직접 본문 grep — "퇴직금" 키워드 위치 + 인접 표 구조

`scripts/spot_retirement_no_data.py` 신규:
- NO_DATA 회사 list 입력
- DART 본문 fetch
- HTML 구조 + 표 raw 출력
- 패턴 분류 (예: 표 머리에 "변경전/후" 미명시 / 별표 형식 / 본문 텍스트만)

### Step 3. parser 강화

Step 2 분석 결과로 `parse_retirement_pay_xml` 강화:
- 표 머리 패턴 다양화 (현재 "변경전/현행", "변경후/개정안" — 추가 패턴 발견 시)
- 별표 형식 (table without before/after columns) fallback
- 본문 텍스트 분석 fallback (표 형식 아닌 case)

목표: NO_DATA 60% → 1% 미만 (G1 99%+).

### Step 4. threshold calibrate

KOSPI 200 + KOSDAQ 50 (이전 + 신규) 데이터로:
- audit_compensation 1인당 평균 분포 측정 → 25/50 percentile으로 threshold_low / threshold_high 재설정
- director_compensation increase_rate / utilization_rate 분포 spot
- retirement_pay 위험 키워드 hit 분포 — false positive 추가 fix

### Step 5. 통합 검증 (G1-G4)

전체 (KOSPI 200 + KOSDAQ 50, n=249) 결과로:
- G1 파싱 성공률 측정
- G2 AGAINST/REVIEW 트리거 분포
- G3 운용사 majority 정합도 (4+ majority case)
- G4 NPS 정책 정합 spot 검증 (하이브 / 에코프로 outlier)

### Step 6. 부족하면 추가 fix + 회귀 검증

threshold 조정 후 회귀 측정. distribution + 정합도 재확인.

### Step 7. 문서화

- ralph 문서 (이번 페이지) iteration log 추가
- `wiki/decisions/260505_1900_decision_compensation-retirement-split.md` 검증 결과 update
- `wiki/log.md` 신규 entry
- (필요 시) `wiki/lessons/` 추가 lesson — parser miss 패턴 / KOSPI 200 calibrate

---

## 영향 범위

- `open_proxy_mcp/tools/parser.py` (`parse_retirement_pay_xml` 강화) — Step 3
- `open_proxy_mcp/services/proxy_advise.py` (threshold + 키워드 정밀화) — Step 4
- `scripts/spot_retirement_no_data.py` (NEW) — Step 2
- `scripts/ralph_compensation_retirement_audit.py` (이미 있음, 확장 호출만)
- `wiki/architecture/audits/data/260505_compensation_retirement_extend/` — 검증 csv

## 비목표 (이번 ralph X)

- 스톡옵션 부여 안건 (별도 ralph)
- 임원 5억원+ 보수 본문 분석 (별도 endpoint)
- KOSDAQ 50 외 추가 회사 (KOSDAQ 150 universe 확장 — 별도)
- frontend / 새 tool 추가

## 가설 / 위험

- **위험 1 (DART rate)**: 6 batch sequential + 169 회사 × 평균 10-15 호출 = ~2000 호출. 60-90분 분산. 분당 cap 900 초과 위험 X (sequential).
- **위험 2 (parser 한계)**: 이전 ralph parse_personnel처럼 데이터 자체 한계 (표준 서식 X) 가능성. → archive 폴더에 정직 fail 케이스 보존 (lessons/ralph-threshold-realism 패턴).
- **위험 3 (calibrate over-fit)**: KOSPI 200 + KOSDAQ 50 표본에 overfit한 threshold가 외부 universe (KOSDAQ 100+ 등)에 안 맞을 수 있음. → audit data 구간 명시 + 향후 universe 확장 시 재calibrate 노트.
- **위험 4 (8 iter 부족)**: 169 회사 batch 60-90분 + parser 분석 + fix + 재검증. 시간 빠듯. 부족하면 promise 못 함 (정직 종료).

## archive 폴더

`wiki/architecture/audits/data/260505_compensation_retirement_extend/`

---

## iteration log (작성하면서 update)

### iter 1 (T0~+30분) — 6 batch BG 시작 + Step 0 NO_DATA spot + parser fallback 1차
- 6 batch chain BG: KOSPI 50-200 (5 batch × 30) + KOSDAQ 30-50 (1 batch × 20) = 169 회사
- spot 4 회사 (에스티팜 / 원익IPS / 에코프로비엠 / SK하이닉스):
  - 모두 본문에 "변경전"/"변경후" 표 있으나 parser miss (anchor "퇴직금" 안건 detect 실패)
- `parse_retirement_pay_xml` fallback 추가 (commit `45c50d3`):
  - `_extract_amendments_from_table_rows` helper 분리 (재사용)
  - `_retirement_fallback_from_html` — BS4로 HTML 전체 표 스캔
  - row-level strict — amendment의 before/after/reason에 "퇴직금" 직접 등장 시만
- 검증 (4 회사):
  - 원익IPS 0 → 4 amendments
  - 에코프로비엠 0 → 1 amendment
  - SK하이닉스 11 (변동 없음, primary 성공)
  - 에스티팜 여전히 0 — 표 header가 "개정 전 내용" / "개정 후 내용" (다른 패턴)

### iter 2 (T+30~+60분) — 에스티팜 분석 + 개정전/후 키워드 fix + batch 50-80 결과
- 에스티팜 HTML structure: 47 tables, 표 head "개정 전 내용" / "개정 후 내용" — 현재 parser는 "개정"만 보고 둘 다 after_idx로 매칭 → before_idx -1 → skip
- fix (commit `42a1b3a`): 키워드 정밀화 — `[변경전, 개정전, 현행]` vs `[변경후, 개정후, 개정안, 변경(안)]`. "개정"만으로는 매칭 X. elif로 충돌 방지.
- 검증:
  - 에스티팜 0 → 2 amendments ✓
  - 에코프로비엠 1 → 8 amendments (정관변경 표 안에 퇴직금 row까지)
  - 원익IPS 4 유지
  - SK하이닉스 11 유지
- batch 50-80 완료 (OLD parser 사용 — 시작 시점 parser 변경 전):
  - director_compensation: 12 FOR (g3 100% 2/2)
  - audit_compensation: 0
  - retirement_pay: 3 (NO_DATA 1 — 카카오페이 / REVIEW 2 — SK바이오팜 한도/규정 신설, LIG넥스원 지급률 hit)
- 카카오페이 spot — NEW parser로 amends=13 (50-80 batch는 OLD라 NO_DATA, NEW라면 정상)

### iter 3 (T+60~+90분) — 80-110 진행 중 + 분석
- batch 80-110 partial 10/30. NEW parser 적용 (process 시작 시점 = parser 변경 후)
- 운용사 majority cache key 불일치 발견:
  - 하이브 director 안건 title 다양 ("이사 보수한도 승인의 건" / "이사 보수한도액 승인" / "제5호. 이사 보수한도 승인의 건" 등)
  - 운용사마다 title 표기 다름 → cache exact match fail → 4+ majority case 적게 잡힘
  - 별도 작업 필요 (title normalize / fuzzy match)

### iter 4 (T+90~+120분) — 진행 batch 결과 통합 대기
- 80-110 ~10/30 / 110-140 / 140-170 / 170-200 / KOSDAQ 30-50 미시작
- 5 iter 한정 — promise 발행 위한 4 기준 측정 시간 부족 가능성
- Aggregate script 미리 준비 / wiki update 미리 준비

### iter 5 (T+120분~) — 통합 결과 + 정직 종료
(batch 끝까지 못 가면 부분 측정 + honest 종료)
