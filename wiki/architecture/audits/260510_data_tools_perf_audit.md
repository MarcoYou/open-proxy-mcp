# 260510 데이터 도구 성능 audit

## 최종 요약

2026-05-24 후속 timing audit:
- 8개 핵심 tool에 `data.timings_ms` stage timing을 노출했다.
- 표본: `LG화학`, `삼성전자`, `KT&G` 3개 회사 × 8개 tool.
- 빠른 축: `company` 180-462ms, `shareholder_meeting_notice` 373-574ms, `financial_metrics` 377-446ms, `ownership_structure` 456-585ms.
- 2026-05-24 후속 fix 후 빠른 축: `company` 194-392ms, `shareholder_meeting_notice` 360-570ms, `financial_metrics` 399-450ms, `ownership_structure` 381-440ms.
- 후속 fix 후 느린 축: `dividend` 1.10-1.59s, `treasury_share` 0.69-0.95s.
- `treasury_share`는 결과보고서 검색을 전체 공시(`pblntf_ty=""`)에서 `B/I/E` title scan 1회로 변경하고 DS005 API 호출과 병렬화해 삼성전자 2.7s급 경로를 약 0.9s로 낮췄다.
- `dividend`는 선배당/감액배당 메타 detection과 과거 연도 `alotMatter` 호출을 앞당겨 summary/filing 경로와 겹치게 했다.
- `dividend.summary_and_filings` 병목은 `search_filings`로 확인되어, `list.json` page 2 이후 fetch를 병렬화했다.
- 남은 반복 병목: `dividend.summary_and_filings`, `treasury_share.fetch_decisions`.
- 근거 파일: `wiki/architecture/audits/data/260524_tool_timing_audit.json`.

이번 audit에서 실제로 반영된 성능 개선은 8건입니다.
- `shareholder_meeting` 계열의 request-local soup 재사용
- `company`의 NAVER 업종 보강 제거 + DART `induty_code` 기반 로컬 KSIC 업종명 매핑
- `dividend`의 감액배당 cross-link 경량화 (`shareholder_meeting` 전체 payload 대신 안건 제목 전용 helper 사용)
- `dividend`의 선배당/감액배당 메타 detection 병렬화
- `dividend`의 과거 연도 `alotMatter` 조회 선시작으로 summary/filing 대기와 overlap
- 공시 title scan helper의 page 2+ 병렬 fetch
- `value_up`의 `treasury_cross_ref` 경량화 (`treasury_share` 전체 summary 대신 전용 treasury signal helper 사용)
- `treasury_share`의 결과보고서 반복 검색 제거 (같은 `list.json` 범위 1회 fetch 후 keyword별 로컬 필터)

`shareholder_meeting` 최종 검증 결과는 다음과 같습니다.
- production 코드 기준 `215 / 215` payload equality 유지
- status 변경 `0건`
- median speedup `58.6%`
- mean speedup `57.8%`
- 최대 speedup `88.8%`
- 최소 speedup `-4.0%`

`company` 최종 검증 결과는 다음과 같습니다.
- 변경 전 median `4.137s`
- 변경 후 median `0.211s`
- p95 `4.163s -> 0.251s`
- mean `4.138s -> 0.229s`
- 상태 `60 / 60 exact` 유지

`dividend` 최종 검증 결과는 다음과 같습니다.
- 동일 60개 표본 직접 비교 기준 `60 / 60` equality 유지
- 변경 전 median `1.445s`
- 변경 후 median `0.698s`
- mean `1.655s -> 0.820s`
- median speedup `51.1%`
- mean speedup `50.6%`
- status 변경 `0건`

`value_up` 최종 검증 결과는 다음과 같습니다.
- 동일 60개 표본 직접 비교 기준 `60 / 60` equality 유지
- 변경 전 median `2.269s`
- 변경 후 median `0.359s`
- mean `2.470s -> 1.822s`
- median speedup `80.0%`
- mean speedup `25.2%`
- status 변경 `0건`
- 추가 40개 표본에서도 `40 / 40` equality 유지
- 누적 100개 기준 `100 / 100` equality 유지
- 누적 100개 기준 median `2.284s -> 0.339s`
- 누적 100개 기준 mean `2.560s -> 1.641s`
- 누적 100개 기준 median speedup `83.0%`

