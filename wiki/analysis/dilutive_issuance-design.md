---
type: analysis
title: dilutive_issuance data tool 설계 + 전수조사
tags: [data-tool, dilutive, capital-raise, convertible-bond, warrant-bond, dart]
related: [OpenProxy-MCP, 유상증자결정, 전환사채발행결정, 신주인수권부사채발행결정, 감자결정]
date: 2026-04-21
---

# dilutive_issuance 설계

희석성 증권 발행 4종을 단일 tool에 통합. 13 → 14번째 tool, Data Tools 9개째. [[corporate_restructuring]]와 동일 패턴.

## 동기

기존 주주 지분 희석에 영향을 주는 증권 발행은 4종 주요사항보고서로 분산:
- 유상증자 (새 주식 직접 발행)
- 전환사채 CB (잠재 희석)
- 신주인수권부사채 BW (잠재 희석)
- 감자 (주식수 감소, 반대 방향이지만 자본구조 변경)

행동주의 대응·경영권 방어·우호지분 형성 분석은 이 4종을 함께 봐야 성립. 따라서 단일 tool로 통합.

## DART API 매핑

| 종류 | endpoint | DS005 apiId |
|------|----------|-------------|
| 유상증자 | `piicDecsn.json` | - |
| 전환사채 | `cvbdIsDecsn.json` | - |
| 신주인수권부사채 | `bdwtIsDecsn.json` | - |
| 감자 | `crDecsn.json` | - |

모두 DS005 주요사항보고서의 구조화 API. [[corporate_restructuring]]와 같은 레이어.

## scope

| scope | 내용 |
|-------|------|
| `summary` | 4종 병렬 호출 → timeline 통합 표 |
| `rights_offering` | 유상증자 카드 (배정방식/희석률/자금목적/보호예수) |
| `convertible_bond` | CB 카드 (전환가/잠재 희석률/refixing/풋옵션 풀세트) |
| `warrant_bond` | BW 카드 (행사가/분리·비분리/대용납입/잠재 희석) |
| `capital_reduction` | 감자 카드 (비율/사유/자본금 변화/일정) |

## 핵심 지표

- **`dilution_pct_approx`** (유상증자): `신주 / 기존 × 100` — 단순 비율. 원본 공시에 없어서 계산.
- **`pct_of_total_shares`** (CB/BW): DART 제공 필드. 발행주식 총수 대비 전환·행사 시 발행될 신주 비율.
- **`refixing_floor`**: 시가 하락 시 전환가 하한. 하한이 낮을수록 희석 위험 ↑.

## 전수조사 결과 (2026-04-21)

| 회사 | scope | 결과 | 비고 |
|------|-------|------|------|
| EDGC | summary | exact, 7건 | 회생 기업, 유상증자 4 + CB 1 + BW 1 + 감자 1 |
| 하이퍼코퍼레이션 | convertible_bond | exact, 4건 | CB 잠재 희석 44.69% (심각) |
| 나무기술 | warrant_bond | exact, 2건 | BW 비분리, 대용납입, 희석 2.87% |
| EDGC | rights_offering | exact, 4건 | 제3자배정 희석 272% |
| EDGC | capital_reduction | exact, 1건 | 83.33% 감자 (회생계획) |
| 삼성전자 | summary | partial | 사건 없음 (정상) |
| 두나무 | summary | error | 비상장 (정상) |

**5/5 통과** (사용 가능한 케이스 기준)

## 거버넌스 분석 활용

- **행동주의 대응**: 경영권 위협 시 CB/BW 사모 발행 → 우호 인수자에게 잠재 지분 부여
- **3자배정 유상증자**: 기존주주 희석 + 최대주주 변경 가능
- **Refixing CB**: 주가 하락 시 전환가 자동 하향 → 무한 희석 위험
- **감자 + 유상증자 세트**: 자본잠식 해소 → 3자배정 → 최대주주 변경 (EDGC 패턴)

## 구현 메모

- API 응답 `-`, `해당사항 없음` → 빈 문자열 정규화
- 긴 텍스트 필드 (`mg_rt_bs`, `ex_prc_dmth` 등) 200자 제한
- lookback 기본 24개월 (M&A와 동일, 드물어서 길게)

## next action

- screen_events에 관련 event_type 추가 (`rights_offering_decision`, `convertible_bond_decision` 등)
- 원문 파싱으로 제3자배정 대상자 명세 추출 (후속)
