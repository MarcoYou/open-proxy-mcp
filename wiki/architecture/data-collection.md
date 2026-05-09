---
type: source
title: OPM 데이터 수집 Architecture (전수 Entry Point + 파싱 방법)
generated: 2026-04-29
tags: [architecture, data-source, entry-point, dart, kind, naver, upstage, opendataloader, fallback]
related: [DART-OpenAPI, KRX-KIND, 네이버-금융, Upstage-OCR, opendataloader, 3-tier-fallback, dart-kind-disclosure-taxonomy, pblntf-ty-필터링, DART-KIND-매핑-화이트리스트-2026-04, free-paid-분리]
---

# OPM 데이터 수집 Architecture

## 개요

OPM(open-proxy-mcp) v2.0.0이 사용하는 모든 데이터 source의 entry point, endpoint URL, 파싱 방법, rate limit, fallback chain을 단일 문서로 정리한다.

OPM v2 운영 원칙(2026-04-18 결정, [[DART-KIND-매핑-화이트리스트-2026-04]] 참조):

- 1순위: `DART OpenAPI` (구조화 JSON/XML)
- 2순위: `DART document.xml` (원문 ZIP→XML→텍스트)
- 3순위: `KIND HTML` (화이트리스트 4종만)
- 보조: `Naver Finance` (시세·뉴스), `KRX Open API` (종가)
- v2에서는 PDF 다운로드를 기본 경로에서 제외(서버 부하·서비스 약관). 단 `tools/pdf_parser.py`는 v1·파이프라인 측에서 여전히 활용된다.
- OCR(`Upstage Document Parse`)은 vector glyph PDF·이미지 공고 등 최후의 수단.

## 데이터 source 전수

### A. 구조화 API (정형)

1. DART OpenAPI — `https://opendart.fss.or.kr/api/...`
2. KRX Open API — `https://data-dbg.krx.co.kr/svc/apis/...` (주가 fallback)
3. Naver 검색 OpenAPI — `https://openapi.naver.com/v1/search/...`
4. Naver Finance JSON — `https://api.finance.naver.com/...`

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
- 사용 services: `shareholder_meeting`, `dividend`, `ownership_structure`, `proxy_contest`, `value_up`, `treasury_share`, `corporate_restructuring`, `dilutive_issuance`, `related_party_transaction`, `corp_gov_report`, `screen_events`, `company`

### `pblntf_ty` 코드표 ([[pblntf-ty-필터링]] 참조)

| 코드 | 분류 | 대표 공시 | OPM tool |
|---|---|---|---|
| `A` | 정기공시 | 사업보고서, 반기보고서, 분기보고서 | (DS003 alotMatter 등 직접 endpoint 사용) |
| `B` | 주요사항보고 | 자기주식취득결정, 합병결정, 유상증자결정, CB·BW, 감자, 소송 | treasury_share, corporate_restructuring, dilutive_issuance, proxy_contest |
| `C` | 발행공시 | 증권신고서 | (현재 미사용) |
| `D` | 지분공시 | 5% 대량보유, 임원소유보고, 위임장권유참고서류, 공개매수 | ownership_structure, proxy_contest |
| `E` | 기타공시 | 주주총회소집공고 | shareholder_meeting |
| `I` | 거래소공시 | 주주총회결과, 현금ㆍ현물배당결정, 기업가치제고계획, 최대주주변경 | dividend, value_up, shareholder_meeting(results), ownership_structure(changes) |
| `J` | 공정위 공시 | 대규모기업집단 공시 | (현재 미사용) |

### 키워드 필터 패턴

`list.json`은 제목 직접 검색이 약하다. `pblntf_ty`로 좁힌 뒤 `report_nm` 키워드로 후처리.
공통 헬퍼: `services/filing_search.py` `search_filings_by_report_name()`
- max_pages 기본 10, page_count 100 → 최대 1,000건/공시유형
- 1,000건 초과 시 `notices`에 명시 (truncated 경고)

## 1.2 corpCode.xml — 기업코드 매핑

