# Codex Suggested Architecture

작성일: 2026-04-17  
대상 레포: `open-proxy-mcp`  
우선순위: `정확도 > coverage > 편의성`

## 1. 문서 목적

이 문서는 현재 `open-proxy-mcp` 구조를 기준으로, Claude용 원격 MCP 환경에서 더 높은 정확도를 확보하기 위한 아키텍처 재설계안을 정리한 문서다.

핵심 문제의식은 단순하다.

- MCP는 tool을 노출할 수는 있지만, 모델의 실행 순서를 강하게 통제하지는 못한다.
- tool 수가 많고 자유도가 높을수록 모델이 "그럴듯하지만 틀린" 경로를 타기 쉬워진다.
- 현재 구조는 기능 범위는 넓지만, 오류가 `error`가 아니라 `빈 결과`, `raw fallback`, `암묵적 자동선택`으로 흡수되는 구간이 있다.

따라서 이 문서는 다음 질문에 답하도록 설계한다.

1. Claude가 현재 MCP를 읽을 때 왜 원하는 방식으로 잘 안 움직이는가
2. 정확도를 높이려면 MCP 표면을 어떻게 줄이고 강제해야 하는가
3. 내부 파이프라인은 어떤 레이어와 상태 모델로 나눠야 하는가
4. 현재 코드베이스를 어떤 순서로 옮기면 되는가

## 2. 현재 구조의 장점과 한계

### 장점

- DART, KIND, Naver를 이미 조합하고 있다.
- AGM, OWN, DIV, PRX, GOV로 도메인 분리가 비교적 명확하다.
- PDF/OCR/LLM fallback 경로가 이미 존재한다.
- `corp_identifier`, `agm_search`, `ownership_full_analysis`, `governance_report` 등 상위 추상화가 이미 있다.

### 핵심 한계

#### 2.1 모델 제어가 프롬프트 의존적이다

현재 `tool_guide`와 각 tool description에는 "corp_identifier를 먼저 실행", "오케스트레이터로 충분하면 detail tool 추가 호출 금지" 같은 규칙이 있다.  
하지만 이것은 실행 규칙이 아니라 설명 텍스트다.

즉:

- 모델이 읽으면 도움은 된다.
- 모델이 안 읽거나 건너뛰면 강제할 수 없다.
- 길고 복잡한 가이드는 실제 호출 시점에 잘 반영되지 않을 수 있다.

결론적으로, 현재 구조는 `잘 작동하면 좋지만, 틀려도 막을 수 없는` 형태다.

#### 2.2 회사 식별이 fail-closed가 아니다

현재 흐름은 `query -> lookup_corp_code -> 첫 결과 채택`에 가깝다.  
이 방식은 coverage는 높지만 정확도 우선 구조는 아니다.

문제:

- 부분 매치로 엉뚱한 회사가 선택될 수 있다.
- 동명/유사명/별칭에서 자동 선택이 일어난다.
- 이후 분석 전체가 잘못된 엔티티 기준으로 진행될 수 있다.

이건 가장 위험한 오류다.  
왜냐하면 시스템이 실패하지 않고, 오히려 완성도 높은 오답을 만들기 때문이다.

#### 2.3 문자열 JSON 체이닝이 많다

상위 orchestrator가 하위 tool을 호출하고, JSON 문자열을 다시 `json.loads()`해서 조합하는 패턴이 많다.

문제:

- 하위 tool이 에러 문자열을 반환해도 상위 레이어가 빈 값처럼 처리할 수 있다.
- 타입 안정성이 없다.
- `error`, `empty`, `partial`, `raw`가 같은 문자열 채널을 공유한다.
- 내부 조합 과정에서 근거와 상태 정보가 쉽게 유실된다.

#### 2.4 fallback이 결과 승격 구조가 아니다

현재 XML -> PDF -> OCR -> LLM fallback은 존재하지만, "어떤 결과가 더 신뢰할 만한가"를 레이어가 명확히 판정하지 않는다.

문제:

- 보조 수단이 주 소스처럼 취급될 수 있다.
- 이미지 기반 문서도 일단 텍스트로 처리한 뒤 조용히 계속 진행한다.
- LLM 결과가 evidence가 없는 상태로 최종 결과에 섞일 수 있다.

