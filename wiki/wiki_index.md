---
type: index
title: OPM Wiki Index
updated: 2026-07-09
---

# OPM Wiki Index

OPM은 한국 상장사 거버넌스 분석 MCP. 이 인덱스에서 시작.

> 👤 **사람이 처음 오셨다면 → [[guide/README]]** (사람용 안내서: 개요·아키텍처·발표자료).
> 아래 인덱스는 AI 에이전트·개발자용 전체 카탈로그입니다.

## Quick Start (사용자 진입점)

OPM tool 25개 카탈로그 -> **[[tools/README]]** (처음 방문 시 여기부터)

### 도메인별 (25 tool, 260722 getting_started 제거)
- **Company (1)**: [[company]]
- **Screening (1)**: [[screener]] (전체시장 공시 스크리너 / 아침 디제스트 — scan 싸게 + details 필요 건만 파서 재사용, market-scan)
- **Meeting (2, 시점 분리)**: [[shareholder_meeting_notice]] (사전 — DART, 5 scope: summary/board/compensation/aoi_change/prov_financials) · [[shareholder_meeting_results]] (사후 — DART 원문 우선, KIND fallback)
- **Data (17)**: [[ownership_structure]] · [[dividend]] · [[financial_metrics]] · [[treasury_share]] · [[proxy_contest]] · [[value_up]] · [[corporate_restructuring]] · [[dilutive_issuance]] · [[corporate_deals]] · [[order_contracts]] · [[risk_events]] · [[corp_gov_report]] · [[director_board]] · [[valuation]] · [[business_details]] · [[provisional_earnings]] · [[asset_holdings]] (자산주·NAV 스크리닝 — 계정 티어+상장지분 시가마크+시총 대비 배수)
- **Evidence (2)**: [[evidence]] · [[law_lookup]] (정관↔법령 양방향 조회 — 상법·자본시장법·공정거래법·외부감사법 원문, 회사·DART 무관)
- **Action (2)**: [[proxy_advise_before_meeting]] (decisions 단일 — facts/risk/citation/근거공고/후보 raw 통합, 사후 결과는 [[shareholder_meeting_results]]) · [[shareholder_commitment]] (밸류업·배당·소각 약속 vs 실제 이행, 연중 스튜어드십 — 자사주소각 장부가손익 신규 계산)

### Internal services (MCP 노출 X — chain 전용)
- `director_evaluation` — proxy_advise 후보 평가 chain (결격 / 독립성 / 전문성 / 과거 행적)
- `director_performance` — 사내이사 재직 중 성과 매트릭스 2x3 (ROE/부채비율/CSR × avg/trend) — proxy_advise 사내이사 분기에 wire
- `agm_first_agenda_fy` — 1번 안건 본문 FY raw 파서

### 주요 변화
최근 변경의 서사·rationale 다이제스트는 **[[log]] 상단 '[다이제스트]' 블록**으로 이관(260709 —
40KB 인덱스에서 59줄 changelog 분리, 라우팅 인덱스 순수화). 개별 작업 상세는 [[log]] 시간순 엔트리,
tool별 현재 상태는 각 [[tools/README]] 페이지가 정본.

## 카테고리 인벤토리

