# Dev Log

## 2026-04-04

### free/paid 2-repo 분리
- open-proxy-mcp (public): 40개 MCP tool + 파서 + API 클라이언트
- open-proxy-ai (private): 파이프라인 + 프론트엔드 + 데이터
- run_pipeline_v4.py, data/ git에서 제거 (paid 전용)
- pyproject.toml optional deps 분리 (llm, pdf)
- OpenProxy 서브모듈 제거

### 아키텍처 정리
- .omc/, egg-info/, samples/ gitignore
- 프로젝트 레벨 ship.md 제거 (글로벌만 유지)
- OPA: run_pipeline_v4.py -> run_pipeline.py (버전 번호 파일명에서 제거)
- OPA: 레거시 문서/빌드 아티팩트 제거

### OMC (oh-my-claudecode) 플러그인 설치
- HUD 상태바 설정 (5h/wk 사용량, ctx%, session)
- Context7 + Exa + Filesystem MCP 서버 추가
- Agent Teams 활성화 (3 agents, executor)
- 이후 기본 비활성화 (필요시 수동 활성화)

## 2026-04-01

### agm_result tool + KIND 크롤링 기반 주총결과 파싱
- KIND 크롤링으로 정기주주총회결과 HTML 파싱 (다운로드 불필요)
- DART rcept_no → KIND acptno 변환 패턴: 8번째 이후 "80" → "00"
- KIND setPath regex 수정 (빈 목차 URL 대응)
- 섹션 분리: span 태그 기반 (정기주총결과/안건세부/이사선임/감사위원/집중투표 등)
- 투표 결과 파싱: 안건별 가결/부결, 찬성률(발행/행사기준), 반대기권률
- 추정 참석률: 발행기준/행사기준 역산, 보통결의 최빈값
- 총 40개 tool

### KOSPI 200 주총결과 전수 크롤링 (199/199 OK)
- save_agm_results.py → pipeline_result/ 디렉토리 저장
- 평균 참석률 73.3%, 중위 75.1%, 최소 30.4%(호텔신라), 최대 94.3%(삼성카드)
- 섹션 표준화: 주총결과(100%), 안건세부(100%), 감사위원(85%), 집중투표(62%)

### 참석률 역산 분석
- 전체 참석률 = 발행기준 찬성률 / 행사기준 찬성률
- 최대주주 제외 참석률 = (전체참석 - 특관인지분) / (의결권주식 - 특관인지분)
- 삼성전자: 전체 74.0%, 일반주주 68.9% (삼성 IR 수치와 일치)
- 감사위원 선임 안건은 3% 의결권 제한으로 분모 변경 → 참석률 차이 발생
- 정확한 행사 주식수는 DART에 미공시 → 추정치 제공

### own 오케스트레이터 개선
- 사업보고서 신고 기준 vs 실질 최다보유자 차이 명확화
- 주요 특수관계인 1%+ 상세 표시
- 5% 대량보유 "보고자+특별관계자 합산 기준" 명시
- own docstring 보강 (baseline + 수시 변동 구조)

## 2026-03-31

### own_* 지분 구조 tool 7개 개발
- own: 오케스트레이터 (최대주주+특관인 합계, 주식총수, 자사주 비율, 소액주주, 5% 대량보유)
- own_major: 최대주주+특관인+변동이력
- own_total: 주식총수+자사주+유통주식+소액주주
- own_treasury: 자사주 기말 보유 (사보 baseline)
- own_treasury_tx: 취득결정/처분결정/신탁체결/해지 (DS005 4개 API)
- own_block: 5% 대량보유 + DART 원문 PUR_OWN 태그로 보유목적 파싱 (단순투자/일반투자/경영참여)
- own_latest: 전 주주 최신 스냅샷+변동 집계
- client.py: DART ownership API 11개 + KIND 크롤링 (1-3초 랜덤 간격) 추가
- KOSPI 200 전수조사: **199/199 전부 OK** (205초, FAIL 0, SKIP 0)
- 총 39개 tool (기존 32 + own 7)

### DART 지분 API 조사 (삼성전자/KB금융/KT&G)
- DS002 (정기보고서): hyslrSttus, hyslrChgSttus, mrhlSttus, tesstkAcqsDspsSttus, stockTotqySttus
- DS004 (수시보고): majorstock (5% 대량보유), elestock (임원소유)
- DS005 (주요사항): tsstkAqDecsn, tsstkDpDecsn, tsstkAqTrctrCnsDecsn, tsstkAqTrctrCcDecsn
- majorstock API에 보유목적 필드 없음 → DART document.xml의 PUR_OWN 태그로 해결
- DART rcept_no ≠ KIND acptno (같은 보고서라도 번호 다를 수 있음)

### KT&G 집중투표 사례 분석
- 최대주주 IBK 8.06%(단독), 특관인 합계 16.12%
- 국민연금 7.50%(최대주주보다 높음), GIC 5.03%, BlackRock 5.01%
- 자사주 12.03% (의결권 없음), 소각 적극 진행 중 (2024-2025 1조원+)
- 재단/기금/우리사주/임원진 지분으로 경영권 방어하는 구조

### agm_steward → agm 리네임
- 오케스트레이터 tool명 간소화

### _pdf/_ocr docstring 보강
- _pdf 8개: "정상 기준은 _xml과 동일" 참조 문구 추가
- _ocr 8개: "UPSTAGE_API_KEY가 .env에 없으면 에러" 명시
- _ocr 8개: fallback 체인 끝 안내 — "OCR도 실패하면 AI가 원문 기반으로 직접 재구성"
- 32개 tool docstring 전체 보강 완료

## 2026-03-30

