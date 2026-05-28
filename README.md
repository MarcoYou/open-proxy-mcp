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

## 주요 기능

- **주총 전 의결권 자문**: 소집공고의 안건, 후보, 보수한도, 정관변경, 재무제표를 구조화하고 안건별 FOR/AGAINST/REVIEW 의견을 제시합니다.
- **주총 후 결과 요약**: 주주총회 결과 공시에서 의결 결과와 찬반율을 추출해 사후 voting outcome을 정리합니다.
- **지분·분쟁 신호 분석**: 최대주주, 5% 보유, 위임장 권유, 소송, 경영권 분쟁 관련 공시를 묶어 control risk를 확인합니다.
- **환원정책 추적**: 배당, 자기주식 취득·처분·소각, 기업가치 제고 계획을 함께 조회해 주주환원 이행 여부를 봅니다.
- **재무·거버넌스 점검**: DART 재무 endpoint와 기업지배구조보고서를 기반으로 주요 재무 지표, 감사의견, 지배구조 핵심지표를 제공합니다.
- **출처 추적**: 모든 분석은 DART 공시번호와 원문 URL을 통해 근거 공시로 돌아갈 수 있게 설계되어 있습니다.

---

## 빠른 시작

### 0단계: 어디에서 쓸 수 있나요?

OpenProxy MCP는 Claude, ChatGPT, Perplexity 같은 AI 서비스에 연결해서 쓰는 도구입니다. 내 컴퓨터에 설치할 필요 없이, 아래 주소를 AI 서비스의 "커넥터 추가" 화면에 넣으면 됩니다.

- **Claude**: 커넥터 추가 기능이 있는 유료 플랜이 필요합니다.
- **ChatGPT**: 앱 또는 커넥터를 직접 추가할 수 있는 계정이어야 합니다.
- **Perplexity**: 사용자 지정 커넥터 추가 기능이 보이는 앱이나 계정이어야 합니다.

> **참고**:
> - 사용하는 요금제나 계정 설정에 따라 커넥터 추가 메뉴가 보이지 않을 수 있습니다.
> - 이 README는 직접 설치하는 방법이 아니라, 이미 배포된 서버에 연결하는 방법을 설명합니다.

### 1단계: DART API 키 발급 (필수)

OpenProxy의 모든 데이터는 DART OpenAPI에서 가져옵니다. **본인의 API 키가 있어야 사용할 수 있습니다.**

