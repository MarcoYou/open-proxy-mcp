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
- homework.md 작성 (미완료 작업 추적)
- 개발 방식 확정: Build → Check → Pass 점진적 사이클

### 다음 단계 → homework.md 참조

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
