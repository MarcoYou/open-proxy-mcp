# OpenProxy MCP

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-green.svg)](https://modelcontextprotocol.io/)
[![Tools](https://img.shields.io/badge/tools-20-orange.svg)](#tool-structure-20-tools)
[![Release](https://img.shields.io/badge/release-v2.1-blue.svg)](docs/RELEASE_NOTES_ENG.md)

[Korean README](README.md)

## Why OpenProxy?

Governance risk is at the heart of the Korea Discount. As passive investing grows, the meaning of share ownership is fading — yet the risk itself is becoming sharper. Accessing and analyzing governance data quickly should be easy, but reading through hundreds of pages of regulatory filings takes more time and expertise than most people have.

**OpenProxy breaks down that barrier using AI.** It converts DART filings into structured data, so anyone can analyze ownership structure, dividend history, AGM agendas, and proxy contests in seconds.

![OpenProxy MCP comparison](screenshot/open-proxy-mcp%20output%20eng.png)

---

## Main Features

Click any feature for a detailed page.

- **[AGM proxy voting](docs/features/en/proxy-voting.md)** — structures the AGM notice agenda items and returns a per-item FOR / AGAINST / REVIEW recommendation with rationale.
- **[Control-contest signals](docs/features/en/control-contest.md)** — gathers proxy solicitation, tender offer, litigation, and 5%-block activism signals (no auto-verdict — it lists information, the analyst decides).
- **[Ownership map](docs/features/en/ownership.md)** — largest shareholder, related parties, 5% blocks, and treasury shares — the real size of control.
- **[AGM agenda](docs/features/en/meeting-agenda.md)** — agenda items, director nominees, compensation limits, articles amendments, plus post-AGM results and approval rates.
- **[Shareholder return](docs/features/en/shareholder-return.md)** — dividends, the treasury buyback-to-cancellation cycle, and value-up plans, comparing what was promised against what was actually executed.
- **[Financial metrics](docs/features/en/financials.md)** — DART financial endpoints unified into ROE, stability, and cash-flow metrics (plus DuPont breakdown and audit-opinion trend). Quarterly results are provided on two bases — cumulative (YTD) and current-quarter (3-month) — with QoQ/YoY, and turnover days use a TTM basis; the basis in use is always stated.
- **Valuation** — PER · PBR · dividend yield (firm-level deep dive) plus market-wide, sector, and per-stock history (weekly snapshots). Controlling-interest basis, automatic FX conversion for non-KRW functional-currency filers (Bank of Korea ECOS), and N/M handling for losses/capital impairment. `scope="explain"` answers "how was this number derived?" with the actual calculation, basis, and source.
- **Corporate risk events** — tracks serious-accident, embezzlement/breach-of-trust, and production-halt filings. With no company specified, it scans the whole market for recent events.

Other capabilities — source tracing, corporate governance report, dilutive issuance, restructuring, order/supply-contract tracking, equity stake deals and related-party transactions, and stewardship follow-up (value-up/dividend/buyback commitment vs. actual execution tracking) — round out the 19-tool set.

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
4. Click "Add" → 20 tools are automatically recognized
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
"Which listed companies filed serious-accident disclosures last month?"  # Market-wide risk scan
```

---

## Tool Structure (20 tools)

20 tools follow the flow **Company → Meeting/Data/Evidence → Action**.

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
│  ├─ valuation
│  ├─ corp_gov_report
│  ├─ director_board
│  ├─ dividend
│  ├─ treasury_share
│  ├─ value_up
│  ├─ corporate_restructuring
│  ├─ dilutive_issuance
│  ├─ proxy_contest
│  ├─ corporate_deals
│  ├─ order_contracts
│  └─ risk_events
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
   ├─ shareholder_meeting_results (post-AGM outcomes)
   │  └─ Post-meeting result summary
   │
   └─ shareholder_commitment
      └─ Value-up/dividend/buyback commitment vs. actual execution (book-value impact of buyback-cancellation cycles)
         └─ value_up / corp_gov_report / dividend / treasury_share / financial_metrics
```

| Layer | Tools | Role |
|---|---|---|
| Company | [`company`](wiki/tools/company.md) | Company identification and common filings index |
| Meeting | [`shareholder_meeting_notice`](wiki/tools/shareholder_meeting_notice.md), [`shareholder_meeting_results`](wiki/tools/shareholder_meeting_results.md) | Pre/post AGM data |
| Data | [`ownership_structure`](wiki/tools/ownership_structure.md), [`financial_metrics`](wiki/tools/financial_metrics.md), [`valuation`](wiki/tools/valuation.md), [`corp_gov_report`](wiki/tools/corp_gov_report.md), [`director_board`](wiki/tools/director_board.md), [`dividend`](wiki/tools/dividend.md), [`treasury_share`](wiki/tools/treasury_share.md), [`value_up`](wiki/tools/value_up.md), [`corporate_restructuring`](wiki/tools/corporate_restructuring.md), [`dilutive_issuance`](wiki/tools/dilutive_issuance.md), [`proxy_contest`](wiki/tools/proxy_contest.md), [`corporate_deals`](wiki/tools/corporate_deals.md), [`order_contracts`](wiki/tools/order_contracts.md), [`risk_events`](wiki/tools/risk_events.md) | Filing, financial, ownership, and governance parsers |
| Evidence | [`evidence`](wiki/tools/evidence.md) | Source tracking from filing receipt numbers |
| Action | [`proxy_advise_before_meeting`](wiki/tools/proxy_advise_before_meeting.md) | Compose multiple data tools into recommendations/reports (post-AGM outcomes: [`shareholder_meeting_results`](wiki/tools/shareholder_meeting_results.md)) |

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
| **Related-party** | Equity/affiliate stake deals (corporate_deals) — acquisitions/disposals of other companies' shares | 1 |
| **Order contracts** | Single sales/supply contract execution & termination — external order backlog, % of revenue, amendment diffs (order_contracts) | 1 |
| **Governance** | Corporate governance report (15 core principles, full KOSPI mandatory from 2026) | 1 |
| **Financials** | DART 4-endpoint integration — 56 metrics + DuPont + FCF + NWC + accounting risk + 3-yr audit opinion | 1 |
| **Valuation** | PER (FY0/TTM) · PBR (MRQ) · dividend yield — controlling-interest basis, auto FX for non-KRW filers, scale guard + N/M gating; also market/sector/firm-history time series | 1 |
| **Risk events** | Serious industrial accidents / embezzlement·breach of trust / production halt — active categories; market-wide scan when no company given | 1 |
| **Evidence** | Filing source links | 1 |
| **Action** | proxy_advise_before_meeting (per-agenda decisions + facts/risk/citation/source filings/candidate raw) | 1 |
| **Stewardship follow-up** | Value-up/dividend/buyback promise vs. actual execution; book-value (BPS) impact of buyback-cancellation cycles | 1 |
| | **Total** | **19** |

---

## Voting Criteria

When you ask for a voting recommendation on an AGM agenda item, OpenProxy returns FOR / AGAINST / REVIEW from the **information inside DART filings only** — it does not check external news or reputation.

**AGAINST is reserved for hard triggers.** Only four conditions produce AGAINST: capital impairment (full), qualified/adverse audit opinion, director disqualification, and audit-committee long tenure. Every other concern returns REVIEW (OpenProxy flags it; the analyst decides) — never an automatic AGAINST.

| Agenda type | FOR | REVIEW | AGAINST |
|-------------|-----|--------|---------|
| Financial statements | Clean audit opinion + no impairment | Data not confirmed (NO_DATA) | Full capital impairment / qualified-adverse opinion |
| **Outside director election** | Independence + no disqualification | Independence concern (largest-shareholder tie / dealing with company / former employee) / long tenure / prior accounting-risk history | Disqualification / (audit committee) long tenure |
| **Inside director re-election** | No disqualification + tenure performance good/moderate | Tenure performance weak **or bad** (not a legal disqualification → user review) | Disqualification |
| Inside director (new) | No disqualification (no tenure → performance N/A) | — | Disqualification |
| Compensation limit | Utilization reasonable / cut / minor change | Low utilization yet increase / 50%+ large increase / earnings slowdown + increase | (none) |
| Articles amendment | Formal / minority-protection wording / fiduciary duty | Removes cumulative voting / supermajority / board-size cut / authorized-shares increase | (none) |
| Treasury shares | Cancellation (shareholder return) | Disposal (possible friendly stake) | (none) |
| Dividend | Profit + sound capital / REIT | Loss / impairment / payout > 200% | (none) |

### Inside director tenure performance matrix (2x3)

Auto-FOR for company-nominated inside directors (only checking disqualification) creates status-quo bias. To counter this, OpenProxy scores each inside director's **tenure-period operating performance** across 6 cells:

| Metric | avg | trend |
|---|---|---|
| **ROE** | average score | trend score |
| **Debt ratio** | average score | cumulative-change score over tenure |
| **CSR** (dividend + cancellation / net income) | average score | trend score |

Each cell: good +2 / moderate +1 / weak 0 / bad -1. Total: +7 or above = good / +3 to +6 = moderate / 0 to +2 = weak / below 0 = bad. Grade maps to the vote as **good/moderate → FOR, weak/bad → REVIEW** (a bad grade is not a legal disqualification, so it never auto-produces AGAINST).

**Special rules**: capital impairment (full) auto-bads ROE/leverage avg / loss + return activity → CSR weak (accelerates impairment) / loss + no return → CSR moderate (conservatism).

Validated on KOSPI 100 + KOSDAQ 50 (n=128): G1 classification coverage 100%, distribution good 29.7% / mod 45.3% / weak 18.0% / bad 7.0% (all within target bands).

---

## Data Sources

| Source | Use | Notes |
|--------|-----|-------|
| [DART OpenAPI](https://opendart.fss.or.kr/) (`opendart.fss.or.kr`) | All structured data: regular/major filings metadata, financial endpoints, dividends, treasury, ownership | **Required** — free API key. 1,000/min hard rule (cap 910) |
| DART Web (`dart.fss.or.kr`) | Filing body HTML parsing (AGM notices, major-event reports — ACODE-based system fields) | Web scraping, `_throttle_web` rate-limited (2-5s) |
| [KRX KIND](https://kind.krx.co.kr/) | Fallback for selected exchange filings | DART original documents are preferred; KIND is auxiliary |
| Anonymized institutional policy corpus | Voting-policy cross-reference | Internal static data. User-facing responses do not expose institution names or identifiers |

---

## Project Structure

```
open_proxy_mcp/
  server.py                # FastMCP server (stdio + HTTP)
  tools_v2/                # 20 tools (active)
  services/                # Domain logic layer (separated from tools)
  dart/client.py           # DART API + KIND fallback + rate limiter (cap 910/min)
  data/asset_managers/     # Anonymized institutional policy corpus + Open Proxy Guideline + 12 matrices
scripts/
  wiki_lint.py             # Wiki link policy auto-validator (downward / bidirectional)
  spot_*.py                # Regression spot scripts (KOSPI/KOSDAQ batch)
wiki/                      # LLM domain knowledge — botanical tree order
  raw/                     # 🌱 Root — external originals (read-only)
  rules/                   # 🪵 Trunk — concepts/ + disclosures/ + laws/ (Korean capital market facts)
  tools/                   # 🌿 Main branch — 20 tool catalog (user entry point)
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

## Release notes

Full version history lives in **[docs/RELEASE_NOTES_ENG.md](docs/RELEASE_NOTES_ENG.md)**.

- Latest: **v2.1** — new risk_events tool, corporate_deals rename, ownership/dividend/financial_metrics precision passes

---

## Disclaimer

OpenProxy structures DART filing data for AI use. AI can hallucinate and may produce inaccurate analysis. The views expressed by the AI do not represent those of the developer or any affiliated organization. Use analysis results for reference only — final investment decisions and voting judgments must always be verified against the original filings and expert review.

---

## License

[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) -- Non-commercial use only

Please credit the source when using this project's code or data. Commercial use is not permitted.
