---
type: ralph
title: treasury 결과보고서 4종 추가 — decision/execution phase 통합 + 사이클 매칭
created: 2026-05-05 00:51
completion_promise: TREASURY_EXECUTION_VERIFIED
max_iterations: 15
---

## Invoke (복붙)

```
/ralph-loop:ralph-loop wiki/ralph/260505_0051_ralph_treasury-execution-results.md 가이드 따라 treasury_share 결과보고서 4종 추가. 검증: KOSPI200 + KOSDAQ top50 표본 150 회사 × 결과보고서 본문 파싱 성공률 ≥99% + 결정↔결과 사이클 매칭률 ≥99% + phase flag + scope 통합 모두 충족 시 promise. --completion-promise TREASURY_EXECUTION_VERIFIED --max-iterations 15
```

# Ralph: treasury_share 결과보고서 4종 추가

## 배경

현재 `treasury_share` tool은 **결정 (decision)** 중심:
- 취득결정 (`tsstkAqDecsn`)
- 처분결정 (`tsstkDpDecsn`)
- 신탁체결/해지결정 (`tsstkAqTrctrCnsDecsn`/`tsstkAqTrctrCcDecsn`)
- 소각결정 (list.json + body)
- 사업보고서 누적 (`tesstkAcqsDspsSttus`)

**누락**: 실제 집행 (execution) 결과 4종
- 자기주식 취득결과보고서 (취득 사이클 종료)
- 자기주식 처분결과보고서 (처분 사이클 종료)
- 신탁계약에 의한 취득상황보고서 (분기 보고)
- 신탁계약 해지결과보고서 (신탁 사이클 종료)

→ "결정만 보고 진짜 집행했는지 검증 X" 본질적 빈틈.

상세는 [[자기주식취득결과보고서]] / [[자기주식처분결과보고서]] / [[신탁계약에의한취득상황보고서]] / [[신탁계약해지결과보고서]] 참조.

## 가정 (이전 ralph 동일)
- No conversation context / no web search / MCP only / deterministic / temperature=0
- year=2026 (현재 정기주총 직후 시점)

## 매 iteration 작업
1. 현황: git status + 직전 검증 csv
2. 다음 1 step만 진행
3. fix 검증: KOSPI200 일부 표본 spot 측정
4. commit
5. 다음 iter 1줄

---

## 성공 기준 (모두 충족 시 promise)

### G1. 결과보고서 4종 본문 파싱 ≥99%

KOSPI 200 + KOSDAQ top50 = 150 회사 표본 중, list.json keyword로 발견된 결과보고서 전체에 대해:
- 자기주식취득결과보고서 본문 파싱 (일자별 raw + 합계 + 미달사유) 성공률 ≥99%
- 자기주식처분결과보고서 동일
- 신탁계약에의한취득상황보고서 동일
- 신탁계약해지결과보고서 동일

**근거**: 자본시장법 시행령 별지 표준 서식 — 일자별 row + 합계 행 구조 강제. parse_personnel처럼 자유 텍스트 X.

도달 못할 시 (≥99% fail): archive에 본문 raw + 실패 패턴 기록. 데이터 한계 (이미지 표 / PDF only / 정정공시 변형 등) 정직하게 audit.

### G2. 결정 ↔ 결과 사이클 매칭률 ≥99%

발견된 결과보고서의 "주요사항보고서 제출일" 필드 → 동일 회사 결정 공시 `rcept_dt` 매칭.

매칭률 = 매칭 성공 결과보고서 / 전체 결과보고서 ≥99%.

**근거**: "주요사항보고서 제출일"은 결과보고서 본문 standard 필드 (자본시장법 강제). 일자 정확 매칭 가능.

매칭 실패는 (a) 결정 공시 lookback 범위 밖 (b) 일자 표기 차이 — audit으로 분류 + window 보강.

### G3. event 필드에 phase 추가 + render 보강

각 event dict에 `phase` 필드 추가:
- `decision` (취득결정/처분결정/신탁체결/신탁해지/소각결정)
- `execution` (4종 결과보고서)
- `snapshot` (사업보고서 잔고)

render에서 timeline에 phase prefix (예: `[D]` 결정 / `[E]` 집행) 또는 분리 섹션.

### G4. scope 통합 (옵션 A — 단일 summary)

기존 6 scope (summary/events/acquisition/disposal/cancelation/annual) →
- `summary`: 모든 events (decision + execution) + type별 breakdown + cancelation summary
- `annual`: 사업보고서 잔고 (별도 chain)

총 2 scope으로 단순화.

cancelation 별도 scope 유지 (옵션 B) 결정은 ralph 진행 중 결과 보고 받아 결정.

---

## 작업 plan (예상 순서)

### Step 1. list.json keyword 추가 + 본문 fetch
`services/treasury_share.py`:
- `_RESULT_REPORT_KEYWORDS = (...)` 4종 keyword 정의
- `_search_result_reports(corp_code, bgn_de, end_de)` 함수
- `_decision_details` 패턴 (소각결정처럼) 그대로 적용

### Step 2. 본문 파서 4개 작성
- `_parse_acquisition_result_body(text)` — 일자별 매입 raw + 합계 + 미달사유
- `_parse_disposal_result_body(text)` — 일자별 처분 raw + 상대방 + 합계
- `_parse_trust_acquisition_status_body(text)` — 신탁 분기 보고 (누적/잔여)
- `_parse_trust_termination_result_body(text)` — 신탁 종료 (총취득실적/잔여재산)

### Step 3. event dict 통합
`build_treasury_share_payload`:
- decisions (기존 5종) + executions (신규 4종) 병렬 fetch
- 각 event에 `phase` 필드
- `events_timeline` 통합

