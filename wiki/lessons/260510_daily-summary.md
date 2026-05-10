---
type: lesson
title: 2026-05-10 일일 작업 종합 — Ralph 7-10 + 응답 품질 fix (43 commits)
date: 2026-05-10
related:
  - wiki/lessons/agenda-hierarchy-260510.md
  - wiki/lessons/subagenda-mapping-260510.md
  - wiki/lessons/director-faithfulness-260510.md
  - wiki/lessons/career-parser-concat-260510.md
related_ralph:
  - 260510_0823_ralph_agenda-hierarchy-body-fallback
  - 260510_0950_ralph_subagenda-amendment-mapping
  - 260510_1100_ralph_director-faithfulness-enhancement
  - 260510_1200_ralph_career-parser-concat
related_decisions:
  - 260510_0900_decision_d-pattern-body-fallback
  - 260510_1015_decision_subagenda-mapping
  - 260510_1130_decision_director-faithfulness
  - 260510_1230_decision_career-parser-concat
---

# 2026-05-10 일일 작업 종합

## 한줄 요약

LG화학 / 메리츠 응답 검토 trigger부터 시작 → 정관변경 분석 fallback 4단계 cascade 구축 + 사외이사 평가 강화 + parser 정확도 ↑. 4 ralph + fix 1 + 50 회사 검증. 510 회사 회귀 0. 43 commits.

## 시간순 timeline

```
01:00~ 새벽 — 기존 fix (parser ※ note span 등) 작업
08:23  Ralph 7 plan — D 패턴 amendments body fallback
09:00  Ralph 7 iter 1-6 진행 (510 회사 spot, 70건 신규 catch)
09:50  Ralph 8 plan — 카카오게임즈 패턴 sub→amendment 매핑
10:00  Ralph 8 iter 1-6 (510 회사, 75건 신규 catch)
11:00  Ralph 9 plan — 사외이사 충실성 강화
11:30  Ralph 9 iter 1-6 (510 회사, concerns 후보 112)
12:00  Ralph 10 plan — careerDetails parser concat 분리
12:30  Ralph 10 iter 1-6 (510 회사, parser 정확도 ↑)
17:00  응답 품질 측정 (메리츠/LG화학/카카오게임즈 재호출)
17:09  fix — 묶음 후보 detail + raw 중복/매핑 활용
18:23  50 회사 광범위 검증 (verify_50.json)
18:24  /ship — DEVLOG / wiki/log 업데이트
```

---

## 1. Ralph 7 — D 패턴 amendments body fallback (08:23~)

### 목적
4 미매치 회사 (에코프로비엠/카카오게임즈/에스엠/메리츠) catch. 정관변경 안건이 sub-agenda 부재 + top title 일반 표현 ("정관 일부 변경의 건")이라 _law_layer title 매칭 X.

### 목표
- G1: 호수 hierarchy 정확 추출 검증
- G2: D 패턴 (parent="" + charter top + children 0 + amendments) body fallback 구현
- G3: 4 미매치 회사 catch
- G4: 510 회사 회귀 0
- G5: 미사용 룰 활성

### 과정 (6 iter)
| iter | 작업 | 결과 |
|---|---|---|
| 1 | 10 회사 raw vs parser 진단 | parser 99% 정확. LG화학 ※ note span 미세 버그 1건 fix (be2e722) |
| 2 | _law_layer_body 함수 + D 패턴 fallback | 단위 검증: 에스엠 A1-5 catch / LG화학 regression 0 |
| 3 | body_pattern 별도 필드 + A1-1/A1-7 raw 표현 보강 | 4 미매치 중 D 패턴 3개 catch (에코프로비엠 A1-1 / 에스엠 A1-5 / 메리츠 A1-7). 카카오게임즈 D 패턴 X (sub-agenda 있음) |
| 4-5 | 510 회사 spot 회귀 | 회귀 0 + body 70건 신규 catch (69 회사 = 13.5%) |
| 6 | 문서화 + promise | AGENDA_HIERARCHY_EXTRACTION_VERIFIED ✅ |

