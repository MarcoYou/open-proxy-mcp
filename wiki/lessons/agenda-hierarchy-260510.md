---
type: lesson
title: 안건 호수 hierarchy + D 패턴 amendments body fallback (Ralph 7)
date: 2026-05-10
related:
  - wiki/ralph/260510_0823_ralph_agenda-hierarchy-body-fallback.md
  - wiki/lessons/law-layer-body-260510.md
  - wiki/architecture/audits/data/260510_agenda_hierarchy/
related_decisions: [260508_0700_decision_law-layer-precision, 260510_0900_decision_d-pattern-body-fallback]
related_ralph: [260510_0823_ralph_agenda-hierarchy-body-fallback]
related_audits: [260510_agenda_hierarchy/iter1_findings, 260510_agenda_hierarchy/iter2_findings]
---

# Ralph 7 — 호수 hierarchy 추출 + D 패턴 body fallback 회고

## 배경

Ralph 6 (260510_0747)에서 _law_layer body 매칭 시도 → 회귀 (LG화학 sub 명확 회사가 모든 amendments 통합 검사로 false positive 다수 발생). matching layer가 아닌 데이터 구조에서 해결 방향 전환.

Ralph 7은 두 가지 가설 검증:
1. parser가 호수 hierarchy를 정확히 추출하나? (사용자 가설)
2. 호수 hierarchy 강화로 4 미매치 회사 catch 가능한가?

## 핵심 발견

### 1. parser 호수 추출은 거의 완벽

10/10 회사 zone 매칭 검증. 호수 표기 (제N호 / 제N-M호 / 제N-M-K호) 정확 추출. LG화학 미세 버그 1건 (제3호 누락 — ※ note span lookahead 정합 실패) fix (commit be2e722).

**사용자 가설 결론**: parser는 정확. 호수 hierarchy 강화로는 4 미매치 catch 불가.

### 2. 4 미매치 회사 = D 패턴 (raw에 sub-agenda 자체 부재)

4 회사 (에코프로비엠 / 카카오게임즈 / 에스엠 / 메리츠금융지주) raw 분석:
- 호수 정확 추출됨 (parser 검증)
- top-level "정관 일부 변경의 건"만 노출 (children 0)
- 실제 변경 내용은 amendments[].label/clause/before/after에만 존재
- title 매칭으로는 _law_layer catch 불가

**예외 — 카카오게임즈는 D 패턴 X**:
- agendas: 제2호 정관 일부 변경 (children 2: 제2-1호 주주총회 기준일 변경, 제2-2호 개정 상법 반영)
- sub-agenda 있지만 sub title이 일반 표현 → 별도 architect 필요

### 3. amendments body fallback 안전 설계

Ralph 6 회귀 회피 안전장치:
- 진입 조건 strict (parent=="" + _is_charter_top + children==0 + amendments 비어있지 않음)
- LG화학 같은 sub 명확 회사는 children > 0이라 자동 제외
- amendment 단위 검사 (모든 amendments 통합 X) → 한 안건 키워드가 다른 sub에 잘못 매칭 회피

## 룰 catalog 변경

### body_pattern 별도 필드 추가 (스키마 확장)

```json
{
  "id": "A1-1",
  "agenda_pattern": {...title용 strict...},
  "body_pattern": {...amendments raw용 lenient — 옵션...}
}
```

`_law_layer_body`에서 body_pattern 우선, 없으면 agenda_pattern fallback. agenda_pattern은 그대로 — title 매칭 회귀 위험 0.

### A1-1 body_pattern 추가
secondary 확장: ["배제", "적용 안", "적용하지 아니", "적용제외", "적용하지않"]
raw "집중투표제는 적용하지 아니한다" 표현 catch (에코프로비엠).

### A1-7 body_pattern 추가
any_of 확장: [..전자주총.., "제542조의14", "제542조의15"] (법령 직접 인용)
secondary 확장: ["도입", "신설", "반영", "개정", "신규"]
raw "상법 제542조의14, 제542조의15" 인용 catch (메리츠금융지주).

## 검증 결과

### 4 미매치 회사 catch (5 회사 단위 검증)

| 회사 | 패턴 | 결과 | rule |
|---|---|---|---|
| 에코프로비엠 | D ✓ | ✅ catch | A1-1 (집중투표 배제 조항 삭제) |
| 카카오게임즈 | **D X** (sub 있음) | ❌ 진입 X | (별도 architect 필요) |
| 에스엠 | D ✓ | ✅ catch | A1-5 (사외→독립이사 명칭 변경) |
| 메리츠금융지주 | D ✓ | ✅ catch | A1-7 (전자주총 도입) |
| LG화학 | sub 명확 | ✅ regression 0 | (children > 0이라 D 진입 X) |

### 510 회사 spot 회귀
TBD (iter 4 진행 중)

## 핵심 교훈

### 1. parser 추출 정확도 가설 검증의 중요성
사용자 가설 "parser가 누락한다"를 검증 전에 가정으로 진행했다면 잘못된 fix 진행 위험. 10 회사 raw vs parser dump 진단 30분으로 가설 false 확인 → architect 방향 정확화.

### 2. Ralph 6 회귀 회피 — 진입 조건 strict
body 매칭 자체가 위험한 게 아니라 **모든 회사 일괄 적용**이 위험. strict 진입 조건 (D 패턴 한정)으로 LG화학 같은 sub 명확 회사 자동 제외.

### 3. body_pattern 별도 필드 — 회귀 위험 0
title 패턴과 body 패턴은 다른 표현 분포 (title은 안건 제목 표현, body는 법령 정합 표현). 별도 필드로 분리하면 title 매칭에 영향 0 + body lenient 보강 가능.

### 4. D 패턴 X 케이스 (카카오게임즈)
sub-agenda 있지만 sub title 일반 표현 — D 패턴 fallback 진입 X. 해결안:
- (option A) sub agenda children 0 + parent가 정관변경 + 자기 title이 일반 + amendments 매핑
  → amendments는 어느 sub에 속하는지 매핑 어려움
- (option B) agenda_pattern (title) 보강 ("기준일 변경", "개정 상법 반영" 등)
  → false positive 위험
- (option C) limitation 인정, 별도 ralph 후보

본 ralph는 option C. sub title 일반 표현은 별도 architect ralph 후보.

## 다음 ralph 후보

1. 카카오게임즈 같은 sub 일반 표현 회사 처리 architect
2. A2 시행 후 자연 검증 (2026-07-23 / 09-10)
3. body_pattern 추가 활용 — 다른 룰 (A1-2 / A1-8 / B1 등)

## archive

- `wiki/architecture/audits/data/260510_agenda_hierarchy/iter1_findings.md`
- `wiki/architecture/audits/data/260510_agenda_hierarchy/iter2_findings.md`
- `wiki/architecture/audits/data/260510_agenda_hierarchy/raw_vs_parser_10.json`
- `wiki/architecture/audits/data/260510_agenda_hierarchy/iter2_body_fallback_verify.json`
- `wiki/architecture/audits/data/260510_agenda_hierarchy/iter4_spot_*.json` (510 회사)