#### 2.5 상태 모델이 너무 약하다

현재는 주로 아래 셋으로 귀결된다.

- 정상처럼 보이는 문자열 결과
- `[오류] ...`
- `데이터가 없습니다`

하지만 정확도 중심 시스템이라면 최소한 아래 상태를 구분해야 한다.

- `exact`
- `ambiguous`
- `partial`
- `source_missing`
- `conflict`
- `requires_review`
- `error`

#### 2.6 field-level evidence가 없다

최종 숫자, 안건명, 찬성률, 지분율, 후보자 경력 등이 어디에서 왔는지 필드 단위로 추적하기 어렵다.

이 상태에서는:

- 사람이 검토하기 어렵고
- 모델의 자기검증도 어렵고
- 회귀 테스트도 약해진다

## 3. 설계 목표

### 3.1 최우선 목표

- 모르면 멈춘다
- 애매하면 물어보거나 `ambiguous`를 반환한다
- 상충하면 `conflict`를 반환한다
- 증거가 약하면 `requires_review`를 반환한다
- 자동 보정은 하되, 보정 사실을 숨기지 않는다

### 3.2 부차적 목표

- Claude가 tool surface를 읽었을 때 최대한 올바른 경로를 타게 한다
- 상위 tool 수를 줄여 planner 부담을 낮춘다
- 도메인별 상세 drill-down은 유지한다
- 현재 코드 자산을 최대한 재사용한다

### 3.3 비목표

- 모든 공시를 무조건 자동 완전 분석
- zero-shot LLM만으로 모든 구조를 복원
- 한 번의 tool call에서 모든 예외를 숨겨서 매끄러운 문장으로 덮기

## 4. 핵심 설계 원칙

### 원칙 1. 인터페이스로 강제하고, 설명으로는 보조한다

MCP에서 가장 잘 먹히는 제어 수단은 긴 가이드가 아니다.

강한 제어 순서:

1. 파라미터 제약
2. opaque handle
3. 적은 수의 상위 tool
4. 명시적 상태 코드
5. 설명 텍스트

즉, "corp_identifier를 먼저 써라"라고 쓰는 것보다, 다른 tool이 `entity_id` 없이는 동작하지 않게 만드는 편이 훨씬 낫다.

### 원칙 2. 내부는 typed, 외부만 text/json

내부 서비스는 반드시 typed object를 주고받아야 한다.

예:

- `EntityResolutionResult`
- `SourceBundle`
- `ExtractionResult[T]`
- `EvidenceRef`
- `AnalysisEnvelope[T]`

MCP tool은 마지막 직전에만 문자열 또는 JSON으로 직렬화한다.

### 원칙 3. source precedence를 코드로 고정한다

권장 우선순위:

1. DART 공식 API 구조화 데이터
2. DART 원문 XML
3. DART PDF
4. OCR 결과
5. KIND
6. 보조 스크래핑
7. LLM inference

여기서 중요한 점은 LLM이 "결과 생성기"가 아니라 "보조 추출기 또는 정리기"여야 한다는 것이다.

### 원칙 4. 모든 중요한 값에 evidence를 붙인다

예:

- 안건 제목
- 후보자 이름
- 지분율
- 배당금
- 찬성률
- 감사의견

각 필드는 아래 중 일부를 반드시 가져야 한다.

- `source_type`
- `rcept_no`
- `section`
- `page`
- `span`
- `snippet`
- `parser`
- `confidence`

### 원칙 5. 정확도 우선이면 성공보다 중단이 낫다

현재처럼 "아무튼 문장으로 만들어준다"가 아니라,

- 충분한 근거가 없으면 `requires_review`
- 엔티티가 애매하면 `ambiguous`
- 데이터가 비면 `source_missing`
- 서로 다르면 `conflict`

로 올려야 한다.

## 5. 권장 MCP 표면 설계

### 5.1 왜 tool 수를 줄여야 하는가

현재처럼 36개 tool이 모두 외부에 보이는 구조는 아래 문제가 있다.

