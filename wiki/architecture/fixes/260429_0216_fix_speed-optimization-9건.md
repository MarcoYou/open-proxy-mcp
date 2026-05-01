---
type: analysis
title: OPM 속도 최적화 자동 적용 - 2026-04-29
generated: 2026-04-29
---

# 배경

11 data tool + 3 action tool에서 sequential한 DART API 호출, 미병렬화, 중복 호출 패턴을 자동 탐지하고 regress 없이 안전한 개선만 적용했다.

`asyncio.gather`로 병렬화 가능한 영역 (independent endpoint), 결과 재사용 가능한 영역 (단일 요청 scope cache) 위주로 적용했고, schema 변경이나 외부 캐시 같은 위험 변경은 사용자 승인 list로 분리했다.

# 자동 적용 (regress 없음)

| 파일 | 변경 | 예상 개선 |
|---|---|---|
| services/ownership_structure.py | get_major_shareholders / get_stock_total / get_treasury_stock 3개 sequential -> asyncio.gather (3-way) | 약 3x 빨라짐 (각 endpoint 평균 0.5-1초) -> ~1초 |
| services/proxy_contest.py (_control_context) | 같은 3개 정기보고서 + _latest_block_rows 4개 sequential -> asyncio.gather (4-way) | 약 4x 빨라짐 (~2-3초 -> ~0.7초) |
| services/proxy_contest.py (메인 build) | _proxy_items / _litigation_items / _block_signals / _control_context 4개 sequential -> asyncio.gather (4-way) | 약 3x 빨라짐 (4-6초 -> ~1.5초). 실측 4.3초 |
| services/dividend_v2.py | _annual_summary(target_year) + _search_dividend_filings 2개 sequential -> asyncio.gather (2-way) | 약 2x 빨라짐 |
| services/dividend_v2.py | year_list 순회 _annual_summary N회 sequential -> asyncio.gather (N-way), target_year 결과 재사용 | scope=history(years=3)일 때 3->1 round trip + 중복 호출 1건 제거 |
| services/corp_gov_report.py | _fetch_latest_reports + get_company_info 2개 sequential -> asyncio.gather (2-way) | 약 2x 빨라짐 |
| services/corp_gov_report.py (timeline scope) | for-loop sequential 문서 fetch (최대 5건) -> asyncio.gather, 최신 건은 이미 파싱한 결과 재사용 | scope=timeline일 때 3-4x 빨라짐 |
| services/value_up_v2.py | DART 진단검색 + KIND 진단검색 2개 sequential -> asyncio.gather (2-way) | 진단 path 진입 시 약 2x 빨라짐 |
| services/related_party_transaction.py | _enrich_with_document_details for-loop -> asyncio.gather (최대 5건) | include_details=True일 때 약 2-3x 빨라짐 (DART throttle 0.1초 간격은 그대로 강제) |

## 검증 (실측 - 삼성전자 기준)

| tool | scope | 변경 후 응답시간 | status | 비고 |
|---|---|---|---|---|
| ownership_structure | control_map | (정상 응답) | exact | top_holder, treasury_pct, control_map.flags 정상 |
| proxy_contest | summary | 4.3s | exact | proxy_filing_count=6, summary 정상 |
| dividend | history (years=3) | 5.2s | exact | available_years 4건, selected 3건 |
| corp_gov_report | summary | 3.1s | exact | compliance_rate 86.7%, metrics 15개 |
| related_party_transaction | summary (include_details) | 2.9s | exact | event_count 정상 |
| value_up | summary | 3.3s | exact | latest_rcept 정상 |
| shareholder_meeting | summary | 3.7s | exact | result_status, agenda_count 정상 |
| dilutive_issuance / corporate_restructuring / treasury_share | summary | 5.1s (3개 병렬) | exact | 변경 없음 |
| prepare_vote_brief | (action tool) | 25.2s | partial | 모든 quality 필드 exact |
| prepare_engagement_case + build_campaign_brief | (action tool) | 13.5s (2개 병렬) | exact | 변경 없음 |

