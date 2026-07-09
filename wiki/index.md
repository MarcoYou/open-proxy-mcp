---
type: index
title: OPM Wiki Index
updated: 2026-07-07
---

# OPM Wiki Index

OPM은 한국 상장사 거버넌스 분석 MCP. 이 인덱스에서 시작.

> 👤 **사람이 처음 오셨다면 → [[guide/README]]** (사람용 안내서: 개요·아키텍처·발표자료).
> 아래 인덱스는 AI 에이전트·개발자용 전체 카탈로그입니다.

## Quick Start (사용자 진입점)

OPM tool 20개 카탈로그 -> **[[tools/README]]** (처음 방문 시 여기부터)

### 도메인별 (20 tool, 260708 director_board 추가)
- **Company (1)**: [[company]]
- **Meeting (2, 시점 분리)**: [[shareholder_meeting_notice]] (사전 — DART, 5 scope: summary/board/compensation/aoi_change/prov_financials) · [[shareholder_meeting_results]] (사후 — DART 원문 우선, KIND fallback)
- **Data (14)**: [[ownership_structure]] · [[dividend]] · [[financial_metrics]] · [[treasury_share]] · [[proxy_contest]] · [[value_up]] · [[corporate_restructuring]] · [[dilutive_issuance]] · [[corporate_deals]] · [[order_contracts]] · [[risk_events]] · [[corp_gov_report]] · [[director_board]] · [[valuation]]
- **Evidence (1)**: [[evidence]]
- **Action (2)**: [[proxy_advise_before_meeting]] (decisions 단일 — facts/risk/citation/근거공고/후보 raw 통합, 사후 결과는 [[shareholder_meeting_results]]) · [[shareholder_commitment]] (밸류업·배당·소각 약속 vs 실제 이행, 연중 스튜어드십 — 자사주소각 장부가손익 신규 계산)

### Internal services (MCP 노출 X — chain 전용)
- `director_evaluation` — proxy_advise 후보 평가 chain (결격 / 독립성 / 전문성 / 과거 행적)
- `director_performance` — 사내이사 재직 중 성과 매트릭스 2x3 (ROE/부채비율/CSR × avg/trend) — proxy_advise 사내이사 분기에 wire
- `agm_first_agenda_fy` — 1번 안건 본문 FY raw 파서

### 주요 변화 (2026-07-07 ~ )
- **director_board tool 신설 — 20번째 tool (2026-07-08)** — **개별 이사 단위** 정보: 이사 인당보수·
  보수한도 소진율·임원 재직/사퇴 변동(연도 diff)·(v2)이사회 출석률·겸직. corp_gov_report(회사 15지표
  준수)와 무중복. 소진율은 DART 정형 API(exctvSttus·drctrAdtAllMendngSttus 2종)로 산출 —
  감사위원 포함 이사류 실지급÷주총 승인한도(IR 검증 260708: 헤드카운트 정합 확증), 한도 공백해는
  최근 유효연도 lookback. 재직/사퇴 diff는 이름 OR 생년월 매칭으로 로마자표기·birth 오타 오탐 억제
  (QA 260708: 기아 신재용 birth_ym 오타 이중오탐 발견·수정). 가치판단 안 함(수치·변동·flag만).
  client에 이사·보수 정형 API 4종 신규 추가. IR·스튜어드십·QA 3에이전트 교차검증.
- **shareholder_commitment tool 신설 — 19번째 tool, 2번째 Action Tool (2026-07-07)** — 밸류업·배당·자사주
  소각의 **약속 vs 실제 이행**을 연중 추적(proxy_advise가 주총 1회성 판단이라면, 이건 스튜어드십
  engagement 관점의 지속 추적). 4개 upstream 재사용(value_up·corp_gov_report·dividend·treasury_share,
  신규 파싱 없음) + 신규 계산 1개: 자사주 소각 사이클마다 매입시점 BPS 대비 실제 매입가를 비교해
  **장부가(BPS) 손익을 원화로 계산**(배당은 방향이 반대라 이 계산에서 제외, 대신 CSR 종합에는 포함).
  이 계산이 정확하려면 treasury_share의 실행결과보고서 금액이 정확해야 했는데, 원문 표 단위(백만원 등)
  미인식 버그를 KOSPI200 전수 스캔으로 발견·수정(26건→0건, QA 2인 원문대조 검증)한 뒤 그 위에 tool을
  올림. sanity 필터로 별개 이슈(`_link_cycles` 매칭 오탐 4건)를 조용히 흡수하지 않고 명시적으로 플래그.
  ([[shareholder_commitment]] / [[treasury_share]])

