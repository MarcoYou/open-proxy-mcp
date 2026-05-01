---
type: ralph
title: action tool 재편 — advise_vote_before_meeting + recap_vote_after_meeting
created: 2026-05-02 09:30
completion_promise: ADVISE_RECAP_VOTE_DONE
max_iterations: 25
---

# action tool 재편 ralph

기존 3개 action tool (`prepare_vote_brief` / `build_campaign_brief` / `prepare_engagement_case`)을 시점 기반 2개 (`advise_vote_before_meeting` / `recap_vote_after_meeting`)로 재편.

**핵심**: 운용사 의결권 행사 보고서 스타일. 안건마다 행사방향 + 사유. **gap 비교 X, 검증 가능한 fact + 정책 근거만**.

---

## 매 iteration 작업

1. **현황 확인**: `git status` + 가이드 체크리스트 대조
2. **다음 1 step만 진행** (검증 가능 단위로 작게 쪼갬)
3. **데이터 매핑 검증** — 모든 upstream upstream 매핑마다 success/soft-fail/hard-fail 명시
4. **sanity test** (정기주총 + 임시주총 모두)
5. **self-critique**: 성공 기준 vs 현재 갭 / 가짜 데이터 risk / 회귀 가능성
6. **commit** (의미 있는 변경마다, 커밋 메시지에 step 명시)
7. 다음 iteration 계획 1줄

---

## 데이터 매핑 분류 (필수 — 모든 항목)

### success (그대로 사용)
정형 필드로 깔끔하게 추출됨. 코드가 정확히 매핑하고 메모/render에 그대로 쓸 수 있음.
- 예: 주총 일시 / 안건 리스트 / 지분율 / 매출 / ROE / 감사의견 / 후보 이름 / 임기

### soft-fail (raw 통째로 LLM에게)
정형 필드는 없지만 **raw text 또는 unstructured data가 존재**. OPM 코드는 매핑 실패 → LLM에게 raw payload를 그대로 노출해서 이해·답변하게 함.
- 예: 후보 약력 텍스트 (과거 회사 추출 실패 시) / 안건 본문 디테일 / 임원 추천 사유 / 정관 조문 변경 사항
- 처리 방식: payload에 `raw_text` 또는 `unstructured` 필드로 노출 + render에서 "참고: 아래 원문을 기반으로 판단" 명시

### hard-fail (데이터 자체 없음)
데이터 출처 자체에 정보가 없음. **메모에 표시하지 않음** (코붕이 지시: "자동 검증 안 된 건 안된거니까 표시하지 말고").
- 예: 형사 처벌 이력 / 사적 관계 (학연 등) / 동명이인 정확 식별 / 파산 / 임원 자격 박탈
- 처리 방식: 코드에서 침묵, 메모에서 언급 안 함

**모든 신규 코드 매핑 라인마다 위 3-tier 중 어느 분류인지 주석 명시.**

---

## 성공 기준 (모두 충족 시 promise 출력)

### 코드 — 신규
- [ ] **`open_proxy_mcp/services/director_evaluation.py`** 신규 (Marco 후보 평가 모듈)
  - [ ] personnel parser에서 후보 약력 텍스트 추출 (success로 분류)
  - [ ] 약력에서 과거 회사명 정규식 추출 (성공 시 success / 실패 시 soft-fail로 raw 노출)
  - [ ] 과거 회사 corp_code 매핑 + 재직 기간 추출
  - [ ] 각 과거 회사 financial_metrics audit_opinion + yoy 호출
  - [ ] 재직 기간 × 회계 risk 시점 overlap 체크 → red flag (success 시)
  - [ ] 독립성 sub-factor 4개 매핑 (success): 최대주주 매칭 / 최근 2년 직원 / 5년 룰 / 회사와 거래
  - [ ] 충실성 sub-factor 3개 매핑 (success): 출석률 / Marco overlap / 거버넌스 위반 추이
  - [ ] 결격사유 sub-factor 1개 매핑 (success): 나이 (미성년만)
- [ ] **`open_proxy_mcp/services/advise_vote.py`** 신규
  - [ ] 6 upstream 통합 (shareholder_meeting + ownership_structure + corp_gov_report + financial_metrics + proxy_guideline + director_evaluation)
  - [ ] 안건별 FOR/AGAINST/REVIEW 자동 결정 (proxy_guideline + 매트릭스 자동 채점)
  - [ ] 안건별 **결정 사유** 1-2 문장 (정책 근거 + 사실 근거 + evidence rcept_no)
  - [ ] meeting_type 자동 감지 (annual / extraordinary)
