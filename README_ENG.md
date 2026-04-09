# OpenProxy MCP (OPM)

[Korean README (default)](README.md)

AI-powered MCP (Model Context Protocol) server that transforms Korean corporate governance filings into structured, AI-ready data -- covering shareholder meetings, ownership structures, dividends, and news.

> **OpenProxy brings institutional-grade governance intelligence to every investor -- parsing shareholder meeting filings, ownership disclosures, and dividend decisions into structured, AI-ready data in seconds, not hours.**

![OpenProxy MCP Comparison](screenshot/openproxy_mcp_compare_v2.png)

---

## Why OpenProxy?

The global shift toward passive investing is concentrating ownership in permanent, price-insensitive strategies. As a result, proxy voting has become a critical mechanism for corporate governance and long-term value creation. Yet the process of analyzing shareholder meeting materials remains overwhelmingly manual, fragmented, and inaccessible to most market participants.

In Korea -- one of Asia's largest equity markets -- Annual General Meeting (AGM) filings are published through DART (Korea's equivalent of SEC EDGAR) as dense, 100+ page HTML documents. These filings mix regulatory boilerplate, financial footnotes, and actual voting items in unstructured Korean text, making systematic analysis nearly impossible without dedicated teams.

**The problem is threefold:**

1. **Institutional investors** rely on expensive internal teams or external proxy advisory firms to manually parse each filing.
2. **Individual investors** are effectively locked out of governance analysis entirely.
3. **AI tools** cannot consume raw DART filings without significant preprocessing -- the data is unstructured, inconsistent across companies, and buried in complex document hierarchies.

**OpenProxy bridges this gap.** By exposing 48 MCP tools that transform raw DART disclosures into clean, structured JSON, OpenProxy enables any investor, analyst, or AI agent to perform institutional-quality governance analysis at scale. Whether you are evaluating director appointments, compensation limits, ownership concentration, or dividend adequacy, OpenProxy delivers the data in a format that both humans and AI can immediately act on.

---

## Why Parsing Matters

A raw DART AGM notice is a 100+ page HTML document mixing regulatory boilerplate, financial footnotes, and actual voting items. Finding a single fact -- like the CEO's proposed compensation limit -- requires reading through thousands of lines of unstructured Korean text.

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

This transformation is not trivial. DART filings have no standardized schema -- every company formats its disclosures differently. Table structures, naming conventions, and even the order of information vary widely. OpenProxy handles all of this through robust parsing logic with a 3-tier fallback system that achieves near-100% accuracy across the KOSPI 200 universe.

---

## Key Features

### AGM (Annual General Meeting) -- 34 tools

Full coverage of AGM notice analysis: agenda parsing, financial statement extraction, director/auditor appointments, articles of incorporation changes, executive compensation limits, treasury share operations, capital reserve reductions, and retirement pay regulations. Includes an orchestrator for one-call summaries and a built-in AI usage manual.

### OWN (Ownership Structure) -- 8 tools

Comprehensive shareholder structure analysis: largest shareholders and related parties, total share breakdown (treasury/float/minority), treasury stock transactions (acquisition/disposal/trust), 5% block holder filings with purpose extraction, and a latest-snapshot aggregator across all shareholder types.

### DIV (Dividends) -- 5 tools

Dividend decision analysis: search dividend-related disclosures, extract detailed payout structures (cash/stock, interim/annual), track multi-year dividend history with payout ratios and yield calculations, plus an orchestrator and AI usage manual.

### NEWS -- 1 tool

Real-time news monitoring via Naver News API for any listed Korean company, providing the latest headlines and article links for governance-relevant developments.

---

## Tool Tree (48 tools)

### AGM Domain (34 tools)