1. [DART OpenAPI](https://opendart.fss.or.kr/) 접속 -> 회원가입
2. 인증키 신청 -> 발급 (무료, 바로 발급됩니다)

### 2단계: AI 서비스에 연결

API 키를 발급받았다면, 사용하는 AI 서비스에 아래 주소를 등록합니다.

> **API 키 주의**: 아래 주소에는 본인의 OpenDART API 키가 들어갑니다. 일반 채팅창에 그대로 붙여넣지 말고, 커넥터 설정 화면의 서버 주소 입력칸에만 넣습니다.

```
https://open-proxy-mcp.fly.dev/mcp?opendart=발급받은_OpenDART_API_키
```

#### Claude에서 연결하기

1. [claude.ai](https://claude.ai)에 접속합니다.
2. `설정` -> `커넥터` -> `맞춤 설정` -> `커넥터 추가`로 이동합니다.
3. 이름에 `open-proxy-mcp`를 입력하고, 서버 주소 입력칸에 위 URL을 붙여넣은 뒤 `추가`를 클릭합니다.
4. 추가된 `open-proxy-mcp`를 다시 클릭하고, `도구 권한`을 **항상 허용**으로 바꿉니다.
5. 새 채팅을 열고 좌측 하단 `+` 버튼을 눌러, 커넥터에 `open-proxy-mcp`가 선택되어 있는지 확인합니다.

#### ChatGPT에서 연결하기

1. [chatgpt.com](https://chatgpt.com)에 접속합니다.
2. `설정` -> `앱` -> `고급설정`에서 `개발자 모드`를 켠 뒤, 이전 화면으로 돌아와 `앱 만들기`로 이동합니다.
3. 앱 만들기 화면에서 이름에 `open-proxy-mcp`를 입력하고, 서버 주소 입력칸에 위 URL을 붙여넣습니다.
4. 새 채팅을 열고 좌측 하단 `+` 버튼을 누른 뒤 `더보기`에서 `open-proxy-mcp`를 선택할 수 있는지 확인합니다.

#### Perplexity에서 연결하기

1. [perplexity.ai](https://www.perplexity.ai/)에 접속합니다.
2. `설정` -> `커넥터` -> `사용자 지정 커넥터`로 이동해 커넥터 추가 버튼을 선택합니다.
3. 이름에 `open-proxy-mcp`를 입력하고, 서버 주소 입력칸에 위 URL을 붙여넣습니다.
4. 새 대화를 열고 좌측 하단 `+` 버튼 또는 커넥터 선택 영역을 눌러, `open-proxy-mcp`를 선택할 수 있는지 확인합니다.

> **참고**: 처음 사용할 때는 서버가 켜지는 데 시간이 걸릴 수 있습니다. 타임아웃 오류가 나면 잠시 후 다시 시도합니다. 기능이 새로 추가됐는데 보이지 않는 경우에는 커넥터를 삭제한 뒤 다시 연결하면 더 빨리 반영됩니다.
>
> ChatGPT와 Perplexity는 계정, 요금제, 앱 버전에 따라 커넥터 추가 메뉴가 보이지 않을 수 있습니다.

### 사용 예시

연결이 끝났다면, 자연어로 이어서 질문하면 됩니다. 한 번에 모든 tool 이름을 알 필요는 없습니다.

**주총 안건 검토**

1. `LG화학 2026년 정기 주주총회 안건 알려줘`
2. `각 안건별로 찬성/반대/보류 의견을 조언해줘`
3. `보류로 나온 안건은 어떤 근거 때문에 보류인지 설명해줘`

**주주환원 점검**

1. `KT&G 기업가치제고계획 알려줘`
2. `지난 3년 동안의 배당 이력 알려줘`
3. `자사주 취득 이력도 같이 보여줘`
4. `기업가치제고계획과 실제 주주환원 이력이 일관적인지 정리해줘`

**분쟁·지배구조 점검**

1. `고려아연 경영권 분쟁 관련 공시 알려줘`
2. `현재 지분 구조와 5% 보유자 변화를 같이 보여줘`

더 많은 사용 패턴 → [wiki/tools/README.md](wiki/tools/README.md) (16 tool 카탈로그) 참조.

---

## Tool 구조 (16개)

OpenProxy MCP의 16개 tool은 **Company → Meeting/Data/Evidence → Action** 흐름으로 동작합니다.

| Layer | Tools | 역할 |
|---|---|---|
| Company | `company` | 기업 식별과 공통 공시 인덱스 |
| Meeting | `shareholder_meeting_notice`, `shareholder_meeting_results` | 주총 전/후 데이터 |
| Data | `corp_gov_report`, `corporate_restructuring`, `dilutive_issuance`, `dividend`, `financial_metrics`, `ownership_structure`, `proxy_contest`, `related_party_transaction`, `treasury_share`, `value_up` | 개별 공시/재무/지배구조 파싱 |
| Evidence | `evidence` | 공시번호 기반 출처 추적 |
| Action | `proxy_advise_before_meeting`, `proxy_result_after_meeting` | 여러 data tool을 묶어 판단/보고 생성 |

상세 문서는 아래에서 확인합니다.

- [Tool 카탈로그](wiki/tools/README.md): 16개 public tool의 scope, 입력, 출력, data source
- [Data tool disclosure map](wiki/tools/data_tool_disclosure_map.md): data tool별 참조 공시 유형
- [의결권 판단 구조](wiki/architecture/proxy-voting-decision-tree.md): `proxy_advise_before_meeting` 판단 흐름
- [프로젝트 구조](wiki/architecture/project_structure.md): 코드와 wiki 디렉터리 구조

### 의결권 정책

`proxy_advise_before_meeting`은 OPM 자체 Open Proxy Guideline을 기본 정책으로 사용합니다. 판단 기준은 소수주주 보호, 거버넌스 투명성, 장기 가치, 추적 가능성입니다. 익명화된 기관 정책 corpus는 내부 cross-reference로만 사용하며, 사용자 응답에는 기관 실명이나 식별자를 노출하지 않습니다.

**모든 응답에 `data.usage` 블록**: DART API 호출 수 + MCP tool 호출 수 노출 (분당 1000 한도 — `dart/client.py` rolling window cap 900으로 hard guard).

```
사용 패턴:  company로 시작 → 데이터 탭으로 사실 확인 → action tool로 종합 분석
```

---

## 데이터 소스

| 소스 | 용도 | 비고 |
|------|------|------|
| [DART OpenAPI](https://opendart.fss.or.kr/) (`opendart.fss.or.kr`) | 정기·주요 공시 메타 + 재무 endpoint + 배당/자사주/지분 등 모든 정형 데이터 | **필수** — 무료 API 키. 분당 1,000회 hard rule (cap 900) |
| DART 웹 (`dart.fss.or.kr`) | 공시 본문 HTML 파싱 (주총소집공고 / 주요사항보고서 등 ACODE 기반) | 웹 스크래핑, `_throttle_web` rate-limited (2-5초) |
| [KRX KIND](https://kind.krx.co.kr/) | 일부 거래소 공시 보조 확인 | 필요 시 공시 확인 보조 소스로 사용 |
| 익명화 기관 정책 corpus | 의결권 판단 cross-reference | 내부 정적 데이터. 사용자 응답에는 기관 실명/식별자 비노출 |

---

## 개발자 문서

개발자용 구조, 감사 결과, tool 상세는 wiki에 정리되어 있습니다.

- [프로젝트 구조](wiki/architecture/project_structure.md)
- [Tool 카탈로그](wiki/tools/README.md)
- [Parsing 성공률 감사](wiki/architecture/audits/260517_parsing_success_rate_audit.md)
- [Agenda parser marketwide audit](wiki/architecture/audits/260525_1620_audit_agenda-parser-marketwide.md)

---

## Disclaimer

OpenProxy는 DART 공시 데이터를 구조화하여 AI에게 제공하는 도구입니다. AI는 할루시네이션(hallucination)을 일으킬 수 있고, 부정확한 분석을 제공할 수도 있습니다. AI가 제시하는 의견은 개발자 또는 개발자의 소속 단체의 의견이 아닙니다. 분석 결과는 참고 목적으로만 사용하고, 투자 결정이나 의결권 행사의 최종 판단은 반드시 원문 공시와 전문가 검토를 거쳐야 합니다.

---

## 라이선스

[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) -- 비상업적 사용만 허용

이 프로젝트의 코드와 데이터를 사용할 때는 출처를 밝혀야 합니다. 상업적 목적으로는 사용할 수 없습니다.