### 결과
| 항목 | 성공/실패 |
|---|---|
| ✅ G1 호수 hierarchy 정확 추출 | 10/10 회사 검증 |
| ✅ G2 D 패턴 fallback | strict 진입 조건 (children 0 + 정관변경 top) |
| ✅ G3 4 미매치 catch | 3/4 (카카오게임즈 별도 ralph로 분리) |
| ✅ G4 510 회사 회귀 0 | (회사, rule) set diff 검증 |
| ✅ G5 A1-8 (자사주 의무소각) 첫 활성 | Ralph 6 미사용 룰 lesson 첫 catch |
| 부분 실패 | 카카오게임즈 미해결 — Ralph 8로 분리 |

### archive
- lesson: `wiki/lessons/agenda-hierarchy-260510.md`
- decision: `wiki/decisions/260510_0900_decision_d-pattern-body-fallback.md`
- audit: `wiki/architecture/audits/data/260510_agenda_hierarchy/`
- commits: be2e722, e2292d8, 9d15aed, df5855b, 212258e, c08c8fc

---

## 2. Ralph 8 — 카카오게임즈 패턴 sub→amendment 1:1 매핑 (09:50~)

### 목적
Ralph 7에서 미해결한 카카오게임즈 같은 케이스 (sub-agenda 있고 sub title 일반 표현). 510 회사에서 26개 진정 패턴 (5.1%) 식별 → 별도 architect.

### 목표
- G1: 26 회사 매핑 가능성 정량화
- G2: strict 매핑 logic (label substring → clause 매칭)
- G3: 단위 검증 + LG화학 regression 0
- G4: 510 회사 회귀 0
- G5: 미사용 룰 추가 활성

### 과정
| iter | 작업 | 결과 |
|---|---|---|
| 1 | 26 회사 매핑 가능성 정량화 | 102 sub: clear 14.7% / partial 60.8% / none 24.5% |
| 2-3 | 매핑 logic 구현 | 단위 검증 — 한미사이언스 3 / 차바이오텍 2 / 유한양행 1 catch / **LG화학 regression 0** ★ |
| 4-5 | 510 회사 회귀 | 회귀 0 + sub 75건 신규 catch (55 회사 = 10.8%) |
| 6 | 문서화 + promise | SUBAGENDA_AMENDMENT_MAPPING_VERIFIED ✅ |

### 결과
| 항목 | 성공/실패 |
|---|---|
| ✅ G1 매핑 가능성 정량화 | 75.5% 매핑 가능 |
| ✅ G2 strict cascade logic | label substring + clause 매칭 (keyword 매칭 의도적 제외) |
| ✅ G3 LG화학 regression 0 | keyword 매칭 제거 fix — semantic mismatch ("선임독립이사 선임" → "독립이사 명칭 변경") false positive 회피 |
| ✅ G4 510 회사 회귀 0 | KOSPI 23.1% catch / KOSDAQ 4.7% / 0% |
| ✅ G5 A1-3 / B1-8 / A1-2 첫 활성 | 미사용 룰 lesson 항목 catch |
| 부분 실패 | 카카오게임즈 자체 미catch (sub keyword reason 매칭 의도적 제외) |

### archive
- lesson: `wiki/lessons/subagenda-mapping-260510.md`
- decision: `wiki/decisions/260510_1015_decision_subagenda-mapping.md`
- audit: `wiki/architecture/audits/data/260510_subagenda_mapping/`
- commits: 1f96899, 27db7dd, b1f2f76, da711bc, 2098c83

---

## 3. Ralph 9 — 사외이사 충실성 강화 + 사내이사 독립성 표기 정정 (11:00~)

### 목적
메리츠금융지주 응답 검토 시 사용자 피드백:
1. 김용범 사내이사 "독립성 충족" 표기 부적절 (마치 독립이라 오인)
2. 사외이사 겸직 ≥ 2개 = concerns 신호 추가 필요

### 목표
- G1: careerDetails 데이터 가용성 audit
- G2: 사외이사 겸직 ≥ 2 = concerns / ≥ 3 = strong
- G3: 사내이사 독립성 표기 정정 ("독립성 평가 비대상")
- G4: 510 회사 회귀
- G5: 메리츠금융지주 단위 검증