- [ ] **`open_proxy_mcp/services/recap_vote.py`** 신규
  - [ ] 5 upstream (shareholder_meeting results + proxy_contest + dividend/treasury_share/corporate_restructuring/dilutive_issuance + corp_gov_report 변화)
  - [ ] 안건별 결과 (가결/부결/찬반율/출석률) + **OPM 정책상 행사 사유** (gap 비교 X)
  - [ ] 주총 직후 30일 후속 공시 surface
- [ ] **`open_proxy_mcp/tools_v2/advise_vote_before_meeting.py`** 신규 (register + render)
  - [ ] 운용사 의결권 행사 메모 스타일 (회사명 / 주총일 / 안건별 표 — 안건 / 방향 / 사유 / rcept_no)
- [ ] **`open_proxy_mcp/tools_v2/recap_vote_after_meeting.py`** 신규 (register + render)
  - [ ] 운용사 분기 의결권 보고서 스타일

### 코드 — 기존 제거
- [ ] `open_proxy_mcp/tools_v2/prepare_vote_brief.py` 삭제 (advise에 로직 흡수 후)
- [ ] `open_proxy_mcp/services/vote_brief.py` 삭제
- [ ] `open_proxy_mcp/tools_v2/build_campaign_brief.py` 삭제 (timeline 로직 advise/recap에 분산)
- [ ] `open_proxy_mcp/services/campaign_brief.py` 삭제
- [ ] `open_proxy_mcp/tools_v2/prepare_engagement_case.py` → `tools_v2/_archive/` 이동 (FastMCP 자동 디스커버리 자연 제외)
- [ ] `open_proxy_mcp/services/engagement_case.py` → `services/_archive/` 이동
- [ ] FastMCP register_all_tools_v2 → 18 tool → 17 tool 자동 인식 확인

### 매핑 검증 (필수 — 모든 항목 분류 명시)
모든 upstream 데이터 매핑마다 success / soft-fail / hard-fail 분류:

**advise_*에서 검증할 매핑** (예시 — 코드 작성하며 추가):
- 안건 리스트 (shareholder_meeting → advise) → success
- 후보 이름·임기·약력 (shareholder_meeting → director_evaluation) → success / soft-fail (약력 정규식 실패 시)
- 후보의 최대주주 관계 (ownership_structure × 후보 이름 매칭) → success / soft-fail (이름 동일/유사 매칭 모호 시)
- 후보 이사회 출석률 (corp_gov_report) → success
- Marco 시나리오 과거 회사 (약력 → corp_code 매핑) → success / soft-fail (회사명 매핑 실패 시 raw 약력 노출)
- 회사 재무 risk (financial_metrics summary + audit_opinion) → success
- 정책 권고 (proxy_guideline + 12 매트릭스 채점) → success

**recap_*에서 검증할 매핑** (예시):
- 주총 결과 안건별 가결/부결/찬반율 (shareholder_meeting results) → success
- 후속 공시 (dividend/treasury/restructuring/dilutive 4종) → success
- 결정 사유 (proxy_guideline 정책) → success

### Sanity Test (정기 + 임시 + 얼라인 행사 대상 + edge case)

#### A. advise_vote_before_meeting

**A1. 정기주총 — 다양한 시장/산업 (13 case = 11 회사, 양년 비교 2개 포함)**
- [ ] 삼성전자 (KOSPI 005930, 대형 거버넌스 모범)
- [ ] **KT&G 2025 정기** (KOSPI 033780, 배당주 + 행동주의 이력)
- [ ] **KT&G 2026 정기** ← 양년 비교 (정책 변화 detect)
- [ ] 한국타이어앤테크놀로지 (KOSPI 161390, 이사 보수 + 일감몰아주기)
- [ ] LG화학 (KOSPI 051910, 배터리 분할 후 거버넌스 + 주주환원)
- [ ] KB금융 (KOSPI 105560, 금융지주, 얼라인 행사 대상)
- [ ] 두산밥캣 (KOSPI 241560, 두산 합병 이력 + 얼라인 행사)
- [ ] 에스엠엔터테인먼트 (KOSDAQ 041510, 행동주의 + 사명변경, 얼라인 행사)
- [ ] 인바디 (KOSDAQ 041830, 의료기기, 얼라인 행사)
- [ ] 셀트리온 (KOSPI 068270, 합병 후 거버넌스)
- [ ] **고려아연 2025 정기** (KOSPI 010130, 영풍/MBK 분쟁 진행 중)
- [ ] **고려아연 2026 정기** ← 양년 비교 (분쟁 결과 반영)

