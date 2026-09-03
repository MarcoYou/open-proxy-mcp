---
type: readme
title: tools/ — 도구 카탈로그
updated: 2026-09-03
---

# 도구(Tool) 카탈로그

> OPM의 런타임 도구 목록입니다. 도구마다 **답해주는 정보가 다릅니다**. AI 에이전트는 질문에 맞는
> 도구를 스스로 골라 호출합니다. 사용자는 "○○기업 분석해줘"처럼 자연어로 물어보면 됩니다.
>
> 👤 처음이라면 → **[[guide/README]]** (사람용 안내서) · 시스템 동작은 [[guide/architecture]]
> 각 도구의 입력·출력·데이터 출처는 도구 이름을 클릭하면 나옵니다. 「이런 질문에 답한다」류 자연어
> 예시도 각 도구 페이지의 **「사용법」 절**에 있습니다(예전의 별도 예시 폴더는 여기로 흡수).

## 도구 한눈에 — "무엇을 알고 싶을 때 무엇을 쓰나"

### 🏢 기본 — 회사 찾기
| 도구 | 무엇을 답하나 |
|---|---|
| [company](company.md) | 회사 식별 + 최근 공시 목록 — **모든 분석의 출발점** |

### 🔔 전체시장 스캔 · 디제스트
| 도구 | 무엇을 답하나 |
|---|---|
| [screener](screener.md) | 전체시장 공시 스크리너 / **아침 공시 디제스트** — 직전 실행 이후 뜬 주요 공시를 카드형(시총·유형·단계·정정·분모%·링크)으로. scan(싸게)+details(필요 건만 숫자) |

### 🗳️ 주주총회 · 의결권
| 도구 | 무엇을 답하나 |
|---|---|
| [shareholder_meeting_notice](shareholder_meeting_notice.md) | 주총 **소집공고**(주총 전) — 안건·이사 후보·보수한도·정관 변경 |
| [shareholder_meeting_results](shareholder_meeting_results.md) | 주총 **결과**(주총 후) — 안건별 가결/부결·찬반율 |
| [proxy_advise_before_meeting](proxy_advise_before_meeting.md) | **의결권 보조** — 안건별 찬성/반대/검토 + 근거 (핵심 도구) |
| [proxy_guideline](proxy_guideline.md) | **의결권 판단 기준 문서** — 위 판정 사유에 달리는 인용(「OPM Guideline §…」)의 원문. 회사·DART 무관, API 0회 |

### 💰 지분 · 재무 · 지배구조
| 도구 | 무엇을 답하나 |
|---|---|
| [ownership_structure](ownership_structure.md) | 지분 구조 — 최대주주·특수관계인·5% 대량보유·자사주 |
| [financial_metrics](financial_metrics.md) | 재무 지표 — 수익성·안정성·현금흐름·회계 리스크 (정기보고서 **확정치**) |
| [provisional_earnings](provisional_earnings.md) | **영업(잠정)실적**(I002 공정공시) — 분기 잠정 매출·영업이익·순이익+YoY. 정기보고서보다 먼저 나오는 가장 빠른 실적. table_markdown primary + headline best-effort. 자동차 판매대수·조선 수주 등 비재무형도 커버. screener 연동 |
| [business_details](business_details.md) | **"II.사업의 내용" 11필드**: 사업부문별 매출·영업이익 + **사업장·가동률·연구개발·수주·고객·원재료·제품가격** + **D-트랙(금융/REIT): 영업현황·재무건전성·투자부동산**(KSIC 게이트). segments 정형→저신뢰 시 원문 마크다운, 나머지는 **markdown-primary**. `period=latest` 기본(사업·반기·분기 중 최신), `bsns_year`+`reprt_code`로 특정 과거 시점 조회(시계열은 반복 호출). KOSPI500 census 검증(사업의내용_ksic별양식) |
| [asset_holdings](asset_holdings.md) | **자산주·NAV 스크리닝** — 보유 자산(현금성·투자부동산·지분증권·관계기업) 티어 + **상장지분 시가마크** + 시총 대비 잉여자산/지분NAV 배수. "시총보다 보유 자산이 값진가"에 답함 |
| [price_multiple_data](price_multiple_data.md) | 상대가치 **배수** — PER·PBR·배당수익률, 기업·시장·섹터 시계열 (구 `valuation`) |
| [forward_estimates_data](forward_estimates_data.md) | **컨센서스 포워드 추정치** — 내년·내후년 예상 매출·영업이익·EPS·PER/PBR/PSR·성장률 + 대조용 최근 실적. 애널리스트 추정 스냅샷(`fwd`), DART 아님. 커버리지 713/2,764종목 |
| [trading_data](trading_data.md) | 거래·**규모** — 주가·시총·상장주식수 시계열, 시장·섹터 시총 집계, 단일시점 시세(OHLC·거래량) |
| [corp_gov_report](corp_gov_report.md) | 기업지배구조보고서 — 15개 핵심지표 준수 여부 + 서식 표 11종(이사회 구성·출석률·겸직·안건별 찬반) |
| [director_board](director_board.md) | 이사회/개별 이사 — 인당보수·보수한도 소진율·재직/사퇴 변동·개별보수·미등기·이사회 출석률·원문 각주 해소·보수 산정기준(pay_criteria, 정형API 하이브리드 검증) |

