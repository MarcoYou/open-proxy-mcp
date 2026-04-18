---
type: log
title: Operation Log
---

# Operation Log

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
