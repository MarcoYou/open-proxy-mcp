# Release Notes

Version history for OpenProxy MCP. [한국어](RELEASE_NOTES.md)

## Unreleased — 2026-10-23

### `financial_notes` no longer reports a section or report search it could not read as "no such note"

- When the table of contents pointed to several note sections and one of them failed to load (DART maintenance, quota or a transient error), the fields that section should have supplied were reported as "no such note" (`NOT_APPLICABLE`) with `status` still `exact`. Those fields are now "not collected" (`NOT_COLLECTED`), `status` is `partial`, and `warnings` says which section failed and to retry. When one field is gathered from two sections (restricted deposits and pledged assets) and only one fails, the tables that were read are returned as they are and the field's `note` adds that tables may be missing.
- When the balance-sheet (denominator) section could not be loaded, the markdown used to say "no account of that name was found — no denominator". It now says the section could not be loaded, so the denominator is unconfirmed rather than absent.
- With `year` set, the report search runs once per report type (annual, half-year, Q1, Q3). If some searches failed and another type was found, the found report used to be returned as `exact` with no warning (an annual-report search failure with only a quarterly report found made the quarterly report look like the latest). The chosen report is unchanged; the response is now `partial` and a warning names the search that failed. "No such report" (DART 013) is still an answer, not a failure.
- Markdown carries the same warnings as "⚠️" lines under the header. Calls without a failure return the same response as before (the failure branch only runs when a section or search was missed; checked by replaying the default half-year consolidated call for 3 financial companies; separate/all bases, annual/quarterly reports, an explicit `year` and other companies were not compared); tool names, arguments, response keys and the tool count are unchanged (no new keys).

### Lookup failures are no longer reported as "none" — `asset_holdings`, `ownership_structure`, `proxy_contest`, `price_multiple_data`, `screener`

- Common rule: only DART's "no data found" (013) means "none". Maintenance, quota and transport errors, plus 014 and 404, are lookup failures. For document fetches, 014/404 count as "none" only when the caller knows the filing is an attachment-only correction (a same-day original may simply not be readable yet, so otherwise the response says the data could not be read and may not exist yet on the filing day). When a point-in-time run cannot confirm that a document was public at that time, the message says so and does not suggest retrying.
- `asset_holdings`: a failed financial-statement, company-profile or other-corporation-investment lookup used to return an empty `ok`; it now returns `partial` with a failure warning. A failed consolidated lookup no longer falls through to separate statements. A failed company-profile lookup leaves "financial / holding company" undetermined (`null`) instead of asserting "no" and blanks the surplus-asset and equity-NAV multiples (the absolute surplus amount is shown on the non-financial formula, and the warning says so). A failed investment-list lookup blanks the equity NAV instead of reading zero holdings. A dead retry that swapped the quarterly report code was removed. The markdown view now draws a `partial` response with its warnings.
- `ownership_structure`, `proxy_contest`: failing to read a 5% block-holding filing's text (holding purpose, holder-sum table) used to pass silently. Each unread row with 5% or more now carries `doc_read_failed` (a new key, present only on unread rows) and the response is `partial`. `proxy_contest` no longer reports zero signals as "no signals (normal)" in that case but as `partial` (a company whose 5% holders only filed short-form reports can turn `partial` on a single transient failure). The tool description's old sentence "013/014/404 are not failures" was updated to the new rule.
- `price_multiple_data`: a failed share-count lookup adds a warning (the share count used for BPS/EPS is missing), and when financials, dividends and share count are all empty but one of them was a lookup failure, the response no longer asserts a "fund-style filer".
- `screener`: a detail lookup that fails now shows the existing "(lookup failed)" badge instead of "(no data)".
- Tool names, parameters and existing response keys are unchanged. Responses without a failure were the same in the replayed range (3 asset_holdings summaries, 8 raw 5%-holding filings of one company, 2 total-share-count lookups). One exception: the retry that re-fetched with the opposite quarterly report code when the account list came back empty was removed, so that path can differ. When a failure occurs, status, warnings and the new key above change.

### `dividend_disclosure` / `dividend_data` — default year, class-share table label and decision-table fiscal-year numbers fixed for March/June year-end companies

- When `year` is omitted, `dividend_disclosure` now defaults to the latest fiscal year that has already ended according to the company's fiscal year-end month. Previously the default was always last calendar year, so a fiscal year that ended this calendar year for a March or June year-end company (e.g. June year-end FY2026 final dividend of 115 won, March year-end FY2026 of 50 won) was missing from the default summary, trend and decision table. The default only moves when that year's annual report is already out; otherwise last year stays the default, and if a final-dividend resolution for the newer year exists the response says to query it with `year=`. When it moves, the response gains a new key `default_year` plus one warning line. December year-end companies and calls with an explicit `year` are unchanged in numbers and tables (the stage-timing table of a default-year JSON call gains one `default_year` entry). A default call first reads last year's annual report to learn the settlement month, so it is slower by that one serial round trip. For non-December companies one call can make at most one extra annual-report dividend lookup (none for December year-ends).
- In the "decisions by quarter" table the "preferred DPS" column now carries preferred shares only. For companies whose annual report names the non-common stock something other than preferred ("종류주식", "1종 종류주식" – Doosan, Korea Investment Holdings) a separate column headed with that original name is added (the response gains new keys `dps_class_krw` and `dps_class_label`). Values are as filed. Tables for companies without class shares, or with preferred shares only, are unchanged.
- `dividend_data` now numbers the fiscal year of decision counts for non-December year-end companies the way the ledger table and `dividend_disclosure` do (the year the fiscal year ends in). The stored decision ledger labels a non-December fiscal year by its **start** year (decided by the settlement month, `acc_mt`: a March year-end covering 2025.04–2026.03 is stored as 2025), so the same dividend (Dongwon Mobility 018500, record date 2025-05-30, resolved 2025-06-11) was FY2024 in the decision table and FY2025 in the ledger table. Non-December companies are now shifted by one year to FY2025 (checked on 129 operating non-December rows: no row differs from the shared attribution function). The "incomplete" (⚠️) marker is shifted the same way. The decision table of `scope=firm`, and the `min_payments` condition and decision-history columns of `scope=screen`, all use the same numbers. December year-end responses are unchanged; for shifted companies the `firm` response gains a new key `fiscal_year_alignment`. When `year_to` is omitted, `scope=firm` now extends the end of the decision-table range past the ledger's last year, so a second-half interim or quarterly resolution of a non-December company (e.g. a June year-end quarterly dividend resolved 2026-10-13 belongs to fiscal year 2027) shows up as an incomplete year instead of silently dropping out. When `year_to` is given explicitly and resolutions fall past it, one line under the table says "N resolution(s) outside the range — query with year_to=…", and the response gains `out_of_range` (or `beyond_ledger_year`). December year-end responses are unchanged.
- Tool names, arguments, existing response keys and the tool count are unchanged (only new keys are added).

### `asset_holdings` no longer reports a failed notes fetch as "not disclosed"

- In `scope="detail"`, when both the DART document fetch and the web viewer fallback fail, the four items (land, equity securities, pledged assets, contingent liabilities) used to say the notes contain no such disclosure. They now say the notes could not be read (`absence_kind="not_collected"`, `fetch_failed=true`), with a source-location line appended only when an installed extension supplies one (otherwise the message stops at "retry shortly").
- In `scope="summary"`, the same failure also leaves no way to tell listed from unlisted holdings (listed stakes were being counted at book value as unlisted). The equity NAV multiple (`nav.equity_nav_cov`), the NAV amount, the listed-holdings totals, the unrealized gap and the haircut flags (`nav.haircut_flags`, `haircuts.pledged`, `haircuts.contingent`) are now `null`, with a warning giving the reason and the retry path. Response keys stay; only values go empty, and `notes_fetch_failed`, `nav.equity_nav_unavailable` and `listed_stakes.unavailable` are added on failure only.
- Responses where the notes were read normally (including notes that really have no such disclosure) are unchanged.

