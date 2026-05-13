# OpenProxy MCP Tool Catalog

## Meta
- **Topic**: OpenProxy MCP의 전체 tool 카탈로그, 아키텍처, action workflow
- **Target Audience**: MCP connector 사용자, 내부 협업자, 기술 검토자, 도입 검토자
- **Tone/Mood**: Technical, structured, catalog-like, precise
- **Style**: architectural-blueprint
- **Slide Count**: 23 slides
- **Aspect Ratio**: 16:9

## Slide Composition

### Slide 1 - Cover
- **Type**: Cover
- **Title**: OpenProxy MCP
- **Subtitle**: Tool Catalog, Architecture, and Workflows

### Slide 2 - Why This Exists
- **Type**: Content
- **Key Message**: OpenProxy MCP는 공시 원문 중심 한국 상장사 거버넌스 분석을 바로 호출 가능한 tool surface로 압축한다.

### Slide 3 - System Architecture
- **Type**: Diagram
- **Key Message**: 전체 시스템은 `source -> parser/service -> tool surface -> web client`의 4층 구조다.

### Slide 4 - Catalog Map
- **Type**: Content
- **Key Message**: 발표는 16개 tool을 전부 훑고, action tool만 workflow를 한 장씩 추가 설명한다.

### Slide 5 - company
- **Type**: Tool Card
- **Key Message**: 모든 흐름의 진입점인 회사 식별과 공시 인덱스 tool

### Slide 6 - shareholder_meeting_notice
- **Type**: Tool Card
- **Key Message**: 사전 주총 소집공고를 구조화하는 tool

### Slide 7 - shareholder_meeting_results
- **Type**: Tool Card
- **Key Message**: 사후 주총 의결 결과를 구조화하는 tool

### Slide 8 - ownership_structure
- **Type**: Tool Card
- **Key Message**: 최대주주·5%·control map 중심 지분 구조 tool

### Slide 9 - financial_metrics
- **Type**: Tool Card
- **Key Message**: DART 재무 4 endpoint를 통합한 재무 지표 tool

### Slide 10 - corp_gov_report
- **Type**: Tool Card
- **Key Message**: 기업지배구조보고서 15지표를 정리하는 tool

### Slide 11 - dividend
- **Type**: Tool Card
- **Key Message**: 배당 사실과 breakdown을 정리하는 tool

### Slide 12 - treasury_share
- **Type**: Tool Card
- **Key Message**: 자사주 결정·결과·사이클 매칭을 다루는 tool

### Slide 13 - value_up
- **Type**: Tool Card
- **Key Message**: 밸류업 계획과 자사주 이행을 연결하는 tool

### Slide 14 - corporate_restructuring
- **Type**: Tool Card
- **Key Message**: 합병·분할·주식교환·이전을 묶는 restructuring tool

### Slide 15 - dilutive_issuance
- **Type**: Tool Card
- **Key Message**: 유상증자·CB·BW·감자 등 희석 이벤트 tool

### Slide 16 - proxy_contest
- **Type**: Tool Card
- **Key Message**: 경영권 분쟁, 위임장 경쟁, 소송 신호 tool

### Slide 17 - related_party_transaction
- **Type**: Tool Card
- **Key Message**: 내부거래와 타법인주식 거래를 보는 tool

### Slide 18 - evidence
- **Type**: Tool Card
- **Key Message**: 근거 공시 링크와 traceability를 제공하는 tool

### Slide 19 - proxy_advise_before_meeting
- **Type**: Tool Card
- **Key Message**: 주총 사전 안건별 의결권 권고를 만드는 action tool

### Slide 20 - proxy_advise_before_meeting Workflow
- **Type**: Workflow
- **Key Message**: notice parsing + cross-check + policy/law layer를 거쳐 권고가 조립된다.

### Slide 21 - proxy_result_after_meeting
- **Type**: Tool Card
- **Key Message**: 주총 사후 결과를 요약·집계하는 action tool

### Slide 22 - proxy_result_after_meeting Workflow
- **Type**: Workflow
- **Key Message**: 결과 공시 수집, 결과 정규화, 안건 단위 사후 보고를 만든다.

### Slide 23 - Closing
- **Type**: Closing
- **Message**: OpenProxy MCP는 16개 tool과 2개의 action workflow로 한국 거버넌스 분석을 reusable MCP surface로 만든다.