### _xml 파서 docstring 품질 기준 구체화
- 8개 _xml 파서 docstring을 1줄 압축에서 멀티라인 구조로 확장
- financials: "BS 5행+, IS 3행+" → BS 자산/부채/자본 구조 + IS 필수항목 7개 명시
- personnel: 이름 2-5자(영문 병기), 경력 각 100자 이내 기준 명시
- aoi_change: ---생략/삭제가 정상임을 명시
- compensation: headcount, 소진율 자동 계산 안내 추가
- fallback 체인 끝에 "AI가 원문 기반 재구성" 단계 추가 (OCR 이후 LLM)
- "agm_agenda_xml 결과에 [안건유형] 없으면 빈 결과는 정상" 구체화
- 32개 tool docstring 전면 업데이트 완료 (_xml 8개 + _pdf 8개 + _ocr 8개 + 유틸 8개)

## 2026-03-29

### filing_tracker.json — 소집공고 이력 트래킹
- 199개 기업 × filing 이력 (최초/정정/정정2차...)
- 정정 있는 기업 69개, 2회+ 11개 (삼성바이오로직스 3회 최다)
- 주총 종료 177개 / 예정 22개
- run_pipeline에서 최신 rcept_no 자동 사용 가능

### 자기주식/자본준비금/퇴직금 파서 + 침범 방지
- parse_treasury_share_xml: 보유/처분/소각 (93.6%, 침범 1건)
- parse_capital_reserve_xml: 감소 금액 + reducedCapital 플래그 (100%, 침범 0건)
- parse_retirement_pay_xml: 현행/개정안 테이블 (93.3%, 침범 0건)
- 31개 MCP tool (_xml/_pdf/_ocr)
- 전수 침범 검사 실시 — 본문 fallback 제거로 침범 최소화
- 감액배당: reducedCapital 플래그 (17개 기업 자본준비금 감소)

### README 영문/한국어 분리
- README.md: 영문 (홍보용, 31 tools, 3-tier fallback)
- README_KR.md: 한국어 (상세 설명)

### free-open-proxy end-to-end 완료
- agm_guide → CASE_DEFINITION 읽기 → tool 호출 → 결과 검증 → fallback 안내 흐름 확인
- SUCCESS: 삼성전자 comp (450억원 바로 답변)
- HARD_FAIL: 기업은행 pers (XML 0명 → PDF fallback)
- SOFT_FAIL: 한화 pers (XML 270자 병합 → PDF 6건 분리)
- AI 검증 + 포맷 보정 지시 agm_guide에 추가

### Upstage OCR fallback
- opendataloader 실패 시 → 키워드로 페이지 특정 → Upstage OCR → 재파싱
- 11건 실패 케이스 전부 OCR에서 성공 (100%)
- `ocr_fallback_for_parser()` 구현 (pdf_parser.py)
- SK스퀘어 comp: `(단위:억원)` + `100` = 100억원 해결
- BGF리테일/KCC/LIG넥스원/포스코DX pers: 후보자 경력 전부 추출
- GS/SK아이이테크놀로지/TKG휴켐스/세아베스틸지주 BS: 테이블 정상 추출
- SK케미칼/지역난방공사 aoi: 정관변경 조항 추출

### 안건 tree 기반 판정 원칙
- CASE_DEFINITION 업데이트: 해당 안건 없으면 실패 아님
- 실제 성공률 재계산: comp 99.5%, pers 97.9%, BS 97.9%, IS 95.7%, aoi 99.0%, agenda 98.0%

### 아키텍처 결정 — free/paid 분리

**free-open-proxy (MCP only)**:
- XML → LLM 보강(유저 AI 토큰) → PDF → OCR 순서
- AI가 유저와 대화하면서 점진적으로 fallback
- agm_guide + CASE_DEFINITION이 AI 판단 기준
- 23개 MCP tool (_xml/_pdf/_ocr + guide)

**paid-open-proxy (API + Frontend)**:
- XML → PDF → OCR → LLM(provider API 토큰) 자동 체이닝
- 배치 파이프라인(run_pipeline.py)으로 미리 최선 데이터 생성
- DB 저장, 프론트엔드에서 조회만

**공유 레이어**: parser.py, pdf_parser.py, dart/client.py, CASE_DEFINITION.md
**DB/캐시**: free는 파일캐시, paid는 DB (나중에 PostgreSQL)

### PDF 파서 3회 개선 루프 (pdf_parser.py)
- 5개 파서 구현: parse_compensation_pdf, parse_personnel_pdf, parse_financials_pdf, parse_aoi_pdf, parse_agenda_pdf
- 파서 네이밍 _xml/_pdf 구분 리팩토링

**성능 추이 (v1 → 최종, KOSPI 200 198건):**
- compensation: 88.9% → 97.5%
- personnel: 89.9% → 93.9% (후보자 중복 67건 → 0건)
- financials BS: 82.3% → 96.0%
- financials IS: 12.6% → 93.9%
- aoi: 76.3% → 97.0% (멀티라인 셀 합치기)
- agenda: 80.3% → 97.5% (장식 패턴 15종 대응)

**핵심 개선:**
- IS 감지: BS 이후 매출/영업이익/순이자 계정으로 자동 판별
- BS 오감지 방지: 거래내역 테이블 제외
- aoi 멀티라인: |로 시작하지만 끝나지 않는 행 연결
- agenda 장식: ○●■, (N), ①②, 가.나., 1) 등 다양한 prefix
- personnel dedup: 같은 이름은 경력 많은 쪽 유지

**남은 실패 (파서로 해결 불가 — 원본 구조 문제):**
- agenda 5건, comp 5건, personnel 12건, BS 8건, IS 12건, aoi 6건
- PDF 원본에 데이터 자체가 없거나 opendataloader 변환 품질 문제

