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

## 샘플 확인 (2026-04-19 실행, scope=summary)

| company | contest_signal | shareholder | retail_activism | litigation | external_active | overlap | 해석 |
|---|---|---|---|---|---|---|---|
| 고려아연 | **True** | 4 (영풍) | 0 | 22 | 3 (최윤범/크루시블/한국기업투자홀딩스) | 1 (영풍) | 진행중 경영권 분쟁 — 위임장·소송·5% 보유 전방위 |
| 한진칼 | False | 0 | 0 | 0 | 0 | 1 (조원태) | 과거 조현아 사건 이후 지배 안정. 현 회장 5% 신고만 잔존 |
| 삼성전자 (대조군) | False | 0 | 3 (**컨두잇**) | 0 | 0 | 1 (삼성물산) | 소액주주 집단 위임 플랫폼 ACT(컨두잇) 캠페인은 retail_activism으로 분리. 삼성물산 "경영참여" 신고는 계열사 등재(registry_overlap) → 분쟁 아님 |

### 지표 정의

- `shareholder_side_count`: 주주측 위임장 권유 (실제 경영권 분쟁 주체)
- `retail_activism_count`: 소액주주 집단 위임 플랫폼 (ACT/컨두잇, 헤이홀더, 비사이드코리아)
- `external_active_block`: 외부 5% 대량보유 + 경영참여 목적
- `registry_overlap`: 회사 등재자(계열사/현 경영진)의 5% 신고 — 분쟁 아님
- `has_contest_signal` = `shareholder_side OR litigation OR external_active` (retail_activism, overlap 제외)

### 교차 참조 힌트 (fight scope)

각 shareholder-side / retail_activism 위임장 행에 주체 중심 플래그 추가:

- `filer_has_5pct_active_block`: 같은 filer가 5% 대량보유 경영참여 신고도 했는지 (overlap/external 무관)
- `filer_in_litigation`: 같은 filer가 소송/가처분 공시의 제출인으로 잡혔는지

**자동 분류는 하지 않는다.** 애널리스트/LLM이 아래 신호 조합으로 판단:

| 힌트 조합 | 해석 |
|---|---|
| 5%경영참여 ✓ + 소송 ✓ | proxy_fight (경영권 분쟁 주체) — 예: 고려아연 영풍 |
| 5%경영참여 - + 소송 - | proxy_campaign (주주제안/캠페인) — 예: LG화학 Palliser Capital |
| retail_activism side | 소액주주 집단 위임 — 예: 삼성전자 컨두잇(ACT) |

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
