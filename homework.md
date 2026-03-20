# Homework

## 완료

### Step 4: FastMCP 서버 구축 ✓
- [x] server.py 작성 (FastMCP 진입점)
- [x] tools/shareholder.py 작성 — 주주총회 소집공고 검색 + 본문 조회 MCP tool 4개
- [x] get_document()에서 이미지 파일명 본문에서 제거 + 별도 목록으로 분리 반환

### 안건 파서 (parser.py) ✓
- [x] 정규식 기반 안건 트리 파싱 (표준 + 괄호형 패턴)
- [x] 섹션 기반 파싱 — 정정공고 범용 대응
- [x] 155개 기업 대규모 테스트 — 90% 정규식 처리율
- [x] validate_agenda_result() 품질 검사
- [x] 하이브리드 LLM fallback (gpt-5.4-mini, use_llm 옵션)
- [x] hard fail / soft fail 구분

## 해야 할 일

### OCR 파이프라인
- [ ] 공시 본문 내 이미지(BSM, 확인서 등) OCR 처리 기능 추가
- [ ] img2table + PaddleOCR 조합 검토
- [ ] get_document()에서 이미지 파일명 분리 반환 구조 만들기

### 기업 검색 개선
- [ ] 영문 브랜드명(KT&G 등) → corp_code 매핑 지원 (별칭 또는 영문명 API)

### 연동 테스트
- [ ] Claude Desktop 또는 Claude Code에서 실제 연동 테스트

### 파서 개선 (LLM fallback으로 커버 중)
- [ ] `제` 없는 비표준 하위안건 번호 패턴 대응
- [ ] 후보자 테이블 제목 분리
- [ ] Claude API (Anthropic) fallback 추가 (현재 OpenAI만 테스트 완료)