### XML vs PDF 비교 분석
- 198개 전체 비교: xml_vs_pdf_comparison.json
- XML 1차 + PDF 보강이 최적 전략으로 확인
- PDF-only 전환 시 financials/agenda에서 역효과

### 프론트엔드
- DART 원문 보기 버튼 (개요탭, rceptNo 직링크)
- pipeline JSON 198개에 rceptNo 패치

## 2026-03-28

### agm_compensation 신규 tool
- parse_compensation() 구현 — DART 표준 서식 (당기 한도/전기 실지급) 파싱
- _parse_krw_amount() — 억원/백만원/천원 → 원 단위 변환
- KOSPI 200 배치 테스트: 188/191 (98.4%)
- 실패 3건 (기업은행/한국금융지주/현대백화점) — parse_agenda_details 비표준 구조

### PDF 다운로드 인프라
- get_document_pdf(rcept_no) — DART 웹에서 dcm_no 추출 → PDF 다운로드
- Rate limiter 추가 (API 0.1초, 웹 2초 최소 간격)
- CLAUDE.md에 웹 스크래핑 안전 규칙 명시

### opendataloader PDF 파싱 테스트
- 10건 샘플 → 60건 fallback 대상 전체 다운로드 + 파싱 완료
- **핵심 발견**: XML 실패 케이스에서 PDF가 유효 데이터 추출 성공
  - 기업은행: XML parse_agenda_details 완전 실패 → PDF에서 재무제표/보수한도/정관변경 전부 정상
  - 미래에셋증권: XML 경력 245자 병합 → PDF에서 기간/내용 17건 개별 분리
- opendataloader `table_method="cluster"` + `keep_line_breaks=True` 조합이 DART 테이블에 적합

### CASE_DEFINITION.md 작성
- 5개 파서별 성공 기준 정의 (SUCCESS/SOFT_FAIL/HARD_FAIL)
- LLM fallback용 도메인 설명 + 실제 파싱 예시 포함
- Fallback Decision Matrix (HARD→PDF→LLM, SOFT→PDF 보강)

### fallback 대상 정리
- KOSPI 200 pipeline JSON에서 파서별 실패 추출 → fallback_targets.json
- 61개 기업, HARD 53건, SOFT 112건 (대부분 personnel 경력 이슈)

### 프론트엔드
- DART 원문 보기 버튼 추가 (개요탭 테이블 우측 하단, rceptNo 직링크)
- pipeline JSON 198개에 rceptNo 패치
- run_pipeline.py에 rceptNo 저장 로직 추가

### PDF 전체 다운로드 + 파싱
- KOSPI 200 전체 198개 PDF 다운로드 (랜덤 간격 2-5초, 배치 15-30초)
- opendataloader로 198개 전체 마크다운 파싱 완료
- PDF-only vs XML 비교 분석 준비 완료

### 기타
- agm_aoi → agm_aoi_change 리네임
- blueprint.md → README.md 통합 (관리 포인트 축소)
- TO_DO.md 재구성 (미완료 상단, 완료 strikeout 하단)
- Claude API fallback 이미 구현돼 있었음 확인 → TODO 체크

## 2026-03-25

### 전수점검
- 199개 JSON 구조 유효성 검사 — 크래시 위험 0건 (구조적)
- careerDetails undefined 크래시 발견 (태광산업 제3호) → optional chaining 수정
- 후보자 이슈 전수 조사: 비정상 이름 6건, 경력 없음 43건 (3유형 분류)
- PATTERN_ARCHIVE에 전체 기록

### 경력 content 소프트/하드 패턴
- 소프트: 영문)+한글 경계 분리 (13건, 오분리 0건 검증 완료)
- 소프트: 한글+㈜ 경계 분리
- 하드: 직책 뒤 대기업 그룹명 분리 (20개 하드코딩)

### 오늘의 성과
- 크래시 1건 수정 + 전수점검 체계 확립
- 소프트/하드/LLM fallback 3단계 설계 완성
- PATTERN_ARCHIVE에 이슈 전량 기록

### 오늘의 실패 / 한계
- 안건번호=후보자이름 17건, 정관텍스트=후보자 4건 → 파서 근본 수정 필요
- 경력 없는 후보자 13건 → 파서가 나. 서브섹션을 못 찾는 케이스
- 미해결 경력 24건 → LLM fallback 미구현

## 2026-03-24

### 파서 개선 (추가)
- rowspan 경력 테이블 파싱 — 72개 기업 대응, 22건 이슈 해결
- YYYY.MM / YYYY년M월 기간 형식 지원
- `<p>` 있는 경력 이슈 5건 전부 해결 (한화, 호텔신라, 한일시멘트)

### 프론트엔드
- KOSPI 200 전체 199개 기업 FE 반영 (pipeline 자동 로드)
- mockData.ts → import.meta.glob 전환
- 192개 기업 로고 다운로드 (AlphaSquare CDN)
- CEO/결산월 DART company.json에서 자동 가져오기
- 공고일/정정 칩 표시 (DART 최신 접수일 기준)
- 분석상태: 정상/검토필요/실패 + Radix Tooltip 호버
- checklist=null 크래시 수정
- 경력 레이아웃 폭 100→130px + whitespace-nowrap

### 경력 content 분리 패턴
- 소프트: 영문)+한글 경계 (13건, 오분리 0)
- 소프트: 한글+㈜ 경계
- 하드: 직책 뒤 대기업 그룹명 분리 (한화, 삼성 등 20개)
- PATTERN_ARCHIVE.md 생성 — 소프트/하드/미해결 아카이브 + LLM fallback 설계

