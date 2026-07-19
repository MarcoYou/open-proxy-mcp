---
type: tool
title: business_details
domain: data
scope: [segments, sites, utilization, rnd, backlog, customers, financial_ops, financial_soundness, investment_property]
data_source: [DART get_document (전체보고서 XML 1콜 → II.사업의 내용 + 연결재무제표주석 부문정보 슬라이스), search list.json A001/A002/A003]
related_disclosures: [사업보고서, 분기보고서, 반기보고서]
related_concepts: [사업부문, 영업부문, K-IFRS 1108, SOTP, 부문 영업이익, 연구개발비, 수주잔고, 고객집중]
related_decisions: [260717_1220_decision_business-content-tool-roadmap, XML-vs-PDF, ksic-sector-mapping]
created: 2026-07-18
---

# business_details

## 한 줄 요약
DART 사업보고서 **"II. 사업의 내용"**에서 **① 사업부문별 매출·영업이익 ② 사업장·생산설비 ③ 생산실적·가동률 ④ 연구개발 ⑤ 수주현황 ⑥ 주요 고객·매출처**를 추출. SOTP·부문 수익성·생산능력·수주잔고·고객집중 분석의 1차 소스. 286사 census + 재무·공시·산업 3전문가 QA로 검증.

## 사용법
- `business_details(company, period="latest", fields="", format="md")`
- `period`: **`latest`(기본, 사업·반기·분기 중 가장 최신 제출분=최신 데이터)** / `annual`(연간 사업보고서 고정) / `quarterly`(분기·반기 고정). 응답 `report.report_nm`으로 어느 보고서인지 확인. II.사업의내용은 분기/반기도 완전구조라 동일 필드(사업의내용_ksic별양식 참조).
- `fields`: 쉼표구분 선택(`segments,sites,utilization,rnd,backlog,customers`, 미지정 시 전체). **특정 필드만 지정하면 응답이 가벼움**(전체는 대형주 ~35K자).
- 예: `business_details("에코프로비엠", fields="utilization")` · `business_details("HD한국조선해양", fields="backlog")`

