---
type: ralph
title: treasury 결과보고서 4종 추가 — decision/execution phase 통합 + 사이클 매칭
created: 2026-05-05 00:51
completion_promise: TREASURY_EXECUTION_VERIFIED
max_iterations: 15
---

## Invoke (복붙)

```
/ralph-loop:ralph-loop wiki/ralph/260505_0051_ralph_treasury-execution-results.md 가이드 따라 treasury_share 결과보고서 4종 추가. 검증: KOSPI200 + KOSDAQ top50 표본 150 회사 × phase=execution 이벤트 추출 성공률 ≥80% + 결정↔결과 사이클 매칭률 ≥75% + scope 통합 (summary 단일) 모두 충족 시 promise. --completion-promise TREASURY_EXECUTION_VERIFIED --max-iterations 15
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

### G1. 결과보고서 4종 list.json 검색 + 본문 파싱 ≥80%

KOSPI 200 + KOSDAQ top50 = 150 회사 표본 중, **결과보고서 발생한 회사**에서:
- 자기주식취득결과보고서 검색 + 본문 파싱 (일자별 raw + 합계) 성공률 ≥80%
- 자기주식처분결과보고서 동일
- 신탁계약 취득상황보고서 동일
- 신탁계약 해지결과보고서 동일

부분 파싱 (raw 없으나 메타만)은 partial로 구분.

### G2. 결정 ↔ 결과 사이클 매칭률 ≥75%

발견된 결과보고서의 "주요사항보고서 제출일" 필드 → 동일 회사 결정 공시 (취득/처분/신탁) `rcept_dt` 매칭 시도.

매칭률 = 매칭 성공 결과보고서 / 전체 결과보고서 ≥75%.

매칭 실패는 결정 공시 미수집 또는 일자 mismatch — 별도 audit으로 분류.

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

## archive 폴더

`wiki/architecture/audits/data/260505_treasury_execution/`
