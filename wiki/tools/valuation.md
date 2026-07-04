---
type: tool
title: valuation
domain: data
status: 등록 완료 (260705 — tools_v2/valuation.py, v2 toolset 18번째)
scope: [lean-v1 상대가치 배수]
data_source: [DART financial_metrics 4EP(요약), DART company.json(업종·결산월), DART fnlttSinglAcntAll(재무원장·통화), DART stockTotqySttus(유통주식수), DART alotMatter(배당), KRX stk/ksq_bydd_trd(시세·시총), ECOS 731Y001(환율)]
related_disclosures: [사업보고서, 분기보고서]
related_concepts: [배당수익률, 당기순이익, ROE]
related_decisions: [valuation-methodology]
created: 2026-07-05
---

# valuation

## 한 줄 요약
DART(공시) + KRX(공식시세) 기반 **상대가치 배수** — PER(FY0·TTM) · PBR(MRQ) · 배당수익률. 지배주주
귀속 기준, 비KRW 기능통화 자동 환산(ECOS), 실시간 스케일가드 + N/M 게이팅 + 식별 status 4단.
설계·검증 근거 = [[valuation-methodology]].

## 사용법
```
valuation(company="두산밥캣")
```
자연어 예시:
- "삼성전자 밸류에이션" → PER 46.9(FY0)/21.6(TTM) · PBR 4.33 · 배당수익률 0.54%
- "두산밥캣 PBR" → USD 재무 자동 KRW 환산(ECOS 1,434.9) → PBR 0.86
- "카카오뱅크 배수" → 금융업 판별 → PBR·PER·배당·ROE 중심(EV/EBITDA·PSR·FCF N/A)

## 입력 인자
| 인자 | 타입 | 필수 | 설명 | 기본값 |
|---|---|---|---|---|
| company | str | yes | 회사명 / ticker(6자리) / corp_code | - |
| format | str | no | "md" / "json" | "md" |

## 데이터 계보 (소스 → 아이템 → 연산) — 핵심

| 단계 | 소스 (호출) | 뽑는 아이템 | 쓰임 |
|---|---|---|---|
| 식별 | `lookup_corp_code` → DART corp master(master.db 캐시) | corp_code · stock_code · 상장여부 | 진입 게이트(비상장/우선주/빈입력 차단) |
| 재무요약 | `build_financial_metrics_payload(stock_code)` → DART **fnlttSinglAcnt·AcntAll·Indx·감사의견** | eps_krw · revenue_krw · roe_pct · capital_impairment_status · fiscal year | EPS(FY0)·ROE·매출·자본잠식 상태 |
| 업종/결산월 | `get_company_info` → DART **company.json** | induty_code(KSIC) · acc_mt(결산월) | 금융 판별(64/65/66) · FX 기준일 |
| 재무원장 | `get_fnltt_singl_acnt_all` ×3 (연간 11011 + 1Q당해 11013 + 1Q전년 11013) | `_ctrl_ni`(지배순이익) · `_ctrl_equity`(지배자본) · `_gid`(Assets/Liab/Equity, **exact-match**) | TTM 순이익 · BPS · 스케일 항등식 |
| 통화 | `statement_currency`(currency 필드) + `fx_to_krw` → **ECOS 731Y001**(→야후 폴백) + Supabase `fx_rate` 캐시 | 기능통화 · 기말환율 | 비KRW → 재무 KRW 환산 |
| 주식수 | `get_stock_total` → DART **stockTotqySttus** | distb_stock_co(합계/보통주, 자기주식 제외) | EPS 분모(보통주)·BPS 분모(합계) |
| 시세 | **Supabase `krx_daily`(일별 누적)** 우선 → 미스·스냅샷 확보 시만 라이브 KRX stk/ksq_bydd_trd | close(종가) · mktcap · list_shrs | 배수 분자(주가)·시총 노출·주식수 검증 |
| 배당 | `_annual_summary` → DART **alotMatter** | cash_dps(주당현금배당, 이미 주당값) | 배당수익률 |

## 연산 파이프라인
```
TTM 순이익   = ni_fy(연간) + ni_qc(1Q당해) − ni_qp(1Q전년)      # 지배순이익 기준
EPS(FY0)     = financial_metrics 공시 기본주당이익            # 없으면 지배순이익÷보통주 폴백
EPS(TTM)     = TTM 순이익 ÷ shares_common(보통주)
BPS          = 지배자본(MRQ 우선, 없으면 FY0) ÷ shares_total(합계)
PER(FY0/TTM) = 주가 ÷ EPS(FY0/TTM)
PBR(MRQ)     = 주가 ÷ BPS
배당수익률    = DPS ÷ 주가 × 100
```
- **지배주주 귀속 일관**: EPS·BPS·PER·PBR 모두 지배지분(`_ctrl_*`). 지주사(NCI 큰) 과대 방지.
  단 **스케일 항등식은 총자본**(지배+비지배, `_gid` Equity) — 지배자본만 쓰면 NCI만큼 상시 오탐.