### 주요 변화 (2026-05-04 ~ 06-21)
- **주총 안건/정기·임시 파서 6사이클 production 배포 + 검증 측정 함정 5패턴 (2026-06-21)** — detect 재작성(880→888, 섹션 오선택→소집공고 직후 40자 앵커)·marker/zone·bleeding 경계·하위안건 분리·**proposer 복원**(주주제안 `source` 전파, 다원시스 라이브 확인)·빈제목 부모 추론. 통합 픽스처 **3,016건**(코스피·코스닥 3/1~5/15, 정기 2,849) 확보. **측정 스크립트가 6번 거짓 결론**(이미지=본문부재·미상=사각지대·0개=버그·bleed 날짜오탐·v2 정답오판·변경분만 봄) → 직접 표본·전수 diff·production 경로(html)로만 잡힘. 프로토콜: **html 픽스처 0콜 + 전수 diff + 직접 표본**. ([[lessons/agenda-parser-validation-260621]])
- **proxy_result 제거(17→16) + 2차 전수조사 4축 (2026-06-13)** — proxy_result_after_meeting 제거: 핵심(안건별 가결/부결/찬반율)을 `shareholder_meeting_results`가 3콜 vs 32콜로 대체, desc의 'cross-match' 미구현, 핵심 0건 회귀를 한 달간 무인지 = 실사용 부재. 코드 archive 보존. 남은 검증축 전수: **seam audit**(proxy_advise 8사 composite vs 직접 호출 — 고려아연 headcount str crash 발견·근원 fix), **render smoke**(16툴×31케이스 FastMCP call_tool — 솔루엠 perf None 포맷 crash 발견 → 31/31, payload audit이 못 보는 render 레이어), **corp_gov 값**(30사 450지표 O/X + 기준값 정확 일치, 삼성 13/15), **production MCP smoke**(정식 클라이언트 initialize→list→call, 재설계 반영 확인). 메타: 가짜 clean 3회(틀린 키)→첫 케이스 실키 출력+양성 검사량 보고 표준화 / 인프라 이상은 단발 증상 말고 curl 직접 격리 확인(키 무효 오진 정정). 릴리즈 노트 docs/RELEASE_NOTES.md로 분리. ([[lessons/tool-coverage-audit-260612]])
- **audit 미커버 툴 전수조사 — proxy_result 0건 회귀 발견·교정 (2026-06-12)** — 커버리지 매핑(파싱 성공률 vs 내용 정확도 2층위) 후 미흡 순서로 전수. ① ownership 450사: DART 원본 단위 오염 2사(LS 명부 ×1,000·LS에코 발행총수 ×1,000,000) — 지분율 anchor 자가 교정 + 분모(보통주 vs 총주식) 괴리 경고. ② dilutive·restructuring·deals 값 정확도 286사: deals 행 1,560 채움률 100% issue 0, 날짜 676건 한국어 원본 → ISO 일괄 정규화. ③ evidence 8케이스(불가능 달력 날짜 검증 추가) + **proxy_result: 핵심 안건결과가 항상 0건이던 회귀**(upstream 키 `agenda_results`→`results.items` rename 미반영, no_results로 위장) 발견 → 교정 후 15/15 복구. 과거 연도(2024·25) 결과는 서술형 공시라 소스 한계. 교훈: baseline 없는 툴은 죽어도 모른다 / upstream rename 시 소비자 동시 갱신 / upstream exact + 자기 0건 = 버그 신호. ([[lessons/tool-coverage-audit-260612]])
- **financial_metrics 정밀화 — hedge 역추적 → 412사 전수 audit (2026-06-12)** — 실사용(SK하이닉스 질의)에서 호스트 모델의 장시간 추론·"확인 필요" hedge가 전부 tool 파싱 결함의 증상임을 역추적. 교정 9건: **Q4 누적차분 + QoQ·YoY 기본 동봉**, fs_div 필터(CFS+OFS 동시 반환 실측), **이자보상배율 왜곡 제거**(금융비용 오염 — 삼성전자 3.72→92.8배) + 변형 정확일치 세트로 커버리지 59%→97%, 차입금 generic(차입금/차입부채), 분기 fallback CF 결측(used_rc 전파), 재작성 gap flag 이원화·손익 3키 확장, EBITDA(D&A 24% 원천 한계)·잠식률 표시 정책. 검증: 라운드 점증(4→412사 × FY24·25, ~4,200콜) + 불변식(4분기합=연간·FCF=CFO−CapEx·듀퐁곱=ROE) 수렴. 교훈: 모델의 hedge는 공짜 버그 리포트, 결측이 오류보다 낫다, 누적 공시는 항상 차분. ([[financial_metrics]] / [[lessons/financial-metrics-precision-260612]] / [[260612_fm_market_audit_412|audit raw]])

