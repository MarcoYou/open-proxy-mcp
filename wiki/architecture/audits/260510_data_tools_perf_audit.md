# 260510 데이터 도구 성능 audit

## 최종 요약

이번 audit에서 실제로 반영된 성능 개선은 `shareholder_meeting` 계열의 request-local soup 재사용 1건입니다. 한 요청 안에서 같은 공고 HTML을 여러 번 `BeautifulSoup`로 다시 만들던 경로를, 같은 `rcept_no` 문서에 한해 한 번만 파싱하고 재사용하도록 바꿨습니다.

최종 검증 결과는 다음과 같습니다.
- production 코드 기준 `215 / 215` payload equality 유지
- status 변경 `0건`
- median speedup `58.6%`
- mean speedup `57.8%`
- 최대 speedup `88.8%`
- 최소 speedup `-4.0%`

이번 audit에서 채택하지 않은 개선안도 분명합니다.
- `treasury_share`의 body enrichment 생략은 속도 이득이 작고 semantic drift가 커서 기각
- `company`, `dividend`, `value_up`은 병목 후보는 보였지만 이번 턴에서는 안전한 구현안까지 확정하지 못함

한 줄 결론:
- 이번 성능 개선은 `shareholder_meeting` 1건만 실제 반영했고, 넓은 표본 검증 기준에서 merge-safe로 판단했습니다.

## 무엇을 바꿨나

적용한 변경:
- 대상: `open_proxy_mcp/services/shareholder_meeting.py`
- 변경 전: 같은 공고 HTML을 같은 요청 안에서 여러 번 다시 `BeautifulSoup` 파싱
- 변경 후: 같은 요청 안에서는 같은 `rcept_no` 문서 soup를 재사용
- 캐시 범위: request-local
- 안전 장치: 전역 캐시 없음, 파싱 결과 dict 공유 없음, raw document/soup 재사용만 허용

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

## 최종 결정

채택:
- `shareholder_meeting` request-local soup 재사용 구현 및 유지

기각:
- `treasury_share` body enrichment 생략

후속 profiling 필요:
- `company`
- `dividend`
- `value_up`
- `treasury_share`의 안전한 세부 단계 분해 측정

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
