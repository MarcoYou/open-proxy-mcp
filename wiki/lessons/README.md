---
type: readme
title: lessons/ — 작업 회고
updated: 2026-05-05
---

# lessons/

작업하면서 배운 것 + 결정의 trade-off. 시점에 묶지 않은 정체성 문서 (`{topic}.md`).

각 페이지 schema:
- **Context**: 왜 이걸 다뤘나
- **Did**: 무엇을 했나
- **Improved**: 무엇이 나아졌나
- **Trade-off**: 무엇을 잃었나
- **Takeaway**: 다음에 반복할 원칙

## 목록 (2026-05-05 기준)

1. [[acode-semantic-markers]] — DART 본문 ACODE 발견 → text regex 한계 돌파, 99% 안정성
2. [[scope-simplification]] — tool 안 specialized scope 폐지 → 사용자 라우팅 단순화
3. [[time-axis-tool-split]] — shareholder_meeting을 사전(notice)/사후(results)로 분리 → fragility 격리
4. [[hard-rate-limit]] — DART 분당 1000회 hard rule을 코드로 강제 → 차단 사고 재발 방지
5. [[ralph-threshold-realism]] — 표준 서식 99% / 자유 텍스트 90% — 데이터 자체 한계가 threshold 결정
6. [[decision-vs-raw-separation]] — decision logic은 tool 안에서, raw expose는 외부 tool로
7. [[enrichment-as-infrastructure]] — facts/risk/citation/근거공고 = 검증 가능한 응답의 핵심
8. [[distribution-calibrated-thresholds]] — classification cutoff은 prior 직관이 아니라 audit 표본 분포 본 후 정함
