---
type: tool
title: value_up
domain: data
scope: [summary, plan, commitments, timeline]
data_source: [DART OpenAPI list.json (I) + document.xml (밸류업 본문 + 키워드 매칭), KIND HTML (기업가치 제고 계획 0184 fallback), treasury_share (cross-ref)]
related_disclosures: [기업가치제고계획, 자기주식취득결정, 자기주식소각결정, 자기주식의무소각-2026신법]
related_concepts: [주주환원, 배당성향]
related_decisions: [DART-KIND-매핑-화이트리스트-2026-04, cross-domain-체이닝]
related_audits: [260429_0912_audit_parsing-200기업-v2-no_filing]
created: 2026-05-01
---

# value_up

## 한 줄 요약
기업가치제고계획(밸류업) 공시 + 핵심 commitment 문장. 주주환원 정책·미래 약속 탭. 자사주 소각 이행 교차참조 포함.

## 사용법
```
value_up(
    company="KB금융",
    scope="commitments",
)
```

자연어 예시:
- "KB금융 밸류업 commitment + 자사주 이행" → `scope="commitments"`
- "하나금융지주 밸류업 본문 발췌" → `scope="plan"`
- "메리츠금융지주 밸류업 공시 timeline" → `scope="timeline"`

## 입력 인자
| 인자 | 타입 | 필수 | 설명 | 기본값 |
|---|---|---|---|---|
| company | str | yes | 회사명 / ticker / corp_code | - |
| scope | str | no | 4종 (아래 참조) | "summary" |
| year | int | no | 사업연도 | 0 |
| start_date / end_date | str | no | YYYYMMDD | "" |
| format | str | no | "md" / "json" | "md" |

scope:
- `summary`: 최신 공시 + 카테고리 분류 + treasury cross-ref (기본)
- `plan`: 원본 계획 본문 발췌 (1800자 제한)
- `commitments`: 핵심 약속 문장 + 24개월 자사주 이행 교차참조
- `timeline`: 공시 이력

## 출력 schema (data dict)
```json
{
  "company_id": "...",
  "availability_status": "...",
  "latest": {"disclosure_date": "...", "report_name": "...",
             "category": "pre_announcement|plan|progress|meta_amendment",
             "plan_title": "...",
             "source_type": "dart_xml|kind_html",
             "rcept_no": "...", "acptno": "..."},
  "latest_plan": {"disclosure_date": "...", "category": "plan",
                  "plan_title": "...",
                  "rcept_no": "...", "note": "..."},
  "latest_status": {"disclosure_date": "...", "category": "progress",
                    "plan_title": "...",
                    "rcept_no": "...", "note": "..."},
  "latest_result": {"disclosure_date": "...", "category": "progress|meta_amendment",
                    "plan_title": "...",
                    "implementation_sections": [{"tag": "implementation_result", "text": "..."}]},
  "meta_amendment": {"disclosure_date": "...", "category": "meta_amendment",
                     "plan_title": "...", "note": "..."},
  "implementation_sections": [
    {"tag": "implementation_status", "label": "이행현황", "text": "..."},
    {"tag": "implementation_outlook", "label": "이행전망", "text": "..."},
    {"tag": "implementation_result", "label": "이행결과", "text": "..."},
    {"tag": "future_plan", "label": "향후계획", "text": "..."}
  ],
  "embedded_results": [
    {"tag": "implementation_result", "text": "고배당기업 재공시 안에 업데이트된 이행결과..."}
  ],
  "items": [...],
  "highlights": [...],
  "latest_excerpt": "...",
  "treasury_cross_ref": {"cancelation_decision_count_24m": N,
                         "acquisition_count_24m": N,
                         "acquisition_for_cancelation_count_24m": N,
                         "acquisition_for_cancelation_amount_krw_24m": ...,
                         "trust_contract_count_24m": N},
  "search_diagnostics": {...},
  "no_filing": false,
  "filing_count": N,
  "usage": {"dart_api_calls": N, "mcp_tool_calls": 1}
}
```

핵심 필드:
- 공시 카테고리 자동 분류:
  - `pre_announcement`: 계획 수립/공시 예정 안내
  - `plan`: 원본 계획 또는 개정 계획
  - `progress`: 이행현황
  - `meta_amendment`: 고배당기업 형식 재공시 (실계획은 원본에 있음)