### `dividend_disclosure` — fiscal-year attribution for March/June year-end companies and class-share DPS fixed

- Dividend resolutions are now assigned to a fiscal year using the company's fiscal year-end month. Previously a December year-end was assumed, so a March year-end company's final dividend (record date March 31) landed in the prior year and a June year-end company's interim dividend (record date December 31) landed a year early. They now go to the fiscal year containing the record date (the year the fiscal year ends in). December year-end figures are unchanged.
- The trend table's "undecided" check uses the same fiscal year as the annual-report summary. For a completed fiscal year with no resolution filing, the annual report's dividend section is read once to confirm, so a year whose report is already out shows its confirmed DPS instead of "undecided" (at most one extra DART call per such year). A failed lookup is reported as a warning, not as "undecided".
- Quarterly cumulative and interim dividend calculations pick the common-share DPS with the same rule as the annual-report summary, so a class-share row without the word "preferred" no longer overwrites the common-share DPS.
- In the quarterly cumulative-difference table the "preferred DPS" column now carries preferred shares only. A class share that is not preferred (e.g. "1종 종류주식") gets its own column headed with the filing's own class name (the response gains new keys `dps_class` and `dps_class_label`). Where preferred and class shares coexist, the class share no longer overwrites the preferred value.
- When the common shares pay nothing (0) and only a class share pays, the class share's value is no longer reported as the common DPS (applies to the annual-report summary, the trend table and the quarterly cumulative path alike). Companies whose common shares pay a dividend are unchanged.
- Tool names, arguments, existing response keys and the tool count are unchanged (only new keys are added).

### `provisional_earnings` — [correction] tables are read by section and column header, and structure-change corrections become latest candidates

- In an earnings-correction table, when the current-period amount cell was empty, the tool used to read the same-named row from a later change-rate section (a percentage) as the current value and present it like an amount. It now follows the section header rows and takes only rows from the current-period section as amounts.
- The before/after columns are fixed by the header row. When the "after" cell is "-" or blank, the before value is no longer reported as the current value; the field stays empty and the before value is kept only in the prior-value field.
- Corrections whose title carries a prefix, such as "[기재정정]매출액또는손익구조…", are now recognised as structure-change disclosures. Previously they dropped out of the candidates and the uncorrected original could be returned as the latest.
- Disclosures without corrections and the standard two-column (before | after) correction table give the same values; response keys, shape and DART call counts are unchanged.

### `forward_estimates_data` — a universe with zero stocks no longer turns into the whole-market table

- In the consensus revision screen, when a universe was resolved but contained zero matching stocks, the empty list used to be read as "all stocks" and a whole-market revision table could come back under that universe's name. It now answers `no_data` with a warning explaining why.
- Responses for universes that do contain stocks (such as "KOSPI all") are unchanged.

### `financial_metrics` — an adverse audit opinion is no longer counted as a clean one

- The audit-opinion check matched on a substring, so "부적정" (adverse), which contains "적정" (unqualified), was counted as clean. That switched off the non-clean-opinion alert (`non_clean_audit_opinion`) and the opinion-change alert (`audit_opinion_change`) and made the `audit_opinion` summary report everything clean (`all_clean`). An opinion is now clean only when, after trimming whitespace and a trailing "의견", it is exactly "적정".
- The placeholder row "해당사항없음" (not applicable) that can sit beside the real opinion row for the same year-end, and blank rows, are left out of the check (they used to count as non-clean and could raise the alert falsely). With no row left to judge, `all_clean` is null.
- Responses for companies with an unqualified opinion are unchanged; no new keys and no extra DART calls.

### Operator notices can ride along on every tool response (no dedicated tool)

- When the operator has an announcement (maintenance, a heads-up about a change), tool responses carry it. Markdown responses get a `> 📢 공지 (OPM <version> · <MM-DD>) …` block right under the `# title`; JSON responses get a top-level `notices` field (separate from `warnings`; a new key). With no notice, every tool's response is unchanged. Tool names, arguments, existing response keys and the tool count do not change.
- A notice can be shown once per key (default), on every call until it expires, or only on `company` responses (important notices show every call). The server instructions gain a single sentence telling the model to relay the notice to the user once, and the currently valid notices can also be read as the resource `opm://notices`.
- Notices come from the JSON at the environment variable `OPM_NOTICE_URL`, re-fetched in the background every 10 minutes. If fetching fails, the last notices received are kept for up to 72 hours, then responses go out without a notice (a response never waits for the fetch).

### Preferred shares are left out of sector composition and performance-analysis universes

- The sector composition of `trading_data(scope="universe")` ("semiconductor sector", "mid-class …", "major-class …") no longer lists preferred shares. Rank, weight, cumulative weight, sector market cap and the market-share denominator are over common stock only; the number and market cap of the excluded preferred shares are stated in a warning and in the new fields `data.sector.preferred_n`, `preferred_cap_krw` and `cap_incl_preferred_krw`. The composition total is smaller than the `scope="sector"` aggregate (which includes preferred shares) by exactly that preferred share.
- `performance_analysis` sector targets, blend components and the computed benchmarks "KOSPI total" / "KOSDAQ total" also leave preferred shares out (including the default attribution benchmark, so returns and excess returns of those calls change). Preferred-share quotes called by ticker, preferred shares named directly by code or basket, index constituents and the `scope="sector"` aggregate are unchanged.

### `performance_analysis` computes official indices themselves and reports risk metrics for the benchmark too

- Give `target` "코스피" / "코스닥" or "지수:<index name>" (for example "지수:코스피 200") and it computes cumulative and annualised return, volatility, maximum drawdown (peak and trough dates), Sharpe (risk-free = one-year Treasury) and the start and end index points from the index closes. The existing phrases "코스피200", "코스닥150" and "코스피 전체" still mean a basket of constituent stocks, and an index with no daily closes in the database is explained rather than filled in. An index is never compared with itself (`benchmark_same_as_target` warning), and because there is no constituent history, contribution and sector attribution (`scope="contribution"` / `"attribution"`) are not available for it.
- `relative` gains the benchmark's volatility, maximum drawdown and Sharpe and the start and end index points (official indices only), and the series carries `bench_point`. When the benchmark history is shorter than the target's, every value in `relative` is for the same overlapping period (`relative.period`) and the table states the period per column. The benchmark's cumulative series now counts the day the benchmark starts as 0% and leaves earlier rows empty (it used to put a 0% on the target's first row).
- Fixed: a start date inside the database's weekly floor was shown as having read the archive, and the sector attribution table cut off the sectors the target actually holds (banks, for example) by keeping only the top 20. Held sectors are now all listed, the cut non-held sectors are merged into one "기타" row, and a total row is added.

### `trading_data` answers official indices, the one-year Treasury rate and multi-stock quote rankings

