---
type: lesson
title: 카카오게임즈 패턴 — sub→amendment 1:1 매핑 (Ralph 8)
date: 2026-05-10
related:
  - wiki/ralph/260510_0950_ralph_subagenda-amendment-mapping.md
  - wiki/lessons/agenda-hierarchy-260510.md
related_decisions: [260510_0900_decision_d-pattern-body-fallback, 260510_1015_decision_subagenda-mapping]
related_ralph: [260510_0950_ralph_subagenda-amendment-mapping, 260510_0823_ralph_agenda-hierarchy-body-fallback]
related_audits: [architecture/audits/data/260510_subagenda_mapping/iter1_findings, architecture/audits/data/260510_subagenda_mapping/iter4_findings]
---

# Ralph 8 — 카카오게임즈 패턴 회고

## 배경

Ralph 7 D 패턴 fallback (top + children 0 + amendments)으로 510 중 70건 신규 catch. 단 카카오게임즈 같은 케이스 (sub-agenda 있고 sub title 일반 표현) 미해결. 510 회사 식별 → 진정 카카오게임즈 패턴 26개 (5.1%) 발견 → Ralph 8 신규.

## 핵심 design

### 진입 조건 (D 패턴과 다름)

```
parent에 "정관" + "변경"/"개정"
+ 자기 children == 0
+ 자기 title generic 아님 (도메인 키워드 1+)
+ amendments 비어있지 않음
```

### sub→amendment 1:1 매핑 — strict cascade

```python
Priority 1: amendment label == sub title (substring) — 강원랜드 케이스
Priority 2: clause 매칭 (amendment label/before/after에서 조항 추출)
※ keyword 매칭 의도적 제외 — semantic mismatch false positive 회피
```

## Ralph 6 회귀 회피 — 다시 확인

### 시도 1: keyword 매칭 추가
- LG화학 "선임독립이사 선임" sub → "독립이사" 키워드로 amendment 제20조 매핑 → A1-5 (사외→독립이사 명칭 변경) hit
- **false positive!** "선임독립이사 선임"은 주주제안 안건, "명칭 변경"이 아님
- semantic mismatch — keyword overlap만으로는 의미 보장 X

### 시도 2: keyword 매칭 제거 (현재 채택)
- LG화학 sub_hits 0 ✅ regression 0
- catch 줄어듦 — 카카오게임즈 자체는 미catch (sub title이 generic 아니지만 reason keyword만 매칭 가능)
- trade-off: 정확성 우선 (Ralph 6 lesson 정합)

## generic title 정책 — 옵션 B (skip)

- generic sub (도메인 키워드 없음 — "그 외 변경의 건" / "기타 정비")는 fallback 진입 X
- 운용사 정책 fallback 또는 _decide_articles_amendment default FOR
- cross-match 위험 0

## 단위 검증 (7 회사)

| 회사 | sub-hit | 결과 |
|---|---:|---|
| 카카오게임즈 | 0 | 미catch (sub keyword "기준일" reason 매칭 — 의도적 제거) |
| 한미사이언스 | 3 | A1-5 / A1-5 / A1-4 catch (sub clause 명시) |
| 강원랜드 | 0 | 미catch (label 매칭 시도했으나 clause 0 / keyword 0) |
| 한라캐스트 | 0 | 미catch |
| 차바이오텍 | 2 | A1-6 / A1-1 catch |
| 유한양행 | 1 | A1-1 catch |
| LG화학 | 0 | ✅ **regression 0** |

## 510 회사 회귀

| universe | n | 기존 | 신규 | 회귀 | sub | sub 회사 |
|---|---:|---:|---:|---:|---:|---:|
| KOSPI200 | 199 | 287 | 344 | 0 | 65 | 46 |
| KOSDAQ150 | 150 | 40 | 48 | 0 | 8 | 7 |
| KOSDAQ151-300 | 150 | 30 | 30 | 0 | 0 | 0 |
| DISPUTE | 10 | 18 | 20 | 0 | 2 | 2 |
| **TOTAL** | **509** | **375** | **442** | **0** | **75** | **55** |

✅ 회귀 0 ✅ sub 75건 신규 (10.8% 회사) ✅ A1-3/B1-8/A1-2 미사용 룰 활성

sub fallback rule 분포: A1-3 18 / A1-5 15 / A1-1 13 / A1-7 12 / B2-1 4 / B2-7 4 / A1-4 3 / A1-6 3 / B1-8 1 / B1-8b 1 / A1-2 1

## 핵심 교훈

### 1. semantic mismatch는 keyword overlap으로 보장 X
LG화학 "선임독립이사 선임" → "독립이사 명칭 변경" amendment 매핑 false positive. 단어 overlap이 의미 보장하지 않음. semantic 매핑은 LLM 영역.

### 2. strict cascade — label/clause 명시 안건만 catch
한미사이언스 / 차바이오텍 / 유한양행 같은 회사는 sub title에 조항 번호 명시 ("제22조") — 이런 명시 회사만 정확 매핑 가능. 그 외 generic / fuzzy는 미catch.

### 3. Ralph 6 lesson 재확인
catch 욕심으로 매핑 strict 약화하면 false positive 발생. 정확성 우선 — 미catch는 운용사 정책 또는 LLM 위임.

## 다음 ralph 후보

1. 카카오게임즈 같은 generic / fuzzy sub title 처리 — LLM 위임 (raw 첨부 패턴 확장)
2. amendments label이 빈 string인 케이스 — before/after raw에서 조항 추출 보강 (한미사이언스 효과 ↑)
3. body_pattern 다른 룰 추가 (A1-2 / B1-x) — D 패턴 catch ↑
