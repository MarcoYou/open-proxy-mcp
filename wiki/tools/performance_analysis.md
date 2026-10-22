---
type: tool
title: performance_analysis
domain: data
status: 등록 완료 (261022 — tools/performance_analysis.py)
scope: [summary, contribution, attribution, series]
data_source: [일별 시세 저장분(공표 전일대비 포함), 주간 시세 저장분, 주간 시세 보관본(DB 밖 서빙본 — 콜드 서빙), 기준가 재설정 사건 원장, KRX 공식 지수 종가, ECOS 국고채 1년 금리, 업종분류 구간, 배당 결정공시 원장]
related_disclosures: []
related_concepts: [시가총액, 배당수익률, 단위-표기-규약]
created: 2026-10-22
updated: 2026-10-23
---

# performance_analysis

## 한 줄 요약
종목·업종·순위·바스켓·**블렌드**(여러 구성요소를 비중으로 섞은 것)와 **공식 지수 자체**(코스피·코스닥 등)의 수익률을 **부를 때 계산**한다 — 누적·연율 수익률, 변동성·하방 변동성·최대 낙폭, 샤프·소르티노,
벤치마크 대비 초과수익·베타·알파·추적오차·정보비율, 종목·업종·구성요소별 **기여 분해**, 업종 **Brinson-Fachler 분해**(배분·선택·상호작용), 누적 시계열.
가격 수익률 또는 **세전** 총수익(배당 재투자). 새 표를 만들지 않고 저장된 시세·지수·금리·배당·업종 구간을 읽을 때 계산한다(DART·KRX·ECOS 0콜, 쓰기 0).

전제 개념: [[시가총액]](상장주식수 × 종가) · [[배당수익률]] · [[단위-표기-규약]](`_pct`·`_krw`). 시세 시계열 그 자체는 [[trading_data]], 배수는 [[price_multiple_data]], 배당 이력은 [[dividend_data]].

## 사용법
```
performance_analysis(target="반도체 업종")                                          # 최근 1년 수익·위험 + 코스피 대비(기본)
performance_analysis(target="005930:60, 000660:40", basis="total")                  # 바스켓(종목:비중%) 세전 총수익
performance_analysis(target="반도체 업종=60; 대분류 금융=40", rebalance="quarterly")   # 블렌드(구성요소=비중%) + 분기 리밸런스
performance_analysis(target="반도체 업종", scope="contribution", group_by="종목")      # 종목별 기여(합 = 누적 수익률)
performance_analysis(target="코스피 시총 상위 50", scope="attribution")              # 업종 배분·선택·상호작용 (계산 벤치마크 「코스피 전체」)
performance_analysis(target="반도체 업종", benchmark="코스피200", since="20250102")    # 공식 지수 이름 + 시작일
performance_analysis(target="코스피")                                               # 공식 지수 자체: 1년 수익률·변동성·최대낙폭·샤프 + 시작·끝 지수 포인트
performance_analysis(target="지수:코스피 200", benchmark="코스피")                    # 「지수:」 접두사 + 공식 지수 이름, 다른 지수를 벤치마크로
performance_analysis(target="005930, 000660", scope="series", format="json")        # 누적 시계열 전량
```