- `scope="index"`: an official index's latest point, change from the previous day, 1-week, 1-month, year-to-date and 1-year returns, each with its reference date. Names match exactly first, then by market prefix, then by partial match; when several match it gives the candidates and asks instead of guessing. An index with no daily closes in the database is stated, not invented.
- `scope="rate"`: the latest one-year Treasury rate, its change from one week to one year ago and a period trend. The policy rate and exchange rates are not in the database and the answer says so.
- `scope="quote"` + `universe` (without `company`): ranking by change rate, trading value or volume (new argument `sort`) with the 52-week high, low, gap and new-high flag. A stock with a price-base adjustment that is not mere tick-size rounding (split, merger and so on) inside the 52-week window is left blank as "cannot be computed because of an adjustment event"; the table holds up to 300 rows (beyond that both ends are shown and the omission is stated). The KOSPI 200 and KOSDAQ 150 constituent tables gain a market-cap weight (%) column (not the float-adjusted weight of the official index). A single-stock call with `company` is unchanged.

### `trading_data(scope="index")` no longer repeats the market name, and its follow-up question reads correctly

- Titles such as "코스피 코스피" and "KRX KRX 300" now read "코스피" and "KRX 300" (sector indices such as "코스피 전기전자" are unchanged), and the follow-up sentences' malformed endings ("있어해", "여럿이라해") became "있어서" and "여럿이라서". Only the wording changed; values and response structure are the same.

### Dividend yield screen added; the quarter a quarterly dividend belongs to now agrees across both tables

- `dividend_data(scope="screen")` gains `min_yield` (%, lower bound) and `sort="yield"`. Yield = the chosen fiscal year's common-share annual DPS (summed over settlement dates for REITs that settle semi-annually or quarterly) ÷ the same company's latest common-share close; filtering and sorting apply to the whole population of that year before `limit` cuts the list. A stock without a price is "yield unknown", drops out of `min_yield` and its count is written under the table. The table gains DPS fiscal year, close, yield and reference price date columns, and choosing a year other than the last one with an annual report warns "past-year DPS divided by the latest price". A call that leaves both arguments empty keeps the old order (largest total dividend first). Stocks that split or merged after the fiscal year-end have distorted yields; the value is not corrected, only marked with ‡.
- `dividend_disclosure(scope="history")`: the quarter label of the decision-notice table is now "the most recent fiscal quarter that ended on or before the record date" (for example KB Financial 2025 Q2, Q3, Q4 interim → Q1, Q2, Q3). December-year-end companies whose record date is a quarter end (Samsung Electronics, for example) are unchanged. In the cumulative-difference table, when the previous report's row is empty and the difference spans two quarters, it is no longer written as one quarter but as "Q2+Q3 combined (no half-year report)" followed by a matching decision-notice line (DATA-DIV8).

### The dividend cumulative-difference table no longer reads "report exists, cumulative dividend 0" as a missing report

- A quarterly report whose value cells are all "-" used to be read as "no report" and shown as a combined row. Now, when DART answers no rows (013) it is a missing report (combined row kept); when it returns rows it is an existing report even if every value is "-" (cumulative 0 at that point, so 0 won for that quarter). IBK 2025 changes from "Q1+Q2 combined 0 · Q3+year-end combined 1,048" to Q1 0 · Q2 0 · Q3 0 · year-end 1,048 with the same total. KB Financial's "no half-year report" combined row is unchanged (DATA-DIV8).

### Screener cards: dividend pairing, subsidiary notices, automatic details for small universes

- A dividend decision and a dividend-specific record-date notice from the same company on the same day (neither a correction) are merged into one card only when exactly that pair is found; the record-date notice stays in `companions` and is counted in `counts.companion_merged` (Shinhan Financial Group, 2026-10-21: two dividend cards → one). A record-date notice for a shareholders' meeting is not merged and keeps its own card; when unsure, nothing is merged.
- Notices marked "(subsidiary's major management matter)" get `subsidiary_matter` and a "subsidiary notice" label in markdown, and are kept apart from the parent's own notices when matching corrections. The parent company's dividend per share and record date no longer attach to a subsidiary's dividend card (Shinhan Bank's interim dividend card used to show the parent's 1,480 won; it now shows nothing, with `detail_status: no_data` and the reason).
- When `details` is not given, it is switched on automatically for universes of 200 stocks or fewer and periods of 7 days or fewer (`types.details_auto`). Calls with an explicit `details=false` and the default whole-market call are unchanged. Treasury-stock cards gain `treasury_action`, and markdown gains market-cap and contract-amount labels.

### The `ownership_structure` 100% breakdown table states where the denominator comes from and checks it against the latest listed shares

- Under the summary breakdown table it states which fiscal year, receipt number and settlement date the denominator (common shares issued, from the periodic report) comes from. When it differs from the latest listed share count, both values and the difference (report minus listed, as % of listed shares) are shown and a "stake (on latest listed shares)" column is added (Samsung Electronics 2025: denominator 5,919,637,922 vs listed 5,846,278,608, +1.25%). Holdings and treasury shares remain as of the report date, so later cancellations or issues are not reflected.
- A difference of more than 20% of listed shares may mean a split or merger changed the share unit, so only both values and the difference are shown and no listed-share-based stake is computed. Asking for a year other than the latest fiscal year does not compare against today's listed shares. The new keys are `data.share_basis` and `data.treasury.basis`, with no extra DART or KRX calls.

### `business_details` segment and product tables state their period, and the △ sign in the self-check is fixed

- The segment table (`by_segment`) and product table (`by_product`) of `revenue_breakdown` carry `period` (quarter alone, cumulative, annual, or unknown, with the basis it was read from), and markdown prints a "기간" line above the table (Samsung Electronics 2026 half-year: the segment table is the 3-month column, the product table is the half-year cumulative). When the table header of a half-year or quarterly segment table cannot be read, the period is not inferred from the report type: it says "unknown" and asks you to check the source header.
- The product-table self-check read △ as positive, inflating the ratio sum; fixed (Samsung Electronics 2026 half-year ratio sum 117.7% → 99.9%, item total equals the total row). ▲ and ▽ are read as negative only when a footnote in the same section says they are negative.

### `financial_metrics` quarterly table: an explanatory line when net income exceeds operating profit

- For a quarter with positive operating profit where net income (total) exceeds 1.3 times operating profit, an explanatory line "operating profit + non-operating items − income tax = net income" and the new keys `net_to_operating_ratio`, `non_operating_net_krw` and `income_tax_krw` are added, and every quarterly row gains `income_before_tax_krw`. SK Hynix 2026-Q2 net income of 93.9 trillion won above operating profit of 60.5 trillion is not an error but non-operating income (62.2 trillion); the values are unchanged (DATA-NI4). When pre-tax income minus net income is negative it is not asserted to be income tax: it is shown only as `pretax_minus_net_krw` with a note that tax income or discontinued operations may be mixed in.

### `financial_metrics(scope="accounts")` fixes the IS filter and the cache for companies that file a single statement of comprehensive income

- For a company that files one statement of comprehensive income (CIS) and no separate income statement (SK Hynix 2025 consolidated), `sj_div=["IS"]` used to return 0 rows with status exact and no warning. It now returns the CIS rows as the income rows with `accounts.sj_div_mapped={"IS":"CIS"}` and a warning; `["IS","CIS"]` together does not duplicate rows. When the filter leaves 0 rows, the warning lists the requested `sj_div` and the report's composition before filtering.
- The cache key lacked `sj_div`, so a 0-row result for an IS filter was served for five minutes even to calls without `sj_div`; fixed, and responses that had 0 rows before filtering are not cached. For calls that pass `sj_div`, `sj_div_present` now holds the report's composition before filtering instead of after (`rows` and `row_count` are unchanged).

### `ownership_structure` with a past `as_of_date` uses only that day's information, and the largest shareholder is the row the filing marks as principal

