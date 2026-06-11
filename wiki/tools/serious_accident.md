---
type: tool
title: serious_accident
domain: data
scope: [단일 (scope 인자 없음)]
data_source: [DART OpenAPI list.json (pblntf_detail_ty=I001) + 키워드 '중대재해' + document (include_details=True 시 본문 파싱)]
related_disclosures: [공시유형코드체계]
related_concepts: []
related_decisions: [pblntf-ty-필터링]
related_audits: []
created: 2026-06-11
---

# serious_accident

## 한 줄 요약
중대재해 공시 통합 — 중대재해발생(본사·종속/자회사) + 중대재해 관련 (형사)처벌사실확인. 중대재해처벌법(2022) 리스크·ESG 안전(S) 모니터링. **company 미지정 시 시장 전체 최근 30일(최대 90일) 스캔** — 공시가 희소(305사 중 27사)해 "최근 누가 냈나"가 실질 수요. 기본은 list.json 메타, `include_details=True`면 사망·부상자 수/발생일자·장소/재해내용/조치·향후대책 원문 파싱 + 사상자 집계.

## 사용법
```
serious_accident(company="한화오션", include_details=True)   # 회사 24개월 이력
serious_accident()                                            # 시장 전체 최근 30일 스캔
```

자연어 예시:
- "한화오션 중대재해 공시 알려줘" → 타임라인 + 본사/종속회사 구분
- "POSCO 자회사 산재 사망사고 이력" → 자회사 변형 포착 + 사상자 집계
- "최근 중대재해 발생한 기업들 알려줘" → company 미지정 시장 스캔 (회사별 건수 + 타임라인)

## 입력 인자
| 인자 | 타입 | 필수 | 설명 | 기본값 |
|---|---|---|---|---|
| company | str | no | 회사명 / ticker / corp_code. **공백이면 시장 전체 스캔** | "" |
| start_date / end_date | str | no | YYYYMMDD | "" (company 지정 24개월 / 미지정 30일·최대 90일) |
| include_details | bool | no | True면 원문 파싱 + 사상자 집계 (DART 호출 N회 추가) | False |
| details_limit | int | no | 원문 파싱 대상 건수 (1-10) | 5 |
| format | str | no | "md" / "json" | "md" |

## 출력 schema (data dict)
```json
{
  "company_id": "...",
  "event_count": {"total": N, "occurrence": N, "punishment": N,
                  "subsidiary_reports": N, "corrections": N},
  "casualties": {"deaths": N, "injuries": N, "parsed_rows": N},
  "events": [{"event_type": "occurrence|punishment",
              "rcept_dt": "...", "report_nm": "...",
              "subsidiary_report": true, "is_correction": false,
              "details": {"subsidiary_name": "...", "location": "...",
                          "description": "...", "deaths": N, "injuries": N,
                          "accident_date": "...", "labor_ministry_report_date": "...",
                          "response_plan": "..."}}],
  "no_filing": false,
  "usage": {"dart_api_calls": N, "mcp_tool_calls": 1}
}
```

## Data sources
- **DART API**: `list.json` (pblntf_detail_ty=`I001`) + 키워드 `중대재해` / `document` (include_details 시 N건)
- 외부 호출: 기본 1회, include_details=True 시 +N (details_limit 기본 5)

## 파싱 전략
- **검색**: 키워드 `중대재해` 하나로 제목 변형 전부 매칭 (실측 6종):
  `중대재해발생` / `중대재해발생(종속회사의주요경영사항)` / `중대재해발생(자회사의 주요경영사항)` / `[기재정정]` 변형 2종 / **`중대재해관련형사처벌사실확인`** (시장 스캔서 실물 2건 확인 — 화일약품 `20260529902110`, THE CUBE& `20260513900553`; punishment 분류 정상).
