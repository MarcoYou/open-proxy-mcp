---
type: tool
title: trading_data
domain: data
status: 등록 완료 (260824 — tools/trading.py)
scope: [firm, quote, market, sector]
data_source: [KRX stk/ksq_bydd_trd(일별매매정보), Supabase krx_weekly(주간 시세), Supabase krx_cap_agg(시장·섹터 시총 집계), Supabase krx_adj_events(기준가 조정), Supabase wise_sector(WICS 업종분류)]
related_disclosures: []
related_concepts: [시가총액, 상장주식수, 수정주가]
created: 2026-08-24
updated: 2026-08-24
---

# trading_data

## 한 줄 요약
가격과 **규모** 그 자체 — 종목의 주가·시가총액·상장주식수 시계열(주간, 2015-12~), 시장·섹터
시총 집계 시계열, 특정 거래일의 전체 시세(OHLC·거래량·거래대금·등락률). 배수(PER·PBR)는
[[price_multiple_data]] 가 맡는다.

## 왜 갈랐나 (260824)
한 tool(`valuation`) 이 배수와 규모를 함께 들고 있었다. 그런데 둘은 다른 질문이다 —
「PER 이 몇이냐」와 「시총이 얼마냐」는 필요한 파라미터도, 갱신 주기도, 정확도의 기준도 다르다.
한 이름 아래 두니 `scope` 하나로 다섯 가지를 가르게 됐고, 「주가를 뽑아 달라」는 요청이
배수 산출 경로를 통째로 태우고서야 종가 하나를 꺼내는 모양이 됐다.

## 사용법
```
trading_data(company="삼성전자")                             # firm: 주가·시총·주식수 주간 시계열
trading_data(company="삼성전자", since="20240101")           # 구간 지정
trading_data(company="삼성전자", scope="quote")              # 최근 거래일 전체 시세
trading_data(company="삼성전자", scope="quote", as_of="20260820")  # 그 날 OHLC·거래량·거래대금
trading_data(scope="market")                                 # KOSPI·KOSDAQ 시총 시계열
trading_data(scope="sector")                                 # WICS 하위업종 28 시총·비중
trading_data(scope="sector", scheme="wics_sector")           # WICS 대분류 10
trading_data(scope="sector", bucket="반도체와반도체장비")     # 그 섹터의 전 구간 시계열
```

## 입력 인자
| 인자 | 타입 | 필수 | 설명 | 기본값 |
|---|---|---|---|---|
| company | str | firm·quote 필수 | 회사명 / ticker(6자리) / corp_code | "" |
| scope | str | no | `firm`(종목 시계열) / `quote`(단일 거래일 전체 시세) / `market` / `sector` | "firm" |
| format | str | no | "md" / "json" — 전 구간 시계열은 json 의 `data.series` | "md" |
| as_of | str | no | quote 전용. YYYYMMDD. 비우면 최근 거래일 | "" |
| since | str | no | firm·market·sector 시계열 시작일 YYYYMMDD | "" |
| scheme | str | no | sector 전용. `wics_industry`(28) / `wics_sector`(10) | "wics_industry" |
| bucket | str | no | sector 전용. 섹터명·코드 지정 시 그 섹터의 전 구간 시계열 | "" |

## 데이터 출처와 콜 비용
| scope | 출처 | DART | KRX |
|---|---|---|---|
| firm | `krx_weekly` (+ `krx_adj_events`) | 0 | 0 |
| market · sector | `krx_cap_agg` 사전계산 | 0 | 0 |
| quote | KRX 일별매매정보 라이브 | 0 | 0~2 (오늘분은 캐시 적중 시 0) |

## 반드시 알아야 할 것 셋

### ① `close_krw` 는 수정주가가 아니다
그 날의 실제 종가다. 액면분할·병합·무상증자 시점에서 **불연속**이다. 산출물이
`price_adjusted: false` 와 구간 내 조정 이벤트 목록(`adj_events`)을 함께 싣고, 이벤트가 있으면
경고를 단다. 연속 비교가 필요하면 `mktcap_krw` 를 쓴다 — 시총은 주가×주식수라 조정에 불변이다.
[[price_multiple_data]] 의 배수도 같은 이유로 시총 기반이라 조정에 불변이다.

### ② 여기 시총과 `price_multiple_data` 의 Σ시총은 **다른 값**이다
| | 모집단 | 20260821 KOSPI |
|---|---|---|
| `trading_data` (`krx_cap_agg.cap`) | 그 날 상장된 **전 종목**(우선주 포함) | 5,713조 / 942종목 |
| `price_multiple_data` (`opm_val_market.cap`) | 배수 분모(지배순이익·지배자본)를 가진 종목만 | 5,497조 / 822사 |

**3.8% 차이**다. 저쪽은 분자·분모 모집단이 맞아야 배수가 성립하므로 그게 맞고, 이쪽은
「시장 규모가 얼마냐」의 답이라 전 종목이 맞다. 둘 다 옳은데 뜻이 다르다 — 그래서 같은 표에
스킴을 하나 더 얹지 않고 **표를 나눴다**. 한 이름이 두 뜻을 가지면 둘 중 하나는 반드시 틀리게
쓰인다.

### ③ 섹터 합 == 시장 합 (미분류를 버리지 않는다)
WICS 구성종목에 없는 종목(우선주·신규상장 등, 20260821 기준 KOSPI 시총의 3.45%·143종목)을
조용히 떨어뜨리면 섹터 합이 시장 합보다 작아지고 아무도 그 이유를 모른다. `_UNCLASSIFIED`
버킷에 남겨 항등을 유지하고, 배치가 **전 시점·전 스킴에서 그 항등을 검사**한다(어긋나면 실패).

업종분류는 2026-08 부터 관측이라 그 이전 구간은 **소급**(지금 분류를 과거에 적용)이다.
`sector_asof` 로 어느 관측을 적용했는지 밝힌다. 월 1회 관측이 쌓일수록 소급 구간이 뒤로 밀린다.

## 배치
| 스크립트 | 무엇을 | 언제 |
|---|---|---|
| `scripts/krx_cap_agg.py` | `krx_cap_agg` 재적재 (44,636행 · 8MB) | 일간 (krx_weekly 갱신 후) |
| `scripts/refresh_wics.py` | `wise_sector` 업종분류 관측 | 월 1회 ([[wics-monthly]] cron) |

## 성능 (실측 260824)
| 질의 | 사전계산 전 | 후 |
|---|---|---|
| 시장 시총 전 구간 | 3.16초 (cold, 1,342,779행 Seq Scan) | **0.05초** |
| WICS 하위업종 전 구간 | 1.02초 | **0.10초** |
| 종목 시계열 555주 | 0.52초 | 0.52초 (그대로 — `idx_krx_weekly_isu` 사용) |

요청 경로에서 3초는 260823 의 502(느린 한 경로가 워커를 소진)와 같은 형태라 사전계산으로 뺐다.

## 관련
- [[price_multiple_data]] — 배수(PER·PBR·배당수익률). 시총 정의 차이는 위 ② 참조
- [[screener]] — 조건 검색
- [[financial_metrics]] — 재무 펀더멘탈