### 과정
| iter | 작업 | 결과 |
|---|---|---|
| 1 | 510 회사 careerDetails audit | 98.4% 채워짐. 단순 키워드 false positive 발견 (본 회사 사외이사 표기) |
| 2 | logic v3 (본 회사명 매칭 + 후보 본인 보장) | 510 v3: concerns 64 회사 / strong 13 회사 (false positive 32 제거) |
| 3 | 코드 구현 | count_outside_director_positions / _is_outside_director_role / 사내이사 표기 정정 |
| 4 | 단위 검증 | 김용범 (사내) → "비대상" / 김정연 (삼성바이오 strong 3) / 박진규 (LG에너지 concerns 2) |
| 5 | 510 회사 회귀 | decision 변경 0 (facts 노출만) |
| 6 | 문서화 + promise | DIRECTOR_FAITHFULNESS_ENHANCED ✅ |

### 결과
| 항목 | 성공/실패 |
|---|---|
| ✅ G1 careerDetails 가용성 | 98.4% 채워짐 |
| ✅ G2 겸직 logic v3 | 본 회사 자동 포함 카운트 (false positive 32 회사 제거) |
| ✅ G3 사내이사 표기 정정 | "독립성 평가 비대상 (사내이사)" |
| ✅ G4 회귀 0 | facts 신규 노출 (concurrent_outside_positions / concurrent_summary) |
| ✅ G5 메리츠 검증 | 김용범 사내 / 4 사외이사 모두 single (정상) |

### 510 분포
- concerns 후보 (≥2): 108 (13.3%) → fix 후 112 (Ralph 10에서 +4)
- strong 후보 (≥3): 22 (2.7%)
- concerns 회사: 64/493 (13.0%)

### archive
- lesson: `wiki/lessons/director-faithfulness-260510.md`
- decision: `wiki/decisions/260510_1130_decision_director-faithfulness.md`
- audit: `wiki/architecture/audits/data/260510_director_faithfulness/`
- commits: 070780e, 2ffd52f, e6e51e2

---

## 4. Ralph 10 — careerDetails parser concat 분리 강화 (12:00~)

### 목적
Ralph 9에서 메리츠 진단 시 발견 — XML 본문에 careerDetails 풍부하지만 parser가 concat된 표 layout (한 셀에 4개 period + 4개 직책) 분리 못함. 메리츠 조홍희 사례: 2 entries 추출 (실제 4 entries — 2011~현재 법무법인 태평양 고문 누락).

### 목표
- G1: 510 회사 concat 패턴 정량화
- G2: parser 강화 (직책 boundary split)
- G3: 단위 검증 (메리츠 + LG화학 regression 0)
- G4: 510 회사 회귀
- G5: 미발견 케이스 catalog

### 과정
| iter | 작업 | 결과 |
|---|---|---|
| 1 | 510 회사 concat 정량화 | multi-period 68 / split 가능 15 (1.8%) |
| 2 | parser pipeline 진단 | _extract_career_from_html 1단계 None / fallback 2단계 contents 분리 부족 발견 |
| 3 | 직책 boundary split logic 구현 | _split_content_by_role_endings 헬퍼 + fallback 2단계 통합 |
| 4 | 단위 검증 | 메리츠 조홍희 2→4 / 김우진 1→5 / LG화학 regression 0 |
| 5 | 510 회사 회귀 | concerns 후보 108→112 (+4) / strong 후보 22→20 (정확도 ↑로 strong→concerns 이동) |
| 6 | 문서화 + promise | CAREER_PARSER_CONCAT_VERIFIED ✅ |

### 결과
| 항목 | 성공/실패 |
|---|---|
| ✅ G1 정량화 | multi-period entry 식별 |
| ✅ G2 parser 강화 | 직책 boundary split + N 정확 일치 안전 fallback |
| ✅ G3 단위 검증 | 메리츠 조홍희 2→4 (2011~현재 회수) / 김우진 1→5 |
| ✅ G4 510 회귀 0 | 기존 entries 보존 + 신규 추가 |
| ⚠ G5 미발견 | period 1개 + content multi (메리츠 김명애/김연미) — 별도 case |

### 핵심 교훈
**parser 강화는 깊은 위치 필요** — 표면 logic (_clean_career_details) 추가는 효과 없음. raw 추출 단계 (fallback 2단계) fix가 진짜 fix.

### archive
- lesson: `wiki/lessons/career-parser-concat-260510.md`
- decision: `wiki/decisions/260510_1230_decision_career-parser-concat.md`
- audit: `wiki/architecture/audits/data/260510_career_concat/`
- commits: f01aff8, bdba147, f2e818d, cc3ad0b, bd48ee0, 44f4781

---

