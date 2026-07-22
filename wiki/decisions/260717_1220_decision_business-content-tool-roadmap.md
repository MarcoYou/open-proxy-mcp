---
type: decision
title: business_details tool — "II. 사업의 내용" 자동추출 설계·실현가능성 로드맵 (156사 census 근거)
date: 2026-07-17
status: 설계확정 · 구현중 (A+B 착수, D 보류). tool명 business_details 확정(260717)
---

# business_details tool 로드맵 — "II. 사업의 내용" 자동추출

> DART 정기보고서 "II. 사업의 내용"에서 **사업부문별 매출·이익·비중, 원재료, 생산실적, 가동률,
> 주요사업장, 연구개발비, 고객집중, 수주**를 구조화 추출하는 21번째 tool. 3라운드 census(55→156사,
> KOSPI+KOSDAQ) + 12에이전트 카탈로그 + 156사 기계검증으로 실현가능성을 확정한 설계 결정.
> 원본 데이터·스크립트·카탈로그·v3설계서·검증부록: **`wiki/_local/census-biz-content-260717/`** (gitignore, 67MB).

## 왜 (배경)

기존 20 tool은 거버넌스(agm·ownership·proxy·div)·전사재무(financial_metrics)·밸류(valuation) 축.
**부문 단위 사업 실질**(부문 수익성·SOTP·적자부문·일감몰아주기·고객집중·수주가시성)을 주는 tool은 부재.
"II. 사업의 내용"이 그 유일 공급원이나 기업·업종마다 양식이 달라 사람이 일일이 못 함 → 자동 추출 필요.

## 어떻게 검증했나 (census 3라운드)

1. **기술 spike**: DART viewer treeData(node1/2/3 전체 노드트리)로 "II.사업의 내용" chapter 1콜(~40KB) +
   영업부문 주석 1콜. per-firm 2콜, 156사 전수 성공(하드룰 준수 `hard-rate-limit`(lessons)).
2. **표본 55→156 확대**(KOSPI 83/KOSDAQ 73) — 대형 편향 깨기. 중앙 throttled fetch 1회 → 캐시 →
   12에이전트(섹터전문가7+회계3+QA+합성)가 캐시만 읽어 DART 0콜(opm-tool-validation 원칙).
3. **156사 기계검증 + 3표본 육안**(측정도구 3/3 정확) — 에이전트 서사를 실측으로 교정(`agenda-parser-validation-260621`(lessons) 원칙).

## 실현가능성 판정 (필드별 · 실측근거)

| 등급 | 필드 | 커버리지(실측) | 판정 |
|---|---|---|---|
| **A 바로 가능** | 사업부문별 매출·비중 | **156/156** | 유일 완전안정 필드 |
| | 연구개발비/매출비율 | 제조·바이오·IT 대부분 | DART 최표준 표 |
| | 수주잔고 | 조선·건설·방산·장비 | 있음/해당없음 이분 |
| | 고객집중도(10%↑ 외부고객) | 중소·단일부문 지배적 | 단일부문사 유일 매출분해 |
| **B P0 후 가능** | **사업부문별 이익** | **60%(94/156) 취득**, gap 47은 대부분 창밖 회수가능, 단일부문 15만 정상 N/A | P0 엔지니어링으로 실현 |
| | 주요 원재료 | 표준폼 제조 존재, heading 변이 | 앵커사전+서술형 병행 |
| | 주요 사업장 | 개요+설비현황+종속표 분산 | 조각 조립 |
| **C 추출OK·비교금지** | 생산실적·생산능력 | 제조만, 단위 카오스(대/톤/장/시간/금액) | 값+주석, 회사간 합산·비교 금지 |
| | 가동률 | 정의 4~5종, 100%+ 정상, 오타 '가동율' 24사 | 산출근거 필수 캡처, 비교 무의미 |
| **D 별도 스키마** | 금융폼(NIM·CET1·CSM·AUM) | census 금융 9사·REIT 2사(얇음) | 전용 스키마 + 표본 보강 후 |

### 스코프 정의 (2026-07-17 확정 — 명확한 경계, 조용한 overreach 금지)

