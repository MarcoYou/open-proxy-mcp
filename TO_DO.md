# Homework

## 해야 할 일

### DXT 패키징 (free-open-proxy 배포)
- [ ] manifest.json 작성 (user_config: DART API 키, Upstage API 키)
- [ ] 프로젝트 구조를 DXT 형태로 재구성
- [ ] icon.png 제작
- [ ] DXT 빌드 + 테스트 (Claude Desktop 설치 확인)
- [ ] 배포 (GitHub Release에 .dxt 파일 첨부)

### 파서 미해결 케이스
- [ ] 안건번호=후보자이름 17건 — 파서 근본 수정 필요
- [ ] 정관텍스트=후보자 4건 — 파서 수정
- [ ] 경력 없는 후보자 13건 — 나. 서브섹션 매칭 실패
- [ ] 미해결 경력 24건 — LLM fallback 구현
- [ ] BNK금융지주/기업은행 비표준 구조 — LLM fallback 대상
- [ ] 직책/대기업 키워드 리스트 data/ 디렉토리 분리

### agm_personnel 개선
- [ ] careerCompanyGroups 회사명 분리 정확도 개선 (부서명이 회사명에 포함되는 이슈)
- [ ] DART 원본에서 공백 없이 합쳐진 경력 내용 분리 — `<p>`/`<br>` 없이 순수 텍스트인 기업 (DB손해보험 민수아 등). 법인격 `(주)/(재)` 앵커로 분리 후 재조합 접근 가능하나 오분리 위험. LLM fallback도 고려

### agm_proposals (향후)
- [ ] 주주제안 안건 정규화 (의안 제목 + 요지 텍스트)
- [ ] 권고적 주주제안 구조 (LG화학 제3호 — 팰리서 등 행동주의 펀드)
- [ ] 프록시 파이트 분석 연계

### paid-open-proxy 파이프라인
- [x] ~~run_pipeline.py에 PDF/OCR fallback 통합 (XML → PDF → OCR 자동 체이닝)~~
- [x] ~~filing_tracker.json — 199개 기업 소집공고 이력 트래킹 (정정/최초/날짜)~~
- [ ] run_pipeline에서 filing_tracker 연동 (최신 rcept_no 자동 사용, 종료 기업 스킵)
- [ ] filing_tracker 자동 갱신 스크립트
- [ ] 파서별 품질 판정 함수 (CASE_DEFINITION 기준)
- [ ] 최선 결과 선택 로직 (XML vs PDF 비교)
- [ ] DB 스키마 설계 (향후, PostgreSQL)
- [ ] API 서버 (향후, FastAPI)

### ownership tool 개선
- [ ] own_major에 최대주주등소유주식변동신고서 연동 (KIND 크롤링, ticker 기반 검색)
- [ ] own_block 보유목적 변경 시계열 추적 (동일 보고자 이력 비교)
- [ ] own_latest에 3대 주체 분류 태깅 (최대주주+특관인 / 국민연금 / 기관투자자)
- [ ] 집중투표 분석 tool 체이닝 (agm + own 연계, 의결권 시뮬레이션)

### free-open-proxy 개선
- [x] ~~23개 MCP tool 등록 (_xml/_pdf/_ocr + guide)~~
- [x] ~~agm_guide tool — AI용 사용 가이드~~
- [x] ~~CASE_DEFINITION을 agm_guide 응답에 포함 (AI 판단 기준)~~
- [x] ~~own_* 지분 구조 tool 7개 (총 39개 tool, KOSPI 200 전수조사 199/199 OK)~~
- [x] ~~agm_result tool — KIND 크롤링 기반 주총 투표결과 + 추정참석률 (총 40개 tool)~~
- [x] ~~KOSPI 200 주총결과 전수 크롤링 → pipeline_result/ 199개 JSON~~
- [ ] LLM fallback tool — XML 원문 + CASE_DEFINITION으로 AI 보강 (향후)

