# OpenProxy MCP (OPM)

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-green.svg)](https://modelcontextprotocol.io/)
[![Tools](https://img.shields.io/badge/tools-33-orange.svg)](#mcp-tool-33개)

[English README](README_ENG.md)

![OpenProxy MCP 비교](screenshot/openproxy_mcp_compare_v2.png)

> **주주총회 공시 100페이지를 몇 초 만에 구조화된 데이터로 -- 기관투자자급 의결권 분석을 모든 투자자에게.**

---

## 왜 OpenProxy인가?

### 문제

패시브 투자의 급격한 확대로 소수의 자산운용사에 의결권이 집중되고 있습니다. 의결권 행사는 기업 거버넌스와 장기 가치 창출의 핵심 수단이 되었지만, 정작 의결권 분석 과정은 여전히 수작업에 의존하고 있습니다.

- DART 소집공고는 100페이지 이상의 비정형 HTML 문서
- 규제 양식, 재무 주석, 실제 의결 안건이 뒤섞여 있음
- 기관투자자는 내부 팀과 외부 자문사(ISS, Glass Lewis)에 의존
- 개인투자자와 소규모 운용사는 구조화된 의결권 정보에 접근 불가

### 해결

OpenProxy는 DART 전자공시 문서를 AI가 바로 읽을 수 있는 구조화 데이터로 변환합니다. MCP(Model Context Protocol)를 통해 Claude, GPT 등 AI 모델이 직접 공시 데이터를 조회하고 분석할 수 있으므로, 누구나 일관되고 체계적인 의결권 분석이 가능해집니다.

주총(AGM) 분석뿐 아니라 지분 구조(OWN), 배당 정책(DIV), 뉴스 검증(NEWS)까지 -- 거버넌스 분석에 필요한 데이터를 하나의 MCP 서버로 통합 제공합니다.

---

## 파싱이 왜 중요한가

DART 소집공고는 사람이 읽기 위해 설계된 문서입니다. "CEO 보수한도가 얼마인가?" 같은 단순한 질문에도 수천 줄의 HTML을 읽어야 합니다.

**Before -- 원본 DART 공시:**
```
가. 이사의 수ㆍ보수총액 내지 최고 한도액
당 기(제58기, 2026년)
이사의 수 (사외이사수) 8(    5    )
보수총액 또는 최고한도액 450억원
전 기(제57기, 2025년)
이사의 수 (사외이사수) 10(    6    )
실제 지급된 보수총액 287억원
최고한도액 360억원
※ 당기(제58기) 보수 한도 총액 450억 : 일반보수 260억 ...
```

**After -- OpenProxy 구조화 결과:**
```json
{
  "current": {"headcount": "8(5)", "limit": "450억원", "limitAmount": 45000000000},
  "prior": {"actualPaid": "287억원", "limit": "360억원"},
  "priorUtilization": 79.7
}
```

API 한 번 호출. 구조화 완료. 바로 분석 가능.

---

## 주요 기능

### AGM -- 주주총회 분석 (12 tools)

주주총회 소집공고를 안건 유형별로 자동 파싱합니다. 재무제표, 이사/감사 선임, 정관변경, 보수한도, 자기주식, 자본준비금, 퇴직금 규정까지 7개 안건 유형을 지원하며, 각 파서는 XML/PDF/OCR 3단계 fallback을 제공합니다. KIND 크롤링을 통한 주총 결과(의결권 행사 현황) 조회도 포함됩니다.

### OWN -- 지분 구조 분석 (5 tools)

최대주주 및 특수관계인, 주식총수/자사주/유통주식/소액주주, 자기주식 취득/처분/신탁 이력, 5% 대량보유자(보유목적 원문 파싱)까지 -- 기업의 지분 구조를 종합적으로 파악합니다. 사업보고서, 주요사항보고서, 대량보유 공시를 모두 활용합니다.

### DIV -- 배당 분석 (2 tools)

배당 결정 공시 검색, 상세 내역(보통주/우선주 구분), 3개년 배당 추이를 제공합니다. 배당수익률, 배당성향, 주당배당금 등 투자자에게 필요한 지표를 구조화합니다.

### PRX -- 프록시파이트 분석 (2 tools)

위임장 권유 공시를 검색하고, 회사측·주주측 양측의 후보 목록과 의결권 행사 방향을 비교합니다. 프록시파이트 상황 감지 및 경영권 분쟁 현황 파악에 활용됩니다.

### NEWS -- 뉴스 검증 (1 tool)

이사/감사 후보자의 부정적 뉴스를 자동 검색합니다. 의결권 행사 판단 시 후보자 리스크 확인에 활용됩니다. 네이버 뉴스 API를 통해 실시간 검색하며, 부정 키워드 매칭 결과를 반환합니다.

---

## Tier 체계

OPM의 33개 tool은 5단계 실행 계층으로 구성됩니다. AI는 상위 tier부터 순서대로 호출하며, 필요에 따라 하위 Detail tool로 내려갑니다.

| Tier | 역할 | tool 수 |
|------|------|---------|
| **Tier 1 Entity** | 기업 식별 (ticker 조회) | 1 |
| **Tier 2 Context** | 전체 tool 사용 가이드 로드 | 1 |
| **Tier 3 Search** | 도메인별 공시 검색 (AGM/DIV/PRX) | 3 |
| **Tier 4 Orchestrate** | 도메인별 종합 분석 + 체인 실행 | 6 |
| **Tier 5 Detail** | 안건별 세부 파서 + 지분/배당/프록시 상세 | 22 |

## MCP Tool (33개)

### Tier 1 -- Entity (1 tool)

```
corp_identifier(name_or_ticker)         <- 기업명/종목코드 → corp_code 변환
```

### Tier 2 -- Context (1 tool)

```
tool_guide()                            <- 전체 tool 사용법 + fallback 전략 가이드
```

### Tier 3 -- Search (3 tools)

```
agm_search(ticker)                      <- 소집공고 검색 (연도/rcept_no 목록)
div_search(ticker)                      <- 배당 결정 공시 검색
prx_search(ticker)                      <- 위임장 권유 공시 검색
```

### Tier 4 -- Orchestrate (6 tools)

```
agm_pre_analysis(ticker)                <- 주총 사전 분석 (안건 요약 + 판단)
agm_post_analysis(ticker)               <- 주총 사후 분석 (결과 + 참석률)
own_full_analysis(ticker)               <- 지분 구조 종합 분석
div_full_analysis(ticker)               <- 배당 종합 분석 (추이 + 적정성)
prx_fight(ticker)                       <- 프록시파이트 감지 + 양측 비교
governance_report(ticker)               <- AGM + OWN + DIV 3도메인 통합 리포트
```

### Tier 5 -- Detail (22 tools)

#### AGM 안건 파서 (12 tools, _xml 기준)

```
agm_agenda_xml(rcept_no)                안건 트리 (세부의안 포함)
agm_financials_xml(rcept_no)            재무제표 (BS/IS)
agm_personnel_xml(rcept_no)             이사/감사 선임/해임
agm_aoi_change_xml(rcept_no)            정관변경 (변경전/변경후)
agm_compensation_xml(rcept_no)          보수한도 (당기/전기 + 소진율)
agm_treasury_share_xml(rcept_no)        자기주식 보유/처분/소각
agm_capital_reserve_xml(rcept_no)       자본준비금 감소
agm_retirement_pay_xml(rcept_no)        퇴직금 규정 개정
agm_info(rcept_no)                      회의 정보 (일시/장소/정족수)
agm_corrections(rcept_no)               정정 전/후 비교
agm_result(ticker)                      의결권 행사 결과 (KIND 크롤링)
agm_items(rcept_no)                     안건 본문 블록 (범용 fallback)

각 파서에 _xml / _pdf / _ocr 3단계 fallback 지원
```

#### OWN 지분 상세 (5 tools)

```
own_major(ticker, year)                 최대주주 + 특수관계인 + 변동이력
own_total(ticker, year)                 주식총수 / 자사주 / 유통주식 / 소액주주
own_treasury_tx(ticker)                 취득결정 / 처분결정 / 신탁체결 / 해지
own_block(ticker)                       5% 대량보유 (보유목적 원문 파싱)
own_latest(ticker)                      전 주주 최신 스냅샷
```

#### DIV 배당 상세 (2 tools)

```
div_detail(ticker)                      배당 상세 (보통주/우선주)
div_history(ticker)                     배당 추이 (3개년 + 성향/수익률)
```

#### PRX 프록시 상세 (2 tools)

```
prx_detail(rcept_no)                    위임장 공시 상세 파싱 (후보/안건)
prx_direction(rcept_no)                 의결권 행사 방향 추출 (회사측/주주측)
```

#### NEWS (1 tool)

```
news_check(name, company)               후보자 부정 뉴스 검색
```

---

**예시: `own("삼성전자")` -- 삼성전자 지분 구조 조회**

![삼성전자 지분 구조](screenshot/samsung_ownership_kr.png)

---

## 3-Tier Fallback (XML - PDF - OCR)

DART 공시 문서의 형식은 기업마다, 연도마다 다릅니다. 하나의 파싱 방식으로 모든 경우를 커버할 수 없기 때문에, OpenProxy는 3단계 fallback 구조를 채택했습니다.

각 안건 파서(financials, personnel, aoi_change, compensation, treasury_share, capital_reserve, retirement_pay, agenda)에 `_xml`, `_pdf`, `_ocr` 변형이 존재하며, AI가 결과 품질을 판단하여 자율적으로 다음 단계로 전환합니다.

```
AI가 agm_personnel_xml(rcept_no) 호출
  -> 정상 결과 -> 답변
  -> 빈 결과 or 품질 이슈
     -> AI: "XML 파싱이 불완전합니다. PDF로 재시도합니다."
     -> agm_personnel_pdf(rcept_no) 호출
        -> 정상 -> 답변
        -> 실패 -> agm_personnel_ocr(rcept_no) 호출
```

| 단계 | 소스 | 속도 | 정확도 | 비용 |
|------|------|------|--------|------|
| `_xml` | DART API (HTML/XML) | 빠름 | 98%+ | 무료 |
| `_pdf` | PDF 다운로드 + opendataloader | 4초+ | 98%+ | 무료 |
| `_ocr` | Upstage Document Parse API | 가장 느림 | 100% | API 크레딧 |

핵심 설계: AI가 스스로 판단합니다. 개발자가 fallback 로직을 하드코딩하는 대신, AI 모델이 _xml 결과를 보고 "이 데이터가 불완전하다"고 판단하면 _pdf를 호출하고, 그래도 안 되면 _ocr로 넘어갑니다. 이것이 MCP의 강점입니다.

---

## Proxy Voting Decision Tree

OpenProxy는 단순 데이터 제공을 넘어, 안건 유형별 의결권 행사 판단 기준을 내장하고 있습니다. AI가 파싱된 데이터를 기반으로 각 안건에 대해 3단계 판정을 제시합니다.

| 판정 | 의미 | 설명 |
|------|------|------|
| **FOR** | 찬성 권유 | 데이터 기준 주주 이익에 부합 |
| **AGAINST** | 반대 권유 | 데이터 기준 주주 이익 침해 의심 |
| **REVIEW** | 검토 필요 | 자동 판단 불가, 추가 분석 필요 |

### 안건별 판단 예시

- **재무제표**: 감사의견 적정 -> FOR / 한정/부적정 -> AGAINST / 배당성향 < 10% -> REVIEW
- **이사 선임**: 사외이사 독립성 미달 -> AGAINST / 겸직 3개+ -> REVIEW / 부정 뉴스 -> REVIEW
- **보수한도**: 소진율 < 30%이고 한도 인상 -> AGAINST / 전기 대비 50%+ 인상 -> REVIEW
- **정관변경**: 집중투표 배제 조항 삭제 -> FOR / 이사 정원 축소(방어 전술 의심) -> REVIEW
- **자기주식**: 목적 = 소각 -> FOR / 목적 = 경영권 방어 -> REVIEW
- **자본준비금**: 감액배당 목적 -> FOR / 기타 -> REVIEW

상세 판단 기준은 `open_proxy_mcp/AGM_TOOL_RULE.md`에 정의되어 있으며, 집중투표 심화분석(소수주주 진입 허들 계산) 등 고급 시나리오도 포함합니다.

---

## 파싱 성능 (KOSPI 200)

KOSPI 200 구성종목(199개)을 대상으로 벤치마크한 파서별 정확도입니다.

| 파서 | XML | PDF | OCR |
|------|-----|-----|-----|
| 안건 목록 (Agenda) | 99.5% | 98.0% | 100% |
| 재무상태표 (BS) | 97.4% | 97.9% | 100% |
| 손익계산서 (IS) | 100% | 95.7% | 100% |
| 이사/감사 선임 (Personnel) | 98.9% | 97.9% | 100% |
| 정관변경 (AOI) | 97.8% | 99.0% | 100% |
| 보수한도 (Compensation) | 98.4% | 99.5% | 100% |
| 자기주식 (Treasury) | 93.6% | 100% | 100% |
| 자본준비금 (Capital Reserve) | 100% | 100% | 100% |
| 퇴직금 (Retirement Pay) | 93.3% | 86.7% | 86.7% |

XML 단독으로도 대부분 97%+ 정확도를 달성하며, PDF/OCR fallback을 통해 100%에 수렴합니다.

---

## 데이터 소스

| 소스 | 용도 | 비고 |
|------|------|------|
| [DART OpenAPI](https://opendart.fss.or.kr/) | 소집공고, 사업보고서, 대량보유 공시 | 필수 (무료 API 키) |
| [KRX KIND](https://kind.krx.co.kr/) | 주총 의결권 행사 결과 | 웹 크롤링 |
| [KRX Open API](https://data.krx.co.kr/) | 종목 코드, 시가총액 등 | 선택 (무료 API 키) |
| [네이버 뉴스 API](https://developers.naver.com/) | 후보자 부정 뉴스 검색 | 선택 (무료 API 키) |
| [네이버 금융](https://finance.naver.com/) | 주가, 배당 시세 | 웹 크롤링 |

---

## 프로젝트 구조

```
open-proxy-mcp/
  open_proxy_mcp/
    server.py              # FastMCP 서버 진입점 (stdio + SSE)
    tools/
      __init__.py          # register_all_tools() -- 자동 등록
      shareholder.py       # AGM tool (파서 + 포매터)
      ownership.py         # OWN tool (DART API + 포매터)
      dividend.py          # DIV tool (배당 공시)
      proxy.py             # PRX tool (프록시파이트 분석)
      news.py              # NEWS tool 1개
      formatters.py        # 공유 포매터 함수
      errors.py            # 공통 에러 핸들러
      parser.py            # XML 파서 (bs4 + regex)
      pdf_parser.py        # PDF 파서 + Upstage OCR fallback
    dart/
      client.py            # DART API + KIND 크롤링 + 네이버 + rate limiter
    llm/
      client.py            # LLM fallback (Claude / OpenAI)
    AGM_TOOL_RULE.md       # AGM tool 규칙 + Proxy Voting Decision Tree
    OWN_TOOL_RULE.md       # OWN tool 규칙
    DIV_TOOL_RULE.md       # DIV tool 규칙
  wiki/                    # 도메인 지식 위키 (68페이지)
  screenshot/              # README 스크린샷
  pyproject.toml           # 패키지 설정
  .env.example             # 환경변수 템플릿
```

---

## 빠른 시작

### 1. 설치

```bash
git clone https://github.com/MarcoYou/open-proxy-mcp.git
cd open-proxy-mcp
uv sync                    # .venv 생성 + 의존성 설치
cp .env.example .env       # 환경변수 파일 생성
```

### 2. 환경변수 설정

`.env` 파일을 열고 API 키를 입력합니다.

```bash
# 필수 -- opendart.fss.or.kr에서 무료 발급
OPENDART_API_KEY=발급받은_키

# 선택 -- 보조 키 (분당 1,000회 제한 도달 시 자동 전환)
OPENDART_API_KEY_2=보조_키

# 선택 -- OCR fallback용 (upstage.ai에서 발급)
UPSTAGE_API_KEY=업스테이지_키

# 선택 -- KRX Open API (data.krx.co.kr에서 발급)
KRX_API_KEY=KRX_키

# 선택 -- 네이버 뉴스 검색 API (developers.naver.com에서 발급)
NAVER_SEARCH_API_CLIENT_ID=네이버_클라이언트_ID
NAVER_SEARCH_API_CLIENT_SECRET=네이버_클라이언트_시크릿
```

OPENDART_API_KEY만 있으면 AGM/OWN/DIV 핵심 기능을 모두 사용할 수 있습니다. 나머지는 부가 기능(OCR fallback, 뉴스 검색 등)에 필요합니다.

### 3. Editable Install (권장)

Claude Desktop은 `cwd`를 자유롭게 설정할 수 없는 경우가 있습니다. editable install을 해두면 어디서든 `open_proxy_mcp` 모듈을 실행할 수 있습니다.

```bash
uv pip install -e .
```

### 4. Claude Desktop 연결

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

`/path/to/open-proxy-mcp`를 실제 경로로 변경하세요. Claude Desktop을 재시작하면 MCP 서버가 자동으로 연결됩니다.

### 5. Claude Code 연결

프로젝트 루트에 `.mcp.json` 파일을 추가합니다:

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

### 6. 선택 의존성

```bash
uv sync                                  # 코어만 (XML 파싱)
uv pip install -e ".[pdf]"               # + PDF/OCR fallback
uv pip install -e ".[llm]"               # + LLM fallback (Claude/OpenAI)
uv pip install -e ".[all]"               # 전부 설치
```

### 7. 첫 사용

새 대화를 열고 다음과 같이 말하세요:

> **"agm_manual을 먼저 호출해줘"**

`agm_manual`은 AI에게 전체 tool 사용법, 안건 유형별 파서 매핑, fallback 전략, 의결권 판단 기준을 알려주는 가이드입니다. 이것을 먼저 호출하면 AI가 최적의 방식으로 tool을 활용할 수 있습니다.

이후 자연어로 질문하면 됩니다:

- "삼성전자 주주총회 안건 분석해줘"
- "KB금융 사외이사 후보 독립성 검토해줘"
- "현대차 보수한도 적정성 판단해줘"
- "삼성전자 지분 구조 보여줘"
- "SK하이닉스 배당 추이 알려줘"

---

## 라이선스

[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) -- 비상업적 사용만 허용

본 프로젝트의 코드와 데이터를 사용할 때는 출처를 표시해야 하며, 상업적 목적으로 사용할 수 없습니다. 상업적 이용이 필요한 경우 별도 문의 바랍니다.