- **⚠ EPS 방법론 비대칭**: EPS(FY0)=공시 기본주당이익(가중평균 주식수·우선주 배분 반영),
  EPS(TTM)=지배순이익÷보통주(시점) → 분모 기준이 달라 **FY0·PER과 TTM·PER 직접 비교는 주의**
  (예: 순이익 YoY 감소해도 EPS_ttm>EPS_fy0 역전 가능). 방법론 통일은 v1.1.

## 가드 4종
1. **식별 status**(진입부): `invalid`(빈입력) · `not_found`(미존재·우선주 — 마스터는 보통주 코드만) ·
   `unlisted`(비상장, 주가 없어 배수 불가 + 상장 후보 안내) · `no_financials`(재무 미확정). 크래시·오매핑 0.
2. **N/M 게이팅**: 분모(EPS·BPS)≤0 또는 **완전자본잠식**(cap_status=full)이면 해당 배수 = None(N/M).
   적자를 숫자 배수로 내보내지 않음.
3. **스케일가드**([[valuation-methodology]] §9, `services/scale_guard.py`): hard=②항등식(자산=부채+자본)·
   ③시장최댓값배수 / soft=①배수점프·④시총비율. **개별조회는 마스킹 안 함 — 값 유지 + 강한 경고**.
   (시장 aggregate는 반대로 무효화 — 소비 맥락이 다르므로.)
4. **통화**: 비KRW면 회계기말 환율로 순이익·자본·자산·부채 환산 후 배수 산출 + 환산 경고.

## 출력 schema (data dict)
```json
{
  "company_id": "cmp_241560",
  "identifiers": {"ticker": "241560", "corp_code": "01032486"},
  "sector_class": "general",           // general | financial
  "fiscal_year": 2025,
  "price_krw": 64400, "price_date": "20260703",
  "multiples": {
    "per_fy0": 15.19, "per_ttm": 14.58,
    "pbr_mrq": 0.86, "pbr_basis": "MRQ",
    "dividend_yield_pct": 2.64
  },
  "inputs": {
    "eps_fy0_krw": 4241, "eps_ttm_krw": 4416, "bps_krw": 74931, "roe_pct": 5.83,
    "net_income_fy0_krw": 405931775100, "net_income_ttm_krw": 422682797700,
    "controlling_equity_krw": 7171963096800,
    "shares_common": 95713802, "shares_total": 95713802,
    "dps_krw": 1700, "revenue_fy0_krw": 8870273429400,
    "common_market_cap_krw": 6173130586000,
    "capital_impairment_status": "normal",
    "functional_currency": "USD", "fx_rate_to_krw": 1434.9
  },
  "warnings": ["기능통화 USD — 재무를 …환율 1,434.9원/USD로 KRW 환산…", "주가 기준일 …"],
  "data_quality": {"scale_tier": "clean", "scale_flags": [], "values_masked": false},
  "note": "lean v1 — RIM·EV/EBITDA·PSR·FCF·5년밴드·PIT는 v1.1. EPS(FY0)=공시 기본주당이익…"
}
```
- **단위**: 모든 `_krw` 필드 = 원 raw int. `_pct` = float(2.64 = 2.64%). 비KRW사는 환산 후 KRW.
- status 필드: `ok` / `invalid` / `not_found` / `unlisted` / `no_financials`.

## DART/KRX 콜 budget (per-firm)
| 소스 | 콜 | 비고 |
|---|---|---|
| DART financial_metrics(summary) | ~7 | 4 endpoint + TTM/폴백 |
| DART company.json | 1 | 업종·결산월 |
| DART fnlttSinglAcntAll ×3 | 3 (~6) | 연간+1Q당해+1Q전년, CFS→OFS 폴백 시 최대 2배 |
| DART stockTotqySttus | 1 | 유통주식수 |
| DART alotMatter(배당) | ~2 | 연간 요약 |
| **DART 합계** | **~14 (최대 ~18)** | per-firm |
| KRX (시세) | **serve-time 0** | Supabase `krx_daily`에서 읽음. 라이브 KRX는 하루 1회 최신 거래일 스냅샷 확보 시만(전종목 2콜, 코스피·코스닥 병렬) → **유저 수 무관 하루 ~2콜**. KRX 개인키 일 10,000 한도 보호 |
| ECOS 환율 | 0~1 | 비KRW사만, 분기말 캐시 히트 시 0 |

