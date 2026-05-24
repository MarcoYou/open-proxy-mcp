# OpenProxy MCP

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-green.svg)](https://modelcontextprotocol.io/)
[![Tools](https://img.shields.io/badge/tools-16-orange.svg)](#tool-구조-16개)

[English README](README_ENG.md)

## Why OpenProxy?

코리아 디스카운트의 핵심에는 거버넌스 리스크가 있습니다. 패시브 투자가 늘면서 주식 오너십의 의미가 희미해지는 지금, 이 리스크는 오히려 더 선명해지고 있습니다. 거버넌스 정보에 쉽게 접근하고 빠르게 분석할 수 있어야 하지만, 수백 페이지의 공시 원문을 직접 읽고 판단하기에는 시간도 전문성도 부족합니다.

**OpenProxy는 AI로 이 장벽을 낮춥니다.** DART 공시를 구조화된 데이터로 바꿔서, 지분 구조부터 배당 이력, 주총 안건, 경영권 분쟁까지 거버넌스 분석 전반을 누구나 몇 초 만에 수행할 수 있게 만듭니다.

![OpenProxy MCP 비교](screenshot/open-proxy-mcp%20output%20kor.png)

---

## 빠른 시작

### 0단계: 지원 클라이언트 및 접근 조건 확인 (필수)

OpenProxy MCP는 **원격 MCP 서버**로 배포되어 있어 Claude 웹과 ChatGPT 웹의 custom connector / MCP app 표면에서 연결할 수 있습니다.

- **Claude**: 커스텀 커넥터 사용이 가능한 유료 플랜 필요
- **ChatGPT**: custom connector / MCP app 지원 플랜과 developer mode / workspace 권한이 필요할 수 있습니다

> **참고**:
> - 각 서비스의 플랜, 권한, UI 롤아웃 상태에 따라 실제 연결 메뉴가 보이지 않을 수 있습니다.
> - ChatGPT는 로컬 MCP 서버가 아니라 **원격 MCP 서버** 연결을 전제로 합니다.

### 1단계: DART API 키 발급 (필수)

OpenProxy의 모든 데이터는 DART OpenAPI에서 가져옵니다. **본인의 API 키가 있어야 사용할 수 있습니다.**

1. [DART OpenAPI](https://opendart.fss.or.kr/) 접속 -> 회원가입
2. 인증키 신청 -> 발급 (무료, 바로 발급됩니다)

### 2단계: 연결

API 키를 발급받았다면, 아래 두 가지 방법 중 하나를 선택합니다.

#### 방법 A: Claude 웹 custom connector (설치 없이 30초면 됩니다)

URL 끝에 발급받은 DART API 키를 붙여서 연결합니다. 키는 서버에서만 사용되고, AI에게는 노출되지 않습니다.

**claude.ai 웹:**

1. [claude.ai](https://claude.ai) 접속 -> 설정 -> 커넥터
2. "커스텀 커넥터 추가" 선택
3. 이름: `open-proxy-mcp`, URL 입력:
```
https://open-proxy-mcp.fly.dev/mcp?opendart=발급받은_키
```
4. "추가" 클릭 -> 16개 tool이 자동으로 인식됩니다
5. 추가된 커넥터의 구성 -> 권한에서 **"항상 허용"** 선택 (매번 승인 없이 tool이 자동 실행됩니다)

> **참고**: tool이 추가되거나 변경된 경우 커넥터 MCP 서버 업데이트에 시간이 걸릴 수 있습니다. 커넥터를 삭제한 뒤 다시 연결하면 바로 최신 tool이 반영됩니다. 재연결한 후 새 채팅을 열어서 다시 시도합니다.

#### 방법 B: ChatGPT web custom connector / MCP app (beta)

ChatGPT web에서도 원격 MCP 서버를 custom connector / MCP app으로 연결할 수 있습니다.

1. ChatGPT web 접속
2. developer mode 또는 custom connector 생성 권한 확인
3. `Settings -> Apps & Connectors -> Create`
   또는 `Workspace Settings -> Connectors -> Create`
4. 이름: `open-proxy-mcp`
5. MCP 서버 URL 입력:
```
https://open-proxy-mcp.fly.dev/mcp?opendart=발급받은_키
```
6. 인증 방식 선택
7. 저장 후 새 채팅에서 connector/app 선택

> **참고**:
> - ChatGPT custom connector / MCP app은 계정 플랜, 워크스페이스 권한, 베타 롤아웃 상태에 따라 메뉴가 보이지 않을 수 있습니다.
> - custom connector는 OpenAI가 검증한 기본 커넥터가 아니므로, 조직 사용 시 별도 검토가 필요할 수 있습니다.

### 사용 예시

연결이 끝났다면, 자연어로 질문하면 됩니다.

```
"삼성전자 주주총회 안건 분석해줘"                         # 통합 분석 (proxy_advise)
"KB금융 사외이사 후보 독립성 검토해줘"                    # 후보 평가
"고려아연 경영권 분쟁 분석해줘"                           # 분쟁 시그널
"삼성전자 지분 구조 보여줘"                              # 지분 + control map
"SK하이닉스 배당 추이"                                  # 배당 + 분기별 breakdown
"삼성전자 최근 자사주 취득·소각 이력 보여줘"              # 자사주 이력
"롯데케미칼 2024 yoy + 회계 risk alert"                # 재무 + 감사의견
"KT&G 기업지배구조보고서 준수율"                          # 거버넌스 15 지표
"KT&G 의결권 메모 만들어줘"                              # Open Proxy Guideline 기반 의결권 자문
```

더 많은 사용 패턴 → [wiki/tools/README.md](wiki/tools/README.md) (16 tool 카탈로그) 참조.

---

## Tool 구조 (16개)

16개 tool은 **Company → Meeting/Data/Evidence → Action** 흐름으로 동작합니다.

```text
OpenProxy MCP
├─ Company
│  └─ company
│     └─ 기업 식별, corp_code, 최근 공시 인덱스
│
├─ Meeting
│  ├─ shareholder_meeting_notice
│  │  └─ 주총 전: 소집공고, 안건, 후보, 보수, 정관, 재무
│  └─ shareholder_meeting_results
│     └─ 주총 후: 의결 결과, 찬반율, 사후 결과 요약
│
├─ Data Tools
│  ├─ corp_gov_report: 공시 1종 (기업지배구조보고서)
│  ├─ corporate_restructuring: 공시 4종 (합병, 분할 등)
│  ├─ dilutive_issuance: 공시 4종 (유상증자, CB/BW, 감자 등)
│  ├─ dividend: 공시 5종 (현금배당, 주식배당, 배당기준일 등)
│  ├─ financial_metrics: 공시 3종 (사업·반기·분기보고서)
│  ├─ ownership_structure: 공시 4종 (대량보유, 임원·주요주주, 사업보고서 등)
│  ├─ proxy_contest: 공시 5종 (위임장, 소송, 대량보유, 주총결과 등)
│  ├─ related_party_transaction: 공시 2종 (타법인주식, 단일판매공급계약)
│  ├─ treasury_share: 공시 5종 (자기주식 취득·처분·소각·신탁 등)
│  └─ value_up: 공시 3종 (기업가치제고계획, 자기주식 등)
│
├─ Evidence
│  └─ evidence
│     └─ rcept_no 기반 공시 URL, 출처, 메타데이터
│
└─ Action Tools
   ├─ proxy_advise_before_meeting
   │  └─ 주총 전 의결권 자문
   │     ├─ company
   │     ├─ shareholder_meeting_notice
   │     ├─ ownership_structure
   │     ├─ financial_metrics
   │     ├─ corp_gov_report
   │     ├─ dividend / treasury_share / value_up
   │     └─ proxy_contest / evidence
   │
   └─ proxy_result_after_meeting
      └─ 주총 후 결과 요약
         ├─ shareholder_meeting_results
         ├─ evidence
         └─ 필요 시 관련 data tools
```

각 data tool이 참조하는 상세 공시 유형은 [data tool disclosure map](wiki/tools/data_tool_disclosure_map.md)을 참조합니다.

| Layer | Tools | 역할 |
|---|---|---|
| Company | `company` | 기업 식별과 공통 공시 인덱스 |
| Meeting | `shareholder_meeting_notice`, `shareholder_meeting_results` | 주총 전/후 데이터 |
| Data | `corp_gov_report`, `corporate_restructuring`, `dilutive_issuance`, `dividend`, `financial_metrics`, `ownership_structure`, `proxy_contest`, `related_party_transaction`, `treasury_share`, `value_up` | 개별 공시/재무/지배구조 파싱 |
| Evidence | `evidence` | 공시번호 기반 출처 추적 |
| Action | `proxy_advise_before_meeting`, `proxy_result_after_meeting` | 여러 data tool을 묶어 판단/보고 생성 |

> 각 tool의 scope·옵션·data source·검증 결과는 **[wiki/tools/README.md](wiki/tools/README.md)** 카탈로그 또는 개별 tool 페이지 (`wiki/tools/{name}.md`) 참조.

### 의결권 정책 — Open Proxy Guideline

`proxy_advise_before_meeting`의 default 정책은 **OPM 자체 Open Proxy Guideline** (v1.3):
- **12 카테고리** × 116 룰 + 11 신규 토픽 + **2026 신법 7개 즉시 반영**
- **4 기준**: 소수주주 보호 / 거버넌스 투명성 / 장기 가치 / 추적 가능성
- **38 법령 layer 룰** (1·2·3차 상법 개정 + 정관 우회 시나리오)
- 익명화된 기관 정책 corpus는 내부 cross-reference로만 사용하며, 사용자 응답에는 기관 실명이나 식별자를 노출하지 않음

**모든 응답에 `data.usage` 블록**: DART API 호출 수 + MCP tool 호출 수 노출 (분당 1000 한도 — `dart/client.py` rolling window cap 900으로 hard guard).

```
사용 패턴:  company로 시작 → 데이터 탭으로 사실 확인 → action tool로 종합 분석
```

### 도메인별 요약

| 도메인 | 설명 | tool 수 |
|--------|------|---------|
| **회사** | 기업 식별 + 최근 공시 인덱스 | 1 |
| **주총 (사전)** | shareholder_meeting_notice — 안건·이사후보·보수한도·정관변경 (DART) | 1 |
| **주총 (사후)** | shareholder_meeting_results — 의결 결과, 찬반율, 사후 결과 요약 | 1 |
| **지분** | 최대주주, 대량보유, control map, 변동신고서 | 1 |
| **배당** | 실지급 배당 사실 + 분기별 breakdown | 1 |
| **자사주** | 결정 5종 (사전) + 결과 4종 (실집행) + 사이클 매칭 (★ 결정-실집행 검증) | 1 |
| **분쟁** | 위임장 경쟁, 소송, 5% 시그널 | 1 |
| **밸류업** | 기업가치 제고 계획, 이행현황 | 1 |
| **재편** | 합병·분할·분할합병·주식교환·이전 결정 | 1 |
| **희석** | 유상증자·CB·BW·감자 발행 결정 | 1 |
| **내부거래** | 타법인주식 거래 + 단일공급계약 | 1 |
| **거버넌스** | 기업지배구조보고서 (15 핵심지표, 2026년부터 KOSPI 전체 의무) | 1 |
| **재무** | DART 재무 4 endpoint 통합 — 51 지표 + 듀퐁 + FCF + NWC + 회계 risk + 감사의견 3년 추이 | 1 |
| **근거** | 공시 원문 링크 제공 | 1 |
| **액션** | proxy_advise_before_meeting (사전 안건별 결정 + facts/risk/citation/근거공고/후보 raw) + proxy_result_after_meeting (사후 결과) | 2 |
| | **합계** | **16** |

---

## 의결권 행사 판단

주주총회 안건에 대한 의결권 행사 판단을 요청하면, 아래의 기준에 따라 찬성/반대/검토 의견을 제시합니다.

| 안건 유형 | FOR | AGAINST | REVIEW |
|-----------|-----|---------|--------|
| 재무제표 | 감사의견 적정 | 한정/부적정 | 배당성향 극단적 |
| **사외이사 선임** | 독립성 충족 + 결격 없음 | 독립성 미달 / 결격사유 | 겸직 3개+, 부정 뉴스 |
| **사내이사 선임 (연임)** | 결격 없음 + 재직 성과 good/moderate | 재직 성과 **bad** (자본잠식/적자 + 누적 악화) | 재직 성과 **weak** (사용자 검토) |
| 사내이사 선임 (신임) | 결격 없음 (재직 X → 성과 미평가) | 결격사유 발견 | — |
| **이사 보수한도** | 동결/소폭 변경, 흑자+자본 정상 | 자본잠식+인상, 소진율<30%+인상, 적자/yoy<0+인상 | 50%+ 인상, 30~50% 인상, 소진율<30%+동결/미파악 |
| **감사 보수한도** | 1인당 평균 ≥1억+소폭 변경, 동결 | 자본잠식+인상, 1인당 평균 과소, 고액 구간에서 과도한 인상 | 30~50% 인상, 1인당 평균 5천만~1억 경계 |
| **퇴직금 규정** | 단순 정정/법령 반영, 퇴직연금 제도 도입 | 황금낙하산/경영권 변동 가산, 사외이사 퇴직금 신설, 지급률 2배수+ | 자본잠식+변경, 한도/규정 신설, 대상 확장, 위험 키워드 hit |
| 정관변경 | 법령 반영 (형식적) | 집중투표 배제 | 이사 정원 축소 |
| 자기주식 | 소각 목적 | 경영권 방어 목적 | 재단 출연 |
| 배당 | 업종 평균 이상 | 이익 증가인데 DPS 감소 | 감액배당 |

### 사내이사 재직 중 성과 매트릭스 (2x3)

회사 추천 사내이사를 결격사유만 보고 자동 FOR 처리하면 status quo 편향이 생깁니다. 그래서 **재직 기간 동안의 회사 운영 성과** 를 6 cell로 채점해서 의결권 권고에 반영합니다.

| Metric | avg | trend |
|---|---|---|
| **ROE** (자기자본이익률) | 평균 점수 | 추세 점수 |
| **부채비율** | 평균 점수 | 재직 누적변화 점수 |
| **CSR** (배당+소각/순이익) | 평균 점수 | 추세 점수 |

각 cell: good +2 / moderate +1 / weak 0 / bad -1. 총점 ≥+7 = good / +3~+6 = moderate / 0~+2 = weak / <0 = bad.

**Special rules**: 자본잠식 (full) 시 ROE/leverage avg 자동 bad / 적자 + 환원 활동 시 CSR weak (자본잠식 가속) / 적자 + 환원 자제 시 CSR moderate (보수성).

KOSPI 100 + KOSDAQ 50 (n=128) 검증: G1 classification 노출률 100%, distribution good 29.7% / mod 45.3% / weak 18.0% / bad 7.0% (모든 target band 충족).

---

## 데이터 소스

| 소스 | 용도 | 비고 |
|------|------|------|
| [DART OpenAPI](https://opendart.fss.or.kr/) (`opendart.fss.or.kr`) | 정기·주요 공시 메타 + 재무 endpoint + 배당/자사주/지분 등 모든 정형 데이터 | **필수** — 무료 API 키. 분당 1,000회 hard rule (cap 900) |
| DART 웹 (`dart.fss.or.kr`) | 공시 본문 HTML 파싱 (주총소집공고 / 주요사항보고서 등 ACODE 기반) | 웹 스크래핑, `_throttle_web` rate-limited (2-5초) |
| [KRX KIND](https://kind.krx.co.kr/) | 일부 거래소 공시 보조 확인 | 필요 시 공시 확인 보조 소스로 사용 |
| 익명화 기관 정책 corpus | 의결권 판단 cross-reference | 내부 정적 데이터. 사용자 응답에는 기관 실명/식별자 비노출 |

---

## 프로젝트 구조

```
open_proxy_mcp/
  server.py                # FastMCP 서버 (stdio + HTTP)
  tools_v2/                # 16개 tool (active)
  services/                # 도메인별 분석 로직 (tool과 분리)
  dart/client.py           # DART API + 보조 공시 조회 + rate limiter (cap 900/분)
  data/asset_managers/     # 익명화 기관 정책 corpus + Open Proxy Guideline + 12 매트릭스
scripts/
  wiki_lint.py             # wiki link 정책 자동 검증 (단방향/양방향)
  spot_*.py                # 회귀 spot 스크립트 (KOSPI/KOSDAQ batch)
wiki/                      # LLM 도메인 지식 — 트리 흐름순
  raw/                     # 🌱 뿌리 — 외부 원본 (수정 X)
  rules/                   # 🪵 줄기 — concepts/ + disclosures/ + laws/ (한국 자본시장 사실)
  tools/                   # 🌿 큰가지 — 16 tool 카탈로그 (사용자 진입점)
  decisions/               # 🌿 큰가지 — OPM 정책 (open-proxy-guideline 등)
  architecture/            # 🌿 큰가지 (core) + 🌾 잔가지 (audits/ + fixes/)
  ralph/                   # 🌾 잔가지 — 작업 plan 시간순
  lessons/                 # 🌾 잔가지 — 회고
  archive/                 # 🍂 낙엽 — 흡수/대체 보존
  index.md                 # 전체 인덱스 (시작점)
  WIKI_SCHEMA.md           # 트리 정책 + 카테고리 + 명명 규칙
  log.md                   # 작업 로그
.github/workflows/
  wiki-lint.yml            # wiki/ 변경 시 lint --strict 자동 (PR/push CI)
  deploy.yml               # Fly.io 배포
Dockerfile                 # Fly.io 배포용 컨테이너
fly.toml                   # Fly.io 설정 (nrt 리전, auto-suspend)
```

---

## Disclaimer

OpenProxy는 DART 공시 데이터를 구조화하여 AI에게 제공하는 도구입니다. AI는 할루시네이션(hallucination)을 일으킬 수 있고, 부정확한 분석을 제공할 수도 있습니다. AI가 제시하는 의견은 개발자 또는 개발자의 소속 단체의 의견이 아닙니다. 분석 결과는 참고 목적으로만 사용하고, 투자 결정이나 의결권 행사의 최종 판단은 반드시 원문 공시와 전문가 검토를 거쳐야 합니다.

---

## 라이선스

[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) -- 비상업적 사용만 허용

이 프로젝트의 코드와 데이터를 사용할 때는 출처를 밝혀야 합니다. 상업적 목적으로는 사용할 수 없습니다.
