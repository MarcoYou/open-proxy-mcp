---
type: log
title: Operation Log
---

# Operation Log

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
