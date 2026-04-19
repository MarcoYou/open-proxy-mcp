# OpenProxy MCP

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-green.svg)](https://modelcontextprotocol.io/)
[![Tools](https://img.shields.io/badge/tools-11-orange.svg)](#tool-구조-11개)

[English README](README_ENG.md)

## Why OpenProxy?

코리아 디스카운트의 핵심에는 거버넌스 리스크가 있어요. 패시브 투자가 늘면서 주식 오너십의 의미가 희미해지는 지금, 이 리스크는 오히려 더 선명해지고 있죠. 거버넌스 정보에 쉽게 접근하고, 빠르게 분석할 수 있어야 하지만 -- 수백 페이지의 공시 원문을 직접 읽고 판단하기엔 시간도 전문성도 부족해요.

**OpenProxy는 AI로 이 장벽을 허물어요.** DART 공시를 구조화된 데이터로 바꿔서, 지분 구조부터 배당 이력, 주총 안건, 경영권 분쟁까지 -- 거버넌스 분석 전반을 누구나 몇 초 만에 할 수 있게 만들었어요.

![OpenProxy MCP 비교](screenshot/open-proxy-mcp%20output%20kor.png)

---

## 빠른 시작

### 0단계: Claude 구독 확인 (필수)

MCP 커넥터는 **Claude Pro, Max, Teams** 구독자만 사용할 수 있어요. [claude.ai](https://claude.ai)에서 구독 상태를 확인해주세요.

### 1단계: DART API 키 발급 (필수)

OpenProxy의 모든 데이터는 DART OpenAPI에서 가져와요. **본인의 API 키가 있어야 사용할 수 있어요.**

1. [DART OpenAPI](https://opendart.fss.or.kr/) 접속 -> 회원가입
2. 인증키 신청 -> 발급 (무료, 바로 발급돼요)

### 2단계: 연결

API 키를 발급받았다면, 아래 두 가지 방법 중 하나를 선택하세요.

#### 방법 A: 원격 서버 (설치 없이 30초면 돼요)

URL 끝에 발급받은 DART API 키를 붙여서 연결해요. 키는 서버에서만 사용되고, AI에게는 노출되지 않아요.

**claude.ai 웹:**

1. [claude.ai](https://claude.ai) 접속 -> 설정 -> 커넥터
2. "커스텀 커넥터 추가" 선택
3. 이름: `open-proxy-mcp`, URL 입력:
```
https://open-proxy-mcp.fly.dev/mcp?opendart=발급받은_키
```
4. "추가" 클릭 -> 11개 tool이 자동으로 인식돼요
5. 추가된 커넥터의 구성 -> 권한에서 **"항상 허용"** 선택 (매번 승인 없이 tool이 자동 실행돼요)

> **참고**: tool이 추가되거나 변경된 경우 커넥터 MCP 서버 업데이트에 시간이 걸릴 수 있어요. 커넥터를 삭제한 뒤 다시 연결하면 바로 최신 tool이 반영돼요. 재연결한 후 새 채팅을 열어서 다시 시도해주세요.

#### 방법 B: 로컬 설치

로컬에서 실행하면 DART 외에 추가 API 키도 설정할 수 있어요 (후보자 뉴스 검색, OCR fallback 등).

[로컬 설치 가이드](docs/connect.md) 참조

### 사용 예시

연결이 끝났다면, 자연어로 질문하면 돼요:

```
"삼성전자 주주총회 안건 분석해줘"
"KB금융 사외이사 후보 독립성 검토해줘"
"현대차 보수한도 적정성 판단해줘"
"삼성전자 지분 구조 보여줘"
"SK하이닉스 배당 추이 알려줘"
"고려아연 경영권 분쟁 분석해줘"
```

\* OpenProxy는 현재 DART의 재무지표를 분석하지 않으니 주의해주세요 (업데이트 예정)

---

## Tool 구조 (11개)

11개 tool은 **데이터 탭**과 **결과물 생성** 두 단계로 나뉘어요.

```
company                      # 모든 시작점 — 기업 식별 + 최근 공시 인덱스
│
├─ Data Tools (7)
│  ├─ shareholder_meeting    # 주총 (안건 / 이사후보 / 보수한도 / 결과)
│  ├─ ownership_structure    # 지분 구조 (최대주주 / 5% 블록 / 자사주 / control map)
│  ├─ dividend               # 배당 사실 (DPS / 배당성향 / 추이)
│  ├─ treasury_share         # 자사주 이벤트 (취득 / 처분 / 소각 / 신탁)
│  ├─ proxy_contest          # 경영권 분쟁 (위임장 / 소송 / 5% 시그널)
│  ├─ value_up               # 밸류업 계획 (약속 / 이행현황)
│  └─ evidence               # 공시 원문 링크 (rcept_no → viewer_url)
│
└─ Action Tools (3)
   ├─ prepare_vote_brief      # 의결권 행사 메모
   ├─ prepare_engagement_case # 주주관여 케이스 메모
   └─ build_campaign_brief    # 캠페인 브리프
```

한 줄로 요약하면:

```
회사 이름으로 시작해서
-> 데이터 탭으로 사실을 확인하고
-> evidence로 공시 원문을 검증하고
-> action tool로 결과물을 만든다
```

### 도메인별 요약

| 도메인 | 설명 | tool 수 |
|--------|------|---------|
| **회사** | 기업 식별 + 최근 공시 인덱스 | 1 |
| **주총** | 안건, 이사후보, 보수한도, 정관변경, 결과 | 1 |
| **지분** | 최대주주, 대량보유, 자사주, control map | 1 |
| **배당** | 실지급 배당 사실, DPS, 배당성향, 추이 | 1 |
| **자사주** | 취득·처분·소각·신탁 이벤트 | 1 |
| **분쟁** | 위임장 경쟁, 소송, 5% 시그널 | 1 |
| **밸류업** | 기업가치 제고 계획, 이행현황 | 1 |
| **근거** | 공시 원문 링크 제공 | 1 |
| **액션** | 의결권 메모, 주주관여 케이스, 캠페인 브리프 | 3 |
| | **합계** | **11** |

---

## 의결권 행사 판단

주주총회 안건에 대한 의결권 행사 판단을 요청하면, 아래의 기준에 따라 찬성/반대/검토 의견을 제시해요.

| 안건 유형 | FOR | AGAINST | REVIEW |
|-----------|-----|---------|--------|
| 재무제표 | 감사의견 적정 | 한정/부적정 | 배당성향 극단적 |
| 이사 선임 | 사외이사 독립성 충족 | 독립성 미달 | 겸직 3개+, 부정 뉴스 |
| 보수한도 | 소진율 적정 | 소진율 < 30%인데 인상 | 50%+ 대폭 인상 |
| 정관변경 | 법령 반영 (형식적) | 집중투표 배제 | 이사 정원 축소 |
| 자기주식 | 소각 목적 | 경영권 방어 목적 | 재단 출연 |
| 배당 | 업종 평균 이상 | 이익 증가인데 DPS 감소 | 감액배당 |

---

## 데이터 소스

| 소스 | 용도 | 비고 |
|------|------|------|
| [DART OpenAPI](https://opendart.fss.or.kr/) | 소집공고, 사업보고서, 대량보유 공시 | 필수 (무료 API 키) |
| [KRX KIND](https://kind.krx.co.kr/) | 주총 의결권 행사 결과 | 웹 크롤링 |
| [네이버 뉴스 API](https://developers.naver.com/) | 후보자 부정 뉴스 검색 | 선택 (무료 API 키) |
| [네이버 금융](https://finance.naver.com/) | 주가, 업종명, 배당 시세 | 웹 크롤링 |

---

## 프로젝트 구조

```
open-proxy-mcp/
  open_proxy_mcp/
    server.py              # FastMCP 서버 (stdio + HTTP)
    tools_v2/              # 11개 tool
    services/              # 도메인별 분석 로직 (tool과 분리)
    dart/client.py         # DART API + KIND 크롤링 + 네이버 + rate limiter
  Dockerfile               # Fly.io 배포용 컨테이너
  fly.toml                 # Fly.io 설정 (nrt 리전, auto-suspend)
  wiki/                    # 도메인 지식 위키
```

---

## Disclaimer

OpenProxy는 DART 공시 데이터를 구조화하여 AI에게 제공하는 도구예요. AI는 할루시네이션(hallucination)을 일으킬 수 있고, 부정확한 분석을 제공할 수도 있어요. AI가 제시하는 의견은 개발자 또는 개발자의 소속 단체의 의견이 아니에요. 분석 결과는 참고 목적으로만 사용하시고, 투자 결정이나 의결권 행사의 최종 판단은 반드시 원문 공시와 전문가 검토를 거쳐주세요.

---

## 라이선스

[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) -- 비상업적 사용만 허용

이 프로젝트의 코드와 데이터를 사용할 때는 출처를 밝혀주세요. 상업적 목적으로는 사용할 수 없어요.