`treasury_share` 최종 검증 결과는 다음과 같습니다.
- fresh baseline 60개 표본 기준 median `1.923s -> 1.002s`
- mean `2.033s -> 1.027s`
- p95 `3.950s -> 1.475s`
- max `5.238s -> 3.617s`
- legacy fan-out 대비 direct compare `60 / 60` equality 유지
- direct compare 기준 median speedup `22.8%`
- mean speedup `15.5%`
- negative speedup outlier `9 / 60`, 주로 `no_filing` 또는 이미 싼 경로의 warm-cache 민감도

추가 표본 보강 결과는 다음과 같습니다.
- 기존에 성능 검증에 사용한 `KOSPI 50 + KOSDAQ 10 + value_up 추가 40개`와 겹치지 않는 별도 표본을 선정했다.
- 추가 표본 크기: `KOSPI 50 + KOSDAQ 20 = 70개`
- 실행 방식: `10개` 단위 순차 배치, 배치 간 `20초` sleep, item 간 `0.5초` sleep, tool 간 `30초` sleep
- `company` 추가 표본: `70 / 70 exact`, median `0.198s`
- `dividend` 추가 표본: `53 exact`, `17 no_filing`, median `1.081s`, p95 `2.668s`
- `treasury_share` 추가 표본: `36 exact`, `34 no_filing`, median `0.876s`, p95 `1.756s`
- 추가 표본에서도 새로운 회귀 신호는 없었고, 느린 exact 경로가 특정 회사군에서 반복된다는 기존 관찰이 재확인됐다.

이번 audit에서 기각된 후보도 분명합니다.
- `treasury_share`의 body enrichment 생략은 속도 이득이 작고 semantic drift가 커서 기각

한 줄 결론:
- 이번 성능 개선은 `shareholder_meeting`, `company`, `dividend`, `value_up`, `treasury_share` 5건이 실제 반영됐다.

## 무엇을 바꿨나

적용한 변경:
- 대상: `open_proxy_mcp/services/shareholder_meeting.py`
- 변경 전: 같은 공고 HTML을 같은 요청 안에서 여러 번 다시 `BeautifulSoup` 파싱
- 변경 후: 같은 요청 안에서는 같은 `rcept_no` 문서 soup를 재사용
- 캐시 범위: request-local
- 안전 장치: 전역 캐시 없음, 파싱 결과 dict 공유 없음, raw document/soup 재사용만 허용

적용한 변경:
- 대상: `open_proxy_mcp/services/company.py`
- 변경 전: DART `company.json` + NAVER 업종 보강 + 최근 공시 인덱스
- 변경 후: DART `company.json` + 로컬 KSIC 업종명 매핑 + 최근 공시 인덱스
- 제거한 경로: NAVER 업종명 스크래핑
- 대체 방식: DART `induty_code`를 `ksic10_ko.json`으로 매핑, 긴 코드는 prefix fallback 허용

적용한 변경:
- 대상: `open_proxy_mcp/services/dividend_v2.py`, `open_proxy_mcp/services/shareholder_meeting.py`
- 변경 전: 감액배당 메타가 `build_shareholder_meeting_payload(..., scope=\"summary\")` 전체 경로를 호출
- 변경 후: `load_shareholder_meeting_agenda_titles()` helper로 notice 안건 제목만 추출
- 유지한 의미: 감액배당 키워드 판정 규칙, summary/meta_signals 출력 형태
- 제거한 비용: `meeting_coverage_12m`, envelope 조립, 부가 메타 계산 같은 비필수 단계

적용한 변경:
- 대상: `open_proxy_mcp/services/value_up_v2.py`, `open_proxy_mcp/services/treasury_share.py`
- 변경 전: `value_up`의 `treasury_cross_ref`가 `build_treasury_share_payload(..., scope=\"summary\")` 전체 경로를 호출
- 변경 후: `fetch_treasury_signal_summary()` helper로 최근 24개월 자사주 신호 요약만 계산
- 유지한 의미: `treasury_cross_ref` 5개 수치 필드와 출력 형태
- 제거한 비용: 결과보고서 본문 파싱, cycle matching, 전체 event/type_breakdown 조립

