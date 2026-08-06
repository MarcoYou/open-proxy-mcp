---
type: decision
title: business_details tool — "II. 사업의 내용" 자동추출 스코프·계약
date: 2026-07-17
status: 확정 (스코프 정의 + strict/candidate 문맥 계약). 근거 census·로드맵은 storage 이관 260806
---

# business_details tool — "II. 사업의 내용" 자동추출 스코프·계약

> DART 정기보고서 "II. 사업의 내용"에서 **사업부문별 매출·이익·비중, 원재료, 생산실적, 가동률,
> 주요사업장, 연구개발비, 고객집중, 수주**를 구조화 추출하는 tool 의 스코프·계약을 정한 설계 결정.
> 이 결정을 세운 census·실현가능성 실측·로드맵은 storage
> (`wiki-private/architecture/이관_260806_arch-decisions.md`).

## 왜 (배경)

기존 20 tool은 거버넌스(agm·ownership·proxy·div)·전사재무(financial_metrics)·밸류(valuation) 축.
**부문 단위 사업 실질**(부문 수익성·SOTP·적자부문·일감몰아주기·고객집중·수주가시성)을 주는 tool은 부재.
"II. 사업의 내용"이 그 유일 공급원이나 기업·업종마다 양식이 달라 사람이 일일이 못 함 → 자동 추출 필요.

## 스코프 정의 (2026-07-17 확정 — 명확한 경계, 조용한 overreach 금지)

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

## 파싱 원칙

XML 단독([[XML-vs-PDF]]) · 이름기반 열매핑(위치 금지) · 표별 단위 파싱 · 조정/총계 열 분리 ·
결측 3분류(NOT_APPLICABLE / NOT_COLLECTED / EXTRACTION_FAILED).

**폼은 KSIC 로 가를 수 없다** — 같은 업종코드에 금융지주·제조지주·호텔지주가 공존한다
([[ksic-sector-mapping]]). 폼 판별은 **목차 소절 제목**으로 한다.

## 구현 아키텍처 확정 (260717 오후 — flatten 한계 실증 후 전환)

flatten 텍스트 파서는 2D표를 1D로 뭉개 정렬 whack-a-mole 천장에 걸린다. **표 선택**(수백 중첩표 중
진짜 부문표 판별)은 regex 가 약하고 LLM 이 강한 문제라 — CLAUDE.md soft-fail 원칙([[LLM-fallback-설계]]) —
정식 추출 경로를 아래처럼 갈랐다.

**segment_profit fallback (260718 최종확정 — 내부 LLM 폐기):**
> **핵심 통찰(사용자)**: MCP tool은 이미 LLM(호출측 Claude)이 부른다 → tool이 *또* 내부 LLM을 부르면
> LLM이 LLM 대신 추출하는 중복(지연·비용·비결정성·API키·anthropic/pandas 의존). **호출측 Claude가 공짜로 할 일.**
```
① 정형(flatten 텍스트, pandas無) — sum(부문)≈부문합계·총계 게이트 통과 → 구조화 반환 [공짜, ~30사]  source=deterministic
② 저신뢰/실패 → tool이 수백 중첩표를 '부문표 후보 ~3-5개'로 기계적 narrow(bs4 점수순, colspan확장) → raw 반환
                + note("이게 부문표 후보, 읽어서 추출") → 호출측 Claude가 선택+추출 [공짜, 내부 LLM 없음]  source=raw_candidates
③ NOT_APPLICABLE — 부문표 부재(단일부문·금융폼). 후보 안 붙임(깔끔한 N/A+사유)
```
이 분담선의 효과는 **anthropic·pandas·API키·지연·비용·비결정성을 전부 제거**한 것이다 — "AI in
production" 리스크가 통째로 사라진다. 비-LLM 배치 소비자가 생기면 그때 구조화를 재고한다.

**파일**: `services/business_details.py`(정형) + `services/segment_candidates.py`(후보 narrow, bs4-only) +
`tools/business_details.py`(래퍼) + `wiki/tools/business_details.md`.

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

> 이 교체의 전수 검증(원문 대조 건수·회수/교정 내역·최종 슬롯 집계)은 storage
> (`wiki-private/architecture/이관_260806_arch-decisions.md`).

## strict + candidate 문맥 계약 (260723)

구조 경계가 헤딩 없는 예외를 놓칠 수 있다는 반론을 고정 문자 창과 통제 비교했다(같은 표본, 끝 경계만 교체).
고정 창이 더 많은 `SUCCESS`를 냈지만 **그 추가분은 거의 전부 strict 에서 명시적 N/A 로 판정한 인접 절
문맥**이었다. 즉 고정 창의 초과 성공은 공식 결과에 섞을 수 없는 문맥이고, 처리율은 strict 가 오히려
높았다. 성능 이점만으로 기본 경로를 바꾸지 않는다.

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

## 관련
- [[ksic-sector-mapping]] (KSIC 한계 — 폼 판별에 KSIC 불신 근거)
- [[XML-vs-PDF]] (XML 단독 파싱 결정)
- 설계 근거가 된 census·실현가능성 실측·로드맵·검증 하네스 → storage
  (`wiki-private/architecture/이관_260806_arch-decisions.md`)