- planner가 canonical chain을 자주 어긴다
- detail tool을 먼저 호출할 수 있다
- 회사 식별을 건너뛸 수 있다
- 비슷한 tool들 사이에서 선택 오류가 늘어난다

따라서 외부 공개 MCP surface는 줄이고, 기존 tool 상당수는 internal service로 내리는 것이 맞다.

### 5.2 권장 공개 tool 세트

#### 필수 공개 tool

1. `resolve_company(query)`
2. `analyze_governance(entity_id, year?, mode?)`
3. `analyze_agm(entity_id, year?, scope?)`
4. `analyze_ownership(entity_id, as_of?)`
5. `analyze_dividend(entity_id, years?)`
6. `analyze_proxy(entity_id, year?)`
7. `get_analysis_item(analysis_id, item_id)`
8. `get_evidence(evidence_id | item_id)`

#### 선택 공개 tool

9. `list_recent_filings(entity_id, category?, start_date?, end_date?)`
10. `explain_status(status_code)`

### 5.3 비공개 internal service로 내릴 대상

- `agm_*_xml`
- `ownership_major`
- `ownership_total`
- `ownership_treasury`
- `ownership_block`
- `div_detail`
- `div_history`
- `proxy_direction`
- `proxy_detail`
- `agm_parse_fallback`

이들은 모델이 직접 호출할 대상이 아니라, 서버 내부 파이프라인 단계로 다뤄야 한다.

## 6. 권장 실행 모델

### 6.1 Stage 1: Entity Resolution

입력:

- 자유 텍스트 query

출력:

- `EntityResolutionResult`

예시:

```json
{
  "status": "exact",
  "entity_id": "ent_005930",
  "canonical_name": "삼성전자",
  "stock_code": "005930",
  "corp_code": "00126380",
  "alternatives": []
}
```

또는:

```json
{
  "status": "ambiguous",
  "entity_id": null,
  "canonical_name": null,
  "alternatives": [
    {
      "candidate_id": "cand_1",
      "corp_name": "OOO",
      "stock_code": "123456",
      "corp_code": "00123456"
    },
    {
      "candidate_id": "cand_2",
      "corp_name": "OOO홀딩스",
      "stock_code": "654321",
      "corp_code": "00987654"
    }
  ]
}
```

중요:

- `partial match 자동 채택 금지`
- `exact`가 아니면 다음 단계 tool 호출 금지

### 6.2 Stage 2: Source Acquisition

입력:

- `entity_id`
- 도메인
- 기간

출력:

- `SourceBundle`

예시:

```json
{
  "status": "exact",
  "entity_id": "ent_005930",
  "domain": "agm",
  "sources": [
    {
      "source_id": "src_1",
      "source_type": "dart_list",
      "rcept_no": "20260301001234",
      "quality": "official",
      "fetched_at": "2026-04-17T10:00:00+09:00"
    },
    {
      "source_id": "src_2",
      "source_type": "dart_document_xml",
      "rcept_no": "20260301001234",
      "quality": "official_text",
      "is_image_based": false
    }
  ]
}
```

중요:

- 원문 확보 실패와 데이터 없음은 구분
- 이미지 기반 여부를 acquisition 단계에서 표시

### 6.3 Stage 3: Extraction

입력:

- `SourceBundle`

출력:

- `ExtractionResult[T]`

예시:

```json
{
  "status": "partial",
  "parser_used": ["xml_regex", "pdf_parser"],
  "warnings": [
    "agenda_2_title_conflict",
    "candidate_3_career_truncated"
  ],
  "data": {
    "agendas": [
      {
        "item_id": "agm_1",
        "number": "제1호",
        "title": "재무제표 승인의 건",
        "evidence": [
          {
            "evidence_id": "ev_001",
            "source_type": "dart_document_xml",
            "rcept_no": "20260301001234",
            "section": "회의목적사항",
            "snippet": "제1호 의안: 재무제표 승인의 건"
          }
        ]
      }
    ]
  }
}
```

### 6.4 Stage 4: Reconciliation

여기가 정확도의 핵심이다.

역할:

- XML 결과와 PDF 결과 비교
- OCR 결과와 XML 결과 비교
- LLM 결과는 보조 후보로만 사용
- 충돌 시 최종 승격 규칙 적용

