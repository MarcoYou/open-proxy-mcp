---
type: log
title: Ralph Invoke History
updated: 2026-05-04
---

# Ralph Invoke History

각 ralph 작업 시 실제 사용한 invoke prompt + 결과.

ralph-loop plugin: https://ghuntley.com/ralph/

---

## 1. financial-metrics-phase1 (2026-05-01)

**파일**: `260501_1547_ralph_financial-metrics-phase1.md`

**invoke**: (history 미기록 — 옛 작업, 추정)

**결과**: financial_metrics 4 endpoint 통합 완료.

---

## 2. advise-recap-vote (2026-05-02)

**파일**: `260502_0930_ralph_advise-recap-vote.md`

**invoke**: (history 미기록 — 옛 작업, 추정)

**결과**: advise_vote_before_meeting + recap_vote_after_meeting 2 신규 action tool.

---

## 3. advise-200기업-가상실험 (Phase 2, 2026-05-03)

**파일**: `260503_0030_ralph_advise-200기업-가상실험.md`

**invoke**: (history 미기록 — 추정)

**결과**:
- 200×3 batch 91.4% (target ≥95% 미달)
- audit: 260510_proxy_advise_audit_통합정리.md (옛 `260503_0130_audit_advise-200-virtual.md` 흡수)

---

## 4. advise-phase3-99pct (Phase 3, 2026-05-03)

**파일**: `260503_0230_ralph_advise-phase3-99pct.md`

**invoke**:
```
/ralph-loop:ralph-loop wiki/ralph/260503_0230_ralph_advise-phase3-99pct.md 매 iteration read해서 가이드 따라 작업. 실패 case 만나면 무조건 archive 우선 (260503_failure_archive/, 원문 raw text + 분석 6항목). soft pattern 우선, hard pattern 다층 fallback (silent X), OCR/PDF study only runtime parser only, F0-F5 .py commit, regression 0, 200×3 ≥99% 모두 충족 후 promise. --completion-promis ADVISE_PHASE_3_99PCT_DONE
```

**파라미터**:
- max_iterations: (없음)
- completion_promise: `ADVISE_PHASE_3_99PCT_DONE`

**결과**:
- 200×3 batch: 91.9% (target 99% 미달, regression 6 회사)
- promise 출력 X (정직)
- audit: `260510_proxy_advise_audit_통합정리.md` (옛 `260503_0500_audit_phase3_final.md` 흡수)

**후속 작업** (사용자 명시 추가 → ralph 외부 진행):
- Phase 4 fix (F6-F11): corpCode race + cache + 정정공고
- 결과: 200×3 100% + regression 0 ✅
- audit: `260503_1847_audit_phase4_final.md`

---

## 5. proxy-advise-verification (2026-05-04, 메인 작업)

**파일**: `260503_0002_ralph_proxy-advise-verification.md`

### 시도 1 (shell escape 실패)
```
/ralph-loop:ralph-loop wiki/ralph/260503_0002_ralph_proxy-advise-verification.md 매 iteration read해서 3 gate 가이드 따라 작업. 실패 case 만나면 무조건 archive 우선 (wiki/architecture/audits/data/260504_proxy_advise_failure_archive/, 원문 raw + 분석 6항목). soft pattern 우선, hard pattern 다층 fallback (silent X), OCR/PDF 진단 study only runtime parser only. G1 일관성 G2 정확도 95이상 G3 사실 100 regression 0 모두 충족 후 promise PROXY_ADVISE_VERIFIED. --completion-promise PROXY_ADVISE_VERIFIED --max-iterations 10
```

**문제**: 한글 콤마/슬래시 shell escape error. 단순화한 prompt로 재시도.

### 시도 2 (성공, 95% target)
```
/ralph-loop:ralph-loop wiki/ralph/260503_0002_ralph_proxy-advise-verification.md 가이드 따라 3 gate 검증. archive 우선. soft pattern 우선 hard pattern 다층 fallback. OCR 진단 only parser final. G1 일관성 G2 정확도 95이상 G3 사실 100 regression 0 모두 충족 시 promise. --completion-promise PROXY_ADVISE_VERIFIED --max-iterations 20
```

