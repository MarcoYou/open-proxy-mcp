---
type: reference
title: tool별 DART API 콜 budget (기업당 최대)
updated: 2026-06-20
method: 코드 실측 (services/*.py 의 DART client 호출 지점)
---

# Tool별 DART API 콜 budget

> 각 tool이 **기업 1곳당 호출하는 DART API 최대 콜 수**입니다. 유니버스 배치(전수조사·workflow)에서
> **안전한 기업 수를 계산**하는 근거이며, DART 분당 한도(910)를 넘지 않게 관리합니다.
>
> ⚠️ **tool 코드가 바뀌면 콜 수도 바뀝니다.** 코드 변경 시 이 표를 갱신하세요. (예: proxy_advise가
> scope 10→1로 줄며 콜 수가 변했던 이력) — 주간 wiki 점검 routine과 커밋 hook이 갱신을 환기합니다.
>
> 📌 **두 가지 콜 모드 (중요)** — budget 계산이 완전히 다릅니다:
> - **per-firm (기업당)**: 대부분 tool. 유니버스 N사를 순회 → **총 콜 = N × 기업당 콜**.
> - **market-scan (시장 전체 1회 쿼리)**: `risk_events`를 company 없이 쓰는 경우 등. 유니버스를
>   순회하지 않고 **한 번에 시장 전체를 훑음 → 고정 ~45콜** (N 곱셈 없음). 유니버스가 커도 콜 수
>   불변. 안전 크기를 계산할 때 이 모드는 N×C 공식을 쓰지 않습니다.

## 기업당 콜 수 (실측, 2026-06-20)

| tool | 최대 콜 | 일반 | 가변 요인 |
|---|---|---|---|
| evidence | 0 | 0 | API 0회 (문자열 가공) |
| company | 3 | 2 | corpCode 캐시 적중 시 2 |
| corporate_restructuring | 4 | 4 | DS005 4종 병렬 (단일 scope 1~2) |
| dilutive_issuance | 4 | 4 | DS005 4종 병렬 (단일 scope 1) |
| value_up | 6 | 5 | commitments scope +자사주 교차참조 |
| corp_gov_report | 7 | 3 | timeline scope 시 문서 4건 추가 |
| risk_events (company 지정) | 6 | 1 | **per-firm**. company 미지정은 아래 '시장 스캔' 참조 |
| shareholder_meeting_notice | 9 | 6 | auto 모드 2회 검색 + viewer fallback |
| treasury_share | 10 | 5 | 소각 공시 N건 본문(최대 5) |
| shareholder_meeting_results | 10 | 6 | notice + 결과 공시 |
| valuation | ~18 | ~14 | financial_metrics(~7) 래핑 + acntAll×3(CFS→OFS 폴백) + company + stockTotqySttus + 배당. **KRX 시세=Supabase krx_weekly_px 서빙(serve-time 0콜, 하루 ~2콜 스냅샷, 주간 누적)** · ECOS 0~1(캐시). 상세 [[valuation]] |
| ownership_structure | 12 | 9 | 대주주 reprt 폴백 4 + 5% 블록 문서 3 |
| financial_metrics | 12 | 7 | reprt 폴백 + TTM + 당기분해 (quarterly scope는 ~24) |
| corporate_deals | 11 | 1 | include_details 시 details_limit(기본5·최대10) |
| dividend | 21 | 13 | 배당결정 공시 N(최대12) + history 분기교정 8 |
| order_contracts | 31 | 1 | max_documents (기본 30, 범위 5~50) |
| proxy_contest | 35 | 5 | litigation 파싱 시 미상 소송 본문 최대 30 |
| proxy_advise_before_meeting | 32 | 23 | upstream 5개(주총=advise scope 1회) + 사내이사 연임 시 추가 4개. 260623: 주총 4-scope→advise(=full-results) 통합으로 -5 |

### 시장 전체 스캔 (기업당 아님 — 1회 쿼리당)
| tool | 콜/쿼리 | 비고 |
|---|---|---|
| risk_events (company 미지정) | ~45 | 30일 시장 스캔: I001 ~36p + B001 ~7p + 상세. 기간 길면 증가 |

## 안전한 유니버스 크기 계산

DART 분당 한도는 **910콜**(client `_throttle_api`가 강제 — 초과 시 차단이 아니라 자동 대기). 따라서
유니버스 N사를 한 tool로 돌리면 **총 콜 = N × (tool 최대 콜)**, 소요 시간 ≈ 총 콜 / 910 분.

| tool 최대 콜 | 안전 유니버스(≈1분 내, 910콜 기준) | 비고 |
|---|---|---|
| 0 (evidence) | 사실상 무제한 | |
| 3~4 (company·restructuring·dilutive) | ~200사 | KOSPI200 전수 가능 |
| 7~12 (대부분 data tool) | ~75~130사 | 한 번에 100사 안팎 |
| 21~37 (dividend history·order_contracts·proxy_contest·proxy_advise) | ~25~43사 | **소규모로 나눠 실행** |

> 차단(분당 1000) 위험은 limiter가 막지만, 큰 유니버스 × 무거운 tool은 **시간이 오래** 걸립니다.
> 100사 넘는 무거운 tool 배치는 batch로 나누거나 fly machine(다른 IP)을 씁니다.

## 기계 파싱용 (workflow·점검 스크립트가 읽음)

```json
{
  "dart_per_minute_cap": 910,
  "max_calls_per_company": {
    "evidence": 0,
    "company": 3,
    "corporate_restructuring": 4,
    "dilutive_issuance": 4,
    "value_up": 6,
    "corp_gov_report": 7,
    "shareholder_meeting_notice": 9,
    "treasury_share": 10,
    "shareholder_meeting_results": 10,
    "ownership_structure": 12,
    "financial_metrics": 12,
    "corporate_deals": 11,
    "dividend": 21,
    "order_contracts": 31,
    "proxy_contest": 35,
    "proxy_advise_before_meeting": 32,
    "risk_events": 6
  },
  "market_scan_per_query": { "risk_events": 45 }
}
```

## 갱신 규칙
- tool의 services 코드에서 DART 호출 지점(scope·gather·루프 상한)이 바뀌면 위 표·JSON을 함께 갱신.
- 검증: 해당 tool을 실제 호출하고 `data.timings_ms` 또는 client 콜 카운트로 실측 대조.