- **risk_events tool 신설 — 17번째 tool (2026-06-11, serious_accident 흡수 확장)** — 기업 리스크 이벤트 6종 통합: **중대재해 / 횡령·배임 / 파생상품손실 / 회생·부도 / 생산중단·영업정지 / 해산** (본사·종속/자회사 변형 포함). 채널 = **I001+B001 동시 조회** (회생'신청'·부도·영업정지·해산은 B001 전용 — 시장 90일 sweep 실측으로 채널 매핑 확정). 중대재해는 **305사 × 3.5년** 검증(I 전체 vs I001 차집합 0·truncation 0·풀스캔 누수 0) — 공시 79건 전수: **대형 원청·지주사 집중**(KOSDAQ 상위 100 보유 0, 중소형 건설 35사 0건). **시장 전체 스캔 모드**(company 미지정, 30일 기본·90일 max, ~45콜): 30일 실측 54건/6카테고리. 카테고리별 원문 파싱(사상자/혐의금액·자기자본%/손실액/중단부문) + **사상자 supersede 집계**(정정·지주/사업회사 이중 공시 → 발생일자+장소 정규화 키, 한화 '㈜' vs '(주)' 이중 집계 버그 실측 교정). 처벌확인 실물 2건 첫 확인(화일약품·THE CUBE&), 횡령배임 파서 태광산업 검증(38.3억·자기자본 0.09%). 제도 시점: 중대재해 수시공시 신설 2025-10 → 이전 무공시 ≠ 무사고 warning. 본문 파싱은 연속 2개 90일 윈도우 **359건 전수 audit**(파생 `손실발생금액`·영업정지 라벨 등 교정, 전 카테고리 87~100%). **스콥 결정: 활성 3종(중대재해/횡령배임/생산중단영업정지), 파생손실·회생부도·해산은 mute** — 파서·검증 보존, 기본 조회·desc 비노출, 명시 category 요청 시만 동작. ([[risk_events]] / [[lessons/risk-events-pipeline-260611]])
- **related_party_transaction → corporate_deals rename (2026-06-10)** — "SK스퀘어가 인수하거나 매각한 회사" 질의가 tool 라우팅 실패. 원인 = 이름·desc에 사용자 어휘(인수/매각) 부재 + 이름이 기능 절반(지분 딜 추적)보다 좁음 + corporate_restructuring이 "M&A" 선점. scope 분기 대신 **rename + desc 사용자 어휘 보강**("어떤 회사를 인수했나/팔았나", 출자·회수) + restructuring과 **양방향 경계 문구**. 내부 plumbing 전면 통일, 기능 변화 0, SK스퀘어 회귀 동일(취득4/처분3). ([[lessons/tool-naming-discovery-260610]])
- **proxy_advise 2단계 조기 발사 시도 → 실측 반증 → 롤백 (2026-06-10)** — perf를 director gate 판단 후 1차 완료 전 조기 발사. component-timing 모델상 이득 1.7초(회귀 수학적 불가능, before/after bit-identical)였으나 **wall-clock 실측에서 반증**: 복잡 20개 손해 16/20, 흔한 15개 손해 12/15, 평균 음수. 측정 노이즈(같은 코드 2회 차이 중앙 426ms)가 효과(-350ms)보다 커 이득이 묻힘 + Semaphore(3)·throttle 경합으로 약한 손해 경향 → 롤백. 교훈: component 모델 ≠ wall-clock, 효과 측정 전 노이즈 baseline 필수. ([[lessons/proxy-advise-stage2-parallel-260610]])
- **ownership_structure summary 재설계 + 정합성 버그 2건 (2026-06-10)** — "고려아연 지분구조" 질문에서 출발. summary 헤드라인을 **명부 단독 vs 본인+특관 vs 5% 실세**로 라벨 분리(집계 기준 다름), **지분 구성 100% 정합 분해**(명부+자사주+기타, 5%보고는 보고자 중복이라 합산 100% 불가) 추가, 노이즈 컷·블록 병합(6→3블록). changes scope를 **I004(최대주주변동신고서)**로 좁히고 **5% 대량보유 변동 통합**(분쟁사는 5%보고로 움직여 I004만 보면 빔, 고려아연 0→15건). director_evaluation **E006** narrowing. 33개사 스크리닝으로 정합성 버그 교정: **셀트리온 issued=0**(우선주 없는 회사는 `합계` 행만 → 보통주 행 가정 실패 → 합계 fallback), **금호석유 resolve 실패**(정식명 prefix 단일후보 자동선택). 펀드형(맥쿼리인프라) issued=0 안내. ([[lessons/ownership-summary-integrity-260610]])
- **공시 검색 페이지컷 truncation 교정 — 6 tool detail-code 좁히기 (2026-06-09)** — 넓은 공시유형(I·B,I,E·D) 페이지 순회(max_pages=10)가 prolific 회사에서 truncation. proxy_contest D 검색이 삼성에서 D002(임원 수천건)에 밀려 D001/D003/D004 일부 잘림(broad 6 vs detail 14, +8 복구). [[공시유형코드체계]] 카탈로그 기반 정밀 매핑 + '넓은type vs detail 차집합 0' 검증으로 6 tool(corp_gov·value_up·shareholder_meeting·treasury·related_party·proxy_contest) 좁힘. filing_search 멀티 detail-code 지원 + 013(no-data) abort 버그 fix. ([[lessons/page-cut-detail-code-260609]])
- **배당 파서 출처확정+누적차분+분류 정밀화 (2026-06-09)** — 출처맵(A 사업보고서 다년컬럼=권위 / B 분기·반기 누적 / C 결정공시 날짜 / D 명부폐쇄 기준일). 분기별을 결정공시 날짜추측 대신 **정기보고서 누적 차분**(Q2=반기-Q1…)으로 보통+우선 DPS·총액 산출 → 경계 오귀속·예비결산 중복 제거, 무배당 분기 0·특별배당 포착. 최신연도 4분류(중간확정/확정전/미공시/무배당, target연도 매칭). 중복제거(pre_dividend 통합·pending_annual 제거 → DART -3/회사). per-decision 시가배당률 0 억제. stateless MCP(머신별 세션 in-memory → nrt×2 "Session not found" 해소). **51개사 정합성 100%** 검증. ([[lessons/dividend-source-of-truth-260609]] / [[배당공시유형]])
- **proxy_contest 분쟁 신호 정밀화 (2026-06-05~07)** — 경영권 분쟁 탐지 다축화. ① 5% 대량보유 시계열 동학 (목적전환 단순투자→경영참여 / 지속 추가매입 / 급변 ±5%p 매집·exit 양방향). ② 소송 4단계 분류: 정정 dedup → 경영권/상거래 구분(commercial false positive 제거, 아시아나항공 등) → 미상 회사단위 추정 → 본문 "사건의 명칭" 파싱(litigation scope, 병렬, 📄) → LLM 위임. 키워드 단독 행위 + 명사·행위 조합 구조(substring FP 제거). ③ 능동 5% 외부세력/대주주 본인 분리. ④ dead code(_block_signals 중복 majorstock) 제거. 역추적 방법론 검증: 시총순 14% vs 분쟁 공시 역추적 71.6% (5배 효율), 142종목 진짜 경영권 분쟁 70 추출. 자동 판정 X — 정보 구조화 + LLM 위임 철학. ([[lessons/contest-signals-500-260605]] / [[lessons/dispute-reverse-lookup-260607]])
- **데이터 tool latency pass (2026-05-24)** — `data.timings_ms` 노출 + low-risk 병렬화. treasury_share 결과보고서 검색 전체공시→B/I/E title scan(삼성전자 2.7s→0.9s), dividend metadata overlap, filing_search page2+ 병렬, corp_gov 검색윈도우 4년→2년. median speedup 58.6% (4.137s→0.211s), p95 4.163→0.251s. 핵심: 병렬화보다 "대기를 겹치는 위치"가 중요. ([[260510_data_tools_perf_audit]] / [[perf-timing-260524]])
- **value_up role extraction (2026-05-31)** — 최신 공시 1개 중심에서 `latest_plan` / `latest_status` / `latest_result` / `meta_amendment` 역할 분리. `계획서 명칭` 기반 보정, meta-only 구간 최근 2년 role backfill, `이행결과` nullable 분리. KOSPI500 + KOSDAQ150, 562 filing 전수조사 기준. ([[260530_audit_value-up-implementation-tags]] / [[value_up]])
- **financial_metrics Tier 1 확장 (2026-05-31)** — CFO/순이익, DSO/DIO/DPO/CCC 추가. 51 → 56 지표, 추가 API 호출 없음. ([[financial_metrics]])
- **agenda parser marketwide (2026-05-25)** — KOSPI500 + KOSDAQ150, XML 641건, no_filing 9, 3회 재파싱 hash diff 0. 최신 기준은 [[260525_1620_audit_agenda-parser-marketwide]].
- **agenda relation KOSPI300 rerun (2026-05-25)** — exact 298 / no_filing 2 / requires_review 0, relation metadata는 결론이 아니라 자동 판단을 멈추는 guardrail. ([[260525_0200_audit_agenda-relation-kospi300]] / [[agenda-relation-parser-260525]])
- **key data tools parsing 성공률 감사 (2026-05-17~18)** — KOSPI 300 + KOSDAQ 150 baseline과 비중복 100개 recheck 기준 문서 신설. 최신 기준은 [[architecture/audits/260517_parsing_success_rate_audit]]. `value_up`은 outside-window/013 no_filing 분류 보강 후 strict 100%, `shareholder_meeting_results`는 DART-first 결과 파싱 후 adjusted hard fail 0%.
- 17 → 16 tool: `screen_events` drop, `proxy_guideline` archive, `shareholder_meeting` → notice + results 분리
- proxy_advise scope **10 → 1** (`decisions`만, raw는 각 tool 직접 호출)
- treasury_share scope **6 → 2** (summary + annual)
- 자사주 결과보고서 **4종 추가** (취득결과/처분결과/신탁상황/신탁해지결과)
- ralph proxy_advise framework 99% 검증 (KOSPI 100 + KOSDAQ 50, G1 100% / G2 0% FP / G3 100% / G4 100%)
- 사내이사 **재직 중 성과 매트릭스 (2x3)** 도입 — status quo bias mitigation. ROE/부채비율/CSR × avg/trend, bad → AGAINST, weak → REVIEW. KOSPI 100 + KOSDAQ 50 검증 G1 100% / G4 dist 29.7/45.3/18.0/7.0 모두 target band 충족. ([[260505_1700_decision_inside-director-performance-matrix]])
- **보수한도 / 퇴직금 분기 정밀화** — 이사 13 / 감사 11 / 퇴직금 12 분기 + 정관 hybrid 통합. KOSPI 200 + KOSDAQ 50 (n=226) G1 99-100% / G3 100% / G4 N연기금 정합 100%. AGAINST 5건 (지급률 2배수+ × 3, 사외이사 퇴직금 × 1, 자본잠식+인상 × 1) 모두 정확 분기. ([[260505_1900_decision_compensation-retirement-split]])
- **shareholder_meeting_notice scope 정리** — 6→5 (`agenda`/`full` 폐지, `prov_financials` 신설). summary 강화 (hierarchy + 1호 안건 메타) + aoi_change에 retirement raw 통합. `provisional_financial_statement.py` 독립 모듈 (parser.py 의존성 제거). ([[260506_0030_decision_notice-scope-cleanup-prov-financials]])
- **parser omnibus 검증 + DART 6컬럼 sub-column fix** — KOSPI 200 + KOSDAQ 100 (300 회사) 통합 audit, 9 Tier A parser G1 ≥98.7% 모두 충족. PFS metric extraction 19 sparse 케이스 (현대차/셀트리온/두산 등) root cause = `_period_by_num` 다음 colspan 확장 빈 셀이 "unknown" 분류되던 것 → fix 후 100%. v1 dead 3 parser logical archive 결정 + G4 layer 정합 PASS. ([[lessons/parser-omnibus-260506]] / [[260506_2330_decision_v1-dead-parsers-archive]])
- **법령 layer 정밀화 (Ralph 4)** — Ralph 3 follow-up. 280 회사 광범위 검증 (KOSPI 200 + KOSDAQ 100 + 분쟁 20). B1-4 분기 (정관변경 vs 후보 임기) + B1-8b 신규 (KT&G 정관 사전 우회 catch) + B1-7 보강 (정원 키워드). `_agenda_pattern_match()`에 parent_must_contain/parent_excludes 패턴 키 신규. 36 → 38 룰. false positive 0 / 회귀 0. 분쟁 회사 hits 11.6% (KOSPI 9.8% / KOSDAQ 1.8%). ([[lessons/law-layer-precision-260508]] / [[260508_0700_decision_law-layer-precision]])
- **파서 전수조사 + 정밀화 검증 (Ralph 5)** — 40 파서 분류 (A 명명형 25 / B raw 보존 1 / C 혼합 14). framework: 데이터 본질에 따라 (숫자→파싱, 자연어→raw, 메타+본문→혼합). parse_aoi_xml이 모범 사례 (clause/label 명명 + before/after raw). audit 1차 권장 (parse_personnel_xml + parse_aoi_xml 보강) Ralph 5 실측 후 무효화 — careerDetails 0% 누락 (44회사/225후보) / aoi 1.66% 누락 (모두 source 한계). 두 파서 정밀도 충분, 코드 변경 X. ([[architecture/audits/260508_parser_audit]] / [[lessons/parser-precision-260508]])
- **Wiki 트리 정책 명문화 + lint hook (2026-05-09)** — 식물학 metaphor 도입 (🌱뿌리 raw → 🪵줄기 rules → 🌿큰가지 → 🌾잔가지 → 🍂낙엽). Link 정책: 단방향(위→아래)/양방향(큰가지↔잔가지)/자유(잎↔잎). ABCDE 정리: 단방향 위반 34→0, 양방향 결손 44→0, orphan 24→7, edges 1261→1558. `scripts/wiki_lint.py` + GitHub Actions CI. CLAUDE.md 124→109 가벼움화. 구 *_RULE.md 7개 archive 이동 (`wiki/archive/tools/legacy_rules/`). data-collection.md DS003 섹션 추가 (financial_metrics 4 API). ([[architecture/audits/260509_wiki_graph_audit]] / [[WIKI_SCHEMA#0-트리-구조-식물학-metaphor]])
- **financial_metrics yoy 병렬화 (2026-05-09 perf)** — Explore agent 효율성 audit 결과 #1 fix. sequential 3 호출 (curr/prev/audit_opinion) → `asyncio.gather` 병렬. 회사당 ~3초 → ~1초 (2-3배). 100 회사 배치 시 3-7분 단축. regression 0 (read-only API + 독립 인자). 다른 발견 (#2-#4)은 trade-off로 skip — cache 인프라 견고하여 ROI 낮음.
- **proxy_advise decision 시각 강조 + B1/B2 raw 첨부 (2026-05-10)** — LG화학 LLM misread (proxy_advise FOR 무시하고 안건명 "배제"만 보고 자체 AGAINST 추측) 방지. ✅ FOR + 🛡️ 강행규정 정합 marker / B1/B2 hit 안건 정관 본문 raw `[clause 변경 전/후]` 첨부 (cache hit으로 latency +1-2%). A1/A2는 결정 강제 유지 (토큰 절약), B1/B2만 LLM case-by-case 판단용 raw.
- **운용사·NPS·ISS 전수 익명화 (2026-05-10)** — 9 commits. tool description vote_style 옵션 list 제거 + README 표 제거 + `_VOTE_STYLE_POLICY_FILE` 실명 alias 제거 + wiki/data 200+ 파일 일괄 익명화 + sa_active → sa_legacy (실제 운용 스타일) + ISS/BAMK 일반화 + "외부 advisor" 항목 제거 (b_foreign에 흡수). 최종 익명 catalog: m/s/sa/k_legacy + t/a/c_activist + b_foreign + n_pension (9개). manager_aliases.json (gitignored) v4.
- **★ production wiki/rules/laws/ 누락 fix (2026-05-10 b5951a4)** — Dockerfile에 `COPY wiki/rules/laws/` 누락으로 38 법령 룰이 **production에서 작동 안 했음**. LG화학 misread의 진짜 원인. v355 deploy로 production /app/wiki/rules/laws/ 활성. + llm_misread_patterns.json (6 패턴 catalog) 신규 — 새 misread 발견 시 JSON 한 줄 추가, 코드 변경 X. + Tool description ⛔ CRITICAL 가이드 inline (Layer 1).
- **호수 hierarchy 진단 + D 패턴 amendments body fallback (Ralph 7, 2026-05-10)** — 사용자 가설 "parser가 호수 누락" 검증 → false (10/10 회사 거의 완벽, LG화학 ※ note span 미세 버그 1건만 fix). 4 미매치 회사 = D 패턴 (raw에 sub-agenda 자체 부재 + top title 일반 표현). 룰 catalog `body_pattern` 별도 필드 추가 (title 매칭 회귀 위험 0). amendment 단위 검사 + strict 진입 조건 (children 0)으로 Ralph 6 회귀 회피. **510 회사 spot 회귀 0** + body fallback 신규 70건 catch (69 회사 = 13.5%) + **A1-8 (자사주 의무소각) 첫 활성** (Ralph 6 미사용 룰 lesson 중 첫 catch). 카카오게임즈는 D 패턴 X (sub 있고 sub title 일반) — 별도 ralph 후보. ([[lessons/agenda-hierarchy-260510]] / [[260510_0900_decision_d-pattern-body-fallback]])
- **카카오게임즈 패턴 sub→amendment 1:1 매핑 (Ralph 8, 2026-05-10)** — 510 회사 중 진정 카카오게임즈 패턴 26개 (5.1%) 처리 architect. 진입 조건 (parent 정관변경 + sub children 0 + sub generic 아님 + amendments) + strict cascade (label substring → clause 매칭, keyword 매칭 의도적 제외 — semantic mismatch false positive 회피). cross-match 회피 (회사별 used_amendments track). 510 회사 회귀 0 + sub 75건 신규 catch (55 회사 = 10.8%) + 미사용 룰 A1-3 (18건) / B1-8 / A1-2 활성. KOSPI 23% vs KOSDAQ 5% (대형사 sub-hierarchy 명확). ([[lessons/subagenda-mapping-260510]] / [[260510_1015_decision_subagenda-mapping]])
- **사외이사 충실성 강화 — 겸직 카운트 + 사내이사 독립성 표기 정정 (Ralph 9, 2026-05-10)** — 메리츠금융지주 응답 검토 사용자 피드백. careerDetails 510 회사 audit (98.4% 채워짐) → 단순 키워드 카운트 false positive 발견 (본 회사 사외이사 표기) → logic v3 (본 회사명 매칭 + 후보 본인 보장). `count_outside_director_positions` + faithfulness 통합 (≥3 strong / ≥2 concerns). 사내이사 "독립성 평가 비대상 (사내이사)" 표기 (오인 방지). decision 변경 0 (facts 신규 노출만). 510 분포: concerns 13.3% / strong 2.7% 후보. 김정연(삼성바이오 strong 3개) / 박진규(LG에너지 concerns 2개) 사례 검증. ([[lessons/director-faithfulness-260510]] / [[260510_1130_decision_director-faithfulness]])
- proxy_advise render Korean label 자연화 (`weak_concerns` → "약한 우려" 등)
- archive: `wiki/archive/services/` (proxy_guideline / proxy_guideline_scoring / policy_comparison / agm_first_agenda_fy_v1_regex)

## 카테고리 구조

| 카테고리 | 목적 | 페이지 수 | 수정 가능 |
|---|---|---|---|
| **raw/** | 외부 source (운용사 정책 PDF/xlsx, 외부 reference) | 29 binary + 4 md | NO (절대 수정 금지) |
| **tools/** | 20 tool 진입점 + data source map | 19 + README | YES (tool 변경 시) |
| **architecture/** | OPM 시스템 설계 + audit + fix + data archive | 60+ | YES |
| **decisions/** | OPM 정책 + 판단 + debate | 26 + README | YES |
| **rules/** | 한국 자본시장 사실 (concepts/disclosures/laws) | 70+ | YES (사실 update 시) |
| **lessons/** | 작업 회고 (Did / Improved / Trade-off / Takeaway) | 23 + README | YES (배운 것 추가 시) |
| **archive/** | 흡수된 페이지 (역사 보존) | 69 | WARN (단순 보존) |

총 305 markdown + raw binary.

## 명명 규칙 (2026-05-01~)

```
시점 있는 문서:  yymmdd_hhmm_{type}_{title}.md
정체성 문서:     {name}.md
```

| Type | Prefix | 예시 |
|---|---|---|
| audit / fix / decision / debate / changelog / improvement / release / log | YES | `260429_2030_audit_parsing-200기업.md` |
| tool / concept / disclosure / law | NO (정체성=이름) | `tools/shareholder_meeting.md` |

상세 schema와 워크플로우는 [[WIKI_SCHEMA]] 참조.

## 자주 쓰는 진입점

### 처음 사용자
- [[tools/README]] - 20 tool 카탈로그
- [[WIKI_SCHEMA]] - wiki 구조 + 명명 규칙

### OPM 정책 알고 싶음
- [[open-proxy-guideline]] - Open Proxy Guideline v1.3 (12 카테고리 + 16 novel topics)
- [[260429_0059_decision_voting-policy-consensus-matrix]] - 8 운용사 합의 매트릭스
- [[260429_0059_debate_opm-guideline-7전문가]] - 7 전문가 토론

### 시스템 동작 이해
- [[architecture/data-collection]] - 데이터 수집 architecture
- [[architecture/3-tier-fallback]] - XML -> PDF -> OCR
- [[architecture/matrix-system]] - 12 매트릭스 설계 자산 (자동 채점은 의결권 엔진 미사용 — dead code)
- [[architecture/proxy-voting-decision-tree]] - 의결권 판단 framework
- [[architecture/pipeline-architecture]] - 199 기업 v4 JSON 배치 파이프라인
- [[architecture/multi-upstream-pattern]] - asyncio.gather tool 표준 5 요소 (corpCode lock/retry/per-call timeout/semaphore/cache)
- [[architecture/lessons-learned]] - MCP 개발 7가지 교훈

### 한국 자본시장 용어 모름
- [[rules/concepts/]] - 31 개념 (배당성향 / 최대주주 / 동일인 / 집중투표 등)
- [[rules/disclosures/]] - 36 공시 유형 (현금배당결정 / 유상증자결정 / 자기주식취득결정 등)
- [[rules/laws/상법-2025-2026-종합]] - 1·2·3차 상법 개정 통합본 + 4 시나리오 + 36 catalog (master, 260508)
- `wiki/rules/laws/law_layer_rules.json` - 머신리더블 36 룰 (proxy_advise._law_layer 직접 로드)
- [[rules/laws/README]] - 법령 자료 입구 (옛 archive 안내)

### 최근 audit / fix
- [[260530_audit_value-up-implementation-tags]] - value_up plan/status/result/meta 분리. KOSPI500 + KOSDAQ150, 562 filing, meta 28건 비교
- [[260525_1620_audit_agenda-parser-marketwide]] - KOSPI500 + KOSDAQ150 agenda numbering/title/body extraction 전수검증
- [[260525_0200_audit_agenda-relation-kospi300]] - agenda relation KOSPI300 재실행 exact 298 / no_filing 2
- [[260517_parsing_success_rate_audit]] - key data tools parsing 성공률 감사. KOSPI 300 + KOSDAQ 150 baseline, 비중복 100개 recheck, value_up/shareholder_meeting_results 보강 및 regression 확인
- [[260510_data_tools_perf_audit]] - public data tools 성능 감사와 low-risk 개선 후보
- `260505_inside_director_performance/` — 사내이사 성과 매트릭스 KOSPI 100 + KOSDAQ 50 audit (n=128, G1 100%, dist 29.7/45.3/18.0/7.0 target band 모두 충족, threshold ≥9→≥7 calibration)
- [[260504_0724_audit_parse_personnel_iter1-7]] - parse_personnel ralph 7 iter — role 88.7→100% + regression 0 (G2 99.36% 유지)
- [[260510_proxy_advise_audit_통합정리]] - proxy_advise / action audit 통합 정리
- [[260503_2304_audit_recap_pattern]] - recap_vote 패턴 적용 200×3 100% (multi-upstream-pattern 일반화 검증)
- [[260503_1847_audit_phase4_final]] - advise_vote 200×3 deterministic 100% + regression 0 (Phase 4)
- [[260510_parsing_audit_통합정리]] - 2026-05-10 이전 parsing audit 통합 정리
- [[260429_0912_audit_parsing-200기업-v2-no_filing]] - 196 기업 × 11 tool audit 이력
- [[260429_2053_audit_personnel-878명]] - personnel 파서 SUCCESS 79->95%
- [[260429_0942_audit_arithmetic-21지표]] - 산술 정확성 audit
- [[260427_1145_fix_ownership-stockknd]] - 보통주 변형 매칭 fix
- [[260429_0942_fix_corp_gov_report-financial-holding]] - 금융지주 분류 fix

---

## Tools (18 진입점) - `tools/`

전체 카탈로그 + 통계 + 흡수된 archive 매핑은 [[tools/README]] — 아래는 요약 목록(신규 tool 추가 시
[[tools/README]]와 함께 갱신).

### Company (1)
- [[company]] - 기업 식별 + 최근 공시 인덱스

### Meeting (2)
- [[shareholder_meeting_notice]] - 주총 소집공고 사전 데이터
- [[shareholder_meeting_results]] - 주총 의결 결과 사후 데이터

### Data (14)
- [[ownership_structure]] - 최대주주/특수관계인/5%/control_map
- [[financial_metrics]] - DART 재무 4 endpoint 통합
- [[corp_gov_report]] - 기업지배구조보고서 15지표
- [[director_board]] - 이사 인당보수·보수한도 소진율·재직/사퇴 변동·개별보수·미등기·이사회 출석률·원문 각주 해소 (260708 신설, 260709 각주정밀도·출석률·성능 검수)
- [[dividend]] - 배당 사실 + 분기별 breakdown
- [[treasury_share]] - 자사주 결정/결과/신탁/소각
- [[value_up]] - 기업가치제고계획
- [[corporate_restructuring]] - 합병/분할/주식교환·이전
- [[dilutive_issuance]] - 유상증자/CB/BW/감자
- [[proxy_contest]] - 위임장/소송/5%/vote_math
- [[corporate_deals]] - 지분 인수·매각(타법인주식) + 단일공급계약 (구 related_party_transaction)
- [[order_contracts]] - 단일판매·공급계약
- [[risk_events]] - 리스크 이벤트 활성 3종 (중대재해/횡령배임/생산중단·영업정지, 파생·회생·해산 mute)
- [[valuation]] - PER·PBR·배당수익률(기업 심층) + 시장/섹터/종목 히스토리 (260705 신설)

### Evidence (1)
- [[evidence]] - rcept_no -> 공시일/소스/뷰어 URL

### Action (1)
- [[proxy_advise_before_meeting]] - 주총 전 의결권 자문

---

## Architecture (6 + audits 28 + fixes 3 + data archive)

### 시간순 인덱스 (READMEs)
- [[architecture/audits/README]] — Audits 시간순 인덱스 (28 entries)
- [[architecture/audits/data/README]] — Audit raw data 인덱스
- [[ralph/README]] — Ralph plans 시간순 인덱스 (24 plans)
- [[lessons/README]] — Lessons 인덱스
- [[decisions/README]] — Decisions 인덱스
- [[tools/README]] — Tools 카탈로그 (사용자 진입점)

### 시스템 설계 (6)
- [[architecture/data-collection]] - OPM 전수 데이터 수집 entry point + 파싱 방법 (DART/KIND/Naver/Upstage/정적 JSON, 14 섹션 639줄)
- [[architecture/3-tier-fallback]] - XML -> PDF -> OCR 3단계 파싱 전략
- [[architecture/matrix-system]] - 12 카테고리 매트릭스 (100 dim, 76 빙고 패턴) + 자동 채점 v1.3 (통합 페이지)
- [[architecture/proxy-voting-decision-tree]] - 3개 소스 통합 의결권 행사 판단 프레임워크
- [[architecture/pipeline-architecture]] - 199개 기업 v4 JSON 생성 배치 파이프라인
- [[architecture/lessons-learned]] - MCP 개발 7가지 핵심 교훈 (v1->v2 회고, 2026-04-19)

### audits/ (10 시점별)
- [[260411_2023_audit_personnel-벤치마크-v1]] - personnel XML 878명 전수 벤치마크 (SUCCESS 79.4%)
- [[260421_2308_audit_parsing-10tool-20기업]] - 10 data tool × 20 회사 파싱 건강도 audit
- [[260422_0005_audit_parsing-14scope-15기업]] - 확장 audit: 14 scope × 15 회사 + 필드 채움률 + corp_gov_report 포함
- [[260429_0216_audit_parsing-200기업-v1]] - 196 기업 (KOSPI 100 + KOSDAQ 96) × 11 tool 전수 audit (exact 66.9%)
- [[260429_0912_audit_parsing-200기업-v2-no_filing]] - audit v2: no_filing 분리 + 진짜 partial 측정 (4-class)
- [[260429_0942_audit_arithmetic-21지표]] - 산술 정확성 audit (21 지표)
- [[260429_2053_audit_personnel-878명]] - personnel 파서 SUCCESS 79->95%
- [[260510_financial_metrics_audit_통합정리]] - financial_metrics audit 통합 정리
- [[260501_2030_audit_financial_metrics-200기업]] - financial_metrics 전수 audit (KOSPI 100 + KOSDAQ 100, exact 96.9%, 자본잠식 2건 검출, 5분)
- [[260502_2300_audit_advise-recap-vote]] - action tool 재편 sanity (advise/recap 신규 + 18→17 회귀 0 + 매핑 3-tier 분류)

### fixes/ (3 시점별)
- [[260427_1145_fix_ownership-stockknd]] - ownership_structure 17건 partial -> 0 fix (stock_knd 변형 positive matching + 3-tier fallback, regression 0)
- [[260429_0216_fix_speed-optimization-9건]] - 9건 sequential -> asyncio.gather 적용 (proxy_contest 4x, ownership 3x, dividend 3x)
- [[260429_0942_fix_corp_gov_report-financial-holding]] - corp_gov_report 금융지주 18건 partial -> 0 fix (financial_form 감지)

---

## Decisions (18)

### 정책 + 매트릭스
- [[open-proxy-guideline]] - OPM 자체 의결권 행사 정책 v1.2 (12 카테고리 116 룰 + 11 novel topics + 2026 신법 7개 + §382의3 cross-cutting)
- [[260429_0059_decision_voting-policy-consensus-matrix]] - 7 운용사 의결권 정책 합의/이견 매트릭스 (79 토픽, 12 카테고리)
- [[260429_0059_debate_opm-guideline-7전문가]] - 7 전문가 토론 + v1.0 -> v1.1 -> v1.2 결정 transcript
- [[260429_0216_improvement_turnkey-11agent]] - 11 agent 병렬 작업 통합 (G1-G4 + 7 페르소나 + 모더레이터)
- [[260505_1700_decision_inside-director-performance-matrix]] - 사내이사 재직 중 성과 매트릭스 2x3 도입 (status quo bias mitigation, KOSPI 100 + KOSDAQ 50 검증)
- [[260505_1900_decision_compensation-retirement-split]] - 보수한도/퇴직금 분리 (이사 13 / 감사 11 / 퇴직금 12 분기 + 정관 hybrid + 3 ralph 검증 G1 모두 99%+/G3 100%/G4 100% — KOSPI 200+KOSDAQ 50 n=226)
- [[260506_0030_decision_notice-scope-cleanup-prov-financials]] - shareholder_meeting_notice scope 정리 (6→5) + provisional_financial_statement 독립 모듈 + prov_financials scope 신설 (data/action layer 정합)
- [[260506_2330_decision_v1-dead-parsers-archive]] - v1 dead 3 parser (treasury_share / capital_reserve / financials) logical archive (코드 보존, decision-only)

### Tool 정책 + 변경 이력
- [[tool-changelog]] - Tool 제거/통합/리네임 이력 (41->32->17개, 이유 포함)
- [[tool-추가-검증-정책]] - release_v2 신규 tool 추가 시 action/data별 검증 매뉴얼 + 화이트리스트 체크
- [[cross-domain-체이닝]] - AGM/OWN/DIV 도메인 간 tool 연결 맵 + 시나리오
- [[free-paid-분리]] - MCP(public) + Pipeline(private) 2-repo 구조

### 파서 + 데이터 소스 결정
- [[XML-vs-PDF]] - XML 1차 + PDF 보강이 최적, PDF-only는 역효과
- [[BeautifulSoup-파서-선택]] - lxml 채택 (30% 빠름, 결과 동일)
- [[LLM-fallback-설계]] - 정규식 -> zone 추출 -> LLM 하이브리드 전략
- [[pblntf-ty-필터링]] - DART 검색 시 pblntf_ty 필수 지정, 전체 순회 금지 (D/E/I 코드표)
- [[DART-KIND-매핑-화이트리스트-2026-04]] - KIND 병행 허용 공시 화이트리스트 + false match 사례
- [[파서-성능-추이]] - 2026-03-20부터 04-06까지 8개 파서 개선 이력

---

## Rules

### Concepts (31) - `rules/concepts/`
한국 자본시장 도메인 개념. tool 본문에서 link only.

#### 배당
- [[배당성향]] · [[배당수익률]] · [[시가배당률]] · [[분기배당]] · [[특별배당]] · [[감액배당]] · [[당기순이익]] · [[자본준비금]]

#### 지분 + 주체
- [[지분구조]] · [[최대주주]] · [[대주주]] · [[동일인]] · [[특수관계인]] · [[5%-대량보유]] · [[소액주주]] · [[자사주]]

#### 의결권 + 주총
- [[의결권]] · [[집중투표]] · [[감사위원-의결권-제한]] · [[참석률]] · [[정관변경]] · [[주주제안]] · [[보수한도]] · [[소진율]]

#### 분쟁 + 환원
- [[프록시-파이트]] · [[위임장-권유]] · [[경영권-방어]] · [[주주환원]]

#### 시스템 메타
- [[v4-스키마]] · [[시간순서-규칙]] · [[파서-판정-등급]]

### Disclosures (41) - `rules/disclosures/`
DART/KIND 공시 유형. 공시명 = 페이지명.

#### 코드체계
- [[공시유형코드체계]] - pblntf_ty(A-J) + pblntf_detail_ty(I001 등) → 실제 공시 매핑, 6사 실증

#### 주총 + 정기보고서
- [[주주총회소집공고]] · [[주주총회결과]] · [[사업보고서]] · [[반기보고서]] · [[분기보고서]]

#### 배당 (6)
- [[현금배당결정]] · [[주식배당결정]] · [[배당기준일결정]] · [[분기배당결정]] · [[감액배당결정]] · [[배당공시유형]]

#### 자사주 (6)
- [[자기주식결정]] · [[자기주식취득결정]] · [[자기주식처분결정]] · [[자기주식소각결정]] · [[자기주식신탁결정]] · [[자기주식의무소각-2026신법]]

#### 지분 + 위임장
- [[대량보유상황보고서]] · [[위임장권유참고서류]] · [[최대주주등소유주식변동신고서]] · [[최대주주변경]] · [[임원·주요주주특정증권등소유상황보고서]]

#### 분쟁
- [[소송등의제기]] · [[경영권분쟁소송]]

#### 발행 + 재편
- [[유상증자결정]] · [[전환사채발행결정]] · [[신주인수권부사채발행결정]] · [[감자결정]]
- [[회사합병결정]] · [[회사분할결정]] · [[회사분할합병결정]] · [[주식교환·이전결정]]

#### 거래 + 거버넌스
- [[타법인주식및출자증권거래]] · [[단일판매공급계약체결]] · [[기업지배구조보고서]] · [[기업가치제고계획]]

### Laws (3) - `rules/laws/`
- [[rules/laws/상법-2025-2026-종합]] - 2025-2027 상법 개정 시행 일정
- [[rules/laws/주총방어-시나리오-4가지]] - 상법 개정 대응 방어 전술 4가지 (미래에셋증권)
- [[rules/laws/주총체크리스트-2026]] - 주총 체크리스트 9개 + 상법 개정 타임라인

---

## Archive (48)

흡수된 페이지 (역사 보존, 신규 사용자 안 봐도 OK).

### archive/analysis/ (18)
release_v2 검증 예시 + 설계 문서. 현재 17 public tools/* 페이지와 archive 이력으로 흡수.
[[release_v2-tool-아키텍처]] · [[release_v2-public-tool-검증-매트릭스]] · [[release_v2-action-tool-검증-초안]] · [[KIND-주총결과]] · [[cash-shareholder-return-2026-04-29]] · [[total-shareholder-return-2026-04-29]] 등

### archive/comparison/ (3)
- [[stkrt-vs-ctr_stkrt]] · [[회사측-vs-주주측-위임장]] · [[배당-자사주-공시-종합]]

### archive/decisions/ (2)
matrix-system.md 통합으로 흡수.
[[archive/decisions/decision-matrix-design]] · [[archive/decisions/matrix-auto-scoring-2026-04-29]]

### archive/entities/ (9)
DART/KIND/Upstage 등 외부 entity 페이지. CLAUDE.md path만 archive 보존.
[[archive/entities/DART-OpenAPI]] · [[archive/entities/KRX-KIND]] · [[archive/entities/네이버-금융]] · [[archive/entities/Upstage-OCR]] · [[archive/entities/OpenProxy-MCP]] · [[archive/entities/OpenProxy-AI]] · [[archive/entities/국민연금]] · [[archive/entities/FastMCP]] · [[archive/entities/opendataloader]]

### archive/sources/ (6)
구 RULE 파일 요약 + taxonomy.
[[agm-tool-rule]] · [[div-tool-rule]] · [[own-tool-rule]] · [[dart-kind-disclosure-taxonomy]] · [[devlog]] · [[주총방어전략-2026]]

### archive/templates/ (1)
- [[tool-추가-검증-템플릿]] - 신규 data/action tool 제안 템플릿

### archive root (8)
구 readme + case rule + 단일 disclosure 페이지.
- [opm-readme](archive/opm-readme.md) · [opa-readme](archive/opa-readme.md) · [benchmark](archive/benchmark-personnel-results.md)
- [agm-case-rule](archive/agm-case-rule.md) · [own-case-rule](archive/own-case-rule.md) · [div-case-rule](archive/div-case-rule.md)
- [임원주요주주](archive/임원주요주주특정증권등소유상황보고서.md) · [자기주식취득처분결정](archive/자기주식취득처분결정.md) · [정정공시](archive/정정공시.md)
