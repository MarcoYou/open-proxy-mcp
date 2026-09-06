# Release Notes

Version history for OpenProxy MCP. [한국어](RELEASE_NOTES.md)

## beta — 2026-09-06

### `dividend_disclosure` — class shares no longer overwrite the common-share DPS

In the annual-report dividend table, class-share rows whose label lacks the word "preferred" (e.g. 종류주식, 1종 종류주식, 전환주 — 235 rows in the KOSPI ledger) were read as common shares, so a later row overwrote the common DPS. Korea Investment Holdings FY2024 showed 4,042 KRW (class 1 shares) instead of the common 3,980 KRW, Doosan showed 2,050 instead of 2,000, and the current-price yield, the yearly history, and `price_multiple_data`'s dividend yield used the same wrong value. Share-class classification is now a single rule shared by the year-end summary and the multi-year history, non-common DPS is labelled with the filing's own wording, and the render states that total dividends and payout ratio are company-wide (all classes, consolidated).

### Expanded one-page company prompt

`company_snapshot` now covers business structure, sector-specific sources, three-year financial and dividend queries, annual consensus availability, and recent filings with targeted follow-ups. Its table combines up to three actual years (A) and two available estimate years (E), leaving missing forecast fields unfilled. It distinguishes accounting bases, estimate snapshot dates, and price dates. Clients with visualization support are asked for revenue bars and an operating-profit line, distinguishing actuals from estimates. Input remains a single company name or ticker.

### `tools_guide` capability resource

Read `opm://tools_guide` for the currently available tools and their descriptions. Names, count, and introductions come from the runtime registry, with no DART calls. Available in clients that support MCP resources.

## beta — 2026-09-05

### AGM analysis and proxy voting guidance aligned with the implementation

- Korean and English READMEs and feature pages now distinguish FOR, AGAINST, REVIEW, NO_VOTE, and NO_DATA, and explain annual/extraordinary meeting selection, information cutoffs, and uncertain evidence locations. REVIEW is not an instruction to abstain; missing data never means automatic approval.
- Explained the difference between policy criteria and engine triggers, including the unimplemented attendance trigger. Corrected the five-year audit-committee tenure row in policy §0-A from automatic AGAINST to REVIEW. No decision logic changed.
- Aligned meeting-result documentation with DART API first, KIND fallback, and `table`/`summary` output. Summary results may have no voting percentages; missing values are not zero.

## beta — 2026-09-04

### Policy citations, election structure, and evidence guidance for meeting recommendations

- Aligned policy citations with actual document sections and items, with document-comparison tests. Citations distinguish policy criteria from their implementation in the engine.
- Passed parent-agenda seat counts and cumulative-voting information to candidate items, and reconciled a FOR parent with children that are all non-FOR. Unified independent/outside-director role interpretation while preserving the filing's original wording.
- Preserved excerpts while flagging uncertain evidence matches. Explicit annual/extraordinary requests without a matching notice return `no_filing` with available notice references. Board-seat cap detection is limited to the relevant sentence.
- Refined recommendation/candidate wording and reduced repeated policy quotations. Standardized dates on Korean time and limited the cache for filing searches covering today to 120 seconds.

## beta — 2026-09-03

### `dividend_history_data`+`dividend_screener` merged into `dividend_data` (32→31)

An architecture review of the three dividend tools (`dividend_disclosure`,
`dividend_history_data`, `dividend_screener`) found that these two weren't splitting up
data so much as `dividend_screener` was **giving a wrong answer**. Its `quarterly_only`
condition ("paid two or more times that year") read the quarterly ledger (`div_quarterly`),
whose cells about half of companies leave blank. Measured (FY2024, "2+ payments"): the
ledger gave **20 companies**; counting actual decision-disclosure filings gave **84**
(64 missed, 0 false positives). Both tools already shared the same service module, so the
merge swapped in the correct source in the same motion. `dividend_disclosure` (the live
filing tool) is unchanged — only one of the three was actually broken.

- New `dividend_data` — `scope=firm` (company time series + decision-disclosure counts),
  `screen` (conditional screening), `market`, `sector`. `min_payments` (actual resolved
  count from decision disclosures) replaces `quarterly_only`; `quarterly_only=True` is kept
  as an alias for `min_payments=2` for backward compatibility.
