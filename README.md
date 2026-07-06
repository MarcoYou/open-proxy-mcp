# OpenProxy MCP

[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/License-PolyForm%20Noncommercial%201.0.0-lightgrey.svg)](https://polyformproject.org/licenses/noncommercial/1.0.0/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-green.svg)](https://modelcontextprotocol.io/)
[![Tools](https://img.shields.io/badge/tools-19-orange.svg)](#tool-구조-19개)
[![Release](https://img.shields.io/badge/release-v2.1-blue.svg)](docs/RELEASE_NOTES.md)

[English README](README_ENG.md)

## Why OpenProxy?

코리아 디스카운트의 핵심에는 거버넌스 리스크가 있습니다. 패시브 투자가 늘면서 주식 오너십의 의미가 희미해지는 지금, 이 리스크는 오히려 더 선명해지고 있습니다. 거버넌스 정보에 쉽게 접근하고 빠르게 분석할 수 있어야 하지만, 수백 페이지의 공시 원문을 직접 읽고 판단하기에는 시간도 전문성도 부족합니다.

**OpenProxy는 AI로 이 장벽을 낮춥니다.** DART 공시를 구조화된 데이터로 바꿔서, 지분 구조부터 배당 이력, 주총 안건, 경영권 분쟁까지 거버넌스 분석 전반을 누구나 몇 초 만에 수행할 수 있게 만듭니다.

![OpenProxy MCP 비교](screenshot/open-proxy-mcp%20output%20kor.png)

## 주요 기능

각 기능을 클릭하면 상세 설명 페이지로 이동합니다.

- **[주총 의결권 보조](docs/features/proxy-voting.md)**: 소집공고 안건을 구조화하고 안건별 FOR/AGAINST/REVIEW 권고와 근거를 제시합니다.
- **[경영권 분쟁 시그널](docs/features/control-contest.md)**: 위임장·공개매수·소송·5% 경영참여 신호를 모아 분쟁/액티비즘 정황을 나열합니다 (자동 판정 X, 정보 나열).
- **[지분·지배구조 맵](docs/features/ownership.md)**: 최대주주·특수관계인·5% 대량보유·자사주로 소유 구조를 그립니다.
- **[주총 안건 구조화](docs/features/meeting-agenda.md)**: 소집공고 안건·후보·보수한도·정관변경과 주총 후 의결 결과·찬반율을 정리합니다.
- **[주주환원](docs/features/shareholder-return.md)**: 배당·자기주식 소각 사이클·밸류업 계획을 묶어 환원 정책의 약속과 실제 집행을 비교합니다.
- **[재무지표](docs/features/financials.md)**: DART 재무 endpoint 통합 — 수익성·안정성·현금흐름 + 듀퐁 분해·감사의견 추이. 분기는 누적(YTD)·당기(3개월) 두 기준으로 QoQ·YoY를 제공하고, 회전일수는 TTM 기준으로 산출하며 어느 기준인지 항상 명시합니다.
- **밸류에이션**: PER·PBR·배당수익률(기업 심층) + 시장 전체·산업별·종목 히스토리(주간 스냅샷). 지배주주 귀속, 비KRW 기능통화 자동 환산(한국은행 ECOS), 적자·자본잠식 N/M 처리. `scope="explain"`으로 수치의 계산 과정·기준·출처를 답합니다.
- **기업 리스크 이벤트**: 중대재해·횡령배임·생산중단 공시를 추적합니다. 회사를 지정하지 않으면 시장 전체에서 최근 사건을 스캔합니다.

그 외 출처 추적, 기업지배구조보고서, 희석 이벤트(증자/CB), 구조개편(합병/분할), 지분 인수·매각과 내부거래, 밸류업·배당·소각 약속 이행 추적 등 총 19개 tool을 제공합니다.

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

**리스크 모니터링**

1. `최근 한 달 사이에 중대재해나 횡령 공시 낸 상장사 알려줘`
2. `한화에어로스페이스 중대재해 이력을 사상자까지 자세히 보여줘`

---

## Tool 구조 (19개)

OpenProxy MCP의 19개 tool은 **Company → Meeting/Data/Evidence → Action** 흐름으로 동작합니다.

| Layer | Tools | 역할 |
|---|---|---|
| Company | [`company`](wiki/tools/company.md) | 기업 식별과 공통 공시 인덱스 |
| Meeting | [`shareholder_meeting_notice`](wiki/tools/shareholder_meeting_notice.md), [`shareholder_meeting_results`](wiki/tools/shareholder_meeting_results.md) | 주총 전/후 데이터 |
| Data | [`corp_gov_report`](wiki/tools/corp_gov_report.md), [`corporate_restructuring`](wiki/tools/corporate_restructuring.md), [`dilutive_issuance`](wiki/tools/dilutive_issuance.md), [`dividend`](wiki/tools/dividend.md), [`financial_metrics`](wiki/tools/financial_metrics.md), [`valuation`](wiki/tools/valuation.md), [`ownership_structure`](wiki/tools/ownership_structure.md), [`corporate_deals`](wiki/tools/corporate_deals.md), [`order_contracts`](wiki/tools/order_contracts.md), [`proxy_contest`](wiki/tools/proxy_contest.md), [`risk_events`](wiki/tools/risk_events.md), [`treasury_share`](wiki/tools/treasury_share.md), [`value_up`](wiki/tools/value_up.md) | 개별 공시/재무/지배구조 파싱 |
| Evidence | [`evidence`](wiki/tools/evidence.md) | 공시번호 기반 출처 추적 |
| Action | [`proxy_advise_before_meeting`](wiki/tools/proxy_advise_before_meeting.md), [`shareholder_commitment`](wiki/tools/shareholder_commitment.md) | 여러 data tool을 묶어 판단/보고 생성 (사후 결과는 [`shareholder_meeting_results`](wiki/tools/shareholder_meeting_results.md)) — 후자는 밸류업·배당·소각 약속 vs 실제 이행 추적 |

### 의결권 정책

`proxy_advise_before_meeting`은 OPM 자체 Open Proxy Guideline을 기본 정책으로 사용합니다. 판단 기준은 소수주주 보호, 거버넌스 투명성, 장기 가치, 추적 가능성입니다. 익명화된 기관 정책 corpus는 내부 cross-reference로만 사용하며, 사용자 응답에는 기관 실명이나 식별자를 노출하지 않습니다.

**모든 응답에 `data.usage` 블록**: DART API 호출 수 + MCP tool 호출 수 노출 (분당 1000 한도 — `dart/client.py` rolling window cap 910으로 hard guard).

```
사용 패턴:  company로 시작 → 데이터 탭으로 사실 확인 → action tool로 종합 분석
```

---

## 데이터 소스

| 소스 | 용도 | 비고 |
|------|------|------|
| [DART OpenAPI](https://opendart.fss.or.kr/) (`opendart.fss.or.kr`) | 정기·주요 공시 메타 + 재무 endpoint + 배당/자사주/지분 등 모든 정형 데이터 | **필수** — 무료 API 키. 분당 1,000회 hard rule (cap 910) |
| DART 웹 (`dart.fss.or.kr`) | 공시 본문 HTML 파싱 (주총소집공고 / 주요사항보고서 등 ACODE 기반) | 웹 스크래핑, `_throttle_web` rate-limited (2-5초) |
| [KRX KIND](https://kind.krx.co.kr/) | 일부 거래소 공시 보조 확인 | 필요 시 공시 확인 보조 소스로 사용 |
| 익명화 기관 정책 corpus | 의결권 판단 cross-reference | 내부 정적 데이터. 사용자 응답에는 기관 실명/식별자 비노출 |

---

## 릴리즈 노트

버전별 변경 이력은 **[docs/RELEASE_NOTES.md](docs/RELEASE_NOTES.md)** 에서 확인할 수 있습니다.

- 최신: **v2.1** — risk_events 신설, corporate_deals rename, ownership·dividend·financial_metrics 정밀화

---

## Disclaimer

OpenProxy는 DART 공시 데이터를 구조화하여 AI에게 제공하는 도구입니다. AI는 할루시네이션(hallucination)을 일으킬 수 있고, 부정확한 분석을 제공할 수도 있습니다. AI가 제시하는 의견은 개발자 또는 개발자의 소속 단체의 의견이 아닙니다. 분석 결과는 참고 목적으로만 사용하고, 투자 결정이나 의결권 행사의 최종 판단은 반드시 원문 공시와 전문가 검토를 거쳐야 합니다.

---

## 라이선스

[PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0/) — 비상업적 사용만 허용 (전문: 루트 [`LICENSE`](LICENSE))

- **비상업적 사용**(개인 연구·학습·비영리 단체·공공기관 등)은 자유롭게 허용됩니다.
- **상업적 사용**은 허용되지 않습니다. 상업적 이용을 원하면 별도 라이선스 계약이 필요합니다 (OpenProxy AI).
- **재배포 시 출처 표기 의무**: 복사·재배포·수정본 배포 시 저작권 고지 `Copyright (c) 2026 OpenProxy AI (https://github.com/MarcoYou/open-proxy-mcp)` 를 그대로 유지해야 합니다 (PolyForm 'Notices' 조항).
- 본 라이선스에는 저작권·특허 라이선스 및 면책 조항이 포함됩니다.

> 상업 라이선스·기타 문의: gunhoqw20@gmail.com
