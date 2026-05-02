---
type: ralph
title: advise_vote Phase 3 — ≥99% consistency 도달 (parser 보강 + retry 강화 + cache)
created: 2026-05-03 02:30
completion_promise: ADVISE_PHASE3_99PCT_DONE
max_iterations: 25
---

# advise_vote Phase 3 — 99%+ deterministic 달성

이전 Phase 2 (260503_0030_ralph_advise-200기업-가상실험) 결과:
- 200 × 3 = 597 호출 ✅ 완료 (37분)
- 일치율 **91.4%** (180/197) — gate ≥95% 미달
- 불일치 17 회사 + 파싱 실패 855건 추출

본 Phase 3 목표: **≥99% consistency** (불일치 ≤2 회사).

## 가정 (가상환경 명시 — Phase 2와 동일)
- No current conversation context
- No web search
- MCP only (17 tool)
- as if it's the first question
- deterministic (temperature=0)

## 매 iteration 작업
1. **현황 확인**: git status + 이전 fix 적용된 결과 csv 확인
2. **다음 1 step만 진행** (검증 가능 단위로 작게 쪼갬)
3. **fix 검증**: 5-20 회사 spot 재실행 후 일치율 측정
4. **commit** (의미 있는 변경마다)
5. 다음 iteration 1줄

---

## 성공 기준 (모두 충족 시 promise)

### 코드 fix (Phase 2 audit에서 도출)

#### F1. retry 강화 (1회 → 3회 + exponential backoff)
- `advise_vote.py`의 `_safe` wrapper:
  - 현재: `for attempt in range(2)` + `sleep(0.5)`
  - 개선: `for attempt in range(3)` + `sleep(0.5 * 2^attempt)` (0.5/1.0/2.0s)
  - 일시적 timeout / network race 회수율 ↑
- 검증: 호출 600건 중 timeout/error 24+3건 → 0-5건 reduce

#### F2. financial_metrics 응답 caching (TTL 5분)
- `services/financial_metrics.py`의 `build_financial_metrics_payload`:
  - LRU cache 또는 dict cache (회사 + scope + year 키)
  - TTL 5분 (datetime 비교)
  - 같은 advise_vote 3 run 내에서는 동일 fm 결과 보장 → 불일치 1 안건 변동 fix
- 영향: KT&G / 현대건설 / JB금융지주 / LG이노텍 등 1 안건 변동 0으로

#### F3. parser 보강 — 855 파싱 실패 reduce

**F3a. career_period_reverse 자동 swap (637 → 0)**
- `tools/parser.py` `_parse_career_period`:
  - 시작 > 끝 발견 시 자동 swap (예: "2024 ~ 2021" → start=2021, end=2024)
  - 단 1813 같은 비정상 연도는 invalid로 skip 유지

**F3b. agenda_section_missing 패턴 다양화 (116 → ≤30)**
- `tools/parser.py` `parse_agenda_xml` / `parse_agenda_details_xml`:
  - 현재 keyword: "목적사항별 기재사항", "회의목적사항/결의사항/부의안건"
  - 추가: "회의의 목적사항", "결의사항", "안건", "의결사항", "회의 안건"
  - HTML/XML 다양 변형 대응 (table 구조 / div 구조 / p 구조 모두)

**F3c. image_notice_ocr_needed Upstage fallback (15 → ≤3)**
- 본문이 이미지 base64로만 있는 경우:
  - `_ENV_LOCAL`의 `UPSTAGE_API_KEY` 사용
  - Upstage Document AI OCR endpoint 호출
  - 결과 텍스트로 parse_agenda_xml/parse_personnel_xml 재호출
  - cost: ~10s 추가 (배치당)

#### F4. status no_filing/error 시 이전 결과 caching
- 같은 process 내 동일 회사 호출이 fail이면 이전 정상 결과 reuse
- 또는 status 변동 자체를 재시도 trigger
- 카카오뱅크 / BNK금융지주 / JB금융지주 변동 fix

#### F5. timeout 90s → 120s
- `asyncio.wait_for(timeout=120.0)` — 이전 timeout 3건 회수

### Sanity test

#### S1. spot 검증 (20 회사 × 3, ≤5분)
- KT&G / 현대건설 / JB금융지주 / LG이노텍 (불일치 회사 4개) + KOSPI 8 + KOSDAQ 8
- target: 일치율 ≥95% (n=20)

