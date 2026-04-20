# OpenProxy MCP

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-green.svg)](https://modelcontextprotocol.io/)
[![Tools](https://img.shields.io/badge/tools-12-orange.svg)](#tool-structure-12-tools)

[Korean README](README.md)

## Why OpenProxy?

Governance risk is at the heart of the Korea Discount. As passive investing grows, the meaning of share ownership is fading — yet the risk itself is becoming sharper. Accessing and analyzing governance data quickly should be easy, but reading through hundreds of pages of regulatory filings takes more time and expertise than most people have.

**OpenProxy breaks down that barrier using AI.** It converts DART filings into structured data, so anyone can analyze ownership structure, dividend history, AGM agendas, and proxy contests in seconds.

![OpenProxy MCP comparison](screenshot/open-proxy-mcp%20output%20eng.png)

---

## Quick Start

### Step 0: Check your Claude subscription (required)

MCP connectors are available for **Claude Pro, Max, and Teams** subscribers only. Check your plan at [claude.ai](https://claude.ai).

### Step 1: Get a DART API key (required)

All data in OpenProxy comes from DART OpenAPI. **You need your own API key to use it.**

1. Go to [DART OpenAPI](https://opendart.fss.or.kr/) and create an account
2. Request an authentication key — it's free and issued immediately

### Step 2: Connect

Once you have the API key, choose one of the two options below.

#### Option A: Remote server (no installation, takes 30 seconds)

Append your DART API key to the URL. The key is only used server-side and is never exposed to the AI.

**claude.ai web:**

1. Go to [claude.ai](https://claude.ai) → Settings → Connectors
2. Select "Add custom connector"
3. Name: `open-proxy-mcp`, enter the URL:
```
https://open-proxy-mcp.fly.dev/mcp?opendart=YOUR_API_KEY
```
4. Click "Add" → 12 tools are automatically recognized
5. Go to the connector settings → Permissions → select **"Always allow"** (tools run automatically without per-call approval)

> **Note**: If tools have been added or updated, it may take a moment for the connector to sync. Remove the connector and re-add it to get the latest tools immediately. Open a new chat after reconnecting.

### Usage examples

Once connected, just ask in natural language:

```
"Summarize Samsung Electronics' AGM agenda items"
"Review the independence of KB Financial's outside director candidates"
"Is Hyundai Motor's compensation limit reasonable?"
"Show me Samsung Electronics' ownership structure"
"What is SK Hynix's dividend history?"
"Analyze the Korea Zinc proxy contest"
"Find KOSPI companies that disclosed treasury share cancellations in the last 30 days"
"List companies that called an extraordinary general meeting in the past 60 days"
```

\* OpenProxy does not currently analyze DART financial metrics (planned for a future update)

---

## Tool Structure (12 tools)

12 tools are organized into **discovery → data tabs → action outputs**.

```
company                      # Entry point — company ID + recent filings index
│
├─ Discovery Tool (1)
│  └─ screen_events          # Find companies by recent event (14 event_types, KOSPI+KOSDAQ)
│
├─ Data Tools (7)
│  ├─ shareholder_meeting    # AGM/EGM (agendas / candidates / compensation / results)
│  ├─ ownership_structure    # Ownership (largest shareholders / 5% blocks / treasury / change filings)
│  ├─ dividend               # Dividend facts (DPS / payout ratio / history)
│  ├─ treasury_share         # Treasury events (acquisition / disposal / cancellation / trust)
│  ├─ proxy_contest          # Proxy contest (solicitations / litigation / 5% signals)
│  ├─ value_up               # Value-up plan (commitments / implementation)
│  └─ evidence               # Filing source links (rcept_no → viewer_url)
│
└─ Action Tools (3)
   ├─ prepare_vote_brief      # Vote memo
   ├─ prepare_engagement_case # Shareholder engagement memo
   └─ build_campaign_brief    # Campaign brief
```

Two usage patterns:

```
Pattern A (company → analysis):  start with `company` → confirm facts via data tabs → generate action outputs
Pattern B (event → companies):   start with `screen_events` → drill down into each company
```

### Supported events in `screen_events` (14 types)

| Category | event_type |
|---------|-----------|
| AGM | `shareholder_meeting_notice` |
| Ownership | `major_shareholder_change`, `ownership_change_filing`, `block_holding_5pct`, `executive_ownership` |
| Treasury | `treasury_acquire`, `treasury_dispose`, `treasury_retire` |
| Contest | `proxy_solicit`, `litigation`, `management_dispute` |
| Value-up | `value_up_plan` |
| Dividend | `cash_dividend`, `stock_dividend` |

Default window: last 30 days. Market: KOSPI+KOSDAQ. Each result row includes a clickable link to the original DART viewer.

### Domain summary

| Domain | Description | Tools |
|--------|-------------|-------|
| **Discovery** | Event → company lookup | 1 |
| **Company** | Company ID + recent filings index | 1 |
| **AGM** | Agendas, board candidates, compensation, articles, results | 1 |
| **Ownership** | Largest shareholders, block holders, treasury, control map, change filings | 1 |
| **Dividend** | Actual dividend payouts, DPS, payout ratio, history | 1 |
| **Treasury** | Acquisition, disposal, cancellation, trust events | 1 |
| **Proxy** | Proxy solicitations, litigation, 5% signals | 1 |
| **Value-up** | Corporate value-up plans, implementation | 1 |
| **Evidence** | Filing source links | 1 |
| **Action** | Vote memo, engagement case, campaign brief | 3 |
| | **Total** | **12** |

---

## Voting Criteria

When you ask for a voting recommendation on an AGM agenda item, OpenProxy follows the criteria below to return FOR / AGAINST / REVIEW.

| Agenda type | FOR | AGAINST | REVIEW |
|-------------|-----|---------|--------|
| Financial statements | Clean audit opinion | Qualified / adverse | Extreme payout ratio |
| Director election | Outside director independence met | Independence not met | 3+ concurrent roles, adverse news |
| Compensation limit | Utilization rate reasonable | Rate < 30% yet proposed increase | 50%+ large increase |
| Articles amendment | Statutory update (formal) | Removes cumulative voting | Reduces board size |
| Treasury shares | Cancellation purpose | Entrenchment purpose | Foundation donation |
| Dividend | Above sector average | EPS up but DPS down | Dividend cut |

---

## Data Sources

| Source | Use | Notes |
|--------|-----|-------|
| [DART OpenAPI](https://opendart.fss.or.kr/) | AGM notices, annual reports, large-holding reports | Required (free API key) |
| [KRX KIND](https://kind.krx.co.kr/) | AGM voting results | Web crawl |
| [Naver News API](https://developers.naver.com/) | Candidate adverse news search | Optional (free API key) |
| [Naver Finance](https://finance.naver.com/) | Stock price, sector, dividend yield | Web crawl |

---

## Project Structure

```
open-proxy-mcp/
  open_proxy_mcp/
    server.py              # FastMCP server (stdio + HTTP)
    tools_v2/              # 12 tools
    services/              # Domain logic layer (separated from tools)
    dart/client.py         # DART API + KIND crawl + Naver + rate limiter
  Dockerfile               # Container for Fly.io deployment
  fly.toml                 # Fly.io config (nrt region, auto-suspend)
  wiki/                    # Domain knowledge wiki
```

---

## Disclaimer

OpenProxy structures DART filing data for AI use. AI can hallucinate and may produce inaccurate analysis. The views expressed by the AI do not represent those of the developer or any affiliated organization. Use analysis results for reference only — final investment decisions and voting judgments must always be verified against the original filings and expert review.

---

## License

[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) -- Non-commercial use only

Please credit the source when using this project's code or data. Commercial use is not permitted.
