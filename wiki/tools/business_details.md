---
type: tool
title: business_details
domain: data
scope: [segments, rnd, backlog, customers]
data_source: [DART viewer HTML (II.사업의 내용 chapter + 재무제표주석 부문정보), search list.json A001/A002/A003]
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
  - `status=NEEDS_REVIEW` + `source=raw_candidates` → `candidates:[{rendered, score}]`(부문표 원문 후보) → **호출측 LLM이 표 읽어 추출**
  - `status=NOT_APPLICABLE` → 단일부문(정상)
  - `status=UNSUPPORTED_FORM` → 금융폼·REIT(v1 미지원, D-트랙 별도)
- `rnd`{amount, ratio_to_sales_pct} · `backlog` · `customers`[10%↑ 외부고객]
- `timings_ms`(단계별 병목) · `note_fetched`(주석 lazy fetch 여부)

## Data sources
DART viewer HTML 단독([[XML-vs-PDF]]). main.do 목차(treeData 노드트리) → II.사업의 내용 chapter + 재무제표주석(연결 우선, 없으면 별도) 섹션 fetch. **PDF/OCR·내부 LLM·pandas 불필요.**

## 파싱전략 (핵심 — [[260717_1220_decision_business-content-tool-roadmap]])
flatten이 2D표를 1D로 뭉개 정렬이 깨지는 게 근본 난제(156 census 실증: 부문표 101사 중 정형 신뢰 ~30만).
**설계 결정(260718, 사용자)**: MCP tool은 이미 호출측 LLM이 부르므로 **내부 LLM 불필요** — tool은 기계적으로 좁히고 의미 추출은 호출측이.
- **① 정형(flatten)** — sum(부문)≈부문합계 + 부문명 clean(junk/이름형태불일치 배제) 게이트 통과 시 구조화 반환(공짜). GT 대조 정형신뢰 ~77-85%(flatten 본질한계) → best-effort 빠른힌트.
- **② 저신뢰/실패 → 후보표 raw 반환**: 수백 중첩표를 부문표 후보 ~3-5개로 bs4 점수순 narrow(colspan 확장) → 호출측 Claude가 선택+추출. **이게 신뢰 경로**(156사 에이전트 추출 152/156 high로 검증). 부문합계/조정/총계 열 제외 안내.
- **③ N/A** — 단일부문 선언·금융폼(표 부재).
- **폼 게이트**: 금융지주·REIT는 목차 제목으로 판별해 `UNSUPPORTED_FORM`(억지 파싱 금지, D-트랙).

**성능**: 웹fetch가 90%(2초 throttle×콜수 + 주석 688KB~1.3MB). **lazy note fetch**로 본문형+segments-only는 주석 skip(오리온 6s→2.5s). 향후 `get_document`(1 API콜)로 웹3콜 대체 여지(파서 재작업).

## 관련
- [[260717_1220_decision_business-content-tool-roadmap]] (설계·실현가능성·스콥·아키텍처)
- [[ksic-sector-mapping]] (KSIC 한계 — 폼 판별에 불신)
- [[XML-vs-PDF]] (viewer HTML 단독)
- `wiki/_local/census-biz-content-260717/` (156사 census 원본·ground-truth·재현 스크립트, gitignore)
