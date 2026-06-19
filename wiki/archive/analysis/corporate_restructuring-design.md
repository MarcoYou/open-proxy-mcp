---
type: analysis
title: corporate_restructuring data tool 설계 + 전수조사
tags: [data-tool, restructuring, merger, split, share-exchange, dart]
related: [OpenProxy-MCP, 회사합병결정, 회사분할결정, 주식교환·이전결정, MCP-개발-lessons-learned]
date: 2026-04-21
---

# corporate_restructuring 설계

지배구조 재편 4종(합병/분할/분할합병/주식교환·이전) 결정을 통합 제공하는 data tool. 12 → 13번째 tool, Data Tools 8개째.

## 동기

지배구조 재편은 분산된 4개 DART 주요사항보고서로 표시된다:
- 회사합병결정
- 회사분할결정
- 회사분할합병결정
- 주식교환·이전결정

각각 별도 API지만 분석 관점은 동일 — "비율, 상대방, 일정, 매수청구권, 외부평가". 이걸 4개 tool로 쪼개면 AI 선택 정확도 떨어지고(MCP lesson #1), 사용자도 4번 호출해야 함. 하나의 tool에 4 scope로 통합.

## 설계 원칙

- **scope 기반 4분류**: `summary`(timeline) / `merger` / `split` / `share_exchange`
- **병렬 fetch**: scope=summary 시 4개 API를 asyncio.gather로 한 번에 호출
- **구조화 우선**: DART 공식 API의 정형 응답을 그대로 활용. 본문 파싱 없음
- **기본 lookback 24개월**: M&A는 빈도 낮아 월/분기 단위로는 비어 있음

## DART API 매핑

| 종류 | endpoint | DS005 apiId |
|------|----------|-------------|
| 회사합병 | `cmpMgDecsn.json` | 2020050 |
| 회사분할 | `cmpDvDecsn.json` | 2020051 |
| 회사분할합병 | `cmpDvmgDecsn.json` | 2020052 |
| 주식교환·이전 | `stkExtrDecsn.json` | 2020053 |

명명 패턴: `cmp` (company) + `Mg/Dv/Dvmg` + `Decsn` (decision). 주식교환만 별도 패턴 `stkExtr`.

## scope 별 출력

### summary
4개 API 병렬 호출 → 사건 timeline 통합 표

| 컬럼 | 내용 |
|------|------|
| 날짜 | rcept_dt |
| 종류 | 회사합병결정 / 회사분할결정 / 주식교환·이전결정 |
| 상대방·신설 | counterparty.name / target_company.name / new_company.name |
| 비율 | mg_rt / dv_rt / extr_rt |
| 원문 | DART 뷰어 링크 |

### merger
- `mg_rt`, `mg_stn`, `mg_mth`, `mg_pp`
- `mgptncmp_*`: 상대방 정보 (이름/사업/관계/재무)
- `exevl_intn`, `exevl_op`: 외부평가
- `aprskh_plnprc`: 매수청구권 가격
- `popt_ctr_*`: 풋옵션 약정

### split
`cmpDvDecsn` + `cmpDvmgDecsn` 합쳐서 표시
- `ex_sm_r`: 분할형태 (단순물적 / 인적 등)
- `dv_trfbsnprt_cn`: 분할대상 사업
- `atdv_excmp_*`: 존속회사 (재상장 유지 여부 포함)
- `dvfcmp_*`: 신설회사 (예상매출, 재상장 여부)

### share_exchange
- `extr_sen`: 주식교환 / 주식이전
- `extr_rt`, `extr_rt_bs`: 비율과 근거
- `extr_tgcmp_*`: 대상회사 (관계, 발행주식, 재무)
- `extrsc_*`: 일정 (교환계약 → 주총 → 교환일)

## 전수조사 결과 (2026-04-21)

| 회사 | scope | 결과 | 비고 |
|------|-------|------|------|
| 온코크로스 | summary | exact, 합병 1건 | (주)온코마스터 흡수합병 |
| 일동제약 | merger | exact, 1건 | 유노비아 합병 |
| 감성코퍼레이션 | split | exact, 1건 | 단순물적분할 → 엑티몬 신설 |
| 이마트 | share_exchange | exact, 2건 | 신세계건설/신세계푸드 100% 자회사 편입 |
| 신세계푸드 | summary | exact, 1건 | 이마트 모회사 편입 |
| 두나무 | share_exchange | error | 비상장사, company resolution 실패 (정상) |
| 삼성전자 | summary | partial | 사건 없음 (정상) |

**5/5 통과** (사용 가능한 케이스 기준)

## 구현 메모

- API 응답에서 `-`, `해당사항없음`은 `_clean()`이 빈 문자열로 정규화
- 긴 텍스트 필드는 `_truncate()`로 200자 제한 (`mg_rt_bs`, `mg_pp`, `extr_pp` 등)
- 단일 회사가 같은 scope에서 여러 사건 가질 수 있음 (이마트 share_exchange = 2건)
- evidence_refs는 최근 5건까지만 노출

## 거버넌스 분석에서의 의의

- **물적분할 후 재상장 패턴**: LG화학 → LG에너지솔루션 사례에서 본 "기존 주주 가치 희석" 이슈를 즉시 탐지 가능 (`dvfcmp_rlst_atn=예`)
- **주식매수청구권 가격**: 반대주주 보호 장치의 합리성 평가
- **외부평가 의견**: 가격 산정의 독립성 / 합리성
- **상대방 관계**: `mgptncmp_rl_cmpn` / `extr_tgcmp_rl_cmpn`이 "자회사"이면 내부거래 성격 강함

## next action

- screen_events에 `merger_decision`, `split_decision`, `share_exchange_decision` event_type 추가 (현재는 ownership/treasury 등만 있음)
- 다음 data tool: `related_party_transaction` (일감몰아주기·내부거래)
