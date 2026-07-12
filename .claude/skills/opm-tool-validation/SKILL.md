---
name: opm-tool-validation
description: OPM tool·지표의 정확도를 시장 표본으로 검증할 때 쓰는 절차. "이 지표/파싱 맞는지 전수 검증", "KOSPI/KOSDAQ 표본으로 before/after", "멀티에이전트로 교차검증" 요청 시 사용. DART 레이트리밋 안전 캐시 수집 → DART-0콜 멀티에이전트 2룹 검증.
metadata:
  short-description: OPM 지표/파싱 시장표본 검증 (캐시수집→멀티에이전트 2룹)
---

# opm-tool-validation

OPM의 tool·지표·파싱 로직이 **시장 전반에서 실제로 맞는지** 검증하는 절차. 작은 샘플 단정 금지,
큰 표본 × 이중검증(기계 + 사람-독자) 원칙(CLAUDE.md 작업원칙 2·4)을 **레이트리밋 안전하게** 실행한다.

이론(왜 측정이 거짓 결론을 내는가 — 측정 함정 5패턴·체크리스트)은 만들지 말고 참조:
`wiki/lessons/agenda-parser-validation-260621.md`. 이 스킬은 그 위의 **실행 절차**다.

## 언제 쓰나
- "이 지표(총차입금·순이익·배당 등) 시장에서 맞아?" / "파싱 전수 검증하자" / "before/after 회귀"
- 가설("A를 B로 고치면 정확도↑")을 세웠을 때 — **즉시 구현 금지**, 이 절차로 엣지 상상→테스트→통계검증 후 실행.

## 핵심 원칙 (이 스킬이 강제하는 것)
1. **데이터 수집과 검증을 분리한다.** DART 호출은 **중앙(나)에서 딱 1번**, throttled로 캐시에 저장.
   그 다음 모든 검증(멀티에이전트 포함)은 **캐시만 읽어 DART 0콜**. 에이전트가 각자 DART를 때리면
   레이트리밋이 폭발한다(절대 금지).
2. **패널을 측정 단계에 투입한다.** 수정 검토뿐 아니라 **측정 도구 자체의 버그**를 잡으려고 전문가
   렌즈를 측정에 붙인다(실측: 예수금 금융오탐·소계 과보정을 5인 패널이 잡음).
3. **2룹: 발견 → 수정 → 재검증.** 1룹에서 찾고, 고치고, 2룹에서 회귀(과대교정 0)를 재확인.
4. **침묵누락을 잡는다.** "무엇을 조용히 버렸나" — 분류기가 EXCL한 행 중 대상 냄새가 나는 것을 표면화.
5. **표본 편향을 깬다.** KOSPI만으로 만든 사전은 KOSPI에서 안 깨진다 — KOSDAQ·중소형·엣지를 더한다.

## 절차

### 1) 표본 설계 + throttled 캐시 수집 (DART 호출 유일 지점)
- 표본: 기존 캐시 재사용 + 신규분만 fetch(중복 회피). 시장 분산 — KOSPI + KOSDAQ + 엣지케이스 ~10.
  - 시장구분·업종(induty)은 Postgres `mkt_fundamentals`(mkt·corp_code·induty)에서 (DART 아님).
  - corp_code는 `configs/master.db` 또는 mkt_fundamentals.
- **DART 하드룰 (절대 위반 금지 — 위반 시 24h IP 차단)**: 동시성 **1~2** · 콜 사이 **sleep 0.9s+** ·
  `httpx.ReadError`/status `020`/`011`/`012` 감지 시 **즉시 ABORT**. 100+사는 fly machine 고려.
- 저장: BS만 필요하면 BS행만(회사당 1~2콜), induty·mkt·fs_div 동봉(교차검증용). jsonl 한 줄=한 회사.
- 스크립트 골격: `scripts/` 골격 섹션 참조. 실행 후 "abort=False·오류 목록"을 반드시 확인.

### 2) 기계 검증 (내가 먼저, 캐시 전용)
- **production 함수를 직접 import**해 캐시 전수에 돌린다(측정 스크립트가 아니라 실제 코드 경로).
- **before/after diff**: 옛 로직 재현 함수 vs 새 값. 과소복구 수·중앙 과소율 + **과대교정(new<old) 검사**
  (고치다 깨진 것 0인지).