- Endpoint: `https://opendart.fss.or.kr/api/corpCode.xml`
- 응답: ZIP → XML (전체 상장+비상장 corp_code)
- 호출 위치: `DartClient._load_corp_codes()` (모듈 글로벌 캐시 `_corp_code_cache` — 프로세스 동안 1회만 로드)
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
- 캐시: 메모리 LRU 30개 + 디스크 캐시 `tmp/opm_cache/{rcept_no}.json`
- 텍스트 변환: `_html_to_text()` (br/p/tr 등을 줄바꿈 처리, 이미지 파일명은 본문에서 제거)
- 이미지 감지: 파일명에 "소집/통지/주총/공고" 키워드 포함 시 `[IMAGE_NOTICE]` 경고 로그 발생
- 인코딩 fallback: utf-8 → euc-kr → cp949

## 1.4 viewer.do — DART HTML viewer (2차 경로)

- Endpoint: `https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}`, `https://dart.fss.or.kr/report/viewer.do`
- 호출 위치: `DartClient.get_viewer_document(rcept_no, section_keywords=...)`
- 동작: main.do HTML에서 `treeData.push(node1)` 블록 정규식 추출 → 섹션별 `report/viewer.do` 호출 → HTML 결합
- 사용 시점: `document.xml`이 빈 본문/구조 깨졌을 때 fallback
- Rate limit: `_throttle_web()` (최소 2초)
- 캐시: `_viewer_doc_cache` 30개 LRU (rcept_no + keywords 키)

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
| `alotMatter.json` | get_dividend_info | dividend (사업보고서 배당 상세) |

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

- 환경변수: `OPENDART_API_KEY`(필수), `OPENDART_API_KEY_2`(선택)
- HTTP 요청 단위 키 주입: `?opendart=KEY` 쿼리 → contextvar(`_ctx_opendart_key`) → 인스턴스 캐시(키별 1개)
- 자동 키 회전: status `020`(rate limit 등) 발생 시 `_rotate_key()` 호출, 보조 키로 1회 재시도
- 사용량 추적: `_request_counter` (각 service가 `api_call_snapshot()` 차이로 호출 수 보고)

## 1.7 DART API Rate Limit

| 항목 | 값 | 출처 |
|---|---|---|
| 일일 한도 | 20,000건 | OpenDART 정책 |
| 분당 한도 | 1,000건 | OpenDART 정책 (초과 시 24시간 IP 차단) |
| 클라이언트 최소 간격 | 0.1초 | `_MIN_INTERVAL_API` (분당 600회 이하 보장) |
| 키 회전 | rotate on status≠"000" 시 1회 | `_rotate_key()` |
| build_usage 노출 | `dart_api_calls`, `mcp_tool_calls`, `dart_daily_limit_per_minute` | services/contracts.py |

---

# 2. DART 웹 (HTML/PDF, 보조 경로)

## 2.1 dsaf001/main.do — 공시 viewer 메인

- URL: `https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}`
- 용도: `dcm_no` 추출 (PDF 다운로드 선결조건), viewer treeData 파싱
- 호출 위치: `_fetch_dcm_no()`, `_fetch_viewer_main_html()`
- 정규식: `\['dcmNo'\]\s*=\s*"(\d+)"` (makeToc JS에서 추출)
- User-Agent: `OpenProxyMCP/1.0 (research; +https://github.com/MarcoYou/open-proxy-mcp)`
- Rate limit: 최소 2초 (`_throttle_web` `_MIN_INTERVAL_WEB`)

## 2.2 report/viewer.do — 섹션 HTML

- URL: `https://dart.fss.or.kr/report/viewer.do`
- 파라미터: `rcpNo`, `dcmNo`, `eleId`, `offset`, `length`, `dtd`
- 용도: `get_viewer_document()`가 main.do의 노드별로 호출 (목차 단위)
- 사용 service: `shareholder_meeting`, `corp_gov_report` 등 document.xml이 깨질 때 2차 경로

## 2.3 pdf/download/pdf.do — PDF 다운로드 (v1·파이프라인 only)

- URL: `https://dart.fss.or.kr/pdf/download/pdf.do?rcp_no=...&dcm_no=...`
- 호출 위치: `DartClient.get_document_pdf()`
- 용도: opendataloader 입력 PDF 확보 (XML 파싱 실패 case)
- 검증: `%PDF` 매직 넘버 확인
- v2 운영방침: 기본 경로에서 제외. tools/pdf_parser.py가 v1 toolset과 open-proxy-ai 파이프라인에서만 사용

