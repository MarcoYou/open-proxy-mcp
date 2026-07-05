# 핸드오프 260705 — 밸류에이션 시계열 밴드 + 통화통일

**한 줄**: FY0 시장·섹터 과거 밴드 **완성·검증(유지)**. 통화통일 실험은 corruption 나서 **전체 롤백**.
분기 백필은 **DART 차단 지속**으로 여전히 미완.

목표(북극성): 정확한 수정주가 × 정확한 재무 → PER·PBR을 FY0·TTM 두 기준 + 시장/섹터/종목 시계열로.

---

## ✅ 완료 — 유지

### FY0 시장·섹터 과거 시계열 밴드
- `mkt_val_history`(시장) + `mkt_sector_val`(섹터)에 **76개 월말 시점(2020-01~2026-06) FY0 PER/PBR** 저장.
- `market` scope가 이제 시계열 반환(이전 현재값 1개 → 76시점). "2020년부터 코스피 PER 추이" 가능.
- **방법**: 각 월말 d마다 Σ보통주시총(krx_weekly) ÷ Σ지배순이익/자본(PIT FY, mkt_fund_hist). firm_history와
  동일 계산의 전종목 합산 = 지수 PER 표준. **FX 없음**(아래 통화 참조).
- **검증**: KOSPI 연말 PER/PBR이 실제 시장사와 일치 — 2023·24 PBR 0.99·0.88(코리아디스카운트), 2022 PER 11.4(실적급증).
- 스크립트: `scripts/market_val_history_backfill.py` (DART 0콜, ON CONFLICT로 FY0열만 갱신·TTM/MRQ 보존, 재실행 안전).

---

## ↩️ 롤백함 — 원위치 (건드리지 말 것, 재작업 대상)

### 통화통일(currency unify) 실험 → 전체 롤백
- **시도**: 22 외화 공시사(USD 12·CNY 9·JPY 1)의 mkt_fund_hist/q를 원화로 통일 저장 + 라벨 KRW화 →
  하위 read-time FX 제거 목적.
- **터진 것**: `scripts/unify_fund_currency.py`를 `--apply`로 **여러 번 실행** → 통화전환사 **두산밥캣(241560)**의
  일부 행이 ×fx **2회 적용**돼 6.4e15(6천조) corruption.
- **롤백 내역**: 22사 `mkt_fund_hist`(112행)+`mkt_fund_q`(176행) 삭제, `currency` 라벨 원복(USD/CNY/JPY),
  `mkt_fundamentals` 파생재무(ni_fy/ni_ttm/eq_fy/eq_mrq) NULL, orig_currency 제거. → 22사 = **"재수집 대기"**.
- 세션발 corruption **0** 잔존 확인.
- 스크립트(`unify_fund_currency.py`)는 **남겨둠** — 감지로직은 재사용 가능하나 apply 방식 재작업 필요(아래 2번).

---

## 🔜 테이블 병합 후보 — 3에이전트 독립검토(마이그레이션·정합성·devil's advocate) 완료 260705

세 관점 다 코드/DB 직접 읽고 판단. **A=진행(단 설계 1곳 수정) / B=지금 안 함(신규 버그 발견, 장기 백로그 유지)**.

### 병합안 A — `mkt_val_history` + `mkt_sector_val` → 단일 `mkt_val_history` — ✅ 승인(설계 수정 후 rename 작업에 묶어서)

**전(현재, 2테이블)**:
```
mkt_val_history(snap_dd, mkt, per_fy0, per_ttm, pbr_fy0, pbr_mrq, cap, ni_ttm, eq, cap_pref)  -- PK(snap_dd,mkt)
mkt_sector_val (snap_dd, mkt, sector, label, n, cap, per_ttm, pbr_mrq, cap_pref, per_fy0, pbr_fy0)  -- PK(snap_dd,mkt,sector)
```
8/10 컬럼 동일. 사실상 "시장전체 집계" vs "섹터별 집계"로 grain만 다른 같은 개념. (이름은 `mkt_val_history`
그대로 유지 — `mkt_` 접두사는 프로젝트 전체 도메인 네임스페이스이지 "시장전체 scope"라는 뜻이 아니므로,
병합 후에도 섹터행을 포함해서 담아도 이름이 안 어긋남. 병합 전 제안했던 `market_valuation_history`는
오히려 "시장전체만"으로 오해되기 쉬워 기각.)