### 🎁 주주환원 · 자본
| 도구 | 무엇을 답하나 |
|---|---|
| [dividend_disclosure](dividend_disclosure.md) | 배당 **공시 원문** — 배당금·총액·배당성향·추이 (실시간) |
| [dividend_data](dividend_data.md) | 확정 배당 시계열·조건 스크리닝·시장/섹터 집계 — DART 정기보고서 전수 수집본(코스피 828사 × FY2020~2025) + 결정공시 횟수 집계(FY2020~2024) |
| [treasury_share](treasury_share.md) | 자기주식 — 취득·처분·소각·신탁 |
| [value_up](value_up.md) | 기업가치 제고(밸류업) 계획과 이행 현황 |
| [shareholder_commitment](shareholder_commitment.md) | 밸류업·배당·소각 **약속 vs 실제 이행** 추적 (연중 스튜어드십) — 자사주소각 장부가 손익 계산 |
| [corporate_restructuring](corporate_restructuring.md) | 합병·분할·주식교환·이전 |
| [dilutive_issuance](dilutive_issuance.md) | 유상증자·전환사채(CB)·신주인수권부사채(BW)·감자 (지분 희석) |

### ⚔️ 분쟁 · 거래 · 리스크
| 도구 | 무엇을 답하나 |
|---|---|
| [proxy_contest](proxy_contest.md) | 경영권 분쟁 신호 — 위임장·소송·5% 경영참여 |
| [corporate_deals](corporate_deals.md) | 회사·지분 인수/매각 (계열사 출자·회수) |
| [order_contracts](order_contracts.md) | 수주·공급계약 (체결·해지, 매출 대비 규모) |
| [risk_events](risk_events.md) | 리스크 사건 — 중대재해·횡령배임·생산중단 |
| [financial_notes](financial_notes.md) | **금융사 재무제표 주석 원형 추출** — 사용제한 예치금·담보제공자산(→unencumbered cash)과 투자자산 유형별 구성 FVPL·FVOCI·상각후원가(→유형별 헤어컷). 연결/별도·시점·축·단위·뺄 계정을 판정해 함께 낸다 |
| [director_news](director_news.md) | 이사 후보 **부정 뉴스** 점검 — 공시에 안 나오는 횡령·배임·제재를 훑는다. 동명이인은 가르지 못한다 |