## 2.4 DART 웹 Rate Limit

| 항목 | 값 |
|---|---|
| 최소 간격 | 2.0초 (`_MIN_INTERVAL_WEB`) |
| 배치 사용 | 금지 (1건씩만) |
| User-Agent | 프로젝트명·연락처 명시 필수 |
| 비공식 | 공식 API가 아니므로 보수적 접근 |

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

- BeautifulSoup `lxml` 파서로 후처리 (services/value_up_v2._kind_html_to_text 등)
- Rate limit: `_throttle_kind()` 1.0~3.0초 random

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
| shareholder_meeting | `_fetch_kind_results` (services/shareholder_meeting.py:696) | 주총결과 80→00 변환 후 본문 |
| ownership_structure | services/ownership_structure.py:375 | 변동신고서 본문 보강 |
| value_up | services/value_up_v2.py:150,386,410 | 밸류업 plan 본문 + KIND 직접 검색 |

shareholder.py(v1)도 acptno → rcept_no 양방향 fallback 사용(line 1252-1256).

## 3.6 KIND Rate Limit

| 항목 | 값 |
|---|---|
| 최소 간격 | 1.0~3.0초 random (`_throttle_kind`) |
| 배치 시 | 추가로 15~30초 random 권장 (CLAUDE.md) |
| 공식 API | 아님 (HTML 크롤링) |
| User-Agent | OpenProxyMCP/1.0 명시 |

---

# 4. Naver 검색·금융 API

## 4.1 Naver 뉴스 검색 OpenAPI

- Endpoint: `https://openapi.naver.com/v1/search/news.json`
- 호출 위치: `DartClient.naver_news_search(query, display=100, sort)`
- 헤더: `X-Naver-Client-Id`, `X-Naver-Client-Secret`
- 환경변수: `NAVER_SEARCH_API_CLIENT_ID`, `NAVER_SEARCH_API_CLIENT_SECRET`
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
- 호출 위치: `DartClient._naver_stock_price(stock_code, base_date)`
- 파라미터: `symbol`, `requestType=1`, `startTime`, `endTime`, `timeframe=day`
- 응답 파싱: 정규식 `\["(\d{8})",(\d+),(\d+),(\d+),(\d+)` → 종가 추출
- 비거래일 fallback: 7일 전부터 재조회 → 마지막 행 사용
- 사용: get_stock_price()의 KRX fallback (KRX_API_KEY 미설정 또는 응답 없음 시)

## 4.4 Naver Finance — 업종 (coinfo + sise_group)

- Endpoint: `https://finance.naver.com/item/coinfo.naver?code={stock_code}` → `sise_group_detail.naver?type=upjong&no={sector_code}`
- 호출 위치: `DartClient.get_naver_corp_profile(stock_code)`
- 응답 파싱: 페이지 1에서 `sise_group_detail.naver?type=upjong&no=(\d+)` 정규식 → sector_code, 페이지 2에서 `<title>` 태그로 sector_name
- Rate limit: 각 단계 사이 `asyncio.sleep(2.0)`
- 사용: company / value_up 업종 메타

## 4.5 Naver Finance — 기타 (참고)

- 시가총액·로고: AlphaSquare CDN 경유 — open-proxy-ai 프론트엔드 별도 수집
- 192개 기업 로고 정적 (네이버 금융 직접 호출 아님)

---

# 5. KRX Open API (종가 1차)

- Endpoint: `https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd`
- 호출 위치: `DartClient._krx_stock_price(stock_code, base_date)`
- 파라미터: `AUTH_KEY`, `basDd`(YYYYMMDD)
- 응답: JSON `OutBlock_1[]` → ISU_CD 매칭 → TDD_CLSPRC
- 환경변수: `KRX_API_KEY` 또는 `KRX_OPEN_API_KEY`
- 승인 상태: 전 서비스 승인 완료 (2026-04-08, 자세한 내역은 [[KRX-KIND]])
- get_stock_price()는 KRX → Naver 순서로 fallback

---

# 6. Upstage Document Parse API (OCR)

