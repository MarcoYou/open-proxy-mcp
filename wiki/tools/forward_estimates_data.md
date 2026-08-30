---
type: tool
title: forward_estimates_data
domain: data
status: 등록 완료 (260830 — tools/forward_estimates_data.py, 브랜치 beta)
scope: [firm]
data_source: [Supabase `fwd` 컨센서스 추정치 스냅샷 (외부 벤더 원천 + 파생 계산)]
related_disclosures: []
related_concepts: [당기순이익, ROE, 배당수익률]
created: 2026-08-30
updated: 2026-08-30
---

# forward_estimates_data

컨센서스 **포워드 추정치**(내년·내후년 예상 실적과 배수)를 낸다. 대조용으로 최근 실적 행을
같이 싣는다 — 「2026E EPS 48,139」는 「2025A 6,564」 옆에 있어야 뜻이 생긴다.

**DART 공시가 아니다.** 애널리스트 컨센서스 스냅샷(`fwd`, Supabase)을 읽는다.

## 입력 인자
| 인자 | 타입 | 필수 | 설명 | 기본값 |
|---|---|---|---|---|
| company | str | yes | 회사명 / ticker(6자리) / corp_code. 공용 리졸버(`company` 도구와 동일 진입) | "" |
| bundle | str | no | `core` / `growth` / `quality` / `keys` / `all`. 쉼표로 겹쳐 부른다(`"core,growth"`) | "core" |
| period_type | str | no | `FY`(연간) / `Q`(분기) / `all` | "FY" |
| actual_years | int | no | 대조용으로 실을 **실적** 행 수 | 4 |
| format | str | no | "md" / "json" | "md" |

## 응답 구조 — 자(尺)를 두 겹으로 싣는다

```
{ "tool":"forward_estimates_data", "status":"ok",
  "data": {
    "ruler": { "as_of":..., "price_dd":..., "price_krw":..., "mktcap_krw":...,
               "unit":..., "per_def":..., "multiple_scope":..., "row_split":... },   ← 봉투에 한 번
    "rows": [ { "period":"2026.12E", "row_kind":"estimate", "basis":"IFRS연결",
                "reported": {...},     ← 벤더가 말한 것 (틀리면 벤더 책임)
                "derived":  {...} } ]  ← 우리가 계산한 것 (검산 대상)
  } }
```

🔴 **`price_dd` 는 어떤 bundle 에서도 빠지지 않는다.** `as_of` 가 2026-08-30(일)인데 주가는
8/28 종가다. 안 실으면 읽는 AI 가 「8월 30일 기준 PER」이라고 말한다.

### 왜 실적/추정이 아니라 원천/파생으로 가르나
성장률이 그 경계를 넘나든다 — 추정 행의 전기(前期)가 실적 행인 경우가 2,180행이다.
실적/추정으로 가르면 `eps_growth_pct` 가 블록 경계를 넘어 읽는 쪽이 두 번 호출해 조인해야 한다.
반면 원천/파생은 **신뢰 등급·갱신 주기·틀렸을 때의 책임**이 다르다. 추정이냐는 `row_kind`
한 칸으로 이미 행이 지고 있다. (판정 [[../decisions/README|260830 verdict]] 1장)

## 묶음(bundle) — 기본은 좁게, 넓힐 손잡이를 준다
| 묶음 | 무엇 |
|---|---|
| `core` | 기간·구분·회계기준 · 매출·영업이익·지배순이익·EPS·BPS·DPS·배당수익률 · PER/PBR/PSR + 각 배수의 자(`*_basis`)·빈 이유(`*_why`) |
| `growth` | `*_growth_pct` · `*_growth_disp` · `*_growth_state` · `prev_*` · `prev_period` · PEG · 벤더 YoY |
| `quality` | ROE·ROA·마진·부채비율·유보율·당좌비율·payout·EBITDA·CAPEX·FCF·주식수 |
| `keys` | `sec_id`·`co_id`·`period_end`·`period_months`·`fyr`·`fy_end`·`fy_major`·`basis_from`·`fiscal_year` |

`fiscal_year`·`fy_end`·`fy_major` 는 30,609행 중 각각 191·218·307행이 서로 다르다. 이름 셋 다
그럴듯해서 그냥 내보내면 읽는 AI 가 아무거나 고른다 — 그래서 `keys` 로 숨긴다.

