# Financial Metrics

**Pulls the financial statements and auto-computes dozens of key metrics like ROE.** Rather than echoing DART numbers, it turns them into ratios for profitability, stability, and cash flow at a glance.

## What it answers

- **Profitability** (ROE, ROA, operating margin), **stability** (debt ratio, capital-impairment status), **cash flow** (free cash flow, cash-conversion cycle), and the **audit-opinion trend.**
- Net income is **controlling-interest**, statements are **consolidated-first** — so results don't distort in a market full of holding companies and subsidiaries.
- For a quarter, it **always labels which basis the number is on (current 3-month / cumulative / TTM)** — mixing them silently breaks ratios. Turnover days and the cash-conversion cycle use a **TTM (trailing four quarters)** basis to avoid single-quarter annualization distortion.
- Advanced: ROE DuPont decomposition and the book-earnings-vs-cash-flow gap.
- Source: DART financial-statement (balance sheet / income / cash flow) and audit-report endpoints. Detail → [financial_metrics](../../../wiki/tools/financial_metrics.md).

## Ask it like this

> "How did POSCO Holdings' ROE and debt ratio change over the last 3 years?"
>
> "Has this company's audit opinion stayed unqualified?"

## See also

- [Proxy Voting Support](proxy-voting.md) — capital impairment, audit opinion, and performance feed voting judgment
- [Valuation](price_multiple_data.md) — these metrics are the denominator of the multiples
