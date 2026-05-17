# OpenProxy MCP

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-green.svg)](https://modelcontextprotocol.io/)
[![Tools](https://img.shields.io/badge/tools-16-orange.svg)](#tool-structure-16-tools)

[Korean README](README.md)

## Why OpenProxy?

Governance risk is at the heart of the Korea Discount. As passive investing grows, the meaning of share ownership is fading — yet the risk itself is becoming sharper. Accessing and analyzing governance data quickly should be easy, but reading through hundreds of pages of regulatory filings takes more time and expertise than most people have.

**OpenProxy breaks down that barrier using AI.** It converts DART filings into structured data, so anyone can analyze ownership structure, dividend history, AGM agendas, and proxy contests in seconds.

![OpenProxy MCP comparison](screenshot/open-proxy-mcp%20output%20eng.png)

---

## Quick Start

### Step 0: Check supported clients and access requirements

OpenProxy MCP is deployed as a **remote MCP server**. You can connect it from Claude web and ChatGPT web surfaces that support custom connectors / MCP apps.

- **Claude**: a paid plan with custom connector support is required.
- **ChatGPT**: a plan and workspace/developer permission that supports custom connectors / MCP apps may be required.

> **Note**:
> - Actual menus depend on plan, workspace permissions, and feature rollout state.
> - ChatGPT integration assumes a **remote MCP server**, not a local MCP process.

### Step 1: Get a DART API key (required)

All data in OpenProxy comes from DART OpenAPI. **You need your own API key to use it.**

1. Go to [DART OpenAPI](https://opendart.fss.or.kr/) and create an account
2. Request an authentication key — it's free and issued immediately

### Step 2: Connect

Once you have the API key, choose one of the two options below.

#### Option A: Claude web custom connector (no installation, takes 30 seconds)

Append your DART API key to the URL. The key is only used server-side and is never exposed to the AI.

**claude.ai web:**

1. Go to [claude.ai](https://claude.ai) → Settings → Connectors
2. Select "Add custom connector"
3. Name: `open-proxy-mcp`, enter the URL:
```
https://open-proxy-mcp.fly.dev/mcp?opendart=YOUR_API_KEY
```
4. Click "Add" → 16 tools are automatically recognized
5. Go to the connector settings → Permissions → select **"Always allow"** (tools run automatically without per-call approval)

> **Note**: If tools have been added or updated, it may take a moment for the connector to sync. Remove the connector and re-add it to get the latest tools immediately. Open a new chat after reconnecting.

#### Option B: ChatGPT web custom connector / MCP app (beta)

ChatGPT web can also connect to remote MCP servers through custom connector / MCP app surfaces when available to your account.

1. Open ChatGPT web
2. Confirm developer mode or custom connector creation permission
3. Go to `Settings -> Apps & Connectors -> Create`
   or `Workspace Settings -> Connectors -> Create`
4. Name: `open-proxy-mcp`
5. MCP server URL:
```
https://open-proxy-mcp.fly.dev/mcp?opendart=YOUR_API_KEY
```
6. Choose the authentication mode
7. Save, then select the connector/app in a new chat

> **Note**:
> - ChatGPT custom connector / MCP app availability depends on plan, workspace permission, and beta rollout.
> - This is a custom MCP server, so organizations may need separate review before use.

### Usage examples

Once connected, just ask in natural language:

```
"Summarize Samsung Electronics' AGM agenda items"                # Integrated analysis (proxy_advise)
"Review independence of KB Financial's outside director candidates"  # Candidate evaluation
"Analyze the Korea Zinc proxy contest"                            # Contest signals
"Show Samsung Electronics' ownership structure"                   # Ownership + control map
"SK Hynix dividend history"                                       # Dividend + quarterly breakdown
"Find KOSPI companies that cancelled treasury shares in last 30 days"  # Treasury screening
"Lotte Chemical 2024 YoY + accounting risk alerts"                # Financials + audit opinion
"KT&G corporate governance report compliance rate"                # Governance 15 principles
"Create a KT&G AGM voting memo"                                   # Open Proxy Guideline recommendation
```

More usage patterns → [wiki/tools/README.md](wiki/tools/README.md) (16 tool catalog).

---

## Tool Structure (16 tools)

16 tools follow the flow **Company → Meeting/Data/Evidence → Action**.

```text
OpenProxy MCP
├─ Company
│  └─ company
│     └─ Company identification, corp_code, recent filings index
│
├─ Meeting
│  ├─ shareholder_meeting_notice
│  │  └─ Pre-meeting: notice, agendas, candidates, compensation, articles, financials
│  └─ shareholder_meeting_results
│     └─ Post-meeting: voting results, vote ratios, DART-first + KIND fallback
│
├─ Data Tools
│  ├─ ownership_structure
│  ├─ financial_metrics
│  ├─ corp_gov_report
│  ├─ dividend
│  ├─ treasury_share
│  ├─ value_up
│  ├─ corporate_restructuring
│  ├─ dilutive_issuance
│  ├─ proxy_contest
│  └─ related_party_transaction
│
├─ Evidence
│  └─ evidence
│     └─ Filing URL, source, and metadata from rcept_no
│
└─ Action Tools
   ├─ proxy_advise_before_meeting
   │  └─ Pre-meeting voting recommendation
   │     ├─ company
   │     ├─ shareholder_meeting_notice
   │     ├─ ownership_structure
   │     ├─ financial_metrics
   │     ├─ corp_gov_report
   │     ├─ dividend / treasury_share / value_up
   │     └─ proxy_contest / evidence
   │
   └─ proxy_result_after_meeting
      └─ Post-meeting result summary
         ├─ shareholder_meeting_results
         ├─ evidence
         └─ Related data tools when needed
```

| Layer | Tools | Role |
|---|---|---|
| Company | `company` | Company identification and common filings index |
| Meeting | `shareholder_meeting_notice`, `shareholder_meeting_results` | Pre/post AGM data |
| Data | `ownership_structure`, `financial_metrics`, `corp_gov_report`, `dividend`, `treasury_share`, `value_up`, `corporate_restructuring`, `dilutive_issuance`, `proxy_contest`, `related_party_transaction` | Filing, financial, ownership, and governance parsers |
| Evidence | `evidence` | Source tracking from filing receipt numbers |
| Action | `proxy_advise_before_meeting`, `proxy_result_after_meeting` | Compose multiple data tools into recommendations/reports |

> Each tool's scope, options, data sources, and validation results: see catalog at **[wiki/tools/README.md](wiki/tools/README.md)** or per-tool pages (`wiki/tools/{name}.md`).

### Voting Policy — Open Proxy Guideline

`proxy_advise_before_meeting` uses the OPM **Open Proxy Guideline** by default:

- 12 categories × 116 rules + 11 novel topics + **7 new 2026 Korea law rules**
- 4 principles: minority shareholder protection / governance transparency / long-term value / traceability
- 38 legal-layer rules covering Commercial Act amendments and articles-of-incorporation bypass scenarios
- An anonymized institutional policy corpus is used only as internal cross-reference. User-facing responses do not expose institution names or identifiers.

**Every data tool returns `data.usage`**: DART API call count + MCP tool call count, so you can track how much of the 1,000/min DART limit each query consumes.

```
Usage pattern: start with `company` → confirm facts via data tabs → generate action outputs
```

### Domain summary

| Domain | Description | Tools |
|--------|-------------|-------|
| **Company** | Company ID + recent filings index | 1 |
| **AGM (pre)** | shareholder_meeting_notice — agendas, board candidates, compensation, articles changes (DART) | 1 |
| **AGM (post)** | shareholder_meeting_results — DART-first + KIND fallback voting results | 1 |
| **Ownership** | Largest shareholders, block holders, control map, change filings | 1 |
| **Dividend** | Actual dividend payouts + quarterly breakdown | 1 |
| **Treasury** | 5 decisions (pre) + 4 result reports (executed) + cycle matching (★ decision-execution validation) | 1 |
| **Proxy contest** | Proxy solicitations, litigation, 5% signals | 1 |
| **Value-up** | Corporate value-up plans, implementation | 1 |
| **Restructuring** | Merger / split / division-merger / share exchange decisions | 1 |
| **Dilution** | Rights offering / CB / BW / capital reduction | 1 |
| **Related-party** | Equity deals + single supply contracts | 1 |
| **Governance** | Corporate governance report (15 core principles, full KOSPI mandatory from 2026) | 1 |
| **Financials** | DART 4-endpoint integration — 51 metrics + DuPont + FCF + NWC + accounting risk + 3-yr audit opinion | 1 |
| **Evidence** | Filing source links | 1 |
| **Action** | proxy_advise_before_meeting (per-agenda decisions + facts/risk/citation/source filings/candidate raw) + proxy_result_after_meeting (post-AGM result) | 2 |
| | **Total** | **16** |

---

## Voting Criteria

When you ask for a voting recommendation on an AGM agenda item, OpenProxy follows the criteria below to return FOR / AGAINST / REVIEW.

| Agenda type | FOR | AGAINST | REVIEW |
|-------------|-----|---------|--------|
| Financial statements | Clean audit opinion | Qualified / adverse | Extreme payout ratio |
| **Outside director election** | Independence + no disqualification | Independence not met / disqualifying issue | 3+ concurrent roles, adverse news |
| **Inside director re-election** | No disqualification + tenure performance good/moderate | Tenure performance **bad** (capital impairment / loss + cumulative deterioration) | Tenure performance **weak** (user review) |
| Inside director (new) | No disqualification (no tenure → performance N/A) | Disqualifying issue | — |
| Compensation limit | Utilization rate reasonable | Rate < 30% yet proposed increase | 50%+ large increase |
| Articles amendment | Statutory update (formal) | Removes cumulative voting | Reduces board size |
| Treasury shares | Cancellation purpose | Entrenchment purpose | Foundation donation |
| Dividend | Above sector average | EPS up but DPS down | Dividend cut |

### Inside director tenure performance matrix (2x3)

Auto-FOR for company-nominated inside directors (only checking disqualification) creates status-quo bias. To counter this, OpenProxy scores each inside director's **tenure-period operating performance** across 6 cells:

| Metric | avg | trend |
|---|---|---|
| **ROE** | average score | trend score |
| **Debt ratio** | average score | cumulative-change score over tenure |
| **CSR** (dividend + cancellation / net income) | average score | trend score |

Each cell: good +2 / moderate +1 / weak 0 / bad -1. Total ≥+7 = good / +3~+6 = moderate / 0~+2 = weak / <0 = bad.

**Special rules**: capital impairment (full) auto-bads ROE/leverage avg / loss + return activity → CSR weak (accelerates impairment) / loss + no return → CSR moderate (conservatism).

Validated on KOSPI 100 + KOSDAQ 50 (n=128): G1 classification coverage 100%, distribution good 29.7% / mod 45.3% / weak 18.0% / bad 7.0% (all within target bands).

---

## Data Sources

| Source | Use | Notes |
|--------|-----|-------|
| [DART OpenAPI](https://opendart.fss.or.kr/) (`opendart.fss.or.kr`) | All structured data: regular/major filings metadata, financial endpoints, dividends, treasury, ownership | **Required** — free API key. 1,000/min hard rule (cap 900) |
| DART Web (`dart.fss.or.kr`) | Filing body HTML parsing (AGM notices, major-event reports — ACODE-based system fields) | Web scraping, `_throttle_web` rate-limited (2-5s) |
| [KRX KIND](https://kind.krx.co.kr/) | Fallback for selected exchange filings | DART original documents are preferred; KIND is auxiliary |
| Anonymized institutional policy corpus | Voting-policy cross-reference | Internal static data. User-facing responses do not expose institution names or identifiers |

---

## Project Structure

```
open_proxy_mcp/
  server.py                # FastMCP server (stdio + HTTP)
  tools_v2/                # 16 tools (active)
  services/                # Domain logic layer (separated from tools)
  dart/client.py           # DART API + KIND fallback + rate limiter (cap 900/min)
  data/asset_managers/     # Anonymized institutional policy corpus + Open Proxy Guideline + 12 matrices
scripts/
  wiki_lint.py             # Wiki link policy auto-validator (downward / bidirectional)
  spot_*.py                # Regression spot scripts (KOSPI/KOSDAQ batch)
wiki/                      # LLM domain knowledge — botanical tree order
  raw/                     # 🌱 Root — external originals (read-only)
  rules/                   # 🪵 Trunk — concepts/ + disclosures/ + laws/ (Korean capital market facts)
  tools/                   # 🌿 Main branch — 16 tool catalog (user entry point)
  decisions/               # 🌿 Main branch — OPM policy (open-proxy-guideline, etc.)
  architecture/            # 🌿 Main branch (core) + 🌾 sub-branch (audits/ + fixes/)
  ralph/                   # 🌾 Sub-branch — work plans (chronological)
  lessons/                 # 🌾 Sub-branch — retrospectives
  archive/                 # 🍂 Fallen — absorbed/superseded pages
  index.md                 # Full index (entry point)
  WIKI_SCHEMA.md           # Tree policy + categories + naming rules
  log.md                   # Operation log
.github/workflows/
  wiki-lint.yml            # Auto lint --strict on wiki/ change (PR/push CI)
  deploy.yml               # Fly.io deployment
Dockerfile                 # Container for Fly.io deployment
fly.toml                   # Fly.io config (nrt region, auto-suspend)
```

---

## Disclaimer

OpenProxy structures DART filing data for AI use. AI can hallucinate and may produce inaccurate analysis. The views expressed by the AI do not represent those of the developer or any affiliated organization. Use analysis results for reference only — final investment decisions and voting judgments must always be verified against the original filings and expert review.

---

## License

[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) -- Non-commercial use only

Please credit the source when using this project's code or data. Commercial use is not permitted.