## 입력 인자
| 인자 | 타입 | 필수 | 설명 | 기본값 |
|---|---|---|---|---|
| target | str | ✅ | 대상. **공식 지수 자체**(단일 문구 「코스피」「코스닥」 또는 「지수:」 뒤 `krx_idx_series` 이름 — 아래 「지수 target」) 이거나 `trading_data(scope="universe")` 와 같은 문구 — 업종(「반도체 업종」「중분류 반도체와반도체장비」「대분류 IT」「코스피 IT 섹터 상위 10」) · 순위·지수(「코스피 시총 상위 50」「코스피200」) · 나열(「005930, 000660」) · **바스켓**(「005930:40, 000660:60」 — 종목코드:비중%) · **블렌드**(구성요소를 `;` 로 나누고 `=비중%`: 「반도체 업종=60; 대분류 금융=40」). 블렌드·바스켓 비중 합이 100(±0.5)이 아니면 **맞춰 채우지 않고** `invalid` 로 되묻는다. 구성요소는 최대 10개 | — |
| weights | str | no | 구성요소 안 가중. `시총`(시작일 시총) / `동일`. 바스켓은 적은 비중이 이긴다 | "시총" |
| since | str | no | 시작일 YYYYMMDD(2026-01-05 꼴도 받는다). 휴장이면 앞 거래일 종가가 기준 | 끝 기준일의 1년 전 |
| as_of | str | no | 끝 기준일. 휴장이면 앞 거래일로 풀고 `period.requested_end` 에 요청값을 남긴다 | 보관된 최신 거래일 |
| benchmark | str | no | 비우면 시작일 비중이 큰 시장의 공식 지수(코스피 또는 코스닥 — 외국주 제외 가격지수)이고 이유를 `relative.benchmark_reason` 에 적는다. 공식 지수 이름(「코스피200」「KRX 300」「코스닥 전기전자」) 또는 유니버스 문구(「코스피 전체」「코스닥 전체」「대분류 IT」) | "" |
| scope | str | no | `summary`(수익·위험·상대) / `contribution`(구성요소·종목·업종 기여) / `attribution`(업종 Brinson 분해 — 계산 벤치마크만) / `series`(누적 수익률 시계열) | "summary" |
| group_by | str | no | 분해 단위 `중분류` / `대분류` / `종목` / `구성요소`. attribution 은 중분류·대분류만 | "중분류" |
| basis | str | no | `price`(가격 수익률) / `total`(세전 총수익 — 배당 재투자) | "price" |
| rebalance | str | no | `none`(시작 비중에서 표류) / `monthly` / `quarterly`(새 달·분기 첫 거래일에 목표 비중으로) | "none" |
| rf | str | no | 무위험 금리. 비우면 ECOS 국고채 1년, 숫자(연 %)를 주면 고정 금리 | "" |
| freq | str | no | `daily` / `weekly`. 비우면 자동 — 기간 전체가 일별 보관 창(약 1년) 안이면 daily, 아니면 weekly. 창 밖에서 daily 를 요구하면 `invalid` | "" |
| format | str | no | `md` / `json` | "md" |

## 출력 schema (json)
기존 도구와 같은 관례(`status`·`warnings`·`data.*`, 비율 `_pct`, 금액 `_krw`). 키는 **추가만** 하고 이름·뜻은 고정이다.
```
data:
  scope, period{start, end, requested_start, requested_end, freq, n_obs, calendar_days, annualization}
  target{label, spec, n_members, membership_asof, weights_basis, rebalance,
         components[{label, spec, weight_pct, n_members, sector_asof, classification_retro, membership_asof}],
         index{sid, src, name, kind, price_index:true, start_point, end_point,                                   # 지수 target 일 때만
               resolution{freq("daily"|"weekly"), n_obs, ann_factor, nominal_ann_factor(252|52)}}}
  metrics{return_cum_pct, return_ann_pct(365일 미만이면 null), vol_ann_pct, downside_vol_ann_pct, mdd_pct, mdd_from, mdd_to, sharpe, sortino}
  relative{benchmark, benchmark_source("official"|"computed"), benchmark_series{src, name, kind}, benchmark_reason, benchmark_return_cum_pct,
           excess_cum_pct, excess_ann_pct, beta, alpha_ann_pct, corr, tracking_error_pct, info_ratio, n_obs_aligned,
           period{start, end, n_obs}, target_return_cum_pct,                                                        # 아래 값은 모두 이 겹친 기간 기준
           benchmark_vol_ann_pct, benchmark_mdd_pct, benchmark_mdd_from, benchmark_mdd_to, benchmark_sharpe,
           benchmark_start_point, benchmark_end_point, benchmark_points_reason}   # 벤치마크를 못 읽거나 지수 target 이 자기 비교면 null
  contributions[{ticker, name, component, sector, weight_start_pct, weight_end_pct, return_pct, contribution_pct}]    # scope=contribution
  contributions_by                                                                                                   # 종목/구성요소/중분류/대분류
  attribution{group_by, linking:"carino", by_group[{code, name, w_p_pct, w_b_pct, r_p_pct, r_b_pct, allocation_pct, selection_pct, interaction_pct}],
              allocation_pct, selection_pct, interaction_pct, active_pct, residual_pct, w_basis, r_basis,          # w_p_pct·w_b_pct 는 소수 넷째 자리
              classification{first_snapshot, retro, taxonomy_break}}                                                # scope=attribution
  series[{dd, cum_pct, bench_cum_pct, bench_point, point}]                                                           # scope=series (point 는 지수 target 일 때만)
  coverage{price_adjusted:true, adj_events_used, tick_rounding_skipped, cmp_missing_rows, excluded_flags[], delisted_in_period[{ticker,last_dd}],
           not_priced_at_start[], cold{used, seam, reason},
           dividend{basis, gross:true, covered_weight_pct, pending, preferred_price_only, ex_date_basis("daily_calendar"|"weekday_approx")}}
  rf{series, source, avg_pct, asof, convention:"ACT/365", missing}
  method, sources[], warning_codes[]
```
`warning_codes[]` 가 기계가 읽는 경고이고 사람이 읽는 문장은 `warnings[]` 에 그대로 둔다(응답 맨 위에도 같은 `warning_codes`).

