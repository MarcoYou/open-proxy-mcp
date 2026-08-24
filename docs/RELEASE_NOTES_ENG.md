# Release Notes

Version history for OpenProxy MCP. [한국어](RELEASE_NOTES.md)

## beta — 2026-08-25

### Stronger fiscal-period filtering in `financial_metrics`

- Collects nearby fiscal years and classifies FY/Q using `period_end` and the company's fiscal-year-end month, instead of relying on `bsns_year` alone.
- Returns the requested fiscal year and the two preceding fiscal years, preventing the next fiscal year's quarter from leaking into the result.
- Shows `period_end` and `fiscal_year_end_month` on quarterly rows so labels can be checked against the filing period.
- Calculates QoQ only when the immediately preceding quarter exists.
- Marks a past but unfiled quarter as `미제출` and hides future quarters.
- Calculates Q4 as annual cumulative minus Q3 cumulative.

### Coalesced duplicate DART document requests

- When concurrent requests need the same filing, only one DART `document.xml` request runs and the other callers share its result.
- `doc_misses` now counts actual DART round trips rather than every caller that observed a cache miss.
- The full beta test suite passes 1,131 tests.

Verified with beta commit `62f702e`, 1,110 tests, and live-data regressions for Shinyoung Securities (001720), Hyungji Elite (093240), and SK hynix (000660).

## v2.5.2 (2026-08-05)

Fixes a case where a mistyped or colloquial company name **silently returned a different company's answer**. It affects all 25 tools.

- **"지에스" was resolving to "지에스이"** — a partial name collision matched first and stopped the search, so the transliteration path that reaches "GS" was never taken ("에스케이" → 에스케이바이오팜 was the same fault). An exact name now wins. Compared before and after across 3,967 listed-company names: **only the five items below changed; everything else stayed the same.**
- **Three names it could not recognise** — `에쓰오일` / `에스오일` (registered as `S-Oil`) and `기아자동차` (the pre-2021 name). All three previously ended in "company not found".
- **When the name is not an exact match, the answer now says so** — an inferred pick is declared at the top of the response: "resolved 지에스 to 지에스이 — ask again with the ticker or the registered name". Reading a different company's answer as the right one is worse than getting nothing. Exact input (삼성전자, 005930) stays silent.
- **`corp_gov_report` — meeting-table columns went unnamed for newly listed companies** — when a company writes "미개최(전기)" because no meeting was held in the prior year, that column was emitted without its name.

## v2.5.1 (2026-08-05)

`scope=tables` grows from 4 tables to **10**. They come from the same fetched document, so DART calls still do not increase.

- **Three shareholder-meeting tables (1-1-1, 1-2-1)** — the key indicators only told you "notice given 4 weeks ahead: O/X"; now you get **how many days it actually was** (median 16, minimum 0). The convocation resolution date, venue, board and audit-committee attendance at the meeting, and a summary of shareholder remarks come with it for the last five meetings, along with concentration-day avoidance, written and electronic voting, and proxy solicitation.
- **Two director tables (4-2-1, 7-1-1)** — this fills the gap `director_board` documents as "reason for departure undetermined; check separate filings". The **reason is now a declared value** (reappointment 679 · appointment 600 · resignation 256 · term expiry 255 · other 5), together with first-appointment date, term end and whether the person still serves. Table 7-1-1 gives the number of board meetings and the **average days between agenda notice and the meeting**.
- **Two audit tables (9-1-1, 10-2-1)** — the internal audit body's members with their credentials and the **"accounting expert" / "financial expert" marking**, plus the record of communication with the external auditor (date, quarter, format, attendees). You can now see why key indicators 13 and 14 are marked O.
- Tables laid out with items in rows are transposed so that **one shareholder meeting is one row**.

## v2.5 (2026-08-05)

Still 25 tools. This one is about `corp_gov_report`. The corporate governance report previously yielded only a compliance rate and O/X flags; now its **source tables come out as tables**, and a defect that made financial companies point at a years-old filing is fixed.

- **`corp_gov_report` — director attendance, outside-director concurrent posts, candidate notice periods, and per-agenda vote counts (`scope=tables`)** — the governance report carries tables that bear directly on voting decisions, and none of them were reachable. Four are now open. **Table 7-2-1** each director's attendance and approval rates over three years (current, prior, and the year before, kept separate) · **Table 5-2-1** outside directors' concurrent positions (institution, role, start month, listed status) · **Table 4-3-1** how many days before the meeting candidate information was provided · **Table 1-2-2** shares for and against each agenda item. Column names are **taken verbatim from the filing form** — renaming them would break the link back to the source. They are read from a document already fetched, so **no additional DART calls**.
  - **Table 1-2-2 shows the 3% rule as a number** — only the audit-committee-member election has a smaller "shares entitled to vote" figure (HD Hyundai Heavy Industries: 30.19M vs 104.86M on other items), because the largest shareholder's voting rights are capped under Commercial Act §542-12(2).
  - **Columns whose meaning cannot be verified are left unnamed** — the form supplies no header for the first two columns of tables 1-2-2 and 4-3-1, so companies fill them differently. Most use "meeting + candidate", but some put the candidate's name first; when the shape disagrees the columns come back as `키1`/`키2` with a warning.
