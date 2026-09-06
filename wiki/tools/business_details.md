---
type: tool
title: business_details
domain: data
scope: [revenue_breakdown, sites, utilization, rnd, backlog, customers, raw_materials, product_pricing, financial_ops, financial_soundness, investment_property, key_contracts]
data_source: [DART get_document (전체보고서 XML 1콜 → II.사업의 내용 + 연결재무제표주석 부문정보 슬라이스), search list.json A001/A002/A003]
related_disclosures: [사업보고서, 분기보고서, 반기보고서]
related_concepts: []
related_decisions: [260717_1220_decision_business-content-tool-roadmap, XML-vs-PDF, ksic-sector-mapping]
created: 2026-07-18
updated: 2026-09-06
---

# business_details

## 한 줄 요약
DART 사업보고서 **"II. 사업의 내용"**에서 **① 사업부문별 매출·영업이익 ② 사업장·생산설비 ③ 생산실적·가동률 ④ 연구개발 ⑤ 수주현황 ⑥ 주요 고객·매출처 ⑦ 원재료·투입원가 ⑧ 제품·서비스 가격 추이**를 추출. SOTP·부문 수익성·생산능력·수주잔고·고객집중·마진 분석의 1차 소스.

## 이렇게 물어보세요

> "에코프로비엠 생산능력이랑 가동률 어떻게 돼?"
>
> "HD한국조선해양 수주잔고 얼마나 쌓여 있어?"
>
> "삼성전자 사업부문별 매출이랑 영업이익 나눠서 보여줘"

(`docs/features/` 의 같은 예시 — 자연어로 물으면 AI 가 이 도구를 고른다.)

## 사용법
- `business_details(company, period="latest", fields="", format="md", bsns_year="", reprt_code="", context_mode="strict", context_chars=20000)`
- `period`: **`latest`(기본, 사업·반기·분기 중 가장 최신 제출분=최신 데이터)** / `annual`(연간 사업보고서 고정) / `quarterly`(분기·반기 고정). 응답 `report.report_nm`으로 어느 보고서인지 확인. II.사업의내용은 분기/반기도 완전구조라 동일 필드(사업의내용_ksic별양식 참조). `bsns_year`+`reprt_code` 지정 시 무시됨.
- `fields`: 쉼표구분 선택(`segments,sites,utilization,rnd,backlog,customers,raw_materials,product_pricing,revenue_mix_form,key_contracts,financial_ops,financial_soundness,investment_property,geo_revenue`, 미지정 시 전체). **특정 필드만 지정하면 응답이 가벼움**. 연구개발 상세표처럼 실제 소절이 큰 회사는 단일 필드도 수만 자일 수 있다.
- `bsns_year`+`reprt_code`(**시계열/추이 조회용**): 둘 다 지정 시 특정 과거 시점 1건을 조회(`period` 대신). DART 표준 `reprt_code` — `11011`(사업/연간) `11012`(반기) `11013`(1분기) `11014`(3분기). 한 번에 여러 분기를 반환하지 않으므로 **추이는 분기마다 반복 호출**해서 호출측이 이어붙임(`ownership_major(ticker, year)` 등 기존 DART 표준 파라미터명과 동일 컨벤션 재사용, 결산월 비표준(3월결산 등)에도 안전 — 분기보고서가 연내 2회 등장하면 `report_nm` 기수라벨의 상대순서로 1분기/3분기 구분, 절대월 하드코딩 없음). 하나만 지정하면 `status=error`.
- 예: `business_details("에코프로비엠", fields="utilization")` · `business_details("HD한국조선해양", fields="backlog")` · `business_details("삼성전자", bsns_year="2025", reprt_code="11014")`(2025 3분기 스냅샷)

### strict / candidate 문맥
- `context_mode="strict"`(기본): 헤딩 구조 경계와 field content-gate를 모두 통과한 공식 결과만 반환한다. 정밀도와 상태 의미를 보존하는 정상 경로다.
- `context_mode="candidate"`: `NOT_COLLECTED`의 **단일 표준 필드**(`sites, utilization, rnd, backlog, customers`)에만 쓴다. 공식 필드를 넓히지 않고, 해당 헤딩부터 고정 문자 창을 `candidate_context`로 별도 반환하는 재탐색 보조 경로다.
- `context_chars`: candidate 창의 크기. 기본 20,000자, 최대 60,000자. 호출 AI가 문맥이 부족할 때만 더 큰 값으로 재호출한다. candidate markdown은 인접 소절을 포함할 수 있어 값 확정·hint·자동 비교에 사용하면 안 된다.

## 알려진 한계 (v1 스코프)
- **한 번의 호출로 여러 기간을 반환하지 않음** — "지난 1년 부문별 매출 추이" 같은 질문은 `bsns_year`/`reprt_code` 조합으로 분기마다 반복 호출 필요(예: 2Q25/3Q25/4Q25/1Q26 4번). 서버가 자동으로 시계열을 조립해주지 않음(설계 스코프는 「최신 스냅샷」 — [[260717_1220_decision_business-content-tool-roadmap]]).

