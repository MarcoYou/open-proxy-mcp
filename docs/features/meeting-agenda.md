# 주총 안건 구조화

주주총회 소집공고의 **안건 원문**을 구조화된 데이터로 바꿉니다.
주총 전 안건 파악(소집공고)과 주총 후 결과 확인(의결 결과)을 모두 다룹니다.

## 보기(scope) — 주총 전 (소집공고)

| scope | 무엇을 보나 |
|---|---|
| **summary** | 안건 종합 (아래 탭 한눈에) |
| **agenda** | 안건 목록 (이사·감사 선임, 정관변경, 보수한도 등) |
| **board** | 이사·감사 후보 — 후보별 경력·추천인 |
| **compensation** | 보수한도 — 이사·감사 보수 한도 승인 안건 |
| **aoi_change** | 정관변경 — 변경 전/후 조문 대비 |
| **prov_financials** | 재무제표 — 승인 대상 재무제표 요약 |

## 보기(scope) — 주총 후 (결과)

| scope | 무엇을 보나 |
|---|---|
| **results** | 의결 결과 (각 안건 가결/부결) + 찬반율 |

소집공고(사전)와 결과(사후)는 공시 포맷이 달라 별도 tool로 분리되어 있습니다 — `shareholder_meeting_notice`(사전) / `shareholder_meeting_results`(사후).

## 어떻게 쓰나

> "LG화학 2026 정기주총 안건 보여줘" (소집공고)
> "현대차 지난 주총 결과랑 찬반율" (결과)

## 함께 보면 좋은 기능

- [주총 의결권 보조](proxy-voting.md) — 안건을 **어떻게 의결할지** 권고 (이 안건 데이터가 판단 입력)

## 기술 상세 (개발자용)

> 아래는 각 tool의 입력·출력 등 기술 문서입니다. 일반 사용자는 보지 않아도 됩니다.

- [shareholder_meeting_notice](../../wiki/tools/shareholder_meeting_notice.md) — 소집공고 구조화 (사전)
- [shareholder_meeting_results](../../wiki/tools/shareholder_meeting_results.md) — 의결 결과·찬반율 (사후)
- [proxy_result_after_meeting](../../wiki/tools/proxy_result_after_meeting.md) — 주총 후 결과 종합
