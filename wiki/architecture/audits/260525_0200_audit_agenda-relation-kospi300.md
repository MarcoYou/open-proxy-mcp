---
type: audit
title: agenda-relation-kospi300
date: 2026-05-25
scope: KOSPI 300 shareholder_meeting_notice/proxy_advise agenda relation regression
related:
  - wiki/tools/shareholder_meeting_notice.md
  - wiki/tools/proxy_advise_before_meeting.md
  - wiki/lessons/agenda-relation-parser-260525.md
  - wiki/architecture/audits/data/260524_agenda_relation_corpus/README.md
---

# Agenda Relation KOSPI300 Audit

## 결론

KOSPI300 기준으로 `shareholder_meeting_notice`의 2026 정기 주주총회 소집공고 파싱과 `proxy_advise_before_meeting`의 agenda relation metadata를 재검증했다.

최종 재실행 결과:

| 항목 | 결과 |
|---|---:|
| 회사 수 | 300 |
| `exact` | 298 |
| `no_filing` | 2 |
| `requires_review` | 0 |
| `timeout` / `exception` | 0 |
| 안건 노드 | 3,212 |

남은 `no_filing` 2건은 파싱 실패가 아니라 정기 소집공고가 현재 DART에서 확인되지 않는 케이스다.

| 회사 | 결산월 | 판단 |
|---|---:|---|
| 신영증권 | 3월 | 예상 정기주총 window `2026-04-01~2026-07-31`, 현재 DART 기준 정기 소집공고 없음 |
| 프레스티지바이오파마 | 6월 | 예상 정기주총 window `2026-07-01~2026-10-31`, 현재 DART 기준 정기 소집공고 없음 |

## 변경 전 문제

KOSPI300 1차 측정에서는 다음 6건이 문제로 남았다.

| 회사 | 기존 상태 | 원인 |
|---|---|---|
| 신영증권 | `no_filing` | 3월 결산, 2026 정기 소집공고 미출현 |
| 한샘 | `no_filing` | 원문 제목부는 정기인데 뒤쪽 참고사항의 "임시주주총회부터" 문구를 보고 임시로 오판 |
| 호텔신라 | `requires_review` | 정정공고의 `4. 목적사항` 형식이 일반 `회의목적사항` zone에 걸리지 않음 |
| SNT홀딩스 | `requires_review` | 후보자 표 헤더가 제3호 안건 제목에 붙어 200자 초과 |
| 해성디에스 | `requires_review` | `제N호 의안.` 마침표형 + `※1주당 배당금` 주석 skip 경계 누락 |
| 프레스티지바이오파마 | `no_filing` | 6월 결산, 2026년에 확인된 것은 임시주총뿐 |

## 수정 내용

`open_proxy_mcp/tools/parser.py`

- 정기/임시 판별은 소집공고 제목부 `(제N기 정기|임시)`를 우선한다.
- 안건 번호 marker에 `제N호 의안.` 마침표형을 허용한다.
- `※` 주석 skip boundary도 마침표형 안건 marker를 인식한다.
- 후보자 표 헤더 boundary를 보강했다.
- `4. 목적사항`처럼 `회의` prefix가 없는 목적사항 heading도 agenda zone으로 인정한다.

`open_proxy_mcp/services/shareholder_meeting.py`

- `no_filing` warning에 결산월(`fiscal_month`)과 예상 정기주총 개최 window를 표시한다.
- 메시지는 미래 구간을 단정하지 않고 "현재 조회 가능한 DART 공시 기준 아직 없음"으로 표현한다.

`open_proxy_mcp/services/proxy_advise.py` / `shareholder_meeting.py`

- agenda node에 `proposer_type`, `agenda_relation_type`, `agenda_relation_reasons`를 노출한다.
- 절차성/대안형/조건부 안건은 law layer hit가 없으면 자동 FOR 대신 REVIEW로 둔다.
- 집중투표 5인/6인 선임형 slate는 행사 의결권 기준 필요최소지분율을 facts에 추가한다.

## 최종 relation 분포

| relation | count |
|---|---:|
| normal | 3,034 |
| cumulative_related | 139 |
| conditional | 33 |
| procedural | 2 |
| alternative | 4 |

`procedural`/`alternative`는 KOSPI300 전체에서 고려아연형 복합 구조에 집중되어 있었다. KOSDAQ top 50에서는 procedural/alternative 신규 케이스가 없었다.

## proxy_advise consistency 해석

이번 audit의 의미는 "모든 안건이 법령 layer에 걸린다"가 아니다. 실제 보장 범위는 다음이다.

- 파싱된 모든 agenda node는 `proposer_type`, `agenda_relation_type`, `agenda_relation_reasons`를 동일 schema로 가진다.
- `proxy_advise_before_meeting`은 full agenda tree를 기준으로 안건 결정을 생성한다.
- layer 적용 순서는 일관된다:
  1. law layer hit 우선
  2. law layer hit가 없는 절차성/대안형/조건부 안건은 REVIEW guardrail
  3. 일반 재무제표/배당/후보/보수/퇴직금/정관변경 decision path
  4. policy default
- 법령상 당연히 찬성해야 하는 안건은 relation REVIEW보다 law layer가 우선한다.
- relation metadata는 결론이 아니라 자동 판단을 멈추는 guardrail이다.

따라서 사용자-facing report에서는 "모든 안건이 layer에 걸렸다"가 아니라 "같은 schema와 같은 판단 순서로 리포트된다"고 설명해야 한다.

## 검증

명령:

```bash
uv run pytest tests/test_shareholder_meeting_parser_edges.py tests/test_shareholder_meeting_notice_perf.py tests/test_agenda_relation.py tests/test_proxy_advise_timings.py -q
```

결과:

```text
23 passed
```

KOSPI300 재실행:

- output: `wiki/architecture/audits/data/260524_agenda_relation_corpus/relation_audit/kospi_001_300_relation_live_rerun_after_parser_fix.json`
- `exact`: 298
- `no_filing`: 2
- `requires_review`: 0

성능 spot:

- 실제 문제 케이스와 대표 대형사 6개 모두 summary 호출 0.1~0.5초대.
- parser micro benchmark는 `parse_agenda_xml` / `parse_meeting_info_xml` 대략 22~46ms/회.
- 이번 변경은 regex/boundary 보강이라 네트워크/API 시간 대비 의미 있는 latency regression은 관찰되지 않았다.

## 남은 리스크

- 이미지 중심 공고는 XML 파서만으로 한계가 있다.
- 정정공고 표 형식은 추가 변형이 있을 수 있다.
- agenda relation은 파싱 보조 metadata이지 단독 판단 근거가 아니다. 법령 layer와 후보/정관 원문 판단이 우선한다.

## 관련

- [[shareholder_meeting_notice]]
- [[proxy_advise_before_meeting]]
- [[agenda-relation-parser-260525]]
- [[data/260524_agenda_relation_corpus/README]]