## 필드별 가이드 (직접 테스트용)
| 필드 | 읽는 소절 | 무엇이 나오나 | 테스트 예시 |
|---|---|---|---|
| **segments** | 영업부문 주석(K-IFRS 1108) | 부문별 매출·영업이익 (정형표 or 원문 마크다운) | 삼성전자(정형)·SK하이닉스(후보)·삼일(마크다운) |
| **sites** 사업장 | 3.원재료 및 생산설비 / 사업장 현황 | 공장·사업장 소재지·면적 원문(유통은 점포) | 삼성전자(공장 주소)·이마트(점포 창동점) |
| **utilization** 가동률 | 생산실적 및 가동률 | 생산능력·생산실적·가동률 원문(+% 힌트) | SK하이닉스(100%)·에코프로비엠(생산능력 톤) |
| **rnd** 연구개발 | 연구개발활동 | 연구개발비·실적·조직 원문(+매출대비% 힌트) | 유한양행·한국항공우주 |
| **backlog** 수주 | 4.매출 및 수주상황 | 수주잔고·수주계약 원문(조선 flow표 포함) | HD한국조선해양(기초계약잔액→수주잔고) |
| **customers** 고객 | 주요 매출처 / 주요 고객 주석 | 주요 고객·매출처·판매경로 원문(익명 다수) | 삼성전자·유한양행 |
| **raw_materials** 원재료 | 주요 원재료 현황 / 원재료 가격변동추이 | 원재료 구성·매입과 원재료 가격 추이 원문 | LG화학·대한항공 |
| **product_pricing** 제품가격 | 주요 제품 등의 가격변동추이 | 판매가격·ASP·가격변동 원인 원문 | 삼성전자·HD한국조선해양 |
| **revenue_breakdown** 매출 분해 | **매출 축 4개의 단일 진입점** | `by_segment`(III 주석·감사O·**매출+이익**) + `by_product`(II-2-가·감사X·매출만) + `by_region`(III ¶33·**연결**·매출만) + `by_trade`(II-4 매출실적표·**별도**·수출/내수) + `available`/`needs_review` | 현대차(4축 전부)·HD현대일렉트릭(부문 없음→제품 있음) |
| ~~geo_revenue~~ → `by_region` | 위와 동일 | **옛 이름은 별칭으로 유지** — `fields="geo_revenue"` 로 부르면 종전대로 평평하게 반환(옛 호출 비깨짐). 새 코드는 축 이름을 쓴다 | 기아·LG엔솔(단일부문사) |
| **revenue_mix_form** 매출구성 | II-2-가 주요 제품 등의 현황 | 제품·품목별 매출액·비중 원문 + `self_check` | HD현대일렉트릭(단일부문인데 제품 3종)·신풍제약 |
| **key_contracts** 주요계약 | II-6-가 주요계약 | 라이선스·기술도입·장기공급 계약 원문 | 녹십자(라이선스아웃/인)·대원화성 |
| **financial_ops** (금융) | 2.영업의 현황 | 영업개황·영업실적·**영업부문별 재무정보**(금융판 segments) | 신한지주·미래에셋증권 |
| **financial_soundness** (금융) | 재무건전성·지급여력 | RBC·지급여력비율(K-ICS)·순자본비율·연체율 | 삼성생명·우리금융지주 |
| **investment_property** (REIT/보험) | 투자부동산 내역·투자자산 개요 | 부동산 목록·임대율·임대면적·공실 | SK리츠·삼성생명 |

> **자산가치(토지·투자부동산·지분증권 원가vs공정가치)는 별도 tool [[asset_holdings]]로 이관(260720)**.
> 소스는 동일 III.재무 주석이지만 계정 API(자산 티어)·타법인출자 API(시가마크)·시총 대비 NAV 커버리지까지
> 결합한 별도 유즈케이스(자산저평가주 스크리닝)라 분리. 시그니처 설계·하드닝 이력은 그쪽 페이지 참조.

> **D-트랙 금융·REIT**: 마지막 3필드는 금융/증권/보험/지주·REIT용. segments는 금융폼 `UNSUPPORTED_FORM`이므로 `financial_ops`의 영업부문별 재무정보가 대체.
> **KSIC(업종코드) 게이트** — content 마커만이면 카카오(포털)·한화(화약)·아모레퍼시픽(화장품)이 자회사/우발 신호로 오발. → `get_company_info`의 `induty_code`로 **금융권(KSIC 64/65/66)일 때만** financial 필드, **부동산(68)/보험(65)/지주(64)**일 때만 investment_property. 비금융은 아예 시도 안 함(오발 0). **지주회사 64992는 충돌**(신한금융 vs SK)이라 그 안에서만 content-signature로 판별. 응답에 `induty_code` 동봉.
> **헤딩 앵커 + 내용 시그니처 폴백 이중구조** — 금융/REIT는 서식이 덜 표준화돼, 헤딩 키워드가 미스해도 **데이터 시그니처(순이자손익·지급여력비율·임대율 등)로 표를 찾아** 렌더(헤딩 라벨보다 안정적). `source=heading|signature`로 어느 경로인지 표기.
> **D-트랙은 II.사업의내용 구간만 스캔**(`_biz_html_region`) — 시그니처 폴백이 full html을 훑으면 **III.재무 주석의 회계표**(공정가치 서열체계·투자부동산 장부금액 헤더조각)를 REIT 투자부동산으로 **오발**(NH올원·이리츠코크렙: 1095·398자 회계각주). II→III 경계는 **목차 stub(수십자) skip 후 첫 실질구간(>2000자)**으로 잡는다(max-span 금물 — 한화생명류는 말미에 종속사 사업보고서가 embedded돼 그 span이 본문보다 커 오선택→본문 DP Real Estate·종속REIT 서술 통째 누락). II 제한 후 NH올원·이리츠는 오히려 II 프로즈(reit_prose 7203·2123자)로 정상 회수, 한화생명은 종속 부동산 서술 유지, 은행·증권 financial 필드·정상 REIT 전부 불변.
> **지주형 REIT 프로즈 폴백**(`source=reit_prose`) — 명목회사(해외리츠 등)는 표준(제조)폼에 부동산을 **서술형**으로 싣는다(제이알글로벌리츠: 파이낸스타워(벨기에)·498 Seventh Avenue를 '2.주요 제품 및 서비스→영업개황'에 임대료·WALE·임차율 프로즈로. 전용 투자부동산 헤딩·표 없음). 전용 헤딩/시그니처가 다 실패할 때만 표준폼 헤딩(주요 제품 및 서비스·영업개황·회사의 현황)을 시도하되 **content-gate 강화**(임대료/임대차/임차 + 부동산/투자대상/WALE 동반)로 보험 영업개황 등 오섹션을 차단. 작동하는 REIT는 전용경로에서 이미 반환돼 영향 없음(신한알파·SK·롯데·ESR 전부 `source=heading` 유지 확인).

