---
type: lesson
title: decision logic은 안에, raw expose는 밖에
context: proxy_guideline 12 매트릭스 dead 발견 (2026-05-05)
date_learned: 2026-05-05
related_decisions: [260505_1700_decision_inside-director-performance-matrix]
---

# decision logic vs raw expose 분리

## Context

OPM에는 두 가지 종류의 logic 자산이 쌓여있었음:

1. **proxy_guideline 12 매트릭스 + 100 dim 자동 채점** (`services/proxy_guideline_scoring.py`):
   - 카테고리별 8 dim (사외이사 독립성 / 이해상충 / 보수 적정성 등)
   - 빙고 패턴 인터프리터 + condition 표현식 평가
   - 자동 채점 (~71 dim auto, ~29 manual)

2. **proxy_advise OPM 자체 logic** (`services/proxy_advise.py` `_decide_*` 함수들):
   - `_decide_director_election`, `_decide_financial_statements`, `_decide_compensation` 등
   - 카테고리별 직접 if-else (자본잠식 / 감사의견 / 소진율 등)
   - `vote_style` 정책 JSON (운용사별 default for/against)와 함께 wire

ralph 27 iter G2 99.36% (4+ vote majority 기준) 검증 — 어느 logic으로?

## Did

코드 grep 결과:
- `build_proxy_guideline_payload` 호출하는 곳 **0개**.
- `proxy_advise.py` line 48 import만 (사용 X — dead import).
- ralph G2 99.36% 검증은 **OPM 자체 `_decide_*` + vote_style policy JSON**으로 도달.

12 매트릭스는 사실상 dead code 상태. proxy_guideline tool도 internal로 만들었지만 internal에서도 호출 X.

처리:
- `services/proxy_guideline.py` + `proxy_guideline_scoring.py` + `policy_comparison.py` → `wiki/archive/services/`로 archive
- proxy_advise scope `policy_basis` (proxy_comparison 호출했던 유일 scope)도 폐지
- decision 도출은 OPM 자체 logic 그대로 유지 — 검증된 작동

## Improved

- **dead code 9% 감소** (~707 lines archive)
- proxy_advise tool 단순화: scope 10 → 1 (decisions만)
- 사용자 의문 ("decisions 어떻게 도출됨?") 해결: OPM 자체 logic + vote_style JSON, 12 매트릭스 무관
- import overhead 감소

## Trade-off

- **12 매트릭스 자산 잃을 위험**: archive에 보존하지만 활용 X. 부활하려면 별도 standalone tool로 만들어야.
- **자동 채점 logic 잃음**: proxy_guideline_scoring의 ~71 dim 자동 채점이 valid했는데 사용 안 함.
- **OPM 자체 logic 의존도 ↑**: 12 매트릭스 같은 차별화 자산 없이 단순 if-else로만 결정. 다만 G2 99.36% 검증되어 작동 OK.

## Takeaway

- **dead code는 audit으로 식별 후 archive**. import만 살아있으면 false sense of safety.
- **decision logic은 단순 + 검증된 게 valid**. 복잡한 매트릭스보다 "자본잠식이면 AGAINST" 같은 명확 룰이 trace 가능 + audit 가능.
- **raw expose는 별도 tool 직접 호출 권장**. proxy_advise scope=financial 같은 raw 노출 scope보다 financial_metrics tool 직접 호출이 일관 (사용자가 다른 tool도 있다는 사실 인지 + scope 학습 부담 ↓).
- **decision tool과 raw tool은 layer 분리**: decision tool은 결정 + 근거 (facts + risk + citation), raw tool은 도메인 detail. 한 tool에서 둘 다 노출하려는 욕심이 scope 비대를 만듦.

## 관련

- [[scope-simplification]] (raw expose scope 폐지)
- [[enrichment-as-infrastructure]] (decision의 근거 enrichment가 raw expose 대체)
- archive: `wiki/archive/services/proxy_guideline.py`, `proxy_guideline_scoring.py`, `policy_comparison.py`
- commit: `c37e330` (2026-05-05)
