---
type: architecture
title: Multi-upstream Tool Pattern (concurrency + race fix 표준)
date: 2026-05-03
related_tools: [advise_vote_before_meeting, recap_vote_after_meeting, proxy_contest, ownership_structure, corp_gov_report]
related_audits: [260503_1847_audit_phase4_final]
---

# Multi-upstream Tool Pattern

OPM action/data tool이 **여러 upstream service를 asyncio.gather로 병렬 호출**할 때 반드시 갖춰야 하는 5 요소.

Phase 4 advise_vote 200×3 검증 (91.4% → 100.0%, regression 0)에서 도출. 같은 패턴 미적용 tool들이 동일 race/timeout 문제를 겪을 것으로 예상되므로 표준화.

## 문제 — Phase 2/3에서 발견

advise_vote 200 회사 × 3 run batch:

| Phase | 일치율 | timeout | root cause 가설 | 결과 |
|---|---|---|---|---|
| 2 | 91.4% | 3건 | retry 1회 부족 | retry 3회 적용 → 거의 변화 X |
| 3 | 91.9% | 15건 ⚠ | alias / parser 문제 | F0/F2/F3 모두 fix해도 91.9% 그대로 |
| 4 | **100.0%** | **0건** | **infra (corpCode race + cache 없음)** | F6-F11 단번에 100% |

logic (alias / parser / 정규식)을 의심했으나 진짜 병목은 **네트워크 race + 결과 cache 부재**였다. 같은 위치에 같은 함정이 다른 tool에도 존재.

## 5 요소 표준

### 1. corpCode lock (asyncio.Lock)

**문제**: N worker가 첫 호출에서 `_load_corp_codes()` 동시 호출 → 50MB ZIP 다운로드 N번 → race 시 일부 worker httpx ReadError로 hang.

**해결**: 모듈 레벨 `asyncio.Lock`으로 한 번만 다운로드, 나머지는 cache 채워질 때까지 대기.

```python
# dart/client.py
_corp_code_lock: asyncio.Lock | None = None

async def _load_corp_codes(self):
    global _corp_code_cache, _corp_code_lock
    if _corp_code_cache is not None:
        return _corp_code_cache
    if _corp_code_lock is None:
        _corp_code_lock = asyncio.Lock()
    async with _corp_code_lock:
        if _corp_code_cache is not None:  # double-check
            return _corp_code_cache
        # 다운로드 + 파싱 + cache 채움
```

### 2. corpCode retry (httpx 끊김 회복)

**문제**: 50MB 다운로드 중 `httpx.ReadError` / `ConnectError` / `ReadTimeout` 발생 시 즉시 fail.

**해결**: 3회 retry + exponential backoff (1/2/4s).

```python
for attempt in range(3):
    try:
        data = await self._request_binary("corpCode.xml", {})
        # ... parse
        return corps
    except (httpx.ReadError, httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError):
        if attempt < 2:
            await asyncio.sleep(1.0 * (2 ** attempt))
raise DartClientError("CORPCODE_DOWNLOAD_FAILED", ...)
```

추가: `_request_binary` 안에서 corpCode.xml은 timeout 60s → 120s로 별도 처리.

### 3. per-call timeout (asyncio.wait_for)

**문제**: 1개 upstream이 hang하면 전체 `asyncio.gather`가 잠식 → 외곽 wait_for cap에서 fail.

**해결**: 각 upstream을 60s로 wrap. 한 worker만 fail해도 나머지는 정상 회수.

```python
async def _safe(fn, *args, **kw):
    for attempt in range(3):
        try:
            return await asyncio.wait_for(fn(*args, **kw), timeout=60.0)
        except asyncio.TimeoutError:
            if attempt < 2:
                await asyncio.sleep(0.5 * (2 ** attempt))
        except Exception:
            if attempt < 2:
                await asyncio.sleep(0.5 * (2 ** attempt))
    return {"status": "error", "warnings": [f"3회 retry 모두 실패"], ...}
```

### 4. Semaphore 동시성 제한 (3-worker)

**문제**: 6 worker 동시 = DART API rate limit margin 부족 + race 가능성.

