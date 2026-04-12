# OpenProxy MCP (OPM)

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-green.svg)](https://modelcontextprotocol.io/)
[![Tools](https://img.shields.io/badge/tools-33-orange.svg)](#tool-아키텍처-33개)

[English README](README_ENG.md)

![OpenProxy MCP 비교](screenshot/openproxy_mcp_compare_v2.png)

> **주주총회 공시 100페이지를 몇 초 만에 구조화된 데이터로 -- 기관투자자급 의결권 분석을 모든 투자자에게.**

---

## 빠른 시작

### 방법 A: 원격 서버 (설치 불필요, 30초)

OpenProxy는 Fly.io에 배포되어 있어, URL만으로 바로 사용할 수 있습니다.

**claude.ai 웹:**

1. [claude.ai](https://claude.ai) 접속 -> 채팅 입력창 하단 MCP 아이콘 클릭
2. "커스텀 커넥터 추가" 선택
3. 이름: `open-proxy-mcp`, URL: `https://open-proxy-mcp.fly.dev/mcp` 입력
4. "추가" 클릭 -> 33개 tool 자동 인식
5. 도구 권한에서 **"항상 허용"** 선택 (매번 승인 없이 tool 자동 실행)

**Claude Desktop:**

설정 > MCP 서버 추가 > URL 커넥터:

```
https://open-proxy-mcp.fly.dev/mcp
```

**Claude Code:**

```bash
claude mcp add open-proxy-mcp --transport streamable-http https://open-proxy-mcp.fly.dev/mcp
```

> 원격 서버는 공용 DART API 키를 사용합니다. 분당 1,000회 제한에 걸릴 수 있으므로, 안정적 사용을 위해서는 로컬 설치를 권장합니다.

### 방법 B: 로컬 설치

<details>
<summary>로컬 설치 가이드 (클릭하여 펼치기)</summary>

#### 1. 설치

```bash
git clone https://github.com/MarcoYou/open-proxy-mcp.git
cd open-proxy-mcp
uv sync                    # .venv 생성 + 의존성 설치
cp .env.example .env       # 환경변수 파일 생성
```

#### 2. 환경변수 설정

`.env` 파일을 열고 API 키를 입력합니다. OPENDART_API_KEY만 있으면 핵심 기능 전부 사용 가능합니다.

```bash
# 필수 -- opendart.fss.or.kr에서 무료 발급
OPENDART_API_KEY=발급받은_키

# 선택
OPENDART_API_KEY_2=보조_키              # 분당 1,000회 제한 도달 시 자동 전환
UPSTAGE_API_KEY=업스테이지_키            # OCR fallback용
KRX_API_KEY=KRX_키                     # 종목 코드, 시가총액
NAVER_SEARCH_API_CLIENT_ID=네이버_ID     # 뉴스 검색
NAVER_SEARCH_API_CLIENT_SECRET=네이버_시크릿
```

#### 3. Editable Install

```bash
uv pip install -e .
```

#### 4. Claude Desktop 연결

`~/Library/Application Support/Claude/claude_desktop_config.json`에 추가:

```json
{
  "mcpServers": {
    "open-proxy-mcp": {
      "command": "/path/to/open-proxy-mcp/.venv/bin/python",
      "args": ["-m", "open_proxy_mcp"],
      "cwd": "/path/to/open-proxy-mcp"
    }
  }
}
```

#### 5. Claude Code 연결

```json
// .mcp.json (프로젝트 루트)
{
  "mcpServers": {
    "open-proxy-mcp": {
      "command": "/path/to/open-proxy-mcp/.venv/bin/python",
      "args": ["-m", "open_proxy_mcp"],
      "cwd": "/path/to/open-proxy-mcp"
    }
  }
}
```

#### 6. 선택 의존성

```bash
uv pip install -e ".[pdf]"               # + PDF/OCR fallback
uv pip install -e ".[llm]"               # + LLM fallback (Claude/OpenAI)
uv pip install -e ".[all]"               # 전부 설치
```

</details>

### 사용 예시

연결 후 자연어로 질문하면 됩니다:

```
"삼성전자 주주총회 안건 분석해줘"
"KB금융 사외이사 후보 독립성 검토해줘"
"현대차 보수한도 적정성 판단해줘"
"삼성전자 지분 구조 보여줘"
"SK하이닉스 배당 추이 알려줘"
"고려아연 프록시파이트 분석해줘"
```

---

## 왜 OpenProxy인가?

패시브 투자의 급격한 확대로 의결권 행사는 거버넌스의 핵심 수단이 되었지만, 분석 과정은 여전히 수작업에 의존합니다.

- DART 소집공고는 100페이지 이상의 비정형 HTML -- 기업마다 형식이 다름
- 기관투자자는 내부 팀과 외부 자문사(ISS, Glass Lewis)에 의존
- 개인투자자와 소규모 운용사는 구조화된 의결권 정보에 접근 불가

**OpenProxy는 DART 공시를 AI가 바로 읽을 수 있는 구조화 데이터로 변환합니다.** MCP를 통해 AI 모델이 직접 공시 데이터를 조회하고 분석하므로, 누구나 일관되고 체계적인 의결권 분석이 가능해집니다.

**Before -- 원본 DART 공시:**
```
가. 이사의 수ㆍ보수총액 내지 최고 한도액
당 기(제58기, 2026년)
이사의 수 (사외이사수) 8(    5    )
보수총액 또는 최고한도액 450억원 ...
```

**After -- OpenProxy:**
```json
{
  "current": {"headcount": "8(5)", "limit": "450억원", "limitAmount": 45000000000},
  "prior": {"actualPaid": "287억원", "limit": "360억원"},
  "priorUtilization": 79.7
}
```

---

## Tool 아키텍처 (33개)

33개 tool은 5단계 Tier로 구성됩니다. AI는 Tier 1부터 순서대로 호출하며, 필요에 따라 하위 Detail tool로 내려갑니다.

```
                         ┌─────────────────────┐
                         │   corp_identifier    │  Tier 1 Entity
                         │  기업명/ticker 식별   │  "삼성전자" -> 005930
                         └──────────┬──────────┘
                                    │
                         ┌──────────▼──────────┐
                         │     tool_guide       │  Tier 2 Context
                         │  사용법 + 판단 기준   │
                         └──────────┬──────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              │                     │                     │
    ┌─────────▼────────┐ ┌─────────▼────────┐ ┌─────────▼────────┐
    │   agm_search     │ │   div_search     │ │   prx_search     │  Tier 3
    │   소집공고 검색   │ │   배당공시 검색   │ │  위임장공시 검색  │  Search
    └─────────┬────────┘ └─────────┬────────┘ └─────────┬────────┘
              │                    │                     │
    ┌─────────▼────────┐ ┌────────▼─────────┐ ┌────────▼─────────┐
    │ agm_pre_analysis │ │div_full_analysis │ │    prx_fight     │  Tier 4
    │ agm_post_analysis│ │  배당 종합 분석   │ │ 프록시파이트 감지 │  Orchestrate
    │own_full_analysis │ └────────┬─────────┘ └────────┬─────────┘
    │governance_report │          │                     │
    └─────────┬────────┘          │                     │
              │                   │                     │
    ┌─────────▼─────────────────────────────────────────▼────────┐
    │                        Tier 5 Detail                       │
    │                                                            │
    │  AGM (12)           OWN (5)        DIV (2)    PRX (2)     │
    │  ├ agenda_xml       ├ own_major    ├ detail   ├ detail    │
    │  ├ financials_xml   ├ own_total    └ history  └ direction │
    │  ├ personnel_xml    ├ own_treasury                        │
    │  ├ aoi_change_xml   ├ own_block    NEWS (1)               │
    │  ├ compensation_xml └ own_latest   └ news_check           │
    │  ├ treasury_share_xml                                     │
    │  ├ capital_reserve_xml                                    │
    │  ├ retirement_pay_xml                                     │
    │  ├ info / corrections / result / items                    │
    │  └ 각 파서 _xml / _pdf / _ocr fallback                    │
    └───────────────────────────────────────────────────────────┘
```

### 도메인별 요약

| 도메인 | 설명 | tool 수 |
|--------|------|---------|
| **AGM** | 주총 소집공고 파싱 -- 안건, 재무제표, 이사선임, 정관변경, 보수한도, 자기주식 등 | 14 |
| **OWN** | 지분 구조 -- 최대주주, 주식총수, 자사주, 5% 대량보유자 | 6 |
| **DIV** | 배당 분석 -- 배당 상세, 3개년 추이, 배당성향/수익률 | 4 |
| **PRX** | 프록시파이트 -- 위임장 권유 검색, 양측 비교 | 4 |
| **NEWS** | 후보자 부정 뉴스 검색 | 1 |
| **CORP** | 기업 식별 (ticker/corp_code 변환) | 1 |
| **GUIDE** | 전체 tool 사용 가이드 | 1 |
| **GOV** | 거버넌스 종합 리포트 (AGM+OWN+DIV 통합) | 1 |
| | **합계** | **33** |

---

## Fallback 파싱

AGM 안건 파서는 대부분 XML 단계에서 정상 파싱됩니다 (KOSPI 200 기준 평균 97%+ 정확도). 비표준 형식의 공시에 한해 PDF, OCR 순서로 fallback합니다.

```
_xml (DART API, 무료, <1초)  ← 대부분 여기서 완료
  ↓ 비표준 형식인 경우
_pdf (PDF 다운로드, 무료, 4초+)
  ↓ 그래도 안 되는 경우
_ocr (Upstage OCR API, 유료, 8초+)  ← 100% 정확도
```

AI가 결과 품질을 보고 자율적으로 다음 단계로 넘어갑니다. 사용자 개입 불필요.

<details>
<summary>KOSPI 200 파서별 정확도 (클릭하여 펼치기)</summary>

| 파서 | XML | PDF | OCR |
|------|-----|-----|-----|
| 안건 목록 | 99.5% | 98.0% | 100% |
| 재무상태표 | 97.4% | 97.9% | 100% |
| 손익계산서 | 100% | 95.7% | 100% |
| 이사/감사 선임 | 98.9% | 97.9% | 100% |
| 정관변경 | 97.8% | 99.0% | 100% |
| 보수한도 | 98.4% | 99.5% | 100% |
| 자기주식 | 93.6% | 100% | 100% |
| 자본준비금 | 100% | 100% | 100% |
| 퇴직금 | 93.3% | 86.7% | 86.7% |

</details>

---

## 의결권 행사 판단

파싱된 데이터를 기반으로 안건별 의결권 행사 방향을 판정합니다.

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
    tools/                 # 33개 tool (AGM/OWN/DIV/PRX/NEWS/CORP/GUIDE/GOV)
    dart/client.py         # DART API + KIND 크롤링 + 네이버 + rate limiter
  Dockerfile               # Fly.io 배포용 컨테이너
  fly.toml                 # Fly.io 설정 (nrt 리전, auto-suspend)
  wiki/                    # 도메인 지식 위키 (68페이지)
```

---

## 라이선스

[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) -- 비상업적 사용만 허용

본 프로젝트의 코드와 데이터를 사용할 때는 출처를 표시해야 하며, 상업적 목적으로 사용할 수 없습니다.
