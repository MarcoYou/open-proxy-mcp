---
type: lesson
title: scope simplification — tool 안 specialized scope 폐지
context: tools_v2 정리 (2026-05-04~05)
date_learned: 2026-05-05
related_decisions: [260506_0030_decision_notice-scope-cleanup-prov-financials]
---

# scope simplification

## Context

OPM v2 tool이 시간이 지나면서 scope이 점점 늘어남. tool 안에서 "더 detail하게 보고 싶다" 요구를 specialized scope으로 흡수.

예시 (단순화 전):
- `proxy_advise_before_meeting`: 10 scope (decisions / agenda / candidates / financial / governance / ownership / policy_basis / proxy_battle / engagement / evidence / all)
- `dividend`: 6 scope (summary / detail / history / policy_signals / cash_shareholder_return / total_shareholder_return)
- `treasury_share`: 6 scope (summary / events / acquisition / disposal / cancelation / annual)
- `corporate_restructuring`: 4 scope (summary / merger / split / share_exchange)
- `dilutive_issuance`: 5 scope (summary / rights_offering / convertible_bond / warrant_bond / capital_reduction)

scope이 많으면:
- Claude.ai docstring 길어짐 → 동적 tool loading 부담 ↑
- LLM이 어떤 scope 골라야 할지 헷갈림
- specialized scope이 다른 tool과 raw 중복 (예: proxy_advise scope=financial = financial_metrics tool 직접 호출과 동일)

## Did

**원칙**: tool 안 scope이 다른 tool과 raw 중복이면 폐지.

| Tool | Before | After | 폐지 |
|---|---|---|---|
| proxy_advise_before_meeting | 10 | **1** (decisions) | 9 (agenda/candidates/financial/governance/ownership → 각 tool 직접 호출, policy_basis/proxy_battle/engagement/evidence → archive) |
| dividend | 6 | **3** (summary/detail/history) | 3 (CSR/TSR → 외부 의존 high, policy_signals → history에 통합) |
| treasury_share | 6 | **2** (summary/annual) | 4 (events/acquisition/disposal/cancelation → summary에 type_breakdown 통합) |
| corporate_restructuring | 4 | **1** | 3 type-specific scope (summary가 timeline + 4 type detail 모두 포함) |
| dilutive_issuance | 5 | **1** | 4 type-specific (동일 패턴) |
| ownership_structure | 7 | **5** | treasury (treasury_share 사용 권장), timeline → blocks 통합 |

## Improved

- **총 scope 32 → 17** (47% 감소)
- proxy_advise는 1회 호출로 안건별 결정 + 후보 평가 + 재무/거버넌스 summary 모두 — Claude.ai 사용자가 호출 횟수 고민 X
- tool docstring 짧아져 Claude.ai 동적 loading 부담 ↓
- LLM scope 선택 lag 감소 ("어떤 scope 적절?" → 항상 default)
- treasury/dilutive/restructuring은 1회 호출에 모든 type detail card 노출 — 사용자 의도 정렬

## Trade-off

- **사용자 자유도 ↓**: "재무만" 보고 싶을 때 proxy_advise scope=financial 호출 못함. 대신 financial_metrics tool 직접 호출.
- **응답 size 증가**: scope 통합으로 한 응답이 모든 detail 포함 (예: dilutive_issuance가 4 type 다 노출). 사용자가 token 더 받음.
- **migration cost**: 기존 scope 명시 호출은 모두 폐지 (default로 fallback). docstring 명시.

## Takeaway

- **scope이 다른 tool과 raw 중복이면 폐지가 옳음**. tool 16개 모두 노출되어 있으니 사용자가 필요시 직접 호출.
- type-specific scope (merger/split, rights/CB/BW/감자)은 summary가 timeline + 모든 type detail card 한 번에 노출하는 게 일관.
- scope 1개로 가는 게 진정한 단순화. "default scope이면 충분"이라는 docstring보다 그냥 scope param 자체 제거.
- 단, **별도 source가 필요한 chain (예: dividend의 CSR이 treasury_share chain)은 별도 scope 또는 별도 tool**. 가벼운 chain만 단일 scope에 통합.

## 관련

- [[time-axis-tool-split]] (시점 분리는 단순화의 다른 axis)
- [[decision-vs-raw-separation]] (raw expose는 외부 tool 직접 호출 권장)
- 코드 변경: 2026-05-04 commits `ea00d53`, `4638669`, 2026-05-05 commit `c37e330`