### 인프라
- SSE transport 지원 (`--sse` 옵션) — Claude 웹 Cowork 커넥터 연결 성공
- ngrok으로 외부 URL 노출
- venv 환경 구축 (.venv — anaconda 충돌 해결)
- DART company.json API 추가 (get_company_info)
- 후보자 매칭 개선 — 제목 내 이름 우선 매칭 (한화/오리온홀딩스 섞임 수정)
- 법인명 정리 ((주)/주식회사 제거)

## 2026-03-23

### 파서 개선 4건

**1. 삼성전자 제4호(허은녕) 파싱 실패 수정**
- 문제: HTML `<SPAN>` 분리로 제목 줄바꿈 합침 실패 → title이 `)`만 남음
- 수정: `_split_p_lines()` 접두 기호 선택적(`?`) + `의안/안건` 뒤 `)）` 패턴 추가

**2. 경력 상세 연도별 분리 — HTML `<p>` 태그 직접 파싱 fallback**
- 문제: 마크다운 변환 시 `<p>` 구분 소실 → 경력이 하나로 합쳐짐
- 수정: `_extract_career_from_html()` 추가, periods > contents일 때 HTML fallback
- 삼성전자 김용관 1→7항목, 허은녕 0→9항목

**3. 경력 기간 단독연도 감지 + 내용 분리 통합 패턴**
- 문제: NAVER 김희철 기간 `200320032017~2019...` — 단독연도(YYYY) 미감지
- 수정: 기간 정규식에 `\d{4}` 패턴 추가, 내용 분리 `-한글/-(/영문` 통합
- NAVER 김희철 1→4항목, 김이배 3→10항목

**4. 조건부 안건 제목 파싱 — boundary에서 (제N호 조건) 패턴 제외**
- 문제: 코웨이 제5~7호 `(제2-7호 인가되는 경우) 이사 선임의 건` → boundary가 `(제2-7호`를 안건 시작으로 오인
- 수정: boundary의 `\(제\d+` 패턴을 `(제N호 의안)` 형태만 매치하도록 제한 + AGENDA_RE 캡처에 선행 괄호 블록 포함

### 프론트엔드

- 5개 기업 추가: DB손해보험, 코웨이, 고려아연, SK하이닉스, SK
- 삼성전자/NAVER pipeline JSON 재생성 (경력 분리 반영)
- eligibility 표시 정규화: `-`, `해당 사항 없음`, `null` → "없음"/"있음" 2값
- checklist 필드 optional 처리 (null이면 미표시)
- MCP 서버 `.mcp.json` 설정 (gitignore)

### 파서 개선 (추가)

**5. bs4 1단계 경력 파싱 + regex 2단계 fallback**
- `_extract_career_from_html`을 1단계로 승격, 기존 마크다운 경유를 2단계로
- 코웨이: 기간+내용 `<p>` 매핑 성공 (방준혁 3건, 서장원 6건)

**6. 2자리 연도 기간 파싱**
- 4자리 매치 전부 비정상이면 2자리(`YY~YY`) 자동 전환
- 고려아연 최윤범: `22~현21~22` → `2022~현재, 2021~2022` 분리 성공

**7. 기간 구분자 하이픈(-) 지원**
- DB손해보험: `2026-현재2025-2025` → 10개 기간 분리 성공

**8. 아포스트로피 연도, 역순 기간 보정, 빈 content 제거**
- `'20` → 2020, 역순(2010~2007) → 자동 보정, content="-" 제거

**9. agenda details fallback — 안건 마커 없는 섹션**
- DB손해보험: `■ 제3호` 마커 없이 `가.` 서브섹션만 시작하는 경우
- 카테고리 타이틀로 임시 안건 생성 → 8명 후보자 추출

**10. _request_binary XML 에러 처리**
- 정정공고로 rcept_no 무효화 시 BadZipFile 대신 명확한 DartClientError

### KOSPI 200 검증 (189개)

| 항목 | 결과 | 비율 |
|------|------|------|
| agenda valid | 186/189 | 98.4% |
| financials | 183/189 | 96.8% |
| personnel | 180/189, 814 candidates | 95.2% |
| 경력 이슈 | 46/189 기업 | 24.3% |

- agenda invalid 3건: KB금융(dup), DL이앤씨(dup), 호텔신라(count=0)
- financials 실패 6건: 문서 구조 비표준 (한국금융지주, KCC, 한솔케미칼, 농심, 대한유화, 세방전지)
- 경력 이슈 46건: content>100자(한 줄 합쳐짐) 또는 빈 기간 — DART 원본 한계, LLM fallback 대상

### 패턴 개선 (KOSPI 200 검증 후)

**11. agenda boundary — 의결권 안내, 주총 소집 통지 패턴**
- KB금융, DL이앤씨 long_title 해결 → agenda valid 186→188/189

**12. 재무제표 library fallback — section 직계 테이블 탐색**
- 한국금융지주: library 없이 `<p>` + `<table>` 직접 나열 패턴 → financials 183→184/189
- KCC/농심/대한유화/세방전지: 문서 자체에 재무제표 미포함 (파서 한계 아님)

### AOI/Personnel 추가 검증 (189개, 디스크 캐시)

| 항목 | 성공 | 비율 |
|------|------|------|
| AOI (정관변경) | 180/184 | 97.8% |
| Personnel (이사 선임) | 177/179 | 98.9% |

- 실패: BNK금융지주(aoi+pers), 기업은행(aoi), SK케미칼/iM금융지주는 rcept_no 갱신으로 자동 해결

### KOSPI 200 전체 FE 반영

- `run_pipeline.py` 확장: 새 기업 골격 생성(`_build_new_json`) + 기존 업데이트 통합
- 199개 pipeline JSON 생성 (기존 10개 UPD + 신규 189개 NEW)
- `mockData.ts` → pipeline 자동 로드 전환 필요 (다음 작업)