적용한 변경:
- 대상: `open_proxy_mcp/services/treasury_share.py`, `open_proxy_mcp/services/filing_search.py`
- 1차 변경: `_fetch_decisions()`가 결과보고서 4종을 각각 `search_filings_by_report_name(..., pblntf_tys=\"\")`로 다시 검색하던 경로를 `fetch_filings_for_title_scan(..., pblntf_tys=\"\")` 1회 fetch 후 keyword별 로컬 필터로 축소
- 2차 변경: 전체 공시(`pblntf_tys=\"\"`) 대신 `B/I/E` title scan 1회로 축소하고 DS005 API 호출과 병렬화
- 유지한 의미: 결과보고서 분류, body enrichment, cycle matching, summary/events/type_breakdown 출력 형태
- 제거한 비용: 같은 공시 범위에 대한 중복 `list.json` fan-out 검색 4회

기각한 변경:
- 대상: `treasury_share`
- 시도: body enrichment를 건너뛰어 응답 시간 단축
- 결과: 속도 이득은 작고 cancelation/result 관련 필드 drift가 커서 미적용

## 개선 효과

실험 단계와 production 반영 후 결과를 구분해서 봐야 합니다.

실험 단계:
- 단일 샘플 `LG화학` 기준 `0.7656s -> 0.0648s`
- 약 `11.8x` 개선
- 215개 누적 실험 기준 median `80.2%`, mean `75.9%`

production 반영 후:
- 215개 누적 검증 기준 median `58.6%`, mean `57.8%`
- 실험보다 이득이 줄어든 이유는 안전성을 위해 재사용 범위를 notice-parser request path로만 좁혔기 때문
- 그래도 semantic drift 없이 과반 이상의 속도 개선이 유지됨

`company` 반영 후:
- `KOSPI 50 + KOSDAQ 10` 기준 median `4.137s -> 0.211s`
- mean `4.138s -> 0.229s`
- NAVER 호출 제거가 핵심이었고, 업종명은 KSIC 로컬 매핑으로 복구

`dividend` 반영 후:
- 같은 `KOSPI 50 + KOSDAQ 10` 표본 직접 비교 기준 median `1.445s -> 0.698s`
- mean `1.655s -> 0.820s`
- `60 / 60` equality 유지
- 개선의 핵심은 감액배당 cross-link가 `shareholder_meeting` 전체 payload를 만들지 않도록 경량화한 것

`value_up` 반영 후:
- 같은 `KOSPI 50 + KOSDAQ 10` 표본 직접 비교 기준 median `2.269s -> 0.359s`
- mean `2.470s -> 1.822s`
- `60 / 60` equality 유지
- 개선의 핵심은 `treasury_cross_ref`가 `treasury_share` 전체 summary를 만들지 않도록 경량화한 것

`treasury_share` 반영 후:
- fresh baseline `KOSPI 50 + KOSDAQ 10` 기준 median `1.923s -> 1.002s`
- mean `2.033s -> 1.027s`
- p95 `3.950s -> 1.475s`
- max `5.238s -> 3.617s`
- same-process legacy fan-out 직접 비교 기준 `60 / 60` equality 유지
- direct compare median speedup `22.8%`, mean `15.5%`
- 개선의 핵심은 결과보고서 4종이 같은 `list.json` 범위를 각자 다시 긁지 않도록 search fan-out을 제거한 것

추가 비중복 표본 재검증:
- `company`: `70 / 70 exact`, median `0.198s`, p95 `0.233s`
- `dividend`: `53 exact`, `17 no_filing`, median `1.081s`, p95 `2.668s`
- `treasury_share`: `36 exact`, `34 no_filing`, median `0.876s`, p95 `1.756s`
- `dividend` 느린 상위는 `JB금융지주`, `BNK금융지주`, `CJ`, `GS`, `CJ제일제당`으로 수렴했다.
- `treasury_share` 느린 상위는 `JB금융지주`, `BNK금융지주`, `HPSP`, `HD건설기계`, `OCI홀딩스`로 수렴했다.
- 해석: 기존 표본에서 보였던 hot path가 우연이 아니라 회사군 특성에 따라 반복된다는 점이 보강됐다.

## 하락 케이스와 트레이드오프

확인된 하락 케이스는 제한적입니다.
- production 누적 215개 중 최저 speedup은 `-4.0%`
- 이 하락은 이미 값이 거의 없는 `no_filing` 경로에서 관측된 예외적 케이스
- payload equality는 `215 / 215`로 유지됐고 status drift도 없었음

