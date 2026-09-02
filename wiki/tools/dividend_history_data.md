---
type: tool
title: dividend_history_data
domain: data
status: 등록 완료 (260902 — tools/dividend_history_data.py)
scope: [firm, market, sector]
data_source: [DART 정기보고서 alotMatter(사업·반기·분기), WISE 섹터 매핑(wise_sector)]
related_disclosures: [사업보고서, 반기보고서, 분기보고서]
related_concepts: [배당성향, DPS, 분기배당, 중간배당]
created: 2026-09-02
updated: 2026-09-02
---

# dividend_history_data

## 한 줄 요약
DART 정기보고서 `alotMatter` **전수 수집본**(코스피 828사 × FY2020~2025)에서 **확정 배당의 시계열**을
읽는다. 한 회사의 여러 해(`firm`), 시장 전체 집계(`market`), WICS 섹터별 집계(`sector`) 셋.
[[dividend]] 가 회사 하나를 실시간으로 깊게 본다면, 이쪽은 **가로·세로로 넓게** 본다.

## 사용법
```
dividend_history_data(scope="firm", company="현대차", year_from=2020, year_to=2025)
dividend_history_data(scope="market", year_from=2020, year_to=2025)
dividend_history_data(scope="sector", sector="금융", year_from=2020, year_to=2025)
```

## 자(尺) — 이 표의 모든 숫자에 붙는 기준
- **출처**: DART 정기보고서 `alotMatter`. **확정치**이지 추정도 결정공시 예고도 아니다.
- **기간**: 사업연도(`bsns_year`). 12월 결산이 아니면 결산일 칸이 실제 결산일이다.
- **배당성향의 분모**: 공시 원문 `(연결)현금배당성향(%)` 을 그대로 싣는다 — **연결 기준이며
  우리가 계산한 값이 아니다.** 원문 표본 19,076건 전수에서 이 라벨 하나뿐이었다.
  같은 회사에서 해마다 크게 튀면 회사가 신고한 분모가 바뀐 것이다(삼성전자 FY2022 17.9% ↔
  FY2023 67.8%는 DPS·총액이 같은데도 그렇다). 원문은 [[evidence]] 로 본다.
- **빈칸**: `확정` / `무배당` / `항목없음` / `보고서없음` 을 가른다. **0 으로 메우지 않는다.**
- **주식 종류**: `보통` / `우선` / `종류` / `미구분` 네 갈래. `종류` 는 상환주·전환주·무의결권주·
  트래킹스톡이다 — 260902 이전에는 이것들이 우선주 통에 섞여 있었다(821행).
- **분기**: 누적 차분(3분기 누계 − 반기 누계). 앞 원장이 없으면 `미산출`.

## 못 하는 것 — 먼저 밝힌다
- **보통/우선 배당총액 배분값을 내지 않는다.** 종류별 발행주식수가 서식에 없어 검산이 57.2%만
  맞았다. 신고총액 하나만 낸다.
- **한 종목의 우선주가 여러 종류(우·2우B·3우B)여도 「우선주」 한 줄로 나온다.** 종목별로
  갈라야 하는 실무에서는 원문을 함께 본다.
- **정정 여부 칸이 없다.** 접수번호가 사업연도보다 뒤인 줄이 정정본일 수 있다.

## 관련
[[dividend]] · [[dividend_screener]] · [[price_multiple_data]] · [[evidence]]
