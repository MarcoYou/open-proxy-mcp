---
type: source
title: OPM 데이터 수집 Architecture (전수 Entry Point + 파싱 방법)
generated: 2026-04-29
tags: [architecture, data-source, entry-point, dart, kind, naver, upstage, opendataloader, fallback]
related: [DART-OpenAPI, KRX-KIND, 네이버-금융, Upstage-OCR, opendataloader, 3-tier-fallback, dart-kind-disclosure-taxonomy, pblntf-ty-필터링, DART-KIND-매핑-화이트리스트-2026-04, free-paid-분리]
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
- 사용 services: `shareholder_meeting`, `dividend`, `ownership_structure`, `proxy_contest`, `value_up`, `treasury_share`, `corporate_restructuring`, `dilutive_issuance`, `related_party_transaction`, `corp_gov_report`, `company`

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
- 캐시: 메모리 LRU(바이트 예산 96MB, 전역) + 디스크 캐시 `/data/opm_cache/{rcept_no}.json`(볼륨, 640MB · LRU) → [[#11-2-캐시-정책]]
- 텍스트 변환: `_html_to_text()` (br/p/tr 등을 줄바꿈 처리, 이미지 파일명은 본문에서 제거)
- 이미지 감지: 파일명에 "소집/통지/주총/공고" 키워드 포함 시 `[IMAGE_NOTICE]` 경고 로그 발생
- 인코딩 fallback: utf-8 → euc-kr → cp949

## 1.4 viewer.do — DART HTML viewer (2차 경로)

- Endpoint: `https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}`, `https://dart.fss.or.kr/report/viewer.do`
- 호출 위치: `DartClient.get_viewer_document(rcept_no, section_keywords=...)`
- 동작: main.do HTML에서 `treeData.push(node1)` 블록 정규식 추출 → 섹션별 `report/viewer.do` 호출 → HTML 결합
- 사용 시점: `document.xml`이 빈 본문/구조 깨졌을 때 fallback
- Rate limit: `_throttle_web()` → `_throttle_scrape()` (1~2초 랜덤, KIND 와 시계 공유)
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
- Rate limit: 1~2초 랜덤 (`_throttle_scrape` `_WEB_INTERVAL_RANGE`)

## 2.2 report/viewer.do — 섹션 HTML

- URL: `https://dart.fss.or.kr/report/viewer.do`
- 파라미터: `rcpNo`, `dcmNo`, `eleId`, `offset`, `length`, `dtd`
- 용도: `get_viewer_document()`가 main.do의 노드별로 호출 (목차 단위)
- 사용 service: `shareholder_meeting`, `corp_gov_report` 등 document.xml이 깨질 때 2차 경로

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
| value_up | services/value_up.py | 밸류업 plan 본문 + KIND 직접 검색 |

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
- 한계: 일부 PDF에서 변환 품질 불안정 → Upstage OCR로 최종 fallback ([[opendataloader]] 참조)
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

## 9.2 shareholder_meeting

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

## 9.12 (참고) evidence

- DART viewer URL 생성 (`_build_viewer_url`)
- KIND_HTML/DART_XML/DART_HTML/DART_API source 모두 DART viewer URL로 통일 (KIND 직접 URL은 404 위험)

## 9.13 (참고) proxy_guideline

- DART 호출 0회. 100% 정적 JSON.
- 6 scope: policy, record, predict, compare, consensus, audit

---

# 10. 3-tier Fallback 체계 ([[3-tier-fallback]] 참조)

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
| DART 웹·KIND | 1~2초 랜덤 (`_WEB_INTERVAL_RANGE`, 시계 공유) | 비공식 (IP 차단 위험) | User-Agent 명시 |
| KIND | 1~3초 random (`_throttle_kind`) | 비공식 | 봇 감지 회피 |
| Naver 뉴스 API | 0.1초 (공유) | 25,000/day, 분당 100 | 키 환경변수 |
| Naver Finance | 2.0초 (asyncio.sleep) | 비공식 | UA 위장 (Mozilla/5.0) |
| KRX Open API | 0.1초 (공유) | 미공개 | 서비스 승인 필요 |
| Upstage OCR | 클라이언트 미강제 | 유료 (per-page) | 50MB 파일 제한 |

## 11.2 캐시 정책

한도는 **개수가 아니라 바이트**다. 260804 OOM 의 뿌리가 개수 예산이었다 — 사업보고서 한
건이 8.7~29.0MB 라 「200건 = 100MB」 가정이 35~58배 어긋났다.

| 캐시 | 저장소 | 예산 | 키 |
|---|---|---|---|
| corpCode.xml | 모듈 글로벌 (`_corp_code_cache`) + sqlite(`/data/master.db`) | unlimited | 프로세스 단위 |
| document.xml + viewer 본문 | 메모리 LRU (`_DOC_CACHE`, 전역) | **96MB** + TTL 24h | `doc:` / `viewer:` 네임스페이스 |
| 과거 배당(alotMatter) | 메모리 LRU (`_DIVIDEND_CACHE`, 전역) | **16MB** + TTL 24h | corp_code + 연도 |
| list.json (검색) | 인스턴스 dict | 50건 (실측 ~0.5MB) | corp_code+bgn+end+pblntf_ty (단일 corp + page1 + count100만) |
| 스크리너 스캔 | 메모리 LRU (`_SCAN_CACHE`, 전역) | **24MB** + TTL 3분/1시간 | 공시유형+기간 (→ [[screener]]) |
| proxy_advise | 메모리 LRU (`_PROXY_ADVISE_CACHE`, 전역) | **128MB** + TTL 1h | 회사+연도 |
| KRX 전종목 스냅샷 | 메모리 LRU (`_KRX_CACHE`, 전역) | **32MB** + TTL 48h | 기준일자 |
| 문서 디스크 캐시 | **볼륨** `/data/opm_cache/{rcept_no}.json` | **640MB**, LRU 청소 (32MB 쓸 때마다) | 단일 파일 per rcept_no |

메모리 캐시는 모두 evict 가 **고수위 95% → 저수위 75%** 다(아래 절). 선언 예산 합 **296MB**
(머신 1GB) — `/health` 의 `cache._budget_mb`·`_used_mb` 가 그 합을 낸다.

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

| cpu ≳ latency/2 | inflight | 읽는 법 |
|---|---|---|
| 예 | > 1 | **줄에 서 있었다** (피해자 — 고칠 곳은 그 tool 이 아니라 동시성) |
| 예 | = 1 | **이 호출 자신이 무겁다** (원인) |
| 아니오 | — | 기다렸다 (네트워크·스로틀) |

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

**청소는 `OPM_DOC_CACHE_DIR` 를 명시한 곳에서만 한다.** 예산은 볼륨을 지키려 있고, 볼륨이
아니면 지킬 것이 없다. 로컬 기본 경로는 그냥 캐시가 아니라 **회귀 재생의 유일한 소재**이므로
(CLAUDE.md) 예산을 집행하면 그 소재를 우리 손으로 지운다 — 실측 로컬 1.35GB/2,350건.

`/health` 의 `cache.document_disk` 가 `persistent`(배포를 견디나)·`swept`(예산이 집행되나)를
같이 낸다. 숫자만으로는 그 둘을 구분할 수 없어서다. 볼륨은 **머신마다 따로**이므로 두 머신이
캐시를 공유하지는 않는다 — 볼륨 이전으로 얻는 것은 「배포를 견딘다」 하나다.

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

---

# 13. 환경 변수 전수

| 변수 | 용도 | 필수 |
|---|---|---|
| `OPENDART_API_KEY` | DART OpenAPI 1차 키 | 필수 (또는 ?opendart=...) |
| `OPENDART_API_KEY_2` | DART API 보조 키 (자동 회전) | 권장 |
| `KRX_API_KEY` 또는 `KRX_OPEN_API_KEY` | KRX Open API 종가 | 선택 (미설정 시 Naver fallback) |
| `NAVER_SEARCH_API_CLIENT_ID` | Naver 뉴스 API client id | 선택 (news_check 사용 시) |
| `NAVER_SEARCH_API_CLIENT_SECRET` | Naver 뉴스 API client secret | 선택 |
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
v4-스키마 [[OpenProxy-MCP]] [[release_v2-tool-아키텍처]]
