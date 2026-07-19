# Business Details

**Reads the "Business Overview" section of periodic reports for you.** From segment revenue and profit to production facilities, utilization, R&D, order backlog, and key customers — it pulls exactly the subsections you need, verbatim, out of a report section that runs dozens of pages.

## What it answers

- **Segment revenue & operating profit** (segments) — the primary source for SOTP and segment-profitability analysis. Standard tables come structured; unusual formats are returned as the original table.
- **Sites & production facilities** (sites), **production output & utilization** (utilization), **R&D** (rnd), **order backlog** (backlog), **key customers** (customers).
- **Dedicated financial/REIT track** — financial firms get operating overview & soundness ratios (K-ICS, net capital ratio) instead of segment tables; REITs/insurers get investment-property detail (occupancy, vacancy). Auto-detected by industry code (KSIC).
- **Asset-value scope** (opt-in only) — returns the footnote schedules for land & investment property (cost, book value, fair value, revaluation) and equity-securities holdings (cost vs. market), verbatim.
- Supports **quarterly and half-year reports** as well — defaults to the most recently filed report.
- Core design: units and formats differ by company (utilization in %, hours, or tons), so the tool doesn't guess values — it **returns the subsection as markdown** and the reading AI extracts values from the original. Source: DART periodic-report originals. Details → [business_details](../../../wiki/tools/business_details.md).

## Ask like this

> "What's EcoPro BM's production capacity and utilization?"
>
> "How big is HD KSOE's order backlog?"
>
> "Break down Samsung Electronics' revenue and operating profit by segment"

## Related features

- [Financial metrics](financials.md) — company-wide financials there; segment/production/backlog structure here
- [Provisional earnings](provisional-earnings.md) — preliminary numbers before quarterly finals
- [Valuation](valuation.md) — segment profit feeds SOTP multiples