- The latest 5% report per reporter is now picked among reports received by the reference date. It used to be picked from all reports first and dropped afterwards if outside the date, so a holder who reported again after the date vanished from the past-date list (Korea Zinc as of 2026-01-31: 1 holder block -> 5) and the holding purpose could be read from a filing received after the date.
- With `as_of_date` and no `year`, the business year follows the fiscal-month filing calendar (December year-end: Jan-Mar dates use the year before last; March year-end: the same year from July). It used to be pinned to the year of `as_of_date` minus 1. A past date is no longer compared with today's listed shares.
- `summary.top_holder` is the row whose `relate` field reads principal ("본인", including "최대주주 본인" and "최대주주"), not the first row by stake. Hanmi Science 2025 annual report: principal Song Young-sook 3.38% while an issuer officer, Shin Dong-guk 16.43%, was reported as the largest shareholder. The registry largest shareholder in `proxy_contest` uses the same function (only the name, relation and stake of `summary.top_holder` and `control_map.core_holder_block.top_holder` change; the keys stay). Companies whose principal already holds the largest stake return the same numbers.
- When a related party or officer in the same group holds more than the principal row, a warning line is added ("Largest shareholder (principal) Song Young-sook 3.38% — largest holder in the group Shin Dong-guk (issuer officer) 16.43%, group total 53.55%", in Korean in the response), so reading `top_holder` alone does not end at "largest shareholder holds 3.38%". Existing keys are unchanged.
- Scope of the reference-date handling: calls with neither `as_of_date` nor `end_date` return the same 5% blocks, business year and listed-share comparison. Passing only `end_date` also picks each reporter's latest report among reports received by that date (it used to pick the overall latest and drop it if outside the window). The path that estimates the largest shareholder from 5% reports when the registry is empty has the same filter. With `as_of_date` and no `year`, one extra company.json (company overview) call is made (process cache, 1 hour).

## Unreleased — 2026-10-22

### New tool `performance_analysis` — returns, risk, contribution and sector attribution, computed on call

- Computes cumulative and annualised return, volatility, maximum drawdown, Sharpe and Sortino, and benchmark-relative excess return, beta, alpha, tracking error and information ratio for a stock, sector, ranking, basket or **blend** (for example "반도체 업종=60; 대분류 금융=40"). It takes the same universe phrases as `trading_data` and offers price return or gross (pre-tax) total return with dividends reinvested.
- `scope="contribution"` splits the return by stock, sector or component with Carino linking so contributions sum to the cumulative return, and `scope="attribution"` gives a Brinson-Fachler sector breakdown (allocation, selection, interaction). Official indices have no constituent history, so attribution works only against a computed benchmark such as "코스피 전체".
- Closing prices are not divided naively: daily returns use KRX's published change from the base price, weekly returns use the raw price-reset events, and tick-size rounding is not counted as a corporate action. Membership is fixed at the start date and names delisted during the period are held as 0% cash.
- The response states its own limits through `warning_codes`: sector taxonomy break (before 2019-06-28), retroactive classification, survivorship, periods beyond three years (joined from the archive or cut at the database floor), gross-of-tax returns, dividends decided after the end date, and preferred shares. No new tables and no external calls — it reads stored prices, indices, rates and dividends only.

### Granting the server read access to the risk-free rate table — `ecos_rate`

- Performance analysis reads the risk-free rate (one-year Treasury) with the server account. The table definition used to revoke every server privilege; it now grants read-only access (writes stay with the daily batch). Until it is applied in production, only the rate-based metrics (Sharpe, Sortino, alpha) come back empty and everything else is unchanged.

## Unreleased — 2026-10-19

### DART "request limit exceeded" (020) is now explained as the daily limit

- When DART answered 020, tools said it was a per-minute limit and suggested querying one or two companies at a time with pauses. 020 is the API key's **daily request limit** (usually 20,000); pauses do not lift it and it resets at midnight KST. The message now says so and gives the next reset time.
- JSON responses carry `data.error_kind="daily_limit"`, `data.retry=false` and `data.retry_after` (next midnight, `+09:00`). HTTP 429 on web paths stays `rate_limited` (retry shortly).

### The first `law_lookup` query no longer pins about 470 MiB of server memory

- The query morphological analyzer runs in a separate process that exits after 10 minutes without law queries, returning its memory. Answers are unchanged (tokens of all 3,950 articles and 294 responses compared). The first query after a pause takes 1–2 seconds to restart the analyzer, as the very first query did before.

## Unreleased — 2026-10-18

### When 5% block stakes add up to more than 100%, the note now says why — `ownership_structure`

- The note "adding up 5% block-holding stakes exceeds 100%" always blamed reporters holding the same related parties, and when no reporters overlapped it still printed "counting the overlap once gives" the **same number** as the plain sum (STX: "199.86% … counted once: 199.86%"). Overlap is now mentioned only when overlapping reporters are actually found.
- When there is no overlap, reports are grouped by the **share count behind each stake** (shares held ÷ stake). Reports filed on either side of a debt-for-equity swap, capital reduction or new share issue have different denominators and cannot be added — at STX, the swap-era reports (KDB, Woori Bank, NH Bank) sit on about 170.52M shares, Bumhan Industries after the reduction and third-party allotment on about 28.66M. The `blocks` view shows this as a table; the data gains `block_camps.report_bases`, `overlap_found` and `over_100_note`. If no reason can be found, the note says so and points to the filings.
- When a reporter files two 5% reports on the same day, the **later** one is now taken as current. Until now the one listed first won, so STX's Shinhan Bank kept its 5.81% swap-era stake although a same-day 0.34% report followed the capital reduction (STX total 199.86% → 194.05%). The path that estimates the largest shareholder from 5% reports had the same issue.

### Largest-shareholder stake change reports are read by column name · no more birth dates — `ownership_structure(scope="changes")`

- The stake change report summary now shows the **shares, stake and report date** of the previous and current reports, and the change. Until now the report date (e.g. 2026-09-21) landed in the shares slot, the shares in the stake slot, and the current report came out as 0 shares — for every such report since the feature shipped in 2026-04.
- 🔴 The holdings table no longer puts an **individual holder's birth date in the shares column**. Personal details (birth date, business registration number, gender, nationality) are not included from any table; only a marker that they were left out. The holdings table now has name, relation, domestic/foreign and common/total shares and stake.
- The "(largest shareholder change)" form shows the **before** and **after** holder groups separately. Holder names in per-person changes no longer come out as the section heading ("4. 개인별 세부변동사항").
- A table whose columns are not recognised is reported as "not read — table N of the filing (section heading)" and raised as a warning, never filled with zeros. A mismatch between the summary total and the holdings total is flagged.

### The morning disclosure digest shows the consensus gap on provisional-earnings cards too

- Provisional-earnings cards in `screener(details=true)` get one line such as "vs consensus revenue −4.7% (expected 204.57T KRW) · operating profit −2.9% (expected 110.57T KRW) — snapshot 2026-09-26, 12 days before the filing". In JSON it is the card's `detail_fields.consensus_gap`.
- Only quarterly cards that were compared with consensus get the line. Monthly results and cards with no consensus found show the provisional figures only, as before. 9 of 17 earnings cards from 2026-10-07 to 10-16.

### Provisional earnings now show the gap to consensus