상태: `ok` / `invalid`(되물음·한도 초과) / `not_found` / `no_data` / `db_error`(일시 — 재시도 유효) / `db_unconfigured`(이 서버에 DB 미연결 — 재시도해도 안 됨).
무위험 금리·배당·딱지·콜드·벤치마크 읽기 실패는 `db_error` 로 올리지 않고 **그 지표만 비운 뒤 경고**한다.

## 계산식 (`services/perf_math.py` — 순수 함수, 표준 라이브러리만)
- **종목 구간 수익률** — 종가를 그대로 나누지 않는다(DATA-PX2·PX4·PX5).
  · 일별: `r = cmp / (close − cmp)`. `cmp` 는 KRX 공표 전일대비라 기준가가 분할·권리락을 이미 반영한다. 공표값이 없는 옛 행은 `close / (전일 종가 × 그날 사건 계수) − 1`(`cmp_missing_rows`).
    호가단위 맞춤으로 기준가가 올라간 날은 공표 기준가 대신 전일 종가를 기준으로 한다.
  · 주간: `r = close_t / (close_{t−1} × Π base_price/prev_close) − 1`. 곱하는 것은 구간 (t−1, t] 안 **사건 원값**이다 — 반올림해 저장한 `adj_factor` 를 곱하면 어긋난다. 호가단위 맞춤 사건은 곱하지 않고 센다.