```
agm(ticker)                          <- Orchestrator (one-call summary)
|
+-- agm_search(ticker)                    Search AGM notices by ticker
+-- agm_info(rcept_no)                    Meeting info (date, location, quorum)
+-- agm_agenda_xml(rcept_no)              Agenda tree with sub-items
+-- agm_corrections(rcept_no)             Correction before/after diff
+-- agm_result(ticker)                    Voting results (KRX KIND scraping)
|
+-- agm_items(rcept_no)                   Raw agenda detail blocks (generic)
|   +-- agm_financials_xml                Financial statements (BS/IS)
|   +-- agm_personnel_xml                 Director/auditor appointments
|   +-- agm_aoi_change_xml               Articles of incorporation changes
|   +-- agm_compensation_xml             Executive compensation limits
|   +-- agm_treasury_share_xml           Treasury share hold/dispose/cancel
|   +-- agm_capital_reserve_xml          Capital reserve reduction
|   +-- agm_retirement_pay_xml           Retirement pay regulation changes
|
+-- agm_extract(rcept_no)                Raw text + structural extraction
+-- agm_document(rcept_no)               Raw document text
+-- agm_manual()                          AI assistant usage guide

Each item parser has _xml, _pdf, _ocr variants:
  8 parsers x 3 tiers = 24 tools (included in count above)

Agenda type -> Tool mapping:
  Financial statements       -> agm_financials_xml
  Director/auditor election  -> agm_personnel_xml
  Articles of incorporation  -> agm_aoi_change_xml
  Compensation limits        -> agm_compensation_xml
  Treasury shares            -> agm_treasury_share_xml
  Capital reserve reduction  -> agm_capital_reserve_xml
  Retirement pay             -> agm_retirement_pay_xml
  Other                      -> agm_items (raw blocks)
```

### OWN Domain (8 tools)

```
own(ticker)                              <- Ownership orchestrator
|
+-- own_major(ticker, year)                   Largest shareholder + related parties
+-- own_total(ticker, year)                   Total shares / treasury / float / minority
+-- own_treasury(ticker, year)                Treasury stock baseline (from annual report)
+-- own_treasury_tx(ticker)                   Acquisition/disposal/trust decisions
+-- own_block(ticker)                         5% block holders (purpose from filing)
+-- own_latest(ticker)                        All shareholders latest snapshot
+-- own_manual()                              AI assistant usage guide
```

### DIV Domain (5 tools)

```
div(ticker)                              <- Dividend orchestrator
|
+-- div_search(ticker)                        Search dividend disclosures
+-- div_detail(ticker)                        Detailed payout structure
+-- div_history(ticker)                       Multi-year dividend history + ratios
+-- div_manual()                              AI assistant usage guide
```

### NEWS Domain (1 tool)

```
news_check(ticker)                       <- Latest news headlines via Naver News API
```

---

**Example: Samsung Electronics ownership structure via `own("Samsung Electronics")`**

![Samsung Ownership](screenshot/samsung_ownership_en.png)

---

## 3-Tier Fallback (XML -> PDF -> OCR)

OpenProxy implements a 3-tier fallback architecture to ensure maximum parsing accuracy. The AI agent autonomously escalates through tiers when data quality is insufficient:

```
AI calls agm_*_xml(rcept_no)
  -> Good result -> Answer user
  -> Incomplete or quality issue
     -> AI decides: "XML parsing incomplete. Retrying with PDF."
     -> agm_*_pdf(rcept_no)
         -> Good result -> Answer user
         -> Still failing
            -> AI decides: "Trying OCR." -> agm_*_ocr(rcept_no)
```

| Tier | Source | Speed | Accuracy | Cost |
|------|--------|-------|----------|------|
| `_xml` | DART API (HTML/XML parsing) | Fast (< 1s) | 98%+ | Free |
| `_pdf` | PDF download + opendataloader | Moderate (4s+) | 98%+ | Free |
| `_ocr` | Upstage Document Parse API | Slowest (8s+) | 100% | API key required |

**How it works:**

- **Tier 1 (XML):** Parses the DART API response directly using BeautifulSoup (lxml) with regex fallbacks. Handles the vast majority of filings successfully.
- **Tier 2 (PDF):** Downloads the original PDF filing from DART's web interface and converts it to markdown via opendataloader, then applies the PDF parser. Used when XML parsing produces incomplete results (e.g., complex nested tables, non-standard formatting).
- **Tier 3 (OCR):** Extracts specific pages from the PDF using keyword targeting, sends them to the Upstage Document Parse API for visual OCR, then re-runs the PDF parser on the OCR output. Achieves 100% accuracy but requires an API key and is the slowest option.

