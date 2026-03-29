# OpenProxy MCP (OPM)

[한국어 README](README_KR.md)

AI-powered MCP (Model Context Protocol) server that structures Korean AGM (Annual General Meeting) filings from DART into actionable, AI-ready data.

> **OpenProxy brings institutional-grade AGM intelligence to every investor — parsing shareholder meeting filings into structured, AI-ready data in seconds, not hours.**

## Why OpenProxy?

The world's largest asset managers are building proprietary AI systems for proxy voting analysis. OpenProxy democratizes this capability — any AI assistant connected via MCP can instantly access structured AGM data: agenda trees, financial statements, director candidates with career histories, charter amendments, and executive compensation analysis.

## Data Source

- [OpenDART API](https://opendart.fss.or.kr/) — Korea Financial Supervisory Service Electronic Disclosure System

## MCP Tools (31 tools)

```
agm_steward(ticker)          <- Orchestrator (one-call summary)
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
| Charter changes | 97.8% | 99.0% | 100% |
| Compensation | 98.4% | 99.5% | 100% |
| Treasury shares | 93.6% | - | - |
| Capital reserve | 100% | - | - |
| Retirement pay | 93.3% | - | - |

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
|  shareholder.py - MCP Tool Layer (31 tools)              |
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
    shareholder.py    # 31 MCP tools + formatters
    parser.py         # XML parsers - parse_*_xml()
    pdf_parser.py     # PDF parsers - parse_*_pdf() + Upstage OCR fallback
  dart/
    client.py         # OpenDART API + web PDF download (rate limiter)
  llm/
    client.py         # LLM fallback (Claude Sonnet / OpenAI)
```

## Installation

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment variables
cp .env.example .env
# Edit .env with your API keys (OPENDART_API_KEY required)
```

## Usage

```bash
# MCP server (stdio - Claude Code/Desktop)
python -m open_proxy_mcp

# SSE mode (web clients)
python -m open_proxy_mcp --sse
```

### Environment Variables (.env)

```
OPENDART_API_KEY=...          # Required - DART API key
OPENDART_API_KEY_2=...        # Optional - backup key (auto-switch on rate limit)
ANTHROPIC_API_KEY=...         # Optional - LLM fallback (Claude)
OPENAI_API_KEY=...            # Optional - LLM fallback (OpenAI)
UPSTAGE_API_KEY=...           # Optional - OCR fallback (Upstage Document Parse)
```

### Claude Code (.mcp.json)

```json
{
  "mcpServers": {
    "open-proxy-mcp": {
      "command": "python",
      "args": ["-m", "open_proxy_mcp"],
      "cwd": "/path/to/open-proxy-mcp"
    }
  }
}
```

### First Use

Tell your AI: "Call `agm_guide` first to read the usage guide."

## License

[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) - NonCommercial use only