결과:

- `exact`: 충돌 없음
- `partial`: 일부 필드 누락
- `conflict`: 필드 충돌
- `requires_review`: image-heavy 또는 low-confidence

### 6.5 Stage 5: Presentation

이 단계에서만 MCP 응답을 만든다.

출력 형식:

- 짧은 Markdown 요약
- structured JSON
- 상태 코드
- 필요한 경우 evidence drill-down 안내

중요:

- presentation 단계는 데이터를 바꾸면 안 된다
- 요약은 하되, extractor 결과를 재해석해서 덮어쓰면 안 된다

## 7. LLM harnessing에 대한 현실적 결론

### 7.1 무엇을 할 수 없는가

원격 Claude MCP에서 일반적으로 아래는 강하게 보장하기 어렵다.

- 모델이 특정 tool을 반드시 먼저 읽는 것
- tool description을 항상 충실히 따르는 것
- 긴 canonical chain을 매번 그대로 재현하는 것
- detail tool 직접 호출을 완전히 막는 것

즉, "설명을 잘 써서 행동을 유도"하는 방식에는 한계가 있다.

### 7.2 무엇을 할 수 있는가

대신 아래는 꽤 잘 통제할 수 있다.

#### A. 단계 토큰 강제

다른 모든 tool이 `entity_id`를 요구하게 한다.  
`ticker` 자유입력을 없애면, 모델은 회사를 먼저 resolve하지 않고는 आगे 갈 수 없다.

#### B. opaque handle 사용

예:

- `entity_id`
- `analysis_id`
- `item_id`
- `evidence_id`

이 handle들이 있어야만 다음 단계 호출이 가능하게 하면 planner 자유도가 줄어든다.

#### C. 공개 tool 수 축소

모델에게는 적은 수의 목적 지향 tool만 보여준다.  
세부 파서는 내부 함수로만 둔다.

#### D. 상태 코드를 강제

분석 결과에 항상 `status`가 들어가게 하고,

- `ambiguous`
- `conflict`
- `requires_review`

중 하나면 요약문보다 상태가 먼저 보이게 한다.

#### E. evidence drill-down 분리

분석 tool과 근거 tool을 분리하면 모델도 "요약 -> 근거 확인" 체인을 더 쉽게 탄다.

## 8. 권장 상태 모델

모든 공개 analysis tool은 아래 공통 envelope를 가져야 한다.

```json
{
  "status": "exact | ambiguous | partial | source_missing | conflict | requires_review | error",
  "entity_id": "ent_...",
  "analysis_id": "ana_...",
  "domain": "agm | own | div | prx | gov",
  "summary": "...",
  "warnings": [],
  "data": {},
  "items": [],
  "evidence_index": [],
  "next_actions": []
}
```

### 상태 정의

#### `exact`

- 엔티티가 확정되었고
- 주요 필드 충돌이 없고
- 근거가 충분함

#### `ambiguous`

- 엔티티가 하나로 좁혀지지 않음
- 분석 진행 금지

#### `partial`

- 분석은 가능하지만 일부 필드가 빠짐

#### `source_missing`

- 공식 소스가 없거나 아직 공시가 없음

#### `conflict`

- 소스 간 값 충돌
- 자동 해소하지 않고 review로 올림

#### `requires_review`

- 이미지 기반
- OCR 의존
- LLM 보조 의존
- 중요 필드 confidence 낮음

#### `error`

- 네트워크
- 파싱 실패
- 내부 예외

## 9. Evidence 모델

정확도 중심 시스템에서는 결과보다 근거 구조가 더 중요하다.

권장 모델:

```json
{
  "evidence_id": "ev_001",
  "field_name": "agenda.title",
  "source_type": "dart_document_xml",
  "source_id": "src_2",
  "rcept_no": "20260301001234",
  "section": "회의목적사항",
  "page": null,
  "span": [1204, 1238],
  "snippet": "제1호 의안: 재무제표 승인의 건",
  "parser": "xml_regex",
  "confidence": 0.98
}
```

### evidence 필수 부착 대상