## 필드별 가이드 (직접 테스트용)
| 필드 | 읽는 소절 | 무엇이 나오나 | 테스트 예시 |
|---|---|---|---|
| **segments** | 영업부문 주석(K-IFRS 1108) | 부문별 매출·영업이익 (정형표 or 원문 마크다운) | 삼성전자(정형)·SK하이닉스(후보)·삼일(마크다운) |
| **sites** 사업장 | 3.원재료 및 생산설비 / 사업장 현황 | 공장·사업장 소재지·면적 원문(유통은 점포) | 삼성전자(공장 주소)·이마트(점포 창동점) |
| **utilization** 가동률 | 생산실적 및 가동률 | 생산능력·생산실적·가동률 원문(+% 힌트) | SK하이닉스(100%)·에코프로비엠(생산능력 톤) |
| **rnd** 연구개발 | 연구개발활동 | 연구개발비·실적·조직 원문(+매출대비% 힌트) | 유한양행·한국항공우주 |
| **backlog** 수주 | 4.매출 및 수주상황 | 수주잔고·수주계약 원문(조선 flow표 포함) | HD한국조선해양(기초계약잔액→수주잔고) |
| **customers** 고객 | 주요 매출처 / 주요 고객 주석 | 주요 고객·매출처·판매경로 원문(익명 다수) | 삼성전자·유한양행 |
| **financial_ops** (금융) | 2.영업의 현황 | 영업개황·영업실적·**영업부문별 재무정보**(금융판 segments) | 신한지주·미래에셋증권 |
| **financial_soundness** (금융) | 재무건전성·지급여력 | RBC·지급여력비율(K-ICS)·순자본비율·연체율 | 삼성생명·우리금융지주 |
| **investment_property** (REIT/보험) | 투자부동산 내역·투자자산 개요 | 부동산 목록·임대율·임대면적·공실 | SK리츠·삼성생명 |
| **real_estate** (opt-in) | III.재무 주석 유형자산·투자부동산 명세 | 토지 장부가(취득원가/총장부금액/**변동표 기초~기말**) + 재평가적립금 + 토지 공정가치/공시지가 원문(**단위 always-carry**) | 경방(재평가 5,522억≫토지 1,278억)·태광·성창기업 |
| **equity_holdings** (opt-in) | III.재무 지분증권 명세(FVPL/FVOCI 보강) | 상장/비상장 지분증권 취득원가 vs 공정가치·평가손익. **표준 소스는 타법인출자현황 API**(`otrCprInvstmntSttus`) — 이 재무주석 필드는 트레이딩 포트폴리오 보강용 | 삼성전자·신세계·태광 |

> **자산가치 스콥(260719, opt-in)** — 토지·투자부동산·지분증권의 장부가·공정가치 원문 반환. `real_estate`=토지·투자부동산 장부가+재평가+공정가치, `equity_holdings`=지분증권 원가vs시가. **명시해야 실행**(full html 스캔·기본 want 제외). 소스=III.재무 주석(D-트랙 investment_property가 배제하는 그 영역과 정반대). **DART 중첩 <table> 파싱 실패**(경방 grid=헤더만·데이터행 nested) → table 파싱 포기, **stripped 텍스트 region 윈도를 마크다운으로**(markdown-primary 순수형, 텍스트엔 데이터 다 있음). 순수-lookahead content-signature로 산문 회계정책·BS 한줄·CF조각·종속/관계기업투자(지분법, 시가 아님) 배제. 성능=프리strip 1회 + region window(SK 22MB 0.9s). **KOSPI 자산주+KOSDAQ+엣지 23사 멀티에이전트 QA: 토지명세 23/23 정확·재평가/공시지가/투자부동산 오발 0** (markdown-primary-anchor-260719). 한계: 서식변형 커 region 앞부분 over-capture(관계기업/소유구조 산문 bleed, 데이터는 정확)·재평가 region이 자본변동표 광범위 캡처(값은 포함)·전방류 상장/비상장 미표기 지분상품 미스(정밀도 우선).
>
> **하드닝 업데이트(260719)**: ① `real_estate` 토지 sig를 **유형자산 변동표(기초~기말 롤포워드)** 대응으로 확장(dominant miss) — 전수 회귀0·복구 187(79→86%). ② region이 단위선언 위를 잘라 스케일 유실 → **근접 지배 단위 백필**(real_estate 100%). ③ 성능: 투자부동산 sig 프리필터 + _strip 1회공유 = **1.5x, 회귀0**. ④ 총액 확인은 재무제표 계정 API(`fnlttSinglAcntAll`)가 정본 — 주석 필드는 명세·원가vs공정가치 breakdown 용도.

> **D-트랙 금융·REIT(260718)**: 마지막 3필드는 금융/증권/보험/지주·REIT용. segments는 금융폼 `UNSUPPORTED_FORM`이므로 `financial_ops`의 영업부문별 재무정보가 대체.
> **KSIC(업종코드) 게이트**(사용자 제안 260718) — content 마커만이면 카카오(포털)·한화(화약)·아모레퍼시픽(화장품)이 자회사/우발 신호로 오발. → `get_company_info`의 `induty_code`로 **금융권(KSIC 64/65/66)일 때만** financial 필드, **부동산(68)/보험(65)/지주(64)**일 때만 investment_property. 비금융은 아예 시도 안 함(오발 0). **지주회사 64992는 충돌**(신한금융 vs SK)이라 그 안에서만 content-signature로 판별. 응답에 `induty_code` 동봉.
> **헤딩 앵커 + 내용 시그니처 폴백 이중구조** — 금융/REIT는 서식이 덜 표준화돼, 헤딩 키워드가 미스해도 **데이터 시그니처(순이자손익·지급여력비율·임대율 등)로 표를 찾아** 렌더(헤딩 라벨보다 안정적). `source=heading|signature`로 어느 경로인지 표기.
> **D-트랙은 II.사업의내용 구간만 스캔**(`_biz_html_region`) — 시그니처 폴백이 full html을 훑으면 **III.재무 주석의 회계표**(공정가치 서열체계·투자부동산 장부금액 헤더조각)를 REIT 투자부동산으로 **오발**(NH올원·이리츠코크렙: 1095·398자 회계각주). II→III 경계는 **목차 stub(수십자) skip 후 첫 실질구간(>2000자)**으로 잡는다(max-span 금물 — 한화생명류는 말미에 종속사 사업보고서가 embedded돼 그 span이 본문보다 커 오선택→본문 DP Real Estate·종속REIT 서술 통째 누락). II 제한 후 NH올원·이리츠는 오히려 II 프로즈(reit_prose 7203·2123자)로 정상 회수, 한화생명은 종속 부동산 서술 유지, 은행·증권 financial 필드·정상 REIT 전부 불변.
> **지주형 REIT 프로즈 폴백**(`source=reit_prose`) — 명목회사(해외리츠 등)는 표준(제조)폼에 부동산을 **서술형**으로 싣는다(제이알글로벌리츠: 파이낸스타워(벨기에)·498 Seventh Avenue를 '2.주요 제품 및 서비스→영업개황'에 임대료·WALE·임차율 프로즈로. 전용 투자부동산 헤딩·표 없음). 전용 헤딩/시그니처가 다 실패할 때만 표준폼 헤딩(주요 제품 및 서비스·영업개황·회사의 현황)을 시도하되 **content-gate 강화**(임대료/임대차/임차 + 부동산/투자대상/WALE 동반)로 보험 영업개황 등 오섹션을 차단. 작동하는 REIT는 전용경로에서 이미 반환돼 영향 없음(신한알파·SK·롯데·ESR 전부 `source=heading` 유지 확인).

> **핵심: segments 외 필드는 "markdown-primary"** — 도구가 값을 판정하지 않고 **해당 소절 원문을 마크다운 표로 통째 반환**합니다. 값·단위·정의 판단은 **읽는 AI(=당신의 Claude)의 몫**. 회사마다 단위·정의가 달라(가동률 %/시간/톤, 사업장 주소~국가) 원문을 봐야 정확하기 때문(자세히 아래 파싱전략).

## 출력 (ToolEnvelope.data)
- `form_type`: `standard7` / `financial5` / `reit` / `dual` (목차 소절 제목 기반 판별, KSIC 불신)
- `segments`:
  - `status=OK` + `source=deterministic` → `items:[{name, revenue, profit}]` + `reconciliation`(sum-check)
  - `status=NEEDS_REVIEW` + `source=note_markdown`/`biz_markdown` → `segment_note_md`(영업부문 주석 원문 마크다운) → **호출측 LLM이 읽어 추출**. 앵커 실패 시 `source=raw_candidates` + `candidates:[{rendered, score}]`
  - `status=NOT_APPLICABLE` → 단일부문(정상)
  - `status=UNSUPPORTED_FORM` → 금융폼·REIT(v1 미지원, D-트랙 별도)
- **추가 필드 5종(markdown-primary, 260718 census+QA패널 결정)** — 각 `{status, markdown, na_reason}`:
  - `sites`(사업장·생산설비) · `utilization`(생산실적·가동률, +`pct_hint`) · `rnd`(연구개발, +`ratio_to_sales_pct_hint`) · `backlog`(수주현황) · `customers`(주요 고객·매출처)
  - `status=MARKDOWN` → 해당 소절 원문을 마크다운으로(`markdown`) → **호출측 AI가 읽어 값 추출**(단위·정의 회사별 상이). `status=NOT_APPLICABLE` → 소절 부재/기재생략(`na_reason`).
  - **파서가 값 판정 안 함**: 사업장 유형자산 장부가 함정·가동률 단위카오스·수주 flow표 오귀속·rnd 회계처리/보조금·customers 다위치 충돌은 호출측 AI가 원문 읽어 판별(QA패널 BLOCKER 대응).
- `timings_ms`(단계별 병목)

## Data sources
DART **`get_document`(전체 보고서 XML) 1 API콜**([[XML-vs-PDF]]) → text에서 `II.사업의 내용`·`연결재무제표 주석`(별도 heading 전까지) 슬라이스(`_slice_getdoc_sections`), html은 후보표 스캔용 원본. viewer 3웹콜(~5s) 대비 **~3x 빠름**. **PDF/OCR·내부 LLM·pandas 불필요.**

**014(document.xml 부재) 폴백 — 정정보고서 처리**: 최신 정기보고서가 **첨부/기재정정**이면 그 정정본의 `document.xml`이 부재해 `get_document`가 DART 014를 낸다. 첨부정정은 **첨부(감사보고서 등)만 갱신**하고 II.사업의 내용 본문은 담지 않으므로(첨부정정은 viewer 노드트리도 정정표지 9개뿐) — `_find_report_candidates`가 전 후보를 rcept_dt 내림차순 반환하고, 014 시 **동일 기수(`(2025.12)` 라벨 일치) 원본 보고서로 get_document 폴백**한다(작년 기수로는 폴백 안 함). 실측: KB금융(최신 `[기재정정]` 014 → 하루 전 원본 1.66M), 삼성화재(`[첨부정정]` 014 → 원본 1.08M) 모두 영업·건전성 정상 회수. 폴백 사실은 `warnings` + `data.fetch_method="get_document(정정폴백)"`로 투명 기록. 동일기수 원본도 전부 014면 **최종적으로 viewer 웹fetch 폴백**(`_fetch_viewer_sec`, 극소수·느림), 그것도 실패 시 graceful ERROR.

## 파싱전략 (핵심 — [[260717_1220_decision_business-content-tool-roadmap]])
flatten이 2D표를 1D로 뭉개 정렬이 깨지는 게 근본 난제(156 census 실증: 정형 신뢰 ~91%가 천장).
**설계 결정(260718, 사용자)**: MCP tool은 이미 호출측 LLM이 부르므로 **내부 LLM 불필요** — tool은 기계적으로 좁히고 의미 추출은 호출측이.
- **① 정형(flatten)** — 본문표+주석표를 **둘 다 파싱해 교차검증**(`_seg_names_agree` 60%겹침): 불일치=지주사가 본문에 자회사표 실은 케이스 → `cross_conflict`로 후보강등(주석=K-IFRS 1108 권위). 통과 시 clean 게이트 후 구조화 반환(공짜, 대형주 값정확). 게이트=junk명·**지역정보표(국내/해외/외국/'본사 소재지 국가')**·**매출유형(제품/상품매출액)**·**비기타부문 음수매출**·`_scrub_segments`(값없는행·'감가상각비'/'연결 후 금액'/'3)비유동자산' 재무라인 제거). ~300사(156+제조145) 검증: 정형OK 64사 육안 clean → best-effort 빠른힌트.
- **② 저신뢰/실패 → 영업부문 주석 원문을 마크다운으로 통째 반환**(260718 사용자 결정): '어느 표인지' 점수매기는 파서 대신, `render_segment_note_markdown`이 **'N. 영업부문' 번호 헤딩을 앵커**로 주석 구간을 잡아 설명문단+표 전부를 **깔끔한 마크다운 표**로 렌더 → 호출측 AI가 읽어 추출(값 억지추출 X). 단일선언(`_SINGLE_DECL_RE`) 구간은 스킵(단일사 noise 방지), 앵커 실패 시 **II.사업의 내용 마크다운 폴백**(지주사류), 그것도 없으면 후보표(bs4). 이 마크다운 회수로 **정형이 못 뽑던 하드케이스(액트로·아미노로직스·삼일=2D표·부문명만·수익유형) 전부 surfaced** (~300사 검증 MISS 0). `_is_roster`로 임원명부 배제.
- **③ N/A** — 후보표 0개(단일부문·표부재).
- **폼 게이트**: `detect_form`에서 **has_mfg(주요제품·원재료 소절)가 REIT/금융 veto** — 유통사가 리츠 자회사 보유해 프로즈에 '부동산투자회사' 있어도 표준폼 유지(롯데쇼핑 회귀 fix). 진짜 금융지주·REIT만 `UNSUPPORTED_FORM`(D-트랙).

**성능**: get_document 1콜이 지배(미캐시 ~2-2.5s, 캐시히트 150-470ms). 후보 스캔(Afields)은 정규식 프리필터로 <150ms(POSCO 9.7MB도). 단계별 `timings_ms`(resolve/search/fetch/segment/Afields/total)로 실측.

## 추가 5필드 markdown-primary 구현 (`services/biz_fields.py`)
- `render_biz_subsection_markdown(html, kw_patterns, content_re)` — II.사업의 내용 특정 소절을 마크다운 렌더. **번호/한글자 접두 헤딩**만 앵커(프로즈 오탐 방지) + **content-gate**(렌더 구간이 실제 그 필드 내용 담을 때만 채택 — 부모/오섹션 오탐 차단) + 요약+상세(XII.상세표) 최대 2구간 + `_is_roster`(임원명부) 배제.
- 각 필드 = 헤딩패턴 + content_re + 최소 hint. **파서 판정 없음** = QA BLOCKER(사업장 유형자산함정·수주 flow표 오귀속·rnd 회계처리/보조금·customers 다위치) 를 "AI가 원문 읽어 판별"로 해소.

## 실사용 검증 + 알려진 한계
- **오탐(false-MD) 0** — 실 DART 300사 스윕: content-gate 도입 전 사업장 52·수주 37·가동률 7 오탐(부모헤딩이 매출/투자현황 오렌더) → gate로 **전 필드 0**.
- **조선 수주잔고 검증** — HD한국조선해양·HD현대중공업·대한조선 backlog가 flow표(기초계약잔액→신규→기납품→**수주잔고**, 산식 포함) 전체 캡처 확인. value-hint 안 내므로 오귀속 없음.
- **슬라이서 버그픽스** — `_slice_getdoc_sections`가 목차(TOC) stub 대신 최대 span II→III 슬라이스 → 대형사 48/155 body 회복(SK하이닉스·한화솔루션). 기존 segments도 OK 35→39 개선.
- **제약 가동률 헤딩 확대(260718 검증)** — 제약사는 가동률을 제목이 아니라 **표 라벨**(자간삽입 "가 동 률")로 싣고 헤딩은 "생산능력 및 (생산)실적"·"평균 가동 시간"을 쓴다(한미약품·파마리서치). `_UTIL_HEAD`에 이 헤딩을 추가 — content-gate `_C_UTIL`(가동률/가동시간, 자간 허용 `가\s*동`)이 실제 가동 데이터를 담을 때만 렌더하므로 오발 0(제약바이오 30사 회귀: 한미·파마리서치 N/A→md, 나머지 불변). 자간삽입으로 literal 문자열검색이 속으니 검증은 정규식·content-gate로.
- **유통·서비스·건설 사업장 헤딩 확대(260718 5산업군 스캔)** — 업종별로 사업장 소절명이 다르다: 편의점 "영업설비"(BGF리테일 점포현황), 호텔·면세 "영업장 현황"(호텔신라), 건설 "주요 설비의 현황"(대우건설·DL이앤씨 토지/건물 소재지). `_SITE_HEAD`에 `영업\s*설비`·`영업장\s*현황`·`주요\s*설비의?\s*현황`·`설비의?\s*현황`(무접두 '가. 설비 현황'=넷마블 사업장별 소재지表) 추가 — content-gate `_C_SITE`(소재지·점포·㎡ 등)로 7사 N/A→md(카카오·HLB·넷마블 포함), sites-only 변화·타필드 불변. 지주/유통 util=md는 오발 아님(종속 제조/통신사 실가동률: 현대백화점=지누스, GS리테일=후레쉬서브, SK=SK온, LG=LG유플러스). "(제조서비스업)/(금융업)" 업종 프리픽스 헤딩(카카오·HLB)은 진단결과 **무의미**(prefixed 헤딩=KSIC-off 금융필드거나·HEAD에 없거나·이미 non-prefixed로 잡힘).
- **content-주도 폴백 검증 후 기각(260718)** — "헤딩 대신 content-gate로 서브섹션 지목" 아이디어를 N/A 29사(sites 9 + util 20)에 프로토타입으로 실측: **util은 0/20 회수(전부 진짜 무내용=정상 N/A 확증)**, **sites는 5중 4가 오발**(현대건설→'주요 제품 및 서비스', 유한양행→제품표, 크래프톤→'환율변동 위험', 리가켐→'옵션계약' — `_C_SITE`의 지역명·해외가 제품/위험/계약 섹션에 스침). **결론: content-gate는 확인(confirmation) 필터지 지목(identification) 필터가 아니라, 단독 지목 시 오발** → 헤딩앵커+content-gate 2단 유지가 정답. 진짜 미스 1건(넷마블 '설비 현황')만 헤딩 추가로 해결.
- **한계**: ① 비정형 가동률(KX 송출 가동율 등 소수)은 앵커 미스로 N/A 가능(markdown-primary 한계, 수용) ② 금융지주·REIT는 segments `UNSUPPORTED_FORM`(D-트랙 별도) ③ customers 고객명 다수 익명(주요고객A/B) — 이름 억지생성 안 함 ④ 전체 필드 응답 대형주 ~35K자(특정 fields 지정 권장).

## 관련
- [[260717_1220_decision_business-content-tool-roadmap]] (설계·실현가능성·스콥·아키텍처)
- 사업의내용_ksic별양식 (KOSPI500 census — 업종별 소절 양식·정보 존재율·헤딩 variant 사전)
- [[ksic-sector-mapping]] (KSIC 한계 — 폼 판별에 불신)
- [[XML-vs-PDF]] (viewer HTML 단독)
- `wiki/_local/census-biz-content-260717/` (156사 segment census 원본·ground-truth·재현 스크립트, gitignore)
- `wiki/_local/census-fields-260718/` (286사 5필드 census + 재무·공시·산업 3전문가 QA 합성 결과 `synthesis_qa_full.json`, gitignore)