**⚠️ 원안의 결함(3에이전트 전원 확인) — `sector=NULL`로 시장전체 표시하면 안 됨**:
- PostgreSQL은 **PK 컬럼에 NULL을 허용하지 않는다** — PK=(snap_dd,mkt,sector)에 NULL 삽입 시 에러.
- PK를 포기하고 일반 UNIQUE로 우회해도 표준 SQL은 **NULL ≠ NULL**이라 ON CONFLICT가 시장전체 행을
  매번 신규로 취급 → **매주 중복 INSERT가 계속 쌓인다.**

**✅ 센티넬 값 `_ALL`로 확정(사용자 결정 260705)**:
```
mkt_val_history(snap_dd, mkt, sector NOT NULL DEFAULT '_ALL', label, n,
                per_fy0, per_ttm, pbr_fy0, pbr_mrq, cap, cap_pref, eq, ni_ttm)  -- PK(snap_dd,mkt,sector)
-- sector='_ALL' = 시장전체, 그 외 값 = 해당 섹터
```
검증: 실제 `mkt_sector_val`의 기존 섹터코드 81개 전수 확인 결과 `0`/`00`/`000`류 충돌 **없음**. 게다가
이미 `_fold`(="기타/소규모" 섹터)라는 **언더스코어 접두 특수코드가 기존 관례로 존재** — `_ALL`이 이 관례와
일치해 신규 규칙이 아니라 기존 패턴을 따르는 선택. `WHERE sector='_ALL'`(시장 scope) /
`WHERE sector!='_ALL'`(sector scope)로 조회.

**수정해야 할 코드 지점(3에이전트 grep 전수 확인)**:
- `scripts/market_val_weekly.py` L49-66(DDL), L258-270(INSERT 2건→1건, 시장행 sector='_ALL')
- `scripts/market_val_history_backfill.py` L32-37(DDL), L89-99(INSERT 2건→1건)
- `scripts/market_val_agg.py` L169-187 — **레거시 스크립트, 스키마 이미 불일치**(sector 컬럼 자체 없음,
  IF NOT EXISTS라 지금은 no-op) — 병합 후 sector NOT NULL 제약 생기면 이 스크립트 INSERT가 깨짐.
  병합 시 **수정 또는 폐기 결정 필요**.
- `open_proxy_mcp/services/valuation.py`: `build_market_val_payload` → `WHERE sector='_ALL'`,
  `build_sector_val_payload` → `WHERE sector != '_ALL'`(또는 `sector=%s`)로 변경.

**배포 시퀀싱(A·B 공통 권장)**: ① 신규 스키마로 새 테이블 생성 + 백필(구 테이블 유지) → ② 코드를 신규
테이블 참조로 배포 → ③ 검증 후 구 테이블 drop. **in-place RENAME과 코드 배포를 동시에 하지 말 것**
(둘 중 하나만 먼저 나가면 배포서버가 옛/새 스키마 불일치로 `UndefinedTable` 에러 — cron은 예외캐치가
없어 워크플로 실패+스냅샷 유실, valuation.py는 `_pg_rows`가 캐치해 `db_error` 상태로 방어됨).

**최종 판정**: 진행 가능. 위 센티넬 수정 + `market_val_agg.py` 처리 결정만 하면 리스크 낮음(devil's
advocate도 반대 근거를 못 찾음 — 절대 크기 작아 성능 우려 무의미, 교차검증 상실 우려도 해당 없음).

### 병합안 B — `mkt_fund_hist` + `mkt_fund_q` → 단일 `mkt_finstat` — ❌ **병합 안 함(사용자 최종 결정 260705)**

> **결정**: 병합하지 않는다. 아래는 그 판단의 근거 기록(향후 상황이 바뀌어 재검토할 경우를 위한 참고용).

**전(현재, 2테이블 + 복사 로직)**:
```
mkt_fund_hist(isu_cd, fy, fs, ni, eq, ni_restated, eq_restated, fetched)      -- 연간, restated 정정 있음, PK(isu_cd,fy)
mkt_fund_q   (isu_cd, fy, quarter, reprt_code, fs, ni_cum, eq, fetched, ni_case, eq_case)  -- 분기, PK(isu_cd,fy,quarter)
```
실측(COALESCE(restated,raw) 기준 전수 대조): quarter=4 행과 hist 값 **불일치 0건** — `seed_q4()`가 매번
hist→q로 복사. hist가 원본, q의 Q4행은 복제본. PK는 병합해도 문제없음(quarter은 NULL 될 일이 없어 A같은
결함 없음) — 그럼에도 아래 사유로 **지금은 보류**.

