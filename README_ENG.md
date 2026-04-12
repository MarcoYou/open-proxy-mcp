# OpenProxy MCP

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-green.svg)](https://modelcontextprotocol.io/)
[![Tools](https://img.shields.io/badge/tools-33-orange.svg)](#tool-architecture-33-tools)

[Korean README (default)](README.md)

## Why OpenProxy?

As passive investing grows, the concept of stock ownership is fading -- yet this is precisely when more active shareholder engagement and deeper analysis of management quality matters most. But AGM filing explanations are opaque, the content is overwhelming, and the expertise required to analyze them creates a high barrier to entry.

**OpenProxy uses AI to break down that barrier.** It transforms 100+ page DART filings into structured data, enabling anyone to access institutional-grade proxy voting analysis in seconds.

![OpenProxy MCP Comparison](screenshot/open-proxy-mcp%20output%20eng.png)

---

## Quick Start

### Step 1: Get a DART API Key (Required)

All data in OpenProxy comes from DART OpenAPI. **You need your own API key to use it.**

1. Go to [DART OpenAPI](https://opendart.fss.or.kr/) -> Sign up
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
                            ┌──────────────────────┐
                            │    corp_identifier    │  Tier 1 Entity
                            │  name/ticker lookup   │  "Samsung" -> 005930
                            └──────────┬───────────┘
                                       │
                            ┌──────────▼───────────┐
                            │      tool_guide      │  Tier 2 Context
                            │   usage + decisions   │
                            └──────────┬───────────┘
                                       │
              ┌────────────────────────┼────────────────────────┐
              │                        │                        │
    ┌─────────▼─────────┐  ┌──────────▼──────────┐  ┌──────────▼──────────┐
    │    agm_search      │  │     div_search      │  │    proxy_search     │  Tier 3
    │   AGM notices      │  │    dividends        │  │   proxy filings     │  Search
    └─────────┬─────────┘  └──────────┬──────────┘  └──────────┬──────────┘
              │                       │                        │
    ┌─────────▼──────────────┐ ┌──────▼───────────┐ ┌──────────▼──────────┐
    │ agm_pre_analysis       │ │div_full_analysis │ │    proxy_fight      │  Tier 4
    │ agm_post_analysis      │ │  full dividend   │ │  proxy fight        │  Orchestrate
    │ ownership_full_analysis│ └──────┬───────────┘ └──────────┬──────────┘
    │ governance_report      │        │                        │
    └─────────┬──────────────┘        │                        │
              │                       │                        │
    ┌─────────▼───────────────────────▼────────────────────────▼──────────┐
    │                           Tier 5 Detail                            │
    │                                                                    │
    │  AGM (12)              OWN (5)               DIV (2)   PRX (2)    │
    │  ├ agenda_xml          ├ ownership_major     ├ detail  ├ detail   │
    │  ├ financials_xml      ├ ownership_total     └ history └ direction│
    │  ├ personnel_xml       ├ ownership_treasury                       │
    │  ├ aoi_change_xml      ├ ownership_block     NEWS (1)             │
    │  ├ compensation_xml    └ ownership_treasury_tx└ news_check        │
    │  ├ treasury_share_xml                                             │
    │  ├ capital_reserve_xml                                            │
    │  ├ retirement_pay_xml                                             │
    │  ├ info / corrections / result / items                            │
    │  └ each parser: _xml / _pdf / _ocr fallback                      │
    └───────────────────────────────────────────────────────────────────┘
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