- **A new table distinguishes "zero" from "unknown."** `krx_listing` (derived from each
  ticker's first appearance in the weekly `krx_weekly` price snapshot) tells apart a year
  the company was listed but didn't pay (`0`) from a year it wasn't listed yet, so the
  question doesn't apply (`null`). Measured: 28 dividend-paying companies weren't yet
  listed as of FY2020's fiscal year-end — without this distinction, that many would have
  been misread as "paid zero times."
- **Decision-disclosure counts are only trusted for FY2020–2024**
  (`div_payment_scope.is_complete`). `min_payments` on years outside that window returns
  `status=scope_incomplete`.
- Side finding — the serving-path Postgres connection pool was adding about 55ms per
  query (a 29ms liveness ping on every borrow, plus a 26ms implicit transaction). Switched
  the read-only pool to autocommit and replaced the ping with a retry on connection-class
  errors — round-trip dropped from **65ms to 10ms**. This isn't dividend-specific: it
  applies to every tool on this pool (`price_multiple_data`, `trading_data`,
  `forward_estimates_data`, etc.).

### `dividend_disclosure` special-dividend detection fixed — 21 false positives removed

The same review re-ran the filing parser (`dividend_parser.py`) behind the live tool over
**all 3,831** KOSPI decision disclosures. It flagged 23 filings as special dividends, and
**21 of those were wrong**: "preferred shares receive an **additional** 1%p over the par
dividend rate" (a charter provision for preferred shares, not a special dividend),
"**additional** shares issued in a rights offering," and "**additional** treasury share
purchases" all matched.

- **Fixed the boundary that extracts the remarks section (item 11).** It previously cut at
  any `※`, so in 15 filings whose remarks *begin* with `※` the entire body vanished.
  Conversely, the **full pre-amendment body** that amended filings append afterwards was
  pulled *into* the remarks in 60 filings. Narrowing the boundary to "※ 관련공시" and the
  filing-body header corrected 115 filings; documents with empty remarks went 15 → 0.
- **Narrowed the special-dividend test** to `특별배당`/`기념배당`: 23 → 2 hits on the same
  corpus (0 false positives), and 0 hits on a **128-filing sample of non-cash forms**
  (subsidiary, REIT, stock-dividend, record-date filings) that the same parser also reads.
- A sentence saying the payout is **funded by another company's** special dividend no
  longer counts as this company's special dividend.
- **Extracts the per-share special amount.** The old rule only looked for 조원 (trillion
  KRW), so it extracted nothing from Samsung Electronics' FY2020 "adding **1,578 KRW** in
  the nature of a special dividend." Its absence also meant `special_dps` carried the
  **entire** per-share dividend (1,932) instead of just the special portion — now aligned
  with the ledger path's meaning (`total_dps = regular + special`).

No change to existing dividend data (2 filings flagged, identical after reload).

## beta — 2026-09-02

### Seven new tools and one rename, written up

Tools that landed after v2.5.2 (08-05) without a release note, collected in one place. **25 tools → 32.** The README tools badge now says 32.

The arithmetic closes like this: 25 tools as of 07-22 (`screener` included) plus seven added since: `proxy_guideline` (08-13) · `director_news` (08-20) · `financial_notes` (08-23) · `trading_data` (08-24) · `forward_estimates_data` (08-30, written up in the 08-31 entry) · `dividend_history_data` and `dividend_screener` (09-02). Dates are each wiki page's `created` field — this repository's git history begins on 2026-08-24, so first commits of earlier tools resolve to the history-import commit (`fd6eeb7`, 08-28) and were not used as evidence. `screener` predates v2.5.2 but was never written up, so it is documented here as well.

### `valuation` renamed to `price_multiple_data` (tool name 08-24, file 08-30)

- One name, "valuation", carried both **multiples (PER, PBR) and size (price, market cap)**: a single `scope` had to split five things, and a request for "just the share price" ran the whole multiple-derivation path to return one close. Multiples stay in `price_multiple_data`; size and trading moved to `trading_data` below.
- The tool name changed on 08-24 (wiki: "Name (renamed 260824)"); the file name followed in commit `a24a692` on 08-30. Usage statistics for the old name are folded into the same lineage by `usage_tracker.TOOL_ALIASES`.
- Definitions are unchanged — **PER = common-share market cap ÷ net income attributable to controlling interests; PBR = common-share market cap ÷ controlling equity (MRQ)** (switched 260823). `scope=market/sector/firm_history` read Supabase weekly snapshots, so **they return `no_data` when the batch has not run.**

### `screener` — market-wide filing screener / morning digest (added before v2.5.2, never written up; wiki updated 08-25)

- Answers "what filings came out today / yesterday". Summarises the major filings across every listed company since the last run as cards (company + market cap + type + stage + correction flag + DART/Naver links). A call with no arguments is the morning digest.
- `types` — `core` (provisional earnings, orders, treasury shares, dividends, capital increases/CBs, meeting notices, 5% holdings) / `all` / a comma-separated plain-language list ("treasury, dividend", "orders", "earnings", ...). `scan` (what appeared, cheaply) is the default; `details=true` opens only the filings it needs and fills in type-specific key figures (amount, % of base, DPS, agenda, stake %) by reusing the per-type parsers (`order_contracts` and the like).
- Source: DART `list.json` market-wide pages (no corp_code, scanned by type detail code) plus market cap from `krx_weekly` (zero DART calls). Corrections keep only the latest version under a `[기재정정]` prefix, and stages are tagged so that decision ≠ result ≠ cancellation.
- **An empty result is split into `no_new` (nothing new) and `status=error` (lookup failed).** Deep dives on one company belong to the individual tools.

### New tool `financial_notes` — liquidity and asset-quality notes for financial companies (wiki created 08-23)

- Extracts **financial-statement note tables from banks, brokers and insurers verbatim, without reshaping**: (1) restricted deposits and pledged assets (→ unencumbered cash), (2) investment assets by category — FVPL, FVOCI, amortised cost (→ per-category haircuts). Built at the request of credit and bond analysts: `financial_metrics` is a company-level aggregate with no note-level breakdown, and `business_details` covers "II. Business", a different chapter.
- `fields` (comma-separated; empty means all — the document is downloaded once per company, so one call is cheaper) · `period` (`latest` / `annual` / `half` / `quarter` / `quarterly`) · `basis` (consolidated / separate) · `year` (fiscal year; added after a 260824 tester report — there was no way to ask for the past before).
- Source: DART `document.xml`, notes under "III. Financial matters". **Tables are never merged or split** — that companies lay them out differently is itself information.
- Two cautions are written into the docstring. **Always separate current-period-end from prior-period-end** (KB Insurance restricted deposits: prior year-end 391,082 → current half-year-end 26,356, a 15-fold drop) · **units differ by company and, within one company, by report** (Hyundai Marine: quarterly in `won`, half-year in `thousand won`, annual in `won` — chaining them as-is is off by 1,000x). "Restricted" and "pledged" are separated by `kind` and never added together. Computing unencumbered cash and applying haircuts is outside this tool.

### New tool `director_news` — negative-news check on director candidates (wiki created 08-20)

- Searches Naver News by **candidate name (plus company name)** for director, auditor and audit-committee nominees and keeps only articles matching 48 negative keywords (embezzlement, breach of trust, investigation, sanction, dismissal, ...). It fills the gap beside the career and concurrent-post data `shareholder_meeting_notice` gives: incidents that never reach a filing, or reach it late.
- Source: **one NAVER API HUB news-search call (up to 100 items)**, filtered locally. The search API has no publisher option, so publisher groups are inferred from the article domain. Widen or narrow with `extra_keywords` / `exclude_keywords`. DART is not used.
- **A keyword hit is not a confirmed fact** — open the article; namesakes cannot be separated. **Zero results means "nothing matched under these conditions", not "cleared".** The tool does not decide for or against.

### New tool `proxy_guideline` — the voting-policy document itself (wiki created 08-13)

- Reads the **full text behind the citation** that `proxy_advise_before_meeting` attaches to each verdict ("OPM Guideline §Financial statements — ..."). Before this there was no way to see what stood behind that one line. Company- and DART-independent, **zero API calls.**
- Leave `section` empty for the table of contents plus full text; pass a word to get only sections whose title contains it (`재무제표`, `이사선임`, `정관`, `0-A`). An unknown section returns the list of available ones. Capped at 120,000 characters.
- The same document is also exposed as the `opm://guideline` resource, but a 260813 test confirmed that **the Claude.ai connector does not surface resources to the model**, so it is a tool as well.
- **The policy and the engine differ on purpose** — §0-A is the map of that gap. Even where the policy declares `against`, the engine returns REVIEW unless a hard trigger fires.

### New tool `trading_data` — price, market cap, share count, quotes (wiki created 08-24)

- Price and **size as such** — a ticker's weekly price / market cap / listed-share time series (from 2015-12), KOSPI and KOSDAQ market-cap aggregates, WICS sector market cap and weights, and the full quote for a given trading day (OHLC, volume, value traded, change). Multiples belong to `price_multiple_data`.
- `scope` — `firm` (default, with `since`) / `market` / `sector` (`scheme=wics_industry` 28 buckets, `wics_sector` 10; `bucket` for one sector's series) / `quote` (`as_of=YYYYMMDD`). `freq` defaults to weekly for firm and monthly for market/sector; `data.points_weekly` reports the number of underlying observations.
- Source: `firm/market/sector` read Supabase (`krx_weekly`, `krx_cap_agg`, **zero DART or KRX calls**); only `quote` hits KRX live (at most 2 calls). Status distinguishes `no_data` (market holiday, pre-listing, **batch not run**) / `db_error` (retry is valid) / `db_unconfigured` (no snapshot DB on this server — firm and market fall back to the latest single KRX live point with `timeseries_available:false`; sector has no fallback).
- **`close_krw` is not an adjusted price** — it is discontinuous at splits and reverse splits, and the output says so with `price_adjusted:false` and the list of adjustment events. Use `mktcap_krw` for continuous comparison. Market cap is the sum of every issue listed that day, preferred shares included, so it runs 3–4% above the Σ market cap in `price_multiple_data`. Unclassified issues are kept as `_UNCLASSIFIED` so that **sector sum == market sum**. Sector classification is observed from 2026-08; earlier periods are back-filled (`sector_asof`).

### New tool `dividend_history_data` — confirmed dividend time series (09-02, `a0dbddb`)

- Reads confirmed dividends from a **full collection of DART periodic-report `alotMatter` tables (828 KOSPI companies × FY2020–2025)**. `scope=firm` (one company across years) / `market` (KOSPI aggregate) / `sector` (WICS sector aggregate), with `year_from` / `year_to`. Where `dividend` looks at one company deeply and live, this looks wide across companies and years. **DART is not called live.**
- **Confirmed figures only** — no estimates, no decision-notice previews. Total payout is given as the **declared total only** (a common/preferred split cannot be reconciled without per-class share counts, so it is not produced). Payout ratio is the filing's own `(연결)현금배당성향(%)` — **consolidated, and not computed by us.**
- Blanks are split into **confirmed / no dividend / item absent / no report** and never filled with 0. Share class is one of `보통` / `우선` / `종류` / `미구분`; redeemable, convertible and tracking shares that had been lumped in with preferred (821 rows) were separated on 09-02 in `90cc13b`. Quarterly values are cumulative differences; a row flagged `음수차분` is a range where that premise broke.
- A failed database read ends with `status=db_error` (possibly transient — retry shortly).

### New tool `dividend_screener` — screening companies by dividend conditions (09-02, `a0dbddb`)

- **Filters companies in one fiscal year (`bsns_year`) by dividend conditions** — `min_payout` / `max_payout` (payout-ratio range) · `min_dps` · `quarterly_only` · `sector` (WICS) · `limit`. Reads the same full collection.
- **Counts only common-share rows (`보통`, `미구분`), confirmed figures, DPS > 0.** The result separates **population, matches, and rows shown** — **`limit` is a display cap, not the match count.** Before 09-02 the three shared one cell, and what read as "100 companies" was in fact 121.
- `quarterly_only` was redefined as **companies that paid two or more times in that year** (two or more quarters with payout > 0 in the quarterly ledger) in `90cc13b`.
- Zero results means "no such company", not a failed lookup; failure comes back separately as `status=db_error`.

Verified: `python3 scripts/check_tool_catalog.py` confirms 32 tools registered on the runtime MCP server (wiki catalog, README table and domain counts all match). `python3 scripts/wiki_lint.py --strict` passes.

## beta — 2026-08-31

### `price_multiple_data` — market and sector dividend yield

Tables for `scope="market"` and `scope="sector"` (`scheme="wics_sector"`) now carry a **cap-weighted dividend yield** next to PER and PBR. KOSPI 1.60% and KOSDAQ 0.70% for confirmed FY2025 on the all-issuers basis.

- **Two denominators, always together** — `all` (counts non-payers and issuers whose DPS is not yet confirmed; the market-convention headline) and `payers` (dividend-paying issuers only). The table prints them as `1.60 (1.89)`. Publishing one alone distorts KOSDAQ: its value doubles from `all` to `payers` (−59.7% in FY2023). **KOSDAQ really is lower, but half of the gap is composition — fewer companies pay at all — not payout capacity** (KOSPI moves only −15.5% to −20.8%). On `all`, the FY2024 KOSPI/KOSDAQ gap looks like 3.12x; on `payers` it is 1.64x.
- **Confirmed and forward sit side by side** — confirmed comes from December-fiscal-year-end confirmed DPS (`div_yield_hist`, refreshed once a year), forward from analyst-estimated DPS (`fwd_agg`). **They differ in source table, as-of date, and population** — the confirmed denominator is market cap in the last week of that December, while forward counts only issuers that have an estimate. The response says so explicitly, because the difference between the two must not be read as a change in dividends.
- **Gating differs from PER and PBR** — a loss suppresses PER, but dividend yield **still produces a value for a loss-making issuer that pays a dividend.** That is why PER can be blank in the same row, so it is stated in a footnote.
- Three different as-of dates are in play (weekly snapshot, confirmed fiscal year, estimate as_of); each is printed under the table. If the dividend lookup fails, the PER/PBR table still renders — only the column goes blank, and the footnote says why.
- Not attached for `scheme="ksic"` or `"wics_industry"` — the aggregate buckets are WICS sectors, and forcing them onto another axis would attach mismatched values.
- Small sectors are not folded together. `n_total` is kept so the reader can judge (for example, KOSDAQ Utilities has 2 issuers).

**[Unverified]** Confirmed FY2025 DPS is incomplete — 608 issuers (114 KOSPI, 494 KOSDAQ) are still blank, leaving 8.9% of KOSPI and 21.7% of KOSDAQ market cap unconfirmed. The `all` figure is suppressed by that much and will refresh when the annual load runs again after the March annual-report deadline.

Verified: MCP calls for `scope=market`, `scope=sector(wics_sector)`, and `scope=sector(ksic)` as a regression check; all 1,406 tests pass.

### New tool `forward_estimates_data` — consensus forward estimates

A new tool that answers next- and following-year expected results and forward multiples. It reads an analyst-estimate snapshot (`fwd`); these are not DART filings.

- **The ruler is carried twice** — once in the envelope `ruler` (as_of, `price_dd`, units, PER definition, multiple scope) and again on every row (`period`, `row_kind`, `basis`). `as_of` and `price_dd` diverge on weekends and holidays, so "PER as of `as_of`" is wrong.
- **Rows split by `reported` (vendor source) vs `derived` (our computation), not by actual vs estimate.** Growth rates cross the actual/estimate boundary — 2,180 estimate rows have an actual row as their prior period.
- **Multiples exist only on estimate FYs and the latest confirmed FY.** The previous data held 8,386 rows of "today's price ÷ an EPS from years ago" under the name PER, 80.5% of them past FYs. Those are no longer produced; `per_why` records why a cell is empty (loss-making, not the latest confirmed FY, and so on).
- **The PER definition now matches `price_multiple_data`** — common-share market cap ÷ net income attributable to controlling interests. The vendor formula (price ÷ EPS) was dropped house-wide on 2026-08-23 because stock splits mix an old share count's EPS with a new price. Periods diverging by more than 10% are flagged in the response (Samsung Electronics FY2025: 33.95 vs 39.15).
- **All monetary values are integer KRW.** The `_eok` (100-million-won) notation is gone — placed beside a won-denominated field in one answer it produces a 100-million-fold error.
- **Widen with `bundle`** — `core` by default, plus `growth`, `quality`, `keys`, `all`. A full-column response for a single ticker (Samsung Electronics) is 31 KB (~8k tokens), so the default is narrow for size, not because it is the right answer.
- **"Nothing found" splits three ways** — `no_estimates` (no analyst coverage; only 713 of 2,764 tickers have estimates), `not_found` (no such ticker), `db_error` (database failure). Collapsing them removes the distinction that decides what the caller should do next.
- The number of contrast rows of reported actuals is set by `actual_years`, default 2.

Verification: commit `332faa5`. `ok`, `no_estimates` and `not_found` confirmed through MCP calls; full suite of 1,406 tests passing.

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
- Existing response formats and single-request behavior are preserved; the full beta test suite passes 1,131 tests.
- At the time of writing this was on the `beta` branch only; it has since been merged into `main` and deployed.

Verified with beta commit `0879021`, the concurrent-request single-flight regression test, and the full 1,131-test suite.

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
