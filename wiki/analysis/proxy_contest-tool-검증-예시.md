---
type: analysis
title: proxy_contest tool 검증 예시
tags: [release-v2, tool, validation, proxy-contest]
date: 2026-04-18
related: [tool-추가-검증-템플릿, tool-추가-검증-정책, prx-tool-rule, DART-KIND-매핑-화이트리스트-2026-04]
---

# proxy_contest tool 검증 예시

## 목적

`proxy_contest`는 위임장, 분쟁 소송, 5% 보유변동, 표 대결 신호를 한데 모아 보는 탭이다.

## 제안 요약

- tool type: `data`
- 핵심 질문:
  - 지금 분쟁이 있는가
  - 누가 어떤 문서로 싸우고 있는가
  - 표 대결이나 캠페인 가능성을 보여주는 신호가 있는가
- 권장 scope:
  - `summary`
  - `fight`
  - `litigation`
  - `timeline`
  - `signals`
  - `vote_math`
  - `evidence`

## 소스 정책

| field | disclosure/source | primary source | secondary source | note |
|---|---|---|---|---|
| proxy docs | 위임장권유참고서류 | DART `list.json + document.xml` | 없음 | DART-only |
| direction / detail | 위임장 본문 | DART XML/text | 없음 | 정규식/본문 파싱 |
| litigation | 소송등의 제기/판결 | DART B/I + XML | KIND whitelist 일부 가능 | exchange-style만 제한적 |
| ownership signals | 5% 보유 | DART majorstock API | XML 목적 파싱 | campaign signal |
| vote_math | AGM result | KIND HTML | DART list | AGM result whitelist 필요 |

## 샘플 확인

| company | subdomain | rcept_no | primary result | secondary result | 판정 | note |
|---|---|---|---|---|---|---|
| 고려아연 | proxy | `20260309001811` | DART 위임장 공시 확보 | 없음 | exact | DART-only |
| 한진칼 | proxy | `20260225005188` | DART 위임장 공시 확보 | 없음 | exact | DART-only |
| 영풍 | proxy | `20260312001045` | DART 위임장 공시 확보 | 없음 | exact | raw KIND false match 위험 |
| 고려아연 | litigation | `20260417800134` | DART 공시 확보 | KIND `20260417000134` 가능 | exact | whitelist 제한적 허용 |
| LG화학 | litigation | `20260225801485` | DART 공시 확보 | KIND `20260225001485` 가능 | exact | whitelist 검증됨 |
| 고려아연 | ownership signal | `20260414001999` | majorstock API 확보 | XML 목적 파싱 가능 | exact | signal layer |

## requires_review 조건

- 위임장 본문에서 행사방향이 정규식으로 안 잡히는 경우
- 대량보유 목적과 proxy/litigation 타임라인이 충돌하는 경우
- vote_math를 위해 AGM result를 붙였는데 KIND 검증이 실패한 경우

## release_v2 판정

- `conditional`
- 이유:
  - `fight`, `litigation`, `signals`는 비교적 바로 묶을 수 있다
  - 하지만 `vote_math`까지 포함하면 AGM result와의 연결 검증이 더 필요하다

## 실무 해석

이 도구는 가치가 크지만 범위가 넓다.  
그래서 release_v2에서는 `summary/fight/litigation/signals`를 먼저 열고, `vote_math`는 뒤 단계로 두는 것이 안전하다.
