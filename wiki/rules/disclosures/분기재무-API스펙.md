---
type: reference
title: 분기재무-API스펙
tags: [dart-api, financial-statement, quarterly, timeseries, ttm, pit]
source: DART OpenAPI (fnlttSinglAcntAll)
related: [분기보고서, 반기보고서, 사업보고서, 공시유형코드체계]
purpose: DART 분기 재무 API 스펙 — TTM·PIT 계산에 필요한 필드 의미와 공시 타이밍
updated: 2026-07-06
---

# 분기 재무 API 스펙 (fnlttSinglAcntAll)

> DART 분기 재무 API의 정밀 스펙. TTM(직전 4분기 합산) 계산과 look-ahead 방지(PIT)를 위한
> 필드 의미·공시 타이밍이 핵심.
>
> 관련: [[분기보고서]] · [[반기보고서]] · [[사업보고서]] · [[공시유형코드체계]]

---

## 1. 엔드포인트: `fnlttSinglAcntAll.json`

DART "단일회사 전체 재무제표" — BS/IS/CIS/CF/SCE를 한 응답(`list`)에 준다.

### 1.1 파라미터

| 파라미터 | 값 | 의미 |
|---|---|---|
| `corp_code` | 8자리 | DART 기업코드 |
| `bsns_year` | "YYYY" | 사업연도 (예: "2024") |
| `reprt_code` | 4종(아래) | 보고서 구분 |
| `fs_div` | `CFS`/`OFS` | 연결(default)/별도 |

### 1.2 reprt_code 4종 (분기 커버리지)

| reprt_code | 보고서 | 누적 구간 | 결산(12월법인) |
|---|---|---|---|
| `11013` | 1분기보고서 | 3개월 (1Q) | 3/31 |
| `11012` | 반기보고서 | 6개월 (1H) | 6/30 |
| `11014` | 3분기보고서 | 9개월 (3Q) | 9/30 |
| `11011` | 사업보고서 | 12개월 (연간=Q4 포함) | 12/31 |

> **Q4(4분기 standalone)는 별도 보고서가 없다.** 사업보고서(11011)는 연간 누적이므로
> `Q4 = 연간(11011) − 3Q누적(11014)`로 파생 계산해야 한다.

---

## 2. 누적(YTD) vs 당기(standalone) vs 잔액 — TTM의 핵심

DART IS 응답은 **당기 3개월(standalone)**과 **당기 누적(YTD)**을 별도 필드로 준다.
이 구분을 틀리면 TTM이 통째로 어긋난다.

### 2.1 필드 의미

| sj_div | 필드 | 의미 | 기간성 |
|---|---|---|---|
| IS/CIS (손익) | `thstrm_amount` | **당기 3개월(standalone)** | 분기별 3개월 |
| IS/CIS (손익) | `thstrm_add_amount` | **당기 누적(YTD)** | 보고시점까지 누적 |
| BS (재무상태) | `thstrm_amount` | **기말 잔액** | 시점(stock), 기간 무관 |
| 전기 비교 | `frmtrm_amount` | 전기(직전연도 동보고서) 값 | — |
| 전전기 비교 | `bfefrmtrm_amount` | 전전기 값 | — |

### 2.2 결정적 예외 — 1분기·사업보고서는 `thstrm_add`가 빈다

누적값을 얻는 규칙은 이렇게 된다:

```
누적값 = thstrm_add_amount 우선; 없으면(None/공란) thstrm_amount
```

- **1분기(11013)**: 3개월 = 누적이라 `thstrm_add`가 비어 → `thstrm`이 곧 누적.
- **사업보고서(11011)**: 연간 = 누적이라 `thstrm_add`가 비어 → `thstrm`이 곧 누적.
- **반기(11012)·3분기(11014)**: `thstrm_amount`=당기 3개월, `thstrm_add_amount`=누적(6·9개월).
  → 누적을 쓰려면 반드시 `thstrm_add_amount`를 읽어야 한다.

> **함정**: 반기/3분기에서 `thstrm_amount`만 읽으면 "그 분기 3개월치"만 잡혀 YTD가 아니다.
> 이 누적 규칙이 필요한 건 IS/CIS뿐이고, BS(잔액)·CF(응답 자체가 누적)는 `thstrm_amount`를
> 그대로 쓴다.

### 2.3 BS는 기간 무관 잔액