- 최신이 `meta_amendment`면 실계획 본문을 `latest_plan`으로 별도 노출.
- `latest_plan`: 가장 최신 본계획/개정계획. "무엇을 하겠다는 계획인지" 확인하는 기준 문서.
- `latest_status`: 가장 최신 이행현황/이행내역. "지금 어디까지 했는지" 확인하는 기준 문서. 없으면 `null`.
- `latest_result`: 명시적 `이행결과`가 발견될 때만 별도 노출. 없으면 `null`.
- `meta_amendment`: 고배당기업 표시 등 형식 재공시. 본계획이나 최신 이행현황을 대체하지 않는다.
- `plan_title`: 본문 `1. 계획서 명칭` 값. `report_nm`보다 문서 성격을 더 잘 드러내는 경우가 있어 category 보정에 사용한다.
- `implementation_sections`: `2. 주요 내용` 내부의 이행현황/이행내역/이행전망/이행결과/향후계획 태그.
- `embedded_results`: 고배당기업 재공시처럼 meta 공시 안에 업데이트된 이행결과/현황이 들어 있는 경우 별도 노출.
- `treasury_cross_ref`: 24개월 내 자사주 소각/취득/신탁 카운트 (commitment 이행 검증)

## Data sources
- **DART API**: `list.json` (pblntf_ty=I) + 키워드 "기업가치 제고" → 없으면 KIND `기업가치 제고 계획(0184)` 재시도
- **KIND**: 밸류업 카테고리 추가 source
- **treasury_share**: 24개월 cross-ref (별도 호출)
- 외부 호출: 2-4회 (commitments scope는 treasury cross-ref 추가)

## Flow

```mermaid
sequenceDiagram
    participant U as User
    participant T as value_up
    participant R as resolve_company_query
    participant DL as DART list.json (I)
    participant DX as DART document.xml
    participant K as KIND 0184 (밸류업 카테고리)
    participant TS as treasury_share API
    U->>T: company="KB금융", scope="commitments"
    T->>R: company_query → corp_code
    T->>DL: list.json (pblntf_ty=I, "기업가치 제고" keyword, requested window)
    DL-->>T: items (밸류업 공시 후보)
    alt items 0건
        T->>K: KIND 0184 카테고리 검색 (fallback)
    end
    alt 둘 다 0건
        par 진단 검색 병렬 (2-year window)
            T->>DL: DART 진단 (target_year-2 ~ target_year)
        and
            T->>K: KIND 진단 (같은 window)
        end
    end
    loop plan/status/meta 판별에 필요한 후보 본문
        T->>DX: document.xml (계획서 명칭 + 주요 내용 태깅)
    end
    T->>T: category 분류 (pre_announcement/plan/progress/meta_amendment)
    T->>T: latest_plan + latest_status + nullable latest_result 구성
    opt scope in {summary, commitments}
        T->>TS: treasury_share (24개월 acquire/cancelation cross-ref)
    end
    T-->>U: ToolEnvelope (latest + highlights + treasury_cross_ref)
```

호출 횟수: 기본 DART list + 최신 본문. plan/status가 별도 공시면 필요한 후보 본문을 추가 조회한다.
`latest_result`는 별도 검색을 강제하지 않고, 이미 읽은 status/meta 본문 안에서 명시적 `이행결과`가 발견될 때만 채운다.
요청 구간에 고배당 재공시 등 meta만 있어 plan/status가 비면, 명시적 start/end 요청이 아닌 한
최근 2년 role backfill 검색으로 본계획과 최신 이행현황을 보강한다.
KIND fallback +1, 진단검색 +2, role backfill +1, treasury cross-ref +1.

## 파싱 전략
- DART 거래소 공시(I) 밸류업 키워드 검색.
- 카테고리 자동 분류:
  - `pre_announcement`: report_name에 "기업가치제고계획예고" 포함
  - `meta_amendment`: report_name에 "고배당기업"/"고배당법인" 포함 (조세특례제한법 형식 재공시)
  - `progress`: report_name 또는 본문 `계획서 명칭`에 실제 관측된 progress 표현 포함
    (`이행현황`, `이행 현황`, `이행결과`, `진행 현황`)
  - `plan`: 그 외