### 🔗 근거 · 참조
| 도구 | 무엇을 답하나 |
|---|---|
| [evidence](evidence.md) | 공시 원문 링크 (접수번호 → DART 열람 URL) |
| [law_lookup](law_lookup.md) | 정관↔법령 양방향 조회 — 정관 조항/키워드 → 관련 법령 조문(전문), 또는 법조문 → 관련 정관 변경유형·우회·안건. 상법·자본시장법·공정거래법·외부감사법 + 지배구조법·상증세법·금융지주회사법·금산법·은행법·보험업법 원문(10법). **회사·DART 무관** |

> 도구–공시 채널 매핑은 아래 「데이터 소스 매트릭스」와 각 페이지 frontmatter `data_source`, 도구별 DART 콜 수는
> 각 페이지 「외부 호출」 절이 정본이다. 옛 시각 자료(17-tool 시점 도식·PPT·콜 budget 표)는 storage
> `wiki-private/archive/opm-wiki-tools/`·`decks/opm-wiki-diagrams/` 로 이관(260902).

---

# 개발자 · AI용 상세

> 아래는 도구 설계·성능·데이터 출처 등 기술 상세입니다. 사람용 개요는 위 표와 [[guide/architecture]]를
> 보세요. 각 도구 1페이지는 통일된 형식(아래 schema)을 따릅니다.

## 각 도구 페이지 통일 schema

```yaml
---
type: tool
title: <tool_name>
domain: data | action | reference   # 실제 쓰이는 값은 이 셋뿐 (260817 정리 — 아래 통계표가 SSOT)
scope: [...]                 # 지원 scope list
data_source: [...]           # DART API / KIND / Naver / 정적 JSON
related_disclosures: [...]   # rules/disclosures/ link
related_concepts: [...]      # rules/concepts/ link
related_decisions: [...]     # decisions/ link
related_audits: [...]        # architecture/audits/ link
created: 2026-05-01
---
```

본문 섹션: 1. 한 줄 요약 · 2. 사용법(자연어 예시) · 3. 입력 인자 · 4. 출력 schema · 5. Data sources
(호출 횟수) · 6. 파싱 전략(XML 단독·한계·regression audit) · 7~10. 관련 공시/개념/결정/audit
link · 11. 알려진 issue·TODO · 12. 변경 이력. (도메인 개념·공시 본문·정책은 본 폴더에 중복하지 않고
`rules/`·`decisions/`·`architecture/`로 link만.)

> **2026-05 정리**: screen_events drop · proxy_guideline archive · shareholder_meeting → notice+results
> 분리 · proxy_advise scope 10→1(specialized scope은 각 data tool 직접 호출).

## 카테고리별 통계

각 tool 페이지의 `domain:` 프론트매터가 근거다(합 31 = 런타임 tool 수, 260903
`dividend_history_data`+`dividend_screener` → `dividend_data` 통합으로 32→31). **표를 손으로
세지 말 것** — `scripts/check_tool_catalog.py` 가 이 합과 런타임을 대조한다.

| 도메인 | tool 수 | 무엇이 다른가 |
|--------|---------|---------|
| data | 25 | **DART(일부 KIND·KRX·ECOS)를 직접 읽어** 값을 만든다. 회사 식별(`company`)도 여기 — list/corpCode 조회다. API 1~14회 병렬 |
| action | 3 | **upstream data tool 을 불러 판단·요약**한다. `proxy_advise_before_meeting`(안건별 찬반) · `shareholder_commitment`(약속↔이행 대조, 신규 계산 1개 추가) · `screener`(전체시장 market-scan + hit 별 파서 디스패치) |
| reference | 3 | **회사·DART 무관 · API 0회.** `evidence`(접수번호→뷰어 URL) · `law_lookup`(법령 원문) · `proxy_guideline`(OPM 의결권 정책 원문) |