- entity resolution 결과
- 안건 제목
- 후보자 이름/직함/경력
- 보수한도
- 배당금/DPS
- 지분율
- 찬성률/참석률
- 보유목적

## 10. 도메인별 권장 파이프라인

### 10.1 AGM

권장 체인:

`resolve_company -> analyze_agm -> get_analysis_item -> get_evidence`

내부 처리:

1. 소집공고 탐색
2. 원문 XML 확보
3. 회의목적사항/상세 섹션 추출
4. 안건/재무/인사 파싱
5. 실패 시 PDF parser
6. 필요 시 OCR
7. 그래도 부족하면 LLM 보조 추출
8. reconciliation
9. 상태 판정

중요:

- `use_llm` 같은 자유 옵션은 외부 MCP 표면에서 숨긴다
- LLM 경로는 내부에서만 발동
- LLM 사용 여부는 결과 메타에 노출

### 10.2 OWN

권장 체인:

`resolve_company -> analyze_ownership`

핵심:

- 사업보고서 baseline과 5% 공시를 분리된 source stream으로 유지
- 기준일이 다르면 한 행으로 섞지 말고 날짜를 명시
- 목적(`경영권`, `단순투자`, `일반투자`)은 evidence를 반드시 붙인다

### 10.3 DIV

권장 체인:

`resolve_company -> analyze_dividend`

핵심:

- 최근 배당 상세와 3년 추이는 구조적으로 분리
- 특정 연도 값이 누락되면 0으로 두지 말고 `missing`으로 둔다
- 보통주/우선주 혼합 시 명시적으로 분리

### 10.4 PRX

권장 체인:

`resolve_company -> analyze_proxy`

핵심:

- `proxy fight 없음`과 `관련 공시 탐지 실패`를 구분
- 소송/가처분/대량보유/주총결과는 source lineage를 따로 유지
- fight 감지 여부는 deterministic rule로 판정

### 10.5 GOV

권장 체인:

`resolve_company -> analyze_governance`

핵심:

- GOV는 도메인 종합 요약이므로 하위 도메인들의 상태를 승격해서 가져와야 한다
- 하위 도메인 중 하나라도 `conflict`면 GOV도 `conflict`
- 하위 도메인 중 하나라도 `requires_review`면 GOV도 최소 `requires_review`

## 11. 권장 코드 구조

현재 폴더를 완전히 뒤엎기보다, 점진적으로 아래 구조로 옮기는 것을 권한다.

```text
open_proxy_mcp/
  server.py
  api/
    mcp_tools.py
    presenters.py
  domain/
    models/
      common.py
      entity.py
      source.py
      evidence.py
      agm.py
      own.py
      div.py
      prx.py
      gov.py
    services/
      entity_resolution.py
      source_acquisition.py
      agm_pipeline.py
      ownership_pipeline.py
      dividend_pipeline.py
      proxy_pipeline.py
      governance_pipeline.py
      reconciliation.py
  integrations/
    dart/
      client.py
      acquisition.py
    kind/
      client.py
    naver/
      client.py
    llm/
      extraction_assist.py
  extractors/
    agm/
      xml.py
      pdf.py
      ocr.py
      llm.py
    own/
      parser.py
    div/
      parser.py
    prx/
      parser.py
  repositories/
    entity_cache.py
    source_cache.py
    analysis_store.py
  tests/
    golden/
    fixtures/
    unit/
    integration/
    regression/
```

## 12. 현재 코드와의 매핑

### 유지 가능한 것

- `dart/client.py`
- `tools/parser.py`
- `tools/pdf_parser.py`
- `tools/shareholder.py` 내부 파서 로직 일부
- `tools/ownership.py`, `tools/dividend.py`, `tools/proxy.py`의 데이터 가공 로직 일부

### 역할을 바꿔야 하는 것

#### 현재 `tools/*`

현재:

- MCP 공개 tool
- 내부 orchestration
- data shaping
- error formatting

권장:

- `tools/*`는 거의 thin MCP adapter만 담당
- 실제 orchestration은 `domain/services/*`로 이동

#### 현재 `tool_guide`

현재:

- 모델에게 긴 canonical chain 설명

권장:

