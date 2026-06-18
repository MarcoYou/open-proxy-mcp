# Release Notes

Version history for OpenProxy MCP. [한국어](RELEASE_NOTES.md)

## Since v2.1 (unreleased, 2026-06-12 ~ )

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