### 인프라
- 디스크 캐시 추가 (cache/ 디렉토리, 세션 간 API 재사용)
- DART API 차단 원인 규명: IP 차단이 아닌 rcept_no 만료 (정정공고 대체)
- KRX Open API 키 저장
- `/ship` 커맨드 서브모듈 경로 규칙 추가

### 오늘의 성과
- KOSPI 200 전체 파서 검증 + 패턴 개선 12건
- agenda 99.5%, financials 97.4%, personnel 98.9%, aoi 97.8%
- 199개 기업 pipeline JSON 생성 + FE push

### 오늘의 실패 / 한계
- BNK금융지주/기업은행: 비표준 library 구조 → LLM fallback 대상
- 경력 content 합쳐짐 46건: DART 원본에 구분자 없음, soft pattern으로 해결 불가
- mockData.ts 자동 로드 미전환

## 2026-03-22

### parse_agenda_items 811건 배치 테스트 + 개선

**배치 테스트 결과 (수정 전)**
- 811건 2026 정기주총 소집공고 전수 검색 (DART API, 10분)
- agenda tree: 753/811 (93%) — invalid 58건, error 0건
- agenda details: 811/811 (100%)
- personnel: 665/811 (82%) — 나머지는 인사안건 없음
- financials: ~99% (수정된 테스트 88건 기준)
- aoi: ~84% — 나머지는 정관변경 안건 없음

**실패 원인 분류 (58건)**
1. count=0 (완전 실패): 16건 — 비표준 소집공고, LLM fallback 대상
2. dup_numbers (중복 번호): ~25건
   - 보고사항 vs 결의사항 번호 충돌 (엑시온그룹, 모아라이프플러스)
   - ※ 비고에서 안건 번호 참조가 안건으로 잡힘 (솔루엠 제3-1-7호)
   - 의안 목록 + 경영참고사항에서 이중 파싱 (태경산업)
   - 원본 공시 오류 (남성 제2-1호 실제 중복)
3. long_title (테이블 혼입): ~15건
   - 후보자 테이블 (`성 명 생년월일`) 혼입 (신라교역)
   - 정관변경 테이블 (`조문 현 행 변 경`) 혼입 (본느)
   - 안건 상세 (`가. 의안의 요지`) 혼입 (메타바이오메드)

**적용한 수정 (parser.py)**
1. ※ 비고 필터링 — 비고 문장 안의 안건 번호 참조를 스킵
2. 보고사항 필터링 — `_is_report_item()` 추가 (감사보고/영업보고/내부회계)
3. 중복 번호 dedup — 같은 number가 중복되면 첫 번째만 유지
4. boundary 패턴 추가 — `성 명 생년월일`(공백허용), `조문 현 행 변 경`, `구분 병합전`, `가. 의안의 요지`
5. `_clean_title` 연속 공백 정리 — `\s{2,}` → 단일 공백

**검증 (API 제한 전 12건)**
- 솔루엠, 남성, 엑시온그룹, 태경산업, 신라교역, 본느, 메타바이오메드: ✗ → ✓
- 케이티앤지, 케이티: ✓ (regression 없음)
- LG화학, NAVER, 삼성전자: API rate limit으로 미검증 (추후 확인)

**예상 효과**: 93% → 97~98% (42건 중 ~35건 해결, count=0 16건은 LLM fallback)

### 경력 분리 로직 개선
- 기간 여러 개 + 내용 1개(DART 원본에서 합쳐진 경우)일 때 빈 content 나열 대신 전체 기간 범위로 합쳐서 1건 반환
- 삼성전자 김용관, NAVER 김희철 케이스 해결

### API 아키텍처 분석
- **결론: API-SAVING** — `_doc_cache` (30건 LRU)가 효과적. 같은 rcept_no 연속 호출 시 API 1회만
- 낭비 포인트 2개 발견:
  1. `search_filings_by_ticker` 미캐싱 — agm_search + agm_steward 같은 ticker = list.json 2회 (1회 중복)
  2. `parse_agenda_items` 중복 파싱 — agm_agenda/agm_aoi/agm_steward가 같은 문서에서 각각 재파싱 (CPU 낭비, API 아님)

### Frontend 데이터 이슈 진단
- 삼성전자 허은녕: candidates 빈 배열 — 파서가 HTML 섹션 매치 실패 (tool 이슈, frontend 아님)
- NAVER 김이배: 일부 careerDetails에 period 빈칸 — periods < contents 시 빈 period 생성 (파서 이슈)
- Frontend 코드는 정상: `keyData.candidates` 경로로 올바르게 읽음

### 오늘의 성과
- 811건 배치 테스트 인프라 구축 (test_batch.py)
- agenda tree 93%→97~98% (5가지 개선)
- 경력 분리 로직 개선
- API 아키텍처 분석 완료
- DART API 키 자동 전환 (속도 제한 시 보조 키 fallback)
- i18n(한영 토글) 시도 → `feat/eng-added` 브랜치에 아카이브 (런타임 이슈로 한국어 전용 유지)

### 오늘의 실패 / 한계
- DART API 속도 제한 — 배치 테스트 빠른 연속 호출로 IP 차단 (일일 한도 2만건은 미도달)
- 삼성전자/NAVER pipeline JSON 재생성 미완 (API 복구 후 진행)
- count=0 실패 케이스 16건은 비표준 구조라 단순 개선 불가
- i18n: react-i18next 적용했으나 일부 컴포넌트에서 런타임 에러 발생. 한국어 전용으로 롤백, 영어 버전은 `feat/eng-added`에 아카이브

## 2026-03-19

