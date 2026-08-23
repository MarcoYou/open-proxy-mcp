---
type: readme
title: tools/ — 도구 카탈로그
updated: 2026-07-22
---

# 도구(Tool) 카탈로그

> OPM의 런타임 도구 목록입니다. 도구마다 **답해주는 정보가 다릅니다**. AI 에이전트는 질문에 맞는
> 도구를 스스로 골라 호출합니다. 사용자는 "○○기업 분석해줘"처럼 자연어로 물어보면 됩니다.
>
> 👤 처음이라면 → **[[guide/README]]** (사람용 안내서) · 시스템 동작은 [[guide/architecture]]
> 각 도구의 입력·출력·데이터 출처는 도구 이름을 클릭하면 나옵니다.

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
| [trading_data](trading_data.md) | 거래·**규모** — 주가·시총·상장주식수 시계열, 시장·섹터 시총 집계, 단일시점 시세(OHLC·거래량) |
| [corp_gov_report](corp_gov_report.md) | 기업지배구조보고서 — 15개 핵심지표 준수 여부 + 서식 표 11종(이사회 구성·출석률·겸직·안건별 찬반) |
| [director_board](director_board.md) | 이사회/개별 이사 — 인당보수·보수한도 소진율·재직/사퇴 변동·개별보수·미등기·이사회 출석률·원문 각주 해소·보수 산정기준(pay_criteria, 정형API 하이브리드 검증) |

### 🎁 주주환원 · 자본
| 도구 | 무엇을 답하나 |
|---|---|
| [dividend](dividend.md) | 배당 — 배당금·총액·배당성향·추이 |
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
| [law_lookup](law_lookup.md) | 정관↔법령 양방향 조회 — 정관 조항/키워드 → 관련 법령 조문(전문), 또는 법조문 → 관련 정관 변경유형·우회·안건. 상법·자본시장법·공정거래법·외부감사법 원문. **회사·DART 무관** |

> 📊 도구–공시 채널 매핑(시각 자료): [[tool_disclosure_map]] · [[data_tool_disclosure_map]]
> 📞 도구별 DART 콜 budget(기업당 최대 콜·유니버스 배치 안전 크기): [[tool_call_budget]]

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

각 tool 페이지의 `domain:` 프론트매터가 근거다(합 29 = 런타임 tool 수). **표를 손으로 세지 말 것** —
`scripts/check_tool_catalog.py` 가 이 합과 런타임을 대조한다.

| 도메인 | tool 수 | 무엇이 다른가 |
|--------|---------|---------|
| data | 23 | **DART(일부 KIND·KRX·ECOS)를 직접 읽어** 값을 만든다. 회사 식별(`company`)도 여기 — list/corpCode 조회다. API 1~14회 병렬 |
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
`treasury_share.fetch_decisions`였으며 상세 근거는 `architecture/audits/260510_data_tools_perf_audit.md`
참조. `value_up`은 `classify_value_up_roles`, `role_backfill_search.dart`로 plan/status/result/meta 분리
비용을 노출한다.

## 데이터 소스 매트릭스

| tool | DART API | KIND | Naver | 정적 JSON |
|------|----------|------|-------|----------|
| company | ✅ corpCode/company/list | - | 🔧 보강 | - |
| screener | ✅ list.json 전체시장 필러(corp_code 無) + details=유형별 파서 재사용 | - | 🔧 카드 링크 | ✅ krx_weekly 시총(DART 0콜) |
| shareholder_meeting_notice | ✅ list/document | - | - | - |
| shareholder_meeting_results | ✅ list/document | 🔧 fallback | - | - |
| ownership_structure | ✅ 사업보고서/majorstock | ✅ changes scope | - | - |
| dividend | ✅ alotMatter | - | - | - |
| financial_metrics | ✅ fnlttSinglAcnt + Indx + AcntAll + audit | - | - | - |
| treasury_share | ✅ DS005 5종 | - | - | - |
| value_up | ✅ list/document | ✅ 0184 fallback | - | - |
| corp_gov_report | ✅ list/원문 | - | - | - |
| director_board | ✅ exctvSttus+drctrAdtAllMendngSttus 2종+개인별 · 사업보고서 원문(출석률·각주 해소·보수 산정기준 VIII-2) · 개인별5억+ API 하이브리드 교차검증 | - | - | - |
| asset_holdings | ✅ fnlttSinglAcntAll(계정) + otrCprInvstmntSttus(타법인출자) + get_document(III.주석) + stockPrice/stockTotal(시가마크) | - | - | - |
| corporate_restructuring | ✅ DS005 4종 병렬 | - | - | - |
| dilutive_issuance | ✅ DS005 4종 병렬 | - | - | - |
| corporate_deals | ✅ list+키워드 | - | - | - |
| risk_events | ✅ list(I001+B001)+키워드 | - | - | - |
| proxy_contest | ✅ D/B/I + document | ✅ vote_math whitelist | - | - |
| evidence | - | - | - | - (문자열 가공) |
| law_lookup | - | - | - | ✅ legalize-kr 법령 corpus (상법·자본시장법·공정거래법·외부감사법) + 40룰 bridge |
| proxy_advise_before_meeting | upstream data tools | upstream | - | 판단 규칙/records |
| shareholder_commitment | ✅ value_up+corp_gov_report+dividend+treasury_share+financial_metrics+stockTotqySttus (전부 재사용) | - | - | - |

✅ = 1차 source / 🔧 = 보조

