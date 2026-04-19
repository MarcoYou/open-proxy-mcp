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

## 소스 정책 (v2 재설계)

evidence tool은 외부 소스 조회를 하지 않는다. rcept_no 문자열만으로 즉시 유도 가능한 정보만 반환한다.

| field | 도출 방법 |
|---|---|
| `rcept_no` | 입력값 그대로 |
| `rcept_dt` | `rcept_no[:8]`에서 `YYYY-MM-DD` |
| `source_type` | `rcept_no[8:10] == "80"` → KIND, 그 외 → DART |
| `viewer_url` | source별 패턴 (DART: `dart.fss.or.kr/dsaf001/main.do?rcpNo=`, KIND: `kind.krx.co.kr/common/disclsviewer.do?method=search&acptno=`) |
| `report_nm` | upstream evidence_refs에 이미 있으면 그대로 전달. 생 rcept_no 입력 시에는 공란 (viewer_url로 사용자가 직접 확인) |

## 샘플 확인 (2026-04-19 실행)

| rcept_no | status | rcept_dt | source_type | viewer_url | note |
|---|---|---|---|---|---|
| `20260305001616` | exact | 2026-03-05 | dart_xml | `dart.fss.or.kr/.../rcpNo=20260305001616` | 고려아연 정기주총 정정공고 (DART XML 경로) |
| `20260213800001` | exact | 2026-02-13 | kind_html | `kind.krx.co.kr/.../acptno=20260213800001` | KIND 형식 (9~10자리 `80`) 정상 분류 |
| `ABC` (엣지) | requires_review | - | - | - | 14자리 숫자 아님 → requires_review + 경고 문구 |

- DART/KIND 분기 규칙이 rcept_no 구조만으로 결정됨 (API 호출 0)
- viewer_url은 자동 생성, 사용자가 클릭해 원문 확인

## requires_review 조건

- rcept_no가 14자리 숫자가 아닌 경우
- evidence_id만 있고 내부에 rcept_no 패턴이 없는 경우

## release_v2 판정

- `go`
- 이유:
  - API 호출 없는 순수 문자열 가공이라 실패 지점이 없다
  - upstream evidence_refs가 `rcept_dt`/`report_nm`을 이미 채우고 있어 tool 호출 없이도 인용 정보 확보 가능
  - 원문 본문은 viewer_url 클릭으로 DART/KIND 뷰어에서 보는 게 더 정확 (표·각주 포함)

## 실무 해석

`evidence`가 있어야 action tool이 과장되지 않는다.  
즉 이 도구는 보기 좋은 부가기능이 아니라, `결론과 근거를 분리하는 안전장치`다.