## 5. proxy_advise 응답 품질 fix (17:09~)

### 목적
사용자 응답 품질 측정 (메리츠/LG화학/카카오게임즈 재호출) 후 발견 3가지 문제 fix.

### 발견 3가지
1. **묶음 안건 후보 detail 부족** — "후보 5명" reason만, 후보별 평가 X
2. **reason vs raw 신호 불일치** — "위험 신호 없음" reason + 강행규정 정합 raw 첨부 → LLM 모호
3. **raw 중복 + 매핑된 amendment 누락** — LG화학 미catch 4 안건에 같은 amendments[:5] 4번 첨부 (5980자 중복) + 미catch 안건의 진짜 amendment ([5][6][7]) 누락

### 사용자 결정
1번 fix / 2번 LLM 위임 (skip) / 3번 fix

### 과정
**fix 1**: facts.candidate_summary 추가
- 묶음 안건에서 후보별 mini-info (이름/role/appointment/독립성/결격/겸직) 노출

**fix 3**: 두 가지 동시 통합
- 회사 단위 첨부 flag (_amendments_attached_for_company): 첫 미매핑 안건에 모든 amendments / 다음은 anchor
- Ralph 8 매핑 활용 (_subagenda_attempted_mappings): 매핑 성공 sub는 자기 amendment 1개만 첨부

### 결과
| 항목 | 성공/실패 |
|---|---|
| ✅ fix 1 묶음 후보 detail | facts.candidate_summary list |
| ✅ fix 3 raw 중복 회피 | LG화학 5980→2951자 (-50%) + amendments [5][6][7] (미catch sub) 노출 |
| ✅ fix 3 매핑 활용 | sub→amendment 1:1 정확 첨부 |
| ⏭ fix 2 reason 강화 | LLM 위임 (skip 결정) |
| ✅ 50 회사 광범위 검증 | 25/50 묶음 안건 후보 160명 detail / anchor 61건 ~76KB 절약 / 매핑 10 회사 |
| ✅ KT&G B1-8 sub-mapped + B1/B2 raw 동시 작동 검증 | Ralph 6/8 호환 |
| ✅ 회귀 0 / 에러 0 | decision logic 영향 X |

### archive
- spot script: `scripts/spot_fix_verify_50.py`
- audit: `wiki/architecture/audits/data/260510_fix_verify/verify_50.json`
- commits: 7f1b88c, 4fec268, a102b04

---

## 6. 기타 fix (낮 시간)

### parser ※ note span lookahead 정합 (be2e722)
LG화학 raw 시퀀스에서 `제 3 호 의안 (주주제안) :` 직후 ※ 비고 패턴 충돌로 제3호 누락. lookahead에 괄호 옵션 추가.

### proxy_advise raw 첨부 length 통일 + 미catch 정관변경 raw 첨부 (69df0f3)
- B1/B2 raw 400 → 300자
- retirement_pay amendments_sample 200 → 300자
- 신규 1.6 분기: 미catch 정관변경 안건에 amendments raw 첨부

---

## 누적 효과 (510 회사 누적)

| 영역 | 누적 hits | 회귀 |
|---|---:|---|
| 정관변경 (Ralph 4 baseline) | 293 | — |
| Ralph 6 (변경 키워드 보강) | +21 = 314 | 0 |
| Ralph 7 (D 패턴 body) | +70 신규 (별도 cascade) | 0 |
| Ralph 8 (sub→amendment 매핑) | +75 신규 | 0 |
| **총합 (정관변경 영역)** | **442** | **0** |
| 사외이사 겸직 concerns (Ralph 9) | 108 후보 / 64 회사 | 0 |
| careerDetails entries 정확도 (Ralph 10) | concerns +4 / strong→concerns 이동 | 0 |

---

## 핵심 패턴 (4 ralph + fix 공통)

1. **정확성 우선** — false positive 0 정책 일관 (Ralph 6 회귀 lesson 정합)
2. **strict 안전 fallback** — N 정확 일치 / children 0 / generic skip / keyword 매칭 의도적 제외
3. **광범위 회귀 검증** — ralph마다 510 회사 spot으로 회귀 0 보장
4. **점진적 분해** — edge case (카카오게임즈 / 메리츠) 별도 ralph
5. **사용자 가설 검증** — 작은 sample에 속지 않고 광범위 spot으로 진단 (Ralph 7 / 9 / 10 모두 첫 가설과 다른 진짜 문제 발견)
6. **parser 깊은 진단** — 표면 logic 추가가 아닌 pipeline 추적 (Ralph 10)