- **`corp_gov_report` — financial companies no longer point at a two-year-old filing** — filings whose name contained "annual report" were being filtered out, but for financial companies that is the **only** governance filing of the year. The current year vanished and an older report was served as the latest (KB Financial 2024-02-29 → **2026-03-05**; same for Shinhan, Samsung Life, Mirae Asset Securities). The name filter is gone; an annual report is deprioritised only when an exchange-form filing exists in the same year.
- **`corp_gov_report` — the financial-company notice now says "different form", not "not filed"** — the old wording used an internal term ("cannot parse the 15-metric table") and never said why. It now explains: a financial company that discloses its governance and remuneration annual report by the 31 May deadline is **exempt from filing the exchange-form report**, so the key indicators, sub-principles and form tables are simply not in the document. The notice points to the attached PDF that does hold the content. The rendered output no longer falls through to "could not read the key indicators", which read like a parsing failure.
- **`filings_count` renamed to `filings_found` (JSON field change)** — it differed from the shared `filing_count` by one letter but meant something else. `filings_found` is the **number of filings the search returned**; `filing_count` is the **number accepted as parseable** (used to derive status). They only look identical on the happy path — deleting either one makes financial companies report "0 filings on record". Callers reading this field via `format="json"` need to update the name.

## v2.4 (2026-08-03)

25 tools. The theme is **saying why an answer could not be produced** and **stating what basis a table was read on**. `business_details` consolidates the revenue axes and adds raw-material and product-price fields; `asset_holdings` now reports consolidated vs separate for note tables. Old field names survive as aliases, so existing calls keep working — the only removal is `getting_started`.

- **`business_details` / `asset_holdings` — "not available" now splits four ways (2026-08-03)** — when a value could not be produced, everything came back as a single "not applicable". That hid the difference between *the filing does not contain it* and *we failed to read it*, so readers stopped checking the original. There are now four outcomes, each **with its evidence** — **not disclosed** (the company said so; the sentence is quoted) · **not here** (the filing points to another section) · **no table** (the subsection exists but is prose only) · **not found** (a table is there and we failed to read it).
- **`asset_holdings` — states consolidated vs separate** — when reading pledged-asset, contingent-liability, equity-holding and property tables from the notes, the tool never said which financial statements they came from. Mistaking separate-entity assets for consolidated changes the whole liquidation-value (NAV) calculation. It now reads the basis the filing declares on each cell and reports it, warning when (1) one region mixes both, (2) separate was read although consolidated notes also exist, or (3) the section title and the cell declaration disagree. **When nothing is declared, no basis is invented.**
- **`asset_holdings` — quotes where the text came from** — the tool returned note excerpts without saying where they sat in the filing. It now quotes the surrounding wording verbatim so readers can find the same place in the original.
- **Both tools — tables we used to miss** — several format variations are now handled: raw-material and product-price tables whose heading is embedded in a sentence rather than a title, facilities disclosed as book values instead of locations (city-gas supply pipelines and the like), collateral schedules titled differently ("details of insurance pledged as collateral"), and equity-holding schedules filtered out because of an adjacent note. **No previously returned value changed** (verified against the full corpus).

- **`business_details` — four revenue axes in one place (2026-08-02)** — revenue breakdowns used to live in three separate places: `revenue_breakdown` (segment/product), `geo_revenue` (a standalone field), and export/domestic nested inside it. All four slice the same revenue differently, so they now sit together under `revenue_breakdown`: `by_segment` (the **only axis carrying operating profit**) · `by_product` · `by_region` · `by_trade`. The old names `geo_revenue`, `segments`, `revenue_mix_form` still work as aliases — **existing calls are unaffected**.
  - **Do not add the axes together.** `by_region` is consolidated, `by_trade` is separate-entity, so one can be larger or smaller than the other (Hyundai Motor 1.4x, Daehan Flour 0.5x). Each axis carries its own source and audit-scope label.
- **`business_details` — readable units, per-company provenance** — figures used to print in the source table's raw unit (`3,147,338` in millions) with the unit relegated to a footnote. The unit now sits in the column header and figures scale to the company's size. Each axis also states **which note section of that company's filing** it came from — section numbers differ per company, so "Note III" alone was not enough to locate the original.
- **`business_details` — regional revenue now states its basis** — (1) whether the table came from consolidated or separate-entity notes, and a notice when separate was used although consolidated data exists; (2) large regional tables carrying an elimination row are no longer missed; (3) the hardcoded "customer location" label is gone — the tool now reports the **attribution basis the company actually disclosed**. Only 5% disclose one, and some use "place of business" rather than customer location, so when absent it says so. This basis determines what the overseas-revenue share actually means.
- **`proxy_advise_before_meeting` / `director_board` — director nomination rationales were being mixed up** — where one block holds the rationale for every candidate, the first candidate's text was attached to all of them. The tool now splits by the section markers the filing itself declares, keeps the previous behaviour when it cannot split (flagged as shared text), and **never attaches another candidate's rationale** to someone the filing did not name.
- **Internal codes removed from responses** — identifiers such as `map_not_loaded` and form codes like `dual` / `standard7` no longer leak into user-facing sentences.

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