> **핵심: segments 외 필드는 "markdown-primary"** — 도구가 값을 판정하지 않고 **해당 소절 원문을 마크다운 표로 통째 반환**합니다. 값·단위·정의 판단은 **읽는 AI(=당신의 Claude)의 몫**. 회사마다 단위·정의가 달라(가동률 %/시간/톤, 사업장 주소~국가) 원문을 봐야 정확하기 때문(자세히 아래 파싱전략).

## 출력 (ToolEnvelope.data)
- `form_type`: `standard7` / `financial5` / `reit` / `dual` (목차 소절 제목 기반 판별, KSIC 불신)
- **공통 응답 계약**: 모든 필드는 같은 사다리를 따른다 —
  ① 정형(검산 등 자격 심사 통과분만) → ② 심사 탈락 시 해당 구간 **원문 마크다운 + 탈락 사유** →
  ③ 원문도 없으면 명시적 부재(`NOT_COLLECTED`/`NOT_APPLICABLE` + `na_reason`).
  정형 성공 응답에는 `self_check`(호출 AI용 자가검증 안내 — 의심 신호 체크리스트 + 원문 재조회
  경로)가 동봉된다. 어떤 단계에서도 근거 없는 숫자를 만들지 않는다.
- `segments`:
  - `status=OK` + `source=deterministic` → `items:[{name, revenue, profit}]` + `revenue_metric`/
    `profit_metric`(어느 행을 읽었는지 — 외부매출/총부문수익 개념 구분용) + `unit` +
    `reconciliation`(sum-check) + `self_check`
  - `status=NEEDS_REVIEW` + `source=note_markdown`/`biz_markdown` → `segment_note_md`(영업부문 주석 원문 마크다운) → **호출측 LLM이 읽어 추출**. 앵커 실패 시 `source=raw_candidates` + `candidates:[{rendered, score}]`
  - `status=NOT_APPLICABLE` → 단일부문(정상)
  - `status=UNSUPPORTED_FORM` → 금융폼·REIT(v1 미지원, D-트랙 별도)
- **추가 필드 7종(markdown-primary)** — 각 `{status, extraction_status, markdown, na_reason, section_source}`:
  - `sites`(사업장·생산설비) · `utilization`(생산실적·가동률, +`pct_hint`) · `rnd`(연구개발, +`ratio_to_sales_pct_hint`) · `backlog`(수주현황) · `customers`(주요 고객·매출처) · `raw_materials`(원재료 구성·매입 + 원재료 가격 추이) · `product_pricing`(제품·서비스 가격 추이)
  - `raw_materials`는 `materials`와 `input_price`를 **각각 구조 경계로 추출해 최대 하나씩** 결합한다. 한 사업부의 기재 생략이 다른 사업부의 실제 매입·가격 표를 `NOT_APPLICABLE`로 덮지 않는다.
  - `product_pricing`은 명시적 가격 추이 기재 생략·산출 곤란만 `NOT_APPLICABLE`로 처리한다. 가격 결정 방식·정성 설명은 원문으로 유지한다.
  - 기존 `status=MARKDOWN|NOT_APPLICABLE`은 호환성을 위해 유지. `extraction_status=SUCCESS|NOT_APPLICABLE|NOT_COLLECTED`가 명시적 기재없음과 앵커 미검출을 구분한다.
  - **값이 없을 때 왜 없는지를 넷으로 가른다(`absence_kind`)** — 읽는 쪽이 「원문에 없다」와 「우리가 못 찾았다」를 구분하지 못하면 원문 확인을 포기하게 된다.
    | `absence_kind` | 뜻 | md 표시 |
    |---|---|---|
    | `not_disclosed` | 소절이 없거나, 있어도 회사가 부재를 밝혔다 | `해당 없음` |
    | `cross_reference` | 소절은 있으나 다른 절을 가리킨다 | `여기엔 없음 — 원문이 다른 절을 가리킵니다` |
    | `narrative_only` | 소절은 있으나 표 없이 문장 서술만 | `표 없음 — 문장 서술만` |
    | `extraction_failed` | 소절과 표가 있는데 읽어내지 못했다 | `찾지 못함 — 원문에 표가 있습니다` |
    `absence_note`에 판정 근거(어느 소절·회사가 밝힌 문장 인용)를 함께 싣는다. `absence_kind`가 없는 필드는 종전대로 `extraction_status` 기준(`NOT_COLLECTED`=`확인하지 못함`).
  - `status=MARKDOWN` → 해당 소절 원문을 마크다운으로(`markdown`) → **호출측 AI가 읽어 값 추출**(단위·정의 회사별 상이).
  - `section_source`: 실제 매칭 헤딩·chapter·선택 방식·경계 방식(`peer_heading|section_end|section_end_recovery|top_level_recovery|fixed_window_fallback|paragraph`)을 기록한다. md 렌더에 **`원문 위치: II. 사업의 내용 → 다. 영업용 설비 현황`** 한 줄로 나온다 — 회사마다 소절 제목이 달라 이게 없으면 읽는 쪽이 원문에서 같은 자리를 못 찾는다. 장(章) 이름은 로마숫자로 시작하는 것만 신뢰한다(모르면 적지 않는다).
  - 소절 제목이 **문단 안에 녹아 있어**(「…주요 원재료의 가격변동추이는 다음과 같습니다」) 헤딩 요소가 아닌 경우, 정규 경로가 아무것도 못 찾았을 때만 문단 단위로 회수한다(`boundary=prose_paragraph`, `raw_materials`·`product_pricing`). 문단이 이끄는 표는 잇달아 오는 것까지 담는다 — DART는 「(단위 : 원)」을 별도 표로 렌더한다.
  - 기존 `pct_hint`·`ratio_to_sales_pct_hint`는 유지하고, 동일 값을 비권위 `hints[]`에도 제공한다. 힌트는 반드시 반환된 markdown에서만 산출한다.
  - **파서가 값 판정 안 함**: 사업장 유형자산 장부가 함정·가동률 단위카오스·수주 flow표 오귀속·rnd 회계처리/보조금·customers 다위치 충돌은 호출측 AI가 원문 읽어 판별(QA패널 BLOCKER 대응).
