---
type: audit
title: agenda parser marketwide audit
created: 2026-05-25
scope: KOSPI 500 + KOSDAQ 150 shareholder_meeting_notice agenda parser
related:
  - wiki/tools/shareholder_meeting_notice.md
  - wiki/architecture/audits/260525_0200_audit_agenda-relation-kospi300.md
  - wiki/architecture/audits/260517_parsing_success_rate_audit.md
---

# Agenda Parser Marketwide Audit

## 목적

KOSPI 시가총액 상위 500개와 KOSDAQ 시가총액 상위 150개에 대해 2026년 정기주주총회 소집공고의 안건 번호, 제목, 본문/세부내용 추출 안정성을 점검했다.

이번 audit는 live DART 조회와 parser determinism 검증을 분리했다.

- Loop 1: DART live fetch + `document.xml` 로컬 저장
- Loop 2-1 / 2-2 / 2-3: 저장 XML만 재파싱
- 목적: rate-limit 리스크 없이 동일 XML에 대한 parser 결과가 반복 실행마다 같은지 확인

## 실행 결과

| 항목 | 결과 |
|---|---:|
| Universe | 650 회사 |
| KOSPI | 500 회사 |
| KOSDAQ | 150 회사 |
| XML 확보 / 파싱 완료 | 641 |
| no_filing | 9 |
| resolve_failed | 0 |
| 재파싱 loop | 3 |
| loop별 hash diff vs live | 0 / 0 / 0 |

`no_filing` 9건은 조회 구간 내 2026 정기주총 소집공고가 없다고 분리됐다.

- 맥쿼리인프라
- 신영증권
- KB발해인프라
- 맵스리얼티
- 프레스티지바이오파마
- 삼립
- 만호제강
- 코스모로보틱스
- 마키나락스

## 발견된 이슈

중복 loop를 제외한 live 기준 구조 이슈는 4개 회사 / 5건이다.

핵심 KPI:

| KPI | 산식 | 결과 |
|---|---|---:|
| Non-empty parse rate | 641 / 641 | 100.00% |
| Clean parse rate | 637 / 641 | 99.38% |

| issue code | live 건수 | 의미 |
|---|---:|---|
| `charter_agenda_without_aoi_amendments` | 2 | 정관 변경 안건은 있으나 aoi amendment 파싱 비어 있음 |
| `title_too_long` | 2 | 제목이 긴 정관 조문 요약/항목 나열 형태임 |
| `agenda_low_confidence` | 1 | agenda parser confidence 낮음 |

상위 문제 회사:

| ticker | 회사 | issue 건수 |
|---|---|---:|
| 241590 | 화승엔터프라이즈 | 2 |
| 071050 | 한국금융지주 | 1 |
| 024110 | 기업은행 | 1 |
| 030610 | 교보증권 | 1 |

## 유사 표현 Catalog

`variants.csv`에 anchor 주변 원문 snippet을 저장했다. 완전 동일 워딩이 아니어도 같은 계열 표현을 추적하기 위한 자료다.

| anchor | snippet 수 |
|---|---:|
| 사외이사 | 11,837 |
| 감사위원 | 8,102 |
| 전자투표 | 6,167 |
| 독립이사 | 5,758 |
| 자기주식 | 5,265 |
| 보수한도 | 2,808 |
| 분리선출 | 1,968 |
| 집중투표 | 1,545 |
| 퇴직금 | 1,252 |
| 전자주주총회 | 624 |
| 충실의무 | 320 |
| 별개의 조 | 2 |

## 산출물

Tracked:

- `scripts/audit_agenda_parser_marketwide.py`
- `wiki/architecture/audits/data/260525_agenda_parser_marketwide/universe_kospi500.csv`
- `wiki/architecture/audits/data/260525_agenda_parser_marketwide/universe_kosdaq150.csv`
- `wiki/architecture/audits/data/260525_agenda_parser_marketwide/issues.csv`
- `wiki/architecture/audits/data/260525_agenda_parser_marketwide/summary.json`

Local-only:

- `wiki/architecture/audits/data/260525_agenda_parser_marketwide/loop_01_live.jsonl`
- `wiki/architecture/audits/data/260525_agenda_parser_marketwide/loop_02_1_reparse.jsonl`
- `wiki/architecture/audits/data/260525_agenda_parser_marketwide/loop_02_2_reparse.jsonl`
- `wiki/architecture/audits/data/260525_agenda_parser_marketwide/loop_02_3_reparse.jsonl`
- `wiki/architecture/audits/data/260525_agenda_parser_marketwide/variants.csv`
- `cache/audits/260525_agenda_parser_marketwide/documents/`

대형 loop JSONL, variants snippet, XML corpus는 repo 추적 대상이 아닌 로컬 재현/점검 자료로 둔다.

## 해석

