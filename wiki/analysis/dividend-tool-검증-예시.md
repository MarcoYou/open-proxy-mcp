---
type: analysis
title: dividend tool 검증 예시
tags: [release-v2, tool, validation, dividend]
date: 2026-04-18
related: [tool-추가-검증-템플릿, tool-추가-검증-정책, div-tool-rule, 현금배당결정]
---

# dividend tool 검증 예시

## 목적

`dividend`는 배당 수준, 배당결정 공시, 연간 추이를 한 도구에서 다루는 탭이다.

## 제안 요약

- tool type: `data`
- 핵심 질문:
  - 이번 배당결정의 DPS와 기준일은 무엇인가
  - 연간 배당성향과 시가배당률은 어떠한가
  - 특별배당/분기배당 신호가 있었는가
- 권장 scope:
  - `summary`
  - `detail`
  - `history`
  - `policy_signals`
  - `evidence`

## 소스 정책

| field | disclosure/source | primary source | secondary source | note |
|---|---|---|---|---|
| annual payout / yield | `alotMatter` | DART dividend API | price API fallback | DART 공식 값 우선 |
| dividend decision detail | 현금ㆍ현물배당결정 | DART `list.json + document.xml` | KIND whitelist 가능 | 건별 공시 파싱 |
| historical timeline | 거래소 배당공시 | DART search + XML | KIND whitelist 선택적 | 연도별 추이 |
| price fallback | 일별 종가 | KRX Open API | NAVER | DART yield 부재 시만 |

## 샘플 확인 (2026-04-19 실행, scope=summary)

| company | status | cash_dps | payout_ratio_dart | yield_dart | decisions | note |
|---|---|---|---|---|---|---|
| 삼성전자 | exact | 1,668원 | 25.1% | 1.5% | 5건 | 분기 고정 배당, alotMatter + 결정공시 정상 |
| KT&G | exact | 6,000원 | 57.6% | 4.1% | 5건 | 고배당 대표주, 배당성향 매우 높음 |
| 메리츠금융지주 (엣지) | exact | 0원 | null | null | 5건 | DART alotMatter 요약은 비어있으나 결정공시 5건 존재 → 특별배당/자사주 소각 중심 정책이라 일반 배당 요약 공란. scope=`detail`/`policy_signals`로 보완 필요 |

## requires_review 조건

- `alotMatter`와 거래소 공시 수치가 충돌하는 경우
- 특별배당 문구는 있는데 금액 구조가 비정형인 경우
- 시가배당률이 비어 있고 가격 fallback도 실패한 경우

## release_v2 판정

- `go`
- 이유:
  - 연간 요약은 `alotMatter`가 안정적
  - 건별 결정은 DART XML이 기본이고, KIND는 검증된 화이트리스트 안에서만 보강하면 된다

## 실무 해석

이 도구는 단순히 `배당금 얼마`가 아니라 `배당정책 변화`를 읽는 탭이다.  
그래서 연간 요약과 건별 결정 공시를 같이 보여주는 구조가 중요하다.
