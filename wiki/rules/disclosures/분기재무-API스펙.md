---
type: reference
title: 분기재무-API스펙
tags: [dart-api, financial-statement, quarterly, timeseries, ttm, pit]
source: DART OpenAPI (fnlttSinglAcntAll)
related: [분기보고서, 반기보고서, 사업보고서, 공시유형코드체계]
purpose: mkt_fund_q(분기 재무 시계열 저장소) 구축용 DART 분기 재무 API 스펙 — 코드 실측 기반
updated: 2026-07-05
---

# 분기 재무 API 스펙 (fnlttSinglAcntAll)

> **분기 재무 시계열 저장소(`mkt_fund_q`)** 구축을 위한 DART 분기 재무 API의 정밀 스펙.
> 모든 항목은 OPM 코드 실측 근거(`파일:줄`)로 문서화한다. TTM(직전 4분기 합산) 계산과
> look-ahead 방지(PIT)를 위한 필드 의미·공시 타이밍이 핵심.
>
> 관련: [[분기보고서]] · [[반기보고서]] · [[사업보고서]] · [[공시유형코드체계]]

---

## 1. 엔드포인트: `fnlttSinglAcntAll.json`

DART "단일회사 전체 재무제표" — BS/IS/CIS/CF/SCE를 한 응답(`list`)에 준다.
구현: `open_proxy_mcp/dart/client.py:1315`의 `get_fnltt_singl_acnt_all`.

### 1.1 파라미터

| 파라미터 | 값 | 의미 | 근거 |
|---|---|---|---|
| `corp_code` | 8자리 | DART 기업코드 | client.py:1325 |
| `bsns_year` | "YYYY" | 사업연도 (예: "2024") | client.py:1326 |
| `reprt_code` | 4종(아래) | 보고서 구분 | client.py:1327 |
| `fs_div` | `CFS`/`OFS` | 연결(default)/별도 | client.py:1328 |

### 1.2 reprt_code 4종 (분기 커버리지)

| reprt_code | 보고서 | 누적 구간 | 결산(12월법인) | 근거 |
|---|---|---|---|---|
| `11013` | 1분기보고서 | 3개월 (1Q) | 3/31 | client.py:1327 |
| `11012` | 반기보고서 | 6개월 (1H) | 6/30 | client.py:1327 |
| `11014` | 3분기보고서 | 9개월 (3Q) | 9/30 | client.py:1327 |
| `11011` | 사업보고서 | 12개월 (연간=Q4 포함) | 12/31 | client.py:1327 |

`_period_months`(`services/financial_metrics.py:228`)가 이 매핑을 코드로 고정:
누적 개월 = `{"11013":3, "11012":6, "11014":9}`, `11011`(사업보고서)=12 (financial_metrics.py:235).

> **Q4(4분기 standalone)는 별도 보고서가 없다.** 사업보고서(11011)는 연간 누적이므로
> `Q4 = 연간(11011) − 3Q누적(11014)`로 파생 계산해야 한다.

---

## 2. 누적(YTD) vs 당기(standalone) vs 잔액 — TTM의 핵심

DART IS 응답은 **당기 3개월(standalone)**과 **당기 누적(YTD)**을 별도 필드로 준다.
이 구분을 틀리면 TTM이 통째로 어긋난다.

### 2.1 필드 의미 (`_extract_cumulative_is` financial_metrics.py:217)

| sj_div | 필드 | 의미 | 기간성 |
|---|---|---|---|
| IS/CIS (손익) | `thstrm_amount` | **당기 3개월(standalone)** | 분기별 3개월 |
| IS/CIS (손익) | `thstrm_add_amount` | **당기 누적(YTD)** | 보고시점까지 누적 |
| BS (재무상태) | `thstrm_amount` | **기말 잔액** | 시점(stock), 기간 무관 |
| 전기 비교 | `frmtrm_amount` | 전기(직전연도 동보고서) 값 | — |
| 전전기 비교 | `bfefrmtrm_amount` | 전전기 값 | — |

### 2.2 결정적 예외 — 1분기·사업보고서는 `thstrm_add`가 빈다

`_extract_cumulative_is`(financial_metrics.py:217-224)의 로직:

```
누적값 = thstrm_add_amount 우선; 없으면(None/공란) thstrm_amount
```

