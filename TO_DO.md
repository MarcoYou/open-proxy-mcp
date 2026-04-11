# Homework

## 해야 할 일

### 파서 미해결 케이스 (KOSPI 200 벤치마크 기준)
- [ ] HARD_FAIL 78명 — 이름이 조문번호/안건번호로 잡힌 케이스 (DL, POSCO홀딩스 등)
- [ ] HARD_FAIL — 경력 없음 (BGF리테일 민승배, 삼성물산 이정식, 롯데칠성 이양수 등)
- [ ] SOFT_FAIL 103명 — 경력 병합 (content > 100자) 잔여 케이스
- [ ] 안건번호=후보자이름 17건 — 파서 근본 수정 필요
- [ ] 정관텍스트=후보자 4건 — 파서 수정
- [ ] BNK금융지주/기업은행 비표준 구조 — LLM fallback 대상
- [ ] 직책/대기업 키워드 리스트 data/ 디렉토리 분리

### agm_personnel 개선
- [ ] careerCompanyGroups 회사명 분리 정확도 개선 (부서명이 회사명에 포함되는 이슈)
- [ ] 경력 병합 잔여 케이스 추가 분리 패턴 (現/前 완료, `-`/법인격 등 추가 필요)

### agm_proposals (향후)
- [ ] 주주제안 안건 정규화 (의안 제목 + 요지 텍스트)
- [ ] 권고적 주주제안 구조 (LG화학 제3호 — 팰리서 등 행동주의 펀드)
- [ ] 프록시 파이트 분석 연계

### ownership tool 개선
- [ ] own_major에 최대주주등소유주식변동신고서 연동 (KIND 크롤링, ticker 기반 검색)
- [ ] own_block 보유목적 변경 시계열 추적 (동일 보고자 이력 비교)
- [ ] own_latest에 3대 주체 분류 태깅 (최대주주+특관인 / 국민연금 / 기관투자자)
- [ ] 집중투표 분석 tool 체이닝 (agm + own 연계, 의결권 시뮬레이션)

### 기업 검색 (lookup_corp_code) 개선
- [ ] alias dict 확장 — 새 브랜드명/영문명 발견 시 추가

### prx_* 위임장 권유 tool (향후)
- [x] prx_search — 위임장 권유 참고서류 검색
- [x] prx_detail — 권유자 + 방법 (비용은 별도 공시 "의결권대리행사권유신고서"에 있음)
- [x] prx_direction — 안건별 의결권 행사 방향
- [x] prx_fight — 프록시 파이트 감지 (양측 비교)
- [x] prx_manual — PRX_TOOL_RULE + PRX_CASE_RULE
- [ ] prx_search 정정본 처리 — 주주측 초기 제출 시 후보자 미기재, 최신 정정본 rcept_no 자동 선택 필요

### LLM fallback
- [ ] LLM fallback tool — XML 원문 + AGM_CASE_RULE로 AI 보강 (향후)

### DXT 패키징 (배포)
- [ ] manifest.json 작성 (user_config: DART API 키, Upstage API 키)
- [ ] 프로젝트 구조를 DXT 형태로 재구성
- [ ] icon.png 제작
- [ ] DXT 빌드 + 테스트 (Claude Desktop 설치 확인)
- [ ] 배포 (GitHub Release에 .dxt 파일 첨부)

### 이미지 인덱싱 + OCR 파이프라인
- [ ] parse_agenda_details에서 이미지 메타데이터 인덱싱 (파일명, 위치, 카테고리)
- [ ] ZIP 내 이미지 바이너리 추출 (client.py)
- [ ] Tesseract + EasyOCR 벤치마크 (KT&G BSM으로)
- [ ] 별도 OCR tool → 결과를 agenda detail에 병합


### own_full_analysis 추가 개선
- [ ] 대량보유자 중 사업보고서에 없는 케이스 처리 개선 (국민연금 등)

### Chain Tools (향후)
- [ ] agm_full_analysis(ticker) — agenda → personnel → compensation → vote result → 종합 판정
- [ ] governance_report(ticker) — agm + own + div 3개 도메인 한 번에

### Meta Tool (향후, tool 40개+ 시)
- [ ] discover_tools(category, keyword) — 카테고리/키워드로 tool 검색
- [ ] execute_tool(name, params) — 내부 tool 프록시 호출

### API 최적화
- [ ] search_filings_by_ticker 결과 캐싱 (같은 ticker 중복 호출 방지)
- [ ] parse_agenda_items 결과를 _doc_cache에 저장 (CPU 중복 파싱 방지)
- [ ] 응답 truncation (50KB 제한) — law-mcp 참고

