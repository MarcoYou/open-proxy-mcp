---
type: audit
title: value_up implementation tag audit
created: 2026-05-30
scope: KOSPI 500 + KOSDAQ 150 value_up filings since 2024-01-01
related:
  - wiki/tools/value_up.md
  - wiki/rules/disclosures/기업가치제고계획.md
  - wiki/architecture/audits/260517_parsing_success_rate_audit.md
---

# Value Up Implementation Tag Audit

## 목적

`value_up` tool이 기업가치제고계획 공시에서 `report_nm`만 보지 않고 본문
`계획서 명칭`과 `주요 내용`을 이용해 본계획/이행현황/이행전망/이행결과를
구분할 수 있는지 전수 점검했다.

KT&G 사례에서 확인한 핵심 문제는 다음이었다.

- `report_nm`은 `기업가치제고계획(자율공시)`로 같아도 본문 `계획서 명칭`이
  `2025년 KT&G 기업가치 제고계획 이행현황`이면 성격은 본계획이 아니라 이행현황이다.
- `주요 내용` 안에는 이행현황과 이행전망, 향후 계획이 함께 들어갈 수 있다.
- 고배당기업 표시 재공시는 `meta_amendment`지만, 본문 안에 업데이트된 이행결과가 들어갈 수 있다.

## 실행 범위

| 항목 | 값 |
|---|---:|
| Universe | 650 회사 |
| KOSPI | 500 회사 |
| KOSDAQ | 150 회사 |
| 조회 구간 | 2024-01-01 ~ 2026-05-30 |
| status ok | 650 / 650 |
| value_up 공시 보유 회사 | 317 |
| value_up filing | 562 |
| 실행 시간 | 약 160초 |

마지막 KOSDAQ 139~150 구간에서 일시적인 `ConnectError/ReadTimeout`이 발생해 해당 12개사를
concurrency 1로 재시도했다. 최종 병합본은 `650/650 ok`다.

## 최종 분포

| category | filing 수 |
|---|---:|
| `plan` | 384 |
| `progress` | 92 |
| `pre_announcement` | 58 |
| `meta_amendment` | 28 |

`pre_announcement`는 이번 audit 중 추가한 분류다. 기존에는
`기업가치제고계획예고(안내공시)`가 `plan`으로 남았는데, 실제 계획 본문이 없는 예고공시라
별도 category가 맞다.

## 계획서 명칭

| 항목 | filing 수 |
|---|---:|
| `plan_title` present | 501 |
| `plan_title` missing | 61 |

누락 61건의 대부분은 정상적인 예고공시다.

| 누락 report_nm | 건수 |
|---|---:|
| `기업가치제고계획예고(안내공시)` | 52 |
| `기업가치제고계획예고` | 6 |
| `[첨부정정]기업가치제고계획(자율공시)` | 3 |

## 주요 내용 태그

| tag | 건수 |
|---|---:|
| `implementation_status` | 216 |
| `future_plan` | 165 |
| `meta_reference` | 89 |
| `implementation_outlook` | 20 |
| `implementation_result` | 1 |

`implementation_result`는 KT&G 2026-03-27 고배당기업 재공시에서 확인됐다.
다른 고배당기업 재공시는 대부분 기존 계획 참조 또는 고배당기업 표시 자체가 중심이었다.

## 대표 사례

`report_nm`만 보면 일반 `기업가치제고계획(자율공시)`인데 `계획서 명칭` 때문에
`progress`로 보정된 사례:

| ticker | 회사 | 공시일 | rcept_no | 계획서 명칭 |
|---|---|---:|---|---|
| 329180 | HD현대중공업 | 20260401 | 20260401800278 | 2026 HD현대중공업 기업가치 제고 계획 이행현황 |
| 105560 | KB금융 | 20250424 | 20250424800498 | KB금융그룹 Value-up 이행 현황 |
| 010130 | 고려아연 | 20260325 | 20260325801109 | 2025년 고려아연 기업가치제고 이행 현황 |
| 009540 | HD한국조선해양 | 20260401 | 20260401800247 | 2026년 HD한국조선해양 기업가치 제고 계획 이행현황 |
| 033780 | KT&G | 20250923 | 20250923800350 | 2025년 KT G 기업가치 제고계획 이행현황 |
| 086280 | 현대글로비스 | 20260326 | 20260326802320 | 현대글로비스 기업가치 제고 계획 및 이행현황 |