- `계획서 명칭` 우선 단서: KT&G처럼 report_name은 일반 `기업가치제고계획(자율공시)`이나 본문 명칭이 `2025년 KT&G 기업가치 제고계획 이행현황`인 경우 `progress`로 본다.
- `주요 내용` 태깅:
  - `implementation_status`: 실제 관측된 현황/내역 표현
    (`이행현황`, `이행 현황`, `이행내역`, `이행 내역`, `진행 현황`), 실적 비교, 전년 대비 개선 등.
  - `implementation_outlook`: 이행전망, 예상, 추진/예정 수치.
  - `implementation_result`: 명시적 `이행결과`.
  - `future_plan`: 배분원칙 upgrade, 중장기 계획.
  - `meta_reference`: 고배당기업 표시, 기존 공시 참조, 재공시 안내.
- 사용자에게 기업가치제고계획을 설명할 때의 기본 패키지는 `latest_plan` + `latest_status`다.
  Plain language: `latest_plan`은 "무엇을 하겠다는 계획", `latest_status`는 "지금 어디까지 했는지"다.
- `latest_result`는 억지로 찾는 필드가 아니라, status/meta 본문에서 명시적 `이행결과`가 발견되면 따로 올리는 nullable 필드다.
- `meta_amendment`는 최신 공시일 수 있어도 `latest_status` 대체물로 쓰지 않는다.
  - progress와 거의 동일하면 중복 본문을 반복하지 않고 고배당기업 표시/재공시 사실만 표시한다.
  - meta 안에 `implementation_result`가 있으면 `embedded_results`로 progress 옆에 병합 노출한다.
- `_COMMITMENT_KEYWORDS` 매칭 문장 추출 (highlights).
- summary/commitments scope에 24개월 자사주 이벤트 교차참조 (`treasury_cross_ref`).
- 알려진 한계:
  - 본문 텍스트 짧음 (PDF 첨부 중심) — viewer_url로 직접 확인 권장.
  - `_COMMITMENT_KEYWORDS` 매칭 0건이면 highlights 비어있음 (키워드 튜닝 여지).
- regression 0 검증: 200기업 audit `value_up.summary` 50.5% exact (99/196), no_filing 48.0% (94건, 미제출 정상).

## 관련 공시 (rules/disclosures/)
- [[기업가치제고계획]] — DART+KIND, 자율/수시, 본계획·이행점검
- [[자기주식취득결정]] — cross-ref (24개월 acquire 카운트)
- [[자기주식소각결정]] — cross-ref (24개월 retire 카운트)
- [[자기주식의무소각-2026신법]] — 1년 내 의무소각 (commitment 검증 trigger)

## 관련 개념 (rules/concepts/)
- [[주주환원]] — value_up은 정책·약속, dividend는 사실 (역할 분리)
- [[배당성향]] — 밸류업 commitment 핵심 지표

## 관련 결정 (decisions/)
- [[DART-KIND-매핑-화이트리스트-2026-04]] — KIND 밸류업 카테고리 0184 fallback
- [[cross-domain-체이닝]] — VUP → DIV (사실) / TRS (자사주 이행) 체이닝

## 관련 audit/fix (architecture/)
- [[260429_0912_audit_parsing-200기업-v2-no_filing]] — value_up.summary 50.5% exact
- [[260530_audit_value-up-implementation-tags]] — KOSPI500 + KOSDAQ150, 계획서 명칭/주요 내용 이행 태그 전수조사

## 알려진 issue + TODO
- `_COMMITMENT_KEYWORDS` 튜닝 (LG에너지솔루션 등 매칭 0건 케이스).
- 재공시/기재정정 timeline 연결 케이스 → `requires_review`.
- KIND 제목 검증 실패 시 `requires_review`.
- ROE/PBR/배당성향 목표 자동 추출 (TODO, 현재는 highlights 문장만).

## 변경 이력
- 2026-04-18: value_up tool 검증 + release_v2 go
- 2026-04-19: 4개 기업 (KB금융 / 하나금융지주 / LG에너지솔루션 / 메리츠금융지주) summary 통과
- 2026-04-29: 200기업 audit 50.5% exact (no_filing 48% 분리)
- 2026-05-01: tool wiki 페이지 작성
