# OpenProxy MCP

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-green.svg)](https://modelcontextprotocol.io/)
[![Tools](https://img.shields.io/badge/tools-17-orange.svg)](#tool-structure-17-tools)

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
4. Click "Add" → 17 tools are automatically recognized
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
"Analyze E-Mart's recent share exchange / transfer decision"
"Show the split decision details and counterparty for Gamsung Corporation"
"Calculate the potential dilution from Hyper Corp's recent convertible bond"
"List EDGC's rights offerings and capital reductions over the last two years"
"POSCO Holdings — show recent subsidiary equity deal disclosures"
"Hyundai E&C — single supply contract pattern for the last 2 years"
"Show KT&G's corporate governance report compliance rate"
"Hyundai Motor — all 15 governance core principles with notes"
"KT&G AGM vote brief (activist manager style)"
"Compare 8 asset managers' director compensation voting policies"
"Audit S-legacy (large manager) policy vs actual voting gap"
"Show M-legacy 2025 voting record on Samsung Electronics"
"Show Open Proxy Guideline 12-category policy"
"Lotte Chemical 2024 consolidated YoY + accounting risk alerts"
"SK Hynix 2024 turnaround validation (loss → profit, DuPont split)"
"Osstem Implant 5-year audit opinion timeline"
"Samsung Electronics DuPont 3-step decomposition — what drove ROE 13.07%"
```

\* Financial metrics analysis is now available via the `financial_metrics` tool (Phase 1: DART 4-endpoint integration — 51 core indicators + 22 alerts + audit opinion timeline)

---

## Tool Structure (16 tools)

16 tools follow the flow **company → time-split AGM → data tabs → action analysis**.

```
company                            # Company ID + recent filings index
│
├─ Meeting Tools (2)
│  ├─ shareholder_meeting_notice   # AGM convocation notice (pre-meeting, DART)
│  └─ shareholder_meeting_results  # AGM voting results (post-meeting, KIND)
│
├─ Data Tools (10)
│  ├─ ownership_structure          # Ownership (largest shareholders / 5% / control_map)
│  ├─ dividend                     # Dividend facts + quarterly breakdown
│  ├─ financial_metrics            # DART 4-endpoint — 51 metrics + DuPont
│  ├─ treasury_share               # Treasury 5 decisions + 4 results + cycle matching
│  ├─ proxy_contest                # Proxy contest (solicitation / litigation / 5%)
│  ├─ value_up                     # Value-up plan (commitments / implementation)
│  ├─ corporate_restructuring      # Merger / split / share exchange (unified)
│  ├─ dilutive_issuance            # Rights offering / CB / BW / capital reduction
│  ├─ related_party_transaction    # Related-party (equity deals + supply contracts)
│  ├─ corp_gov_report              # Corporate governance report (15 principles)
│  └─ evidence                     # Filing source links (rcept_no → URL)
│
└─ Action Tools (2)
   ├─ proxy_advise_before_meeting  # Pre-AGM per-agenda FOR/AGAINST/REVIEW/NO_DATA
   └─ proxy_result_after_meeting   # Post-AGM result report
```

> Each tool's scope, options, data sources, and validation results: see catalog at **[wiki/tools/README.md](wiki/tools/README.md)** or per-tool pages (`wiki/tools/{name}.md`).

### Recent changes (2026-05-04~09)

- 17 → 16 tools: `screen_events` dropped, `proxy_guideline` archived (kept internal only), `shareholder_meeting` split → notice + results
- proxy_advise scope **10 → 1** (`decisions` only; raw data via direct data-tool calls)
- treasury_share scope **6 → 2**; 4 result reports added; decision↔execution cycle matching
- DART 1,000/min hard rule (rolling-window rate limiter, cap 900)
- **Inside director tenure performance matrix (2x3)** — auto-FOR for company-nominated inside directors creates status-quo bias. ROE / debt ratio / CSR × avg / trend, bad → AGAINST · weak → REVIEW. Validated on KOSPI 100 + KOSDAQ 50 (n=128): G1 100% / distribution within target bands.
- **Compensation / retirement classification refinement** — director 13 / auditor 11 / retirement 12 branches + articles_amendment hybrid integration. KOSPI 200 + KOSDAQ 50 (n=226): G1 99-100% / G3 manager majority alignment 100% / G4 NPS policy alignment 100%.
- **Law layer refinement (Ralph 4, 2026-05-08)** — 1st·2nd·3rd Commercial Law amendments + articles bypass scenarios → 38 rule catalog (A1=8 / A2=5 / B1=12 / B2=9 / C=4). Catches KT&G articles pre-bypass (B1-8b), 1-year director term in proxy contests (B1-4b), board-size reduction (B1-7). Validated on 280 companies (KOSPI 200 + KOSDAQ 100 + dispute 20) — false positive 0, regression 0%. proxy_advise responses now tag `[Law X-Y]`.
- **Wiki tree policy + lint hook (2026-05-09)** — botanical metaphor (🌱root → 🪵trunk → 🌿main branch → 🌾sub-branch → 🍂fallen). Link policy (downward-only / bidirectional / free) + `scripts/wiki_lint.py` + GitHub Actions CI auto-validation.
- **`financial_metrics` yoy parallelization (2026-05-09 perf)** — sequential 3 calls → `asyncio.gather`. Per-company ~3s → ~1s (2-3x faster).

### Internal policies & matrices

**8 asset managers' policy data** (parsed JSON, 14MB+ static, anonymized):
- M-legacy / S-legacy / SA-active / K-legacy (4 large legacy managers)
- T-activist / A-activist / C-activist (3 activist managers)
- B-foreign (references ISS Korea 2026 — but OPM does NOT use ISS as benchmark)

> Note: Real manager names are kept internal-only (gitignored mapping file). External docs use anonymized initials + classification.

**Open Proxy Guideline v1.2** (OPM proprietary best-practice policy):
- 12 categories × 116 rules + 11 novel topics + **7 new 2026 Korea laws** (5 managers haven't reflected yet)
- 4 principles: minority shareholder protection / governance transparency / long-term value / traceability
- Commercial Law §382의3 (2025) fiduciary duty cross-cutting

**12 decision matrices** (unique to OPM, no asset manager or proxy advisor offers this):
- 8 dim per category (independence / conflict / compensation / disclosure / compliance rate / consistency / legal procedure / ESG)
- Total 100 dim + 76 bingo patterns (specific combinations auto-trigger decisions)

**Every data tool returns `data.usage`**: DART API call count + MCP tool call count, so you can track how much of the 1,000/min DART limit each query consumes.

```
Usage pattern: start with `company` → confirm facts via data tabs → generate action outputs
```

### Domain summary

| Domain | Description | Tools |
|--------|-------------|-------|
| **Company** | Company ID + recent filings index | 1 |
| **AGM (pre)** | shareholder_meeting_notice — agendas, board candidates, compensation, articles changes (DART) | 1 |
| **AGM (post)** | shareholder_meeting_results — KIND voting results | 1 |
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
| **Action** | proxy_advise_before_meeting (per-agenda decisions + facts/risk/citation/source filings/candidate raw, ralph G2 99.36%) + proxy_result_after_meeting (post-AGM result) | 2 |
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
| [KRX KIND](https://kind.krx.co.kr/) | AGM voting results (post-meeting) | Web crawl |
| [Naver Finance](https://finance.naver.com/) | Sector name lookup (`company` tool) | Web scraping |
| Asset manager voting policies & records | 8 manager policies + voting records (17,900+ votes total, anonymized) | Static parsed JSON — `proxy_advise_before_meeting`'s `vote_style` option |

---

## Project Structure

```
open_proxy_mcp/
  server.py                # FastMCP server (stdio + HTTP)
  tools_v2/                # 16 tools (active)
  services/                # Domain logic layer (separated from tools)
  dart/client.py           # DART API + KIND crawl + Naver + rate limiter (cap 900/min)
  data/asset_managers/     # 8 manager policies (anonymized) + records + Open Proxy Guideline + 12 matrices
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