**IN (v1 지원):**
- **폼**: `standard7`(표준 7소절) — 제조·일반지주·유통·게임·유틸·운송 등.
- **필드 A(바로)**: `segment_revenue`(부문매출·비중) · `rnd`(연구개발비/매출비율) · `backlog`(수주잔고) · `customer_concentration`(10%↑ 외부고객).
- **필드 B(P0)**: `segment_profit`(부문이익 3단 fallback).
- **필드 C**: `production`(생산실적·능력) · `utilization`(가동률) — **값 + 산출근거(산출 기준)만** 병기. 경고 없음(단위·정의가 회사·산업마다 다른 건 사용자가 이미 아는 전제 — 260717 사용자 결정). 랭킹·합산은 tool이 안 함(값만 제공).
- **필드(P1)**: `raw_materials` · `sites`.
- **기간**: `annual`(기본) + `quarterly`(있으면, 기간기준 라벨).

**OUT (v1 미지원 — garbage 대신 명시 신호):**
- **폼 `financial5`(은행·보험·증권 지주) · `reit`**: form_type 판별 후 각 필드 `status=UNSUPPORTED_FORM` + 해당 원문 소절 포인터만 반환. **부문 손익을 제조 스키마로 억지 파싱하지 않음**. → **금융·REIT는 v1 완료 후 별도 스콥/tool로 신설**(260717 사용자 결정 — 단순 보류 아니라 전용 트랙. census 표본 금융 9·REIT 2로 얇아 표본 보강 선행).
- **D 필드**(NIM·CET1·CSM·AUM·WALE 등 업권 KPI): 금융 별도 스콥에서.

**입력 scope 파라미터:**
- `fields`: 반환 필드 선택(기본 in-scope 전체). 부분조회로 fetch·파싱 비용 절감.
- `period`: `annual` | `quarterly`.
- `prefer_consolidated`: 연결/별도(note_source 3/5) 라우팅 힌트.

**스콥 규율(핵심):** 폼 게이트가 **먼저** 판별 → out-of-scope 폼은 필드별 `UNSUPPORTED_FORM`으로 정직하게 반환. 결측은 3분류(`NOT_APPLICABLE`/`NOT_COLLECTED`/`EXTRACTION_FAILED`) + `UNSUPPORTED_FORM`로 "왜 없는지"를 항상 구분. **D·C-비교는 tool이 할 수 있는 척하지 않는다.**

## 핵심 검증 결과 (v3 초안 대비 교정)

- **F2 부문이익은 초안보다 실현가능**: 초안 "안정필드는 F1뿐/실포착 ~18"은 과도 비관 → 실측 **60% 취득**.
  잔여 47사(30%) gap의 대부분은 "표가 15KB 창 밖"인 **window-truncation**(LG화학=주석33형)이지 구조부재 아님.
- **본문게시 42사(27%)**: 부문이익이 주석 아닌 **본문**(1.개요·7.기타 요약재무)에 → 주석 2번째 콜 불필요(예산절감).
- **이익라벨 다형성 확정**: 표포착 81사 중 **19%가 '영업이익' 라벨 없음**(당기순이익·계속영업이익·매출총이익) → 화이트리스트 필수.
- **별도(5번) 주석 라우팅**: 별도 19사 중 18사 KOSDAQ → 연결-only 파서면 전멸.
- **폼은 KSIC로 못 가름**: induty 64992에 금융지주·제조지주·호텔지주 공존 → **목차 소절 제목**으로 폼 판별([[ksic-sector-mapping]] 한계).

## 추출 아키텍처 (3층)

0. **폼 게이트**(목차 제목 기반, KSIC 불신): 표준7소절 / 금융5소절 / REIT / 이중템플릿 + 정정·stale 3자 신선도.
1. **공통 코어 필드**(F1~F9): 폼 무관 재사용. F1(매출) 안정, F9(고객집중) 1차 승격.
2. **부문이익 3단 fallback**: ①본문 소절1/7 → ②note-title 정밀 재앵커(동적창, 15.6KB 고정 폐기) → ③단일부문 N/A(제품/지역/고객 pivot). 연결/별도(note_source 3/5) 라우팅 + 이익라벨 화이트리스트.

