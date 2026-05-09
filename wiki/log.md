---
type: log
title: Operation Log
---

## [2026-05-10] feat | proxy_advise B1/B2 raw 첨부 + decision 시각 강조 + 운용사·NPS 익명화 4 Phase

**LLM misread 방지 (LG화학 사례 trigger)**:
- 사용자가 LG화학 호출 시 LLM이 proxy_advise FOR 응답 무시하고 안건명("집중투표 배제")만 보고 자체적으로 AGAINST 추측 → 잘못된 분석 제공
- 원인: decision 시각 강조 부족 + reason truncate (80자) → 법령 [A1-1] tag 잘림

**fix 1 (a87b0ab) — decision 시각 강조**:
- ✓ FOR / ✗ AGAINST / ? REVIEW → ✅ / ❌ / ⚠️
- 법령 layer marker: A1/A2 → 🛡️ 강행규정 정합/위반, B1/B2 → 🔍 우회 의심
- reason truncate 80 → 250자
- LLM 경고 박스: "법령 layer 태그는 강행규정 — LLM 자체 판단으로 뒤집지 말 것"

**fix 2 (312d731) — B1/B2 raw 첨부**:
- proxy_advise에 aoi_change scope 추가 호출 (cache hit으로 free)
- B1/B2 hit 안건 reason에 정관 변경 본문 raw `[clause 변경 전/후]` 첨부
- A1/A2는 결정 강제 유지, raw 추가 X (토큰 절약)
- _find_amendment_for_title: label/clause + reason/before/after fuzzy 매칭 (≥2 키워드)
- detail 섹션에 reason full 노출 (표는 250자)

**검증 (5 회사)**:
- LG화학: A1/A2 4건 / B1-10 1건 (raw ✓ — 분리선출 1명→2명 본문)
- KT&G: A1/A2 2건 / B1-8b 1건 (raw ✓)
- 현대차: A1/A2 2건 / B1 1건 (raw ✓)
- Latency: 5-17s — 이전 대비 +1-2% (cache hit)
- regression 0

**운용사·NPS 익명화 4 Phase (보안 sweep, 9 commits)**:
- Phase 1 (d015bfa): tool description vote_style 옵션 list 제거 + README 표 제거
- Phase 2 (fdef0d1): `_VOTE_STYLE_POLICY_FILE` 실명 alias 9개 제거 (익명만)
- Phase 3a (2d4e8ed): wiki/ + docs/ 124 파일 batch 익명화
- Phase 3b (d882f87): open_proxy_mcp/ 27 파일
- 추가 (2a81b8a): sa_active → sa_legacy (실제 운용 스타일)
- 추가 (d5fb8b2 + 1c9dae7): ISS/BAMK 일반화 + 외부 advisor 항목 제거 (b_foreign에 흡수)

**최종 익명 catalog**: m_legacy / s_legacy / sa_legacy / k_legacy / t_activist / a_activist / c_activist / b_foreign / n_pension (9개) — manager_aliases.json (gitignored, local) v4.

**검증 sweep**: git tracked (raw 제외) 운용사 실명 / ISS / BAMK / iss_overseas 잔존 0.

## [2026-05-09] perf | financial_metrics yoy scope 병렬화 (3배 단축)

**효율성 audit (Explore agent)**:
- 37 파일 (services 21 + tools_v2 16) 중복 호출 / 비효율 패턴 점검
- 6 발견 사항 (Sequential 1 / 중복 fetch 3 / parsing 중복 1 / scope 중복 1)
- cache 인프라 견고 (memory LRU 200 + sqlite 24h + disk) → 실제 API 추가 제한적

