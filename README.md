# OpenProxy MCP (OPM)

[한국어 README](README_KR.md)

AI-powered MCP (Model Context Protocol) server that structures Korean AGM (Annual General Meeting) filings from DART into actionable, AI-ready data.

> **OpenProxy brings institutional-grade AGM intelligence to every investor — parsing shareholder meeting filings into structured, AI-ready data in seconds, not hours.**

![OpenProxy MCP Comparison](screenshot/openproxy_mcp_compare_v2.png)

## Why OpenProxy?

The rise of passive investing is concentrating ownership in permanent, price-insensitive strategies — making proxy voting a critical mechanism for governance and long-term value creation. Yet the process remains manual, fragmented, and inaccessible. Institutional investors rely on costly internal teams and third-party advisors, while most market participants lack access to structured, decision-ready information.

OpenProxy bridges this gap. By transforming unstructured AGM disclosures into AI-ready structured data via MCP, it enables any investor or analyst to make informed, consistent proxy voting decisions at scale — without proprietary infrastructure or advisory dependencies.

## Why Parsing Matters

A raw DART AGM filing is a 100+ page HTML document mixing regulatory boilerplate, financial footnotes, and actual voting items. Finding "how much is the CEO getting paid?" requires reading through thousands of lines.

**Before (raw DART filing):**
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

**After (OpenProxy):**
```json
{
  "current": {"headcount": "8(5)", "limit": "450억원", "limitAmount": 45000000000},
  "prior": {"actualPaid": "287억원", "limit": "360억원"},
  "priorUtilization": 79.7
}
```

One API call. Structured. Ready for analysis.

## Data Source

