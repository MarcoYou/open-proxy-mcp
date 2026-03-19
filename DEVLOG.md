# Dev Log

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

### 문서화
- README.md, CLAUDE.md 작성
- references.md 작성 (참고 프로젝트 상세 분석)
- DEVLOG.md 작성 (개발 일지)
- 개발 방식 확정: Build → Check → Pass 점진적 사이클

### 다음 단계
- [ ] OpenDART API 키 동작 확인 (주주총회 소집공고 검색 테스트)
- [ ] 응답 구조 파악 후 dart/client.py 설계
