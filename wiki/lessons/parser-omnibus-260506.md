---
type: lesson
title: parser omnibus 검증 — DART 6컬럼 패턴 sub-column 처리 (260506)
date: 2026-05-06
related:
  - wiki/ralph/260505_2330_ralph_parser-omnibus-perf.md
  - wiki/decisions/260506_2330_decision_v1-dead-parsers-archive.md
  - wiki/lessons/scope-simplification.md
---

# Parser omnibus 검증 + sub-column 발견 (260506)

## 결과 요약

300 회사 (KOSPI 200 + KOSDAQ 100) 통합 audit 후 9 parser G1 ≥98.7% 달성:

| Parser | G1 (final) | 비고 |
|---|---|---|
| meeting_info | 100% | |
| agenda | 99.7% | 호텔신라 1건 doc 구조 |
| agenda_details | 100% | |
| corrections | 100% | |
| personnel(director) | 99.6% | |
| personnel(audit) | 99.6% | |
| aoi | 98.7% | |
| compensation | 98.8% | |
| retirement(call) | 100% | |
| pfs(call) | 100% | |
| **pfs metrics ≥6** | **100%** | column-meta fix 후 |

## 핵심 lesson — DART 6컬럼 row pattern

### 현상

19개 KOSPI 회사 (현대차/셀트리온/두산/기업은행/LG/KT 등) 잠정 재무제표 metric 추출 sparse (filled=2 or 0).

처음 가설:
- (X) "잠정 재무제표 disclosure에 summary 라인 자체가 빈 값" — 데이터 한계로 잘못 판단
- 실제는 **parser column-meta 버그** ← 코붕이 피드백 "데이터 없는건지 잘못 검색한건지 별도 파서 필요인지 창의적으로 다시 생각" 후 발견

### Root cause

DART 잠정 재무제표 html은 6컬럼 row 사용 (공통 패턴):
```html
<TR>
  <TD>I. 매출액</TD>            ← col 0: account
  <TD>25,37</TD>                ← col 1: note
  <TD ALIGN="RIGHT"></TD>       ← col 2: empty (visual padding)
  <TD ALIGN="RIGHT">186,254,472</TD>  ← col 3: current value
  <TD ALIGN="RIGHT"></TD>       ← col 4: empty (visual padding)
  <TD ALIGN="RIGHT">175,231,153</TD>  ← col 5: prior value
</TR>
```

헤더는 4 TH + colspan="2":
```html
<TR>
  <TH>과 목</TH>                ← col 0
  <TH>주석</TH>                 ← col 1
  <TH colspan="2">제58기</TH>    ← col 2-3 (current period)
  <TH colspan="2">제57기</TH>    ← col 4-5 (prior period)
</TR>
```

기존 `_build_column_meta` 로직:
- "과 목" → account
- "주석" → note
- "제58기" → `_period_by_num`
- "" (colspan 확장) → "**unknown**" (← 버그!)
- "제57기" → `_period_by_num`
- "" (colspan 확장) → "**unknown**"

후속 변환:
- `_period_by_num` → current/prior 변환 (큰 기수가 current)
- "unknown"은 그대로 → `_normalize_financial_rows`에서 무시
- 결과: row[2] (empty)을 current로, row[4] (empty)을 prior로 인식 → 둘 다 빈 값

### Fix

`_build_column_meta`에서 빈 셀 분류 logic 보강:

```python
elif not clean:
    if columns and columns[-1] in ("current", "prior"):
        columns.append(f"{columns[-1]}_sub")
    elif columns and columns[-1] == "_period_by_num":
        columns.append("_period_by_num_sub")  # ← 추가
    elif columns and columns[-1] in ("current_sub", "prior_sub", "_period_by_num_sub"):
        columns.append(columns[-1])  # 연속 빈 셀
    else:
        columns.append("unknown")
```

`_period_by_num` 변환 후 `_period_by_num_sub`도 propagate:

```python
for i, c in enumerate(columns):
    if c == "_period_by_num_sub":
        for j in range(i - 1, -1, -1):
            if columns[j] in ("current", "prior"):
                columns[i] = f"{columns[j]}_sub"
                break
```

기존 `_normalize_financial_rows`는 이미 `current_sub`/`prior_sub`를 fallback으로 사용 (값 없으면 sub 사용).

### 검증

- 19 sparse 케이스 100% PASS (이전 1/19)
- 회귀 90 회사 (KOSPI + KOSDAQ) PFS 100% — Samsung 등 4컬럼 패턴도 정상 유지
- 최종 phase1 aggregate (357 OK records) PFS metric extraction **100%**

## Meta-lesson — 의심 vs 확신

**잘못된 결론**: "데이터가 없는것으로 보인다 → honest data limit"
- 첫번째 spot debug에서 표 cells 비어있음 확인
- `'현대차의 잠정재무제표 disclosure는 summary line이 비어있다'` 추정
- **확인 안 함** — raw html 직접 검색 안 함

**창의적 재검토 (코붕이 피드백)**:
- "데이터가 없는건지, 잘못 검색한건지, 별도 파서가 필요한건지"
- raw html 직접 search → 매출액 186,254,472 명확히 존재
- table cell 구조 인쇄 → 6컬럼 패턴 발견
- column meta 로직 점검 → 헤더 colspan 확장 빈 셀 처리 누락

**교훈**:
- 표면적 결과로 결론 내리지 말고 raw 데이터 (html / 텍스트 / cell 구조)로 검증
- 데이터 한계 vs parser 버그 구분: raw에 값 있으면 parser 문제, 없으면 데이터 한계
- Spot debug script가 핵심 — `parsed dict` 출력 + `cell list` 출력 + `raw html search`

## 영향

- `services/provisional_financial_statement.py`:
  - `_METRIC_KEYWORDS.net_income_krw`에 `지배기업소유주지분` 등 추가 (보조 keyword)
  - `_NON_FS_TABLE_HINTS` 신규 (영문 사명 ≥6 줄 — 종속회사 목록 reject)
  - `_build_column_meta` — `_period_by_num_sub` 처리 (핵심 fix)
  - `extract_metrics` `scope_used` 보고 버그 fix (실제 추출한 scope만 기록)
- `scripts/spot_pfs_html_search.py` 신규 (raw html 직접 검색 도구)
- `scripts/spot_pfs_sparse_recheck.py` 신규 (19 sparse 회귀 검증)
- `scripts/spot_parser_omnibus.py` (Tier A 9 parser 통합 master)
- `scripts/agg_parser_omnibus.py` (batch JSON 통합 분석)

## 비목표

- v1 dead parser physical archive (별도 결정, 보류)
- 도메인 services 깊이 audit (별도 ralph)
- KOSDAQ universe 확장