- `geo_revenue`(지역별 수익 — 전사 차원 공시):
  - `extraction_status=SUCCESS` → `items:[{name, revenue}]` + `unit` + `revenue_metric` +
    `regional_total`(표 자체의 지역 합계 — 연결 매출과의 tie-out 재료) + `reconciliation` +
    `basis_caption`(표 직전 캡션 — 소재지/도착지 등 기준 확인용) + `self_check`.
    **정형 자격**: 항목합≈지역합계 검산 + 단위 확인 + 외부매출 기준(내부거래 포함 총액 표·
    수익 구성행만 있는 표는 정형 거부)을 전부 통과한 표만.
  - `extraction_status=NEEDS_REVIEW` → 지역표는 찾았으나 정형 자격 미달: 원문 표 마크다운
    (`markdown`) + 탈락 사유(`note` — 예: "내부거래 포함 총액 기준") → 호출 AI가 함정을 알고
    직접 읽는다.
  - `NOT_COLLECTED` → 검산 가능한 지역표 부재. 금융·REIT 폼은 `UNSUPPORTED_FORM`.
  - **`foreign_share_pct` 해외 매출 비중 — 이 필드를 절대금액보다 앞세운다.**
    비중은 **단위가 약분**되므로 단위를 잘못 읽어도 맞는다 — 격자 매핑이 어긋나거나 단위를 놓쳐
    절대금액이 10⁶배 틀려도 비중은 그대로다.
    함께: `domestic_revenue`/`foreign_revenue`/`share_basis`(국내=본사 소재지 국가·국내·한국).
    지역명에 각주가 붙으면(카카오 「국내(주1)」) 국내로 못 읽어 100%가 나오므로 각주를 뗀다.
    표에 국내 구분 항목이 아예 없으면(대한해운) `share_caveat`로 밝힌다 — 「해외 100%」와
    「국내 항목이 표에 없음」은 다르다.
  - **`source_location` — 원문을 직접 찾아보라고 위치를 싣는다.**
    `{chapter: "III. 재무에 관한 사항 — 재무제표 주석", note_section: "37. 부문정보 (연결)",
    table_caption, how_to_find}`. 지역 정보는 **III 주석**에 있고 II가 아니다.
  - `SUCCESS_NO_TOTAL_COLUMN` → 합계 열이 없지만 항목이 **전부 지역명**이라 항목합을 총계로
    쓴 경우(HD현대일렉트릭 「외국 | 본사 소재지 국가」 2칸). 검산은 못 했다고 밝힌다.
  - 알려진 한계(v2): 총액 기준·구성행 표의 합계행 재선택(현재는 안전 강등), 3개월/누적 구분,
    제품·서비스별 분해(`product_revenue` — 오분류 위험으로 이연).
- `revenue_breakdown`(매출 분해 — **기본 반환 세트의 단일 진입점**):
  - **묻는 곳을 하나로 합친 필드다.** `segments`만 물으면 「단일부문 선언」을 보고 "이 회사는 부문
    정보가 없다"로 끝나는데, 실제로는 II-2-가에 제품별 구성이 있다(HD현대일렉트릭 전력기기 69.5%).
  - `{by_segment, by_product, available, needs_review, guidance}`. 각 축에 `source`(출처·감사여부)를
    붙여 **칸막이는 유지**한다 — 평평하게 섞으면 감사받은 주석과 공시서식 기재사항이 구분되지 않고,
    제품+지역을 더해 매출이 두 배가 되는 오독이 열린다.
  - `available`은 값이 나온 축, `needs_review`는 **원문만 있고 값은 못 믿는 축**. 섞지 않는다
    (남광토건: 제품별이 시공실적 표라 검토필요인데 available 에 넣으면 안내가 거짓이 된다).
  - 옛 이름 `segments`·`revenue_mix_form`은 `fields`에 직접 주면 종전대로 평평하게 반환(별칭) —
    기존 호출은 깨지지 않는다. 지역별(`geo_revenue`)은 묶지 않고 독립 필드로 둔다.
  - **출력 문구 원칙**: 렌더 문구에 ⚠️·「~하지 마세요」를 쓰지 않는다. 자료의 성격을 알려주는 것이지
    읽는 사람이 뭘 잘못한 게 아닌데, 경고 표지와 금지문은 그렇게 읽힌다. 앞머리는 한 줄
    (「제품별·부문별 매출 구분은 K-IFRS 기준과 다를 수 있습니다」)로 두고, 상세(감사여부·분모·검산)는
    축별 `_출처:_`·`_자가검산:_` 줄이 회사별 실측값으로 말한다 — 앞머리에서 반복하면 중복이다.
    회귀 테스트로 고정(`test_output_does_not_scold_the_reader`). 같은 이유로 `warnings` 푸터는
    「⚠」가 아니라 「_처리 메모:_」다 — 담기는 내용이 대개 실패가 아니라 처리 경위다(어느 문서를
    썼나 · 정형 대신 원문을 냈나).
