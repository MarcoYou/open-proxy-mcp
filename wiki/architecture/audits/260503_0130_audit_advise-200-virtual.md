---
type: audit
title: advise_vote 200기업 가상실험 (partial — STATUS REPORT)
created: 2026-05-03 01:30
domain: action
universe: 200 → 50 → 20 (scope 점진 축소, batch hang)
result: partial — 26 rows / 2 complete 회사 100% deterministic
---

# advise_vote 200기업 가상실험 audit (partial — ralph 시간 한계)

## 가정 (가상환경 명시)
- No current conversation context
- No web search
- MCP only (17 tool)
- as if it's the first question
- deterministic (temperature=0)

## 실행 결과

### Scope 변천 (ralph cap 압박)
- 초기 plan: 200 회사 × 3 run = 600 호출 (예상 30분)
- 1차 batch (50 worker x async): 5분에 ~10 rows → 추정 10시간 → 중단
- 2차 batch (50×3 sequential per company × 6 worker): 7분에 ~26 rows → 24 rows partial → 중단
- 3차 batch (20×3 sequential): hang (4 process running, 0 rows)
- 4차 sequential 10×3: hang (출력 0)

→ ralph cap 31 iter 안에 200 × 3 = 600 호출 완료 불가능 (DART API throttle + corpCode XML build + 6 upstream + Marco off도 회사당 25-40s).

## 부분 결과 분석 (50 sample 시도, 26 rows 회수)

### Status 분포 (26 호출)
- exact: 23 (88%)
- no_filing: 3 (12%)
- error / exception: 0

### Consistency 검증 (complete 회사 2건)

| 회사 | run 1 | run 2 | run 3 | 일치 |
|---|---|---|---|---|
| 삼성전자 (005930) | FOR=7, AGAINST=0, REVIEW=3 | 동일 | 동일 | ✓ 100% |
| 삼천당제약 (000250) | FOR=3, AGAINST=0, REVIEW=5 | 동일 | 동일 | ✓ 100% |

→ **n=2 sample 100% deterministic** (가이드 target ≥95%).

### Partial / Single 회사 (15건)
- 5 회사 2 run 데이터
- 10 회사 1 run 데이터
- 모두 동일 회사 내 결과 일치 (불일치 0건)

### 응답 시간
- 평균: 25.3s
- p95: 38.6s
- max: 42.4s

→ 단일 advise_vote 호출 비용이 큰 편. asyncio.gather 6 upstream + Marco off 옵션이어도 corp_gov_report (PDF/HTML 본문 fetch) + financial_metrics (4 endpoint × 2년) + director_evaluation (소집공고 본문 + parse_personnel_xml) 누적이 25s+ 차지.

## 매핑 분류 (success / soft-fail / hard-fail)

### success (정형 추출)
- agenda 리스트 (10/12 회사)
- 후보 이름 + 임기
- 회사 펀더멘털 (revenue / ROE / 부채비율 / 자본잠식 status)

### soft-fail
- agenda 추출 실패 시 director_evaluation의 fallback agenda 사용 (이미 구현)
- 후보 약력 자유 텍스트 (LLM 자연어 처리 영역)

### hard-fail (메모 침묵)
- 형사 / 사적 관계 / 동명이인 / 파산 (가이드 명시)

## 막힘 분석

### 1. corpCode XML 첫 빌드 (~30-60s)
새 process 시작마다 corpCode.xml fetch + 캐시 빌드. 6 worker 동시 시작 시 모두 대기.

### 2. DART API rate limit
1,000 req/min × 키 2개 = 2,000/min 한도. advise_vote 1회 = ~13 DART call. 6 worker 동시 = ~78 call/min worst case. 한도 안.
→ 한도 hit 아님. 다른 원인.

### 3. asyncio.gather 6 upstream 중 하나 hang
가능성 높음. corp_gov_report (PDF fetch + HTML 파싱)나 financial_metrics (4 endpoint) 중 하나가 timeout 없이 무한 대기.

### 4. csv write_lock 경합
가능성 낮음 (single async lock).

## STATUS REPORT (Ralph iteration 9-13)

### 가이드 ✅ 충족
- 유니버스 추출 (200 회사 csv 작성)
- 가정 (no context / no web / MCP only / deterministic) 엄격 적용
- Fixed question + Fixed schema 정의
- 매핑 3-tier 분류 명시
- partial 데이터로 consistency mechanism 작동 확인 (2/2 100%)

### ⚠ 미충족
- **600 호출 (200 × 3) 완료 불가** — 시스템 한계
- **per-company consistency 통계** — n=2만 (target 200)
- **score variance** — n=2 (계산 가능하나 underpowered)
- **unstable cases 분석** — 0 cases 발견 (n=2 한계)

### 막힘 원인
- batch 호출 hang (corpCode 또는 upstream 중 하나)
- ralph cap 31 iter 안 600 호출 비현실적

### 결정 요청 (사용자)
A. **현재 상태로 종료** — 26 rows partial + 2 complete 회사 100% deterministic 만으로 spirit 충족
B. **batch hang 디버그 + 재시도** — Phase 별도 ralph (timeout 추가 + 6 upstream 개별 timeout + cache pre-warm)
C. **scope 더 축소 (5 회사 × 3)** — 즉시 완료 가능 sample → consistency 분석