### 프로젝트 초기 설정
- GitHub 레포 생성 (MarcoYou/OpenProxy_MCP, public)
- 프로젝트 구조 설계: `open_proxy_mcp/` 패키지 (server.py, tools/, dart/)
- 기술 스택 결정: Python + FastMCP + httpx + OpenDART API
- `.env`에 OpenDART API 키 설정, `.gitignore` 구성

### 참고 프로젝트 리서치
- **dart-mcp** — DART 재무제표 MCP 서버 분석. FastMCP 패턴, OpenDART 호출 구조 참고. 단일파일/캐싱없음 개선 필요.
- **Kensho (S&P Global)** — LLM 최적화 API 설계, dual transport, 도메인별 tool 분리 참고.
- **FactSet** — 엔터프라이즈 MCP 거버넌스 패턴 참고.
- 공통 교훈 정리: LLM 친화적 구조화, 도메인별 tool 분리, 캐싱 필수

### Step 1: OpenDART API 동작 확인 ✓
- API 키 정상 작동 확인
- 주주총회소집공고 위치 발견: `pblntf_ty=E` (기타공시)
- `report_nm`에 "소집" 포함 여부로 클라이언트 필터

### Step 2: DartClient 구현 ✓
- `dart/client.py` — API 호출 래퍼 (인증, 에러체크, JSON 파싱)
- `search_filings()` 메서드로 공시 검색 한 줄 호출 가능

### Step 3: ticker/회사명 조회 + 본문 가져오기 ✓
- `corpCode.xml` ZIP 다운로드 → 파싱 → 캐싱 (종목코드/회사명 → corp_code 변환)
- `lookup_corp_code()` — 종목코드, corp_code, 회사명 정확/부분 매치
- `search_filings_by_ticker()` — ticker로 공시 검색 편의 메서드
- `get_document()` — document.xml ZIP → XML → 텍스트 추출
- KT&G(033780) 주총 소집공고 본문 49,451자 정상 추출 확인

### 문서화
- README.md, CLAUDE.md, DEVLOG.md, references.md 작성
- TO_DO.md 작성 (미완료 작업 추적)
- 개발 방식 확정: Build → Check → Pass 점진적 사이클

### 다음 단계 → TO_DO.md 참조

## 2026-03-20

### 안건 파서 디버깅 루프 (계속)

**테스트 기업 8개**: 삼성전자, 세방전지, 한화, GS, 솔루엠, 현대리바트, 인포뱅크, 대양금속

**수정 사항:**
1. **정정공고 중복 파싱 해결** — `_strip_correction_preamble()` 추가. `정 정 신 고` 감지 시 마지막 `주N) 정정 후` 이후 본문만 파싱. 세방전지 14건→6건 정상화.
2. **zone 끝점 5000자 강제 컷 제거** — 끝점 패턴에 의존하도록 변경
3. **끝점 패턴 강화** — `\n` 의존 제거, 특수문자(■□○) 시작 섹션 추가, 줄바꿈 없이 이어지는 케이스 대응
4. **zone 내 줄바꿈 제거** — 제목이 여러 줄에 걸치는 케이스 해결. 삼성전자 제2호 제목 정상 추출.
5. **`_clean_title` 보강** — ①②③ 원문자 제거(한화), 끝에 매달린 `(` 제거(솔루엠 제4·5호)

**현재 상태:**
- 삼성전자 ✅, 한화 ✅, GS ✅, 현대리바트 ✅, 인포뱅크 ✅, 솔루엠 ⚠️(하위안건 누락), 세방전지 ⚠️(제2-11호 제목잘림), 대양금속 ❌(정정 포맷 변형으로 중복)

**추가 수정:**
6. **섹션 기반 파싱으로 전환** — `_strip_correction_preamble` 제거, `_extract_notice_section` 추가. '주주총회 소집공고' 본문 헤더를 `(제N기/정기/임시)` 패턴으로 식별. 정정공고 범용 대응. 대양금속, 한국화장품제조 정정 중복 해결.
7. **괄호형 안건 패턴 추가** — `(제N-M-K호)` 콜론 없이 바로 제목. 솔루엠 하위안건 파싱 해결.

**대규모 테스트 결과 (155개 기업):**

| 구분 | 건수 | 비율 |
|------|------|------|
| ✅ validate=True (정규식 파싱 성공) | 140 | 90% |
| ⚠️ validate=False (LLM fallback 대상) | 9 | 6% |
| ❌ 0건 (section 추출 실패) | 3 | 2% |
| 검색 불가 (소집공고 미제출) | 3 | 2% |

**정규식으로 처리 불가한 패턴 (LLM fallback 대상):**
- `제` 없는 비표준 하위안건 번호 (`2-1호`, `3-1호` 등) — 제목에 합침됨
- 후보자 테이블이 제목에 딸려오는 케이스
- section 추출 실패 (문서 구조가 비표준)
- 번호 중복 / 번호 누락
- 정정공고 section 오배치

### 하이브리드 LLM Fallback 구현

**구조:**
```
get_meeting_agenda(rcept_no)
        │
        ▼
  get_document (캐싱)
        │ text
        ▼
  parse_agenda_items(text) ── 정규식 파싱
        │ agenda[]
        ▼
  validate_agenda_result()
  (0건/중복/제목200자↑)
        │
   ✅ True ───────────────────────────┐
        │ ❌ False                    │
        ▼                             │
  extract_notice_section()            │
  extract_agenda_zone()               │
        │                             │
   zone 없음 ──┐    zone 있음         │
        │       │         │           │
        ▼       │         ▼           │
  [HARD FAIL]   │   [SOFT FAIL]      │
  "안건 영역    │   LLM fallback     │
   찾을 수 없음"│   (gpt-5.4-mini)   │
                │         │           │
                │    validate again   │
                │         │           │
                │    ✅ ─────────┐    │
                │         │      │    │
                │    ❌   │      │    │
                │         ▼      ▼    ▼
                │   [HARD FAIL]  format_agenda_tree()
                │   "정규식+LLM       │
                │    모두 실패"       ▼
                │              마크다운 응답
                │
                ▼
           로그 기록
```