- **1분기(11013)**: 3개월 = 누적이라 `thstrm_add`가 비어 → `thstrm`이 곧 누적. (financial_metrics.py:221)
- **사업보고서(11011)**: 연간 = 누적이라 `thstrm_add`가 비어 → `thstrm`이 곧 누적. (financial_metrics.py:221)
- **반기(11012)·3분기(11014)**: `thstrm_amount`=당기 3개월, `thstrm_add_amount`=누적(6·9개월).
  → 누적을 쓰려면 반드시 `thstrm_add_amount`를 읽어야 한다.

> **함정**: 반기/3분기에서 `thstrm_amount`만 읽으면 "그 분기 3개월치"만 잡혀 YTD가 아니다.
> `_build_account_map_all`(financial_metrics.py:290)은 `cumulative_is=True`일 때만
> IS/CIS를 `_extract_cumulative_is`로 읽고, BS(잔액)·CF(native 누적)는 `thstrm_amount`
> 그대로 쓴다(financial_metrics.py:277,289).

### 2.3 BS는 기간 무관 잔액

`_build_account_map`(financial_metrics.py:258): "BS는 항상 잔액" — `_extract_period_amount`로
`thstrm_amount`를 그대로 읽는다. 자본·자산 등은 특정 시점의 stock 값이므로 누적 개념이 없다.

### 2.4 왜 이 구분이 TTM에 필수인가

**TTM(Trailing Twelve Months) = 전년 연간 + 당해 누적(YTD) − 전년동기 누적(YTD)**

예) 3Q 시점 TTM 순이익:

```
TTM = FY(전년, 11011)  +  3Q누적(당해, 11014)  −  3Q누적(전년, 11014)
       └ thstrm(연간)      └ thstrm_add(9M)        └ 전년동기 9M 누적
```

- 세 항 모두 **누적(YTD)** 기준이어야 성립 — standalone 3개월을 섞으면 틀린다.
- 전년동기 누적은 **당해 보고서의 `frmtrm_amount`**로 한 응답에서 얻을 수 있어 추가 콜 불필요
  (frmtrm=전기 동보고서 비교치이므로 그 자체가 전년동기 누적).
- BS 항목(자본 등)은 잔액이라 TTM 합산이 아니라 **최신 분기 잔액을 그대로** 쓴다.

---

## 3. account_id — 지배주주 귀속 값 (sj_div별 탐색)

회사마다 `account_nm`(계정명 한글) 표기가 달라 substring 매칭이 실패하므로, 지배주주
귀속 값은 **IFRS `account_id` 정확일치(==)**로 잡는다. 근거: `market_val_series.py:106-109`,
`scale_guard.gid_exact`(scale_guard.py:34).

| 지표 | 1순위 account_id | 폴백 account_id | sj_div | 근거 |
|---|---|---|---|---|
| 지배순이익 | `ifrs-full_ProfitLossAttributableToOwnersOfParent` | `ifrs-full_ProfitLoss` | `("CIS","IS")` | market_val_series.py:106-108 |
| 지배자본 | `ifrs-full_EquityAttributableToOwnersOfParent` | `ifrs-full_Equity` | `("BS",)` | market_val_series.py:106,109 |

- **순이익은 CIS 우선, IS 폴백** — `gid(rows, attr, ("CIS","IS"))`는 sj_div 허용집합을
  `("CIS","IS")`로 넘겨 CIS(포괄손익)·IS(손익) 어디에 있든 잡는다 (market_val_series.py:107).
- **자본은 BS에서만** — `("BS",)` (market_val_series.py:109).
- `gid_exact`는 `account_id == target` **정확일치만** 매칭 — substring(`in`) 금지.
  260704 실측: `"ifrs-full_Liabilities" in "ifrs-full_LiabilitiesIncludedInDisposalGroups…"`
  접두어 충돌로 정상 종목을 오탐 → exact로 교정 (scale_guard.py:7-9,37).
- `financial_metrics.py:295`도 동일 원리: BS의 지배자본을 `account_id`에
  `"EquityAttributableToOwnersOfParent"`가 있으면 우선 매칭(계정명 표기 편차 방어).

### 3.1 frmtrm/bfefrmtrm로 3개년 한 콜 확보