- **블렌딩** — 보유 비중이 구간마다 표류한다(`W_t = W_{t−1}(1+r)/(1+r_p)`). `rebalance` 구간 앞에서만 목표 비중으로 되돌린다(시총 가중은 그 날 시총 비중). 상장폐지로 가격이 끊긴 종목은 마지막 값에 현금으로 묶이고 리밸런스에서도 건드리지 않는다.
- **누적·연율** — `R = Π(1+r)−1`, 연율 `(1+R)^(365.25/일수)−1`(365일 미만이면 비움), 연간 관측 수 `A = n × 365.25 / 일수`(응답의 `period.annualization`).
- **위험** — 변동성 = 표본 표준편차(ddof=1)×√A, 하방 변동성 = √mean(min(r−rf,0)²)×√A, MDD 는 부(wealth) 곡선 기준.
- **무위험 금리** — 연율 %를 구간 달력일/365 로 단리(ACT/365), **구간 시작 이전의 마지막 관측**을 쓴다(미래 정보 금지). 휴일·지연은 앞 값을 이어 쓰고 `rf.asof` 로 밝힌다.
- **상대 지표** — 샤프 = mean(r−rf)/sd(r−rf)×√A · 소르티노 = mean(r−rf)×A / 하방 변동성 · 베타 = 초과수익끼리 OLS · 알파(연율) = (mean ex_p − β·mean ex_b)×A · 추적오차 = sd(r_p−r_b)×√A · 정보비율 = mean(r_p−r_b)×A / 추적오차. 벤치마크와 같은 날짜만 맞대어 계산한다.
- **기여** — 기간별 `c = W_{t−1}·r` 을 Carino 로 이어 `Σ c·k_t/K` (`k = ln(1+r)/r`) — 종목 기여의 합이 누적 수익률과 정확히 같다.
- **Brinson-Fachler** — 배분 `(wP−wB)(rB_j−RB)` · 선택 `wB(rP_j−rB_j)` · 상호작용 `(wP−wB)(rP_j−rB_j)`, 합 = `rP − rB`. 업종은 그 기간 시작일을 덮는 분류 구간(`valid_from ≤ 날 < valid_to`). 다기간은 **초과 Carino** 로 이어 합이 누적 초과수익률과 같다(`residual_pct` 는 반올림 오차).
- **총수익(세전)** — 배당락일 구간의 `r` 에 `DPS / 조정 기준가` 를 더한다(현금배당락은 기준가를 조정하지 않는다). **배당락일 = 기준일 이하 마지막 거래일의 앞 거래일**이다 — 12-31 처럼 기준일이 휴장이면 12-30 의 앞인 12-29 (「기준일 직전 거래일」로 쓰면 연말 결산배당에서 하루 틀린다, DATA-DIV9).
  보통주 DPS 만 쓰고 정정 공시는 가장 늦은 접수만, 결의일이 끝 기준일보다 늦은 배당은 아직 몰랐던 정보라 넣지 않는다(DATA-PIT9). 우선주와 배당 원장 범위 밖(수집 시작 이전 연도)은 가격 수익률로 둔다.

## 지수 target
- **고르는 법** — 블렌드·바스켓이 아닌 단일 문구이고 (a) 「코스피」「코스닥」(`kospi`·`kosdaq` 영문, 앞뒤 공백 무시)이거나 (b) 「지수:」 접두사 뒤 이름이 `krx_idx_series.name` 과 정규화(공백·`·`·`-` 제거, 영문 별칭) 일치할 때만 지수 자체를 대상으로 한다.
  「코스피200」「코스피 200」「코스닥150」「코스닥 150」「코스피 전체」와 블렌드 안의 지수 이름은 종전대로 **구성 종목 바스켓**이다(정규화가 띄어쓰기를 지우므로 정규화 일치만으로 고르면 기존 바스켓 호출이 바뀐다). 같은 이름이 양쪽 시장에 있으면(「지수:전기전자」) 되묻는다.
- **DB 에 있는 계열만** — `krx_idx_series.in_db=true` 만 계산한다. `in_db=false` 이름은 `invalid` 와 「이 지수는 DB 에 일별 종가가 없습니다」 안내를 내고 값을 만들지 않는다.
- **해상도** — 기존 `plan.freq` 를 따른다. 시작일이 일별 보관 창 안이면 지수 일별 행만, 밖이면 종목 주간 시세와 같은 주 정의(주 닫는 날, DATA-PX6)의 점만 쓰며(끝이 아직 닫히지 않은 주면 일별 행으로 끝점을 잇는다) **한 계산 안에 일별·주간을 섞지 않는다**.
  응답 `target.index.resolution` 에 해상도·관측 수·연환산 계수(`period.annualization` 과 같은 값 — 관측 수 × 365.25 ÷ 달력일수)와 표준 계수(252/52)를 적는다. 주간이면 변동성·최대낙폭이 주 종가 기준이라 그 사이 낙폭을 놓칠 수 있고 `index_weekly_resolution` 경고가 선다.