**구현 파일:**
- `open_proxy_mcp/llm/client.py` — LLM 호출 (Claude Sonnet 기본, OpenAI 대체)
- `open_proxy_mcp/tools/parser.py` — `validate_agenda_result()` 추가
- `open_proxy_mcp/tools/shareholder.py` — `get_meeting_agenda`에 fallback 로직

**트리거 조건 (validate_agenda_result):**
- 빈 리스트 (0건)
- 같은 number 중복 (정정공고 잔류)
- 제목 200자 초과 (zone 텍스트 딸려옴)

**토큰 사용:** 정규식 성공 시 0, fallback 시 zone 크기만큼 (500~1500자)

**DLQ:** 로그만 남김 (별도 저장소 없음)

**use_llm 옵션:** `get_meeting_agenda(rcept_no, use_llm=False)` 기본. True 시 fallback 활성화.

### 오늘의 성과
- 안건 파서를 섹션 기반으로 전면 리팩토링 — 정정공고 포맷 변형에 범용 대응
- 155개 기업 대규모 테스트 완료, 정규식만으로 90% 처리율 달성
- 하이브리드 LLM fallback 구현 (gpt-5.4-mini) — hard/soft fail 구분, use_llm 옵션
- zone 끝점 패턴, _clean_title 잔류 문자 제거 등 반복 개선으로 처리율 점진적 상승

### 오늘의 실패 / 한계
- `제` 없는 비표준 하위안건 번호(`2-1호`, `3-1호`)는 정규식으로 안전하게 잡을 수 없음 — 오매치 위험
- 후보자 테이블이 제목에 딸려오는 케이스도 정규식 경계로 분리 불가
- section 추출 실패 3건(한국항공우주, 두산밥캣, HD현대마린엔진) — 문서 구조가 비표준
- LLM fallback 실제 e2e 테스트는 OpenAI만 완료, Anthropic API는 미테스트
- 터미널 강제 종료로 이전 대화 메모리 유실 — 작업 맥락 복구에 시간 소요

## 2026-03-21

### get_agenda_detail — 안건 상세 파싱 tool 신설

**배경:** 기존 `get_meeting_agenda`는 안건 **제목 트리**만 추출. 안건별 상세 내용(재무제표, 정관변경 비교표, 이사 후보 정보 등)은 `III. 경영참고사항 > 2. 목적사항별 기재사항`에 있으나 파싱하지 않고 있었음.

**핵심 발견:** `client.py`의 `get_document()`가 HTML→plain text 변환 시 `<table>` 구조를 모두 제거. BeautifulSoup으로 HTML을 직접 파싱하면 테이블 구조를 자연스럽게 보존 가능.

**구현:**
- `client.py` — `get_document()` 반환에 `html` 필드 추가 (raw HTML 보존)
- `parser.py` — `parse_agenda_details(html)` 추가 (BeautifulSoup 기반)
  - DART XML 구조: `<section-2>` > `<library>` > `<section-3>` > `<title>□카테고리` > `<p>■제N호` > `<table>`
  - `<table>` → 마크다운 테이블 변환, 단일 셀 테이블은 텍스트로 반환
  - `<p>` 내 여러 항목 합쳐진 경우 `_split_p_lines()`로 분리
  - 서브섹션(`가.`~`하.`) 감지, `※` 조건부 노트 분리
- `shareholder.py` — `get_agenda_detail(rcept_no, agenda_no, format)` tool 등록
  - `agenda_no` 미지정 시 전체, `"2"` 지정 시 제2호 + 하위 전체 반환
  - `format="md"` 마크다운 / `format="json"` 구조화 JSON

**검증:** KT&G(20260225005779) 8개 안건 전체 파싱 성공. 재무제표 테이블, 정관변경 비교표, 이사 후보 정보 테이블 모두 정상 변환.

### 안건 트리 파서 개선 — bs4 + regex 강화

**1단계: bs4 기반 섹션 추출 (`_extract_agenda_zone_html`)**
- `<section-1>/<title>주주총회 소집공고` 태그로 섹션 경계를 정확히 잡음
- 기존 text regex는 "경영참고사항 참조" 등 인라인 텍스트에 end_pattern 오발동 → zone 잘림
- HTML은 `<section-1>` 범위가 정확하여 이 문제 해결
- 효과: +7건 (하나기술, 레이저옵텍, 삼보모터스, 벡트, 하이퍼코퍼레이션, 플럼라인생명과학, 폴라리스오피스)

**2단계: regex 패턴 개선**
- AGENDA_RE에 `안건` 키워드 추가 (기존 `의안`만): +1건 (에스바이오메딕스)
- AGENDA_NO_COLON_RE 신설 — 콜론 없이 `제N호 의안 제목` 형태: +1건 (글로벌에스엠)
- zone 시작 패턴에 `부의사항` 추가: +1건 (우리산업홀딩스)

**3단계: lookahead 경계 강화 (`_AGENDA_BOUNDARY`)**
- `N-M호` (제 없는 하위안건): +4건 (일진디스플, 시큐브, 대진첨단소재, 삼익악기)
- 후보자 테이블 헤더 (`성명 생년월일`): +2건 (보해양조, 에코마케팅)
- 정관변경 비교 테이블 (`변경전 내용`): +1건 (삼양케이씨아이)

**처리율 변화 (250건 기준):**

| 단계 | 성공 | 비율 |
|------|------|------|
| 이전 (text regex only) | 219 | 87% |
| + bs4 섹션 추출 | 226 | 90% |
| + regex 패턴 개선 | 229 | 91% |
| + lookahead 경계 강화 | **234** | **93%** |