### Step 4. 결정-결과 매칭
`_match_decision_to_execution(decisions, executions)`:
- 결과보고서 본문의 "주요사항보고서 제출일" 추출
- 동일 회사 결정 공시 `rcept_dt`와 매칭
- event에 `linked_decision_rcept_no` / `linked_execution_rcept_no` 추가

### Step 5. scope 통합 + render
- scope 6 → 2
- render: phase별 그룹 + cycle 매칭 표시

### Step 6. 검증 harness 작성 + 측정
`scripts/ralph_treasury_audit.py`:
- 150 회사 sample
- G1~G4 metric 산출 + json archive

---

## 영향 범위

- `open_proxy_mcp/services/treasury_share.py`: keyword + 4 parser + matching logic + scope 통합
- `open_proxy_mcp/tools_v2/treasury_share.py`: docstring + render 보강
- `wiki/rules/disclosures/`: 4 새 페이지 (이미 작성됨)
- `wiki/architecture/audits/data/260505_treasury_execution/`: 검증 csv archive

## 비목표 (이번 ralph X)

- ownership_structure 변동 cross-ref 자동화 (별도 ralph)
- 사업보고서 자기주식 변동 history (이미 annual scope에 있음)
- value_up tool 환원율 보강 (별도)

---

## 추출 필드 명세 (sampling 기반)

삼성전자 / 대한조선 / 카카오 / KT&G / 메리츠금융지주 본문 inspect 후 표준 표 포맷 확인. **회사별 variation 없음** — 표준 서식 일관 (자본시장법 시행령 별지).

### 결정 공시 5종

| 공시 | 핵심 필드 (parser 추출 대상) |
|---|---|
| **취득결정** (`tsstkAqDecsn`) | 취득예정주식(보통/기타) / 예정금액 / 취득예상기간(시작·종료) / **취득목적** / 취득방법 / 위탁증권사 / 취득전 보유현황(배당가능범위/기타) / 이사회결의일 / 사외이사 참석 / 보유예상기간 |
| **처분결정** (`tsstkDpDecsn`) | 처분예정주식(보통/기타) / **처분 대상 주식가격(시가)** / 예정금액 / 처분예정기간 / **처분목적** / 처분방법 (장내/시간외/장외/기타) / **처분상대방** (직원 N명, 회사명) / 위탁증권사 / 처분전 보유현황 / 이사회결의일 / 사외이사 참석 |
| **소각결정** (list.json+body) | 소각할 주식(보통/기타) / 발행주식 총수 / 소각예정금액 / 취득방법(기취득 vs 신규) / **소각방법** (자본감소/이익잉여금) / 소각 예정일 / 이사회결의일 / 사외이사 참석 |
| **신탁계약 체결결정** (`tsstkAqTrctrCnsDecsn`) | 계약금액 / 계약기간 / **계약목적** / 계약체결기관 / 위탁증권사 / 취득예정주식·가격 / **취득 후 보유예상기간** ("신탁기간 종료 후 전량 소각" 등 명시) / 이사회결의일 / 사외이사 참석 |
| **신탁계약 해지결정** (`tsstkAqTrctrCcDecsn`) | 해지일 / 해지사유 / 잔여재산 처리 / 해지 후 보유 자기주식 현황 (직접+신탁) / 이사회결의일 |

### 결과 공시 4종 (NEW)

| 공시 | 핵심 필드 (NEW parser 추출 대상) |
|---|---|
| **취득결과보고서** | 주요사항보고서 제출일(↔결정 매칭) / 취득기간 / **일자별 매입수량·평균단가·위탁증권사** raw / 합계(총수량·총금액·평균단가) / 미달 여부 + 사유 / 보유 자기주식 현황 |
| **처분결과보고서** | 주요사항보고서 제출일 / 처분기간 / **일자별 처분수량·단가·상대방** raw / 합계 / 미달 사유 / 보유 자기주식 현황 |
| **신탁취득상황보고서** (분기 정기) | 신탁계약 체결일 / 보고기간 / **월별 취득 raw** (수량·금액·단가·비중) / 누적 취득 / 잔여 한도 / 미달 여부 |
| **신탁해지결과보고서** | 신탁계약 체결일 / 해지일 / **총 취득실적** / 미달 사유 / **해지 후 보유현황** (직접A + 신탁B 합계) / 잔여재산 처리 |

### 제외 필드 (수집 X — 가치 낮음 / 별도 source)

- 1일 매수/매도 한도 (시장 영향 cap — 분석 가치 낮음)
- 공정위 신고 여부 (대기업 집단 specific)
- 1주당 액면가 (treasury 공시에 없음 — 회사 기본 정보)

---

## 회사별 패턴 (sampling 결과)

표준 표 포맷 일관, 다만 **사이클 패턴은 회사별 다름** — tool은 자동 detect.

| 회사 | 패턴 | 대표 이벤트 |
|---|---|---|
| **삼성전자** | 직접 취득 + 처분 + 소각 (신탁 X) | 취득결정 → 취득결과 → 소각결정 / 처분결정 → 처분결과 |
| **카카오** | **처분만** (취득/소각 X — RSU 지급 패턴) | 처분결정 (RSU 지급) → 처분결과 |
| **KT&G** | 직접 + 소각 빈번 | 취득결정 → 소각결정 반복 |
| **대한조선** | **신탁만** (직접 X) | 신탁체결 → 신탁취득상황(분기) → 신탁해지 → 신탁해지결과 → 소각 |
| **메리츠금융지주** | 신탁 + 소각 사이클 반복 (분기마다) | 신탁체결/해지/소각 동시 다수 |

→ tool 설계 시 사이클 자동 grouping 로직 필요 ("주요사항보고서 제출일" 또는 "신탁계약 체결일" 키 활용).

## archive 폴더

`wiki/architecture/audits/data/260505_treasury_execution/`