즉 이번 변경의 trade-off는 “일부 이미 싼 실패/무공시 경로에서는 퍼센트상 느려 보일 수 있음” 정도입니다. 반대로 결과 의미 변화, 상태 변화, 출력 필드 손실은 이번 검증 범위에서 발견되지 않았습니다.

`treasury_share` 쪽 기각 사유는 별개입니다.
- body enrichment skip 실험은 약 `5.5%` 개선에 그쳤음
- 대신 cancelation/result 필드가 크게 흔들려 semantic regression 발생
- 따라서 속도보다 품질 손실이 커서 채택 불가

이번에 채택한 `treasury_share` 최적화의 trade-off도 분리해서 봐야 합니다.
- direct compare에서 negative speedup outlier `9 / 60`이 있었음
- 하지만 이 비교는 현재 구현을 먼저 실행하고 legacy를 나중에 실행하는 same-process 비교라 문서/검색 cache warm 영향을 강하게 받음
- 실제 fresh baseline before/after에서는 median, mean, p95, max가 모두 개선됨
- 따라서 이 outlier는 semantic drift가 아니라 cache-sensitive path variance로 해석하는 것이 맞음
- equality는 `60 / 60`으로 유지됐고 status drift도 없었음

## 최종 결정

채택:
- `shareholder_meeting` request-local soup 재사용 구현 및 유지
- `company` NAVER 제거 + KSIC 로컬 매핑 유지
- `dividend` agenda-title helper 기반 감액배당 cross-link 경량화
- `value_up` treasury signal helper 기반 `treasury_cross_ref` 경량화
- `treasury_share` execution-report title scan 재사용

표본 보강 완료:
- `company`, `dividend`, `treasury_share`는 기존 겹침 없는 추가 `70개` 표본까지 확인했다.
- 따라서 현재 문서 기준으로는 `shareholder_meeting`, `value_up`뿐 아니라 위 3개도 “표본이 너무 얕다”는 상태에서는 벗어났다.

기각:
- `treasury_share` body enrichment 생략

후속 profiling 필요:
- `financial_metrics`와 기타 미측정 tool의 세부 단계 분해 측정

stage profiling 완료:
- `company`
- `dividend`
- `value_up`
- `treasury_share`

## 근거 파일 인덱스

주 문서:
- `wiki/architecture/audits/260510_data_tools_perf_audit.md`

핵심 근거 파일:
- 대표 baseline
  - `wiki/architecture/audits/data/260510_perf_data_tools_audit/representative_baseline.json`
  - `wiki/architecture/audits/data/260510_perf_data_tools_audit/evidence_baseline.json`
- 사전 실험
  - `wiki/architecture/audits/data/260510_perf_data_tools_audit/shareholder_meeting_soup_cache_experiment.json`
  - `wiki/architecture/audits/data/260510_perf_data_tools_audit/shareholder_meeting_soup_cache_60_summary.json`
  - `wiki/architecture/audits/data/260510_perf_data_tools_audit/shareholder_meeting_soup_cache_additional155_and_cumulative215_summary.json`
- production 반영 후 검증
  - `wiki/architecture/audits/data/260510_perf_data_tools_audit/kospi35_shareholder_meeting_prod_cache_verify.json`
  - `wiki/architecture/audits/data/260510_perf_data_tools_audit/kosdaq25_shareholder_meeting_prod_cache_verify.json`
  - `wiki/architecture/audits/data/260510_perf_data_tools_audit/kospi100_additional_shareholder_meeting_prod_cache_verify.json`
  - `wiki/architecture/audits/data/260510_perf_data_tools_audit/kosdaq55_additional_shareholder_meeting_prod_cache_verify.json`
  - `wiki/architecture/audits/data/260510_perf_data_tools_audit/shareholder_meeting_prod_cache_verify_summary.json`