The AI agent makes the escalation decision autonomously based on quality criteria defined in `agm_manual`. No user intervention is required.

---

## Proxy Voting Decision Tree

OpenProxy provides a structured framework for proxy voting decisions based on institutional best practices. The decision tree covers all major agenda types:

### Decision Framework by Agenda Type

| Agenda Type | FOR | AGAINST | REVIEW |
|-------------|-----|---------|--------|
| **Financial Statements** | Clean audit opinion + positive earnings trend | Qualified/adverse audit opinion | Reported item (no vote required) |
| **Director Election** | Independent outside director, relevant expertise | Insider director, excessive concurrent positions | Cumulative voting -- evaluate each candidate |
| **Articles of Incorporation** | Reflects statutory amendments (formality) | Removes cumulative voting rights | Board size reduction before director election (defense tactic) |
| **Compensation Limits** | Modest increase, prior utilization 70%+ | Large increase, prior utilization < 30% | Separate limits for directors vs. auditors |
| **Treasury Shares** | Acquisition for cancellation (shareholder return) | Held for management entrenchment | Foundation donation plan (defense tactic) |
| **Dividends** | Payout ratio at/above industry average | DPS decrease despite earnings growth | Reduced dividend -- requires justification |

### Summary Criteria

| Decision | Condition |
|----------|-----------|
| **FOR** | Meets guidelines + no defense tactics detected + enhances shareholder value |
| **AGAINST** | Destroys shareholder value, defense tactic detected, or lacks independence |
| **ABSTAIN** | Insufficient information, balanced arguments, or conflict of interest |

This framework is derived from global institutional voting guidelines (including JPMAM voting process documentation) and adapted for the Korean market context.

---

## Parsing Performance (KOSPI 200)

Benchmarked against 199 companies in the KOSPI 200 universe (one company excluded due to delisting). Success rates measured on agenda-tree-level parsing accuracy:

| Parser | XML | PDF | OCR |
|--------|-----|-----|-----|
| Agenda list | 99.5% | 98.0% | 100% |
| Balance Sheet | 97.4% | 97.9% | 100% |
| Income Statement | 100% | 95.7% | 100% |
| Director/Auditor | 98.9% | 97.9% | 100% |
| Articles of Incorporation | 97.8% | 99.0% | 100% |
| Compensation | 98.4% | 99.5% | 100% |
| Treasury Shares | 93.6% | 100% | 100% |
| Capital Reserve | 100% | 100% | 100% |
| Retirement Pay | 93.3% | 86.7% | 86.7% |

With the 3-tier fallback, effective accuracy across all parser types reaches near-100% for the entire KOSPI 200 universe.

---

## Data Sources

