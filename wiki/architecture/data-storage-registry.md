---
type: architecture
title: 데이터 저장소 레지스트리 — Supabase 전 테이블 (item·주기·검증·목적·workflow)
updated: 2026-07-05
---

# 데이터 저장소 레지스트리 (Supabase Postgres)

> **단일 출처**: 어떤 데이터가 · 어떤 테이블에 · 어떤 item(컬럼)명으로 · 누가/어떤 주기로 갱신하고 ·
> 어떤 검증을 거쳤고 · 무엇에 쓰이는지. 테이블 추가/변경 시 이 문서를 함께 갱신한다.
> 연결: `DATABASE_URL`(로컬 .env + fly secrets, [[environment-secrets]]).
>
> **설계 원칙 (260705 확정)**
> 1. **DB-first**: 저장해둔 걸 읽어 서빙하고 주기적으로 갱신한다. 외부 API는 (a) 갱신 배치와
>    (b) 실시간 정밀 조회(firm 심층)에만 쓴다. 이유: KRX=개인키 1개·일 10,000 한도, DART=분당 1,000.
> 2. **주간 수렴 저장**: 시세·스냅샷류는 매일 수집하되 같은 ISO주 슬롯에 덮어써(수렴) **주 마지막
>    거래일만 영구 보존** — 항상 전날 종가까지 표시하면서 무료티어 안 넘김. (fx·krx_weekly·스냅샷 공통)
> 3. **불변만 영구**: 과거 확정값(분기말 환율·확정 주간 종가)만 영구 캐시. "오늘"류 변동값은 미저장.
> 4. **컬럼명 명시**: 모든 INSERT는 컬럼명 명시(위치의존 금지 — 260704 mkt_fund_hist 사고, CLAUDE.md).

## 한눈에 — 그룹별 지도

| 그룹 | 테이블 | 갱신 주체 | 주기 |
|---|---|---|---|
| **밸류에이션 서빙** | krx_weekly · mkt_valuation · mkt_val_history · mkt_sector_val · fx_rate | valuation tool(수동갱신 겸) + `market_val_weekly.py` cron | 일별 수집→주간 수렴 |
| **밸류에이션 원천** | mkt_fundamentals · mkt_fund_hist(FY2018~2024) | `market_val_agg.py --fetch` / `market_val_series.py --fetch` | 분기(보고 시즌) / 연 1회(+과거 FY 백필) |
| **수정주가 파이프라인** | krx_adj_factor_v3 · krx_base_resets · krx_shares_ledger · krx_reset_days · krx_stock_flags · dart_capital_events | 전용 스크립트([[adjusted-price-timeseries]]) | 이벤트/수동 |
| **운영·미터링** | events · krx_call_log | 앱 자동 | 실시간 |

---

## 1. 밸류에이션 서빙 계층 (valuation tool scope=market/sector/firm_history + firm 시세)

### `krx_weekly` — 주별 전종목 시세 (검증 자산, 2015-12~)
- **item**: `bas_dd`(YYYYMMDD 기준일) · `isu_cd`(단축코드) · `mkt`(KOSPI/KOSDAQ) · `close`(종가 원) ·
  `mktcap`(시총 원) · `list_shrs`(상장주식수). PK(bas_dd, isu_cd). ~548주 × ~2,750종목 = 132만 행.
- **갱신**: ① valuation tool의 daily-refresh(`_ensure_krx_fresh`) — 프로세스당 하루 1회, 최신 거래일
  전종목(KRX bydd_trd 2콜)을 같은 ISO주 수렴(트랜잭션 DELETE+INSERT)으로 기록. ② cron
  `market-val-weekly.yml`(화~토 KST 10:17)이 조회 없어도 매일 보장.
- **안전장치(260705 QA)**: 과거로 롤백 금지(`dd > db_latest`만 기록 — KRX API가 저장된 거래일
  데이터를 일시 소실하는 실측 사례 방어) · 두 시장 모두 있을 때만 기록(반쪽 스냅샷 금지).
- **검증**: 원장 재생과 132만 포인트 대조 불일치 0([[adjusted-price-timeseries]]) + 260705 QA(과거
  548주 불변·신규주 품질·7/2 -15.8%는 실제 서킷브레이커 웹검증).
- **목적/소비자**: valuation firm 시세(주가·시총·상장주식수) · 주간 스냅샷 배치의 시총 원천 ·
  수정주가 파이프라인(adj_factor_v3·base_resets) · market_val_series의 분기말 시총.

### `mkt_valuation` — 종목별 밸류 주간 스냅샷 (260705 신설)
- **item**: `snap_dd` · `isu_cd` · `mkt` · `sector`(KSIC 하이브리드 버킷) · `cap`(**보통주 시총**) ·
  `cap_pref`(**우선주 시총 별도** — 배수 미포함) · `per_fy0` · `per_ttm` · `pbr_fy0` · `pbr_mrq`.
  PK(snap_dd, isu_cd). ~2,600행/주.
