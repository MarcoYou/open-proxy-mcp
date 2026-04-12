# OpenProxy MCP

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-green.svg)](https://modelcontextprotocol.io/)
[![Tools](https://img.shields.io/badge/tools-33-orange.svg)](#tool-architecture-33-tools)

[Korean README (default)](README.md)

## Why OpenProxy?

At the heart of the Korea Discount lies governance risk. As passive investing grows and the meaning of stock ownership fades, this risk is only becoming sharper. Addressing it requires easy access to governance data and fast, in-depth analysis -- but parsing hundreds of pages of regulatory filings demands both time and expertise that most investors lack.

**OpenProxy breaks down that barrier with AI.** It transforms [DART](https://englishdart.fss.or.kr/) (Korea's electronic disclosure system, similar to SEC EDGAR) filings into structured data, making the full spectrum of governance analysis -- ownership structure, dividend history, AGM agendas, and proxy fights -- accessible to anyone in seconds.

![OpenProxy MCP Comparison](screenshot/open-proxy-mcp%20output%20eng.png)

---

## Quick Start

### Step 1: Get a DART API Key (Required)

All data in OpenProxy comes from DART OpenAPI. **You need your own API key to use it.**

1. Go to [DART OpenAPI](https://englishdart.fss.or.kr/) -> Sign up
2. Request API key -> Issued instantly (free)

### Step 2: Connect

Once you have your API key, choose one of the two methods below.

#### Option A: Remote Server (No Install, 30 seconds)

Append your DART API key to the URL. The key is used server-side only and never exposed to the AI.

**claude.ai web:**

1. Go to [claude.ai](https://claude.ai) -> Settings -> Connectors
2. Select "Add custom connector"
3. Name: `open-proxy-mcp`, URL:
```
https://open-proxy-mcp.fly.dev/mcp?opendart=YOUR_KEY
```
4. Click "Add" -> 33 tools auto-detected
5. Go to the added connector's Configuration -> Permissions and select **"Always allow"** (tools run without per-call approval)

#### Option B: Local Installation

Local installation lets you configure additional API keys beyond DART (news search, OCR fallback, etc.).

See [local installation guide](docs/connect_eng.md)

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
Tier 1  corp_identifier ............. "005930" / "Samsung"
        |
Tier 2  tool_guide
        |
        +------------------+------------------+
        |                  |                  |
Tier 3  agm_search         div_search         proxy_search
        |                  |                  |
Tier 4  agm_pre_analysis   div_full_analysis  proxy_fight
        agm_post_analysis
        ownership_full_analysis
        governance_report
        |                  |                  |
        +------------------+------------------+
        |
Tier 5  AGM (12)                OWN (5)
        agm_agenda_xml          ownership_major
        agm_financials_xml      ownership_total
        agm_personnel_xml       ownership_treasury
        agm_aoi_change_xml      ownership_treasury_tx
        agm_compensation_xml    ownership_block
        agm_treasury_share_xml
        agm_capital_reserve_xml DIV (2)
        agm_retirement_pay_xml  div_detail
        agm_result              div_history
        agm_items
        agm_corrections         PRX (2)
        agm_parse_fallback      proxy_detail
                                proxy_direction
        NEWS (1)
        news_check
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

The remote server supports XML parsing only. PDF/OCR fallback is available with local installation. See [local installation guide](docs/connect_eng.md#fallback-parsing) for details.

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
| [DART OpenAPI](https://englishdart.fss.or.kr/) | AGM notices, annual reports, ownership filings | Required (free API key) |
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