#### S2. 200 × 3 = 597 회사 재실행 (≤45분)
- 이전과 동일 universe (`260503_universe_200.csv`)
- nohup + incremental csv (`260503_advise_200x3_phase3.csv`)
- target: **일치율 ≥99%** (불일치 ≤2 회사)

#### S3. 파싱 실패 reduce 검증
- 새 log 파싱 + csv 비교 (`260503_parsing_failures_phase3.csv`)
- target:
  - career_period_reverse: 637 → 0 (자동 swap)
  - agenda_section_missing: 116 → ≤30
  - image_notice_ocr_needed: 15 → ≤3

### Audit + 문서
- [ ] `wiki/architecture/audits/{yymmdd_hhmm}_audit_advise-phase3-99pct.md`:
  - Phase 2 vs Phase 3 일치율 비교 표 (91.4% → ?%)
  - 17 불일치 회사 → 새 결과 비교
  - 파싱 실패 reduce 통계
  - 응답 시간 변동 (cache 효과)
- [ ] `wiki/log.md` — Phase 3 완료 entry

---

## 종료 조건

### ✅ promise 출력 조건
1. 200 × 3 = 597 호출 완료 + **일치율 ≥99%** (불일치 ≤2 회사)
2. 파싱 실패 reduce 통계 audit
3. git push 완료
4. 마지막 commit 메시지 명시

→ **`<promise>ADVISE_PHASE3_99PCT_DONE</promise>` 출력**

### ⚠ 막힘 발생 시
- F2 cache 구현 후에도 변동 발생 (DART API row 순서 자체 비결정성) → CONFLICT 명시
- F3b agenda 패턴 추가해도 일부 회사 본문 구조 매우 특이 → 개별 case STATUS REPORT
- 200 × 3 ≥99% 안 되면 (예: 96-98%) → 정직 보고 + 사용자 결정 요청

---

## 반복 단위 (작은 step)

좋은 1 iteration 단위 예시:
- "F1 retry 3회 적용 + 5 회사 spot 재실험"
- "F2 fm cache 구현 + KT&G/현대건설 spot 일치율 100% 검증"
- "F3a career period swap + 파싱 실패 csv 재추출 (637 → 0 검증)"
- "F3b agenda 패턴 추가 (5개) + 116 fail 회사 일부 spot 재실험"
- "F3c Upstage OCR fallback 통합 + 이미지 회사 spot"
- "F4 status caching + 이전 변동 회사 spot"
- "S2 200 × 3 재실행 + 일치율 측정"

너무 큰 step (예: "F1+F2+F3 한 번에") 금지. 하나씩 검증.

---

## 참고 — Phase 2 finding

### Phase 2 audit (`260503_0130_audit_advise-200-virtual.md`)
- root cause: financial_metrics _safe silent fallback 시 cash_dividend → REVIEW
- retry 1회 fix → 95% 도달 (n=20)
- 200 × 3 결과: 91.4% (n=197) — n 크면 변동 더 보임

### Phase 2 결과 csv (비교 baseline)
- `wiki/architecture/audits/data/260503_advise_200x3_final.csv` (597 rows)
- `wiki/architecture/audits/data/260503_parsing_failures.csv` (855 rows)

### 17 불일치 회사 (Phase 2)
HMM, 한전기술, 현대건설, JB금융지주, LG이노텍, 에스티팜, 피에스케이, 휴젤, 두산테스나, 카카오뱅크, BNK금융지주, LG디스플레이, 동진쎄미켐, 원익홀딩스, 오리온, 현대글로비스, 현대오토에버

→ Phase 3 후 이 17 회사 모두 일치 100% 되어야 99%+ 달성 가능.

---

## 명명
- 이 ralph 파일: `wiki/ralph/260503_0230_ralph_advise-phase3-99pct.md` (이미 정확)
- audit 페이지: `wiki/architecture/audits/260503_HHMM_audit_advise-phase3-99pct.md`
- 결과 csv: `wiki/architecture/audits/data/260503_advise_200x3_phase3.csv`
- 파싱 실패 csv: `wiki/architecture/audits/data/260503_parsing_failures_phase3.csv`