- 260511 후속 profiling / `company` 반영 검증
  - `wiki/architecture/audits/data/260511_perf_company_dividend_valueup_audit/universe_kospi50.csv`
  - `wiki/architecture/audits/data/260511_perf_company_dividend_valueup_audit/universe_kosdaq10.csv`
  - `wiki/architecture/audits/data/260511_perf_company_dividend_valueup_audit/baseline_kospi50_kosdaq10.json`
  - `wiki/architecture/audits/data/260511_perf_company_dividend_valueup_audit/stage_profile_kospi50_kosdaq10.json`
  - `wiki/architecture/audits/data/260511_perf_company_dividend_valueup_audit/dividend_summary_after_agenda_titles_helper.json`
  - `wiki/architecture/audits/data/260511_perf_company_dividend_valueup_audit/dividend_summary_agenda_titles_helper_compare.json`
  - `wiki/architecture/audits/data/260511_perf_company_dividend_valueup_audit/value_up_treasury_signal_helper_compare.json`
  - `wiki/architecture/audits/data/260511_perf_company_dividend_valueup_audit/value_up_treasury_signal_helper_compare_additional40.json`
  - `wiki/architecture/audits/data/260511_perf_company_dividend_valueup_audit/value_up_treasury_signal_helper_compare_cumulative100_summary.json`
  - `wiki/architecture/audits/data/260511_perf_company_dividend_valueup_audit/universe_kospi50_additional_nonoverlap.csv`
  - `wiki/architecture/audits/data/260511_perf_company_dividend_valueup_audit/universe_kosdaq20_additional_nonoverlap.csv`
  - `wiki/architecture/audits/data/260511_perf_company_dividend_valueup_audit/additional_nonoverlap_safe_audit_kospi50_kosdaq20.json`
  - `wiki/architecture/audits/data/260511_perf_treasury_share_audit/stage_profile_kospi50_kosdaq10.json`
  - `wiki/architecture/audits/data/260511_perf_treasury_share_audit/stage_profile_kospi50_kosdaq10_after_scan_reuse.json`
  - `wiki/architecture/audits/data/260511_perf_treasury_share_audit/legacy_search_compare_kospi50_kosdaq10_after_notice_fix.json`
- 기각 근거
  - `wiki/architecture/audits/data/260510_perf_data_tools_audit/treasury_skip_body_experiment.json`

## 범위

`wiki/tools/README.md`에 정리된 public data tools를 대상으로, 회귀 없는 속도 개선 여지를 점검하고 실제 반영 여부를 판단한 audit이다.

검토 중 고정한 제약:
- output semantics
- precision and coverage
- source-priority and fallback behavior
- DART rate-limit safety
- real-time behavior

## 구조 맵

public data tool 진입점은 `open_proxy_mcp/tools_v2/*.py`에 있고, 실제 로직은 `open_proxy_mcp/services/*.py`로 위임된다.

주요 tool/service 경로:
- `company` -> `services/company.py`
- `shareholder_meeting_notice`, `shareholder_meeting_results` -> `services/shareholder_meeting.py`
- `ownership_structure` -> `services/ownership_structure.py`
- `financial_metrics` -> `services/financial_metrics.py`
- `corp_gov_report` -> `services/corp_gov_report.py`
- `dividend` -> `services/dividend.py`
- `treasury_share` -> `services/treasury_share.py`
- `value_up` -> `services/value_up.py`
- `corporate_restructuring` -> `services/corporate_restructuring.py`
- `dilutive_issuance` -> `services/dilutive_issuance.py`
- `proxy_contest` -> `services/proxy_contest.py` plus `shareholder_meeting` and `ownership_structure`
- `related_party_transaction` -> `services/related_party_transaction.py`
- `evidence` -> `services/evidence.py`

공통 parser hot path:
- `open_proxy_mcp/tools/parser.py`
- `open_proxy_mcp/services/provisional_financial_statement.py`

## 대표 baseline

대표 baseline 근거 파일:
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/representative_baseline.json`
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/evidence_baseline.json`

대표 실행의 warm-path 측정값:

| Tool | Warm sec | Notes |
| --- | ---: | --- |
| `related_party_transaction_summary` | 0.235 | already lean |
| `ownership_structure_summary` | 0.388 | parallel fetch already present |
| `corp_gov_report_summary` | 0.427 | parallel fetch already present |
| `proxy_contest_summary` | 0.535 | composes other tools |
| `shareholder_meeting_results` | 0.677 | parser-heavy |
| `shareholder_meeting_notice_summary` | 0.816 | parser-heavy |
| `value_up_summary` | 1.227 | multi-source diagnostic path |
| `dividend_summary` | 1.787 | multi-filing aggregation |
| `company` | 4.138 | upstream-bound |
| `treasury_share_summary` | 4.187 | heavy body enrichment |
| `financial_metrics_summary` | ~0.000 | warm cache hit in current implementation |
| `evidence` | 0.0001 | pure metadata transform, no upstream fetch |