BS는 항상 잔액이라 `thstrm_amount`를 그대로 읽는다. 자본·자산 등은 특정 시점의 stock 값이므로
누적 개념이 없다.

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
귀속 값은 **IFRS `account_id` 정확일치(==)**로 잡는다.

| 지표 | 1순위 account_id | 폴백 account_id | sj_div |
|---|---|---|---|
| 지배순이익 | `ifrs-full_ProfitLossAttributableToOwnersOfParent` | `ifrs-full_ProfitLoss` | `("CIS","IS")` |
| 지배자본 | `ifrs-full_EquityAttributableToOwnersOfParent` | `ifrs-full_Equity` | `("BS",)` |

- **순이익은 CIS 우선, IS 폴백** — 회사에 따라 CIS(포괄손익)·IS(손익) 어느 쪽에도 실린다.
- **자본은 BS에서만.**
- `account_id`는 **접두어가 겹치는 쌍**이 있어 substring(`in`) 매칭이 다른 계정을 잡는다 —
  `"ifrs-full_Liabilities"`는 `"ifrs-full_LiabilitiesIncludedInDisposalGroups…"`의 접두어다.

### 3.1 frmtrm/bfefrmtrm로 3개년 한 콜 확보

같은 응답 안에 전기·전전기가 함께 온다: `frmtrm_amount`=전년, `bfefrmtrm_amount`=전전년.
→ **1콜로 당해+전년+전전년 3개년** 확보 가능.

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

- 연 단위 근사(`4월 이후면 전년 FY, 아니면 전전년 FY`)로는 연간 재무까지만 막을 수 있다.
  분기 재무의 look-ahead를 막으려면 위 표처럼 분기 해상도가 필요하다.
- 실제 접수일이 필요하면 `list.json`의 `rcept_dt`로 종목별 실제 공시일을 확인해 근사 대신
  실측 PIT를 쓸 수 있다(기한보다 일찍 내는 종목 다수).

---

## 5. 콜 budget

| 단위 | 콜 수 | 비고 |
|---|---|---|
| 종목·분기 1건 (CFS 성공) | **1콜** | |
| 종목·분기 1건 (CFS 부재→OFS 폴백) | **2콜** | |
| 종목·연 (4 reprt 전체) | **4~8콜** | 11013+11012+11014+11011 |
| 백필: 종목 × 연 × 4reprt | N×Y×4 | 최소, OFS 폴백 시 최대 ×2 |

한 응답에 전기·전전기가 함께 오므로(§3.1) 연간(11011)은 종목당 1콜로 3개년을 덮을 수 있다.

---

## 6. 엣지 / 함정

| # | 케이스 | 증상 | 처리 |
|---|---|---|---|
| 1 | **[013] 데이터없음** | DartClientError `[013]` | 빈 리스트로 흡수(예외 재raise 안 함) |
| 2 | **CFS 부재(별도만)** | CFS 응답 빈 rows | OFS로 재조회 폴백 |
| 3 | **스케일오류(100만배)** | XBRL 단위 미적용 부풀림 (소프트센 032680 FY2022, 매출 73조×10^6) | scale_guard hard tier → ni/eq 무효화 |
| 4 | **비12월 결산** | 결산월≠12월 → PIT 매핑 어긋남 | 종목별 결산월 확인해 §4.2 표를 결산월 기준으로 shift |
| 5 | **재작성(restated)** | 당해 XBRL 오류를 다음해 보고서 전기란이 정정 | `frmtrm/bfefrmtrm`로 재작성치 수집, 있으면 우선 |

### 6.1 scale_guard 판정 요약

- **hard**(→ 값 무효화/N/M): ② 자산=부채+자본 항등식 위반(balance_identity) 또는
  ③ 시장최댓값 대비 배수 초과(market_relative_cap) — market_max 없으면 자릿수 백스톱(digit_cap, 16자리).
- **soft**(→ 경고만): ① 당기/전기 배수점프(magnitude_jump) ④ 시총 대비 비율(mktcap_ratio).
  ①은 정상 종목에서도 자주 켜져 hard에서 제외 — 정보성 신호로만 쓴다.
- 시장 집계는 hard 종목을 **제외**, 개별 조회는 값 유지 + 강한 경고.

---

## 관련 문서

- [[분기보고서]] · [[반기보고서]] · [[사업보고서]] — 정기보고서 3종 개요
- [[공시유형코드체계]] — reprt_code / pblntf 코드 체계