- **산식(260705 보통주 기준 확정)**: PER=**보통주** cap÷지배순이익(TTM/FY0), PBR=보통주 cap÷지배자본
  (MRQ/FY0). **ni≤0·eq≤0 → NULL(N/M)**. 비KRW 22사는 fx_rate(기말환율)로 KRW 환산 후 산출.
  우선주 시총(cap_pref)은 배수에서 제외(KRX 지수 PER 관행) — 분모 이익·자본엔 우선주 몫 포함이라
  소폭 하향 편향(클래스 분리는 공시 부재로 불가). 전수 검증: 2,599사 cap==krx 보통주시총 불일치 0,
  cap_pref==우선주형제합 불일치 0(260705).
- **갱신**: `market_val_weekly.py`(cron) — 재무는 mkt_fundamentals, 시총은 krx_weekly. API 0콜(KRX
  종목유형 2콜 제외). 같은 ISO주 수렴.
- **검증**: 260705 QA — 표본 17사 손계산 재현 <1e-6 · 비KRW 22사 전수 환산 확인 · 커버리지
  2,599/2,599 누락 0.
- **목적/소비자**: valuation `scope=firm_history`(종목 PER/PBR/시총 시계열) · `scope=sector`의 기업
  vs 섹터 비교.
- **firm_history 시계열 = compute-on-query(저장 X)**: 주간 스냅샷(현재·미래 축적)에 더해, **연말 PIT
  밴드**를 질의 시 `krx_weekly`(연말 보통주 시총) × `mkt_fund_hist`(그 시점 최신 확정 FY 재무)로 즉시
  산출(백필 배치·저장 없음, 2~3 쿼리). PIT: 연말 YYYY→FY(YYYY−1) 사용(FY는 익년 3월 공시 →
  look-ahead 방지). 시총 기반이라 수정주가 조정 불변, 비KRW는 FY 기말환율 환산. spinoff 종목은
  krx_stock_flags 경고. 검증 260705: 삼성 2024말 PBR 0.91·SK하이닉스 2024 PER N/M(FY2023 순손실)
  등 시장 서사와 정합.

### `mkt_val_history` — 시장 전체 aggregate 주간 스냅샷
- **item**: `snap_dd` · `mkt` · `per_fy0/per_ttm/pbr_fy0/pbr_mrq`(시총가중=Σ보통주 시총÷Σ지배순이익·자본) ·
  `cap`(Σ보통주 시총) · `cap_pref`(Σ우선주 시총 — 배수 미포함) · `ni_ttm` · `eq`. PK(snap_dd, mkt).
  주 2행(KOSPI·KOSDAQ).
  ※ 표기 PER의 분모별 Σ시총은 해당 지표 보유 종목만 — cap÷ni_ttm 재계산과 다를 수 있음(명시됨).
- **갱신**: `market_val_weekly.py`(cron). 구 `market_val_agg.py --report/--snapshot`은 FX 미환산이라
  **deprecated**(KOSDAQ PER 5.7% 왜곡 실측) — 저장 정본은 weekly 하나.
- **검증**: 260705 QA — 9필드 독립 재계산 소수 4자리 일치, 신·구 차이 전액 FX 환산 효과로 설명.
- **목적/소비자**: valuation `scope=market` ("코스피 지금 싸?"·시장 밸류 추이).

### `mkt_sector_val` — 산업(KSIC)별 aggregate 주간 스냅샷 (260705 신설)
- **item**: `snap_dd` · `mkt` · `sector`(버킷코드, 소규모는 `_fold`) · `label`(표시명) · `n`(사수) ·
  `cap`(Σ보통주 시총) · `cap_pref`(Σ우선주 시총) · `per_ttm` · `pbr_mrq`. PK(snap_dd, mkt, sector). ~97버킷/주.
- **분류**: KSIC 하이브리드(`open_proxy_mcp/data/ksic/opm_sector_map.json` — 제품용 보존 자산).
  **WI26은 내부 분석 전용 — 제품/저장 탑재 금지**. MINB(5사) 미만 버킷은 `_fold`로 접음.
- **갱신**: `market_val_weekly.py`(cron). / **검증**: 97버킷 전수 재계산 일치 · Σ버킷=Σ시장 100.0% ·
  음수 배수 0(260705 QA).
- **목적/소비자**: valuation `scope=sector` ("반도체 업종 밸류"·기업 vs 소속 섹터 비교 — `_fold`
  종목은 폴드 버킷과 비교로 폴백).