- `revenue_mix_form`(매출구성 — 기업공시서식 II-2-가):
  - **`segments`와 다른 자료다.** `segments`는 III 주석의 K-IFRS 1108 영업부문(외부감사 대상),
    `revenue_mix_form`은 II. 사업의 내용의 기재사항이다. **단일 영업부문 회사도 제품별 구성은
    여기 있다** — HD현대일렉트릭은 부문 주석이 「단일부문」인데 II-2-가에는 전력기기 69.5% ·
    회전기기 14.4% · 배전기기 16.1%가 있다. 이 필드가 없으면 그 회사의 제품 믹스를 못 본다.
  - `status=MARKDOWN` → 소절 원문 + `basis_note`(회계 부문이 아님을 명시) + `self_check`.
  - `self_check`: `unit`(천원/백만원/USD천 — 오환산 방지) · `pct_sum`/`pct_sum_is_100` ·
    `declared_total`(표의 합계행) · `item_sum`(항목 합) · `tie_out` · `tables_in_region`.
    **표가 밝힌 것만 검산한다** — 연결매출과의 대조는 값이 없으므로 하지 않고, 하지 말라고 적는다.
    검산은 '합계행을 가진 첫 표' 기준(구간에 표가 여럿이면 `scope_note`로 알린다).
  - `status=NEEDS_REVIEW` → 절은 찾았으나 매출표가 아님(`not_sales_caption`): 시공실적·매입현황·
    생산능력·가격변동, 그리고 **은행·보험의 상품 카탈로그**(상품수·가입대상, 단위 「개」).
    값을 내지 않고 원문을 그대로 넘긴다 — 렌더도 「해당없음」이 아니라 「원문 · 검토필요」로 낸다.
  - 페이로드 상한은 **호출 파라미터 `section_chars`**(기본 20,000 · 2,000~200,000). 하드코딩이 아니라
    호출측 AI 가 정보가 모자라면 올려서 다시 부를 수 있게 한 것이다. 잘리면 `markdown_truncated`·
    `markdown_full_chars`·`truncation_note`(올리는 방법 안내)가 붙고 마크다운에도 렌더된다.
    적용 대상: `revenue_mix_form` · `key_contracts` · `financial_ops` · `financial_soundness` ·
    `investment_property`.
- `key_contracts`(주요계약 — II-6-가): 라이선스·기술도입·기술제휴·장기공급 계약의 상대방·기간·금액
  원문. 같은 소절을 읽는 `rnd`(연구개발)와 별개 필드다. 「해당사항 없음」은 정상적인
  `NOT_APPLICABLE`이며 실패가 아니다.
- `candidate_context`(선택): `context_mode="candidate"`이고 공식 `extraction_status=NOT_COLLECTED`일 때만 추가된다. `{status="LOW_CONFIDENCE", field, anchor, selection_method="fixed_window_heading", context_chars, warning, markdown}`이며 공식 필드 상태·`hints[]`·자동 비교와 분리된다. 앵커조차 없으면 `status="NOT_FOUND"`만 반환한다.
- `timings_ms`(단계별 병목)

## Data sources
DART **`get_document`(전체 보고서 XML) 1 API콜**([[XML-vs-PDF]]) → text에서 `II.사업의 내용`·`연결재무제표 주석`(별도 heading 전까지) 슬라이스(`_slice_getdoc_sections`), html은 후보표 스캔용 원본. viewer 3웹콜(~5s) 대비 **~3x 빠름**. **PDF/OCR·내부 LLM·pandas 불필요.**

**014(document.xml 부재) 폴백 — 정정보고서 처리**: 최신 정기보고서가 **첨부/기재정정**이면 그 정정본의 `document.xml`이 부재해 `get_document`가 DART 014를 낸다. 첨부정정은 **첨부(감사보고서 등)만 갱신**하고 II.사업의 내용 본문은 담지 않으므로(첨부정정은 viewer 노드트리도 정정표지 9개뿐) — `_find_report_candidates`가 전 후보를 rcept_dt 내림차순 반환하고, 014 시 **동일 기수(`(2025.12)` 라벨 일치) 원본 보고서로 get_document 폴백**한다(작년 기수로는 폴백 안 함). 실측: KB금융(최신 `[기재정정]` 014 → 하루 전 원본 1.66M), 삼성화재(`[첨부정정]` 014 → 원본 1.08M) 모두 영업·건전성 정상 회수. 폴백 사실은 `warnings` + `data.fetch_method="get_document(정정폴백)"`로 투명 기록. 동일기수 원본도 전부 014면 **최종적으로 viewer 웹fetch 폴백**(`_fetch_viewer_sec`, 극소수·느림), 그것도 실패 시 graceful ERROR.