### PDF 파싱 보조 소스
- [x] ~~opendataloader-pdf 설치 + DART PDF 파싱 품질 테스트~~
- [x] ~~PDF 파서 5개 구현 (pdf_parser.py) — comp 99.5%, pers 97.9%, BS 97.9%, IS 95.7%, aoi 99%, agenda 98%~~
- [x] ~~198개 전체 PDF 다운로드 + opendataloader 파싱~~
- [x] ~~Upstage OCR fallback 구현 + 11건 전부 성공 검증~~
- [x] ~~PDF 파서 남은 실패 → Upstage OCR로 100% 해결 확인~~

### 이미지 인덱싱 + OCR 파이프라인
- [ ] parse_agenda_details에서 이미지 메타데이터 인덱싱 (파일명, 위치, 카테고리)
  - BSM → `{"type": "image", "category": "bsm"}`, 확인서 → 스킵
- [ ] ZIP 내 이미지 바이너리 추출 (client.py)
- [ ] Tesseract + EasyOCR 벤치마크 (KT&G BSM으로)
- [ ] 별도 OCR tool → 결과를 agenda detail에 병합

### 기업 검색 개선
- [ ] 영문 브랜드명(KT&G 등) → corp_code 매핑 지원 (별칭 또는 영문명 API)

### API 최적화
- [ ] search_filings_by_ticker 결과 캐싱 (같은 ticker 중복 호출 방지)
- [ ] parse_agenda_items 결과를 _doc_cache에 저장 (CPU 중복 파싱 방지)

### 프론트엔드 연동
- [ ] v3 스키마 전체 호환 (classification, governanceAnalysis, checklist 등)
- [ ] 주총 이력 관리 (정기/임시, 연도별) — DB에서 처리
- [ ] i18n 한영 토글 — `feat/eng-added` 브랜치에 아카이브, 런타임 에러 수정 후 재적용

---

## 완료

### ~~agm_compensation~~ ✓
- ~~보수한도 안건 파싱 (당기 한도/전기 실지급/전기 한도)~~
- ~~이사의 수(사외이사수) 추출, 금액 원 단위 변환~~
- ~~이사/감사 분리 감지 (GKL 등)~~
- ~~KOSPI 200 배치 테스트 — 188/191 (98.4%)~~
- ~~실패 3건: 기업은행/한국금융지주/현대백화점 — parse_agenda_details 자체의 비표준 구조~~

### ~~PDF 다운로드~~ ✓
- ~~`get_document_pdf(rcept_no)` — DART 웹에서 PDF 다운로드 (dcm_no 자동 추출)~~
- ~~Rate limiter 적용 (API 0.1초, 웹 2초 최소 간격)~~

### ~~파서 개선~~ ✓
- ~~811건 배치 테스트 + 5가지 개선 (93%→97-98%)~~
- ~~KOSPI 200 검증 (189개) + 패턴 개선 12건 → agenda 99.5%, fin 97.4%, pers 98.9%, aoi 97.8%~~
- ~~bs4 1단계 경력 파싱 + regex 2단계 fallback~~
- ~~2자리 연도, 하이픈 구분자, 아포스트로피, 역순 기간 보정, 빈 content 제거~~
- ~~재무제표 library fallback (section 직계 테이블)~~
- ~~agenda details fallback (안건 마커 없는 가.나.다. 섹션)~~
- ~~rowspan 경력 파싱 (72개 기업), YYYY.MM/YYYY년M월/아포스트로피 기간~~
- ~~소프트패턴: 영문)+한글, 한글+㈜ 경계 분리~~
- ~~하드패턴: 대기업 그룹명 뒤 직책+회사명 분리~~
- ~~전수점검: 비정상 이름 6건, 경력 없음 43건, 크래시 1건 수정~~
- ~~Claude API (Anthropic) fallback 추가 — `_call_claude()` 구현 완료~~

### ~~agm_aoi_change~~ ✓
- ~~정관변경 비교 테이블 파싱 (변경전/변경후/사유)~~
- ~~세부의안 병합 (additionalClauses)~~
- ~~프론트엔드 접이식 카드 UI~~
- ~~세부의안 번호 매핑 — `<p>` 헤더에서 감지 (LG화학 패턴)~~
- ~~agm_agenda 세부의안 전달 (parse_aoi sub_agendas 파라미터)~~