| 카테고리 | 페이지 수 |
|---|---|
| **raw/** | 29 binary + 4 md |
| **tools/** | 25 + README |
| **architecture/** | 13 + README 2 (audits·goals는 private 이관 260806) |
| **decisions/** | 25 + README |
| **rules/** | 88 + README 4 |
| ~~lessons/~~ | private 이관(260720, open-proxy-storage/wiki-private/lessons) |
| **archive/** | 71 + README 2 |

총 264 markdown (git-tracked, raw 제외 — wiki_lint 실측과 동기. gitignore된 로컬 전용 파일은 미포함).

> **규칙은 여기 두지 않는다.** 각 카테고리의 목적·수정정책·layer 정의, 명명 규칙, frontmatter schema,
> link 방향 정책은 전부 [[wiki_schema]]가 단일 출처(SSOT). 이 파일은 "무엇이 어디 있나"(인벤토리·라우팅)만
> 담는다. 규칙 서술을 여기 복붙하면 `wiki_lint [8]`이 CI를 막는다(260712 패널 결정 — 규칙 이중장부 금지).

## 자주 쓰는 진입점

### 처음 사용자
- [[tools/README]] - 25 tool 카탈로그
- [[wiki_schema]] - wiki 구조 + 명명 규칙

### OPM 정책 알고 싶음
- [[open-proxy-guideline]] - Open Proxy Guideline v1.3 (12 카테고리 + 16 novel topics)
- [[260429_0059_decision_voting-policy-consensus-matrix]] - 8 운용사 합의 매트릭스

### 시스템 동작 이해
- [[architecture/data-collection]] - 데이터 수집 architecture
- [[architecture/3-tier-fallback]] - XML -> PDF -> OCR (OPM은 XML 단독; PDF/OCR은 open-proxy-ai로 이관 260712)
- matrix-system - 12 매트릭스 설계 자산 (자동 채점은 의결권 엔진 미사용 — dead code)
- [[architecture/proxy-voting-decision-tree]] - 의결권 판단 framework
- pipeline-architecture - 199 기업 v4 JSON 배치 파이프라인
- [[architecture/multi-upstream-pattern]] - asyncio.gather tool 표준 5 요소 (corpCode lock/retry/per-call timeout/semaphore/cache)
- [[architecture/mcp-endpoints]] - live-opm / pilot-opm (목적이 다른 별개 대상, stdio 금지)

### 한국 자본시장 용어 모름
- [[rules/concepts/]] - 31 개념 (배당성향 / 최대주주 / 동일인 / 집중투표 등)
- [[rules/disclosures/]] - 36 공시 유형 (현금배당결정 / 유상증자결정 / 자기주식취득결정 등)
- [[rules/laws/상법-2025-2026-종합]] - 1·2·3차 상법 개정 통합본 + 4 시나리오 + 36 catalog (master, 260508)
- `wiki/rules/laws/law_layer_rules.json` - 머신리더블 40 룰 (proxy_advise._law_layer 직접 로드)
- `wiki/rules/laws/law_provisions.json` - **시행일 SSOT** (조항별 시행·공포일 원본). md 표 자동생성·엔진 날짜 검증의 유일 출처 (260709)
- [[rules/laws/README]] - 법령 자료 입구 (옛 archive 안내)

### 최근 audit / fix

> **audit 서술(.md)은 260806 private storage 로 이관** — `open-proxy-storage/wiki-private/architecture/audits/`
> (raw data 는 260718에 `open-proxy-storage/audits_data/`). 아래는 wiki 에 남은 archive 이력과 fix 뿐이다.

- [[260504_0724_audit_parse_personnel_iter1-7]] - parse_personnel ralph 7 iter — role 88.7→100% + regression 0 (G2 99.36% 유지)
- [[260503_2304_audit_recap_pattern]] - recap_vote 패턴 적용 200×3 100% (multi-upstream-pattern 일반화 검증)
- [[260503_1847_audit_phase4_final]] - advise_vote 200×3 deterministic 100% + regression 0 (Phase 4)
- [[260429_0912_audit_parsing-200기업-v2-no_filing]] - 196 기업 × 11 tool audit 이력
- [[260429_2053_audit_personnel-878명]] - personnel 파서 SUCCESS 79->95%
- [[260624_1503_fix_dilutive-exchangeable-bond]] - 교환사채(EB) 5종 확장 + 원문 복원 fix
- [[260427_1145_fix_ownership-stockknd]] - 보통주 변형 매칭 fix
- [[260429_0942_fix_corp_gov_report-financial-holding]] - 금융지주 분류 fix

---

## Tools (25 진입점) - `tools/`

전체 카탈로그 + 통계 + 흡수된 archive 매핑은 [[tools/README]] — 아래는 요약 목록(신규 tool 추가 시
[[tools/README]]와 함께 갱신).

### Company (1)
- [[company]] - 기업 식별 + 최근 공시 인덱스

### Screening (1)
- [[screener]] - 전체시장 공시 스크리너 / 아침 디제스트 (scan+details, market-scan)

### Meeting (2)
- [[shareholder_meeting_notice]] - 주총 소집공고 사전 데이터
- [[shareholder_meeting_results]] - 주총 의결 결과 사후 데이터

### Data (17)
- [[ownership_structure]] - 최대주주/특수관계인/5%/control_map
- [[financial_metrics]] - DART 재무 4 endpoint 통합
- [[corp_gov_report]] - 기업지배구조보고서 15지표
- [[director_board]] - 이사 인당보수·보수한도 소진율·재직/사퇴 변동·개별보수·미등기·이사회 출석률·원문 각주 해소·보수 산정기준(pay_criteria) (260708 신설, 260709 각주정밀도·출석률·성능 검수, 260713 pay_criteria 원문파서+정형API 하이브리드 교차검증)
- [[dividend]] - 배당 사실 + 분기별 breakdown
- [[treasury_share]] - 자사주 결정/결과/신탁/소각
- [[value_up]] - 기업가치제고계획
- [[corporate_restructuring]] - 합병/분할/주식교환·이전
- [[dilutive_issuance]] - 유상증자/CB/BW/감자
- [[proxy_contest]] - 위임장/소송/5%/vote_math
- [[corporate_deals]] - 지분 인수·매각(타법인주식) + 단일공급계약 (구 related_party_transaction)
- [[order_contracts]] - 단일판매·공급계약
- [[risk_events]] - 리스크 이벤트 활성 3종 (중대재해/횡령배임/생산중단·영업정지, 파생·회생·해산 mute)
- [[valuation]] - PER·PBR·배당수익률(기업 심층) + 시장/섹터/종목 히스토리 (260705 신설)
- [[business_details]] - "II.사업의 내용" 11필드(segments+사업장·가동률·rnd·수주·고객·원재료·제품가격+D-트랙 금융/REIT) (260718 신설)
- [[provisional_earnings]] - 영업(잠정)실적 분기 속보(I002 공정공시) + YoY (260719 신설)
- [[asset_holdings]] - 자산주·NAV 스크리닝 (계정 티어+상장지분 시가마크+시총 대비 배수) (260720 신설)

### Evidence (2)
- [[evidence]] - rcept_no -> 공시일/소스/뷰어 URL
- [[law_lookup]] - 정관↔법령 양방향 조회 (상법·자본시장법·공정거래법·외부감사법 원문 corpus, 3신호 매처, 회사·DART 0콜)

### Action (2)
- [[proxy_advise_before_meeting]] - 주총 전 의결권 자문
- [[shareholder_commitment]] - 밸류업·배당·소각 약속 vs 실제 이행 (연중 스튜어드십)

---

## Architecture (9 + fixes 4)

### 인덱스 (READMEs)
- [[architecture/README]] — 시스템 설계 입구
- [[architecture/fixes/README]] — 설계·성능 시점 수정 기록
- [[ralph/README]] — Ralph plans 시간순 인덱스 (24 plans)
- [[decisions/README]] — Decisions 인덱스
- [[tools/README]] — Tools 카탈로그 (사용자 진입점)
- Lessons — private 이관(open-proxy-storage/wiki-private/lessons, 260720)
- Audits · Goals — private 이관(open-proxy-storage/wiki-private/architecture/{audits,goals}, 260806).
  raw data 는 260718에 먼저 `open-proxy-storage/audits_data/`로 이관됨

### 시스템 설계
- [[architecture/data-collection]] - OPM 전수 데이터 수집 entry point + 파싱 방법 (DART/KIND/Naver/정적 JSON)
- [[architecture/3-tier-fallback]] - XML -> PDF -> OCR 3단계 전략 (OPM은 XML 단독; PDF/OCR은 open-proxy-ai 이관 260712)
- [[architecture/multi-upstream-pattern]] - 여러 upstream 병렬 호출 5요소 표준 (concurrency + race fix)
- [[architecture/proxy-voting-decision-tree]] - 3개 소스 통합 의결권 행사 판단 프레임워크
- [[architecture/mcp-endpoints]] - live-opm / pilot-opm — 목적이 다르고 따로 관리, stdio 금지
- [[architecture/environment-secrets]] - 어떤 키가 왜 필요한가 · 로컬 `.env` + fly secrets
- [[architecture/project_structure]] - 코드 구조
- matrix-system · pipeline-architecture · per-pbr-data-points · adjusted-price-timeseries — private storage

### fixes/ (4 시점별)
- [[260427_1145_fix_ownership-stockknd]] - ownership_structure 17건 partial -> 0 fix (stock_knd 변형 positive matching + 3-tier fallback, regression 0)
- [[260429_0216_fix_speed-optimization-9건]] - 9건 sequential -> asyncio.gather 적용 (proxy_contest 4x, ownership 3x, dividend 3x)
- [[260429_0942_fix_corp_gov_report-financial-holding]] - corp_gov_report 금융지주 18건 partial -> 0 fix (financial_form 감지)
- [[260624_1503_fix_dilutive-exchangeable-bond]] - dilutive_issuance 교환사채(EB) 5종 확장 + 정정/철회/누락 원문 복원

---

## Decisions (25) - `decisions/`

### 정책 + 매트릭스
- [[open-proxy-guideline]] - OPM 자체 의결권 행사 정책 v1.2 (12 카테고리 116 룰 + 11 novel topics + 2026 신법 7개 + §382의3 cross-cutting)
- [[260429_0059_decision_voting-policy-consensus-matrix]] - 7 운용사 의결권 정책 합의/이견 매트릭스 (79 토픽, 12 카테고리)
- [[260505_1700_decision_inside-director-performance-matrix]] - 사내이사 재직 중 성과 매트릭스 2x3 도입 (status quo bias mitigation, KOSPI 100 + KOSDAQ 50 검증)
- [[260505_1900_decision_compensation-retirement-split]] - 보수한도/퇴직금 분리 (이사 13 / 감사 11 / 퇴직금 12 분기 + 정관 hybrid + 3 ralph 검증 G1 모두 99%+/G3 100%/G4 100% — KOSPI 200+KOSDAQ 50 n=226)
- [[260506_0030_decision_notice-scope-cleanup-prov-financials]] - shareholder_meeting_notice scope 정리 (6→5) + provisional_financial_statement 독립 모듈 + prov_financials scope 신설 (data/action layer 정합)

### Tool 정책 + 변경 이력
- [[tool-changelog]] - Tool 제거/통합/리네임 이력 (41->32->17개, 이유 포함)
- [[tool-추가-검증-정책]] - release_v2 신규 tool 추가 시 action/data별 검증 매뉴얼 + 화이트리스트 체크
- [[free-paid-분리]] - MCP(public) + Pipeline(private) 2-repo 구조

### 파서 + 데이터 소스 결정
- [[XML-vs-PDF]] - 왜 XML 단독인가 (문서가 선언한 구조를 PDF는 잃는다; PDF/OCR tier는 260712 폐기)
- [[BeautifulSoup-파서-선택]] - lxml 채택 (30% 빠름, 결과 동일)
- [[LLM-fallback-설계]] - 정규식 -> zone 추출 -> LLM 하이브리드 전략
- [[pblntf-ty-필터링]] - DART 검색 시 pblntf_ty 필수 지정, 전체 순회 금지 (D/E/I 코드표)
- [[DART-KIND-매핑-화이트리스트-2026-04]] - KIND 병행 허용 공시 화이트리스트 + false match 사례

---

## Rules

### Concepts (43) - `rules/concepts/`
한국 자본시장 도메인 개념. tool 본문에서 link only.

#### 배당
- [[배당성향]] · [[배당수익률]] · [[시가배당률]] · [[분기배당]] · [[특별배당]] · [[감액배당]] · [[당기순이익]] · [[자본준비금]]

#### 지분 + 주체
- [[지분구조]] · [[최대주주]] · [[대주주]] · [[동일인]] · [[특수관계인]] · [[5%-대량보유]] · [[소액주주]] · [[자사주]]

#### 의결권 + 주총
- [[의결권]] · [[집중투표]] · [[감사위원-의결권-제한]] · [[참석률]] · [[정관변경]] · [[주주제안]] · [[보수한도]] · [[소진율]]

#### 분쟁 + 환원
- [[프록시-파이트]] · [[위임장-권유]] · [[경영권-방어]] · [[주주환원]]

#### 시스템 메타
- v4-스키마 · [[시간순서-규칙]] · [[파서-판정-등급]]

### Disclosures (44) - `rules/disclosures/`
DART/KIND 공시 유형. 공시명 = 페이지명.

#### 코드체계
- [[공시유형코드체계]] - pblntf_ty(A-J) + pblntf_detail_ty(I001 등) → 실제 공시 매핑, 6사 실증

#### 주총 + 정기보고서
- [[주주총회소집공고]] · [[주주총회결과]] · [[사업보고서]] · [[반기보고서]] · [[분기보고서]]

#### 배당 (6)
- [[현금배당결정]] · [[주식배당결정]] · [[배당기준일결정]] · [[분기배당결정]] · [[감액배당결정]] · [[배당공시유형]]

#### 자사주 (6)
- [[자기주식결정]] · [[자기주식취득결정]] · [[자기주식처분결정]] · [[자기주식소각결정]] · [[자기주식신탁결정]] · [[자기주식의무소각-2026신법]]

#### 지분 + 위임장
- [[대량보유상황보고서]] · [[위임장권유참고서류]] · [[최대주주등소유주식변동신고서]] · [[최대주주변경]] · [[임원·주요주주특정증권등소유상황보고서]]

#### 분쟁
- [[소송등의제기]] · [[경영권분쟁소송]]

#### 발행 + 재편
- [[유상증자결정]] · [[전환사채발행결정]] · [[신주인수권부사채발행결정]] · [[감자결정]]
- [[회사합병결정]] · [[회사분할결정]] · [[회사분할합병결정]] · [[주식교환·이전결정]]

#### 거래 + 거버넌스
- [[타법인주식및출자증권거래]] · [[단일판매공급계약체결]] · [[기업지배구조보고서]] · [[기업가치제고계획]]

### Laws (1) - `rules/laws/`
- [[rules/laws/상법-2025-2026-종합]] - 2025-2027 상법 개정 시행 일정
- (보조 자료는 archive 보존: [[archive/laws/주총방어-시나리오-4가지]] · [[archive/laws/주총체크리스트-2026]] — [[rules/laws/README]] 참조)

---

## Archive (71)

흡수된 페이지 (역사 보존, 신규 사용자 안 봐도 OK).

### archive/analysis/ (18)
release_v2 검증 예시 + 설계 문서. 현재 17 public tools/* 페이지와 archive 이력으로 흡수.
[[release_v2-tool-아키텍처]] · [[release_v2-public-tool-검증-매트릭스]] · [[release_v2-action-tool-검증-초안]] · [[KIND-주총결과]] · [[cash-shareholder-return-2026-04-29]] · [[total-shareholder-return-2026-04-29]] 등

### archive/comparison/ (3)
- [[stkrt-vs-ctr_stkrt]] · [[회사측-vs-주주측-위임장]] · [[배당-자사주-공시-종합]]

### archive/decisions/ (2)
현행 코드와 어긋나지만 현재 페이지들이 아직 가리키는 v1 결정 (260806 이동, `superseded_by` 명시).
[[archive/decisions/cross-domain-체이닝]] · [[archive/decisions/260721_1600_decision_getting-started-tool-vs-resource]]

### archive/entities/ (8)
DART/KIND/Upstage 등 외부 entity 페이지. CLAUDE.md path만 archive 보존.
[[archive/entities/DART-OpenAPI]] · [[archive/entities/KRX-KIND]] · [[archive/entities/네이버-금융]] · [[archive/entities/Upstage-OCR]] · [[archive/entities/OpenProxy-MCP]] · OpenProxy-AI · [[archive/entities/국민연금]] · [[archive/entities/FastMCP]] · [[archive/entities/opendataloader]]

### archive/sources/ (5)
구 RULE 파일 요약 + taxonomy.
[[agm-tool-rule]] · [[div-tool-rule]] · [[own-tool-rule]] · [[dart-kind-disclosure-taxonomy]] · [[주총방어전략-2026]]

### archive/templates/ (1)
- [[tool-추가-검증-템플릿]] - 신규 data/action tool 제안 템플릿

### archive root (7)
구 case rule + 단일 disclosure 페이지.
- [benchmark](archive/benchmark-personnel-results.md)
- [agm-case-rule](archive/agm-case-rule.md) · [own-case-rule](archive/own-case-rule.md) · [div-case-rule](archive/div-case-rule.md)
- [임원주요주주](archive/임원주요주주특정증권등소유상황보고서.md) · [자기주식취득처분결정](archive/자기주식취득처분결정.md) · [정정공시](archive/정정공시.md)