## 파싱전략 (핵심 — [[260717_1220_decision_business-content-tool-roadmap]])
flatten 이 2D 표를 1D 로 뭉개 행·열 정렬이 깨지는 것이 이 서식의 근본 난제다.
**설계 결정**: MCP tool 은 이미 호출측 LLM 이 부르므로 **내부 LLM 을 두지 않는다** —
tool 은 기계적으로 구간을 좁히고 의미 추출은 호출측이 한다.
- **① 정형(flatten)** — 본문표와 주석표를 **둘 다 파싱해 부문명이 서로 맞는지 교차검증**한다.
  어긋나면 지주사가 본문에 자회사 표를 실은 경우이므로 후보로 강등한다(주석 = K-IFRS 1108 이 권위).
  지역정보표·매출유형표·재무라인처럼 부문표가 아닌 표는 게이트에서 걸러낸다.
- **② 저신뢰·실패 → 영업부문 주석 원문을 마크다운으로 통째 반환** — 어느 표가 맞는지 점수 매기는
  파서 대신, `render_segment_note_markdown` 이 주석 구간을 설명문단+표째로 렌더해 호출측 AI 가
  읽는다. 단일부문 선언 구간은 건너뛰고, 앵커 실패 시 II. 사업의 내용 마크다운, 그것도 없으면
  후보표 순으로 내려간다. 값을 억지로 만들지 않는다.
- **③ N/A** — 후보표 0개(단일부문·표 부재).
- **폼 게이트**: `detect_form` 에서 제조 소절(주요제품·원재료)이 있으면 REIT·금융으로 판정하지
  않는다(유통사가 리츠 자회사를 보유해 프로즈에 '부동산투자회사'가 있어도 표준폼 유지). 진짜
  금융지주·REIT 만 `UNSUPPORTED_FORM`(D-트랙).

**성능**: get_document 1콜이 지배(미캐시 ~2-2.5s, 캐시히트 150-470ms). 헤딩 색인은 문서당 한 번 만들고 요청 필드가 공유한다. 실측 7.7~8MB 약 60ms, 22.2MB 약 170ms. 단계별 `timings_ms`(resolve/search/fetch/segment/Afields/total)로 실측.

## 추가 5필드 markdown-primary 구현 (`services/biz_fields.py`)
- `build_region_index(html)` — 문서의 헤딩을 한 번 색인한다(표 안 제목·목차 점선·일반 본문
  언급은 배제). 굵은 표기 뒤에 본문이 붙은 문단 등 변형 헤딩도 제한적으로 회수한다.
- `render_biz_subsection_markdown(html, kw_patterns, content_re)` — 실제 헤딩에서 시작해 **다음
  동급/상위 헤딩 또는 DART section 끝**까지 렌더한다. 고정 글자 창은 구조 헤딩이 전혀 없는 문서의
  마지막 호환 폴백에서만 쓴다. 번호 깊이가 뒤집힌 문서는 최초 경계가 제목만 남긴 경우에 한해
  복구하고, 내용이 충분한데 content-gate 가 실패하면 확장하지 않는다(뒤 소절 우연 일치 방지).
- 요약+상세(XII.상세표)는 최대 2구간을 원문 그대로 반환한다. 임의 문자수 절단을 하지 않으므로 큰 상세표는 `fields` 부분조회 권장.
- 각 필드 = 헤딩패턴 + content_re + 최소 hint. **파서 판정 없음** = QA BLOCKER(사업장 유형자산함정·수주 flow표 오귀속·rnd 회계처리/보조금·customers 다위치) 를 "AI가 원문 읽어 판별"로 해소.

## 설계 근거 · 알려진 한계

- **매출 축 4개는 한 서랍장(`revenue_breakdown`)에 담되 칸막이는 유지한다.** 같은 매출을 다르게
  자른 것이라 묶어 두지 않으면 한 축만 보고 「이 회사는 부문 정보가 없다」로 끝난다. 반대로 평평하게
  섞으면 감사받은 주석과 공시서식 기재사항이 구분되지 않고, 제품+지역을 더해 매출이 두 배가 되는
  오독이 열린다. 옛 이름(`segments`·`revenue_mix_form`·`geo_revenue`)은 별칭으로 살아 있다.
- **축마다 기준이 달라 총계가 같지 않을 수 있다.** `by_region` 은 연결(주석 ¶33), `by_trade` 는
  별도(II 매출실적표·선적 기준 수출)라 방향이 양쪽으로 갈린다. K-IFRS 1108 ¶23(CODM 에게 보고되는
  측정치)과 ¶33(재무제표 작성에 쓴 재무정보)은 **다른 장부**이고, ¶28 이 둘 사이 조정을 요구하는
  것 자체가 「원래 다르다」는 전제다. 두 축을 더하거나 곧바로 견주지 않는다. 두 축은 상호보완이기도
  하다 — III 부문 주석이 없는 회사가 II 에 수출 표기를 갖는 경우가 흔하다.
- **이익이 있는 축은 `by_segment` 하나뿐이다.** ¶23 이 이익을 영업부문에만 요구하고 지역(¶33)엔
  수익·비유동자산만 요구한다(지역 표 머리글이 조문 문구 그대로다). 지역별 이익이 필요하면 부문명이
  지역·현지법인인 회사의 `by_segment` 를 본다 — 강등 게이트는 순수 지역명만 보므로 「미국
  **사업본부**」 같은 부문명은 걸리지 않는다.