**진행한 fix (#1만)** — 다른 fix는 trade-off로 skip:
- `services/financial_metrics.py:1206-1218` yoy scope:
  - sequential `await x; await y; await z` (3-9초)
  - → `asyncio.gather(curr, prev, audit)` 병렬 (1.0~1.4초)
- 검증: LG화학/셀트리온/NAVER cold start 모두 1초대 / status=exact / curr+prev / alerts 정상
- regression 위험 0 (read-only API + 독립 인자 + tuple unpacking만)

**Skip 결정**:
- #2 audit_opinion module cache: stale risk (사업보고서 신규 발행 시 옛 데이터)
- #3 block_holders 인자 전달: 효과 작음 (fallback 발동 빈도 낮음)
- #4 retirement parsing 통합: 호출 흐름 변경 → 다른 회귀 가능

**효과**: 100 회사 배치 시 yoy scope 3-7분 단축.

**commit**: 521b64b

## [2026-05-09] docs | wiki 트리 정책 명문화 + lint hook + CLAUDE.md 정리

**Wiki 그래프 audit (260509_wiki_graph_audit)**:
- 252 페이지 × 1261 edges 분석
- Orphan 26 (10.3%) / Weak 35 (13.9%) / Leaf 58 / Unresolved 57
- rules/concepts hub 강건 (자사주 27 incoming) / 시점 페이지 외부 link 빈약 / decisions 명명 혼재

**트리 metaphor 명문화 (WIKI_SCHEMA Section 0)**:
- 🌱 뿌리 raw → 🪵 줄기 rules → 🌿 큰가지 (decisions/arch/tools) → 🌾 잔가지 (ralph/audits/fixes/lessons) → 🍂 낙엽 archive
- Link 정책: 뿌리→줄기→큰가지 단방향 / 큰가지↔잔가지 양방향 / 잎↔잎 자유
- 시점 작업 4축 표준 (ralph ↔ audit ↔ lesson ↔ decision)

**ABCDE 정리 작업**:
- A. rules → 큰가지 link 34건 제거 (단방향 정책 적용, 52 페이지 정리)
- B. 큰가지 ↔ 가지 양방향 보강 (30 페이지, 단방향만 → 양방향 22쌍 추가)
  - tools↔audit: 0 → 22 양방향 / decision↔ralph: 1 → 7 / audit↔lesson: 0 → 3
  - 첫 시도 본문 손실 → revert 후 안전 검증선 95% 추가하여 재실행
- C. scripts/wiki_lint.py 신규 + .github/workflows/wiki-lint.yml CI 통합
  - 단방향 위반 + 양방향 결손 자동 검출 (--strict mode CI 차단)
- D. orphan 17 정리 (24 → 7) — ralph/README + audits/README + audits/data/README 신규
- E. CLAUDE.md 정리 + 구 *_RULE.md 7개 archive 이동
  - 7+1 카테고리 / 트리 흐름 / 시점 4축 / MCP 호출 우선
  - tools_v2 17 → 16 (실제), open_proxy_mcp/*_RULE.md → wiki/archive/tools/legacy_rules/
  - 124 → 109 lines 가벼움화 (-15, -12%)

**DART-OpenAPI 검증 + DS003 섹션 추가**:
- archive 검증: wikilink resolve 7/7 ✓ / 기본 내용 정확
- 13 API 누락 점검: 10개는 data-collection.md DS005에 있음 ✓
- 누락 3개 추가 (DS003 — financial_metrics): get_audit_opinion / get_fnltt_singl_acnt / get_fnltt_singl_indx

**최종 상태**:
- Wiki 페이지: 252 → 264 (+12 README 등)
- 총 edges: 1261 → 1558 (+297)
- 단방향 위반: 34 → 0 / 양방향 결손: 44 → 0
- lint --strict 통과 ✓

**artifacts** (10 commits):
- `wiki/architecture/audits/260509_wiki_graph_audit.md`
- `wiki/WIKI_SCHEMA.md` (Section 0 트리 정책)
- `scripts/wiki_lint.py`
- `.github/workflows/wiki-lint.yml`
- `wiki/ralph/README.md` / `wiki/architecture/audits/README.md` / `wiki/architecture/audits/data/README.md`
- `wiki/archive/tools/legacy_rules/README.md` (구 *_RULE.md 7개 + 흡수 매핑)
- CLAUDE.md / wiki/architecture/data-collection.md update

## [2026-05-08] audit | 파서 정밀화 검증 — 보강 불필요 (Ralph 5)
- ralph: `wiki/ralph/260508_0207_ralph_parser-precision.md` (1+4 iter / promise 발행)
- 발견 (parser audit follow-up):
  - parse_personnel_xml careerDetails 누락 가설 부정확 (44회사/225후보 0% 누락)
  - parse_aoi_xml amendments 누락 1.66% (KOSPI 200 / 3건 모두 source 한계 — 별첨 PDF)
  - 두 파서 강화 ralph 불필요 결론
- audit 자체 정확성 issue:
  - parser audit (260508_parser_audit)는 코드 정적 분석 + TO_DO 정보 기반
  - TO_DO 정보가 stale (옛 batch v7b 시점) → audit 결론 부정확
  - audit는 가설, ralph가 실측 검증 — 두 단계 분리 패턴 재확인
- 다음 후보 재정렬:
  - 🟡 _law_layer 룰 슬림화 + amendments raw 통합 (LLM 판단 영역 명시화)
  - 🟢 PDF fallback (3-tier 2단계) 검증
  - 🟢 _classify_director_tenure logic (5년 룰)
- artifacts:
  - `wiki/lessons/parser-precision-260508.md`
  - `wiki/architecture/audits/260508_parser_audit.md` (실측 결과 추가)
- code 변경 X

## [2026-05-08] feat | 법령 layer 정밀화 — B1-4 분기 + B1-8b 신규 + B1-7 보강 (Ralph 4)
- ralph: `wiki/ralph/260508_0500_ralph_law-layer-precision.md` (6 iter / promise 발행)
- 발견 (Ralph 3 follow-up):
  - B1-4 false positive (정관변경 vs director_election 의미 혼선)
  - KT&G 2025 사전 우회 사례 미발견 (안건 title 일반 표현 — 본문에만 "별개의 조" 키워드)
  - B1-7 패턴 협소 (하이브 "정원 상한 축소" 미스 — "정수"만 매치)
- 룰 변경 (36 → 38):
  - B1-4 분기: parent_must_contain=["정관"] 추가 (정관변경 한정)
  - B1-4b 신규: parent_excludes=["정관"] + 후보 임기 1년 reason
  - B1-8b 신규: applies_after=2024-01-01 + 자산 2조+ + 정관변경 이사 선임/정원 변경 catch
  - B1-7 보강: "정원" + "상한" 키워드 추가
- `_agenda_pattern_match()`: parent_must_contain / parent_excludes 패턴 키 신규 지원
- 광범위 검증 (266 unique 회사 / 2792 안건 / 213 hits / 7.6%):
  - KOSPI 200: 9.8% / KOSDAQ 100: 1.8% / 분쟁 20: 11.6%
  - false positive 0 / 회귀 0%
  - B1-4b 8건 폭발 (영풍 6 + 현대엘리베이터 + 효성티앤씨) — 분쟁 시그널 효과
- artifacts:
  - `wiki/rules/laws/law_layer_rules.json` (38 룰)
  - `wiki/lessons/law-layer-precision-260508.md`
  - `wiki/decisions/260508_0700_decision_law-layer-precision.md`
  - `wiki/architecture/audits/data/260508_law_layer/iter08_*.json` (KOSPI 130-200 / KOSDAQ 0-100 / 분쟁 20)

## [2026-05-08] feat | 법령 layer 도입 — 1·2·3차 상법 개정 + 36 catalog (Ralph 3)
- ralph: `wiki/ralph/260508_0130_ralph_law-layer.md` (7 iter / promise 발행)
- 발견 (코붕이 review): LG화학 정관 sub 안건 잘못 분류 (운용사 정책 stale + hardcoded 키워드 stale)
- 1·2·3차 상법 개정 web 검증 (김·장/신·김/지평/태평양/율촌/Deloitte/삼일/FNguide)
  - 1차 (2025-07-22): 이사 충실의무 + 독립이사 + 3% 룰 + 전자주총
  - 2차 (2025-09-09): 자산 2조+ 집중투표 의무화 + 분리선출 2명 이상
  - 3차 (2026-02-25): 자사주 의무소각 + 합병/분할 신주 배정 금지
- 36 catalog (코붕이 정밀화):
  - A1 (FOR) 8 — 법 정합
  - A2 (AGAINST) 5 — 법 위반
  - B1·B2 (REVIEW) 19 — 법 테두리 안 우회 의심
  - C (risk_factors) 4 — ownership 신호
- 핵심 원칙: AGAINST는 명백한 법 위반만, REVIEW는 법 테두리 안 모든 의심 (B1·B2 둘 다 REVIEW)
- 구조: `Layer 1 법령 → Layer 2 vote_style → Layer 3 hardcoded` 우선 적용
- 검증:
  - LG화학 5/5 핵심 안건 [법령 X-Y] tag 정확 분류
  - 자산 2조+ 30 회사 spot 39 hits (A1-5 11 / A1-1 10 / A1-7 7 / A1-4 5 / A1-2 3 / B1-10 3)
  - 새 패턴 발견 X
- 운용사 7→8 표기 통일 (open_proxy_v1.json + open-proxy-guideline.md + wiki/index.md 등)
- OPM 4 기준 → 5 기준 (5번째 = 법령 layer 우선 + 의무·우회 분기)
- artifacts:
  - `wiki/rules/laws/상법-2025-2026-종합.md`
  - `wiki/rules/laws/상법-2025-2026-종합.md`
  - `wiki/rules/laws/law_layer_rules.json` (머신리더블 36 룰)
  - `services/proxy_advise.py` `_law_layer()` 추가
  - `scripts/spot_law_layer.py` 회귀 spot
  - `wiki/architecture/audits/data/260508_law_layer/iter05_kospi_top30.json`
- decision: [[260508_0200_decision_law-layer]]
- lesson: [[lessons/law-layer-260508]]

## [2026-05-08] audit | high-impact 분류기 audit 결과 (fix 불필요 확정)
- ralph: `wiki/ralph/260508_0030_ralph_classify-high-impact.md` (3 iter / promise 발행)
- 대상: `_classify_value_up_item` (value_up) / `_is_company_side` / `_is_retail_activism_side` (proxy_contest filer)
- 300 회사 sample (KOSPI 200 + KOSDAQ 100) 통합 audit
- value_up: 127 items / 19 unique 패턴 / **mismatch 0** — 견고
- filer 3-way: 255 filings / **99.22% 정확도** — 견고
  - mismatch 2건은 filer 분류기 이슈 X — 회사 resolver 모호 매칭 (셀트리온제약 → 셀트리온 잘못 해석)
- meta-lesson: audit script 측 버그 주의 (universe csv 약칭 vs DART 정식명 차이)
- 분류기 코드 변경 0 (견고 확인)
- lesson: [[lessons/classify-high-impact-260508]]

## [2026-05-08] fix | _classify_agenda 정관 sub-안건 분류 (mismatch 19.3% → 0%)
- ralph: `wiki/ralph/260507_2330_ralph_classify-agenda-fix.md` (4 iter / promise 발행)
- 발견 (코붕이 review): 롯데케미칼 proxy_advise 정관 sub-안건 NO_DATA
- 300 회사 audit (KOSPI 200 + KOSDAQ 100): mismatch 607/3145 = 19.3% — **모두 정관 sub-안건이 다양한 카테고리로 잘못 분류** (other/director_election/audit_committee_election/treasury_share/retirement_pay/cash_dividend/director_compensation/merger/shareholder_proposal/financial_statements)
- fix: `_classify_agenda(title, parent_title='')` 시그니처 추가 + parent에 정관 키워드 있으면 sub 안건 short-circuit articles_amendment
- caller (`proxy_advise._run`): agenda tree 순회로 title→parent map 추출 + 전달
- post-fix 검증 (300 회사 재 audit): mismatch 0.00% / 정관 sub 정확도 100% (607/607)
- 롯데케미칼 회귀: NO_DATA 2건 → 0건
- decision: [[260508_0030_decision_classify-agenda-parent-shortcircuit]]
- lesson: [[lessons/agenda-classification-260507]]

## [2026-05-07] perf | OPM 응답 속도 다수 단축 (10s → 4-6s 체감)
- 코붕이 review: "옛날엔 잘 됐는데 왜 지금 10초?" 분석 흐름
- fix: `auto_stop_machines = 'suspend'` (fly.toml, 04-13 자동 stop으로 덮어쓰여 cold start 5-15s 발생)
- perf: shareholder_meeting candidate doc fetch TOP_N=2 + fallback (정정공시 누적 시 5-8 doc → 2 doc)
- perf: 주총결과 KIND scraping → DART API 우선 (4-5s → 1.4-2s, ~3배 빠름)
- perf: search_filings에 `last_reprt_at='Y'` 옵션 (정정공시 자동 정리, summary 0.2-0.5s 단축)
- perf: ownership_structure 변동신고서 KIND → DART API (3.7s → 0.12s, 30배 빠름)
- perf: doc cache LRU 30→200 + TTL 24h (메모리 only, 영구 저장 X 원칙 유지)
- perf: tool description trim (-25%, 11,170 → 8,408 chars)
- perf: notice tool path에서 `_find_meeting_result_filing` 완전 제거 (auto 모드에서도 skip, fly logs 5초 gap 제거)
- perf: DartClient persistent httpx AsyncClient (16개 `async with httpx.AsyncClient()` → `self._http`, TLS handshake 200-400ms × N 절약)
- decision: [[260507_2330_decision_httpx-connection-pool]]

## [2026-05-06] fix | parser omnibus 검증 + DART 6컬럼 sub-column 처리 (PFS 100%)
- ralph: `wiki/ralph/260505_2330_ralph_parser-omnibus-perf.md` (9 iter / promise 발행)
- 300 회사 (KOSPI 200 + KOSDAQ 100) 통합 audit — Tier A 9 parser G1 ≥98.7% 모두 충족
- **핵심 발견**: DART 잠정 재무제표 html 6컬럼 row 패턴 — `account/note/empty/current/empty/prior`
  - 기존 `_build_column_meta` 가 `_period_by_num` 다음 colspan 확장 빈 셀을 "unknown"으로 분류 → row[2]/row[4] (empty)을 current/prior로 인식하여 모든 metric empty 추출
  - 19개 KOSPI 회사 (현대차/셀트리온/두산/기업은행/LG/KT 등) sparse 원인
  - 코붕이 피드백 "데이터 없는건지 잘못 검색한건지 별도 파서 필요인지 창의적으로 다시 생각" → raw html 직접 search → 매출액 186,254,472 명확 존재 → parser 버그 확인
- fix: `services/provisional_financial_statement.py`
  - `_build_column_meta`에 `_period_by_num_sub` 처리 (empty 셀이 `_period_by_num` 다음에 오면 sub-column 로 propagate)
  - `_METRIC_KEYWORDS.net_income_krw`에 `지배기업소유주지분` 등 4 변형 추가 (보조)
  - `_NON_FS_TABLE_HINTS` 추가 (영문 사명 ≥6 줄 — 종속회사 목록 reject)
  - `extract_metrics` `scope_used` 보고 버그 fix
- 19 sparse 100% PASS / 회귀 90 회사 PFS 100% / 최종 phase1 (n=357 OK) all G1 ≥98.7%
- v1 dead parser archive 결정 (logical only — code 보존)
- G4 layer 정합 검증 PASS — data tool 14 services + Tier A 9 parser decision 키워드 0건 / proxy_advise 8 `_decide_*` action layer
- 17 tool scope inventory — 추가 폐지/신설 결정 없음
- artifacts: `scripts/spot_parser_omnibus.py` / `scripts/spot_pfs_html_search.py` / `scripts/spot_pfs_sparse_recheck.py` / `scripts/agg_parser_omnibus.py` / `wiki/architecture/audits/data/260505_parser_omnibus/`
- lesson: [[lessons/parser-omnibus-260506]]
- decision: [[decisions/260506_2330_decision_v1-dead-parsers-archive]]

## [2026-05-06] feat | shareholder_meeting_notice scope 정리 + provisional_financial_statement 독립
- `shareholder_meeting_notice` scope: 6 → 5 (`summary`/`board`/`compensation`/`aoi_change`/`prov_financials`)
  - 폐지: `agenda` (summary 흡수, hierarchy 통합) + `full` (병렬 wrapper, 종합 분석은 proxy_advise)
  - silent fallback to summary (caller 깨짐 방지)
- `summary` 강화: agenda hierarchy + 1호 안건 메타 (회기/사업연도/배당 예정액) regex 추출
- `aoi_change` 보강: parse_retirement_pay_xml raw 통합 (data tool 원칙 — 판단 X)
- `prov_financials` 신설: 잠정 재무제표 4 quadrant raw (consolidated/separate × balance/income) + flat metrics
- result_status / result_reference 제거 (사후 정보, 시점 분리 위반)
- `services/provisional_financial_statement.py` 신규 (독립 모듈):
  - `parse_financials_xml` 본체 + 의존 helper들 통째로 이동 (parser.py 의존성 제거)
  - `parse_provisional_financial_statement(html)` + `extract_metrics(parsed)`
  - data/action tool layer 분리 정합 — data tool은 raw 노출, action tool (proxy_advise)은 extract_metrics로 facts evidence
- 구 `services/agm_first_agenda_fy.py` (정규식 텍스트 파서) archive
- universe csv 신규 (`260506_universe_kospi_200.csv`, `260506_universe_kosdaq_100.csv`, `260506_universe_kosdaq_150.csv`, `260506_universe_kosdaq_300.csv`, `260506_universe.xlsx`)
  - source: `esgQuant/.../멀티인덱스_dataguide.xlsx` (시총 내림차순, KOSPI 810 + KOSDAQ 1816 식별 가능)
- 검증 (삼성전자 2026 AGM): prov_financials 12 metric 정확 (매출 333.6조 등) / summary hierarchy + 1호 메타 + 정정공시 detect / aoi_change 정관 4건
- decision: [[260506_0030_decision_notice-scope-cleanup-prov-financials]]

## [2026-05-05] feat | 보수한도 / 퇴직금 분기 정밀화 (G1 99%+ / 5 AGAINST detect)
- 보수/퇴직 분기 wire 후속 검증 + parser 강화 + financial_metrics fetch chain fix
- 1차 ralph (260505_1750): 카테고리 분리 + hybrid wire — promise 미발행 (G1 retirement 40%)
- 2차 ralph (260505_2030): KOSPI 200 + KOSDAQ 50 (n=226) 확장 + parser fallback — promise 미발행 (G1 retirement 78.6%)
- 3차 ralph (260505_2200): 정밀화 — promise 발행 ✓
  - parser 강화 (commit `782af95`): anchor 검출 + 표 head 키워드 확장 (현재/개정(안)/개정전후) + 표 본문 "퇴직" broad-match
  - financial_metrics summary scope에 prev_net_income/yoy_pct 노출 (commit `8fe8bff`) — 흑자+yoy<0 trigger 활성화
  - 소진율 단독 강화 (commit `db44182`) — 소진율<30 + 인상률 미파악/동결 → REVIEW
  - 5 batch 재측정 (KOSPI 0-30 / 30-50 / 50-80 / 140-170 / KOSDAQ 0-30) — NEW parser 적용
- 최종 G1-G4 (n=226):
  - G1 파싱 성공률: director 99.2 / audit 100 / retirement **100** (이전 78.6) ✓
  - G2 trigger 정확도 100%: AGAINST 5건 — 피에스케이/피에스케이홀딩스/GST 지급률 2배수+ / 카카오페이 사외이사 퇴직금 (OPM #6) / 퓨쳐메디신 자본잠식+인상
  - G3 운용사 4+ majority 정합 100% (director 11/11, audit 1/1)
  - G4 N연기금 정책 정합 100% (N연기금 [별표 1] IV-33/34/35 + OPM Open Proxy v1.3 #6/#7/#8 trigger 일치)
- KT&G false positive 수정: 이전 REVIEW (퇴직연금 키워드 hit) → FOR (퇴직연금 제도 도입 형식적 변경)
- ralph: [[260505_1750_ralph_compensation-retirement-split]] / [[260505_2030_ralph_compensation-retirement-extend]] / [[260505_2200_ralph_compensation-retirement-precision]]
- decision: [[260505_1900_decision_compensation-retirement-split]]
- audit: `wiki/architecture/audits/data/260505_compensation_retirement_*` (3개 폴더)

## [2026-05-05] feat | 보수한도 / 퇴직금 안건 분리 (이사·감사 + 정관 hybrid)
- 발단: 코붕이 (이사·감사 보수한도 + 퇴직금이 어떻게 처리되는지 확인) → 갭 발견:
  1. 퇴직금이 `_decide_compensation` 같이 처리 → 인상률 데이터 없으니 fm_fallback FOR (사실상 자동 FOR, status quo bias)
  2. 이사/감사 분리 안 됨 (parser는 분리하나 결정은 합산)
- 해결:
  1. **카테고리 3 분리**: `director_compensation` (강화) / `audit_compensation` (NEW) / `retirement_pay` (NEW)
  2. **Hybrid wire** (코붕이 의견): 한국 회사 관행상 퇴직금/보수 변경은 대부분 "정관 일부 변경" 형식.
     `_decide_articles_amendment`에 retirement/comp helper 통합 — 같은 helper 재사용, 결정 logic 중복 X.
  3. **결정 분기**: 이사 13 분기 / 감사 11 분기 / 퇴직금 12 분기. 정책 근거 (N연기금 [별표 1] IV-33/34/35 + OPM Open Proxy v1.3 #2/#6/#7/#8 + 운용사 패턴) 모두 wire.
  4. **2 layer 원칙**: 정책 카탈로그 (정성+정량) + 결정 코드 (자동 trigger wire + 정성은 facts raw 노출).
  5. **Step 0 sample**: KOSPI/KOSDAQ 10 회사 spot — SK하이닉스 11 amendments / 고려아연 5 (황금낙하산 sample 0)
  6. **Step 0.5 운용사 majority cache**: 22 records 합산 → director 31 / audit 2 / retirement 1 4+ majority case (모두 FOR). AGAINST outlier: 하이브 (3대1) / 에코프로 (3대0).
- iter02 KOSPI 0-50 baseline (정관 우선 fix 전): director 20 (모두 FOR, g3 정합 100%) / audit 2 (FOR) / retirement 2 (REVIEW — KT&G "퇴직연금 정비")
- iter04 키워드 정밀화: "확정기여형/확정급여형/퇴직연금" 위험 → 형식적 (FOR) — KT&G false positive 회피
- iter03 hybrid batch (KOSPI 50 + KOSDAQ 30): 진행 중. 결과 측정 후 G1-G4 검증 + promise 가능 여부 결정.
- ralph: [[260505_1750_ralph_compensation-retirement-split]]
- decision: [[260505_1900_decision_compensation-retirement-split]]
- audit data: `wiki/architecture/audits/data/260505_compensation_retirement/`

## [2026-05-05] feat | 사내이사 재직 중 성과 매트릭스 (2x3) — status quo bias mitigation
- 발단: 코붕이 고려아연 케이스 비판 — proxy_advise 사내이사 분기는 결격사유만 검증 → 회사 추천 후보 자동 FOR. status quo 무검증.
- 해결: 재직 중 회사 운영 성과 axis 추가. 2x3 매트릭스 (ROE/부채비율/CSR × avg/trend), good +2 / mod +1 / weak 0 / bad -1.
- Special rules: 자본잠식 ROE/leverage avg 자동 bad, 적자+환원 CSR weak (자본잠식 가속), 적자+환원 자제 CSR moderate (보수성).
- decision branch: bad → AGAINST, weak → REVIEW, moderate/good/신임 → FOR. 묶음 안건도 동일.
- 데이터 chain (회사당 +2 호출): `dividend(history, 10y)` + `treasury_share(summary, 120m)` + `financial_metrics(yearly)`.
- threshold tune: ≥9→≥7 (KOSPI 100 baseline 7.7% 너무 보수적, ≥7로 26.4%·target 20-40% 충족).
- 검증 (KOSPI 100 + KOSDAQ 50, n=128):
  - G1 classification 노출률 **100%**
  - G2 적자 16건 모두 special rule 작동, 자본잠식 0건
  - G3 bad→AGAINST (한화오션 김희철, 삼성SDI 오재균), weak→REVIEW (HD현대중공업 금석호) 분기 작동
  - G4 distribution good 29.7 / mod 45.3 / weak 18.0 / bad 7.0 — 모든 target band 충족
- 추가 변경: Korean label 자연화 (weak_concerns → "약한 우려", concerns → "우려" 등 — `_INDEPENDENCE_LABELS` 등 dict)
- ralph: [[260505_1611_ralph_inside-director-performance-matrix]]
- decision: [[260505_1700_decision_inside-director-performance-matrix]]
- lesson: [[distribution-calibrated-thresholds]] (8번째 lesson — 임계값은 prior가 아니라 audit posterior에서 정함)
- audit: `wiki/architecture/audits/data/260505_inside_director_performance/` (KOSPI 4 + KOSDAQ 2 batch JSON)

## [2026-05-05] feat | DART OpenAPI 분당 1000회 hard rule 강제
- `dart/client.py`에 rolling window rate limiter (60s deque + asyncio.Lock), cap **900/min** (10% buffer + race 방지). 모든 `_request` 자동 throttle.
- 발단: treasury ralph 측정 중 KOSPI 100 batch (~1000 호출/min)로 두 차례 24h IP 차단 발생.
- CLAUDE.md "hard rule, 절대 위반 X" 명시 + memory `feedback_dart_openapi_rate_limit.md` 강화.
- 새 batch script: 회사수 × 평균 호출수 estimate, 최대 30 회사 단위 + offset arg + sleep.

## [2026-05-05] feat | treasury ralph iter 13~15 — G2 사이클 매칭 100% 달성
- iter 13 (rate-safe batch): 30 회사 batch + offset, KOSPI 100 G2 adj 98.16%, KOSDAQ 50 79.17% (합 91.40%)
- iter 14 (trust fallback fix):
  - `trust_termination_result` → `trust_contract` (사이클 시작 결정) fallback
  - trust 사이클 out_of_lookback 분류 (er_dt < 가장 오래된 trust_contract decision)
  - 신탁 본문 "체결일자" 라벨 추가 (휴젤 등에서 발견)
  - KOSDAQ 79.17% → 97.32% (+18%p)
- iter 15 (acq/dsp fallback + main_date noise):
  - `_parse_main_report_date` 강화: "주요사항보고서 제출일 : 최초제출일: ..." noise 30자 cover
  - "최초제출일" 라벨 단독 추가 (정정공시)
  - acquisition/disposal result도 단일 결정 fallback
  - KOSPI 100% (220/220), KOSDAQ 100% (112/112), 합산 100% (332/332)
- 모든 gate (G1 본문 파싱 100% + G2 사이클 매칭 100% + G3 phase + G4 scope) 충족
- normalize 보강 (iter10 fix): broker_name `cs_iv_bk`, price_*_krw `dpstk_prc_*`, holding_*_date `hdexpd_*`, before_div/before_other 보유현황 추가, 처분방법 4 field (dp_m_mkt/otc/ovtm/etc)
- audit: [[260505_0530_audit_treasury_execution_iter1-8]] (iter 11~15 추가 update)

## [2026-05-05] refactor | proxy_advise scope 10→1 + dead service archive
- proxy_advise: scope param 폐지, 항상 `decisions` 호출. specialized scope 9개 (agenda/candidates/financial/governance/ownership/policy_basis/proxy_battle/engagement/evidence/all) 폐지.
- 사용자가 raw 보고 싶으면 각 tool 직접 호출 (shareholder_meeting_notice / financial_metrics / corp_gov_report / ownership_structure / proxy_contest / value_up).
- decisions enrichment (facts / risk_factors / policy_citation / 근거 공고 / 후보 raw) 그대로 유지.
- archive (`wiki/archive/services/`): `proxy_guideline.py`, `proxy_guideline_scoring.py`, `policy_comparison.py` — 12 매트릭스 logic은 호출 X (dead). ralph G2 99.36% 검증은 OPM 자체 logic + vote_style JSON으로 도달.
- archive (`wiki/archive/tools/`): `screen_events.md`, `proxy_guideline.md`, `shareholder_meeting.md` (notice + results 분리됨).
- index.md / tools/README.md 16 tool 반영.

## [2026-05-04] feat | treasury_share 결과보고서 4종 ralph (iter 1~10)
- 결정 5종 (decision) + 결과 보고서 4종 (execution) 통합.
- ACODE 기반 본문 파싱 (DART 표준 서식 system field id) — G1 100% 안정성.
- 결정↔결과 사이클 매칭: 본문 "주요사항보고서 제출일" / "신탁계약 체결일" ↔ decision rcept_dt.
- scope 통합 6→2 (summary + annual). phase=decision/execution flag.
- KOSPI 100 audit: G1 100% / G2 adjusted 97.69% (lookback 밖 17건 제외).
- iter 10 normalize 보강: 보통/우선주 별도 + 위탁사 + 사외이사 + 보유예상기간 + 신탁기관 + 해지사유 + 처분상대방.
- 측정 보류 사유: opendart.fss.or.kr API 차단 (24h cool-down) — dart.fss.or.kr 본문은 정상.
- audit: [[260505_0530_audit_treasury_execution_iter1-8]]

## [2026-05-04] feat | proxy_advise framework enrichment ralph
- decisions 응답에 facts (정량 fact dict) + risk_factors + policy_citation + 근거 공고 (rcept_no) 추가.
- 후보 평가 (candidates_evaluations) 4 dimension raw: 결격사유 / 독립성 / 전문성 (main_job + 추천사유) / 과거 행적 (career_company_groups + audit_history_check).
- 신임/연임 auto detect (career_company_groups + main_job fallback).
- 1번 안건 FY 본문 raw 추출 (`agm_first_agenda_fy`).
- KOSPI 100 + KOSDAQ 50 검증: G1 100%, G2 0% FP, G3 99.5% classified, G4 98.6%.
- audit: [[260504_2200_audit_proxy_advise_framework_iter1-8]]

## [2026-05-04] refactor | tools_v2 정리 (17→16 + scope 통합)
- screen_events drop (외부 호출 0).
- proxy_guideline → internal (tools_v2 wrapper 삭제) — 후속 archive로.
- shareholder_meeting → notice (DART) + results (KIND) 두 tool 분리.
- dilutive_issuance / corporate_restructuring scope 단일화.
- ownership_structure 7→5 (treasury 제거 → treasury_share 사용 권장, timeline → blocks 통합).
- dividend CSR/TSR/policy_signals scope 폐지 (6→3).

# Operation Log

## [2026-05-04] fix + audit | parse_personnel ralph 7 iter — role 88.7→100% + regression 0
- iter4 role normalize + title fallback (가장 큰 성공)
  - `_normalize_role_value()` 노이즈 set 분류 + 표준 표기 (사외/사내/감사위원/상근감사 등)
  - alg 알 수 없는 case → raw 보존 (silent fallback X)
  - header 매칭 확장 ('이사구분/직위/구분/직책')
- iter6 period 단일 연도 + content year extract (+0.3%p)
- iter8 한자 이름 cover (`[一-鿿]`)
- 영문 검증 통과: KIM JOONYOUNG / Takashi Abe / Edward Chin 등 정상
- career_period 89.0% (target 95% 미달, 본문 데이터 한계 — parser fix 효과 X)
- batch v8 regression: 4+ majority 99.36% 유지 ✅
- audit: [[260504_0724_audit_parse_personnel_iter1-7]]

## [2026-05-04] feat + audit | proxy_advise rename + 9 scope 추가 — regression 0
- Step 1 rename: services/{advise_vote→proxy_advise, recap_vote→proxy_result} + tools_v2 + 옛 wiki archive (commit 7b06b75)
- Step 3 단순 expose 5 scope (agenda/candidates/financial/governance/ownership) (commit 6711228)
- Step 4a policy_basis — 모범 사례 + 특이 케이스 example 형태 (재설계, commit c937505)
- Step 4b/c/d proxy_battle/engagement/evidence 추가 (commit 543293e)
- Step 4e proxy_result.brief — vote_brief render 흡수 (commit 4a75b87)
- 200×3 batch 결과: exact 492 / error 6 / no_filing 99 — Phase 4와 완전 동일, 일관성 100%, cross-match 197/197 ([[260504_0028_audit_proxy_advise_rename_regression]])

## [2026-05-04] docs | proxy_advise/proxy_result 신규 spec + 검증 ralph
- [[proxy_advise_before_meeting]] (10 scope: decisions/agenda/candidates/financial/governance/ownership/policy_basis/proxy_battle/engagement/evidence)
  - 옛 prepare_engagement_case + build_campaign_brief 사전 부분 흡수
- [[proxy_result_after_meeting]] (2 scope: results/brief)
  - 옛 prepare_vote_brief render 흡수, followup 30일 윈도우 제거 (의도적 단순)
- [[260503_0002_ralph_proxy-advise-verification]] — 3 gate (일관성/정확도/사실정확성) 검증 ralph
- index.md 갱신 (Action 2 tool rename 표기)

## [2026-05-03] fix | 정정공고 4건 items[0] fallback 적용
- `value_up_v2.py:127, 130, 394`, `corp_gov_report.py:386`, `shareholder_meeting.py:395`, `tools/proxy.py:421`
- 표준 패턴: 정정 제외 우선 + 빈 결과 fallback (`(non_corr or items)[0]`)
- multi-upstream-pattern 페이지 4 위치 ✅ 표시 + 표준 코드 스니펫 추가

## [2026-05-03] audit | ownership_structure baseline — 패턴 fix 불필요
- 200×3: 100% 일치, max 1.8s, timeout 0 ([[260503_2345_audit_ownership_baseline]])
- proxy_contest와 동일 결론: DART endpoint 직접 호출은 fix 효과 미미

## [2026-05-03] audit | proxy_contest baseline — 패턴 fix 불필요 결정
- 200×3 baseline (fix 없이): 100% 일치, timeout 0, mean 1.09s ([[260503_2330_audit_proxy_contest_baseline]])
- 적용 판단 기준 정립: build_*_payload 재귀(적용) vs DART endpoint 직접(불필요)
- multi-upstream-pattern 페이지 갱신 (체크리스트 + 기준 추가)

## [2026-05-03] fix | recap_vote multi-upstream-pattern 적용 + 100% 일치 검증
- `services/recap_vote.py` 8 upstream gather에 5 요소 적용 (commit `21bdf58`)
- 200×3 batch: 일치율 100.0% (195/195), timeout 0 ([[260503_2304_audit_recap_pattern]])
- 패턴 일반화 입증 — advise_vote 특수 case가 아닌 OPM 표준

## [2026-05-03] fix + docs | advise_vote Phase 4 100% + multi-upstream 패턴 표준화
- `dart/client.py` `_load_corp_codes`: asyncio.Lock + 3회 retry (1/2/4s) + corpCode timeout 60→120s
- `services/advise_vote.py`: per-call wait_for(60s) + Semaphore(3) + process result cache + 명시 pre-warm
- `services/director_evaluation.py`: notices[0] → 시간 desc 최대 3개 fallback (정정공고 처리)
- 200×3 batch: 91.9% → 100.0%, timeout 15→0, regression 0 ([[260503_1847_audit_phase4_final]])
- 신규 [[architecture/multi-upstream-pattern]] — 5 요소 표준 + 적용 대상 체크리스트
- TO_DO: recap_vote / proxy_contest / ownership_structure 같은 패턴 적용 대상 등록

## [2026-05-02] feat | action tool 재편 (3 → 2, 시점 분리: advise/recap)
### 신규 (3 service + 2 tools_v2)
- `services/director_evaluation.py`: 후보 평가 3축 (독립성/충실성/결격사유) + Marco 시나리오
- `services/advise_vote.py`: 6 upstream 통합 + 안건별 FOR/AGAINST/REVIEW + 결정 사유
- `services/recap_vote.py`: 5 upstream + 후속 공시 30일 + gap 비교 X
- `tools_v2/advise_vote_before_meeting.py`: 운용사 의결권 메모 render
- `tools_v2/recap_vote_after_meeting.py`: 분기 보고서 render

### 제거 / archive
- 제거: `prepare_vote_brief` (advise 흡수), `build_campaign_brief` (advise/recap 분산)
- archive: `prepare_engagement_case` → `_archive/`
- 자동 디스커버리 18 → **17 tool**

### 매핑 분류 (코붕이 명시 지시)
- success: 정형 필드 직접 (안건/후보/지분/재무/감사의견)
- soft-fail: raw text 노출 (careerDetails / dutyPlan / recommendationReason)
- hard-fail: 메모/코드 모두 침묵 (형사/사적관계/동명이인/파산)

### Sanity (7 iteration)
- 정기: 삼성전자 / KT&G / KB금융 (Marco 활성)
- 임시: HMM (정관변경 1 안건)
- Edge: 알지노믹스 (자본잠식 회사)
- 회귀: financial_metrics + dividend 변경 0

### Phase 2 (별도)
- A5 A행동주의 12 회사 backtest
- A6 9 비교군 (8 운용사 + N연기금) backtest
- vote_style 정책 wire + 매트릭스 자동 채점 통합

## [2026-05-01] feat | financial_metrics tool Phase 1 (재무 4 endpoint 통합 신규)
### 신규
- DART client에 4 endpoint 추가: fnlttSinglAcnt + fnlttSinglIndx + fnlttSinglAcntAll + accnutAdtorNmNdAdtOpinion
- `services/financial_metrics.py` (1155 lines): 6 scope (summary/yearly/quarterly/yoy/qoq/audit_opinion), 51 metrics, 22 alerts, normalize_amount (괄호 음수 + 콤마 strip)
- `tools_v2/financial_metrics.py` (328 lines): MCP tool register, format_krw_human (조/억 변환), 6 scope render
- `tools/financial_metrics.md` (wiki tool 페이지, 12 섹션 + Flow mermaid)
- `architecture/audits/260501_1820_audit_financial_metrics-6기업.md` (sanity audit)
- 17 tools → 18 tools 모든 documentation 동기화 (index.md / tools/README.md / README.md / README_ENG.md / CLAUDE.md)

### 검증
- 6 회사 sanity 100% PASS (삼성전자/KT&G/롯데케미칼/SK하이닉스/삼천당제약/오스템임플란트, 모두 status=exact)
- turnaround / operating_loss / continued_loss / receivables_surge / accruals_red 등 핵심 alert 정확 detect
- 기존 17 tool regression 0 (dividend 회귀 검증 통과, register_all_tools_v2 자동 디스커버리 18 모두 등록)

### Phase 2 (별도)
- vote_brief 통합 (재무 risk 신호 → 사외이사 후보 cross-check, Marco 시나리오)
- 매트릭스 dim 자동 채점 (이자보상배율/FCF/cfo_quality wire)
- 응답 시간 최적화 (asyncio.gather 병렬화)

## [2026-05-01] feat | wiki 재구조 (5+1 카테고리 + 명명 규칙) + 17 tools 진입점
### W1: 카테고리 재편 + prefix rename
- 154 파일 이동: `wiki/{old}` -> `wiki/{new}`
- 13 prefix rename (audit/fix/decision/debate/improvement) -> `yymmdd_hhmm_{type}_{title}`
- 1 통합: `architecture/matrix-system.md` (구 decision-matrix-design + matrix-auto-scoring)
- `raw/` 신규 (구 sources + raw 합침, 수정 금지 명시)
- `archive/` 신규 (흡수된 38 페이지 역사 보존)
- 카테고리: raw / tools / architecture / decisions / rules(concepts+disclosures+laws) / archive

### W2: tools/ 17 페이지 + README catalog
- `tools/{17}.md` 일괄 작성 (통일 schema: frontmatter + 12 섹션)
- `tools/README.md` catalog (도메인별 진입표 + 데이터 소스 매트릭스 + archive 매핑)
- 흡수된 archive: 18 analysis 페이지 -> tools/ 본문에 통합

### W3: 사용자 진입점 통합
- `index.md` 재작성: Quick Start 섹션 최상단, 17 tools + 카테고리 테이블 + 자주 쓰는 진입점
- `WIKI_SCHEMA.md` 재작성: 5+1 카테고리 정의 + 명명 규칙 + frontmatter schema (type별) + 신규 페이지 워크플로우
- `CLAUDE.md` 보강: 명명 규칙 명시 + raw 수정 금지 강조 + "처음 [[tools/README]]" 권고
- `README.md` + `README_ENG.md` 이미 17 tool 반영 완료 (W1)

### 통계
- 총 173 markdown + 29 binary
- raw 29 binary + 4 md / tools 17+1 / architecture 6+10 / decisions 14 / rules 31+36+3 / archive 48
- 깨진 link 0건

## [2026-04-29] docs | 배당·자사주 공시 10종 + 2026.03 신법 wiki 정밀 분류
### 신규 disclosures 페이지 (9종)
- **배당 4 신규**: 주식배당결정.md, 배당기준일결정.md, 분기배당결정.md, 감액배당결정.md
- **자사주 5 신규**: 자기주식취득결정.md, 자기주식처분결정.md, 자기주식소각결정.md, 자기주식신탁결정.md (체결+해지 통합), 자기주식의무소각-2026신법.md
### 업데이트 (1종)
- **현금배당결정.md**: 트리/필드 통합, 자회사판 중복 제거 명시, 11개 핵심 데이터 항목 표 추가
### 통합 비교표 (1신규)
- **comparison/배당-자사주-공시-종합.md**: 10종 + 2026.03 신법 종합 (의무/소스/필드/OPM tool/신법 영향/거버넌스 시나리오 4종)
### 핵심 발견
- **2026.03 신법 영향 정량화**: 소각결정 빈도 50건/년 → 200건+ 예상, 자사주 비중 7% → 1-2% 정상화
- **자사주 마법 차단 메커니즘**: `dpptncmp_cmpnm` 채워짐 + 분쟁 중 → against 절대
- **선배당-후결의 (2024 개정) 추적**: 분기마다 [[배당기준일결정]]+[[분기배당결정]] 2종 동시 제출 패턴
- **report_nm 함정**: 자기주식소각결정의 실제 등록명은 "주식소각결정" (자기주식 prefix 없음)
### 인덱스 업데이트
- index.md: Disclosures 섹션 (배당 5 + 자사주 5 신규), Comparison 섹션 (배당-자사주-공시-종합 신규)
### TODO
- treasury_share tool에 `scope=commitment_check` 신규 (1년 시점 자동 알람)
- `screen_events(treasury_pending_cancelation)` 신규 이벤트 타입
- 기존 자사주 보유분 2027.09까지 처리 추적 자동화

## [2026-04-29] feat | proxy_guideline tool + Open Proxy Guideline v1.2 + 12 의사결정 매트릭스
### Phase A: 7 운용사 데이터 파싱
- 정책 5건: opendataloader-pdf (s_legacy·sa_legacy·t_activist·kim·a_activist 1-4초)
- M레거시: vector glyph PDF → PyMuPDF DPI 120 raster + JPEG 70% → Upstage OCR 우회 (35KB md)
- B외국계: B외국계 2026 Voting Guidelines 직접 채택 발견 → `policy_classification: b_foreign_self`
- 행사내역 15 xlsx → 통일 schema JSON (총 17,900 votes)
### Phase B-C: 합의 매트릭스 + Open Proxy Guideline v1.2
- `_consensus_matrix.json`: 7 운용사 79 토픽, consensus + majority 62%
- 7 페르소나 토론 (학자·운용사출신·소수주주활동가·자본시장변호사·상법변호사·글로벌ESG·법안리서처) + 모더레이터 통합
- v1.0 → v1.1 (B외국계 B외국계 + A행동주의 행동주의) → v1.2 (B외국계 reference 다운그레이드)
- v1.2: 12 카테고리 116 룰 + 11 novel topics + 2026 신법 7개 + §382의3 cross-cutting
- 12 의사결정 매트릭스 (100 dim, 76 빙고 패턴) — 운용사·자문사 단독 차별화
### Phase D: proxy_guideline tool (6 scope)
- `services/proxy_guideline.py` + `tools_v2/proxy_guideline.py`
- scopes: policy / record / predict / compare / consensus / audit
- audit가 정책-실제 갭 자동 검출 (s_legacy director_election 4.3% — `policy_strict_practice_lenient`)
- DART API 호출 0회 (정적 데이터, <100ms 응답)
### Phase E: prepare_vote_brief 통합
- `vote_style` 인자 (default `open_proxy`, 7 운용사 선택)
- `_build_proxy_guideline_brief()` — 안건 → 카테고리 분류 → 정책 룰 매핑
- 새 출력 블록 `## OPM 정책 권고`
### 산출물
- `wiki/decisions/`: open-proxy-guideline.md, decision-matrix-design.md, opm-guideline-debate-transcript.md
- `wiki/analysis/voting-policy-consensus-matrix.md`
- `open_proxy_mcp/data/asset_managers/` 14MB JSON
- `.gitignore`: open_proxy_mcp/data/ 예외 + wiki/sources/binary 무시

## [2026-04-24] fix | agenda 파서 boundary 보강 (공공기관·전각ｏ 마커 대응)
- 한국전력공사 임시주총 안건 title이 후보 테이블까지 길게 잡히는 현상 발견 (commit `6fe44d2`)
  - `_AGENDA_BOUNDARY`에 추가: `-\s*후보에\s*관한\s*사항`, `의안\s+후보자\s+임기`
  - 결과: "상임이사 선임의 건 - 후보에 관한 사항 의안 후보자..." → "상임이사 선임의 건"
- 강원랜드 임시주총 title에 "ｏ 후보 최우식" 잔류 발견 (commit `c22aa95`)
  - 원인: 전각 ｏ(U+FF4F) 마커가 boundary 미지원 (반각 ○과 다른 글자)
  - 마커 클래스 `[□◎●○▶·ㆍ]` → `[□◎●○▶·ㆍｏ]` 확장
  - boundary에 `ｏ\s*(?:제\s*\d+|후보)` 추가
- 8개 회사 회귀 테스트 통과: 한전·강원랜드·KT&G·한국가스공사·현대차

## [2026-04-22] feat | prepare_vote_brief에 corp_gov_report 통합 + 세부원칙 파서 수정
### prepare_vote_brief 거버넌스 통합
- `services/vote_brief.py`:
  - `build_corp_gov_report_payload` import
  - asyncio.gather에 `governance_payload` 추가 (shareholder_meeting × 3 + ownership + **governance**)
  - `governance_brief` 블록 신규: 준수율 / 준수·미준수 지표 수 / 미준수 라벨 상위 10개 / 의무여부 / 시장 / 최신 보고서 날짜
  - key_flags 자동 생성: 준수율 60%↓="낮다", 80%↓="보통", 95%↑="우수"
  - 구조적 약점 감지(집중투표/사외이사 의장/독립 내부감사 미준수 → structural 플래그)
  - quality.governance_status + evidence_refs에 governance 건 병합
- `tools_v2/prepare_vote_brief.py`:
  - 렌더러에 `## 거버넌스 (기업지배구조보고서)` 섹션 추가
  - docstring: upstream에 corp_gov_report 명시 / 자동 플래그 규칙 기술
- 검증: KT&G → 준수율 100% / 미준수 0개 / "우수" 자동 플래그 + evidence 삽입

### 세부원칙 파서 수정 (0건 → 6-7건)
- 정규식 문자 클래스에 하이픈·마침표 빠져서 모든 기업 principles=0건이었음
- `\(세부원칙 X-Y\)` 명시 매칭 + DOTALL로 설명 캡처
- 스키마 변경: `principle_snippet` → `principle_number` + `principle_description`
- 검증: 현대차 7건, 삼성 7건, KT&G/SK하이닉스/NAVER 각 6건 (원문 세부원칙 수와 일치)

### 문서
- README / README_ENG: Action Tool 설명에 "거버넌스 준수율 자동 포함" 표기

### 후속 fix (timeout)
- 웹 커넥터에서 prepare_vote_brief 호출 시 일부 회사 실패 (응답 20s+ 누적 → MCP timeout 도달 추정)
- `_safe_governance()` 헬퍼 추가: corp_gov_report fetch에 `asyncio.wait_for(timeout=10)` 래핑 + 실패 시 빈 payload 반환
- 거버넌스 fetch 실패해도 vote brief 자체는 항상 생성됨

## [2026-04-22] fix | corp_gov_report 파서 보강 + timeline scope + 의무화 연도 정정
- **의무화 연도 정정** (사용자 지적 반영, WebSearch 소스 재확인):
  - 잘못된 기재: "2024 사업연도부터 전체 KOSPI 의무"
  - 정정: "2019 자산2조 → 2022 자산1조 → 2024 자산5천억 → **2026년 제출분부터 KOSPI 전체**"
  - 제출 시한 5월말, 연중 [기재정정] 재제출 빈번
  - wiki/disclosures/기업지배구조보고서.md, wiki/analysis/corp_gov_report-design.md, tool docstring, README 모두 정정
- **파서 보강**:
  - v1 문제: 4줄 고정 패턴 가정 → 비고 없는 서식(삼성) 실패
  - v2 해결: 15 표준 지표 라벨 prefix(25자)로 위치 찾고 각 블록에서 O/X 동적 수집
  - 삼성전자 7/15 → **15/15**, SK하이닉스 8/15 → **15/15**
  - 키워드 `"기업지배구조보고서"` → `"기업지배구조보고서공시"` 엄격화
  - `"연차보고서"` 명시 제외 → KB금융 같은 금융지주 별도 서식 skip
- **timeline scope 신규**:
  - 최근 5년 filings 각 원문 파싱 → 연도별 준수율 + 15지표 O/X 수집
  - `transitions` 필드: 지표별 improved / regressed / changed 자동 감지
  - 렌더러에 ✅ 개선 / ❌ 후퇴 / — 변동 카테고리 표시
- **audit 해석 정정**:
  - shareholder_meeting.summary 필드체커 0/15: tool 코드는 정상, 실제 data는 `meeting_info`/`selected_meeting`/`agenda_summary` 등에 저장. audit script만 수정 필요
  - dilutive 1 exception 재현 시도: 에러 0건 → 일시적 이상치로 판정
- README: 의무화 연도 "2026년부터 KOSPI 전체" 반영, timeline scope 예시 추가

## [2026-04-22] feat | 4-phase 릴리스: usage 표준화 / 확장 audit / corp_gov_report / 원문파싱 보강
### Phase 1: data.usage 표준화 (7 → 모든 data tool)
- `dart/client.py`: `_request_counter` 추가, 매 `_request()`에서 +1
- `services/contracts.py`: `build_usage(api_calls)` 공통 헬퍼 추가
- 7개 service(`company`, `shareholder_meeting`, `ownership_structure`, `dividend_v2`, `treasury_share`, `proxy_contest`, `value_up_v2`) 각 payload에 `data.usage` 주입 — ERROR/AMBIGUOUS/성공 경로 모두 포함
- 검증: 7 tool 모두 `{dart_api_calls, mcp_tool_calls, dart_daily_limit_per_minute}` 노출 확인

### Phase 2+3: 확장 audit (scope × 필드 채움률)
- 15 회사 × 14 tool.scope = 210 호출 병렬
- 매트릭스: status 분포 + 필드 채움률 + avg_s + avg_api
- 에러 3건 (0.8%): shareholder_meeting 2건, dilutive 1건 (이상치)
- 필드 채움률: 86% 수준. shareholder_meeting.summary 0/15은 audit checker 버그 (tool 정상)
- 결과: `wiki/analysis/parsing-audit-2026-04-22.md` 저장

### Phase 4: corp_gov_report tool 신규 (15 → 16 tool)
- **의무 범위 정정**: 2024 사업연도부터 KOSPI 전체 의무 / KOSDAQ 자율공시
- `services/corp_gov_report.py`: list.json + 키워드 "기업지배구조보고서" → 최신 원문 다운로드 → BeautifulSoup 파싱
- 4 scope: `summary` / `metrics` / `principles` / `filings`
- 파싱 필드:
  - **기업개요** (표 1-0-0): 최대주주, 지분율, 소액주주, 업종, 기업집단, 요약재무
  - **준수율** (%)
  - **15 핵심지표**: 지표명 + 당기 O/X + 직전기 O/X + 비고
  - **세부원칙 응답** 최대 30건
- 전수조사 10개 회사: 7개 완벽 파싱 (15/15), 3개 서식 차이로 7-8지표만 (파서 보강 필요)
- KT&G 100% 준수율, 에이피알 66.7%, NAVER 86.7% 등 정확 추출
- wiki 신규: `disclosures/기업지배구조보고서.md`, `analysis/corp_gov_report-design.md`

### 문서
- README / README_ENG: 15 → 16 tool, 거버넌스 카테고리 추가, 사용 예시 2종 추가
- wiki/entities/OpenProxy-MCP.md: 15 → 16 tool, screen_events 14 → 22 event_type
- disclosure 페이지 총 26 → 27개

## [2026-04-21] feat | screen_events 22 event_type 확장 + rpt 원문 파싱 + audit 매트릭스
### Phase 1: screen_events event_type 14 → 22
- 희석성 증권 4종 (rights_offering / convertible_bond / warrant_bond / capital_reduction)
- 내부거래 4종 (equity_deal_acquire / equity_deal_dispose / supply_contract_conclude / supply_contract_terminate)
- 전수조사 8/8 exact 통과 (market=all, 최근 30일)

### Phase 2: related_party_transaction 원문 파싱 보강
- 새 tool 파라미터: `include_details`, `details_limit`
- 타법인주식 거래: 거래 상대방/관계/금액/자기자본대비/자산대비/취득후 지분/방법/목적/풋옵션/최대주주관계 추출
- 단일공급계약: 계약 종류/명/금액/최근매출/매출대비비율/상대방/관계/기간 추출
- `_extract_relationship()` 헬퍼: 정해진 관계 값 후보만 허용 (자회사/계열회사/관계회사 등)
- 실측: POSCO홀딩스/성호전자/현대건설/삼성전자 80-90% 정확도

### Phase 3: 파싱 audit 매트릭스
- 20 회사(대형5+분쟁5+지주3+M&A 3+중소4) × 10 data tool 병렬 호출
- 결과: 에러 0건 / company·shareholder·dividend·proxy_contest 100% exact / ownership·treasury·rpt 85-90% exact
- partial 많은 tool (corp_restructuring, dilutive_issuance, value_up)은 "사건 없음" 케이스로 정상 해석
- 평균 응답시간: 1.2s (가벼운 tool) ~ 6.4s (dividend 등 무거운 tool)
- wiki/analysis/parsing-audit-2026-04-21.md 저장

## [2026-04-21] feat | dilutive_issuance + related_party_transaction data tool 2종 추가 (13→15 tool)
- **dilutive_issuance** (희석성 증권 발행 4종 통합):
  - `dart/client.py`: 4개 메서드 (piicDecsn / cvbdIsDecsn / bdwtIsDecsn / crDecsn)
  - `services/dilutive_issuance.py`: 4개 API 병렬, scope별 정규화, 희석률 근사 계산
  - `tools_v2/dilutive_issuance.py`: 5 scope (summary/rights_offering/convertible_bond/warrant_bond/capital_reduction), headline_metric 기반 timeline
  - 전수조사: EDGC(7건)/하이퍼코퍼레이션(CB 4건)/나무기술(BW 2건)/감자(EDGC 83.33%) 5/5 통과
- **related_party_transaction** (내부거래 모니터링):
  - `services/related_party_transaction.py`: DART 전용 API 없어 list.json + 키워드 방식. filing_search.search_filings_by_report_name 재사용
  - scope: summary / equity_deal / supply_contract
  - 플래그: subsidiary_report(자회사주요경영사항), autonomous_disclosure(자율공시), is_correction([기재정정])
  - 전수조사: POSCO홀딩스(3건 모두 자회사)/삼성전자(2건 공급계약)/현대건설(72건 supply)/성호전자(9건 equity_deal acquire) 5/5 통과
- wiki 신규 disclosure 페이지 6종: 유상증자결정/전환사채발행결정/신주인수권부사채발행결정/감자결정/타법인주식및출자증권거래/단일판매공급계약체결
- wiki analysis 2종: dilutive_issuance-design.md, related_party_transaction-design.md
- README / README_ENG / entities/OpenProxy-MCP / index.md / log.md 업데이트 (13→15 tool)
- disclosure 페이지 총 20 → 26개

## [2026-04-21] docs | disclosure 페이지 일관성 정비 + 누락 7종 신규 작성
- 신버전 3개 페이지를 구버전 양식(트리 + API필드대응 + OPM활용)으로 보강
  - 회사합병결정.md, 회사분할결정.md, 주식교환·이전결정.md
- 신규 disclosure 페이지 7종:
  - 회사분할합병결정.md (cmpDvmgDecsn — 표본 적어 합병/분할 superset 표기)
  - 자기주식결정.md (취득/처분/소각/신탁 5종 통합)
  - 기업가치제고계획.md (DART+KIND 자율공시, 3단계 분류)
  - 최대주주변경.md (양수도·담보·합병·단순변경 4형태)
  - 임원·주요주주특정증권등소유상황보고서.md (elestock DS004)
  - 소송등의제기.md (4종 + 거버넌스 시그널 매트릭스)
  - 경영권분쟁소송.md (분쟁 단계별 사건 유형 + 후속 신호)
- index.md disclosures 섹션에 9개 신규 항목 추가 (총 22 → 23개)
- 표준 양식: frontmatter / 개요 / 소스 / 전체 문서 구조 (트리) / API 필드 vs 원문 대응 / OPM에서의 활용 / 거버넌스 분석 포인트 / 관련 / 샘플 rcept_no

## [2026-04-21] feat | corporate_restructuring data tool 추가 (합병/분할/분할합병/주식교환·이전 4종 통합)
- `dart/client.py`: DART 주요사항보고서(DS005) 4개 메서드 신규
  - `get_merger_decision()` → cmpMgDecsn.json
  - `get_division_decision()` → cmpDvDecsn.json
  - `get_division_merger_decision()` → cmpDvmgDecsn.json
  - `get_stock_exchange_decision()` → stkExtrDecsn.json
- `services/corporate_restructuring.py` 신규:
  - 4개 API 병렬 호출 (asyncio.gather), summary scope에서 통합 timeline
  - 정규화: 합병비율, 상대방 재무, 외부평가, 매수청구권, 일정 등 핵심 필드 추출
  - 기본 lookback 24개월 (M&A는 빈도 낮음)
- `tools_v2/corporate_restructuring.py` 신규: 4 scope 렌더러 (summary/merger/split/share_exchange) + 사용량 표시
- 전수조사: 7개 회사 케이스 (온코크로스/일동제약/감성코퍼레이션/이마트/신세계푸드/두나무/삼성전자) — 5/5 통과 (비상장+사건없음 케이스 제외)
- wiki 추가: `disclosures/회사합병결정.md`, `disclosures/회사분할결정.md`, `disclosures/주식교환·이전결정.md`, `analysis/corporate_restructuring-design.md`
- `wiki/entities/OpenProxy-MCP.md`: 12 → 13 tool 반영
- README/README_ENG: 12 → 13 tool, 사용 예시 2개 추가

## [2026-04-21] fix | 공시 viewer_url을 DART로 통일 (KIND URL 404 해결)
- 문제: KIND 원문 URL(`kind.krx.co.kr/common/disclsviewer.do?acptno=...`)이 직접 접근 시 404
- `services/contracts.py::_build_viewer_url()`: source_type=KIND_HTML도 DART 뷰어 URL 반환
  - DART 뷰어(`dart.fss.or.kr/dsaf001/main.do?rcpNo=`)는 80 포맷(거래소 수시공시) rcept_no도 정상 렌더링
- `tools_v2/evidence.py`: docstring에서 KIND URL 언급 제거
- `wiki/analysis/evidence-tool-검증-예시.md`: viewer_url 매핑 설명 + 샘플 테이블 수정
- 영향: ownership_structure(changes), shareholder_meeting(results), value_up 등 KIND-HTML 사용 tool의 evidence viewer_url이 DART로 자동 전환
- 내부 KIND 크롤링(`kind_fetch_document()`, 3단계 iframe)은 그대로 유지 — 사용자 노출 URL만 변경

## [2026-04-19] feat | screen_events UX 보강 (사용량 노출, 원문 링크, market 축소)
- `services/screen_events.py`:
  - market 3종(`kospi`/`kosdaq`/`all=KOSPI+KOSDAQ`)으로 축소 — KONEX/기타 제거
  - `_search_market_wide()`: `corp_clses` 튜플 지원 (all은 Y→K 순차 호출로 구현)
  - api_calls/truncated/pages_cut_off stats 반환
  - 결과가 max_results 도달하면 별도 truncation warning 추가
- `tools_v2/screen_events.py`:
  - 렌더러에 `## 사용량` 블록 추가 (DART API 호출 수, MCP tool 호출 수, 분당 한도)
  - 결과 테이블 `rcept_no` → 클릭 가능한 원문 링크로 변경
  - docstring market 옵션 업데이트
- `wiki/analysis/screen_events-design.md`: 사용량 추적/market 설계 섹션 보강

## [2026-04-19] feat | screen_events discovery tool 추가 (14 event_type, market-wide 역조회)
- `dart/client.py::search_filings()`: `corp_cls` 파라미터 추가 (Y/K/N/E 시장 필터)
- `services/screen_events.py` 신규:
  - 14 event_type 카탈로그 + (pblntf_tys, keywords, strip_spaces) 매핑
  - `_search_market_wide()`: corp_code 없이 시장 전체 대상 페이지 순회 (max 20페이지/ty)
  - `build_screen_events_payload()`: market→corp_cls 변환, rcept_dt 내림차순, max_results=1-100
- `tools_v2/screen_events.py` 신규: MCP 인터페이스, compact 테이블 렌더러
- 전수조사 (최근 30일, market=all): **14/14 통과**
  - 초기 설계에서 `annual_meeting`/`extraordinary_meeting` 분리 시도 → DART report_nm이 "주주총회소집공고" 단일 포맷이라 구분 불가능 → `shareholder_meeting_notice` 단일로 통합 (15→14)
  - `treasury_retire` 키워드 오류 발견 → 실제 제목은 "주식소각결정" + pblntf_ty=I로 수정
- `wiki/analysis/screen_events-design.md` 신규, `wiki/entities/OpenProxy-MCP.md` (11→12 tool) 업데이트

## [2026-04-19] feat | ownership_structure scope=changes 추가 (최대주주등소유주식변동신고서)
- `_parse_change_filing()`: KIND HTML 5개 테이블 파싱 (보고개요 직전/금번, 개인별변동, 총괄현황)
- `_fetch_change_filings()`: DART pblntf_ty=I 검색 → rcept_no 80→00 변환 → kind_fetch_document() 최대 5건
- `build_ownership_structure_payload()`: scope=changes 처리 + KIND_HTML evidence_refs
- `tools_v2/ownership_structure.py`: changes scope 렌더러, docstring 업데이트
- `wiki/disclosures/최대주주등소유주식변동신고서.md` 신규, `wiki/index.md` disclosures 항목 추가

## [2026-04-18] feat | shareholder_meeting v2 2차 구현 (board, compensation, results, 시점 구분)
- `services/shareholder_meeting.py` 확장:
  - `scope=board|compensation|results` 추가
  - `meeting_phase` 추가: `pre_meeting | post_meeting_pre_result | post_result | undetermined`
  - `result_status` 추가: `not_due_yet | pending_or_missing | available | requires_review | unknown`
  - 결과 공시는 DART `주주총회결과` 검색 후 `80 -> 00` 변환이 가능한 whitelist 건만 KIND HTML로 연결
- `meeting_type=auto` 기본화:
  - `annual` 최신 회차와 `extraordinary` 최신 회차를 후보로 생성
  - 일반 조회는 정기/임시를 가리지 않고 가장 현재적인 회차 우선
  - 결과 조회는 결과공시가 확인된 회차 중 최신 회차 우선
  - `selection_basis`, `selected_meeting`, `alternative_meetings` 추가
- 최근 12개월 커버리지 추가:
  - 주총 관련 제목군(`주주총회소집공고`)만 대상으로 조사
  - `annual_only | extraordinary_only | annual_and_extraordinary | none` 플래그 추가
  - 선택된 회차 기준 최근 12개월 구간과 정기/임시 최신 회차 메타데이터 제공
  - `auto` 후보도 최근 12개월 기준 `가장 최근 정기 1개 + 가장 최근 임시 1개`로 변경
  - 교차연도 회차는 각 회차의 실제 회의연도로 결과공시 검색해 매핑
- `tools_v2/shareholder_meeting.py` 확장:
  - `summary`에 결과 시점 블록 추가
  - `summary`에 회차 선택 근거와 대안 회차 블록 추가
  - `summary`에 최근 12개월 커버리지 블록 추가
  - `board`, `compensation`, `results` 출력면 추가
  - 회의 전과 결과공시 후를 구분해 표시
- 실조회:
  - `KT&G`, `auto`, `2026`, `summary` → `meeting_type=annual`, `meeting_phase=post_result`, `result_status=available`
  - `KT&G`, `annual`, `2026`, `board` → 후보 3명 확인
  - `KT&G`, `annual`, `2026`, `compensation` → 당기 한도 `6,000백만원`, 전기 지급 `2,445백만원`
  - `KT&G`, `auto`, `2026`, `results` → `rcept_no=20260326802654`, KIND `20260326002654`, 의결 결과 파싱 성공
  - `한화`, `auto`, `2025`, `summary` → `meeting_type=extraordinary`, 대안으로 `annual` 표시
  - `아시아나항공`, `auto`, `2025`, `summary` → `meeting_type=annual`, 대안으로 `extraordinary` 표시
  - `KT&G`, `auto`, `2026`, `summary` → coverage `annual_only`
  - `한화`, `auto`, `2025`, `summary` → coverage `annual_and_extraordinary`
- sanity check:
  - `python -m compileall open_proxy_mcp` 통과

## [2026-04-18] feat | ownership_structure control_map 고도화
- `services/ownership_structure.py` 확장:
  - `control_map`를 단순 raw dump에서 해석 가능한 블록 구조로 재편
  - `core_holder_block`, `treasury_block`, `overlap_blocks`, `non_overlap_blocks`, `active_non_overlap_blocks`, `flags`, `observations`, `notes`
  - 최대주주 명부와 5% 블록의 이름 겹침 여부를 `registry_overlap`으로 표시
  - 관찰 포인트는 의미 있는 5% 이상 능동 블록만 기준으로 생성
- `tools_v2/ownership_structure.py` 확장:
  - `control_map` 전용 출력면 추가
  - 명부상 특수관계인 합계, 자사주, 겹치지 않는 능동 5% 블록, 겹치는 5% 블록을 분리 표시
- 실조회:
  - `삼성전자`, `control_map` → `삼성물산` 블록은 명부와 겹치는 능동 블록으로 표시
  - `고려아연`, `control_map` → `한국기업투자홀딩스`, `최윤범`, `크루시블제이브이`를 겹치지 않는 능동 블록으로 표시
  - `한화`, `control_map` → 명부상 특수관계인 50% 이상 + 자사주 5% 이상 플래그 확인
- sanity check:
  - `python -m compileall open_proxy_mcp` 통과

## [2026-04-18] feat | proxy_contest를 control_map과 최근 12개월 기준으로 재정렬
- `services/proxy_contest.py` 확장:
  - 최근 12개월 조사구간(`window_start`, `window_end`)을 공시 검색 기본 단위로 추가
  - 위임장/공개매수, 소송/분쟁은 제목군만 타깃해서 최근 12개월 안에서 조회
  - `ownership_structure`의 `control_map`을 가져와서 분쟁 문서와 5% 블록을 같은 판에서 해석
  - `players` 추가:
    - `company_side_filers`
    - `shareholder_side_filers`
    - `active_external_blocks`
    - `active_overlap_blocks`
  - `fight`에 `actor_group` 추가:
    - `company`
    - `external_active_block`
    - `registry_overlap`
    - `shareholder`
  - `signals`에 `actor_side` 추가:
    - `external_active_block`
    - `registry_overlap`
    - `external_or_passive`
  - `timeline`에도 `actor`, `side`를 넣어 누가 어떤 문서를 냈는지 바로 읽히게 변경
  - 5% 시그널은 최근 12개월 밖 공시가 섞이지 않도록 window 필터 적용
- `tools_v2/proxy_contest.py` 확장:
  - `summary`에 조사구간, 최대주주/특수관계인 합계/자사주 비중 추가
  - `판 구조` 블록 추가: 회사측 제출인, 주주측 제출인, 외부 능동 블록, 명부 겹침 블록
  - `fight`, `signals`, `timeline` 표에 플레이어 분류 열 추가
- 실조회:
  - `고려아연`, `summary`, `2026`
    - 회사측 제출인 `고려아연`
    - 주주측 제출인 `영풍`
    - 명부와 안 겹치는 능동 5% 블록 `최윤범`, `크루시블제이브이`, `한국기업투자홀딩스`
    - 명부와 겹치는 능동 블록 `영풍`
  - `한화`, `summary`, `2026`
    - 회사측 제출인 `한화`
    - 명부상 특수관계인 합계 `55.84%`
    - 자사주 `7.45%`
    - 최근 12개월 시그널은 `김승연` 1건으로 정리
- sanity check:
  - `python -m compileall open_proxy_mcp` 통과

## [2026-04-18] feat | v2 public tool 날짜 파라미터 표준화
- `services/date_utils.py` 신규:
  - `start_date`, `end_date` 파싱
  - 기본 조회구간 계산
  - 날짜 역전 시 자동 보정
- `company`
  - `start_date`, `end_date` 추가
  - 최근 공시 인덱스를 지정 구간으로 조회
- `shareholder_meeting`
  - `start_date`, `end_date`, `lookback_months` 추가
  - 지정 구간 또는 롤링 구간에서 정기/임시 최신 회차를 고름
  - 응답에 `requested_window` 추가
- `ownership_structure`
  - `as_of_date`, `start_date`, `end_date` 추가
  - 스냅샷 기준 연도는 `as_of_date`의 직전 사업연도로 연결
  - 5% 블록/타임라인은 지정 구간으로 필터
- `dividend`
  - `start_date`, `end_date` 추가
  - 배당결정 공시와 history 구간을 날짜 기준으로 제한
- `proxy_contest`
  - `start_date`, `end_date`, `lookback_months` 추가
  - 분쟁 공시/시그널 window를 명시적으로 제어
- `value_up`
  - `start_date`, `end_date` 추가
  - 밸류업 공시 검색 구간을 날짜 기준으로 제어
- `evidence`
  - `start_date`, `end_date`를 받도록 시그니처 통일
  - 현재는 `rcept_no` 직접 조회가 우선이라 window는 메타데이터로만 저장
- 실조회:
  - `company('삼성전자', 2026-03-01~2026-04-18)` → recent filings window 반영
  - `shareholder_meeting('한화', 2025-12-01~2026-04-18)` → `annual` 선택, `annual_and_extraordinary`
  - `ownership_structure('한화', as_of=2026-04-18, 2026-01-01~2026-04-18)` → `exact`, timeline 4건
  - `dividend('삼성전자', 2024-01-01~2025-12-31)` → `exact`, 최근 결정 5건
  - `proxy_contest('고려아연', 2025-12-01~2026-04-18)` → `exact`, 능동 시그널 4건
  - `value_up('KB금융', 2025-01-01~2026-04-18)` → `exact`, timeline 3건
  - `evidence(rcept_no=20260225005779, keyword='제39기', 2026-01-01~2026-12-31)` → `exact`
- sanity check:
  - `python -m compileall open_proxy_mcp` 통과

## [2026-04-18] feat | prepare_vote_brief 1차 구현
- `services/vote_brief.py` 신규:
  - `shareholder_meeting`의 `summary/agenda/board/compensation/results`와
    `ownership_structure control_map`을 묶어 한 장 메모 payload 생성
  - 추천 찬반을 단정하지 않고, 회차/판 구조/안건/후보자/보수/결과/체크포인트 중심으로 정리
  - `meeting_date`를 `ownership as_of_date`로 넘겨 같은 회차 기준 스냅샷을 맞춤
  - evidence는 하위 data tool의 evidence를 합쳐 dedupe
- `tools_v2/prepare_vote_brief.py` 신규:
  - `prepare_vote_brief` public action tool 추가
  - 기본 파라미터: `company`, `meeting_type`, `year`, `start_date`, `end_date`, `lookback_months`
  - markdown 출력은 `회차`, `판 구조`, `안건`, `후보자`, `보수`, `결과`, `체크 포인트`, `근거` 순으로 정리
- 실조회:
  - `KT&G`, `2026`
    - `annual`, `post_result`, agenda 15건, 후보 3명, 보수안건 1건
    - 결과 안건 14건 모두 가결
    - 체크 포인트: 자사주 5% 이상
  - `한화`, `2025-12-01 ~ 2026-04-18`
    - `annual`, `post_result`, agenda 17건, 후보 5명, 보수안건 1건
    - 반대율 10% 이상 안건: 정관 일부 변경(이사 임기 변경), 이사 보수 한도 승인
    - 체크 포인트: 정정공고 반영, 특수관계인 50%+, 자사주 5%+, 명부 겹침 능동 블록
- sanity check:
  - `python -m compileall open_proxy_mcp` 통과
  - `build_mcp('v2')`에서 `prepare_vote_brief` 등록 확인

## [2026-04-18] feat | prepare_engagement_case + build_campaign_brief 추가
- `services/engagement_case.py`, `tools_v2/prepare_engagement_case.py` 신규:
  - `ownership_structure(control_map)`, `proxy_contest(summary)`, `value_up(summary)`를 합쳐 engagement memo 생성
  - 출력은 `쟁점 프레이밍`, `지배구조 맥락`, `분쟁 신호`, `밸류업/주주환원 맥락`, `체크 포인트`, `근거`
  - 자동 추천이나 처방은 넣지 않고 fact-first 구조 유지
- `services/campaign_brief.py`, `tools_v2/build_campaign_brief.py` 신규:
  - `shareholder_meeting(summary/agenda/board)`, `ownership_structure(control_map)`, `proxy_contest(timeline)`를 합쳐 campaign fact brief 생성
  - 출력은 `회의 맥락`, `플레이어`, `지배구조`, `분쟁 개요`, `타임라인`, `핵심 플래그`, `근거`
  - `brief_note`로 vote math/추천 부재를 명시
- 실조회:
  - `prepare_engagement_case('KT&G', 2025-12-01~2026-04-18)` → `exact`, `cmp_033780`
    - 최대주주 `중소기업은행 8.06%`
    - 자사주 `12.03%`
    - engagement용 쟁점 프레이밍/분쟁 신호/밸류업 맥락 정상 생성
  - `build_campaign_brief('KT&G', 2026)` → `exact`, `cmp_033780`
    - `meeting_type=annual`
    - timeline 3건
    - 플레이어/지배구조/회의 맥락 정상 생성
- sanity check:
  - `python -m compileall open_proxy_mcp/services/engagement_case.py open_proxy_mcp/tools_v2/prepare_engagement_case.py open_proxy_mcp/services/campaign_brief.py open_proxy_mcp/tools_v2/build_campaign_brief.py` 통과

## [2026-04-18] feat | release_v2 scaffold + company facade 첫 구현
- `open_proxy_mcp/tools_v2/` 신규: v2 public facade layer 시작
- `open_proxy_mcp/services/` 신규: v2 공통 service layer 시작
- `services/contracts.py` 신규: `AnalysisStatus`, `SourceType`, `ToolEnvelope`, `EvidenceRef` 정의
- `server.py` 업데이트: `build_mcp(toolset)` 추가, `v1|v2|hybrid` 선택 지원
- `__main__.py` 업데이트: `main()` 직접 호출 구조로 단순화
- `tools_v2/company.py`, `services/company.py` 신규: `company` data tool 초안 구현
- 정책 반영: partial match 자동선택 금지, exact가 아니면 `ambiguous`
- `company` 현재 범위: 회사 식별 + 기본 카드 + 최근 공시 인덱스
- sanity check:
  - `python -m compileall open_proxy_mcp` 통과
  - `build_mcp('v2')` 성공
  - `build_company_payload('삼성전자')` → `exact`, `cmp_005930`

## [2026-04-18] feat | shareholder_meeting v2 1차 구현 (summary, agenda)
- `services/shareholder_meeting.py`, `tools_v2/shareholder_meeting.py` 신규
- 정기/임시 주총을 하나의 public tool에서 `meeting_type=annual|extraordinary`로 처리
- 현재 scope는 `summary`, `agenda`만 지원
- 동작 원칙:
  - 회사 식별 exact가 아니면 자동선택 금지
  - 소스는 `DART list.json + DART XML`
  - PDF fallback 없음
  - 안건 파싱 신뢰도 낮으면 `requires_review`
- 반환 범위:
  - notice 메타데이터
  - meeting_info
  - agenda_summary
  - agendas(scope=agenda)
  - correction_summary
  - DART XML evidence ref
- 실조회:
  - `KT&G` → alias로 `케이티앤지` 식별
  - `2026 annual summary` → `exact`, `cmp_033780`, `rcept_no=20260225005779`, `agenda_total_count=15`
  - `2026 annual agenda` → root 8건, 첫 안건 `제1호 제39기 재무제표 및 이익잉여금처분계산서 승인의 건`

## [2026-04-18] feat | remaining v2 data tools 구현 (ownership_structure, dividend, value_up, proxy_contest, evidence)
- 신규 service:
  - `ownership_structure.py`
  - `dividend_v2.py`
  - `value_up_v2.py`
  - `proxy_contest.py`
  - `evidence.py`
- 신규 public facade:
  - `ownership_structure.py`
  - `dividend.py`
  - `value_up.py`
  - `proxy_contest.py`
  - `evidence.py`
- 지원 범위
  - `ownership_structure`: `summary`, `major_holders`, `blocks`, `treasury`, `control_map`, `timeline`
  - `dividend`: `summary`, `detail`, `history`, `policy_signals`
  - `value_up`: `summary`, `plan`, `commitments`, `timeline`
  - `proxy_contest`: `summary`, `fight`, `litigation`, `signals`, `timeline`
  - `evidence`: `evidence_id` 또는 `rcept_no` 기반 원문 발췌
- 정책 반영
  - partial match 자동선택 금지 유지
  - PDF fallback 없음
  - `proxy_contest.vote_math`는 아직 비공개 (`requires_review`)
- sanity check
  - `python -m compileall open_proxy_mcp` 통과
  - `build_mcp('v2')` 성공
- 샘플 실조회
  - `ownership_structure('삼성전자', summary, 2025)` → `exact`, `cmp_005930`, 자사주 `1.55%`
  - `dividend('삼성전자', summary, 2025)` → `exact`, `cmp_005930`, 연간 DPS `1668원`
  - `value_up('KB금융', summary, 2026)` → `exact`, `cmp_105560`, 최신 `rcept_no=20260327802428`
  - `proxy_contest('고려아연', summary, 2026)` → `exact`, `cmp_010130`, fight `7`, shareholder-side `4`, litigation `40`, active signals `4`
  - `evidence(rcept_no='20260225005779')` → `exact`

## [2026-04-18] docs | 신규 tool 추가 검증 정책 + release_v2 소스 검증 기준 정리
- `decisions/tool-추가-검증-정책.md` 신규: data/action tool 분류, 공시 매핑표, 화이트리스트 체크, 샘플 검증, 출시 게이트 정리
- `DART-KIND-매핑-화이트리스트-2026-04`를 신규 tool 검증 정책의 기준 문서로 연결
- `index.md` 업데이트: release_v2 정책 문서 카탈로그 반영
- `templates/tool-추가-검증-템플릿.md` 신규: 제안서, data/action 검증, whitelist extension, release gate 복붙 템플릿 추가
- `WIKI_SCHEMA.md` 업데이트: templates/ 디렉토리와 `type: template` 정의 추가
- `analysis/shareholder_meeting-tool-검증-예시.md` 신규: 실제 `rcept_no` 샘플로 `shareholder_meeting` data tool 검증 예시 작성
- `analysis/release_v2-public-tool-검증-매트릭스.md` 신규: release_v2 공개 data/action tool 전체 판정 요약
- `analysis/company/ownership/dividend/proxy_contest/value_up/evidence` 검증 예시 추가
- `analysis/release_v2-action-tool-검증-초안.md` 신규: action tool 3종을 phase-2 검증 대상으로 정리
- `analysis/release_v2-tool-아키텍처.md` 신규: `company -> data tools -> evidence -> action tools` 구조를 도식화
- `contestation` 명칭을 `proxy_contest`로 통일

## [2026-04-12] refactor | tool 체이닝 + governance_report + tier 체계 완성 (33개)
- agm_pre_analysis + own_full_analysis → tier-5 asyncio.gather 병렬 체이닝
- prx_fight → prx_search + prx_direction 체이닝 (중복 제거)
- governance_report: AGM + OWN + DIV 3도메인 통합 (33번째 tool)
- div_full_analysis format="json" 추가 → 전 tool json 지원 완성
- tier 태그 32/32 완성, tool_guide tier-2, news_check tier-5
- pblntf_ty 필터링 전면 적용 (D/E/I), _DIV_KEYWORDS 상수화
- wiki 정리: archive/ 9개, decisions/pblntf-ty-필터링.md, disclosures/배당공시유형.md

## [2026-04-11] docs | wiki 구조 재편 + disclosures 트리 + comparison 카테고리 신설
- analysis/ → decisions/(기술결정) + analysis/(외부소스+주총분석) 분리
- comparison/ 신규: 공시 간/내 컨셉 비교 카테고리
- stkrt-vs-ctr_stkrt.md: DART 대량보유 필드 오해 정정 (ctr_stkrt = 주요계약체결, 보고자 직접보유 아님)
- disclosures/ 10개 페이지 전체 문서 구조 트리 추가
- graphify로 wiki knowledge graph 탐색 (202 nodes, 360 edges)

## [2026-04-10] fix | own_full_analysis 테이블 포맷 + 대량보유 비교 기준 정리
- 헤더 카드: 최대주주/특관합계/자사주
- ctr_stkrt(본인) vs stkrt(합산) 구분, 비고에 합산 명시
- docstring rule에 테이블 출력 형식 지시

## [2026-04-10] refactor | Dispatch Table + Chain Tool + README 재작성
- Dispatch Table: 16 PDF/OCR → agm_parse_fallback 1개 (48→32 tools)
- Chain Tool: own_full_analysis (지분+배당+자사주+주주환원)
- README.md 한국어 전면 재작성 + README_ENG.md 영어 신규
- OpenProxy-MCP entity 업데이트 (33 tools, 아키텍처 패턴)

## [2026-04-09] ingest | news_check tool + decision tree
- news_check: 네이버 뉴스 API 기반 후보자 부정 뉴스 검색 tool
- Proxy Voting Decision Tree: AGM_TOOL_RULE에 6개 안건 판정 기준
- 네이버-금융 entity: 뉴스 검색 API 섹션 추가

## [2026-04-05] lint | 누락 개념 4개 + broken ref 수정 + sources 필드 추가
- concepts/ 4개 신규: 자본준비금, 당기순이익, 주주환원, 경영권-방어
- DART-OpenAPI.md: related에서 alotMatter 제거, 배당성향/div-tool-rule로 교체
- analysis/ 4개: sources 필드 추가 (cross-domain-체이닝, proxy-voting-decision-tree, 상법개정-타임라인-2026, 주총방어-시나리오-4가지)
- index.md 업데이트

## [2026-04-05] ingest | 외부 소스 3건 (JPM voting, 주총방어전략, 주총체크리스트)
- raw/ 3건: J.P Morgan Asset Management Voting Process.md, 주총방어전략.pdf, 주주총회 체크리스트.pdf
- sources/ 3개 신규: jpm-voting-process, 주총방어전략-2026, 주총체크리스트-2026
- analysis/ 3개 신규: 주총방어-시나리오-4가지, 상법개정-타임라인-2026, proxy-voting-decision-tree
- concepts/ 2개 업데이트: 프록시-파이트 (방어전술/글로벌 프로세스 추가), 위임장-권유 (글로벌 기관 구조 추가)
- index.md, log.md 업데이트

## [2026-04-09] ingest | docstring 전면 업그레이드 + cross-domain 체이닝
- 46/46 tool desc/when/rule/ref 포맷 적용 (100%)
- cross-domain ref 7개 추가 (AGM↔OWN↔DIV)
- cross-domain-체이닝.md 신규: 도메인 간 tool 연결 맵 + 시나리오 3개
- index.md 업데이트

## [2026-04-18] feat | v2 proxy_contest vote_math 추가
- `proxy_contest(scope="vote_math")` 공개
- 기준:
  - `shareholder_meeting(results)`의 KIND 결과표 사용
  - 안건별 추정참석률 = 발행기준 찬성률 / 행사기준 찬성률
  - 보통결의 안건 최빈값을 대표 추정참석률로 사용
  - 감사위원/집중투표/비교 불가 안건은 제외
- 출력:
  - 대표 추정참석률
  - 특수관계인/자사주/능동 5% 블록
  - 특수관계인 제외 추정 참석분
  - 고반대율/부결 안건
  - signal_level (`stable` / `watch` / `contestable`)
- 원칙:
  - 승패 예측 아님
  - 결과공시 없거나 비교 가능한 보통결의 안건이 없으면 `requires_review`

## [2026-04-18] feat | 요약형 KIND 주총결과 파서 추가
- `shareholder_meeting(results)`가 세부표형뿐 아니라 요약형 결과공시도 읽도록 확장
- 지원 패턴:
  - `○ 제1호 의안 : ... → 원안가결`
  - `제2-1호 의안 : ...` 다음 줄 `→ 부결`
  - `1) 제1호 의안: ... → 원안대로 승인`
- 출력에 `result_format=table|summary`, `numerical_vote_table_available` 추가
- 요약형이면:
  - 안건별 가결/부결은 제공
  - 찬성률/반대율/추정참석률은 비제공
  - `vote_math`는 계속 `requires_review`

## [2026-04-18] feat | prepare_vote_brief에 결과 품질과 vote_math 연결
- `prepare_vote_brief`가 이제 결과공시 품질을 같이 보여줌
- 결과가 세부표형이면:
  - `result_format=table`
  - 수치표 제공 여부 표시
  - `vote_math` 요약(대표 추정참석률, signal_level, 특수관계인 제외 추정 참석분) 포함
- 결과가 요약형이면:
  - `result_format=summary`
  - 안건별 가결/부결만 사용
  - `vote_math`는 비활성
- 목적:
  - “결과 확인 가능”과 “숫자 분석 가능”을 분리해서 읽히게 함

## [2026-04-18] feat | prepare_vote_brief에 집중투표 사전 전략 블록 추가
- `prepare_vote_brief`에 `cumulative_voting_strategy` 추가
- 포함 내용:
  - 집중투표 대상 이사 수
  - 자사주 차감 후 전체 의결권 모수 비율
  - 100% 참석 가정 1석선
  - 이전/동일 회차 참석률 참고 1석선
  - 최대주주/특수관계인/능동 블록과의 격차
- 가드레일:
  - 공시에 집중투표가 명시되지 않고 복수 이사 선임만 있는 경우 `partial`
  - 감사위원/분리선출은 집중투표 대상 수에서 제외하는 보수적 기준 사용

## [2026-04-18] feat | shareholder_meeting에 DART viewer HTML crawl fallback 추가
- 원칙 반영:
  - `document.xml` 기반 파싱이 약하면 DART `main.do -> report/viewer.do` HTML crawl로 재시도
  - 자동 fallback은 `shareholder_meeting` notice 파싱에만 제한적으로 적용
- 구현:
  - `DartClient.get_viewer_document()` 추가
  - `shareholder_meeting`가 `meeting_type/datetime/agenda` 품질이 낮을 때 viewer HTML로 재파싱
  - `notice_parse_source=dart_xml|dart_html` 메타 추가
- 의도:
  - 공식 API/XML을 기본으로 유지
  - 구조가 깨질 때만 웹 크롤링을 2차 경로로 사용

## [2026-04-18] feat | KT&G 2024 구형 요약형 결과공시 파서 보강
- 샘플:
  - KT&G 2024 정기주총 결과 `20240328801345`
- 문제:
  - KIND 본문이 `주주총회 안건 세부내역` 표가 없는 구형 요약형
  - `- 제1호 : ...`, `☞ 제3-1호 및 제3-3호 가결, 제3-2호 부결` 패턴
- 보강:
  - `의안` 없는 구형 제목형 파싱
  - `내지`, `및`이 섞인 하위호안 outcome line 분해
  - 후보자 출처 괄호 문장을 안건 제목에 이어붙이기
- 결과:
  - `shareholder_meeting(results)`가 KT&G 2024를 `summary` 형식으로 구조화
  - `vote_math`는 여전히 `numerical unavailable`로 보수적으로 유지

## [2026-04-08] lint | 고립 노드 수정 + disclosure 카테고리 추가
- 34개 페이지에 본문 wikilink 추가 (고립 해소)
- disclosures/ 신규: 11개 공시 유형 페이지
- index.md 업데이트

## [2026-04-07] lint | 건강 점검 + 수정
- broken link 수정: v4-스키마, 소진율 페이지 생성
- cross-ref 불일치 11개 수정 (8개 페이지 related 필드 업데이트)

## [2026-04-07] init | Wiki 초기화
- 디렉토리 구조 생성 (raw/ + wiki/)
- CLAUDE.md(schema) 작성
- raw/ 시딩: rules 6개 + devlog 1개 + benchmarks 1개 + READMEs 2개

## [2026-04-05] ingest | 첫 전체 ingest (10 raw sources)
- raw/rules/ 6개: AGM_TOOL_RULE, AGM_CASE_RULE, DIV_TOOL_RULE, DIV_CASE_RULE, OWN_TOOL_RULE, OWN_CASE_RULE
- raw/rules/ 2개: OPM_README, OPA_README
- raw/devlog/DEVLOG.md
- raw/benchmarks/benchmark_personnel_results.json
- 생성: sources 10개, concepts 24개, entities 9개, analysis 8개 (총 51 페이지)
- index.md 전체 업데이트

## [2026-04-19] feat | action tool에 source quality 메타 전파
- `prepare_vote_brief`, `prepare_engagement_case`, `build_campaign_brief`에 quality 블록 추가
- 포함 항목:
  - component status
  - `notice_parse_source`
  - `result_format`, `numerical_vote_table_available`
- 목적:
  - action memo를 볼 때 결론의 기반 소스 품질을 바로 판단하게 함

## [2026-04-19] fix | v1-v2 실패 원인 구분 보강
- `value_up_v2`
  - `availability_status` 추가
  - `search_diagnostics` 추가
  - 요청 구간에 공시가 없는지, v1 호환 진단 구간(`전년도 1월 1일 ~ 대상연도 12월 31일`)에도 없는지 구분
- `dividend_v2`
  - `history_selection` 추가
  - `history` scope는 미완료 사업연도를 제외하고 최근 완료 사업연도 기준으로 3개년을 고르도록 보강
- 확인:
  - `현대자동차 value_up`: 요청 구간에도 없고 v1 호환 진단 구간에도 공시 없음
  - `삼성전자 dividend history`: 2026 미완료 사업연도 대신 2023/2024/2025 완료 3개년 반환

## [2026-04-19] fix | value_up KIND fallback + 현대자동차 pagination 보정
- `현대자동차` 사례로 기존 `공시 없음` 판정을 정정
  - DART 웹과 DART API 모두 공시가 존재
  - 예: `rcept_no=20240828800218`
  - 이전 누락 원인은 `2024~2026`처럼 구간이 길 때 `list.json` 첫 100건만 보고 pagination을 넘기지 않아 예전 공시가 밀린 것
- KIND fallback은 유지
  - DART가 진짜 비는 거래소 자율공시를 대비한 보조 경로로 유지
  - 다만 `현대자동차`는 `KIND-only`가 아니라 `DART pagination 누락` 사례로 재분류
- `dart/client.py`
  - KIND 상세검색 기반 `kind_search_disclosures()` 추가
  - `기업가치 제고 계획(0184)` 전용 `kind_search_value_up()` 추가
- `value_up_v2`
  - DART search에서 pagination 처리 추가
  - DART에서 못 찾으면 KIND `기업가치 제고 계획(0184)` 검색으로 재시도
  - 진단 구간을 `최근 3개 연도`로 확대해, 최근 12개월 밖에 있는 기존 계획도 `요청구간 밖 존재`로 구분
  - `availability_status=exists_outside_requested_window`에서 DART/KIND 샘플 공시를 함께 노출
  - `primary_source=dart|kind` 추가
  - 최신 공시 메타에 `rcept_no` 또는 `KIND acptno`, `source_type` 반영
- `company` 식별 보정
  - 동일 회사명 이력 중 현재 상장 엔티티가 명확할 때는 최신 상장 엔티티를 우선 선택
  - `기아`, `우리금융지주` 같은 케이스를 exact로 연결

## [2026-04-19] verify | value_up 10개 추가 검증
- 검증 구간: `2024-01-01 ~ 2026-04-19`
- 결과:
  - `현대자동차`: `exact`, DART, `rcept_no=20240828800218`
  - `기아`: `exact`, DART
  - `현대모비스`: `exact`, DART
  - `KB금융`: `exact`, DART
  - `하나금융지주`: `exact`, DART
  - `신한지주`: `exact`, DART
  - `우리금융지주`: `exact`, DART
  - `메리츠금융지주`: `exact`, DART
  - `삼성생명`: `exact`, DART
  - `POSCO홀딩스`: `exact`, DART
  - `삼성전자`: `exact`, DART
- 해석:
  - 현재 검증 샘플 기준 11개 전부 DART 경로로 정상 조회
  - 현대자동차는 `KIND-only`가 아니라 `DART pagination` 처리 부족이 원인이었음

## [2026-04-19] fix | 제목 타깃 검색 경고 전파 + taxonomy wiki 반영
- `filing_search` 경고 문구를 구체화
  - 단순히 `몇 페이지까지만 확인`이 아니라
  - `어느 기간`, `어떤 pblntf_ty`, `어떤 제목군`을 기준으로 확인했는지 같이 남기도록 수정
- `proxy_contest`
  - 위임장/공개매수, 소송/분쟁 검색에서 제목 타깃 helper가 내는 경고를 실제 warnings에 반영
  - 앞으로 `정해진 기간 내 일부 페이지만 확인`한 경우 analyst가 바로 알 수 있게 됨
- `shareholder_meeting`
  - notice/result 검색에서도 helper 경고를 warnings에 반영
  - `소집공고 없음`과 `검색 범위를 제한해 확인함`을 분리해서 볼 수 있게 됨
- `dart-kind-disclosure-taxonomy.md`
  - wiki source 문서 `[[dart-kind-disclosure-taxonomy]]`로 반영
  - v2 소스 정책과 공시군 분류 기준 문서로 재사용 시작
