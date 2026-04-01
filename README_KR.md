# OpenProxy MCP (OPM)

[English README](README.md)

DART 주주총회 소집공고를 AI가 바로 활용할 수 있는 구조화된 데이터로 변환하는 MCP 서버.

> **주주총회 공시 문서를 몇 초 만에 구조화된 데이터로 — 수작업 분석이 아닌 AI 자동 파싱.**

![OpenProxy MCP 비교](screenshot/openproxy_mcp_compare.png)

## 왜 OpenProxy인가?

패시브 투자의 확대로 의결권 행사의 중요성이 커지고 있지만, 주주총회 분석은 여전히 수작업에 의존합니다. 기관투자자는 내부 팀과 외부 자문사에 의존하고, 대부분의 투자자는 구조화된 의결권 정보에 접근할 수 없습니다.

OpenProxy는 DART 공시 문서를 AI가 바로 읽을 수 있는 구조화 데이터로 변환하여, 누구나 일관되고 체계적인 의결권 분석을 할 수 있게 합니다.

## 파싱이 왜 중요한가

DART 소집공고는 100페이지 이상의 HTML 문서로, 규제 양식/재무 주석/실제 의결 안건이 뒤섞여 있습니다. "CEO 보수한도가 얼마인지" 알려면 수천 줄을 읽어야 합니다.

**원본 (DART 공시):**
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

**OpenProxy 결과:**
```json
{
  "current": {"headcount": "8(5)", "limit": "450억원", "limitAmount": 45000000000},
  "prior": {"actualPaid": "287억원", "limit": "360억원"},
  "priorUtilization": 79.7
}
```

API 한 번 호출. 구조화 완료. 바로 분석 가능.

## 데이터 소스