### 시도 3 (99% 수정 후 진행)
```
/ralph-loop:ralph-loop wiki/ralph/260503_0002_ralph_proxy-advise-verification.md 가이드 따라 3 gate 검증. archive 우선. soft pattern 우선 hard pattern 다층 fallback. OCR 진단 only parser final. G1 일관성 99이상 G2 정확도 99이상 G3 사실 100 regression 0 모두 충족 시 promise. --completion-promise PROXY_ADVISE_VERIFIED --max-iterations 20
```

**파라미터**:
- max_iterations: 20
- completion_promise: `PROXY_ADVISE_VERIFIED`

**결과** (iter 20 + 사용자 명시 추가 7 iter = 총 27 iter):
- baseline 32.4% → batch v7b 4+ majority 99.36% ✅
- G1/G2/G3/regression 모두 충족
- 핵심 fix: birth_date age bug (iter22) + 퇴직금 (iter26) + wire (iter27)
- archive: `260504_proxy_advise_failure_archive/iter01-iter27_*.md`
- audit final: `260510_proxy_advise_audit_통합정리.md` (옛 `260504_0705_audit_proxy_advise_ralph_final.md` 흡수)

---

## Invoke Prompt 작성 가이드

ralph plugin shell escape 회피 + iteration 한도 명시:

### 안전 패턴
- 한글 콤마 `,` → 띄어쓰기로 분리
- 슬래시 `/` → 단어 분리 또는 우회 표현
- 여러 줄 → 한 줄로 (큰따옴표 안)
- max_iterations 명시 (디폴트 무한)
- completion_promise 명시 (디폴트 없음 — 무한)

### 권장 프롬프트 골격
```
/ralph-loop:ralph-loop wiki/ralph/{path} 가이드 따라 {목적}. archive 우선. soft pattern 우선 hard pattern 다층 fallback. {gate 1} {gate 2} {gate 3} 모두 충족 시 promise. --completion-promise {PROMISE_TAG} --max-iterations {N}
```

### Invoke 박스 ralph md 최상단
각 ralph md frontmatter 직후 "## Invoke" 섹션 — 사용자가 복붙해서 실행 가능.

---

## 6. parse-personnel-xml-verification (2026-05-04, 신규)

**파일**: `260504_0014_ralph_parse-personnel-xml-verification.md`

**목적**: proxy_advise ralph 잔여 unique 3건 root cause `parse_personnel_xml` 한계 fix.
- 8 필드 검증: name / birthDate / 전현직 / role_type / period / 정렬 / 포맷 / 한자한글
- 전수조사 500 회사 sample
- careerDetails 비어 있는 비율 ≤10% target

**invoke**:
```
/ralph-loop:ralph-loop wiki/ralph/260504_0014_ralph_parse-personnel-xml-verification.md 가이드 따라 parse_personnel_xml 8 필드 검증 + 강화. archive 우선. soft pattern 우선 hard pattern 다층 fallback. OCR 진단 only parser final. 전수조사 sample 500 회사 + 8 필드 success rate 95이상 + careerDetails 비어있는 비율 30이하 모두 충족 시 promise. --completion-promise PARSE_PERSONNEL_VERIFIED --max-iterations 20
```

**파라미터**:
- max_iterations: 20
- completion_promise: `PARSE_PERSONNEL_VERIFIED`

**결과**: (진행 전)

---

## 종합 통계

| ralph | iter | 결과 | 핵심 finding |
|---|---|---|---|
| financial-metrics-phase1 | - | ✅ 완료 | 4 endpoint 통합 |
| advise-recap-vote | - | ✅ 완료 | 2 action tool 신규 |
| advise-200기업-가상실험 | - | ⚠ 91.4% (target 95%) | 비결정성 root cause: financial_metrics race |
| advise-phase3-99pct | - | ⚠ 91.9% + regression 6 | corpCode race + cache 부재 (Phase 4에서 해결) |
| proxy-advise-verification | 27 | ✅ 99.36% (4+ majority) | birth_date age bug + 퇴직금 + wire |
| parse-personnel-xml-verification | (진행 전) | (대기) | parse_personnel_xml 8 필드 강화 — 전수조사 500 회사 |

총 6 ralph 작업, 5 완료 (1 promise 충족), 1 대기.