- **지표** — 누적·연율 수익률, 연율 변동성, 최대낙폭(고점·저점 날짜), 샤프(무위험 = `ecos_rate` 국고채 1년), 시작·끝 지수 포인트(`target.index.start_point`·`end_point`, 종가 = `close_x100 ÷ 100`).
- **자기 비교 금지** — `benchmark` 를 비우거나 같은 지수(같은 `sid`)를 주면 `relative` 를 내지 않고 `benchmark_same_as_target` 경고를 단다. 다른 공식 지수나 계산 벤치마크를 주면 낸다. scope=attribution·contribution 은 구성 이력이 없어 `invalid`.
- 지수 이력은 최근 3년(2023-10-20~)이라 시작일이 그 앞이면 첫 관측으로 자르고 `index_truncated` 를 단다. 지수는 가격지수라 `basis=total` 은 가격 수익률로 계산하고 `index_price_only` 를 단다.

## 벤치마크
- **공식 지수** — `krx_idx_series` 의 이름만 받는다. 열쇠가 (엔드포인트, 이름)이라 20개 업종 이름이 코스피·코스닥 양쪽에 같다 — 모호하면 **추측하지 않고 후보를 보여 되묻는다**. 「코스피200」·「코스피 200」 같은 띄어쓰기와 「코스닥 전기전자」처럼 시장 이름이 앞에 붙은 말은 풀어 준다.
  공식 코스피·코스닥은 외국주를 뺀 가격지수라 같은 이름의 계산 벤치마크와 다를 수 있다 — `benchmark_source` 로 구분한다. 공식 업종 지수(KRX 반도체 등)는 WICS 업종과 구성이 다르다(`official_vs_wics`).
- **계산 벤치마크** — 「코스피 전체」·「코스닥 전체」·「전체」는 **우선주를 뺀** 보통주 시총 가중이고(2026-10-23 — 업종 구성·시총 순위 유니버스 「코스피 전체」와 같은 모집단) 전일 기준가 시총으로 매일 비중을 잡는다: `r_B = Σ cmp·주식수 / Σ (close−cmp)·주식수`. 일별은 DB 쪽 한 질의로 (날짜, 업종)별 합만 받는다(약 1.4만 행). 주간은 시장 전 종목 행을 읽어 같은 식으로 계산하고 행 상한(20만)을 받는다.
  그 밖의 유니버스 문구(「대분류 IT」「코스피 시총 상위 50」)는 시작일 구성을 고정한 시총 가중(리밸런스 없음). 계산 벤치마크는 배당을 넣지 않는다.
- **벤치마크 블록** — 대상과 같은 위험지표 칸(벤치마크 연율 변동성·최대낙폭과 날짜·샤프)과 시작·끝 지수 포인트를 싣는다. 공식 지수가 아닌 계산 벤치마크는 지수 종가가 없어 포인트 칸이 null 이고 `benchmark_points_reason` 에 이유를 적는다. `scope=series` 는 `bench_point`(벤치마크 종가)를 함께 주고 md 발췌 표에도 싣는다.
- **겹친 기간** — 벤치마크 이력이 대상보다 짧으면(3년 밖 시작일) `relative` 의 대상 누적(`target_return_cum_pct`)·벤치마크 누적·초과수익·베타 등은 모두 **같은 겹친 기간**(`relative.period`)의 값이다. `metrics`(대상 전체 기간)와 기간이 다르며 md 는 표의 칸마다 기간을 적는다.
  시계열의 `bench_cum_pct` 는 벤치마크가 시작하는 날을 0% 로 세고 그 앞 행은 null 이다.
- **Brinson 분해는 계산 벤치마크만** 된다 — 공식 지수는 구성 이력이 없어 `invalid` 와 안내를 낸다.
- 공식 지수·금리는 DB 에 최근 3년뿐이라(보관본은 이 도구가 읽지 않는다) 3년보다 앞 구간은 겹치는 구간만 상대 지표를 내고 `benchmark_truncated`·`rf_unavailable` 을 단다.