관찰:
- `financial_metrics` already has a strong warm-cache path and is not an immediate performance priority.
- `shareholder_meeting_*` is not the slowest tool overall, but it is the clearest low-risk parser hotspot.
- `treasury_share` is one of the most expensive tools and makes many DART calls, but unsafe shortcuts regress payload quality quickly.

## 실험 결과

### 1. shareholder_meeting parser stack의 soup 재사용

Artifact:
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/shareholder_meeting_soup_cache_experiment.json`
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/kospi35_shareholder_meeting_soup_cache.json`
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/kosdaq25_shareholder_meeting_soup_cache.json`
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/shareholder_meeting_soup_cache_60_summary.json`
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/kospi35_shareholder_meeting_soup_cache_rerun.json`
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/kosdaq25_shareholder_meeting_soup_cache_rerun.json`
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/shareholder_meeting_soup_cache_60_rerun_summary.json`
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/kospi100_additional_shareholder_meeting_soup_cache.json`
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/kosdaq55_additional_shareholder_meeting_soup_cache.json`
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/shareholder_meeting_soup_cache_additional155_and_cumulative215_summary.json`

실험 설정:
- temporary monkeypatch to reuse `BeautifulSoup` parse results in
  - `open_proxy_mcp/tools/parser.py`
  - `open_proxy_mcp/services/provisional_financial_statement.py`
- target call: `build_shareholder_meeting_payload("LG화학", scope="summary", year=2026, meeting_type="annual")`

실측 결과:
- baseline avg: `0.7656s`
- experimental avg: `0.0648s`
- speedup: about `11.8x`

회귀 확인:
- payload diff was only `generated_at`
- no semantic drift found in payload body

해석:
- repeated reparsing of identical HTML is a real hotspot
- this is a valid optimization direction
- implementation should be request-local, not a global unbounded cache

확장 표본 검증:
- fixed universe:
  - `KOSPI 35` in `universe_kospi35.csv`
  - `KOSDAQ 25` in `universe_kosdaq25.csv`
- target path: `shareholder_meeting summary` with `year=2026`, `meeting_type="annual"`
- `KOSPI 35` summary:
  - `n_equal = 35 / 35`
  - median speedup `80.4%`
  - mean speedup `72.6%`
  - status mix: `exact 31`, `error 4`
- `KOSDAQ 25` summary:
  - `n_equal = 25 / 25`
  - median speedup `82.0%`
  - mean speedup `75.3%`
  - status mix: `exact 21`, `requires_review 3`, `no_filing 1`
- combined `60-company` summary:
  - `n_equal = 60 / 60`
  - median speedup `81.7%`
  - mean speedup `73.7%`
  - no exception cases

확장 검증 해석:
- the optimization signal is not confined to one issuer
- semantic equality held across exact / requires_review / no_filing / error outcomes
- negative speedup outliers came from already-cheap failure/no_filing paths, not payload drift

Second-pass regression and trade-off check on the same 60-company universe:
- `n_equal = 60 / 60`
- `n_status_changed = 0`
- median speedup `80.9%`
- mean speedup `72.1%`
- only two negative-speedup outliers:
  - `포스코홀딩스` (`error`, `-9.0%`)
  - `이오테크닉스` (`no_filing`, `-73.4%`)

재검증 후 trade-off 결론:
- no evidence of semantic regression
- no evidence of status drift
- the remaining trade-off is only that already-cheap `error` / `no_filing` paths may not benefit and can measure slower in percentage terms

추가 미검수 기업 검증:
- additional sample:
  - `KOSPI 100` from `kospi200` with `start=35`, `limit=100`
  - `KOSDAQ 55` from `kosdaq100` with `start=25`, `limit=55`
- additional `155-company` summary:
  - `n_equal = 155 / 155`
  - `n_status_changed = 0`
  - median speedup `79.9%`
  - mean speedup `76.8%`
  - status mix: `exact 149`, `error 5`, `requires_review 1`
  - negative-speedup outliers: `KCC`, `신영증권` and both were `error`
- cumulative `215-company` summary:
  - `n_equal = 215 / 215`
  - `n_status_changed = 0`
  - median speedup `80.2%`
  - mean speedup `75.9%`