- **부문·지역 표는 XBRL 택소노미 코드를 1차 앵커로 찾는다**(코드가 없으면 호출측 앵커 → 제목 사전
  → 문서 전체 순으로 내려간다). 회사마다 주석 절의 **번호와 제목이 다르고 연도가 바뀌면 밀리는데**
  코드는 바뀌지 않는다. 절 번호를 앵커로 저장하면 번호가 밀린 해에 엉뚱한 절을 짚고도 조용히 성공한
  것처럼 보인다. 절 맵을 쌓는다면 키는 번호가 아니라 (회사 × 보고서종류 × **코드**)이고, 값어치는
  속도가 아니라 **드리프트 감지**다.
- **지역이 열에 오는 서식과 행에 오는 서식이 둘 다 있다.** 행 지향 표는 총부문수익과 비유동자산이
  한 표에 있어 수출형(해외 수익 큰데 해외 자산 0) vs 현지생산형 판별에 바로 쓸 수 있다
  (`assets_by_region`). DART XML 의 데이터 셀은 `<TD>` 가 아니라 **`<TE>`** 다(머리만 `<TH>`).
- **지역이 하나뿐인 표도 정보다** — 「본사 소재지 국가 | 325,458」은 해외비중 0.0%(전량 국내)를
  확정해 준다. 항목 2개 이상 게이트로 버리지 않는다.
- **해외 매출 비중(`foreign_share_pct`)을 절대금액보다 앞세운다.** 비중은 **단위가 약분**되므로
  표 단위를 잘못 읽어도 맞는다. 지역명에 각주가 붙으면(「국내(주1)」) 떼고 읽고, 표에 국내 구분
  항목이 아예 없으면 `share_caveat` 로 밝힌다 — 「해외 100%」와 「국내 항목이 표에 없음」은 다르다.
- **귀속기준은 원문에 있을 때만 싣는다**(`attribution_basis`). 실제로 밝히는 회사는 소수이고,
  밝힌 경우도 「고객 소재지」와 「사업장 소재지」로 갈린다 — 하나로 못박으면 나머지에서 거짓이 된다.
  이 값이 **해외비중의 의미를 좌우한다**: 대한항공 국제선은 「본사 소재지 국가」로 잡혀 해외비중이
  낮게 나오는데, 파싱은 정확하고 경제적 실질과 다를 뿐이다. 없으면 「귀속기준 미공시」로 밝힌다.
- **연결인지 별도인지도 라벨이 아니라 값으로 싣는다** — `basis`(XBRL 컨텍스트 코드로 판별, 코드가
  없으면 「미상」) + `basis_conflict`(연결 절이 있는데 별도를 읽었을 때만). 하드코딩된 「연결 기준」
  라벨은 별도 절을 읽고도 연결이라 말하게 된다. 값을 버리는 대신 **무엇을 읽었는지 밝히는** 쪽이다.
- **표 숫자는 사람이 읽는 단위로 환산하되 표 전체에 한 단위를 쓴다**(행마다 단위가 다르면 행끼리
  눈으로 비교가 안 된다). 규모에 따라 조원·억원으로 자동으로 갈리고, 매출·이익은 함께 스케일한다.
  **단위를 못 읽었으면 환산하지 않고 「단위 미상 — 원문 확인」으로 밝힌다** — 모르는 채 곱하면
  10³·10⁶ 배 틀린 값을 확신 있게 낸다. 정확한 원값은 payload(JSON)에 그대로 있고 각주에 원문 표
  단위를 남겨 원문 대조 경로를 유지한다.
- **출력 문구는 서술문으로.** 자료의 성격을 알려주는 것이지 읽는 사람이 뭘 잘못한 게 아니므로
  ⚠️·「~하지 마세요」를 쓰지 않는다(`warnings` 푸터도 「_처리 메모:_」). 내부 사유 코드는 진단
  필드(`na_code`)로만 남기고 사람이 읽는 문장엔 한국어 문면만 싣는다 — **모르는 코드는 빈 문자열로
  떨군다**(원문 코드를 기본값으로 두면 새 코드가 그대로 다시 샌다).
- **축마다 「이 회사 원문의 어느 절」을 밝힌다.** 회사마다 절 번호·제목이 달라 「III 주석」 같은
  일반론으로는 원문을 못 찾는다. 축마다 payload 모양이 달라(`source_location` / `section_source`)
  렌더에서 하나로 흡수한다. **모르면 적지 않는다** — 빈 「원문 위치: →」는 절을 짚은 것처럼 보여
  더 나쁘다.
- **지역 정보가 표가 아니라 서술문에만 있는 회사가 있다.** 금액이 없어 매출 축에 넣을 수 없으므로
  파싱하지 않는다(근거 없는 숫자 금지). 미검출 사유는 「표 없음 · 지역별 금액이 표로 공시되지
  않았습니다」로 낸다 — 탐색의 마지막 창이 문서 전체라, 이 신호가 뜬 시점엔 이미 다 훑은 뒤다.
  「다른 절을 찾아보라」는 없는 표를 찾으러 보내는 안내가 된다.
- **분기·반기는 주석 절이 줄어 지역 정보를 생략하는 회사가 있다**(자본시장법 시행령 §170).
  분·반기에서 부재면 `absence_hint` 가 사업보고서(`reprt_code='11011'`) 재조회를 안내한다.
  다만 지역 정보 자체는 세 보고서 모두에 실리는 것이 보통이고(재무제표 주석이라서다),
  **분기보고서가 두 달 더 신선하다** — 시의성이 필요하면 최신 분·반기를 쓰는 게 맞다.