- **침묵누락 후보**: EXCL된 행 중 대상 토큰(차입·사채 등) 포함 & 정상배제 토큰(금융부채·파생·자산) 제외.
- **신뢰티어·CONFLICT·REVIEW** 분포. 이 단계에서 확정 가능한 건 에이전트 없이 확정.

### 3) 멀티에이전트 검증 (캐시 전용, DART 0콜) — 판정이 필요한 것만
- 슬라이스별 병렬 배치(예: KOSPI 회귀 / KOSDAQ 침묵누락 / 금융·지주 판별 / 엣지 특수케이스).
- 각 에이전트 프롬프트에 **"DART 추가호출 절대 금지 — 캐시 파일 경로만 읽어라"** 명시.
- **패널을 측정에 투입**할 땐 도메인 렌즈를 명시(회계 KICPA/AICPA·데이터 DART API·도메인 전문가·실무자).
  각자 "현재 스펙 → 문제 → 권고 + 실측근거", 심각도(BLOCKER/IMPORTANT/NICE)로.
- 에이전트 결과가 애매하면 기계검증으로 교차확인(나의 육안 우선, 에이전트는 보조).

### 4) 수정 → 2룹 재검증
- 에이전트/기계가 확정한 **진짜 버그만** 수정(원칙 4 — 가설 즉시실행 금지, 근거 확인 후).
- 같은 기계검증을 재실행: **침묵누락 0 · 과대교정 0 · 대상 정상화 · 회귀 유지** 확인.
- production end-to-end(`build_*_payload` 실제 호출)로 최종 확인.

### 5) 문서화
- 회고 lesson: `wiki/lessons/{topic}-yymmdd.md`(Context/Did/Improved/Trade-off/Takeaway), README ② 또는 ④ 인덱스 추가.
- tool 문서(`wiki/tools/*.md`) 파싱전략·변경이력·출력schema 갱신. `python3 scripts/wiki_lint.py --strict` 통과.
- 측정 도구가 스스로 낸 오류(도구 버그)도 기록 — 다음에 같은 함정 피하려고.

## 스크립트 골격 (스크래치패드에 복사해 지시에 맞게 수정 후 실행)

**수집** (`fetch_*.py`) — DART 하드룰 준수:
```python
import asyncio, httpx
from open_proxy_mcp.dart.client import get_dart_client, DartClientError
ABORT = asyncio.Event(); sem = asyncio.Semaphore(2)   # 동시성 1~2
async def fetch_one(ticker, corp_code, ...):
    if ABORT.is_set(): return
    async with sem:
        client = get_dart_client()
        try:
            data = await client.get_fnltt_singl_acnt_all(corp_code, YEAR, REPRT, fs)
            ...  # BS만 추출, induty·mkt·fs_div 동봉
        except DartClientError as e:
            if e.status in ("020","011","012"): ABORT.set(); return  # 과호출/차단 즉시중단
        except (httpx.ReadError, httpx.ConnectError, httpx.RemoteProtocolError):
            ABORT.set(); return                                       # 전송오류 즉시중단
        await asyncio.sleep(0.9)                                      # 콜 사이 sleep
```

**검증** (`verify_*.py`) — production 함수 직접 import, 캐시만:
```python
from open_proxy_mcp.services import <module> as m   # 측정 재구현 X, 실제 코드
cos = [json.loads(l) for l in open(CACHE) if "bs" in json.loads(l)]
def old_value(bs): ...        # 옛 로직 재현(before)
for co in cos:
    new = m.<production_fn>(co["bs"])   # after
    # before/after · 과대교정(new<old) · 침묵누락(EXCL인데 대상토큰) · 신뢰티어 집계
```

## 주의
- **캐시는 raw가 아니다** — 스크래치패드에 두고 `wiki/raw/`엔 절대 안 넣는다(외부원본 규칙과 무관).
- 작업 지시가 바뀌면 **스크립트도 그 지시에 맞게 수정 후 재실행**(stale 로직 재사용 금지 — CLAUDE.md).
- 실측 사례: `wiki/lessons/financial-metrics-borrowings-260713.md`(총차입금 account_id 이관, 298사 2룹).