누적 검증 후 해석:
- request-local soup reuse is now validated on a materially broader market sample
- every observed downside case remains confined to already-failing or already-cheap paths
- no company in the 215-company cumulative sample showed semantic drift or status transition

실제 코드 반영 및 반영 후 검증:
- implemented in `open_proxy_mcp/services/shareholder_meeting.py`
- approach: request-local soup cache keyed by `rcept_no + raw HTML`, applied only while notice parser stack runs
- parser decision logic unchanged; only repeated `BeautifulSoup(...)` construction inside one payload build was deduplicated
- verification artifacts:
  - `wiki/architecture/audits/data/260510_perf_data_tools_audit/kospi35_shareholder_meeting_prod_cache_verify.json`
  - `wiki/architecture/audits/data/260510_perf_data_tools_audit/kosdaq25_shareholder_meeting_prod_cache_verify.json`
  - `wiki/architecture/audits/data/260510_perf_data_tools_audit/kospi100_additional_shareholder_meeting_prod_cache_verify.json`
  - `wiki/architecture/audits/data/260510_perf_data_tools_audit/kosdaq55_additional_shareholder_meeting_prod_cache_verify.json`
  - `wiki/architecture/audits/data/260510_perf_data_tools_audit/shareholder_meeting_prod_cache_verify_summary.json`
- production-code cumulative `215-company` summary:
  - `n_equal = 215 / 215`
  - `n_status_changed = 0`
  - median speedup `58.6%`
  - mean speedup `57.8%`
  - status mix: `exact 209`, `no_filing 2`, `requires_review 4`
- 해석:
  - production implementation preserved semantics on the same broad sample used for pre-merge safety gating
  - measured gain is smaller than the monkeypatch experiment because only the safe notice-parser request scope was optimized, not every potential soup construction site
  - this is still a strong P0 merge outcome because it materially reduces latency while holding equality across all verified payloads

### 2. treasury body enrichment 생략 실험

Artifact:
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/treasury_skip_body_experiment.json`

실험 설정:
- temporary no-op for
  - `_enrich_cancelation_with_body`
  - `_enrich_result_reports_with_body`
- target call: `build_treasury_share_payload("삼성전자", scope="summary", lookback_months=24)`

실측 결과:
- baseline avg: `4.4666s`
- experimental avg: `4.2193s`
- speedup: about `5.5%`

회귀 확인:
- payload equality failed
- drift affected 125 fields
- critical drift included zeroed or missing cancelation shares/amounts and missing execution detail fields

해석:
- body enrichment is expensive
- skipping it is not acceptable under current product constraints
- this is a useful proof that the expensive path is semantically necessary, not a merge candidate

## 도구별 정리

- `company`: expensive on both cold and warm runs; likely dominated by upstream work rather than obvious local parser waste. Needs targeted profiling before change.
- `shareholder_meeting_notice` and `shareholder_meeting_results`: strongest proven local optimization surface. Parser reuse is promising and validated.
- `ownership_structure`: already uses concurrent upstream fetches; no obvious safe win found from static review.
- `financial_metrics`: current warm-cache path is already excellent; avoid churn unless a correctness-safe cold-path optimization is demonstrated.
- `corp_gov_report`: already parallelized; no immediate low-risk candidate found.
- `dividend`: materially slower than most mid-tier tools, but no regression-safe improvement was validated in this pass.
- `treasury_share`: expensive and DART-call-heavy, but naive speedups break semantics quickly.
- `value_up`: moderate cost with multi-source diagnostics; no validated low-risk change yet.
- `corporate_restructuring` and `dilutive_issuance`: representative sample was `no_filing`; not enough evidence here to justify optimization work.
- `proxy_contest`: piggybacks on other tools; earlier soup-cache testing showed negligible benefit here, so optimize dependencies first.
- `related_party_transaction`: already lean on summary path; no action recommended.
- `evidence`: effectively free already; no optimization work justified.

## 공통 발견 사항

- The codebase already uses `asyncio.gather` in several high-value places. Broad "just add concurrency" advice is not supported by this audit.
- The clearest remaining low-risk opportunity is duplicate HTML parsing, not missing parallelism.
- Expensive fallback/body-enrichment paths often carry real semantics. Removing them may improve speed while violating product guarantees.

## 우선순위 권고

### P0

- `shareholder_meeting` request-local soup reuse is already implemented and verified.
- cache lifetime is scoped to one top-level payload build.
- validation was done by comparing payloads while ignoring `generated_at` and `usage`.

### P1

- Profile `company` and `dividend` with per-stage timers before changing behavior.
- Profile `treasury_share` more finely to separate network cost from body-parse cost; only pursue optimizations that preserve enriched fields.

### P2

- Do not merge body-enrichment skipping in `treasury_share`.
- Do not spend time on `financial_metrics` warm-path optimization; current cache behavior already removes most latency there.

## 머지 가이드

Recommended merge candidate:
- request-local parsed-soup reuse in the shareholder-meeting parser stack
- status: implemented and production-verified

Do not merge:
- treasury body-enrichment skipping

Additional evidence needed before implementation:
- `company`
- `dividend`
- `value_up`
- `treasury_share` beyond the rejected skip-body shortcut

## 검증 메모

근거는 `uv run`으로 project env를 로드한 뒤 직접 실행해 수집했다.

Artifacts:
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/representative_baseline.json`
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/evidence_baseline.json`
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/shareholder_meeting_soup_cache_experiment.json`
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/kospi35_shareholder_meeting_soup_cache.json`
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/kosdaq25_shareholder_meeting_soup_cache.json`
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/shareholder_meeting_soup_cache_60_summary.json`
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/kospi35_shareholder_meeting_soup_cache_rerun.json`
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/kosdaq25_shareholder_meeting_soup_cache_rerun.json`
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/shareholder_meeting_soup_cache_60_rerun_summary.json`
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/kospi100_additional_shareholder_meeting_soup_cache.json`
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/kosdaq55_additional_shareholder_meeting_soup_cache.json`
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/shareholder_meeting_soup_cache_additional155_and_cumulative215_summary.json`
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/kospi35_shareholder_meeting_prod_cache_verify.json`
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/kosdaq25_shareholder_meeting_prod_cache_verify.json`
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/kospi100_additional_shareholder_meeting_prod_cache_verify.json`
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/kosdaq55_additional_shareholder_meeting_prod_cache_verify.json`
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/shareholder_meeting_prod_cache_verify_summary.json`
- `wiki/architecture/audits/data/260510_perf_data_tools_audit/treasury_skip_body_experiment.json`

