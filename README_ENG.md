# OpenProxy MCP

[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/License-PolyForm%20Noncommercial%201.0.0-lightgrey.svg)](https://polyformproject.org/licenses/noncommercial/1.0.0/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-green.svg)](https://modelcontextprotocol.io/)
[![Tools](https://img.shields.io/badge/tools-26-orange.svg)](#tool-structure-26-tools)
[![Release](https://img.shields.io/badge/release-v2.2-blue.svg)](docs/RELEASE_NOTES_ENG.md)

[Korean README](README.md)

## Why OpenProxy?

**To vote properly on an AGM agenda, you need to know everything about the company.**

OpenProxy was born for AGM proxy-voting analysis. But judging a single agenda item turned out to require everything — financial statements, ownership structure, dividend history, the board, even the law. So we built it all, and it became a **general-purpose engine for Korean regulatory (DART) filings**. From financial analysis to voting recommendations, ask an AI and get answers in seconds, backed by the underlying filings.

![Financial & cash-flow analysis example](screenshot/opx-cashflow.png)
*Financial analysis grounded in filings (annual & audit reports) — an example conversation with OpenProxy connected*

![AGM agenda analysis example](screenshot/opx-agm.png)
*And this is where it started — AGM notices, statutes, and governance reports combined into per-item opinions with rationale*

## Main Features

Click any feature for a detailed page.

- **[AGM proxy voting](docs/features/en/proxy-voting.md)** — structures AGM notice agenda items and returns per-item FOR / AGAINST / REVIEW recommendations with rationale.
- **[Financial metrics](docs/features/en/financials.md)** — profitability, stability, cash flow + DuPont breakdown and audit-opinion trend. Quarterly on two bases (YTD / 3-month) with QoQ·YoY.
- **[Valuation](docs/features/en/valuation.md)** — PER · PBR · dividend yield (firm deep-dive) plus market/sector/ticker history. `scope="explain"` shows how each number was derived.
- **[Asset-holdings screen](docs/features/en/asset-holdings.md)** — tiers a firm's holdings (cash, investment property, equity stakes), marks listed stakes to market, and compares surplus-asset / equity-NAV to market cap to surface "hidden asset" plays.
- **[Business details](docs/features/en/business-details.md)** — segment revenue & profit, production capacity & utilization, R&D, order backlog, key customers — reads the "Business Overview" section for you.
- **[Provisional earnings](docs/features/en/provisional-earnings.md)** — quarterly preliminary earnings filings, tabulated with growth rates.
- **[Shareholder return](docs/features/en/shareholder-return.md)** — dividends, buyback-to-cancellation cycles, value-up plans — promises vs. actual execution.
- **[Ownership map](docs/features/en/ownership.md)** — largest shareholder, related parties, 5% blocks, treasury shares.
- **[AGM agenda](docs/features/en/meeting-agenda.md)** — agenda items, nominees, compensation limits, articles amendments, plus post-AGM results and approval rates.
- **[Control-contest signals](docs/features/en/control-contest.md)** — proxy solicitation, tender offers, litigation, 5% activism signals (no auto-verdict).
- **[Corporate risk events](docs/features/en/risk-events.md)** — serious accidents, embezzlement/breach-of-trust, production halts. Scans the whole market if no company is given.
- **[Market-wide disclosure digest](wiki/tools/screener.md)** — sweeps orders, buybacks, dividends, capital increases, AGM notices, 5% blocks, and provisional earnings into a card digest — a morning disclosure-alert routine ([recipe](docs/routines/screener-morning-digest.md)).

Plus source tracing, corporate governance reports, dilutive issuance (rights/CB), restructuring (mergers/splits), stake deals, and bidirectional articles↔statute lookup — **26 tools in total**.

---

## Quick Start

OpenProxy MCP is a **remote server you connect** to AI services like Claude, ChatGPT, or Perplexity. No installation needed.

### Step 1: Get a DART API key (required, free)

OPM combines three sources — DART filing text, exchange filing text, and the OpenDART API. Your own key is needed for the OpenDART API calls.
Go to [DART OpenAPI](https://opendart.fss.or.kr/) → sign up → request an authentication key (issued immediately).

### Step 2: Connect to your AI service

Register the URL below in your AI service's connector (app) settings.

```
https://open-proxy-mcp.fly.dev/mcp?opendart=YOUR_DART_API_KEY
```

> **API key caution**: the URL contains your personal API key. Don't paste it into a normal chat — only into the server-URL field of the connector settings.

The procedure is the same everywhere — **add a connector named `open-proxy-mcp` with the URL above → open a new chat and confirm the connector is selected via the `+` button**:

| Service | Menu path | Notes |
|---|---|---|
| **Claude** | Settings → Connectors → Custom → Add connector | Paid plan required. Afterwards set tool permission to **Always allow** |
| **ChatGPT** | Settings → Apps → enable `Developer mode` in advanced settings → Create app | New chat `+` → More to select |
| **Perplexity** | Settings → Connectors → Add custom connector | — |

> **Note**: connector menus may be unavailable depending on plan/account. The first call may time out while the server spins up — retry shortly. If new features don't appear, remove and re-add the connector.

### Usage examples

Once connected, just ask in natural language. You don't need to know tool names.

**AGM agenda review**
1. `Show me LG Chem's 2026 AGM agenda`
2. `Advise FOR/AGAINST/REVIEW for each item`
3. `Explain the rationale behind any REVIEW items`

**Shareholder-return check**
1. `Show me KT&G's corporate value-up plan`
2. `Show dividends and buybacks for the last 3 years`
3. `Is the actual return consistent with the plan?`

**Risk monitoring**
1. `Which listed companies filed serious-accident or embezzlement disclosures in the last month?`
2. `Show Hanwha Aerospace's serious-accident history in detail, including casualties`

More examples (director pay, control contests, financials, valuation) → **[docs/examples/](docs/examples/README.md)**

---

## Tool Structure (26 tools)

Tools flow **Company → Meeting/Data/Evidence → Action** (statute lookup is a company-independent Reference).

| Layer | Tools | Role |
|---|---|---|
| Discovery | [`getting_started`](wiki/tools/getting_started.md) | "What can this do?" — auto-generated capability overview |
| Company | [`company`](wiki/tools/company.md) | Company identification and common filing index |
| Meeting | [`shareholder_meeting_notice`](wiki/tools/shareholder_meeting_notice.md), [`shareholder_meeting_results`](wiki/tools/shareholder_meeting_results.md) | Pre-/post-AGM data |
| Data | [`corp_gov_report`](wiki/tools/corp_gov_report.md), [`director_board`](wiki/tools/director_board.md), [`corporate_restructuring`](wiki/tools/corporate_restructuring.md), [`dilutive_issuance`](wiki/tools/dilutive_issuance.md), [`dividend`](wiki/tools/dividend.md), [`financial_metrics`](wiki/tools/financial_metrics.md), [`valuation`](wiki/tools/valuation.md), [`business_details`](wiki/tools/business_details.md), [`provisional_earnings`](wiki/tools/provisional_earnings.md), [`asset_holdings`](wiki/tools/asset_holdings.md), [`ownership_structure`](wiki/tools/ownership_structure.md), [`corporate_deals`](wiki/tools/corporate_deals.md), [`order_contracts`](wiki/tools/order_contracts.md), [`proxy_contest`](wiki/tools/proxy_contest.md), [`risk_events`](wiki/tools/risk_events.md), [`treasury_share`](wiki/tools/treasury_share.md), [`value_up`](wiki/tools/value_up.md) | Individual filing / financial / business / governance parsing |
| Evidence | [`evidence`](wiki/tools/evidence.md) | Source tracing by filing number |
| Action | [`proxy_advise_before_meeting`](wiki/tools/proxy_advise_before_meeting.md), [`shareholder_commitment`](wiki/tools/shareholder_commitment.md), [`screener`](wiki/tools/screener.md) | Orchestrate data tools into judgments, comparisons, digests |
| Reference | [`law_lookup`](wiki/tools/law_lookup.md) | Bidirectional articles↔statute lookup (Commercial Act, FSCMA, etc.) — zero API calls |

> Per-tool example questions → [docs/examples/](docs/examples/README.md) · schemas & data sources → [wiki/tools catalog](wiki/tools/README.md)

### Voting policy

`proxy_advise_before_meeting` uses OPM's own **Open Proxy Guideline** as its default policy. Its criteria: minority-shareholder protection, governance transparency, long-term value, traceability. An anonymized institutional-policy corpus is used only as internal cross-reference — no institution names are ever exposed. Every response includes a `data.usage` block (DART & tool call counts; DART limit 1,000/min — hard-capped at 910).

---

## Data Sources

| Source | Use | Notes |
|------|------|------|
| [DART OpenAPI](https://opendart.fss.or.kr/) | Filing metadata + financial endpoints + dividends/treasury/ownership | **Required** — free API key. 1,000/min hard rule (cap 910) |
| DART web (`dart.fss.or.kr`) | Filing body parsing (AGM notices, material reports) | rate-limited (2–5s) |
| [KRX KIND](https://kind.krx.co.kr/) | Exchange-filing cross-checks | auxiliary source |
| Anonymized institutional policy corpus | Voting-judgment cross-reference | internal static data, no names exposed |

---

## Release Notes

Version history → **[docs/RELEASE_NOTES_ENG.md](docs/RELEASE_NOTES_ENG.md)**

---

## Disclaimer

OpenProxy is a tool that structures DART filing data for AI consumption. AI can hallucinate and may produce inaccurate analysis. Opinions presented by the AI are not those of the developer or any affiliated organization. Use the output for reference only — final investment or voting decisions must go through the original filings and expert review.

---

## License

[PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0/) — noncommercial use only (full text: root [`LICENSE`](LICENSE))

- **Noncommercial use** (personal research, learning, nonprofits, public institutions) is freely permitted.
- **Commercial use** requires a separate license agreement (OpenProxy AI).
- **Attribution on redistribution**: keep `Copyright (c) 2026 OpenProxy AI (https://github.com/MarcoYou/open-proxy-mcp)` intact (PolyForm 'Notices' clause).

> Commercial licensing & inquiries: gunhoqw20@gmail.com
