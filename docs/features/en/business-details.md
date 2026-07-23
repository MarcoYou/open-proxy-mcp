# Business Details

<!-- documentation-contract: business_details fields=segments,sites,utilization,rnd,backlog,customers,raw_materials,product_pricing,financial_ops,financial_soundness,investment_property,geo_revenue -->

**Reads the "Business Overview" section of periodic reports for you.** From segment revenue and profit to production facilities, utilization, R&D, order backlog, key customers, input costs, and product pricing trends — it pulls exactly the subsections you need, verbatim, out of a report section that runs dozens of pages.

## What it answers

- **Segment revenue & operating profit** (segments) — the primary source for SOTP and segment-profitability analysis. Standard tables come structured; unusual formats are returned as the original table.
- **Sites & production facilities** (sites), **production output & utilization** (utilization), **R&D** (rnd), **order backlog** (backlog), **key customers** (customers).
- **Raw materials & input costs** (raw_materials) — original material-composition, purchase, and input-price-trend sections.
- **Product & service pricing trends** (product_pricing) — original selling-price, ASP, and price-change-rationale sections.
- **Revenue by region** (geo_revenue) — geographic revenue breakdown. Structured output is returned only when the table passes reconciliation, unit, and external-revenue checks; otherwise the original table markdown is returned with the rejection reason.

Every field follows the same response contract: structured numbers only when certain, otherwise the original section markdown with a reason, otherwise an explicit absence. Structured responses include a `self_check` note — if a value looks off, re-query the original text via the suggested path.
- **Dedicated financial/REIT track** — financial firms get operating overview & soundness ratios (K-ICS, net capital ratio) instead of segment tables; REITs/insurers get investment-property detail (occupancy, vacancy). Auto-detected by industry code (KSIC).
- Supports **quarterly and half-year reports** as well — defaults to the most recently filed report.
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