> 260817 정리: 이 표가 합 21 로 굳어 런타임 26 과 5 만큼 어긋나 있었다. 분류명(Company/Meeting/
> Data/Evidence/Action)이 프론트매터 `domain:` 과 별개 어휘라 어느 쪽도 갱신되지 않았다.
> **어휘를 `domain:` 하나로 합치고**, 미기재였던 3개(`order_contracts`·`shareholder_meeting_notice`·
> `shareholder_meeting_results`)를 `data` 로 채웠다. `evidence` 는 `data` 로 선언돼 있었지만
> **API 0회 · DART 무관**이라 `law_lookup` 과 같은 `reference` 로 옮겼다 — 옛 표도 이미 그 둘을
> 한 칸에 묶어두고 있었으니, 선언 쪽이 표를 못 따라간 것이었다.

## 진단 필드

주요 data/action tool은 `data.timings_ms`를 노출한다. 공통 키는 `total`, `resolve_company`이고,
tool별로 `scope.summary`, `fetch_decisions`, `decision_details`, `load_report_document` 같은 stage 키가
추가된다. 최근 3개 회사 실측 기준 반복 병목은 `dividend.decision_details`,
`treasury_share.fetch_decisions`였다(감사 기록은 private — open-proxy-storage). `value_up`은 `classify_value_up_roles`, `role_backfill_search.dart`로 plan/status/result/meta 분리
비용을 노출한다.

## 데이터 소스 매트릭스

| tool | DART API | KIND | Naver | 정적 JSON |
|------|----------|------|-------|----------|
| company | ✅ corpCode/company/list | - | 🔧 보강 | - |
| screener | ✅ list.json 전체시장 필러(corp_code 無) + details=유형별 파서 재사용 | - | 🔧 카드 링크 | ✅ krx_weekly 시총(DART 0콜) |
| shareholder_meeting_notice | ✅ list/document | - | - | - |
| shareholder_meeting_results | ✅ list/document | 🔧 fallback | - | - |
| ownership_structure | ✅ 사업보고서/majorstock | ✅ changes scope | - | - |
| dividend_disclosure | ✅ alotMatter | - | - | - |
| financial_metrics | ✅ fnlttSinglAcnt + Indx + AcntAll + audit | - | - | - |
| treasury_share | ✅ DS005 5종 | - | - | - |
| value_up | ✅ list/document | ✅ 0184 fallback | - | - |
| corp_gov_report | ✅ list/원문 | - | - | - |
| director_board | ✅ exctvSttus+drctrAdtAllMendngSttus 2종+개인별 · 사업보고서 원문(출석률·각주 해소·보수 산정기준 VIII-2) · 개인별5억+ API 하이브리드 교차검증 | - | - | - |
| asset_holdings | ✅ fnlttSinglAcntAll(계정) + otrCprInvstmntSttus(타법인출자) + get_document(III.주석) + stockPrice/stockTotal(시가마크) | - | - | - |
| corporate_restructuring | ✅ DS005 4종 병렬 | - | - | - |
| dilutive_issuance | ✅ DS005 4종 병렬 | - | - | - |
| corporate_deals | ✅ list+키워드 | - | - | - |
| order_contracts | ✅ list(I001) 단일판매·공급계약 키워드 + 원문 파싱 | - | - | - |
| risk_events | ✅ list(I001+B001)+키워드 | - | - | - |
| provisional_earnings | ✅ list(I001 결산잠정치·I002 공정공시) + 원문 파싱 | - | - | - |
| business_details | ✅ get_document 1콜(II.사업의 내용 + 주석 부문정보) + list(A001~A003) | - | - | - |
| financial_notes | ✅ get_document(III.재무에 관한 사항 주석·재무상태표) | - | - | - |
| price_multiple_data | ✅ 재무 4EP + company.json + fnlttSinglAcntAll + stockTotqySttus + alotMatter (firm) | - | - | ✅ Supabase 주간 스냅샷(market/sector/firm_history) · KRX 시세 · ECOS 환율 |
| trading_data | - | - | - | ✅ Supabase krx_weekly·krx_cap_agg·krx_adj_events·wise_sector (quote 만 KRX 라이브) |
| forward_estimates_data | - | - | - | ✅ Supabase `fwd` 컨센서스 스냅샷(벤더 원천, DART 아님) |
| dividend_data | - (전수 수집본·결정공시 집계 조회) | - | - | ✅ Supabase div_declared·div_quarterly(alotMatter 수집본) + div_payment·div_payment_scope(결정공시) + krx_listing + wise_sector |
| director_news | - | - | ✅ 뉴스 검색 API 1콜 | ✅ 부정 키워드 사전 |
| proxy_guideline | - | - | - | ✅ 패키지 데이터 open-proxy-guideline.md (API 0콜) |
| proxy_contest | ✅ D/B/I + document | ✅ vote_math whitelist | - | - |
| evidence | - | - | - | - (문자열 가공) |
| law_lookup | - | - | - | ✅ legalize-kr 법령 corpus 10법(4법 + 260902 확장 지배구조법·상증세법·금융지주회사법·금산법·은행법·보험업법) + 40룰 bridge |
| proxy_advise_before_meeting | upstream data tools | upstream | - | 판단 규칙/records |
| shareholder_commitment | ✅ value_up+corp_gov_report+dividend_disclosure+treasury_share+financial_metrics+stockTotqySttus (전부 재사용) | - | - | - |

