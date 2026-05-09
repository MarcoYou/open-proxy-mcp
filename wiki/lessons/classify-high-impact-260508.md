---
type: lesson
title: high-impact 분류기 audit 결과 — fix 불필요 확정 (260508)
date: 2026-05-08
related:
  - wiki/ralph/260508_0030_ralph_classify-high-impact.md
  - wiki/lessons/agenda-classification-260507.md
---

# high-impact 분류기 audit 결과

## 결과 요약

300 회사 sample (KOSPI 200 + KOSDAQ 100) 통합 audit:

| 분류기 | 정확도 | Fix 필요? |
|---|---|---|
| `_classify_value_up_item` (밸류업 카테고리) | **100.00%** (127/127) | ❌ 견고 |
| `_is_company_side` / `_is_retail_activism_side` (filer 3-way) | **99.22%** (253/255) | ❌ 견고 (mismatch 2건은 별도 resolver 이슈) |

## value_up 분류기 — 견고

19 unique report_name 패턴 catalog:

| 카테고리 | 개수 | 패턴 |
|---|---|---|
| plan | 85 | 자율공시 / 2025년/2026년 변형 / 한국투자금융지주 / 중기주주환원정책 / 정정공시 prefix |
| progress | 24 | 이행현황 / 2025년이행현황 / 2025년2분기이행현황 |
| meta_amendment | 18 | 고배당기업표시를위한재공시 / 정정공시 prefix |

**borderline 1건**: `이행현황 및 고배당기업표시` 혼합 — 현재 meta_amendment (정책 판단 영역, 명백한 오류 X)

## proxy_contest filer 3-way 분류기 — 견고

255 위임장 filings 분포:
- company: 218 (대부분 자기 회사 직접 위임장)
- retail_activism: 31 (컨두잇 / 헤이홀더 / 비사이드코리아)
- shareholder: 6 (영풍 4건 [고려아연 분쟁] / A행동주의 / Palliser Capital)

**Mismatch 2건** — 분류기 이슈 아님:
- 셀트리온제약 query → service가 canonical을 "셀트리온"으로 해석 (회사 모호 매칭)
- 셀트리온 위임장이 셀트리온제약 결과로 반환됨
- filer="셀트리온", corp="셀트리온"(service)이라 분류 자체는 정확
- → **회사 resolver 단계 이슈** (별도 영역)

## Meta-lesson — Audit script 측 버그 주의

iter 2에서 false positive 10건 발견 (audit script bug):
- universe csv 약칭 "현대차" vs DART 정식명 "현대자동차"
- audit이 universe name 사용해서 분류기 검증 → false negative
- service는 DART corp_name 사용 → 정확

→ **audit script가 service의 input 출처와 정합되어야** 의미 있는 측정.

## 결론

Ralph 1 (`_classify_agenda`)와 달리 두 분류기 모두 **견고**. 단일 fix 패턴 발견 안 됨.

다음 우선순위:
- 회사 resolver (`resolve_company_query`) 정확도 — 셀트리온제약 → 셀트리온 케이스
- medium-impact classifier (`_classify_filing` / `_is_active_purpose` 등) — 별도 ralph

## 영향

- 분류기 코드 변경 없음 (견고 확인)
- `scripts/spot_classify_value_up.py` 신규
- `scripts/spot_classify_filer.py` 신규
- `wiki/architecture/audits/data/260508_classify_high_impact/` 검증 데이터
