# OPM Wiki Index

OPM은 한국 상장사 거버넌스 분석 MCP. 이 인덱스에서 시작.

> 👤 **사람이 처음 오셨다면 → [[guide/README]]** (사람용 안내서).
> 아래 인덱스는 AI 에이전트·개발자용 전체 카탈로그입니다.

## Quick Start

OPM tool 카탈로그 → **[[tools/README]]** (처음 방문 시 여기부터)
wiki 구조·명명·link 정책 → **[[wiki_schema]]**

## 카테고리 인벤토리

| 카테고리 | 페이지 수 | 무엇 |
|---|---|---|
| **raw/** | ~39 files | 외부 원본 (수정 금지) |
| **rules/** | 88 + README 4 | 한국 자본시장 사실 |
| **tools/** | 33 + README | MCP tool 카탈로그 |
| **decisions/** | 40 + README | 설계·정책·판단·시점 작업 |

총 178 markdown (git-tracked, raw 제외).

> 규칙(명명·link·수정정책)은 [[wiki_schema]] 단일 출처. 이 파일은 인벤토리·라우팅만.

## Tools (35) - `tools/`

### Company (1)
- [[company]]

### Screening (1)
- [[screener]]

### Meeting (2)
- [[shareholder_meeting_notice]] (사전 — DART)
- [[shareholder_meeting_results]] (사후 — DART 원문 우선, KIND fallback)

### Data (22)
- [[ownership_structure]] · [[dividend]] · [[financial_metrics]] · [[treasury_share]]
- [[proxy_contest]] · [[value_up]] · [[corporate_restructuring]] · [[dilutive_issuance]]
- [[corporate_deals]] · [[order_contracts]] · [[risk_events]] · [[corp_gov_report]]
- [[director_board]] · [[price_multiple_data]] · [[trading_data]] · [[business_details]]
- [[provisional_earnings]] · [[asset_holdings]] · [[financial_notes]] · [[forward_estimates_data]]
- [[dividend_history_data]] · [[dividend_screener]]

### Evidence (2)
- [[evidence]]
- [[law_lookup]]

### Action (2)
- [[proxy_advise_before_meeting]]
- [[shareholder_commitment]]

### Internal (MCP 노출 X)
- `director_evaluation` — proxy_advise 후보 평가 chain
- `director_performance` — 사내이사 성과 매트릭스 2x3
- `agm_first_agenda_fy` — 1번 안건 본문 FY raw 파서

### 참조 (5)
- [[tool_call_budget]] · [[tool_disclosure_map]] · [[data_tool_disclosure_map]]
- [[director_news]] · [[proxy_guideline]]

## Rules (0) - `rules/`

### Concepts (43) - `rules/concepts/`
[[5%-대량보유]] · [[FCF]] · [[NWC]] · [[ROA]] · [[ROE]] · [[ROIC]] · [[가결]] · [[감사위원-의결권-제한]] · [[감액배당]] · [[경영권-방어]] · [[당기순이익]] · [[대주주]] · [[동일인]] · [[듀퐁분석]] · [[배당성향]] · [[배당수익률]] · [[보고사항]] · [[보수한도]] · [[부결]] · [[분기배당]] · [[소액주주]] · [[소진율]] · [[순현금]] · [[시가배당률]] · [[시간순서-규칙]] · [[위임장-권유]] · [[위임장]] · [[의결권]] · [[이자보상배율]] · [[자본준비금]] · [[자사주]] · [[정관변경]] · [[주주제안]] · [[주주환원]] · [[지분구조]] · [[집중투표]] · [[찬반율]] · [[참석률]] · [[최대주주]] · [[특별배당]] · [[특수관계인]] · [[파서-판정-등급]] · [[프록시-파이트]]

### Disclosures (44) - `rules/disclosures/`
[[공시유형코드체계]] · [[감액배당결정]] · [[감자결정]] · [[경영권분쟁소송]] · [[교환사채권발행결정]] · [[기업가치제고계획]] · [[기업지배구조보고서]] · [[단일판매공급계약체결]] · [[대량보유상황보고서]] · [[반기보고서]] · [[배당공시유형]] · [[배당기준일결정]] · [[분기배당결정]] · [[분기보고서]] · [[분기재무-API스펙]] · [[사업보고서]] · [[소송등의제기]] · [[신주인수권부사채발행결정]] · [[신탁계약에의한취득상황보고서]] · [[신탁계약해지결과보고서]] · [[위임장권유참고서류]] · [[유상증자결정]] · [[임원·주요주주특정증권등소유상황보고서]] · [[임원보수-API스펙]] · [[자기주식결정]] · [[자기주식소각결정]] · [[자기주식신탁결정]] · [[자기주식의무소각-2026신법]] · [[자기주식처분결과보고서]] · [[자기주식처분결정]] · [[자기주식취득결과보고서]] · [[자기주식취득결정]] · [[전환사채발행결정]] · [[주식교환·이전결정]] · [[주식배당결정]] · [[주주총회결과]] · [[주주총회소집공고]] · [[최대주주등소유주식변동신고서]] · [[최대주주변경]] · [[타법인주식및출자증권거래]] · [[현금배당결정]] · [[회사분할결정]] · [[회사분할합병결정]] · [[회사합병결정]]

### Laws (1 + corpus) - `rules/laws/`
[[상법-2025-2026-종합]]
corpus/: 상법·자본시장법·공정거래법·외부감사법 원문 (legalize-kr 자동 복사)

## Decisions (40) - `decisions/`

### 설계·아키텍처
[[open-proxy-guideline]] · [[proxy-voting-decision-tree]] · [[3-tier-fallback]] · [[data-collection]] · [[multi-upstream-pattern]] · [[mcp-endpoints]] · [[environment-secrets]] · [[project_structure]] · [[proxy_advise_word_report_design]] · [[proxy_advise_word_report_spec]]

### 정책·판단
[[BeautifulSoup-파서-선택]] · [[XML-vs-PDF]] · [[free-paid-분리]] · [[LLM-fallback-설계]] · [[pblntf-ty-필터링]] · [[DART-KIND-매핑-화이트리스트-2026-04]] · [[ksic-sector-mapping]] · [[tool-추가-검증-정책]] · [[후보반환-설계]] · [[tool-changelog]]

### 시점 결정
[[260429_0059_decision_voting-policy-consensus-matrix]] · [[260505_1700_decision_inside-director-performance-matrix]] · [[260505_1900_decision_compensation-retirement-split]] · [[260506_0030_decision_notice-scope-cleanup-prov-financials]] · [[260507_2330_decision_httpx-connection-pool]] · [[260508_0030_decision_classify-agenda-parent-shortcircuit]] · [[260508_0200_decision_law-layer]] · [[260508_0700_decision_law-layer-precision]] · [[260510_0900_decision_d-pattern-body-fallback]] · [[260510_1015_decision_subagenda-mapping]] · [[260510_1130_decision_director-faithfulness]] · [[260510_1230_decision_career-parser-concat]] · [[260624_1503_fix_dilutive-exchangeable-bond]] · [[260702_1520_decision_usage-is-error-tracking]] · [[260717_1220_decision_business-content-tool-roadmap]] · [[260721_1500_decision_asset-holdings-purpose-buckets]] · [[260823_1720_decision_financial-notes-tool]]

### 시점 수정
[[260427_1145_fix_ownership-stockknd]] · [[260429_0216_fix_speed-optimization-9건]] · [[260429_0942_fix_corp_gov_report-financial-holding]]

## 기타

- [[log]] — 작업 로그 (시간순)
- [[wiki_schema]] — wiki 구조·명명·link 정책
- `guide/` — [[guide/README]] · [[guide/architecture]] · [[guide/presentation]]
- `handoff/` — [[handoff/README]]
