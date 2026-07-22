# Release Notes

Version history for OpenProxy MCP. [한국어](RELEASE_NOTES.md)

## Since v2.3 (unreleased, 2026-07-22 ~ )

- **`business_details` — added raw-material and product-pricing fields** — `raw_materials` separately captures material composition/purchases and input-price trends, so an omission in one segment cannot hide a valid table in another. `product_pricing` returns product/service prices, ASP, and price-change rationale as its own section. Verified with LG Chem raw-material and Samsung Electronics product-pricing production MCP smoke tests plus a local 300-company sample.
- **Removed `getting_started` (26→25 tools, Discovery category retired)** — reversed one day after
  launch. Broad capability questions ("what can this do?") are adequately handled by the FastMCP
  `instructions` orientation plus the client model reading tool descriptions directly; a dedicated
  tool only added list overhead. The design-review record (tool vs. resource arguments) is preserved
  in the wiki decision doc with a postscript.

## v2.3 (2026-07-20)

26-tool lineup. Centered on a new capability-question tool and an extension to `business_details`
for point-in-time lookups.

- **New `getting_started` tool (26th, new Discovery category)** — answers broad capability questions
  like "what can this do?". Decided by a 4-expert panel (MCP protocol, LLM tool-use practice,
  multi-client, DX/maintenance) that favored a tool over a resource or doing nothing — per the MCP
  spec, a model deciding on its own to respond to a vague question is model-controlled territory,
  which is exactly what tools (not resources) are designed for, and resources aren't reliably
  supported the same way across Claude/ChatGPT/Perplexity. While reviewing, the panel found that the
  v1 toolset's `tool_guide` had been completely abandoned after the v2 rewrite — it shares zero tool
  names with what's registered today. Taking that as a cautionary tale, the new tool's content is
  assembled at runtime via `mcp.list_tools()` introspection instead of hardcoded markdown, so drift
  is structurally impossible. Also added a FastMCP `instructions` field (a one-time orientation sent
  at connection time).
