# OpenProxy MCP

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-green.svg)](https://modelcontextprotocol.io/)
[![Branch](https://img.shields.io/badge/branch-release_v2.0.0-blue.svg)](https://github.com/MarcoYou/open-proxy-mcp/tree/release_v2.0.0)

[English README](README_ENG.md)

> 이 README는 `release_v2.0.0` 브랜치 기준 문서예요.  
> 즉, **다음 공개 표면(v2)** 을 설명하는 문서이고, 현재 stable 운영 문서는 [docs/v1/README.md](docs/v1/README.md)에서 따로 봐야 해요.

## v2가 바꾸려는 것

지금까지의 OpenProxy는 `세부 tool이 많은 구조`였어요.  
이 방식은 coverage는 넓었지만, 애널리스트 입장에서는 아래 문제가 있었어요.

- 어느 tool부터 써야 하는지 직관적이지 않음
- 안건, 지분, 배당, 분쟁이 내부 구현 단위로 노출됨
- 결과와 근거를 분리해서 보기 어려움
- DART/KIND/Naver를 어떤 기준으로 쓰는지 한눈에 안 보임

v2는 이걸 **“회사 식별 -> 데이터 탭 -> 근거 확인 -> 결과물 생성”** 구조로 바꾸는 게 목표예요.

## 문서 트랙

- `v1 (현재 stable / 운영 기준)`: [docs/v1/README.md](docs/v1/README.md)
- `v2 (release_v2.0.0 설계 / 다음 공개 표면)`: [docs/v2/README.md](docs/v2/README.md)

## At a Glance

```text
OpenProxy MCP v2
├─ company
│  ├─ company identification
│  ├─ ticker / corp_code / ISIN
│  └─ recent filings index
│
├─ Data Tools
│  ├─ shareholder_meeting
│  ├─ ownership_structure
│  ├─ dividend
│  ├─ proxy_contest
│  ├─ value_up
│  └─ evidence
│
└─ Action Tools
   ├─ prepare_vote_brief
   ├─ prepare_engagement_case
   └─ build_campaign_brief
```

한 줄로 요약하면:

```text
회사 이름으로 시작해서
-> 데이터 탭으로 사실을 보고
-> evidence로 근거를 확인하고
-> 마지막에 action tool로 결과물을 만든다
```

## Public Data Tools

### 1. `company`

모든 시작점이에요.

- 회사명, 영문명, 약칭, ticker로 진입
- 내부적으로 `ticker / corp_code / ISIN` 정리
- 이후 사용할 최근 공시 인덱스를 붙이는 허브 역할

### 2. `shareholder_meeting`

주총 탭이에요.

- 정기주총 / 임시주총
- 안건
- 이사/감사 후보
- 보수한도
- 정관변경
- 주총 결과
- 정정공시

권장 scope:

```text
summary
agenda
board
compensation
aoi_change
results
corrections
evidence
```

### 3. `ownership_structure`

지분 구조 탭이에요.

- 최대주주 / 특수관계인
- 5% 대량보유
- 자사주
- control map
- timeline

권장 scope:

```text
summary
major_holders
blocks
treasury
control_map
timeline
```

### 4. `dividend`

배당 탭이에요.

- 배당결정
- DPS
- 배당성향
- 시가배당률 / 배당수익률
- 배당 추이
- 특별배당 / 분기배당 신호

권장 scope:

```text
summary
detail
history
policy_signals
evidence
```

### 5. `proxy_contest`

분쟁 탭이에요.

- 위임장 권유
- 소송 / 판결 / 신청
- 5% 보유 목적 변화
- 분쟁 시그널
- timeline
- vote math

권장 scope:

```text
summary
fight
litigation
signals
timeline
vote_math
evidence
```

### 6. `value_up`

밸류업 탭이에요.

- 기업가치 제고 계획
- 재공시
- 이행현황
- commitments
- timeline

권장 scope:

```text
summary
plan
commitments
timeline
evidence
```

### 7. `evidence`

근거 탭이에요.

- `rcept_no`
- `source_type`
- `section`
- `snippet`
- `confidence`

즉, “이 말이 어디 공시에서 나왔는가”를 바로 확인하는 용도예요.

## Action Tools

Action tool은 `바로 쓰는 결과물`이에요.  
다만 v2에서는 **data layer를 먼저 안정화하고**, action tool은 그 위에 얹는 순서로 가는 게 맞다고 보고 있어요.

```text
prepare_vote_brief
prepare_engagement_case
build_campaign_brief
```

정리하면:

- `data tool` = 사실과 원문을 보는 탭
- `action tool` = 그 사실을 바탕으로 바로 쓰는 메모/브리프

## Source Policy

v2의 기본 소스 정책은 아래예요.

1. `DART API`
2. `DART document.xml`
3. `KIND HTML` (`화이트리스트 공시만`)
4. `Naver` 보조
5. `requires_review`

핵심 원칙:

- `DART`가 기본
- `KIND`는 전 공시에 붙이지 않음
- `Naver`는 참고만
- `PDF 다운로드`는 기본 경로에서 제외
- 애매하면 억지로 채우지 말고 `requires_review`

관련 문서:

- [DART-KIND 매핑 화이트리스트](wiki/decisions/DART-KIND-매핑-화이트리스트-2026-04.md)
- [신규 tool 추가 검증 정책](wiki/decisions/tool-추가-검증-정책.md)

## shareholder_meeting는 어떻게 동작하나

예를 들어:

```text
shareholder_meeting(company="삼성전자", meeting_type="annual", scope="summary")
```

이렇게 들어오면 내부적으로는 대략 아래가 트리거돼요.

```text
1. company resolution
2. annual notice search
   └─ pblntf_ty=E / 주주총회소집공고 / 기재정정 포함
3. correction resolver
4. DART XML fetch
5. meeting_info parser
6. agenda parser (상위 안건 위주)
7. evidence refs 생성
```

scope가 바뀌면 필요한 것만 더 열어요.

- `board`
  - `personnel` parser 추가
- `compensation`
  - `compensation` parser 추가
- `aoi_change`
  - `aoi_change` parser 추가
- `results`
  - 결과 공시 검색
  - whitelist check
  - KIND fetch
  - result parser

즉 `summary`가 모든 하위 파서를 다 돌리는 구조가 아니라,  
먼저 주총의 뼈대를 보고 필요할 때 drill-down 하는 구조예요.

## Release Priority

```text
Phase 1
  company
  shareholder_meeting
  ownership_structure
  dividend
  value_up

Phase 1.5
  proxy_contest
  evidence

Phase 2
  prepare_vote_brief
  prepare_engagement_case
  build_campaign_brief
```

이 우선순위를 둔 이유는 간단해요.

- 먼저 `빠르고 정확한 데이터 접근`을 만들고
- 그 다음 `근거 확인`을 안정화하고
- 마지막에 `결과물 생성`을 얹는 쪽이 정확도에 유리하기 때문이에요

## 구현/검증 문서

- [v2 문서 인덱스](docs/v2/README.md)
- [release_v2 tool 아키텍처](wiki/analysis/release_v2-tool-아키텍처.md)
- [release_v2 public tool 검증 매트릭스](wiki/analysis/release_v2-public-tool-검증-매트릭스.md)
- [tool 추가 검증 정책](wiki/decisions/tool-추가-검증-정책.md)
- [tool 추가 검증 템플릿](wiki/templates/tool-추가-검증-템플릿.md)

## 현재 stable을 쓰려면

지금 실제 운영 구조와 현재 연결 방식은 `v1` 문서를 따라가야 해요.

- [v1 문서](docs/v1/README.md)
- [로컬 설치 가이드](docs/connect.md)
- [현재 아키텍처(v1)](docs/ARCHITECTURE.md)

## Disclaimer

OpenProxy는 공시 데이터를 구조화해서 AI가 읽기 쉽게 만드는 도구예요.  
AI는 할루시네이션을 일으킬 수 있고, 부정확한 분석을 낼 수도 있어요.  
특히 v2는 아직 `release_v2.0.0` 브랜치 기준 설계/정리 단계이므로, 최종 투자 판단이나 의결권 행사 판단은 반드시 원문 공시와 전문가 검토를 함께 거쳐야 해요.

## License

[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)  
비상업적 사용만 허용돼요.