## 배수 정의 — `price_multiple_data` 와 맞췄다
**PER = 보통주 시총 ÷ 지배주주순이익.** `fwd` 원본(`fwd_per`)은 주가÷EPS 인데 그 식은 260823 에
하우스에서 **의도적으로 버린 것**이다(액면분할·병합 때 옛 주식수 기준 EPS 와 새 주가가 섞인다).
같은 `per` 라는 이름으로 두 도구가 다른 값을 내면 — 삼성 FY2025 **33.95 vs 39.15, 15.3% 차** —
한 답변에 나란히 놓였을 때 읽는 AI 가 하나를 고르고 근거를 지어낸다. 이름을 가르는 대신
**정의를 맞췄다.** 10% 이상 갈리는 기간은 응답 경고에 기간별로 적는다.

PBR·PSR 은 충돌이 없다. 시총 = 주가 × 보통주식수 라서 주가÷BPS ≡ 시총÷(BPS×주식수) 이고
주가÷SPS ≡ 시총÷매출 이다. 값은 그대로 두고 자(`pbr_basis`·`psr_basis`)만 적는다.

### 배수를 두는 범위
**추정 FY 행과 최신 확정 FY 행에만** 둔다. 원본은 실적 행에 `fwd_per` 을 16,778개 채워 놨는데
그 80.5%(6,754행)가 「**오늘 주가 ÷ 몇 년 전 EPS**」다(삼성 2023.12A = 120.6, `per_why`='ok').
이름 없는 숫자에 PER 이라는 이름이 붙은 것이라 지웠고, **왜 지웠는지**를 `per_why` 로 남긴다.

## 단위
**금액은 전부 원(KRW) 정수.** 억원(`_eok`)은 응답 밖으로 나가지 않는다(마스터 결정 260830).
DB 물리 칸이 아직 `_eok` 면 도구가 ×1e8 해서 원으로 통일한다. `ruler.unit` 에 명시된다.

## status — 「없음」을 세 가지로 가른다
| status | 뜻 | 읽는 쪽이 할 일 |
|---|---|---|
| `ok` | 추정 행이 있다 | — |
| `no_estimates` | 그 종목은 **애널리스트 미커버**. 전체 2,764종목 중 추정 보유는 **713종목(25.8%)** | 자료 없음이지 오류가 아니다. `price_multiple_data`·`financial_metrics` 로 |
| `not_found` | 그런 종목이 없다(오탈자·비상장) | 이름 재확인. `company` 도구 |
| `unlisted` | 회사는 있는데 비상장 | — |
| `ambiguous` | 동명 후보 여러 건 | 후보표에서 골라 종목코드로 재시도 |
| `db_error` | **DB 장애** | 재시도. 자료 없음과 다르다 |
| `invalid` | 입력 오류 | 인자 확인 |

🔴 셋(`no_estimates`/`not_found`/`db_error`)을 뭉뚱그리면 안 된다 — 취할 행동이 셋 다 다르다.
`db.py` 의 `pg_rows()` 가 지키는 「None = DB 장애, [] = 데이터 없음」 갈래를 status 로 올린 것이다.

## 추정 행에 원래 없는 칸
`debt_interest_krw` · `reserve_ratio_pct` · `quick_ratio_pct` · `shares_common` 은 추정 행
채움률이 **0.0%** 다. 그냥 비워 내보내면 읽는 AI 가 「이 회사는 자료가 없구나」로 읽는데
**회사 특성이 아니라 데이터 종류의 특성**이다. 그래서 값이 아니라 `fields_absent_by_design`
으로 **이유를 적어** 내보낸다. `shares_common` 은 없으면 주당값 검산이 막히므로 봉투에
`shares_common_latest`(최근 실적 행 주식수)를 실어 검산 길을 터 준다.

## 칸 이름 개명 대응
`fwd` 표는 개명이 진행 중이다(`fwd_per`→`per`, `_eok`→`_krw`, `div_yield_pct`→
`div_yield_at_period_end_pct` 등). 도구는 `information_schema` 로 **실제 있는 칸을 골라 쓴다** —
매핑 표는 `services/forward_estimates.py` 의 `_FIELDS` **한 곳**에만 있다. 개명 전후 양쪽에서 돈다.

## DART 콜
**0콜.** Supabase 조회만 한다(회사 식별은 공용 리졸버 캐시 경로).

## 관련
- [[price_multiple_data]] — 확정 실적 기반 현재 배수(정의 동일)
- [[financial_metrics]] — DART 재무 원본
- [[dividend]] — 배당 상세
- [[company]] — 회사 식별
