---
type: tool
title: business_details
domain: data
scope: [segments, rnd, backlog, customers]
data_source: [DART get_document (전체보고서 XML 1콜 → II.사업의 내용 + 연결재무제표주석 부문정보 슬라이스), search list.json A001/A002/A003]
related_disclosures: [사업보고서, 분기보고서, 반기보고서]
related_concepts: [사업부문, 영업부문, K-IFRS 1108, SOTP, 부문 영업이익, 연구개발비, 수주잔고, 고객집중]
related_decisions: [260717_1220_decision_business-content-tool-roadmap, XML-vs-PDF, ksic-sector-mapping]
created: 2026-07-18
---

# business_details

## 한 줄 요약
DART 사업보고서 **"II. 사업의 내용"**에서 사업부문별 매출·영업이익·비중, 연구개발비, 수주잔고, 주요고객 집중도를 구조화 추출. SOTP·부문 수익성·적자부문·일감몰아주기 분석의 1차 소스. 156사 census로 실현가능성 검증.

## 사용법
- `business_details(company, period="annual", fields="", format="md")`
- `period`: `annual`(기본) / `quarterly`
- `fields`: 쉼표구분 선택(`segments,rnd,backlog,customers`, 미지정 시 전체). **`segments`만 지정하면 주석 fetch 생략돼 빠름**(고객집중은 주석 필요).

## 출력 (ToolEnvelope.data)
- `form_type`: `standard7` / `financial5` / `reit` / `dual` (목차 소절 제목 기반 판별, KSIC 불신)
- `segments`:
  - `status=OK` + `source=deterministic` → `items:[{name, revenue, profit}]` + `reconciliation`(sum-check)
  - `status=NEEDS_REVIEW` + `source=note_markdown`/`biz_markdown` → `segment_note_md`(영업부문 주석 원문 마크다운) → **호출측 LLM이 읽어 추출**. 앵커 실패 시 `source=raw_candidates` + `candidates:[{rendered, score}]`
  - `status=NOT_APPLICABLE` → 단일부문(정상)
  - `status=UNSUPPORTED_FORM` → 금융폼·REIT(v1 미지원, D-트랙 별도)
- `rnd`{amount, ratio_to_sales_pct} · `backlog` · `customers`[10%↑ 외부고객]
- `timings_ms`(단계별 병목) · `note_fetched`(주석 lazy fetch 여부)

## Data sources
DART **`get_document`(전체 보고서 XML) 1 API콜**([[XML-vs-PDF]]) → text에서 `II.사업의 내용`·`연결재무제표 주석`(별도 heading 전까지) 슬라이스(`_slice_getdoc_sections`), html은 후보표 스캔용 원본. viewer 3웹콜(~5s) 대비 **~3x 빠름**. **PDF/OCR·내부 LLM·pandas 불필요.** (get_document 014=document.xml 부재 시 graceful ERROR — KB금융류 극소수)

## 파싱전략 (핵심 — [[260717_1220_decision_business-content-tool-roadmap]])
flatten이 2D표를 1D로 뭉개 정렬이 깨지는 게 근본 난제(156 census 실증: 정형 신뢰 ~91%가 천장).
**설계 결정(260718, 사용자)**: MCP tool은 이미 호출측 LLM이 부르므로 **내부 LLM 불필요** — tool은 기계적으로 좁히고 의미 추출은 호출측이.
- **① 정형(flatten)** — 본문표+주석표를 **둘 다 파싱해 교차검증**(`_seg_names_agree` 60%겹침): 불일치=지주사가 본문에 자회사표 실은 케이스 → `cross_conflict`로 후보강등(주석=K-IFRS 1108 권위). 통과 시 clean 게이트 후 구조화 반환(공짜, 대형주 값정확). 게이트=junk명·**지역정보표(국내/해외/외국/'본사 소재지 국가')**·**매출유형(제품/상품매출액)**·**비기타부문 음수매출**·`_scrub_segments`(값없는행·'감가상각비'/'연결 후 금액'/'3)비유동자산' 재무라인 제거). ~300사(156+제조145) 검증: 정형OK 64사 육안 clean → best-effort 빠른힌트.
- **② 저신뢰/실패 → 영업부문 주석 원문을 마크다운으로 통째 반환**(260718 사용자 결정): '어느 표인지' 점수매기는 파서 대신, `render_segment_note_markdown`이 **'N. 영업부문' 번호 헤딩을 앵커**로 주석 구간을 잡아 설명문단+표 전부를 **깔끔한 마크다운 표**로 렌더 → 호출측 AI가 읽어 추출(값 억지추출 X). 단일선언(`_SINGLE_DECL_RE`) 구간은 스킵(단일사 noise 방지), 앵커 실패 시 **II.사업의 내용 마크다운 폴백**(지주사류), 그것도 없으면 후보표(bs4). 이 마크다운 회수로 **정형이 못 뽑던 하드케이스(액트로·아미노로직스·삼일=2D표·부문명만·수익유형) 전부 surfaced** (~300사 검증 MISS 0). `_is_roster`로 임원명부 배제.
- **③ N/A** — 후보표 0개(단일부문·표부재).
- **폼 게이트**: `detect_form`에서 **has_mfg(주요제품·원재료 소절)가 REIT/금융 veto** — 유통사가 리츠 자회사 보유해 프로즈에 '부동산투자회사' 있어도 표준폼 유지(롯데쇼핑 회귀 fix). 진짜 금융지주·REIT만 `UNSUPPORTED_FORM`(D-트랙).

**성능**: get_document 1콜이 지배(미캐시 ~2-2.5s, 캐시히트 150-470ms). 후보 스캔(Afields)은 정규식 프리필터로 <150ms(POSCO 9.7MB도). 단계별 `timings_ms`(resolve/search/fetch/segment/Afields/total)로 실측.

## 관련
- [[260717_1220_decision_business-content-tool-roadmap]] (설계·실현가능성·스콥·아키텍처)
- [[ksic-sector-mapping]] (KSIC 한계 — 폼 판별에 불신)
- [[XML-vs-PDF]] (viewer HTML 단독)
- `wiki/_local/census-biz-content-260717/` (156사 census 원본·ground-truth·재현 스크립트, gitignore)