---

## 실패 / 미해결

### 의도적 비목표
- 카카오게임즈 자체 catch (sub keyword reason 매칭) — Ralph 8 strict 정책상 의도적 제외
- 사외이사 keyword 매칭 (Ralph 8) — semantic mismatch 회피로 의도적 제외
- 모든 회사 amendments 통합 검사 (Ralph 6 회귀로 폐기)
- HTML viewer fallback 추가 (Ralph 10 XML only 정책)

### 미해결 (별도 ralph 후보)
- generic sub title (메리츠 김명애/김연미 period 1개 + content multi)
- _extract_career_from_html 1단계 None 반환 비율 측정
- 분쟁 회사 광범위 spot (60+) — Ralph 6 lesson 미실행
- 다른 안건 영역 fallback 확산 (퇴직금/자사주/보수) — Ralph 11 후보

### 응답 품질 fix 한계
- 문제 2 (reason vs raw 신호 불일치) — LLM 위임 (skip 결정, 향후 개선 가능)

---

## 다음 방향 추천 (우선순위)

1. **사용자 응답 품질 추가 측정** — 4 ralph + fix 효과 실 사용 후 추가 발견
2. **Ralph 11: 다른 안건 영역 fallback 확산** — 퇴직금 / 자사주 / 보수한도
3. **`_extract_career_from_html` 1단계 진단** — 메리츠처럼 None 반환 회사 비율
4. **분쟁 회사 광범위 spot (60+)** — Ralph 6 lesson 미실행
5. **LLM 위임 raw 정책 통일** — B1/B2 / 미catch / amendments_sample 단일 decision

---

## archive 전체 list (오늘 생성)

### Ralph plans
- `wiki/ralph/260510_0823_ralph_agenda-hierarchy-body-fallback.md`
- `wiki/ralph/260510_0950_ralph_subagenda-amendment-mapping.md`
- `wiki/ralph/260510_1100_ralph_director-faithfulness-enhancement.md`
- `wiki/ralph/260510_1200_ralph_career-parser-concat.md`

### Lessons
- `wiki/lessons/agenda-hierarchy-260510.md`
- `wiki/lessons/subagenda-mapping-260510.md`
- `wiki/lessons/director-faithfulness-260510.md`
- `wiki/lessons/career-parser-concat-260510.md`
- `wiki/lessons/260510_daily-summary.md` (이 문서)

### Decisions
- `wiki/decisions/260510_0900_decision_d-pattern-body-fallback.md`
- `wiki/decisions/260510_1015_decision_subagenda-mapping.md`
- `wiki/decisions/260510_1130_decision_director-faithfulness.md`
- `wiki/decisions/260510_1230_decision_career-parser-concat.md`

### Audit data
- `wiki/architecture/audits/data/260510_agenda_hierarchy/` (iter 1/2/4/iter5_kakaogames)
- `wiki/architecture/audits/data/260510_subagenda_mapping/` (iter 1/2/4)
- `wiki/architecture/audits/data/260510_director_faithfulness/` (iter 1/2 v3)
- `wiki/architecture/audits/data/260510_career_concat/` (iter 1/4)
- `wiki/architecture/audits/data/260510_fix_verify/verify_50.json`

### Scripts (오늘 신규)
- `scripts/spot_agenda_hierarchy_diagnose.py`
- `scripts/spot_d_pattern_body_fallback.py`
- `scripts/spot_law_layer_with_body.py`
- `scripts/spot_kakaogames_pattern.py`
- `scripts/spot_subagenda_mapping_audit.py`
- `scripts/spot_subagenda_mapping_unit.py`
- `scripts/spot_law_layer_full.py`
- `scripts/spot_concurrent_director_audit.py`
- `scripts/spot_concurrent_director_v3.py`
- `scripts/spot_career_concat_audit.py`
- `scripts/spot_fix_verify_50.py`

### 커밋 (43건)
parser fix → ralph 7 (8 commits) → ralph 8 (6 commits) → ralph 9 (5 commits) → ralph 10 (6 commits) → fix + verify (3 commits) + 새벽 작업 (15 commits) = **43 commits**
