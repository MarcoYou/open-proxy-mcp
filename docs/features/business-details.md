# 사업의 내용

<!-- documentation-contract: business_details fields=segments,sites,utilization,rnd,backlog,customers,raw_materials,product_pricing,financial_ops,financial_soundness,investment_property -->

**정기보고서의 "II. 사업의 내용"을 통째로 읽어줍니다.** 사업부문별 매출·이익부터 생산설비·가동률·연구개발·수주잔고·주요 고객, 원재료·투입원가와 제품가격 추이까지 — 수십 페이지 서술형 섹션에서 필요한 소절만 골라 원문 그대로 가져옵니다.

## 무엇을 답하나

- **사업부문별 매출·영업이익**(segments) — SOTP·부문 수익성 분석의 1차 소스. 표준 표는 구조화해서, 서식이 특이한 회사는 원문 표를 그대로 보여줍니다.
- **사업장·생산설비**(sites), **생산실적·가동률**(utilization), **연구개발**(rnd), **수주현황**(backlog), **주요 고객·매출처**(customers).
- **원재료·투입원가**(raw_materials) — 주요 원재료 구성·매입과 원재료 가격변동 추이 원문.
- **제품·서비스 가격 추이**(product_pricing) — 판매가격·ASP·가격변동 원인 원문.
- **금융·REIT 전용 트랙** — 금융사는 부문표 대신 영업의 현황·재무건전성(K-ICS·순자본비율), REIT·보험은 투자부동산 내역(임대율·공실)을 읽습니다. 업종코드(KSIC)로 자동 판별합니다.
- 사업보고서뿐 아니라 **분기·반기보고서도 지원** — 기본값은 가장 최신 제출분입니다.
- 핵심 설계: 회사마다 단위·서식이 달라(가동률 %/시간/톤) 도구가 값을 단정하지 않고 **해당 소절 원문을 마크다운으로 반환**, 읽는 AI가 원문을 보고 값을 추출합니다. 출처: DART 정기보고서 원문. 상세 → [business_details](../../wiki/tools/business_details.md).

## 이렇게 물어보세요

> "에코프로비엠 생산능력이랑 가동률 어떻게 돼?"
>
> "HD한국조선해양 수주잔고 얼마나 쌓여 있어?"
>
> "삼성전자 사업부문별 매출이랑 영업이익 나눠서 보여줘"
>
> "LG화학 원재료 가격이랑 투입원가 변동 알려줘"
>
> "삼성전자 주요 제품 가격 추이와 변동 원인 보여줘"

## 함께 보면 좋은 기능

- [재무지표](financials.md) — 전사 재무는 이쪽, 부문·생산·수주 구조는 사업의 내용
- [잠정실적 속보](provisional-earnings.md) — 분기 확정 전 잠정 숫자
- [밸류에이션](valuation.md) — 부문 이익이 SOTP 배수의 입력
- [자산주 스크리닝](asset-holdings.md) — 토지·투자부동산·지분증권 원가vs공정가치, 시총 대비 NAV는 이쪽
