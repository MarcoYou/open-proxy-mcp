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
OpenProxy MCP v2 (11 tools)
├─ company                    # 기업 식별 + 최근 공시 인덱스
│
├─ Data Tools (7)
│  ├─ shareholder_meeting     # 주총 (정기/임시, 안건·후보·보수·정관·결과)
│  ├─ ownership_structure     # 지분 구조 + 자사주 잔고
│  ├─ dividend                # 실지급 배당 사실
│  ├─ treasury_share          # 자기주식 이벤트 (취득·처분·소각·신탁)
│  ├─ proxy_contest           # 위임장·소송·5% 시그널 + 교차 힌트
│  ├─ value_up                # 주주환원 정책·약속
│  └─ evidence                # 인용 정보 제공 (rcept_no → viewer_url)
│
└─ Action Tools (3, phase-2)
   ├─ prepare_vote_brief      # 투표 메모
   ├─ prepare_engagement_case # engagement 메모
   └─ build_campaign_brief    # 캠페인 브리프
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

실지급 배당 **사실** 탭이에요. 미래 정책은 `value_up`으로.

- 배당결정 공시 합산 (alotMatter 비었을 때 fallback)
- DPS
- 배당성향
- 시가배당률 / 배당수익률
- 배당 추이 (**결산배당은 사업연도 = rcept_dt 연도-1 규칙 적용**)
- 특별배당 / 분기배당 패턴

권장 scope:

```text
summary / detail / history / policy_signals
```

### 5. `treasury_share`

자기주식 이벤트 탭이에요. 배당과 함께 주주환원 사실의 다른 축.

- 자사주 취득결정 (`aq_pp`에 "소각" 포함 시 `for_cancelation=True`)
- 자사주 처분결정 (임직원 성과급 지급 등)
- 자사주 소각결정 (별도 공시)
- 신탁계약 체결 / 해지
- 연간 누적 (사업보고서 기반)

권장 scope:

```text
summary / events / acquisition / disposal / cancelation / annual
```

### 6. `proxy_contest`

분쟁·액티비즘 탭이에요. **자동 binary 분류 대신 힌트 제공**.

- 위임장 권유 3-way 분류: `company` / `shareholder` / `retail_activism`
  (컨두잇/헤이홀더/비사이드코리아 whitelist로 일반 주주와 소액주주 플랫폼 구분)
- 소송 / 판결 / 가처분
- 5% 보유 목적 변화
- 교차 힌트: `filer_has_5pct_active_block`, `filer_in_litigation`
- `has_contest_signal` (retail_activism과 회사측 등재자 overlap 제외)
- timeline
- vote math

권장 scope:

```text
summary / fight / litigation / signals / timeline / vote_math
```

### 7. `value_up`

주주환원 **정책·미래 약속** 탭이에요. 배당 실지급은 `dividend`로.

- 기업가치 제고 계획 (카테고리: plan / progress / meta_amendment 자동 분류)
- 재공시 / 이행현황
- 핵심 commitment 문장 (`_COMMITMENT_KEYWORDS` 매칭)
- 자사주 이행 교차참조 (`treasury_cross_ref`): 최근 24개월 취득·소각 현황
- timeline

권장 scope:

```text
summary / plan / commitments / timeline
```

### 8. `evidence`

**인용 정보 제공자**. API 호출 없이 rcept_no 문자열만으로 유도.

- `rcept_dt` (rcept_no 앞 8자리)
- `source_type` (9~10자리 `00`=DART / `80`=KIND)
- `viewer_url` 자동 생성
- `report_nm`은 upstream evidence_refs에 이미 있는 경우만 포함

원문 본문은 `viewer_url`로 DART/KIND 뷰어에서 직접 확인.

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
