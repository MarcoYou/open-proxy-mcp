---
type: architecture
title: parsing-success-rate-audit-goal
updated: 2026-05-17
related_notes:
  - parsing_success_rate_audit_spec
  - parsing_success_rate_audit_checklist
related_audits:
  - 260517_parsing_success_rate_audit
---

# Goal Invoke Message

/goal Use wiki/architecture/parsing_success_rate_audit_spec.md as the source of truth for executing the key data tools parsing success rate audit. Run the 450 company baseline for company sample tools. Keep shareholder_meeting_notice as a separate filing sample audit with all 2026 annual meeting notices and all extraordinary meeting notices filed since 2026 03 31. Classify results into success soft fail and hard fail. Identify parser family failure clusters. Apply only regression safe fixes. Rerun targeted checks and non overlap recheck when fixes are made. Produce a Korean audit result document with success rates usable rates hard fail rates latency findings regression findings and next priorities.

## 목적

이 문서는 parsing success rate audit를 실제로 `/goal`로 실행할 때 바로 붙여넣을 수 있는 실행용 문서다.

## Source Of Truth

- [parsing_success_rate_audit_spec.md](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/parsing_success_rate_audit_spec.md:1)
- [parsing_success_rate_audit_checklist.md](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/parsing_success_rate_audit_checklist.md:1)

## 실행 범위

- 회사 표본 기반 key data tools baseline
- `KOSPI 300 + KOSDAQ 150 = 450개 회사`
- 비중복 재검증 표본 `KOSPI 50 + KOSDAQ 50 = 100개 회사`
- `shareholder_meeting_notice`는 별도 공시 표본 감사
  - `2026년 정기 주총 notice 전수`
  - `2026-03-31 이후 현재까지의 임시주총 notice 전수`

## 실행 원칙

- `success`, `soft fail`, `hard fail` 정의는 spec을 따른다.
- DART rate limit을 넘지 않도록 tool 하나씩 배치 실행한다.
- parser family failure cluster를 먼저 분류하고 그 다음 수정한다.
- semantic drift가 있거나 latency를 크게 악화시키는 수정은 merge 후보에서 제외한다.
- 수정 후에는 subset rerun과 non overlap recheck를 수행한다.

## 기대 산출물

- 한국어 audit 결과 문서 1개
- baseline raw output과 summary artifact
- 필요 시 tool별 또는 parser family별 추가 recheck artifact
- 다음 우선순위와 수정 후보 목록
