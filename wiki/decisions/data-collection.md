---
type: source
title: OPM 데이터 수집 Architecture (전수 Entry Point + 파싱 방법)
generated: 2026-04-29
tags: [architecture, data-source, entry-point, dart, kind, naver, upstage, opendataloader, fallback]
related: [DART-OpenAPI, KRX-KIND, 네이버-금융, 공시유형코드체계, pblntf-ty-필터링, DART-KIND-매핑-화이트리스트-2026-04, XML-vs-PDF]
---

# OPM 데이터 수집 Architecture

## 개요

OPM(open-proxy-mcp)이 사용하는 모든 데이터 source의 entry point, endpoint URL, 파싱 방법, rate limit, fallback chain을 단일 문서로 정리한다.

OPM 운영 원칙(2026-04-18 결정, [[DART-KIND-매핑-화이트리스트-2026-04]] 참조):

- 1순위: `DART OpenAPI` (구조화 JSON/XML)
- 2순위: `DART document.xml` (원문 ZIP→XML→텍스트)
- 3순위: `KIND HTML` (화이트리스트 4종만)
- 보조: `Naver Finance` (시세·뉴스), `KRX Open API` (종가)
- PDF 다운로드와 OCR은 OPM 런타임에서 제거되어 `open-proxy-ai`로 이관됐다.

## 데이터 source 전수

### A. 구조화 API (정형)

1. DART OpenAPI — `https://opendart.fss.or.kr/api/...`
2. KRX Open API — `https://data-dbg.krx.co.kr/svc/apis/...` (주가 fallback)
3. Naver 검색 API — `https://naverapihub.apigw.ntruss.com/search/v1/...`(HUB, 우선) · `https://openapi.naver.com/v1/search/...`(개발자센터, 2027-06-30 까지)
4. Naver Finance JSON — `https://api.finance.naver.com/...`
4-1. Naver 증권(모바일) JSON — `https://m.stock.naver.com/api/index/{지수}/enrollStocks` (지수 구성종목 목록 — 아래 4.6, 261018)
4-2. 한국은행 ECOS — `https://ecos.bok.or.kr/api/StatisticSearch/…` (무위험 금리 일별 — 아래 4.7, 261021. 환율 환산용 `731Y001` 은 `dart/fx.py`)

### B. HTML 크롤링 (반정형)

5. DART 웹 viewer — `https://dart.fss.or.kr/dsaf001/main.do`, `report/viewer.do`
6. DART 웹 PDF 다운로드 — `https://dart.fss.or.kr/pdf/download/pdf.do` (v1 only)
7. KIND 공시 viewer — `https://kind.krx.co.kr/common/disclsviewer.do`
8. KIND 상세 검색 — `https://kind.krx.co.kr/disclosure/details.do`
9. Naver Finance — `https://finance.naver.com/item/coinfo.naver`, `sise_group_detail.naver`

### C. 외부 OCR (이진 → 텍스트)

10. Upstage Document Parse — `https://api.upstage.ai/v1/document-ai/document-parse`
11. opendataloader-pdf — Java 11+ 로컬 라이브러리 (PDF → 마크다운)

### D. 정적 사내 데이터 (호출 0회)

12. `open_proxy_mcp/data/asset_managers/` — 운용사 정책/행사내역/매트릭스 JSON

전 11개 data tool은 위 source의 조합으로 동작한다.

---

# 1. DART OpenAPI (JSON/XML, 정형 구조화)

엔드포인트 베이스: `https://opendart.fss.or.kr/api`

## 1.1 list.json — 공시 검색 (DS001)

- Endpoint: `https://opendart.fss.or.kr/api/list.json`
- 호출 위치: `open_proxy_mcp/dart/client.py` `DartClient.search_filings()`
- 주요 파라미터:

| 파라미터 | 의미 | 비고 |
|---|---|---|
| `corp_code` | DART 8자리 기업코드 | corpCode.xml로 미리 매핑 |
| `bgn_de`, `end_de` | YYYYMMDD 시작·종료일 | 둘 중 하나 필수 |
| `pblntf_ty` | 공시 유형 코드 | 아래 표 참조. 미지정 시 누락 위험 |
| `corp_cls` | Y(KOSPI)/K(KOSDAQ)/N(KONEX)/E(기타) | 시장 와이드 검색용 |
| `page_no`, `page_count` | 페이지·페이지당 건수 | `page_count` 최대 100 |

- 캐시: `_search_cache` (corp_code 단독, page=1, count=100일 때만 메모리 캐시)
- 사용 services: `shareholder_meeting_notice`·`shareholder_meeting_results`(service 파일은 `shareholder_meeting.py` 하나), `dividend_disclosure`, `ownership_structure`, `proxy_contest`, `value_up`, `treasury_share`, `corporate_restructuring`, `dilutive_issuance`, `corporate_deals`·`order_contracts`(구 `related_party_transaction`), `corp_gov_report`, `company`

### `pblntf_ty` 코드표 ([[pblntf-ty-필터링]] 참조)

| 코드 | 분류 | 대표 공시 | OPM tool |
|---|---|---|---|
| `A` | 정기공시 | 사업보고서, 반기보고서, 분기보고서 | (DS003 alotMatter 등 직접 endpoint 사용) |
| `B` | 주요사항보고 | 자기주식취득결정, 합병결정, 유상증자결정, CB·BW, 감자, 소송 | treasury_share, corporate_restructuring, dilutive_issuance, proxy_contest |
| `C` | 발행공시 | 증권신고서 | (현재 미사용) |
| `D` | 지분공시 | 5% 대량보유, 임원소유보고, 위임장권유참고서류, 공개매수 | ownership_structure, proxy_contest |
| `E` | 기타공시 | 주주총회소집공고 | shareholder_meeting_notice |
| `I` | 거래소공시 | 주주총회결과, 현금ㆍ현물배당결정, 기업가치제고계획, 최대주주변경 | dividend_disclosure, value_up, shareholder_meeting_results, ownership_structure(changes) |
| `J` | 공정위 공시 | 대규모기업집단 공시 | (현재 미사용) |

### 키워드 필터 패턴

`list.json`은 제목 직접 검색이 약하다. `pblntf_ty`로 좁힌 뒤 `report_nm` 키워드로 후처리.
공통 헬퍼: `services/filing_search.py` `search_filings_by_report_name()`
- max_pages 기본 10, page_count 100 → 최대 1,000건/공시유형
- 1,000건 초과 시 `notices`에 명시 (truncated 경고)

## 1.2 corpCode.xml — 기업코드 매핑

- Endpoint: `https://opendart.fss.or.kr/api/corpCode.xml`
- 응답: ZIP → XML (전체 상장+비상장 corp_code)
- 호출 위치: `DartClient._load_corp_codes()` (모듈 글로벌 캐시 `_corp_code_cache` — 프로세스가 뜰 때 1회 적재) ·
  `DartClient.refresh_corp_codes()` (261018 — 하루 한 번·신규상장 신호·해석 실패 때 새로 받아 통째로 바꿔 끼움. 아래 「원장 관측」)
- 사용: `lookup_corp_code()` / `lookup_corp_code_all()` (종목코드/회사명/약칭/영문명 → corp_code 변환)

### Alias 매핑

`_CORP_ALIASES` (client.py)에 슬랭/영문/사명변경 등 30+ alias 등록.
- 영문: `kt&g` → 케이티앤지, `ls electric` → 엘에스일렉트릭
- 슬랭: `삼전` → 삼성전자, `현차` → 현대자동차, `카뱅` → 카카오뱅크
- 사명 변경: `dgb금융지주` → iM금융지주, `대구은행` → 아이엠뱅크
- 영문 약칭: `kb` → KB금융, `bnk` → BNK금융지주, `jb` → JB금융지주

## 1.3 document.xml — 원문 본문 ZIP