파싱 원칙: XML 단독([[XML-vs-PDF]]) · 이름기반 열매핑(위치 금지) · 표별 단위 파싱 · 조정/총계 열 분리 · 결측 3분류(NOT_APPLICABLE/NOT_COLLECTED/EXTRACTION_FAILED).

## 구현 우선순위

- **P0(정확성 봉쇄)**: 동적창 재앵커 · 이익라벨 화이트리스트 · 본문-우선 fallback · note_source 연결/별도 라우팅 · 단위파싱·이름기반 열매핑 · 정정/stale 게이트.
- **P1(커버리지)**: induty 무력화(본문 재판정) · F9 승격 · 앵커 alias 사전('가동율' 등) · 서술형 병행 · field_status/warnings 전파.
- **P2(완결)**: C필드(생산·가동률 주석부) · D 폼(금융·REIT) 별도 스키마 · **47 gap 회수율 재실측**(before/after).

## 구현-검증 하네스 (다음 단계)

156사 캐시를 ground truth로, 코드 산출 vs 캐시 원문을 **6렌즈 에이전트**로 교차검증(캐시 전용 DART 0콜):
값·단위 검증 / 표·텍스트(정합) 검증 / financial analyst(비즈니스 정합: 부문합≈연결) / 섹터전문가(산업 sanity) / 코드·데이터 QA ×2. 2룹(발견→수정→회귀). 상세 절차는 이 문서 갱신으로 추적.

## 구현 아키텍처 확정 (260717 오후 — flatten 한계 실증 후 전환)

flatten 텍스트 파서 v1은 2D표를 1D로 뭉개 정렬 whack-a-mole 천장(부문표 101사 중 정형 신뢰 ~30만). **사용자 결정 2건으로 전환:**

1. **HTML-primary**: DART viewer HTML을 `pandas.read_html`(colspan/rowspan 처리)로 2D격자 복원 → 정렬문제 소멸. flatten은 fallback.
2. **멀티에이전트 = 정식 추출 경로**: 표 선택(수백 중첩표 중 진짜 부문표 판별)은 regex가 약하고 LLM이 강함. CLAUDE.md soft-fail 원칙([[LLM-fallback-설계]])에 부합. **156사 에이전트 추출로 ground-truth 확보**(101 found·152/156 high conf·0에러, `wiki/_local/.../artifacts/agent_extractions_156.json`).

**segment_profit fallback (260718 최종확정 — 내부 LLM 폐기):**
> **핵심 통찰(사용자)**: MCP tool은 이미 LLM(호출측 Claude)이 부른다 → tool이 *또* 내부 LLM을 부르면
> LLM이 LLM 대신 추출하는 중복(지연·비용·비결정성·API키·anthropic/pandas 의존). **호출측 Claude가 공짜로 할 일.**
```
① 정형(flatten 텍스트, pandas無) — sum(부문)≈부문합계·총계 게이트 통과 → 구조화 반환 [공짜, ~30사]  source=deterministic
② 저신뢰/실패 → tool이 수백 중첩표를 '부문표 후보 ~3-5개'로 기계적 narrow(bs4 점수순, colspan확장) → raw 반환
                + note("이게 부문표 후보, 읽어서 추출") → 호출측 Claude가 선택+추출 [공짜, 내부 LLM 없음]  source=raw_candidates
③ NOT_APPLICABLE — 부문표 부재(단일부문·금융폼). 후보 안 붙임(깔끔한 N/A+사유)
```
분담선 실측(156사): 정형충분 ~30 · 후보반환 ~71 · N/A ~50. 후보 narrow 검증: 8사 중 7사 진짜표 1위·1사 2위(둘 다 반환). **anthropic·pandas·API키·지연·비용·비결정성 전부 제거** — "AI in production" 리스크 통째 해소. 비-LLM 배치소비자 생기면 그때 구조화 재고.