- 사람용 문서/도움말로 축소
- 모델 실행 제어를 맡기지 않음

#### 현재 `llm/client.py`

현재:

- fallback 호출 유틸

권장:

- `LLM extraction assistant`로 격하
- structured output only
- source-backed field만 승격 가능

## 13. 권장 API 계약 예시

### 13.1 `resolve_company`

```json
{
  "query": "KT&G"
}
```

응답:

```json
{
  "status": "exact",
  "entity_id": "ent_033780",
  "canonical_name": "케이티앤지",
  "stock_code": "033780",
  "corp_code": "00106641",
  "alternatives": []
}
```

### 13.2 `analyze_agm`

```json
{
  "entity_id": "ent_033780",
  "year": 2026,
  "scope": "summary"
}
```

응답:

```json
{
  "status": "requires_review",
  "analysis_id": "agm_2026_ent_033780",
  "summary": "안건 추출은 완료되었으나 일부 후보자 경력은 PDF/OCR 보강이 필요합니다.",
  "warnings": [
    "image_heavy_notice",
    "personnel_partial"
  ],
  "items": [
    {
      "item_id": "agm_item_1",
      "number": "제1호",
      "title": "재무제표 승인의 건",
      "status": "exact"
    },
    {
      "item_id": "agm_item_2",
      "number": "제2호",
      "title": "이사 선임의 건",
      "status": "partial"
    }
  ],
  "next_actions": [
    "get_analysis_item(agm_item_2)",
    "get_evidence(agm_item_2)"
  ]
}
```

### 13.3 `get_evidence`

```json
{
  "item_id": "agm_item_2"
}
```

응답:

```json
{
  "status": "exact",
  "item_id": "agm_item_2",
  "evidence": [
    {
      "evidence_id": "ev_111",
      "source_type": "dart_document_xml",
      "rcept_no": "20260301001234",
      "section": "주주총회 목적사항별 기재사항",
      "snippet": "사외이사 후보자 홍길동 ..."
    },
    {
      "evidence_id": "ev_112",
      "source_type": "dart_pdf_ocr",
      "rcept_no": "20260301001234",
      "page": 14,
      "snippet": "세부경력 ..."
    }
  ]
}
```

## 14. LLM 사용 원칙

### 14.1 허용 역할

- 표 또는 구문 파손이 심한 원문에서 보조 구조화
- 긴 원문을 evidence-linked summary로 정리
- conflict explanation 생성

### 14.2 금지 역할

- 엔티티 자동 확정
- evidence 없는 숫자 생성
- source precedence 덮어쓰기
- conflict 자동 무시
- `partial`을 `exact`처럼 승격

### 14.3 사용 규칙

- structured output only
- 입력 길이 제한
- 입력 source id 포함
- 출력은 provisional 상태로 수신
- deterministic validator 통과 후에만 일부 필드 승격

## 15. 에러 처리 전략

### 15.1 문자열 에러 대신 구조화 에러

현재처럼 `[오류] ...` 한 줄 반환 대신 아래 형태를 권장한다.

```json
{
  "status": "error",
  "error_code": "DART_RATE_LIMIT",
  "message": "DART API 호출 한도를 초과했습니다.",
  "retryable": true,
  "context": {
    "entity_id": "ent_033780",
    "domain": "dividend"
  }
}
```

### 15.2 `no data`와 `error` 분리

예:

- `source_missing`: 해당 연도 공시 없음
- `error`: 네트워크 실패
- `partial`: 일부 공시는 있으나 핵심 필드 누락

이 셋은 절대 같은 문자열로 표현되면 안 된다.

## 16. 테스트 전략

정확도 우선이면 테스트 전략이 코드보다 중요하다.

### 16.1 Golden corpus 구축

도메인별 최소 10~20건, 총 50~100건의 hard case를 고정한다.

권장 포함 케이스:

- 동명기업
- 별칭/영문명
- 정정공고
- 이미지 기반 공고
- 하위 안건 분할이 많은 정관변경
- 감사위원/사외이사 혼합 선임
- 자사주/재단/경영권 방어 목적 혼동
- 배당 공시 누락/중간배당
- 프록시 파이트 있으나 명시 표현이 약한 경우

