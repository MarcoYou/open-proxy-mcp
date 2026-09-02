---
type: tool
title: dividend_screener
domain: data
status: 등록 완료 (260902 — tools/dividend_screener.py) · 260902 quarterly_only 판정 교체
scope: [screen]
data_source: [DART 정기보고서 alotMatter 전수 수집본(div_declared·div_quarterly), WISE 섹터 매핑]
related_disclosures: [사업보고서, 분기보고서]
related_concepts: [배당성향, DPS, 분기배당, 중간배당]
created: 2026-09-02
updated: 2026-09-02
---

# dividend_screener

## 한 줄 요약
한 사업연도에서 **배당 조건으로 회사를 가로로 거른다** — 배당성향 범위 · 최소 DPS ·
그 해 두 번 이상 배당했나 · WICS 섹터. 회사 하나는 [[dividend_disclosure]], 한 회사의 여러 해는
[[dividend_history_data]].

## 사용법
```
dividend_screener(bsns_year=2025, min_payout=50, limit=300)
dividend_screener(bsns_year=2025, quarterly_only=True)
dividend_screener(bsns_year=2025, sector="금융", min_dps=1000)
```

## 세는 법 — 셋을 갈라 낸다
| 값 | 무엇 |
|---|---|
| 모집단 | 조건을 걸기 전 그 해 회사 수 |
| 매칭 | **조건에 걸린 전체** |
| 실은 수 | 이번 응답에 담은 수 (`limit` = 표시 한도) |

🔴 **`limit` 은 표시 한도지 매칭 수가 아니다.** 260902 이전에는 이 셋이 한 칸에 있어서
「100사」로 읽힌 것이 실은 121사였다.

## `quarterly_only` — 무엇을 세나 (260902 교체)
**그 해에 두 번 이상 배당한 회사.** 판정은 분기 원장에서 배당액 > 0 인 분기가 2개 이상인지.

종전 판정은 「4칸이 모두 확정인가」였는데 그건 **데이터가 채워졌나**를 보는 조건이지
**분기배당을 하나**를 보는 조건이 아니었다. 두 방향으로 틀렸다 —
- 계룡건설(013580)은 연 1회 배당인데 들어왔다
- KB금융(105560)은 실제 분기배당인데 FY2025 원장이 비어 빠졌다

그 과정에서 더 깊은 결함이 드러났다. **연 1회 배당사는 1~3분기 보고서에 전기 확정 배당액을
그대로 싣는다.** 그 해 분기 누적이 전기 사업연도 누적과 같으면 `전기이월` 로 갈라 담고
차분의 기준선에서도 뺀다(실측 256건 중 183건).

## 「없다」와 「모른다」를 가른다
분기 원장이 4칸을 못 채운 배당사는 목록에서 빼되 **`판단불가 N사`** 로 수를 밝힌다.
목록에 없다고 분기배당을 안 하는 것이 아니다(FY2025 566사).

## 대상 행
`보통`·`미구분` 주식 행만. `종류`(상환·전환·무의결권)는 뺀다. **DPS 0원은 배당한 것이
아니므로 뺀다** — 원문에 0 이 적혀 있어도 그렇다.

## 관련
[[dividend_disclosure]] · [[dividend_history_data]] · [[screener]] · [[price_multiple_data]] · [[evidence]]