## 흡수된 archive 페이지 (정보 출처)

본 17 페이지가 흡수·대체한 archive/analysis/ 자료:
- `company-tool-검증-예시` → `company.md` / `shareholder_meeting-tool-검증-예시` → `notice`·`results`
- `ownership_structure`·`dividend`·`proxy_contest`·`value_up`·`evidence` 검증예시 → 각 동명 tool
- `corporate_restructuring-design`·`dilutive_issuance-design` → 각 tool / `related_party_transaction-design`
  → `corporate_deals` / `corp_gov_report-design` → `corp_gov_report`
- `cash-shareholder-return`·`total-shareholder-return` → archive 유지(CSR/TSR scope 제거)
- `release_v2-action-tool-검증-초안` → `proxy_advise_before_meeting` / `KIND-주총결과` → `results` fallback 이력

## 변경 이력
- 2026-05-01: 초기 tool 페이지 일괄 작성 + financial_metrics 신규
- 2026-05-18: 현재 16 public tool 체계로 정리(구 tool 명칭 제거)
- 2026-05-20: 도구–공시 매핑 [[data_tool_disclosure_map]] 추가
- 2026-05-31: financial_metrics 56 지표 확장 · value_up 역할 분리
- 2026-06-20: 카탈로그를 사람용("무엇을 답하나")으로 재정리 + 개발 상세 분리
- 2026-07-13: **law_lookup 신규(21번째 tool)** — 정관↔법령 양방향 조회. legalize-kr 원문 corpus(상법·자본시장법·공정거래법·외부감사법) + 40룰 bridge, 회사·DART 무관. Evidence 카테고리 1→2
- 2026-07-15: **screener 신규(22번째 tool)** — 전체시장 공시 스크리너 / 아침 디제스트. scan(전체시장 list.json market-scan, 하루 4콜)+details(유형별 파서 재사용). 시총=krx_weekly(DART 0콜)
- 2026-07-15: **screener `domain: action` 재분류** — Screening 카테고리를 Action으로 흡수(2→3). upstream 파서 오케스트레이션 + 디제스트/루틴 구동(액션 산출물). 루틴 레시피 [docs/routines](../../docs/routines/screener-morning-digest.md) 연동
- 2026-07-18: **business_details 신규(23번째 tool)** — "II.사업의 내용" 6필드(segments+사업장·가동률·rnd·수주·고객). markdown-primary.
- 2026-07-19: **business_details 확장** — D-트랙 3필드(financial_ops·financial_soundness·investment_property, KSIC 게이트) + 014 정정폴백 + reit_prose + `period=latest` 기본(사업·반기·분기 최신).
- 2026-07-19: **provisional_earnings 신규(24번째 tool)** — 영업(잠정)실적(I002 공정공시) 분기 잠정 매출·영업익·순익+YoY. financial_metrics 확정치보다 먼저. markdown-primary(table_markdown) + best-effort headline. 자동차 판매대수·조선 수주 등 비재무형 커버. screener `detail_kind=earnings` 연동.
- 2026-07-20: **asset_holdings 신규(25번째 tool)** — 자산주·NAV 스크리닝(계정 티어 + 상장지분 시가마크 + 시총 대비 배수). business_details의 자산가치 opt-in 필드에서 분리. 2스콥(summary·detail). Data 12→13.
- 2026-07-21: **business_details 확장** — `bsns_year`+`reprt_code`(DART 표준 11011/11012/11013/11014)로 특정 과거 시점 1건 조회 추가(`period`는 최신 스냅샷 전용, 시계열은 분기마다 반복 호출). 절대월 하드코딩 없이 `report_nm` 기수라벨 상대순서로 1/3분기 구분(비12월 결산법인 안전). [[260717_1220_decision_business-content-tool-roadmap]] 스코프 확장 참조.
- 2026-07-22: **getting_started 제거(26→25 tool, Discovery 카테고리 폐지)** — capability 질문은 FastMCP `instructions` 의 서버 오리엔테이션 + 각 tool desc 로 답한다(클라이언트 모델이 tool 목록을 직접 읽는다). 설계 기록 [[260721_1600_decision_getting-started-tool-vs-resource]].
- 2026-07-23: **business_details 확장** — `raw_materials`(원재료 구성·매입 + 원재료 가격 추이)와 `product_pricing`(제품·서비스 가격·ASP·변동 원인) 추가. 전자는 두 소절을 독립 경계로 회수해 한 사업부의 기재 생략이 다른 유효 표를 덮지 않게 했고, 후자는 별도 가격 소절로 반환한다.
- 2026-08-03: **business_details·asset_holdings 응답 계약 확장** — 부재를 `absence_kind` 넷(`not_disclosed`·`cross_reference`·`narrative_only`·`extraction_failed`)으로 가르고 `absence_note` 에 근거를 싣는다. 값이 있을 때는 **원문 위치**(그 회사의 소절 제목 / 주석 원문 문구 인용)를 함께 낸다. `asset_holdings` 주석 4필드에 **연결/별도 기준**(`basis`)과 불일치 경고(`basis_conflict`) 추가 — 문서가 셀마다 선언한 XBRL 컨텍스트를 읽고, 선언이 없으면 기준을 내지 않는다.
- 2026-08-06: `wiki/tools/` 전반의 과정 서사를 현재형 설계 근거로 정리하고 회고는 private storage 로(경계 규칙 [[wiki_schema]] 0.0).