**regression 0건** — 250건 전수 테스트에서 기존 성공 케이스 영향 없음.

### 파서 엔진 벤치마크

**BeautifulSoup 파서 비교 (250건 전수 테스트):**

| 파서 | zone 성공 | 속도 | 결과 차이 |
|------|-----------|------|----------|
| html.parser | 246/250 | 89ms/doc | baseline |
| **lxml** | **246/250** | **62ms/doc (30%↑)** | **0건** |
| html5lib | 246/250 | 159ms/doc (79%↓) | 0건 |

→ lxml을 기본 파서로 채택 (없으면 html.parser fallback)

**regex 라이브러리 평가:**
- `re` 대비 60% 느림
- `\p{Hangul}`은 DART에서 불필요 (자모 443자 추가 매치하나 사용 안 함)
- `[가-하]` 범위는 오히려 `갸`, `거` 등 오매치 — 명시적 나열이 안전
- → 도입하지 않음

### 남은 실패 케이스 분류 (13개 기업, LLM fallback 대상)

| 원인 | 건수 | 기업 |
|------|------|------|
| 비표준 구조 / 기타 | 5 | 유니온바이오메트릭스, 메타바이오메드, 차바이오텍, 동일산업, 와이바이오로직스 |
| zone 추출 실패 | 3 | 인천유나이티드, 아스플로, 태양금속공업(이미지 기반) |
| 번호 중복 | 2 | 솔루엠, 신라교역 |
| 기타 | 3 | 남성, 프로이천, 삼보산업 |

### 오늘의 성과
- `get_agenda_detail` tool 신설 — 안건별 상세 내용을 테이블/텍스트 구분하여 파싱
- BeautifulSoup + lxml 도입으로 HTML 구조 직접 활용
- 안건 트리 파서 처리율 87% → 93% 개선 (250건 기준, +15건, regression 0)
- 파서 엔진 / regex 라이브러리 벤치마크 — 실측 근거로 기술 선택

### 오늘의 실패 / 한계
- 13개 기업은 여전히 정규식으로 해결 불가 — LLM fallback 필요
- `get_agenda_detail`의 다기업 검증 미완 (KT&G만 상세 확인)
- lxml-xml 파서는 DART 문서의 대소문자 혼용 때문에 사용 불가

## 2026-03-21 (continued)

### agm_financials 완성
- 재무상태표/손익계산서 정규화 → 100건 96% 성공
- 자본변동표 추가 + 자사주 취득/소각 플래그
- 이익잉여금처분계산서 전체 반환 + has_dividend 플래그
- 정정공고 사유 분류 (철회/보고전환/이사회승인)
- `format_krw()` 단위 변환 유틸 (백만원×값 → 조/억/만)
- 컬럼 정규화 (4/5/6컬럼 → 통일), 주석 유무 동적 감지

### agm_personnel 완성
- 이사/감사/감사위원 선임·해임 정규화 (41건 에러 0)
- v3 스키마 호환 (camelCase 키명)
- 세부경력 기간/내용 분리 (careerDetails)
- 결격사유 3필드 분리 (eligibility)
- 직무수행계획/추천사유 전문 반환 + 확인서 텍스트 제거
- 소스 태그(주주제안) 제거 후 빈 제목 fallback 수정

### agm_steward 오케스트레이터
- agm_tldr → agm_sherlock → agm_steward 리네이밍
- 재무 하이라이트 자동 추출 (format_krw 적용)
- 인사 하이라이트 (선임 N명, 후보자 이름)

### tool 리네이밍 (agm_ prefix)
9개 tool: agm_search, agm_steward, agm_agenda, agm_info, agm_items, agm_financials, agm_personnel, agm_corrections, agm_document

### 프론트엔드 연동 (OpenProxy)
- KT&G/삼성전자/NAVER/LG화학 MCP v3 JSON 생성 → 프론트엔드 표시
- 실데이터 반영: CEO, 최대주주(지분율), 회계월 (DART company.json/hyslrSttus.json)
- 재무 하이라이트 카드 (자산총계/매출/영업이익/당기순이익 + 전년비)
- 재무제표 로우 데이터 테이블 (연결/별도 탭 + BS/IS/자본변동표/처분계산서)
- 단위 변환 표시 (백만원×값 → 억/조, 단위:원 유지)
- 계층 트리 테이블 (자산>유동>세부 접이식 ▼/▶)
- 변화율 컬럼 (+X.X% 초록 / -X.X% 빨강 / 신규 파랑)
- 계정명 정리 (Ⅰ. 1. l. 접두사 제거 + 공백 정리)
- 긴 제목 truncate + tooltip
- 'AI 요약' → '요약' 변경
- 주석(note) 컬럼 숨김

### 파서 수정
- 소스 태그(주주제안) 제거 시 제목 보호 — 괄호 안만 제거
- 선행 콜론 제거 fallback
- LG화학 제3호 "주주 제안의 건" 정상 파싱

### 오늘의 성과
- MCP 9개 tool 완성 + 프론트엔드 연동까지 e2e 동작
- 4개 기업(KT&G/삼성전자/NAVER/LG화학) 실데이터로 프론트엔드 표시
- 재무제표 계층 트리, 변화율, 단위 변환 등 display 완성

### 오늘의 실패 / 한계
- careerCompanyGroups 회사명 분리 정확도 (부서명 포함 이슈)
- 주총 이력 관리 (정기/임시, 연도별) — 프론트엔드/DB에서 처리 필요
- mockData.ts 하드코딩 → API 자동화 미완 (수동 JSON 생성)
- NAVER 최대주주 지분율 0.00% (DART API 한계)