## 경고 코드 (`warning_codes`)
| 코드 | 뜻 |
|---|---|
| `sector_taxonomy_break` | 2019-06-28 이전 날짜에 업종 분류를 썼다 — WICS 가 2018-06~2019-06 에 체계를 개편(미디어와엔터테인먼트 신설)해 앞뒤 업종은 구성이 다르다(DATA-SEC3) |
| `sector_retroactive` | 가장 이른 분류 스냅샷보다 앞선 날에 그 스냅샷을 소급 적용했다(DATA-SEC2) |
| `survivorship` | 업종 원천이 지수 구성종목이라 탈락·상장폐지 종목이 빠질 수 있다. 구성은 시작일 시점으로 고정했다 |
| `delisted_cash` | 기간 중 마지막 가격 관측이 끝 기준일 앞인 종목은 그 뒤 0% 현금. 청산·합병 대가는 원천이 없다 |
| `cold_coverage` | 3년 밖을 보관본에서 이었거나, 보관본을 못 읽어 시작을 DB 하한으로 잘랐다(부분 곡선 없음 — DATA-COLD1·COLD2). 시작일이 DB 주간 하한 안이면 보관본을 읽지 않으므로 서지 않는다(`coverage.cold.used`=false). 경고 문구의 기간은 늘 시작 ≤ 끝 순서 |
| `index_truncated` | 시작일이 공식 지수의 DB 이력(첫 관측) 앞이라 첫 관측으로 잘랐다 |
| `index_weekly_resolution` | 지수 target 이 주간 계산이다 — 주 종가 기준이라 그 사이 낙폭을 놓칠 수 있다 |
| `index_price_only` | 지수 target 에 basis=total 을 줬지만 공식 지수는 가격지수라 가격 수익률로 계산했다 |
| `benchmark_same_as_target` | 지수 target 의 benchmark 가 비었거나 같은 지수라 자기 비교가 되므로 상대 지표를 내지 않았다 |
| `benchmark_truncated` | 벤치마크·금리가 3년 밖에 없어 겹치는 구간만으로 상대 지표를 냈다 |
| `gross_total_return` | 세전 총수익이다 — 배당소득세 15.4% 를 빼지 않았으니 KRX NTR(세후)과 직접 비교하지 않는다 |
| `dividend_pending` | 결의일이 끝 기준일보다 늦어 넣지 않은 배당이 있다(DATA-PIT9) |
| `dividend_coverage` | 배당 원장이 온전하지 않은 연도·우선주·읽기 실패라 가격 수익률로 둔 비중이 있다 |
| `rf_unavailable` | 무위험 금리를 읽지 못했거나 일부 구간만 있어 샤프·소르티노·알파·하방 변동성을 비웠다 |
| `official_vs_wics` | 공식 업종 지수와 WICS 업종은 구성이 다르다 |
| `membership_lookahead` | 시총 순위·지수 문구는 보관된 최신 거래일 구성이다(구성 이력이 없다) |
| `benchmark_price_only` | basis=total 인데 벤치마크는 가격 수익률이다 |
| `benchmark_unavailable` | 벤치마크를 읽지 못해 상대 지표를 비웠다 |
| `row_limit` · `rss_guard` | 한도(종목 수·시세 행·메모리)를 넘어 거절했다 |

## 데이터 출처와 콜 비용
| 읽는 것 | 출처 | 비고 |
|---|---|---|
| 거래일 달력 | 일별 달력 `krx_day` | 1 왕복 — 일별 창·휴장 풀이·배당락일 정확 달력 |
| 대상 구성 | `universe.list_universe`(`trading_data` 와 같은 해석) | 업종은 시작일 시세 단면 + 분류 스냅샷 |
| 대상 시세 | 일별 `krx_px`(종목 번호 `tid = ANY(...)`) / 주간 `krx_weekly` + 하한 이전은 콜드 `krx_weekly_hist` | 주간은 배수 도구와 같은 이음매(`serving_seam`) |
| 사건 | `krx_adj_events`(원값) · 딱지 `krx_stock_flags` | |
| 업종 구간 | `wise_sector_iv` · 스냅샷 날짜 `wise_sector_snap` | 뷰 `wise_sector` 의 max 는 쓰지 않는다 |
| 지수 | `krx_idx_series` · `krx_idx` | 최근 3년 |
| 금리 | `ecos_rate` 계열 1 | 서버 계정이 읽으려면 SELECT 권한이 있어야 한다 |
| 배당 | `div_payment` · `div_payment_scope` | 보통주 DPS |