✅ = 1차 source / 🔧 = 보조

## 흡수된 archive 페이지 (정보 출처)

본 17 페이지가 흡수·대체한 archive/analysis/ 자료:
- `company-tool-검증-예시` → `company.md` / `shareholder_meeting-tool-검증-예시` → `notice`·`results`
- `ownership_structure`·`dividend_disclosure`·`proxy_contest`·`value_up`·`evidence` 검증예시 → 각 동명 tool
- `corporate_restructuring-design`·`dilutive_issuance-design` → 각 tool / `related_party_transaction-design`
  → `corporate_deals` / `corp_gov_report-design` → `corp_gov_report`
- `cash-shareholder-return`·`total-shareholder-return` → archive 유지(CSR/TSR scope 제거)
- `release_v2-action-tool-검증-초안` → `proxy_advise_before_meeting` / `KIND-주총결과` → `results` fallback 이력

## 변경 이력
- 2026-05-01: 초기 tool 페이지 일괄 작성 + financial_metrics 신규
- 2026-05-18: 현재 16 public tool 체계로 정리(구 tool 명칭 제거)
- 2026-05-20: 도구–공시 매핑 페이지 추가 (260902 storage 이관 — 매트릭스와 frontmatter `data_source` 로 대체)
- 2026-05-31: financial_metrics 56 지표 확장 · value_up 역할 분리
- 2026-06-20: 카탈로그를 사람용("무엇을 답하나")으로 재정리 + 개발 상세 분리
- 2026-07-13: **law_lookup 신규(21번째 tool)** — 정관↔법령 양방향 조회. legalize-kr 원문 corpus(당시 4법: 상법·자본시장법·공정거래법·외부감사법) + 40룰 bridge, 회사·DART 무관. Evidence 카테고리 1→2
- 2026-09-03: **옛 도구명 `dividend` 잔재 정리 + 카탈로그 검사 확장** — 260902 개명 뒤 도구 설명 `ref:`·`when:` 16곳과 wiki 5곳이 없는 이름 `dividend` 를 가리키고 있었다(읽는 쪽이 LLM 이라 그대로 호출한다). 전부 `dividend_disclosure` 로. `check_tool_catalog.py` 가 이제 `ref:` 토큰을 런타임과 대조하고, 은퇴한 이름(`usage_tracker.TOOL_ALIASES` 키)이 설명에 서 있으면 실패한다. 같은 날 `dividend_data` 의 부분 장애 경로(매칭 수·원장·이력열 질의 하나만 실패)를 「없다」·예외 대신 「모른다」로 렌더하도록 고침.
- 2026-09-02: **law_lookup 코퍼스 4법 → 10법** — 금융회사 지배구조법·상증세법·금융지주회사법·금산법·은행법·보험업법 추가(조문 2,734 → 3,949). 법 우선순위·제목 앵커로 옛 4법 질의 정확도 유지(harness recall@10 85%).
- 2026-07-15: **screener 신규(22번째 tool)** — 전체시장 공시 스크리너 / 아침 디제스트. scan(전체시장 list.json market-scan, 하루 4콜)+details(유형별 파서 재사용). 시총=krx_weekly(DART 0콜)
- 2026-07-15: **screener `domain: action` 재분류** — Screening 카테고리를 Action으로 흡수(2→3). upstream 파서 오케스트레이션 + 디제스트/루틴 구동(액션 산출물). 루틴 레시피 [docs/routines](../../docs/routines/screener-morning-digest.md) 연동
- 2026-07-18: **business_details 신규(23번째 tool)** — "II.사업의 내용" 6필드(segments+사업장·가동률·rnd·수주·고객). markdown-primary.
- 2026-07-19: **business_details 확장** — D-트랙 3필드(financial_ops·financial_soundness·investment_property, KSIC 게이트) + 014 정정폴백 + reit_prose + `period=latest` 기본(사업·반기·분기 최신).
- 2026-07-19: **provisional_earnings 신규(24번째 tool)** — 영업(잠정)실적(I002 공정공시) 분기 잠정 매출·영업익·순익+YoY. financial_metrics 확정치보다 먼저. markdown-primary(table_markdown) + best-effort headline. 자동차 판매대수·조선 수주 등 비재무형 커버. screener `detail_kind=earnings` 연동.
- 2026-07-20: **asset_holdings 신규(25번째 tool)** — 자산주·NAV 스크리닝(계정 티어 + 상장지분 시가마크 + 시총 대비 배수). business_details의 자산가치 opt-in 필드에서 분리. 2스콥(summary·detail). Data 12→13.
- 2026-07-21: **business_details 확장** — `bsns_year`+`reprt_code`(DART 표준 11011/11012/11013/11014)로 특정 과거 시점 1건 조회 추가(`period`는 최신 스냅샷 전용, 시계열은 분기마다 반복 호출). 절대월 하드코딩 없이 `report_nm` 기수라벨 상대순서로 1/3분기 구분(비12월 결산법인 안전). [[260717_1220_decision_business-content-tool-roadmap]] 스코프 확장 참조.
- 2026-07-22: **getting_started 제거(26→25 tool, Discovery 카테고리 폐지)** — capability 질문은 MCPServer `instructions` 의 서버 오리엔테이션 + 각 tool desc 로 답한다(클라이언트 모델이 tool 목록을 직접 읽는다).
- 2026-07-23: **business_details 확장** — `raw_materials`(원재료 구성·매입 + 원재료 가격 추이)와 `product_pricing`(제품·서비스 가격·ASP·변동 원인) 추가. 전자는 두 소절을 독립 경계로 회수해 한 사업부의 기재 생략이 다른 유효 표를 덮지 않게 했고, 후자는 별도 가격 소절로 반환한다.
- 2026-08-03: **business_details·asset_holdings 응답 계약 확장** — 부재를 `absence_kind` 넷(`not_disclosed`·`cross_reference`·`narrative_only`·`extraction_failed`)으로 가르고 `absence_note` 에 근거를 싣는다. 값이 있을 때는 **원문 위치**(그 회사의 소절 제목 / 주석 원문 문구 인용)를 함께 낸다. `asset_holdings` 주석 4필드에 **연결/별도 기준**(`basis`)과 불일치 경고(`basis_conflict`) 추가 — 문서가 셀마다 선언한 XBRL 컨텍스트를 읽고, 선언이 없으면 기준을 내지 않는다.
- 2026-08-06: `wiki/tools/` 전반의 과정 서사를 현재형 설계 근거로 정리하고 회고는 private storage 로(경계 규칙 [[wiki_schema]] 0.0).
