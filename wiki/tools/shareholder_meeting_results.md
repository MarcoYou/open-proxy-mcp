---
type: tool
title: shareholder_meeting_results
description: 주주총회 의결 결과 (사후) — KIND scraping 기반
related: [shareholder_meeting_notice, proxy_result_after_meeting, evidence]
---

# shareholder_meeting_results

주총 **의결 결과** 공시 (사후 — KIND scraping). 안건별 가결/부결 + 찬반율. 4-5s.

## 분리 배경 (2026-05-04)

KIND 웹 스크래핑은 fragile (KIND 변경 시 깨짐). DART API 기반 `shareholder_meeting_notice`와 격리 — 한쪽 source 장애가 다른 쪽 영향 X.

## scope

`results` 단일 (param 자체 없음):

| 필드 | 의미 |
|---|---|
| `result_format` | "table" / "text" / "image" |
| `numerical_vote_table_available` | 표 정형 추출 가능 여부 |
| `items[]` | 안건별 결과 |
| - `agenda` | 안건명 |
| - `resolution_type` | 보통결의 / 특별결의 / 보고 |
| - `passed` | 가결 / 부결 / 보고완료 |
| - `approval_rate_issued` | 발행주식수 기준 찬성률 |
| - `approval_rate_voted` | 출석주식수 기준 찬성률 |
| - `opposition_rate` | 반대율 |

## source

- DART rcept_no → KIND acptno 변환 (80→00 whitelist)
- KIND `kind_fetch_document` 본문 HTML 파싱
- 결과 미공시 (가결 후 KIND 노출 지연) 시 status=pending_or_missing

## 사용 예

```
"삼성전자 2026 정기주총 결과"
"LG화학 안건별 찬반율"
"고려아연 임시주총 의결 결과"
```

## ref

- 사전 안건/후보: [[shareholder_meeting_notice]]
- 종합 사후 보고: [[proxy_result_after_meeting]]
- 원문: [[evidence]]