| Source | Description | Usage |
|--------|-------------|-------|
| [DART OpenAPI](https://opendart.fss.or.kr/) | Korea Financial Supervisory Service Electronic Disclosure System (Korea's SEC EDGAR). Provides AGM notices, annual reports, ownership filings, and all regulated corporate disclosures. | AGM, OWN, DIV |
| [KRX KIND](https://kind.krx.co.kr/) | Korea Exchange Information Disclosure platform. Publishes AGM voting results, shareholder meeting schedules, and exchange-mandated disclosures. | AGM voting results |
| [KRX Open API](https://data.krx.co.kr/) | Korea Exchange market data API. Provides stock prices, market cap, index constituents, and trading data. | Ticker resolution, market data |
| [Naver News API](https://developers.naver.com/) | Korea's largest portal search API. Provides real-time news article search and retrieval. | NEWS |
| [Naver Finance](https://finance.naver.com/) | Korea's most widely used retail finance portal. Provides stock quotes, financial summaries, and company profiles. | Price data, company info |

---

## Project Structure

```
open_proxy_mcp/
  server.py              # FastMCP server entry point (stdio + SSE)
  tools/
    __init__.py          # register_all_tools() - auto-discovery
    shareholder.py       # 34 AGM tools (parsers + formatters)
    ownership.py         # 8 OWN tools (DART API + formatters)
    dividend.py          # 5 DIV tools (dividend disclosures)
    news.py              # 1 NEWS tool (Naver News API)
    formatters.py        # Shared formatter functions
    errors.py            # Common error helpers
    parser.py            # XML parsers (BeautifulSoup + regex fallback)
    pdf_parser.py        # PDF parsers + Upstage OCR fallback
  dart/
    client.py            # OpenDART API + KIND scraping + Naver + singleton
  llm/
    client.py            # LLM fallback (Claude / OpenAI)
```

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/MarcoYou/open-proxy-mcp.git
cd open-proxy-mcp
uv sync                    # Creates .venv + installs all dependencies
cp .env.example .env
```

### 2. Configure environment

Edit `.env` and add your DART API key (free registration at [opendart.fss.or.kr](https://opendart.fss.or.kr)):

```
OPENDART_API_KEY=your_key_here
```

### 3. Install as editable package (required for Claude Desktop)

```bash
uv pip install -e .
```

This installs OpenProxy as an editable package so Claude Desktop can locate the module.

### 4. Connect to Claude Desktop

Add the following to `~/Library/Application Support/Claude/claude_desktop_config.json`:

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

Replace `/path/to/open-proxy-mcp` with the actual absolute path to your cloned repository.

Restart Claude Desktop. Start a new conversation and say: **"Call agm_manual first."**

This loads the AI usage guide, which teaches the AI agent how to use the tools effectively, including quality criteria for the 3-tier fallback and agenda-type-to-tool mapping.

### 5. Connect to Claude Code

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

### 6. Optional dependencies

```bash
uv sync                         # Core only (XML parsing)
pip install open-proxy-mcp[pdf]  # + PDF/OCR fallback (Tier 2 & 3)
pip install open-proxy-mcp[llm]  # + LLM fallback (Claude/OpenAI)
pip install open-proxy-mcp[all]  # Everything
```

### 7. API Keys (.env)

```
OPENDART_API_KEY=...          # Required - free at opendart.fss.or.kr
OPENDART_API_KEY_2=...        # Optional - backup key (auto-switches on rate limit)
UPSTAGE_API_KEY=...           # Optional - OCR fallback, Tier 3 (Upstage Document Parse)
```

### 8. First use

After connecting to Claude Desktop or Claude Code, start every new session with:

> **"Call agm_manual first."**

This is essential. The `agm_manual` tool returns a comprehensive guide that teaches the AI how to:
- Map agenda types to the correct parser tools
- Evaluate parsing quality and decide when to escalate tiers
- Format results for human readability
- Handle edge cases (corrections, split agendas, dual-class shares)

Without calling `agm_manual` first, the AI may use tools suboptimally.

---

## Glossary of Korean Market Terms

For international developers and investors unfamiliar with the Korean market:

| Term | English | Description |
|------|---------|-------------|
| **DART** | Data Analysis, Retrieval and Transfer | Korea's SEC EDGAR equivalent. Operated by FSS (Financial Supervisory Service). All regulated disclosures are filed here. |
| **KIND** | KRX Information Disclosure | Korea Exchange's disclosure platform. Publishes exchange-mandated information including AGM voting results. |
| **KOSPI 200** | Korea Composite Stock Price Index 200 | The benchmark index of 200 largest companies on the Korea Exchange. OpenProxy is benchmarked against this universe. |
| **AGM** | Annual General Meeting | Called "주주총회" (Joo-joo-chong-hoe) in Korean. |
| **rcept_no** | Receipt Number | DART's unique identifier for each filing. Used as the primary key across all AGM tools. |
| **ticker** | Stock Code | 6-digit numeric code (e.g., "005930" for Samsung Electronics). Tools also accept Korean company names. |

---

## License

[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) -- NonCommercial use only.

You are free to share and adapt this work for non-commercial purposes, provided you give appropriate credit.