**KRX 시세 = Supabase krx_daily 서빙(260705)**: KRX Open API는 개인키 1개·일 10,000콜 한도(배치와
공유)라 [N]명 유저를 라이브로 서빙하면 키 소진 시 배수 N/M 위험. FX 캐시와 동형 — 매일 최신 거래일
전종목 스냅샷을 라이브로 확보해 krx_daily에 누적(확정 거래일 keep), valuation은 DB 우선 읽기. 서빙
KRX 콜 = 하루 ~2(첫 조회). 축적 일별 가격은 v1.1 5년밴드·PIT 시계열에 재사용. price_date로 기준일 투명.

**fetch 병렬화(260705)**: 최상위 await를 의존성 3단계 gather로 — P1(financial_metrics·company·KRX,
fy 무관) → P2(연간원장·주식수·배당, fy 의존) → P3(1Q당해·전년, fs_used 의존). info·market이 무거운
financial_metrics 뒤에서 대기하던 것 제거. 실측 개별 조회 ~6.3s→~2.2s, 8종목 배치 50s→20s(회귀 클린).

→ [[tool_call_budget]] 갱신 필요(등록 시). financial_metrics를 감싸므로 콜 수가 큰 편.

## Flow
```mermaid
sequenceDiagram
    participant U as User
    participant V as valuation
    participant R as lookup_corp_code
    participant FM as financial_metrics(4EP)
    participant AA as fnlttSinglAcntAll×3
    participant FX as fx_to_krw(ECOS/캐시)
    participant SH as stockTotqySttus
    participant KX as KRX bydd_trd
    U->>V: company="두산밥캣"
    V->>R: 식별 → corp_code·stock_code (비상장/우선주 게이트)
    V->>FM: 요약(eps·roe·revenue·cap_status·fy)
    V->>V: get_company_info (induty·acc_mt) → 금융판별
    V->>AA: 지배순이익·지배자본·Assets/Liab/Equity
    V->>FX: 통화≠KRW면 기말환율 환산
    V->>SH: 유통주식수(보통/합계)
    V->>KX: 종가·시총
    V->>V: TTM·EPS·BPS·PER·PBR·배당수익률 + 스케일가드 + N/M 게이팅
    V-->>U: multiples + inputs(근거 투명) + warnings + data_quality
```

## 검증 (260705, 등록 전)
7-에이전트 다각 검증(대형제조·금융·통화환산·지주NCI·부실스케일·엣지식별·독립산식감사) + 웹검증.
**견고성 blocker 0** — 18개 배수 독립 재계산 전부 일치, 두 자본 구분 SK(NCI 71%) 정확, 통화 ECOS
정합, 완전자본잠식(이오플로우) N/M 정확, 크래시·오매핑 0. 상세 = [[valuation-methodology]] §"등록 전
7-에이전트 검증".

## 알려진 issue + v1.1
- **EPS FY0/TTM 방법론 비대칭**(문서화 완료, 통일은 v1.1).
- **shares_bad**(유통주식수 파싱오류) 시 PBR·EPS_ttm은 무효화되나 eps_fy0(공시값)는 유지 — 흑자
  종목이면 EPS 과소·PER 과대 누수 가능(v1.1: eps_fy0도 차단).
- **unlisted 상장후보 안내**가 리졸버 exact-match 단락으로 비는 경우(예 "삼성") — 부분매치 별도조회(v1.1).
- 리츠 전용 섹터 처리 없음 / 데이터부재(상폐) 종목이 금융으로 오분류(경미).
- v1.1 백로그: RIM·EV/EBITDA·PSR·FCF·peer 랭킹·자기 5년 밴드·PIT 시계열 · FX 평균환율(flow) ·
  한국은행 ECOS를 야후 폴백 대신 정본 유지 · 우선주 총시총 합산.

## 관련
- [[valuation-methodology]] — 설계·스케일가드·FX·검증 전체 근거(decisions/)
- [[financial_metrics]] — 재무 펀더멘탈(이 tool이 요약을 재사용). valuation=시장배수, financial_metrics=펀더멘탈
- [[배당수익률]] — DPS ÷ 주가
- [[environment-secrets]] — ECOS_API_KEY·KRX_OPEN_API_KEY 등 필요 키