DART·KRX·ECOS 호출은 **0**이다. 한 가지 예외: 종목 이름표(`universe._names_by_ticker`)가 DART 회사 원장 캐시를 쓰므로 캐시가 비어 있으면 corpCode 받기 1콜이 날 수 있다.
DB 왕복은 보통 8~12회(기준일·달력 1, 구성 1~2, 시세 1~2, 사건 1, 벤치마크 1, 금리 1, 배당 2, 업종 구간 2)이고 호출 안에서 기존 동시 읽기 상한 안에서 돈다.

## 가드
- 구성 종목 상한: 일별 500, 주간 300. 3년 밖 시세는 종목마다 보관본을 읽으므로 300개까지.
- 파이썬으로 가져오는 시세 행은 20만 이하(`OPM_PA_MAX_ROWS`) — 읽기 **전에** 어림해 넘으면 `invalid` 와 함께 기간을 줄이거나 대상을 좁히라고 안내한다.
- 프로세스 메모리(RSS)가 콜드 서빙과 같은 문턱(기본 1,700 MB)을 넘으면 무거운 읽기 전에 거절한다.
- 계산은 표준 라이브러리(`math`·`statistics`)만 쓴다(numpy 는 실행 의존성이 아니다).

## 알려진 한계
- 구성은 시작일 시점이지만 **시총 순위·지수 문구는 이 서버가 보관한 최신 거래일의 구성**이다. 시작일에 없던 종목은 가격이 없어 빠지지만, 그 뒤 탈락한 종목이 과거에 있었는지는 모른다 — 시작일 시점 구성이 필요하면 종목 나열·업종 문구를 쓴다.
- 나열(「005930, 000660」)은 `trading_data` 유니버스와 같은 해석이라 보관된 최신 거래일에 시세가 있는 종목만 잡힌다 — 이미 상장폐지된 종목을 넣으려면 바스켓(「005930:50, 000660:50」)으로 적는다(코드 6자).
- 업종 구간은 DB 에 3년 창만 있다. 그 앞의 업종은 가장 이른 스냅샷을 소급한 값이다.
- 3년 밖의 공식 지수·금리는 이 도구가 읽지 않는다(서빙 목록에 없다) — 3년 밖에서는 겹치는 구간만 상대 지표를 낸다. 지수 target 도 같아 지수 이력(2023-10-20~) 앞은 첫 관측으로 자른다.
- 지수 target 은 DB 에 일별 종가가 있는 계열(`in_db`)만 된다. 총수익지수는 DB 에 없어 `basis=total` 을 적용하지 못한다.
- 일별 창 밖의 배당락일은 평일 달력에 12-31 휴장만 넣어 근사한다(설·추석 같은 평일 휴장은 반영하지 못한다).
- 상장폐지·합병·분할 대가와 청산 분배는 원천이 없어 0% 현금으로 둔다.
- 총수익은 세전이다. 공식 지수는 가격지수라 basis=total 의 초과수익은 배당만큼 커 보인다.
- 계산 코스닥 시장 수익률은 공식 지수와 다를 수 있다(외국주 제외 차이 — 계산 벤치마크는 우선주를 뺀 보통주 시총 가중이라 공식 코스피·코스닥 지수와 모집단이 다를 수 있다. 공식 지수의 우선주 포함 여부는 확인하지 못했다) — 같은 이름으로 섞지 않고 `benchmark_source` 를 붙인다.