- **`business_details` — point-in-time lookups via bsns_year+reprt_code** — the existing `period`
  parameter (latest/annual/quarterly) always returned only the most recent filing, so there was no
  way to answer segment-revenue-trend questions (discovered in a live session where an AI couldn't
  answer a question about Samsung Electronics' segment revenue trend over the past year). Added
  DART's standard parameters (same convention already used by `get_major_shareholders` etc.) to
  query one specific past quarter/half/annual filing. Matches precisely on the report-title's fiscal
  label so it stays safe for companies whose fiscal year-end isn't December. Verified against 8 edge
  cases against real DART data with no regression on the existing `period` path. Still returns only
  one period per call (trend questions require one call per quarter).

## v2.2

25-tool lineup. Centered on three new tools (business details, provisional earnings, asset-holdings
screening) plus director/shareholder-return/treasury precision work.

- **New `asset_holdings` tool (25th, 2026-07-20)** — classifies audited consolidated balance-sheet
  accounts into purpose buckets (cash-like, tradable securities, friendly/allied stakes,
  controlling/associate stakes, investment property, core operating assets), re-marks listed stakes
  at today's price, and screens for asset plays via surplus-asset / equity-NAV coverage vs. market
  cap. Auto-generates a one-line character read ("trading-heavy", "real-estate play", "holdco
  discount", "friendly-stake"). Reviewed by an accountant + Data QA expert-agent panel and validated
  by reusing an existing 2,608-company census cache (KOSPI+KOSDAQ+EDGE, zero new DART calls), which
  surfaced and fixed a separate-financial-statement combined-account (subsidiary+associate) NAV-loss
  bug (130 companies, up to 4.57x), an active REIT false-positive, and a 19% market-cap source
  mismatch.
- **New `provisional_earnings` tool (24th, 2026-07-19)** — quarterly preliminary earnings filings
  (I002 fair disclosure): revenue, operating profit, net income + YoY. The earliest available
  earnings signal, ahead of confirmed periodic-report figures. table_markdown primary + best-effort
  headline, also covers non-financial metrics like auto unit sales and shipbuilding orders. Verified
  via multi-agent review across 24 companies plus a KOSPI500 census.
- **New `business_details` tool (23rd, 2026-07-18)** — reads the "Business Overview" section for
  segment revenue/profit (structured when possible, falls back to the original note as markdown at
  low confidence) plus five markdown-primary fields (sites, utilization, R&D, order backlog, key
  customers), and a dedicated financial/REIT track (operating overview, soundness ratios, investment
  property, gated by industry code). Verified via a 286-company census plus a three-expert panel
  (financial/disclosure/industry). Defaults to `period="latest"` (most recent of annual/half-year/
  quarterly).
- **New `director_board` tool (20th, 2026-07-08; footnote precision & attendance-rate hardening,
  2026-07-09)** — per-director data: registered-director pay, pay-limit utilization, tenure changes,
  individual pay disclosures ≥500M KRW, unregistered-officer pay, employee-to-officer pay multiple
  (pay_gap), and board attendance rate. Resolves footnote markers (e.g. `(주1)`) that the structured
  API leaves unexpanded by pulling the original filing text — while blocking false attributions
  (litigation provisions, related-party notes, stock options, table fragments, other footnotes) via
  a five-stage gate, downgrading to a raw excerpt when confidence falls short (300-company review:
  zero resolved errors). Attendance rate is also parsed from the original text (section-local, with
  a partial-attendance flag when a company's inline summary covers only outside directors).
  Performance: parallelized footnote fetches and a notice-parsing timeout cut max wall time from
  21.6s to 8.7s. A golden regression test (`spot_footnote_golden.py`) watches five known error types.
- **`shareholder_commitment` added — 19th tool, 2nd Action Tool (2026-07-07)** — tracks value-up,
  dividend, and treasury-buyback commitments against what was actually executed, year-round (whereas
  `proxy_advise_before_meeting` is a one-time AGM-timed judgment, this is a stewardship-engagement
  follow-up). For each buyback-cancellation cycle it computes the book-value (BPS) gain or loss by
  comparing the weighted-average purchase price against BPS at the time of purchase; dividends are
  shown separately in an overall shareholder-return figure. Dividend yield now also fills gaps in
  DART's own resolution-date yield (missing for older years) using krx_weekly year-end close
  (`yield_pct_yearend`).
- **`treasury_share` unit-misparsing bug fix (2026-07-07)** — result-report tables that declare a
  scale (e.g. "in millions of won") had their ACODE-tagged amounts misread as raw won, understating
  values by up to 1,000,000x. Found via a KOSPI200-wide sweep (7 companies, 26 rows affected), fixed,
  and re-verified at 0 remaining.
- **financial_metrics period handling** — DART reports carry different period semantics per item (income statement `thstrm` = current 3 months, cumulative is `thstrm_add`; cash flow = cumulative; balance sheet = point-in-time). For interim reports the tool now ① computes P&L on two bases — cumulative (YTD) plus current-quarter standalone (half/Q3 derived by differencing the prior report), ② computes turnover days (DSO/DIO/CCC) on a TTM (trailing-12-month) denominator to remove single-quarter annualization distortion (SK Hynix 26Q1 DIO 511→133 days; DSO false 38.6→61.4), ③ leaves ROE/ROA un-annualized (period value), and ④ always states the basis via `period_basis`/`turnover_basis`/`basis_note`. Also: evidence now carries the source report rcept/viewer URL, a warning when consolidated (CFS) is unavailable and standalone (OFS) is used, a quarter-aware default year for quarterly/qoq, and operating-margin QoQ/YoY in %p.
- **ownership_structure co-holder breakdown productized** — a 5% block's headline stake is filer + related parties combined; now split into `reporter_self_pct` + `co_holders`[{name, ownership_pct, is_registry_holder}] + `co_holders_verified` (sum≈headline invariant), with a rendered breakdown table (answers "who holds how much of OO's N%"). When related parties include the registry's largest shareholder, reclassified as `coheld_with_registry` (prevents proxy_contest mislabeling an ally as external). Parser hardening: self-name pollution, ㈜ symbol, fund-name digits (제N호), long English names, foreign IDs (LEI / foreign reg number) — 332-company census incl. proxy-contest edges, invariant 92.7→95.3%, unverified flagged via verified=False.
- **shareholder_meeting proposer_type unified** — shareholder-proposal agendas now use the canonical `shareholder_proposal` value (previously `shareholder`, mismatching consumers that missed proposals). Validated via a KOSDAQ shareholder-proposal census (raw-HTML cross-check).
- **treasury_share share-class split (common / other) + multi-class undercount fix** — acquisition/disposal results now split into common vs other-class (preferred / 기타주식 / RCPS unified). Fixed an ACODE undercount where the result report has separate common/preferred tables but ACODE captured only common (Mirae Asset 600→1,000억 = 600 common + 400 other), via per-day total summation. Census across KOSPI 200 + KOSDAQ 200 (172 preferred-active): only Mirae Asset affected (disposal/cancellation fine); fractional-share noise excluded by a 100M-KRW floor.
- **compensation single-library fallback** — for filings that cram all agendas into one `<library>` (IBK, Korea Investment Holdings), the director/auditor pay-limit current/prior tables don't attach, yielding `amount_unparsed`; a raw-text fallback fires only when structured parse fails (untouched for normal filers = regression-safe). Confirmed scoped to those two via a 35-financial census.
- **Removed `proxy_result_after_meeting`** (offset by the new order_contracts tool — 17 tools total) — the core (per-agenda pass/fail and vote rates) is served by `shareholder_meeting_results` with far fewer calls. Follow-up filings, contest, and governance context chain through direct tool calls.
- **Full tool-by-tool audit completed** — beyond parse success rates: value accuracy, units, composite seams, render layer, and production. Ownership across 450 companies (self-correcting DART source unit contamination), value accuracy across 286, corp_gov reference match across 30, 31 render cases, production MCP smoke.
- **Fixed a silent proxy_result regression** — agenda results were always empty due to an unsynced upstream key rename (verified fixed before removal).
- **proxy_advise robustness** — fixed crashes on string headcounts in compensation parsing and None formatting in the performance matrix.

## v2.1

17-tool lineup. Centered on risk-event tracking and natural-language routing improvements.

- **New `risk_events` tool (17th)** — serious accidents, embezzlement/breach of trust, and production halts in one view. With no company given, scans the whole market for the last 30 days. Verified against 305 companies x 3.5 years (zero search diff) and 359 full-text parses.
- **`related_party_transaction` → `corporate_deals`** — fixed tool-routing misses on "acquired/sold" natural-language queries by renaming and rewording the tool description.
- **`ownership_structure` precision** — 100% reconciliation of total shares (registry + treasury + others), registry top holder vs actual 5% blockholder split, contest-aware 5% change tracking.
- **`dividend` precision** — quarterly DPS derived from periodic-report cumulative differencing, 100% consistency across 51 companies.
- **`financial_metrics` precision** — fixed Q4 rows carrying full-year cumulative figures; all quarters are now standalone three-month values with QoQ/YoY attached by default. Interest-coverage distortion (total finance costs leaking into the denominator) removed with coverage expanded to 97%; borrowings and quarterly cash flow restored. Financial firms and mid-year restatements are auto-flagged. Verified across 412 companies x 2 fiscal years (KOSPI top 300, KOSDAQ top 100).

## v2.0

First stable release. The `tools_v2` toolset ships 16 public tools covering Korean listed-company governance analysis end to end.

- **16 public tools** — Company → Meeting/Data/Evidence → Action flow.
- **Control-contest signals** (`proxy_contest`) — 4-stage litigation classification + dedup, 5% holding dynamics (purpose shift, sustained accumulation), external-raider vs insider split.
- **Disclosure-type code index** — `pblntf_ty`/`pblntf_detail_ty` → actual disclosure mapping. Searches narrow by detail code first (dividends = `I001`, etc.).
- **Shareholder-return tracking** — dividends/treasury/value-up in one view.
- **Financial & governance checks** — DART financial endpoints + corporate governance report.
- **Reliability** — DART 1,000/min rolling-window hard guard (cap 910), 3-tier fallback (XML→PDF→OCR), full source tracing (`data.usage` + receipt numbers).

Next items (tracked internally): financial-statement footnote parsing (related-party, contingencies, segments), detail-code search rollout.