→ promise 정직 X — 가이드 200 × 3 미충족.

---

## Update — 5 회사 × 3 run sequential 완료 (option C 실행, n=5)

`PYTHONUNBUFFERED=1 uv run python` + asyncio.wait_for(timeout=60s) + sequential — 15 rows 정상.

### 핵심 finding: ⚠ Deterministic 60% (target ≥95% 미충족)

| 회사 | run1 | run2 | run3 | 일치 |
|---|---|---|---|---|
| 삼성전자 | F=7/A=0/R=3 | F=7/A=0/R=3 | F=7/A=0/R=3 | ✓ 100% |
| **KT&G** | F=5/A=0/R=5 | **F=4/A=0/R=6** | **F=4/A=0/R=6** | ✗ 1 안건 FOR↔REVIEW |
| **KB금융** | F=2/A=0/R=8 | **F=1/A=0/R=9** | F=2/A=0/R=8 | ✗ 1 안건 FOR↔REVIEW |
| LG화학 | F=3/A=0/R=7 | F=3/A=0/R=7 | F=3/A=0/R=7 | ✓ 100% |
| 한진칼 | F=5/A=0/R=5 | F=5/A=0/R=5 | F=5/A=0/R=5 | ✓ 100% |

**일치율 3/5 (60%) — 가이드 target ≥95% 미충족**.

### 비결정성 원인 분석 (KT&G / KB금융)

advise_vote 호출이 deterministic 하지 않은 원인 후보:
1. **shareholder_meeting v2 검색 + director_evaluation fallback 분기**: agenda 추출 source가 호출마다 다를 수 있음 (E type 검색 → fallback → director_eval). 분기 따라 추출 안건 list 다름.
2. **후보 매칭 substring** — `if nm and nm in title`이 후보 dict iteration 순서 의존. dict 순서는 Python 3.7+ insertion order 보장이지만 fetch 결과 자체가 순서 다를 가능성.
3. **DART API 응답 순서** — list.json이 동일 query에 대해 매번 동일 row 순서 보장하지 않을 수 있음. 첫 매칭 안건이 다르게 잡힘.
4. **corp_gov_report PDF cache** — first call vs subsequent call 결과 다를 가능성.

→ **action item**: deterministic 보장 위해 advise_vote 내부 정렬 + tie-break logic 명시 필요. Phase 3 별도 작업.

### 응답 시간
- 평균: 12.8s (sequential 단일)
- max: 32.5s
- min: 4.5s

### 결과
- ✅ Sequential + PYTHONUNBUFFERED + timeout 60s = batch 작동 (이전 hang 원인은 stdout buffer 추정)
- ⚠ Deterministic 60% — gate 미충족, 비결정성 source 식별 필요
- ⚠ 가이드 200 × 3 = 600 호출 ralph cap 안 비현실적

### Promise 정직 평가
- 가이드 명시 "≥95% 100% 일치" → 미충족 (60%)
- 사용자 결정 + advise_vote 비결정성 fix 필요
- → **promise X**

---

## 비결정성 root cause 정확 isolated (KT&G 3 run 안건별 비교)

| # | 안건 | run1 | run2 | run3 |
|---|---|---|---|---|
| 1 | 제38기 재무제표 승인 | FOR | FOR | FOR |
| 2 | 정관 일부 변경 | REVIEW | REVIEW | REVIEW |
| 3 | 이사 인원수 명확화 | REVIEW | REVIEW | REVIEW |
| 4 | 감사위원 선임 관련 조문 | REVIEW | REVIEW | REVIEW |
| 5 | 대표이사 사장 선임 방법 | REVIEW | REVIEW | REVIEW |
| **6** | **분기배당기준일 변경** | **FOR** ✗ | **REVIEW** | **REVIEW** |
| 7-10 | 사내/사외이사 선임 4건 | 동일 | 동일 | 동일 |

→ **단 1 안건 (cash_dividend) 만 변동**. 다른 9 안건 100% 일치.

### Root cause: financial_metrics 응답 변동
- `_decide_dividend` logic은 `fm_payload`의 `capital_impairment_status` / `net_income_krw` / `payout_ratio_pct` 검사
- `fm_payload` 없거나 `_safe` exception fallback → REVIEW
- `fm_payload` 정상 + 흑자 + payout < 80% → FOR
- → KT&G run1 fm 정상 fetch / run2,3 timeout 또는 일부 실패

### advise_vote의 6 upstream 응답 변동성
asyncio.gather + `_safe` wrapper:
```python
async def _safe(fn, *args, **kw):
    try: return await fn(*args, **kw)
    except Exception as exc:
        return {"tool": fn.__name__, "status": "error", "data": {}, ...}
```
→ exception 시 silent fallback. 호출 시점에 따라 timeout/network 변동으로 결과 다름.

### Phase 3 fix 권장
1. **각 upstream에 explicit timeout** (예: 20s) — exception 명시적 분리
2. **fm_payload retry on timeout** — 1회 재시도
3. **cache TTL 증가** — 같은 회사 짧은 시간 내 재호출 시 동일 결과
4. **결정 logic에 deterministic tie-break** — fm None 시 default REVIEW (현재도 그런데 partial fm 시 변동)

→ **이는 logic 결함이 아니라 upstream fetch 비결정성**. 진짜 deterministic 보장 위해 cache 또는 retry 필요.