**🆕 3에이전트 검토에서 새로 발견한 이유(devil's advocate)** — 이전엔 몰랐던 진짜 버그:
- **`ni_case`/`eq_case`가 quarter=4 행의 100%(14,916/14,916)에서 NULL.** `seed_q4()`의 INSERT문이 이
  컬럼 자체를 안 채워서(hist엔 케이스 컬럼이 없어 자연스러웠는데, 병합하면 "이 테이블 전 행엔 case가
  있다"는 암묵적 기대가 생김). **병합하면 전체 행의 25%(14,916/59,497)가 전수 케이스 분포 집계에서
  조용히 결측 처리** — 병합 자체가 만들어내는 신규 버그.

**기존에 알려진 이유(정합성·마이그레이션 에이전트)**:
- **침묵 오류 위험**: `_firm_fin_by_fy()`(valuation.py:371) 등 여러 지점이 `ORDER BY` 없이 "fy당 마지막
  읽힌 행"을 채택 — quarter=4 필터를 한 곳이라도 빠뜨리면 **크래시 없이 PER/PBR이 조용히 틀린 값**으로
  나옴(Q1 ni가 Q4 ni의 1/5 수준인 등 차이가 커서 실사용자가 알아채기도 전에 배포될 위험).
  구체 지점: `market_val_series.py`(fs_map L149, done판정 L152-154), `market_val_history_backfill.py`
  L54-56, `_firm_fin_by_fy` L372 — 전부 `AND quarter=4` 누락 검사 필요.
- **restated 백필 로직 재작성 필요**: `backfill_restated()`가 지금 hist 전체 대상 → quarter=4 필터로
  재작성해야 하는데, 병합 후엔 "같은 테이블 내에서 quarter=4만 갱신하고 quarter&lt;4는 안 건드림"을 코드
  스스로 보장해야 함(지금은 테이블 경계가 공짜로 보장해주던 것).
- **`ni_cum→ni` 전역 리네임** + `seed_q4()` 함수 자체 삭제(자기복사가 돼 무의미해짐) + `refresh_financials.sh`
  재배선 필요.
- **타이밍 충돌**: DART 분기백필 미완 + 통화통일 롤백이 이미 같은 fetch/저장 코드 표면을 건드리는 중 —
  스키마 병합까지 얹으면 디버깅 난이도 배가.
- `reprt_code` 컬럼은 write-only(다른 파일에서 read 안 함) — 드롭해도 downstream 영향 없음(확인됨, 병합
  시 참고).

**최종 판정: 병합 안 함(확정, 재검토 불필요 — 사용자 결정).** `mkt_fund_hist`·`mkt_fund_q`는 계속 별도
테이블로 유지한다. 위 근거(ni_case 비대칭 신규버그·침묵오류 위험·restated 로직 재작성 필요·타이밍 충돌)는
"왜 안 하기로 했는지" 기록으로만 남긴다.

## 📊 DB 용량 (260705, `scripts/db_usage.py` 실측)

```
[██████████░░░░░░░░░░] 247 MB / 500 MB (Supabase 무료티어 49%)
```
- **`krx_weekly` 208MB(84%)**가 압도적 1위 — 2015-12~ 전종목 주간시세, 행당 ~165바이트, 주당 ~2,750행 증가
  → 증가속도 **월 ~1.8MB·연 ~22MB**. 70% 경고선(350MB)까지 현재 속도로 **약 5~6년** — 당장 조치 불필요.
- 나머지 11개 테이블 합쳐도 15% 수준(mkt_fund_q 8MB, events 9MB가 다음 순위).
- 이번 세션에 드랍한 4개 stale 테이블은 용량 기여 미미(수 MB) — 드랍은 네이밍/위생 목적이었지 용량 목적 아님.
- 정기적으로 `python3 scripts/db_usage.py`로 재점검 권장(무료티어 70% 넘으면 스크립트가 자동 경고).

### `events` 테이블 용량 관리

사용자 활동 관련 데이터라 세부 정책은 wiki에 기록하지 않음(비공개 유지). 용량 관리 방안이 별도로
운영 중이라는 사실만 표기 — 세부는 로컬 메모리에만 보관.

### `mkt_valuation`(종목별 주간 스냅샷) — 2순위 증가 후보, **용도 확인 완료(260705)**

260705 신설(현재 1주치 2,599행)이라 아직 작지만, **종목 단위**라 시장/섹터 집계보다 훨씬 빨리 커짐 —
주 2,599행 추정 시 **연 ~43MB**(krx_weekly보다 빠름). **확인 결과: 실제로 쓰인다** —
`build_firm_history_payload`(valuation.py:557-563)가 이 테이블을 직접 읽어 "최근/현재 촘촘한 포인트"로
반환(과거 PIT밴드는 mkt_fund_hist/q가 별도 담당, 이 테이블은 cron이 앞으로 축적할 최신 구간용). 삭제·설계
고정 대상 아님 — 정상 성장. 다만 용량 관리는 필요하니 시장/섹터처럼 오래된 과거행을 별도 압축 보관하는
방안은 장기적으로 검토 가능(지금 급하지 않음, krx_weekly와 같은 급의 우선순위로 취급).

### 검토했으나 병합 비권장

| 쌍 | 이유 |
|---|---|
| `events` ↔ `krx_call_log` | 이름은 비슷해도 목적 다름 — events=MCP 툴콜 텔레메트리, krx_call_log=KRX API 분당한도 2PC 합산 카운터. grain·소비자 다름. |
| `dart_capital_events` ↔ `krx_base_resets` | adj_factor_v3가 조인해 쓰지만 소스가 다름(공시 vs 거래소 실측). 병합하면 원천 구분 소실. |
| `mkt_fundamentals` ↔ `mkt_fund_q` | fundamentals는 mkt/induty/currency 등 fund_q엔 없는 메타데이터 + 빠른조회 파생캐시. 역할이 달라 유지가 맞음. |

## 🔜 다음 작업 (우선순위)

1. **DART 차단 해제 대기 → 분기 백필 재개**
   - 지금도 차단(로컬 IP). auto-resume watcher가 홈페이지 200을 보고 오판했으나 실제 fetch는 `ReadError`
     연속으로 빈손 중단. 사이트/`curl`로 **실제 해제 확인 후** `market_fund_quarterly.py --fetch --conc 2`.
   - 남은 대상: 미수집분(~600사, isu_cd 20만번대~) + 롤백한 22 외화사.
   - ⚠️ 하드룰: 분당 1000콜 초과 = 24h IP차단. 동시성 1~2, 재시작 churn 금지(이전 프로세스 완전종료 확인).
     [[hard-rate-limit]] · [[feedback_dart_batch_restart]].

2. **통화 문제 "제대로" 풀기** (heuristic in-place 변환 재시도 금지)
   - **근본원인**: mkt_fund_hist/q는 연도별 공시통화 그대로 저장(통화컬럼 없음). 두산밥캣은 fy≤2021 원화 /
     fy2022+ USD로 **연도별 통화 상이**. 하위 read-time FX(`_firm_fin_by_fy/_by_q`, `market_val_weekly`)가
     mkt_fundamentals.currency 단일 라벨을 전 연도에 곱해 옛 연도 폭증.
   - **올바른 해법**: 수집 시점에 `statement_currency()`로 통화 감지 → **원화 변환해서 저장**
     (`market_val_series.py`·`market_fund_quarterly.py`의 추출부에 삽입). 이미 `valuation.py:799` live fetch가
     이 패턴 사용. 수집단에서 원화 통일하면 하위 FX 불필요 + 통화전환사 자동 정합.
   - unify_fund_currency의 감지(magnitude 1차 + cap-anchor 회색지대 + 경계스무딩)는 **기존 데이터 정정용**으로
     재활용 가능. 단 **apply는 1회만, 반드시 DB backup 후, idempotency 재검증**.
   - 참고: [[project_fund_currency]] 메모리.

3. **TTM/MRQ 과거 밴드** — 분기 백필 완주 후. `market_val_history_backfill.py`와 동일 방식, 분모만 mkt_fund_q(TTM/MRQ)로
   교체. `per_ttm`/`pbr_mrq` 컬럼 이미 준비됨.

4. **firm_history 통화전환사 버그** — 위 2번 해결 시 자동 해결.

---

## ⚠️ 별도 발견 (내 소행 아님) — read-only sweep 완료(scripts/scale_sweep_readonly.py, scale_guard 재사용)

- **mkt_fund_hist**: HARD **0건**. 032680(소프트센) fy2022 raw ni/eq는 오염(1.07e16/6.81e16)이지만
  `ni_restated`/`eq_restated`에 이미 정정값 존재(107억/680억, 이웃연도 정합) — 프로덕션 코드가 전부
  `COALESCE(restated, raw)`로 읽어 **이미 해결된 상태**. (이전 버전 핸드오프에 "미해결"로 잘못 기재했었음 — 정정.)
- **mkt_fund_q**: HARD **4건** — 진짜 미해결. 분기 테이블엔 restated 컬럼 자체가 없어서.
  - 007720 fy2024Q3 · 032080 fy2019Q3 · 060310 fy2022Q3 · 069330 fy2020Q1
  - 전부 동일 패턴: 그 분기 하나만 ni_cum·eq가 이웃분기 대비 정확히 ×100만, 그 외 전 분기 정상.
    소프트센과 같은 DART 원단위/백만원단위 오류로 추정.
  - **미결정**(사용자와 논의 예정): 정정(÷1e6, 이웃분기로 값 복원) vs 무효화(NULL+flag) vs mkt_fund_q에도
    restated류 인프라 신설. 재발방지로 quarterly fetch에도 scale_guard 배선 여부 확인 필요.
- soft 44건(mkt_fund_hist, 전년대비 배수점프) — scale_guard 문서상 오탐률 97.5%로 hard 승격 금지된 신호,
  조치 불필요(정상 실적급변 가능성 높음).

## 🔜 대기 중인 rename (지금 안 함 — 다음 배포 사이클에 코드+DB 원자적으로 같이)

DART 차단·통화이슈·미배포 커밋 13개로 상태가 얽혀있어 **지금은 rename 안 함**. 분기 백필 완주 + QA 완료 후
다음 배포 사이클에: DB `ALTER TABLE RENAME` + 코드 105곳(14개 파일) 일괄치환 + wiki 갱신을 **같은 타이밍에**
(안 그러면 배포서버가 옛/새 이름 불일치로 깨짐).

| 현재 | 제안 | 근거 |
|---|---|---|
| `mkt_fund_hist` | `mkt_finstat_y` | 연간 재무제표라는 뜻이 이름에서 바로 읽히게 |
| `mkt_fund_q` | `mkt_finstat_q` | 분기 재무제표 |
| `mkt_valuation` | `firm_valuation_snapshot` | isu_cd 있는 **종목별** 현재값인데 "market"이라 시장집계로 오해되기 쉬움 |
| `mkt_val_history` | `market_valuation_history` | 실제론 **시장 전체 집계** 히스토리(종목 아님) — 위와 뒤바뀐 느낌 정정 |
| `mkt_sector_val` | `sector_valuation_history` | 위 둘과 접미사(`_history`) 통일 |
| `events` | `tool_call_events` | `dart_capital_events`(공시)와 이름이 겹쳐 혼동 |
| `krx_reset_days` | `krx_reset_sweep_checkpoint` | 실제론 리셋 데이터 아니라 `krx_base_resets.py` 스윕 진행 체크포인트 (드랍 대상 아님, 살아있음) |

`mkt_fundamentals`는 "파생 요약"이란 이름이 이미 적절 — 유지.
`fs` 컬럼(CFS/OFS)은 이름 그대로 두고 스키마 옆 주석만 보강 권장(사소).

## ✅ 완료 — stale 테이블 4개 백업 후 드랍 (260705)

read-only 네이밍 감사 중 발견한 **완전 고아 테이블**(코드 참조 0건 확인 후) 백업+드랍 완료:

| 테이블 | 행수 | 확인한 근거 |
|---|---|---|
| `krx_adj_factor`(v1) | 1,580 | 코드 참조 0건. v3가 정본(wiki 기존 문서화) |
| `krx_adj_factor_v2` | 1,751 | v3 생성스크립트가 1회성 승계 입력으로만 참조, 서빙엔 미사용 |
| `krx_ledger_days` | 2,738 | 코드 참조 0건 |
| `dart_sweep_done` | 3,051 | 코드 참조 0건(read/write 어디에도 없음) |

- **백업**: CSV 전량 `wiki/handoff/backups_260705/{table}.csv` (행수 원본과 일치 확인).
- **드랍 후 회귀체크**: firm/market/sector 3개 scope 전부 `status=ok` 정상 확인.
- **주의**: `krx_reset_days`·`krx_stock_flags`는 이름이 비슷해도 **살아있음**(각각 base_resets 스윕 체크포인트,
  valuation firm_history 경고) — 드랍 대상 아니었고 안 건드림.
- wiki `data-storage-registry.md` 갱신 완료(드랍된 4개 제거 + 백업 위치 명시).

## 상태 스냅샷
- DART: **차단 지속**(로컬 IP). fly 프로덕션은 다른 IP라 서빙 정상.
- 미배포 커밋 13개(이전 세션 firm_history TTM/MRQ, SSOT derive 등). 배포는 백필+QA 완료 후 권장.
- 백그라운드 프로세스/모니터: 없음(정리됨).
- 신규 스크립트: `market_val_history_backfill.py`(유지·good), `unify_fund_currency.py`(재작업 대상).
