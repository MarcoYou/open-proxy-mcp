# OpenProxy MCP (OPM)

[한국어 README](README_KR.md)

AI-powered MCP (Model Context Protocol) server that structures Korean AGM (Annual General Meeting) filings from DART into actionable, AI-ready data.

> **OpenProxy brings institutional-grade AGM intelligence to every investor -- parsing shareholder meeting filings into structured, AI-ready data in seconds, not hours.**

## Why OpenProxy?

The rise of passive investing is concentrating ownership in permanent, price-insensitive strategies -- making proxy voting a critical mechanism for governance and long-term value creation. Yet the process remains manual, fragmented, and inaccessible.

OpenProxy bridges this gap. By transforming unstructured AGM disclosures into AI-ready structured data via MCP, it enables any investor or analyst to make informed, consistent proxy voting decisions at scale.

## Why Parsing Matters

A raw DART AGM filing is a 100+ page HTML document mixing regulatory boilerplate, financial footnotes, and actual voting items.

**Before (raw DART filing):**
```
가. 이사의 수ㆍ보수총액 내지 최고 한도액
당 기(제58기, 2026년)
이사의 수 (사외이사수) 8(    5    )
보수총액 또는 최고한도액 450억원
...
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

- [OpenDART API](https://opendart.fss.or.kr/) -- Korea Financial Supervisory Service Electronic Disclosure System
- [KRX KIND](https://kind.krx.co.kr/) -- Korea Exchange Information Disclosure (AGM voting results)

## MCP Tools (40 tools)

### AGM (33 tools) -- Shareholder Meeting Analysis

```
agm(ticker)                      <- Orchestrator (one-call summary)
|
+-- agm_search(ticker)                Search AGM notices
+-- agm_info(rcept_no)                Meeting info (date/location)
+-- agm_agenda_xml(rcept_no)          Agenda tree with sub-items
+-- agm_corrections(rcept_no)         Correction before/after
+-- agm_result(ticker)                Voting results (KIND crawling)
|
+-- agm_items(rcept_no)               Raw agenda detail blocks
|   +-- agm_financials_xml            Financial statements (BS/IS)
|   +-- agm_personnel_xml             Director/auditor appointments
|   +-- agm_aoi_change_xml            Articles of incorporation changes
|   +-- agm_compensation_xml          Executive compensation limits
|   +-- agm_treasury_share_xml        Treasury share hold/dispose/cancel
|   +-- agm_capital_reserve_xml       Capital reserve reduction
|   +-- agm_retirement_pay_xml        Retirement pay regulation changes
|
+-- agm_extract(rcept_no)             Raw text + structural extraction
+-- agm_document(rcept_no)            Raw document text
+-- agm_manual()                       AI assistant usage guide

Each parser has _xml, _pdf, _ocr variants (8 parsers x 3 tiers = 24 tools)
```

### Ownership (7 tools) -- Shareholder Structure

```
own(ticker)                      <- Ownership orchestrator
|
+-- own_major(ticker, year)           Largest shareholder + related parties
+-- own_total(ticker, year)           Total shares / treasury / float / minority
+-- own_treasury(ticker, year)        Treasury stock baseline (annual report)
+-- own_treasury_tx(ticker)           Acquisition/disposal/trust decisions
+-- own_block(ticker)                 5% block holders (purpose from filing)
+-- own_latest(ticker)                All shareholders latest snapshot
```

### 3-Tier Fallback (XML -> PDF -> OCR)

```
AI calls agm_*_xml(rcept_no)
  -> Good result -> Answer user
  -> Incomplete -> AI decides: agm_*_pdf(rcept_no)
      -> Good -> Answer
      -> Still failing -> agm_*_ocr(rcept_no)
```

| Tier | Source | Speed | Accuracy |
|------|--------|-------|----------|
| `_xml` | DART API (HTML/XML) | Fast | 98%+ |
| `_pdf` | PDF + opendataloader | 4s+ | 98%+ |
| `_ocr` | Upstage OCR API | Slowest | 100% |

## Parsing Performance (KOSPI 200)

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

## Project Structure

```
open_proxy_mcp/
  server.py           # FastMCP entry point (auto-discovery)
  tools/
    __init__.py       # register_all_tools() - auto-discovery
    shareholder.py    # 33 AGM tools
    ownership.py      # 7 ownership tools
    formatters.py     # 27 shared formatter functions
    errors.py         # Common error helpers
    parser.py         # XML parsers (bs4 + regex)
    pdf_parser.py     # PDF parsers + Upstage OCR fallback
  dart/
    client.py         # OpenDART API + KIND crawling + singleton
  llm/
    client.py         # LLM fallback (Claude / OpenAI)
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

Restart Claude Desktop. Start a new chat and say: **"Call agm_manual first."**

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

### Optional Dependencies

```bash
uv sync                         # Core only (XML parsing)
pip install open-proxy-mcp[pdf]  # + PDF/OCR fallback
pip install open-proxy-mcp[llm]  # + LLM fallback (Claude/OpenAI)
pip install open-proxy-mcp[all]  # Everything
```

### API Keys (.env)

```
OPENDART_API_KEY=...          # Required - free at opendart.fss.or.kr
OPENDART_API_KEY_2=...        # Optional - backup key (auto-switch on rate limit)
UPSTAGE_API_KEY=...           # Optional - OCR fallback (Upstage Document Parse)
```

## License

[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) - NonCommercial use only
