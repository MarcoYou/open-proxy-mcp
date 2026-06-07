# 지분·지배구조 맵

회사의 **소유 구조(who holds what)**를 한 탭에서 그립니다.
최대주주부터 특수관계인, 5% 대량보유까지 — 판의 구조를 한눈에 봅니다.

## 5개 보기(scope)

| scope | 무엇을 보나 |
|---|---|
| **summary** | 지분 구조 종합 |
| **major_holders** | 최대주주 + 특수관계인 명부 (본인·친인척·계열사) |
| **blocks** | 5% 대량보유 — 최신 + 과거 이력(history) |
| **control_map** | 지배 구조 관계도 |
| **changes** | 지분 변동 내역 |

> 자사주(자기주식)는 별도 [treasury_share](../../wiki/tools/treasury_share.md)로 분리되어 있습니다 (이 tool에서는 폐기).

## control_map — registry_overlap

지배 구조 관계도에서 각 주체가 **최대주주 명부에 있는지(registry_overlap)**를 표시합니다.

- `registry_overlap = true` — 신고자 이름이 사업보고서 최대주주 명부에 존재 (대주주 본인 계열로 추정)
- `registry_overlap = false` — 명부에 없음 (외부 세력 가능성)

> registry_overlap은 **같은 이름이 명부에 있는지**를 뜻하며, 현재 이해관계가 완전히 같다는 의미는 아닙니다 (동명이인·이해관계 변화 가능).

## 어떻게 쓰나

> "SK하이닉스 지분 구조랑 최대주주 알려줘"

최대주주-특수관계인-5%가 정리된 지분 표로 나옵니다.

## 함께 보면 좋은 기능

- [경영권 분쟁 시그널](control-contest.md) — 지분 **변동**이 분쟁 신호인지
- [주주환원](shareholder-return.md) — 자기주식(의결권 없는 지분) 상세

## 기술 상세 (개발자용)

> 아래는 각 tool의 입력·출력 등 기술 문서입니다. 일반 사용자는 보지 않아도 됩니다.

- [ownership_structure](../../wiki/tools/ownership_structure.md) — 지분 구조
- [treasury_share](../../wiki/tools/treasury_share.md) — 자기주식
