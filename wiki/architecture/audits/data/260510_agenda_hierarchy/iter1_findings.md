---
type: audit
title: 안건 호수 hierarchy 추출 진단 — iter 1
date: 2026-05-10
related:
  - wiki/ralph/260510_0823_ralph_agenda-hierarchy-virtual-sub.md
  - wiki/lessons/law-layer-body-260510.md
---

# iter 1 — 호수 hierarchy 진단 결과

## 방법

10 회사 sample (4 미매치 + LG화학 + KOSPI 5) 대상:
- DART 정기주총 소집공고 raw text 받기
- parse_agenda_xml 출력 vs raw 호수 직접 grep 비교 (공백 정규화 후)
- 진짜 누락 / 오인식 식별

## 결과 표 (공백 정규화 후)

| 회사 | parser_n_total | 진짜 누락 | 비고 |
|---|---:|---|---|
| 에코프로비엠 | 11 | 0 | ✅ 완벽 |
| 카카오게임즈 | 12 | 0 | ✅ 완벽 |
| 에스엠 | 10 | (0) | "제8호" zone에 잡혔지만 사업보고서의 이사회 의결 history 표 영역 — parser가 zone 정확히 분리 |
| 메리츠금융지주 | 7 | 0 | ✅ 완벽 |
| LG화학 | 16 | **1 (제3호)** | children 제3-1/제3-2/제3-3호는 추출, 부모 "제3호 의안 (주주제안): 주주 제안의 건" 라인 누락 |
| 삼성전자 | 10 | 0 | ✅ 완벽 |
| SK하이닉스 | 18 | 0 | ✅ 완벽 |
| 현대차 | 19 | 0 | ✅ 완벽 |
| NAVER | 7 | 0 | ✅ 완벽 |
| 셀트리온 | 21 | 0 | ✅ 완벽 |

## 핵심 발견

### 발견 1 — parser 호수 추출 정확도 매우 높음

10/10 회사 zone 매칭 거의 완벽. 진짜 누락 1건만 (LG화학 제3호) — 그것도 children은 정확히 추출됨.

→ **사용자 가설 ("parser가 sub-agenda 호수를 누락한다")는 false**. parser는 이미 정확히 추출 중.

### 발견 2 — 4 미매치 회사는 D 패턴 (raw에 sub 자체 부재)

4 미매치 회사 (에코프로비엠 / 카카오게임즈 / 에스엠 / 메리츠금융지주):
- 호수는 정확히 추출됨 (zone_only 0)
- 단 정관변경 안건의 title이 **일반 표현** ("정관 일부 변경의 건")
- raw에 sub-agenda 호수 자체가 없음
- 변경 내용은 amendments[].label/clause/before/after에만 존재

→ 호수 hierarchy 강화로 catch 불가. **amendments fallback만이 유일 해결책**.

### 발견 3 — LG화학 제3호 누락 (parser 미세 버그) — FIXED

**원인 정확 식별** (파서 line 178~189):

`※ note span` lookahead 패턴이 AGENDA_RE의 괄호 옵션과 정합되지 않음. AGENDA_RE는 `제N호 의안 (주주제안) :` 형태를 매치하지만, ※ note span lookahead는 괄호 옵션 누락:

```python
# Before
r'※.+?(?=\s*[□◎●]\s*제|\s*(?<![가-힣])제\s*\d+\s*(?:-\s*\d+)*\s*호\s*(?:의안|안건)?\s*[:：]|$)'
# After (괄호 옵션 추가)
r'※.+?(?=\s*[□◎●]\s*제|\s*(?<![가-힣])제\s*\d+\s*(?:-\s*\d+)*\s*호\s*(?:의안|안건)?\s*(?:\([^)]*\))?\s*[:：]|$)'
```

LG화학 raw 시퀀스에서 `※ 주주제안자는 ...   제 3 호 의안 (주주제안) : 주주 제안의 건  ※ 제 3호 의안은 ...` — ※가 다음 안건 마커 (`제 3 호 의안 (주주제안) :`)를 인식 못 해서 ※ span이 본 안건 위치까지 삼킴 → `_note_spans` 추가 → 본 안건 skip.

**Fix 검증**:
| 회사 | before parser_total | after parser_total |
|---|---|---|
| LG화학 | 16 | **17** (제3호 추가) |
| 9 회사 | 동일 | 동일 (regression 0) |

LG화학 parser_top 8→6도 정상화 (제3-1/-2/-3이 부모 부재로 잘못 top에 올라갔던 게 정상 children으로 복귀).

**영향**: _law_layer hit에는 영향 X (제3호 title "주주 제안의 건"은 어떤 룰과도 매칭 X). 단 데이터 정확성 ↑.

raw 1058~1089 위치:
```
제 2-8 호 (주주제안) : 선임독립이사 선임
※ 주주제안자는 팰리서캐피털마스터펀드리미티드 외 1인임.
-

제 3 호 의안 (주주제안) : 주주 제안의 건
※ 제 3호 의안은 제2-7호 의안이 가결될 경우에만 상정되고, 그 외의 경우에는 자동 폐기됨.
※ 주주제안자는 팰리서캐피털마스터펀드리미티드 외 1인임.
ㆍ 제 3-1 호 : 기업가치 제고계획에 NAV 할인율을 ...
ㆍ 제 3-2 호 : 기...
```

표준 포맷 (`제 3 호 의안 (주주제안) : 주주 제안의 건`)인데 parser 누락. 추정 원인:
- ※ 비고 처리 (parser line 178~189) — 직후 ※ 패턴으로 안건 자체가 ※ note span에 잘못 포함될 가능성
- 또는 conditionals 추출 (CONDITIONAL_RE)이 안건과 충돌

**영향**: _law_layer hit에는 영향 X (제3호 title "주주 제안의 건"은 어떤 룰과도 매칭 X). 단 데이터 정확성 문제.

## 결론

| 가설 | 검증 | 결과 |
|---|---|---|
| parser 호수 누락 多 | ✅ 검증 | **false** (10/10 회사 거의 완벽, 1건 미세 버그만) |
| parser 보강으로 4 회사 catch 가능 | 도출 | **불가** (D 패턴 — sub 자체 부재) |
| amendments fallback (D 패턴 한정) 필요 | 도출 | **유일 해결책** |

## Ralph 7 plan 재조정

- iter 2 (parser 보강) → ✅ **완료** (LG화학 제3호 fix, regression 0)
- iter 3 (510 회사 회귀) → 단 1줄 lookahead 옵션 추가 + 9 회사 영향 0 검증 → 별도 spot 선택
- iter 4 (D 패턴 식별) → **이미 식별됨** (4 미매치 회사 = D 패턴)
- iter 5 (D 패턴 amendments fallback) → **다음 단계로 직행**