고배당기업 재공시 안의 이행결과 사례:

| ticker | 회사 | 공시일 | rcept_no | tag |
|---|---|---:|---|---|
| 033780 | KT&G | 20260327 | 20260327800467 | `implementation_result` |

KT&G 재공시는 category를 `meta_amendment`로 유지하되, 주요 내용 안의
`'25년 주주환원 이행결과`를 `embedded_results`로 별도 노출한다.

## 산출물

- `scripts/audit_value_up_implementation_tags.py`
- `wiki/architecture/audits/data/260530_value_up_implementation_tags/summary.json`
- `wiki/architecture/audits/data/260530_value_up_implementation_tags/filings.csv`
- `wiki/architecture/audits/data/260530_value_up_implementation_tags/records.json`

샘플/재시도 중간 산출물은 로컬 점검용이다.

## 해석

이번 개선은 regression 위험이 낮은 분류 보강이다.

- `계획서 명칭`이 있으면 `report_nm`보다 더 정밀한 문서 성격 단서로 사용한다.
- 예고공시는 `plan`이 아니라 `pre_announcement`로 분리한다.
- 고배당기업 재공시는 여전히 `meta_amendment`로 두되, 안에 업데이트 결과가 있으면 버리지 않는다.

남은 한계:

- `주요 내용`은 회사별 자유형식이라 heading hierarchy가 안정적이지 않다.
- 일부 `implementation_outlook`은 “예상” 키워드가 넓게 잡힐 수 있어, 향후 샘플 리뷰로 정밀도를 더 봐야 한다.
- `[첨부정정]` 3건은 본문 텍스트가 얇거나 계획서 명칭이 노출되지 않아 viewer/PDF 확인이 필요할 수 있다.

## Meta Amendment vs Progress 비교

후속으로 `meta_amendment` 28건을 같은 회사의 최신 `progress`/`plan`과 비교했다.

산출물:

- `scripts/audit_value_up_meta_progress_compare.py`
- `wiki/architecture/audits/data/260530_value_up_meta_progress_compare/summary.json`
- `wiki/architecture/audits/data/260530_value_up_meta_progress_compare/compare.csv`
- `wiki/architecture/audits/data/260530_value_up_meta_progress_compare/details.json`

| relation | 건수 | 의미 |
|---|---:|---|
| `meta_duplicates_progress` | 12 | meta 공시의 주요 내용이 최신 progress와 거의 동일 |
| `meta_without_progress_compare_to_plan` | 9 | 비교할 progress가 없고 plan을 고배당기업 표시 목적으로 재공시 |
| `meta_reference_only` | 4 | 고배당기업 표시/기존 공시 참조 중심 |
| `meta_partially_overlaps_progress` | 2 | progress와 일부 겹치지만 동일하다고 보기 어려움 |
| `meta_embedded_result` | 1 | meta 안에 별도 이행결과 업데이트 포함 |

대표적으로 하나금융지주, LG화학, LG, 아모레퍼시픽, LG유플러스, 두산밥캣,
한전기술, 이마트 등은 meta가 progress와 거의 중복이었다. 반면 KB금융, 우리금융지주,
강원랜드 등은 참조/고배당 표시 성격이 강했고, KT&G는 meta 안에 `'25년 주주환원
이행결과`가 별도 업데이트로 들어갔다.

결론:

- `meta_amendment`는 최신 공시일 수 있지만 최신 `progress`를 대체하는 기본 데이터로 쓰지 않는다.
- 기본 응답 패키지는 `latest_plan` + `latest_progress`다.
- `latest_meta_amendment`는 고배당기업 표시/조세특례 reference로 별도 보존한다.
- 단, meta 안에 `implementation_result`가 있으면 `embedded_results`로 `latest_progress` 옆에 병합 노출한다.
- meta와 progress가 거의 동일하면 중복 본문을 반복하지 않고 “meta가 progress 재공시와 동일/유사”로만 표시한다.