- Endpoint: `https://api.upstage.ai/v1/document-ai/document-parse`
- 호출 위치: `open_proxy_mcp/tools/pdf_parser.py` `upstage_ocr_parse()`
- 인증: Bearer 토큰 (`UPSTAGE_API_KEY` 환경변수)
- 입력: PDF 멀티파트 (`document` 필드), `output_formats=["markdown"]`
- 응답: JSON `content.markdown`
- 파일 크기 제한: 약 50MB → 페이지 추출 후 호출 권장 (`extract_pdf_pages` 헬퍼)
- 처리 시간: 10초+ (3-tier에서 가장 느림)
- 사용 흐름: opendataloader 마크다운 → 키워드로 페이지 특정(`_PARSER_KEYWORDS`) → 앞뒤 1페이지 포함 최대 10페이지 추출 → Upstage OCR → 동일 파서 재투입(`ocr_fallback_for_parser`)
- 적용 대상: vector glyph PDF(M레거시 정책 등), 이미지 공고
- v2: 기본 미사용. v1 / open-proxy-ai 파이프라인에서 활용

---

# 7. opendataloader-pdf (PDF → 마크다운)

- 라이브러리: `opendataloader-pdf` (Java 11+ 의존)
- 호출 위치: `open_proxy_mcp/tools/pdf_parser.py`
- 사용 흐름: DART 웹에서 PDF 다운로드 → opendataloader-pdf로 마크다운 변환(table_method="cluster", keep_line_breaks=True) → AGM 파서 재실행
- 한국어 OCR 벤치마크 1위, KOSPI 200 198개 PDF 변환 완료
- 한계: 일부 PDF에서 변환 품질 불안정 → Upstage OCR로 최종 fallback ([[opendataloader]] 참조)
- v2: 기본 미사용 (PDF 경로 제외)

---

# 8. 자산운용사 의결권 행사 데이터 (정적 JSON, 호출 0회)

- 위치: `open_proxy_mcp/data/asset_managers/`
- 로딩 service: `services/proxy_guideline.py` (`load_index`, `load_policy`, `load_records`, `load_consensus_matrix`, `load_decision_matrices`)
- 외부 호출: 0회. proxy_guideline tool 단독 동작 (cross-domain 시만 DART 호출)

| 디렉토리/파일 | 내용 | 건수 |
|---|---|---|
| `_index.json` | 운용사 메타 + OPM 디폴트 정책 매핑 | 1 |
| `_consensus_matrix.json` | 운용사 합의/이견 매트릭스 | 1 |
| `_decision_matrices.json` | 12 카테고리 의사결정 매트릭스 (100 dim, 76 빙고 패턴) | 1 |
| `policies/` | 운용사 정책 + open_proxy_v1.json | 9 |
| `records/` | 운용사 행사내역 (period별) | 16 |

운용사 8종 + OPM 1종(open_proxy):
- a_activist (A행동주의), b_foreign (B외국계), c_activist (C행동주의), k_legacy (한국투자신탁), m_legacy (M레거시), s_legacy (삼성), sa_active (삼성액티브), t_activist (T행동주의), open_proxy_v1 (OPM 자체 정책 v1.2)

원본 정적 데이터(엑셀·PDF):
- `wiki/raw/records/2024.04~2026.04 *_의결권 행사내역.xlsx` (17건)
- `wiki/raw/policies/2025.04~2026.04 *_의결권행사 내부지침.pdf` (9건, N연기금 포함)
- 위 원본은 사전 수집·구조화되어 JSON으로 사내 보관

---

# 9. 11 Data Tool 별 Source Flow

각 v2 data tool의 entry point + scope별 호출 흐름. 모든 service는 `open_proxy_mcp/services/`에 위치.

## 9.1 company

- Endpoint: `corpCode.xml` (캐싱), `list.json` (최근 180일 lookback)
- Source: DART API only
- Scope: 1 (회사 식별 + recent filings)

## 9.2 shareholder_meeting

- Primary: `list.json` (pblntf_ty=E, "소집") + `document.xml` 본문
- Results scope: `list.json` (pblntf_ty=I, "주주총회결과") + KIND `disclsviewer.do` (80→00 변환)
- Fallback: `viewer.do` HTML (XML 깨질 때)
- Scope: summary, agenda, board, compensation, aoi_change, results, full
- 캐시: get_document_cached LRU 30 + 디스크
- KIND 호출: 주총결과 본문 보강 (KOSPI 200 100% 매핑)

