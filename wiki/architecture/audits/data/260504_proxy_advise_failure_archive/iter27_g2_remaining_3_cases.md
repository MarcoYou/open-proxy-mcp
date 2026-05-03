---
type: failure_archive
iteration: 27 (post-wire batch v7b)
gate: G2
fail_type: parser_limitation + minority_outlier
date: 2026-05-04
g2_overall: 97.35% (551/566) / 4+ majority 99.36% (464/467)
---

# Iter 27 — 잔여 3건 4+ majority 실패 case (parser 한계)

batch v7b (152 회사 / 566 entries) 결과:
- 전체: 97.35%
- **4+ vote majority: 99.36% ✅** (target ≥99% 충족 유지)
- unique 전체 9 (대부분 1-3 vote 약한 majority)
- **4+ majority mismatch 3건만** — 아래 분석.

## Case 1: 서진시스템 audit_committee_election (5/5 AGAINST)

**우리 결정**: FOR — "사외이사 결격사유 없음 (사외이사 후보 — audit-strict)"

**운용사 reason (5/5)**:
- M레거시: "감사로 9년 연임 + 재선임 시 12년 — 장기 연임 → 독립성 훼손 → AGAINST"
- 삼성액티브 / 삼성자산 / S레거시 / 한국투자: 동일 (장기연임)

**OPM 한계**:
- iter23 `five_year_signal`: careerDetails에 "재선임/재임/연임/중임" 키워드 발견 시 long_tenure_concerns
- 정전환 careerDetails에 위 키워드 없음 (보통 "2016년 - 현재 상근감사" 같은 표기)
- 우리 parser가 9년 재직 자체 detect 못 함

**제안 fix (별도)**:
- careerDetails 정규식: `"\d{4}\s*년.*?상근감사"` 같은 회사 + 연도 패턴 → 5년+ 자동 detect
- 또는 director_evaluation에서 회사 history 누적 (DART 사외이사 변동 history)

## Case 2: 올릭스 director_election (3/4 AGAINST, outlier match)

**우리 결정**: FOR / mainstream 3 AGAINST + 1 outlier
**OPM 한계**: 4 표본 중 3 mainstream — 상대 약한 majority. 우리 outlier 운용사와 일치.
**개선 우선도**: 낮음 (소수표본).

## Case 3: 이오플로우 articles_amendment (7/10 AGAINST, outlier match)

**우리 결정**: FOR / mainstream 7 AGAINST + 3 outlier
**OPM 한계**: 정관 변경의 위험 신호를 OPM이 detect 못함 (본문 분석 필요)
**제안 fix**: 정관 본문 스캔으로 새 위험 키워드 발견

## 통계 요약 (batch v1-v7b 추이)

| version | 전체 | 4+ majority | 핵심 fix |
|---|---|---|---|
| v1 | - | 32.4% (10 spot) | baseline |
| v3 | 95.98% | - | iter12-18 누적 |
| v4 | 96.50% | - | iter21 |
| v5 | 97.20% | **99.36%** | iter22 birth_date age |
| v6 | 97.20% | 99.15% | iter23 (REVIEW fallback — revert) |
| v7b | 97.35% | **99.36%** | iter25 fallback FOR + iter26 퇴직금 + iter27 wire |

## 결론

22+ iter ralph + iter23-27 추가 작업:
- **5/5 gate 모두 충족** ✅
- 잔여 3건 — parser 한계 (careerDetails 9년 재직 detect / 정관 본문 위험 키워드)
- 별도 ralph 작업 가능 (다음 ralph 후보)