- `provisional_earnings` compares a quarterly provisional result with **the consensus from the week before the filing** and shows the expected revenue, operating profit and controlling net income with the gap (%). The provisional figures and the original table are unchanged; a comparison line and JSON `data.consensus_gap` are added.
- The consensus used is the latest weekly snapshot **before** the filing date — estimates revised after the filing are not used. The snapshot date, basis (consolidated/separate) and source are stated.
- Monthly results (monthly sales or volumes), fiscal-year results, cumulative periods, consolidated/separate mismatches and cases where no consensus is found (outside coverage or history) show only the provisional figures, without a comparison line (estimate history starts 2026-08-29 and is kept for 13 weeks).
- Measured on 39 provisional filings from 2026-08-29 to 10-18: 26 monthly filings excluded, 9 of 13 quarterly filings compared (Samsung Electronics Q3 revenue −4.7%, operating profit −2.9%).

### When the largest shareholder changes, and the contracts that would change it — `ownership_structure(scope="changes")`

- Reads **largest-shareholder change** filings and the **share transfer** and **share pledge** contracts attached to them (signed and terminated), cell text as written — before/after holder and stake, reason, purpose and funding, transferor, transferee, price and expected date, the stake left if the pledge is fully enforced, termination reason and before/after.
- `changes` used to read only stake movements where the largest shareholder stays the same, so a company whose largest shareholder changed did not show up here. `screener` largest-shareholder-change cards now open details too.
- Tables carrying addresses or birth dates (e.g. the debtor profile in pledge filings) are not included.

### Before the meeting notice — board resolutions to convene and their amendment history — `shareholder_meeting_notice(scope="resolution")`

- Reads the **board resolution to convene** a shareholders' meeting, filed before the meeting notice (which comes two weeks ahead). Date and venue, record date, board date, outside-director attendance, the agenda table (sub-items, shareholder proposals and cumulative voting marked) and nominee tables, cell text as written.
- An extraordinary meeting with only a board resolution used to look like "no meeting" until the notice appeared. When no notice is found, `summary` now points here.
- Resolutions are amended often (almost half of meetings), so each version comes with its **amendment history** — reason and changed cells (before → after). Warns when an amendment points to another meeting, or when a comparison-only amendment cleared the date.
- `screener` opens board-resolution cards through this path too — fixing cards that showed zero agenda items or labelled an extraordinary meeting as annual. Shows "agenda pending" when there is none yet.

### "No data" is an answer, "lookup failed" is a failure — response wording and status values corrected

- Places that reported DART's "no data found" as a failure now say it as information. E.g. for a merged or delisted company, `company` no longer says "최근 공시 인덱스 조회 실패: 013" (recent filings lookup failed) but "no DART filings in the search window — the lookup succeeded", and `dividend_disclosure` drops "alotMatter 조회 실패: 013" (it only means that year's annual report does not exist; the existing note explains it).
- Conversely, when a DART rate limit, maintenance window or transport error kept a list from being read, tools used to answer "no filings (normal)", "treated as no dividend (normal)" and `no_filing`. They now say "unknown": `status=error` (`partial` for `proxy_contest` and `shareholder_commitment`, which still show what they did receive) with `filing_status=fetch_failed` — `dividend_disclosure`, `corporate_deals`, `corporate_restructuring`, `dilutive_issuance`, `treasury_share`, `order_contracts`, `corp_gov_report`, `risk_events`, `financial_metrics`, and `company` (recent filings). `order_contracts` that finds supply-contract filings but cannot fetch any of their documents now returns `partial`, not `no_filing`.
- `proxy_contest` ownership register: when the base-year annual report is not filed yet (January–March every year, or after a merger or delisting), it falls back to the previous year's report and says which year. Until now the ownership context came back empty with "ownership register lookup failed".
- `provisional_earnings`: a company with no preliminary-earnings filings returned "filing search failed" (`error`); it now returns `no_filing`. `risk_events` market-wide scan: one empty channel (e.g. a single weekend or holiday) no longer fails the whole scan.
- `financial_notes` status values were upper-case `OK`/`NO_DATA`; they now use the same `exact`/`partial`/`no_filing`/`error` as other tools, and a missing report returns a response with a status (`error`, not `no_filing`, when the report search itself fails with a DART error). Field states in markdown read as words instead of `NOT_APPLICABLE`.
- `financial_metrics`: removed the "reprt_code=11013 no_filing" line and the "⚠ some filings could not be read" line for years without financial filings. Values for normal data are unchanged.

### Amendment cards show what changed — `screener`

- Amendment cards in `screener(details=true)` now carry the filing's own **item · before · after table**, cell text as written, with the stated reason, the amended document and its original filing date. Each row is marked changed, added or deleted.
- Previously only the latest amended values were shown, so what had been corrected was invisible. E.g. a supply-contract amendment "confirmed contract amount 27,631,500,000 → 34,028,600,000", or preliminary-earnings amendments by revenue and growth-rate block.
- When there is no table, the card says why — attachment-only amendment, values only in footnotes, or form not recognized.
- Usually no extra DART call (the same document is already cached). Treasury-share and capital-raise amendments, which use structured APIs only, cost one call each.

### "KOSPI200" now means the actual KOSPI 200 constituents · "KOSDAQ150" is accepted

- In `screener`, `trading_data(scope="universe")` and `forward_estimates_data(universe=…)`, "코스피200" (KOSPI 200) used to fall back to the **top 200 KOSPI stocks by market cap** because there was no constituent ledger. The index is chosen by sector representativeness and liquidity, not by market-cap rank, so the two differed — as of 2026-10-16, 34 of the 201 actual constituents (Hite Jinro, Ottogi, Chong Kun Dang, Daewoong Pharmaceutical, …) were missing and 33 non-members (REITs, an infrastructure fund, …) were included.
- Answers now use the actual constituents. The membership is refreshed daily and applied only when it matches the index totals published by KRX (market cap, volume and trading value) exactly. Responses carry the membership check date (`members_dd`) and warn if the check is more than 10 days behind.
- "코스닥150" (KOSDAQ 150) and "KOSPI 200" (with a space) are understood — previously they were asked back as "top 150 by market cap, or a company name?".
- The ranked list is not cut at the nominal size (a spun-off company stays in the index until the next regular review, so some days have 201 members). Only when the constituent ledger cannot be read does it fall back to the market-cap ranking, and it says so.

### Company-name inference picks only currently listed companies

- When a company is inferred from a partial name and any company that traded on KOSPI or KOSDAQ on the latest trading day matches, companies that did not trade — delisted or merged companies and KONEX companies — are not chosen ("세이" → SAVEZONE I&C). Their official names and tickers still resolve ("에이치디현대미포", "HD현대미포", "010620").
- When no trading company matches and **exactly one non-trading company** does, that company is returned ("현대미포" → HD Hyundai Mipo · "동양생명" → Tongyang Life Insurance). The response says up front that it is a delisted/KONEX-type inactive company; `company` reports low confidence and `listing_status=inactive`. If two or more such companies match ("더존"), none is chosen and near candidates are shown.
- The notice that a company was inferred from a partial name (the "「현대미포」를 **에이치디현대미포**(으)로 추정했습니다 …" line) was missing from `dividend_data`, `forward_estimates_data`, the history and sector views of `price_multiple_data`, and the company-list view of `trading_data`. These now carry it at the top too. Values are unchanged.
- When a name in a company list (`universe="삼성전자, 현대미포"`) resolves to such a company, it stays in the list with the note "identified as an inactive company — no row in price or market-cap tables" (it has no latest quote, so `trading_data` and `forward_estimates_data` tables have no row for it). Holdings valuation (`asset_holdings`) does not attach such a company as a "guessed candidate".
- This was the intended design, but it had been switched off since the 2026-08-23 market-code rename. The inference reason now reads "active listed companies" instead of "companies with a ticker". Official-name lookups are unchanged.
- If the quote store cannot be read once, the previous listing registry is kept and re-read after one minute.

### Weekly prices store closed weeks only · year-end values use the actual last trading day