- **시장 스캔 모드**: company 공백 시 corp_code 없이 I001 전체 조회 (실측 30일=36페이지·41콜, 90일=162페이지 — page cap 200). 페이지 2+ 병렬. 최근 30일 실측 14건: **지주사+사업회사 이중 공시 패턴**(OCI홀딩스+OCI, DL+DL이앤씨, 한화+한화에어로 — 같은 사고 각자 공시) → 사상자 집계는 (발생일자+장소 정규화) supersede 키로 중복 제거. 장소는 `㈜`/`(주)`·공백 정규화 — 한화(`㈜`) vs 한화에어로(`(주)`) 표기 차이로 사망 5명이 이중 집계되던 실측 버그 교정.
- **I001 좁히기 검증 (2026-06-11)**: ① 고위험 업종 49사 × 3.5년(20230101~20260611)에서
  `I 전체`(33건) vs `I001`(33건) **차집합 0** + 보유 21사 전체유형 풀스캔(삼성전자 35페이지 포함) **I 밖 누수 0**.
  ② 확대 검증 — **KOSPI 100 + KOSDAQ 100 + 건설 20** (220사, 3사는 사명 변경으로 ticker resolve):
  전 회사 차집합 0 · truncation 0 · 신규 제목 변형 0, 총 45건. **KOSPI 21/100사 36건, KOSDAQ 0/100사**
  (중대재해 공시는 사실상 대형주·지주사·건설/조선/중공업 현상 — 한화 6건·한화에어로 4건이 최다),
  건설 6/20사 9건(동부건설·KCC건설·HL D&I 각 2 등).
  ③ 건설 확장 — **중소형 건설·전문건설·설계/CM·플랜트정비 36사** 추가: 차집합 0 · truncation 0 ·
  변형 0 유지. **35사 0건, 유일한 1건은 한전KPS(플랜트 정비)** — 공시는 대형 원청·지주사에 집중되고
  중소형 시공·설계사는 사고가 있어도 공시 단위가 아닌 패턴. 누적 **305사 / 79건 / 차집합 0**.
- **본문 파싱** (발생 공시 정형 필드): 발생 장소 / 재해 내용 / 사망자 수 / 부상자 수 / 중대재해 발생일자 / 고용노동부 보고일자 / 조치사항 및 향후대책. 종속·자회사 변형은 대상 회사명 추가.
- **사상자 집계 supersede**: 같은 사건(발생일자+장소)의 원본·[기재정정]은 최신 공시(rcept_no 최대)가 대체 — 정정이 사상자 수를 바꾸는 경우 원본 기준 집계 오류 방지.
- 알려진 한계:
  - **제도 시점**: 거래소 중대재해 수시공시는 2025-10월부터 관측 (실측 최초 2025-10-29). 이전 구간 무공시 ≠ 무사고 — warning 자동 부착. 과거 이력은 뉴스·고용노동부 자료로 보완.
  - **공시 주체 편중**: 공시가 대형 원청·지주사에 집중 (KOSDAQ 상위 100 보유 0, 중소형 건설 35사 0건). 중소형사·하청의 무공시는 무사고 단정 불가 — 산재 통계는 고용노동부 자료가 정본.
  - 처벌사실확인 공시는 본문 파싱이 보수적 (일반 필드 + 발췌) — 실물 2건 확인됐으나 비정형이라 발췌 위주. 표본 쌓이면 정형 파싱 보강.
  - POSCO홀딩스 자회사 변형 일부에서 대상 회사명 라벨 미추출 (필드 라벨 상이) — 제목·제출인으로 식별 가능.

## 관련 공시 (rules/disclosures/)
- [[공시유형코드체계]] — I001 주요경영사항 (중대재해발생 포함)

## 관련 결정 (decisions/)
- [[pblntf-ty-필터링]] — detail-code 좁히기 + 차집합 0 검증 방법론

## 알려진 issue + TODO
- 처벌사실확인 공시 첫 출현 시 정형 파싱 보강 (TODO).
- 사업보고서 내 안전·보건 관련 기재와 교차참조 (TODO, 우선순위 낮음).

## 변경 이력
- 2026-06-11: tool 신설 (16 → 17번째 tool, Data 11개째). 49사 검증(차집합 0·누수 0) 후 I001 타겟팅. 한화오션/POSCO홀딩스/HD현대중공업/농심 스모크 통과, 정정 supersede 집계.
- 2026-06-11: 확대 검증 — KOSPI 100 + KOSDAQ 100 + 건설 20 (220사). 차집합 0·truncation 0·신규 변형 0, 총 45건. KOSDAQ 상위 100은 보유 0 확인.
- 2026-06-11: **시장 전체 스캔 모드** 추가 (company 공백 시 최근 30일·최대 90일) + 비상장 자회사 → 상장 모회사 조회 안내. 스캔 실측서 처벌확인 공시 실물 2건 첫 확인(화일약품·THE CUBE&), 지주+사업회사 이중 공시 supersede 처리.
