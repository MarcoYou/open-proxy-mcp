---
type: architecture
title: PER·PBR 계산용 유효 데이터 포인트 전수조사 (보통주·우선주, 실측 260705)
updated: 2026-07-05
---

# PER·PBR 데이터 포인트 인벤토리 — 전수조사 (실측)

> **목적**: PER·PBR 계산에 사용 가능한 모든 유효 데이터 포인트를 소스·필드 단위로 실측 목록화 —
> EPS FY0/TTM 분모 비대칭의 근본 해결과 우선주 처리 설계의 단일 근거 문서.
> **실측 조건**: 에이전트 2명 병렬(DART 17콜·KRX 3콜·Supabase SELECT), 표본 = 현대차(우선주
> 3종)·삼성전자(우선주 1종)·두산밥캣(우선주 無·USD). [[valuation-methodology]]와 상호 참조.

## 결론 요약 (설계 판정)

| # | 질문 | 답 (실측) |
|---|---|---|
| 1 | 가중평균유통주식수 직접 취득? | **NO** — 전 재무 endpoint 0건. 역산(NI÷EPS)은 우선주 無 회사만 정확, 클래스별 EPS 회사(현대차)는 미지수 2·식 1로 복원 불가 |
| 2 | FY0·TTM EPS를 **같은 기준**으로? | **YES — 공시 EPS끼리 조립**: 분기 IS의 `thstrm_add_amount`(누적 EPS)·`frmtrm_add_amount`(전년동기 누적 EPS)가 실존 → **TTM EPS = FY0 EPS + Q누적 EPS − 전년동기누적 EPS** (전부 공시 가중평균·우선주 배분 기준 = 비대칭 근본 해소) |
| 3 | 분기(MRQ) 시점 주식수? | DART stockTotqySttus **불가**(11013 응답은 오나 전 필드 `-`). **KRX `LIST_SHRS`(일별·종별)** + `krx_shares_ledger`(변동 원장)가 유일 경로. 단 LIST_SHRS는 자기주식 **포함** |
| 4 | 우선주 종류별(1우·2우B·3우B) 데이터? | **시세·시총·주식수 = KRX에서 종별 전부 가능**(각각 행). **DART는 합산 1행**(stockTotqySttus `우선주`, alotMatter `stock_knd=우선주`). 종별 DPS는 배당결정 수시공시 필요 |
| 5 | 우선주 유형 식별? | suffix(5/7/9/K/L)로는 불가 — **`KIND_STKCERT_TP_NM` 4값**(보통주/구형우선주/신형우선주/**종류주권**)으로만. 260705 QA "미분류 12건" = `종류주권` 미처리가 원인 |
| 6 | PBR 분자 | `ifrs-full_EquityAttributableToOwnersOfParent` (분기 `thstrm_amount`로 MRQ 갱신). **신종자본증권 혼입은 BS에서 분리 불가**(SCE/CF 흐름 행으로 발행 힌트만) |
| 7 | 쓸모없는 endpoint | fnlttSinglAcnt(EPS·지배NI 없음, 콤마 문자열)·fnlttSinglIndx(주당 지표 전무) — PER/PBR 파이프라인 제외 |

## 1. fnlttSinglAcntAll — PER·PBR의 유일한 실전 재무 소스

### EPS 계정 (sj_div=IS 하단)
| account_id | 내용 | 실측 예 | 주의 |
|---|---|---|---|
| `ifrs-full_BasicEarningsLossPerShare` | **보통주 기본 EPS (표준)** | 현대차 36,088 · 삼전 6,605 · 밥캣 "2.95" | 밥캣(USD)은 **소수점 문자열** — int 파싱 금지 |
| `ifrs-full_DilutedEarningsLossPerShare` | 희석 EPS | 현대차 36,088 | v1.1 |
| `ifrs-full_BasicEarningsLossPerShareFromContinuingOperations` / `...Discontinued...` | 계속/중단영업 분리 EPS | 현대차 FY24 48,829 / -1,214 | 중단영업 있는 해의 정규화 PER 재료 |
| `dart_BasicEarningsLossPerSharePreferredStock` (+Diluted·Continuing 변형) | **우선주 EPS** | 현대차 "1우선주" 37,851 | **"1우선주" 1행만**(2우B·3우B 없음). 삼전은 이 행 자체가 없음(단일 EPS가 보·우 합산 가중평균) — 회사 공시 스타일 의존 |

- 삼전형(우선주 있는데 단일 EPS): 역산 분모 = 보통+우선 **합산** 가중평균(44.261조÷6,605≈6.70B 실측 정합).
- 현대차형(클래스별 EPS): NI÷EPS ≠ 어느 클래스의 가중평균도 아님 — 역산 금지.

### 순이익(PER 분자)·자본(PBR 분자)
| account_id | 실측(현대차 FY25 CFS) | 역할 |
|---|---|---|
| `ifrs-full_ProfitLossAttributableToOwnersOfParent` (IS) | 9,445,987백만 | **PER 분자 표준** (없으면 `ProfitLoss` 폴백 — 밥캣은 NCI 행 자체가 없음) |
| `ifrs-full_ProfitLoss` | 10,364,775백만 | IS·CIS **중복 행** — dedup 필요 |
| `ifrs-full_EquityAttributableToOwnersOfParent` (BS) | 115,446,507백만 | **PBR 분자 표준** |
| `ifrs-full_Equity` / `NoncontrollingInterests` | 127,648,237 / 12,201,730백만 | 검산·스케일 항등식(총자본) |
| `dart_IssuedCapitalOfCommonStock` / `...PreferredStock` | 삼전 778,047 / 119,467백만 | 우선주 자본 분리는 **액면 기준**뿐, 그마저 **삼전만 공시** |
| `dart_ElementsOfOtherStockholdersEquity` | 현대차 -374,595백만 | **자기주식이 BS 별도 행 없이 여기 흡수**(Treasury 행 0건) |

- **신종자본증권(영구채)**: BS 별도 행 없음 — 지배자본에 섞임. `dart_IssueOfHybridBond`(SCE)·CF 행으로 발행 여부 힌트만. 발행사 PBR 분자 왜곡은 이 API로 차단 불가(주석 필요).
- **주당배당·액면가**: 재무제표에 **없음**(배당 총액만 SCE/CF — 같은 account_id 3~8회 중복이라 합산 금지, `account_detail` 구분). 소스는 alotMatter.

### 필드 메타 (기간 조립 규칙 — TTM의 핵심)
| reprt | 유효 필드 | 의미 |
|---|---|---|
| 연간 11011 | `thstrm/frmtrm/bfefrmtrm_amount` | **1콜 3개년** (재작성 검증·추이) |
| 분기 11013 IS | `thstrm_amount`(3개월)·**`thstrm_add_amount`(누적)**·`frmtrm_q_amount`(전년동기 3개월)·**`frmtrm_add_amount`(전년동기 누적)** | `bfefrmtrm/frmtrm_amount` 키 자체 없음. **EPS도 누적으로 옴**(현대차 Q1 보통 8,897·1우 8,908) → TTM EPS 조립 가능 |
| 분기 11013 BS | `thstrm_amount`(분기말)·`frmtrm_amount`(**직전 기말**, 전년동기말 아님) | MRQ 자본 |
| 공통 | `currency`·`ord`·`account_detail`·`rcept_no` | 금액 전부 문자열(콤마 없음) |

## 2. 주식수 소스

### DART stockTotqySttus (사업보고서 11011만 유효)
| 필드 | 의미 | 실측(현대차 보통주) | 역할/주의 |
|---|---|---|---|
| `se` | 구분 — **보통주/우선주/합계/비고 4종뿐**(종별 분리 없음) | 우선주 1행 = 3종 합산 60,632,342 (KRX 합과 정확 일치) | `비고` 행은 숫자 필드에 **텍스트** — 제외 필수 |
| `istc_totqy` | **발행주식 총수(현재)** | 204,757,766 | FY말 기준 분모 |
| `tesstk_co` | 자기주식수 | 1,648,558 | 유통 기준 산출 |
| `distb_stock_co` | **유통주식수**(발행−자기) | 203,109,208 | 현행 EPS(TTM)·BPS 분모 |
| `isu_stock_totqy` | 수권주식수(발행 아님!) | 450,000,000 | 이름 함정 — 사용 금지 |
| `now_to_isu/dcrs_stock_totqy`·`redc`·`profit_incnr`·`etc` | 누적 발행/감소·감자·이익소각·기타 | — | 전환우선주 소멸이 `etc`에 기록 |
| **분기(11013)** | 응답은 오나 **전 필드 `-`** | — | **MRQ 주식수 불가** — KRX로 |

### KRX LIST_SHRS + krx_shares_ledger
- `LIST_SHRS`(bydd_trd·base_info): **일별·종별** 상장주식수. **자기주식 포함**, 소각은 즉시 반영(삼전 실측: 2026 소각 73.36M이 DART FY말보다 먼저 차감) — **DART FY말과 시점 어긋남 주의**.
- `krx_shares_ledger`(Supabase, 31,809행): 종별 주식수 변동 원장(현대차 4종 20250521 소각 기록 실측) — 시점 보정·이벤트 추적.

## 3. KRX 시세 (종별 전부 가능)
- `bydd_trd` 15필드: **`TDD_CLSPRC`(종가)·`MKTCAP`·`LIST_SHRS`**가 종목(종류)별 각각 행 — 현대차 005380 100.7조 / 005385 4.99조 / 005387 7.48조 / 005389 0.48조.
- `isu_base_info` 12필드: `ISU_CD`(**ISIN**)·`ISU_SRT_CD`·`PARVAL`(액면가)·`LIST_DD`·**`KIND_STKCERT_TP_NM`**.

### 우선주 식별 체계 (실측 규칙)
| suffix | 관행 | 실례 | 함정 |
|---|---|---|---|
| `0` | 보통주 (첫5자리+0 매핑) | 005380 | |
| `5`/`7`/`9` | 1우/2우/3우 | 005385·005387·**005389**(3우B — K 아님) | `5`에도 신형 3건 |
| `K`/`L` | 번호 소진·재편 신규 | 03473K SK우·37550L | K에 구형·신형·**종류주권** 혼재 |

**분류는 반드시 `KIND_STKCERT_TP_NM` 4값으로**: 보통주(2,653) / 구형우선주(78) / 신형우선주(23) / **종류주권(12)** ← QA 미분류 12건의 정체. suffix는 "우선주 여부 + 보통주 매핑"까지만 신뢰.

## 4. 배당 (alotMatter) — 주당·수익률의 유일 소스
| se | stock_knd | 실측(현대차 2025) | 역할/주의 |
|---|---|---|---|
| (연결)주당순이익(원) | - | **36,088** | **회사 공식 EPS ground truth** (fnltt EPS와 일치 검증됨) |
| 주당 현금배당금(원) | **보통주/우선주** | 10,000 / **10,100** | 우선주 +100(액면 2% 가산, **기말분만** — 분기 중간배당은 동일액 실측). 종별(2우B 등) 구분 없음 |
| 현금배당수익률(%) | 보통주/우선주 | 1.90 / 3.50 | 결의 당시 기준(과거) |
| 주당액면가액(원) | - | 5,000 | PARVAL 교차검증 |
| (연결)당기순이익·배당총액·배당성향 | - | 9,445,987백만 · 2,618,263백만 · 27.70% | 백만원 단위 |
- 3개년(`thstrm/frmtrm/lwfr`) 동봉. **분기·반기(11012~14)도 옴 — 누적(YTD) 기준**(Q1 2,500 → 반기 5,000 → 3Q 7,500 → 연간 10,000+100).

## 5. Supabase 기보유 자산 (API 0콜)
| 테이블 | PER/PBR 포인트 |
|---|---|
| `krx_weekly` (132만행·548주·2015-12~) | **우선주 종별 포함** 주간 close/mktcap/list_shrs 10.5년 |
| `mkt_fundamentals` (2,653) | ni_fy/ni_ttm/eq_fy/**eq_mrq**·currency — MRQ 자본은 이미 보유 |
| `krx_shares_ledger` (31,809) | 종별 주식수 변동 원장 |
| `fx_rate` (265) | 비KRW 환산(기말환율) |

## 6. 권장 계산 경로 (보통주 통일 설계 — 이 조사의 귀결)

**EPS: "공시 EPS 조립" 경로가 정답** — 주식수를 직접 만들지 않는다:
```
EPS(FY0) = 공시 BasicEarningsLossPerShare (연간 11011 thstrm)
EPS(TTM) = FY0 EPS + Q누적 EPS(thstrm_add_amount) − 전년동기누적 EPS(frmtrm_add_amount)
```
→ 양쪽 다 **공시 가중평균·우선주 배분 기준** = FY0/TTM 비대칭(현대차 29% 괴리·방향 왜곡) 근본 해소.
가중평균주식수가 안 오므로(결론 1) 이것이 유일한 대칭화 경로. EPS 결측 시에만 지배NI÷보통주 폴백(비대칭 경고 유지).

**BPS(보통주 기준)**: 지배자본(MRQ, 분기 BS thstrm) ÷ 주식수. 주식수 선택지 — ① DART FY말 유통(현행, 자기주식 제외·시점 낡음) ② KRX LIST_SHRS(일별·최신, 자기주식 포함). 우선주 자본의 장부가 분리는 불가(액면 분리도 삼전만)이므로 "보통주 BPS"는 근사가 불가피 — 분모를 합계주식수(보·우)로 두는 현행이 일관 근사, 보통주만 쓰려면 우선주 자본 차감 불가 문제를 명시해야 함.

**우선주 PER**: 종별 시세(KRX) × `dart_...PreferredStock` EPS("1우선주"만, 회사 스타일 의존) — 커버리지 한계 명시하면 v1.1 옵션.

## 함정 총정리 (파싱 시 필수 체크)
1. 밥캣(USD) EPS = 소수점 문자열 · 2. `ProfitLoss` IS/CIS 중복 · 3. SCE 배당 행 다중 중복(합산 금지) · 4. stockTotqySttus `비고` 행 텍스트 · 5. `isu_stock_totqy`=수권(발행 아님) · 6. 분기 stockTotqySttus 전 필드 `-` · 7. LIST_SHRS 자기주식 포함+소각 선반영(DART FY말과 어긋남) · 8. KIND 4값(종류주권 누락 금지) · 9. suffix로 우선주 유형 판별 금지 · 10. 신종자본증권 지배자본 혼입 분리 불가 · 11. fnlttSinglAcnt 콤마 문자열+CFS/OFS 동시 반환 · 12. fnlttSinglIndx `idx_val` 키 부재 가능.

## 관련
- [[valuation-methodology]] — 이 인벤토리를 소비하는 설계 결정(EPS 통일·우선주 처리)
- 원본 실측 JSON: scratchpad `fnltt/`·`krx_*.json` (세션 한정)