- The weekly price store no longer deletes and rewrites the current week's rows every trading day; it stores each week's last trading day once, after the week ends. The current week's latest values still come from the daily quotes — `trading_data`, `price_multiple_data` and `screener` responses are unchanged.
- Fixed the year's last trading day (Dec 30) being deleted by the next year's first trading day in years where Dec 31 falls on a Tuesday–Thursday. Year-end values for 2019, 2024 and 2025 were a day or two early (the Friday: Dec 27, Dec 27, Dec 26); year-end bands and year-end dividend yields now use the actual last trading day. `trading_data(scope="firm")` series gain one extra point in the week the year changes.

### About a year of quotes served from the database — faster `trading_data` quotes

- `trading_data(scope="quote")` answers instantly for **about the last year** (from the latest trading day back one year and one week) from daily quotes the server keeps (open, high, low, close, volume, traded value, listed shares, change); only older dates are fetched from KRX. Output fields are unchanged; `data.source` is added.
- The first quote question of the day no longer waits 2-12 seconds on a KRX refresh. The server refreshes in the background Monday–Saturday mornings (07:30-20:00 KST; Saturday picks up Friday's close) and fills in days it missed while down.
- Market cap is close × listed shares. Change and change % are the KRX-published figures (against the base price), stored with each day — they match KRX on the day after a holiday and on stock-split or ex-rights days too.

### Faster dividend and per-company valuation-history lookups

- `dividend_data` no longer freezes the rest of the server while it waits for the database. The span of fiscal years is read through an index on every call, so a newly loaded fiscal year shows up on the next call. Output is unchanged.
- `price_multiple_data` `scope="firm_history"` (a company's weekly history) and `scope="firm"` (one company) send database lookups that do not depend on each other at the same time. One call sends at most 3 lookups at once; output is unchanged.
- The indexes on the dividend ledger (`div_declared`) are applied once by the operator (`scripts/div_indexes_ddl.py`).

### Companies resolve from the legal names used in filings

- Official names carrying a Korean legal-form marker ("한화갤러리아㈜", "(주)한화갤러리아") now resolve as exact matches rather than inferences.
- Legal names that spell an English name in Hangul ("삼성에스디아이" → Samsung SDI, "삼성이앤에이" → Samsung E&A) and REIT legal names ("롯데위탁관리부동산투자회사" → LOTTE REIT) also resolve. They are accepted only when they become exactly a registered name, and never when two companies match.
- Names that already resolved are unchanged. Preferred-share names ("삼성전자우") are still only inferred as the common-share company.

### Consensus revisions over 26 and 52 weeks — `forward_estimates_data`

- In step with the storage change that keeps **52 weeks** of consensus history in the database, revisions add **26-week and 52-week** comparisons — both per company (`bundle="revision"`) and in the universe screen (`window="26w"`, `"52w"`).
- Per company, the 26-week and 52-week columns appear only once the history reaches that far; until then the response says "only N weeks of history". Today (7 weeks of history) the 1/4/12-week table is unchanged.
- The "13-week rolling" note now states **how many weeks the database actually holds**, and that older weeks live only in an off-database archive this tool does not read.
- After the storage change, a company whose latest snapshot is older than the newest one (delisted, suspended, or dropped from coverage) gets a warning instead of an old snapshot read as current.
- Response fields are unchanged; `revision.first_as_of`, `history_weeks`, `windows_not_reached` and `db_window_weeks` are added.
- The consensus comparison on preliminary earnings (`provisional_earnings` → `consensus_gap`) follows the same rule: its source note states the weeks actually held, and it gives the same reference snapshot and values after the storage change.

### Small fixes — not-found guidance · evidence order · call count

- When a company cannot be found, the guidance now always ends with the **company-registry time** ("Company registry as of 2026-10-18 22:04 KST …") — a company listed after that may not be in it yet.
- `trading_data` (per-company prices and quotes) and `dividend_data` (a company's dividend history) now give the same not-found guidance as the other tools (where a renamed company went, retry by ticker). They used to return a single "not found" line.
- `company` lookups by ticker or corp_code no longer give the reason "Matched an official name or registered alias"; they say "Matched the ticker (stock code)" / "Matched the DART corp_code".
- `law_lookup` evidence references (`evidence_refs`) no longer change order each time the server restarts; they follow the result ranking.
- `business_details` asks the DART filing list once instead of three times (the same list) when finding reports. Results are unchanged.

## 2026-10-05

### Staged document cache increase

- Increase the document RAM cache from 96 to 144 MiB on the 2 GiB production deployment. Registered budgets total 360 MiB per process; runtime defaults and reclamation/restart thresholds stay unchanged.
- Require at least 24 hours of observation including an actual workload peak before considering another increase. Pause and rollback criteria are in the [memory operations contract](operations/memory.md).

### Cache and memory operational safety

- Disk accounting includes compressed documents and volume free space. Financial results use a byte-bounded expiring cache included in shared reclamation.
- Administration supports expiry-first, limited reclamation and separates removed payload bytes from process memory reduction. Diagnostic GET requests no longer force collection.
- Client occupancy, recent activity, busy requests, and evictable clients are reported separately. Failed readiness returns an HTTP failure; authenticated short drain leases allow in-flight requests to finish before restart.
- See the [memory operations contract](operations/memory.md). Public MCP tool inputs and outputs are unchanged.

## Unreleased — 2026-10-01

### Company Snapshot — quick company overview

The prompt presents company identity, business, financial results, valuation, and ownership through short descriptions and compact tables. Each section uses one or two sentences, with material recent changes and evidence gaps added when relevant. It has no fixed character count, page size, or mandatory question list. A small financial chart is requested only when useful and supported by the client. Units, numeric alignment, actual/estimate legends, and numbered citations use a consistent format. Annual consensus, newer quarterly or provisional results, and differing accounting bases remain distinct. Input remains a company name or ticker.

## v2.7.3 · beta — 2026-09-21

- The industry-classification argument takes neutral names: `scheme` in `price_multiple_data` and `trading_data` accepts 「대분류」 (10, broad) and 「중분류」 (28, detailed, default), and the previous values still work. Table headers and footnotes name the levels 업종 대분류·중분류.
- Disclosure cards no longer carry a portal link; they keep the DART link only, and the JSON portal-link field is gone.
- Data-source names are removed from code and messages. The classification refresh script and its monthly job are renamed (`refresh_sector_class.py`, `sector-class-monthly`). Database table names and external lookup addresses will be handled in a later step.

## v2.7.2 · beta — 2026-09-21

- Restored the real names of analyzed companies in public documentation that v2.7.1 had replaced with placeholder codes ("예시기업 CW", "Example Company CW" and similar): the READMEs in all three languages, feature guides, tool and disclosure-rule wiki pages, decision records and the voting guideline. The per-file masking notice is gone.
- Masking now applies to data sources only. Asset managers whose voting policies and records feed the guideline keep their existing codes (`K레거시` and others), and compared external services stay unnamed. See the [documentation rules](../wiki/wiki_schema.md).
- Fixed one garbled company name in the guideline (삼성SDS).
- The v2.7.1 validation split, pre-deploy documentation checks and tool-calling guide are unchanged.

## v2.7.1 · beta — 2026-09-20

- Masked company/brand analysis examples in public documentation and clarified tool calling and expansion paths. Connection service names, raw sources, and regression inputs are preserved.
- Split validation into collection safety, parsing, MCP responses, output, and validator audits. Unsupported PASS claims and missing required stages block promotion.
- Deployment now requires wiki index/link, documentation contract, and output vocabulary checks in addition to tests.
- Personnel parsing and its feedback/archive workflow remain on `codex/parser-feedback-loop`. Features lacking real positive term labels are not included in this production release.
- Git retains previous code versions; raw inputs, labels, experimental evidence, and previous documentation copies stay in private storage.

## v2.7.0 · beta — 2026-09-18

Bumped from 2.6.0 (09-09). This release gathers the governance review pilot merged on 09-10
(below) and the week of changes that followed. The tool count stays at 32.

### See by sector whether filings run above the usual level

The disclosure digest scans DART live and lists what was filed, one line per filing. Nothing was
kept, so it could not tell whether filings were running above the usual level. Major market-wide
filings are now recorded every night, and `screener(view="flow")` reads only that record (no DART
calls): new filing counts and amounts by broad and detailed sector, compared with the usual level
of the previous 13 weeks scaled to the same number of days. Large orders relative to revenue and
each company's cumulative orders for the year come with it.

- Corrections and terminations are counted separately, never as new filings.
- The record starts on 2025-09-16 — about a year — so the three-month limit does not apply. It is built overnight, so today's filings are in the digest.

### Market and sector averages gain forward PER and PBR

The market and sector tables of `price_multiple_data` (broad and detailed sectors) now show
forward PER and PBR next to the trailing figures. Only companies with analyst estimates are summed
(total market cap ÷ total estimated earnings, loss-making estimates included), so the population
differs from the trailing figures; the number of companies with estimates is shown alongside. An
eight-week trend and as-of queries are available. The record starts with the 2026-08-28 estimates.

### Give the period in words — this week, last month, Q3

The disclosure digest and the sector flow now read periods the same way. The same words used to
mean different things: "last month" was the last 30 days in the digest and was not understood by
the flow view, and "the last 7 days" covered eight days in the digest.

- "Last week" and "last month" are the calendar week (Monday–Sunday) and month; "this week" and "this month" start on their first day. All are anchored to today.
- "The last 7 days" and "the past month" count days back from today, inclusive.
- Months, quarters and ranges work too: August, Q3, the first half, from September 1, August 1 to August 20.
- A period beyond the digest's three-month limit is cut, the cut is stated, and the flow view is suggested.

### Ask by market-cap rank in one call

- `trading_data(scope="universe")` returns a ranking such as the KOSPI top 100 by market cap (rank, code, market, market cap, close) in one call. Preferred shares are left out of the ranking.
- `forward_estimates_data(universe=…, window=…)` returns estimate revisions for those companies in one table, over 1, 4 or 12 weeks. A weekly check that called each company separately now takes two calls.
- A counter word after the number — 「상위 200개 기업」, the top 200 companies — no longer stops the phrase from reading as a ranking.
- When none of the listed companies can be found, or a phrase like "KOSPI 120" could be a count or a name, the tool asks before any lookup. The digest used to switch to the whole market and label it as the selected companies.

### The cash shareholder-return ratio counts buybacks, not cancellations

In `shareholder_commitment`, the numerator of the cash shareholder-return ratio now uses
treasury-share **purchases** (acquisition decisions and trust contracts) instead of
**cancellations**. Cancellations also cover shares bought in earlier years, so they did not match
the period's actual spending. Purchases are aligned to the dividend's fiscal year — previously
three years of purchases were added to one year of dividends, inflating the ratio (Shinhan
Financial Group 94.5% → 49.2%, against the 50.2% the company disclosed). Treasury-share decisions
filed after that fiscal year appear separately at the top of the result. The return ratio in the
inside-director performance assessment used for voting recommendations is corrected to the same
basis.

## pilot — 2026-09-09 (merged 2026-09-10 · included in v2.7.0)

2026-09-10 integration: incorporated production-main improvements to filing coverage, correction handling, and company resolution into the v2 pilot. `governance_screen` now carries the shared candidate, renamed-company, and retry guidance in JSON and Markdown. Criterion-level missing-data skips and competing-filer separation remain intact. The [specification](../wiki/decisions/260908_1200_decision_guideline-v2-final-redesign-pilot.md) defines the execution profile and next quality-validation procedure. The v2 recommendations apply only when selected; the default recommendations are unchanged.

### `governance_screen` — source-bound governance review across selected companies

The tool gathers filing excerpts for up to 30 explicitly selected companies and gives the calling LLM reading tasks. A second call with `governance_assessments` for those tasks returns a review order based on evidence and materiality. The server does not invoke a separate LLM. Review categories cover minority-shareholder treatment, conflicts of interest, board accountability, disclosure reliability and control procedures. Tender offers, activism and litigation are not adverse findings by themselves. Party claims remain distinct from court rulings, and contractual holdings from settlement and exercisable voting rights.

Use `evidence_sources` to select up to five filings and source windows per company. Extend missing context with `text_offset`, `text_chars` and `focus_terms`; changed evidence, cutoff dates or settings require assessment of the new task. Missing checks are reported individually while other checks and companies continue. Citation matching does not verify the semantic accuracy of an assessment or completeness of filing coverage. Results are labelled **LLM assessment · human unreviewed · partial evidence**.

`since` and `known_receipts` narrow the list of new receipt candidates. They do not replace the evidence scope; the caller retains the returned checkpoint for a later request. The tool creates neither scheduled jobs nor actual votes. The tool catalog grows from 31 to 32, and the 2026-09-10 merge to main put the tool on the production server. See the [tool specification](../wiki/tools/governance_screen.md) for inputs, outputs and external-call bounds.

## v2.6.0 · beta — 2026-09-09 (4)

Bumped from 2.5.2 (08-05). A month of changes had shipped while the version stayed put, so the
README badge alone made the project look stalled since August. A check now fails if the badge and
`pyproject.toml` disagree (`check_tool_catalog.py`).

### Market-wide searches now use the real three-month limit

Searches that name no company are capped by DART at three months. That cap was approximated in
days (90 and others), but the allowance runs 89–92 days depending on the start date — so some
ranges were being refused silently. It is now computed in calendar months, returning exactly as
much as the limit allows.

### When director pay is missing, we say where to look

If no pay figures appear in the requested range but the approved pay ceiling did read, only half
the report came through. In that case the response flags a possible form change and names the
section to check in the original filing.

### A channel for reporting vulnerabilities

Root [`SECURITY.md`](../SECURITY.md) sets out the private reporting path and its scope.

## beta — 2026-09-09 (3)

### See whether raised funds were used as planned

When a company raises money through a rights issue or a bond, the filing states what it intends to
spend it on. What was missing was the other half: what actually happened. `scope="fund_use"` now
returns the capital-use table from the annual report — stated purpose and amount alongside actual
use and amount, plus the stated reason where the two differ.

**We do not reconcile plan against actual for you.** Item names are free text and vary by company,
so machine matching produces wrong pairings. The two columns sit side by side; the judgement is yours.

### See why the share count changed

Previously only "how many shares" could be answered. `scope="share_changes"` shows the reasons —
rights issues, bonus issues, stock dividends, conversions — dated. Because it comes from the annual
report, it reaches further back than the 24-month window used for issuance decisions.

### Tables no longer break on filing text

Filings genuinely contain `|` characters and line breaks (statement-of-equity item names, bond
tranche names). Passed straight into a table, one row split across several cells or two lines and
the whole table went out of alignment.

## beta — 2026-09-09 (2)

### Values no longer go missing from shareholder-meeting notice tables

Filing tables mark cells with two different tags, and only one was being read. The result was not a
broken-looking table but an intact-looking one with values quietly removed — a row reading
"fiscal year · through", with the date in the middle gone.

Measured against the local document cache: 64.5% of notice tables were losing columns, and 40.6% of
all cells were dropped. Both tags are now read.

### Multi-year financial queries no longer fetch the same data twice

Building a yearly trend fetched "this year + last year" for each year, so adjacent years overlapped
and the same data was retrieved twice. It is now fetched once — for a 10-year Samsung Electronics
query, 47 calls dropped to 29 (−38%), with every figure unchanged.

A cap of 10 years was also added; longer requests are trimmed and the response says so.

### The yearly table states its real period and shows year-on-year change

A 10-year query used to be titled "Annual trend (3 years)"; it now reads the actual span
("2016–2025, 10 years"). Year-on-year change for revenue, operating profit and net income — already
computed on the server but never rendered — is now in the table, so you no longer recompute it by hand.

## beta — 2026-09-09

### Several filings from the same company no longer collapse into one card

The disclosure screener groups filings of the same company and type. But the grouping key had no way
to tell one event from another — supply contracts only distinguish "signed" from "terminated" — so
three separate contracts filed by one company in a month became a single card. The amounts and links
were all correct, so nothing on screen revealed the error: "one contract this month" was really three.

Now only a **correction** supersedes an original. When several originals exist, there is no way to
know which one a correction amends, so nothing is merged and the candidates are listed instead — if
we don't know, we don't delete. Measured: supply contracts went from 186 to 225 over a month, capital
increases from 176 to 216. The daily morning digest is unchanged.

### The response now tells you what it could not see

Over busy periods a scan hits its page limit and sees only part of the window. Previously the only
signal was a six-word footer, which told you neither how much was cut nor what was missing.

The response now carries, per disclosure type, how many pages of how many were read and the filing-date
range actually received. A type whose scan failed is reported as failed rather than as "nothing found" —
an empty result must not read as success. It also says that splitting the period into two calls will
cover more.

## beta — 2026-09-08

### Companies whose functional currency is not the won — dollar figures no longer labelled in won

For companies like Doosan Bobcat that report in USD, the filings carry dollar amounts. Financial metrics and
asset holdings were passing those straight into won-labelled fields, so revenue read as 6.27 billion won when
the real figure is about 9.2 trillion — roughly 1,400x too small. Asset holdings were worse: dollar book values
were divided by a won market cap, so surplus-asset coverage came out at 0.00x instead of 0.346x, reversing the
conclusion into "this company has no surplus assets".

Amounts are now converted to won at the fiscal year-end rate, and the response carries the basis alongside the
values (`functional_currency`, `fx_rate_to_krw`, `fx_basis`). If the rate cannot be fetched, values are left
untouched and the response says they are not in won rather than quietly pretending otherwise. Quarterly trends
use a single rate across all twelve quarters, because per-quarter rates would corrupt the Q4 differencing.
Won-reporting companies are byte-identical (verified across 4 companies x 3 tools).

### Financial statement accounts, exactly as filed

`scope="accounts"` returns every account line in filing order instead of the 35 the summary keeps
(52 rows for Samsung Electronics' balance sheet). Narrow it with `sj_div=["BS","IS"]`. This answers what the
summary could not: what sits inside current assets, and whether a company files "revenue" or "operating revenue".

## beta — 2026-09-07

### Meeting-notice bundle: correction summary only for corrections, agenda details parsed once

The bundle no longer builds a second document tree looking for a correction summary in non-correction notices, and the agenda-details section that the personnel, compensation and AoI parsers each re-parsed is now parsed once per request. The board profile for KEPCO drops from 3.4 s to 1.9 s locally, and full meeting-notice queries speed up as well. Output is unchanged (verified on 16 companies for summary and 8 for full scope).

### Candidate meeting notices: parse only the notice section

To tell annual from extraordinary meetings, each candidate notice used to be parsed as a whole tree. The notice section is now sliced out as a string and only that slice is parsed. Across 143 cached notices every extracted field was identical and total time fell from 11.2 s to 0.3 s. Board profiles for companies with several candidate notices (KEPCO) gain the most.

### Faster `director_board` and `shareholder_meeting_notice`: notice parsing off the event loop

The board profile for SK drops from 7.8 s to about 3 s, and the 4–6 s window in which the server could not answer other requests or health checks is now under 0.4 s. The cost was the synchronous parse of a 3 to 5 MB meeting notice fetched for the pay-limit agenda. Parsing now runs in a worker thread, tables the requested scope does not use are skipped, and meeting info parsed while classifying candidate notices is reused. Output is unchanged.

### Operations: per-call timing log and stack dump

Every tool call now logs wall and CPU time (argument names only, never values), with a warning above 10 seconds. If the process stops answering, SIGUSR1 dumps every thread's Python stack to the log. This is the minimum needed after an incident (2026-09-07) where a machine stalled and nothing showed which request was holding the event loop.

### Output cleanup: storage names and codes in user-facing text

Table and column names (`krx_weekly`, `close_krw`, `mktcap_krw`) in `trading_data`, `shareholder_commitment` and `price_multiple_data`, the "bundle=core · period_type=FY" line in `forward_estimates_data`, and the filing-type code (`ownership_block`) in `company` are now plain Korean. `financial_metrics` says "undisclosed" instead of "-원" for a missing diluted EPS, and `proxy_contest` no longer breaks a table row when a position title contains a line break.

### No more literal "None" in `director_board` and `business_details`

Empty notes rendered as `> None` and `[roster] None`, and the business-details footer leaked an internal diagnostic (`주석fetch=None`). Notes are now omitted when absent, and the roster cross-check warning, whose body was lost to a key typo, is back.

### `provisional_earnings` labels: fiscal year, quarter and year-end month fixed

The year-end month used to be inferred from the period's start month, so every quarter came out as "Q1" (Samsung's 2025 Q3 read as "FY2026 Q1, June year-end"). The tool now uses the company's registered year-end month from DART and labels its source (company / default). The filing date and the results period are shown side by side on the second line, one-month periods read as "April 2026 (monthly)", and filings without an explicit date range derive the period from phrases such as "2025년 2분기". When the newest filing in the window is an attachment-only correction with no body, the tool falls back to the filing that has one.

### `as_of` for `price_multiple_data`

Past-dated questions such as "PER at last year-end" are now answerable. `scope=firm` reads the latest weekly snapshot at or before the date (PER, PBR, market cap; no dividend yield), while `market` and `sector` draw their tables from the snapshot at or before the date. Empty means latest, as before.

### `months`, `start_date`, `end_date` for `provisional_earnings`

Instead of only the latest filing within six months, a month count or a filing-date window (YYYYMMDD) now selects a past quarter's provisional results. The parameters existed in the service and are now exposed on the tool.

### `report` and `year` for `asset_holdings`

Previously fixed to the latest annual report. `report` (annual, half, quarter, q1, q3, latest) and `year` (fiscal year) now select half-year, quarterly, or past-year holdings; attachment-only corrections (`[첨부정정]`) are skipped. Quarterly and half-year reports carry thinner notes, so the response warns that detail schedules may be absent.

## beta — 2026-09-06

### Extension hooks

If an extension package is installed, the server calls its registration function, and tools append a one-line "source location" from the extension where parsing is weak or a value was not found. Without an extension, behaviour is unchanged. `financial_notes` JSON gains a `sections` list of the sections read; `asset_holdings` gains `rcept_no`.

### document.xml requests now rotate to a spare key on quota errors

When a filing-text (document.xml) request hits a DART quota error (020 or 021), it retries once with a spare key, as JSON requests already did. Previously only JSON requests rotated and the text request simply failed. Requests made with a single user key still never switch to another key.

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
