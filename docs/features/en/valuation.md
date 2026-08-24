# Valuation

**Combines DART filings with official KRX prices to compute relative-value multiples like PER, PBR, and dividend yield.** All figures are confirmed and **trailing** — it does **not** produce forward (analyst-estimate) numbers.

## What it answers

- **PER, PBR, dividend yield** (on the current price). The same PER differs by which point-in-time number you use, so it **always labels the basis — FY0 (latest confirmed annual) / TTM (trailing four quarters) / MRQ (most recent quarter-end).** e.g. `PER 12.3x (TTM, controlling) · PBR 1.1x (MRQ)`.
- Net income/equity are **controlling-interest**, statements **consolidated-first**. Non-KRW functional-currency firms (e.g. USD) are **auto-converted at Bank of Korea (ECOS) rates** (e.g. Doosan Bobcat).
- When multiples become meaningless (losses, capital impairment), it **shows N/M (not meaningful) instead of forcing a number.**
- Beyond a single firm, it also answers **market-wide and sector (industry) comparison, a stock's history**, and a plain-language **"how was this PER computed?"** explanation.
- Source: DART financials, shares outstanding, dividends + KRX prices + ECOS rates. Detail → [price_multiple_data](../../../wiki/tools/price_multiple_data.md).

## Ask it like this

> "What's Doosan Bobcat's PBR?"
>
> "Walk me through how this PER was computed"

## See also

- [Financial Metrics](financials.md) — the profitability/capital metrics behind the multiples
- [Shareholder Return](shareholder-return.md) — how dividend yield and buybacks tie to return policy
