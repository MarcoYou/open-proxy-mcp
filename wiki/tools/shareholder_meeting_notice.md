---
type: tool
title: shareholder_meeting_notice
description: 주주총회 소집공고 (사전) — DART API/XML 기반
related: [shareholder_meeting_results, proxy_advise_before_meeting, ownership_structure, proxy_contest, evidence]
---

# shareholder_meeting_notice

주총 **소집공고** 공시 데이터 (사전 — DART API/XML). 빠르고 안정 (0.5-1.5s). 2026-05-24부터 summary 기본 응답은 경량화하고, stage별 `timings_ms`를 노출한다.

## 분리 배경 (2026-05-04)

기존 `shareholder_meeting` tool은 DART API + KIND scraping 두 source를 한 데에 묶었음:
- **notice scopes** (DART, 0.5-1.5s, 안정적): summary/agenda/board/compensation/aoi_change
- **results scope** (KIND, 4.9s, fragile): 결과 의결 결과

→ proxy_advise_before / proxy_result_after 분리 패턴과 consistency. KIND fragile 부분 격리. Claude.ai 동적 tool loading 부담 감소.

## scope (5, 260506 정리)

| scope | 데이터 | 시간 |
|---|---|---|
| `summary` (default) | 메타 + 정정공시 cover + **안건 hierarchy (number+title+children)** + **1호 안건 메타 (회기/사업연도/배당 예정액)**. 긴 전자투표/온라인중계 안내문은 기본 제외. | 0.5s |
| `board` | 이사·감사 후보 + 경력 (raw) | 0.5s |
| `compensation` | 보수한도 안건 + 소진율 | 0.5s |
| `aoi_change` | 정관변경 (변경 전/후/사유) **+ 퇴직금 변경 raw** (260505 통합) | 0.5s |
| `prov_financials` (NEW 260506) | 잠정 재무제표 4 quadrant raw — consolidated/separate × balance_sheet/income_statement + flat metrics | 0.5s |

### 폐지된 scope (260506)

- `agenda` — summary에 hierarchy 통합 (silent fallback to summary)
- `full` — 병렬 wrapper, 거의 사용 X. 종합 분석은 `proxy_advise_before_meeting` 호출 (silent fallback to summary)

### 제거된 필드 (시점 분리)

- `result_status` / `result_reference` — 사후 정보, `shareholder_meeting_results` tool 참조

## source

- DART OpenAPI `list.json` 검색 + 상세 (`fnlttSinglAcnt` 등 X — XML 직접 파싱)
- DART XML 본문 (rcept_no → viewer_url)
- 정정공시 자동 선택 (rcept_no rank — 최신 정정 우선)

## 성능/디버깅 옵션 (2026-05-24)

| 옵션/필드 | 의미 |
|---|---|
| `include_coverage=false` (default) | 명시적 `annual`/`extraordinary` 조회에서 최근 12개월 정기/임시 coverage 재검색을 생략. 정기/임시 판별은 선택된 소집공고 본문으로 계속 수행. |
| `include_coverage=true` | `meeting_coverage_12m`를 추가 계산. 최근 정기/임시 주총 존재 여부가 필요한 경우에만 사용. |
| `rcept_no` | 이미 소집공고 접수번호를 알면 회사 식별/후보 검색을 건너뛰고 해당 원문을 직접 파싱. 리포트 재현과 timeout fallback에 유용. |
| `fiscal_month` | `annual` + `year` 조회에서 OpenDART `company.json.acc_mt` 결산월을 읽어 정기주총 후보 window를 먼저 좁힘. fiscal window에서는 최신 후보 1건만 먼저 열고, 정기 매칭 실패 시 나머지 후보와 full-year 검색으로 fallback. |
| `data.timings_ms` | `resolve_company`, `fiscal_month_lookup`, `select_notice_candidate`, `select_notice_candidate.search_filings`, `select_notice_candidate.fetch_top_documents`, `select_notice_candidate.parse_top_documents`, `select_notice_candidate.filter_meeting_window`, `select_notice_candidate.build_candidate`, `select_notice_candidate.full_year_fallback`, `coverage_search`, `load_notice_bundle`, `total` 등 stage별 소요 시간(ms). 병목 원인 확인용. |

## 사용 예

```
"삼성전자 다음 주총 안건 알려줘"
"LG화학 사외이사 후보 명단"
"카카오 보수한도 인상률 정보"
"현대차 정관변경 변경 전/후 비교"
"LG화학 주총소집공고 rcept_no=20260224004273으로 다시 파싱해줘"
```

## ref

- 주총 결과 의결: [[shareholder_meeting_results]]
- 종합 분석 (안건별 FOR/AGAINST): [[proxy_advise_before_meeting]]
- 후보 평가 (사용자 노출 X — proxy_advise chain): director_evaluation (services internal)
- 지분 구조: [[ownership_structure]]
- 분쟁 맥락: [[proxy_contest]]