반복 실험용 스크립트:
- `scripts/perf_data_tools_audit.py`
- `scripts/perf_candidate_universe_audit.py`

알려진 한계:
- the helper script was not run end-to-end for all cases in one shot because the full sweep was too slow for a single pass; representative and experimental artifacts above were generated with narrower direct runs instead.

## 완료 체크리스트

목표 대비 근거 매핑:

- Structure mapping completed: see `Structure Map` and the service-entrypoint mapping above.
- Current-code execution completed: representative timings in `representative_baseline.json` plus direct `evidence_baseline.json`.
- Improvement opportunities identified: duplicate HTML reparsing, treasury body enrichment cost, plus tool-by-tool notes.
- Temporary experimental variants created: soup reuse experiment and treasury enrichment skip experiment.
- Baseline vs experimental benchmarking completed: both experiment artifacts include measured baseline and experimental timings, and the shareholder-meeting candidate was widened to a 60-company universe before implementation.
- Production implementation and verification completed: `shareholder_meeting` request-local soup reuse was merged and rechecked on the cumulative `215-company` sample.
- Speed gain and regression drift recorded: shareholder-meeting equality held pre-merge and post-merge; treasury 125-field drift recorded above.
- Trade-offs assessed: see `Experimental Findings`, `Cross-Cutting Findings`, and `Prioritized Recommendations`.
- Prioritized merge recommendations delivered: see `P0`, `P1`, `P2`, plus `Merge Guidance`.
- Output semantics / source priority / fallback / rate-limit safety preserved in recommended path: only request-local parser reuse is recommended; the semantically unsafe treasury shortcut is explicitly rejected.
- Tool-by-tool findings included for all public data tools in scope: `company`, `shareholder_meeting_notice`, `shareholder_meeting_results`, `ownership_structure`, `financial_metrics`, `corp_gov_report`, `dividend`, `treasury_share`, `value_up`, `corporate_restructuring`, `dilutive_issuance`, `proxy_contest`, `related_party_transaction`, `evidence`.
