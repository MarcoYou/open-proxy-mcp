---
type: analysis
title: corp_gov_report 금융지주 파싱 fix 2026-04-29
tags: [audit, parsing, fix, corp_gov_report, financial-holding, regression-test]
related: [parsing-audit-2026-04-29-v2, corp_gov_report-design]
date: 2026-04-29
---

# corp_gov_report 금융지주 파싱 fix 2026-04-29

audit v2 (`parsing-audit-2026-04-29-v2.md`)에서 발견된 corp_gov_report 18건 partial_failure
(모두 KOSPI 금융지주/은행/보험/증권)를 NO_FILING으로 정확 분리. 진짜 partial은 18 -> 0.

## 문제 요약

audit v2 결과: corp_gov_report.summary 196건 중
- exact 94 / no_filing 82 / **partial_failure 18** / error 2

partial_failure 18건 모두 KOSPI 금융권:
KB금융, 삼성생명, 신한지주, 미래에셋증권, 하나금융지주, 우리금융지주, 삼성화재,
메리츠금융지주, 기업은행, 한국금융지주, 카카오뱅크, DB손해보험, 키움증권,
NH투자증권, 삼성증권, 삼성카드, JB금융지주, BNK금융지주.

기존 파서: `_EXCLUDE_REPORT_SUBSTR = ("연차보고서",)` 만으로 일부 형식 제외했지만,
여전히 옛 보고서 (suffix 없는 것)들이 잡혀 본문 파싱이 실패하고 있었음.

## 근본 원인 분석

KB금융 (rcept_no=20240229801471) 본문 직접 fetch 결과:

```
KB금융/기업지배구조 보고서 공시/(2024.02.29)기업지배구조 보고서 공시
기업지배구조 보고서 공시
1. 구분
금융회사 지배구조 연차보고서
2. 보고서 명칭
KB금융지주 2023년 지배구조 및 보수체계 연차보고서
3. 주요내용
- 본 보고서는 '금융회사의 지배구조에 관한 법률' 등에 따라 작성한
  '2023년 지배구조 및 보수체계 연차보고서'로서 ...
4. 제출(확인)일자
2024-02-29
5. 기타 투자판단과 관련한 중요사항
보고서의 세부내용은 첨부된「KB금융지주 2023년 지배구조 및 보수체계 연차보고서」를
참고하시기 바랍니다.
```

**핵심 차이**:
- 일반 KOSPI 거버넌스 보고서: 본문이 33,000자 안팎이며 15개 핵심지표 표를 직접 포함
- 금융회사: 본문이 500-800자 메타데이터만, 실제 내용은 PDF 첨부에 있음
- 「자본시장법」 기업지배구조보고서가 아닌 「금융회사의 지배구조에 관한 법률」에 따른 별도 서식

18건 모두 검증 결과:
- 15건: HTML 본문 < 1000자, 'fin_form=True' 마커 명확히 감지
- 3건 (신한지주/DB손해보험/NH투자증권): API 014 (파일 없음) — 정정/첨부 파일들이라
  `_EXCLUDE_REPORT_SUBSTR` 확장으로 미리 제거하면 자연스럽게 다음 후보로 넘어감

## Fix

### 코드 변경 (`open_proxy_mcp/services/corp_gov_report.py`)

#### 1. `_EXCLUDE_REPORT_SUBSTR` 확장

```python
_EXCLUDE_REPORT_SUBSTR = (
    "연차보고서",       # 금융회사 지배구조 연차보고서 형식
    "(자율공시)",       # 자회사 보고서를 지주가 대신 공시
    "[첨부정정]",       # 본문 014 (파일 없음) 다수
    "[첨부추가]",       # 본문 014 (파일 없음) 다수
)
```

#### 2. financial_form 마커 신규 정의

```python
_FINANCIAL_FORM_MARKERS = (
    "금융회사 지배구조 연차보고서",
    "지배구조 및 보수체계 연차보고서",
)

def _is_financial_form(text: str) -> bool:
    if not text:
        return False
    return any(marker in text for marker in _FINANCIAL_FORM_MARKERS)
```

#### 3. 본문 파싱 직후 분기 추가

`_extract_text(html)` 후 `_is_financial_form(text)` True 시:
- `filing_count=0`로 처리하여 **NO_FILING** 분류
- `data["report_format"] = "financial_holding_annual"` 메타 추가
- evidence ref 보존 (분석가가 PDF 직접 확인 가능하도록)
- warning에 명확한 안내: "「금융회사의 지배구조에 관한 법률」 제출 보고서, PDF 첨부 형식"

```python
is_financial_form = _is_financial_form(text)
if is_financial_form:
    financial_meta = build_filing_meta(filing_count=0, parsing_failures=0)
    data.update(financial_meta)
    data["report_format"] = "financial_holding_annual"
    data["report_meta"] = {...}
    return ToolEnvelope(status=NO_FILING, ..., evidence_refs=[...PDF 첨부...])
```

## 검증 결과

### 18 금융권 회사 (audit v2 partial_failure 명시)

전부 **NO_FILING**으로 정확히 분류, `report_format="financial_holding_annual"` 메타 부착.