**파일**: `services/business_details.py`(정형, 기존) + `services/segment_candidates.py`(신규, 후보 narrow, bs4-only) + `tools/business_details.py`(신규 래퍼) + `wiki/tools/business_details.md`. **검증**: 156 ground-truth(에이전트 추출 = 호출측 LLM이 후보에서 뽑을 값과 동일)로 대조.

## 관련
- `hard-rate-limit`(lessons) (DART 하드룰 — census fetch 준수)
- `agenda-parser-validation-260621`(lessons) (측정 함정·이중검증 프로토콜 — 156사 검증에 적용)
- [[ksic-sector-mapping]] (KSIC 한계 — 폼 판별에 KSIC 불신 근거)
- [[XML-vs-PDF]] (XML 단독 파싱 결정)
- `wiki/_local/census-biz-content-260717/` (원본 캐시·카탈로그·v3설계서·검증부록·재현 스크립트)

## 스코프 확장 — 시계열(추이) 조회 추가 (260721)

원 설계는 **"기간": `annual`(기본) + `quarterly`(있으면)** — 즉 "그 유형 중 가장 최신 제출분"만 스코프였고,
과거 특정 분기를 지정 조회하는 기능은 없었다. 실사용 세션(삼성전자 "지난 1년 부문별 매출 추이" 질문)에서
이 한계가 드러남 — AI가 여러 우회를 시도하다 결국 "이 tool로는 불가"로 막힘.

**추가**: `bsns_year`(사업연도) + `reprt_code`(DART 표준: 11011/11012/11013/11014) 파라미터로 특정 과거
시점 1건 조회 가능(둘 다 필수, `period`보다 우선). 여전히 **한 번의 호출로 여러 기간을 반환하지 않음** —
추이는 호출측이 분기마다 반복 호출해 조립(dart 콜 예산·기존 아키텍처 유지, tool 자체는 단순 유지).

구현: `report_nm`의 기수라벨 `(YYYY.MM)`로 정밀 매칭(절대월 하드코딩 없이 상대순서로 1분기/3분기 구분 —
결산월이 12월이 아닌 회사도 안전). 8개 엣지케이스(1분기/3분기/반기/연간/파라미터 누락 에러/존재하지 않는
연도) 실DART 검증 + 기존 `period` 경로 회귀 없음 확인 + 삼성전자 1Q26 rcept_no가 실사용 세션에서 인용된
값(`20260515002181`)과 정확히 일치함을 대조.

파일: `services/business_details.py`(`_find_report_for_bsns_year` 신규) + `tools/business_details.py`
(파라미터 배선) + `wiki/tools/business_details.md`.

## 정확도 하드닝 — 구조 헤딩 경계 (260722)

고정 18~24KB window는 해당 소절 뒤의 위험관리·재무자료를 함께 반환하고, `MARKDOWN`이 실제 값 존재가 아니라
단순 구간 발견을 뜻하게 만드는 문제가 있었다. 공개 signature와 markdown-primary 원칙은 유지하고 수집 경계만 교체했다.

1. 전체 보고서에서 `<TITLE>`·번호형 `<P>/<SPAN>`을 1회 색인하고 요청 필드가 공유한다.
2. 목차·표 내부 라벨·일반 교차참조를 제외하고 실제 헤딩에서 다음 동급/상위 헤딩 또는 section 끝까지 반환한다.
3. 굵은 span+본문 결합, 비강조 장문 문단, 공백이 소실된 `...마. 주요매출처`를 제한적으로 지원한다.
4. 번호 깊이 역전은 최초 경계가 제목만 남을 때만 복구한다. DART `<TITLE>`은 같은 `SECTION-2` 끝,
   일반 문단 헤딩은 다음 최상위 절까지만 허용한다. 내용이 있는데 content-gate가 실패한 구간은 확장하지 않는다.
5. 기존 `status`는 호환 유지하고 `extraction_status`, `section_source`, 비권위 `hints[]`를 병행 추가한다.
   힌트 값은 반환 markdown 밖에서 가져오지 않는다.

