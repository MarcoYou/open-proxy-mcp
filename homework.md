# Homework

## 완료

### Step 4: FastMCP 서버 구축 ✓
- [x] server.py 작성 (FastMCP 진입점)
- [x] tools/shareholder.py 작성 — 주주총회 소집공고 검색 + 본문 조회 MCP tool 4개
- [x] get_document()에서 이미지 파일명 본문에서 제거 + 별도 목록으로 분리 반환

### 안건 파서 (parser.py) ✓
- [x] 정규식 기반 안건 트리 파싱 (표준 + 괄호형 패턴)
- [x] 섹션 기반 파싱 — 정정공고 범용 대응
- [x] 250개 기업 대규모 테스트 — 93% 처리율 (bs4+regex)
- [x] validate_agenda_result() 품질 검사
- [x] 하이브리드 LLM fallback (gpt-5.4-mini, use_llm 옵션)
- [x] hard fail / soft fail 구분
- [x] BeautifulSoup + lxml 기반 섹션 추출 (text regex fallback 유지)
- [x] lookahead 경계 강화 (하위안건/테이블헤더/정관변경)

### 안건 상세 파싱 (get_agenda_detail) ✓
- [x] BeautifulSoup으로 HTML 직접 파싱 — 테이블은 마크다운 테이블, 텍스트는 텍스트
- [x] KT&G 8개 안건 검증 통과
- [x] 다기업 호환성 수정 (삼성전자/현대차 패턴, section-4 재귀 파싱)
- [x] 6개 기업 cross-check 통과 (KT&G, 삼성전자, LG화학, NAVER, 현대차, SK이노베이션)

## 해야 할 일

### 이미지 인덱싱 + OCR 파이프라인
- [ ] parse_agenda_details에서 이미지 메타데이터 인덱싱 (파일명, 위치, 카테고리)
  - BSM → `{"type": "image", "category": "bsm"}`, 확인서 → 스킵
- [ ] ZIP 내 이미지 바이너리 추출 (client.py)
- [ ] Tesseract + EasyOCR 벤치마크 (KT&G BSM으로)
- [ ] 별도 OCR tool → 결과를 agenda detail에 병합

### 기업 검색 개선
- [ ] 영문 브랜드명(KT&G 등) → corp_code 매핑 지원 (별칭 또는 영문명 API)

### 연동 테스트
- [ ] Claude Desktop 또는 Claude Code에서 실제 연동 테스트

### 파서 개선 (LLM fallback으로 커버 중)
- [x] `제` 없는 비표준 하위안건 번호 패턴 대응 (lookahead 경계로 해결)
- [x] 후보자 테이블 제목 분리 (테이블 헤더 경계로 해결)
- [ ] Claude API (Anthropic) fallback 추가 (현재 OpenAI만 테스트 완료)

### agm_items (구 get_agenda_detail) ✓
- [x] 110건 100% 성공
- [x] 안건 마커 없는 library fallback (카테고리 제목 사용)

### agm_financials ✓
- [x] 재무상태표/손익계산서 정규화 (96% 성공)
- [x] 자본변동표 + 자사주 취득/소각 플래그
- [x] 이익잉여금처분계산서 + has_dividend 플래그
- [x] 연결/별도 자동 분류
- [x] 컬럼 정규화 (4/5/6컬럼 → 통일)
- [x] format_krw() 단위 변환 유틸

### agm_personnel ✓
- [x] 이사/감사/감사위원 선임·해임 정규화
- [x] v3 스키마 호환 (camelCase 키명)
- [x] 세부경력 기간/내용 분리 (careerDetails)
- [x] 결격사유 3필드 분리 (eligibility)
- [x] 직무수행계획/추천사유 전문 반환
- [x] 확인서 텍스트 자동 제거
- [x] 41건 테스트 에러 0건

### agm_steward ✓
- [x] 종합 오케스트레이터 (info + agenda + financials highlights + personnel summary)

### agm_corrections ✓
- [x] 정정 전/후 비교 + 메타데이터

## 해야 할 일

### agm_aoi ✓ (기본 구현 완료, 개선 필요)
- [x] 정관변경 비교 테이블 파싱 (변경전/변경후/사유)
- [x] 세부의안 병합 (additionalClauses)
- [x] 프론트엔드 접이식 카드 UI
- [ ] 세부의안 번호 ↔ charterChanges 매핑 (LG화학 등 테이블에 번호 없는 케이스)
- [ ] agm_agenda 세부의안과 charterChanges 1:1 연결

### agm_personnel 개선
- [ ] careerCompanyGroups 회사명 분리 정확도 개선 (부서명이 회사명에 포함되는 이슈)
- [ ] 경력 기간/내용 분리 — HTML <table> 행 단위 직접 추출 (마크다운 변환 시 합쳐지는 이슈)

### 이미지 인덱싱 + OCR 파이프라인
- [ ] parse_agenda_details에서 이미지 메타데이터 인덱싱 (파일명, 위치, 카테고리)
  - BSM → `{"type": "image", "category": "bsm"}`, 확인서 → 스킵
- [ ] ZIP 내 이미지 바이너리 추출 (client.py)
- [ ] Tesseract + EasyOCR 벤치마크 (KT&G BSM으로)
- [ ] 별도 OCR tool → 결과를 agenda detail에 병합

### 기업 검색 개선
- [ ] 영문 브랜드명(KT&G 등) → corp_code 매핑 지원 (별칭 또는 영문명 API)

### 파서 개선
- [ ] Claude API (Anthropic) fallback 추가 (현재 OpenAI만 테스트 완료)

### 프론트엔드 연동
- [ ] v3 스키마 전체 호환 (classification, governanceAnalysis, checklist 등)
- [x] OpenProxy 프론트엔드에 JSON 연결 (KT&G MCP v3)
- [x] 재무제표 로우 데이터 테이블 + 하이라이트 카드
- [x] 단위 변환 표시 (백만원 × 값 → 억/조, 단위:원 유지)
- [x] KT&G/삼성전자/NAVER/LG화학 실데이터 반영 (company.json + hyslrSttus.json)
- [x] 재무 계층 트리 (인덴트 + 접이식)
- [x] 변화율 컬럼 (초록/빨강)
- [x] 계정명 접두사 제거 (Ⅰ. 1. l.)
- [x] 주석 컬럼 숨김
- [x] 정관변경 접이식 카드 UI
- [x] 주총 종료 자동 감지
- [ ] 주총 이력 관리 (정기/임시, 연도별) — DB에서 처리
- [ ] mockData.ts 자동화 (수동 JSON 생성 → API 자동)