- **한계**: ① 필드 제목 자체가 없고 일반 본문에만 값이 있는 경우는 정밀도 우선으로 `NOT_COLLECTED`
  가능 ② 금융지주·REIT 는 segments `UNSUPPORTED_FORM`(D-트랙 별도) ③ customers 고객명 다수 익명
  (주요고객A/B) — 이름 억지생성 안 함 ④ **금융지주·보험은 한 소절이 크다** — 계열사마다 같은 항목
  (자본적정성·유동성·지급여력)을 싣기 때문이다. `section_chars` 기본 20,000 을 올리면 전량을 받는다
  (연구개발 상세표가 수만 자인 회사도 같은 방식으로 조절) ⑤ `key_contracts` 는 기중 종료된 계약을
  기말 기준 `NOT_APPLICABLE` 로 낸다 — 사실은 맞으나 정보가 접힌다 ⑥ 총액 기준·구성행 표의 합계행
  재선택, 3개월/누적 구분, 제품·서비스별 분해는 오분류 위험으로 이연.

### 귀속기준은 재조립 전에 글자로 거른다 (260829)

`_mark_attribution` 은 「지역 매출을 무슨 기준으로 나라에 배분했나」를 찾으려고 지역 구간의
표를 **하나씩 객체로 재조립**해 셀을 뒤졌다. 실측(사용자가 실제로 조회한 120사, 캐시 재생):

| | |
|---|---|
| 이 코드까지 간 회사 | 64/79 (81%) — 자주 지난다 |
| 재조립한 표 | **2,536개** |
| 그중 문장을 찾아낸 표 | **2개** (회사 1곳) |

그래서 재조립 전에 표 원문을 글자로 훑어 거른다 — 통과 3/2,536. **필요조건이라 결과가
달라질 수 없다**: 셀에서 잡히는 문장은 표 원문에도 반드시 있다. 다만 그게 성립하려면
셀과 **같은 정규화**(태그→공백·엔티티 해제·공백 접기)를 걸어야 한다. 안 걸면
`고객&nbsp;소재지` 나 줄바꿈이 낀 문장을 놓쳐 **조용한 회귀**가 된다. 테스트가 그 셋을
각각 못 박고, 「프리필터는 느슨해야 한다」(통과해도 최종 판정은 따로)까지 검사한다.

검증: 120사 전수 before/after 대조 **289만 자, 차이 0** — 유일한 양성 케이스(HPSP
「수익은 고객의 소재지에 기초한 국가에 귀속시킴.」)도 그대로 살아남았다.

**효과는 작다.** 재조립 횟수는 건당 55→34회(-38%)인데 CPU 는 400→391ms(-2.3%)다.
걷어낸 표들이 **싼 표**였다. 「전체 파싱의 63%가 헛일」은 **횟수** 기준이었고 비용 기준이
아니었다 — 다음엔 횟수가 아니라 시간으로 잰다. 실제 비용은 여기 있다(120사 기준):

```
_render_html_region_md   14.0초      _export_from_biz_table  10.2초 (회당 85ms)
find_segment_candidates   6.5초      build_region_index       6.4초 (건당 2회)
```

<sub>같은 작업에서 `build_region_index` 중복 제거도 시도했다가 **되돌렸다** — 「두 HTML 이
같으면 재사용」 조건이 실측상 한 번도 참이 되지 않았다(건당 여전히 2.0회). 발동하지 않는
코드에 「줄였다」는 주석만 남는다.</sub>

## 변경 이력
- 2026-09-06: 응답 머리와 「저신뢰 보조 문맥: 찾지 못함」에 **원문 절 주소**(`opm://filing/{rcept_no}/toc` → `/section/{no}`)를 적는다 — 표가 약하면 AI 가 그 절을 직접 읽는다(`opm://filing` 절 리소스, 뷰어 추가 호출 0).
- 2026-08-06: 발견 경위·census 서술을 private storage 로 이관(경계 규칙 [[wiki_schema]] 0.0).
- 2026-08-03: `absence_kind` 4갈래 신설 · `basis`(연결/별도)를 XBRL 컨텍스트로 판별.
- 2026-08-02: `revenue_breakdown` 을 매출 4축(`by_segment`/`by_product`/`by_region`/`by_trade`)
  단일 진입점으로 재편, 옛 이름은 별칭 유지.
- 2026-07-31: `foreign_share_pct`·`source_location` 신설.
- 2026-07-28: `revenue_mix_form`(II-2-가)·`key_contracts`(II-6-가) 신설 · 출력 문구 서술문화.
- 2026-07-24: 공통 응답 계약(정형 → 원문 마크다운 → 명시적 부재) 통일 · `geo_revenue` 신설.
- 2026-07-21: `bsns_year`+`reprt_code` 시점 조회 추가.
- 2026-07-20: 자산가치·NAV 유즈케이스를 [[asset_holdings]] 로 분리.
- 2026-07-18: D-트랙(금융·REIT) 3필드 + KSIC 게이트 · 014 정정폴백.

## 관련
- [[asset_holdings]] (자산가치·NAV 스크리닝 — 이 tool에서 분리)
- [[260717_1220_decision_business-content-tool-roadmap]] (설계·실현가능성·스콥·아키텍처)
- 사업의내용_ksic별양식 (업종별 소절 양식·헤딩 variant 레퍼런스)
- [[ksic-sector-mapping]] (KSIC 한계 — 폼 판별에 불신)
- [[XML-vs-PDF]] (viewer HTML 단독)
