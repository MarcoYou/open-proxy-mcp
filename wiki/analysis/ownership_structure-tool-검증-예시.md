---
type: analysis
title: ownership_structure tool 검증 예시
tags: [release-v2, tool, validation, ownership]
date: 2026-04-18
related: [tool-추가-검증-템플릿, tool-추가-검증-정책, own-tool-rule, 대량보유상황보고서]
---

# ownership_structure tool 검증 예시

## 목적

`ownership_structure`는 지분 구조, 5% 보유, 자사주, 지배력 변화를 한 화면에서 보는 도구다.

## 제안 요약

- tool type: `data`
- 핵심 질문:
  - 최대주주와 특수관계인 지분은 얼마인가
  - 5% 보고자의 목적과 변화는 무엇인가
  - 자사주와 자사주 이벤트는 어떤 신호를 주는가
- 권장 scope:
  - `summary`
  - `major_holders`
  - `blocks`
  - `treasury`
  - `control_map`
  - `timeline`

## 소스 정책

| field | disclosure/source | primary source | secondary source | note |
|---|---|---|---|---|
| major holders | 사업보고서 | DART major shareholders API | 없음 | 사업연도 기준 |
| stock total / minority | 사업보고서 | DART stock/minority API | 없음 | 보통주/우선주 구분 |
| treasury balance | 사업보고서 | DART treasury stock API | 없음 | 연말 잔액 |
| treasury events | 주요사항보고 | DART treasury event APIs | 없음 | 취득/처분/신탁 |
| block holders | 5% 대량보유 | DART majorstock API | `document.xml` 목적 파싱 | 보유목적 보강 필요 |
| control map | derived | upstream 조합 | 없음 | release_v2 내부 정규화 필요 |

## 샘플 확인 (2026-04-19 실행, scope=summary)

| company | status | major_holders | blocks | treasury_pct | note |
|---|---|---|---|---|---|
| 삼성전자 | exact | 19 | 1 | 보유 | 표준 케이스, 다수 특수관계인 |
| 고려아연 | exact | 16 | 5 | - | 5% 대량보유 다수 보고자 (MBK, 영풍, Palisade 등) |
| KT&G (엣지) | exact | 1 | 1 | 자사주 이벤트 | 자사주 소각 이력 있는 기업, block은 외국인 |

- 3개 전부 DART 공식 API 경로로 `exact` 판정
- 대량보유 수(blocks)로 분쟁/액티비스트 진입 밀도 비교 가능 (고려아연 5건 > 삼성전자 1건)

## requires_review 조건

- 5% 보유목적이 `불명`으로 남는 경우
- 보고자명 정규화가 불완전해 control map이 찢어지는 경우
- 사업보고서 기준 지분과 최신 5% 공시가 비정상적으로 충돌하는 경우

## release_v2 판정

- `go`
- 이유:
  - 대부분이 공식 DART API 기반이라 소스 안정성이 높다
  - KIND 의존도가 없고 false match 리스크가 낮다

## 실무 해석

이 도구는 `공격/방어`보다 먼저 `현재 판의 구조`를 보여준다.  
특히 `blocks`와 `control_map`은 액티비스트나 스튜어드십 담당자가 가장 자주 되돌아보는 탭이 된다.
