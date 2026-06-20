---
type: tool
title: shareholder_meeting_notice
description: 주주총회 소집공고 (사전) — DART API/XML 기반
related: [shareholder_meeting_results, proxy_advise_before_meeting, ownership_structure, proxy_contest, evidence]
---

# shareholder_meeting_notice

주총 **소집공고** 공시 데이터 (사전 — DART API/XML). 빠르고 안정 (0.5-1.5s). 2026-05-24부터 summary 기본 응답은 경량화하고, stage별 `timings_ms`를 노출한다. 2026-05-25 KOSPI300 재검증에서 정기 소집공고가 현재 DART에 없는 2건을 제외하고 `requires_review` 0을 확인했다.

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

## 파싱 정확도 / relation metadata (2026-05-25)

- agenda node는 `proposer_type`, `agenda_relation_type`, `agenda_relation_reasons`를 포함한다.
- `agenda_relation_type`: `normal`, `procedural`, `conditional`, `alternative`, `cumulative_related`.
- 정기/임시 판별(`detect_meeting_type`, 2026-06-20 개선)은 **head 길이 제한 없이** text 전체에서 `주주총회 소집공고` 매칭을 순회하며, 그 직후 40자 윈도우의 괄호 종류표기 — `(제N기 정기|임시)` · `(YYYY년 정기|임시)` · `(정기|임시주주총회)` — 가 가까이 오는 **첫 매칭**을 앵커로 채택한다. 본문 후방 문장(`주주총회 소집공고 등)에 의거…`)을 앵커로 잡던 오선택(임시→정기 fallback), head 내 참고사항의 `임시` 단어 선점, `(YYYY년 임시)` 변형 누락을 함께 해소한다. 윈도우에서 못 찾으면 text 전체의 첫 `(정기|임시)주주총회` 키워드로 fallback. `parse_meeting_info_xml`의 1순위 heading 패턴도 `(YYYY년 …)` 변형까지 확장했다. 전수 검증(2026 3/15~5/15, 891건, text 픽스처 순수함수 재호출 DART 0콜): 구 detect 880/891 → 신 detect 888/891. 잔여 차이는 회사가 **제목과 본문에 정기/임시를 다르게 적은 모순 공시**(891건 중 11건, 1.2% — 예: 파멥신 제목 `(정기)`/본문 `임시주주총회를 …개최`, 프리티 제목 `(제56기 임시)`/본문 `정기주주총회를 …개최`)다. 이런 공시는 `detect_meeting_type_conflict(text)`가 감지해(제목 종류표기 ≠ 본문 `(정기|임시)주주총회를 다음/아래 …` 소집문구) `parse_meeting_info_xml`의 `meeting_type_conflict` 플래그로 노출한다. detect는 제목을 우선해 한 값을 주되, 플래그가 뜨면 안건(재무제표 승인 여부 등)으로 **수동 확인**이 필요하다. 회귀 앵커(와이즈넛 20260512000585=임시, JTC 20260513000621=정기) 유지.
- 마침표형 안건 marker(`제N호 의안.`), 후보자 표 boundary, `4. 목적사항` 정정공고형 목록, `※` 주석 뒤 안건 경계를 지원한다.
- `annual` 조회에서 정기 소집공고가 아직 없으면 결산월과 예상 정기주총 window를 warning에 표시한다.
- KOSPI300 재실행: `exact` 298, `no_filing` 2, `requires_review` 0. 상세: [[260525_0200_audit_agenda-relation-kospi300]].
- KOSPI500 + KOSDAQ150 marketwide audit: XML 확보 641건, `no_filing` 9건, 로컬 재파싱 3회 hash diff 0. 상세: [[260525_1620_audit_agenda-parser-marketwide]].

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
- relation/parser audit: [[260525_0200_audit_agenda-relation-kospi300]]
- marketwide parser audit: [[260525_1620_audit_agenda-parser-marketwide]]