핵심 결론은 parser determinism은 통과했다는 점이다. 같은 XML을 세 번 재파싱했을 때 결과 hash diff가 0이었다.

남은 문제는 nondeterminism이 아니라 source 구조 다양성이다.

- 제목에 후보 상세 표가 붙는 공고
- 인사 안건 제목과 후보 표 구조가 느슨하게 분리된 공고
- 정관/보수한도 section title은 있으나 detail section이 비표준인 공고
- 이미지나 viewer fallback이 필요한 공고

이번 후속 수정에서 처리한 항목:

- `parse_agenda_xml`: 사업보고서/감사보고서 첨부 섹션이 마지막 안건 제목에 붙는 boundary 문제를 차단했다.
- `parse_personnel_xml`: `독립이사 후보 추천에 관한 규정 신설` 같은 정관 문구를 후보자 이름으로 오인하지 않도록 제목 후보명 추출을 보수화했다.
- `parse_personnel_xml`: `후보자 : 김 종 민`처럼 한글 이름이 음절 단위로 띄어진 제목 fallback을 허용했다.
- `parse_personnel_xml`: 상세 섹션에는 부모 안건만 있고 agenda tree 하위 안건에 후보명이 있는 구조를 board 후보 fallback으로 보강했다.
- `parse_personnel_xml`: `사외이사 후보 전병선 선임`, `이사후보(사외이사) 조강래`, `장세욱(남성)` 같은 후보명 표현을 정규화했다.
- `parse_compensation_xml`: `보수총액 한도`, `보수지급한도` 표현을 보수한도 승인 안건으로 인식하도록 확장했다.
- `parse_compensation_xml`: 상세 표가 없지만 agenda title에 `이사/감사 보수한도 승인`이 있는 경우 금액 없이 target item만 보수적으로 생성한다.
- `parse_personnel_xml`: 정관 변경성 문구(`선임 관련 변경`, `의무 추가`, `분리선임 인원 변경`, `선ㆍ해임시 의결권 제한기준 변경`)를 인사 안건으로 보지 않게 조정했다.
- `parse_personnel_xml`: `사외이사 한종복 (신규선임)`처럼 role+name 뒤 괄호 suffix가 붙는 후보명을 추출한다.
- `parse_agenda_xml`: `제1호의 안`, `제1호의안` 같은 의안 marker를 정규 안건 번호로 인식한다.
- `parse_personnel_xml`: `가. 후보자의 성명...` 문구가 section heading이 아니라 본문 text block으로 들어오는 경우에도 뒤따르는 후보자 table을 후보 section으로 인식한다.
- `parse_aoi_xml`: `변경(안)`, `변경안`, `개정 전`, `개정 후`, `개정(안)` header를 정관 변경 전/후 table로 인식한다.
- audit validator: `보수한도 규정 신설`은 보수한도 승인 item 누락으로 보지 않고, `감사위원인 이사`는 감사 보수가 아니라 이사 보수로 판정하도록 조정했다.
- audit validator: parser보다 느슨했던 후보명 추출 규칙을 production parser와 같은 방향으로 조정해 `후보 추천`, `후보추천위원회`, 영어 이름 중복 카운트, 국적어 오인을 제거했다.
- audit validator: 정관 변경성 선임/해임 문구는 `personnel_agenda_without_candidates`에서 제외한다.

성능 메모:

- compensation parser timing: avg 113.85ms, p95 406.88ms (same-run `parse_agenda_details_xml` avg 107.39ms, p95 355.36ms).
- personnel parser timing after final low-risk fixes: avg 324.84ms, p95 1072.82ms.
- 보수한도 fallback 자체의 추가 비용은 same-run 기준 평균 약 +6.5ms 수준이다.
- 전체 parser determinism은 3-loop hash diff 0 / 0 / 0으로 유지됐다.

남은 개선 후보와 분류:

| 케이스 | 분류 | 판단 |
|---|---|---|
| 한국금융지주 / 기업은행 `charter_agenda_without_aoi_amendments` | regression-risk | 정관 변경 안건은 있으나 현재 XML section에서 변경 전/후 table 근거가 비어 있다. agenda title만으로 amendment를 생성하면 원문 없는 추정이 된다. raw/PDF/viewer fallback 설계가 필요하다. |
| 교보증권 / 화승엔터프라이즈 `title_too_long` | 보류 | 후보 표나 사업보고서 안내문 누수가 아니라 긴 정관 조문 요약/항목 나열이다. 일괄 truncation은 정보 손실 가능성이 있다. |
| 화승엔터프라이즈 `agenda_low_confidence` | 보류 | 긴 정관 요약과 정정공시 구조 영향으로 confidence가 낮다. 현 단계에서는 제목/번호가 비어 있는 실패가 아니므로 raw 확인 후 별도 처리한다. |
