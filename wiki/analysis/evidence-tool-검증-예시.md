---
type: analysis
title: evidence tool 검증 예시
tags: [release-v2, tool, validation, evidence]
date: 2026-04-18
related: [tool-추가-검증-템플릿, tool-추가-검증-정책, shareholder_meeting-tool-검증-예시]
---

# evidence tool 검증 예시

## 목적

`evidence`는 분석 도구가 아니라 `근거 조회 도구`다.  
애널리스트가 “이 문장이 어디서 나왔는가”를 바로 확인하는 용도다.

## 제안 요약

- tool type: `data`
- 핵심 질문:
  - 이 숫자/문장/판단의 원문은 어디에 있는가
  - 어떤 소스에서 어떤 방식으로 추출됐는가
- 기대 결과물:
  - `evidence_id`
  - `source_type`
  - `rcept_no`
  - `section`
  - `snippet`
  - `confidence`

## 소스 정책

`evidence`는 새 외부 소스를 붙이지 않는다.  
이미 검증된 upstream data tool의 evidence pointer만 조회한다.

| field | primary source | note |
|---|---|---|
| snippet | upstream stored snippet | 새 추론 없음 |
| rcept_no | upstream filing id | source lineage 유지 |
| source_type | upstream enum | `dart_xml`, `kind_html` 등 |
| confidence | upstream parser result | derived |

## 샘플 설계

| origin tool | example filing | expected evidence |
|---|---|---|
| shareholder_meeting | `20260312000987` | 안건 section snippet |
| dividend | `20260129800004` | DPS / record date snippet |
| value_up | `20260327802428` | 밸류업 약속 문장 snippet |

## requires_review 조건

- upstream tool이 evidence를 충분히 남기지 않은 경우
- section/snippet이 비어 있는데 결과만 남아 있는 경우
- source_type이 혼재돼 lineage가 끊기는 경우

## release_v2 판정

- `conditional`
- 이유:
  - 개념적으로는 바로 필요하다
  - 하지만 공개 전에 `evidence_id`, `item_id`, `source_type`, `confidence` 스키마를 먼저 고정해야 한다

## 실무 해석

`evidence`가 있어야 action tool이 과장되지 않는다.  
즉 이 도구는 보기 좋은 부가기능이 아니라, `결론과 근거를 분리하는 안전장치`다.
