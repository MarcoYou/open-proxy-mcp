# Business Details

<!-- documentation-contract: business_details fields=revenue_breakdown,sites,utilization,rnd,backlog,customers,raw_materials,product_pricing,financial_ops,financial_soundness,investment_property,geo_revenue,key_contracts -->

**Reads the "Business Overview" section of periodic reports for you.** From segment revenue and profit to production facilities, utilization, R&D, order backlog, key customers, input costs, and product pricing trends — it pulls exactly the subsections you need, verbatim, out of a report section that runs dozens of pages.

## What it answers

- **Revenue breakdown** (revenue_breakdown) — one entry point for how revenue splits, carrying two axes.
  - `by_segment`: reportable operating segments from the financial-statement notes (K-IFRS 1108, audited, revenue + operating profit). The primary source for SOTP and segment-profitability analysis.
  - `by_product`: the product/line revenue mix filed in the business section (disclosure-form item, not audited, revenue only).
  - **A single-segment filer often still discloses a product mix** — reading both axes together keeps it from being missed.
    The two axes slice the same revenue differently, so they do not sum.
- **Key contracts** (key_contracts) — counterparty, term, and purpose of licensing, technology-transfer, and long-term supply agreements.
- **Sites & production facilities** (sites), **production output & utilization** (utilization), **R&D** (rnd), **order backlog** (backlog), **key customers** (customers).
- **Raw materials & input costs** (raw_materials) — original material-composition, purchase, and input-price-trend sections.
- **Product & service pricing trends** (product_pricing) — original selling-price, ASP, and price-change-rationale sections.
- **Revenue by region** (geo_revenue) — geographic revenue breakdown. Structured output is returned only when the table passes reconciliation, unit, and external-revenue checks; otherwise the original table markdown is returned with the rejection reason.

Every field follows the same response contract: structured numbers only when certain, otherwise the original section markdown with a reason, otherwise an explicit absence. Structured responses include a `self_check` note — if a value looks off, re-query the original text via the suggested path.
- **Dedicated financial/REIT track** — financial firms get operating overview & soundness ratios (K-ICS, net capital ratio) instead of segment tables; REITs/insurers get investment-property detail (occupancy, vacancy). Auto-detected by industry code (KSIC).
- Supports **quarterly and half-year reports** as well — defaults to the most recently filed report.
- **When a value is missing, it says why** — "not disclosed" (the company said so, with the sentence quoted) · "not here" (the filing points to another section) · "no table" (the subsection exists but is prose only) · "not found" (a table is there and we failed to read it). A single "not applicable" makes readers stop checking the original.
- **Says which section of the filing it came from** (e.g. "II. Business → 다. Operating facilities"). Subsection titles differ per company, so without this you cannot locate the same place in the original.
- Core design: units and formats differ by company (utilization in %, hours, or tons), so the tool doesn't guess values — it **returns the subsection as markdown** and the reading AI extracts values from the original. Source: DART periodic-report originals. Details → [business_details](../../../wiki/tools/business_details.md).

## Ask like this

> "What's EcoPro BM's production capacity and utilization?"
>
> "How big is HD KSOE's order backlog?"
>
> "Break down Samsung Electronics' revenue and operating profit by segment"
>
> "Show LG Chem's raw-material price and input-cost trends"
>
> "Show Samsung Electronics' product-price trends and the reasons for changes"

## Related features

- [Financial metrics](financials.md) — company-wide financials there; segment/production/backlog structure here
- [Provisional earnings](provisional-earnings.md) — preliminary numbers before quarterly finals
- [Valuation](valuation.md) — segment profit feeds SOTP multiples
- [Asset-holdings screen](asset-holdings.md) — land/investment-property/equity cost-vs-fair-value and NAV-to-market-cap live here