- Endpoint: `https://opendart.fss.or.kr/api/document.xml`
- 파라미터: `rcept_no`
- 응답: ZIP (PK 시그니처) → XML 추출 → HTML/텍스트 변환
- 호출 위치: `DartClient.get_document()`, 캐싱 wrapper `get_document_cached()`
- 캐시: 메모리 LRU(바이트 예산 96MB, 전역) + 디스크 캐시 `/data/opm_cache/{rcept_no}.json`(볼륨, 640MB · LRU) → [[#11-2-캐시-정책]]
- 텍스트 변환: `_html_to_text()` (br/p/tr 등을 줄바꿈 처리, 이미지 파일명은 본문에서 제거)
- 이미지 감지: 파일명에 "소집/통지/주총/공고" 키워드 포함 시 `[IMAGE_NOTICE]` 경고 로그 발생
- 인코딩 fallback: utf-8 → euc-kr → cp949

## 1.4 viewer.do — DART HTML viewer (2차 경로)

- Endpoint: `https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}`, `https://dart.fss.or.kr/report/viewer.do`
- 호출 위치: `DartClient.get_viewer_document(rcept_no, section_keywords=...)`
- 동작: main.do HTML에서 `treeData.push(node1)` 블록 정규식 추출 → 섹션별 `report/viewer.do` 호출 → HTML 결합
- 사용 시점: `document.xml`이 빈 본문/구조 깨졌을 때 fallback
- Rate limit: `_throttle_web()` → `_throttle_scrape()` (0.4~1초 랜덤 + 분당 40건, KIND 와 프로세스 시계 하나 공유)
- 캐시: `_DOC_CACHE` 를 `viewer:` 네임스페이스로 공유 (rcept_no + keywords 키) — doc 과 하나의 바이트 예산

## 1.5 DS001~DS005 그룹 endpoint

OPM이 사용하는 구조화 endpoint를 그룹별로 정리. 모든 endpoint는 `_request()`를 통해 호출되며 상태 "000"이 정상.

### DS001 — 공시검색

| Endpoint | OPM 메서드 | 용도 |
|---|---|---|
| `list.json` | search_filings | 공시 검색 (전 tool 공통) |
| `corpCode.xml` | _load_corp_codes | 기업코드 매핑 |
| `document.xml` | get_document | 원문 ZIP |

### DS002 — 정기보고서 (지분·배당·자기주식)

`reprt_code`: `11011`(사업), `11012`(반기), `11013`(1분기), `11014`(3분기)

| Endpoint | OPM 메서드 | 사용 service |
|---|---|---|
| `hyslrSttus.json` | get_major_shareholders | ownership_structure (major_holders) |
| `hyslrChgSttus.json` | get_major_shareholder_changes | ownership_structure (changes) |
| `mrhlSttus.json` | get_minority_shareholders | ownership_structure (summary) |
| `stockTotqySttus.json` | get_stock_total | ownership_structure, proxy_contest |
| `tesstkAcqsDspsSttus.json` | get_treasury_stock | treasury_share (annual), proxy_contest |
| `alotMatter.json` | get_dividend_info | dividend_disclosure (사업보고서 배당 상세) |

### DS003 — 재무제표·감사의견 (정기보고서)

`reprt_code` 동일 (11011/11012/11013/11014).

| Endpoint | OPM 메서드 | 사용 service |
|---|---|---|
| `accnutAdtorNmNdAdtOpinion.json` | get_audit_opinion | financial_metrics (audit_opinion scope) |
| `fnlttSinglAcnt.json` | get_fnltt_singl_acnt | financial_metrics (yearly/quarterly — 단일 회사 주요 재무) |
| `fnlttSinglAcntAll.json` | get_fnltt_singl_acnt_all | financial_metrics (전체 계정과목) |
| `fnlttSinglIndx.json` | get_fnltt_singl_indx | financial_metrics (재무지표 — ROE/ROA 등) |

`fs_div`: `OFS`(개별재무) / `CFS`(연결재무).

### DS004 — 수시보고 (지분 대량보유·임원소유)

| Endpoint | OPM 메서드 | 사용 service | 비고 |
|---|---|---|---|
| `majorstock.json` | get_block_holders | ownership_structure (blocks), proxy_contest | 5% 대량보유. 보유목적 필드 없음 → document.xml의 PUR_OWN 태그 보강 |
| `elestock.json` | get_executive_holdings | ownership_structure (timeline) | 임원·주요주주 특정증권 소유 (전체 이력) |

### DS005 — 주요사항보고 (M&A·자사주·증자·소송)

자기주식 4종:

| Endpoint | OPM 메서드 | service |
|---|---|---|
| `tsstkAqDecsn.json` | get_treasury_acquisition | treasury_share (acquisition) |
| `tsstkDpDecsn.json` | get_treasury_disposal | treasury_share (disposal) |
| `tsstkAqTrctrCnsDecsn.json` | get_treasury_trust_contract | treasury_share (events) |
| `tsstkAqTrctrCcDecsn.json` | get_treasury_trust_termination | treasury_share (events) |

기업 재편 4종 (corporate_restructuring):

| Endpoint | OPM 메서드 |
|---|---|
| `cmpMgDecsn.json` | get_merger_decision (회사합병결정) |
| `cmpDvDecsn.json` | get_division_decision (회사분할결정) |
| `cmpDvmgDecsn.json` | get_division_merger_decision (회사분할합병결정) |
| `stkExtrDecsn.json` | get_stock_exchange_decision (주식교환·이전결정) |

희석성 증권 발행 4종 (dilutive_issuance):

| Endpoint | OPM 메서드 |
|---|---|
| `piicDecsn.json` | get_rights_offering_decision (유상증자결정) |
| `cvbdIsDecsn.json` | get_convertible_bond_decision (전환사채발행결정) |
| `bdwtIsDecsn.json` | get_warrant_bond_decision (신주인수권부사채발행결정) |
| `crDecsn.json` | get_capital_reduction_decision (감자결정) |

자기주식 소각결정은 별도 구조화 endpoint가 없어 `list.json + report_nm 키워드`로 검색 (treasury_share `_CANCELATION_KEYWORDS`).

### 기업 기본정보

| Endpoint | OPM 메서드 | service |
|---|---|---|
| `company.json` | get_company_info | company (대표이사, 결산월 등) |

## 1.6 DART API 인증·키 운영

- 환경변수: `OPENDART_API_KEY`(필수), `OPENDART_API_KEY_2`(선택). 재무 배치(연간·분기 수집)는 외화 보고사 환산에 `ECOS_API_KEY` 도 둔다 — [[environment-secrets]]
- HTTP 요청 단위 키 주입: `?opendart=KEY` 쿼리 → contextvar(`_ctx_opendart_key`) → 인스턴스 캐시(키별 1개)
- 자동 키 회전: 키의 사정(`020` 키의 하루 한도 · `010`·`011`·`901` 키 문제)이면 남은 키를 모두 차례로(키마다 한 번), 그 밖의 비정상 답은 보조 키로 1회 재시도. 사용자 키 하나로 온 요청은 회전하지 않는다
- 사용량 추적: `_request_counter` (각 service가 `api_call_snapshot()` 차이로 호출 수 보고)
- **요청 단위 메모 (261021)**: 한 도구 호출(서버가 요청마다 여는 요청 장부 하나) 안에서 같은 `(엔드포인트, 정렬한 인자)` 의 DART 요청은 HTTP 를 **한 번**만 한다 (`DartClient._request` → 본체 `_request_http`).
  배수 도구(`price_multiple_data` firm) 한 번이 DART 14콜을 부르는데 그중 3콜(회사 개황·사업보고서 재무제표·배당)이 같은 인자의 중복이었다 (실측 261021: 삼성전자 14→11콜, 롯데리츠 16→14콜 — 응답은 전후 같다).
  - 동시에 오면 진행 중인 한 호출을 함께 기다리고(문서 `get_document_cached` 의 single-flight 와 같은 꼴 — 한 호출자가 취소돼도 공동 호출은 살린다), 끝난 뒤에 오면 그 결과를 쓴다. 호출자마다 **복사본**을 받아 서로 영향이 없다
  - 메모는 클라이언트(API 키 풀)마다 따로다 — 키의 사정인 `020`·`010` 이 다른 키로 번지지 않는다
  - **요청 경계를 넘지 않는다** — 메모는 요청 장부에 매달려 응답이 끝나면 함께 사라진다. 장부가 없는 경로(배치·스크립트)는 메모하지 않는다. 요청당 512항목·8MB(기억 중인 응답의 JSON 길이 합) 상한 — 넘으면 메모만 포기하고 호출은 그대로 한다
  - **기준일(`as_of`)·엄격 과거 시점(`strict_as_of`)이 걸린 호출은 메모하지 않는다** — 응답 행 거르기와 그 기록(`note_row_drop`·엄격 제외 장부)이 호출마다 남아야 하기 때문이다
  - 오류: DART 가 **숫자 상태로 답한** 오류(`013` 자료 없음·`020` 한도 …)는 같은 요청 안에서만 기억한다(`020` 은 남은 키까지 다 본 뒤의 답이다). 전송 오류·시간 초과·취소·`busy`, 그리고 숫자 상태라도 `800` 점검·`900` 알 수 없는 오류처럼 다시 하면 달라질 수 있는 것은 기억하지 않아 다음 호출(같은 요청 안의 재시도 포함)이 새로 시도한다
  - 키 회전·`020` 처리·`_request_counter`(`dart_api_calls`, **실제 HTTP 수**)는 `_request_http` 안에 그대로다 — 메모 적중은 세지 않는다. 결과 값은 바뀌지 않는다
- **회사 개황 캐시 (261021)**: `get_company_info`(`company.json`)는 프로세스 전역 메모리에 **1시간**(`_COMPANY_INFO_TTL_SEC`) 둔다 — 최대 512사(`_COMPANY_INFO_CACHE_MAX`, 항목당 약 1KB, 오래된 것부터 밀려남).
  키마다 두면 키 수만큼 곱해지므로 전역 하나(문서 캐시와 같은 이유). 정상 응답(`000`)만 두고, 기준일·엄격 과거 시점 호출은 건드리지 않으며, 호출자마다 복사본을 준다

## 1.7 DART API Rate Limit

| 항목 | 값 | 출처 |
|---|---|---|
| 일일 한도 | 20,000건/키 — 넘기면 status `020`, 자정(KST) 초기화(실측 2026-08-15 00:01) | OpenDART 개발가이드(020 설명) |
| 분당 한도 | 1,000건 (OPM cap 910) | OpenDART 정책 (초과 시 **그 키가** 차단 — 실측 2~3시간) |
| 클라이언트 최소 간격 | 0.1초 | `_MIN_INTERVAL_API` (분당 600회 이하 보장) |
| 키 회전 | 키의 사정(020·010·011·901)이면 남은 키 모두, 그 밖은 1회 | `_rotate_key()` |
| 중복 호출 | 한 요청 안의 같은 (엔드포인트, 인자)는 HTTP 1번 — 사용량 `dart_api_calls` 는 실제 HTTP 수 | 1.6 「요청 단위 메모」 |
| build_usage 노출 | `dart_api_calls`, `mcp_tool_calls`, `dart_daily_limit_per_minute`(옛 키 · 값 1000 = 분당 공표 한도, 이름과 달리 분당), `dart_limit_per_minute`(1000 분당 공표), `dart_client_cap_per_minute`(910 = OPM 분당 상한, `_API_RATE_LIMIT_PER_MINUTE`), `dart_limit_per_day`(20000 키당 하루) | services/contracts.py `build_usage(n)` — 사용량 블록은 이 함수로만 만든다 |

---

# 2. DART 웹 (HTML/PDF, 보조 경로)

## 2.1 dsaf001/main.do — 공시 viewer 메인

- URL: `https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}`
- 용도: `dcm_no` 추출 (PDF 다운로드 선결조건), viewer treeData 파싱
- 호출 위치: `_fetch_dcm_no()`, `_fetch_viewer_main_html()`
- 정규식: `\['dcmNo'\]\s*=\s*"(\d+)"` (makeToc JS에서 추출)
- User-Agent: `OpenProxyMCP/1.0 (research; +https://github.com/MarcoYou/open-proxy-mcp)`
- Rate limit: 0.4~1초 랜덤 + 프로세스당 분당 40건 (`_throttle_scrape` `_WEB_INTERVAL_RANGE`·`_WEB_PER_MINUTE`, 260906 1~2초에서 하향 · 차단 감지·백오프 `web_block_stats`)

## 2.2 report/viewer.do — 섹션 HTML

- URL: `https://dart.fss.or.kr/report/viewer.do`
- 파라미터: `rcpNo`, `dcmNo`, `eleId`, `offset`, `length`, `dtd`
- 용도: `get_viewer_document()`가 main.do의 노드별로 호출 (목차 단위)
- 사용 service: `shareholder_meeting_notice`/`_results`, `corp_gov_report` 등 document.xml이 깨질 때 2차 경로

## 2.3 pdf/download/pdf.do — PDF 다운로드 (open-proxy-ai 전용, 260712 이관)

- URL: `https://dart.fss.or.kr/pdf/download/pdf.do?rcp_no=...&dcm_no=...`
- 호출 위치: **open-proxy-ai `pipeline/pdf_download.py`의 `get_document_pdf(client, rcept_no)`**
  (2026-07-12 OPM `DartClient.get_document_pdf` 폐기·이관, OPM DartClient의 rate-limited 세션 재사용)
- 용도: opendataloader 입력 PDF 확보 (XML 파싱 실패 case)
- 검증: `%PDF` 매직 넘버 확인
- OPM 운영방침: PDF 경로 완전 제거(XML 단독). PDF/OCR 파서는 open-proxy-ai `pipeline/pdf_parser.py`가 소유

## 2.4 DART 웹 Rate Limit

| 항목 | 값 |
|---|---|
| 간격 | **1.0~2.0초 랜덤** (`_WEB_INTERVAL_RANGE`) · KIND 와 **시계 공유** |
| 직렬화 | **락**(`_web_rate_lock`, 260824) — 아래 참조 |
| 배치 사용 | 금지 (1건씩만) |
| User-Agent | 프로젝트명·연락처 명시 필수 |
| 비공식 | 공식 API가 아니므로 보수적 접근 |

1.0~2.0초는 **측정해서 나온 값이 아니라 예의로 고른 값**이다. OpenDART 는 공표된 한도가
있지만(분당 1,000·일 4만) 웹은 없다 — 「한도가 없다」가 아니라 **「한도를 모른다」**다.
게다가 격리 수준이 다르다: API 한도는 **키마다**라 한 사용자가 넘겨도 그 사람만 막히지만,
웹 차단은 **IP 기준**이라 우리 서버 하나가 막히면 **전원의 폴백 경로가 사라진다.**

### 260810 통일 — 왜 하나로 합쳤나

종전엔 DART 웹 `2.0초 고정` / KIND `1~3초 랜덤`으로 갈려 있었다. 그런데 둘은 이미
`_last_web_request` 라는 **같은 시계**를 쓰고 있었다 — KIND 를 긁으면 다음 DART 요청이
밀리고 그 반대도 마찬가지였다. 즉 **두 정책이 아니라 한 흐름의 간격만 호출 경로에 따라
달랐던 것**이라, 근거 없는 불일치였다. `_throttle_scrape()` 하나로 합쳤다.

| 정한 것 | 근거 |
|---|---|
| 1.0~2.0초 **랜덤** | 고정 간격은 요청이 정확히 규칙적으로 나가 기계 티가 그대로 난다. 지터는 예의 스크래핑의 표준 관행 |
| 하한 1.0초 | **새로 만든 값이 아니라** KIND 가 이미 쓰던 하한이다(사고 없이 운영 중). 0.5→0.67 req/s 는 차단 판정이 갈리는 구간이 아니다 — 차단은 지속 볼륨·병렬·정체불명 UA 같은 **패턴**이 좌우한다 |
| 시계 공유 유지 | 호스트별로 나누면 우리 **총** 요청률이 2배가 된다. 둘 다 드문 경로라 나눠서 얻을 게 없다 |

**숫자가 아니라 이 셋이 규칙이다** — ① 하한 1.0초 아래로 안 내림 ② 시계 공유 ③ 배치·병렬 금지.
「비용이 0이니 느릴수록 좋다」는 끝까지 밀면 10초가 되는 논리라, 어딘가에서 멈춰야 한다.

### 그래도 낮추고 싶으면 먼저 재라 (260810 계기)

`_throttle_scrape` 안에서 요청 장부에 `fetch_viewer`·`fetch_kind`(폴백 몇 번 갔나)와
`web_wait_ms`(그래서 얼마나 잤나)를 적고 `tool_call_events` 로 흘려보낸다.
조회는 `python3 scripts/usage_tracker.py --paths [일수]`.

| 읽은 결과 | 뜻 | 할 일 |
|---|---|---|
| 폴백이 드물다 | 간격은 아무 비용도 아니다 | 건드리지 않는다 |
| 폴백이 잦다 | 간격이 아니라 **주 경로(document.xml)가 자주 실패**한다 | 주 경로를 고친다 |

어느 쪽이든 「간격을 낮춘다」가 답이 되는 경우는 거의 없다. 계기를 스로틀 **안**에 둔 이유는
viewer/KIND 요청이 전부 그 함수를 지나기 때문이다 — 호출측에 두면 fetch 함수가 늘 때마다
조용히 누락된다.

폴백이 도는 조건 자체가 드물다: `_fetch_viewer_sec` 주석대로 **`document.xml` 이 아예 없는
회사**(KB금융·삼성화재류)에서만 돈다.

---

# 3. KIND (KRX 한국거래소, HTML)

베이스 URL: `https://kind.krx.co.kr`

## 3.1 disclsviewer.do — 공시 본문 viewer

- URL: `https://kind.krx.co.kr/common/disclsviewer.do`
- 호출 위치: `DartClient.kind_fetch_document(acptno)` (3-step iframe 패턴)

3-step crawling:

1. `?method=search&acptno={acptno}` → HTML에서 `<option value="docNo|...">` 정규식 추출
2. `?method=searchContents&docNo={docNo}` → JS `setPath('목차URL', '본문URL')` 정규식에서 본문 URL 추출
3. 본문 URL GET → 최종 HTML 반환

- BeautifulSoup `lxml` 파서로 후처리 (services/value_up._kind_html_to_text 등)
- Rate limit: `_throttle_kind()` — DART 웹과 같은 1.0~2.0초 랜덤·같은 시계(`_WEB_INTERVAL_RANGE`, 260810 통일)

## 3.2 disclosure/details.do — 상세 검색 (POST)

- URL: `https://kind.krx.co.kr/disclosure/details.do`
- 호출 위치: `DartClient.kind_search_disclosures(...)`, `kind_search_value_up(...)`
- POST payload: `method=searchDetailsSub`, `searchCorpName`, `repIsuSrtCd=A{stock_code}`, `fromDate`, `toDate`, `disclosureType01={code}`
- 응답: HTML 테이블 → `_parse_kind_disclosure_rows()` (acptno, datetime, corp_name, report_name, filer_name 추출)
- KIND 세부 공시 코드:
  - `0184`: 기업가치 제고 계획 (밸류업) — `_KIND_VALUE_UP_DISCLOSURE_CODE`
- 일반 검색(searchDetailsMainSub)은 봇 감지로 차단됨. 위 POST 형태는 정상 동작

## 3.3 rcept_no → acptno 변환 ([[KRX-KIND]] 참조)

거래소 공시(`pblntf_ty=I`)는 100% `80→00` 변환으로 KIND viewer 접근 가능:

```
DART rcept_no: YYYYMMDD80XXXX (거래소 공시)
KIND acptno:   YYYYMMDD00XXXX (같은 문서)
변환: rcept_no.replace("80", "00", 1)
```

KOSPI 200 8개 기업 전수 검증: 100% 매칭. 자세한 화이트리스트는 [[DART-KIND-매핑-화이트리스트-2026-04]].

## 3.4 KIND 화이트리스트 (병행 허용 4종)

| key | DART selector | KIND title 검증 |
|---|---|---|
| `agm_result` | pblntf_ty=I + "주주총회결과" | "정기/임시주주총회 결과" |
| `dividend_decision` | pblntf_ty=I + "현금ㆍ현물배당결정" | "현금ㆍ현물배당 결정" |
| `value_up` | pblntf_ty=I + "기업가치제고/밸류업" | "기업가치 제고 계획(자율공시)" |
| `litigation_exchange_style` | pblntf_ty=I/B + "소송/경영권분쟁소송" | "소송 등의 …", "경영권분쟁소송" |

비화이트리스트(KIND 병행 금지): 주주총회소집공고, 위임장권유참고서류, 5% 대량보유, 임원소유보고, 자기주식 이벤트.

## 3.5 사용 OPM service 매핑

| service | 호출 위치 | 용도 |
|---|---|---|
| shareholder_meeting_results | `_fetch_kind_results` (services/shareholder_meeting.py) | 주총결과 80→00 변환 후 본문 |
| ownership_structure | services/ownership_structure.py:375 | 변동신고서 본문 보강 |
| value_up | services/value_up.py | 밸류업 plan 본문 + KIND 직접 검색 |

shareholder.py(v1)도 acptno → rcept_no 양방향 fallback 사용(line 1252-1256).

## 3.6 KIND Rate Limit

| 항목 | 값 |
|---|---|
| 최소 간격 | 1.0~2.0초 random (`_WEB_INTERVAL_RANGE`) — DART 웹과 시계 공유 (260810 통일) |
| 배치·병렬 | 금지 (CLAUDE.md) |
| 공식 API | 아님 (HTML 크롤링) |
| User-Agent | OpenProxyMCP/1.0 명시 |

---

# 4. Naver 검색·금융 API

## 4.1 Naver 뉴스 검색 OpenAPI

- 호출 위치: `DartClient.news_search(query, display=100, sort)`
- 키 짝마다 주소·헤더가 묶여 있다. 두 키는 서로의 방식에서 401 이라 바꿔 끼울 수 없다(260820 실측).

| 우선 | 환경변수 | Endpoint | 헤더 |
|---|---|---|---|
| 1 | `NAVER_API_HUB_CLIENT_ID`, `NAVER_API_HUB_CLIENT_SECRET` | `https://naverapihub.apigw.ntruss.com/search/v1/news` | `X-NCP-APIGW-API-KEY-ID`, `X-NCP-APIGW-API-KEY` |
| 2 | `NAVER_SEARCH_API_CLIENT_ID`, `NAVER_SEARCH_API_CLIENT_SECRET` | `https://openapi.naver.com/v1/search/news.json` (2027-06-30 까지) | `X-Naver-Client-Id`, `X-Naver-Client-Secret` |

- 두 짝 모두 없으면(한쪽 반만 있어도) 호출하지 않고 `[]`. 응답 items 필드는 둘이 같다.
- 260926: 종전 코드는 2번 키 이름을 읽어 1번 주소로 보내 **늘 401 → 조용히 `[]`** 였다.
- 파라미터: `query`(필수), `display`(최대 100), `sort`(date/sim)
- 사용 tool: `news_check`(v1) — 이사·감사 후보자 부정 뉴스 (33 키워드 필터, 11개 일간지 우선)
- v2 통합 상태: 미통합. value_brief / vote_brief 매트릭스의 `adverse_news` dim은 manual

## 4.2 Naver 뉴스 Rate Limit

| 항목 | 값 |
|---|---|
| 무료 한도 | 25,000회/일 (네이버 정책) |
| 분당 한도 | 100건 (무료) / 250건 (유료) |
| 본 클라이언트 최소 간격 | `_throttle_api`(0.1초) 공유 |

## 4.3 Naver Finance — 종가 (siseJson)

- Endpoint: `https://api.finance.naver.com/siseJson.naver`
- 호출 위치: `DartClient._portal_stock_price(stock_code, base_date)`
- 파라미터: `symbol`, `requestType=1`, `startTime`, `endTime`, `timeframe=day`
- 응답 파싱: 정규식 `\["(\d{8})",(\d+),(\d+),(\d+),(\d+)` → 종가 추출
- 비거래일 fallback: 7일 전부터 재조회 → 마지막 행 사용
- 사용: get_stock_price()의 KRX fallback (KRX_API_KEY 미설정 또는 응답 없음 시)

## 4.4 Naver Finance — 업종 (coinfo + sise_group)

- Endpoint: `https://finance.naver.com/item/coinfo.naver?code={stock_code}` → `sise_group_detail.naver?type=upjong&no={sector_code}`
- 호출 위치: `DartClient.get_portal_corp_profile(stock_code)`
- 응답 파싱: 페이지 1에서 `sise_group_detail.naver?type=upjong&no=(\d+)` 정규식 → sector_code, 페이지 2에서 `<title>` 태그로 sector_name
- Rate limit: 각 단계 사이 `asyncio.sleep(2.0)`
- 사용: company / value_up 업종 메타

## 4.5 Naver Finance — 기타 (참고)

- 시가총액·로고: AlphaSquare CDN 경유 — open-proxy-ai 프론트엔드 별도 수집
- 192개 기업 로고 정적 (네이버 금융 직접 호출 아님)

## 4.6 지수 구성종목 목록 — 코스피200·코스닥150 원장 (261018)

- Endpoint: `https://m.stock.naver.com/api/index/{KPI200|KQI150}/enrollStocks?page=N&pageSize=60` (61 이상은 400 — 짧은 쪽이 끝)
- 호출 위치: `scripts/krx_index_members.py` (매일 `.github/workflows/index-members-daily.yml`) — 서버는 부르지 않는다. 원장 `krx_index_members` 만 읽는다.
- 받는 것: **종목코드만**(`itemCode`). 응답의 가격·시총은 쓰지 않는다.
- 왜 이 원천인가: KRX 는 지수 구성종목을 무료 API 로 주지 않는다 — Open API 서비스 목록에 구성종목 항목이 없고(지수는 일별시세만),
  정보데이터시스템 「지수구성종목」(`MDCSTAT00601`)은 조회·내려받기 모두 로그인 요구(비로그인 응답 `LOGOUT`, 261018 확인),
  지수 사이트는 구성정보를 구매 요청으로 판다. 옛 PC 편입종목 화면(`finance.naver.com/sise/entryJongmok.naver`)은 410 으로 닫혔다.
- **확인(쓰기 조건)**: 목록 종목들의 KRX 일별 매매정보(`sto/stk_bydd_trd`·`sto/ksq_bydd_trd`) `MKTCAP`·`ACC_TRDVOL`·`ACC_TRDVAL` 합이
  같은 날 KRX 지수 일별시세(`idx/kospi_dd_trd` 「코스피 200」·`idx/kosdaq_dd_trd` 「코스닥 150」)의 세 값과 **정확히 같을 때만** 쓴다.
  실측 20261016: 코스피200 201종목·코스닥150 150종목 모두 세 합 차이 0. 종목 하나만 달라도 거래량 합이 어긋난다.
- 구성이 바뀐 날(정기변경 6·12월 · 수시변경) 아침에는 목록(새 구성)과 전 거래일 KRX 합계(옛 구성)가 어긋난다 → 쓰지 않고 경고, 다음 날 맞춘다.
  직전 확인에서 4일을 넘게 어긋나면 실패로 알린다. 목록·KRX 응답 이상(HTTP 오류·모양·종목 수 범위 밖·중복)은 바로 실패.
- 속도: 지수당 4~7요청, 프로세스 공통 웹 속도제한(`throttle_web_request`). KRX 3~7콜/일.
- 없는 것: KRX300 — 이 목록에 그 지수가 없고 KRX 무료 원천도 없다(원장 미보유). 코리아 밸류업 지수(`KVALUE`)·코스피100(`KPI100`)은 같은 방식으로
  받아 KRX 합계와 맞는 것을 확인했지만(20261016 차이 0) 유니버스 문법이 그 이름을 받지 않아 담지 않았다.

## 4.7 한국은행 ECOS — 무위험 금리 (`ecos_rate`, 261021)

- Endpoint: `https://ecos.bok.or.kr/api/StatisticSearch/{키}/json/kr/1/100000/{통계표}/D/{시작}/{끝}/{항목}` — **키가 URL 경로에 들어간다**(예외·로그에 URL 을 싣지 않는다, 규칙 3).
- 호출 위치: `scripts/ecos_rate_daily.py` (매일 `.github/workflows/ecos-rate-daily.yml`) — 서버는 부르지 않는다. 표 `ecos_rate` 만 읽는다(스키마 `scripts/ecos_rate_ddl.py`).
- 계열(`series` 한 글자): C=CD 91일 `817Y002/010502000` · K=KOFR `817Y002/010901000` · 1=국고채 1년 `…/010190000` · 3=국고채 3년 `…/010200000` · B=한국은행 기준금리 `722Y001/0101000`.
  연율 %. 기간 수익률로의 변환(예 ACT/365)은 읽는 쪽이 하고, 응답에 변환 규칙을 밝힌다.
- 모양 두 가지: 시장금리는 **거래일만** 값을 준다(마지막 날짜가 어제 — KOFR 은 하루 더 늦다). 기준금리는 **달력 날짜마다 같은 값**을 줘서(실측 261021: 2015-12-01~ 3,976행, 값이 바뀐 날 24곳)
  표에는 **바뀐 날(적용일)만** 둔다. 읽는 법은 계열 모두 같다 — `d <= 기준일` 인 가장 늦은 행.
- 보관: 목표는 DB 에 3년 창(주간 시세 보존 하한 − 14일)과 계열마다 창 앞 마지막 행 하나만 두는 것이다. 그 앞 전 기간(2015-12~)은 DB 밖 보관본(콜드 서빙)이 맡는다.
  창 밖을 지우는 `--prune` 은 보관본 coverage·발행 뒤 72시간 가드를 통과해야만 하고, **보관본이 서빙되기 전에는 지우지 않으므로 그 전의 표는 3년 창보다 길다**
  (거래일마다 계열 4개가 한 줄씩, 연 약 980행. 로컬 PG17 24개월 모의: 지우기 없음 4,977행 0.37 MiB · 지우기 있음 3,021행 0.25 MiB).
  **창 밖 지우기가 실제로 도는 조건** — 일별 실행(`.github/workflows/ecos-rate-daily.yml`)은 받기 단계가 성공한 뒤 아래 넷이 **모두** 맞을 때만 `--prune --manifest … --if-ready` 를 이어 돈다:
  ① 저장소 변수 `ECOS_RATE_WRITE=1` ② 저장소 **비밀값** `ECOS_RATE_COLD_MANIFEST`(보관본 서빙 목록 주소) ③ 수동 실행의 prune 입력이 아님 ④ dry_run 이 아님.
  ②는 서명 URL 이라 비밀값이다(변수로 두면 공개 저장소의 Actions 로그가 단계의 env 를 그대로 보여 준다). 서명을 갱신할 때 함께 갱신한다 — 만료되면 목록 읽기 실패로 그 단계가 빨갛게 끝난다(`--if-ready` 가 누그러뜨리는 것은 아래 「아직 아님」뿐).
  ②가 없으면 단계를 건너뛰고 「창 밖 지우기를 건너뛰었다」 안내 줄(`::notice::`)을 매일 남긴다 — 발행 전에는 정상, 그동안 표는 3년 창보다 길어진다.
  **켜는 순서**: 보관본 발행 → 서버가 목록에서 `ecos_rate_hist` 를 읽는 것을 확인(coverage 끝 ≥ 창 하한 − 1일) → 비밀값 `ECOS_RATE_COLD_MANIFEST` 설정 → 다음 일별 실행에서 스크립트 가드가 따로 본다.
  가드를 통과하기 전(목록에 데이터셋 없음 · coverage 부족 · 목록 발행 72시간 전)에는 지우지 않고 정상 종료(안내 줄), 그 밖의 이상은 실패. 지우기 가드의 하나는 「지우고 남는 행이 표의 30% 이상」이다 — 지우기가 오래 밀려도(24개월 밀리면 지울 행이 표의 39%) 막히지 않고, 창 하한이 잘못 밀려 거의 다 지우게 되는 때만 막는다.
  (목록 발행 시각은 목록 전체의 `updated_at` 이라 다른 데이터셋을 발행해도 72시간이 다시 센다 — 지우기가 늦어질 뿐 틀리지는 않는다.)
- 보관본 원천 Parquet: 칸은 `scripts/ecos_rate_daily.py` 의 `COLD_COLUMNS` = (`series`, `d`, `rate`) — 날짜는 YYYYMMDD 문자열, 값은 받은 십진 값(DOUBLE). 콜드 발행기의 데이터셋 정의와 글자까지 같아야 한다
  (날짜 칸을 `dd` 로 쓴 보관본은 발행기가 읽지 못했다). 쓰는 함수 `write_cold_parquet` 가 정본이고 백필은 그것을 부른다. 서빙 목록의 coverage 칸 이름(`min_dd`·`max_dd`)은 원천 칸 이름과 별개다.
- 쓰기: 저장소 변수 `ECOS_RATE_WRITE=1` 이 있어야 쓴다(없으면 계획만). 최근 30일은 매번 다시 받아 **옛 값 조건**으로 비교·정정하고(같은 값은 건드리지 않는다),
  0.5%p 넘게 바뀐 옛 값은 계열 정의 변경으로 보고 쓰지 않고 실패한다. 한 달에 한 번 `VACUUM (ANALYZE)` + 기본키 색인 다시 만들기 — 이 뒷정리가 실패해도 이미 커밋된 적재는 성공으로 두고 경고만 낸다.
  쓰기 전 백업: 바꾸는 행(정정)과 지우는 행(창 밖)의 **옛 값**을 같은 트랜잭션 안에서 먼저 CSV(`--backup-dir`)로 남기고 워크플로가 산출물(30일)로 올린다 — 파일을 못 쓰면 쓰기도 없다. 새 행은 옛 값이 없어 대상이 아니다.
- 운영 적용 순서: ① 표 만들기 `scripts/ecos_rate_ddl.py` → ② 서버 계정 권한 행렬(비공개 운영 스크립트)에 이 표를 「서버가 읽지 않는 표」로 넣고 점검 → 적용 → 검증 순으로 돌린다 → ③ 백필 → ④ 저장소 변수 `ECOS_RATE_WRITE=1`. 보관본이 발행·확인되면 비밀값 `ECOS_RATE_COLD_MANIFEST`(위 「창 밖 지우기가 실제로 도는 조건」).
- 함정: ① `ecos_fx_rate`(환율 캐시)와 다른 표다 — 읽는 곳·뜻·보관이 다르다. ② 휴일·게시 지연 날은 앞 값을 쓰는 것이 읽는 쪽 몫(읽는 도구가 생기면 응답에 기준 날짜를 밝히는 필드를 두는 것이 예정이며, 필드 이름은 아직 정하지 않았다). ③ CD91 은 최근 발행량이 줄어 대표성 논란이 있어 KOFR·국고채를 함께 둔다.
- 속도: 하루 ECOS 5콜(계열당 1). 표가 비었을 때 첫 실행만 같은 5콜이 창 전체를 받는다. DART·KRX 0.

---

# 5. KRX Open API (종가 1차)

- Endpoint: `https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd`
- 호출 위치: `DartClient._krx_stock_price(stock_code, base_date)`
- 파라미터: `AUTH_KEY`, `basDd`(YYYYMMDD)
- 응답: JSON `OutBlock_1[]` → ISU_CD 매칭 → TDD_CLSPRC
- 환경변수: `KRX_API_KEY` 또는 `KRX_OPEN_API_KEY`
- 승인 상태: 전 서비스 승인 완료 (2026-04-08, 자세한 내역은 [[KRX-KIND]])
- get_stock_price()는 KRX → Naver 순서로 fallback
- 지수 일별시세(`idx/kospi_dd_trd`·`idx/kosdaq_dd_trd`·`idx/krx_dd_trd`): 지수마다 `MKTCAP`·`ACC_TRDVOL`·`ACC_TRDVAL` = 구성종목 합.
  지수 구성종목 원장 적재가 목록 확인에 쓴다(4.6). 구성종목 목록 자체는 Open API 에 없다.

---

# 6. Upstage Document Parse API (OCR) — open-proxy-ai 전용 (260712 이관)

- Endpoint: `https://api.upstage.ai/v1/document-ai/document-parse`
- 호출 위치: **open-proxy-ai `pipeline/pdf_parser.py` `upstage_ocr_parse()`** (OPM에서 폐기·이관 2026-07-12)
- 인증: Bearer 토큰 (`UPSTAGE_API_KEY` 환경변수)
- 입력: PDF 멀티파트 (`document` 필드), `output_formats=["markdown"]`
- 응답: JSON `content.markdown`
- 파일 크기 제한: 약 50MB → 페이지 추출 후 호출 권장 (`extract_pdf_pages` 헬퍼)
- 처리 시간: 10초+ (3-tier에서 가장 느림)
- 사용 흐름: opendataloader 마크다운 → 키워드로 페이지 특정(`_PARSER_KEYWORDS`) → 앞뒤 1페이지 포함 최대 10페이지 추출 → Upstage OCR → 동일 파서 재투입(`ocr_fallback_for_parser`)
- 적용 대상: vector glyph PDF(M레거시 정책 등), 이미지 공고
- v2: 기본 미사용. v1 / open-proxy-ai 파이프라인에서 활용

---

# 7. opendataloader-pdf (PDF → 마크다운) — open-proxy-ai 전용 (260712 이관)

- 라이브러리: `opendataloader-pdf` (Java 11+ 의존)
- 호출 위치: **open-proxy-ai `pipeline/pdf_parser.py`** (OPM에서 폐기·이관 2026-07-12)
- 사용 흐름: DART 웹에서 PDF 다운로드 → opendataloader-pdf로 마크다운 변환(table_method="cluster", keep_line_breaks=True) → AGM 파서 재실행
- 한국어 OCR 벤치마크 1위, KOSPI 200 198개 PDF 변환 완료
- 한계: 일부 PDF에서 변환 품질 불안정 → Upstage OCR로 최종 fallback (아래 7절 참조)
- v2: 기본 미사용 (PDF 경로 제외)

---

# 8. 자산운용사 의결권 행사 데이터 (정적 JSON, 호출 0회)

- 위치: `open_proxy_mcp/data/asset_managers/`
- 로딩 service: (구 백엔드 — private archive)
- 외부 호출: 0회. proxy_guideline tool 단독 동작 (cross-domain 시만 DART 호출)

| 디렉토리/파일 | 내용 | 건수 |
|---|---|---|
| `_index.json` | 운용사 메타 + OPM 디폴트 정책 매핑 | 1 |
| `_consensus_matrix.json` | 운용사 합의/이견 매트릭스 | 1 |
| `_decision_matrices.json` | 12 카테고리 의사결정 매트릭스 (100 dim, 76 빙고 패턴) | 1 |
| `policies/` | 운용사 정책 + open_proxy_v1.json | 9 |
| `records/` | 운용사 행사내역 (period별) | 16 |

운용사 8종 + OPM 1종(open_proxy):
- a_activist, b_foreign, c_activist, k_legacy, m_legacy, s_legacy, sa_legacy, t_activist (전부 익명 코드 — 실명 매핑은 gitignored manager_aliases.json), open_proxy_v1 (OPM 자체 정책 v1.2)

원본 정적 데이터(엑셀·PDF):
- `wiki/raw/records/2024.04~2026.04 *_의결권 행사내역.xlsx` (17건)
- `wiki/raw/policies/2025.04~2026.04 *_의결권행사 내부지침.pdf` (9건, N연기금 포함)
- 위 원본은 사전 수집·구조화되어 JSON으로 사내 보관

---

# 9. 11 Data Tool 별 Source Flow

각 data tool의 entry point + scope별 호출 흐름. 모든 service는 `open_proxy_mcp/services/`에 위치.

## 9.1 company

- Endpoint: `corpCode.xml` (캐싱), `list.json` (최근 180일 lookback)
- Source: DART API only
- Scope: 1 (회사 식별 + recent filings)

## 9.2 shareholder_meeting_notice · shareholder_meeting_results (구 shareholder_meeting — 2026-05 분리, service 파일은 하나)

- Primary: `list.json` (pblntf_ty=E, "소집") + `document.xml` 본문
- Results scope: `list.json` (pblntf_ty=I, "주주총회결과") + KIND `disclsviewer.do` (80→00 변환)
- Fallback: `viewer.do` HTML (XML 깨질 때)
- Scope: summary, agenda, board, compensation, aoi_change, results, full
- 캐시: get_document_cached — 메모리 바이트 예산 LRU + 볼륨 디스크
- KIND 호출: 주총결과 본문 보강 (KOSPI 200 100% 매핑)

## 9.3 ownership_structure

- Primary: DS002 4종 (`hyslrSttus`, `hyslrChgSttus`, `mrhlSttus`, `stockTotqySttus`) + DS004 (`majorstock`)
- 보조: `document.xml` PUR_OWN 태그 (보유목적 — majorstock에 필드 없음)
- changes scope: `list.json` (pblntf_ty=I, "최대주주등소유주식변동신고서") + KIND HTML
- Scope: summary, major_holders, blocks, treasury, control_map, timeline, changes

## 9.4 dividend_disclosure (구 dividend — 260902 개명; DB 시계열은 dividend_data 로 분리)

- Primary: DS002 `alotMatter.json` (사업보고서 alotMatter)
- 보강: `list.json` (pblntf_ty=I, `_DIV_KEYWORDS` — 6 배당 공시유형 매칭) + `document.xml` (`_parse_dividend_decision`)
- Scope: summary, detail, history, policy_signals
- alotMatter가 비면 배당결정 공시 합산을 source of truth로 사용 (분기배당·특별배당 fallback)

## 9.5 treasury_share

- Primary: DS005 4종 (취득/처분/신탁체결/신탁해지) — 병렬 호출
- 추가: `list.json` + `_CANCELATION_KEYWORDS` (소각결정은 별도 endpoint 없음) → `document.xml` 파싱
- 연간 잔고: DS002 `tesstkAcqsDspsSttus.json`
- Scope: summary, events, acquisition, disposal, cancelation, annual

## 9.6 proxy_contest

- Primary: `list.json` (pblntf_ty=D, `_PROXY_KEYWORDS`) + `list.json` (pblntf_ty=I/B, `_LITIGATION_KEYWORDS`)
- 본문: `document.xml` (`_parse_holding_purpose`, 위임장 회사측/주주측 구분)
- vote_math scope: ownership_structure 재사용 (DS002+DS004 호출 + `_build_control_map`)
- Scope: summary, fight, litigation, signals, timeline, vote_math
- KIND 보강: 소송 화이트리스트 4종 (litigation_exchange_style)

## 9.7 value_up

- Primary: `list.json` (pblntf_ty=I, "기업가치제고/밸류업") + `document.xml`
- 직접 검색 fallback: KIND `kind_search_value_up()` (POST disclosureType01=0184) → `kind_fetch_document()`
- Scope: summary, plan, commitments, timeline
- 분류: meta_amendment(고배당기업 형식 재공시) / progress(이행현황) / plan(원본·개정)

## 9.8 corporate_restructuring

- Primary: DS005 4종 병렬 — `cmpMgDecsn`, `cmpDvDecsn`, `cmpDvmgDecsn`, `stkExtrDecsn`
- Source: DART API only (구조화 직접 endpoint)
- Scope: summary, merger, split, share_exchange

## 9.9 dilutive_issuance

- Primary: DS005 4종 병렬 — `piicDecsn`(유증), `cvbdIsDecsn`(CB), `bdwtIsDecsn`(BW), `crDecsn`(감자)
- Source: DART API only
- Scope: summary, rights_offering, convertible_bond, warrant_bond, capital_reduction
- 부가 계산: `_pct_of_existing` (기존 발행주식 대비 신주 비율 — 희석률 근사)

## 9.10 corporate_deals · order_contracts (구 related_party_transaction — equity_deal → corporate_deals, supply_contract → order_contracts)

- Primary: `list.json` (pblntf_ty=B/I, `_EQUITY_DEAL_KEYWORDS` 4종 + `_SUPPLY_CONTRACT_KEYWORDS` 4종) + `document.xml`
- DART 전용 구조화 endpoint 없음 (list+키워드 매칭)
- Scope: summary, equity_deal, supply_contract
- 자회사 주요경영사항 / 자율공시 / 본인 제출 구분

## 9.11 corp_gov_report

- Primary: `list.json` (pblntf_ty=I, "기업지배구조보고서공시") + `document.xml` 원문 파싱
- 전용 구조화 endpoint 없음
- 파싱: 15 핵심지표 라벨 매칭 (BeautifulSoup lxml + XBRL 태그 시작 전까지 텍스트 스캔)
- 지표값: O/X/○/×/해당없음 표준화
- Scope: summary, metrics, principles, filings, timeline
- 대상: 2024년 사업연도부터 KOSPI 의무, KOSDAQ은 자율

## 9.12 (참고) evidence

- DART viewer URL 생성 (`_build_viewer_url`)
- KIND_HTML/DART_XML/DART_HTML/DART_API source 모두 DART viewer URL로 통일 (KIND 직접 URL은 404 위험)

## 9.13 (참고) proxy_guideline

- DART 호출 0회. 100% 정적 JSON.
- 6 scope: policy, record, predict, compare, consensus, audit

---

# 10. 3-tier Fallback 체계 (OPM 은 XML tier 만 — [[XML-vs-PDF]]. PDF/OCR 은 open-proxy-ai 로 이관)

> ⚠️ **2026-07-12: OPM은 XML 단독.** `_pdf`(get_document_pdf + opendataloader)·`_ocr`(Upstage) tier와
> 관련 코드(`pdf_parser.py`, `get_document_pdf`, `agm_parse_fallback`)는 OPM에서 폐기하고
> open-proxy-ai(`pipeline/pdf_parser.py` + `pipeline/pdf_download.py`)로 이관했다. 아래 표의 `_pdf`/`_ocr`
> tier는 이제 **open-proxy-ai 전용**이다. 이 섹션 6·7(Upstage·opendataloader)도 동일.

8개 AGM 파서의 fallback 패턴(OPM은 `_xml` tier만, PDF/OCR은 open-proxy-ai):

| Tier | Source | 속도 | 정확도 | 비용 | 위치 |
|---|---|---|---|---|---|
| `_xml` | DART API + document.xml | 빠름 | 98%+ | 무료 | OPM + open-proxy-ai |
| `_pdf` | get_document_pdf + opendataloader | 4초+ | 98%+ | 무료 | open-proxy-ai 전용 |
| `_ocr` | Upstage Document Parse | 10초+ | 100% | 유료 | open-proxy-ai 전용 |

흐름:
1. `agm_*_xml` 호출 → CASE_RULE 기준 검증
2. SUCCESS → 즉시 답변
3. SOFT_FAIL → AI 자체 보정 (구분자/누락 추론)
4. (OPM) 보정 불가 → 한계 명시하고 답변 / (open-proxy-ai) PDF·OCR 폴백 체이닝

OPM 운영(2026-07-12~ XML 단독):
- PDF 다운로드·OCR 경로 완전 제거 (open-proxy-ai로 이관)
- DART_XML이 깨지면 viewer.do HTML(get_viewer_document) → KIND 화이트리스트 4종 → REQUIRES_REVIEW로 종결

---

# 11. Rate Limit + 캐싱 종합

## 11.1 Rate Limit per source

| Source | 최소 간격 | 한도 | 처리 |
|---|---|---|---|
| DART OpenAPI | 0.1초 (`_MIN_INTERVAL_API`) | 1,000/min, 20,000/day | 키 회전 |
| DART 웹·KIND | 0.4~1초 랜덤 + 분당 40건 (`_WEB_INTERVAL_RANGE`·`_WEB_PER_MINUTE`, 프로세스 시계 하나) | 비공식 (IP 차단 위험) | User-Agent 명시 |
| KIND | 1~3초 random (`_throttle_kind`) | 비공식 | 봇 감지 회피 |
| Naver 뉴스 API | 0.1초 (공유) | 25,000/day, 분당 100 | 키 환경변수 |
| Naver Finance | 2.0초 (asyncio.sleep) | 비공식 | UA 위장 (Mozilla/5.0) |
| KRX Open API | 0.1초 (공유) | 미공개 | 서비스 승인 필요 |
| Upstage OCR | 클라이언트 미강제 | 유료 (per-page) | 50MB 파일 제한 |


## 원장 관측 (`/health` `registry`)

회사 목록(`corpCode.xml` 118,942사 · 실측 RSS 약 68MB)과 정기보고서 명부는 3층 캐시다 —
메모리 → sqlite(TTL 7일) → DART 다운로드. **만료는 sqlite 층에만 있다.** 메모리에 한 번 올라가면
프로세스가 죽을 때까지 그대로이고, fly 는 `suspend`(재시작이 아니라 재개)라 배포가 없으면
프로세스가 몇 주 산다 → 원장이 그만큼 묵는다.

증상은 조용하다 — 최근 상장사를 물으면 「그런 회사를 찾지 못했습니다」가 나오고, 응답 어디에도
「내 원장은 3주 전 것」이라는 말이 없다. 그래서 **TTL 을 걸기 전에 먼저 보이게 했다**(260909):

`/health` 의 `registry` = `{entries, bytes_est, as_of, loaded_at, age_hours, source}`

- `_CACHE_REGISTRY` 에 넣지 않는다 — 거기는 `LruByteCache` 전용이고 이 둘은 list/frozenset 이다.
- **바이트는 표본(500) 추정**이다. `/health` 는 자주 불리는데 118k dict 를 매번 재귀로 재면
  관측이 그 자체로 비용이 된다. 실측: 적재 시 기록 7ms · `/health` 호출당 0.001ms · RSS +1MB.
- 표본을 `list(items)[:500]` 으로 뜨지 않는다 — 그러면 **전체 사본이 먼저 생겨** 관측하려다
  메모리를 두 배로 쓴다(명부는 frozenset). `islice` 로 앞 500개만 꺼낸다.
- 관측이 실패해도 서빙은 계속된다(예외를 삼키고 debug 로만 남긴다).

TTL 은 이 수치를 며칠 보고 정한다. 걸 때는 **fail-open** — 만료됐는데 새로 못 받으면 옛 값을
계속 쓰되 「원장 기준일 이후라 아직 없을 수 있다」를 응답에 붙인다. 만료됐다고 답을 못 내면 더 나쁘다.

### 회사 목록 새로 받기 (261018)

수치가 답을 줬다 — 261018 운영 두 머신 모두 `as_of` 20261008·119,601사(그날 새로 받은 명부는 119,664사)였고,
10/15 상장 멜콘(179880)을 `price_multiple_data`·`trading_data` 가 not_found 로 답했다. `age_hours` 는 0.2 였다 — 그 값은
**메모리에 올린 뒤** 시간이라 원장의 나이를 가렸다. 그래서 `fetched_at`·`fetched_age_hours`(DART 에서 받은 시각)를 따로 싣는다.

새로 받는 길은 셋이고, 모두 실패하면 옛 명부를 그대로 쓴다(fail-open):

| 길 | 언제 | 상한 |
|---|---|---|
| 정기 | 서버 백그라운드 갱신기(`services/corp_registry_refresher.py`)가 기동 직후·1시간마다 보고, 받은 지 23시간이 넘었으면 | 하루 1회 |
| 신규상장 신호 | `krx_daily` 최신일(활성 상장 명부)에 있는 보통주 코드(끝자리 0)가 명부에 없으면 — 새로 받은 뒤에도 남은 코드(DART 가 늦게 싣는 경우)는 다시 세지 않고, 받기가 실패했으면 다음 확인 때 다시 | 시간당 1회 |
| 해석 실패 | `lookup_corp_code_all` 이 빈 결과(종목코드는 명부에 없는 보통주 코드일 때만)이고 명부를 받은 지 1시간이 넘었으면 — 받고 다시 찾는다(최대 20초 대기) | 시간당 1회 |

- **우선주는 신호가 아니다.** DART 명부에는 보통주 코드만 있어 `krx_daily` 의 우선주 114개(261018 실측, 전부 끝자리 0 이 아님)는
  늘 명부에 없다. 보통주 코드 기준으로는 새 명부에서 없는 코드가 0개, 10/12 명부에서는 179880 하나였다.
- **명부에 이미 있는데 못 찾은 코드**(영숫자 코드 해석 등)는 받아도 안 바뀌므로 신호로 세지 않는다.
- **받기는 프로세스에 하나** — 동시 요청은 같은 받기를 기다린다. 기다리던 요청이 끊겨도 받기는 끝까지 간다.
  받기는 요청 문맥을 물려받지 않고 서버 키로 돈다(서버 키가 없을 때만 요청자의 키).
- **반쪽 응답 방어** — 검증(10만 사·상장 3,000·영문명 90%)에 더해, 상장사 수가 지금 명부보다 3% 넘게 줄었으면 버린다.
  단 지금 명부가 3일 넘게 묵었으면(며칠째 같은 이유로 버리는 중) 받아들이고 WARNING 을 남긴다 — DART 쪽 정리였다면 영영 굳는 편이 더 나쁘다.
- **해석은 조각으로** — 30MB XML 을 `ET.fromstring` 한 번에 읽으면 그동안 GIL 을 안 놓아 스레드로 빼도 이벤트 루프가 멈춘다.
  256KB 조각으로 먹이는 풀 파서로 바꿨다(실측 119,664사: 정점 261MB → 69MB, 1.6초 → 1.8초).
- **머신 두 대** — 볼륨(sqlite)이 머신마다 따로라 각자 받는다. 서로 기다리거나 덮어쓸 것이 없고, 잠깐 한쪽만 새 명부여도
  다른 쪽은 해석 실패 길이 메운다. 같은 파일을 쓰는 다른 프로세스(로컬)가 더 새로 받아 두었으면 받지 않고 그것을 올린다.
- 끄기: `OPM_CORP_REFRESHER=0` — 자동으로 새로 받는 길 전부(시험·비상용). 프로세스가 뜰 때의 첫 적재(sqlite 7일 상한)는 그대로다.
- 덤: 사명 이력(`corp_name_history`)은 받을 때마다 남으므로 이제 하루 단위로 쌓인다(종전 주 단위).

## 11.2 캐시 정책

한도는 **개수가 아니라 바이트**다. 260804 OOM 의 뿌리가 개수 예산이었다 — 사업보고서 한
건이 8.7~29.0MB 라 「200건 = 100MB」 가정이 35~58배 어긋났다.

| 캐시 | 저장소 | 예산 | 키 |
|---|---|---|---|
| corpCode.xml | 모듈 글로벌 (`_corp_code_cache`) + sqlite(`/data/master.db`) | unlimited | 하루 1회 + 신호 때 통째로 교체 (261018) |
| document.xml + viewer 본문 | 메모리 LRU (`_DOC_CACHE`, 전역) | **96MB** + TTL 24h | `doc:` / `viewer:` 네임스페이스 |
| 과거 배당(alotMatter) | 메모리 LRU (`_DIVIDEND_CACHE`, 전역) | **16MB** + TTL 24h | corp_code + 연도 |
| list.json (검색) | 인스턴스 dict | 50건 (실측 ~0.5MB) | corp_code+bgn+end+pblntf_ty (단일 corp + page1 + count100만) |
| company.json (회사 개황, 261021) | 프로세스 전역 OrderedDict (`_COMPANY_INFO_CACHE`) | 512사 (≈0.5MB, 위 합계에 안 넣음) + TTL 1h | corp_code |
| DART 요청 단위 메모 (261021) | 요청 장부 `dart_memo` (응답이 끝나면 사라짐) | 요청당 512항목·8MB | (엔드포인트, 정렬한 인자) — `as_of` 호출은 제외 (→ 1.6) |
| 스크리너 스캔 | 메모리 LRU (`_SCAN_CACHE`, 전역) | **24MB** + TTL 3분/1시간 | 공시유형+기간 (→ [[screener]]) |
| proxy_advise | 메모리 LRU (`_PROXY_ADVISE_CACHE`, 전역) | **128MB** + TTL 1h | 회사+연도 |
| KRX 전종목 스냅샷 | 메모리 LRU (`_KRX_CACHE`, 전역) | **32MB** + TTL 48h | 기준일자 |
| 재무 분석 결과 | 메모리 LRU | **16 MiB** + TTL 300초 | 요청 조건 |
| 문서 디스크 캐시 | **볼륨** `/data/opm_cache/{rcept_no}.json.gz` | **640 MiB**, LRU 청소 (32 MiB 쓸 때마다) | 단일 파일 per rcept_no |
| 로컬 회귀 원문 | 기본 임시 경로 `opm_cache` | **2 GiB / 4,096파일** (`OPM_DOC_CORPUS_MB=2048`), 자동 삭제 없이 새 사본 저장 제한 | 기존 원문 보존 |

메모리 캐시는 모두 evict가 **고수위 95% → 저수위 75%**다(아래 절). 표의 코드 기본값에
재무 결과 캐시를 포함한 합계는 **312 MiB**다. 운영 문서 캐시 144 MiB override 적용 시
합계는 **360 MiB**이며 머신 RAM은 2 GiB다. 실제 장부는 `/health`의
`cache._budget_mb`·`_used_mb`로 확인한다.

**캐시는 스스로 장부에 등록한다**(`_CACHE_REGISTRY`). 종전엔 `/health` 가 셋을 손으로
나열했고, 그 사이 생긴 `krx`·`proxy_advise`·`screener_scan` **184MB 가 관측 밖**이었다
(260824 실측). 「예산을 정해 놓고 채워지는 걸 못 보면 같은 일이 반복된다」가 그 함수가 있는
이유인데(260804 OOM) 정작 그 함수가 그러고 있었다 — 나열식은 한쪽만 고쳐진다.

### evict 는 고수위/저수위로 (260824)

**메모리와 디스크가 같은 수위를 쓴다** — `_CACHE_HIGH_RATIO` 95% / `_CACHE_LOW_RATIO` 75%
(`dart/client.py`, SSOT). 고수위에 닿으면 저수위까지 **한 번에** 쓸어낸다.

종전엔 상한에 닿으면 「딱 들어갈 만큼만」 밀어내서 캐시가 **100% 에 붙박였다.**
실측(live 260824): `document` 가 몇 시간에 3,722건을 밀어냈고, 항목 수는 696→528 로 줄었는데
용량은 82→95MB 로 늘었다 — 큰 문서가 작은 것들을 끊임없이 밀어내는 중이었다. 디스크도 같은
형태로 상한까지만 쓸어 곧바로 다시 찼고, 스윕마다 디렉터리 전체를 stat 했다.

★ **evict 총량은 안 줄어든다.** 워킹셋이 예산보다 크면 들어온 바이트만큼 나가야 하는 산수라
어떤 정책도 그걸 못 바꾼다. 달라지는 것은 **어디에 앉아 있느냐**다.

| 정책 | evict | sweeps | 평균 채움 | 최대 |
|---|---|---|---|---|
| 옛 (딱 맞을 만큼) | 3,854 | 869 | 96.1% | 100.0% |
| 새 (95→75%) | 3,854 | **75** | **83.3%** | **95.0%** |

<sub>시뮬레이션: 100MB 예산 · 실측 크기분포(60KB~20MB) · 4,000 삽입</sub>

스윕 **횟수** 11.6배 감소, 상시 여유 약 13MB. 1GB 머신에서 그게 260804 OOM 여유다.
디스크 쪽은 스윕마다 디렉터리 전체 stat 이라 횟수 감소가 곧 비용 감소다.

배포 직후 실측으로 디스크가 628MB(93.6%) → 473MB(70.5%) 로 첫 스윕에 1,020건이 빠졌다.

계산식은 `min(저수위, 상한 − 항목크기)` 다. 저수위보다 큰 항목이 오면 상한 조건이 더 엄해야
하고, 반대로 `저수위 − 항목크기` 로 잡으면 저수위 **아래로** 과하게 파내려가 evict 가 되레
늘어난다(설계 중 실측). `/health` 가 `sweeps`·`high_pct`·`low_pct` 를 낸다.

### 디스크 캐시가 볼륨에 있는 이유 (260810)

경로 기본값은 `tempfile.gettempdir()/opm_cache` 이고, 운영은 `OPM_DOC_CACHE_DIR=/data/opm_cache`
(fly.toml)로 덮는다. `/tmp` 는 **컨테이너 이미지 안**이라 배포마다 통째로 갈린다 — 메모리
캐시가 죽는 그 순간 받침도 같이 죽었다(실측: 배포 직후 적중률 0%, 24h 평균 36%, 디스크
적중은 24h 통틀어 13건, 두 머신 중 하나는 디렉터리조차 없었다).

**경로 이동과 예산·안전장치는 한 몸이다** — `/tmp` 가 공짜로 해주던 일이 볼륨엔 없다.

| 딸려온 것 | 없으면 |
|---|---|
| 예산 640MB + LRU 청소 | 볼륨에 `master.db`(원장 14MB)가 같이 산다. 캐시가 채우면 **원장 쓰기가 실패** |
| 원자적 쓰기 (tmp→`os.replace`) | 잘린 json 이 `/tmp` 에선 배포 때 사라졌지만 볼륨에선 그 rcept_no 를 **영구히** 못 읽게 만든다 |
| 손상 파일 삭제 후 miss 처리 | 같은 이유 |

### 조용한 대체를 센다 (260824)

**에러가 아니라 대체로 나타나는 고장**이 있다. 원래 답을 못 줘서 다른 것으로 바꿔 답하는
경우다 — 응답은 200 이고 오류율은 안 움직인다.

계기는 `screener` 였다. 유니버스 폴백이 「krx_weekly 조회 실패 → 전체시장으로 대체」를
**모든 kospi200 호출에서 100% 발화**하고 있었는데(260823 개명이 KS/KQ 로 바꾸면서 질의가
0건을 냈다), 그 문장이 사용자 응답에만 실려 나가고 우리가 보는 곳엔 아무 데도 안 쌓였다.

| 시각 | 일 |
|---|---|
| 08-23 16:26 | 컬럼 개명 커밋 (KOSPI → KS) |
| 08-23 09시 | screener 1건 — 정상 |
| 08-23 19시 | **12건** — 개명 2시간 반 뒤 |
| 08-23~24 밤 | **58건** |

그동안 오류율은 1% 대였다. 우리가 보는 지표는 한 번도 안 움직였고, 사용자는 밤새 다시 눌렀다.

`note_degradation(kind)` 이 요청 장부에 **종류만** 적고 미들웨어가 `ops_tool_calls.degraded`
로 올린다(`weak_kinds` 와 같은 구조 — 적는 곳은 대체가 확정되는 지점, 읽는 곳은 미들웨어 하나).
회사명·질의 원문은 싣지 않는다. `usage_tracker --stats` 의 `[조용한 대체]` 절이 종류별·
tool별·**날짜별**을 낸다 — 총량보다 「언제부터 늘었나」가 답이라서다.

종류는 **닫힌 목록**(`DEGRADATION_KINDS`)이고 테스트가 호출부 문자열을 그 목록과 대조한다.
오타로 유령 범주가 생기면 집계가 조용히 갈라지는데, 그게 바로 이 계측이 고치려는 병이다.

<sub>이 지표는 첫 실행에서 스스로 틀렸다 — 드레인 백업엔 그 컬럼이 없어 `None` 으로 채워지는데
DB 쪽 필터만 걸어서 `str(None)`="None" 이 65,500건짜리 가짜 범주가 됐다. 합류분을 다시 거른다.</sub>

### 느린 이유를 가른다 — 줄 / 자신이 무겁다 / 대기 (260824)

`latency_ms` 하나로는 **「스스로 느린 것」과 「줄에 서 있던 것」이 같은 숫자**로 보인다.
그래서 `business_details` 의 178초를 진단하는 데 반나절이 들었다 — 장부에 「178,000ms」밖에
없어서 호출들의 **종료시각이 같은 초에 몰린 것**을 보고 거꾸로 짜맞춰야 했다.

```
23:18:24 끝  178.7s  business_details  disk=1   ← 캐시히트, 원문 0건
23:18:24 끝  168.5s  business_details  disk=1   ← 캐시히트, 원문 0건
23:18:24 끝  159.6s  financial_metrics
...
23:26:29 끝    0.7s  business_details  disk=1   ← 같은 조건, 한가할 때
```

셋이 같은 초에 끝났고 8분 뒤 같은 호출은 0.7초였다. 178초는 그 호출이 한 일이 아니라 **줄**이다.
전수로도 같은 그림이었다 — 30초 초과 580건 중 **535건(92%)이 DART 원문을 한 번도 안 받았고**
웹 대기도 0, 진행 중 겹친 요청은 **느린 호출 중앙 8건 / 빠른 호출 0건**이었다.

그래서 두 숫자를 그 자리에서 남긴다.

| 컬럼 | 뜻 |
|---|---|
| `inflight` | 이 요청이 도는 동안 **함께 돌던 요청의 최대 수** |
| `cpu_ms` | 그동안 **프로세스 전체**가 쓴 CPU(ms) — 이 요청 「자신의」 것이 아니다 |

`cpu_ms` 가 제 몫이 아닌 건 의도다. 단일 이벤트루프라 남의 코루틴이 태운 것도 들어오는데,
가르려는 건 **기다린 시간과 코어가 실제로 일한 시간**이고 누가 태웠는지는 `inflight` 가 답한다.

**셋이 아니라 넷이다(260827 정정).** 종전 규칙은 `cpu` 가 낮은 것을 전부 「네트워크 대기」로
몰았는데, 그 안에 **CPU 차례를 못 받은 것**이 섞여 있었다. `law_lookup` 은 `await` 도 HTTP 도
없는 **순수 로컬 조회**인데 15.1~16.4초가 걸렸고(할 일은 3.8초어치 = 코어의 1/4, 여섯 번 거의
동일), 그런데도 「대기(네트워크)」로 적혔다 — 기다릴 상대가 구조적으로 없는 tool 인데.
그래서 `lag_ms`(이 요청이 도는 동안 이벤트루프가 밀린 시간)를 하나 더 잰다.

| cpu ≳ lat/2 | lag ≳ lat/4 | inflight | 읽는 법 | 고칠 곳 |
|---|---|---|---|---|
| 예 | 예 | > 1 | **줄에 서 있었다** | 동시성 |
| 예 | — | = 1 | **이 호출 자신이 무겁다** | 그 tool 의 계산량 |
| 아니오 | **예** | — | **CPU 를 못 받았다** | **머신 등급** (코드 문제 아님) |
| 아니오 | 아니오 | — | 기다렸다 | 네트워크·상류 |

`loop_lag_ms()` 는 누적분에 **진행 중인 지각**을 더해서 낸다. 표본기는 다음에 깨어날 때야 제
지각을 적는데 요청은 막힌 **직후** 읽으므로, 안 더하면 **정작 막혔던 그 요청만** 0 으로 적히고
그 지연이 다음 요청에 뒤늦게 얹혀 엉뚱한 쪽을 범인으로 만든다(실측: 0.8초 붙들었는데 lag=0).

`inflight_max` 는 들어올 때 한 번 재는 게 아니라 **나중에 들어온 요청이 앞사람 기록도 함께
올린다.** 안 그러면 첫 요청은 영원히 1 로 남는데, 정작 뒤에 몰린 것을 다 겪는 건 그 요청이다.
등록 해제는 반드시 `finally` — 빠뜨리면 목록이 자라 **이후 모든 측정이 조용히 부푼다**.
`usage_tracker --stats` 의 `[느린 이유]` 절이 tool 별로 셋의 비율을 낸다.

`inflight` 는 **`tools/call` 만** 센다. streamable-http 클라이언트는 `GET /mcp` 로 스트림을
열어 세션 내내 붙들고 있어서, 함께 세면 「CPU 를 다투는 요청 수」가 아니라 「열려 있는 연결 수」가
된다. 핸드셰이크(`initialize`·`ping`)도 뺀다 — 비용이 0 에 가까워 줄을 만들지 않는다.

<sub>계측 전 기간(260824 이전)은 두 컬럼이 비어 있다 — 「0」이 아니라 **「안 쟀다」**라서
집계에서 분모째 빠진다. `degraded` 가 첫날 `None` 을 65,500건짜리 범주로 만든 그 자리다.</sub>

<sub>이 지표도 첫 배포에서 조용히 틀렸다 — 붙들린 스트림을 함께 세는 바람에 기록 19건이
**전부 6 이상**이었고 64ms 짜리 호출이 `inflight=12` 로 적혔다. **1 이 한 번도 안 나오는
지표는 「모두가 줄에 서 있다」고 말한다.** 지표를 켠 다음엔 분포를 먼저 본다 — 값이 있는지가
아니라 **말이 되는지**를.</sub>

### 기록 경로 — 서버는 DDL 을 보내지 않고, 묶어 쓴다 (261018)

기록 표(`ops_tool_calls`·`ops_corp_daily`·`ops_krx_calls`)의 정의는 `scripts/ops_ddl.py` 한 곳이다. 칸을 더하면 그 스크립트를
**배포 전에** 적용한다(기본은 SQL 출력만, `--apply` 가 적용). 서버는 INSERT 만 한다.

| | 종전 | 지금 |
|---|---|---|
| 사용 기록 워커가 접속할 때 | CREATE/ALTER 22문장 — `ALTER TABLE` 은 칸이 이미 있어도 표 전체 잠금을 잡는다 | DDL 없음 |
| 표 소유자가 아닌 역할(최소 권한 계정)에서 | `must be owner` 로 거절 → 2초마다 재접속, 0행 | 쓰기 권한만 있으면 쓴다 |
| 확정(COMMIT) | 호출 1건마다 | 첫 행부터 2초 모아 한 번(`OPM_USAGE_FLUSH_SEC`, 261018 5초→2초 — 아래) |
| 쓰기 실패 | 그 묶음을 버림 | 들고 다시 씀 — 이벤트는 `event_id` 로, 기업 집계는 `RETURNING` 으로 이번에 들어간 행만 더해 두 번 세지 않는다 |
| 종료 | (즉시 쓰므로 해당 없음) | 종료 신호에서 곧장 쓰고 atexit 에서 끝낸다 — fly 는 정해진 시간 뒤 강제 종료하므로 atexit 만으론 모자라다. 신호 처리기는 잠금을 잡지 않고 이벤트 루프에 한 칸 미뤄 알린다(261019 — 끊긴 메인 스레드가 쥔 큐·깃발 잠금을 처리기가 기다리면 교착) |
| KRX 호출 장부 `bump()` | 20번째 호출이 그 자리에서 새 접속 + CREATE TABLE — async 경로라 이벤트루프가 멈췄다 | 메모리 계수만. 쓰기는 배경 스레드가 공용 풀로(표가 없을 때만 만든다) |

**묶음 창 5초 → 2초**(261018). 디스크 송신함(볼륨 sqlite)은 워커가 **꺼낸 묶음**만 받친다 — 모으는 창 안의 행은 아직 메모리에만 있어 종료 신호 없는 강제 종료(OOM 등)에 잃는다. 운영 7일 호출 시각(36,711건)으로 모의하면 무작위 시점에 창 안에 든 행의 기대값이 5초 0.21건 → 2초 0.09건이고, 하루 확정 횟수는 1,835 → 2,872번(분당 2번꼴)이다. 확정 횟수보다 잃는 폭을 줄이는 쪽을 택했다. 요청 경로는 그대로 메모리 큐만 만진다.

**호출 인자 두 칸**(`scope` text · `as_of` date, 261018). 인자는 정책이 세 번 바뀌는 동안 한 번도 안 열었던 자리라
**닫힌 목록**으로만 연다 — 미들웨어는 본문에서 이 두 키만 꺼내고(`server._extract_call_dims`), 저장 경계(`usage._call_dims`)가
정규화한 값만 남긴다(못 읽으면 NULL). 회사명·질의 원문·문서번호는 여전히 싣지 않는다.

| 칸 | 남기는 것 | NULL 의 뜻 |
|---|---|---|
| `scope` | 영문 소문자로 시작하는 32자 이내 식별자(대소문자·앞뒤 공백은 바로잡음). 닫힌 목록이 아니라 꼴 검사 — scope 가 늘어도 고칠 곳이 없다 | 안 보냄(= 그 도구의 기본값) 또는 꼴이 안 맞음 |
| `as_of` | 달력에 있는 날짜(`YYYYMMDD`·`YYYY-MM-DD`·같은 꼴의 정수). 미래 날짜도 남긴다 | 안 보냄 또는 날짜가 아님(연도·월만 등) |

목적은 quote/firm 비중과 「일별 시세 7거래일 창이 충분한가」(호출일 − `as_of`)를 재는 것이다.

### 호출측이 아니라 스로틀에서 (260824)

**속도 제한은 클라이언트 스로틀 한 곳에서만 한다.** 호출측(예: `screener`)이 따로 sleep 을
두면 두 곳이 같은 일을 하게 되고, 실측상 그 대기가 응답의 **87%** 였다(kospi200·details
42.3초 중 36.7초 → 걷어내고 12.1초).

걷어내기 전에 **그 sleep 이 가리고 있던 결함**부터 고쳐야 했다:

| | 종전 | 지금 |
|---|---|---|
| `_throttle_api` | 롤링 60초 910/분 + 최소간격, `_api_rate_lock` 직렬화 | 그대로 (실측 동시 100건 → 903/분, 위반 0) |
| `_throttle_scrape` | **락 없음** — 동시 호출이 같은 시각을 읽고 함께 나감 | `_web_rate_lock` 으로 직렬화 |

실측(락 없을 때): 동시 4건 간격 **0.299초·0.151초** — 하한 1.0초 위반. API 한도는 키마다라
넘겨도 그 사람만 막히지만 **웹 차단은 IP 기준이라 우리 서버가 막히면 전원이 막힌다.**
락을 채운 뒤엔 동시 8건에서도 전부 1.0초 이상.

곁들여 두 락 모두 **루프별 lazy** 로 바꿨다. `asyncio.Lock` 은 만든 이벤트루프에 묶이는데
클라이언트는 키별 모듈 싱글턴이라, 루프가 바뀌면 「다른 루프에 묶임」으로 깨진다
(`_corp_code_lock` 이 이미 같은 이유로 lazy 였다).

호출측이 할 일은 **양**(캡·상한)이고, **속도**는 스로틀이 잡는다. 소비 측 상세는 [[screener]].

### 예산 640MB · 트리거는 바이트 · 순서는 LRU (260810 실측)

| 정한 것 | 근거 |
|---|---|
| 640MB | 볼륨 974MB − 원장 14MB. 문서 평균 0.58MB 라 약 1,100건, 남는 여유 320MB. **볼륨은 머신마다 따로**라 2대여도 2GB 한 벌이 아니다 — A 에 받아둔 문서는 B 에 없다. 디스크는 RAM 을 안 먹어 260804 OOM 과 무관 |
| 32MB 마다 청소 | 종전 「32건마다」는 크기를 못 봤다. 실측 문서가 20KB~42MB 로 2,000배 흩어져 있어 큰 것만 연달아 오면 청소 전에 32×42MB=1.3GB 가 쌓인다 — **개수 예산으로 터졌던 260804 와 같은 실수**. 바이트로 세면 초과분이 「32MB + 문서 한 건」으로 묶인다 |
| LRU (LFU 아님) | `_load_from_disk` 가 적중마다 mtime 을 올려 「마지막 사용 시각」으로 쓴다. 안 올리면 mtime 이 생성 시각으로 굳어 **FIFO** 가 되고, 매일 읽히는 문서도 나이 때문에 나간다. 빈도(LFU)로 안 가는 건 ① 디스크가 메모리 **뒤**에 있어 뜨거운 문서는 여기까지 안 오므로 빈도 신호가 약하고 ② 횟수 저장용 사이드카 인덱스가 필요하며 한때 인기였던 항목이 영영 안 나가기 때문. 볼륨 이전 전 디스크 적중은 24h 13건뿐이라 분포를 논할 표본이 없었다 — **재본 뒤에 얹는다** |

**자동 퇴출은 `OPM_DOC_CACHE_DIR`를 명시한 곳에서만 한다.** 로컬 기본 경로는 회귀 재생
원문이므로 삭제하지 않는다. 대신 보관 상한 2 GiB에 닿으면 새 디스크 사본만 생략한다.
요청 결과와 RAM 캐시는 그대로 사용한다. 같은 접수번호의 gzip 교체만 기존 크기를 차감하며,
POSIX에서 프로세스 간 잠금으로 검사·게시를 직렬화한다. 원문을 정리할 때는 테스트가
사용하는 접수번호를 먼저 고정해야 한다. 상한 도달만으로 원문을 자동 삭제하지 않는다.

게시된 사본과 중단된 쓰기의 임시파일을 함께 세며 상한 도달 시 다음 쓰기를 시작하지 않는다.
잠금 아래 자체 임시파일 형식만 24시간 후 정리한다. 새 문서 압축 중에는 임시 사본 하나가
추가될 수 있고, 그때 프로세스가 종료되면 다음 저장은 보류된다. 매 순간의 파일시스템 절대
상한이 아니라 무한 게시·중단 잔재 누적을 막는 기준이다. 미확인 이름의 임시파일은 지우지 않는다.
관리 볼륨에도 중단 임시파일은 32 MiB/64파일에서 새 저장을 보류한다. `admission_reason`이
`temporary_limit`이면 임시파일 누적, `corpus_limit`이면 로컬 전체 보관 상한이다.

`/health`의 `cache.document_disk`는 평문·gzip을 모두 집계하고 `persistent`(배포를 견디나),
`swept`(자동 퇴출하나), `admission_blocked`(로컬 새 사본 저장 생략 횟수), 실제 `max_bytes`,
`max_files`, `temporary_bytes`, `retained_bytes`를
낸다. 볼륨은 **머신마다 따로**이므로 두 머신이
캐시를 공유하지는 않는다 — 볼륨 이전으로 얻는 것은 「배포를 견딘다」 하나다.

서버 측 회사·기간 단위 캐싱은 없음 (실시간 조회 원칙). open-proxy-ai 파이프라인이 별도 KOSPI 200 v4 JSON 199개를 사전 생성해 보관.

---

# 12. Entry Point Quick Reference Table

| Tool | 1차 source | 2차 (보강·KIND 화이트리스트) | 3차 (fallback) |
|---|---|---|---|
| company | corpCode.xml + list.json (180일) | — | — |
| shareholder_meeting_notice / _results | list.json (E,I) + document.xml | KIND disclsviewer (주총결과 80→00) | viewer.do HTML / OCR (v1) |
| ownership_structure | DS002 4종 + DS004 majorstock + document.xml(PUR_OWN) | KIND HTML (변동신고서) | viewer.do HTML |
| dividend_disclosure | DS002 alotMatter + list.json (I) + document.xml | KIND HTML (현금ㆍ현물배당결정) | 배당결정 합산 fallback |
| treasury_share | DS005 4종 + list.json (소각) + DS002 tesstkAcqs | document.xml (소각 본문) | — |
| proxy_contest | list.json (D,I,B) + document.xml + DS002+DS004 (vote_math) | KIND HTML (소송) | viewer.do HTML |
| value_up | list.json (I, 밸류업) + document.xml | KIND search/fetch (코드 0184) | — |
| corporate_restructuring | DS005 4종 (병렬) | — | — |
| dilutive_issuance | DS005 4종 (병렬) | — | — |
| corporate_deals · order_contracts | list.json (B,I, 8종 키워드) + document.xml | — | — |
| corp_gov_report | list.json (I, "기업지배구조보고서공시") + document.xml | viewer.do HTML | OCR (v1) |
| (참고) news_check (v1) | Naver 뉴스 OpenAPI | — | — |
| (참고) get_stock_price | KRX `stk_bydd_trd` | Naver Finance siseJson | — |
| (참고) get_portal_corp_profile | Naver coinfo + sise_group_detail | — | — |
| (참고) 지수 구성종목 원장 `krx_index_members` | Naver 증권 enrollStocks(코드만) | KRX 지수 일별시세 합계로 확인 | 원장을 못 읽으면 유니버스가 시총 상위 정원으로 대체(경고) |
| (참고) 무위험 금리 `ecos_rate` | ECOS `817Y002`(시장금리 일별) · `722Y001`(기준금리) | 항목 코드·통계표 코드 일치 + 값 상식 범위 + 신선도 | 못 받으면 배치 실패(표는 그대로 — 낡은 값을 읽는 쪽이 기준 날짜를 밝히는 것은 읽는 도구가 생길 때 정할 예정) |
| (참고) proxy_guideline | data/asset_managers/ JSON (정적) | — | — |

---

# 13. 환경 변수 전수

| 변수 | 용도 | 필수 |
|---|---|---|
| `OPENDART_API_KEY` | DART OpenAPI 1차 키 | 필수 (또는 ?opendart=...) |
| `OPENDART_API_KEY_2` | DART API 보조 키 (자동 회전) | 권장 |
| `KRX_API_KEY` 또는 `KRX_OPEN_API_KEY` | KRX Open API 종가 | 선택 (미설정 시 Naver fallback) |
| `ECOS_API_KEY` | 한국은행 ECOS — 환율 환산(`dart/fx.py`)·무위험 금리 배치(`scripts/ecos_rate_daily.py`) | 선택 (없으면 환율은 야후 대체값, 금리 배치는 못 돈다) |
| `ECOS_RATE_WRITE` | 금리 배치의 DB 쓰기 스위치(`1` 일 때만 씀) — 저장소 변수, 서버는 읽지 않는다 | 배치 전용 |
| `ECOS_RATE_COLD_MANIFEST` | 금리 보관본 서빙 목록 주소(서명 URL) — 있으면 일별 실행이 창 밖 행을 지운다(가드 통과 시), 없으면 건너뛰고 안내 줄 — **저장소 비밀값**(변수 아님), 서버는 읽지 않는다 | 배치 전용 |
| `NAVER_API_HUB_CLIENT_ID` / `NAVER_API_HUB_CLIENT_SECRET` | Naver 뉴스 검색 HUB 키 (우선) | 선택 (director_news 사용 시) |
| `NAVER_SEARCH_API_CLIENT_ID` / `NAVER_SEARCH_API_CLIENT_SECRET` | Naver 개발자센터 키 — HUB 키가 없을 때만, 2027-06-30 까지 | 선택 |
| `FASTMCP_HOST`, `FASTMCP_PORT` | streamable-http 호스트/포트 | 선택 |
| `FASTMCP_ALLOWED_HOSTS` | DNS rebinding 허용 호스트 | 선택 |

---

# 14. Source Type → 최종 viewer URL 규칙

`services/contracts.py`의 `_build_viewer_url()`:
- DART_API / DART_XML / DART_HTML / KIND_HTML 모두 → `https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}`
- KIND 직접 URL(`disclsviewer.do?acptno=...`)은 사용자가 직접 클릭 시 404 위험 → 항상 DART viewer로 통일
- DART viewer는 80(거래소 수시) 포맷 rcept_no도 정상 동작

---

# 관련 페이지

[[DART-OpenAPI]] [[KRX-KIND]] [[네이버-금융]] (Upstage OCR·opendataloader 는 open-proxy-ai 이관 — 위 6·7절)
[[XML-vs-PDF]] [[pblntf-ty-필터링]] [[DART-KIND-매핑-화이트리스트-2026-04]]
[[배당공시유형]] [[주주총회소집공고]] [[주주총회결과]]
v4-스키마·release_v2 tool 아키텍처는 private storage(`wiki-private/concepts/`·`wiki-private/archive/`)