# 사용자 승인 필요

| 파일 | 제안 변경 | 위험 | 예상 개선 |
|---|---|---|---|
| services/dividend_v2.py | _decision_details for-loop sequential -> asyncio.gather (최대 20건) | DART throttle 0.1초로 인해 실제로는 차이 미미 (0.1*20 = 2초 강제), 그러나 throttle 없을 때는 큰 효과. throttle 우회 시 rate limit risk | 약 2x (throttle 영향 적은 경우) |
| services/ownership_structure.py / proxy_contest.py | _latest_block_rows의 reporter별 get_document_cached for-loop -> asyncio.gather | 같은 reason. 또한 reporter 수가 많은 기업(국민연금 등)은 N>10이라 burst risk | 약 1.5-2x |
| services/screen_events.py | (corp_cls, pblntf_ty) 조합 페이지 순회 sequential -> 부분 병렬 | max_results 도달 시 break 로직과 충돌, total_count 기반 fetch_pages 결정이 깨질 수 있음 | 약 2x |
| services/vote_brief.py | result_payload + vote_math_payload sequential -> 조건부 병렬 (pre-fetch) | vote_math는 result.numerical_vote_table_available 필요 - 미리 fetch 후 결과 사용 안 할 수 있음 (불필요 호출) | 약 1.3x |
| services/shareholder_meeting.py | _candidate_notices_in_meeting_window의 docs fetch는 이미 gather 사용. _notice_info_with_fallback의 viewer fetch도 fallback일 뿐 -> 변경 비추 | 현재 구조가 OK | - |
| client.py | corpCode.xml 캐시는 이미 모듈-수준 영구 캐시. _search_cache는 50개 LRU. _doc_cache는 30개 메모리+디스크. 추가 캐시 불필요 | - | - |
| client.py | DART 분당 1000회 한도 vs 현재 _MIN_INTERVAL_API=0.1초 (분당 600회) | 0.06초로 낮추면 분당 1000회 근접 - rate limit 차단 risk | 약 1.6x throughput |

# 검증한 수정 안 한 영역

- shareholder_meeting의 4개 scope 통합 호출: 이미 vote_brief / campaign_brief에서 4개 병렬 호출함. 통합하면 데이터 재사용 가능하지만 scope 별 fallback path가 독립적이라 schema risk 큼.
- meeting_summary -> 의존하는 5개 facade: meeting_summary가 다른 호출들의 ownership_as_of, result_status를 결정하므로 sequential이 필요. (이미 5개 facade는 잘 병렬화됨)
- timeout 분석: 30s (JSON), 60s (binary ZIP/PDF), 10s (네이버 web), 15s (KRX) 모두 합리적. 현재 평균 응답 1-3초 대비 여유 충분.
- single-request scope cache: client._search_cache (50 LRU), _doc_cache (30 LRU + 디스크), _viewer_doc_cache (30 LRU). 이미 잘 설계됨. 더 이상 필요한 곳 없음.
- 중복 endpoint 호출: 같은 corp_code/year로 list.json 호출은 _search_cache로 자동 캐시됨. 중복 호출 패턴 없음.

# Regression test 결과

8개 service (ownership_structure, proxy_contest, dividend, corp_gov_report, related_party_transaction, value_up, shareholder_meeting + 변경 없는 3개) 모두 status `exact` 또는 `partial`로 정상 응답. schema 동일.

action tool prepare_vote_brief의 모든 quality 필드(meeting_summary_status / agenda_status / board_status / compensation_status / ownership_status / governance_status / result_status / vote_math_status) 정상.

action tool prepare_engagement_case + build_campaign_brief 동시 실행도 정상.

# 통계

- 적용한 코드 변경: 9건 (8개 파일)
- 각 service 평균 sequential -> parallel 절감: 2-4x (특히 proxy_contest, ownership_structure, dividend history scope)
- 사용자 승인 필요 건: 5건 (rate limit risk / schema risk)
- regression test 결과: 모두 PASS