### 16.2 assertion 기반 회귀

현재 print 중심 테스트 대신 아래를 체크해야 한다.

- entity resolution status
- agenda 개수
- agenda title exact match
- candidate count
- compensation limit parse
- ownership top holder
- dividend DPS
- proxy fight detection boolean
- analysis status

### 16.3 parser-by-parser unit test

- regex extractor
- XML section locator
- PDF table parser
- OCR post-parser
- reconciliation rules

### 16.4 snapshot test는 보조

Markdown 전체 snapshot은 부차적이다.  
핵심은 field-level structured assertions다.

## 17. 마이그레이션 단계 제안

### Phase 1. 안전장치 추가

목표:

- 큰 구조 변경 없이 정확도 위험부터 낮춘다

할 일:

- `resolve_ticker` 자동 선택 제거
- `ambiguous` 상태 도입
- image-heavy filing이면 warning이 아니라 `requires_review`
- 상위 tool의 JSON parsing failure를 빈 값으로 삼키지 않기

### Phase 2. structured envelope 도입

목표:

- 공개 tool 응답에 `status`, `warnings`, `evidence_index` 추가

할 일:

- 공통 response model 추가
- `tool_error`, `tool_empty`를 structured response로 교체
- Markdown 출력과 JSON 출력 모두 같은 내부 모델 기반으로 생성

### Phase 3. internal service 분리

목표:

- `tools/*`에서 orchestration 제거

할 일:

- `entity_resolution_service`
- `agm_pipeline_service`
- `ownership_pipeline_service`
- `dividend_pipeline_service`
- `proxy_pipeline_service`

### Phase 4. external tool surface 축소

목표:

- Claude에 노출하는 tool 수를 36개에서 6~10개 수준으로 감소

할 일:

- public MCP adapter 신설
- 기존 detail tools는 internal only 전환
- `entity_id`, `analysis_id`, `item_id` 기반 체인 도입

### Phase 5. evidence-first deepening

목표:

- 최종 결과의 감사 가능성 확보

할 일:

- field-level evidence 저장
- evidence retrieval tool 추가
- reconciliation confidence 모델 추가

## 18. 내가 실제로 채택할 최소 실행안

시간과 리스크를 고려해서, 내가 이 레포를 바로 손댄다면 아래 순서로 간다.

### Step 1

- `resolve_ticker`를 fail-closed로 변경
- `corp_identifier` 결과가 ambiguous면 다른 tool 진행 중단

### Step 2

- `analysis envelope` 공통 타입 추가
- 최소한 모든 상위 tool에 `status`, `warnings`, `provenance_summary` 추가

### Step 3

- `governance_report`, `ownership_full_analysis`, `div_full_analysis`, `proxy_full_analysis`에서 문자열 JSON 재조합 제거

### Step 4

- image-heavy / OCR-used / LLM-used를 명시적으로 노출

### Step 5

- Claude 공개 tool surface 축소

이 다섯 단계만 해도 정확도와 신뢰도는 지금보다 크게 올라간다.

## 19. 최종 결론

정확도 우선 MCP에서 중요한 것은 "도구가 많다"가 아니다.  
"모델이 잘못 써도 크게 망가지지 않는 surface"가 중요하다.

현재 `open-proxy-mcp`는 도메인 지식과 파서 자산은 충분히 좋다.  
문제는 그것이 `모델 친화적`이기보다 `모델 자유도에 많이 의존하는 형태`라는 점이다.

따라서 권장 방향은 다음 한 줄로 요약된다.

`36개 detail tool을 그대로 잘 설명하는 시스템`이 아니라,  
`적은 수의 강한 tool과 typed evidence pipeline으로 감싼 시스템`으로 바꾸는 것이 맞다.

정확도 관점에서 가장 중요한 세 가지는 아래다.

1. 엔티티 해석을 fail-closed로 바꿀 것
2. 내부 오케스트레이션을 typed pipeline으로 옮길 것
3. 모든 핵심 필드에 evidence와 상태를 붙일 것

이 세 가지가 들어가면, Claude가 planner를 조금 어긋나게 써도 결과의 신뢰도는 훨씬 높아진다.