**A2. 임시주총 — 4월 이후 20 회사 (KOSPI 8 + KOSDAQ 12)**
*(코붕이 지시: "3월31일 이후 주총한 기업들은 대부분 임시주총". DART list.json E type, 2026-04-01~ 공시일 기준 unique 회사)*

KOSPI 8:
- [ ] 한전기술 (Y, 20260430, rcept 20260430001131)
- [ ] 진원생명과학 (Y, 20260429, 20260429000610)
- [ ] 달바글로벌 (Y, 20260428, 20260428000562)
- [ ] 대신밸류리츠 (Y, 20260428, 20260428000439)
- [ ] DKME (Y, 20260427, 20260427000511)
- [ ] 영흥 (Y, 20260427, 20260427000206)
- [ ] HMM (Y, 20260423, 20260423000572) — 산업은행 주주환원 분쟁 가능
- [ ] 한국수출포장공업 (Y, 20260422, 20260422000392)

KOSDAQ 12:
- [ ] 아이로보틱스 (K, 20260430, 20260430001868)
- [ ] 나우로보틱스 (K, 20260430, 20260430001642)
- [ ] 소노스퀘어 (K, 20260430, 20260430001605)
- [ ] 오텍 (K, 20260430, 20260430001362)
- [ ] 케이피엠테크 (K, 20260430, 20260430001284)
- [ ] 셀리드 (K, 20260430, 20260430000921)
- [ ] 솔트룩스 (K, 20260430, 20260430000559)
- [ ] 케이사인 (K, 20260430, 20260430000059)
- [ ] KBG (K, 20260429, 20260429001162)
- [ ] 한국비티비 (K, 20260429, 20260429001063)
- [ ] 텔콘RF제약 (K, 20260429, 20260429000762)
- [ ] 멕아이씨에스 (K, 20260429, 20260429000513)

**A3. 위임장 분쟁 (3 회사)**
- [ ] 한진칼 (180640, 2024 KCGI 위임장 분쟁)
- [ ] 고려아연 (010130, 2025 영풍/MBK vs 최윤범 분쟁)
- [ ] JB금융지주 (175330, 얼라인 추천 비례적 비상임이사 — 2024+2025 양년)

**A4. Edge case (5 회사)**
- [ ] 알지노믹스 (KOSDAQ 476830, **자본잠식 full** 5,587%)
- [ ] 지투지바이오 (KOSDAQ 456160, **자본잠식 full** 16,161%)
- [ ] 맥쿼리인프라 (088980, 인프라 펀드 — 일반 사업보고서 X)
- [ ] 리브스메드 (491000, 신규 상장 KOSDAQ — 사업보고서 미공시)
- [ ] 삼성전자우 (005935, 우선주 — corpCode 별도)

**A5. 얼라인파트너스 의결권 행사 대상 12 unique 회사 전수 (advise 기준)**
*(코붕이 지시: "얼라인파트너스가 공시한 의결권행사대상 기업들 전부 advise 검증". 2024-04 + 2025-04 + 2026-04 records 합산 dedupe. A1과 일부 중복 OK — 양 카테고리에서 검증 보장)*

5 금융지주:
- [ ] KB금융지주 (KOSPI 105560)
- [ ] 신한지주 (KOSPI 055550)
- [ ] 하나금융지주 (KOSPI 086790)
- [ ] 우리금융지주 (KOSPI 316140)
- [ ] JB금융지주 (KOSPI 175330)

4 KOSPI 사업회사:
- [ ] 코웨이 (KOSPI 021240)
- [ ] 두산밥캣 (KOSPI 241560)
- [ ] 인바디 (KOSDAQ 041830)
- [ ] 덴티움 (KOSPI 145720)

3 KOSDAQ:
- [ ] 에스엠엔터테인먼트 (KOSDAQ 041510)
- [ ] 가비아 (KOSDAQ 079940)
- [ ] 스틱인베스트먼트 (KOSPI 026890)

→ A5 검증 핵심: **얼라인 records 실제 결정 vs 우리 advise 추천 일치율**.
- 얼라인 (행동주의) 행사 대상은 보통 거버넌스 이슈 농후 → OPM 정책과 일치 가능성 높음.
- 일치 ≥70% target. 다른 case는 사유 상세 비교 (특히 JB금융지주 4년 장기재임 사외이사 / 핀다 상호주 거래 등 알려진 이슈).

