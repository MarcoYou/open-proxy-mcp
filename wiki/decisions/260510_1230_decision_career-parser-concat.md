---
type: decision
title: careerDetails parser concat 분리 — 직책 boundary split (XML only)
date: 2026-05-10 12:30
status: active
related_ralph: [260510_1200_ralph_career-parser-concat]
related_lessons: [career-parser-concat-260510]
related_audits: [architecture/audits/data/260510_career_concat/iter4_findings]
---

# Decision — careerDetails parser concat 분리 강화

## 결정

`parse_personnel_xml`의 fallback 2단계 (md table parsing)에 직책 끝 boundary split 추가. periods N개 + contents M < N mismatch 시 boundary split 시도, N 정확 일치할 때만 채택.

## 진입 조건

```python
# fallback 2단계 contents 분리 후
if len(periods) >= 2 and len(contents) < len(periods):
    boundary_split = _split_content_by_role_endings(contents_raw)
    if len(boundary_split) == len(periods):  # 안전 fallback
        contents = boundary_split
```

## 직책 끝 키워드 catalog

```
국장 / 청장 / 위원장 / 위원 / 장관 / 차관 /
사외이사 / 독립이사 / 사내이사 / 이사 / 감사위원 / 감사 /
고문 / 자문 / 회원 /
사장 / 부사장 / 회장 / 부회장 / 대표이사 / 대표 /
본부장 / 센터장 / 소장 / 원장 / 팀장 / 실장 / 부장 /
교수 / 조교수 / 부교수 / 강사 / 연구원 / 박사 /
CEO / CFO / CTO
```

## XML only 정책

HTML viewer fallback 추가 X. 기존 XML raw 본문에서 추출 정확도만 강화. 추가 DART API 호출 0.

## 안전 fallback

- boundary split N != periods N → 원본 contents 유지
- false positive 0 (안전 우선)

## 510 회사 회귀

- 회귀 0 (기존 entries 보존)
- 신규 catch: concerns 후보 +4, 회사 +2
- 일부 strong → concerns 이동 (entries 정확도 ↑로 카운트 정확)

## 비목표

- _extract_career_from_html 1단계 강화 X (별도 ralph)
- period 1개 + content multi 케이스 X (별도 case)
- HTML viewer fallback X (XML only)

## 영향 범위

- `open_proxy_mcp/tools/parser.py`:
  - `_CAREER_PERIOD_RE` / `_CAREER_ROLE_END_RE` 정규식
  - `_split_content_by_role_endings` 헬퍼
  - `_split_concatenated_career_entry` 헬퍼 (clean 단계용)
  - fallback 2단계 통합 (line 2127~)
- 기존 logic 영향 0 (안전 fallback 정책)