- [OpenDART API](https://opendart.fss.or.kr/) — Korea Financial Supervisory Service Electronic Disclosure System

## MCP Tools (40 tools)

```
agm(ticker)                  <- Orchestrator (one-call summary)
|
+-- agm_search(ticker)            Search AGM notices
+-- agm_info(rcept_no)            Meeting info (date/location)
+-- agm_agenda_xml(rcept_no)      Agenda tree with sub-items
+-- agm_corrections(rcept_no)     Correction before/after
|
+-- agm_items(rcept_no)           Raw agenda detail blocks
|   +-- agm_financials_xml        Financial statements (BS/IS)
|   +-- agm_personnel_xml         Director/auditor appointments
|   +-- agm_aoi_change_xml        Articles of incorporation changes
|   +-- agm_compensation_xml      Executive compensation limits
|   +-- agm_treasury_share_xml    Treasury share hold/dispose/cancel
|   +-- agm_capital_reserve_xml   Capital reserve reduction
|   +-- agm_retirement_pay_xml    Retirement pay regulation changes
|
+-- agm_document(rcept_no)        Raw document text
+-- agm_guide()                   AI assistant usage guide

Agenda type -> Tool mapping:
  Financial statements    -> agm_financials_xml
  Director appointments   -> agm_personnel_xml
  Charter amendments      -> agm_aoi_change_xml
  Compensation limits     -> agm_compensation_xml
  Treasury shares         -> agm_treasury_share_xml
  Capital reserve         -> agm_capital_reserve_xml
  Retirement regulations  -> agm_retirement_pay_xml
  Other                   -> agm_items (raw blocks)

own(ticker)                  <- Ownership orchestrator
|
+-- own_major(ticker, year)       Largest shareholder + related parties
+-- own_total(ticker, year)       Total shares / treasury / float / minority
+-- own_treasury(ticker, year)    Treasury stock baseline (annual report)
+-- own_treasury_tx(ticker)       Acquisition/disposal/trust decisions
+-- own_block(ticker)             5% block holders (purpose from filing)
+-- own_latest(ticker)            All shareholders latest snapshot
```

### 3-Tier Fallback (XML -> PDF -> OCR)

Each parser tool has `_xml`, `_pdf`, and `_ocr` variants. The AI autonomously decides when to escalate:

```
AI calls agm_personnel_xml(rcept_no)
  -> Good result -> Answer user
  -> Empty or poor quality
  -> AI: "XML parsing incomplete. Try PDF?" -> agm_personnel_pdf(rcept_no)
      -> Good -> Answer
      -> Still failing -> AI: "Try OCR?" -> agm_personnel_ocr(rcept_no)
```

| Tier | Source | Speed | Accuracy |
|------|--------|-------|----------|
| `_xml` | DART API (HTML/XML) | Fast | 98%+ |
| `_pdf` | PDF download + opendataloader | 4s+ | 98%+ |
| `_ocr` | Upstage OCR API | Slowest | 100% |

## Parsing Performance (KOSPI 200, agenda-tree-based)

| Parser | XML | PDF | OCR |
|--------|-----|-----|-----|
| Agenda | 99.5% | 98.0% | 100% |
| Financials BS | 97.4% | 97.9% | 100% |
| Financials IS | 100% | 95.7% | 100% |
| Personnel | 98.9% | 97.9% | 100% |
| AOI changes | 97.8% | 99.0% | 100% |
| Compensation | 98.4% | 99.5% | 100% |
| Treasury shares | 93.6% | 100% | 100% |
| Capital reserve | 100% | 100% | 100% |
| Retirement pay | 93.3% | 86.7% | 86.7% |

## Data Flow

```
+---------------------------------------------------------+
|  Tier 1: XML Parsing (default, fast)                     |
|                                                          |
|  DART API (document.xml ZIP)                             |
|    -> parser.py (XML parsers)                            |
|       bs4(lxml) + regex fallback                         |
+----------------------------+-----------------------------+
                             | on failure
+----------------------------v-----------------------------+
|  Tier 2: PDF Parsing (slow, 4s+)                         |
|                                                          |
|  DART Web -> PDF download -> opendataloader -> markdown  |
|    -> pdf_parser.py (PDF parsers)                        |
+----------------------------+-----------------------------+
                             | on failure
+----------------------------v-----------------------------+
|  Tier 3: OCR (slowest, requires UPSTAGE_API_KEY)         |
|                                                          |
|  Keyword page detection -> PDF page extraction           |
|    -> Upstage OCR API -> markdown -> PDF parser re-run   |
+---------------------------------------------------------+
                             |
                             v
+---------------------------------------------------------+
|  shareholder.py - MCP Tool Layer (40 tools)              |
|                                                          |
|  agm_*_xml  - Tier 1                                     |
|  agm_*_pdf  - Tier 2 (AI decides autonomously)           |
|  agm_*_ocr  - Tier 3 (AI decides autonomously)           |
|  agm_guide  - AI usage guide + case definitions          |
+---------------------------------------------------------+
```

## Project Structure

```
open_proxy_mcp/
  server.py           # FastMCP server entry point (stdio + SSE)
  tools/
    shareholder.py    # 33 AGM tools + formatters (incl. agm_result)
    ownership.py      # 7 ownership tools + formatters
    parser.py         # XML parsers - parse_*_xml()
    pdf_parser.py     # PDF parsers - parse_*_pdf() + Upstage OCR fallback
  dart/
    client.py         # OpenDART API + web PDF download (rate limiter)
  llm/
    client.py         # LLM fallback (Claude Sonnet / OpenAI)
```

## Quick Start

```bash
git clone https://github.com/MarcoYou/open-proxy-mcp.git
cd open-proxy-mcp
uv sync                    # creates .venv + installs dependencies
cp .env.example .env
```

Edit `.env` and add your DART API key (get one free at [opendart.fss.or.kr](https://opendart.fss.or.kr)):

```
OPENDART_API_KEY=your_key_here
```

### Connect to Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

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

Restart Claude Desktop. Start a new chat and say: **"Call agm_guide first."**

### Connect to Claude Code

Add `.mcp.json` in your project root:

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

### Optional API Keys (.env)

```
OPENDART_API_KEY=...          # Required - get free at opendart.fss.or.kr
OPENDART_API_KEY_2=...        # Optional - backup key (auto-switch on rate limit)
UPSTAGE_API_KEY=...           # Optional - OCR fallback (Upstage Document Parse)
```

## License

[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) - NonCommercial use only
