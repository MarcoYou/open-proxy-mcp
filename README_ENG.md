# OpenProxy MCP (OPM)

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-green.svg)](https://modelcontextprotocol.io/)
[![Tools](https://img.shields.io/badge/tools-33-orange.svg)](#tool-architecture-33-tools)

[Korean README (default)](README.md)

## Why OpenProxy?

As passive investing grows, the concept of stock ownership is fading -- yet this is precisely when more active shareholder engagement and deeper analysis of management quality matters most. But AGM filing explanations are opaque, the content is overwhelming, and the expertise required to analyze them creates a high barrier to entry.

**OpenProxy uses AI to break down that barrier.** It transforms 100+ page DART filings into structured data, enabling anyone to access institutional-grade proxy voting analysis in seconds.

![OpenProxy MCP Comparison](screenshot/openproxy_mcp_compare_v2.png)

---

## Quick Start

### Option A: Remote Server (No Install, 30 seconds)

OpenProxy is deployed on Fly.io. Connect via URL -- no local setup required.

**claude.ai web:**

1. Go to [claude.ai](https://claude.ai) -> click MCP icon at the bottom of chat input
2. Select "Add custom connector"
3. Name: `open-proxy-mcp`, URL: `https://open-proxy-mcp.fly.dev/mcp`
4. Click "Add" -> 33 tools auto-detected
5. Set tool permissions to **"Always allow"** (tools run without per-call approval)

**Claude Desktop:**

Settings > MCP Servers > Add URL connector:

```
https://open-proxy-mcp.fly.dev/mcp
```

**Claude Code:**

```bash
claude mcp add open-proxy-mcp --transport streamable-http https://open-proxy-mcp.fly.dev/mcp
```

> The remote server uses a shared DART API key. For heavy usage, consider local installation to avoid rate limits (1,000 calls/min).

### Option B: Local Installation

<details>
<summary>Local installation guide (click to expand)</summary>

#### 1. Clone and install

```bash
git clone https://github.com/MarcoYou/open-proxy-mcp.git
cd open-proxy-mcp
uv sync                    # Creates .venv + installs all dependencies
cp .env.example .env
```

#### 2. Configure environment

Edit `.env` and add your DART API key (free at [opendart.fss.or.kr](https://opendart.fss.or.kr)). This is the only required key.

```bash
OPENDART_API_KEY=your_key_here

# Optional
OPENDART_API_KEY_2=backup_key              # Auto-switches on rate limit
UPSTAGE_API_KEY=upstage_key                # OCR fallback
NAVER_SEARCH_API_CLIENT_ID=naver_id        # News search
NAVER_SEARCH_API_CLIENT_SECRET=naver_secret
```

#### 3. Editable install

```bash
uv pip install -e .
```

#### 4. Connect to Claude Desktop

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

#### 5. Connect to Claude Code

```json
// .mcp.json (project root)
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

#### 6. Optional dependencies

```bash
uv pip install -e ".[pdf]"               # + PDF/OCR fallback
uv pip install -e ".[llm]"               # + LLM fallback (Claude/OpenAI)
uv pip install -e ".[all]"               # Everything
```

</details>

### Usage Examples

After connecting, just ask in natural language:

```
"Analyze Samsung Electronics AGM agenda"
"Review KB Financial's outside director candidates"
"Check Hyundai Motor's compensation limits"
"Show Samsung Electronics ownership structure"
"What's SK Hynix's dividend history?"
"Analyze the Korea Zinc proxy fight"
```

---

## Tool Architecture (33 tools)

33 tools are organized into 5 execution tiers. The AI calls from Tier 1 downward, descending into Detail tools as needed.

```
                         ┌─────────────────────┐
                         │   corp_identifier    │  Tier 1 Entity
                         │  name/ticker lookup  │  "Samsung" -> 005930
                         └──────────┬──────────┘
                                    │
                         ┌──────────▼──────────┐
                         │     tool_guide       │  Tier 2 Context
                         │  usage + decisions   │
                         └──────────┬──────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              │                     │                     │
    ┌─────────▼────────┐ ┌─────────▼────────┐ ┌─────────▼────────┐
    │   agm_search     │ │   div_search     │ │   prx_search     │  Tier 3
    │   AGM notices    │ │   dividends      │ │   proxy filings  │  Search
    └─────────┬────────┘ └─────────┬────────┘ └─────────┬────────┘
              │                    │                     │
    ┌─────────▼────────┐ ┌────────▼─────────┐ ┌────────▼─────────┐
    │ agm_pre_analysis │ │div_full_analysis │ │    prx_fight     │  Tier 4
    │ agm_post_analysis│ │  full dividend   │ │  proxy fight     │  Orchestrate
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
    │  └ each parser: _xml / _pdf / _ocr fallback               │
    └───────────────────────────────────────────────────────────┘
```

### Domain Summary

| Domain | Description | Tools |
|--------|-------------|-------|
| **AGM** | AGM notice parsing -- agenda, financials, directors, articles, compensation, treasury | 14 |
| **OWN** | Ownership structure -- largest shareholders, total shares, treasury, 5% block holders | 6 |
| **DIV** | Dividends -- payout details, 3-year history, payout ratio/yield | 4 |
| **PRX** | Proxy fights -- solicitation filings, both-side comparison | 4 |
| **NEWS** | Negative news search for director/auditor candidates | 1 |
| **CORP** | Company identification (ticker/corp_code resolution) | 1 |
| **GUIDE** | Full tool usage guide | 1 |
| **GOV** | Integrated governance report (AGM+OWN+DIV) | 1 |
| | **Total** | **33** |

---

## Fallback Parsing

AGM parsers resolve most filings at the XML tier (97%+ accuracy across KOSPI 200). PDF and OCR fallbacks are available for non-standard formats.

```
_xml (DART API, free, <1s)  <- most filings complete here
  ↓ non-standard format
_pdf (PDF download, free, 4s+)
  ↓ still failing
_ocr (Upstage OCR API, paid, 8s+)  <- 100% accuracy
```

The AI evaluates result quality and autonomously escalates to the next tier. No user intervention required.

<details>
<summary>KOSPI 200 parser accuracy (click to expand)</summary>

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

</details>

---

## Proxy Voting Decisions

Structured voting recommendations based on parsed data:

| Agenda Type | FOR | AGAINST | REVIEW |
|-------------|-----|---------|--------|
| Financial Statements | Clean audit opinion | Qualified/adverse | Extreme payout ratio |
| Director Election | Independent outside director | Fails independence test | 3+ concurrent positions |
| Compensation Limits | Appropriate utilization | Utilization < 30% + increase | 50%+ increase |
| Articles of Incorporation | Reflects statutory changes | Removes cumulative voting | Board size reduction |
| Treasury Shares | Cancellation purpose | Entrenchment purpose | Foundation donation |
| Dividends | At/above industry average | DPS decrease despite earnings growth | Reduced dividend |

---

## Data Sources

| Source | Usage | Note |
|--------|-------|------|
| [DART OpenAPI](https://opendart.fss.or.kr/) | AGM notices, annual reports, ownership filings | Required (free API key) |
| [KRX KIND](https://kind.krx.co.kr/) | AGM voting results | Web scraping |
| [Naver News API](https://developers.naver.com/) | Candidate negative news search | Optional (free API key) |
| [Naver Finance](https://finance.naver.com/) | Stock prices, sector, dividends | Web scraping |

---

## Project Structure

```
open-proxy-mcp/
  open_proxy_mcp/
    server.py              # FastMCP server (stdio + HTTP)
    tools/                 # 33 tools (AGM/OWN/DIV/PRX/NEWS/CORP/GUIDE/GOV)
    dart/client.py         # DART API + KIND scraping + Naver + rate limiter
  Dockerfile               # Fly.io container
  fly.toml                 # Fly.io config (nrt region, auto-suspend)
  wiki/                    # Domain knowledge wiki (68 pages)
```

---

## Glossary

| Term | Description |
|------|-------------|
| **DART** | Korea's SEC EDGAR equivalent (Financial Supervisory Service) |
| **KIND** | Korea Exchange information disclosure platform |
| **KOSPI 200** | Benchmark index of 200 largest Korean companies |
| **rcept_no** | DART filing receipt number (unique ID per disclosure) |
| **ticker** | 6-digit stock code (e.g., "005930" for Samsung Electronics) |

---

## License

[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) -- NonCommercial use only.

You are free to share and adapt this work for non-commercial purposes, provided you give appropriate credit.