검증: 최초 `NOT_COLLECTED` 132건을 원문으로 전수 판정하고, 수정 과정에서 상태가 바뀐 sites 16건과 backlog
24건 및 기존 성공→N/A 경계도 다시 육안 검사했다. 실제 정보 51건을 회수하고 명시적 N/A 13건을 교정했다.
특히 `소수주주권` 문자열 충돌, 기존 false-SUCCESS 18건(수주 17·R&D 1), 위치 없는 유형자산표의 sites 오인을
회귀 테스트로 고정했다. 로컬 DART 원문 300사(KOSPI 169/KOSDAQ 131) × 5필드 = 1,500 슬롯 최종 결과는
예외 0, `SUCCESS` 1,245(83.0%), `NOT_APPLICABLE` 147, `NOT_COLLECTED` 108, 처리율 92.8%, 41.4초다.
잔여 108건은 실제 부재·무관 신호 또는 원천 보고서 불일치로 모두 분류했다. 한국단자공업 표본은 감사보고서가
잘못 선택돼 source-selection 후속 과제로 남긴다. 외부 DART 호출 없이 수행했고 전체 157 tests를 통과했다.
별도 로컬 FastMCP 실호출 1건(삼성전자 최신 분기 backlog)은 `SUCCESS`, 380자,
`II. 사업의 내용 / 바. 수주상황`, 약 5.5초로 통과했다.

## strict + candidate 문맥 계약 (260723)

구조 경계가 헤딩 없는 예외를 놓칠 수 있다는 반론을 고정 문자 창과 통제 비교했다. 같은 300사·1,500필드에서
현행 앵커·게이트·N/A 규칙은 고정하고 끝 경계만 바꿨다. 고정 창은 `SUCCESS` 1,276(85.1%)로 strict
1,245(83.0%)보다 높았지만, fixed-only `SUCCESS` 44건 중 43건은 strict에서 명시적 N/A, 1건은 미수집이었다.
strict-only `SUCCESS`는 13건이고 처리율은 strict 92.8% > fixed 92.4%였다. 따라서 고정 창의 추가 성공은
공식 결과에 섞을 수 없는 인접 절 문맥이며, 성능 이점(32.8초 vs 41.4초)만으로 기본 경로를 바꾸지 않는다.

결정: `context_mode="strict"`를 기본으로 유지한다. `context_mode="candidate"`는 strict가 `NOT_COLLECTED`일
때만 단일 표준 필드에 대해 활성화한다. `context_chars`는 기본 20,000자, 최대 60,000자이며 호출 AI가 필요할 때
재호출로 늘린다. 반환은 공식 필드와 분리한 `candidate_context.status="LOW_CONFIDENCE"`이고,
markdown·anchor·warning만 담는다. candidate는 hint 산출, `SUCCESS` 상태, 자동 비교에 절대 사용하지 않는다.

### 고정창 기본화 검토와 보류 (260723)

호출 AI는 넓은 원문에서 앵커 이후의 다른 소절을 읽고 제외해 요약할 수 있다. markdown-primary라는 도구 성격상
고정창 원문을 기본으로 주는 방식은 실사용 관점에서 성립한다. 다만 이는 **AI가 읽어 판단하는 문맥 제공 계약**이며,
현재의 `SUCCESS`·`NOT_APPLICABLE`·hint처럼 소절 자체를 기계적으로 판정하는 **공식 추출 계약**과는 다르다.

고정창을 기본으로 바꾸려면 상태와 hint를 축소하거나, 결과 타입을 `anchored_context`로 분리해야 한다. 그렇지 않으면
호출 AI가 아닌 소비자와 자동 후속 처리도 인접 소절을 공식 필드로 오인할 수 있다. 앵커 사전은 이미 strict와
candidate가 공유한다. 제목 변형을 계속 조사·추가하면 두 경로의 회수율이 함께 개선된다.

현재 결정은 유지: strict 기본 + `NOT_COLLECTED`에서만 candidate 재호출. 향후 실제 사용 로그에서 candidate의
재호출 빈도와 AI의 문맥 판독 성공률이 충분히 확인되고, 공식 상태·hint를 포기해도 되는 별도 문맥형 응답 계약이
필요해질 때에만 고정창 기본화 또는 `anchored_context` 별도 모드를 재검토한다.
