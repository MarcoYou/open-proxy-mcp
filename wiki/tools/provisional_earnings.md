---
type: tool
title: provisional_earnings
domain: data
scope: [영업잠정실적, 결산잠정치, 매출, 영업이익, 순이익, YoY, QoQ]
data_source: [DART search list.json I001(결산잠정치)/I002(공정공시) 발견, get_document 원문 HTML 파싱]
related_disclosures: [사업보고서, 분기보고서]
related_concepts: [영업잠정실적, 공정공시, 잠정치, 연결_별도]
related_decisions: [ksic-sector-mapping]
created: 2026-07-19
updated: 2026-08-25
---

## 한 줄

DART **영업(잠정)실적(I001 결산잠정치·I002 공정공시)** 에서 분기·연간 **잠정 매출·영업이익·순이익 + YoY/QoQ** 추출.
정기보고서 확정치([[financial_metrics]])보다 **먼저 나오는 가장 빠른 실적 신호**(분기말 며칠 뒤).

## 사용법
- `provisional_earnings(company, format="md")` — 최신 영업잠정실적(최근 6개월 내).
- 예: `provisional_earnings("삼성전자")` · `provisional_earnings("현대자동차")`.

## 왜 필요한가 (financial_metrics와 차이)
- **잠정 ≠ 확정**: financial_metrics는 정기보고서 확정치(fnlttSinglAcnt, 감사 후). 잠정실적은 **자가 공시**(감사 전, 분기말 ~7일 뒤). 확정치와 다를 수 있음.
- **속도**: 반기보고서(~45일 뒤)·분기보고서보다 훨씬 빠름. 예: 삼성전자 2026 2Q(6/30 종료) → 7/7 잠정공시.
- **정형 API 없음**: DART OpenAPI에 잠정실적 전용 엔드포인트 없음 → **공시검색(I002) + 원문파싱** 패턴([[공시유형코드체계]] I002).

## 출력 (ToolEnvelope.data)
- `headline`(재무형): `{revenue, operating_profit, net_income}.{value_krw(당기, 원 정규화), yoy_pct}` — best-effort, screener 카드용.
- `table_markdown`(**primary**): 원문 실적표 통째(colspan확장, ※잠정치·정보제공 boilerplate 제거). 당해/누계 × 당기/전기/전년동기 전체 — 호출측 AI가 읽어 값 추출.
- `kind`: `financial` | `non_financial`(자동차 판매대수·조선 수주 등 — 재무표 전부 '-'이고 도메인표만).
- `consolidated`(연결/별도) · `unit_raw`(조원/억원/백만원) · `period`(실적기간) · `report`(rcept_no·공시일·url).

## 파싱전략 (markdown-primary + best-effort headline)
- **table_markdown이 진실**: 잠정실적은 항상 표라서 정형 positional 파싱보다 **표를 통째 마크다운으로 렌더**(colspan/rowspan 확장 `_table_to_grid`)가 robust. 재무형·비재무형(판매대수·수주) 모두 같은 방식.
- **headline은 best-effort**: 매출·영업익·순익 당기값+YoY만 구조화(screener 카드/빠른보기). **열은 헤더로 식별**(당기실적=값열, 두 번째 '증감율(%)'=전년동기대비). ⚠ positional backward-search 금물 — 적자전환/음수 YoY 에서 '전년동기실적' 절대값을 오채택한다. 실패해도 table_markdown 이 전부 담는다.
- **단위 정규화**: 원문 '단위:'(조원×1e12·억원×1e8·백만원×1e6) → value_krw는 원 단위 raw.
- **비재무형**: 재무표가 전부 '-'면 kind=non_financial. 자동차(판매대수)·조선(수주 백만불) 등 도메인 표가 같은 실적표에 이어져 있어 table_markdown이 그대로 담는다.

## 공시 landscape (KOSPI500 census)

영업잠정실적은 **자율 공정공시**(의무 아님) — 규격화된 업종규칙보다 **시총·IR 성숙도**가 지배한다:
- **공시율 시총 강상관**: top100 **94%** · 100~300위 67% · 300~500위 **32%** (전체 293/499=58%). 대형주는 거의 필수, 중소형은 선택.
- **업종별**(참고): 게임SW·항공 100%, 보험 83%, 소매 78%, 조선 77%, 전기장비 75% vs 식품 17%·부동산 13%·의료기기 14%·해운 20%(저조). 업종 자체보다 그 업종의 시총분포 영향.
- **포맷 분포**(공시 293사): 재무형 282 / **비재무형 11**. 단위 백만원 235·억원 56·조원 2. 연결 239·별도 54. **파서 anomaly 0**(전 단위·연결/별도·재무/비재무 전수 clean).
- **비재무형 = 도메인 실적표**(표준 매출/영업이익 표는 '-', 별도 도메인표): 자동차(현대차·기아·KG모빌리티=판매대수) · 조선(HD현대중공업·HD한국조선해양=수주 백만불) · 유틸리티(한국가스공사·지역난방=판매량) · 카지노(파라다이스·GKL·롯데관광=카지노매출 테이블/머신) · **오리온(지역별 매출=한국/중국/베트남/러시아 법인별)**. 전부 `table_markdown`이 도메인 데이터를 정확히 담음 → 호출측 AI가 읽음.
- **함의**: 미공시(NO_FILING)는 정상(중소형·특정업종). 최신 확정 필요하면 정기보고서([[financial_metrics]]).

## screener 연동
`screener`의 `잠정실적`(I002, tier2)이 `detail_kind="earnings"` → `_extract_earnings`로 이 tool의 `build_provisional_earnings_payload`를 재사용. 시장스캔이 잠정실적 **이벤트+숫자**(매출/영업익/YoY) 함께 반환.

## 한계
- 잠정치(감사 전) — 확정과 다를 수 있음. 확정 재무비율은 [[financial_metrics]].
- 비재무형(자동차 판매대수 등)은 headline 없음(table_markdown만).
- colspan 확장으로 헤더 셀이 중복 표기(가독성 경미, 수치 왜곡 없음).

## 관련
- [[financial_metrics]] (확정 재무 — 잠정과 대비)
- [[공시유형코드체계]] (I002 공정공시)
- [[사업보고서]] · [[분기보고서]] (확정 정기보고서)