#### B. recap_vote_after_meeting

**B1. 정기주총 결과 (13 case = 11 회사, 양년 포함)**
A1 case와 동일 회사 — 정기주총 결과 + 후속 공시 (2025/2026 양년):
- [ ] 삼성전자 / 한국타이어 / LG화학 / KB금융 / 두산밥캣 / 에스엠엔터 / 인바디 / 셀트리온
- [ ] **KT&G 2025 결과** + **KT&G 2026 결과** (양년 비교 — 정책 변화 detect)
- [ ] **고려아연 2025 결과** + **고려아연 2026 결과** (분쟁 진행 → 결과 변화)

**B2. 임시주총 결과 (5 회사)**
- [ ] 한진칼 (2024 KCGI 위임장 결과 — KIND 기록)
- [ ] 고려아연 (2025 영풍/MBK 위임장 결과)
- [ ] JB금융지주 (얼라인 비례 비상임이사 부결 / 가결 결과)
- [ ] HMM (산업은행 vs 일반주주 — 후속 공시 cross-link 필요)
- [ ] 코웨이 (얼라인 2024+2025 행사 — 결과 vs 우리 추천 비교)

**B3. 얼라인파트너스 의결권 행사 대상 — 12 unique 회사 전수**
*(코붕이 지시: "얼라인파트너스가 공시한 의결권행사대상 기업들 전부". 2024+2025 합산 dedupe)*
- [ ] KB금융지주 / 신한지주 / 하나금융지주 / 우리금융지주 / JB금융지주 (5 금융지주)
- [ ] 코웨이 / 두산밥캣 / 인바디 / 덴티움 (4 KOSPI 사업회사)
- [ ] 에스엠엔터테인먼트 / 가비아 / 스틱인베스트먼트 (3 KOSDAQ)

→ recap에서 얼라인의 실제 결정 사유(이미 학습한 records JSON)와 OPM 행사 사유 비교 검증.
**검증 포인트**: 얼라인이 반대한 안건들 (예: JB금융지주 사외이사 후보 4년 장기재임 + 핀다 상호주 거래) 우리 advise도 반대 결과 나오는지.

#### 검증 포인트 (모든 sanity case 공통)

각 case마다 아래 9개 항목 명시 검증 + audit 페이지 표로 기록:

1. ✅ 6 upstream tool 모두 호출 정상 (status=exact, no exception)
2. ✅ 안건 리스트 빠짐없이 추출 (소집공고 안건 vs 추출 결과 일치)
3. ✅ 후보자 (이사/감사/감사위원) 이름 + 약력 추출
4. ✅ 후보 평가 3축 매핑이 success/soft-fail로 정확 분류 (hard-fail은 침묵)
5. ✅ 안건별 FOR/AGAINST/REVIEW 추천 + 1-2문장 결정 사유
6. ✅ 결정 사유에 정책 근거 (Open Proxy Guideline 조항) + 사실 근거 (rcept_no) 둘 다 포함
7. ✅ evidence_refs 모두 viewer_url 정상 (DART rcpNo 패턴)
8. ✅ soft-fail 항목은 raw text 노출 (LLM이 자연어 처리 가능하게)
9. ✅ hard-fail 항목 (형사 / 사적 관계 등)은 메모 + 코드 모두 침묵 (코붕이 명시 지시)

**대조 검증** (얼라인 학습 데이터 활용):
- 얼라인 기존 records (`a_activist_2024-04 + 2025-04 + 2026-04`) 중 KOSPI 12 회사
- 얼라인의 실제 결정 (for/against) vs 우리 advise 결정 비교
- 일치율 측정 (target ≥ 70% — 얼라인은 행동주의, 우리는 OPM 정책으로 ≠ 가능. 다르면 사유 비교)

### Regression 0
- [ ] 14 data tool (financial_metrics 포함) 회귀 0 — 1 회사 spot-check (예: 삼성전자 dividend / corp_gov_report / financial_metrics 호출 → 변경 없음)
- [ ] proxy_guideline 정적 데이터 영향 없음
- [ ] 18 tool → 17 tool (3 제거 + 2 추가) 자동 디스커버리 확인