### `fx_rate` — 환율 영구 캐시
- **item**: `base_ccy` · `dt`(YYYYMMDD) · `rate`(1단위당 KRW). PK(base_ccy, dt). 265행(11통화 ×
  2020~2025 분기말 프리워밍).
- **갱신**: `open_proxy_mcp/dart/fx.py` — 조회 시 자동(과거 확정일만 저장). 소스 ① ECOS 731Y001
  매매기준율(공식, 통화별 item + **JPY·VND는 100단위 divisor**) ② 야후(폴백). 3층: 인메모리→DB→소스.
- **검증**: 11통화 × 24분기말 전수 해석 정상 · ECOS 원본 대조 일치(260704~05).
- **목적/소비자**: 비KRW 기능통화 상장사(USD 12·CNY 9·JPY 1 — 전수 스캔 260704) 재무의 KRW 환산 —
  valuation firm + 주간 스냅샷 배치.

## 2. 밸류에이션 원천 계층 (재무 배치)

### `mkt_fundamentals` — 종목별 최신 FY 재무 (**파생 = mkt_fund_q에서**)
- **item**: `isu_cd` · `corp_code` · `mkt` · `fs`(CFS/OFS) · `ni_fy` · `ni_ttm` · `eq_fy` ·
  `eq_mrq`(지배자본) · `fetched`(ok/nocorp/err) · `induty`(KSIC) · `currency` · `scale_flag`.
  2,653행. **⚠ 비KRW 종목은 원통화 저장** — 소비 시 fx_rate로 환산 필수(weekly 배치가 수행).
- **갱신(260705 전환)**: 재무 4열(`ni_fy`·`ni_ttm`·`eq_fy`·`eq_mrq`)은 **`market_fund_quarterly.py
  --derive`가 mkt_fund_q(SSOT)에서 파생 — DART 0콜**. 최신 공시분기 기준 TTM/MRQ(=분기 공시마다 자동
  최신화). **일일 워크플로(market-val-weekly.yml)의 선행 step**이라 스냅샷 전 항상 fresh. partial-백필
  가드(Q1-3 없는 종목=기존값 유지). ⚠ 구 `market_val_agg.py --fetch`는 done셋+`DO NOTHING`이라 기존행
  **갱신 불가** → 이제 **신규 상장사 onboarding**만 담당(재무 갱신 아님). raw 통화 유지.
- **검증**: derive 파생값 = 기존 mkt_fundamentals 정확 일치(삼성 ni_ttm 83.33조 등, Data-QA 검토).
- **목적/소비자**: 주간 스냅샷 배치(mkt_valuation·mkt_val_history·mkt_sector_val)의 재무 원천.
  ※ valuation firm은 이걸 안 쓰고 실시간 DART(최신성 우선).

### `mkt_fund_q` — 종목별 **분기** 재무 (SSOT · firm/market/sector TTM·MRQ 원천)
- **item**: `isu_cd` · `fy` · `quarter`(1/2/3/4=사업보고서) · `reprt_code` · `fs` · `ni_cum`(지배순이익
  **누적 YTD**) · `eq`(지배자본 **기말잔액**) · `fetched` · `ni_case`/`eq_case`(추출 경우의수 로직트리
  태그). 54,579행(2019~2026 Q1-3 + Q4 seed). **raw 통화**.
- **갱신**: `market_fund_quarterly.py --fetch` — **공시-인지**(_disclosed: 1Q 5/15·반기 8/14·3Q 11/14) +
  resume(done셋) + 동시성 2 + sleep. **월간/공시후 스케줄**(refresh_financials.sh, fly). Q4는 `--seed`
  로 mkt_fund_hist(연간)에서 0콜 복제(bootstrap은 최신FY·hist부재시·DO NOTHING — seed↔derive 순환차단).
- **검증**: 로직트리 전수 케이스 분포(ni 플래그 0 · eq NO_EQUITY_ROW 2=DL 소스파손). TTM 역산 일치.
- **목적/소비자**: `_ttm_ni`(FY(y-1)+누적(y,q)−누적(y-1,q)) · `_mrq_eq` — firm_history 주간곡선 TTM/MRQ ·
  mkt_fundamentals derive · (예정) market/sector 분기 밴드.

### `mkt_fund_hist` — 종목별 과거 FY 재무 (연간 시계열)
- **item**: `isu_cd` · `fy` · `fs` · `ni` · `eq` · `ni_restated`/`eq_restated`(다음 해 보고서 전기비교치로
  재작성 검증 — 소프트센 100만배 오류 자동정정) · `fetched`. ~2,600행/FY.
- **갱신**: `market_val_series.py --fetch` — **YEARS 동적**(`range(2018, _latest_annual_fy()+1)`, 신규
  사업연도 자동 확장), resume `done`이 누락분만, 직렬 0.45s/콜. / **목적**: PIT 시장 밸류 시계열 ·
  firm_history 연말 밴드 · **mkt_fund_q Q4 seed의 authoritative 소스** · `_pit_fy` look-ahead 방지.

