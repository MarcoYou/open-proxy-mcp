# OpenProxy MCP

[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/License-PolyForm%20Noncommercial%201.0.0-lightgrey.svg)](https://polyformproject.org/licenses/noncommercial/1.0.0/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-green.svg)](https://modelcontextprotocol.io/)
[![Tools](https://img.shields.io/badge/tools-31-orange.svg)](#tool-structure-31-tools)
[![Release](https://img.shields.io/badge/release-v2.5-blue.svg)](docs/RELEASE_NOTES_ENG.md)

[Korean README](README.md)

[Quick Start](#quick-start) · [Main Features](#main-features) · [Tool Structure](#tool-structure-31-tools) · [Data Sources](#data-sources)

## Why OpenProxy?

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="screenshot/opm-readme-particle-flow-dark-en-20260906.png">
  <source media="(prefers-color-scheme: light)" srcset="screenshot/opm-readme-particle-flow-light-en-20260906.png">
  <img alt="Filings converging into AI financial analysis and voting recommendations" src="screenshot/opm-readme-particle-flow-light-en-20260906.png">
</picture>

**An agenda item may fit on one line. A sound decision requires the full picture.**

OpenProxy began with AGM and proxy voting analysis. The capabilities needed to read financial statements, ownership structures, dividend history, boards, and relevant laws together grew into a general-purpose engine for DART filings. From financial analysis to voting recommendations, AI presents each conclusion with the underlying source evidence.

## Quick Start

**Connect with one DART API key. Nothing to install.**

### 1. Get a free API key

DART is South Korea's corporate disclosure system. Sign up at [DART OpenAPI](https://opendart.fss.or.kr/) and request a free authentication key.

### 2. Connect your AI service

Enter this URL as the server address when adding a connector or app:

```
https://open-proxy-mcp.fly.dev/mcp?opendart=YOUR_DART_API_KEY
```

> Enter this URL only in the connector settings. OpenProxy does not retain the raw key and redacts it from logs.

| Service | Setup path | Availability |
|---|---|---|
| [**Claude**](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp) | `Customize → Connectors → + → Add custom connector` | Free (one connector), Pro, Max; admin setup for Team/Enterprise |
| [**ChatGPT**](https://developers.openai.com/api/docs/guides/developer-mode) | `Settings → Security and login → Developer mode`, then `Plugins → +` | Plus, Pro, Business, Enterprise, Education on web |
| [**Perplexity**](https://www.perplexity.ai/help-center/en/articles/13915507-adding-custom-remote-connectors.html) | `Account settings → Connectors → + Custom connector → Remote` | Pro, Max, Enterprise |

Name it `open-proxy-mcp` and select it in a new chat. Availability may vary with workspace settings.

### 3. Send your first request

Start with `Show Samsung Electronics' company information and three recent filings.` If the company and filings appear, the connection works. Continue in natural language; you do not need to know the tool names.

- `Review LG Chem's next AGM agenda and give an evidence-backed voting view on each item.`
- `Compare Samsung Electronics' last three years of results with the next two years of consensus estimates.`

Find more prompts on each page in the [tool catalog](wiki/tools/README.md).

---

## Main Features

**Read the filing. Connect the numbers. Keep the evidence behind every conclusion.**

| Analysis | The question | What OpenProxy delivers |
|---|---|---|
| 🗳️ [AGM & proxy voting](docs/features/en/proxy-voting.md) | How should I vote on this item? | **FOR / AGAINST / REVIEW** with filing evidence, policy citations, and statute links; NO_VOTE and NO_DATA remain distinct |
| 📊 [Financials & earnings](docs/features/en/financials.md) | Where did performance change? | Confirmed, [provisional](docs/features/en/provisional-earnings.md), and consensus comparisons with profitability, cash flow, and DuPont analysis |
| 💹 [Valuation & estimates](docs/features/en/price_multiple_data.md) | What is priced in? | Historical and forward PER/PBR/PSR, dividend yield, and [estimates for the next two years](wiki/tools/forward_estimates_data.md) |
| 🏭 [Business & assets](docs/features/en/business-details.md) | How does it make money, and what does it own? | Segments, utilization, input costs, backlog, [surplus assets, and stake NAV](docs/features/en/asset-holdings.md) |
| 🧭 [Ownership & returns](docs/features/en/ownership.md) | Who controls it, and where does capital go? | Ownership map, dividends, buybacks and cancellations, and [value-up plans versus actual execution](docs/features/en/shareholder-return.md) |
| 🔔 [Market & risk](wiki/tools/screener.md) | What changed today? | Market disclosure digest plus [control contests](docs/features/en/control-contest.md), deals, dilution, and [risk events](docs/features/en/risk-events.md) |

These six workflows are backed by **31 tools**, including source tracing and two-way lookup between articles of incorporation and statutes. See the complete [Tool Structure](#tool-structure-31-tools).

---

## Tool Structure (31 tools)

Categories match the "what do you want to know → which tool" table in the [wiki/tools catalog](wiki/tools/README.md) — that table is the source of truth.

| Category | Tools | Role |
|---|---|---|
| 🏢 Start — find the company | [`company`](wiki/tools/company.md) | Company identification + recent filings — every analysis starts here |
| 🔔 Market-wide scan · digest | [`screener`](wiki/tools/screener.md) | Market-wide disclosure screener / morning digest |
| 🗳️ Shareholder meetings · voting | [`shareholder_meeting_notice`](wiki/tools/shareholder_meeting_notice.md), [`shareholder_meeting_results`](wiki/tools/shareholder_meeting_results.md), [`proxy_advise_before_meeting`](wiki/tools/proxy_advise_before_meeting.md), [`proxy_guideline`](wiki/tools/proxy_guideline.md) | Notice (pre) · results (post) · per-agenda FOR/AGAINST/REVIEW support · the voting-policy document itself |
| 💰 Ownership · financials · governance | [`ownership_structure`](wiki/tools/ownership_structure.md), [`financial_metrics`](wiki/tools/financial_metrics.md), [`provisional_earnings`](wiki/tools/provisional_earnings.md), [`business_details`](wiki/tools/business_details.md), [`asset_holdings`](wiki/tools/asset_holdings.md), [`price_multiple_data`](wiki/tools/price_multiple_data.md), [`forward_estimates_data`](wiki/tools/forward_estimates_data.md), [`trading_data`](wiki/tools/trading_data.md), [`corp_gov_report`](wiki/tools/corp_gov_report.md), [`director_board`](wiki/tools/director_board.md) | Ownership map · confirmed/provisional earnings · business description · asset plays · PER/PBR · consensus · price/market cap · governance report · board |
| 🎁 Shareholder returns · capital | [`dividend_disclosure`](wiki/tools/dividend_disclosure.md), [`dividend_data`](wiki/tools/dividend_data.md), [`treasury_share`](wiki/tools/treasury_share.md), [`value_up`](wiki/tools/value_up.md), [`shareholder_commitment`](wiki/tools/shareholder_commitment.md), [`corporate_restructuring`](wiki/tools/corporate_restructuring.md), [`dilutive_issuance`](wiki/tools/dilutive_issuance.md) | Dividend filings/time series · treasury shares · value-up · promise vs delivery · mergers/splits · rights/CB/BW/reductions |
| ⚔️ Contests · deals · risk | [`proxy_contest`](wiki/tools/proxy_contest.md), [`corporate_deals`](wiki/tools/corporate_deals.md), [`order_contracts`](wiki/tools/order_contracts.md), [`risk_events`](wiki/tools/risk_events.md), [`financial_notes`](wiki/tools/financial_notes.md), [`director_news`](wiki/tools/director_news.md) | Control-contest signals · stake deals · orders/supply contracts · risk events · financial-firm notes · director-candidate news |
| 🔗 Evidence · reference | [`evidence`](wiki/tools/evidence.md), [`law_lookup`](wiki/tools/law_lookup.md) | Filing number → viewer URL · bidirectional articles↔statute lookup (zero API calls) |

> Per-tool example questions, schemas & data sources → [wiki/tools catalog](wiki/tools/README.md) (natural-language examples live in each tool page's usage section)

### Voting policy

**Policy opposition does not always mean an automatic AGAINST recommendation.** The engine leaves judgment-dependent concerns as REVIEW; board attendance is not currently a decision trigger. Ask for the cited policy section with `proxy_guideline`, or section `0-A` for the policy-to-engine mapping. [How to interpret recommendations, meeting selection, and information cutoffs](docs/features/en/proxy-voting.md). The default report and detailed policy references are in Korean; ask your AI to explain them in English while preserving the evidence and statuses.

`proxy_advise_before_meeting` uses OPM's own **Open Proxy Guideline** as its default policy. Its criteria: minority-shareholder protection, governance transparency, long-term value, traceability. An anonymized institutional-policy corpus is used only as internal cross-reference — no institution names are ever exposed. Every response includes a `data.usage` block (DART & tool call counts; DART limit 1,000/min — hard-capped at 910).

**Check the financial basis** — distinguish confirmed figures for the year being approved, provisional figures in the meeting notice, and prior confirmed figures. Provisional figures do not replace every metric: check the reported year, source, and provisional labels. A provisional capital-impairment assessment does not replace a regulatory determination requiring audited financial statements. See the [feature guide](docs/features/en/proxy-voting.md) for information cutoffs and later filings.

---

## Data Sources

| Source | Use | Notes |
|------|------|------|
| [DART OpenAPI](https://opendart.fss.or.kr/) | Filing metadata + financial endpoints + dividends/treasury/ownership | **Required** — free API key. 1,000/min hard rule (cap 910) |
| DART web (`dart.fss.or.kr`) | Filing body parsing (AGM notices, material reports) | rate-limited (random 1–2s between requests) |
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