## 9.3 ownership_structure

- Primary: DS002 4종 (`hyslrSttus`, `hyslrChgSttus`, `mrhlSttus`, `stockTotqySttus`) + DS004 (`majorstock`)
- 보조: `document.xml` PUR_OWN 태그 (보유목적 — majorstock에 필드 없음)
- changes scope: `list.json` (pblntf_ty=I, "최대주주등소유주식변동신고서") + KIND HTML
- Scope: summary, major_holders, blocks, treasury, control_map, timeline, changes

## 9.4 dividend

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

## 9.10 related_party_transaction

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

## 9.12 (참고) screen_events — Discovery tool

- 22 event_type → (pblntf_tys, keywords, strip_spaces) 매핑 (services/screen_events.py `_EVENT_TYPES`)
- 시장 와이드 검색: 각 (corp_cls, pblntf_ty) 조합별 페이지 순회 + report_nm 키워드 후처리
- 페이지 한도: max_pages_per_ty=20 (page_count=100), max_results 도달 시 truncated 경고
- corp_cls: Y(KOSPI) / K(KOSDAQ) / Y+K(all) — KONEX/기타 제외

## 9.13 (참고) evidence

- DART viewer URL 생성 (`_build_viewer_url`)
- KIND_HTML/DART_XML/DART_HTML/DART_API source 모두 DART viewer URL로 통일 (KIND 직접 URL은 404 위험)

## 9.14 (참고) proxy_guideline

- DART 호출 0회. 100% 정적 JSON.
- 6 scope: policy, record, predict, compare, consensus, audit

---

# 10. 3-tier Fallback 체계 ([[3-tier-fallback]] 참조)

OPM v1 8개 AGM 파서의 fallback 패턴(v2에서는 PDF tier 기본 제외):

| Tier | Source | 속도 | 정확도 | 비용 |
|---|---|---|---|---|
| `_xml` | DART API + document.xml | 빠름 | 98%+ | 무료 |
| `_pdf` | get_document_pdf + opendataloader | 4초+ | 98%+ | 무료 |
| `_ocr` | Upstage Document Parse | 10초+ | 100% | 유료 |

흐름:
1. `agm_*_xml` 호출 → CASE_RULE 기준 검증
2. SUCCESS → 즉시 답변
3. SOFT_FAIL → AI 자체 보정 (구분자/누락 추론)
4. 보정 불가 → PDF fallback 제안 → 동의 시 `agm_*_pdf`
5. PDF 부족 → OCR fallback 제안 → `agm_*_ocr` (UPSTAGE_API_KEY 필요)

v2 운영(2026-04-19~):
- PDF 다운로드 기본 경로 제외
- DART_XML이 깨지면 viewer.do HTML(get_viewer_document) → KIND 화이트리스트 4종 → REQUIRES_REVIEW로 종결

---

# 11. Rate Limit + 캐싱 종합

## 11.1 Rate Limit per source

| Source | 최소 간격 | 한도 | 처리 |
|---|---|---|---|
| DART OpenAPI | 0.1초 (`_MIN_INTERVAL_API`) | 1,000/min, 20,000/day | 키 회전 |
| DART 웹 | 2.0초 (`_MIN_INTERVAL_WEB`) | 비공식 (IP 차단 위험) | User-Agent 명시 |
| KIND | 1~3초 random (`_throttle_kind`) | 비공식 | 봇 감지 회피 |
| Naver 뉴스 API | 0.1초 (공유) | 25,000/day, 분당 100 | 키 환경변수 |
| Naver Finance | 2.0초 (asyncio.sleep) | 비공식 | UA 위장 (Mozilla/5.0) |
| KRX Open API | 0.1초 (공유) | 미공개 | 서비스 승인 필요 |
| Upstage OCR | 클라이언트 미강제 | 유료 (per-page) | 50MB 파일 제한 |

## 11.2 캐시 정책