## 3. 수정주가 파이프라인 — [[adjusted-price-timeseries]]가 정본, 여기선 요약

| 테이블 | item 요지 | 목적 |
|---|---|---|
| `krx_adj_factor_v3` (3,509) | isu_cd · effective_date · factor(기준가 리셋 실측) · event_type · evidence | **수정주가 계수 정본(v3)** — 주당 가격·EPS 시계열 노출 시 반드시 적용 |
| `krx_base_resets` (3,509) | reset_dd · prev_close · base_price · factor | 거래소 기준가 리셋 실측(계수의 원천) |
| `krx_shares_ledger` (31,809) | chg_dd · prev_shrs · new_shrs | 주식수 변화 전량(교차검증·소각/CB 구분) |
| `krx_stock_flags` (155) | flag(spinoff_break 40 · unresolved_adjustment 115) | **시계열 해석 주의 종목** — valuation firm_history가 대조해 경고 부착 |
| `dart_capital_events` (813) | kind · rcept_no | 리셋의 공시 근거 라벨 |
*(v1·v2·krx_ledger_days·dart_sweep_done — 260705 백업 후 드랍. 코드참조 0건 확인, CSV backup: wiki/handoff/backups_260705/)* | — | — |

**밸류에이션과의 관계**: PER/PBR/시총 시계열은 시총 기반이라 분할·무상증자 등 **조정성 이벤트에
불변**(주가×주식수 상쇄) → 계수 불필요. 유증·소각·인적분할의 시총 점프는 **실제 이벤트**라 보존
(조정 대상 아님, firm_history 경고로 안내). 주당 가격 시계열을 만들게 되면 v3 계수 적용이 필수.

## 4. 운영 계층

| 테이블 | item | 목적 |
|---|---|---|
| `events` | ts_ns · key_hash(익명 유저) · tool · latency_ms · is_error | 사용 통계(scripts/usage_tracker.py) — 233유저/2주, financial_metrics 1,244콜 등 |
| `krx_call_log` | day · machine · calls | KRX 일별 사용량 장부(잔여한도 미제공 → 자체 집계, 두 PC 합산) |

---

## Workflow (갱신 흐름)

```
[매일, 화~토 KST 10:17 — cron market-val-weekly.yml (KRX 4콜, DART 0콜)]
  KRX bydd_trd(2콜) ──▶ krx_weekly (같은 ISO주 수렴 — 주중엔 최신 거래일, 주말 지나면 금요일로 굳음)
  KRX isu_base_info(2콜: 보통주/우선주 구분) ─┐
  krx_weekly(시총) × mkt_fundamentals(재무) × fx_rate(환산) 
      ──▶ mkt_valuation(종목) · mkt_val_history(시장) · mkt_sector_val(섹터)  [같은 주 수렴]

[유저 조회 시 — valuation tool]
  scope=firm:        DART 실시간(재무 ~14콜) × krx_weekly(시세, KRX 0콜) → 심층 배수+경고
                     (조회 자체가 krx_weekly daily-refresh 겸함 — cron과 이중 안전망)
  scope=market:      mkt_val_history 읽기만 (API 0콜)
  scope=sector:      mkt_sector_val (+mkt_valuation 비교) 읽기만
  scope=firm_history: mkt_valuation + krx_stock_flags(경고) 읽기만

[분기 보고 시즌 — 수동]
  market_val_agg.py --fetch ──▶ mkt_fundamentals 재수집 (DART ~8k콜, 동시성 1+sleep, ~60분)

[연 1회 — 수동]
  market_val_series.py --fetch ──▶ mkt_fund_hist 신규 FY 추가 (+재작성 검증)
```

## 검증 이력 (요약)
- **260705 4단계 QA**(각 단계 독립 data QA agent): Step1 krx_weekly 통합(승인, WARN 2 패치) ·
  Step2 주간 스냅샷(승인 — 표본 17+비KRW 22 전수+97버킷 재현 일치, 권장 4 반영) · Step3 scope
  라우팅(조건부 승인 → 전건 해소: _fold 폴백·db_error 구분·scope 검증·spinoff 경고).
- 상세 방법론·사건 이력: [[valuation-methodology]] / 수정주가: [[adjusted-price-timeseries]].

## 갱신 체크리스트 (테이블 추가/변경 시)
1. 이 문서에 item·갱신 주체·주기·검증·목적 기재.
2. INSERT는 컬럼명 명시. 주간류는 ISO주 수렴 규칙 적용 검토.
3. 비KRW 재무를 소비하면 fx_rate 환산 필수(원통화 저장 규약).
4. data QA agent 감수 후 반영(260705 프로세스).