## 변경 이력
- 2026-10-23: **우선주를 뺀다** — 우선주는 업종·성과 분석 대상이 아니다. ① 업종 대상·블렌드 구성요소(「반도체 업종」「대분류 금융=40」 등 `list_universe` 의 업종 문구)의 구성 종목에서 우선주가 빠진다(구성요소 안 비중은 보통주끼리 합 1). ② 계산 벤치마크 「코스피 전체」·「코스닥 전체」·「전체」가 우선주를 뺀 보통주 시총 가중이 된다(판정: 업종분류 구간 출처가 우선주 이음이거나 분류 없는 행의 코드 끝자리 규칙). attribution 의 기본 벤치마크(대상 시장의 「코스피 전체」/「코스닥 전체」)도 같이 바뀌어 대상 업종 구성과 벤치마크 업종 비중의 모집단이 보통주로 맞는다. 응답 모양은 그대로이고 해당 호출의 수익률·초과수익·Brinson 값이 의도된 만큼 달라진다. 종목 코드·바스켓으로 직접 적은 우선주, 시총 순위·지수 구성종목 문구, 공식 지수 대상은 그대로다.
- 2026-10-22: **공식 지수 target + 벤치마크 블록 보강 + 버그 3건(B1·B2·B3)** — ① `target` 이 「코스피」「코스닥」 또는 「지수:<이름>」이면 구성 종목이 아니라 지수 종가 자체를 계산한다(기존 「코스피200」 등 바스켓 표기는 그대로, 구성 종목 상한 오류가 나던 「코스피」「코스닥」 호출이 지수로 풀린다). 일별·주간 선택은 기존 `freq` 를 따르고 한 계산에 섞지 않는다.
  ② `relative` 에 벤치마크 연율 변동성·최대낙폭·샤프와 시작·끝 지수 포인트, 같은 겹친 기간의 대상 누적(`target_return_cum_pct`)과 `period` 를 더했다(키 추가만). `scope=series` 에 `bench_point`.
  ③ B1: 시작일이 DB 주간 하한 안이면 보관본(콜드)을 읽지 않는다(`cold.used`=false, `cold_coverage` 없음 — 값은 그대로). 보관본 안내 문구의 기간 순서를 시작 ≤ 끝으로. ④ B2: 벤치마크 이력이 짧으면 한 표 안에서 기간이 다른 값이 섞이지 않게 같은 겹친 기간 값을 따로 두고 md 표에 칸마다 기간을 적는다; 시계열 벤치마크 누적은 벤치마크 시작일을 0% 로 센다.
  ⑤ B3: md 업종 분해 표는 대상이 담은 업종을 상위 N 자르기로 빼지 않고 잘린 비보유 업종은 「기타」 한 줄로 합쳐 합계 줄과 맞춘다(비중·효과 소수 셋째 자리, 응답 `w_p_pct`·`w_b_pct` 소수 넷째 자리). 새 표·뷰·칸·색인 없음, 외부 호출 0, 도구 수 그대로.
- 2026-10-22: **신설** — 블렌딩·초과수익·기여 분해·위험 지표를 부를 때 계산한다(`scope=summary|contribution|attribution|series`). 새 표·뷰·칸·색인 없음, 외부 호출 0, 운영 크기 증감 0.
  서버 계정이 무위험 금리 표를 읽을 수 있게 하는 권한 변경은 별도 커밋(`ecos_rate` SELECT)과 짝이다 — 권한이 없으면 금리 지표만 비고 나머지는 그대로 나간다.

## 관련
- [[trading_data]] — 시세·시총 시계열, 유니버스 문법(`scope="universe"`)의 정본
- [[price_multiple_data]] — 배수(PER·PBR·배당수익률)
- [[dividend_data]] — 배당 이력·결정공시 원장
- [[data-pitfalls]] — DATA-PX2·PX4·PX5 · DATA-COLD1·COLD2 · DATA-SEC2·SEC3 · DATA-PIT9 · DATA-DIV4·DIV9