`gid`에 `field` 인자를 바꿔 같은 응답에서 전기·전전기를 읽는다
(market_val_series.py:108,159-161): `frmtrm_amount`=전년, `bfefrmtrm_amount`=전전년.
→ **1콜로 당해+전년+전전년 3개년** 확보 가능 (분기 시계열 백필 시 콜 절감의 핵심).

---

## 4. PIT 공시 타이밍 — look-ahead 방지

분기 재무는 결산일이 아니라 **보고서 제출(공시)일에야 시장에 알려진다.** 시계열에서 가격일 D의
밸류를 계산할 때, D 시점에 **실제로 공시돼 있던** (fy,quarter)만 써야 한다(look-ahead 방지).

### 4.1 법정 제출기한 (자본시장법 §160·§159)

반기·분기 = 기간 경과 후 **45일 이내**, 사업보고서 = 사업연도 경과 후 **90일 이내**
([자본시장법 §160](https://casenote.kr/%EB%B2%95%EB%A0%B9/%EC%9E%90%EB%B3%B8%EC%8B%9C%EC%9E%A5%EA%B3%BC_%EA%B8%88%EC%9C%B5%ED%88%AC%EC%9E%90%EC%97%85%EC%97%90_%EA%B4%80%ED%95%9C_%EB%B2%95%EB%A5%A0/%EC%A0%9C160%EC%A1%B0),
[찾기쉬운 생활법령정보](https://easylaw.go.kr/CSP/CnpClsMain.laf?popMenu=ov&csmSeq=1701&ccfNo=1&cciNo=3&cnpClsNo=1)).

| 보고서 | reprt_code | 결산(12월법인) | 법정기한 | PIT 가용 시작(근사) |
|---|---|---|---|---|
| 1분기 | 11013 | 3/31 | +45일 | **~5/15** |
| 반기 | 11012 | 6/30 | +45일 | **~8/14** |
| 3분기 | 11014 | 9/30 | +45일 | **~11/14** |
| 사업보고서 | 11011 | 12/31 | +90일 | **~다음해 3/31** |

> 특례: 연결 기준 반기·분기보고서는 최초 사업연도와 그 다음 사업연도에 한해 60일 이내 연장 가능
> (신규 상장·연결 최초 적용 종목만) — 일반 시계열엔 45일 기준을 쓰되 이 종목은 예외.

### 4.2 가격일 D → 최신 가용 (fy, quarter) 매핑 규칙 (12월결산 기준)

가격일 D의 (월/일)로 그 시점 **가장 최근에 공시됐을** 정기재무를 정한다:

| D 구간(12월결산) | 최신 가용 보고서 | 근거 |
|---|---|---|
| 1/1 ~ 3/31 | 전전년 3Q(11014) 누적 or 전전년 연간 미확정 → **전전년 3Q** | 사업보고서 90일 전이면 연간 미공시 |
| 4/1 ~ 5/15 | **전년 사업보고서**(11011, 연간) | 3월말까지 제출 |
| 5/16 ~ 8/14 | **당해 1분기**(11013) | 5/15까지 제출 |
| 8/15 ~ 11/14 | **당해 반기**(11012) | 8/14까지 제출 |
| 11/15 ~ 12/31 | **당해 3분기**(11014) | 11/14까지 제출 |

- 현행 `market_val_series.series()`(market_val_series.py:216-217)는 연 단위 근사만 씀:
  `pit_fy = y-1 if m>=4 else y-2` — 4월 이후면 전년 FY, 아니면 전전년 FY(사업보고서 3월중순
  공시 규칙의 연 단위 축약). **분기 시계열(`mkt_fund_q`)에선 위 표처럼 분기 해상도로 세분**해야
  분기 재무의 look-ahead를 막는다.
- 실제 접수일이 필요하면 `list.json`의 `rcept_dt`로 종목별 실제 공시일을 확인해 근사 대신
  실측 PIT를 쓸 수 있다(기한보다 일찍 내는 종목 다수).

---

## 5. 콜 budget

| 단위 | 콜 수 | 비고 |
|---|---|---|
| 종목·분기 1건 (CFS 성공) | **1콜** | market_val_series.py:103 |
| 종목·분기 1건 (CFS 부재→OFS 폴백) | **2콜** | market_val_series.py:104-105 |
| 종목·연 (4 reprt 전체) | **4~8콜** | 11013+11012+11014+11011 |
| 백필: 종목 × 연 × 4reprt | N×Y×4 | 최소, OFS 폴백 시 최대 ×2 |

- **콜 절감**: `frmtrm/bfefrmtrm`로 한 응답에서 3개년 비교치 확보(§3.1) →
  연간(11011) 백필은 종목당 1콜로 3개년 커버 가능(market_val_series.py:133-172, `backfill_restated`).
- **분당 한도 910**(키 2개 fallback, CLAUDE.md). 실측 배치는 **콜 사이 `sleep(0.45)`**
  (market_val_series.py:103,105) → 분당 ~130콜 수준으로 안전(910 대비 여유). 100+사 대량 백필은
  fly machine에서, 독립 스크립트는 동시성 1~2 + ReadError 즉시 중단(market_val_series.py:122-125).

---

## 6. 엣지 / 함정

| # | 케이스 | 증상 | 처리 | 근거 |
|---|---|---|---|---|
| 1 | **[013] 데이터없음** | DartClientError `[013]` | 빈 리스트로 흡수(예외 재raise 안 함) | market_val_series.py:98 |
| 2 | **CFS 부재(별도만)** | CFS 응답 빈 rows | OFS로 재조회 폴백 | market_val_series.py:104-105 |
| 3 | **스케일오류(100만배)** | XBRL 단위 미적용 부풀림 (소프트센 032680 FY2022, 매출 73조×10^6) | scale_guard `assess` hard tier → ni/eq 무효화 | market_val_series.py:114-117; scale_guard.py:101 |
| 4 | **비12월 결산** | 결산월≠12월 → PIT 매핑 어긋남 | 종목별 결산월 확인해 §4.2 표를 결산월 기준으로 shift | (12월법인 기준 표 주의) |
| 5 | **재작성(restated)** | 당해 XBRL 오류를 다음해 보고서 전기란이 정정 | `frmtrm/bfefrmtrm`로 재작성치 수집, 있으면 우선 | market_val_series.py:159-163,189 |

### 6.1 scale_guard 판정 요약 (scale_guard.py:101-124)

- **hard**(→ 값 무효화/N/M): ② 자산=부채+자본 항등식 위반(balance_identity) 또는
  ③ 시장최댓값 대비 배수 초과(market_relative_cap, `MARKET_MAX_NI_ANCHOR=44.26조`
  scale_guard.py:25) — market_max 없으면 자릿수 백스톱(digit_cap, 16자리).
- **soft**(→ 경고만): ① 당기/전기 배수점프(magnitude_jump) ④ 시총 대비 비율(mktcap_ratio).
  ①은 260704 전수검증서 오탐률 97.5%라 hard에서 제외 — 정보성 신호로만(scale_guard.py:105-108).
- 시장 집계는 hard 종목을 **제외**, 개별 조회는 값 유지 + 강한 경고(valuation.py:840).

---

## 7. mkt_fund_q 설계 권고 (요약)

1. **키**: `(isu_cd, fy, quarter)` — quarter ∈ {1,2,3,4}. Q4는 `연간 − 3Q누적`으로 파생.
2. **저장 필드**: 지배순이익 누적(YTD)·지배자본 잔액·전기 비교치(재작성 감지용)·`fs`(CFS/OFS)·
   `rcept_dt`(PIT 실측)·`fetched` 상태.
3. **컬럼명 명시 INSERT** — 위치 의존 금지(CLAUDE.md; 260704 mkt_fund_hist 사고).
4. **TTM 파생**: §2.4 공식으로 뷰/쿼리 단계에서 계산(원천은 누적치 저장).
5. **PIT 조인**: 가격일 D → §4.2 매핑으로 가용 (fy,quarter) 선택(연 단위 근사 금지).

---

## 관련 문서

- [[분기보고서]] · [[반기보고서]] · [[사업보고서]] — 정기보고서 3종 개요
- [[공시유형코드체계]] — reprt_code / pblntf 코드 체계
- 코드: `open_proxy_mcp/dart/client.py:1315` · `services/financial_metrics.py:212-310` ·
  `services/scale_guard.py` · `scripts/market_val_series.py`