### ~~agm_personnel~~ ✓
- ~~이사/감사/감사위원 선임·해임 정규화~~
- ~~v3 스키마 호환 (camelCase 키명)~~
- ~~세부경력 기간/내용 분리 (careerDetails)~~
- ~~결격사유 3필드 분리 (eligibility)~~
- ~~직무수행계획/추천사유 전문 반환~~
- ~~확인서 텍스트 자동 제거~~
- ~~41건 테스트 에러 0건~~
- ~~경력 내용 분리 — 법인격 패턴 (주)/(재)/법무법인 앞에서 split~~

### ~~agm_financials~~ ✓
- ~~재무상태표/손익계산서 정규화 (96% 성공)~~
- ~~자본변동표 + 자사주 취득/소각 플래그~~
- ~~이익잉여금처분계산서 + has_dividend 플래그~~
- ~~연결/별도 자동 분류~~
- ~~컬럼 정규화 (4/5/6컬럼 → 통일)~~
- ~~format_krw() 단위 변환 유틸~~

### ~~agm_steward~~ ✓
- ~~종합 오케스트레이터 (info + agenda + financials highlights + personnel summary)~~

### ~~agm_corrections~~ ✓
- ~~정정 전/후 비교 + 메타데이터~~

### ~~agm_items~~ ✓
- ~~110건 100% 성공~~
- ~~안건 마커 없는 library fallback (카테고리 제목 사용)~~

### ~~안건 파서 (parser.py)~~ ✓
- ~~정규식 기반 안건 트리 파싱 (표준 + 괄호형 패턴)~~
- ~~섹션 기반 파싱 — 정정공고 범용 대응~~
- ~~250개 기업 대규모 테스트 — 93% 처리율 (bs4+regex)~~
- ~~validate_agenda_result() 품질 검사~~
- ~~하이브리드 LLM fallback (gpt-5.4-mini, use_llm 옵션)~~
- ~~hard fail / soft fail 구분~~
- ~~BeautifulSoup + lxml 기반 섹션 추출 (text regex fallback 유지)~~
- ~~lookahead 경계 강화 (하위안건/테이블헤더/정관변경)~~
- ~~`제` 없는 비표준 하위안건 번호 패턴 대응 (lookahead 경계로 해결)~~
- ~~후보자 테이블 제목 분리 (테이블 헤더 경계로 해결)~~

### ~~안건 상세 파싱~~ ✓
- ~~BeautifulSoup으로 HTML 직접 파싱 — 테이블은 마크다운 테이블, 텍스트는 텍스트~~
- ~~KT&G 8개 안건 검증 통과~~
- ~~다기업 호환성 수정 (삼성전자/현대차 패턴, section-4 재귀 파싱)~~
- ~~6개 기업 cross-check 통과~~

### ~~FastMCP 서버 구축~~ ✓
- ~~server.py 작성 (FastMCP 진입점)~~
- ~~tools/shareholder.py 작성~~
- ~~get_document()에서 이미지 파일명 본문에서 제거 + 별도 목록으로 분리 반환~~

### ~~연동 테스트~~ ✓
- ~~Claude Code MCP 연동 (stdio)~~
- ~~Claude 웹 Cowork 커넥터 연동 (SSE + ngrok)~~

### ~~프론트엔드 연동~~ ✓ (부분)
- ~~OpenProxy 프론트엔드에 JSON 연결 (KT&G MCP v3)~~
- ~~재무제표 로우 데이터 테이블 + 하이라이트 카드~~
- ~~단위 변환 표시 (백만원 × 값 → 억/조, 단위:원 유지)~~
- ~~KT&G/삼성전자/NAVER/LG화학 실데이터 반영~~
- ~~재무 계층 트리 (인덴트 + 접이식)~~
- ~~변화율 컬럼 (초록/빨강)~~
- ~~계정명 접두사 제거~~
- ~~주석 컬럼 숨김~~
- ~~정관변경 접이식 카드 UI~~
- ~~주총 종료 자동 감지~~
- ~~mockData.ts → pipeline 자동 로드 전환 (import.meta.glob)~~
- ~~KOSPI 200 전체 pipeline JSON 생성 (199개)~~