### Wiki + 문서
- [ ] `wiki/tools/advise_vote_before_meeting.md` 신규 (12 섹션 + Flow mermaid)
- [ ] `wiki/tools/recap_vote_after_meeting.md` 신규 (12 섹션 + Flow mermaid)
- [ ] `wiki/tools/prepare_vote_brief.md` → `archive/tools/`
- [ ] `wiki/tools/build_campaign_brief.md` → `archive/tools/`
- [ ] `wiki/tools/prepare_engagement_case.md` → `archive/tools/`
- [ ] `wiki/architecture/audits/{yymmdd_hhmm}_audit_advise-recap-vote.md` (sanity 결과)
- [ ] `wiki/index.md` (18 → 17 tool, action 3 → 2)
- [ ] `wiki/tools/README.md` (action 3 → 2)
- [ ] `README.md` + `README_ENG.md` (badge / Tool Structure / 도메인 표)
- [ ] `CLAUDE.md` (action tool 설명)
- [ ] `wiki/log.md` (재편 기록)

---

## 품질 강화

- 자동 검증 안 된 항목 (hard-fail)은 메모에 **표시하지 않음** (코붕이 지시 명시).
- 매핑 분류 (success/soft-fail/hard-fail)는 service 코드 주석 + audit 페이지에 표 형태로 정리.
- 결정 사유는 정책 근거 + 사실 근거 둘 다 + evidence rcept_no 포함 (1-2 문장 압축).
- 운용사 보고서 스타일 — 안건 / 방향 / 사유 / 근거 4컬럼 표 중심.
- 가짜 데이터 절대 X — soft-fail은 raw text 그대로 노출, 추정/조작 X.
- 명명 규칙:
  - tool 페이지 = `advise_vote_before_meeting.md` / `recap_vote_after_meeting.md` (정체성)
  - audit 페이지 = `260502_HHMM_audit_advise-recap-vote.md` (시점 prefix)

---

## 종료 조건

### ✅ 모든 성공 기준 충족
1. 위 모든 체크박스 ✅
2. `git status` clean (모두 commit + push)
3. fly.io 자동 배포 진행
4. 마지막 commit 메시지에 action tool 재편 완료 명시
5. **`<promise>ADVISE_RECAP_VOTE_DONE</promise>` 출력 → ralph 종료**

### ⚠️ 막힘 발생 시
- shareholder_meeting personnel parser가 임시주총 후보 못 추출 (정관 변경만 있는 케이스)
- 후보 약력에서 과거 회사명 정규식 패턴 회사마다 너무 다양 → soft-fail 빈도 높음
- 위임장 분쟁 케이스 (한진칼/고려아연) personnel/proxy_contest 파서 한계
- regression 발생 (14 data tool 영향)
- → **promise 출력 X**, 사용자에게 `## STATUS REPORT` 섹션으로 막힘 영역 + 결정 요청

---

## 반복 단위 (작은 step)

좋은 1 iteration 단위 예시:
- "director_evaluation.py 작성 + 후보 약력 success/soft-fail 분류 + 삼성전자 sanity"
- "advise_vote.py 작성 + 안건별 FOR/AGAINST 추천 + KT&G sanity"
- "Marco 시나리오 (과거 회사 × 재직 기간 overlap) + 한진칼 sanity"
- "recap_vote.py 작성 + 후속 공시 surface + SK하이닉스 sanity"
- "기존 3 tool 제거 + register_all_tools_v2 17 tool 확인"
- "wiki tools/ 2 페이지 + archive 3 이동"
- "README 17 tools 동기화 + audit 페이지"

너무 큰 step (예: "전체 코드 + wiki 한 번에") 금지. 검증 가능한 단위로 쪼갬.

---

## 참고 — 기존 패턴

- `services/dividend_v2.py` — 6 scope facade 패턴
- `services/financial_metrics.py` — 신규 통합 + 매핑 + alerts (Phase 1 완료, 참고 가능)
- `services/contracts.py` — `AnalysisStatus`, `build_usage`, `EvidenceRef`, `ToolEnvelope`
- `services/vote_brief.py` (제거 예정) — upstream 통합 + auto_score_matrix 흡수 logic
- `services/campaign_brief.py` (제거 예정) — timeline 흡수 logic
- `services/engagement_case.py` (archive 예정) — engagement 흡수 logic (Phase 3 부활 시 참고)

---

## 명명 + frontmatter 규칙

- tool 페이지: `tools/advise_vote_before_meeting.md` / `tools/recap_vote_after_meeting.md` (정체성)
- audit 페이지: `architecture/audits/260502_HHMM_audit_advise-recap-vote.md`
- 이 ralph 파일: `wiki/ralph/260502_0930_ralph_advise-recap-vote.md` (이미 정확)
- 모든 신규 페이지 `created: 2026-05-02`