**해결**: 내부 `asyncio.Semaphore(3)`로 한 advise_vote 안에서 동시 upstream 3개로 제한. (외부 batch 단위 동시성과 별개.)

```python
_UPSTREAM_SEM = asyncio.Semaphore(3)

async def _safe_throttled(fn, *args, **kw):
    async with _UPSTREAM_SEM:
        return await _safe(fn, *args, **kw)
```

### 5. Process-level result cache

**문제**: 같은 process 내 같은 회사 호출이 매번 fresh DART fetch → 결과 미세 변동 (DART API row 순서 비결정성) → run1/run2 결과 다름.

**해결**: dict cache (key: corp_code + tool + scope + year + meeting_type). status="error"는 cache X (재시도 기회 유지).

```python
_ADVISE_RESULT_CACHE: dict[tuple, dict] = {}

async def _safe(fn, *args, **kw):
    cache_key = (corp_code, fn.__name__, kw.get("scope"), kw.get("year"), kw.get("meeting_type"))
    cached = _ADVISE_RESULT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    # ... call + cache (성공 시만)
```

## 부가 — 정정공고 (notices[0]) 처리

`director_evaluation.fetch_appointments`처럼 DART list.json에서 `notices[0]` 사용 시 `[기재정정]`이 첫 매칭. 정정 본문이 변경 부분만 포함하면 parse 실패.

**표준**:
- 시간 desc 순으로 최대 3개 시도
- 첫 후보가 빈 결과 → 다음 후보 fallback
- 정정 우선 (full re-publish 가정), parse 성공 시 채택

```python
for idx, candidate in enumerate(notices[:3]):
    doc = await client.get_document_cached(candidate["rcept_no"])
    parsed = parse_personnel_xml(doc.get("html", ""))
    appointments = parsed.get("appointments", [])
    if appointments or agenda_titles:
        notice = candidate
        break
```

## 적용 대상 (체크리스트)

| Tool | gather 수 | 상태 | TO_DO |
|---|---|---|---|
| `advise_vote_before_meeting` | 6 upstream | ✅ Phase 4 적용 (commit `d949f68`) | - |
| `recap_vote_after_meeting` | 8 upstream (4+4) | ❌ 미적용 — advise_vote와 동일 위험 | 🔴 즉시 |
| `proxy_contest` | 4+4 upstream | ❌ 미적용 | 🟡 검증 필요 |
| `ownership_structure` | 3 upstream | ❌ 미적용 | 🟡 batch 시 위험 |
| `corp_gov_report` | 2 upstream + N doc gather | ❌ 부분 (소량) | 🟢 낮음 |

`notices[0]` / `items[0]` / `filings[0]` 패턴 (정정공고 미처리):

| 위치 | 코드 | 위험 |
|---|---|---|
| `director_evaluation.py:86` | `notice = notices[0]` | ✅ Phase 4 fix됨 |
| `value_up_v2.py:127, 130, 394` | `plan_items[0]` / `kind_items[0]` | 🟡 검증 |
| `corp_gov_report.py:386` | `filings[0]  # 최신` | 🟡 검증 |
| `shareholder_meeting.py:395` | `result_items[0]` | 🟡 검증 |
| `proxy.py:421` | `company_items[0]` | 🟢 낮음 |

## 신규 multi-upstream tool 만들 때

체크리스트:
1. [ ] `_load_corp_codes()` 호출 전 lock + retry 보장? → 자동 (dart/client.py에 적용됨)
2. [ ] `asyncio.gather`로 N upstream 병렬 → `_safe` wrapper로 retry + per-call timeout 60s
3. [ ] `_UPSTREAM_SEM = Semaphore(3)`로 동시성 제한
4. [ ] 모듈 레벨 result cache dict 정의
5. [ ] DART list.json 첫 매칭 (`[0]`) 사용 시 시간 desc fallback 3개

advise_vote (`services/advise_vote.py`)를 reference 구현으로 복붙.

## 관련 문서

- [[260503_1847_audit_phase4_final]] - Phase 4 검증 audit
- [[architecture/3-tier-fallback]] - DART API → 웹 → OCR fallback (별개 패턴)
- [[architecture/data-collection]] - 데이터 수집 전반
