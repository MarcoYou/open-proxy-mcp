# OPM Wiki Index

OPM은 한국 상장사 거버넌스 분석 MCP. 이 인덱스에서 시작.

> 👤 **사람이 처음 오셨다면 → [[guide/README]]** (사람용 안내서) · 설치·연결은 루트 [`README.md` 「빠른 시작」](../README.md) · 용어는 [[guide/용어-첫걸음]].
> 아래 인덱스는 AI 에이전트·개발자용 전체 카탈로그입니다.

## Quick Start

OPM tool 카탈로그 → **[[tools/README]]** (처음 방문 시 여기부터)
wiki 구조·명명·link 정책 → **[[wiki_schema]]**

## 카테고리 인벤토리

| 카테고리 | 페이지 수 | 무엇 |
|---|---|---|
| **raw/** | ~39 files | 외부 원본 (수정 금지) |
| **rules/** | 90 + README 4 | 한국 자본시장 사실 |
| **tools/** | 31 + README | MCP tool 카탈로그 |
| **decisions/** | 27 + README | 설계·정책·판단·시점 작업 |
| **guide/** · **handoff/** | 3 + 2 | 사람용 안내(README·아키텍처·용어 첫걸음) · 세션 간 미해결 항목 (보조) |

총 162 markdown (git-tracked, raw·corpus 제외).

> 규칙(명명·link·수정정책)은 [[wiki_schema]] 단일 출처. 이 파일은 인벤토리·라우팅만.

## Tools (31) - `tools/`

도구 목록·분류의 정본은 **[[tools/README]]** 「무엇을 알고 싶을 때 무엇을 쓰나」 표 하나다 — 여기서 반복하지
않는다(`scripts/check_tool_catalog.py` 가 그 표와 런타임을 대조하고, 이 헤더의 수는 `gen_index.py` 가 채운다).

### Internal (MCP 노출 X)
- `director_evaluation` — proxy_advise 후보 평가 chain
- `director_performance` — 사내이사 성과 매트릭스 2x3
- `agm_first_agenda_fy` — 1번 안건 본문 FY raw 파서

## Rules (0) - `rules/`

### Concepts (45) - `rules/concepts/`
[[5%-대량보유]] · [[DART-OpenAPI]] · [[FCF]] · [[KRX-KIND]] · [[NWC]] · [[PER-PBR]] · [[ROA]] · [[ROE]] · [[ROIC]] · [[감사위원-의결권-제한]] · [[감액배당]] · [[국민연금]] · [[네이버-금융]] · [[단위-표기-규약]] · [[당기순이익]] · [[대주주]] · [[동일인]] · [[듀퐁분석]] · [[배당성향]] · [[배당수익률]] · [[보고사항]] · [[보수한도]] · [[분기배당]] · [[소액주주]] · [[순현금]] · [[시가총액]] · [[시점-제약]] · [[연결-별도]] · [[위임장-권유]] · [[의결권]] · [[이자보상배율]] · [[자본준비금]] · [[자사주]] · [[정관변경]] · [[주주제안]] · [[주주환원]] · [[주총-결의]] · [[지분구조]] · [[집중투표]] · [[참석률]] · [[최대주주]] · [[특별배당]] · [[특수관계인]] · [[파서-판정-등급]] · [[프록시-파이트]]

### Disclosures (44) - `rules/disclosures/`
[[공시유형코드체계]] · [[감액배당결정]] · [[감자결정]] · [[경영권분쟁소송]] · [[교환사채권발행결정]] · [[기업가치제고계획]] · [[기업지배구조보고서]] · [[단일판매공급계약체결]] · [[대량보유상황보고서]] · [[반기보고서]] · [[배당공시유형]] · [[배당기준일결정]] · [[분기배당결정]] · [[분기보고서]] · [[분기재무-API스펙]] · [[사업보고서]] · [[소송등의제기]] · [[신주인수권부사채발행결정]] · [[신탁계약에의한취득상황보고서]] · [[신탁계약해지결과보고서]] · [[위임장권유참고서류]] · [[유상증자결정]] · [[임원·주요주주특정증권등소유상황보고서]] · [[임원보수-API스펙]] · [[자기주식결정]] · [[자기주식소각결정]] · [[자기주식신탁결정]] · [[자기주식의무소각-2026신법]] · [[자기주식처분결과보고서]] · [[자기주식처분결정]] · [[자기주식취득결과보고서]] · [[자기주식취득결정]] · [[전환사채발행결정]] · [[주식교환·이전결정]] · [[주식배당결정]] · [[주주총회결과]] · [[주주총회소집공고]] · [[최대주주등소유주식변동신고서]] · [[최대주주변경]] · [[타법인주식및출자증권거래]] · [[현금배당결정]] · [[회사분할결정]] · [[회사분할합병결정]] · [[회사합병결정]]

### Laws (1 + corpus) - `rules/laws/`
[[상법-2025-2026-종합]]
corpus/: 10법 원문 — 상법·자본시장법·공정거래법·외부감사법 + 지배구조법·상증세법·금융지주회사법·금산법·은행법·보험업법 (legalize-kr 자동 복사)

## Decisions (28) - `decisions/`

### 설계·아키텍처
[[open-proxy-guideline]] · [[data-collection]] · [[multi-upstream-pattern]] · [[mcp-endpoints]] · [[environment-secrets]]

### 정책·판단
[[BeautifulSoup-파서-선택]] · [[XML-vs-PDF]] · [[pblntf-ty-필터링]] · [[DART-KIND-매핑-화이트리스트-2026-04]] · [[ksic-sector-mapping]] · [[후보반환-설계]]

### 시점 결정
[[260429_0059_decision_voting-policy-consensus-matrix]] · [[260505_1700_decision_inside-director-performance-matrix]] · [[260505_1900_decision_compensation-retirement-split]] · [[260506_0030_decision_notice-scope-cleanup-prov-financials]] · [[260507_2330_decision_httpx-connection-pool]] · [[260508_0030_decision_classify-agenda-parent-shortcircuit]] · [[260508_0200_decision_law-layer]] · [[260508_0700_decision_law-layer-precision]] · [[260510_0900_decision_d-pattern-body-fallback]] · [[260510_1015_decision_subagenda-mapping]] · [[260510_1130_decision_director-faithfulness]] · [[260510_1230_decision_career-parser-concat]] · [[260702_1520_decision_usage-is-error-tracking]] · [[260717_1220_decision_business-content-tool-roadmap]] · [[260721_1500_decision_asset-holdings-purpose-buckets]] · [[260823_1720_decision_financial-notes-tool]]

### 시점 수정
(fix 분석문은 storage `wiki-private/archive/opm-decisions/` 로 이관 — 260902)

## 기타

- 작업 로그(구 `log.md`, 2026-04-05~08-25)는 storage `wiki-private/archive/opm-wiki-log.md` 로 이관(260902). 변경 이력은 `docs/RELEASE_NOTES.md` 와 각 tool 페이지 변경 이력을 본다.
- [[wiki_schema]] — wiki 구조·명명·link 정책
- `guide/` — [[guide/README]] · [[guide/architecture]] · [[guide/용어-첫걸음]]
- `handoff/` — [[handoff/README]]