| 캐시 | 저장소 | 한도 | 키 |
|---|---|---|---|
| corpCode.xml | 모듈 글로벌 (`_corp_code_cache`) | unlimited | 프로세스 단위 |
| document.xml | 메모리 LRU + 디스크 | 30 / unlimited | rcept_no |
| viewer 본문 | 메모리 LRU | 30 | rcept_no + section keywords |
| list.json (검색) | 메모리 LRU | 50 | corp_code+bgn+end+pblntf_ty (단일 corp + page1 + count100만) |
| 디스크 캐시 경로 | `tempfile.gettempdir()/opm_cache/{rcept_no}.json` | 영구 | 단일 파일 per rcept_no |

서버 측 회사·기간 단위 캐싱은 없음 (실시간 조회 원칙). open-proxy-ai 파이프라인이 별도 KOSPI 200 v4 JSON 199개를 사전 생성해 보관.

---

# 12. Entry Point Quick Reference Table

| Tool | 1차 source | 2차 (보강·KIND 화이트리스트) | 3차 (fallback) |
|---|---|---|---|
| company | corpCode.xml + list.json (180일) | — | — |
| shareholder_meeting | list.json (E,I) + document.xml | KIND disclsviewer (주총결과 80→00) | viewer.do HTML / OCR (v1) |
| ownership_structure | DS002 4종 + DS004 majorstock + document.xml(PUR_OWN) | KIND HTML (변동신고서) | viewer.do HTML |
| dividend | DS002 alotMatter + list.json (I) + document.xml | KIND HTML (현금ㆍ현물배당결정) | 배당결정 합산 fallback |
| treasury_share | DS005 4종 + list.json (소각) + DS002 tesstkAcqs | document.xml (소각 본문) | — |
| proxy_contest | list.json (D,I,B) + document.xml + DS002+DS004 (vote_math) | KIND HTML (소송) | viewer.do HTML |
| value_up | list.json (I, 밸류업) + document.xml | KIND search/fetch (코드 0184) | — |
| corporate_restructuring | DS005 4종 (병렬) | — | — |
| dilutive_issuance | DS005 4종 (병렬) | — | — |
| related_party_transaction | list.json (B,I, 8종 키워드) + document.xml | — | — |
| corp_gov_report | list.json (I, "기업지배구조보고서공시") + document.xml | viewer.do HTML | OCR (v1) |
| (참고) news_check (v1) | Naver 뉴스 OpenAPI | — | — |
| (참고) get_stock_price | KRX `stk_bydd_trd` | Naver Finance siseJson | — |
| (참고) get_naver_corp_profile | Naver coinfo + sise_group_detail | — | — |
| (참고) proxy_guideline | data/asset_managers/ JSON (정적) | — | — |
| (참고) screen_events | list.json (시장 와이드, 22 event_type) | — | — |

---

# 13. 환경 변수 전수

| 변수 | 용도 | 필수 |
|---|---|---|
| `OPENDART_API_KEY` | DART OpenAPI 1차 키 | 필수 (또는 ?opendart=...) |
| `OPENDART_API_KEY_2` | DART API 보조 키 (자동 회전) | 권장 |
| `KRX_API_KEY` 또는 `KRX_OPEN_API_KEY` | KRX Open API 종가 | 선택 (미설정 시 Naver fallback) |
| `NAVER_SEARCH_API_CLIENT_ID` | Naver 뉴스 API client id | 선택 (news_check 사용 시) |
| `NAVER_SEARCH_API_CLIENT_SECRET` | Naver 뉴스 API client secret | 선택 |
| `UPSTAGE_API_KEY` | Upstage Document Parse | 선택 (OCR fallback 사용 시) |
| `OPEN_PROXY_TOOLSET` | v1/v2/hybrid toolset 분기 | 선택 (default v1) |
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

[[DART-OpenAPI]] [[KRX-KIND]] [[네이버-금융]] [[Upstage-OCR]] [[opendataloader]]
[[3-tier-fallback]] [[pblntf-ty-필터링]] [[DART-KIND-매핑-화이트리스트-2026-04]]
[[free-paid-분리]] [[배당공시유형]] [[주주총회소집공고]] [[주주총회결과]]
[[v4-스키마]] [[OpenProxy-MCP]] [[release_v2-tool-아키텍처]]