- [OpenDART API](https://opendart.fss.or.kr/) - 금융감독원 전자공시시스템

## MCP Tool (40개)

```
agm(ticker)                  <- 종합 오케스트레이터
|
+-- agm_search(ticker)            소집공고 검색
+-- agm_info(rcept_no)            회의 정보 (일시/장소)
+-- agm_agenda_xml(rcept_no)      안건 트리 (세부의안 포함)
+-- agm_corrections(rcept_no)     정정 전/후 비교
|
+-- agm_items(rcept_no)           안건 본문 블록 (범용)
|   +-- agm_financials_xml        재무제표 (BS/IS)
|   +-- agm_personnel_xml         이사/감사 선임·해임
|   +-- agm_aoi_change_xml        정관변경 (변경전/변경후)
|   +-- agm_compensation_xml      보수한도 (당기/전기)
|   +-- agm_treasury_share_xml    자기주식 보유/처분/소각
|   +-- agm_capital_reserve_xml   자본준비금 감소
|   +-- agm_retirement_pay_xml    퇴직금 규정 개정
|
+-- agm_document(rcept_no)        원문 텍스트
+-- agm_guide()                   AI 사용 가이드

안건 유형별 tool 매핑:
  재무제표 승인      -> agm_financials_xml
  이사/감사 선임     -> agm_personnel_xml
  정관변경          -> agm_aoi_change_xml
  보수한도 승인      -> agm_compensation_xml
  자기주식          -> agm_treasury_share_xml
  자본준비금 감소     -> agm_capital_reserve_xml
  퇴직금 규정       -> agm_retirement_pay_xml
  기타             -> agm_items (raw 블록)

own(ticker)                      <- 지분 구조 오케스트레이터
|
+-- own_major(ticker, year)           최대주주 + 특수관계인 + 변동이력
+-- own_total(ticker, year)           주식총수 / 자사주 / 유통주식 / 소액주주
+-- own_treasury(ticker, year)        자기주식 기말 보유 (사업보고서 기준)
+-- own_treasury_tx(ticker)           취득결정 / 처분결정 / 신탁체결 / 해지
+-- own_block(ticker)                 5% 대량보유 (보유목적 원문 파싱)
+-- own_latest(ticker)                전 주주 최신 스냅샷
```

### 3단계 Fallback (XML -> PDF -> OCR)

각 파서 tool에 `_xml`, `_pdf`, `_ocr` 변형이 있으며, AI가 자율적으로 판단하여 단계를 올립니다:

```
AI가 agm_personnel_xml(rcept_no) 호출
  -> 정상 결과 -> 답변
  -> 빈 결과 or 품질 이슈
  -> AI: "XML 파싱이 불완전합니다. PDF로 재시도할까요?"
  -> agm_personnel_pdf(rcept_no) 호출
      -> 정상 -> 답변
      -> 실패 -> AI: "OCR로 시도할까요?" -> agm_personnel_ocr(rcept_no)
```

| 단계 | 소스 | 속도 | 정확도 |
|------|------|------|--------|
| `_xml` | DART API (HTML/XML) | 빠름 | 98%+ |
| `_pdf` | PDF 다운로드 + opendataloader | 4초+ | 98%+ |
| `_ocr` | Upstage OCR API | 가장 느림 | 100% |

## 파싱 성능 (KOSPI 200, 안건 tree 기반)

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

## 데이터 흐름

```
+---------------------------------------------------------+
|  1단계: XML 파싱 (기본, 빠름)                             |
|                                                          |
|  DART API (document.xml ZIP)                             |
|    -> parser.py (XML 파서)                               |
|       bs4(lxml) + regex fallback                         |
+----------------------------+-----------------------------+
                             | 실패 시
+----------------------------v-----------------------------+
|  2단계: PDF 파싱 (느림, 4초+)                             |
|                                                          |
|  DART 웹 -> PDF 다운로드 -> opendataloader -> 마크다운     |
|    -> pdf_parser.py (PDF 파서)                           |
+----------------------------+-----------------------------+
                             | 실패 시
+----------------------------v-----------------------------+
|  3단계: OCR (가장 느림, UPSTAGE_API_KEY 필요)              |
|                                                          |
|  키워드로 페이지 특정 -> PDF 페이지 추출                    |
|    -> Upstage OCR API -> 마크다운 -> PDF 파서 재실행       |
+---------------------------------------------------------+
                             |
                             v
+---------------------------------------------------------+
|  shareholder.py - MCP Tool 레이어 (40개)                  |
|                                                          |
|  agm_*_xml  - 1단계                                      |
|  agm_*_pdf  - 2단계 (AI 자율 판단)                        |
|  agm_*_ocr  - 3단계 (AI 자율 판단)                        |
|  agm_guide  - AI 사용 가이드 + 성공 기준                   |
+---------------------------------------------------------+
```

## 프로젝트 구조

```
open_proxy_mcp/
  server.py           # FastMCP 서버 진입점 (stdio + SSE)
  tools/
    shareholder.py    # AGM tool 33개 + 포매터 (agm_result 포함)
    ownership.py      # 지분 구조 tool 7개 + 포매터
    parser.py         # XML 파서 - parse_*_xml()
    pdf_parser.py     # PDF 파서 - parse_*_pdf() + Upstage OCR fallback
  dart/
    client.py         # OpenDART API + 웹 PDF 다운로드 (rate limiter)
  llm/
    client.py         # LLM fallback (Claude Sonnet / OpenAI)
```

## 빠른 시작

```bash
git clone https://github.com/MarcoYou/open-proxy-mcp.git
cd open-proxy-mcp
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

`.env` 파일에 DART API 키 입력 ([opendart.fss.or.kr](https://opendart.fss.or.kr)에서 무료 발급):

```
OPENDART_API_KEY=발급받은_키
```

### Claude Desktop 연결

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

Claude Desktop 재시작 후 새 대화에서: **"agm_guide를 먼저 호출해줘"**

### Claude Code 연결

프로젝트 루트에 `.mcp.json` 추가:

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

### 선택 API 키 (.env)

```
OPENDART_API_KEY=...          # 필수 - opendart.fss.or.kr에서 무료 발급
OPENDART_API_KEY_2=...        # 선택 - 보조 키 (속도 제한 시 자동 전환)
UPSTAGE_API_KEY=...           # 선택 - OCR fallback (Upstage Document Parse)
```

## 라이선스

[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) - 비상업적 사용만 허용