| ticker | 회사 | before status | after status |
|---|---|---|---|
| 105560 | KB금융 | partial | no_filing |
| 032830 | 삼성생명 | partial | no_filing |
| 055550 | 신한지주 | partial | no_filing |
| 006800 | 미래에셋증권 | partial | no_filing |
| 086790 | 하나금융지주 | partial | no_filing |
| 316140 | 우리금융지주 | partial | no_filing |
| 000810 | 삼성화재 | partial | no_filing |
| 138040 | 메리츠금융지주 | partial | no_filing |
| 024110 | 기업은행 | partial | no_filing |
| 071050 | 한국금융지주 | partial | no_filing |
| 323410 | 카카오뱅크 | partial | no_filing |
| 005830 | DB손해보험 | partial | no_filing |
| 039490 | 키움증권 | partial | no_filing |
| 005940 | NH투자증권 | partial | no_filing |
| 016360 | 삼성증권 | partial | no_filing |
| 029780 | 삼성카드 | partial | no_filing |
| 175330 | JB금융지주 | partial | no_filing |
| 138930 | BNK금융지주 | partial | no_filing |

### Regression 0 검증

#### 일반 KOSPI 비금융 (12개) — 모두 status=exact, metrics=15

| ticker | 회사 | filing_count | parsing_failures | metrics |
|---|---|---:|---:|---:|
| 005930 | 삼성전자 | 3 | 0 | 15 |
| 000660 | SK하이닉스 | 4 | 0 | 15 |
| 005380 | 현대차 | 4 | 0 | 15 |
| 051910 | LG화학 | 4 | 0 | 15 |
| 006400 | 삼성SDI | 4 | 0 | 15 |
| 005490 | POSCO홀딩스 | 4 | 0 | 15 |
| 035420 | NAVER | 4 | 0 | 15 |
| 035720 | 카카오 | 4 | 0 | 15 |
| 033780 | KT&G | 4 | 0 | 15 |
| 068270 | 셀트리온 | 4 | 0 | 15 |
| 017670 | SK텔레콤 | 5 | 0 | 15 |
| 030200 | KT | 4 | 0 | 15 |

#### KOSDAQ 자율공시 (5개) — 모두 NO_FILING 유지

| ticker | 회사 | filing_count | status |
|---|---|---:|---|
| 247540 | 에코프로비엠 | 0 | no_filing |
| 086520 | 에코프로 | 0 | no_filing |
| 328130 | 루닛 | 0 | no_filing |
| 091990 | 셀트리온헬스케어 | 0 | no_filing |
| 357780 | 솔브레인 | 0 | no_filing |

### 전체 universe (KOSPI 100 + KOSDAQ 96 = 196) 재실행 결과

| 분류 | before fix | after fix | 변화 |
|---|---:|---:|---:|
| exact | 94 | **94** | 동일 (regression 0) |
| no_filing | 82 | **100** | +18 (금융권) |
| partial_failure | 18 | **0** | 0건 |
| error | 2 | 2 | 동일 |

**핵심 성과**:
- 진짜 partial_failure **18건 -> 0건** (목표 100% 달성)
- 일반 KOSPI 비금융 회사는 모두 동일하게 exact 유지 (regression 0)
- 사용자/agent는 이제 `data.report_format == "financial_holding_annual"`로
  "이 회사는 다른 서식의 보고서를 제출했음"을 명확히 인식 가능

## 영향 범위

### 4-class 매트릭스 갱신 (corp_gov_report.summary)

| | exact | no_filing | partial_failure | error | exact% | no_filing% | partial% |
|---|---:|---:|---:|---:|---:|---:|---:|
| before | 94 | 82 | 18 | 2 | 48.0% | 41.8% | 9.2% |
| **after** | **94** | **100** | **0** | **2** | **48.0%** | **51.0%** | **0.0%** |

### 11 tool 합계 영향

| | exact | no_filing | partial_failure | error |
|---|---:|---:|---:|---:|
| before | 1,393 | 699 | 33 | 27 |
| **after** | **1,393** | **717** | **15** | **27** |

진짜 partial_failure는 33 -> 15 (54% 감소). 남은 15건은 모두 ownership_structure
(SK하이닉스/현대차/LG전자 등 hyslrSttus 빈 응답).

## 디자인 노트

### 왜 "no_filing"으로 분류하는가

「금융회사의 지배구조에 관한 법률」 제33조에 따른 "지배구조 연차보고서"는
**「자본시장과 금융투자업에 관한 법률」 시행령 제93조의2의 기업지배구조보고서와는 별개의 제도**.
- 본 tool의 의도: 자본시장법상 KOSPI 의무공시인 기업지배구조보고서 (15개 핵심지표 + 세부원칙) 분석
- 금융회사 보고서는 분석 대상이 아님 (다른 법률, 다른 표준, 다른 서식)
- 따라서 "조회 구간에 사건이 없는 정상 케이스" = NO_FILING이 정확한 분류

### Evidence 보존 이유

분석가가 금융회사의 거버넌스를 분석하려면:
- 첨부 PDF 직접 확인 필요 (DART 뷰어 URL 제공)
- `next_actions`에 "DART 뷰어에서 첨부 PDF 직접 확인" 안내
- 추후 PDF 파싱 자동화 (Upstage OCR 또는 OEK PDF parser) 적용 가능성 열림

### 향후 작업

- 금융회사 PDF 첨부 본문 파싱 자동화 (별도 tool: `financial_holding_governance` 신설 검토)
- 또는 corp_gov_report에 `report_format="financial_holding_annual"` 케이스에서
  PDF 첨부를 OEK PDF parser로 시도하는 fallback 추가

## 코드 변경 파일

- `open_proxy_mcp/services/corp_gov_report.py`
  - `_EXCLUDE_REPORT_SUBSTR` 확장 (연차보고서 + 자율공시 + 첨부정정/추가)
  - `_FINANCIAL_FORM_MARKERS` + `_is_financial_form()` 신규
  - `build_corp_gov_report_payload`: 본문 파싱 직후 financial_form 분기 추가

## 관련

[[parsing-audit-2026-04-29-v2]] [[parsing-audit-2026-04-29]] [[corp_gov_report-design]]
